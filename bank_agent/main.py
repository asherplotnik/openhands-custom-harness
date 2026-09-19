"""Interactive, approval-first local OpenHands coding agent."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
import warnings
from collections.abc import Callable, Sequence
from pathlib import Path

from openhands.sdk import (
    AgentContext,
    BaseConversation,
    Conversation,
    LLM,
    LocalConversation,
)
from openhands.sdk.conversation.exceptions import ConversationRunError
from openhands.sdk.event import (
    ActionEvent,
    AgentErrorEvent,
    MessageEvent,
    SystemPromptEvent,
)
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.llm import TextContent, content_to_str
from openhands.sdk.llm.llm import LLMCallContext
from openhands.sdk.security.confirmation_policy import AlwaysConfirm
from openhands.sdk.tool import Tool
from openhands.tools.preset.gemini import get_gemini_agent

from bank_agent.allowlist import CommandAllowlist
from bank_agent.approvals import run_until_finished
from bank_agent.config import Settings, load_env_file, load_settings
from bank_agent.gemini_connector import register as register_gemini_connector
from bank_agent.instructions import (
    offer_workspace_instructions,
    workspace_instruction_context,
)
from bank_agent.paths import (
    APP_ROOT,
    BUILTIN_SKILLS_PATH,
    COMMAND_ALLOWLIST_PATH,
    CONFIG_PATH,
    GENERAL_INSTRUCTIONS_PATH,
    HELP_PATH,
    PROJECTS_WORKSPACE,
    SCRATCH_WORKSPACE,
    SESSION_LOGS_PATH,
    SESSIONS_PATH,
    SKILL_DRAFTS_PATH,
    SKILLS_PATH,
)
from bank_agent.sessions import (
    SessionInUseError,
    SessionLock,
    SessionRecord,
    SessionStore,
    choose_session,
    show_help,
)
from bank_agent.search_tool import SearchTool
from bank_agent.session_logs import (
    format_session_log,
    parse_session_log_command,
    write_session_log,
)
from bank_agent.skills import (
    GUIDED_SKILL_CREATION_ENABLED,
    HarnessSkillCatalog,
    install_skill_draft,
    load_harness_skills,
    prepare_skill_install,
)
from bank_agent.ui import (
    MUTED,
    SUCCESS,
    agent_header,
    echo_user_message,
    field,
    hide_activity_indicator,
    notice,
    screen_title,
    shortcut_bar,
    style,
    user_header,
    user_prompt,
)


# The bank connector's custom model is intentionally absent from LiteLLM's
# public pricing table. Cost estimation is unavailable, but inference works.
warnings.filterwarnings(
    "ignore",
    message=r"Cost calculation failed: This model isn't mapped yet.*",
    category=UserWarning,
    module=r"openhands\.sdk\.llm\.utils\.telemetry",
)


KEEP_TERMINAL_OPEN_EXIT_CODE = 3
RETURN_TO_SESSION_PICKER = object()
RESUMED_MESSAGE_LIMIT = 10


class GroqConversation(LocalConversation):
    """Conversation adapter for Groq's Chat Completions API.

    OpenHands associates each conversation with a prompt-cache key. Groq rejects
    that OpenAI-specific request field, so preserve the session correlation ID
    while omitting only the unsupported cache key.
    """

    def get_llm_call_context(self) -> LLMCallContext:
        return LLMCallContext(session_id=str(self._state.id))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    values = list(sys.argv[1:] if argv is None else argv)
    if values and values[0] in {"sessions", "archive", "unarchive", "resume"}:
        return _parse_session_args(values)

    parser = argparse.ArgumentParser(
        description="Run a local OpenHands coding agent through Gemini or Groq."
    )
    parser.add_argument(
        "task",
        nargs="*",
        help="Optional initial task. Omit it to start an interactive prompt.",
    )
    parser.add_argument(
        "--workspace",
        help=(
            "A project directory under agent-workspace/projects. "
            "Defaults to disposable agent-workspace/scratch."
        ),
    )
    parser.add_argument(
        "--base-url",
        help="Override BANK_AGENT_BASE_URL for this invocation.",
    )
    parser.add_argument(
        "--model",
        help="Override BANK_AGENT_MODEL for this invocation.",
    )
    parser.add_argument(
        "--provider",
        choices=("gemini", "groq"),
        help="Override BANK_AGENT_PROVIDER for this invocation.",
    )
    parser.set_defaults(command="run")
    return parser.parse_args(values)


def _parse_session_args(argv: Sequence[str]) -> argparse.Namespace:
    """Parse the small set of session-management commands."""
    parser = argparse.ArgumentParser(description="Manage local bank-agent sessions.")
    commands = parser.add_subparsers(dest="command", required=True)

    sessions = commands.add_parser("sessions", help="List saved sessions.")
    filter_group = sessions.add_mutually_exclusive_group()
    filter_group.add_argument(
        "--archived", action="store_true", help="Show archived sessions only."
    )
    filter_group.add_argument(
        "--all", action="store_true", help="Show active and archived sessions."
    )

    for command in ("archive", "unarchive", "resume"):
        command_parser = commands.add_parser(command)
        command_parser.add_argument(
            "session_id",
            help="Unique session name, full session ID, or unique ID prefix.",
        )

    return parser.parse_args(argv)


def configure_console_encoding() -> None:
    """Keep model output from failing on legacy Windows console code pages."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _settings_for_args(args: argparse.Namespace) -> Settings:
    values = dict(os.environ)
    if getattr(args, "provider", None):
        values["BANK_AGENT_PROVIDER"] = args.provider
    if getattr(args, "model", None):
        values["BANK_AGENT_MODEL"] = args.model
    if getattr(args, "base_url", None):
        values["BANK_AGENT_BASE_URL"] = args.base_url
    return load_settings(values)


