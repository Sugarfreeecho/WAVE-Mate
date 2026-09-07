const PLUGIN_ID_PATTERN = /^[a-z0-9][a-z0-9._-]{0,127}$/;
const CONTRIBUTION_ID_PATTERN = /^[a-z][a-z0-9._-]{0,63}$/;
const MESSAGE_VARIANTS = new Set(['neutral', 'info', 'success', 'warning', 'danger']);
const FIELD_FORMATS = new Set(['text', 'number', 'boolean', 'json']);
const UNSAFE_POINTER_PARTS = new Set(['__proto__', 'prototype', 'constructor']);
let pluginMessageRenderers = new Map();
let pluginUiContributionsLoaded = false;
let sessionUiRequest = typeof globalThis.fetch === 'function'
    ? globalThis.fetch.bind(globalThis) : null;
let sessionUiObserver = null;
let sessionUiRefreshTimer = null;
let sessionUiRequestGeneration = 0;
let sessionUiFullRefreshPending = false;
let sessionUiPendingIds = new Set();
let sessionUiLatestGeneration = new Map();
let pluginSessionUiCache = new Map();
let pluginSessionPanelRenderers = new Map();
let pluginSessionPanelCleanups = [];
let pluginChatExtensionCleanups = [];

export function normalizePluginChatExtensions(rows) {
    if (!Array.isArray(rows)) return [];
    const seen = new Set();
    return rows.slice(0, 64).flatMap(function (raw) {
        if (!raw || typeof raw !== 'object' || raw.slot !== 'chat.extension'
            || !raw.renderer || typeof raw.renderer !== 'object') return [];
        const pluginId = String(raw.plugin_id || '').trim();
        const id = String(raw.id || '').trim();
        const moduleUrl = String(raw.renderer.module || '').trim();
        const styleUrl = String(raw.renderer.style || '').trim();
        const prefix = `/plugin-assets/${pluginId}/`;
        const safeUrl = function (value, suffixes) {
            if (!value || !value.startsWith(prefix) || value.includes('..') || value.includes('\\')) return false;
            const path = value.split('?', 1)[0].toLowerCase();
            return suffixes.some(function (suffix) { return path.endsWith(suffix); });
        };
        const key = `${pluginId}:${id}`;
        if (!PLUGIN_ID_PATTERN.test(pluginId) || !CONTRIBUTION_ID_PATTERN.test(id)
            || !safeUrl(moduleUrl, ['.js', '.mjs'])
            || (styleUrl && !safeUrl(styleUrl, ['.css'])) || seen.has(key)) return [];
        seen.add(key);
        return [{ pluginId, id, moduleUrl, styleUrl }];
    });
}

async function loadPluginChatExtensions(rows) {
    pluginChatExtensionCleanups.splice(0).forEach(function (cleanup) {
        try { cleanup(); } catch (error) { console.warn('Plugin chat extension cleanup failed', error); }
    });
    const definitions = normalizePluginChatExtensions(rows);
    await Promise.all(definitions.map(async function (definition) {
        try {
            if (definition.styleUrl && !document.querySelector(`link[data-plugin-chat-style="${definition.pluginId}:${definition.id}"]`)) {
                const link = document.createElement('link');
                link.rel = 'stylesheet';
                link.href = definition.styleUrl;
                link.dataset.pluginChatStyle = `${definition.pluginId}:${definition.id}`;
                document.head.appendChild(link);
            }
            const module = await import(/* @vite-ignore */ definition.moduleUrl);
            if (!module || typeof module.installChatExtension !== 'function') return;
            const cleanup = await module.installChatExtension({
                pluginId: definition.pluginId,
                id: definition.id,
                request: sessionUiRequest,
                translate: function (value) {
                    return typeof globalThis.translateUiString === 'function'
                        ? globalThis.translateUiString(value) : value;
                },
            });
            if (typeof cleanup === 'function') pluginChatExtensionCleanups.push(cleanup);
        } catch (error) {
            console.warn(`Plugin chat extension failed to load (${definition.pluginId})`, error);
        }
    }));
}

export function normalizePluginSessionPanelRenderers(rows) {
    if (!Array.isArray(rows)) return [];
    const seen = new Set();
    return rows.slice(0, 128).flatMap(function (raw) {
        if (!raw || typeof raw !== 'object' || raw.slot !== 'session.panel'
            || !raw.renderer || typeof raw.renderer !== 'object') return [];
        const pluginId = String(raw.plugin_id || '').trim();
        const id = String(raw.id || '').trim();
        const moduleUrl = String(raw.renderer.module || '').trim();
        const styleUrl = String(raw.renderer.style || '').trim();
        const prefix = `/plugin-assets/${pluginId}/`;
        const safeUrl = function (value, suffixes) {
            if (!value || !value.startsWith(prefix) || value.includes('..') || value.includes('\\')) return false;
            const path = value.split('?', 1)[0].toLowerCase();
            return suffixes.some(function (suffix) { return path.endsWith(suffix); });
        };
        const key = `${pluginId}:${id}`;
        if (!PLUGIN_ID_PATTERN.test(pluginId) || !CONTRIBUTION_ID_PATTERN.test(id)
            || !safeUrl(moduleUrl, ['.js', '.mjs'])
            || (styleUrl && !safeUrl(styleUrl, ['.css'])) || seen.has(key)) return [];
        seen.add(key);
        return [{ pluginId, id, moduleUrl, styleUrl }];
    });
}

async function loadPluginSessionPanelRenderers(rows) {
    const definitions = normalizePluginSessionPanelRenderers(rows);
    const loaded = new Map();
    await Promise.all(definitions.map(async function (definition) {
        try {
            if (definition.styleUrl && !document.querySelector(`link[data-plugin-panel-style="${definition.pluginId}:${definition.id}"]`)) {
                const link = document.createElement('link');
                link.rel = 'stylesheet';
                link.href = definition.styleUrl;
                link.dataset.pluginPanelStyle = `${definition.pluginId}:${definition.id}`;
                document.head.appendChild(link);
            }
            const module = await import(/* @vite-ignore */ definition.moduleUrl);
            if (module && typeof module.renderSessionPanel === 'function') {
                loaded.set(`${definition.pluginId}:${definition.id}`, module.renderSessionPanel);
            }
        } catch (error) {
            console.warn(`Plugin session panel renderer failed to load (${definition.pluginId})`, error);
        }
    }));
    pluginSessionPanelRenderers = loaded;
}

