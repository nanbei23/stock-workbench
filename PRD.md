# 炒股小牛马 — PRD（产品需求文档）

---

## 设计准则

1. **股票代码不单独显示**：任何地方出现股票代码时，必须同时显示股票名称，或只显示股票名称。不允许只显示代码不带名字。

---

## 一、产品概述

炒股小牛马是一个本地运行的 A 股盯盘 + AI 分析工作台。

| 维度 | 说明 |
|------|------|
| 定位 | A股个人投资者的一站式决策辅助工具 |
| 核心功能 | 自选股行情、持仓管理、AI深度分析（TradingAgents）、新闻聚合、研究报告 |
| 数据源 | 腾讯行情（tencent_quote_batch）、东方财富（push2/search-api/np-weblist）、财联社（cls.cn）、搜狗微信搜索 |
| AI引擎 | **TradingAgents-astock**（pip依赖·进程内调用）— 7类分析师多Agent深度分析 |
| 部署 | 本地FastAPI服务 `localhost:8000` |
| 技术栈 | FastAPI + Jinja2 + vanilla JS + SQLite + aiohttp |
| 主题 | 3套皮肤（赛博朋克/午夜/阳光户外），CSS变量驱动，`data-theme` 切换 |

---

## 二、页面架构

三栏布局，顶部Banner导航：

| 页面 | 路由 | 说明 |
|------|------|------|
| 自选股 | `/` | 左栏股票卡片 + 右栏详情（异动/概览/K线/七层数据/策略/买点/新闻/AI分析/研报/公告） |
| 持仓管理 | `/portfolio` | 持仓列表 + 盈亏统计 + 交易计划 + 盈亏日历 |
| AI分析台 | `/ai` | 三栏布局：左自选股卡片 + 中深度分析控制+进度+报告 + 右异动+历史报告 |
| 信号绩效 | `/signal` | 统计卡片 + 信号柱状图 + 月度趋势 + 持仓列表 |
| 设置 | `/settings` | 行情监控/AI引擎(含获取模型)/通知/费率/数据 |

---

## 三、AI分析台v2布局（2026-05-23 重构）

**页面路由**：`/ai`，三栏Grid布局。

```
┌──────────┬───────────────────────────────────────┬───────────────────┐
│          │  上证 4112 +0.87%  深证 15597 +2.3%  │                   │
│ 📊 自选股 │  创业板 3938 +2.84%                   │  🔔 异动监控       │
│          ├───────────────────────────────────────┤                   │
│ ☑ 批量   │  🔬 深度分析  分析深度[▼标准] 模型模式[▼均衡] [🚀开始分析]│  [异动日志...]    │
│          ├───────────────────────────────────────┤                   │
│ 工业富联 │  ANALYSTS                              ├───────────────────┤
│ 67.16元  │  ┌────┐ ┌────┐ ┌────┐ ┌────┐ ┌────┐ │  📋 历史报告       │
│ 当日+1660│  │📈  │ │🎭  │ │📰  │ │📋  │ │⚖️  │ │                   │
│ 持仓-450 │  │技术│ │情绪│ │新闻│ │基本面│ │政策│ │ [搜索代码...]      │
│ +2.53%   │  └────┘ └────┘ └────┘ └────┘ └────┘ │                   │
│──────────│  ┌────┐ ┌────┐                        │ 工业富联 05-22 ✅  │
│ 安集科技 │  │🚀  │ │🔓  │                        │ 安集科技 05-21 ✅  │
│ 311.19元 │  │游资│ │解禁│                        │ 扬杰科技 05-20 ⏳  │
│ 当日+6.40│  └────┘ └────┘                        │ ...               │
│ +2.10%   │  PIPELINE                             │                   │
│──────────│  ┌────┐ ┌────┐ ┌────┐ ┌────┐ ┌────┐ │                   │
│ 扬杰科技 │  │✅  │ │⚔️  │ │💰  │ │🛡️  │ │👑  │ │                   │
│ 86.38元  │  │门控│ │多空│ │交易│ │风控│ │决策│ │                   │
│ ...      │  └────┘ └────┘ └────┘ └────┘ └────┘ │                   │
└──────────┴───────────────────────────────────────┴───────────────────┘
```

### 左栏：自选股卡片列表
- 样式与自选股页面左侧卡片完全一致：名称、代码、价格、当日盈亏、持仓盈亏、当日涨幅、底部涨跌色条
- 默认无复选框，点击顶栏「☑ 批量」按钮后每张卡片左侧出现复选框
- 单击卡片 = 选中该股（蓝色高亮边框），中栏显示「已选: xxx」
- 批量模式下勾选多只，底部出现批量操作栏（已选N只 + 批量分析 + 取消）

### 中栏：控制区 + 进度模块

**顶部控制区**（单行）：
- 左侧标签「🔬 深度分析」
- 分析深度下拉框：⚡快速 / 📋标准 / 🔬深度 / 🛠️自定义
- 模型模式下拉框：💰经济 / ⚖️均衡 / 👑旗舰
- 「🚀 开始分析」按钮
- 每个下拉框上方带字段提示文字（分析深度、模型模式）

**🛠️ 自定义模式**：
- 选择「自定义」后，进度模块进入编辑态，每个阶段卡片变为可点击切换
- 必选项（🔒锁定）：📈技术、📋基本面、✅门控、💰交易、👑决策
- 可选项（灰色点击切换）：🎭情绪、📰新闻、⚖️政策、🚀游资、🔓解禁、⚔️多空、🛡️风控

**进度模块**（始终显示，铺满下方70%空间）：
- 分析师阶段（7个色块卡片）+ Pipeline阶段（5个色块卡片）
- 每个卡片为独立圆角色块，带唯一背景色（sky/purple/gold/green/rose/orange/cyan/mint/indigo/coral/slate）
- 状态：idle（默认灰色半透明）→ pending（分析中）→ running（发光边框）→ completed（✅标记）/ skipped（虚线边框）

**报告详情态**：点击历史报告后，控制区隐藏，报告详情铺满整个中栏，含报告头（信号标签+操作按钮）+ 左导航Tab + 右内容区。

### 右栏：异动监控 + 历史报告

