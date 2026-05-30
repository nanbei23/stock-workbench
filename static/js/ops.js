const OPS_API = '/api';

function opsMoney(value) {
  const n = Number(value || 0);
  if (Math.abs(n) >= 10000) return `${(n / 10000).toFixed(2)}万`;
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 0 });
}

function opsPct(value) {
  const n = Number(value || 0);
  return `${n.toFixed(1)}%`;
}

function opsStatusClass(ok) {
  return ok ? 'ok' : 'warn';
}

function opsBadge(text, ok) {
  return `<span class="ops-badge ${opsStatusClass(ok)}">${escapeHtml(text)}</span>`;
}

function opsRow(title, detail, right = '') {
  return `<div class="ops-row">
    <div><b>${escapeHtml(title)}</b><small>${escapeHtml(detail || '')}</small></div>
    <div>${right}</div>
  </div>`;
}

function opsEmpty(text) {
  return `<div class="ops-empty">${escapeHtml(text)}</div>`;
}

async function opsFetch(url, options = {}) {
  const resp = await fetch(url, options);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || data.error || `HTTP ${resp.status}`);
  return data;
}

async function loadOpsDashboard() {
  const scoreBand = document.getElementById('opsScoreBand');
  scoreBand.innerHTML = `<div class="ops-score-main"><span>运营分</span><strong>...</strong></div>
    ${[1,2,3,4].map(() => '<div class="ops-kpi"><span>加载中</span><strong>--</strong></div>').join('')}`;
  try {
    const data = await opsFetch(`${OPS_API}/operations/dashboard`);
    renderOpsScore(data);
    renderDataTrust(data.data_trust || {});
    renderPortfolio(data.portfolio || {});
    renderRisk(data.risk || {});
    renderQuality(data.ai_quality || {});
    renderReleaseOps(data.release_ops || {});
    renderNotifications(data.notifications || {});
    renderDiagnostics(data.diagnostics || {});
  } catch (e) {
    scoreBand.innerHTML = `<div class="ops-score-main"><span>运营中心</span><strong>--</strong></div>`;
    ['opsDataChecks','opsFreshness','opsPortfolioSummary','opsRiskCenter','opsQualitySummary','opsReleaseOps','opsNotifications','opsDiagnostics']
      .forEach(id => { const el = document.getElementById(id); if (el) el.innerHTML = opsEmpty(`加载失败：${e.message}`); });
  }
}

function renderOpsScore(data) {
  const audit = data.data_trust?.audit || {};
  const risk = data.risk || {};
  const quality = data.ai_quality || {};
  const notifications = data.notifications || {};
  document.getElementById('opsScoreBand').innerHTML = `
    <div class="ops-score-main"><span>运营分</span><strong>${Number(data.score || 0)}</strong><small>${escapeHtml(data.generated_at || '')}</small></div>
    <div class="ops-kpi"><span>数据可信</span><strong class="${audit.ok ? 'up' : 'down'}">${audit.score || 0}</strong><small>${audit.warning_count || 0} 个提示</small></div>
    <div class="ops-kpi"><span>风控状态</span><strong class="${risk.ok ? 'up' : 'down'}">${risk.score || 0}</strong><small>${(risk.warnings || []).length} 个风险</small></div>
    <div class="ops-kpi"><span>AI报告</span><strong>${quality.report_count || 0}</strong><small>核对 ${quality.verified_count || 0} 份</small></div>
    <div class="ops-kpi"><span>通知</span><strong>${notifications.count_24h || 0}</strong><small>最近 24 小时</small></div>
  `;
}

function renderDataTrust(dataTrust) {
  const audit = dataTrust.audit || {};
  const checks = audit.checks || [];
  document.getElementById('opsDataChecks').innerHTML = checks.length
    ? checks.map(item => opsRow(item.label, item.message, opsBadge(item.status === 'ok' ? '正常' : '处理', item.status === 'ok'))).join('')
    : opsEmpty('暂无数据体检结果');

  const freshness = dataTrust.freshness?.items || [];
  document.getElementById('opsFreshness').innerHTML = freshness.length
    ? freshness.map(item => {
      const age = item.age_hours == null ? '未记录' : `${item.age_hours} 小时`;
      return opsRow(item.label, `${item.time || '暂无时间'} · ${age}`, opsBadge(item.status === 'ok' ? '新鲜' : '过期', item.status === 'ok'));
    }).join('')
    : opsEmpty('暂无新鲜度数据');
}

function renderPortfolio(data) {
  const accounts = data.accounts || [];
  const top = data.top_positions || [];
  const buckets = data.buckets || [];
  const accountRows = accounts.slice(0, 4).map(a => `<div class="ops-table-row">
    <span><b>${escapeHtml(a.name || a.id)}</b><small>${escapeHtml(a.broker || a.id || '')}</small></span>
    <span>${opsMoney(a.market_value)}</span>
    <span>${a.position_count || 0}只</span>
    <span>${opsPct(a.weight_pct)}</span>
  </div>`).join('');
  const topRows = top.slice(0, 5).map(p => opsRow(
    `${p.name || p.code} ${p.code}`,
    `市值 ${opsMoney(p.market_value_calc)} · 仓位 ${opsPct(p.weight_pct)}`,
    `<div class="ops-bar" style="width:90px"><span style="width:${Math.min(100, Number(p.weight_pct || 0))}%"></span></div>`
  )).join('');
  const bucketRows = buckets.slice(0, 4).map(b => opsRow(`代码段 ${b.bucket}`, `市值 ${opsMoney(b.market_value)} · 暴露 ${opsPct(b.weight_pct)}`)).join('');
  document.getElementById('opsPortfolioSummary').innerHTML = `
    <div class="ops-two">
      <div><span class="ops-mini-label">账户对比</span><div class="ops-table">${accountRows || opsEmpty('暂无账户资产')}</div></div>
      <div><span class="ops-mini-label">重仓与暴露</span><div class="ops-list">${topRows || opsEmpty('暂无持仓')}${bucketRows}</div></div>
    </div>`;
}

