"""MCP server: multi-database with SSH tunnels, read-only, schema filtering."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .config import Config, load_config
from .database import ConnectionManager, DatabaseConnection
from .mongo import MongoConnection, MongoManager
from .schema import get_schema
from .tunnels import TunnelManager, check_vpn

logger = logging.getLogger(__name__)

# --- SQL validation (defense in depth) ---

_ALLOWED_PREFIXES = ("SELECT", "WITH", "EXPLAIN", "SHOW")

_WRITE_PATTERN = re.compile(
    r"\b("
    r"INSERT|UPDATE|DELETE|TRUNCATE|DROP|ALTER|CREATE|GRANT|REVOKE|"
    r"COPY|DO|CALL|LOCK|VACUUM|REINDEX|CLUSTER|REFRESH|"
    r"PREPARE|EXECUTE|DEALLOCATE|DISCARD|COMMENT|NOTIFY|LISTEN|"
    r"IMPORT|SECURITY\s+LABEL"
    r")\b",
    re.IGNORECASE,
)


def validate_sql(sql: str) -> str | None:
    """Return error message if SQL is not safe, None if OK."""
    # Strip block comments and line comments
    cleaned = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    cleaned = re.sub(r"--.*$", "", cleaned, flags=re.MULTILINE)
    cleaned = cleaned.strip()

    if not cleaned:
        return "Empty query"

    first_word = cleaned.split()[0].upper()
    if first_word not in _ALLOWED_PREFIXES:
        return f"Only SELECT/WITH/EXPLAIN/SHOW allowed (got {first_word})"

    # For EXPLAIN ANALYZE, skip the EXPLAIN ANALYZE prefix before checking
    check_text = cleaned
    if first_word == "EXPLAIN":
        check_text = re.sub(r"^EXPLAIN\s+(ANALYZE\s+)?", "", check_text, flags=re.IGNORECASE)

    if _WRITE_PATTERN.search(check_text):
        match = _WRITE_PATTERN.search(check_text)
        return f"Write operation detected: {match.group(0)}"

    return None


# --- JSON serializer ---


def _json_serializer(obj: object) -> object:
    """Handle types that json.dumps can't serialize natively."""
    if isinstance(obj, (datetime, date, time)):
        return obj.isoformat()
    if isinstance(obj, timedelta):
        return str(obj)
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, (bytes, memoryview)):
        return bytes(obj).hex() if isinstance(obj, memoryview) else obj.hex()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


# --- Global state ---

_config: Config | None = None
_tunnel_mgr = TunnelManager()
_conn_mgr = ConnectionManager()
_mongo_mgr = MongoManager()


async def _startup() -> None:
    """Connect to all configured databases, opening tunnels as needed."""
    assert _config is not None
    vpn_prefixes = _config.defaults.vpn_route_prefixes or None
    vpn_targets = [(c.host, c.port) for c in _config.connections if c.require_vpn]
    has_vpn = check_vpn(vpn_prefixes, probe_targets=vpn_targets or None)
    logger.info("VPN detected: %s", has_vpn)

    for conn_cfg in _config.connections:
        conn_id = conn_cfg.id

        if conn_cfg.require_vpn and not has_vpn:
            logger.info("Skipping %s: requires VPN", conn_id)
            if conn_cfg.type == "mongodb":
                _mongo_mgr.connections[conn_id] = MongoConnection(
                    config=conn_cfg,
                    effective=_config.get_effective(conn_cfg),
                    error="VPN not available",
                )
            else:
                _conn_mgr.connections[conn_id] = DatabaseConnection(
                    config=conn_cfg,
                    effective=_config.get_effective(conn_cfg),
                    error="VPN not available",
                )
            continue

        resolved_host = ""
        resolved_port = 0

        if conn_cfg.tunnel:
            try:
                resolved_host, resolved_port = await _tunnel_mgr.open(conn_id, conn_cfg.tunnel)
            except Exception as e:
                logger.warning("Skipping %s: tunnel failed: %s", conn_id, e)
                if conn_cfg.type == "mongodb":
                    _mongo_mgr.connections[conn_id] = MongoConnection(
                        config=conn_cfg,
                        effective=_config.get_effective(conn_cfg),
                        error=f"Tunnel failed: {e}",
                    )
                else:
                    db = DatabaseConnection(config=conn_cfg, effective=_config.get_effective(conn_cfg))
                    db.error = f"Tunnel failed: {e}"
                    _conn_mgr.connections[conn_id] = db
                continue

        if conn_cfg.type == "mongodb":
            mc = MongoConnection(
                config=conn_cfg,
                effective=_config.get_effective(conn_cfg),
                resolved_host=resolved_host,
                resolved_port=resolved_port,
            )
            await _mongo_mgr.connect(conn_id, mc)
        else:
            db = DatabaseConnection(
                config=conn_cfg,
                effective=_config.get_effective(conn_cfg),
                resolved_host=resolved_host,
                resolved_port=resolved_port,
            )
            await _conn_mgr.connect(conn_id, db)


