# SPEC: db-mcp-py Multi-Database Support

## Objetivo
Migrar de asyncpg (PostgreSQL-only) para SQLAlchemy async + motor, suportando PostgreSQL, MySQL, Oracle e MongoDB.

## Motivação
- Atualmente só PostgreSQL (asyncpg hardcoded)
- Dataprev usa Oracle em vários sistemas
- MongoDB para dados não-estruturados (logs, documentos)
- MySQL para sistemas legados

## Design

### Arquitetura

```
db-mcp-py/
├── src/db_mcp_py/
│   ├── server.py          → MCP server (sem mudanças na interface)
│   ├── config.py          → ConnectionConfig + tipo de driver
│   ├── database.py        → SQLAlchemy async engine (PG, MySQL, Oracle)
│   ├── mongo.py           → motor async (MongoDB, caso especial)
│   ├── schema.py          → Schema discovery unificado
│   └── tunnels.py         → SSH tunnels (sem mudanças)
```

### Interface (sem breaking changes para o usuário)

```yaml
databases:
  mir_dev:
    type: postgresql          # novo campo (default: postgresql)
    host: 10.202.171.138
    port: 5433
    database: p9scdevmir
    user: mir_intranet
    
  legacy_oracle:
    type: oracle
    host: 10.195.16.20
    port: 1521
    database: ORCL
    user: app_user
    
  logs_mongo:
    type: mongodb
    host: localhost
    port: 27017
    database: app_logs
```

### Drivers (extras opcionais)

| DB | Driver | Extra | URL scheme |
|---|---|---|---|
| PostgreSQL | asyncpg | (default, sempre incluso) | `postgresql+asyncpg://` |
| MySQL | aiomysql | `[mysql]` | `mysql+aiomysql://` |
| Oracle | oracledb | `[oracle]` | `oracle+oracledb://` |
| MongoDB | motor | `[mongo]` | N/A (não SQLAlchemy) |
| Todos | — | `[all]` | — |

### Schema Discovery

| DB | Método |
|---|---|
| PostgreSQL | `inspect(engine).get_columns()` + `get_table_names()` |
| MySQL | Idem (SQLAlchemy inspect funciona) |
| Oracle | Idem (SQLAlchemy inspect funciona) |
| MongoDB | `db.list_collection_names()` + sample document fields |

### Queries

- Relacionais: `text(sql)` via SQLAlchemy (pass-through, sem ORM)
- MongoDB: traduzir query simples ou aceitar JSON filter

## Backward Compatibility

- Config sem `type` → assume `postgresql` (zero breaking change)
- asyncpg continua como driver PG (via SQLAlchemy, não direto)
- SSH tunnels funcionam igual para todos

## Fora do Escopo

- ORM / models
- Migrations
- Write operations (continua read-only)
- Connection pooling avançado (usa defaults do SQLAlchemy)

## CI/CD

```yaml
services:
  postgres:
    image: postgres:16
    env: { POSTGRES_PASSWORD: test, POSTGRES_DB: testdb }
    ports: ["5432:5432"]
  mysql:
    image: mysql:8.0
    env: { MYSQL_ROOT_PASSWORD: test, MYSQL_DATABASE: testdb }
    ports: ["3306:3306"]
  oracle:
    image: gvenzl/oracle-free:slim
    env: { ORACLE_PASSWORD: test }
    ports: ["1521:1521"]
  mongo:
    image: mongo:7
    ports: ["27017:27017"]
```

## Testes

- Unit: mocks de engine/connection por driver
- Integração: queries reais contra services do CI
- Fixtures: seed data por banco (CREATE TABLE + INSERT)

## Critérios de Aceite

- [ ] `pip install db-mcp-py` → funciona com PG (como antes)
- [ ] `pip install db-mcp-py[mysql]` → funciona com MySQL
- [ ] `pip install db-mcp-py[oracle]` → funciona com Oracle
- [ ] `pip install db-mcp-py[mongo]` → funciona com MongoDB
- [ ] CI verde com 4 bancos
- [ ] Schema discovery funciona em todos
- [ ] Query read-only funciona em todos
- [ ] SSH tunnel funciona com todos os relacionais
- [ ] Zero breaking change para configs existentes
