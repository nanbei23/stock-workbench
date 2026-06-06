async function selfEvolutionFetch(url, options = {}) {
  const resp = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  if (!resp.ok) throw new Error(await resp.text());
  return resp.json();
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value == null || value === '' ? '--' : value;
}

function fmtNum(value, digits = 3) {
  const num = Number(value);
  return Number.isFinite(num) ? num.toFixed(digits).replace(/\.?0+$/, '') : '--';
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

function outcomeClass(outcome) {
  if (outcome === 'loss') return 'loss';
  if (outcome === 'win') return 'win';
  return '';
}

function renderRules(rules) {
  const el = document.getElementById('selfEvolutionRules');
  if (!el) return;
  if (!rules || !rules.length) {
    el.innerHTML = '<div class="muted">暂无进化规则</div>';
    return;
  }
  el.innerHTML = rules.map(rule => {
    const evidence = (rule.evidence || []).map(item => `${item.source_type || '--'}:${item.source_id || '--'} ${item.metric || ''}=${item.value ?? '--'}`).join('；');
    return `<div class="evolution-rule">
      <div><strong>${escapeHtml(rule.scope || 'system')}</strong> ${escapeHtml(rule.rule || '')}</div>
      <small>证据：${escapeHtml(evidence || '--')}</small>
    </div>`;
  }).join('');
}

function renderAttributions(items) {
  const el = document.getElementById('selfEvolutionAttributions');
  if (!el) return;
  if (!items || !items.length) {
    el.innerHTML = '<tr><td colspan="7" class="muted">暂无推荐归因样本</td></tr>';
    return;
  }
  el.innerHTML = items.map(item => `<tr>
    <td>${escapeHtml(item.code || '--')}</td>
    <td>${escapeHtml(item.name || '--')}</td>
    <td class="${outcomeClass(item.outcome)}">${escapeHtml(item.outcome || 'neutral')}</td>
    <td class="${Number(item.realized_pnl) < 0 ? 'loss' : Number(item.realized_pnl) > 0 ? 'win' : ''}">${fmtNum(item.realized_pnl)}</td>
    <td>${fmtNum(item.tracking_pnl_pct)}%</td>
    <td>${escapeHtml((item.source_report_ids || []).join(', ') || '--')}</td>
    <td>交易 ${escapeHtml((item.trade_ids || []).join(', ') || '--')} / 记忆 ${escapeHtml((item.memory_ids || []).join(', ') || '--')}</td>
  </tr>`).join('');
}

function renderSearch(matches) {
  const el = document.getElementById('selfEvolutionSearchResults');
  if (!el) return;
  if (!matches || !matches.length) {
    el.innerHTML = '<tr><td colspan="5" class="muted">未找到相似复盘</td></tr>';
    return;
  }
  el.innerHTML = matches.map(item => `<tr>
    <td>${escapeHtml(item.code || '--')}</td>
    <td>${escapeHtml(item.name || '--')}</td>
    <td>${fmtNum(item.score, 0)}</td>
    <td>${escapeHtml((item.matched_terms || []).join('、') || '--')}</td>
    <td>${escapeHtml(item.summary || '--')}</td>
  </tr>`).join('');
}

async function loadSelfEvolution() {
  const latest = await selfEvolutionFetch('/api/self-evolution/latest');
  const attributions = await selfEvolutionFetch('/api/self-evolution/attributions');
  setText('selfEvolutionScore', fmtNum(latest.system_score));
  setText('selfEvolutionSignals', latest.source_counts?.signal_tracking ?? 0);
  setText('selfEvolutionMemories', latest.source_counts?.active_trade_memories ?? 0);
  setText('selfEvolutionTrades', latest.source_counts?.closed_trades ?? 0);
  setText('selfEvolutionSnapshotId', latest.snapshot_id || '--');
  renderRules(latest.rules || []);
  renderAttributions(attributions.items || []);
}

async function runSelfEvolution() {
  const snapshot = await selfEvolutionFetch('/api/self-evolution/run', { method: 'POST' });
  setText('selfEvolutionSnapshotId', snapshot.snapshot_id || '--');
  await loadSelfEvolution();
}

async function searchSelfEvolutionMemory() {
  const input = document.getElementById('selfEvolutionSearchInput');
  const query = input ? input.value : '';
  const data = await selfEvolutionFetch('/api/self-evolution/semantic-search', {
    method: 'POST',
    body: JSON.stringify({ query, limit: 10 }),
  });
  renderSearch(data.matches || []);
}

document.addEventListener('DOMContentLoaded', () => {
  loadSelfEvolution().then(searchSelfEvolutionMemory).catch(err => {
    console.error('self evolution load failed', err);
    setText('selfEvolutionScore', '读取失败');
  });
});