### 报告验证

**事实账本**（报告生成时自动计算并入库）：
- 时机：报告存DB时自动执行，先于用户看到报告
- 方法：正则提取报告数值断言 vs 分析时刻快照行情，PE等多源交叉验证（腾讯+东财动态/静态/TTM）
- 消除时间衰减：比对的是分析时刻的数据，不会因为时间推移降低准确率
- 展示：报告头准确率徽章 + 「事实账本」Tab（读取缓存，不调API）

**报告复核**（报告生成时自动运行并入库）：
- 时机：报告存DB后MiMo自动复核，结果写入 `bystander_verify` 列
- 方法：MiMo v2.5 Pro 接收完整上下文（报告+快照+事实核查）进行评估
- 展示：「报告复核」Tab 直接读取缓存，底部标注「报告生成时自动复核」
- 手动重新复核：`POST /api/ai/reports/{id}/bystander-verify` 端点保留

### API依赖

| 端点 | 用途 |
|------|------|
| `GET /api/watchlist` | 左栏股票卡片 |
| `GET /api/ai/suggestions` | 大盘指数 |
| `POST /api/ai/analyze/{code}` | 启动深度分析 |
| `GET /api/ai/analyze/{id}/stream` | SSE进度流 |
| `GET /api/ai/reports` | 历史报告列表 |
| `GET /api/ai/reports/{id}` | 单份报告详情 |
| `GET /api/ai/reports/{id}/fact-check` | 事实账本 |
| `POST /api/ai/reports/{id}/bystander-verify` | 报告复核 |

---

## 四、新闻聚合

### 数据源

| 源 | 接口 | 更新频率 |
|---|------|---------|
| 东财个股新闻 | search-api-web JSONP | 实时 |
| 财联社快讯 | cls.cn | 实时 |
| 东财全球资讯 | np-weblist 7×24 | 实时 |
| 微信公众号 | 搜狗微信搜索 weixin.sogou.com | 实时 |

### 微信公众号搜索

- 默认关键词：`{股票名称}`
- 数据源：搜狗微信搜索 `https://weixin.sogou.com/weixin?query={keyword}&type=2`
- 反爬策略：8个随机 User-Agent 轮换 + Referer 头
- 返回字段：title/summary/source(公众号名称)/date/sentiment
- API端点：`GET /api/news/wechat/{code}`

---

## 五、主题换肤系统

三套主题，CSS变量驱动，`data-theme` 属性切换：

| 主题 | 文件 | 特点 |
|------|------|------|
| 赛博朋克 | `cyberpunk.css` | 暗黑玻璃态，琥珀金+蓝，up=#FF4D6A(粉红), down=#00D4A1(翡翠绿) |
| 午夜 | `midnight.css` | 纯黑平铺，Binance交易所风，up=#EF4444(标准红), down=#22C55E(标准绿) |
| 阳光户外 | `sunny.css` | 暖白底#FFF8F0，天蓝#2563EB+阳光金#F59E0B，up=#DC2626, down=#16A34A |

**实现方式：**
- `[data-theme="X"]` CSS选择器覆盖变量，JS切换 `localStorage` 持久化
- `THEMES` 数组定义主题列表（id/name/icon），base.html 动态渲染切换按钮
- 规范文档：`static/css/SKIN_SPEC.md`（26KB，10章：架构/变量/涨跌颜色/按钮/组件清单/日历/陷阱/设计方向/创建步骤/文件参考）

**CSS变量清单（51个组件组）：**
- 基础：`--bg-primary`, `--bg-secondary`, `--bg-card`, `--text-primary`, `--text-secondary`, `--border-color`
- 强调：`--color-accent`, `--color-accent-2`, `--color-accent-hover`
- 涨跌：`--color-up`(红涨), `--color-down`(绿跌), `--color-flat`
- 组件：`--btn-primary-bg`, `--btn-secondary-bg`, `--input-bg`, `--modal-overlay`, `--toast-bg` 等

**A股配色规范：** 红涨绿跌（非国际惯例），`.up` / `.down` / `.flat` 全局类用 `!important` 确保优先级。

---

## 六、设置页

5个Tab分区，`id="section-xxx"` 切换：

| 分区 | 设置项 |
|------|--------|
| 📊 行情监控 | 自动刷新开关(`auto_refresh_enabled`)、刷新间隔(10/15/30/60秒/2分钟)、异动阈值(涨跌幅/成交量/北向)、自动异动监控开关 |
| 🤖 AI引擎 | 模型模式(经济/均衡/旗舰)、LLM供应商(9个)、深度/快速思考模型、API密钥(👁显示隐藏)、自定义API端点+**🔍获取模型按钮**、输出语言、辩论轮数、Crash恢复、旁观者核对(模型+端点+密钥+获取模型+测试连接)、AI调度计划(4个时段+异动) |
| 🔔 通知 | 策略变化/条件单触发/异动/分析完成通知开关 |
| 💰 费率 | 佣金/印花税/过户费 |
| 💾 数据 | 导入/导出/清空 |

**获取模型功能：**
- 主模型：读取`set-custom_endpoint`（空则用placeholder=供应商默认端点）+ `set-api_key` → `POST /api/settings/fetch-models` → 填充深度/快速下拉框
- 核对模型：读取`set-verification_endpoint`（空则根据核对模型自动推断端点）+ `set-verification_api_key` → 同一API → 合并到预设选项
- 后端：兼容 OpenAI `/v1/models` 协议，智能拼接URL（处理端点已含`/v1`的情况）
- 供应商默认端点映射（`PROVIDER_DEFAULT_ENDPOINTS`）：deepseek/openai/anthropic/qwen/glm/xai/minimax/ollama/google

**设置存储：** 用户设置存DB（`settings`表），通过 `/api/settings/bulk` 批量读写。`settings.js` 通用识别：`id="set-xxx"` 命名规则，`applySettings()` 遍历API key找元素，`collectSettings()` 遍历`[id^="set-"]` 元素。

**自动刷新同步：** 设置页 toggle ↔ 自选股页 checkbox 双向同步，均读写同一DB key。

---

## 七、技术架构

### 7.1 后端

