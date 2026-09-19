"""Readable exports of persisted Bank Agent conversation events."""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from openhands.sdk.event import (
    ActionEvent,
    MessageEvent,
    ObservationEvent,
    SystemPromptEvent,
)
from openhands.sdk.llm import content_to_str

from bank_agent.sessions import SessionRecord


LOG_COMMANDS = frozenset({"/l", "/log", "/logs"})
_SAFE_FILE_COMPONENT = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class SessionLogOptions:
    """Options accepted by the interactive session-log command."""

    limit: int | None = None
    messages_only: bool = False
    write_file: bool = False


def parse_session_log_command(command: str) -> SessionLogOptions | None:
    """Parse a log command, returning ``None`` for unrelated chat input."""
    try:
        parts = shlex.split(command, posix=False)
    except ValueError as error:
        first_word = command.lstrip().split(maxsplit=1)[0].casefold()
        if first_word in LOG_COMMANDS:
            raise ValueError(f"Invalid log command: {error}") from error
        return None
    if not parts or parts[0].casefold() not in LOG_COMMANDS:
        return None

    limit: int | None = None
    messages_only = False
    write_file = False
    index = 1
    while index < len(parts):
        option = parts[index].casefold()
        if option in {"-m", "--messages-only"}:
            messages_only = True
        elif option in {"-f", "--file"}:
            write_file = True
        elif option in {"-l", "--limit"}:
            index += 1
            if index >= len(parts):
                raise ValueError(f"{parts[index - 1]} requires a positive number")
            try:
                limit = int(parts[index])
            except ValueError as error:
                raise ValueError("Log limit must be a positive number") from error
            if limit < 1:
                raise ValueError("Log limit must be a positive number")
        else:
            raise ValueError(f"Unknown log option: {parts[index]}")
        index += 1

    return SessionLogOptions(
        limit=limit,
        messages_only=messages_only,
        write_file=write_file,
    )


def _indented(value: object) -> str:
    text = str(value).rstrip() or "[empty]"
    return "\n".join(f"    {line}" if line else "" for line in text.splitlines())


def _event_metadata(event: object) -> list[str]:
    lines = [
        f"- Type: `{event.__class__.__name__}`",
        f"- Source: `{getattr(event, 'source', 'unknown')}`",
        f"- Event ID: `{getattr(event, 'id', 'unknown')}`",
    ]
    parent_id = getattr(event, "parent_id", None)
    if parent_id:
        lines.append(f"- Parent ID: `{parent_id}`")
    llm_response_id = getattr(event, "llm_response_id", None)
    if llm_response_id:
        lines.append(f"- LLM response ID: `{llm_response_id}`")
    return lines


def _response_reasoning_text(item: object) -> list[str]:
    if item is None:
        return []
    value: Any = item.model_dump(exclude_none=True) if hasattr(item, "model_dump") else item
    texts: list[str] = []

    def visit(candidate: object) -> None:
        if isinstance(candidate, dict):
            for key, nested in candidate.items():
                if key in {"text", "summary_text"} and isinstance(nested, str):
                    if nested.strip():
                        texts.append(nested.strip())
                else:
                    visit(nested)
        elif isinstance(candidate, (list, tuple)):
            for nested in candidate:
                visit(nested)

    visit(value)
    return texts


def _reasoning_parts(event: object) -> list[str]:
    parts: list[str] = []

    def add(value: object) -> None:
        if isinstance(value, str) and value.strip() and value.strip() not in parts:
            parts.append(value.strip())

    add(getattr(event, "reasoning_content", None))
    llm_message = getattr(event, "llm_message", None)
    if llm_message is not None:
        add(getattr(llm_message, "reasoning_content", None))

    for thought in getattr(event, "thought", ()) or ():
        add(getattr(thought, "text", None))
    for owner in (event, llm_message):
        if owner is None:
            continue
        for block in getattr(owner, "thinking_blocks", ()) or ():
            add(getattr(block, "thinking", None))
        for text in _response_reasoning_text(
            getattr(owner, "responses_reasoning_item", None)
        ):
            add(text)
    return parts


def _message_text(event: MessageEvent) -> str:
    return "".join(content_to_str(event.llm_message.content)).strip()


def _finish_message(event: ActionEvent) -> str:
    if event.tool_name != "finish" or event.action is None:
        return ""
    return str(getattr(event.action, "message", "")).strip()


def _is_visible_message(event: object) -> bool:
    if isinstance(event, MessageEvent):
        return event.source in {"user", "agent"} and bool(_message_text(event))
    return isinstance(event, ActionEvent) and bool(_finish_message(event))


def _reasoning_section(event: object) -> list[str]:
    reasoning = _reasoning_parts(event)
    if not reasoning:
        return []
    return ["", "**Reasoning summary**", "", _indented("\n\n".join(reasoning))]


def _format_message(event: MessageEvent) -> tuple[str, str]:
    speaker = {"user": "User", "agent": "Agent"}.get(
        event.source, str(event.source).title()
    )
    body = [*_event_metadata(event), "", _indented(_message_text(event))]
    body.extend(_reasoning_section(event))
    return f"{event.timestamp} — {speaker} message", "\n".join(body)


def _format_finish(event: ActionEvent) -> tuple[str, str]:
    body = [*_event_metadata(event), "", _indented(_finish_message(event))]
    body.extend(_reasoning_section(event))
    return f"{event.timestamp} — Agent message", "\n".join(body)


