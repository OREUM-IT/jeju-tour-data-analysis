"""Host-side overtourism flow pin: fake runner only, no Docker, Langflow or model."""
import ast
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare = load('prepare_overtourism', ROOT / 'prepare-overtourism.py')
gateway_tests = load('prepare_overtourism_gateway_fixtures', ROOT / 'tests/test_prepare_gateway.py')
FLOW_ID = '4b7e4a0e-6f8e-4d2c-9a43-1f0d5f6b2c11'
FLOW_HASH = 'd' * 64


def result(**overrides):
    return {'status': 'passed', 'flowStatus': 'created', 'flowId': FLOW_ID, 'flowSha256': FLOW_HASH, 'nodeCount': 6,
            'edgeCount': 7, 'toolCount': 3, 'modelCallLimit': 8, 'modelExecuted': False, 'productionReady': False, **overrides}


def runner(value=None, *, code=0, raw=None):
    stdout = raw if raw is not None else json.dumps(value if value is not None else result()).encode()
    return Mock(return_value=subprocess.CompletedProcess([], code, stdout, b''))


class OvertourismPinTest(unittest.TestCase):
    def setUp(self):
        fixture = gateway_tests.GatewaySettingsTest('test_init_creates_distinct_private_secret_pair_without_touching_other_settings')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.root = fixture.root

    def quiet(self, call, *args, **kwargs):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            value = call(*args, **kwargs)
        self.assertEqual(output.getvalue(), '')
        return value

    def settings(self):
        return prepare.gateway.settings.read_env(self.root / prepare.SETTINGS, private_mode=True)

    def test_program_embeds_the_reviewed_sources_as_literals(self):
        tree = ast.parse(prepare.program())
        assignments = tree.body[:3]
        self.assertEqual([node.targets[0].id for node in assignments], list(prepare.SOURCES))
        for node, path in zip(assignments, prepare.SOURCES.values()):
            self.assertEqual(ast.literal_eval(node.value), path.read_text(encoding='utf-8'))

    def test_provision_requires_a_ready_gateway_and_never_runs_before(self):
        fake = runner()
        with self.assertRaisesRegex(ValueError, '^GATEWAY_PROVISIONING_REQUIRED$'):
            self.quiet(prepare.flow, self.root, 'provision', runner=fake)
        fake.assert_not_called()
        self.assertFalse(os.path.lexists(self.root / prepare.SETTINGS))

    def test_provision_pins_only_non_secret_metadata_and_is_idempotent(self):
        self.fixture.ready_root()
        before = {name: (self.root / name).read_bytes() for name in prepare.gateway.NAMES}
        fake = runner()
        value = self.quiet(prepare.flow, self.root, 'provision', runner=fake)
        self.assertEqual(value, {'status': 'passed', 'flowStatus': 'created', 'modelCallLimit': 8, 'modelExecuted': False,
                                 'productionReady': False, 'scope': 'overtourism-flow-pinned', 'settingsChanged': True})
        command = fake.call_args.args[0]
        self.assertEqual(command[-6:], ['exec', '-T', 'langflow', 'python', '-', '--provision'])
        self.assertEqual(fake.call_args.kwargs['input'], prepare.program())
        self.assertEqual(self.settings(), {'LANGFLOW_OVERTOURISM_FLOW_ID': FLOW_ID, 'LANGFLOW_OVERTOURISM_FLOW_SHA256': FLOW_HASH})
        self.assertEqual(stat.S_IMODE((self.root / prepare.SETTINGS).stat().st_mode), 0o600)
        # The recommendation Gateway settings are untouched byte for byte.
        self.assertEqual({name: (self.root / name).read_bytes() for name in prepare.gateway.NAMES}, before)
        again = self.quiet(prepare.flow, self.root, 'provision', runner=runner(result(flowStatus='already-current')))
        self.assertEqual((again['flowStatus'], again['settingsChanged']), ('already-current', False))
        checked = self.quiet(prepare.flow, self.root, 'check', runner=runner(result(flowStatus='checked')))
        self.assertEqual((checked['scope'], checked['settingsChanged']), ('overtourism-flow-checked', False))

    def test_replace_keeps_an_operator_daily_limit_and_check_detects_a_stale_pin(self):
        self.fixture.ready_root()
        self.quiet(prepare.flow, self.root, 'provision', runner=runner())
        prepare.write_settings(self.root, {**self.settings(), 'GATEWAY_OVERTOURISM_DAILY_LIMIT': '4'})
        replaced = self.quiet(prepare.flow, self.root, 'replace', runner=runner(result(flowStatus='updated', flowSha256='e' * 64)))
        self.assertTrue(replaced['settingsChanged'])
        self.assertEqual(self.settings()['GATEWAY_OVERTOURISM_DAILY_LIMIT'], '4')
        self.assertEqual(self.settings()['LANGFLOW_OVERTOURISM_FLOW_SHA256'], 'e' * 64)
        with self.assertRaisesRegex(ValueError, '^OVERTOURISM_FLOW_PIN_MISMATCH$'):
            self.quiet(prepare.flow, self.root, 'check', runner=runner(result(flowStatus='checked')))

    def test_failures_keep_settings_and_expose_only_fixed_codes(self):
        self.fixture.ready_root()
        self.quiet(prepare.flow, self.root, 'provision', runner=runner())
        pinned = (self.root / prepare.SETTINGS).read_bytes()
        cases = [
            (runner(raw=json.dumps({'status': 'failed', 'code': 'OVERTOURISM_EXISTING_FLOW_DIFFERS'}).encode(), code=1), 'OVERTOURISM_EXISTING_FLOW_DIFFERS'),
            (runner(raw=b'private: sk-secret', code=1), 'OVERTOURISM_FLOW_FAILED_OUTPUT_WITHHELD'),
            (runner(result(modelExecuted=True)), 'OVERTOURISM_FLOW_RESULT_INVALID'),
            (runner(result(flowStatus='updated')), 'OVERTOURISM_FLOW_RESULT_INVALID'),
            (runner(result(apiKey='sk-' + 'x' * 30)), 'OVERTOURISM_FLOW_RESULT_INVALID'),
            (runner(result(flowId='not-a-uuid')), 'OVERTOURISM_FLOW_RESULT_INVALID'),
        ]
        for fake, code in cases:
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, f'^{code}$'):
                self.quiet(prepare.flow, self.root, 'provision', runner=fake)
            self.assertEqual((self.root / prepare.SETTINGS).read_bytes(), pinned)

    def test_disable_removes_only_the_pin(self):
        self.fixture.ready_root()
        self.quiet(prepare.flow, self.root, 'provision', runner=runner())
        self.assertTrue(self.quiet(prepare.disable, self.root)['settingsChanged'])
        self.assertFalse(os.path.lexists(self.root / prepare.SETTINGS))
        self.assertTrue(all((self.root / name).exists() for name in prepare.gateway.NAMES))
        self.assertFalse(self.quiet(prepare.disable, self.root)['settingsChanged'])
        with self.assertRaisesRegex(ValueError, '^OVERTOURISM_NOT_PROVISIONED$'):
            self.quiet(prepare.flow, self.root, 'check', runner=runner(result(flowStatus='checked')))

    def test_tampered_settings_fail_closed(self):
        self.fixture.ready_root()
        path = self.root / prepare.SETTINGS
        for content in ('LANGFLOW_OVERTOURISM_FLOW_ID=x\nLANGFLOW_OVERTOURISM_FLOW_SHA256=' + FLOW_HASH + '\n',
                        f'LANGFLOW_OVERTOURISM_FLOW_ID={FLOW_ID}\nLANGFLOW_OVERTOURISM_FLOW_SHA256={FLOW_HASH}\nOTHER=1\n',
                        f'LANGFLOW_OVERTOURISM_FLOW_ID={FLOW_ID}\nLANGFLOW_OVERTOURISM_FLOW_SHA256={FLOW_HASH}\nGATEWAY_OVERTOURISM_DAILY_LIMIT=99\n'):
            if path.exists():
                path.unlink()
            path.write_text(content)
            path.chmod(0o600)
            with self.subTest(content=content), self.assertRaisesRegex(ValueError, '^OVERTOURISM_SETTINGS_INVALID$'):
                self.quiet(prepare.flow, self.root, 'provision', runner=runner())


if __name__ == '__main__':
    unittest.main()
