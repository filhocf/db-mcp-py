# db-mcp-py — Revisão de Design (DBA Sênior)

**Data:** 2026-05-03
**Revisor:** db-analyst (sessão orquestrada)
**Escopo:** config.py, tunnels.py, database.py, server.py, pyproject.toml
**Versão analisada:** v0.1.0

---

## 1. Config Model (Pydantic)

### Veredicto: ✅ Bom, com ajustes necessários

O modelo cobre os 3 bancos reais (mir_dev, sicar_dev, coreapi_dev) e os cenários documentados no README. Pontos de atenção:

### 1.1 Falta `ssl` / `sslmode`

Ambientes corporativos frequentemente exigem `sslmode=require` ou certificados. O asyncpg suporta `ssl` nativo.

```python
class ConnectionConfig(BaseModel):
    # ... campos existentes ...
    ssl: bool | str = False  # True, False, ou path para CA cert
```

**Impacto nos 3 bancos:** mir_dev (VPN direta) provavelmente não usa SSL. sicar_dev e coreapi_dev passam por tunnel SSH, então SSL é redundante mas pode ser exigido por policy.

### 1.2 `remote_port` default deveria ser 5432, não 5433

O PostgreSQL padrão usa porta 5432. O default 5433 no `TunnelConfig` é específico dos bancos Dataprev. Isso vai confundir usuários externos.

```python
class TunnelConfig(BaseModel):
    remote_port: int = 5432  # PostgreSQL padrão
```

**Recomendação:** Mudar default para 5432 e declarar 5433 explicitamente no config.json dos bancos Dataprev.

### 1.3 `password` deveria aceitar `None` explícito

Para conexões via tunnel com `peer` auth ou `.pgpass`, password vazio é válido. O campo aceita `""` mas semanticamente `None` é mais claro.

```python
password: str | None = None
```

### 1.4 Validação de `local_port` — risco de colisão

Não há validação cross-connection para garantir que dois tunnels não usem a mesma `local_port`. Adicionar um `model_validator` no `Config`:

```python
class Config(BaseModel):
    @model_validator(mode="after")
    def check_unique_local_ports(self) -> "Config":
        ports = []
        for conn in self.connections:
            if conn.tunnel:
                if conn.tunnel.local_port in ports:
                    raise ValueError(
                        f"Duplicate local_port {conn.tunnel.local_port} "
                        f"in connection '{conn.id}'"
                    )
                ports.append(conn.tunnel.local_port)
        return self
```

### 1.5 Falta validação de `id` único

Dois connections com mesmo `id` causariam sobrescrita silenciosa no dict `connections`. Adicionar validação:

```python
@model_validator(mode="after")
def check_unique_ids(self) -> "Config":
    ids = [c.id for c in self.connections]
    dupes = [x for x in ids if ids.count(x) > 1]
    if dupes:
        raise ValueError(f"Duplicate connection IDs: {set(dupes)}")
    return self
```

### 1.6 Suporte a YAML/TOML

JSON não suporta comentários. Para config de infra, YAML ou TOML são mais ergonômicos. Considerar para v0.2.

---

## 2. SSH Tunnels

### Veredicto: ⚠️ Funcional, mas com gaps críticos

### 2.1 Tunnel direto — ✅ OK

O fluxo `asyncssh.connect(ssh_host) → forward_local_port()` está correto.

### 2.2 Jump host — ⚠️ Bug potencial

O código usa as **mesmas** `ssh_opts` (incluindo `username`) para jump host e target host. Em cenários reais, o jump host e o target frequentemente têm users diferentes.

```python
# PROBLEMA: mesmo ssh_user para jump e target
if tunnel.jump_host:
    jump_conn = await asyncssh.connect(tunnel.jump_host, **ssh_opts)  # user X
    ssh_conn = await asyncssh.connect(tunnel.ssh_host, tunnel=jump_conn, **ssh_opts)  # user X (deveria ser Y?)
```

**Correção:** Adicionar `jump_user` ao `TunnelConfig`, ou resolver via `ssh -G` para cada host separadamente:

```python
class TunnelConfig(BaseModel):
    jump_host: str | None = None
    jump_user: str | None = None  # NOVO
    jump_key: str | None = None   # NOVO
```

