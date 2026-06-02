const SHADOW_API = '/api/shadow';
const PERFORMANCE_API = '/api/performance';
const SIGNAL_ORDER = ['STRONG_BUY', 'BUY', 'OVERWEIGHT', 'HOLD', 'UNDERWEIGHT', 'SELL', 'STRONG_SELL'];
const SELL_SIGNALS = new Set(['STRONG_SELL', 'SELL', 'UNDERWEIGHT']);

let signalTrackingState = {
  filter: 'open',
  rows: [],
};
let performanceState = {
  tab: 'signal',
  filters: {
    window: '30',
    model_mode: 'all',
    depth: 'all',
  },
};

function shadowNum(value, digits = 2) {
  const n = Number(value || 0);
  return Number.isFinite(n) ? n.toFixed(digits) : '--';
}

function shadowMoney(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return '--';
  return `${n >= 0 ? '' : '-'}¥${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 3 })}`;
}

function shadowShares(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return '--';
  return n.toLocaleString('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 3 });
}

function shadowClass(value) {
  const n = Number(value || 0);
  if (n > 0) return 'price-up';
  if (n < 0) return 'price-down';
  return 'muted';
}

function shadowPct(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '--';
  return `${n > 0 ? '+' : ''}${n.toFixed(3)}%`;
}

function signalText(signal) {
  const map = {
    STRONG_BUY: '强烈买入',
    BUY: '买入',
    OVERWEIGHT: '增持',
    HOLD: '持有',
    UNDERWEIGHT: '减持',
    SELL: '卖出',
    STRONG_SELL: '强烈卖出',
  };
  return map[String(signal || '').toUpperCase()] || signal || '--';
}

function actionText(action) {
  return action === 'sell' ? '卖出' : '买入';
}

function planStageText(stage) {
  const map = {
    screening: '初筛',
    shortlist: '精选',
    final: '最终建仓',
  };
  return map[String(stage || '').toLowerCase()] || stage || '--';
}

function planStrategyText(strategy) {
  const map = {
    auto: '自动',
    full_text: '完整原文',
    summary_plus_evidence: '摘要+证据',
    candidate_screening: '候选筛选',
    single: '单模型',
    dual: '双模型',
    per_role: '按角色',
  };
  return map[String(strategy || '').toLowerCase()] || strategy || '--';
}

function nullablePct(value) {
  return value == null || Number.isNaN(Number(value))
    ? '<span class="muted">--</span>'
    : shadowPct(Number(value));
}

function planFollowText(deviation = {}) {
  if (!deviation || !deviation.evaluated) return '--';
  const rate = deviation.follow_rate == null ? '--' : shadowPct(deviation.follow_rate);
  return `${rate}<br><span class="muted">缺口 ${shadowMoney(deviation.amount_gap || 0)}</span>`;
}

function allocationWarningText(allocation = {}) {
  const warnings = allocation.warnings || [];
  if (warnings.length) return warnings.map(escapeHtml).join('<br>');
  const amount = Number(allocation.suggested_amount || 0);
  const cash = Number(allocation.cash_total || 0);
  if (amount || cash) return `建议 ${shadowMoney(amount)}<br><span class="muted">现金 ${shadowMoney(cash)}</span>`;
  return '<span class="muted">--</span>';
}

async function shadowFetch(path, options = {}) {
  const resp = await fetch(`${SHADOW_API}${path}`, options);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || data.error || `HTTP ${resp.status}`);
  return data;
}

async function signalFetch(path, options = {}) {
  const resp = await fetch(`/api/signal${path}`, options);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || data.error || `HTTP ${resp.status}`);
  return data;
}

async function performanceFetch(path, options = {}) {
  const resp = await fetch(`${PERFORMANCE_API}${path}`, options);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || data.error || `HTTP ${resp.status}`);
  return data;
}