| 层 | 技术 | 说明 |
|---|------|------|
| Web框架 | FastAPI | 异步路由，自动OpenAPI文档 |
| 模板引擎 | Jinja2 | 服务端渲染，`TemplateResponse(request=request, name="xxx.html")` |
| 数据库 | SQLite | 同步连接 `sqlite3.connect(str(DB_PATH))` + `db.row_factory = sqlite3.Row` |
| 异步DB | aiosqlite | async函数用 `models.database.get_db()`（必须await） |
| HTTP客户端 | aiohttp | 异步数据源请求，macOS需 `TCPConnector(family=AF_INET)` 绕过IPv6 |
| AI引擎 | TradingAgents-astock | pip依赖，进程内调用，12阶段Pipeline |
| 定时任务 | APScheduler | 每日15:30信号跟踪、看盘时段异动监控 |
| 进度推送 | SSE | `GET /api/ai/analyze/{id}/stream` |

### 7.2 前端

| 层 | 技术 | 说明 |
|---|------|------|
| JS框架 | 无（vanilla JS） | 无npm/webpack，直接 `<script>` 引入 |
| API调用 | 全局 `API.get()` / `API.post()` | stock.js用API对象；portfolio.js用 `apiGet` / `apiPost` |
| K线图表 | Lightweight Charts v4 | TradingView开源库，分时图+日K |
| 主题切换 | CSS变量 + `data-theme` | localStorage持久化 |
| 设置存储 | DB（非localStorage） | 通过 `/api/settings/bulk` 读写 |

### 7.3 数据库表（14张）

| 表 | 用途 | 关键列 |
|---|------|--------|
| `settings` | 用户设置 | key TEXT PK, value TEXT |
| `watchlist` | 自选股 | code, name, account |
| `trades` | 交易记录 | code(非code6), direction(非type), price, quantity |
| `positions` | 持仓 | code, name, quantity, cost_price |
| `analysis_reports` | AI分析报告 | code, signal, report_text, market_snapshot, fact_check, bystander_verify |
| `analysis_tasks` | 分析任务 | task_id, code, status, progress |
| `analysis_progress` | 断点续跑 | task_id, stage_id, report_text |
| `signal_tracking` | 信号跟踪 | signal, entry_price, current_price, pnl_pct, status |
| `news_cache` | 新闻缓存 | code, source, title, sentiment |
| `announcements` | 公告 | code, title, date, type |
| `anomaly_log` | 异动记录 | code, type, value, detected_at |
| `accounts` | 账户 | name, is_default |
| `notifications` | 通知 | type, message, read |
| `conditional_orders` | 条件单 | code, condition, target_price |

### 7.4 前端文件结构

```
static/
├── css/
│   ├── style.css          # 基础样式(~4800行): 组件+涨跌+.up/.down/.flat+btn类
│   ├── cyberpunk.css      # 赛博主题(~960行): 变量覆盖+btn类
│   ├── midnight.css       # 午夜主题(~920行): 变量覆盖+btn类
│   ├── sunny.css          # 阳光户外(~960行): 51组件组181规则
│   └── SKIN_SPEC.md       # 皮肤开发规范(26KB)
├── js/
│   ├── stock.js           # 自选股页(~1500行): 异动卡片+公告+自动刷新同步
│   ├── ai.js              # AI分析台(~440行): 卡片选择+进度+自定义模式
│   ├── portfolio.js       # 持仓页(~780行): apiGet/apiPost
│   ├── settings.js        # 设置页(~530行): 通用set-xxx识别+获取模型
│   └── chart.js           # K线图表渲染
templates/
├── base.html              # 基础模板: 导航+主题切换+浮窗+账户胶囊
├── index.html             # 自选股页: 异动Tab+概览+公告下拉
├── ai.html                # AI分析台: 三栏Grid
├── portfolio.html         # 持仓页: 3Tab
├── settings.html          # 设置页: 5Tab
└── signal.html            # 信号绩效页
```

### 7.5 按钮统一样式体系

| 类名 | 用途 | 赛博 | 午夜 | 阳光 |
|------|------|------|------|------|
| `.btn` / `.btn-primary` | 主操作 | 青蓝渐变 | 琥珀金 | 天蓝 |
| `.btn-secondary` | 次要操作 | 半透明青蓝 | 石灰绿#A3E635 | 暖灰 |
| `.btn-save` | 保存 | 青蓝渐变 | 琥珀金 | 天蓝 |
| `.btn-test` | 测试连接 | 青蓝渐变 | 石灰绿 | 天蓝 |
| `.btn-ghost` | 透明按钮 | 透明+青蓝边框 | 透明+石灰绿边框 | 透明+天蓝边框 |
| `.btn-icon` | 图标按钮 | 青蓝 | 石灰绿 | 天蓝 |
| `.pwd-toggle` | 密码显示隐藏 | 青蓝 | 石灰绿 | 天蓝 |
| `.btn-sm` | 小按钮 | 同上缩小 | 同上缩小 | 同上缩小 |

### 7.6 涨跌颜色规范

```css
/* 全局类（!important确保优先级） */
.up   { color: var(--color-up) !important; }    /* 红涨 */
.down { color: var(--color-down) !important; }   /* 绿跌 */
.flat { color: var(--color-flat) !important; }   /* 平盘 */
```

| 场景 | 涨 | 跌 | 平 |
|------|---|---|---|
| 赛博 | #FF4D6A(粉红) | #00D4A1(翡翠绿) | #888 |
| 午夜 | #EF4444(标准红) | #22C55E(标准绿) | #888 |
| 阳光 | #DC2626(标准红) | #16A34A(标准绿) | #888 |
| 情感分析 | positive(看多)=红 | negative(看空)=绿 | neutral=灰 |

**CSS specificity**：`.up`(0,1,0) < `[data-theme] .pnl-value`(0,2,0)，但 `.up{!important}` 已解决。

### 7.7 设计准则

1. **股票代码不单独显示**：任何地方出现股票代码时，必须同时显示股票名称
2. **单行内联标签**：标签左右排列，不堆叠
3. **色块圆角大卡片**：背景色+圆角+阴影
4. **批量选择默认隐藏**：toggle控制显示
5. **进度始终显示**：不用吉祥物占位
6. **PRD先草稿确认再写入**：用户确认后才patch
7. **A股红涨绿跌**：不使用国际惯例
8. **确认/可以=直接写代码不反问**

