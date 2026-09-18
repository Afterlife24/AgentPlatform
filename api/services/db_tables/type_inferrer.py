"""Type inference engine for the Relational Dataset Tool.

Implements Requirement 3:
1. Strict, all-must-match inference across integer, numeric, boolean, date, timestamp, text.
2. Precedence: integer -> numeric -> boolean -> date -> timestamp -> text.
3. Literals '0' and '1' are numeric/integer and never boolean.
4. Ambiguous date formats fall back to text.
5. Generates detailed Type Report with up to 50 non-conforming example rows.
"""

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

NULL_TOKENS = {"", "n/a", "na", "-", "none", "null", "nil"}

# Regex patterns for strict parsing
INT_PATTERN = re.compile(r"^[+-]?\d{1,19}$")
NUMERIC_PATTERN = re.compile(r"^[+-]?\d{1,28}(?:\.\d{1,6})?$")

BOOLEAN_TOKENS = {"true", "false", "yes", "no", "t", "f", "y", "n"}

DATE_FORMATS = [
    ("%Y-%m-%d", "YYYY-MM-DD"),
    ("%d/%m/%Y", "DD/MM/YYYY"),
    ("%m/%d/%Y", "MM/DD/YYYY"),
]

TIME_SUFFIX_PATTERNS = [
    r" \d{2}:\d{2}",
    r" \d{2}:\d{2}:\d{2}",
    r" \d{2}:\d{2}(?:[+-]\d{2}:?\d{2}|Z)",
    r" \d{2}:\d{2}:\d{2}(?:[+-]\d{2}:?\d{2}|Z)",
]


