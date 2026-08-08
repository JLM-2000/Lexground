from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select

from lexground.api.deps import SessionDep
from lexground.db.models import Chunk, Document, EvalRun

router = APIRouter(prefix="/api", tags=["corpus"])


@router.get("/documents")
async def list_documents(session: SessionDep) -> list[dict[str, object]]:
    rows = await session.execute(
        select(
            Document.source,
            Document.source_id,
            Document.short_title,
            Document.title,
            Document.language,
            Document.version,
            Document.source_url,
            func.count(Chunk.id).label("chunk_count"),
        )
        .join(Chunk, Chunk.document_id == Document.id, isouter=True)
        .group_by(Document.id)
        .order_by(Document.short_title, Document.language)
    )
    return [dict(row._mapping) for row in rows]


@router.get("/evaluation/runs")
async def list_eval_runs(session: SessionDep, limit: int = 20) -> list[dict[str, object]]:
    rows = await session.scalars(
        select(EvalRun).order_by(EvalRun.created_at.desc()).limit(min(limit, 100))
    )
    return [
        {
            "id": str(run.id),
            "git_sha": run.git_sha,
            "index_version": run.index_version,
            "case_count": run.case_count,
            "metrics": run.metrics,
            "passed": run.passed,
            "created_at": run.created_at.isoformat(),
        }
        for run in rows
    ]
