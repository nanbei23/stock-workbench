# 🎨 皮肤规范全集 — 新皮肤开发参考

> **用途**：创建第三套皮肤「阳光户外」时的完整参考文档
> **基准**：以赛博(cyberpunk)和午夜(midnight)两套现有皮肤为蓝本
> **更新日期**：2026-05-24

---

## 一、架构概览

```
style.css          ← 基础样式 + CSS 变量定义（:root 默认值）
cyberpunk.css      ← 赛博主题覆盖（[data-theme="cyberpunk"]）
midnight.css       ← 午夜主题覆盖（[data-theme="midnight"]）
sunny.css          ← 阳光户外主题覆盖（[data-theme="sunny"]）← 你要做的
SKIN_GUIDE.md      ← 旧版指南（已被本文档取代）
SKIN_SPEC.md       ← 本文件（完整规范）
```

**主题切换机制**：
- `document.body.setAttribute('data-theme', id)` 切换
- `localStorage.setItem('theme', id)` 持久化
- 默认主题：`cyberpunk`
- 旧主题 `classic` 已废弃，读到后自动回退到 `cyberpunk`

**注册新皮肤**：在 `templates/base.html` 的 `THEMES` 数组中添加：
```js
const THEMES = [
    { id: 'cyberpunk', name: '赛博', icon: '🌆' },
    { id: 'midnight',  name: '午夜', icon: '🌙' },
    { id: 'sunny',     name: '阳光', icon: '☀️' },  // ← 新增
];
```

---

## 二、必须定义的 CSS 变量（Design Tokens）

每个主题文件**必须**在 `[data-theme="xxx"]` 下重新定义这些变量：

```css
[data-theme="xxx"] {
    /* ── 涨跌色（A股标准：红涨绿跌）── */
    --color-up:       #???(红);   /* 涨 */
    --color-down:     #???(绿);   /* 跌 */

    /* ── 背景 ── */
    --bg:             #???;       /* 主背景 */
    --bg-card:        #???;       /* 卡片/面板背景 */
    --bg-hover:       #???;       /* 悬停背景 */
    --bg-sidebar:     #???;       /* 左侧边栏 */

    /* ── 文字 ── */
    --text-primary:   #???;       /* 主文字 */
    --text-secondary: #???;       /* 次文字/标签 */
    --text-muted:     #???;       /* 弱文字/禁用 */

    /* ── 强调色 ── */
    --color-accent:   #???;       /* 主强调色（链接/活跃） */
    --color-accent-2: #???;       /* 副强调色（色条渐变） */
    --color-warn:     #???;       /* 警告色 */
    --color-primary:  #???;       /* 主色调（按钮等） */

    /* ── 渐变 ── */
    --gradient-warm:  linear-gradient(135deg, ???, ???);
    --gradient-cool:  linear-gradient(135deg, ???, ???);

    /* ── 阴影 ── */
    --shadow-card:    ???;        /* 卡片阴影（午夜主题为 none） */
    --shadow-hover:   ???;        /* 悬停阴影 */
    --border-light:   ???;        /* 浅色边框 */

    /* ── 字体 ── */
    --font-sans:  'Inter', 'PingFang SC', 'Noto Sans SC', -apple-system, sans-serif;
    --font-mono:  'JetBrains Mono', 'SF Mono', monospace;
}
```

**现有两套主题参考值**：

