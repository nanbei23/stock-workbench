"""Self-evolution feedback loop API."""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import self_evolution_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/self-evolution", tags=["AI自我进化"])


class SemanticSearchRequest(BaseModel):
    query: str
    limit: int = 10


@router.post("/run")
async def run_self_evolution_cycle():
    try:
        return self_evolution_service.run_cycle()
    except Exception as exc:
        logger.error("run_self_evolution_cycle error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/latest")
async def get_latest_self_evolution_snapshot():
    try:
        snapshot = self_evolution_service.latest_snapshot()
        if snapshot:
            return snapshot
        return self_evolution_service.run_cycle()
    except Exception as exc:
        logger.error("get_latest_self_evolution_snapshot error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/context")
async def get_self_evolution_context():
    try:
        context = self_evolution_service.latest_context()
        if not context:
            context = self_evolution_service.run_cycle().get("context") or ""
        return {"enabled": bool(context), "context": context}
    except Exception as exc:
        logger.error("get_self_evolution_context error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/attributions")
async def get_self_evolution_attributions(limit: int = 100):
    try:
        return self_evolution_service.list_recommendation_attributions(limit=limit)
    except Exception as exc:
        logger.error("get_self_evolution_attributions error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/semantic-search")
async def semantic_search_trade_memories(req: SemanticSearchRequest):
    try:
        return self_evolution_service.semantic_memory_search(req.query, limit=req.limit)
    except Exception as exc:
        logger.error("semantic_search_trade_memories error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
