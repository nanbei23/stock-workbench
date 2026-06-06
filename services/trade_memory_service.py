"""Trade review memory cards for self-improving account guidance."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

import models.database as database
from services import model_provider_resolver

TRADE_MEMORY_VERSION = "trade-memory-v2"
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536
EMBEDDING_ENDPOINT = "https://api.openai.com/v1/embeddings"
VECTOR_RECALL_LIMIT = 50
RISK_MEMORY_TAGS = {"涨停", "追涨", "高估值", "仓位过重", "信号冲突"}

SCENARIO_KEYWORDS = {
    "涨停": ("涨停", "打板", "一字板"),
    "追涨": ("追入", "追涨", "高位买入", "突破追"),
    "高估值": ("高估值", "PE高", "pe高", "PE>50", "pe>50", "估值贵"),
    "仓位过重": ("仓位过重", "重仓", "满仓", "单笔买入金额过大"),
    "信号冲突": ("信号冲突", "AI卖出", "SELL", "STRONG_SELL", "UNDERWEIGHT", "卖出信号"),
    "低估值": ("低估值", "PE<25", "pe<25", "估值便宜", "便宜"),
    "主线": ("主线", "产业逻辑", "AI服务器", "供应链", "催化"),
    "左侧": ("左侧", "抄底", "下跌末期", "底部区间"),
    "回踩": ("回踩", "缩量", "不破支撑", "MA20", "MA10"),
    "止盈": ("止盈", "估值修复", "目标价", "兑现"),
}


class OpenAIEmbeddingProvider:
    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str = EMBEDDING_ENDPOINT,
        model: str = EMBEDDING_MODEL,
        dimensions: int = EMBEDDING_DIMENSIONS,
        timeout: float = 30.0,
    ):
        self.api_key = api_key
        self.endpoint = _normalize_embedding_endpoint(endpoint)
        self.model = model or EMBEDDING_MODEL
        self.dimensions = int(dimensions or EMBEDDING_DIMENSIONS)
        self.timeout = timeout

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not self.api_key:
            raise ValueError("OpenAI embedding api_key required")
        if not texts:
            return []
        payload: dict[str, Any] = {
            "model": self.model,
            "input": texts,
            "encoding_format": "float",
            "dimensions": self.dimensions,
        }
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        rows = sorted(data.get("data") or [], key=lambda item: int(item.get("index") or 0))
        return [list(row.get("embedding") or []) for row in rows]


def _db_path(path: Path | None = None) -> Path:
    return path or database.DB_PATH


def _normalize_embedding_endpoint(endpoint: str | None) -> str:
    clean = (endpoint or EMBEDDING_ENDPOINT).strip().rstrip("/")
    if not clean:
        return EMBEDDING_ENDPOINT
    if clean.endswith("/embeddings"):
        return clean
    if clean.endswith("/v1") or clean.endswith("/compatible-mode/v1"):
        return f"{clean}/embeddings"
    return clean


def _loads(value: Any, fallback: Any):
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed is not None else fallback


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("T", " ").split(".")[0]
    for candidate, fmt in ((text[:19], "%Y-%m-%d %H:%M:%S"), (text[:10], "%Y-%m-%d")):
        try:
            return datetime.strptime(candidate, fmt)
        except ValueError:
            continue
    return None


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    return dict(row) if row else {}


def _decode_memory(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    for key, fallback in (
        ("trade_ids_json", []),
        ("facts_json", {}),
        ("lesson_tags_json", []),
        ("rules_json", []),
        ("veto_lessons_json", []),
        ("report_context_json", {}),
    ):
        public_key = key.removesuffix("_json")
        item[public_key] = _loads(item.get(key), fallback)
    return item


def _compact_text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value or "")


def extract_scenario_tags(*values: Any) -> list[str]:
    text = "\n".join(_compact_text(value) for value in values if value not in (None, ""))
    tags: list[str] = []
    for tag, keywords in SCENARIO_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            tags.append(tag)
    return tags


def _memory_scenario_tags(memory: dict[str, Any]) -> list[str]:
    tags = set(extract_scenario_tags(
        memory.get("name"),
        memory.get("summary"),
        memory.get("lesson_tags"),
        memory.get("rules"),
        memory.get("veto_lessons"),
        memory.get("report_context"),
    ))
    for raw in memory.get("lesson_tags") or []:
        text = str(raw).strip()
        if text:
            tags.add(text)
    return sorted(tags)


def _settings_from_db(db_path: Path | None = None) -> dict[str, str]:
    path = _db_path(db_path)
    if not path.exists():
        return {}
    try:
        with sqlite3.connect(str(path)) as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
    except sqlite3.Error:
        return {}
    return {str(key): str(value or "") for key, value in rows}


def _default_embedding_provider(db_path: Path | None = None) -> OpenAIEmbeddingProvider | None:
    settings = _settings_from_db(db_path)
    resolved = model_provider_resolver.resolve_embedding_config(settings, db_path=db_path)
    api_key = (
        resolved.get("api_key")
        or os.environ.get("OPENAI_API_KEY")
        or settings.get("openai_api_key")
        or ""
    ).strip()
    if not api_key:
        return None
    endpoint = (resolved.get("endpoint") or settings.get("embedding_endpoint") or EMBEDDING_ENDPOINT).strip()
    model = (resolved.get("model") or settings.get("embedding_model") or EMBEDDING_MODEL).strip()
    dimensions = int(_num(resolved.get("dimensions") or settings.get("embedding_dimensions"), EMBEDDING_DIMENSIONS) or EMBEDDING_DIMENSIONS)
    return OpenAIEmbeddingProvider(
        api_key=api_key,
        endpoint=endpoint,
        model=model,
        dimensions=dimensions,
    )


def _load_sqlite_vec(conn: sqlite3.Connection) -> None:
    try:
        import sqlite_vec
    except ImportError as exc:
        raise RuntimeError("sqlite-vec is not installed") from exc
    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)


def _ensure_embedding_store(conn: sqlite3.Connection) -> None:
    _load_sqlite_vec(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_memory_embeddings (
            memory_id INTEGER PRIMARY KEY,
            memory_key TEXT NOT NULL,
            model TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            embedding_text TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(memory_id) REFERENCES trade_memories(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS trade_memory_embedding_vec
        USING vec0(embedding float[{EMBEDDING_DIMENSIONS}])
        """
    )


