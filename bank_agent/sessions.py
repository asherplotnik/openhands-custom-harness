"""Local, single-user session metadata for the bank-agent CLI."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from bank_agent.skills import GUIDED_SKILL_CREATION_ENABLED, load_harness_skills
from bank_agent.ui import (
    ACCENT,
    MUTED,
    WARNING,
    field,
    notice as ui_notice,
    screen_title,
    selected_text,
    selection_marker,
    shortcut_bar,
    status as styled_status,
    style,
)


SESSION_INDEX_VERSION = 2
UNTITLED_SESSION = "New session"
_WORKSPACE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _process_is_active(pid: int) -> bool:
    """Check local process liveness without relying on Unix-only signal semantics."""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    invalid_parameter = 87
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    get_exit_code_process = kernel32.GetExitCodeProcess
    get_exit_code_process.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    get_exit_code_process.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    handle = open_process(process_query_limited_information, False, pid)
    if not handle:
        # Access denied still means a process exists; only an invalid PID is stale.
        return ctypes.get_last_error() != invalid_parameter
    try:
        exit_code = wintypes.DWORD()
        if not get_exit_code_process(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == still_active
    finally:
        close_handle(handle)


def title_from_task(task: str) -> str:
    """Build a compact, one-line title without interpreting user content."""
    title = " ".join(task.split())
    return title[:80] if title else UNTITLED_SESSION


def has_whitespace(value: str) -> bool:
    """Return whether a user-provided identifier contains any whitespace."""
    return any(character.isspace() for character in value)


def name_suggestions(value: str) -> tuple[str, str]:
    """Offer camelCase and kebab-case alternatives for a spaced identifier."""
    words = value.split()
    kebab_case = "-".join(word.lower() for word in words)
    camel_case = words[0].lower() + "".join(
        word[:1].upper() + word[1:] for word in words[1:]
    )
    return camel_case, kebab_case


def whitespace_name_message(kind: str, value: str) -> str:
    """Explain the naming rule with suggestions based on the attempted name."""
    camel_case, kebab_case = name_suggestions(value)
    return f'{kind} names cannot contain spaces. Try "{camel_case}" or "{kebab_case}".'


@dataclass(frozen=True)
class SessionRecord:
    """The small, user-visible index entry for one persisted conversation."""

    id: str
    title: str
    workspace: str
    provider: str
    model: str
    created_at: str
    updated_at: str
    archived: bool = False
    archived_at: str | None = None
    run_count: int = 0
    last_started_at: str | None = None
    last_ended_at: str | None = None

    @classmethod
    def from_dict(cls, data: object) -> "SessionRecord":
        if not isinstance(data, dict):
            raise ValueError("Session record must be an object")
        required = {
            "id",
            "title",
            "workspace",
            "provider",
            "model",
            "created_at",
            "updated_at",
            "archived",
            "archived_at",
        }
        lifecycle_fields = {"run_count", "last_started_at", "last_ended_at"}
        field_names = set(data)
        if (
            field_names != required
            and field_names != required | lifecycle_fields
        ) or not all(
            isinstance(data[key], str)
            for key in ("id", "title", "workspace", "provider", "model", "created_at", "updated_at")
        ):
            raise ValueError("Invalid session record")
        if not isinstance(data["archived"], bool) or (
            data["archived_at"] is not None and not isinstance(data["archived_at"], str)
        ):
            raise ValueError("Invalid session archive state")
        run_count = data.get("run_count", 0)
        if not isinstance(run_count, int) or run_count < 0:
            raise ValueError("Invalid session lifecycle state")
        for field_name in ("last_started_at", "last_ended_at"):
            if data.get(field_name) is not None and not isinstance(data[field_name], str):
                raise ValueError("Invalid session lifecycle state")
        try:
            normalized_id = uuid.UUID(hex=data["id"]).hex
        except (TypeError, ValueError) as error:
            raise ValueError("Invalid session ID") from error
        return cls(
            id=normalized_id,
            title=data["title"],
            workspace=data["workspace"],
            provider=data["provider"],
            model=data["model"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            archived=data["archived"],
            archived_at=data["archived_at"],
            run_count=run_count,
            last_started_at=data.get("last_started_at"),
            last_ended_at=data.get("last_ended_at"),
        )


class SessionStore:
    """An atomic JSON index beside the SDK's per-session event directories."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.index_path = base_dir / "index.json"

    def list(self, *, archived: bool | None = False) -> list[SessionRecord]:
        records = self._load()
        if archived is not None:
            records = [record for record in records if record.archived is archived]
        return sorted(records, key=lambda record: record.updated_at, reverse=True)

    def matching_workspace_path(self, directory: Path) -> list[SessionRecord]:
        """Find sessions whose saved workspace contains ``directory``."""
        try:
            resolved_directory = directory.resolve()
        except OSError:
            return []

        matches: list[SessionRecord] = []
        for record in self._load():
            try:
                resolved_directory.relative_to(Path(record.workspace).resolve())
            except (OSError, ValueError):
                continue
            matches.append(record)
        return sorted(matches, key=lambda record: record.updated_at, reverse=True)

    def has_title(self, title: str) -> bool:
        """Check names across active and archived sessions without exposing their state."""
        normalized_title = title.casefold()
        return any(record.title.casefold() == normalized_title for record in self._load())

    def sharing_workspace(self, session: SessionRecord) -> list[SessionRecord]:
        """Return other active or archived sessions using the same workspace."""
        try:
            workspace = Path(session.workspace).resolve()
        except OSError:
            return []
        matches: list[SessionRecord] = []
        for record in self._load():
            if record.id == session.id:
                continue
            try:
                if Path(record.workspace).resolve() == workspace:
                    matches.append(record)
            except OSError:
                continue
        return matches

    def create(
        self,
        *,
        workspace: Path,
        provider: str,
        model: str,
        session_id: str | None = None,
        title: str = UNTITLED_SESSION,
    ) -> SessionRecord:
        records = self._load()
        record = SessionRecord(
            id=uuid.UUID(hex=session_id).hex if session_id else uuid.uuid4().hex,
            title=title,
            workspace=str(workspace),
            provider=provider,
            model=model,
            created_at=_now(),
            updated_at=_now(),
        )
        if any(existing.id == record.id for existing in records):
            raise ValueError(f"Session already exists: {record.id}")
        self._save([*records, record])
        return record

    def get(self, identifier: str) -> SessionRecord:
        """Resolve an exact session name, full ID, or unambiguous ID prefix."""
        name = identifier.strip()
        name_matches = [
            record for record in self._load() if record.title.casefold() == name.casefold()
        ]
        if len(name_matches) == 1:
            return name_matches[0]
        if len(name_matches) > 1:
            raise ValueError(f"Session name is ambiguous: {identifier}")

        normalized = name.lower().replace("-", "")
        matches = [record for record in self._load() if record.id.startswith(normalized)]
        if not matches:
            raise ValueError(f"No session matches: {identifier}")
        if len(matches) != 1:
            raise ValueError(f"Session ID is ambiguous: {identifier}")
        return matches[0]

    def touch(self, session_id: str, *, task: str | None = None) -> SessionRecord:
        record = self.get(session_id)
        title = record.title
        if task and title == UNTITLED_SESSION:
            title = title_from_task(task)
        return self._replace(
            record,
            title=title,
            updated_at=_now(),
        )

    def archive(self, identifier: str) -> SessionRecord:
        record = self.get(identifier)
        return self._replace(record, archived=True, archived_at=_now(), updated_at=_now())

    def unarchive(self, identifier: str) -> SessionRecord:
        record = self.get(identifier)
        return self._replace(record, archived=False, archived_at=None, updated_at=_now())

    def delete(self, identifier: str) -> SessionRecord:
        """Permanently delete one session while leaving its workspace untouched."""
        record = self.get(identifier)
        session_lock = SessionLock.acquire(self.base_dir, record.id)
        try:
            session_directory = self.base_dir / record.id
            resolved_base = self.base_dir.resolve()
            resolved_session = session_directory.resolve()
            if resolved_session.parent != resolved_base:
                raise ValueError(
                    f"Refusing to delete an invalid session path: {session_directory}"
                )
            if session_directory.exists():
                if not session_directory.is_dir():
                    raise ValueError(
                        f"Session history path is not a directory: {session_directory}"
                    )
                shutil.rmtree(session_directory)
            self._save([item for item in self._load() if item.id != record.id])
            return record
        finally:
            session_lock.release()

    def start_run(self, session_id: str) -> SessionRecord:
        """Record a CLI start before the agent receives its next message."""
        record = self.get(session_id)
        now = _now()
        return self._replace(
            record,
            run_count=record.run_count + 1,
            last_started_at=now,
            updated_at=now,
        )

    def end_run(self, session_id: str) -> SessionRecord:
        """Record a normal CLI shutdown after tool executors are closed."""
        record = self.get(session_id)
        now = _now()
        return self._replace(record, last_ended_at=now, updated_at=now)

    def _replace(self, record: SessionRecord, **changes: object) -> SessionRecord:
        updated = SessionRecord(**(asdict(record) | changes))
        records = [updated if item.id == record.id else item for item in self._load()]
        self._save(records)
        return updated

    def _load(self) -> list[SessionRecord]:
        if not self.index_path.is_file():
            return []
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"Could not read session index: {self.index_path}") from error
        if not isinstance(data, dict) or data.get("version") not in {1, SESSION_INDEX_VERSION}:
            raise ValueError(f"Invalid session index format: {self.index_path}")
        entries = data.get("sessions")
        if not isinstance(entries, list):
            raise ValueError(f"Invalid session index format: {self.index_path}")
        records = [SessionRecord.from_dict(entry) for entry in entries]
        if len({record.id for record in records}) != len(records):
            raise ValueError(f"Duplicate session ID in index: {self.index_path}")
        return records

    def _save(self, records: list[SessionRecord]) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": SESSION_INDEX_VERSION,
            "sessions": [asdict(record) for record in records],
        }
        temporary_path = self.index_path.with_suffix(".tmp")
        temporary_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary_path.replace(self.index_path)


