#!/usr/bin/env python3
"""Check Access configuration, private dependencies, or public authentication.

No mode executes a recommendation/model request. Runtime checks never expose
tokens, raw response bodies, container environment dumps, or subprocess errors.
"""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request

import configure

MAX_RESPONSE = 64 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def run_compose(deploy, arguments):
    command = ['docker', 'compose']
    for name in ('compose.yaml', 'compose.catalog.yaml', 'compose.langflow.yaml',
                 'compose.gateway.yaml', 'compose.tunnel.yaml'):
        command.extend(['-f', name])
    # COMPOSE_PROJECT_NAME is intentionally inherited for isolated CI projects.
    result = subprocess.run(command + arguments, cwd=deploy, capture_output=True,
                            check=False, timeout=50)
    configure.require(result.returncode == 0)
    return result.stdout


def gateway_ready(deploy, access):
    # Check the live container against the file before the safe internal readiness
    # probe, so a stale private-mode container cannot pass a file-only check.
    script = ('const wanted=JSON.parse(process.argv[1]); '
              'if(!Object.entries(wanted).every(([k,v])=>process.env[k]===v))process.exit(1);')
    run_compose(deploy, ['exec', '-T', 'agent-gateway', 'node', '-e', script,
                         json.dumps(access, separators=(',', ':'))])
    run_compose(deploy, ['exec', '-T', 'agent-gateway', 'node', 'check.mjs', '--internal'])


def connector_ready(deploy):
    run_compose(deploy, ['exec', '-T', 'cloudflared', 'cloudflared', 'tunnel',
                         '--metrics', '127.0.0.1:2000', 'ready'])


def signed_headers(key):
    configure.require(isinstance(key, str) and 32 <= len(key) <= 4096
                      and all(32 <= ord(char) <= 126 for char in key))
    subject = 'ops-readiness'
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    canonical = '\n'.join(('GET', '/readyz', subject, timestamp, nonce,
                           hashlib.sha256(b'').hexdigest()))
    return {'X-Jeju-Subject': subject, 'X-Jeju-Timestamp': timestamp, 'X-Jeju-Nonce': nonce,
            'X-Jeju-Signature': hmac.new(key.encode('ascii'), canonical.encode('ascii'),
                                       hashlib.sha256).hexdigest()}


def request(hostname, headers):
    configure.require(hostname == configure.PUBLIC_HOST)
    opener = urllib.request.build_opener(NoRedirect())
    # No redirects, alternate URL, or TLS bypass; respect the host proxy policy.
    req = urllib.request.Request(f'https://{hostname}/readyz', headers=headers, method='GET')
    try:
        response = opener.open(req, timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        content = response.read(MAX_RESPONSE + 1)
        configure.require(len(content) <= MAX_RESPONSE)
        return response.status, content


def public_ready(access, service, gateway):
    hostname = access['GATEWAY_PUBLIC_HOST']
    status, _ = request(hostname, {})
    configure.require(status in (301, 302, 303, 307, 308, 401, 403))
    headers = {'CF-Access-Client-Id': service['CF_ACCESS_CLIENT_ID'],
               'CF-Access-Client-Secret': service['CF_ACCESS_CLIENT_SECRET']}
    status, _ = request(hostname, headers)
    configure.require(status == 401)
    status, content = request(hostname, {**headers, **signed_headers(gateway.get('GATEWAY_AUTH_KEY'))})
    configure.require(status == 200)
    value = json.loads(content)
    configure.require(isinstance(value, dict) and value.get('ready') is True
                      and value.get('mode') == 'access' and value.get('flowVerified') is True
                      and value.get('catalogPlaces') == 981 and value.get('productionReady') is False)


def check(deploy, scope):
    configure.require(os.geteuid() == configure.ROOT_UID)
    root = configure.directory(deploy / '.local')
    access = configure.read_access(root)
    gateway = configure.check_gateway_file(root, required=scope != 'config')
    has_token = configure.check_token(root, required=scope in ('connector', 'public'))
    if scope != 'config':
        gateway_ready(deploy, access)
    if scope in ('connector', 'public'):
        connector_ready(deploy)
    if scope == 'public':
        public_ready(access, configure.read_service(root), gateway)
    return {'status': 'passed', 'scope': scope, 'tokenConfigured': has_token,
            'runtimeChecked': scope != 'config', 'modelExecuted': False,
            'productionReady': False}


def main():
    parser = configure.SafeParser(description=__doc__)
    parser.add_argument('--scope', choices=('config', 'gateway', 'connector', 'public'), default='config')
    args = parser.parse_args()
    try:
        print(json.dumps(check(Path(__file__).resolve().parents[1], args.scope), separators=(',', ':')))
    except (OSError, ValueError, UnicodeError, configure.ConfigurationError,
            subprocess.SubprocessError, urllib.error.URLError):
        print('TUNNEL_CHECK_FAILED: configuration, readiness, or authentication did not pass; no response or secret values displayed.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
