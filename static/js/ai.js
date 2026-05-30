/**
 * AI分析台 — v2 重构版
 * 左栏:自选股卡片(单选/批量选) | 中栏:指数+控制+卡通/进度/报告 | 右栏:异动+历史
 */

// 7档信号中文标签
const SIG_LABEL = {
    STRONG_BUY:'强烈买入', BUY:'买入', OVERWEIGHT:'增持',
    HOLD:'持有', UNDERWEIGHT:'减持', SELL:'卖出', STRONG_SELL:'强烈卖出'
};

document.addEventListener('DOMContentLoaded', async () => {
    await Promise.all([
        loadAIStockCards(),
        loadReports(),
        loadIndices(),
        loadTaskCenter(),
        loadReportQuality(),
        loadAiReadiness(),
        loadStrategyReview(),
        loadRiskExposure(),
        loadEventsPanel()
    ]);
    initIdleStages();
    pollQueueStatus();
    setInterval(pollQueueStatus, 10000);
    pollAnomalies();
    setInterval(pollAnomalies, 30000);
    restoreActiveTask();
    handleResponsiveLayout();
    window.addEventListener('resize', handleResponsiveLayout);
});

let selectedCardCode = null;
let currentTaskId = null;
let currentSSE = null;
let activeAnalysisCode = null;
let currentDepth = 'standard';
let currentModelMode = 'balanced';
let reportCodes = new Set();

const DEPTH_CONFIG = {
    quick: { analysts: ['market','fundamentals'], debate_rounds: 1, risk_rounds: 1, label: '快速模式' },
    standard: { analysts: ['market','social','news','fundamentals','policy','hot_money','lockup'], debate_rounds: 1, risk_rounds: 1, label: '标准模式' },
    deep: { analysts: ['market','social','news','fundamentals','policy','hot_money','lockup'], debate_rounds: 3, risk_rounds: 3, label: '深度模式' },
    custom: { analysts: [], debate_rounds: 1, risk_rounds: 1, label: '自定义模式' }
};

// 必选项
const MANDATORY_STAGES = new Set(['market', 'fundamentals', 'quality_gate', 'trader', 'pm']);
const ALL_ANALYSTS = ['market','social','news','fundamentals','policy','hot_money','lockup'];
const ALL_PIPELINE = ['quality_gate','debate','trader','risk','pm'];
const STAGE_META = {
    market: { n: '技术', code: 'TA', c: 'sky' },
    social: { n: '情绪', code: 'SN', c: 'purple' },
    news: { n: '新闻', code: 'NW', c: 'gold' },
    fundamentals: { n: '基本面', code: 'FA', c: 'green' },
    policy: { n: '政策', code: 'PL', c: 'rose' },
    hot_money: { n: '游资', code: 'FM', c: 'orange' },
    lockup: { n: '解禁', code: 'LU', c: 'cyan' },
    quality_gate: { n: '门控', code: 'QA', c: 'mint' },
    debate: { n: '多空', code: 'DB', c: 'indigo' },
    trader: { n: '交易', code: 'TR', c: 'coral' },
    risk: { n: '风控', code: 'RK', c: 'slate' },
    pm: { n: '决策', code: 'PM', c: 'gold' }
};
const REPORT_STAGE_LABELS = {
    market: '技术分析',
    social: '情绪面',
    news: '新闻',
    fundamentals: '基本面',
    policy: '政策',
    hot_money: '资金流',
    lockup: '解禁'
};
const FACT_CHECK_STAGE_ORDER = ['market','social','news','fundamentals','policy','hot_money','lockup'];

function renderStageAvatar(id, state = 'idle', suffix = '') {
    const meta = STAGE_META[id] || { n: id, code: id.slice(0, 2).toUpperCase(), c: 'slate' };
    return `<div class="avatar-card ${state}" data-color="${meta.c}" id="stage-${id}">
        <div class="avatar-mark">${meta.code}</div>
        <div class="avatar-name">${meta.n}${suffix}</div>
    </div>`;
}

function statusLabel(status) {
    if (status === 'verified') return '<span class="status-pill status-ok">一致</span>';
    if (status === 'mismatch') return '<span class="status-pill status-bad">差异</span>';
    return '<span class="status-pill status-warn">待核</span>';
}

function showBottomPanel(panel) {
    const top = document.getElementById('aiCenterTop');
    const progress = document.getElementById('centerProgress');
    const report = document.getElementById('centerReport');
    // 报告态：隐藏顶部控制区
    if (top) top.style.display = panel === 'report' ? 'none' : 'flex';
    if (progress) progress.style.display = panel === 'progress' ? 'block' : 'none';
    if (report) report.style.display = panel === 'report' ? 'flex' : 'none';
}
function toggleLeftPanel() { document.getElementById('aiContainer')?.classList.toggle('left-collapsed'); }
function toggleRightPanel() { document.getElementById('aiContainer')?.classList.toggle('right-collapsed'); }
function handleResponsiveLayout() {
    const c = document.getElementById('aiContainer'); if (!c) return;
    const w = window.innerWidth;
    if (w <= 1024 && w > 768) c.classList.add('left-collapsed','right-collapsed');
    else c.classList.remove('left-collapsed','right-collapsed');
}

