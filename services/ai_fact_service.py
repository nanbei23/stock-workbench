"""Fact-check and verification workflows for AI reports."""

import json
import logging
import os
import re
from datetime import datetime, timedelta

import httpx
from fastapi import HTTPException

from repositories import ai_fact_repository

logger = logging.getLogger(__name__)


def _get_report_row(report_id: int):
    row = ai_fact_repository.get_report_row(report_id)
    if not row:
        raise HTTPException(404, "报告不存在")
    return row


def extract_numerical_claims(text: str) -> list:
    """Extract numeric claims from a report for legacy fact-check fallback."""
    claims = []
    patterns = [
        (r'(?:PE|市盈率|P/E)[^\d]{0,10}(\d+\.?\d*)\s*倍?', 'PE'),
        (r'(?:PB|市净率|P/B)[^\d]{0,10}(\d+\.?\d*)\s*倍?', 'PB'),
        (r'(?:ROE|净资产收益率)[^\d]{0,10}(\d+\.?\d*)\s*%?', 'ROE'),
        (r'(?:营收|收入|营业收入)[^\d]{0,10}[+]?(\d+\.?\d*)\s*%?', '营收'),
        (r'(?:净利润|净利)[^\d]{0,10}[+]?(\d+\.?\d*)\s*%?', '净利润'),
        (r'(?:毛利率)[^\d]{0,10}(\d+\.?\d*)\s*%?', '毛利率'),
        (r'(?:目标价|目标价位)[^\d]{0,10}[¥￥]?(\d+\.?\d*)', '目标价'),
        (r'(?:现价|股价|收盘价)[^\d]{0,10}[¥￥]?(\d+\.?\d*)', '现价'),
        (r'(?:涨跌幅|涨幅|跌幅)[^\d]{0,10}[+-]?(\d+\.?\d*)\s*%?', '涨跌幅'),
        (r'(?:成交量|成交额)[^\d]{0,10}(\d+\.?\d*)\s*[万亿]?', '成交量'),
        (r'(?:总市值|市值)[^\d]{0,10}(\d+\.?\d*)\s*[万亿]?', '总市值'),
    ]

    for pattern, keyword in patterns:
        for match in re.finditer(pattern, text):
            claims.append({
                "text": match.group(0)[:50],
                "keyword": keyword,
                "value": match.group(1),
                "position": match.start(),
            })

    return claims[:30]


async def get_fact_check(report_id: int):
    """Return stored stage fact-check data or perform legacy regex fallback."""
    from data.info import get_stock_info

    row = _get_report_row(report_id)

    fc_raw = row["fact_check"]
    if fc_raw:
        try:
            fc = json.loads(fc_raw) if isinstance(fc_raw, str) else fc_raw
            if isinstance(fc, dict) and "stages" in fc:
                return fc
        except Exception:
            pass

    code = row["code"]
    report_text = ""
    for col in [
        "market_report",
        "sentiment_report",
        "news_report",
        "fundamentals_report",
        "policy_report",
        "hot_money_report",
        "lockup_report",
        "final_decision",
    ]:
        val = row[col] or ""
        if val:
            report_text += val + "\n"

    claims = extract_numerical_claims(report_text)

    actual_data = {}
    try:
        from data.helpers import tencent_quote_batch
        quotes = await tencent_quote_batch([code])
        q = quotes.get(code, {})
        if q:
            actual_data = {
                "现价": q.get("price"),
                "涨跌幅": q.get("change_pct"),
                "PE": q.get("pe"),
                "总市值": q.get("total_market_cap"),
                "成交量": q.get("volume"),
            }
            actual_data = {k: v for k, v in actual_data.items() if v is not None}
        if actual_data and "PB" not in actual_data:
            try:
                info = await get_stock_info(code)
                if info and info.get("pb"):
                    actual_data["PB"] = info.get("pb")
            except Exception:
                pass
    except Exception:
        pass

    results = []
    for claim in claims:
        matched = False
        actual_val = None
        for key, val in actual_data.items():
            if val and claim["keyword"] in key:
                actual_val = val
                try:
                    diff = abs(float(claim["value"]) - float(val))
                    threshold = max(abs(float(val)) * 0.1, 1)
                    matched = diff < threshold
                except (ValueError, TypeError):
                    matched = False
                break

        results.append({
            "claim": claim["text"],
            "keyword": claim["keyword"],
            "claimed_value": claim["value"],
            "actual_value": actual_val,
            "status": "verified" if matched else ("mismatch" if actual_val else "unverifiable"),
        })

    verified = sum(1 for r in results if r["status"] == "verified")
    mismatched = sum(1 for r in results if r["status"] == "mismatch")
    unverifiable = sum(1 for r in results if r["status"] == "unverifiable")
    total = len(results)

    return {
        "report_id": report_id,
        "code": code,
        "total_claims": total,
        "verified": verified,
        "mismatched": mismatched,
        "unverifiable": unverifiable,
        "accuracy": round(verified / max(total - unverifiable, 1) * 100, 1),
        "claims": results,
    }