class SessionInUseError(ValueError):
    """Raised when another Bank Agent process owns a session lock."""


@dataclass
class SessionLock:
    """An exclusive, crash-recoverable lock for one locally persisted session."""

    path: Path
    owner_id: str
    released: bool = False

    @classmethod
    def acquire(cls, sessions_dir: Path, session_id: str) -> "SessionLock":
        lock_directory = sessions_dir / "locks"
        lock_path = lock_directory / f"{uuid.UUID(hex=session_id).hex}.lock"
        owner_id = uuid.uuid4().hex
        payload = {
            "owner_id": owner_id,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "started_at": _now(),
        }
        lock_directory.mkdir(parents=True, exist_ok=True)

        for _ in range(3):
            try:
                with lock_path.open("x", encoding="utf-8") as lock_file:
                    json.dump(payload, lock_file)
                    lock_file.write("\n")
                return cls(lock_path, owner_id)
            except FileExistsError:
                owner = cls._read_owner(lock_path)
                if cls._owner_is_active(owner):
                    pid = owner.get("pid")
                    started_at = owner.get("started_at", "an unknown time")
                    raise SessionInUseError(
                        f"Session {session_id[:8]} is already active in another Bank Agent "
                        f"window (PID {pid}, started {started_at})."
                    )
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    continue

        raise OSError(f"Could not acquire the lock for session {session_id[:8]}")

    @staticmethod
    def _read_owner(lock_path: Path) -> dict[str, object]:
        try:
            data = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _owner_is_active(owner: dict[str, object]) -> bool:
        pid = owner.get("pid")
        host = owner.get("host")
        if not isinstance(pid, int) or pid <= 0:
            return False
        if host != socket.gethostname():
            return True
        return _process_is_active(pid)

    def release(self) -> None:
        """Remove the lock only when this process remains its recorded owner."""
        if self.released:
            return
        try:
            owner = self._read_owner(self.path)
            if owner.get("owner_id") == self.owner_id:
                self.path.unlink(missing_ok=True)
        finally:
            self.released = True


