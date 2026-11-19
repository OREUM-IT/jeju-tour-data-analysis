import { createHash } from 'node:crypto';
import { chmodSync, closeSync, lstatSync, mkdirSync, openSync } from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';

export const STATE_POLICY = Object.freeze({
  windowMs: 3_600_000, globalLimit: 60, subjectLimit: 12, maxConcurrent: 2, maxQueue: 8,
  leaseMs: 120_000, uncertainWaitMs: 120_000, queueTtlMs: 600_000,
  retentionMs: 3_780_000, maxRows: 120,
});
const ASCII_TOKEN = /^[\x21-\x7e]{1,128}$/u;
const CODES = Object.freeze({ REPLAY_DETECTED: 409, RATE_LIMITED: 429, BUSY: 429,
  QUEUE_FULL: 429, JOB_EXPIRED: 410, JOB_ALREADY_RUNNING: 409, STATE_UNAVAILABLE: 503 });

export class GatewayStateError extends Error {
  constructor(code = 'STATE_UNAVAILABLE', retryAfterSeconds) {
    super('Gateway request unavailable.');
    this.name = 'GatewayStateError';
    this.code = Object.hasOwn(CODES, code) ? code : 'STATE_UNAVAILABLE';
    this.status = CODES[this.code];
    if (Number.isInteger(retryAfterSeconds)) this.retryAfterSeconds = Math.max(1, Math.min(3600, retryAfterSeconds));
  }
}
const reject = (code, retry) => { throw new GatewayStateError(code, retry); };
const hash = (kind, value) => createHash('sha256').update(`${kind}\n${value}`, 'ascii').digest('hex');
function instant(value) {
  const now = typeof value === 'function' ? value() : value;
  if (!Number.isSafeInteger(now) || now < 0 || now > Number.MAX_SAFE_INTEGER - STATE_POLICY.retentionMs) reject();
  return now;
}
function validId(value) { if (typeof value !== 'string' || !ASCII_TOKEN.test(value)) reject(); }
function bounded(value, min, max) { if (!Number.isInteger(value) || value < min || value > max) reject(); return value; }
const TABLE = `CREATE TABLE requests (
  nonce_hash TEXT PRIMARY KEY NOT NULL, subject_hash TEXT NOT NULL,
  request_id TEXT UNIQUE NOT NULL, reserved_at INTEGER NOT NULL, lease_until INTEGER NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','inflight','finished','uncertain','cancelled','expired','abandoned'))
) STRICT;`;