| 变量 | 赛博 cyberpunk | 午夜 midnight |
|------|---------------|--------------|
| --color-up | `#FF4D6A` (粉红) | `#EF4444` (标准红) |
| --color-down | `#00D4A1` (翡翠绿) | `#22C55E` (标准绿) |
| --bg | `#0D0D1A` | `#121212` |
| --bg-card | `#1A1A2E` | `#1E1E1E` |
| --bg-hover | `#252540` | `#2A2A2A` |
| --bg-sidebar | `#0F0F1E` | `#0A0A0A` |
| --text-primary | `#E8E8FF` | `#FFFFFF` |
| --text-secondary | `#8888AA` | `#9CA3AF` |
| --text-muted | `#4A4A6A` | `#6B7280` |
| --color-accent | `#7B61FF` | `#3B82F6` |
| --color-accent-2 | `#4EA8DE` | `#A3E635` |
| --color-warn | `#FF4D6A` | `#F59E0B` |
| --color-primary | `#4EA8DE` | `#F59E0B` |
| --shadow-card | `0 4px 12px rgba(0,0,0,0.18)` | `none` |
| --shadow-hover | `0 8px 24px rgba(0,0,0,0.25)` | `0 4px 12px rgba(0,0,0,0.4)` |
| --border-light | `rgba(123,97,255,0.15)` | `rgba(255,255,255,0.06)` |

---

## 三、涨跌颜色规则（最重要！）

### 3.1 A股标准

**涨 = 红色，跌 = 绿色**（与国际惯例相反！）

```
--color-up   → 红色系
--color-down → 绿色系
```

### 3.2 基础类（style.css 定义，所有主题通用）

```css
/* style.css L42-45 */
.up   { color: var(--color-up) !important; }
.down { color: var(--color-down) !important; }
.flat { color: var(--text-secondary); }
```

### 3.3 ⚠️ specificity 陷阱（踩过坑！）

**问题**：如果主题 CSS 写了这种规则：

```css
/* ❌ 错误！这会覆盖 .up/.down 的颜色 */
[data-theme="cyberpunk"] .pnl-value {
    color: var(--text-primary);  /* specificity: 0,2,0 > .up 的 0,1,0 */
}
```

结果：`.pnl-value.up` 的 color 被覆盖，涨跌颜色全部失效！

**正确做法**：

```css
/* ✅ 方法1：不设置 color（推荐） */
[data-theme="xxx"] .pnl-value {
    /* 只设置其他属性，不设 color */
    font-family: var(--font-mono);
}

/* ✅ 方法2：.up/.down 已用 !important，不需要额外处理 */
```

### 3.4 所有使用涨跌颜色的元素

| 元素/类 | 用途 | 来源 |
|---------|------|------|
| `.up / .down / .flat` | 通用涨跌文字 | style.css L42-45 |
| `.pnl-value` | 盈亏数字（概览、卡片） | 通过 .up/.down 类控制 |
| `.calendar-day-pnl.up/.down` | 日历盈亏数字 | style.css |
| `.stock-card-bar.up/.down` | 自选股卡片色条 | style.css |
| `.signal-buy / .signal-sell` | 信号标签 | style.css |
| `.badge-up / .badge-down` | 徽章 | style.css |
| `.data-badge.up / .down` | 数据徽章 | style.css |
| `.order-status.up / .down` | 订单状态 | style.css |

### 3.5 情感标签颜色方向（新！）

**A股看多=红色，看空=绿色**，与涨跌一致：

```css
/* style.css 中定义 */
.sent-badge.positive,
.sentiment-tag.positive {
  color: var(--color-up);      /* 看多=红 */
  background: rgba(..., 0.10);
}
.sent-badge.negative,
.sentiment-tag.negative {
  color: var(--color-down);    /* 看空=绿 */
  background: rgba(..., 0.10);
}
```

---

## 四、按钮统一样式体系

### 4.1 按钮类名总览

| 类名 | 用途 | 样式 |
|------|------|------|
| `.btn` | 基础按钮 | padding 8px 16px, border-radius 8px |
| `.btn-sm` | 小按钮 | padding 4px 12px, font-size 0.85rem |
| `.btn-primary` | 实心强调按钮 | accent 色背景，白色文字 |
| `.btn-ghost` | 边框透明按钮 | 边框+透明背景 |
| `.btn-secondary` | 边框卡片底色按钮 | 边框+卡片背景色 |
| `.btn-save` | 保存按钮 | accent-2 色背景，大号 |
| `.btn-test` | 测试按钮 | 边框透明，小号 |
| `.btn-icon` | 圆形图标按钮 | 32px圆形，透明背景 |
| `.pwd-toggle` | 密码显示切换 | 同 btn-icon 样式 |
| `.btn-danger` | 危险按钮 | 红色背景 |
| `.btn-cancel` | 取消按钮 | 次要背景色 |
| `.btn-refresh-now` | 立即刷新按钮 | accent-2 色背景 |
| `.btn-remove` | 删除按钮 | 红色背景 |

