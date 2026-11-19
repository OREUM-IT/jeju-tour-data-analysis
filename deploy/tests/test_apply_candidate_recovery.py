"""Exercise deployment ordering with fake commands, never Docker or a live service."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'apply-candidate-recovery.sh'
MOCK = r'''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
name = Path(sys.argv[0]).name
phase = 'public' if name == 'python3' else next((item for item in args if item in ['config', 'build', 'up', 'exec']), 'unknown')
if phase == 'exec':
    source = sys.stdin.read()
    phase = 'idle' if 'CANDIDATE_RECOVERY_IDLE_CHECK' in source else 'smoke'
with open(os.environ['APPLY_TEST_LOG'], 'a') as log:
    log.write(json.dumps({'name': name, 'phase': phase, 'args': args, 'project': os.environ.get('COMPOSE_PROJECT_NAME')}) + '\n')
if phase == os.environ.get('APPLY_FAIL_PHASE'):
    raise SystemExit(75 if phase == 'idle' else 1)
print(json.dumps({'status': 'passed', 'scope': phase, 'modelExecuted': False}))
'''


class ApplyCandidateRecoveryTests(unittest.TestCase):
    def run_apply(self, failure=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('docker', 'python3'):
                executable = root / name
                executable.write_text(f'#!{sys.executable}\n' + MOCK, encoding='utf-8')
                executable.chmod(0o700)
            log = root / 'commands.jsonl'
            env = {**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH'],
                   'APPLY_TEST_LOG': str(log), 'COMPOSE_PROJECT_NAME': 'isolated-test-project'}
            if failure:
                env['APPLY_FAIL_PHASE'] = failure
            else:
                env.pop('APPLY_FAIL_PHASE', None)
            result = subprocess.run(['bash', str(SCRIPT)], env=env, capture_output=True, text=True, timeout=10)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            return result, commands

    def test_only_mcp_recreated_after_idle_check_and_all_checks_are_required(self):
        result, commands = self.run_apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([item['phase'] for item in commands], ['config', 'build', 'idle', 'up', 'smoke', 'public'])
        for command in commands:
            self.assertEqual(command['project'], 'isolated-test-project')
            if command['name'] != 'docker':
                self.assertEqual(command['args'], ['tunnel/check.py', '--scope', 'public'])
                continue
            args = command['args']
            self.assertEqual(args[:4], ['compose', '-p', 'isolated-test-project', '-f'])
            self.assertEqual([args[i + 1] for i, value in enumerate(args) if value == '-f'],
                             ['compose.yaml', 'compose.catalog.yaml', 'compose.langflow.yaml',
                              'compose.gateway.yaml', 'compose.tunnel.yaml'])
            if command['phase'] in ('build', 'up'):
                self.assertEqual(args[-1], 'jeju-mcp')
            if command['phase'] == 'up':
                self.assertIn('--no-deps', args)
                self.assertIn('--wait', args)
                self.assertNotIn('--force-recreate', args)
        self.assertIn('"scope":"candidate-recovery-applied"', result.stdout)
        self.assertIn('"modelExecuted":false', result.stdout)

    def test_active_request_stops_before_recreation_and_instructs_manual_retry(self):
        result, commands = self.run_apply('idle')
        self.assertEqual(result.returncode, 75)
        self.assertEqual([item['phase'] for item in commands], ['config', 'build', 'idle'])
        self.assertIn('완료된 뒤 이 명령을 직접 다시 실행', result.stderr)
        self.assertNotIn('candidate-recovery-applied', result.stdout)

    def test_failure_never_claims_success_or_runs_later_phases(self):
        phases = ['config', 'build', 'idle', 'up', 'smoke', 'public']
        for failure in ('build', 'up', 'smoke', 'public'):
            with self.subTest(failure=failure):
                result, commands = self.run_apply(failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual([item['phase'] for item in commands], phases[:phases.index(failure) + 1])
                self.assertNotIn('candidate-recovery-applied', result.stdout)
                self.assertIn('CANDIDATE_RECOVERY_APPLY_FAILED', result.stderr)


if __name__ == '__main__':
    unittest.main()
