"""TradingAgents bridge — invoke and parse TA results.

Encapsulates the TradingAgents-astock pipeline:
- Pipeline stage definitions
- _run_trading_agents() blocking entry-point (runs in thread)
- Report persistence to SQLite
- trigger_l2_for_stock() async scheduler entry-point
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from typing import Optional, List, Dict, Any

import httpx
from langchain_core.callbacks.base import BaseCallbackHandler

from scheduler.ai_engine import (
    _get_db,
    get_llm_config,
    apply_llm_config_to_ta_config,
    strip_think,
    extract_signal,
    extract_target_price,
    extract_confidence,
    extract_risk_score,
    parse_risk_debate,
)
from scheduler.gbrain_client import get_context, write_analysis_report
from scheduler.data_snapshot import DataSnapshotCallback
from scheduler.fact_checker import check_all_stages
from tasks import AnalysisTask, _tasks


class TokenTrackerCallback(BaseCallbackHandler):
    """追踪 LLM 真实 token 使用量"""

    def __init__(self):
        self.llm_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def on_llm_end(self, response, **kwargs):
        self.llm_calls += 1
        usage = response.llm_output.get("token_usage", {}) if response.llm_output else {}
        self.input_tokens += usage.get("prompt_tokens", 0)
        self.output_tokens += usage.get("completion_tokens", 0)

    def get_stats(self):
        return {
            "llm_calls": self.llm_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }

logger = logging.getLogger(__name__)

# ============================================================
# Pipeline stages
# ============================================================

PIPELINE_STAGES = [
    {"id": "market", "name": "技术分析", "icon": "📊", "report_key": "market_report"},
    {"id": "social", "name": "情绪分析", "icon": "💬", "report_key": "sentiment_report"},
    {"id": "news", "name": "新闻舆情", "icon": "📰", "report_key": "news_report"},
    {"id": "fundamentals", "name": "基本面", "icon": "📋", "report_key": "fundamentals_report"},
    {"id": "policy", "name": "政策分析", "icon": "🏛️", "report_key": "policy_report"},
    {"id": "hot_money", "name": "游资追踪", "icon": "🔥", "report_key": "hot_money_report"},
    {"id": "lockup", "name": "解禁监控", "icon": "🔒", "report_key": "lockup_report"},
    {"id": "quality_gate", "name": "质量门控", "icon": "✅", "report_key": "data_quality_summary"},
    {"id": "debate", "name": "多空辩论", "icon": "⚔️", "report_key": "investment_plan"},
    {"id": "trader", "name": "交易决策", "icon": "💹", "report_key": "trader_investment_plan"},
    {"id": "risk", "name": "风控评估", "icon": "🛡️", "report_key": "risk_debate_state"},
    {"id": "pm", "name": "最终决策", "icon": "👔", "report_key": "final_trade_decision"},
]


# ============================================================
# Core TA runner (blocking — intended for asyncio.to_thread)
# ============================================================
# 数据快照：由 DataSnapshotCallback 在 LangChain callback 中自动捕获
# 七层分析师工具的原始返回值，不再依赖腾讯/东财行情API
# ============================================================


def _save_stage_progress(task_id: str, code: str, task):
    """将已完成的stage报告存入analysis_progress表，供断点续跑加载。"""
    try:
        db = _get_db()
        try:
            for stage_id, stage_data in task.stages.items():
                if stage_data.get("status") == "completed" and stage_data.get("report"):
                    db.execute(
                        "INSERT OR REPLACE INTO analysis_progress (task_id, code, stage_id, report_text, completed_at) VALUES (?, ?, ?, ?, ?)",
                        (task_id, code, stage_id, stage_data["report"], stage_data.get("completed_at"))
                    )
            db.commit()
            logger.info("_save_stage_progress: 已保存 %s 的进度到DB", task_id)
        finally:
            db.close()
    except Exception as e:
        logger.warning("_save_stage_progress 失败: %s", e)


def _load_stage_progress(task_id: str) -> dict:
    """从analysis_progress表加载之前任务的已完成stage报告。"""
    try:
        db = _get_db()
        try:
            rows = db.execute(
                "SELECT stage_id, report_text, completed_at FROM analysis_progress WHERE task_id = ?",
                (task_id,)
            ).fetchall()
            result = {}
            for row in rows:
                result[row["stage_id"]] = {
                    "report": row["report_text"],
                    "completed_at": row["completed_at"],
                }
            logger.info("_load_stage_progress: 从DB加载了 %d 个阶段 (task=%s)", len(result), task_id)
            return result
        finally:
            db.close()
    except Exception as e:
        logger.warning("_load_stage_progress 失败: %s", e)
        return {}


async def run_with_snapshot(task_id: str, code: str, trade_date: str,
                           resume_from_task_id: str = None):
    """在线程中运行分析（数据快照由 DataSnapshotCallback 在分析过程中自动捕获）。"""
    await asyncio.to_thread(
        run_trading_agents, task_id, code, trade_date,
        resume_from_task_id,
    )


def run_trading_agents(task_id: str, code: str, trade_date: str,
                       resume_from_task_id: str = None):
    """TradingAgents-astock分析（在线程池中运行）"""
    task = _tasks[task_id]
    task.status = "running"
    task.started_at = datetime.now().isoformat()

    # 获取深度参数
    depth = getattr(task, 'depth', 'standard') or 'standard'
    selected_analysts = getattr(task, 'selected_analysts', None)
    debate_rounds = getattr(task, 'debate_rounds', None)
    risk_rounds = getattr(task, 'risk_rounds', None)

    # 根据深度初始化阶段
    if depth == 'quick':
        # 快速模式：只有market+fundamentals分析师，但pipeline节点仍会执行
        quick_stages = ['market', 'fundamentals', 'quality_gate', 'debate', 'trader', 'risk', 'pm']
        for stage in PIPELINE_STAGES:
            task.stages[stage["id"]] = {
                "status": "pending" if stage["id"] in quick_stages else "skipped",
                "started_at": None,
                "completed_at": None,
                "report": None,
            }
    else:
        # 标准/深度模式：全部阶段
        for stage in PIPELINE_STAGES:
            task.stages[stage["id"]] = {
                "status": "pending",
                "started_at": None,
                "completed_at": None,
                "report": None,
            }

    # 模拟分析师阶段为运行中
    if selected_analysts:
        analyst_ids = selected_analysts
    elif depth == 'quick':
        analyst_ids = ['market', 'fundamentals']
    else:
        analyst_ids = ["market", "social", "news", "fundamentals", "policy", "hot_money", "lockup"]

    for aid in analyst_ids:
        task.stages[aid]["status"] = "running"
        task.stages[aid]["started_at"] = datetime.now().isoformat()

    # ★ 断点续跑：加载之前完成的stage报告
    _resume_stages = {}
    if resume_from_task_id:
        _resume_stages = _load_stage_progress(resume_from_task_id)
        if _resume_stages:
            for sid, data in _resume_stages.items():
                if sid in task.stages and task.stages[sid].get("status") != "skipped":
                    task.stages[sid]["status"] = "completed"
                    task.stages[sid]["report"] = data["report"]
                    task.stages[sid]["completed_at"] = data["completed_at"]
            logger.info("断点续跑: 从任务 %s 加载了 %d 个已完成阶段", resume_from_task_id, len(_resume_stages))

    try:
        # L3: gbrain read-enhancement
        gbrain_context = ""
        try:
            gbrain_context = get_context(code, task.name)
            if gbrain_context:
                logger.info("gbrain context for %s: %d chars", code, len(gbrain_context))
        except Exception as e:
            logger.warning("gbrain context fetch failed: %s", e)

        from tradingagents.default_config import DEFAULT_CONFIG
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        config = DEFAULT_CONFIG.copy()
        apply_llm_config_to_ta_config(config)
        
        # ★ 强化数据准确性：降低温度 + 注入准确性指令
        config["deep_think_temperature"] = 0.1
        config["quick_think_temperature"] = 0.1
        # 在所有分析师prompt前追加数据准确性约束
        accuracy_prefix = (
            "【强制规则】你必须严格基于提供的实际数据进行分析。"
            "禁止编造任何数字（价格、PE、ROE、涨跌幅、成交量等）。"
            "如果数据源未提供某个指标，明确说【该数据未提供】，不得猜测。"
            "所有数值引用必须与数据源完全一致，不得四舍五入或近似。"
            "禁止在推理过程中凭空生成历史价格、对比数据或假设数值。"
            f"【标的信息】本报告分析的股票是 {task.code}（{task.name}），"
            f"报告中必须使用正确的公司名称「{task.name}」，禁止使用其他公司名称。"
        )
        current_system = config.get("system_prompt", "")
        config["system_prompt"] = accuracy_prefix + "\n\n" + current_system
        
        # 覆盖辩论轮数（如果任务指定了）
        if debate_rounds is not None:
            config["max_debate_rounds"] = debate_rounds
        if risk_rounds is not None:
            config["max_risk_discuss_rounds"] = risk_rounds

        start_time = time.time()

        token_tracker = TokenTrackerCallback()
        snapshot_cb = DataSnapshotCallback()
        graph = TradingAgentsGraph(selected_analysts=analyst_ids, debug=True, config=config, callbacks=[token_tracker, snapshot_cb])
        logger.info("Graph实例化耗时: %.2fs", time.time() - start_time)

        # ★ 初始化数据快照追踪：设置第一个分析师阶段
        _analyst_queue = [aid for aid in analyst_ids if task.stages.get(aid, {}).get("status") == "running"]
        if _analyst_queue:
            snapshot_cb.set_current_stage(_analyst_queue[0])

        # ── 逐节点stream，通过state新增key实时更新阶段 ──
        STATE_KEY_TO_STAGE = {
            "market_report": "market",
            "sentiment_report": "social",
            "news_report": "news",
            "fundamentals_report": "fundamentals",
            "policy_report": "policy",
            "hot_money_report": "hot_money",
            "lockup_report": "lockup",
            "data_quality_summary": "quality_gate",
            "investment_debate_state": "debate",
            "investment_plan": "debate",
            "trader_investment_plan": "trader",
            "risk_debate_state": "risk",
            "final_trade_decision": "pm",
        }
        _completed_stages = set()

        # 模拟propagate()的初始化
        graph.ticker = code
        graph._resolve_pending_entries(code)
        checkpointer_ctx = None
        if graph.config.get("checkpoint_enabled"):
            from tradingagents.graph.checkpointer import get_checkpointer
            checkpointer_ctx = get_checkpointer(graph.config["data_cache_dir"], code)
            saver = checkpointer_ctx.__enter__()
            graph.graph = graph.workflow.compile(checkpointer=saver)

        try:
            past_context = graph.memory_log.get_past_context(code)
            init_state = graph.propagator.create_initial_state(code, trade_date, past_context=past_context)
            stream_args = graph.propagator.get_graph_args()
            if graph.config.get("checkpoint_enabled"):
                from tradingagents.graph.checkpointer import thread_id as _tid
                tid = _tid(code, str(trade_date))
                stream_args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = tid

            trace = []
            _last_stream_error = None
            for _attempt in range(3):
                try:
                    trace = []
                    for chunk in graph.graph.stream(init_state, **stream_args):
                        # 检测state key值从空变为有内容 → 映射到stage
                        for key, stage_id in STATE_KEY_TO_STAGE.items():
                            if stage_id in _completed_stages:
                                continue
                            value = chunk.get(key)
                            # 字符串: 内容>20字才算有报告; dict: 至少一个子值>10字
                            has_content = False
                            if isinstance(value, str) and len(value.strip()) > 20:
                                has_content = True
                            elif isinstance(value, dict):
                                has_content = any(len(str(v).strip()) > 10 for v in value.values() if isinstance(v, (str, int, float)))
                            if has_content:
                                # 跳过标记为skipped的阶段（快速模式）
                                if task.stages.get(stage_id, {}).get("status") == "skipped":
                                    _completed_stages.add(stage_id)
                                    continue
                                _completed_stages.add(stage_id)
                                task.stages[stage_id]["status"] = "completed"
                                task.stages[stage_id]["completed_at"] = datetime.now().isoformat()
                                logger.info("Stage完成: %s (key='%s', len=%d)", stage_id, key, len(str(value)))
                                # ★ 数据快照：flush 当前阶段的工具数据，切换到下一阶段
                                snapshot_cb.flush()
                                _next_analyst = [a for a in _analyst_queue if a not in _completed_stages]
                                if _next_analyst:
                                    snapshot_cb.set_current_stage(_next_analyst[0])
                                # ★ 每完成一个stage立即存盘
                                _save_stage_progress(task_id, code, task)

                        trace.append(chunk)
                        # 实时同步token统计到task，供SSE读取
                        task.token_stats = token_tracker.get_stats()
                    _last_stream_error = None
                    break  # 成功，退出重试循环
                except Exception as stream_err:
                    _last_stream_error = stream_err
                    if _attempt < 2:
                        wait_sec = 15 * (_attempt + 1)
                        logger.warning("Stream第%d次尝试失败: %s，%ds后重试...", _attempt+1, stream_err, wait_sec)
                        _save_stage_progress(task_id, code, task)  # 保存已有的进度
                        time.sleep(wait_sec)
                    else:
                        logger.error("Stream重试3次均失败: %s", stream_err)
            if _last_stream_error:
                raise _last_stream_error

            final_state = trace[-1] if trace else {}
            signal = graph.process_signal(final_state.get("final_trade_decision", ""))
        finally:
            if checkpointer_ctx is not None:
                checkpointer_ctx.__exit__(None, None, None)
                graph.graph = graph.workflow.compile()

        # 存储memory log
        graph.curr_state = final_state
        graph._log_state(trade_date, final_state)
        graph.memory_log.store_decision(
            ticker=code,
            trade_date=trade_date,
            final_trade_decision=final_state.get("final_trade_decision", ""),
        )
        if graph.config.get("checkpoint_enabled"):
            from tradingagents.graph.checkpointer import clear_checkpoint
            clear_checkpoint(graph.config["data_cache_dir"], code, str(trade_date))

        elapsed = time.time() - start_time

        # 从final_state提取各阶段报告（补充未被stream覆盖的stage）
        report_keys = {
            "market": "market_report",
            "social": "sentiment_report",
            "news": "news_report",
            "fundamentals": "fundamentals_report",
            "policy": "policy_report",
            "hot_money": "hot_money_report",
            "lockup": "lockup_report",
            "quality_gate": "data_quality_summary",
            "debate": "investment_plan",
            "trader": "trader_investment_plan",
            "risk": "risk_debate_state",
            "pm": "final_trade_decision",
        }

        for stage_id, key in report_keys.items():
            value = final_state.get(key)
            if value:
                if isinstance(value, dict):
                    task.stages[stage_id]["report"] = strip_think(json.dumps(value, ensure_ascii=False, indent=2))
                else:
                    task.stages[stage_id]["report"] = strip_think(str(value))
            task.stages[stage_id]["status"] = "completed"
            task.stages[stage_id]["completed_at"] = datetime.now().isoformat()

        # 提取最终决策
        final_decision = final_state.get("final_trade_decision", {})
        if isinstance(final_decision, str):
            final_decision_text = final_decision
            final_decision = {"reasoning": final_decision_text}

        risk_state = final_state.get("risk_debate_state", {})
        if isinstance(risk_state, str):
            risk_state = {"decision": risk_state}

        all_text = ""
        if isinstance(final_decision, dict):
            all_text = final_decision.get("reasoning", "")
        else:
            all_text = str(final_decision)
        pm_text = task.stages.get("pm", {}).get("report", "") or ""
        trader_text = task.stages.get("trader", {}).get("report", "") or ""
        parse_text = all_text + "\n" + pm_text + "\n" + trader_text

        # 提取信号：只从 PM 最终决策提取，不混入 trader 执行方案
        # trader 文本含"卖出"/"买入"是执行方案描述，不是决策
        signal_str = extract_signal(pm_text)
        if signal_str == "HOLD":
            # PM 没有明确信号，尝试从 final_decision reasoning 提取
            signal_str = extract_signal(all_text)
        target_price = extract_target_price(pm_text) or extract_target_price(all_text)
        confidence = extract_confidence(pm_text) or extract_confidence(all_text)
        risk_score = extract_risk_score(pm_text) or extract_risk_score(all_text)

        risk_debate = parse_risk_debate(risk_state)

        task.status = "completed"
        task.completed_at = datetime.now().isoformat()
        task.elapsed = round(elapsed, 1)
        task.token_stats = token_tracker.get_stats()
        task.result = {
            "code": task.code,
            "name": task.name,
            "action": signal_str,
            "target_price": target_price,
            "confidence": confidence,
            "risk_score": risk_score,
            "reasoning": strip_think(all_text),
            "signal": signal_str,
            "risk_debate": risk_debate,
            "stages": {sid: s["report"] for sid, s in task.stages.items() if s["report"]},
            "gbrain_context": gbrain_context,
        }

        # ★ 事实账本：用旁观者模型逐阶段核对数据快照 vs 报告
        try:
            snapshot_summary = snapshot_cb.get_summary()
            logger.info("数据快照摘要: %s", snapshot_summary)
            data_snapshot = {}
            for sid in ["market", "social", "news", "fundamentals", "policy", "hot_money", "lockup"]:
                data_snapshot[sid] = snapshot_cb.get_stage_snapshot(sid)
            db = _get_db()
            try:
                # 注入股票代码→名称映射，供事实账本核对公司名称
                try:
                    _name_rows = db.execute("SELECT code, name FROM watchlist").fetchall()
                    name_lines = ["【股票代码→公司名称映射】"]
                    for _nr in _name_rows:
                        name_lines.append(f"{_nr['code']} = {_nr['name']}")
                    if task.code and task.name:
                        name_lines.append(f"{task.code} = {task.name}")
                    name_info = "\n".join(name_lines)
                    for sid in data_snapshot:
                        if data_snapshot[sid]:
                            data_snapshot[sid] = data_snapshot[sid] + "\n\n" + name_info
                except Exception:
                    pass
                fact_ledger = check_all_stages(data_snapshot, task.stages, db)
            finally:
                db.close()
            task._fact_ledger = fact_ledger
            logger.info("事实账本生成完成: 准确率=%.1f%%, 幻觉=%d",
                        fact_ledger.get("overall_accuracy", 0),
                        fact_ledger.get("total_hallucinations", 0))
        except Exception as e:
            logger.warning("事实账本生成失败: %s", e)
            task._fact_ledger = None

        _save_report_to_db(task)
        write_analysis_report(task)

        logger.info("TradingAgents分析完成: %s 耗时%.1fs signal=%s", code, elapsed, signal_str)

    except ImportError as e:
        task.status = "failed"
        task.error = f"TradingAgents未安装: {e}"
        _save_stage_progress(task_id, code, task)
        logger.error("TradingAgents未安装: %s", e)
    except Exception as e:
        task.status = "failed"
        task.error = str(e)
        _save_stage_progress(task_id, code, task)
        logger.error("TradingAgents分析失败: %s", e, exc_info=True)


def _extract_claims(text: str) -> list:
    """从报告文本中提取数值断言"""
    import re
    claims = []
    patterns = [
        (r'(?:PE|市盈率|P/E)[^\d]{0,10}(\d+\.?\d*)\s*倍?', 'PE'),
        (r'(?:PB|市净率|P/B)[^\d]{0,10}(\d+\.?\d*)\s*倍?', 'PB'),
        (r'(?:ROE|净资产收益率)[^\d]{0,10}(\d+\.?\d*)\s*%?', 'ROE'),
        (r'(?:现价|股价|收盘价)[^\d]{0,10}(\d+\.?\d*)', '现价'),
        (r'(?:涨跌幅|涨幅|跌幅)[^\d]{0,10}[+-]?(\d+\.?\d*)\s*%?', '涨跌幅'),
        (r'(?:成交量)[^\d]{0,10}(\d+\.?\d*)', '成交量'),
        (r'(?:总市值|市值)[^\d]{0,10}(\d+\.?\d*)', '总市值'),
    ]
    for pattern, keyword in patterns:
        for m in re.finditer(pattern, text):
            val = m.group(1)
            if val and float(val) > 0:
                claims.append({"text": m.group(0)[:100], "keyword": keyword, "value": val})
    return claims


def _run_bystander_verify(db, report_id, code, _unused):
    """报告生成后自动运行旁观者复核，结果存入DB"""
    import os
    import yaml
    row = db.execute("SELECT * FROM analysis_reports WHERE id=?", (report_id,)).fetchone()
    if not row: return

    # 读设置
    cfg = {}
    for r in db.execute("SELECT key, value FROM settings WHERE key LIKE 'verification_%'").fetchall():
        cfg[r["key"]] = r["value"]
    verify_model = cfg.get("verification_model") or "mimo-v2.5-pro"
    verify_endpoint = cfg.get("verification_endpoint", "")
    verify_key = cfg.get("verification_api_key", "")

    api_url = verify_endpoint or "https://token-plan-cn.xiaomimimo.com/v1"
    if not api_url.endswith("/chat/completions"):
        api_url = api_url.rstrip("/") + "/chat/completions"

    api_key = verify_key
    if not api_key:
        try:
            hermes_cfg = os.path.expanduser("~/.hermes/config.yaml")
            with open(hermes_cfg) as f:
                hc = yaml.safe_load(f)
            for m in hc.get("custom_providers", []):
                if "mimo" in str(m.get("base_url", "")).lower():
                    if not api_key or m.get("name") == "小米mimo":
                        api_key = m.get("api_key", "")
        except Exception:
            pass
    if not api_key:
        return

    # 构建上下文：①分析结论 ②报告全文 ③事实账本
    signal = row["signal"] or "N/A"
    confidence = row["confidence"]
    risk_score = row["risk_score"]
    conclusion = f"信号={signal}"
    if confidence: conclusion += f" 置信度={confidence}"
    if risk_score: conclusion += f" 风险评分={risk_score}"

    # ② 报告全文（7分析师 + 4决策链，各400字）
    analyst_sections = [
        ("市场技术", row["market_report"]),
        ("市场情绪", row["sentiment_report"]),
        ("新闻舆情", row["news_report"]),
        ("基本面", row["fundamentals_report"]),
        ("政策分析", row["policy_report"]),
        ("游资追踪", row["hot_money_report"]),
        ("解禁监控", row["lockup_report"]),
    ]
    decision_sections = [
        ("多空辩论", row["investment_debate"]),
        ("风控评估", row["risk_debate"]),
        ("交易计划", row["trader_plan"]),
        ("最终决策", row["final_decision"]),
    ]
    active_analysts = [name for name, v in analyst_sections if v]
    report_text = ""
    for name, content in analyst_sections:
        if content:
            report_text += f"\n### {name}\n{content[:400]}\n"
    for name, content in decision_sections:
        if content:
            report_text += f"\n### {name}\n{content[:400]}\n"

    # ③ 事实账本（新格式 stages）
    fact_check_info = "无"
    if row["fact_check"]:
        try:
            fc = json.loads(row["fact_check"])
            if fc.get("stages"):
                lines = [f"总准确率={fc.get('overall_accuracy',0)}% 幻觉={fc.get('total_hallucinations',0)}"]
                for sid, st in fc["stages"].items():
                    lines.append(f"  {sid}: {st.get('accuracy',0)}% (匹配{st.get('matched',0)}/幻觉{st.get('mismatched',0)}/无源{st.get('no_source',0)})")
                    for h in st.get("hallucinations", [])[:3]:
                        if h.get("status") == "mismatch":
                            lines.append(f"    ⚠ {h['keyword']}: 报告={h.get('claimed_value')} 实际={h.get('snapshot_value')}")
                fact_check_info = "\n".join(lines)
        except: pass

    prompt = f"""你是A股分析报告的独立复核员。评估报告质量。

