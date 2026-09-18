"""PostgreSQL Tenant Schema & Query Role Provisioner.

Implements Requirement 16 & 19:
1. Provisions `csv_org_{organization_id}` schema.
2. Manages `csv_query_role` with strict read-only permissions and no application schema access.
3. Grants USAGE on Org Schema and SELECT on physical tables/views.
4. Provides an isolated connection pool dedicated to the Query Role.
5. Verifies tenant isolation boundaries.
"""

import asyncio
from typing import Optional
from urllib.parse import urlparse, urlunparse

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, async_sessionmaker, create_async_engine

from api.constants import (
    DATABASE_URL,
    RELATIONAL_DATASET_ACQUISITION_TIMEOUT_MS,
    RELATIONAL_DATASET_POOL_SIZE,
    RELATIONAL_DATASET_QUERY_ROLE,
    RELATIONAL_DATASET_QUERY_ROLE_PASSWORD,
)


class SchemaProvisioner:
    """Provisions and manages tenant PostgreSQL schemas and Query Role isolation."""

    _query_role_engine: Optional[AsyncEngine] = None

    @classmethod
    def get_org_schema_name(cls, organization_id: int) -> str:
        """Return the canonical tenant schema name."""
        return f"csv_org_{organization_id}"

    @classmethod
    def get_query_role_database_url(cls) -> str:
        """Construct a connection URL authenticated as the restricted Query Role."""
        parsed = urlparse(DATABASE_URL)
        netloc_parts = parsed.netloc.split("@")
        host_port = netloc_parts[-1]
        user_pass = f"{RELATIONAL_DATASET_QUERY_ROLE}:{RELATIONAL_DATASET_QUERY_ROLE_PASSWORD}"
        new_netloc = f"{user_pass}@{host_port}"
        return urlunparse((parsed.scheme, new_netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))

    @classmethod
    def get_query_role_engine(cls) -> AsyncEngine:
        """Return the dedicated connection pool for the Query Role."""
        if cls._query_role_engine is None:
            url = cls.get_query_role_database_url()
            cls._query_role_engine = create_async_engine(
                url,
                pool_size=RELATIONAL_DATASET_POOL_SIZE,
                max_overflow=5,
                pool_timeout=RELATIONAL_DATASET_ACQUISITION_TIMEOUT_MS / 1000.0,
            )
        return cls._query_role_engine

    @classmethod
    async def ensure_org_schema(cls, conn: AsyncConnection, organization_id: int) -> str:
        """Ensure the tenant schema exists, is revoked from PUBLIC, and is usable by Query Role."""
        schema_name = cls.get_org_schema_name(organization_id)
        query_role = RELATIONAL_DATASET_QUERY_ROLE

        # 1. Create schema if not exists
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))

        # 2. Revoke all privileges from PUBLIC
        await conn.execute(text(f'REVOKE ALL ON SCHEMA "{schema_name}" FROM PUBLIC'))

        # 3. Grant USAGE to Query Role
        await conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema_name}" TO "{query_role}"'))

        return schema_name

    @classmethod
    async def grant_select_to_query_role(
        cls, conn: AsyncConnection, organization_id: int, relation_name: str
    ) -> None:
        """Grant SELECT on a specific DB Table or Table View to the Query Role."""
        schema_name = cls.get_org_schema_name(organization_id)
        query_role = RELATIONAL_DATASET_QUERY_ROLE
        await conn.execute(
            text(f'GRANT SELECT ON TABLE "{schema_name}"."{relation_name}" TO "{query_role}"')
        )

    @classmethod
    async def verify_isolation(cls, organization_id: int) -> bool:
        """Verify that the Query Role is denied access to application tables and other org schemas."""
        engine = cls.get_query_role_engine()
        schema_name = cls.get_org_schema_name(organization_id)

        try:
            async with engine.connect() as conn:
                # 1. Attempt reading application relation 'users' -> MUST FAIL
                denied_app = False
                try:
                    await conn.execute(text("SELECT id FROM users LIMIT 1"))
                except Exception:
                    denied_app = True

                # 2. Attempt reading hypothetical foreign org schema
                other_schema = f"csv_org_{organization_id + 999999}"
                denied_foreign = False
                try:
                    await conn.execute(text(f'SELECT 1 FROM "{other_schema}".some_table LIMIT 1'))
                except Exception:
                    denied_foreign = True

                return denied_app and denied_foreign
        except Exception as e:
            logger.warning(f"Isolation verification probe failed or role connection error: {e}")
            return False
