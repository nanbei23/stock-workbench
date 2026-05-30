/* 自选股页面交互 — Phase 2 完整实现 */

let currentCode = null;
let pendingDeleteCode = null;
let currentKlinePeriod = 'm1';
let currentKlineCount = 240;
let currentChartType = 'candlestick';
let currentPrevClose = null;  // 昨收价，用于分时图涨跌色参考
let dragSrcEl = null;   // 当前拖拽的卡片

const STOCK_SIGNAL_LABEL = {
  'STRONG_BUY': '强烈买入', 'BUY': '买入', 'OVERWEIGHT': '增持',
  'HOLD': '持有', 'UNDERWEIGHT': '减持', 'SELL': '卖出', 'STRONG_SELL': '强烈卖出'
};

function panelState(message, iconCode = '') {
  const icon = iconCode ? `<div class="icon ui-glyph" data-icon="${escapeAttr(iconCode)}"></div>` : '';
  return `<div class="empty-state" style="padding:24px;">${icon}<p>${escapeHtml(message)}</p></div>`;
}

function loadingState(message = '加载中...') {
  return panelState(message);
}

function stockMoveBadge(changePct) {
  if (changePct == null) return '';
  if (changePct >= 5) return '<span class="stock-alert-token up">强势</span>';
  if (changePct <= -5) return '<span class="stock-alert-token down">风险</span>';
  return '';
}

function signalBadge(signal) {
  if (!signal) return '';
  const label = STOCK_SIGNAL_LABEL[signal.signal] || signal.signal;
  const sigCls = signal.signal.includes('BUY') ? 'signal-buy' : signal.signal.includes('SELL') ? 'signal-sell' : 'signal-hold';
  return `<div class="sc-signal ${sigCls}">${escapeHtml(label)}</div>`;
}

function stockCardMetrics(s) {
  const cls = priceClass(s.change_pct);
  const pctSign = s.change_pct != null ? (s.change_pct >= 0 ? '+' : '') : '';
  const chgSign = s.change != null ? (s.change >= 0 ? '+' : '') : '';
  const dailyPnl = s.daily_pnl ? formatPnl(s.daily_pnl) : (s.change != null ? (chgSign + s.change.toFixed(2) + '元') : '--');
  const dailyPnlCls = s.daily_pnl ? priceClass(s.daily_pnl) : cls;
  const holdPnl = s.unrealized_pnl ? formatPnl(s.unrealized_pnl) : '--';
  const holdPnlCls = s.unrealized_pnl ? priceClass(s.unrealized_pnl) : '';
  return { cls, pctSign, dailyPnl, dailyPnlCls, holdPnl, holdPnlCls };
}

/* ============================================================
   0a. K线周期Tab
   ============================================================ */
function initKlineTabs() {
  const tabs = document.querySelectorAll('#klineTabs .chart-tab');
  tabs.forEach(tab => {
    tab.addEventListener('click', async () => {
      tabs.forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      currentKlinePeriod = tab.dataset.period;
      currentKlineCount = parseInt(tab.dataset.count) || 120;
      currentChartType = tab.dataset.chartType || 'candlestick';
      await reloadKline();
    });
  });
}

async function reloadKline() {
  if (!currentCode) return;
  const container = document.getElementById('klineChart');
  const dateEl = document.getElementById('klineDataDate');
  if (dateEl) dateEl.textContent = '';
  container.innerHTML = '<div class="empty-state" style="padding:24px;"><p>加载中...</p></div>';
  try {
    const data = await API.get(`/api/kline/${currentCode}?period=${currentKlinePeriod}&count=${currentKlineCount}`);
    const klines = data?.klines || [];
    if (typeof renderKline === 'function' && klines.length > 0) {
      // 分时图：显示数据日期
      if (dateEl && isIntradayPeriod(currentKlinePeriod) && klines[0]?.date) {
        const d = klines[0].date.split(' ')[0];
        dateEl.textContent = d;
      }
      let chartOptions = {};
      try {
        const paramsData = await API.get(`/api/strategy/${currentCode}/params`);
        const params = paramsData?.data || {};
        if (params.buy_prices) {
          const buyPrices = typeof params.buy_prices === 'string' ? JSON.parse(params.buy_prices) : params.buy_prices;
          chartOptions.buy_prices = buyPrices;
        }
      } catch (_) {}
      try {
        const wlData = await API.get('/api/watchlist');
        const stock = (wlData?.stocks || []).find(s => s.code === currentCode);
        if (stock) {
          chartOptions.stop_loss_price = stock.stop_loss_price;
          chartOptions.target_sell_price = stock.target_sell_price;
        }
      } catch (_) {}
      renderKline('klineChart', klines, { ...chartOptions, period: currentKlinePeriod, chartType: currentChartType, refPrice: currentPrevClose });
    } else {
      container.innerHTML = panelState('暂无K线数据');
    }
  } catch (e) {
    console.error('reloadKline error:', e);
    container.innerHTML = panelState('K线加载失败');
  }
}

/* ============================================================
   1. loadWatchlist — GET /api/watchlist，渲染左侧股票卡片
   ============================================================ */
async function loadWatchlist() {
  const listEl = document.getElementById('stockList');
  try {
    const data = await API.get('/api/watchlist');
    const stocks = data.stocks || [];

    if (stocks.length === 0) {
      listEl.innerHTML = panelState('暂无自选股', 'WL');
      return;
    }

    // 获取AI信号
    let signals = {};
    try {
      const sigData = await API.get('/api/signal/signals/latest');
      if (sigData && sigData.signals) signals = sigData.signals;
    } catch (e) {}

    listEl.innerHTML = '<div class="stock-card-list">' + stocks.map(s => renderStockCard(s, signals[s.code])).join('') + '</div>';
  } catch (e) {
    console.error('loadWatchlist error:', e);
    listEl.innerHTML = panelState('加载失败，请刷新重试');
  }

  initDragDrop();
}

function renderStockCard(s, signal) {
  const isActive = currentCode === s.code;
  const { cls, pctSign, dailyPnl, dailyPnlCls, holdPnl, holdPnlCls } = stockCardMetrics(s);
  const barCls = cls === 'up' ? 'up' : cls === 'down' ? 'down' : 'flat';
  return `
    <div class="stock-card ${isActive ? 'active' : ''}"
         onclick="selectStock('${escapeAttr(s.code)}')"
         data-code="${escapeAttr(s.code)}">
      <div class="stock-card-inner">
        <div class="sc-grip" title="拖拽排序">⋮⋮</div>
        <div class="sc-left">
          <div>
            <div class="sc-name">${stockMoveBadge(s.change_pct)}${escapeHtml(s.name || s.code)}</div>
            <div class="sc-code">${escapeHtml(s.code)}</div>
          </div>
          <div class="sc-price ${cls}">${formatPrice(s.price)}<span class="sc-price-unit">元</span></div>
        </div>
        <div class="sc-right">
          <div class="sc-data-row">
            <span class="sc-data-lbl">当日盈亏</span>
            <span class="sc-data-val ${dailyPnlCls}">${dailyPnl}</span>
          </div>
          <div class="sc-data-row">
            <span class="sc-data-lbl">持仓盈亏</span>
            <span class="sc-data-val ${holdPnlCls}">${holdPnl}</span>
          </div>
          <div class="sc-data-row">
            <span class="sc-data-lbl">当日涨幅</span>
            <span class="sc-data-val ${cls}">${pctSign}${s.change_pct != null ? s.change_pct.toFixed(2) : '--'}%</span>
          </div>
          ${signalBadge(signal)}
        </div>
      </div>
      <div class="stock-card-bar ${barCls}"></div>
      <button class="btn-remove" onclick="event.stopPropagation();removeStock('${escapeAttr(s.code)}')" title="删除">×</button>
    </div>`;
}
/* ============================================================
   1a. 拖拽排序 — HTML5 Drag and Drop
   ============================================================ */
