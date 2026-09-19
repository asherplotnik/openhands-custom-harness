"""Small, dependency-free presentation helpers for the Bank Agent CLI."""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
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
    return f"\n{user_header()} "


def user_header() -> str:
    """Return the stable label used for submitted user messages."""
    return style("You >", ACCENT)


def echo_user_message(message: str) -> None:
    """Show a message submitted outside the terminal's normal input line."""
    print(f"\n{user_header()}\n{message}")


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
    """Render one changing line describing the latest known activity."""

    _FRAMES = ("█    ", " █   ", "  █  ", "   █ ", "    █")
    _INTERVAL_SECONDS = 0.14

    def __init__(self, message: str, started_at: float) -> None:
        self._stop = threading.Event()
        self._hidden = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._shown = False
        self._message = _one_line(message)
        self._started_at = started_at
        self._rendered_width = 0

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

    def update(self, message: str) -> None:
        """Replace the status text while keeping the task's elapsed timer."""
        with self._lock:
            self._message = _one_line(message)

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
                    elapsed = max(0.0, time.monotonic() - self._started_at)
                    plain = format_activity_line(
                        self._FRAMES[frame_index],
                        self._message,
                        elapsed,
                        shutil.get_terminal_size(fallback=(100, 24)).columns,
                    )
                    clear_width = max(self._rendered_width, len(plain))
                    sys.stdout.write(f"\r{' ' * clear_width}\r{style(plain, '94')}")
                    sys.stdout.flush()
                    self._shown = True
                    self._rendered_width = len(plain)
            frame_index = (frame_index + 1) % len(self._FRAMES)
            self._stop.wait(self._INTERVAL_SECONDS)

    def _clear_line(self) -> None:
        sys.stdout.write(f"\r{' ' * self._rendered_width}\r")
        sys.stdout.flush()
        self._rendered_width = 0


def _one_line(value: str) -> str:
    """Collapse event summaries into a stable single terminal line."""
    return " ".join(value.split()) or "Analyzing request"


def format_elapsed(seconds: float) -> str:
    """Format elapsed task time compactly for the activity line."""
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def format_activity_line(
    frame: str, message: str, elapsed_seconds: float, terminal_width: int
) -> str:
    """Build a width-bounded activity line without ANSI escape sequences."""
    prefix = f"  {frame}  "
    suffix = f"  ·  {format_elapsed(elapsed_seconds)}"
    available = max(1, terminal_width - len(prefix) - len(suffix) - 1)
    label = _one_line(message)
    if len(label) > available:
        label = f"{label[: max(1, available - 1)]}…"
    return f"{prefix}{label}{suffix}"


_active_activity: ActivityIndicator | None = None
_activity_message = "Analyzing request"
_activity_started_at = time.monotonic()
_activity_state_lock = threading.Lock()


def reset_activity_status(message: str = "Analyzing request") -> None:
    """Start a fresh elapsed timer and status for one user task."""
    global _activity_message, _activity_started_at
    with _activity_state_lock:
        _activity_message = _one_line(message)
        _activity_started_at = time.monotonic()
        active = _active_activity
    if active is not None:
        active.update(_activity_message)


def update_activity_status(message: str) -> None:
    """Publish the latest truthful activity summary to this and later run cycles."""
    global _activity_message
    with _activity_state_lock:
        _activity_message = _one_line(message)
        active = _active_activity
    if active is not None:
        active.update(_activity_message)


def run_with_activity(conversation: RunnableConversation) -> None:
    """Run one SDK turn with a non-persistent visual activity indicator."""
    global _active_activity
    with _activity_state_lock:
        activity = ActivityIndicator(_activity_message, _activity_started_at)
        _active_activity = activity
    activity.start()
    try:
        conversation.run()
    finally:
        activity.stop()
        with _activity_state_lock:
            if _active_activity is activity:
                _active_activity = None


def hide_activity_indicator() -> None:
    """Prevent the indicator from overwriting a visible agent message or error."""
    with _activity_state_lock:
        active = _active_activity
    if active is not None:
        active.hide()
