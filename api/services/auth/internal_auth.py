"""Authentication for internal service-to-service routes.

This module provides authentication for /internal/ routes that should only be
called by other Dograh services (e.g., WhatsApp Adapter calling AgentPlatform).
Uses a separate secret from DOGRAH_DEVOPS_SECRET so read-only diagnostic access
doesn't also grant money-moving permissions.
"""

import secrets
from typing import Annotated

from fastapi import Header, HTTPException, status

from api.constants import DOGRAH_INTERNAL_SECRET

# Header name for internal service authentication
DOGRAH_INTERNAL_SECRET_HEADER = "X-Dograh-Internal-Secret"


def verify_internal_secret(
    x_dograh_internal_secret: Annotated[
        str | None,
        Header(alias=DOGRAH_INTERNAL_SECRET_HEADER),
    ] = None,
) -> None:
    """FastAPI dependency that verifies the internal service secret.

    Use this as a dependency on any route that should only be callable by
    internal Dograh services (not end users, not even authenticated operators).

    Returns:
        None if authentication succeeds

    Raises:
        HTTPException 503: Secret is not configured (service not set up)
        HTTPException 401: Secret is missing or incorrect
    """
    if not DOGRAH_INTERNAL_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal API is not configured. Set DOGRAH_INTERNAL_SECRET.",
        )

    if not x_dograh_internal_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing internal authentication header",
        )

    # Use secrets.compare_digest to prevent timing attacks
    if not secrets.compare_digest(x_dograh_internal_secret, DOGRAH_INTERNAL_SECRET):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal authentication",
        )
