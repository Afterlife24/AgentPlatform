"""Knowledge Base retrieval tool for workflow execution.

Full RAG pipeline:
  query → router → query expansion → hybrid search (dense + BM25, k=20)
        → RRF merge → rerank → top-5 returned to LLM

Implements OpenTelemetry tracing for observability in Langfuse.
"""

import asyncio
import json
from typing import Any, Dict, List, Optional

from loguru import logger
from opentelemetry import trace

from api.db import db_client
from api.services.gen_ai import build_embedding_service
from api.services.gen_ai.reranking import build_reranking_service
from api.services.pipecat.tracing_config import ensure_tracing
from api.services.workflow.tools.query_expansion import expand_query
from api.services.workflow.tools.rag_router import RAGRoute, route_query

# Number of candidates fetched per query variant before reranking.
# Caller passes limit=20; we use that as our fetch k.
_DEFAULT_FETCH_K = 20

# Final chunks returned to the LLM after reranking.
# Reranker is disabled for Dograh proxy models (returns empty responses).
# With RRF-only ordering, returning more chunks gives the LLM enough context
# to find the answer even when the target chunk ranks 6-12th by RRF.
_FINAL_TOP_N = 12


async def retrieve_from_knowledge_base(
    query: str,
    organization_id: int,
    document_uuids: Optional[List[str]] = None,
    limit: int = _DEFAULT_FETCH_K,
    embeddings_api_key: Optional[str] = None,
    embeddings_model: Optional[str] = None,
    embeddings_base_url: Optional[str] = None,
    embeddings_provider: Optional[str] = None,
    embeddings_endpoint: Optional[str] = None,
    embeddings_api_version: Optional[str] = None,
    correlation_id: Optional[str] = None,
    # LLM config — reused for query expansion, reranking, and routing
    llm_api_key: Optional[str] = None,
    llm_model: Optional[str] = None,
    llm_base_url: Optional[str] = None,
    tracing_context=None,
    query_expansion_enabled: bool = True,
    reranking_enabled: bool = True,
    top_n_chunks: int = _FINAL_TOP_N,
) -> Dict[str, Any]:
    """Retrieve relevant information from the knowledge base.

    Full RAG pipeline:
      1. Deterministic router — pick semantic / metadata_filter / aggregation / out_of_scope
      2. Query expansion    — generate up to 2 alternative phrasings
      3. Hybrid search      — dense (pgvector cosine) + BM25 (tsvector) per query variant
      4. RRF merge          — fuse all candidate lists (k=60)
      5. LLM reranker       — cross-encoder rescoring, keep top-5
      6. Return to LLM      — tight context window, high precision

    Args:
        query: The search query
        organization_id: Organization ID for scoping
        document_uuids: Optional document UUID filter
        limit: Fetch k per query variant (default 20). Reranker trims to top-5.
        embeddings_*: Embedding service configuration
        correlation_id: MPS billing correlation id (v2 orgs)
        llm_api_key: LLM key for query expansion, reranking, and routing
        llm_model: LLM model override (default gpt-4o-mini)
        llm_base_url: LLM base URL override
        tracing_context: OpenTelemetry context for Langfuse tracing

    Returns:
        Dict with ``chunks``, ``query``, ``total_results``, and optionally ``route``.
    """
    if ensure_tracing():
        try:
            parent_context = tracing_context
            tracer = trace.get_tracer("pipecat")
        except Exception as e:
            logger.debug(f"Failed to setup tracing context: {e}")
            return await _perform_retrieval(
                query, organization_id, document_uuids, limit,
                embeddings_api_key, embeddings_model, embeddings_base_url,
                embeddings_provider, embeddings_endpoint, embeddings_api_version,
                correlation_id, llm_api_key, llm_model, llm_base_url,
                query_expansion_enabled=query_expansion_enabled,
                reranking_enabled=reranking_enabled,
                top_n_chunks=top_n_chunks,
            )

        if parent_context:
            with tracer.start_as_current_span(
                "knowledge_base_retrieval", context=parent_context
            ) as span:
                try:
                    span.set_attribute("langfuse.trace.public", True)
                    span.set_attribute("gen_ai.operation.name", "knowledge_base_retrieval")
                    span.set_attribute("retrieval.query", query)
                    span.set_attribute("retrieval.limit", limit)
                    span.set_attribute("retrieval.organization_id", organization_id)
                    if document_uuids:
                        span.set_attribute("retrieval.document_count", len(document_uuids))
                        span.set_attribute("retrieval.document_uuids", json.dumps(document_uuids))

                    result = await _perform_retrieval(
                        query, organization_id, document_uuids, limit,
                        embeddings_api_key, embeddings_model, embeddings_base_url,
                        embeddings_provider, embeddings_endpoint, embeddings_api_version,
                        correlation_id, llm_api_key, llm_model, llm_base_url,
                        query_expansion_enabled=query_expansion_enabled,
                        reranking_enabled=reranking_enabled,
                        top_n_chunks=top_n_chunks,
                    )

                    span.set_attribute("retrieval.results_count", result["total_results"])
                    span.set_attribute("retrieval.route", result.get("route", "unknown"))

                    if result.get("error"):
                        span.set_attribute("retrieval.error", result["error"])
                        span.set_status(trace.Status(trace.StatusCode.ERROR, result["error"]))
                    else:
                        if result["chunks"]:
                            scores = [c.get("rerank_score") or c.get("similarity") or 0 for c in result["chunks"]]
                            span.set_attribute("retrieval.avg_score", round(sum(scores) / len(scores), 4))
                            span.set_attribute("retrieval.max_score", max(scores))
                        filenames = list(set(c.get("filename", "") for c in result["chunks"]))
                        span.set_attribute("retrieval.source_files", json.dumps(filenames))
                        output_data = {
                            "query": query,
                            "route": result.get("route"),
                            "chunks_retrieved": len(result["chunks"]),
                            "chunks": [
                                {
                                    "text": c["text"][:200] + "..." if len(c["text"]) > 200 else c["text"],
                                    "filename": c.get("filename"),
                                    "rerank_score": c.get("rerank_score"),
                                    "similarity": c.get("similarity"),
                                }
                                for c in result["chunks"]
                            ],
                        }
                        span.set_attribute("output", json.dumps(output_data))
                    return result

                except Exception as e:
                    logger.error(f"Error in traced retrieval: {e}")
                    span.record_exception(e)
                    span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
                    raise
        else:
            logger.debug("No parent context available for knowledge base retrieval tracing")
            return await _perform_retrieval(
                query, organization_id, document_uuids, limit,
                embeddings_api_key, embeddings_model, embeddings_base_url,
                embeddings_provider, embeddings_endpoint, embeddings_api_version,
                correlation_id, llm_api_key, llm_model, llm_base_url,
                query_expansion_enabled=query_expansion_enabled,
                reranking_enabled=reranking_enabled,
                top_n_chunks=top_n_chunks,
            )
    else:
        return await _perform_retrieval(
            query, organization_id, document_uuids, limit,
            embeddings_api_key, embeddings_model, embeddings_base_url,
            embeddings_provider, embeddings_endpoint, embeddings_api_version,
            correlation_id, llm_api_key, llm_model, llm_base_url,
            query_expansion_enabled=query_expansion_enabled,
            reranking_enabled=reranking_enabled,
            top_n_chunks=top_n_chunks,
        )