function initDragDrop() {
  const cards = document.querySelectorAll('.stock-card');
  const list  = document.querySelector('.stock-card-list');
  if (!list || cards.length === 0) return;

  cards.forEach(card => {
    const grip = card.querySelector('.sc-grip');
    if (!grip) return;

    // 拖拽手柄：mousedown 设置 draggable
    grip.addEventListener('mousedown', () => {
      card.setAttribute('draggable', 'true');
    });
    // mouseup 恢复（避免普通点击时也能拖拽）
    document.addEventListener('mouseup', () => {
      card.setAttribute('draggable', 'false');
    });

    // ── dragstart ──
    card.addEventListener('dragstart', (e) => {
      dragSrcEl = card;
      card.classList.add('dragging');
      list.classList.add('drag-active');
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', card.dataset.code);
    });

    // ── dragend ──
    card.addEventListener('dragend', () => {
      card.classList.remove('dragging');
      list.classList.remove('drag-active');
      document.querySelectorAll('.drag-over-top, .drag-over-bottom').forEach(el => {
        el.classList.remove('drag-over-top', 'drag-over-bottom');
      });
      dragSrcEl = null;
    });

    // ── dragover ──
    card.addEventListener('dragover', (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      if (card === dragSrcEl) return;

      const rect = card.getBoundingClientRect();
      const midY = rect.top + rect.height / 2;

      card.classList.remove('drag-over-top', 'drag-over-bottom');
      if (e.clientY < midY) {
        card.classList.add('drag-over-top');
      } else {
        card.classList.add('drag-over-bottom');
      }
    });

    // ── dragleave ──
    card.addEventListener('dragleave', () => {
      card.classList.remove('drag-over-top', 'drag-over-bottom');
    });

    // ── drop ──
    card.addEventListener('drop', (e) => {
      e.preventDefault();
      e.stopPropagation();
      card.classList.remove('drag-over-top', 'drag-over-bottom');

      if (!dragSrcEl || dragSrcEl === card) return;

      const rect = card.getBoundingClientRect();
      const midY = rect.top + rect.height / 2;
      const parent = card.parentNode;

      if (e.clientY < midY) {
        parent.insertBefore(dragSrcEl, card);
      } else {
        parent.insertBefore(dragSrcEl, card.nextSibling);
      }

      // 持久化排序
      saveReorder();
    });
  });
}

/* ============================================================
   1b. saveReorder — 保存自选股排序到后端
   ============================================================ */
async function saveReorder() {
  const cards = document.querySelectorAll('.stock-card');
  const items = [];
  cards.forEach((card, idx) => {
    items.push({ code: card.dataset.code, sort_order: idx + 1 });
  });
  try {
    await API.put('/api/watchlist/reorder', { items });
  } catch (e) {
    console.error('saveReorder error:', e);
  }
}

/* ============================================================
   2. selectStock — 点击左侧股票项
   ============================================================ */
function selectStock(code) {
  currentCode = code;

  document.querySelectorAll('.stock-card').forEach(el => {
    el.classList.toggle('active', el.dataset.code === code);
  });

  const url = new URL(window.location);
  url.searchParams.set('stock', code);
  history.replaceState(null, '', url);

  loadStockDetail(code);
}

/* ============================================================
   3. loadStockDetail — 获取并展示股票详情
   ============================================================ */
async function loadStockDetail(code) {
  const detailView  = document.getElementById('detailView');
  const detailEmpty = document.getElementById('detailEmpty');

  detailEmpty.style.display = 'none';
  detailView.style.display  = 'block';

  const [quoteRes] = await Promise.all([
    API.get(`/api/quote/${code}`).catch(() => null),
  ]);

  if (quoteRes) {
    const q = quoteRes;
    currentPrevClose = q.prev_close || null;
    setText('d-name', q.name || code);
    setText('d-code', code);

    const priceEl = document.getElementById('d-price');
    const changeEl = document.getElementById('d-change');
    priceEl.textContent = formatPrice(q.price);
    priceEl.className = 'quote-price ' + priceClass(q.change_pct);
    changeEl.textContent = `${formatPct(q.change_pct)}  ${q.change != null ? (q.change >= 0 ? '+' : '') + q.change.toFixed(2) : ''}`;
    changeEl.className = 'quote-change ' + priceClass(q.change_pct);

    // 3×3 信息网格
    setText('d-open',     q.open != null ? q.open.toFixed(2) : '--');
    setText('d-high',     q.high != null ? q.high.toFixed(2) : '--');
    setText('d-amount',   formatAmount(q.amount));
    setText('d-prev',     q.prev_close != null ? q.prev_close.toFixed(2) : '--');
    setText('d-low',      q.low != null ? q.low.toFixed(2) : '--');
    setText('d-volume',   formatVolume(q.volume));
    setText('d-mcap',     formatMarketCap(q.total_market_cap || q.circ_market_cap));
    setText('d-pe',       q.pe != null ? q.pe.toFixed(2) : '--');
    setText('d-turnover', q.turnover != null ? q.turnover.toFixed(2) + '%' : '--');
    // 高低价着色
    const highEl = document.getElementById('d-high');
    const lowEl  = document.getElementById('d-low');
    if (highEl) highEl.className = 'info-value ' + priceClass((q.high||0) - (q.prev_close||0));
    if (lowEl)  lowEl.className  = 'info-value ' + priceClass((q.low||0)  - (q.prev_close||0));

    // 盈亏行
    const pnlRow = document.getElementById('pnlRow');
    const hasPnl = q.unrealized_pnl != null && q.unrealized_pnl !== 0;
    if (pnlRow) pnlRow.style.display = hasPnl ? 'flex' : 'none';
    if (hasPnl) {
      const pnlEl = document.getElementById('d-unrealized-pnl');
      const dailyPnlEl = document.getElementById('d-daily-pnl');
      const dailyChgEl = document.getElementById('d-daily-change');
      if (pnlEl) {
        pnlEl.textContent = formatPnl(q.unrealized_pnl) + (q.unrealized_pnl_pct ? ` (${q.unrealized_pnl_pct > 0 ? '+' : ''}${q.unrealized_pnl_pct.toFixed(2)}%)` : '');
        pnlEl.className = 'pnl-value ' + priceClass(q.unrealized_pnl);
      }
      if (dailyPnlEl) {
        dailyPnlEl.textContent = formatPnl(q.daily_pnl || 0);
        dailyPnlEl.className = 'pnl-value ' + priceClass(q.daily_pnl || 0);
      }
      if (dailyChgEl) {
        dailyChgEl.textContent = formatPct(q.change_pct);
        dailyChgEl.className = 'pnl-value ' + priceClass(q.change_pct);
      }
    }
  }

  // K线：默认分时
  currentKlinePeriod = 'm1';
  currentKlineCount = 240;
  currentChartType = 'intraday';
  // 重置Tab高亮到"分时"
  document.querySelectorAll('#klineTabs .chart-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.period === 'm1' && t.dataset.count === '240');
  });
  await reloadKline();

  load7Layer(code);

  // 默认异动Tab — 自动加载
  loadStockAnomalies(code);
}

