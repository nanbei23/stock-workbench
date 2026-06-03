"""Trading market classification and permission filtering."""

from __future__ import annotations

from typing import Any


MARKETS = {
    "main": {"key": "main", "label": "主板"},
    "gem": {"key": "gem", "label": "创业板"},
    "star": {"key": "star", "label": "科创板"},
    "bse": {"key": "bse", "label": "北交所"},
    "unknown": {"key": "unknown", "label": "未知市场"},
}


def normalize_code(code: str | int | None) -> str:
    raw = "".join(ch for ch in str(code or "").strip() if ch.isdigit())
    return raw[-6:] if len(raw) >= 6 else raw


def classify_stock_market(code: str | int | None) -> dict[str, str]:
    code6 = normalize_code(code)
    if code6.startswith(("688", "689")):
        return MARKETS["star"].copy()
    if code6.startswith(("300", "301")):
        return MARKETS["gem"].copy()
    if code6.startswith(("8", "4", "920")):
        return MARKETS["bse"].copy()
    if code6.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        return MARKETS["main"].copy()
    return MARKETS["unknown"].copy()


def allowed_market_keys(settings: dict[str, Any] | None = None) -> set[str]:
    settings = settings or {}
    allowed = set()
    for key in ("main", "gem", "star", "bse"):
        value = settings.get(f"trade_market_{key}", "true")
        if value is True or str(value).lower() == "true":
            allowed.add(key)
    allowed.add("unknown")
    return allowed


def is_stock_allowed(code: str | int | None, settings: dict[str, Any] | None = None) -> bool:
    return classify_stock_market(code)["key"] in allowed_market_keys(settings)


def filter_allowed_stocks(
    stocks: list[dict[str, Any]],
    *,
    settings: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    allowed = allowed_market_keys(settings)
    kept: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for stock in stocks:
        market = classify_stock_market(stock.get("code"))
        enriched = {**stock, "market_key": market["key"], "market_label": market["label"]}
        if market["key"] in allowed:
            kept.append(enriched)
        else:
            excluded.append(enriched)
    return kept, excluded
