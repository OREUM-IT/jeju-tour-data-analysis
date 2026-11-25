// Gateway maintenance mode: while the flag file exists, new model runs are refused with
// 503 MAINTENANCE and Retry-After; polls of accepted jobs and cancellations keep working,
// and accepted/queued jobs run to completion. The flag lives next to the state database
// in the gateway_state volume, so it survives a container recreation until an operator or
// a deploy helper clears it. A separate file, not a SQLite table, keeps older Gateway
// images able to open the same state database after a rollback.
//
// CLI (inside the Gateway image, e.g. `docker compose run --rm --no-deps --entrypoint node
// agent-gateway /app/maintenance.mjs on`): on [--retry-after N] | off | status. Prints one
// JSON line and never reads or prints any credential.
import { closeSync, fsyncSync, lstatSync, openSync, readFileSync, renameSync, rmSync, writeSync } from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const NAME = 'maintenance.json';
const MAX_BYTES = 512;
export const MAINTENANCE_DEFAULT_RETRY = 600;

export const maintenancePath = (statePath = process.env.GATEWAY_STATE_PATH || '/app/state/gateway.sqlite') => {
  if (typeof statePath !== 'string' || !path.isAbsolute(statePath)) throw new Error('MAINTENANCE_PATH_INVALID');
  return path.join(path.dirname(statePath), NAME);
};

/** Current flag or null. A flag that cannot be read safely counts as ON: fail closed for new runs. */
export function readMaintenance(file) {
  let stat;
  try { stat = lstatSync(file); }
  catch (error) { if (error.code === 'ENOENT') return null; return { since: 0, retryAfterSeconds: MAINTENANCE_DEFAULT_RETRY, damaged: true }; }
  try {
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > MAX_BYTES) throw new Error();
    const value = JSON.parse(readFileSync(file, 'utf8'));
    if (!value || typeof value !== 'object' || !Number.isSafeInteger(value.since) || value.since < 0
      || !Number.isInteger(value.retryAfterSeconds) || value.retryAfterSeconds < 60 || value.retryAfterSeconds > 3600) throw new Error();
    return { since: value.since, retryAfterSeconds: value.retryAfterSeconds };
  } catch {
    return { since: 0, retryAfterSeconds: MAINTENANCE_DEFAULT_RETRY, damaged: true };
  }
}

/** Atomic write (temporary file + rename) with mode 0600; an existing flag keeps its start time. */
export function setMaintenance(file, { retryAfterSeconds = MAINTENANCE_DEFAULT_RETRY, now = Date.now() } = {}) {
  if (!Number.isInteger(retryAfterSeconds) || retryAfterSeconds < 60 || retryAfterSeconds > 3600) throw new Error('MAINTENANCE_RETRY_INVALID');
  const current = readMaintenance(file);
  const value = { since: current && !current.damaged ? current.since : now, retryAfterSeconds };
  const temporary = `${file}.${process.pid}.tmp`;
  const fd = openSync(temporary, 'wx', 0o600);
  try { writeSync(fd, JSON.stringify(value)); fsyncSync(fd); } finally { closeSync(fd); }
  renameSync(temporary, file);
  return value;
}

export function clearMaintenance(file) { rmSync(file, { force: true }); }

function main(argv) {
  const [command, ...rest] = argv;
  const file = maintenancePath();
  if (command === 'on') {
    const index = rest.indexOf('--retry-after');
    const retry = index >= 0 ? Number(rest[index + 1]) : MAINTENANCE_DEFAULT_RETRY;
    if ((index >= 0 && rest.length !== 2) || (index < 0 && rest.length)) throw new Error('MAINTENANCE_USAGE');
    const value = setMaintenance(file, { retryAfterSeconds: retry });
    return { status: 'passed', maintenance: true, ...value };
  }
  if (command === 'off' && !rest.length) { clearMaintenance(file); return { status: 'passed', maintenance: false }; }
  if (command === 'status' && !rest.length) {
    const value = readMaintenance(file);
    return { status: 'passed', maintenance: !!value, ...(value || {}) };
  }
  throw new Error('MAINTENANCE_USAGE');
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try { console.log(JSON.stringify(main(process.argv.slice(2)))); }
  catch (error) {
    const code = /^MAINTENANCE_[A-Z_]+$/.test(error?.message) ? error.message : 'MAINTENANCE_FAILED';
    console.log(JSON.stringify({ status: 'failed', code }));
    process.exitCode = code === 'MAINTENANCE_USAGE' ? 2 : 1;
  }
}
