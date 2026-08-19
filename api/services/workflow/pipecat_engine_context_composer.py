"""System prompt and function schema composition for PipecatEngine nodes.

Extracts prompt and function composition logic from PipecatEngine into
reusable functions. Defines recording response mode markers and instructions.
"""

from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager
    from api.services.workflow.workflow_graph import Node, WorkflowGraph

from api.services.workflow.pipecat_engine_custom_tools import get_function_schema
from api.services.workflow.tools.knowledge_base import get_knowledge_base_tool
from api.services.workflow.tools.knowledge_base_filter import get_knowledge_base_filter_tool
from api.services.workflow.tools.knowledge_base_aggregate import get_knowledge_base_aggregate_tool
from api.services.workflow.tools.csv_table import (
    get_column_schema_for_tables,
    get_csv_aggregate_tool,
    get_csv_query_tool,
)
from api.services.workflow.tools.csv_sql_executor import get_csv_sql_tool
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


def compose_system_prompt_for_node(
    *,
    node: "Node",
    workflow: "WorkflowGraph",
    format_prompt: Callable[[str], str],
    has_recordings: bool,
) -> str:
    """Compose the full system prompt text for a workflow node.

    Combines the global prompt, node-specific prompt, and (when recordings
    are enabled anywhere in the workflow) the recording response mode
    instructions into a single string.

    Args:
        node: The workflow node to compose the prompt for.
        workflow: The full workflow graph (needed for global node prompt).
        format_prompt: Callable to render template variables in prompts.
        has_recordings: Whether any node in the workflow uses recordings.

    Returns:
        The composed system prompt text.
    """
    global_prompt = ""
    if workflow.global_node_id and node.add_global_prompt:
        global_node = workflow.nodes[workflow.global_node_id]
        global_prompt = format_prompt(global_node.prompt)

    formatted_node_prompt = format_prompt(node.prompt)

    parts = [p for p in (global_prompt, formatted_node_prompt) if p]

    if has_recordings and "RECORDING_ID:" in formatted_node_prompt:
        parts.append(RECORDING_RESPONSE_MODE_INSTRUCTIONS)

    return "\n\n".join(parts)


async def compose_functions_for_node(
    *,
    node: "Node",
    custom_tool_manager: Optional["CustomToolManager"],
    organization_id: Optional[int] = None,
) -> list[dict]:
    """Compose the function/tool schemas for a workflow node.

    Gathers knowledge-base tools, custom tools (including built-in
    categories like calculator), and transition function schemas
    into a single list.

    Args:
        node: The workflow node to compose functions for.
        custom_tool_manager: Manager for custom and built-in tools (may be None).
        organization_id: Organization ID for fetching dynamic metadata fields.

    Returns:
        A list of function schemas to register with the LLM.
    """
    functions: list[dict] = []

    # Knowledge base retrieval tool
    if node.document_uuids:
        # Separate table documents from RAG documents for schema injection
        rag_doc_uuids = []
        csv_table_uuids_from_docs = []
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

            # Metadata filter tool
            available_fields = None
            if organization_id:
                try:
                    available_fields = await db_client.get_metadata_fields_for_org(
                        organization_id=organization_id,
                        document_uuids=rag_doc_uuids,
                    )
                except Exception:
                    pass

            kb_filter_def = get_knowledge_base_filter_tool(
                rag_doc_uuids,
                available_metadata_fields=available_fields,
            )
            kb_filter_schema = get_function_schema(
                kb_filter_def["function"]["name"],
                kb_filter_def["function"]["description"],
                properties=kb_filter_def["function"]["parameters"].get("properties", {}),
                required=kb_filter_def["function"]["parameters"].get("required", []),
            )
            functions.append(kb_filter_schema)

            # Aggregation tool
            kb_agg_def = get_knowledge_base_aggregate_tool(
                rag_doc_uuids,
                available_metadata_fields=available_fields,
            )
            kb_agg_schema = get_function_schema(
                kb_agg_def["function"]["name"],
                kb_agg_def["function"]["description"],
                properties=kb_agg_def["function"]["parameters"].get("properties", {}),
                required=kb_agg_def["function"]["parameters"].get("required", []),
            )
            functions.append(kb_agg_schema)

        # Table-mode documents found via document_uuids → inject CSV tool schemas
        if csv_table_uuids_from_docs:
            col_schema_from_docs = None
            if organization_id:
                try:
                    col_schema_from_docs = await get_column_schema_for_tables(
                        organization_id, csv_table_uuids_from_docs
                    )
                except Exception:
                    pass

            csv_q_def = get_csv_query_tool(csv_table_uuids_from_docs, col_schema_from_docs)
            functions.append(get_function_schema(
                csv_q_def["function"]["name"],
                csv_q_def["function"]["description"],
                properties=csv_q_def["function"]["parameters"].get("properties", {}),
                required=csv_q_def["function"]["parameters"].get("required", []),
            ))
            csv_a_def = get_csv_aggregate_tool(csv_table_uuids_from_docs, col_schema_from_docs)
            functions.append(get_function_schema(
                csv_a_def["function"]["name"],
                csv_a_def["function"]["description"],
                properties=csv_a_def["function"]["parameters"].get("properties", {}),
                required=csv_a_def["function"]["parameters"].get("required", []),
            ))
            csv_sql_def = get_csv_sql_tool(csv_table_uuids_from_docs, col_schema_from_docs)
            functions.append(get_function_schema(
                csv_sql_def["function"]["name"],
                csv_sql_def["function"]["description"],
                properties=csv_sql_def["function"]["parameters"].get("properties", {}),
                required=csv_sql_def["function"]["parameters"].get("required", []),
            ))

    # CSV table query + aggregate tools — resolve document UUIDs to csv table UUIDs
    if node.csv_table_uuids:
        # Resolve: csv_table_uuids may hold document_uuids (KB upload path)
        # Run same split logic to get the actual csv_tables.table_uuid values
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
                        # Treat as a direct csv_tables.table_uuid
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

        csv_query_def = get_csv_query_tool(effective_uuids, col_schema)
        csv_query_schema = get_function_schema(
            csv_query_def["function"]["name"],
            csv_query_def["function"]["description"],
            properties=csv_query_def["function"]["parameters"].get("properties", {}),
            required=csv_query_def["function"]["parameters"].get("required", []),
        )
        functions.append(csv_query_schema)

        csv_agg_def = get_csv_aggregate_tool(effective_uuids, col_schema)
        csv_agg_schema = get_function_schema(
            csv_agg_def["function"]["name"],
            csv_agg_def["function"]["description"],
            properties=csv_agg_def["function"]["parameters"].get("properties", {}),
            required=csv_agg_def["function"]["parameters"].get("required", []),
        )
        functions.append(csv_agg_schema)

<<<<<<< Updated upstream
=======
        # execute_csv_sql — LLM writes raw SQL, tool executes safely
>>>>>>> Stashed changes
        csv_sql_def = get_csv_sql_tool(effective_uuids, col_schema)
        csv_sql_schema = get_function_schema(
            csv_sql_def["function"]["name"],
            csv_sql_def["function"]["description"],
            properties=csv_sql_def["function"]["parameters"].get("properties", {}),
            required=csv_sql_def["function"]["parameters"].get("required", []),
        )
        functions.append(csv_sql_schema)

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

    return functions