### 4.2 主题必须覆盖的按钮

每个主题 CSS 必须覆盖以下按钮类：

```css
/* ── 按钮：实心 ── */
[data-theme="xxx"] .btn-primary { background: ???; color: ???; }
[data-theme="xxx"] .btn-primary:hover { background: ???; box-shadow: ???; }
[data-theme="xxx"] .btn-save { background: ???; color: ???; }
[data-theme="xxx"] .btn-save:hover { background: ???; }
[data-theme="xxx"] .btn-refresh-now { background: ???; color: ???; }
[data-theme="xxx"] .btn-refresh-now:hover { background: ???; }
[data-theme="xxx"] .btn-danger { background: ???; box-shadow: none; }
[data-theme="xxx"] .btn-danger:hover { background: ???; box-shadow: ???; }

/* ── 按钮：边框 ── */
[data-theme="xxx"] .btn-ghost { border-color: ???; color: ???; }
[data-theme="xxx"] .btn-ghost:hover { border-color: ???; color: ???; background: transparent; }
[data-theme="xxx"] .btn-secondary { border-color: ???; background: ???; }
[data-theme="xxx"] .btn-secondary:hover { border-color: ???; color: ???; }
[data-theme="xxx"] .btn-test { border-color: ???; background: transparent; }
[data-theme="xxx"] .btn-test:hover { border-color: ???; color: ???; }
[data-theme="xxx"] .btn-cancel { background: ???; border-color: ???; color: ???; }
[data-theme="xxx"] .btn-cancel:hover { border-color: ???; color: ???; }

/* ── 按钮：图标 ── */
[data-theme="xxx"] .btn-icon:hover { background: ???; }
[data-theme="xxx"] .pwd-toggle:hover { background: ???; }
[data-theme="xxx"] .btn-remove { background: ???; box-shadow: none; }
[data-theme="xxx"] .btn-remove:hover { background: ???; box-shadow: ???; }
```

---

## 五、完整组件覆盖清单

以下是**每个主题必须覆盖的全部组件**，按区域分组：

### 5.1 全局 & Banner

```css
[data-theme="xxx"] body { background: ???; color: ???; }
[data-theme="xxx"] .banner { background: ???; border-bottom: ???; box-shadow: ???; }
[data-theme="xxx"] .logo { color: ???; }
```

### 5.2 导航Tab

```css
[data-theme="xxx"] .tab-btn { color: ???; border-radius: 8px; }
[data-theme="xxx"] .tab-btn:hover { color: ???; background: ???; }
[data-theme="xxx"] .tab-btn.active { color: ???; background: ???; font-weight: 600; }
```

### 5.3 股票列表 & 搜索

```css
[data-theme="xxx"] .stock-list { background: ???; border-right: ???; }
[data-theme="xxx"] .stock-search input { background: ???; border-color: ???; color: ???; }
[data-theme="xxx"] .stock-search input:focus { border-color: ???; box-shadow: ???; }
```

### 5.4 股票卡片

```css
[data-theme="xxx"] .stock-card { background: ???; border: ???; box-shadow: ???; }
[data-theme="xxx"] .stock-card:hover { background: ???; box-shadow: ???; transform: ???; }
[data-theme="xxx"] .stock-card.active { background: ???; border-left: ???; box-shadow: ???; }
[data-theme="xxx"] .sc-grip { background: ???; border-right: ???; color: ???; }
[data-theme="xxx"] .sc-name { color: ???; }
[data-theme="xxx"] .sc-code { color: ???; }
[data-theme="xxx"] .sc-data-lbl { color: ???; }
[data-theme="xxx"] .sc-data-val { color: ???; }

/* ⚠️ .stock-card-bar 的 background 由 .up/.down 类控制，不要在主题中覆盖 */
```

