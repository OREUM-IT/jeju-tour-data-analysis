#!/usr/bin/env python3
"""Offline secret-file and public-check contracts; no Cloudflare account needed."""
import base64
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

import configure

SPEC = importlib.util.spec_from_file_location('tunnel_check', Path(__file__).with_name('check.py'))
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)

ISSUER = 'https://ci-example.cloudflareaccess.com'
AUD = 'a' * 64
APP_SECRET = 'fictional-secret-for-tests-only-' + 'b' * 32
CLIENT_ID = 'c' * 32 + '.access'
CLIENT_SECRET = 'cfast_' + 'D' * 48
TOKEN = base64.b64encode(json.dumps({'a': 'a' * 32,
    't': '00000000-0000-4000-8000-000000000000',
    's': base64.b64encode(b'f' * 32).decode('ascii')}).encode('ascii'))


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        # Exercise file modes/publication as an unprivileged CI user. Production
        # still requires UID0 and fchown65532; no test override is exposed by CLI.
        self.patches = [patch.object(configure, 'ROOT_UID', os.geteuid()),
                        patch.object(configure, 'RUNTIME_UID', os.geteuid()),
                        patch.object(configure.os, 'fchown')]
        for mock in self.patches:
            mock.start()
        self.gateway = ('# preserve exact existing bytes\nGATEWAY_MODE=private\n'
                        f'GATEWAY_AUTH_KEY={APP_SECRET}\n').encode()
        (self.root / 'gateway.env').write_bytes(self.gateway)
        (self.root / 'gateway.env').chmod(0o600)

    def tearDown(self):
        for mock in reversed(self.patches):
            mock.stop()
        self.temporary.cleanup()

    def access(self, **override):
        return configure.configure(self.root, 'access', **{
            'issuer': ISSUER, 'audience': AUD, 'hostname': configure.PUBLIC_HOST, **override})

    def token(self, value=TOKEN):
        return configure.configure(self.root, 'token', secret=value)

    def test_access_preserves_gateway_secrets_and_reuses_identical_file(self):
        self.assertEqual(self.access()['status'], 'created')
        path = self.root / 'gateway-access.env'
        inode = path.stat().st_ino
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(self.access()['status'], 'preserved')
        self.assertEqual(path.stat().st_ino, inode)
        self.assertEqual((self.root / 'gateway.env').read_bytes(), self.gateway)
        self.assertNotIn(APP_SECRET.encode(), path.read_bytes())

    def test_access_rejects_unsafe_issuer_host_and_audience(self):
        invalid = [dict(issuer='http://ci-example.cloudflareaccess.com'),
                   dict(issuer='https://ci-example.cloudflareaccess.com.evil.test'),
                   dict(issuer='https://ci-example.cloudflareaccess.com:443'),
                   dict(issuer='https://ci-example.cloudflareaccess.com/path'),
                   dict(issuer='https://user@ci-example.cloudflareaccess.com'),
                   dict(issuer='https://127.0.0.1'), dict(hostname='oreumit.com'),
                   dict(hostname='www.oreumit.com'), dict(hostname='*.oreumit.com'),
                   dict(hostname='agent.oreumit.com/route'), dict(audience='AUD'),
                   dict(audience='a' * 63), dict(audience='a' * 64 + '\nKEY=value')]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(configure.ConfigurationError):
                self.access(**values)
        self.assertFalse((self.root / 'gateway-access.env').exists())

    def test_existing_access_is_never_silently_rotated(self):
        self.access()
        previous = (self.root / 'gateway-access.env').read_bytes()
        with self.assertRaises(configure.ConfigurationError):
            self.access(audience='b' * 64)
        self.assertEqual((self.root / 'gateway-access.env').read_bytes(), previous)

    def test_file_only_check_needs_no_control_token_or_tunnel(self):
        self.access()
        result = configure.configure(self.root, 'check')
        self.assertEqual(result['scope'], 'configuration_only')
        self.assertFalse(result['runtimeChecked'])
        self.assertFalse(result['tokenConfigured'])
        self.assertEqual((self.root / 'gateway.env').read_bytes(), self.gateway)

    def test_token_modes_reuse_and_original_gateway_preservation(self):
        self.assertEqual(self.token(TOKEN + b'\n')['status'], 'created')
        folder = self.root / 'tunnel'
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((folder / 'token').stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((folder / 'token.runtime').stat().st_mode), 0o400)
        inode = (folder / 'token.runtime').stat().st_ino
        self.assertTrue(configure.check_token(self.root, required=True))
        self.assertEqual(self.token()['status'], 'preserved')
        self.assertEqual((folder / 'token.runtime').stat().st_ino, inode)
        self.assertEqual((self.root / 'gateway.env').read_bytes(), self.gateway)

    def test_partial_pair_and_overpermissive_token_fail_closed(self):
        self.token()
        runtime = self.root / 'tunnel' / 'token.runtime'
        runtime.chmod(0o644)
        with self.assertRaises(configure.ConfigurationError):
            configure.check_token(self.root)
        runtime.unlink()
        with self.assertRaises(configure.ConfigurationError):
            self.token()
        self.assertFalse(runtime.exists())

    def test_wrong_owner_and_hardlinks_are_rejected(self):
        path = self.root / 'gateway.env'
        with self.assertRaises(configure.ConfigurationError):
            configure.read_private(path, uid=os.geteuid() + 1)
        os.link(path, self.root / 'extra-link')
        with self.assertRaises(configure.ConfigurationError):
            self.access()

    def test_gateway_permissions_and_symlink_are_rejected_without_repair(self):
        path = self.root / 'gateway.env'
        path.chmod(0o644)
        with self.assertRaises(configure.ConfigurationError):
            self.access()
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
        path.rename(self.root / 'original.env')
        path.symlink_to('original.env')
        with self.assertRaises((OSError, configure.ConfigurationError)):
            self.access()
        self.assertEqual((self.root / 'original.env').read_bytes(), self.gateway)

    def test_symlink_access_directory_and_lock_rejected(self):
        target = self.root / 'gateway-access.env'
        target.symlink_to('gateway.env')
        with self.assertRaises((OSError, configure.ConfigurationError)):
            self.access()
        target.unlink()
        lock = self.root / '.tunnel-prepare.lock'
        lock.unlink()
        lock.symlink_to('gateway.env')
        with self.assertRaises((OSError, configure.ConfigurationError)):
            self.access()
        lock.unlink()
        (self.root / 'tunnel').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(configure.ConfigurationError):
            self.token()

    def test_failed_second_publication_rolls_back_only_new_pair(self):
        real_link = os.link
        count = 0
        def fail_second(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError('fictional failure')
            return real_link(*args, **kwargs)
        with patch.object(configure.os, 'link', side_effect=fail_second), self.assertRaises(OSError):
            self.token()
        self.assertFalse((self.root / 'tunnel' / 'token').exists())
        self.assertFalse((self.root / 'tunnel' / 'token.runtime').exists())
        self.assertEqual((self.root / 'gateway.env').read_bytes(), self.gateway)

    def test_service_secret_accepts_current_and_legacy_formats(self):
        for secret in (CLIENT_SECRET, 'f' * 64):
            value = configure.service_values((CLIENT_ID + '\n' + secret + '\n').encode())
            self.assertEqual(value['CF_ACCESS_CLIENT_SECRET'], secret)
        content = (CLIENT_ID + '\n' + CLIENT_SECRET).encode()
        self.assertEqual(configure.configure(self.root, 'service-token', secret=content)['status'], 'created')
        self.assertEqual(configure.configure(self.root, 'service-token', secret=content)['status'], 'preserved')
        self.assertEqual(configure.read_service(self.root)['CF_ACCESS_CLIENT_ID'], CLIENT_ID)
        path = self.root / 'tunnel' / 'service-token.env'
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


class PublicCheckTests(unittest.TestCase):
    def setUp(self):
        self.access = configure.access_values(ISSUER, AUD, configure.PUBLIC_HOST)
        self.service = {'CF_ACCESS_CLIENT_ID': CLIENT_ID, 'CF_ACCESS_CLIENT_SECRET': CLIENT_SECRET}
        self.ready = json.dumps({'ready': True, 'mode': 'access', 'flowVerified': True,
                                'catalogPlaces': 981, 'productionReady': False}).encode()

    def test_signature_matches_gateway_exact_get_canonicalization(self):
        with patch.object(checker.time, 'time', return_value=1700000000), \
                patch.object(checker.secrets, 'token_hex', return_value='f' * 32):
            headers = checker.signed_headers(APP_SECRET)
        payload = '\n'.join(('GET', '/readyz', 'ops-readiness', '1700000000', 'f' * 32,
                             hashlib.sha256(b'').hexdigest()))
        self.assertEqual(headers['X-Jeju-Signature'],
                         hmac.new(APP_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest())

    def test_public_check_requires_both_auth_layers_and_no_model_request(self):
        with patch.object(checker, 'request', side_effect=[(403, b''), (401, b''), (200, self.ready)]) as req:
            checker.public_ready(self.access, self.service, {'GATEWAY_AUTH_KEY': APP_SECRET})
        self.assertEqual(req.call_count, 3)
        self.assertEqual(req.call_args_list[0].args[1], {})
        self.assertNotIn('X-Jeju-Signature', req.call_args_list[1].args[1])
        self.assertIn('X-Jeju-Signature', req.call_args_list[2].args[1])

    def test_public_check_rejects_unprotected_or_stale_private_gateway(self):
        cases = [[(200, self.ready)], [(403, b''), (200, self.ready)],
                 [(403, b''), (401, b''), (200, self.ready.replace(b'access', b'private'))],
                 [(403, b''), (401, b''), (302, b'')]]
        for responses in cases:
            with self.subTest(responses=responses), patch.object(checker, 'request', side_effect=responses), \
                    self.assertRaises(configure.ConfigurationError):
                checker.public_ready(self.access, self.service, {'GATEWAY_AUTH_KEY': APP_SECRET})

    def test_runtime_checks_use_only_expected_service_commands(self):
        with patch.object(checker, 'run_compose', return_value=b'') as run:
            checker.gateway_ready(Path('/deployment'), self.access)
            checker.connector_ready(Path('/deployment'))
        commands = [call.args[1] for call in run.call_args_list]
        self.assertEqual(commands[1], ['exec', '-T', 'agent-gateway', 'node', 'check.mjs', '--internal'])
        self.assertEqual(commands[2][:3], ['exec', '-T', 'cloudflared'])
        self.assertTrue(all(command[0] == 'exec' for command in commands))
        self.assertNotIn(APP_SECRET, json.dumps(commands))


if __name__ == '__main__':
    unittest.main()
