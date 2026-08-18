"""CSV Table tool — text-to-SQL counterpart to the RAG knowledge base tool.

Exposes two LLM-callable functions when a workflow node has csv_table_uuids:
  - query_csv_table     : natural language OR structured filter queries
  - aggregate_csv_table : COUNT/SUM/AVG/MAX/MIN with optional GROUP BY

The query_csv_table tool accepts a plain-English `query` parameter.
The tool internally maps the user's intent to the correct columns using an
LLM call — so "weight it can lift" correctly maps to
"Lifting Capacity - Unrestricted" even when the CSV has a "Weight" column.
This works for ANY CSV file — the column mapping is done dynamically at
runtime using the actual column names from the uploaded file.
"""

import json
from typing import Any, Dict, List, Optional

from loguru import logger

from api.db import db_client


# ---------------------------------------------------------------------------
# Helper: column hint for tool descriptions
# ---------------------------------------------------------------------------

def _build_column_hint(column_schema: List[Dict]) -> str:
    if not column_schema:
        return ""
    parts = [f"{c['name']} ({c.get('type', 'text')})" for c in column_schema[:25]]
    hint = ", ".join(parts)
    if len(column_schema) > 25:
        hint += f" ... (+{len(column_schema) - 25} more)"
    return hint


# ---------------------------------------------------------------------------
# Natural language → SQL plan via LLM
# Issue 11 fix: improved system prompt — universal intent mapping, never
#               returns empty plan for "highest/lowest/heaviest" queries.
# Issue 12 fix: smart fallback when plan is empty — infer sort from query.
# ---------------------------------------------------------------------------

def _infer_fallback_plan(query: str, column_schema: List[Dict]) -> Dict[str, Any]:
    """When the LLM returns an empty plan, infer a best-effort sort plan
    from the query text + column names. Handles 'highest/lowest/heaviest/
    lightest/most/least/best/worst' patterns universally for any CSV.

    Issue 12: previously returned {} → full unsorted scan → wrong row 1.
    Now returns a sort plan based on semantic keyword matching.
    """
    q = query.lower()
    col_names = [c["name"] for c in column_schema]
    number_cols = [c["name"] for c in column_schema if c.get("type") == "number"]

    # Keyword → (search terms in col name, sort direction)
    INTENT_MAP = [
        # height / reach
        (["high", "tall", "reach", "height", "elevation"], ["working height", "height", "reach"], "desc"),
        # weight / heavy
        (["heavy", "heaviest", "weight", "heavi"], ["weight", "mass"], "desc"),
        # lightest
        (["light", "lightest"], ["weight", "mass"], "asc"),
        # capacity / load / lift
        (["capac", "load", "lift", "carry"], ["capacity", "load", "lifting"], "desc"),
        # width / narrow
        (["wide", "width", "widest"], ["width"], "desc"),
        (["narrow", "narrowest"], ["width"], "asc"),
        # price / cost / cheap / expensive
        (["cheap", "cheapest", "low price", "lowest price"], ["price", "cost", "rate"], "asc"),
        (["expens", "most expens", "highest price"], ["price", "cost", "rate"], "desc"),
        # radius / outreach
        (["outreach", "radius", "reach out"], ["radius", "outreach", "reach"], "desc"),
    ]

    for query_keywords, col_keywords, direction in INTENT_MAP:
        # Check if any query keyword is in the question
        if not any(kw in q for kw in query_keywords):
            continue
        # Find the best matching column
        for col_kw in col_keywords:
            for col in col_names:
                if col_kw in col.lower():
                    logger.debug(
                        f"Fallback plan: query='{query[:50]}' → "
                        f"order_by='{col}' {direction}"
                    )
                    return {"order_by": col, "order_dir": direction, "limit": 1}

    # No match found — return first number column sorted desc as last resort
    if number_cols:
        return {"order_by": number_cols[0], "order_dir": "desc", "limit": 20}

    return {}


