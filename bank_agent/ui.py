"""Small, dependency-free presentation helpers for the Bank Agent CLI."""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Iterable
from typing import Protocol, TextIO


RESET = "0"
BRAND = "1;96"
ACCENT = "1;36"
TEXT = "1;37"
MUTED = "90"
SUCCESS = "1;92"
WARNING = "1;93"
ERROR = "1;91"


def color_enabled(stream: TextIO | None = None) -> bool:
    """Return whether ANSI styling is appropriate for this output stream."""
    target = stream or sys.stdout
    return target.isatty() and "NO_COLOR" not in os.environ


def style(text: object, code: str, *, stream: TextIO | None = None) -> str:
    """Apply ANSI styling only for an interactive terminal."""
    value = str(text)
    if not color_enabled(stream):
        return value
    return f"\033[{code}m{value}\033[{RESET}m"


def user_prompt() -> str:
    """Return a distinct prompt so submitted text is identifiable in scrollback."""
    return f"\n{style('You >', ACCENT)} "


def echo_user_message(message: str) -> None:
    """Show a message submitted outside the terminal's normal input line."""
    print(f"\n{style('You >', ACCENT)}\n{message}")


def agent_header() -> str:
    """Return a stable, high-contrast label for agent responses."""
    return style("Agent >", SUCCESS)


def screen_title(section: str) -> str:
    """Render the common heading used by full-screen menus."""
    return (
        f"{style('BANK AGENT', BRAND)}  "
        f"{style('>', MUTED)}  {style(section.upper(), TEXT)}"
    )


def selection_marker(selected: bool) -> str:
    """Return a stable-width marker for an arrow-key menu row."""
    return style(">", ACCENT) if selected else " "


def selected_text(text: str, selected: bool) -> str:
    """Emphasize only the currently selected menu label."""
    return style(text, ACCENT) if selected else text


def shortcut_bar(items: Iterable[tuple[str, str]]) -> str:
    """Render a consistent key/action footer without changing its wording."""
    rendered = [f"{style(key, ACCENT)} {style(action, MUTED)}" for key, action in items]
    return style(" | ", MUTED).join(rendered)


def field(label: str, value: object) -> str:
    """Render one compact metadata label/value pair."""
    return f"{style(f'{label:<12}', MUTED)} {value}"


def status(value: str) -> str:
    """Color familiar state words while keeping unknown states neutral."""
    normalized = value.strip().casefold()
    if normalized in {"active", "valid", "ready"}:
        code = SUCCESS
    elif normalized in {"archived", "disabled"}:
        code = MUTED
    elif normalized in {"invalid", "failed", "error"}:
        code = ERROR
    else:
        code = TEXT
    return style(value, code)


def notice(text: str, *, tone: str = "info") -> str:
    """Render a short notice with a restrained semantic marker."""
    marker, code = {
        "success": ("+", SUCCESS),
        "warning": ("!", WARNING),
        "error": ("!", ERROR),
        "info": ("i", ACCENT),
    }.get(tone, ("i", ACCENT))
    return f"{style(marker, code)} {text}"


class RunnableConversation(Protocol):
    """Minimal conversation surface required by the activity wrapper."""

    def run(self) -> object: ...


class ActivityIndicator:
    """Render a quiet terminal-only activity wave while the model is running."""

    _FRAMES = ("█    ", " █   ", "  █  ", "   █ ", "    █")
    _INTERVAL_SECONDS = 0.14
    _CLEAR_WIDTH = 32

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._hidden = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._shown = False

    def start(self) -> None:
        if not sys.stdout.isatty():
            return
        self._thread = threading.Thread(target=self._animate, daemon=True)
        self._thread.start()

    def hide(self) -> None:
        """Clear the line permanently before another visible console message."""
        self._hidden.set()
        with self._lock:
            if self._shown:
                self._clear_line()
                self._shown = False

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        with self._lock:
            if self._shown:
                self._clear_line()
                self._shown = False

    def _animate(self) -> None:
        frame_index = 0
        while not self._stop.is_set():
            if not self._hidden.is_set():
                with self._lock:
                    line = style(f"  {self._FRAMES[frame_index]}  working", "94")
                    sys.stdout.write(f"\r{line}")
                    sys.stdout.flush()
                    self._shown = True
            frame_index = (frame_index + 1) % len(self._FRAMES)
            self._stop.wait(self._INTERVAL_SECONDS)

    def _clear_line(self) -> None:
        sys.stdout.write(f"\r{' ' * self._CLEAR_WIDTH}\r")
        sys.stdout.flush()


_active_activity: ActivityIndicator | None = None


def run_with_activity(conversation: RunnableConversation) -> None:
    """Run one SDK turn with a non-persistent visual activity indicator."""
    global _active_activity
    activity = ActivityIndicator()
    _active_activity = activity
    activity.start()
    try:
        conversation.run()
    finally:
        activity.stop()
        if _active_activity is activity:
            _active_activity = None


def hide_activity_indicator() -> None:
    """Prevent the indicator from overwriting a visible agent message or error."""
    if _active_activity is not None:
        _active_activity.hide()