### 5.5 大盘指数条

```css
[data-theme="xxx"] .index-bar,
[data-theme="xxx"] .indices-bar { background: ???; border-bottom-color: ???; }
[data-theme="xxx"] .idx-name,
[data-theme="xxx"] .index-name { color: ???; }
[data-theme="xxx"] .idx-value,
[data-theme="xxx"] .index-price { color: ???; }
```

### 5.6 详情Tab

```css
[data-theme="xxx"] .detail-tabs { background: ???; }
[data-theme="xxx"] .detail-tab { color: ???; }
[data-theme="xxx"] .detail-tab.active { background: ???; color: ???; }
[data-theme="xxx"] .detail-tab:hover:not(.active) { color: ???; background: ???; }
```

### 5.7 新闻/研报子Tab

```css
[data-theme="xxx"] .news-tab { border-color: ???; color: ???; }
[data-theme="xxx"] .news-tab:hover { border-color: ???; color: ???; }
[data-theme="xxx"] .news-tab.active { background: ???; border-color: ???; color: ???; }
```

### 5.8 卡片 & 表格

```css
[data-theme="xxx"] .card { background: ???; box-shadow: ???; }
[data-theme="xxx"] .card:hover { box-shadow: ???; }
[data-theme="xxx"] .card-title { color: ???; }
[data-theme="xxx"] .data-table th { color: ???; border-bottom-color: ???; }
[data-theme="xxx"] .data-table td { border-bottom-color: ???; }
[data-theme="xxx"] .data-table tr:hover td { background: ???; }
[data-theme="xxx"] .suggestions-table th { color: ???; border-bottom-color: ???; }
[data-theme="xxx"] .suggestions-table td { border-bottom-color: ???; }
[data-theme="xxx"] .suggestions-table tr:hover { background: ???; }
```

### 5.9 Badge（涨跌、警告、信息）

```css
[data-theme="xxx"] .badge-up { background: rgba(涨色r,g,b, 0.12); color: var(--color-up); }
[data-theme="xxx"] .badge-down { background: rgba(跌色r,g,b, 0.12); color: var(--color-down); }
[data-theme="xxx"] .badge-warn { background: rgba(警告色r,g,b, 0.12); color: ???; }
[data-theme="xxx"] .badge-info { background: rgba(强调色r,g,b, 0.10); color: ???; }
```

### 5.10 情感Badge

```css
[data-theme="xxx"] .sent-badge.positive { background: rgba(涨色r,g,b, 0.10); color: var(--color-up); }
[data-theme="xxx"] .sent-badge.negative { background: rgba(跌色r,g,b, 0.10); color: var(--color-down); }
[data-theme="xxx"] .sent-badge.neutral { background: ???; color: ???; }
```

### 5.11 信号标签

```css
[data-theme="xxx"] .signal-buy { color: var(--color-up); }
[data-theme="xxx"] .signal-sell { color: var(--color-down); }
[data-theme="xxx"] .signal-hold { color: ???; }
[data-theme="xxx"] .signal-buy,
[data-theme="xxx"] .report-signal.signal-buy { background: rgba(涨色r,g,b, 0.10); }
[data-theme="xxx"] .signal-sell,
[data-theme="xxx"] .report-signal.signal-sell { background: rgba(跌色r,g,b, 0.10); }
[data-theme="xxx"] .signal-hold,
[data-theme="xxx"] .report-signal.signal-hold { background: rgba(强调色r,g,b, 0.10); }
```

### 5.12 Alert 告警

```css
[data-theme="xxx"] .alert-warn { background: ???; color: ???; border-left: ???; }
[data-theme="xxx"] .alert-error { background: ???; color: ???; border-left: ???; }
[data-theme="xxx"] .alert-info { background: ???; color: ???; border-left: ???; }
```