---

## 八、自选股页面布局（`/`）

左栏股票卡片 + 右栏详情，右栏Tab顺序：

```
┌────────────┬──────────────────────────────────────────────┐
│ 📊 自选股   │  [异动] [概览] [K线] [策略] [新闻] [AI] [研报] [公告] │
│ ☑ 批量      │                                              │
│ ┌────────┐ │  ┌──────────────────────────────────────────┐│
│ │工业富联 │ │  │  异动Tab（默认激活，第一位）                ││
│ │67.16元  │ │  │  ┌─────────────────────────────────┐     ││
│ │+2.53%   │ │  │  │ 📡 暂无异动                      │     ││
│ │[色条]   │ │  │  │ 系统将自动监控行情异动...         │     ││
│ ├────────┤ │  │  └─────────────────────────────────┘     ││
│ │安集科技 │ │  │  异动卡片: 类型图标+中文标签+数值+时间     ││
│ │311.19元 │ │  │  点击卡片 → 跳转到该股票详情              ││
│ │+2.10%   │ │  └──────────────────────────────────────────┘│
│ └────────┘ │                                              │
└────────────┴──────────────────────────────────────────────┘
```

### 异动Tab（默认激活，第一位）
- 空状态：大图标📡 + 标题"暂无异动" + 描述
- 异动卡片：`renderAnomalyCard(a, isNew)` 渲染，10种中文类型映射
- 点击卡片 → `switchStock(code)` 跳转到该股票详情
- 自动加载：`loadStockAnomalies(code)` 页面打开时自动调用

### 概览Tab
- 🔄 刷新概览按钮
- 七层数据概览（价格/成交量/资金流/北向/技术指标/基本面/估值）

### 公告Tab
- 年/月/类型三个下拉select替代date input
- `loadAnnounce(code)` + `filterAnnouncements()` 前端筛选

### 自动刷新
- 设置页 toggle ↔ 自选股页 checkbox 双向同步
- `auto_refresh_enabled` + `refresh_interval` 存DB
- stock.js DOMContentLoaded 从API读取设置同步UI

---

## 九、持仓页面布局（`/portfolio`）

3个Tab：持仓列表 / 交易计划 / 盈亏日历

### 持仓列表Tab
- 股票卡片：名称+代码+数量+成本价+现价+盈亏金额+盈亏比例
- 盈亏颜色：读CSS变量 `--color-up` / `--color-down`（A股红涨绿跌）
- 止损设置：每只股票可设止损价

### 交易计划Tab
- 待执行的交易计划列表

### 盈亏日历Tab
- 日历格式展示每日盈亏，颜色同涨跌规范

---

## 十、异动监控系统

### 10.1 异动类型（10种中文映射）

| 类型ID | 中文标签 | 触发条件 |
|--------|---------|---------|
| `volume_spike` | 成交量异动 | 成交量超过N倍均量 |
| `price_surge` | 价格急涨 | 短时间涨幅超阈值 |
| `price_drop` | 价格急跌 | 短时间跌幅超阈值 |
| `large_buy` | 大单买入 | 大单净买入超阈值 |
| `large_sell` | 大单卖出 | 大单净卖出超阈值 |
| `northbound_in` | 北向流入 | 北向资金净买入超阈值 |
| `northbound_out` | 北向流出 | 北向资金净卖出超阈值 |
| `limit_up` | 涨停 | 涨幅≥9.9% |
| `limit_down` | 跌停 | 跌幅≤-9.9% |
| `turnover_spike` | 换手率异动 | 换手率超阈值 |

### 10.2 异动浮窗（base.html）

- 🔔 铃铛按钮 + badge数字（未读数）
- 点击打开浮窗 overlay（`rgba(0,0,0,0.55)` + `blur(4px)`）
- 浮窗560px宽，14px圆角
- 按股票分组显示，每组可展开/折叠
- 分组标题和每条异动都可点击跳转
- 中文类型标签（typeLabel映射）

### 10.3 API端点

| 端点 | 用途 |
|------|------|
| `GET /api/ai/anomalies?limit=50&code=XXXXXX` | 获取异动列表 |
| `POST /api/ai/trigger/{code}` | 单股异动检测 |

### 10.4 定时轮询

- `pollAnomalies()` 每30秒轮询一次
- 新异动时铃铛badge数字更新
- 工作日看盘时间（9:15-15:15）自动启用

---

## 十一、API端点清单

### 设置相关
| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/settings` | GET | 获取全部设置（合并DEFAULTS） |
| `/api/settings/bulk` | POST | 批量更新设置 |
| `/api/settings/reset` | POST | 重置为默认 |
| `/api/settings/test-llm` | POST | 测试LLM连接 |
| `/api/settings/test-verification` | POST | 测试核对连接 |
| `/api/settings/export` | GET | 导出设置 |
| `/api/settings/import` | POST | 导入设置 |
| `/api/settings/clear-all` | POST | 清空所有数据 |
| `/api/settings/model_mode` | POST | 快捷设置模型模式 |
| `/api/settings/fetch-models` | POST | 获取远程模型列表 |
| `/api/settings/{key}` | GET/PUT | 单个设置CRUD |

### 自选股相关
| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/watchlist` | GET | 自选股列表 |
| `/api/watchlist` | POST | 添加自选股 |
| `/api/watchlist/{code}` | DELETE | 删除自选股 |
| `/api/quote/{code}` | GET | 单股行情 |
| `/api/quote/batch` | POST | 批量行情 |

