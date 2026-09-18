"""Relationship Candidate Auto-Detector for the Relational Dataset Tool.

Implements Requirement 6:
1. Evaluates ordered pairs of columns across DB Tables of the same organization.
2. Filters by type comparability, candidate keys on target, and minimum source cardinality (>= 8).
3. Computes containment ratio (threshold >= 0.80) and name-similarity score.
4. Ranks candidates and infers cardinality (one_to_one vs many_to_one).
5. Returns up to 20 candidate suggestions in state 'suggested'.
"""

import difflib
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from api.services.db_tables.schema_provisioner import SchemaProvisioner


class RelationDetector:
    """Detects foreign key candidate relationships across tables."""

    MIN_SOURCE_DISTINCT_VALUES = 8
    CONTAINMENT_THRESHOLD = 0.80
    MAX_CANDIDATES_PER_ORG = 20
    MAX_PAIRS_EVALUATED = 10_000
    DETECTION_TIMEOUT_SECONDS = 120

    @classmethod
    def are_types_comparable(cls, type_a: str, type_b: str) -> bool:
        """Check if two column types are comparable in PostgreSQL."""
        if type_a == type_b:
            return True
        numeric_group = {"integer", "numeric"}
        if type_a in numeric_group and type_b in numeric_group:
            return True
        temporal_group = {"date", "timestamp"}
        if type_a in temporal_group and type_b in temporal_group:
            return True
        return False

    @classmethod
    def compute_name_similarity(cls, name_a: str, name_b: str) -> float:
        """Compute normalized similarity score between column identifiers."""
        clean_a = name_a.lower().replace("_", "")
        clean_b = name_b.lower().replace("_", "")
        return difflib.SequenceMatcher(None, clean_a, clean_b).ratio()

    @classmethod
    async def detect_relationships(
        cls,
        conn: AsyncConnection,
        organization_id: int,
        tables_metadata: List[Dict[str, Any]],  # metadata of all loaded tables in org
    ) -> List[Dict[str, Any]]:
        """Run auto-detection across all loaded tables in the organization."""
        start_time = time.time()
        schema_name = SchemaProvisioner.get_org_schema_name(organization_id)
        candidates: List[Dict[str, Any]] = []
        pairs_evaluated = 0

        # Build lookup of candidate keys per table
        table_keys_map: Dict[int, Set[str]] = {}
        for t in tables_metadata:
            t_id = t["id"]
            cand_keys = set(t.get("candidate_keys") or [])
            table_keys_map[t_id] = cand_keys

        # Evaluate pairs (source_table, target_table)
        for s_tbl in tables_metadata:
            s_id = s_tbl["id"]
            s_name = s_tbl["table_name"]
            s_cols = s_tbl.get("column_schema") or []

            for t_tbl in tables_metadata:
                t_id = t_tbl["id"]
                t_name = t_tbl["table_name"]
                t_cols = t_tbl.get("column_schema") or []

                if s_id == t_id:
                    continue  # Exclude self-relations in auto-detection

                target_candidate_keys = table_keys_map.get(t_id, set())
                if not target_candidate_keys:
                    continue

                for s_col in s_cols:
                    s_col_name = s_col["name"]
                    s_col_type = s_col["type"]
                    s_distinct = s_col.get("distinct_count", 0)

                    # Criterion 11: source must have >= 8 distinct non-null values
                    if s_distinct < cls.MIN_SOURCE_DISTINCT_VALUES:
                        continue

                    for t_col in t_cols:
                        t_col_name = t_col["name"]
                        t_col_type = t_col["type"]

                        # Target column MUST be a candidate key
                        if t_col_name not in target_candidate_keys:
                            continue

                        # Criterion 2: Types must be comparable
                        if not cls.are_types_comparable(s_col_type, t_col_type):
                            continue

                        pairs_evaluated += 1
                        if pairs_evaluated > cls.MAX_PAIRS_EVALUATED or (time.time() - start_time) > cls.DETECTION_TIMEOUT_SECONDS:
                            logger.info(f"Relation detection reached evaluation limits ({pairs_evaluated} pairs evaluated).")
                            return cls._rank_and_trim_candidates(candidates)

                        # Query containment in database
                        containment, absent_count = await cls._compute_containment(
                            conn, schema_name, s_name, s_col_name, t_name, t_col_name, s_distinct
                        )

                        if containment >= cls.CONTAINMENT_THRESHOLD:
                            name_sim = cls.compute_name_similarity(s_col_name, t_col_name)
                            s_null_count = s_col.get("null_count", 0)
                            s_row_count = s_tbl.get("row_count", 0)
                            s_non_null = s_row_count - s_null_count

                            # Inferred cardinality
                            cardinality = "one_to_one" if s_distinct == s_non_null else "many_to_one"

                            candidates.append({
                                "source_db_table_id": s_id,
                                "source_table_name": s_name,
                                "source_column": s_col_name,
                                "target_db_table_id": t_id,
                                "target_table_name": t_name,
                                "target_column": t_col_name,
                                "cardinality": cardinality,
                                "state": "suggested",
                                "containment_ratio": round(containment, 4),
                                "distinct_source_count": s_distinct,
                                "absent_source_count": absent_count,
                                "name_similarity_score": round(name_sim, 4),
                                "s_distinct": s_distinct,
                                "s_non_null": s_non_null,
                            })

        return cls._rank_and_trim_candidates(candidates)

    @classmethod
    async def _compute_containment(
        cls,
        conn: AsyncConnection,
        schema: str,
        s_table: str,
        s_col: str,
        t_table: str,
        t_col: str,
        s_distinct: int,
    ) -> Tuple[float, int]:
        """Compute containment ratio of source values in target column."""
        if s_distinct <= 0:
            return 0.0, 0

        query = text(f"""
            SELECT COUNT(DISTINCT s."{s_col}") AS matched_count
            FROM "{schema}"."{s_table}" s
            WHERE s."{s_col}" IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM "{schema}"."{t_table}" t
                  WHERE t."{t_col}" = s."{s_col}"
              )
        """)

        res = await conn.execute(query)
        row = res.fetchone()
        matched = row[0] if row else 0
        ratio = float(matched) / float(s_distinct)
        absent = max(0, s_distinct - matched)
        return ratio, absent

    @classmethod
    def _rank_and_trim_candidates(cls, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Rank candidates according to Requirement 6 Criterion 6 and cap at 20."""
        # Rank by containment_ratio DESC, then name_similarity_score DESC, then s_distinct < s_non_null, then source_table_name
        def rank_key(c):
            return (
                -c["containment_ratio"],
                -c["name_similarity_score"],
                0 if c.get("s_distinct", 0) < c.get("s_non_null", 0) else 1,
                c["source_table_name"],
            )

        candidates.sort(key=rank_key)
        return candidates[: cls.MAX_CANDIDATES_PER_ORG]