class SessionPickerChoice:
    """The result of the interactive session picker."""

    def __init__(
        self,
        action: str,
        session: SessionRecord | None = None,
        title: str = UNTITLED_SESSION,
        workspace: Path | None = None,
    ) -> None:
        self.action = action
        self.session = session
        self.title = title
        self.workspace = workspace


def delete_project_workspace(workspace: Path, projects_dir: Path) -> None:
    """Delete one named project workspace, never Scratch or an arbitrary path."""
    resolved_projects = projects_dir.resolve()
    resolved_workspace = workspace.resolve()
    if (
        workspace.is_symlink()
        or resolved_workspace.parent != resolved_projects
        or not resolved_workspace.is_dir()
    ):
        raise ValueError(
            "Only an existing named workspace directly under agent-workspace/projects "
            "can be deleted."
        )
    shutil.rmtree(resolved_workspace)


def list_project_workspaces(projects_dir: Path) -> list[Path]:
    """List immediate project directories deterministically without creating any."""
    if not projects_dir.is_dir():
        return []
    return sorted(
        (path.resolve() for path in projects_dir.iterdir() if path.is_dir()),
        key=lambda path: path.name.casefold(),
    )


def project_workspace_name_exists(projects_dir: Path, name: str) -> bool:
    """Check immediate workspace names case-insensitively before creating one."""
    if not projects_dir.is_dir():
        return False
    normalized_name = name.casefold()
    return any(path.name.casefold() == normalized_name for path in projects_dir.iterdir())