### 5.13 进度条

```css
[data-theme="xxx"] .progress-bar { background: ???; }
[data-theme="xxx"] .progress-fill { background: ???; }
[data-theme="xxx"] .progress-bar-container { background: ???; }
```

### 5.14 研报评级 & 公告类型

```css
[data-theme="xxx"] .report-rating { background: ???; color: ???; }
[data-theme="xxx"] .announce-type { background: ???; color: ???; }
```

### 5.15 新闻/研报/公告列表项

```css
[data-theme="xxx"] .news-item,
[data-theme="xxx"] .research-item,
[data-theme="xxx"] .announce-item { background: ???; box-shadow: ???; }

[data-theme="xxx"] .news-item:hover,
[data-theme="xxx"] .research-item:hover,
[data-theme="xxx"] .announce-item:hover { background: ???; box-shadow: ???; }

[data-theme="xxx"] .news-item-title a,
[data-theme="xxx"] .announce-item-title a { color: ???; }
[data-theme="xxx"] .news-item-title a:hover,
[data-theme="xxx"] .announce-item-title a:hover { color: ???; }
```

### 5.16 信息网格

```css
[data-theme="xxx"] .info-label { color: ???; }
[data-theme="xxx"] .info-value { color: ???; }
```

### 5.17 K线Tab

```css
[data-theme="xxx"] .chart-tab:hover { color: ???; background: ???; }
[data-theme="xxx"] .chart-tab.active { color: ???; background: ???; border-bottom-color: ???; }
```

### 5.18 盈亏 & 日历

```css
[data-theme="xxx"] .pnl-label { color: ???; }
[data-theme="xxx"] .calendar-header button { border-color: ???; color: ???; }
[data-theme="xxx"] .calendar-header button:hover { background: ???; color: ???; }
[data-theme="xxx"] .calendar-stats { color: ???; }
[data-theme="xxx"] .calendar-day-header { color: ???; }
[data-theme="xxx"] .calendar-day-num { color: ???; }
```

### 5.19 Modal 弹窗

```css
[data-theme="xxx"] .modal-overlay { background: ???; }
[data-theme="xxx"] .modal-box { background: ???; border: ???; box-shadow: ???; }
[data-theme="xxx"] .modal-title { color: ???; }
[data-theme="xxx"] .modal-body { color: ???; }
[data-theme="xxx"] .modal-body strong { color: ???; }
```

### 5.20 表单控件

```css
[data-theme="xxx"] .form-control,
[data-theme="xxx"] .form-select,
[data-theme="xxx"] .form-input-sm,
[data-theme="xxx"] .form-group input,
[data-theme="xxx"] .form-group select {
  background: ???; border-color: ???; color: ???;
}

[data-theme="xxx"] .form-control:focus,
[data-theme="xxx"] .form-select:focus,
[data-theme="xxx"] .form-input-sm:focus,
[data-theme="xxx"] .form-group input:focus,
[data-theme="xxx"] .form-group select:focus {
  border-color: ???; box-shadow: ???;
}

[data-theme="xxx"] select.form-control {
  background-image: url("data:image/svg+xml,...");  /* 自定义下拉箭头 */
}
```

### 5.21 条件单 & 筛选栏

```css
[data-theme="xxx"] .order-card { background: ???; border-color: ???; }
[data-theme="xxx"] .order-code { color: ???; }
[data-theme="xxx"] .order-body { color: ???; }
[data-theme="xxx"] .order-meta { color: ???; }
[data-theme="xxx"] .filter-bar > span { color: ???; }
```

### 5.22 EPS 预测 & 七层数据

```css
[data-theme="xxx"] .eps-forecast-card { background: ???; }
[data-theme="xxx"] .layer-card { background: ???; box-shadow: ???; }
[data-theme="xxx"] .layer-card .layer-title { color: ???; border-bottom-color: ???; }
[data-theme="xxx"] .layer-row .label { color: ???; }
[data-theme="xxx"] .layer-row .value { color: ???; }
```

