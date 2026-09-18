from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bank_agent.search_tool import SearchAction, SearchTool


class SearchToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.workspace = self.root / "workspace"
        self.application = self.root / "application"
        self.outside = self.root / "outside"
        self.workspace.mkdir()
        self.application.mkdir()
        self.outside.mkdir()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def create_tool(self):
        state = SimpleNamespace(
            workspace=SimpleNamespace(working_dir=str(self.workspace))
        )
        return SearchTool.create(state, readable_root=str(self.application))[0]

    def test_search_finds_file_names_and_content_in_an_allowed_root(self) -> None:
        skill_file = self.application / "builtin_skills" / "skill-creator" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("Create reusable harness skills", encoding="utf-8")
        tool = self.create_tool()

        observation = tool.executor(
            SearchAction(
                query="SKILL.md",
                path=str(self.application),
                max_results=20,
            )
        )

        self.assertFalse(observation.is_error)
        self.assertIn("builtin_skills\\skill-creator\\SKILL.md", observation.matches)

    def test_search_rejects_paths_outside_workspace_and_application(self) -> None:
        tool = self.create_tool()

        observation = tool.executor(
            SearchAction(query="secret", path=str(self.outside))
        )

        self.assertTrue(observation.is_error)
        self.assertIn("outside the allowed roots", observation.text)

    def test_application_search_excludes_private_runtime_data(self) -> None:
        session_file = self.application / "sessions" / "one" / "event.json"
        session_file.parent.mkdir(parents=True)
        session_file.write_text("private-marker", encoding="utf-8")
        source_file = self.application / "bank_agent" / "main.py"
        source_file.parent.mkdir()
        source_file.write_text("public-marker", encoding="utf-8")
        tool = self.create_tool()

        broad_search = tool.executor(
            SearchAction(query="marker", path=str(self.application))
        )
        direct_private_search = tool.executor(
            SearchAction(query="private", path=str(session_file.parent))
        )

        self.assertFalse(broad_search.is_error)
        self.assertIn("bank_agent\\main.py:1: public-marker", broad_search.matches)
        self.assertNotIn("private-marker", broad_search.text)
        self.assertTrue(direct_private_search.is_error)
        self.assertIn("private Bank Agent runtime data", direct_private_search.text)

    def test_search_excludes_build_artifacts_and_binary_content(self) -> None:
        source_file = self.workspace / "src" / "Example.java"
        source_file.parent.mkdir()
        source_file.write_text(
            "class Example { String value = 'IndexRate'; }", encoding="utf-8"
        )
        compiled_file = self.workspace / "target" / "Example.class"
        compiled_file.parent.mkdir()
        compiled_file.write_bytes(b"\xca\xfe\xba\xbe\x00\x00IndexRate\x01\x02\x03")
        binary_file = self.workspace / "artifact.bin"
        binary_file.write_bytes(b"\x01\x02\x03IndexRate\x04\x05\x06")
        tool = self.create_tool()

        observation = tool.executor(SearchAction(query="IndexRate", path="."))

        self.assertFalse(observation.is_error)
        self.assertIn(
            "src\\Example.java:1: class Example { String value = 'IndexRate'; }",
            observation.matches,
        )
        self.assertNotIn("target", observation.text)
        self.assertNotIn("artifact.bin", observation.text)


if __name__ == "__main__":
    unittest.main()
