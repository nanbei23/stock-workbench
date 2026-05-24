/**
 * chart.js — K-line chart using TradingView Lightweight Charts v4
 * Color scheme: A股惯例 红涨绿跌 + 治愈系暖灰
 */

const COLORS = {
  up: '#E07A5F',       /* 涨=红 */
  down: '#52B788',     /* 跌=绿 */
  volumeUp: 'rgba(224,122,95,0.35)',
  volumeDown: 'rgba(82,183,136,0.35)',
  background: '#FFFFFF',
  grid: 'rgba(0,0,0,0.04)',
  text: '#8C8C8C',
  macdDif: '#5B9BD5',
  macdDea: '#E07A5F',
  macdHistUp: 'rgba(224,122,95,0.5)',
  macdHistDown: 'rgba(82,183,136,0.5)',
  lineBlue: '#5B9BD5',
  lineMain: '#5B9BD5',
};

function isIntradayPeriod(period) {
  return ['m1', 'm5', '15', '30', '60'].includes(period);
}

function parseTime(dateStr, isIntraday) {
  if (!dateStr) return '';
  const s = String(dateStr).trim();
  if (isIntraday) {
    const iso = s.includes('T') ? s : s.replace(' ', 'T');
    const ms = new Date(iso).getTime();
    return isNaN(ms) ? s : Math.floor(ms / 1000);
  }
  return s.split(' ')[0];
}

function intradayTickFormatter(timestamp) {
  const d = new Date(timestamp * 1000);
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  return `${hh}:${mi}`;
}

/**
 * 将分时数据按昨收精确分段：红(>=昨收) / 绿(<昨收)
 * 关键：在线段穿越昨收时，用线性插值找到精确交叉点
 * LWC v4.2.0 LineSeries per-point color 只对 marker 有效，线段用 series 默认色
 * 所以用多段 series 叠加实现分时涨跌色
 */
function createColorSegments(chart, klineData, timeValues, refPrice) {
  const upColor = COLORS.up;
  const downColor = COLORS.down;
  const segments = [];
  let currentColor = null;
  let currentPoints = [];

  function flushSegment() {
    if (currentPoints.length > 0 && currentColor) {
      segments.push({ color: currentColor, points: [...currentPoints] });
      currentPoints = [];
    }
  }

  function pushPoint(pt) {
    currentPoints.push(pt);
  }

  for (let i = 0; i < klineData.length; i++) {
    const price = klineData[i].close;
    const point = { time: timeValues[i], value: price };
    const color = price >= refPrice ? upColor : downColor;

    if (i === 0) {
      // 第一个点：初始化
      currentColor = color;
      pushPoint(point);
      continue;
    }

    const prevPrice = klineData[i - 1].close;
    const crossed = (prevPrice >= refPrice && price < refPrice) ||
                    (prevPrice < refPrice && price >= refPrice);

    if (crossed) {
      // 线性插值：找到精确穿越时间
      const t0 = timeValues[i - 1];
      const t1 = timeValues[i];
      const ratio = Math.abs(refPrice - prevPrice) / Math.abs(price - prevPrice);
      const crossTime = Math.floor(t0 + ratio * (t1 - t0));
      const crossPoint = { time: crossTime, value: refPrice };

      // 结束当前段：追加到 refPrice
      pushPoint(crossPoint);
      flushSegment();

      // 开始新段：从 refPrice 开始，然后到当前点
      currentColor = price >= refPrice ? upColor : downColor;
      pushPoint(crossPoint);
      pushPoint(point);
    } else {
      // 未穿越：继续当前段
      pushPoint(point);
    }
  }

  // 最后一段
  flushSegment();

  // 创建多个 LineSeries
  const seriesList = [];
  segments.forEach((seg, idx) => {
    const isFirst = idx === 0;
    const s = chart.addLineSeries({
      color: seg.color,
      lineWidth: 2,
      crosshairMarkerVisible: isFirst,
      crosshairMarkerRadius: 3,
      lastValueVisible: isFirst,
      priceLineVisible: false,
    });
    s.setData(seg.points);
    seriesList.push(s);
  });
  return seriesList;
}

