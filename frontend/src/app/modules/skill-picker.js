let skillPickerCache = null;
let skillPickerRefreshPromise = null;
let selectedSkillNames = [];
let skillPickerActiveTab = 'skills';
let mcpToolsCache = null;
let mcpServersCache = null;
let mcpToolsRefreshPromise = null;
let mcpToolsLoading = false;
let mcpToolsError = null;
let extensionsCache = null;
let extensionsRefreshPromise = null;
let extensionsLoading = false;
let extensionsError = null;
const skillPickerToggleBusy = Object.create(null);
const mcpToolToggleBusy = Object.create(null);
const mcpServerRegisterBusy = Object.create(null);
const pluginToggleBusy = Object.create(null);
const skillPickerCollapsedGroups = {
    mcp: Object.create(null),
    plugins: Object.create(null),
};
const LS_SKILL_DRAFT_PREFIX = 'myagent-skill-draft:';

function skillPickerEls() {
    return {
        row: document.querySelector('.composer-row'),
        button: document.getElementById('skill-picker-btn'),
        popover: document.getElementById('skill-picker-popover'),
    };
}

function skillPickerEscape(str) {
    return String(str == null ? '' : str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function skillPickerHoverDetail(skill) {
    var name = String(skill && skill.name || '未命名 Skill');
    var description = String(skill && skill.description || '').trim() || '暂无描述';
    var status = skill && skill.enabled !== false ? '已启用' : '已禁用';
    return ['Skill：' + name, '描述：' + description, '状态：' + status].join('\n');
}

function selectedSkillSet() {
    var out = {};
    selectedSkillNames.forEach(function (name) { out[String(name)] = true; });
    return out;
}

function reconcileSelectedSkillsWithEnabledCatalog() {
    if (!skillPickerCache) return;
    var enabled = {};
    (skillPickerCache.skills || []).forEach(function (skill) {
        if (skill && skill.enabled !== false) enabled[String(skill.name || '')] = true;
    });
    selectedSkillNames = selectedSkillNames.filter(function (name) { return enabled[String(name)]; });
    persistSkillPickerDraft(currentSessionId);
    syncSkillPickerButton();
}

function skillDraftStorageKey(sessionId) {
    const draftKey = sessionId ? String(sessionId) : NEW_SESSION_DRAFT_KEY;
    return LS_SKILL_DRAFT_PREFIX + draftKey;
}

function persistSkillPickerDraft(sessionId) {
    try {
        var key = skillDraftStorageKey(sessionId);
        if (selectedSkillNames.length) localStorage.setItem(key, JSON.stringify(selectedSkillNames));
        else localStorage.removeItem(key);
    } catch (e) { /* ignore */ }
}

function readStoredSkillPickerDraft(sessionId) {
    try {
        var raw = localStorage.getItem(skillDraftStorageKey(sessionId));
        var parsed = raw ? JSON.parse(raw) : [];
        if (!Array.isArray(parsed)) return [];
        return parsed.map(function (item) { return String(item || '').trim(); }).filter(Boolean);
    } catch (e) {
        return [];
    }
}

function removeStoredSkillPickerDraft(sessionId) {
    try { localStorage.removeItem(skillDraftStorageKey(sessionId)); } catch (e) { /* ignore */ }
}

function skillPickerPlusIcon() {
    return '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>';
}

function syncSkillPickerButton() {
    var e = skillPickerEls();
    if (!e.button) return;
    var count = selectedSkillNames.length;
    e.button.classList.toggle('is-active', count > 0);
    e.button.innerHTML = skillPickerPlusIcon()
        + (count > 0 ? '<span class="skill-picker-count">' + count + '</span>' : '');
    e.button.setAttribute('data-ui-tip', count > 0 ? ('已选择 ' + count + ' 个 Skill') : '选择 Skill');
}

function closeSkillPicker() {
    var e = skillPickerEls();
    if (e.popover) e.popover.classList.remove('is-open');
    if (e.button) e.button.setAttribute('aria-expanded', 'false');
}

function openSkillPicker() {
    var e = skillPickerEls();
    if (!e.popover || !e.button) return;
    constrainSkillPickerToTitlebar();
    e.popover.classList.add('is-open');
    e.button.setAttribute('aria-expanded', 'true');
}

function constrainSkillPickerToTitlebar() {
    var e = skillPickerEls();
    if (!e.popover || !e.row) return;
    var titlebar = document.querySelector('.titlebar');
    var titlebarBottom = titlebar ? titlebar.getBoundingClientRect().bottom : 44;
    var rowTop = e.row.getBoundingClientRect().top;
    var available = Math.max(1, Math.floor(rowTop - titlebarBottom - 10));
    var rootSize = parseFloat(getComputedStyle(document.documentElement).fontSize || '16') || 16;
    var cap = Math.floor(44 * rootSize);
    e.popover.style.setProperty('--composer-popover-max-height', Math.min(cap, available) + 'px');
}

function renderSkillPickerLoading() {
    var e = skillPickerEls();
    if (!e.popover) return;
    e.popover.innerHTML = '<div class="skill-picker-empty">正在加载 Skill</div>';
}

function renderSkillPickerError(err) {
    var e = skillPickerEls();
    if (!e.popover) return;
    e.popover.innerHTML = '<div class="skill-picker-empty">Skill 加载失败：' + skillPickerEscape(err && err.message ? err.message : err) + '</div>';
}

function skillPickerToggleIcon(action) {
    if (action === 'enable') {
        return '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2v10"/><path d="M18.4 6.6a9 9 0 1 1-12.8 0"/></svg>';
    }
    return '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="m5.5 5.5 13 13"/></svg>';
}

function skillPickerToggleHtml(enabled) {
    return '<span class="skill-picker-toggle-ico" aria-hidden="true">' + skillPickerToggleIcon(enabled ? 'disable' : 'enable') + '</span>';
}

function skillPickerOpenPageHtml() {
    return '<span class="skill-picker-toggle-ico" aria-hidden="true"><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M14 5h5v5"/><path d="m10 14 9-9"/><path d="M19 13v6H5V5h6"/></svg></span>';
}

function skillPickerGroupChevronHtml() {
    return '<svg viewBox="0 0 20 20" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m7 5 5 5-5 5"/></svg>';
}

function skillPickerGroupHtml(tab, key, title, summary, bodyHtml, summaryClass) {
    var collapsed = !!(skillPickerCollapsedGroups[tab] && skillPickerCollapsedGroups[tab][key]);
    var summaryClassName = 'skill-picker-group-summary' + (summaryClass ? ' ' + summaryClass : '');
    return '<section class="skill-picker-group' + (collapsed ? ' is-collapsed' : '') + '" data-skill-picker-group="' + skillPickerEscape(tab) + '" data-skill-picker-group-key="' + skillPickerEscape(key) + '">'
        + '<button type="button" class="skill-picker-group-toggle" aria-expanded="' + (collapsed ? 'false' : 'true') + '">'
        + '<span class="skill-picker-group-chevron">' + skillPickerGroupChevronHtml() + '</span>'
        + '<span class="skill-picker-group-name">' + skillPickerEscape(title) + '</span>'
        + '<span class="' + skillPickerEscape(summaryClassName) + '">' + skillPickerEscape(summary) + '</span>'
        + '</button>'
        + '<div class="skill-picker-group-items"' + (collapsed ? ' hidden' : '') + '>' + bodyHtml + '</div>'
        + '</section>';
}

function skillPickerMcpToolHoverDetail(tool) {
    var toolName = String(tool && tool.tool_name || '');
    var server = String(tool && tool.server || '');
    var description = String(tool && tool.description || '').trim() || '暂无描述';
    var status = tool && tool.enabled !== false ? '已启用' : '已禁用';
    return ['MCP 工具：' + toolName, '服务器：' + server, '描述：' + description, '状态：' + status].join('\n');
}

function renderSkillPickerSkillsHtml() {
    var skills = (skillPickerCache && skillPickerCache.skills) || [];
    if (!skills.length) {
        return '<div class="skill-picker-empty">当前没有已注册 Skill</div>';
    }
    var active = selectedSkillSet();
    var html = '';
    skills.forEach(function (skill) {
        var name = String(skill && skill.name || '');
        var enabled = skill && skill.enabled !== false;
        var checked = active[name] ? ' checked' : '';
        var disabled = enabled ? '' : ' disabled';
        html += '<div class="skill-picker-option' + (enabled ? '' : ' is-disabled') + '" data-ui-tip="' + skillPickerEscape(skillPickerHoverDetail(skill)) + '">'
            + '<label class="skill-picker-select">'
            + '<input type="checkbox" value="' + skillPickerEscape(name) + '"' + checked + disabled + '>'
            + '<span class="skill-picker-option-body">'
            + '<span class="skill-picker-option-name">' + skillPickerEscape(name) + '</span>'
            + '<span class="skill-picker-option-desc">' + skillPickerEscape(skill && skill.description || '') + '</span>'
            + '</span>'
            + '</label>'
            + '<button type="button" class="skill-picker-toggle skill-toggle-action" data-skill-name="' + skillPickerEscape(name) + '" data-enabled="' + (enabled ? 'true' : 'false') + '" data-ui-tip="' + (enabled ? '禁用' : '启用') + '" aria-label="' + (enabled ? '禁用' : '启用') + '">' + skillPickerToggleHtml(enabled) + '</button>'
            + '</div>';
    });
    return html;
}

function renderSkillPickerMcpToolsHtml() {
    if (mcpToolsLoading && !mcpToolsCache) {
        return '<div class="skill-picker-empty">正在加载 MCP 工具</div>';
    }
    if (mcpToolsError && !mcpToolsCache) {
        return '<div class="skill-picker-empty">MCP 工具加载失败：' + skillPickerEscape(mcpToolsError && mcpToolsError.message ? mcpToolsError.message : String(mcpToolsError)) + '</div>';
    }
    var tools = mcpToolsCache || [];
    var servers = mcpServersCache || [];
    var groups = Object.create(null);
    servers.forEach(function (serverInfo) {
        var server = String(serverInfo && serverInfo.server || '').trim() || '未命名服务器';
        if (!groups[server]) groups[server] = { info: serverInfo || {}, tools: [] };
    });
    tools.forEach(function (tool) {
        var server = String(tool && tool.server || '').trim() || '未命名服务器';
        if (!groups[server]) groups[server] = { info: {}, tools: [] };
        groups[server].tools.push(tool);
    });
    if (!Object.keys(groups).length) {
        return '<div class="skill-picker-empty">当前没有已配置的 MCP 服务器</div>';
    }
    return Object.keys(groups).sort(function (a, b) { return a.localeCompare(b); }).map(function (server) {
        var group = groups[server];
        var serverTools = group.tools;
        var enabledCount = serverTools.filter(function (tool) { return tool && tool.enabled !== false; }).length;
        var discovered = serverTools.length > 0 || group.info.discovered === true;
        var body = serverTools.length ? serverTools.map(function (tool) {
            var fn = String(tool && tool.function_name || '');
            var name = String(tool && tool.tool_name || fn);
            var enabled = tool && tool.enabled !== false;
            var desc = String(tool && tool.description || '').trim();
            var prefix = '[MCP server `' + server + '`] ';
            if (desc.indexOf(prefix) === 0) desc = desc.slice(prefix.length).trim();
            return '<div class="skill-picker-option mcp-tool-option' + (enabled ? '' : ' is-disabled') + '" data-ui-tip="' + skillPickerEscape(skillPickerMcpToolHoverDetail(tool)) + '">'
                + '<span class="skill-picker-option-body">'
                + '<span class="skill-picker-option-name">' + skillPickerEscape(name) + '</span>'
                + (desc ? '<span class="skill-picker-option-desc">' + skillPickerEscape(desc) + '</span>' : '')
                + (fn ? '<span class="mcp-tool-fname">' + skillPickerEscape(fn) + '</span>' : '')
                + '</span>'
                + '<button type="button" class="skill-picker-toggle mcp-tool-toggle" data-mcp-tool="' + skillPickerEscape(fn) + '" data-enabled="' + (enabled ? 'true' : 'false') + '" data-ui-tip="' + (enabled ? '禁用' : '启用') + '" aria-label="' + (enabled ? '禁用' : '启用') + '">' + skillPickerToggleHtml(enabled) + '</button>'
                + '</div>';
        }).join('') : '<div class="skill-picker-group-empty">'
            + '<span class="mcp-server-register-message">' + skillPickerEscape(group.info.error || '服务器尚未完成工具注册；请检查连接、凭据或服务配置。') + '</span>'
            + '<button type="button" class="mcp-server-register-btn" data-mcp-server="' + skillPickerEscape(server) + '"' + (mcpServerRegisterBusy[server] ? ' disabled' : '') + '>'
            + (mcpServerRegisterBusy[server] ? '注册中…' : '注册')
            + '</button>'
            + '</div>';
        return skillPickerGroupHtml(
            'mcp',
            server,
            server,
            discovered ? ('已启用 ' + enabledCount + ' / 共 ' + serverTools.length + ' 个工具') : '未注册',
            body,
            discovered ? '' : 'is-undiscovered'
        );
    }).join('');
}

function skillPickerComponentPills(plugin) {
    var components = (plugin && plugin.components) || {};
    var keys = ['skills', 'hooks', 'commands', 'mcp_servers', 'agents', 'prompts', 'runtime'];
    var rows = [];
    keys.forEach(function (key) {
        var value = components[key];
        var count = Array.isArray(value)
            ? value.length
            : (value && typeof value === 'object'
                ? (key === 'runtime' ? 1 : Object.keys(value).length)
                : Number(value || 0));
        if (count) rows.push('<span class="ext-pill">' + skillPickerEscape(key) + ' ' + count + '</span>');
    });
    return rows.join('') || '<span class="ext-pill">' + skillPickerEscape('无') + '</span>';
}

function renderSkillPickerHooksHtml() {
    if (extensionsLoading && !extensionsCache) {
        return '<div class="skill-picker-empty">正在加载扩展</div>';
    }
    if (extensionsError && !extensionsCache) {
        return '<div class="skill-picker-empty">扩展加载失败：' + skillPickerEscape(extensionsError && extensionsError.message ? extensionsError.message : String(extensionsError)) + '</div>';
    }
    var hooks = (extensionsCache && extensionsCache.hooks) || [];
    if (!hooks.length) {
        return '<div class="skill-picker-empty">当前没有已注册 Hook</div>';
    }
    return hooks.map(function (hook) {
        var id = String(hook && hook.id || '');
        var event = String(hook && hook.event || '');
        var matcher = String(hook && hook.matcher || '(全部)');
        var source = String(hook && (hook.source_id || hook.source) || 'project');
        var policy = String(hook && hook.failure_policy || 'warn');
        var timeout = hook && (hook.timeout_seconds != null ? hook.timeout_seconds : hook.timeout);
        var detail = ['事件：' + event, '匹配器：' + matcher, '来源：' + source, '策略 / 超时：' + policy + ' / ' + (timeout != null ? timeout + 's' : '—')].join('\n');
        return '<div class="skill-picker-option ext-option" data-ui-tip="' + skillPickerEscape(detail) + '">'
            + '<span class="hook-event-badge">' + skillPickerEscape(event || 'hook') + '</span>'
            + '<span class="skill-picker-option-body">'
            + '<span class="skill-picker-option-name">' + skillPickerEscape(id) + '</span>'
            + '<span class="skill-picker-option-desc">' + skillPickerEscape([matcher, source, policy + ' / ' + (timeout != null ? timeout + 's' : '—')].join(' · ')) + '</span>'
            + '</span>'
            + '</div>';
    }).join('');
}

function skillPickerPluginPageHref(plugin) {
    var id = String(plugin && plugin.id || '').trim();
    if (!/^[a-z0-9][a-z0-9._-]{0,127}$/.test(id)) return '';
    var expectedHref = '/plugins/' + id;
    var rows = Array.isArray(plugin && plugin.ui) ? plugin.ui : [];
    var hasPage = rows.some(function (row) {
        if (!row || String(row.href || '') !== expectedHref || String(row.target || '') !== 'plugin-page') return false;
        var slot = String(row.slot || '');
        return slot === 'navigation' || slot === 'settings.section';
    });
    return hasPage ? '/plugins/' + encodeURIComponent(id) : '';
}

function renderSkillPickerPluginsHtml() {
    if (extensionsLoading && !extensionsCache) {
        return '<div class="skill-picker-empty">正在加载扩展</div>';
    }
    if (extensionsError && !extensionsCache) {
        return '<div class="skill-picker-empty">扩展加载失败：' + skillPickerEscape(extensionsError && extensionsError.message ? extensionsError.message : String(extensionsError)) + '</div>';
    }
    var plugins = (extensionsCache && extensionsCache.plugins) || [];
    if (!plugins.length) {
        return '<div class="skill-picker-empty">当前没有已发现插件</div>';
    }
    var groups = Object.create(null);
    plugins.forEach(function (plugin) {
        var name = String(plugin && (plugin.name || plugin.id) || '').trim() || '未命名 Plugin';
        if (!groups[name]) groups[name] = [];
        groups[name].push(plugin);
    });
    return Object.keys(groups).sort(function (a, b) { return a.localeCompare(b); }).map(function (name) {
        var namedPlugins = groups[name];
        var enabledCount = namedPlugins.filter(function (plugin) {
            return plugin && (plugin.configured_enabled === undefined ? !!plugin.enabled : !!plugin.configured_enabled);
        }).length;
        var globalOn = !!(extensionsCache && extensionsCache.enabled && extensionsCache.enabled.plugins);
        var body = namedPlugins.map(function (plugin) {
            var id = String(plugin && plugin.id || '');
            var version = String(plugin && plugin.version || '');
            var enabled = plugin && (plugin.configured_enabled === undefined ? !!plugin.enabled : !!plugin.configured_enabled);
            var type = String(plugin && (plugin.source_format || plugin.format) || 'native');
            var compatibility = plugin && plugin.compatibility && plugin.compatibility.status || 'unknown';
            var pageHref = skillPickerPluginPageHref(plugin);
            var busy = !!pluginToggleBusy[id];
            var detail = ['插件：' + name, 'ID：' + id, '版本：' + version, '格式：' + type, '兼容性：' + compatibility, '状态：' + (enabled ? '已启用' : '已禁用')].join('\n');
            return '<div class="skill-picker-option ext-option plugin-option' + (enabled ? '' : ' is-disabled') + (pageHref ? ' has-page' : '') + '" data-ui-tip="' + skillPickerEscape(detail) + '">'
                + '<span class="plugin-type-badge">' + skillPickerEscape(type) + '</span>'
                + '<span class="skill-picker-option-body">'
                + '<span class="skill-picker-option-name">' + skillPickerEscape(id || name) + ' <span class="plugin-state' + (enabled ? '' : ' is-off') + '">' + (enabled ? '已启用' : '已禁用') + '</span></span>'
                + '<span class="skill-picker-option-desc">' + skillPickerEscape((version ? 'v' + version + ' · ' : '') + type + ' · ' + compatibility) + '</span>'
                + '<span class="ext-pills">' + skillPickerComponentPills(plugin) + '</span>'
                + '</span>'
                + '<span class="plugin-option-actions">'
                + '<button type="button" class="skill-picker-toggle plugin-toggle-action" data-plugin-id="' + skillPickerEscape(id) + '" data-enabled="' + (enabled ? 'true' : 'false') + '" data-ui-tip="' + (busy ? '处理中…' : (enabled ? '停用' : '启用')) + '" aria-label="' + (busy ? '处理中…' : (enabled ? '停用' : '启用')) + '"' + ((busy || !globalOn) ? ' disabled' : '') + (!globalOn ? ' title="PLUGINS_ENABLED is disabled"' : '') + '>' + skillPickerToggleHtml(enabled) + '</button>'
                + (pageHref ? '<a class="skill-picker-toggle plugin-open-action" href="' + skillPickerEscape(pageHref) + '" target="_blank" rel="noopener noreferrer" data-ui-tip="打开页面" aria-label="打开页面">' + skillPickerOpenPageHtml() + '</a>' : '')
                + '</span>'
                + '</div>';
        }).join('');
        return skillPickerGroupHtml(
            'plugins',
            name,
            name,
            '已启用 ' + enabledCount + ' / 共 ' + namedPlugins.length + ' 个',
            body
        );
    }).join('');
}

function renderSkillPicker(opts) {
    opts = opts || {};
    var e = skillPickerEls();
    if (!e.popover) return;
    var prevList = e.popover.querySelector('.skill-picker-list');
    var prevScrollTop = opts.preserveScroll === false ? 0 : (prevList ? prevList.scrollTop : 0);
    var focusedToggleName = '';
    if (document.activeElement && e.popover.contains(document.activeElement)) {
        var active = document.activeElement;
        if (active.classList && active.classList.contains('skill-toggle-action')) {
            focusedToggleName = String(active.getAttribute('data-skill-name') || '');
        }
    }
    var skills = (skillPickerCache && skillPickerCache.skills) || [];
    var activeTab = ['skills', 'mcp', 'hooks', 'plugins'].indexOf(skillPickerActiveTab) >= 0
        ? skillPickerActiveTab
        : 'skills';
    var selectedCount = selectedSkillNames.length;
    var enabledCount = skills.filter(function (skill) { return skill && skill.enabled !== false; }).length;
    var hooks = (extensionsCache && extensionsCache.hooks) || [];
    var plugins = (extensionsCache && extensionsCache.plugins) || [];
    var title = activeTab === 'mcp' ? 'MCP 工具'
        : activeTab === 'hooks' ? 'Hooks'
            : activeTab === 'plugins' ? 'Plugins'
                : '选择 Skill';
    var total = activeTab === 'mcp'
        ? (mcpToolsCache && mcpServersCache ? '共 ' + mcpServersCache.length + ' 个服务器 · ' + mcpToolsCache.length + ' 个工具' : '')
        : activeTab === 'hooks'
            ? (extensionsCache ? '共 ' + hooks.length : '')
            : activeTab === 'plugins'
                ? (extensionsCache ? '共 ' + plugins.length : '')
                : '已选 ' + selectedCount + ' / 已启用 ' + enabledCount + ' / 共 ' + skills.length;
    var html = '<div class="skill-picker-head">'
        + '<div class="skill-picker-title">' + skillPickerEscape(title)
        + (total ? ' <span class="skill-picker-total">' + skillPickerEscape(total) + '</span>' : '')
        + '</div>'
        + '<button type="button" class="skill-picker-clear' + (activeTab === 'skills' ? '' : ' is-hidden') + '"' + (activeTab === 'skills' ? '' : ' tabindex="-1" aria-hidden="true"') + '>清空</button>'
        + '</div>'
        + '<div class="skill-picker-tabs" role="tablist" aria-label="Skill">'
        + '<button type="button" class="skill-picker-tab' + (activeTab === 'skills' ? ' is-active' : '') + '" role="tab" aria-selected="' + (activeTab === 'skills' ? 'true' : 'false') + '" data-skill-picker-tab="skills">Skill</button>'
        + '<button type="button" class="skill-picker-tab' + (activeTab === 'mcp' ? ' is-active' : '') + '" role="tab" aria-selected="' + (activeTab === 'mcp' ? 'true' : 'false') + '" data-skill-picker-tab="mcp">MCP 工具</button>'
        + '<button type="button" class="skill-picker-tab' + (activeTab === 'hooks' ? ' is-active' : '') + '" role="tab" aria-selected="' + (activeTab === 'hooks' ? 'true' : 'false') + '" data-skill-picker-tab="hooks">Hooks</button>'
        + '<button type="button" class="skill-picker-tab' + (activeTab === 'plugins' ? ' is-active' : '') + '" role="tab" aria-selected="' + (activeTab === 'plugins' ? 'true' : 'false') + '" data-skill-picker-tab="plugins">Plugins</button>'
        + '</div>'
        + '<div class="skill-picker-list">'
        + (activeTab === 'mcp' ? renderSkillPickerMcpToolsHtml()
            : activeTab === 'hooks' ? renderSkillPickerHooksHtml()
                : activeTab === 'plugins' ? renderSkillPickerPluginsHtml()
                    : renderSkillPickerSkillsHtml())
        + '</div>';
    e.popover.innerHTML = html;
    if (typeof initUiHoverTips === 'function') initUiHoverTips(e.popover);
    var nextList = e.popover.querySelector('.skill-picker-list');
    if (nextList && prevScrollTop > 0) nextList.scrollTop = prevScrollTop;
    if (focusedToggleName) {
        var focusTarget = null;
        e.popover.querySelectorAll('.skill-toggle-action').forEach(function (btn) {
            if (String(btn.getAttribute('data-skill-name') || '') === focusedToggleName) focusTarget = btn;
        });
        if (focusTarget) focusTarget.focus();
    }
    e.popover.querySelectorAll('input[type="checkbox"]').forEach(function (checkbox) {
        checkbox.addEventListener('change', function () {
            var name = String(checkbox.value || '');
            var set = selectedSkillSet();
            if (checkbox.checked) set[name] = true;
            else delete set[name];
            selectedSkillNames = Object.keys(set).filter(Boolean);
            persistSkillPickerDraft(currentSessionId);
            syncSkillPickerButton();
            renderSkillPicker();
        });
    });
    e.popover.querySelectorAll('[data-skill-picker-tab]').forEach(function (tab) {
        tab.addEventListener('click', function (ev) {
            ev.preventDefault();
            ev.stopPropagation();
            skillPickerActiveTab = tab.getAttribute('data-skill-picker-tab') || 'skills';
            renderSkillPicker({ preserveScroll: false });
        });
    });
    e.popover.querySelectorAll('.skill-picker-group-toggle').forEach(function (button) {
        button.addEventListener('click', function (ev) {
            ev.preventDefault();
            ev.stopPropagation();
            var group = button.closest('.skill-picker-group');
            if (!group) return;
            var tab = String(group.getAttribute('data-skill-picker-group') || '');
            var key = String(group.getAttribute('data-skill-picker-group-key') || '');
            if (!skillPickerCollapsedGroups[tab]) return;
            var collapsed = !group.classList.contains('is-collapsed');
            skillPickerCollapsedGroups[tab][key] = collapsed;
            group.classList.toggle('is-collapsed', collapsed);
            button.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
            var items = group.querySelector('.skill-picker-group-items');
            if (items) items.hidden = collapsed;
        });
    });
    var clear = e.popover.querySelector('.skill-picker-clear');
    if (clear) {
        clear.addEventListener('click', function (ev) {
            ev.preventDefault();
            ev.stopPropagation();
            selectedSkillNames = [];
            persistSkillPickerDraft(currentSessionId);
            syncSkillPickerButton();
            renderSkillPicker();
        });
    }
    e.popover.querySelectorAll('.skill-toggle-action').forEach(function (button) {
        button.addEventListener('click', function (ev) {
            ev.preventDefault();
            ev.stopPropagation();
            var name = String(button.getAttribute('data-skill-name') || '');
            var enabled = button.getAttribute('data-enabled') !== 'true';
            setSkillPickerEnabled(name, enabled);
        });
    });
    e.popover.querySelectorAll('.mcp-tool-toggle').forEach(function (button) {
        button.addEventListener('click', function (ev) {
            ev.preventDefault();
            ev.stopPropagation();
            var fname = String(button.getAttribute('data-mcp-tool') || '');
            var enabled = button.getAttribute('data-enabled') !== 'true';
            setMcpToolEnabled(fname, enabled);
        });
    });
    e.popover.querySelectorAll('.mcp-server-register-btn').forEach(function (button) {
        button.addEventListener('click', function (ev) {
            ev.preventDefault();
            ev.stopPropagation();
            registerMcpServer(String(button.getAttribute('data-mcp-server') || ''));
        });
    });
    e.popover.querySelectorAll('.plugin-toggle-action').forEach(function (button) {
        button.addEventListener('click', function (ev) {
            ev.preventDefault();
            ev.stopPropagation();
            toggleSkillPickerPlugin(button);
        });
    });
    e.popover.querySelectorAll('.plugin-open-action').forEach(function (link) {
        link.addEventListener('click', function (ev) { ev.stopPropagation(); });
    });
}

async function setSkillPickerEnabled(name, enabled) {
    name = String(name || '').trim();
    if (!name || skillPickerToggleBusy[name]) return;
    skillPickerToggleBusy[name] = true;
    try {
        var response = await fetch('/api/skills/' + encodeURIComponent(name) + '/enabled', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ enabled: enabled === true }),
        });
        var data = await response.json();
        if (!data || !data.ok) throw new Error((data && data.error) || 'Skill 启停失败');
        (skillPickerCache.skills || []).forEach(function (skill) {
            if (String(skill && skill.name || '') === name) skill.enabled = enabled === true;
        });
        reconcileSelectedSkillsWithEnabledCatalog();
        renderSkillPicker();
    } catch (err) {
        if (typeof appendLogVisible === 'function') appendLogVisible('Skill 启停失败：' + String(err.message || err), 'error-log');
    } finally {
        delete skillPickerToggleBusy[name];
    }
}