### 5.23 状态Badge

```css
[data-theme="xxx"] .status-danger { background: ???; color: ???; }
[data-theme="xxx"] .status-warning { background: ???; color: ???; }
[data-theme="xxx"] .status-info { background: ???; color: ???; }
[data-theme="xxx"] .status-default { background: ???; color: ???; }
```

### 5.24 AI分析台

```css
[data-theme="xxx"] .ai-left { background: ???; border-right-color: ???; }
[data-theme="xxx"] .ai-right { background: ???; border-left-color: ???; }
[data-theme="xxx"] .ai-container > .queue-panel { background: ???; border-bottom-color: ???; color: ???; }
[data-theme="xxx"] .ai-toggle-left,
[data-theme="xxx"] .ai-toggle-right { background: ???; border-color: ???; box-shadow: ???; }
[data-theme="xxx"] .ai-toggle-left:hover,
[data-theme="xxx"] .ai-toggle-right:hover { background: ???; color: ???; }
[data-theme="xxx"] .ai-section { background: ???; box-shadow: ???; }
[data-theme="xxx"] .section-header h3 { color: ???; }
[data-theme="xxx"] .update-time { color: ???; }
[data-theme="xxx"] .analysis-progress { background: ???; }
[data-theme="xxx"] .stage-indicator { background: ???; }
[data-theme="xxx"] .stage-name { color: ???; }
[data-theme="xxx"] .stage-dot.running { color: ???; }
[data-theme="xxx"] .stage-dot.completed { color: ???; }
```

### 5.25 分析结果

```css
[data-theme="xxx"] .result-section { background: ???; }
[data-theme="xxx"] .result-section h4 { color: ???; }
[data-theme="xxx"] .advice-item { background: ???; }
[data-theme="xxx"] .advice-label { color: ???; }
[data-theme="xxx"] .advice-value { color: ???; }
[data-theme="xxx"] .advice-reasoning { color: ???; }
```

### 5.26 风控Tab

```css
[data-theme="xxx"] .risk-tab { border-color: ???; background: ???; color: ???; }
[data-theme="xxx"] .risk-tab:hover { border-color: ???; color: ???; }
[data-theme="xxx"] .risk-tab.active { background: ???; color: ???; border-color: ???; }
[data-theme="xxx"] .risk-content { background: ???; color: ???; }
```

### 5.27 异动日志

```css
[data-theme="xxx"] .anomaly-item { background: ???; }
[data-theme="xxx"] .anomaly-empty { color: ???; }
[data-theme="xxx"] .anomaly-stock { color: ???; }
[data-theme="xxx"] .anomaly-change { color: ???; }
[data-theme="xxx"] .anomaly-message { color: ???; }
[data-theme="xxx"] .anomaly-advice { color: ???; }
[data-theme="xxx"] .anomaly-time { color: ???; }
```

### 5.28 历史报告

```css
[data-theme="xxx"] .report-item { background: ???; }
[data-theme="xxx"] .report-item:hover { background: ???; }
[data-theme="xxx"] .report-code { color: ???; }
[data-theme="xxx"] .report-time { color: ???; }
[data-theme="xxx"] .report-item-meta { color: ???; }
[data-theme="xxx"] .report-expander { background: ???; border-color: ???; }
[data-theme="xxx"] .report-expander summary { background: ???; color: ???; }
[data-theme="xxx"] .report-expander summary:hover { background: ???; }
[data-theme="xxx"] .report-content { color: ???; }
[data-theme="xxx"] .report-content h3,
[data-theme="xxx"] .report-content h4,
[data-theme="xxx"] .report-content h5 { color: ???; }
[data-theme="xxx"] .report-content strong { color: ???; }
[data-theme="xxx"] .report-content code { background: ???; color: ???; }
[data-theme="xxx"] .report-content hr { border-top-color: ???; }
[data-theme="xxx"] .report-chars { color: ???; }
```

### 5.29 信号Banner