def create_project_workspace(projects_dir: Path, name: str) -> Path:
    """Create one safe child workspace; names cannot escape the projects root."""
    normalized = name.strip()
    if not _WORKSPACE_NAME.fullmatch(normalized):
        raise ValueError(
            "Workspace name may contain only letters, numbers, dot, underscore, and hyphen."
        )
    projects_dir = projects_dir.resolve()
    workspace = (projects_dir / normalized).resolve()
    if workspace.parent != projects_dir:
        raise ValueError("Workspace must be a direct child of projects.")
    if project_workspace_name_exists(projects_dir, normalized):
        raise ValueError(f"Workspace already exists: {normalized}")
    workspace.mkdir(parents=True)
    return workspace


def confirm_existing_workspace(workspace: Path) -> bool:
    """Require an explicit second confirmation before sharing a project folder."""
    import msvcrt

    os.system("cls")
    print(screen_title("Shared workspace"))
    print()
    print(style("!  NOT RECOMMENDED", WARNING))
    print(style("-" * 70, WARNING))
    print(f"\nprojects/{workspace.name} is an existing workspace.")
    print("A new session shares its files but not its previous agent context.")
    print("Resume the existing session unless you deliberately want a fresh view.")
    print()
    print(shortcut_bar((("Enter", "use this workspace anyway"), ("Esc", "go back"))))
    while True:
        key = msvcrt.getwch()
        if key == "\r":
            return True
        if key == "\x1b":
            return False


def choose_workspace(projects_dir: Path, scratch_workspace: Path) -> Path | None:
    """Choose a project, disposable Scratch workspace, or create a project."""
    import msvcrt

    options: list[Path | None] = [
        *list_project_workspaces(projects_dir),
        None,
        scratch_workspace,
    ]
    selected = options.index(None)
    while True:
        os.system("cls")
        print(screen_title("Choose workspace"))
        print()
        for index, option in enumerate(options):
            is_selected = index == selected
            marker = selection_marker(is_selected)
            if option is None:
                label = "Create new workspace"
            elif option == scratch_workspace:
                label = "Start clean scratch session (clears temporary work)"
            else:
                label = f"projects/{option.name}"
            print(f"{marker} {selected_text(label, is_selected)}")
        print()
        print(
            shortcut_bar(
                (
                    ("Up/Down", "select"),
                    ("Enter", "choose"),
                    ("n", "create new workspace"),
                    ("Esc", "back"),
                )
            )
        )

        key = msvcrt.getwch()
        if key in {"\x00", "\xe0"}:
            key = msvcrt.getwch()
            if key == "H":
                selected = (selected - 1) % len(options)
            elif key == "P":
                selected = (selected + 1) % len(options)
        elif key == "\r" or key.lower() == "n":
            selected_option = None if key.lower() == "n" else options[selected]
            if selected_option is not None:
                if (
                    selected_option != scratch_workspace
                    and not confirm_existing_workspace(selected_option)
                ):
                    continue
                return selected_option
            os.system("cls")
            while True:
                raw_name = input("New workspace name: ")
                name = raw_name.strip()
                if not name:
                    break
                if has_whitespace(raw_name):
                    print(
                        ui_notice(
                            whitespace_name_message("Workspace", raw_name),
                            tone="warning",
                        )
                    )
                    continue
                if project_workspace_name_exists(projects_dir, name):
                    print(
                        ui_notice(
                            f'A workspace named "{name}" already exists.',
                            tone="warning",
                        )
                    )
                    input("Press Enter to choose another name.")
                    break
                try:
                    return create_project_workspace(projects_dir, name)
                except ValueError as error:
                    print(ui_notice(str(error), tone="error"))
                    input("Press Enter to try again.")
                    break
        elif key == "\x1b":
            return None


