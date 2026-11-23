"""Rule-action deployment ordering with fake commands; no Docker, model calls or real secrets."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'apply-agent-rule-action.sh'
MOCK = r'''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
name = Path(sys.argv[0]).name
marker = Path(os.environ['APPLY_STATE_DIR']) / 'marker'
counter = Path(os.environ['APPLY_STATE_DIR']) / 'idle-count'
if name == 'python3':
    phase = {('check-agent-queue-idle.py',): 'idle', ('check-agent-queue-idle.py', '--queue-ready'): 'ready',
             ('check-agent-queue-idle.py', '--resume'): 'resume_idle',
             ('prepare-gateway.py', '--flow-change-pending'): 'pending',
             ('prepare-gateway.py', '--complete-flow-change'): 'complete',
             ('prepare-gateway.py', '--rule-action-flow'): 'apply_flow',
             ('prepare-gateway.py', '--rule-action-rollback-flow'): 'rollback_flow',
             ('prepare-gateway.py', '--check-rule-action-flow'): 'rule_check',
             ('prepare-gateway.py', '--check-flow'): 'flow_check',
             ('tunnel/check.py', '--scope', 'public'): 'public'}.get(tuple(args), 'unknown')
else:
    phase = next((item for item in args if item in ['config', 'build', 'up', 'exec', 'restart', 'down', 'rm']), 'unknown')
if phase == 'idle':
    count = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(count))
    phase = f'idle{count}'
with open(os.environ['APPLY_TEST_LOG'], 'a') as log:
    log.write(json.dumps({'name': name, 'phase': phase, 'args': args, 'project': os.environ.get('COMPOSE_PROJECT_NAME')}) + '\n')
failures = json.loads(os.environ.get('APPLY_FAILURES', '{}'))
if phase in ('apply_flow', 'rollback_flow'):
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
APPLY = ['config', 'pending', 'build', 'idle1', 'up', 'exec', 'idle2', 'apply_flow', 'up', 'rule_check',
         'public', 'ready', 'complete']
ROLLBACK = ['config', 'pending', 'idle1', 'rollback_flow', 'up', 'flow_check', 'public', 'ready', 'complete']
VERIFY = ['config', 'pending', 'rule_check', 'public', 'ready']


class ApplyAgentRuleActionTests(unittest.TestCase):
    def run_apply(self, failures=None, arguments=(), pending=False):
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
                   'APPLY_TEST_LOG': str(log), 'COMPOSE_PROJECT_NAME': 'rule-test-project',
                   'APPLY_FAILURES': json.dumps(failures or {}), 'APPLY_STATE_DIR': str(state)}
            result = subprocess.run(['bash', str(SCRIPT), *arguments], env=env, input='', capture_output=True,
                                    text=True, timeout=20)
            commands = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            return result, commands, (state / 'marker').exists()

    @staticmethod
    def phases(commands):
        return [item['phase'] for item in commands]

    def assert_scoped(self, commands, services):
        for command in commands:
            self.assertEqual(command['project'], 'rule-test-project')
            args = command['args']
            if command['name'] == 'docker':
                self.assertEqual(args[:4], ['compose', '-p', 'rule-test-project', '-f'])
                self.assertEqual([args[i + 1] for i, value in enumerate(args) if value == '-f'],
                                 ['compose.yaml', 'compose.catalog.yaml', 'compose.langflow.yaml',
                                  'compose.gateway.yaml', 'compose.tunnel.yaml'])
            for forbidden in ('down', 'rm', 'restart', 'stop', 'kill', '--volumes', '--run-once',
                              '--provision', '--init', 'langflow', 'unknown', '--update-flow', '--rollback-flow'):
                self.assertNotIn(forbidden, args)
                self.assertNotEqual(command['phase'], forbidden)
        self.assertEqual([command['args'][-1] for command in commands if command['phase'] in ('up', 'build')], services)
        for command in commands:
            if command['phase'] == 'up':
                self.assertIn('--no-deps', command['args'])
                self.assertIn('--wait', command['args'])
            if command['phase'] == 'exec':
                self.assertEqual(command['args'][-5:], ['-T', 'jeju-mcp', 'node', '--input-type=module', '-'])

    def test_apply_updates_mcp_before_switching_flow_and_clears_marker_last(self):
        result, commands, marker = self.run_apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.phases(commands), APPLY)
        self.assertFalse(marker)
        self.assert_scoped(commands, ['jeju-mcp', 'jeju-mcp', 'agent-gateway'])
        gateway = [command for command in commands if command['phase'] == 'up'][-1]
        self.assertIn('--force-recreate', gateway['args'])
        for expected in ('"scope":"agent-rule-action-applied"', '"model":"gpt-5.4-mini"',
                         '"modelExecuted":false', '"productionReady":false'):
            self.assertIn(expected, result.stdout)

    def test_rollback_restores_the_eight_call_flow_without_touching_mcp(self):
        result, commands, marker = self.run_apply(arguments=('--rollback',))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.phases(commands), ROLLBACK)
        self.assertFalse(marker)
        self.assert_scoped(commands, ['agent-gateway'])
        self.assertIn('"scope":"agent-rule-action-rolled-back"', result.stdout)

    def test_verify_only_never_checks_idle_changes_flow_or_restarts(self):
        result, commands, marker = self.run_apply(arguments=('--verify-only',))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.phases(commands), VERIFY)
        self.assert_scoped(commands, [])
        self.assertIn('"servicesRestarted":false', result.stdout)
        result, commands, marker = self.run_apply(arguments=('--verify-only',), pending=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.phases(commands), ['config', 'pending'])
        self.assertTrue(marker)

    def test_busy_queue_stops_before_any_restart_or_before_the_flow_switch(self):
        result, commands, marker = self.run_apply(failures={'idle1': 75})
        self.assertEqual(result.returncode, 75)
        self.assertEqual(self.phases(commands), ['config', 'pending', 'build', 'idle1'])
        self.assertFalse(marker)
        result, commands, marker = self.run_apply(failures={'idle2': 75})
        self.assertEqual(result.returncode, 75)
        self.assertEqual(self.phases(commands), APPLY[:7])
        self.assertFalse(marker)
        self.assertIn('모두 끝난 뒤', result.stderr)

    def test_failed_offline_check_never_switches_the_flow(self):
        result, commands, marker = self.run_apply(failures={'exec': 1})
        self.assertNotEqual(result.returncode, 0)
        # Only the read-only resume probe of the failure handler follows the failed check.
        self.assertEqual(self.phases(commands)[-2:], ['exec', 'pending'])
        self.assertNotIn('apply_flow', self.phases(commands))
        self.assertIn('phase=offline_rule_check', result.stderr)

    def test_interrupted_switch_reports_resume_and_rerun_resumes_through_internal_checks(self):
        result, commands, marker = self.run_apply(failures={'up': 1, 'idle1': 0})
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('apply_flow', self.phases(commands))
        result, commands, marker = self.run_apply(failures={'rule_check': 1})
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(marker)
        self.assertIn('같은 명령을 다시 실행하면 이어서 마무리합니다', result.stderr)
        result, commands, marker = self.run_apply(pending=True, failures={'idle1': 76, 'idle2': 76})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.phases(commands), ['config', 'pending', 'build', 'idle1', 'resume_idle', 'up', 'exec',
                                                 'idle2', 'resume_idle', 'apply_flow', 'up', 'rule_check',
                                                 'public', 'ready', 'complete'])
        self.assertFalse(marker)

    def test_unknown_arguments_are_rejected_without_commands(self):
        result, commands, _ = self.run_apply(arguments=('--force',))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(commands, [])


if __name__ == '__main__':
    unittest.main()
