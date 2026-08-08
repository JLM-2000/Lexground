from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from lexground.api.routes import corpus, health, query
from lexground.config import Settings, get_settings
from lexground.db.session import dispose_engine, init_engine
from lexground.observability.logging import configure_logging
from lexground.pipeline import QueryService
from lexground.retrieval.embedder import get_embedder
from lexground.retrieval.service import HybridRetriever
from lexground.synthesis.answerer import build_answerer


def build_query_service(settings: Settings) -> QueryService:
    embedder = get_embedder(settings)
    return QueryService(
        settings=settings,
        retriever=HybridRetriever(settings, embedder),
        answerer=build_answerer(settings),
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging()
    init_engine(settings)
    app.state.settings = settings
    app.state.query_service = build_query_service(settings)
    try:
        yield
    finally:
        await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Lexground",
        version="0.1.0",
        summary="Grounded retrieval over EU regulatory law, with evaluation gates in CI",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(query.router)
    app.include_router(corpus.router)
    return app


app = create_app()
