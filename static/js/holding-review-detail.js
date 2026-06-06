(function () {
  'use strict';

  const root = document.querySelector('.report-detail-page');
  const reviewId = root?.dataset.reviewId || '';

  document.addEventListener('DOMContentLoaded', loadHoldingReviewDetail);

  async function requestJson(url, options) {
    const resp = await fetch(url, options || {});
    const text = await resp.text();
    const data = text ? JSON.parse(text) : {};
    if (!resp.ok) throw new Error(data.detail || data.message || resp.statusText);
    return data;
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  }

  function formatNum(value, digits = 3) {
    const num = Number(value || 0);
    return Number.isFinite(num) ? num.toFixed(digits) : '--';
  }

  function signalLabel(value) {
    return {
      STRONG_BUY: '强烈买入',
      BUY: '买入',
      OVERWEIGHT: '增持',
      HOLD: '持有',
      UNDERWEIGHT: '减持',
      SELL: '卖出',
      STRONG_SELL: '强烈卖出',
    }[value] || value || '--';
  }

  function actionLabel(value) {
    return {
      hold: '持有观察',
      review: '需要复核',
      reduce: '考虑减仓',
      take_profit: '止盈观察',
      candidate: '候选观察',
      wait: '等待',
    }[value] || value || '--';
  }

  function dailyDecisionActionLabel(value) {
    return {
      hold: '持有',
      reduce: '减仓',
      sell: '卖出',
      add: '加仓',
      forbid_buy: '禁止买入',
      watch: '观察',
      review: '复核',
      take_profit: '止盈观察',
      candidate: '候选观察',
      wait: '等待',
    }[String(value || '').toLowerCase()] || actionLabel(value);
  }

  function decisionStatusLabel(value) {
    return {
      executed: '已执行',
      not_executed: '未执行',
      ignored: '忽略',
      watching: '观察中',
    }[String(value || '').toLowerCase()] || value || '未执行';
  }

  function decisionStatusClass(value) {
    const clean = String(value || 'not_executed').toLowerCase();
    if (clean === 'executed') return 'signal-buy';
    if (clean === 'ignored') return 'signal-sell';
    return 'signal-hold';
  }

  function decisionActions(item) {
    const id = Number(item.id || 0);
    if (!id) return '<span class="muted">--</span>';
    return `<div class="inline-action-group">
      <button class="btn btn-xs" onclick="updateDailyDecisionItemStatus(${id}, 'executed')">已执行</button>
      <button class="btn btn-xs" onclick="updateDailyDecisionItemStatus(${id}, 'watching')">观察中</button>
      <button class="btn btn-xs" onclick="updateDailyDecisionItemStatus(${id}, 'ignored')">忽略</button>
    </div>`;
  }

  function executionClassLabel(value) {
    return {
      full_executed: '完全执行',
      partial_executed: '部分执行',
      over_executed: '超额执行',
      not_executed: '未执行',
      reverse_executed: '反向执行',
      mixed_execution: '混合执行',
      complied_no_trade: '遵守不交易',
      discretionary_trade: '自主交易',
      violated: '违反建议',
    }[String(value || '').toLowerCase()] || value || '--';
  }

  function renderExecutionCell(execution) {
    if (!execution) return '<span class="muted">--</span>';
    const ids = (execution.matched_trade_ids || []).map(id => `#${Number(id)}`).join(' ');
    return `${escapeHtml(execution.label || executionClassLabel(execution.classification))}<br><span class="muted">${formatNum(execution.matched_amount)} ${escapeHtml(ids)}</span>`;
  }

  function renderExecutionDelta(execution) {
    if (!execution) return '<span class="muted">--</span>';
    const value = Number(execution.deviation_amount || 0);
    return `<span class="${priceClass(value)}">${formatNum(value)}</span>`;
  }

  function severityLabel(value) {
    return { critical: '高风险', warning: '提醒', info: '信息' }[value] || value || '--';
  }

  function priceClass(value) {
    const num = Number(value || 0);
    if (num > 0) return 'up';
    if (num < 0) return 'down';
    return 'flat';
  }

  function renderMarkdown(text) {
    const raw = String(text || '').replace(/\\n/g, '\n');
    if (window.marked) return window.marked.parse(raw);
    return `<pre>${escapeHtml(raw)}</pre>`;
  }

  async function loadHoldingReviewDetail() {
    if (!reviewId) return;
    const title = document.getElementById('holdingReviewTitle');
    const meta = document.getElementById('holdingReviewMeta');
    const markdownLink = document.getElementById('holdingReviewMarkdownLink');
    try {
      const [review, items, flags] = await Promise.all([
        requestJson(`/api/daily-decision-reports/${encodeURIComponent(reviewId)}`),
        requestJson(`/api/daily-decision-reports/${encodeURIComponent(reviewId)}/items`),
        requestJson(`/api/daily-decision-reports/${encodeURIComponent(reviewId)}/flags`),
      ]);
      const execution = await loadDailyDecisionExecution(reviewId);
      const planTitle = review.tomorrow_plan?.title || '每日 AI 决策报告';
      if (title) title.textContent = `${planTitle} ${review.date || ''}`;
      if (meta) {
        meta.textContent = `${review.status || '--'} · 持仓 ${review.holding_count || 0} 只 · 候选 ${review.candidate_count || 0} 只 · 触发 ${review.trigger_count || 0} 项`;
      }
      if (markdownLink) markdownLink.href = `/api/daily-decision-reports/${encodeURIComponent(reviewId)}/markdown`;
      renderAsset(review.asset_snapshot || {});
      renderItems(items.items || [], execution);
      renderFlags(flags.flags || []);
      renderRoles(review.tomorrow_plan?.role_discussion || []);
      renderBattlePlan(review.tomorrow_plan?.battle_plan || {});
      document.getElementById('holdingReviewMarkdown').innerHTML = renderMarkdown(review.tomorrow_plan_markdown || '');
    } catch (err) {
      if (meta) meta.textContent = `加载失败：${err.message}`;
    }
  }

  function renderAsset(asset) {
    const el = document.getElementById('holdingReviewAsset');
    if (!el) return;
    el.innerHTML = `
      <div><span>总资产</span><strong>${formatNum(asset.total_assets)}</strong></div>
      <div><span>可用资金</span><strong>${formatNum(asset.cash)}</strong></div>
      <div><span>持仓市值</span><strong>${formatNum(asset.market_value)}</strong></div>
      <div><span>仓位使用率</span><strong>${formatNum(asset.position_usage_pct)}%</strong></div>
      <div><span>现金占比</span><strong>${formatNum(asset.cash_pct)}%</strong></div>
      <div><span>持仓盈亏</span><strong class="${priceClass(asset.holding_pnl)}">${formatNum(asset.holding_pnl)} (${formatNum(asset.holding_pnl_pct)}%)</strong></div>
    `;
  }

  async function loadDailyDecisionExecution(id) {
    try {
      const data = await requestJson(`/api/performance/suggestion-execution?source=daily&source_id=${encodeURIComponent(id)}&limit=500`);
      const map = new Map();
      (data.rows || []).forEach(row => map.set(Number(row.item_id), row.execution || {}));
      return map;
    } catch (_err) {
      return new Map();
    }
  }

  function renderItems(items, executionByItem = new Map()) {
    const holdings = items.filter(item => item.item_type === 'holding');
    const candidates = items.filter(item => item.item_type === 'candidate');
    const holdingBody = document.getElementById('holdingReviewHoldings');
    const candidateBody = document.getElementById('holdingReviewCandidates');
    if (holdingBody) {
      holdingBody.innerHTML = holdings.length ? holdings.map(item => `<tr>
        <td><strong>${escapeHtml(item.name || item.code)}</strong><span>${escapeHtml(item.code)}</span></td>
        <td>${formatNum(item.shares)}</td>
        <td>${formatNum(item.avg_cost)} / ${formatNum(item.price)}</td>
        <td>${formatNum(item.position_pct)}%</td>
        <td class="${priceClass(item.change_pct)}">${formatNum(item.change_pct)}%</td>
        <td class="${priceClass(item.holding_pnl)}">${formatNum(item.holding_pnl)} (${formatNum(item.holding_pnl_pct)}%)</td>
        <td>${escapeHtml(signalLabel(item.latest_signal))}</td>
        <td>${escapeHtml(dailyDecisionActionLabel(item.decision_action || item.action_hint))}</td>
        <td><span class="report-signal ${decisionStatusClass(item.decision_status)}">${escapeHtml(decisionStatusLabel(item.decision_status))}</span></td>
        <td>${renderExecutionCell(executionByItem.get(Number(item.id)))}</td>
        <td>${renderExecutionDelta(executionByItem.get(Number(item.id)))}</td>
        <td>${decisionActions(item)}</td>
      </tr>`).join('') : '<tr><td colspan="12" class="empty-row">暂无持仓</td></tr>';
    }
    if (candidateBody) {
      candidateBody.innerHTML = candidates.length ? candidates.map(item => `<tr>
        <td><strong>${escapeHtml(item.name || item.code)}</strong><span>${escapeHtml(item.code)}</span></td>
        <td>${escapeHtml(item.source_group || '--')}</td>
        <td>${formatNum(item.price)}</td>
        <td class="${priceClass(item.change_pct)}">${formatNum(item.change_pct)}%</td>
        <td>${escapeHtml(signalLabel(item.latest_signal))}</td>
        <td>${escapeHtml(dailyDecisionActionLabel(item.decision_action || item.action_hint))}</td>
        <td><span class="report-signal ${decisionStatusClass(item.decision_status)}">${escapeHtml(decisionStatusLabel(item.decision_status))}</span></td>
        <td>${renderExecutionCell(executionByItem.get(Number(item.id)))}</td>
        <td>${renderExecutionDelta(executionByItem.get(Number(item.id)))}</td>
        <td>${decisionActions(item)}</td>
        <td>${escapeHtml(item.reason || '')}</td>
      </tr>`).join('') : '<tr><td colspan="11" class="empty-row">未加入候选池</td></tr>';
    }
  }

  async function updateDailyDecisionItemStatus(itemId, status) {
    if (!reviewId || !itemId) return;
    try {
      await requestJson(`/api/daily-decision-reports/${encodeURIComponent(reviewId)}/items/${Number(itemId)}/status`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      });
      await loadHoldingReviewDetail();
    } catch (err) {
      alert(`更新每日决策状态失败：${err.message}`);
    }
  }

  function renderFlags(flags) {
    const el = document.getElementById('holdingReviewFlags');
    if (!el) return;
    if (!flags.length) {
      el.innerHTML = '<div class="empty-row">暂无异常触发项。</div>';
      return;
    }
    el.innerHTML = flags.map(flag => `<div class="anomaly-modal-item">
      <div class="anomaly-modal-item-row">
        <strong>${escapeHtml(flag.name || flag.code)} ${escapeHtml(flag.code)}</strong>
        <span class="report-signal ${flag.severity === 'critical' ? 'signal-sell' : flag.severity === 'warning' ? 'signal-hold' : 'signal-buy'}">${escapeHtml(severityLabel(flag.severity))}</span>
      </div>
      <div class="anomaly-modal-item-msg">${escapeHtml(flag.description || '')}</div>
      <div class="anomaly-modal-item-advice">${flag.requires_full_report ? '建议补跑完整单股报告' : '无需补跑完整报告'}</div>
    </div>`).join('');
  }

  function renderRoles(roles) {
    const el = document.getElementById('holdingReviewRoles');
    if (!el) return;
    if (!roles.length) {
      el.innerHTML = '<div class="empty-row">暂无三角色讨论。</div>';
      return;
    }
    el.innerHTML = roles.map(role => {
      const actions = Array.isArray(role.action_items) ? role.action_items : [];
      return `<div class="holding-review-role-card">
        <div class="holding-review-role-head">
          <strong>${escapeHtml(role.role || '--')}</strong>
          <span>${escapeHtml(role.stance || '--')}</span>
        </div>
        <p>${escapeHtml(role.view || '')}</p>
        ${actions.length ? `<ul>${actions.map(action => `<li>${escapeHtml(action)}</li>`).join('')}</ul>` : ''}
      </div>`;
    }).join('');
  }

  function renderBattleItems(items, detailKey) {
    if (!Array.isArray(items) || !items.length) return '<div class="empty-row">无</div>';
    return items.map(item => `<div class="holding-review-battle-item">
      <strong>${escapeHtml(item.name || item.code || '--')} <span>${escapeHtml(item.code || '')}</span></strong>
      <p>${escapeHtml(item[detailKey] || item.reason || item.action_label || item.condition || '--')}</p>
    </div>`).join('');
  }

  function renderBattlePlan(plan) {
    const el = document.getElementById('holdingReviewBattlePlan');
    if (!el) return;
    if (!plan || Object.keys(plan).length === 0) {
      el.innerHTML = '<div class="empty-row">暂无决策清单。</div>';
      return;
    }
    const sections = [
      ['持仓管理建议', plan.holding_management, 'action_label'],
      ['次日进攻候选', plan.offensive_candidates, 'condition'],
      ['禁止操作清单', plan.do_not_touch, 'reason'],
      ['触发条件', plan.trigger_conditions, 'condition'],
    ];
    el.innerHTML = sections.map(([heading, items, detailKey]) => `<div class="holding-review-battle-card">
      <h3>${escapeHtml(heading)}</h3>
      ${renderBattleItems(items, detailKey)}
    </div>`).join('');
  }

  window.loadHoldingReviewDetail = loadHoldingReviewDetail;
  window.updateDailyDecisionItemStatus = updateDailyDecisionItemStatus;
})();
