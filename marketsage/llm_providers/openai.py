"""
OpenAI provider — subclass of BaseLLMClient.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from marketsage.llm_client import BaseLLMClient, LLMResponse, ToolCall, ToolResult

logger = logging.getLogger("marketsage.llm.openai")


class OpenAIClient(BaseLLMClient):
    """OpenAI LLM client using the openai SDK."""

    def _init_sdk(self) -> None:
        try:
            import openai
            self._openai = openai
            self._client = openai.OpenAI(api_key=self.api_key)
        except ImportError as exc:
            raise ImportError(
                "openai SDK not installed. Run: pip install openai"
            ) from exc

    def _api_call(self, messages: list[dict], system: str,
                  tools: Any | None) -> Any:
        # Ensure system message is first
        full_messages = [{"role": "system", "content": system}] + messages
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": full_messages,
            "temperature": self.temperature,
            "max_completion_tokens": self.max_tokens,
        }
        if tools is not None:
            kwargs["tools"] = tools
        return self._client.chat.completions.create(**kwargs)

    def _parse_response(self, raw: Any) -> LLMResponse:
        choice = raw.choices[0]
        msg = choice.message
        text = msg.content or ""
        tool_calls: list[ToolCall] = []

        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    args = {}
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    args=args,
                ))

        usage = raw.usage
        inp = usage.prompt_tokens if usage else 0
        out = usage.completion_tokens if usage else 0

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            raw=raw,
            input_tokens=inp,
            output_tokens=out,
        )

    def _make_initial_messages(self, user_text: str) -> list[dict]:
        return [{"role": "user", "content": user_text}]

    def _append_assistant(self, messages: list[dict],
                          response: LLMResponse) -> None:
        msg = response.raw.choices[0].message
        # Serialize to dict for the message list
        entry: dict[str, Any] = {
            "role": "assistant",
            "content": msg.content or "",
        }
        if msg.tool_calls:
            entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
        messages.append(entry)

    def _append_tool_results(self, messages: list[dict],
                             results: list[ToolResult]) -> None:
        for tr in results:
            messages.append({
                "role": "tool",
                "tool_call_id": tr.call_id,
                "content": tr.result,
            })

    def _build_tools(self, declarations: list[dict]) -> list[dict]:
        tools = []
        for decl in declarations:
            tools.append({
                "type": "function",
                "function": {
                    "name": decl["name"],
                    "description": decl.get("description", ""),
                    "parameters": decl.get("parameters", {}),
                },
            })
        return tools

    def _estimate_context_chars(self, messages: list[dict]) -> int:
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += len(content)
        return total

    def _find_largest_tool_result(
        self, messages: list[dict]
    ) -> tuple[str, int, str] | None:
        largest: tuple[str, int, str] | None = None
        for msg in messages:
            if msg.get("role") == "tool":
                text = msg.get("content", "")
                size = len(text)
                if largest is None or size > largest[1]:
                    # Try to find the tool name from preceding assistant msg
                    name = msg.get("tool_call_id", "unknown")
                    largest = (name, size, text)
        return largest

    def _shrink_tool_result_in_messages(
        self, messages: list[dict], name: str, condensed: str,
    ) -> None:
        # Find largest tool message and replace its content
        max_size = 0
        target_idx = None
        for i, msg in enumerate(messages):
            if msg.get("role") == "tool":
                size = len(msg.get("content", ""))
                if size > max_size:
                    max_size = size
                    target_idx = i
        if target_idx is not None:
            messages[target_idx]["content"] = condensed
