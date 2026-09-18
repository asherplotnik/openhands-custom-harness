"""Tests for the dependency-free CLI presentation helpers."""

from __future__ import annotations

import os
import unittest
from io import StringIO
from unittest.mock import patch

from bank_agent.ui import ACCENT, screen_title, shortcut_bar, style


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


if __name__ == "__main__":
    unittest.main()