function queryFromFilters(extra = {}) {
  const filters = { ...performanceState.filters, ...extra };
  const params = new URLSearchParams();
  params.set('window', filters.window || 'all');
  if (filters.model_mode && filters.model_mode !== 'all') params.set('model_mode', filters.model_mode);
  if (filters.depth && filters.depth !== 'all') params.set('depth', filters.depth);
  params.set('limit', '120');
  return params.toString();
}

function renderShadowKpis(summary) {
  const orders = summary.orders || {};
  const positions = summary.positions || {};
  const comparison = summary.comparison || {};
  const calibration = summary.calibration || {};
  const rows = [
    ['影子委托', orders.total || 0, `已成交 ${orders.filled || 0} · 待补价 ${orders.pending || 0}`],
    ['影子持仓', positions.count || 0, `买入 ${orders.buys || 0} · 卖出 ${orders.sells || 0}`],
    ['影子市值', shadowMoney(positions.market_value), '按当前行情估算'],
    ['影子浮盈', shadowMoney(positions.unrealized_pnl), `${shadowNum(positions.unrealized_pnl_pct)}%`],
    ['实盘差异', shadowMoney(comparison.pnl_gap), `市值差 ${shadowMoney(comparison.market_value_gap)}`],
    ['AI校准', shadowPct(calibration.hit_rate), `样本 ${calibration.evaluated || 0} · 均值 ${shadowPct(calibration.avg_return_pct)}`],
  ];
  document.getElementById('shadowKpis').innerHTML = rows.map(([label, value, hint]) => `
    <div class="shadow-kpi">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(String(value))}</strong>
      <small>${escapeHtml(String(hint))}</small>
    </div>
  `).join('');
}

function renderCalibrationSummary(summary = {}) {
  const el = document.getElementById('shadowCalibrationSummary');
  el.innerHTML = `
    <div><span>可评估样本</span><strong>${Number(summary.evaluated || 0).toLocaleString()}</strong></div>
    <div><span>方向命中率</span><strong class="${shadowClass(summary.hit_rate - 50)}">${shadowPct(summary.hit_rate)}</strong></div>
    <div><span>平均后验收益</span><strong class="${shadowClass(summary.avg_return_pct)}">${shadowPct(summary.avg_return_pct)}</strong></div>
    <div><span>Brier Score</span><strong>${summary.brier_score == null ? '--' : Number(summary.brier_score).toFixed(4)}</strong></div>
    <div><span>正负样本</span><strong>${Number(summary.positive || 0)} / ${Number(summary.negative || 0)}</strong></div>
  `;
}

function renderStatRows(items, emptyText) {
  if (!items || !items.length) {
    return `<div class="shadow-empty">${escapeHtml(emptyText)}</div>`;
  }
  return items.map(item => {
    const hitRate = Number(item.hit_rate || 0);
    const avgReturn = Number(item.avg_return_pct || 0);
    const gap = item.calibration_gap == null ? '--' : shadowPct(item.calibration_gap);
    return `
      <div class="shadow-stat-row">
        <div>
          <b>${escapeHtml(signalText(item.label || item.key))}</b>
          <small>${Number(item.count || 0)} 笔 · 命中 ${Number(item.wins || 0)} 笔</small>
        </div>
        <div class="shadow-meter" title="命中率 ${shadowPct(hitRate)}">
          <i style="width:${Math.max(0, Math.min(hitRate, 100))}%"></i>
        </div>
        <div>
          <b class="${shadowClass(avgReturn)}">${shadowPct(avgReturn)}</b>
          <small>偏差 ${escapeHtml(gap)}</small>
        </div>
      </div>
    `;
  }).join('');
}

function renderShadowCalibration(data) {
  renderCalibrationSummary(data.summary || {});
  document.getElementById('shadowConfidenceBuckets').innerHTML = renderStatRows(
    data.by_confidence || [],
    '暂无可复盘的置信度样本'
  );
  const tips = data.recommendations || [];
  document.getElementById('shadowCalibrationTips').innerHTML = tips.length
    ? tips.map(tip => `<p class="shadow-insight">${escapeHtml(tip)}</p>`).join('')
    : '<p class="shadow-insight">暂无明显偏差。</p>';
}

