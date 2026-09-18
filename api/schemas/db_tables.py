"""Pydantic schemas for the Relational Dataset Tool."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ConfirmPrimaryKeySchema(BaseModel):
    column_name: str = Field(..., description="Sanitized identifier of the candidate key column to confirm as primary key")


class ReparseTableSchema(BaseModel):
    forced_text_columns: Optional[List[str]] = Field(
        default=None, description="List of column identifiers to force to text during rebuild"
    )


class CreateRelationshipSchema(BaseModel):
    source_document_uuid: str = Field(..., description="Document UUID of the source DB table")
    source_column: str = Field(..., description="Foreign key column on the source table")
    target_document_uuid: str = Field(..., description="Document UUID of the target DB table")
    target_column: str = Field(..., description="Primary/unique key column on the target table")
    cardinality: str = Field(
        ..., description="Cardinality label: 'one_to_one', 'one_to_many', or 'many_to_one'"
    )


class UpdateRelationshipStatusSchema(BaseModel):
    state: str = Field(..., description="New state: 'accepted' or 'rejected'")


class CreateComputedColumnSchema(BaseModel):
    target_column_name: str = Field(..., description="Name of the new computed column")
    result_type: str = Field(default="boolean", description="Result type: 'boolean' or 'text'")
    combinator: str = Field(default="all", description="Combinator: 'all' or 'any'")
    rules: List[Dict[str, Any]] = Field(..., description="List of rule specifications")


class CreateTableViewSchema(BaseModel):
    view_name: str = Field(..., description="Identifier for the new PostgreSQL Table View")
    rules: List[Dict[str, Any]] = Field(..., description="List of conjunction filter rules")


class DbTableResponseSchema(BaseModel):
    db_table_uuid: str
    document_uuid: str
    table_name: str
    source_filename: str
    state: str
    row_count: int
    column_count: int
    column_schema: List[Dict[str, Any]]
    type_report: Optional[List[Dict[str, Any]]] = None
    load_report: Optional[Dict[str, Any]] = None
    value_profile: Optional[List[Dict[str, Any]]] = None
    suggested_primary_key: Optional[str] = None
    confirmed_primary_key: Optional[str] = None
    forced_text_columns: Optional[List[str]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class RelationshipResponseSchema(BaseModel):
    relationship_uuid: str
    source_table_name: Optional[str] = None
    source_column: str
    target_table_name: Optional[str] = None
    target_column: str
    cardinality: str
    state: str
    containment_ratio: Optional[float] = None
    distinct_source_count: Optional[int] = None
    absent_source_count: Optional[int] = None
    name_similarity_score: Optional[float] = None
    warning_message: Optional[str] = None
    created_at: Optional[datetime] = None
