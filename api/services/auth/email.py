"""Email service for OTP delivery (supports Resend HTTP API, SendGrid HTTP API, and SMTP)."""

import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib
import httpx
from loguru import logger

from api.constants import (
    DEPLOYMENT_MODE,
    ENVIRONMENT,
    SMTP_FROM_EMAIL,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
)


async def send_otp_email(to_email: str, otp: str, purpose: str) -> None:
    """Send a 6-digit OTP to the given address.

    purpose: "signup" | "reset"
    """
    if purpose == "signup":
        subject = "Your verification code"
        body_text = f"Your signup verification code is: {otp}\n\nIt expires in 10 minutes."
        body_html = f"""
        <div style="font-family:sans-serif;max-width:480px;margin:0 auto">
          <h2 style="color:#1a1a1a">Verify your email</h2>
          <p>Use the code below to complete your signup:</p>
          <div style="background:#f4f4f5;border-radius:8px;padding:24px;text-align:center;margin:24px 0">
            <span style="font-size:36px;font-weight:700;letter-spacing:8px;color:#1a1a1a">{otp}</span>
          </div>
          <p style="color:#71717a;font-size:14px">This code expires in <strong>10 minutes</strong>. If you didn't request this, ignore this email.</p>
        </div>
        """
    else:
        subject = "Reset your password"
        body_text = f"Your password reset code is: {otp}\n\nIt expires in 10 minutes."
        body_html = f"""
        <div style="font-family:sans-serif;max-width:480px;margin:0 auto">
          <h2 style="color:#1a1a1a">Reset your password</h2>
          <p>Use the code below to reset your password:</p>
          <div style="background:#f4f4f5;border-radius:8px;padding:24px;text-align:center;margin:24px 0">
            <span style="font-size:36px;font-weight:700;letter-spacing:8px;color:#1a1a1a">{otp}</span>
          </div>
          <p style="color:#71717a;font-size:14px">This code expires in <strong>10 minutes</strong>. If you didn't request this, ignore this email.</p>
        </div>
        """

    resend_api_key = os.getenv("RESEND_API_KEY")
    resend_from_email = os.getenv("RESEND_FROM_EMAIL") or "onboarding@resend.dev"
    sendgrid_api_key = os.getenv("SENDGRID_API_KEY")
    sendgrid_from_email = os.getenv("SENDGRID_FROM_EMAIL") or os.getenv("SMTP_FROM_EMAIL") or "noreply@dograh.com"
    smtp_host = os.getenv("SMTP_HOST") or SMTP_HOST
    smtp_port = int(os.getenv("SMTP_PORT") or SMTP_PORT)
    smtp_from_email = os.getenv("SMTP_FROM_EMAIL") or SMTP_FROM_EMAIL
    smtp_password = os.getenv("SMTP_PASSWORD") or SMTP_PASSWORD

    # 1. First priority: Resend HTTP API (works over Port 443 HTTPS - immune to ISP SMTP port blocks)
    if resend_api_key:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {resend_api_key}"},
                    json={
                        "from": resend_from_email,
                        "to": [to_email],
                        "subject": subject,
                        "html": body_html,
                    },
                )
                if res.status_code in (200, 201):
                    logger.info(f"OTP email successfully sent via Resend API to {to_email} (purpose={purpose})")
                    return
                else:
                    logger.error(f"Resend API error ({res.status_code}): {res.text}")
        except Exception as exc:
            logger.error(f"Failed to send email via Resend API: {exc}")

    # 2. Second priority: SendGrid HTTP API (works over Port 443 HTTPS)
    if sendgrid_api_key:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(
                    "https://api.sendgrid.com/v3/mail/send",
                    headers={
                        "Authorization": f"Bearer {sendgrid_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "personalizations": [{"to": [{"email": to_email}]}],
                        "from": {"email": sendgrid_from_email},
                        "subject": subject,
                        "content": [
                            {"type": "text/plain", "value": body_text},
                            {"type": "text/html", "value": body_html},
                        ],
                    },
                )
                if res.status_code in (200, 202):
                    logger.info(f"OTP email successfully sent via SendGrid API to {to_email} (purpose={purpose})")
                    return
                else:
                    logger.error(f"SendGrid API error ({res.status_code}): {res.text}")
        except Exception as exc:
            logger.error(f"Failed to send email via SendGrid API: {exc}")

    # 3. Third priority: SMTP (ports 587 or 465)
    if not smtp_password or not smtp_from_email:
        logger.warning(
            f"No email provider configured (set RESEND_API_KEY or SMTP_PASSWORD in .env). "
            f"=== OTP VERIFICATION CODE ({purpose}) for {to_email}: {otp} ==="
        )
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_from_email
    msg["To"] = to_email
    msg.attach(MIMEText(body_text, "plain"))
    msg.attach(MIMEText(body_html, "html"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=smtp_host,
            port=smtp_port,
            username=smtp_from_email,
            password=smtp_password,
            use_tls=(smtp_port == 465),
            start_tls=(smtp_port != 465),
            timeout=10,
        )
        logger.info(f"OTP email sent via SMTP to {to_email} (purpose={purpose})")
    except Exception as e:
        logger.error(f"Failed to send OTP email to {to_email} via SMTP: {e}")
        if ENVIRONMENT == "local" or DEPLOYMENT_MODE == "oss":
            logger.warning(
                f"[LOCAL/OSS] Falling back after SMTP connection failure. "
                f"=== VERIFICATION CODE ({purpose}) for {to_email}: {otp} ==="
            )
            return
        raise