def _embedding_text(memory: dict[str, Any]) -> str:
    sections = [
        f"股票：{memory.get('name') or ''} {memory.get('code') or ''}",
        f"交易闭环：{memory.get('memory_key') or ''}",
        f"结果：{memory.get('outcome') or ''}，盈亏：{memory.get('realized_pnl') or 0}，盈亏比例：{memory.get('realized_pnl_pct') or 0}",
        f"摘要：{memory.get('summary') or ''}",
        "标签：" + "、".join(str(item) for item in memory.get("lesson_tags") or []),
        "规则：" + "；".join(str(item) for item in memory.get("rules") or []),
        "否决：" + "；".join(str(item) for item in memory.get("veto_lessons") or []),
        "报告上下文：" + _compact_text(memory.get("report_context") or {}),
    ]
    return "\n".join(part for part in sections if part.strip())


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _embedding_provider_meta(provider: Any) -> tuple[str, int]:
    model = str(getattr(provider, "model", EMBEDDING_MODEL) or EMBEDDING_MODEL)
    dimensions = int(getattr(provider, "dimensions", EMBEDDING_DIMENSIONS) or EMBEDDING_DIMENSIONS)
    return model, dimensions


def _validate_embedding(vector: list[float], *, dimensions: int = EMBEDDING_DIMENSIONS) -> list[float]:
    if len(vector) != dimensions:
        raise ValueError(f"embedding dimension mismatch: expected {dimensions}, got {len(vector)}")
    return [float(item) for item in vector]


def _embedding_error_payload(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, httpx.HTTPStatusError):
        resp = exc.response
        request_id = resp.headers.get("x-request-id") or resp.headers.get("openai-request-id") or ""
        try:
            data = resp.json()
        except ValueError:
            data = {}
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            message = str(err.get("message") or resp.text or exc)
            return {
                "status": "error",
                "error_type": str(err.get("type") or "http_error"),
                "error_code": str(err.get("code") or ""),
                "http_status": resp.status_code,
                "request_id": request_id,
                "message": message[:1000],
            }
        return {
            "status": "error",
            "error_type": "http_error",
            "error_code": "",
            "http_status": resp.status_code,
            "request_id": request_id,
            "message": f"HTTP {resp.status_code}: {resp.text[:1000]}",
        }
    if isinstance(exc, httpx.TimeoutException):
        return {"status": "error", "error_type": "timeout", "error_code": "", "http_status": None, "request_id": "", "message": "连接超时，请检查网络、代理或 endpoint。"}
    if isinstance(exc, httpx.RequestError):
        return {"status": "error", "error_type": "request_error", "error_code": "", "http_status": None, "request_id": "", "message": str(exc)[:1000]}
    return {"status": "error", "error_type": "connection_error", "error_code": "", "http_status": None, "request_id": "", "message": str(exc)[:1000]}


