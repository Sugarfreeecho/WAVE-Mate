var permissionModeBusy = false;
var currentPermissionStatus = null;
var newSessionPermissionMode = '';
var LS_NEW_SESSION_PERMISSION_MODE = 'myagent-new-session-permission-mode';
var mcpRegistrationPromptBusy = false;
var mcpRegistrationPrompted = new Set();

var PERMISSION_MODE_ICONS = {
    ask_for_approval: '<path d="M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6z"/>',
    approve_for_me: '<path d="M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6z"/><path d="M9.2 11.8l2 2 3.8-4"/>',
    full_access: '<rect x="4" y="10" width="16" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 7.6-1.6"/><circle cx="12" cy="15" r="1" fill="currentColor"/>',
};

function permissionModeLabel(mode) {
    if (mode === 'approve_for_me') return '替我审批';
    if (mode === 'full_access') return '完全访问权限';
    return '请求批准';
}

function selectedNewSessionPermissionMode() {
    if (newSessionPermissionMode) return newSessionPermissionMode;
    try { return String(localStorage.getItem(LS_NEW_SESSION_PERMISSION_MODE) || ''); }
    catch (e) { return ''; }
}

function setNewSessionPermissionMode(mode) {
    newSessionPermissionMode = String(mode || '');
    try {
        if (newSessionPermissionMode) localStorage.setItem(LS_NEW_SESSION_PERMISSION_MODE, newSessionPermissionMode);
        else localStorage.removeItem(LS_NEW_SESSION_PERMISSION_MODE);
    } catch (e) { /* ignore */ }
}

function commitNewSessionPermissionMode(status) {
    var mode = selectedNewSessionPermissionMode();
    setNewSessionPermissionMode('');
    if (status) renderPermissionMode(status);
    return mode;
}

async function loadNewSessionPermissionStatus() {
    var urls = ['/api/security/permissions'];
    if (typeof sessionStore !== 'undefined' && sessionStore && typeof sessionStore.list === 'function') {
        var existing = sessionStore.list().find(function (session) { return session && session.id; });
        if (existing) urls.push('/sessions/' + encodeURIComponent(existing.id) + '/permissions');
    }
    for (var i = 0; i < urls.length; i += 1) {
        try {
            var response = await fetch(urls[i], { cache: 'no-store' });
            if (!response.ok) continue;
            var data = await response.json();
            if (data && data.ok) return data;
        } catch (e) { /* try the compatibility endpoint */ }
    }
    return {
        ok: true,
        mode: selectedNewSessionPermissionMode() || 'ask_for_approval',
        security_enabled: permissionControlsEnabled(null),
        available_modes: {
            ask_for_approval: true,
            approve_for_me: true,
            full_access: true,
        },
    };
}

function permissionControlsEnabled(status) {
    if (status && status.security_enabled === false) return false;
    var flags = typeof window !== 'undefined' ? window.__MYAGENT_FEATURES__ : null;
    return !(flags && flags.security === false);
}

function syncPermissionControlVisibility(status) {
    var control = document.getElementById('permission-mode-control');
    var enabled = permissionControlsEnabled(status);
    if (control) control.hidden = !enabled;
    var menu = document.getElementById('permission-mode-menu');
    var trigger = document.getElementById('permission-mode-trigger');
    if (!enabled && menu) menu.classList.remove('is-open');
    if (!enabled && trigger) {
        trigger.classList.remove('is-open');
        trigger.setAttribute('aria-expanded', 'false');
    }
    return enabled;
}

function showGlobalWarningBanner(message, options) {
    options = options || {};
    var host = document.querySelector('.chat-stage') || document.querySelector('.main-center') || document.body;
    var notice = host.querySelector('.permission-global-warning-toast[data-global-warning-banner="true"]');
    if (!notice) {
        notice = document.createElement('div');
        notice.className = 'permission-global-warning-toast';
        notice.dataset.globalWarningBanner = 'true';
        notice.setAttribute('role', 'alert');
        notice.setAttribute('aria-live', 'assertive');
        host.appendChild(notice);
    }
    notice.textContent = String(message || '操作失败');
    if (notice._dismissTimer) window.clearTimeout(notice._dismissTimer);
    notice._dismissTimer = window.setTimeout(function () {
        notice.remove();
    }, Math.max(1000, Number(options.durationMs || 9000)));
    return notice;
}