```css
[data-theme="xxx"] .signal-banner { border: ???; background: ???; }
```

### 5.30 持仓页 & 设置页

```css
[data-theme="xxx"] .portfolio-container { color: ???; }
[data-theme="xxx"] .settings-container { color: ???; }
```

### 5.31 空状态

```css
[data-theme="xxx"] .empty-state { color: ???; }
```

### 5.32 滚动条

```css
[data-theme="xxx"] ::-webkit-scrollbar-track { background: ???; }
[data-theme="xxx"] ::-webkit-scrollbar-thumb { background: ???; border-radius: 3px; }
[data-theme="xxx"] ::-webkit-scrollbar-thumb:hover { background: ???; }
```

### 5.33 通知Badge

```css
[data-theme="xxx"] .notify-badge { background: ???; color: ???; box-shadow: ???; }
[data-theme="xxx"] .notify-badge span { background: ???; color: ???; }
```

### 5.34 账户切换器

```css
[data-theme="xxx"] #accountSwitcher { background: ???; color: ???; border-color: ???; }
```

### 5.35 移动端底部Tab

```css
[data-theme="xxx"] .mobile-tab-bar { background: ???; border-top: ???; }
[data-theme="xxx"] .mobile-tab-item { color: ???; }
[data-theme="xxx"] .mobile-tab-item.active { color: ???; }
[data-theme="xxx"] .mobile-tab-item:hover { color: ???; }
```

### 5.36 换肤按钮

```css
[data-theme="xxx"] .theme-switcher { background: ???; border-color: ???; color: ???; }
[data-theme="xxx"] .theme-switcher:hover { background: ???; border-color: ???; color: ???; }
```

---

## 六、盈亏日历背景色（JS 动态生成）

日历格子的背景色由 JS 动态生成，**必须读 CSS 变量**：

```js
// ✅ 正确：从 CSS 变量读取颜色（每次渲染时重新读取）
function renderCalendar() {
    const root = getComputedStyle(document.documentElement);
    const upColor = root.getPropertyValue('--color-up').trim();
    const downColor = root.getPropertyValue('--color-down').trim();
    // ...
}

// ❌ 错误：硬编码 RGBA 值（不跟随主题）
return `rgba(224, 122, 95, ${alpha})`;  // 只对默认主题有效
```

---

## 七、已知陷阱

### 7.1 CSS 缓存问题

修改 CSS 后浏览器可能缓存旧版本，需硬刷新：
- `Cmd + Shift + R` (macOS)
- `Ctrl + Shift + R` (Windows)

### 7.2 主题切换后 JS 读取颜色

如果 JS 需要在运行时读取主题色（如日历背景色），**必须在每次渲染时读取**，不能缓存。

### 7.3 badge/panel 的 rgba 背景色

徽章的 `background: rgba(...)` 是硬编码的，新主题需要覆盖（见 5.9）。

### 7.4 不要覆盖的属性

以下属性**不要在主题 CSS 中设置 color**，它们由 .up/.down 类控制：

- `.pnl-value` 的 `color`
- `.calendar-day-pnl` 的 `color`
- `.stock-card-bar` 的 `background`
- `.signal-buy / .signal-sell` 的 `color`
- `.order-status` 的 `color`
- `.sent-badge.positive / .negative` 的 `color`（已用 CSS 变量）

---

## 八、「阳光户外」主题设计方向

### 8.1 设计理念

**阳光户外** = 活力、自然、温暖、开放
- 灵感：清晨阳光、绿叶、蓝天、露珠
- 风格：清新明亮，略带自然纹理感
- 目标：让长时间看盘不疲劳，温暖但不刺眼

### 8.2 配色建议

