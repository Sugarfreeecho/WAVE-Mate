function renderSessionListFromStore() {
    if (!sessionsList) return Object.create(null);
    const nextStreamMap = Object.create(null);
    const sections = selectSessionSections();
    const allSessions = selectAllSessions();

    sessionsList.innerHTML = '';

    function appendSection(sectionKey, title, list) {
        if (!list.length && sectionKey !== 'archived') return;
        var displayCount = sectionKey === 'archived' ? selectArchivedDisplayCount() : list.length;
        var expanded = sessionSectionExpanded(sectionKey);
        var sec = document.createElement('div');
        sec.className = 'session-section' + (expanded ? '' : ' is-collapsed');
        sec.dataset.section = sectionKey;

        var toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'session-section-toggle';
        toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        toggle.innerHTML = '<span class="session-section-toggle-label">' + escapeHtml(title) + '</span>'
            + '<span class="session-section-meta">'
            + '<span class="session-section-count">' + String(displayCount) + '</span>'
            + '<span class="session-section-chev" aria-hidden="true">▾</span>'
            + '</span>';
        toggle.addEventListener('click', function (e) {
            e.preventDefault();
            sec.classList.toggle('is-collapsed');
            var isExpanded = !sec.classList.contains('is-collapsed');
            persistSessionSectionExpanded(sectionKey, isExpanded);
            toggle.setAttribute('aria-expanded', isExpanded ? 'true' : 'false');
        });

        var body = document.createElement('div');
        body.className = 'session-section-body';
        if (sectionKey === 'archived') appendArchiveLoadButton(body);
        for (let j = 0; j < list.length; j += 1) {
            body.appendChild(buildAndBindSessionRow(list[j], allSessions, nextStreamMap));
        }
        sec.appendChild(toggle);
        sec.appendChild(body);
        sessionsList.appendChild(sec);
    }

    appendSection('pinned', '置顶目录', sections.pinned);
    appendSection('normal', '会话目录', sections.normal);
    appendSection('archived', '归档目录', sections.archived);
    return nextStreamMap;
}

function appendArchiveLoadButton(body) {
    var loadBtn = document.createElement('button');
    loadBtn.type = 'button';
    loadBtn.className = 'session-archive-load-btn';
    loadBtn.textContent = sessionStore.archivedLoaded ? '刷新归档目录' : '加载归档目录';
    loadBtn.addEventListener('click', async function (e) {
        e.preventDefault();
        e.stopPropagation();
        loadBtn.disabled = true;
        loadBtn.textContent = '加载中...';
        try {
            const response = await fetch('/sessions?include_archived=true');
            const sessions = await response.json();
            const all = Array.isArray(sessions) ? sessions : [];
            sessionStore.setArchivedLoaded(all);
            syncArchivedSessionStateFromStore();
            sessionListCache.set(all.filter(function (s) { return s && s.id && !s.archived; }));
            await loadSessions();
        } catch (err) {
            console.error('加载归档目录失败:', err);
            loadBtn.disabled = false;
            loadBtn.textContent = sessionStore.archivedLoaded ? '刷新归档目录' : '加载归档目录';
        }
    });
    body.appendChild(loadBtn);
}

function renderSessionTitleFromStore() {
    updateSessionTitle();
}
