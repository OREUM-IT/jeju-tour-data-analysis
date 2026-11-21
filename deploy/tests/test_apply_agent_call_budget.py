"""Deployment ordering with fake commands; no Docker, model calls or real secrets."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'apply-agent-call-budget.sh'
# The fake prepare-gateway.py keeps the unfinished-switch marker in a temporary
# directory, like the real one: written by the switch, cleared by completion.
MOCK = r'''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
name = Path(sys.argv[0]).name
marker = Path(os.environ['APPLY_STATE_DIR']) / 'marker'
if name == 'python3':
    phase = {('check-agent-queue-idle.py',): 'idle', ('check-agent-queue-idle.py', '--queue-ready'): 'ready',
             ('check-agent-queue-idle.py', '--resume'): 'resume_idle',
             ('prepare-gateway.py', '--flow-change-pending'): 'pending',
             ('prepare-gateway.py', '--complete-flow-change'): 'complete',
             ('prepare-gateway.py', '--update-flow'): 'update', ('prepare-gateway.py', '--rollback-flow'): 'rollback',
             ('prepare-gateway.py', '--check-flow'): 'flow_check',
             ('tunnel/check.py', '--scope', 'public'): 'public'}.get(tuple(args), 'unknown')
else:
    phase = next((item for item in args if item in ['config', 'build', 'up', 'exec', 'restart', 'down', 'rm']), 'unknown')
with open(os.environ['APPLY_TEST_LOG'], 'a') as log:
    log.write(json.dumps({'name': name, 'phase': phase, 'args': args, 'project': os.environ.get('COMPOSE_PROJECT_NAME')}) + '\n')
failures = json.loads(os.environ.get('APPLY_FAILURES', '{}'))
if phase in ('update', 'rollback') and not (phase in failures and os.environ.get('APPLY_REFUSED_BEFORE_PATCH') == '1'):
    marker.write_text('started\n')
if phase in failures:
    raise SystemExit(failures[phase])
if phase == 'pending' and not marker.exists():
    print(json.dumps({'status': 'passed', 'flowChangePending': False}))
    raise SystemExit(3)
if phase == 'complete':
    marker.unlink(missing_ok=True)
print(json.dumps({'status': 'passed', 'scope': phase, 'modelExecuted': False}))
'''
APPLY = ['config', 'pending', 'idle', 'update', 'up', 'flow_check', 'public', 'ready', 'complete']
VERIFY = ['config', 'pending', 'flow_check', 'public', 'ready']
ROLLBACK = ['config', 'pending', 'idle', 'rollback', 'up', 'public', 'ready', 'complete']
RESUME = ['config', 'pending', 'idle', 'resume_idle', 'update', 'up', 'flow_check', 'public', 'ready', 'complete']
UNCHANGED = '이번 실행에서는 흐름과 서비스를 바꾸지 않았습니다'
RESUME_HINT = '같은 명령을 다시 실행하면 이어서 마무리합니다'


class ApplyAgentCallBudgetTests(unittest.TestCase):
    def run_apply(self, failures=None, arguments=(), pending=False, refused_before_patch=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('docker', 'python3'):
                executable = root / name
                executable.write_text(f'#!{sys.executable}\n' + MOCK, encoding='utf-8')
                executable.chmod(0o700)
            state = root / 'state'
            state.mkdir()
            if pending:
                (state / 'marker').write_text('started\n')
            log = root / 'commands.jsonl'
            env = {**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH'],
                   'APPLY_TEST_LOG': str(log), 'COMPOSE_PROJECT_NAME': 'budget-test-project',
                   'APPLY_FAILURES': json.dumps(failures or {}), 'APPLY_STATE_DIR': str(state),
                   'APPLY_REFUSED_BEFORE_PATCH': '1' if refused_before_patch else '0'}
            result = subprocess.run(['bash', str(SCRIPT), *arguments], env=env, input='', capture_output=True,
                                    text=True, timeout=20)
            commands = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            return result, commands, (state / 'marker').exists()

    @staticmethod
    def phases(commands):
        return [item['phase'] for item in commands]

    def assert_scoped(self, commands):
        for command in commands:
            self.assertEqual(command['project'], 'budget-test-project')
            args = command['args']
            if command['name'] == 'docker':
                self.assertEqual(args[:4], ['compose', '-p', 'budget-test-project', '-f'])
                self.assertEqual([args[i + 1] for i, value in enumerate(args) if value == '-f'],
                                 ['compose.yaml', 'compose.catalog.yaml', 'compose.langflow.yaml',
                                  'compose.gateway.yaml', 'compose.tunnel.yaml'])
            if command['phase'] == 'up':
                self.assertEqual(args[-1], 'agent-gateway')
                for required in ('--no-deps', '--force-recreate', '--wait'):
                    self.assertIn(required, args)
                self.assertNotIn('langflow', args)
                self.assertNotIn('jeju-mcp', args)
            for forbidden in ('build', 'down', 'rm', 'restart', 'stop', 'kill', '--volumes', '--run-once',
                              '--provision', '--init', '--mcp-only', 'unknown'):
                self.assertNotIn(forbidden, args)
                self.assertNotEqual(command['phase'], forbidden)

    def test_apply_changes_flow_after_idle_check_recreates_only_gateway_and_clears_marker_last(self):
        result, commands, marker = self.run_apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.phases(commands), APPLY)
        self.assertFalse(marker)
        self.assert_scoped(commands)
        self.assertIn('"scope":"agent-call-budget-applied"', result.stdout)
        self.assertIn('"modelCallLimit":8', result.stdout)
        self.assertIn('"modelExecuted":false', result.stdout)
        self.assertIn('"productionReady":false', result.stdout)

    def test_verify_only_never_checks_idle_changes_flow_restarts_or_clears_marker(self):
        result, commands, marker = self.run_apply(arguments=('--verify-only',))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.phases(commands), VERIFY)
        self.assertFalse(marker)
        self.assert_scoped(commands)
        self.assertIn('"scope":"agent-call-budget-verified"', result.stdout)
        self.assertIn('"servicesRestarted":false', result.stdout)

    def test_rollback_pins_previous_flow_and_skips_the_eight_call_check(self):
        result, commands, marker = self.run_apply(arguments=('--rollback',))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.phases(commands), ROLLBACK)
        self.assertFalse(marker)
        self.assert_scoped(commands)
        self.assertIn('"scope":"agent-call-budget-rolled-back"', result.stdout)
        self.assertIn('"modelCallLimit":4', result.stdout)

    def test_flow_changed_or_unfinished_switch_resumes_through_the_local_gate(self):
        rollback = ['config', 'pending', 'idle', 'resume_idle', 'rollback', 'up', 'public', 'ready', 'complete']
        cases = [
            # Gateway answers FLOW_CHANGED, with or without a marker (e.g. an older helper).
            ({'idle': 76}, (), False, RESUME, 'FLOW_CHANGED'),
            ({'idle': 76}, ('--rollback',), True, rollback, 'FLOW_CHANGED'),
            # Marker present and the public gate fails for any reason.
            ({'idle': 1}, (), True, RESUME, '이전 흐름 변경이 끝나지 않았습니다'),
            ({'idle': 1}, ('--rollback',), True, rollback, '이전 흐름 변경이 끝나지 않았습니다'),
        ]
        for failures, arguments, pending, expected, message in cases:
            with self.subTest(failures=failures, arguments=arguments, pending=pending):
                result, commands, marker = self.run_apply(failures, arguments, pending=pending)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.phases(commands), expected)
                self.assertFalse(marker)
                self.assert_scoped(commands)
                self.assertIn(message, result.stderr)
        # An unfinished switch whose public gate passes resumes without the local gate.
        result, commands, marker = self.run_apply(pending=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.phases(commands), APPLY)
        self.assertFalse(marker)

    def test_resume_still_waits_for_gateway_queue_or_live_mcp_work(self):
        for failures, pending in (({'idle': 75}, True), ({'idle': 76, 'resume_idle': 75}, False),
                                  ({'idle': 1, 'resume_idle': 75}, True)):
            with self.subTest(failures=failures, pending=pending):
                result, commands, marker = self.run_apply(failures, pending=pending)
                self.assertEqual(result.returncode, 75)
                expected = ['config', 'pending', 'idle'] + (['resume_idle'] if 'resume_idle' in failures else [])
                self.assertEqual(self.phases(commands), expected)
                self.assertEqual(marker, pending)
                self.assertIn('불확실 실행 보호 시간', result.stderr)
                self.assertNotIn('"status":"passed"', result.stdout)
        result, commands, _ = self.run_apply({'idle': 76, 'resume_idle': 1})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.phases(commands), ['config', 'pending', 'idle', 'resume_idle'])
        self.assertIn('Gateway 내부 작업 수 또는 MCP 실행 상태를 확인하지 못했습니다', result.stderr)

    def test_public_gate_failure_without_marker_changes_nothing(self):
        result, commands, marker = self.run_apply({'idle': 1})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.phases(commands), ['config', 'pending', 'idle'])
        self.assertFalse(marker)
        self.assertIn(UNCHANGED, result.stderr)

    def test_verify_only_refuses_an_unfinished_switch(self):
        result, commands, marker = self.run_apply(arguments=('--verify-only',), pending=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.phases(commands), ['config', 'pending'])
        self.assertTrue(marker)
        self.assertIn('이전 적용이 끝나지 않았습니다', result.stderr)

    def test_unknown_arguments_fail_before_any_external_command(self):
        for arguments in (('--unknown',), ('--verify-only', '--rollback'), ('--rollback', '--unknown')):
            with self.subTest(arguments=arguments):
                result, commands, _ = self.run_apply(arguments=arguments)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(commands, [])

    def test_busy_queue_without_a_marker_changes_nothing_and_requires_manual_retry(self):
        for arguments in ((), ('--rollback',)):
            with self.subTest(arguments=arguments):
                result, commands, marker = self.run_apply({'idle': 75}, arguments)
                self.assertEqual(result.returncode, 75)
                self.assertEqual(self.phases(commands), ['config', 'pending', 'idle'])
                self.assertFalse(marker)
                self.assertIn('불확실 실행 보호 시간', result.stderr)
                self.assertIn(UNCHANGED, result.stderr)
                self.assertNotIn('"status":"passed"', result.stdout)

    def test_refusal_before_any_patch_leaves_no_marker_and_no_resume_hint(self):
        for arguments, phase in (((), 'update'), (('--rollback',), 'rollback')):
            with self.subTest(arguments=arguments):
                result, commands, marker = self.run_apply({phase: 1}, arguments, refused_before_patch=True)
                self.assertEqual(result.returncode, 1)
                self.assertFalse(marker)
                self.assertEqual(self.phases(commands), ['config', 'pending', 'idle', phase, 'pending'])
                self.assertIn(f'AGENT_CALL_BUDGET_APPLY_FAILED: phase=flow_change', result.stderr)
                self.assertNotIn(RESUME_HINT, result.stderr)

    def test_any_failure_stops_before_later_phases_with_an_accurate_message(self):
        for arguments, phases in (((), APPLY), (('--verify-only',), VERIFY), (('--rollback',), ROLLBACK)):
            for failure in phases:
                with self.subTest(arguments=arguments, failure=failure):
                    result, commands, marker = self.run_apply({failure: 1}, arguments)
                    self.assertEqual(result.returncode, 1)
                    seen = self.phases(commands)
                    prefix = phases[:phases.index(failure) + 1]
                    self.assertEqual(seen[:len(prefix)], prefix)
                    self.assertNotIn('agent-call-budget-', result.stdout)
                    switched = failure in phases[phases.index('pending') + 1:] and 'up' in phases \
                        and phases.index(failure) >= phases.index('up') - 1
                    if failure == 'pending':
                        self.assertIn('이전 흐름 변경 상태를 확인하지 못해 중단했습니다', result.stderr)
                        self.assertEqual(seen, prefix)
                    elif failure == 'idle':
                        self.assertIn(UNCHANGED, result.stderr)
                        self.assertEqual(seen, prefix)
                    elif failure == 'config':
                        self.assertIn('AGENT_CALL_BUDGET_APPLY_FAILED: phase=config', result.stderr)
                        self.assertEqual(seen, prefix)
                    else:
                        # The ERR trap asks for the marker state once, read-only.
                        self.assertIn('AGENT_CALL_BUDGET_APPLY_FAILED: phase=', result.stderr)
                        self.assertIn('modelExecuted=false', result.stderr)
                        self.assertEqual(seen, prefix + ['pending'])
                    self.assertEqual(marker, switched)
                    if switched:
                        self.assertIn(RESUME_HINT, result.stderr)
                    else:
                        self.assertNotIn(RESUME_HINT, result.stderr)


if __name__ == '__main__':
    unittest.main()