import re as _re

def _enforce_approximate_filters(
    query: str,
    plan: Dict[str, Any],
    column_schema: List[Dict],
) -> Dict[str, Any]:
    """Post-process LLM plan to inject filters the LLM missed.

    Handles two cases reliably via regex — no LLM needed:
    1. Approximate numeric values: 'about 13.7m', 'around 20', 'approximately 400kg'
       → adds gte/lte range filter (±15%) on the most relevant number column
    2. Exact numeric values WITH a comparison operator that LLM dropped
    Also ensures the plan has limit > 1 when a range filter is added
    (so multiple matches are returned, not just 1).
    """
    import re

    q = query.lower()
    col_names = [c["name"] for c in column_schema]
    number_cols = [c["name"] for c in column_schema if c.get("type") == "number"]

    # Approximate keywords that signal a range query
    approx_keywords = ["about", "around", "approximately", "roughly", "close to", "near"]
    is_approximate = any(kw in q for kw in approx_keywords)

    if not is_approximate:
        return plan

    # Extract the numeric value from the query (e.g. "about 13.7m" → 13.7)
    num_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:m|kg|ft|cm|mm|lb|lbs|t)?", q)
    if not num_match:
        return plan

    value = float(num_match.group(1))
    margin = 0.15  # ±15%
    low  = round(value * (1 - margin), 2)
    high = round(value * (1 + margin), 2)

    # Find the best matching column from plan's order_by or by keyword search
    target_col = plan.get("order_by")

    # Verify target_col exists in schema
    if target_col and target_col not in col_names:
        target_col = None

    # If no order_by or it doesn't match, find best numeric column by keyword
    if not target_col:
        q_words = q.split()
        for col in number_cols:
            col_lower = col.lower()
            if any(w in col_lower for w in ["height", "reach", "weight", "capacity", "width", "price", "cost", "fee"]):
                # Check if any of those words appear in the query too
                for w in ["height", "reach", "weight", "capacity", "width", "price", "cost", "fee"]:
                    if w in q and w in col_lower:
                        target_col = col
                        break
            if target_col:
                break

    if not target_col and number_cols:
        # Last resort: use first number column
        target_col = number_cols[0]

    if not target_col:
        return plan

    # Inject the range filter into the plan
    if "filters_op" not in plan:
        plan["filters_op"] = {}

    # Only add if not already set
    if target_col not in plan.get("filters_op", {}):
        plan["filters_op"][target_col] = {"gte": low, "lte": high}
        logger.debug(
            f"Injected approximate filter: '{target_col}' gte={low} lte={high} "
            f"(from '{query[:50]}')"
        )

    # Remove order_by when we have a range — we want ALL matches, not just top 1
    if plan.get("order_by") == target_col:
        plan.pop("order_by", None)
        plan.pop("order_dir", None)

    # Increase limit to return multiple matches
    if plan.get("limit", 20) <= 1:
        plan["limit"] = 10

    return plan