### 2.3 Leitura de `~/.ssh/config` — ⚠️ Parcial

A função `_get_ssh_user()` usa `ssh -G` para resolver o user, mas:

1. **Não resolve `HostName`** — se `ssh_host` é um alias (ex: `coreapi-db-dev`), o asyncssh precisa do hostname real. O `ssh -G` retorna o `hostname` resolvido, mas o código não o usa.
2. **Não resolve `IdentityFile`** — a key do ssh config é ignorada.
3. **Não resolve `ProxyJump`** — se o jump host está definido no ssh config, o código não o detecta.

**Correção recomendada:** Criar um resolver completo:

```python
def _resolve_ssh_config(host_alias: str) -> dict:
    """Resolve ssh config for a host alias via ssh -G."""
    try:
        result = subprocess.run(
            ["ssh", "-G", host_alias],
            capture_output=True, text=True, timeout=5,
        )
        config = {}
        for line in result.stdout.splitlines():
            key, _, value = line.partition(" ")
            config[key] = value
        return {
            "hostname": config.get("hostname", host_alias),
            "user": config.get("user"),
            "port": int(config.get("port", 22)),
            "identity_file": config.get("identityfile"),
            "proxy_jump": config.get("proxyjump"),
        }
    except Exception:
        return {"hostname": host_alias}
```

Depois usar `hostname` resolvido no `asyncssh.connect()`.

### 2.4 Reconnect — ❌ Não implementado

O README menciona "Automatic retry on connection loss with exponential backoff" mas **não há implementação**. Se o tunnel SSH cair (timeout de rede, restart do bastion), o pool asyncpg vai falhar silenciosamente.

**Implementação mínima para v0.1:**

```python
async def _monitor_tunnel(self, conn_id: str, tunnel: TunnelConfig) -> None:
    """Monitor and reconnect tunnel if it drops."""
    backoff = 1
    while conn_id in self._tunnels:
        await asyncio.sleep(5)
        conn = self._tunnels.get(conn_id)
        if conn is None:
            break
        # asyncssh connection has a _transport attribute
        if conn._transport is None or conn._transport.is_closing():
            logger.warning("Tunnel %s: connection lost, reconnecting...", conn_id)
            await self.close(conn_id)
            for attempt in range(5):
                try:
                    await self.open(conn_id, tunnel)
                    logger.info("Tunnel %s: reconnected (attempt %d)", conn_id, attempt + 1)
                    backoff = 1
                    break
                except Exception:
                    wait = min(backoff * 2, 60)
                    logger.warning("Tunnel %s: reconnect failed, retry in %ds", conn_id, wait)
                    await asyncio.sleep(wait)
                    backoff = wait
```

### 2.5 `known_hosts=None` — ⚠️ Risco de segurança

Aceitar qualquer host key é aceitável em dev, mas deveria ser configurável:

```python
class TunnelConfig(BaseModel):
    strict_host_key: bool = False  # True = usar known_hosts do sistema
```

---

## 3. Segurança SQL

### Veredicto: ❌ Insuficiente — bypass trivial

### 3.1 Bloqueio atual é facilmente contornável

O filtro atual verifica apenas a **primeira palavra** do SQL:

```python
first_word = sql.split()[0].upper() if sql else ""
if first_word not in ("SELECT", "WITH", "EXPLAIN", "SHOW"):
```

**Bypasses triviais:**

```sql
-- Bypass 1: CTE com write
WITH deleted AS (DELETE FROM users RETURNING *) SELECT * FROM deleted;

-- Bypass 2: SELECT com side-effect
SELECT dblink_exec('host=localhost', 'DROP TABLE users');

-- Bypass 3: EXPLAIN com execução (não aplica ao PostgreSQL, mas mostra a fragilidade)

-- Bypass 4: Comentário antes do comando
/* comment */ DELETE FROM users;

-- Bypass 5: Subquery com função que faz write
SELECT lo_import('/etc/passwd');
```

### 3.2 Lista completa de comandos a bloquear

Além de INSERT/UPDATE/DELETE, bloquear:

