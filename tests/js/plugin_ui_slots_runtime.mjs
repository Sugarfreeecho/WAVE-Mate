import assert from 'node:assert/strict';
import {
    installPluginUiContributions,
    normalizePluginNavigationContributions,
    normalizePluginMessageRenderers,
    normalizePluginComposerActions,
    normalizePluginSessionUiResponse,
    normalizePluginSessionPanelRenderers,
    normalizePluginChatExtensions,
    normalizePluginSettingsSections,
    normalizePluginSettingsResponse,
    resolvePluginExtensionEvent,
} from '../../frontend/src/app/plugin-ui-slots.js';

assert.deepEqual(normalizePluginChatExtensions([{
    plugin_id: 'change-review', id: 'change-review', slot: 'chat.extension',
    renderer: {
        module: '/plugin-assets/change-review/change-review.js?v=abc',
        style: '/plugin-assets/change-review/change-review.css?v=abc',
    },
}, {
    plugin_id: 'evil', id: 'chat', slot: 'chat.extension',
    renderer: { module: 'https://evil.example/chat.js' },
}]), [{
    pluginId: 'change-review', id: 'change-review',
    moduleUrl: '/plugin-assets/change-review/change-review.js?v=abc',
    styleUrl: '/plugin-assets/change-review/change-review.css?v=abc',
}]);

assert.deepEqual(normalizePluginSessionPanelRenderers([{
    plugin_id: 'session-todo', id: 'current-plan', slot: 'session.panel',
    renderer: {
        module: '/plugin-assets/session-todo/session-panel.js?v=abc',
        style: '/plugin-assets/session-todo/session-panel.css?v=abc',
    },
}, {
    plugin_id: 'evil', id: 'panel', slot: 'session.panel',
    renderer: { module: 'https://evil.example/panel.js' },
}]), [{
    pluginId: 'session-todo', id: 'current-plan',
    moduleUrl: '/plugin-assets/session-todo/session-panel.js?v=abc',
    styleUrl: '/plugin-assets/session-todo/session-panel.css?v=abc',
}]);

const rows = normalizePluginNavigationContributions([
    {
        plugin_id: 'game-arena',
        id: 'main',
        slot: 'navigation',
        label: '<img src=x onerror=alert(1)>',
        description: 'safe text',
        href: '/plugins/game-arena',
        order: 50,
    },
    {
        plugin_id: 'bad',
        id: 'main',
        slot: 'navigation',
        label: 'Redirect',
        href: 'https://evil.example',
    },
    {
        plugin_id: 'game-arena',
        id: 'main',
        slot: 'navigation',
        label: 'Duplicate',
        href: '/plugins/game-arena',
    },
]);

assert.deepEqual(rows, [{
    pluginId: 'game-arena',
    id: 'main',
    label: '<img src=x onerror=alert(1)>',
    description: 'safe text',
    href: '/plugins/game-arena',
    order: 50,
}]);