def _stage_reports_from_row(row) -> dict:
    stage_report_map = {
        "market": "market_report",
        "social": "sentiment_report",
        "news": "news_report",
        "fundamentals": "fundamentals_report",
        "policy": "policy_report",
        "hot_money": "hot_money_report",
        "lockup": "lockup_report",
    }
    task_stages = {}
    for sid, col in stage_report_map.items():
        val = row[col] or ""
        if val:
            task_stages[sid] = {"report": val}
    return task_stages


async def recheck_report(report_id: int):
    """Rebuild data snapshots, run stage fact-checking, and persist the result."""
    from scheduler.fact_checker import check_all_stages
    from tradingagents.agents.utils.core_stock_tools import get_stock_data
    from tradingagents.agents.utils.technical_indicators_tools import get_indicators
    from tradingagents.agents.utils.fundamental_data_tools import (
        get_balance_sheet,
        get_cashflow,
        get_fundamentals,
    )
    from tradingagents.agents.utils.news_data_tools import (
        get_global_news,
        get_insider_transactions,
        get_news,
    )

    row = _get_report_row(report_id)
    code = row["code"]
    today = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    snapshots = {}

    try:
        sd = get_stock_data.invoke({"symbol": code, "start_date": start, "end_date": today})
        ind = get_indicators.invoke({"symbol": code, "indicator": "all", "curr_date": today})
        from data.helpers import tencent_quote_batch
        quotes = await tencent_quote_batch([code])
        q = quotes.get(code, {})
        snapshots["market"] = f"[get_stock_data]\n{sd}\n\n[get_indicators]\n{ind}\n\n[tencent_quote]\n{json.dumps(q, ensure_ascii=False)}"
    except Exception as e:
        logger.warning("recheck market 失败: %s", e)

    try:
        news = get_news.invoke({"ticker": code, "start_date": start, "end_date": today})
        snapshots["social"] = f"[get_news]\n{news}"
    except Exception as e:
        logger.warning("recheck social 失败: %s", e)

    try:
        news = get_news.invoke({"ticker": code, "start_date": start, "end_date": today})
        gnews = get_global_news.invoke({"curr_date": today})
        snapshots["news"] = f"[get_news]\n{news}\n\n[get_global_news]\n{gnews}"
    except Exception as e:
        logger.warning("recheck news 失败: %s", e)

    try:
        fund = get_fundamentals.invoke({"ticker": code, "curr_date": today})
        bs = get_balance_sheet.invoke({"ticker": code})
        cf = get_cashflow.invoke({"ticker": code})
        snapshots["fundamentals"] = f"[get_fundamentals]\n{fund}\n\n[get_balance_sheet]\n{bs}\n\n[get_cashflow]\n{cf}"
    except Exception as e:
        logger.warning("recheck fundamentals 失败: %s", e)

    try:
        gnews = get_global_news.invoke({"curr_date": today})
        snapshots["policy"] = f"[get_global_news]\n{gnews}"
    except Exception as e:
        logger.warning("recheck policy 失败: %s", e)

    try:
        sd = get_stock_data.invoke({"symbol": code, "start_date": start, "end_date": today})
        news = get_news.invoke({"ticker": code, "start_date": start, "end_date": today})
        insider = get_insider_transactions.invoke({"ticker": code})
        snapshots["hot_money"] = f"[get_stock_data]\n{sd}\n\n[get_news]\n{news}\n\n[get_insider_transactions]\n{insider}"
    except Exception as e:
        logger.warning("recheck hot_money 失败: %s", e)

    try:
        insider = get_insider_transactions.invoke({"ticker": code})
        fund = get_fundamentals.invoke({"ticker": code, "curr_date": today})
        snapshots["lockup"] = f"[get_insider_transactions]\n{insider}\n\n[get_fundamentals]\n{fund}"
    except Exception as e:
        logger.warning("recheck lockup 失败: %s", e)

    if not snapshots:
        return {"error": "无法获取数据快照", "stages_checked": 0}

    db = ai_fact_repository.open_connection()
    try:
        fact_ledger = check_all_stages(snapshots, _stage_reports_from_row(row), db)
    finally:
        db.close()

    ai_fact_repository.update_report_json_column(report_id, "fact_check", fact_ledger)
    return fact_ledger


