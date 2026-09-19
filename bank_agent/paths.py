"""Canonical filesystem locations owned by the Bank Agent installation."""

from pathlib import Path


CONFIG_PATH = Path(__file__).resolve().parents[1] / ".env"
APP_ROOT = CONFIG_PATH.parent
AGENT_WORKSPACE = APP_ROOT / "agent-workspace"
PROJECTS_WORKSPACE = AGENT_WORKSPACE / "projects"
SCRATCH_WORKSPACE = AGENT_WORKSPACE / "scratch"
COMMAND_ALLOWLIST_PATH = APP_ROOT / "policy" / "allowlist.json"
SESSIONS_PATH = APP_ROOT / "sessions"
SESSION_LOGS_PATH = APP_ROOT / "session-logs"
GENERAL_INSTRUCTIONS_PATH = AGENT_WORKSPACE / "AGENTS.md"
SKILLS_PATH = AGENT_WORKSPACE / "skills"
SKILL_DRAFTS_PATH = AGENT_WORKSPACE / "skill-drafts"
BUILTIN_SKILLS_PATH = Path(__file__).resolve().parent / "builtin_skills"
HELP_PATH = APP_ROOT / "HELP.txt"