/**
 * Render a chart.
 * chartType: 'candlestick' | 'line' | 'intraday'
 */
function renderKline(containerId, klineData, options = {}) {
  const container = document.getElementById(containerId);
  if (!container) return null;
  container.innerHTML = '';

  const period = options.period || 'day';
  const isIntraday = isIntradayPeriod(period);
  let chartType = options.chartType || 'candlestick';
  if (isIntraday && chartType === 'candlestick') chartType = 'intraday';

  const timeValues = klineData.map(d => parseTime(d.date, isIntraday));
  const candles = klineData.map((d, i) => ({
    time: timeValues[i],
    open: d.open, high: d.high, low: d.low, close: d.close,
  }));
  const volumes = klineData.map((d, i) => ({
    time: timeValues[i],
    value: d.volume,
    color: d.close >= d.open ? COLORS.volumeUp : COLORS.volumeDown,
  }));

  const timeScaleOpts = {
    borderColor: COLORS.grid,
    timeVisible: true,
    secondsVisible: false,
    fixLeftEdge: true,
    fixRightEdge: true,
    tickMarkFormatter: isIntraday ? intradayTickFormatter : undefined,
  };

  const chartOpts = {
    width: container.clientWidth,
    height: container.clientHeight || 500,
    layout: { background: { type: 'solid', color: COLORS.background }, textColor: COLORS.text },
    grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: COLORS.grid },
    timeScale: timeScaleOpts,
  };
  // 分时图: crosshair 显示 HH:MM 格式时间
  if (isIntraday) {
    chartOpts.localization = {
      timeFormatter: (time) => {
        // time 对于分时图是 BusinessDay 对象或时间戳
        if (typeof time === 'number') {
          const d = new Date(time * 1000);
          const hh = String(d.getHours()).padStart(2, '0');
          const mm = String(d.getMinutes()).padStart(2, '0');
          return `${hh}:${mm}`;
        }
        // BusinessDay: { year, month, day }
        if (time && typeof time === 'object' && time.year) {
          return `${String(time.month).padStart(2,'0')}-${String(time.day).padStart(2,'0')}`;
        }
        return String(time);
      }
    };
  }
  const chart = LightweightCharts.createChart(container, chartOpts);
  window._klineChart = chart; // 暴露给调试用

  let mainSeries;
  let segmentSeriesList = [];

  if (chartType === 'intraday') {
    // === 分时图：多段折线实现涨跌色 ===
    const refPrice = options.refPrice || klineData[0]?.close || 0;

    // 涨跌色分段
    segmentSeriesList = createColorSegments(chart, klineData, timeValues, refPrice);
    mainSeries = segmentSeriesList[0]; // 第一个 series 用于价格线标注

    // 昨收参考线（灰色虚线）
    mainSeries.createPriceLine({
      price: refPrice,
      color: '#999',
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: '昨收',
    });

    // 确保Y轴范围包含昨收价（当昨收在数据范围外时）
    const prices = klineData.map(d => [d.open, d.high, d.low, d.close]).flat();
    const dataMin = Math.min(...prices);
    const dataMax = Math.max(...prices);
    if (refPrice < dataMin || refPrice > dataMax) {
      // 添加不可见参考点，强制 autoscaler 包含昨收
      const refSeries = chart.addLineSeries({
        color: 'transparent',
        lineWidth: 0,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      });
      // 仅需首尾两点即可撑开Y轴
      refSeries.setData([
        { time: timeValues[0], value: refPrice },
        { time: timeValues[timeValues.length - 1], value: refPrice },
      ]);
    }

    // 成交量颜色也按涨跌分
    volumes.forEach((v, i) => {
      v.color = klineData[i].close >= refPrice ? COLORS.volumeUp : COLORS.volumeDown;
    });

  } else if (chartType === 'line') {
    // === 五日折线图 ===
    mainSeries = chart.addLineSeries({
      color: COLORS.lineMain,
      lineWidth: 2,
      crosshairMarkerVisible: true,
      crosshairMarkerRadius: 3,
    });
    const lineData = klineData.map((d, i) => ({ time: timeValues[i], value: d.close }));
    mainSeries.setData(lineData);
  } else {
    // === 蜡烛图 ===
    mainSeries = chart.addCandlestickSeries({
      upColor: COLORS.up, downColor: COLORS.down,
      borderUpColor: COLORS.up, borderDownColor: COLORS.down,
      wickUpColor: COLORS.up, wickDownColor: COLORS.down,
    });
    mainSeries.setData(candles);
  }

  // Volume histogram
  const volumeSeries = chart.addHistogramSeries({
    color: COLORS.volumeUp, priceFormat: { type: 'volume' }, priceScaleId: 'vol',
  });
  chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
  volumeSeries.setData(volumes);

  // MACD（分时和五日不画）
  let macdSeries, deaSeries, histSeries;
  if (chartType === 'candlestick') {
    const macdData = calcMACD(klineData, isIntraday);
    macdSeries = chart.addLineSeries({
      color: COLORS.macdDif, lineWidth: 1, priceScaleId: 'macd',
      lastValueVisible: false, priceLineVisible: false,
    });
    deaSeries = chart.addLineSeries({
      color: COLORS.macdDea, lineWidth: 1, priceScaleId: 'macd',
      lastValueVisible: false, priceLineVisible: false,
    });
    histSeries = chart.addHistogramSeries({
      priceScaleId: 'macd', lastValueVisible: false, priceLineVisible: false,
    });
    chart.priceScale('macd').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
    macdSeries.setData(macdData.dif);
    deaSeries.setData(macdData.dea);
    histSeries.setData(macdData.histogram);
  }

  // 止损/止盈/加仓标线
  const lineForMarkers = segmentSeriesList.length > 0 ? segmentSeriesList[segmentSeriesList.length - 1] : mainSeries;
  if (options.stop_loss_price != null) {
    lineForMarkers.createPriceLine({
      price: options.stop_loss_price, color: COLORS.down, lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: '止损',
    });
  }
  if (options.target_sell_price != null) {
    lineForMarkers.createPriceLine({
      price: options.target_sell_price, color: COLORS.up, lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: '目标价',
    });
  }
  if (options.buy_prices && Array.isArray(options.buy_prices)) {
    options.buy_prices.forEach((bp, idx) => {
      if (bp != null && bp > 0) {
        lineForMarkers.createPriceLine({
          price: bp, color: COLORS.lineBlue, lineWidth: 1,
          lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true,
          title: `加仓${idx + 1}`,
        });
      }
    });
  }

  // Responsive
  const ro = new ResizeObserver(() => {
    chart.applyOptions({ width: container.clientWidth, height: container.clientHeight || 500 });
  });
  ro.observe(container);

  container.__lwc_chart = chart;
  chart.timeScale().fitContent();

  // 分时图/五日: 强制时间轴范围 = 09:30~15:00（完整交易时段）
  if (isIntraday && klineData.length > 0) {
    const firstDate = klineData[0].date; // "2026-05-22 09:31"
    const dateStr = firstDate.split(' ')[0]; // "2026-05-22"
    const sessionStart = Math.floor(new Date(dateStr + 'T09:30').getTime() / 1000);
    const sessionEnd = Math.floor(new Date(dateStr + 'T15:00').getTime() / 1000);
    chart.timeScale().setVisibleRange({ from: sessionStart, to: sessionEnd });
  }
  const result = { chart, mainSeries, segmentSeriesList, volumeSeries, macdSeries, deaSeries, histSeries, chartType, _refPrice: options.refPrice };
  return result;
}

