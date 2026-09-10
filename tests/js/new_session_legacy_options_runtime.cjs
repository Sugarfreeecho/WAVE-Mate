const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..', '..');
const source = fs.readFileSync(
  path.join(root, 'frontend', 'src', 'app', 'modules', 'session-management.js'),
  'utf8',
);
const start = source.indexOf('async function applyNewSessionOptionsToLegacyBackend');
const end = source.indexOf('async function materializeNewSessionInner', start);
assert(start >= 0 && end > start, 'legacy create-option compatibility helper is missing');

const calls = [];
const ctx = vm.createContext({
  Promise,
  String,
  Error,
  encodeURIComponent,
  fetch: async (url, options) => {
    calls.push({ url, body: JSON.parse(options.body) });
    if (url.endsWith('/permissions')) {
      return { ok: true, json: async () => ({ ok: true, mode: 'approve_for_me' }) };
    }
    return { ok: true, json: async () => ({ ok: true, profile_id: 'profile-fast' }) };
  },
});
vm.runInContext(
  `${source.slice(start, end)}\nglobalThis.__apply = applyNewSessionOptionsToLegacyBackend;`,
  ctx,
);

(async () => {
  const oldResponse = {};
  const options = {
    model_profile_id: 'profile-fast',
    permission_mode: 'approve_for_me',
  };
  await ctx.__apply('created', options, oldResponse);
  assert.deepStrictEqual(calls, [
    {
      url: '/sessions/created/model_profile',
      body: { profile_id: 'profile-fast' },
    },
    {
      url: '/sessions/created/permissions',
      body: { mode: 'approve_for_me' },
    },
  ]);
  assert.strictEqual(oldResponse.permission_status.mode, 'approve_for_me');

  await ctx.__apply('created', options, {
    model_profile_id: 'profile-fast',
    permission_status: { mode: 'approve_for_me' },
  });
  assert.strictEqual(calls.length, 2, 'new backends must not receive duplicate option writes');
  process.stdout.write('new session legacy option checks passed\n');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
