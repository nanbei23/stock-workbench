"""异动检测器 — 每60秒检查一次，检测涨跌幅/成交量异常"""
import asyncio
import datetime
import logging
import sqlite3

from config import DB_PATH

logger = logging.getLogger(__name__)

# 异动阈值
ANOMALY_THRESHOLDS = {
    "change_pct_up": 5.0,      # 涨幅异动阈值 %
    "change_pct_down": -5.0,   # 跌幅异动阈值 %
    "volume_ratio": 3.0,       # 放量异动倍数（相对近期）
    "northbound_yi": 5.0,      # 北向资金净流入/流出阈值（亿元）
}


def _get_db():
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    return db


def _safe_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _get_anomaly_thresholds(db) -> dict:
    rows = db.execute(
        "SELECT key, value FROM settings WHERE key IN (?, ?, ?)",
        ("change_threshold", "volume_threshold", "northbound_threshold"),
    ).fetchall()
    settings = {row["key"]: row["value"] for row in rows}
    change = abs(_safe_float(settings.get("change_threshold"), ANOMALY_THRESHOLDS["change_pct_up"]))
    return {
        "change_pct_up": change,
        "change_pct_down": -change,
        "volume_ratio": _safe_float(settings.get("volume_threshold"), ANOMALY_THRESHOLDS["volume_ratio"]),
        "northbound_yi": _safe_float(settings.get("northbound_threshold"), ANOMALY_THRESHOLDS["northbound_yi"]),
    }


def _get_all_watchlist_codes():
    """获取所有自选股 + 持仓股代码"""
    db = _get_db()
    try:
        rows = db.execute(
            "SELECT code, name FROM watchlist "
            "UNION "
            "SELECT code, name FROM portfolio WHERE total_shares > 0"
        ).fetchall()
        return [(r['code'], r['name']) for r in rows]
    finally:
        db.close()


def _has_today_anomaly(db, code: str, anomaly_type: str) -> bool:
    row = db.execute(
        """
        SELECT id
        FROM anomaly_logs
        WHERE code = ?
          AND anomaly_type = ?
          AND date(created_at, 'localtime') = date('now', 'localtime')
        LIMIT 1
        """,
        (code, anomaly_type),
    ).fetchone()
    return row is not None


def _check_strategy_thresholds(code: str, name: str, price: float, db) -> tuple:
    """Check if current price has crossed strategy thresholds from watchlist.
    Returns (anomaly_type, description, severity) or (None, None, None).
    """
    try:
        row = db.execute(
            "SELECT target_buy_price, target_sell_price, stop_loss_price "
            "FROM watchlist WHERE code = ?",
            (code,)
        ).fetchone()
        if not row:
            return None, None, None

        target_buy = row["target_buy_price"]
        target_sell = row["target_sell_price"]
        stop_loss = row["stop_loss_price"]

        if target_buy and price <= target_buy:
            return (
                "策略-到达买入价",
                f"{name}({code}) 现价 {price:.2f} ≤ 目标买入价 {target_buy:.2f}",
                "warning",
            )
        if stop_loss and price <= stop_loss:
            return (
                "策略-触及止损价",
                f"{name}({code}) 现价 {price:.2f} ≤ 止损价 {stop_loss:.2f}",
                "critical",
            )
        if target_sell and price >= target_sell:
            return (
                "策略-到达卖出价",
                f"{name}({code}) 现价 {price:.2f} ≥ 目标卖出价 {target_sell:.2f}",
                "warning",
            )
    except Exception:
        pass
    return None, None, None


def _get_recent_avg_volume(code: str, days: int = 5) -> float:
    """获取近N日平均成交量（手），用于放量检测（mootdx sync）"""
    try:
        from data.kline import get_kline
        klines = get_kline(code, period="day", count=days + 1)
        if not klines or len(klines) < 2:
            return 0
        # 排除最后一条（当天），取前N条计算平均
        recent = klines[:-1] if len(klines) > days else klines
        volumes = [k.get("volume", 0) for k in recent if k.get("volume", 0) > 0]
        if not volumes:
            return 0
        return sum(volumes) / len(volumes)
    except Exception as e:
        logger.debug("获取近期成交量失败 %s: %s", code, e)
        return 0


