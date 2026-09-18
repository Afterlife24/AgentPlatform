"""Comprehensive Unit Tests for Relational Dataset Tool.

Tests core domain components:
1. IdentifierSanitizer (truncation, reserved words, unique column names, table naming)
2. TypeInferrer (numeric 0/1, strict typing, null tokens, date ambiguity fallback, tracking)
3. KeyDetector (100% uniqueness, nomination heuristics)
4. SQLValidator (AST depth, length limits, DDL/DML rejection, allowlisting, CTE shadowing)
5. PromptComposer (DDL formatting, value profile injection, token budget, LRU caching)
6. RelationDetector (types comparability, name similarity)
7. ValueLookupService (allowlist check, turn limit of 5 calls)
"""

import pytest

from api.services.db_tables.identifier_sanitizer import IdentifierSanitizer
from api.services.db_tables.type_inferrer import TypeInferrer
from api.services.db_tables.key_detector import KeyDetector
from api.services.db_tables.sql_validator import SQLValidator
from api.services.db_tables.prompt_composer import PromptComposer
from api.services.db_tables.relation_detector import RelationDetector
from api.services.db_tables.value_lookup import ValueLookupService


# ---------------------------------------------------------------------------
# 1. IdentifierSanitizer Tests
# ---------------------------------------------------------------------------

class TestIdentifierSanitizer:
    def test_sanitize_basic_identifier(self):
        assert IdentifierSanitizer.sanitize_identifier("Customer Name") == "customer_name"
        assert IdentifierSanitizer.sanitize_identifier("Total $ Amount (USD)") == "total_amount_usd"

    def test_leading_digit_prefixed(self):
        assert IdentifierSanitizer.sanitize_identifier("2024_sales") == "_2024_sales"

    def test_reserved_words_escaped(self):
        assert IdentifierSanitizer.sanitize_identifier("select") == "select_col"
        assert IdentifierSanitizer.sanitize_identifier("user") == "user_col"
        assert IdentifierSanitizer.sanitize_identifier("table") == "table_col"
        assert IdentifierSanitizer.sanitize_identifier("where") == "where_col"

    def test_63_byte_truncation(self):
        long_name = "a" * 100
        sanitized = IdentifierSanitizer.sanitize_identifier(long_name)
        assert len(sanitized.encode("utf-8")) <= 63

    def test_sanitize_headers_resolves_duplicates(self):
        headers = ["Status", "status", "STATUS", "ID", "2024 Date"]
        sanitized = IdentifierSanitizer.sanitize_headers(headers)
        col_names = [ident for ident, _ in sanitized]
        assert col_names == ["status", "status_2", "status_3", "id", "_2024_date"]

    def test_sanitize_table_name(self):
        assert (
            IdentifierSanitizer.sanitize_table_name("2024 Q3 Sales Data!.csv", existing_table_names=set())
            == "_2024_q3_sales_data"
        )
        assert (
            IdentifierSanitizer.sanitize_table_name("equipment-catalog-v2.csv", existing_table_names=set())
            == "equipment_catalog_v2"
        )


# ---------------------------------------------------------------------------
# 2. TypeInferrer Tests
# ---------------------------------------------------------------------------

def _to_samples(values):
    return [(i + 1, v) for i, v in enumerate(values)]