def _skill_summary(value: str | None, limit: int = 68) -> str:
    """Keep skill descriptions readable on one terminal line."""
    summary = " ".join((value or "No description.").split())
    return summary if len(summary) <= limit else summary[: limit - 3] + "..."


def show_skills_menu(skills_path: Path) -> str | None:
    """Browse the harness-owned skill catalog or request guided creation."""
    import msvcrt

    selected = 0
    while True:
        catalog = load_harness_skills(skills_path)
        entries = [
            (
                "valid",
                skill.name,
                _skill_summary(skill.description),
                str(skill.source or skills_path / skill.name / "SKILL.md"),
            )
            for skill in catalog.skills
        ]
        entries.extend(
            (
                "invalid",
                issue.name,
                _skill_summary(issue.message),
                str(skills_path / issue.name / "SKILL.md"),
            )
            for issue in catalog.issues
        )
        entries.sort(key=lambda entry: entry[1].casefold())
        options = list(entries)
        if GUIDED_SKILL_CREATION_ENABLED:
            options.insert(0, ("create", "Create new skill", "", ""))
        selected = min(selected, max(0, len(options) - 1))

        os.system("cls")
        print(screen_title("Skills"))
        print()
        for index, (status, name, summary, _) in enumerate(options):
            is_selected = index == selected
            marker = selection_marker(is_selected)
            if status == "create":
                print(f"{marker} {selected_text(name, is_selected)}")
            else:
                padded_name = f"{name[:28]:28}"
                print(
                    f"{marker} {selected_text(padded_name, is_selected)} "
                    f"{styled_status(f'{status:7}')}  {style(summary, MUTED)}"
                )
        if not entries:
            print(f"\n{style('No skills installed.', MUTED)}")
        if not GUIDED_SKILL_CREATION_ENABLED:
            print(
                "\n"
                + ui_notice(
                    "Guided skill creation is temporarily unavailable.",
                    tone="warning",
                )
            )
        footer_items = [("Up/Down", "select"), ("Enter", "details")]
        if GUIDED_SKILL_CREATION_ENABLED:
            footer_items.append(("n", "create new skill"))
        footer_items.append(("Esc", "back"))
        print(f"\n{shortcut_bar(footer_items)}")

        key = msvcrt.getwch()
        if key in {"\x00", "\xe0"}:
            key = msvcrt.getwch()
            if key == "H" and options:
                selected = (selected - 1) % len(options)
            elif key == "P" and options:
                selected = (selected + 1) % len(options)
        elif GUIDED_SKILL_CREATION_ENABLED and (
            key.lower() == "n" or (key == "\r" and selected == 0)
        ):
            return "create"
        elif key == "\r" and options:
            status, name, summary, location = options[selected]
            os.system("cls")
            print(screen_title("Skill details"))
            print()
            print(field("Name", name))
            print(field("Status", styled_status(status)))
            print(field("Description", summary))
            print(field("Location", location))
            print(f"\n{shortcut_bar((('Any key', 'return'),))}")
            msvcrt.getwch()
        elif key == "\x1b":
            return


