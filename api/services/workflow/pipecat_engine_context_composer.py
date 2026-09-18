"""System prompt and function schema composition for PipecatEngine nodes.

Extracts prompt and function composition logic from PipecatEngine into
reusable functions. Defines recording response mode markers and instructions.
"""

from typing import TYPE_CHECKING, Callable, Optional

from loguru import logger

if TYPE_CHECKING:
    from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager
    from api.services.workflow.workflow_graph import Node, WorkflowGraph

from api.services.workflow.pipecat_engine_custom_tools import get_function_schema
from api.services.workflow.tools.knowledge_base import get_knowledge_base_tool
from api.services.workflow.tools.csv_table import (
    get_column_schema_for_tables,
    get_value_profile_for_tables,
    render_value_profile,
)
from api.services.workflow.tools.csv_sql_executor import get_csv_sql_tool
from api.services.workflow.tools.db_table import (
    get_db_lookup_tool,
    get_db_sql_tool,
)
from api.db import db_client

# ---------------------------------------------------------------------------
# Recording response mode markers
# ---------------------------------------------------------------------------

RECORDING_MARKER = "●"  # Play pre-recorded audio
TTS_MARKER = "▸"  # Generate dynamic TTS text

# ---------------------------------------------------------------------------
# Recording response mode system prompt instructions
# ---------------------------------------------------------------------------

RECORDING_RESPONSE_MODE_INSTRUCTIONS = """\
RESPONSE MODE INSTRUCTIONS - MANDATORY FORMAT:
Every response you generate MUST begin with excatcly one response mode indicator.
You have two modes for responding:

1. DYNAMIC SPEECH (▸): Generate text that will be converted to speech by TTS.
   Format: ▸ followed by a space and your full spoken response. Nothing else.
   Example: ▸ Hello! How can I help you today?

2. PRE-RECORDED AUDIO (●): Play a pre-recorded audio message.
   Format: ● followed by a space followed by recording_id followed by provided transcript. Nothing else.
   Example: ● rec_greeting_01 [ Provided Transcript ]

RULES:
- Your response MUST start with either ▸ or ● as the very first character.
- For ▸ (dynamic speech): Follow with a space and your response to be generated using TTS engine. Dont mix with ●
- For ● (pre-recorded audio): Follow with a space and recording_id of the audio clip with its transcript. Dont mix with ▸
- Use ● when a pre-recorded message matches the situation well.
- Use ▸ when you need to generate a dynamic, contextual response.
- *NEVER* mix modes in a single response, since we rely on the markers to decide whether to play using TTS or Pre-recorded audio."""


