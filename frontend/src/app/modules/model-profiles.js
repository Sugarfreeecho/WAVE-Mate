let modelProfilesCache = null;
const modelProfilesRefreshPromises = Object.create(null);
const modelProfileBusyBySession = Object.create(null);
const modelProfileIdBySession = Object.create(null);
const modelProfileToggleBusy = Object.create(null);
let modelProfileSelectionEpoch = 0;
let activeModelProfileId = '';
const LS_NEW_SESSION_MODEL_PROFILE = 'myagent-new-session-model-profile';

function newSessionModelProfileId() {
    var memoryValue = String(modelProfileIdBySession[NEW_SESSION_DRAFT_KEY] || '');
    if (memoryValue) return memoryValue;
    try { return String(localStorage.getItem(LS_NEW_SESSION_MODEL_PROFILE) || ''); }
    catch (e) { return ''; }
}

function setNewSessionModelProfileId(profileId) {
    var value = String(profileId || '');
    if (value) modelProfileIdBySession[NEW_SESSION_DRAFT_KEY] = value;
    else delete modelProfileIdBySession[NEW_SESSION_DRAFT_KEY];
    try {
        if (value) localStorage.setItem(LS_NEW_SESSION_MODEL_PROFILE, value);
        else localStorage.removeItem(LS_NEW_SESSION_MODEL_PROFILE);
    } catch (e) { /* ignore */ }
}

function commitNewSessionModelProfile(sessionId) {
    var value = newSessionModelProfileId();
    if (value && sessionId) modelProfileIdBySession[String(sessionId)] = value;
    setNewSessionModelProfileId('');
    return value;
}