export function normalizePluginNavigationContributions(rows) {
    if (!Array.isArray(rows)) return [];
    const seen = new Set();
    return rows.slice(0, 64).flatMap(function (raw) {
        if (!raw || typeof raw !== 'object' || raw.slot !== 'navigation') return [];
        const pluginId = String(raw.plugin_id || '').trim();
        const id = String(raw.id || '').trim();
        const label = String(raw.label || '').trim();
        const description = String(raw.description || '').trim();
        const expectedHref = `/plugins/${pluginId}`;
        const key = `${pluginId}:${id}`;
        if (!PLUGIN_ID_PATTERN.test(pluginId)
            || !CONTRIBUTION_ID_PATTERN.test(id)
            || !label
            || label.length > 64
            || description.length > 200
            || String(raw.href || '') !== expectedHref
            || seen.has(key)) {
            return [];
        }
        seen.add(key);
        const parsedOrder = Number(raw.order);
        return [{
            pluginId,
            id,
            label,
            description,
            href: expectedHref,
            order: Number.isFinite(parsedOrder) ? parsedOrder : 100,
        }];
    }).sort(function (left, right) {
        return left.order - right.order
            || left.label.localeCompare(right.label)
            || left.pluginId.localeCompare(right.pluginId)
            || left.id.localeCompare(right.id);
    });
}

function contributionOrder(raw) {
    const parsed = Number(raw && raw.order);
    return Number.isFinite(parsed) ? Math.max(-10000, Math.min(10000, parsed)) : 100;
}

export function normalizePluginSettingsSections(rows) {
    if (!Array.isArray(rows)) return [];
    const seen = new Set();
    return rows.slice(0, 64).flatMap(function (raw) {
        if (!raw || typeof raw !== 'object' || raw.slot !== 'settings.section') return [];
        const pluginId = String(raw.plugin_id || '').trim();
        const id = String(raw.id || '').trim();
        const title = boundedText(raw.title, 64);
        const label = boundedText(raw.label, 64);
        const description = boundedText(raw.description, 200);
        const target = String(raw.target || '').trim();
        const expectedHref = `/plugins/${pluginId}`;
        const expectedEndpoint = `/api/plugins/${pluginId}/settings`;
        const key = `${pluginId}:${id}`;
        if (!PLUGIN_ID_PATTERN.test(pluginId) || !CONTRIBUTION_ID_PATTERN.test(id)
            || !title || !label || !new Set(['plugin-page', 'plugin-settings']).has(target)
            || (target === 'plugin-page' && String(raw.href || '') !== expectedHref)
            || (target === 'plugin-settings' && String(raw.endpoint || '') !== expectedEndpoint)
            || seen.has(key)) return [];
        seen.add(key);
        const item = { pluginId, id, title, label, description, target, order: contributionOrder(raw) };
        if (target === 'plugin-page') item.href = expectedHref;
        else item.endpoint = expectedEndpoint;
        return [item];
    }).sort(function (left, right) {
        return left.order - right.order || left.title.localeCompare(right.title)
            || left.pluginId.localeCompare(right.pluginId) || left.id.localeCompare(right.id);
    });
}

export function normalizePluginComposerActions(rows) {
    if (!Array.isArray(rows)) return [];
    const seen = new Set();
    return rows.slice(0, 64).flatMap(function (raw) {
        if (!raw || typeof raw !== 'object' || raw.slot !== 'composer.action') return [];
        const pluginId = String(raw.plugin_id || '').trim();
        const id = String(raw.id || '').trim();
        const label = boundedText(raw.label, 64);
        const description = boundedText(raw.description, 200);
        const action = String(raw.action || '').trim();
        const key = `${pluginId}:${id}`;
        if (!PLUGIN_ID_PATTERN.test(pluginId) || !CONTRIBUTION_ID_PATTERN.test(id)
            || !label || !new Set(['insert_text', 'open_plugin_page']).has(action) || seen.has(key)) return [];
        const item = { pluginId, id, label, description, action, order: contributionOrder(raw) };
        if (action === 'insert_text') {
            const text = String(raw.text || '');
            if (!text.trim() || text.length > 2000) return [];
            item.text = text;
        } else {
            const expectedHref = `/plugins/${pluginId}`;
            if (String(raw.href || '') !== expectedHref) return [];
            item.href = expectedHref;
        }
        seen.add(key);
        return [item];
    }).sort(function (left, right) {
        return left.order - right.order || left.label.localeCompare(right.label)
            || left.pluginId.localeCompare(right.pluginId) || left.id.localeCompare(right.id);
    });
}

function normalizeRendererField(raw) {
    if (!raw || typeof raw !== 'object') return null;
    const path = String(raw.path || '').trim();
    const label = String(raw.label || '').trim();
    const format = String(raw.format || 'text').trim().toLowerCase();
    if (!path.startsWith('/') || path.length > 160 || !label || label.length > 64
        || !FIELD_FORMATS.has(format)) return null;
    const parts = path.slice(1).split('/').map(function (part) {
        return part.replace(/~1/g, '/').replace(/~0/g, '~');
    });
    if (parts.some(function (part) { return !part || UNSAFE_POINTER_PARTS.has(part); })) return null;
    return { path, label, format, optional: raw.optional !== false };
}