def print_sessions(records: Sequence[SessionRecord], heading: str) -> None:
    """Render a compact, stable listing suitable for an ordinary terminal."""
    print(screen_title(heading.rstrip(":")))
    print()
    if not records:
        print(f"  {style('No sessions found.', MUTED)}")
        return
    for record in records:
        state = "archived" if record.archived else "active"
        state_code = MUTED if record.archived else SUCCESS
        print(
            f"  {style(record.id[:8], MUTED)}  {style(f'{state:8}', state_code)}  "
            f"{style(record.updated_at[:16], MUTED)}  "
            f"{record.title}"
        )


def create_new_session(
    store: SessionStore,
    workspace: Path,
    settings: Settings,
    title: str = "New session",
) -> SessionRecord:
    """Create index metadata before the SDK writes the conversation event log."""
    return store.create(
        workspace=workspace,
        provider=settings.provider,
        model=settings.model,
        title=title,
    )


def create_skill_authoring_session(
    store: SessionStore, settings: Settings
) -> tuple[SessionRecord, Path]:
    """Create an isolated, resumable draft workspace and its dedicated session."""
    SKILL_DRAFTS_PATH.mkdir(parents=True, exist_ok=True)
    for _ in range(10):
        suffix = uuid.uuid4().hex[:8]
        title = f"create-skill-{suffix}"
        workspace = SKILL_DRAFTS_PATH / title
        if store.has_title(title) or workspace.exists():
            continue
        workspace.mkdir()
        try:
            session = create_new_session(store, workspace, settings, title=title)
            return session, workspace.resolve()
        except Exception:
            workspace.rmdir()
            raise
    raise ValueError("Could not allocate a unique skill draft workspace.")