def show_settings_menu(
    general_instructions_path: Path, skills_path: Path
) -> str | None:
    """Show the small settings home without exposing implementation filenames."""
    import msvcrt

    options = (
        "Cross-project/session harness instructions",
        "Skills",
    )
    selected = 0
    notice: str | None = None
    while True:
        os.system("cls")
        print(screen_title("Settings"))
        print()
        if notice:
            print(f"{ui_notice(notice, tone='success')}\n")
        for index, option in enumerate(options):
            is_selected = index == selected
            print(
                f"{selection_marker(is_selected)} "
                f"{selected_text(option, is_selected)}"
            )
        print()
        print(
            shortcut_bar(
                (("Up/Down", "select"), ("Enter", "choose"), ("Esc", "return"))
            )
        )

        key = msvcrt.getwch()
        if key in {"\x00", "\xe0"}:
            key = msvcrt.getwch()
            if key == "H":
                selected = (selected - 1) % len(options)
            elif key == "P":
                selected = (selected + 1) % len(options)
        elif key == "\r" and selected == 0:
            try:
                general_instructions_path.parent.mkdir(parents=True, exist_ok=True)
                general_instructions_path.touch(exist_ok=True)
                subprocess.run(["notepad.exe", str(general_instructions_path)], check=False)
                notice = (
                    "Returned from Notepad. Saved changes apply when you start or resume a session."
                )
            except OSError as error:
                notice = f"Could not open the instructions editor: {error}"
        elif key == "\r":
            skills_action = show_skills_menu(skills_path)
            if GUIDED_SKILL_CREATION_ENABLED and skills_action == "create":
                return "create_skill"
        elif key == "\x1b":
            return


def show_help(help_path: Path) -> None:
    """Render the maintained local help file and return to the invoking menu."""
    import msvcrt

    os.system("cls")
    print(screen_title("Help"))
    print()
    try:
        lines = help_path.read_text(encoding="utf-8").rstrip().splitlines()
        if lines and lines[0].strip().casefold() == "bank agent help":
            lines = lines[1:]
            if lines and not lines[0].strip():
                lines = lines[1:]
        for line in lines:
            if line and not line.startswith(" "):
                print(style(line, ACCENT))
            else:
                print(line)
    except OSError as error:
        print(ui_notice(f"Could not read help: {error}", tone="error"))
    print(f"\n{shortcut_bar((('Any key', 'return'),))}")
    msvcrt.getwch()