export function normalizePluginMessageRenderers(rows) {
    if (!Array.isArray(rows)) return [];
    const seenIds = new Set();
    const seenEvents = new Set();
    return rows.slice(0, 128).flatMap(function (raw) {
        if (!raw || typeof raw !== 'object' || raw.slot !== 'message.renderer') return [];
        const pluginId = String(raw.plugin_id || '').trim();
        const id = String(raw.id || '').trim();
        const eventName = String(raw.event_name || '').trim();
        const title = String(raw.title || '').trim();
        const description = String(raw.description || '').trim();
        const variant = String(raw.variant || 'neutral').trim().toLowerCase();
        const rawFields = Array.isArray(raw.fields) ? raw.fields : [];
        const fields = rawFields.map(normalizeRendererField);
        const idKey = `${pluginId}:${id}`;
        const eventKey = `${pluginId}:${eventName}`;
        if (!PLUGIN_ID_PATTERN.test(pluginId)
            || !CONTRIBUTION_ID_PATTERN.test(id)
            || !CONTRIBUTION_ID_PATTERN.test(eventName)
            || !title
            || title.length > 64
            || description.length > 200
            || !MESSAGE_VARIANTS.has(variant)
            || rawFields.length < 1
            || rawFields.length > 12
            || fields.some(function (field) { return !field; })
            || new Set(fields.map(function (field) { return field.path; })).size !== fields.length
            || seenIds.has(idKey)
            || seenEvents.has(eventKey)) {
            return [];
        }
        seenIds.add(idKey);
        seenEvents.add(eventKey);
        return [{ pluginId, id, eventName, title, description, variant, fields }];
    });
}

function pointerValue(value, pointer) {
    const parts = pointer.slice(1).split('/').map(function (part) {
        return part.replace(/~1/g, '/').replace(/~0/g, '~');
    });
    let current = value;
    for (const part of parts) {
        if (current == null || (typeof current !== 'object')
            || !Object.prototype.hasOwnProperty.call(current, part)) {
            return { found: false, value: undefined };
        }
        current = current[part];
    }
    return { found: true, value: current };
}

