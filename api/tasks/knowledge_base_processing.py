"""ARQ background task for processing knowledge base documents.

Document conversion and chunking live in the Model Proxy Service (MPS);
this task downloads the file from S3, calls MPS, then handles the embedding
and DB writes locally.
"""

import json
import os
import tempfile

from loguru import logger

from api.db import db_client
from api.db.models import KnowledgeBaseChunkModel
from api.services.gen_ai import build_embedding_service
from api.services.mps_service_key_client import mps_service_key_client
from api.services.storage import storage_fs
from api.services.workflow.tools.metadata_extraction import (
    extract_metadata_from_structured_json,
    extract_metadata_from_text,
)

MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024
EMBEDDING_BATCH_SIZE = 64
EMBEDDING_CONCURRENCY = 5  # Max parallel embedding requests


async def _embed_texts_in_batches(
    embedding_service,
    texts: list[str],
    batch_size: int = EMBEDDING_BATCH_SIZE,
    concurrency: int = EMBEDDING_CONCURRENCY,
) -> list[list[float]]:
    """Generate embeddings in bounded batches with concurrent requests."""
    import asyncio

    batches = [
        texts[start: start + batch_size]
        for start in range(0, len(texts), batch_size)
    ]

    # Pre-allocate result slots to maintain order
    results: list[list[list[float]] | None] = [None] * len(batches)
    semaphore = asyncio.Semaphore(concurrency)

    async def _process_batch(index: int, batch: list[str]):
        async with semaphore:
            logger.info(
                f"Generating embedding batch {index + 1}/{len(batches)} ({len(batch)} texts)"
            )
            results[index] = await embedding_service.embed_texts(batch)

    # Run all batches concurrently (bounded by semaphore)
    await asyncio.gather(*[
        _process_batch(i, batch) for i, batch in enumerate(batches)
    ])

    # Flatten results in order
    embeddings: list[list[float]] = []
    for result in results:
        if result is not None:
            embeddings.extend(result)
    return embeddings


