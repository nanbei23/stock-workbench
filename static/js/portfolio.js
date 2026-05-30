/**
 * portfolio.js - 持仓管理页面 Phase 3
 * 功能：资产概览、持仓列表、交易记录、盈亏日历
 */

// ── Toast 通知系统 ─────────────────────────────────────────
function showToast(msg, type='info', duration=3000) {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.style.cssText = 'position:fixed;top:16px;right:16px;z-index:10000;display:flex;flex-direction:column;gap:8px;';
    document.body.appendChild(container);
  }
  const toast = document.createElement('div');
  const colors = {success:'#52B788',error:'#E07A5F',info:'#4A90D9',warn:'#E9C46A'};
  toast.style.cssText = `padding:10px 16px;border-radius:8px;color:#fff;font-size:0.85rem;background:${colors[type]||colors.info};box-shadow:0 4px 12px rgba(0,0,0,0.3);opacity:0;transform:translateX(40px);transition:all 0.3s ease;max-width:320px;`;
  toast.textContent = msg;
  container.appendChild(toast);
  requestAnimationFrame(()=>{toast.style.opacity='1';toast.style.transform='translateX(0)';});
  setTimeout(()=>{
    toast.style.opacity='0';
    toast.style.transform='translateX(40px)';
    setTimeout(()=>toast.remove(),300);
  }, duration);
}

// ── 全局状态 ──────────────────────────────────────────────
let calendarYear = new Date().getFullYear();
let calendarMonth = new Date().getMonth() + 1;
let _selectedStockCode = null;
const _tabLoaded = {};
let _stopLossPct = 8; // 默认值，将从API加载

function selectedAccountId() {
  return localStorage.getItem('accountId') || 'default';
}

function withAccount(url) {
  const accountId = selectedAccountId();
  if (!accountId || accountId === 'all') return url;
  const join = url.includes('?') ? '&' : '?';
  return `${url}${join}account_id=${encodeURIComponent(accountId)}`;
}

// ── 初始化 ────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  // 首次加载所有数据（holdings是默认tab，直接加载）
  await Promise.all([
    loadOverview(),
    loadPortfolio(),
    loadHoldingsTable(),
    loadAccountDashboard()
  ]);
  // 标记 holdings tab 已加载
  _tabLoaded['holdings'] = true;

  // 从API加载止损百分比设置
  try {
    const settings = await portfolioGet('/api/settings');
    if (settings.stop_loss_pct) {
      _stopLossPct = parseInt(settings.stop_loss_pct);
      await loadHoldingsTable(); // 用正确的止损值重新渲染
    }
  } catch (e) {
    console.error('加载止损设置失败:', e);
  }

  // 设置止损百分比下拉框初始值
  const stopLossSelect = document.getElementById('stopLossSelect');
  if (stopLossSelect) {
    stopLossSelect.value = _stopLossPct;
  }

  // 盘中自动刷新
  function isMarketOpen() {
    const now = new Date();
    const day = now.getDay();
    if (day === 0 || day === 6) return false;
    const h = now.getHours(), m = now.getMinutes();
    const t = h * 60 + m;
    return (t >= 570 && t <= 690) || (t >= 780 && t <= 900); // 9:30-11:30, 13:00-15:00
  }
  setInterval(() => {
    if (isMarketOpen()) {
      loadOverview();
      loadPortfolio();
      loadHoldingsTable();
      loadAccountDashboard();
    }
  }, 30000);
});

// ── API 工具函数 ──────────────────────────────────────────
async function portfolioGet(url) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

async function portfolioPost(url, data) {
  const resp = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data)
  });
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

