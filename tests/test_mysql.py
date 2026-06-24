"""Tests for MySQL/MariaDB support — connect_args, schema discovery, read-only."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from db_mcp_py.config import ConnectionConfig
from db_mcp_py.database import ConnectionManager, DatabaseConnection, _build_url


# --- URL building ---


def test_build_url_mysql_standard():
    cfg = ConnectionConfig(id="m", type="mysql", database="mydb", user="root", password="secret", port=3306)
    assert _build_url(cfg, "db.host", 3306) == "mysql+aiomysql://root:secret@db.host:3306/mydb"


def test_build_url_mysql_special_chars():
    cfg = ConnectionConfig(id="m", type="mysql", database="mydb", user="user@host", password="p@ss/w0rd", port=3306)
    url = _build_url(cfg, "localhost", 3306)
    assert "user%40host" in url
    assert "p%40ss%2Fw0rd" in url


def test_build_url_mysql_no_password():
    cfg = ConnectionConfig(id="m", type="mysql", database="mydb", user="root", port=3306)
    assert _build_url(cfg, "localhost", 3306) == "mysql+aiomysql://root:@localhost:3306/mydb"


# --- Connect args ---


@pytest.mark.asyncio
@patch("db_mcp_py.database._create_engine")
async def test_mysql_connect_args_read_only(mock_create_engine):
    """MySQL connections should set SESSION TRANSACTION READ ONLY."""
    mock_engine = MagicMock()
    mock_create_engine.return_value = mock_engine

    cfg = ConnectionConfig(id="mysql_test", type="mysql", database="db", user="u", password="p", port=3306)
    db = DatabaseConnection(
        config=cfg,
        effective={"max_connections": 2, "query_timeout": 10, "connect_timeout": 10},
        resolved_host="localhost",
        resolved_port=3306,
    )
    mgr = ConnectionManager()
    await mgr.connect("mysql_test", db)

    mock_create_engine.assert_called_once()
    call_kwargs = mock_create_engine.call_args[1]
    assert call_kwargs["connect_args"]["init_command"] == "SET SESSION TRANSACTION READ ONLY"


@pytest.mark.asyncio
@patch("db_mcp_py.database._create_engine")
async def test_mysql_no_oracle_events(mock_create_engine):
    """MySQL should NOT have Oracle-specific connect events."""
    mock_engine = MagicMock()
    mock_create_engine.return_value = mock_engine

    cfg = ConnectionConfig(id="mysql_test", type="mysql", database="db", user="u", password="p", port=3306)
    db = DatabaseConnection(
        config=cfg,
        effective={"max_connections": 2, "query_timeout": 10, "connect_timeout": 10},
        resolved_host="localhost",
        resolved_port=3306,
    )
    mgr = ConnectionManager()
    await mgr.connect("mysql_test", db)

    call_kwargs = mock_create_engine.call_args[1]
    assert call_kwargs.get("events", {}) == {}


# --- Schema discovery ---


@pytest.mark.asyncio
@patch("db_mcp_py.database._create_engine")
async def test_mysql_schema_uses_generic_inspect(mock_create_engine):
    """MySQL should use _generic_schema (SQLAlchemy inspect), not PG-specific SQL."""
    mock_engine = AsyncMock()
    mock_conn = AsyncMock()

    # Mock run_sync to simulate inspect
    schema_data = [
        {
            "table_schema": "mydb",
            "table_name": "users",
            "column_name": "id",
            "data_type": "INTEGER",
            "is_nullable": "NO",
            "column_default": None,
            "is_primary_key": True,
        },
        {
            "table_schema": "mydb",
            "table_name": "users",
            "column_name": "name",
            "data_type": "VARCHAR(255)",
            "is_nullable": "YES",
            "column_default": None,
            "is_primary_key": False,
        },
    ]
    mock_conn.run_sync = AsyncMock(return_value=schema_data)
    mock_engine.connect = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock()))

    mock_create_engine.return_value = mock_engine

    cfg = ConnectionConfig(id="mysql_test", type="mysql", database="db", user="u", password="p", port=3306)
    db = DatabaseConnection(
        config=cfg,
        effective={"max_connections": 2, "query_timeout": 10, "connect_timeout": 10, "schema_cache_ttl": 300},
        resolved_host="localhost",
        resolved_port=3306,
    )
    mgr = ConnectionManager()
    await mgr.connect("mysql_test", db)

    result = await mgr.get_schema("mysql_test")
    assert result == schema_data
    mock_conn.run_sync.assert_called_once()


# --- Config ---


def test_mysql_config_default_port():
    """MySQL connections should work with custom port."""
    cfg = ConnectionConfig(id="m", type="mysql", database="db", user="u", port=3307)
    assert cfg.port == 3307
    assert cfg.type == "mysql"


def test_mysql_config_with_tunnel():
    """MySQL via SSH tunnel."""
    cfg = ConnectionConfig(
        id="m",
        type="mysql",
        database="db",
        user="u",
        password="p",
        port=3306,
        tunnel={
            "ssh_host": "bastion.example.com",
            "remote_host": "mysql-internal",
            "remote_port": 3306,
            "local_port": 13306,
            "ssh_user": "deploy",
        },
    )
    assert cfg.tunnel is not None
    assert cfg.tunnel.remote_port == 3306
    assert cfg.tunnel.local_port == 13306


def test_mysql_config_require_vpn():
    """MySQL with VPN requirement."""
    cfg = ConnectionConfig(id="m", type="mysql", database="db", user="u", port=3306, require_vpn=True)
    assert cfg.require_vpn is True