async def process_knowledge_base_document(
    ctx,
    document_id: int,
    s3_key: str,
    organization_id: int,
    created_by_provider_id: str,
    max_tokens: int = 128,
    retrieval_mode: str = "chunked",
):
    """Process a knowledge base document via MPS: download, call MPS, embed, store.

    Args:
        ctx: ARQ context
        document_id: Database ID of the document
        s3_key: S3 key where the file is stored
        organization_id: Organization ID
        created_by_provider_id: Uploading user's provider ID (for OSS-mode auth to MPS)
        max_tokens: Maximum number of tokens per chunk (default: 128)
        retrieval_mode: "chunked" for vector search or "full_document" for full text
    """
    logger.info(
        f"Processing knowledge base document: document_id={document_id}, "
        f"s3_key={s3_key}, org={organization_id}, mode={retrieval_mode}"
    )

    temp_file_path = None

    try:
        await db_client.update_document_status(document_id, "processing")

        filename = s3_key.split("/")[-1]
        file_extension = os.path.splitext(filename)[1] or ".bin"

        temp_file = tempfile.NamedTemporaryFile(
            delete=False, suffix=file_extension)
        temp_file_path = temp_file.name
        temp_file.close()

        logger.info(f"Downloading file from S3: {s3_key}")
        download_success = await storage_fs.adownload_file(s3_key, temp_file_path)
        if not download_success:
            raise Exception(f"Failed to download file from S3: {s3_key}")
        if not os.path.exists(temp_file_path):
            raise FileNotFoundError(
                f"Downloaded file not found: {temp_file_path}")

        file_size = os.path.getsize(temp_file_path)
        logger.info(f"Downloaded file size: {file_size} bytes")

        if file_size > MAX_FILE_SIZE_BYTES:
            error_message = (
                f"File size ({file_size / (1024 * 1024):.1f}MB) exceeds the "
                f"maximum allowed size of {MAX_FILE_SIZE_BYTES // (1024 * 1024)}MB."
            )
            logger.warning(f"Document {document_id}: {error_message}")
            await db_client.update_document_status(
                document_id, "failed", error_message=error_message
            )
            return

        file_hash = db_client.compute_file_hash(temp_file_path)
        mime_type = db_client.get_mime_type(temp_file_path)

        document = await db_client.get_document_by_id(document_id)
        if not document:
            raise Exception(f"Document {document_id} not found")

        # Reject duplicates (same hash already ingested for this org).
        existing_doc = await db_client.get_document_by_hash(file_hash, organization_id)
        if existing_doc and existing_doc.id != document_id:
            error_message = (
                f"This file is a duplicate of '{existing_doc.filename}'. "
                f"Please delete the duplicate files and consolidate them into a "
                f"single unique file before uploading."
            )
            logger.warning(
                f"Duplicate document detected: {document_id} is duplicate of "
                f"{existing_doc.id} ({existing_doc.filename})"
            )
            await db_client.update_document_metadata(
                document_id,
                file_size_bytes=file_size,
                file_hash=file_hash,
                mime_type=mime_type,
            )
            await db_client.update_document_status(
                document_id,
                "failed",
                error_message=error_message,
                docling_metadata={
                    "duplicate_of": existing_doc.document_uuid,
                    "duplicate_filename": existing_doc.filename,
                },
            )
            return

        await db_client.update_document_metadata(
            document_id,
            file_size_bytes=file_size,
            file_hash=file_hash,
            mime_type=mime_type,
        )

        embeddings_provider = None
        embeddings_api_key = None
        embeddings_model = None
        embeddings_base_url = None
        embeddings_endpoint = None
        embeddings_api_version = None
        if retrieval_mode == "chunked" and document.created_by:
            from api.services.configuration.ai_model_configuration import (
                apply_managed_embeddings_base_url,
                get_resolved_ai_model_configuration,
            )

            resolved_config = await get_resolved_ai_model_configuration(
                user_id=document.created_by,
                organization_id=document.organization_id,
            )
            effective_config = resolved_config.effective
            if effective_config.embeddings:
                embeddings_provider = getattr(
                    effective_config.embeddings, "provider", None
                )
                embeddings_api_key = effective_config.embeddings.api_key
                embeddings_model = effective_config.embeddings.model
                embeddings_base_url = apply_managed_embeddings_base_url(
                    provider=embeddings_provider,
                    base_url=getattr(
                        effective_config.embeddings, "base_url", None),
                )
                embeddings_endpoint = getattr(
                    effective_config.embeddings, "endpoint", None
                )
                embeddings_api_version = getattr(
                    effective_config.embeddings, "api_version", None
                )
                logger.info(
                    f"Using user embeddings config: provider={embeddings_provider}, "
                    f"model={embeddings_model}"
                )

        logger.info(
            f"Delegating document processing to MPS (mode={retrieval_mode})")
        mps_response = await mps_service_key_client.process_document(
            file_path=temp_file_path,
            filename=filename,
            content_type=mime_type or "application/octet-stream",
            retrieval_mode=retrieval_mode,
            max_tokens=max_tokens,
            organization_id=organization_id,
            created_by=created_by_provider_id,
        )

        docling_metadata = mps_response.get("docling_metadata", {})

        if retrieval_mode == "full_document":
            full_text = mps_response.get("full_text") or ""
            await db_client.update_document_full_text(document_id, full_text)
            await db_client.update_document_status(
                document_id,
                "completed",
                total_chunks=0,
                docling_metadata=docling_metadata,
            )
            logger.info(
                f"Successfully processed full_document {document_id}. "
                f"Text length: {len(full_text)} chars"
            )
            return

        if not embeddings_api_key:
            error_message = (
                "API key not configured. Please set your API key in "
                "Model Configurations > Embedding to process documents."
            )
            logger.warning(f"Document {document_id}: {error_message}")
            await db_client.update_document_status(
                document_id, "failed", error_message=error_message
            )
            return

        # Ingestion runs outside any workflow run, so resolve the MPS correlation
        # id here (mint only for orgs already on v2; never create an account).
        embedding_service = await build_embedding_service(
            db_client=db_client,
            provider=embeddings_provider,
            api_key=embeddings_api_key,
            model=embeddings_model,
            base_url=embeddings_base_url,
            endpoint=embeddings_endpoint,
            api_version=embeddings_api_version,
            organization_id=organization_id,
            created_by=created_by_provider_id,
            resolve_correlation=True,
        )

        mps_chunks = mps_response.get("chunks", [])
        if not mps_chunks:
            logger.warning(f"Document {document_id}: MPS returned zero chunks")

        # Attempt to enrich chunk_metadata from structured JSON sources.
        # If the source file is JSON with a known structure (e.g., equipment catalog),
        # we extract filterable metadata fields per chunk.
        enriched_metadata_map = await _build_enriched_metadata(
            s3_key=s3_key,
            temp_file_path=temp_file_path,
            mime_type=mime_type,
            mps_chunks=mps_chunks,
        )

        chunk_records = []
        chunk_texts = []
        for chunk in mps_chunks:
            contextualized = chunk.get(
                "contextualized_text") or chunk["chunk_text"]

            # Merge MPS chunk metadata with enriched extraction
            base_metadata = chunk.get("chunk_metadata") or {}
            chunk_idx = chunk["chunk_index"]
            if chunk_idx in enriched_metadata_map:
                base_metadata = {**base_metadata, **
                                 enriched_metadata_map[chunk_idx]}
            elif not enriched_metadata_map:
                # Fallback: extract from text for unstructured docs
                text_metadata = extract_metadata_from_text(
                    contextualized, filename
                )
                if text_metadata:
                    base_metadata = {**base_metadata, **text_metadata}

            chunk_records.append(
                KnowledgeBaseChunkModel(
                    document_id=document_id,
                    organization_id=organization_id,
                    chunk_text=chunk["chunk_text"],
                    contextualized_text=contextualized,
                    chunk_index=chunk_idx,
                    chunk_metadata=base_metadata,
                    embedding_model=embedding_service.get_model_id(),
                    embedding_dimension=embedding_service.get_embedding_dimension(),
                    token_count=chunk.get("token_count", 0),
                )
            )
            chunk_texts.append(contextualized)

        logger.info(
            f"Generating embeddings for {len(chunk_texts)} chunks "
            f"using {embedding_service.get_model_id()}"
        )
        embeddings = await _embed_texts_in_batches(embedding_service, chunk_texts)
        if len(embeddings) != len(chunk_records):
            raise ValueError(
                "Embedding count mismatch: "
                f"expected {len(chunk_records)}, got {len(embeddings)}"
            )
        for chunk_record, embedding in zip(chunk_records, embeddings):
            chunk_record.embedding = embedding

        logger.info("Storing chunks in database")
        await db_client.replace_chunks_for_document(
            document_id=document_id,
            organization_id=organization_id,
            chunks=chunk_records,
        )

        await db_client.update_document_status(
            document_id,
            "completed",
            total_chunks=len(chunk_records),
            docling_metadata=docling_metadata,
        )

        logger.info(
            f"Successfully processed knowledge base document {document_id}. "
            f"Total chunks: {len(chunk_records)}"
        )

    except Exception as e:
        logger.exception(
            "Error processing knowledge base document {}: {}", document_id, e
        )
        await db_client.update_document_status(
            document_id, "failed", error_message=str(e)
        )
        raise

    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                logger.debug(f"Cleaned up temp file: {temp_file_path}")
            except Exception as e:
                logger.warning(
                    f"Failed to clean up temp file {temp_file_path}: {e}")


