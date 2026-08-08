from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from lexground.config import Settings, get_settings
from lexground.db.session import get_sessionmaker
from lexground.pipeline import QueryService


async def db_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def query_service(request: Request) -> QueryService:
    return request.app.state.query_service  # type: ignore[no-any-return]


SessionDep = Annotated[AsyncSession, Depends(db_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
QueryServiceDep = Annotated[QueryService, Depends(query_service)]
