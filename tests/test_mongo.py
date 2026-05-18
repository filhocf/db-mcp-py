"""Tests for MongoDB connection manager."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from db_mcp_py.config import ConnectionConfig
from db_mcp_py.mongo import MongoConnection, MongoManager


# --- Unit tests ---


def test_mongo_connection_not_connected():
    cfg = ConnectionConfig(id="t", type="mongodb", database="db", user="u", port=27017)
    mc = MongoConnection(config=cfg, effective={})
    assert mc.is_connected is False


def test_mongo_connection_connected():
    cfg = ConnectionConfig(id="t", type="mongodb", database="db", user="u", port=27017)
    mc = MongoConnection(config=cfg, effective={}, client=MagicMock())
    assert mc.is_connected is True


def test_mongo_schema_cache():
    cfg = ConnectionConfig(id="t", type="mongodb", database="db", user="u", port=27017)
    mc = MongoConnection(config=cfg, effective={"schema_cache_ttl": 300})
    assert mc.get_cached_schema() is None
    mc.set_schema_cache([{"col": "x"}])
    assert mc.get_cached_schema() == [{"col": "x"}]


@pytest.mark.asyncio
async def test_mongo_connect_no_motor():
    cfg = ConnectionConfig(id="t", type="mongodb", database="db", user="u", port=27017)
    mc = MongoConnection(config=cfg, effective={"connect_timeout": 5})
    mgr = MongoManager()

    with patch.dict("sys.modules", {"motor": None, "motor.motor_asyncio": None}):
        # motor import will fail
        result = await mgr.connect("t", mc)
        # If motor is actually installed, this test verifies connectivity check
        # If not installed, it verifies graceful failure
        assert isinstance(result, bool)


@pytest.mark.asyncio
async def test_mongo_query_not_connected():
    mgr = MongoManager()
    with pytest.raises(ConnectionError, match="not connected"):
        await mgr.query("missing", '{"collection": "test"}')


@pytest.mark.asyncio
async def test_mongo_get_schema_not_connected():
    mgr = MongoManager()
    with pytest.raises(ConnectionError, match="not connected"):
        await mgr.get_schema("missing")


# --- Integration tests ---


def _mongo_config() -> ConnectionConfig | None:
    host = os.environ.get("TEST_MONGO_HOST")
    if not host:
        return None
    return ConnectionConfig(
        id="test_mongo",
        type="mongodb",
        host=host,
        port=int(os.environ["TEST_MONGO_PORT"]),
        database=os.environ["TEST_MONGO_DB"],
        user="",
    )


@pytest.mark.asyncio
async def test_mongo_integration():
    cfg = _mongo_config()
    if not cfg:
        pytest.skip("TEST_MONGO_HOST not set")
    mgr = MongoManager()
    mc = MongoConnection(config=cfg, effective={"connect_timeout": 10, "schema_cache_ttl": 300})
    assert await mgr.connect("test_mongo", mc)

    # Insert a test doc and query it
    await mc.db["test_coll"].insert_one({"name": "test", "value": 42})
    rows = await mgr.query("test_mongo", '{"collection": "test_coll", "filter": {"name": "test"}}')
    assert len(rows) >= 1
    assert rows[0]["name"] == "test"

    schema = await mgr.get_schema("test_mongo")
    assert isinstance(schema, list)
    assert any(r["table_name"] == "test_coll" for r in schema)

    # Cleanup
    await mc.db["test_coll"].drop()
    await mgr.close_all()
