#!/usr/bin/env python3
"""Diagnose occasional blank K-line charts on deployment machines.

This script is read-only. It checks the same backend data path used by
/api/kline/{code}, optionally compares it with the running HTTP API, and writes
JSON + Markdown evidence for later analysis.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sqlite3
import sys
import time
import traceback
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.kline import get_kline  # noqa: E402


DEFAULT_PERIODS = ["m1", "day", "week", "month"]
INTRADAY_PERIODS = {"m1", "m5", "15", "30", "60"}
REQUIRED_FIELDS = ["date", "open", "high", "low", "close", "volume", "amount"]


@dataclass
class CheckResult:
    source: str
    code: str
    period: str
    count_requested: int
    ok: bool
    row_count: int = 0
    elapsed_ms: int = 0
    first_date: str = ""
    last_date: str = ""
    first_close: float | None = None
    last_close: float | None = None
    price_min: float | None = None
    price_max: float | None = None
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sample_head: list[dict[str, Any]] = field(default_factory=list)
    sample_tail: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""


def _pure_code(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[-6:]


def _is_finite_number(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _frontend_time_value(date_value: Any, period: str) -> int | str:
    """Mirror static/js/chart.js parseTime enough to detect blank-chart input."""
    if not date_value:
        return ""
    text = str(date_value).strip()
    if period in INTRADAY_PERIODS:
        iso = text if "T" in text else text.replace(" ", "T")
        try:
            dt = datetime.fromisoformat(iso)
            return int(dt.timestamp())
        except ValueError:
            return text
    return text.split(" ")[0]


def _sort_key(time_value: int | str) -> tuple[int, Any]:
    if isinstance(time_value, int):
        return (0, time_value)
    return (1, str(time_value))


def validate_rows(rows: list[dict[str, Any]], *, code: str, period: str, count: int, source: str, elapsed_ms: int = 0) -> CheckResult:
    result = CheckResult(
        source=source,
        code=code,
        period=period,
        count_requested=count,
        ok=True,
        row_count=len(rows),
        elapsed_ms=elapsed_ms,
    )
    if not rows:
        result.ok = False
        result.issues.append("empty_rows: K-line source returned no rows")
        return result

    result.sample_head = rows[:3]
    result.sample_tail = rows[-3:]
    result.first_date = str(rows[0].get("date", ""))
    result.last_date = str(rows[-1].get("date", ""))
    result.first_close = _as_float(rows[0].get("close"))
    result.last_close = _as_float(rows[-1].get("close"))

    prices: list[float] = []
    time_values: list[int | str] = []
    missing_fields = 0
    non_numeric = 0
    invalid_ohlc = 0
    zero_or_negative_price = 0
    zero_volume = 0

    for idx, row in enumerate(rows):
        missing = [field_name for field_name in REQUIRED_FIELDS if field_name not in row]
        if missing:
            missing_fields += 1
            if missing_fields <= 5:
                result.issues.append(f"row_{idx}_missing_fields: {','.join(missing)}")

        o = _as_float(row.get("open"))
        h = _as_float(row.get("high"))
        lo = _as_float(row.get("low"))
        c = _as_float(row.get("close"))
        vol = _as_float(row.get("volume"))
        if any(value is None for value in [o, h, lo, c]) or not _is_finite_number(row.get("volume")):
            non_numeric += 1
            if non_numeric <= 5:
                result.issues.append(f"row_{idx}_non_numeric_ohlcv: {row}")
            continue

        assert o is not None and h is not None and lo is not None and c is not None
        prices.extend([o, h, lo, c])
        if min(o, h, lo, c) <= 0:
            zero_or_negative_price += 1
        if h < max(o, c, lo) or lo > min(o, c, h):
            invalid_ohlc += 1
            if invalid_ohlc <= 5:
                result.issues.append(f"row_{idx}_invalid_ohlc: open/high/low/close={o}/{h}/{lo}/{c}")
        if vol is not None and vol <= 0:
            zero_volume += 1

        time_value = _frontend_time_value(row.get("date"), period)
        if time_value == "":
            result.issues.append(f"row_{idx}_invalid_date_for_frontend: {row.get('date')!r}")
        time_values.append(time_value)

    if prices:
        result.price_min = min(prices)
        result.price_max = max(prices)

    if missing_fields > 5:
        result.issues.append(f"missing_fields_extra_rows: {missing_fields - 5}")
    if non_numeric > 5:
        result.issues.append(f"non_numeric_extra_rows: {non_numeric - 5}")
    if invalid_ohlc > 5:
        result.issues.append(f"invalid_ohlc_extra_rows: {invalid_ohlc - 5}")
    if zero_or_negative_price:
        result.issues.append(f"zero_or_negative_price_rows: {zero_or_negative_price}")

    duplicate_count = len(time_values) - len(set(time_values))
    if duplicate_count:
        result.issues.append(f"duplicate_frontend_time_values: {duplicate_count}")

    out_of_order = 0
    for prev, current in zip(time_values, time_values[1:]):
        if _sort_key(current) <= _sort_key(prev):
            out_of_order += 1
    if out_of_order:
        result.issues.append(f"non_increasing_frontend_time_values: {out_of_order}")

    if len(rows) < min(count, 20):
        result.warnings.append(f"short_series: got {len(rows)} rows for requested {count}")
    if zero_volume == len(rows):
        result.warnings.append("all_volume_zero")
    elif zero_volume > max(3, len(rows) // 2):
        result.warnings.append(f"many_zero_volume_rows: {zero_volume}")

    result.ok = not result.issues
    return result


def fetch_direct(code: str, period: str, count: int) -> CheckResult:
    start = time.perf_counter()
    try:
        rows = get_kline(code, period, count)
        elapsed = int((time.perf_counter() - start) * 1000)
        return validate_rows(rows, code=code, period=period, count=count, source="direct_get_kline", elapsed_ms=elapsed)
    except Exception as exc:
        return CheckResult(
            source="direct_get_kline",
            code=code,
            period=period,
            count_requested=count,
            ok=False,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
            issues=["exception"],
            error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
        )


def fetch_http(base_url: str, code: str, period: str, count: int, timeout: float) -> CheckResult:
    start = time.perf_counter()
    query = urllib.parse.urlencode({"period": period, "count": count})
    url = f"{base_url.rstrip('/')}/api/kline/{urllib.parse.quote(code)}?{query}"
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "stock-workbench-kline-diagnose/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        rows = payload.get("klines") or []
        elapsed = int((time.perf_counter() - start) * 1000)
        result = validate_rows(rows, code=code, period=period, count=count, source="http_api", elapsed_ms=elapsed)
        if int(payload.get("count") or 0) != len(rows):
            result.warnings.append(f"http_count_mismatch: payload_count={payload.get('count')} rows={len(rows)}")
        return result
    except Exception as exc:
        return CheckResult(
            source="http_api",
            code=code,
            period=period,
            count_requested=count,
            ok=False,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
            issues=["exception"],
            error=f"{type(exc).__name__}: {exc}",
        )


def load_watchlist_codes(db_path: Path, limit: int = 0) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT code
            FROM watchlist
            ORDER BY COALESCE(sort_order, 0), code
            """
        ).fetchall()
    codes = [_pure_code(row[0]) for row in rows if _pure_code(row[0])]
    return codes[:limit] if limit > 0 else codes