def session_runtime_context(
    session: SessionRecord, *, resumed: bool, previous_activity: str | None
) -> str:
    """Build small, explicit lifecycle context without creating a user message."""
    now = datetime.now().astimezone()
    mode = "resumed" if resumed else "new"
    prior_activity = previous_activity if resumed else "none (new session)"
    prior_end = session.last_ended_at or "no recorded normal shutdown"
    return "\n".join(
        (
            "Runtime session context (not a user instruction):",
            f"- Current local time: {now.isoformat(timespec='seconds')}",
            f"- Current UTC time: {now.astimezone(UTC).isoformat(timespec='seconds')}",
            f"- Session state: {mode}; CLI run #{session.run_count}.",
            f"- Workspace: {session.workspace}",
            f"- Previous recorded activity (UTC): {prior_activity}",
            f"- Previous normal session end (UTC): {prior_end}",
            "Use this only when it is relevant to the user's task.",
        )
    )


def compose_pasted_task() -> str | None:
    """Let the user edit clipboard text in Notepad and return one task message."""
    try:
        clipboard = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-Command",
                "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; Get-Clipboard -Raw",
            ],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as error:
        print(f"Could not read the clipboard: {error}")
        return None

    if clipboard.returncode != 0:
        print("Could not read text from the clipboard.")
        return None

    draft_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".txt",
            prefix="bank-agent-paste-",
            delete=False,
        ) as draft:
            draft.write(clipboard.stdout)
            draft_path = Path(draft.name)

        print("Opening Notepad with clipboard text. Save with Ctrl+S, then close Notepad to send it.")
        subprocess.run(["notepad.exe", str(draft_path)], check=False)
        message = draft_path.read_text(encoding="utf-8")
    except OSError as error:
        print(f"Could not open the paste editor: {error}")
        return None
    finally:
        if draft_path is not None:
            try:
                draft_path.unlink(missing_ok=True)
            except OSError:
                print(f"Could not remove temporary paste draft: {draft_path}")

    if not message.strip():
        print("Paste cancelled: the draft is empty.")
        return None
    return message


def clear_scratch_workspace() -> None:
    """Empty only the dedicated scratch directory before a new scratch session."""
    scratch = SCRATCH_WORKSPACE.resolve()
    if not scratch.is_dir():
        raise ValueError(f"Scratch workspace does not exist or is not a directory: {scratch}")
    for child in scratch.iterdir():
        if child.is_symlink() or child.is_file():
            child.unlink()
        else:
            shutil.rmtree(child)


def resolve_workspace(value: str) -> Path:
    """Allow project, Scratch, and harness-owned skill draft workspaces."""
    workspace = Path(value).expanduser().resolve()
    scratch_workspace = SCRATCH_WORKSPACE.resolve()
    projects_workspace = PROJECTS_WORKSPACE.resolve()
    skill_drafts_workspace = SKILL_DRAFTS_PATH.resolve()
    if workspace == scratch_workspace:
        if not workspace.is_dir():
            raise ValueError(
                f"Scratch workspace does not exist or is not a directory: {workspace}"
            )
        return workspace
    try:
        relative_path = workspace.relative_to(projects_workspace)
    except ValueError:
        try:
            relative_path = workspace.relative_to(skill_drafts_workspace)
        except ValueError as error:
            raise ValueError(
                "Workspace must be Scratch, a project under "
                f"{projects_workspace}, or a Bank Agent skill draft."
            ) from error
        if relative_path == Path(".") or len(relative_path.parts) != 1:
            raise ValueError("Choose one named Bank Agent skill draft workspace.")
    else:
        if relative_path == Path("."):
            raise ValueError("Choose a named project under agent-workspace/projects.")
    if not workspace.is_dir():
        raise ValueError(f"Workspace does not exist or is not a directory: {workspace}")
    return workspace


def is_skill_authoring_workspace(workspace: Path) -> bool:
    """Identify the hidden draft roots used by guided skill-creation sessions."""
    workspace = workspace.resolve()
    return workspace.parent == SKILL_DRAFTS_PATH.resolve()