/** Only hashed reservations and leases persist. Raw jobs and results belong to the memory queue. */
export class GatewayState {
  #db;
  #now;
  #policy;
  constructor(options) {
    try {
      const { filename, now = Date.now, maxConcurrent = 2, globalLimit = 60, subjectLimit = 12, maxQueue = 8 } = options;
      this.#policy = Object.freeze({ ...STATE_POLICY, maxConcurrent: bounded(maxConcurrent, 1, 4),
        globalLimit: bounded(globalLimit, 1, 240), subjectLimit: bounded(subjectLimit, 1, Math.min(globalLimit, 48)),
        maxQueue: bounded(maxQueue, 0, 8), maxRows: globalLimit * 2 });
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
      if (![0, 1, 2].includes(version)) reject();
      if (version !== 2) this.#transaction(() => {
        if (version === 1) {
          this.#db.exec('ALTER TABLE requests RENAME TO requests_v1;');
          this.#db.exec(TABLE);
          this.#db.exec('INSERT INTO requests SELECT * FROM requests_v1; DROP TABLE requests_v1;');
        } else this.#db.exec(TABLE);
        this.#db.exec(`CREATE INDEX requests_subject_time ON requests(subject_hash,reserved_at);
          CREATE INDEX requests_time ON requests(reserved_at); PRAGMA user_version = 2;`);
      });
    } catch {
      try { this.#db?.close(); } catch { /* Fixed outward error. */ }
      this.#db = undefined;
      reject();
    }
  }
  get policy() { return this.#policy; }
  #transaction(operation) {
    if (!this.#db) reject();
    let began = false;
    try {
      this.#db.exec('BEGIN IMMEDIATE'); began = true;
      const result = operation(); this.#db.exec('COMMIT'); return result;
    } catch (error) {
      if (began) try { this.#db.exec('ROLLBACK'); } catch { /* Never proceed without a commit. */ }
      if (error instanceof GatewayStateError) throw error;
      reject();
    }
  }
  #clean(at) {
    this.#db.prepare("UPDATE requests SET status='expired' WHERE status='queued' AND lease_until <= ?").run(at);
    this.#db.prepare('DELETE FROM requests WHERE reserved_at < ? AND lease_until <= ?').run(at - this.#policy.retentionMs, at);
  }
  #active(at) {
    return this.#db.prepare("SELECT subject_hash,lease_until,status FROM requests WHERE status IN ('inflight','uncertain') AND lease_until > ? ORDER BY lease_until").all(at);
  }
  #insert(options, queued) {
    try {
      const { subject, nonce, requestId, now = this.#now } = options;
      validId(subject); validId(requestId);
      if (typeof nonce !== 'string' || !/^[a-f0-9]{32}$/u.test(nonce)) reject();
      const at = instant(now), subjectHash = hash('subject', subject), nonceHash = hash('nonce', nonce);
      return this.#transaction(() => {
        this.#clean(at);
        if (this.#db.prepare('SELECT 1 FROM requests WHERE nonce_hash=? OR request_id=? LIMIT 1').get(nonceHash, requestId)) reject('REPLAY_DETECTED');
        const recent = this.#db.prepare('SELECT subject_hash,reserved_at FROM requests WHERE reserved_at > ? ORDER BY reserved_at').all(at - this.#policy.windowMs);
        const ownerRecent = recent.filter(row => row.subject_hash === subjectHash);
        if (recent.length >= this.#policy.globalLimit || ownerRecent.length >= this.#policy.subjectLimit) {
          const ends = [];
          if (recent.length >= this.#policy.globalLimit) ends.push(recent[recent.length - this.#policy.globalLimit].reserved_at + this.#policy.windowMs);
          if (ownerRecent.length >= this.#policy.subjectLimit) ends.push(ownerRecent[ownerRecent.length - this.#policy.subjectLimit].reserved_at + this.#policy.windowMs);
          reject('RATE_LIMITED', Math.ceil((Math.max(...ends) - at) / 1000));
        }
        const owner = this.#db.prepare("SELECT lease_until FROM requests WHERE subject_hash=? AND status IN ('queued','inflight','uncertain') AND lease_until>? ORDER BY lease_until DESC LIMIT 1").get(subjectHash, at);
        if (owner) reject('BUSY', Math.ceil((owner.lease_until - at) / 1000));
        const active = this.#active(at);
        const waiting = this.#db.prepare("SELECT COUNT(*) AS count FROM requests WHERE status='queued'").get().count;
        if (queued) {
          if (waiting >= this.#policy.maxQueue) reject('QUEUE_FULL', 2);
        } else if (active.length >= this.#policy.maxConcurrent || waiting > 0) {
          reject('BUSY', active.length ? Math.ceil((active[0].lease_until - at) / 1000) : 1);
        }
        if (this.#db.prepare('SELECT COUNT(*) AS count FROM requests').get().count >= this.#policy.maxRows) reject();
        const leaseUntil = at + (queued ? this.#policy.queueTtlMs : this.#policy.leaseMs);
        this.#db.prepare('INSERT INTO requests VALUES(?,?,?,?,?,?)').run(nonceHash, subjectHash, requestId, at, leaseUntil, queued ? 'queued' : 'inflight');
        return { requestId, leaseUntil };
      });
    } catch (error) { if (error instanceof GatewayStateError) throw error; reject(); }
  }
  reserve(options) { return this.#insert(options, false); }
  enqueue(options) { return this.#insert(options, true); }
  claim({ requestId, now = this.#now } = {}) {
    validId(requestId); const at = instant(now);
    return this.#transaction(() => {
      this.#clean(at);
      const row = this.#db.prepare('SELECT status,lease_until FROM requests WHERE request_id=?').get(requestId);
      if (!row) reject();
      if (row.status === 'expired') reject('JOB_EXPIRED');
      if (row.status !== 'queued') reject('JOB_ALREADY_RUNNING');
      const first = this.#db.prepare("SELECT request_id FROM requests WHERE status='queued' ORDER BY rowid LIMIT 1").get();
      const active = this.#active(at);
      if (first.request_id !== requestId || active.length >= this.#policy.maxConcurrent) reject('BUSY', active.length ? Math.ceil((active[0].lease_until - at) / 1000) : 1);
      const leaseUntil = at + this.#policy.leaseMs;
      this.#db.prepare("UPDATE requests SET status='inflight',lease_until=? WHERE request_id=?").run(leaseUntil, requestId);
      return { requestId, leaseUntil };
    });
  }
  cancel({ requestId, now = this.#now } = {}) {
    validId(requestId); const at = instant(now);
    return this.#transaction(() => {
      this.#clean(at);
      const row = this.#db.prepare('SELECT status,lease_until FROM requests WHERE request_id=?').get(requestId);
      if (!row) reject();
      if (row.status === 'expired') reject('JOB_EXPIRED');
      if (row.status !== 'queued' && row.status !== 'cancelled') reject('JOB_ALREADY_RUNNING');
      this.#db.prepare("UPDATE requests SET status='cancelled',lease_until=? WHERE request_id=?").run(at, requestId);
      return { requestId, status: 'cancelled', leaseUntil: at };
    });
  }
  abandonQueued({ now = this.#now } = {}) {
    const at = instant(now);
    return this.#transaction(() => {
      this.#clean(at);
      return this.#db.prepare("UPDATE requests SET status='abandoned',lease_until=? WHERE status='queued'").run(at).changes;
    });
  }
  activeCounts({ now = this.#now } = {}) {
    const at = instant(now);
    return this.#transaction(() => {
      this.#clean(at);
      const active = this.#active(at);
      return { queued: this.#db.prepare("SELECT COUNT(*) AS count FROM requests WHERE status='queued'").get().count,
        running: active.filter(row => row.status === 'inflight').length,
        uncertain: active.filter(row => row.status === 'uncertain').length };
    });
  }
  finish(options) {
    try {
      const { requestId, now = this.#now, uncertain = false } = options;
      validId(requestId); if (typeof uncertain !== 'boolean') reject(); const at = instant(now);
      return this.#transaction(() => {
        const row = this.#db.prepare('SELECT status,lease_until FROM requests WHERE request_id=?').get(requestId);
        if (!row) reject();
        // Terminal idempotence prevents a late callback from releasing an uncertain lease.
        if (row.status !== 'inflight') return { requestId, status: row.status, leaseUntil: row.lease_until };
        const status = uncertain ? 'uncertain' : 'finished';
        const leaseUntil = uncertain ? Math.max(row.lease_until, at + this.#policy.uncertainWaitMs) : at;
        this.#db.prepare('UPDATE requests SET status=?,lease_until=? WHERE request_id=?').run(status, leaseUntil, requestId);
        return { requestId, status, leaseUntil };
      });
    } catch (error) { if (error instanceof GatewayStateError) throw error; reject(); }
  }
  close() {
    try { this.#db?.close(); } catch { reject(); } finally { this.#db = undefined; }
  }
}
export function createGatewayState(options) { return new GatewayState(options); }
