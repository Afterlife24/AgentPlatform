"""Gmail SMTP email service for OTP delivery."""

import aiosmtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from loguru import logger

from api.constants import SMTP_FROM_EMAIL, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT


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

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM_EMAIL
    msg["To"] = to_email
    msg.attach(MIMEText(body_text, "plain"))
    msg.attach(MIMEText(body_html, "html"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=SMTP_HOST,
            port=SMTP_PORT,
            username=SMTP_FROM_EMAIL,
            password=SMTP_PASSWORD,
            use_tls=False,
            start_tls=True,
        )
        logger.info(f"OTP email sent to {to_email} (purpose={purpose})")
    except Exception as e:
        logger.error(f"Failed to send OTP email to {to_email}: {e}")
        raise
