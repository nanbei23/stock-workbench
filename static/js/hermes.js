let hermesSessionId = localStorage.getItem('hermesSessionId') || '';
let hermesActiveDraft = null;
let hermesSessions = [];

document.addEventListener('DOMContentLoaded', async () => {
    bindHermesInputShortcuts();
    await loadHermesSessions();
    await loadHermesTasks();
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

async function loadHermesTasks() {
    const panel = document.getElementById('hermesTasksPanel');
    if (!panel) return;
    panel.innerHTML = '<div class="empty-row">读取任务...</div>';
    try {
        const data = await hermesRequestJson('/api/hermes/tasks?limit=12');
        renderHermesTasks(data.tasks || []);
    } catch (e) {
        panel.innerHTML = `<div class="empty-row">任务读取失败：${escapeHtml(e.message)}</div>`;
    }
}

function renderHermesTasks(tasks) {
    const panel = document.getElementById('hermesTasksPanel');
    if (!panel) return;
    if (!tasks.length) {
        panel.innerHTML = '<div class="empty-row">暂无多步任务</div>';
        return;
    }
    panel.innerHTML = tasks.map(task => {
        const progress = `${task.write_done || 0}/${task.write_total || 0}`;
        const steps = (task.steps || []).slice(0, 4).map(step => {
            return `<span class="${escapeAttr(step.status || '')}">${escapeHtml(step.title || step.summary || step.step_id)}</span>`;
        }).join('');
        return `<button class="hermes-task-item ${escapeAttr(task.status || '')}" onclick="loadHermesSession('${escapeAttr(task.session_id)}')">
            <span><b>${escapeHtml(task.title || task.summary || task.task_id)}</b><small>${escapeHtml(formatHermesTime(task.updated_at || task.created_at))} · ${escapeHtml(planStatusLabel(task.status || 'waiting_confirm'))} · ${progress}</small></span>
            <div class="hermes-task-steps">${steps}</div>
        </button>`;
    }).join('');
}

function hermesPost(url, data = {}) {
    return hermesRequestJson(url, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data)
    });
}