function maybeShowGlobalFullAccessNotice(status) {
    if (!status || status.mode !== 'full_access' || status.security_enabled === false) return;
    var key = 'myagent-full-access-notice:' + String(status.updated_at || 'legacy');
    try {
        if (window.sessionStorage.getItem(key) === '1') return;
        window.sessionStorage.setItem(key, '1');
    } catch (_) {}
    var notice = document.createElement('div');
    notice.className = 'permission-global-warning-toast';
    notice.textContent = '完全访问已开启：普通文件、工作区删除、命令和联网不再逐项询问；高危系统命令仍需确认，Agent 自保红线始终拦截。';
    var host = document.querySelector('.chat-stage') || document.querySelector('.main-center') || document.body;
    host.appendChild(notice);
    window.setTimeout(function () { notice.remove(); }, 9000);
}

function maybeShowEgressDegradedNotice(status) {
    var restriction = status && status.restriction;
    if (!restriction || restriction.enforcement_level !== 'degraded') return;
    var key = 'sugaragent-egress-degraded:' + String(restriction.reason || 'missing-helper');
    try {
        if (window.sessionStorage.getItem(key) === '1') return;
        window.sessionStorage.setItem(key, '1');
    } catch (_) {}
    var notice = document.createElement('div');
    notice.className = 'permission-global-warning-toast';
    notice.textContent = '出站防护处于降级状态：命令仍会按上传/读取规则审批，但当前没有系统级网络隔离。';
    var host = document.querySelector('.chat-stage') || document.querySelector('.main-center') || document.body;
    host.appendChild(notice);
    window.setTimeout(function () { notice.remove(); }, 9000);
}

function maybeShowEgressPartialNotice(status) {
    var restriction = status && status.restriction;
    if (!restriction || restriction.enforcement_level !== 'partial') return;
    var key = 'sugaragent-egress-partial:' + String(restriction.implementation || 'helper');
    try {
        if (window.sessionStorage.getItem(key) === '1') return;
        window.sessionStorage.setItem(key, '1');
    } catch (_) {}
    var notice = document.createElement('div');
    notice.className = 'permission-global-warning-toast';
    notice.textContent = '出站助手已启用：无网络命令会被系统强制断网；获批联网命令当前仍可访问审批目标之外的地址。';
    var host = document.querySelector('.chat-stage') || document.querySelector('.main-center') || document.body;
    host.appendChild(notice);
    window.setTimeout(function () { notice.remove(); }, 9000);
}

function renderPermissionMode(status) {
    currentPermissionStatus = status || null;
    var controlsEnabled = syncPermissionControlVisibility(status);
    maybeShowGlobalFullAccessNotice(status);
    maybeShowEgressDegradedNotice(status);
    maybeShowEgressPartialNotice(status);
    var trigger = document.getElementById('permission-mode-trigger');
    var label = document.getElementById('permission-mode-current');
    var triggerIco = document.getElementById('permission-mode-ico');
    var menu = document.getElementById('permission-mode-menu');
    if (label) label.textContent = permissionModeLabel(status && status.mode);
    if (trigger) trigger.setAttribute('data-mode', String((status && status.mode) || 'ask_for_approval'));
    if (trigger) {
        var fullAccess = !!status && status.mode === 'full_access';
        trigger.classList.toggle('is-global-full-access', fullAccess);
        trigger.title = fullAccess
            ? '完全访问已开启；普通操作和工作区删除免审批，高危系统命令和 Agent 自保红线仍受保护。'
            : '更改权限';
    }
    var settingsStatus = document.getElementById('settings-security-status');
    if (settingsStatus && status) {
        var restrictionLabel = status.restriction && status.restriction.label
            ? ' · ' + status.restriction.label
            : '';
        settingsStatus.textContent = status.mode === 'full_access'
            ? '完全访问已开启：普通操作和工作区删除免审批；高危系统命令仍需确认，Agent 自保红线始终拦截。'
            : permissionModeLabel(status.mode) + '（全局统一，对所有任务生效）' + restrictionLabel;
    }
    if (triggerIco) {
        var mode = status && status.mode;
        triggerIco.innerHTML = PERMISSION_MODE_ICONS[mode] || PERMISSION_MODE_ICONS.ask_for_approval;
    }
    if (trigger) trigger.disabled = !controlsEnabled || permissionModeBusy;
    if (!menu) return;
    var available = (status && status.available_modes) || { ask_for_approval: true };
    Array.from(menu.querySelectorAll('[data-permission-mode]')).forEach(function (button) {
        var mode = button.getAttribute('data-permission-mode');
        button.disabled = permissionModeBusy || available[mode] !== true;
        var active = !!status && status.mode === mode;
        button.classList.toggle('is-active', active);
        button.classList.toggle('is-disabled', button.disabled);
        button.setAttribute('aria-selected', active ? 'true' : 'false');
    });
}

