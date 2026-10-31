"""Resolve the public URLs baked into embed (voice/chat widget) snippets.

The snippet is pasted onto third-party websites, so every URL in it must be
reachable from the public internet. Configured values (``UI_APP_URL`` /
``BACKEND_API_ENDPOINT``) win when they point at a real public host; when they
are local/internal defaults (``localhost``, ``host.docker.internal``, private
IPs, docker service names such as ``api``), the origin the dashboard is
actually being served from is derived from the incoming request instead. This
makes the snippet correct on any domain without extra configuration.
"""

from urllib.parse import urlsplit

from fastapi import Request

from api.constants import BACKEND_API_ENDPOINT, PUBLIC_BASE_URL, UI_APP_URL
from api.utils.common import is_local_or_private_url


def _normalize_origin(value: str | None) -> str | None:
    """Return ``scheme://host[:port]`` for a URL-ish value, or None if unusable."""
    if not value:
        return None
    value = value.strip()
    if not value or value.lower() == "null":
        return None
    if "://" not in value:
        value = f"https://{value}"
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}".rstrip("/")


def _is_internal(url: str | None) -> bool:
    """True for addresses an external website visitor could never reach."""
    if not url:
        return True
    if is_local_or_private_url(url):
        return True
    host = (urlsplit(url).hostname or "").lower()
    # host.docker.internal and bare docker service names (api, ui, nginx, ...)
    return host == "host.docker.internal" or "." not in host


def _first_header_value(request: Request, name: str) -> str | None:
    """First entry of a possibly comma-separated proxy header."""
    raw = request.headers.get(name)
    if not raw:
        return None
    first = raw.split(",", 1)[0].strip()
    return first or None


def _request_backend_origin(request: Request) -> str | None:
    """Origin the browser used to reach this API (honours reverse-proxy headers)."""
    host = _first_header_value(request, "x-forwarded-host") or request.headers.get(
        "host"
    )
    if not host:
        return None
    scheme = _first_header_value(request, "x-forwarded-proto") or request.url.scheme
    return _normalize_origin(f"{scheme}://{host}")


def _request_dashboard_origin(request: Request) -> str | None:
    """Origin of the dashboard page that issued the request (Origin, then Referer)."""
    return _normalize_origin(request.headers.get("origin")) or _normalize_origin(
        request.headers.get("referer")
    )


def resolve_embed_public_urls(request: Request) -> tuple[str, str]:
    """Return ``(script_base_url, api_endpoint)`` for embed snippets.

    - script_base_url: where ``/embed/*.js`` is served (the UI).
    - api_endpoint: where the widget sends ``/api/v1/public/...`` calls.

    Priority (first public value wins):
      1. The subsystem-specific setting (UI_APP_URL / BACKEND_API_ENDPOINT).
      2. PUBLIC_BASE_URL — the deployment's canonical public origin
         (e.g. https://agentx.example.com). Set this in production .env.
      3. The origin derived from the current request (dashboard page / proxy
         headers), so it still works on any domain without configuration.

    Behind the bundled nginx both live on the same public origin, so each falls
    back to the other when only one can be derived.
    """
    configured_ui = _normalize_origin(str(UI_APP_URL) if UI_APP_URL else None)
    configured_api = _normalize_origin(BACKEND_API_ENDPOINT)
    public_base = _normalize_origin(PUBLIC_BASE_URL)
    public_base = None if _is_internal(public_base) else public_base

    dashboard_origin = _request_dashboard_origin(request)
    backend_origin = _request_backend_origin(request)

    script_base = (
        configured_ui
        if not _is_internal(configured_ui)
        else public_base or dashboard_origin or backend_origin or configured_ui or ""
    )
    api_endpoint = (
        configured_api
        if not _is_internal(configured_api)
        else public_base or backend_origin or dashboard_origin or configured_api or ""
    )
    return script_base.rstrip("/"), api_endpoint.rstrip("/")
