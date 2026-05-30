"""
逐阶段事实核对 — 用旁观者模型对比数据快照与分析师报告。

核心原则：
- 旁观者模型 ≠ 生成报告的模型（独立审计）
- 只做数字比对，不做解读
- 每个分析师阶段独立核对
"""
import json
import logging
import os
import re
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

# 阶段中文名映射
STAGE_NAMES = {
    "market": "技术分析",
    "social": "情绪分析",
    "news": "新闻舆情",
    "fundamentals": "基本面",
    "policy": "政策分析",
    "hot_money": "游资追踪",
    "lockup": "解禁监控",
}

# ============================================================
# 核对 prompt 模板
# ============================================================

FACT_CHECK_PROMPT = """你是独立数据审计员。你的唯一任务是比对【数据快照】和【分析师报告】中的数字。

## 严格规则
1. 从报告中提取每一个数据快照中**直接出现**的原始数字（价格、PE、ROE、涨跌幅、成交量、市值、百分比等）
2. 逐个和数据快照中的原始数据比对
3. 差异超过1%即标记为"mismatch"（数据幻觉）
4. 报告中出现了数据快照里没有的数字 → 标记为"no_source"（无源数据）
5. 数字完全匹配 → 标记为"match"
6. 禁止理解、解释、补充上下文 —— 只做数字比对
7. 如果数据快照为空或无有效数据，所有提取的数字都标记为"no_source"
8. **跳过所有推导计算值**——包括但不限于：通过两个原始值相除/相加/相减得出的值（如流通比例=流通股本÷总股本）、同比增速、环比变化、隐含利润（市值÷PE）、推算比率等。只核对数据快照中直接出现的原始数字，不核对任何需要二次计算才能得到的数字
9. **公司名称核对**——报告中出现的股票代码必须搭配正确的公司名称。如果数据快照中提供了「股票代码→公司名称」映射，报告中使用了错误的公司名称则标记为"mismatch"（如代码300673对应"佩蒂股份"，但报告写成了"天津普林"）

## 输出格式（严格JSON，不要其他文字）
```json
{{
  "stage": "{stage_name}",
  "accuracy": 0-100,
  "total_claims": 数字总数,
  "matched": 匹配数,
  "mismatched": 幻觉数,
  "no_source": 无源数,
  "hallucinations": [
    {{"claim": "报告中的原文片段", "keyword": "PE/现价/涨跌幅/...", "claimed_value": "报告中的值", "snapshot_value": "快照中的值或null", "status": "match/mismatch/no_source"}}
  ],
  "summary": "一句话结论"
}}
```

## 数据快照（{stage_name}阶段的原始数据）
{snapshot_text}

## 分析师报告（{stage_name}阶段）
{report_text}

请输出严格JSON："""


def _get_verify_config(db) -> dict:
    """从DB读取旁观者核对模型配置。"""
    cfg = {}
    for r in db.execute(
        "SELECT key, value FROM settings WHERE key LIKE 'verification_%'"
    ).fetchall():
        cfg[r["key"]] = r["value"]

    verify_model = cfg.get("verification_model") or "mimo-v2.5-pro"
    verify_endpoint = cfg.get("verification_endpoint", "")
    verify_key = cfg.get("verification_api_key", "")

    api_url = verify_endpoint or "https://token-plan-cn.xiaomimimo.com/v1"
    if not api_url.endswith("/chat/completions"):
        api_url = api_url.rstrip("/") + "/chat/completions"

    # 密钥优先级：设置页 → 环境变量 → Hermes配置
    api_key = verify_key
    if not api_key:
        api_key = os.environ.get("MIMO_API_KEY", "")
    if not api_key:
        try:
            import yaml
            hermes_cfg = os.path.expanduser("~/.hermes/config.yaml")
            with open(hermes_cfg) as f:
                hc = yaml.safe_load(f)
            for m in hc.get("custom_providers", []):
                if "mimo" in str(m.get("base_url", "")).lower():
                    if not api_key or m.get("name") == "小米mimo":
                        api_key = m.get("api_key", "")
        except Exception:
            pass

    return {
        "model": verify_model,
        "api_url": api_url,
        "api_key": api_key,
    }