function renderSignalPerformanceSummary(stats = {}) {
  const el = document.getElementById('signalPerformanceSummary');
  el.innerHTML = `
    <div><span>总跟踪</span><strong>${Number(stats.total || 0).toLocaleString()}</strong></div>
    <div><span>已闭环</span><strong>${Number(stats.closed || 0).toLocaleString()}</strong></div>
    <div><span>胜率</span><strong class="${shadowClass((Number(stats.win_rate || 0) * 100) - 50)}">${shadowPct(Number(stats.win_rate || 0) * 100)}</strong></div>
    <div><span>平均收益</span><strong class="${shadowClass(stats.avg_pnl_pct)}">${shadowPct(stats.avg_pnl_pct)}</strong></div>
    <div><span>超额收益</span><strong class="${shadowClass(stats.avg_excess_return)}">${shadowPct(stats.avg_excess_return)}</strong></div>
    <div><span>基准样本</span><strong>${Number(stats.benchmark_coverage || 0).toLocaleString()}</strong></div>
  `;
}

function renderSignalPerformanceBars(bySignal = {}) {
  const rows = SIGNAL_ORDER.map(signal => {
    const item = bySignal[signal] || { count: 0, win_rate: 0, avg_pnl: 0 };
    return {
      key: signal,
      label: signalText(signal),
      count: Number(item.count || 0),
      wins: Math.round(Number(item.count || 0) * Number(item.win_rate || 0)),
      hit_rate: Number(item.win_rate || 0) * 100,
      avg_return_pct: Number(item.avg_pnl || 0),
      calibration_gap: null,
    };
  }).filter(item => item.count > 0);
  document.getElementById('signalPerformanceBars').innerHTML = renderStatRows(rows, '暂无已闭环的信号绩效样本');
}

function trackingPnl(row) {
  if (row.pnl_pct != null) return Number(row.pnl_pct || 0);
  const entry = Number(row.entry_price || 0);
  const current = Number(row.current_price || row.entry_price || 0);
  if (entry <= 0) return 0;
  const raw = (current - entry) / entry * 100;
  return SELL_SIGNALS.has(String(row.signal || '').toUpperCase()) ? -raw : raw;
}

function renderSignalTrackingRows() {
  const el = document.getElementById('signalTrackingBody');
  const filter = signalTrackingState.filter;
  const rows = (signalTrackingState.rows || []).filter(row => filter === 'all' || row.status === filter);
  document.querySelectorAll('#signalTrackingFilters button').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.filter === filter);
  });
  if (!rows.length) {
    el.innerHTML = '<tr><td colspan="8"><div class="shadow-empty">暂无对应状态的信号跟踪记录。</div></td></tr>';
    return;
  }
  el.innerHTML = rows.slice(0, 80).map(row => {
    const pnl = trackingPnl(row);
    const entry = Number(row.entry_price || 0);
    const current = Number(row.exit_price || row.current_price || row.entry_price || 0);
    const holdDays = Number(row.hold_days || Math.max(0, Math.floor((Date.now() - new Date(row.signal_date || Date.now()).getTime()) / 86400000)));
    const statusText = row.status === 'closed' ? '已闭环' : '持仓中';
    const closeAction = row.status === 'open'
      ? `<button class="btn btn-xs" onclick="closeSignalTracking(${Number(row.id)}, ${current || entry || 0})">平仓</button>`
      : '<span class="muted">--</span>';
    return `
      <tr>
        <td><b>${escapeHtml(row.name || row.code)}</b><br><span class="muted">${escapeHtml(row.code || '')}</span></td>
        <td>${escapeHtml(signalText(row.signal))}</td>
        <td>${entry ? shadowNum(entry, 3) : '<span class="muted">--</span>'}</td>
        <td>${current ? shadowNum(current, 3) : '<span class="muted">--</span>'}</td>
        <td class="${shadowClass(pnl)}">${shadowPct(pnl)}</td>
        <td>${holdDays}天</td>
        <td>${statusText}</td>
        <td>${closeAction}</td>
      </tr>
    `;
  }).join('');
}

