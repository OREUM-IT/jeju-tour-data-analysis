"""Overtourism deployment ordering with fake commands; no Docker, model calls or real secrets."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'apply-overtourism-agent.sh'
MOCK = r'''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
name = Path(sys.argv[0]).name
counter = Path(os.environ['APPLY_STATE_DIR']) / 'idle-count'
if name == 'python3':
    phase = {('check-agent-queue-idle.py',): 'idle', ('check-agent-queue-idle.py', '--queue-ready'): 'ready',
             ('prepare-overtourism.py', '--provision'): 'provision', ('prepare-overtourism.py', '--replace'): 'replace',
             ('prepare-overtourism.py', '--check'): 'flow_check', ('prepare-overtourism.py', '--disable'): 'disable',
             ('tunnel/check.py', '--scope', 'public'): 'public'}.get(tuple(args), 'unknown')
else:
    phase = next((item for item in args if item in ['config', 'build', 'up', 'exec', 'restart', 'down', 'rm']), 'unknown')
    if phase == 'exec':
        phase = 'route_check' if 'agent-gateway' in args else 'offline_check' if 'jeju-mcp' in args else 'exec'
if phase == 'idle':
    count = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(count))
    phase = f'idle{count}'
with open(os.environ['APPLY_TEST_LOG'], 'a') as log:
    log.write(json.dumps({'name': name, 'phase': phase, 'args': args, 'stdin': sys.stdin.read() if phase == 'offline_check' else '',
                          'project': os.environ.get('COMPOSE_PROJECT_NAME')}) + '\n')
failures = json.loads(os.environ.get('APPLY_FAILURES', '{}'))
if phase in failures:
    raise SystemExit(failures[phase])
print(json.dumps({'status': 'passed', 'scope': phase, 'modelExecuted': False}))
'''
APPLY = ['config', 'build', 'idle1', 'up', 'offline_check', 'provision', 'idle2', 'up', 'flow_check', 'route_check',
         'public', 'ready']
VERIFY = ['config', 'flow_check', 'route_check', 'public', 'ready']
REPLACE = ['config', 'idle1', 'replace', 'idle2', 'up', 'flow_check', 'route_check', 'public', 'ready']
DISABLE = ['config', 'idle1', 'disable', 'up', 'public', 'ready']


class ApplyOvertourismAgentTests(unittest.TestCase):
    def run_apply(self, failures=None, arguments=()):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('docker', 'python3'):
                executable = root / name
                executable.write_text(f'#!{sys.executable}\n' + MOCK, encoding='utf-8')
                executable.chmod(0o700)
            state = root / 'state'
            state.mkdir()
            log = root / 'commands.jsonl'
            env = {**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH'], 'APPLY_TEST_LOG': str(log),
                   'COMPOSE_PROJECT_NAME': 'overtourism-test-project', 'APPLY_FAILURES': json.dumps(failures or {}),
                   'APPLY_STATE_DIR': str(state)}
            result = subprocess.run(['bash', str(SCRIPT), *arguments], env=env, input='', capture_output=True,
                                    text=True, timeout=20)
            commands = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            return result, commands

    @staticmethod
    def phases(commands):
        return [item['phase'] for item in commands]

    def test_apply_builds_checks_offline_pins_then_recreates_only_mcp_and_gateway(self):
        result, commands = self.run_apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.phases(commands), APPLY)
        self.assertEqual(json.loads(result.stdout.splitlines()[-1])['scope'], 'overtourism-agent-applied')
        docker = [item['args'] for item in commands if item['name'] == 'docker']
        files = ['-f', 'compose.yaml', '-f', 'compose.catalog.yaml', '-f', 'compose.langflow.yaml',
                 '-f', 'compose.gateway.yaml', '-f', 'compose.tunnel.yaml']
        self.assertTrue(all(args[:4] == ['compose', '-p', 'overtourism-test-project', *files][:4] and args[3:13] == files for args in docker))
        self.assertEqual(docker[1][13:], ['build', 'jeju-mcp', 'agent-gateway'])
        self.assertEqual(docker[2][13:], ['up', '-d', '--no-deps', '--wait', '--wait-timeout', '90', 'jeju-mcp'])
        self.assertEqual(docker[-2][13:], ['up', '-d', '--no-deps', '--force-recreate', '--wait', '--wait-timeout', '120', 'agent-gateway'])
        self.assertEqual(docker[-1][13:], ['exec', '-T', 'agent-gateway', 'node', '/app/check.mjs', '--internal', '--overtourism'])
        offline = next(item for item in commands if item['phase'] == 'offline_check')
        self.assertEqual(offline['stdin'], (SCRIPT.parent / 'tests/check-overtourism.mjs').read_text())
        # Langflow, the database, the tunnel and the recommendation flow are never touched.
        self.assertFalse(any(service in args for args in docker for service in ('langflow', 'langflow-db', 'cloudflared', 'postgres')))
        self.assertFalse(any('prepare-gateway.py' in item['args'] for item in commands))

    def test_busy_queue_stops_before_any_service_change(self):
        result, commands = self.run_apply({'idle1': 75})
        self.assertEqual(result.returncode, 75)
        self.assertEqual(self.phases(commands), ['config', 'build', 'idle1'])
        self.assertIn('다시 실행해 주세요', result.stderr)
        result, commands = self.run_apply({'idle2': 75})
        self.assertEqual(result.returncode, 75)
        self.assertEqual(self.phases(commands), APPLY[:7])
        # --replace-flow checks before the stored flow changes, not only before the Gateway.
        result, commands = self.run_apply({'idle1': 75}, arguments=('--replace-flow',))
        self.assertEqual(result.returncode, 75)
        self.assertEqual(self.phases(commands), ['config', 'idle1'])
        result, commands = self.run_apply({'idle2': 75}, arguments=('--replace-flow',))
        self.assertEqual(result.returncode, 75)
        self.assertEqual(self.phases(commands), REPLACE[:4])

    def test_failed_phase_reports_only_the_phase(self):
        for phase in ('offline_check', 'provision', 'route_check', 'public'):
            with self.subTest(phase=phase):
                result, commands = self.run_apply({phase: 1})
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.phases(commands)[-1], phase)
                self.assertIn(f'OVERTOURISM_AGENT_APPLY_FAILED: phase=', result.stderr)
                self.assertIn('modelExecuted=false', result.stderr)

    def test_verify_replace_and_disable_modes(self):
        for arguments, expected, scope in ((('--verify-only',), VERIFY, 'overtourism-agent-verified'),
                                           (('--replace-flow',), REPLACE, 'overtourism-flow-replaced'),
                                           (('--disable',), DISABLE, 'overtourism-agent-disabled')):
            with self.subTest(arguments=arguments):
                result, commands = self.run_apply(arguments=arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.phases(commands), expected)
                self.assertEqual(json.loads(result.stdout.splitlines()[-1])['scope'], scope)
        result, commands = self.run_apply(arguments=('--unknown',))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(commands, [])


if __name__ == '__main__':
    unittest.main()