async def compose_system_prompt_for_node(
    *,
    node: "Node",
    workflow: "WorkflowGraph",
    format_prompt: Callable[[str], str],
    has_recordings: bool,
    organization_id: Optional[int] = None,
    csv_table_uuids: Optional[list[str]] = None,
    db_table_uuids: Optional[list[str]] = None,
) -> str:
    """Compose the full system prompt text for a workflow node.

    Combines the global prompt, node-specific prompt, the auto-injected
    value profile (when the node has CSV tables attached), the database
    schema block (when the node has DB tables attached), and (when
    recordings are enabled) the recording response mode instructions.

    Args:
        node: The workflow node to compose the prompt for.
        workflow: The full workflow graph (needed for global node prompt).
        format_prompt: Callable to render template variables in prompts.
        has_recordings: Whether any node in the workflow uses recordings.
        organization_id: Tenant scope for the value-profile / schema lookup.
        csv_table_uuids: Already-resolved csv_tables.table_uuid values for
            this node — resolution stays in the engine so it is not duplicated.
        db_table_uuids: Already-resolved database table document UUIDs or names.

    Returns:
        The composed system prompt text.
    """
    global_prompt = ""
    if workflow.global_node_id and node.add_global_prompt:
        global_node = workflow.nodes[workflow.global_node_id]
        global_prompt = format_prompt(global_node.prompt)

    formatted_node_prompt = format_prompt(node.prompt)

    parts = [p for p in (global_prompt, formatted_node_prompt) if p]

    # Facts come from the table, policy comes from the prompt.
    # Injecting the actual values stops the LLM guessing a value that does
    # not exist in the data.
    if organization_id and csv_table_uuids:
        logger.info(
            f"🧩 [Composer] compose_system_prompt_for_node | "
            f"node={node.id if hasattr(node,'id') else 'unknown'} "
            f"org={organization_id} table_uuids={csv_table_uuids} — injecting value profile"
        )
        profile = await get_value_profile_for_tables(organization_id, csv_table_uuids)
        rendered = render_value_profile(profile)
        if rendered:
            parts.append(rendered)
            logger.info(
                f"🧩 [Composer] compose_system_prompt_for_node | "
                f"value profile injected: {len(profile)} columns, {len(rendered)} chars"
            )
        else:
            logger.warning(
                f"🧩 [Composer] compose_system_prompt_for_node | "
                f"value profile resolved to empty — LLM will not see column values"
            )

    # Relational Database Schema Block + Value Profile injection
    if organization_id and db_table_uuids:
        logger.info(
            f"🧩 [Composer] compose_system_prompt_for_node | "
            f"node={node.id if hasattr(node,'id') else 'unknown'} "
            f"org={organization_id} db_table_uuids={db_table_uuids} — injecting schema block"
        )
        try:
            from api.db.db_table_client import db_table_client
            from api.services.db_tables.prompt_composer import PromptComposer

            tables_data = []
            table_ids = []
            for uid in db_table_uuids:
                tbl = await db_table_client.get_db_table_by_document_uuid(uid, organization_id)
                if not tbl:
                    tbl = await db_table_client.get_db_table_by_name(uid, organization_id)
                if tbl and tbl.state in ("loaded", "loaded_degraded"):
                    tables_data.append({
                        "table_name": tbl.table_name,
                        "row_count": tbl.row_count,
                        "confirmed_primary_key": tbl.confirmed_primary_key,
                        "suggested_primary_key": tbl.suggested_primary_key,
                        "column_schema": tbl.column_schema or [],
                        "value_profile": tbl.value_profile or [],
                    })
                    table_ids.append(tbl.id)

            views_data = []
            relationships_data = []
            if table_ids:
                rels = await db_table_client.list_relationships(organization_id, status="accepted")
                for r in rels:
                    if r.source_table_id in table_ids or r.target_table_id in table_ids:
                        relationships_data.append({
                            "source_table_name": r.source_table_name,
                            "source_column": r.source_column,
                            "target_table_name": r.target_table_name,
                            "target_column": r.target_column,
                            "cardinality": r.cardinality,
                        })
                for tid in table_ids:
                    vws = await db_table_client.list_views(tid, organization_id)
                    for v in vws:
                        views_data.append({
                            "view_name": v.view_name,
                            "rules": v.rules or [],
                        })

            if tables_data:
                schema_block = PromptComposer.compose_schema_block(
                    organization_id=organization_id,
                    tables_data=tables_data,
                    views_data=views_data,
                    relationships=relationships_data,
                )
                if schema_block:
                    parts.append(schema_block)
                    logger.info(
                        f"🧩 [Composer] Schema block injected: {len(tables_data)} tables, {len(schema_block)} chars"
                    )
        except Exception as e:
            logger.warning(f"🧩 [Composer] Failed to inject database schema block: {e}")

    if has_recordings and "RECORDING_ID:" in formatted_node_prompt:
        parts.append(RECORDING_RESPONSE_MODE_INSTRUCTIONS)

    return "\n\n".join(parts)


def _schema_function_name(schema) -> Optional[str]:
    """Best-effort function name from a composed tool schema."""
    name = getattr(schema, "name", None)
    if isinstance(name, str):
        return name
    if isinstance(schema, dict):
        if isinstance(schema.get("name"), str):
            return schema["name"]
        fn = schema.get("function")
        if isinstance(fn, dict) and isinstance(fn.get("name"), str):
            return fn["name"]
    return None


