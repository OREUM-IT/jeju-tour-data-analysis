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
             ('prepare-gateway.py', '--update-flow'): 'update', ('prepare-gateway.py', '--rollback-flow'): 'rollback',
             ('prepare-gateway.py', '--check-flow'): 'flow_check',
             ('tunnel/check.py', '--scope', 'public'): 'public'}.get(tuple(args), 'unknown')
else:
    phase = next((item for item in args if item in ['config', 'build', 'up', 'exec', 'restart', 'down', 'rm']), 'unknown')
with open(os.environ['APPLY_TEST_LOG'], 'a') as log:
    log.write(json.dumps({'name': name, 'phase': phase, 'args': args, 'project': os.environ.get('COMPOSE_PROJECT_NAME')}) + '\n')
if phase == os.environ.get('APPLY_FAIL_PHASE'):
    raise SystemExit(int(os.environ.get('APPLY_EXIT_CODE', '1')))
print(json.dumps({'status': 'passed', 'scope': phase, 'modelExecuted': False}))
'''
APPLY = ['config', 'idle', 'update', 'up', 'flow_check', 'public', 'ready']
VERIFY = ['config', 'flow_check', 'public', 'ready']
ROLLBACK = ['config', 'idle', 'rollback', 'up', 'public', 'ready']


class ApplyAgentCallBudgetTests(unittest.TestCase):
    def run_apply(self, failure=None, code=1, arguments=()):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('docker', 'python3'):
                executable = root / name
                executable.write_text(f'#!{sys.executable}\n' + MOCK, encoding='utf-8')
                executable.chmod(0o700)
            log = root / 'commands.jsonl'
            env = {**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH'],
                   'APPLY_TEST_LOG': str(log), 'COMPOSE_PROJECT_NAME': 'budget-test-project',
                   'APPLY_EXIT_CODE': str(code)}
            if failure:
                env['APPLY_FAIL_PHASE'] = failure
            else:
                env.pop('APPLY_FAIL_PHASE', None)
            result = subprocess.run(['bash', str(SCRIPT), *arguments], env=env, input='', capture_output=True,
                                    text=True, timeout=10)
            commands = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            return result, commands

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

    def test_apply_changes_flow_after_idle_check_and_recreates_only_gateway(self):
        result, commands = self.run_apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([item['phase'] for item in commands], APPLY)
        self.assert_scoped(commands)
        self.assertIn('"scope":"agent-call-budget-applied"', result.stdout)
        self.assertIn('"modelCallLimit":8', result.stdout)
        self.assertIn('"modelExecuted":false', result.stdout)
        self.assertIn('"productionReady":false', result.stdout)

    def test_verify_only_never_checks_idle_changes_flow_or_restarts(self):
        result, commands = self.run_apply(arguments=('--verify-only',))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([item['phase'] for item in commands], VERIFY)
        self.assert_scoped(commands)
        self.assertIn('"scope":"agent-call-budget-verified"', result.stdout)
        self.assertIn('"servicesRestarted":false', result.stdout)

    def test_rollback_pins_previous_flow_and_skips_the_eight_call_check(self):
        result, commands = self.run_apply(arguments=('--rollback',))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([item['phase'] for item in commands], ROLLBACK)
        self.assert_scoped(commands)
        self.assertIn('"scope":"agent-call-budget-rolled-back"', result.stdout)
        self.assertIn('"modelCallLimit":4', result.stdout)

    def test_unknown_arguments_fail_before_any_external_command(self):
        for arguments in (('--unknown',), ('--verify-only', '--rollback'), ('--rollback', '--unknown')):
            with self.subTest(arguments=arguments):
                result, commands = self.run_apply(arguments=arguments)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(commands, [])

    def test_busy_or_uncertain_queue_changes_nothing_and_requires_manual_retry(self):
        for arguments in ((), ('--rollback',)):
            with self.subTest(arguments=arguments):
                result, commands = self.run_apply('idle', 75, arguments)
                self.assertEqual(result.returncode, 75)
                self.assertEqual([item['phase'] for item in commands], ['config', 'idle'])
                self.assertIn('불확실 실행 보호 시간', result.stderr)
                self.assertIn('흐름과 서비스를 바꾸지 않았습니다', result.stderr)
                self.assertNotIn('"status":"passed"', result.stdout)

    def test_any_failure_stops_before_later_phases_and_never_reports_success(self):
        for arguments, phases in (((), APPLY), (('--verify-only',), VERIFY), (('--rollback',), ROLLBACK)):
            for failure in phases:
                with self.subTest(arguments=arguments, failure=failure):
                    result, commands = self.run_apply(failure, arguments=arguments)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual([item['phase'] for item in commands], phases[:phases.index(failure) + 1])
                    self.assertNotIn('agent-call-budget-', result.stdout)
                    if failure == 'idle':
                        self.assertIn('상태를 확인하지 못해 중단했습니다. 흐름과 서비스를 바꾸지 않았습니다.', result.stderr)
                    else:
                        self.assertIn('AGENT_CALL_BUDGET_APPLY_FAILED: phase=', result.stderr)
                        self.assertIn('modelExecuted=false', result.stderr)


if __name__ == '__main__':
    unittest.main()