/* ============================================================
   3a. load7Layer — 七层数据（并发获取全部数据）
   ============================================================ */
async function load7Layer(code) {
  try {
    const data = await API.get(`/api/7layer/${code}/all`);
    const d = data.data || {};

    // 基本面 — 从 signal 数据中取 info
    if (d.info) {
      setText('l-pe',    d.info.pe ?? '--');
      setText('l-pb',    d.info.pb ?? '--');
      setText('l-roe',   d.info.roe ?? '--');
      setText('l-eps',   d.info.eps ?? '--');
      setText('l-margin', d.info.industry ?? '--');
    }

    // 技术指标 — 暂用价格相关数据占位
    if (d.quote) {
      setText('l-rsi',  '--');
      setText('l-macd', '--');
      setText('l-kdj',  '--');
      setText('l-boll', '--');
      setText('l-ma',   '--');
    }

    // 资金面 (l-mainflow, l-north, l-margin-bal, l-dragon)
    if (d.fund) {
      const fund = d.fund;
      setLayerValue('l-mainflow', fund.main_net_flow, '万', formatFundFlow);
      setLayerValue('l-north',    fund.north_net, '亿', formatNorthFund);
      setLayerValue('l-margin-bal', fund.margin_balance, '亿', formatMarginBal);
      setText('l-dragon', fund.dragon_tiger_count != null ? `${fund.dragon_tiger_count}次` : '--');
    }

    // 消息面 (l-sentiment, l-rating, l-institution)
    if (d.signal) {
      const sig = d.signal;
      // 新闻情绪
      if (sig.sentiment) {
        const s = sig.sentiment;
        const trend = s.score > 0.1 ? '偏多' : (s.score < -0.1 ? '偏空' : '中性');
        setText('l-sentiment', `${trend} · ${s.label} (${s.positive}/${s.negative})`);
      } else {
        setText('l-sentiment', '--');
      }
      // 研报评级
      if (sig.rating && sig.rating.rating) {
        setText('l-rating', `${sig.rating.rating} · ${sig.rating.org}`);
      } else {
        setText('l-rating', '--');
      }
      // 机构动向
      if (sig.institution) {
        setText('l-institution', `${sig.institution.count}家 · ${sig.institution.latest || '--'}`);
      } else {
        setText('l-institution', '--');
      }

      // 政策面 (l-policy, l-sector)
      if (sig.policy) {
        setText('l-policy', sig.policy.industry || '--');
      } else {
        setText('l-policy', '--');
      }
      if (sig.sector) {
        const names = sig.sector.blocks.slice(0, 3).map(b => b.block_name).join('、');
        setText('l-sector', names || `${sig.sector.count}个板块`);
      } else {
        setText('l-sector', '--');
      }

      // 风险面 (l-lockup, l-pledge, l-goodwill)
      if (sig.lockup) {
        setText('l-lockup', `${sig.lockup.count}次 · 最近${sig.lockup.next_date || '--'}`);
      } else {
        setText('l-lockup', '无解禁');
      }
      if (sig.pledge && sig.pledge.ratio) {
        setText('l-pledge', `${sig.pledge.ratio}%`);
      } else {
        setText('l-pledge', '--');
      }
      if (sig.goodwill && sig.goodwill.ratio) {
        setText('l-goodwill', `${sig.goodwill.ratio}%`);
      } else {
        setText('l-goodwill', '--');
      }
    }

    // 策略 (l-target, l-stoploss, l-buypoint, l-strategy-state)
    if (d.strategy) {
      const st = d.strategy;
      setText('l-target', st.target_sell_price ? `¥${st.target_sell_price}` : '--');
      setText('l-stoploss', st.stop_loss ? `¥${st.stop_loss}` : '--');
      const buyPrices = st.buy_prices ? (typeof st.buy_prices === 'string' ? JSON.parse(st.buy_prices) : st.buy_prices) : [];
      setText('l-buypoint', buyPrices.length ? buyPrices.map(p => `¥${p}`).join(' / ') : '--');
      setText('l-strategy-state', st.strategy_state || '--');
    } else {
      setText('l-target', '--');
      setText('l-stoploss', '--');
      setText('l-buypoint', '--');
      setText('l-strategy-state', '--');
    }
  } catch (_) { /* 占位 */ }
}

/* 格式化辅助函数 */
function formatFundFlow(v) {
  if (v == null) return '--';
  const abs = Math.abs(v);
  if (abs >= 1e8) return (v / 1e8).toFixed(2) + '亿';
  if (abs >= 1e4) return (v / 1e4).toFixed(2) + '万';
  return v.toFixed(2);
}

function formatNorthFund(v) {
  if (v == null) return '--';
  return (v / 1e8).toFixed(2);
}

function formatMarginBal(v) {
  if (v == null) return '--';
  const abs = Math.abs(v);
  if (abs >= 1e8) return (v / 1e8).toFixed(2);
  if (abs >= 1e4) return (v / 1e4).toFixed(2);
  return v.toFixed(2);
}

function setLayerValue(id, value, unit, formatter) {
  const el = document.getElementById(id);
  if (!el) return;
  if (value == null) { el.textContent = '--'; return; }
  const formatted = formatter ? formatter(value) : value;
  const sign = value > 0 ? '+' : '';
  el.textContent = `${sign}${formatted}${unit || ''}`;
  // 着色
  if (typeof value === 'number') {
    el.className = 'value ' + (value > 0 ? 'up' : value < 0 ? 'down' : '');
  }
}

/* ============================================================
   4. toggleSearch — 显示/隐藏搜索框
   ============================================================ */
function toggleSearch() {
  const box = document.getElementById('searchBox');
  const visible = box.style.display !== 'none';
  box.style.display = visible ? 'none' : 'block';
  if (!visible) document.getElementById('searchInput').focus();
}

/* ============================================================
   5. addStock — POST /api/watchlist
   ============================================================ */
async function addStock() {
  const input = document.getElementById('searchInput');
  const code = input.value.trim();
  if (!code) return;

  try {
    await API.post('/api/watchlist', { code, name: '', group: '默认' });
    input.value = '';
    toggleSearch();
    await loadWatchlist();
    selectStock(code);
  } catch (e) {
    console.error('addStock error:', e);
    alert('添加失败，请重试');
  }
}

/* ============================================================
   6. removeStock — 弹出确认弹窗
   ============================================================ */
async function removeStock(code) {
  // 查找股票名称
  const card = document.querySelector(`.stock-card[data-code="${code}"]`);
  const name = card ? card.querySelector('.sc-name')?.textContent : code;
  pendingDeleteCode = code;

  document.getElementById('deleteModalBody').innerHTML =
    `是否删除自选股票 <strong>${name}（${code}）</strong>？`;
  document.getElementById('deleteModal').classList.add('show');
}

function closeDeleteModal() {
  document.getElementById('deleteModal').classList.remove('show');
  pendingDeleteCode = null;
}

async function confirmDelete() {
  const code = pendingDeleteCode;
  if (!code) return;
  closeDeleteModal();

  try {
    await API.del(`/api/watchlist/${code}`);
    if (currentCode === code) {
      currentCode = null;
      document.getElementById('detailView').style.display = 'none';
      document.getElementById('detailEmpty').style.display = 'block';
    }
    await loadWatchlist();
  } catch (e) {
    console.error('removeStock error:', e);
    alert('删除失败，请重试');
  }
}