def _fix_brand_in_model_filter(
    plan: Dict[str, Any],
    column_schema: List[Dict],
) -> Dict[str, Any]:
    """Fix query plans where the LLM puts 'Brand Model' in the Model filter.

    E.g. filters={'Model': 'JCB 540-170'} →
         filters={'Brand': 'JCB', 'Model': '540-170'}

    The LLM frequently merges brand + model into the Model filter when the
    user says 'JCB 540-170' or 'JLG 450AJ'. This splits them correctly.
    """
    import re as _re

    filters = plan.get("filters", {})
    if not filters:
        return plan

    model_val = filters.get("Model", "")
    if not model_val or not isinstance(model_val, str):
        return plan

    # Known brands — extracted from column schema brands or hardcoded common ones
    # This list is checked as a prefix of the model value
    known_brands = [
        "JLG", "Genie", "Skyjack", "Dingli", "JCB", "Niftylift", "MEC",
        "Hinowa", "Snorkel", "Teupen", "UpRight", "Omme", "Holland Lift",
        "Liebherr", "Grove", "Tadano", "Terex", "Sany", "Manitowoc",
        "Manitou", "Merlo", "Caterpillar", "CAT", "Haulotte", "Ruthmann",
        "Versalift", "Time", "Platform Basket",
    ]

    model_val_stripped = model_val.strip()

    for brand in known_brands:
        # Check if model value starts with "Brand " (brand + space)
        if model_val_stripped.lower().startswith(brand.lower() + " "):
            actual_model = model_val_stripped[len(brand):].strip()
            if actual_model:
                # Split: set Brand filter + fix Model filter
                if "Brand" not in filters:
                    filters["Brand"] = brand
                filters["Model"] = actual_model
                plan["filters"] = filters
                logger.debug(
                    f"Split model filter: '{model_val}' → Brand='{brand}', Model='{actual_model}'"
                )
                break

    return plan


