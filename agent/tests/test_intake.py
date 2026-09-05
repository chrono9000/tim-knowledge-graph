import json
import io
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from agent.intake import IntakeConfig, approve, import_export, load_staging, main, preview, publish, reject


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXED_TIME = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


class IntakeWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.public = self.root / "data" / "graph.json"
        self.public.parent.mkdir(parents=True)
        self.public.write_bytes((REPOSITORY_ROOT / "data" / "graph.json").read_bytes())
        self.private = self.root / "data" / "private" / "master-graph.json"
        self.staging = self.root / "data" / "staging" / "proposals.json"
        self.logs = self.root / "logs"
        self.config = IntakeConfig(self.public, self.private, self.staging, self.logs, clock=lambda: FIXED_TIME)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def export(self, name: str = "synthetic-conversation.json", project: str = "Project Aurora") -> Path:
        path = self.root / name
        payload = [{
            "title": "Synthetic planning conversation",
            "create_time": 1788537600,
            "update_time": 1788537660,
            "mapping": {
                "one": {"message": {"author": {"role": "user"}, "content": {"parts": [f"Person: Casey Example\nProject: {project}\nRelationship: Casey Example | leads | {project}"]}}}
            },
        }]
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def graph(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def test_import_stages_private_proposals_without_touching_graphs(self) -> None:
        before = self.public.read_bytes()
        result = import_export(self.export(), self.config)

        self.assertFalse(result.details["duplicateImport"])
        self.assertEqual(self.public.read_bytes(), before)
        self.assertFalse(self.private.exists())
        staged = load_staging(self.staging)
        self.assertEqual(len(staged["batches"]), 1)
        batch = staged["batches"][0]
        self.assertTrue(batch["private"])
        self.assertEqual(batch["sourceFilename"], "synthetic-conversation.json")
        self.assertTrue(batch["sourceTimestamp"].endswith("Z"))
        self.assertTrue(all(item["status"] in {"pending", "needs-review"} for item in batch["proposals"]))
        for item in batch["proposals"]:
            self.assertIn(item["recordType"], {"source", "node", "edge"})
            self.assertEqual(item["provenance"]["sourceFilename"], batch["sourceFilename"])
            for key in ("sourceTimestamp", "confidence", "authorityLevel", "firstSeen", "lastSeen"):
                self.assertIn(key, item["provenance"])

    def test_preview_and_duplicate_import_are_read_only(self) -> None:
        export = self.export()
        first = import_export(export, self.config)
        staged_before = self.staging.read_bytes()

        shown = preview(self.config, ["pending", "needs-review"])
        duplicate = import_export(export, self.config)

        self.assertEqual(shown["proposalCount"], first.details["proposalCount"])
        self.assertTrue(duplicate.details["duplicateImport"])
        self.assertEqual(self.staging.read_bytes(), staged_before)

    def test_private_approval_never_changes_public_graph(self) -> None:
        before = self.public.read_bytes()
        imported = import_export(self.export(), self.config)

        approved = approve(self.config, "private", batch_id=imported.batch_id)

        self.assertTrue(approved.graph_changed)
        self.assertEqual(self.public.read_bytes(), before)
        private = self.graph(self.private)
        self.assertIn("Project Aurora", {node["label"] for node in private["nodes"]})
        self.assertNotIn("Project Aurora", {node["label"] for node in self.graph(self.public)["nodes"]})
        self.assertTrue(all(item["status"] == "approved-private" for item in load_staging(self.staging)["batches"][0]["proposals"]))

    def test_public_approval_requires_publish_and_sanitizes_source(self) -> None:
        before = self.public.read_bytes()
        imported = import_export(self.export(project="Project Helios"), self.config)
        approve(self.config, "public", batch_id=imported.batch_id)
        self.assertEqual(self.public.read_bytes(), before)

        result = publish(self.config)

        self.assertTrue(result.graph_changed)
        public = self.graph(self.public)
        self.assertIn("Project Helios", {node["label"] for node in public["nodes"]})
        source = next(item for item in public["sources"] if item["title"] == "Approved private source")
        self.assertNotIn("filename", source)
        self.assertNotIn("contentHash", source)
        self.assertTrue(source["location"].startswith("private-source:"))
        self.assertTrue(all(item.get("publishedAt") for item in load_staging(self.staging)["batches"][0]["proposals"]))

    def test_private_approval_can_be_promoted_to_public(self) -> None:
        imported = import_export(self.export(project="Project Promoted"), self.config)
        approve(self.config, "private", batch_id=imported.batch_id)

        promoted = approve(self.config, "public", batch_id=imported.batch_id)
        published = publish(self.config)

        self.assertEqual(promoted.proposals_changed, len(imported.proposal_ids))
        self.assertTrue(published.graph_changed)
        self.assertIn("Project Promoted", {node["label"] for node in self.graph(self.public)["nodes"]})

    def test_rejection_changes_no_graph(self) -> None:
        before = self.public.read_bytes()
        imported = import_export(self.export(), self.config)

        result = reject(self.config, batch_id=imported.batch_id)

        self.assertEqual(result.proposals_changed, len(imported.proposal_ids))
        self.assertEqual(self.public.read_bytes(), before)
        self.assertFalse(self.private.exists())
        self.assertTrue(all(item["status"] == "rejected" for item in load_staging(self.staging)["batches"][0]["proposals"]))

    def test_sensitive_low_confidence_duplicate_and_contradiction_need_review(self) -> None:
        payload = {
            "authority": "tertiary",
            "confidence": 0.5,
            "entities": [{"label": "Knowledge Hub", "description": "Knowledge Hub is not available", "confidence": 0.5}, {"label": "owner@example.test", "confidence": 0.5}],
        }
        path = self.root / "synthetic-records.json"
        path.write_text(json.dumps(payload), encoding="utf-8")

        import_export(path, self.config)

        proposals = preview(self.config)["proposals"]
        duplicate = next(item for item in proposals if item["targetId"] == "knowledge-hub")
        sensitive = next(item for item in proposals if (item.get("proposedRecord") or {}).get("label") == "owner@example.test")
        self.assertEqual(duplicate["kind"], "contradiction")
        self.assertTrue({"possible-duplicate", "authority-conflict", "possible-contradiction", "low-confidence"} <= set(duplicate["reviewReasons"]))
        self.assertEqual(sensitive["status"], "needs-review")
        self.assertTrue({"sensitive-information", "low-confidence"} <= set(sensitive["reviewReasons"]))

    def test_zip_project_export_is_read_in_memory(self) -> None:
        archive = self.root / "synthetic-project.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("project/notes.md", "Project: Project Zephyr\nDecision: Project Zephyr will use a staged launch.\n")
            output.writestr("project/ignored.html", "<p>not imported</p>")

        imported = import_export(archive, self.config)

        self.assertGreater(imported.details["proposalCount"], 1)
        labels = {(item.get("proposedRecord") or {}).get("label") for item in preview(self.config)["proposals"]}
        self.assertIn("Project Zephyr", labels)

    def test_publishing_nothing_is_a_no_op(self) -> None:
        before = self.public.read_bytes()
        result = publish(self.config)
        self.assertFalse(result.graph_changed)
        self.assertEqual(result.details["publishedCount"], 0)
        self.assertEqual(self.public.read_bytes(), before)

    def test_malformed_input_cannot_change_graph_or_staging(self) -> None:
        malformed = self.root / "malformed.json"
        malformed.write_text("{not valid json", encoding="utf-8")
        public_before = self.public.read_bytes()

        with self.assertRaises(json.JSONDecodeError):
            import_export(malformed, self.config)

        self.assertEqual(self.public.read_bytes(), public_before)
        self.assertFalse(self.private.exists())
        self.assertFalse(self.staging.exists())

    def test_command_line_complete_public_workflow(self) -> None:
        export = self.export(project="Project CLI")
        common = [
            "--public-graph", str(self.public),
            "--private-graph", str(self.private),
            "--staging", str(self.staging),
            "--log-dir", str(self.logs),
        ]
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([*common, "import", str(export)]), 0)
        batch_id = json.loads(output.getvalue())["batchId"]

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([*common, "preview"]), 0)
        self.assertGreater(json.loads(output.getvalue())["proposalCount"], 0)

        with redirect_stdout(io.StringIO()):
            self.assertEqual(main([*common, "approve-public", "--batch", batch_id]), 0)
            self.assertEqual(main([*common, "publish"]), 0)

        self.assertIn("Project CLI", {node["label"] for node in self.graph(self.public)["nodes"]})


if __name__ == "__main__":
    unittest.main()
