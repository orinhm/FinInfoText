"""
LLM client — MarketSage wrapper over the shared Utilities.llm_client.

This is a thin compatibility layer. The base LLM infrastructure lives
in ``Utilities.llm_client``; this module re-exports everything and adds
MarketSage-specific functionality (settings.yaml loading, tool-calling
wired to ``marketsage.tools``).

All existing ``from marketsage.llm_client import ...`` statements
continue to work unchanged.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import yaml

# ── Ensure Utilities is importable ────────────────────────────────
# Add the FinancialProjects root so ``import Utilities`` works.
_FP_ROOT = Path(__file__).resolve().parent.parent.parent  # FinancialProjects/
if str(_FP_ROOT) not in sys.path:
    sys.path.insert(0, str(_FP_ROOT))

# ── Re-export everything from the shared Utilities module ─────────
from Utilities.llm_client import (  # noqa: E402, F401
    BaseLLMClient,
    LLMResponse,
    ToolCall,
    ToolResult,
    MAX_TOOL_ITERATIONS,
    MAX_TOOL_RESULT_CHARS,
    CHUNK_SIZE_CHARS,
    MAX_CONDENSER_WORKERS,
    MAX_CONTEXT_CHARS,
)

logger = logging.getLogger("marketsage.llm")

_SETTINGS_PATH = Path(__file__).parent / "settings.yaml"

# ── Schema keys allowed by Gemini FunctionDeclaration ─────────────
_SCHEMA_KEYS = {"name", "description", "parameters"}


def _load_settings() -> dict[str, Any]:
    with open(_SETTINGS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ══════════════════════════════════════════════════════════════════
#  Factory — MarketSage-specific wrapper
# ══════════════════════════════════════════════════════════════════

_PROVIDERS = {
    "gemini": "Utilities.llm_providers.gemini.GeminiClient",
    "openai": "Utilities.llm_providers.openai.OpenAIClient",
}


def create_llm_client(
    settings: dict[str, Any] | None = None
) -> BaseLLMClient:
    """
    Factory: create the right LLM client based on settings.

    Reads ``active_llm`` + ``llm_profiles`` from settings, falls
    back to legacy ``llm:`` section. If settings is None, loads
    from ``marketsage/settings.yaml``.
    """
    all_settings = settings or _load_settings()

    # Resolve profile
    active_name = all_settings.get("active_llm", "")
    profiles = all_settings.get("llm_profiles", {})
    if active_name and active_name in profiles:
        cfg = profiles[active_name]
        logger.info("  LLM profile: %s", active_name)
    elif profiles:
        first_name = next(iter(profiles))
        cfg = profiles[first_name]
        logger.warning("  ⚠ active_llm '%s' not found, using '%s'",
                       active_name, first_name)
    else:
        cfg = all_settings.get("llm", {})

    provider = cfg.get("provider", "gemini")
    model = cfg.get("model", "gemini-2.0-flash-001")
    temperature = cfg.get("temperature", 0.3)
    max_tokens = cfg.get("max_tokens", 8000)

    # Resolve API key
    api_key = cfg.get("api_key", "") or ""
    if not api_key:
        env_var = cfg.get("api_key_env", "GEMINI_API_KEY")
        api_key = os.environ.get(env_var, "")

    # Import and instantiate the right subclass
    class_path = _PROVIDERS.get(provider)
    if not class_path:
        raise ValueError(
            f"Unknown LLM provider '{provider}'. "
            f"Supported: {list(_PROVIDERS.keys())}"
        )

    module_path, class_name = class_path.rsplit(".", 1)
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)

    return cls(
        provider=provider,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        api_key=api_key,
    )


# Backward-compatible alias
LLMClient = create_llm_client


def list_profile_models(profile_name: str | None = None) -> list[str]:
    """
    List available models for a given profile (or the active one).

    Parameters
    ----------
    profile_name : str, optional
        Name from llm_profiles in settings.yaml.
        If None, uses active_llm.
    """
    settings = _load_settings()
    if profile_name:
        settings["active_llm"] = profile_name
    llm = create_llm_client(settings)
    return llm.list_models()


def refresh_available_models() -> Path:
    """
    Query every profile in settings.yaml for available models and
    write the results to ``marketsage/available_models.yaml``.

    Includes pricing (per 1M tokens) when known.
    Returns the path to the written file.
    """
    from datetime import datetime, timezone
    from marketsage.model_pricing import get_model_info

    settings = _load_settings()
    profiles = settings.get("llm_profiles", {})
    if not profiles:
        raise ValueError("No llm_profiles found in settings.yaml")

    output: dict[str, Any] = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "profiles": {},
    }

    # De-duplicate API keys to avoid redundant calls
    seen_keys: dict[str, list[str]] = {}  # api_key -> model list
    for name, cfg in profiles.items():
        provider = cfg.get("provider", "gemini")
        api_key = cfg.get("api_key", "") or ""

        cache_key = f"{provider}:{api_key[:20]}"
        if cache_key in seen_keys:
            models = seen_keys[cache_key]
        else:
            try:
                settings_copy = dict(settings)
                settings_copy["active_llm"] = name
                llm = create_llm_client(settings_copy)
                models = llm.list_models()
                seen_keys[cache_key] = models
            except Exception as exc:
                models = [f"ERROR: {exc}"]
                seen_keys[cache_key] = models

        # Build model entries with pricing, context, and description
        model_entries = []
        for m in models:
            entry: dict[str, Any] = {"id": m}
            info = get_model_info(m)
            if info:
                entry["pricing_per_1M_tokens"] = {
                    "input": info["input"],
                    "output": info["output"]
                }
                if info.get("context_window_k"):
                    entry["context_window_k"] = info["context_window_k"]
                if info.get("description"):
                    entry["description"] = info["description"]
            model_entries.append(entry)

        priced = sum(1 for e in model_entries if "pricing_per_1M_tokens" in e)
        output["profiles"][name] = {
            "provider": provider,
            "model_count": len(models),
            "priced_count": priced,
            "models": model_entries,
        }

    out_path = Path(__file__).parent / "available_models.yaml"
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(output, f, default_flow_style=False, sort_keys=False,
                  allow_unicode=True)

    return out_path


if __name__ == "__main__":
    import sys

    if "--refresh" in sys.argv:
        path = refresh_available_models()
        print(f"✓ Written to {path}")
    else:
        profile = sys.argv[1] if len(sys.argv) > 1 else None
        settings = _load_settings()
        if profile:
            settings["active_llm"] = profile
        llm = create_llm_client(settings)
        print(f"Provider: {llm.provider}")
        print(f"Model:    {llm.model}")
        print(f"API key:  {llm.api_key[:12]}...")
        print()
        print("Available models:")
        for m in llm.list_models():
            print(f"  {m}")
