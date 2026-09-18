from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from openhands.sdk import AgentContext


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bank_agent.skills import (
    GUIDED_SKILL_CREATION_ENABLED,
    install_skill_draft,
    load_harness_skills,
    prepare_skill_install,
)
from bank_agent.sessions import show_settings_menu, show_skills_menu
from bank_agent.main import confirm_and_install_skill


VALID_SKILL = """---
name: service-map
description: Explains service ownership and request paths when tracing a request.
---

# Service map

Use the maintained service relationship data when tracing requests.
"""


class HarnessSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.skills_dir = Path(self.temporary_directory.name) / "skills"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_skill(self, name: str, content: str = VALID_SKILL) -> Path:
        skill_dir = self.skills_dir / name
        skill_dir.mkdir(parents=True)
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(content, encoding="utf-8")
        return skill_path

    def test_valid_agentskill_is_loaded_for_progressive_disclosure(self) -> None:
        self.write_skill("service-map")

        catalog = load_harness_skills(self.skills_dir)

        self.assertEqual([skill.name for skill in catalog.skills], ["service-map"])
        self.assertEqual(catalog.issues, ())
        self.assertTrue(catalog.skills[0].is_agentskills_format)

        dynamic_context = AgentContext(
            skills=list(catalog.skills), current_datetime=None
        ).get_system_message_suffix()
        self.assertIn("<name>service-map</name>", dynamic_context)
        self.assertIn("Explains service ownership", dynamic_context)

    def test_missing_skills_directory_is_created(self) -> None:
        catalog = load_harness_skills(self.skills_dir)

        self.assertTrue(self.skills_dir.is_dir())
        self.assertEqual(catalog.skills, ())
        self.assertEqual(catalog.issues, ())

    def test_invalid_skill_does_not_prevent_other_skills_from_loading(self) -> None:
        self.write_skill("service-map")
        self.write_skill("broken-skill", VALID_SKILL.replace("service-map", "wrong-name"))

        catalog = load_harness_skills(self.skills_dir)

        self.assertEqual([skill.name for skill in catalog.skills], ["service-map"])
        self.assertEqual([issue.name for issue in catalog.issues], ["broken-skill"])

    def test_inline_commands_and_mcp_configuration_are_rejected(self) -> None:
        self.write_skill(
            "inline-command",
            VALID_SKILL.replace("service-map", "inline-command") + "\n!`whoami`\n",
        )
        mcp_skill = self.write_skill(
            "mcp-skill", VALID_SKILL.replace("service-map", "mcp-skill")
        )
        (mcp_skill.parent / ".mcp.json").write_text("{}", encoding="utf-8")

        catalog = load_harness_skills(self.skills_dir)

        self.assertEqual(catalog.skills, ())
        self.assertEqual(
            [issue.name for issue in catalog.issues],
            ["inline-command", "mcp-skill"],
        )

    def test_one_valid_draft_can_be_installed_without_overwriting(self) -> None:
        draft = Path(self.temporary_directory.name) / "draft"
        installed = Path(self.temporary_directory.name) / "installed"
        skill_dir = draft / "service-map"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(VALID_SKILL, encoding="utf-8")

        plan = prepare_skill_install(draft, installed)
        destination = install_skill_draft(plan)

        self.assertEqual(plan.name, "service-map")
        self.assertEqual(destination, installed / "service-map")
        self.assertEqual(
            (destination / "SKILL.md").read_text(encoding="utf-8"), VALID_SKILL
        )

    def test_install_confirmation_keeps_a_declined_draft_uninstalled(self) -> None:
        draft = Path(self.temporary_directory.name) / "draft"
        installed = Path(self.temporary_directory.name) / "installed"
        skill_dir = draft / "service-map"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(VALID_SKILL, encoding="utf-8")

        with patch("builtins.input", return_value="n"):
            result = confirm_and_install_skill(draft, installed)

        self.assertFalse(result)
        self.assertFalse((installed / "service-map").exists())

    def test_install_rejects_multiple_drafts_and_case_insensitive_duplicates(self) -> None:
        draft = Path(self.temporary_directory.name) / "draft"
        installed = Path(self.temporary_directory.name) / "installed"
        for name in ("service-map", "other-skill"):
            skill_dir = draft / name
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                VALID_SKILL.replace("service-map", name), encoding="utf-8"
            )

        with self.assertRaisesRegex(ValueError, "exactly one skill folder"):
            prepare_skill_install(draft, installed)

        (draft / "other-skill").rename(
            Path(self.temporary_directory.name) / "removed-other-skill"
        )
        (installed / "SERVICE-MAP").mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "already exists"):
            prepare_skill_install(draft, installed)

    def test_skills_browser_lists_valid_and_invalid_skills(self) -> None:
        self.write_skill("service-map")
        (self.skills_dir / "broken-skill").mkdir()
        output = StringIO()

        with (
            patch("bank_agent.sessions.os.system"),
            patch("msvcrt.getwch", return_value="\x1b"),
            redirect_stdout(output),
        ):
            show_skills_menu(self.skills_dir)

        rendered = output.getvalue()
        self.assertIn("Guided skill creation is temporarily unavailable", rendered)
        self.assertNotIn("Create new skill", rendered)
        self.assertIn("service-map", rendered)
        self.assertIn("valid", rendered)
        self.assertIn("broken-skill", rendered)
        self.assertIn("invalid", rendered)

    def test_skills_browser_does_not_offer_shelved_creation(self) -> None:
        with (
            patch("bank_agent.sessions.os.system"),
            patch("msvcrt.getwch", return_value="\x1b"),
        ):
            result = show_skills_menu(self.skills_dir)

        self.assertIsNone(result)
        self.assertFalse(GUIDED_SKILL_CREATION_ENABLED)

    def test_settings_opens_the_skills_browser(self) -> None:
        with (
            patch("bank_agent.sessions.os.system"),
            patch(
                "bank_agent.sessions.show_skills_menu", return_value="create"
            ) as show_skills,
            patch("msvcrt.getwch", side_effect=["\xe0", "P", "\r", "\x1b"]),
        ):
            result = show_settings_menu(
                Path(self.temporary_directory.name) / "AGENTS.md",
                self.skills_dir,
            )

        show_skills.assert_called_once_with(self.skills_dir)
        self.assertIsNone(result)

    def test_built_in_skill_creator_is_a_valid_standard_skill(self) -> None:
        builtins = Path(__file__).resolve().parents[1] / "bank_agent" / "builtin_skills"

        catalog = load_harness_skills(builtins)

        self.assertEqual([skill.name for skill in catalog.skills], ["skill-creator"])
        self.assertEqual(catalog.issues, ())
        self.assertTrue(catalog.skills[0].is_agentskills_format)