async def _resolve_nl_query_to_plan(
    query: str,
    column_schema: List[Dict],
    *,
    llm_api_key: Optional[str] = None,
    llm_model: Optional[str] = None,
    llm_base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Use LLM to map a natural language query to a structured query plan.

    Given the actual column names of the CSV, the LLM figures out:
    - Which columns the user is asking about (handles synonyms / intent)
    - Any filter conditions
    - Sort order and limit

    Returns a dict compatible with query_csv_table_rows parameters.
    Falls back to _infer_fallback_plan() if LLM is unavailable or returns {}.
    """
    if not column_schema:
        return {}

    col_names_json = json.dumps([c["name"] for c in column_schema], ensure_ascii=False)

    # Issue 11: completely rewritten system prompt.
    # - Never hardcodes column names — always uses the provided list.
    # - Gives intent-based reasoning rules that work for ANY CSV.
    # - Explicitly handles ambiguous multi-column cases (3 height cols etc).
    # - Never returns {} for superlative queries (highest/lowest/heaviest).
    system_prompt = (
        "You are a CSV data query planner. Your job is to translate a user's natural "
        "language question into a structured JSON query plan.\n\n"
        "OUTPUT: Only valid JSON. No explanation. No markdown.\n\n"
        "JSON SCHEMA:\n"
        '{\n'
        '  "columns": [],           // list of column names to return; [] means return all\n'
        '  "filters": {},           // exact match: {"ColName": "value"}\n'
        '  "filters_op": {},        // comparison: {"ColName": {"gt|lt|gte|lte|contains|in": value}}\n'
        '  "order_by": "ColName",   // EXACT column name from the provided list\n'
        '  "order_dir": "asc|desc", // asc = smallest first, desc = largest first\n'
        '  "limit": 20              // number of rows to return\n'
        '}\n\n'
        "CRITICAL RULES — follow these exactly:\n"
        "1. COLUMN NAMES: You MUST use the EXACT column name from the provided list. "
        "Never invent a column name. Never abbreviate. Copy it character-for-character.\n"
        "2. AMBIGUOUS COLUMNS: When multiple columns look similar (e.g. 3 height columns), "
        "use this priority to pick the right one:\n"
        "   - 'how high / reach / maximum height / operational height' → "
        "pick the column whose name contains 'Working Height' or 'Maximum Height' or 'Reach'\n"
        "   - 'platform height / basket height' → pick column with 'Platform Height'\n"
        "   - 'transport / stowed / storage height' → pick column with 'Stowed' or 'Transport'\n"
        "   - 'machine weight / how heavy is the machine' → pick column with 'Weight' "
        "but NOT 'Lifting' or 'Capacity'\n"
        "   - 'load / capacity / how much can it lift / carry' → pick column with "
        "'Capacity' or 'Load' or 'Lifting'\n"
        "3. SUPERLATIVES: For 'highest/tallest/largest/most/maximum/best', set "
        "order_dir='desc' and limit=1. For 'lowest/smallest/least/minimum/lightest', "
        "set order_dir='asc' and limit=1.\n"
        "3a. LISTING QUERIES: For 'which models / what models / list / show me / all models "
        "that / models with / models above / models under' — set limit=20 (NOT limit=1). "
        "These are listing queries that need ALL matching results, not just the top one.\n"
        "4. NEVER return an empty plan {} for superlative questions. Always produce "
        "an order_by + order_dir for any question containing: "
        "highest, lowest, tallest, shortest, heaviest, lightest, most, least, "
        "best, worst, maximum, minimum, largest, smallest.\n"
        "5. FILTERS: For model lookups like 'what is the weight of 450AJ', set "
        "filters={'Model': '450AJ'} and return the weight column.\n"
        "6. BRAND FILTERS: 'all JLG models' → filters={'Brand': 'JLG'}, columns=[].\n"
        "7. NUMERIC COMPARISONS: 'above 20m', 'more than 400kg', 'under 3 tons' → "
        "use filters_op with gt/lt/gte/lte on the matching column.\n"
        "8. The provided column list is your ONLY source of truth for column names.\n"
        "9. APPROXIMATE VALUES: 'about X', 'around X', 'approximately X', 'close to X' → "
        "use filters_op with gte=(X*0.9) and lte=(X*1.1) to find values within 10% range. "
        "Example: 'about 13.7m' → filters_op={'Working Height...': {'gte': 12.3, 'lte': 15.1}}. "
        "ALWAYS combine with any brand/type filters mentioned.\n"
        "10. COMBINED FILTERS: Always include ALL conditions from the question. "
        "'Genie models about 13.7m' → filters={'Brand': 'Genie'} AND "
        "filters_op={'Working Height...': {'gte': 12.3, 'lte': 15.1}}. "
        "Never drop any filter condition.\n"
        "11. SPECIFIC MODEL LOOKUP: When user asks about a specific model by name "
        "(e.g. 'JCB 540-170', 'Grove RT875E', 'JLG 450AJ', 'Genie Z-45/25JRT') → "
        "ALWAYS set filters={'Model': '<exact model name>'} and optionally "
        "filters={'Brand': '<brand>'}. "
        "NEVER use order_by for specific model lookups. "
        "Return all columns for that model. limit=5.\n"
    )

    user_prompt = (
        f"Available columns:\n{col_names_json}\n\n"
        f"User question: {query}"
    )

    if not llm_api_key:
        # No LLM key — use keyword fallback
        return _infer_fallback_plan(query, column_schema)

    try:
        import httpx

        headers = {
            "Authorization": f"Bearer {llm_api_key}",
            "Content-Type": "application/json",
        }
        # Route to the correct LLM endpoint — use exactly what the system configured:
        # - llm_base_url set → user configured a custom endpoint (Azure, Groq, etc.)
        # - oss_sk_ key with no base_url → Dograh managed LLM
        # - anything else → treat as OpenAI-compatible with whatever key/url is set
        if llm_base_url:
            base  = llm_base_url.rstrip("/")
            model = llm_model or "gpt-4o-mini"
        elif llm_api_key and llm_api_key.startswith("oss_sk_"):
            base  = "https://services.dograh.com/api/v1/llm"
            model = "default"
        else:
            # User-supplied key with no base_url — assume OpenAI-compatible
            base  = "https://api.openai.com/v1"
            model = llm_model or "gpt-4o-mini"

        # response_format json_object is OpenAI-specific — skip for Dograh endpoint
        is_dograh = "dograh.com" in base
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            "temperature": 0,
            "max_tokens": 400,
        }
        if not is_dograh:
            payload["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{base}/chat/completions", json=payload, headers=headers)

        if resp.status_code == 200:
            raw_content = resp.json()["choices"][0]["message"]["content"]

            # Robustly extract JSON from the response — Dograh may wrap it
            # in markdown code blocks or add extra text around the JSON
            content = raw_content.strip()
            # Strip markdown code blocks if present
            if content.startswith("```"):
                lines = content.split("\n")
                # Remove first line (```json or ```) and last line (```)
                content = "\n".join(
                    l for l in lines
                    if not l.strip().startswith("```")
                ).strip()
            # Find JSON object in the response
            start = content.find("{")
            end   = content.rfind("}") + 1
            if start >= 0 and end > start:
                content = content[start:end]

            plan = json.loads(content)
            logger.info(f"CSV query plan for '{query[:80]}': {plan}")

            # Post-process: enforce approximate value ranges that LLM missed
            plan = _enforce_approximate_filters(query, plan, column_schema)

            # Post-process: split "Brand Model" in Model filter
            plan = _fix_brand_in_model_filter(plan, column_schema)

            # Issue 12: if LLM returned empty plan for a superlative query,
            # use keyword fallback instead of returning {} (wrong row 1).
            # IMPORTANT: only use fallback if plan has NO filters at all —
            # if plan has filters_op but no order_by, that's a valid filter query.
            has_filters = bool(plan.get("filters") or plan.get("filters_op"))
            if not has_filters and (not plan or not plan.get("order_by")):
                superlatives = [
                    "highest", "lowest", "tallest", "shortest", "heaviest",
                    "lightest", "most", "least", "maximum", "minimum",
                    "largest", "smallest", "best", "worst",
                ]
                q_lower = query.lower()
                if any(s in q_lower for s in superlatives):
                    fallback = _infer_fallback_plan(query, column_schema)
                    if fallback:
                        logger.debug(
                            f"LLM returned empty plan for superlative query — "
                            f"using fallback: {fallback}"
                        )
                        return fallback

            return plan
        else:
            logger.warning(f"LLM query plan failed ({resp.status_code}): {resp.text[:200]}")
            return _enforce_approximate_filters(query, _infer_fallback_plan(query, column_schema), column_schema)

    except Exception as e:
        logger.warning(f"CSV query plan LLM call failed: {e}")
        return _infer_fallback_plan(query, column_schema)


# ---------------------------------------------------------------------------
# Tool definitions (schemas the LLM sees)
# ---------------------------------------------------------------------------

def get_csv_query_tool(
    table_uuids: Optional[List[str]] = None,
    column_schema: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """Return the LLM-facing schema for query_csv_table."""
    n_tables = len(table_uuids) if table_uuids else 0
    table_note = (
        f" The search will look across {n_tables} attached table(s)."
        if n_tables else ""
    )
    col_hint = _build_column_hint(column_schema or [])
    col_note = f" Available columns: {col_hint}." if col_hint else ""

    description = (
        "Query structured data rows from a CSV table using natural language. "
        "Use this tool for questions like 'list all JLG models', "
        "'what weight can the 600AJ lift?', "
        "'show machines with working height above 20m', "
        "'find electric scissor lifts with capacity above 400kg', "
        "'which machine is the heaviest?', 'which model reaches the highest?'. "
        "Just describe what you want in plain English — the tool figures out "
        "which columns to use automatically."
        f"{table_note}{col_note}"
    )

    return {
        "type": "function",
        "function": {
            "name": "query_csv_table",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Natural language question or description of what data you want. "
                            "Examples: 'JLG models with working height above 20m', "
                            "'what is the lifting capacity of the 600AJ', "
                            "'which machine reaches the highest working height', "
                            "'all electric scissor lifts'. "
                            "The tool maps your question to the right columns automatically."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of rows to return (default 20, max 100).",
                    },
                },
                "required": ["query"],
            },
        },
    }


def get_csv_aggregate_tool(
    table_uuids: Optional[List[str]] = None,
    column_schema: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """Return the LLM-facing schema for aggregate_csv_table."""
    n_tables = len(table_uuids) if table_uuids else 0
    table_note = (
        f" Operates across {n_tables} attached table(s)."
        if n_tables else ""
    )
    col_hint = _build_column_hint(column_schema or [])
    col_note = f" Available columns: {col_hint}." if col_hint else ""

    description = (
        "Perform aggregation queries on CSV table data: COUNT, SUM, AVG, MAX, MIN "
        "with optional GROUP BY. Use for analytical questions like "
        "'how many models per brand', 'average working height by category', "
        "'maximum lifting capacity', 'top 5 heaviest machines'."
        f"{table_note}{col_note}"
    )

    return {
        "type": "function",
        "function": {
            "name": "aggregate_csv_table",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "aggregate_function": {
                        "type": "string",
                        "enum": ["count", "sum", "avg", "max", "min"],
                        "description": "Aggregation function to apply.",
                    },
                    "aggregate_field": {
                        "type": "string",
                        "description": (
                            "Column to aggregate (required for sum/avg/max/min; "
                            "optional for count). Use the exact column name."
                        ),
                    },
                    "group_by": {
                        "type": "string",
                        "description": "Column name to group results by (optional).",
                    },
                    "filters": {
                        "type": "object",
                        "description": (
                            "Optional exact-match filters. "
                            "Example: {\"Energy Source\": \"Diesel\"}"
                        ),
                        "additionalProperties": True,
                    },
                    "order_by": {
                        "type": "string",
                        "enum": ["asc", "desc"],
                        "description": "Sort direction (default 'desc').",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum result rows (default 20).",
                    },
                },
                "required": ["aggregate_function"],
            },
        },
    }


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

async def query_csv_table(
    *,
    organization_id: int,
    table_uuids: List[str],
    query: str,
    limit: int = 20,
    llm_api_key: Optional[str] = None,
    llm_model: Optional[str] = None,
    llm_base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a natural language query against csv_table_rows."""
    col_schema = await get_column_schema_for_tables(organization_id, table_uuids)

    plan = await _resolve_nl_query_to_plan(
        query,
        col_schema,
        llm_api_key=llm_api_key,
        llm_model=llm_model,
        llm_base_url=llm_base_url,
    )

    # Merge plan filters
    filters = {}
    if plan.get("filters"):
        filters.update(plan["filters"])
    if plan.get("filters_op"):
        filters.update(plan["filters_op"])

    result = await db_client.query_csv_table_rows(
        organization_id=organization_id,
        table_uuids=table_uuids,
        filters=filters if filters else None,
        columns=plan.get("columns") or None,
        order_by=plan.get("order_by"),
        order_dir=plan.get("order_dir", "asc"),
        limit=min(plan.get("limit", limit), 100),
        known_columns=[c["name"] for c in col_schema],  # Issue 1: SQL injection guard
    )

    result["query"] = query
    result["mapped_plan"] = plan
    return result


async def aggregate_csv_table(
    *,
    organization_id: int,
    table_uuids: List[str],
    aggregate_function: str,
    aggregate_field: Optional[str] = None,
    group_by: Optional[str] = None,
    filters: Optional[Dict[str, Any]] = None,
    order_by: str = "desc",
    limit: int = 20,
) -> Dict[str, Any]:
    """Execute an aggregation query against csv_table_rows."""
    col_schema = await get_column_schema_for_tables(organization_id, table_uuids)
    known_columns = [c["name"] for c in col_schema]

    return await db_client.aggregate_csv_table_rows(
        organization_id=organization_id,
        table_uuids=table_uuids,
        aggregate_function=aggregate_function,
        aggregate_field=aggregate_field,
        group_by=group_by,
        filters=filters,
        order_by=order_by,
        limit=limit,
        known_columns=known_columns,  # Issue 1: SQL injection guard
    )


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
        return merged
    except Exception:
        return []
