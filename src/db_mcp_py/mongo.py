"""MongoDB connection manager via motor."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from time import monotonic

from .config import ConnectionConfig

logger = logging.getLogger(__name__)


@dataclass
class MongoConnection:
    """A managed MongoDB connection."""

    config: ConnectionConfig
    effective: dict
    client: object | None = None
    db: object | None = None
    error: str | None = None
    resolved_host: str = ""
    resolved_port: int = 0
    _schema_cache: list[dict] | None = field(default=None, repr=False)
    _schema_cached_at: float = 0

    @property
    def is_connected(self) -> bool:
        return self.client is not None

    def get_cached_schema(self) -> list[dict] | None:
        ttl = self.effective.get("schema_cache_ttl", 300)
        if self._schema_cache and (monotonic() - self._schema_cached_at) < ttl:
            return self._schema_cache
        return None

    def set_schema_cache(self, data: list[dict]) -> None:
        self._schema_cache = data
        self._schema_cached_at = monotonic()


@dataclass
class MongoManager:
    """Manages MongoDB connections via motor."""

    connections: dict[str, MongoConnection] = field(default_factory=dict)

    async def connect(self, conn_id: str, mc: MongoConnection) -> bool:
        """Establish MongoDB connection."""
        try:
            from motor.motor_asyncio import AsyncIOMotorClient
        except ImportError as e:
            mc.error = "motor not installed. Install with: pip install db-mcp-py[mongo]"
            self.connections[conn_id] = mc
            logger.warning("motor not available: %s", e)
            return False

        cfg = mc.config
        host = mc.resolved_host or cfg.host
        port = mc.resolved_port or cfg.port

        try:
            url = f"mongodb://{cfg.user}:{cfg.password}@{host}:{port}" if cfg.password else f"mongodb://{host}:{port}"
            client = AsyncIOMotorClient(
                url,
                serverSelectionTimeoutMS=mc.effective.get("connect_timeout", 15) * 1000,
            )
            # Verify connectivity
            await client.admin.command("ping")
            mc.client = client
            mc.db = client[cfg.database]
            mc.error = None
            self.connections[conn_id] = mc
            logger.info("Connected to MongoDB %s (%s:%d/%s)", conn_id, host, port, cfg.database)
            return True
        except Exception as e:
            mc.error = str(e)
            mc.client = None
            self.connections[conn_id] = mc
            logger.warning("Failed to connect to MongoDB %s: %s", conn_id, e)
            return False

    async def query(self, conn_id: str, query_str: str) -> list[dict]:
        """Execute a read-only MongoDB query (JSON filter on a collection)."""
        mc = self.connections.get(conn_id)
        if not mc or not mc.db:
            raise ConnectionError(f"MongoDB '{conn_id}' is not connected")

        # Expected format: {"collection": "name", "filter": {...}, "limit": N}
        params = json.loads(query_str)
        collection = params.get("collection")
        if not collection:
            raise ValueError("MongoDB query requires 'collection' field")

        filt = params.get("filter", {})
        limit = params.get("limit", 100)
        projection = params.get("projection")

        cursor = mc.db[collection].find(filt, projection).limit(limit)
        results = []
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            results.append(doc)
        return results

    async def get_schema(self, conn_id: str) -> list[dict]:
        """Get schema by sampling collections."""
        mc = self.connections.get(conn_id)
        if not mc or not mc.db:
            raise ConnectionError(f"MongoDB '{conn_id}' is not connected")

        cached = mc.get_cached_schema()
        if cached is not None:
            return cached

        collections = await mc.db.list_collection_names()
        result = []
        for coll_name in sorted(collections):
            sample = await mc.db[coll_name].find_one()
            if sample:
                for key, value in sample.items():
                    result.append(
                        {
                            "table_schema": "default",
                            "table_name": coll_name,
                            "column_name": key,
                            "data_type": type(value).__name__,
                            "is_nullable": "YES",
                            "column_default": None,
                            "is_primary_key": key == "_id",
                        }
                    )

        mc.set_schema_cache(result)
        return result

    async def close_all(self) -> None:
        """Close all MongoDB connections."""
        for conn_id, mc in self.connections.items():
            if mc.client:
                mc.client.close()
                logger.info("Closed MongoDB %s", conn_id)
        self.connections.clear()