async function loadSkillPickerSkills() {
    const response = await fetch('/api/skills', { credentials: 'same-origin' });
    const data = await response.json();
    if (!data || !data.ok) throw new Error((data && data.error) || 'Skill 加载失败');
    skillPickerCache = data;
    reconcileSelectedSkillsWithEnabledCatalog();
    return data;
}

async function loadSkillPickerMcpTools() {
    const response = await fetch('/api/mcp/tools', { credentials: 'same-origin' });
    const data = await response.json();
    if (!data || !data.ok) throw new Error((data && data.error) || 'MCP 工具加载失败');
    mcpToolsCache = Array.isArray(data.tools) ? data.tools : [];
    mcpServersCache = Array.isArray(data.servers) ? data.servers : [];
    mcpToolsError = null;
    return mcpToolsCache;
}

async function setMcpToolEnabled(functionName, enabled) {
    functionName = String(functionName || '').trim();
    if (!functionName || mcpToolToggleBusy[functionName]) return;
    mcpToolToggleBusy[functionName] = true;
    try {
        var response = await fetch('/api/mcp/tools/' + encodeURIComponent(functionName) + '/enabled', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ enabled: enabled === true }),
        });
        var data = await response.json();
        if (!data || !data.ok) throw new Error((data && data.error) || 'MCP 工具启停失败');
        (mcpToolsCache || []).forEach(function (tool) {
            if (String(tool && tool.function_name || '') === functionName) {
                tool.enabled = enabled === true;
            }
        });
        renderSkillPicker();
    } catch (err) {
        if (typeof appendLogVisible === 'function') {
            appendLogVisible('MCP 工具启停失败：' + String(err.message || err), 'error-log');
        }
    } finally {
        delete mcpToolToggleBusy[functionName];
    }
}