async function aiRequestJson(url, options = {}) {
    const resp = await fetch(url, options);
    const contentType = resp.headers.get('content-type') || '';
    if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${url}`);
    if (!contentType.includes('application/json')) throw new Error(`接口返回非 JSON: ${url}`);
    return resp.json();
}

async function aiGet(url) { return aiRequestJson(url); }
async function aiPost(url, data = {}) {
    return aiRequestJson(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
}

function aiTaskClient() {
    if (!window.AiTaskClient) throw new Error('AI 任务客户端未加载');
    return window.AiTaskClient;
}

function initIdleStages() {
    document.getElementById('analystStages').innerHTML = `<div class="avatar-selfie">${ALL_ANALYSTS.map(id => renderStageAvatar(id)).join('')}</div>`;
    document.getElementById('pipelineStages').innerHTML = `<div class="avatar-pipeline">${ALL_PIPELINE.map(id => renderStageAvatar(id)).join('')}</div>`;
    document.getElementById('progressBar').style.width = '0%';
    document.getElementById('progressText').textContent = '';
}

// === 左栏：自选股卡片 ===
async function loadAIStockCards() {
    try {
        const data = await aiGet('/api/watchlist');
        const stocks = data.stocks || [];
        const container = document.getElementById('aiStockList');
        if (!stocks.length) { container.innerHTML = '<div style="text-align:center;padding:24px;color:var(--text-muted)">暂无自选股</div>'; return; }
        container.innerHTML = stocks.map(s => {
            const cp = s.change_pct ?? 0;
            const cls = cp > 0 ? 'up' : cp < 0 ? 'down' : 'flat';
            const barCls = cls;
            const pctSign = cp >= 0 ? '+' : '';
            const chgSign = s.change != null ? (s.change >= 0 ? '+' : '') : '';
            const dailyPnl = s.daily_pnl ? formatPnlJS(s.daily_pnl) : (s.change != null ? (chgSign + s.change.toFixed(2) + '元') : '--');
            const dailyPnlCls = s.daily_pnl ? (s.daily_pnl >= 0 ? 'up' : 'down') : cls;
            const holdPnl = s.unrealized_pnl ? formatPnlJS(s.unrealized_pnl) : '--';
            const holdPnlCls = s.unrealized_pnl ? (s.unrealized_pnl >= 0 ? 'up' : 'down') : '';

            return `<div class="ai-stock-card" data-code="${escapeAttr(s.code)}" onclick="selectCard('${escapeAttr(s.code)}')">
                <div class="ai-stock-card-inner">
                    <div class="ai-sc-check" onclick="event.stopPropagation()"><input type="checkbox" data-code="${escapeAttr(s.code)}" onchange="updateBatchBar()"></div>
                    <div class="sc-left">
                        <div><div class="sc-name">${escapeHtml(s.name||s.code)}</div><div class="sc-code">${escapeHtml(s.code)}</div></div>
                        <div class="sc-price ${cls}">${(s.price??0).toFixed(2)}<span class="sc-price-unit">元</span></div>
                    </div>
                    <div class="sc-right">
                        <div class="sc-data-row"><span class="sc-data-lbl">当日盈亏</span><span class="sc-data-val ${dailyPnlCls}">${dailyPnl}</span></div>
                        <div class="sc-data-row"><span class="sc-data-lbl">持仓盈亏</span><span class="sc-data-val ${holdPnlCls}">${holdPnl}</span></div>
                        <div class="sc-data-row"><span class="sc-data-lbl">当日涨幅</span><span class="sc-data-val ${cls}">${pctSign}${cp.toFixed(2)}%</span></div>
                    </div>
                </div>
                <div class="stock-card-bar ${barCls}"></div>
            </div>`;
        }).join('');
    } catch (e) { console.error('加载失败:', e); }
}

function formatPnlJS(val) {
    if (val == null) return '--';
    const sign = val >= 0 ? '+' : '';
    return sign + val.toFixed(2) + '元';
}

function selectCard(code) {
    document.querySelectorAll('.ai-stock-card').forEach(c => c.classList.remove('selected'));
    if (selectedCardCode === code) { selectedCardCode = null; document.getElementById('selectedStockInfo').textContent = ''; }
    else {
        selectedCardCode = code;
        document.querySelector(`.ai-stock-card[data-code="${code}"]`)?.classList.add('selected');
        const name = document.querySelector(`.ai-stock-card[data-code="${code}"] .sc-name`)?.textContent || code;
        document.getElementById('selectedStockInfo').textContent = `已选: ${name} (${code})`;
        loadReportVersions();
    }
}

function getSelectedCodes() { return Array.from(document.querySelectorAll('.ai-sc-check input:checked')).map(cb => cb.dataset.code); }
function updateBatchBar() {
    const n = getSelectedCodes().length;
    const bar = document.getElementById('batchBar');
    document.getElementById('batchCount').textContent = `已选 ${n} 只`;
    bar.style.display = n > 0 ? '' : 'none';
}
function clearSelection() {
    document.querySelectorAll('.ai-sc-check input').forEach(cb => cb.checked = false);
    updateBatchBar();
    document.getElementById('aiStockList')?.classList.remove('batch-active');
    const btn = document.getElementById('batchToggleBtn');
    if (btn) btn.classList.remove('active');
}
function toggleBatchMode() {
    const list = document.getElementById('aiStockList');
    const btn = document.getElementById('batchToggleBtn');
    const active = list.classList.toggle('batch-active');
    btn.classList.toggle('active', active);
    if (!active) clearSelection();
}

async function batchAnalyze() {
    const codes = getSelectedCodes();
    if (!codes.length) return alert('请至少选择一只股票');
    try {
        const dc = getActiveDepthConfig();
        if (!dc) return;
        const resp = await aiTaskClient().batch({
            codes,
            depth: currentDepth,
            selected_analysts: dc.analysts,
            debate_rounds: dc.debate_rounds,
            risk_rounds: dc.risk_rounds,
            mode: currentModelMode
        });
        showToast(resp.message || `已提交 ${resp.count} 个分析任务`, 'success');
        clearSelection();
        pollQueueStatus();
        loadTaskCenter();
    } catch (e) {
        showToast('批量提交失败: ' + e.message, 'error');
    }
}

// === 指数 ===
async function loadIndices() {
    try { const d = await aiGet('/api/ai/suggestions'); renderIndices(d.indices||[]); } catch(e) {}
}
function renderIndices(indices) {
    const bar = document.getElementById('indicesBar'); if (!bar||!indices.length) return;
    bar.innerHTML = indices.map(i => `<div class="index-item"><span class="index-name">${escapeHtml(i.name)}</span><span class="index-price">${(i.price??0).toLocaleString()}</span><span class="index-change ${(i.change_pct??0)>=0?'price-up':'price-down'}">${(i.change_pct??0)>=0?'+':''}${(i.change_pct??0).toFixed(2)}%</span></div>`).join('');
}

// === 模型模式 + 深度 ===
function setModelMode(mode) {
    currentModelMode = mode;
    const sel = document.getElementById('modelModeSelect');
    if (sel) sel.value = mode;
    fetch('/api/settings/model_mode', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value: mode }) }).catch(()=>{});
}
function setResearchDepth(depth) {
    currentDepth = depth;
    const sel = document.getElementById('depthSelect');
    if (sel) sel.value = depth;
    if (depth === 'custom') {
        enterCustomMode();
    } else {
        exitCustomMode();
    }
}

function enterCustomMode() {
    // 重置所有卡片为 selectable 态，必选项锁定
    const allStages = [...ALL_ANALYSTS, ...ALL_PIPELINE];
    allStages.forEach(id => {
        const el = document.getElementById(`stage-${id}`);
        if (!el) return;
        el.classList.remove('idle','pending','selected','locked','selectable');
        if (MANDATORY_STAGES.has(id)) {
            el.classList.add('locked');
        } else {
            el.classList.add('selectable');
        }
        el.onclick = () => toggleStage(id);
    });
}

function exitCustomMode() {
    const allStages = [...ALL_ANALYSTS, ...ALL_PIPELINE];
    allStages.forEach(id => {
        const el = document.getElementById(`stage-${id}`);
        if (!el) return;
        el.classList.remove('selectable','selected','locked');
        el.onclick = null;
    });
    initIdleStages();
}

function toggleStage(id) {
    if (MANDATORY_STAGES.has(id)) return;
    const el = document.getElementById(`stage-${id}`);
    if (!el) return;
    el.classList.toggle('selected');
}

function getCustomSelectedStages() {
    const analysts = ALL_ANALYSTS.filter(id => {
        return MANDATORY_STAGES.has(id) || document.getElementById(`stage-${id}`)?.classList.contains('selected');
    });
    const pipeline = ALL_PIPELINE.filter(id => {
        return MANDATORY_STAGES.has(id) || document.getElementById(`stage-${id}`)?.classList.contains('selected');
    });
    return { analysts, pipeline };
}

// === 分析 ===
function getActiveDepthConfig() {
    if (currentDepth === 'custom') {
        const sel = getCustomSelectedStages();
        if (sel.analysts.length < 2) {
            alert('请至少选择2个分析师阶段');
            return null;
        }
        return {
            analysts: sel.analysts,
            debate_rounds: sel.pipeline.includes('debate') ? 1 : 0,
            risk_rounds: sel.pipeline.includes('risk') ? 1 : 0,
            label: '自定义模式'
        };
    }
    return DEPTH_CONFIG[currentDepth] || DEPTH_CONFIG.standard;
}

async function startAnalysis() {
    let code = selectedCardCode;
    if (!code) { const b = getSelectedCodes(); if (b.length === 1) code = b[0]; }
    if (!code) return alert('\u8bf7\u5728\u5de6\u4fa7\u9009\u62e9\u4e00\u53ea\u80a1\u7968');
    await startAnalysisFor(code);
}

async function startAnalysisFor(code) {
    try {
        const dc = getActiveDepthConfig();
        if (!dc) return;
        const readiness = await loadAiReadiness();
        if (readiness && !readiness.ready) {
            showToast('AI 引擎尚未就绪，请先完成模型配置', 'error');
            return;
        }
        const resp = await aiTaskClient().start(code, { depth: currentDepth, selected_analysts: dc.analysts, debate_rounds: dc.debate_rounds, risk_rounds: dc.risk_rounds, model_mode: currentModelMode });
        if (resp.status === 'running') return alert('\u8be5\u80a1\u7968\u5df2\u6709\u4efb\u52a1\u8fd0\u884c');
        if (!resp.task_id) throw new Error(resp.message || '任务创建失败');
        currentTaskId = resp.task_id; activeAnalysisCode = code;
        showProgressPanel(code, resp.task_id, dc);
        startSSE(resp.task_id);
        loadTaskCenter();
    } catch (e) { alert('\u542f\u52a8\u5931\u8d25: ' + e.message); }
}

async function loadAiReadiness() {
    const el = document.getElementById('aiReadinessPanel');
    try {
        const data = await aiGet('/api/ai/readiness');
        if (el) {
            const cfg = data.config || {};
            const details = data.ready
                ? `AI就绪 · ${escapeHtml(cfg.quick_model || '快速模型')} / ${escapeHtml(cfg.deep_model || '深度模型')} · ${cfg.model_count || 0}个模型`
                : `AI未就绪 · ${(data.blockers || []).map(escapeHtml).join('；')}`;
            const warn = data.ready && (data.warnings || []).length ? ` · ${(data.warnings || []).map(escapeHtml).join('；')}` : '';
            el.className = `ai-readiness-panel ${data.ready ? ((data.warnings || []).length ? 'warning' : 'ready') : 'blocked'}`;
            el.innerHTML = `${details}${warn} <a href="/settings" style="margin-left:8px;">去设置</a>`;
        }
        const btn = document.getElementById('btnStartAnalysis');
        if (btn) btn.disabled = !data.ready;
        return data;
    } catch (e) {
        if (el) {
            el.className = 'ai-readiness-panel blocked';
            el.textContent = 'AI就绪检查失败: ' + e.message;
        }
        return {ready: false, blockers: [e.message]};
    }
}

function showProgressPanel(code, taskId, depthCfg) {
    showBottomPanel('progress');
    const allA = ['market','social','news','fundamentals','policy','hot_money','lockup'];
    const allP = ['quality_gate','debate','trader','risk','pm'];
    const aa = depthCfg ? depthCfg.analysts : allA;
    const hd = !depthCfg || depthCfg.debate_rounds > 0;
    const hr = !depthCfg || depthCfg.risk_rounds > 0;
    document.getElementById('analystStages').innerHTML = `<div class="avatar-selfie">${allA.map(id => renderStageAvatar(id, aa.includes(id) ? 'pending' : 'skipped', aa.includes(id) ? '' : ' · 跳过')).join('')}</div>`;
    const ap = allP.filter(id => { if(id==='debate') return hd; if(id==='risk') return hr; return true; });
    document.getElementById('pipelineStages').innerHTML = `<div class="avatar-pipeline">${allP.map(id => renderStageAvatar(id, ap.includes(id) ? 'pending' : 'skipped', ap.includes(id) ? '' : ' · 跳过')).join('')}</div>`;
    window._activeStageTotal = aa.length + ap.length;
    const nameEl = document.querySelector(`.ai-stock-card[data-code="${code}"] .sc-name`);
    const stockName = nameEl ? nameEl.textContent.replace(code, '').trim() : '';
    document.getElementById('progressTitle').textContent = `${depthCfg?.label||'标准'} 分析: ${stockName ? stockName + ' ' : ''}${code}`;
    document.getElementById('progressBar').style.width = '0%';
    document.getElementById('progressText').textContent = `0/${window._activeStageTotal} \u9636\u6bb5`;
    document.getElementById('cancelContainer').style.display = 'block';
    document.getElementById('resumeContainer').style.display = 'none';
    document.getElementById('queuePosition').textContent = '';
    const te = document.getElementById('tokenStats'); if(te) te.textContent = '';
}

// === SSE ===
function startSSE(taskId) {
    if (currentSSE) currentSSE.close();
    const es = aiTaskClient().stream(taskId);
    currentSSE = es;
    es.onmessage = (e) => {
        const d = JSON.parse(e.data);
        if (d.type==='stage_completed') { const el=document.getElementById(`stage-${d.stage}`); if(el) el.className='avatar-card completed'; }
        else if (d.type==='progress') {
            const t = window._activeStageTotal||d.total;
            const p = Math.round((d.completed/t)*100);
            document.getElementById('progressBar').style.width = p+'%';
            document.getElementById('progressText').textContent = `${d.completed}/${t} \u9636\u6bb5 \u00b7 ${formatElapsed(d.elapsed)}`;
            document.getElementById('progressElapsed').textContent = formatElapsed(d.elapsed);
            document.getElementById('queuePosition').textContent = '';
            if (d.token_stats) { const ts=d.token_stats; const te=document.getElementById('tokenStats'); if(te) te.textContent=`LLM:${ts.llm_calls}\u6b21 | ${(ts.input_tokens/1000).toFixed(1)}k\u5165/${(ts.output_tokens/1000).toFixed(1)}k\u51fa`; }
        }
        else if (d.type==='queued') { document.getElementById('queuePosition').textContent=`\u6392\u961f\u4e2d(\u7b2c${d.position||0}\u4f4d)`; }
        else if (d.type==='completed') { activeAnalysisCode=null; showReport(d.result, d.elapsed); es.close(); loadTaskCenter(); loadReportQuality(); }
        else if (d.type==='failed') { activeAnalysisCode=null; showResumeButton(taskId, d.error||'分析失败'); es.close(); loadTaskCenter(); }
    };
    es.onerror = () => { es.close(); pollTaskStatus(taskId); };
}

async function pollTaskStatus(taskId) {
    try {
        const s = await aiTaskClient().status(taskId);
        if (s.status==='completed') { const r=await aiTaskClient().result(taskId); showReport(r.result, r.elapsed); }
        else if (s.status==='failed') { showResumeButton(taskId, s.error||'分析失败'); }
        else setTimeout(()=>pollTaskStatus(taskId), 2000);
    } catch(e) {}
}

async function cancelAnalysis() {
    if (!currentTaskId) return alert('\u6ca1\u6709\u8fd0\u884c\u4e2d\u7684\u4efb\u52a1');
    if (!confirm('\u786e\u5b9a\u53d6\u6d88\uff1f')) return;
    try { const r = await aiTaskClient().cancel(currentTaskId); if(r.status==='ok') { document.getElementById('cancelContainer').style.display='none'; if(currentSSE){currentSSE.close();currentSSE=null;} loadTaskCenter(); setTimeout(()=>{showBottomPanel('progress'); initIdleStages();},1000); } } catch(e) {}
}

function showResumeButton(taskId, errorMsg) {
    currentTaskId = taskId;
    document.getElementById('cancelContainer').style.display = 'none';
    document.getElementById('resumeContainer').style.display = 'block';
    document.getElementById('resumeError').textContent = errorMsg;
}

async function resumeAnalysis() {
    if (!currentTaskId) return;
    const btn = document.querySelector('#resumeContainer .btn-primary');
    if (btn) { btn.disabled = true; btn.textContent = '续跑中...'; }
    document.getElementById('resumeContainer').style.display = 'none';
    try {
        const r = await aiTaskClient().resume(currentTaskId);
        if (r.task_id) {
            currentTaskId = r.task_id;
            initIdleStages();
            startSSE(r.task_id);
        } else {
            alert(r.message || '续跑失败');
            document.getElementById('resumeContainer').style.display = 'block';
            if (btn) { btn.disabled = false; btn.textContent = '继续分析'; }
        }
    } catch(e) {
        alert('续跑请求失败: ' + e.message);
        document.getElementById('resumeContainer').style.display = 'block';
        if (btn) { btn.disabled = false; btn.textContent = '继续分析'; }
    }
}

// === 报告详情 ===
// === 报告顶部大盘指数 ===
function loadReportIndex() {
    const bar = document.getElementById('reportIndexBar');
    if (!bar) return;
    aiGet('/api/index').then(data => {
        if (!data || typeof data !== 'object') { bar.style.display='none'; return; }
        const keys = ['sh','sz','cyb'];
        let html = '';
        for (const k of keys) {
            const d = data[k]; if (!d || !d.price) continue;
            const chg = d.change_pct || 0;
            const cls = chg > 0 ? 'price-up' : chg < 0 ? 'price-down' : '';
            const sign = chg > 0 ? '+' : '';
            html += `<div class="index-item">
                <span class="index-name">${escapeHtml(d.name||k)}</span>
                <span class="index-price">${d.price.toLocaleString('zh-CN',{minimumFractionDigits:1,maximumFractionDigits:1})}</span>
                <span class="index-change ${cls}">${sign}${chg.toFixed(2)}%</span>
            </div>`;
        }
        if (html) { bar.innerHTML = html; bar.style.display = 'flex'; }
        else { bar.style.display = 'none'; }
    }).catch(() => { bar.style.display = 'none'; });
}

function showReport(result, elapsed) {
    if (!result) return;
    showBottomPanel('report');
    loadReportIndex();
    window._currentResult = result;
    const sig = (result.signal||'HOLD').toUpperCase();
    const colors = {BUY:'#E07A5F',SELL:'#52B788',HOLD:'#F4A261',STRONG_BUY:'#C0392B',STRONG_SELL:'#1B7A3D',OVERWEIGHT:'#E8927C',UNDERWEIGHT:'#7BC47F'};
    document.getElementById('reportStockName').textContent = (result.name ? result.name + ' ' : '') + (result.code||'') + (result.depth ? ' · ' + ({quick:'快速',standard:'标准',deep:'深度'}[result.depth]||result.depth) : '') + (result.model_mode ? ' · ' + ({balanced:'均衡',deepseek:'DeepSeek',openai:'OpenAI',custom:'自定义'}[result.model_mode]||result.model_mode) : '');
    const se = document.getElementById('reportSignal');
    se.textContent = `${SIG_LABEL[sig]||sig} ${sig}`; se.style.background = colors[sig]||'#888'; se.style.color='#fff';
    // 事实账本准确率徽章
    const fce = document.getElementById('reportFactBadge');
    if (fce) {
        const fc = result._fact_check;
        // 新结构: per_stage (七层数据核对)
        if (fc && fc.stages && Object.keys(fc.stages).length > 0) {
            const acc = fc.overall_accuracy || 0;
            const accColor = acc >= 80 ? '#52B788' : acc >= 50 ? '#F4A261' : '#E07A5F';
            const stageCount = Object.keys(fc.stages).length;
            fce.textContent = `事实核对 ${acc}% · ${stageCount}阶段`;
            fce.style.background = accColor;
            fce.style.color = '#fff';
            fce.style.display = 'inline-block';
        // 旧结构: flat claims
        } else if (fc && (fc.verified + fc.mismatched) > 0) {
            const acc = fc.accuracy || 0;
            const accColor = acc >= 80 ? '#52B788' : acc >= 50 ? '#F4A261' : '#E07A5F';
            fce.textContent = `准确率 ${acc}%`;
            fce.style.background = accColor;
            fce.style.color = '#fff';
            fce.style.display = 'inline-block';
        } else {
            fce.style.display = 'none';
        }
    }
    const cb = document.getElementById('btnGenCondOrder');
    if(cb) cb.style.display = (['STRONG_BUY','BUY','OVERWEIGHT','STRONG_SELL','SELL','UNDERWEIGHT'].includes(sig))?'inline-block':'none';
    const ft = document.querySelector('.report-nav .nav-item[data-target]');
    if(ft) switchReportTab(ft, ft.dataset.target);
}
function closeReport() {
    showBottomPanel('progress');
    initIdleStages();
    window._currentResult = null;
}

function viewReport(id) {
    aiGet(`/api/ai/reports/${id}`).then(report => {
        let p = report.result || {};
        const r = { _reportId:id, code:report.code, name:report.name||p.name||'', depth:report.depth||'', model_mode:report.model_mode||'', signal:report.signal||p.signal||'HOLD', confidence:report.confidence||p.confidence, risk_score:report.risk_score||p.risk_score, target_price:p.target_price||null, reasoning:p.reasoning||report.final_decision||'', stages:{ market:report.market_report, social:report.sentiment_report, news:report.news_report, fundamentals:report.fundamentals_report, policy:report.policy_report, hot_money:report.hot_money_report, lockup:report.lockup_report, debate:report.investment_debate, risk:report.risk_debate, trader:report.trader_plan, pm:report.final_decision }, risk_debate:parseRiskDebate(report.risk_debate, report), _fact_check: report._fact_check, _bystander_verify: report._bystander_verify };
        showReport(r, report.duration_seconds);
    }).catch(e => console.error(e));
}

// === 报告Tab ===
// 从markdown文本提取关键摘要点
function extractHighlights(text, max) {
    max = max || 3;
    if (!text) return [];
    const lines = text.split('\n');
    const bullets = [];
    for (const line of lines) {
        const t = line.trim();
        // 匹配markdown列表项或加粗结论
        if (t.match(/^[-*•]\s+/) && t.length > 10) {
            bullets.push(t.replace(/^[-*•]\s+/, ''));
        } else if (t.match(/^\d+[.)]\s+/) && t.length > 10) {
            bullets.push(t.replace(/^\d+[.)]\s+/, ''));
        }
        if (bullets.length >= max) break;
    }
    // 如果没找到列表项，取前3句有意义的句子
    if (!bullets.length) {
        const sentences = text.replace(/[#*_]/g, '').split(/[。！？\n]/).filter(s => s.trim().length > 15);
        for (const s of sentences.slice(0, max)) {
            bullets.push(s.trim());
        }
    }
    return bullets.slice(0, max);
}

// 提取第一段有意义的文本作为一句话摘要
function extractFirstLine(text) {
    if (!text) return '';
    const clean = text.replace(/^[\s\S]*?(?:---\n|#)/m, '').replace(/[#*_]/g, '');
    const lines = clean.split('\n').filter(l => l.trim().length > 10);
    return (lines[0] || '').trim().substring(0, 80);
}

// 渲染可折叠阶段卡片
// 内联markdown渲染（不生成块级标签）
function renderInlineMd(s) {
    if (!s) return '';
    if (typeof marked !== 'undefined') {
        return marked.parseInline(escapeHtml(s));
    }
    return escHtml(s);
}

function renderStageCard(icon, title, text, open) {
    if (!text || text === '暂无') return '';
    const highlights = extractHighlights(text, 3);
    const summary = highlights.length ? highlights[0].substring(0, 60) + (highlights[0].length > 60 ? '...' : '') : extractFirstLine(text);
    const highlightHtml = highlights.map(h => `<div class="stage-highlight-item"><div class="stage-highlight-dot"></div><div>${renderInlineMd(h)}</div></div>`).join('');
    return `<div class="stage-collapsible ${open?'open':''}" onclick="this.classList.toggle('open')">
        <div class="stage-collapsible-header">
            <span class="stage-collapsible-icon">${icon}</span>
            <span class="stage-collapsible-title">${title}</span>
            <span class="stage-collapsible-summary">${renderInlineMd(summary)}</span>
            <span class="stage-collapsible-toggle">▶</span>
        </div>
        <div class="stage-collapsible-body">
            <div class="stage-highlights">${highlightHtml}</div>
            <div class="report-full-text">${formatReport(text)}</div>
        </div>
    </div>`;
}

// HTML转义
function escHtml(s) {
    if (!s) return '';
    return escapeHtml(s);
}

// 渲染Dashboard卡片
function renderDashboard(r, container) {
    const sig = (r.signal || 'HOLD').toUpperCase();
    const sigLabel = SIG_LABEL[sig] || sig;
    const sigClass = {BUY:'signal-buy',SELL:'signal-sell',HOLD:'signal-hold',STRONG_BUY:'signal-strong-buy',STRONG_SELL:'signal-strong-sell',OVERWEIGHT:'signal-overweight',UNDERWEIGHT:'signal-underweight'}[sig] || '';
    const sigColor = {BUY:'#E07A5F',SELL:'#52B788',HOLD:'#F4A261',STRONG_BUY:'#C0392B',STRONG_SELL:'#1B7A3D',OVERWEIGHT:'#E8927C',UNDERWEIGHT:'#7BC47F'}[sig] || '#999';
    const tp = r.target_price;
    const conf = r.confidence || r.risk_score;
    const riskVal = r.risk_score || r.confidence;

    // 从PM决策提取一句话结论
    const pmText = r.reasoning || r.stages?.pm || '';
    const verdict = extractFirstLine(pmText);

    let html = `<div class="report-dashboard">`;
    // 信号卡片
    html += `<div class="dash-card ${sigClass}">
        <div class="dash-card-label">信号</div>
        <div class="dash-card-value" style="color:${sigColor}">${sigLabel}</div>
        <div class="dash-card-sub">${sig}</div>
    </div>`;
    // 目标价卡片
    if (tp) {
        html += `<div class="dash-card">
            <div class="dash-card-label">目标价</div>
            <div class="dash-card-value">¥${Number(tp).toFixed(2)}</div>
        </div>`;
    }
    // 置信度卡片
    if (conf != null) {
        const confPct = Math.min(100, Math.max(0, Number(conf)));
        const confColor = confPct >= 70 ? '#52B788' : confPct >= 40 ? '#F4A261' : '#E07A5F';
        html += `<div class="dash-card">
            <div class="dash-card-label">置信度</div>
            <div class="dash-card-value" style="color:${confColor}">${confPct}%</div>
            <div class="dash-bar-track"><div class="dash-bar-fill" style="width:${confPct}%;background:${confColor}"></div></div>
        </div>`;
    }
    // 风险评分卡片
    if (riskVal != null && riskVal !== conf) {
        const rv = Math.min(100, Math.max(0, Number(riskVal)));
        const rColor = rv <= 30 ? '#52B788' : rv <= 60 ? '#F4A261' : '#E07A5F';
        html += `<div class="dash-card">
            <div class="dash-card-label">风险评分</div>
            <div class="dash-card-value" style="color:${rColor}">${rv}</div>
            <div class="dash-bar-track"><div class="dash-bar-fill" style="width:${rv}%;background:${rColor}"></div></div>
        </div>`;
    }
    // 一句话结论
    if (verdict) {
        html += `<div class="dash-verdict">
            <div class="dash-verdict-label">PM决策摘要</div>
            <div>${escHtml(verdict)}</div>
        </div>`;
    }
    html += `</div>`;
    container.innerHTML = html;
}

// 渲染基本面指标网格
function renderMetricsGrid(text) {
    if (!text) return '';
    // 提取常见财务指标
    const patterns = [
        {re: /PE[：:\s]*(\d+[\d.]*)/i, label: 'PE'},
        {re: /市盈率[：:\s]*(\d+[\d.]*)/, label: '市盈率'},
        {re: /ROE[：:\s]*([+-]?\d+[\d.]*)%?/i, label: 'ROE'},
        {re: /毛利率[：:\s]*([+-]?\d+[\d.]*)%?/, label: '毛利率'},
        {re: /净利率[：:\s]*([+-]?\d+[\d.]*)%?/, label: '净利率'},
        {re: /营收[：:\s]*([+-]?\d+[\d.]*[亿万]?)/, label: '营收'},
        {re: /净利润[：:\s]*([+-]?\d+[\d.]*[亿万]?)/, label: '净利润'},
        {re: /总市值[：:\s]*([+-]?\d+[\d.]*[亿万]?)/, label: '总市值'},
        {re: /流通市值[：:\s]*([+-]?\d+[\d.]*[亿万]?)/, label: '流通市值'},
        {re: /换手率[：:\s]*([+-]?\d+[\d.]*)%?/, label: '换手率'},
        {re: /成交量[：:\s]*([+-]?\d+[\d.]*[万手]?)/, label: '成交量'},
        {re: /MA\d+[：:\s]*(\d+[\d.]*)/, label: '均线'},
        {re: /MACD[：:\s]*([+-]?[\d.]+)/i, label: 'MACD'},
        {re: /RSI[：:\s]*(\d+[\d.]*)/i, label: 'RSI'},
        {re: /KDJ[：:\s]*([^\n]{3,15})/i, label: 'KDJ'},
    ];
    const found = [];
    for (const p of patterns) {
        const m = text.match(p.re);
        if (m) found.push({label: p.label, value: m[1]});
    }
    if (!found.length) return '';
    return `<div class="metric-grid">${found.map(m => `<div class="metric-cell"><div class="metric-cell-label">${escapeHtml(m.label)}</div><div class="metric-cell-value">${escapeHtml(m.value)}</div></div>`).join('')}</div>`;
}

function switchReportTab(btn, target) {
    document.querySelectorAll('.report-nav .nav-item').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    const c = document.getElementById('reportContent');
    const r = window._currentResult; if(!r||!c) return;
    const s = r.stages||{};

    // 总览 tab — Dashboard + 所有阶段折叠卡片
    if (target === 'overview') {
        let html = '';
        renderDashboard(r, c);
        html = c.innerHTML;
        // 七层分析师折叠卡片
        const ai = Object.entries(REPORT_STAGE_LABELS).map(([k, n]) => ({ k, i: STAGE_META[k]?.code || 'ST', n }));
        for (const a of ai) {
            const txt = s[a.k];
            if (txt && txt !== '暂无') {
                html += renderStageCard(a.i, a.n, txt, false);
            }
        }
        // 基本面指标网格
        if (s.fundamentals) {
            html += renderMetricsGrid(s.fundamentals);
        }
        c.innerHTML = html;
        return;
    }

    // 七层分析师 tab — 带指标网格的折叠卡片
    if (REPORT_STAGE_LABELS[target]) {
        const txt = s[target] || '暂无';
        let html = renderStageCard(STAGE_META[target]?.code || 'ST', REPORT_STAGE_LABELS[target], txt, true);
        // 基本面tab额外显示指标网格
        if (target === 'fundamentals' && txt !== '暂无') {
            html += renderMetricsGrid(txt);
        }
        c.innerHTML = html || `<div style="text-align:center;padding:40px;color:var(--text-muted)">暂无</div>`;
        return;
    }

    // 辩论 tab — 双栏卡片
    if (target==='debate') {
        const dt=s.debate||'暂无';
        const bullText=extractSide(dt,'bull'), bearText=extractSide(dt,'bear');
        c.innerHTML=`<div class="report-section-title">多空辩论</div>
            <div class="debate-dual">
                <div class="debate-side bull">
                    <h5>多头</h5>
                    <div>${formatReport(bullText)}</div>
                </div>
                <div class="debate-divider">VS</div>
                <div class="debate-side bear">
                    <h5>空头</h5>
                    <div>${formatReport(bearText)}</div>
                </div>
            </div>`;
        return;
    }

    // 风控 tab — 三栏卡片
    if (target==='risk') {
        const rd=r.risk_debate||{};
        const agg=rd.aggressive||rd.current_aggressive_response||'暂无';
        const con=rd.conservative||rd.current_conservative_response||'暂无';
        const neu=rd.neutral||rd.current_neutral_response||'暂无';
        const dec=rd.decision||rd.judge_decision||'';
        c.innerHTML=`<div class="report-section-title">风控</div>
            <div class="risk-tri">
                <div class="risk-card aggressive"><h5>激进</h5><div>${formatReport(agg)}</div></div>
                <div class="risk-card conservative"><h5>保守</h5><div>${formatReport(con)}</div></div>
                <div class="risk-card neutral"><h5>中性</h5><div>${formatReport(neu)}</div></div>
            </div>
            ${dec?`<div class="risk-verdict"><h5>裁决</h5><div>${formatReport(dec)}</div></div>`:''}`;
        return;
    }

    // 决策 tab
    if (target==='pm') {
        const pmText = r.reasoning||s.pm||'暂无';
        c.innerHTML=`<div class="report-section-title">决策</div>${renderStageCard('PM','PM决策',pmText,true)}`;
        return;
    }

    // 事实账本 tab
    if (target==='factcheck') {
        if (r._fact_check) {
            renderFactCheckInline(r._fact_check, c, r._reportId);
        } else if (r._reportId) {
            loadFactCheck(r._reportId, c);
        } else {
            c.innerHTML='请从历史报告中选择';
        }
        return;
    }

    // 复核 tab
    if (target==='verify') {
        if (r._bystander_verify) {
            renderVerificationInline(r._bystander_verify, c, r._reportId);
        } else if (r._reportId) {
            c.innerHTML=`<div style="text-align:center;padding:40px;color:var(--text-muted)">
                <div style="margin-bottom:12px">暂无复核数据</div>
                <button class="btn btn-primary btn-sm" onclick="rerunVerification(${r._reportId},this.closest('.report-tab-content')||document.getElementById('reportContent'))">开始复核</button>
            </div>`;
        } else {
            c.innerHTML='请从历史报告中选择';
        }
        return;
    }

    // 信号绩效 tab
    if (target==='performance') {
        loadPerformanceTab(c);
        return;
    }
}

function extractSide(text, side) {
    if (!text) return '\u6682\u65e0';
    const kw = side==='bull'?['\u591a\u5934','\u770b\u591a','\u4e70\u5165','\u5229\u597d','\u4e0a\u6da8']:['\u7a7a\u5934','\u770b\u7a7a','\u5356\u51fa','\u5229\u7a7a','\u4e0b\u8dcc'];
    const ot = side==='bull'?['\u7a7a\u5934','\u770b\u7a7a','\u5356\u51fa','\u5229\u7a7a','\u4e0b\u8dcc']:['\u591a\u5934','\u770b\u591a','\u4e70\u5165','\u5229\u597d','\u4e0a\u6da8'];
    let r=[],cap=false;
    for (const l of text.split('\n')) { if(kw.some(k=>l.includes(k))) cap=true; if(ot.some(k=>l.includes(k))) cap=false; if(cap) r.push(l); }
    return r.length?r.join('\n'):text.substring(0,500);
}

function showFullReport() {
    const r=window._currentResult; if(!r) return; const s=r.stages||{}; const c=document.getElementById('reportContent');
    const sec=Object.entries(REPORT_STAGE_LABELS).map(([k,n])=>({k,n}));
    c.innerHTML=sec.filter(x=>s[x.k]).map(x=>`<div style="margin-bottom:16px"><div class="report-section-title">${x.n}</div><div class="report-summary">${formatReport(s[x.k])}</div></div>`).join('')+`<div style="margin-bottom:16px"><div class="report-section-title">辩论</div><div class="report-summary">${formatReport(s.debate||'\u6682\u65e0')}</div></div><div style="margin-bottom:16px"><div class="report-section-title">决策</div><div class="report-summary">${formatReport(s.pm||r.reasoning||'\u6682\u65e0')}</div></div>`;
}

function hasStageFactCheck(fc) {
    return !!(fc?.stages && Object.keys(fc.stages).length > 0);
}

function hasFlatFactCheck(fc) {
    return !!(fc && ((fc.claims || []).length > 0 || fc.verified || fc.mismatched));
}

function factAccuracyColor(value) {
    const score = Number(value || 0);
    return score >= 80 ? '#52B788' : score >= 50 ? '#F4A261' : '#E07A5F';
}

function factCheckActionButton(rid) {
    if (!rid) return '';
    return `<button class="btn btn-sm" style="font-size:0.75rem;opacity:0.7" onclick="recheckFactCheck(${rid},this.closest('.report-tab-content')||document.getElementById('reportContent'))">重新核对</button>`;
}

function renderFactCheckHeader(fc, rid) {
    const acc = fc.overall_accuracy || fc.accuracy || 0;
    const stats = [
        `<span>总一致率：<b style="color:${factAccuracyColor(acc)}">${acc}%</b></span>`,
        `<span>核对阶段：${Object.keys(fc.stages || {}).length}</span>`,
        `<span style="color:#E07A5F">幻觉：${fc.total_hallucinations || 0}</span>`
    ];
    if (fc.total_claims != null) stats.push(`<span>总声明：${fc.total_claims || 0}</span>`);
    return `<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
            <div class="report-section-title" style="margin:0">事实账本</div>
            ${factCheckActionButton(rid)}
        </div>
        <div style="display:flex;gap:16px;margin-bottom:16px;font-size:0.85rem;flex-wrap:wrap">
            ${stats.join('')}
        </div>`;
}

function renderMissingFactStage(stageId) {
    return `<div style="margin-bottom:8px;padding:10px 14px;border:1px dashed var(--border-color);border-radius:8px;font-size:0.85rem;color:var(--text-muted);display:flex;align-items:center;gap:8px">
        <span style="font-weight:600">${REPORT_STAGE_LABELS[stageId] || stageId}</span>
        <span style="font-size:0.75rem">— 该阶段未捕获数据快照，无法核对</span>
    </div>`;
}

function renderFactClaimsTable(claims) {
    return `<table style="width:100%;font-size:0.8rem;border-collapse:collapse">
        <thead><tr style="border-bottom:1px solid var(--border-color)">
            <th style="text-align:left;padding:4px 6px">数据项</th>
            <th style="text-align:left;padding:4px 6px">报告引用</th>
            <th style="text-align:left;padding:4px 6px">实际数据</th>
            <th style="text-align:center;padding:4px 6px">状态</th>
        </tr></thead><tbody>${claims.map(item => {
            const actualVal = item.actual_value != null ? item.actual_value : (item.snapshot_value != null ? item.snapshot_value : '—');
            return `<tr style="border-bottom:1px solid var(--border-color)">
                <td style="padding:4px 6px">${escapeHtml(item.keyword || '')}</td>
                <td style="padding:4px 6px">${escapeHtml(item.claimed_value || '—')}</td>
                <td style="padding:4px 6px">${escapeHtml(actualVal)}</td>
                <td style="padding:4px 6px;text-align:center">${statusLabel(item.status)}</td>
            </tr>`;
        }).join('')}</tbody></table>`;
}

function renderFactStageDetails(stageId, st) {
    if (!st) return renderMissingFactStage(stageId);
    const stAcc = st.accuracy || 0;
    const claims = (st.checked_claims && st.checked_claims.length > 0) ? st.checked_claims : (st.hallucinations || []);
    const hallus = (st.hallucinations || []).filter(h => h.status === 'mismatch');
    const matchedCount = st.matched || 0;
    const mismatchedCount = st.mismatched || 0;
    const noSourceCount = st.no_source || 0;
    const claimsSummary = claims.length > 0
        ? `<div style="font-size:0.75rem;color:var(--text-muted);margin-bottom:8px">
            共 ${st.total_claims || claims.length} 条声明：${matchedCount > 0 ? `<b style="color:#52B788">${matchedCount}条一致</b>` : ''}
            ${mismatchedCount > 0 ? ` · <b style="color:#E07A5F">${mismatchedCount}条幻觉</b>` : ''}
            ${noSourceCount > 0 ? ` · <b style="color:#F4A261">${noSourceCount}条无源数据</b>` : ''}
            ${matchedCount > 0 ? ' · 一致项不逐条列出' : ''}
        </div>${renderFactClaimsTable(claims)}`
        : '<div style="font-size:0.8rem;color:var(--text-muted)">无核对记录</div>';

    return `<details style="margin-bottom:8px;border:1px solid var(--border-color);border-radius:8px;overflow:hidden" ${stAcc < 80 ? 'open' : ''}>
        <summary style="padding:10px 14px;cursor:pointer;display:flex;align-items:center;gap:10px;font-size:0.85rem;background:var(--bg-secondary)">
            <span style="font-weight:600">${escapeHtml(st.stage_name || REPORT_STAGE_LABELS[stageId] || stageId)}</span>
            <span style="color:${factAccuracyColor(stAcc)};font-weight:600">${stAcc}%</span>
            <span style="font-size:0.75rem;color:var(--text-muted)">一致 ${matchedCount} · 差异 ${mismatchedCount} · 待核 ${noSourceCount}</span>
            ${hallus.length > 0 ? `<span style="color:#E07A5F;font-size:0.8rem">${hallus.length}个幻觉</span>` : ''}
        </summary>
        <div style="padding:10px 14px">${claimsSummary}</div>
    </details>`;
}

function renderFactCheckStages(fc, rid) {
    return renderFactCheckHeader(fc, rid)
        + FACT_CHECK_STAGE_ORDER.map(stageId => renderFactStageDetails(stageId, fc.stages[stageId])).join('');
}

function renderFactCheckFlat(fc) {
    const ac = fc.accuracy || 0;
    return `<div class="report-section-title">事实账本</div>
        <div style="display:flex;gap:16px;margin-bottom:12px;font-size:0.9rem">
            <span>准确率: <b style="color:${factAccuracyColor(ac)}">${ac}%</b></span>
            <span style="color:#52B788">一致 ${fc.verified || 0}</span>
            <span style="color:#E07A5F">差异 ${fc.mismatched || 0}</span>
            <span style="color:var(--text-muted)">待核 ${fc.unverifiable || 0}</span>
        </div>${renderFactClaimsTable(fc.claims || [])}`;
}

function renderFactCheckEmpty(rid, buttonTarget = "this.closest('.report-tab-content')||document.getElementById('reportContent')") {
    return `<div style="text-align:center;padding:20px;color:var(--text-muted)">暂无事实账本数据</div>
        ${rid ? `<div style="text-align:center;padding:0 20px 20px"><button class="btn btn-primary btn-sm" onclick="recheckFactCheck(${rid},${buttonTarget})">重新核对</button></div>` : ''}`;
}

function renderFactCheckInline(fc, c, rid) {
    if (hasStageFactCheck(fc)) {
        c.innerHTML = renderFactCheckStages(fc, rid);
    } else if (hasFlatFactCheck(fc)) {
        c.innerHTML = renderFactCheckFlat(fc);
    } else {
        c.innerHTML = renderFactCheckEmpty(rid);
    }
}

async function recheckFactCheck(rid, c) {
    c.innerHTML='<div style="text-align:center;padding:20px">正在重新核对（约30秒）...</div>';
    try {
        const d = await aiPost(`/api/ai/reports/${rid}/recheck`);
        if (d.error) { c.innerHTML=escapeHtml(d.error); return; }
        // 核对完成，重新渲染
        if (d.stages && Object.keys(d.stages).length > 0) {
            renderFactCheckInline(d, c, rid);
        } else {
            c.innerHTML='<div style="text-align:center;padding:20px;color:var(--text-muted)">核对完成，无可用数据</div>';
        }
    } catch(e) { c.innerHTML='核对失败: '+escapeHtml(e.message); }
}

async function loadFactCheck(rid, c) {
    c.innerHTML='<div style="text-align:center;padding:20px">核对中...</div>';
    try {
        const d = await aiGet(`/api/ai/reports/${rid}/fact-check`);
        if(d.error){c.innerHTML=escapeHtml(d.error);return;}
        if (hasStageFactCheck(d)) c.innerHTML = renderFactCheckStages(d, rid);
        else if (hasFlatFactCheck(d)) c.innerHTML = renderFactCheckFlat(d);
        else c.innerHTML = renderFactCheckEmpty(rid, 'this.parentElement.parentElement');
    } catch(e){c.innerHTML=escapeHtml(e.message);}
}
function renderVerificationInline(bv, c, rid) {
    const sc = (bv.overall_score||0) >= 80 ? '#52B788' : (bv.overall_score||0) >= 50 ? '#F4A261' : '#E07A5F';
    c.innerHTML = `<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
        <div class="report-section-title" style="margin:0">报告复核</div>
        <button class="btn btn-sm" style="font-size:0.75rem;opacity:0.7" onclick="rerunVerification(${rid},this.closest('.report-tab-content')||document.getElementById('reportContent'))">重新复核</button>
    </div>
        <div style="margin-bottom:16px;padding:12px;background:rgba(91,155,213,0.06);border-radius:8px">
            <div style="font-size:1.2rem;font-weight:600;color:${sc}">可信度：${bv.overall_score||'—'}/100</div>
            <div style="font-size:0.85rem;margin-top:4px">${escapeHtml(bv.summary||'')}</div>
        </div>
        ${(bv.hallucinations||[]).length > 0 ? `<div class="report-section-title" style="font-size:0.9rem">发现的问题</div>
            ${bv.hallucinations.map(h => `<div style="padding:8px 12px;margin-bottom:6px;border-left:3px solid ${h.severity==='high'?'#E07A5F':'#F4A261'}">
                <div style="font-size:0.85rem;font-weight:500">${escapeHtml(h.claim||'')}</div>
                <div style="font-size:0.8rem;color:var(--text-secondary)">${escapeHtml(h.issue||'')}</div>
            </div>`).join('')}` : '<div style="color:#52B788;font-size:0.85rem">未发现明显幻觉</div>'}`;
}

async function rerunVerification(rid, c) {
    c.innerHTML='<div style="text-align:center;padding:20px">旁观者复核中...</div>';
    try {
        const d=await aiPost(`/api/ai/reports/${rid}/bystander-verify`);
        if(d.error){c.innerHTML=escapeHtml(d.error);return;}
        renderVerificationInline(d.result||{}, c, rid);
    } catch(e){c.innerHTML=escapeHtml(e.message);}
}

function parseRiskDebate(rd, report) {
    // API已返回parsed dict，直接映射
    if (rd && typeof rd === 'object') {
        const agg = rd.aggressive || rd.current_aggressive_response || '';
        const con = rd.conservative || rd.current_conservative_response || '';
        const neu = rd.neutral || rd.current_neutral_response || '';
        const dec = rd.decision || rd.judge_decision || '';
        if (agg || con || neu || dec) return {aggressive:agg, conservative:con, neutral:neu, decision:dec};
    }
    // fallback: report层的risk_debate（可能是string）
    let raw = report.risk_debate;
    if (typeof raw === 'string' && raw.trim()) { try { const p=JSON.parse(raw); if(typeof p==='object') return {aggressive:p.current_aggressive_response||p.aggressive_history||'',conservative:p.current_conservative_response||p.conservative_history||'',neutral:p.current_neutral_response||p.neutral_history||'',decision:p.judge_decision||p.decision||''}; } catch(e){} }
    return {decision:report.final_decision||'暂无'};
}

// === 格式化 ===
function formatReport(text) {
    if (!text) return '<span class="text-muted">暂无数据</span>';
    if (typeof text === 'object') text = text.judge_decision||text.decision||text.content||text.report||JSON.stringify(text,null,2);
    text = String(text).replace(/\\\\n/g,'\n');
    try { const p=JSON.parse(text); if(typeof p==='object') text=p.judge_decision||p.decision||p.content||p.report||JSON.stringify(p,null,2); text=text.replace(/\\\\n/g,'\n'); } catch(e){}
    if (typeof marked !== 'undefined') {
        marked.setOptions({ breaks: true, gfm: true });
        return marked.parse(escapeHtml(text));
    }
    return escapeHtml(text).replace(/^### (.*$)/gm,'<h5>$1</h5>').replace(/^## (.*$)/gm,'<h4>$1</h4>').replace(/^# (.*$)/gm,'<h3>$1</h3>').replace(/^---+$/gm,'<hr>').replace(/\*\*(.*?)\*\*/g,'<strong>$1</strong>').replace(/\*(.*?)\*/g,'<em>$1</em>').replace(/`(.*?)`/g,'<code>$1</code>').replace(/^- (.*$)/gm,'• $1').replace(/\n/g,'<br>');
}
function formatElapsed(s) { if(!s) return '\u2014'; return `${Math.floor(s/60)}:${Math.floor(s%60).toString().padStart(2,'0')}`; }
function formatTime(ts) { if(!ts) return '\u2014'; const d=new Date(ts); return `${d.getMonth()+1}/${d.getDate()} ${d.getHours()}:${d.getMinutes().toString().padStart(2,'0')}`; }

// === 历史报告 ===
async function loadReports(code='') {
    try {
        const d = await aiGet(code ? `/api/ai/reports?code=${code}` : '/api/ai/reports');
        if (!code && d.reports) reportCodes = new Set(d.reports.map(r => r.code));
        const l = document.getElementById('reportsList');
        if (!d.reports?.length) {
            l.innerHTML = '<div class="empty-row">暂无</div>';
            return;
        }
        const depthLabel = { quick:'快速', standard:'标准', deep:'深度' };
        const modeLabel = { balanced:'均衡', deepseek:'DeepSeek', openai:'OpenAI', custom:'自定义' };
        l.innerHTML = d.reports.map(r => `<div class="report-item">
            <div class="report-item-header" onclick="viewReport(${Number(r.id)})">
                <span class="report-signal signal-${escapeAttr((r.signal||'hold').toLowerCase().replace(/_/g,'-'))}">${escapeHtml(SIG_LABEL[(r.signal||'HOLD').toUpperCase()]||r.signal||'—')}</span>
                <span class="report-code">${escapeHtml(r.name?r.name+' '+r.code:r.code)}</span>
                <span class="report-time">${escapeHtml(formatTime(r.created_at))}</span>
            </div>
            <div class="report-item-meta" onclick="viewReport(${Number(r.id)})">
                <span>${escapeHtml(depthLabel[r.depth]||r.depth||'标准')} · ${escapeHtml(modeLabel[r.model_mode]||r.model_mode||'均衡')}</span>
                <span>置信: ${r.confidence?(r.confidence*100).toFixed(0)+'%':'—'}</span>
                <span>耗时: ${escapeHtml(formatElapsed(r.duration_seconds))}</span>
            </div>
            <div class="report-item-actions">
                <a href="/api/ai/report/${Number(r.id)}/pdf" class="btn btn-sm" onclick="event.stopPropagation()">PDF</a>
            </div>
        </div>`).join('');
    } catch(e) {}
}
function searchReports(e) { if(e.key==='Enter') loadReports(e.target.value.trim()); }

// === 异动 ===
async function triggerL1() {
    try { const d=await aiPost('/api/ai/trigger'); if(d.anomalies?.length) renderAnomalies(d.anomalies); else document.getElementById('anomalyLog').innerHTML=`<div class="anomaly-empty">无异动（${d.checked}只）</div>`; } catch(e){}
}
async function pollAnomalies() {
    try {
        const d = await aiGet('/api/ai/anomalies');
        if (d.anomalies?.length) renderAnomalies(d.anomalies);
    } catch(e) {}
}
function renderAnomalies(a) {
    const log = document.getElementById('anomalyLog');
    if (!log) return;
    log.innerHTML=a.map(x=>{
        const levelLabel = x.level === 'critical' ? '严重' : x.level === 'warning' ? '预警' : '关注';
        const name = x.name || x.code;
        const changeHtml = x.change_pct ? `<span class="anomaly-change ${x.change_pct>=0?'price-up':'price-down'}">${x.change_pct>=0?'+':''}${(x.change_pct||0).toFixed(2)}%</span>` : '';
        const timeStr = x.time ? new Date(x.time).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit'}) : '';
        return `<div class="anomaly-item"><div class="anomaly-header"><span class="anomaly-level">${levelLabel} ${escapeHtml(x.anomaly_type||x.level||'')}</span><span class="anomaly-time">${escapeHtml(timeStr)}</span><span class="anomaly-stock">${escapeHtml(name)} ${escapeHtml(x.code || '')}</span>${changeHtml}</div><div class="anomaly-message">${escapeHtml(x.message||'')}</div></div>`;
    }).join('');
}

// === gbrain + 条件单 ===
async function saveToGbrain() {
    const r=window._currentResult; if(!r) return alert('\u6ca1\u6709\u7ed3\u679c');
    const slug=`deep-analysis/${r.code||'x'}-${new Date().toISOString().slice(0,10)}`; const title=`${r.name||''} ${r.code||''} 深度分析`;
    try { const d=await aiTaskClient().saveToGbrain({slug,title,content:r.reasoning||''}); alert(d.status==='ok'?'\u5df2\u5b58\u5165: '+slug:'\u5931\u8d25'); } catch(e){alert('\u5931\u8d25: '+e.message);}
}
async function generateCondOrder() {
    const r=window._currentResult;
    if(!r?._reportId) return showToast('请先从历史报告打开一份可追溯报告','error');
    try {
        const draftResp = await aiTaskClient().conditionalOrderDraft({report_id:r._reportId, shares:r.shares||0});
        const d = draftResp.draft;
        let backtestText = '历史回测：暂无样本';
        try {
            const bt = await aiPost('/api/ai/conditional-order/backtest', {
                code: d.code,
                condition_type: d.condition_type,
                target_price: d.target_price,
                days: 90
            });
            backtestText = `历史回测：${bt.sample_days}日样本，触发${bt.trigger_count}次，首触发后收益${bt.post_trigger_return_pct == null ? '无样本' : bt.post_trigger_return_pct + '%'}`;
        } catch(e) {}
        const warningText = (d.warnings||[]).map(x => `- ${x}`).join('\n');
        const message = `确认写入条件单？\n\n${d.name||d.code} ${d.code}\n方向：${d.action === 'buy' ? '买入' : '卖出'}\n触发：${d.condition_type} ${d.target_price}\n数量：${d.shares || 0}股\n来源报告：#${d.source_report_id}\n${backtestText}\n\n${warningText}`;
        if (!confirm(message)) return;
        const resp = await aiTaskClient().confirmConditionalOrderDraft(d);
        if(resp.id||resp.success) showToast(resp.message||`条件单已创建: ${d.code}`,'success');
        else showToast('失败','error');
    } catch(e){showToast(e.message,'error');}
}
function downloadPdf() { const r=window._currentResult; if(!r?._reportId) return alert('\u8bf7\u9009\u62e9\u62a5\u544a'); window.open(`/api/ai/report/${r._reportId}/pdf`,'_blank'); }

