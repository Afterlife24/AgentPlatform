"""PostgreSQL Identifier Sanitizer for the Relational Dataset Tool.

Implements Requirement 2:
1. Transforms CSV headers and filenames into legal, safe PostgreSQL unquoted identifiers.
2. Handles Postgres reserved words by appending `_col`.
3. Handles column name deduplication in left-to-right order using `_2`, `_3`, etc.
4. Handles empty headers via `column_{position}`.
5. Deterministic and bounded to 63 bytes.
"""

import re
from typing import List, Set, Tuple

# PostgreSQL 16+ reserved words that cannot be used as unquoted column/table identifiers
POSTGRES_RESERVED_WORDS = {
    "all", "analyse", "analyze", "and", "any", "array", "as", "asc", "asymmetric",
    "authorization", "binary", "both", "case", "cast", "check", "collate", "collation",
    "column", "concurrently", "constraint", "create", "cross", "current_catalog",
    "current_date", "current_role", "current_schema", "current_time", "current_timestamp",
    "current_user", "default", "deferrable", "desc", "distinct", "do", "else", "end",
    "except", "false", "fetch", "filter", "for", "foreign", "freeze", "from", "full",
    "grant", "group", "having", "ilike", "in", "initially", "inner", "intersect", "into",
    "is", "isnull", "join", "lateral", "leading", "left", "like", "limit", "localtime",
    "localtimestamp", "natural", "not", "notnull", "null", "offset", "on", "only",
    "or", "order", "outer", "overlaps", "placing", "primary", "references", "returning",
    "right", "select", "session_user", "similar", "some", "symmetric", "sysid", "table",
    "tablesample", "then", "to", "trailing", "true", "union", "unique", "user", "using",
    "variadic", "verbose", "when", "where", "window", "with"
}

# Internal reserved names used by the Relational Dataset Tool
INTERNAL_RESERVED_WORDS = {
    "_row_id", "_row_number", "ctid", "oid", "xmin", "cmin", "xmax", "cmax", "tableoid"
}


class IdentifierSanitizer:
    """Deterministic sanitizer for PostgreSQL identifiers."""

    MAX_IDENTIFIER_BYTES = 63

    @classmethod
    def sanitize_identifier(cls, name: str, fallback: str = "column") -> str:
        """Apply Requirement 2 Criterion 1 & 2 sanitization to a string."""
        if not name:
            name = fallback

        # 1. Trim leading and trailing whitespace
        s = name.strip()

        # 2. Convert uppercase ASCII to lowercase ASCII
        s = s.lower()

        # 3. Replace every character that is not lowercase ASCII letter, digit, or underscore with underscore
        s = re.sub(r"[^a-z0-9_]", "_", s)

        # 4. Collapse runs of two or more consecutive underscores into one
        s = re.sub(r"_+", "_", s)

        # 5. Remove leading and trailing underscores
        s = s.strip("_")

        # 6. Prefix with single underscore if begins with an ASCII digit
        if s and s[0].isdigit():
            s = f"_{s}"

        # If empty after stripping
        if not s:
            s = fallback

        # 7. Truncate to at most 63 bytes
        s = cls._truncate_bytes(s, cls.MAX_IDENTIFIER_BYTES)

        # 8. Remove any trailing underscore introduced by truncation
        s = s.rstrip("_")
        if not s:
            s = fallback

        # 9. Handle reserved words (Criterion 2)
        while s in POSTGRES_RESERVED_WORDS or s in INTERNAL_RESERVED_WORDS:
            # truncate so that identifier + '_col' (4 bytes) <= 63 bytes
            max_len = cls.MAX_IDENTIFIER_BYTES - 4
            s = cls._truncate_bytes(s, max_len).rstrip("_")
            s = f"{s}_col"

        return s

    @classmethod
    def sanitize_headers(cls, headers: List[str]) -> List[Tuple[str, str]]:
        """Sanitize an ordered list of CSV headers.

        Returns a list of tuples: (sanitized_identifier, original_header_text).
        Implements left-to-right uniqueness resolution and empty-header fallback.
        """
        result: List[Tuple[str, str]] = []
        seen_identifiers: Set[str] = set()

        for idx, raw_header in enumerate(headers, 1):
            original = (raw_header or "").strip()[:1000]

            if not original:
                base_ident = f"column_{idx}"
            else:
                base_ident = cls.sanitize_identifier(original, fallback=f"column_{idx}")

            ident = base_ident
            suffix_num = 2

            # Criterion 3: left-to-right uniqueness resolution
            while ident in seen_identifiers:
                suffix = f"_{suffix_num}"
                max_base_len = cls.MAX_IDENTIFIER_BYTES - len(suffix)
                truncated_base = cls._truncate_bytes(base_ident, max_base_len).rstrip("_")
                ident = f"{truncated_base}{suffix}"
                suffix_num += 1

            seen_identifiers.add(ident)
            result.append((ident, original))

        return result

    @classmethod
    def sanitize_table_name(cls, filename: str, existing_table_names: Set[str]) -> str:
        """Derive a DB Table identifier from a filename (Requirement 2 Criterion 7)."""
        # Strip extension
        clean_name = filename
        if "." in clean_name:
            clean_name = clean_name.rsplit(".", 1)[0]

        base_ident = cls.sanitize_identifier(clean_name, fallback="db_table")

        ident = base_ident
        suffix_num = 2

        while ident in existing_table_names:
            suffix = f"_{suffix_num}"
            max_base_len = cls.MAX_IDENTIFIER_BYTES - len(suffix)
            truncated_base = cls._truncate_bytes(base_ident, max_base_len).rstrip("_")
            ident = f"{truncated_base}{suffix}"
            suffix_num += 1

        return ident

    @staticmethod
    def _truncate_bytes(s: str, max_bytes: int) -> str:
        """Truncate string to at most max_bytes in UTF-8."""
        encoded = s.encode("utf-8")
        if len(encoded) <= max_bytes:
            return s
        truncated = encoded[:max_bytes]
        # Decode ignoring broken trailing multibyte character
        return truncated.decode("utf-8", errors="ignore")
