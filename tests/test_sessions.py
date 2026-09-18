from __future__ import annotations

from io import StringIO
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bank_agent.allowlist import CommandAllowlist
from bank_agent.approvals import (
    is_auto_approved_action,
    is_direct_file_write_permitted,
    is_workspace_file_write,
    run_until_finished,
)
from bank_agent.instructions import (
    offer_workspace_instructions,
    workspace_instruction_context,
)
from bank_agent.main import (
    GENERAL_INSTRUCTIONS_PATH,
    HELP_PATH,
    KEEP_TERMINAL_OPEN_EXIT_CODE,
    RETURN_TO_SESSION_PICKER,
    close_conversation_safely,
    interactive_loop,
    is_skill_authoring_workspace,
    main,
    recent_visible_messages,
    refresh_resumed_system_context,
    resolve_workspace,
    run_task,
    sessions_matching_launch_directory,
    show_resumed_history,
)
from bank_agent.sessions import (
    SessionInUseError,
    SessionLock,
    SessionStore,
    UNTITLED_SESSION,
    choose_session,
    choose_workspace,
    create_project_workspace,
    delete_project_workspace,
    has_whitespace,
    list_project_workspaces,
    name_suggestions,
    whitespace_name_message,
)
from bank_agent.config import load_settings
from openhands.sdk.context.view import View
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.event import MessageEvent, SystemPromptEvent
from openhands.sdk.llm import Message, TextContent


class SessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = SessionStore(Path(self.temporary_directory.name) / "sessions")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_ctrl_c_interrupts_only_the_current_task(self) -> None:
        class Conversation:
            def __init__(self) -> None:
                self.messages: list[str] = []
                self.interrupted = False

            def send_message(self, task: str) -> None:
                self.messages.append(task)

            def interrupt(self) -> None:
                self.interrupted = True

        class Store:
            def __init__(self) -> None:
                self.touches: list[tuple[str, str | None]] = []

            def touch(self, session_id: str, task: str | None = None) -> None:
                self.touches.append((session_id, task))

        conversation = Conversation()
        store = Store()
        with patch("bank_agent.main.run_until_finished", side_effect=KeyboardInterrupt):
            run_task(
                conversation,
                Path(self.temporary_directory.name),
                SimpleNamespace(),
                "long-running task",
                store,
                "session-id",
            )

        self.assertEqual(conversation.messages, ["long-running task"])
        self.assertTrue(conversation.interrupted)
        self.assertEqual(
            store.touches,
            [("session-id", "long-running task"), ("session-id", None)],
        )

    def test_archived_sessions_are_hidden_by_default_but_remain_retrievable(self) -> None:
        record = self.store.create(
            workspace=Path("C:/work/agent-workspace/project"),
            provider="gemini",
            model="bank_gemini/test",
        )

        self.assertEqual(self.store.list(), [record])
        archived = self.store.archive(record.id)

        self.assertEqual(self.store.list(), [])
        self.assertEqual(self.store.list(archived=True), [archived])
        self.assertEqual(self.store.get(record.id[:8]), archived)

    def test_first_task_becomes_the_title_and_unarchive_restores_default_listing(self) -> None:
        record = self.store.create(
            workspace=Path("C:/work/agent-workspace/project"),
            provider="groq",
            model="groq/test",
        )
        self.assertEqual(record.title, UNTITLED_SESSION)

        named = self.store.touch(record.id, task="Investigate payment timeout\nwith logs")
        self.assertEqual(named.title, "Investigate payment timeout with logs")
        self.store.archive(record.id)

        restored = self.store.unarchive(record.id)
        self.assertFalse(restored.archived)
        self.assertEqual(self.store.list(), [restored])

    def test_session_title_check_includes_archived_sessions_case_insensitively(self) -> None:
        record = self.store.create(
            workspace=Path("C:/work/agent-workspace/project"),
            provider="groq",
            model="groq/test",
            title="Payment investigation",
        )
        self.store.archive(record.id)

        self.assertTrue(self.store.has_title("payment investigation"))
        self.assertFalse(self.store.has_title("different investigation"))

    def test_session_name_is_a_case_insensitive_selector(self) -> None:
        record = self.store.create(
            workspace=Path("C:/work/agent-workspace/project"),
            provider="groq",
            model="groq/test",
            title="Payment investigation",
        )

        self.assertEqual(self.store.get("PAYMENT INVESTIGATION"), record)
        archived = self.store.archive("payment investigation")
        self.assertTrue(archived.archived)
        restored = self.store.unarchive("Payment Investigation")
        self.assertFalse(restored.archived)

    def test_deleting_a_session_removes_history_but_keeps_workspace(self) -> None:
        workspace = Path(self.temporary_directory.name) / "projects" / "payments"
        workspace.mkdir(parents=True)
        workspace_file = workspace / "important.java"
        workspace_file.write_text("keep me", encoding="utf-8")
        record = self.store.create(
            workspace=workspace,
            provider="groq",
            model="groq/test",
            title="Payments fix",
        )
        history_directory = self.store.base_dir / record.id
        history_directory.mkdir()
        (history_directory / "base_state.json").write_text("{}", encoding="utf-8")

        deleted = self.store.delete(record.id)

        self.assertEqual(deleted, record)
        self.assertEqual(self.store.list(archived=None), [])
        self.assertFalse(history_directory.exists())
        self.assertEqual(workspace_file.read_text(encoding="utf-8"), "keep me")

    def test_deleting_a_locked_session_is_refused(self) -> None:
        record = self.store.create(
            workspace=Path(self.temporary_directory.name) / "projects" / "payments",
            provider="groq",
            model="groq/test",
            title="Payments fix",
        )
        active_window = SessionLock.acquire(self.store.base_dir, record.id)
        try:
            with self.assertRaises(SessionInUseError):
                self.store.delete(record.id)
        finally:
            active_window.release()

        self.assertEqual(self.store.get(record.id), record)

    def test_workspace_deletion_is_limited_to_a_named_project_workspace(self) -> None:
        root = Path(self.temporary_directory.name)
        projects = root / "projects"
        workspace = projects / "payments"
        workspace.mkdir(parents=True)

        delete_project_workspace(workspace, projects)

        self.assertFalse(workspace.exists())
        scratch = root / "scratch"
        scratch.mkdir()
        with self.assertRaisesRegex(ValueError, "directly under"):
            delete_project_workspace(scratch, projects)

    def test_session_workspace_match_includes_descendant_directories(self) -> None:
        workspace = Path(self.temporary_directory.name) / "projects" / "payments"
        record = self.store.create(
            workspace=workspace,
            provider="groq",
            model="groq/test",
            title="Payments fix",
        )

        matches = self.store.matching_workspace_path(workspace / "payments-api" / "src")

        self.assertEqual(matches, [record])
        self.assertEqual(
            self.store.matching_workspace_path(
                Path(self.temporary_directory.name) / "unrelated"
            ),
            [],
        )

    def test_launch_directory_matches_one_session_workspace(self) -> None:
        workspace = Path(self.temporary_directory.name) / "projects" / "payments"
        record = self.store.create(
            workspace=workspace,
            provider="groq",
            model="groq/test",
            title="Payments fix",
        )

        with patch.dict(
            "os.environ", {"BANK_AGENT_LAUNCH_DIRECTORY": str(workspace / "repo")}, clear=False
        ):
            matches = sessions_matching_launch_directory(self.store)

        self.assertEqual(matches, [record])

    def test_llm_request_timeout_defaults_and_can_be_overridden(self) -> None:
        base_settings = load_settings(
            {
                "BANK_AGENT_PROVIDER": "groq",
                "BANK_AGENT_GROQ_API_KEY": "test-key",
            }
        )
        configured_settings = load_settings(
            {
                "BANK_AGENT_PROVIDER": "groq",
                "BANK_AGENT_GROQ_API_KEY": "test-key",
                "BANK_AGENT_LLM_TIMEOUT_SECONDS": "45",
                "BANK_AGENT_LLM_NUM_RETRIES": "0",
            }
        )

        self.assertEqual(base_settings.llm_timeout_seconds, 120)
        self.assertEqual(base_settings.llm_num_retries, 1)
        self.assertEqual(configured_settings.llm_timeout_seconds, 45)
        self.assertEqual(configured_settings.llm_num_retries, 0)

    def test_shutdown_keyboard_interrupt_is_not_re_raised(self) -> None:
        conversation = SimpleNamespace(close=lambda: (_ for _ in ()).throw(KeyboardInterrupt()))

        close_conversation_safely(conversation)

    def test_chat_exit_commands_distinguish_home_from_terminal_exit(self) -> None:
        for command, expected in (
            ("/e", "home"),
            ("/exit", "home"),
            ("/q", "terminal"),
            ("/quit", "terminal"),
        ):
            with self.subTest(command=command), patch("builtins.input", return_value=command):
                result = interactive_loop(
                    SimpleNamespace(),
                    Path(self.temporary_directory.name),
                    SimpleNamespace(),
                    self.store,
                    "1" * 32,
                )
            self.assertEqual(result, expected)

    def test_chat_help_command_shows_help_then_returns_to_chat(self) -> None:
        with (
            patch("builtins.input", side_effect=["/help", "/e"]),
            patch("bank_agent.main.show_help") as show_help,
        ):
            result = interactive_loop(
                SimpleNamespace(),
                Path(self.temporary_directory.name),
                SimpleNamespace(),
                self.store,
                "1" * 32,
            )

        self.assertEqual(result, "home")
        show_help.assert_called_once_with(HELP_PATH)

    def test_paste_commands_send_the_complete_draft_as_one_task(self) -> None:
        pasted_task = "Review this code:\n\n```python\nprint('hello')\n```\n"
        for command in ("/p", "/paste"):
            with (
                self.subTest(command=command),
                patch("builtins.input", side_effect=[command, "/e"]),
                patch("bank_agent.main.compose_pasted_task", return_value=pasted_task),
                patch("bank_agent.main.echo_user_message") as echo_user_message,
                patch("bank_agent.main.run_task") as run_task_mock,
            ):
                result = interactive_loop(
                    SimpleNamespace(),
                    Path(self.temporary_directory.name),
                    SimpleNamespace(),
                    self.store,
                    "1" * 32,
                )

            self.assertEqual(result, "home")
            echo_user_message.assert_called_once_with(pasted_task)
            run_task_mock.assert_called_once_with(
                unittest.mock.ANY,
                Path(self.temporary_directory.name),
                unittest.mock.ANY,
                pasted_task,
                self.store,
                "1" * 32,
            )

    def test_skill_install_command_is_available_only_with_an_installer(self) -> None:
        installer_calls: list[bool] = []

        def install() -> bool:
            installer_calls.append(True)
            return True

        with patch("builtins.input", return_value="/install"):
            result = interactive_loop(
                SimpleNamespace(),
                Path(self.temporary_directory.name),
                SimpleNamespace(),
                self.store,
                "1" * 32,
                install,
            )

        self.assertEqual(result, "skill-installed")
        self.assertEqual(installer_calls, [True])

    def test_main_returns_to_picker_in_the_same_process(self) -> None:
        with patch(
            "bank_agent.main._run_once", side_effect=[RETURN_TO_SESSION_PICKER, 0]
        ) as run_once:
            result = main(["--provider", "groq"])

        self.assertEqual(result, 0)
        self.assertEqual(run_once.call_count, 2)
        self.assertEqual(run_once.call_args_list[1].args[0].provider, "groq")
        self.assertEqual(run_once.call_args_list[1].args[0].task, [])

    def test_terminal_exit_code_bypasses_the_session_picker(self) -> None:
        with patch(
            "bank_agent.main._run_once", return_value=KEEP_TERMINAL_OPEN_EXIT_CODE
        ) as run_once:
            result = main([])

        self.assertEqual(result, KEEP_TERMINAL_OPEN_EXIT_CODE)
        run_once.assert_called_once()

    def test_project_workspaces_are_sorted_and_files_are_excluded(self) -> None:
        projects = Path(self.temporary_directory.name) / "projects"
        (projects / "zebra").mkdir(parents=True)
        (projects / "Alpha").mkdir()
        (projects / "notes.txt").write_text("not a workspace", encoding="utf-8")

        self.assertEqual(
            [workspace.name for workspace in list_project_workspaces(projects)],
            ["Alpha", "zebra"],
        )

    def test_creating_project_workspace_requires_one_new_safe_directory_name(self) -> None:
        projects = Path(self.temporary_directory.name) / "projects"
        workspace = create_project_workspace(projects, "payments-api")

        self.assertTrue(workspace.is_dir())
        self.assertEqual(workspace.parent, projects.resolve())
        with self.assertRaises(ValueError):
            create_project_workspace(projects, "../outside")
        with self.assertRaises(ValueError):
            create_project_workspace(projects, "payments-api")
        with self.assertRaises(ValueError):
            create_project_workspace(projects, "PAYMENTS-API")

    def test_session_and_workspace_names_detect_whitespace(self) -> None:
        self.assertTrue(has_whitespace("debug repo"))
        self.assertTrue(has_whitespace("debug\trepo"))
        self.assertFalse(has_whitespace("debug-repo"))
        self.assertEqual(name_suggestions("some session"), ("someSession", "some-session"))
        self.assertEqual(
            whitespace_name_message("Session", "some session"),
            'Session names cannot contain spaces. Try "someSession" or "some-session".',
        )

        projects = Path(self.temporary_directory.name) / "projects"
        with self.assertRaises(ValueError):
            create_project_workspace(projects, "debug repo")

    def test_workspace_picker_reprompts_after_a_name_with_spaces(self) -> None:
        root = Path(self.temporary_directory.name)
        projects = root / "projects"
        scratch = root / "scratch"
        scratch.mkdir()

        with (
            patch("bank_agent.sessions.os.system"),
            patch("builtins.input", side_effect=["debug workspace", "debug-workspace"]),
            patch("msvcrt.getwch", return_value="\r"),
        ):
            workspace = choose_workspace(projects, scratch)

        self.assertEqual(workspace, (projects / "debug-workspace").resolve())

    def test_session_picker_reprompts_after_a_name_with_spaces(self) -> None:
        root = Path(self.temporary_directory.name)
        projects = root / "projects"
        scratch = root / "scratch"
        scratch.mkdir()

        with (
            patch("bank_agent.sessions.os.name", "nt"),
            patch("bank_agent.sessions.sys.stdin.isatty", return_value=True),
            patch("bank_agent.sessions.sys.stdout.isatty", return_value=True),
            patch("bank_agent.sessions.os.system"),
            patch(
                "builtins.input",
                side_effect=["debug repo", "debug-repo", "debug-workspace"],
            ),
            patch("msvcrt.getwch", side_effect=["n", "\r"]),
        ):
            choice = choose_session(
                self.store,
                projects,
                scratch,
                root / "AGENTS.md",
                root / "HELP.txt",
            )

        self.assertEqual(choice.action, "new")
        self.assertEqual(choice.title, "debug-repo")
        self.assertEqual(choice.workspace, (projects / "debug-workspace").resolve())

    def test_empty_session_name_returns_to_the_session_list(self) -> None:
        root = Path(self.temporary_directory.name)
        scratch = root / "scratch"
        scratch.mkdir()

        with (
            patch("bank_agent.sessions.os.name", "nt"),
            patch("bank_agent.sessions.sys.stdin.isatty", return_value=True),
            patch("bank_agent.sessions.sys.stdout.isatty", return_value=True),
            patch("bank_agent.sessions.os.system"),
            patch("builtins.input", return_value=""),
            patch("msvcrt.getwch", side_effect=["n", "\x1b"]),
        ):
            choice = choose_session(
                self.store,
                root / "projects",
                scratch,
                root / "AGENTS.md",
                root / "HELP.txt",
            )

        self.assertEqual(choice.action, "quit")

    def test_session_picker_deletes_history_but_keeps_workspace(self) -> None:
        root = Path(self.temporary_directory.name)
        workspace = root / "projects" / "payments"
        workspace.mkdir(parents=True)
        record = self.store.create(
            workspace=workspace,
            provider="groq",
            model="groq/test",
            title="Payments fix",
        )
        history_directory = self.store.base_dir / record.id
        history_directory.mkdir()
        (history_directory / "base_state.json").write_text("{}", encoding="utf-8")
        scratch = root / "scratch"
        scratch.mkdir()

        with (
            patch("bank_agent.sessions.os.name", "nt"),
            patch("bank_agent.sessions.sys.stdin.isatty", return_value=True),
            patch("bank_agent.sessions.sys.stdout.isatty", return_value=True),
            patch("bank_agent.sessions.os.system"),
            patch("builtins.input", side_effect=["y", "n"]),
            patch("msvcrt.getwch", side_effect=["d", "\x1b"]),
        ):
            choice = choose_session(
                self.store,
                root / "projects",
                scratch,
                root / "AGENTS.md",
                root / "HELP.txt",
            )

        self.assertEqual(choice.action, "quit")
        self.assertEqual(self.store.list(archived=None), [])
        self.assertFalse(history_directory.exists())
        self.assertTrue(workspace.exists())

    def test_session_picker_can_delete_its_unshared_project_workspace(self) -> None:
        root = Path(self.temporary_directory.name)
        projects = root / "projects"
        workspace = projects / "payments"
        workspace.mkdir(parents=True)
        record = self.store.create(
            workspace=workspace,
            provider="groq",
            model="groq/test",
            title="Payments fix",
        )
        scratch = root / "scratch"
        scratch.mkdir()

        with (
            patch("bank_agent.sessions.os.name", "nt"),
            patch("bank_agent.sessions.sys.stdin.isatty", return_value=True),
            patch("bank_agent.sessions.sys.stdout.isatty", return_value=True),
            patch("bank_agent.sessions.os.system"),
            patch("builtins.input", side_effect=["y", "y"]),
            patch("msvcrt.getwch", side_effect=["d", "\x1b"]),
        ):
            choice = choose_session(
                self.store,
                projects,
                scratch,
                root / "AGENTS.md",
                root / "HELP.txt",
            )

        self.assertEqual(choice.action, "quit")
        self.assertEqual(self.store.list(archived=None), [])
        self.assertFalse(workspace.exists())

    def test_shared_workspace_is_kept_without_a_second_confirmation(self) -> None:
        root = Path(self.temporary_directory.name)
        projects = root / "projects"
        workspace = projects / "payments"
        workspace.mkdir(parents=True)
        first = self.store.create(
            workspace=workspace,
            provider="groq",
            model="groq/test",
            title="First session",
        )
        second = self.store.create(
            workspace=workspace,
            provider="groq",
            model="groq/test",
            title="Second session",
        )
        scratch = root / "scratch"
        scratch.mkdir()

        with (
            patch("bank_agent.sessions.os.name", "nt"),
            patch("bank_agent.sessions.sys.stdin.isatty", return_value=True),
            patch("bank_agent.sessions.sys.stdout.isatty", return_value=True),
            patch("bank_agent.sessions.os.system"),
            patch("builtins.input", return_value="y") as confirmation,
            patch("msvcrt.getwch", side_effect=["d", "\x1b"]),
        ):
            choice = choose_session(
                self.store,
                projects,
                scratch,
                root / "AGENTS.md",
                root / "HELP.txt",
            )

        self.assertEqual(choice.action, "quit")
        self.assertEqual(confirmation.call_count, 1)
        self.assertEqual(len(self.store.list(archived=None)), 1)
        self.assertTrue(workspace.exists())
        self.assertIn(self.store.list(archived=None)[0].id, {first.id, second.id})

    def test_workspace_picker_defaults_and_shortcuts_to_new_workspace(self) -> None:
        root = Path(self.temporary_directory.name)
        projects = root / "projects"
        (projects / "existing-a").mkdir(parents=True)
        (projects / "existing-b").mkdir()
        scratch = root / "scratch"
        scratch.mkdir()

        with (
            patch("bank_agent.sessions.os.system"),
            patch("builtins.input", return_value="new-project"),
            patch("msvcrt.getwch", return_value="\r"),
        ):
            selected_by_default = choose_workspace(projects, scratch)

        with (
            patch("bank_agent.sessions.os.system"),
            patch("builtins.input", return_value="shortcut-project"),
            patch("msvcrt.getwch", return_value="n"),
        ):
            selected_by_shortcut = choose_workspace(projects, scratch)

        self.assertEqual(selected_by_default, (projects / "new-project").resolve())
        self.assertEqual(selected_by_shortcut, (projects / "shortcut-project").resolve())

    def test_session_lifecycle_tracks_each_cli_start_and_normal_end(self) -> None:
        record = self.store.create(
            workspace=Path("C:/work/agent-workspace/projects/payments"),
            provider="gemini",
            model="bank_gemini/test",
        )

        started = self.store.start_run(record.id)
        ended = self.store.end_run(record.id)

        self.assertEqual(started.run_count, 1)
        self.assertIsNotNone(started.last_started_at)
        self.assertEqual(ended.run_count, 1)
        self.assertIsNotNone(ended.last_ended_at)

    def test_session_lock_rejects_a_second_active_window(self) -> None:
        session_id = "1" * 32
        first_window = SessionLock.acquire(self.store.base_dir, session_id)
        try:
            with self.assertRaises(SessionInUseError):
                SessionLock.acquire(self.store.base_dir, session_id)
        finally:
            first_window.release()

        second_window = SessionLock.acquire(self.store.base_dir, session_id)
        second_window.release()
        self.assertFalse((self.store.base_dir / "locks" / f"{session_id}.lock").exists())

    def test_session_lock_recovers_a_stale_lock(self) -> None:
        session_id = "2" * 32
        lock_path = self.store.base_dir / "locks" / f"{session_id}.lock"
        lock_path.parent.mkdir(parents=True)
        lock_path.write_text(
            '{"owner_id":"old","pid":999999,"host":"local","started_at":"old"}\n',
            encoding="utf-8",
        )

        with patch("bank_agent.sessions.socket.gethostname", return_value="local"), patch(
            "bank_agent.sessions._process_is_active", return_value=False
        ):
            recovered_lock = SessionLock.acquire(self.store.base_dir, session_id)

        self.assertNotEqual(recovered_lock.owner_id, "old")
        recovered_lock.release()

    def test_only_general_and_workspace_instruction_files_are_loaded(self) -> None:
        root = Path(self.temporary_directory.name)
        general = root / "agent-workspace" / "AGENTS.md"
        workspace = root / "agent-workspace" / "projects" / "test1"
        nested = workspace / "debugRepo" / "src" / "AGENTS.md"
        general.parent.mkdir(parents=True)
        nested.parent.mkdir(parents=True)
        general.write_text("General organization guidance", encoding="utf-8")
        (workspace / "AGENTS.md").write_text("Test1 guidance", encoding="utf-8")
        nested.write_text("Repository-only guidance", encoding="utf-8")

        context, sources = workspace_instruction_context(workspace, general_path=general)

        self.assertIn("General organization guidance", context)
        self.assertIn("Test1 guidance", context)
        self.assertNotIn("Repository-only guidance", context)
        self.assertIn(f"Its session-specific instruction file is: {workspace.resolve() / 'AGENTS.md'}", context)
        self.assertIn("Create it if it does not exist.", context)
        self.assertIn(f"The cross-project default instruction file is: {general.resolve()}", context)
        self.assertEqual(sources, (general.resolve(), (workspace / "AGENTS.md").resolve()))

    def test_new_workspace_opens_notepad_and_keeps_saved_instructions(self) -> None:
        workspace = Path(self.temporary_directory.name) / "projects" / "test1"
        workspace.mkdir(parents=True)

        def save_instructions(command: list[str], *, check: bool) -> None:
            self.assertEqual(command[0], "notepad.exe")
            self.assertFalse(check)
            Path(command[1]).write_text(
                "Use read-only logs first\nThen inspect GKE logs\n", encoding="utf-8"
            )

        with (
            patch("msvcrt.getwch", return_value="\r"),
            patch(
                "bank_agent.instructions.subprocess.run",
                side_effect=save_instructions,
            ) as editor,
        ):
            offer_workspace_instructions(workspace)

        editor.assert_called_once()
        self.assertEqual(
            (workspace / "AGENTS.md").read_text(encoding="utf-8"),
            "Use read-only logs first\nThen inspect GKE logs\n",
        )

    def test_new_workspace_removes_empty_instruction_file(self) -> None:
        workspace = Path(self.temporary_directory.name) / "projects" / "test1"
        workspace.mkdir(parents=True)

        with (
            patch("msvcrt.getwch", return_value="\r"),
            patch("bank_agent.instructions.subprocess.run"),
        ):
            offer_workspace_instructions(workspace)

        self.assertFalse((workspace / "AGENTS.md").exists())

    def test_new_workspace_can_skip_instruction_editor(self) -> None:
        workspace = Path(self.temporary_directory.name) / "projects" / "test1"
        workspace.mkdir(parents=True)

        with (
            patch("msvcrt.getwch", return_value="p"),
            patch("bank_agent.instructions.subprocess.run") as editor,
        ):
            offer_workspace_instructions(workspace)

        editor.assert_not_called()
        self.assertFalse((workspace / "AGENTS.md").exists())

    def test_general_instructions_are_the_only_workspace_external_edit_allowed(self) -> None:
        workspace = Path(self.temporary_directory.name) / "projects" / "test1"
        workspace.mkdir(parents=True)
        general_edit = SimpleNamespace(
            tool_name="write_file", file_path=str(GENERAL_INSTRUCTIONS_PATH)
        )
        other_edit = SimpleNamespace(
            tool_name="write_file",
            file_path=str(Path(self.temporary_directory.name) / "outside.txt"),
        )

        self.assertTrue(is_direct_file_write_permitted(general_edit, workspace))
        self.assertFalse(is_direct_file_write_permitted(other_edit, workspace))

    def test_only_direct_file_writes_inside_the_workspace_are_auto_approved(self) -> None:
        root = Path(self.temporary_directory.name)
        workspace = root / "projects" / "test1"
        workspace.mkdir(parents=True)
        relative_edit = SimpleNamespace(tool_name="edit", file_path="src/App.java")
        absolute_edit = SimpleNamespace(
            tool_name="write_file", file_path=str(workspace / "README.md")
        )
        escaped_edit = SimpleNamespace(
            tool_name="write_file", file_path="../outside.txt"
        )
        general_edit = SimpleNamespace(
            tool_name="write_file", file_path=str(GENERAL_INSTRUCTIONS_PATH)
        )
        terminal_action = SimpleNamespace(tool_name="terminal", command="mvn test")

        self.assertTrue(is_workspace_file_write(relative_edit, workspace))
        self.assertTrue(is_workspace_file_write(absolute_edit, workspace))
        self.assertFalse(is_workspace_file_write(escaped_edit, workspace))
        self.assertFalse(is_workspace_file_write(general_edit, workspace))
        self.assertFalse(is_workspace_file_write(terminal_action, workspace))

    def test_read_only_git_with_dash_c_is_auto_approved_for_any_directory(self) -> None:
        for command in (
            "git -C quotesClient status",
            "git -C .. diff --stat",
            'git -C "C:\\other workspace\\repository" log -5',
            "git -C first -C nested show HEAD",
        ):
            with self.subTest(command=command):
                action = SimpleNamespace(
                    tool_name="terminal",
                    command=command,
                    is_input=False,
                )
                self.assertTrue(is_auto_approved_action(action))

    def test_mutating_or_malformed_git_with_dash_c_still_requires_approval(self) -> None:
        for command in (
            "git -C quotesClient reset --hard",
            "git -C quotesClient checkout main",
            "git -C",
            'git -C "unterminated status',
        ):
            with self.subTest(command=command):
                action = SimpleNamespace(
                    tool_name="terminal",
                    command=command,
                    is_input=False,
                )
                self.assertFalse(is_auto_approved_action(action))

    def test_workspace_file_write_runs_without_calling_the_confirmer(self) -> None:
        workspace = Path(self.temporary_directory.name) / "workspace"
        workspace.mkdir()
        action = SimpleNamespace(tool_name="write_file", file_path="src/App.java")
        state = SimpleNamespace(
            execution_status=ConversationExecutionStatus.WAITING_FOR_CONFIRMATION,
            events=[],
        )
        conversation = SimpleNamespace(state=state)
        confirmer_calls: list[list[object]] = []

        def finish(_: object) -> None:
            state.execution_status = ConversationExecutionStatus.FINISHED

        def confirmer(actions: list[object]) -> bool:
            confirmer_calls.append(actions)
            return True

        with (
            patch.object(
                ConversationState,
                "get_unmatched_actions",
                return_value=[action],
            ),
            patch("bank_agent.approvals.run_with_activity", side_effect=finish),
        ):
            run_until_finished(
                conversation,
                workspace,
                CommandAllowlist(
                    Path(self.temporary_directory.name) / "allowlist.json"
                ),
                confirmer=confirmer,
            )

        self.assertEqual(confirmer_calls, [])

    def test_skill_draft_workspace_is_allowed_but_nested_drafts_are_not(self) -> None:
        root = Path(self.temporary_directory.name)
        projects = root / "projects"
        scratch = root / "scratch"
        drafts = root / "skill-drafts"
        draft = drafts / "create-skill-12345678"
        nested = draft / "nested"
        projects.mkdir()
        scratch.mkdir()
        nested.mkdir(parents=True)

        with (
            patch("bank_agent.main.PROJECTS_WORKSPACE", projects),
            patch("bank_agent.main.SCRATCH_WORKSPACE", scratch),
            patch("bank_agent.main.SKILL_DRAFTS_PATH", drafts),
        ):
            self.assertEqual(resolve_workspace(str(draft)), draft.resolve())
            self.assertTrue(is_skill_authoring_workspace(draft))
            with self.assertRaisesRegex(ValueError, "one named.*draft"):
                resolve_workspace(str(nested))

    def test_session_picker_does_not_propagate_shelved_skill_creation(self) -> None:
        root = Path(self.temporary_directory.name)
        scratch = root / "scratch"
        scratch.mkdir()

        with (
            patch("bank_agent.sessions.os.name", "nt"),
            patch("bank_agent.sessions.sys.stdin.isatty", return_value=True),
            patch("bank_agent.sessions.sys.stdout.isatty", return_value=True),
            patch("bank_agent.sessions.os.system"),
            patch(
                "bank_agent.sessions.show_settings_menu",
                return_value="create_skill",
            ),
            patch("msvcrt.getwch", side_effect=["s", "\x1b"]),
        ):
            choice = choose_session(
                self.store,
                root / "projects",
                scratch,
                root / "AGENTS.md",
                root / "HELP.txt",
            )

        self.assertEqual(choice.action, "quit")

    def test_resuming_replaces_the_preserved_system_context_with_current_instructions(
        self,
    ) -> None:
        original = SystemPromptEvent(
            source="agent",
            system_prompt=TextContent(text="Base agent instructions"),
            dynamic_context=TextContent(text="Old AGENTS.md content"),
            tools=[],
        )
        conversation = SimpleNamespace(
            state=SimpleNamespace(view=View(events=[original]))
        )

        refreshed = refresh_resumed_system_context(
            conversation, "Current AGENTS.md content"
        )

        self.assertTrue(refreshed)
        event = conversation.state.view.events[0]
        self.assertIsInstance(event, SystemPromptEvent)
        self.assertEqual(event.system_prompt.text, "Base agent instructions")
        self.assertEqual(event.dynamic_context.text, "Current AGENTS.md content")

    def test_resuming_replays_only_the_last_ten_user_and_agent_messages(self) -> None:
        messages = [
            MessageEvent(
                source="user" if index % 2 == 0 else "agent",
                llm_message=Message(
                    role="user" if index % 2 == 0 else "assistant",
                    content=[TextContent(text=f"message {index}")],
                ),
            )
            for index in range(12)
        ]
        messages.insert(
            5,
            MessageEvent(
                source="environment",
                llm_message=Message(
                    role="user",
                    content=[TextContent(text="internal tool feedback")],
                ),
            ),
        )
        conversation = SimpleNamespace(
            state=SimpleNamespace(view=SimpleNamespace(events=messages))
        )

        visible, omitted = recent_visible_messages(messages)
        output = StringIO()
        with patch("sys.stdout", output):
            show_resumed_history(conversation)

        self.assertTrue(omitted)
        self.assertEqual(
            [text for _, text in visible],
            [f"message {i}" for i in range(2, 12)],
        )
        rendered = output.getvalue()
        self.assertIn("SESSION RESUMED | earlier messages omitted", rendered)
        self.assertNotIn("message 0\n", rendered)
        self.assertNotIn("internal tool feedback", rendered)
        self.assertIn("You >\nmessage 10", rendered)
        self.assertIn("Agent >\nmessage 11", rendered)


if __name__ == "__main__":
    unittest.main()
