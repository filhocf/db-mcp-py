"""Database connection manager with SQLAlchemy async engine."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from time import monotonic

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from .config import ConnectionConfig

logger = logging.getLogger(__name__)

_URL_SCHEMES: dict[str, str] = {
    "postgresql": "postgresql+asyncpg",
    "mysql": "mysql+aiomysql",
    "oracle": "oracle+oracledb",
}


def _build_url(cfg: ConnectionConfig, host: str, port: int) -> str:
    """Build SQLAlchemy async URL from config."""
    from urllib.parse import quote_plus

    scheme = _URL_SCHEMES.get(cfg.type)
    if not scheme:
        raise ValueError(f"Unsupported database type: {cfg.type}")
    user = quote_plus(cfg.user or "")
    password = quote_plus(cfg.password or "")
    return f"{scheme}://{user}:{password}@{host}:{port}/{cfg.database}"


@dataclass
class DatabaseConnection:
    """A managed database connection."""

    config: ConnectionConfig
    effective: dict
    engine: AsyncEngine | None = None
    error: str | None = None
    resolved_host: str = ""
    resolved_port: int = 0
    _schema_cache: list[dict] | None = field(default=None, repr=False)
    _schema_cached_at: float = 0

    @property
    def is_connected(self) -> bool:
        return self.engine is not None

    def get_cached_schema(self) -> list[dict] | None:
        ttl = self.effective.get("schema_cache_ttl", 300)
        if self._schema_cache and (monotonic() - self._schema_cached_at) < ttl:
            return self._schema_cache
        return None

    def set_schema_cache(self, data: list[dict]) -> None:
        self._schema_cache = data
        self._schema_cached_at = monotonic()


_PG_SCHEMA_SQL = """
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
    AND n.nspname = ANY(:schemas::text[])
ORDER BY n.nspname, c.relname, a.attnum
"""

_PG_SCHEMA_SQL_ALL = """
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
    """Manages database connections via SQLAlchemy async engines."""

    connections: dict[str, DatabaseConnection] = field(default_factory=dict)

    async def connect(self, conn_id: str, db: DatabaseConnection) -> bool:
        """Create async engine for a database."""
        cfg = db.config
        host = db.resolved_host or cfg.host
        port = db.resolved_port or cfg.port

        if cfg.type == "mongodb":
            raise ValueError("Use MongoManager for MongoDB connections")

        try:
            url = _build_url(cfg, host, port)
            connect_args: dict = {}

            if cfg.type == "postgresql":
                connect_args["server_settings"] = {
                    "default_transaction_read_only": "on",
                    "statement_timeout": str(db.effective["query_timeout"] * 1000),
                }
            elif cfg.type == "mysql":
                connect_args["init_command"] = "SET SESSION TRANSACTION READ ONLY"
            elif cfg.type == "oracle":
                connect_args["stmtcachesize"] = 0  # Oracle read-only via event below

            events = {}
            if cfg.type == "oracle":

                def _set_oracle_readonly(dbapi_conn, connection_record):
                    cursor = dbapi_conn.cursor()
                    cursor.execute("SET TRANSACTION READ ONLY")
                    cursor.close()

                events["connect"] = _set_oracle_readonly

            db.engine = await asyncio.wait_for(
                _create_engine(
                    url,
                    pool_size=db.effective["max_connections"],
                    pool_pre_ping=True,
                    connect_args=connect_args,
                    events=events,
                ),
                timeout=db.effective.get("connect_timeout", 15),
            )
            db.error = None
            self.connections[conn_id] = db
            logger.info("Connected to %s (%s:%d/%s)", conn_id, host, port, cfg.database)
            return True
        except Exception as e:
            db.error = str(e)
            db.engine = None
            self.connections[conn_id] = db
            logger.warning("Failed to connect to %s: %s", conn_id, e)
            return False

    async def query(self, conn_id: str, sql: str, *, _retry: bool = True) -> list[dict]:
        """Execute a read-only query."""
        db = self.connections.get(conn_id)
        if not db or not db.engine:
            raise ConnectionError(f"Database '{conn_id}' is not connected")

        logger.debug("Query [%s]: %s", conn_id, sql[:200])
        timeout = db.effective.get("query_timeout", 30)
        try:
            async with db.engine.connect() as conn:
                result = await asyncio.wait_for(conn.execute(text(sql)), timeout=timeout)
                return [dict(row._mapping) for row in result]
        except Exception as e:
            if _retry and "connection" in str(e).lower():
                logger.warning("Connection lost for %s, retrying...", conn_id)
                return await self.query(conn_id, sql, _retry=False)
            raise

    async def get_schema(self, conn_id: str) -> list[dict]:
        """Get schema information."""
        db = self.connections.get(conn_id)
        if not db or not db.engine:
            raise ConnectionError(f"Database '{conn_id}' is not connected")

        cached = db.get_cached_schema()
        if cached is not None:
            return cached

        if db.config.type == "postgresql":
            result = await self._pg_schema(db)
        else:
            result = await self._generic_schema(db)

        db.set_schema_cache(result)
        return result

    async def _pg_schema(self, db: DatabaseConnection) -> list[dict]:
        """PostgreSQL schema via pg_catalog."""
        async with db.engine.connect() as conn:
            if db.config.schemas:
                result = await conn.execute(text(_PG_SCHEMA_SQL), {"schemas": db.config.schemas})
            else:
                result = await conn.execute(text(_PG_SCHEMA_SQL_ALL))
            return [dict(row._mapping) for row in result]

    async def _generic_schema(self, db: DatabaseConnection) -> list[dict]:
        """Schema discovery via SQLAlchemy inspect (MySQL, Oracle)."""
        from sqlalchemy import inspect as sa_inspect

        async with db.engine.connect() as conn:
            # Run inspect in sync context via run_sync
            def _inspect(sync_conn):
                insp = sa_inspect(sync_conn)
                rows = []
                for schema_name in db.config.schemas or [None]:
                    tables = insp.get_table_names(schema=schema_name)
                    for table in tables:
                        pk_cols = set()
                        pk_info = insp.get_pk_constraint(table, schema=schema_name)
                        if pk_info:
                            pk_cols = set(pk_info.get("constrained_columns", []))
                        for col in insp.get_columns(table, schema=schema_name):
                            rows.append(
                                {
                                    "table_schema": schema_name or "default",
                                    "table_name": table,
                                    "column_name": col["name"],
                                    "data_type": str(col["type"]),
                                    "is_nullable": "YES" if col.get("nullable", True) else "NO",
                                    "column_default": str(col.get("default")) if col.get("default") else None,
                                    "is_primary_key": col["name"] in pk_cols,
                                }
                            )
                return rows

            return await conn.run_sync(_inspect)

    async def close_all(self) -> None:
        """Dispose all engines."""
        for conn_id, db in self.connections.items():
            if db.engine:
                await db.engine.dispose()
                logger.info("Disposed engine for %s", conn_id)
        self.connections.clear()


async def _create_engine(url: str, **kwargs) -> AsyncEngine:
    """Create and verify an async engine."""


async def _create_engine(url: str, **kwargs) -> AsyncEngine:
    events = kwargs.pop("events", {})
    engine = create_async_engine(url, **kwargs)
    # Apply driver-level events (e.g., Oracle read-only)
    if events:
        from sqlalchemy import event as sa_event

        for event_name, handler in events.items():
            sa_event.listen(engine.sync_engine, event_name, handler)
    # Verify connectivity
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return engine
