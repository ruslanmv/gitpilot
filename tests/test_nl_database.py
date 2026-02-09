"""Tests for gitpilot.nl_database module."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from gitpilot.nl_database import (
    NLQueryEngine,
    QueryDialect,
    QueryResult,
    SafetyLevel,
    TableSchema,
)


# ---------------------------------------------------------------------------
# QueryDialect / SafetyLevel enums
# ---------------------------------------------------------------------------

class TestEnums:
    def test_query_dialect_values(self):
        assert QueryDialect.POSTGRESQL.value == "postgresql"
        assert QueryDialect.MYSQL.value == "mysql"
        assert QueryDialect.SQLITE.value == "sqlite"
        assert QueryDialect.GENERIC_SQL.value == "sql"

    def test_safety_level_values(self):
        assert SafetyLevel.READ_ONLY.value == "read_only"
        assert SafetyLevel.READ_WRITE.value == "read_write"
        assert SafetyLevel.UNRESTRICTED.value == "unrestricted"


# ---------------------------------------------------------------------------
# TableSchema
# ---------------------------------------------------------------------------

class TestTableSchema:
    def test_default_fields(self):
        ts = TableSchema(name="users")
        assert ts.name == "users"
        assert ts.columns == []
        assert ts.primary_key is None
        assert ts.description == ""

    def test_to_prompt_text(self):
        ts = TableSchema(
            name="orders",
            columns=[
                {"name": "id", "type": "INTEGER"},
                {"name": "total", "type": "DECIMAL"},
            ],
            primary_key="id",
        )
        text = ts.to_prompt_text()
        assert "TABLE orders" in text
        assert "id INTEGER" in text
        assert "total DECIMAL" in text
        assert "PK=id" in text

    def test_to_prompt_text_no_pk(self):
        ts = TableSchema(name="logs", columns=[{"name": "msg", "type": "TEXT"}])
        text = ts.to_prompt_text()
        assert "PK=" not in text

    def test_to_prompt_text_no_type(self):
        ts = TableSchema(name="t", columns=[{"name": "col"}])
        text = ts.to_prompt_text()
        assert "col TEXT" in text


# ---------------------------------------------------------------------------
# QueryResult
# ---------------------------------------------------------------------------

class TestQueryResult:
    def test_to_dict(self):
        qr = QueryResult(
            original_question="How many users?",
            generated_sql="SELECT COUNT(*) FROM users",
            dialect=QueryDialect.POSTGRESQL,
            rows=[{"count": 42}],
            columns=["count"],
            row_count=1,
            explanation="Counts users",
        )
        d = qr.to_dict()
        assert d["original_question"] == "How many users?"
        assert d["generated_sql"] == "SELECT COUNT(*) FROM users"
        assert d["dialect"] == "postgresql"
        assert d["row_count"] == 1
        assert d["error"] is None

    def test_to_dict_limits_rows(self):
        rows = [{"id": i} for i in range(150)]
        qr = QueryResult(
            original_question="", generated_sql="",
            dialect=QueryDialect.SQLITE, rows=rows,
        )
        d = qr.to_dict()
        assert len(d["rows"]) == 100

    def test_to_table_string_with_data(self):
        qr = QueryResult(
            original_question="", generated_sql="",
            dialect=QueryDialect.SQLITE,
            rows=[{"name": "Alice", "age": "30"}, {"name": "Bob", "age": "25"}],
            columns=["name", "age"],
            row_count=2,
        )
        table = qr.to_table_string()
        assert "Alice" in table
        assert "Bob" in table
        assert "name" in table
        assert "age" in table

    def test_to_table_string_no_results(self):
        qr = QueryResult(
            original_question="", generated_sql="",
            dialect=QueryDialect.SQLITE, rows=[], row_count=0,
        )
        assert qr.to_table_string() == "No results."

    def test_to_table_string_with_error(self):
        qr = QueryResult(
            original_question="", generated_sql="",
            dialect=QueryDialect.SQLITE, error="Connection refused",
        )
        assert "Error: Connection refused" in qr.to_table_string()

    def test_to_table_string_truncated_rows(self):
        rows = [{"val": str(i)} for i in range(50)]
        qr = QueryResult(
            original_question="", generated_sql="",
            dialect=QueryDialect.SQLITE,
            rows=rows, columns=["val"], row_count=50,
        )
        table = qr.to_table_string(max_rows=5)
        assert "45 more rows" in table

    def test_to_table_string_infers_columns(self):
        qr = QueryResult(
            original_question="", generated_sql="",
            dialect=QueryDialect.SQLITE,
            rows=[{"x": 1, "y": 2}],
            columns=[],
            row_count=1,
        )
        table = qr.to_table_string()
        assert "x" in table
        assert "y" in table


# ---------------------------------------------------------------------------
# NLQueryEngine — schema management
# ---------------------------------------------------------------------------

class TestSchemaManagement:
    def test_set_schema(self):
        engine = NLQueryEngine()
        tables = [TableSchema(name="users"), TableSchema(name="orders")]
        engine.set_schema(tables)
        assert engine.get_table_names() == ["users", "orders"]

    def test_add_table(self):
        engine = NLQueryEngine()
        engine.add_table(TableSchema(name="products"))
        assert engine.get_table_names() == ["products"]

    def test_get_schema_context_empty(self):
        engine = NLQueryEngine()
        ctx = engine.get_schema_context()
        assert ctx == "No schema information available."

    def test_get_schema_context_populated(self):
        engine = NLQueryEngine()
        engine.set_schema([
            TableSchema(name="users", columns=[{"name": "id", "type": "INT"}]),
        ])
        ctx = engine.get_schema_context()
        assert "TABLE users" in ctx


# ---------------------------------------------------------------------------
# NLQueryEngine.translate
# ---------------------------------------------------------------------------

class TestTranslate:
    def _make_engine(self):
        engine = NLQueryEngine()
        engine.set_schema([
            TableSchema(name="users"),
            TableSchema(name="orders"),
            TableSchema(name="products"),
        ])
        return engine

    def test_how_many(self):
        engine = self._make_engine()
        sql = engine.translate("How many users are there?")
        assert "COUNT(*)" in sql.upper()
        assert "users" in sql.lower()

    def test_list_all(self):
        engine = self._make_engine()
        sql = engine.translate("List all orders")
        assert "SELECT *" in sql.upper()
        assert "orders" in sql.lower()

    def test_show_all(self):
        engine = self._make_engine()
        sql = engine.translate("Show all products")
        assert "products" in sql.lower()

    def test_top_n(self):
        engine = self._make_engine()
        sql = engine.translate("Top 5 users by score")
        assert "LIMIT" in sql.upper()

    def test_fallback_default(self):
        engine = self._make_engine()
        sql = engine.translate("Something random about users")
        assert "SELECT" in sql.upper()
        assert "LIMIT 100" in sql.upper()

    def test_singular_table_match(self):
        engine = self._make_engine()
        # "order" should match "orders" via singular heuristic
        sql = engine.translate("How many order placed today?")
        assert "orders" in sql.lower()

    def test_no_schema_fallback(self):
        engine = NLQueryEngine()
        sql = engine.translate("How many things")
        # With no tables, uses empty string
        assert "SELECT" in sql.upper()


# ---------------------------------------------------------------------------
# NLQueryEngine.validate_query — READ_ONLY
# ---------------------------------------------------------------------------

class TestValidateReadOnly:
    def test_select_allowed(self):
        engine = NLQueryEngine(safety_level=SafetyLevel.READ_ONLY)
        assert engine.validate_query("SELECT * FROM users") is None

    def test_insert_blocked(self):
        engine = NLQueryEngine(safety_level=SafetyLevel.READ_ONLY)
        result = engine.validate_query("INSERT INTO users (name) VALUES ('a')")
        assert result is not None
        assert "blocked" in result.lower()

    def test_update_blocked(self):
        engine = NLQueryEngine(safety_level=SafetyLevel.READ_ONLY)
        result = engine.validate_query("UPDATE users SET name='a' WHERE id=1")
        assert result is not None

    def test_delete_blocked(self):
        engine = NLQueryEngine(safety_level=SafetyLevel.READ_ONLY)
        result = engine.validate_query("DELETE FROM users WHERE id=1")
        assert result is not None

    def test_drop_blocked(self):
        engine = NLQueryEngine(safety_level=SafetyLevel.READ_ONLY)
        result = engine.validate_query("DROP TABLE users")
        assert result is not None

    def test_multiple_statements_blocked(self):
        engine = NLQueryEngine(safety_level=SafetyLevel.READ_ONLY)
        result = engine.validate_query("SELECT 1; SELECT 2")
        assert result is not None
        assert "Multiple" in result


# ---------------------------------------------------------------------------
# NLQueryEngine.validate_query — READ_WRITE
# ---------------------------------------------------------------------------

class TestValidateReadWrite:
    def test_select_allowed(self):
        engine = NLQueryEngine(safety_level=SafetyLevel.READ_WRITE)
        assert engine.validate_query("SELECT * FROM users") is None

    def test_insert_allowed(self):
        engine = NLQueryEngine(safety_level=SafetyLevel.READ_WRITE)
        assert engine.validate_query("INSERT INTO users (name) VALUES ('a')") is None

    def test_update_with_where_allowed(self):
        engine = NLQueryEngine(safety_level=SafetyLevel.READ_WRITE)
        assert engine.validate_query("UPDATE users SET name='b' WHERE id=1") is None

    def test_drop_blocked(self):
        engine = NLQueryEngine(safety_level=SafetyLevel.READ_WRITE)
        result = engine.validate_query("DROP TABLE users")
        assert result is not None

    def test_truncate_blocked(self):
        engine = NLQueryEngine(safety_level=SafetyLevel.READ_WRITE)
        result = engine.validate_query("TRUNCATE TABLE users")
        assert result is not None

    def test_delete_without_where_blocked(self):
        engine = NLQueryEngine(safety_level=SafetyLevel.READ_WRITE)
        result = engine.validate_query("DELETE FROM users")
        assert result is not None

    def test_delete_with_where_allowed(self):
        engine = NLQueryEngine(safety_level=SafetyLevel.READ_WRITE)
        assert engine.validate_query("DELETE FROM users WHERE id=1") is None


# ---------------------------------------------------------------------------
# NLQueryEngine.validate_query — UNRESTRICTED
# ---------------------------------------------------------------------------

class TestValidateUnrestricted:
    def test_drop_allowed(self):
        engine = NLQueryEngine(safety_level=SafetyLevel.UNRESTRICTED)
        assert engine.validate_query("DROP TABLE users") is None

    def test_truncate_allowed(self):
        engine = NLQueryEngine(safety_level=SafetyLevel.UNRESTRICTED)
        assert engine.validate_query("TRUNCATE TABLE users") is None

    def test_delete_without_where_allowed(self):
        engine = NLQueryEngine(safety_level=SafetyLevel.UNRESTRICTED)
        assert engine.validate_query("DELETE FROM users") is None


# ---------------------------------------------------------------------------
# NLQueryEngine.explain
# ---------------------------------------------------------------------------

class TestExplain:
    def test_explain_select(self):
        engine = NLQueryEngine()
        result = engine.explain("SELECT * FROM users LIMIT 10")
        assert "users" in result
        assert "10" in result

    def test_explain_count(self):
        engine = NLQueryEngine()
        result = engine.explain("SELECT COUNT(*) FROM orders")
        assert "Count" in result
        assert "orders" in result

    def test_explain_avg(self):
        engine = NLQueryEngine()
        result = engine.explain("SELECT AVG(price) FROM products")
        assert "average" in result.lower()

    def test_explain_insert(self):
        engine = NLQueryEngine()
        result = engine.explain("INSERT INTO users (name) VALUES ('a')")
        assert "Insert" in result

    def test_explain_update(self):
        engine = NLQueryEngine()
        result = engine.explain("UPDATE users SET name='b' WHERE id=1")
        assert "Update" in result

    def test_explain_delete(self):
        engine = NLQueryEngine()
        result = engine.explain("DELETE FROM users WHERE id=1")
        assert "Delete" in result

    def test_explain_with_where(self):
        engine = NLQueryEngine()
        result = engine.explain("SELECT * FROM users WHERE age > 18")
        assert "filtered" in result.lower()

    def test_explain_unknown(self):
        engine = NLQueryEngine()
        result = engine.explain("GRANT ALL ON schema TO admin")
        assert "Unknown" in result


# ---------------------------------------------------------------------------
# NLQueryEngine.execute — async
# ---------------------------------------------------------------------------

class TestExecute:
    @pytest.mark.asyncio
    async def test_no_mcp_client(self):
        engine = NLQueryEngine()
        result = await engine.execute("SELECT 1")
        assert result.error is not None
        assert "No MCP client" in result.error

    @pytest.mark.asyncio
    async def test_successful_execution(self):
        mock_client = AsyncMock()
        mock_client.call_tool.return_value = [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
        ]
        engine = NLQueryEngine(mcp_client=mock_client, mcp_tool_name="query")
        result = await engine.execute("SELECT * FROM users")
        assert result.error is None
        assert result.row_count == 2
        assert result.columns == ["id", "name"]
        mock_client.call_tool.assert_called_once_with("query", {"query": "SELECT * FROM users"})

    @pytest.mark.asyncio
    async def test_execution_with_dict_response(self):
        mock_client = AsyncMock()
        mock_client.call_tool.return_value = {
            "rows": [{"count": 42}],
        }
        engine = NLQueryEngine(mcp_client=mock_client)
        result = await engine.execute("SELECT COUNT(*) FROM users")
        assert result.row_count == 1
        assert result.rows[0]["count"] == 42

    @pytest.mark.asyncio
    async def test_execution_error(self):
        mock_client = AsyncMock()
        mock_client.call_tool.side_effect = ConnectionError("DB down")
        engine = NLQueryEngine(mcp_client=mock_client)
        result = await engine.execute("SELECT 1")
        assert result.error is not None
        assert "DB down" in result.error

    @pytest.mark.asyncio
    async def test_empty_result(self):
        mock_client = AsyncMock()
        mock_client.call_tool.return_value = []
        engine = NLQueryEngine(mcp_client=mock_client)
        result = await engine.execute("SELECT * FROM empty_table")
        assert result.row_count == 0
        assert result.columns == []


# ---------------------------------------------------------------------------
# NLQueryEngine.ask — full pipeline
# ---------------------------------------------------------------------------

class TestAsk:
    @pytest.mark.asyncio
    async def test_ask_full_pipeline(self):
        mock_client = AsyncMock()
        mock_client.call_tool.return_value = [{"count": 10}]

        engine = NLQueryEngine(mcp_client=mock_client, safety_level=SafetyLevel.READ_ONLY)
        engine.set_schema([TableSchema(name="users")])

        result = await engine.ask("How many users are there?")
        assert result.original_question == "How many users are there?"
        assert "COUNT" in result.generated_sql.upper()
        assert result.error is None
        assert result.row_count == 1

    @pytest.mark.asyncio
    async def test_ask_blocked_by_validation(self):
        engine = NLQueryEngine(safety_level=SafetyLevel.READ_ONLY)
        # Force a dangerous SQL through translate override
        engine.translate = lambda q: "DROP TABLE users"
        result = await engine.ask("drop all data")
        assert result.error is not None
        assert "blocked" in result.error.lower()

    @pytest.mark.asyncio
    async def test_ask_sets_original_question(self):
        mock_client = AsyncMock()
        mock_client.call_tool.return_value = []
        engine = NLQueryEngine(mcp_client=mock_client)
        engine.set_schema([TableSchema(name="items")])
        result = await engine.ask("Show all items")
        assert result.original_question == "Show all items"
