"""Trusted storage for exact, user-approved read-only terminal commands."""

from __future__ import annotations

import json
from pathlib import Path


class CommandAllowlist:
    """Persist exact commands approved from the local CLI confirmation prompt."""

    def __init__(self, path: Path, commands: set[str] | None = None) -> None:
        self.path = path
        self.commands = commands or set()

    @classmethod
    def load(cls, path: Path) -> "CommandAllowlist":
        if not path.is_file():
            return cls(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"Could not read command allowlist: {path}") from error

        commands = data.get("commands") if isinstance(data, dict) else None
        if not isinstance(commands, list) or not all(
            isinstance(command, str) for command in commands
        ):
            raise ValueError(f"Invalid command allowlist format: {path}")
        return cls(path, {normalize_command(command) for command in commands})

    def contains(self, command: str) -> bool:
        return normalize_command(command) in self.commands

    def add(self, command: str) -> None:
        self.commands.add(normalize_command(command))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "commands": sorted(self.commands)}
        temporary_path = self.path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        temporary_path.replace(self.path)


def normalize_command(command: str) -> str:
    """Normalize harmless whitespace differences without interpreting shell syntax."""
    return " ".join(command.split())
