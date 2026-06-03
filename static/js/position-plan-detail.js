(function () {
  'use strict';

  const stageLabels = {
    screening: '初筛',
    shortlist: '精选',
    final: '最终建仓',
  };
  const adoptionLabels = {
    draft: '待确认',
    adopted: '已采纳',
    superseded: '已被替代',
    archived: '已归档',
  };
  const strategyLabels = {
    auto: '自动',
    full_text: '完整原文',
    summary_plus_evidence: '摘要 + 证据',
    candidate_screening: '候选筛选',
    single: '单模型',
    dual: '双模型',
    per_role: '按角色配置',
  };

  document.addEventListener('DOMContentLoaded', loadPositionPlanDetail);

  function planId() {
    return document.querySelector('.position-plan-page')?.dataset.planId || '';
  }

  async function requestJson(url, options) {
    const resp = await fetch(url, options || {});
    const text = await resp.text();
    const data = text ? JSON.parse(text) : {};
    if (!resp.ok) throw new Error(data.detail || data.message || resp.statusText);
    return data;
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[ch]));
  }

  function asNumber(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n : 0;
  }

  function money(value) {
    return asNumber(value).toLocaleString('zh-CN', {
      minimumFractionDigits: 3,
      maximumFractionDigits: 3,
    });
  }

  function pct(value) {
    return `${asNumber(value).toFixed(3)}%`;
  }

  function time(value) {
    if (!value) return '--';
    const date = new Date(String(value).replace(' ', 'T'));
    if (Number.isNaN(date.getTime())) return String(value);
    return `${date.getMonth() + 1}/${date.getDate()} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
  }

  function markdown(value) {
    const text = String(value || '').trim();
    if (!text) return '<div class="library-empty-state">暂无内容</div>';
    if (window.marked?.parse) return window.marked.parse(text);
    return `<pre>${escapeHtml(text)}</pre>`;
  }

  function jsonBlock(value) {
    return escapeHtml(JSON.stringify(value || {}, null, 2));
  }

  function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  }

  function setHtml(id, value) {
    const el = document.getElementById(id);
    if (el) el.innerHTML = value;
  }

  function renderMeta(plan) {
    const cash = plan.cash_snapshot_json || {};
    const portfolio = plan.portfolio_snapshot_json || {};
    const market = plan.decision_market_snapshot_json || {};
    const values = [
      [stageLabels[plan.stage] || plan.stage || '--', '阶段'],
      [adoptionLabels[plan.adoption_status] || plan.adoption_status || '--', '采纳状态'],
      [`${Number(plan.candidate_count || 0)} / ${Number(plan.selected_count || 0)}`, '候选 / 入选'],
      [`${money(cash.total_cash || 0)} 元`, '现金快照'],
      [`${money(portfolio.market_value || 0)} 元`, '持仓市值'],
      [market.status ? `${market.status} ${time(plan.market_context_captured_at || market.captured_at)}` : '未校准', '行情校准'],
    ];
    setHtml('planMetaGrid', values.map(([strong, span]) => `<div><span>${escapeHtml(span)}</span><strong>${escapeHtml(strong)}</strong></div>`).join(''));
  }

  function renderItems(plan) {
    const items = plan.items || [];
    setText('planItemsMeta', `${items.length} 条建议`);
    const rows = items.map(item => {
      const sourceId = item.source_report_id || item.report_id || '';
      return `<tr>
        <td><strong>${escapeHtml(item.name || item.code)}</strong><span>${escapeHtml(item.code)}</span></td>
        <td>${escapeHtml(item.action || '--')}</td>
        <td>${money(item.suggested_amount)}</td>
        <td>${pct(item.position_pct)}</td>
        <td>${money(item.suggested_shares)}</td>
        <td>${item.confidence == null ? '--' : asNumber(item.confidence).toFixed(3)}</td>
        <td>${item.risk_score == null ? '--' : asNumber(item.risk_score).toFixed(3)}</td>
        <td><span class="plan-item-reason">${escapeHtml(item.reason || item.entry_plan || item.risk_note || '--')}</span></td>
        <td>${sourceId ? `<a class="text-link" href="/ai?report_id=${Number(sourceId)}">报告 ${Number(sourceId)}</a>` : '--'}</td>
      </tr>`;
    }).join('');
    setHtml('planItemRows', rows || '<tr><td colspan="9" class="library-empty-state">暂无建议明细</td></tr>');
  }

  function roleName(item, index) {
    return item.role_name || item.name || item.role || item.role_key || `角色 ${index + 1}`;
  }

  function renderRoles(plan) {
    const roles = plan.role_discussion_json || [];
    setText('planRoleMeta', `${roles.length} 个角色`);
    const html = roles.map((item, index) => {
      const content = item.content || item.output || item.text || '';
      return `<article class="position-plan-role-card">
        <header>
          <strong>${escapeHtml(roleName(item, index))}</strong>
          <span>${escapeHtml(item.model || item.provider || item.role_key || '')}</span>
        </header>
        <div class="position-plan-prose compact">${markdown(content)}</div>
      </article>`;
    }).join('');
    setHtml('planRoleDiscussion', html || '<div class="library-empty-state">暂无角色讨论全文</div>');
  }

  function renderSources(plan) {
    const ids = plan.source_report_ids || [];
    setText('planSourceMeta', `${ids.length} 份来源报告`);
    setHtml('planSourceReports', ids.length
      ? ids.map(id => `<a class="source-report-chip" href="/ai?report_id=${Number(id)}">报告 ${Number(id)}</a>`).join('')
      : '<div class="library-empty-state">暂无来源报告记录</div>');

    const market = plan.decision_market_snapshot_json || {};
    const rows = (market.summary || []).slice(0, 40).map(item => {
      const day = item.day || {};
      return `<div class="source-report-row">
        <strong>${escapeHtml(item.name || item.code)} <span>${escapeHtml(item.code || '')}</span></strong>
        <span>${money(item.price)} · ${pct(item.change_pct)} · 5日 ${pct(day.return_5d_pct)} · 20日 ${pct(day.return_20d_pct)}</span>
      </div>`;
    }).join('');
    setHtml('planMarketSnapshot', rows || '<div class="library-empty-state">暂无行情校准快照</div>');
  }

  function renderAudit(plan) {
    setText('planModelLabel', `${strategyLabels[plan.context_strategy] || plan.context_strategy || '--'} / ${strategyLabels[plan.model_strategy] || plan.model_strategy || '--'}`);
    setHtml('planRiskControls', markdown(Array.isArray(plan.risk_controls_json) ? plan.risk_controls_json.map(item => `- ${typeof item === 'string' ? item : JSON.stringify(item)}`).join('\n') : plan.risk_controls_json));
    setText('planModelConfig', JSON.stringify(plan.model_config_json || {}, null, 2));
    setText('planCashSnapshot', JSON.stringify(plan.cash_snapshot_json || {}, null, 2));
    setText('planPortfolioSnapshot', JSON.stringify(plan.portfolio_snapshot_json || {}, null, 2));
    setText('planConfirmedSnapshot', JSON.stringify(plan.confirmed_snapshot_json || {}, null, 2));
    setHtml('planMarkdownBody', markdown(plan.output_markdown || ''));
  }

  async function loadPositionPlanDetail() {
    const id = planId();
    if (!id) return;
    try {
      const plan = await requestJson(`/api/position-plans/${encodeURIComponent(id)}`);
      document.title = `${plan.title || plan.plan_id} - 建仓计划详情`;
      setText('planDetailTitle', plan.title || plan.plan_id);
      setText('planDetailSubtitle', `${plan.plan_id} · ${stageLabels[plan.stage] || plan.stage || '--'} · ${time(plan.created_at)} · 任务 ${plan.batch_job_id || '--'}`);
      document.getElementById('planMarkdownLink')?.setAttribute('href', `/api/position-plans/${encodeURIComponent(id)}/markdown`);
      const adoptBtn = document.getElementById('planAdoptBtn');
      if (adoptBtn) {
        adoptBtn.style.display = plan.stage === 'final' && plan.adoption_status !== 'adopted' ? '' : 'none';
        adoptBtn.onclick = () => adoptPlan(id);
      }
      const archiveBtn = document.getElementById('planArchiveBtn');
      if (archiveBtn) archiveBtn.onclick = () => archivePlan(id);
      renderMeta(plan);
      renderItems(plan);
      setHtml('planSummaryBody', markdown(plan.summary || '暂无摘要'));
      renderRoles(plan);
      renderSources(plan);
      renderAudit(plan);
    } catch (err) {
      setText('planDetailTitle', '建仓计划加载失败');
      setHtml('planDetailSubtitle', escapeHtml(err.message));
      setHtml('planItemRows', `<tr><td colspan="9" class="library-empty-state">加载失败：${escapeHtml(err.message)}</td></tr>`);
    }
  }

  async function adoptPlan(id) {
    if (!confirm('确认采纳这份最终建仓计划作为 AI 绩效基准？这不会自动写交易或下单。')) return;
    await requestJson(`/api/position-plans/${encodeURIComponent(id)}/adopt`, { method: 'POST' });
    await loadPositionPlanDetail();
  }

  async function archivePlan(id) {
    if (!confirm('确认归档这份建仓计划？')) return;
    await requestJson(`/api/position-plans/${encodeURIComponent(id)}/archive`, { method: 'POST' });
    await loadPositionPlanDetail();
  }
})();
