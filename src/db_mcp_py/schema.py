"""Unified schema discovery across all database types."""

from __future__ import annotations

from .database import ConnectionManager
from .mongo import MongoManager


async def get_schema(
    conn_id: str,
    conn_mgr: ConnectionManager,
    mongo_mgr: MongoManager,
) -> list[dict]:
    """Get schema from any connected database."""
    if conn_id in mongo_mgr.connections:
        return await mongo_mgr.get_schema(conn_id)
    return await conn_mgr.get_schema(conn_id)
