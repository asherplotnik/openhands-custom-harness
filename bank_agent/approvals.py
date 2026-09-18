"""Approval policy and console confirmation for agent tool actions."""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from pathlib import Path

from openhands.sdk import BaseConversation
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)

from bank_agent.allowlist import CommandAllowlist
from bank_agent.paths import AGENT_WORKSPACE, GENERAL_INSTRUCTIONS_PATH
from bank_agent.ui import ACCENT, MUTED, WARNING, run_with_activity, style


DIRECT_FILE_WRITE_TOOLS = frozenset({"write_file", "edit"})
READ_ONLY_FILE_TOOLS = frozenset(
    {"read_file", "list_directory", "invoke_skill", "search"}
)
SAFE_POWERSHELL_COMMANDS = frozenset(
    {
        "get-childitem",
        "get-content",
        "get-location",
        "get-command",
        "get-date",
        "resolve-path",
        "select-string",
        "test-path",
        "where.exe",
    }
)
SAFE_GIT_SUBCOMMANDS = frozenset(
    {
        "check-ignore",
        "describe",
        "diff",
        "grep",
        "log",
        "ls-files",
        "rev-parse",
        "show",
        "status",
    }
)
SHELL_CONTROL_CHARACTERS = frozenset({";", "|", "&", "`", ">", "<", "\n", "\r"})
WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z])([A-Za-z]:[\\/][^\s'\"`|;&<>]*)"
)


def action_payload(action_event: object) -> object:
    """Get the tool action from either an ActionEvent or a direct test action."""
    return getattr(action_event, "action", action_event)


def _direct_file_write_path(action: object, writable_root: Path) -> Path | None:
    """Resolve a direct file action target, or return None for another action."""
    payload = action_payload(action)
    tool_name = getattr(action, "tool_name", None) or getattr(payload, "tool_name", None)
    if tool_name not in DIRECT_FILE_WRITE_TOOLS:
        return None

    file_path = getattr(payload, "file_path", None)
    if not isinstance(file_path, str) or not file_path.strip():
        return None

    candidate = Path(file_path).expanduser()
    return (candidate if candidate.is_absolute() else writable_root / candidate).resolve()


def is_direct_file_write_permitted(action: object, writable_root: Path) -> bool:
    """Allow direct file edits in a workspace and the one general instructions file."""
    payload = action_payload(action)
    tool_name = getattr(action, "tool_name", None) or getattr(payload, "tool_name", None)
    if tool_name not in DIRECT_FILE_WRITE_TOOLS:
        return True

    resolved = _direct_file_write_path(action, writable_root)
    if resolved is None:
        return False
    if resolved == GENERAL_INSTRUCTIONS_PATH.resolve():
        return True
    try:
        resolved.relative_to(writable_root.resolve())
    except ValueError:
        return False
    return True


def is_workspace_file_write(action: object, writable_root: Path) -> bool:
    """Identify a direct edit whose resolved target stays in this workspace."""
    resolved = _direct_file_write_path(action, writable_root)
    if resolved is None:
        return False
    try:
        resolved.relative_to(writable_root.resolve())
    except ValueError:
        return False
    return True


def is_auto_approved_action(action: object) -> bool:
    """Return whether an action matches the conservative read-only allowlist."""
    payload = action_payload(action)
    tool_name = getattr(action, "tool_name", None) or getattr(payload, "tool_name", None)
    if tool_name in READ_ONLY_FILE_TOOLS:
        return True
    if tool_name != "terminal" or getattr(payload, "is_input", False):
        return False

    command = getattr(payload, "command", None)
    if not isinstance(command, str):
        return False
    command = command.strip()
    if not command or any(char in command for char in SHELL_CONTROL_CHARACTERS):
        return False

    try:
        tokens = shlex.split(command, posix=False)
    except ValueError:
        return False
    if not tokens:
        return False
    executable = tokens[0].lower()
    if executable in SAFE_POWERSHELL_COMMANDS or executable == "rg":
        return True
    if executable != "git":
        return False

    index = 1
    while index < len(tokens) and tokens[index].lower() == "-c":
        if index + 1 >= len(tokens):
            return False
        index += 2
    return index < len(tokens) and tokens[index].lower() in SAFE_GIT_SUBCOMMANDS


def is_allowlist_candidate(action: object) -> bool:
    """Allow a user to remember one command that stays in the agent workspace."""
    payload = action_payload(action)
    tool_name = getattr(action, "tool_name", None) or getattr(payload, "tool_name", None)
    if tool_name != "terminal" or getattr(payload, "is_input", False):
        return False

    command = getattr(payload, "command", None)
    if not isinstance(command, str):
        return False
    command = command.strip()
    if not command or any(char in command for char in SHELL_CONTROL_CHARACTERS):
        return False
    normalized = command.replace("/", "\\")
    if re.search(r"(^|\s)\.\.?(?:\\|\s|$)", normalized):
        return False
    writable_root = AGENT_WORKSPACE.resolve()
    for path_text in WINDOWS_ABSOLUTE_PATH.findall(command):
        try:
            Path(path_text).resolve().relative_to(writable_root)
        except ValueError:
            return False
    return True


def is_persistently_allowed(action: object, allowlist: CommandAllowlist) -> bool:
    """Apply stored rules only when the current command remains a safe candidate."""
    payload = action_payload(action)
    command = getattr(payload, "command", None)
    return (
        is_allowlist_candidate(action)
        and isinstance(command, str)
        and allowlist.contains(command)
    )


def _print_pending_actions(pending_actions: list[object]) -> None:
    print()
    print(style(f"APPROVAL REQUIRED  ({len(pending_actions)} action(s))", WARNING))
    for index, action in enumerate(pending_actions, start=1):
        tool_name = getattr(action, "tool_name", "unknown tool")
        summary = getattr(action, "summary", None) or "No summary provided."
        raw_action = str(getattr(action, "action", action)).replace("\n", " ")
        print(
            f"  {style(f'{index}.', ACCENT)} "
            f"{style(f'[{tool_name}]', MUTED)} {summary}"
        )
        print(f"     {style(raw_action[:500], MUTED)}")


def confirm_in_console(
    pending_actions: list[object], allowlist: CommandAllowlist | None = None
) -> bool:
    """Ask for explicit approval and reject safely on missing input."""
    _print_pending_actions(pending_actions)
    can_remember = (
        allowlist is not None
        and len(pending_actions) == 1
        and is_allowlist_candidate(pending_actions[0])
    )
    prompt = (
        "Execute this action? [y/N/a]: "
        if can_remember
        else "Execute these actions? [y/N]: "
    )
    while True:
        try:
            answer = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nNo approval received; rejecting the actions.")
            return False

        if answer in {"y", "yes"}:
            return True
        if answer in {"", "n", "no"}:
            return False
        if answer == "a" and can_remember:
            payload = action_payload(pending_actions[0])
            command = getattr(payload, "command")
            assert isinstance(command, str)
            allowlist.add(command)
            print("Added this exact command to the local allowlist.")
            return True
        print("Please enter y or n.")


def run_until_finished(
    conversation: BaseConversation,
    writable_root: Path,
    allowlist: CommandAllowlist,
    confirmer: Callable[[list[object]], bool] = confirm_in_console,
) -> None:
    """Run, pause for approval, then resume until the task has finished."""
    while conversation.state.execution_status != ConversationExecutionStatus.FINISHED:
        if (
            conversation.state.execution_status
            == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
        ):
            pending = ConversationState.get_unmatched_actions(conversation.state.events)
            if not pending:
                raise RuntimeError(
                    "The agent requested confirmation but no pending action was found."
                )
            blocked = [
                action
                for action in pending
                if not is_direct_file_write_permitted(action, writable_root)
            ]
            if blocked:
                print("\nsorry, I dont have permission to execute this action")
                conversation.reject_pending_actions(
                    "Write policy: file path is outside the dedicated agent workspace"
                )
                continue
            if all(
                is_workspace_file_write(action, writable_root)
                or is_auto_approved_action(action)
                or is_persistently_allowed(action, allowlist)
                for action in pending
            ):
                run_with_activity(conversation)
                continue
            if confirmer is confirm_in_console:
                approved = confirm_in_console(pending, allowlist)
            else:
                approved = confirmer(pending)
            if not approved:
                conversation.reject_pending_actions("User rejected the proposed action")
                continue

        run_with_activity(conversation)