async function apiDelete(url) {
  const resp = await fetch(url, { method: 'DELETE' });
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

async function apiPut(url, data) {
  const resp = await fetch(url, {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data)
  });
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

// ── 格式化函数 ────────────────────────────────────────────
function formatMoney(n) {
  if (n == null) return '--';
  return '¥' + n.toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
}

function formatPct(n) {
  if (n == null) return '--';
  return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
}

function priceClass(n) {
  if (n > 0) return 'up';
  if (n < 0) return 'down';
  return 'flat';
}

function esc(value) {
  return typeof escapeHtml === 'function'
    ? escapeHtml(value)
    : String(value ?? '').replace(/[&<>"']/g, ch => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[ch]));
}

function escAttr(value) {
  return typeof escapeAttr === 'function' ? escapeAttr(value) : esc(value);
}

function jsArg(value) {
  return String(value ?? '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/\r?\n/g, ' ');
}

// ── Tab 切换（懒加载） ──────────────────────────────────
function switchPTab(name) {
  document.querySelectorAll('[id^="ptab-"]').forEach(el => el.style.display = 'none');
  document.querySelectorAll('.detail-tab').forEach(el => el.classList.remove('active'));
  const tab = document.getElementById('ptab-' + name);
  if (tab) tab.style.display = 'block';
  // Find and activate the correct button
  document.querySelectorAll('.detail-tab').forEach(btn => {
    if (btn.dataset.tab === name) btn.classList.add('active');
  });
  // Lazy load: only load data on first switch
  if (!_tabLoaded[name]) {
    _tabLoaded[name] = true;
    if (name === 'trades') loadTrades();
    else if (name === 'plan') loadTradingPlans();
    else if (name === 'calendar') loadCalendar();
  }
}

// ── 资产概览 ──────────────────────────────────────────────
async function loadOverview() {
  try {
    const data = await portfolioGet(withAccount('/api/portfolio/overview'));
    document.getElementById('totalAssets').textContent = formatMoney(data.total_assets);
    document.getElementById('marketValue').textContent = formatMoney(data.market_value);
    document.getElementById('cash').textContent = formatMoney(data.cash);
    
    const pnlEl = document.getElementById('totalPnl');
    pnlEl.textContent = formatMoney(data.unrealized_pnl) + ' (' + formatPct(data.unrealized_pnl_pct) + ')';
    pnlEl.className = 'pnl-value ' + priceClass(data.unrealized_pnl);

    // 当日盈亏
    const dailyPnlEl = document.getElementById('dailyPnl');
    if (dailyPnlEl) {
      dailyPnlEl.textContent = formatMoney(data.daily_pnl) + ' (' + formatPct(data.daily_pnl_pct) + ')';
      dailyPnlEl.className = 'pnl-value ' + priceClass(data.daily_pnl);
    }

    // 资金使用率
    const cashRatioEl = document.getElementById('cashRatio');
    if (cashRatioEl && data.total_assets > 0) {
      const ratio = ((data.total_assets - data.cash) / data.total_assets * 100).toFixed(1);
      cashRatioEl.textContent = ratio + '%';
    }

    // 显示费用统计（如果元素存在）
    const commEl = document.getElementById('totalCommission');
    if (commEl) commEl.textContent = formatMoney(data.total_commission);
    const stampEl = document.getElementById('totalStampTax');
    if (stampEl) stampEl.textContent = formatMoney(data.total_stamp_tax);
  } catch (e) {
    console.error('loadOverview error:', e);
  }
}

async function loadAccountDashboard() {
  const el = document.getElementById('accountDashboard');
  if (!el) return;
  try {
    const data = await portfolioGet('/api/portfolio/accounts/overview');
    const combined = data.combined || {};
    const accounts = data.accounts || [];
    const active = selectedAccountId();
    const combinedRow = `<div class="account-dashboard-summary">
      <div><span>合并总资产</span><strong>${formatMoney(combined.total_assets || 0)}</strong></div>
      <div><span>持仓市值</span><strong>${formatMoney(combined.market_value || 0)}</strong></div>
      <div><span>当日盈亏</span><strong class="${priceClass(combined.daily_pnl || 0)}">${formatMoney(combined.daily_pnl || 0)}</strong></div>
      <div><span>浮动盈亏</span><strong class="${priceClass(combined.unrealized_pnl || 0)}">${formatMoney(combined.unrealized_pnl || 0)}</strong></div>
    </div>`;
    const rows = accounts.length ? accounts.map(a => {
      const isActive = a.id === active;
      const weight = combined.market_value ? (a.market_value / combined.market_value * 100).toFixed(1) : '0.0';
      return `<button class="account-dashboard-row ${isActive ? 'active' : ''}" onclick="switchAccountFromDashboard('${jsArg(a.id)}')">
        <span><b>${esc(a.name || a.id)}</b><small>${esc(a.broker || a.id)}</small></span>
        <span>${formatMoney(a.total_assets || 0)}</span>
        <span>${a.position_count || 0}只</span>
        <span>${weight}%</span>
        <strong class="${priceClass(a.unrealized_pnl || 0)}">${formatMoney(a.unrealized_pnl || 0)}</strong>
      </button>`;
    }).join('') : '<div class="empty-state"><p>暂无账户数据</p></div>';
    el.innerHTML = `${combinedRow}<div class="account-dashboard-head"><span>账户</span><span>总资产</span><span>持仓</span><span>市值占比</span><span>浮动盈亏</span></div>${rows}`;
  } catch (e) {
    console.error('loadAccountDashboard error:', e);
    el.innerHTML = '<div class="empty-state"><p>账户看板加载失败</p></div>';
  }
}

function switchAccountFromDashboard(accountId) {
  localStorage.setItem('accountId', accountId);
  location.reload();
}

// ── 持仓列表（左侧） ────────────────────────────────────
async function loadPortfolio() {
  try {
    const data = await portfolioGet(withAccount('/api/portfolio'));
    const positions = data.positions || [];
    const listEl = document.getElementById('portfolioList');
    
    if (positions.length === 0) {
      listEl.innerHTML = '<div class="empty-state" style="padding:32px 16px;"><div class="icon ui-glyph">PF</div><p>暂无持仓</p></div>';
      return;
    }
    
    listEl.innerHTML = positions.map(p => {
      const cls = priceClass(p.unrealized_pnl);
      const code = esc(p.code);
      const name = esc(p.name);
      const clickCode = jsArg(p.code);
      return `
        <div class="stock-card" onclick="showPositionDetail('${clickCode}')">
          <div class="stock-card-inner">
            <div class="sc-left">
              <div>
                <div class="sc-name">${name}</div>
                <div class="sc-code">${code}</div>
              </div>
              <div class="sc-price ${priceClass(p.change_pct)}">
                ${p.price ? p.price.toFixed(2) : '--'}<span class="sc-price-unit">元</span>
              </div>
            </div>
            <div class="sc-right">
              <div class="sc-data-row">
                <span class="sc-data-lbl">持仓</span>
                <span class="sc-data-val">${p.total_shares}股</span>
              </div>
              <div class="sc-data-row">
                <span class="sc-data-lbl">成本</span>
                <span class="sc-data-val">${p.avg_cost.toFixed(2)}</span>
              </div>
              <div class="sc-data-row">
                <span class="sc-data-lbl">盈亏</span>
                <span class="sc-data-val ${cls}">${formatMoney(p.unrealized_pnl)}</span>
              </div>
            </div>
          </div>
          <div class="stock-card-bar ${cls}"></div>
        </div>
      `;
    }).join('');
  } catch (e) {
    console.error('loadPortfolio error:', e);
  }
}

// ── 止损百分比设置 ──────────────────────────────────────
function updateStopLoss() {
  const select = document.getElementById('stopLossSelect');
  if (select) {
    _stopLossPct = parseInt(select.value);
    // 保存到数据库
    portfolioPost('/api/settings/bulk', { settings: { stop_loss_pct: _stopLossPct.toString() } });
    loadHoldingsTable(); // 重新渲染持仓表
  }
}

// ── 持仓明细表格 ──────────────────────────────────────────
async function loadHoldingsTable() {
  try {
    const [portfolioData, overview] = await Promise.all([
      portfolioGet(withAccount('/api/portfolio')),
      portfolioGet(withAccount('/api/portfolio/overview'))
    ]);
    const positions = portfolioData.positions || [];
    const totalAssets = overview.total_assets || 1;
    const tbody = document.getElementById('holdingsBody');
    
    if (positions.length === 0) {
      tbody.innerHTML = '<tr><td colspan="9" class="empty-state"><p>暂无持仓</p></td></tr>';
      return;
    }
    
    tbody.innerHTML = positions.map(p => {
      const cls = priceClass(p.unrealized_pnl);
      const marketValue = p.price * p.total_shares;
      const weight = (marketValue / totalAssets * 100).toFixed(1);
      const dailyPnl = p.daily_pnl != null ? p.daily_pnl : (p.price - (p.prev_close || p.avg_cost)) * p.total_shares;
      const dailyCls = priceClass(dailyPnl);
      const code = esc(p.code);
      const name = esc(p.name);
      const clickCode = jsArg(p.code);
      
      // 止损距离计算（可配置百分比）
      const stopLossPrice = p.avg_cost * (1 - _stopLossPct / 100);
      const stopLossDist = p.price > 0 ? ((p.price - stopLossPrice) / p.price * 100).toFixed(1) : '--';
      const stopLossCls = stopLossDist === '--' ? 'flat' : (parseFloat(stopLossDist) > 5 ? 'up' : (parseFloat(stopLossDist) > 2 ? 'flat' : 'down'));
      
      return `
        <tr style="cursor:pointer;" onclick="showPositionDetail('${clickCode}')">
          <td><strong>${name}</strong><br><small class="text-muted">${code}</small></td>
          <td>${p.total_shares}</td>
          <td>${p.avg_cost.toFixed(2)}</td>
          <td class="${priceClass(p.change_pct)}">${p.price ? p.price.toFixed(2) : '--'}</td>
          <td>${formatMoney(marketValue)}</td>
          <td>${weight}%</td>
          <td class="${dailyCls}">${formatMoney(dailyPnl)}</td>
          <td class="${cls}">${formatMoney(p.unrealized_pnl)}</td>
          <td class="${cls}">${formatPct(p.unrealized_pnl_pct)}</td>
          <td class="${stopLossCls}">${stopLossDist}%</td>
        </tr>
      `;
    }).join('');
  } catch (e) {
    console.error('loadHoldingsTable error:', e);
  }
}

// ── 交易计划（合并 待持仓 + 条件单） ─────────────────────
async function loadTradingPlans() {
  try {
    const data = await portfolioGet(withAccount('/api/trading-plans'));
    const plans = data.plans || [];

    const countEl = document.getElementById('planCount');
    const listEl = document.getElementById('planList');
    if (countEl) countEl.textContent = plans.length;

    if (plans.length === 0) {
      listEl.innerHTML = '<div class="empty-state"><div class="icon ui-glyph">PL</div><p>暂无交易计划</p><button class="btn btn-sm btn-primary" onclick="showAddPlan()" style="margin-top:8px;">+ 新建计划</button></div>';
      return;
    }

    listEl.innerHTML = plans.map(p => {
      const dirText = p.direction === 'buy' ? '买入' : '卖出';
      const dirClass = p.direction === 'buy' ? 'up' : 'down';
      const typeMap = {watch:'观察', near_target:'接近买点', conditional:'条件触发'};
      const typeText = typeMap[p.plan_type] || p.plan_type;
      const statusMap = {pending:'待触发', triggered:'已触发', filled:'已建仓', cancelled:'已取消'};
      const statusText = statusMap[p.status] || p.status;
      const statusClass = p.status === 'pending' ? 'up' : p.status === 'filled' ? 'down' : '';
      const name = esc(p.name || p.code);
      const code = esc(p.code);
      const reason = esc(p.reason);

      const distance = p.distance_pct != null ? (p.distance_pct >= 0 ? '+' : '') + p.distance_pct.toFixed(2) + '%' : '--';
      const planCost = p.plan_total_cost ? formatMoney(p.plan_total_cost) : '--';

      return `
        <div class="order-card" style="margin-bottom:8px;">
          <div class="order-header" style="display:flex;align-items:center;gap:8px;">
            <span class="order-status ${statusClass}" style="font-size:0.75rem;padding:2px 6px;border-radius:4px;">${statusText}</span>
            <span class="${dirClass}" style="font-size:0.75rem;font-weight:600;">${dirText}</span>
            <span class="order-stock" style="font-weight:600;">${name}</span>
            <span class="order-code" style="color:var(--text-secondary);font-size:0.8rem;">${code}</span>
            <span style="margin-left:auto;font-size:0.75rem;color:var(--text-secondary);">${typeText}</span>
          </div>
          <div class="order-body" style="display:flex;gap:16px;align-items:center;padding:8px 0;font-size:0.85rem;">
            ${p.target_price ? `<span>目标价: <strong>${p.target_price.toFixed(2)}</strong></span>` : ''}
            <span>现价: ${p.current_price ? '<strong>' + p.current_price.toFixed(2) + '</strong>' : '--'}</span>
            ${p.target_price ? `<span>距离: <strong class="${p.distance_pct != null && p.distance_pct <= 0 ? 'up' : 'down'}">${distance}</strong></span>` : ''}
            <span>计划: ${p.plan_shares || '--'}股</span>
            <span>金额: ${planCost}</span>
            ${p.reason ? `<span style="color:var(--text-secondary);font-style:italic;">${reason}</span>` : ''}
            <span style="margin-left:auto;">
              <button class="btn btn-sm btn-ghost" onclick="deletePlan(${p.id})" style="font-size:0.75rem;" title="删除计划">删除</button>
            </span>
          </div>
        </div>
      `;
    }).join('');

    // render comparison table
    renderPendingComparison(plans.filter(p => p.plan_type !== 'conditional'));
  } catch (e) {
    console.error('loadTradingPlans error:', e);
  }
}

// ── 交易计划弹窗 ──────────────────────────────────────
function showAddPlan() {
  document.getElementById('planModal').classList.add('show');
  document.getElementById('planCode').focus();
}

function closePlanModal() {
  document.getElementById('planModal').classList.remove('show');
  document.getElementById('planForm').reset();
}

async function submitPlan(e) {
  e.preventDefault();
  const code = document.getElementById('planCode').value.trim();
  const name = document.getElementById('planName').value.trim();
  const direction = document.getElementById('planDirection').value;
  const plan_type = document.getElementById('planType').value;
  const target_price = parseFloat(document.getElementById('planTargetPrice').value) || null;
  const condition_type = document.getElementById('planCondType').value;
  const plan_shares = parseInt(document.getElementById('planShares').value) || 100;
  const reason = document.getElementById('planReason').value.trim();
  const expiresAtInput = document.getElementById('planExpiresAt');
  let expires_at = null;
  if (expiresAtInput && expiresAtInput.value) {
    expires_at = expiresAtInput.value.replace('T', ' ') + ':00';
  }

  if (!code) {
    showToast('请填写股票代码', 'warn');
    return;
  }

  try {
    await portfolioPost('/api/trading-plans', {
      code, name, direction, plan_type, target_price, condition_type, plan_shares, reason, expires_at
    });
    closePlanModal();
    await loadTradingPlans();
    showToast('交易计划创建成功！', 'success');
  } catch (e) {
    console.error('submitPlan error:', e);
    showToast('创建失败: ' + e.message, 'error');
  }
}

async function deletePlan(pid) {
  if (!confirm('确认删除此交易计划？')) return;
  try {
    await apiDelete(`/api/trading-plans/${pid}`);
    await loadTradingPlans();
  } catch (e) {
    console.error('deletePlan error:', e);
    showToast('删除失败: ' + e.message, 'error');
  }
}

// ── 交易弹窗 ──────────────────────────────────────────────
let _allTrades = []; // cache for undo

async function loadTrades() {
  try {
    const data = await portfolioGet(withAccount('/api/trades'));
    const trades = data.trades || [];
    _allTrades = trades;
    const tbody = document.getElementById('tradesBody');
    
    if (trades.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" class="empty-state"><p>暂无记录</p></td></tr>';
      return;
    }
    
    tbody.innerHTML = trades.map(t => {
      const dirClass = t.direction === 'buy' ? 'up' : 'down';
      const dirText = t.direction === 'buy' ? '买入' : '卖出';
      const totalCost = t.commission + t.stamp_tax + t.transfer_fee;
      const name = esc(t.name || t.code);
      const tradeTime = esc(t.trade_time || '--');
      return `
        <tr>
          <td>${tradeTime}</td>
          <td><strong>${name}</strong></td>
          <td class="${dirClass}">${dirText}</td>
          <td>${t.price.toFixed(2)}</td>
          <td>${t.shares}</td>
          <td>¥${t.amount.toLocaleString()}</td>
          <td>${totalCost > 0 ? '¥' + totalCost.toFixed(2) : '--'}</td>
          <td><button class="btn btn-sm btn-ghost" onclick="deleteTrade(${t.id})" title="删除此笔">删除</button></td>
        </tr>
      `;
    }).join('');
  } catch (e) {
    console.error('loadTrades error:', e);
  }
}

// ── 交易管理 ──────────────────────────────────────────────
async function loadTradeStats(code) {
  try {
    const data = await portfolioGet(`/api/trades/stats/${code}`);
    document.getElementById('lowestBuyPrice').textContent = data.lowest_buy_price ? data.lowest_buy_price.toFixed(2) : '--';
    document.getElementById('latestBuyPrice').textContent = data.latest_buy_price ? data.latest_buy_price.toFixed(2) : '--';
  } catch (e) {
    console.error('loadTradeStats error:', e);
  }
}

function closeStockActionPanel() {
  document.getElementById('stockActionPanel').style.display = 'none';
  _selectedStockCode = null;
}

async function deleteTrade(id) {
  if (!confirm('确认删除此笔交易记录？删除后持仓将自动重算。')) return;
  try {
    await apiDelete(`/api/trades/${id}`);
    await refreshAll();
  } catch (e) {
    console.error('deleteTrade error:', e);
    showToast('删除失败: ' + e.message, 'error');
  }
}

async function undoLastTrade() {
  if (!_allTrades || _allTrades.length === 0) {
    showToast('没有可撤销的交易记录', 'warn');
    return;
  }
  const last = _allTrades[0]; // trades are sorted by time DESC
  if (!confirm(`确认撤销最近一笔交易？\n${last.name || last.code} ${last.direction === 'buy' ? '买入' : '卖出'} ${last.shares}股 @ ${last.price.toFixed(2)}`)) return;
  try {
    await apiDelete(`/api/trades/${last.id}`);
    await refreshAll();
  } catch (e) {
    console.error('undoLastTrade error:', e);
    showToast('撤销失败: ' + e.message, 'error');
  }
}

async function clearStockTrades() {
  if (!_selectedStockCode) {
    showToast('请先选择一只股票', 'warn');
    return;
  }
  if (!confirm(`确认清空 ${_selectedStockCode} 的所有交易记录？此操作不可恢复！`)) return;
  try {
    await apiDelete(`/api/trades/stock/${_selectedStockCode}`);
    closeStockActionPanel();
    await refreshAll();
  } catch (e) {
    console.error('clearStockTrades error:', e);
    showToast('清空失败: ' + e.message, 'error');
  }
}

async function saveManualTargetPrice() {
  if (!_selectedStockCode) {
    showToast('请先选择一只股票', 'warn');
    return;
  }
  const priceInput = document.getElementById('manualTargetPrice');
  const price = parseFloat(priceInput.value);
  if (!price || price <= 0) {
    showToast('请输入有效的目标价格', 'warn');
    return;
  }
  try {
    await apiPut(`/api/watchlist/${_selectedStockCode}`, { target_buy_price: price });
    showToast('目标价已保存', 'success');
    priceInput.value = '';
  } catch (e) {
    console.error('saveManualTargetPrice error:', e);
    showToast('保存失败: ' + e.message, 'error');
  }
}

async function refreshAll() {
  await Promise.all([
    loadOverview(),
    loadPortfolio(),
    loadHoldingsTable(),
    loadTrades(),
    loadAccountDashboard()
  ]);
}

// ── 盈亏日历 ──────────────────────────────────────────────
async function loadCalendar() {
  try {
    const data = await portfolioGet(`/api/pnl/calendar?year=${calendarYear}&month=${calendarMonth}`);
    
    // 更新标题
    document.getElementById('calendarTitle').textContent = `${calendarYear}年${calendarMonth}月`;
    document.getElementById('monthPnl').textContent = formatMoney(data.total_pnl);
    document.getElementById('monthPnl').className = priceClass(data.total_pnl);
    document.getElementById('winRate').textContent = data.win_rate + '%';
    
    // 构建日历网格
    const grid = document.getElementById('calendarGrid');
    const firstDay = new Date(calendarYear, calendarMonth - 1, 1).getDay(); // 0=周日
    const daysInMonth = new Date(calendarYear, calendarMonth, 0).getDate();
    
    // 盈亏数据映射
    const pnlMap = {};
    (data.days || []).forEach(d => {
      const day = parseInt(d.date.split('-')[2]);
      pnlMap[day] = d.total_pnl;
    });
    
    // 计算颜色等级 — 用 CSS 变量跟随主题
    const allPnl = Object.values(pnlMap).filter(v => v !== 0);
    const maxPnl = Math.max(...allPnl.map(Math.abs), 1);
    const root = getComputedStyle(document.documentElement);
    const upColor = root.getPropertyValue('--color-up').trim();   // e.g. #E07A5F or #FF4D6A
    const downColor = root.getPropertyValue('--color-down').trim(); // e.g. #52B788 or #00D4A1
    
    function getPnlColor(pnl) {
      if (pnl === 0 || pnl == null) return '';
      const ratio = Math.min(Math.abs(pnl) / maxPnl, 1);
      const alpha = 0.15 + ratio * 0.35;
      const hex = pnl > 0 ? upColor : downColor;
      // hex → rgba
      const r = parseInt(hex.slice(1,3), 16);
      const g = parseInt(hex.slice(3,5), 16);
      const b = parseInt(hex.slice(5,7), 16);
      return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }
    
    let html = '';
    
    // 星期头
    ['一', '二', '三', '四', '五', '六', '日'].forEach(d => {
      html += `<div class="calendar-day-header">${d}</div>`;
    });
    
    // 空白天
    for (let i = 0; i < (firstDay === 0 ? 6 : firstDay - 1); i++) {
      html += '<div class="calendar-day empty"></div>';
    }
    
    // 日期
    for (let day = 1; day <= daysInMonth; day++) {
      const pnl = pnlMap[day];
      const color = getPnlColor(pnl);
      const pnlText = pnl != null ? (pnl >= 0 ? '+' : '') + pnl.toFixed(0) : '';
      const pnlClass = pnl > 0 ? 'up' : pnl < 0 ? 'down' : '';
      
      html += `
        <div class="calendar-day" style="background:${color}" onclick="showDayDetail(${day})">
          <div class="calendar-day-num">${day}</div>
          <div class="calendar-day-pnl ${pnlClass}">${pnlText}</div>
        </div>
      `;
    }
    
    grid.innerHTML = html;
  } catch (e) {
    console.error('loadCalendar error:', e);
  }
}

function changeMonth(delta) {
  calendarMonth += delta;
  if (calendarMonth > 12) {
    calendarMonth = 1;
    calendarYear++;
  } else if (calendarMonth < 1) {
    calendarMonth = 12;
    calendarYear--;
  }
  loadCalendar();
}

function showDayDetail(day) {
  // 显示当日各股票盈亏明细
  const dateStr = `${calendarYear}-${String(calendarMonth).padStart(2,'0')}-${String(day).padStart(2,'0')}`;

  // 找到日历格子的位置
  const dayEl = event?.target?.closest('.calendar-day');
  const existing = document.getElementById('dayDetailPopup');
  if (existing) existing.remove();

  const popup = document.createElement('div');
  popup.id = 'dayDetailPopup';
  popup.className = 'day-detail-popup';
  popup.innerHTML = '<div class="day-detail-loading">加载中...</div>';

  // 定位到日历格子附近
  if (dayEl) {
    const parent = dayEl.closest('.card');
    if (parent) parent.style.position = 'relative';
    (dayEl.parentElement || document.body).appendChild(popup);
  } else {
    document.getElementById('calendarGrid').appendChild(popup);
  }

  // 点击其他地方关闭
  setTimeout(() => {
    document.addEventListener('click', function closePopup(e) {
      if (!popup.contains(e.target)) {
        popup.remove();
        document.removeEventListener('click', closePopup);
      }
    });
  }, 100);

  // 加载数据
  loadDayDetailData(dateStr, popup);
}

async function loadDayDetailData(dateStr, popup) {
  try {
    const data = await portfolioGet(`/api/pnl/calendar/day/${dateStr}`);
    const dailyPnl = data.daily_pnl;
    const stockPnl = data.stock_pnl || [];
    const trades = data.trades || [];

    let totalPnl = dailyPnl ? (dailyPnl.total_pnl || 0) : 0;
    let totalPnlText = totalPnl >= 0 ? '+' + totalPnl.toFixed(2) : totalPnl.toFixed(2);

    let html = `
      <div class="day-detail-header">
        <strong>${dateStr}</strong>
        <span class="${priceClass(totalPnl)}" style="font-family:var(--font-mono);font-weight:700;">${totalPnlText}</span>
        <button class="btn btn-sm btn-ghost" onclick="document.getElementById('dayDetailPopup').remove()" style="margin-left:auto;">关闭</button>
      </div>
    `;

    if (stockPnl.length > 0) {
      html += '<div class="day-detail-stocks">';
      stockPnl.forEach(s => {
        const amt = s.amount || 0;
        const cls = priceClass(amt);
        html += `<div class="day-detail-row">
          <span>${esc(s.name || s.code)}</span>
          <span class="${cls}" style="font-family:var(--font-mono);">${amt >= 0 ? '+' : ''}${amt.toFixed(2)}</span>
        </div>`;
      });
      html += '</div>';
    }

    if (trades.length > 0) {
      html += '<div class="day-detail-trades"><div style="font-size:0.75rem;color:var(--text-muted);margin-bottom:4px;">当日交易</div>';
      trades.forEach(t => {
        const dir = t.direction === 'buy' ? '买入' : '卖出';
        const cls = t.direction === 'buy' ? 'up' : 'down';
        html += `<div class="day-detail-row" style="font-size:0.8rem;">
          <span class="${cls}">${dir}</span>
          <span>${esc(t.name || t.code)}</span>
          <span>${t.shares}股 × ${t.price.toFixed(2)}</span>
        </div>`;
      });
      html += '</div>';
    }

    if (stockPnl.length === 0 && trades.length === 0) {
      html += '<div style="text-align:center;color:var(--text-muted);padding:12px;">当日无交易记录</div>';
    }

    popup.innerHTML = html;
  } catch (e) {
    console.error('loadDayDetailData error:', e);
    popup.innerHTML = '<div class="day-detail-header"><strong>' + esc(dateStr) + '</strong><button class="btn btn-sm btn-ghost" onclick="document.getElementById(\'dayDetailPopup\').remove()">关闭</button></div><div style="text-align:center;color:var(--text-muted);padding:12px;">加载失败</div>';
  }
}

function showPositionDetail(code) {
  // 显示股票操作面板
  _selectedStockCode = code;
  const panel = document.getElementById('stockActionPanel');
  panel.style.display = 'block';

  // 查找股票名称
  const stockName = code; // fallback
  document.getElementById('stockActionTitle').textContent = `操作: ${code}`;

  // 加载交易统计
  loadTradeStats(code);
}

// ── 交易弹窗 ──────────────────────────────────────────────
function showAddTrade() {
  document.getElementById('tradeModal').classList.add('show');
  document.getElementById('tradeCode').focus();
}

function closeTradeModal() {
  document.getElementById('tradeModal').classList.remove('show');
  document.getElementById('tradeForm').reset();
}

async function submitTrade(e) {
  e.preventDefault();
  
  const code = document.getElementById('tradeCode').value.trim();
  const name = document.getElementById('tradeName').value.trim();
  const direction = document.getElementById('tradeDir').value;
  const price = parseFloat(document.getElementById('tradePrice').value);
  const shares = parseInt(document.getElementById('tradeShares').value);
  const commission = parseFloat(document.getElementById('tradeCommission').value) || 0;
  const stampTax = parseFloat(document.getElementById('tradeStampTax').value) || 0;
  const transferFee = parseFloat(document.getElementById('tradeTransferFee').value) || 0;
  
  if (!code || !price || !shares) {
    showToast('请填写完整信息', 'warn');
    return;
  }
  
  try {
    const result = await portfolioPost('/api/trades', {
      code, name, direction, price, shares,
      commission, stamp_tax: stampTax, transfer_fee: transferFee
    });
    
    closeTradeModal();
    
    // 刷新数据
    await Promise.all([
      loadOverview(),
      loadPortfolio(),
      loadTrades(),
      loadHoldingsTable()
    ]);
    
    showToast('交易录入成功！', 'success');
  } catch (e) {
    console.error('submitTrade error:', e);
    showToast('录入失败: ' + e.message, 'error');
  }
}