def choose_session(
    store: SessionStore,
    projects_dir: Path,
    scratch_workspace: Path,
    general_instructions_path: Path,
    help_path: Path,
    *,
    skills_path: Path | None = None,
    notice: str | None = None,
) -> SessionPickerChoice:
    """Offer a lightweight Windows selector without introducing a TUI dependency."""
    if os.name != "nt" or not sys.stdin.isatty() or not sys.stdout.isatty():
        return SessionPickerChoice("new")

    import msvcrt

    query = ""
    selected = 0
    while True:
        # The default picker stays clean; a deliberate search also includes
        # archived sessions so old work remains discoverable.
        sessions = store.list(archived=None if query else False)
        if query:
            needle = query.casefold()
            sessions = [
                session
                for session in sessions
                if needle in f"{session.title} {session.workspace} {session.id}".casefold()
            ]
        if sessions:
            selected = min(selected, len(sessions) - 1)
        else:
            selected = 0

        os.system("cls")
        print(screen_title("Sessions"))
        print()
        if notice:
            notice_tone = "error" if "already active" in notice.casefold() else "info"
            print(f"{ui_notice(notice, tone=notice_tone)}\n")
        if query:
            print(f"{field('Search', query)}\n")
        if not sessions:
            print(style("No active sessions found.", MUTED))
        for index, session in enumerate(sessions):
            is_selected = index == selected
            marker = selection_marker(is_selected)
            status = "archived" if session.archived else "active"
            title = f"{session.title[:46]:46}"
            print(
                f"{marker} {selected_text(title, is_selected)} "
                f"{styled_status(f'{status:8}')} "
                f"{style(session.updated_at[:16], MUTED)}  "
                f"{style(session.id[:8], MUTED)}"
            )
        print()
        print(
            shortcut_bar(
                (
                    ("Up/Down", "select"),
                    ("Enter", "resume"),
                    ("n", "new"),
                    ("/", "search"),
                )
            )
        )
        print(
            shortcut_bar(
                (
                    ("a", "archive/unarchive"),
                    ("d", "delete"),
                    ("s", "settings"),
                    ("h", "help"),
                    ("Esc", "quit"),
                )
            )
        )

        key = msvcrt.getwch()
        if key in {"\x00", "\xe0"}:
            key = msvcrt.getwch()
            if key == "H" and sessions:
                selected = (selected - 1) % len(sessions)
            elif key == "P" and sessions:
                selected = (selected + 1) % len(sessions)
        elif key == "\r" and sessions:
            return SessionPickerChoice("resume", sessions[selected])
        elif key.lower() == "n":
            os.system("cls")
            cancelled = False
            while True:
                raw_name = input("Name this session for later use: ")
                name = raw_name.strip()
                if not name:
                    cancelled = True
                    break
                if has_whitespace(raw_name):
                    print(
                        ui_notice(
                            whitespace_name_message("Session", raw_name),
                            tone="warning",
                        )
                    )
                    continue
                title = title_from_task(name)
                if store.has_title(title):
                    print(
                        ui_notice(
                            f'A session named "{title}" already exists, including '
                            "archived sessions.",
                            tone="warning",
                        )
                    )
                    continue
                break
            if cancelled:
                continue
            workspace = choose_workspace(projects_dir, scratch_workspace)
            if workspace is not None:
                return SessionPickerChoice("new", title=title, workspace=workspace)
        elif key.lower() == "a" and sessions:
            session = sessions[selected]
            if session.archived:
                restored = store.unarchive(session.id)
                notice = f"Unarchived: {restored.title}"
            else:
                archived = store.archive(session.id)
                notice = f"Archived: {archived.title}"
            query = ""
        elif key.lower() == "d" and sessions:
            session = sessions[selected]
            print(
                f'\nPermanently delete session "{session.title}" and its chat history?\n'
                "You can choose separately whether to delete its project workspace."
            )
            if input("Delete this session? [y/N]: ").strip().casefold() == "y":
                workspace = Path(session.workspace)
                shared_sessions = store.sharing_workspace(session)
                try:
                    is_named_project_workspace = (
                        not workspace.is_symlink()
                        and workspace.resolve().parent == projects_dir.resolve()
                        and workspace.is_dir()
                    )
                except OSError:
                    is_named_project_workspace = False
                delete_workspace = False
                if is_named_project_workspace and not shared_sessions:
                    delete_workspace = (
                        input(
                            f'Also permanently delete workspace "{workspace.name}" '
                            "and every file in it? [y/N]: "
                        )
                        .strip()
                        .casefold()
                        == "y"
                    )
                try:
                    deleted = store.delete(session.id)
                except (OSError, ValueError) as error:
                    notice = str(error)
                else:
                    if delete_workspace:
                        remaining_workspace_users = store.sharing_workspace(session)
                        if remaining_workspace_users:
                            notice = (
                                f"Deleted session: {deleted.title}. Workspace kept because "
                                "another session began using it."
                            )
                        else:
                            try:
                                delete_project_workspace(workspace, projects_dir)
                            except (OSError, ValueError) as error:
                                notice = (
                                    f"Deleted session: {deleted.title}. "
                                    f"Could not delete workspace: {error}"
                                )
                            else:
                                notice = (
                                    f"Deleted session and workspace: {deleted.title} / "
                                    f"{workspace.name}."
                                )
                    elif shared_sessions:
                        notice = (
                            f"Deleted session: {deleted.title}. Workspace kept because "
                            f"{len(shared_sessions)} other session(s) use it."
                        )
                    else:
                        notice = f"Deleted session: {deleted.title}. Workspace kept."
                    query = ""
            else:
                notice = "Session deletion cancelled."
        elif key == "/":
            print("\nSearch: ", end="", flush=True)
            query = input().strip()
        elif key.lower() == "s":
            settings_action = show_settings_menu(
                general_instructions_path,
                skills_path or general_instructions_path.parent / "skills",
            )
            if (
                GUIDED_SKILL_CREATION_ENABLED
                and settings_action == "create_skill"
            ):
                return SessionPickerChoice("create_skill")
        elif key.lower() == "h":
            show_help(help_path)
        elif key == "\x1b":
            return SessionPickerChoice("quit")
