# Code Review: feat/multi-database

**Reviewer:** Code Reviewer Agent  
**Date:** 2025-05-18  
**Branch:** `feat/multi-database`  
**Status:** Changes requested (2 critical, 2 high, 4 medium, 2 low)

---

## Summary

The implementation adds MySQL, Oracle, and MongoDB support via SQLAlchemy async engines and motor. The architecture is sound — PostgreSQL keeps asyncpg as the driver, MongoDB is correctly separated, and backward compatibility is preserved. However, there are security gaps in read-only enforcement for non-PG databases and URL construction, plus a missing Oracle integration test.

---

## Findings

### CRITICAL

#### 1. No read-only enforcement for MySQL/Oracle

**File:** `src/db_mcp_py/database.py` lines 124-128  
**Issue:** `default_transaction_read_only` is only set for PostgreSQL. MySQL and Oracle connections have **no server-side read-only enforcement**. The `engine.connect()` context manager does not auto-commit, but this is not equivalent to read-only — a `SET` statement or explicit `COMMIT` in raw SQL could still mutate data.

The SQL validator in `server.py` blocks write keywords, but defense-in-depth requires server-side enforcement too. For MySQL, `SET SESSION TRANSACTION READ ONLY` should be issued. For Oracle, `SET TRANSACTION READ ONLY` at session level.

```python
# Current: only PG gets read-only
if cfg.type == "postgresql":
    connect_args["server_settings"] = {
        "default_transaction_read_only": "on",
        ...
    }
# MySQL and Oracle: nothing
```

**Recommendation:** Add connection-level read-only enforcement per driver:
- MySQL: add `init_command="SET SESSION TRANSACTION READ ONLY"` in connect_args or use an event listener
- Oracle: use `execution_options(isolation_level="AUTOCOMMIT")` is insufficient; issue `ALTER SESSION SET TRANSACTION READ ONLY` via pool event

#### 2. Password not URL-encoded in `_build_url()`

**File:** `src/db_mcp_py/database.py` line 29-30  
**Issue:** Passwords containing `@`, `:`, `/`, `%`, or `#` will break the URL parsing, causing connection failures or — worse — connecting to the wrong host if `@` is in the password.

```python
password = cfg.password or ""
return f"{scheme}://{cfg.user}:{password}@{host}:{port}/{cfg.database}"
```

Verified: `_build_url(cfg_with_password="p@ss:word/test", ...)` produces `postgresql+asyncpg://u:p@ss:word/test@localhost:5432/db` which is unparseable.

**Recommendation:** Use `urllib.parse.quote_plus()` for both user and password:
```python
from urllib.parse import quote_plus
return f"{scheme}://{quote_plus(cfg.user)}:{quote_plus(password)}@{host}:{port}/{cfg.database}"
```

---

### HIGH

#### 3. MongoDB NoSQL injection via `$where` and `$function`

**File:** `src/db_mcp_py/mongo.py` lines 92-101  
**Issue:** The filter dict from user input is passed directly to `collection.find()` without sanitizing MongoDB operators. An attacker can use `{"$where": "sleep(5000)"}` for DoS or `{"$expr": {...}}` for data exfiltration beyond the intended collection scope.

```python
filt = params.get("filter", {})
cursor = mc.db[collection].find(filt, projection).limit(limit)
```

**Recommendation:** Reject filters containing keys starting with `$where`, `$function`, `$accumulator`, or at minimum log a warning. Consider a deny-list of dangerous operators:
```python
_DANGEROUS_OPS = {"$where", "$function", "$accumulator"}
def _check_filter(filt: dict) -> str | None:
    for key in filt:
        if key in _DANGEROUS_OPS:
            return f"Operator {key} is not allowed"
    return None
```

#### 4. Query timeout not enforced for MySQL/Oracle

**File:** `src/db_mcp_py/database.py` lines 124-128  
**Issue:** `statement_timeout` is only configured for PostgreSQL. MySQL and Oracle queries can run indefinitely, potentially causing resource exhaustion.

**Recommendation:**
- MySQL: pass `connect_timeout` and use `init_command="SET SESSION MAX_EXECUTION_TIME={ms}"` 
- Oracle: use `callTimeout` in oracledb connect args
- Alternatively, wrap `conn.execute()` with `asyncio.wait_for(timeout=...)` as a universal fallback

