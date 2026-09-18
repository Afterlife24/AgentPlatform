"""Primary Key and Uniqueness Detector for the Relational Dataset Tool.

Implements Requirement 5:
1. Evaluates uniqueness and nullness per column across all rows.
2. Identifies candidate keys (row_count >= 2, duplicates == 0, nulls == 0).
3. Nominates suggested primary key using precedence rules: id -> *_id -> integer -> leftmost.
4. Marks low-confidence flag if row count < 10.
5. All outputs are metadata only — no PostgreSQL constraints or unique indexes emitted.
"""

from typing import Any, Dict, List, Optional


class KeyDetector:
    """Detects candidate keys and nominates suggested primary keys."""

    MIN_ROWS_FOR_CANDIDATE_KEY = 2
    LOW_CONFIDENCE_ROW_THRESHOLD = 10

    @classmethod
    def evaluate_table_keys(
        cls,
        row_count: int,
        columns: List[Dict[str, Any]],  # List of column schema dicts with metrics
    ) -> Dict[str, Any]:
        """Evaluate columns for candidate keys and nominate a suggested primary key.

        Args:
            row_count: Total data rows committed.
            columns: List of column dicts containing 'name', 'type', 'null_count', 'distinct_count'.

        Returns:
            Dict containing evaluated column metadata, candidate keys, and suggested primary key.
        """
        candidate_keys: List[str] = []
        low_confidence = row_count < cls.LOW_CONFIDENCE_ROW_THRESHOLD

        if row_count < cls.MIN_ROWS_FOR_CANDIDATE_KEY:
            # Below minimum rows for candidate keys
            return {
                "candidate_keys": [],
                "suggested_primary_key": None,
                "low_confidence": low_confidence,
                "reason": f"Row count ({row_count}) is below minimum of {cls.MIN_ROWS_FOR_CANDIDATE_KEY} required for uniqueness evaluation.",
            }

        evaluated_columns = []
        for idx, col in enumerate(columns):
            name = col["name"]
            null_count = col.get("null_count", 0)
            distinct_count = col.get("distinct_count", 0)
            non_null_count = max(0, row_count - null_count)
            duplicate_count = max(0, non_null_count - distinct_count)

            is_candidate = (null_count == 0 and duplicate_count == 0 and non_null_count == row_count)
            if is_candidate:
                candidate_keys.append(name)

            col_info = dict(col)
            col_info["is_candidate_key"] = is_candidate
            col_info["duplicate_count"] = duplicate_count
            col_info["null_count"] = null_count
            col_info["distinct_count"] = distinct_count
            col_info["csv_position"] = idx
            evaluated_columns.append(col_info)

        suggested_pk = cls._nominate_suggested_primary_key(candidate_keys, evaluated_columns)

        return {
            "candidate_keys": candidate_keys,
            "suggested_primary_key": suggested_pk,
            "low_confidence": low_confidence,
            "columns": evaluated_columns,
        }

    @classmethod
    def _nominate_suggested_primary_key(
        cls, candidate_keys: List[str], columns: List[Dict[str, Any]]
    ) -> Optional[str]:
        """Nominate a suggested primary key from candidate keys."""
        if not candidate_keys:
            return None

        col_map = {c["name"]: c for c in columns}

        # Rule 1: sanitized identifier is exactly 'id'
        for k in candidate_keys:
            if k == "id":
                return k

        # Rule 2: identifier ends with '_id'
        for k in candidate_keys:
            if k.endswith("_id"):
                return k

        # Rule 3: assigned type is 'integer'
        for k in candidate_keys:
            if col_map.get(k, {}).get("type") == "integer":
                return k

        # Rule 4: leftmost candidate key in CSV column order
        sorted_keys = sorted(
            candidate_keys,
            key=lambda k: col_map.get(k, {}).get("csv_position", 999999),
        )
        return sorted_keys[0] if sorted_keys else None
