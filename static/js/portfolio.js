/**
 * portfolio.js - 持仓管理页面 Phase 3
 * 功能：资产概览、持仓列表、交易记录、盈亏日历
 */

// ── 全局状态 ──────────────────────────────────────────────
let calendarYear = new Date().getFullYear();
let calendarMonth = new Date().getMonth() + 1;
let _selectedStockCode = null;

// ── 初始化 ────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  await Promise.all([
    loadOverview(),
    loadPortfolio(),
    loadHoldingsTable(),
    loadTrades(),
    loadOrders(),
    loadCalendar(),
    loadPendingPositions()
  ]);
});

// ── API 工具函数 ──────────────────────────────────────────
async function apiGet(url) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

async function apiPost(url, data) {
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

// ── Tab 切换 ──────────────────────────────────────────────
function switchPTab(name) {
  document.querySelectorAll('[id^="ptab-"]').forEach(el => el.style.display = 'none');
  document.querySelectorAll('.detail-tab').forEach(el => el.classList.remove('active'));
  document.getElementById('ptab-' + name).style.display = 'block';
  event.target.classList.add('active');
}

// ── 资产概览 ──────────────────────────────────────────────
async function loadOverview() {
  try {
    const data = await apiGet('/api/portfolio/overview');
    document.getElementById('totalAssets').textContent = formatMoney(data.total_assets);
    document.getElementById('marketValue').textContent = formatMoney(data.market_value);
    document.getElementById('cash').textContent = formatMoney(data.cash);
    
    const pnlEl = document.getElementById('totalPnl');
    pnlEl.textContent = formatMoney(data.unrealized_pnl) + ' (' + formatPct(data.unrealized_pnl_pct) + ')';
    pnlEl.className = 'pnl-value ' + priceClass(data.unrealized_pnl);

    // 显示费用统计（如果元素存在）
    const commEl = document.getElementById('totalCommission');
    if (commEl) commEl.textContent = formatMoney(data.total_commission);
    const stampEl = document.getElementById('totalStampTax');
    if (stampEl) stampEl.textContent = formatMoney(data.total_stamp_tax);
  } catch (e) {
    console.error('loadOverview error:', e);
  }
}

// ── 持仓列表（左侧） ────────────────────────────────────
async function loadPortfolio() {
  try {
    const data = await apiGet('/api/portfolio');
    const positions = data.positions || [];
    const listEl = document.getElementById('portfolioList');
    
    if (positions.length === 0) {
      listEl.innerHTML = '<div class="empty-state" style="padding:32px 16px;"><div class="icon">💼</div><p>暂无持仓</p></div>';
      return;
    }
    
    listEl.innerHTML = positions.map(p => {
      const cls = priceClass(p.unrealized_pnl);
      return `
        <div class="stock-card" onclick="showPositionDetail('${p.code}')">
          <div class="stock-card-inner">
            <div class="sc-left">
              <div>
                <div class="sc-name">${p.name}</div>
                <div class="sc-code">${p.code}</div>
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

// ── 持仓明细表格 ──────────────────────────────────────────
async function loadHoldingsTable() {
  try {
    const data = await apiGet('/api/portfolio');
    const positions = data.positions || [];
    const tbody = document.getElementById('holdingsBody');
    
    if (positions.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" class="empty-state"><p>暂无持仓</p></td></tr>';
      return;
    }
    
    tbody.innerHTML = positions.map(p => {
      const cls = priceClass(p.unrealized_pnl);
      const marketValue = p.price * p.total_shares;
      return `
        <tr style="cursor:pointer;" onclick="showPositionDetail('${p.code}')">
          <td><strong>${p.name}</strong><br><small class="text-muted">${p.code}</small></td>
          <td>${p.total_shares}</td>
          <td>${p.avg_cost.toFixed(2)}</td>
          <td class="${priceClass(p.change_pct)}">${p.price ? p.price.toFixed(2) : '--'}</td>
          <td>${formatMoney(marketValue)}</td>
          <td class="${cls}">${formatMoney(p.unrealized_pnl)}</td>
          <td class="${cls}">${formatPct(p.unrealized_pnl_pct)}</td>
        </tr>
      `;
    }).join('');
  } catch (e) {
    console.error('loadHoldingsTable error:', e);
  }
}

// ── 待持仓（Pending Positions） ─────────────────────────
async function loadPendingPositions() {
  try {
    const data = await apiGet('/api/pending-positions');
    const pending = data.positions || [];

    const countEl = document.getElementById('pendingCount');
    const bodyEl  = document.getElementById('pendingBody');

    if (countEl) countEl.textContent = pending.length;

    if (pending.length === 0) {
      bodyEl.innerHTML = '<div class="pending-empty">暂无待持仓股票 <button class="btn btn-sm btn-ghost" onclick="showAddPending()" style="margin-left:8px;">+ 添加</button></div>';
      return;
    }

    const rows = pending.map(s => {
      const stateText = s.strategy_state === 'near_buy' ? '接近买点' : '观察中';
      const stateClass = s.strategy_state === 'near_buy' ? 'near_buy' : 'watch';
      const targetPrice = s.target_buy_price;
      const currentPrice = s.current_price || 0;
      const planCost = s.plan_total_cost ? formatMoney(s.plan_total_cost) : '--';

      // 距离目标价的距离
      let distanceText = '--';
      let distanceClass = '';
      if (s.distance_pct != null) {
        const dist = s.distance_pct;
        distanceText = (dist >= 0 ? '+' : '') + dist.toFixed(2) + '%';
        distanceClass = dist <= 0 ? 'up' : 'down';
      }

      return `
        <tr data-pid="${s.id}">
          <td class="stock-name-cell">${s.name || '--'}</td>
          <td class="stock-code-cell">${s.code}</td>
          <td><span class="state-badge ${stateClass}">${stateText}</span></td>
          <td>${targetPrice ? targetPrice.toFixed(2) : '--'}</td>
          <td class="${priceClass(s.change_pct)}">${currentPrice ? currentPrice.toFixed(2) : '--'}</td>
          <td class="distance-cell ${distanceClass}">${distanceText}</td>
          <td>${s.plan_shares || '--'}</td>
          <td>${planCost}</td>
          <td>
            ${s.target_buy_price ? '<button class="btn btn-sm btn-primary" style="font-size:0.7rem;padding:2px 6px;" onclick="convertPendingToOrder(\'' + s.code + '\',\'' + (s.name || '') + '\',' + s.target_buy_price + ',' + (s.plan_shares || 100) + ')">转条件单</button> ' : ''}
            <button class="btn btn-sm btn-ghost" onclick="deletePendingPos(${s.id})">🗑</button>
          </td>
        </tr>
      `;
    }).join('');

    bodyEl.innerHTML = `
      <table class="pending-table">
        <thead>
          <tr>
            <th>股票</th>
            <th>代码</th>
            <th>状态</th>
            <th>目标买入价</th>
            <th>现价</th>
            <th>距离</th>
            <th>计划股数</th>
            <th>计划金额</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
      <div style="margin-top:8px;">
        <button class="btn btn-sm btn-primary" onclick="showAddPending()">+ 添加待持仓</button>
      </div>
    `;

    // Render comparison table
    renderPendingComparison(pending);
  } catch (e) {
    console.error('loadPendingPositions error:', e);
  }
}
function renderPendingComparison(pending) {
    const body = document.getElementById('pendingCompBody');
    const container = document.getElementById('pendingComparison');
    if (!body || !pending.length) { if(container) container.style.display='none'; return; }
    container.style.display = 'block';
    body.innerHTML = pending.map(p => {
        const planPrice = p.target_buy_price || 0;
        const curPrice = p.current_price || planPrice;
        const deviation = planPrice ? ((curPrice - planPrice) / planPrice * 100).toFixed(2) : '--';
        const planCost = p.plan_total_cost || (planPrice * (p.plan_shares || 0));
        const actualCost = curPrice * (p.plan_shares || 0);
        const status = p.status === 'filled' ? '✅ 已建仓' : p.status === 'cancelled' ? '❌ 已取消' : '⏳ 待触发';
        const devColor = deviation > 0 ? 'color:var(--color-up)' : deviation < 0 ? 'color:var(--color-down)' : '';
        return `<tr>
            <td>${p.code} ${p.name||''}</td>
            <td>${formatMoney(planPrice)}</td>
            <td>${formatMoney(curPrice)}</td>
            <td style="${devColor}">${deviation}%</td>
            <td>${formatMoney(planCost)}</td>
            <td>${formatMoney(actualCost)}</td>
            <td>${status}</td>
        </tr>`;
    }).join('');
}

// ── 添加待持仓弹窗 ──────────────────────────────────────
function showAddPending() {
  document.getElementById('pendingModal').classList.add('show');
  document.getElementById('pendingCode').focus();
}

function closePendingModal() {
  document.getElementById('pendingModal').classList.remove('show');
  document.getElementById('pendingForm').reset();
}

async function submitPending(e) {
  e.preventDefault();
  const code = document.getElementById('pendingCode').value.trim();
  const name = document.getElementById('pendingName').value.trim();
  const target_buy_price = parseFloat(document.getElementById('pendingPrice').value) || null;
  const plan_shares = parseInt(document.getElementById('pendingShares').value) || 100;
  const reason = document.getElementById('pendingReason').value.trim();

  if (!code) {
    alert('请填写股票代码');
    return;
  }

  try {
    await apiPost('/api/pending-positions', {
      code, name, target_buy_price, plan_shares, reason
    });
    closePendingModal();
    await loadPendingPositions();
  } catch (e) {
    console.error('submitPending error:', e);
    alert('添加失败: ' + e.message);
  }
}

async function deletePendingPos(pid) {
  if (!confirm('确认删除此待持仓记录？')) return;
  try {
    await apiDelete(`/api/pending-positions/${pid}`);
    await loadPendingPositions();
  } catch (e) {
    console.error('deletePendingPos error:', e);
    alert('删除失败: ' + e.message);
  }
}

// ── 待持仓转条件单 ──────────────────────────────────────
function convertPendingToOrder(code, name, targetPrice, planShares) {
  // 填充条件单表单并打开弹窗
  showAddOrder();
  document.getElementById('orderCode').value = code;
  document.getElementById('orderName').value = name;
  document.getElementById('orderCondType').value = 'price_lte';
  document.getElementById('orderTargetPrice').value = targetPrice;
  document.getElementById('orderAction').value = 'buy';
  document.getElementById('orderShares').value = planShares;
  document.getElementById('orderNotes').value = '由待持仓自动创建';
}

// ── 交易记录 ──────────────────────────────────────────────
let _allTrades = []; // cache for undo

async function loadTrades() {
  try {
    const data = await apiGet('/api/trades');
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
      return `
        <tr>
          <td>${t.trade_time || '--'}</td>
          <td><strong>${t.name || t.code}</strong></td>
          <td class="${dirClass}">${dirText}</td>
          <td>${t.price.toFixed(2)}</td>
          <td>${t.shares}</td>
          <td>¥${t.amount.toLocaleString()}</td>
          <td>${totalCost > 0 ? '¥' + totalCost.toFixed(2) : '--'}</td>
          <td><button class="btn btn-sm btn-ghost" onclick="deleteTrade(${t.id})" title="删除此笔">🗑</button></td>
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
    const data = await apiGet(`/api/trades/stats/${code}`);
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
    alert('删除失败: ' + e.message);
  }
}

async function undoLastTrade() {
  if (!_allTrades || _allTrades.length === 0) {
    alert('没有可撤销的交易记录');
    return;
  }
  const last = _allTrades[0]; // trades are sorted by time DESC
  if (!confirm(`确认撤销最近一笔交易？\n${last.name || last.code} ${last.direction === 'buy' ? '买入' : '卖出'} ${last.shares}股 @ ${last.price.toFixed(2)}`)) return;
  try {
    await apiDelete(`/api/trades/${last.id}`);
    await refreshAll();
  } catch (e) {
    console.error('undoLastTrade error:', e);
    alert('撤销失败: ' + e.message);
  }
}

async function clearStockTrades() {
  if (!_selectedStockCode) {
    alert('请先选择一只股票');
    return;
  }
  if (!confirm(`确认清空 ${_selectedStockCode} 的所有交易记录？此操作不可恢复！`)) return;
  try {
    await apiDelete(`/api/trades/stock/${_selectedStockCode}`);
    closeStockActionPanel();
    await refreshAll();
  } catch (e) {
    console.error('clearStockTrades error:', e);
    alert('清空失败: ' + e.message);
  }
}

async function saveManualTargetPrice() {
  if (!_selectedStockCode) {
    alert('请先选择一只股票');
    return;
  }
  const priceInput = document.getElementById('manualTargetPrice');
  const price = parseFloat(priceInput.value);
  if (!price || price <= 0) {
    alert('请输入有效的目标价格');
    return;
  }
  try {
    await apiPut(`/api/watchlist/${_selectedStockCode}`, { target_buy_price: price });
    alert('目标价已保存');
    priceInput.value = '';
  } catch (e) {
    console.error('saveManualTargetPrice error:', e);
    alert('保存失败: ' + e.message);
  }
}

async function refreshAll() {
  await Promise.all([
    loadOverview(),
    loadPortfolio(),
    loadHoldingsTable(),
    loadTrades()
  ]);
}

// ── 盈亏日历 ──────────────────────────────────────────────
async function loadCalendar() {
  try {
    const data = await apiGet(`/api/pnl/calendar?year=${calendarYear}&month=${calendarMonth}`);
    
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
    
    // 计算颜色等级
    const allPnl = Object.values(pnlMap).filter(v => v !== 0);
    const maxPnl = Math.max(...allPnl.map(Math.abs), 1);
    
    function getPnlColor(pnl) {
      if (pnl === 0 || pnl == null) return '';
      const ratio = Math.min(Math.abs(pnl) / maxPnl, 1);
      if (pnl > 0) {
        // 红色系（涨）
        const alpha = 0.2 + ratio * 0.6;
        return `rgba(224, 122, 95, ${alpha})`;
      } else {
        // 绿色系（跌）
        const alpha = 0.2 + ratio * 0.6;
        return `rgba(82, 183, 136, ${alpha})`;
      }
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
    const data = await apiGet(`/api/pnl/calendar/day/${dateStr}`);
    const dailyPnl = data.daily_pnl;
    const stockPnl = data.stock_pnl || [];
    const trades = data.trades || [];

    let totalPnl = dailyPnl ? (dailyPnl.total_pnl || 0) : 0;
    let totalPnlText = totalPnl >= 0 ? '+' + totalPnl.toFixed(2) : totalPnl.toFixed(2);

    let html = `
      <div class="day-detail-header">
        <strong>${dateStr}</strong>
        <span class="${priceClass(totalPnl)}" style="font-family:var(--font-mono);font-weight:700;">${totalPnlText}</span>
        <button class="btn btn-sm btn-ghost" onclick="document.getElementById('dayDetailPopup').remove()" style="margin-left:auto;">✕</button>
      </div>
    `;

    if (stockPnl.length > 0) {
      html += '<div class="day-detail-stocks">';
      stockPnl.forEach(s => {
        const amt = s.amount || 0;
        const cls = priceClass(amt);
        html += `<div class="day-detail-row">
          <span>${s.name || s.code}</span>
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
          <span>${t.name || t.code}</span>
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
    popup.innerHTML = '<div class="day-detail-header"><strong>' + dateStr + '</strong><button class="btn btn-sm btn-ghost" onclick="document.getElementById(\'dayDetailPopup\').remove()">✕</button></div><div style="text-align:center;color:var(--text-muted);padding:12px;">加载失败</div>';
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

// ── 条件单 ────────────────────────────────────────────────
async function loadOrders() {
  try {
    const data = await apiGet('/api/orders');
    const orders = data.orders || [];
    const container = document.getElementById('ordersList');
    
    if (orders.length === 0) {
      container.innerHTML = '<div class="empty-state"><div class="icon">🔔</div><p>暂无条件单</p></div>';
      return;
    }
    
    container.innerHTML = orders.map(o => {
      const statusClass = o.status === 'pending' ? 'up' : o.status === 'triggered' ? 'down' : '';
      const statusText = o.status === 'pending' ? '活跃' : o.status === 'triggered' ? '已触发' : '已取消';
      const condText = getConditionText(o.condition_type, o.target_price);
      const actionText = o.action === 'buy' ? '买入' : '卖出';
      const distance = o.distance_pct != null ? (o.distance_pct >= 0 ? '+' : '') + o.distance_pct.toFixed(2) + '%' : '--';
      
      return `
        <div class="order-card">
          <div class="order-header">
            <span class="order-status ${statusClass}">${statusText}</span>
            <span class="order-stock">${o.name || o.code}</span>
            <span class="order-code">${o.code}</span>
          </div>
          <div class="order-body">
            <div class="order-condition">条件: ${condText}</div>
            <div class="order-action">动作: ${actionText} ${o.shares || '--'}股</div>
            <div class="order-meta">
              <span>现价: ${o.current_price ? o.current_price.toFixed(2) : '--'}</span>
              <span>距触发: ${distance}</span>
              ${o.expires_at ? '<span>失效: ' + o.expires_at.substring(0, 16) + '</span>' : ''}
            </div>
          </div>
          <div class="order-actions">
            <button class="btn btn-sm btn-ghost" onclick="cancelOrder(${o.id})">取消</button>
          </div>
        </div>
      `;
    }).join('');
  } catch (e) {
    console.error('loadOrders error:', e);
  }
}

function getConditionText(type, price) {
  switch(type) {
    case 'price_lte': return `价格 ≤ ${price}`;
    case 'price_gte': return `价格 ≥ ${price}`;
    case 'change_pct_gte': return `涨幅 ≥ ${price}%`;
    case 'change_pct_lte': return `跌幅 ≥ ${price}%`;
    default: return type;
  }
}

function showAddOrder() {
  document.getElementById('orderModal').classList.add('show');
  document.getElementById('orderCode').focus();
}

function closeOrderModal() {
  document.getElementById('orderModal').classList.remove('show');
  document.getElementById('orderForm').reset();
}

async function submitOrder(e) {
  e.preventDefault();
  
  const code = document.getElementById('orderCode').value.trim();
  const name = document.getElementById('orderName').value.trim();
  const condition_type = document.getElementById('orderCondType').value;
  const target_price = parseFloat(document.getElementById('orderTargetPrice').value);
  const action = document.getElementById('orderAction').value;
  const shares = parseInt(document.getElementById('orderShares').value) || 0;
  const notes = document.getElementById('orderNotes').value.trim();
  const expiresAtInput = document.getElementById('orderExpiresAt');
  let expires_at = null;
  if (expiresAtInput && expiresAtInput.value) {
    // datetime-local gives "YYYY-MM-DDTHH:MM", convert to "YYYY-MM-DD HH:MM:SS"
    expires_at = expiresAtInput.value.replace('T', ' ') + ':00';
  }
  
  if (!code || !target_price) {
    alert('请填写完整信息');
    return;
  }
  
  try {
    await apiPost('/api/orders', {
      code, name, condition_type, target_price, action, shares, notes, expires_at
    });
    
    closeOrderModal();
    await loadOrders();
    alert('条件单创建成功！');
  } catch (e) {
    console.error('submitOrder error:', e);
    alert('创建失败: ' + e.message);
  }
}

async function cancelOrder(id) {
  if (!confirm('确认取消此条件单？')) return;
  
  try {
    await fetch(`/api/orders/${id}`, { method: 'DELETE' });
    await loadOrders();
  } catch (e) {
    console.error('cancelOrder error:', e);
    alert('取消失败: ' + e.message);
  }
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
    alert('请填写完整信息');
    return;
  }
  
  try {
    const result = await apiPost('/api/trades', {
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
    
    alert('交易录入成功！');
  } catch (e) {
    console.error('submitTrade error:', e);
    alert('录入失败: ' + e.message);
  }
}
