"""Server-side provider API key resolution from environment variables.

Section 1 (AgentX Model & Pricing Architecture) removes end-user API-key entry.
Keys are no longer read from the saved org/user model configuration — they are
sourced exclusively from environment variables and injected into the effective
configuration server-side at resolve time. Any key stored in the database is
ignored.

Each curated provider maps to one environment variable. When the variable is
unset, the provider's key is left as-is (empty / None) so the failure is a clear
"missing credential" rather than a silent fallback to a stale DB key.
"""

from __future__ import annotations

import os

from api.services.configuration.registry import ServiceProviders

# Provider -> environment variable holding its upstream API key.
# Covers exactly the Section 1 curated providers across LLM / realtime / TTS /
# STT / embeddings.
PROVIDER_ENV_KEYS: dict[str, str] = {
    ServiceProviders.OPENAI.value: "OPENAI_API_KEY",
    ServiceProviders.ANTHROPIC.value: "ANTHROPIC_API_KEY",
    ServiceProviders.GOOGLE.value: "GOOGLE_API_KEY",
    ServiceProviders.GROQ.value: "GROQ_API_KEY",
    ServiceProviders.CARTESIA.value: "CARTESIA_API_KEY",
    ServiceProviders.DEEPGRAM.value: "DEEPGRAM_API_KEY",
    ServiceProviders.ELEVENLABS.value: "ELEVENLABS_API_KEY",
    # Speech-to-Speech realtime providers.
    ServiceProviders.OPENAI_REALTIME.value: "OPENAI_API_KEY",
    ServiceProviders.GOOGLE_REALTIME.value: "GOOGLE_API_KEY",
    ServiceProviders.GROK_REALTIME.value: "GROK_API_KEY",
}


def get_provider_api_key(provider: str | None) -> str | None:
    """Return the env-sourced API key for a provider, or None when unset.

    `provider` is the ServiceProviders value (e.g. "openai"). Unknown providers
    and unset environment variables both yield None.
    """
    if not provider:
        return None
    env_var = PROVIDER_ENV_KEYS.get(provider)
    if not env_var:
        return None
    value = os.getenv(env_var)
    return value or None
