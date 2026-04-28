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
from pathlib import Path
from typing import Any

import yaml

from marketsage.tools import TOOL_DECLARATIONS, execute_tool

logger = logging.getLogger("marketsage.llm")

_SETTINGS_PATH = Path(__file__).parent / "settings.yaml"

# Maximum number of tool-call round-trips before forcing a text response
MAX_TOOL_ITERATIONS = 25


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
            kwargs["tools"] = [
                types.Tool(function_declarations=TOOL_DECLARATIONS)
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

            # Process function calls and build responses
            function_response_parts = []
            for fc_part in function_calls:
                fc = fc_part.function_call
                self._tool_call_count += 1
                logger.info("")
                logger.info("  ┌─ Tool Call #%d: %s", self._tool_call_count, fc.name)
                logger.info("  │  Args: %s", dict(fc.args) if fc.args else "{}")

                # Execute the tool
                t0 = time.time()
                result = execute_tool(fc.name, dict(fc.args) if fc.args else {})
                elapsed = time.time() - t0

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
                    result_file = run_dir / f"tool_{self._tool_call_count}_{fc.name}.txt"
                    result_file.write_text(result, encoding="utf-8")

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
