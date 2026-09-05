import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from agent.harness import load_harness
from agent.ingest import assert_append_only
from agent.intake import IntakeConfig, approve, import_export, load_staging, preview, publish


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXED_TIME = datetime(2026, 9, 5, 18, 0, tzinfo=timezone.utc)


class OperatingHarnessTests(unittest.TestCase):
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

    def write_export(self, name: str, payload: dict) -> Path:
        path = self.root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def graph(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def proposal_for(self, target_id: str) -> dict:
        return next(item for item in preview(self.config)["proposals"] if item["targetId"] == target_id)

    def test_machine_harness_defines_feos_and_unique_rules(self) -> None:
        harness = load_harness()
        tiers = sorted(harness["authority"]["tiers"], key=lambda item: item["rank"], reverse=True)
        self.assertEqual(harness["authority"]["model"], "FEOS")
        self.assertEqual([item["id"] for item in tiers], ["owner", "primary", "secondary", "tertiary", "unknown"])
        self.assertEqual(len({rule["id"] for rule in harness["rules"]}), len(harness["rules"]))
        self.assertEqual(set(harness["statementTypes"]), {"fact", "decision", "preference", "recommendation", "assumption", "unresolved-question", "policy", "superseded"})

    def test_higher_authority_is_recommended_and_retained_after_review(self) -> None:
        graph = self.graph(self.public)
        existing = next(node for node in graph["nodes"] if node["id"] == "knowledge-hub")
        existing["authorityLevel"] = "tertiary"
        existing["confidence"] = 0.6
        original_wording = existing["description"]
        self.public.write_text(json.dumps(graph), encoding="utf-8")
        path = self.write_export("synthetic-primary.json", {
            "authority": "primary",
            "entities": [{"label": "Knowledge Hub", "description": "Knowledge Hub is active and current."}],
        })

        imported = import_export(path, self.config)
        proposal = self.proposal_for("knowledge-hub")

        self.assertEqual(proposal["policyDecision"]["authorityPrecedence"], "prefer-proposed-after-review")
        self.assertIn("AUTH-002", proposal["policyDecision"]["ruleIds"])
        self.assertEqual(proposal["status"], "needs-review")
        approve(self.config, "private", [proposal["id"]])
        retained = next(node for node in self.graph(self.private)["nodes"] if node["id"] == "knowledge-hub")
        self.assertEqual(retained["authorityLevel"], "primary")
        self.assertEqual(retained["description"], "Knowledge Hub is active and current.")
        self.assertIn(original_wording, {claim["description"] for claim in retained["claimHistory"]})
        self.assertEqual(load_staging(self.staging)["batches"][0]["id"], imported.batch_id)

    def test_conflict_is_surfaced_with_both_versions(self) -> None:
        path = self.write_export("synthetic-conflict.json", {
            "authority": "secondary",
            "entities": [{"label": "Knowledge Hub", "description": "Knowledge Hub is not active."}],
        })

        import_export(path, self.config)
        proposal = self.proposal_for("knowledge-hub")

        self.assertEqual(proposal["kind"], "contradiction")
        self.assertEqual(proposal["status"], "needs-review")
        self.assertIn("CONFLICT-001", proposal["policyDecision"]["ruleIds"])
        self.assertIsNotNone(proposal["previousRecord"])
        self.assertEqual(proposal["proposedRecord"]["description"], "Knowledge Hub is not active.")
        self.assertFalse(self.private.exists())

    def test_superseded_information_remains_traceable(self) -> None:
        original = next(node for node in self.graph(self.public)["nodes"] if node["id"] == "knowledge-hub")["description"]
        path = self.write_export("synthetic-supersession.json", {
            "authority": "primary",
            "entities": [{"label": "Knowledge Hub", "description": "Knowledge Hub is superseded by Knowledge Archive.", "statementType": "superseded"}],
        })

        import_export(path, self.config)
        proposal = self.proposal_for("knowledge-hub")
        approve(self.config, "private", [proposal["id"]])
        staged = self.proposal_for("knowledge-hub")

        self.assertEqual(proposal["kind"], "supersession")
        self.assertIn("SUPER-001", proposal["policyDecision"]["ruleIds"])
        self.assertEqual(staged["previousRecord"]["description"], original)
        self.assertEqual(staged["proposedRecord"]["description"], "Knowledge Hub is superseded by Knowledge Archive.")
        self.assertEqual(next(node for node in self.graph(self.private)["nodes"] if node["id"] == "knowledge-hub")["description"], original)

    def test_sensitive_information_defaults_private_and_needs_override(self) -> None:
        before = self.public.read_bytes()
        path = self.write_export("synthetic-sensitive.json", {
            "entities": [{"label": "person@example.test", "description": "Synthetic contact only."}],
        })
        imported = import_export(path, self.config)
        proposal = next(item for item in preview(self.config)["proposals"] if (item.get("proposedRecord") or {}).get("label") == "person@example.test")

        self.assertEqual(proposal["policyDecision"]["classification"], "private")
        self.assertFalse(proposal["policyDecision"]["publicEligible"])
        self.assertIn("SENS-001", proposal["policyDecision"]["ruleIds"])
        with self.assertRaisesRegex(ValueError, "--allow-sensitive"):
            approve(self.config, "public", batch_id=imported.batch_id)
        self.assertEqual(self.public.read_bytes(), before)
        self.assertFalse(self.private.exists())

    def test_low_confidence_requires_review_and_unapproved_never_publishes(self) -> None:
        before = self.public.read_bytes()
        path = self.write_export("synthetic-low-confidence.json", {
            "authority": "tertiary",
            "entities": [{"label": "Project Confidence", "confidence": 0.5}],
        })
        import_export(path, self.config)
        proposal = next(item for item in preview(self.config)["proposals"] if (item.get("proposedRecord") or {}).get("label") == "Project Confidence")

        self.assertEqual(proposal["status"], "needs-review")
        self.assertIn("CONF-002", proposal["policyDecision"]["ruleIds"])
        self.assertTrue(proposal["policyDecision"]["requiresReview"])
        self.assertEqual(publish(self.config).details["publishedCount"], 0)
        self.assertEqual(self.public.read_bytes(), before)

    def test_unsupported_inference_is_never_classified_as_fact(self) -> None:
        path = self.root / "synthetic-inference.md"
        path.write_text("Project Lantern will probably launch next season.\n", encoding="utf-8")

        import_export(path, self.config)
        proposal = next(item for item in preview(self.config)["proposals"] if (item.get("proposedRecord") or {}).get("label", "").startswith("Project Lantern will"))

        self.assertEqual(proposal["policyDecision"]["statementType"], "assumption")
        self.assertIn("EPI-002", proposal["policyDecision"]["ruleIds"])
        self.assertIn("unsupported-inference", proposal["reviewReasons"])
        self.assertEqual(proposal["status"], "needs-review")

    def test_explicit_decision_owner_and_exact_wording_are_preserved(self) -> None:
        wording = "Use  two spaces exactly.\nKeep this second line."
        path = self.write_export("synthetic-owner.json", {
            "authority": "owner",
            "decisions": [{
                "label": "Synthetic Launch Decision",
                "approvedWording": wording,
                "preserveExactWording": True,
                "decisionOwner": "Jordan Example",
            }],
        })
        import_export(path, self.config)
        proposals = preview(self.config)["proposals"]
        decision = next(item for item in proposals if (item.get("proposedRecord") or {}).get("label") == "Synthetic Launch Decision")
        relationship = next(item for item in proposals if (item.get("record") or {}).get("relationship") == "decision-owner")

        self.assertEqual(decision["proposedRecord"]["description"], wording)
        self.assertTrue(decision["policyDecision"]["preservesExactWording"])
        self.assertIn("WORDING-001", decision["policyDecision"]["ruleIds"])
        self.assertEqual(relationship["recordType"], "edge")

    def test_every_proposal_and_audit_event_records_harness_rules(self) -> None:
        path = self.write_export("synthetic-audit.json", {"projects": ["Project Audit"]})
        import_export(path, self.config)
        proposals = preview(self.config)["proposals"]

        self.assertTrue(proposals)
        self.assertTrue(all(item["policyDecision"]["harnessVersion"] == "1.0.0" for item in proposals))
        self.assertTrue(all(item["policyDecision"]["ruleIds"] for item in proposals))
        event = json.loads(next(self.logs.glob("intake-*.jsonl")).read_text(encoding="utf-8"))
        self.assertEqual(event["harnessVersion"], "1.0.0")
        self.assertTrue(event["ruleIds"])

    def test_existing_wording_cannot_change_without_trace_history(self) -> None:
        original = self.graph(self.public)
        changed = json.loads(json.dumps(original))
        changed["nodes"][0]["description"] = "Silent replacement"

        with self.assertRaisesRegex(RuntimeError, "without history"):
            assert_append_only(original, changed)


if __name__ == "__main__":
    unittest.main()
