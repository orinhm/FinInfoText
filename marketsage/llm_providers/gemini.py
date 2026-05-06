"""
Gemini provider — subclass of BaseLLMClient.
"""

from __future__ import annotations

import logging
from typing import Any

from marketsage.llm_client import BaseLLMClient, LLMResponse, ToolCall, ToolResult

logger = logging.getLogger("marketsage.llm.gemini")


class GeminiClient(BaseLLMClient):
    """Gemini LLM client using the google-genai SDK."""

    def _init_sdk(self) -> None:
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

    def _api_call(self, messages: list, system: str,
                  tools: Any | None) -> Any:
        types = self._types
        kwargs: dict[str, Any] = {
            "system_instruction": system,
            "temperature": self.temperature,
            "max_output_tokens": self.max_tokens,
        }
        if tools is not None:
            kwargs["tools"] = tools
        config = types.GenerateContentConfig(**kwargs)
        return self._client.models.generate_content(
            model=self.model, contents=messages, config=config,
        )

    def _parse_response(self, raw: Any) -> LLMResponse:
        candidate = raw.candidates[0]
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for part in candidate.content.parts:
            if part.function_call:
                fc = part.function_call
                tool_calls.append(ToolCall(
                    id=fc.id or "",
                    name=fc.name,
                    args=dict(fc.args) if fc.args else {},
                ))
            elif part.text:
                text_parts.append(part.text)

        usage = getattr(raw, "usage_metadata", None)
        inp = getattr(usage, "prompt_token_count", 0) or 0 if usage else 0
        out = getattr(usage, "candidates_token_count", 0) or 0 if usage else 0

        return LLMResponse(
            text="\n".join(text_parts) if text_parts else "",
            tool_calls=tool_calls,
            raw=raw,
            input_tokens=inp,
            output_tokens=out,
        )

    def _make_initial_messages(self, user_text: str) -> list:
        types = self._types
        return [
            types.Content(
                role="user",
                parts=[types.Part(text=user_text)],
            )
        ]

    def _append_assistant(self, messages: list,
                          response: LLMResponse) -> None:
        # Append the raw candidate content (preserves function_call parts)
        candidate = response.raw.candidates[0]
        messages.append(candidate.content)

    def _append_tool_results(self, messages: list,
                             results: list[ToolResult]) -> None:
        types = self._types
        parts = []
        for tr in results:
            parts.append(types.Part(
                function_response=types.FunctionResponse(
                    name=tr.name,
                    response={"result": tr.result},
                    id=tr.call_id,
                )
            ))
        messages.append(types.Content(role="user", parts=parts))

    def _build_tools(self, declarations: list[dict]) -> Any:
        types = self._types
        return [types.Tool(function_declarations=declarations)]

    def _estimate_context_chars(self, messages: list) -> int:
        total = 0
        for content in messages:
            for part in content.parts:
                if part.text:
                    total += len(part.text)
                if part.function_response:
                    r = (part.function_response.response or {})
                    total += len(r.get("result", ""))
        return total

    def _find_largest_tool_result(
        self, messages: list
    ) -> tuple[str, int, str] | None:
        largest: tuple[str, int, str] | None = None
        for content in messages:
            for part in content.parts:
                if part.function_response:
                    r = (part.function_response.response or {})
                    text = r.get("result", "")
                    if largest is None or len(text) > largest[1]:
                        largest = (part.function_response.name,
                                   len(text), text)
        return largest

    def _shrink_tool_result_in_messages(
        self, messages: list, name: str, condensed: str,
    ) -> None:
        types = self._types
        # Find the largest instance of this tool's result and replace it
        max_size = 0
        target = None
        for ci, content in enumerate(messages):
            for pi, part in enumerate(content.parts):
                if (part.function_response
                        and part.function_response.name == name):
                    r = (part.function_response.response or {})
                    size = len(r.get("result", ""))
                    if size > max_size:
                        max_size = size
                        target = (ci, pi, part)

        if target:
            ci, pi, part = target
            messages[ci].parts[pi] = types.Part(
                function_response=types.FunctionResponse(
                    name=name,
                    response={"result": condensed},
                    id=part.function_response.id,
                )
            )
