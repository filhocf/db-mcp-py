# MEMORY.md — db-mcp-py

## Estado Atual (19/mai/2026)

- **Versão**: 0.2.0
- **Repo**: https://github.com/filhocf/db-mcp-py
- **PyPI**: https://pypi.org/project/db-mcp-py/
- **Status**: ✅ Estável, em uso diário (3 bancos configurados: mir_dev, sicar_dev, coreapi_dev)

## Último trabalho

- PR #7 mergeado (18/mai): multi-database support (SQLAlchemy async + MongoDB)
- Fixes: unused import, ruff format, motor bool

## Pendente

- [ ] Mover CHANGELOG.md de docs/ para raiz
- [ ] Adicionar pytest-cov ao dev deps
- [ ] CodeQL workflow (P2 — projeto opensource)

## Decisões

- SQLAlchemy async em vez de asyncpg (suporta PG + MySQL + Oracle)
- MongoDB via motor (async driver)
- Read-only enforcement em 3 camadas: SQL whitelist + DB session + Oracle events
- Não precisa ser singleton (PG lida com concorrência multi-sessão)
- Resiliente sem VPN (conexão falha graciosamente, reconecta quando disponível)
