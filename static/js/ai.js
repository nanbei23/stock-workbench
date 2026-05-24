/**
 * AI分析台 — v2 重构版
 * 左栏:自选股卡片(单选/批量选) | 中栏:指数+控制+卡通/进度/报告 | 右栏:异动+历史
 */

document.addEventListener('DOMContentLoaded', async () => {
    await Promise.all([loadAIStockCards(), loadReports(), loadIndices()]);
    initIdleStages();
    pollQueueStatus();
    setInterval(pollQueueStatus, 10000);
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
    quick: { analysts: ['market','fundamentals'], debate_rounds: 1, risk_rounds: 1, label: '\u26a1 \u5feb\u901f\u6a21\u5f0f' },
    standard: { analysts: ['market','social','news','fundamentals','policy','hot_money','lockup'], debate_rounds: 1, risk_rounds: 1, label: '\ud83d\udccb \u6807\u51c6\u6a21\u5f0f' },
    deep: { analysts: ['market','social','news','fundamentals','policy','hot_money','lockup'], debate_rounds: 3, risk_rounds: 3, label: '\ud83d\udd2c \u6df1\u5ea6\u6a21\u5f0f' },
    custom: { analysts: [], debate_rounds: 1, risk_rounds: 1, label: '\ud83d\udee0\ufe0f \u81ea\u5b9a\u4e49\u6a21\u5f0f' }
};

// 必选项
const MANDATORY_STAGES = new Set(['market', 'fundamentals', 'quality_gate', 'trader', 'pm']);
const ALL_ANALYSTS = ['market','social','news','fundamentals','policy','hot_money','lockup'];
const ALL_PIPELINE = ['quality_gate','debate','trader','risk','pm'];

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
    if (w <= 1200 && w > 768) c.classList.add('left-collapsed','right-collapsed');
    else c.classList.remove('left-collapsed','right-collapsed');
}

async function apiGet(url) { return (await fetch(url)).json(); }
async function apiPost(url, data = {}) {
    return (await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) })).json();
}

function initIdleStages() {
    const si = { market:{n:'技术',a:'📈',c:'sky'}, social:{n:'情绪',a:'🎭',c:'purple'}, news:{n:'新闻',a:'📰',c:'gold'}, fundamentals:{n:'基本面',a:'📋',c:'green'}, policy:{n:'政策',a:'⚖️',c:'rose'}, hot_money:{n:'游资',a:'🚀',c:'orange'}, lockup:{n:'解禁',a:'🔓',c:'cyan'} };
    const pi = { quality_gate:{n:'门控',a:'✅',c:'mint'}, debate:{n:'多空',a:'⚔️',c:'indigo'}, trader:{n:'交易',a:'💰',c:'coral'}, risk:{n:'风控',a:'🛡️',c:'slate'}, pm:{n:'决策',a:'👑',c:'gold'} };
    document.getElementById('analystStages').innerHTML = `<div class="avatar-selfie">${Object.entries(si).map(([id,x])=>`<div class="avatar-card idle" data-color="${x.c}" id="stage-${id}"><div class="avatar-emoji">${x.a}</div><div class="avatar-name">${x.n}</div></div>`).join('')}</div>`;
    document.getElementById('pipelineStages').innerHTML = `<div class="avatar-pipeline">${Object.entries(pi).map(([id,x])=>`<div class="avatar-card idle" data-color="${x.c}" id="stage-${id}"><div class="avatar-emoji">${x.a}</div><div class="avatar-name">${x.n}</div></div>`).join('')}</div>`;
    document.getElementById('progressBar').style.width = '0%';
    document.getElementById('progressText').textContent = '';
}