async function refreshPermissionModeSelector(sessionId) {
    var sid = String(sessionId || currentSessionId || '');
    var previousPermissionStatus = currentPermissionStatus;
    if (!sid) {
        if (previousPermissionStatus) {
            var draftStatus = Object.assign({}, previousPermissionStatus);
            draftStatus.mode = selectedNewSessionPermissionMode() || draftStatus.mode;
            renderPermissionMode(draftStatus);
            return;
        }
        try {
            var globalStatus = await loadNewSessionPermissionStatus();
            if (!currentSessionId) {
                globalStatus.mode = selectedNewSessionPermissionMode() || globalStatus.mode;
                renderPermissionMode(globalStatus);
            }
        } catch (error) {
            if (!currentSessionId) renderPermissionMode(null);
        }
        return;
    }
    try {
        var response = await fetch('/sessions/' + encodeURIComponent(sid) + '/permissions', { cache: 'no-store' });
        var data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || ('HTTP ' + response.status));
        if (sid === String(currentSessionId || '')) renderPermissionMode(data);
    } catch (error) {
        // A read failure is not a global mode transition. Restore the last
        // known badge instead of presenting the fallback as a real downgrade.
        renderPermissionMode(previousPermissionStatus);
    }
}

async function selectPermissionMode(mode) {
    if (permissionModeBusy) return;
    if (mode === 'full_access') {
        var accepted = await openUiModal({
            title: '完全访问权限',
            subtitle: '仅在信任 Agent 时才建议开启',
            message: '完全访问开启后，普通文件、工作区内删除、Shell 和网络操作不再逐项征求你的同意；格式化、磁盘写入、关机等高危系统命令仍需一次性人工确认，终止 Agent 或篡改控制器始终拒绝。此设置对所有会话生效，重启后也不会自动关闭，直到你手动切回“请求批准”。是否继续？',
            danger: true,
            confirmText: '确认切换',
            cancelText: '取消',
        });
        if (!accepted) return;
    }
    if (!currentSessionId) {
        setNewSessionPermissionMode(mode);
        renderPermissionMode(Object.assign({}, currentPermissionStatus || {}, { mode: mode }));
        return;
    }
    permissionModeBusy = true;
    renderPermissionMode(currentPermissionStatus);
    try {
        var response = await fetch('/sessions/' + encodeURIComponent(currentSessionId) + '/permissions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode: mode }),
        });
        var data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || ('HTTP ' + response.status));
        renderPermissionMode(data);
    } catch (error) {
        showUiAlert({
            title: '切换权限失败',
            message: String(error && error.message ? error.message : error),
            confirmText: '知道了',
        });
    } finally {
        permissionModeBusy = false;
        renderPermissionMode(currentPermissionStatus);
    }
}

function securityRulesContext() {
    var win = typeof window !== 'undefined' ? window : globalThis;
    return {
        sessionId: String(currentSessionId || ''),
        workspace: String((win && win.__WORK_DIR__) || ''),
    };
}

