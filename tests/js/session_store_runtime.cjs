const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..', '..');
const storeSource = fs.readFileSync(
  path.join(root, 'frontend', 'src', 'app', 'state', 'session-store.js'),
  'utf8',
);
const actionsSource = fs.readFileSync(
  path.join(root, 'frontend', 'src', 'app', 'state', 'session-actions.js'),
  'utf8',
);

const ctx = vm.createContext({
  console,
  Date,
  Map,
  Set,
  Object,
  Number,
  Array,
  persistSessionUnread() {},
  applyServerStreamActiveMap() {},
});
vm.runInContext(`${storeSource}\nglobalThis.__sessionStore = sessionStore;`, ctx);
vm.runInContext(`${actionsSource}\nglobalThis.__applySessionSnapshot = applySessionSnapshot;`, ctx);

const store = ctx.__sessionStore;
const applySnapshot = ctx.__applySessionSnapshot;

store.applySnapshot([{ id: 'old', name: 'Old' }], 0);
store.protectFromSnapshots({
  id: 'new',
  name: 'New',
  created_at: '2026-09-09T10:00:00Z',
});
store.applySnapshot([{ id: 'old', name: 'Old' }], 0);
assert(store.get('new'), 'an older full snapshot must not erase a just-created session');
assert(store.snapshotProtectedSessions.has('new'));

store.applySnapshot([
  { id: 'new', name: 'Server New', created_at: '2026-09-09T10:00:00Z' },
  { id: 'old', name: 'Old' },
], 0);
assert.strictEqual(store.get('new').name, 'Server New');
assert.strictEqual(store.snapshotProtectedSessions.has('new'), false);

store.protectFromSnapshots({ id: 'deleted-new', name: 'Deleted' });
store.markDeletedSession('deleted-new');
store.applySnapshot([{ id: 'old', name: 'Old' }], 0);
assert.strictEqual(store.get('deleted-new'), null, 'delete tombstones must win over protection');

assert.strictEqual(applySnapshot({
  client_request_seq: 2,
  sessions: [{ id: 'latest', name: 'Latest' }],
}), true);
assert.strictEqual(applySnapshot({
  client_request_seq: 1,
  sessions: [{ id: 'stale', name: 'Stale' }],
}), false);
assert(store.get('latest'), 'a late response from an older request must be ignored');
assert.strictEqual(store.get('stale'), null);

process.stdout.write('session store runtime checks passed\n');
