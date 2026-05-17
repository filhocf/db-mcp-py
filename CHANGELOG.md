# Changelog

## [0.2.0] - 2026-05-06

### Added
- SSH tunnel support for remote databases
- Resilient connection handling (graceful failure without VPN)
- Multiple database configs via YAML

### Fixed
- Connection timeout handling

## [0.1.0] - 2026-04-20

### Added
- Initial release
- PostgreSQL adapter (asyncpg)
- SQLite adapter (aiosqlite)
- Read-only query execution with LIMIT enforcement
- Schema discovery (tables, columns, types)
- MCP tools: query, schema, list_databases
