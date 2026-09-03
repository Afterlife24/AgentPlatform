"""CSV Table schema and value-profile helpers.

The old query_csv_table and aggregate_csv_table tools, along with the
planner LLM (_resolve_nl_query_to_plan) and its three post-processors
(_infer_fallback_plan, _enforce_approximate_filters, _fix_brand_in_model_filter),
have been removed. execute_csv_sql in csv_sql_executor.py is now the only
structured-data tool.

What remains:
  - get_column_schema_for_tables  : merged {name, type} per column
  - build_value_profile_from_rows : pure builder — per-column value profiles
  - render_value_profile           : renders the profile as a compact prompt block
  - build_value_profile            : async DB-backed builder (lazy fallback)
  - get_value_profile_for_tables   : cached resolver used by the context composer
  - clear_value_profile_cache      : called after CSV re-upload
"""

from typing import Any, Dict, List, Optional

from loguru import logger

from api.db import db_client

# Log prefix — makes every line from this module easy to grep
_LOG = "📊 [ValueProfile]"


# ---------------------------------------------------------------------------
# Schema metadata fetching
# ---------------------------------------------------------------------------

async def get_column_schema_for_tables(
    organization_id: int,
    table_uuids: List[str],
) -> List[Dict]:
    """Fetch combined column schema for a set of table UUIDs."""
    try:
        seen: set = set()
        merged: List[Dict] = []
        for uid in table_uuids:
            table = await db_client.get_csv_table_by_uuid(uid, organization_id)
            if table and table.column_schema:
                for col in table.column_schema:
                    name = col.get("name", "")
                    if name and name not in seen:
                        seen.add(name)
                        merged.append(col)
        logger.debug(
            f"{_LOG} get_column_schema_for_tables | "
            f"org={organization_id} tables={table_uuids} → {len(merged)} columns"
        )
        return merged
    except Exception as e:
        logger.warning(f"{_LOG} get_column_schema_for_tables FAILED: {e}")
        return []


# ---------------------------------------------------------------------------
# Value profile — the fix that removes value hallucination
# ---------------------------------------------------------------------------

VALUE_PROFILE_CUTOFF = 25
VALUE_PROFILE_EXAMPLES = 3


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _coerce_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def build_value_profile_from_rows(
    rows: List[Dict[str, Any]],
    column_schema: List[Dict],
    *,
    cutoff: int = VALUE_PROFILE_CUTOFF,
) -> List[Dict]:
    """Build a per-column value profile from data rows. Pure function, no I/O.

    Rules:
      text, distinct <= cutoff  → every distinct value listed
      text, distinct >  cutoff  → distinct count + examples
      number                    → min and max
      unnamed column ("")       → skipped
      all-NULL / all-empty      → skipped
    """
    profile: List[Dict] = []
    skipped_unnamed = 0
    skipped_empty = 0

    for col in column_schema:
        name = col.get("name") or ""
        if not name.strip():
            skipped_unnamed += 1
            continue

        non_empty = [
            value for value in (row.get(name) for row in rows)
            if not _is_empty(value)
        ]
        if not non_empty:
            skipped_empty += 1
            continue

        declared_type = str(col.get("type") or "text").lower()

        if declared_type == "number":
            numbers = [n for n in (_coerce_number(v) for v in non_empty) if n is not None]
            if not numbers:
                skipped_empty += 1
                continue
            profile.append({
                "name": name,
                "type": "number",
                "distinct_count": len(set(numbers)),
                "values": None,
                "examples": None,
                "min": min(numbers),
                "max": max(numbers),
            })
            continue

        distinct = sorted({str(v).strip() for v in non_empty})
        entry: Dict[str, Any] = {
            "name": name,
            "type": "text",
            "distinct_count": len(distinct),
            "values": None,
            "examples": None,
            "min": None,
            "max": None,
        }
        if len(distinct) <= cutoff:
            entry["values"] = distinct
        else:
            entry["examples"] = distinct[:VALUE_PROFILE_EXAMPLES]
        profile.append(entry)

    logger.debug(
        f"{_LOG} build_value_profile_from_rows | "
        f"rows={len(rows)} schema_cols={len(column_schema)} "
        f"profiled={len(profile)} skipped_unnamed={skipped_unnamed} skipped_empty={skipped_empty}"
    )
    return profile


def _fmt_number(value: float) -> str:
    return f"{value:g}"


def render_value_profile(profile: List[Dict]) -> str:
    """Render the value profile as a compact prompt block. Pure, no I/O."""
    if not profile:
        logger.debug(f"{_LOG} render_value_profile | profile is empty — nothing injected")
        return ""

    lines = [
        "DATA COLUMNS AND THEIR ACTUAL VALUES",
        "These are the exact column names and the exact values stored in the table.",
        "Use them verbatim in SQL. Never invent a column name or a value.",
    ]

    text_cols = 0
    number_cols = 0

    for entry in profile:
        name = entry.get("name", "")
        distinct_count = entry.get("distinct_count", 0)

        if entry.get("type") == "number":
            lines.append(
                f"- {name} (number, {distinct_count} distinct): "
                f"min {_fmt_number(float(entry.get('min') or 0.0))} "
                f"max {_fmt_number(float(entry.get('max') or 0.0))}"
            )
            number_cols += 1
        elif entry.get("values") is not None:
            values = " | ".join(str(v) for v in entry["values"])
            lines.append(f"- {name} (text, {distinct_count} distinct): {values}")
            text_cols += 1
        else:
            examples = " | ".join(str(v) for v in (entry.get("examples") or []))
            lines.append(f"- {name} (text, {distinct_count} distinct) examples: {examples}")
            text_cols += 1

    rendered = "\n".join(lines)
    logger.info(
        f"{_LOG} render_value_profile | "
        f"total_cols={len(profile)} text={text_cols} number={number_cols} "
        f"prompt_chars={len(rendered)}"
    )
    return rendered


