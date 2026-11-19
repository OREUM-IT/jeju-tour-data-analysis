"""Print bounded failure logs with this job's disposable credentials removed."""
import os
from pathlib import Path
import subprocess

if os.environ.get('CI') != 'true':
    raise SystemExit('Diagnostics are restricted to the disposable CI environment.')
root = Path(__file__).resolve().parents[1]
secrets = set()
for name in ('mcp.env', 'db.env', 'langflow.env', 'langflow-db.env'):
    file = root / '.local' / name
    if file.exists():
        for line in file.read_text().splitlines():
            key, separator, value = line.partition('=')
            if separator and any(word in key for word in ('PASSWORD', 'TOKEN', 'SECRET', 'DATABASE_URL')):
                secrets.add(value)
command = ['docker', 'compose', '-f', 'compose.yaml', '-f', 'compose.catalog.yaml', '-f', 'compose.langflow.yaml',
           'logs', '--no-color', '--tail', '100', 'langflow', 'langflow-db']
result = subprocess.run(command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
output = result.stdout
for secret in sorted(secrets, key=len, reverse=True):
    if secret:
        output = output.replace(secret, '[REDACTED]')
print(output[-60000:])