async def _shutdown() -> None:
    """Clean shutdown."""
    await _conn_mgr.close_all()
    await _mongo_mgr.close_all()
    await _tunnel_mgr.close_all()


def _create_server() -> Server:
    """Create and configure the MCP server."""
    server = Server("db-mcp-py")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        connected = [cid for cid, db in _conn_mgr.connections.items() if db.is_connected]
        connected += [cid for cid, mc in _mongo_mgr.connections.items() if mc.is_connected]
        db_enum_desc = ", ".join(connected) if connected else "(none connected)"

        return [
            Tool(
                name="list_databases",
                description="List all configured databases and their connection status",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="query",
                description=f"Execute a read-only SQL query. Available databases: {db_enum_desc}",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "database": {"type": "string", "description": f"Database ID. One of: {db_enum_desc}"},
                        "sql": {"type": "string", "description": "SQL query (SELECT only). Always use LIMIT."},
                    },
                    "required": ["database", "sql"],
                },
            ),
            Tool(
                name="schema",
                description=f"Get table/column info for a database. Available: {db_enum_desc}",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "database": {"type": "string", "description": f"Database ID. One of: {db_enum_desc}"},
                    },
                    "required": ["database"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        try:
            if name == "list_databases":
                result = [
                    {
                        "id": cid,
                        "type": db.config.type,
                        "database": db.config.database,
                        "connected": db.is_connected,
                        "error": db.error,
                        "schemas": db.config.schemas or ["(all)"],
                        "read_only": db.effective.get("read_only", True),
                        "has_tunnel": db.config.tunnel is not None,
                    }
                    for cid, db in _conn_mgr.connections.items()
                ]
                result += [
                    {
                        "id": cid,
                        "type": "mongodb",
                        "database": mc.config.database,
                        "connected": mc.is_connected,
                        "error": mc.error,
                        "schemas": [],
                        "read_only": True,
                        "has_tunnel": mc.config.tunnel is not None,
                    }
                    for cid, mc in _mongo_mgr.connections.items()
                ]
                return [TextContent(type="text", text=json.dumps(result, indent=2))]

            elif name == "query":
                db_id = arguments["database"]
                sql = arguments["sql"].strip()

                # MongoDB: pass JSON query directly
                if db_id in _mongo_mgr.connections:
                    rows = await _mongo_mgr.query(db_id, sql)
                    text_out = json.dumps(rows, indent=2, default=_json_serializer)
                    return [TextContent(type="text", text=f"({len(rows)} docs)\n{text_out}")]

                error = validate_sql(sql)
                if error:
                    return [TextContent(type="text", text=f"Error: {error}")]

                rows = await _conn_mgr.query(db_id, sql)
                text_out = json.dumps(rows, indent=2, default=_json_serializer)
                return [TextContent(type="text", text=f"({len(rows)} rows)\n{text_out}")]

            elif name == "schema":
                db_id = arguments["database"]
                rows = await get_schema(db_id, _conn_mgr, _mongo_mgr)

                tables: dict[str, list] = {}
                for row in rows:
                    key = f"{row['table_schema']}.{row['table_name']}"
                    tables.setdefault(key, []).append(
                        {
                            "column": row["column_name"],
                            "type": row["data_type"],
                            "nullable": row["is_nullable"],
                            "default": row.get("column_default"),
                            "primary_key": row.get("is_primary_key", False),
                        }
                    )

                return [TextContent(type="text", text=json.dumps(tables, indent=2, default=_json_serializer))]

            else:
                return [TextContent(type="text", text=f"Unknown tool: {name}")]

        except Exception as e:
            logger.exception("Tool %s failed", name)
            return [TextContent(type="text", text=f"Error: {e}")]

    return server


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(description="db-mcp-py: Multi-database MCP server")
    parser.add_argument("-c", "--config", required=True, help="Path to config.json")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "streamable-http"],
        help="Transport mode (default: stdio)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=3200, help="HTTP port (default: 3200)")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    global _config
    _config = load_config(args.config)
    logger.info("Loaded config: %d connections", len(_config.connections))

    server = _create_server()

    if args.transport == "streamable-http":
        asyncio.run(_run_http(server, args.host, args.port))
    else:
        asyncio.run(_run_stdio(server))


async def _run_stdio(server: Server) -> None:
    """Run server over stdio transport."""
    await _startup()
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    finally:
        await _shutdown()


async def _run_http(server: Server, host: str, port: int) -> None:
    """Run server over StreamableHTTP transport."""
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    import uvicorn

    await _startup()

    session_manager = StreamableHTTPSessionManager(
        app=server,
        stateless=True,
    )

    async def lifespan(app):
        async with session_manager.run():
            yield

    starlette_app = Starlette(
        routes=[Mount("/", app=session_manager.handle_request)],
        lifespan=lifespan,
    )

    logger.info("Serving db-mcp-py via StreamableHTTP on http://%s:%d/mcp", host, port)

    config = uvicorn.Config(starlette_app, host=host, port=port, log_level="info")
    uvi_server = uvicorn.Server(config)

    try:
        await uvi_server.serve()
    finally:
        await _shutdown()


if __name__ == "__main__":
    main()