function captureChart() {
    const chartContainer = document.getElementById('chartContainer');
    if (!chartContainer || !chartContainer.__lwc_chart) return null;
    try {
        const chart = chartContainer.__lwc_chart;
        const screenshot = chart.takeScreenshot();
        return screenshot ? screenshot.toDataURL('image/png') : null;
    } catch(e) {
        console.warn('Chart capture failed:', e);
        return null;
    }
}

function updateKline(result, newData, period) {
  if (!result || !newData) return;
  const isIntraday = isIntradayPeriod(period || 'day');
  const timeValues = newData.map(d => parseTime(d.date, isIntraday));

  if (result.chartType === 'intraday') {
    // 分时图更新：删除旧的分段 series，重新创建
    if (result.segmentSeriesList) {
      result.segmentSeriesList.forEach(s => {
        try { result.chart.removeSeries(s); } catch(_) {}
      });
    }
    const refPrice = result._refPrice || newData[0]?.close || 0;
    result.segmentSeriesList = createColorSegments(result.chart, newData, timeValues, refPrice);
    result.mainSeries = result.segmentSeriesList[0];
    const volumes = newData.map((d, i) => ({
      time: timeValues[i], value: d.volume,
      color: d.close >= refPrice ? COLORS.volumeUp : COLORS.volumeDown,
    }));
    result.volumeSeries.setData(volumes);
  } else if (result.chartType === 'line') {
    const lineData = newData.map((d, i) => ({ time: timeValues[i], value: d.close }));
    result.mainSeries.setData(lineData);
    const volumes = newData.map((d, i) => ({
      time: timeValues[i], value: d.volume,
      color: d.close >= d.open ? COLORS.volumeUp : COLORS.volumeDown,
    }));
    result.volumeSeries.setData(volumes);
  } else {
    const candles = newData.map((d, i) => ({ time: timeValues[i], open: d.open, high: d.high, low: d.low, close: d.close }));
    result.mainSeries.setData(candles);
    const volumes = newData.map((d, i) => ({
      time: timeValues[i], value: d.volume,
      color: d.close >= d.open ? COLORS.volumeUp : COLORS.volumeDown,
    }));
    result.volumeSeries.setData(volumes);
    const macd = calcMACD(newData, isIntraday);
    if (result.macdSeries) result.macdSeries.setData(macd.dif);
    if (result.deaSeries) result.deaSeries.setData(macd.dea);
    if (result.histSeries) result.histSeries.setData(macd.histogram);
  }
}