def test_embedding_connection(
    *,
    api_key: str = "",
    endpoint: str = EMBEDDING_ENDPOINT,
    model: str = EMBEDDING_MODEL,
    dimensions: int = EMBEDDING_DIMENSIONS,
    embedding_provider: Any | None = None,
) -> dict[str, Any]:
    clean_key = str(api_key or "").strip()
    clean_endpoint = _normalize_embedding_endpoint(str(endpoint or "").strip() or EMBEDDING_ENDPOINT)
    clean_model = str(model or EMBEDDING_MODEL).strip() or EMBEDDING_MODEL
    clean_dimensions = int(_num(dimensions, EMBEDDING_DIMENSIONS) or EMBEDDING_DIMENSIONS)
    if not clean_key and embedding_provider is None:
        return {
            "status": "error",
            "error_type": "missing_api_key",
            "error_code": "",
            "http_status": None,
            "request_id": "",
            "message": "Embedding API密钥未配置",
        }
    if clean_dimensions != EMBEDDING_DIMENSIONS:
        return {
            "status": "error",
            "error_type": "unsupported_dimensions",
            "error_code": "",
            "http_status": None,
            "request_id": "",
            "message": f"当前 sqlite-vec 索引只支持 {EMBEDDING_DIMENSIONS} 维，实际配置为 {clean_dimensions} 维。",
        }
    provider = embedding_provider or OpenAIEmbeddingProvider(
        api_key=clean_key,
        endpoint=clean_endpoint,
        model=clean_model,
        dimensions=clean_dimensions,
        timeout=15.0,
    )
    try:
        vectors = provider.embed_texts(["ping"])
        if not vectors:
            raise ValueError("embedding response is empty")
        _validate_embedding(vectors[0], dimensions=clean_dimensions)
        provider_model, provider_dimensions = _embedding_provider_meta(provider)
        return {
            "status": "ok",
            "message": f"连接成功 ({provider_model}, {provider_dimensions}维)",
            "model": provider_model,
            "dimensions": provider_dimensions,
            "embedding_count": len(vectors),
        }
    except Exception as exc:
        return _embedding_error_payload(exc)


def _trade_fee(row: dict[str, Any]) -> float:
    return round(_num(row.get("commission")) + _num(row.get("stamp_tax")) + _num(row.get("transfer_fee")), 3)


def _completed_cycles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cycles: list[dict[str, Any]] = []
    state: dict[tuple[str, str], dict[str, Any]] = {}
    for row in sorted(rows, key=lambda r: (str(r.get("account_id") or "default"), str(r.get("code") or ""), str(r.get("trade_time") or ""), int(r.get("id") or 0))):
        account_id = str(row.get("account_id") or "default")
        code = str(row.get("code") or "")
        if not code:
            continue
        key = (account_id, code)
        direction = str(row.get("direction") or "").lower()
        shares = _num(row.get("shares"))
        amount = _num(row.get("amount"))
        fee = _trade_fee(row)
        current = state.setdefault(
            key,
            {
                "account_id": account_id,
                "code": code,
                "name": row.get("name") or code,
                "shares": 0.0,
                "cost": 0.0,
                "buy_amount": 0.0,
                "sell_amount": 0.0,
                "fees": 0.0,
                "realized_pnl": 0.0,
                "trade_ids": [],
                "opened_at": None,
                "closed_at": None,
                "buy_shares": 0.0,
            },
        )
        if direction == "buy" and shares > 0:
            if current["shares"] <= 0:
                current.update(
                    {
                        "name": row.get("name") or code,
                        "shares": 0.0,
                        "cost": 0.0,
                        "buy_amount": 0.0,
                        "sell_amount": 0.0,
                        "fees": 0.0,
                        "realized_pnl": 0.0,
                        "trade_ids": [],
                        "opened_at": row.get("trade_time"),
                        "closed_at": None,
                        "buy_shares": 0.0,
                    }
                )
            current["shares"] += shares
            current["buy_shares"] += shares
            current["cost"] += amount + fee
            current["buy_amount"] += amount
            current["fees"] += fee
            current["trade_ids"].append(int(row["id"]))
        elif direction == "sell" and shares > 0 and current["shares"] > 0:
            avg_cost = current["cost"] / current["shares"] if current["shares"] else 0.0
            matched = min(shares, current["shares"])
            ratio = matched / shares if shares else 0.0
            sell_fee = fee * ratio
            proceeds = amount * ratio
            current["realized_pnl"] += proceeds - sell_fee - avg_cost * matched
            current["shares"] = max(0.0, current["shares"] - matched)
            current["cost"] = avg_cost * current["shares"]
            current["sell_amount"] += proceeds
            current["fees"] += sell_fee
            current["trade_ids"].append(int(row["id"]))
            current["closed_at"] = row.get("trade_time")
            if current["shares"] <= 0.000001 and current["buy_amount"] > 0:
                opened = _dt(current["opened_at"])
                closed = _dt(current["closed_at"])
                holding_days = round((closed - opened).total_seconds() / 86400, 3) if opened and closed else 0.0
                buy_base = current["buy_amount"] + max(0.0, current["fees"] - sell_fee)
                cycles.append(
                    {
                        "memory_key": f"{account_id}:{code}:{current['trade_ids'][0]}-{current['trade_ids'][-1]}",
                        "account_id": account_id,
                        "code": code,
                        "name": current["name"],
                        "trade_ids": list(current["trade_ids"]),
                        "opened_at": current["opened_at"],
                        "closed_at": current["closed_at"],
                        "holding_days": holding_days,
                        "buy_amount": round(current["buy_amount"], 3),
                        "sell_amount": round(current["sell_amount"], 3),
                        "fees": round(current["fees"], 3),
                        "realized_pnl": round(current["realized_pnl"], 3),
                        "realized_pnl_pct": round(current["realized_pnl"] / buy_base * 100, 3) if buy_base else 0.0,
                    }
                )
                state[key] = {
                    **current,
                    "shares": 0.0,
                    "cost": 0.0,
                    "buy_amount": 0.0,
                    "sell_amount": 0.0,
                    "fees": 0.0,
                    "realized_pnl": 0.0,
                    "trade_ids": [],
                    "opened_at": None,
                    "closed_at": None,
                    "buy_shares": 0.0,
                }
    return cycles


