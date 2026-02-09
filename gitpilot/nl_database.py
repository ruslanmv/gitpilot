# gitpilot/nl_database.py
"""Natural language database queries via MCP.

Translates plain-English questions into SQL (or other query languages),
executes them through an MCP database server connection, and returns
human-readable results.

Architecture::

    User question
        │
        ▼
    NLQueryEngine.ask()
        │
        ├─► schema_context()   — fetch table/collection metadata
        ├─► translate()        — NL → SQL via LLM or rule-based
        ├─► validate_query()   — safety checks (no DROP, DELETE without WHERE, etc.)
        ├─► execute()          — run via MCP call_tool
        └─► format_response()  — tabular or narrative answer

Inspired by:
- C3 SQL (2023): zero-shot text-to-SQL with calibrated confidence
- DIN-SQL (2023): decomposed in-context learning for text-to-SQL
- BIRD benchmark (2023): bridging text-to-SQL with real-world databases

Security: Queries are validated before execution.  Destructive statements
(DROP, TRUNCATE, ALTER, DELETE without WHERE) are blocked by default.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Enums & data models
# ---------------------------------------------------------------------------

class QueryDialect(str, Enum):
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    SQLITE = "sqlite"
    GENERIC_SQL = "sql"


class SafetyLevel(str, Enum):
    READ_ONLY = "read_only"         # SELECT only
    READ_WRITE = "read_write"       # SELECT, INSERT, UPDATE (with WHERE)
    UNRESTRICTED = "unrestricted"   # All statements (use with caution)


@dataclass
class TableSchema:
    """Metadata about a database table."""

    name: str
    columns: List[Dict[str, str]] = field(default_factory=list)  # [{"name": ..., "type": ...}]
    primary_key: Optional[str] = None
    row_count: Optional[int] = None
    description: str = ""

    def to_prompt_text(self) -> str:
        """Format as context for LLM prompt."""
        cols = ", ".join(
            f"{c['name']} {c.get('type', 'TEXT')}" for c in self.columns
        )
        pk = f" PK={self.primary_key}" if self.primary_key else ""
        return f"TABLE {self.name} ({cols}){pk}"


@dataclass
class QueryResult:
    """Result of a natural language database query."""

    original_question: str
    generated_sql: str
    dialect: QueryDialect
    rows: List[Dict[str, Any]] = field(default_factory=list)
    columns: List[str] = field(default_factory=list)
    row_count: int = 0
    explanation: str = ""
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "original_question": self.original_question,
            "generated_sql": self.generated_sql,
            "dialect": self.dialect.value,
            "rows": self.rows[:100],  # Limit for API responses
            "columns": self.columns,
            "row_count": self.row_count,
            "explanation": self.explanation,
            "error": self.error,
        }

    def to_table_string(self, max_rows: int = 20) -> str:
        """Format as a plain-text table for CLI output."""
        if self.error:
            return f"Error: {self.error}"
        if not self.rows:
            return "No results."

        headers = self.columns or list(self.rows[0].keys())
        col_widths = [len(h) for h in headers]
        display_rows = self.rows[:max_rows]

        for row in display_rows:
            for i, h in enumerate(headers):
                val = str(row.get(h, ""))
                col_widths[i] = max(col_widths[i], min(len(val), 40))

        sep = "+-" + "-+-".join("-" * w for w in col_widths) + "-+"
        header_line = "| " + " | ".join(
            h.ljust(col_widths[i]) for i, h in enumerate(headers)
        ) + " |"

        lines = [sep, header_line, sep]
        for row in display_rows:
            vals = []
            for i, h in enumerate(headers):
                v = str(row.get(h, ""))[:40]
                vals.append(v.ljust(col_widths[i]))
            lines.append("| " + " | ".join(vals) + " |")
        lines.append(sep)

        if self.row_count > max_rows:
            lines.append(f"... {self.row_count - max_rows} more rows")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# SQL translation helpers
# ---------------------------------------------------------------------------

_BLOCKED_PATTERNS_READ_ONLY = [
    r"\b(?:INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE)\b",
]

_BLOCKED_PATTERNS_READ_WRITE = [
    r"\b(?:DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE)\b",
    r"\bDELETE\b(?!.*\bWHERE\b)",  # DELETE without WHERE
]

_NL_TO_SQL_MAPPINGS = [
    # Pattern → SQL fragment mappings for simple rule-based fallback
    (r"(?i)\bhow many\b.*\b(\w+)\b", "SELECT COUNT(*) FROM {table}"),
    (r"(?i)\blist\s+(?:all\s+)?(\w+)\b", "SELECT * FROM {table} LIMIT 100"),
    (r"(?i)\bshow\s+(?:all\s+)?(\w+)\b", "SELECT * FROM {table} LIMIT 100"),
    (r"(?i)\baverage\b.*\b(\w+)\b.*\bof\b\s+(\w+)", "SELECT AVG({column}) FROM {table}"),
    (r"(?i)\btop\s+(\d+)\b.*\b(\w+)\b", "SELECT * FROM {table} LIMIT {limit}"),
]


class NLQueryEngine:
    """Translate natural language to SQL and execute via MCP.

    Usage::

        engine = NLQueryEngine(dialect=QueryDialect.POSTGRESQL)
        engine.set_schema([table1, table2])

        # Translate only
        sql = engine.translate("How many users signed up this month?")

        # Full pipeline
        result = await engine.ask("Show top 10 customers by revenue")
        print(result.to_table_string())
    """

    def __init__(
        self,
        dialect: QueryDialect = QueryDialect.POSTGRESQL,
        safety_level: SafetyLevel = SafetyLevel.READ_ONLY,
        mcp_client: Optional[Any] = None,
        mcp_tool_name: str = "query",
    ) -> None:
        self.dialect = dialect
        self.safety_level = safety_level
        self.mcp_client = mcp_client
        self.mcp_tool_name = mcp_tool_name
        self._schema: List[TableSchema] = []

    # --- Schema management ------------------------------------------------

    def set_schema(self, tables: List[TableSchema]) -> None:
        """Set the database schema for context-aware translation."""
        self._schema = tables

    def add_table(self, table: TableSchema) -> None:
        """Add a single table to the schema."""
        self._schema.append(table)

    def get_schema_context(self) -> str:
        """Build a prompt-friendly schema description."""
        if not self._schema:
            return "No schema information available."
        return "\n".join(t.to_prompt_text() for t in self._schema)

    def get_table_names(self) -> List[str]:
        """Return list of known table names."""
        return [t.name for t in self._schema]

    # --- Translation ------------------------------------------------------

    def translate(self, question: str) -> str:
        """Translate natural language to SQL.

        Uses rule-based matching as a deterministic fallback.
        In production, this would call an LLM with the schema context.
        """
        table_names = self.get_table_names()
        question_lower = question.lower()

        # Try to find a matching table name in the question
        matched_table = ""
        for name in table_names:
            if name.lower() in question_lower:
                matched_table = name
                break

        # If no exact match, try singular/plural heuristics
        if not matched_table:
            for name in table_names:
                singular = name.rstrip("s").lower()
                if singular in question_lower:
                    matched_table = name
                    break

        if not matched_table and table_names:
            matched_table = table_names[0]

        # Rule-based translation
        for pattern, sql_template in _NL_TO_SQL_MAPPINGS:
            match = re.search(pattern, question)
            if match:
                sql = sql_template.replace("{table}", matched_table)
                # Handle captures
                groups = match.groups()
                if "{limit}" in sql and groups:
                    sql = sql.replace("{limit}", groups[0])
                if "{column}" in sql and len(groups) > 1:
                    sql = sql.replace("{column}", groups[0])
                    sql = sql.replace("{table}", groups[1] if len(groups) > 1 else matched_table)
                return sql

        # Default: SELECT * with LIMIT
        return f"SELECT * FROM {matched_table} LIMIT 100"

    # --- Validation -------------------------------------------------------

    def validate_query(self, sql: str) -> Optional[str]:
        """Validate a SQL query against safety rules.

        Returns None if valid, or an error message string if blocked.
        """
        sql_upper = sql.upper().strip()

        if self.safety_level == SafetyLevel.READ_ONLY:
            patterns = _BLOCKED_PATTERNS_READ_ONLY
        elif self.safety_level == SafetyLevel.READ_WRITE:
            patterns = _BLOCKED_PATTERNS_READ_WRITE
        else:
            return None  # Unrestricted

        for pattern in patterns:
            if re.search(pattern, sql_upper):
                return f"Query blocked by safety policy ({self.safety_level.value}): matches '{pattern}'"

        # Check for multiple statements (SQL injection prevention)
        statements = [s.strip() for s in sql.split(";") if s.strip()]
        if len(statements) > 1:
            return "Multiple SQL statements are not allowed."

        return None

    # --- Execution --------------------------------------------------------

    async def execute(self, sql: str) -> QueryResult:
        """Execute a SQL query via MCP and return structured results."""
        if self.mcp_client is None:
            return QueryResult(
                original_question="",
                generated_sql=sql,
                dialect=self.dialect,
                error="No MCP client configured. Cannot execute queries.",
            )

        try:
            raw = await self.mcp_client.call_tool(
                self.mcp_tool_name,
                {"query": sql},
            )
        except Exception as exc:
            return QueryResult(
                original_question="",
                generated_sql=sql,
                dialect=self.dialect,
                error=f"Execution error: {exc}",
            )

        rows = raw if isinstance(raw, list) else raw.get("rows", []) if isinstance(raw, dict) else []
        columns = list(rows[0].keys()) if rows else []

        return QueryResult(
            original_question="",
            generated_sql=sql,
            dialect=self.dialect,
            rows=rows,
            columns=columns,
            row_count=len(rows),
        )

    # --- Full pipeline ----------------------------------------------------

    async def ask(self, question: str) -> QueryResult:
        """Full NL-to-SQL pipeline: translate → validate → execute.

        This is the main entry point for natural language queries.
        """
        sql = self.translate(question)

        # Validate before execution
        error = self.validate_query(sql)
        if error:
            return QueryResult(
                original_question=question,
                generated_sql=sql,
                dialect=self.dialect,
                error=error,
            )

        result = await self.execute(sql)
        result.original_question = question
        return result

    def explain(self, sql: str) -> str:
        """Return a human-readable explanation of what a SQL query does."""
        upper = sql.upper().strip()
        parts = []

        if upper.startswith("SELECT"):
            # Extract main components
            table_match = re.search(r"\bFROM\s+(\w+)", sql, re.IGNORECASE)
            where_match = re.search(r"\bWHERE\s+(.+?)(?:\bORDER|\bLIMIT|\bGROUP|$)", sql, re.IGNORECASE)
            limit_match = re.search(r"\bLIMIT\s+(\d+)", sql, re.IGNORECASE)

            table = table_match.group(1) if table_match else "unknown"

            if "COUNT(*)" in upper:
                parts.append(f"Count all rows in '{table}'")
            elif "AVG(" in upper:
                parts.append(f"Calculate averages from '{table}'")
            elif "SUM(" in upper:
                parts.append(f"Sum values from '{table}'")
            else:
                parts.append(f"Retrieve data from '{table}'")

            if where_match:
                parts.append(f"filtered by: {where_match.group(1).strip()}")
            if limit_match:
                parts.append(f"limited to {limit_match.group(1)} rows")

        elif upper.startswith("INSERT"):
            table_match = re.search(r"\bINTO\s+(\w+)", sql, re.IGNORECASE)
            table = table_match.group(1) if table_match else "unknown"
            parts.append(f"Insert new data into '{table}'")

        elif upper.startswith("UPDATE"):
            table_match = re.search(r"\bUPDATE\s+(\w+)", sql, re.IGNORECASE)
            table = table_match.group(1) if table_match else "unknown"
            parts.append(f"Update records in '{table}'")

        elif upper.startswith("DELETE"):
            table_match = re.search(r"\bFROM\s+(\w+)", sql, re.IGNORECASE)
            table = table_match.group(1) if table_match else "unknown"
            parts.append(f"Delete records from '{table}'")

        return ". ".join(parts) if parts else "Unknown query type."