### AI分析相关
| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/ai/analyze/{code}` | POST | 启动深度分析 |
| `/api/ai/analyze/{id}/stream` | GET | SSE进度流 |
| `/api/ai/analyze/{id}/status` | GET | 任务状态 |
| `/api/ai/analyze/{task_id}/resume` | POST | 断点续跑 |
| `/api/ai/reports` | GET | 历史报告列表 |
| `/api/ai/reports/{id}` | GET | 单份报告详情 |
| `/api/ai/reports/{id}/fact-check` | GET | 事实账本 |
| `/api/ai/reports/{id}/bystander-verify` | POST | 报告复核 |
| `/api/ai/anomalies` | GET | 异动列表 |
| `/api/ai/trigger/{code}` | POST | 单股异动检测 |

### 信号跟踪相关
| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/signal/track` | POST | 手动添加跟踪 |
| `/api/signal/tracking` | GET | 跟踪列表 |
| `/api/signal/stats` | GET | 绩效统计 |
| `/api/signal/tracking/{id}/close` | POST | 手动平仓 |

### 新闻相关
| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/news/{code}` | GET | 个股新闻 |
| `/api/news/wechat/{code}` | GET | 微信公众号 |
| `/api/news/sentiment/{code}` | GET | 情感分析 |
| `/api/announce/{code}` | GET | 公告列表 |

---

## 十二、更新日志（2026-05-23）

### 微信公众号新闻搜索
- `data/news.py`: 新增 `search_wechat_articles()` — 搜狗微信搜索，8个UA轮换
- `api/news_api.py`: 新增 `/news/wechat/{code}` — 默认用股票名搜索
- `templates/index.html`: 新闻区新增「公众号」Tab
- `static/js/stock.js`: 新增 `loadWechatNews()` + `renderWechatItem()`

### AI分析台v2重构
- `templates/ai.html`: 全部重写 — 左栏自选股卡片 + 中栏指数/控制/进度 + 右栏异动/报告
- `static/css/style.css`: 替换~300行AI CSS
- `static/js/ai.js`: 全部重写(~440行) — 卡片选择/进度切换/自定义模式/批量选择

### 自定义分析模式
- 深度下拉框新增「🛠️ 自定义」选项
- 必选项(🔒): 技术、基本面、门控、交易、决策
- 可选项(点击切换): 情绪、新闻、政策、游资、解禁、多空、风控

### 阶段卡片色块化
- 12个阶段卡片改为独立色块（sky/purple/gold/green/rose/orange/cyan/mint/indigo/coral/slate）
- flex:1 自适应撑满，五态样式（idle/pending/running/completed/skipped）

### 事实账本改造（快照对比）
- DB 新增 `market_snapshot` + `fact_check` 列
- 分析启动时快照行情，存报告时对比快照计算事实账本（消除时间衰减）

### 报告复核升级
- 上下文从2段摘要升级为：完整报告(11个section) + 分析结论 + 元数据 + 快照行情 + 事实核查
- Key 自动读取：设置页 → 环境变量 → Hermes 配置 `custom_providers`

### Bug修复
- Surrogate emoji报错：`\ud83d\udcc8` → 真实emoji
- 模型模式POST 405：新增 `/api/settings/model_mode` 端点
- `get_all_settings` 不返回新增key：改为DB优先+DEFAULTS补充
- 事实账本 `await` 缺失修复
- 核对模型URL自动补全 `/chat/completions`

### 其他
- 自选股新增吉祥航空(603885)
- 设置页核对API密钥加 👁 显示/隐藏 + 🔌 测试连接
- 报告验证Tab标签改为「事实账本」「报告复核」
- **新增设计准则：股票代码不单独显示**
- **事实账本多源交叉验证**：PE同时比对腾讯+东财（动态/静态/TTM），任一匹配即通过
- **报告复核自动入库**：分析完成时自动调MiMo复核，结果存入DB，前端Tab读缓存不调API

---

## 十三、2026-05-24 更新日志

### 异动监控系统全面重构
- 异动Tab移到自选股右栏**第一位**，默认激活
- 异动卡片重写：`renderAnomalyCard(a, isNew)` 含10种中文类型映射
- 异动浮窗改modal（560px/14px圆角/overlay+blur），替代旧下拉
- 浮窗按股票分组，分组标题和每条异动都可点击跳转
- CSS `.anomaly-modal*` 替换旧 `.bell-dropdown*`

### 概览Tab + 公告下拉
- 概览Tab新增🔄刷新概览按钮
- 公告Tab：年/月/类型三个select替代date input，前端 `filterAnnouncements()` 筛选

### 涨跌颜色规范化
- `.up` / `.down` / `.flat` 全局类（之前根本没定义！）
- `!important` 确保优先级覆盖 `[data-theme]` 选择器
- sent-badge / sentiment-tag 改用CSS变量

### 按钮统一样式体系
- 新增 `.btn-secondary` / `.btn-save` / `.btn-test` / `.pwd-toggle` 基础定义
- 赛博/午夜/阳光三主题各自覆盖
- 设置页👁按钮统一样式

### 阳光户外(sunny)第三套皮肤
- `sunny.css` 961行181条规则覆盖全部51个组件组
- 配色：暖白底#FFF8F0 + 天蓝#2563EB + 阳光金#F59E0B
- notify-badge修复：天蓝accent色+白底（原白底白字不可见）

### 设置页 ↔ 自选股页自动刷新同步
- `auto_refresh_enabled` + `refresh_interval` 存DB
- 设置页toggle ↔ 自选股页checkbox双向同步
- `settings.js` 通用 `set-xxx` 命名规则自动识别，无需改JS

### 获取模型按钮
- 主模型：`POST /api/settings/fetch-models`，兼容OpenAI `/v1/models` 协议
- 核对模型：同一API，根据核对模型名自动推断端点（9供应商映射）
- 端点为空时用placeholder（=供应商默认端点）
- 后端智能URL拼接：处理端点已含`/v1` 的情况

### 账户选择器胶囊样式
- select改为胶囊样式（圆角20px，自定义SVG下拉箭头）

### switchDetail bug修复
- 按name匹配按钮替代 `event.target`（自动化点击时丢失）

### 皮肤规范文档
- `static/css/SKIN_SPEC.md` 26KB完整规范（10章）
- 取代旧版 `SKIN_GUIDE.md`

---

## 十四、AI分析台 — 断点续跑与容错（2026-05-24 新增）

### 4.1 问题背景

AI分析任务（TradingAgents 12阶段Pipeline）在运行过程中可能因LLM API瞬时错误（500/429）而失败。
失败时已完成的9个阶段报告丢失，用户只能从头重新分析，浪费5-15分钟。

### 4.2 LLM API 重试机制

- stream循环包裹在3次重试逻辑中（间隔15s/30s指数退避）
- 每次重试时LangGraph checkpointer自动跳过已完成节点
- 仅在3次重试均失败后才标记任务为failed
- 覆盖错误类型：500 Internal Server Error、429 Rate Limit、网络超时

### 4.3 中间结果持久化

- **新表 `analysis_progress`**：`(task_id, code, stage_id, report_text, completed_at)`，主键 `(task_id, stage_id)`
- 每完成一个stage立即写DB（`INSERT OR REPLACE`），不等全部完成
- 任务失败时自动调用 `_save_stage_progress()` 保存已有进度
- 任务成功时仍走原有 `_save_report_to_db()` 完整入库

### 4.4 断点续跑流程

```
分析失败（9/12阶段完成）
  ↓
