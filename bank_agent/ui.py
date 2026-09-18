"""Small, dependency-free presentation helpers for the Bank Agent CLI."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable
from typing import TextIO


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