async def _perform_retrieval(
    query: str,
    organization_id: int,
    document_uuids: Optional[List[str]],
    limit: int,
    embeddings_api_key: Optional[str] = None,
    embeddings_model: Optional[str] = None,
    embeddings_base_url: Optional[str] = None,
    embeddings_provider: Optional[str] = None,
    embeddings_endpoint: Optional[str] = None,
    embeddings_api_version: Optional[str] = None,
    correlation_id: Optional[str] = None,
    llm_api_key: Optional[str] = None,
    llm_model: Optional[str] = None,
    llm_base_url: Optional[str] = None,
    workflow_run_id: Optional[int] = None,
    query_expansion_enabled: bool = True,
    reranking_enabled: bool = True,
    top_n_chunks: int = _FINAL_TOP_N,
) -> Dict[str, Any]:
    """Full RAG pipeline with per-step latency logging.

    Each step's wall-clock time is recorded and logged as a single structured
    line at the end so you can grep for "RAG LATENCY" and see exactly where
    time is spent across router / expansion / embedding / search / rerank.
    """
    import time as _time

    t_total_start = _time.perf_counter()
    _lat: Dict[str, float] = {}   # step → ms

    def _ms(start: float) -> float:
        return round((_time.perf_counter() - start) * 1000, 1)

    try:
        chunks: List[Dict[str, Any]] = []

        # ------------------------------------------------------------------
        # 0. Handle full_document mode — bypass pipeline entirely
        # ------------------------------------------------------------------
        if document_uuids:
            full_text_docs = await db_client.get_full_text_documents(
                organization_id=organization_id,
                document_uuids=document_uuids,
            )
            for doc in full_text_docs:
                if doc.full_text:
                    chunks.append({
                        "text": doc.full_text,
                        "filename": doc.filename,
                        "similarity": 1.0,
                        "chunk_index": 0,
                        "rerank_score": 1.0,
                    })
            full_doc_uuids = {doc.document_uuid for doc in full_text_docs}
            chunked_uuids = [u for u in document_uuids if u not in full_doc_uuids]
        else:
            chunked_uuids = document_uuids

        # Nothing left to search with vector/BM25
        if chunked_uuids is not None and len(chunked_uuids) == 0:
            return {"chunks": chunks, "query": query, "total_results": len(chunks), "route": "full_document"}

        if not embeddings_api_key:
            raise ValueError(
                "Embeddings API key not configured. Please set your API key in "
                "Model Configurations > Embedding."
            )

        # ------------------------------------------------------------------
        # 1. Deterministic router
        # ------------------------------------------------------------------
        t0 = _time.perf_counter()
        rag_route = await route_query(
            query=query,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            llm_base_url=llm_base_url,
        )
        _lat["router_ms"] = _ms(t0)
        logger.info("RAG route for query '{}': {}", query[:60], rag_route)

        # Out-of-scope — return empty immediately, no retrieval needed
        if rag_route == RAGRoute.OUT_OF_SCOPE:
            logger.info("Query routed as out_of_scope — skipping retrieval")
            return {
                "chunks": [],
                "query": query,
                "total_results": 0,
                "route": RAGRoute.OUT_OF_SCOPE.value,
            }

        # ------------------------------------------------------------------
        # 2. Query expansion (semantic route only — filters don't benefit)
        # ------------------------------------------------------------------
        t0 = _time.perf_counter()
        if rag_route == RAGRoute.SEMANTIC and query_expansion_enabled:
            queries = await expand_query(
                query=query,
                llm_api_key=llm_api_key,
                llm_model=llm_model,
                llm_base_url=llm_base_url,
            )
        else:
            queries = [query]
            if rag_route == RAGRoute.SEMANTIC:
                logger.debug("Query expansion disabled for this workflow")
        _lat["expansion_ms"] = _ms(t0)

        # ------------------------------------------------------------------
        # 3. Build embedding service
        # ------------------------------------------------------------------
        t0 = _time.perf_counter()
        embedding_service = await build_embedding_service(
            db_client=db_client,
            provider=embeddings_provider,
            api_key=embeddings_api_key,
            model=embeddings_model,
            base_url=embeddings_base_url,
            endpoint=embeddings_endpoint,
            api_version=embeddings_api_version,
            correlation_id=correlation_id,
        )
        _lat["embed_init_ms"] = _ms(t0)

        # ------------------------------------------------------------------
        # 4. Hybrid search × N query variants in parallel
        # ------------------------------------------------------------------
        fetch_k = limit

        async def _hybrid_for_query(q: str) -> List[dict]:
            try:
                q_embedding = await embedding_service.embed_query(q)
                return await db_client.hybrid_search_chunks(
                    query_embedding=q_embedding,
                    query=q,
                    organization_id=organization_id,
                    limit=fetch_k,
                    document_uuids=chunked_uuids if chunked_uuids else None,
                    embedding_model=embedding_service.get_model_id(),
                )
            except Exception as exc:
                logger.warning("Hybrid search failed for variant '{}': {}", q[:40], exc)
                try:
                    q_embedding = await embedding_service.embed_query(q)
                    return await db_client.search_similar_chunks(
                        query_embedding=q_embedding,
                        organization_id=organization_id,
                        limit=fetch_k,
                        document_uuids=chunked_uuids if chunked_uuids else None,
                        embedding_model=embedding_service.get_model_id(),
                    )
                except Exception as exc2:
                    logger.error("Dense fallback also failed for '{}': {}", q[:40], exc2)
                    return []

        t0 = _time.perf_counter()
        per_query_results = await asyncio.gather(*[_hybrid_for_query(q) for q in queries])
        _lat["search_ms"] = _ms(t0)

        # ------------------------------------------------------------------
        # 5. Cross-query RRF merge
        # ------------------------------------------------------------------
        t0 = _time.perf_counter()
        rrf_k = 60
        rrf_scores: Dict[int, float] = {}
        chunk_data: Dict[int, dict] = {}

        for variant_results in per_query_results:
            for rank, row in enumerate(variant_results, start=1):
                cid = row.get("id")
                if cid is None:
                    continue
                rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (rrf_k + rank)
                if cid not in chunk_data:
                    chunk_data[cid] = row

        sorted_ids = sorted(rrf_scores, key=lambda c: rrf_scores[c], reverse=True)
        merged_candidates = []
        for cid in sorted_ids[:fetch_k]:
            entry = dict(chunk_data[cid])
            entry["rrf_score"] = round(rrf_scores[cid], 6)
            merged_candidates.append(entry)
        _lat["rrf_ms"] = _ms(t0)

        logger.info(
            "Cross-query RRF: {} variants × {} results → {} unique candidates",
            len(queries),
            fetch_k,
            len(merged_candidates),
        )

        # ------------------------------------------------------------------
        # 6. LLM reranker
        # ------------------------------------------------------------------
        # Normalise to the shape the reranker expects
        for c in merged_candidates:
            if "text" not in c:
                c["text"] = c.get("contextualized_text") or c.get("chunk_text") or ""

        reranker = (
            build_reranking_service(
                api_key=llm_api_key,
                model=llm_model,
                base_url=llm_base_url,
            )
            if reranking_enabled
            else None
        )

        t0 = _time.perf_counter()
        if reranker:
            try:
                reranked = await reranker.rerank(
                    query=query,
                    chunks=merged_candidates,
                    top_n=top_n_chunks,
                )
            except Exception as exc:
                logger.warning("Reranking failed, using RRF order: {}", exc)
                reranked = merged_candidates[:top_n_chunks]
        else:
            if not reranking_enabled:
                logger.debug("Reranking disabled for this workflow")
            reranked = merged_candidates[:top_n_chunks]
        _lat["rerank_ms"] = _ms(t0)

        # ------------------------------------------------------------------
        # 7. Normalise output shape for the LLM
        # ------------------------------------------------------------------
        for result in reranked:
            # Prefer contextualized_text (has model name + clean summary)
            # over raw chunk_text (garbled JSON with spaces).
            text = (
                result.get("contextualized_text")
                or result.get("text")
                or result.get("chunk_text")
                or ""
            )
            chunk_info = {
                "text": text,
                "filename": result.get("filename"),
                "similarity": round(result.get("similarity") or 0, 4),
                "chunk_index": result.get("chunk_index"),
                "rerank_score": result.get("rerank_score"),
                "rrf_score": result.get("rrf_score"),
            }
            chunks.append(chunk_info)

        logger.info(
            "RAG pipeline complete: query='{}', route={}, expanded={}, "
            "candidates={}, final={}",
            query[:60],
            rag_route.value,
            len(queries),
            len(merged_candidates),
            len(chunks),
        )

        # ------------------------------------------------------------------
        # Structured latency breakdown — grep for "RAG LATENCY" to see it
        # ------------------------------------------------------------------
        _lat["total_ms"] = _ms(t_total_start)
        logger.info(
            "RAG LATENCY | query='{}' | total={}ms | "
            "router={}ms | expansion={}ms | search={}ms | rrf={}ms | rerank={}ms | "
            "route={} | variants={} | candidates={} | final={}",
            query[:50],
            _lat["total_ms"],
            _lat.get("router_ms", 0),
            _lat.get("expansion_ms", 0),
            _lat.get("search_ms", 0),
            _lat.get("rrf_ms", 0),
            _lat.get("rerank_ms", 0),
            rag_route.value,
            len(queries),
            len(merged_candidates),
            len(chunks),
        )

        # Fire-and-forget feedback log — does not block the response
        asyncio.create_task(
            _log_retrieval(
                organization_id=organization_id,
                workflow_run_id=workflow_run_id,
                query=query,
                expanded_queries=queries,
                route=rag_route.value,
                chunks=chunks,
                candidates_fetched=len(merged_candidates),
            )
        )

        return {
            "chunks": chunks,
            "query": query,
            "total_results": len(chunks),
            "route": rag_route.value,
        }

    except Exception as e:
        logger.error(f"Error retrieving from knowledge base: {e}")
        return {
            "error": str(e),
            "chunks": [],
            "query": query,
            "total_results": 0,
        }