function boundedText(value, limit) {
    const text = String(value == null ? '' : value).replace(/[\r\n]+/g, ' ').trim();
    return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

function formatRendererValue(value, format) {
    if (format === 'number') {
        const number = Number(value);
        return Number.isFinite(number) ? String(number) : '';
    }
    if (format === 'boolean') {
        return typeof value === 'boolean' ? (value ? 'true' : 'false') : '';
    }
    if (format === 'json' || (value !== null && typeof value === 'object')) {
        try { return boundedText(JSON.stringify(value), 2000); }
        catch (_error) { return ''; }
    }
    return boundedText(value, 1000);
}

export function installPluginUiContributions(rows) {
    const renderers = normalizePluginMessageRenderers(rows);
    pluginMessageRenderers = new Map(renderers.map(function (renderer) {
        return [`${renderer.pluginId}:${renderer.eventName}`, renderer];
    }));
    pluginUiContributionsLoaded = true;
    return renderers;
}

export function resolvePluginExtensionEvent(event) {
    if (!event || typeof event !== 'object' || event.type !== 'extension_event') {
        return { handled: false };
    }
    if (!pluginUiContributionsLoaded) return { handled: true, pending: true };
    const pluginId = String(event.plugin_id || '').trim();
    const eventName = String(event.event_name || '').trim();
    const renderer = pluginMessageRenderers.get(`${pluginId}:${eventName}`);
    if (!renderer) {
        return {
            handled: true,
            title: 'Extension',
            description: 'No active declarative renderer is available for this historical event.',
            variant: 'neutral',
            // Preserve history pagination/count consistency without exposing
            // an undeclared plugin payload.
            content: `${boundedText(pluginId, 128)} / ${boundedText(eventName, 64)}`,
            fallback: true,
        };
    }
    const data = event.data && typeof event.data === 'object' ? event.data : {};
    const lines = [];
    renderer.fields.forEach(function (field) {
        const resolved = pointerValue(data, field.path);
        const value = resolved.found ? formatRendererValue(resolved.value, field.format) : '';
        if (!value && field.optional) return;
        lines.push(`${field.label}: ${value || '—'}`);
    });
    return {
        handled: true,
        title: renderer.title,
        description: renderer.description,
        variant: renderer.variant,
        content: lines.join('\n'),
    };
}

globalThis.resolvePluginExtensionEvent = resolvePluginExtensionEvent;

export function renderPluginSettingsSections(container, rows) {
    if (!container) return;
    const sections = normalizePluginSettingsSections(rows);
    const fragment = document.createDocumentFragment();
    sections.forEach(function (item) {
        const section = document.createElement('div');
        section.className = 'settings-modal__section plugin-settings-section';
        section.dataset.pluginId = item.pluginId;
        section.dataset.contributionId = item.id;
        const title = document.createElement('div');
        title.className = 'settings-modal__label';
        title.textContent = item.title;
        section.appendChild(title);
        if (item.description) {
            const description = document.createElement('div');
            description.className = 'plugin-settings-description';
            description.textContent = item.description;
            section.appendChild(description);
        }
        if (item.target === 'plugin-page') {
            const link = document.createElement('a');
            link.className = 'settings-advanced-btn plugin-settings-link';
            link.href = item.href;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            link.textContent = item.label;
            section.appendChild(link);
        } else {
            const form = document.createElement('form');
            form.className = 'plugin-settings-form';
            form.dataset.endpoint = item.endpoint;
            form.dataset.saveLabel = item.label;
            const status = document.createElement('div');
            status.className = 'plugin-settings-status';
            status.setAttribute('role', 'status');
            status.textContent = 'Loading…';
            form.appendChild(status);
            form.addEventListener('submit', function (event) {
                event.preventDefault();
                void savePluginSettingsForm(form, item);
            });
            section.appendChild(form);
            void loadPluginSettingsForm(form, item);
        }
        fragment.appendChild(section);
    });
    container.replaceChildren(fragment);
    container.hidden = sections.length === 0;
}

function normalizedSettingField(raw) {
    if (!raw || typeof raw !== 'object') return null;
    const id = String(raw.id || '').trim();
    const type = String(raw.type || '').trim();
    const title = boundedText(raw.title, 64);
    const description = boundedText(raw.description, 200);
    const format = String(raw.format || '').trim();
    if (!CONTRIBUTION_ID_PATTERN.test(id) || !new Set(['string', 'boolean', 'integer', 'number']).has(type)
        || !title || (format && !new Set(['text', 'multiline', 'secret']).has(format))) return null;
    const field = { id, type, title, description, format, required: raw.required === true };
    if (format === 'secret') {
        field.configured = raw.configured === true;
        field.reference = boundedText(raw.reference, 128);
        return field;
    }
    field.value = raw.value;
    if (Array.isArray(raw.enum) && raw.enum.length <= 32) field.enum = raw.enum.slice();
    if (Number.isFinite(Number(raw.minimum))) field.minimum = Number(raw.minimum);
    if (Number.isFinite(Number(raw.maximum))) field.maximum = Number(raw.maximum);
    if (Number.isInteger(Number(raw.min_length))) field.minLength = Number(raw.min_length);
    if (Number.isInteger(Number(raw.max_length))) field.maxLength = Number(raw.max_length);
    return field;
}

export function normalizePluginSettingsResponse(payload, pluginId) {
    const raw = payload && payload.ok === true && payload.settings && typeof payload.settings === 'object'
        ? payload.settings : null;
    if (!raw || String(raw.plugin_id || '') !== pluginId || !Array.isArray(raw.fields)
        || raw.fields.length > 64) return null;
    const fields = raw.fields.map(normalizedSettingField);
    if (fields.some(function (field) { return !field; })
        || new Set(fields.map(function (field) { return field.id; })).size !== fields.length) return null;
    return {
        pluginId,
        title: boundedText(raw.title, 64),
        description: boundedText(raw.description, 200),
        valid: raw.valid === true,
        missingRequired: Array.isArray(raw.missing_required)
            ? raw.missing_required.slice(0, 64).map(String) : [],
        fields,
    };
}

function createPluginSettingControl(field) {
    if (field.format === 'secret') {
        const status = document.createElement('div');
        status.className = `plugin-setting-secret ${field.configured ? 'is-configured' : 'is-missing'}`;
        status.textContent = field.configured
            ? `Configured via ${field.reference || 'host secret reference'}`
            : `Missing host secret ${field.reference || 'reference'}`;
        return status;
    }
    let input;
    if (field.enum) {
        input = document.createElement('select');
        field.enum.forEach(function (value) {
            const option = document.createElement('option');
            option.value = String(value);
            option.textContent = String(value);
            option.selected = value === field.value;
            input.appendChild(option);
        });
    } else if (field.type === 'boolean') {
        input = document.createElement('input');
        input.type = 'checkbox';
        input.checked = field.value === true;
    } else if (field.format === 'multiline') {
        input = document.createElement('textarea');
        input.rows = 3;
        input.value = field.value == null ? '' : String(field.value);
    } else {
        input = document.createElement('input');
        input.type = field.type === 'integer' || field.type === 'number' ? 'number' : 'text';
        input.value = field.value == null ? '' : String(field.value);
        if (field.type === 'integer') input.step = '1';
        if (field.type === 'number') input.step = 'any';
    }
    input.className = 'plugin-setting-control';
    input.dataset.settingId = field.id;
    input.dataset.settingType = field.type;
    input.required = field.required;
    if (field.minimum !== undefined) input.min = String(field.minimum);
    if (field.maximum !== undefined) input.max = String(field.maximum);
    if (field.minLength !== undefined) input.minLength = field.minLength;
    if (field.maxLength !== undefined) input.maxLength = field.maxLength;
    return input;
}

function renderPluginSettingsForm(form, model, item) {
    const fragment = document.createDocumentFragment();
    model.fields.forEach(function (field) {
        const row = document.createElement('label');
        row.className = 'plugin-setting-field';
        const label = document.createElement('span');
        label.className = 'plugin-setting-label';
        label.textContent = field.title;
        row.appendChild(label);
        if (field.description) {
            const description = document.createElement('span');
            description.className = 'plugin-setting-description';
            description.textContent = field.description;
            row.appendChild(description);
        }
        row.appendChild(createPluginSettingControl(field));
        fragment.appendChild(row);
    });
    const button = document.createElement('button');
    button.type = 'submit';
    button.className = 'settings-advanced-btn plugin-settings-save';
    button.textContent = item.label;
    const status = document.createElement('div');
    status.className = 'plugin-settings-status';
    status.setAttribute('role', 'status');
    if (!model.valid && model.missingRequired.length) {
        status.textContent = `Missing required settings: ${model.missingRequired.join(', ')}`;
        status.classList.add('is-error');
    }
    fragment.append(button, status);
    form.replaceChildren(fragment);
}

async function loadPluginSettingsForm(form, item) {
    try {
        const response = await sessionUiRequest(item.endpoint, {
            method: 'GET', credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' },
        });
        const payload = await response.json();
        const model = response.ok ? normalizePluginSettingsResponse(payload, item.pluginId) : null;
        if (!model) throw new Error((payload && payload.error) || `HTTP ${response.status}`);
        renderPluginSettingsForm(form, model, item);
    } catch (error) {
        const status = form.querySelector('.plugin-settings-status');
        if (status) {
            status.textContent = `Settings unavailable: ${String(error && error.message ? error.message : error)}`;
            status.classList.add('is-error');
        }
    }
}

function pluginSettingsFormValues(form) {
    const values = {};
    form.querySelectorAll('.plugin-setting-control[data-setting-id]').forEach(function (input) {
        const id = String(input.dataset.settingId || '');
        const type = String(input.dataset.settingType || 'string');
        if (input.tagName === 'SELECT' && type === 'boolean') values[id] = input.value === 'true';
        else if (input.tagName === 'SELECT' && type === 'integer') values[id] = Number.parseInt(input.value, 10);
        else if (input.tagName === 'SELECT' && type === 'number') values[id] = Number(input.value);
        else if (type === 'boolean') values[id] = input.checked === true;
        else if ((type === 'integer' || type === 'number') && input.value === '') values[id] = null;
        else if (type === 'integer') values[id] = Number.parseInt(input.value, 10);
        else if (type === 'number') values[id] = Number(input.value);
        else values[id] = input.value;
    });
    return values;
}

async function savePluginSettingsForm(form, item) {
    const button = form.querySelector('.plugin-settings-save');
    const status = form.querySelector('.plugin-settings-status');
    if (button) button.disabled = true;
    if (status) { status.textContent = 'Saving…'; status.classList.remove('is-error'); }
    try {
        const response = await sessionUiRequest(item.endpoint, {
            method: 'PATCH', credentials: 'same-origin', cache: 'no-store',
            headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
            body: JSON.stringify({ values: pluginSettingsFormValues(form) }),
        });
        const payload = await response.json();
        const model = response.ok ? normalizePluginSettingsResponse(payload, item.pluginId) : null;
        if (!model) throw new Error((payload && payload.error) || `HTTP ${response.status}`);
        renderPluginSettingsForm(form, model, item);
        const saved = form.querySelector('.plugin-settings-status');
        if (saved && model.valid) saved.textContent = 'Saved';
    } catch (error) {
        if (status) {
            status.textContent = `Save failed: ${String(error && error.message ? error.message : error)}`;
            status.classList.add('is-error');
        }
    } finally {
        if (button && button.isConnected) button.disabled = false;
    }
}

function insertComposerText(input, text) {
    if (!input) return;
    const start = Number.isInteger(input.selectionStart) ? input.selectionStart : input.value.length;
    const end = Number.isInteger(input.selectionEnd) ? input.selectionEnd : start;
    if (typeof input.setRangeText === 'function') input.setRangeText(text, start, end, 'end');
    else input.value = `${input.value.slice(0, start)}${text}${input.value.slice(end)}`;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.focus();
}

export function renderPluginComposerActions(container, rows) {
    if (!container) return;
    const actions = normalizePluginComposerActions(rows);
    const fragment = document.createDocumentFragment();
    actions.forEach(function (item) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'plugin-composer-action';
        button.dataset.pluginId = item.pluginId;
        button.dataset.contributionId = item.id;
        button.textContent = item.label;
        if (item.description) {
            button.title = item.description;
            button.setAttribute('aria-label', `${item.label}: ${item.description}`);
        }
        button.addEventListener('click', function () {
            if (item.action === 'insert_text') {
                insertComposerText(document.getElementById('message-input'), item.text);
                return;
            }
            if (typeof globalThis.open !== 'function') return;
            const opened = globalThis.open(item.href, '_blank', 'noopener,noreferrer');
            if (opened) opened.opener = null;
        });
        fragment.appendChild(button);
    });
    container.replaceChildren(fragment);
    container.hidden = actions.length === 0;
}

