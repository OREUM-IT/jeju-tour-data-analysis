"""Read-only upgrade gate tests, including old SQLite leases and fixed diagnostics."""
from contextlib import ExitStack, redirect_stderr
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / 'check-agent-queue-idle.py'
spec = importlib.util.spec_from_file_location('agent_queue_idle', MODULE_PATH)
idle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(idle)
READY = {'ready': True, 'mode': 'access', 'flowVerified': True,
         'catalogPlaces': 981, 'productionReady': False,
         'asyncJobs': True, 'maxConcurrent': 2,
         'jobs': {'queued': 0, 'running': 0, 'uncertain': 0}}
SCOPES = {'activeScopes': 0, 'maxConcurrentScopes': 2}


class QueueIdleTests(unittest.TestCase):
    def probe(self, ready, outputs, *, queue_ready=False):
        with ExitStack() as stack:
            stack.enter_context(patch.object(idle.os, 'geteuid', return_value=0))
            stack.enter_context(patch.object(idle.configure, 'directory', return_value=Path('/unused/.local')))
            stack.enter_context(patch.object(idle.configure, 'read_access', return_value={'GATEWAY_PUBLIC_HOST': 'agent.oreumit.com'}))
            stack.enter_context(patch.object(idle.configure, 'check_gateway_file', return_value={'GATEWAY_AUTH_KEY': 'a' * 64}))
            stack.enter_context(patch.object(idle.configure, 'read_service', return_value={'CF_ACCESS_CLIENT_ID': 'test-id', 'CF_ACCESS_CLIENT_SECRET': 'secret-do-not-display'}))
            public = stack.enter_context(patch.object(idle.tunnel_check, 'public_request', return_value=(200, json.dumps(ready).encode())))
            containers = stack.enter_context(patch.object(idle, 'container_json', side_effect=outputs))
            result = idle.check_idle(Path('/unused'), queue_ready=queue_ready)
            return result, public.call_args, containers.call_args_list

    def test_new_gateway_reads_authenticated_counts_and_zero_mcp_scopes(self):
        result, request, containers = self.probe(READY, [SCOPES])
        self.assertEqual(result['gatewayJobs'], {'queued': 0, 'running': 0, 'uncertain': 0})
        self.assertFalse(result['legacyGateway'])
        self.assertFalse(result['modelExecuted'])
        self.assertEqual(request.args[:2], ('signed', 'agent.oreumit.com'))
        self.assertIn('X-Jeju-Signature', request.args[2])
        self.assertEqual(len(containers), 1)
        self.assertEqual(containers[0].args[1:], ('jeju-mcp', idle.MCP_IDLE_CHECK))
        self.assertNotIn('secret-do-not-display', json.dumps(result))

    def test_queued_running_uncertain_or_active_scope_blocks_update(self):
        for name in ('queued', 'running', 'uncertain'):
            with self.subTest(name=name), self.assertRaises(idle.ActiveRequests):
                self.probe({**READY, 'jobs': {**READY['jobs'], name: 1}}, [])
        with self.assertRaises(idle.ActiveRequests):
            self.probe(READY, [{**SCOPES, 'activeScopes': 1}])

    def test_invalid_jobs_never_fall_back_to_old_state(self):
        for jobs in (None, {}, [], {'queued': False, 'running': 0, 'uncertain': 0},
                     {'queued': 0, 'running': -1, 'uncertain': 0}, {'queued': 0, 'running': 0, 'uncertain': '0'}):
            with self.subTest(jobs=jobs), self.assertRaises(idle.configure.ConfigurationError):
                self.probe({**READY, 'jobs': jobs}, [])
        with self.assertRaises(idle.configure.ConfigurationError):
            self.probe({**READY, 'productionReady': True}, [])

    def test_legacy_requires_readonly_state_proof_before_mcp_check(self):
        ready = {name: value for name, value in READY.items() if name not in ('jobs', 'asyncJobs', 'maxConcurrent')}
        result, _, containers = self.probe(ready, [{'queued': 0, 'running': 0, 'uncertain': 0}, {'activeScopes': 0}])
        self.assertTrue(result['legacyGateway'])
        self.assertEqual([call.args[1] for call in containers], ['agent-gateway', 'jeju-mcp'])
        with self.assertRaises(idle.ActiveRequests):
            self.probe(ready, [{'queued': 0, 'running': 0, 'uncertain': 1}])

    def test_fixed_failure_output_does_not_include_exception_or_secret(self):
        errors = io.StringIO()
        with patch.object(idle.sys, 'argv', ['check-agent-queue-idle.py']), patch.object(idle, 'check_idle', side_effect=RuntimeError('secret-do-not-display')), redirect_stderr(errors):
            self.assertEqual(idle.main(), 1)
        self.assertIn('AGENT_QUEUE_IDLE_CHECK_FAILED', errors.getvalue())
        self.assertNotIn('secret-do-not-display', errors.getvalue())

    def resume(self, running, outputs):
        with ExitStack() as stack:
            stack.enter_context(patch.object(idle.os, 'geteuid', return_value=0))
            stack.enter_context(patch.object(idle.configure, 'directory', return_value=Path('/unused/.local')))
            public = stack.enter_context(patch.object(idle.tunnel_check, 'public_request',
                                                      side_effect=AssertionError('Gateway readiness is not used')))
            stack.enter_context(patch.object(idle, 'gateway_running', return_value=running))
            containers = stack.enter_context(patch.object(idle, 'container_json', side_effect=outputs))
            try:
                return idle.check_resume_idle(Path('/unused')), [call.args[1:] for call in containers.call_args_list]
            finally:
                public.assert_not_called()

    def test_resume_gate_reads_gateway_sqlite_counts_and_mcp_scopes_without_public_readiness(self):
        idle_jobs = {'queued': 0, 'running': 0, 'uncertain': 0}
        result, calls = self.resume(True, [idle_jobs, SCOPES])
        self.assertEqual(result, {'status': 'passed', 'scope': 'idle-before-gateway-repin', 'gatewayRunning': True,
                                  'gatewayJobs': idle_jobs, 'activeScopes': 0,
                                  'modelExecuted': False, 'productionReady': False})
        self.assertEqual(calls, [('agent-gateway', idle.GATEWAY_STATE_CHECK), ('jeju-mcp', idle.MCP_IDLE_CHECK)])
        # Waiting, running or uncertain work in the Gateway itself defers the resume.
        for name in ('queued', 'running', 'uncertain'):
            with self.subTest(name=name), self.assertRaises(idle.ActiveRequests):
                self.resume(True, [{**idle_jobs, name: 1}, SCOPES])
        with self.assertRaises(idle.ActiveRequests):
            self.resume(True, [idle_jobs, {**SCOPES, 'activeScopes': 1}])
        # A stopped Gateway has no worker; only live MCP scopes are left to check.
        result, calls = self.resume(False, [SCOPES])
        self.assertEqual((result['gatewayRunning'], result['gatewayJobs']), (False, None))
        self.assertEqual(calls, [('jeju-mcp', idle.MCP_IDLE_CHECK)])
        with self.assertRaises(idle.ActiveRequests):
            self.resume(False, [{**SCOPES, 'activeScopes': 2}])
        for invalid in ([[], SCOPES], [{'queued': -1, 'running': 0, 'uncertain': 0}, SCOPES],
                        [{'queued': True, 'running': 0, 'uncertain': 0}, SCOPES], [idle_jobs, {}]):
            with self.subTest(invalid=invalid), self.assertRaises(idle.configure.ConfigurationError):
                self.resume(True, invalid)
        with patch.object(idle.os, 'geteuid', return_value=1000), self.assertRaises(idle.configure.ConfigurationError):
            idle.check_resume_idle(Path('/unused'))

    def test_gateway_running_reads_only_running_compose_services(self):
        def run_with(stdout, code=0):
            completed = subprocess.CompletedProcess([], code, stdout, b'')
            with patch.object(idle.subprocess, 'run', return_value=completed) as run:
                return idle.gateway_running(Path('/unused')), run.call_args
        running, call = run_with(b'jeju-mcp\nagent-gateway\nlangflow\n')
        self.assertTrue(running)
        self.assertEqual(call.args[0][-4:], ['ps', '--status', 'running', '--services'])
        self.assertFalse(run_with(b'jeju-mcp\nlangflow\n')[0])
        self.assertFalse(run_with(b'')[0])
        for stdout, code in ((b'agent-gateway\n', 1), (b'agent gateway;rm\n', 0), (b'x' * 2048, 0)):
            with self.subTest(stdout=stdout[:20], code=code), self.assertRaises(idle.configure.ConfigurationError):
                run_with(stdout, code)

    def test_signed_flow_changed_readiness_is_reported_separately_with_exit_76(self):
        changed = (503, b'{"error":{"code":"FLOW_CHANGED"}}')
        with ExitStack() as stack:
            stack.enter_context(patch.object(idle.os, 'geteuid', return_value=0))
            stack.enter_context(patch.object(idle.configure, 'directory', return_value=Path('/unused/.local')))
            stack.enter_context(patch.object(idle.configure, 'read_access', return_value={'GATEWAY_PUBLIC_HOST': 'agent.oreumit.com'}))
            stack.enter_context(patch.object(idle.configure, 'check_gateway_file', return_value={'GATEWAY_AUTH_KEY': 'a' * 64}))
            stack.enter_context(patch.object(idle.configure, 'read_service', return_value={'CF_ACCESS_CLIENT_ID': 'test-id', 'CF_ACCESS_CLIENT_SECRET': 'secret-do-not-display'}))
            public = stack.enter_context(patch.object(idle.tunnel_check, 'public_request', return_value=changed))
            containers = stack.enter_context(patch.object(idle, 'container_json', side_effect=AssertionError('no container read')))
            for queue_ready in (False, True):
                with self.subTest(queue_ready=queue_ready), self.assertRaises(idle.FlowChanged):
                    idle.check_idle(Path('/unused'), queue_ready=queue_ready)
            for other in ((503, b'{"error":{"code":"MCP_NOT_READY"}}'), (502, b'{"error":{"code":"FLOW_CHANGED"}}'),
                          (503, b'<html>edge</html>'), (503, b'{"error":{"code":"FLOW_CHANGED","x":1}}')):
                public.return_value = other
                with self.subTest(other=other), self.assertRaises(idle.configure.ConfigurationError):
                    idle.check_idle(Path('/unused'))
            containers.assert_not_called()
        errors = io.StringIO()
        with patch.object(idle.sys, 'argv', ['check-agent-queue-idle.py']), \
             patch.object(idle, 'check_idle', side_effect=idle.FlowChanged()), redirect_stderr(errors):
            self.assertEqual(idle.main(), 76)
        self.assertEqual(json.loads(errors.getvalue())['code'], 'GATEWAY_FLOW_CHANGED')

    def test_resume_cli_exit_codes_and_mutually_exclusive_modes(self):
        for side_effect, code in ((None, 0), (idle.ActiveRequests(), 75), (RuntimeError('secret-do-not-display'), 1)):
            output, errors = io.StringIO(), io.StringIO()
            result = {'status': 'passed', 'scope': 'idle-before-gateway-repin'}
            with self.subTest(code=code), patch.object(idle.sys, 'argv', ['check-agent-queue-idle.py', '--resume']), \
                 patch.object(idle, 'check_resume_idle', side_effect=side_effect, return_value=result) as resume, \
                 patch.object(idle, 'check_idle', side_effect=AssertionError('public gate must not run')), \
                 patch('sys.stdout', output), redirect_stderr(errors):
                self.assertEqual(idle.main(), code)
                resume.assert_called_once()
                self.assertNotIn('secret-do-not-display', output.getvalue() + errors.getvalue())
        with patch.object(idle.sys, 'argv', ['check-agent-queue-idle.py', '--resume', '--queue-ready']), \
             redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            idle.main()
        self.assertEqual(raised.exception.code, 2)

    @unittest.skipUnless(shutil.which('node'), 'Node is unavailable')
    def test_gateway_state_probe_matches_real_active_counts_without_writing(self):
        state_module = Path(__file__).resolve().parents[2] / 'services' / 'agent-gateway' / 'state.mjs'
        seed = f"""
import {{ createGatewayState }} from {json.dumps(state_module.as_uri())};
const [filename, scenario] = process.argv.slice(2);
const now = Date.now(), nonce = n => n.toString(16).padStart(32, '0');
const state = createGatewayState({{ filename, maxConcurrent: 2, now: () => now }});
const request = (n, subject) => ({{ subject, nonce: nonce(n), requestId: `request-${{n}}`, now }});
if (scenario === 'busy') {{
  state.reserve(request(1, 'subject-a'));
  state.reserve(request(2, 'subject-b'));
  state.finish({{ requestId: 'request-2', uncertain: true, now }});
  state.enqueue(request(3, 'subject-c'));
}} else {{
  state.reserve(request(1, 'subject-a'));
  state.finish({{ requestId: 'request-1', now }});
}}
console.log(JSON.stringify(state.activeCounts({{ now }})));
state.close();
"""
        guard = "  if (!existsSync('/app/jobs.mjs')) throw new Error();\n"
        self.assertIn(guard, idle.GATEWAY_STATE_CHECK)
        # Outside the Gateway image the probe refuses to read any state.
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(['node', '--input-type=module', '-'], input=idle.GATEWAY_STATE_CHECK,
                                    env={**os.environ, 'GATEWAY_STATE_PATH': str(Path(directory) / 'state.sqlite')},
                                    capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
        probe = idle.GATEWAY_STATE_CHECK.replace(guard, '')
        for scenario, expected in (('busy', {'queued': 1, 'running': 1, 'uncertain': 1}),
                                   ('settled', {'queued': 0, 'running': 0, 'uncertain': 0})):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                private = Path(directory) / 'private'
                filename = private / 'gateway.sqlite'
                made = subprocess.run(['node', '--input-type=module', '-', str(filename), scenario], input=seed,
                                      capture_output=True, text=True, timeout=20)
                self.assertEqual(made.returncode, 0, made.stderr)
                self.assertEqual(json.loads(made.stdout), expected)
                before = filename.read_bytes()
                result = subprocess.run(['node', '--input-type=module', '-'], input=probe,
                                        env={**os.environ, 'GATEWAY_STATE_PATH': str(filename)},
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), expected)
                self.assertEqual(filename.read_bytes(), before)
        with tempfile.TemporaryDirectory() as directory:
            database = sqlite3.connect(Path(directory) / 'old.sqlite')
            database.executescript('PRAGMA user_version=1; CREATE TABLE requests(status TEXT, lease_until INTEGER);')
            database.close()
            result = subprocess.run(['node', '--input-type=module', '-'], input=probe,
                                    env={**os.environ, 'GATEWAY_STATE_PATH': str(Path(directory) / 'old.sqlite')},
                                    capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)

    def test_post_update_requires_new_queue_and_matching_capacity_but_does_not_cancel_new_work(self):
        result, _, _ = self.probe({**READY, 'jobs': {'queued': 1, 'running': 1, 'uncertain': 0}},
                                  [{**SCOPES, 'activeScopes': 1}], queue_ready=True)
        self.assertEqual(result['scope'], 'queue-runtime-ready')
        self.assertEqual(result['maxConcurrent'], 2)
        with self.assertRaises(idle.configure.ConfigurationError):
            self.probe(READY, [{'activeScopes': 0, 'maxConcurrentScopes': 1}], queue_ready=True)
        with self.assertRaises(idle.configure.ConfigurationError):
            self.probe({name: value for name, value in READY.items() if name not in ('jobs', 'asyncJobs', 'maxConcurrent')}, [], queue_ready=True)
        with self.assertRaises(idle.configure.ConfigurationError):
            self.probe({name: value for name, value in READY.items() if name != 'jobs'}, [])

    @unittest.skipUnless(shutil.which('node'), 'Node is unavailable')
    def test_legacy_sqlite_probe_preserves_database_and_counts_only_unexpired_leases(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / 'state.sqlite'
            database = sqlite3.connect(filename)
            now = int(time.time() * 1000)
            database.executescript('PRAGMA user_version=1; CREATE TABLE requests(status TEXT, lease_until INTEGER);')
            database.executemany('INSERT INTO requests VALUES (?, ?)', [('inflight', now + 120000), ('uncertain', now + 120000), ('finished', now + 120000), ('inflight', now - 1000)])
            database.commit()
            database.close()
            before = filename.read_bytes()
            result = subprocess.run(['node', '--input-type=module', '-'], input=idle.LEGACY_STATE_CHECK,
                                    env={**os.environ, 'GATEWAY_STATE_PATH': str(filename)}, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {'queued': 0, 'running': 1, 'uncertain': 1})
            self.assertEqual(filename.read_bytes(), before)
            missing = Path(directory) / 'missing.sqlite'
            result = subprocess.run(['node', '--input-type=module', '-'], input=idle.LEGACY_STATE_CHECK,
                                    env={**os.environ, 'GATEWAY_STATE_PATH': str(missing)}, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(missing.exists())


if __name__ == '__main__':
    unittest.main()
