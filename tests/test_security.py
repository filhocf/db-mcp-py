"""Tests for SQL validation security."""

from db_mcp_py.server import validate_sql


# --- Blocked operations ---


def test_blocks_insert():
    assert validate_sql("INSERT INTO users VALUES (1)") is not None


def test_blocks_update():
    assert validate_sql("UPDATE users SET name='x'") is not None


def test_blocks_delete():
    assert validate_sql("DELETE FROM users") is not None


def test_blocks_drop():
    assert validate_sql("DROP TABLE users") is not None


def test_blocks_truncate():
    assert validate_sql("TRUNCATE users") is not None


def test_blocks_alter():
    assert validate_sql("ALTER TABLE users ADD col int") is not None


# --- Allowed operations ---


def test_allows_select():
    assert validate_sql("SELECT * FROM users") is None


def test_allows_with():
    assert validate_sql("WITH cte AS (SELECT 1) SELECT * FROM cte") is None


def test_allows_explain():
    assert validate_sql("EXPLAIN SELECT * FROM users") is None


def test_allows_explain_analyze():
    assert validate_sql("EXPLAIN ANALYZE SELECT * FROM users") is None


def test_allows_show():
    assert validate_sql("SHOW search_path") is None


# --- Case insensitive blocking ---


def test_blocks_case_insensitive():
    assert validate_sql("insert INTO users VALUES (1)") is not None
    assert validate_sql("Insert Into users VALUES (1)") is not None
    assert validate_sql("DELETE from users") is not None


# --- Advanced bypass attempts ---


def test_blocks_cte_with_delete():
    """CTE containing DELETE should be blocked."""
    sql = "WITH deleted AS (DELETE FROM users RETURNING *) SELECT * FROM deleted"
    assert validate_sql(sql) is not None


def test_blocks_comment_before_delete():
    """Block comments before write commands."""
    assert validate_sql("/* comment */ DELETE FROM users") is not None


def test_blocks_line_comment_before_drop():
    """Line comments before write commands."""
    assert validate_sql("-- comment\nDROP TABLE users") is not None


def test_blocks_create():
    assert validate_sql("CREATE TABLE evil (id int)") is not None


def test_blocks_grant():
    assert validate_sql("GRANT ALL ON users TO evil") is not None


def test_blocks_copy():
    assert validate_sql("COPY users TO '/tmp/dump'") is not None


def test_blocks_do_block():
    assert validate_sql("DO $$ BEGIN DELETE FROM users; END $$") is not None


def test_blocks_call():
    assert validate_sql("CALL dangerous_proc()") is not None


def test_blocks_empty():
    assert validate_sql("") is not None
    assert validate_sql("   ") is not None


def test_blocks_only_comments():
    assert validate_sql("/* nothing */ -- also nothing") is not None
