"""Bank Agent-specific normalization around the OpenHands LLM adapter."""

from __future__ import annotations

from typing import Any

from litellm.types.utils import ModelResponse

from openhands.sdk import LLM
from openhands.sdk.llm.llm_response import LLMResponse


def _groq_reasoning(response: ModelResponse) -> str | None:
    """Return Groq reasoning that LiteLLM left in its provider-native field."""
    try:
        message = response["choices"][0]["message"]
    except (IndexError, KeyError, TypeError):
        return None

    reasoning: Any = getattr(message, "reasoning", None)
    if reasoning is None and isinstance(message, dict):
        reasoning = message.get("reasoning")
    return reasoning if isinstance(reasoning, str) and reasoning.strip() else None


class BankAgentLLM(LLM):
    """Normalize provider responses before OpenHands persists them.

    Groq currently exposes GPT-OSS thought summaries as ``message.reasoning``.
    OpenHands reads the provider-neutral ``message.reasoning_content`` field.
    Keep that compatibility shim in Bank Agent so neither LiteLLM nor the
    separately deployed OpenHands SDK has to be patched.
    """

    def _build_completion_result(self, response: ModelResponse) -> LLMResponse:
        result = super()._build_completion_result(response)
        if result.message.reasoning_content:
            return result

        reasoning = _groq_reasoning(response)
        if reasoning is None:
            return result

        return result.model_copy(
            update={
                "message": result.message.model_copy(
                    update={"reasoning_content": reasoning}
                )
            }
        )