/* ============================================================
   7. loadIndices — GET /api/index，填充三大指数
   ============================================================ */
async function loadIndices() {
  try {
    const data = await API.get('/api/index');
    setIndex('sh',  data.sh);
    setIndex('sz',  data.sz);
    setIndex('cyb', data.cyb);
  } catch (e) {
    console.error('loadIndices error:', e);
  }
}

function setIndex(key, d) {
  if (!d) return;
  const valEl  = document.getElementById(`idx-${key}`);
  const chgEl  = document.getElementById(`idx-${key}-chg`);
  if (!valEl || !chgEl) return;

  valEl.textContent = d.price != null ? d.price.toFixed(2) : '--';
  chgEl.textContent = d.change_pct != null ? formatPct(d.change_pct) : '--';

  const cls = priceClass(d.change_pct);
  valEl.className = 'idx-value ' + cls;
  chgEl.className = 'idx-change ' + cls;
}

/* ============================================================
   7a. loadSentiment — GET /api/market/sentiment，填充市场情绪条
   ============================================================ */
async function loadSentiment() {
  try {
    const data = await API.get('/api/market/sentiment');
    if (!data) return;

    const breadth = data.breadth || {};
    const northbound = data.northbound || {};

    // 涨跌家数
    const upEl = document.getElementById('sent-up');
    const downEl = document.getElementById('sent-down');
    const limitUpEl = document.getElementById('sent-limit-up');
    const limitDownEl = document.getElementById('sent-limit-down');
    const northEl = document.getElementById('sent-north');

    if (upEl) upEl.textContent = breadth.up || '--';
    if (downEl) downEl.textContent = breadth.down || '--';
    if (limitUpEl) limitUpEl.textContent = breadth.limit_up || '--';
    if (limitDownEl) limitDownEl.textContent = breadth.limit_down || '--';

    // 北向资金（API已返回亿元单位）
    if (northEl && northbound.total_net != null) {
      const val = Number(northbound.total_net).toFixed(2);
      northEl.textContent = val + '亿';
      northEl.className = 'sentiment-value ' + (northbound.total_net >= 0 ? 'up' : 'down');
    }
  } catch (e) {
    console.error('loadSentiment error:', e);
  }
}

/* ============================================================
   8. refreshQuotes — 刷新列表中所有股票行情（不重绘 DOM）
   ============================================================ */
async function refreshQuotes() {
  try {
    const data = await API.get('/api/watchlist');
    const stocks = data.stocks || [];
    stocks.forEach(s => {
      const el = document.querySelector(`.stock-card[data-code="${s.code}"]`);
      if (!el) return;

      const { cls, pctSign, dailyPnl, dailyPnlCls, holdPnl, holdPnlCls } = stockCardMetrics(s);
      const barCls = cls === 'up' ? 'up' : cls === 'down' ? 'down' : 'flat';

      // 底部色条
      const bar = el.querySelector('.stock-card-bar');
      if (bar) bar.className = `stock-card-bar ${barCls}`;

      // 价格
      const priceEl = el.querySelector('.sc-price');
      if (priceEl) {
        priceEl.innerHTML = formatPrice(s.price) + '<span class="sc-price-unit">元</span>';
        priceEl.className = `sc-price ${cls}`;
      }

      // 当日盈亏
      const pnlEl = el.querySelector('.sc-data-row:nth-child(1) .sc-data-val');
      if (pnlEl) {
        pnlEl.textContent = dailyPnl;
        pnlEl.className = `sc-data-val ${dailyPnlCls}`;
      }

      // 持仓盈亏
      const holdEl = el.querySelector('.sc-data-row:nth-child(2) .sc-data-val');
      if (holdEl) {
        holdEl.textContent = holdPnl;
        holdEl.className = `sc-data-val ${holdPnlCls}`;
      }

      // 当日涨幅
      const pctEl = el.querySelector('.sc-data-row:nth-child(3) .sc-data-val');
      if (pctEl) {
        pctEl.textContent = `${pctSign}${s.change_pct != null ? s.change_pct.toFixed(2) : '--'}%`;
        pctEl.className = `sc-data-val ${cls}`;
      }
    });

    // 刷新详情头部
    if (currentCode) {
      const q = await API.get(`/api/quote/${currentCode}`);
      const priceEl  = document.getElementById('d-price');
      const changeEl = document.getElementById('d-change');
      if (priceEl && q.price != null) {
        priceEl.textContent = formatPrice(q.price);
        priceEl.className = 'quote-price ' + priceClass(q.change_pct);
      }
      if (changeEl) {
        changeEl.textContent = `${formatPct(q.change_pct)}  ${q.change != null ? (q.change >= 0 ? '+' : '') + q.change.toFixed(2) : ''}`;
        changeEl.className = 'quote-change ' + priceClass(q.change_pct);
      }
      setText('d-open',     q.open != null ? q.open.toFixed(2) : '--');
      setText('d-high',     q.high != null ? q.high.toFixed(2) : '--');
      setText('d-amount',   formatAmount(q.amount));
      setText('d-prev',     q.prev_close != null ? q.prev_close.toFixed(2) : '--');
      setText('d-low',      q.low != null ? q.low.toFixed(2) : '--');
      setText('d-volume',   formatVolume(q.volume));
      setText('d-mcap',     formatMarketCap(q.total_market_cap || q.circ_market_cap));
      setText('d-pe',       q.pe != null ? q.pe.toFixed(2) : '--');
      setText('d-turnover', q.turnover != null ? q.turnover.toFixed(2) + '%' : '--');
      // 高低价着色
      const highEl = document.getElementById('d-high');
      const lowEl  = document.getElementById('d-low');
      if (highEl) highEl.className = 'info-value ' + priceClass((q.high||0) - (q.prev_close||0));
      if (lowEl)  lowEl.className  = 'info-value ' + priceClass((q.low||0)  - (q.prev_close||0));
      // 盈亏行
      const pnlRow = document.getElementById('pnlRow');
      const hasPnl = q.unrealized_pnl != null && q.unrealized_pnl !== 0;
      if (pnlRow) pnlRow.style.display = hasPnl ? 'flex' : 'none';
      if (hasPnl) {
        const pnlEl = document.getElementById('d-unrealized-pnl');
        const dailyPnlEl = document.getElementById('d-daily-pnl');
        const dailyChgEl = document.getElementById('d-daily-change');
        if (pnlEl) {
          pnlEl.textContent = formatPnl(q.unrealized_pnl) + (q.unrealized_pnl_pct ? ` (${q.unrealized_pnl_pct > 0 ? '+' : ''}${q.unrealized_pnl_pct.toFixed(2)}%)` : '');
          pnlEl.className = 'pnl-value ' + priceClass(q.unrealized_pnl);
        }
        if (dailyPnlEl) {
          dailyPnlEl.textContent = formatPnl(q.daily_pnl || 0);
          dailyPnlEl.className = 'pnl-value ' + priceClass(q.daily_pnl || 0);
        }
        if (dailyChgEl) {
          dailyChgEl.textContent = formatPct(q.change_pct);
          dailyChgEl.className = 'pnl-value ' + priceClass(q.change_pct);
        }
      }
    }
  } catch (e) {
    console.error('refreshQuotes error:', e);
  }
}