function normalizeProjectedItem(raw, kind) {
    if (!raw || typeof raw !== 'object') return null;
    const pluginId = String(raw.plugin_id || '').trim();
    const id = String(raw.id || '').trim();
    const variant = String(raw.variant || 'neutral').trim().toLowerCase();
    if (!PLUGIN_ID_PATTERN.test(pluginId) || !CONTRIBUTION_ID_PATTERN.test(id)
        || !MESSAGE_VARIANTS.has(variant)) return null;
    if (kind === 'badge') {
        const label = boundedText(raw.label, 64);
        const description = boundedText(raw.description, 200);
        const display = String(raw.display || 'badge').trim().toLowerCase();
        if (!label || !new Set(['badge', 'activity']).has(display)) return null;
        return { pluginId, id, label, description, variant, display };
    }
    const title = boundedText(raw.title, 64);
    const description = boundedText(raw.description, 200);
    if (!title || !Array.isArray(raw.fields) || raw.fields.length > 12) return null;
    const fields = raw.fields.flatMap(function (field) {
        if (!field || typeof field !== 'object') return [];
        const label = boundedText(field.label, 64);
        const format = String(field.format || 'text').toLowerCase();
        if (format === 'list') {
            if (!label || !Array.isArray(field.columns) || !Array.isArray(field.rows)
                || field.columns.length < 1 || field.columns.length > 4 || field.rows.length > 100) return [];
            const columns = field.columns.map(function (column) {
                const columnLabel = boundedText(column && column.label, 64);
                const columnFormat = String(column && column.format || 'text').toLowerCase();
                return columnLabel && FIELD_FORMATS.has(columnFormat)
                    ? { label: columnLabel, format: columnFormat } : null;
            });
            if (columns.some(function (column) { return !column; })) return [];
            const rows = field.rows.flatMap(function (row) {
                if (!row || !Array.isArray(row.values) || row.values.length !== columns.length) return [];
                return [{ values: row.values.map(function (value) { return boundedText(value, 1000); }) }];
            });
            if (rows.length !== field.rows.length) return [];
            return [{ label, format, columns, rows }];
        }
        const value = boundedText(field.value, 4000);
        if (!label || !FIELD_FORMATS.has(format)) return [];
        return [{ label, value, format }];
    });
    const actions = (Array.isArray(raw.actions) ? raw.actions : []).slice(0, 4).flatMap(function (action) {
        if (!action || typeof action !== 'object') return [];
        const actionId = String(action.id || '').trim();
        const label = boundedText(action.label, 64);
        const actionVariant = String(action.variant || 'neutral').trim().toLowerCase();
        const confirm = boundedText(action.confirm, 200);
        if (!CONTRIBUTION_ID_PATTERN.test(actionId) || !label || !MESSAGE_VARIANTS.has(actionVariant)) return [];
        const rawInputs = Array.isArray(action.inputs) ? action.inputs.slice(0, 8) : [];
        const inputIds = new Set();
        const inputs = rawInputs.flatMap(function (rawInput) {
            if (!rawInput || typeof rawInput !== 'object') return [];
            const id = String(rawInput.id || '').trim();
            const inputLabel = boundedText(rawInput.label || id, 64);
            const description = boundedText(rawInput.description, 200);
            const type = String(rawInput.type || 'string').trim().toLowerCase();
            if (!CONTRIBUTION_ID_PATTERN.test(id) || inputIds.has(id) || !inputLabel
                || !new Set(['string', 'boolean', 'integer', 'number']).has(type)) return [];
            inputIds.add(id);
            const input = { id, label: inputLabel, description, type, required: rawInput.required === true };
            if (Array.isArray(rawInput.enum) && rawInput.enum.length > 0 && rawInput.enum.length <= 32) {
                input.enum = rawInput.enum.slice();
            }
            ['minimum', 'maximum', 'min_length', 'max_length'].forEach(function (key) {
                if (Number.isFinite(Number(rawInput[key]))) input[key] = Number(rawInput[key]);
            });
            return [input];
        });
        if (inputs.length !== rawInputs.length) return [];
        const normalized = { id: actionId, label, variant: actionVariant, confirm };
        if (inputs.length) normalized.inputs = inputs;
        return [normalized];
    });
    return { pluginId, id, title, description, variant, fields, actions };
}