```css
[data-theme="sunny"] {
    /* ── 涨跌色（A股标准）── */
    --color-up:       #DC2626;    /* 涨=正红（不偏粉不偏橙） */
    --color-down:     #16A34A;    /* 跌=翠绿 */

    /* ── 背景 ── */
    --bg:             #FFF8F0;    /* 暖白底（阳光感） */
    --bg-card:        #FFFFFF;    /* 纯白卡片 */
    --bg-hover:       #FFF5EB;    /* 悬停微暖 */
    --bg-sidebar:     #FEF3E2;    /* 侧栏暖黄底 */

    /* ── 文字 ── */
    --text-primary:   #1C1917;    /* 深棕黑（非纯黑，柔和） */
    --text-secondary: #78716C;    /* 灰棕 */
    --text-muted:     #A8A29E;    /* 浅灰棕 */

    /* ── 强调色 ── */
    --color-accent:   #2563EB;    /* 天蓝 */
    --color-accent-2: #F59E0B;    /* 阳光金 */
    --color-warn:     #EA580C;    /* 橙色警示 */
    --color-primary:  #2563EB;    /* 天蓝 */

    /* ── 渐变 ── */
    --gradient-warm:  linear-gradient(135deg, #FEF3E2, #FFF7ED);
    --gradient-cool:  linear-gradient(135deg, #EFF6FF, #F0FDF4);

    /* ── 阴影 ── */
    --shadow-card:    0 2px 8px rgba(0,0,0,0.06);
    --shadow-hover:   0 8px 24px rgba(0,0,0,0.10);
    --border-light:   rgba(0,0,0,0.08);

    /* ── 字体 ── */
    --font-sans:  'Inter', 'PingFang SC', 'Noto Sans SC', -apple-system, sans-serif;
    --font-mono:  'JetBrains Mono', 'SF Mono', monospace;
}
```

### 8.3 关键组件风格

| 组件 | 阳光户外风格 |
|------|-------------|
| Banner | 暖白底 + 天蓝 logo |
| 股票卡片 | 白底 + 微阴影 + 左侧天蓝色条（active） |
| 侧栏 | 暖黄底 + 浅色边框 |
| 按钮 primary | 天蓝实心 |
| 按钮 save/refresh | 阳光金实心 |
| Badge | 半透明彩色底 + 实色文字 |
| 异动卡片 | 左边框天蓝 + 白底 |
| 滚动条 | 浅灰 |
| Modal | 白底 + 柔和阴影 |
| 信号标签 | 涨红/跌绿/持有橙 |

---

## 九、新皮肤创建步骤

1. **创建 CSS 文件**：`static/css/sunny.css`
2. **复制模板**：从第八节的配色建议开始
3. **覆盖全部组件**：按第五节清单逐一覆盖（约 130+ 选择器）
4. **注册主题**：在 `templates/base.html` 的 `THEMES` 数组中添加
5. **测试**：切换主题后检查以下场景：
   - [ ] 自选股页面涨跌颜色正确（红涨绿跌）
   - [ ] 持仓页面盈亏数字颜色正确
   - [ ] 盈亏日历背景色和文字色正确
   - [ ] AI分析台信号颜色正确
   - [ ] Modal 弹窗在新主题下正常显示
   - [ ] 设置页面在新主题下正常显示
   - [ ] 异动浮窗在新主题下正常显示
   - [ ] 所有按钮样式统一
   - [ ] 情感标签颜色方向正确（positive=红，negative=绿）
   - [ ] 滚动条样式符合主题风格

---

## 十、文件参考

| 文件 | 行数 | 说明 |
|------|------|------|
| `static/css/style.css` | ~4810 | 基础样式 + CSS 变量 |
| `static/css/cyberpunk.css` | ~960 | 赛博主题覆盖 |
| `static/css/midnight.css` | ~919 | 午夜主题覆盖 |
| `templates/base.html` | ~305 | 主题注册 + 浮窗 + 账户胶囊 |
| `templates/index.html` | ~570 | 自选股页面 |
| `static/js/stock.js` | ~1508 | 自选股 JS |
| `static/js/portfolio.js` | ~778 | 持仓页面 JS |

---

*最后更新：2026-05-24*
*当前皮肤：赛博 cyberpunk、午夜 midnight*
*待创建：阳光户外 sunny*