function h(str) {
    return String(str == null ? '' : str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function modelToggleIcon(action) {
    if (action === 'enable') {
        return '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2v10"/><path d="M18.4 6.6a9 9 0 1 1-12.8 0"/></svg>';
    }
    return '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="m5.5 5.5 13 13"/></svg>';
}

function modelToggleHtml(enabled) {
    return '<span class="composer-model-toggle-ico" aria-hidden="true">' + modelToggleIcon(enabled ? 'disable' : 'enable') + '</span>';
}

function profileLabel(profile) {
    if (!profile) return '默认方案';
    return String(profile.name || profile.model || '未命名方案');
}

function formatContextWindow(value) {
    var n = Number(value);
    if (!Number.isFinite(n) || n <= 0) return String(value == null ? '' : value);
    if (n >= 1000000) {
        var m = n / 1000000;
        return String(Math.round(m * 10) / 10).replace(/\.0$/, '') + 'M';
    }
    if (n >= 1000) return String(Math.round(n / 1000)) + 'k';
    return String(Math.round(n));
}

function canonicalLlmType(raw) {
    var k = String(raw || '').trim().toLowerCase();
    if (k === 'openai-responses' || k === 'responses' || k === '@ai-sdk/openai') return 'openai-responses';
    if (k === 'openai' || k === 'openai-compatible' || k === 'openai_compatible' || k === 'compatible' || k === 'chat-completions' || k === 'local' || k === '@ai-sdk/openai-compatible') return 'openai';
    if (k === 'anthropic' || k === 'claude' || k === 'messages' || k === '@ai-sdk/anthropic') return 'anthropic';
    if (k === 'auto' || k === '') return 'auto';
    return k;
}
function isResponsesProfile(profile) {
    return canonicalLlmType(profile && profile.llm_type) === 'openai-responses';
}
function isChatProfile(profile) {
    return canonicalLlmType(profile && profile.llm_type) === 'openai';
}
function profileEffortValue(profile) {
    var p = profile || {};
    var thinkingDisabled = String(p.thinking_mode || '').toLowerCase() === 'disabled';
    var usesResponses = isResponsesProfile(p);
    return String(p.reasoning_effort || (usesResponses ? 'auto' : (thinkingDisabled ? 'none' : 'high'))).toLowerCase();
}

function profileMeta(profile) {
    if (!profile) return '';
    var model = profile.model || '';
    var effort = profileEffortValue(profile);
    var ctx = profile.context_window ? formatContextWindow(profile.context_window) + ' ctx' : '';
    var out = profile.max_output_tokens ? formatContextWindow(profile.max_output_tokens) + ' out' : '';
    return [model, effort, ctx, out].filter(Boolean).join(' · ');
}

function modelProfileCapabilityDescription(profile) {
    var p = profile || {};
    var language = (document.documentElement && document.documentElement.getAttribute('data-language'))
        || localStorage.getItem('myagent-language')
        || 'zh-CN';
    if (language === 'en' && p.capability_description_en) return String(p.capability_description_en);
    return String(p.capability_description || '');
}

function modelProfileUiLanguage() {
    return (document.documentElement && document.documentElement.getAttribute('data-language'))
        || localStorage.getItem('myagent-language')
        || 'zh-CN';
}

function modelProfileHoverDetail(profile) {
    var p = profile || {};
    var english = modelProfileUiLanguage() === 'en';
    var lines = [
        (english ? 'Model profile: ' : '模型配置：') + profileLabel(p),
        'model_porfile_id: ' + String(p.id || (english ? 'Not set' : '未设置')),
        (english ? 'Model ID: ' : '模型 ID：') + String(p.model || (english ? 'Not set' : '未设置')),
        (english ? 'API type: ' : '接口类型：') + String(p.llm_type || 'openai'),
        (english ? 'Context window: ' : '上下文窗口：') + (p.context_window ? formatContextWindow(p.context_window) : (english ? 'Not set' : '未设置')),
        (english ? 'Max output: ' : '最大输出：') + String(p.max_output_tokens || (english ? 'Not set' : '未设置')),
        (english ? 'Thinking effort: ' : '思考强度：') + profileEffortValue(p),
    ];
    var capability = modelProfileCapabilityDescription(p);
    if (capability) lines.push((english ? 'Capability: ' : '能力：') + capability);
    lines.push((english ? 'Status: ' : '状态：') + (
        p.enabled === false
            ? (english ? 'Disabled' : '已禁用')
            : (p.usable === false ? (english ? 'Not ready' : '未就绪') : (english ? 'Available' : '可用'))
    ));
    return lines.join('\n');
}

function els() {
    return {
        control: document.getElementById('model-profile-control'),
        trigger: document.getElementById('model-profile-trigger'),
        current: document.getElementById('model-profile-current'),
        menu: document.getElementById('model-profile-menu'),
    };
}

async function loadModelProfilesForSwitcher() {
    const response = await fetch('/api/model_profiles', { credentials: 'same-origin' });
    const data = await response.json();
    if (!data || !data.ok) throw new Error((data && data.error) || '模型配置加载失败');
    modelProfilesCache = data;
    return data;
}

function storedProfiles() {
    if (!modelProfilesCache) return [];
    return (modelProfilesCache.profiles || []).filter((profile) => profile);
}

function allProfiles() {
    return storedProfiles().filter((profile) => profile.enabled !== false && profile.usable !== false);
}

function activeProfile() {
    var list = allProfiles();
    for (var i = 0; i < list.length; i += 1) {
        if (String(list[i].id || '') === String(activeModelProfileId || '')) return list[i];
    }
    return list[0] || null;
}

function activeProfileContextWindow() {
    var profile = activeProfile();
    var n = profile && profile.context_window != null ? Number(profile.context_window) : 0;
    return Number.isFinite(n) && n > 0 ? Math.floor(n) : null;
}

function closeModelMenu() {
    var e = els();
    if (e.menu) e.menu.classList.remove('is-open');
    if (e.trigger) {
        e.trigger.classList.remove('is-open');
        e.trigger.setAttribute('aria-expanded', 'false');
    }
}

function openModelMenu() {
    var e = els();
    if (!e.menu || !e.trigger) return;
    constrainModelMenuToTitlebar();
    e.menu.classList.add('is-open');
    e.trigger.classList.add('is-open');
    e.trigger.setAttribute('aria-expanded', 'true');
}

function constrainModelMenuToTitlebar() {
    var e = els();
    if (!e.menu || !e.trigger) return;
    var titlebar = document.querySelector('.titlebar');
    var titlebarBottom = titlebar ? titlebar.getBoundingClientRect().bottom : 44;
    var triggerTop = e.trigger.getBoundingClientRect().top;
    var available = Math.max(1, Math.floor(triggerTop - titlebarBottom - 8));
    var rootSize = parseFloat(getComputedStyle(document.documentElement).fontSize || '16') || 16;
    var cap = Math.floor(44 * rootSize);
    e.menu.style.setProperty('--composer-popover-max-height', Math.min(cap, available) + 'px');
}

function renderModelProfileControl() {
    var e = els();
    if (!e.trigger || !e.current || !e.menu) return;
    var active = activeProfile();
    e.current.textContent = active ? profileLabel(active) : '没有启用的模型配置';
    e.trigger.removeAttribute('title');
    e.trigger.removeAttribute('data-ui-tip');
    var profiles = storedProfiles();
    if (!profiles.length) {
        e.menu.innerHTML = '<button type="button" class="composer-model-option" disabled><span class="composer-model-option-name">没有可用模型配置</span></button>';
        return;
    }
    var html = '';
    for (var i = 0; i < profiles.length; i += 1) {
        var p = profiles[i] || {};
        var id = String(p.id || '');
        var enabled = p.enabled !== false;
        var activeCls = id === String(activeModelProfileId || '') ? ' is-active' : '';
        html += '<div class="composer-model-option-row' + (enabled ? '' : ' is-disabled') + '" data-ui-tip="' + h(modelProfileHoverDetail(p)) + '">'
            + '<button type="button" class="composer-model-option' + activeCls + '" role="option" data-profile-id="' + h(id) + '"' + (enabled ? '' : ' disabled') + '>'
            + '<span class="composer-model-option-name">' + h(profileLabel(p)) + '</span>'
            + '<span class="composer-model-option-meta">' + h(profileMeta(p)) + '</span>'
            + '</button>'
            + '<button type="button" class="composer-model-toggle" data-toggle-profile-id="' + h(id) + '" data-enabled="' + (enabled ? 'true' : 'false') + '" data-ui-tip="' + (enabled ? '禁用' : '启用') + '" aria-label="' + (enabled ? '禁用' : '启用') + '">' + modelToggleHtml(enabled) + '</button>'
            + '</div>';
    }
    e.menu.innerHTML = html;
    if (typeof initUiHoverTips === 'function') initUiHoverTips(e.menu);
    e.menu.querySelectorAll('[data-profile-id]').forEach((btn) => {
        btn.addEventListener('click', () => {
            setCurrentSessionModelProfile(btn.getAttribute('data-profile-id') || '');
            closeModelMenu();
        });
    });
    e.menu.querySelectorAll('[data-toggle-profile-id]').forEach((btn) => {
        btn.addEventListener('click', () => {
            var enabled = btn.getAttribute('data-enabled') !== 'true';
            setModelProfileEnabled(btn.getAttribute('data-toggle-profile-id') || '', enabled);
        });
    });
}

async function setModelProfileEnabled(profileId, enabled) {
    const id = String(profileId || '');
    const sid = String(currentSessionId || '');
    if (!id || modelProfileToggleBusy[id]) return;
    modelProfileToggleBusy[id] = true;
    try {
        var response = await fetch('/api/model_profiles/' + encodeURIComponent(id) + '/enabled', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ enabled: enabled === true }),
        });
        var data = await response.json();
        if (!data || !data.ok) throw new Error((data && data.error) || '模型配置启停失败');
        await refreshModelProfileSelector(sid, { silent: true });
        openModelMenu();
    } catch (err) {
        if (typeof appendLogVisible === 'function') appendLogVisible('模型配置启停失败: ' + String(err.message || err), 'error-log');
    } finally {
        delete modelProfileToggleBusy[id];
    }
}