async function registerMcpServer(serverName) {
    serverName = String(serverName || '').trim();
    if (!serverName || mcpServerRegisterBusy[serverName]) return;
    mcpServerRegisterBusy[serverName] = true;
    renderSkillPicker();
    try {
        var response = await fetch('/api/mcp/servers/' + encodeURIComponent(serverName) + '/register', {
            method: 'POST',
            credentials: 'same-origin',
        });
        var data = await response.json();
        if (!data || !data.ok) throw new Error((data && data.error) || 'MCP 注册失败');
        await loadSkillPickerMcpTools();
        if (!data.registered && typeof showGlobalWarningBanner === 'function') {
            showGlobalWarningBanner(
                'MCP 注册未完成：' + String((data.server && data.server.error) || serverName)
            );
        }
    } catch (err) {
        (mcpServersCache || []).forEach(function (server) {
            if (String(server && server.server || '') === serverName) server.error = String(err.message || err);
        });
        if (typeof showGlobalWarningBanner === 'function') {
            showGlobalWarningBanner('MCP 注册失败：' + String(err.message || err));
        }
    } finally {
        delete mcpServerRegisterBusy[serverName];
        renderSkillPicker();
    }
}

function refreshSkillPickerSkills() {
    if (skillPickerRefreshPromise) return skillPickerRefreshPromise;
    skillPickerRefreshPromise = loadSkillPickerSkills()
        .then(function () { renderSkillPicker(); })
        .catch(function (err) { renderSkillPickerError(err); })
        .finally(function () { skillPickerRefreshPromise = null; });
    return skillPickerRefreshPromise;
}

