let selectedHotspotName = '';
let cachedHotspots = [];

function hotspotEmpty(text) {
  return `<div class="hotspots-empty">${escapeHtml(text)}</div>`;
}

function directionClass(value) {
  if (value === 'up') return 'up';
  if (value === 'down') return 'down';
  return 'flat';
}

function renderRegime(data) {
  const card = document.getElementById('regimeCard');
  const notes = document.getElementById('regimeNotes');
  const time = document.getElementById('regimeTime');
  if (!card || !notes) return;
  const guidance = data.position_guidance || {};
  const source = data.source_summary || {};
  card.innerHTML = `<div class="regime-score">
    <strong class="${data.regime === 'risk_off' ? 'down' : data.regime === 'risk_on' ? 'up' : ''}">${Number(data.score || 0)}</strong>
    <div>
      <span>Market Regime</span>
      <b>${escapeHtml(data.label || '--')}</b>
      <p>${escapeHtml(data.action_bias || '')}</p>
      <p>现金 ${Number(guidance.cash_pct || 0).toFixed(1)}% · 持仓 ${guidance.position_count || 0} 只 · 风控 ${Number(guidance.risk_score || 0).toFixed(0)} · 可信度 ${source.reliability || '--'}</p>
    </div>
  </div>`;
  const rows = data.notes || [];
  notes.innerHTML = rows.length
    ? rows.map(item => `<div class="regime-note">${escapeHtml(item)}</div>`).join('')
    : '<div class="regime-note">当前没有明显风险提示，保持既定仓位纪律。</div>';
  if (time) time.textContent = data.generated_at || '';
}

function renderPulse(data) {
  const list = document.getElementById('pulseList');
  const active = document.getElementById('pulseActive');
  if (!list) return;
  const phases = data.phases || [];
  list.innerHTML = phases.map(item => `<div class="phase-item ${item.status === 'active' ? 'active' : ''}">
    <div><b>${escapeHtml(item.label)}</b><small>${escapeHtml(item.time)}</small></div>
    <p>${escapeHtml(item.focus || '')}</p>
  </div>`).join('') || hotspotEmpty('暂无研究节奏');
  if (active) active.textContent = data.active?.label || '--';
}

function renderHotspotList(data) {
  const list = document.getElementById('hotspotList');
  const count = document.getElementById('hotspotCount');
  if (!list) return;
  cachedHotspots = data.topics || [];
  if (count) count.textContent = `${cachedHotspots.length} 个主题`;
  const summary = data.source_summary || {};
  if (!cachedHotspots.length) {
    list.innerHTML = hotspotEmpty(summary.mode === 'local_research_fallback' ? '实时行情源暂不可用，本地研究数据也不足。可先刷新新闻或维护自选分组。' : '暂无热点数据。');
    return;
  }
  if (!selectedHotspotName || !cachedHotspots.some(item => item.name === selectedHotspotName)) {
    selectedHotspotName = cachedHotspots[0].name;
  }
  list.innerHTML = cachedHotspots.map(item => `<div class="hotspot-item ${item.name === selectedHotspotName ? 'active' : ''}" data-topic="${escapeAttr(item.name)}">
    <div>
      <b>${escapeHtml(item.name)}</b>
      <small>${escapeHtml(item.reason || '')}</small>
      <small>新闻 ${item.news_count || 0} · 标的 ${item.stock_count || 0} · 可信度 ${item.reliability || '--'} · ${escapeHtml((item.source_tags || []).slice(0, 2).join(' / '))}</small>
    </div>
    <div class="heat-pill ${directionClass(item.trend_direction)}">${Number(item.heat_score || 0)}</div>
  </div>`).join('');
  list.querySelectorAll('.hotspot-item').forEach(node => {
    node.addEventListener('click', () => selectHotspot(node.dataset.topic));
  });
  selectHotspot(selectedHotspotName);
}

async function selectHotspot(name) {
  selectedHotspotName = name;
  document.querySelectorAll('.hotspot-item').forEach(node => node.classList.toggle('active', node.dataset.topic === name));
  const detail = document.getElementById('hotspotDetail');
  if (!detail) return;
  const fallback = cachedHotspots.find(item => item.name === name);
  detail.innerHTML = fallback ? renderHotspotDetail({ topic: fallback, related_news: [], playbook: [] }, true) : hotspotEmpty('加载中...');
  try {
    const data = await API.get(`/api/hotspots/${encodeURIComponent(name)}`);
    detail.innerHTML = renderHotspotDetail(data, false);
  } catch (e) {
    detail.innerHTML = fallback
      ? renderHotspotDetail({ topic: fallback, related_news: [], playbook: [`详情加载失败：${e.message}`] }, true)
      : hotspotEmpty(`详情加载失败：${e.message}`);
  }
}