function calcMACD(klineData, isIntraday, fast = 12, slow = 26, signal = 9) {
  const closes = klineData.map(d => d.close);
  const dates = klineData.map(d => parseTime(d.date, isIntraday));
  if (closes.length < slow) return { dif: [], dea: [], histogram: [] };

  const emaFast = ema(closes, fast);
  const emaSlow = ema(closes, slow);
  const difRaw = emaFast.map((v, i) => v - emaSlow[i]);
  const deaRaw = ema(difRaw, signal);

  const dif = [], dea = [], histogram = [];
  for (let i = slow - 1; i < closes.length; i++) {
    const d = dates[i];
    dif.push({ time: d, value: rd(difRaw[i]) });
    dea.push({ time: d, value: rd(deaRaw[i]) });
    const v = rd((difRaw[i] - deaRaw[i]) * 2);
    histogram.push({ time: d, value: v, color: v >= 0 ? COLORS.macdHistUp : COLORS.macdHistDown });
  }
  return { dif, dea, histogram };
}

function ema(data, period) {
  const k = 2 / (period + 1);
  const result = [data[0]];
  for (let i = 1; i < data.length; i++) result.push(data[i] * k + result[i - 1] * (1 - k));
  return result;
}

function rd(v) { return Math.round(v * 100) / 100; }