def _call_verify_model(prompt: str, config: dict) -> dict | None:
    """调用旁观者模型，返回解析后的JSON或None。"""
    if not config.get("api_key"):
        logger.warning("旁观者模型未配置API密钥，跳过核对")
        return None

    try:
        resp = httpx.post(
            config["api_url"],
            headers={"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"},
            json={
                "model": config["model"],
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 2000,
                "temperature": 0.1,
            },
            timeout=90,
        )
        if resp.status_code >= 400 or not resp.text.strip():
            logger.warning("旁观者模型请求失败: status=%s body=%s", resp.status_code, resp.text[:200])
            return None

        data = resp.json()
        if "choices" not in data:
            logger.warning("旁观者模型响应异常: %s", json.dumps(data, ensure_ascii=False)[:300])
            return None

        content = data["choices"][0]["message"]["content"]
        # 提取JSON（可能被```json```包裹）
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            raw = json_match.group()
            # 尝试直接解析，失败则尝试修复常见问题
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                # 尝试去掉尾部多余逗号
                cleaned = re.sub(r',\s*([}\]])', r'\1', raw)
                try:
                    return json.loads(cleaned)
                except json.JSONDecodeError:
                    logger.warning("旁观者模型JSON解析失败: %s", raw[:300])
                    return None
        logger.warning("旁观者模型返回非JSON: %s", content[:200])
        return None

    except Exception as e:
        logger.warning("旁观者模型调用失败: %s", e)
        return None


def check_stage(stage_id: str, snapshot_text: str, report_text: str, db) -> dict | None:
    """对单个分析师阶段进行事实核对。

    Args:
        stage_id: 阶段ID (market, social, news, ...)
        snapshot_text: 该阶段的数据快照文本（工具原始返回）
        report_text: 该阶段的分析师报告文本
        db: 数据库连接（用于读取配置）

    Returns:
        核对结果dict，或None（跳过/失败）
    """
    if not snapshot_text or not report_text:
        logger.info("阶段 %s: 快照或报告为空，跳过核对", stage_id)
        return None

    if len(snapshot_text.strip()) < 50 or len(report_text.strip()) < 50:
        logger.info("阶段 %s: 快照或报告过短，跳过核对", stage_id)
        return None

    stage_name = STAGE_NAMES.get(stage_id, stage_id)
    config = _get_verify_config(db)

    if not config.get("api_key"):
        logger.warning("阶段 %s: 旁观者模型未配置，跳过核对", stage_id)
        return None

    prompt = FACT_CHECK_PROMPT.format(
        stage_name=stage_name,
        snapshot_text=snapshot_text[:4000],  # 限制长度避免超token
        report_text=report_text[:4000],
    )

    logger.info("阶段 %s: 调用旁观者模型核对 (model=%s, snapshot=%d chars, report=%d chars)",
                stage_id, config["model"], len(snapshot_text), len(report_text))

    result = _call_verify_model(prompt, config)
    if result:
        result["stage"] = stage_id
        result["checked_at"] = datetime.now().isoformat()
        logger.info("阶段 %s: 核对完成 accuracy=%s matched=%s mismatched=%s",
                     stage_id, result.get("accuracy"), result.get("matched"), result.get("mismatched"))
    else:
        logger.warning("阶段 %s: 核对失败或返回无效结果", stage_id)

    return result


def check_all_stages(data_snapshot: dict[str, str], task_stages: dict, db) -> dict:
    """对所有完成的分析师阶段进行事实核对，汇总为事实账本。

    Args:
        data_snapshot: {stage_id: snapshot_text} — 七层数据快照
        task_stages: task.stages dict — 包含每个阶段的report
        db: 数据库连接

    Returns:
        事实账本dict:
        {
            "stages": {stage_id: check_result, ...},
            "overall_accuracy": float,
            "total_hallucinations": int,
            "checked_at": "ISO时间",
        }
    """
    analyst_stages = ["market", "social", "news", "fundamentals", "policy", "hot_money", "lockup"]
    results = {}

    for stage_id in analyst_stages:
        snapshot_text = data_snapshot.get(stage_id, "")
        report_text = task_stages.get(stage_id, {}).get("report", "")

        if not snapshot_text:
            logger.info("阶段 %s: 无数据快照，跳过", stage_id)
            continue
        if not report_text:
            logger.info("阶段 %s: 无报告，跳过", stage_id)
            continue

        result = check_stage(stage_id, snapshot_text, report_text, db)
        if result:
            results[stage_id] = result

    # 汇总
    accuracies = [r["accuracy"] for r in results.values() if r.get("accuracy") is not None]
    total_hallucinations = sum(r.get("mismatched", 0) for r in results.values())
    total_no_source = sum(r.get("no_source", 0) for r in results.values())

    fact_ledger = {
        "stages": results,
        "overall_accuracy": round(sum(accuracies) / len(accuracies), 1) if accuracies else 0,
        "total_hallucinations": total_hallucinations,
        "total_no_source": total_no_source,
        "checked_stages": len(results),
        "checked_at": datetime.now().isoformat(),
    }

    logger.info("事实账本汇总: %d个阶段核对, 整体准确率=%.1f%%, 幻觉=%d, 无源=%d",
                len(results), fact_ledger["overall_accuracy"],
                total_hallucinations, total_no_source)

    return fact_ledger