function renderSignalPerformance(stats, tracking) {
  renderSignalPerformanceSummary(stats || {});
  renderSignalPerformanceBars((stats || {}).by_signal || {});
  signalTrackingState.rows = tracking || [];
  renderSignalTrackingRows();
}

function renderFilterOptions(filters = {}) {
  const options = filters.options || {};
  const model = document.getElementById('performanceModel');
  const depth = document.getElementById('performanceDepth');
  const win = document.getElementById('performanceWindow');
  if (win) win.value = performanceState.filters.window;
  if (model) {
    const current = performanceState.filters.model_mode;
    model.innerHTML = '<option value="all">全部模型</option>' + (options.model_modes || [])
      .map(item => `<option value="${escapeAttr(item)}">${escapeHtml(item)}</option>`)
      .join('');
    model.value = current;
  }
  if (depth) {
    const current = performanceState.filters.depth;
    depth.innerHTML = '<option value="all">全部深度</option>' + (options.depths || [])
      .map(item => `<option value="${escapeAttr(item)}">${escapeHtml(item)}</option>`)
      .join('');
    depth.value = current;
  }
}

function renderBreakdownRows(rows, emptyText) {
  if (!rows || !rows.length) {
    return `<div class="shadow-empty">${escapeHtml(emptyText)}</div>`;
  }
  return rows.map(row => {
    const winRate = Number(row.win_rate || 0) * 100;
    return `
      <div class="shadow-stat-row">
        <div>
          <b>${escapeHtml(row.label || '--')}</b>
          <small>${Number(row.count || 0)} 笔 · 胜率 ${shadowPct(winRate)}</small>
        </div>
        <div class="shadow-meter"><i style="width:${Math.max(0, Math.min(winRate, 100))}%"></i></div>
        <div>
          <b class="${shadowClass(row.avg_pnl)}">${shadowPct(row.avg_pnl)}</b>
          <small>超额 ${shadowPct(row.avg_excess_return)}</small>
        </div>
      </div>
    `;
  }).join('');
}

function renderModelCalibration(signalStats, calibration) {
  document.getElementById('modelModeBreakdown').innerHTML = renderBreakdownRows(
    signalStats.by_model_mode || [],
    '暂无模型模式样本'
  );
  document.getElementById('depthBreakdown').innerHTML = renderBreakdownRows(
    signalStats.by_depth || [],
    '暂无分析深度样本'
  );
  document.getElementById('confidenceModelBuckets').innerHTML = renderStatRows(
    calibration.by_confidence || [],
    '暂无置信度样本'
  );
  const brier = calibration.summary && calibration.summary.brier_score != null
    ? `当前 Brier Score 为 ${Number(calibration.summary.brier_score).toFixed(4)}，越低代表置信度越贴近真实命中结果。`
    : '暂无足够样本计算 Brier Score。';
  const tips = [brier, ...(calibration.recommendations || [])];
  document.getElementById('modelCalibrationTips').innerHTML = tips
    .map(tip => `<p class="shadow-insight">${escapeHtml(tip)}</p>`)
    .join('');
}

function renderDeviation(data = {}) {
  const rows = data.rows || [];
  const el = document.getElementById('shadowDeviationGrid');
  if (!rows.length) {
    el.innerHTML = '<div class="shadow-empty">暂无执行偏差数据。</div>';
    return;
  }
  el.innerHTML = rows.map(row => `
    <div class="shadow-deviation-card">
      <span>${escapeHtml(row.label)}</span>
      <strong>${Number(row.count || 0)} 只</strong>
      <small class="${shadowClass(row.pnl_gap)}">盈亏差 ${shadowMoney(row.pnl_gap)}</small>
    </div>
  `).join('');
}