class TypeInferrer:
    """Evaluates CSV column data and produces assigned types and type reports."""

    MAX_RECORDED_EXAMPLES = 50
    MAX_EXAMPLE_LENGTH = 200

    @classmethod
    def is_null_value(cls, val: Any) -> bool:
        """Check if a raw cell value is null according to Requirement 3 Criterion 4."""
        if val is None:
            return True
        s = str(val).strip().lower()
        return s in NULL_TOKENS

    @classmethod
    def parse_as_integer(cls, s: str) -> bool:
        """Integer: optional sign, 1-19 digits, within signed 64-bit int range."""
        if not INT_PATTERN.match(s):
            return False
        try:
            val = int(s)
            return -9223372036854775808 <= val <= 9223372036854775807
        except ValueError:
            return False

    @classmethod
    def parse_as_numeric(cls, s: str) -> bool:
        """Numeric: optional sign, 1-28 digits, optional . followed by 1-6 digits."""
        return bool(NUMERIC_PATTERN.match(s))

    @classmethod
    def parse_as_boolean(cls, s: str) -> bool:
        """Boolean: true, false, yes, no, t, f, y, n (0 and 1 are explicitly excluded)."""
        lower = s.lower()
        if lower in ("0", "1"):
            return False
        return lower in BOOLEAN_TOKENS

    @classmethod
    def match_date_format(cls, s: str) -> Optional[str]:
        """Try matching supported date formats. Returns the format label if unambiguous."""
        for fmt, label in DATE_FORMATS:
            try:
                dt = datetime.strptime(s, fmt)
                # Verify round-trip formatting to prevent strptime lenient parsing
                if dt.strftime(fmt) == s:
                    return label
            except ValueError:
                pass
        return None

    @classmethod
    def match_timestamp_format(cls, s: str) -> Optional[str]:
        """Try matching supported timestamp formats."""
        for fmt, date_label in DATE_FORMATS:
            for time_fmt in ["%H:%M", "%H:%M:%S"]:
                full_fmt = f"{fmt} {time_fmt}"
                try:
                    dt = datetime.strptime(s, full_fmt)
                    if dt.strftime(full_fmt) == s:
                        return f"{date_label} {time_fmt}"
                except ValueError:
                    pass
        return None

    @classmethod
    def infer_column(
        cls,
        col_identifier: str,
        original_header: str,
        values: List[Tuple[int, Any]],  # list of (1-based row number, raw value)
        forced_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Infer type for a single column across all data rows."""
        total_rows = len(values)
        null_count = 0
        non_null_values: List[Tuple[int, str]] = []
        distinct_values: Set[str] = set()

        for row_num, val in values:
            if cls.is_null_value(val):
                null_count += 1
            else:
                trimmed = str(val).strip()
                non_null_values.append((row_num, trimmed))
                distinct_values.add(trimmed)

        distinct_count = len(distinct_values)

        # Forced text override
        if forced_type == "text":
            return {
                "name": col_identifier,
                "original_header": original_header,
                "type": "text",
                "null_count": null_count,
                "distinct_count": distinct_count,
                "non_conforming_count": 0,
                "non_conforming_examples": [],
                "truncated": False,
                "is_forced": True,
            }

        if not non_null_values:
            # Criterion 8: zero non-null values -> text
            return {
                "name": col_identifier,
                "original_header": original_header,
                "type": "text",
                "null_count": null_count,
                "distinct_count": 0,
                "non_conforming_count": 0,
                "non_conforming_examples": [],
                "truncated": False,
                "note": "no observed values",
            }

        # Check candidate types in strict precedence order
        # 1. Integer
        int_failures: List[Tuple[int, str]] = []
        for r_num, s_val in non_null_values:
            if not cls.parse_as_integer(s_val):
                int_failures.append((r_num, s_val))

        if not int_failures:
            return {
                "name": col_identifier,
                "original_header": original_header,
                "type": "integer",
                "null_count": null_count,
                "distinct_count": distinct_count,
                "non_conforming_count": 0,
                "non_conforming_examples": [],
                "truncated": False,
            }

        # 2. Numeric
        num_failures: List[Tuple[int, str]] = []
        for r_num, s_val in non_null_values:
            if not cls.parse_as_numeric(s_val):
                num_failures.append((r_num, s_val))

        if not num_failures:
            return {
                "name": col_identifier,
                "original_header": original_header,
                "type": "numeric",
                "null_count": null_count,
                "distinct_count": distinct_count,
                "non_conforming_count": len(int_failures),
                "non_conforming_examples": [
                    {"row": r, "value": v[:cls.MAX_EXAMPLE_LENGTH]}
                    for r, v in int_failures[:cls.MAX_RECORDED_EXAMPLES]
                ],
                "truncated": len(int_failures) > cls.MAX_RECORDED_EXAMPLES,
            }

        # 3. Boolean
        bool_failures: List[Tuple[int, str]] = []
        for r_num, s_val in non_null_values:
            if not cls.parse_as_boolean(s_val):
                bool_failures.append((r_num, s_val))

        if not bool_failures:
            return {
                "name": col_identifier,
                "original_header": original_header,
                "type": "boolean",
                "null_count": null_count,
                "distinct_count": distinct_count,
                "non_conforming_count": 0,
                "non_conforming_examples": [],
                "truncated": False,
            }

        # 4. Date (check ambiguity)
        date_failures: List[Tuple[int, str]] = []
        matched_date_formats: Set[str] = set()
        for r_num, s_val in non_null_values:
            fmt_label = cls.match_date_format(s_val)
            if fmt_label:
                matched_date_formats.add(fmt_label)
            else:
                date_failures.append((r_num, s_val))

        # Criterion 9: If all match date, but matched more than one format (ambiguous), fall back to text
        if not date_failures and len(matched_date_formats) == 1:
            return {
                "name": col_identifier,
                "original_header": original_header,
                "type": "date",
                "null_count": null_count,
                "distinct_count": distinct_count,
                "non_conforming_count": 0,
                "non_conforming_examples": [],
                "truncated": False,
            }

        # 5. Timestamp (check ambiguity)
        ts_failures: List[Tuple[int, str]] = []
        matched_ts_formats: Set[str] = set()
        for r_num, s_val in non_null_values:
            ts_label = cls.match_timestamp_format(s_val)
            if ts_label:
                matched_ts_formats.add(ts_label)
            else:
                ts_failures.append((r_num, s_val))

        if not ts_failures and len(matched_ts_formats) == 1:
            return {
                "name": col_identifier,
                "original_header": original_header,
                "type": "timestamp",
                "null_count": null_count,
                "distinct_count": distinct_count,
                "non_conforming_count": 0,
                "non_conforming_examples": [],
                "truncated": False,
            }

        # Fallback to Text (Criterion 3 & 6: record strictest rejected candidate type)
        # Select strictest rejected candidate type (fewest failures)
        candidate_checks = [
            ("numeric", num_failures),
            ("integer", int_failures),
            ("date", date_failures),
            ("timestamp", ts_failures),
            ("boolean", bool_failures),
        ]
        candidate_checks.sort(key=lambda item: len(item[1]))
        strictest_type, strictest_failures = candidate_checks[0]

        is_ambiguous = (not date_failures and len(matched_date_formats) > 1) or (
            not ts_failures and len(matched_ts_formats) > 1
        )

        return {
            "name": col_identifier,
            "original_header": original_header,
            "type": "text",
            "null_count": null_count,
            "distinct_count": distinct_count,
            "non_conforming_count": len(strictest_failures),
            "non_conforming_examples": [
                {"row": r, "value": v[:cls.MAX_EXAMPLE_LENGTH]}
                for r, v in strictest_failures[:cls.MAX_RECORDED_EXAMPLES]
            ],
            "truncated": len(strictest_failures) > cls.MAX_RECORDED_EXAMPLES,
            "ambiguous": is_ambiguous,
            "matched_formats": list(matched_date_formats or matched_ts_formats) if is_ambiguous else None,
        }