---

### MEDIUM

#### 5. No Oracle integration test

**File:** `tests/test_database.py`  
**Issue:** CI sets `TEST_ORACLE_*` env vars and runs an Oracle service container, but there is no `test_oracle_integration()` function in the test file. The Oracle driver path is untested in CI.

**Recommendation:** Add a test similar to `test_pg_integration` / `test_mysql_integration`:
```python
@pytest.mark.asyncio
async def test_oracle_integration():
    cfg = _oracle_config()
    if not cfg:
        pytest.skip("TEST_ORACLE_HOST not set")
    ...
```

#### 6. MongoDB URL construction doesn't URL-encode credentials

**File:** `src/db_mcp_py/mongo.py` line 65  
**Issue:** Same URL-encoding problem as finding #2:
```python
url = f"mongodb://{cfg.user}:{cfg.password}@{host}:{port}"
```

**Recommendation:** Use `urllib.parse.quote_plus()` for user and password.

#### 7. `validate_sql` can be bypassed with semicolons

**File:** `src/db_mcp_py/server.py` lines 42-63  
**Issue:** The validator checks the first statement but doesn't block multi-statement queries. `SELECT 1; DROP TABLE users` passes because the first word is `SELECT` and the write-pattern check would catch `DROP`, but stacked queries via `;` followed by a write keyword inside a string literal could potentially bypass:
```sql
SELECT ''; -- the regex won't match inside strings reliably
```
This is partially mitigated by asyncpg/aiomysql not supporting multi-statement by default, but it's still a defense-in-depth gap.

**Recommendation:** Strip string literals before regex matching, or reject queries containing `;` outside of string literals.

#### 8. `effective` dict lacks type annotation

**File:** `src/db_mcp_py/database.py` line 37, `src/db_mcp_py/mongo.py` line 22  
**Issue:** `effective: dict` is untyped — should be `dict[str, Any]` or ideally a TypedDict for the known keys (`read_only`, `query_timeout`, `max_connections`, `connect_timeout`, `schema_cache_ttl`).

---

### LOW

#### 9. `_inspect` inner function missing type annotations

**File:** `src/db_mcp_py/database.py` line 200  
**Issue:** `def _inspect(sync_conn):` has no type annotations. While ruff's default rules don't enforce ANN, this is a quality gap in a function that handles schema introspection.

#### 10. Logging SQL in query method

**File:** `src/db_mcp_py/database.py` line 156  
**Issue:** `logger.info("Query [%s]: %s", conn_id, sql[:200])` logs the first 200 chars of every query at INFO level. If queries contain sensitive data in WHERE clauses (e.g., `WHERE ssn = '...'`), this could leak PII to logs. Consider DEBUG level or redacting parameters.

---

## Checklist Results

| Check | Status | Notes |
|-------|--------|-------|
| SQL injection | ⚠️ | SQL validator is solid for SQL DBs; MongoDB has NoSQL injection risk |
| Credentials not logged | ✅ | Password not in log statements; URL not logged |
| Read-only enforced | ❌ | Only PostgreSQL has server-side enforcement |
| Type hints | ⚠️ | Good overall; `effective: dict` and `_inspect` lack specificity |
| Error handling | ✅ | Graceful failures, retry logic, proper exception propagation |
| Dead code | ✅ | None found |
| Ruff passes | ✅ | `ruff check` and `ruff format --check` both pass |
| Tests for each driver | ⚠️ | PG, MySQL, MongoDB have tests; Oracle is missing |
| Backward compat | ✅ | `type` defaults to `"postgresql"`, existing configs work unchanged |

---

## Positive Observations

- Clean separation of MongoDB into its own module
- Schema caching with TTL is well-implemented
- `validate_sql()` is thorough with comment stripping and write-pattern detection
- Integration tests skip gracefully when env vars are absent
- CI matrix covers Python 3.11–3.13 with all four database services
- Backward compatibility is preserved perfectly (default type = postgresql)

---

## Required Actions Before Merge

1. **Fix `_build_url()` to URL-encode user/password** (Critical #2)
2. **Add read-only enforcement for MySQL/Oracle** (Critical #1)
3. **Sanitize MongoDB filter operators** (High #3)
4. **Add Oracle integration test** (Medium #5)
