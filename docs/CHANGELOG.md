# Changelog

All notable changes to this project will be documented in this file.

## [0.1.0] — 2025-05-04

### Added

- Initial release
- Multi-database PostgreSQL support via asyncpg
- SSH tunnel management with asyncssh (including jump host support)
- Read-only SQL enforcement (prefix validation + transaction-level)
- Schema introspection with optional schema filtering
- Pydantic-based configuration with environment variable expansion (`${VAR}`)
- VPN detection via route table inspection
- Graceful degradation — failed connections don't block server startup
- MCP tools: `list_databases`, `query`, `schema`
- Custom JSON serialization for PostgreSQL types (datetime, Decimal, UUID, bytes)
- Configurable per-connection: query timeout, max connections, SSL, read-only override