function showToast(msg, type) {
    let t=document.getElementById('globalToast'); if(!t){t=document.createElement('div');t.id='globalToast';t.style.cssText='position:fixed;top:60px;right:20px;z-index:99999;padding:12px 20px;border-radius:8px;font-size:0.9rem;font-weight:600;box-shadow:0 4px 16px rgba(0,0,0,0.15);transition:opacity 0.3s;max-width:360px;';document.body.appendChild(t);}
    const c={success:'#52B788',error:'#E07A5F',warning:'#F4A261'}; t.style.background=c[type]||c.success; t.style.color='#fff'; t.textContent=msg; t.style.opacity='1'; clearTimeout(window._toastTimer); window._toastTimer=setTimeout(()=>{t.style.opacity='0';},3500);
}

// === 队列 + 恢复 ===
async function pollQueueStatus() {
    try {
        const d = await aiTaskClient().queueStatus();
        const el = document.getElementById('queuePanelText');
        const p = el?.closest('.queue-panel');
        if (!el) return;
        if (d.running > 0 || d.queued > 0) {
            el.textContent = `运行:${d.running} 排队:${d.queued||0}`;
            if (p) p.style.display = '';
        } else if (p) {
            p.style.display = 'none';
        }
    } catch(e) {}
}

const TASK_STATUS_LABELS = {
    pending: '等待中',
    queued: '排队中',
    running: '运行中',
    completed: '已完成',
    failed: '失败',
    timeout: '超时',
    cancelled: '已取消',
    cancelling: '取消中'
};