| Comando | Risco |
|---------|-------|
| `TRUNCATE` | Apaga todos os dados da tabela |
| `DROP` | Remove objetos (tabelas, schemas, databases) |
| `ALTER` | Modifica estrutura |
| `CREATE` | Cria objetos (pode criar triggers maliciosos) |
| `GRANT` / `REVOKE` | Altera permissões |
| `COPY` | Lê/escreve arquivos do servidor |
| `DO` | Executa bloco PL/pgSQL arbitrário |
| `CALL` | Executa procedures (podem ter side-effects) |
| `SET` | Pode alterar configurações de sessão |
| `RESET` | Reseta configurações |
| `LOCK` | Pode causar deadlocks |
| `VACUUM` | Operação de manutenção |
| `ANALYZE` (standalone) | Atualiza estatísticas |
| `REINDEX` | Operação de manutenção |
| `CLUSTER` | Reorganiza tabela |
| `REFRESH MATERIALIZED VIEW` | Pode ser pesado |
| `NOTIFY` / `LISTEN` | Canais de mensagem |
| `PREPARE` / `EXECUTE` / `DEALLOCATE` | Statements preparados podem esconder writes |
| `DISCARD` | Reseta estado da sessão |
| `COMMENT` | Modifica metadados |
| `SECURITY LABEL` | Modifica labels de segurança |
| `IMPORT FOREIGN SCHEMA` | Importa schemas externos |

### 3.3 Defesa em profundidade recomendada

A abordagem correta é **allowlist + transaction readonly + role readonly**:

```python
# Camada 1: Allowlist de prefixos (defesa superficial)
_ALLOWED_PREFIXES = ("SELECT", "WITH", "EXPLAIN", "SHOW")

# Camada 2: Regex para detectar writes dentro de CTEs
_WRITE_PATTERN = re.compile(
    r'\b(INSERT|UPDATE|DELETE|TRUNCATE|DROP|ALTER|CREATE|GRANT|REVOKE|'
    r'COPY|DO|CALL|LOCK|VACUUM|ANALYZE|REINDEX|CLUSTER|REFRESH|'
    r'PREPARE|EXECUTE|DEALLOCATE|DISCARD|COMMENT|NOTIFY|LISTEN)\b',
    re.IGNORECASE,
)

def validate_sql(sql: str) -> str | None:
    """Return error message if SQL is not safe, None if OK."""
    cleaned = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)  # strip block comments
    cleaned = re.sub(r'--.*$', '', cleaned, flags=re.MULTILINE)  # strip line comments
    cleaned = cleaned.strip()

    if not cleaned:
        return "Empty query"

    first_word = cleaned.split()[0].upper()
    if first_word not in _ALLOWED_PREFIXES:
        return f"Only SELECT/WITH/EXPLAIN/SHOW allowed (got {first_word})"

    # Check for write keywords anywhere (catches CTE abuse)
    # Exclude EXPLAIN ANALYZE which is safe
    check_text = cleaned
    if first_word == "EXPLAIN":
        check_text = re.sub(r'^EXPLAIN\s+(ANALYZE\s+)?', '', check_text, flags=re.IGNORECASE)

    if _WRITE_PATTERN.search(check_text):
        match = _WRITE_PATTERN.search(check_text)
        return f"Write operation detected: {match.group(0)}"

    return None
```

```python
# Camada 3: Transaction readonly (já implementado — BOM!)
async with conn.transaction(readonly=True):
    rows = await conn.fetch(sql)
```

```python
# Camada 4 (recomendada): Usar role read-only no PostgreSQL
# No config dos bancos, criar um role dedicado:
# CREATE ROLE mcp_readonly LOGIN;
# GRANT CONNECT ON DATABASE mydb TO mcp_readonly;
# GRANT USAGE ON SCHEMA public TO mcp_readonly;
# GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_readonly;
# ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO mcp_readonly;
```

A **camada 3** (transaction readonly) é a defesa mais forte — o PostgreSQL rejeita writes dentro de transações readonly. Mas a camada 1+2 evita mensagens de erro confusas e dá feedback claro ao LLM.

---

## 4. Schema Filtering

### Veredicto: ✅ Correto, com otimizações necessárias

### 4.1 Query atual — funcional

A query com `information_schema.tables JOIN information_schema.columns` está correta e usa parametrização (`$1`, `$2`...) contra SQL injection. ✅

