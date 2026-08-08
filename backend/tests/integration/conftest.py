from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from lexground.config import Settings
from lexground.db import session as session_module
from lexground.db.models import Base, Chunk, Document
from lexground.ingest.chunk import chunk_units
from lexground.ingest.parse import parse_document
from lexground.main import build_query_service, create_app
from lexground.retrieval.embedder import HashEmbedder

TEST_DATABASE_URL = os.environ.get(
    "LEXGROUND_TEST_DATABASE_URL",
    "postgresql+asyncpg://lexground:lexground@localhost:5432/lexground_test",
)

ACT = """
(1) This recital explains why records of automated decisions must be retained.

Article 4

Right to human review

1. A person subject to an automated decision has the right to obtain review of that
decision by a natural person.

2. The deployer shall complete the review within 30 days of receiving the request.

Article 6

Record keeping

1. The deployer shall keep a record of each decision, comprising the input data, the
output and the date of the decision.

2. Records shall be retained for five years from the date of the decision.
"""


def _admin_url() -> str:
    return TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres"


@pytest_asyncio.fixture(scope="session")
async def engine() -> AsyncIterator[object]:
    database = TEST_DATABASE_URL.rsplit("/", 1)[1]
    admin = create_async_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{database}"'))
            await connection.execute(text(f'CREATE DATABASE "{database}"'))
    except Exception as error:  # pragma: no cover - environment guard
        pytest.skip(f"Postgres unavailable for integration tests: {error}")
    finally:
        await admin.dispose()

    test_engine = create_async_engine(TEST_DATABASE_URL)
    async with test_engine.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(Base.metadata.create_all)

    yield test_engine
    await test_engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def seeded(engine) -> AsyncIterator[None]:
    """One indexed act, embedded with the deterministic backend so assertions are stable."""
    embedder = HashEmbedder(384)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        document = Document(
            celex_id="TEST0001",
            title="Test Act",
            short_title="TEST",
            language="en",
            version="original",
            source_url="http://example.invalid/test",
        )
        session.add(document)
        await session.flush()

        chunks = chunk_units(parse_document(ACT, language="en"), short_title="TEST", language="en")
        vectors = embedder.embed_documents([chunk.text for chunk in chunks])
        session.add_all(
            Chunk(
                document_id=document.id,
                ordinal=chunk.ordinal,
                unit_type=chunk.unit_type,
                unit_number=chunk.unit_number,
                paragraph=chunk.paragraph,
                heading=chunk.heading,
                citation=chunk.citation,
                text=chunk.text,
                token_estimate=chunk.token_estimate,
                language="en",
                embedding=vector,
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        )
        await session.commit()
    yield


@pytest.fixture
def settings() -> Settings:
    return Settings(database_url=TEST_DATABASE_URL, anthropic_api_key=None)


@pytest_asyncio.fixture
async def db(engine, seeded) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session


@pytest_asyncio.fixture
async def client(engine, seeded, settings) -> AsyncIterator[AsyncClient]:
    app = create_app()
    session_module._engine = engine
    session_module._sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    app.state.settings = settings
    app.state.query_service = build_query_service(settings)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
