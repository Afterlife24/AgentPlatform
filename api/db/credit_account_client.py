"""Credit account database client for prepaid wallet operations.

This module provides database access for organization credit accounts and ledger entries.
All money amounts are stored in USD as Decimal with 6 decimal places.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert

from api.db.base_client import BaseDBClient
from api.db.models import CreditLedgerEntryModel, OrganizationCreditAccountModel
from api.enums import CreditLedgerEntryType


class CreditAccountClient(BaseDBClient):
    """Database client for credit wallet operations."""

    async def apply_ledger_entry(
        self,
        organization_id: int,
        entry_type: CreditLedgerEntryType,
        amount_usd: Decimal,
        idempotency_key: str,
        workflow_run_id: Optional[int] = None,
        tokens_charged: Optional[int] = None,
        paid_amount_minor: Optional[int] = None,
        paid_currency: Optional[str] = None,
        fx_rate_to_usd: Optional[Decimal] = None,
        payment_provider_reference: Optional[str] = None,
        memo: Optional[str] = None,
    ) -> CreditLedgerEntryModel:
        """Apply a credit ledger entry atomically with idempotency.

        This is the core wallet mutation primitive. It:
        1. Locks the account row (SELECT FOR UPDATE)
        2. Checks idempotency (if entry exists, returns existing entry)
        3. Calculates new balance
        4. Inserts ledger entry
        5. Updates account balance
        All within a single transaction.

        Args:
            organization_id: Organization owning the account
            entry_type: Type of entry (topup, debit_call, refund, etc.)
            amount_usd: Signed amount in USD (Decimal). Positive = credit, negative = debit
            idempotency_key: Unique key to prevent duplicate entries (workflow_run_id for debits)
            workflow_run_id: Optional workflow run that triggered this entry
            tokens_charged: Optional Dograh tokens consumed (call debits only)
            paid_amount_minor: Optional payment amount in minor units (topups only)
            paid_currency: Optional ISO 4217 currency code (topups only)
            fx_rate_to_usd: Optional exchange rate used (topups only)
            payment_provider_reference: Optional payment gateway transaction ID
            memo: Optional human-readable note for adjustments/refunds

        Returns:
            The created or existing ledger entry

        Raises:
            ValueError: If account doesn't exist or amount_usd is zero
        """
        if amount_usd == 0:
            raise ValueError("amount_usd cannot be zero")

        async with self.async_session() as session:
            # Check idempotency first (without locking) - fast path for duplicates
            existing_entry_result = await session.execute(
                select(CreditLedgerEntryModel).where(
                    CreditLedgerEntryModel.idempotency_key == idempotency_key
                )
            )
            existing_entry = existing_entry_result.scalars().first()
            if existing_entry:
                return existing_entry

            # Lock the account row for update to prevent concurrent mutations
            account_result = await session.execute(
                select(OrganizationCreditAccountModel)
                .where(OrganizationCreditAccountModel.organization_id == organization_id)
                .with_for_update()
            )
            account = account_result.scalars().first()

            if not account:
                raise ValueError(
                    f"No credit account found for organization_id={organization_id}"
                )

            # Calculate new balance
            old_balance = account.balance_usd
            new_balance = old_balance + amount_usd

            # Create ledger entry
            ledger_entry = CreditLedgerEntryModel(
                organization_id=organization_id,
                account_id=account.id,
                entry_type=entry_type.value,
                amount_usd=amount_usd,
                balance_after_usd=new_balance,
                idempotency_key=idempotency_key,
                workflow_run_id=workflow_run_id,
                tokens_charged=tokens_charged,
                paid_amount_minor=paid_amount_minor,
                paid_currency=paid_currency,
                fx_rate_to_usd=fx_rate_to_usd,
                payment_provider_reference=payment_provider_reference,
                memo=memo,
                created_at=datetime.now(UTC),
            )
            session.add(ledger_entry)

            # Update account balance and timestamp
            account.balance_usd = new_balance
            account.updated_at = datetime.now(UTC)

            # Commit the transaction
            await session.commit()
            await session.refresh(ledger_entry)

            return ledger_entry

    async def grant_topup(
        self,
        organization_id: int,
        amount_usd: Decimal,
        idempotency_key: str,
        display_currency: str,
        display_fx_rate_to_usd: Decimal,
        paid_amount_minor: Optional[int] = None,
        paid_currency: Optional[str] = None,
        fx_rate_to_usd: Optional[Decimal] = None,
        payment_provider_reference: Optional[str] = None,
        memo: Optional[str] = None,
    ) -> tuple[CreditLedgerEntryModel, bool]:
        """Apply a topup AND set the account's display currency in one transaction.

        This is the grant primitive for the internal credits API. Unlike
        apply_ledger_entry, it also updates display_currency and
        display_fx_rate_to_usd on the account so the operator's currency choice
        shows up on the customer's billing screen. Both writes happen in the
        same locked transaction.

        Args:
            organization_id: Organization owning the account
            amount_usd: Positive USD amount to credit (Decimal)
            idempotency_key: Unique key to prevent duplicate grants
            display_currency: ISO 4217 code to display balances in going forward
            display_fx_rate_to_usd: Rate used for display (1 display_currency = X USD)
            paid_amount_minor: Payment amount in minor units (provenance)
            paid_currency: ISO 4217 currency the customer paid in (provenance)
            fx_rate_to_usd: Exchange rate applied to the payment (provenance)
            payment_provider_reference: Payment gateway reference
            memo: Human-readable note

        Returns:
            Tuple of (ledger_entry, was_duplicate). When was_duplicate is True the
            returned entry is the original and the balance was NOT changed again.

        Raises:
            ValueError: If account doesn't exist or amount_usd is zero
        """
        if amount_usd == 0:
            raise ValueError("amount_usd cannot be zero")

        async with self.async_session() as session:
            # Fast-path idempotency check
            existing_entry_result = await session.execute(
                select(CreditLedgerEntryModel).where(
                    CreditLedgerEntryModel.idempotency_key == idempotency_key
                )
            )
            existing_entry = existing_entry_result.scalars().first()
            if existing_entry:
                return existing_entry, True

            # Lock the account row
            account_result = await session.execute(
                select(OrganizationCreditAccountModel)
                .where(
                    OrganizationCreditAccountModel.organization_id == organization_id
                )
                .with_for_update()
            )
            account = account_result.scalars().first()

            if not account:
                raise ValueError(
                    f"No credit account found for organization_id={organization_id}"
                )

            old_balance = account.balance_usd
            new_balance = old_balance + amount_usd

            ledger_entry = CreditLedgerEntryModel(
                organization_id=organization_id,
                account_id=account.id,
                entry_type=CreditLedgerEntryType.TOPUP.value,
                amount_usd=amount_usd,
                balance_after_usd=new_balance,
                idempotency_key=idempotency_key,
                paid_amount_minor=paid_amount_minor,
                paid_currency=paid_currency,
                fx_rate_to_usd=fx_rate_to_usd,
                payment_provider_reference=payment_provider_reference,
                memo=memo,
                created_at=datetime.now(UTC),
            )
            session.add(ledger_entry)

            # Update balance AND display metadata in the same transaction
            account.balance_usd = new_balance
            account.display_currency = display_currency
            account.display_fx_rate_to_usd = display_fx_rate_to_usd
            account.updated_at = datetime.now(UTC)

            await session.commit()
            await session.refresh(ledger_entry)

            return ledger_entry, False

    async def get_ledger_count(self, organization_id: int) -> int:
        """Count total ledger entries for an organization (for pagination)."""
        from sqlalchemy import func

        async with self.async_session() as session:
            result = await session.execute(
                select(func.count())
                .select_from(CreditLedgerEntryModel)
                .where(CreditLedgerEntryModel.organization_id == organization_id)
            )
            return result.scalar_one()

    async def get_account(
        self, organization_id: int
    ) -> Optional[OrganizationCreditAccountModel]:
        """Get credit account for an organization.

        Args:
            organization_id: Organization ID

        Returns:
            Credit account or None if not found
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(OrganizationCreditAccountModel).where(
                    OrganizationCreditAccountModel.organization_id == organization_id
                )
            )
            return result.scalars().first()

    async def get_or_create_account(
        self, organization_id: int
    ) -> OrganizationCreditAccountModel:
        """Get or create a credit account for an organization.

        This uses INSERT ... ON CONFLICT DO NOTHING to handle race conditions.
        If the account already exists, it returns the existing account.

        Args:
            organization_id: Organization ID

        Returns:
            The credit account (existing or newly created)

        Raises:
            ValueError: If organization doesn't exist (FK constraint violation)
        """
        async with self.async_session() as session:
            # First try to get existing account
            result = await session.execute(
                select(OrganizationCreditAccountModel).where(
                    OrganizationCreditAccountModel.organization_id == organization_id
                )
            )
            account = result.scalars().first()

            if account:
                return account

            # Use INSERT ... ON CONFLICT DO NOTHING for atomicity
            stmt = insert(OrganizationCreditAccountModel.__table__).values(
                organization_id=organization_id,
                balance_usd=Decimal("0"),
                display_currency="USD",
                display_fx_rate_to_usd=Decimal("1"),
                low_balance_threshold_usd=Decimal("0"),
                is_blocked=False,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            stmt = stmt.on_conflict_do_nothing(index_elements=["organization_id"])

            await session.execute(stmt)
            await session.commit()

            # Fetch the account (either newly created or existing from race condition)
            result = await session.execute(
                select(OrganizationCreditAccountModel).where(
                    OrganizationCreditAccountModel.organization_id == organization_id
                )
            )
            account = result.scalars().first()

            if not account:
                raise ValueError(
                    f"Failed to create or fetch account for organization_id={organization_id}"
                )

            return account

    async def get_ledger(
        self,
        organization_id: int,
        limit: int = 100,
        offset: int = 0,
    ) -> list[CreditLedgerEntryModel]:
        """Get ledger entries for an organization (newest first).

        Args:
            organization_id: Organization ID
            limit: Maximum number of entries to return (default 100)
            offset: Number of entries to skip (default 0)

        Returns:
            List of ledger entries ordered by created_at DESC
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(CreditLedgerEntryModel)
                .where(CreditLedgerEntryModel.organization_id == organization_id)
                .order_by(desc(CreditLedgerEntryModel.created_at))
                .limit(limit)
                .offset(offset)
            )
            return list(result.scalars().all())

    async def get_entry_by_idempotency_key(
        self, idempotency_key: str
    ) -> Optional[CreditLedgerEntryModel]:
        """Get a ledger entry by its idempotency key.

        Args:
            idempotency_key: Unique idempotency key

        Returns:
            Ledger entry or None if not found
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(CreditLedgerEntryModel).where(
                    CreditLedgerEntryModel.idempotency_key == idempotency_key
                )
            )
            return result.scalars().first()

    async def update_account_settings(
        self,
        organization_id: int,
        display_currency: Optional[str] = None,
        display_fx_rate_to_usd: Optional[Decimal] = None,
        low_balance_threshold_usd: Optional[Decimal] = None,
        is_blocked: Optional[bool] = None,
    ) -> OrganizationCreditAccountModel:
        """Update account settings (display currency, threshold, block status).

        Args:
            organization_id: Organization ID
            display_currency: Optional ISO 4217 currency code for UI display
            display_fx_rate_to_usd: Optional exchange rate (1 display_currency = X USD)
            low_balance_threshold_usd: Optional alert threshold in USD
            is_blocked: Optional flag to prevent new workflow runs

        Returns:
            Updated credit account

        Raises:
            ValueError: If account doesn't exist
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(OrganizationCreditAccountModel).where(
                    OrganizationCreditAccountModel.organization_id == organization_id
                )
            )
            account = result.scalars().first()

            if not account:
                raise ValueError(
                    f"No credit account found for organization_id={organization_id}"
                )

            # Update only provided fields
            if display_currency is not None:
                account.display_currency = display_currency
            if display_fx_rate_to_usd is not None:
                account.display_fx_rate_to_usd = display_fx_rate_to_usd
            if low_balance_threshold_usd is not None:
                account.low_balance_threshold_usd = low_balance_threshold_usd
            if is_blocked is not None:
                account.is_blocked = is_blocked

            account.updated_at = datetime.now(UTC)

            await session.commit()
            await session.refresh(account)

            return account