function renderPositionPlanPerformance(data = {}) {
  const plans = data.plans || [];
  const trackedPlans = plans.filter(plan => Number(plan.tracked || 0) > 0);
  const trackedItems = trackedPlans.reduce((sum, plan) => sum + Number(plan.tracked || 0), 0);
  const avgPnlValues = trackedPlans
    .map(plan => Number(plan.avg_pnl_pct))
    .filter(Number.isFinite);
  const winRateValues = trackedPlans
    .map(plan => Number(plan.win_rate))
    .filter(Number.isFinite);
  const avgPnl = avgPnlValues.length
    ? avgPnlValues.reduce((sum, value) => sum + value, 0) / avgPnlValues.length
    : null;
  const portfolioReturnValues = trackedPlans
    .map(plan => Number(plan.portfolio_return_pct))
    .filter(Number.isFinite);
  const avgPortfolioReturn = portfolioReturnValues.length
    ? portfolioReturnValues.reduce((sum, value) => sum + value, 0) / portfolioReturnValues.length
    : null;
  const avgWinRate = winRateValues.length
    ? winRateValues.reduce((sum, value) => sum + value, 0) / winRateValues.length
    : null;
  const summaryEl = document.getElementById('positionPlanSummary');
  if (summaryEl) {
    summaryEl.innerHTML = `
      <div><span>计划数</span><strong>${Number(data.count || plans.length || 0).toLocaleString()}</strong></div>
      <div><span>已跟踪标的</span><strong>${trackedItems.toLocaleString()}</strong></div>
      <div><span>组合收益</span><strong class="${shadowClass(avgPortfolioReturn)}">${avgPortfolioReturn == null ? '--' : shadowPct(avgPortfolioReturn)}</strong></div>
      <div><span>计划胜率</span><strong class="${shadowClass((avgWinRate || 0) - 50)}">${avgWinRate == null ? '--' : shadowPct(avgWinRate)}</strong></div>
    `;
  }
  const stageEl = document.getElementById('positionPlanStageRows');
  if (stageEl) {
    const rows = [
      ...(data.by_stage || []).map(stage => ({
      label: planStageText(stage.stage),
      count: stage.plans,
      wins: stage.tracked,
      hit_rate: stage.tracked ? 100 : 0,
      avg_return_pct: stage.avg_portfolio_return_pct ?? stage.avg_plan_pnl_pct,
      calibration_gap: null,
      })),
      ...(data.by_model_strategy || []).map(model => ({
        label: `模型：${planStrategyText(model.model_strategy)}`,
        count: model.plans,
        wins: model.tracked,
        hit_rate: model.tracked ? 100 : 0,
        avg_return_pct: model.avg_portfolio_return_pct ?? model.avg_plan_pnl_pct,
        calibration_gap: null,
      })),
    ];
    stageEl.innerHTML = renderStatRows(rows, '暂无建仓计划阶段样本');
  }
  const body = document.getElementById('positionPlanPerformanceBody');
  if (!body) return;
  if (!plans.length) {
    body.innerHTML = '<tr><td colspan="15"><div class="shadow-empty">暂无已采纳的最终建仓计划。先在 AI报告库生成计划，并采纳为 AI 绩效基准。</div></td></tr>';
    return;
  }
  body.innerHTML = plans.map(plan => `
    <tr>
      <td><b>${escapeHtml(plan.title || plan.plan_id)}</b><br><span class="muted">${escapeHtml(plan.plan_id || '')}</span></td>
      <td>${escapeHtml(planStageText(plan.stage))}</td>
      <td>${escapeHtml(planStrategyText(plan.context_strategy))}</td>
      <td>${escapeHtml(planStrategyText(plan.model_strategy))}</td>
      <td>${Number(plan.items || 0).toLocaleString()}</td>
      <td>${Number(plan.tracked || 0).toLocaleString()}</td>
      <td class="${shadowClass((Number(plan.win_rate || 0)) - 50)}">${plan.win_rate == null ? '<span class="muted">--</span>' : shadowPct(plan.win_rate)}</td>
      <td class="${shadowClass(plan.portfolio_return_pct)}">${nullablePct(plan.portfolio_return_pct)}</td>
      <td class="${shadowClass(plan.portfolio_excess_return)}">${nullablePct(plan.portfolio_excess_return)}</td>
      <td class="${shadowClass(plan.horizon_returns && plan.horizon_returns['1'])}">${nullablePct(plan.horizon_returns && plan.horizon_returns['1'])}</td>
      <td class="${shadowClass(plan.horizon_returns && plan.horizon_returns['3'])}">${nullablePct(plan.horizon_returns && plan.horizon_returns['3'])}</td>
      <td class="${shadowClass(plan.horizon_returns && plan.horizon_returns['20'])}">${nullablePct(plan.horizon_returns && plan.horizon_returns['20'])}</td>
      <td class="${shadowClass(plan.max_drawdown_pct)}">${nullablePct(plan.max_drawdown_pct)}</td>
      <td>${planFollowText(plan.deviation)}</td>
      <td>${allocationWarningText(plan.allocation)}</td>
    </tr>
  `).join('');
}

