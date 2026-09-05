"""Private, review-gated intake workflow for knowledge graph exports."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .ingest import (
    AUTHORITY_CONFIDENCE,
    AUTHORITY_LEVELS,
    AUTHORITY_RANK,
    ChangeLog,
    ExtractedDocument,
    IngestionResult,
    assert_append_only,
    atomic_json_write,
    canonical_text,
    extract_json,
    extract_text,
    infer_authority,
    iso_timestamp,
    merge_document,
    parse_timestamp,
    read_source,
    source_confidence,
    source_id,
    source_timestamp,
    utc_now,
    validate_graph,
)


REVIEW_STATUSES = {"pending", "approved-private", "approved-public", "rejected", "needs-review"}
SUPPORTED_EXPORT_SUFFIXES = {".txt", ".md", ".json", ".zip"}
ZIP_MEMBER_SUFFIXES = {".txt", ".md", ".json"}
MAX_ZIP_FILES = 1_000
MAX_ZIP_BYTES = 25 * 1024 * 1024
LOW_CONFIDENCE = 0.7
SENSITIVE_PATTERN = re.compile(
    r"(?ix)(?:\b\d{3}-\d{2}-\d{4}\b|\b(?:password|secret|api[_ -]?key|access[_ -]?token)\b|"
    r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}|(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4})"
)
NEGATION_PATTERN = re.compile(r"\b(?:not|never|no longer|isn't|wasn't|won't|cannot|can't)\b", re.IGNORECASE)


@dataclass(frozen=True)
class IntakeConfig:
    public_graph_path: Path
    private_graph_path: Path
    staging_path: Path
    log_dir: Path
    clock: Any = utc_now


@dataclass
class WorkflowResult:
    action: str
    batch_id: str | None = None
    proposal_ids: list[str] = field(default_factory=list)
    proposals_changed: int = 0
    graph_changed: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "batchId": self.batch_id,
            "proposalIds": self.proposal_ids,
            "proposalsChanged": self.proposals_changed,
            "graphChanged": self.graph_changed,
            **self.details,
        }


def default_config() -> IntakeConfig:
    root = Path(__file__).resolve().parents[1]
    return IntakeConfig(
        public_graph_path=root / "data" / "graph.json",
        private_graph_path=root / "data" / "private" / "master-graph.json",
        staging_path=root / "data" / "staging" / "proposals.json",
        log_dir=root / "logs",
    )


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return copy.deepcopy(default)
    return json.loads(path.read_text(encoding="utf-8"))


def load_staging(path: Path) -> dict[str, Any]:
    value = _load_json(path, {"schemaVersion": "1.0.0", "batches": []})
    if not isinstance(value, dict) or not isinstance(value.get("batches"), list):
        raise ValueError("Invalid proposal staging document")
    for batch in value["batches"]:
        if not isinstance(batch, dict) or not isinstance(batch.get("proposals"), list):
            raise ValueError("Invalid proposal batch")
        for proposal in batch["proposals"]:
            if proposal.get("status") not in REVIEW_STATUSES:
                raise ValueError(f"Invalid proposal status: {proposal.get('status')}")
    return value


def load_private_master(config: IntakeConfig) -> dict[str, Any]:
    source = config.private_graph_path if config.private_graph_path.exists() else config.public_graph_path
    graph = json.loads(source.read_text(encoding="utf-8"))
    validate_graph(graph)
    return graph


def _merge_documents(target: ExtractedDocument, source: ExtractedDocument) -> None:
    for candidate in source.nodes.values():
        target.add_node(candidate)
    for candidate in source.edges.values():
        target.add_edge(candidate)
    target.is_conversation = target.is_conversation or source.is_conversation
    target.metadata.update(source.metadata)


def _timestamp_value(value: Any) -> str | None:
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return iso_timestamp(datetime.fromtimestamp(value, timezone.utc))
        if isinstance(value, str) and value.strip():
            return iso_timestamp(parse_timestamp(value.strip()))
    except (OSError, OverflowError, ValueError):
        return None
    return None


def _export_timestamps(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, list):
        for item in value:
            found.extend(_export_timestamps(item))
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in {"source_timestamp", "sourceTimestamp", "create_time", "update_time", "created_at", "updated_at"}:
                parsed = _timestamp_value(item)
                if parsed:
                    found.append(parsed)
            elif key not in {"content", "parts", "text"}:
                found.extend(_export_timestamps(item))
    return found


def _read_export(path: Path) -> tuple[bytes, ExtractedDocument, str]:
    suffix = path.suffix.casefold()
    if suffix not in SUPPORTED_EXPORT_SUFFIXES:
        raise ValueError(f"Unsupported export type: {suffix or '(none)'}")
    if suffix != ".zip":
        raw, document = read_source(path)
        if suffix == ".json":
            value = json.loads(raw.decode("utf-8-sig"))
            timestamps = _export_timestamps(value)
            if timestamps:
                document.metadata["source-timestamp"] = max(timestamps, key=parse_timestamp)
        return raw, document, source_timestamp(path, document)

    raw = path.read_bytes()
    document = ExtractedDocument()
    timestamps: list[str] = []
    member_count = 0
    total_bytes = 0
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            member_path = Path(member.filename)
            if member.is_dir() or member_path.suffix.casefold() not in ZIP_MEMBER_SUFFIXES:
                continue
            if member.flag_bits & 0x1:
                raise ValueError("Encrypted ZIP members are not supported")
            if member.file_size < 0 or member.file_size > MAX_ZIP_BYTES:
                raise ValueError("ZIP member exceeds the private intake size limit")
            member_count += 1
            total_bytes += member.file_size
            if member_count > MAX_ZIP_FILES or total_bytes > MAX_ZIP_BYTES:
                raise ValueError("ZIP export exceeds the private intake limits")
            content = archive.read(member)
            text = content.decode("utf-8-sig")
            if member_path.suffix.casefold() == ".json":
                value = json.loads(text)
                _merge_documents(document, extract_json(value))
                timestamps.extend(_export_timestamps(value))
            else:
                _merge_documents(document, extract_text(text))
            try:
                member_time = datetime(*member.date_time, tzinfo=timezone.utc)
                timestamps.append(iso_timestamp(member_time))
            except (TypeError, ValueError):
                pass
    if not member_count:
        raise ValueError("ZIP contains no supported UTF-8 .txt, .md, or .json export files")
    if timestamps:
        document.metadata["source-timestamp"] = max(timestamps, key=parse_timestamp)
    observed = source_timestamp(path, document)
    return raw, document, observed


def _record_provenance(record: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    timestamps = record.get("timestamps", {})
    return {
        "sourceId": source["id"],
        "sourceFilename": source["filename"],
        "sourceTimestamp": source["sourceTimestamp"],
        "confidence": record.get("confidence", source["confidence"]),
        "authorityLevel": record.get("authorityLevel", source["authorityLevel"]),
        "firstSeen": timestamps.get("firstSeen", source["sourceTimestamp"]),
        "lastSeen": timestamps.get("lastSeen", source["sourceTimestamp"]),
    }


def _assertion_tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", text.casefold()) if token not in {"a", "an", "the", "is", "was", "will", "be", "to"}}


def _review_reasons(record: dict[str, Any], previous: dict[str, Any] | None, source: dict[str, Any], proposed: dict[str, Any] | None = None) -> list[str]:
    reasons: list[str] = []
    review_record = proposed or record
    searchable = " ".join(str(review_record.get(key, "")) for key in ("label", "description", "relationship"))
    if SENSITIVE_PATTERN.search(searchable):
        reasons.append("sensitive-information")
    if float(review_record.get("confidence", record.get("confidence", source["confidence"]))) < LOW_CONFIDENCE:
        reasons.append("low-confidence")
    if previous:
        reasons.append("possible-duplicate")
        prior_authority = previous.get("authorityLevel", "unknown")
        if prior_authority != source["authorityLevel"]:
            reasons.append("authority-conflict")
        old_text = " ".join(str(previous.get(key, "")) for key in ("label", "description"))
        overlap = _assertion_tokens(searchable) & _assertion_tokens(old_text)
        if overlap and bool(NEGATION_PATTERN.search(searchable)) != bool(NEGATION_PATTERN.search(old_text)):
            reasons.append("possible-contradiction")
    return sorted(set(reasons))


def _proposal(batch_id: str, kind: str, action: str, record: dict[str, Any], source: dict[str, Any], previous: dict[str, Any] | None = None, proposed: dict[str, Any] | None = None) -> dict[str, Any]:
    reasons = _review_reasons(record, previous, source, proposed)
    identity = f"{batch_id}\0{kind}\0{record['id']}"
    return {
        "id": f"proposal-{hashlib.sha256(identity.encode()).hexdigest()[:16]}",
        "kind": "contradiction" if "possible-contradiction" in reasons else kind,
        "recordType": kind if kind in {"source", "node", "edge"} else ("edge" if "source" in record and "target" in record else "node"),
        "action": action,
        "targetId": record["id"],
        "status": "needs-review" if reasons else "pending",
        "reviewReasons": reasons,
        "record": record,
        "proposedRecord": proposed,
        "previousRecord": previous,
        "provenance": _record_provenance(record, source),
        "reviewedAt": None,
        "publishedAt": None,
    }


def import_export(path: Path, config: IntakeConfig, authority_override: str = "auto") -> WorkflowResult:
    path = path.resolve()
    if not path.is_file() or path.is_symlink():
        raise ValueError("Export path must be a regular file")
    raw, document, observed_at = _read_export(path)
    digest = hashlib.sha256(raw).hexdigest()
    staging = load_staging(config.staging_path)
    existing_batch = next((batch for batch in staging["batches"] if batch.get("contentHash") == digest), None)
    if existing_batch:
        return WorkflowResult("import", existing_batch["id"], details={"duplicateImport": True, "proposalCount": len(existing_batch["proposals"])})
    now = iso_timestamp(config.clock())
    master = load_private_master(config)
    candidate = copy.deepcopy(master)
    authority = infer_authority(path, document, authority_override)
    confidence = source_confidence(document, authority)
    private_source_id = source_id(path.name, digest)
    source = {
        "id": private_source_id,
        "title": path.stem,
        "type": "conversation" if document.is_conversation else ("dataset" if path.suffix.casefold() in {".json", ".zip"} else "document"),
        "location": f"private-source:{private_source_id}",
        "filename": path.name,
        "sourceTimestamp": observed_at,
        "contentHash": digest,
        "retrievedAt": now,
        "confidence": confidence,
        "authorityLevel": authority,
    }
    changes = ChangeLog(now.replace(":", "").replace("-", ""), now)
    result = IngestionResult(dry_run=False)
    merge_document(candidate, document, source, observed_at, now, changes, result)
    validate_graph(candidate)
    batch_id = f"batch-{digest[:16]}"
    proposals: list[dict[str, Any]] = []
    proposals.append(_proposal(batch_id, "source", "add", source, source))
    for collection, kind in (("nodes", "node"), ("edges", "edge")):
        before = {item["id"]: item for item in master[collection]}
        for record in candidate[collection]:
            previous = before.get(record["id"])
            if previous != record:
                proposed: dict[str, Any] | None = None
                if collection == "nodes":
                    candidate_node = document.nodes.get(canonical_text(record.get("label", "")))
                    if candidate_node:
                        proposed = {
                            "label": candidate_node.label,
                            "description": candidate_node.description,
                            "entityType": candidate_node.entity_type,
                            "statementType": candidate_node.statement_type,
                            "confidence": candidate_node.confidence,
                            "authorityLevel": authority,
                        }
                proposals.append(_proposal(batch_id, "duplicate" if previous else kind, "update" if previous else "add", record, source, previous, proposed))
    batch = {
        "id": batch_id,
        "importedAt": now,
        "sourceFilename": path.name,
        "sourceTimestamp": observed_at,
        "contentHash": digest,
        "private": True,
        "source": source,
        "proposals": proposals,
    }
    staging["batches"].append(batch)
    atomic_json_write(config.staging_path, staging)
    _write_log(config, "import", {
        "batchId": batch_id,
        "sourceFilename": path.name,
        "proposalIds": [item["id"] for item in proposals],
        "proposalCount": len(proposals),
    })
    return WorkflowResult("import", batch_id, [item["id"] for item in proposals], details={"duplicateImport": False, "proposalCount": len(proposals)})


def preview(config: IntakeConfig, statuses: Iterable[str] | None = None) -> dict[str, Any]:
    staging = load_staging(config.staging_path)
    selected = set(statuses or REVIEW_STATUSES)
    proposals = [
        {"batchId": batch["id"], "sourceFilename": batch["sourceFilename"], **proposal}
        for batch in staging["batches"] for proposal in batch["proposals"] if proposal["status"] in selected
    ]
    return {"proposalCount": len(proposals), "proposals": proposals}


def _select(staging: dict[str, Any], proposal_ids: Iterable[str] | None, batch_id: str | None, select_all: bool, allowed: set[str]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    requested = set(proposal_ids or [])
    selected: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for batch in staging["batches"]:
        for proposal in batch["proposals"]:
            if proposal["status"] not in allowed:
                continue
            if select_all or (batch_id and batch["id"] == batch_id) or proposal["id"] in requested:
                selected.append((batch, proposal))
    if not selected:
        raise ValueError("No matching reviewable proposals")
    missing = requested - {proposal["id"] for _, proposal in selected}
    if missing:
        raise ValueError(f"Unknown or non-reviewable proposal IDs: {sorted(missing)}")
    return selected


def _merge_provenance(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
    for source in incoming.get("sourceIds", []):
        if source not in existing.setdefault("sourceIds", []):
            existing["sourceIds"].append(source)
    old = existing.setdefault("timestamps", {})
    new = incoming.get("timestamps", {})
    old["firstSeen"] = min(filter(None, (old.get("firstSeen"), new.get("firstSeen"))), key=parse_timestamp)
    old["lastSeen"] = max(filter(None, (old.get("lastSeen"), new.get("lastSeen"))), key=parse_timestamp)
    old["updatedAt"] = max(filter(None, (old.get("updatedAt"), new.get("updatedAt"))), key=parse_timestamp)
    existing["confidence"] = max(float(existing.get("confidence", 0)), float(incoming.get("confidence", 0)))
    if AUTHORITY_RANK.get(incoming.get("authorityLevel", "unknown"), 0) > AUTHORITY_RANK.get(existing.get("authorityLevel", "unknown"), 0):
        existing["authorityLevel"] = incoming["authorityLevel"]


def _apply_record(graph: dict[str, Any], collection: str, record: dict[str, Any]) -> None:
    existing = next((item for item in graph[collection] if item["id"] == record["id"]), None)
    if existing is None:
        graph[collection].append(copy.deepcopy(record))
    elif collection != "sources":
        _merge_provenance(existing, record)


def approve(config: IntakeConfig, visibility: str, proposal_ids: Iterable[str] | None = None, batch_id: str | None = None, select_all: bool = False) -> WorkflowResult:
    if visibility not in {"private", "public"}:
        raise ValueError("Approval visibility must be private or public")
    staging = load_staging(config.staging_path)
    allowed = {"pending", "needs-review", "approved-private"} if visibility == "public" else {"pending", "needs-review"}
    selected = _select(staging, proposal_ids, batch_id, select_all, allowed)
    master_before = load_private_master(config)
    master = copy.deepcopy(master_before)
    now = iso_timestamp(config.clock())
    touched: list[str] = []
    selected_batches = {batch["id"] for batch, _ in selected}
    for batch in staging["batches"]:
        if batch["id"] not in selected_batches:
            continue
        source_proposal = next(item for item in batch["proposals"] if item["kind"] == "source")
        if source_proposal["status"] in {"pending", "needs-review"}:
            selected.append((batch, source_proposal))
    seen: set[str] = set()
    ordered = sorted(selected, key=lambda item: {"source": 0, "node": 1, "duplicate": 1, "contradiction": 1, "edge": 2}.get(item[1]["kind"], 1))
    for _, proposal in ordered:
        if proposal["id"] in seen:
            continue
        seen.add(proposal["id"])
        record = proposal["record"]
        collection = "sources" if proposal["kind"] == "source" else ("edges" if "source" in record and "target" in record else "nodes")
        _apply_record(master, collection, record)
        proposal["status"] = f"approved-{visibility}"
        proposal["reviewedAt"] = now
        touched.append(proposal["id"])
    assert_append_only(master_before, master)
    validate_graph(master)
    atomic_json_write(config.private_graph_path, master)
    atomic_json_write(config.staging_path, staging)
    _write_log(config, f"approve-{visibility}", {"proposalIds": touched})
    return WorkflowResult(f"approve-{visibility}", proposal_ids=touched, proposals_changed=len(touched), graph_changed=master != master_before)


def reject(config: IntakeConfig, proposal_ids: Iterable[str] | None = None, batch_id: str | None = None, select_all: bool = False) -> WorkflowResult:
    staging = load_staging(config.staging_path)
    selected = _select(staging, proposal_ids, batch_id, select_all, {"pending", "needs-review"})
    now = iso_timestamp(config.clock())
    touched: list[str] = []
    for _, proposal in selected:
        proposal["status"] = "rejected"
        proposal["reviewedAt"] = now
        touched.append(proposal["id"])
    atomic_json_write(config.staging_path, staging)
    _write_log(config, "reject", {"proposalIds": touched})
    return WorkflowResult("reject", proposal_ids=touched, proposals_changed=len(touched))


def _public_source(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": source["id"],
        "title": "Approved private source",
        "type": source["type"],
        "location": f"private-source:{source['id']}",
        "sourceTimestamp": source["sourceTimestamp"],
        "retrievedAt": source["retrievedAt"],
        "confidence": source["confidence"],
        "authorityLevel": source["authorityLevel"],
    }


def publish(config: IntakeConfig) -> WorkflowResult:
    staging = load_staging(config.staging_path)
    original = json.loads(config.public_graph_path.read_text(encoding="utf-8"))
    validate_graph(original)
    graph = copy.deepcopy(original)
    now = iso_timestamp(config.clock())
    publishable = [(batch, proposal) for batch in staging["batches"] for proposal in batch["proposals"] if proposal["status"] == "approved-public" and not proposal.get("publishedAt")]
    if not publishable:
        return WorkflowResult("publish", details={"publishedCount": 0})
    source_by_batch = {batch["id"]: batch["source"] for batch, _ in publishable}
    for source in source_by_batch.values():
        _apply_record(graph, "sources", _public_source(source))
    ordered = sorted(publishable, key=lambda item: 1 if "source" in item[1]["record"] and "target" in item[1]["record"] else 0)
    published: list[str] = []
    for _, proposal in ordered:
        if proposal["kind"] == "source":
            proposal["publishedAt"] = now
            published.append(proposal["id"])
            continue
        record = proposal["record"]
        collection = "edges" if "source" in record and "target" in record else "nodes"
        _apply_record(graph, collection, record)
        proposal["publishedAt"] = now
        published.append(proposal["id"])
    if graph != original:
        graph["generatedAt"] = now
    assert_append_only(original, graph)
    validate_graph(graph)
    atomic_json_write(config.public_graph_path, graph)
    atomic_json_write(config.staging_path, staging)
    _write_log(config, "publish", {"proposalIds": published})
    return WorkflowResult("publish", proposal_ids=published, proposals_changed=len(published), graph_changed=graph != original, details={"publishedCount": len(published)})


def _write_log(config: IntakeConfig, action: str, details: dict[str, Any]) -> None:
    now = config.clock()
    timestamp = iso_timestamp(now)
    run_id = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    config.log_dir.mkdir(parents=True, exist_ok=True)
    path = config.log_dir / f"intake-{run_id}.jsonl"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({"timestamp": timestamp, "action": action, **details}, sort_keys=True) + "\n")


def build_parser() -> argparse.ArgumentParser:
    defaults = default_config()
    parser = argparse.ArgumentParser(description="Private, review-gated knowledge graph intake.")
    parser.add_argument("--public-graph", type=Path, default=defaults.public_graph_path)
    parser.add_argument("--private-graph", type=Path, default=defaults.private_graph_path)
    parser.add_argument("--staging", type=Path, default=defaults.staging_path)
    parser.add_argument("--log-dir", type=Path, default=defaults.log_dir)
    subparsers = parser.add_subparsers(dest="command", required=True)
    importer = subparsers.add_parser("import", help="Import a private ChatGPT or project export into staging.")
    importer.add_argument("path", type=Path)
    importer.add_argument("--authority-tier", choices=("auto", *AUTHORITY_LEVELS), default="auto")
    previewer = subparsers.add_parser("preview", help="Preview proposals without changing either graph.")
    previewer.add_argument("--status", action="append", choices=sorted(REVIEW_STATUSES))
    for name in ("approve-private", "approve-public", "reject"):
        reviewer = subparsers.add_parser(name)
        reviewer.add_argument("proposal_ids", nargs="*")
        reviewer.add_argument("--batch")
        reviewer.add_argument("--all", action="store_true")
    subparsers.add_parser("publish", help="Publish only approved-public proposals.")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    config = IntakeConfig(arguments.public_graph.resolve(), arguments.private_graph.resolve(), arguments.staging.resolve(), arguments.log_dir.resolve())
    try:
        if arguments.command == "import":
            result: Any = import_export(arguments.path, config, arguments.authority_tier).as_dict()
        elif arguments.command == "preview":
            result = preview(config, arguments.status)
        elif arguments.command == "approve-private":
            result = approve(config, "private", arguments.proposal_ids, arguments.batch, arguments.all).as_dict()
        elif arguments.command == "approve-public":
            result = approve(config, "public", arguments.proposal_ids, arguments.batch, arguments.all).as_dict()
        elif arguments.command == "reject":
            result = reject(config, arguments.proposal_ids, arguments.batch, arguments.all).as_dict()
        else:
            result = publish(config).as_dict()
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(json.dumps({"status": "error", "error": str(error)}, indent=2))
        return 1
    print(json.dumps({"status": "ok", **result}, indent=2))
    return 0
