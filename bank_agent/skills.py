"""Discovery and validation for Bank Agent's local skill catalog."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from openhands.sdk.skills import Skill


_EXECUTABLE_INLINE_COMMAND = re.compile(r"(?<!\\)!`[^`\r\n]+`")
# Preserve the implementation while the authoring quality is evaluated with a
# stronger model. The catalog and manually installed skills remain available.
GUIDED_SKILL_CREATION_ENABLED = False


@dataclass(frozen=True)
class SkillLoadIssue:
    """One invalid skill that was skipped without blocking the harness."""

    name: str
    message: str


@dataclass(frozen=True)
class HarnessSkillCatalog:
    """The valid and invalid entries discovered in the harness skill directory."""

    skills: tuple[Skill, ...]
    issues: tuple[SkillLoadIssue, ...]


@dataclass(frozen=True)
class SkillInstallPlan:
    """One validated draft ready to be copied into the live catalog."""

    name: str
    description: str
    source: Path
    destination: Path


def load_harness_skills(skills_dir: Path) -> HarnessSkillCatalog:
    """Load strict AgentSkills directories without executing or starting anything."""
    if not skills_dir.exists():
        try:
            skills_dir.mkdir(parents=True)
        except OSError as error:
            return HarnessSkillCatalog(
                (),
                (
                    SkillLoadIssue(
                        skills_dir.name,
                        f"Could not create skills directory: {error}",
                    ),
                ),
            )
    if not skills_dir.is_dir():
        return HarnessSkillCatalog(
            (),
            (
                SkillLoadIssue(
                    skills_dir.name, "The configured skills path is not a directory."
                ),
            ),
        )

    skills_root = skills_dir.resolve()
    loaded: list[Skill] = []
    issues: list[SkillLoadIssue] = []
    for entry in sorted(skills_dir.iterdir(), key=lambda path: path.name.casefold()):
        if not entry.is_dir():
            continue
        skill_path = entry / "SKILL.md"
        if not skill_path.is_file():
            issues.append(SkillLoadIssue(entry.name, "Missing SKILL.md."))
            continue
        try:
            entry.resolve().relative_to(skills_root)
            skill_path.resolve().relative_to(skills_root)
        except (OSError, ValueError):
            issues.append(
                SkillLoadIssue(entry.name, "Skill paths must stay inside the skills directory.")
            )
            continue
        if (entry / ".mcp.json").exists():
            issues.append(
                SkillLoadIssue(
                    entry.name,
                    "MCP configuration is not supported by Bank Agent skills yet.",
                )
            )
            continue
        try:
            skill = Skill.load(skill_path, skills_dir)
        except Exception as error:
            issues.append(SkillLoadIssue(entry.name, str(error)))
            continue
        if _EXECUTABLE_INLINE_COMMAND.search(skill.content):
            issues.append(
                SkillLoadIssue(
                    entry.name,
                    "Inline command execution is disabled. Describe commands normally so "
                    "the agent runs them through the approval policy.",
                )
            )
            continue
        loaded.append(skill)

    return HarnessSkillCatalog(tuple(loaded), tuple(issues))


def prepare_skill_install(draft_workspace: Path, skills_dir: Path) -> SkillInstallPlan:
    """Validate that a draft workspace contains exactly one installable skill."""
    draft_workspace = draft_workspace.resolve()
    catalog = load_harness_skills(draft_workspace)
    if catalog.issues:
        details = "; ".join(
            f"{issue.name}: {issue.message}" for issue in catalog.issues
        )
        raise ValueError(f"The skill draft is not valid: {details}")
    if len(catalog.skills) != 1:
        raise ValueError(
            "The draft workspace must contain exactly one skill folder with a SKILL.md."
        )

    skill = catalog.skills[0]
    source_path = draft_workspace / skill.name
    if source_path.is_symlink() or any(
        path.is_symlink() for path in source_path.rglob("*")
    ):
        raise ValueError("Skill drafts cannot contain symbolic links.")
    source = source_path.resolve()
    try:
        source.relative_to(draft_workspace)
    except ValueError as error:
        raise ValueError("The skill draft must stay inside its draft workspace.") from error
    skills_dir.mkdir(parents=True, exist_ok=True)
    existing_names = {path.name.casefold() for path in skills_dir.iterdir()}
    if skill.name.casefold() in existing_names:
        raise ValueError(
            f'A skill named "{skill.name}" already exists. Choose a new name.'
        )

    return SkillInstallPlan(
        name=skill.name,
        description=skill.description,
        source=source,
        destination=skills_dir / skill.name,
    )


def install_skill_draft(plan: SkillInstallPlan) -> Path:
    """Copy one validated draft without replacing an existing installed skill."""
    try:
        shutil.copytree(plan.source, plan.destination)
    except FileExistsError as error:
        raise ValueError(
            f'A skill named "{plan.name}" already exists. Nothing was overwritten.'
        ) from error
    return plan.destination
