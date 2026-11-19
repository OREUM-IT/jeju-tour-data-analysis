import assert from 'node:assert/strict';
import { loadCatalog } from '../../services/jeju-mcp/src/catalog.mjs';

const catalog = await loadCatalog(process.argv[2]);
assert.equal(catalog.schemaVersion, '2.0');
assert.equal(catalog.dataMode, 'app-export');
assert.equal(catalog.source.repository, 'OREUM-IT/jeju-tour');
assert.equal(catalog.source.commit, 'b0602a175c1d08959b7187da2b29ac450b52e0ba');
assert.equal(catalog.source.rawSha256, '646505a2a65cbf9dbe8d49d526e866bed3d61d7e95907f5e31621e8611bd91c6');
assert.equal(catalog.source.placesContentSha256, 'e3c47e843b708adf1e70b0205c4b367c37214d3c2763ab73316a68619d44156a');
assert.equal(catalog.places.length, 981);
assert.ok(catalog.places.every(p => Object.values(p.attributes).every(a => a.basis !== 'verified')));
console.log('PASS: catalog v2, pinned source, 981 places, verified attributes=0; private local file only.');