/* ============================================================
   辅助函数
   ============================================================ */
function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val ?? '--';
}

function formatAmount(n) {
  if (n == null) return '--';
  if (n >= 1e8)  return (n / 1e8).toFixed(2)  + '亿';
  if (n >= 1e4)  return (n / 1e4).toFixed(2)  + '万';
  return n.toFixed(2) + '万';
}

function formatVolume(n) {
  if (n == null) return '--';
  if (n >= 1e8)  return (n / 1e8).toFixed(2)  + '亿手';
  if (n >= 1e4)  return (n / 1e4).toFixed(2)  + '万手';
  return n.toLocaleString() + '手';
}



function formatMarketCap(n) {
  if (n == null) return '--';
  if (n >= 1e12) return (n / 1e12).toFixed(2) + '万亿';
  if (n >= 1e8)  return (n / 1e8).toFixed(2)  + '亿';
  if (n >= 1e4)  return (n / 1e4).toFixed(2)  + '万';
  return n.toLocaleString();
}

/* ============================================================
   初始化
   ============================================================ */
document.addEventListener('DOMContentLoaded', async () => {
  loadIndices();
  loadSentiment();
  initKlineTabs();
  await loadWatchlist();

  const params = new URLSearchParams(window.location.search);
  const autoCode = params.get('stock');
  if (autoCode) {
    selectStock(autoCode);
  } else {
    // 自动选中第一支股票
    const firstCard = document.querySelector('.stock-card');
    if (firstCard) {
      const firstCode = firstCard.dataset.code;
      if (firstCode) selectStock(firstCode);
    }
  }

  // 从设置API同步自动刷新配置
  const toggle = document.getElementById('autoRefreshToggle');
  const intervalSel = document.getElementById('refreshInterval');
  try {
    const resp = await API.get('/api/settings');
    if (resp) {
      // 同步刷新间隔
      if (intervalSel && resp.refresh_interval) {
        intervalSel.value = resp.refresh_interval;
      }
      // 同步自动刷新开关
      if (toggle && resp.auto_refresh_enabled === 'true') {
        toggle.checked = true;
        startAutoRefresh();
      }
    }
  } catch(e) { console.warn('读取刷新设置失败:', e); }

  // 刷新控件绑定
  if (toggle) {
    toggle.addEventListener('change', () => {
      if (toggle.checked) startAutoRefresh();
      else stopAutoRefresh();
      // 保存设置到后端
      API.post('/api/settings/bulk', { settings: { auto_refresh_enabled: toggle.checked ? 'true' : 'false' } }).catch(()=>{});
    });
  }
  if (intervalSel) {
    intervalSel.addEventListener('change', () => {
      if (toggle && toggle.checked) {
        stopAutoRefresh();
        startAutoRefresh();
      }
      // 保存设置到后端
      API.post('/api/settings/bulk', { settings: { refresh_interval: intervalSel.value } }).catch(()=>{});
    });
  }

  // 启动 WebSocket 实时行情
  initWebSocket();
});

/* ============================================================
   WebSocket 实时行情推送
   ============================================================ */
let wsQuotes = null;
let wsReconnectTimer = null;
let wsConnected = false;

function initWebSocket() {
  if (wsQuotes) {
    try { wsQuotes.close(); } catch(_) {}
  }
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${proto}//${location.host}/ws/quotes`;
  wsQuotes = new WebSocket(url);

  wsQuotes.onopen = () => {
    wsConnected = true;
    console.log('[WS] 实时行情已连接');
    // WebSocket 连接成功后，停止 HTTP 轮询
    stopAutoRefresh();
  };

  wsQuotes.onmessage = (evt) => {
    try {
      const msg = JSON.parse(evt.data);
      if (msg.type === 'quotes' && msg.data) {
        updateCardsFromWS(msg.data);
      }
    } catch (_) {}
  };

  wsQuotes.onclose = () => {
    wsConnected = false;
    console.log('[WS] 连接断开，5秒后重连');
    clearTimeout(wsReconnectTimer);
    wsReconnectTimer = setTimeout(initWebSocket, 5000);
  };

  wsQuotes.onerror = () => {
    wsConnected = false;
    try { wsQuotes.close(); } catch(_) {}
  };
}

function updateCardsFromWS(quotesMap) {
  // 更新左侧卡片（不重绘 DOM）
  Object.entries(quotesMap).forEach(([code, q]) => {
    const el = document.querySelector(`.stock-card[data-code="${code}"]`);
    if (!el) return;

    const cls = priceClass(q.change_pct);
    const barCls = cls === 'up' ? 'up' : cls === 'down' ? 'down' : 'flat';
    const pctSign = q.change_pct != null ? (q.change_pct >= 0 ? '+' : '') : '';
    const chgSign = q.change != null ? (q.change >= 0 ? '+' : '') : '';

    const bar = el.querySelector('.stock-card-bar');
    if (bar) bar.className = `stock-card-bar ${barCls}`;

    const priceEl = el.querySelector('.sc-price');
    if (priceEl) {
      priceEl.innerHTML = formatPrice(q.price) + '<span class="sc-price-unit">元</span>';
      priceEl.className = `sc-price ${cls}`;
    }

    const pnlEl = el.querySelector('.sc-data-row:nth-child(1) .sc-data-val');
    if (pnlEl) {
      const v = q.daily_pnl || q.change;
      pnlEl.textContent = v != null ? formatPnl(v) : '--';
      pnlEl.className = `sc-data-val ${priceClass(v || 0)}`;
    }

    const pctEl = el.querySelector('.sc-data-row:nth-child(3) .sc-data-val');
    if (pctEl) {
      pctEl.textContent = `${pctSign}${q.change_pct != null ? q.change_pct.toFixed(2) : '--'}%`;
      pctEl.className = `sc-data-val ${cls}`;
    }
  });

  // 更新详情头部
  if (currentCode && quotesMap[currentCode]) {
    const q = quotesMap[currentCode];
    const priceEl = document.getElementById('d-price');
    const changeEl = document.getElementById('d-change');
    if (priceEl && q.price != null) {
      priceEl.textContent = formatPrice(q.price);
      priceEl.className = 'quote-price ' + priceClass(q.change_pct);
    }
    if (changeEl) {
      changeEl.textContent = `${formatPct(q.change_pct)}  ${q.change != null ? (q.change >= 0 ? '+' : '') + q.change.toFixed(2) : ''}`;
      changeEl.className = 'quote-change ' + priceClass(q.change_pct);
    }
    setText('d-open', q.open != null ? q.open.toFixed(2) : '--');
    setText('d-high', q.high != null ? q.high.toFixed(2) : '--');
    setText('d-low', q.low != null ? q.low.toFixed(2) : '--');
    setText('d-amount', formatAmount(q.amount));
    setText('d-volume', formatVolume(q.volume));
    setText('d-turnover', q.turnover != null ? q.turnover.toFixed(2) + '%' : '--');
  }
}

/* ============================================================
   刷新控制
   ============================================================ */
let autoRefreshTimer = null;

