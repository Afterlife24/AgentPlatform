"""Database client for dynamic model pricing (Stage A).

Covers the four pricing tables:
  - model_pricing           versioned per-model rates (chat/embedding/tts/stt)
  - model_provider_settings per-provider enable/disable
  - model_pricing_pending   LiteLLM sync approval queue
  - model_usage_audit        immutable per-run billing audit

All money fields are Decimal. Versioning rule: a price change never mutates an
existing row — the current row (effective_to IS NULL) is closed by setting
effective_to, and a new current row is inserted.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, select, update

from api.db.base_client import BaseDBClient
from api.db.models import (
    ModelPricingModel,
    ModelPricingPendingModel,
    ModelProviderSettingsModel,
    ModelUsageAuditModel,
)
from api.enums import (
    ModelPricingChangeType,
    ModelPricingKind,
    ModelPricingSource,
    ModelPricingStatus,
    ModelPricingUnit,
)


class ModelPricingClient(BaseDBClient):
    """DB access for the dynamic model pricing subsystem."""

    # ─────────────────────────── model_pricing reads ──────────────────────────

    async def get_active_price(
        self, provider: str, model_identifier: str
    ) -> Optional[ModelPricingModel]:
        """Return the current (effective_to IS NULL) price row for a model."""
        async with self.async_session() as session:
            result = await session.execute(
                select(ModelPricingModel).where(
                    and_(
                        ModelPricingModel.provider == provider,
                        ModelPricingModel.model_identifier == model_identifier,
                        ModelPricingModel.effective_to.is_(None),
                    )
                )
            )
            return result.scalars().first()

    async def get_active_price_by_model(
        self, model_identifier: str, kind: Optional[ModelPricingKind] = None
    ) -> Optional[ModelPricingModel]:
        """Return the current price row for a model id, optionally scoped by kind.

        Billing knows the model name from usage_info but not always the provider,
        so this looks up by model id across providers (model ids are unique in
        practice). When kind is given it disambiguates (e.g. an embedding vs chat
        row that share a provider).
        """
        async with self.async_session() as session:
            conditions = [
                ModelPricingModel.model_identifier == model_identifier,
                ModelPricingModel.effective_to.is_(None),
            ]
            if kind is not None:
                conditions.append(ModelPricingModel.kind == kind.value)
            result = await session.execute(
                select(ModelPricingModel).where(and_(*conditions))
            )
            return result.scalars().first()

    async def list_pricing(
        self,
        *,
        current_only: bool = True,
        kind: Optional[ModelPricingKind] = None,
    ) -> list[ModelPricingModel]:
        """List pricing rows for the admin table. current_only hides history."""
        async with self.async_session() as session:
            conditions = []
            if current_only:
                conditions.append(ModelPricingModel.effective_to.is_(None))
            if kind is not None:
                conditions.append(ModelPricingModel.kind == kind.value)
            stmt = select(ModelPricingModel)
            if conditions:
                stmt = stmt.where(and_(*conditions))
            stmt = stmt.order_by(
                ModelPricingModel.provider, ModelPricingModel.model_identifier
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_pricing_by_id(self, pricing_id: int) -> Optional[ModelPricingModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(ModelPricingModel).where(ModelPricingModel.id == pricing_id)
            )
            return result.scalars().first()

    # ────────────────────────── model_pricing writes ──────────────────────────

    async def upsert_price_version(
        self,
        *,
        provider: str,
        model_identifier: str,
        kind: ModelPricingKind,
        source: ModelPricingSource,
        unit: ModelPricingUnit,
        input_per_1m: Optional[Decimal] = None,
        output_per_1m: Optional[Decimal] = None,
        cached_input_per_1m: Optional[Decimal] = None,
        embedding_per_1m: Optional[Decimal] = None,
        rt_text_input_per_1m: Optional[Decimal] = None,
        rt_text_output_per_1m: Optional[Decimal] = None,
        rt_audio_input_per_1m: Optional[Decimal] = None,
        rt_audio_output_per_1m: Optional[Decimal] = None,
        rate: Optional[Decimal] = None,
        status: ModelPricingStatus = ModelPricingStatus.ACTIVE,
        is_enabled: Optional[bool] = None,
    ) -> ModelPricingModel:
        """Insert a new current price version, closing any existing current row.

        If a current row exists, its enable state is preserved unless is_enabled
        is explicitly provided (a price change shouldn't silently re-enable or
        disable a model). A brand-new model defaults to disabled.
        """
        now = datetime.now(UTC)
        async with self.async_session() as session:
            existing_result = await session.execute(
                select(ModelPricingModel).where(
                    and_(
                        ModelPricingModel.provider == provider,
                        ModelPricingModel.model_identifier == model_identifier,
                        ModelPricingModel.effective_to.is_(None),
                    )
                )
            )
            existing = existing_result.scalars().first()

            resolved_enabled = is_enabled
            if resolved_enabled is None:
                resolved_enabled = existing.is_enabled if existing else False

            if existing:
                existing.effective_to = now
                existing.updated_at = now

            new_row = ModelPricingModel(
                provider=provider,
                model_identifier=model_identifier,
                kind=kind.value,
                source=source.value,
                unit=unit.value,
                input_per_1m=input_per_1m,
                output_per_1m=output_per_1m,
                cached_input_per_1m=cached_input_per_1m,
                embedding_per_1m=embedding_per_1m,
                rt_text_input_per_1m=rt_text_input_per_1m,
                rt_text_output_per_1m=rt_text_output_per_1m,
                rt_audio_input_per_1m=rt_audio_input_per_1m,
                rt_audio_output_per_1m=rt_audio_output_per_1m,
                rate=rate,
                status=status.value,
                is_enabled=resolved_enabled,
                effective_from=now,
                effective_to=None,
                created_at=now,
                updated_at=now,
            )
            session.add(new_row)
            await session.commit()
            await session.refresh(new_row)
            return new_row

    async def set_model_enabled(
        self, pricing_id: int, is_enabled: bool
    ) -> Optional[ModelPricingModel]:
        """Enable/disable a single current price row (admin toggle)."""
        async with self.async_session() as session:
            result = await session.execute(
                select(ModelPricingModel).where(ModelPricingModel.id == pricing_id)
            )
            row = result.scalars().first()
            if not row:
                return None
            row.is_enabled = is_enabled
            row.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(row)
            return row

    async def set_model_status(
        self, provider: str, model_identifier: str, status: ModelPricingStatus
    ) -> Optional[ModelPricingModel]:
        """Set the active/deprecated status on the current row for a model."""
        async with self.async_session() as session:
            result = await session.execute(
                select(ModelPricingModel).where(
                    and_(
                        ModelPricingModel.provider == provider,
                        ModelPricingModel.model_identifier == model_identifier,
                        ModelPricingModel.effective_to.is_(None),
                    )
                )
            )
            row = result.scalars().first()
            if not row:
                return None
            row.status = status.value
            row.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(row)
            return row

    async def delete_pricing(self, pricing_id: int) -> bool:
        """Hard-delete a pricing row (used for removing a manual STT/TTS entry)."""
        async with self.async_session() as session:
            result = await session.execute(
                select(ModelPricingModel).where(ModelPricingModel.id == pricing_id)
            )
            row = result.scalars().first()
            if not row:
                return False
            await session.delete(row)
            await session.commit()
            return True

    # ───────────────────────── provider settings ──────────────────────────────

    async def get_provider_settings(
        self, provider: str
    ) -> Optional[ModelProviderSettingsModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(ModelProviderSettingsModel).where(
                    ModelProviderSettingsModel.provider == provider
                )
            )
            return result.scalars().first()

    async def list_provider_settings(self) -> list[ModelProviderSettingsModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(ModelProviderSettingsModel).order_by(
                    ModelProviderSettingsModel.provider
                )
            )
            return list(result.scalars().all())

    async def set_provider_enabled(
        self, provider: str, is_enabled: bool
    ) -> ModelProviderSettingsModel:
        """Enable/disable a provider, creating the settings row if absent."""
        now = datetime.now(UTC)
        async with self.async_session() as session:
            result = await session.execute(
                select(ModelProviderSettingsModel).where(
                    ModelProviderSettingsModel.provider == provider
                )
            )
            row = result.scalars().first()
            if row:
                row.is_enabled = is_enabled
                row.updated_at = now
            else:
                row = ModelProviderSettingsModel(
                    provider=provider,
                    is_enabled=is_enabled,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    # ───────────────────────── pending (approval queue) ───────────────────────

    async def clear_pending(self) -> int:
        """Remove all pending rows (a fresh sync replaces the diff)."""
        async with self.async_session() as session:
            result = await session.execute(select(ModelPricingPendingModel))
            rows = list(result.scalars().all())
            for row in rows:
                await session.delete(row)
            await session.commit()
            return len(rows)

    async def add_pending(
        self,
        *,
        provider: str,
        model_identifier: str,
        kind: ModelPricingKind,
        change_type: ModelPricingChangeType,
        sync_run_id: Optional[str] = None,
        proposed_input_per_1m: Optional[Decimal] = None,
        proposed_output_per_1m: Optional[Decimal] = None,
        proposed_cached_input_per_1m: Optional[Decimal] = None,
        proposed_embedding_per_1m: Optional[Decimal] = None,
        proposed_rt_text_input_per_1m: Optional[Decimal] = None,
        proposed_rt_text_output_per_1m: Optional[Decimal] = None,
        proposed_rt_audio_input_per_1m: Optional[Decimal] = None,
        proposed_rt_audio_output_per_1m: Optional[Decimal] = None,
        proposed_status: Optional[ModelPricingStatus] = None,
        current_input_per_1m: Optional[Decimal] = None,
        current_output_per_1m: Optional[Decimal] = None,
        current_cached_input_per_1m: Optional[Decimal] = None,
        current_embedding_per_1m: Optional[Decimal] = None,
        current_rt_text_input_per_1m: Optional[Decimal] = None,
        current_rt_text_output_per_1m: Optional[Decimal] = None,
        current_rt_audio_input_per_1m: Optional[Decimal] = None,
        current_rt_audio_output_per_1m: Optional[Decimal] = None,
    ) -> ModelPricingPendingModel:
        async with self.async_session() as session:
            row = ModelPricingPendingModel(
                provider=provider,
                model_identifier=model_identifier,
                kind=kind.value,
                change_type=change_type.value,
                sync_run_id=sync_run_id,
                proposed_input_per_1m=proposed_input_per_1m,
                proposed_output_per_1m=proposed_output_per_1m,
                proposed_cached_input_per_1m=proposed_cached_input_per_1m,
                proposed_embedding_per_1m=proposed_embedding_per_1m,
                proposed_rt_text_input_per_1m=proposed_rt_text_input_per_1m,
                proposed_rt_text_output_per_1m=proposed_rt_text_output_per_1m,
                proposed_rt_audio_input_per_1m=proposed_rt_audio_input_per_1m,
                proposed_rt_audio_output_per_1m=proposed_rt_audio_output_per_1m,
                proposed_status=proposed_status.value if proposed_status else None,
                current_input_per_1m=current_input_per_1m,
                current_output_per_1m=current_output_per_1m,
                current_cached_input_per_1m=current_cached_input_per_1m,
                current_embedding_per_1m=current_embedding_per_1m,
                current_rt_text_input_per_1m=current_rt_text_input_per_1m,
                current_rt_text_output_per_1m=current_rt_text_output_per_1m,
                current_rt_audio_input_per_1m=current_rt_audio_input_per_1m,
                current_rt_audio_output_per_1m=current_rt_audio_output_per_1m,
                detected_at=datetime.now(UTC),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def list_pending(self) -> list[ModelPricingPendingModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(ModelPricingPendingModel).order_by(
                    ModelPricingPendingModel.provider,
                    ModelPricingPendingModel.model_identifier,
                )
            )
            return list(result.scalars().all())

    async def get_pending_by_id(
        self, pending_id: int
    ) -> Optional[ModelPricingPendingModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(ModelPricingPendingModel).where(
                    ModelPricingPendingModel.id == pending_id
                )
            )
            return result.scalars().first()

    async def delete_pending(self, pending_id: int) -> bool:
        async with self.async_session() as session:
            result = await session.execute(
                select(ModelPricingPendingModel).where(
                    ModelPricingPendingModel.id == pending_id
                )
            )
            row = result.scalars().first()
            if not row:
                return False
            await session.delete(row)
            await session.commit()
            return True

    # ─────────────────────────── usage audit ──────────────────────────────────

    async def add_usage_audit(
        self,
        *,
        workflow_run_id: Optional[int],
        computed_charge_usd: Decimal,
        chat_model: Optional[str] = None,
        embedding_model: Optional[str] = None,
        tts_model: Optional[str] = None,
        stt_model: Optional[str] = None,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        embedding_tokens: Optional[int] = None,
        tts_characters: Optional[int] = None,
        stt_seconds: Optional[Decimal] = None,
        chat_pricing_version_id: Optional[int] = None,
    ) -> ModelUsageAuditModel:
        async with self.async_session() as session:
            row = ModelUsageAuditModel(
                workflow_run_id=workflow_run_id,
                chat_model=chat_model,
                embedding_model=embedding_model,
                tts_model=tts_model,
                stt_model=stt_model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                embedding_tokens=embedding_tokens,
                tts_characters=tts_characters,
                stt_seconds=stt_seconds,
                chat_pricing_version_id=chat_pricing_version_id,
                computed_charge_usd=computed_charge_usd,
                created_at=datetime.now(UTC),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row