前端显示：⚠️ 错误信息 + 🔄 继续分析按钮
  ↓
用户点击 → POST /api/ai/analyze/{task_id}/resume
  ↓
后端创建新任务，从 analysis_progress 加载已完成stage报告
  ↓
LangGraph checkpointer 检测到已保存的state → 跳过已完成节点
  ↓
仅执行失败的3个阶段（trader/risk/pm）→ 完成
```

### 4.5 新增/修改文件清单

| 文件 | 类型 | 改动 |
|------|------|------|
| `models/database.py` | 修改 | SCHEMA新增 `analysis_progress` 表 |
| `scheduler/ta_bridge.py` | 修改 | 新增 `_save_stage_progress()` / `_load_stage_progress()`；stream循环加重试(3次)；每stage完成立即存盘；失败时保存进度；函数签名加 `resume_from_task_id` 参数 |
| `api/ai_api.py` | 修改 | 新增 `POST /ai/analyze/{task_id}/resume` 端点；status端点返回error字段 |
| `templates/ai.html` | 修改 | 新增 `#resumeContainer`（错误信息+继续分析按钮） |
| `static/js/ai.js` | 修改 | 新增 `showResumeButton()` / `resumeAnalysis()`；失败改为显示续跑按钮而非alert |

### 4.6 API接口

**POST /api/ai/analyze/{task_id}/resume**

请求：无body，task_id为失败任务的ID

响应：
```json
{
  "task_id": "新任务ID",
  "status": "pending",
  "message": "已从断点续跑 佩蒂股份(300673) 分析任务"
}
```

错误：
- 404：任务不存在
- 400：任务状态不是failed

### 4.7 状态端点增强

`GET /api/ai/analyze/{task_id}/status` 现在在任务失败时返回 `error` 字段：

```
{
  "task_id": "d162a005",
  "status": "failed",
  "progress": "9/12",
  "error": "Error code: 500 - Internal Server Error",
  "stages": { ... }
}
```

---

## 十五、信号跟踪与绩效验证（2026-05-24）

### 5.1 背景与动机

AI分析台每天生成研究报告，每份报告产出一个7档信号（STRONG_BUY → STRONG_SELL）。但这些信号的**真实有效性**无法通过历史回测验证（数据点不足、AI不可复现、A股无自动交易API）。

**解决方案：前向验证（Forward Testing）**
- 从今天开始，每次AI出报告时自动记录"信号快照"
- 每天自动跟踪信号股票的收盘价
- 3-6个月后积累足够数据，统计每档信号的真实胜率和收益率

这是唯一**没有偏差**的验证方式——真实信号、真实价格、真实持有期。

### 5.2 数据模型

#### signal_tracking 表

```sql
CREATE TABLE IF NOT EXISTS signal_tracking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER,                -- 关联 analysis_reports.id
    code TEXT NOT NULL,               -- 股票代码 (e.g. "688525")
    name TEXT NOT NULL,               -- 股票名称 (e.g. "佰维存储")
    signal TEXT NOT NULL,             -- 7档信号: STRONG_BUY/BUY/OVERWEIGHT/HOLD/UNDERWEIGHT/SELL/STRONG_SELL
    signal_date TEXT NOT NULL,        -- 信号日期 (YYYY-MM-DD)
    entry_price REAL NOT NULL,        -- 入场价 (报告生成时的收盘价)
    target_price REAL,                -- 报告中的目标价 (可为空)
    stop_loss_price REAL,             -- 止损价 (可为空，默认 entry * 0.9)
    current_price REAL,               -- 最新收盘价 (每日更新)
    highest_price REAL,               -- 持仓期间最高价
    lowest_price REAL,                -- 持仓期间最低价
    exit_price REAL,                  -- 出场价
    exit_date TEXT,                   -- 出场日期
    exit_reason TEXT,                 -- 出场原因: signal_change/target_hit/stop_loss/max_hold
    pnl_pct REAL,                     -- 收益率 (%)
    hold_days INTEGER,                -- 持有天数
    benchmark_return REAL,            -- 同期沪深300收益率 (%)
    excess_return REAL,               -- 超额收益 = pnl_pct - benchmark_return
    status TEXT DEFAULT 'open',       -- open / closed
    notes TEXT,                       -- 备注
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
```

#### 索引

```sql
CREATE INDEX idx_signal_tracking_status ON signal_tracking(status);
CREATE INDEX idx_tracking_code ON signal_tracking(code);
CREATE INDEX idx_tracking_signal ON signal_tracking(signal);
CREATE INDEX idx_tracking_date ON signal_tracking(signal_date);
```

### 5.3 信号生命周期

```
AI报告生成
  ↓
自动创建 signal_tracking 记录
  status = 'open'
  entry_price = 当日收盘价
  signal = 报告信号
  ↓
每日定时任务（15:30 收盘后）
  更新 current_price / highest_price / lowest_price
  检查出场条件
  ↓
触发出场 → status = 'closed'
  计算 pnl_pct / hold_days / benchmark_return / excess_return
```

### 5.4 出场规则（优先级从高到低）