def _bystander_fact_check_info(row) -> str:
    fact_check_info = "无"
    try:
        if row["fact_check"]:
            fc = json.loads(row["fact_check"])
            if fc.get("stages"):
                lines = [f"总准确率={fc.get('overall_accuracy',0)}% 幻觉={fc.get('total_hallucinations',0)}"]
                for sid, st in fc["stages"].items():
                    lines.append(
                        f"  {sid}: {st.get('accuracy',0)}% "
                        f"(匹配{st.get('matched',0)}/幻觉{st.get('mismatched',0)}/无源{st.get('no_source',0)})"
                    )
                    for h in st.get("hallucinations", [])[:3]:
                        if h.get("status") == "mismatch":
                            lines.append(
                                f"    ⚠ {h['keyword']}: 报告={h.get('claimed_value')} 实际={h.get('snapshot_value')}"
                            )
                fact_check_info = "\n".join(lines)
    except Exception:
        pass
    return fact_check_info


def _bystander_prompt(row) -> tuple[str, str]:
    code = row["code"]
    analyst_sections = {
        "市场技术": row["market_report"] or "",
        "市场情绪": row["sentiment_report"] or "",
        "新闻舆情": row["news_report"] or "",
        "基本面": row["fundamentals_report"] or "",
        "政策分析": row["policy_report"] or "",
        "游资追踪": row["hot_money_report"] or "",
        "解禁监控": row["lockup_report"] or "",
    }
    decision_sections = {
        "多空辩论": row["investment_debate"] or "",
        "风控评估": row["risk_debate"] or "",
        "交易计划": row["trader_plan"] or "",
        "最终决策": row["final_decision"] or "",
    }

    active_analysts = [k for k, v in analyst_sections.items() if v]
    skipped_analysts = [k for k, v in analyst_sections.items() if not v]

    report_text = ""
    for name, content in analyst_sections.items():
        if content:
            report_text += f"\n### {name}\n{content[:400]}\n"
    for name, content in decision_sections.items():
        if content:
            report_text += f"\n### {name}\n{content[:400]}\n"

    signal = row["signal"] or "N/A"
    confidence = row["confidence"]
    risk_score = row["risk_score"]
    target_price = None
    try:
        if row["raw_state"]:
            raw = json.loads(row["raw_state"])
            target_price = raw.get("target_price")
    except Exception:
        pass
    conclusion = f"信号={signal}"
    if confidence:
        conclusion += f" 置信度={confidence}"
    if risk_score:
        conclusion += f" 风险评分={risk_score}"
    if target_price:
        conclusion += f" 目标价={target_price}"

    meta = f"已运行分析师({len(active_analysts)}个): {', '.join(active_analysts)}"
    if skipped_analysts:
        meta += f" | 未运行({len(skipped_analysts)}个): {', '.join(skipped_analysts)}"

    prompt = f"""你是A股分析报告的独立复核员。请基于以下完整证据，评估报告质量。

## 分析结论
{conclusion}

## 分析元数据
{meta}

## 完整报告
{report_text[:3500]}

## 事实核查
{_bystander_fact_check_info(row)}

## 评估要求
基于以上证据评估：
1. **逻辑严密性**：各分析师观点是否自洽，结论是否被报告内容支撑
2. **深度充分性**：分析广度（{len(active_analysts)}个分析师）是否足够支撑结论
3. **数据一致性**：报告中引用的数字是否与事实核查结果一致
4. **整体可信度**：综合评分

JSON输出：
{{"hallucinations": [{{"claim": "具体问题", "issue": "说明", "severity": "high/medium/low"}}], "overall_score": 0-100, "summary": "评估结论"}}"""
    return code, prompt