export function normalizePluginSessionUiResponse(payload, requestedSessionIds) {
    const sessions = payload && payload.ok === true && payload.sessions
        && typeof payload.sessions === 'object' ? payload.sessions : {};
    const out = Object.create(null);
    (Array.isArray(requestedSessionIds) ? requestedSessionIds : []).slice(0, 200).forEach(function (rawId) {
        const sessionId = String(rawId || '').trim();
        if (!sessionId || sessionId.length > 256 || Object.prototype.hasOwnProperty.call(out, sessionId)) return;
        const raw = Object.prototype.hasOwnProperty.call(sessions, sessionId) && sessions[sessionId]
            && typeof sessions[sessionId] === 'object' ? sessions[sessionId] : {};
        out[sessionId] = {
            badges: (Array.isArray(raw.badges) ? raw.badges : []).slice(0, 16)
                .map(function (item) { return normalizeProjectedItem(item, 'badge'); }).filter(Boolean),
            panels: (Array.isArray(raw.panels) ? raw.panels : []).slice(0, 16)
                .map(function (item) { return normalizeProjectedItem(item, 'panel'); }).filter(Boolean),
        };
    });
    return out;
}

function visibleSessionRows() {
    return Array.from(document.querySelectorAll('#sessions-list .session-item[data-session-id]'))
        .slice(0, 200);
}

function renderSessionBadges(rows, sessions) {
    rows.forEach(function (row) {
        const old = row.querySelector('.plugin-session-badges');
        if (old) old.remove();
        const sessionId = String(row.dataset.sessionId || '');
        const model = sessions[sessionId];
        const activity = Boolean(model && model.badges.some(function (item) {
            return item.display === 'activity';
        }));
        row.classList.toggle('has-plugin-activity', activity);
        const badges = model ? model.badges.filter(function (item) {
            return item.display !== 'activity';
        }) : [];
        if (!badges.length) return;
        const titleRow = row.querySelector('.session-item-title-row');
        if (!titleRow) return;
        const host = document.createElement('span');
        host.className = 'plugin-session-badges';
        badges.forEach(function (item) {
            const badge = document.createElement('span');
            badge.className = `plugin-session-badge plugin-session-badge--${item.variant}`;
            badge.dataset.pluginId = item.pluginId;
            badge.dataset.contributionId = item.id;
            badge.textContent = item.label;
            if (item.description) badge.title = item.description;
            host.appendChild(badge);
        });
        const date = titleRow.querySelector('.session-item-date');
        titleRow.insertBefore(host, date || null);
    });
}