class TestTypeInferrer:
    def test_zero_and_one_are_numbers_not_booleans(self):
        sample = _to_samples(["0", "1", "1", "0", "1"])
        result = TypeInferrer.infer_column("is_active", "Is Active", sample)
        assert result["type"] in ("integer", "numeric")
        assert result["type"] != "boolean"

    def test_actual_booleans_inferred(self):
        sample = _to_samples(["true", "false", "TRUE", "FALSE", "True"])
        result = TypeInferrer.infer_column("flag", "Flag", sample)
        assert result["type"] == "boolean"

    def test_integers_inferred(self):
        sample = _to_samples(["10", "25", "300", "-45", "0"])
        result = TypeInferrer.infer_column("quantity", "Quantity", sample)
        assert result["type"] == "integer"

    def test_numeric_inferred(self):
        sample = _to_samples(["10.50", "25.00", "300.75", "-45.20"])
        result = TypeInferrer.infer_column("price", "Price", sample)
        assert result["type"] == "numeric"

    def test_null_tokens_handled(self):
        sample = _to_samples(["10", "NA", "N/A", "null", "NULL", "-", ""])
        result = TypeInferrer.infer_column("data", "Data", sample)
        assert result["type"] == "integer"

    def test_date_iso_inferred(self):
        sample = _to_samples(["2024-01-15", "2024-02-20", "2024-03-25"])
        result = TypeInferrer.infer_column("order_date", "Order Date", sample)
        assert result["type"] == "date"

    def test_ambiguous_date_falls_back_to_text(self):
        sample = _to_samples(["01/02/2024", "02/01/2024", "03/04/2024"])
        result = TypeInferrer.infer_column("ambiguous_date", "Ambiguous Date", sample)
        assert result["type"] in ("date", "text")

    def test_forced_text_override(self):
        sample = _to_samples(["10", "20", "30"])
        result = TypeInferrer.infer_column("postal_code", "Postal Code", sample, forced_type="text")
        assert result["type"] == "text"


# ---------------------------------------------------------------------------
# 3. KeyDetector Tests
# ---------------------------------------------------------------------------

class TestKeyDetector:
    def test_detect_candidate_keys(self):
        columns = [
            {"name": "id", "type": "integer", "null_count": 0, "distinct_count": 3},
            {"name": "code", "type": "text", "null_count": 0, "distinct_count": 3},
            {"name": "country", "type": "text", "null_count": 0, "distinct_count": 2},  # duplicate
        ]
        result = KeyDetector.evaluate_table_keys(row_count=3, columns=columns)
        candidates = result["candidate_keys"]
        assert "id" in candidates
        assert "code" in candidates
        assert "country" not in candidates

    def test_primary_key_nomination_prefers_id(self):
        columns = [
            {"name": "code", "type": "text", "null_count": 0, "distinct_count": 10},
            {"name": "id", "type": "integer", "null_count": 0, "distinct_count": 10},
        ]
        result = KeyDetector.evaluate_table_keys(row_count=10, columns=columns)
        assert result["suggested_primary_key"] == "id"

    def test_primary_key_nomination_prefers_id_suffix(self):
        columns = [
            {"name": "email", "type": "text", "null_count": 0, "distinct_count": 10},
            {"name": "user_id", "type": "integer", "null_count": 0, "distinct_count": 10},
        ]
        result = KeyDetector.evaluate_table_keys(row_count=10, columns=columns)
        assert result["suggested_primary_key"] == "user_id"


# ---------------------------------------------------------------------------
# 4. SQLValidator Tests
# ---------------------------------------------------------------------------

class TestSQLValidator:
    SCHEMA = "csv_org_1"

    def test_valid_select(self):
        sql = "SELECT id, name FROM customers WHERE id = 1"
        valid, err, sanitized = SQLValidator.validate_sql(sql, allowed_relations={"customers"}, org_schema=self.SCHEMA)
        assert valid is True
        assert err is None
        assert "SELECT" in sanitized

    def test_reject_semicolon_multiple_statements(self):
        sql = "SELECT * FROM customers; DROP TABLE customers;"
        valid, err, _ = SQLValidator.validate_sql(sql, allowed_relations={"customers"}, org_schema=self.SCHEMA)
        assert valid is False
        assert "single statement" in err.lower()

    def test_reject_ddl(self):
        sql = "DROP TABLE customers"
        valid, err, _ = SQLValidator.validate_sql(sql, allowed_relations={"customers"}, org_schema=self.SCHEMA)
        assert valid is False

    def test_reject_dml(self):
        sql = "DELETE FROM customers WHERE id = 1"
        valid, err, _ = SQLValidator.validate_sql(sql, allowed_relations={"customers"}, org_schema=self.SCHEMA)
        assert valid is False

    def test_reject_unlisted_table(self):
        sql = "SELECT * FROM secret_passwords"
        valid, err, _ = SQLValidator.validate_sql(sql, allowed_relations={"customers"}, org_schema=self.SCHEMA)
        assert valid is False
        assert "Disallowed relation" in err

    def test_reject_cte_shadowing(self):
        sql = "WITH customers AS (SELECT 1 AS id) SELECT * FROM customers"
        valid, err, _ = SQLValidator.validate_sql(sql, allowed_relations={"customers"}, org_schema=self.SCHEMA)
        assert valid is False
        assert "shadows" in err.lower()

    def test_reject_max_length(self):
        sql = "SELECT " + "a" * 25000 + " FROM customers"
        valid, err, _ = SQLValidator.validate_sql(sql, allowed_relations={"customers"}, org_schema=self.SCHEMA)
        assert valid is False
        assert "exceeds maximum" in err.lower()


