# db-mcp-py

Multi-database MCP server with built-in SSH tunnels, read-only mode, and schema filtering.

## Credits & References

This project is a Python reimplementation inspired by [FreePeak/db-mcp-server](https://github.com/FreePeak/db-mcp-server) (Go, MIT License, 372+ stars). The original project provides multi-database MCP support for MySQL, PostgreSQL, SQLite, Oracle, and TimescaleDB.

**Why rewrite?** We needed features the upstream doesn't provide and the solo-maintainer project has slow response times:
- Built-in SSH tunnel management (jump hosts, port forwarding)
- Read-only mode by default (no accidental writes)
- Schema filtering (whitelist specific schemas per connection)
- Environment variable expansion in config
- Graceful degradation (if 1 of N databases is unreachable, serve the rest)
- VPN/network detection before connecting
- Python ecosystem integration (uvx, PyPI, MCP SDK)

## Features

- **Multi-database**: Connect to N PostgreSQL databases simultaneously
- **SSH tunnels**: Built-in asyncssh tunnels with jump host support — no external scripts needed
- **Read-only**: Only `query` and `schema` tools exposed. No `execute`, `transaction`, or DDL
- **Schema filtering**: Per-connection `schemas` whitelist — only expose relevant tables
- **Env var expansion**: Use `${DB_PASSWORD}`, `${HOME}` etc. in config
- **Graceful degradation**: Unreachable databases are skipped, rest keeps working
- **Network detection**: Optional VPN check before attempting connections
- **Reconnect**: Automatic retry on connection loss with exponential backoff
- **Unified tools**: Single `query` tool with `database` parameter (inspired by FreePeak v1.9.0)

## Installation

```bash
# Via uvx (recommended)
uvx db-mcp-py --config config.json

# Via pip
pip install db-mcp-py
db-mcp-py --config config.json
```

## Configuration

```json
{
  "defaults": {
    "read_only": true,
    "query_timeout": 30,
    "max_connections": 5
  },
  "connections": [
    {
      "id": "mir_dev",
      "host": "10.202.171.138",
      "port": 5433,
      "database": "p9scdevmir",
      "user": "mir_intranet",
      "password": "${MIR_DEV_PASSWORD}",
      "schemas": ["mir", "public"],
      "require_vpn": true
    },
    {
      "id": "sicar_dev",
      "host": "localhost",
      "port": 5433,
      "database": "car_nacional",
      "user": "car_nacional",
      "schemas": ["usr_geocar_aplicacao"],
      "tunnel": {
        "ssh_host": "coreapi-sicar-dev",
        "remote_host": "localhost",
        "remote_port": 5433,
        "local_port": 5435
      }
    },
    {
      "id": "coreapi_dev",
      "host": "localhost",
      "port": 5433,
      "database": "pgsc_dev_coreapi",
      "user": "postgres",
      "tunnel": {
        "ssh_host": "coreapi-db-dev",
        "remote_host": "localhost",
        "remote_port": 5433,
        "local_port": 5434
      }
    }
  ]
}
```

### Tunnel with jump host

```json
{
  "tunnel": {
    "ssh_host": "target-server",
    "jump_host": "bastion.example.com",
    "remote_host": "localhost",
    "remote_port": 5433,
    "local_port": 5435
  }
}
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `list_databases` | List all connected databases with status |
| `query` | Execute read-only SQL query on a database |
| `schema` | Get table/column information for a database |

## License

MIT — see [LICENSE](LICENSE).