| 优先级 | 出场条件 | exit_reason | 说明 |
|--------|---------|-------------|------|
| 1 | 信号反转 | `signal_change` | 同一股票新报告信号方向反转（增持↔减持/卖出↔买入）。HOLD不算反转 |
| 2 | 止损触发 | `stop_loss` | current_price ≤ stop_loss_price（默认 entry * 0.9） |
| 3 | 目标价到达 | `target_hit` | current_price ≥ target_price（仅当信号为买入/增持方向时） |
| 4 | 最大持有期 | `max_hold` | 持有超过 60 个交易日仍未出场 |

**信号反转定义：**
- 买入方向信号：STRONG_BUY / BUY / OVERWEIGHT
- 卖出方向信号：STRONG_SELL / SELL / UNDERWEIGHT
- 中性信号：HOLD（不触发反转出场）
- 反转 = 从买入方向变为卖出方向，或反之

### 5.5 每日定时任务

**触发时间：** 每个交易日 15:30（收盘后30分钟）

**执行逻辑：**
1. 获取所有 `status='open'` 的跟踪记录
2. 批量调用 `tencent_quote_batch` 获取当前价格
3. 更新 `current_price`、`highest_price`、`lowest_price`
4. 检查出场条件（按优先级）
5. 触发出场时：
   - 设置 `exit_price`、`exit_date`、`exit_reason`、`status='closed'`
   - 计算 `pnl_pct`、`hold_days`
   - 查询同期沪深300收益率，计算 `benchmark_return`、`excess_return`
6. 记录执行日志

### 5.6 API接口

#### POST /api/signal/track — 手动添加跟踪（备用）

```json
// 请求
{
  "code": "688525",
  "name": "佰维存储",
  "signal": "OVERWEIGHT",
  "entry_price": 85.20,
  "target_price": 100.00
}

// 响应
{
  "id": 1,
  "status": "open",
  "message": "已添加跟踪：佰维存储 OVERWEIGHT @ ¥85.20"
}
```

#### GET /api/signal/tracking — 获取跟踪列表

```
?status=open          // 只看未平仓
?status=closed        // 只看已平仓
?signal=OVERWEIGHT    // 按信号筛选
?code=688525          // 按股票筛选
```

#### GET /api/signal/stats — 获取绩效统计

```json
// 响应
{
  "total": 45,
  "open": 20,
  "closed": 25,
  "win_rate": 0.68,              // 总体胜率
  "avg_pnl_pct": 5.2,           // 平均收益率
  "avg_hold_days": 18,          // 平均持有天数
  "avg_excess_return": 2.1,     // 平均超额收益
  "best_trade": { "code": "688525", "name": "佰维存储", "pnl_pct": 25.3 },
  "worst_trade": { "code": "300673", "name": "佩蒂股份", "pnl_pct": -8.5 },
  "by_signal": {
    "STRONG_BUY": { "count": 3, "win_rate": 1.0, "avg_pnl": 12.5 },
    "BUY": { "count": 8, "win_rate": 0.75, "avg_pnl": 6.2 },
    "OVERWEIGHT": { "count": 12, "win_rate": 0.67, "avg_pnl": 4.1 },
    "HOLD": { "count": 5, "win_rate": 0.4, "avg_pnl": -1.2 },
    "UNDERWEIGHT": { "count": 10, "win_rate": 0.6, "avg_pnl": 3.8 },
    "SELL": { "count": 5, "win_rate": 0.8, "avg_pnl": 7.2 },
    "STRONG_SELL": { "count": 2, "win_rate": 1.0, "avg_pnl": 15.0 }
  },
  "monthly_returns": [
    { "month": "2026-05", "return_pct": 3.2, "count": 8 },
    { "month": "2026-06", "return_pct": -1.5, "count": 12 }
  ]
}
```

#### POST /api/signal/tracking/{id}/close — 手动平仓

```json
// 请求
{
  "exit_price": 92.50,
  "exit_reason": "manual"
}
```

### 5.7 前端页面（方案A：仪表盘卡片式）

**路由：** `/ai` 页面新增 tab "📈 信号绩效"