function startAutoRefresh() {
  stopAutoRefresh();
  const sel = document.getElementById('refreshInterval');
  const sec = parseInt(sel?.value) || 30;
  autoRefreshTimer = setInterval(() => {
    const now = new Date();
    const day = now.getDay(); // 0=Sun, 6=Sat
    if (day === 0 || day === 6) return; // 周末不刷新
    const h = now.getHours(), m = now.getMinutes();
    const t = h * 60 + m;
    // A股交易时段: 9:25-11:35, 12:55-15:05
    if ((t >= 565 && t <= 695) || (t >= 775 && t <= 905)) {
      refreshQuotes();
      loadIndices();
      if (currentCode) reloadKline();
    }
  }, sec * 1000);
}

function stopAutoRefresh() {
  if (autoRefreshTimer) {
    clearInterval(autoRefreshTimer);
    autoRefreshTimer = null;
  }
}

function manualRefresh() {
  refreshQuotes();
  loadIndices();
  if (currentCode) reloadKline();
  // 按钮反馈
  const btn = document.getElementById('btnRefreshNow');
  if (btn) {
    const orig = btn.textContent;
    btn.textContent = '刷新中…';
    btn.disabled = true;
    setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 800);
  }
}

/* ============================================================
   新闻功能
   ============================================================ */
function switchNewsTab(target) {
  document.querySelectorAll('.news-tab').forEach(t => t.classList.remove('active'));
  document.querySelector(`.news-tab[data-target="${target}"]`).classList.add('active');
  document.getElementById('newsStockList').style.display = target === 'stock' ? 'block' : 'none';
  document.getElementById('newsIndustryList').style.display = target === 'industry' ? 'block' : 'none';
  document.getElementById('newsGlobalList').style.display = target === 'global' ? 'block' : 'none';
  document.getElementById('newsClsList').style.display = target === 'cls' ? 'block' : 'none';
  document.getElementById('newsWechatList').style.display = target === 'wechat' ? 'block' : 'none';
  document.getElementById('newsSentimentBar').style.display = target === 'stock' ? 'flex' : 'none';
  if (target === 'industry' && currentCode) loadIndustryNews(currentCode);
  if (target === 'wechat' && currentCode) loadWechatNews(currentCode);
}

async function loadStockNews(code) {
  const container = document.getElementById('newsStockList');
  container.innerHTML = loadingState('加载中...');
  try {
    const [newsRes, sentRes] = await Promise.all([
      API.get(`/api/news/${code}`),
      API.get(`/api/news/sentiment/${code}`)
    ]);

    // 渲染情感统计
    const bar = document.getElementById('newsSentimentBar');
    if (sentRes && sentRes.total > 0) {
      bar.style.display = 'flex';
      document.getElementById('sentPos').textContent = `看多 ${sentRes.positive}`;
      document.getElementById('sentNeu').textContent = `中性 ${sentRes.neutral}`;
      document.getElementById('sentNeg').textContent = `看空 ${sentRes.negative}`;
    }

    const news = newsRes?.news || [];
    if (news.length === 0) {
      container.innerHTML = panelState('暂无相关新闻');
      return;
    }
    container.innerHTML = news.map(renderNewsItem).join('');
  } catch (e) {
    console.error('loadStockNews error:', e);
    container.innerHTML = panelState('加载失败');
  }
}

async function loadClsNews() {
  const container = document.getElementById('newsClsList');
  container.innerHTML = loadingState('加载中...');
  try {
    const data = await API.get('/api/news/cls');
    const news = data?.news || [];
    if (news.length === 0) {
      container.innerHTML = panelState('暂无快讯');
      return;
    }
    container.innerHTML = news.map(renderClsItem).join('');
  } catch (e) {
    console.error('loadClsNews error:', e);
    container.innerHTML = panelState('加载失败');
  }
}

async function loadWechatNews(code) {
  const container = document.getElementById('newsWechatList');
  container.innerHTML = loadingState('搜索公众号文章...');
  try {
    const data = await API.get(`/api/news/wechat/${code}`);
    const news = data?.news || [];
    if (news.length === 0) {
      container.innerHTML = `<div class="empty-state" style="padding:24px;"><p>未找到 "${escapeHtml(data?.keyword || code)}" 相关公众号文章</p></div>`;
      return;
    }
    container.innerHTML = `<div class="wechat-search-hint" style="padding:4px 8px;font-size:0.78rem;color:var(--text-secondary);">搜索: ${escapeHtml(data.keyword)}</div>` +
      news.map(renderWechatItem).join('');
  } catch (e) {
    console.error('loadWechatNews error:', e);
    container.innerHTML = panelState('加载失败');
  }
}

function renderWechatItem(n) {
  const href = safeUrl(n.url);
  const title = escapeHtml(n.title || '--');
  const url = href ? `<a href="${escapeAttr(href)}" target="_blank" rel="noopener">${title}</a>` : title;
  return `<div class="news-item">
    <div class="news-item-head">
      <span class="news-item-title">${url}</span>
      ${sentimentBadge(n.sentiment)}
    </div>
    ${n.summary ? `<div class="news-item-content">${escapeHtml(n.summary)}</div>` : ''}
    <div class="news-item-meta">
      <span>${n.source ? escapeHtml(n.source) : ''}</span>
      <span>${escapeHtml(n.date || '')}</span>
    </div>
  </div>`;
}

function sentimentBadge(s) {
  if (s === 'positive') return '<span class="sent-badge positive">利好</span>';
  if (s === 'negative') return '<span class="sent-badge negative">利空</span>';
  return '<span class="sent-badge neutral">中性</span>';
}

function renderNewsItem(n) {
  const href = safeUrl(n.url);
  const title = escapeHtml(n.title || '--');
  const url = href ? `<a href="${escapeAttr(href)}" target="_blank" rel="noopener">${title}</a>` : title;
  return `<div class="news-item">
    <div class="news-item-head">
      <span class="news-item-title">${url}</span>
      ${sentimentBadge(n.sentiment)}
    </div>
    <div class="news-item-meta">
      <span>${escapeHtml(n.media || '')}</span>
      <span>${escapeHtml(n.date || '')}</span>
    </div>
  </div>`;
}

function renderClsItem(n) {
  return `<div class="news-item cls-item">
    <div class="news-item-head">
      <span class="news-item-title">${escapeHtml(n.title || '--')}</span>
      ${sentimentBadge(n.sentiment)}
    </div>
    <div class="news-item-content">${escapeHtml(n.content || '')}</div>
    <div class="news-item-meta"><span>${escapeHtml(n.time || '')}</span></div>
  </div>`;
}

/* ============================================================
   全球资讯（7×24）
   ============================================================ */
async function loadIndustryNews(code) {
  const container = document.getElementById('newsIndustryList');
  container.innerHTML = loadingState('加载中...');
  try {
    const data = await API.get(`/api/news/${code}?category=industry`);
    const news = data?.news || [];
    if (news.length === 0) {
      container.innerHTML = panelState('暂无行业新闻');
      return;
    }
    container.innerHTML = news.map(renderNewsItem).join('');
  } catch (e) {
    console.error('loadIndustryNews error:', e);
    container.innerHTML = panelState('加载失败');
  }
}

async function loadGlobalNews() {
  const container = document.getElementById('newsGlobalList');
  container.innerHTML = loadingState('加载中...');
  try {
    const data = await API.get('/api/news/global');
    const news = data?.news || [];
    if (news.length === 0) {
      container.innerHTML = panelState('暂无全球资讯');
      return;
    }
    container.innerHTML = news.map(renderNewsItem).join('');
  } catch (e) {
    console.error('loadGlobalNews error:', e);
    container.innerHTML = panelState('加载失败');
  }
}

