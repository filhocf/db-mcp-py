# Architecture — db-mcp-py

## Overview

Python MCP server providing read-only access to multiple PostgreSQL databases with SSH tunnel support, schema filtering, and graceful degradation.

## Stack

- **Runtime**: Python 3.11+
- **Protocol**: MCP via `mcp` SDK, stdio transport
- **Database**: asyncpg (async PostgreSQL driver)
- **Tunnels**: asyncssh (async SSH client)
- **Config**: Pydantic models with env var expansion

## Module Layout

```
src/db_mcp_py/
├── server.py    # MCP server — tool registration, SQL validation, dispatch
├── config.py    # Pydantic config models, env var expansion, validation
├── database.py  # Connection pool management, query execution, schema introspection
└── tunnels.py   # SSH tunnel lifecycle, VPN detection
```

## Configuration

JSON config file with `os.path.expandvars()` applied before parsing — supports `${ENV_VAR}` references for secrets.

```json
{
  "defaults": { "read_only": true, "query_timeout": 30, "max_connections": 5 },
  "connections": [
    { "id": "prod", "host": "${DB_HOST}", "database": "app", "user": "${DB_USER}", ... }
  ]
}
```

Validation: unique connection IDs, unique tunnel local ports.

## Connection Lifecycle

```
startup()
  ├── check VPN (route table inspection)
  ├── for each connection:
  │   ├── skip if requires_vpn and no VPN detected
  │   ├── open SSH tunnel (if configured)
  │   └── create asyncpg pool (or record error)
  └── server ready

shutdown()
  ├── close all connection pools
  └── close all SSH tunnels
```

## Graceful Degradation

Connections that fail (tunnel error, VPN missing, auth failure) are recorded with their error message but don't prevent the server from starting. The `list_databases` tool shows connection status including errors.

## Security: Read-Only Enforcement

Defense in depth — two layers:
1. **SQL validation**: regex-based prefix check (only SELECT/WITH/EXPLAIN/SHOW) + write-operation pattern detection
2. **asyncpg transaction**: queries execute inside `SET TRANSACTION READ ONLY` blocks

## SSH Tunnels

- Managed by `TunnelManager` using asyncssh
- Support for jump hosts (ProxyJump equivalent)
- Each tunnel binds to a configured `local_port`
- Automatic cleanup on shutdown

## Schema Filtering

Connections can specify `schemas: ["public", "app"]` to restrict which schemas are visible in the `schema` tool output. Queries are not restricted — filtering is informational.

## MCP Tools

| Tool | Purpose |
|------|---------|
| `list_databases` | Show all configured databases and their connection status |
| `query` | Execute read-only SQL (SELECT/WITH/EXPLAIN/SHOW only) |
| `schema` | Get table/column metadata for a database |

## Type Serialization

Custom JSON serializer handles: datetime, date, time, timedelta, Decimal, UUID, bytes, memoryview.
