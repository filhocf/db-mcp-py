# Changelog

## [0.3.0] - 2026-06-24

### Added
- MySQL/MariaDB support fully tested and documented
- 9 unit tests for MySQL path (connect_args, schema discovery, config, tunnel)
- MySQL service container in CI for integration tests

### Changed
- Feature release: MySQL/MariaDB promoted from experimental to supported

## [0.2.2] - 2026-06-24

### Fixed
- VPN detection fallback to TCP probe when `ip route` fails (openconnect compatibility)

## [0.2.1] - 2026-06-22

### Added
- Bandit SAST scanning
- CodeQL analysis workflow
- Coverage reporting (threshold 55%)
- Standards compliance audit

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
