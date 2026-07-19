"""Database client for managing knowledge base documents and chunks."""

import hashlib
from pathlib import Path
from typing import List, Optional

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from api.db.base_client import BaseDBClient
from api.db.models import KnowledgeBaseChunkModel, KnowledgeBaseDocumentModel


class KnowledgeBaseClient(BaseDBClient):
    """Client for managing knowledge base documents and vector embeddings."""

    async def create_document(
        self,
        organization_id: int,
        created_by: int,
        filename: str,
        file_size_bytes: int,
        file_hash: str,
        mime_type: str,
        source_url: Optional[str] = None,
        custom_metadata: Optional[dict] = None,
        docling_metadata: Optional[dict] = None,
        document_uuid: Optional[str] = None,
        retrieval_mode: str = "chunked",
    ) -> KnowledgeBaseDocumentModel:
        """Create a new knowledge base document record.

        Args:
            organization_id: ID of the organization
            created_by: ID of the user uploading the document
            filename: Name of the file
            file_size_bytes: Size of the file in bytes
            file_hash: SHA-256 hash of the file
            mime_type: MIME type of the file
            source_url: Optional URL if document was fetched from web
            custom_metadata: Optional custom metadata dictionary
            docling_metadata: Optional docling processing metadata
            document_uuid: Optional UUID to use (if not provided, one will be generated)

        Returns:
            The created KnowledgeBaseDocumentModel
        """
        async with self.async_session() as session:
            document = KnowledgeBaseDocumentModel(
                organization_id=organization_id,
                created_by=created_by,
                filename=filename,
                file_size_bytes=file_size_bytes,
                file_hash=file_hash,
                mime_type=mime_type,
                source_url=source_url,
                custom_metadata=custom_metadata or {},
                docling_metadata=docling_metadata or {},
                processing_status="pending",
                total_chunks=0,
                retrieval_mode=retrieval_mode,
            )

            # Use provided UUID or let the model generate one
            if document_uuid:
                document.document_uuid = document_uuid

            session.add(document)
            await session.commit()
            await session.refresh(document)

            logger.info(
                f"Created document '{filename}' ({document.document_uuid}) "
                f"for organization {organization_id}"
            )
            return document

    async def get_document_by_id(
        self,
        document_id: int,
    ) -> Optional[KnowledgeBaseDocumentModel]:
        """Get a document by its database ID.

        Args:
            document_id: The database ID of the document

        Returns:
            KnowledgeBaseDocumentModel if found, None otherwise
        """
        async with self.async_session() as session:
            query = select(KnowledgeBaseDocumentModel).where(
                KnowledgeBaseDocumentModel.id == document_id
            )

            result = await session.execute(query)
            return result.scalar_one_or_none()

    async def get_document_by_uuid(
        self,
        document_uuid: str,
        organization_id: int,
    ) -> Optional[KnowledgeBaseDocumentModel]:
        """Get a document by its UUID, scoped to organization.

        Args:
            document_uuid: The unique document UUID
            organization_id: ID of the organization

        Returns:
            KnowledgeBaseDocumentModel if found, None otherwise
        """
        async with self.async_session() as session:
            query = (
                select(KnowledgeBaseDocumentModel)
                .where(
                    KnowledgeBaseDocumentModel.document_uuid == document_uuid,
                    KnowledgeBaseDocumentModel.organization_id == organization_id,
                    KnowledgeBaseDocumentModel.is_active == True,
                )
                .options(selectinload(KnowledgeBaseDocumentModel.created_by_user))
            )

            result = await session.execute(query)
            return result.scalar_one_or_none()

    async def get_document_by_hash(
        self,
        file_hash: str,
        organization_id: int,
    ) -> Optional[KnowledgeBaseDocumentModel]:
        """Check if a document with the same hash already exists.

        Returns the first matching document if multiple exist (can happen with duplicates).

        Args:
            file_hash: SHA-256 hash of the file
            organization_id: ID of the organization

        Returns:
            KnowledgeBaseDocumentModel if found, None otherwise
        """
        async with self.async_session() as session:
            query = (
                select(KnowledgeBaseDocumentModel)
                .where(
                    KnowledgeBaseDocumentModel.file_hash == file_hash,
                    KnowledgeBaseDocumentModel.organization_id == organization_id,
                    KnowledgeBaseDocumentModel.is_active == True,
                )
                .order_by(KnowledgeBaseDocumentModel.created_at.asc())
                .limit(1)
            )

            result = await session.execute(query)
            return result.scalars().first()

    async def get_documents_for_organization(
        self,
        organization_id: int,
        processing_status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[KnowledgeBaseDocumentModel]:
        """Get all documents for an organization.

        Args:
            organization_id: ID of the organization
            processing_status: Optional filter by status
            limit: Maximum number of documents to return
            offset: Number of documents to skip

        Returns:
            List of KnowledgeBaseDocumentModel instances
        """
        async with self.async_session() as session:
            query = select(KnowledgeBaseDocumentModel).where(
                KnowledgeBaseDocumentModel.organization_id == organization_id,
                KnowledgeBaseDocumentModel.is_active == True,
            )

            if processing_status:
                query = query.where(
                    KnowledgeBaseDocumentModel.processing_status == processing_status
                )

            query = (
                query.order_by(KnowledgeBaseDocumentModel.created_at.desc())
                .limit(limit)
                .offset(offset)
            )

            result = await session.execute(query)
            return list(result.scalars().all())

    async def update_document_metadata(
        self,
        document_id: int,
        file_size_bytes: Optional[int] = None,
        file_hash: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> Optional[KnowledgeBaseDocumentModel]:
        """Update document file metadata.

        Args:
            document_id: ID of the document
            file_size_bytes: Optional file size in bytes
            file_hash: Optional SHA-256 hash of the file
            mime_type: Optional MIME type

        Returns:
            Updated KnowledgeBaseDocumentModel
        """
        async with self.async_session() as session:
            query = select(KnowledgeBaseDocumentModel).where(
                KnowledgeBaseDocumentModel.id == document_id
            )
            result = await session.execute(query)
            document = result.scalar_one_or_none()

            if not document:
                return None

            if file_size_bytes is not None:
                document.file_size_bytes = file_size_bytes
            if file_hash is not None:
                document.file_hash = file_hash
            if mime_type is not None:
                document.mime_type = mime_type

            await session.commit()
            await session.refresh(document)

            logger.info(f"Updated document {document_id} metadata")
            return document

    async def update_document_status(
        self,
        document_id: int,
        status: str,
        error_message: Optional[str] = None,
        total_chunks: Optional[int] = None,
        docling_metadata: Optional[dict] = None,
    ) -> Optional[KnowledgeBaseDocumentModel]:
        """Update document processing status.

        Args:
            document_id: ID of the document
            status: New status (pending, processing, completed, failed)
            error_message: Optional error message if status is failed
            total_chunks: Optional total number of chunks
            docling_metadata: Optional docling metadata

        Returns:
            Updated KnowledgeBaseDocumentModel
        """
        async with self.async_session() as session:
            query = select(KnowledgeBaseDocumentModel).where(
                KnowledgeBaseDocumentModel.id == document_id
            )
            result = await session.execute(query)
            document = result.scalar_one_or_none()

            if not document:
                return None

            document.processing_status = status
            if error_message:
                document.processing_error = error_message
            if total_chunks is not None:
                document.total_chunks = total_chunks
            if docling_metadata:
                document.docling_metadata = docling_metadata

            await session.commit()
            await session.refresh(document)

            logger.info(f"Updated document {document_id} status to {status}")
            return document

    async def create_chunks_batch(
        self,
        chunks: List[KnowledgeBaseChunkModel],
    ) -> List[KnowledgeBaseChunkModel]:
        """Create multiple chunks in a batch.

        Args:
            chunks: List of KnowledgeBaseChunkModel instances

        Returns:
            List of created chunks with IDs
        """
        async with self.async_session() as session:
            session.add_all(chunks)
            await session.commit()

            for chunk in chunks:
                await session.refresh(chunk)

            logger.info(f"Created {len(chunks)} chunks")
            return chunks

    async def replace_chunks_for_document(
        self,
        document_id: int,
        organization_id: int,
        chunks: List[KnowledgeBaseChunkModel],
    ) -> List[KnowledgeBaseChunkModel]:
        """Replace all chunks for a document with a new precomputed batch."""
        async with self.async_session() as session:
            await session.execute(
                delete(KnowledgeBaseChunkModel).where(
                    KnowledgeBaseChunkModel.document_id == document_id,
                    KnowledgeBaseChunkModel.organization_id == organization_id,
                )
            )
            session.add_all(chunks)
            await session.commit()

            for chunk in chunks:
                await session.refresh(chunk)

            logger.info(
                f"Replaced chunks for document {document_id}: {len(chunks)} chunks"
            )
            return chunks

    async def get_chunks_for_document(
        self,
        document_id: int,
        organization_id: int,
    ) -> List[KnowledgeBaseChunkModel]:
        """Get all chunks for a document.

        Args:
            document_id: ID of the document
            organization_id: ID of the organization (for authorization)

        Returns:
            List of KnowledgeBaseChunkModel instances
        """
        async with self.async_session() as session:
            query = (
                select(KnowledgeBaseChunkModel)
                .where(
                    KnowledgeBaseChunkModel.document_id == document_id,
                    KnowledgeBaseChunkModel.organization_id == organization_id,
                )
                .order_by(KnowledgeBaseChunkModel.chunk_index)
            )

            result = await session.execute(query)
            return list(result.scalars().all())

    async def search_similar_chunks(
        self,
        query_embedding: List[float],
        organization_id: int,
        limit: int = 5,
        document_ids: Optional[List[int]] = None,
        document_uuids: Optional[List[str]] = None,
        embedding_model: Optional[str] = None,
    ) -> List[dict]:
        """Search for similar chunks using vector similarity.

        Returns top-k most similar chunks without any similarity threshold filtering.
        Filtering and reranking should be done at the application layer.

        Args:
            query_embedding: The query embedding vector
            organization_id: Organization ID for scoping
            limit: Maximum number of results to return
            document_ids: Optional list of document IDs to filter by
            document_uuids: Optional list of document UUIDs to filter by
            embedding_model: Optional embedding model to filter by (for dimension compatibility)

        Returns:
            List of dictionaries with chunk data and similarity scores, ordered by similarity (highest first)
        """
        async with self.async_session() as session:
            # Get the raw connection to execute directly with asyncpg
            # This avoids parameter binding issues with text() and asyncpg
            connection = await session.connection()
            raw_connection = await connection.get_raw_connection()

            # Build WHERE clause conditions (no similarity threshold)
            where_conditions = [
                "c.organization_id = $2",
                "d.is_active = true",
            ]
            params = [
                None,
                organization_id,
                limit,
            ]  # $1 will be embedding_str, $3 is limit
            param_index = 4  # Next available parameter index

            # Add document_ids filter if provided
            if document_ids:
                placeholders = ", ".join(
                    f"${param_index + i}" for i in range(len(document_ids))
                )
                where_conditions.append(f"c.document_id IN ({placeholders})")
                params.extend(document_ids)
                param_index += len(document_ids)

            # Add document_uuids filter if provided
            if document_uuids:
                placeholders = ", ".join(
                    f"${param_index + i}" for i in range(len(document_uuids))
                )
                where_conditions.append(f"d.document_uuid IN ({placeholders})")
                params.extend(document_uuids)
                param_index += len(document_uuids)

            # Add embedding_model filter if provided (for dimension compatibility)
            if embedding_model:
                where_conditions.append(f"c.embedding_model = ${param_index}")
                params.append(embedding_model)
                param_index += 1

            # Build the complete SQL query
            where_clause = " AND ".join(where_conditions)
            query_sql = f"""
                SELECT
                    c.id,
                    c.document_id,
                    c.chunk_text,
                    c.contextualized_text,
                    c.chunk_metadata,
                    c.chunk_index,
                    d.filename,
                    d.document_uuid,
                    1 - (c.embedding <=> $1::vector) as similarity
                FROM knowledge_base_chunks c
                JOIN knowledge_base_documents d ON c.document_id = d.id
                WHERE {where_clause}
                ORDER BY c.embedding <=> $1::vector
                LIMIT $3
            """

            # Convert embedding to string format for PostgreSQL vector type
            embedding_str = "[" + ",".join(map(str, query_embedding)) + "]"
            params[0] = embedding_str  # Set $1

            # Execute query directly with asyncpg
            rows = await raw_connection.driver_connection.fetch(
                query_sql,
                *params,
            )

            # Convert asyncpg records to dictionaries
            return [dict(row) for row in rows]

    async def update_document_full_text(
        self,
        document_id: int,
        full_text: str,
    ) -> None:
        """Store full document text for full_document retrieval mode.

        Args:
            document_id: ID of the document
            full_text: The full extracted text content
        """
        async with self.async_session() as session:
            query = select(KnowledgeBaseDocumentModel).where(
                KnowledgeBaseDocumentModel.id == document_id
            )
            result = await session.execute(query)
            document = result.scalar_one_or_none()
            if document:
                document.full_text = full_text
                await session.commit()
                logger.info(
                    f"Stored full text for document {document_id} ({len(full_text)} chars)"
                )

    async def get_full_text_documents(
        self,
        organization_id: int,
        document_uuids: List[str],
    ) -> List[KnowledgeBaseDocumentModel]:
        """Get full_document mode documents by their UUIDs.

        Args:
            organization_id: Organization ID for scoping
            document_uuids: List of document UUIDs to fetch

        Returns:
            List of documents with retrieval_mode='full_document' and full_text set
        """
        async with self.async_session() as session:
            query = select(KnowledgeBaseDocumentModel).where(
                KnowledgeBaseDocumentModel.organization_id == organization_id,
                KnowledgeBaseDocumentModel.document_uuid.in_(document_uuids),
                KnowledgeBaseDocumentModel.retrieval_mode == "full_document",
                KnowledgeBaseDocumentModel.is_active == True,
                KnowledgeBaseDocumentModel.processing_status == "completed",
            )
            result = await session.execute(query)
            return list(result.scalars().all())

    async def delete_document(
        self,
        document_uuid: str,
        organization_id: int,
    ) -> bool:
        """Soft delete a document by setting is_active to False.

        This will also cascade delete all chunks via the database foreign key.

        Args:
            document_uuid: The unique document UUID
            organization_id: ID of the organization (for authorization)

        Returns:
            True if document was deleted, False if not found
        """
        async with self.async_session() as session:
            query = select(KnowledgeBaseDocumentModel).where(
                KnowledgeBaseDocumentModel.document_uuid == document_uuid,
                KnowledgeBaseDocumentModel.organization_id == organization_id,
            )

            result = await session.execute(query)
            document = result.scalar_one_or_none()

            if not document:
                return False

            document.is_active = False
            await session.commit()

            logger.info(
                f"Deleted document {document_uuid} for organization {organization_id}"
            )
            return True

    async def filter_chunks_by_metadata(
        self,
        organization_id: int,
        filters: dict,
        document_uuids: Optional[List[str]] = None,
        limit: int = 20,
    ) -> List[dict]:
        """Filter chunks by structured metadata using PostgreSQL JSON operators.

        Supports exact match, comparison operators, and IN-list filtering
        on the chunk_metadata JSONB column.

        Args:
            organization_id: Organization ID for scoping
            filters: Dict of filters. Keys are metadata field names, values can be:
                - scalar (str/int/float): exact match
                - dict with operator: {"gt": n}, {"lt": n}, {"gte": n}, {"lte": n}
                - dict with "in": {"in": [values]}
            document_uuids: Optional list of document UUIDs to scope
            limit: Max results to return

        Returns:
            List of dicts with chunk data and metadata
        """
        import re as _re

        # Sanitize field names: only allow alphanumeric + underscore
        # This prevents SQL injection via malicious field names
        _SAFE_FIELD = _re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

        async with self.async_session() as session:
            connection = await session.connection()
            raw_connection = await connection.get_raw_connection()

            where_conditions = [
                "c.organization_id = $1",
                "d.is_active = true",
            ]
            params: list = [organization_id, limit]
            param_index = 3  # $1=org_id, $2=limit, next is $3

            # Add document_uuids filter
            if document_uuids:
                placeholders = ", ".join(
                    f"${param_index + i}" for i in range(len(document_uuids))
                )
                where_conditions.append(f"d.document_uuid IN ({placeholders})")
                params.extend(document_uuids)
                param_index += len(document_uuids)

            # Build metadata filter conditions
            for field, value in filters.items():
                # Reject unsafe field names to prevent SQL injection
                if not _SAFE_FIELD.match(field):
                    logger.warning(
                        f"Skipping unsafe metadata filter field name: {field!r}"
                    )
                    continue

                if isinstance(value, dict):
                    # Operator-based filter
                    for op, op_value in value.items():
                        # Use a safe float cast that returns NULL for non-numeric values
                        # instead of throwing an error. This handles chunks where the
                        # metadata field exists but has a non-numeric value.
                        safe_cast = (
                            f"CASE WHEN c.chunk_metadata->>'{field}' ~ '^[0-9]+(\\.[0-9]+)?$' "
                            f"THEN (c.chunk_metadata->>'{field}')::float ELSE NULL END"
                        )
                        if op == "gt":
                            where_conditions.append(
                                f"{safe_cast} > ${param_index}"
                            )
                            params.append(float(op_value))
                            param_index += 1
                        elif op == "lt":
                            where_conditions.append(
                                f"{safe_cast} < ${param_index}"
                            )
                            params.append(float(op_value))
                            param_index += 1
                        elif op == "gte":
                            where_conditions.append(
                                f"{safe_cast} >= ${param_index}"
                            )
                            params.append(float(op_value))
                            param_index += 1
                        elif op == "lte":
                            where_conditions.append(
                                f"{safe_cast} <= ${param_index}"
                            )
                            params.append(float(op_value))
                            param_index += 1
                        elif op == "in":
                            if isinstance(op_value, list) and op_value:
                                placeholders = ", ".join(
                                    f"${param_index + i}"
                                    for i in range(len(op_value))
                                )
                                where_conditions.append(
                                    f"c.chunk_metadata->>'{field}' IN ({placeholders})"
                                )
                                params.extend([str(v) for v in op_value])
                                param_index += len(op_value)
                else:
                    # Exact match
                    if isinstance(value, bool):
                        # Booleans in JSONB: ->> extracts as 'true'/'false' strings
                        where_conditions.append(
                            f"c.chunk_metadata->>'{field}' = ${param_index}"
                        )
                        params.append("true" if value else "false")
                    elif isinstance(value, (int, float)):
                        safe_cast = (
                            f"CASE WHEN c.chunk_metadata->>'{field}' ~ '^[0-9]+(\\.[0-9]+)?$' "
                            f"THEN (c.chunk_metadata->>'{field}')::float ELSE NULL END"
                        )
                        where_conditions.append(
                            f"{safe_cast} = ${param_index}"
                        )
                        params.append(float(value))
                    else:
                        # Case-insensitive string match with LIKE for partial matching
                        # This handles cases like "Tadano" matching "Tadano" or
                        # "Grove (Manitowoc)" matching when user says "Grove"
                        where_conditions.append(
                            f"LOWER(c.chunk_metadata->>'{field}') LIKE LOWER(${param_index})"
                        )
                        # Add % wildcards for contains-match on fields where
                        # values may have suffixes/prefixes
                        val = str(value)
                        fuzzy_fields = (
                            "manufacturer", "category", "equipment_type",
                            "sub_category", "parent_category",
                        )
                        if field in fuzzy_fields:
                            params.append(f"%{val}%")
                        else:
                            params.append(val)
                    param_index += 1

            where_clause = " AND ".join(where_conditions)
            query_sql = f"""
                SELECT
                    c.id,
                    c.document_id,
                    c.chunk_text,
                    c.contextualized_text,
                    c.chunk_metadata,
                    c.chunk_index,
                    d.filename,
                    d.document_uuid
                FROM knowledge_base_chunks c
                JOIN knowledge_base_documents d ON c.document_id = d.id
                WHERE {where_clause}
                ORDER BY c.chunk_index
                LIMIT $2
            """

            rows = await raw_connection.driver_connection.fetch(
                query_sql,
                *params,
            )

            # Convert to list of dicts with useful fields
            results = []
            for row in rows:
                row_dict = dict(row)
                # Parse chunk_metadata if it's a string
                metadata = row_dict.get("chunk_metadata", {})
                if isinstance(metadata, str):
                    try:
                        metadata = __import__("json").loads(metadata)
                    except (ValueError, TypeError):
                        metadata = {}

                results.append({
                    "text": row_dict.get("contextualized_text") or row_dict.get("chunk_text", ""),
                    "chunk_text": row_dict.get("chunk_text", ""),
                    "metadata": metadata,
                    "filename": row_dict.get("filename"),
                    "document_uuid": row_dict.get("document_uuid"),
                    "chunk_index": row_dict.get("chunk_index"),
                })

            return results

    async def aggregate_chunks_metadata(
        self,
        organization_id: int,
        group_by: Optional[str] = None,
        aggregate_field: Optional[str] = None,
        aggregate_function: str = "count",
        document_uuids: Optional[List[str]] = None,
        filters: Optional[dict] = None,
        order_by: str = "desc",
        limit: int = 20,
    ) -> List[dict]:
        """Perform aggregation on chunk metadata using PostgreSQL.

        Supports COUNT, AVG, MAX, MIN, SUM with optional GROUP BY.

        Args:
            organization_id: Organization ID for scoping
            group_by: Field to group by (optional)
            aggregate_field: Numeric field for avg/max/min/sum
            aggregate_function: count, avg, max, min, sum
            document_uuids: Optional document filter
            filters: Optional pre-filters (same as filter_chunks_by_metadata)
            order_by: desc or asc
            limit: Max groups

        Returns:
            List of dicts with group key and aggregate value
        """
        import re as _re
        _SAFE_FIELD = _re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

        # Validate field names
        if group_by and not _SAFE_FIELD.match(group_by):
            raise ValueError(f"Unsafe group_by field: {group_by}")
        if aggregate_field and not _SAFE_FIELD.match(aggregate_field):
            raise ValueError(f"Unsafe aggregate_field: {aggregate_field}")

        async with self.async_session() as session:
            connection = await session.connection()
            raw_connection = await connection.get_raw_connection()

            where_conditions = [
                "c.organization_id = $1",
                "d.is_active = true",
            ]
            params: list = [organization_id]
            param_index = 2

            # Add document_uuids filter
            if document_uuids:
                placeholders = ", ".join(
                    f"${param_index + i}" for i in range(len(document_uuids))
                )
                where_conditions.append(
                    f"d.document_uuid IN ({placeholders})"
                )
                params.extend(document_uuids)
                param_index += len(document_uuids)

            # Add pre-filters on metadata
            if filters:
                for field, value in filters.items():
                    if not _SAFE_FIELD.match(field):
                        continue
                    if isinstance(value, bool):
                        where_conditions.append(
                            f"c.chunk_metadata->>'{field}' = ${param_index}"
                        )
                        params.append("true" if value else "false")
                        param_index += 1
                    elif isinstance(value, str):
                        where_conditions.append(
                            f"LOWER(c.chunk_metadata->>'{field}') LIKE LOWER(${param_index})"
                        )
                        params.append(f"%{value}%")
                        param_index += 1
                    elif isinstance(value, (int, float)):
                        where_conditions.append(
                            f"(c.chunk_metadata->>'{field}')::float = ${param_index}"
                        )
                        params.append(float(value))
                        param_index += 1
                    elif isinstance(value, dict):
                        for op, op_val in value.items():
                            if op == "gt":
                                where_conditions.append(
                                    f"(c.chunk_metadata->>'{field}')::float > ${param_index}"
                                )
                                params.append(float(op_val))
                                param_index += 1
                            elif op == "gte":
                                where_conditions.append(
                                    f"(c.chunk_metadata->>'{field}')::float >= ${param_index}"
                                )
                                params.append(float(op_val))
                                param_index += 1
                            elif op == "lt":
                                where_conditions.append(
                                    f"(c.chunk_metadata->>'{field}')::float < ${param_index}"
                                )
                                params.append(float(op_val))
                                param_index += 1
                            elif op == "lte":
                                where_conditions.append(
                                    f"(c.chunk_metadata->>'{field}')::float <= ${param_index}"
                                )
                                params.append(float(op_val))
                                param_index += 1

            # Require the group_by or aggregate field to be non-null
            if group_by:
                where_conditions.append(
                    f"c.chunk_metadata->>'{group_by}' IS NOT NULL"
                )
                where_conditions.append(
                    f"c.chunk_metadata->>'{group_by}' != ''"
                )

            where_clause = " AND ".join(where_conditions)

            # Build SELECT and GROUP BY
            if group_by:
                group_expr = f"c.chunk_metadata->>'{group_by}'"

                if aggregate_function == "count":
                    # Count unique items — use chunk_index = 0 or distinct model
                    # to avoid counting multiple chunks from the same equipment item.
                    # Heuristic: count chunks where chunk_index is lowest per document
                    # section, approximated by counting distinct contextualized_text
                    # patterns. Simpler: count only the first chunk per model group
                    # by requiring the chunk has the manufacturer in its text.
                    select_expr = (
                        f"{group_expr} as group_key, "
                        f"COUNT(DISTINCT COALESCE(c.chunk_metadata->>'model', c.id::text)) as agg_value"
                    )
                else:
                    # For avg/max/min/sum, need numeric aggregate field
                    agg_field_expr = (
                        f"CASE WHEN c.chunk_metadata->>'{aggregate_field}' ~ '^[0-9]+(\\.[0-9]+)?$' "
                        f"THEN (c.chunk_metadata->>'{aggregate_field}')::float ELSE NULL END"
                    )
                    func_map = {
                        "avg": "AVG", "max": "MAX", "min": "MIN", "sum": "SUM"
                    }
                    sql_func = func_map[aggregate_function]
                    select_expr = (
                        f"{group_expr} as group_key, "
                        f"{sql_func}({agg_field_expr}) as agg_value, "
                        f"COUNT(DISTINCT c.id) as item_count"
                    )

                order_dir = "DESC" if order_by == "desc" else "ASC"
                query_sql = f"""
                    SELECT {select_expr}
                    FROM knowledge_base_chunks c
                    JOIN knowledge_base_documents d ON c.document_id = d.id
                    WHERE {where_clause}
                    GROUP BY {group_expr}
                    ORDER BY agg_value {order_dir} NULLS LAST
                    LIMIT {limit}
                """
            else:
                # No group_by — single aggregate across all matching data
                if aggregate_function == "count":
                    select_expr = (
                        "COUNT(DISTINCT COALESCE(c.chunk_metadata->>'model', c.id::text)) as agg_value"
                    )
                else:
                    agg_field_expr = (
                        f"CASE WHEN c.chunk_metadata->>'{aggregate_field}' ~ '^[0-9]+(\\.[0-9]+)?$' "
                        f"THEN (c.chunk_metadata->>'{aggregate_field}')::float ELSE NULL END"
                    )
                    func_map = {
                        "avg": "AVG", "max": "MAX", "min": "MIN", "sum": "SUM"
                    }
                    sql_func = func_map[aggregate_function]
                    select_expr = f"{sql_func}({agg_field_expr}) as agg_value, COUNT(DISTINCT c.id) as item_count"

                query_sql = f"""
                    SELECT {select_expr}
                    FROM knowledge_base_chunks c
                    JOIN knowledge_base_documents d ON c.document_id = d.id
                    WHERE {where_clause}
                """

            rows = await raw_connection.driver_connection.fetch(
                query_sql, *params
            )

            results = []
            for row in rows:
                row_dict = dict(row)
                result_item = {}
                if group_by:
                    result_item["group"] = row_dict.get("group_key")
                agg_val = row_dict.get("agg_value")
                if agg_val is not None:
                    result_item[aggregate_function] = (
                        round(float(agg_val), 2) if isinstance(agg_val, (int, float))
                        else agg_val
                    )
                if "item_count" in row_dict:
                    result_item["count"] = row_dict["item_count"]
                results.append(result_item)

            return results

    async def search_chunks_by_text(
        self,
        organization_id: int,
        search_terms: List[str],
        document_uuids: Optional[List[str]] = None,
        limit: int = 20,
    ) -> List[dict]:
        """Search chunks by text content using ILIKE (case-insensitive).

        All search terms must appear in the chunk text (AND logic).
        This is the last-resort fallback when metadata filtering returns nothing.

        Args:
            organization_id: Organization ID for scoping
            search_terms: List of strings that must all appear in chunk_text
            document_uuids: Optional document UUID filter
            limit: Max results

        Returns:
            List of dicts with chunk data
        """
        if not search_terms:
            return []

        async with self.async_session() as session:
            connection = await session.connection()
            raw_connection = await connection.get_raw_connection()

            where_conditions = [
                "c.organization_id = $1",
                "d.is_active = true",
            ]
            params: list = [organization_id, limit]
            param_index = 3

            # Add document_uuids filter
            if document_uuids:
                placeholders = ", ".join(
                    f"${param_index + i}" for i in range(len(document_uuids))
                )
                where_conditions.append(
                    f"d.document_uuid IN ({placeholders})"
                )
                params.extend(document_uuids)
                param_index += len(document_uuids)

            # Add text search conditions (all terms must match)
            for term in search_terms:
                where_conditions.append(
                    f"c.chunk_text ILIKE ${param_index}"
                )
                params.append(f"%{term}%")
                param_index += 1

            where_clause = " AND ".join(where_conditions)
            query_sql = f"""
                SELECT
                    c.id,
                    c.document_id,
                    c.chunk_text,
                    c.contextualized_text,
                    c.chunk_metadata,
                    c.chunk_index,
                    d.filename,
                    d.document_uuid
                FROM knowledge_base_chunks c
                JOIN knowledge_base_documents d ON c.document_id = d.id
                WHERE {where_clause}
                ORDER BY c.chunk_index
                LIMIT $2
            """

            rows = await raw_connection.driver_connection.fetch(
                query_sql, *params
            )

            results = []
            for row in rows:
                row_dict = dict(row)
                metadata = row_dict.get("chunk_metadata", {})
                if isinstance(metadata, str):
                    try:
                        metadata = __import__("json").loads(metadata)
                    except (ValueError, TypeError):
                        metadata = {}

                results.append({
                    "text": row_dict.get("contextualized_text")
                    or row_dict.get("chunk_text", ""),
                    "chunk_text": row_dict.get("chunk_text", ""),
                    "metadata": metadata,
                    "filename": row_dict.get("filename"),
                    "document_uuid": row_dict.get("document_uuid"),
                    "chunk_index": row_dict.get("chunk_index"),
                })

            return results

    async def get_metadata_fields_for_org(
        self,
        organization_id: int,
        document_uuids: Optional[List[str]] = None,
        sample_size: int = 100,
    ) -> List[str]:
        """Get distinct metadata field names with sample values for an org.

        Returns field names with example values to help the LLM understand
        what values to use in filters.

        Args:
            organization_id: Organization ID
            document_uuids: Optional scope to specific documents
            sample_size: Number of distinct values to sample per field

        Returns:
            List of strings like "manufacturer (e.g. Tadano, Liebherr, Grove)"
        """
        async with self.async_session() as session:
            connection = await session.connection()
            raw_connection = await connection.get_raw_connection()

            # First get field names
            if document_uuids:
                placeholders = ", ".join(
                    f"${i + 2}" for i in range(len(document_uuids))
                )
                fields_sql = f"""
                    SELECT DISTINCT jsonb_object_keys(chunk_metadata::jsonb) as field_name
                    FROM knowledge_base_chunks c
                    JOIN knowledge_base_documents d ON c.document_id = d.id
                    WHERE c.organization_id = $1
                      AND d.document_uuid IN ({placeholders})
                      AND d.is_active = true
                """
                params = [organization_id, *document_uuids]
            else:
                fields_sql = """
                    SELECT DISTINCT jsonb_object_keys(chunk_metadata::jsonb) as field_name
                    FROM knowledge_base_chunks c
                    JOIN knowledge_base_documents d ON c.document_id = d.id
                    WHERE c.organization_id = $1
                      AND d.is_active = true
                """
                params = [organization_id]

            try:
                rows = await raw_connection.driver_connection.fetch(
                    fields_sql, *params
                )
                field_names = [row["field_name"] for row in rows]

                # For key filterable fields, get sample values
                result_fields = []
                sample_fields = [
                    "manufacturer", "parent_category", "equipment_type",
                    "category", "sub_category"
                ]
                for field in field_names:
                    if field in sample_fields:
                        # Get top 5 distinct values for this field
                        val_sql = f"""
                            SELECT DISTINCT chunk_metadata->>'{field}' as val
                            FROM knowledge_base_chunks
                            WHERE organization_id = $1
                              AND chunk_metadata->>'{field}' IS NOT NULL
                              AND chunk_metadata->>'{field}' != ''
                            LIMIT 6
                        """
                        val_rows = await raw_connection.driver_connection.fetch(
                            val_sql, organization_id
                        )
                        vals = [r["val"] for r in val_rows if r["val"]]
                        if vals:
                            examples = ", ".join(vals[:5])
                            result_fields.append(
                                f"{field} (values: {examples})")
                        else:
                            result_fields.append(field)
                    else:
                        result_fields.append(field)

                return result_fields
            except Exception as e:
                logger.warning(f"Failed to get metadata fields: {e}")
                return []

    @staticmethod
    def compute_file_hash(file_path: str) -> str:
        """Compute SHA-256 hash of a file.

        Args:
            file_path: Path to the file

        Returns:
            SHA-256 hash as hex string
        """
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    @staticmethod
    def get_mime_type(file_path: str) -> str:
        """Get MIME type based on file extension.

        Args:
            file_path: Path to the file

        Returns:
            MIME type string
        """
        extension = Path(file_path).suffix.lower()
        mime_types = {
            ".pdf": "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".doc": "application/msword",
            ".txt": "text/plain",
            ".json": "application/json",
            ".html": "text/html",
            ".md": "text/markdown",
        }
        return mime_types.get(extension, "application/octet-stream")