function securityRuleLabel(rule) {
    var action = String(rule.action || '');
    var pattern = String(rule.pattern || '');
    if (action === 'process.exec') return 'Shell ' + pattern;
    if (action === 'fs.read') return '读取 ' + pattern;
    if (action === 'fs.write') return '写入 ' + pattern;
    if (action === 'fs.delete') return '删除 ' + pattern;
    if (action === 'network.connect') return '网络 ' + pattern;
    if (action === 'web.search') return '联网搜索 ' + pattern;
    if (action === 'mcp.call' || action === 'plugin.call') return (action === 'mcp.call' ? 'MCP ' : '插件 ') + pattern;
    return action + ' ' + pattern;
}

async function refreshWebFetchDomains() {
    var editor = document.getElementById('settings-security-web-fetch-domains');
    var statusEl = document.getElementById('settings-security-web-fetch-status');
    if (!editor) return;
    try {
        var response = await fetch('/api/security/web-fetch-domains', { cache: 'no-store' });
        var data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || ('HTTP ' + response.status));
        editor.value = (Array.isArray(data.domains) ? data.domains : []).join('\n');
        if (statusEl) statusEl.textContent = '已加载 ' + editor.value.split('\n').filter(Boolean).length + ' 个自定义域名（内置清单始终生效）。';
    } catch (error) {
        if (statusEl) statusEl.textContent = '读取预批准域名失败：' + String(error && error.message ? error.message : error);
    }
}

function extensionTrustLabel(item) {
    var kind = item.kind === 'mcp' ? 'MCP' : '插件';
    return kind + ' / ' + String(item.name || item.extension_id || 'unknown');
}

function mcpRegistrationMessage(item) {
    var capabilities = item && item.capabilities ? item.capabilities : {};
    var lines = [
        '连接前需要确认一次当前 MCP 配置。确认仅允许启动或连接服务器并发现工具；每次工具调用仍按当前权限模式审批。',
        '',
        '类型：' + String(item.runtime || capabilities.transport || 'unknown'),
        '命令或地址：' + String(item.source || '未提供'),
    ];
    if (capabilities.working_directory) lines.push('工作目录：' + String(capabilities.working_directory));
    var envNames = Array.isArray(capabilities.configured_environment)
        ? capabilities.configured_environment
        : [];
    if (envNames.length) lines.push('配置环境变量：' + envNames.join(', '));
    lines.push('', '该服务器以当前操作系统用户权限运行，不是硬隔离。');
    return lines.join('\n');
}

async function submitMcpRegistration(item, approved) {
    var response = await fetch(
        '/api/security/mcp/' + encodeURIComponent(item.extension_id) + '/registration',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                approved: !!approved,
                config_digest: String(item.config_digest || ''),
            }),
        }
    );
    var data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || ('HTTP ' + response.status));
    return data.registration;
}

async function confirmMcpRegistration(item) {
    var accepted = await openUiModal({
        title: '注册 MCP 服务器',
        subtitle: extensionTrustLabel(item),
        message: mcpRegistrationMessage(item),
        danger: true,
        confirmText: '确认并连接',
        cancelText: '暂不连接',
    });
    if (!accepted) return false;
    await submitMcpRegistration(item, true);
    return true;
}

async function promptPendingMcpRegistrations(rows) {
    if (mcpRegistrationPromptBusy) return;
    var pending = (Array.isArray(rows) ? rows : []).filter(function (item) {
        var digestKey = String(item.extension_id || '') + ':' + String(item.config_digest || '');
        return item.kind === 'mcp'
            && item.registration_status === 'pending'
            && !mcpRegistrationPrompted.has(digestKey);
    });
    if (!pending.length) return;
    mcpRegistrationPromptBusy = true;
    var changed = false;
    try {
        for (var i = 0; i < pending.length; i += 1) {
            var item = pending[i];
            var digestKey = String(item.extension_id || '') + ':' + String(item.config_digest || '');
            mcpRegistrationPrompted.add(digestKey);
            try {
                changed = (await confirmMcpRegistration(item)) || changed;
            } catch (error) {
                showGlobalWarningBanner(
                    'MCP 注册失败：' + String(error && error.message ? error.message : error)
                );
            }
        }
    } finally {
        mcpRegistrationPromptBusy = false;
    }
    if (changed) await refreshSecurityExtensions();
}