function taskStatusText(task) {
    const status = task.queue_status || task.status;
    return TASK_STATUS_LABELS[status] || status || '未知';
}

async function loadTaskCenter() {
    const el = document.getElementById('aiTaskList');
    if (!el) return;
    try {
        const data = await aiTaskClient().tasks({limit: '30'});
        const tasks = data.tasks || [];
        if (!tasks.length) {
            el.innerHTML = '<div class="empty-row">暂无任务</div>';
            return;
        }
        el.innerHTML = tasks.map(t => {
            const status = taskStatusText(t);
            const queue = t.queue_position ? ` · 第${t.queue_position}位` : '';
            const error = t.error ? `<div class="ai-task-error">${escapeHtml(t.error)}</div>` : '';
            const retry = t.can_retry ? `<button class="btn btn-sm" onclick="retryTask('${escapeAttr(t.task_id)}')">重试</button>` : '';
            const cancel = t.can_cancel ? `<button class="btn btn-sm" onclick="cancelQueuedTask('${escapeAttr(t.task_id)}')">取消</button>` : '';
            return `<div class="ai-task-card">
                <div class="ai-task-main">
                    <div class="ai-task-title">${escapeHtml(t.name||t.code||'任务')} <span>${escapeHtml(t.code||'')}</span></div>
                    <div class="ai-task-meta">${escapeHtml(status)}${queue} · ${escapeHtml(t.progress||'0/0')} · ${escapeHtml(formatTime(t.updated_at||t.started_at))}</div>
                    ${error}
                </div>
                <div class="ai-task-actions">${retry}${cancel}</div>
            </div>`;
        }).join('');
    } catch(e) {
        el.innerHTML = `<div class="empty-row">任务中心加载失败：${escapeHtml(e.message)}</div>`;
    }
}

