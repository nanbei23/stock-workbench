let hermesSessionId = localStorage.getItem('hermesSessionId') || '';
let hermesActiveDraft = null;
let hermesSessions = [];

document.addEventListener('DOMContentLoaded', async () => {
    await loadHermesSessions();
    if (hermesSessionId) {
        await loadHermesSession(hermesSessionId);
    } else {
        startHermesSession();
    }
});

async function hermesRequestJson(url, options = {}) {
    const resp = await fetch(url, options);
    const contentType = resp.headers.get('content-type') || '';
    if (!resp.ok) {
        let message = `HTTP ${resp.status}`;
        if (contentType.includes('application/json')) {
            const data = await resp.json();
            message = data.detail || data.message || message;
        }
        throw new Error(message);
    }
    if (!contentType.includes('application/json')) throw new Error(`接口返回非 JSON: ${url}`);
    return resp.json();
}

function hermesPost(url, data = {}) {
    return hermesRequestJson(url, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data)
    });
}

async function loadHermesSessions() {
    const list = document.getElementById('hermesSessionList');
    if (list) list.innerHTML = '<div class="empty-row">加载会话...</div>';
    try {
        const data = await hermesRequestJson('/api/hermes/sessions?limit=80');
        hermesSessions = data.sessions || [];
        renderHermesSessionSummary();
        renderHermesSessions(hermesSessions);
    } catch (e) {
        if (list) list.innerHTML = `<div class="empty-row">会话加载失败：${escapeHtml(e.message)}</div>`;
    }
}

function renderHermesSessionSummary() {
    const el = document.getElementById('hermesSessionSummary');
    if (!el) return;
    const executed = hermesSessions.reduce((sum, item) => sum + Number(item.executed_count || 0), 0);
    el.innerHTML = `<div><span>最近会话</span><strong>${hermesSessions.length}</strong></div>
        <div><span>已执行</span><strong>${executed}</strong></div>`;
}

function renderHermesSessions(sessions) {
    const list = document.getElementById('hermesSessionList');
    if (!list) return;
    if (!sessions.length) {
        list.innerHTML = '<div class="empty-row">暂无历史会话</div>';
        return;
    }
    list.innerHTML = sessions.map(item => {
        const active = item.session_id === hermesSessionId ? 'active' : '';
        const executed = Number(item.executed_count || 0);
        const draftCount = Number(item.draft_count || 0);
        return `<button class="hermes-session-item ${active}" onclick="loadHermesSession('${escapeAttr(item.session_id)}')">
            <span class="hermes-session-title">${escapeHtml(item.title || item.session_id)}</span>
            <span class="hermes-session-last">${escapeHtml(item.last_message || '')}</span>
            <span class="hermes-session-meta">${escapeHtml(formatHermesTime(item.last_at))} · ${item.message_count || 0}条 · 草稿${draftCount} · 执行${executed}</span>
        </button>`;
    }).join('');
}

function filterHermesSessions() {
    const q = (document.getElementById('hermesSearchInput')?.value || '').trim().toLowerCase();
    if (!q) return renderHermesSessions(hermesSessions);
    renderHermesSessions(hermesSessions.filter(item => {
        return [item.title, item.last_message, item.session_id]
            .some(value => String(value || '').toLowerCase().includes(q));
    }));
}

async function loadHermesSession(sessionId) {
    if (!sessionId) return;
    hermesSessionId = sessionId;
    localStorage.setItem('hermesSessionId', hermesSessionId);
    hermesActiveDraft = null;
    renderHermesDraft(null);
    renderHermesSessions(hermesSessions);
    try {
        const data = await hermesRequestJson(`/api/hermes/session/${encodeURIComponent(sessionId)}?limit=100`);
        const events = data.events || [];
        renderHermesEvents(events);
        const found = hermesSessions.find(item => item.session_id === sessionId);
        document.getElementById('hermesSessionTitle').textContent = found?.title || sessionId;
        document.getElementById('hermesSessionMeta').textContent = `${events.length} 条消息 · ${found?.last_at ? formatHermesTime(found.last_at) : '当前会话'}`;
    } catch (e) {
        appendHermesMessage('assistant', `会话读取失败：${e.message}`);
    }
}

function renderHermesEvents(events) {
    const el = document.getElementById('hermesMessages');
    if (!el) return;
    if (!events.length) {
        el.innerHTML = '<div class="hermes-message assistant"><div class="hermes-bubble">这是一个新会话。写库操作会先生成草稿，确认后执行。</div></div>';
        return;
    }
    el.innerHTML = events.map(event => {
        const role = event.role === 'user' ? 'user' : event.role === 'tool' ? 'tool' : 'assistant';
        const draft = event.draft ? `<div class="hermes-history-draft">${escapeHtml(event.draft.summary || event.draft.label || '草稿')}</div>` : '';
        const result = event.result ? `<div class="hermes-history-result">${escapeHtml(event.result.summary || event.result.label || '已执行')}</div>` : '';
        return `<div class="hermes-message ${role}">
            <div class="hermes-bubble">
                ${escapeHtml(event.message || '')}
                ${draft}
                ${result}
            </div>
        </div>`;
    }).join('');
    el.scrollTop = el.scrollHeight;
}

function appendHermesMessage(role, text) {
    const el = document.getElementById('hermesMessages');
    if (!el) return;
    const item = document.createElement('div');
    item.className = `hermes-message ${role}`;
    item.innerHTML = `<div class="hermes-bubble">${escapeHtml(text || '')}</div>`;
    el.appendChild(item);
    el.scrollTop = el.scrollHeight;
}

