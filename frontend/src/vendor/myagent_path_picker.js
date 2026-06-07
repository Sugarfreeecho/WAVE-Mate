/**
 * MyAgent 本机路径选择：调用 /api/pick-path，为配置项与聊天输入附加浏览按钮。
 */
(function (global) {
  'use strict';

  var FOLDER_SVG =
    '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"></path>' +
    '</svg>';

  function injectStyles() {
    if (document.getElementById('myagent-path-picker-styles')) return;
    var st = document.createElement('style');
    st.id = 'myagent-path-picker-styles';
    st.textContent =
      '.path-input-row{display:flex;align-items:stretch;gap:0.35rem;width:100%;}' +
      '.path-input-row>.ip,.path-input-row>.tx,.path-input-row>input[type="text"],.path-input-row>input:not([type]){flex:1;min-width:0;}' +
      '.path-browse-btn{flex-shrink:0;width:2.35rem;padding:0;border:1px solid var(--border-glass,rgba(255,255,255,.08));' +
      'border-radius:var(--radius-sm,8px);background:var(--surface-glass2,rgba(40,40,60,.94));color:var(--text-secondary,#a6adc8);' +
      'cursor:pointer;display:inline-flex;align-items:center;justify-content:center;transition:color .18s,border-color .18s,background .18s;}' +
      '.path-browse-btn:hover{color:var(--text-primary,#cdd6f4);border-color:var(--border-brand-accent,rgba(124,111,247,.35));background:rgba(108,92,231,.12);}' +
      '.path-browse-btn:disabled{opacity:.45;cursor:not-allowed;}' +
      '.path-browse-btn--ghost{background:transparent;border-color:transparent;box-shadow:none;width:2.1rem;}' +
      '.path-browse-btn--ghost:hover{background:rgba(108,92,231,.1);border-color:transparent;color:var(--accent-2,#d4b8fc);}' +
      '.input-wrapper .path-browse-btn--ghost{align-self:center;margin-right:-0.15rem;}';
    document.head.appendChild(st);
  }

  async function pickPath(kind, initial, multiple) {
    var controller = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    var timer = controller ? setTimeout(function () { controller.abort(); }, 50000) : null;
    var r;
    try {
      r = await fetch('/api/pick-path', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ kind: kind || 'directory', initial: initial || '', multiple: !!multiple }),
        signal: controller ? controller.signal : undefined,
      });
    } finally {
      if (timer) clearTimeout(timer);
    }
    var j = await r.json().catch(function () {
      return { ok: false, error: '请求失败' };
    });
    if (!r.ok || !j.ok) {
      if (j && j.cancelled) return null;
      var err = (j && j.error) || '无法打开选择对话框';
      if (/取消|cancelled|800704c7|2147023673/i.test(err)) return null;
      throw new Error(err);
    }
    if (multiple) return Array.isArray(j.paths) ? j.paths : (j.path ? [j.path] : []);
    return j.path || null;
  }

  async function runPick(btn, kind, initial, onPicked, multiple) {
    btn.disabled = true;
    try {
      var p = await pickPath(kind, initial || '', !!multiple);
      if (onPicked) onPicked(p);
    } catch (e) {
      return;
    } finally {
      btn.disabled = false;
    }
  }

  function quotePickedPath(p) {
    var s = String(p || '').trim();
    if (!s) return '';
    if ((s.charAt(0) === '"' && s.charAt(s.length - 1) === '"')
        || (s.charAt(0) === "'" && s.charAt(s.length - 1) === "'")) {
      s = s.slice(1, -1);
    }
    return '"' + s.replace(/"/g, '\\"') + '"';
  }

  function wrapInputWithBrowse(input, kind, title) {
    if (!input || input.dataset.pathBrowseWrapped === '1') return input;
    injectStyles();
    var row = document.createElement('div');
    row.className = 'path-input-row';
    var parent = input.parentNode;
    if (!parent) return input;
    parent.insertBefore(row, input);
    row.appendChild(input);
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'path-browse-btn';
    btn.innerHTML = FOLDER_SVG;
    var tipText = title || '浏览路径';
    btn.setAttribute('aria-label', tipText);
    if (typeof bindUiHoverTip === 'function') {
      btn.setAttribute('data-ui-tip', tipText);
      btn.removeAttribute('title');
      bindUiHoverTip(btn);
    } else {
      btn.title = tipText;
    }

    btn.addEventListener('click', function (ev) {
      ev.stopPropagation();
      var fixedKind = input.getAttribute('data-path-kind') || kind;
      if (fixedKind !== 'file' && fixedKind !== 'directory') {
        fixedKind = 'directory';
      }
      runPick(btn, fixedKind, input.value || '', function (p) {
        if (!p) return;
        var nextPath = Array.isArray(p) ? (p[0] || '') : String(p);
        if (!nextPath) return;
        input.value = nextPath;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      });
    });
    row.appendChild(btn);
    input.dataset.pathBrowseWrapped = '1';
    return input;
  }

  function insertPathAtCursor(textarea, p) {
    var ins = quotePickedPath(p);
    var start = textarea.selectionStart;
    var end = textarea.selectionEnd;
    var before = textarea.value.slice(0, start);
    var after = textarea.value.slice(end);
    if (before.length && !/\s$/.test(before)) ins = ' ' + ins;
    if (after.length && !/^\s/.test(after)) ins = ins + ' ';
    textarea.value = before + ins + after;
    var pos = before.length + ins.length;
    textarea.selectionStart = textarea.selectionEnd = pos;
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    textarea.focus();
  }

  function insertTextAtCursor(textarea, text) {
    var start = textarea.selectionStart;
    var end = textarea.selectionEnd;
    var before = textarea.value.slice(0, start);
    var after = textarea.value.slice(end);
    var ins = String(text || '');
    if (before.length && !/\s$/.test(before)) ins = ' ' + ins;
    if (after.length && !/^\s/.test(after)) ins = ins + ' ';
    textarea.value = before + ins + after;
    var pos = before.length + ins.length;
    textarea.selectionStart = textarea.selectionEnd = pos;
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    textarea.focus();
  }

  function attachChatPicker(button, textarea) {
    if (!button || !textarea) return;
    injectStyles();
    button.classList.add('path-browse-btn', 'path-browse-btn--ghost');
    button.innerHTML = FOLDER_SVG;
    button.setAttribute('aria-label', '选择文件');
    button.setAttribute('data-ui-tip', '选择文件');
    button.dataset.silentPickerUnavailable = '1';
    button.removeAttribute('title');

    button.addEventListener('click', function (ev) {
      ev.stopPropagation();
      var initial = (global && typeof global.__WORK_DIR__ === 'string') ? global.__WORK_DIR__ : '';
      runPick(button, 'file', initial, function (p) {
        var paths = Array.isArray(p) ? p : (p ? [p] : []);
        if (!paths.length) return;
        var text = paths.map(function (item) { return quotePickedPath(item); }).join(' ');
        insertTextAtCursor(textarea, text);
      }, true);
    });
  }

  function scan(root) {
    root = root || document;
    var nodes = root.querySelectorAll('[data-path-kind]');
    var i;
    for (i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var kind = el.getAttribute('data-path-kind');
      if (kind === 'file' || kind === 'directory') {
        wrapInputWithBrowse(el, kind);
      }
    }
  }

  global.MyAgentPathPicker = {
    pickPath: pickPath,
    wrapInputWithBrowse: wrapInputWithBrowse,
    attachChatPicker: attachChatPicker,
    scan: scan,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      scan(document);
    });
  } else {
    scan(document);
  }
})(typeof window !== 'undefined' ? window : globalThis);