function renderSessionPanels(rows, sessions) {
    const host = document.getElementById('plugin-session-panels');
    if (!host) return;
    const active = rows.find(function (row) { return row.classList.contains('active'); });
    const sessionId = active ? String(active.dataset.sessionId || '') : '';
    const retainedCleanups = [];
    pluginSessionPanelCleanups.forEach(function (cleanup) {
        try {
            if (cleanup({ nextSessionId: sessionId }) === false) retainedCleanups.push(cleanup);
        }
        catch (error) { console.warn('Plugin session panel cleanup failed', error); }
    });
    pluginSessionPanelCleanups = retainedCleanups;
    const model = sessions[sessionId];
    const panels = model ? model.panels : [];
    const fragment = document.createDocumentFragment();
    panels.forEach(function (item) {
        const panel = document.createElement('section');
        panel.className = `plugin-session-panel plugin-session-panel--${item.variant}`;
        panel.dataset.pluginId = item.pluginId;
        panel.dataset.contributionId = item.id;
        const customRenderer = pluginSessionPanelRenderers.get(`${item.pluginId}:${item.id}`);
        if (customRenderer) {
            panel.classList.add('plugin-session-panel--custom');
            try {
                const cleanup = customRenderer({
                    container: panel,
                    item,
                    sessionId,
                    request: sessionUiRequest,
                    refresh: refreshPluginSessionUi,
                    invokeAction: async function (actionId, inputs = {}) {
                        const response = await sessionUiRequest('/api/extensions/session-action', {
                            method: 'POST', credentials: 'same-origin', cache: 'no-store',
                            headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                session_id: sessionId, plugin_id: item.pluginId,
                                action_id: actionId, inputs,
                            }),
                        });
                        if (!response.ok) throw new Error(`Session extension action failed (${response.status})`);
                        return response;
                    },
                    notifyStateChanged: function () {
                        document.dispatchEvent(new CustomEvent('myagent:extension-state-changed', {
                            detail: { sessionId },
                        }));
                    },
                });
                if (typeof cleanup === 'function') pluginSessionPanelCleanups.push(cleanup);
                fragment.appendChild(panel);
                return;
            } catch (error) {
                console.warn(`Plugin session panel renderer failed (${item.pluginId})`, error);
                panel.replaceChildren();
                panel.classList.remove('plugin-session-panel--custom');
            }
        }
        const heading = document.createElement('div');
        heading.className = 'plugin-session-panel-title';
        heading.textContent = item.title;
        panel.appendChild(heading);
        if (item.description) {
            const description = document.createElement('div');
            description.className = 'plugin-session-panel-description';
            description.textContent = item.description;
            panel.appendChild(description);
        }
        const fields = document.createElement('dl');
        fields.className = 'plugin-session-panel-fields';
        item.fields.forEach(function (field) {
            const row = document.createElement('div');
            row.className = 'plugin-session-panel-field';
            const label = document.createElement('dt');
            label.textContent = field.label;
            const value = document.createElement('dd');
            if (field.format === 'list') {
                value.className = 'plugin-session-panel-list';
                field.rows.forEach(function (itemRow) {
                    const listRow = document.createElement('div');
                    listRow.className = 'plugin-session-panel-list-row';
                    itemRow.values.forEach(function (itemValue, index) {
                        const cell = document.createElement('span');
                        cell.className = 'plugin-session-panel-list-cell';
                        const column = field.columns[index];
                        cell.title = column.label;
                        const key = document.createElement('span');
                        key.className = 'plugin-session-panel-list-key';
                        key.textContent = `${column.label}: `;
                        const text = document.createElement('span');
                        text.textContent = itemValue || '—';
                        cell.append(key, text);
                        listRow.appendChild(cell);
                    });
                    value.appendChild(listRow);
                });
            } else {
                value.textContent = field.value;
            }
            row.append(label, value);
            fields.appendChild(row);
        });
        panel.appendChild(fields);
        if (item.actions.length) {
            const actions = document.createElement('div');
            actions.className = 'plugin-session-panel-actions';
            item.actions.forEach(function (action) {
                const actionGroup = document.createElement('div');
                actionGroup.className = 'plugin-session-panel-action-group';
                const controls = new Map();
                (action.inputs || []).forEach(function (input) {
                    const field = document.createElement('label');
                    field.className = 'plugin-session-panel-action-field';
                    const caption = document.createElement('span');
                    caption.textContent = input.label;
                    let control;
                    if (Array.isArray(input.enum)) {
                        control = document.createElement('select');
                        if (!input.required) {
                            const blank = document.createElement('option');
                            blank.value = '';
                            blank.textContent = '—';
                            control.appendChild(blank);
                        }
                        input.enum.forEach(function (value, index) {
                            const option = document.createElement('option');
                            option.value = String(index + 1);
                            option.textContent = boundedText(value, 200);
                            control.appendChild(option);
                        });
                    } else if (input.type === 'boolean') {
                        control = document.createElement('input');
                        control.type = 'checkbox';
                    } else if (input.type === 'string' && Number(input.max_length || 0) > 200) {
                        control = document.createElement('textarea');
                    } else {
                        control = document.createElement('input');
                        control.type = input.type === 'string' ? 'text' : 'number';
                        if (input.type === 'integer') control.step = '1';
                        if (input.type === 'number') control.step = 'any';
                    }
                    control.name = input.id;
                    control.required = input.required;
                    if (input.description) control.title = input.description;
                    if (Number.isFinite(input.minimum)) control.min = String(input.minimum);
                    if (Number.isFinite(input.maximum)) control.max = String(input.maximum);
                    if (Number.isFinite(input.min_length)) control.minLength = input.min_length;
                    if (Number.isFinite(input.max_length)) control.maxLength = input.max_length;
                    field.append(caption, control);
                    actionGroup.appendChild(field);
                    controls.set(input.id, { control, definition: input });
                });
                const button = document.createElement('button');
                button.type = 'button';
                button.className = `plugin-session-panel-action plugin-session-panel-action--${action.variant}`;
                button.textContent = action.label;
                button.addEventListener('click', async function () {
                    if (action.confirm && typeof globalThis.confirm === 'function'
                        && !globalThis.confirm(action.confirm)) return;
                    const request = sessionUiRequest;
                    if (typeof request !== 'function') return;
                    const inputValues = {};
                    for (const [inputId, entry] of controls) {
                        const control = entry.control;
                        const definition = entry.definition;
                        if (typeof control.checkValidity === 'function' && !control.checkValidity()) {
                            if (typeof control.reportValidity === 'function') control.reportValidity();
                            return;
                        }
                        if (definition.type === 'boolean') {
                            inputValues[inputId] = Boolean(control.checked);
                            continue;
                        }
                        if (control.value === '' && !definition.required) continue;
                        if (Array.isArray(definition.enum)) {
                            inputValues[inputId] = definition.enum[Number(control.value) - 1];
                        } else if (definition.type === 'integer') {
                            inputValues[inputId] = Number.parseInt(control.value, 10);
                        } else if (definition.type === 'number') {
                            inputValues[inputId] = Number(control.value);
                        } else {
                            inputValues[inputId] = control.value;
                        }
                    }
                    button.disabled = true;
                    try {
                        const response = await request('/api/extensions/session-action', {
                            method: 'POST',
                            credentials: 'same-origin',
                            cache: 'no-store',
                            headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                session_id: sessionId,
                                plugin_id: item.pluginId,
                                action_id: action.id,
                                inputs: inputValues,
                            }),
                        });
                        if (!response.ok) throw new Error(`Session extension action failed (${response.status})`);
                        await refreshPluginSessionUi([sessionId]);
                    } catch (error) {
                        console.warn('Plugin session action failed', error);
                    } finally {
                        button.disabled = false;
                    }
                });
                actionGroup.appendChild(button);
                actions.appendChild(actionGroup);
            });
            panel.appendChild(actions);
        }
        fragment.appendChild(panel);
    });
    host.replaceChildren(fragment);
    const visiblePanelCount = Array.from(host.children).filter(function (panel) {
        return !panel.hidden;
    }).length;
    host.hidden = visiblePanelCount === 0;
    document.dispatchEvent(new CustomEvent('myagent:plugin-session-ui-rendered', {
        detail: { sessionId, panelCount: visiblePanelCount },
    }));
}

function cachedPluginSessionUi(rows) {
    const sessions = Object.create(null);
    rows.forEach(function (row) {
        const sessionId = String(row.dataset.sessionId || '');
        if (sessionId && pluginSessionUiCache.has(sessionId)) {
            sessions[sessionId] = pluginSessionUiCache.get(sessionId);
        }
    });
    return sessions;
}

