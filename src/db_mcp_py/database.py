"""Database connection manager with schema filtering and caching."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from time import monotonic

import asyncpg

from .config import ConnectionConfig

logger = logging.getLogger(__name__)


@dataclass
class DatabaseConnection:
    """A managed database connection."""

    config: ConnectionConfig
    effective: dict
    pool: asyncpg.Pool | None = None
    error: str | None = None
    resolved_host: str = ""
    resolved_port: int = 0
    _schema_cache: list[dict] | None = field(default=None, repr=False)
    _schema_cached_at: float = 0

    @property
    def is_connected(self) -> bool:
        return self.pool is not None and self.pool.get_size() > 0

    def get_cached_schema(self) -> list[dict] | None:
        ttl = self.effective.get("schema_cache_ttl", 300)
        if self._schema_cache and (monotonic() - self._schema_cached_at) < ttl:
            return self._schema_cache
        return None

    def set_schema_cache(self, data: list[dict]) -> None:
        self._schema_cache = data
        self._schema_cached_at = monotonic()


_SCHEMA_SQL = """
SELECT
    n.nspname AS table_schema,
    c.relname AS table_name,
    a.attname AS column_name,
    pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
    CASE WHEN a.attnotnull THEN 'NO' ELSE 'YES' END AS is_nullable,
    pg_get_expr(d.adbin, d.adrelid) AS column_default,
    CASE WHEN i.indisprimary THEN true ELSE false END AS is_primary_key
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid
LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
LEFT JOIN pg_catalog.pg_index i ON i.indrelid = c.oid AND a.attnum = ANY(i.indkey) AND i.indisprimary
WHERE c.relkind = 'r'
    AND a.attnum > 0
    AND NOT a.attisdropped
    AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
    AND n.nspname = ANY($1::text[])
ORDER BY n.nspname, c.relname, a.attnum
"""

_SCHEMA_SQL_ALL = """
SELECT
    n.nspname AS table_schema,
    c.relname AS table_name,
    a.attname AS column_name,
    pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
    CASE WHEN a.attnotnull THEN 'NO' ELSE 'YES' END AS is_nullable,
    pg_get_expr(d.adbin, d.adrelid) AS column_default,
    CASE WHEN i.indisprimary THEN true ELSE false END AS is_primary_key
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid
LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
LEFT JOIN pg_catalog.pg_index i ON i.indrelid = c.oid AND a.attnum = ANY(i.indkey) AND i.indisprimary
WHERE c.relkind = 'r'
    AND a.attnum > 0
    AND NOT a.attisdropped
    AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
ORDER BY n.nspname, c.relname, a.attnum
"""


@dataclass
class ConnectionManager:
    """Manages database connection pools."""

    connections: dict[str, DatabaseConnection] = field(default_factory=dict)

    async def connect(self, conn_id: str, db: DatabaseConnection) -> bool:
        """Establish connection pool for a database."""
        cfg = db.config
        host = db.resolved_host or cfg.host
        port = db.resolved_port or cfg.port

        try:
            db.pool = await asyncio.wait_for(
                asyncpg.create_pool(
                    host=host,
                    port=port,
                    database=cfg.database,
                    user=cfg.user,
                    password=cfg.password or None,
                    min_size=1,
                    max_size=db.effective["max_connections"],
                    command_timeout=db.effective["query_timeout"],
                    statement_cache_size=0,
                    server_settings={
                        "default_transaction_read_only": "on",
                        "statement_timeout": str(db.effective["query_timeout"] * 1000),
                    },
                ),
                timeout=db.effective.get("connect_timeout", 15),
            )
            db.error = None
            self.connections[conn_id] = db
            logger.info("Connected to %s (%s:%d/%s)", conn_id, host, port, cfg.database)
            return True
        except Exception as e:
            db.error = str(e)
            db.pool = None
            self.connections[conn_id] = db
            logger.warning("Failed to connect to %s: %s", conn_id, e)
            return False

    async def query(self, conn_id: str, sql: str, *, _retry: bool = True) -> list[dict]:
        """Execute a read-only query."""
        db = self.connections.get(conn_id)
        if not db or not db.pool:
            raise ConnectionError(f"Database '{conn_id}' is not connected")

        logger.info("Query [%s]: %s", conn_id, sql[:200])
        try:
            async with db.pool.acquire() as conn:
                async with conn.transaction(readonly=True):
                    rows = await conn.fetch(sql)
                    return [dict(r) for r in rows]
        except (asyncpg.ConnectionDoesNotExistError, asyncpg.InterfaceError):
            if _retry:
                logger.warning("Connection lost for %s, retrying...", conn_id)
                return await self.query(conn_id, sql, _retry=False)
            raise

    async def get_schema(self, conn_id: str) -> list[dict]:
        """Get schema information using pg_catalog (fast) with PK info."""
        db = self.connections.get(conn_id)
        if not db or not db.pool:
            raise ConnectionError(f"Database '{conn_id}' is not connected")

        cached = db.get_cached_schema()
        if cached is not None:
            return cached

        schemas = db.config.schemas
        async with db.pool.acquire() as conn:
            if schemas:
                rows = await conn.fetch(_SCHEMA_SQL, schemas)
            else:
                rows = await conn.fetch(_SCHEMA_SQL_ALL)

        result = [dict(r) for r in rows]
        db.set_schema_cache(result)
        return result

    async def close_all(self) -> None:
        """Close all connection pools."""
        for conn_id, db in self.connections.items():
            if db.pool:
                await db.pool.close()
                logger.info("Closed pool for %s", conn_id)
        self.connections.clear()
