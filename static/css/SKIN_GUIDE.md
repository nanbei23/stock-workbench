# 🎨 皮肤制作须知

> **目的**：避免做新皮肤时踩坑，记录所有已知的 CSS 规则和陷阱。

---

## 一、架构概览

```
style.css        ← 基础样式 + CSS 变量定义（不依赖 data-theme）
cyberpunk.css    ← 赛博主题覆盖（所有规则以 [data-theme="cyberpunk"] 开头）
midnight.css     ← 午夜主题覆盖（所有规则以 [data-theme="midnight"] 开头）
SKIN_GUIDE.md    ← 本文件
```

**主题切换机制**：
- `document.body.setAttribute('data-theme', id)` 切换
- `localStorage.setItem('theme', id)` 持久化
- 默认主题：`cyberpunk`（如果 localStorage 没存值）
- 旧主题 `classic` 已废弃，读到后自动回退到 `cyberpunk`

**主题列表定义在 `templates/base.html`**：
```js
const THEMES = [
    { id: 'cyberpunk', name: '赛博', icon: '🌆' },
    { id: 'midnight',  name: '午夜', icon: '🌙' },
];
```

---

## 二、必须定义的 CSS 变量

每个主题文件**必须**在 `[data-theme="xxx"]` 下重新定义这些变量：

```css
[data-theme="xxx"] {
    /* ── 涨跌色（A股标准：红涨绿跌）── */
    --color-up:       #???(红);   /* 涨 */
    --color-down:     #???(绿);   /* 跌 */

    /* ── 背景 ── */
    --bg-primary:     #???;
    --bg-secondary:   #???;
    --bg-card:        #???;
    --bg-input:       #???;
    --bg-hover:       #???;

    /* ── 文字 ── */
    --text-primary:   #???;
    --text-secondary: #???;
    --text-muted:     #???;

    /* ── 边框 ── */
    --border-color:   #???;

    /* ── 强调色 ── */
    --accent:         #???;
    --accent-hover:   #???;

    /* ── 字体 ── */
    --font-mono:      'JetBrains Mono', 'Fira Code', monospace;
    --font-sans:      'Inter', 'Noto Sans SC', sans-serif;
}
```

**参考值（现有两个主题）**：

| 变量 | 赛博 cyberpunk | 午夜 midnight |
|------|---------------|--------------|
| --color-up | `#FF4D6A` | `#EF4444` |
| --color-down | `#00D4A1` | `#22C55E` |
| --bg-primary | `#0D0D1A` | `#0F172A` |
| --bg-card | `#1A1A2E` | `#1E293B` |
| --text-primary | `#E8E8FF` | `#F1F5F9` |
| --accent | `#7B61FF` | `#3B82F6` |

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
.up   { color: var(--color-up); }     /* specificity: 0,1,0 */
.down { color: var(--color-down); }   /* specificity: 0,1,0 */
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
/* ✅ 正确：主题 CSS 中，不要给有 .up/.down 类的元素设置 color */
/* 如果一定要设，确保 specificity 不高于 .up/.down */

/* 方法1：不设置 color（推荐） */
[data-theme="cyberpunk"] .pnl-value {
    /* 只设置其他属性，不设 color */
    font-family: var(--font-mono);
}

/* 方法2：用 !important（不推荐，但可行） */
.up   { color: var(--color-up) !important; }
.down { color: var(--color-down) !important; }
```

### 3.4 所有使用涨跌颜色的元素

| 元素/类 | 用途 | 来源 |
|---------|------|------|
| `.up / .down / .flat` | 通用涨跌文字 | style.css L603-605 |
| `.pnl-value` | 盈亏数字（概览、卡片） | 通过 .up/.down 类控制 |
| `.calendar-day-pnl.up/.down` | 日历盈亏数字 | style.css L1203-1204 |
| `.stock-card-bar.up/.down` | 自选股卡片色条 | style.css L287-293 |
| `.signal-buy / .signal-sell` | 信号标签 | style.css L699-700 |
| `.badge-up / .badge-down` | 徽章 | style.css L679-680 |
| `.data-badge.up / .down` | 数据徽章 | style.css L1880-1881 |
| `.order-status.up / .down` | 订单状态 | style.css L1261-1268 |

---

## 四、盈亏日历背景色

日历格子的背景色由 JS 动态生成，**必须读 CSS 变量**：

```js
// ✅ 正确：从 CSS 变量读取颜色
const root = getComputedStyle(document.documentElement);
const upColor = root.getPropertyValue('--color-up').trim();
const downColor = root.getPropertyValue('--color-down').trim();

