"""Direct LiteLLM provider for the bank's Vertex-shaped Gemini connector.

The connector exposes ``generateContent`` rather than OpenAI Chat Completions.
This adapter keeps that translation inside the Bank Agent process: no local HTTP
server, port, or sidecar is required.
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator, Callable, Iterable
from typing import Any
from uuid import uuid4

import httpx
import litellm
from litellm import CustomLLM
from litellm.llms.custom_llm import CustomLLMError
from litellm.types.utils import Choices, ModelResponse

PROVIDER_NAME = "bank_gemini"


def register() -> None:
    """Register the direct connector exactly once for this Python process."""
    if not any(item["provider"] == PROVIDER_NAME for item in litellm.custom_provider_map):
        litellm.custom_provider_map.append({"provider": PROVIDER_NAME, "custom_handler": GeminiConnectorLLM()})


def _value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _text_parts(content: Any) -> list[dict[str, str]]:
    if isinstance(content, str):
        return [{"text": content}]
    if not isinstance(content, list):
        return []
    parts: list[dict[str, str]] = []
    for part in content:
        text = _value(part, "text")
        if _value(part, "type") == "text" and isinstance(text, str):
            parts.append({"text": text})
    return parts


def _call_id(signature: str | None) -> str:
    if signature:
        return "call_sig_" + base64.urlsafe_b64encode(signature.encode()).decode().rstrip("=")
    return "call_" + uuid4().hex


def _signature(call_id: str | None) -> str | None:
    if not call_id or not call_id.startswith("call_sig_"):
        return None
    try:
        encoded = call_id.removeprefix("call_sig_")
        return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode() or None
    except (UnicodeDecodeError, ValueError):
        return None


def _schema(schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    result = {key: schema[key] for key in ("type", "description", "format", "nullable", "minimum", "maximum", "minLength", "maxLength", "enum") if key in schema}
    if isinstance(schema.get("properties"), dict):
        result["properties"] = {name: _schema(value) for name, value in schema["properties"].items()}
    if "items" in schema:
        result["items"] = _schema(schema["items"])
    if isinstance(schema.get("required"), list):
        result["required"] = schema["required"]
    return result


def _request_body(messages: list[Any], tools: Iterable[Any] | None) -> dict[str, Any]:
    tool_names: dict[str, str] = {}
    for message in messages:
        for call in _value(message, "tool_calls", []) or []:
            function = _value(call, "function", {})
            call_id = _value(call, "id")
            name = _value(function, "name")
            if call_id and name:
                tool_names[call_id] = name

    system_parts: list[dict[str, str]] = []
    contents: list[dict[str, Any]] = []
    pending_responses: list[dict[str, Any]] = []

    def flush_responses() -> None:
        if pending_responses:
            contents.append({"role": "user", "parts": pending_responses.copy()})
            pending_responses.clear()

    for message in messages:
        role = _value(message, "role")
        if role in {"system", "developer"}:
            system_parts.extend(_text_parts(_value(message, "content")))
            continue
        if role == "tool":
            call_id = _value(message, "tool_call_id")
            pending_responses.append({"functionResponse": {"name": tool_names.get(call_id, "tool"), "response": {"content": _value(message, "content", "")}}})
            continue

        flush_responses()
        parts: list[dict[str, Any]] = _text_parts(_value(message, "content"))
        for call in _value(message, "tool_calls", []) or []:
            function = _value(call, "function", {})
            name = _value(function, "name")
            if not name:
                continue
            arguments = _value(function, "arguments", "{}")
            try:
                args = json.loads(arguments) if isinstance(arguments, str) else arguments
            except json.JSONDecodeError:
                args = {}
            function_call: dict[str, Any] = {"name": name, "args": args if isinstance(args, dict) else {}}
            part: dict[str, Any] = {"functionCall": function_call}
            signature = _signature(_value(call, "id"))
            if signature:
                part["thoughtSignature"] = signature
            parts.append(part)
        if parts:
            contents.append({"role": "model" if role == "assistant" else "user", "parts": parts})
    flush_responses()

    body: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "thinkingConfig": {
                "includeThoughts": True,
            }
        },
    }
    if system_parts:
        body["systemInstruction"] = {"parts": system_parts}
    declarations = []
    for tool in tools or []:
        function = _value(tool, "function", {})
        name = _value(function, "name")
        if name:
            declarations.append({"name": name, "description": _value(function, "description", ""), "parameters": _schema(_value(function, "parameters", {}))})
    if declarations:
        body["tools"] = [{"functionDeclarations": declarations}]
    return body


class GeminiConnectorLLM(CustomLLM):
    """LiteLLM adapter for the non-streaming bank Gemini connector."""

    @staticmethod
    def _url(api_base: str, model: str) -> str:
        return f"{api_base.rstrip('/')}/models/{model.removeprefix(PROVIDER_NAME + '/')}:generateContent"

    @staticmethod
    def _call(api_base: str, api_key: str, model: str, messages: list[Any], optional_params: dict[str, Any], timeout: Any) -> dict[str, Any]:
        response = httpx.post(
            GeminiConnectorLLM._url(api_base, model),
            headers={"content-type": "application/json", "x-goog-api-key": api_key},
            json=_request_body(messages, optional_params.get("tools")),
            timeout=timeout or 120,
        )
        if response.status_code >= 400:
            raise CustomLLMError(response.status_code, response.text)
        return response.json()

    @staticmethod
    def _choices(
        payload: dict[str, Any],
    ) -> tuple[str, str, list[dict[str, Any]], str]:
        candidate = (payload.get("candidates") or [{}])[0]
        text = ""
        reasoning = ""
        tool_calls: list[dict[str, Any]] = []
        for part in candidate.get("content", {}).get("parts", []):
            if isinstance(part.get("text"), str):
                if part.get("thought") is True:
                    reasoning += part["text"]
                else:
                    text += part["text"]
            call = part.get("functionCall")
            if isinstance(call, dict) and call.get("name"):
                tool_calls.append({"id": _call_id(part.get("thoughtSignature")), "type": "function", "function": {"name": call["name"], "arguments": json.dumps(call.get("args", {}))}})
        finish = "tool_calls" if tool_calls else "stop"
        return text, reasoning, tool_calls, finish

    def completion(self, model: str, messages: list, api_base: str, custom_prompt_dict: dict, model_response: ModelResponse, print_verbose: Callable, encoding: Any, api_key: str, logging_obj: Any, optional_params: dict, **kwargs: Any) -> ModelResponse:
        text, reasoning, tool_calls, finish = self._choices(self._call(api_base, api_key, model, messages, optional_params, kwargs.get("timeout")))
        message: dict[str, Any] = {
            "role": "assistant",
            "content": text or None,
            "reasoning_content": reasoning or None,
        }
        if tool_calls:
            message["tool_calls"] = tool_calls
        model_response.choices = [Choices(index=0, message=message, finish_reason=finish)]
        return model_response

    async def acompletion(self, *args: Any, **kwargs: Any) -> ModelResponse:
        return self.completion(*args, **kwargs)

    async def astreaming(self, model: str, messages: list, api_base: str, custom_prompt_dict: dict, model_response: ModelResponse, print_verbose: Callable, encoding: Any, api_key: str, logging_obj: Any, optional_params: dict, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        text, reasoning, tool_calls, finish = self._choices(self._call(api_base, api_key, model, messages, optional_params, kwargs.get("timeout")))
        if reasoning:
            yield {
                "text": "",
                "is_finished": False,
                "finish_reason": "",
                "usage": None,
                "provider_specific_fields": {
                    "reasoning_content": reasoning,
                },
            }
        if text:
            yield {"text": text, "is_finished": False, "finish_reason": "", "usage": None}
        for tool_call in tool_calls:
            yield {"text": "", "tool_use": tool_call, "is_finished": False, "finish_reason": "", "usage": None}
        yield {"text": "", "is_finished": True, "finish_reason": finish, "usage": None}
