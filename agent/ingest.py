"""Deterministic, append-only knowledge-graph ingestion pipeline."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


SUPPORTED_SUFFIXES = {".txt", ".md", ".json"}
AUTHORITY_LEVELS = ("owner", "primary", "secondary", "tertiary", "unknown")
ENTITY_TYPES = {
    "entity", "project", "decision", "person", "system", "policy",
    "event", "open-issue", "historical-note",
}
STATEMENT_TYPES = {
    "fact", "decision", "recommendation", "assumption",
    "unresolved-question", "policy",
}
TAG_TYPES = {
    "entity": ("entity", "fact"),
    "project": ("project", "fact"),
    "decision": ("decision", "decision"),
    "person": ("person", "fact"),
    "system": ("system", "fact"),
    "policy": ("policy", "policy"),
    "event": ("event", "fact"),
    "open issue": ("open-issue", "unresolved-question"),
    "historical note": ("historical-note", "fact"),
    "fact": ("historical-note", "fact"),
    "recommendation": ("historical-note", "recommendation"),
    "assumption": ("historical-note", "assumption"),
    "question": ("open-issue", "unresolved-question"),
}
AUTHORITY_RANK = {"unknown": 0, "tertiary": 1, "secondary": 2, "primary": 3, "owner": 4}
AUTHORITY_CONFIDENCE = {"unknown": 0.5, "tertiary": 0.6, "secondary": 0.75, "primary": 0.9, "owner": 1.0}

TAG_PATTERN = re.compile(
    r"^(entity|project|decision|person|system|policy|event|open[ -]issue|historical[ -]note|fact|recommendation|assumption|question)\s*:\s*(.+)$",
    re.IGNORECASE,
)
RELATIONSHIP_PATTERN = re.compile(r"^relationship\s*:\s*(.+?)\s*\|\s*([^|]+?)\s*\|\s*(.+)$", re.IGNORECASE)
ARROW_PATTERN = re.compile(r"^(?:\[\[)?(.+?)(?:\]\])?\s*(?:--|->)\s*([a-z][a-z0-9 _-]*?)\s*(?:-->|->)\s*(?:\[\[)?(.+?)(?:\]\])?$", re.IGNORECASE)
PROJECT_PATTERN = re.compile(r"\bProject\s+[A-Z][\w-]*(?:\s+[A-Z][\w-]*){0,3}\b")
SYSTEM_PATTERN = re.compile(r"\b[A-Z][A-Za-z0-9&.-]*(?:\s+[A-Z][A-Za-z0-9&.-]*){0,2}\s+(?:System|Platform|Service|API|Database|CRM|ERP)\b")
PERSON_PATTERN = re.compile(r"\b(?:by|with|owner|lead|manager|contact)\s+([A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)?\s+[A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)?)\b")
METADATA_KEYS = {"authority", "authority-level", "source-authority", "confidence", "source-confidence", "source-timestamp"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc).replace(microsecond=0)
    return value.isoformat().replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def earlier_timestamp(*values: str) -> str:
    return iso_timestamp(min(parse_timestamp(value) for value in values if value))


def later_timestamp(*values: str) -> str:
    return iso_timestamp(max(parse_timestamp(value) for value in values if value))


def canonical_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return " ".join(re.findall(r"[a-z0-9]+", normalized.casefold()))


def slugify(value: str) -> str:
    return canonical_text(value).replace(" ", "-")[:80].strip("-") or "item"


def clean_label(value: str) -> str:
    value = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", "", value.strip())
    value = re.sub(r"[*_`#]+", "", value).strip(" \t\r\n.;,:\"'")
    return re.sub(r"\s+", " ", value)[:120]


def clean_description(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())[:1000]


def relationship_slug(value: str) -> str:
    return slugify(value) or "related-to"


@dataclass
class CandidateNode:
    label: str
    description: str
    entity_type: str
    statement_type: str
    confidence: float


@dataclass
class CandidateEdge:
    source_label: str
    target_label: str
    relationship: str
    confidence: float
    directed: bool = True
    statement_type: str = "fact"


@dataclass
class ExtractedDocument:
    nodes: dict[str, CandidateNode] = field(default_factory=dict)
    edges: dict[tuple[str, str, str, bool], CandidateEdge] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)
    is_conversation: bool = False

    def add_node(self, candidate: CandidateNode) -> None:
        candidate.label = clean_label(candidate.label)
        candidate.description = clean_description(candidate.description)
        if not candidate.label or len(candidate.label) < 2:
            return
        if candidate.entity_type not in ENTITY_TYPES:
            candidate.entity_type = "entity"
        if candidate.statement_type not in STATEMENT_TYPES:
            candidate.statement_type = "fact"
        key = canonical_text(candidate.label)
        current = self.nodes.get(key)
        if current is None:
            self.nodes[key] = candidate
            return
        specificity = {"entity": 0, "historical-note": 1}
        if specificity.get(candidate.entity_type, 2) > specificity.get(current.entity_type, 2):
            current.entity_type = candidate.entity_type
            current.statement_type = candidate.statement_type
        current.confidence = max(current.confidence, candidate.confidence)
        if len(candidate.description) > len(current.description):
            current.description = candidate.description

    def add_edge(self, candidate: CandidateEdge) -> None:
        source = clean_label(candidate.source_label)
        target = clean_label(candidate.target_label)
        if not source or not target or canonical_text(source) == canonical_text(target):
            return
        relationship = relationship_slug(candidate.relationship)
        source_key, target_key = canonical_text(source), canonical_text(target)
        if not candidate.directed and source_key > target_key:
            source, target = target, source
            source_key, target_key = target_key, source_key
        key = (source_key, target_key, relationship, candidate.directed)
        candidate.source_label = source
        candidate.target_label = target
        candidate.relationship = relationship
        existing = self.edges.get(key)
        if existing is None or candidate.confidence > existing.confidence:
            self.edges[key] = candidate


@dataclass
class IngestionConfig:
    raw_dir: Path
    graph_path: Path
    manifest_path: Path
    log_dir: Path
    dry_run: bool = False
    authority_override: str = "auto"
    clock: Callable[[], datetime] = utc_now


@dataclass
class IngestionResult:
    dry_run: bool
    files_found: int = 0
    files_processed: int = 0
    files_skipped: int = 0
    sources_added: int = 0
    nodes_added: int = 0
    nodes_merged: int = 0
    edges_added: int = 0
    edges_merged: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)
    graph_changed: bool = False
    log_path: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "dryRun": self.dry_run,
            "filesFound": self.files_found,
            "filesProcessed": self.files_processed,
            "filesSkipped": self.files_skipped,
            "sourcesAdded": self.sources_added,
            "nodesAdded": self.nodes_added,
            "nodesMerged": self.nodes_merged,
            "edgesAdded": self.edges_added,
            "edgesMerged": self.edges_merged,
            "errors": self.errors,
            "graphChanged": self.graph_changed,
            "logPath": self.log_path,
        }


class ChangeLog:
    def __init__(self, run_id: str, occurred_at: str) -> None:
        self.run_id = run_id
        self.occurred_at = occurred_at
        self.events: list[dict[str, Any]] = []

    def record(self, action: str, **details: Any) -> None:
        self.events.append({"runId": self.run_id, "occurredAt": self.occurred_at, "action": action, **details})


def parse_metadata(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    metadata: dict[str, str] = {}
    body_start = 0
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                body_start = index + 1
                break
            if ":" in lines[index]:
                key, value = lines[index].split(":", 1)
                key = key.strip().casefold().replace("_", "-")
                if key in METADATA_KEYS:
                    metadata[key] = value.strip()
    body_lines: list[str] = []
    for line in lines[body_start:]:
        if ":" in line:
            key, value = line.split(":", 1)
            normalized = key.strip().casefold().replace("_", "-")
            if normalized in METADATA_KEYS:
                metadata[normalized] = value.strip()
                continue
        body_lines.append(line)
    return metadata, "\n".join(body_lines)


def classify_statement(text: str) -> tuple[str, str, float]:
    lowered = text.casefold()
    if text.rstrip().endswith("?") or re.search(r"\b(?:open question|unresolved|unknown|tbd)\b", lowered):
        return "open-issue", "unresolved-question", 0.82
    if re.search(r"\b(?:must|shall|required|prohibited|may not|policy)\b", lowered):
        return "policy", "policy", 0.82
    if re.search(r"\b(?:we decided|decided to|decision|approved|selected)\b", lowered):
        return "decision", "decision", 0.82
    if re.search(r"\b(?:recommend|recommended|should|consider)\b", lowered):
        return "historical-note", "recommendation", 0.74
    if re.search(r"\b(?:assume|assumption|likely|probably|believe)\b", lowered):
        return "historical-note", "assumption", 0.68
    if re.search(r"\b(?:on \d{4}-\d{2}-\d{2}|on [A-Z][a-z]+ \d{1,2},? \d{4})\b", text):
        return "event", "fact", 0.76
    return "historical-note", "fact", 0.7


def category_for(label: str, entity_type: str, available: set[str]) -> str:
    padded = f" {label.casefold()} "
    keyword_categories = (
        ("finance", ("finance", "invest", "budget", "bank", "risk", "revenue")),
        ("health-fitness", ("health", "fitness", "sleep", "nutrition", "medical", "workout")),
        ("real-estate", ("property", "real estate", "home", "neighborhood")),
        ("automotive", ("vehicle", "car", "automotive", "mobility")),
        ("everquest", ("everquest", "norrath", "raid")),
        ("media-closet", ("media", "wardrobe", "closet", "film", "music")),
        ("ai-tech", (" ai ", "api", "software", "system", "platform", "database", "automation", "tech")),
        ("business", ("business", "customer", "project", "policy", "decision", "operations")),
    )
    for category, words in keyword_categories:
        if category in available and any(word in padded for word in words):
            return category
    preferred = {"project": "business", "decision": "business", "system": "ai-tech", "policy": "business", "person": "personal"}.get(entity_type, "personal")
    return preferred if preferred in available else (sorted(available)[0] if available else "personal")


def named_entities(sentence: str) -> list[tuple[str, str]]:
    found: dict[str, tuple[str, str]] = {}
    for pattern, entity_type, group in ((PROJECT_PATTERN, "project", 0), (SYSTEM_PATTERN, "system", 0), (PERSON_PATTERN, "person", 1)):
        for match in pattern.finditer(sentence):
            label = clean_label(match.group(group))
            found[canonical_text(label)] = (label, entity_type)
    return list(found.values())


def extract_text(text: str, *, base_confidence: float = 1.0) -> ExtractedDocument:
    document = ExtractedDocument()
    metadata, body = parse_metadata(text)
    document.metadata.update(metadata)
    in_code_block = False
    unstructured: list[str] = []
    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block or not stripped:
            continue
        line = clean_label(stripped)
        if not line:
            continue
        relationship_match = RELATIONSHIP_PATTERN.match(line) or ARROW_PATTERN.match(line)
        if relationship_match:
            source, relationship, target = relationship_match.groups()
            document.add_node(CandidateNode(source, f"Referenced by an explicit relationship: {line}", "entity", "fact", 0.88 * base_confidence))
            document.add_node(CandidateNode(target, f"Referenced by an explicit relationship: {line}", "entity", "fact", 0.88 * base_confidence))
            document.add_edge(CandidateEdge(source, target, relationship, 0.9 * base_confidence))
            continue
        tagged = TAG_PATTERN.match(line)
        if tagged:
            tag = tagged.group(1).casefold().replace("-", " ")
            entity_type, statement_type = TAG_TYPES[tag]
            document.add_node(CandidateNode(tagged.group(2), line, entity_type, statement_type, 0.92 * base_confidence))
            continue
        if stripped.startswith("#"):
            heading = clean_label(stripped)
            document.add_node(CandidateNode(heading, f"Section heading: {heading}", "entity", "fact", 0.72 * base_confidence))
            continue
        unstructured.append(line)

    for line in unstructured:
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            sentence = clean_description(sentence)
            if len(sentence) < 8 or sentence.startswith(("http://", "https://")):
                continue
            entity_type, statement_type, confidence = classify_statement(sentence)
            statement_label = clean_label(sentence[:120])
            document.add_node(CandidateNode(statement_label, sentence, entity_type, statement_type, confidence * base_confidence))
            mentions = named_entities(sentence)
            for label, mention_type in mentions:
                document.add_node(CandidateNode(label, f"Mentioned in: {sentence}", mention_type, "fact", 0.76 * base_confidence))
                if canonical_text(statement_label) != canonical_text(label):
                    document.add_edge(CandidateEdge(statement_label, label, "mentions", 0.72 * base_confidence, True, statement_type))
            for index, (left, _) in enumerate(mentions):
                for right, _ in mentions[index + 1:]:
                    document.add_edge(CandidateEdge(left, right, "mentioned-with", 0.6 * base_confidence, False))
    return document


def content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (content_text(item) for item in value)))
    if isinstance(value, dict):
        for key in ("text", "parts", "content", "value"):
            if key in value:
                text = content_text(value[key])
                if text:
                    return text
    return ""


def conversation_messages(value: Any) -> list[tuple[str, str]]:
    messages: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def walk(item: Any) -> None:
        if isinstance(item, list):
            for child in item:
                walk(child)
            return
        if not isinstance(item, dict):
            return
        role_value = item.get("role")
        if not role_value and isinstance(item.get("author"), dict):
            role_value = item["author"].get("role")
        text = content_text(item.get("content", item.get("text", ""))) if role_value else ""
        if role_value and text.strip():
            key = (str(role_value), text.strip())
            if key not in seen:
                seen.add(key)
                messages.append(key)
        for child_key, child in item.items():
            if child_key not in {"content", "text"}:
                walk(child)

    walk(value)
    return messages


def structured_json(document: ExtractedDocument, value: Any) -> None:
    if not isinstance(value, dict):
        return
    collections = {
        "entities": "entity", "projects": "project", "decisions": "decision",
        "people": "person", "systems": "system", "policies": "policy",
        "events": "event", "openIssues": "open-issue", "open_issues": "open-issue",
        "historicalNotes": "historical-note", "historical_notes": "historical-note",
    }
    default_statements = {"decision": "decision", "policy": "policy", "open-issue": "unresolved-question"}
    for key, entity_type in collections.items():
        items = value.get(key, [])
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, str):
                label, description, confidence = item, item, 0.92
                statement_type = default_statements.get(entity_type, "fact")
            elif isinstance(item, dict):
                label = str(item.get("label") or item.get("name") or item.get("title") or "")
                description = str(item.get("description") or item.get("text") or label)
                confidence = float(item.get("confidence", 0.92))
                statement_type = str(item.get("statementType") or item.get("statement_type") or default_statements.get(entity_type, "fact"))
            else:
                continue
            document.add_node(CandidateNode(label, description, entity_type, statement_type, confidence))
    relationships = value.get("relationships", value.get("edges", []))
    if isinstance(relationships, list):
        for item in relationships:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source") or item.get("from") or "")
            target = str(item.get("target") or item.get("to") or "")
            relationship = str(item.get("relationship") or item.get("type") or item.get("label") or "related-to")
            if source and target:
                document.add_node(CandidateNode(source, f"Referenced by relationship {relationship}.", "entity", "fact", 0.88))
                document.add_node(CandidateNode(target, f"Referenced by relationship {relationship}.", "entity", "fact", 0.88))
                document.add_edge(CandidateEdge(source, target, relationship, float(item.get("confidence", 0.9)), bool(item.get("directed", True)), str(item.get("statementType", "fact"))))


def flatten_json(value: Any, prefix: str = "") -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield from flatten_json(child, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(value, list):
        for child in value:
            yield from flatten_json(child, prefix)
    elif value is not None:
        yield f"{prefix.rsplit('.', 1)[-1].replace('_', ' ')}: {value}"


def extract_json(value: Any) -> ExtractedDocument:
    document = ExtractedDocument()
    structured_json(document, value)
    messages = conversation_messages(value)
    if messages:
        document.is_conversation = True
        for role, text in messages:
            extracted = extract_text(text, base_confidence=0.88 if role.casefold() == "user" else 0.78)
            for candidate in extracted.nodes.values():
                document.add_node(candidate)
            for candidate in extracted.edges.values():
                document.add_edge(candidate)
    elif not document.nodes:
        extracted = extract_text("\n".join(flatten_json(value)), base_confidence=0.75)
        for candidate in extracted.nodes.values():
            document.add_node(candidate)
        for candidate in extracted.edges.values():
            document.add_edge(candidate)
    if isinstance(value, dict):
        for key in METADATA_KEYS:
            json_key = key.replace("-", "_")
            if key in value:
                document.metadata[key] = str(value[key])
            elif json_key in value:
                document.metadata[key] = str(value[json_key])
    return document


def read_source(path: Path) -> tuple[bytes, ExtractedDocument]:
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    return (raw, extract_json(json.loads(text))) if path.suffix.casefold() == ".json" else (raw, extract_text(text))


def infer_authority(path: Path, document: ExtractedDocument, override: str) -> str:
    if override != "auto":
        return override
    for key in ("authority-level", "source-authority", "authority"):
        value = document.metadata.get(key, "").casefold()
        if value in AUTHORITY_LEVELS:
            return value
    lowered = path.name.casefold()
    if any(word in lowered for word in ("official", "policy", "decision", "minutes", "record")):
        return "primary"
    if document.is_conversation or any(word in lowered for word in ("conversation", "chat", "export")):
        return "secondary"
    return "tertiary"


def source_confidence(document: ExtractedDocument, authority: str) -> float:
    for key in ("source-confidence", "confidence"):
        if key in document.metadata:
            try:
                return max(0.0, min(1.0, float(document.metadata[key])))
            except ValueError:
                break
    return AUTHORITY_CONFIDENCE[authority]


def source_timestamp(path: Path, document: ExtractedDocument) -> str:
    declared = document.metadata.get("source-timestamp")
    return iso_timestamp(parse_timestamp(declared)) if declared else iso_timestamp(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc))


def source_id(relative_path: str, digest: str) -> str:
    return f"source-{hashlib.sha256(f'{relative_path}{chr(0)}{digest}'.encode('utf-8')).hexdigest()[:16]}"


def unique_node_id(label: str, existing: dict[str, dict[str, Any]]) -> str:
    base = slugify(label)
    if base not in existing or canonical_text(existing[base].get("label", "")) == canonical_text(label):
        return base
    return f"{base[:70]}-{hashlib.sha256(label.encode('utf-8')).hexdigest()[:8]}"


def edge_identity(source: str, target: str, relationship: str, directed: bool) -> tuple[str, str, str, bool]:
    if not directed and source > target:
        source, target = target, source
    return source, target, relationship, directed


def unique_edge_id(identity: tuple[str, str, str, bool], existing_ids: set[str]) -> str:
    digest = hashlib.sha256(chr(0).join(map(str, identity)).encode("utf-8")).hexdigest()[:16]
    candidate = f"edge-{digest}"
    counter = 2
    while candidate in existing_ids:
        candidate = f"edge-{digest}-{counter}"
        counter += 1
    return candidate


def merge_document(graph: dict[str, Any], document: ExtractedDocument, source: dict[str, Any], observed_at: str, run_at: str, changes: ChangeLog, result: IngestionResult) -> None:
    if source["id"] not in {item["id"] for item in graph["sources"]}:
        graph["sources"].append(source)
        result.sources_added += 1
        changes.record("source-added", sourceId=source["id"], filename=source["filename"], sourceTimestamp=source["sourceTimestamp"])
    node_by_id = {node["id"]: node for node in graph["nodes"]}
    node_by_label = {canonical_text(node.get("label", "")): node for node in graph["nodes"]}
    candidate_to_id: dict[str, str] = {}
    available_categories = {category["id"] for category in graph.get("categories", [])}
    authority, confidence_cap = source["authorityLevel"], source["confidence"]

    for key, candidate in document.nodes.items():
        candidate_confidence = round(min(confidence_cap, max(0.0, min(1.0, candidate.confidence))), 3)
        existing = node_by_label.get(key)
        if existing is None:
            node_id = unique_node_id(candidate.label, node_by_id)
            node = {
                "id": node_id, "label": candidate.label,
                "category": category_for(candidate.label, candidate.entity_type, available_categories),
                "description": candidate.description or candidate.label,
                "entityType": candidate.entity_type, "statementType": candidate.statement_type,
                "sourceIds": [source["id"]],
                "timestamps": {"createdAt": run_at, "updatedAt": run_at, "firstSeen": observed_at, "lastSeen": observed_at},
                "confidence": candidate_confidence, "authorityLevel": authority,
            }
            graph["nodes"].append(node)
            node_by_id[node_id] = node
            node_by_label[key] = node
            candidate_to_id[key] = node_id
            result.nodes_added += 1
            changes.record(
                "node-added", nodeId=node_id, label=candidate.label,
                category=node["category"], entityType=candidate.entity_type,
                statementType=candidate.statement_type, sourceId=source["id"],
                confidence=node["confidence"], authorityLevel=authority,
                timestamps=node["timestamps"],
            )
            continue
        candidate_to_id[key] = existing["id"]
        before = copy.deepcopy(existing)
        existing.setdefault("sourceIds", [])
        if source["id"] not in existing["sourceIds"]:
            existing["sourceIds"].append(source["id"])
        timestamps = existing.setdefault("timestamps", {})
        created_at = timestamps.get("createdAt", run_at)
        updated_at = timestamps.get("updatedAt", created_at)
        timestamps["createdAt"] = created_at
        timestamps["firstSeen"] = earlier_timestamp(timestamps.get("firstSeen", created_at), observed_at)
        timestamps["lastSeen"] = later_timestamp(timestamps.get("lastSeen", updated_at), observed_at)
        existing.setdefault("entityType", candidate.entity_type)
        existing.setdefault("statementType", candidate.statement_type)
        existing["confidence"] = max(float(existing.get("confidence", 0)), candidate_confidence)
        if AUTHORITY_RANK[authority] > AUTHORITY_RANK.get(existing.get("authorityLevel", "unknown"), 0):
            existing["authorityLevel"] = authority
        if existing != before:
            timestamps["updatedAt"] = run_at
            result.nodes_merged += 1
            changes.record(
                "node-merged", nodeId=existing["id"], label=existing["label"],
                sourceId=source["id"],
                changedFields=sorted(key for key in existing if existing.get(key) != before.get(key)),
                confidence=existing["confidence"], authorityLevel=existing["authorityLevel"],
                timestamps=existing["timestamps"],
            )

    edge_by_identity: dict[tuple[str, str, str, bool], dict[str, Any]] = {}
    edge_ids = {edge["id"] for edge in graph["edges"]}
    for edge in graph["edges"]:
        identity = edge_identity(edge["source"], edge["target"], edge["relationship"], bool(edge.get("directed", False)))
        edge_by_identity.setdefault(identity, edge)
    for candidate in document.edges.values():
        source_node_id = candidate_to_id.get(canonical_text(candidate.source_label)) or node_by_label.get(canonical_text(candidate.source_label), {}).get("id")
        target_node_id = candidate_to_id.get(canonical_text(candidate.target_label)) or node_by_label.get(canonical_text(candidate.target_label), {}).get("id")
        if not source_node_id or not target_node_id or source_node_id == target_node_id:
            continue
        identity = edge_identity(source_node_id, target_node_id, candidate.relationship, candidate.directed)
        existing_edge = edge_by_identity.get(identity)
        edge_confidence = round(min(confidence_cap, max(0.0, min(1.0, candidate.confidence))), 3)
        if existing_edge is None:
            edge_id = unique_edge_id(identity, edge_ids)
            edge = {
                "id": edge_id, "source": source_node_id, "target": target_node_id,
                "relationship": candidate.relationship, "directed": candidate.directed,
                "statementType": candidate.statement_type, "sourceIds": [source["id"]],
                "timestamps": {"createdAt": run_at, "updatedAt": run_at, "firstSeen": observed_at, "lastSeen": observed_at},
                "confidence": edge_confidence, "authorityLevel": authority,
            }
            graph["edges"].append(edge)
            edge_by_identity[identity] = edge
            edge_ids.add(edge_id)
            result.edges_added += 1
            changes.record(
                "edge-added", edgeId=edge_id, source=source_node_id,
                target=target_node_id, relationship=candidate.relationship,
                directed=candidate.directed, statementType=candidate.statement_type,
                sourceId=source["id"], confidence=edge["confidence"],
                authorityLevel=authority, timestamps=edge["timestamps"],
            )
            continue
        before = copy.deepcopy(existing_edge)
        existing_edge.setdefault("sourceIds", [])
        if source["id"] not in existing_edge["sourceIds"]:
            existing_edge["sourceIds"].append(source["id"])
        timestamps = existing_edge.setdefault("timestamps", {})
        created_at = timestamps.get("createdAt", run_at)
        updated_at = timestamps.get("updatedAt", created_at)
        timestamps["createdAt"] = created_at
        timestamps["firstSeen"] = earlier_timestamp(timestamps.get("firstSeen", created_at), observed_at)
        timestamps["lastSeen"] = later_timestamp(timestamps.get("lastSeen", updated_at), observed_at)
        existing_edge.setdefault("statementType", candidate.statement_type)
        existing_edge["confidence"] = max(float(existing_edge.get("confidence", 0)), edge_confidence)
        if AUTHORITY_RANK[authority] > AUTHORITY_RANK.get(existing_edge.get("authorityLevel", "unknown"), 0):
            existing_edge["authorityLevel"] = authority
        if existing_edge != before:
            timestamps["updatedAt"] = run_at
            result.edges_merged += 1
            changes.record(
                "edge-merged", edgeId=existing_edge["id"], sourceId=source["id"],
                changedFields=sorted(key for key in existing_edge if existing_edge.get(key) != before.get(key)),
                confidence=existing_edge["confidence"], authorityLevel=existing_edge["authorityLevel"],
                timestamps=existing_edge["timestamps"],
            )


def discover_files(raw_dir: Path) -> list[Path]:
    if not raw_dir.exists():
        return []
    return sorted(path for path in raw_dir.rglob("*") if path.is_file() and not path.is_symlink() and path.suffix.casefold() in SUPPORTED_SUFFIXES and path.name.casefold() != "readme.md" and not any(part.startswith(".") for part in path.relative_to(raw_dir).parts))


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "files": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("files"), dict):
        raise ValueError(f"Invalid ingestion manifest: {path}")
    return value


def atomic_json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False, newline="\n") as handle:
            temporary_name = handle.name
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def write_change_log(log_dir: Path, changes: ChangeLog) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"ingestion-{changes.run_id}.jsonl"
    path.write_text("".join(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n" for event in changes.events), encoding="utf-8", newline="\n")
    return path


def assert_append_only(original: dict[str, Any], merged: dict[str, Any]) -> None:
    for collection in ("sources", "nodes", "edges"):
        missing = {item["id"] for item in original.get(collection, [])} - {item["id"] for item in merged.get(collection, [])}
        if missing:
            raise RuntimeError(f"Append-only invariant failed for {collection}: {sorted(missing)}")


def validate_graph(graph: dict[str, Any]) -> None:
    required = {"categories", "sources", "nodes", "edges"}
    if not required <= graph.keys():
        raise ValueError(f"Graph is missing collections: {sorted(required - graph.keys())}")
    identifiers: dict[str, set[str]] = {}
    for collection in ("categories", "sources", "nodes", "edges"):
        values = graph[collection]
        if not isinstance(values, list):
            raise ValueError(f"Graph collection {collection} must be an array")
        ids = [item.get("id") for item in values if isinstance(item, dict)]
        if len(ids) != len(values) or any(not item for item in ids):
            raise ValueError(f"Every {collection} record must have an ID")
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate IDs found in {collection}")
        identifiers[collection] = set(ids)

    for record in [*graph["sources"], *graph["nodes"], *graph["edges"]]:
        confidence = record.get("confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
            raise ValueError(f"Invalid confidence on {record['id']}")
        if record.get("authorityLevel") not in AUTHORITY_LEVELS:
            raise ValueError(f"Invalid authority level on {record['id']}")

    for node in graph["nodes"]:
        if node.get("category") not in identifiers["categories"]:
            raise ValueError(f"Unknown category on node {node['id']}")
        if not set(node.get("sourceIds", [])) <= identifiers["sources"]:
            raise ValueError(f"Unknown source reference on node {node['id']}")
        if node.get("entityType") is not None and node["entityType"] not in ENTITY_TYPES:
            raise ValueError(f"Invalid entity type on node {node['id']}")
        if node.get("statementType") is not None and node["statementType"] not in STATEMENT_TYPES:
            raise ValueError(f"Invalid statement type on node {node['id']}")
        _validate_record_timestamps(node)

    for edge in graph["edges"]:
        if edge.get("source") not in identifiers["nodes"] or edge.get("target") not in identifiers["nodes"]:
            raise ValueError(f"Unknown endpoint on edge {edge['id']}")
        if not set(edge.get("sourceIds", [])) <= identifiers["sources"]:
            raise ValueError(f"Unknown source reference on edge {edge['id']}")
        if edge.get("statementType") is not None and edge["statementType"] not in STATEMENT_TYPES:
            raise ValueError(f"Invalid statement type on edge {edge['id']}")
        _validate_record_timestamps(edge)


def _validate_record_timestamps(record: dict[str, Any]) -> None:
    timestamps = record.get("timestamps", {})
    for key in ("createdAt", "updatedAt", "firstSeen", "lastSeen"):
        if timestamps.get(key):
            parse_timestamp(timestamps[key])
    if timestamps.get("firstSeen") and timestamps.get("lastSeen"):
        if parse_timestamp(timestamps["firstSeen"]) > parse_timestamp(timestamps["lastSeen"]):
            raise ValueError(f"firstSeen is after lastSeen on {record['id']}")


def run_ingestion(config: IngestionConfig) -> IngestionResult:
    now = config.clock()
    run_at = iso_timestamp(now)
    run_id = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    changes = ChangeLog(run_id, run_at)
    result = IngestionResult(dry_run=config.dry_run)
    original = json.loads(config.graph_path.read_text(encoding="utf-8"))
    validate_graph(original)
    graph = copy.deepcopy(original)
    manifest = load_manifest(config.manifest_path)
    files = discover_files(config.raw_dir)
    result.files_found = len(files)
    known_source_ids = {source["id"] for source in graph["sources"]}
    for path in files:
        relative_path = path.relative_to(config.raw_dir).as_posix()
        try:
            raw, document = read_source(path)
            digest = hashlib.sha256(raw).hexdigest()
            previous = manifest["files"].get(relative_path)
            if previous and previous.get("sha256") == digest and previous.get("sourceId") in known_source_ids:
                result.files_skipped += 1
                changes.record("file-skipped", filename=relative_path, reason="unchanged")
                continue
            observed_at = source_timestamp(path, document)
            authority = infer_authority(path, document, config.authority_override)
            confidence = source_confidence(document, authority)
            current_source_id = source_id(relative_path, digest)
            source = {
                "id": current_source_id, "title": path.stem,
                "type": "conversation" if document.is_conversation else ("dataset" if path.suffix.casefold() == ".json" else "document"),
                "location": f"data/raw/{relative_path}", "filename": relative_path,
                "sourceTimestamp": observed_at, "contentHash": digest, "retrievedAt": run_at,
                "confidence": confidence, "authorityLevel": authority,
            }
            merge_document(graph, document, source, observed_at, run_at, changes, result)
            known_source_ids.add(current_source_id)
            manifest["files"][relative_path] = {"sha256": digest, "sourceId": current_source_id, "sourceTimestamp": observed_at, "processedAt": run_at}
            result.files_processed += 1
            changes.record("file-processed", filename=relative_path, sourceId=current_source_id, nodesExtracted=len(document.nodes), edgesExtracted=len(document.edges))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            result.errors.append({"filename": relative_path, "error": str(error)})
            changes.record("file-error", filename=relative_path, error=str(error))
    result.graph_changed = graph != original
    if result.graph_changed:
        previous_generated_at = graph.get("generatedAt")
        graph["generatedAt"] = run_at
        changes.record("graph-generated-at-updated", previous=previous_generated_at, current=run_at)
    assert_append_only(original, graph)
    validate_graph(graph)
    changes.record("run-complete", **result.as_dict())
    if not config.dry_run:
        if result.graph_changed:
            atomic_json_write(config.graph_path, graph)
        if result.files_processed:
            atomic_json_write(config.manifest_path, manifest)
        result.log_path = str(write_change_log(config.log_dir, changes))
    return result


def build_parser() -> argparse.ArgumentParser:
    repository_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Deterministically ingest raw knowledge files into data/graph.json.")
    parser.add_argument("--raw-dir", type=Path, default=repository_root / "data" / "raw", help="Raw input directory (default: data/raw).")
    parser.add_argument("--graph", type=Path, default=repository_root / "data" / "graph.json", help="Canonical graph JSON path.")
    parser.add_argument("--manifest", type=Path, default=repository_root / "data" / "processed" / "ingestion-manifest.json", help="Processed-file manifest path.")
    parser.add_argument("--log-dir", type=Path, default=repository_root / "logs", help="Change-log directory.")
    parser.add_argument("--dry-run", action="store_true", help="Analyze and report without writing graph, manifest, or logs.")
    parser.add_argument("--authority-tier", choices=("auto", *AUTHORITY_LEVELS), default="auto", help="Override source authority for this run.")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    config = IngestionConfig(arguments.raw_dir.resolve(), arguments.graph.resolve(), arguments.manifest.resolve(), arguments.log_dir.resolve(), arguments.dry_run, arguments.authority_tier)
    try:
        result = run_ingestion(config)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, indent=2))
        return 1
    print(json.dumps({"status": "ok" if not result.errors else "completed-with-errors", **result.as_dict()}, indent=2))
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