async function retryTask(taskId) {
    try {
        const resp = await aiTaskClient().retry(taskId);
        showToast(resp.message || '已重新提交', 'success');
        await loadTaskCenter();
        pollQueueStatus();
    } catch(e) {
        showToast('重试失败: ' + e.message, 'error');
    }
}

async function cancelQueuedTask(taskId) {
    if (!confirm('确认取消这个任务？')) return;
    try {
        const resp = await aiTaskClient().cancelFromCenter(taskId);
        showToast(resp.message || '已取消', 'success');
        await loadTaskCenter();
        pollQueueStatus();
    } catch(e) {
        showToast('取消失败: ' + e.message, 'error');
    }
}

async function loadReportQuality() {
    const el = document.getElementById('reportQualityPanel');
    if (!el) return;
    try {
        const q = await aiGet('/api/ai/report-quality');
        const sr = q.signal_after_return || {};
        const reports = (q.reports || []).slice(0, 4);
        el.innerHTML = `<div class="quality-stats">
            <div><span>事实通过率</span><strong>${(q.fact_check_pass_rate||0).toFixed(1)}%</strong></div>
            <div><span>幻觉项</span><strong class="${q.hallucination_count ? 'down' : 'up'}">${q.hallucination_count||0}</strong></div>
            <div><span>后验收益</span><strong class="${(sr.avg_pnl_pct||0)>=0?'up':'down'}">${(sr.avg_pnl_pct||0)>=0?'+':''}${(sr.avg_pnl_pct||0).toFixed(2)}%</strong></div>
            <div><span>胜率</span><strong>${(sr.win_rate||0).toFixed(1)}%</strong></div>
        </div>
        <div class="quality-subtitle">最近报告</div>
        ${reports.length ? reports.map(r => `<div class="quality-report-row" onclick="viewReport(${Number(r.id)})">
            <span>${escapeHtml(r.name||r.code)} ${escapeHtml(r.code)}</span>
            <b>${r.fact_accuracy == null ? '未核对' : Number(r.fact_accuracy).toFixed(0) + '%'}</b>
            <small>${r.hallucinations||0}项</small>
        </div>`).join('') : '<div class="empty-row">暂无报告</div>'}`;
    } catch(e) {
        el.innerHTML = `<div class="empty-row">质量面板加载失败：${escapeHtml(e.message)}</div>`;
    }
}

