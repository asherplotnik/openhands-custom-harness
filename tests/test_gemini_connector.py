from __future__ import annotations

import unittest
from unittest.mock import patch

from litellm.types.utils import ModelResponse

from bank_agent.gemini_connector import GeminiConnectorLLM, _request_body


class GeminiConnectorTests(unittest.TestCase):
    def test_request_enables_readable_thought_summaries(self) -> None:
        body = _request_body(
            [{"role": "user", "content": "Investigate the failure"}],
            tools=None,
        )

        self.assertEqual(
            body["generationConfig"]["thinkingConfig"],
            {"includeThoughts": True},
        )

    def test_choices_separate_thought_summaries_from_visible_text(self) -> None:
        payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "I should inspect the failing test.", "thought": True},
                            {"text": "I found the cause."},
                            {
                                "functionCall": {
                                    "name": "search",
                                    "args": {"query": "failure"},
                                },
                                "thoughtSignature": "opaque-signature",
                            },
                        ]
                    }
                }
            ]
        }

        text, reasoning, tool_calls, finish = GeminiConnectorLLM._choices(payload)

        self.assertEqual(text, "I found the cause.")
        self.assertEqual(reasoning, "I should inspect the failing test.")
        self.assertEqual(finish, "tool_calls")
        self.assertEqual(tool_calls[0]["function"]["name"], "search")
        self.assertTrue(tool_calls[0]["id"].startswith("call_sig_"))

    def test_completion_maps_thought_summary_to_reasoning_content(self) -> None:
        payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Check the configuration first.", "thought": True},
                            {"text": "The configuration is invalid."},
                        ]
                    }
                }
            ]
        }
        handler = GeminiConnectorLLM()
        response = ModelResponse()

        with patch.object(handler, "_call", return_value=payload):
            result = handler.completion(
                model="gemini-3.8-flash",
                messages=[],
                api_base="https://connector.example",
                custom_prompt_dict={},
                model_response=response,
                print_verbose=lambda *_: None,
                encoding=None,
                api_key="test-key",
                logging_obj=None,
                optional_params={},
            )

        message = result.choices[0].message
        self.assertEqual(message.content, "The configuration is invalid.")
        self.assertEqual(
            message.reasoning_content,
            "Check the configuration first.",
        )


if __name__ == "__main__":
    unittest.main()
