import json
import os
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from agent.ingest import (
    IngestionConfig,
    assert_append_only,
    atomic_json_write,
    canonical_text,
    run_ingestion,
    validate_graph,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXED_TIME = datetime(2026, 9, 4, 20, 0, tzinfo=timezone.utc)


class IngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.raw = self.root / "data" / "raw"
        self.raw.mkdir(parents=True)
        self.graph_path = self.root / "data" / "graph.json"
        self.graph_path.write_text((REPOSITORY_ROOT / "data" / "graph.json").read_text(encoding="utf-8"), encoding="utf-8")
        self.manifest = self.root / "data" / "processed" / "ingestion-manifest.json"
        self.logs = self.root / "logs"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def config(self, *, dry_run: bool = False, clock=lambda: FIXED_TIME) -> IngestionConfig:
        return IngestionConfig(self.raw, self.graph_path, self.manifest, self.logs, dry_run=dry_run, clock=clock)

    def graph(self) -> dict:
        return json.loads(self.graph_path.read_text(encoding="utf-8"))

    @staticmethod
    def by_label(graph: dict, label: str) -> dict:
        key = canonical_text(label)
        return next(node for node in graph["nodes"] if canonical_text(node["label"]) == key)

    def test_markdown_extracts_types_relationships_provenance_and_logs(self) -> None:
        source = self.raw / "official-project-notes.md"
        source.write_text(
            """---
authority: primary
confidence: 0.87
source_timestamp: 2026-08-31T14:15:00-04:00
---
Person: Jane Doe
Project: Project Atlas
System: Billing Platform
Decision: Project Atlas will launch in October.
Policy: Production access must be reviewed quarterly.
Recommendation: The team should stage the migration.
Assumption: Billing Platform is likely ready.
Open issue: Who owns the migration checklist?
Historical note: The pilot started in June.
Relationship: Jane Doe | leads | Project Atlas
Project Atlas -> depends on -> Billing Platform
""",
            encoding="utf-8",
        )
        original = self.graph()

        result = run_ingestion(self.config())

        self.assertEqual(result.files_processed, 1)
        self.assertGreaterEqual(result.nodes_added, 9)
        self.assertEqual(result.edges_added, 2)
        graph = self.graph()
        self.assertTrue({node["id"] for node in original["nodes"]} <= {node["id"] for node in graph["nodes"]})
        self.assertTrue({edge["id"] for edge in original["edges"]} <= {edge["id"] for edge in graph["edges"]})

        jane = self.by_label(graph, "Jane Doe")
        decision = self.by_label(graph, "Project Atlas will launch in October")
        policy = self.by_label(graph, "Production access must be reviewed quarterly")
        recommendation = self.by_label(graph, "The team should stage the migration")
        assumption = self.by_label(graph, "Billing Platform is likely ready")
        question = self.by_label(graph, "Who owns the migration checklist?")
        historical = self.by_label(graph, "The pilot started in June")
        self.assertEqual(jane["entityType"], "person")
        self.assertEqual(decision["statementType"], "decision")
        self.assertEqual(policy["statementType"], "policy")
        self.assertEqual(recommendation["statementType"], "recommendation")
        self.assertEqual(assumption["statementType"], "assumption")
        self.assertEqual(question["statementType"], "unresolved-question")
        self.assertEqual(historical["entityType"], "historical-note")
        self.assertEqual(historical["statementType"], "fact")
        self.assertEqual(jane["timestamps"]["firstSeen"], "2026-08-31T18:15:00Z")
        self.assertEqual(jane["timestamps"]["lastSeen"], "2026-08-31T18:15:00Z")

        source_record = next(item for item in graph["sources"] if item["id"] in jane["sourceIds"])
        self.assertEqual(source_record["filename"], "official-project-notes.md")
        self.assertEqual(source_record["sourceTimestamp"], "2026-08-31T18:15:00Z")
        self.assertEqual(source_record["authorityLevel"], "primary")
        self.assertEqual(source_record["confidence"], 0.87)
        self.assertEqual(len(source_record["contentHash"]), 64)

        leads = [edge for edge in graph["edges"] if edge["relationship"] == "leads"]
        self.assertEqual(len(leads), 1)
        self.assertTrue(leads[0]["directed"])
        actions = [json.loads(line)["action"] for line in Path(result.log_path).read_text(encoding="utf-8").splitlines()]
        for action in ("source-added", "node-added", "edge-added", "file-processed", "run-complete"):
            self.assertIn(action, actions)

    def test_txt_deduplicates_existing_node_and_never_deletes_prior_knowledge(self) -> None:
        source = self.raw / "notes.txt"
        source.write_text("Entity: KNOWLEDGE-HUB\n", encoding="utf-8")
        original = self.graph()

        result = run_ingestion(self.config())

        graph = self.graph()
        self.assertEqual(result.nodes_added, 0)
        self.assertEqual(result.nodes_merged, 1)
        self.assertEqual(len(graph["nodes"]), len(original["nodes"]))
        self.assertEqual(len(graph["edges"]), len(original["edges"]))
        merged = self.by_label(graph, "Knowledge Hub")
        self.assertEqual(merged["label"], "Knowledge Hub")
        self.assertEqual(merged["description"], self.by_label(original, "Knowledge Hub")["description"])
        self.assertEqual(len(merged["sourceIds"]), 2)
        events = [json.loads(line) for line in Path(result.log_path).read_text(encoding="utf-8").splitlines()]
        merge_event = next(event for event in events if event["action"] == "node-merged")
        self.assertIn("sourceIds", merge_event["changedFields"])
        self.assertIn("timestamps", merge_event["changedFields"])

    def test_structured_json_extracts_all_supported_collections(self) -> None:
        source = self.raw / "records.json"
        source.write_text(
            json.dumps(
                {
                    "entities": ["Vendor Alpha"],
                    "people": [{"name": "Alex Smith"}],
                    "projects": ["Project Orion"],
                    "decisions": ["Choose Orion"],
                    "systems": ["Inventory System"],
                    "policies": ["Quarterly access review"],
                    "events": ["Orion kickoff"],
                    "openIssues": ["Select launch date"],
                    "historicalNotes": ["The pilot preceded Orion"],
                    "relationships": [{"source": "Alex Smith", "target": "Project Orion", "relationship": "owns"}],
                }
            ),
            encoding="utf-8",
        )
        source_time = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc).timestamp()
        os.utime(source, (source_time, source_time))

        result = run_ingestion(self.config())

        graph = self.graph()
        self.assertEqual(result.files_processed, 1)
        self.assertEqual(self.by_label(graph, "Vendor Alpha")["entityType"], "entity")
        self.assertEqual(self.by_label(graph, "Alex Smith")["entityType"], "person")
        self.assertEqual(self.by_label(graph, "Project Orion")["entityType"], "project")
        self.assertEqual(self.by_label(graph, "Choose Orion")["entityType"], "decision")
        self.assertEqual(self.by_label(graph, "Inventory System")["entityType"], "system")
        self.assertEqual(self.by_label(graph, "Quarterly access review")["entityType"], "policy")
        self.assertEqual(self.by_label(graph, "Orion kickoff")["entityType"], "event")
        self.assertEqual(self.by_label(graph, "Select launch date")["entityType"], "open-issue")
        self.assertEqual(self.by_label(graph, "The pilot preceded Orion")["entityType"], "historical-note")
        self.assertEqual(len([edge for edge in graph["edges"] if edge["relationship"] == "owns"]), 1)
        source_record = next(item for item in graph["sources"] if item.get("filename") == "records.json")
        self.assertEqual(source_record["sourceTimestamp"], "2026-08-30T12:00:00Z")

    def test_conversation_export_reads_role_content_messages(self) -> None:
        export = {
            "title": "Project conversation",
            "mapping": {
                "message-1": {
                    "message": {
                        "author": {"role": "user"},
                        "content": {"parts": ["Person: Morgan Lee\nProject: Project Nova\nRelationship: Morgan Lee | sponsors | Project Nova"]},
                    }
                }
            },
        }
        (self.raw / "conversation-export.json").write_text(json.dumps(export), encoding="utf-8")

        run_ingestion(self.config())

        graph = self.graph()
        morgan = self.by_label(graph, "Morgan Lee")
        source_record = next(item for item in graph["sources"] if item["id"] in morgan["sourceIds"])
        self.assertEqual(morgan["entityType"], "person")
        self.assertEqual(source_record["type"], "conversation")
        self.assertEqual(source_record["authorityLevel"], "secondary")
        self.assertEqual(len([edge for edge in graph["edges"] if edge["relationship"] == "sponsors"]), 1)

    def test_unchanged_file_is_skipped_by_manifest(self) -> None:
        (self.raw / "facts.txt").write_text("Fact: The migration is complete.\n", encoding="utf-8")
        run_ingestion(self.config())
        graph_after_first = self.graph_path.read_bytes()

        second = run_ingestion(self.config(clock=lambda: datetime(2026, 9, 4, 21, 0, tzinfo=timezone.utc)))

        self.assertEqual(second.files_processed, 0)
        self.assertEqual(second.files_skipped, 1)
        self.assertFalse(second.graph_changed)
        self.assertEqual(self.graph_path.read_bytes(), graph_after_first)

    def test_dry_run_writes_nothing(self) -> None:
        (self.raw / "dry-run.txt").write_text("Project: Project Dry Run\n", encoding="utf-8")
        before = self.graph_path.read_bytes()

        result = run_ingestion(self.config(dry_run=True))

        self.assertTrue(result.graph_changed)
        self.assertEqual(result.nodes_added, 1)
        self.assertEqual(self.graph_path.read_bytes(), before)
        self.assertFalse(self.manifest.exists())
        self.assertFalse(self.logs.exists())

    def test_changed_file_creates_new_source_version(self) -> None:
        source = self.raw / "history.md"
        source.write_text("Historical note: First version.\n", encoding="utf-8")
        run_ingestion(self.config())
        first_graph = self.graph()
        first_sources = {item["id"] for item in first_graph["sources"]}
        source.write_text("Historical note: First version.\nHistorical note: Second version.\n", encoding="utf-8")

        second = run_ingestion(self.config(clock=lambda: datetime(2026, 9, 5, 1, 0, tzinfo=timezone.utc)))

        second_graph = self.graph()
        self.assertEqual(second.files_processed, 1)
        self.assertEqual(second.sources_added, 1)
        self.assertTrue(first_sources < {item["id"] for item in second_graph["sources"]})
        self.assertIsNotNone(self.by_label(second_graph, "Second version"))

    def test_append_only_guard_rejects_removed_knowledge(self) -> None:
        original = self.graph()
        for collection in ("sources", "nodes", "edges"):
            with self.subTest(collection=collection):
                changed = deepcopy(original)
                changed[collection].pop()
                with self.assertRaisesRegex(RuntimeError, collection):
                    assert_append_only(original, changed)

    def test_atomic_write_preserves_existing_graph_when_replace_fails(self) -> None:
        before = self.graph_path.read_bytes()
        changed = self.graph()
        changed["generatedAt"] = "2099-01-01T00:00:00Z"

        with patch("agent.ingest.os.replace", side_effect=OSError("synthetic replace failure")):
            with self.assertRaisesRegex(OSError, "synthetic replace failure"):
                atomic_json_write(self.graph_path, changed)

        self.assertEqual(self.graph_path.read_bytes(), before)
        self.assertEqual(list(self.graph_path.parent.glob(f".{self.graph_path.name}.*.tmp")), [])

    def test_atomic_write_produces_complete_json(self) -> None:
        changed = self.graph()
        changed["generatedAt"] = "2099-01-01T00:00:00Z"

        atomic_json_write(self.graph_path, changed)

        self.assertEqual(self.graph(), changed)

    def test_referential_integrity_rejects_dangling_edge_and_source(self) -> None:
        original = self.graph()
        dangling_edge = deepcopy(original)
        dangling_edge["edges"][0]["target"] = "node-does-not-exist"
        with self.assertRaisesRegex(ValueError, "Unknown endpoint"):
            validate_graph(dangling_edge)

        dangling_source = deepcopy(original)
        dangling_source["nodes"][0]["sourceIds"] = ["source-does-not-exist"]
        with self.assertRaisesRegex(ValueError, "Unknown source reference"):
            validate_graph(dangling_source)

    def test_malformed_input_cannot_change_existing_graph(self) -> None:
        (self.raw / "malformed.json").write_text('{"projects": [', encoding="utf-8")
        before = self.graph_path.read_bytes()

        result = run_ingestion(self.config())

        self.assertEqual(result.files_processed, 0)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(self.graph_path.read_bytes(), before)
        self.assertFalse(self.manifest.exists())
        events = [json.loads(line)["action"] for line in Path(result.log_path).read_text(encoding="utf-8").splitlines()]
        self.assertIn("file-error", events)
        self.assertIn("run-complete", events)


if __name__ == "__main__":
    unittest.main()