function bindHermesInputShortcuts() {
    const input = document.getElementById('hermesInput');
    if (!input) return;
    input.addEventListener('input', () => {
        input.style.height = 'auto';
        input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
    });
    input.addEventListener('keydown', event => {
        if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return;
        event.preventDefault();
        sendHermesMessage(event);
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
        await loadHermesToolRuns(sessionId);
        const found = hermesSessions.find(item => item.session_id === sessionId);
        document.getElementById('hermesSessionTitle').textContent = found?.title || sessionId;
        document.getElementById('hermesSessionMeta').textContent = `${events.length} 条消息 · ${found?.last_at ? formatHermesTime(found.last_at) : '当前会话'}`;
        const latestDraftEvent = [...events].reverse().find(item => item.draft);
        const latestResultEvent = [...events].reverse().find(item => item.result);
        const latestDraft = latestDraftEvent?.draft || found?.last_draft || null;
        const latestResult = latestResultEvent?.result || found?.last_result || null;
        const resultBelongsToDraft = latestDraftEvent && latestResultEvent
            ? Number(latestResultEvent.id || 0) >= Number(latestDraftEvent.id || 0)
            : Boolean(latestResult && !latestDraftEvent);
        renderHermesDraft(latestDraft && !isHermesDraftTerminal(latestDraft, resultBelongsToDraft ? latestResult : null) ? latestDraft : null);
    } catch (e) {
        appendHermesMessage('assistant', `会话读取失败：${e.message}`);
    }
}

function isHermesDraftTerminal(draft, result) {
    if (!draft || !result) return false;
    if (draft.action === 'multi_step_plan') {
        if (result.action === 'multi_step_plan' && ['ok', 'cancelled'].includes(result.status)) return true;
        const writeSteps = (draft.plan_steps || []).filter(step => step.kind === 'write');
        return writeSteps.length > 0 && writeSteps.every(step => ['ok', 'skipped'].includes(step.status));
    }
    return ['ok', 'cancelled'].includes(result.status);
}

async function loadHermesToolRuns(sessionId) {
    const panel = document.getElementById('hermesToolRunsPanel');
    if (!panel) return;
    if (!sessionId) {
        renderHermesToolRuns([]);
        return;
    }
    panel.innerHTML = '<div class="empty-row">读取审计记录...</div>';
    try {
        const data = await hermesRequestJson(`/api/hermes/session/${encodeURIComponent(sessionId)}/tool-runs?limit=20`);
        renderHermesToolRuns(data.runs || []);
    } catch (e) {
        panel.innerHTML = `<div class="empty-row">审计读取失败：${escapeHtml(e.message)}</div>`;
    }
}

function renderHermesToolRuns(runs) {
    const panel = document.getElementById('hermesToolRunsPanel');
    if (!panel) return;
    if (!runs.length) {
        panel.innerHTML = '<div class="empty-row">暂无工具调用</div>';
        return;
    }
    panel.innerHTML = runs.map(run => {
        const status = run.status || 'pending';
        const args = run.args || {};
        const stock = [args.name, args.code].filter(Boolean).join(' ') || args.code || '';
        const detail = hermesToolRunDetail(run);
        return `<div class="hermes-tool-run ${escapeAttr(status)}">
            <div class="hermes-tool-run-head">
                <span>${escapeHtml(toolLabel(run.tool))}</span>
                <strong>${escapeHtml(toolStatusLabel(status))}</strong>
            </div>
            <div class="hermes-tool-run-meta">${escapeHtml(formatHermesTime(run.confirmed_at || run.created_at))}${stock ? ` · ${escapeHtml(stock)}` : ''}</div>
            ${detail ? `<div class="hermes-tool-run-detail">${escapeHtml(detail)}</div>` : ''}
            ${run.error ? `<div class="hermes-tool-run-error">${escapeHtml(run.error)}</div>` : ''}
        </div>`;
    }).join('');
}

function hermesToolRunDetail(run) {
    const args = run.args || {};
    if (run.tool === 'record_trade') {
        return `${args.direction === 'sell' ? '卖出' : '买入'} ${args.shares || 0} 股 @ ${args.price || '-'}`;
    }
    if (run.tool === 'set_position') {
        return `目标持仓 ${args.shares ?? '-'} 股${args.price ? `，参考价 ${args.price}` : ''}`;
    }
    if (run.tool === 'create_conditional_order') {
        return `${args.trade_action === 'sell' ? '卖出' : '买入'} · ${conditionLabel(args.condition_type)} ${args.target_price || '-'}`;
    }
    if (run.tool === 'add_watchlist') {
        return '加入默认自选分组';
    }
    return '';
}

function toolStatusLabel(status) {
    return {ok: '已写入', error: '失败', pending: '待执行', cancelled: '已取消', skipped: '已跳过'}[status] || status;
}

function toolLabel(tool) {
    return {
        add_watchlist: '添加自选股',
        record_trade: '记录交易',
        set_position: '校准持仓',
        create_conditional_order: '创建条件单',
    }[tool] || tool || '工具调用';
}

function renderHermesEvents(events) {
    const el = document.getElementById('hermesMessages');
    if (!el) return;
    if (!events.length) {
        el.innerHTML = hermesMessageHtml('assistant', '你可以像和交易助理说话一样描述需求。我会先理解成草稿，涉及写库的动作一定等你确认。');
        return;
    }
    el.innerHTML = events.map(event => {
        const role = event.role === 'user' ? 'user' : event.role === 'tool' ? 'tool' : 'assistant';
        const extras = [
            event.draft ? `<div class="hermes-history-draft">${escapeHtml(event.draft.summary || event.draft.label || '草稿')}</div>` : '',
            event.result ? `<div class="hermes-history-result">${escapeHtml(event.result.summary || event.result.label || '已执行')}</div>` : '',
        ].join('');
        return hermesMessageHtml(role, event.message || '', extras, event.created_at);
    }).join('');
    el.scrollTop = el.scrollHeight;
}

function appendHermesMessage(role, text) {
    const el = document.getElementById('hermesMessages');
    if (!el) return;
    const wrap = document.createElement('div');
    wrap.innerHTML = hermesMessageHtml(role, text || '');
    const item = wrap.firstElementChild;
    el.appendChild(item);
    el.scrollTop = el.scrollHeight;
    return item;
}

function appendHermesTyping() {
    const el = document.getElementById('hermesMessages');
    if (!el) return null;
    const wrap = document.createElement('div');
    wrap.innerHTML = hermesMessageHtml('assistant', '<span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>', '', '', true);
    const item = wrap.firstElementChild;
    el.appendChild(item);
    el.scrollTop = el.scrollHeight;
    return item;
}

function hermesMessageHtml(role, text, extras = '', createdAt = '', trustedHtml = false) {
    const meta = hermesRoleMeta(role);
    const time = createdAt ? formatHermesTime(createdAt) : formatHermesTime(new Date().toISOString());
    const content = trustedHtml ? text : escapeHtml(text || '');
    return `<div class="hermes-message ${escapeAttr(role)}">
        <div class="hermes-avatar" title="${escapeAttr(meta.name)}">${escapeHtml(meta.mark)}</div>
        <div class="hermes-message-stack">
            <div class="hermes-message-meta"><span>${escapeHtml(meta.name)}</span><time>${escapeHtml(time)}</time></div>
            <div class="hermes-bubble">${content}${extras || ''}</div>
        </div>
    </div>`;
}

function hermesRoleMeta(role) {
    if (role === 'user') return {name: '你', mark: 'ME'};
    if (role === 'tool') return {name: '本地写入', mark: 'DB'};
    return {name: 'Hermes', mark: 'HM'};
}

function renderHermesDraft(draft) {
    const el = document.getElementById('hermesDraftPanel');
    if (!el) return;
    hermesActiveDraft = draft || null;
    if (!draft) {
        el.innerHTML = '<div class="empty-row">暂无待确认草稿</div>';
        return;
    }
    if (draft.action === 'multi_step_plan') {
        renderHermesPlanDraft(el, draft);
        return;
    }
    const risks = (draft.risks || []).map(x => `<li>${escapeHtml(x)}</li>`).join('');
    const blockers = (draft.blockers || []).map(x => `<li>${escapeHtml(x)}</li>`).join('');
    const completions = (draft.completion_sources || []).map(x => `<li>${escapeHtml(x)}</li>`).join('');
    const payloadRows = formatHermesDraftRows(draft);
    const parserLabel = (draft.parser || 'rules') === 'llm' ? '模型理解' : '本地兜底';
    const riskBadge = renderHermesRiskBadge(draft.risk_level);
    const toolRows = formatHermesToolRows(draft.tool_call);
    const impact = renderHermesImpactPreview(draft.impact_preview);
    el.innerHTML = `<div class="hermes-draft-head">
            <span>${escapeHtml(draft.label || '操作草稿')}${riskBadge}</span>
            <strong>${escapeHtml(draft.executable ? '等你确认' : '需要补充')}</strong>
        </div>
        <div class="hermes-draft-summary">${escapeHtml(draft.summary || '')}</div>
        <div class="hermes-draft-parser">${escapeHtml(parserLabel)}</div>
        ${payloadRows ? `<div class="hermes-draft-payload">${payloadRows}</div>` : ''}
        ${toolRows ? `<div class="hermes-tool-call"><div class="hermes-tool-call-title">确认后执行</div>${toolRows}</div>` : ''}
        ${impact}
        ${completions ? `<div class="hermes-draft-note completion"><b>已自动补全</b><ul>${completions}</ul></div>` : ''}
        ${risks ? `<div class="hermes-draft-note"><b>确认前看一眼</b><ul>${risks}</ul></div>` : ''}
        ${blockers ? `<div class="hermes-draft-note blocked"><b>还差这些信息</b><ul>${blockers}</ul></div>` : ''}
        <div class="hermes-draft-actions">
            <button class="btn btn-sm" onclick="cancelHermesDraft()">取消草稿</button>
            <button class="btn btn-primary btn-sm" ${draft.executable ? '' : 'disabled'} onclick="confirmHermesDraft()">确认写入</button>
        </div>`;
}

function renderHermesPlanDraft(el, draft) {
    const risks = (draft.risks || []).map(x => `<li>${escapeHtml(x)}</li>`).join('');
    const blockers = (draft.blockers || []).map(x => `<li>${escapeHtml(x)}</li>`).join('');
    const completions = (draft.completion_sources || []).map(x => `<li>${escapeHtml(x)}</li>`).join('');
    const steps = (draft.plan_steps || []).map(step => renderHermesPlanStep(step)).join('');
    const parserLabel = (draft.parser || 'rules') === 'llm' ? '模型计划' : '本地计划';
    el.innerHTML = `<div class="hermes-draft-head">
            <span>${escapeHtml(draft.label || '多步任务计划')}</span>
            <strong>${escapeHtml(draft.executable ? '等你确认' : '需要补充')}</strong>
        </div>
        <div class="hermes-draft-summary">${escapeHtml(draft.summary || '')}</div>
        <div class="hermes-draft-parser">${escapeHtml(parserLabel)}</div>
        <div class="hermes-plan-steps">${steps || '<div class="empty-row">没有可展示步骤</div>'}</div>
        ${completions ? `<div class="hermes-draft-note completion"><b>已自动补全</b><ul>${completions}</ul></div>` : ''}
        ${risks ? `<div class="hermes-draft-note"><b>确认前看一眼</b><ul>${risks}</ul></div>` : ''}
        ${blockers ? `<div class="hermes-draft-note blocked"><b>还差这些信息</b><ul>${blockers}</ul></div>` : ''}
        <div class="hermes-draft-actions">
            <button class="btn btn-sm" onclick="cancelHermesDraft()">取消计划</button>
            <button class="btn btn-primary btn-sm" ${draft.executable ? '' : 'disabled'} onclick="confirmHermesDraft()">确认执行计划</button>
        </div>`;
}

function renderHermesPlanStep(step) {
    const payload = step.payload || {};
    const stock = [payload.name, payload.code].filter(Boolean).join(' ');
    const blockers = (step.blockers || []).map(item => `<li>${escapeHtml(item)}</li>`).join('');
    const meta = [
        step.kind === 'read' ? '只读预览' : '写库确认',
        step.status ? planStatusLabel(step.status) : '',
        stock,
    ].filter(Boolean).join(' · ');
    const toolRows = step.tool_call ? formatHermesToolRows(step.tool_call) : '';
    const impact = renderHermesImpactPreview(step.impact_preview, true);
    const actions = renderHermesPlanStepActions(step);
    const riskBadge = renderHermesRiskBadge(step.risk_level, true);
    const result = step.kind === 'read' && step.result?.answer
        ? `<div class="hermes-plan-result">${escapeHtml(step.result.answer)}</div>`
        : '';
    const writeResult = step.kind === 'write' && step.result?.status
        ? `<div class="hermes-plan-result">${escapeHtml(step.result.status === 'skipped' ? '此步骤已跳过。' : '此步骤已执行。')}</div>`
        : '';
    const summaryText = step.kind === 'read' && step.result?.answer === step.summary ? '' : (step.summary || '');
    return `<div class="hermes-plan-step ${escapeAttr(step.kind || '')} ${escapeAttr(step.status || '')}">
        <div class="hermes-plan-step-head">
            <span>${escapeHtml(String(step.index || ''))}</span>
            <div>
                <b>${escapeHtml(step.title || step.label || step.summary || '任务步骤')}${riskBadge}</b>
                <small>${escapeHtml(meta)}</small>
            </div>
        </div>
        ${summaryText ? `<div class="hermes-plan-step-summary">${escapeHtml(summaryText)}</div>` : ''}
        ${result}
        ${writeResult}
        ${toolRows ? `<div class="hermes-tool-call compact">${toolRows}</div>` : ''}
        ${impact}
        ${blockers ? `<div class="hermes-draft-note blocked"><b>阻塞项</b><ul>${blockers}</ul></div>` : ''}
        ${actions}
    </div>`;
}

function renderHermesRiskBadge(level, compact = false) {
    if (!level) return '';
    const labels = {low: '低风险', medium: '中风险', high: '高风险'};
    return `<em class="hermes-risk-badge ${escapeAttr(level)} ${compact ? 'compact' : ''}">${escapeHtml(labels[level] || level)}</em>`;
}

function renderHermesPlanStepActions(step) {
    if (step.kind !== 'write') return '';
    const status = step.status || 'ready';
    if (['ok', 'skipped', 'running'].includes(status)) return '';
    const disabled = step.executable ? '' : 'disabled';
    return `<div class="hermes-plan-step-actions">
        <button class="btn btn-sm" onclick="skipHermesPlanStep('${escapeAttr(step.id)}')">跳过此步</button>
        <button class="btn btn-primary btn-sm" ${disabled} onclick="confirmHermesPlanStep('${escapeAttr(step.id)}')">${status === 'error' ? '重试此步' : '确认此步'}</button>
    </div>`;
}

function renderHermesImpactPreview(preview, compact = false) {
    if (!preview) return '';
    const items = (preview.items || [])
        .map(item => `<div><span>${escapeHtml(item.label)}</span><b>${escapeHtml(String(item.value ?? ''))}</b></div>`)
        .join('');
    const warnings = (preview.warnings || []).map(item => `<li>${escapeHtml(item)}</li>`).join('');
    return `<div class="hermes-impact-preview ${escapeAttr(preview.status || '')} ${compact ? 'compact' : ''}">
        <div class="hermes-impact-title">影响预览</div>
        ${preview.summary ? `<p>${escapeHtml(preview.summary)}</p>` : ''}
        ${items ? `<div class="hermes-impact-items">${items}</div>` : ''}
        ${warnings ? `<ul class="hermes-impact-warnings">${warnings}</ul>` : ''}
    </div>`;
}

function planStatusLabel(status) {
    return {
        done: '已预览',
        ready: '待确认',
        waiting_confirm: '待确认',
        running: '执行中',
        blocked: '有缺失',
        ok: '已完成',
        skipped: '已跳过',
        error: '失败',
    }[status] || status;
}

function formatHermesToolRows(toolCall) {
    if (!toolCall?.tool) return '';
    const args = toolCall.args || {};
    const rows = [`<div><span>工具</span><b>${escapeHtml(toolLabel(toolCall.tool))}</b></div>`];
    const safeArgs = Object.entries(args)
        .filter(([, value]) => value !== '' && value !== null && value !== undefined)
        .map(([key, value]) => `${key}: ${value}`)
        .join(' · ');
    if (safeArgs) rows.push(`<div><span>参数</span><b>${escapeHtml(safeArgs)}</b></div>`);
    if (toolCall.reason) rows.push(`<div><span>依据</span><b>${escapeHtml(toolCall.reason)}</b></div>`);
    return rows.join('');
}

function formatHermesDraftRows(draft) {
    const payload = draft.payload || {};
    const stock = [payload.name, payload.code].filter(Boolean).join(' ');
    const action = draft.action;
    const rows = [];
    const push = (label, value) => {
        if (value === '' || value === null || value === undefined) return;
        rows.push(`<div><span>${escapeHtml(label)}</span><b>${escapeHtml(String(value))}</b></div>`);
    };
    push('股票', stock);
    if (action === 'record_trade') {
        push('方向', payload.direction === 'sell' ? '卖出' : '买入');
        push('数量', payload.shares ? `${payload.shares} 股` : '');
        push('成交价', payload.price ? `${payload.price}` : '待补充');
    } else if (action === 'set_position') {
        push('目标持仓', payload.shares ? `${payload.shares} 股` : '');
        push('参考价格', payload.price || '沿用现有均价');
    } else if (action === 'create_conditional_order') {
        push('方向', payload.trade_action === 'sell' ? '卖出' : '买入');
        push('触发条件', `${conditionLabel(payload.condition_type)} ${payload.target_price || '待补充'}`);
        push('数量', payload.shares ? `${payload.shares} 股` : '未指定');
    } else if (action === 'add_watchlist') {
        push('加入分组', '默认');
    }
    return rows.join('');
}

function conditionLabel(value) {
    return {
        price_lte: '价格不高于',
        price_gte: '价格不低于',
        change_pct_gte: '涨幅达到',
        change_pct_lte: '跌幅达到',
    }[value] || value || '';
}

async function sendHermesMessage(event) {
    event?.preventDefault();
    const input = document.getElementById('hermesInput');
    const btn = document.getElementById('hermesSendBtn');
    const message = (input?.value || '').trim();
    if (!message) return;
    appendHermesMessage('user', message);
    if (input) input.value = '';
    if (input) input.style.height = '';
    if (btn) {
        btn.disabled = true;
        btn.dataset.originalText = btn.textContent || '发送';
        btn.textContent = '理解中';
    }
    const typing = appendHermesTyping();
    try {
        const data = await hermesPost('/api/hermes/message', {message, session_id: hermesSessionId || null});
        hermesSessionId = data.session_id || hermesSessionId;
        if (hermesSessionId) localStorage.setItem('hermesSessionId', hermesSessionId);
        typing?.remove();
        appendHermesMessage('assistant', data.answer || '我处理好了。');
        renderHermesDraft(data.draft || null);
        await loadHermesSessions();
        await loadHermesTasks();
        renderHermesSessions(hermesSessions);
        document.getElementById('hermesSessionTitle').textContent = hermesSessions.find(s => s.session_id === hermesSessionId)?.title || '当前会话';
    } catch (e) {
        typing?.remove();
        appendHermesMessage('assistant', `处理失败：${e.message}`);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = btn.dataset.originalText || '发送';
        }
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
        await loadHermesTasks();
        await loadHermesSession(hermesSessionId);
        showHermesToast('Hermes 操作已写入', 'success');
    } catch (e) {
        showHermesToast('写入失败: ' + e.message, 'error');
    }
}