**设计原则：**
- 一屏看完所有数据，不滚动切换
- 柱状图用纯CSS（宽度百分比），不引入ECharts
- 权益曲线用已有 Lightweight Charts
- A股配色：红涨(#E07A5F) 绿跌(#52B788)
- 单行内联标签，色块圆角卡片

**布局结构：**

```
┌─────────────────────────────────────────────────────────┐
│  📈 信号绩效                    [全部] [持仓中] [已平仓]   │
├────────┬────────┬────────┬────────┬────────┬────────────┤
│ 总跟踪 │ 开仓   │ 胜率    │ 平均收益│ 超额   │ 平均持有    │
│  45    │  20    │  68%   │ +5.2%  │ +2.1% │  18天      │
├────────┴────────┴────────┴────────┴────────┴────────────┤
│                                                         │
│  每档信号绩效                    [横向柱状图]              │
│  ┌─────────────────────────────────────────────────┐    │
│  │  STRONG_BUY  ████████████████████  +12.5% (3)   │    │
│  │  BUY         ██████████████       +6.2%  (8)   │    │
│  │  OVERWEIGHT  █████████            +4.1%  (12)  │    │
│  │  HOLD        ███                  -1.2%  (5)   │    │
│  │  UNDERWEIGHT ████████             +3.8%  (10)  │    │
│  │  SELL        █████████████        +7.2%  (5)   │    │
│  │  STRONG_SELL ████████████████████ +15.0% (2)   │    │
│  └─────────────────────────────────────────────────┘    │
│                                                         │
│  月度收益趋势                    [折线图]                  │
│  ┌─────────────────────────────────────────────────┐    │
│  │  15% ─                                    ╭──── │    │
│  │  10% ─                          ╭────────╯     │    │
│  │   5% ─              ╭──────────╯               │    │
│  │   0% ──────────────╯                           │    │
│  │  -5% ─                                          │    │
│  │      5月    6月    7月    8月    9月   10月      │    │
│  └─────────────────────────────────────────────────┘    │
│                                                         │
│  持仓中（20笔）                                           │
│  ┌──────────────────────────────────────────────────┐   │
│  │ 🟢 佰维存储 688525  OVERWEIGHT  +8.5%  15天      │   │
│  │    入场 ¥85.20 → 现价 ¥92.43  目标 ¥100  [平仓]  │   │
│  ├──────────────────────────────────────────────────┤   │
│  │ 🔴 佩蒂股份 300673  BUY        -3.2%  8天        │   │
│  │    入场 ¥45.20 → 现价 ¥43.76  目标 ¥55  [平仓]   │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

#### 区域1：统计卡片（顶部6格）

6个等宽卡片，横向排列：

| 卡片 | 字段 | 格式 | 颜色规则 |
|------|------|------|---------|
| 总跟踪 | total | 数字 | 默认白 |
| 开仓 | open | 数字 | 默认白 |
| 胜率 | win_rate | 百分比 | ≥60% 红色, <50% 绿色 |
| 平均收益 | avg_pnl_pct | ±X.X% | 正红负绿 |
| 超额 | avg_excess_return | ±X.X% | 正红负绿 |
| 平均持有 | avg_hold_days | X天 | 默认白 |

CSS类：`.perf-stat-card`，复用 `.dash-card` 样式。

#### 区域2：每档信号绩效（横向柱状图）

7行，每行包含：
- 左侧：信号中文标签（SIG_LABEL映射）
- 中间：CSS横向条形图，宽度 = (该档avg_pnl / max_abs_pnl) * 50%
  - 正收益：红色条，向右延伸
  - 负收益：绿色条，向左延伸
- 右侧：平均收益率 + 交易笔数

```html
<div class="signal-bar-row">
  <span class="signal-bar-label">增持</span>
  <div class="signal-bar-track">
    <div class="signal-bar-fill bar-up" style="width: 33%"></div>
  </div>
  <span class="signal-bar-value price-up">+4.1% (12笔)</span>
</div>
```

CSS：
- `.signal-bar-row` — flex布局，align-items: center，gap: 8px
- `.signal-bar-label` — 固定宽度 80px，右对齐
- `.signal-bar-track` — flex: 1，height: 20px，背景色 var(--bg-secondary)，border-radius: 4px，position: relative
- `.signal-bar-fill` — height: 100%，border-radius: 4px，position: absolute，top: 0
  - `.bar-up` — left: 50%，background: var(--color-up)
  - `.bar-down` — right: 50%，background: var(--color-down)
- `.signal-bar-value` — 固定宽度 120px，font-size: 0.85em

#### 区域3：月度收益趋势（折线图）

使用 Lightweight Charts 的 LineSeries：
- X轴：月份（2026-05, 2026-06, ...）
- Y轴：累计收益率(%)
- 参考线：0% 水平线
- 颜色：正收益红色，负收益绿色
- 容器高度：180px

如果数据不足3个月，显示"数据积累中，至少需要3个月数据"占位。

#### 区域4：持仓列表（下方卡片）

**筛选按钮组：** [全部(45)] [持仓中(20)] [已平仓(25)]

默认显示"持仓中"。

每条记录为一张卡片，包含：
- 第一行：🟢/🔴 + 股票名 代码 | 信号标签(SIG_LABEL) | 收益率(±X.X%) | 持有X天
- 第二行：入场 ¥XX.XX → 现价 ¥XX.XX | 目标 ¥XX.XX | [手动平仓]按钮
- 已平仓卡片额外显示：出场原因 + 出场价 + 超额收益

颜色规则：
- 收益率正数：price-up（红色）
- 收益率负数：price-down（绿色）
- 🟢 = 收益率 > 0
- 🔴 = 收益率 ≤ 0

**手动平仓弹窗：**

点击 [平仓] 按钮弹出确认框：
```
确认平仓？
佰维存储 688525 — OVERWEIGHT
入场 ¥85.20 → 现价 ¥92.43
收益率：+8.5%

[取消] [确认平仓]
```

### 5.7.1 交互细节

1. **Tab切换**：点击 [全部/持仓中/已平仓] 筛选下方列表，统计区域不受影响
2. **每日刷新**：进入信号绩效tab时自动调用 `/api/signal/stats` 刷新数据
3. **手动平仓**：确认后调用 `POST /api/signal/tracking/{id}/close`，刷新列表
4. **空状态**：无跟踪数据时显示"暂无信号跟踪数据，AI分析报告生成后将自动记录"
5. **信号标签**：统一使用全局 `SIG_LABEL` 映射，中英文对照显示

### 5.8 自动集成点

**报告生成时自动创建跟踪记录：**

在 `scheduler/ta_bridge.py` 的 `_save_report_to_db()` 末尾，报告入库成功后：

```python
# 自动创建信号跟踪
tracking_id = _create_signal_tracking(report_id, code, name, signal, entry_price)
```

**定时任务注册：**

在 `scheduler/scheduler.py` 中注册每日任务：

```python
# 每日15:30更新信号跟踪价格
scheduler.add_job(update_signal_tracking_prices, 'cron', hour=15, minute=30, 
                  day_of_week='mon-fri', id='signal_tracking_update')
```

### 5.9 新增/修改文件清单

| 文件 | 类型 | 改动 |
|------|------|------|
| `models/database.py` | 修改 | SCHEMA新增 `signal_tracking` 表 |
| `scheduler/ta_bridge.py` | 修改 | `_save_report_to_db()` 末尾调用 `_create_signal_tracking()` |
| `scheduler/signal_tracker.py` | 新建 | 信号跟踪核心逻辑：创建/更新/出场/统计 |
| `scheduler/scheduler.py` | 修改 | 注册每日15:30定时任务 |
| `api/signal_api.py` | 新建 | 信号跟踪 REST API |
| `app.py` | 修改 | 注册 signal_api 路由 |
| `templates/ai.html` | 修改 | 新增 "📈 信号绩效" tab |
| `static/js/ai.js` | 修改 | 新增信号绩效tab渲染逻辑 |

### 5.10 验收标准

1. AI报告生成后，`signal_tracking` 表自动新增一条 `status='open'` 的记录
2. 每日15:30定时任务更新所有 open 记录的 current_price
3. 满足出场条件时自动关闭记录并计算 pnl_pct / benchmark_return
4. `/ai` 页面 "📈 信号绩效" tab 正确展示统计数据
5. 手动平仓功能正常
6. 同一股票新报告信号反转时，旧跟踪自动关闭