async def _build_enriched_metadata(
    s3_key: str,
    temp_file_path: str,
    mime_type: str,
    mps_chunks: list[dict],
) -> dict[int, dict]:
    """Attempt to extract structured metadata from the source file.

    For JSON files with a known catalog structure (like Johnson Arabia equipment
    data), this parses the JSON and maps each model to its chunk index, providing
    rich filterable metadata per chunk.

    Args:
        s3_key: The S3 key of the file (used for filename detection).
        temp_file_path: Local path to the downloaded file.
        mime_type: MIME type of the file.
        mps_chunks: The chunks returned by MPS (used to correlate by index/text).

    Returns:
        Dict mapping chunk_index → enriched metadata dict.
        Empty dict if the file isn't a recognized structured format.
    """
    # Only attempt structured extraction for JSON files
    if mime_type not in ("application/json", "text/json") and not s3_key.endswith(".json"):
        return {}

    try:
        with open(temp_file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        logger.debug(f"Could not parse JSON for metadata extraction: {e}")
        return {}

    # Detect the Johnson Arabia / equipment catalog structure:
    # v2: { "chunks": [{ "model": "...", "metadata": {...}, ... }] }
    # v1: { "categories": [{ "category_heading": "...", "models": [...] }] }
    chunks_array = data.get("chunks")
    if chunks_array and isinstance(chunks_array, list) and chunks_array:
        first = chunks_array[0]
        if isinstance(first, dict) and "metadata" in first and "model" in first:
            return _map_v2_chunks_to_mps_chunks(chunks_array, mps_chunks)

    categories = data.get("categories")
    if not categories or not isinstance(categories, list):
        # Try flat array of items with "model" keys
        if isinstance(data, list) and data and "model" in data[0]:
            return _map_flat_models_to_chunks(data, mps_chunks)
        return {}

    return _map_categorized_models_to_chunks(categories, mps_chunks)


def _map_v2_chunks_to_mps_chunks(
    json_chunks: list[dict],
    mps_chunks: list[dict],
) -> dict[int, dict]:
    """Map v2-format JSON chunks (with inline metadata) to MPS chunk indices.

    The v2 JSON format has:
    { "chunks": [{ "model": "Grove RT875E", "metadata": {...}, "sections": [...] }] }

    Each chunk already has a `metadata` dict with fields like manufacturer,
    capacity_ton, equipment_type, parent_category, etc.

    Strategy: For each MPS chunk, check if any model name appears in its text.
    Since MPS may split one equipment item into multiple chunks, we also apply
    metadata to consecutive chunks following a match (until a different model
    is found).
    """
    # Build lookup: model_name_lower → metadata from the JSON
    model_metadata: dict[str, dict] = {}
    # Also build a list of all model names for matching, sorted longest first
    # to avoid partial matches (e.g., "Grove RT" matching before "Grove RT875E")
    model_names: list[str] = []
    for item in json_chunks:
        model_name = item.get("model", "")
        metadata = item.get("metadata")
        if not model_name or not metadata or not isinstance(metadata, dict):
            continue
        model_metadata[model_name.lower()] = metadata
        model_names.append(model_name.lower())

    if not model_metadata:
        return {}

    # Sort model names longest first to avoid partial match issues
    model_names.sort(key=len, reverse=True)

    def _normalize(text: str) -> str:
        """Normalize text for matching: collapse whitespace around hyphens/dots,
        remove special chars that MPS might split differently."""
        import re as _re
        # Collapse spaces around hyphens: "1450 - 8.1" → "1450-8.1"
        text = _re.sub(r'\s*-\s*', '-', text)
        # Collapse spaces around dots: "8 . 1" → "8.1"
        text = _re.sub(r'\s*\.\s*', '.', text)
        # Collapse multiple spaces
        text = _re.sub(r'\s+', ' ', text)
        return text.strip()

    # Pre-normalize model names
    normalized_model_map: dict[str, str] = {}  # normalized → original
    for m in model_names:
        normalized_model_map[_normalize(m)] = m

    # First pass: find direct model name matches in chunk text
    enriched: dict[int, dict] = {}
    chunk_model_assignment: dict[int, str] = {}  # chunk_idx → model_name

    for chunk in mps_chunks:
        chunk_text = (chunk.get("chunk_text") or "").lower()
        normalized_text = _normalize(chunk_text)
        chunk_idx = chunk["chunk_index"]

        # Try normalized matching first
        matched = False
        for norm_model, orig_model in normalized_model_map.items():
            if norm_model in normalized_text:
                enriched[chunk_idx] = model_metadata[orig_model]
                chunk_model_assignment[chunk_idx] = orig_model
                matched = True
                break

        # Fallback: try original matching
        if not matched:
            for model_lower in model_names:
                if model_lower in chunk_text:
                    enriched[chunk_idx] = model_metadata[model_lower]
                    chunk_model_assignment[chunk_idx] = model_lower
                    break

    # Second pass: for chunks not matched by full model name, try matching
    # by manufacturer name alone. Build a manufacturer → metadata list lookup.
    # If a chunk mentions a manufacturer but wasn't matched by a full model name,
    # find the best matching model from that manufacturer.
    if len(enriched) < len(mps_chunks):
        # Group models by manufacturer
        mfr_models: dict[str, list[tuple[str, dict]]] = {}
        for model_lower, metadata in model_metadata.items():
            mfr = (metadata.get("manufacturer") or "").lower()
            if mfr:
                if mfr not in mfr_models:
                    mfr_models[mfr] = []
                mfr_models[mfr].append((model_lower, metadata))

        for chunk in mps_chunks:
            chunk_idx = chunk["chunk_index"]
            if chunk_idx in enriched:
                continue

            chunk_text = (chunk.get("chunk_text") or "").lower()
            normalized_chunk = _normalize(chunk_text)

            # Check if any manufacturer name appears in the chunk
            for mfr_lower, models_list in mfr_models.items():
                if mfr_lower in chunk_text:
                    # Find the best model match from this manufacturer
                    # Try each model's short name (last part after manufacturer)
                    best_match = None
                    for model_lower, metadata in models_list:
                        # Try model suffix (e.g., "gr-300ex" from "tadano gr-300ex")
                        parts = model_lower.split()
                        if len(parts) > 1:
                            suffix = " ".join(parts[1:])
                            norm_suffix = _normalize(suffix)
                            if norm_suffix in normalized_chunk or suffix in chunk_text:
                                best_match = metadata
                                chunk_model_assignment[chunk_idx] = model_lower
                                break
                    if best_match:
                        enriched[chunk_idx] = best_match
                        break
                    else:
                        # Can't determine specific model, but we know manufacturer.
                        # Assign only non-model-specific fields (manufacturer,
                        # parent_category, equipment_type) — NOT capacity or model name
                        # since those are model-specific and would be wrong.
                        if models_list:
                            base_meta = models_list[0][1]
                            generic_meta = {}
                            # Only copy category-level fields, not model-specific ones
                            for key in ("manufacturer", "parent_category",
                                        "equipment_type", "sub_category",
                                        "listing", "crawler", "all_terrain",
                                        "rough_terrain", "truck_mounted",
                                        "pick_and_carry"):
                                if key in base_meta:
                                    generic_meta[key] = base_meta[key]
                            if generic_meta:
                                enriched[chunk_idx] = generic_meta
                            break

    # Second pass: for unmatched chunks, propagate metadata from the nearest
    # preceding matched chunk. This handles multi-chunk equipment items where
    # the model name only appears in the first chunk.
    sorted_indices = sorted(chunk_model_assignment.keys())
    if sorted_indices:
        for chunk in mps_chunks:
            chunk_idx = chunk["chunk_index"]
            if chunk_idx in enriched:
                continue

            # Find the nearest preceding matched chunk
            prev_model = None
            for matched_idx in sorted_indices:
                if matched_idx > chunk_idx:
                    break
                prev_model = chunk_model_assignment[matched_idx]

            if prev_model:
                # Check if the next matched chunk is close enough
                # (if the gap is reasonable, assume this chunk belongs to the same item)
                next_matched = None
                for matched_idx in sorted_indices:
                    if matched_idx > chunk_idx:
                        next_matched = matched_idx
                        break

                # Only propagate if we're between two matches or close after one
                # Heuristic: max 8 chunks gap (typical equipment item is 5-6 chunks)
                prev_matched = max(
                    (i for i in sorted_indices if i <= chunk_idx), default=None)
                if prev_matched is not None and (chunk_idx - prev_matched) <= 8:
                    # If there's a next match and we're past it, don't propagate
                    if next_matched is None or chunk_idx < next_matched:
                        enriched[chunk_idx] = model_metadata[prev_model]

    logger.info(
        f"Enriched {len(enriched)}/{len(mps_chunks)} chunks with v2 metadata "
        f"from {len(model_metadata)} models"
    )
    return enriched


def _map_categorized_models_to_chunks(
    categories: list[dict],
    mps_chunks: list[dict],
) -> dict[int, dict]:
    """Map categorized equipment JSON models to their corresponding MPS chunk indices.

    Correlates by matching model names found in chunk text.
    """
    # Build a lookup: model_name_lower → metadata
    model_metadata: dict[str, dict] = {}
    for category in categories:
        category_heading = category.get("category_heading", "")
        models = category.get("models", [])
        for model_item in models:
            model_name = model_item.get("model", "")
            if not model_name:
                continue
            metadata = extract_metadata_from_structured_json(
                model_item, category_heading
            )
            model_metadata[model_name.lower()] = metadata

    if not model_metadata:
        return {}

    # Now correlate with MPS chunks by checking if model name appears in chunk text
    enriched: dict[int, dict] = {}
    for chunk in mps_chunks:
        chunk_text = (chunk.get("chunk_text") or "").lower()
        chunk_idx = chunk["chunk_index"]

        for model_lower, metadata in model_metadata.items():
            if model_lower in chunk_text:
                enriched[chunk_idx] = metadata
                break

    logger.info(
        f"Enriched {len(enriched)}/{len(mps_chunks)} chunks with structured metadata "
        f"from {len(model_metadata)} models"
    )
    return enriched


def _map_flat_models_to_chunks(
    items: list[dict],
    mps_chunks: list[dict],
) -> dict[int, dict]:
    """Map a flat list of model items to chunks."""
    model_metadata: dict[str, dict] = {}
    for item in items:
        model_name = item.get("model", "")
        if not model_name:
            continue
        metadata = extract_metadata_from_structured_json(item)
        model_metadata[model_name.lower()] = metadata

    if not model_metadata:
        return {}

    enriched: dict[int, dict] = {}
    for chunk in mps_chunks:
        chunk_text = (chunk.get("chunk_text") or "").lower()
        chunk_idx = chunk["chunk_index"]

        for model_lower, metadata in model_metadata.items():
            if model_lower in chunk_text:
                enriched[chunk_idx] = metadata
                break

    logger.info(
        f"Enriched {len(enriched)}/{len(mps_chunks)} chunks with flat model metadata"
    )
    return enriched