function refreshSkillPickerMcpTools() {
    if (mcpToolsRefreshPromise) return mcpToolsRefreshPromise;
    mcpToolsLoading = true;
    renderSkillPicker();
    mcpToolsRefreshPromise = loadSkillPickerMcpTools()
        .catch(function (err) { mcpToolsError = err; })
        .finally(function () {
            mcpToolsLoading = false;
            mcpToolsRefreshPromise = null;
            renderSkillPicker();
        });
    return mcpToolsRefreshPromise;
}

async function loadSkillPickerExtensions() {
    const response = await fetch('/api/extensions', { credentials: 'same-origin' });
    const data = await response.json();
    if (!data || !data.ok) throw new Error((data && data.error) || '扩展加载失败');
    extensionsCache = data;
    extensionsError = null;
    return data;
}

async function toggleSkillPickerPlugin(button) {
    var id = String(button && button.getAttribute('data-plugin-id') || '').trim();
    if (!id || pluginToggleBusy[id]) return;
    var enabled = button.getAttribute('data-enabled') !== 'true';
    pluginToggleBusy[id] = true;
    renderSkillPicker();
    try {
        var response = await fetch('/api/plugins/' + encodeURIComponent(id) + '/enabled', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ enabled: enabled, session_id: currentSessionId || '' }),
        });
        var data = await response.json();
        if (!data || !data.ok) throw new Error((data && data.error) || '插件状态更新失败');
        await loadSkillPickerExtensions();
        document.dispatchEvent(new CustomEvent('myagent:extension-state-changed', {
            detail: { sessionId: currentSessionId || '', pluginId: id },
        }));
    } catch (err) {
        if (typeof showGlobalWarningBanner === 'function') {
            showGlobalWarningBanner('插件状态更新失败：' + String(err.message || err));
        } else if (typeof appendLogVisible === 'function') {
            appendLogVisible('插件状态更新失败：' + String(err.message || err), 'error-log');
        }
    } finally {
        delete pluginToggleBusy[id];
        renderSkillPicker();
    }
}