function renderHotspotDetail(data, partial) {
  const topic = data.topic || {};
  const stocks = topic.related_stocks || [];
  const news = data.related_news || [];
  const playbook = data.playbook || [];
  return `<div class="panel-head">
    <div>
      <span class="hotspot-kicker">热点详情${partial ? ' · 快照' : ''}</span>
      <h2>${escapeHtml(topic.name || '--')}</h2>
    </div>
    <span class="heat-pill ${directionClass(topic.trend_direction)}">${Number(topic.heat_score || 0)}</span>
  </div>
  <p>${escapeHtml(topic.reason || '暂无形成原因。')}</p>
  <p>来源：${escapeHtml((topic.source_tags || []).join(' / ') || '本地聚合')} · 可信度 ${topic.reliability || '--'}${topic.market_metrics?.change_pct != null ? ` · 板块涨跌 ${Number(topic.market_metrics.change_pct || 0).toFixed(3)}%` : ''}</p>
  <div class="stock-chip-list">
    ${stocks.length ? stocks.map(item => `<a class="stock-chip" href="/?code=${escapeAttr(item.code || '')}">
      <span><b>${escapeHtml(item.name || item.code)}</b> <small>${escapeHtml(item.code || '')}</small></span>
      <small>${item.holding ? '持仓' : '观察'} · ${escapeHtml(item.strategy_state || 'watch')}${item.change_pct != null ? ` · 涨跌 ${Number(item.change_pct || 0).toFixed(3)}%` : ''}${item.unrealized_pnl_pct != null ? ` · 持仓 ${Number(item.unrealized_pnl_pct || 0).toFixed(1)}%` : ''}</small>
    </a>`).join('') : hotspotEmpty('暂无关联自选或持仓标的')}
  </div>
  <div class="news-list">
    ${news.length ? news.map(row => `<div class="news-row">
      <b>${escapeHtml(row.title || '')}</b>
      <small>${escapeHtml(row.source || '')} · ${escapeHtml(row.published_at || row.cached_at || '')}</small>
    </div>`).join('') : ''}
  </div>
  <ul class="playbook-list">
    ${playbook.map(item => `<li>${escapeHtml(item)}</li>`).join('')}
  </ul>`;
}

function renderLifecycle(data) {
  const grid = document.getElementById('lifecycleGrid');
  const time = document.getElementById('lifecycleTime');
  if (!grid) return;
  const cols = data.columns || [];
  grid.innerHTML = cols.map(col => `<div class="lifecycle-col">
    <h3>${escapeHtml(col.label)} <span>${col.count || 0}</span></h3>
    ${(col.items || []).length ? col.items.map(item => `<div class="lifecycle-item">
      <b>${escapeHtml(item.name || item.code)} <small>${escapeHtml(item.code || '')}</small></b>
      <small>${escapeHtml(item.detail || '')}</small>
      <small>${escapeHtml(item.source || '')}${item.updated_at ? ' · ' + escapeHtml(item.updated_at) : ''}</small>
    </div>`).join('') : '<div class="lifecycle-item"><small>暂无</small></div>'}
  </div>`).join('') || hotspotEmpty('暂无策略生命周期数据');
  if (time) time.textContent = data.generated_at || '';
}

function renderResearchProgress(data) {
  const list = document.getElementById('researchProgressList');
  const time = document.getElementById('progressTime');
  if (!list) return;
  const rows = data.items || [];
  list.innerHTML = rows.length ? rows.map(item => {
    const pct = Math.max(0, Math.min(100, Number(item.progress_pct || 0)));
    const status = item.error ? `失败：${item.error}` : `${item.status || '--'} · ${item.completed || 0}/${item.total || 0} 阶段`;
    return `<div class="progress-row">
      <div><b>${escapeHtml(item.name || item.code)} <small>${escapeHtml(item.code || '')}</small></b><small>${escapeHtml(item.depth || '')} · ${escapeHtml(item.updated_at || '')}</small></div>
      <div><div class="progress-track"><span style="width:${pct}%"></span></div><small>${escapeHtml(status)}</small></div>
      <a class="btn btn-sm btn-ghost" href="/ai">查看</a>
    </div>`;
  }).join('') : hotspotEmpty('暂无最近研究任务');
  if (time) time.textContent = `${data.active_count || 0} 个进行中 · ${data.generated_at || ''}`;
}

async function loadHotspotsWorkbench() {
  const loaders = [
    API.get('/api/market-regime').then(renderRegime),
    API.get('/api/research-pulse').then(renderPulse),
    API.get('/api/hotspots?limit=12').then(renderHotspotList),
    API.get('/api/strategy-lifecycle').then(renderLifecycle),
    API.get('/api/research-progress').then(renderResearchProgress),
  ];
  const results = await Promise.allSettled(loaders);
  const failures = results.filter(item => item.status === 'rejected');
  if (failures.length) {
    showToast(`热点主线有 ${failures.length} 组数据加载失败`, 'warning');
  }
}

document.addEventListener('DOMContentLoaded', loadHotspotsWorkbench);
