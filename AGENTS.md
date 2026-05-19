# AGENTS.md — db-mcp-py

## Project Overview

Multi-database MCP server supporting PostgreSQL, MySQL, Oracle, and MongoDB with SSH tunnels, read-only enforcement, and schema filtering. Designed for AI agents that need safe, read-only database access.

## Architecture

```
src/db_mcp_py/
├── __init__.py       # Package version
├── server.py         # MCP server (mcp.server.Server) — 3 tools: query, schema, list_databases
├── config.py         # Pydantic config loader (JSON/env) — DatabaseConfig, TunnelConfig
├── database.py       # SQLAlchemy async engine manager (PG/MySQL/Oracle)
├── mongo.py          # Motor async MongoDB manager
├── schema.py         # Schema introspection (tables, columns, types, constraints)
├── tunnels.py        # SSH tunnel manager (sshtunnel library)
```

## Data Flow

```
AI Agent → MCP (stdio/HTTP) → server.py → database.py/mongo.py → [SSH tunnel] → Database
```

## Key Conventions

- **Config**: JSON file via `-c` flag or env vars
- **Read-only**: SQL whitelist + DB session + Oracle events (3 layers)
- **MongoDB**: _sanitize_mongo_filter blocks dangerous operators
- **Transport**: stdio (default) + streamable-http (MCP_TRANSPORT env)

## Tests

```bash
uv sync --group dev
uv run pytest tests/ -v --tb=short
uv run ruff check src/ tests/
```
