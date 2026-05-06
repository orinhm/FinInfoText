"""
LLM client — abstract base with provider-specific subclasses.

Supports Gemini and OpenAI via a factory function:
    llm = create_llm_client(settings)
"""

from __future__ import annotations

import json
import logging
import os
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from marketsage.tools import get_all_tool_declarations, execute_tool

logger = logging.getLogger("marketsage.llm")

_SETTINGS_PATH = Path(__file__).parent / "settings.yaml"

MAX_TOOL_ITERATIONS = 25
MAX_TOOL_RESULT_CHARS = 200_000
CHUNK_SIZE_CHARS = 100_000
MAX_CONDENSER_WORKERS = 2
MAX_CONTEXT_CHARS = 350_000


def _load_settings() -> dict[str, Any]:
    with open(_SETTINGS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── Intermediate data structures ──────────────────────────────────

@dataclass
class ToolCall:
    id: str
    name: str
    args: dict

@dataclass
class ToolResult:
    call_id: str
    name: str
    result: str

@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None
    input_tokens: int = 0
    output_tokens: int = 0


# ── Schema keys allowed by Gemini FunctionDeclaration ─────────────
_SCHEMA_KEYS = {"name", "description", "parameters"}


# ══════════════════════════════════════════════════════════════════
#  Base class
# ══════════════════════════════════════════════════════════════════

class BaseLLMClient(ABC):
    """Abstract LLM client with shared orchestration logic."""

    MAX_RETRIES = 50
    BASE_DELAY = 2.0
    MAX_DELAY = 120.0

    def __init__(self, provider: str, model: str, temperature: float,
                 max_tokens: int, api_key: str):
        self.provider = provider
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.api_key = api_key
        self._call_count = 0
        self._tool_call_count = 0
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_tokens = 0
        self._init_sdk()

    # ── Abstract methods (provider-specific) ──────────────────────

    @abstractmethod
    def _init_sdk(self) -> None:
        """Initialize the provider SDK client."""

    @abstractmethod
    def list_models(self) -> list[str]:
        """List available model names for this provider/key."""

    @abstractmethod
    def _api_call(self, messages: Any, system: str,
                  tools: Any | None) -> Any:
        """Make a single API call. Returns raw provider response."""

    @abstractmethod
    def _parse_response(self, raw: Any) -> LLMResponse:
        """Parse raw response into LLMResponse."""

    @abstractmethod
    def _make_initial_messages(self, user_text: str) -> Any:
        """Create the initial message list with the user request."""

    @abstractmethod
    def _append_assistant(self, messages: Any, response: LLMResponse) -> None:
        """Append the assistant's response to the message history."""

    @abstractmethod
    def _append_tool_results(self, messages: Any,
                             results: list[ToolResult]) -> None:
        """Append tool execution results to the message history."""

    @abstractmethod
    def _build_tools(self, declarations: list[dict]) -> Any:
        """Convert tool declarations to provider-specific format."""

    @abstractmethod
    def _estimate_context_chars(self, messages: Any) -> int:
        """Estimate total character count of the message history."""

    @abstractmethod
    def _shrink_tool_result_in_messages(
        self, messages: Any, name: str, condensed: str,
    ) -> None:
        """Replace the largest tool result in messages with condensed version."""

    # ── Shared logic ─────────────────────────────────────────────

    def _call_with_retry(self, messages: Any, system: str,
                         tools: Any | None, call_id: int) -> LLMResponse:
        """Make an API call with retry on rate-limit/server errors."""
        last_exc = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            t0 = time.time()
            try:
                raw = self._api_call(messages, system, tools)
                resp = self._parse_response(raw)
                elapsed = time.time() - t0
                self._total_input_tokens += resp.input_tokens
                self._total_output_tokens += resp.output_tokens
                self._total_tokens += resp.input_tokens + resp.output_tokens
                logger.info(
                    "[LLM #%d] API call completed in %.1fs (attempt %d) "
                    "— tokens: %d in / %d out",
                    call_id, elapsed, attempt,
                    resp.input_tokens, resp.output_tokens,
                )
                return resp
            except Exception as exc:
                last_exc = exc
                elapsed = time.time() - t0
                err_msg = str(exc).lower()
                if any(kw in err_msg for kw in
                       ("timeout", "connection", "rate", "429", "503",
                        "resource exhausted", "quota", "500", "overloaded")):
                    delay = min(self.BASE_DELAY * (2 ** (attempt - 1)),
                                self.MAX_DELAY)
                    logger.warning(
                        "[LLM #%d] %s after %.1fs (attempt %d/%d) — "
                        "retrying in %.1fs: %s",
                        call_id, type(exc).__name__, elapsed, attempt,
                        self.MAX_RETRIES, delay, str(exc)[:200],
                    )
                    time.sleep(delay)
                    continue
                raise
        logger.error("[LLM #%d] All %d retries exhausted",
                     call_id, self.MAX_RETRIES)
        raise RuntimeError(
            f"LLM call failed after {self.MAX_RETRIES} retries: {last_exc}"
        ) from last_exc

    def simple_call(self, system: str, user: str,
                    label: str = "", agent_name: str = "") -> str:
        """Simple single-turn LLM call without tools."""
        self._call_count += 1
        call_id = self._call_count
        tag = f"[LLM #{call_id}]"
        if agent_name:
            tag += f" [{agent_name}]"
        if label:
            tag += f" ({label})"
        logger.info("%s simple_call — %s / %s", tag, self.provider, self.model)

        messages = self._make_initial_messages(user)
        resp = self._call_with_retry(messages, system, None, call_id)
        return resp.text

    def chat_with_tools(
        self,
        system_prompt: str,
        user_message: str,
        run_dir: Path | None = None,
    ) -> str:
        """Run a multi-turn chat with tool calling."""
        raw_decls = get_all_tool_declarations()
        clean_decls = [
            {k: v for k, v in d.items() if k in _SCHEMA_KEYS}
            for d in raw_decls
        ]
        tools = self._build_tools(clean_decls)
        messages = self._make_initial_messages(user_message)
        tool_log: list[dict] = []
        iteration = 0

        while iteration < MAX_TOOL_ITERATIONS:
            iteration += 1
            self._call_count += 1
            call_id = self._call_count

            logger.info("")
            logger.info("─" * 70)
            logger.info("[LLM #%d] Iteration %d — %s / %s",
                        call_id, iteration, self.provider, self.model)
            logger.info("─" * 70)
            if iteration == 1:
                logger.info("[LLM #%d] SYSTEM PROMPT (%d chars)",
                            call_id, len(system_prompt))
                logger.info("[LLM #%d] USER MESSAGE (%d chars)",
                            call_id, len(user_message))

            resp = self._call_with_retry(messages, system_prompt,
                                         tools, call_id)

            if not resp.tool_calls:
                logger.info("")
                logger.info("[LLM #%d] FINAL RESPONSE (%d chars)",
                            call_id, len(resp.text))
                logger.info("─" * 70)
                if run_dir and tool_log:
                    log_file = run_dir / "tool_calls.json"
                    with open(log_file, "w", encoding="utf-8") as f:
                        json.dump(tool_log, f, indent=2, ensure_ascii=False,
                                  default=str)
                    logger.info("  💾 Tool log → %s", log_file.name)
                return resp.text

            logger.info("[LLM #%d] Model requested %d tool call(s)",
                        call_id, len(resp.tool_calls))

            # Append assistant response to history
            self._append_assistant(messages, resp)

            # Log and execute tools
            fc_meta = []
            for tc in resp.tool_calls:
                self._tool_call_count += 1
                fc_meta.append({"tc": tc, "call_num": self._tool_call_count})
                logger.info("")
                logger.info("  ┌─ Tool Call #%d: %s",
                            self._tool_call_count, tc.name)
                logger.info("  │  Args: %s", tc.args)

            def _run_tool(meta):
                tc = meta["tc"]
                t0 = time.time()
                result = execute_tool(tc.name, tc.args)
                elapsed = time.time() - t0
                return {**meta, "result": result, "elapsed": elapsed}

            if len(fc_meta) > 1:
                logger.info("  ⚡ Executing %d tools in parallel",
                            len(fc_meta))
                completed = []
                with ThreadPoolExecutor(max_workers=len(fc_meta)) as pool:
                    futures = {pool.submit(_run_tool, m): m
                               for m in fc_meta}
                    for future in as_completed(futures):
                        completed.append(future.result())
                completed.sort(key=lambda r: r["call_num"])
            else:
                completed = [_run_tool(fc_meta[0])]

            tool_results: list[ToolResult] = []
            for entry in completed:
                tc = entry["tc"]
                result = entry["result"]
                elapsed = entry["elapsed"]
                call_num = entry["call_num"]

                logger.info("  │  Result: %d chars (%.1fs)",
                            len(result), elapsed)
                logger.info("  └─ Done: %s", tc.name)

                tool_log.append({
                    "iteration": iteration,
                    "tool": tc.name,
                    "args": tc.args,
                    "result_length": len(result),
                    "elapsed_seconds": round(elapsed, 2),
                })
                if run_dir:
                    rf = run_dir / f"tool_{call_num}_{tc.name}.txt"
                    rf.write_text(result, encoding="utf-8")

                result = self._condense_if_needed(
                    result, tc.name, tc.args, user_message)
                tool_results.append(
                    ToolResult(call_id=tc.id, name=tc.name, result=result))

            self._append_tool_results(messages, tool_results)

            # Context overflow guard
            total_chars = self._estimate_context_chars(messages)
            if total_chars > MAX_CONTEXT_CHARS:
                logger.info("")
                logger.info(
                    "  ⚠ Context overflow: %d chars > %d limit — "
                    "re-condensing largest tool results",
                    total_chars, MAX_CONTEXT_CHARS)
                self._shrink_context(messages, user_message,
                                     MAX_CONTEXT_CHARS)

        # Iteration limit
        logger.warning(
            "  ⚠ Tool iteration limit (%d) reached — "
            "requesting final response", MAX_TOOL_ITERATIONS)
        messages_text = self._make_initial_messages(
            "You have reached the maximum number of tool calls. "
            "Please provide your final analysis now based on all "
            "the data you have gathered so far."
        )
        # Extend messages with the forced prompt
        for m in (messages_text if isinstance(messages_text, list)
                  else [messages_text]):
            if isinstance(messages, list):
                messages.append(m)
        resp = self._call_with_retry(messages, system_prompt, None,
                                     self._call_count + 1)
        return resp.text

    # ── Condensation (shared) ────────────────────────────────────

    def _shrink_context(self, messages: Any, user_request: str,
                        target_chars: int) -> None:
        """Shrink context by re-condensing the largest tool results."""
        # Delegate to subclass for provider-specific message inspection
        # This is a generic loop — subclasses implement the actual
        # result extraction and replacement.
        for _ in range(10):  # max 10 shrink iterations
            total = self._estimate_context_chars(messages)
            if total <= target_chars:
                logger.info("  ✓ Context shrunk to %d chars (target: %d)",
                            total, target_chars)
                break
            # Find and shrink largest result
            largest = self._find_largest_tool_result(messages)
            if not largest or largest[1] < 10_000:
                break
            name, size, result_text = largest
            overshoot = total - target_chars
            new_max = max(size - overshoot, size // 4)
            logger.info("  Shrinking %s result: %d → ~%d chars",
                        name, size, new_max)
            condensed = self._condense_if_needed(
                result_text, name, {}, user_request,
                force_max_chars=new_max)
            self._shrink_tool_result_in_messages(messages, name, condensed)

    @abstractmethod
    def _find_largest_tool_result(
        self, messages: Any
    ) -> tuple[str, int, str] | None:
        """Find (name, size, text) of the largest tool result in messages."""

    def _condense_if_needed(
        self, result: str, tool_name: str, tool_args: dict,
        user_request: str, force_max_chars: int | None = None,
    ) -> str:
        """Chunk-summarize oversized tool results."""
        threshold = (force_max_chars if force_max_chars is not None
                     else MAX_TOOL_RESULT_CHARS)
        if len(result) <= threshold:
            return result

        original_chars = len(result)
        logger.info("")
        logger.info(
            "  ⚡ Tool '%s' returned %d chars (> %d limit) — condensing...",
            tool_name, original_chars, MAX_TOOL_RESULT_CHARS)

        lines = result.split("\n")
        chunks: list[str] = []
        current_chunk: list[str] = []
        current_size = 0
        for line in lines:
            line_size = len(line) + 1
            if current_size + line_size > CHUNK_SIZE_CHARS and current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = []
                current_size = 0
            current_chunk.append(line)
            current_size += line_size
        if current_chunk:
            chunks.append("\n".join(current_chunk))

        logger.info("  Split into %d chunks (%d chars/chunk max)",
                    len(chunks), CHUNK_SIZE_CHARS)

        system = (
            "You are a data summarizer for an investment analysis system. "
            "Extract ALL key facts, sentiments, events, dates, numbers, "
            "and notable opinions from the data below. "
            "Preserve specific details (names, tickers, dollar amounts, "
            "dates, direct quotes). This summary will be used for deeper "
            "analysis, so be thorough and factual. Do NOT hallucinate."
        )

        def _summarize_chunk(i: int, chunk: str) -> tuple[int, str]:
            logger.info("  Condensing chunk %d/%d (%d chars)...",
                        i, len(chunks), len(chunk))
            user_msg = (
                f"## Context\n\n"
                f"User request: {user_request}\n"
                f"Data source: {tool_name}({tool_args})\n\n"
                f"## Data (chunk {i}/{len(chunks)})\n\n{chunk}"
            )
            summary = self.simple_call(
                system, user_msg,
                label=f"condense-{i}/{len(chunks)}",
                agent_name="condenser",
            )
            return (i, summary)

        logger.info("  Condensing %d chunks with %d workers...",
                    len(chunks), min(MAX_CONDENSER_WORKERS, len(chunks)))
        results: list[tuple[int, str]] = []
        with ThreadPoolExecutor(max_workers=MAX_CONDENSER_WORKERS) as pool:
            futures = {
                pool.submit(_summarize_chunk, i, chunk): i
                for i, chunk in enumerate(chunks, 1)
            }
            for future in as_completed(futures):
                results.append(future.result())

        results.sort(key=lambda r: r[0])
        summaries = [
            f"## {tool_name} — Condensed Summary "
            f"(Part {i}/{len(chunks)})\n\n{summary}"
            for i, summary in results
        ]
        combined = "\n\n---\n\n".join(summaries)
        logger.info("  ✓ Condensed %d chars → %d chars (%d chunks)",
                    original_chars, len(combined), len(chunks))
        return combined


# ══════════════════════════════════════════════════════════════════
#  Factory
# ══════════════════════════════════════════════════════════════════

_PROVIDERS = {
    "gemini": "marketsage.llm_providers.gemini.GeminiClient",
    "openai": "marketsage.llm_providers.openai.OpenAIClient",
}


def create_llm_client(
    settings: dict[str, Any] | None = None
) -> BaseLLMClient:
    """
    Factory: create the right LLM client based on settings.

    Reads ``active_llm`` + ``llm_profiles`` from settings, falls
    back to legacy ``llm:`` section.
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
    import importlib
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


if __name__ == "__main__":
    import sys
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