const messageRows = [
    {
        plugin_id: 'game-arena',
        id: 'game-update',
        slot: 'message.renderer',
        event_name: 'game_updated',
        title: '<img src=x onerror=alert(1)>',
        description: 'safe text',
        variant: 'success',
        fields: [
            { path: '/game_id', label: 'Game', format: 'text', optional: false },
            { path: '/last_move', label: 'Move', format: 'json', optional: true },
        ],
    },
    {
        plugin_id: 'bad',
        id: 'unsafe',
        slot: 'message.renderer',
        event_name: 'changed',
        title: 'Unsafe',
        fields: [{ path: '/__proto__/polluted', label: 'Bad' }],
    },
];
assert.equal(normalizePluginMessageRenderers(messageRows).length, 1);
installPluginUiContributions(messageRows);
assert.deepEqual(
    resolvePluginExtensionEvent({
        type: 'extension_event',
        plugin_id: 'game-arena',
        event_name: 'game_updated',
        data: { game_id: '<script>alert(1)</script>', last_move: [3, 4] },
    }),
    {
        handled: true,
        title: '<img src=x onerror=alert(1)>',
        description: 'safe text',
        variant: 'success',
        content: 'Game: <script>alert(1)</script>\nMove: [3,4]',
    },
);
assert.deepEqual(
    resolvePluginExtensionEvent({
        type: 'extension_event',
        plugin_id: 'unknown',
        event_name: 'changed',
        data: {},
    }),
    {
        handled: true,
        title: 'Extension',
        description: 'No active declarative renderer is available for this historical event.',
        variant: 'neutral',
        content: 'unknown / changed',
        fallback: true,
    },
);
const sessionUi = normalizePluginSessionUiResponse({
    ok: true,
    sessions: {
        s1: {
            badges: [
                { plugin_id: 'game-arena', id: 'active-game', label: '<img>', variant: 'info' },
                { plugin_id: 'agent-goal', id: 'active-goal', label: 'Goal', variant: 'info', display: 'activity' },
            ],
            panels: [{
                plugin_id: 'game-arena', id: 'current-game', title: '<script>', variant: 'success',
                actions: [{ id: 'clear', label: '<b>Clear</b>', variant: 'danger', confirm: '<script>' }],
                fields: [
                    { label: 'Game', value: '<img src=x>', format: 'text' },
                    {
                        label: 'Moves', format: 'list',
                        columns: [{ label: 'Player', format: 'text' }, { label: 'Move', format: 'text' }],
                        rows: [{ values: ['<b>A</b>', '<script>'] }],
                    },
                ],
            }],
        },
    },
}, ['s1', '__proto__']);
assert.deepEqual(sessionUi.s1.badges[0].label, '<img>');
assert.equal(sessionUi.s1.badges[0].display, 'badge');
assert.equal(sessionUi.s1.badges[1].display, 'activity');
assert.deepEqual(sessionUi.s1.panels[0].fields[0].value, '<img src=x>');
assert.deepEqual(sessionUi.s1.panels[0].fields[1], {
    label: 'Moves', format: 'list',
    columns: [{ label: 'Player', format: 'text' }, { label: 'Move', format: 'text' }],
    rows: [{ values: ['<b>A</b>', '<script>'] }],
});
assert.deepEqual(sessionUi.s1.panels[0].actions, [{
    id: 'clear', label: '<b>Clear</b>', variant: 'danger', confirm: '<script>',
}]);
assert.equal(Object.prototype.polluted, undefined);
assert.deepEqual(normalizePluginSettingsSections([{
    plugin_id: 'game-arena', id: 'main', slot: 'settings.section', title: '<img>', label: 'Open',
    target: 'plugin-page', href: '/plugins/game-arena', order: 5,
}, {
    plugin_id: 'bad', id: 'redirect', slot: 'settings.section', title: 'Bad', label: 'Bad',
    target: 'plugin-page', href: 'https://evil.example',
}]), [{
    pluginId: 'game-arena', id: 'main', title: '<img>', label: 'Open', description: '',
    target: 'plugin-page', href: '/plugins/game-arena', order: 5,
}]);
assert.deepEqual(normalizePluginSettingsResponse({
    ok: true,
    settings: {
        plugin_id: 'game-arena', title: 'Arena', valid: false, missing_required: ['token'],
        fields: [
            { id: 'enabled', type: 'boolean', title: 'Enabled', value: true },
            { id: 'token', type: 'string', format: 'secret', title: 'Token', configured: false, reference: 'GAME_TOKEN' },
        ],
    },
}, 'game-arena'), {
    pluginId: 'game-arena', title: 'Arena', description: '', valid: false, missingRequired: ['token'],
    fields: [
        { id: 'enabled', type: 'boolean', title: 'Enabled', description: '', format: '', required: false, value: true },
        { id: 'token', type: 'string', title: 'Token', description: '', format: 'secret', required: false, configured: false, reference: 'GAME_TOKEN' },
    ],
});
assert.deepEqual(normalizePluginComposerActions([{
    plugin_id: 'game-arena', id: 'draft', slot: 'composer.action', label: '<script>',
    action: 'insert_text', text: '<img src=x>',
}, {
    plugin_id: 'game-arena', id: 'send', slot: 'composer.action', label: 'Send now',
    action: 'send_message', text: 'unsafe',
}]), [{
    pluginId: 'game-arena', id: 'draft', label: '<script>', description: '', action: 'insert_text',
    order: 100, text: '<img src=x>',
}]);
console.log('plugin UI slot runtime checks passed');
