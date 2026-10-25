"""Resolve effective config by merging per-workflow model overrides onto global config."""

from __future__ import annotations

import copy

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.configuration.provider_keys import get_provider_api_key
from api.services.configuration.registry import (
    REGISTRY,
    ServiceType,
)

# Sections of the effective config whose api_key must be sourced from env.
_ENV_KEY_SECTIONS = ("llm", "tts", "stt", "realtime", "embeddings")


def _inject_env_api_keys(
    config: EffectiveAIModelConfiguration,
) -> EffectiveAIModelConfiguration:
    """Overwrite each service's api_key with the env-sourced provider key.

    Section 1: keys never come from the stored configuration anymore. For every
    present section we look up the provider's env key and stamp it onto the
    section's api_key, ignoring whatever was saved. Dograh (managed) sections
    keep their own key — they are not in PROVIDER_ENV_KEYS, so get_provider_api_key
    returns None and we leave the section untouched.
    """
    for section_key in _ENV_KEY_SECTIONS:
        section = getattr(config, section_key, None)
        if section is None:
            continue
        provider = getattr(section, "provider", None)
        env_key = get_provider_api_key(provider)
        if env_key is None:
            # No env mapping for this provider (e.g. dograh managed) — leave as-is.
            continue
        if hasattr(section, "api_key"):
            try:
                setattr(section, "api_key", env_key)
            except Exception:
                # Some provider configs expose api_key as read-only / computed;
                # skip rather than break resolution.
                pass
    return config

# Maps override key → (EffectiveAIModelConfiguration field, ServiceType for registry lookup)
_SECTION_MAP: dict[str, ServiceType] = {
    "llm": ServiceType.LLM,
    "tts": ServiceType.TTS,
    "stt": ServiceType.STT,
    "realtime": ServiceType.REALTIME,
}


def _build_section_from_override(service_type: ServiceType, override: dict):
    """Construct a typed config object from a raw override dict using the registry."""
    provider = override.get("provider")
    if not provider:
        return None
    registry = REGISTRY.get(service_type, {})
    config_cls = registry.get(provider)
    if config_cls is None:
        return None
    return config_cls(**override)


_SECRET_FIELDS = ("api_key", "credentials", "aws_access_key", "aws_secret_key")


def enrich_overrides_with_api_keys(
    model_overrides: dict,
    user_config: EffectiveAIModelConfiguration,
) -> dict:
    """Copy API keys from the global config into model_overrides where missing.

    When a workflow override selects the same provider as the current global
    config but omits the API key, the override becomes broken if the global
    config later switches to a different provider. This function stamps the
    global provider's API key (and other secret fields) into the override at
    save time so the override is self-contained.
    """
    result = copy.deepcopy(model_overrides)
    for section_key in _SECTION_MAP:
        if section_key not in result:
            continue
        override = result[section_key]
        override_provider = override.get("provider")
        if not override_provider:
            continue
        global_section = getattr(user_config, section_key, None)
        if global_section is None:
            continue
        if getattr(global_section, "provider", None) != override_provider:
            continue
        for field in _SECRET_FIELDS:
            if override.get(field):
                continue
            if field == "api_key" and hasattr(global_section, "get_all_api_keys"):
                all_keys = global_section.get_all_api_keys()
                if all_keys:
                    override[field] = all_keys[0] if len(all_keys) == 1 else all_keys
            else:
                global_value = getattr(global_section, field, None)
                if global_value is not None:
                    override[field] = global_value
    return result


def resolve_effective_config(
    user_config: EffectiveAIModelConfiguration,
    model_overrides: dict | None,
) -> EffectiveAIModelConfiguration:
    """Deep-merge workflow model_overrides onto global user config.

    - If model_overrides is None or empty, returns a copy of user_config unchanged.
    - For each section (llm, tts, stt, realtime), if the override contains that key:
      - If the global section is None, construct a new config from the override.
      - If the provider changes, construct a new config from the override.
      - Otherwise, merge override fields onto the existing config (model_copy).
    - is_realtime is a simple boolean override.
    - Sections not in the override are inherited from global unchanged.
    - The original user_config is never mutated.
    """
    if not model_overrides:
        return _inject_env_api_keys(user_config.model_copy(deep=True))

    effective = user_config.model_copy(deep=True)

    # Handle is_realtime boolean
    if "is_realtime" in model_overrides:
        effective.is_realtime = model_overrides["is_realtime"]

    # Handle service sections
    for section_key, service_type in _SECTION_MAP.items():
        if section_key not in model_overrides:
            continue

        override = model_overrides[section_key]
        base = getattr(effective, section_key)

        if base is None:
            # No global config for this section — build from override
            setattr(
                effective,
                section_key,
                _build_section_from_override(service_type, override),
            )
        elif "provider" in override and override["provider"] != base.provider:
            # Provider changed — must construct new typed object
            setattr(
                effective,
                section_key,
                _build_section_from_override(service_type, override),
            )
        else:
            # Same provider — merge fields onto existing config
            merged = base.model_copy(update=override)
            setattr(effective, section_key, merged)

    return _inject_env_api_keys(effective)
