from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from litellm.types.utils import ModelResponse


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bank_agent.llm import BankAgentLLM, _groq_reasoning
from openhands.sdk import LLM
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.llm.llm_response import LLMResponse
from openhands.sdk.llm.utils.metrics import MetricsSnapshot


def groq_response(reasoning: str | None = "Inspect the files first.") -> ModelResponse:
    message = {
        "role": "assistant",
        "content": "I found the relevant file.",
        "reasoning": reasoning,
    }
    return ModelResponse(
        id="groq-response",
        model="openai/gpt-oss-120b",
        choices=[{"index": 0, "message": message}],
    )


class BankAgentLLMTests(unittest.TestCase):
    def test_reads_groq_provider_reasoning_field(self) -> None:
        self.assertEqual(
            _groq_reasoning(groq_response()),
            "Inspect the files first.",
        )

    def test_completion_normalizes_groq_reasoning_for_openhands(self) -> None:
        raw_response = groq_response()
        base_result = LLMResponse(
            message=Message(
                role="assistant",
                content=[TextContent(text="I found the relevant file.")],
            ),
            metrics=MetricsSnapshot(),
            raw_response=raw_response,
        )
        llm = BankAgentLLM.model_construct()

        with patch.object(LLM, "_build_completion_result", return_value=base_result):
            result = llm._build_completion_result(raw_response)

        self.assertEqual(
            result.message.reasoning_content,
            "Inspect the files first.",
        )

    def test_existing_normalized_reasoning_takes_precedence(self) -> None:
        raw_response = groq_response("Provider-native reasoning")
        base_result = LLMResponse(
            message=Message(
                role="assistant",
                content=[TextContent(text="Done")],
                reasoning_content="Already normalized",
            ),
            metrics=MetricsSnapshot(),
            raw_response=raw_response,
        )
        llm = BankAgentLLM.model_construct()

        with patch.object(LLM, "_build_completion_result", return_value=base_result):
            result = llm._build_completion_result(raw_response)

        self.assertEqual(result.message.reasoning_content, "Already normalized")


if __name__ == "__main__":
    unittest.main()
