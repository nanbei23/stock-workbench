"""Resolve model-library provider references into private runtime configs."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from config import DB_PATH


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _loads(value: Any, fallback: Any) -> Any:
    if value in ("", None):
        return fallback
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
        return parsed if parsed is not None else fallback
    except (TypeError, ValueError):
        return fallback


def settings_map(db_path: Path | None = None) -> dict[str, str]:
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {str(row["key"]): str(row["value"] or "") for row in rows}


def _provider_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "base_url": row["base_url"],
        "api_key": row["api_key"],
        "models": _loads(row["models_json"], []),
        "quick_model": row["quick_model"],
        "deep_model": row["deep_model"],
        "default_model": row["default_model"],
        "context_length": row["context_length"],
        "embedding_model": row["embedding_model"],
        "embedding_dimensions": row["embedding_dimensions"],
        "usage": _loads(row["usage_json"], []),
    }


def load_model_providers(db_path: Path | None = None) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        try:
            rows = conn.execute(
                """
                SELECT id, name, base_url, api_key, models_json, quick_model, deep_model,
                       default_model, context_length, embedding_model, embedding_dimensions,
                       usage_json
                FROM model_providers
                ORDER BY updated_at DESC, name ASC
                """
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        if rows:
            return [_provider_from_row(row) for row in rows]
        try:
            row = conn.execute("SELECT value FROM settings WHERE key = 'model_providers'").fetchone()
        except sqlite3.OperationalError:
            row = None
    providers = _loads(row["value"] if row else "[]", [])
    return [provider for provider in providers if isinstance(provider, dict)]


def provider_by_id(provider_id: str, db_path: Path | None = None) -> dict[str, Any] | None:
    if not provider_id:
        return None
    return next((item for item in load_model_providers(db_path) if str(item.get("id")) == str(provider_id)), None)


def _provider_model(provider: dict[str, Any], *, model_tier: str = "deep") -> str:
    if model_tier == "quick":
        return provider.get("quick_model") or provider.get("default_model") or provider.get("deep_model") or ""
    if model_tier == "embedding":
        return provider.get("embedding_model") or provider.get("default_model") or provider.get("quick_model") or ""
    return provider.get("deep_model") or provider.get("default_model") or provider.get("quick_model") or ""


def resolve_ai_config(
    settings: dict[str, Any] | None = None,
    *,
    db_path: Path | None = None,
    model_tier: str = "deep",
) -> dict[str, str]:
    settings = settings or settings_map(db_path)
    provider = provider_by_id(str(settings.get("ai_primary_provider_id") or ""), db_path)
    if provider:
        return {
            "base_url": str(provider.get("base_url") or ""),
            "api_key": str(provider.get("api_key") or ""),
            "model": _provider_model(provider, model_tier=model_tier),
            "context_length": str(provider.get("context_length") or ""),
            "_provider_id": str(provider.get("id") or ""),
            "_profile": str(provider.get("name") or provider.get("id") or ""),
        }

    provider_name = str(settings.get("llm_provider") or "deepseek").upper()
    api_key = (
        str(settings.get("api_key") or "")
        or os.environ.get(f"{provider_name}_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    )
    model = str(settings.get("deep_think_model" if model_tier == "deep" else "quick_think_model") or "")
    if not model:
        model = str(settings.get("quick_think_model") or settings.get("deep_think_model") or "")
    return {
        "base_url": str(settings.get("custom_endpoint") or ""),
        "api_key": api_key,
        "model": model,
        "context_length": str(settings.get("llm_context_length") or ""),
        "_provider_id": "",
        "_profile": str(settings.get("llm_name") or ""),
    }


def resolve_verification_config(settings: dict[str, Any] | None = None, *, db_path: Path | None = None) -> dict[str, str]:
    settings = settings or settings_map(db_path)
    provider = provider_by_id(str(settings.get("verification_provider_id") or ""), db_path)
    if provider:
        return {
            "base_url": str(provider.get("base_url") or ""),
            "api_key": str(provider.get("api_key") or ""),
            "model": _provider_model(provider),
            "context_length": str(provider.get("context_length") or ""),
            "_provider_id": str(provider.get("id") or ""),
            "_profile": str(provider.get("name") or provider.get("id") or ""),
        }
    return {
        "base_url": str(settings.get("verification_endpoint") or ""),
        "api_key": str(settings.get("verification_api_key") or ""),
        "model": str(settings.get("verification_model") or ""),
        "context_length": str(settings.get("verification_context_length") or ""),
        "_provider_id": "",
        "_profile": str(settings.get("verification_name") or ""),
    }


def resolve_embedding_config(settings: dict[str, Any] | None = None, *, db_path: Path | None = None) -> dict[str, Any]:
    settings = settings or settings_map(db_path)
    provider = provider_by_id(str(settings.get("embedding_provider_id") or ""), db_path)
    if provider:
        return {
            "endpoint": str(provider.get("base_url") or ""),
            "api_key": str(provider.get("api_key") or ""),
            "model": _provider_model(provider, model_tier="embedding"),
            "dimensions": int(provider.get("embedding_dimensions") or settings.get("embedding_dimensions") or 1536),
            "_provider_id": str(provider.get("id") or ""),
            "_profile": str(provider.get("name") or provider.get("id") or ""),
        }
    return {
        "endpoint": str(settings.get("embedding_endpoint") or ""),
        "api_key": str(settings.get("embedding_api_key") or settings.get("openai_api_key") or ""),
        "model": str(settings.get("embedding_model") or ""),
        "dimensions": int(settings.get("embedding_dimensions") or 1536),
        "_provider_id": "",
        "_profile": "",
    }
