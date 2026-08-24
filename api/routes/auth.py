import random
import string
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from api.db import db_client
from api.db.models import UserModel
from api.enums import OrganizationConfigurationKey, PostHogEvent
from api.schemas.auth import (
    AuthResponse,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    ResetPasswordRequest,
    SendSignupOtpRequest,
    UserResponse,
    VerifyResetOtpRequest,
    VerifySignupOtpRequest,
)
from api.services.auth.depends import create_user_configuration_with_mps_key, get_user
from api.services.auth.email import send_otp_email
from api.services.configuration.ai_model_configuration import (
    convert_legacy_ai_model_configuration_to_v2,
)
from api.services.posthog_client import capture_event
from api.utils.auth import create_jwt_token, hash_password, verify_password

router = APIRouter(
    prefix="/auth",
    tags=["auth"],
)

OTP_EXPIRY_MINUTES = 10


def _generate_otp() -> str:
    return "".join(random.choices(string.digits, k=6))


# ---------------------------------------------------------------------------
# Step 1 of signup: validate details + send OTP to email
# ---------------------------------------------------------------------------

@router.post("/signup/send-otp", response_model=MessageResponse)
async def signup_send_otp(request: SendSignupOtpRequest):
    """Validate signup details and send a 6-digit OTP to the email address."""
    # Reject if a fully-registered account already exists
    existing = await db_client.get_user_by_email(request.email)
    if existing and existing.password_hash:
        raise HTTPException(status_code=409, detail="Email already registered")

    otp = _generate_otp()
    expires_at = datetime.now(UTC) + timedelta(minutes=OTP_EXPIRY_MINUTES)

    # Park the OTP against a stub user row (created if needed)
    await db_client.save_otp(request.email, otp, "signup", expires_at)

    try:
        await send_otp_email(request.email, otp, "signup")
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to send verification email. Please try again.")

    return MessageResponse(message="Verification code sent. Check your email.")


# ---------------------------------------------------------------------------
# Step 2 of signup: verify OTP → create account → return JWT
# ---------------------------------------------------------------------------

@router.post("/signup/verify-otp", response_model=AuthResponse)
async def signup_verify_otp(request: VerifySignupOtpRequest):
    """Verify the OTP and complete account creation."""
    valid = await db_client.verify_otp(request.email, request.otp, "signup")
    if not valid:
        raise HTTPException(status_code=400, detail="Invalid or expired verification code")

    # Complete the user record with password hash
    user = await db_client.complete_signup(
        email=request.email,
        password_hash=hash_password(request.password),
        name=request.name,
    )

    # Create organization for the user
    org_provider_id = f"org_{user.provider_id}"
    organization, _ = await db_client.get_or_create_organization_by_provider_id(
        org_provider_id=org_provider_id, user_id=user.id
    )

    # Link user to organization (only if not already linked)
    await db_client.add_user_to_organization(user.id, organization.id)
    await db_client.update_user_selected_organization(user.id, organization.id)

    # Create default service configuration
    try:
        mps_config = await create_user_configuration_with_mps_key(
            user.id, organization.id, user.provider_id
        )
        if mps_config:
            await db_client.update_user_configuration(user.id, mps_config)
            model_config_v2 = convert_legacy_ai_model_configuration_to_v2(mps_config)
            await db_client.upsert_configuration(
                organization.id,
                OrganizationConfigurationKey.MODEL_CONFIGURATION_V2.value,
                model_config_v2.model_dump(mode="json", exclude_none=True),
            )
    except Exception:
        logger.warning("Failed to create default configuration for OSS user", exc_info=True)

    token = create_jwt_token(user.id, request.email)

    capture_event(
        distinct_id=str(user.provider_id),
        event=PostHogEvent.SIGNED_UP,
        properties={
            "organization_id": organization.id,
            "auth_provider": "local",
        },
    )

    return AuthResponse(
        token=token,
        user=UserResponse(
            id=user.id,
            email=user.email,
            name=request.name,
            organization_id=organization.id,
            provider_id=user.provider_id,
        ),
    )


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

@router.post("/login", response_model=AuthResponse)
async def login(request: LoginRequest):
    user = await db_client.get_user_by_email(request.email)
    if not user or not user.password_hash:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_jwt_token(user.id, user.email)

    capture_event(
        distinct_id=str(user.provider_id),
        event=PostHogEvent.SIGNED_IN,
        properties={
            "organization_id": user.selected_organization_id,
            "auth_provider": "local",
        },
    )

    return AuthResponse(
        token=token,
        user=UserResponse(
            id=user.id,
            email=user.email,
            organization_id=user.selected_organization_id,
            provider_id=user.provider_id,
        ),
    )


# ---------------------------------------------------------------------------
# Forgot password — Step 1: send OTP
# ---------------------------------------------------------------------------

@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(request: ForgotPasswordRequest):
    """Send a password reset OTP. Always returns success to avoid email enumeration."""
    user = await db_client.get_user_by_email(request.email)
    if user and user.password_hash:
        otp = _generate_otp()
        expires_at = datetime.now(UTC) + timedelta(minutes=OTP_EXPIRY_MINUTES)
        await db_client.save_otp(request.email, otp, "reset", expires_at)
        try:
            await send_otp_email(request.email, otp, "reset")
        except Exception:
            logger.warning(f"Failed to send reset OTP to {request.email}")

    return MessageResponse(message="If that email is registered, a reset code has been sent.")


# ---------------------------------------------------------------------------
# Forgot password — Step 2: verify OTP
# ---------------------------------------------------------------------------

@router.post("/forgot-password/verify-otp", response_model=MessageResponse)
async def verify_reset_otp(request: VerifyResetOtpRequest):
    """Verify the reset OTP — if valid, the client may proceed to reset the password."""
    valid = await db_client.verify_otp(request.email, request.otp, "reset")
    if not valid:
        raise HTTPException(status_code=400, detail="Invalid or expired reset code")
    return MessageResponse(message="Code verified. You may now set a new password.")


# ---------------------------------------------------------------------------
# Forgot password — Step 3: set new password
# ---------------------------------------------------------------------------

@router.post("/forgot-password/reset", response_model=MessageResponse)
async def reset_password(request: ResetPasswordRequest):
    """Verify OTP one final time and update the password."""
    valid = await db_client.verify_otp(request.email, request.otp, "reset")
    if not valid:
        raise HTTPException(status_code=400, detail="Invalid or expired reset code")

    await db_client.set_password(request.email, hash_password(request.new_password))
    await db_client.clear_otp(request.email)

    return MessageResponse(message="Password updated successfully. You can now sign in.")


# ---------------------------------------------------------------------------
# Current user
# ---------------------------------------------------------------------------

@router.get("/me", response_model=UserResponse)
async def get_current_user(user: UserModel = Depends(get_user)):
    return UserResponse(
        id=user.id,
        email=user.email,
        organization_id=user.selected_organization_id,
        provider_id=user.provider_id,
    )