async def build_value_profile(
    organization_id: int,
    table_uuids: List[str],
    *,
    cutoff: int = VALUE_PROFILE_CUTOFF,
) -> List[Dict]:
    """Read the rows of each table and profile them. Returns [] on any failure."""
    logger.info(
        f"{_LOG} build_value_profile | "
        f"org={organization_id} tables={table_uuids} (lazy compute — not cached yet)"
    )
    try:
        merged: List[Dict] = []
        seen: set = set()
        for uid in table_uuids:
            table = await db_client.get_csv_table_by_uuid(uid, organization_id)
            if not table or not table.column_schema:
                logger.warning(
                    f"{_LOG} build_value_profile | table {uid} not found or has no schema"
                )
                continue
            rows_raw = await db_client.execute_raw_query(
                "SELECT row_data FROM csv_table_rows "
                "WHERE table_id = :table_id AND organization_id = :org_id "
                "ORDER BY row_index",
                {"table_id": table.id, "org_id": organization_id},
            )
            rows = [
                r["row_data"] for r in rows_raw
                if isinstance(r.get("row_data"), dict)
            ]
            logger.debug(
                f"{_LOG} build_value_profile | "
                f"table_uuid={uid} table_id={table.id} rows_fetched={len(rows)}"
            )
            for entry in build_value_profile_from_rows(rows, table.column_schema, cutoff=cutoff):
                if entry["name"] not in seen:
                    seen.add(entry["name"])
                    merged.append(entry)
        logger.info(
            f"{_LOG} build_value_profile | "
            f"done → {len(merged)} columns profiled across {len(table_uuids)} table(s)"
        )
        return merged
    except Exception as e:
        logger.warning(f"{_LOG} build_value_profile FAILED: {e}")
        return []


# ---------------------------------------------------------------------------
# Process-local cache
# ---------------------------------------------------------------------------

_VALUE_PROFILE_CACHE: Dict[tuple, List[Dict]] = {}


def clear_value_profile_cache() -> None:
    """Drop every cached profile. Call after a CSV is re-ingested."""
    count = len(_VALUE_PROFILE_CACHE)
    _VALUE_PROFILE_CACHE.clear()
    logger.info(f"{_LOG} clear_value_profile_cache | cleared {count} cache entries")


async def get_value_profile_for_tables(
    organization_id: int,
    table_uuids: List[str],
) -> List[Dict]:
    """Merged value profile for a node's tables. Returns [] on any failure.

    Resolution order per table:
      1. csv_tables.value_profile (written at ingest)
      2. lazy compute + persist (for tables ingested before this change)
    """
    if not table_uuids:
        logger.debug(f"{_LOG} get_value_profile_for_tables | no table_uuids — skipping")
        return []

    key = (organization_id, tuple(sorted(table_uuids)))
    cached = _VALUE_PROFILE_CACHE.get(key)
    if cached is not None:
        logger.debug(
            f"{_LOG} get_value_profile_for_tables | "
            f"CACHE HIT org={organization_id} tables={table_uuids} → {len(cached)} cols"
        )
        return cached

    logger.info(
        f"{_LOG} get_value_profile_for_tables | "
        f"CACHE MISS org={organization_id} tables={table_uuids} — resolving..."
    )

    try:
        merged: List[Dict] = []
        seen: set = set()
        for uid in table_uuids:
            table = await db_client.get_csv_table_by_uuid(uid, organization_id)
            if not table:
                logger.warning(
                    f"{_LOG} get_value_profile_for_tables | table {uid} not found"
                )
                continue

            entries = getattr(table, "value_profile", None)

            if entries:
                logger.debug(
                    f"{_LOG} get_value_profile_for_tables | "
                    f"table {uid} — loaded {len(entries)} cols from stored value_profile"
                )
            else:
                # Lazy compute for tables ingested before this change
                logger.info(
                    f"{_LOG} get_value_profile_for_tables | "
                    f"table {uid} — no stored profile, computing lazily..."
                )
                entries = await build_value_profile(organization_id, [uid])
                if entries:
                    try:
                        await db_client.update_csv_table_value_profile(table.id, entries)
                        logger.info(
                            f"{_LOG} get_value_profile_for_tables | "
                            f"table {uid} — lazy profile persisted ({len(entries)} cols)"
                        )
                    except Exception as persist_err:
                        logger.warning(
                            f"{_LOG} get_value_profile_for_tables | "
                            f"table {uid} — persist failed (non-fatal): {persist_err}"
                        )

            for entry in entries or []:
                name = entry.get("name")
                if name and name not in seen:
                    seen.add(name)
                    merged.append(entry)

        _VALUE_PROFILE_CACHE[key] = merged
        logger.info(
            f"{_LOG} get_value_profile_for_tables | "
            f"CACHED org={organization_id} → {len(merged)} columns total"
        )
        return merged

    except Exception as e:
        logger.warning(f"{_LOG} get_value_profile_for_tables FAILED: {e}")
        return []