function renderModelProfileLoadingMenu() {
    var e = els();
    if (!e.menu) return;
    e.menu.innerHTML = '<button type="button" class="composer-model-option" disabled>'
        + '<span class="composer-model-option-name">正在加载模型配置</span>'
        + '<span class="composer-model-option-meta">请稍候</span>'
        + '</button>';
}

async function refreshModelProfileSelector(sessionId, opts) {
    const sid = String(sessionId || currentSessionId || '');
    const requestEpoch = ++modelProfileSelectionEpoch;
    var e = els();
    opts = opts || {};
    if (!e.control) return;
    if (!opts.silent && e.current) e.current.textContent = '正在加载模型配置';
    try {
        await loadModelProfilesForSwitcher();
        var selectedProfileId = (sid ? modelProfileIdBySession[sid] : newSessionModelProfileId())
            || modelProfilesCache.new_session_default_profile_id
            || '';
        if (sid) {
            var r = await fetch('/sessions/' + encodeURIComponent(sid) + '/model_profile', { credentials: 'same-origin' });
            var j = await r.json();
            if (j && j.ok && j.profile_id) {
                selectedProfileId = String(j.profile_id);
                modelProfileIdBySession[sid] = selectedProfileId;
            }
        }
        if (sid !== String(currentSessionId || '') || requestEpoch !== modelProfileSelectionEpoch) return;
        activeModelProfileId = selectedProfileId;
        renderModelProfileControl();
    } catch (err) {
        if (sid !== String(currentSessionId || '') || requestEpoch !== modelProfileSelectionEpoch) return;
        if (e.current) e.current.textContent = '模型配置加载失败';
        if (e.menu) e.menu.innerHTML = '<button type="button" class="composer-model-option" disabled><span class="composer-model-option-name">模型配置加载失败</span><span class="composer-model-option-meta">' + h(err.message || err) + '</span></button>';
    }
}