function setSignalTrackingFilter(filter) {
  signalTrackingState.filter = filter;
  renderSignalTrackingRows();
}

function setPerformanceFilter(key, value) {
  performanceState.filters[key] = value || 'all';
  loadShadowPage();
}

function setPerformanceTab(tab) {
  performanceState.tab = tab;
  document.querySelectorAll('#performanceTabs button').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tab);
  });
  document.querySelectorAll('[data-performance-panel]').forEach(panel => {
    panel.hidden = panel.dataset.performancePanel !== tab;
  });
}

function renderShadowPositions(data) {
  const rows = data.positions || [];
  const el = document.getElementById('shadowPositionsBody');
  if (!rows.length) {
    el.innerHTML = '<tr><td colspan="5"><div class="shadow-empty">暂无影子持仓。点击“同步AI报告”从历史报告生成。</div></td></tr>';
    return;
  }
  el.innerHTML = rows.map(row => `
    <tr>
      <td><b>${escapeHtml(row.name || row.code)}</b><br><span class="muted">${escapeHtml(row.code)}</span></td>
      <td>${shadowShares(row.total_shares)}</td>
      <td>${shadowNum(row.avg_cost, 3)}</td>
      <td>${shadowNum(row.current_price, 3)}</td>
      <td class="${shadowClass(row.unrealized_pnl)}">${shadowMoney(row.unrealized_pnl)}<br><span class="muted">${shadowNum(row.unrealized_pnl_pct)}%</span></td>
    </tr>
  `).join('');
}

function renderShadowComparison(data) {
  const rows = data.rows || [];
  const el = document.getElementById('shadowComparisonBody');
  if (!rows.length) {
    el.innerHTML = '<tr><td colspan="8"><div class="shadow-empty">暂无可对比数据。影子盘或实盘有持仓后会自动展示差异。</div></td></tr>';
    return;
  }
  el.innerHTML = rows.map(row => `
    <tr>
      <td><b>${escapeHtml(row.name || row.code)}</b><br><span class="muted">${escapeHtml(row.code)}</span></td>
      <td>${shadowNum(row.price, 3)}</td>
      <td>${shadowShares(row.shadow_shares)}<br><span class="muted">均价 ${shadowNum(row.shadow_avg_cost, 3)}</span></td>
      <td>${shadowShares(row.real_shares)}<br><span class="muted">均价 ${shadowNum(row.real_avg_cost, 3)}</span></td>
      <td class="${shadowClass(row.share_gap)}">${shadowShares(row.share_gap)}</td>
      <td class="${shadowClass(row.shadow_unrealized_pnl)}">${shadowMoney(row.shadow_unrealized_pnl)}</td>
      <td class="${shadowClass(row.real_unrealized_pnl)}">${shadowMoney(row.real_unrealized_pnl)}</td>
      <td class="${shadowClass(row.pnl_gap)}">${shadowMoney(row.pnl_gap)}</td>
    </tr>
  `).join('');
}

