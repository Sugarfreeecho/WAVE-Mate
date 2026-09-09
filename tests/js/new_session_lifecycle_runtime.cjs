const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..', '..');
const source = fs.readFileSync(
  path.join(root, 'frontend', 'src', 'app', 'modules', 'session-management.js'),
  'utf8',
);
const start = source.indexOf('async function createNewSession()');
assert(start >= 0, 'new-session lifecycle functions are missing');
const lifecycleSource = source.slice(start);

let fetchCalls = 0;
let releaseCreate;
let restoredRealSession = false;
const createGate = new Promise((resolve) => { releaseCreate = resolve; });
const stream = { querySelector: () => null };
const messageInput = { value: '', focus() {} };
const ctx = vm.createContext({
  console,
  Promise,
  String,
  CustomEvent: function CustomEvent(name, init) { this.name = name; this.detail = init.detail; },
  performance: { now: () => 1 },
  document: { dispatchEvent() {} },
  localStorage: { setItem() {} },
  NEW_SESSION_DRAFT_KEY: '__new_session_draft__',
  messageInput,
  currentSessionId: 'existing',
  materializeNewSessionQueue: null,
  switchSessionEpoch: 0,
  messageLoadEpoch: 0,
  replayingMessages: false,
  cancelSmoothStreamFollowForSessionSwitch() {},
  saveChatScrollForSession() {},
  stashInputDraft() {},
  stashSkillPickerDraft() {},
  prepareStashLeaving() {},
  hideSubagentContinueBanner() {},
  resetSubagentPanelForSession() {},
  clearOptionalPanelsForSessionLoad() {},
  clearTocForSessionLoad() {},
  setCurrentSessionState(sessionId) {
    ctx.currentSessionId = sessionId || null;
    vm.runInContext(`currentSessionId = ${JSON.stringify(sessionId || null)}`, ctx);
  },
  getVisibleChatStream: () => stream,
  ensureVisibleChatStreamSlot() {},
  setWelcome() {},
  restoreInputDraft(sessionId) {
    if (sessionId) restoredRealSession = true;
    else messageInput.value = 'draft text';
  },
  restoreSkillPickerDraft() {},
  renderFollowupQueue() {},
  refreshModelProfileSelector() {},
  updateSessionTitle() {},
  syncSessionListIndicatorClasses() {},
  hideLoading() {},
  setSendButtonState() {},
  sessionStore: {
    protected: null,
    protectFromSnapshots(session) { this.protected = session; },
  },
  readStoredInputDraft: () => 'stored draft',
  persisted: [],
  persistInputDraft(sessionId, value) { ctx.persisted.push([sessionId, value]); },
  removeStoredInputDraft() {},
  updateHumanInteractionBanner() {},
  syncFollowupQueueFromServer() {},
  syncArchivedSessionStateFromStore() {},
  renderSessionListIfChanged() {},
  refreshSingleSessionRow() {},
  loadSessions: async () => true,
  maybeStartStreamPollForSession() {},
  scheduleContextTokensAfterPaint() {},
  uiPerformance: undefined,
  appendLogVisible() {},
  fetch: async () => {
    fetchCalls += 1;
    await createGate;
    return {
      ok: true,
      json: async () => ({ session_id: 'created', session: { id: 'created', name: 'New' } }),
    };
  },
});

vm.runInContext(
  `let materializeNewSessionQueue = null;\n${lifecycleSource}\n`
    + 'globalThis.__createNewSession = createNewSession;\n'
    + 'globalThis.__materializeNewSession = materializeNewSession;',
  ctx,
);

(async () => {
  await ctx.__createNewSession();
  assert.strictEqual(fetchCalls, 0, 'opening a new page must not create a durable session');
  assert.strictEqual(ctx.currentSessionId, null);
  assert.strictEqual(messageInput.value, 'draft text');

  messageInput.value = 'live text must survive';
  const materializing = ctx.__materializeNewSession();
  await Promise.resolve();
  assert.strictEqual(fetchCalls, 1, 'the first send materializes exactly one session');
  releaseCreate();
  assert.strictEqual(await materializing, 'created');
  assert.strictEqual(messageInput.value, 'live text must survive');
  assert.strictEqual(restoredRealSession, false, 'POST completion must not restore over live input');
  assert.deepStrictEqual(ctx.persisted[0], ['created', 'live text must survive']);
  assert.strictEqual(ctx.sessionStore.protected.id, 'created');

  process.stdout.write('new session lifecycle runtime checks passed\n');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