async function setExtensionTrust(item, trust) {
    var statusEl = document.getElementById('settings-security-extensions-status');
    if (item.kind === 'mcp' && trust) {
        try {
            var confirmed = await confirmMcpRegistration(item);
            if (statusEl && confirmed) statusEl.textContent = 'MCP 已注册并连接；工具调用继续正常审批。';
            if (confirmed) await refreshSecurityExtensions();
        } catch (error) {
            var mcpError = String(error && error.message ? error.message : error);
            if (statusEl) statusEl.textContent = 'MCP 注册失败：' + mcpError;
            showGlobalWarningBanner('MCP 注册失败：' + mcpError);
        }
        return;
    }
    if (trust) {
        var accepted = await openUiModal({
            title: '注册可执行扩展',
            subtitle: extensionTrustLabel(item),
            message: '该扩展将以当前操作系统用户权限运行。能力声明只用于审批分类，不能阻止扩展代码读取文件或联网。确认注册当前内容摘要？',
            danger: true,
            confirmText: '确认注册',
            cancelText: '取消',
        });
        if (!accepted) return;
    }
    try {
        var base = '/api/security/extensions/' + encodeURIComponent(item.kind) + '/' + encodeURIComponent(item.extension_id) + '/trust';
        var response = await fetch(base, { method: trust ? 'POST' : 'DELETE' });
        var data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || ('HTTP ' + response.status));
        if (statusEl) {
            statusEl.textContent = trust
                ? '扩展已注册；当前摘要可以启动。'
                : (item.kind === 'mcp'
                    ? 'MCP 注册已撤销，运行中的服务器已停止。'
                    : '扩展注册已撤销，运行中的 worker 已停止。');
        }
        await refreshSecurityExtensions();
    } catch (error) {
        var extensionError = String(error && error.message ? error.message : error);
        if (statusEl) statusEl.textContent = '更新扩展注册失败：' + extensionError;
        showGlobalWarningBanner('扩展注册失败：' + extensionError);
    }
}

async function refreshSecurityExtensions() {
    var listEl = document.getElementById('settings-security-extensions-list');
    var statusEl = document.getElementById('settings-security-extensions-status');
    if (!listEl) return;
    listEl.textContent = '正在读取…';
    try {
        var response = await fetch('/api/security/extensions', { cache: 'no-store' });
        var data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || ('HTTP ' + response.status));
        listEl.textContent = '';
        var rows = Array.isArray(data.extensions) ? data.extensions : [];
        if (!rows.length) {
            listEl.textContent = '没有已安装或已配置的可执行扩展。';
            return;
        }
        rows.forEach(function (item) {
            var row = document.createElement('div');
            row.className = 'settings-security-rule-row';
            var badge = document.createElement('span');
            var registrationStatus = String(item.registration_status || '');
            badge.className = item.trusted ? 'settings-security-rule-allow' : 'settings-security-rule-ask';
            badge.textContent = registrationStatus === 'registered'
                ? '已注册'
                : (registrationStatus === 'rejected' ? '已拒绝' : '待确认');
            var label = document.createElement('span');
            label.className = 'settings-security-rule-label';
            label.textContent = extensionTrustLabel(item) + ' · ' + String(item.runtime || 'runtime');
            label.title = String(item.source || '') + '\n摘要：' + String(item.content_digest || '');
            var action = document.createElement('button');
            action.type = 'button';
            action.className = 'settings-security-rule-delete';
            action.textContent = item.trusted
                ? '撤销'
                : '确认注册';
            action.addEventListener('click', function () { void setExtensionTrust(item, !item.trusted); });
            row.appendChild(badge);
            row.appendChild(label);
            row.appendChild(action);
            listEl.appendChild(row);
        });
        if (statusEl) statusEl.textContent = '';
        void promptPendingMcpRegistrations(rows);
    } catch (error) {
        listEl.textContent = '';
        if (statusEl) statusEl.textContent = '读取扩展注册失败：' + String(error && error.message ? error.message : error);
    }
}