function refreshModelProfileSelectorInBackground(sessionId, opts) {
    const sid = String(sessionId || currentSessionId || '');
    const existing = modelProfilesRefreshPromises[sid];
    if (existing && existing.epoch === modelProfileSelectionEpoch) return existing.promise;
    const promise = refreshModelProfileSelector(sid, opts)
        .catch(function (err) {
            console.error('refresh model profiles failed:', err);
        })
        .finally(function () {
            if (modelProfilesRefreshPromises[sid] === entry) {
                delete modelProfilesRefreshPromises[sid];
            }
        });
    const entry = { promise: promise, epoch: modelProfileSelectionEpoch };
    modelProfilesRefreshPromises[sid] = entry;
    return promise;
}

async function setCurrentSessionModelProfile(profileId) {
    const sid = String(currentSessionId || '');
    const selectedProfileId = String(profileId || '');
    if (!selectedProfileId) return;
    if (!sid) {
        setNewSessionModelProfileId(selectedProfileId);
        modelProfileSelectionEpoch += 1;
        activeModelProfileId = selectedProfileId;
        renderModelProfileControl();
        return;
    }
    if (modelProfileBusyBySession[sid]) return;
    try {
        var __oldId = String(activeModelProfileId || modelProfileIdBySession[sid] || '');
        var __newId = String(selectedProfileId || '');
        if (__oldId && __newId && __oldId !== __newId) {
            var __profiles = (modelProfilesCache && modelProfilesCache.profiles) ? modelProfilesCache.profiles : storedProfiles();
            var __oldProfile = null, __newProfile = null;
            for (var __i = 0; __i < __profiles.length; __i++) {
                var __pp = __profiles[__i] || {};
                if (String(__pp.id || '') === __oldId) __oldProfile = __pp;
                if (String(__pp.id || '') === __newId) __newProfile = __pp;
            }
            if (__oldProfile && __newProfile) {
                var __oldType = canonicalLlmType(__oldProfile.llm_type);
                var __newType = canonicalLlmType(__newProfile.llm_type);
                if (__oldType !== __newType) {
                    var __lang = (document.documentElement && document.documentElement.getAttribute('data-language')) || localStorage.getItem('myagent-language') || 'zh-CN';
                    var __en = __lang === 'en';
                    var __oldLabel = String(__oldProfile.name || __oldProfile.model || __oldId);
                    var __newLabel = String(__newProfile.name || __newProfile.model || __newId);
                    var __title = __en ? 'Model type mismatch' : '\u6a21\u578b\u7c7b\u578b\u4e0d\u4e00\u81f4';
                    var __msg = __en
                        ? 'Model type mismatch, risk of history/tool-call incompatibility. Current "' + __oldLabel + '" (' + __oldType + ') -> target "' + __newLabel + '" (' + __newType + ') may cause truncated history, tool_call_id mismatch and errors. Continue?'
                        : '\u6a21\u578b\u7c7b\u578b\u4e0d\u4e00\u81f4\uff0c\u6709\u5386\u53f2\u4e0e\u5de5\u5177\u8c03\u7528\u4e0d\u517c\u5bb9\u98ce\u9669\u3002\u5f53\u524d\u201c' + __oldLabel + '\u201d\uff08' + __oldType + '\uff09\u2192\u76ee\u6807\u201c' + __newLabel + '\u201d\uff08' + __newType + '\uff09\uff0c\u8de8\u7c7b\u578b\u5207\u6362\u53ef\u80fd\u5bfc\u81f4\u5386\u53f2\u622a\u65ad\u3001tool_call_id\u9519\u4f4d\u800c\u62a5\u9519\uff0c\u662f\u5426\u7ee7\u7eed\u5207\u6362\uff1f';
                    var __confirmed = false;
                    if (typeof openUiModal === 'function') {
                        __confirmed = await openUiModal({
                            title: __title,
                            message: __msg,
                            confirmText: __en ? 'Continue' : '\u7ee7\u7eed\u5207\u6362',
                            cancelText: __en ? 'Cancel' : '\u53d6\u6d88',
                            danger: true,
                            showCancel: true
                        });
                    } else if (typeof window !== 'undefined' && typeof window.confirm === 'function') {
                        __confirmed = window.confirm(__title + '\n\n' + __msg);
                    }
                    if (!__confirmed) return;
                }
            }
        }
    } catch (__confirmErr) { }
    modelProfileBusyBySession[sid] = true;
    try {
        var response = await fetch('/sessions/' + encodeURIComponent(sid) + '/model_profile', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ profile_id: selectedProfileId }),
        });
        var data = await response.json();
        if (!data || !data.ok) throw new Error((data && data.error) || '切换失败');
        modelProfileIdBySession[sid] = selectedProfileId;
        if (sid !== String(currentSessionId || '')) return;
        modelProfileSelectionEpoch += 1;
        activeModelProfileId = selectedProfileId;
        renderModelProfileControl();
        var cachedTokens = selectContextTokens(sid);
        var nextThreshold = activeProfileContextWindow();
        if (cachedTokens && cachedTokens.estimated != null) {
            recordContextTokens(
                sid,
                cachedTokens.estimated,
                nextThreshold != null ? nextThreshold : cachedTokens.threshold
            );
        } else {
            scheduleContextTokensAfterPaint(sid);
        }
    } catch (err) {
        if (sid === String(currentSessionId || '')) {
            appendLogVisible('模型配置切换失败: ' + String(err.message || err), 'error-log');
            await refreshModelProfileSelector(sid);
        }
    } finally {
        delete modelProfileBusyBySession[sid];
    }
}

