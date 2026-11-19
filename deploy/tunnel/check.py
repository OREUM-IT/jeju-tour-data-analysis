#!/usr/bin/env python3
"""Check Access configuration, private dependencies, or public authentication.

No mode executes a recommendation/model request. Runtime checks never expose
tokens, raw response bodies, container environment dumps, or subprocess errors.
"""
import argparse
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request

import configure

MAX_RESPONSE = 64 * 1024
USER_AGENT = 'oreumit-agent-check/1.0'
PUBLIC_PHASES = frozenset(('anonymous', 'access_only', 'signed', 'readiness_response'))
GATEWAY_CODES = frozenset((
    'AUTH_FAILED', 'REPLAY_DETECTED', 'RATE_LIMITED', 'BUSY', 'STATE_UNAVAILABLE',
    'INVALID_RECOMMENDATION_REQUEST', 'BODY_TOO_LARGE', 'INVALID_JSON', 'INVALID_HEADERS',
    'BODY_TIMEOUT', 'METHOD_NOT_ALLOWED', 'NOT_FOUND', 'ORIGIN_REJECTED',
    'UNSUPPORTED_MEDIA_TYPE', 'ACCESS_REQUIRED', 'FLOW_CHANGED', 'MCP_NOT_READY',
    'RECOMMENDATION_BLOCKED', 'FINAL_VALIDATION_REJECTED', 'TOO_MANY_PLACES',
    'SCOPE_CLEANUP_FAILED', 'UPSTREAM_TIMEOUT_NO_RETRY', 'GATEWAY_UNAVAILABLE',
))
GATEWAY_HEADER_CONTEXT = ('originPresent', 'cookiePresent', 'cookieEmpty',
                          'singleCfAuthorizationCookie', 'cookieMatchesAssertion')


def response_json(content):
    """Reject ambiguous/nonstandard JSON without including body fragments in errors."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('RESPONSE_JSON_INVALID')
            result[key] = value
        return result

    def constant(_value):
        raise ValueError('RESPONSE_JSON_INVALID')

    return json.loads(content.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)


def response_summary(content):
    """Return only fixed labels and allowlisted codes; never echo text, headers or JSON values."""
    if not content:
        return {'responseKind': 'empty'}
    try:
        text = content.decode('utf-8')
    except UnicodeError:
        return {'responseKind': 'binary'}
    # A code embedded in HTML, another JSON value or arbitrary text is not this recognizable plain error form.
    cloudflare = re.fullmatch(rb'[ \t\r\n]*error code:[ \t\r\n]*(1[0-9]{3})[ \t\r\n]*', content)
    try:
        value = response_json(content)
    except (ValueError, UnicodeError, RecursionError):
        kind = 'html' if re.match(r'\s*(?:<!doctype\s+html\b|<html\b)', text, re.IGNORECASE) else 'text'
        summary = {'responseKind': kind}
    else:
        summary = {'responseKind': 'json'}
        error = value.get('error') if isinstance(value, dict) else None
        code = error.get('code') if isinstance(error, dict) else None
        if isinstance(code, str) and code in GATEWAY_CODES:
            summary['gatewayCode'] = code
            if code == 'ORIGIN_REJECTED' and isinstance(error.get('headerContext'), dict):
                context = error['headerContext']
                safe_context = {key: context[key] for key in GATEWAY_HEADER_CONTEXT
                                if key in context and type(context[key]) is bool}
                if safe_context:
                    summary['gatewayHeaderContext'] = safe_context
    if cloudflare:
        summary['cloudflareCode'] = int(cloudflare.group(1))
    return summary


class PublicCheckError(configure.ConfigurationError):
    """A failed public stage with a closed, secret-free diagnostic schema."""
    def __init__(self, phase, status=None, content=b'', *, response_kind=None, body_bytes=None):
        super().__init__('TUNNEL_PUBLIC_CHECK_FAILED')
        configure.require(phase in PUBLIC_PHASES)
        configure.require(status is None or type(status) is int and 100 <= status <= 599)
        configure.require(isinstance(content, bytes))
        if response_kind is None:
            summary = response_summary(content)
            count = len(content)
        else:
            configure.require(response_kind in ('oversized', 'unavailable'))
            configure.require(type(body_bytes) is int and 0 <= body_bytes <= MAX_RESPONSE + 1)
            summary = {'responseKind': response_kind}
            count = body_bytes
        self.diagnostic = {'status': 'failed', 'code': 'TUNNEL_PUBLIC_CHECK_FAILED',
                           'phase': phase, 'httpStatus': status, 'bodyBytes': count, **summary,
                           'modelExecuted': False, 'productionReady': False}


class ResponseTooLarge(configure.ConfigurationError):
    """Carry only the HTTP status and observed byte count across the request boundary."""
    def __init__(self, status, body_bytes):
        super().__init__('TUNNEL_RESPONSE_TOO_LARGE')
        self.status = status
        self.body_bytes = body_bytes


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
    # Identify this operator probe honestly; Python's generic urllib agent can trigger Cloudflare error 1010.
    # Preserve every authentication value and prevent caller casing from creating a second User-Agent field.
    identified = {name: value for name, value in headers.items() if name.lower() != 'user-agent'}
    identified['User-Agent'] = USER_AGENT
    req = urllib.request.Request(f'https://{hostname}/readyz', headers=identified, method='GET')
    try:
        response = opener.open(req, timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        content = response.read(MAX_RESPONSE + 1)
        if len(content) > MAX_RESPONSE:
            raise ResponseTooLarge(response.status, len(content))
        return response.status, content


def public_request(phase, hostname, headers):
    try:
        return request(hostname, headers)
    except ResponseTooLarge as error:
        raise PublicCheckError(phase, error.status, response_kind='oversized',
                               body_bytes=error.body_bytes) from None
    except (OSError, ValueError, UnicodeError, configure.ConfigurationError,
            urllib.error.URLError, http.client.HTTPException):
        raise PublicCheckError(phase, response_kind='unavailable', body_bytes=0) from None


def public_ready(access, service, gateway):
    hostname = access['GATEWAY_PUBLIC_HOST']
    status, content = public_request('anonymous', hostname, {})
    if status not in (301, 302, 303, 307, 308, 401, 403):
        raise PublicCheckError('anonymous', status, content)
    headers = {'CF-Access-Client-Id': service['CF_ACCESS_CLIENT_ID'],
               'CF-Access-Client-Secret': service['CF_ACCESS_CLIENT_SECRET']}
    status, content = public_request('access_only', hostname, headers)
    if status != 401:
        raise PublicCheckError('access_only', status, content)
    status, content = public_request('signed', hostname, {**headers, **signed_headers(gateway.get('GATEWAY_AUTH_KEY'))})
    if status != 200:
        raise PublicCheckError('signed', status, content)
    try:
        value = response_json(content)
    except (ValueError, UnicodeError, RecursionError):
        raise PublicCheckError('readiness_response', status, content) from None
    if not (isinstance(value, dict) and value.get('ready') is True
            and value.get('mode') == 'access' and value.get('flowVerified') is True
            and value.get('catalogPlaces') == 981 and value.get('productionReady') is False):
        raise PublicCheckError('readiness_response', status, content)


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
    except PublicCheckError as error:
        print(json.dumps(error.diagnostic, separators=(',', ':')), file=sys.stderr)
        return 1
    except (OSError, ValueError, UnicodeError, configure.ConfigurationError,
            subprocess.SubprocessError, urllib.error.URLError):
        print('TUNNEL_CHECK_FAILED: configuration, readiness, or authentication did not pass; no response or secret values displayed.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