function renderRisk(data) {
  const checks = data.checks || [];
  document.getElementById('opsRiskCenter').innerHTML = checks.length
    ? checks.map(item => {
      const detail = `${item.message || ''} · 阈值 ${item.limit ?? '--'}`;
      return opsRow(item.label, detail, opsBadge(item.status === 'ok' ? '通过' : '预警', item.status === 'ok'));
    }).join('')
    : opsEmpty('暂无风控检查');
}

function renderQuality(data) {
  const after = data.signal_after_return || {};
  const models = data.by_model_mode || [];
  const signals = data.by_signal || [];
  const modelRows = models.slice(0, 3).map(m => opsRow(
    m.model_mode,
    `报告 ${m.reports || 0} · 事实核对 ${m.fact_check_pass_rate || 0}% · 幻觉项 ${m.hallucinations || 0}`
  )).join('');
  const signalRows = signals.slice(0, 4).map(s => opsRow(
    s.signal,
    `跟踪 ${s.tracked || 0} · 胜率 ${s.win_rate || 0}% · 后验收益 ${s.avg_pnl_pct || 0}%`
  )).join('');
  document.getElementById('opsQualitySummary').innerHTML = `
    <div class="ops-two">
      <div class="ops-list">
        ${opsRow('事实核对通过率', `已核对 ${data.verified_count || 0}/${data.report_count || 0} 份`, opsBadge(`${data.fact_check_pass_rate || 0}%`, Number(data.fact_check_pass_rate || 0) >= 80))}
        ${opsRow('信号后验收益', `闭环 ${after.closed || 0}/${after.tracked || 0} 条 · 超额 ${after.avg_excess_return || 0}%`, opsBadge(`${after.win_rate || 0}%`, Number(after.win_rate || 0) >= 50))}
      </div>
      <div class="ops-list">${modelRows || opsEmpty('暂无模型质量样本')}${signalRows}</div>
    </div>`;
}

function renderReleaseOps(data) {
  const migrations = data.migrations || {};
  const db = data.database || {};
  const backup = (data.backups || [])[0];
  document.getElementById('opsReleaseOps').innerHTML = [
    opsRow('数据库版本', `${migrations.latest_applied || 0}/${migrations.latest_known || 0}`, opsBadge(migrations.up_to_date ? '最新' : '待迁移', migrations.up_to_date)),
    opsRow('数据库文件', `${db.path || '未知'} · ${opsMoney(db.size_bytes || 0)}B`),
    opsRow('最近备份', backup ? `${backup.filename} · ${backup.created_at}` : '暂无备份', opsBadge(backup ? '可恢复' : '缺失', !!backup)),
  ].join('');
}

function renderNotifications(data) {
  const byType = data.by_type_24h || {};
  const recent = data.recent || [];
  const channel = Object.entries(data.enabled || {})
    .map(([key, value]) => `${key}:${value ? '开' : '关'}`).join(' · ');
  const rows = recent.slice(0, 4).map(item => opsRow(item.title || item.type, `${item.body || ''} · ${item.time || ''}`)).join('');
  document.getElementById('opsNotifications').innerHTML = [
    opsRow('24小时通知', Object.entries(byType).map(([k, v]) => `${k} ${v}`).join(' · ') || '暂无通知', opsBadge(`${data.count_24h || 0}`, true)),
    opsRow('通道状态', channel || '暂无配置'),
    rows || opsEmpty('暂无最近通知')
  ].join('');
}

function renderDiagnostics(data) {
  const summary = data.summary || {};
  const warnings = data.warnings || [];
  const rows = warnings.slice(0, 5).map(item => opsRow('诊断提示', item, opsBadge('处理', false))).join('');
  document.getElementById('opsDiagnostics').innerHTML = [
    opsRow('系统状态', `任务 ${summary.task_count || 0} · 模型 ${summary.model_provider_count || 0} · 事件 ${summary.event_count || 0}`, opsBadge(summary.warning_count ? '需处理' : '正常', !summary.warning_count)),
    rows || opsEmpty('暂无诊断提示')
  ].join('');
}

async function fixOpsDataHealth() {
  try {
    const data = await opsFetch(`${OPS_API}/data-health/fix`, { method: 'POST' });
    showToast(`已修复：过期单 ${data.expired_orders || 0}，账户引用 ${data.account_refs_fixed || 0}，重算持仓 ${data.portfolio_recalculated || 0}`, 'success');
    await loadOpsDashboard();
  } catch (e) {
    showToast(`修复失败：${e.message}`, 'error');
  }
}

async function createOpsBackup() {
  try {
    const data = await opsFetch(`${OPS_API}/settings/backup/create`, { method: 'POST' });
    showToast(`已创建备份：${data.filename}`, 'success');
    await loadOpsDashboard();
  } catch (e) {
    showToast(`备份失败：${e.message}`, 'error');
  }
}

document.addEventListener('DOMContentLoaded', loadOpsDashboard);
