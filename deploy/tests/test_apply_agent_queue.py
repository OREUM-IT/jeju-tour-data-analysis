"""Deployment ordering with fake commands; no Docker, model calls or real secrets."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'apply-agent-queue.sh'
MOCK = r'''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
name = Path(sys.argv[0]).name
source_verified = None
violation = False
if name == 'python3':
    phase = 'idle' if args == ['check-agent-queue-idle.py'] else 'ready' if args == ['check-agent-queue-idle.py', '--queue-ready'] else 'public'
else:
    phase = next((item for item in args if item in ['config', 'build', 'up', 'exec']), 'unknown')
    if phase == 'exec':
        source = sys.stdin.buffer.read()
        root = Path(os.environ['APPLY_SOURCE_ROOT'])
        if 'agent-gateway' in args:
            matches = [kind for kind in ('state', 'jobs') if source == (root / 'services' / 'agent-gateway' / (kind + '.test.mjs')).read_bytes()]
            phase = 'queue_' + matches[0] if len(matches) == 1 else 'invalid_queue_source'
            source_verified = len(matches) == 1
            # Production deliberately excludes *.test.mjs from the image. Each
            # host test must arrive on stdin, resolving ./state.mjs from /app.
            violation = (not source_verified or '--workdir' not in args
                         or args[args.index('--workdir') + 1] != '/app'
                         or args[-3:] != ['node', '--input-type=module', '-']
                         or any(argument.endswith('.test.mjs') for argument in args))
        else:
            phase = 'candidate'
            source_verified = source == (root / 'deploy' / 'tests' / 'check-candidate-recovery.mjs').read_bytes()
            violation = 'jeju-mcp' not in args or not source_verified
with open(os.environ['APPLY_TEST_LOG'], 'a') as log:
    log.write(json.dumps({'name': name, 'phase': phase, 'args': args, 'project': os.environ.get('COMPOSE_PROJECT_NAME'), 'sourceVerified': source_verified}) + '\n')
if violation:
    print('Invalid offline test transport', file=sys.stderr)
    raise SystemExit(97)
if phase == os.environ.get('APPLY_FAIL_PHASE'):
    raise SystemExit(int(os.environ.get('APPLY_EXIT_CODE', '1')))
print(json.dumps({'status': 'passed', 'scope': phase, 'modelExecuted': False}))
'''


class ApplyAgentQueueTests(unittest.TestCase):
    def run_apply(self, failure=None, code=1, arguments=()):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('docker', 'python3'):
                executable = root / name
                executable.write_text(f'#!{sys.executable}\n' + MOCK, encoding='utf-8')
                executable.chmod(0o700)
            log = root / 'commands.jsonl'
            env = {**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH'],
                   'APPLY_TEST_LOG': str(log), 'COMPOSE_PROJECT_NAME': 'queue-test-project',
                   'APPLY_SOURCE_ROOT': str(SCRIPT.parents[1]),
                   'APPLY_EXIT_CODE': str(code)}
            if failure:
                env['APPLY_FAIL_PHASE'] = failure
            else:
                env.pop('APPLY_FAIL_PHASE', None)
            result = subprocess.run(['bash', str(SCRIPT), *arguments], env=env, input='', capture_output=True, text=True, timeout=10)
            commands = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            return result, commands

    def test_both_builds_precede_idle_check_and_only_mcp_gateway_are_recreated(self):
        result, commands = self.run_apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([item['phase'] for item in commands], ['config', 'build', 'idle', 'up', 'candidate', 'queue_state', 'queue_jobs', 'public', 'ready'])
        for command in commands:
            self.assertEqual(command['project'], 'queue-test-project')
            args = command['args']
            if command['name'] != 'docker':
                self.assertIn(args, [['check-agent-queue-idle.py'], ['tunnel/check.py', '--scope', 'public'], ['check-agent-queue-idle.py', '--queue-ready']])
                continue
            self.assertEqual(args[:4], ['compose', '-p', 'queue-test-project', '-f'])
            self.assertEqual([args[i + 1] for i, value in enumerate(args) if value == '-f'],
                             ['compose.yaml', 'compose.catalog.yaml', 'compose.langflow.yaml',
                              'compose.gateway.yaml', 'compose.tunnel.yaml'])
            if command['phase'] in ('build', 'up'):
                self.assertEqual(args[-2:], ['jeju-mcp', 'agent-gateway'])
            if command['phase'] == 'up':
                self.assertIn('--no-deps', args)
                self.assertIn('--wait', args)
                self.assertNotIn('--force-recreate', args)
            if command['phase'] in ('queue_state', 'queue_jobs'):
                self.assertEqual(args[args.index('--workdir') + 1], '/app')
                self.assertEqual(args[-3:], ['node', '--input-type=module', '-'])
                self.assertFalse(any(argument.endswith('.test.mjs') for argument in args))
            if command['phase'] in ('candidate', 'queue_state', 'queue_jobs'):
                self.assertTrue(command['sourceVerified'])
            for forbidden in ('down', 'rm', '--volumes', '--run-once', 'stop', 'kill'):
                self.assertNotIn(forbidden, args)
        self.assertIn('"scope":"agent-queue-applied"', result.stdout)
        self.assertIn('"modelExecuted":false', result.stdout)

    def test_verify_only_checks_both_host_test_streams_without_build_idle_or_recreation(self):
        result, commands = self.run_apply(arguments=('--verify-only',))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([item['phase'] for item in commands], ['config', 'candidate', 'queue_state', 'queue_jobs', 'public', 'ready'])
        streamed = [item for item in commands if item['sourceVerified'] is not None]
        self.assertEqual(len(streamed), 3)
        self.assertTrue(all(item['sourceVerified'] for item in streamed))
        for command in commands:
            self.assertNotIn(command['phase'], ('build', 'idle', 'up'))
            self.assertNotIn('stop', command['args'])
            self.assertNotIn('kill', command['args'])
        self.assertIn('"scope":"agent-queue-verified"', result.stdout)
        self.assertIn('"servicesRestarted":false', result.stdout)
        self.assertNotIn('"scope":"agent-queue-applied"', result.stdout)

    def test_unknown_arguments_fail_before_any_external_command(self):
        for arguments in (('--unknown',), ('--verify-only', '--unknown')):
            with self.subTest(arguments=arguments):
                result, commands = self.run_apply(arguments=arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(commands, [])

    def test_busy_or_uncertain_lease_never_recreates_and_requires_manual_retry(self):
        result, commands = self.run_apply('idle', 75)
        self.assertEqual(result.returncode, 75)
        self.assertEqual([item['phase'] for item in commands], ['config', 'build', 'idle'])
        self.assertIn('불확실 실행 보호 시간', result.stderr)
        self.assertIn('직접 다시 실행', result.stderr)
        self.assertNotIn('agent-queue-applied', result.stdout)

    def test_any_failure_stops_before_later_phases_and_never_reports_success(self):
        for arguments, phases in (
            ((), ['config', 'build', 'idle', 'up', 'candidate', 'queue_state', 'queue_jobs', 'public', 'ready']),
            (('--verify-only',), ['config', 'candidate', 'queue_state', 'queue_jobs', 'public', 'ready']),
        ):
            for failure in phases:
                with self.subTest(arguments=arguments, failure=failure):
                    result, commands = self.run_apply(failure, arguments=arguments)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual([item['phase'] for item in commands], phases[:phases.index(failure) + 1])
                    self.assertNotIn('agent-queue-applied', result.stdout)
                    self.assertNotIn('agent-queue-verified', result.stdout)


if __name__ == '__main__':
    unittest.main()
