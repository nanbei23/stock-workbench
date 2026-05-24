"""AI Engine — LLM provider abstraction + evaluation logic.

Provides:
- LLM config reading from settings DB
- L1 rule-engine evaluation (evaluate_suggestion)
- Text-parsing helpers for TradingAgents results
"""

import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ============================================================
# Database helper (sync sqlite3 — same pattern as ai_api.py)
# ============================================================

def _get_db():
    """获取数据库连接"""
    db_path = Path(__file__).parent.parent / "data" / "workbench.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================
# LLM provider config
# ============================================================

def get_llm_config() -> dict:
    """Read LLM provider configuration from settings DB.

    Returns keys: llm_provider, deep_think_model, quick_think_model,
    api_key, custom_endpoint, debate_rounds, risk_rounds.
    """
    db = _get_db()
    try:
        rows = db.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        db.close()


def apply_llm_config_to_ta_config(config: dict) -> dict:
    """Populate a TradingAgents DEFAULT_CONFIG dict with settings from DB.

    Injects provider / model names, sets environment variables for API keys,
    and returns the mutated config dict.
    Supports model_mode: economy (all flash), balanced (flash+pro), flagship (all pro).
    """
    cfg = get_llm_config()
    config["llm_provider"] = cfg.get("llm_provider", "deepseek")
    config["output_language"] = "Chinese"
    config["max_debate_rounds"] = int(cfg.get("debate_rounds", "1"))
    config["max_risk_discuss_rounds"] = int(cfg.get("risk_rounds", "1"))

    # 模型模式：覆盖deep/quick think模型
    model_mode = cfg.get("model_mode", "balanced")
    if model_mode == "economy":
        # 经济模式：全链路Flash
        config["deep_think_llm"] = "deepseek-v4-flash"
        config["quick_think_llm"] = "deepseek-v4-flash"
    elif model_mode == "flagship":
        # 旗舰模式：全链路Pro
        config["deep_think_llm"] = "deepseek-v4-pro"
        config["quick_think_llm"] = "deepseek-v4-pro"
    else:
        # 均衡模式（默认）：Flash分析 + Pro裁决
        config["deep_think_llm"] = cfg.get("deep_think_model", "deepseek-v4-pro")
        config["quick_think_llm"] = cfg.get("quick_think_model", "deepseek-v4-flash")

    # Inject API key and custom endpoint into environment
    import os
    _api_key = cfg.get("api_key", "")
    if _api_key:
        _provider = config["llm_provider"].upper()
        os.environ[f"{_provider}_API_KEY"] = _api_key
    _endpoint = cfg.get("custom_endpoint", "")
    if _endpoint:
        os.environ[f"{config['llm_provider'].upper()}_API_BASE"] = _endpoint

    return config


# ============================================================
# Text-parsing helpers (for TradingAgents output)
# ============================================================

def strip_think(text: str) -> str:
    """清理DeepSeek的<think>标签"""
    if not text:
        return ""
    return re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL).strip()


def extract_signal(text: str) -> str:
    """从文本中提取交易信号"""
    if not text:
        return "HOLD"
    text_upper = text.upper()
    if any(kw in text_upper for kw in ["SELL", "卖出", "清仓", "减仓", "平仓"]):
        if "不卖出" in text or "不建议卖出" in text or "不急于卖出" in text:
            return "HOLD"
        return "SELL"
    if any(kw in text_upper for kw in ["BUY", "买入", "建仓", "加仓", "开仓"]):
        if "不买入" in text or "不建议买入" in text:
            return "HOLD"
        return "BUY"
    return "HOLD"