async def _log_retrieval(
    *,
    organization_id: int,
    workflow_run_id: Optional[int],
    query: str,
    expanded_queries: List[str],
    route: str,
    chunks: List[Dict[str, Any]],
    candidates_fetched: int,
) -> None:
    """Persist a retrieval log row for the feedback loop.

    Called fire-and-forget — any exception is swallowed so it never
    affects the live pipeline.
    """
    try:
        from api.db.models import RAGRetrievalLogModel

        scores = [c.get("rerank_score") for c in chunks if c.get("rerank_score") is not None]
        avg_score = round(sum(scores) / len(scores), 4) if scores else None

        chunk_summary = [
            {
                "filename": c.get("filename"),
                "rerank_score": c.get("rerank_score"),
                "rrf_score": c.get("rrf_score"),
                "chunk_index": c.get("chunk_index"),
            }
            for c in chunks
        ]

        async with db_client.async_session() as session:
            log = RAGRetrievalLogModel(
                organization_id=organization_id,
                workflow_run_id=workflow_run_id,
                query=query,
                expanded_queries=expanded_queries,
                route=route,
                retrieved_chunks=chunk_summary,
                avg_rerank_score=avg_score,
                candidates_fetched=candidates_fetched,
            )
            session.add(log)
            await session.commit()

    except Exception as exc:
        logger.debug("RAG feedback log write failed (non-critical): {}", exc)


def get_knowledge_base_tool(
    document_uuids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Get knowledge base retrieval tool definition for LLM function calling.

    Args:
        document_uuids: Optional list of document UUIDs to include in description

    Returns:
        Tool definition compatible with LLM function calling
    """
    # Build description based on whether specific documents are filtered
    if document_uuids and len(document_uuids) > 0:
        description = (
            "Retrieve relevant information from specific documents in the knowledge base. "
            "Use this tool when you need to look up facts, policies, procedures, or any information "
            "that might be stored in the available documents. The search will only look in the "
            f"documents associated with this conversation step ({len(document_uuids)} document(s) available)."
        )
    else:
        description = (
            "Retrieve relevant information from the knowledge base. "
            "Use this tool when you need to look up facts, policies, procedures, or any information "
            "that might be stored in the knowledge base documents."
        )

    return {
        "type": "function",
        "function": {
            "name": "retrieve_from_knowledge_base",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "The search query to find relevant information. "
                            "Be specific and use natural language. "
                            "Example: 'What is the refund policy for canceled orders?'"
                        ),
                    }
                },
                "required": ["query"],
            },
        },
    }