/* ============================================================
   买点管理
   ============================================================ */
async function loadBuyPoints(code) {
  if (!code) return;
  const tbody = document.getElementById('buyPointsBody');
  try {
    const data = await API.get(`/api/buy-points/${code}`);
    const points = data?.buy_points || [];
    if (points.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty-state"><p>暂无买点记录</p></td></tr>';
      return;
    }
    tbody.innerHTML = points.map(p => {
      const statusText = p.status === 'pending' ? '待执行' : p.status === 'triggered' ? '已触发' : '已取消';
      const statusClass = p.status === 'pending' ? 'up' : p.status === 'triggered' ? 'down' : '';
      return `<tr>
        <td style="font-family:var(--font-mono);font-weight:600;">${p.price.toFixed(2)}</td>
        <td>${p.shares || '--'}</td>
        <td>${escapeHtml(p.reason || '--')}</td>
        <td><span class="badge ${statusClass === 'up' ? 'badge-up' : statusClass === 'down' ? 'badge-down' : 'badge-info'}">${statusText}</span></td>
        <td style="font-size:0.8rem;color:var(--text-muted);">${escapeHtml((p.created_at || '').slice(0, 16))}</td>
        <td><button class="btn btn-sm btn-ghost" onclick="deleteBuyPoint(${p.id})">删除</button></td>
      </tr>`;
    }).join('');
  } catch (e) {
    console.error('loadBuyPoints error:', e);
    tbody.innerHTML = '<tr><td colspan="6" class="empty-state"><p>加载失败</p></td></tr>';
  }
}

function showAddBuyPoint() {
  document.getElementById('addBuyPointForm').style.display = 'block';
  document.getElementById('bpPrice').focus();
}

function hideAddBuyPoint() {
  document.getElementById('addBuyPointForm').style.display = 'none';
  document.getElementById('bpPrice').value = '';
  document.getElementById('bpShares').value = '';
  document.getElementById('bpReason').value = '';
}

async function submitBuyPoint() {
  if (!currentCode) { alert('请先选择股票'); return; }
  const price = parseFloat(document.getElementById('bpPrice').value);
  const shares = parseInt(document.getElementById('bpShares').value) || 0;
  const reason = document.getElementById('bpReason').value.trim();
  if (!price || price <= 0) { alert('请输入有效价格'); return; }
  try {
    await API.post(`/api/buy-points/${currentCode}`, { code: currentCode, price, shares, reason });
    hideAddBuyPoint();
    await loadBuyPoints(currentCode);
  } catch (e) {
    console.error('submitBuyPoint error:', e);
    alert('添加失败');
  }
}

async function deleteBuyPoint(id) {
  if (!confirm('确认删除此买点？')) return;
  try {
    await API.del(`/api/buy-points/${id}`);
    if (currentCode) await loadBuyPoints(currentCode);
  } catch (e) {
    console.error('deleteBuyPoint error:', e);
    alert('删除失败');
  }
}

/* ============================================================
   研报功能
   ============================================================ */
async function loadResearch(code) {
  const container = document.getElementById('researchList');
  if (!container) return;
  container.innerHTML = loadingState('加载研报中...');
  try {
    const data = await API.get(`/api/research/${code}`);
    const reports = data?.data?.reports || [];
    const eps = data?.data?.eps_forecast || {};

    // EPS 预测摘要
    let epsHtml = '';
    if (eps && (eps.eps_current_year || eps.analysts)) {
      epsHtml = `<div class="eps-forecast-card">
        <div class="eps-title">盈利预测</div>
        <div class="eps-row"><span class="label">今年EPS</span><span class="value">${eps.eps_current_year ?? '--'}</span></div>
        <div class="eps-row"><span class="label">今年PE</span><span class="value">${eps.pe_current_year ?? '--'}</span></div>
        <div class="eps-row"><span class="label">净利润</span><span class="value">${eps.net_profit ? (eps.net_profit / 1e8).toFixed(2) + '亿' : '--'}</span></div>
        <div class="eps-row"><span class="label">分析师数</span><span class="value">${eps.analysts ?? '--'}</span></div>
      </div>`;
    }

    if (reports.length === 0 && !epsHtml) {
      container.innerHTML = panelState('暂无研报数据');
      return;
    }

    const reportsHtml = reports.map(r => {
      const ratingBadge = r.rating ? `<span class="report-rating">${escapeHtml(r.rating)}</span>` : '';
      const targetPrice = r.target_price ? `<span class="report-target">目标价 ${escapeHtml(r.target_price)}</span>` : '';
      return `<div class="research-item" data-org="${escapeAttr(r.org || '')}" data-rating="${escapeAttr(r.rating || '')}">
        <div class="research-item-head">
          <span class="research-item-title">${escapeHtml(r.title || '--')}</span>
          ${ratingBadge}
        </div>
        <div class="research-item-meta">
          <span class="research-org">${escapeHtml(r.org || '')}</span>
          <span class="research-author">${escapeHtml(r.author || '')}</span>
          <span class="research-date">${escapeHtml(r.date || '')}</span>
          ${targetPrice}
        </div>
        ${r.content ? `<div class="research-item-content">${escapeHtml(r.content)}</div>` : ''}
      </div>`;
    }).join('');

    container.innerHTML = epsHtml + reportsHtml;

    // Populate org filter dropdown
    const orgSelect = document.getElementById('research-org-filter');
    if (orgSelect) {
      const orgs = [...new Set(reports.map(r => r.org).filter(Boolean))].sort();
      orgSelect.innerHTML = '<option value="">全部机构</option>' +
        orgs.map(o => `<option value="${escapeAttr(o)}">${escapeHtml(o)}</option>`).join('');
    }
  } catch (e) {
    console.error('loadResearch error:', e);
    container.innerHTML = panelState('加载失败');
  }
}

/* ============================================================
   研报筛选
   ============================================================ */
function filterResearch() {
  const org = document.getElementById('research-org-filter').value;
  const rating = document.getElementById('research-rating-filter').value;
  document.querySelectorAll('#researchList .research-item').forEach(el => {
    const elOrg = el.dataset.org || '';
    const elRating = el.dataset.rating || '';
    const showOrg = !org || elOrg.includes(org);
    const showRating = !rating || elRating.includes(rating);
    el.style.display = (showOrg && showRating) ? '' : 'none';
  });
}

/* ============================================================
   AI分析（自选股详情内联版）
   ============================================================ */