def extract_target_price(text: str) -> Optional[float]:
    """从文本中提取目标价"""
    if not text:
        return None
    patterns = [
        r'目标价[：:]\s*[¥￥]?\s*(\d+\.?\d*)',
        r'目标价位[：:]\s*[¥￥]?\s*(\d+\.?\d*)',
        r'[¥￥]\s*(\d+\.?\d*)\s*[（(].*?目标',
        r'Target.*?[¥￥]?\s*(\d+\.?\d*)',
        r'(?:卖出|高抛|减仓)[（(]?[^）)]*?[）)]?\s*(?:点|价)[：:]*\s*(?:若.*?至)?\s*(\d+\.?\d*)\s*元',
        r'(?:反弹|回升|涨)[至到]\s*(\d+\.?\d*)\s*元',
        r'(?:买入|低吸|建仓)[（(]?[^）)]*?[）)]?\s*(?:点|价)[：:]*\s*(?:若.*?至)?\s*(\d+\.?\d*)\s*元',
        r'(\d+\.?\d*)\s*元.*?(?:目标|卖出|减仓)',
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            val = float(m.group(1))
            if 0.1 < val < 10000:
                return val
    return None


def extract_confidence(text: str) -> Optional[float]:
    """从文本中提取置信度(0-1)"""
    if not text:
        return None
    patterns = [
        r'置信度[：:]\s*(\d+\.?\d*)\s*%',
        r'置信度[：:]\s*(\d+\.?\d*)',
        r'confidence.*?(\d+\.?\d*)\s*%',
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            val = float(m.group(1))
            if val > 1:
                return round(val / 100, 2)
            return round(val, 2)
    return None


def extract_risk_score(text: str) -> Optional[float]:
    """从文本中提取风险评分(0-1)"""
    if not text:
        return None
    patterns = [
        r'风险[评得]分[：:]\s*(\d+\.?\d*)\s*%',
        r'风险[：:]\s*(\d+\.?\d*)\s*%',
        r'risk.*?(\d+\.?\d*)\s*%',
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            val = float(m.group(1))
            if val > 1:
                return round(val / 100, 2)
            return round(val, 2)
    return None


def parse_risk_debate(risk_state) -> dict:
    """解析risk_debate_state（可能是JSON字符串或dict）"""
    result = {"aggressive": "", "conservative": "", "neutral": "", "decision": ""}

    if isinstance(risk_state, dict):
        for k in result:
            if k in risk_state:
                result[k] = strip_think(str(risk_state[k]))
        if "judge_decision" in risk_state:
            result["decision"] = strip_think(str(risk_state["judge_decision"]))
        return result

    if isinstance(risk_state, str):
        text = strip_think(risk_state)
        try:
            d = json.loads(text)
            if isinstance(d, dict):
                for k in result:
                    if k in d:
                        result[k] = strip_think(str(d[k]))
                if "judge_decision" in d:
                    result["decision"] = strip_think(str(d["judge_decision"]))
                return result
        except json.JSONDecodeError:
            pass
        result["decision"] = text[:2000]
        return result

    return result


# ============================================================
# L1 rule-engine helpers
# ============================================================

ANOMALY_THRESHOLDS = {
    "change_pct_up": 5.0,
    "change_pct_down": -5.0,
    "volume_ratio": 3.0,
}


def get_watchlist_and_portfolio() -> list[dict]:
    """获取所有需要监控的股票（自选+持仓）"""
    db = _get_db()
    try:
        rows = db.execute("""
            SELECT w.code, w.name, COALESCE(p.total_shares, 0) as total_shares,
                   COALESCE(p.avg_cost, 0) as avg_cost
            FROM watchlist w
            LEFT JOIN portfolio p ON w.code = p.code
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def get_quote(code: str) -> Optional[dict]:
    """从腾讯API获取实时行情"""
    import urllib.request
    try:
        prefix = "sh" if code.startswith("6") else "sz"
        url = f"http://qt.gtimg.cn/q={prefix}{code}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read().decode("gbk")
            parts = data.split("~")
            if len(parts) < 45:
                return None
            return {
                "code": code,
                "name": parts[1],
                "price": float(parts[3]) if parts[3] else 0,
                "prev_close": float(parts[4]) if parts[4] else 0,
                "open": float(parts[5]) if parts[5] else 0,
                "volume": int(parts[6]) if parts[6] else 0,
                "amount": float(parts[37]) if parts[37] else 0,
                "high": float(parts[33]) if parts[33] else 0,
                "low": float(parts[34]) if parts[34] else 0,
                "change_pct": float(parts[32]) if parts[32] else 0,
                "change": float(parts[31]) if parts[31] else 0,
                "turnover": float(parts[38]) if parts[38] else 0,
                "pe": float(parts[39]) if parts[39] else 0,
                "total_market_cap": float(parts[45]) if parts[45] else 0,
            }
    except Exception as e:
        logger.warning("获取行情失败 %s: %s", code, e)
        return None


def get_index_quotes() -> list[dict]:
    """获取大盘指数"""
    import urllib.request
    indices = [
        ("sh000001", "上证指数"),
        ("sz399001", "深证成指"),
        ("sz399006", "创业板指"),
    ]
    results = []
    for code, name in indices:
        try:
            url = f"http://qt.gtimg.cn/q={code}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = resp.read().decode("gbk")
                parts = data.split("~")
                if len(parts) >= 35:
                    results.append({
                        "code": code,
                        "name": name,
                        "price": float(parts[3]) if parts[3] else 0,
                        "change_pct": float(parts[32]) if parts[32] else 0,
                    })
        except Exception as e:
            logger.warning("获取指数失败 %s: %s", code, e)
    return results


def get_strategy_prices(code: str) -> dict:
    """从 strategy_params 和 watchlist 获取策略价格"""
    db = _get_db()
    try:
        result = {"stop_loss": None, "target_sell": None, "target_buy": None, "strategy_state": "watch"}
        row = db.execute(
            "SELECT stop_loss_price, target_sell_price, target_buy_price, strategy_state FROM watchlist WHERE code = ?",
            (code,),
        ).fetchone()
        if row:
            result["stop_loss"] = row["stop_loss_price"]
            result["target_sell"] = row["target_sell_price"]
            result["target_buy"] = row["target_buy_price"]
            result["strategy_state"] = row["strategy_state"] or "watch"
        code6 = code[:6]
        sp = db.execute("SELECT entry_price, drop_pct, target_profit_pct FROM strategy_params WHERE code6 = ?", (code6,)).fetchone()
        if sp:
            ep = sp["entry_price"] or 0
            if ep > 0:
                if result["stop_loss"] is None and sp["drop_pct"]:
                    result["stop_loss"] = round(ep * (1 - sp["drop_pct"] / 100), 2)
                if result["target_sell"] is None and sp["target_profit_pct"]:
                    result["target_sell"] = round(ep * (1 + sp["target_profit_pct"] / 100), 2)
        return result
    finally:
        db.close()


def get_stock_name(code: str) -> str:
    """从数据库获取股票名称"""
    db = _get_db()
    try:
        row = db.execute("SELECT name FROM watchlist WHERE code = ?", (code,)).fetchone()
        return row["name"] if row else code
    finally:
        db.close()


def evaluate_suggestion(stock: dict, quote: dict) -> dict:
    """L1规则引擎：根据持仓状态和行情给出建议（使用策略参数）"""
    code = stock["code"]
    name = stock["name"]
    total_shares = stock.get("total_shares", 0)
    avg_cost = stock.get("avg_cost", 0)
    price = quote.get("price", 0)
    change_pct = quote.get("change_pct", 0)

    sp = get_strategy_prices(code)
    stop_loss_price = sp["stop_loss"]
    target_sell_price = sp["target_sell"]
    target_buy_price = sp["target_buy"]
    strategy_state = sp["strategy_state"]

    suggestion = {
        "code": code,
        "name": name,
        "price": price,
        "change_pct": change_pct,
        "total_shares": total_shares,
        "avg_cost": avg_cost,
        "strategy_state": strategy_state,
        "status": "观望",
        "advice": "—",
        "detail": "",
        "anomaly": None,
    }

    # 计算持仓盈亏
    if total_shares > 0 and avg_cost > 0:
        pnl_pct = (price - avg_cost) / avg_cost * 100
        suggestion["pnl_pct"] = round(pnl_pct, 2)
        suggestion["pnl"] = round((price - avg_cost) * total_shares, 2)

        if stop_loss_price and price <= stop_loss_price:
            suggestion["status"] = "⚠️止损"
            suggestion["advice"] = "立即止损"
            suggestion["detail"] = f"现价{price:.2f}≤止损价{stop_loss_price:.2f}（亏损{pnl_pct:.1f}%）"
        elif pnl_pct <= -20:
            suggestion["status"] = "⚠️止损"
            suggestion["advice"] = "立即止损"
            suggestion["detail"] = f"亏损{pnl_pct:.1f}%，已超过20%止损线"
        elif target_sell_price and price >= target_sell_price:
            suggestion["status"] = "🎯止盈"
            suggestion["advice"] = "考虑减仓"
            suggestion["detail"] = f"现价{price:.2f}≥目标卖出{target_sell_price:.2f}（盈利{pnl_pct:.1f}%）"
        elif pnl_pct >= 15:
            suggestion["status"] = "🎯止盈"
            suggestion["advice"] = "考虑减仓"
            suggestion["detail"] = f"盈利{pnl_pct:.1f}%，可分批止盈"
        else:
            suggestion["status"] = "🔵持仓"
            suggestion["advice"] = "持有观望"
            suggestion["detail"] = f"持仓{total_shares}股，均价{avg_cost:.2f}"

    elif target_buy_price and price:
        if price <= target_buy_price:
            suggestion["status"] = "🟢买入"
            suggestion["advice"] = "到达买入区间"
            suggestion["detail"] = f"现价{price:.2f}≤目标买入{target_buy_price:.2f}"
        elif price <= target_buy_price * 1.03:
            suggestion["status"] = "🟡接近买点"
            suggestion["advice"] = "接近买点"
            suggestion["detail"] = f"现价{price:.2f}距买入价{((price/target_buy_price-1)*100):.1f}%"

    # 涨跌幅异动检查
    if change_pct >= ANOMALY_THRESHOLDS["change_pct_up"]:
        suggestion["anomaly"] = {
            "type": "涨幅异动",
            "level": "🔴",
            "message": f"涨幅+{change_pct:.1f}%触及异动阈值",
        }
    elif change_pct <= ANOMALY_THRESHOLDS["change_pct_down"]:
        suggestion["anomaly"] = {
            "type": "跌幅异动",
            "level": "🟢",
            "message": f"跌幅{change_pct:.1f}%触及异动阈值",
        }

    return suggestion