function renderShadowOrders(data) {
  const orders = data.orders || [];
  const el = document.getElementById('shadowOrdersBody');
  if (!orders.length) {
    el.innerHTML = '<tr><td colspan="11"><div class="shadow-empty">暂无影子委托。同步后每一条都能回溯来源报告。</div></td></tr>';
    return;
  }
  el.innerHTML = orders.map(order => {
    const action = order.action === 'sell' ? 'sell' : 'buy';
    const returnPct = order.directional_return_pct;
    return `
      <tr>
        <td>${escapeHtml((order.created_at || '').slice(0, 16))}</td>
        <td><a href="/ai" title="到AI分析台查看报告">#${Number(order.report_id || 0)}</a></td>
        <td><b>${escapeHtml(order.name || order.code)}</b><br><span class="muted">${escapeHtml(order.code)}</span></td>
        <td><span class="shadow-signal ${action}">${actionText(order.action)}</span></td>
        <td>${escapeHtml(signalText(order.signal))}</td>
        <td>${shadowShares(order.shares)}</td>
        <td>${order.fill_price ? shadowNum(order.fill_price, 3) : '<span class="muted">待补价</span>'}</td>
        <td>${order.target_price ? shadowNum(order.target_price, 3) : '<span class="muted">--</span>'}</td>
        <td class="${shadowClass(returnPct)}">${returnPct == null ? '<span class="muted">--</span>' : shadowPct(returnPct)}</td>
        <td>${escapeHtml(order.status === 'filled' ? '已模拟成交' : '待补充价格')}</td>
        <td style="max-width:280px;white-space:normal;color:var(--text-secondary);">${escapeHtml(order.source_reason || '')}</td>
      </tr>
    `;
  }).join('');
}

async function loadShadowPage() {
  try {
    const overview = await performanceFetch(`/overview?${queryFromFilters()}`);
    const shadow = overview.shadow || {};
    const signal = overview.signal || {};
    const summary = shadow.summary || {};
    const positions = shadow.positions || {};
    const comparison = shadow.comparison || {};
    const orders = shadow.orders || {};
    const calibration = shadow.calibration || {};
    const signalStats = signal.stats || {};
    const signalTracking = signal.tracking || [];
    renderFilterOptions(overview.filters || {});
    renderShadowKpis(summary);
    renderShadowCalibration(calibration);
    renderSignalPerformance(signalStats, signalTracking);
    renderModelCalibration(signalStats, calibration);
    renderPositionPlanPerformance(overview.position_plans || {});
    renderDeviation(shadow.deviation || {});
    renderShadowPositions(positions);
    renderShadowComparison(comparison);
    renderShadowOrders(orders);
    setPerformanceTab(performanceState.tab);
  } catch (err) {
    showToast(`AI绩效加载失败：${err.message}`, 'error');
  }
}

async function closeSignalTracking(id, currentPrice) {
  if (!confirm('确认将这条信号跟踪平仓？')) return;
  try {
    await signalFetch(`/tracking/${id}/close`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ exit_price: Number(currentPrice || 0) }),
    });
    showToast('信号跟踪已平仓', 'success');
    await loadShadowPage();
  } catch (err) {
    showToast(`平仓失败：${err.message}`, 'error');
  }
}

async function syncShadowReports() {
  try {
    const data = await shadowFetch('/sync-reports?limit=200', { method: 'POST' });
    showToast(data.message || 'AI报告已同步到影子盘', 'success');
    await loadShadowPage();
  } catch (err) {
    showToast(`同步失败：${err.message}`, 'error');
  }
}

async function markShadowToMarket() {
  try {
    await shadowFetch('/mark-to-market', { method: 'POST' });
    showToast('影子盘估值已刷新', 'success');
    await loadShadowPage();
  } catch (err) {
    showToast(`刷新失败：${err.message}`, 'error');
  }
}

document.addEventListener('DOMContentLoaded', loadShadowPage);