function refreshSkillPickerExtensions() {
    if (extensionsRefreshPromise) return extensionsRefreshPromise;
    extensionsLoading = true;
    renderSkillPicker();
    extensionsRefreshPromise = loadSkillPickerExtensions()
        .catch(function (err) { extensionsError = err; })
        .finally(function () {
            extensionsLoading = false;
            extensionsRefreshPromise = null;
            renderSkillPicker();
        });
    return extensionsRefreshPromise;
}

function consumeSelectedSkillsForSend() {
    var out = selectedSkillNames.slice();
    selectedSkillNames = [];
    removeStoredSkillPickerDraft(currentSessionId);
    syncSkillPickerButton();
    closeSkillPicker();
    if (skillPickerCache) renderSkillPicker();
    return out;
}

function setSelectedSkillsForCurrentSession(skills) {
    selectedSkillNames = Array.isArray(skills)
        ? skills.map(function (item) { return String(item || '').trim(); }).filter(Boolean)
        : [];
    persistSkillPickerDraft(currentSessionId);
    syncSkillPickerButton();
    if (skillPickerCache) renderSkillPicker();
}

function setSelectedSkillsForSession(sessionId, skills) {
    if (!sessionId) return;
    if (sessionId === currentSessionId) {
        setSelectedSkillsForCurrentSession(skills);
        return;
    }
    var normalized = Array.isArray(skills)
        ? skills.map(function (item) { return String(item || '').trim(); }).filter(Boolean)
        : [];
    try {
        var key = skillDraftStorageKey(sessionId);
        if (normalized.length) localStorage.setItem(key, JSON.stringify(normalized));
        else localStorage.removeItem(key);
    } catch (e) { /* ignore */ }
}

