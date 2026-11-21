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

    def test_mcp_only_resume_gate_reads_live_scopes_without_the_flow_verifying_gateway(self):
        def probe(outputs):
            with ExitStack() as stack:
                stack.enter_context(patch.object(idle.os, 'geteuid', return_value=0))
                stack.enter_context(patch.object(idle.configure, 'directory', return_value=Path('/unused/.local')))
                public = stack.enter_context(patch.object(idle.tunnel_check, 'public_request',
                                                          side_effect=AssertionError('Gateway readiness is not used')))
                containers = stack.enter_context(patch.object(idle, 'container_json', side_effect=outputs))
                result = idle.check_mcp_idle(Path('/unused'))
                public.assert_not_called()
                self.assertEqual([call.args[1:] for call in containers.call_args_list], [('jeju-mcp', idle.MCP_IDLE_CHECK)])
                return result
        self.assertEqual(probe([SCOPES]), {'status': 'passed', 'scope': 'mcp-idle-before-gateway-repin',
                                           'activeScopes': 0, 'gatewayJobsChecked': False,
                                           'modelExecuted': False, 'productionReady': False})
        with self.assertRaises(idle.ActiveRequests):
            probe([{**SCOPES, 'activeScopes': 1}])
        for invalid in ([], {'activeScopes': -1}, {'activeScopes': True}, {}):
            with self.subTest(invalid=invalid), self.assertRaises(idle.configure.ConfigurationError):
                probe([invalid])
        with patch.object(idle.os, 'geteuid', return_value=1000), self.assertRaises(idle.configure.ConfigurationError):
            idle.check_mcp_idle(Path('/unused'))

    def test_mcp_only_cli_exit_codes_and_mutually_exclusive_modes(self):
        for side_effect, code in ((None, 0), (idle.ActiveRequests(), 75), (RuntimeError('secret-do-not-display'), 1)):
            output, errors = io.StringIO(), io.StringIO()
            result = {'status': 'passed', 'scope': 'mcp-idle-before-gateway-repin'}
            with self.subTest(code=code), patch.object(idle.sys, 'argv', ['check-agent-queue-idle.py', '--mcp-only']), \
                 patch.object(idle, 'check_mcp_idle', side_effect=side_effect, return_value=result) as mcp_only, \
                 patch.object(idle, 'check_idle', side_effect=AssertionError('full gate must not run')), \
                 patch('sys.stdout', output), redirect_stderr(errors):
                self.assertEqual(idle.main(), code)
                mcp_only.assert_called_once()
                self.assertNotIn('secret-do-not-display', output.getvalue() + errors.getvalue())
        with patch.object(idle.sys, 'argv', ['check-agent-queue-idle.py', '--mcp-only', '--queue-ready']), \
             redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            idle.main()
        self.assertEqual(raised.exception.code, 2)

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
