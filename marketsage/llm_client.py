"""
LLM client — Gemini SDK with function-calling support.

Uses the ``google-genai`` SDK to interact with Gemini models,
providing automatic tool execution in a multi-turn chat loop.
"""

from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import yaml

from marketsage.tools import get_all_tool_declarations, execute_tool

logger = logging.getLogger("marketsage.llm")

_SETTINGS_PATH = Path(__file__).parent / "settings.yaml"

# Maximum number of tool-call round-trips before forcing a text response
MAX_TOOL_ITERATIONS = 25

# Tool result size threshold (chars). Above this, data is chunk-summarized
# before being fed back to the model. ~200K chars ≈ ~50K tokens.
MAX_TOOL_RESULT_CHARS = 200_000

# Chunk size for batch summarization (~50K chars ≈ ~12.5K tokens per chunk,
# leaving ample room for system prompt + response in a summarization call)
CHUNK_SIZE_CHARS = 100_000

# Max parallel condenser LLM calls
MAX_CONDENSER_WORKERS = 4

# Max total conversation context (chars) before sending to the LLM.
# ~350K chars ≈ ~87K tokens, leaving ~73K tokens headroom for output
# in a 160K context window.
MAX_CONTEXT_CHARS = 350_000


def _load_settings() -> dict[str, Any]:
    with open(_SETTINGS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


class LLMClient:
    """
    Gemini LLM client with function-calling support.

    Uses the google-genai SDK for multi-turn chat with automatic
    tool execution.
    """

    def __init__(self, settings: dict[str, Any] | None = None):
        cfg = (settings or _load_settings()).get("llm", {})
        self.provider: str = cfg.get("provider", "gemini")
        self.model: str = cfg.get("model", "gemini-2.0-flash-001")
        self.temperature: float = cfg.get("temperature", 0.3)
        self.max_tokens: int = cfg.get("max_tokens", 8000)
        self._call_count: int = 0
        self._tool_call_count: int = 0

        # Token usage tracking
        self._total_input_tokens: int = 0
        self._total_output_tokens: int = 0
        self._total_tokens: int = 0

        # API key
        self.api_key: str = cfg.get("api_key", "") or ""
        if not self.api_key:
            api_key_env = cfg.get("api_key_env", "GEMINI_API_KEY")
            self.api_key = os.environ.get(api_key_env, "")

        # Initialize google-genai client
        try:
            from google import genai
            from google.genai import types
            self._genai = genai
            self._types = types
            self._client = genai.Client(api_key=self.api_key)
        except ImportError as exc:
            raise ImportError(
                "google-genai SDK not installed. Run: pip install google-genai"
            ) from exc

    # Retry configuration
    MAX_RETRIES = 50
    BASE_DELAY = 2.0     # seconds
    MAX_DELAY = 120.0    # seconds

    # Keys allowed by the Gemini FunctionDeclaration schema
    _SCHEMA_KEYS = {"name", "description", "parameters"}

    def _make_config(self, system_prompt: str,
                     include_tools: bool = True) -> Any:
        """Build a GenerateContentConfig with system instruction and tools."""
        types = self._types
        kwargs: dict[str, Any] = {
            "system_instruction": system_prompt,
            "temperature": self.temperature,
            "max_output_tokens": self.max_tokens,
        }
        if include_tools:
            # Strip non-schema keys (e.g. usage_note) that the SDK rejects
            raw_decls = get_all_tool_declarations()
            clean_decls = [
                {k: v for k, v in d.items() if k in self._SCHEMA_KEYS}
                for d in raw_decls
            ]
            kwargs["tools"] = [
                types.Tool(function_declarations=clean_decls)
            ]
        return types.GenerateContentConfig(**kwargs)

    def chat_with_tools(
        self,
        system_prompt: str,
        user_message: str,
        run_dir: Path | None = None,
    ) -> str:
        """
        Run a multi-turn chat with tool calling.

        The LLM can call tools (scrapers, vault readers, etc.) and
        the results are automatically fed back. The loop continues
        until the LLM produces a final text response or the iteration
        limit is reached.

        Parameters
        ----------
        system_prompt : str
            System instructions for the model.
        user_message : str
            The user's request.
        run_dir : Path, optional
            If set, save tool call logs here.

        Returns
        -------
        str
            The final text response from the model.
        """
        types = self._types

        config = self._make_config(system_prompt, include_tools=True)

        # Build initial contents
        contents = [
            types.Content(
                role="user",
                parts=[types.Part(text=user_message)],
            )
        ]

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

            # Make the API call with retry
            response = self._call_with_retry(contents, config, call_id)

            # Check if there are function calls in the response
            candidate = response.candidates[0]
            function_calls = []
            text_parts = []

            for part in candidate.content.parts:
                if part.function_call:
                    function_calls.append(part)
                elif part.text:
                    text_parts.append(part.text)

            if not function_calls:
                # No tool calls — final text response
                final_text = "\n".join(text_parts) if text_parts else ""
                logger.info("")
                logger.info("[LLM #%d] FINAL RESPONSE (%d chars)",
                            call_id, len(final_text))
                logger.info("─" * 70)

                # Save tool log
                if run_dir and tool_log:
                    log_file = run_dir / "tool_calls.json"
                    with open(log_file, "w", encoding="utf-8") as f:
                        json.dump(tool_log, f, indent=2, ensure_ascii=False,
                                  default=str)
                    logger.info("  💾 Tool log → %s", log_file.name)

                return final_text

            # Execute each function call
            logger.info("[LLM #%d] Model requested %d tool call(s)",
                        call_id, len(function_calls))

            # Append the model's response to contents (preserves thought signatures)
            contents.append(candidate.content)

            # Prepare tool call metadata
            fc_meta = []
            for fc_part in function_calls:
                fc = fc_part.function_call
                self._tool_call_count += 1
                fc_meta.append({
                    "fc_part": fc_part,
                    "fc": fc,
                    "call_num": self._tool_call_count,
                })
                logger.info("")
                logger.info("  ┌─ Tool Call #%d: %s", self._tool_call_count, fc.name)
                logger.info("  │  Args: %s", dict(fc.args) if fc.args else "{}")

            # Execute tools — parallel if multiple, sequential if single
            def _run_tool(meta):
                fc = meta["fc"]
                t0 = time.time()
                result = execute_tool(fc.name, dict(fc.args) if fc.args else {})
                elapsed = time.time() - t0
                return {**meta, "result": result, "elapsed": elapsed}

            if len(fc_meta) > 1:
                # Parallel execution for I/O-bound scrapers
                logger.info("  ⚡ Executing %d tools in parallel",
                            len(fc_meta))
                completed = []
                with ThreadPoolExecutor(max_workers=len(fc_meta)) as pool:
                    futures = {pool.submit(_run_tool, m): m for m in fc_meta}
                    for future in as_completed(futures):
                        completed.append(future.result())
                # Restore original order
                completed.sort(key=lambda r: r["call_num"])
            else:
                completed = [_run_tool(fc_meta[0])]

            # Process results and build response parts
            function_response_parts = []
            for entry in completed:
                fc = entry["fc"]
                result = entry["result"]
                elapsed = entry["elapsed"]
                call_num = entry["call_num"]

                logger.info("  │  Result: %d chars (%.1fs)",
                            len(result), elapsed)
                logger.info("  └─ Done: %s", fc.name)

                # Log the tool call
                tool_log.append({
                    "iteration": iteration,
                    "tool": fc.name,
                    "args": dict(fc.args) if fc.args else {},
                    "result_length": len(result),
                    "elapsed_seconds": round(elapsed, 2),
                })

                # Save raw tool results to run dir
                if run_dir:
                    result_file = run_dir / f"tool_{call_num}_{fc.name}.txt"
                    result_file.write_text(result, encoding="utf-8")

                # Condense oversized tool results via chunk-summarization
                result = self._condense_if_needed(
                    result, fc.name,
                    dict(fc.args) if fc.args else {},
                    user_message,
                )

                # Build function response part (with id to map back to the call)
                fr_part = types.Part(
                    function_response=types.FunctionResponse(
                        name=fc.name,
                        response={"result": result},
                        id=fc.id,
                    )
                )
                function_response_parts.append(fr_part)

            # Send function responses back to the model
            contents.append(
                types.Content(role="user", parts=function_response_parts)
            )

            # ── Context overflow guard ────────────────────────────────
            # Estimate total context and shrink if it would crowd out
            # the model's output budget.
            total_chars = sum(
                len(p.text or "")
                + len(
                    (p.function_response.response or {}).get("result", "")
                    if p.function_response else ""
                )
                for c in contents for p in c.parts
            )
            if total_chars > MAX_CONTEXT_CHARS:
                logger.info("")
                logger.info("  ⚠ Context overflow: %d chars > %d limit — "
                            "re-condensing largest tool results",
                            total_chars, MAX_CONTEXT_CHARS)
                self._shrink_context(contents, user_message, MAX_CONTEXT_CHARS)

        # Iteration limit reached — ask for final response without tools
        logger.warning("  ⚠ Tool iteration limit (%d) reached — requesting final response",
                       MAX_TOOL_ITERATIONS)
        config_no_tools = self._make_config(system_prompt, include_tools=False)
        contents.append(
            types.Content(
                role="user",
                parts=[types.Part(text=(
                    "You have reached the maximum number of tool calls. "
                    "Please provide your final analysis now based on all "
                    "the data you have gathered so far."
                ))],
            )
        )
        response = self._call_with_retry(
            contents, config_no_tools, self._call_count + 1
        )
        return response.text or ""

    def _call_with_retry(
        self,
        contents: list,
        config: Any,
        call_id: int,
    ) -> Any:
        """Make a Gemini API call with retry on rate-limit/server errors."""
        last_exc = None

        for attempt in range(1, self.MAX_RETRIES + 1):
            t0 = time.time()
            try:
                response = self._client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=config,
                )
                elapsed = time.time() - t0

                # Track token usage
                usage = getattr(response, 'usage_metadata', None)
                if usage:
                    inp = getattr(usage, 'prompt_token_count', 0) or 0
                    out = getattr(usage, 'candidates_token_count', 0) or 0
                    self._total_input_tokens += inp
                    self._total_output_tokens += out
                    self._total_tokens += inp + out
                    logger.info("[LLM #%d] API call completed in %.1fs (attempt %d) "
                                "— tokens: %d in / %d out",
                                call_id, elapsed, attempt, inp, out)
                else:
                    logger.info("[LLM #%d] API call completed in %.1fs (attempt %d)",
                                call_id, elapsed, attempt)
                return response

            except Exception as exc:
                last_exc = exc
                elapsed = time.time() - t0
                err_name = type(exc).__name__
                err_msg = str(exc)

                # Retry on rate-limit/server errors
                if any(keyword in err_msg.lower() for keyword in
                       ("timeout", "connection", "rate", "429", "503",
                        "resource exhausted", "quota", "500", "overloaded")):
                    delay = min(
                        self.BASE_DELAY * (2 ** (attempt - 1)),
                        self.MAX_DELAY,
                    )
                    logger.warning(
                        "[LLM #%d] %s after %.1fs (attempt %d/%d) — "
                        "retrying in %.1fs: %s",
                        call_id, err_name, elapsed, attempt,
                        self.MAX_RETRIES, delay, err_msg[:200],
                    )
                    time.sleep(delay)
                    continue
                else:
                    raise

        # All retries exhausted
        logger.error("[LLM #%d] All %d retries exhausted", call_id, self.MAX_RETRIES)
        raise RuntimeError(
            f"LLM call failed after {self.MAX_RETRIES} retries: {last_exc}"
        ) from last_exc

    def simple_call(self, system: str, user: str,
                    label: str = "", agent_name: str = "") -> str:
        """
        Simple single-turn LLM call without tools.

        Used for auxiliary tasks like knowledge extraction.
        """
        types = self._types
        self._call_count += 1
        call_id = self._call_count
        tag = f"[LLM #{call_id}]"
        if agent_name:
            tag += f" [{agent_name}]"
        if label:
            tag += f" ({label})"

        logger.info("%s simple_call — %s / %s", tag, self.provider, self.model)

        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=self.temperature,
            max_output_tokens=self.max_tokens,
        )
        contents = [
            types.Content(
                role="user",
                parts=[types.Part(text=user)],
            )
        ]

        response = self._call_with_retry(contents, config, call_id)
        return response.text or ""

    # ── Data condensation ─────────────────────────────────────────

    def _shrink_context(
        self,
        contents: list,
        user_request: str,
        target_chars: int,
    ) -> None:
        """
        Shrink conversation context in-place by re-condensing the largest
        function_response results until total size fits within *target_chars*.

        Called when accumulated tool results exceed MAX_CONTEXT_CHARS,
        which would leave no room for the model's output.
        """
        types = self._types

        # Collect all function_response parts with their sizes
        fr_refs: list[tuple[int, int, int, str]] = []  # (content_idx, part_idx, size, name)
        for ci, content in enumerate(contents):
            for pi, part in enumerate(content.parts):
                if part.function_response:
                    result = (part.function_response.response or {}).get("result", "")
                    fr_refs.append((ci, pi, len(result), part.function_response.name))

        # Sort by size descending — shrink the biggest first
        fr_refs.sort(key=lambda r: r[2], reverse=True)

        for ci, pi, size, name in fr_refs:
            # Re-estimate total
            total = sum(
                len(p.text or "")
                + len(
                    (p.function_response.response or {}).get("result", "")
                    if p.function_response else ""
                )
                for c in contents for p in c.parts
            )
            if total <= target_chars:
                logger.info("  ✓ Context shrunk to %d chars (target: %d)",
                            total, target_chars)
                break

            part = contents[ci].parts[pi]
            result = (part.function_response.response or {}).get("result", "")

            # Only re-condense if this part is large enough to matter
            if len(result) < 10_000:
                continue

            # Target: shrink this result to fit proportionally
            overshoot = total - target_chars
            new_max = max(len(result) - overshoot, len(result) // 4)

            logger.info("  Shrinking %s result: %d → ~%d chars",
                        name, len(result), new_max)

            condensed = self._condense_if_needed(
                result, name, {}, user_request,
                force_max_chars=new_max,
            )

            # Replace the part in-place
            contents[ci].parts[pi] = types.Part(
                function_response=types.FunctionResponse(
                    name=name,
                    response={"result": condensed},
                    id=part.function_response.id,
                )
            )

    def _condense_if_needed(
        self,
        result: str,
        tool_name: str,
        tool_args: dict,
        user_request: str,
        force_max_chars: int | None = None,
    ) -> str:
        """
        If *result* exceeds the size threshold, split it into chunks,
        summarize each chunk with a lightweight LLM call, and return the
        combined summaries.

        Parameters
        ----------
        force_max_chars : int, optional
            If set, override MAX_TOOL_RESULT_CHARS with this value.
            Used by _shrink_context for second-pass condensation.
        """
        threshold = force_max_chars if force_max_chars is not None else MAX_TOOL_RESULT_CHARS
        if len(result) <= threshold:
            return result

        original_chars = len(result)
        logger.info("")
        logger.info("  ⚡ Tool '%s' returned %d chars (> %d limit) — condensing...",
                    tool_name, original_chars, MAX_TOOL_RESULT_CHARS)

        # Split into line-aware chunks
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

        # Summarize each chunk — parallel for speed
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

        # Run condenser calls in parallel
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

        # Sort by chunk index to maintain order
        results.sort(key=lambda r: r[0])
        summaries = [
            f"## {tool_name} — Condensed Summary (Part {i}/{len(chunks)})\n\n{summary}"
            for i, summary in results
        ]

        combined = "\n\n---\n\n".join(summaries)
        logger.info(
            "  ✓ Condensed %d chars → %d chars (%d chunks)",
            original_chars, len(combined), len(chunks),
        )
        return combined
