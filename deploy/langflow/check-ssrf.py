"""Exercise the installed guards without contacting private addresses or metadata."""
from unittest.mock import patch

from lfx.utils.ssrf_protection import (
    SSRFProtectionError, get_allowed_hosts, is_ssrf_protection_enabled,
    validate_and_resolve_url, validate_url_for_ssrf,
)
from lfx.utils.ssrf_httpx import validate_strict_url_for_ssrf_or_raise

assert is_ssrf_protection_enabled() and get_allowed_hosts() == ['jeju-mcp']
guards = (validate_url_for_ssrf, validate_and_resolve_url, validate_strict_url_for_ssrf_or_raise)
blocked = ('127.0.0.1', '169.254.169.254', '10.0.0.1', '172.16.0.1', '192.168.0.1', '[::1]')
with patch('lfx.utils.ssrf_protection.resolve_hostname', side_effect=AssertionError('Unexpected DNS')):
    for guard in guards:
        guard('http://jeju-mcp:8788/mcp')
        for host in blocked:
            try:
                guard(f'http://{host}/')
            except SSRFProtectionError:
                continue
            raise AssertionError('An unlisted private destination was allowed')
print('PASS: installed SSRF guards allow only the configured MCP exception and reject six private-address probes; no DNS/HTTP request sent.')