def render_event(event: object) -> None:
    """Show useful progress without printing internal prompts or tool output."""
    if isinstance(event, MessageEvent) and event.source == "agent":
        message = "".join(content_to_str(event.llm_message.content)).strip()
        if message:
            hide_activity_indicator()
            print(f"\n{agent_header()}\n{message}")
    elif isinstance(event, ActionEvent) and event.tool_name == "finish":
        message = str(getattr(event.action, "message", "")).strip()
        if message:
            hide_activity_indicator()
            print(f"\n{agent_header()}\n{message}")
    elif isinstance(event, AgentErrorEvent):
        hide_activity_indicator()
        print(f"\nAgent tool error: {event.error}", file=sys.stderr)
    elif isinstance(event, ConversationErrorEvent):
        hide_activity_indicator()
        print(f"\nConversation error: {event.detail}", file=sys.stderr)


def recent_visible_messages(
    events: Sequence[object], limit: int = RESUMED_MESSAGE_LIMIT
) -> tuple[list[tuple[str, str]], bool]:
    """Return the latest persisted user/agent messages for terminal replay."""
    visible: list[tuple[str, str]] = []
    for event in events:
        if not isinstance(event, MessageEvent) or event.source not in {
            "user",
            "agent",
        }:
            continue
        message = "".join(content_to_str(event.llm_message.content)).strip()
        if message:
            visible.append((event.source, message))
    return visible[-limit:], len(visible) > limit


def show_resumed_history(
    conversation: BaseConversation, limit: int = RESUMED_MESSAGE_LIMIT
) -> None:
    """Replay recent chat messages so a resumed session reads naturally."""
    messages, earlier_messages_omitted = recent_visible_messages(
        conversation.state.view.events, limit
    )
    if not messages:
        return

    divider = "SESSION RESUMED"
    if earlier_messages_omitted:
        divider += " | earlier messages omitted"
    print(f"\n{style(divider, MUTED)}")
    for source, message in messages:
        header = user_header() if source == "user" else agent_header()
        print(f"\n{header}\n{message}")
    print()


def create_conversation(
    workspace: Path,
    settings: Settings,
    session_id: str,
    runtime_context: str,
    skill_catalog: HarnessSkillCatalog | None = None,
) -> BaseConversation:
    """Create an approval-first agent whose state is persisted per session."""
    if settings.provider == "gemini":
        register_gemini_connector()
    llm = LLM(
        usage_id="bank-agent",
        model=settings.model,
        base_url=settings.base_url,
        api_key=settings.api_key,
        timeout=settings.llm_timeout_seconds,
        num_retries=settings.llm_num_retries,
    )
    base_agent = get_gemini_agent(llm=llm, cli_mode=True)
    tools = list(base_agent.tools)
    if not any(tool.name == SearchTool.name for tool in tools):
        tools.append(
            Tool(name=SearchTool.name, params={"readable_root": str(APP_ROOT)})
        )
    default_tools = list(base_agent.include_default_tools)
    if "InvokeSkillTool" not in default_tools:
        default_tools.append("InvokeSkillTool")
    agent = base_agent.model_copy(
        update={
            "tools": tools,
            "include_default_tools": default_tools,
            "agent_context": AgentContext(
                skills=list(skill_catalog.skills) if skill_catalog else [],
                current_datetime=datetime.now().astimezone(),
                system_message_suffix=runtime_context,
            )
        }
    )
    conversation_args = {
        "agent": agent,
        "callbacks": [render_event],
        "workspace": str(workspace),
        "conversation_id": uuid.UUID(hex=session_id),
        "persistence_dir": str(SESSIONS_PATH),
        # A closed interactive client must release executors without deleting the
        # conversation that the user expects to resume later.
        "delete_on_close": False,
        # The built-in visualizer echoes the full system prompt and every event.
        # This client renders concise progress through render_event instead.
        "visualizer": None,
    }
    conversation = (
        GroqConversation(**conversation_args)
        if settings.provider == "groq"
        else Conversation(**conversation_args)
    )
    conversation.set_confirmation_policy(AlwaysConfirm())
    return conversation