async function saveWebFetchDomains() {
    var editor = document.getElementById('settings-security-web-fetch-domains');
    var statusEl = document.getElementById('settings-security-web-fetch-status');
    if (!editor) return;
    var saveBtn = document.getElementById('settings-security-web-fetch-save');
    if (saveBtn) saveBtn.disabled = true;
    try {
        var domains = editor.value.split(/\r?\n/).map(function (line) { return line.trim(); }).filter(Boolean);
        var response = await fetch('/api/security/web-fetch-domains', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ domains: domains }),
        });
        var data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || ('HTTP ' + response.status));
        editor.value = (Array.isArray(data.domains) ? data.domains : []).join('\n');
        if (statusEl) statusEl.textContent = '已保存 ' + editor.value.split('\n').filter(Boolean).length + ' 个自定义域名，新会话立即生效。';
    } catch (error) {
        if (statusEl) statusEl.textContent = '保存失败：' + String(error && error.message ? error.message : error);
    } finally {
        if (saveBtn) saveBtn.disabled = false;
    }
}

async function refreshSecurityRules() {
    var listEl = document.getElementById('settings-security-rules-list');
    var statusEl = document.getElementById('settings-security-rules-status');
    if (!listEl) return;
    listEl.textContent = '正在读取…';
    try {
        var ctx = securityRulesContext();
        var query = new URLSearchParams();
        if (ctx.sessionId) query.set('session_id', ctx.sessionId);
        if (ctx.workspace) query.set('workspace', ctx.workspace);
        var response = await fetch('/api/security/rules?' + query.toString(), { cache: 'no-store' });
        var data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || ('HTTP ' + response.status));
        listEl.textContent = '';
        var rules = Array.isArray(data.rules) ? data.rules : [];
        if (rules.length === 0) {
            var empty = document.createElement('div');
            empty.className = 'settings-feature-status';
            empty.textContent = '暂无长期规则。审批时选择“始终允许”可自动添加可生成的长期规则。';
            listEl.appendChild(empty);
            return;
        }
        rules.forEach(function (rule) {
            var row = document.createElement('div');
            row.className = 'settings-security-rule-row';
            var badge = document.createElement('span');
            badge.className = 'settings-security-rule-' + String(rule.behavior || 'allow');
            var behaviorText = rule.behavior === 'deny' ? '拒绝' : (rule.behavior === 'ask' ? '必问' : '允许');
            badge.textContent = behaviorText + (rule.source === 'session' ? '·本会话' : (rule.source === 'project' ? '·项目' : ''));
            var label = document.createElement('span');
            label.className = 'settings-security-rule-label';
            label.title = String(rule.pattern || '');
            label.textContent = securityRuleLabel(rule);
            var del = document.createElement('button');
            del.type = 'button';
            del.className = 'settings-security-rule-delete';
            del.textContent = '删除';
            del.addEventListener('click', function () { void deleteSecurityRule(rule); });
            row.appendChild(badge);
            row.appendChild(label);
            row.appendChild(del);
            listEl.appendChild(row);
        });
        if (statusEl) statusEl.textContent = '';
    } catch (error) {
        listEl.textContent = '';
        if (statusEl) statusEl.textContent = '读取规则失败：' + String(error && error.message ? error.message : error);
    }
}

async function addSecurityRule() {
    var statusEl = document.getElementById('settings-security-rules-status');
    var actionEl = document.getElementById('settings-security-rule-action');
    var behaviorEl = document.getElementById('settings-security-rule-behavior');
    var patternEl = document.getElementById('settings-security-rule-pattern');
    if (!actionEl || !behaviorEl || !patternEl) return;
    var action = String(actionEl.value || 'process.exec');
    var behavior = String(behaviorEl.value || 'allow');
    var pattern = String(patternEl.value || '').trim();
    if (!pattern) {
        if (statusEl) statusEl.textContent = '请输入规则内容。';
        return;
    }
    if (statusEl) statusEl.textContent = '正在添加…';
    try {
        var ctx = securityRulesContext();
        var body = { behavior: behavior, action: action, pattern: pattern, source: 'user', session_id: ctx.sessionId, workspace: ctx.workspace };
        var response = await fetch('/api/security/rules', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        var data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || ('HTTP ' + response.status));
        patternEl.value = '';
        if (statusEl) statusEl.textContent = '规则已添加。';
        await refreshSecurityRules();
    } catch (error) {
        if (statusEl) statusEl.textContent = '添加失败：' + String(error && error.message ? error.message : error);
    }
}

