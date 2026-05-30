"""Schemas for Hermes controlled tool calls."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HermesToolCall(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = None
    reason: str = ""


class HermesToolValidation(BaseModel):
    valid: bool
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    normalized_args: dict[str, Any] = Field(default_factory=dict)