### 4.2 Performance em schemas grandes

Para bancos com centenas de tabelas (coreapi_dev pode ter muitas), a query pode ser lenta porque `information_schema` é uma view sobre `pg_catalog`.

**Otimização: usar `pg_catalog` diretamente:**

```sql
SELECT
    n.nspname AS table_schema,
    c.relname AS table_name,
    a.attname AS column_name,
    pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
    CASE WHEN a.attnotnull THEN 'NO' ELSE 'YES' END AS is_nullable,
    pg_get_expr(d.adbin, d.adrelid) AS column_default
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid
LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
WHERE c.relkind = 'r'  -- regular tables
    AND a.attnum > 0
    AND NOT a.attisdropped
    AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
    AND n.nspname = ANY($1::text[])
ORDER BY n.nspname, c.relname, a.attnum
```

**Benchmark típico:** `pg_catalog` direto é 3-10x mais rápido que `information_schema` em bancos com 500+ tabelas.

### 4.3 Falta informação de PKs e FKs

Para um LLM construir queries úteis, saber as primary keys e foreign keys é essencial. Adicionar:

```sql
-- PKs: adicionar ao schema query
SELECT
    tc.table_schema, tc.table_name, kcu.column_name
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
    ON tc.constraint_name = kcu.constraint_name
WHERE tc.constraint_type = 'PRIMARY KEY'
```

### 4.4 Falta `LIMIT` no schema query

Se um schema tem 1000 tabelas × 50 colunas = 50.000 rows. Considerar paginação ou limite configurável.

---

## 5. Connection Pooling

### Veredicto: ✅ Adequado para v0.1

### 5.1 Defaults atuais

| Parâmetro | Valor | Avaliação |
|-----------|-------|-----------|
| `min_size` | 1 | ✅ Bom — MCP é single-user, não precisa de pool grande |
| `max_size` | 5 | ✅ Adequado — permite queries paralelas do LLM |
| `command_timeout` | 30s | ⚠️ Pode ser curto para queries analíticas em bancos grandes |

### 5.2 Falta `statement_cache_size`

O asyncpg faz cache de prepared statements por padrão (1024). Para um MCP onde cada query é diferente, isso desperdiça memória:

```python
db.pool = await asyncpg.create_pool(
    # ... existente ...
    statement_cache_size=0,  # Queries do LLM raramente se repetem
)
```

### 5.3 Falta `server_settings` para read-only

Além da transaction readonly, forçar no nível da sessão:

```python
db.pool = await asyncpg.create_pool(
    # ... existente ...
    server_settings={
        "default_transaction_read_only": "on",
        "statement_timeout": f"{db.effective['query_timeout'] * 1000}",  # ms
    },
)
```

Isso dá **dupla proteção**: mesmo que alguém esqueça o `transaction(readonly=True)`, a sessão inteira é read-only.

### 5.4 Timeout de conexão

O `asyncio.wait_for(..., timeout=15)` no `connect()` é bom, mas o timeout deveria ser configurável:

```python
class DefaultsConfig(BaseModel):
    connect_timeout: int = 15  # NOVO
```

---

## 6. Graceful Degradation

### Veredicto: ✅ Bem implementado

### 6.1 Pontos positivos

1. **VPN check antes de conectar** — `require_vpn` + `check_vpn()` evita timeouts longos. ✅
2. **Tunnel failure não crasha** — `try/except` no `_startup()` registra erro e continua. ✅
3. **Connection failure não crasha** — `connect()` retorna `False` e registra `db.error`. ✅
4. **`list_databases` mostra status** — LLM pode ver quais bancos estão disponíveis. ✅

### 6.2 Gaps

**6.2.1 — Sem health check periódico**

Se um banco cai depois do startup, o pool asyncpg vai retornar erros até o servidor ser reiniciado. Adicionar health check:

```python
async def _health_check_loop(interval: int = 60) -> None:
    """Periodically check connection health."""
    while True:
        await asyncio.sleep(interval)
        for conn_id, db in _conn_mgr.connections.items():
            if db.pool:
                try:
                    async with db.pool.acquire() as conn:
                        await conn.fetchval("SELECT 1")
                except Exception as e:
                    logger.warning("Health check failed for %s: %s", conn_id, e)
                    db.error = f"Health check failed: {e}"
```