async function deleteSecurityRule(rule) {
    var statusEl = document.getElementById('settings-security-rules-status');
    try {
        var response = await fetch('/api/security/rules/' + encodeURIComponent(String(rule.id)), { method: 'DELETE' });
        var data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || ('HTTP ' + response.status));
        if (statusEl) statusEl.textContent = '规则已删除。';
        await refreshSecurityRules();
    } catch (error) {
        if (statusEl) statusEl.textContent = '删除失败：' + String(error && error.message ? error.message : error);
    }
}

async function clearSessionSecurityRules() {
    var statusEl = document.getElementById('settings-security-rules-status');
    var ctx = securityRulesContext();
    if (!ctx.sessionId) {
        if (statusEl) statusEl.textContent = '未选择会话。';
        return;
    }
    var accepted = await openUiModal({
        title: '清除本会话规则',
        message: '清除当前会话的所有权限规则？用户级“始终允许”规则不受影响。',
        danger: true,
        confirmText: '确认清除',
        cancelText: '取消',
    });
    if (!accepted) return;
    try {
        var response = await fetch('/api/security/rules?session_id=' + encodeURIComponent(ctx.sessionId), { method: 'DELETE' });
        var data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || ('HTTP ' + response.status));
        if (statusEl) statusEl.textContent = '已清除本会话规则（' + String(data.deleted || 0) + ' 条）。';
        await refreshSecurityRules();
    } catch (error) {
        if (statusEl) statusEl.textContent = '清除失败：' + String(error && error.message ? error.message : error);
    }
}

function initPermissionControls() {
    syncPermissionControlVisibility(currentPermissionStatus);
    var trigger = document.getElementById('permission-mode-trigger');
    var menu = document.getElementById('permission-mode-menu');
    if (trigger && menu) {
        if (typeof bindUiHoverTip === 'function') bindUiHoverTip(trigger);
        trigger.addEventListener('click', function () {
            menu.classList.toggle('is-open');
            trigger.classList.toggle('is-open', menu.classList.contains('is-open'));
            trigger.setAttribute('aria-expanded', menu.classList.contains('is-open') ? 'true' : 'false');
        });
        Array.from(menu.querySelectorAll('[data-permission-mode]')).forEach(function (button) {
            button.addEventListener('click', function () {
                menu.classList.remove('is-open');
                trigger.classList.remove('is-open');
                trigger.setAttribute('aria-expanded', 'false');
                void selectPermissionMode(button.getAttribute('data-permission-mode'));
            });
        });
        document.addEventListener('click', function (event) {
            if (!menu.contains(event.target) && !trigger.contains(event.target)) {
                menu.classList.remove('is-open');
                trigger.classList.remove('is-open');
                trigger.setAttribute('aria-expanded', 'false');
            }
        });
    }
    var rulesRefresh = document.getElementById('settings-security-rules-refresh');
    if (rulesRefresh) rulesRefresh.addEventListener('click', function () { void refreshSecurityRules(); });
    var rulesClear = document.getElementById('settings-security-rules-clear-session');
    if (rulesClear) rulesClear.addEventListener('click', function () { void clearSessionSecurityRules(); });
    var rulesAdd = document.getElementById('settings-security-rule-add');
    if (rulesAdd) rulesAdd.addEventListener('click', function () { void addSecurityRule(); });
    var webFetchSave = document.getElementById('settings-security-web-fetch-save');
    if (webFetchSave) webFetchSave.addEventListener('click', function () { void saveWebFetchDomains(); });
    var webFetchReload = document.getElementById('settings-security-web-fetch-reload');
    if (webFetchReload) webFetchReload.addEventListener('click', function () { void refreshWebFetchDomains(); });
    var extensionsRefresh = document.getElementById('settings-security-extensions-refresh');
    if (extensionsRefresh) extensionsRefresh.addEventListener('click', function () { void refreshSecurityExtensions(); });
    void refreshSecurityRules();
    void refreshSecurityExtensions();
    void refreshWebFetchDomains();
    void refreshPermissionModeSelector(currentSessionId);
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initPermissionControls);
} else {
    initPermissionControls();
}
