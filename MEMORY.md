# MEMORY.md — db-mcp-py

## Estado Atual (19/mai/2026)

- **Versão**: 0.2.0
- **Repo**: https://github.com/filhocf/db-mcp-py
- **PyPI**: https://pypi.org/project/db-mcp-py/
- **Status**: ✅ Estável, em uso diário (mir_dev, sicar_dev, coreapi_dev)

## Decisões

- SQLAlchemy async (PG + MySQL + Oracle) + Motor (MongoDB)
- Read-only 3 camadas: SQL whitelist + DB session + Oracle events
- Resiliente sem VPN (reconecta quando disponível)
