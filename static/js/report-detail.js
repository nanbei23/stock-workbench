(function () {
  'use strict';

  const SIG_LABEL = {
    STRONG_BUY: '强烈买入',
    BUY: '买入',
    OVERWEIGHT: '增持',
    HOLD: '持有',
    UNDERWEIGHT: '减持',
    SELL: '卖出',
    STRONG_SELL: '强烈卖出',
  };

  const layerFields = [
    ['market_report', '市场 / 技术分析', '价格、趋势、量能和技术结构'],
    ['sentiment_report', '事件 / 情绪分析', '情绪、热度和市场关注度'],
    ['news_report', '新闻舆情', '新闻、公告和外部信息'],
    ['fundamentals_report', '基本面分析', '财务、业务和估值质量'],
    ['policy_report', '政策分析', '政策驱动、监管和行业约束'],
    ['hot_money_report', '资金分析', '游资、资金流和交易活跃度'],
    ['lockup_report', '解禁监控', '限售解禁、减持和供给压力'],
  ];

  document.addEventListener('DOMContentLoaded', loadReportDetail);

  function reportId() {
    return Number(document.querySelector('.report-detail-page')?.dataset.reportId || 0);
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

  function parseJsonish(value) {
    if (!value || typeof value !== 'string') return value;
    let raw = value.trim();
    if (!raw) return '';
    raw = raw.replace(/^```(?:json)?/i, '').replace(/```$/i, '').trim();
    try {
      const parsed = JSON.parse(raw);
      if (typeof parsed === 'string' && parsed.trim() !== raw && /^[\[{]/.test(parsed.trim())) {
        return parseJsonish(parsed);
      }
      return parsed;
    } catch (_err) {
      try {
        return JSON.parse(raw.replace(/\\n/g, '\n'));
      } catch (_err2) {
        return value;
      }
    }
  }

  function humanizeKey(key) {
    const label = {
      signal: '信号',
      trader_plan: '交易计划',
      bull_case: '多头观点',
      bear_case: '空头观点',
      risk_controls: '风险控制',
      final_decision: '最终决策',
      reason: '理由',
      action: '动作',
      confidence: '置信度',
      risk_score: '风险评分',
    }[key];
    if (label) return label;
    return String(key || '').replace(/_/g, ' ').replace(/\b\w/g, ch => ch.toUpperCase());
  }

  function markdown(value) {
    const parsed = typeof value === 'string' ? parseJsonish(value) : value;
    if (parsed && typeof parsed === 'object') return structuredValue(parsed);
    const raw = String(parsed || '').replace(/\\n/g, '\n').trim();
    if (!raw) return '<div class="library-empty-state">暂无内容</div>';
    if (window.marked?.parse) return window.marked.parse(raw);
    return `<pre>${escapeHtml(raw)}</pre>`;
  }

  function structuredValue(value, depth = 0) {
    const parsed = typeof value === 'string' ? parseJsonish(value) : value;
    if (parsed == null || parsed === '') return '<span class="structured-empty">暂无</span>';
    if (depth > 6) return `<span>${escapeHtml(JSON.stringify(parsed))}</span>`;
    if (Array.isArray(parsed)) {
      if (!parsed.length) return '<span class="structured-empty">暂无</span>';
      return `<ul class="structured-list">${parsed.map(item => `<li>${structuredValue(item, depth + 1)}</li>`).join('')}</ul>`;
    }
    if (typeof parsed === 'object') {
      const entries = Object.entries(parsed).filter(([, val]) => val !== undefined && val !== null && val !== '');
      if (!entries.length) return '<span class="structured-empty">暂无</span>';
      return `<div class="structured-report ${depth ? 'nested' : ''}">${entries.map(([key, val]) => `
        <div class="structured-row">
          <div class="structured-key">${escapeHtml(humanizeKey(key))}</div>
          <div class="structured-value">${structuredValue(val, depth + 1)}</div>
        </div>
      `).join('')}</div>`;
    }
    const raw = String(parsed).replace(/\\n/g, '\n');
    if (window.marked?.parse && /[\n#>*`-]|\*\*/.test(raw)) return window.marked.parse(raw);
    return `<span>${escapeHtml(raw)}</span>`;
  }

  function renderFinalDecision(report) {
    const source = report.final_decision || report.result?.final_decision || report.result?.reasoning || report.result || '';
    const parsed = typeof source === 'string' ? parseJsonish(source) : source;
    const result = report.result && typeof report.result === 'object' ? report.result : {};
    const signal = parsed?.signal || report.signal || result.signal || '';
    const confidence = parsed?.confidence ?? report.confidence ?? result.confidence;
    const riskScore = parsed?.risk_score ?? report.risk_score ?? result.risk_score;
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      const finalText = parsed.final_decision || parsed.reasoning || parsed.summary || parsed.reason || '';
      const planText = parsed.trader_plan || parsed.trade_plan || parsed.plan || '';
      const rest = { ...parsed };
      ['signal', 'confidence', 'risk_score', 'final_decision', 'reasoning', 'summary', 'reason', 'trader_plan', 'trade_plan', 'plan'].forEach(key => delete rest[key]);
      return `
        <div class="decision-summary-grid">
          <div><span>信号</span><strong>${escapeHtml(SIG_LABEL[signal] || signal || '--')}</strong></div>
          <div><span>置信度</span><strong>${escapeHtml(formatPct(confidence))}</strong></div>
          <div><span>风险评分</span><strong>${escapeHtml(formatScore(riskScore))}</strong></div>
        </div>
        ${finalText ? `<h3>裁决结论</h3><div>${markdown(finalText)}</div>` : ''}
        ${planText ? `<h3>交易计划</h3><div>${markdown(planText)}</div>` : ''}
        ${Object.keys(rest).length ? `<h3>结构化字段</h3>${structuredValue(rest)}` : ''}
      `;
    }
    return `
      <div class="decision-summary-grid">
        <div><span>信号</span><strong>${escapeHtml(SIG_LABEL[signal] || signal || '--')}</strong></div>
        <div><span>置信度</span><strong>${escapeHtml(formatPct(confidence))}</strong></div>
        <div><span>风险评分</span><strong>${escapeHtml(formatScore(riskScore))}</strong></div>
      </div>
      ${markdown(source || '暂无最终决策')}
    `;
  }

  function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  }

  function setHtml(id, value) {
    const el = document.getElementById(id);
    if (el) el.innerHTML = value;
  }

  function formatPct(value) {
    if (value == null || value === '') return '--';
    const n = Number(value);
    if (!Number.isFinite(n)) return '--';
    return `${(n > 1 ? n : n * 100).toFixed(1)}%`;
  }

  function formatScore(value) {
    if (value == null || value === '') return '--';
    const n = Number(value);
    if (!Number.isFinite(n)) return '--';
    return (n <= 1 ? n * 100 : n).toFixed(1);
  }

  function formatPrice(value) {
    if (value == null || value === '') return '--';
    const n = Number(value);
    if (!Number.isFinite(n)) return '--';
    return n.toFixed(3);
  }

  function formatTime(value) {
    if (!value) return '--';
    const d = new Date(String(value).replace(' ', 'T'));
    if (Number.isNaN(d.getTime())) return String(value);
    return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  }

  function renderMeta(report) {
    const signal = report.signal || report.result?.signal || 'HOLD';
    const targetPrice = report.target_price || report.result?.target_price;
    const rows = [
      [SIG_LABEL[signal] || signal, '信号'],
      [formatPct(report.confidence || report.result?.confidence), '置信度'],
      [formatScore(report.risk_score || report.result?.risk_score), '风险'],
      [formatPrice(targetPrice), '目标价'],
      [`${report.depth || 'standard'} / ${report.model_mode || 'balanced'}`, '分析模式'],
      [formatTime(report.created_at), '生成时间'],
    ];
    setHtml('reportMetaGrid', rows.map(([strong, label]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(strong)}</strong></div>`).join(''));
    setText('reportDecisionMeta', `报告 #${Number(report.id || reportId())}`);
  }

  function renderLayers(report) {
    const cards = layerFields.map(([field, title, hint]) => {
      const content = report[field];
      const hasContent = content && String(content).trim?.() !== '';
      return `<article class="position-plan-role-card report-layer-card">
        <header>
          <strong>${escapeHtml(title)}</strong>
          <span>${hasContent ? escapeHtml(hint) : '暂无'}</span>
        </header>
        <div class="position-plan-prose compact">${markdown(content || '')}</div>
      </article>`;
    }).join('');
    const complete = layerFields.filter(([field]) => report[field]).length;
    setText('reportLayerMeta', `${complete} / ${layerFields.length} 层有内容`);
    setHtml('reportLayerList', cards);
  }

  function renderVerification(report) {
    const fact = report._fact_check || report.fact_check || {};
    const bystander = report._bystander_verify || report.bystander_verify || {};
    setHtml('reportFactCheckBody', markdown(fact));
    setHtml('reportBystanderBody', markdown(bystander));
  }

  async function runReportAction(id, endpoint, label) {
    const btn = endpoint.includes('recheck')
      ? document.getElementById('reportFactCheckBtn')
      : document.getElementById('reportBystanderBtn');
    const oldText = btn?.textContent || label;
    if (btn) {
      btn.disabled = true;
      btn.textContent = `${label}中...`;
    }
    try {
      await requestJson(endpoint, { method: 'POST' });
      await loadReportDetail();
    } catch (err) {
      alert(`${label}失败：${err.message}`);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = oldText;
      }
    }
  }

  async function loadReportDetail() {
    const id = reportId();
    if (!id) return;
    try {
      const report = await requestJson(`/api/ai/reports/${encodeURIComponent(id)}`);
      const title = `${report.name || report.code || 'AI报告'} ${report.code || ''}`.trim();
      document.title = `${title} - AI报告详情`;
      setText('reportDetailTitle', title);
      setText('reportDetailSubtitle', `报告 #${Number(report.id || id)} · ${formatTime(report.created_at)} · ${report.task_id || '单次报告'}`);
      document.getElementById('reportAiLink')?.setAttribute('href', `/ai?report_id=${Number(id)}`);
      document.getElementById('reportPdfLink')?.setAttribute('href', `/api/ai/report/${Number(id)}/pdf`);
      const factBtn = document.getElementById('reportFactCheckBtn');
      if (factBtn) factBtn.onclick = () => runReportAction(id, `/api/ai/reports/${Number(id)}/recheck`, '事实核查');
      const bystanderBtn = document.getElementById('reportBystanderBtn');
      if (bystanderBtn) bystanderBtn.onclick = () => runReportAction(id, `/api/ai/reports/${Number(id)}/bystander-verify`, '旁观者核对');
      renderMeta(report);
      setHtml('reportDecisionBody', renderFinalDecision(report));
      setHtml('reportTradePlanBody', markdown(report.trader_plan || '暂无交易计划'));
      setHtml('reportInvestmentDebateBody', markdown(report.investment_debate || '暂无多空辩论'));
      setHtml('reportRiskBody', markdown(report.risk_debate || '暂无风险复核'));
      renderLayers(report);
      renderVerification(report);
      setText('reportRawStateBody', JSON.stringify(report.raw_state || report.state || {}, null, 2));
    } catch (err) {
      const message = `加载失败：${escapeHtml(err.message)}`;
      setText('reportDetailTitle', 'AI报告加载失败');
      setText('reportDetailSubtitle', err.message);
      setHtml('reportDecisionBody', `<div class="library-empty-state">${message}</div>`);
      setHtml('reportTradePlanBody', `<div class="library-empty-state">${message}</div>`);
      setHtml('reportInvestmentDebateBody', `<div class="library-empty-state">${message}</div>`);
      setHtml('reportRiskBody', `<div class="library-empty-state">${message}</div>`);
      setHtml('reportLayerList', `<div class="library-empty-state">${message}</div>`);
      setText('reportLayerMeta', '--');
      setHtml('reportFactCheckBody', '--');
      setHtml('reportBystanderBody', '--');
      setText('reportRawStateBody', '{}');
    }
  }
})();