async function loadStrategyReview() {
    const el = document.getElementById('strategyReviewPanel');
    if (!el) return;
    try {
        const metrics = await aiGet('/api/ai/task-metrics');
        const byStatus = metrics.by_status || {};
        const byDepth = metrics.by_depth || {};
        const failures = metrics.recent_failures || [];
        el.innerHTML = `<div class="quality-stats">
            <div><span>近200任务</span><strong>${metrics.total || 0}</strong></div>
            <div><span>平均耗时</span><strong>${(metrics.avg_elapsed || 0).toFixed(1)}s</strong></div>
            <div><span>运行中</span><strong>${(byStatus.running || 0) + (byStatus.queued || 0)}</strong></div>
            <div><span>失败</span><strong class="${(byStatus.failed || 0) ? 'down' : 'up'}">${byStatus.failed || 0}</strong></div>
        </div>
        <div class="quality-subtitle">深度分布</div>
        <div class="ai-task-meta">快速 ${byDepth.quick || 0} · 标准 ${byDepth.standard || 0} · 深度 ${byDepth.deep || 0} · 自定义 ${byDepth.custom || 0}</div>
        <div class="quality-subtitle">最近失败</div>
        ${failures.length ? failures.map(msg => `<div class="ai-task-error">${escapeHtml(String(msg).slice(0, 120))}</div>`).join('') : '<div class="empty-row">暂无失败</div>'}`;
    } catch (e) {
        el.innerHTML = `<div class="empty-row">复盘加载失败：${escapeHtml(e.message)}</div>`;
    }
}