export async function refreshPluginSessionUi(requestedSessionIds) {
    const request = sessionUiRequest;
    if (typeof request !== 'function') return;
    const rows = visibleSessionRows();
    const visibleIds = rows.map(function (row) { return String(row.dataset.sessionId || ''); })
        .filter(Boolean);
    const visibleSet = new Set(visibleIds);
    const requested = Array.isArray(requestedSessionIds)
        ? requestedSessionIds.map(function (value) { return String(value || '').trim(); })
        : visibleIds;
    const sessionIds = Array.from(new Set(requested)).filter(function (sessionId) {
        return sessionId && visibleSet.has(sessionId);
    }).slice(0, 200);
    const generation = ++sessionUiRequestGeneration;
    sessionIds.forEach(function (sessionId) {
        sessionUiLatestGeneration.set(sessionId, generation);
    });
    if (!sessionIds.length) {
        renderSessionBadges(rows, cachedPluginSessionUi(rows));
        renderSessionPanels(rows, cachedPluginSessionUi(rows));
        return;
    }
    try {
        const response = await request('/api/extensions/session-ui', {
            method: 'POST',
            credentials: 'same-origin',
            cache: 'no-store',
            headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_ids: sessionIds }),
        });
        if (!response.ok) throw new Error(`Session extension UI failed (${response.status})`);
        const payload = await response.json();
        const sessions = normalizePluginSessionUiResponse(payload, sessionIds);
        sessionIds.forEach(function (sessionId) {
            if (sessionUiLatestGeneration.get(sessionId) === generation) {
                pluginSessionUiCache.set(sessionId, sessions[sessionId]);
            }
        });
        const currentRows = visibleSessionRows();
        const cached = cachedPluginSessionUi(currentRows);
        renderSessionBadges(currentRows, cached);
        renderSessionPanels(currentRows, cached);
    } catch (error) {
        const isLatest = sessionIds.some(function (sessionId) {
            return sessionUiLatestGeneration.get(sessionId) === generation;
        });
        if (isLatest) console.warn('Plugin session UI refresh failed', error);
    }
}

function schedulePluginSessionUiRefresh(source) {
    if (source && source.full === true) {
        sessionUiFullRefreshPending = true;
        sessionUiPendingIds.clear();
    } else if (!sessionUiFullRefreshPending) {
        const ids = Array.isArray(source)
            ? source
            : [source && source.detail ? source.detail.sessionId : source];
        ids.forEach(function (value) {
            const sessionId = String(value || '').trim();
            if (sessionId) sessionUiPendingIds.add(sessionId);
        });
        if (!sessionUiPendingIds.size) {
            const active = document.querySelector('#sessions-list .session-item.active[data-session-id]');
            if (active) sessionUiPendingIds.add(String(active.dataset.sessionId || ''));
        }
    }
    if (sessionUiRefreshTimer != null) clearTimeout(sessionUiRefreshTimer);
    sessionUiRefreshTimer = setTimeout(function () {
        sessionUiRefreshTimer = null;
        const full = sessionUiFullRefreshPending;
        const sessionIds = Array.from(sessionUiPendingIds);
        sessionUiFullRefreshPending = false;
        sessionUiPendingIds.clear();
        void refreshPluginSessionUi(full ? undefined : sessionIds);
    }, 30);
}

function sessionIdsInNode(node) {
    if (!node || node.nodeType !== 1) return [];
    const rows = [];
    if (node.matches && node.matches('.session-item[data-session-id]')) rows.push(node);
    if (node.querySelectorAll) rows.push(...node.querySelectorAll('.session-item[data-session-id]'));
    return rows.map(function (row) { return String(row.dataset.sessionId || ''); }).filter(Boolean);
}

function initPluginSessionUi() {
    const sessionsList = document.getElementById('sessions-list');
    if (sessionsList && !sessionUiObserver && typeof MutationObserver !== 'undefined') {
        sessionUiObserver = new MutationObserver(function (mutations) {
            const added = new Set();
            mutations.forEach(function (mutation) {
                Array.from(mutation.addedNodes || []).forEach(function (node) {
                    sessionIdsInNode(node).forEach(function (sessionId) { added.add(sessionId); });
                });
                Array.from(mutation.removedNodes || []).forEach(function (node) {
                    sessionIdsInNode(node).forEach(function (sessionId) {
                        pluginSessionUiCache.delete(sessionId);
                        sessionUiLatestGeneration.delete(sessionId);
                    });
                });
            });
            if (added.size) schedulePluginSessionUiRefresh(Array.from(added));
        });
        sessionUiObserver.observe(sessionsList, {
            childList: true,
            subtree: true,
        });
    }
    document.addEventListener('myagent:extension-state-changed', schedulePluginSessionUiRefresh);
    schedulePluginSessionUiRefresh({ full: true });
}

export async function initPluginUiSlots(options = {}) {
    const request = typeof options.fetch === 'function'
        ? options.fetch
        : (typeof globalThis.fetch === 'function' ? globalThis.fetch.bind(globalThis) : null);
    if (typeof request !== 'function') return;
    sessionUiRequest = request;
    try {
        const response = await request('/api/extensions', {
            method: 'GET',
            credentials: 'same-origin',
            cache: 'no-store',
            headers: { Accept: 'application/json' },
        });
        if (!response.ok) throw new Error(`Extension discovery failed (${response.status})`);
        const payload = await response.json();
        const contributions = payload && payload.ui_contributions;
        installPluginUiContributions(contributions);
        renderPluginSettingsSections(document.getElementById('plugin-settings-sections'), contributions);
        renderPluginComposerActions(document.getElementById('plugin-composer-actions'), contributions);
        await loadPluginSessionPanelRenderers(contributions);
        await loadPluginChatExtensions(contributions);
        initPluginSessionUi();
    } catch (error) {
        installPluginUiContributions([]);
        renderPluginSettingsSections(document.getElementById('plugin-settings-sections'), []);
        renderPluginComposerActions(document.getElementById('plugin-composer-actions'), []);
        await loadPluginChatExtensions([]);
        console.warn('Plugin UI discovery failed', error);
    } finally {
        document.dispatchEvent(new CustomEvent('myagent:plugin-ui-ready'));
    }
}