## 分析结论
{conclusion}

## 分析元数据
已运行分析师({len(active_analysts)}个): {', '.join(active_analysts)}

## 完整报告
{report_text[:3500]}

## 事实核查
{fact_check_info}

## 评估要求
基于以上证据评估：
1. **逻辑严密性**：各分析师观点是否自洽，结论是否被报告内容支撑
2. **深度充分性**：分析广度是否足够，是否遗漏关键维度
3. **数据一致性**：报告中引用的数字是否与事实核查结果一致
4. **整体可信度**：综合评分

JSON输出：
{{"hallucinations": [{{"claim": "具体问题", "issue": "说明", "severity": "high/medium/low"}}], "overall_score": 0-100, "summary": "评估结论"}}"""

    try:
        resp = httpx.post(
            api_url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": verify_model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 2000,
                "temperature": 0.3,
            },
            timeout=60,
        )
        if resp.status_code >= 400 or not resp.text.strip():
            logger.warning("报告复核请求失败: status=%s body=%s", resp.status_code, resp.text[:200])
            return
        data = resp.json()
        if "choices" in data:
            content = data["choices"][0]["message"]["content"]
            # 提取JSON
            import re as _re
            m = _re.search(r'\{[\s\S]*\}', content)
            verify_result = json.loads(m.group()) if m else {"summary": content, "overall_score": 50}
            db.execute("UPDATE analysis_reports SET bystander_verify=? WHERE id=?",
                       (json.dumps(verify_result, ensure_ascii=False), report_id))
            db.commit()
            logger.info("报告复核已存入: id=%d score=%s", report_id, verify_result.get("overall_score"))
    except Exception as e:
        logger.warning("报告复核调用失败: %s", e)


def _save_report_to_db(task: AnalysisTask):
    """将分析报告存入SQLite（使用现有analysis_reports表）"""
    db = _get_db()
    try:
        stages = task.stages
        result = task.result or {}

        # ★ 事实账本：使用旁观者模型逐阶段核对的结果
        fact_check_json = None
        fact_ledger = getattr(task, '_fact_ledger', None)
        if fact_ledger:
            try:
                fact_check_json = json.dumps(fact_ledger, ensure_ascii=False)
            except Exception as e:
                logger.warning("事实账本序列化失败: %s", e)

        # 数据快照摘要（存入 market_snapshot 列供旁观者复核参考）
        snapshot_str = None
        try:
            snapshot_cb_summary = {}
            for sid in ["market", "social", "news", "fundamentals", "policy", "hot_money", "lockup"]:
                stage_data = stages.get(sid, {})
                if stage_data.get("report"):
                    snapshot_cb_summary[sid] = {
                        "status": stage_data.get("status"),
                        "report_len": len(stage_data.get("report", "")),
                    }
            if snapshot_cb_summary:
                snapshot_str = json.dumps(snapshot_cb_summary, ensure_ascii=False)
        except Exception:
            pass

        # 模型模式
        model_mode = "balanced"
        try:
            model_mode = get_llm_config().get("model_mode") or "balanced"
        except Exception as e:
            logger.debug("读取模型模式失败，使用默认值: %s", e)

        depth = getattr(task, 'depth', 'standard') or 'standard'

        db.execute("""
            INSERT INTO analysis_reports
            (task_id, code, signal, confidence, risk_score,
             market_report, sentiment_report, news_report, fundamentals_report,
             policy_report, hot_money_report, lockup_report,
             investment_debate, risk_debate, final_decision, trader_plan,
             raw_state, duration_seconds, market_snapshot, fact_check, bystander_verify,
             depth, model_mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            task.task_id,
            task.code,
            result.get("signal"),
            result.get("confidence"),
            result.get("risk_score"),
            stages.get("market", {}).get("report"),
            stages.get("social", {}).get("report"),
            stages.get("news", {}).get("report"),
            stages.get("fundamentals", {}).get("report"),
            stages.get("policy", {}).get("report"),
            stages.get("hot_money", {}).get("report"),
            stages.get("lockup", {}).get("report"),
            stages.get("debate", {}).get("report"),
            stages.get("risk", {}).get("report"),
            stages.get("pm", {}).get("report"),
            stages.get("trader", {}).get("report"),
            json.dumps(task.result, ensure_ascii=False) if task.result else None,
            task.elapsed,
            snapshot_str,
            fact_check_json,
            None,  # bystander_verify 后续自动填充
            depth,
            model_mode,
        ))
        db.commit()
        report_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        task.result = task.result or {}
        task.result['_reportId'] = report_id
        logger.info("报告已保存: id=%d, code=%s", report_id, task.code)

        try:
            from services.shadow_portfolio_service import sync_report_from_sqlite
            sync_report_from_sqlite(db, report_id)
        except Exception as e:
            logger.warning("AI影子盘同步失败: %s", e)

        # 自动创建信号跟踪
        signal = result.get("signal")
        if signal:
            try:
                from scheduler.signal_tracker import create_tracking
                entry_price = result.get("entry_price") or result.get("current_price")
                target_price = result.get("target_price")
                name = result.get("name") or task.code
                create_tracking(report_id, task.code, name, signal,
                                entry_price or 0, target_price)
            except Exception as e:
                logger.warning("自动创建信号跟踪失败: %s", e)

    except Exception as e:
        logger.error("保存报告失败: %s", e)
    finally:
        db.close()


# ============================================================
# Async entry-points for scheduler / API
# ============================================================

async def trigger_l2_for_stock(code: str, trade_date: str = None) -> Optional[str]:
    """Trigger L2 analysis for a single stock (called by scheduler).

    Returns task_id if queued, None if skipped (queue full or already running).
    """
    from services.ai_analysis_service import trigger_l2_for_stock as trigger

    return await trigger(code, trade_date)