async function loadReportVersions() {
    const el = document.getElementById('reportVersionsPanel');
    if (!el) return;
    const code = selectedCardCode || activeAnalysisCode;
    if (!code) {
        el.innerHTML = '<div class="empty-row">选择股票后查看报告版本</div>';
        return;
    }
    try {
        const data = await aiGet(`/api/ai/report-versions/${encodeURIComponent(code)}`);
        const versions = data.versions || [];
        if (!versions.length) {
            el.innerHTML = '<div class="empty-row">当前股票暂无历史报告</div>';
            return;
        }
        const latest = versions[0];
        el.innerHTML = versions.slice(0, 6).map((v, idx) => `<div class="quality-report-row">
            <div onclick="viewReport(${Number(v.id)})">
              <b>#${v.id} ${escapeHtml(v.signal || 'HOLD')}</b>
              <small>${escapeHtml(formatTime(v.created_at))} · ${escapeHtml(v.depth || '')}</small>
            </div>
            <button class="btn btn-sm" onclick="viewReport(${Number(v.id)})">查看</button>
            ${idx > 0 ? `<button class="btn btn-sm" onclick="compareReportVersions(${Number(v.id)}, ${Number(latest.id)})">对比</button>` : '<small>最新</small>'}
        </div>`).join('');
    } catch (e) {
        el.innerHTML = `<div class="empty-row">版本读取失败：${escapeHtml(e.message)}</div>`;
    }
}

async function compareReportVersions(leftId, rightId) {
    try {
        const data = await aiGet(`/api/ai/report-compare?left_id=${leftId}&right_id=${rightId}`);
        const d = data.diff || {};
        const el = document.getElementById('reportVersionsPanel');
        const left = data.left || {};
        const right = data.right || {};
        const html = `<div class="quality-subtitle">版本对比 #${leftId} → #${rightId}</div>
            <div class="quality-stats">
              <div><span>信号变化</span><strong class="${d.signal_changed ? 'down' : 'up'}">${d.signal_changed ? '有变化' : '未变化'}</strong></div>
              <div><span>置信度变化</span><strong class="${Number(d.confidence_delta || 0) >= 0 ? 'up' : 'down'}">${Number(d.confidence_delta || 0).toFixed(1)}</strong></div>
              <div><span>风险变化</span><strong class="${Number(d.risk_delta || 0) <= 0 ? 'up' : 'down'}">${Number(d.risk_delta || 0).toFixed(1)}</strong></div>
              <div><span>股票一致</span><strong>${d.same_code ? '是' : '否'}</strong></div>
            </div>
            <div class="ai-task-meta">旧版：${escapeHtml(left.signal || '—')} · ${escapeHtml(formatTime(left.created_at))}</div>
            <div class="ai-task-meta">新版：${escapeHtml(right.signal || '—')} · ${escapeHtml(formatTime(right.created_at))}</div>`;
        if (el) el.insertAdjacentHTML('afterbegin', html);
        showToast('版本对比已生成', 'success');
    } catch (e) {
        showToast('对比失败: ' + e.message, 'error');
    }
}

async function backtestConditionalDraft(draft) {
    try {
        const data = await aiPost('/api/ai/conditional-order/backtest', {
            code: draft.code,
            condition_type: draft.condition_type,
            target_price: draft.target_price,
            days: 90
        });
        showToast(`历史触发 ${data.trigger_count} 次，首触发后收益 ${data.post_trigger_return_pct == null ? '无样本' : data.post_trigger_return_pct + '%'}`, 'success');
    } catch (e) {
        showToast('回测失败: ' + e.message, 'error');
    }
}

