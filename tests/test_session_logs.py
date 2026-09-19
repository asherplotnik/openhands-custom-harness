from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from bank_agent.search_tool import SearchAction, SearchObservation
from bank_agent.session_logs import (
    SessionLogOptions,
    format_session_log,
    parse_session_log_command,
    write_session_log,
)
from bank_agent.sessions import SessionRecord
from openhands.sdk.event import ActionEvent, MessageEvent, ObservationEvent, PauseEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent


class SessionLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = SessionRecord(
            id="1" * 32,
            title="debug-repo",
            workspace="C:/work/debug-repo",
            provider="groq",
            model="groq/openai/gpt-oss-120b",
            created_at="2026-09-19T08:00:00Z",
            updated_at="2026-09-19T09:00:00Z",
            run_count=2,
        )

    def test_parse_log_aliases_and_composable_options(self) -> None:
        for alias in ("/l", "/log", "/logs"):
            with self.subTest(alias=alias):
                self.assertEqual(
                    parse_session_log_command(
                        f"{alias} --limit 20 --messages-only --file"
                    ),
                    SessionLogOptions(limit=20, messages_only=True, write_file=True),
                )
        self.assertIsNone(parse_session_log_command("explain the logs"))

    def test_parse_log_rejects_invalid_options(self) -> None:
        for command in ("/logs -l", "/logs -l 0", "/logs -l many", "/logs -x"):
            with self.subTest(command=command), self.assertRaises(ValueError):
                parse_session_log_command(command)

    def test_full_log_contains_messages_tool_trace_and_reasoning(self) -> None:
        user = MessageEvent(
            timestamp="2026-09-19T09:00:00",
            source="user",
            llm_message=Message(
                role="user",
                content=[TextContent(text="Find the handler")],
            ),
        )
        tool_call = MessageToolCall(
            id="call-1",
            name="search",
            arguments='{"query":"Handler","path":"src"}',
            origin="completion",
        )
        action = ActionEvent(
            timestamp="2026-09-19T09:00:01",
            source="agent",
            thought=[],
            reasoning_content="I should locate the symbol before editing.",
            action=SearchAction(query="Handler", path="src"),
            tool_name="search",
            tool_call_id="call-1",
            tool_call=tool_call,
            llm_response_id="response-1",
            summary="Locate Handler",
        )
        observation = ObservationEvent(
            timestamp="2026-09-19T09:00:02",
            source="environment",
            tool_name="search",
            tool_call_id="call-1",
            observation=SearchObservation.from_text(
                "src/Handler.java:10: class Handler",
                query="Handler",
                search_path="src",
                matches=["src/Handler.java:10: class Handler"],
            ),
            action_id=action.id,
        )

        rendered = format_session_log(
            self.session,
            [user, action, observation],
            SessionLogOptions(),
            generated_at=datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc),
        )

        self.assertIn("Find the handler", rendered)
        self.assertIn("Tool call: search", rendered)
        self.assertIn('"query": "Handler"', rendered)
        self.assertIn("Tool result: search", rendered)
        self.assertIn("src/Handler.java:10", rendered)
        self.assertIn("Reasoning summary", rendered)
        self.assertIn("I should locate the symbol", rendered)

    def test_messages_only_filters_before_applying_limit(self) -> None:
        events: list[object] = [PauseEvent(timestamp="2026-09-19T09:00:00")]
        for index in range(3):
            events.append(
                MessageEvent(
                    timestamp=f"2026-09-19T09:00:0{index + 1}",
                    source="user" if index % 2 == 0 else "agent",
                    llm_message=Message(
                        role="user" if index % 2 == 0 else "assistant",
                        content=[TextContent(text=f"message {index}")],
                    ),
                )
            )

        rendered = format_session_log(
            self.session,
            events,
            SessionLogOptions(limit=2, messages_only=True),
        )

        self.assertNotIn("message 0", rendered)
        self.assertIn("message 1", rendered)
        self.assertIn("message 2", rendered)
        self.assertNotIn("Conversation Paused", rendered)
        self.assertIn("Entries shown: `2`", rendered)

    def test_file_name_uses_session_and_windows_safe_iso_milliseconds(self) -> None:
        generated = datetime(2026, 9, 19, 12, 34, 56, 789000, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            path = write_session_log(
                Path(directory),
                self.session,
                "readable log\n",
                generated_at=generated,
            )

            self.assertEqual(
                path.name,
                "debug-repo-2026-09-19T12-34-56.789+00-00.md",
            )
            self.assertTrue(path.is_absolute())
            self.assertEqual(path.read_text(encoding="utf-8"), "readable log\n")


if __name__ == "__main__":
    unittest.main()
