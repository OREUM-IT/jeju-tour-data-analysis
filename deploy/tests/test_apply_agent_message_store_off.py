"""Message-storage switch ordering with fake commands; no Docker, model calls or real secrets."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'apply-agent-message-store-off.sh'
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
             ('prepare-gateway.py', '--message-store-off-flow'): 'off_flow',
             ('prepare-gateway.py', '--message-store-on-flow'): 'on_flow',
             ('prepare-gateway.py', '--check-message-store-off-flow'): 'off_check',
             ('prepare-gateway.py', '--check-message-store-on-flow'): 'on_check',
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
if phase in ('off_flow', 'on_flow'):
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
APPLY = ['config', 'pending', 'idle1', 'off_flow', 'up', 'off_check', 'public', 'ready', 'complete']
ROLLBACK = ['config', 'pending', 'idle1', 'on_flow', 'up', 'on_check', 'public', 'ready', 'complete']
VERIFY = ['config', 'pending', 'off_check', 'public', 'ready']


class ApplyAgentMessageStoreOffTests(unittest.TestCase):
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
                   'APPLY_TEST_LOG': str(log), 'COMPOSE_PROJECT_NAME': 'store-test-project',
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
            self.assertEqual(command['project'], 'store-test-project')
            args = command['args']
            if command['name'] == 'docker':
                self.assertEqual(args[:4], ['compose', '-p', 'store-test-project', '-f'])
                self.assertEqual([args[i + 1] for i, value in enumerate(args) if value == '-f'],
                                 ['compose.yaml', 'compose.catalog.yaml', 'compose.langflow.yaml',
                                  'compose.gateway.yaml', 'compose.tunnel.yaml'])
            # Only the Gateway is recreated: no build, MCP, Langflow, DB or Tunnel change and no other flow switch.
            for forbidden in ('down', 'rm', 'restart', 'stop', 'kill', 'build', 'exec', '--volumes', '--run-once',
                              '--provision', '--init', 'langflow', 'langflow-db', 'jeju-mcp', 'cloudflared', 'unknown',
                              '--update-flow', '--rollback-flow', '--rule-action-flow', '--rule-action-rollback-flow'):
                self.assertNotIn(forbidden, args)
                self.assertNotEqual(command['phase'], forbidden)
        self.assertEqual([command['args'][-1] for command in commands if command['phase'] == 'up'], services)
        for command in commands:
            if command['phase'] == 'up':
                for flag in ('--no-deps', '--force-recreate', '--wait'):
                    self.assertIn(flag, command['args'])

    def test_apply_switches_storage_then_repins_the_gateway_and_clears_the_marker_last(self):
        result, commands, marker = self.run_apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.phases(commands), APPLY)
        self.assertFalse(marker)
        self.assert_scoped(commands, ['agent-gateway'])
        final = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(final, {'status': 'passed', 'scope': 'agent-message-store-off-applied', 'messageStore': False,
                                 'nativeTracing': 'unchanged', 'services': ['agent-gateway'],
                                 'modelExecuted': False, 'productionReady': False})

    def test_rollback_stores_messages_again_for_the_same_build(self):
        result, commands, marker = self.run_apply(arguments=('--rollback',))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.phases(commands), ROLLBACK)
        self.assertFalse(marker)
        self.assert_scoped(commands, ['agent-gateway'])
        final = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual((final['scope'], final['messageStore']), ('agent-message-store-off-rolled-back', True))

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

    def test_busy_or_unknown_queue_state_stops_before_the_flow_switch(self):
        result, commands, marker = self.run_apply(failures={'idle1': 75})
        self.assertEqual(result.returncode, 75)
        self.assertEqual(self.phases(commands), ['config', 'pending', 'idle1'])
        self.assertFalse(marker)
        self.assertIn('모두 끝난 뒤', result.stderr)
        result, commands, marker = self.run_apply(failures={'idle1': 1})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.phases(commands), ['config', 'pending', 'idle1'])
        self.assertIn('흐름과 서비스를 바꾸지 않았습니다', result.stderr)
        result, commands, marker = self.run_apply(failures={'idle1': 76, 'resume_idle': 75})
        self.assertEqual(result.returncode, 75)
        self.assertEqual(self.phases(commands), ['config', 'pending', 'idle1', 'resume_idle'])
        self.assertFalse(marker)

    def test_failed_switch_or_check_reports_resume_and_rerun_finishes_through_internal_checks(self):
        result, commands, marker = self.run_apply(failures={'off_flow': 1})
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('up', self.phases(commands))
        self.assertIn('phase=flow_change', result.stderr)
        result, commands, marker = self.run_apply(failures={'off_check': 1})
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(marker)
        self.assertIn('같은 명령을 다시 실행하면 이어서 마무리합니다', result.stderr)
        self.assertNotIn('complete', self.phases(commands))
        result, commands, marker = self.run_apply(pending=True, failures={'idle1': 76})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.phases(commands), ['config', 'pending', 'idle1', 'resume_idle', 'off_flow', 'up',
                                                 'off_check', 'public', 'ready', 'complete'])
        self.assertFalse(marker)
        # A pending marker alone (public readiness unavailable for another reason) also resumes internally.
        result, commands, marker = self.run_apply(pending=True, failures={'idle1': 1})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('resume_idle', self.phases(commands))
        self.assertFalse(marker)

    def test_unknown_arguments_are_rejected_without_commands(self):
        for arguments in (('--force',), ('--rollback', '--verify-only'), ('--days', '30')):
            result, commands, _ = self.run_apply(arguments=arguments)
            self.assertEqual(result.returncode, 2, arguments)
            self.assertEqual(commands, [])


if __name__ == '__main__':
    unittest.main()
