"""Wallet balance alert emails.

Emails the organization's account owner when the prepaid wallet runs low:
  - "low":       balance drops to or below LOW_BALANCE_ALERT_USD (default $2)
  - "exhausted": balance drops to or below $0

Call check_wallet_balance_alerts(organization_id) after any change to the
wallet balance (debit, adjustment, top-up). Each alert is sent once per drop:
the account row records when it was sent, and the flag is cleared once the
balance rises back above that level (e.g. after a top-up), re-arming it.

This module never raises. A failed alert must never break billing.
"""

from decimal import Decimal

from loguru import logger

from api.constants import LOW_BALANCE_ALERT_USD, UI_APP_URL
from api.db import db_client
from api.services.auth.email import is_email_configured, send_email


async def check_wallet_balance_alerts(organization_id: int) -> None:
    """Send any balance alert email that became due for this organization."""
    try:
        if not is_email_configured():
            # Don't claim alerts we can't deliver; they stay armed until SMTP
            # is configured.
            logger.debug(
                "Skipping wallet balance alerts for org {}: SMTP not configured",
                organization_id,
            )
            return

        claimed, account = await db_client.claim_balance_alerts(
            organization_id, LOW_BALANCE_ALERT_USD
        )
        if not claimed or account is None:
            return

        owner = await db_client.get_primary_user_for_organization(organization_id)
        if owner is None or not owner.email:
            logger.warning(
                "Wallet balance alert {} for org {} not sent: no owner email",
                claimed,
                organization_id,
            )
            return

        for kind in claimed:
            try:
                subject, text, html = _render_alert(
                    kind,
                    balance_usd=account.balance_usd,
                    display_currency=account.display_currency,
                    display_fx_rate_to_usd=account.display_fx_rate_to_usd,
                )
                await send_email(owner.email, subject, text, html)
                logger.info(
                    "Sent wallet '{}' alert to {} for org {} (balance ${})",
                    kind,
                    owner.email,
                    organization_id,
                    account.balance_usd,
                )
            except Exception as e:
                logger.error(
                    "Failed to send wallet '{}' alert for org {}: {}",
                    kind,
                    organization_id,
                    e,
                )
                # Re-arm so the next balance change retries the email.
                try:
                    await db_client.release_balance_alert(organization_id, kind)
                except Exception as release_error:
                    logger.error(
                        "Failed to re-arm wallet '{}' alert for org {}: {}",
                        kind,
                        organization_id,
                        release_error,
                    )
    except Exception as e:
        logger.error(
            "Wallet balance alert check failed for org {}: {}", organization_id, e
        )


def _format_balance(
    balance_usd: Decimal, display_currency: str, display_fx_rate_to_usd: Decimal
) -> str:
    """Format the balance in USD, plus the display currency when it differs."""
    # Lazy import: credit_account_service calls into this module.
    from api.services.credit_account_service import (
        _display_rate_from_account,
        to_display,
    )

    usd = to_display(balance_usd, "USD")
    formatted = f"${usd} USD"
    currency = (display_currency or "USD").upper()
    if currency != "USD":
        try:
            shown = to_display(
                balance_usd,
                currency,
                _display_rate_from_account(currency, display_fx_rate_to_usd),
            )
            formatted = f"{shown} {currency} ({formatted})"
        except ValueError:
            pass
    return formatted


def _render_alert(
    kind: str,
    balance_usd: Decimal,
    display_currency: str,
    display_fx_rate_to_usd: Decimal,
) -> tuple[str, str, str]:
    """Build (subject, plain text, HTML) for a balance alert."""
    balance = _format_balance(balance_usd, display_currency, display_fx_rate_to_usd)
    billing_url = f"{UI_APP_URL.rstrip('/')}/billing"
    threshold = to_usd_string(LOW_BALANCE_ALERT_USD)

    if kind == "low":
        subject = "Low wallet balance warning"
        heading = "Your wallet balance is running low"
        message = (
            f"Your wallet balance has dropped to {balance}, at or below the "
            f"${threshold} low-balance level. Top up soon to keep your agents "
            f"running without interruption."
        )
        accent = "#b45309"
    elif kind == "exhausted":
        subject = "Wallet balance exhausted"
        heading = "Your wallet balance is exhausted"
        message = (
            f"Your wallet balance has reached {balance}. New calls and chats "
            f"may be refused until you top up your wallet."
        )
        accent = "#b91c1c"
    else:
        raise ValueError(f"Unknown balance alert kind: {kind}")

    text = (
        f"{heading}\n\n{message}\n\n"
        f"View your balance and top up: {billing_url}\n"
    )
    html = f"""
        <div style="font-family:sans-serif;max-width:480px;margin:0 auto">
          <h2 style="color:{accent}">{heading}</h2>
          <p style="color:#1a1a1a">{message}</p>
          <div style="background:#f4f4f5;border-radius:8px;padding:24px;text-align:center;margin:24px 0">
            <div style="color:#71717a;font-size:14px">Current balance</div>
            <div style="font-size:28px;font-weight:700;color:#1a1a1a">{balance}</div>
          </div>
          <p><a href="{billing_url}" style="color:#2563eb">View your balance and top up</a></p>
        </div>
        """
    return subject, text, html


def to_usd_string(amount: Decimal) -> str:
    """Two-decimal USD string, e.g. Decimal('2') -> '2.00'."""
    return f"{amount.quantize(Decimal('0.01'))}"