async function loadRiskExposure() {
    const el = document.getElementById('riskExposurePanel');
    if (!el) return;
    try {
        const data = await aiGet('/api/portfolio/risk-exposure');
        const positions = (data.positions || []).slice(0, 4);
        el.innerHTML = `<div class="quality-stats">
            <div><span>总市值</span><strong>${formatMoney(data.total_market_value || 0)}</strong></div>
            <div><span>账户数</span><strong>${(data.accounts || []).length}</strong></div>
            <div><span>持仓数</span><strong>${(data.positions || []).length}</strong></div>
            <div><span>风险提示</span><strong class="${(data.warnings || []).length ? 'down' : 'up'}">${(data.warnings || []).length}</strong></div>
        </div>
        <div class="quality-subtitle">集中度</div>
        ${positions.length ? positions.map(p => `<div class="quality-report-row">
            <span>${escapeHtml(p.name || p.code)} ${escapeHtml(p.code || '')}</span>
            <b>${Number(p.weight_pct || 0).toFixed(1)}%</b>
            <small>${formatMoney(p.market_value_calc || 0)}</small>
        </div>`).join('') : '<div class="empty-row">暂无持仓</div>'}
        ${(data.warnings || []).map(w => `<div class="ai-task-error">${escapeHtml(w)}</div>`).join('')}`;
    } catch (e) {
        el.innerHTML = `<div class="empty-row">风险读取失败：${escapeHtml(e.message)}</div>`;
    }
}

async function loadEventsPanel() {
    const el = document.getElementById('eventsPanel');
    if (!el) return;
    try {
        const data = await aiGet('/api/events');
        const events = (data.events || []).slice(0, 6);
        el.innerHTML = events.length ? events.map(ev => `<div class="quality-report-row">
            <div>
              <b>${escapeHtml(ev.code || ev.type || '')}</b>
              <small>${escapeHtml(ev.title || '')}</small>
            </div>
            <small>${escapeHtml(formatTime(ev.time))}</small>
        </div>`).join('') : '<div class="empty-row">暂无事件</div>';
    } catch (e) {
        el.innerHTML = `<div class="empty-row">事件读取失败：${escapeHtml(e.message)}</div>`;
    }
}

function formatMoney(value) {
    const n = Number(value || 0);
    if (Math.abs(n) >= 10000) return `${(n / 10000).toFixed(1)}万`;
    return n.toFixed(0);
}
async function restoreActiveTask() {
    try { const d=await aiTaskClient().activeTask(); if(d.task_id){currentTaskId=d.task_id;activeAnalysisCode=d.code; const dc=d.depth?{analysts:d.selected_analysts||DEPTH_CONFIG[d.depth]?.analysts||DEPTH_CONFIG.standard.analysts,debate_rounds:d.debate_rounds??1,risk_rounds:d.risk_rounds??1,label:DEPTH_CONFIG[d.depth]?.label||'\u6807\u51c6'}:DEPTH_CONFIG.standard; showProgressPanel(d.code,d.task_id,dc); if(d.stages){for(const[s,st] of Object.entries(d.stages)){const el=document.getElementById(`stage-${s}`);if(el&&st==='completed')el.className='avatar-card completed';}} startSSE(d.task_id); } } catch(e){}
}

// === 信号绩效 ===
let _perfData = null;
let _perfFilter = 'open';

async function loadPerformanceTab(c) {
    c.innerHTML = '<div class="perf-empty">加载中...</div>';
    try {
        const [stats, tracking] = await Promise.all([
            aiGet('/api/signal/stats'),
            aiGet('/api/signal/tracking')
        ]);
        _perfData = { stats, tracking };
        renderPerformanceTab(c);
    } catch (e) {
        c.innerHTML = '<div class="perf-empty">暂无信号跟踪数据，AI分析报告生成后将自动记录</div>';
    }
}

function renderPerformanceTab(c) {
    if (!_perfData) return;
    const s = _perfData.stats;
    const all = _perfData.tracking || [];

    // 统计卡片
    const winRateClass = s.win_rate >= 0.6 ? 'perf-win-rate-good' : (s.win_rate < 0.5 ? 'perf-win-rate-bad' : '');
    let html = `<div class="perf-stats">
        <div class="perf-stat-card"><div class="stat-label">总跟踪</div><div class="stat-value">${s.total}</div></div>
        <div class="perf-stat-card"><div class="stat-label">持仓中</div><div class="stat-value">${s.open}</div></div>
        <div class="perf-stat-card"><div class="stat-label">胜率</div><div class="stat-value ${winRateClass}">${(s.win_rate*100).toFixed(1)}%</div></div>
        <div class="perf-stat-card"><div class="stat-label">平均收益</div><div class="stat-value ${s.avg_pnl_pct>=0?'price-up':'price-down'}">${s.avg_pnl_pct>=0?'+':''}${s.avg_pnl_pct.toFixed(2)}%</div></div>
        <div class="perf-stat-card"><div class="stat-label">超额收益</div><div class="stat-value ${s.avg_excess_return>=0?'price-up':'price-down'}">${s.avg_excess_return>=0?'+':''}${s.avg_excess_return.toFixed(2)}%</div></div>
        <div class="perf-stat-card"><div class="stat-label">平均持有</div><div class="stat-value">${Math.round(s.avg_hold_days)}天</div></div>
    </div>`;

    // 信号柱状图
    html += '<div class="perf-section"><div class="perf-section-title">每档信号绩效</div>';
    const bySignal = s.by_signal || {};
    const maxAbs = Math.max(1, ...Object.values(bySignal).map(v => Math.abs(v.avg_pnl || 0)));
    for (const sig of ['STRONG_BUY','BUY','OVERWEIGHT','HOLD','UNDERWEIGHT','SELL','STRONG_SELL']) {
        const d = bySignal[sig] || {count:0, win_rate:0, avg_pnl:0};
        const pct = (Math.abs(d.avg_pnl) / maxAbs) * 50;
        const isUp = d.avg_pnl >= 0;
        const barClass = isUp ? 'bar-up' : 'bar-down';
        const valClass = isUp ? 'price-up' : 'price-down';
        const label = SIG_LABEL[sig] || sig;
        html += `<div class="signal-bar-row">
            <span class="signal-bar-label">${label}</span>
            <div class="signal-bar-track">
                <div class="signal-bar-center"></div>
                <div class="signal-bar-fill ${barClass}" style="width:${pct}%"></div>
            </div>
            <span class="signal-bar-value ${valClass}">${d.avg_pnl>=0?'+':''}${d.avg_pnl.toFixed(1)}% (${d.count}笔)</span>
        </div>`;
    }
    html += '</div>';

    // 月度收益（简化：纯文本，不足3月显示占位）
    const monthly = s.monthly_returns || [];
    if (monthly.length >= 2) {
        html += '<div class="perf-section"><div class="perf-section-title">月度收益</div>';
        for (const m of monthly) {
            const cls = m.return_pct >= 0 ? 'price-up' : 'price-down';
            html += `<div style="display:flex;justify-content:space-between;padding:4px 0;font-size:0.85rem;">
                <span>${m.month}</span><span class="${cls}">${m.return_pct>=0?'+':''}${m.return_pct.toFixed(1)}% (${m.count}笔)</span></div>`;
        }
        html += '</div>';
    }

    // 筛选按钮
    const openCount = all.filter(r => r.status === 'open').length;
    const closedCount = all.filter(r => r.status === 'closed').length;
    html += `<div class="perf-filter-btns">
        <button class="perf-filter-btn ${_perfFilter==='all'?'active':''}" onclick="_perfFilter='all';renderPerformanceTab(document.getElementById('reportContent'))">全部(${all.length})</button>
        <button class="perf-filter-btn ${_perfFilter==='open'?'active':''}" onclick="_perfFilter='open';renderPerformanceTab(document.getElementById('reportContent'))">持仓中(${openCount})</button>
        <button class="perf-filter-btn ${_perfFilter==='closed'?'active':''}" onclick="_perfFilter='closed';renderPerformanceTab(document.getElementById('reportContent'))">已平仓(${closedCount})</button>
    </div>`;

    // 跟踪列表
    const filtered = _perfFilter === 'all' ? all : all.filter(r => r.status === _perfFilter);
    if (filtered.length === 0) {
        html += '<div class="perf-empty">暂无数据</div>';
    } else {
        for (const r of filtered) {
            const pnl = r.pnl_pct || ((r.current_price - r.entry_price) / r.entry_price * 100);
            const pnlCls = pnl >= 0 ? 'price-up' : 'price-down';
            const trendLabel = pnl >= 0 ? '盈利' : '亏损';
            const sigLabel = SIG_LABEL[r.signal] || r.signal;
            const sigClass = `signal-${r.signal.toLowerCase().replace(/_/g,'-')}`;
            const holdDays = r.hold_days || Math.floor((Date.now() - new Date(r.signal_date).getTime()) / 86400000);
            const currentPrice = r.current_price || r.entry_price;

            html += `<div class="perf-tracking-card">
                <div class="perf-tracking-info">
                    <div class="perf-tracking-name"><span class="${pnlCls}">${trendLabel}</span> ${r.name} ${r.code}
                        <span class="perf-tracking-signal ${sigClass}">${sigLabel}</span>
                    </div>
                    <div class="perf-tracking-detail">
                        入场 ¥${r.entry_price.toFixed(2)} → 现价 ¥${currentPrice.toFixed(2)}
                        ${r.target_price ? ` | 目标 ¥${r.target_price.toFixed(2)}` : ''}
                        | ${holdDays}天
                        ${r.exit_reason ? ` | ${r.exit_reason === 'signal_change' ? '信号反转' : r.exit_reason === 'stop_loss' ? '止损' : r.exit_reason === 'target_hit' ? '目标到达' : r.exit_reason === 'max_hold' ? '最大持有' : r.exit_reason}` : ''}
                    </div>
                </div>
                <div class="perf-tracking-pnl ${pnlCls}">${pnl>=0?'+':''}${pnl.toFixed(2)}%</div>
                ${r.status === 'open' ? `<button class="perf-btn-close" onclick="closeTracking(${r.id}, ${currentPrice})">平仓</button>` : ''}
            </div>`;
        }
    }

    c.innerHTML = html;
}

async function closeTracking(id, currentPrice) {
    if (!confirm('确认平仓？')) return;
    try {
        await aiPost(`/api/signal/tracking/${id}/close`, { exit_price: currentPrice });
        showToast('已平仓', 'success');
        // 刷新
        const c = document.getElementById('reportContent');
        loadPerformanceTab(c);
    } catch (e) {
        showToast('平仓失败: ' + e.message, 'error');
    }
}

Object.assign(window, {
    loadStrategyReview,
    loadAiReadiness,
    loadReportVersions,
    compareReportVersions,
    backtestConditionalDraft,
    loadRiskExposure,
    loadEventsPanel,
});
