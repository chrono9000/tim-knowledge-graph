import subprocess
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class RepositorySecurityTests(unittest.TestCase):
    def ignored(self, relative_path: str) -> bool:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "--quiet", relative_path],
            cwd=REPOSITORY_ROOT,
            check=False,
        )
        return result.returncode == 0

    def test_private_runtime_files_are_ignored(self) -> None:
        private_paths = (
            "data/raw/private-notes.txt",
            "data/raw/nested/private-record.md",
            "data/raw/source.json",
            "data/raw/conversation-export.json",
            "data/processed/ingestion-manifest.json",
            "data/private/master-graph.json",
            "data/staging/proposals.json",
            "logs/ingestion-20990101T000000Z.jsonl",
            "logs/intake-20990101T000000Z.jsonl",
            "agent/working-copy.tmp",
            "archive/chat_export_private.json",
            "archive/project-export-private.zip",
        )
        for path in private_paths:
            with self.subTest(path=path):
                self.assertTrue(self.ignored(path), f"Expected Git to ignore {path}")

    def test_placeholder_files_may_be_tracked(self) -> None:
        placeholders = (
            "data/raw/README.md",
            "data/raw/.gitkeep",
            "data/processed/README.md",
            "data/processed/.gitkeep",
            "data/private/README.md",
            "data/private/.gitkeep",
            "data/staging/README.md",
            "data/staging/.gitkeep",
            "logs/README.md",
            "logs/.gitkeep",
        )
        for path in placeholders:
            with self.subTest(path=path):
                self.assertFalse(self.ignored(path), f"Expected placeholder to remain trackable: {path}")

    def test_pages_artifact_is_an_explicit_allowlist(self) -> None:
        workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
        self.assertIn("cp index.html app.js styles.css _site/", workflow)
        self.assertIn("cp data/graph.json _site/data/", workflow)
        self.assertIn("cp schemas/graph.schema.json _site/schemas/", workflow)
        self.assertIn("path: _site", workflow)
        self.assertNotIn("path: .\n", workflow)
        self.assertNotIn("cp -r .", workflow)
        self.assertNotIn("data/private", workflow)
        self.assertNotIn("data/staging", workflow)
        self.assertNotIn("agent/", workflow)
        self.assertNotIn("logs/", workflow)


if __name__ == "__main__":
    unittest.main()