def refresh_resumed_system_context(
    conversation: BaseConversation, system_context: str
) -> bool:
    """Refresh the preserved system event without altering the session transcript.

    Persisted conversations already contain a SystemPromptEvent, so the SDK does
    not generate another one on resume. Replacing its in-memory dynamic context
    makes current AGENTS.md content effective for this run and leaves it in the
    first event that the condenser preserves.
    """
    view = conversation.state.view
    for index, event in enumerate(view.events):
        if isinstance(event, SystemPromptEvent):
            view.events[index] = event.model_copy(
                update={"dynamic_context": TextContent(text=system_context)}
            )
            return True
    return False


def close_conversation_safely(conversation: BaseConversation) -> None:
    """Close tools without letting an impatient second Ctrl+C leak a traceback."""
    try:
        conversation.close()
    except KeyboardInterrupt:
        print("\nShutdown interrupted; releasing the session lock.")
    except Exception as error:
        print(f"\nWarning: conversation cleanup failed: {error}", file=sys.stderr)


def run_task(
    conversation: BaseConversation,
    writable_root: Path,
    allowlist: CommandAllowlist,
    task: str,
    session_store: SessionStore,
    session_id: str,
) -> None:
    session_store.touch(session_id, task=task)
    conversation.send_message(task)
    try:
        run_until_finished(conversation, writable_root, allowlist)
    except KeyboardInterrupt:
        # Keep Ctrl+C scoped to this task.  The SDK turns the active run into a
        # resumable PAUSED conversation, so the user can immediately send the
        # next message without losing the session or closing the harness.
        hide_activity_indicator()
        try:
            conversation.interrupt()
        except KeyboardInterrupt:
            pass
        except Exception as error:
            print(f"\nWarning: could not signal the active run to stop: {error}", file=sys.stderr)
        print("\nRun interrupted. Previous messages and completed work were kept.")
    finally:
        session_store.touch(session_id)


def confirm_and_install_skill(draft_workspace: Path, skills_path: Path) -> bool:
    """Validate a draft, show its destination, and install only after consent."""
    try:
        plan = prepare_skill_install(draft_workspace, skills_path)
    except (OSError, ValueError) as error:
        print(f"\nSkill not installed: {error}")
        return False

    print("\nSkill ready to install:")
    print(f"  Name: {plan.name}")
    print(f"  Description: {plan.description}")
    print(f"  Destination: {plan.destination}")
    try:
        answer = input("Install this skill? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nSkill not installed.")
        return False
    if answer not in {"y", "yes"}:
        print("Skill not installed; the draft remains available in this session.")
        return False

    try:
        destination = install_skill_draft(plan)
    except (OSError, ValueError) as error:
        print(f"Skill not installed: {error}")
        return False
    print(f"Installed skill: {destination}")
    return True


def interactive_loop(
    conversation: BaseConversation,
    writable_root: Path,
    allowlist: CommandAllowlist,
    session_store: SessionStore,
    session_id: str,
    skill_installer: Callable[[], bool] | None = None,
) -> str:
    """Run one chat and report whether to go home, quit, or keep the terminal."""
    if skill_installer is None:
        print(
            shortcut_bar(
                (
                    ("/p", "paste"),
                    ("/l", "logs"),
                    ("/h", "help"),
                    ("/e", "sessions"),
                    ("/q", "quit"),
                )
            )
        )
    else:
        print(
            notice(
                "Skill creation session. Describe the skill you want to create, then "
                "type /install when the draft is ready."
            )
        )
    while True:
        try:
            task = input(user_prompt()).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            return "quit"

        if task in {"/e", "/exit"}:
            return "home"
        if task in {"/q", "/quit"}:
            return "terminal"
        if task in {"/h", "/help"}:
            show_help(HELP_PATH)
            continue
        try:
            log_options = parse_session_log_command(task)
        except ValueError as error:
            print(f"\n{error}")
            print("Usage: /logs [-l NUMBER] [-m] [-f]")
            continue
        if log_options is not None:
            session = session_store.get(session_id)
            generated_at = datetime.now().astimezone()
            log = format_session_log(
                session,
                conversation.state.view.events,
                log_options,
                generated_at=generated_at,
            )
            if log_options.write_file:
                try:
                    path = write_session_log(
                        SESSION_LOGS_PATH,
                        session,
                        log,
                        generated_at=generated_at,
                    )
                except OSError as error:
                    print(f"\nCould not write session log: {error}")
                else:
                    print(f"\nSession log written to:\n{path}")
            else:
                print(f"\n{log}")
            continue
        if task in {"/p", "/paste"}:
            pasted_task = compose_pasted_task()
            if pasted_task is None:
                continue
            echo_user_message(pasted_task)
            run_task(
                conversation,
                writable_root,
                allowlist,
                pasted_task,
                session_store,
                session_id,
            )
            continue
        if task == "/install" and skill_installer is not None:
            if skill_installer():
                return "skill-installed"
            continue
        if not task:
            continue
        run_task(
            conversation,
            writable_root,
            allowlist,
            task,
            session_store,
            session_id,
        )