async function confirmHermesPlanStep(stepId) {
    if (!hermesActiveDraft || !hermesSessionId || !stepId) return;
    try {
        const result = await hermesPost('/api/hermes/step/confirm', {
            session_id: hermesSessionId,
            draft_id: hermesActiveDraft.id,
            step_id: stepId
        });
        appendHermesMessage('tool', `已执行步骤：${result.summary || stepId}`);
        await loadHermesSessions();
        await loadHermesTasks();
        await loadHermesSession(hermesSessionId);
        showHermesToast('步骤已执行', 'success');
    } catch (e) {
        showHermesToast('步骤执行失败: ' + e.message, 'error');
    }
}

async function skipHermesPlanStep(stepId) {
    if (!hermesActiveDraft || !hermesSessionId || !stepId) return;
    try {
        const result = await hermesPost('/api/hermes/step/skip', {
            session_id: hermesSessionId,
            draft_id: hermesActiveDraft.id,
            step_id: stepId
        });
        appendHermesMessage('tool', `已跳过步骤：${result.summary || stepId}`);
        await loadHermesSessions();
        await loadHermesTasks();
        await loadHermesSession(hermesSessionId);
        showHermesToast('步骤已跳过', 'success');
    } catch (e) {
        showHermesToast('跳过失败: ' + e.message, 'error');
    }
}