def _fetch_closed_cycles(conn: sqlite3.Connection, account_id: str = "default", code: str | None = None) -> list[dict[str, Any]]:
    params: list[Any] = [account_id]
    where = "WHERE account_id = ?"
    if code:
        where += " AND code = ?"
        params.append(code[:6])
    rows = conn.execute(
        f"""
        SELECT *
        FROM trades
        {where}
        ORDER BY account_id ASC, code ASC, datetime(trade_time) ASC, id ASC
        """,
        params,
    ).fetchall()
    return _completed_cycles([dict(row) for row in rows])


def _latest_report_context(conn: sqlite3.Connection, code: str, *, before_at: str | None = None) -> dict[str, Any]:
    params: list[Any] = [code]
    time_clause = ""
    if before_at:
        time_clause = " AND datetime(created_at) <= datetime(?)"
        params.append(before_at)
    row = conn.execute(
        f"""
        SELECT id, signal, confidence, risk_score, created_at, final_decision
        FROM analysis_reports
        WHERE code = ?{time_clause}
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    if not row:
        return {}
    report = dict(row)
    final_decision = str(report.get("final_decision") or "")
    report["final_decision_preview"] = final_decision[:240]
    report.pop("final_decision", None)
    return report


def _lesson_payload(cycle: dict[str, Any], report_context: dict[str, Any] | None = None) -> dict[str, Any]:
    pnl = _num(cycle.get("realized_pnl"))
    pct = _num(cycle.get("realized_pnl_pct"))
    buy_amount = _num(cycle.get("buy_amount"))
    outcome = "success" if pnl > 0 else "failure" if pnl < 0 else "neutral"
    signal = str((report_context or {}).get("signal") or "").upper()
    lesson_tags: list[str] = []
    rules: list[str] = []
    veto_lessons: list[str] = []
    if outcome == "success":
        lesson_tags.extend(["盈利案例", "低位/主线/估值修复候选"])
        rules.append("控制仓位参与可验证主线，盈利后按估值或压力位分批兑现。")
        if pct >= 10:
            lesson_tags.append("高赔率成功")
            rules.append("盈利超过15%后至少卖出1/3到1/2，避免盈利回吐。")
    elif outcome == "failure":
        lesson_tags.extend(["亏损案例", "仓位风险"])
        rules.append("亏损案例优先复盘仓位和买点，后续相似场景只允许小仓试错。")
        if buy_amount >= 50000:
            lesson_tags.append("仓位过重")
            veto_lessons.append("单笔买入金额过大时必须二次确认亏损金额和现金占比。")
        veto_lessons.append("涨停后追入、高估值和信号冲突同时出现时禁止重仓。")
    if signal in {"SELL", "STRONG_SELL", "UNDERWEIGHT"}:
        lesson_tags.append("AI卖出信号冲突")
        veto_lessons.append("同一标的最近报告出现SELL/STRONG_SELL/UNDERWEIGHT时禁止新增重仓。")
    facts = {
        "buy_amount": round(buy_amount, 3),
        "sell_amount": cycle.get("sell_amount"),
        "fees": cycle.get("fees"),
        "realized_pnl": cycle.get("realized_pnl"),
        "realized_pnl_pct": cycle.get("realized_pnl_pct"),
        "holding_days": cycle.get("holding_days"),
    }
    summary = (
        f"{cycle.get('name') or cycle.get('code')}案例："
        f"{'盈利' if outcome == 'success' else '亏损' if outcome == 'failure' else '平局'}"
        f"{pnl:.2f}元（{pct:.2f}%）。"
    )
    return {
        **cycle,
        "outcome": outcome,
        "summary": summary,
        "facts": facts,
        "lesson_tags": lesson_tags,
        "rules": rules,
        "veto_lessons": veto_lessons,
        "report_context": report_context or {},
        "status": "draft",
    }


def list_closed_trade_candidates(*, account_id: str = "default", db_path: Path | None = None) -> dict[str, Any]:
    with sqlite3.connect(str(_db_path(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        cycles = _fetch_closed_cycles(conn, account_id)
        existing = {
            row["memory_key"]: dict(row)
            for row in conn.execute("SELECT memory_key, status, id FROM trade_memories").fetchall()
        }
    candidates = []
    for cycle in sorted(cycles, key=lambda item: (str(item.get("closed_at") or ""), item["code"]), reverse=True):
        known = existing.get(cycle["memory_key"]) or {}
        candidates.append(
            {
                **cycle,
                "memory_id": known.get("id"),
                "status": known.get("status") or "pending_review",
            }
        )
    return {"count": len(candidates), "candidates": candidates}


def generate_memory_draft(code: str, *, account_id: str = "default", memory_key: str | None = None, db_path: Path | None = None) -> dict[str, Any]:
    code = str(code or "")[:6]
    with sqlite3.connect(str(_db_path(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        cycles = _fetch_closed_cycles(conn, account_id, code)
        if memory_key:
            cycles = [cycle for cycle in cycles if cycle["memory_key"] == memory_key]
        if not cycles:
            raise ValueError(f"未找到已清仓交易闭环: {code}")
        cycle = sorted(cycles, key=lambda item: str(item.get("closed_at") or ""), reverse=True)[0]
        report_context = _latest_report_context(conn, cycle["code"], before_at=cycle.get("opened_at"))
    return _lesson_payload(cycle, report_context)


def save_trade_memory(
    payload: dict[str, Any],
    *,
    embedding_provider: Any | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    memory_key = str(payload.get("memory_key") or "").strip()
    if not memory_key:
        raise ValueError("memory_key required")
    status = str(payload.get("status") or "draft").strip() or "draft"
    confirmed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if status == "active" else payload.get("confirmed_at")
    values = {
        "memory_key": memory_key,
        "account_id": payload.get("account_id") or "default",
        "code": str(payload.get("code") or "")[:6],
        "name": payload.get("name") or payload.get("code") or "",
        "status": status,
        "outcome": payload.get("outcome") or "neutral",
        "trade_ids_json": _dumps(payload.get("trade_ids") or payload.get("trade_ids_json") or []),
        "opened_at": payload.get("opened_at"),
        "closed_at": payload.get("closed_at"),
        "holding_days": _num(payload.get("holding_days")),
        "buy_amount": _num(payload.get("buy_amount")),
        "sell_amount": _num(payload.get("sell_amount")),
        "fees": _num(payload.get("fees")),
        "realized_pnl": _num(payload.get("realized_pnl")),
        "realized_pnl_pct": _num(payload.get("realized_pnl_pct")),
        "summary": payload.get("summary") or "",
        "facts_json": _dumps(payload.get("facts") or payload.get("facts_json") or {}),
        "lesson_tags_json": _dumps(payload.get("lesson_tags") or payload.get("lesson_tags_json") or []),
        "rules_json": _dumps(payload.get("rules") or payload.get("rules_json") or []),
        "veto_lessons_json": _dumps(payload.get("veto_lessons") or payload.get("veto_lessons_json") or []),
        "report_context_json": _dumps(payload.get("report_context") or payload.get("report_context_json") or {}),
        "confirmed_at": confirmed_at,
    }
    columns = list(values)
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{col}=excluded.{col}" for col in columns if col != "memory_key")
    with sqlite3.connect(str(_db_path(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            f"""
            INSERT INTO trade_memories ({", ".join(columns)})
            VALUES ({placeholders})
            ON CONFLICT(memory_key) DO UPDATE SET
                {updates},
                updated_at=datetime('now')
            """,
            [values[col] for col in columns],
        )
        row = conn.execute("SELECT * FROM trade_memories WHERE memory_key = ?", (memory_key,)).fetchone()
        memory = _decode_memory(row)
        if memory.get("status") == "active":
            memory["embedding_index"] = _index_memory_embedding_if_available(
                conn,
                memory,
                embedding_provider=embedding_provider,
                db_path=db_path,
            )
        conn.commit()
    return memory


def list_trade_memories(*, status: str | None = None, code: str | None = None, account_id: str = "default", limit: int = 50, db_path: Path | None = None) -> dict[str, Any]:
    where = ["account_id = ?"]
    params: list[Any] = [account_id]
    if status:
        where.append("status = ?")
        params.append(status)
    if code:
        where.append("code = ?")
        params.append(code[:6])
    params.append(max(1, min(int(limit or 50), 200)))
    with sqlite3.connect(str(_db_path(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT *
            FROM trade_memories
            WHERE {" AND ".join(where)}
            ORDER BY datetime(updated_at) DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    memories = [_decode_memory(row) for row in rows]
    return {"count": len(memories), "memories": memories}


def _index_memory_embedding(
    conn: sqlite3.Connection,
    memory: dict[str, Any],
    provider: Any,
    *,
    model: str,
    dimensions: int,
) -> str:
    text = _embedding_text(memory)
    content_hash = _content_hash(text)
    existing = conn.execute(
        """
        SELECT content_hash, model, dimensions
        FROM trade_memory_embeddings
        WHERE memory_id = ?
        """,
        (memory["id"],),
    ).fetchone()
    if (
        existing
        and existing["content_hash"] == content_hash
        and existing["model"] == model
        and int(existing["dimensions"] or 0) == dimensions
    ):
        return "skipped"
    vector = _validate_embedding(provider.embed_texts([text])[0], dimensions=dimensions)
    conn.execute("DELETE FROM trade_memory_embedding_vec WHERE rowid = ?", (memory["id"],))
    conn.execute(
        "INSERT INTO trade_memory_embedding_vec(rowid, embedding) VALUES (?, ?)",
        (memory["id"], json.dumps(vector)),
    )
    conn.execute(
        """
        INSERT INTO trade_memory_embeddings
            (memory_id, memory_key, model, dimensions, content_hash, embedding_text, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(memory_id) DO UPDATE SET
            memory_key=excluded.memory_key,
            model=excluded.model,
            dimensions=excluded.dimensions,
            content_hash=excluded.content_hash,
            embedding_text=excluded.embedding_text,
            updated_at=datetime('now')
        """,
        (memory["id"], memory["memory_key"], model, dimensions, content_hash, text),
    )
    return "indexed"


def _index_memory_embedding_if_available(
    conn: sqlite3.Connection,
    memory: dict[str, Any],
    *,
    embedding_provider: Any | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    provider = embedding_provider or _default_embedding_provider(db_path)
    if provider is None:
        return {"status": "disabled", "reason": "embedding provider not configured"}
    model, dimensions = _embedding_provider_meta(provider)
    if dimensions != EMBEDDING_DIMENSIONS:
        return {"status": "disabled", "reason": f"unsupported embedding dimensions: {dimensions}"}
    try:
        _ensure_embedding_store(conn)
        status = _index_memory_embedding(conn, memory, provider, model=model, dimensions=dimensions)
        return {"status": status, "model": model, "dimensions": dimensions}
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}


def backfill_trade_memory_embeddings(
    *,
    account_id: str = "default",
    code: str | None = None,
    limit: int = 200,
    embedding_provider: Any | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    provider = embedding_provider or _default_embedding_provider(db_path)
    if provider is None:
        return {"enabled": False, "indexed": 0, "skipped": 0, "errors": ["embedding provider not configured"]}
    model, dimensions = _embedding_provider_meta(provider)
    if dimensions != EMBEDDING_DIMENSIONS:
        return {"enabled": False, "indexed": 0, "skipped": 0, "errors": [f"unsupported embedding dimensions: {dimensions}"]}
    listing = list_trade_memories(status="active", code=code, account_id=account_id, limit=limit, db_path=db_path)
    indexed = 0
    skipped = 0
    errors: list[str] = []
    with sqlite3.connect(str(_db_path(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        try:
            _ensure_embedding_store(conn)
        except Exception as exc:
            return {"enabled": False, "indexed": 0, "skipped": 0, "errors": [str(exc)]}
        for memory in listing["memories"]:
            try:
                status = _index_memory_embedding(conn, memory, provider, model=model, dimensions=dimensions)
                if status == "indexed":
                    indexed += 1
                else:
                    skipped += 1
            except Exception as exc:
                errors.append(f"{memory.get('memory_key') or memory.get('id')}: {exc}")
        conn.commit()
    return {
        "enabled": True,
        "model": model,
        "dimensions": dimensions,
        "indexed": indexed,
        "skipped": skipped,
        "errors": errors,
    }


def trade_memory_embedding_status(
    *,
    account_id: str = "default",
    db_path: Path | None = None,
) -> dict[str, Any]:
    sqlite_vec_available = True
    with sqlite3.connect(str(_db_path(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        try:
            _ensure_embedding_store(conn)
        except Exception:
            sqlite_vec_available = False
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trade_memory_embeddings (
                    memory_id INTEGER PRIMARY KEY,
                    memory_key TEXT NOT NULL,
                    model TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    embedding_text TEXT NOT NULL,
                    updated_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
        active_rows = conn.execute(
            """
            SELECT id, memory_key, code, name, updated_at
            FROM trade_memories
            WHERE account_id = ? AND status = 'active'
            ORDER BY datetime(updated_at) DESC, id DESC
            """,
            (account_id,),
        ).fetchall()
        indexed_rows = conn.execute(
            """
            SELECT tme.memory_id, tme.updated_at, tme.model, tme.dimensions
            FROM trade_memory_embeddings tme
            JOIN trade_memories tm ON tm.id = tme.memory_id
            WHERE tm.account_id = ? AND tm.status = 'active'
            """,
            (account_id,),
        ).fetchall()
    indexed_by_id = {int(row["memory_id"]): dict(row) for row in indexed_rows}
    active_count = len(active_rows)
    indexed_count = len(indexed_by_id)
    missing = [
        {
            "memory_id": int(row["id"]),
            "memory_key": row["memory_key"],
            "code": row["code"],
            "name": row["name"],
            "updated_at": row["updated_at"],
        }
        for row in active_rows
        if int(row["id"]) not in indexed_by_id
    ]
    last_indexed_at = max((str(row["updated_at"] or "") for row in indexed_rows), default="")
    return {
        "sqlite_vec_available": sqlite_vec_available,
        "provider_configured": _default_embedding_provider(db_path) is not None,
        "model": EMBEDDING_MODEL,
        "dimensions": EMBEDDING_DIMENSIONS,
        "active_memories": active_count,
        "indexed_memories": indexed_count,
        "missing_embeddings": len(missing),
        "coverage_pct": round(indexed_count / active_count * 100, 3) if active_count else 100.0,
        "last_indexed_at": last_indexed_at,
        "missing": missing[:20],
    }


def _vector_memory_hits(
    conn: sqlite3.Connection,
    query_text: str,
    *,
    embedding_provider: Any | None,
    account_id: str,
    db_path: Path | None = None,
    limit: int = VECTOR_RECALL_LIMIT,
) -> dict[int, dict[str, Any]]:
    provider = embedding_provider or _default_embedding_provider(db_path)
    if provider is None or not query_text.strip():
        return {}
    _, dimensions = _embedding_provider_meta(provider)
    if dimensions != EMBEDDING_DIMENSIONS:
        return {}
    try:
        _ensure_embedding_store(conn)
        query_vector = _validate_embedding(provider.embed_texts([query_text])[0], dimensions=dimensions)
        vector_rows = conn.execute(
            """
            SELECT rowid, distance
            FROM trade_memory_embedding_vec
            WHERE embedding MATCH ?
            ORDER BY distance
            LIMIT ?
            """,
            (json.dumps(query_vector), max(1, min(int(limit or VECTOR_RECALL_LIMIT), 200))),
        ).fetchall()
    except Exception:
        return {}
    if not vector_rows:
        return {}
    ids = [int(row["rowid"] if isinstance(row, sqlite3.Row) else row[0]) for row in vector_rows]
    placeholders = ", ".join("?" for _ in ids)
    active = {
        int(row["id"])
        for row in conn.execute(
            f"SELECT id FROM trade_memories WHERE account_id = ? AND status = 'active' AND id IN ({placeholders})",
            [account_id, *ids],
        ).fetchall()
    }
    hits: dict[int, dict[str, Any]] = {}
    for row in vector_rows:
        memory_id = int(row["rowid"] if isinstance(row, sqlite3.Row) else row[0])
        if memory_id not in active:
            continue
        distance = _num(row["distance"] if isinstance(row, sqlite3.Row) else row[1])
        hits[memory_id] = {
            "vector_distance": distance,
            "vector_score": max(0.0, 80.0 - distance * 20.0),
        }
    return hits


def related_trade_memories(
    *,
    code: str | None = None,
    scenario_tags: list[str] | None = None,
    report_text: str | None = None,
    account_id: str = "default",
    limit: int = 6,
    embedding_provider: Any | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    clean_code = str(code or "")[:6]
    requested_tags = set(str(tag).strip() for tag in (scenario_tags or []) if str(tag).strip())
    requested_tags.update(extract_scenario_tags(report_text))
    listing = list_trade_memories(status="active", account_id=account_id, limit=200, db_path=db_path)
    query_text = " ".join(part for part in (code or "", report_text or "", " ".join(sorted(requested_tags))) if part)
    vector_hits: dict[int, dict[str, Any]] = {}
    with sqlite3.connect(str(_db_path(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        vector_hits = _vector_memory_hits(
            conn,
            query_text,
            embedding_provider=embedding_provider,
            account_id=account_id,
            db_path=db_path,
        )
    matches: list[dict[str, Any]] = []
    for memory in listing["memories"]:
        memory_tags = set(_memory_scenario_tags(memory))
        matched_tags = sorted(requested_tags & memory_tags)
        exact_code = bool(clean_code and memory.get("code") == clean_code)
        vector_hit = vector_hits.get(int(memory.get("id") or 0)) or {}
        score = 0
        reasons: list[str] = []
        if vector_hit:
            score += _num(vector_hit.get("vector_score"))
            reasons.append("向量召回")
        if exact_code:
            score += 100
            reasons.append("同标的匹配")
        if matched_tags:
            score += len(matched_tags) * 12
            reasons.append("场景重合：" + "、".join(matched_tags))
        if memory.get("outcome") == "failure" and RISK_MEMORY_TAGS & set(matched_tags):
            score += 10
            reasons.append("亏损类风险记忆优先")
        if not requested_tags and not clean_code:
            score += 1
            reasons.append("最近主动记忆")
        if score <= 0:
            continue
        matches.append(
            {
                **memory,
                "scenario_tags": sorted(memory_tags),
                "matched_tags": matched_tags,
                "match_score": score,
                "vector_distance": vector_hit.get("vector_distance"),
                "vector_score": vector_hit.get("vector_score"),
                "match_reason": "；".join(reasons) if reasons else "最近主动记忆",
            }
        )
    matches.sort(key=lambda item: (item.get("match_score") or 0, str(item.get("updated_at") or ""), item.get("id") or 0), reverse=True)
    matches = matches[:max(1, min(int(limit or 6), 20))]
    return {
        "version": TRADE_MEMORY_VERSION,
        "retrieval_mode": "hybrid_vector" if vector_hits else "rules",
        "scenario_tags": sorted(requested_tags),
        "count": len(matches),
        "matches": matches,
    }


def trade_memory_constraints() -> dict[str, Any]:
    return {
        "scope": "account_action_only",
        "allowed_adjustments": ["position_sizing", "entry_veto", "trial_entry", "add_condition", "exit_rule"],
        "forbidden": ["overwrite_research_signal", "auto_bearish_from_old_loss", "ignore_current_facts"],
        "required_outputs": ["memory_match", "memory_adjustments"],
    }


def context_injection_status() -> dict[str, Any]:
    return {
        "version": TRADE_MEMORY_VERSION,
        "constraints": trade_memory_constraints(),
        "injection_points": {
            "single_stock_report": {
                "status": "covered",
                "path": "scheduler/ta_bridge.py",
                "via": "investment_profile_from_db(code, report_text).context",
            },
            "batch_snapshot_report": {
                "status": "covered",
                "path": "scripts/batch_research.py",
                "via": "investment_profile_from_db(db_path, code, report_text).context",
            },
            "daily_holding_review": {
                "status": "covered",
                "path": "services/holding_review_service.py",
                "via": "investment_profile_snapshot(settings).context",
            },
            "position_plan": {
                "status": "covered",
                "path": "scripts/batch_research.py",
                "via": "investment_profile_from_db(db_path).context",
            },
        },
        "excluded_points": {
            "hermes_operation_parser": {
                "status": "excluded",
                "path": "services/hermes_console_service.py",
                "reason": "intent parser only; it creates controlled tool drafts and should not receive recommendation memories as decision context",
                "guardrail": "all write actions still require user confirmation before execution",
            },
        },
    }


def trade_memory_context(
    *,
    code: str | None = None,
    scenario_tags: list[str] | None = None,
    report_text: str | None = None,
    account_id: str = "default",
    limit: int = 6,
    db_path: Path | None = None,
) -> str:
    related = related_trade_memories(
        code=code,
        scenario_tags=scenario_tags,
        report_text=report_text,
        account_id=account_id,
        limit=limit,
        db_path=db_path,
    )
    memories = related["matches"]
    if not memories and (code or scenario_tags or report_text):
        memories = related_trade_memories(account_id=account_id, limit=limit, db_path=db_path)["matches"]
    if not memories:
        return ""
    lines = [
        "【交易复盘记忆约束】",
        "- 适用范围：只校准账户动作、买入否决、试仓/加仓、仓位上限、退出纪律。",
        "- 禁止事项：不得直接覆盖股票研究信号；不得把历史亏损自动等同于当前标的看空；不得忽略当前财务、价格和资金事实。",
        '- 输出字段：final_decision 或 raw_state 必须补充 memory_match={"matched":true/false,"cases":[],"applicability":""} 与 memory_adjustments={"position_delta":"","entry_veto":"","exit_rule":"","reason":""}。',
        "- 执行规则：触发亏损类相似记忆时，默认降低仓位或进入观察，除非报告说明为什么本次不同。",
        "【交易复盘记忆】",
        "- 用途：以下是用户真实交易闭环沉淀的经验，只校准账户动作、仓位和纪律，不直接覆盖股票研究信号。",
    ]
    if related["scenario_tags"]:
        lines.append(f"- 当前场景标签：{'、'.join(related['scenario_tags'])}")
    for memory in memories[:limit]:
        tags = "、".join(memory.get("lesson_tags") or [])
        rules = "；".join((memory.get("rules") or [])[:2])
        veto = "；".join((memory.get("veto_lessons") or [])[:2])
        match_reason = memory.get("match_reason") or "最近主动记忆"
        lines.append(
            f"- {memory.get('name') or memory.get('code')}({memory.get('code')}): "
            f"{memory.get('summary') or ''} 匹配={match_reason}；标签={tags or '--'}；经验={rules or '--'}；禁止={veto or '--'}"
        )
    return "\n".join(lines)
