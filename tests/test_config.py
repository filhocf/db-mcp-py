"""Tests for config loading and validation."""

import json
import os
import tempfile

import pytest

from db_mcp_py.config import load_config


def _write_config(data: dict) -> str:
    """Write config dict to a temp file and return path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(data, f)
    f.close()
    return f.name


_MINIMAL = {
    "connections": [
        {"id": "test1", "database": "mydb", "user": "me", "port": 5432},
    ],
}


def test_load_config_basic():
    path = _write_config(_MINIMAL)
    try:
        cfg = load_config(path)
        assert len(cfg.connections) == 1
        assert cfg.connections[0].id == "test1"
        assert cfg.connections[0].database == "mydb"
        assert cfg.defaults.read_only is True
        assert cfg.defaults.query_timeout == 30
        assert cfg.defaults.max_connections == 5
    finally:
        os.unlink(path)


def test_env_var_expansion():
    os.environ["TEST_DB_PASS"] = "secret123"
    data = {
        "connections": [
            {"id": "env_test", "database": "db", "user": "u", "password": "${TEST_DB_PASS}", "port": 5432},
        ],
    }
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(data, f)
    f.close()
    try:
        cfg = load_config(f.name)
        assert cfg.connections[0].password == "secret123"
    finally:
        os.unlink(f.name)
        del os.environ["TEST_DB_PASS"]


def test_missing_required_fields():
    # Missing 'database' and 'user'
    data = {"connections": [{"id": "bad"}]}
    path = _write_config(data)
    try:
        with pytest.raises(Exception):
            load_config(path)
    finally:
        os.unlink(path)


def test_defaults_override():
    data = {
        "defaults": {"query_timeout": 60, "max_connections": 10},
        "connections": [
            {"id": "a", "database": "db", "user": "u", "port": 5432, "query_timeout": 120},
        ],
    }
    path = _write_config(data)
    try:
        cfg = load_config(path)
        eff = cfg.get_effective(cfg.connections[0])
        assert eff["query_timeout"] == 120  # connection override
        assert eff["max_connections"] == 10  # from defaults
    finally:
        os.unlink(path)


def test_duplicate_ids_rejected():
    data = {
        "connections": [
            {"id": "dup", "database": "db1", "user": "u", "port": 5432},
            {"id": "dup", "database": "db2", "user": "u", "port": 5432},
        ],
    }
    path = _write_config(data)
    try:
        with pytest.raises(Exception, match="Duplicate connection IDs"):
            load_config(path)
    finally:
        os.unlink(path)


def test_duplicate_local_ports_rejected():
    data = {
        "connections": [
            {
                "id": "a",
                "database": "db1",
                "user": "u",
                "port": 5432,
                "tunnel": {"ssh_host": "h1", "local_port": 5555, "remote_port": 5432},
            },
            {
                "id": "b",
                "database": "db2",
                "user": "u",
                "port": 5432,
                "tunnel": {"ssh_host": "h2", "local_port": 5555, "remote_port": 5432},
            },
        ],
    }
    path = _write_config(data)
    try:
        with pytest.raises(Exception, match="Duplicate local_port"):
            load_config(path)
    finally:
        os.unlink(path)


def test_remote_port_default_5432():
    data = {
        "connections": [
            {
                "id": "t",
                "database": "db",
                "user": "u",
                "port": 5432,
                "tunnel": {"ssh_host": "h", "local_port": 6000},
            },
        ],
    }
    path = _write_config(data)
    try:
        cfg = load_config(path)
        assert cfg.connections[0].tunnel.remote_port == 5432
    finally:
        os.unlink(path)
