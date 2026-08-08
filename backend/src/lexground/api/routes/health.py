from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, select

from lexground.api.deps import SessionDep, SettingsDep
from lexground.db.models import Chunk
from lexground.observability.metrics import INDEXED_CHUNKS

router = APIRouter(tags=["ops"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(session: SessionDep, settings: SettingsDep) -> dict[str, object]:
    """Readiness is index-aware; an empty index reports degraded."""
    chunk_count = await session.scalar(select(func.count(Chunk.id))) or 0
    INDEXED_CHUNKS.set(chunk_count)
    return {
        "status": "ok" if chunk_count else "degraded",
        "indexed_chunks": chunk_count,
        "synthesis_backend": settings.synthesis_backend,
        "embedding_backend": settings.embedding_backend,
    }


@router.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
