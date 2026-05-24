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
from datetime import datetime, date
from typing import Optional, List, Dict, Any

from langchain_core.callbacks.base import BaseCallbackHandler

from scheduler.ai_engine import (
    _get_db,
    apply_llm_config_to_ta_config,
    strip_think,
    extract_signal,
    extract_target_price,
    extract_confidence,
    extract_risk_score,
    parse_risk_debate,
    get_stock_name,
)
from scheduler.gbrain_client import get_context, write_analysis_report
from tasks import AnalysisTask, _tasks, _tasks_status, MAX_CONCURRENT, MAX_QUEUE


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

def run_trading_agents(task_id: str, code: str, trade_date: str):
    """TradingAgents-astock分析（在线程池中运行）"""
    task = _tasks[task_id]
    task.status = "running"
    task.started_at = datetime.now().isoformat()

    # ★ 快照当前行情（腾讯+东财双源，避免不同数据源PE口径差异导致假偏差）
    try:
        from data.helpers import asyncio, tencent_quote_batch
        import aiohttp
        async def _capture():
            quotes = await tencent_quote_batch([code])
            snap = dict(quotes.get(code, {}))
            # 同时拉东财PE（PUSH2 f162=PE动态 f163=PE静态 f115=PE_TTM）
            secid = f"1.{code}" if code.startswith("60") else f"0.{code}"
            async with aiohttp.ClientSession() as s:
                url = "https://push2.eastmoney.com/api/qt/stock/get"
                params = {"secid": secid, "fields": "f162,f163,f115,f167,f43", "ut": "fa5fd1943c7b386f172d6893dbfba10b"}
                async with s.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as r:
                    d = await r.json(content_type=None)
                    em = d.get("data", {}) if d else {}
                    if em:
                        snap["em_pe_dynamic"] = em.get("f162")  # PE动态
                        snap["em_pe_static"] = em.get("f163")   # PE静态
                        snap["em_pe_ttm"] = em.get("f115")      # PE_TTM
                        snap["em_price"] = em.get("f43")        # 现价
                        snap["em_pb"] = em.get("f167")          # PB
            return snap
        snap = asyncio.run(_capture())
        task._market_snapshot = json.dumps(snap) if snap else None
    except Exception as e:
        logger.warning("行情快照失败: %s", e)
        task._market_snapshot = None

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
        graph = TradingAgentsGraph(selected_analysts=analyst_ids, debug=True, config=config, callbacks=[token_tracker])
        logger.info("Graph实例化耗时: %.2fs", time.time() - start_time)

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

                if chunk.get("messages") and len(chunk["messages"]) > 0:
                    chunk["messages"][-1].pretty_print()
                trace.append(chunk)
                # 实时同步token统计到task，供SSE读取
                task.token_stats = token_tracker.get_stats()

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

        signal_str = extract_signal(parse_text)
        target_price = extract_target_price(parse_text)
        confidence = extract_confidence(parse_text)
        risk_score = extract_risk_score(parse_text)

        risk_debate = parse_risk_debate(risk_state)

        task.status = "completed"
        task.completed_at = datetime.now().isoformat()
        task.elapsed = round(elapsed, 1)
        task.token_stats = token_tracker.get_stats()
        task.result = {
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

        _save_report_to_db(task)
        write_analysis_report(task)

        logger.info("TradingAgents分析完成: %s 耗时%.1fs signal=%s", code, elapsed, signal_str)

    except ImportError as e:
        task.status = "failed"
        task.error = f"TradingAgents未安装: {e}"
        logger.error("TradingAgents未安装: %s", e)
    except Exception as e:
        task.status = "failed"
        task.error = str(e)
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
    import subprocess, os, yaml
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

    # 构建上下文（简化版：报告摘要 + 快照 + 事实账本）
    report_text = (row["final_decision"] or "") or ""
    for col in ["market_report", "fundamentals_report"]:
        v = row[col] or ""
        if v: report_text += "\n" + v[:500]

    snapshot_info = ""
    fact_check_info = "无"
    if row["market_snapshot"]:
        try:
            snap = json.loads(row["market_snapshot"])
            snapshot_info = f"现价={snap.get('price')} PE={snap.get('pe')}"
            if snap.get("em_pe_dynamic"):
                snapshot_info += f" PE动态={snap['em_pe_dynamic']}"
            if snap.get("em_pe_static"):
                snapshot_info += f" PE静态={snap['em_pe_static']}"
        except: pass
    if row["fact_check"]:
        try:
            fc = json.loads(row["fact_check"])
            fact_check_info = f"准确率={fc.get('accuracy')}% (通过{fc.get('verified',0)}/偏差{fc.get('mismatched',0)})"
        except: pass

    prompt = f"""你是A股分析报告的独立复核员。评估报告质量。

## 分析报告
{report_text[:2000]}

## 实际行情
{snapshot_info}

## 事实核查
{fact_check_info}

## 评估要求
基于以上证据评估数据准确性、逻辑严密性、整体可信度。
JSON输出：{{"hallucinations":[],"overall_score":0-100,"summary":"结论"}}"""

    try:
        result = subprocess.run([
            "curl", "-s", "-X", "POST", api_url,
            "-H", f"Authorization: Bearer {api_key}",
            "-H", "Content-Type: application/json",
            "-d", json.dumps({"model": verify_model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 2000, "temperature": 0.3}),
            "--max-time", "60",
        ], capture_output=True, text=True, timeout=65)
        if result.stdout:
            data = json.loads(result.stdout)
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

        # ★ 基于快照计算事实账本
        fact_check_json = None
        snapshot_str = getattr(task, '_market_snapshot', None)
        if snapshot_str:
            try:
                snapshot = json.loads(snapshot_str)
                report_text = ""
                for col in ["market_report","fundamentals_report","final_decision"]:
                    val = (stages.get(col, {}).get("report") or
                           stages.get(col, {}) if isinstance(stages.get(col), str) else None)
                    if val:
                        report_text += (val if isinstance(val, str) else str(val)) + "\n"
                # 用快照数据做人肉比对（PE等多源交叉验证）
                claims = _extract_claims(report_text)
                actual_data = {}
                for k, v in {
                    "现价": snapshot.get("price"),
                    "涨跌幅": snapshot.get("change_pct"),
                    "PE": snapshot.get("pe"),
                    "总市值": snapshot.get("total_market_cap"),
                    "成交量": snapshot.get("volume"),
                }.items():
                    if v is not None:
                        actual_data[k] = [v]
                # 东财多口径PE作为备选匹配源
                em_pes = []
                for em_key in ["em_pe_dynamic", "em_pe_static", "em_pe_ttm"]:
                    v = snapshot.get(em_key)
                    if v is not None and float(v) > 0:
                        em_pes.append(v)
                if em_pes:
                    actual_data["PE"] = [snapshot.get("pe")] + em_pes if snapshot.get("pe") else em_pes
                # 东财现价备选
                if snapshot.get("em_price"):
                    actual_data["现价"].append(snapshot["em_price"]) if snapshot.get("price") else [snapshot["em_price"]]
                if snapshot.get("em_pb"):
                    actual_data.setdefault("PB", [])
                    actual_data["PB"].append(snapshot["em_pb"])

                results = []
                for claim in claims:
                    matched = False
                    actual_val = None
                    for key, vals in actual_data.items():
                        if claim["keyword"] in key:
                            for val in vals:
                                if val and abs(float(claim["value"]) - float(val)) < max(abs(float(val)) * 0.1, 1):
                                    matched = True
                                    actual_val = val
                                    break
                            if not matched and vals:
                                actual_val = vals[0]  # 显示首选值
                            break
                    results.append({
                        "claim": claim["text"], "keyword": claim["keyword"],
                        "claimed_value": claim["value"], "actual_value": actual_val,
                        "status": "unverifiable" if not actual_val else ("verified" if matched else "mismatch"),
                    })
                verified = sum(1 for r in results if r["status"] == "verified")
                mismatched = sum(1 for r in results if r["status"] == "mismatch")
                total = len(results)
                fact_check_json = json.dumps({
                    "accuracy": round(verified / max(total - (total - verified - mismatched), 1) * 100, 1),
                    "verified": verified, "mismatched": mismatched,
                    "unverifiable": total - verified - mismatched,
                    "snapshot_time": datetime.now().isoformat(),
                    "claims": results[:20],
                }, ensure_ascii=False)
            except Exception as e:
                logger.warning("事实账本计算失败: %s", e)

        db.execute("""
            INSERT INTO analysis_reports
            (task_id, code, signal, confidence, risk_score,
             market_report, sentiment_report, news_report, fundamentals_report,
             policy_report, hot_money_report, lockup_report,
             investment_debate, risk_debate, final_decision, trader_plan,
             raw_state, duration_seconds, market_snapshot, fact_check, bystander_verify)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        ))
        db.commit()
        report_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        task.result = task.result or {}
        task.result['_reportId'] = report_id
        logger.info("报告已保存: id=%d, code=%s", report_id, task.code)

        # ★ 自动运行报告复核并存入DB
        try:
            _run_bystander_verify(db, report_id, task.code, report_id)
        except Exception as e:
            logger.warning("报告复核自动运行失败: %s", e)
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
    if trade_date is None:
        trade_date = date.today().isoformat()

    # Skip if already running / queued for this code
    for t in _tasks.values():
        if t.code == code and t.status in ("running", "pending"):
            logger.info("L2 already queued/running for %s, skipping", code)
            return t.task_id

    # Check queue capacity
    running = sum(1 for v in _tasks_status.values() if v.get("status") == "running")
    queued = sum(1 for v in _tasks_status.values() if v.get("status") == "queued")
    if running >= MAX_CONCURRENT and queued >= MAX_QUEUE:
        logger.warning("L2 queue full, skipping %s", code)
        return None

    task_id = str(uuid.uuid4())[:8]
    name = get_stock_name(code)
    task = AnalysisTask(
        task_id=task_id,
        code=code,
        name=name,
        status="pending",
        started_at=datetime.now().isoformat(),
    )
    _tasks[task_id] = task

    from tasks import run_with_limits

    async def _wrapper(tid, c, td):
        await asyncio.to_thread(run_trading_agents, tid, c, td)

    asyncio.create_task(run_with_limits(task_id, _wrapper, code, trade_date))
    logger.info("🔄 Auto-triggered L2: %s(%s) task=%s", name, code, task_id)
    return task_id