async function triggerAnalysis() {
  if (!currentCode) return alert('请先选择股票');
  const statusEl = document.getElementById('ai-analysis-status');
  statusEl.textContent = '正在启动分析...';
  try {
    const resp = await API.post(`/api/ai/analyze/${currentCode}`, {});
    if (resp.status === 'running') {
      statusEl.textContent = '该股票已有分析任务在运行';
      return;
    }
    const taskId = resp.task_id;
    statusEl.innerHTML = '分析中... <a href="/ai" style="color:var(--color-up);">查看完整进度</a>';
    const poll = async () => {
      try {
        const s = await API.get(`/api/ai/analyze/${taskId}/status`);
        if (s.status === 'completed') {
          const r = await API.get(`/api/ai/analyze/${taskId}/result`);
          const result = r.result || {};
          const elapsed = r.elapsed ? `${Math.floor(r.elapsed/60)}:${String(Math.floor(r.elapsed%60)).padStart(2,'0')}` : '';
          const mainReport = result.final_decision || result.risk_assessment || result.trader_decision || '暂无结果';
          statusEl.innerHTML = `
            <div class="card" style="margin-top:8px;padding:12px;">
              <div style="font-weight:600;margin-bottom:6px;">AI分析完成 ${elapsed ? '· 耗时 ' + elapsed : ''}</div>
              <div style="font-size:0.85rem;line-height:1.6;max-height:400px;overflow-y:auto;">${escapeHtml(mainReport).replace(/\n/g,'<br>').replace(/\*\*(.*?)\*\*/g,'<strong>$1</strong>')}</div>
              <div style="margin-top:8px;"><a href="/ai" class="btn btn-outline" style="font-size:0.8rem;">查看完整报告</a></div>
            </div>`;
          return;
        }
        if (s.status === 'failed') {
          statusEl.textContent = '分析失败: ' + (s.error || '未知错误');
          return;
        }
        setTimeout(poll, 3000);
      } catch(e) {
        statusEl.textContent = '查询状态失败';
      }
    };
    setTimeout(poll, 5000);
  } catch(e) {
    statusEl.textContent = '启动失败: ' + e.message;
  }
}

// ══════════════════════════════════════════════════════════════
// 异动检测（自选股详情 Tab）
// ══════════════════════════════════════════════════════════════

async function loadStockAnomalies(code) {
  const list = document.getElementById('anomalyStockList');
  const hint = document.getElementById('anomalyHint');
  if (!list) return;
  list.innerHTML = '<div class="anomaly-loading">加载中...</div>';
  try {
    const data = await API.get(`/api/ai/anomalies?code=${code}&limit=30`);
    const anomalies = data.anomalies || [];
    if (!anomalies.length) {
      list.innerHTML = `<div class="anomaly-empty">
        <div class="anomaly-empty-icon ui-glyph" data-icon="AL"></div>
        <div class="anomaly-empty-title">暂无异动记录</div>
        <div class="anomaly-empty-desc">点击上方按钮检测当前股票异动</div>
      </div>`;
      if (hint) hint.textContent = '无异动 · 可点击检测刷新';
      return;
    }
    if (hint) hint.textContent = `${anomalies.length} 条异动`;
    list.innerHTML = anomalies.map(a => renderAnomalyCard(a)).join('');
  } catch(e) {
    list.innerHTML = `<div class="empty-state" style="padding:24px;"><p>加载失败: ${escapeHtml(e.message)}</p></div>`;
  }
}

function renderAnomalyCard(a, isNew) {
  const level = ['critical', 'warning', 'info'].includes(a.level) ? a.level : 'info';
  const levelLabel = level === 'critical' ? '严重' : level === 'warning' ? '警告' : '提示';
  const name = escapeHtml(a.name || a.code || '');
  const timeStr = a.time ? escapeHtml(a.time.replace(/^\d{4}-/, '').replace(/:\d{2}$/, '')) : '';
  const changeHtml = a.change_pct ? `<span class="anomaly-change ${a.change_pct >= 0 ? 'up' : 'down'}">${a.change_pct >= 0 ? '+' : ''}${(a.change_pct||0).toFixed(2)}%</span>` : '';
  const priceHtml = a.price ? `<span class="anomaly-price">¥${a.price}</span>` : '';
  const typeLabel = {
    'volume_spike': '成交量异动',
    'price_surge': '价格急涨',
    'price_drop': '价格急跌',
    'large_buy': '大单买入',
    'large_sell': '大单卖出',
    'limit_up': '涨停',
    'limit_down': '跌停',
    'turnover_spike': '换手率异动',
    'gap_up': '跳空高开',
    'gap_down': '跳空低开'
  }[a.anomaly_type] || escapeHtml(a.anomaly_type || '');
  return `<div class="anomaly-card level-${level} ${isNew ? 'anomaly-new' : ''}">
    <div class="anomaly-card-header">
      <span class="anomaly-level-badge level-${level}">${levelLabel} · ${typeLabel}</span>
      <span class="anomaly-time">${timeStr}</span>
    </div>
    <div class="anomaly-card-body">
      <div class="anomaly-stock-info">
        <span class="anomaly-stock-name">${name}</span>
        <span class="anomaly-stock-code">${escapeHtml(a.code || '')}</span>
      </div>
      <div class="anomaly-price-info">
        ${priceHtml} ${changeHtml}
      </div>
    </div>
    ${a.message ? `<div class="anomaly-message">${escapeHtml(a.message)}</div>` : ''}
    ${a.l1_advice ? `<div class="anomaly-advice">建议：${escapeHtml(a.l1_advice)}</div>` : ''}
  </div>`;
}

async function triggerStockAnomaly() {
  if (!currentCode) return showToast('请先选择股票', 'error');
  const hint = document.getElementById('anomalyHint');
  if (hint) hint.textContent = '检测中...';
  try {
    const data = await API.post(`/api/ai/trigger/${currentCode}`, {});
    if (data.anomalies?.length) {
      if (hint) hint.textContent = `发现 ${data.anomalies.length} 条异动`;
      const list = document.getElementById('anomalyStockList');
      if (list) {
        list.innerHTML = data.anomalies.map(a => renderAnomalyCard(a, true)).join('');
      }
      showToast(`发现 ${data.anomalies.length} 条异动`, 'warning');
    } else {
      if (hint) hint.textContent = `无异动（检测了 ${data.checked} 只）`;
      showToast('无异动', 'success');
      loadStockAnomalies(currentCode);
    }
  } catch(e) {
    if (hint) hint.textContent = '检测失败';
    showToast('检测失败: ' + e.message, 'error');
  }
}

/* ============================================================
   公告加载
   ============================================================ */
let _announceData = [];

async function loadAnnounce(code) {
  const container = document.getElementById('announceList');
  if (!container) return;
  container.innerHTML = loadingState('加载中...');
  try {
    const resp = await API.get(`/api/announce/${code}?limit=50`);
    _announceData = resp?.data || [];
    filterAnnouncements();
  } catch(e) {
    container.innerHTML = panelState('加载失败');
  }
}

function filterAnnouncements() {
  const container = document.getElementById('announceList');
  if (!container) return;
  const year = document.getElementById('announce-year')?.value || '';
  const month = document.getElementById('announce-month')?.value || '';
  const typeF = document.getElementById('announce-type-filter')?.value || '';

  let filtered = _announceData;
  if (year) filtered = filtered.filter(a => a.date && a.date.startsWith(year));
  if (month) filtered = filtered.filter(a => a.date && a.date.substring(5,7) === month);
  if (typeF) filtered = filtered.filter(a => (a.type || '').includes(typeF));

  if (!filtered.length) {
    container.innerHTML = panelState('无匹配公告');
    return;
  }
  container.innerHTML = filtered.map(a => {
    const href = safeUrl(a.url);
    const title = escapeHtml(a.title || '--');
    const url = href ? `<a href="${escapeAttr(href)}" target="_blank" rel="noopener">${title}</a>` : title;
    const typeBadge = a.type ? `<span class="sent-badge neutral" style="margin-left:6px;">${escapeHtml(a.type.trim())}</span>` : '';
    return `<div class="announce-item">
      <div class="announce-item-head">
        <span class="announce-item-title">${url}${typeBadge}</span>
      </div>
      <div class="announce-item-meta"><span>${escapeHtml(a.date || '')}</span></div>
    </div>`;
  }).join('');
}
