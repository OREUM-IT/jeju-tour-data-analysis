#!/usr/bin/env python3
"""Read authenticated job/scope counts before an update. Never submit/cancel work."""
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / 'tunnel'))
import check as tunnel_check
import configure


LEGACY_STATE_CHECK = r'''
// AGENT_QUEUE_LEGACY_IDLE_CHECK: read-only v1 inspection, never create/migrate state.
import { existsSync, lstatSync } from 'node:fs';
import { isAbsolute } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
let database;
try {
  if (existsSync('/app/jobs.mjs')) throw new Error();
  const filename = process.env.GATEWAY_STATE_PATH;
  if (typeof filename !== 'string' || !isAbsolute(filename)) throw new Error();
  const stat = lstatSync(filename);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1) throw new Error();
  database = new DatabaseSync(filename, { readOnly: true, enableDoubleQuotedStringLiterals: false });
  if (database.prepare('PRAGMA user_version').get().user_version !== 1) throw new Error();
  const count = status => database.prepare('SELECT COUNT(*) AS count FROM requests WHERE status = ? AND lease_until > ?').get(status, Date.now()).count;
  console.log(JSON.stringify({ queued: 0, running: count('inflight'), uncertain: count('uncertain') }));
} catch {
  console.error('LEGACY_QUEUE_STATE_UNAVAILABLE');
  process.exitCode = 1;
} finally { database?.close(); }
'''

MCP_IDLE_CHECK = r'''
// AGENT_QUEUE_MCP_IDLE_CHECK: inspect live scopes without registering one.
try {
  const token = process.env.GATEWAY_CONTROL_TOKEN;
  if (typeof token !== 'string' || !/^[\x21-\x7e]{32,256}$/.test(token)) throw new Error();
  const response = await fetch('http://127.0.0.1:8788/gateway/readyz', {
    headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(5000),
  });
  const ready = await response.json();
  if (!response.ok || ready.ready !== true || ready.dataMode !== 'app-export'
    || ready.catalogSchemaVersion !== '2.0' || ready.placeCount !== 981
    || ready.readOnly !== true || ready.productionReady !== false
    || !Number.isSafeInteger(ready.activeScopes) || ready.activeScopes < 0) throw new Error();
  console.log(JSON.stringify({ activeScopes: ready.activeScopes,
    ...(ready.maxConcurrentScopes === undefined ? {} : { maxConcurrentScopes: ready.maxConcurrentScopes }) }));
} catch {
  console.error('MCP_SCOPE_STATE_UNAVAILABLE');
  process.exitCode = 1;
}
'''


class ActiveRequests(Exception):
    pass


def count(value):
    configure.require(type(value) is int and 0 <= value <= 128)
    return value


def job_counts(value):
    configure.require(isinstance(value, dict))
    return {name: count(value.get(name)) for name in ('queued', 'running', 'uncertain')}


def container_json(deploy, service, source):
    command = ['docker', 'compose', '-p', os.environ.get('COMPOSE_PROJECT_NAME', 'oreumit-bootstrap')]
    for name in ('compose.yaml', 'compose.catalog.yaml', 'compose.langflow.yaml',
                 'compose.gateway.yaml', 'compose.tunnel.yaml'):
        command.extend(['-f', name])
    completed = subprocess.run(command + ['exec', '-T', service, 'node', '--input-type=module', '-'],
                               cwd=deploy, input=source.encode('utf-8'), capture_output=True,
                               check=False, timeout=15)
    configure.require(completed.returncode == 0 and len(completed.stdout) <= 1024)
    return tunnel_check.response_json(completed.stdout)


def check_idle(deploy, *, queue_ready=False):
    configure.require(os.geteuid() == configure.ROOT_UID)
    root = configure.directory(deploy / '.local')
    access = configure.read_access(root)
    gateway = configure.check_gateway_file(root, required=True)
    service = configure.read_service(root)
    status, content = tunnel_check.public_request('signed', access['GATEWAY_PUBLIC_HOST'], {
        'CF-Access-Client-Id': service['CF_ACCESS_CLIENT_ID'],
        'CF-Access-Client-Secret': service['CF_ACCESS_CLIENT_SECRET'],
        **tunnel_check.signed_headers(gateway.get('GATEWAY_AUTH_KEY')),
    })
    configure.require(status == 200)
    ready = tunnel_check.response_json(content)
    configure.require(isinstance(ready, dict) and ready.get('ready') is True
                      and ready.get('mode') == 'access' and ready.get('flowVerified') is True
                      and ready.get('catalogPlaces') == 981 and ready.get('productionReady') is False)
    legacy = 'jobs' not in ready
    if legacy:
        configure.require(not queue_ready and 'asyncJobs' not in ready and 'maxConcurrent' not in ready)
    else:
        configure.require(ready.get('asyncJobs') is True and type(ready.get('maxConcurrent')) is int
                          and 1 <= ready['maxConcurrent'] <= 4)
    # Only the old image with schema v1 may omit jobs; a damaged new response fails closed.
    jobs = job_counts(container_json(deploy, 'agent-gateway', LEGACY_STATE_CHECK) if legacy else ready['jobs'])
    if not queue_ready and any(jobs.values()):
        raise ActiveRequests()
    scopes = container_json(deploy, 'jeju-mcp', MCP_IDLE_CHECK)
    configure.require(isinstance(scopes, dict))
    active = count(scopes.get('activeScopes'))
    if not legacy:
        configure.require(type(scopes.get('maxConcurrentScopes')) is int
                          and scopes['maxConcurrentScopes'] == ready['maxConcurrent'])
    if not queue_ready and active:
        raise ActiveRequests()
    return {'status': 'passed', 'scope': 'queue-runtime-ready' if queue_ready else 'idle-before-queue-update', 'gatewayJobs': jobs,
            'activeScopes': active, 'legacyGateway': legacy,
            **({'maxConcurrent': ready['maxConcurrent']} if not legacy else {}),
            'modelExecuted': False, 'productionReady': False}


def main():
    parser = configure.SafeParser(description=__doc__)
    parser.add_argument('--queue-ready', action='store_true', help='Require the new queue API and matching live MCP capacity, allowing active jobs.')
    args = parser.parse_args()
    try:
        print(json.dumps(check_idle(Path(__file__).resolve().parent, queue_ready=args.queue_ready), separators=(',', ':')))
        return 0
    except ActiveRequests:
        print('{"status":"deferred","code":"ACTIVE_REQUEST_PRESENT","restarted":false,"modelExecuted":false,"productionReady":false}', file=sys.stderr)
        return 75
    except Exception:
        print('AGENT_QUEUE_IDLE_CHECK_FAILED: no service was restarted; no response or secret values displayed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
