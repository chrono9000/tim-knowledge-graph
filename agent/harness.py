"""Load and apply the FEOS operating harness to intake proposals."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


DEFAULT_HARNESS_PATH = Path(__file__).with_name("harness.json")
BASE_RULE_IDS = {
    "HARNESS-001", "EPI-001", "AUTH-001", "PROV-001", "TIME-001",
    "CONF-001", "NAME-001", "PRIV-001", "REVIEW-001", "APPEND-001",
    "AUDIT-001", "RECOVERY-001",
}


def load_harness(path: Path | None = None) -> dict[str, Any]:
    harness_path = path or DEFAULT_HARNESS_PATH
    harness = json.loads(harness_path.read_text(encoding="utf-8"))
    required = {"id", "version", "statementTypes", "authority", "confidence", "classification", "rules"}
    if not isinstance(harness, dict) or not required <= harness.keys():
        raise ValueError("Invalid operating harness")
    rules = harness.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("Operating harness must define rules")
    rule_ids = [rule.get("id") for rule in rules if isinstance(rule, dict)]
    if len(rule_ids) != len(rules) or len(set(rule_ids)) != len(rule_ids):
        raise ValueError("Operating harness rule IDs must be present and unique")
    tiers = harness.get("authority", {}).get("tiers", [])
    if {tier.get("id") for tier in tiers if isinstance(tier, dict)} != {"owner", "primary", "secondary", "tertiary", "unknown"}:
        raise ValueError("Operating harness must define every FEOS authority tier")
    return harness


def authority_ranks(harness: dict[str, Any]) -> dict[str, int]:
    return {tier["id"]: int(tier["rank"]) for tier in harness["authority"]["tiers"]}


def rule_catalog(harness: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {rule["id"]: rule for rule in harness["rules"]}


def _record_text(record: dict[str, Any] | None) -> str:
    record = record or {}
    return " ".join(str(record.get(key, "")) for key in ("label", "description", "relationship"))


def evaluate_proposal(
    harness: dict[str, Any],
    record: dict[str, Any],
    source: dict[str, Any],
    reasons: Iterable[str],
    previous: dict[str, Any] | None = None,
    proposed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return deterministic policy impacts for one proposal."""
    reason_set = set(reasons)
    rule_ids = set(BASE_RULE_IDS)
    statement_type = str((proposed or record).get("statementType", record.get("statementType", "fact")))
    if statement_type in {"assumption", "unresolved-question"}:
        rule_ids.add("EPI-002")
    if previous:
        rule_ids.add("DEDUP-001")
    if "authority-conflict" in reason_set:
        rule_ids.update({"AUTH-002", "CONFLICT-001"})
    if "possible-contradiction" in reason_set:
        rule_ids.add("CONFLICT-001")
    if "possible-supersession" in reason_set or statement_type == "superseded":
        rule_ids.add("SUPER-001")
    if "low-confidence" in reason_set:
        rule_ids.add("CONF-002")
    if "sensitive-information" in reason_set:
        rule_ids.add("SENS-001")
    if record.get("entityType") in {"entity", "project", "decision", "person", "system", "policy"}:
        rule_ids.add("OWNER-001")
    if bool((proposed or record).get("exactWording") or record.get("exactWording")):
        rule_ids.add("WORDING-001")

    ranks = authority_ranks(harness)
    incoming_authority = source.get("authorityLevel", "unknown")
    previous_authority = previous.get("authorityLevel", "unknown") if previous else None
    if not previous:
        authority_precedence = "new-record"
    elif ranks.get(incoming_authority, 0) > ranks.get(previous_authority or "unknown", 0):
        authority_precedence = "prefer-proposed-after-review"
    elif ranks.get(incoming_authority, 0) < ranks.get(previous_authority or "unknown", 0):
        authority_precedence = "retain-existing-unless-review-overrides"
    else:
        authority_precedence = "same-tier-review"

    catalog = rule_catalog(harness)
    ordered_rule_ids = sorted(rule_ids)
    missing = set(ordered_rule_ids) - catalog.keys()
    if missing:
        raise ValueError(f"Operating harness is missing referenced rules: {sorted(missing)}")
    sensitive = "sensitive-information" in reason_set
    return {
        "harnessId": harness["id"],
        "harnessVersion": harness["version"],
        "ruleIds": ordered_rule_ids,
        "ruleEffects": [{"ruleId": rule_id, "topic": catalog[rule_id]["topic"]} for rule_id in ordered_rule_ids],
        "statementType": statement_type,
        "classification": "private",
        "publicEligible": not sensitive,
        "requiresReview": True,
        "authorityPrecedence": authority_precedence,
        "incomingAuthority": incoming_authority,
        "previousAuthority": previous_authority,
        "preservesPreviousWording": bool(previous),
        "preservesExactWording": "WORDING-001" in rule_ids,
        "sourceTextStoredAsFact": statement_type == "fact" and "EPI-002" not in rule_ids,
        "textCompared": bool(_record_text(proposed or record)),
    }


def affected_rule_ids(proposals: Iterable[dict[str, Any]]) -> list[str]:
    return sorted({rule_id for proposal in proposals for rule_id in proposal.get("policyDecision", {}).get("ruleIds", [])})
