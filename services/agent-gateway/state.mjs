import { createHash } from 'node:crypto';
import { chmodSync, closeSync, lstatSync, mkdirSync, openSync } from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';

export const STATE_POLICY = Object.freeze({
  windowMs: 3_600_000, globalLimit: 30, subjectLimit: 6, leaseMs: 120_000,
  uncertainWaitMs: 120_000, retentionMs: 3_780_000, maxRows: 60,
});
const ASCII_TOKEN = /^[\x21-\x7e]{1,128}$/u;
const CODES = Object.freeze({ REPLAY_DETECTED: 409, RATE_LIMITED: 429, BUSY: 429, STATE_UNAVAILABLE: 503 });

export class GatewayStateError extends Error {
  constructor(code = 'STATE_UNAVAILABLE') {
    super('Gateway request unavailable.');
    this.name = 'GatewayStateError';
    this.code = Object.hasOwn(CODES, code) ? code : 'STATE_UNAVAILABLE';
    this.status = CODES[this.code];
  }
}

const reject = (code) => { throw new GatewayStateError(code); };
const hash = (kind, value) => createHash('sha256').update(`${kind}\n${value}`, 'ascii').digest('hex');
function instant(value) {
  const now = typeof value === 'function' ? value() : value;
  if (!Number.isSafeInteger(now) || now < 0 || now > Number.MAX_SAFE_INTEGER - STATE_POLICY.retentionMs) reject();
  return now;
}
function validId(value) {
  if (typeof value !== 'string' || !ASCII_TOKEN.test(value)) reject();
}

/**
 * Persistent, synchronous reservations. A transaction commits before the caller can make an upstream request.
 * No response, prompt, credential or unhashed subject/nonce is stored. A crash leaves its original 120-second
 * lease in the database; an uncertain remote outcome additionally holds the slot for 120 seconds after finish.
 * This bound requires the upstream's own execution deadline to be shorter than the lease (gateway: 90 seconds).
 */
export class GatewayState {
  #db;
  #now;