// === 左栏：自选股卡片 ===
async function loadAIStockCards() {
    try {
        const data = await apiGet('/api/watchlist');
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

            return `<div class="ai-stock-card" data-code="${s.code}" onclick="selectCard('${s.code}')">
                <div class="ai-stock-card-inner">
                    <div class="ai-sc-check" onclick="event.stopPropagation()"><input type="checkbox" data-code="${s.code}" onchange="updateBatchBar()"></div>
                    <div class="sc-left">
                        <div><div class="sc-name">${s.name||s.code}</div><div class="sc-code">${s.code}</div></div>
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

// === 指数 ===
async function loadIndices() {
    try { const d = await apiGet('/api/ai/suggestions'); renderIndices(d.indices||[]); } catch(e) {}
}
function renderIndices(indices) {
    const bar = document.getElementById('indicesBar'); if (!bar||!indices.length) return;
    bar.innerHTML = indices.map(i => `<div class="index-item"><span class="index-name">${i.name}</span><span class="index-price">${(i.price??0).toLocaleString()}</span><span class="index-change ${(i.change_pct??0)>=0?'price-up':'price-down'}">${(i.change_pct??0)>=0?'+':''}${(i.change_pct??0).toFixed(2)}%</span></div>`).join('');
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
async function startAnalysis() {
    let code = selectedCardCode;
    if (!code) { const b = getSelectedCodes(); if (b.length === 1) code = b[0]; }
    if (!code) return alert('\u8bf7\u5728\u5de6\u4fa7\u9009\u62e9\u4e00\u53ea\u80a1\u7968');
    await startAnalysisFor(code);
}

async function startAnalysisFor(code) {
    try {
        let dc;
        if (currentDepth === 'custom') {
            const sel = getCustomSelectedStages();
            if (sel.analysts.length < 2) return alert('请至少选择2个分析师阶段');
            dc = { analysts: sel.analysts, debate_rounds: sel.pipeline.includes('debate') ? 1 : 0, risk_rounds: sel.pipeline.includes('risk') ? 1 : 0, label: '🛠️ 自定义模式' };
        } else {
            dc = DEPTH_CONFIG[currentDepth] || DEPTH_CONFIG.standard;
        }
        const resp = await apiPost(`/api/ai/analyze/${code}`, { depth: currentDepth, selected_analysts: dc.analysts, debate_rounds: dc.debate_rounds, risk_rounds: dc.risk_rounds, model_mode: currentModelMode });
        if (resp.status === 'running') return alert('\u8be5\u80a1\u7968\u5df2\u6709\u4efb\u52a1\u8fd0\u884c');
        currentTaskId = resp.task_id; activeAnalysisCode = code;
        showProgressPanel(code, resp.task_id, dc);
        startSSE(resp.task_id);
    } catch (e) { alert('\u542f\u52a8\u5931\u8d25: ' + e.message); }
}

function showProgressPanel(code, taskId, depthCfg) {
    showBottomPanel('progress');
    const allA = ['market','social','news','fundamentals','policy','hot_money','lockup'];
    const allP = ['quality_gate','debate','trader','risk','pm'];
    const aa = depthCfg ? depthCfg.analysts : allA;
    const hd = !depthCfg || depthCfg.debate_rounds > 0;
    const hr = !depthCfg || depthCfg.risk_rounds > 0;
    const si = { market:{n:'技术',a:'📈',c:'sky'}, social:{n:'情绪',a:'🎭',c:'purple'}, news:{n:'新闻',a:'📰',c:'gold'}, fundamentals:{n:'基本面',a:'📋',c:'green'}, policy:{n:'政策',a:'⚖️',c:'rose'}, hot_money:{n:'游资',a:'🚀',c:'orange'}, lockup:{n:'解禁',a:'🔓',c:'cyan'}, quality_gate:{n:'门控',a:'✅',c:'mint'}, debate:{n:'多空',a:'⚔️',c:'indigo'}, trader:{n:'交易',a:'💰',c:'coral'}, risk:{n:'风控',a:'🛡️',c:'slate'}, pm:{n:'决策',a:'👑',c:'gold'} };
    document.getElementById('analystStages').innerHTML = `<div class="avatar-selfie">${allA.map(id => { const ac = aa.includes(id); return `<div class="avatar-card ${ac?'pending':'skipped'}" data-color="${si[id].c}" id="stage-${id}"><div class="avatar-emoji">${si[id].a}</div><div class="avatar-name">${si[id].n}${ac?'':' ⏭'}</div></div>`; }).join('')}</div>`;
    const ap = allP.filter(id => { if(id==='debate') return hd; if(id==='risk') return hr; return true; });
    document.getElementById('pipelineStages').innerHTML = `<div class="avatar-pipeline">${allP.map(id => { const ac = ap.includes(id); return `<div class="avatar-card ${ac?'pending':'skipped'}" data-color="${si[id].c}" id="stage-${id}"><div class="avatar-emoji">${si[id].a}</div><div class="avatar-name">${si[id].n}${ac?'':' ⏭'}</div></div>`; }).join('')}</div>`;
    window._activeStageTotal = aa.length + ap.length;
    document.getElementById('progressTitle').textContent = `🔍 ${depthCfg?.label||'标准'} 分析: ${code}`;
    document.getElementById('progressBar').style.width = '0%';
    document.getElementById('progressText').textContent = `0/${window._activeStageTotal} \u9636\u6bb5`;
    document.getElementById('cancelContainer').style.display = 'block';
    document.getElementById('queuePosition').textContent = '';
    const te = document.getElementById('tokenStats'); if(te) te.textContent = '';
}

// === SSE ===
function startSSE(taskId) {
    if (currentSSE) currentSSE.close();
    const es = new EventSource(`/api/ai/analyze/${taskId}/stream`);
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
        else if (d.type==='completed') { activeAnalysisCode=null; showReport(d.result, d.elapsed); es.close(); }
        else if (d.type==='failed') { activeAnalysisCode=null; showBottomPanel('progress'); initIdleStages(); alert('\u5206\u6790\u5931\u8d25: '+(d.error||'')); es.close(); }
    };
    es.onerror = () => { es.close(); pollTaskStatus(taskId); };
}

async function pollTaskStatus(taskId) {
    try {
        const s = await apiGet(`/api/ai/analyze/${taskId}/status`);
        if (s.status==='completed') { const r=await apiGet(`/api/ai/analyze/${taskId}/result`); showReport(r.result, r.elapsed); }
        else if (s.status==='failed') { showBottomPanel('progress'); initIdleStages(); alert('\u5931\u8d25'); }
        else setTimeout(()=>pollTaskStatus(taskId), 2000);
    } catch(e) {}
}

async function cancelAnalysis() {
    if (!currentTaskId) return alert('\u6ca1\u6709\u8fd0\u884c\u4e2d\u7684\u4efb\u52a1');
    if (!confirm('\u786e\u5b9a\u53d6\u6d88\uff1f')) return;
    try { const r = await apiPost(`/api/ai/analyze/${currentTaskId}/cancel`); if(r.status==='ok') { document.getElementById('cancelContainer').style.display='none'; if(currentSSE){currentSSE.close();currentSSE=null;} setTimeout(()=>{showBottomPanel('progress'); initIdleStages();},1000); } } catch(e) {}
}

// === 报告详情 ===
function showReport(result, elapsed) {
    if (!result) return;
    showBottomPanel('report');
    window._currentResult = result;
    const sig = (result.signal||'HOLD').toUpperCase();
    const labels = {BUY:'\u4e70\u5165',SELL:'\u5356\u51fa',HOLD:'\u6301\u6709'};
    const colors = {BUY:'#E07A5F',SELL:'#52B788',HOLD:'#F4A261'};
    document.getElementById('reportStockName').textContent = result.code||'';
    const se = document.getElementById('reportSignal');
    se.textContent = `${labels[sig]||sig} ${sig}`; se.style.background = colors[sig]||'#888'; se.style.color='#fff';
    // 事实账本准确率徽章
    const fce = document.getElementById('reportFactBadge');
    if (fce) {
        if (result._fact_check && (result._fact_check.verified + result._fact_check.mismatched) > 0) {
            const acc = result._fact_check.accuracy || 0;
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
    if(cb) cb.style.display = (sig==='BUY'||sig==='SELL')?'inline-block':'none';
    const ft = document.querySelector('.report-nav .nav-item[data-target]');
    if(ft) switchReportTab(ft, ft.dataset.target);
}
function closeReport() {
    showBottomPanel('progress');
    initIdleStages();
    window._currentResult = null;
}

function viewReport(id) {
    apiGet(`/api/ai/reports/${id}`).then(report => {
        let p = report.result || {};
        const r = { _reportId:id, code:report.code, signal:report.signal||p.signal||'HOLD', confidence:report.confidence||p.confidence, risk_score:report.risk_score||p.risk_score, target_price:p.target_price||null, reasoning:p.reasoning||report.final_decision||'', stages:{ market:report.market_report, social:report.sentiment_report, news:report.news_report, fundamentals:report.fundamentals_report, policy:report.policy_report, hot_money:report.hot_money_report, lockup:report.lockup_report, debate:report.investment_debate, risk:report.risk_debate, trader:report.trader_plan, pm:report.final_decision }, risk_debate:parseRiskDebate(p.risk_debate, report), _fact_check: report._fact_check, _bystander_verify: report._bystander_verify };
        showReport(r, report.duration_seconds);
    }).catch(e => console.error(e));
}

// === 报告Tab ===
function switchReportTab(btn, target) {
    document.querySelectorAll('.report-nav .nav-item').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    const c = document.getElementById('reportContent');
    const r = window._currentResult; if(!r||!c) return;
    const s = r.stages||{};
    const ai = {market:'\ud83d\udcc8 \u6280\u672f',social:'\ud83d\udcac \u60c5\u7eea',news:'\ud83d\udcf0 \u65b0\u95fb',fundamentals:'\ud83d\udccb \u57fa\u672c\u9762',policy:'\ud83c\udfdb\ufe0f \u653f\u7b56',hot_money:'\ud83d\udd25 \u8d44',lockup:'\ud83d\udd12 \u89e3\u7981'};
    if (ai[target]) { c.innerHTML=`<div class="report-section-title">${ai[target]}</div><div class="report-summary">${formatReport(s[target]||'\u6682\u65e0')}</div>`; return; }
    if (target==='debate') { const dt=s.debate||'\u6682\u65e0'; c.innerHTML=`<div class="report-section-title">\u2694\ufe0f \u591a\u7a7a\u8fa9\u8bba</div><div class="debate-dual"><div class="debate-side bull"><h5>\ud83d\udc02 \u591a\u5934</h5><div>${formatReport(extractSide(dt,'bull'))}</div></div><div class="debate-divider">\u26a1</div><div class="debate-side bear"><h5>\ud83d\udc3b \u7a7a\u5934</h5><div>${formatReport(extractSide(dt,'bear'))}</div></div></div>`; return; }
    if (target==='risk') { const rd=r.risk_debate||{}; c.innerHTML=`<div class="report-section-title">\ud83d\udee1\ufe0f \u98ce\u63a7</div><div class="debate-dual"><div class="debate-side" style="background:#E07A5F08;border:1px solid #E07A5F33"><h5>\ud83d\udd34 \u6fc0\u8fdb</h5><div>${formatReport(rd.aggressive||'\u6682\u65e0')}</div></div><div class="debate-side" style="background:#5B9BD508;border:1px solid #5B9BD533"><h5>\ud83d\udd35 \u4fdd\u5b88</h5><div>${formatReport(rd.conservative||'\u6682\u65e0')}</div></div><div class="debate-side" style="background:#52B78808;border:1px solid #52B78833"><h5>\ud83d\udfe2 \u4e2d\u6027</h5><div>${formatReport(rd.neutral||'\u6682\u65e0')}</div></div></div>${rd.decision?`<div style="margin-top:12px;padding:12px;background:rgba(244,162,97,0.08);border:1px solid rgba(244,162,97,0.2);border-radius:8px"><h5 style="margin:0 0 8px">\u2696\ufe0f \u88c1\u51b3</h5><div>${formatReport(rd.decision)}</div></div>`:''}`; return; }
    if (target==='pm') { c.innerHTML=`<div class="report-section-title">\ud83d\udc54 \u51b3\u7b56</div><div class="report-summary">${formatReport(r.reasoning||s.pm||'\u6682\u65e0')}</div>`; return; }
    if (target==='factcheck') {
        if (r._fact_check) {
            renderFactCheckInline(r._fact_check, c);
        } else if (r._reportId) {
            loadFactCheck(r._reportId, c);
        } else {
            c.innerHTML='⚠️ 请从历史报告中选择';
        }
        return;
    }
    if (target==='verify') {
        if (r._bystander_verify) {
            renderVerificationInline(r._bystander_verify, c);
        } else if (r._reportId) {
            loadVerification(r._reportId, c);
        } else {
            c.innerHTML='⚠️ 请从历史报告中选择';
        }
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
    const sec=[{k:'market',i:'\ud83d\udcc8',n:'\u6280\u672f'},{k:'social',i:'\ud83d\udcac',n:'\u60c5\u7eea'},{k:'news',i:'\ud83d\udcf0',n:'\u65b0\u95fb'},{k:'fundamentals',i:'\ud83d\udccb',n:'\u57fa\u672c\u9762'},{k:'policy',i:'\ud83c\udfdb\ufe0f',n:'\u653f\u7b56'},{k:'hot_money',i:'\ud83d\udd25',n:'\u8d44'},{k:'lockup',i:'\ud83d\udd12',n:'\u89e3\u7981'}];
    c.innerHTML=sec.filter(x=>s[x.k]).map(x=>`<div style="margin-bottom:16px"><div class="report-section-title">${x.i} ${x.n}</div><div class="report-summary">${formatReport(s[x.k])}</div></div>`).join('')+`<div style="margin-bottom:16px"><div class="report-section-title">\u2694\ufe0f \u8fa9\u8bba</div><div class="report-summary">${formatReport(s.debate||'\u6682\u65e0')}</div></div><div style="margin-bottom:16px"><div class="report-section-title">\ud83d\udc54 \u51b3\u7b56</div><div class="report-summary">${formatReport(s.pm||r.reasoning||'\u6682\u65e0')}</div></div>`;
}

function renderFactCheckInline(fc, c) {
    const ac = (fc.accuracy||0) >= 80 ? '#52B788' : (fc.accuracy||0) >= 50 ? '#F4A261' : '#E07A5F';
    c.innerHTML = `<div class="report-section-title">事实账本</div>
        <div style="display:flex;gap:16px;margin-bottom:12px;font-size:0.9rem">
            <span>准确率: <b style="color:${ac}">${fc.accuracy}%</b></span>
            <span style="color:#52B788">✅ ${fc.verified}</span>
            <span style="color:#E07A5F">❌ ${fc.mismatched}</span>
            <span style="color:var(--text-muted)">⚠️ ${fc.unverifiable}</span>
        </div>
        <table style="width:100%;font-size:0.82rem;border-collapse:collapse"><thead><tr style="border-bottom:1px solid var(--border-color)">
            <th style="text-align:left;padding:6px 8px">数据显示</th><th style="text-align:left;padding:6px 8px">报告中的值</th><th style="text-align:left;padding:6px 8px">实际值</th><th style="text-align:center;padding:6px 8px">状态</th>
        </tr></thead><tbody>${(fc.claims||[]).map(c => `<tr style="border-bottom:1px solid var(--border-color)">
            <td style="padding:6px 8px">${c.keyword}</td><td style="padding:6px 8px">${c.claimed_value||'—'}</td><td style="padding:6px 8px">${c.actual_value??'—'}</td>
            <td style="padding:6px 8px;text-align:center">${c.status==='verified'?'✅':c.status==='mismatch'?'<span style="color:#E07A5F">❌ AI幻觉</span>':'⚠️'}</td>
        </tr>`).join('')}</tbody></table>`;
}

async function loadFactCheck(rid, c) {
    c.innerHTML='<div style="text-align:center;padding:20px">\u23f3 \u6838\u5bf9\u4e2d...</div>';
    try { const d=await apiGet(`/api/ai/reports/${rid}/fact-check`); if(d.error){c.innerHTML='\u26a0\ufe0f '+d.error;return;} const ac=d.accuracy>=80?'#52B788':d.accuracy>=60?'#F4A261':'#E07A5F'; c.innerHTML=`<div class="report-section-title">\ud83d\udcca \u4e8b\u5b9e\u8d26\u672c</div><div style="display:flex;gap:16px;margin-bottom:16px;font-size:0.85rem"><span>\u4e00\u81f4\u7387\uff1a<b style="color:${ac}">${d.accuracy}%</b></span><span>\u2705 ${d.verified}</span><span style="color:#E07A5F">\u274c ${d.mismatched}</span><span style="color:#F4A261">\u26a0\ufe0f ${d.unverifiable}</span></div><table style="width:100%;font-size:0.82rem;border-collapse:collapse"><thead><tr style="border-bottom:1px solid var(--border-color)"><th style="text-align:left;padding:6px 8px">\u6570\u636e\u9879</th><th style="text-align:left;padding:6px 8px">\u5f15\u7528</th><th style="text-align:left;padding:6px 8px">\u5b9e\u9645</th><th style="text-align:center;padding:6px 8px">\u72b6\u6001</th></tr></thead><tbody>${d.claims.map(x=>`<tr style="border-bottom:1px solid var(--border-color)"><td style="padding:6px 8px">${x.keyword}</td><td style="padding:6px 8px">${x.claimed_value}</td><td style="padding:6px 8px">${x.actual_value??'\u2014'}</td><td style="padding:6px 8px;text-align:center">${x.status==='verified'?'\u2705':x.status==='mismatch'?'<span style="color:#E07A5F">\u274c</span>':'\u26a0\ufe0f'}</td></tr>`).join('')}</tbody></table>`; } catch(e){c.innerHTML='\u26a0\ufe0f '+e.message;}
}
function renderVerificationInline(bv, c) {
    const sc = (bv.overall_score||0) >= 80 ? '#52B788' : (bv.overall_score||0) >= 50 ? '#F4A261' : '#E07A5F';
    c.innerHTML = `<div class="report-section-title">报告复核</div>
        <div style="margin-bottom:16px;padding:12px;background:rgba(91,155,213,0.06);border-radius:8px">
            <div style="font-size:1.2rem;font-weight:600;color:${sc}">可信度：${bv.overall_score||'—'}/100</div>
            <div style="font-size:0.85rem;margin-top:4px">${bv.summary||''}</div>
        </div>
        ${(bv.hallucinations||[]).length > 0 ? `<div class="report-section-title" style="font-size:0.9rem">⚠️ 发现的问题</div>
            ${bv.hallucinations.map(h => `<div style="padding:8px 12px;margin-bottom:6px;border-left:3px solid ${h.severity==='high'?'#E07A5F':'#F4A261'}">
                <div style="font-size:0.85rem;font-weight:500">${h.claim||''}</div>
                <div style="font-size:0.8rem;color:var(--text-secondary)">${h.issue||''}</div>
            </div>`).join('')}` : '<div style="color:#52B788;font-size:0.85rem">✅ 未发现明显幻觉</div>'}
        <div style="margin-top:12px;font-size:0.75rem;color:var(--text-muted)">（报告生成时自动复核）</div>`;
}

async function loadVerification(rid, c) {
    c.innerHTML='<div style="text-align:center;padding:20px">\u23f3 \u65c1\u89c2\u8005\u6838\u5bf9...</div>';
    try { const d=await apiPost(`/api/ai/reports/${rid}/bystander-verify`); if(d.error){c.innerHTML='\u26a0\ufe0f '+d.error;return;} const r=d.result||{}; const sc=(r.overall_score||0)>=80?'#52B788':(r.overall_score||0)>=60?'#F4A261':'#E07A5F'; c.innerHTML=`<div class="report-section-title">\ud83d\udd0d \u590d\u5408\u9a8c\u8bc1 \u00b7 ${d.verify_model}</div><div style="margin-bottom:16px;padding:12px;background:rgba(91,155,213,0.06);border-radius:8px"><div style="font-size:1.2rem;font-weight:600;color:${sc}">\u53ef\u4fe1\u5ea6\uff1a${r.overall_score||'\u2014'}/100</div><div style="font-size:0.85rem;margin-top:4px">${r.summary||''}</div></div>${(r.hallucinations||[]).length?r.hallucinations.map(h=>`<div style="padding:8px 12px;margin-bottom:6px;border-left:3px solid ${h.severity==='high'?'#E07A5F':'#F4A261'}"><div style="font-size:0.85rem;font-weight:500">${h.claim||''}</div><div style="font-size:0.8rem;color:var(--text-secondary)">${h.issue||''}</div></div>`).join(''):'<div style="color:#52B788;font-size:0.85rem">\u2705 \u672a\u53d1\u73b0\u5e7b\u89c9</div>'}`; } catch(e){c.innerHTML='\u26a0\ufe0f '+e.message;}
}

function parseRiskDebate(rd, report) {
    if (rd && typeof rd === 'object') { if(['aggressive','conservative','neutral','decision'].some(k=>rd[k]&&rd[k].trim())) return rd; }
    let raw = report.risk_debate;
    if (typeof raw === 'string' && raw.trim()) { try { const p=JSON.parse(raw); if(typeof p==='object') return {aggressive:p.aggressive_history||p.aggressive||'',conservative:p.conservative_history||p.conservative||'',neutral:p.neutral_history||p.neutral||'',decision:p.judge_decision||p.decision||''}; } catch(e){} }
    return {decision:report.final_decision||'\u6682\u65e0'};
}

// === 格式化 ===
function formatReport(text) {
    if (!text) return '<span class="text-muted">\u6682\u65e0\u6570\u636e</span>';
    if (typeof text === 'object') text = text.judge_decision||text.decision||text.content||text.report||JSON.stringify(text,null,2);
    text = String(text).replace(/\\\\n/g,'\n');
    try { const p=JSON.parse(text); if(typeof p==='object') text=p.judge_decision||p.decision||p.content||p.report||JSON.stringify(p,null,2); text=text.replace(/\\\\n/g,'\n'); } catch(e){}
    return text.replace(/^### (.*$)/gm,'<h5>$1</h5>').replace(/^## (.*$)/gm,'<h4>$1</h4>').replace(/^# (.*$)/gm,'<h3>$1</h3>').replace(/^---+$/gm,'<hr>').replace(/\*\*(.*?)\*\*/g,'<strong>$1</strong>').replace(/\*(.*?)\*/g,'<em>$1</em>').replace(/`(.*?)`/g,'<code>$1</code>').replace(/^- (.*$)/gm,'\u2022 $1').replace(/\n/g,'<br>');
}
function formatElapsed(s) { if(!s) return '\u2014'; return `${Math.floor(s/60)}:${Math.floor(s%60).toString().padStart(2,'0')}`; }
function formatTime(ts) { if(!ts) return '\u2014'; const d=new Date(ts); return `${d.getMonth()+1}/${d.getDate()} ${d.getHours()}:${d.getMinutes().toString().padStart(2,'0')}`; }

// === 历史报告 ===
async function loadReports(code='') {
    try { const d=await apiGet(code?`/api/ai/reports?code=${code}`:'/api/ai/reports'); if(!code&&d.reports) reportCodes=new Set(d.reports.map(r=>r.code)); const l=document.getElementById('reportsList'); if(!d.reports?.length){l.innerHTML='<div class="empty-row">\u6682\u65e0</div>';return;} l.innerHTML=d.reports.map(r=>`<div class="report-item"><div class="report-item-header" onclick="viewReport(${r.id})"><span class="report-signal signal-${(r.signal||'hold').toLowerCase()}">${r.signal||'\u2014'}</span><span class="report-code">${r.code}</span><span class="report-time">${formatTime(r.created_at)}</span></div><div class="report-item-meta" onclick="viewReport(${r.id})"><span>\u7f6e\u4fe1: ${r.confidence?(r.confidence*100).toFixed(0)+'%':'\u2014'}</span><span>\u8017\u65f6: ${formatElapsed(r.duration_seconds)}</span></div><div class="report-item-actions"><a href="/api/ai/report/${r.id}/pdf" class="btn btn-sm" onclick="event.stopPropagation()">\ud83d\udcc4</a></div></div>`).join(''); } catch(e){}
}
function searchReports(e) { if(e.key==='Enter') loadReports(e.target.value.trim()); }

// === 异动 ===
async function triggerL1() {
    try { const d=await apiPost('/api/ai/trigger'); if(d.anomalies?.length) renderAnomalies(d.anomalies); else document.getElementById('anomalyLog').innerHTML=`<div class="anomaly-empty">\u65e0\u5f02\u52a8\uff08${d.checked}\u53ea\uff09</div>`; } catch(e){}
}
function renderAnomalies(a) {
    document.getElementById('anomalyLog').innerHTML=a.map(x=>`<div class="anomaly-item"><div class="anomaly-header"><span class="anomaly-level">${x.level}</span><span class="anomaly-time">${x.time}</span><span class="anomaly-stock">${x.name} ${x.code}</span><span class="anomaly-change ${x.change_pct>=0?'price-up':'price-down'}">${x.change_pct>=0?'+':''}${x.change_pct.toFixed(2)}%</span></div><div class="anomaly-message">${x.message}</div></div>`).join('');
}

// === gbrain + 条件单 ===
async function saveToGbrain() {
    const r=window._currentResult; if(!r) return alert('\u6ca1\u6709\u7ed3\u679c');
    const slug=`deep-analysis/${r.code||'x'}-${new Date().toISOString().slice(0,10)}`; const title=`${r.code} \u6df1\u5ea6\u5206\u6790`;
    try { const resp=await fetch('/api/ai/gbrain/save',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:`slug=${encodeURIComponent(slug)}&title=${encodeURIComponent(title)}&content=${encodeURIComponent(r.reasoning||'')}`}); const d=await resp.json(); alert(d.status==='ok'?'\u5df2\u5b58\u5165: '+slug:'\u5931\u8d25'); } catch(e){alert('\u5931\u8d25: '+e.message);}
}
async function generateCondOrder() {
    const r=window._currentResult; if(!r?.code) return showToast('\u65e0\u7ed3\u679c','error'); if(!r.target_price) return showToast('\u65e0\u76ee\u6807\u4ef7','error');
    const sig=(r.signal||'HOLD').toUpperCase(); if(sig==='HOLD') return showToast('\u6301\u6709\u4e0d\u751f\u6210','warning');
    try { const resp=await apiPost('/api/orders',{code:r.code,side:sig==='BUY'?'buy':'sell',condition_type:sig==='BUY'?'price_lte':'price_gte',trigger_price:r.target_price}); if(resp.id||resp.status==='ok') showToast(`\u6761\u4ef6\u5355: ${sig} ${r.code} @ \u00a5${r.target_price}`,'success'); else showToast('\u5931\u8d25','error'); } catch(e){showToast(e.message,'error');}
}
function downloadPdf() { const r=window._currentResult; if(!r?._reportId) return alert('\u8bf7\u9009\u62e9\u62a5\u544a'); window.open(`/api/ai/report/${r._reportId}/pdf`,'_blank'); }

function showToast(msg, type) {
    let t=document.getElementById('globalToast'); if(!t){t=document.createElement('div');t.id='globalToast';t.style.cssText='position:fixed;top:60px;right:20px;z-index:99999;padding:12px 20px;border-radius:8px;font-size:0.9rem;font-weight:600;box-shadow:0 4px 16px rgba(0,0,0,0.15);transition:opacity 0.3s;max-width:360px;';document.body.appendChild(t);}
    const c={success:'#52B788',error:'#E07A5F',warning:'#F4A261'}; t.style.background=c[type]||c.success; t.style.color='#fff'; t.textContent=msg; t.style.opacity='1'; clearTimeout(window._toastTimer); window._toastTimer=setTimeout(()=>{t.style.opacity='0';},3500);
}

// === 队列 + 恢复 ===
async function pollQueueStatus() {
    try { const d=await apiGet('/api/ai/queue/status'); const el=document.getElementById('queuePanelText'); const p=el?.closest('.queue-panel'); if(!el) return; if(d.running>0||d.queued>0){el.textContent=`\ud83d\udd04 \u8fd0\u884c:${d.running} \u6392\u961f:${d.queued||0}`;if(p)p.style.display='';} else if(p)p.style.display='none'; } catch(e){}
}
async function restoreActiveTask() {
    try { const d=await apiGet('/api/ai/active-task'); if(d.task_id){currentTaskId=d.task_id;activeAnalysisCode=d.code; const dc=d.depth?{analysts:d.selected_analysts||DEPTH_CONFIG[d.depth]?.analysts||DEPTH_CONFIG.standard.analysts,debate_rounds:d.debate_rounds??1,risk_rounds:d.risk_rounds??1,label:DEPTH_CONFIG[d.depth]?.label||'\u6807\u51c6'}:DEPTH_CONFIG.standard; showProgressPanel(d.code,d.task_id,dc); if(d.stages){for(const[s,st] of Object.entries(d.stages)){const el=document.getElementById(`stage-${s}`);if(el&&st==='completed')el.className='avatar-card completed';}} startSSE(d.task_id); } } catch(e){}
}