def _get_verify_settings():
    from scheduler.ai_engine import get_llm_config

    cfg = get_llm_config()
    verify_model = cfg.get("verification_model") or "mimo-v2.5-pro"
    verify_endpoint = cfg.get("verification_endpoint", "")
    verify_key = cfg.get("verification_api_key", "")

    api_url = verify_endpoint or "https://token-plan-cn.xiaomimimo.com/v1"
    if not api_url.endswith("/chat/completions"):
        api_url = api_url.rstrip("/") + "/chat/completions"

    api_key = verify_key or os.environ.get("MIMO_API_KEY", "")
    if not api_key:
        try:
            import yaml
            hermes_cfg = os.path.expanduser("~/.hermes/config.yaml")
            with open(hermes_cfg) as f:
                cfg = yaml.safe_load(f)
            for m in cfg.get("custom_providers", []):
                if "mimo" in str(m.get("base_url", "")).lower():
                    if not api_key or m.get("name") == "小米mimo":
                        api_key = m.get("api_key", "")
        except Exception:
            pass

    return {
        "api_key": api_key,
        "api_url": api_url,
        "verify_key": verify_key,
        "verify_model": verify_model,
    }


async def bystander_verify(report_id: int):
    """Run independent model verification for an AI report."""
    row = _get_report_row(report_id)
    code, prompt = _bystander_prompt(row)
    settings = _get_verify_settings()

    if not settings["api_key"]:
        return {
            "error": (
                "未配置API密钥 "
                f"verify_key={bool(settings['verify_key'])} env={bool(os.environ.get('MIMO_API_KEY', ''))}"
            ),
            "status": "skipped",
        }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                settings["api_url"],
                headers={
                    "Authorization": f"Bearer {settings['api_key']}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings["verify_model"],
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 2000,
                    "temperature": 0.3,
                },
            )
        if resp.status_code >= 400 or not resp.text.strip():
            return {"error": f"API请求失败 status={resp.status_code}: {resp.text[:300]}", "status": "failed"}
        resp_data = resp.json()
    except ValueError:
        return {"error": f"API响应非JSON url={settings['api_url']} model={settings['verify_model']}: {resp.text[:300]}", "status": "failed"}
    except Exception as e:
        return {"error": f"API调用失败: {e}", "status": "failed"}

    if "choices" not in resp_data:
        return {"error": f"API响应异常: {json.dumps(resp_data, ensure_ascii=False)[:300]}", "status": "failed"}

    try:
        content = resp_data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        return {"error": f"响应格式异常: {e} | {json.dumps(resp_data, ensure_ascii=False)[:300]}", "status": "failed"}

    try:
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            result = json.loads(json_match.group())
        else:
            result = {"summary": content, "overall_score": 50, "hallucinations": []}

        try:
            ai_fact_repository.update_report_json_column(report_id, "bystander_verify", result)
        except Exception as e:
            logger.warning("bystander save failed: %s", e)

        return {
            "report_id": report_id,
            "code": code,
            "verify_model": settings["verify_model"],
            "result": result,
        }
    except Exception as e:
        return {"error": str(e), "status": "failed"}
