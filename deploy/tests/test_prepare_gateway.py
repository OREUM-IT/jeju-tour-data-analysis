import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location('prepare_gateway', Path(__file__).parents[1] / 'prepare-gateway.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)

MCP_TOKEN = 'a' * 64
FLOW_ID = '12345678-1234-4abc-8def-123456789abc'
FLOW_HASH = 'b' * 64
API_KEY = 'sk-synthetic-gateway-api-key-for-tests'


class GatewaySettingsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root = self.new_root('.local')

    def new_root(self, name):
        root = self.directory / name
        root.mkdir(mode=0o700)
        mcp = root / 'mcp.env'
        mcp.write_text('MCP_SERVICE_TOKEN=' + MCP_TOKEN + '\n')
        mcp.chmod(0o600)
        return root

    def quiet(self, root=None, **kwargs):
        output = io.StringIO()
        errors = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = setup.prepare(root or self.root, **kwargs)
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(errors.getvalue(), '')
        return result

    @staticmethod
    def result(**overrides):
        return {'flowId': FLOW_ID, 'flowSha256': FLOW_HASH, 'apiKey': API_KEY, **overrides}

    @staticmethod
    def snapshot(root):
        return {name: ((root / name).read_bytes(), (root / name).stat().st_ino)
                for name in setup.NAMES}

    def test_init_creates_distinct_private_secret_pair_without_touching_other_settings(self):
        unrelated = self.root / 'langflow.env'
        unrelated.write_text('PRESERVE_EXISTING=synthetic-existing-setting\n')
        unrelated.chmod(0o600)
        before = {name: ((self.root / name).read_bytes(), (self.root / name).stat().st_ino)
                  for name in ['mcp.env', 'langflow.env']}
        result = self.quiet()
        self.assertEqual(result, {'status': 'preserved_or_prepared', 'stage': 'pending',
                                  'modelExecuted': False, 'productionReady': False})
        control, gateway = [setup.settings.read_env(self.root / name, private_mode=True)
                            for name in setup.NAMES]
        self.assertEqual(control['GATEWAY_CONTROL_TOKEN'], gateway['GATEWAY_CONTROL_TOKEN'])
        self.assertEqual(gateway['MCP_SERVICE_TOKEN'], MCP_TOKEN)
        self.assertEqual(len({control['GATEWAY_CONTROL_TOKEN'], gateway['GATEWAY_AUTH_KEY'], MCP_TOKEN}), 3)
        for key in ['GATEWAY_CONTROL_TOKEN', 'GATEWAY_AUTH_KEY']:
            self.assertRegex(gateway[key], r'^[a-f0-9]{64}$')
            self.assertNotIn(gateway[key], json.dumps(result))
        for name in setup.NAMES:
            self.assertEqual((self.root / name).stat().st_mode & 0o777, 0o600)
            self.assertEqual((self.root / name).stat().st_nlink, 1)
        self.assertEqual(before, {name: ((self.root / name).read_bytes(), (self.root / name).stat().st_ino)
                                 for name in before})

    def test_existing_complete_settings_are_reused_byte_for_byte_without_rotating_keys(self):
        self.quiet()
        before = self.snapshot(self.root)
        with patch.object(setup.secrets, 'token_hex', side_effect=AssertionError('must not rotate credentials')):
            self.assertEqual(self.quiet()['stage'], 'pending')
        self.assertEqual(before, self.snapshot(self.root))

    def test_a_partial_pair_fails_closed_and_preserves_the_remaining_file(self):
        for index, name in enumerate(setup.NAMES):
            with self.subTest(name=name):
                root = self.new_root(f'partial-{index}')
                target = root / name
                target.write_text('PRESERVE_PARTIAL=existing-data\n')
                target.chmod(0o600)
                before = (target.read_bytes(), target.stat().st_ino)
                runner = Mock(side_effect=AssertionError('no provisioning from partial settings'))
                for provision in [False, True]:
                    with self.assertRaisesRegex(ValueError, 'PARTIAL_GATEWAY_SETTINGS_RESTORE_REQUIRED'):
                        self.quiet(root, provision=provision, runner=runner)
                self.assertEqual(before, (target.read_bytes(), target.stat().st_ino))
                self.assertFalse((root / setup.NAMES[1 - index]).exists())
                runner.assert_not_called()

    def test_linked_or_insecure_settings_are_rejected_without_modifying_link_targets(self):
        linked_root = self.directory / 'linked-root'
        linked_root.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'PRIVATE_SETTINGS_DIRECTORY_REQUIRED'):
            self.quiet(linked_root)
        insecure = self.new_root('insecure')
        insecure.chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'PRIVATE_SETTINGS_DIRECTORY_REQUIRED'):
            self.quiet(insecure)
        self.assertEqual(insecure.stat().st_mode & 0o777, 0o755)
        for index, kind in enumerate(['symlink', 'hardlink', 'permissions']):
            with self.subTest(kind=kind):
                root = self.new_root(f'linked-{index}')
                self.quiet(root)
                target = root / 'gateway.env'
                original = self.directory / f'original-{index}'
                target.rename(original)
                if kind == 'symlink':
                    target.symlink_to(original)
                elif kind == 'hardlink':
                    os.link(original, target)
                else:
                    original.rename(target)
                    target.chmod(0o644)
                    original = target
                before = (original.read_bytes(), original.stat().st_mode, original.stat().st_ino)
                with self.assertRaises(ValueError):
                    self.quiet(root)
                self.assertEqual(before, (original.read_bytes(), original.stat().st_mode, original.stat().st_ino))

    def test_pending_to_ready_captures_one_api_key_privately_and_reexecution_issues_none(self):
        self.quiet()
        before = self.snapshot(self.root)
        pending = setup.settings.read_env(self.root / 'gateway.env')
        def run(command, **kwargs):
            marker = self.root / '.gateway-provisioning'
            self.assertTrue(marker.is_file())
            self.assertEqual(marker.stat().st_mode & 0o777, 0o600)
            for value in [MCP_TOKEN, API_KEY, pending['GATEWAY_AUTH_KEY'], pending['GATEWAY_CONTROL_TOKEN']]:
                self.assertNotIn(value, marker.read_text())
                self.assertNotIn(value, json.dumps(command))
            self.assertEqual(command[:3], ['docker', 'compose', '-p'])
            # The reviewed host builder is streamed; a stale bind-mounted copy is never run.
            self.assertEqual(command[-6:], ['exec', '-T', 'langflow', 'python', '-', '--provision'])
            self.assertEqual(kwargs, {'input': setup.BOOTSTRAP.read_bytes(), 'capture_output': True,
                                      'timeout': 180, 'check': False})
            return subprocess.CompletedProcess(command, 0, json.dumps(self.result()).encode(), b'')
        runner = Mock(side_effect=run)
        result = self.quiet(provision=True, runner=runner)
        self.assertEqual(result['stage'], 'ready')
        self.assertEqual(result['modelExecuted'], False)
        self.assertEqual(runner.call_count, 1)
        self.assertFalse((self.root / '.gateway-provisioning').exists())
        complete = setup.settings.read_env(self.root / 'gateway.env', private_mode=True)
        self.assertEqual(complete['LANGFLOW_GATEWAY_FLOW_ID'], FLOW_ID)
        self.assertEqual(complete['LANGFLOW_GATEWAY_FLOW_SHA256'], FLOW_HASH)
        self.assertEqual(complete['LANGFLOW_GATEWAY_API_KEY'], API_KEY)
        for key in pending:
            if key != 'GATEWAY_BOOTSTRAP_STATUS':
                self.assertEqual(complete[key], pending[key])
        self.assertEqual(before['gateway-control.env'], self.snapshot(self.root)['gateway-control.env'])
        ready_snapshot = self.snapshot(self.root)
        runner = Mock(side_effect=AssertionError('ready state must not issue another API key'))
        self.assertEqual(self.quiet(provision=True, runner=runner)['stage'], 'ready')
        self.assertEqual(self.quiet(runner=runner)['stage'], 'ready')
        self.assertEqual(ready_snapshot, self.snapshot(self.root))
        runner.assert_not_called()
        self.assertNotIn(API_KEY, json.dumps(result))

    def test_provision_without_init_never_calls_the_external_runner(self):
        runner = Mock(side_effect=AssertionError('no provisioning before init'))
        with self.assertRaisesRegex(ValueError, 'GATEWAY_INIT_REQUIRED'):
            self.quiet(provision=True, runner=runner)
        runner.assert_not_called()
        self.assertFalse(any((self.root / name).exists() for name in setup.NAMES))

    def test_failed_provision_keeps_pending_bytes_and_secret_free_marker_and_blocks_retry(self):
        for index, failure in enumerate(['nonzero', 'oversized', 'timeout']):
            with self.subTest(failure=failure):
                root = self.new_root(f'failure-{index}')
                self.quiet(root)
                before = self.snapshot(root)
                if failure == 'timeout':
                    runner = Mock(side_effect=subprocess.TimeoutExpired('synthetic-provision', 180, output=API_KEY.encode()))
                    expected = subprocess.TimeoutExpired
                else:
                    output = API_KEY.encode() if failure == 'nonzero' else b'x' * (16 * 1024 + 1)
                    runner = Mock(return_value=subprocess.CompletedProcess([], 1 if failure == 'nonzero' else 0, output, API_KEY.encode()))
                    expected = ValueError
                captured = io.StringIO()
                with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured), self.assertRaises(expected):
                    setup.prepare(root, provision=True, runner=runner)
                self.assertEqual(captured.getvalue(), '')
                self.assertEqual(before, self.snapshot(root))
                marker = root / '.gateway-provisioning'
                self.assertTrue(marker.exists())
                self.assertNotIn(API_KEY, marker.read_text())
                self.assertNotIn(MCP_TOKEN, marker.read_text())
                self.assertEqual(marker.stat().st_mode & 0o777, 0o600)
                retried = Mock(side_effect=AssertionError('incomplete provisioning cannot automatically retry'))
                with self.assertRaisesRegex(ValueError, 'PREVIOUS_PROVISIONING_INCOMPLETE_OPERATOR_REVIEW_REQUIRED'):
                    self.quiet(root, provision=True, runner=retried)
                retried.assert_not_called()

    def test_malformed_provision_results_are_withheld_and_never_publish_partial_credentials(self):
        invalid = [
            API_KEY.encode(), b'not-json', b'[]', b'null',
            json.dumps(self.result(extra=API_KEY)).encode(),
            json.dumps(self.result(flowId='-' * 36)).encode(),
            json.dumps(self.result(flowSha256='b' * 63)).encode(),
            json.dumps(self.result(flowSha256='B' * 64)).encode(),
            json.dumps(self.result(apiKey='sk-short')).encode(),
            json.dumps(self.result(apiKey='sk-' + 'x' * 257)).encode(),
            json.dumps(self.result(apiKey='sk-' + 'x' * 32 + '\nGATEWAY_MODE=public')).encode(),
        ]
        for index, raw in enumerate(invalid):
            with self.subTest(index=index):
                root = self.new_root(f'invalid-{index}')
                self.quiet(root)
                before = self.snapshot(root)
                runner = Mock(return_value=subprocess.CompletedProcess([], 0, raw, API_KEY.encode()))
                captured = io.StringIO()
                with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
                    with self.assertRaisesRegex(ValueError, '^GATEWAY_PROVISIONING_RESULT_INVALID$') as error:
                        setup.prepare(root, provision=True, runner=runner)
                self.assertEqual(captured.getvalue(), '')
                self.assertNotIn(API_KEY, str(error.exception))
                self.assertEqual(before, self.snapshot(root))
                self.assertTrue((root / '.gateway-provisioning').exists())

    def ready_root(self, name=None):
        root = self.new_root(name) if name else self.root
        self.quiet(root)
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(self.result()).encode(), b''))
        self.assertEqual(self.quiet(root, provision=True, runner=runner)['stage'], 'ready')
        return root

    @staticmethod
    def migration(**overrides):
        return {'status': 'passed', 'flowStatus': 'updated', 'flowId': FLOW_ID, 'flowSha256': 'c' * 64,
                'modelCallLimit': 8, 'build': 'vertex-8-call', 'messageStore': False,
                'modelExecuted': False, 'productionReady': False, **overrides}

    @staticmethod
    def checked(**overrides):
        return {'status': 'passed', 'flowMatches': True, 'nodeCount': 6, 'edgeCount': 7, 'toolCount': 3,
                'modelCallLimit': 8, 'build': 'vertex-8-call', 'messageStore': False, 'flowSha256': FLOW_HASH,
                'modelExecuted': False, 'productionReady': False, **overrides}

    def quiet_flow(self, mode, runner, root=None):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = setup.flow(root or self.root, mode, runner=runner)
        self.assertEqual(output.getvalue(), '')
        return result

    def test_flow_update_streams_reviewed_script_and_changes_only_the_pinned_hash(self):
        self.ready_root()
        before = self.snapshot(self.root)
        ready = setup.settings.read_env(self.root / 'gateway.env', private_mode=True)
        def run(command, **kwargs):
            self.assertEqual(command[:3], ['docker', 'compose', '-p'])
            self.assertEqual(command[-6:], ['exec', '-T', 'langflow', 'python', '-', '--update'])
            self.assertEqual(kwargs, {'input': setup.BOOTSTRAP.read_bytes(), 'capture_output': True,
                                      'timeout': 180, 'check': False})
            for value in ready.values():
                if len(value) >= 32:
                    self.assertNotIn(value, json.dumps(command))
            return subprocess.CompletedProcess(command, 0, json.dumps(self.migration()).encode(), b'')
        runner = Mock(side_effect=run)
        result = self.quiet_flow('update', runner)
        self.assertEqual(result, {'status': 'passed', 'scope': 'gateway-flow-updated', 'flowStatus': 'updated',
                                  'modelCallLimit': 8, 'build': 'vertex-8-call', 'messageStore': False,
                                  'settingsChanged': True, 'modelExecuted': False, 'productionReady': False})
        self.assertEqual(runner.call_count, 1)
        updated = setup.settings.read_env(self.root / 'gateway.env', private_mode=True)
        self.assertEqual(updated, {**ready, 'LANGFLOW_GATEWAY_FLOW_SHA256': 'c' * 64})
        self.assertEqual((self.root / 'gateway.env').stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root / 'gateway.env').stat().st_nlink, 1)
        self.assertEqual(before['gateway-control.env'], self.snapshot(self.root)['gateway-control.env'])
        self.assertNotIn(API_KEY, json.dumps(result))
        settled = self.snapshot(self.root)
        current = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(
            self.migration(flowStatus='already-current')).encode(), b''))
        self.assertFalse(self.quiet_flow('update', current)['settingsChanged'])
        self.assertEqual(settled, self.snapshot(self.root))

    def test_flow_rollback_and_check_use_their_own_limits_and_check_never_writes(self):
        self.ready_root()
        rollback = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(
            self.migration(modelCallLimit=4, build='legacy-4-call', flowSha256='d' * 64)).encode(), b''))
        result = self.quiet_flow('rollback', rollback)
        self.assertEqual((result['scope'], result['modelCallLimit'], result['settingsChanged']),
                         ('gateway-flow-rolled-back', 4, True))
        self.assertEqual(rollback.call_args.args[0][-1], '--rollback')
        self.assertEqual(setup.settings.read_env(self.root / 'gateway.env')['LANGFLOW_GATEWAY_FLOW_SHA256'], 'd' * 64)
        before = self.snapshot(self.root)
        check = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(self.checked(flowSha256='d' * 64)).encode(), b''))
        self.assertEqual(self.quiet_flow('check', check),
                         {'status': 'passed', 'scope': 'gateway-flow-checked', 'modelCallLimit': 8,
                          'build': 'vertex-8-call', 'messageStore': False,
                          'settingsChanged': False, 'modelExecuted': False, 'productionReady': False})
        self.assertEqual(check.call_args.args[0][-1], '--check')
        stale = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(self.checked()).encode(), b''))
        with self.assertRaisesRegex(ValueError, '^GATEWAY_FLOW_PIN_MISMATCH$'):
            self.quiet_flow('check', stale)
        self.assertEqual(before, self.snapshot(self.root))

    def test_rule_action_modes_stream_their_own_flags_and_pin_only_the_new_hash(self):
        self.ready_root()
        ready = setup.settings.read_env(self.root / 'gateway.env', private_mode=True)
        apply = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(
            self.migration(flowSha256='e' * 64, build='rule-action', messageStore=True)).encode(), b''))
        result = self.quiet_flow('rule-action', apply)
        self.assertEqual(result, {'status': 'passed', 'scope': 'gateway-rule-action-applied', 'flowStatus': 'updated',
                                  'modelCallLimit': 8, 'build': 'rule-action', 'messageStore': True,
                                  'settingsChanged': True, 'modelExecuted': False, 'productionReady': False})
        self.assertEqual(apply.call_args.args[0][-1], '--rule-action')
        self.assertEqual(setup.settings.read_env(self.root / 'gateway.env', private_mode=True),
                         {**ready, 'LANGFLOW_GATEWAY_FLOW_SHA256': 'e' * 64})
        self.assertTrue(os.path.lexists(self.root / setup.PENDING))
        before = self.snapshot(self.root)
        check = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(
            self.checked(flowSha256='e' * 64, build='rule-action')).encode(), b''))
        self.assertEqual(self.quiet_flow('check-rule-action', check)['scope'], 'gateway-rule-action-checked')
        self.assertEqual(check.call_args.args[0][-1], '--check-rule-action')
        self.assertEqual(before, self.snapshot(self.root))
        stale = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(
            self.checked(flowSha256='f' * 64, build='rule-action')).encode(), b''))
        with self.assertRaisesRegex(ValueError, '^GATEWAY_FLOW_PIN_MISMATCH$'):
            self.quiet_flow('check-rule-action', stale)
        # A check of the wrong build is not a pin result at all.
        for wrong in (self.checked(flowSha256='e' * 64), self.checked(flowSha256='e' * 64, build='legacy-4-call')):
            with self.assertRaisesRegex(ValueError, '^GATEWAY_FLOW_RESULT_INVALID$'):
                self.quiet_flow('check-rule-action', Mock(return_value=subprocess.CompletedProcess(
                    [], 0, json.dumps(wrong).encode(), b'')))
        back = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(self.migration(flowSha256='c' * 64)).encode(), b''))
        result = self.quiet_flow('rule-action-rollback', back)
        self.assertEqual((result['scope'], result['modelCallLimit'], result['settingsChanged']),
                         ('gateway-rule-action-rolled-back', 8, True))
        self.assertEqual(back.call_args.args[0][-1], '--rule-action-rollback')
        wrong = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(self.migration(modelCallLimit=4)).encode(), b''))
        with self.assertRaisesRegex(ValueError, '^GATEWAY_FLOW_RESULT_INVALID$'):
            self.quiet_flow('rule-action', wrong)
        # Earlier rule-action builds are only states a switch starts from: no mode may report or pin one.
        before = self.snapshot(self.root)
        for earlier in ('rule-action-low', 'rule-action-no-usage'):
            for mode, value in (('rule-action', self.migration(flowSha256='a' * 64, build=earlier)),
                                ('rule-action-rollback', self.migration(flowSha256='a' * 64, build=earlier)),
                                ('message-store-off', self.migration(flowSha256='a' * 64, build=earlier)),
                                ('check-rule-action', self.checked(flowSha256='c' * 64, build=earlier)),
                                ('check-message-store-off', self.checked(flowSha256='c' * 64, build=earlier))):
                with self.subTest(build=earlier, mode=mode), self.assertRaisesRegex(ValueError, '^GATEWAY_FLOW_RESULT_INVALID$'):
                    self.quiet_flow(mode, Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(value).encode(), b'')))
                self.assertEqual(before, self.snapshot(self.root))

    def test_message_store_modes_keep_the_build_validate_storage_and_pin_only_the_new_hash(self):
        self.ready_root()
        ready = setup.settings.read_env(self.root / 'gateway.env', private_mode=True)
        off = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(
            self.migration(flowSha256='e' * 64, build='rule-action')).encode(), b''))
        result = self.quiet_flow('message-store-off', off)
        self.assertEqual(result, {'status': 'passed', 'scope': 'gateway-message-store-off-applied', 'flowStatus': 'updated',
                                  'modelCallLimit': 8, 'build': 'rule-action', 'messageStore': False,
                                  'settingsChanged': True, 'modelExecuted': False, 'productionReady': False})
        self.assertEqual(off.call_args.args[0][-6:], ['exec', '-T', 'langflow', 'python', '-', '--message-store-off'])
        self.assertEqual(off.call_args.kwargs['input'], setup.BOOTSTRAP.read_bytes())
        # Only the pinned digest changes; the API key, flow ID and tokens stay byte-identical.
        self.assertEqual(setup.settings.read_env(self.root / 'gateway.env', private_mode=True),
                         {**ready, 'LANGFLOW_GATEWAY_FLOW_SHA256': 'e' * 64})
        self.assertTrue(os.path.lexists(self.root / setup.PENDING))
        before = self.snapshot(self.root)
        for mode, value in (('check-message-store-off', self.checked(flowSha256='e' * 64, build='rule-action')),
                            ('check-message-store-off', self.checked(flowSha256='e' * 64))):
            check = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(value).encode(), b''))
            checked = self.quiet_flow(mode, check)
            self.assertEqual((checked['scope'], checked['messageStore'], checked['settingsChanged']),
                             ('gateway-message-store-off-checked', False, False))
            self.assertEqual(check.call_args.args[0][-1], '--check-message-store-off')
        self.assertEqual(before, self.snapshot(self.root))
        invalid = [('message-store-off', self.migration(messageStore=True)),
                   ('message-store-off', self.migration(build='legacy-4-call', modelCallLimit=8)),
                   ('message-store-off', self.migration(messageStore='false')),
                   ('message-store-on', self.migration(messageStore=False)),
                   ('check-message-store-off', self.checked(flowSha256='e' * 64, messageStore=True)),
                   ('check-message-store-on', self.checked(flowSha256='e' * 64)),
                   ('update', {key: value for key, value in self.migration().items() if key != 'messageStore'})]
        for mode, value in invalid:
            with self.subTest(mode=mode, value=value), self.assertRaisesRegex(ValueError, '^GATEWAY_FLOW_RESULT_INVALID$'):
                self.quiet_flow(mode, Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(value).encode(), b'')))
            self.assertEqual(before, self.snapshot(self.root))
        stale = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(self.checked()).encode(), b''))
        with self.assertRaisesRegex(ValueError, '^GATEWAY_FLOW_PIN_MISMATCH$'):
            self.quiet_flow('check-message-store-off', stale)
        on = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(
            self.migration(flowSha256='c' * 64, build='rule-action', messageStore=True)).encode(), b''))
        restored = self.quiet_flow('message-store-on', on)
        self.assertEqual((restored['scope'], restored['messageStore'], restored['settingsChanged']),
                         ('gateway-message-store-on-restored', True, True))
        self.assertEqual(on.call_args.args[0][-1], '--message-store-on')
        check = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(
            self.checked(flowSha256='c' * 64, messageStore=True)).encode(), b''))
        self.assertEqual(self.quiet_flow('check-message-store-on', check)['scope'], 'gateway-message-store-on-checked')
        self.assertEqual(check.call_args.args[0][-1], '--check-message-store-on')

    def test_flow_modes_require_ready_settings_and_never_run_before_provisioning(self):
        runner = Mock(side_effect=AssertionError('no flow change before provisioning'))
        with self.assertRaisesRegex(ValueError, '^GATEWAY_INIT_REQUIRED$'):
            self.quiet_flow('update', runner)
        self.quiet()
        for mode in ('update', 'rollback', 'check'):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, '^GATEWAY_PROVISIONING_REQUIRED$'):
                self.quiet_flow(mode, runner)
        root = self.ready_root('marker')
        setup.write_private(root / '.gateway-provisioning', 'incomplete\n')
        with self.assertRaisesRegex(ValueError, '^GATEWAY_PROVISIONING_REQUIRED$'):
            self.quiet_flow('update', runner, root)
        with self.assertRaisesRegex(ValueError, '^GATEWAY_MODE_INVALID$'):
            self.quiet_flow('provision', runner)
        runner.assert_not_called()

    def test_flow_failures_keep_settings_and_expose_only_fixed_codes(self):
        self.ready_root()
        before = self.snapshot(self.root)
        cases = [
            (1, json.dumps({'status': 'failed', 'code': 'GATEWAY_EXISTING_FLOW_DIFFERS'}).encode(), '^GATEWAY_EXISTING_FLOW_DIFFERS$'),
            (1, API_KEY.encode(), '^GATEWAY_FLOW_CHANGE_FAILED_OUTPUT_WITHHELD$'),
            (0, b'x' * (16 * 1024 + 1), '^GATEWAY_FLOW_CHANGE_FAILED_OUTPUT_WITHHELD$'),
        ]
        invalid = [self.migration(flowId='87654321-1234-4abc-8def-123456789abc'), self.migration(extra=API_KEY),
                   self.migration(modelExecuted=True), self.migration(productionReady=True),
                   self.migration(modelCallLimit=4), self.migration(flowSha256='C' * 64),
                   self.migration(flowStatus='created'), self.migration(status='failed'), [], None]
        cases += [(0, json.dumps(value).encode(), '^GATEWAY_FLOW_RESULT_INVALID$') for value in invalid]
        cases += [(0, b'not-json', '^GATEWAY_FLOW_RESULT_INVALID$')]
        for index, (code, stdout, expected) in enumerate(cases):
            with self.subTest(index=index):
                runner = Mock(return_value=subprocess.CompletedProcess([], code, stdout, API_KEY.encode()))
                with self.assertRaisesRegex(ValueError, expected) as error:
                    self.quiet_flow('update', runner)
                self.assertNotIn(API_KEY, str(error.exception))
                self.assertEqual(before, self.snapshot(self.root))
        for value in (self.checked(flowMatches=False), self.checked(modelCallLimit=4), {**self.checked(), 'flowId': FLOW_ID}):
            with self.subTest(check=value):
                runner = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(value).encode(), b''))
                with self.assertRaisesRegex(ValueError, '^GATEWAY_FLOW_RESULT_INVALID$'):
                    self.quiet_flow('check', runner)
        self.assertEqual(before, self.snapshot(self.root))

    def test_switch_marker_spans_flow_change_until_explicit_completion(self):
        self.ready_root()
        marker = self.root / setup.PENDING
        self.assertEqual(setup.flow_change(self.root, 'pending')['flowChangePending'], False)
        check = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(self.checked()).encode(), b''))
        self.quiet_flow('check', check)
        self.assertFalse(os.path.lexists(marker))
        def run(command, **kwargs):
            # The marker exists before Langflow can change, so any interruption is resumable.
            self.assertTrue(marker.is_file())
            return subprocess.CompletedProcess(command, 0, json.dumps(self.migration()).encode(), b'')
        self.quiet_flow('update', Mock(side_effect=run))
        self.assertEqual((marker.stat().st_mode & 0o777, marker.stat().st_nlink), (0o600, 1))
        for value in setup.settings.read_env(self.root / 'gateway.env', private_mode=True).values():
            if len(value) >= 32:
                self.assertNotIn(value, marker.read_text())
        self.assertIs(setup.flow_change(self.root, 'pending')['flowChangePending'], True)
        failing = Mock(return_value=subprocess.CompletedProcess([], 1, b'', b''))
        with self.assertRaises(ValueError):
            self.quiet_flow('rollback', failing)
        self.assertTrue(marker.is_file())
        self.assertEqual(setup.flow_change(self.root, 'complete'),
                         {'status': 'passed', 'flowChangePending': False, 'markerCleared': True,
                          'modelExecuted': False, 'productionReady': False})
        self.assertFalse(os.path.lexists(marker))
        self.assertFalse(setup.flow_change(self.root, 'complete')['markerCleared'])
        with self.assertRaisesRegex(ValueError, '^GATEWAY_MODE_INVALID$'):
            setup.flow_change(self.root, 'remove')

    def test_refusal_before_any_patch_removes_only_a_marker_this_run_created(self):
        self.ready_root()
        marker = self.root / setup.PENDING
        refused = json.dumps({'status': 'failed', 'code': 'GATEWAY_EXISTING_FLOW_DIFFERS',
                              'modelExecuted': False, 'flowPatchAttempted': False}).encode()
        before = self.snapshot(self.root)
        with self.assertRaisesRegex(ValueError, '^GATEWAY_EXISTING_FLOW_DIFFERS$'):
            self.quiet_flow('update', Mock(return_value=subprocess.CompletedProcess([], 1, refused, b'')))
        # Nothing changed in Langflow or gateway.env, so there is nothing to resume.
        self.assertFalse(os.path.lexists(marker))
        self.assertEqual(before, self.snapshot(self.root))
        ambiguous = [
            (1, json.dumps({'status': 'failed', 'code': 'GATEWAY_BOOTSTRAP_HTTP_REJECTED',
                            'modelExecuted': False, 'flowPatchAttempted': True}).encode()),
            (1, json.dumps({'status': 'failed', 'code': 'GATEWAY_BOOTSTRAP_FAILED', 'modelExecuted': False}).encode()),
            (1, b'not-json'), (0, b'x' * (16 * 1024 + 1)),
        ]
        for code, stdout in ambiguous:
            with self.subTest(stdout=stdout[:60]):
                with self.assertRaises(ValueError):
                    self.quiet_flow('rollback', Mock(return_value=subprocess.CompletedProcess([], code, stdout, b'')))
                self.assertTrue(marker.is_file())
                marker.unlink()
        timeout = Mock(side_effect=subprocess.TimeoutExpired('synthetic-switch', 180))
        with self.assertRaises(subprocess.TimeoutExpired):
            self.quiet_flow('update', timeout)
        self.assertTrue(marker.is_file())
        # A marker left by an earlier interrupted run is never cleared by a refusal.
        with self.assertRaisesRegex(ValueError, '^GATEWAY_EXISTING_FLOW_DIFFERS$'):
            self.quiet_flow('update', Mock(return_value=subprocess.CompletedProcess([], 1, refused, b'')))
        self.assertTrue(marker.is_file())

    def test_linked_or_altered_switch_marker_fails_closed_before_langflow(self):
        for index, kind in enumerate(['symlink', 'hardlink', 'permissions', 'directory']):
            with self.subTest(kind=kind):
                root = self.ready_root(f'marker-{kind}')
                marker = root / setup.PENDING
                target = self.directory / f'marker-target-{index}'
                target.write_text('keep\n')
                target.chmod(0o600)
                if kind == 'symlink':
                    marker.symlink_to(target)
                elif kind == 'hardlink':
                    os.link(target, marker)
                elif kind == 'permissions':
                    setup.write_private(marker, 'x\n')
                    marker.chmod(0o644)
                else:
                    marker.mkdir(mode=0o700)
                runner = Mock(side_effect=AssertionError('no flow change with an untrusted marker'))
                for action in ('pending', 'complete'):
                    with self.assertRaisesRegex(ValueError, '^GATEWAY_FLOW_CHANGE_MARKER_INVALID$'):
                        setup.flow_change(root, action)
                with self.assertRaisesRegex(ValueError, '^GATEWAY_FLOW_CHANGE_MARKER_INVALID$'):
                    self.quiet_flow('update', runner, root)
                runner.assert_not_called()
                self.assertTrue(os.path.lexists(marker))
                self.assertEqual(target.read_text(), 'keep\n')

    def test_cli_reports_pending_with_exit_codes_and_never_prints_secrets(self):
        source = Path(setup.__file__).parent
        deploy = self.directory / 'deploy-copy'
        deploy.mkdir()
        for name in ('prepare-gateway.py', 'prepare-langflow.py'):
            (deploy / name).write_bytes((source / name).read_bytes())
        root = deploy / '.local'
        root.mkdir(mode=0o700)
        (root / 'mcp.env').write_text('MCP_SERVICE_TOKEN=' + MCP_TOKEN + '\n')
        (root / 'mcp.env').chmod(0o600)
        def cli(*arguments):
            return subprocess.run([sys.executable, '-I', str(deploy / 'prepare-gateway.py'), *arguments],
                                  capture_output=True, text=True, timeout=30, check=False)
        absent = cli('--flow-change-pending')
        self.assertEqual((absent.returncode, json.loads(absent.stdout)['flowChangePending']), (setup.NOT_PENDING_EXIT, False))
        setup.write_private(root / setup.PENDING, 'started\n')
        present = cli('--flow-change-pending')
        self.assertEqual((present.returncode, json.loads(present.stdout)['flowChangePending']), (0, True))
        cleared = cli('--complete-flow-change')
        self.assertEqual((cleared.returncode, json.loads(cleared.stdout)['markerCleared']), (0, True))
        self.assertFalse(os.path.lexists(root / setup.PENDING))
        # Switch failures say whether an unfinished switch is actually recorded.
        nothing = cli('--update-flow')
        self.assertEqual((nothing.returncode, json.loads(nothing.stdout)['code']), (1, 'GATEWAY_INIT_REQUIRED'))
        self.assertIn('did not change the Langflow flow', json.loads(nothing.stdout)['details'])
        for flag, switching in (('--message-store-off-flow', True), ('--message-store-on-flow', True),
                                ('--check-message-store-off-flow', False), ('--check-message-store-on-flow', False)):
            refused = cli(flag)
            details = json.loads(refused.stdout)['details']
            self.assertEqual((refused.returncode, json.loads(refused.stdout)['code']), (1, 'GATEWAY_INIT_REQUIRED'), flag)
            self.assertEqual('did not change the Langflow flow' in details, switching, flag)
        setup.write_private(root / setup.PENDING, 'started\n')
        unfinished = cli('--rollback-flow')
        self.assertIn('unfinished', json.loads(unfinished.stdout)['details'])
        (root / setup.PENDING).unlink()
        root.chmod(0o755)
        broken = cli('--flow-change-pending')
        self.assertEqual((broken.returncode, json.loads(broken.stdout)['code']), (1, 'PRIVATE_SETTINGS_DIRECTORY_REQUIRED'))
        for result in (absent, present, cleared, broken):
            self.assertNotIn(MCP_TOKEN, result.stdout + result.stderr)

    def test_failed_pair_publication_rolls_back_only_its_own_files(self):
        original_link = os.link
        def fail_second(source, destination, **kwargs):
            if Path(destination).name == 'gateway.env':
                raise PermissionError('synthetic publication failure')
            return original_link(source, destination, **kwargs)
        with patch.object(setup.os, 'link', side_effect=fail_second):
            with self.assertRaises(OSError):
                self.quiet()
        self.assertFalse(any((self.root / name).exists() for name in setup.NAMES))
        self.assertEqual(list(self.root.glob('.gateway-stage-*')), [])
        self.assertEqual(self.quiet()['stage'], 'pending')


if __name__ == '__main__':
    unittest.main()
