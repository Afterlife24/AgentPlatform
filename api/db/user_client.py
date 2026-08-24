import uuid
from datetime import UTC, datetime, timezone

from loguru import logger
from pydantic import ValidationError
from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.future import select

from api.db.base_client import BaseDBClient
from api.db.models import UserConfigurationModel, UserModel
from api.enums import UserConfigurationKey
from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration


class UserClient(BaseDBClient):
    async def get_or_create_user_by_provider_id(
        self, provider_id: str
    ) -> tuple[UserModel, bool]:
        """Return (user, was_created) tuple."""
        async with self.async_session() as session:
            # First try to get existing user
            result = await session.execute(
                select(UserModel).where(UserModel.provider_id == provider_id)
            )
            user = result.scalars().first()

            if user is not None:
                return user, False

            # Use PostgreSQL's INSERT ... ON CONFLICT DO NOTHING
            # This is atomic and handles race conditions at the database level
            stmt = insert(UserModel.__table__).values(
                provider_id=provider_id,
                created_at=datetime.now(timezone.utc),
                selected_organization_id=None,  # Will be set later
                is_superuser=False,  # Default value
            )
            # ON CONFLICT DO NOTHING - if another request already inserted, this becomes a no-op
            stmt = stmt.on_conflict_do_nothing(index_elements=["provider_id"])

            result = await session.execute(stmt)
            await session.commit()
            was_created = result.rowcount > 0

            # Now fetch the user (either the one we just created or the one that existed)
            result = await session.execute(
                select(UserModel).where(UserModel.provider_id == provider_id)
            )
            user = result.scalars().first()

            if user is None:
                # This should never happen, but handle it just in case
                error_msg = (
                    f"Failed to create or fetch user with provider_id {provider_id}"
                )
                raise ValueError(error_msg)
        return user, was_created

    async def get_user_by_id(self, user_id: int) -> UserModel | None:
        """Fetch a user by their internal ID."""
        async with self.async_session() as session:
            result = await session.execute(
                select(UserModel).where(UserModel.id == user_id)
            )
            return result.scalars().first()

    async def _get_user_configuration_row(
        self, session, user_id: int, key: str
    ) -> UserConfigurationModel | None:
        result = await session.execute(
            select(UserConfigurationModel).where(
                UserConfigurationModel.user_id == user_id,
                UserConfigurationModel.key == key,
            )
        )
        return result.scalars().first()

    async def get_user_configuration_value(self, user_id: int, key: str) -> dict | None:
        """Get the JSON value stored for a user under `key`, or None."""
        async with self.async_session() as session:
            row = await self._get_user_configuration_row(session, user_id, key)
            return row.configuration if row else None

    async def upsert_user_configuration_value(
        self, user_id: int, key: str, value: dict
    ) -> dict:
        """Create or update the JSON value stored for a user under `key`."""
        async with self.async_session() as session:
            stmt = insert(UserConfigurationModel.__table__).values(
                user_id=user_id,
                key=key,
                configuration=value,
            )
            stmt = stmt.on_conflict_do_update(
                constraint="_user_configuration_key_uc",
                set_={"configuration": stmt.excluded.configuration},
            ).returning(UserConfigurationModel.configuration)
            try:
                result = await session.execute(stmt)
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e
            return result.scalar_one()

    async def get_user_configurations(
        self, user_id: int
    ) -> EffectiveAIModelConfiguration:
        async with self.async_session() as session:
            configuration_obj = await self._get_user_configuration_row(
                session, user_id, UserConfigurationKey.MODEL_CONFIGURATION.value
            )
            if not configuration_obj:
                return EffectiveAIModelConfiguration()

            try:
                return EffectiveAIModelConfiguration.model_validate(
                    {
                        **configuration_obj.configuration,
                        "last_validated_at": configuration_obj.last_validated_at,
                    }
                )
            except ValidationError as e:
                # If configuration contains an unsupported provider,
                # return a default configuration without failing
                logger.warning(
                    f"Failed to validate user configuration for user {user_id}: {e}. "
                    "Returning default configuration."
                )
                return EffectiveAIModelConfiguration()

    async def update_user_configuration(
        self, user_id: int, configuration: EffectiveAIModelConfiguration
    ) -> EffectiveAIModelConfiguration:
        value = await self.upsert_user_configuration_value(
            user_id,
            UserConfigurationKey.MODEL_CONFIGURATION.value,
            configuration.model_dump(),
        )
        return EffectiveAIModelConfiguration.model_validate(value)

    async def update_user_configuration_last_validated_at(self, user_id: int) -> None:
        async with self.async_session() as session:
            configuration_obj = await self._get_user_configuration_row(
                session, user_id, UserConfigurationKey.MODEL_CONFIGURATION.value
            )
            if not configuration_obj:
                raise ValueError(f"User configuration with ID {user_id} not found")
            configuration_obj.last_validated_at = datetime.now()
            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e
            await session.refresh(configuration_obj)

    async def update_user_selected_organization(
        self, user_id: int, organization_id: int
    ) -> None:
        """Update the user's selected organization ID."""
        async with self.async_session() as session:
            from sqlalchemy import update

            # Use a direct UPDATE statement to avoid race conditions
            # This is atomic at the database level
            stmt = (
                update(UserModel)
                .where(UserModel.id == user_id)
                .values(selected_organization_id=organization_id)
            )

            result = await session.execute(stmt)

            if result.rowcount == 0:
                raise ValueError(f"User with ID {user_id} not found")

            await session.commit()

    async def update_user_email(self, user_id: int, email: str) -> None:
        """Update the user's email address."""
        async with self.async_session() as session:
            from sqlalchemy import update

            stmt = (
                update(UserModel)
                .where(UserModel.id == user_id)
                .values(email=email.lower())
            )
            await session.execute(stmt)
            await session.commit()

    async def get_user_by_email(self, email: str) -> UserModel | None:
        """Fetch a user by their email address (case-insensitive).

        Email addresses are case-insensitive in practice, so a user who
        signed up as "User@example.com" must still be found when they later
        log in as "user@example.com". Compare on lower(email) so lookups are
        robust to capitalization differences across sign-in flows.
        """
        normalized_email = email.lower()
        async with self.async_session() as session:
            result = await session.execute(
                select(UserModel).where(func.lower(UserModel.email) == normalized_email)
            )
            return result.scalars().first()

    async def create_user_with_email(
        self, email: str, password_hash: str, name: str | None = None
    ) -> UserModel:
        """Create a new user with email and password hash."""
        async with self.async_session() as session:
            user = UserModel(
                provider_id=f"oss_{int(datetime.now(timezone.utc).timestamp())}_{uuid.uuid4()}",
                email=email.lower(),
                password_hash=password_hash,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    async def save_otp(self, email: str, otp: str, purpose: str, expires_at: datetime) -> None:
        """Store an OTP code against a user record (or a pending-registration placeholder)."""
        normalized = email.lower()
        async with self.async_session() as session:
            result = await session.execute(
                select(UserModel).where(func.lower(UserModel.email) == normalized)
            )
            user = result.scalars().first()

            if user is None:
                user = UserModel(
                    provider_id=f"pending_{int(datetime.now(timezone.utc).timestamp())}_{uuid.uuid4()}",
                    email=normalized,
                )
                session.add(user)
                await session.flush()

            # Set OTP fields directly on the ORM object — avoids a separate UPDATE
            user.otp_code = otp
            user.otp_expires_at = expires_at
            user.otp_purpose = purpose
            await session.commit()

    async def verify_otp(self, email: str, otp: str, purpose: str) -> bool:
        """Return True if the OTP matches, has the right purpose, and hasn't expired."""
        normalized = email.lower()
        async with self.async_session() as session:
            result = await session.execute(
                select(UserModel).where(func.lower(UserModel.email) == normalized)
            )
            user = result.scalars().first()
            if not user:
                return False
            if user.otp_code != otp:
                return False
            if user.otp_purpose != purpose:
                return False
            if not user.otp_expires_at:
                return False
            expires = user.otp_expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            if expires < datetime.now(UTC):
                return False
            return True

    async def clear_otp(self, email: str) -> None:
        """Clear OTP fields after successful use."""
        normalized = email.lower()
        async with self.async_session() as session:
            result = await session.execute(
                select(UserModel).where(func.lower(UserModel.email) == normalized)
            )
            user = result.scalars().first()
            if user:
                user.otp_code = None
                user.otp_expires_at = None
                user.otp_purpose = None
                await session.commit()

    async def set_password(self, email: str, password_hash: str) -> None:
        """Update the password hash for a user (used after OTP-verified reset)."""
        normalized = email.lower()
        async with self.async_session() as session:
            result = await session.execute(
                select(UserModel).where(func.lower(UserModel.email) == normalized)
            )
            user = result.scalars().first()
            if user:
                user.password_hash = password_hash
                await session.commit()

    async def complete_signup(
        self, email: str, password_hash: str, name: str | None = None
    ) -> UserModel:
        """Finalize a stub user created during OTP send by setting the password hash."""
        normalized = email.lower()
        async with self.async_session() as session:
            result = await session.execute(
                select(UserModel).where(func.lower(UserModel.email) == normalized)
            )
            user = result.scalars().first()

            if user:
                new_provider_id = f"oss_{int(datetime.now(timezone.utc).timestamp())}_{uuid.uuid4()}"
                # Update fields directly on the ORM object — no second query needed
                user.password_hash = password_hash
                user.provider_id = new_provider_id
                user.otp_code = None
                user.otp_expires_at = None
                user.otp_purpose = None
                await session.commit()
                await session.refresh(user)
                return user
            else:
                # No stub — create fresh
                user = UserModel(
                    provider_id=f"oss_{int(datetime.now(timezone.utc).timestamp())}_{uuid.uuid4()}",
                    email=normalized,
                    password_hash=password_hash,
                )
                session.add(user)
                await session.commit()
                await session.refresh(user)
                return user