function getPnlColor(pnl) {
    const alpha = 0.15 + ratio * 0.35;
    const hex = pnl > 0 ? upColor : downColor;
    const r = parseInt(hex.slice(1,3), 16);
    const g = parseInt(hex.slice(3,5), 16);
    const b = parseInt(hex.slice(5,7), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

// ❌ 错误：硬编码 RGBA 值（不跟随主题）
return `rgba(224, 122, 95, ${alpha})`;  // 只对默认主题有效
```

---

## 五、新皮肤检查清单

创建新皮肤时，逐项检查：

### 5.1 文件结构

- [ ] 创建 `static/css/新皮肤名.css`
- [ ] 所有规则以 `[data-theme="新皮肤名"]` 开头
- [ ] 定义所有必须的 CSS 变量（见第二节）
- [ ] 在 `templates/base.html` 的 `THEMES` 数组中注册

### 5.2 涨跌颜色

- [ ] `--color-up` 是红色系（A股标准）
- [ ] `--color-down` 是绿色系（A股标准）
- [ ] 没有给 `.pnl-value`、`.calendar-day-pnl`、`.stock-card-bar` 等元素设置 `color` 属性（会覆盖 .up/.down）
- [ ] 没有给任何带 `.up/.down` 类的父元素设置 `color`

### 5.3 日历背景色

- [ ] 确认 `portfolio.js` 的 `getPnlColor` 函数使用 CSS 变量（非硬编码 RGBA）

### 5.4 视觉一致性

- [ ] 所有页面的背景色、文字色、边框色一致
- [ ] Modal 弹窗背景色不透明（能遮住内容）
- [ ] 输入框、下拉框、按钮在新主题下可读
- [ ] 表格行 hover 高亮效果正常
- [ ] Toast 通知在新主题下可读

### 5.5 测试

- [ ] 切换到新主题后刷新页面，确认 localStorage 正确保存
- [ ] 自选股页面涨跌颜色正确
- [ ] 持仓页面盈亏数字颜色正确
- [ ] 盈亏日历背景色和文字色正确
- [ ] AI分析台信号颜色正确
- [ ] Modal 弹窗在新主题下正常显示
- [ ] 设置页面在新主题下正常显示

---

## 六、已知陷阱

### 6.1 CSS 缓存问题

修改 CSS 后浏览器可能缓存旧版本：

```html
<!-- 方法1：在 CSS 链接后加时间戳 -->
<link rel="stylesheet" href="/static/css/新皮肤.css?_t=20260524">

<!-- 方法2：强制刷新 -->
Cmd + Shift + R (macOS)
Ctrl + Shift + R (Windows)
```

### 6.2 主题切换后 JS 读取颜色

如果 JS 需要在运行时读取主题色（如日历背景色），**必须在每次渲染时读取**，不能缓存：

```js
// ❌ 错误：页面加载时读取一次，切换主题后颜色不变
const upColor = getComputedStyle(document.documentElement)
    .getPropertyValue('--color-up').trim();

// ✅ 正确：每次渲染时重新读取
function renderCalendar() {
    const root = getComputedStyle(document.documentElement);
    const upColor = root.getPropertyValue('--color-up').trim();
    // ...
}
```

### 6.3 badge/panel 的 rgba 背景色

徽章和面板的半透明背景色用了硬编码的 rgba：

```css
/* style.css L679-680 */
.badge-up { background: rgba(224,122,95,0.12); color: var(--color-up); }
.badge-down { background: rgba(82,183,136,0.12); color: var(--color-down); }
```

这些硬编码值对应默认主题的 `#E07A5F` 和 `#52B788`。新主题如果用了不同的 `--color-up/down`，需要在主题 CSS 中覆盖这些 badge 的 `background`：

```css
[data-theme="新皮肤"] .badge-up {
    background: rgba(新up的r, 新up的g, 新up的b, 0.12);
}
[data-theme="新皮肤"] .badge-down {
    background: rgba(新down的r, 新down的g, 新down的b, 0.12);
}
```

### 6.4 不要覆盖的属性

以下属性**不要在主题 CSS 中覆盖**，它们由 .up/.down 类控制：

- `.pnl-value` 的 `color`
- `.calendar-day-pnl` 的 `color`
- `.stock-card-bar` 的 `background`
- `.signal-buy / .signal-sell` 的 `color`
- `.order-status` 的 `color`

---

## 七、快速复制模板

```css
/* ========================================
 * 🎨 新皮肤 — [名称]
 * ======================================== */

[data-theme="新皮肤名"] {
    /* ── 涨跌色（A股标准）── */
    --color-up:       #???(红);
    --color-down:     #???(绿);

    /* ── 背景 ── */
    --bg-primary:     #???;
    --bg-secondary:   #???;
    --bg-card:        #???;
    --bg-input:       #???;
    --bg-hover:       #???;
    --bg-modal:       #???;

    /* ── 文字 ── */
    --text-primary:   #???;
    --text-secondary: #???;
    --text-muted:     #???;

    /* ── 边框 ── */
    --border-color:   #???;

    /* ── 强调色 ── */
    --accent:         #???;
    --accent-hover:   #???;

    /* ── 字体 ── */
    --font-mono:      'JetBrains Mono', 'Fira Code', monospace;
    --font-sans:      'Inter', 'Noto Sans SC', sans-serif;

    /* ── 其他 ── */
    --shadow:         0 2px 8px rgba(0,0,0,0.3);
    --radius:         8px;
}

/* ── 背景 ── */
[data-theme="新皮肤名"] body {
    background: var(--bg-primary);
    color: var(--text-primary);
}

/* ── Banner ── */
[data-theme="新皮肤名"] .banner {
    background: var(--bg-secondary);
    border-bottom: 1px solid var(--border-color);
}

/* ── 卡片 ── */
[data-theme="新皮肤名"] .card {
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: var(--radius);
}

/* ── 输入框 ── */
[data-theme="新皮肤名"] input,
[data-theme="新皮肤名"] select,
[data-theme="新皮肤名"] textarea {
    background: var(--bg-input);
    color: var(--text-primary);
    border: 1px solid var(--border-color);
}

/* ── 按钮 ── */
[data-theme="新皮肤名"] .btn-primary {
    background: var(--accent);
    color: white;
}

[data-theme="新皮肤名"] .btn-primary:hover {
    background: var(--accent-hover);
}

/* ── ⚠️ 不要在这里设置 .pnl-value 的 color ── */
/* ── ⚠️ 不要在这里设置 .calendar-day-pnl 的 color ── */
/* ── ⚠️ 不要在这里设置 .stock-card-bar 的 background ── */
```

---

## 八、常见问题

**Q: 切换主题后涨跌颜色没变？**
A: 检查主题 CSS 是否给 `.pnl-value` 或其父元素设置了 `color`，specificity 压过了 `.up/.down`。

**Q: 日历背景色在新主题下不对？**
A: 检查 `portfolio.js` 的 `getPnlColor` 是否从 CSS 变量读取颜色，而不是硬编码 RGBA。

**Q: 徽章背景色和文字色不匹配？**
A: 徽章的 `background: rgba(...)` 是硬编码的，新主题需要覆盖。

**Q: 主题切换后某些元素颜色没变？**
A: 可能是 JS 缓存了颜色值，需要在渲染时重新读取 `getComputedStyle`。

---

*最后更新：2026-05-24*
*当前皮肤：赛博 cyberpunk、午夜 midnight*