async function cancelHermesDraft() {
    if (!hermesActiveDraft || !hermesSessionId) {
        renderHermesDraft(null);
        return;
    }
    const draft = hermesActiveDraft;
    try {
        const result = await hermesPost('/api/hermes/cancel', {
            session_id: hermesSessionId,
            draft_id: draft.id
        });
        appendHermesMessage('tool', `已取消草稿：${result.summary || draft.summary || '操作草稿'}`);
        renderHermesDraft(null);
        await loadHermesSessions();
        await loadHermesTasks();
        await loadHermesSession(hermesSessionId);
        showHermesToast('草稿已取消', 'success');
    } catch (e) {
        showHermesToast('取消失败: ' + e.message, 'error');
    }
}

function startHermesSession() {
    hermesSessionId = '';
    hermesActiveDraft = null;
    localStorage.removeItem('hermesSessionId');
    document.getElementById('hermesSessionTitle').textContent = '新会话';
    document.getElementById('hermesSessionMeta').textContent = '写库操作会先生成草稿，确认后执行。';
    renderHermesDraft(null);
    renderHermesToolRuns([]);
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
    loadHermesTasks,
    loadHermesSession,
    filterHermesSessions,
    loadHermesToolRuns,
    sendHermesMessage,
    confirmHermesDraft,
    confirmHermesPlanStep,
    skipHermesPlanStep,
    cancelHermesDraft,
    renderHermesDraft,
    startHermesSession,
    fillHermesExample,
});