function initModelProfileSwitcher() {
    var e = els();
    if (!e.control || !e.trigger || !e.menu) return;
    e.trigger.addEventListener('click', async () => {
        var willOpen = !e.menu.classList.contains('is-open');
        if (!willOpen) {
            closeModelMenu();
            return;
        }
        if (modelProfilesCache) renderModelProfileControl();
        else renderModelProfileLoadingMenu();
        openModelMenu();
        refreshModelProfileSelectorInBackground(currentSessionId, { silent: true });
    });
    document.addEventListener('click', (ev) => {
        if (!e.control.contains(ev.target)) closeModelMenu();
    });
    document.addEventListener('keydown', (ev) => {
        if (ev.key === 'Escape') closeModelMenu();
    });
    window.addEventListener('resize', () => {
        var fresh = els();
        if (fresh.menu && fresh.menu.classList.contains('is-open')) constrainModelMenuToTitlebar();
    });
    refreshModelProfileSelectorInBackground(currentSessionId);
}

initModelProfileSwitcher();
document.addEventListener('myagent:language-change', function () {
    if (modelProfilesCache) renderModelProfileControl();
});
window.refreshModelProfileSelector = refreshModelProfileSelector;
window.refreshModelProfileSelectorInBackground = refreshModelProfileSelectorInBackground;
window.loadModelProfilesForSwitcher = loadModelProfilesForSwitcher;
