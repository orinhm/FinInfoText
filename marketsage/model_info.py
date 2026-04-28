"""
Model info — auto-detect LLM context window size.

Queries the provider API to discover model limits (input/output token counts),
then caches results in ``model_limits.json`` so subsequent runs don't need
to query again.

Supported providers:
  - **gemini**: ``GET /v1beta/models/{model}`` → ``inputTokenLimit``
  - **openai**: known limits per model family
  - **anthropic**: known limits per model family
  - **ollama**: ``GET /api/show`` → ``num_ctx``
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger("marketsage.model_info")

_CACHE_FILE = Path(__file__).parent / "model_limits.json"

# Fallback known limits when API query fails (tokens)
_KNOWN_LIMITS: dict[str, dict[str, int]] = {
    # Gemini
    "gemini-2.0-flash-001": {"input": 1048576, "output": 8192},
    "gemini-flash-latest": {"input": 1048576, "output": 8192},
    "gemini-2.0-flash": {"input": 1048576, "output": 8192},
    "gemini-1.5-pro": {"input": 2097152, "output": 8192},
    "gemini-1.5-flash": {"input": 1048576, "output": 8192},
    # OpenAI
    "gpt-4o": {"input": 128000, "output": 16384},
    "gpt-4o-mini": {"input": 128000, "output": 16384},
    "gpt-4-turbo": {"input": 128000, "output": 4096},
    "gpt-4": {"input": 8192, "output": 4096},
    "gpt-3.5-turbo": {"input": 16385, "output": 4096},
    # Anthropic
    "claude-3.5-sonnet": {"input": 200000, "output": 8192},
    "claude-3-opus": {"input": 200000, "output": 4096},
    "claude-3-haiku": {"input": 200000, "output": 4096},
}

# Conservative default if nothing else works
_DEFAULT_LIMIT = {"input": 32000, "output": 4096}


def _load_cache() -> dict[str, dict[str, int]]:
    if _CACHE_FILE.exists():
        with open(_CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_cache(cache: dict[str, dict[str, int]]) -> None:
    with open(_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def _query_gemini(model: str, api_key: str) -> dict[str, int] | None:
    """Query Gemini models API for token limits."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
    try:
        resp = requests.get(url, params={"key": api_key}, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "input": data.get("inputTokenLimit", 0),
                "output": data.get("outputTokenLimit", 0),
            }
        logger.warning("  Gemini models API returned %d", resp.status_code)
    except Exception as exc:
        logger.warning("  Could not query Gemini models API: %s", exc)
    return None


def _query_ollama(model: str) -> dict[str, int] | None:
    """Query local Ollama for model info."""
    try:
        resp = requests.post(
            "http://localhost:11434/api/show",
            json={"name": model},
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            params = data.get("model_info", {})
            ctx = 0
            for k, v in params.items():
                if "context" in k.lower() and isinstance(v, int):
                    ctx = v
                    break
            if ctx:
                return {"input": ctx, "output": ctx // 4}
    except Exception:
        pass
    return None


def get_model_limits(settings: dict[str, Any]) -> dict[str, int]:
    """
    Get the input/output token limits for the configured model.

    Lookup order:
    1. Local cache file (``model_limits.json``)
    2. Provider API query
    3. Built-in known limits table
    4. Conservative default (32K input)

    Returns dict with keys ``input`` and ``output`` (token counts).
    """
    llm_cfg = settings.get("llm", {})
    provider = llm_cfg.get("provider", "gemini")
    model = llm_cfg.get("model", "")
    api_key = llm_cfg.get("api_key", "")

    # 1. Check cache
    cache = _load_cache()
    cache_key = f"{provider}/{model}"
    if cache_key in cache:
        limits = cache[cache_key]
        logger.info("  Model limits (cached): %s → input=%d, output=%d",
                    cache_key, limits["input"], limits["output"])
        return limits

    # 2. Query provider API
    logger.info("  Querying %s API for model limits: %s", provider, model)
    limits = None

    if provider == "gemini" and api_key:
        limits = _query_gemini(model, api_key)
    elif provider == "ollama":
        limits = _query_ollama(model)

    # 3. Fall back to known limits
    if not limits:
        # Try exact match, then prefix match
        if model in _KNOWN_LIMITS:
            limits = _KNOWN_LIMITS[model]
            logger.info("  Using known limits for '%s'", model)
        else:
            for known_model, known_limits in _KNOWN_LIMITS.items():
                if model.startswith(known_model) or known_model.startswith(model):
                    limits = known_limits
                    logger.info("  Using known limits for '%s' (matched '%s')",
                                model, known_model)
                    break

    # 4. Default
    if not limits:
        limits = _DEFAULT_LIMIT.copy()
        logger.warning("  Could not determine limits for '%s/%s', "
                       "using conservative default: %s", provider, model, limits)

    # Cache for next time
    cache[cache_key] = limits
    _save_cache(cache)
    logger.info("  Model limits: %s → input=%d, output=%d",
                cache_key, limits["input"], limits["output"])

    return limits


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 characters per token."""
    return len(text) // 4