function renderHermesDraft(draft) {
    const el = document.getElementById('hermesDraftPanel');
    if (!el) return;
    hermesActiveDraft = draft || null;
    if (!draft) {
        el.innerHTML = '<div class="empty-row">暂无待确认草稿</div>';
        return;
    }
    const risks = (draft.risks || []).map(x => `<li>${escapeHtml(x)}</li>`).join('');
    const blockers = (draft.blockers || []).map(x => `<li>${escapeHtml(x)}</li>`).join('');
    const payloadRows = Object.entries(draft.payload || {})
        .filter(([, value]) => value !== '' && value !== null && value !== undefined)
        .map(([key, value]) => `<div><span>${escapeHtml(key)}</span><b>${escapeHtml(String(value))}</b></div>`)
        .join('');
    el.innerHTML = `<div class="hermes-draft-head">
            <span>${escapeHtml(draft.label || '操作草稿')}</span>
            <strong>${escapeHtml(draft.executable ? '待确认' : '需补充')}</strong>
        </div>
        <div class="hermes-draft-summary">${escapeHtml(draft.summary || '')}</div>
        <div class="hermes-draft-parser">${escapeHtml((draft.parser || 'rules') === 'llm' ? 'LLM 识别' : '规则兜底')}</div>
        ${payloadRows ? `<div class="hermes-draft-payload">${payloadRows}</div>` : ''}
        ${risks ? `<div class="hermes-draft-note"><b>风险提示</b><ul>${risks}</ul></div>` : ''}
        ${blockers ? `<div class="hermes-draft-note blocked"><b>不能执行</b><ul>${blockers}</ul></div>` : ''}
        <div class="hermes-draft-actions">
            <button class="btn btn-sm" onclick="renderHermesDraft(null)">取消</button>
            <button class="btn btn-primary btn-sm" ${draft.executable ? '' : 'disabled'} onclick="confirmHermesDraft()">确认写入</button>
        </div>`;
}

async function sendHermesMessage(event) {
    event?.preventDefault();
    const input = document.getElementById('hermesInput');
    const btn = document.getElementById('hermesSendBtn');
    const message = (input?.value || '').trim();
    if (!message) return;
    appendHermesMessage('user', message);
    if (input) input.value = '';
    if (btn) btn.disabled = true;
    try {
        const data = await hermesPost('/api/hermes/message', {message, session_id: hermesSessionId || null});
        hermesSessionId = data.session_id || hermesSessionId;
        if (hermesSessionId) localStorage.setItem('hermesSessionId', hermesSessionId);
        const parserLabel = data.parser === 'llm' ? 'LLM 识别' : '规则兜底';
        appendHermesMessage('assistant', `${data.answer || '已处理'}\n\n${parserLabel}`);
        renderHermesDraft(data.draft || null);
        await loadHermesSessions();
        renderHermesSessions(hermesSessions);
        document.getElementById('hermesSessionTitle').textContent = hermesSessions.find(s => s.session_id === hermesSessionId)?.title || '当前会话';
    } catch (e) {
        appendHermesMessage('assistant', `处理失败：${e.message}`);
    } finally {
        if (btn) btn.disabled = false;
        input?.focus();
    }
}

async function confirmHermesDraft() {
    if (!hermesActiveDraft || !hermesSessionId) return;
    try {
        const result = await hermesPost('/api/hermes/confirm', {
            session_id: hermesSessionId,
            draft_id: hermesActiveDraft.id
        });
        appendHermesMessage('tool', `已执行：${result.summary || result.label || '操作成功'}`);
        renderHermesDraft(null);
        await loadHermesSessions();
        await loadHermesSession(hermesSessionId);
        showHermesToast('Hermes 操作已写入', 'success');
    } catch (e) {
        showHermesToast('写入失败: ' + e.message, 'error');
    }
}

function startHermesSession() {
    hermesSessionId = '';
    hermesActiveDraft = null;
    localStorage.removeItem('hermesSessionId');
    document.getElementById('hermesSessionTitle').textContent = '新会话';
    document.getElementById('hermesSessionMeta').textContent = '写库操作会先生成草稿，确认后执行。';
    renderHermesDraft(null);
    renderHermesEvents([]);
    renderHermesSessions(hermesSessions);
    document.getElementById('hermesInput')?.focus();
}

function fillHermesExample(text) {
    const input = document.getElementById('hermesInput');
    if (!input) return;
    input.value = text;
    input.focus();
}

function showHermesToast(msg, type) {
    let t = document.getElementById('globalToast');
    if (!t) {
        t = document.createElement('div');
        t.id = 'globalToast';
        t.style.cssText = 'position:fixed;top:60px;right:20px;z-index:99999;padding:12px 20px;border-radius:8px;font-size:0.9rem;font-weight:600;box-shadow:0 4px 16px rgba(0,0,0,0.15);transition:opacity 0.3s;max-width:360px;';
        document.body.appendChild(t);
    }
    const colors = {success: '#52B788', error: '#E07A5F', warning: '#F4A261'};
    t.style.background = colors[type] || colors.success;
    t.style.color = '#fff';
    t.textContent = msg;
    t.style.opacity = '1';
    clearTimeout(window._hermesToastTimer);
    window._hermesToastTimer = setTimeout(() => { t.style.opacity = '0'; }, 3500);
}

function formatHermesTime(value) {
    if (!value) return '';
    const date = new Date(String(value).replace(' ', 'T'));
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString('zh-CN', {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'});
}

function escapeHtml(str) {
    return String(str ?? '').replace(/[&<>"']/g, m => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    }[m]));
}

function escapeAttr(str) {
    return escapeHtml(str).replace(/`/g, '&#96;');
}

Object.assign(window, {
    loadHermesSessions,
    loadHermesSession,
    filterHermesSessions,
    sendHermesMessage,
    confirmHermesDraft,
    renderHermesDraft,
    startHermesSession,
    fillHermesExample,
});
