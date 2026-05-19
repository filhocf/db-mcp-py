# AGENTS.md — db-mcp-py

## Project Overview

Multi-database MCP server supporting PostgreSQL, MySQL, Oracle, and MongoDB with SSH tunnels, read-only enforcement, and schema filtering. Designed for AI agents that need safe, read-only database access.

## Architecture

```
src/db_mcp_py/
├── __init__.py       # Package version
├── server.py         # MCP server (mcp.server.Server) — registers 3 tools: query, schema, list_databases
├── config.py         # Pydantic config loader (JSON/env) — DatabaseConfig, TunnelConfig
├── database.py       # SQLAlchemy async engine manager — connect, query, get_schema (PG/MySQL/Oracle)
├── mongo.py          # Motor async MongoDB manager — connect, query, get_schema
├── schema.py         # Schema introspection (tables, columns, types, constraints)
├── tunnels.py        # SSH tunnel manager (sshtunnel library)

tests/
├── test_server.py    # MCP tool integration tests
├── test_database.py  # SQLAlchemy engine + query tests
├── test_mongo.py     # MongoDB manager tests
├── test_config.py    # Config loading tests
├── test_security.py  # SQL injection + read-only enforcement tests
├── test_tunnels.py   # SSH tunnel tests
```

## Data Flow

```
AI Agent → MCP (stdio/HTTP) → server.py → database.py/mongo.py → [SSH tunnel] → Database
```

## Key Conventions

- **Config**: JSON file via `-c` flag or env vars (`DB_MCP_CONFIG`)
- **Read-only**: Multi-layer enforcement (SQL whitelist + DB-level SET TRANSACTION READ ONLY + Oracle events)
- **SQL validation**: `validate_sql()` whitelists only SELECT/WITH/EXPLAIN/SHOW
- **MongoDB**: `_sanitize_mongo_filter()` blocks dangerous operators ($where, $function, $accumulator, $expr)
- **Errors**: Returns error strings in TextContent (low-level MCP SDK, not FastMCP)
- **Transport**: stdio (default) + streamable-http (via `MCP_TRANSPORT` env)

## Adding a New Database Type

1. Create manager class in new file (e.g., `redis.py`) following `MongoManager` pattern
2. Add config type to `DatabaseConfig` in `config.py`
3. Register in `server.py` connect/query/schema handlers
4. Add tests in `tests/test_{type}.py`
5. Run: `uv run pytest tests/ -v && uv run ruff check src/ tests/`

## Tests

```bash
uv sync --group dev
uv run pytest tests/ -v --tb=short
uv run ruff check src/ tests/
```

Integration tests (PG/MySQL/MongoDB) run in CI with service containers. Locally they skip.