def sessions_matching_launch_directory(session_store: SessionStore) -> list[SessionRecord]:
    """Return sessions whose workspace contains the terminal that launched us."""
    raw_directory = os.environ.get("BANK_AGENT_LAUNCH_DIRECTORY")
    if not raw_directory:
        return []
    return session_store.matching_workspace_path(Path(raw_directory))


def _run_once(args: argparse.Namespace) -> int | object:
    configure_console_encoding()
    session_store = SessionStore(SESSIONS_PATH)

    try:
        if args.command == "sessions":
            archived = True if args.archived else None if args.all else False
            heading = "Archived sessions:" if args.archived else "Sessions:"
            print_sessions(session_store.list(archived=archived), heading)
            return 0
        if args.command == "archive":
            record = session_store.archive(args.session_id)
            print(f"Archived session {record.id[:8]}: {record.title}")
            return 0
        if args.command == "unarchive":
            record = session_store.unarchive(args.session_id)
            print(f"Unarchived session {record.id[:8]}: {record.title}")
            return 0
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2

    try:
        load_env_file(CONFIG_PATH)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2

    try:
        settings = _settings_for_args(args)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2

    try:
        allowlist = CommandAllowlist.load(COMMAND_ALLOWLIST_PATH)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2

    resumed = args.command == "resume"
    session_lock: SessionLock | None = None
    try:
        if args.command == "resume":
            session = session_store.get(args.session_id)
            workspace = resolve_workspace(session.workspace)
            if (
                is_skill_authoring_workspace(workspace)
                and not GUIDED_SKILL_CREATION_ENABLED
            ):
                raise ValueError(
                    "Guided skill creation is temporarily unavailable; this draft "
                    "session is preserved for later."
                )
            session_lock = SessionLock.acquire(SESSIONS_PATH, session.id)
            if session.archived:
                session = session_store.unarchive(session.id)
                print(f"Unarchived session {session.id[:8]} before resuming.")
        else:
            workspace = resolve_workspace(args.workspace or str(SCRATCH_WORKSPACE))
            if (
                is_skill_authoring_workspace(workspace)
                and not GUIDED_SKILL_CREATION_ENABLED
            ):
                raise ValueError(
                    "Guided skill creation is temporarily unavailable; draft "
                    "workspaces are preserved but cannot be opened."
                )
            initial_task = " ".join(args.task).strip()
            if initial_task:
                session = create_new_session(session_store, workspace, settings)
            else:
                picker_notice: str | None = None
                auto_matches = [] if args.workspace else sessions_matching_launch_directory(session_store)
                if len(auto_matches) == 1:
                    candidate = auto_matches[0]
                    candidate_workspace = resolve_workspace(candidate.workspace)
                    if (
                        is_skill_authoring_workspace(candidate_workspace)
                        and not GUIDED_SKILL_CREATION_ENABLED
                    ):
                        picker_notice = (
                            "Guided skill creation is temporarily unavailable; "
                            "the draft session remains preserved."
                        )
                    else:
                        try:
                            session_lock = SessionLock.acquire(
                                SESSIONS_PATH, candidate.id
                            )
                        except SessionInUseError as error:
                            picker_notice = str(error)
                        else:
                            session = candidate
                            workspace = candidate_workspace
                            resumed = True
                            print(
                                "Resuming session from launch directory: "
                                f"{session.title}"
                            )
                            if session.archived:
                                session = session_store.unarchive(session.id)
                                print(
                                    f"Unarchived session {session.id[:8]} "
                                    "before resuming."
                                )
                elif len(auto_matches) > 1:
                    picker_notice = (
                        "Multiple sessions use the current directory's workspace; choose one."
                    )

                if session_lock is None:
                    while True:
                        choice = choose_session(
                            session_store,
                            PROJECTS_WORKSPACE,
                            SCRATCH_WORKSPACE,
                            GENERAL_INSTRUCTIONS_PATH,
                            HELP_PATH,
                            skills_path=SKILLS_PATH,
                            notice=picker_notice,
                        )
                        if choice.action == "quit":
                            return 0
                        if choice.action == "resume":
                            assert choice.session is not None
                            candidate = choice.session
                            candidate_workspace = resolve_workspace(candidate.workspace)
                            if (
                                is_skill_authoring_workspace(candidate_workspace)
                                and not GUIDED_SKILL_CREATION_ENABLED
                            ):
                                picker_notice = (
                                    "Guided skill creation is temporarily unavailable; "
                                    "the draft session remains preserved."
                                )
                                continue
                            try:
                                candidate_lock = SessionLock.acquire(SESSIONS_PATH, candidate.id)
                            except SessionInUseError as error:
                                picker_notice = str(error)
                                continue
                            session = candidate
                            workspace = candidate_workspace
                            session_lock = candidate_lock
                            resumed = True
                            if session.archived:
                                session = session_store.unarchive(session.id)
                                print(f"Unarchived session {session.id[:8]} before resuming.")
                            break

                        if choice.action == "create_skill":
                            if not GUIDED_SKILL_CREATION_ENABLED:
                                picker_notice = (
                                    "Guided skill creation is temporarily unavailable."
                                )
                                continue
                            session, workspace = create_skill_authoring_session(
                                session_store, settings
                            )
                            session_lock = SessionLock.acquire(
                                SESSIONS_PATH, session.id
                            )
                            break

                        workspace = choice.workspace or workspace
                        offer_workspace_instructions(workspace)
                        session = create_new_session(
                            session_store,
                            workspace,
                            settings,
                            title=choice.title,
                        )
                        session_lock = SessionLock.acquire(SESSIONS_PATH, session.id)
                        break
        if session_lock is None:
            session_lock = SessionLock.acquire(SESSIONS_PATH, session.id)
        # Scratch is one disposable shared folder, not a durable project. A
        # resumed Scratch session intentionally gets a fresh filesystem too.
        if workspace.resolve() == SCRATCH_WORKSPACE.resolve():
            clear_scratch_workspace()
            print("Scratch workspace cleared.")
        previous_activity = session.updated_at if resumed else None
        skill_authoring = is_skill_authoring_workspace(workspace)
        instruction_context, instruction_sources = workspace_instruction_context(workspace)
        session = session_store.start_run(session.id)
        runtime_context = session_runtime_context(
            session,
            resumed=resumed,
            previous_activity=previous_activity,
        )
    except (OSError, ValueError) as error:
        if session_lock is not None:
            session_lock.release()
        print(str(error), file=sys.stderr)
        return 2

    print(screen_title("Chat"))
    print()
    print(field("Workspace", workspace))
    print(field("Session", f"{session.title} ({session.id[:8]})"))
    print(field("Provider", settings.provider))
    print(field("Model", settings.model))
    if instruction_sources:
        print(
            field(
                "Instructions", ", ".join(str(path) for path in instruction_sources)
            )
        )
    else:
        print(field("Instructions", "no Bank Agent AGENTS.md files found."))

    skill_catalog = load_harness_skills(
        BUILTIN_SKILLS_PATH if skill_authoring else SKILLS_PATH
    )
    if skill_catalog.skills:
        suffix = " (authoring)" if skill_authoring else ""
        print(
            field(
                "Skills",
                ", ".join(skill.name for skill in skill_catalog.skills) + suffix,
            )
        )
    else:
        print(field("Skills", "none installed."))
    print()
    print(
        notice(
            "Read-only actions and direct workspace file edits run automatically; "
            "terminal changes require approval. Use a to remember an eligible command."
        )
    )
    print()
    for issue in skill_catalog.issues:
        print(f"Skill skipped ({issue.name}): {issue.message}", file=sys.stderr)

    authoring_context = ""
    if skill_authoring:
        authoring_context = (
            "\n\nSkill authoring mode (harness-enforced):\n"
            "- Invoke the skill-creator skill before creating or changing the draft.\n"
            "- Work only in this isolated draft workspace.\n"
            "- Do not install skills yourself; the user installs with /install."
        )
    full_system_context = f"{runtime_context}\n\n{instruction_context}{authoring_context}"
    assert session_lock is not None
    conversation: BaseConversation | None = None
    try:
        conversation = create_conversation(
            workspace,
            settings,
            session.id,
            full_system_context,
            skill_catalog,
        )
        if resumed:
            refreshed_context = (
                conversation.state.agent.dynamic_context or full_system_context
            )
            refresh_resumed_system_context(conversation, refreshed_context)
            show_resumed_history(conversation)
        initial_task = " ".join(getattr(args, "task", [])).strip()
        if initial_task:
            run_task(
                conversation,
                workspace,
                allowlist,
                initial_task,
                session_store,
                session.id,
            )
        else:
            chat_exit = interactive_loop(
                conversation,
                workspace,
                allowlist,
                session_store,
                session.id,
                (
                    (lambda: confirm_and_install_skill(workspace, SKILLS_PATH))
                    if skill_authoring
                    else None
                ),
            )
    except ConversationRunError as error:
        print(
            f"Task stopped: {type(error.original_exception).__name__}: "
            f"{error.original_exception}",
            file=sys.stderr,
        )
        print(
            "Check that the configured provider endpoint and model are available.",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        if conversation is not None:
            conversation.interrupt()
        print("\nInterrupted.")
        return 130
    finally:
        try:
            if conversation is not None:
                close_conversation_safely(conversation)
        finally:
            try:
                session_store.end_run(session.id)
            finally:
                session_lock.release()
    if not initial_task and chat_exit == "home":
        return RETURN_TO_SESSION_PICKER
    if not initial_task and chat_exit == "skill-installed":
        session_store.archive(session.id)
        print(f"Archived completed skill-creation session: {session.title}")
        return RETURN_TO_SESSION_PICKER
    if not initial_task and chat_exit == "terminal":
        return KEEP_TERMINAL_OPEN_EXIT_CODE
    return 0


def _picker_arguments(args: argparse.Namespace) -> list[str]:
    """Keep connection overrides while intentionally dropping task/workspace state."""
    values: list[str] = []
    for attribute, flag in (
        ("provider", "--provider"),
        ("model", "--model"),
        ("base_url", "--base-url"),
    ):
        value = getattr(args, attribute, None)
        if value:
            values.extend((flag, value))
    return values


def main(argv: Sequence[str] | None = None) -> int:
    """Run the client, returning to the picker without replacing the terminal process."""
    args = parse_args(argv)
    while True:
        result = _run_once(args)
        if result is not RETURN_TO_SESSION_PICKER:
            return result
        args = parse_args(_picker_arguments(args))


if __name__ == "__main__":
    raise SystemExit(main())
