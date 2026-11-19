#!/usr/bin/env python3
"""Offline secret-file and public-check contracts; no Cloudflare account needed."""
import base64
import contextlib
import hashlib
import hmac
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch

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

    def test_token_allows_long_secret_and_empty_endpoint_but_rejects_custom_endpoint(self):
        value = json.loads(base64.b64decode(TOKEN))
        value['s'] = base64.b64encode(b'f' * 48).decode('ascii')
        value['e'] = ''
        encoded = base64.b64encode(json.dumps(value).encode())
        self.assertEqual(configure.tunnel_token(encoded), encoded)
        value['e'] = 'fed'
        with self.assertRaises(configure.ConfigurationError):
            configure.tunnel_token(base64.b64encode(json.dumps(value).encode()))

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

    def failed(self, responses):
        with patch.object(checker, 'request', side_effect=responses), self.assertRaises(checker.PublicCheckError) as error:
            checker.public_ready(self.access, self.service, {'GATEWAY_AUTH_KEY': APP_SECRET})
        self.assertIsInstance(error.exception, configure.ConfigurationError)
        diagnostic = error.exception.diagnostic
        required = {'status', 'code', 'phase', 'httpStatus', 'bodyBytes', 'responseKind', 'modelExecuted', 'productionReady'}
        self.assertTrue(required <= set(diagnostic) <= required | {'cloudflareCode', 'gatewayCode', 'gatewayHeaderContext'})
        self.assertEqual(diagnostic['status'], 'failed')
        self.assertEqual(diagnostic['code'], 'TUNNEL_PUBLIC_CHECK_FAILED')
        self.assertIs(diagnostic['modelExecuted'], False)
        self.assertIs(diagnostic['productionReady'], False)
        for secret in [APP_SECRET, CLIENT_ID, CLIENT_SECRET]:
            self.assertNotIn(secret, json.dumps(diagnostic))
            self.assertNotIn(secret, str(error.exception))
        return diagnostic, error.exception

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

    def test_failure_diagnostic_identifies_each_public_authentication_phase(self):
        cases = [
            ([(200, b'unprotected')], 'anonymous', 200, len(b'unprotected'), 'text'),
            ([(403, b''), (403, b'error code: 1010')], 'access_only', 403, len(b'error code: 1010'), 'text'),
            ([(403, b''), (401, b''), (302, b'')], 'signed', 302, 0, 'empty'),
            ([(403, b''), (401, b''), (200, b'[]')], 'readiness_response', 200, 2, 'json'),
        ]
        for responses, phase, status, size, kind in cases:
            with self.subTest(phase=phase):
                diagnostic, _ = self.failed(responses)
                self.assertEqual((diagnostic['phase'], diagnostic['httpStatus'], diagnostic['bodyBytes'], diagnostic['responseKind']),
                                 (phase, status, size, kind))

    def test_response_classification_keeps_only_safe_bounded_metadata(self):
        bodies = [(b'', 'empty'), (b'Forbidden', 'text'), (b'<!DOCTYPE html><html>Forbidden</html>', 'html'),
                  (json.dumps({'error': {'code': 'AUTH_FAILED'}, 'secret': CLIENT_SECRET}).encode(), 'json'),
                  (b'\x00\xff' + CLIENT_SECRET.encode(), 'binary')]
        for body, kind in bodies:
            with self.subTest(kind=kind):
                diagnostic, _ = self.failed([(403, b''), (403, body)])
                self.assertEqual(diagnostic['responseKind'], kind)
                self.assertEqual(diagnostic['bodyBytes'], len(body))
                if kind == 'json':
                    self.assertEqual(diagnostic['gatewayCode'], 'AUTH_FAILED')
                else:
                    self.assertNotIn('gatewayCode', diagnostic)

    def test_cloudflare_error_code_is_extracted_only_from_an_exact_ascii_error_body(self):
        for body in [b'error code: 1010', b' \r\nerror code:\n1010\r\n\t', b'error code: 1000', b'error code: 1999']:
            with self.subTest(body=body):
                diagnostic, _ = self.failed([(403, b''), (403, body)])
                self.assertEqual(diagnostic['cloudflareCode'], int(body.split(b':')[1].strip()))
        malicious = [b'error code: 999', b'error code: 2000', b'error code: 01010',
                     b'<html>error code: 1010</html>', b'error code: 1010 ' + CLIENT_SECRET.encode(),
                     'error code: １０１０'.encode(), 'error code:\u00a01010'.encode()]
        for body in malicious:
            with self.subTest(body=body):
                diagnostic, _ = self.failed([(403, b''), (403, body)])
                self.assertNotIn('cloudflareCode', diagnostic)

    def test_unknown_or_malicious_gateway_codes_never_become_diagnostic_strings(self):
        for code in [CLIENT_SECRET, 'UNKNOWN_GATEWAY_CODE', 'AUTH_FAILED\n' + CLIENT_SECRET,
                     {'secret': CLIENT_SECRET}, ['AUTH_FAILED'], 1010, None]:
            with self.subTest(code=code):
                body = json.dumps({'error': {'code': code}, 'raw': CLIENT_SECRET}).encode()
                diagnostic, _ = self.failed([(403, b''), (401, b''), (403, body)])
                self.assertNotIn('gatewayCode', diagnostic)
                self.assertNotIn('cloudflareCode', diagnostic)

    def test_origin_rejection_copies_only_allowlisted_boolean_header_context(self):
        context = {'originPresent': False, 'cookiePresent': True, 'cookieEmpty': False,
                   'singleCfAuthorizationCookie': True, 'cookieMatchesAssertion': False}
        body = json.dumps({'error': {'code': 'ORIGIN_REJECTED', 'headerContext': context}}).encode()
        diagnostic, _ = self.failed([(403, b''), (403, body)])
        self.assertEqual(diagnostic['gatewayCode'], 'ORIGIN_REJECTED')
        self.assertEqual(diagnostic['gatewayHeaderContext'], context)

        malicious = {'originPresent': CLIENT_SECRET, 'cookiePresent': 1, 'cookieEmpty': 0,
                     'singleCfAuthorizationCookie': 'true', 'cookieMatchesAssertion': True,
                     'rawCookie': CLIENT_SECRET, 'unknownFlag': True}
        body = json.dumps({'error': {'code': 'ORIGIN_REJECTED', 'headerContext': malicious}}).encode()
        diagnostic, _ = self.failed([(403, b''), (403, body)])
        self.assertEqual(diagnostic['gatewayHeaderContext'], {'cookieMatchesAssertion': True})
        self.assertNotIn('rawCookie', json.dumps(diagnostic))
        self.assertNotIn('unknownFlag', json.dumps(diagnostic))
        self.assertNotIn(CLIENT_SECRET, json.dumps(diagnostic))

    def test_header_context_is_ignored_for_other_codes_or_non_object_values(self):
        context = {'originPresent': True, 'cookiePresent': True, 'cookieEmpty': False,
                   'singleCfAuthorizationCookie': False, 'cookieMatchesAssertion': True}
        cases = [('AUTH_FAILED', context), ('UNKNOWN_GATEWAY_CODE', context),
                 ('ORIGIN_REJECTED', CLIENT_SECRET), ('ORIGIN_REJECTED', [context]),
                 ('ORIGIN_REJECTED', None), ('ORIGIN_REJECTED', True)]
        for code, value in cases:
            with self.subTest(code=code, contextType=type(value).__name__):
                body = json.dumps({'error': {'code': code, 'headerContext': value}}).encode()
                diagnostic, _ = self.failed([(403, b''), (403, body)])
                self.assertNotIn('gatewayHeaderContext', diagnostic)

    def test_invalid_readiness_json_and_unexpected_shapes_fail_in_the_response_phase(self):
        malformed = [b'{invalid json', b'null', b'[]', b'true', b'\xff',
                     self.ready.replace(b'"ready": true', b'"ready": "true"'),
                     self.ready.replace(b'"mode": "access"', b'"mode": "private"'),
                     self.ready.replace(b'"catalogPlaces": 981', b'"catalogPlaces": 980'),
                     self.ready.replace(b'"ready": true', b'"ready": false, "ready": true'),
                     self.ready[:-1] + b', "extra": NaN}', b'[' * 2000 + b'0' + b']' * 2000]
        for body in malformed:
            with self.subTest(body=body[:60]):
                diagnostic, _ = self.failed([(403, b''), (401, b''), (200, body)])
                self.assertEqual(diagnostic['phase'], 'readiness_response')
                self.assertEqual(diagnostic['httpStatus'], 200)
                self.assertEqual(diagnostic['bodyBytes'], len(body))

    def test_network_failures_are_unavailable_at_the_current_phase_without_raw_exception_text(self):
        cases = [([], 'anonymous'), ([(403, b'')], 'access_only'), ([(403, b''), (401, b'')], 'signed')]
        for completed, phase in cases:
            with self.subTest(phase=phase):
                diagnostic, _ = self.failed(completed + [checker.urllib.error.URLError(CLIENT_SECRET)])
                self.assertEqual(diagnostic['phase'], phase)
                self.assertIsNone(diagnostic['httpStatus'])
                self.assertEqual(diagnostic['bodyBytes'], 0)
                self.assertEqual(diagnostic['responseKind'], 'unavailable')

    def test_real_request_sets_a_fixed_user_agent_without_overriding_service_token_values(self):
        class Response(io.BytesIO):
            status = 403

            def read(self, amount=-1):
                self.amount = amount
                return super().read(amount)

        for existing in [{}, {'User-Agent': 'wrong-client'}, {'uSeR-aGeNt': CLIENT_SECRET}]:
            with self.subTest(headerNames=list(existing)):
                response = Response(b'error code: 1010')
                opener = Mock()
                opener.open.return_value = response
                headers = {'CF-Access-Client-Id': CLIENT_ID, 'CF-Access-Client-Secret': CLIENT_SECRET, **existing}
                before = headers.copy()
                with patch.object(checker.urllib.request, 'build_opener', return_value=opener) as build:
                    self.assertEqual(checker.request(configure.PUBLIC_HOST, headers), (403, b'error code: 1010'))
                self.assertEqual(headers, before)
                self.assertEqual(len(build.call_args.args), 1)
                self.assertIsInstance(build.call_args.args[0], checker.NoRedirect)
                request = opener.open.call_args.args[0]
                transmitted = {name.lower(): value for name, value in request.header_items()}
                self.assertEqual(transmitted['user-agent'], 'oreumit-agent-check/1.0')
                self.assertEqual(transmitted['cf-access-client-id'], CLIENT_ID)
                self.assertEqual(transmitted['cf-access-client-secret'], CLIENT_SECRET)
                self.assertEqual(request.full_url, 'https://agent.oreumit.com/readyz')
                self.assertEqual(request.get_method(), 'GET')
                self.assertEqual(opener.open.call_args.kwargs, {'timeout': 20})
                self.assertEqual(response.amount, 65537)

    def test_redirect_responses_are_not_followed_and_alternate_hosts_never_open(self):
        response = checker.urllib.error.HTTPError('https://agent.oreumit.com/readyz', 302,
                                                 'Found', {'Location': 'https://invalid.example/'}, io.BytesIO(b''))
        opener = Mock()
        opener.open.side_effect = response
        with patch.object(checker.urllib.request, 'build_opener', return_value=opener):
            self.assertEqual(checker.request(configure.PUBLIC_HOST, {}), (302, b''))
        self.assertEqual(opener.open.call_count, 1)
        self.assertIsNone(checker.NoRedirect().redirect_request(None, None, 302, 'Found', {}, 'https://invalid.example/'))
        with patch.object(checker.urllib.request, 'build_opener') as build, self.assertRaises(configure.ConfigurationError):
            checker.request('different.example', {})
        build.assert_not_called()

    def test_oversized_response_read_is_bounded_and_reports_only_the_limit_observation(self):
        class Response(io.BytesIO):
            status = 403

            def read(self, amount=-1):
                self.amount = amount
                return super().read(amount)

        response = Response(b'x' * 100_000 + CLIENT_SECRET.encode())
        opener = Mock()
        opener.open.return_value = response
        with patch.object(checker.urllib.request, 'build_opener', return_value=opener), self.assertRaises(checker.PublicCheckError) as error:
            checker.public_ready(self.access, self.service, {'GATEWAY_AUTH_KEY': APP_SECRET})
        diagnostic = error.exception.diagnostic
        self.assertEqual(diagnostic['phase'], 'anonymous')
        self.assertEqual(diagnostic['httpStatus'], 403)
        self.assertEqual(diagnostic['responseKind'], 'oversized')
        self.assertEqual(diagnostic['bodyBytes'], 65537)
        self.assertEqual(response.amount, 65537)
        self.assertNotIn(CLIENT_SECRET, json.dumps(diagnostic))

    def test_main_emits_only_safe_diagnostic_json_and_keeps_configuration_failures_generic(self):
        diagnostic, error = self.failed([(403, b''), (403, b'error code: 1010')])
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(checker.sys, 'argv', ['check.py', '--scope', 'public']), \
                patch.object(checker, 'check', side_effect=error), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.assertEqual(checker.main(), 1)
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(json.loads(errors.getvalue()), diagnostic)
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(checker.sys, 'argv', ['check.py', '--scope', 'public']), \
                patch.object(checker, 'check', side_effect=configure.ConfigurationError(CLIENT_SECRET)), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.assertEqual(checker.main(), 1)
        self.assertEqual(output.getvalue(), '')
        self.assertTrue(errors.getvalue().startswith('TUNNEL_CHECK_FAILED:'))
        self.assertNotIn(CLIENT_SECRET, errors.getvalue())

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