async def compose_functions_for_node(
    *,
    node: "Node",
    custom_tool_manager: Optional["CustomToolManager"],
    organization_id: Optional[int] = None,
) -> list[dict]:
    """Compose the function/tool schemas for a workflow node.

    Gathers knowledge-base tools, the CSV SQL tool, custom tools (including
    built-in categories like calculator), and transition function schemas
    into a single list.

    Args:
        node: The workflow node to compose functions for.
        custom_tool_manager: Manager for custom and built-in tools (may be None).
        organization_id: Organization ID for fetching dynamic metadata fields.

    Returns:
        A list of function schemas to register with the LLM.
    """
    functions: list[dict] = []

    # Knowledge base retrieval tool — only retrieve_from_knowledge_base.
    # filter_knowledge_base and aggregate_knowledge_base are retired: they
    # duplicate the SQL path against a weaker substrate and were already
    # forbidden by the node prompts.
    if node.document_uuids:
        rag_doc_uuids = []
        csv_table_uuids_from_docs = []
        db_table_uuids_from_docs = []
        if organization_id:
            try:
                for doc_uuid in node.document_uuids:
                    rows = await db_client.execute_raw_query(
                        "SELECT retrieval_mode, docling_metadata FROM knowledge_base_documents "
                        "WHERE document_uuid = :uuid",
                        {"uuid": doc_uuid}
                    )
                    if rows and rows[0].get("retrieval_mode") == "table":
                        meta = rows[0].get("docling_metadata") or {}
                        t_uuid = meta.get("table_uuid")
                        if t_uuid:
                            csv_table_uuids_from_docs.append(t_uuid)
                    elif rows and rows[0].get("retrieval_mode") == "database":
                        db_table_uuids_from_docs.append(doc_uuid)
                    else:
                        rag_doc_uuids.append(doc_uuid)
            except Exception:
                rag_doc_uuids = list(node.document_uuids)
        else:
            rag_doc_uuids = list(node.document_uuids)

        if rag_doc_uuids:
            kb_tool_def = get_knowledge_base_tool(rag_doc_uuids)
            kb_schema = get_function_schema(
                kb_tool_def["function"]["name"],
                kb_tool_def["function"]["description"],
                properties=kb_tool_def["function"]["parameters"].get("properties", {}),
                required=kb_tool_def["function"]["parameters"].get("required", []),
            )
            functions.append(kb_schema)

        # Table-mode documents — register execute_csv_sql
        if csv_table_uuids_from_docs:
            col_schema_from_docs = None
            if organization_id:
                try:
                    col_schema_from_docs = await get_column_schema_for_tables(
                        organization_id, csv_table_uuids_from_docs
                    )
                except Exception:
                    pass

            csv_sql_def = get_csv_sql_tool(csv_table_uuids_from_docs, col_schema_from_docs)
            functions.append(get_function_schema(
                csv_sql_def["function"]["name"],
                csv_sql_def["function"]["description"],
                properties=csv_sql_def["function"]["parameters"].get("properties", {}),
                required=csv_sql_def["function"]["parameters"].get("required", []),
            ))

        # Database-mode documents — register execute_db_sql and lookup_db_column_values
        if db_table_uuids_from_docs:
            allowed_relations = []
            if organization_id:
                try:
                    from api.db.db_table_client import db_table_client
                    for doc_uuid in db_table_uuids_from_docs:
                        tbl = await db_table_client.get_db_table_by_document_uuid(doc_uuid, organization_id)
                        if tbl:
                            allowed_relations.append(tbl.table_name)
                            views = await db_table_client.list_views(tbl.id, organization_id)
                            for v in views:
                                allowed_relations.append(v.view_name)
                except Exception as e:
                    logger.warning(f"Could not resolve allowed relations: {e}")

            db_sql_def = get_db_sql_tool(allowed_relations)
            functions.append(get_function_schema(
                db_sql_def["function"]["name"],
                db_sql_def["function"]["description"],
                properties=db_sql_def["function"]["parameters"].get("properties", {}),
                required=db_sql_def["function"]["parameters"].get("required", []),
            ))
            db_lookup_def = get_db_lookup_tool(allowed_relations)
            functions.append(get_function_schema(
                db_lookup_def["function"]["name"],
                db_lookup_def["function"]["description"],
                properties=db_lookup_def["function"]["parameters"].get("properties", {}),
                required=db_lookup_def["function"]["parameters"].get("required", []),
            ))

    # CSV Tables section — execute_csv_sql only
    if node.csv_table_uuids:
        resolved_csv_uuids: list[str] = []
        if organization_id:
            try:
                for doc_uuid in node.csv_table_uuids:
                    sql = """
                        SELECT retrieval_mode, docling_metadata
                        FROM knowledge_base_documents
                        WHERE document_uuid = :uuid
                          AND organization_id = :org_id
                        LIMIT 1
                    """
                    from api.db import db_client as _dbc
                    rows = await _dbc.execute_raw_query(sql, {"uuid": doc_uuid, "org_id": organization_id})
                    if rows and rows[0].get("retrieval_mode") == "table":
                        meta = rows[0].get("docling_metadata") or {}
                        t_uuid = meta.get("table_uuid")
                        if t_uuid:
                            resolved_csv_uuids.append(t_uuid)
                    else:
                        resolved_csv_uuids.append(doc_uuid)
            except Exception:
                resolved_csv_uuids = node.csv_table_uuids

        effective_uuids = resolved_csv_uuids if resolved_csv_uuids else node.csv_table_uuids

        col_schema = None
        if organization_id:
            try:
                col_schema = await get_column_schema_for_tables(
                    organization_id, effective_uuids
                )
            except Exception:
                pass

        csv_sql_def = get_csv_sql_tool(effective_uuids, col_schema)
        csv_sql_schema = get_function_schema(
            csv_sql_def["function"]["name"],
            csv_sql_def["function"]["description"],
            properties=csv_sql_def["function"]["parameters"].get("properties", {}),
            required=csv_sql_def["function"]["parameters"].get("required", []),
        )
        functions.append(csv_sql_schema)

    # Database Tables section — execute_db_sql and lookup_db_column_values
    node_db_table_uuids = getattr(node, "db_table_uuids", None)
    if node_db_table_uuids:
        allowed_relations = []
        if organization_id:
            try:
                from api.db.db_table_client import db_table_client
                for doc_uuid in node_db_table_uuids:
                    tbl = await db_table_client.get_db_table_by_document_uuid(doc_uuid, organization_id)
                    if not tbl:
                        tbl = await db_table_client.get_db_table_by_name(doc_uuid, organization_id)
                    if tbl:
                        allowed_relations.append(tbl.table_name)
                        views = await db_table_client.list_views(tbl.id, organization_id)
                        for v in views:
                            allowed_relations.append(v.view_name)
            except Exception as e:
                logger.warning(f"Could not resolve allowed relations for db_table_uuids: {e}")

        db_sql_def = get_db_sql_tool(allowed_relations)
        functions.append(get_function_schema(
            db_sql_def["function"]["name"],
            db_sql_def["function"]["description"],
            properties=db_sql_def["function"]["parameters"].get("properties", {}),
            required=db_sql_def["function"]["parameters"].get("required", []),
        ))
        db_lookup_def = get_db_lookup_tool(allowed_relations)
        functions.append(get_function_schema(
            db_lookup_def["function"]["name"],
            db_lookup_def["function"]["description"],
            properties=db_lookup_def["function"]["parameters"].get("properties", {}),
            required=db_lookup_def["function"]["parameters"].get("required", []),
        ))

    # Custom tools
    if node.tool_uuids and custom_tool_manager:
        custom_tool_schemas = await custom_tool_manager.get_tool_schemas(
            node.tool_uuids,
            mcp_tool_filters=getattr(node, "mcp_tool_filters", None),
        )
        functions.extend(custom_tool_schemas)

    # Transition function schemas
    for outgoing_edge in node.out_edges:
        function_schema = get_function_schema(
            outgoing_edge.get_function_name(), outgoing_edge.condition
        )
        functions.append(function_schema)

    # De-duplicate by function name — keep first occurrence, preserve order.
    # Both block A (table-mode docs) and block B (csv_table_uuids) can
    # append execute_csv_sql; de-duplication makes that structurally safe.
    deduped: list[dict] = []
    seen_function_names: set[str] = set()
    for schema in functions:
        fn_name = _schema_function_name(schema)
        if fn_name is not None:
            if fn_name in seen_function_names:
                continue
            seen_function_names.add(fn_name)
        deduped.append(schema)

    return deduped