  constructor(options) {
    try {
      const { filename, now = Date.now } = options;
      this.#now = now;
      if (typeof filename !== 'string' || !path.isAbsolute(filename) || filename.length > 4096) reject();
      instant(now);
      mkdirSync(path.dirname(filename), { recursive: true, mode: 0o700 });
      try { const fd = openSync(filename, 'wx', 0o600); closeSync(fd); }
      catch (error) { if (error.code !== 'EEXIST') throw error; }
      const stat = lstatSync(filename);
      if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1) reject();
      chmodSync(filename, 0o600);
      this.#db = new DatabaseSync(filename, { enableDoubleQuotedStringLiterals: false });
      this.#db.exec('PRAGMA journal_mode = WAL; PRAGMA synchronous = FULL; PRAGMA busy_timeout = 0;');
      const version = this.#db.prepare('PRAGMA user_version').get().user_version;
      if (version !== 0 && version !== 1) reject();
      this.#db.exec(`
        CREATE TABLE IF NOT EXISTS requests (
          nonce_hash TEXT PRIMARY KEY NOT NULL,
          subject_hash TEXT NOT NULL,
          request_id TEXT UNIQUE NOT NULL,
          reserved_at INTEGER NOT NULL,
          lease_until INTEGER NOT NULL,
          status TEXT NOT NULL CHECK (status IN ('inflight', 'finished', 'uncertain'))
        ) STRICT;
        CREATE INDEX IF NOT EXISTS requests_subject_time ON requests(subject_hash, reserved_at);
        CREATE INDEX IF NOT EXISTS requests_time ON requests(reserved_at);
        PRAGMA user_version = 1;
      `);
    } catch {
      try { this.#db?.close(); } catch { /* Preserve the fixed outward error. */ }
      this.#db = undefined;
      reject();
    }
  }

  #transaction(operation) {
    if (!this.#db) reject();
    let began = false;
    try {
      this.#db.exec('BEGIN IMMEDIATE');
      began = true;
      const result = operation();
      this.#db.exec('COMMIT');
      return result;
    } catch (error) {
      if (began) try { this.#db.exec('ROLLBACK'); } catch { /* No request proceeds without a successful commit. */ }
      if (error instanceof GatewayStateError) throw error;
      reject();
    }
  }

  reserve(options) {
    try {
      const { subject, nonce, requestId, now = this.#now } = options;
      validId(subject);
      validId(requestId);
      if (typeof nonce !== 'string' || !/^[a-f0-9]{32}$/u.test(nonce)) reject();
      const at = instant(now);
      const subjectHash = hash('subject', subject);
      const nonceHash = hash('nonce', nonce);
      return this.#transaction(() => {
        // Retention is longer than both the one-hour quota window and the entire signed timestamp acceptance
        // window. At most two hourly budgets (60 rows) can exist, so new opaque subjects cannot grow this table.
        this.#db.prepare('DELETE FROM requests WHERE reserved_at < ? AND lease_until <= ?')
          .run(at - STATE_POLICY.retentionMs, at);
        if (this.#db.prepare('SELECT 1 FROM requests WHERE nonce_hash = ? OR request_id = ? LIMIT 1').get(nonceHash, requestId)) reject('REPLAY_DETECTED');
        const cutoff = at - STATE_POLICY.windowMs;
        const globalCount = this.#db.prepare('SELECT COUNT(*) AS count FROM requests WHERE reserved_at > ?').get(cutoff).count;
        const subjectCount = this.#db.prepare('SELECT COUNT(*) AS count FROM requests WHERE subject_hash = ? AND reserved_at > ?').get(subjectHash, cutoff).count;
        if (globalCount >= STATE_POLICY.globalLimit || subjectCount >= STATE_POLICY.subjectLimit) reject('RATE_LIMITED');
        if (this.#db.prepare("SELECT 1 FROM requests WHERE status IN ('inflight', 'uncertain') AND lease_until > ? LIMIT 1").get(at)) reject('BUSY');
        if (this.#db.prepare('SELECT COUNT(*) AS count FROM requests').get().count >= STATE_POLICY.maxRows) reject('STATE_UNAVAILABLE');
        const leaseUntil = at + STATE_POLICY.leaseMs;
        this.#db.prepare('INSERT INTO requests (nonce_hash, subject_hash, request_id, reserved_at, lease_until, status) VALUES (?, ?, ?, ?, ?, ?)')
          .run(nonceHash, subjectHash, requestId, at, leaseUntil, 'inflight');
        return { requestId, leaseUntil };
      });
    } catch (error) {
      if (error instanceof GatewayStateError) throw error;
      reject();
    }
  }

  finish(options) {
    try {
      const { requestId, now = this.#now, uncertain = false } = options;
      validId(requestId);
      if (typeof uncertain !== 'boolean') reject();
      const at = instant(now);
      return this.#transaction(() => {
        const row = this.#db.prepare('SELECT status, lease_until FROM requests WHERE request_id = ?').get(requestId);
        if (!row) reject();
        // Terminal state is idempotent. A later callback cannot release a previously uncertain remote execution.
        if (row.status !== 'inflight') return { requestId, status: row.status, leaseUntil: row.lease_until };
        const status = uncertain ? 'uncertain' : 'finished';
        const leaseUntil = uncertain ? Math.max(row.lease_until, at + STATE_POLICY.uncertainWaitMs) : at;
        this.#db.prepare('UPDATE requests SET status = ?, lease_until = ? WHERE request_id = ?').run(status, leaseUntil, requestId);
        return { requestId, status, leaseUntil };
      });
    } catch (error) {
      if (error instanceof GatewayStateError) throw error;
      reject();
    }
  }

  close() {
    try { this.#db?.close(); }
    catch { reject(); }
    finally { this.#db = undefined; }
  }
}

export function createGatewayState(options) {
  return new GatewayState(options);
}