async def _check_anomalies():
    """异步检测异动"""
    from data.quote import get_batch_quotes

    stocks = _get_all_watchlist_codes()
    if not stocks:
        return []

    codes = [s[0] for s in stocks]
    name_map = {s[0]: s[1] for s in stocks}

    quotes = await get_batch_quotes(codes)
    if not quotes:
        return []

    anomalies = []
    db = _get_db()
    try:
        thresholds = _get_anomaly_thresholds(db)
        # ── 北向资金异动检测 ──
        try:
            from data.signal import get_northbound
            nb_data = await get_northbound()
            if nb_data:
                total_net = (nb_data.get("sh_net", 0) or 0) + (nb_data.get("sz_net", 0) or 0)
                # total_net 单位: 万元；设置项单位是亿元。
                if abs(total_net) >= thresholds["northbound_yi"] * 10000:
                    direction = "流入" if total_net >= 0 else "流出"
                    nb_desc = f"北向资金大幅{direction}: {total_net/10000:.1f}亿元"
                    if not _has_today_anomaly(db, "000000", "northbound_active"):
                        db.execute(
                            "INSERT INTO anomaly_logs (code, name, anomaly_type, description, severity) "
                            "VALUES (?, ?, ?, ?, ?)",
                            ("000000", "北向资金", "northbound_active", nb_desc, "info")
                        )
                        anomalies.append({
                            "code": "000000",
                            "name": "北向资金",
                            "type": "northbound_active",
                            "change_pct": 0,
                            "price": 0,
                        })
                        logger.info("ℹ️ 北向资金异动: %s", nb_desc)
        except Exception as e:
            logger.debug("北向资金检测跳过: %s", e)

        for code, name in stocks:
            q = quotes.get(code)
            if not q:
                continue

            price = q.get('price', 0)
            change_pct = q.get('change_pct', 0)
            volume = q.get('volume', 0)  # 手

            anomaly_type = None
            description = None
            severity = "warning"

            # 涨跌幅异动
            if change_pct >= thresholds["change_pct_up"]:
                anomaly_type = "涨幅异动"
                description = f"{name}({code}) 涨幅 +{change_pct:.2f}%，当前价 {price:.2f}"
                severity = "warning"
            # 跌幅异动
            elif change_pct <= thresholds["change_pct_down"]:
                anomaly_type = "跌幅异动"
                description = f"{name}({code}) 跌幅 {change_pct:.2f}%，当前价 {price:.2f}"
                severity = "warning"

            # 涨停检测 (change_pct >= 9.9%)
            if not anomaly_type and change_pct >= 9.9:
                anomaly_type = "涨停"
                description = f"{name}({code}) 涨停 +{change_pct:.2f}%，当前价 {price:.2f}"
                severity = "critical"
            # 跌停检测 (change_pct <= -9.9%)
            elif not anomaly_type and change_pct <= -9.9:
                anomaly_type = "跌停"
                description = f"{name}({code}) 跌停 {change_pct:.2f}%，当前价 {price:.2f}"
                severity = "critical"

            # 策略阈值突破检测
            if not anomaly_type:
                anomaly_type, description, severity = _check_strategy_thresholds(
                    code, name, price, db
                )

            # 放量异动检测（独立于其他异动类型）
            volume_spike_logged = False
            if volume and volume > 0:
                avg_vol = await asyncio.to_thread(_get_recent_avg_volume, code)
                if avg_vol > 0:
                    vol_ratio = volume / avg_vol
                    if vol_ratio >= thresholds["volume_ratio"]:
                        vol_desc = f"{name}({code}) 成交量 {volume}手，近5日均量 {avg_vol:.0f}手，倍率 {vol_ratio:.1f}x"
                        if not _has_today_anomaly(db, code, "volume_spike"):
                            db.execute(
                                "INSERT INTO anomaly_logs (code, name, anomaly_type, description, severity) "
                                "VALUES (?, ?, ?, ?, ?)",
                                (code, name, "volume_spike", vol_desc, "warning")
                            )
                            anomalies.append({
                                "code": code,
                                "name": name,
                                "type": "volume_spike",
                                "change_pct": change_pct,
                                "price": price,
                                "volume": volume,
                                "avg_volume": round(avg_vol),
                                "volume_ratio": round(vol_ratio, 1),
                            })
                            volume_spike_logged = True
                            logger.info("⚠️ 异动检测: %s", vol_desc)

            if anomaly_type:
                # 同一交易日同股票同类型异动只记录一次，避免每分钟重复刷屏。
                if not _has_today_anomaly(db, code, anomaly_type):
                    db.execute(
                        "INSERT INTO anomaly_logs (code, name, anomaly_type, description, severity) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (code, name, anomaly_type, description, severity)
                    )
                    # 策略异动时更新 strategy_state_updated_at
                    if anomaly_type and anomaly_type.startswith("策略"):
                        db.execute(
                            "UPDATE watchlist SET strategy_state_updated_at = ? WHERE code = ?",
                            (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), code)
                        )
                    anomalies.append({
                        "code": code,
                        "name": name,
                        "type": anomaly_type,
                        "change_pct": change_pct,
                        "price": price,
                    })
                    logger.info("⚠️ 异动检测: %s", description)

        db.commit()
    except Exception as e:
        logger.error("异动检测失败: %s", e)
    finally:
        db.close()

    return anomalies


async def check_anomalies():
    """异步入口 — 检测异动 + 自动触发L2深度分析"""
    try:
        anomalies = await _check_anomalies()
        if anomalies:
            logger.info("🔔 本轮共检测到 %d 条异动", len(anomalies))
            # Auto-trigger L2 for high-severity anomalies
            await _trigger_l2_for_severe_anomalies(anomalies)
    except Exception as e:
        logger.error("异动检测任务异常: %s", e)


async def _trigger_l2_for_severe_anomalies(anomalies: list):
    """Trigger L2 for stocks with severe anomalies (|change%| >= 7).
    Uses fire-and-forget create_task so the anomaly checker is never blocked.
    """
    from services.ai_analysis_service import trigger_l2_for_stock
    import datetime

    trade_date = datetime.datetime.now().strftime('%Y-%m-%d')
    triggered = 0

    for a in anomalies:
        change_pct = abs(a.get("change_pct", 0))
        if change_pct >= 7:
            task_id = await trigger_l2_for_stock(a["code"], trade_date)
            if task_id:
                triggered += 1
                logger.info(
                    "🔬 异动触发L2: %s(%s) %.1f%%",
                    a["name"], a["code"], a.get("change_pct", 0),
                )

    if triggered:
        logger.info("🔔 异动触发了 %d 个L2深度分析", triggered)
