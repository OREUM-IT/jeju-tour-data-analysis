"""Deployment ordering with fake commands; no Docker, model calls or real secrets."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'apply-agent-call-budget.sh'
MOCK = r'''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
name = Path(sys.argv[0]).name
if name == 'python3':
    phase = {('check-agent-queue-idle.py',): 'idle', ('check-agent-queue-idle.py', '--queue-ready'): 'ready',
             ('check-agent-queue-idle.py', '--mcp-only'): 'mcp_idle',
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
if phase in failures:
    raise SystemExit(failures[phase])
if phase == 'pending' and os.environ.get('APPLY_PENDING') != '1':
    print(json.dumps({'status': 'passed', 'flowChangePending': False}))
    raise SystemExit(3)
print(json.dumps({'status': 'passed', 'scope': phase, 'modelExecuted': False}))
'''
APPLY = ['config', 'pending', 'idle', 'update', 'up', 'flow_check', 'public', 'ready', 'complete']
VERIFY = ['config', 'pending', 'flow_check', 'public', 'ready']
ROLLBACK = ['config', 'pending', 'idle', 'rollback', 'up', 'public', 'ready', 'complete']
RESUME = ['config', 'pending', 'idle', 'mcp_idle', 'update', 'up', 'flow_check', 'public', 'ready', 'complete']
UNCHANGED = '이번 실행에서는 흐름과 서비스를 바꾸지 않았습니다'
RESUME_HINT = '같은 명령을 다시 실행하면 이어서 마무리합니다'


class ApplyAgentCallBudgetTests(unittest.TestCase):
    def run_apply(self, failures=None, arguments=(), pending=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('docker', 'python3'):
                executable = root / name
                executable.write_text(f'#!{sys.executable}\n' + MOCK, encoding='utf-8')
                executable.chmod(0o700)
            log = root / 'commands.jsonl'
            env = {**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH'],
                   'APPLY_TEST_LOG': str(log), 'COMPOSE_PROJECT_NAME': 'budget-test-project',
                   'APPLY_FAILURES': json.dumps(failures or {}), 'APPLY_PENDING': '1' if pending else '0'}
            result = subprocess.run(['bash', str(SCRIPT), *arguments], env=env, input='', capture_output=True,
                                    text=True, timeout=10)
            commands = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            return result, commands

    def phases(self, commands):
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
                              '--provision', '--init', 'unknown'):
                self.assertNotIn(forbidden, args)
                self.assertNotEqual(command['phase'], forbidden)

    def test_apply_changes_flow_after_idle_check_recreates_only_gateway_and_clears_marker_last(self):
        result, commands = self.run_apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.phases(commands), APPLY)
        self.assert_scoped(commands)
        self.assertIn('"scope":"agent-call-budget-applied"', result.stdout)
        self.assertIn('"modelCallLimit":8', result.stdout)
        self.assertIn('"modelExecuted":false', result.stdout)
        self.assertIn('"productionReady":false', result.stdout)

    def test_verify_only_never_checks_idle_changes_flow_restarts_or_clears_marker(self):
        result, commands = self.run_apply(arguments=('--verify-only',))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.phases(commands), VERIFY)
        self.assert_scoped(commands)
        self.assertIn('"scope":"agent-call-budget-verified"', result.stdout)
        self.assertIn('"servicesRestarted":false', result.stdout)

    def test_rollback_pins_previous_flow_and_skips_the_eight_call_check(self):
        result, commands = self.run_apply(arguments=('--rollback',))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.phases(commands), ROLLBACK)
        self.assert_scoped(commands)
        self.assertIn('"scope":"agent-call-budget-rolled-back"', result.stdout)
        self.assertIn('"modelCallLimit":4', result.stdout)

    def test_unfinished_switch_resumes_when_the_gateway_refuses_readiness(self):
        for arguments, expected in (((), RESUME),
                                    (('--rollback',), ['config', 'pending', 'idle', 'mcp_idle', 'rollback', 'up',
                                                       'public', 'ready', 'complete'])):
            with self.subTest(arguments=arguments):
                result, commands = self.run_apply({'idle': 1}, arguments, pending=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.phases(commands), expected)
                self.assert_scoped(commands)
                self.assertIn('MCP 실행 상태만 확인하고 이어서 진행합니다', result.stderr)
        result, commands = self.run_apply(pending=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.phases(commands), APPLY)

    def test_resume_still_waits_for_queued_running_or_live_mcp_work(self):
        for failures, expected in (({'idle': 75}, ['config', 'pending', 'idle']),
                                   ({'idle': 1, 'mcp_idle': 75}, ['config', 'pending', 'idle', 'mcp_idle'])):
            with self.subTest(failures=failures):
                result, commands = self.run_apply(failures, pending=True)
                self.assertEqual(result.returncode, 75)
                self.assertEqual(self.phases(commands), expected)
                self.assertNotIn('"status":"passed"', result.stdout)
        result, commands = self.run_apply({'idle': 1, 'mcp_idle': 1}, pending=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.phases(commands), ['config', 'pending', 'idle', 'mcp_idle'])
        self.assertIn('MCP 실행 상태를 확인하지 못했습니다', result.stderr)

    def test_verify_only_refuses_an_unfinished_switch(self):
        result, commands = self.run_apply(arguments=('--verify-only',), pending=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.phases(commands), ['config', 'pending'])
        self.assertIn('이전 적용이 끝나지 않았습니다', result.stderr)

    def test_unknown_arguments_fail_before_any_external_command(self):
        for arguments in (('--unknown',), ('--verify-only', '--rollback'), ('--rollback', '--unknown')):
            with self.subTest(arguments=arguments):
                result, commands = self.run_apply(arguments=arguments)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(commands, [])

    def test_busy_queue_without_a_marker_changes_nothing_and_requires_manual_retry(self):
        for arguments in ((), ('--rollback',)):
            with self.subTest(arguments=arguments):
                result, commands = self.run_apply({'idle': 75}, arguments)
                self.assertEqual(result.returncode, 75)
                self.assertEqual(self.phases(commands), ['config', 'pending', 'idle'])
                self.assertIn('불확실 실행 보호 시간', result.stderr)
                self.assertIn(UNCHANGED, result.stderr)
                self.assertNotIn('"status":"passed"', result.stdout)

    def test_any_failure_stops_before_later_phases_with_an_accurate_message(self):
        for arguments, phases in (((), APPLY), (('--verify-only',), VERIFY), (('--rollback',), ROLLBACK)):
            for failure in phases:
                with self.subTest(arguments=arguments, failure=failure):
                    result, commands = self.run_apply({failure: 1}, arguments)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(self.phases(commands), phases[:phases.index(failure) + 1])
                    self.assertNotIn('agent-call-budget-', result.stdout)
                    changed = phases.index(failure) >= phases.index('up') - 1 if 'up' in phases else False
                    if failure == 'pending':
                        self.assertIn('이전 흐름 변경 상태를 확인하지 못해 중단했습니다', result.stderr)
                    elif failure == 'idle':
                        self.assertIn(UNCHANGED, result.stderr)
                    else:
                        self.assertIn(f'AGENT_CALL_BUDGET_APPLY_FAILED: phase=', result.stderr)
                        self.assertIn('modelExecuted=false', result.stderr)
                    if changed:
                        self.assertIn(RESUME_HINT, result.stderr)
                    else:
                        self.assertNotIn(RESUME_HINT, result.stderr)


if __name__ == '__main__':
    unittest.main()