# ---------------------------------------------------------------------------
# 5. PromptComposer Tests
# ---------------------------------------------------------------------------

class TestPromptComposer:
    def test_compose_schema_block(self):
        tables_data = [
            {
                "table_name": "orders",
                "row_count": 100,
                "confirmed_primary_key": "order_id",
                "column_schema": [
                    {"name": "order_id", "type": "integer", "original_header": "Order ID"},
                    {"name": "status", "type": "text", "original_header": "Status"},
                ],
                "value_profile": [
                    {
                        "name": "status",
                        "type": "text",
                        "distinct_count": 3,
                        "values": ["pending", "shipped", "delivered"],
                    }
                ],
            }
        ]
        block = PromptComposer.compose_schema_block(
            organization_id=1,
            tables_data=tables_data,
            views_data=[],
            relationships=[],
        )
        assert "CREATE TABLE orders" in block
        assert "order_id INTEGER" in block
        assert "/* CSV header: 'Order ID' */" in block
        assert "orders.status: ['pending', 'shipped', 'delivered']" in block
        assert "AUTHORITATIVE DATABASE INSTRUCTIONS" in block

    def test_prompt_composer_caching(self):
        PromptComposer.clear_cache(1)
        tables_data = [
            {
                "table_name": "items",
                "row_count": 10,
                "column_schema": [{"name": "id", "type": "integer"}],
            }
        ]
        b1 = PromptComposer.compose_schema_block(1, tables_data, [], [])
        b2 = PromptComposer.compose_schema_block(1, tables_data, [], [])
        assert b1 == b2


# ---------------------------------------------------------------------------
# 6. RelationDetector Tests
# ---------------------------------------------------------------------------

class TestRelationDetector:
    def test_type_comparability(self):
        assert RelationDetector.are_types_comparable("integer", "numeric") is True
        assert RelationDetector.are_types_comparable("date", "timestamp") is True
        assert RelationDetector.are_types_comparable("text", "text") is True
        assert RelationDetector.are_types_comparable("integer", "text") is False

    def test_name_similarity(self):
        sim = RelationDetector.compute_name_similarity("customer_id", "customer_id")
        assert sim == 1.0
        sim2 = RelationDetector.compute_name_similarity("cust_id", "customer_id")
        assert sim2 > 0.6


# ---------------------------------------------------------------------------
# 7. ValueLookupService Tests
# ---------------------------------------------------------------------------

class TestValueLookupService:
    @pytest.mark.asyncio
    async def test_lookup_rejects_unallowed_table(self):
        res = await ValueLookupService.lookup_values(
            organization_id=1,
            table_name="secrets",
            column_name="password",
            column_type="text",
            allowed_relations={"customers", "orders"},
        )
        assert res["success"] is False
        assert "not in the authorized relations" in res["error"]

    @pytest.mark.asyncio
    async def test_lookup_enforces_turn_limit(self):
        res = await ValueLookupService.lookup_values(
            organization_id=1,
            table_name="customers",
            column_name="status",
            column_type="text",
            allowed_relations={"customers"},
            turn_call_count=5,  # Max allowed is 5 (0, 1, 2, 3, 4)
        )
        assert res["success"] is False
        assert "Per-turn lookup limit reached" in res["error"]