function stashSkillPickerDraft(sessionId) {
    persistSkillPickerDraft(sessionId);
}

function restoreSkillPickerDraft(sessionId) {
    selectedSkillNames = readStoredSkillPickerDraft(sessionId);
    syncSkillPickerButton();
    closeSkillPicker();
    if (skillPickerCache) renderSkillPicker();
}

function initSkillPicker() {
    var e = skillPickerEls();
    if (!e.button || !e.popover) return;
    syncSkillPickerButton();
    e.button.addEventListener('click', function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        var willOpen = !e.popover.classList.contains('is-open');
        if (!willOpen) {
            closeSkillPicker();
            return;
        }
        if (skillPickerCache) renderSkillPicker();
        else renderSkillPickerLoading();
        openSkillPicker();
        refreshSkillPickerSkills();
        refreshSkillPickerMcpTools();
        refreshSkillPickerExtensions();
    });
    document.addEventListener('click', function (ev) {
        var fresh = skillPickerEls();
        if (!fresh.row || !fresh.row.contains(ev.target)) closeSkillPicker();
    });
    document.addEventListener('keydown', function (ev) {
        if (ev.key === 'Escape') closeSkillPicker();
    });
    window.addEventListener('resize', function () {
        var fresh = skillPickerEls();
        if (fresh.popover && fresh.popover.classList.contains('is-open')) constrainSkillPickerToTitlebar();
    });
}

initSkillPicker();
window.consumeSelectedSkillsForSend = consumeSelectedSkillsForSend;
window.setSelectedSkillsForCurrentSession = setSelectedSkillsForCurrentSession;
window.setSelectedSkillsForSession = setSelectedSkillsForSession;
window.refreshSkillPickerSkills = refreshSkillPickerSkills;
window.stashSkillPickerDraft = stashSkillPickerDraft;
window.restoreSkillPickerDraft = restoreSkillPickerDraft;
