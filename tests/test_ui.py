"""Tests for the dependency-free CLI presentation helpers."""

from __future__ import annotations

import os
import unittest
from io import StringIO
from unittest.mock import patch

from bank_agent.ui import (
    ACCENT,
    format_activity_line,
    format_elapsed,
    screen_title,
    shortcut_bar,
    style,
)


class InteractiveStream:
    """Small stream stand-in whose only relevant behavior is TTY detection."""

    @staticmethod
    def isatty() -> bool:
        return True


class UiTests(unittest.TestCase):
    def test_redirected_output_remains_plain_text(self) -> None:
        self.assertEqual(style("hello", ACCENT, stream=StringIO()), "hello")

    def test_no_color_disables_ansi_for_interactive_output(self) -> None:
        with patch.dict(os.environ, {"NO_COLOR": ""}):
            self.assertEqual(style("hello", ACCENT, stream=InteractiveStream()), "hello")

    def test_interactive_output_uses_ansi_without_changing_words(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            rendered = style("hello", ACCENT, stream=InteractiveStream())

        self.assertIn("\033[", rendered)
        self.assertIn("hello", rendered)

    def test_plain_helpers_keep_stable_readable_labels(self) -> None:
        with patch("bank_agent.ui.color_enabled", return_value=False):
            self.assertEqual(screen_title("Sessions"), "BANK AGENT  >  SESSIONS")
            self.assertEqual(
                shortcut_bar((("Enter", "resume"), ("Esc", "quit"))),
                "Enter resume | Esc quit",
            )

    def test_elapsed_time_stays_compact_as_tasks_get_longer(self) -> None:
        self.assertEqual(format_elapsed(8.9), "8s")
        self.assertEqual(format_elapsed(68), "1m 08s")
        self.assertEqual(format_elapsed(3723), "1h 02m 03s")

    def test_activity_line_collapses_and_truncates_event_summaries(self) -> None:
        line = format_activity_line(
            "  █  ",
            "Reading   a very long\nsource file to understand its behavior",
            68,
            terminal_width=54,
        )

        self.assertNotIn("\n", line)
        self.assertLess(len(line), 54)
        self.assertIn("Reading a very long", line)
        self.assertIn("…", line)
        self.assertTrue(line.endswith("1m 08s"))


if __name__ == "__main__":
    unittest.main()
