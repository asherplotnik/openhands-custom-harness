"""Small read-only search tool compatible with common model tool calls."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field

from openhands.sdk.tool import (
    Action,
    DeclaredResources,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState


class SearchAction(Action):
    """Search file names and text content using a literal query."""

    query: str = Field(description="Literal text to find in file names or file content.")
    path: str = Field(
        default=".",
        description="Directory or file to search. Relative paths use the workspace.",
    )
    max_results: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Maximum number of matching paths or lines to return.",
    )


class SearchObservation(Observation):
    """Results returned by a literal workspace search."""

    query: str
    search_path: str
    matches: list[str] = Field(default_factory=list)
    truncated: bool = False


class SearchExecutor(ToolExecutor[SearchAction, SearchObservation]):
    """Search safely without a shell or a third-party dependency."""

    _MAX_FILE_SIZE = 2 * 1024 * 1024
    _TEXT_SAMPLE_SIZE = 8 * 1024
    _SKIPPED_DIRECTORIES = frozenset(
        {
            ".git",
            ".venv",
            ".openhands",
            ".uv-cache",
            "__pycache__",
            "agent-workspace",
            "local-wheel-transfer",
            "local-wheels",
            "node_modules",
            "policy",
            "sessions",
            "target",
            "tools",
            "vendor",
            "wheelhouse",
        }
    )
    _SENSITIVE_FILES = frozenset({".env", "bank-agent-startup.log"})

    def __init__(self, workspace_root: str, readable_root: str | None = None) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        roots = [self.workspace_root]
        if readable_root:
            roots.append(Path(readable_root).resolve())
        self.readable_roots = tuple(dict.fromkeys(roots))

    @classmethod
    def _is_binary_file(cls, file_path: Path) -> bool:
        """Avoid including compiled or binary data in a model-facing search result."""
        try:
            with file_path.open("rb") as file:
                sample = file.read(cls._TEXT_SAMPLE_SIZE)
        except OSError:
            return True
        if not sample:
            return False
        if b"\x00" in sample:
            return True
        control_bytes = sum(
            byte < 32 and byte not in {9, 10, 13} for byte in sample
        )
        return control_bytes > len(sample) // 16

    def _resolve_allowed_path(self, value: str) -> Path:
        candidate = Path(value).expanduser()
        resolved = (
            candidate if candidate.is_absolute() else self.workspace_root / candidate
        ).resolve()
        for index, root in enumerate(self.readable_roots):
            try:
                relative = resolved.relative_to(root)
            except ValueError:
                continue
            if index > 0:
                if relative.name.casefold() in self._SENSITIVE_FILES:
                    raise ValueError("Search cannot read runtime or secret files.")
                if relative.parts and relative.parts[0].casefold() in {
                    ".openhands",
                    ".uv-cache",
                    "agent-workspace",
                    "policy",
                    "sessions",
                }:
                    raise ValueError("Search cannot read private Bank Agent runtime data.")
            return resolved
        allowed = ", ".join(str(root) for root in self.readable_roots)
        raise ValueError(f"Search path is outside the allowed roots: {allowed}")

    def __call__(
        self,
        action: SearchAction,
        conversation: object | None = None,  # noqa: ARG002
    ) -> SearchObservation:
        query = action.query.strip()
        try:
            search_path = self._resolve_allowed_path(action.path)
            if not query:
                raise ValueError("Search query cannot be empty.")
            if not search_path.exists():
                raise ValueError(f"Search path does not exist: {search_path}")
        except (OSError, ValueError) as error:
            return SearchObservation.from_text(
                str(error),
                is_error=True,
                query=query,
                search_path=str(action.path),
            )

        needle = query.casefold()
        matches: list[str] = []
        truncated = False

        def add_match(value: str) -> bool:
            nonlocal truncated
            if len(matches) >= action.max_results:
                truncated = True
                return False
            matches.append(value)
            return True

        files: list[Path] = []
        if search_path.is_file():
            files.append(search_path)
        else:
            for directory, directory_names, file_names in os.walk(search_path):
                directory_names[:] = sorted(
                    name
                    for name in directory_names
                    if name.casefold() not in self._SKIPPED_DIRECTORIES
                    and not (Path(directory) / name).is_symlink()
                )
                files.extend(
                    Path(directory) / name
                    for name in sorted(file_names)
                    if name.casefold() not in self._SENSITIVE_FILES
                )

        for file_path in files:
            if self._is_binary_file(file_path):
                continue
            try:
                relative = file_path.relative_to(search_path)
            except ValueError:
                relative = Path(file_path.name)
            display_path = str(relative)
            if needle in display_path.casefold() and not add_match(display_path):
                break
            try:
                if file_path.is_symlink() or file_path.stat().st_size > self._MAX_FILE_SIZE:
                    continue
                with file_path.open("r", encoding="utf-8", errors="ignore") as file:
                    for line_number, line in enumerate(file, start=1):
                        if needle in line.casefold():
                            excerpt = line.strip()[:240]
                            if not add_match(
                                f"{display_path}:{line_number}: {excerpt}"
                            ):
                                break
            except OSError:
                continue
            if truncated:
                break

        if matches:
            text = "Search matches:\n" + "\n".join(matches)
        else:
            text = f'No matches found for "{query}" in {search_path}.'
        if truncated:
            text += f"\nResults limited to {action.max_results}; narrow the query if needed."
        return SearchObservation.from_text(
            text,
            query=query,
            search_path=str(search_path),
            matches=matches,
            truncated=truncated,
        )


class SearchTool(ToolDefinition[SearchAction, SearchObservation]):
    """Expose a generic search name used by several coding models."""

    def declared_resources(self, action: Action) -> DeclaredResources:  # noqa: ARG002
        return DeclaredResources(keys=(), declared=True)

    @classmethod
    def create(
        cls,
        conv_state: ConversationState,
        readable_root: str | None = None,
    ) -> Sequence[SearchTool]:
        workspace = conv_state.workspace.working_dir
        roots = workspace if readable_root is None else f"{workspace}, {readable_root}"
        description = (
            "Read-only literal search across file names and text content. "
            "Use it to locate files or occurrences of text. Regex is not supported. "
            f"Allowed roots: {roots}."
        )
        return [
            cls(
                description=description,
                action_type=SearchAction,
                observation_type=SearchObservation,
                annotations=ToolAnnotations(
                    title="search",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
                executor=SearchExecutor(workspace, readable_root),
            )
        ]


register_tool(SearchTool.name, SearchTool)
