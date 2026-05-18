"""Tests for multi-database connection manager."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from db_mcp_py.config import ConnectionConfig
from db_mcp_py.database import ConnectionManager, DatabaseConnection, _build_url


# --- URL building ---


def test_build_url_postgresql():
    cfg = ConnectionConfig(id="t", type="postgresql", database="db", user="u", password="p", port=5432)
    assert _build_url(cfg, "localhost", 5432) == "postgresql+asyncpg://u:p@localhost:5432/db"


def test_build_url_mysql():
    cfg = ConnectionConfig(id="t", type="mysql", database="db", user="u", password="p", port=3306)
    assert _build_url(cfg, "host", 3306) == "mysql+aiomysql://u:p@host:3306/db"


def test_build_url_oracle():
    cfg = ConnectionConfig(id="t", type="oracle", database="db", user="u", password="p", port=1521)
    assert _build_url(cfg, "host", 1521) == "oracle+oracledb://u:p@host:1521/db"


def test_build_url_unsupported():
    cfg = ConnectionConfig(id="t", type="sqlite", database="db", user="u", port=0)
    with pytest.raises(ValueError, match="Unsupported"):
        _build_url(cfg, "localhost", 0)


def test_build_url_no_password():
    cfg = ConnectionConfig(id="t", type="postgresql", database="db", user="u", port=5432)
    assert _build_url(cfg, "localhost", 5432) == "postgresql+asyncpg://u:@localhost:5432/db"


# --- DatabaseConnection ---


def test_database_connection_not_connected():
    cfg = ConnectionConfig(id="t", database="db", user="u", port=5432)
    db = DatabaseConnection(config=cfg, effective={})
    assert db.is_connected is False


def test_database_connection_connected():
    cfg = ConnectionConfig(id="t", database="db", user="u", port=5432)
    db = DatabaseConnection(config=cfg, effective={}, engine=MagicMock())
    assert db.is_connected is True


def test_schema_cache_ttl():
    cfg = ConnectionConfig(id="t", database="db", user="u", port=5432)
    db = DatabaseConnection(config=cfg, effective={"schema_cache_ttl": 300})
    assert db.get_cached_schema() is None
    db.set_schema_cache([{"col": "a"}])
    assert db.get_cached_schema() == [{"col": "a"}]


# --- ConnectionManager ---


@pytest.mark.asyncio
async def test_connect_mongodb_raises():
    cfg = ConnectionConfig(id="t", type="mongodb", database="db", user="u", port=27017)
    db = DatabaseConnection(config=cfg, effective={"max_connections": 5, "query_timeout": 30})
    mgr = ConnectionManager()
    with pytest.raises(ValueError, match="MongoManager"):
        await mgr.connect("t", db)


@pytest.mark.asyncio
async def test_query_not_connected():
    mgr = ConnectionManager()
    with pytest.raises(ConnectionError, match="not connected"):
        await mgr.query("missing", "SELECT 1")


@pytest.mark.asyncio
async def test_get_schema_not_connected():
    mgr = ConnectionManager()
    with pytest.raises(ConnectionError, match="not connected"):
        await mgr.get_schema("missing")


# --- Integration tests (require running databases) ---


def _pg_config() -> ConnectionConfig | None:
    host = os.environ.get("TEST_PG_HOST")
    if not host:
        return None
    return ConnectionConfig(
        id="test_pg",
        type="postgresql",
        host=host,
        port=int(os.environ["TEST_PG_PORT"]),
        database=os.environ["TEST_PG_DB"],
        user=os.environ["TEST_PG_USER"],
        password=os.environ.get("TEST_PG_PASSWORD"),
    )


def _mysql_config() -> ConnectionConfig | None:
    host = os.environ.get("TEST_MYSQL_HOST")
    if not host:
        return None
    return ConnectionConfig(
        id="test_mysql",
        type="mysql",
        host=host,
        port=int(os.environ["TEST_MYSQL_PORT"]),
        database=os.environ["TEST_MYSQL_DB"],
        user=os.environ["TEST_MYSQL_USER"],
        password=os.environ.get("TEST_MYSQL_PASSWORD"),
    )


@pytest.mark.asyncio
async def test_pg_integration():
    cfg = _pg_config()
    if not cfg:
        pytest.skip("TEST_PG_HOST not set")
    mgr = ConnectionManager()
    db = DatabaseConnection(config=cfg, effective={"max_connections": 2, "query_timeout": 10, "connect_timeout": 10})
    assert await mgr.connect("test_pg", db)
    rows = await mgr.query("test_pg", "SELECT 1 AS val")
    assert rows == [{"val": 1}]
    schema = await mgr.get_schema("test_pg")
    assert isinstance(schema, list)
    await mgr.close_all()


@pytest.mark.asyncio
async def test_mysql_integration():
    cfg = _mysql_config()
    if not cfg:
        pytest.skip("TEST_MYSQL_HOST not set")
    mgr = ConnectionManager()
    db = DatabaseConnection(config=cfg, effective={"max_connections": 2, "query_timeout": 10, "connect_timeout": 10})
    assert await mgr.connect("test_mysql", db)
    rows = await mgr.query("test_mysql", "SELECT 1 AS val")
    assert rows[0]["val"] == 1
    schema = await mgr.get_schema("test_mysql")
    assert isinstance(schema, list)
    await mgr.close_all()
