import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

from agent.chatgpt_export import ExportFilters, approve_access_design, process_export, validate_export, validate_summary
from agent.intake import IntakeConfig, approve, load_staging, publish, reject, review_list
from agent.private_view import make_server


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXED_TIME = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)


class ChatGPTExportWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.public = self.root / "data" / "graph.json"
        self.public.parent.mkdir(parents=True)
        self.public.write_bytes((REPOSITORY_ROOT / "data" / "graph.json").read_bytes())
        self.config = IntakeConfig(
            self.public,
            self.root / "data" / "private" / "master-graph.json",
            self.root / "data" / "staging" / "proposals.json",
            self.root / "logs",
            clock=lambda: FIXED_TIME,
            harness_path=REPOSITORY_ROOT / "agent" / "harness.json",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def conversation(self, identifier: str, title: str, project: str, timestamp: int, text: str) -> dict:
        return {
            "id": identifier,
            "title": title,
            "project": {"id": project.casefold().replace(" ", "-"), "name": project},
            "create_time": timestamp,
            "update_time": timestamp,
            "mapping": {
                "message": {"message": {"author": {"role": "user"}, "content": {"parts": [text]}}}
            },
        }

    def export(self) -> Path:
        path = self.root / "data" / "raw" / "conversations.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([
            self.conversation("c-1", "Alpha planning", "Synthetic Lab", 1788739200, "Project: Project Lantern\nDecision: Use the blue synthetic route.\nRelationship: Project Lantern | uses | Blue Route\nThis ordinary transcript sentence must never become a node."),
            self.conversation("c-2", "Beta review", "Other Lab", 1788825600, "Person: private@example.test\nQuestion: Is the synthetic launch ready?"),
        ]), encoding="utf-8")
        return path

    def test_validate_and_filter_without_writes(self) -> None:
        export = self.export()
        before = self.public.read_bytes()
        result = process_export(export, self.config, ExportFilters(projects=("Synthetic Lab",), title="Alpha", from_date=date(2026, 9, 6), to_date=date(2026, 9, 8)))
        summary = result.details["summary"]
        self.assertEqual((summary["conversations"], summary["projects"]), (1, 1))
        self.assertGreater(summary["proposedNodes"], 0)
        self.assertGreater(summary["proposedEdges"], 0)
        self.assertFalse(self.config.staging_path.exists())
        self.assertFalse(self.config.private_graph_path.exists())
        self.assertFalse(self.config.log_dir.exists())
        self.assertEqual(before, self.public.read_bytes())
        self.assertNotIn("This ordinary transcript sentence must never become a node", json.dumps(result.as_dict()))

    def test_stage_requires_access_approval_and_remains_resumable(self) -> None:
        export = self.export()
        with self.assertRaisesRegex(ValueError, "Approve the private-access design"):
            process_export(export, self.config, ExportFilters(), stage=True)
        approve_access_design(self.config, "local-only")
        first = process_export(export, self.config, ExportFilters(conversations=("c-1",)), stage=True)
        staged = self.config.staging_path.read_bytes()
        self.assertFalse(self.config.private_graph_path.exists())
        self.assertNotIn(b"This ordinary transcript sentence must never become a node", staged)
        second = process_export(export, self.config, ExportFilters(conversations=("c-1",)), stage=True)
        self.assertFalse(first.details["duplicateImport"])
        self.assertTrue(second.details["duplicateImport"])
        self.assertEqual(staged, self.config.staging_path.read_bytes())

    def test_private_and_public_are_separate_approvals(self) -> None:
        before = self.public.read_bytes()
        approve_access_design(self.config, "local-only")
        staged = process_export(self.export(), self.config, ExportFilters(conversations=("c-1",)), stage=True)
        approve(self.config, "private", batch_id=staged.batch_id)
        self.assertEqual(before, self.public.read_bytes())
        self.assertIn("Project Lantern", {node["label"] for node in json.loads(self.config.private_graph_path.read_text(encoding="utf-8"))["nodes"]})
        self.assertEqual(publish(self.config).details["publishedCount"], 0)
        approve(self.config, "public", batch_id=staged.batch_id)
        self.assertEqual(before, self.public.read_bytes())
        self.assertTrue(publish(self.config).graph_changed)

    def test_numbered_review_supports_individual_rejection(self) -> None:
        approve_access_design(self.config, "local-only")
        process_export(self.export(), self.config, ExportFilters(conversations=("c-1",)), stage=True)
        items = review_list(self.config)["items"]
        self.assertEqual([item["number"] for item in items], list(range(1, len(items) + 1)))
        rejected = reject(self.config, [items[-1]["proposalId"]])
        self.assertEqual(rejected.proposals_changed, 1)

    def test_sensitive_and_malformed_counts_and_zip_attachment_is_ignored(self) -> None:
        conversations = json.loads(self.export().read_text(encoding="utf-8"))
        conversations.append({"id": "broken", "title": "Malformed"})
        archive = self.root / "chat-export.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("export/conversations.json", json.dumps(conversations))
            output.writestr("export/attachments/private.txt", "Project: Must Never Be Read")
        validated = validate_summary(archive)
        self.assertEqual(validated["failures"], 1)
        result = process_export(archive, self.config, ExportFilters(conversations=("c-2",)))
        self.assertGreaterEqual(result.details["summary"]["sensitiveRecords"], 1)
        self.assertNotIn("Must Never Be Read", json.dumps(result.as_dict()))

    def test_malformed_export_and_interruption_cannot_change_state(self) -> None:
        malformed = self.root / "malformed.json"
        malformed.write_text("{", encoding="utf-8")
        before = self.public.read_bytes()
        with self.assertRaisesRegex(ValueError, "Malformed"):
            validate_export(malformed)
        approve_access_design(self.config, "local-only")
        with mock.patch("agent.intake.atomic_json_write", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                process_export(self.export(), self.config, ExportFilters(), stage=True)
        self.assertFalse(self.config.staging_path.exists())
        self.assertFalse(self.config.private_graph_path.exists())
        self.assertEqual(before, self.public.read_bytes())

    def test_private_viewer_serves_only_allowlisted_content(self) -> None:
        server = make_server(self.config, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            graph = json.loads(urllib.request.urlopen(base + "/data/graph.json").read())
            self.assertEqual(len(graph["nodes"]), 28)
            for private_path in ("/data/raw/conversations.json", "/data/staging/proposals.json", "/logs/intake.jsonl", "/agent/intake.py"):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(base + private_path)
                self.assertEqual(caught.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