def _format_action(event: ActionEvent) -> tuple[str, str]:
    body = _event_metadata(event)
    if event.summary:
        body.append(f"- Summary: {event.summary}")
    security_risk = str(event.security_risk)
    if security_risk and not security_risk.endswith("UNKNOWN"):
        body.append(f"- Security risk: `{security_risk}`")
    body.extend(_reasoning_section(event))

    if event.action is not None:
        arguments = event.action.model_dump(exclude_none=True)
    else:
        raw_arguments = event.tool_call.arguments
        try:
            arguments = json.loads(raw_arguments)
        except (TypeError, json.JSONDecodeError):
            arguments = raw_arguments
    rendered_arguments = (
        json.dumps(arguments, indent=2, ensure_ascii=False, default=str)
        if not isinstance(arguments, str)
        else arguments
    )
    body.extend(("", "**Arguments**", "", _indented(rendered_arguments)))
    return f"{event.timestamp} — Tool call: {event.tool_name}", "\n".join(body)


def _format_observation(event: ObservationEvent) -> tuple[str, str]:
    body = _event_metadata(event)
    body.append(f"- Action ID: `{event.action_id}`")
    is_error = getattr(event.observation, "is_error", None)
    if isinstance(is_error, bool):
        body.append(f"- Status: `{'error' if is_error else 'success'}`")
    try:
        result = event.observation.visualize.plain.strip()
    except Exception:
        result = "".join(content_to_str(event.observation.to_llm_content)).strip()
    if not result:
        result = json.dumps(
            event.observation.model_dump(exclude_none=True),
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    body.extend(("", "**Result**", "", _indented(result)))
    if event.extended_content:
        extension = "".join(content_to_str(event.extended_content)).strip()
        if extension:
            body.extend(("", "**Context added after the result**", "", _indented(extension)))
    return f"{event.timestamp} — Tool result: {event.tool_name}", "\n".join(body)


def _format_system_prompt(event: SystemPromptEvent) -> tuple[str, str]:
    body = [*_event_metadata(event), "", _indented(event.system_prompt.text)]
    dynamic_context = getattr(event, "dynamic_context", None)
    dynamic_text = getattr(dynamic_context, "text", "") if dynamic_context else ""
    if dynamic_text:
        body.extend(("", "**Dynamic context**", "", _indented(dynamic_text)))
    return f"{event.timestamp} — System prompt", "\n".join(body)


def _format_generic(event: object) -> tuple[str, str]:
    title = event.__class__.__name__.replace("Event", "").replace("_", " ")
    try:
        details = event.visualize.plain.strip()
    except Exception:
        if hasattr(event, "model_dump"):
            details = json.dumps(
                event.model_dump(exclude_none=True),
                indent=2,
                ensure_ascii=False,
                default=str,
            )
        else:
            details = str(event)
    body = [*_event_metadata(event), "", _indented(details)]
    timestamp = getattr(event, "timestamp", "unknown time")
    return f"{timestamp} — {title}", "\n".join(body)


def _format_event(event: object) -> tuple[str, str]:
    if isinstance(event, MessageEvent):
        return _format_message(event)
    if isinstance(event, ActionEvent):
        return _format_finish(event) if _finish_message(event) else _format_action(event)
    if isinstance(event, ObservationEvent):
        return _format_observation(event)
    if isinstance(event, SystemPromptEvent):
        return _format_system_prompt(event)
    return _format_generic(event)


def format_session_log(
    session: SessionRecord,
    events: Sequence[object],
    options: SessionLogOptions,
    *,
    generated_at: datetime | None = None,
) -> str:
    """Render session metadata and selected events as readable Markdown."""
    generated = generated_at or datetime.now().astimezone()
    selected = list(events)
    if options.messages_only:
        selected = [event for event in selected if _is_visible_message(event)]
    if options.limit is not None:
        selected = selected[-options.limit :]

    lines = [
        "# Bank Agent Session Log",
        "",
        f"- Session: `{session.title}`",
        f"- Session ID: `{session.id}`",
        f"- Workspace: `{session.workspace}`",
        f"- Provider: `{session.provider}`",
        f"- Model: `{session.model}`",
        f"- Created: `{session.created_at}`",
        f"- Last updated: `{session.updated_at}`",
        f"- Runs: `{session.run_count}`",
        f"- Generated: `{generated.isoformat(timespec='milliseconds')}`",
        f"- View: `{'messages only' if options.messages_only else 'all events'}`",
        f"- Entries shown: `{len(selected)}`",
        "",
        "## Entries",
    ]
    if not selected:
        lines.extend(("", "No matching entries."))
    for event in selected:
        title, body = _format_event(event)
        lines.extend(("", f"### {title}", "", body))
    return "\n".join(lines).rstrip() + "\n"


def write_session_log(
    output_directory: Path,
    session: SessionRecord,
    content: str,
    *,
    generated_at: datetime | None = None,
) -> Path:
    """Write a uniquely named Markdown log and return its absolute path."""
    generated = generated_at or datetime.now().astimezone()
    timestamp = generated.isoformat(timespec="milliseconds").replace(":", "-")
    safe_title = _SAFE_FILE_COMPONENT.sub("-", session.title).strip("-.") or "session"
    output_directory.mkdir(parents=True, exist_ok=True)
    candidate = output_directory / f"{safe_title}-{timestamp}.md"
    suffix = 2
    while candidate.exists():
        candidate = output_directory / f"{safe_title}-{timestamp}-{suffix}.md"
        suffix += 1
    candidate.write_text(content, encoding="utf-8")
    return candidate.resolve()
