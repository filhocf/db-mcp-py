"""Tests for write operations (issue #13)."""

import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from db_mcp_py.config import ConnectionConfig, Config, DefaultsConfig, load_config
from db_mcp_py.server import validate_sql, validate_write_sql


# --- Config: permission field ---


def _write_config(data: dict) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(data, f)
    f.close()
    return f.name


class TestPermissionConfig:
    """Test permission field in config."""

    def test_default_permission_is_readonly(self):
        cfg = ConnectionConfig(id="test", database="db", user="u", port=5432)
        assert cfg.permission == "readonly"

    def test_permission_readwrite(self):
        cfg = ConnectionConfig(id="test", database="db", user="u", port=5432, permission="readwrite")
        assert cfg.permission == "readwrite"

    def test_permission_admin(self):
        cfg = ConnectionConfig(id="test", database="db", user="u", port=5432, permission="admin")
        assert cfg.permission == "admin"

    def test_invalid_permission_rejected(self):
        with pytest.raises(Exception):
            ConnectionConfig(id="test", database="db", user="u", port=5432, permission="superuser")

    def test_config_file_with_permission(self):
        data = {
            "connections": [
                {"id": "writable_db", "database": "mydb", "user": "me", "port": 5432, "permission": "readwrite"},
            ]
        }
        path = _write_config(data)
        try:
            cfg = load_config(path)
            assert cfg.connections[0].permission == "readwrite"
        finally:
            os.unlink(path)

    def test_read_only_true_conflicts_with_readwrite(self):
        with pytest.raises(Exception):
            ConnectionConfig(id="test", database="db", user="u", port=5432, read_only=True, permission="readwrite")

    def test_read_only_none_allows_readwrite(self):
        cfg = ConnectionConfig(id="test", database="db", user="u", port=5432, read_only=None, permission="readwrite")
        assert cfg.permission == "readwrite"


# --- SQL validation for writes ---


class TestValidateWriteSql:
    """Test write SQL validation."""

    def test_insert_allowed(self):
        assert validate_write_sql("INSERT INTO t (a) VALUES (1)", "readwrite") is None

    def test_update_allowed(self):
        assert validate_write_sql("UPDATE t SET a=1 WHERE id=2", "readwrite") is None

    def test_delete_with_where_allowed(self):
        assert validate_write_sql("DELETE FROM t WHERE id=1", "readwrite") is None

    def test_delete_without_where_rejected(self):
        err = validate_write_sql("DELETE FROM t", "readwrite")
        assert err is not None
        assert "WHERE" in err

    def test_delete_without_where_forced(self):
        assert validate_write_sql("DELETE FROM t", "readwrite", force=True) is None

    def test_truncate_rejected_readwrite(self):
        err = validate_write_sql("TRUNCATE t", "readwrite")
        assert err is not None
        assert "admin" in err.lower()

    def test_truncate_allowed_admin(self):
        assert validate_write_sql("TRUNCATE t", "admin") is None

    def test_drop_rejected_readwrite(self):
        err = validate_write_sql("DROP TABLE t", "readwrite")
        assert err is not None

    def test_drop_allowed_admin(self):
        assert validate_write_sql("DROP TABLE t", "admin") is None

    def test_select_rejected(self):
        """Write tool should not accept SELECT."""
        err = validate_write_sql("SELECT * FROM t", "readwrite")
        assert err is not None

    def test_readonly_rejects_all(self):
        err = validate_write_sql("INSERT INTO t (a) VALUES (1)", "readonly")
        assert err is not None
        assert "readonly" in err.lower() or "read-only" in err.lower()


# --- Audit log ---


class TestAuditLog:
    """Test audit logging for write operations."""

    def test_audit_log_created(self, tmp_path):
        from db_mcp_py.server import write_audit_log

        log_path = tmp_path / "audit.log"
        write_audit_log(str(log_path), "test_db", "INSERT INTO t VALUES (1)", "success")

        assert log_path.exists()
        content = log_path.read_text()
        assert "test_db" in content
        assert "INSERT" in content
        assert "success" in content

    def test_audit_log_appends(self, tmp_path):
        from db_mcp_py.server import write_audit_log

        log_path = tmp_path / "audit.log"
        write_audit_log(str(log_path), "db1", "INSERT INTO a VALUES (1)", "success")
        write_audit_log(str(log_path), "db2", "UPDATE b SET x=1", "success")

        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 2