def parse_codes(value: str) -> list[str]:
    codes = []
    for part in str(value or "").replace("\n", ",").split(","):
        code = _pure_code(part)
        if code and code not in codes:
            codes.append(code)
    return codes


def compare_sources(results: list[CheckResult]) -> list[str]:
    notes: list[str] = []
    by_key: dict[tuple[str, str], list[CheckResult]] = {}
    for result in results:
        by_key.setdefault((result.code, result.period), []).append(result)
    for (code, period), group in sorted(by_key.items()):
        direct = next((item for item in group if item.source == "direct_get_kline"), None)
        http = next((item for item in group if item.source == "http_api"), None)
        if not direct or not http:
            continue
        if direct.row_count != http.row_count:
            notes.append(f"{code} {period}: direct/http row count differs ({direct.row_count} vs {http.row_count})")
        if direct.last_date and http.last_date and direct.last_date != http.last_date:
            notes.append(f"{code} {period}: direct/http last_date differs ({direct.last_date} vs {http.last_date})")
        if direct.ok and not http.ok:
            notes.append(f"{code} {period}: direct source ok but HTTP API failed")
        if http.ok and not direct.ok:
            notes.append(f"{code} {period}: HTTP API ok but direct source failed")
    return notes


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# K-line Blank Chart Diagnostic",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        f"- db_path: `{payload['db_path']}`",
        f"- base_url: `{payload.get('base_url') or ''}`",
        f"- codes: `{', '.join(payload['codes'])}`",
        f"- periods: `{', '.join(payload['periods'])}`",
        "",
        "## Summary",
        "",
    ]
    summary = payload["summary"]
    lines.extend(
        [
            f"- checks: `{summary['total_checks']}`",
            f"- failed: `{summary['failed_checks']}`",
            f"- warnings: `{summary['warning_checks']}`",
            "",
        ]
    )
    if payload["source_compare_notes"]:
        lines.append("## Source Compare Notes")
        lines.append("")
        for note in payload["source_compare_notes"]:
            lines.append(f"- {note}")
        lines.append("")

    lines.append("## Checks")
    lines.append("")
    lines.append("| source | code | period | ok | rows | first | last | ms | issues | warnings |")
    lines.append("| --- | --- | --- | --- | ---: | --- | --- | ---: | --- | --- |")
    for item in payload["results"]:
        issues = "<br>".join(item["issues"]) if item["issues"] else ""
        warnings = "<br>".join(item["warnings"]) if item["warnings"] else ""
        lines.append(
            f"| {item['source']} | {item['code']} | {item['period']} | {item['ok']} | "
            f"{item['row_count']} | {item['first_date']} | {item['last_date']} | "
            f"{item['elapsed_ms']} | {issues} | {warnings} |"
        )
    lines.append("")
    lines.append("## How To Read")
    lines.append("")
    lines.append("- `empty_rows` means the backend data source returned no K-line rows.")
    lines.append("- `duplicate_frontend_time_values` or `non_increasing_frontend_time_values` can make Lightweight Charts render blank or unstable series.")
    lines.append("- If `direct_get_kline` is OK but `http_api` fails, focus on the running web process/environment.")
    lines.append("- If both sources fail for `day/week/month`, focus on mootdx TCP connectivity/data source.")
    lines.append("- If only `m1/m5/15/30/60` fails, focus on Tencent minute API/network.")
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose blank K-line chart data issues.")
    parser.add_argument("--codes", default="", help="Comma-separated stock codes, e.g. 600699,000977")
    parser.add_argument("--watchlist", action="store_true", help="Read stock codes from watchlist table")
    parser.add_argument("--db-path", default=str(ROOT / "data" / "workbench.db"))
    parser.add_argument("--limit", type=int, default=0, help="Limit watchlist code count")
    parser.add_argument("--periods", default=",".join(DEFAULT_PERIODS), help="Comma-separated periods: m1,m5,15,30,60,day,week,month")
    parser.add_argument("--count", type=int, default=120)
    parser.add_argument("--base-url", default="", help="Optional running app URL, e.g. http://127.0.0.1:8000")
    parser.add_argument("--http-timeout", type=float, default=15)
    parser.add_argument("--output-dir", default=str(ROOT / "data" / "kline_diagnostics"))
    args = parser.parse_args()

    codes = parse_codes(args.codes)
    db_path = Path(args.db_path)
    if args.watchlist:
        codes.extend(code for code in load_watchlist_codes(db_path, args.limit) if code not in codes)
    if not codes:
        parser.error("provide --codes or --watchlist")

    periods = [part.strip() for part in args.periods.split(",") if part.strip()]
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[CheckResult] = []
    for code in codes:
        for period in periods:
            direct = fetch_direct(code, period, args.count)
            results.append(direct)
            print(f"[direct] {code} {period}: ok={direct.ok} rows={direct.row_count} issues={len(direct.issues)}")
            if args.base_url:
                http = fetch_http(args.base_url, code, period, args.count, args.http_timeout)
                results.append(http)
                print(f"[http]   {code} {period}: ok={http.ok} rows={http.row_count} issues={len(http.issues)}")

    result_dicts = [asdict(item) for item in results]
    summary = {
        "total_checks": len(results),
        "failed_checks": sum(1 for item in results if not item.ok),
        "warning_checks": sum(1 for item in results if item.warnings),
    }
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "db_path": str(db_path),
        "base_url": args.base_url,
        "codes": codes,
        "periods": periods,
        "count": args.count,
        "summary": summary,
        "source_compare_notes": compare_sources(results),
        "results": result_dicts,
    }

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = output_dir / f"kline-diagnostic-{stamp}.json"
    md_path = output_dir / f"kline-diagnostic-{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    print(f"\nJSON: {json_path}")
    print(f"MD:   {md_path}")
    return 1 if summary["failed_checks"] else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
