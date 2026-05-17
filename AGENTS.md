# AGENTS.md

## Project Overview

MCP server for database access — read-only SQL queries and schema discovery across multiple databases. Built with Python (FastMCP), supports PostgreSQL and SQLite. Stdio transport. Entry point: `src/db_mcp_py/server.py`.

## Architecture

```
src/db_mcp_py/
├── server.py            ← FastMCP server, registers tools (query, schema, list_databases)
├── config.py            ← Database config loading from YAML/env
├── adapters/
│   ├── base.py          ← Abstract adapter interface
│   ├── postgres.py      ← asyncpg adapter (read-only queries, schema)
│   └── sqlite.py        ← aiosqlite adapter
└── utils.py             ← Query sanitization, LIMIT enforcement
```

**Data flow:** Tool call → resolve database by ID → get adapter → execute query (read-only) → format result → return.

## Key Conventions

- **Read-only**: all queries are SELECT only. No mutations allowed.
- **LIMIT enforcement**: queries without LIMIT get one appended automatically.
- **Config**: databases defined in `config.yaml` or env vars (`DB_MCP_*`).
- **Adapters**: each DB type has an async adapter implementing `query()` and `schema()`.
- **Resilient**: if a database is unreachable, other databases still work.

## Adding a New Database Adapter

1. Create `src/db_mcp_py/adapters/{driver}.py` implementing `BaseAdapter`.
2. Implement `async query(sql, params)` and `async get_schema()`.
3. Register in `config.py` type mapping.
4. Add tests in `tests/test_{driver}.py`.

## Tests

```bash
pytest                        # All tests
pytest tests/test_sqlite.py   # SQLite adapter only
```

- Tests use in-memory SQLite (no external DB needed).
- PostgreSQL tests require a running instance (skipped if unavailable).