**6.2.2 — Sem retry na query**

Se uma query falha por connection reset, o erro é retornado direto ao LLM. Um retry simples ajudaria:

```python
async def query(self, conn_id: str, sql: str, *, _retry: bool = True) -> list[dict]:
    try:
        # ... query existente ...
    except (asyncpg.ConnectionDoesNotExistError, asyncpg.InterfaceError):
        if _retry:
            logger.warning("Connection lost for %s, retrying...", conn_id)
            return await self.query(conn_id, sql, _retry=False)
        raise
```

**6.2.3 — `check_vpn()` é específico demais**

Os prefixos `10.195.`, `10.202.`, `10.188.` são hardcoded para Dataprev. Tornar configurável:

```python
class DefaultsConfig(BaseModel):
    vpn_route_prefixes: list[str] = Field(default_factory=list)
```

---

## 7. Sugestões para v0.1

### 7.1 Schema Cache TTL — ✅ Implementar

Schema metadata raramente muda. Cachear evita queries repetidas ao `information_schema` a cada chamada do tool `schema`.

```python
from dataclasses import dataclass, field
from time import monotonic

@dataclass
class DatabaseConnection:
    # ... existente ...
    _schema_cache: list[dict] | None = field(default=None, repr=False)
    _schema_cached_at: float = 0

    def get_cached_schema(self, ttl: int = 300) -> list[dict] | None:
        if self._schema_cache and (monotonic() - self._schema_cached_at) < ttl:
            return self._schema_cache
        return None

    def set_schema_cache(self, data: list[dict]) -> None:
        self._schema_cache = data
        self._schema_cached_at = monotonic()
```

**Custo:** ~15 linhas. **Benefício:** Elimina query repetida em cada interação do LLM. **Veredicto: sim, v0.1.**

### 7.2 Audit Log — ⚠️ Parcial na v0.1

Log básico de queries já existe via `logger`. Para v0.1, adicionar apenas o SQL executado ao log:

```python
logger.info("Query [%s]: %s", db_id, sql[:200])
```

Audit log completo (com user, timestamp, resultado) é v0.2.

**Veredicto: log básico sim, audit completo não.**

### 7.3 EXPLAIN Automático — ❌ Não na v0.1

Rodar `EXPLAIN` antes de cada query adiciona latência e complexidade. O LLM pode pedir `EXPLAIN` explicitamente via o tool `query`. Não automatizar.

**Veredicto: não.**

### 7.4 Outras sugestões para v0.1

| Sugestão | Prioridade | Justificativa |
|----------|-----------|---------------|
| Validação de `id` e `local_port` únicos | 🔴 Alta | Previne bugs silenciosos |
| SQL validation com regex (seção 3.3) | 🔴 Alta | Segurança |
| `server_settings` read-only no pool | 🔴 Alta | Defesa em profundidade |
| `statement_cache_size=0` | 🟡 Média | Performance |
| Schema cache TTL | 🟡 Média | Performance |
| Resolver `ssh -G` completo | 🟡 Média | Compatibilidade |
| Health check periódico | 🟢 Baixa | Resiliência (v0.2) |
| Tunnel reconnect | 🟢 Baixa | Resiliência (v0.2) |

---

## Resumo Executivo

| Área | Status | Ação Necessária |
|------|--------|-----------------|
| Config model | ✅ Bom | Validar IDs/ports únicos, default port 5432 |
| SSH tunnels | ⚠️ Parcial | Resolver ssh config completo, separar jump_user |
| Segurança SQL | ❌ Insuficiente | Implementar regex + server_settings read-only |
| Schema filtering | ✅ Correto | Otimizar com pg_catalog, adicionar PKs |
| Connection pooling | ✅ Adequado | Adicionar statement_cache_size=0, server_settings |
| Graceful degradation | ✅ Bom | VPN prefixes configuráveis |
| Reconnect | ❌ Não implementado | Mínimo: retry na query. Tunnel monitor v0.2 |

**Risco principal:** A validação SQL é contornável. Priorizar a implementação do regex validator e `server_settings` read-only antes de expor o servidor a LLMs em produção.
