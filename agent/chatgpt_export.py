"""Validated, resumable and review-gated handling of ChatGPT data exports."""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .ingest import ExtractedDocument, atomic_json_write, conversation_messages, extract_text, iso_timestamp, parse_timestamp
from .intake import IntakeConfig, WorkflowResult, import_export, load_staging


MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_CONVERSATIONS_BYTES = 256 * 1024 * 1024
MAX_CONVERSATIONS = 100_000
MAX_MESSAGES_PER_CONVERSATION = 10_000
ACCESS_MODES = {"local-only", "tailscale", "cloudflare-access"}


@dataclass(frozen=True)
class ExportFilters:
    conversations: tuple[str, ...] = ()
    projects: tuple[str, ...] = ()
    title: str | None = None
    from_date: date | None = None
    to_date: date | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "conversations": sorted(self.conversations),
            "projects": sorted(self.projects),
            "title": self.title,
            "fromDate": self.from_date.isoformat() if self.from_date else None,
            "toDate": self.to_date.isoformat() if self.to_date else None,
        }


@dataclass
class ValidatedExport:
    path: Path
    raw: bytes
    conversations: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)
    source_timestamp: str = ""


def access_approval_path(config: IntakeConfig) -> Path:
    return config.private_graph_path.parent / "access-approval.json"


def approve_access_design(config: IntakeConfig, mode: str) -> dict[str, Any]:
    if mode not in ACCESS_MODES:
        raise ValueError(f"Unsupported private-access mode: {mode}")
    record = {
        "schemaVersion": "1.0.0",
        "approved": True,
        "mode": mode,
        "approvedAt": iso_timestamp(config.clock()),
        "notice": "Local private state. Never commit or deploy this file.",
    }
    atomic_json_write(access_approval_path(config), record)
    return record


def access_status(config: IntakeConfig) -> dict[str, Any]:
    path = access_approval_path(config)
    if not path.exists():
        return {"approved": False, "recommendedMode": "local-only", "approvalFile": str(path)}
    value = json.loads(path.read_text(encoding="utf-8"))
    approved = bool(value.get("approved")) and value.get("mode") in ACCESS_MODES
    return {"approved": approved, "mode": value.get("mode"), "approvedAt": value.get("approvedAt"), "approvalFile": str(path)}


def _regular_private_file(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("Export path must be a regular, non-symlink file")
    path = path.resolve()
    if not path.is_file():
        raise ValueError("Export path must be a regular, non-symlink file")
    return path


def _assert_private_location(path: Path, config: IntakeConfig) -> None:
    repository_root = config.public_graph_path.resolve().parent.parent
    raw_root = repository_root / "data" / "raw"
    resolved = path.resolve()
    if resolved.is_relative_to(repository_root) and not resolved.is_relative_to(raw_root):
        raise ValueError("An export inside the repository must be placed under the Git-ignored data/raw directory")


def _read_payload(path: Path) -> tuple[bytes, bytes]:
    path = _regular_private_file(path)
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("Export exceeds the 512 MiB archive limit")
    raw = path.read_bytes()
    if path.suffix.casefold() == ".json":
        if len(raw) > MAX_CONVERSATIONS_BYTES:
            raise ValueError("conversations.json exceeds the 256 MiB limit")
        return raw, raw
    if path.suffix.casefold() != ".zip":
        raise ValueError("ChatGPT exports must be conversations.json or a .zip containing it")
    with zipfile.ZipFile(path) as archive:
        matches = []
        for member in archive.infolist():
            member_path = PurePosixPath(member.filename.replace("\\", "/"))
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError("ZIP contains an unsafe path")
            if member_path.name.casefold() == "conversations.json" and not member.is_dir():
                matches.append(member)
        if len(matches) != 1:
            raise ValueError("ZIP must contain exactly one conversations.json")
        member = matches[0]
        if member.flag_bits & 0x1:
            raise ValueError("Encrypted ZIP exports are not supported")
        if member.file_size > MAX_CONVERSATIONS_BYTES:
            raise ValueError("conversations.json exceeds the 256 MiB limit")
        payload = archive.read(member)
        if len(payload) > MAX_CONVERSATIONS_BYTES:
            raise ValueError("Expanded conversations.json exceeds the 256 MiB limit")
        return raw, payload


def _conversation_project(item: dict[str, Any]) -> tuple[str, str]:
    nested = item.get("project") if isinstance(item.get("project"), dict) else {}
    project_id = str(item.get("project_id") or nested.get("id") or "").strip()
    project_name = str(item.get("project_title") or item.get("project_name") or nested.get("title") or nested.get("name") or "").strip()
    return project_id, project_name


def _timestamp(value: Any) -> str | None:
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return iso_timestamp(datetime.fromtimestamp(value, timezone.utc))
        if isinstance(value, str) and value.strip():
            return iso_timestamp(parse_timestamp(value))
    except (OSError, OverflowError, ValueError):
        pass
    return None


def validate_export(path: Path) -> ValidatedExport:
    raw, payload = _read_payload(path)
    try:
        value = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Malformed conversations.json: {error}") from error
    if not isinstance(value, list):
        raise ValueError("conversations.json must contain a top-level array")
    if len(value) > MAX_CONVERSATIONS:
        raise ValueError(f"Export exceeds the {MAX_CONVERSATIONS:,}-conversation limit")
    valid: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    timestamps: list[str] = []
    for number, item in enumerate(value, 1):
        if not isinstance(item, dict):
            failures.append({"conversation": str(number), "error": "Conversation is not an object"})
            continue
        conversation_id = str(item.get("id") or item.get("conversation_id") or "").strip()
        mapping = item.get("mapping")
        if not conversation_id or not isinstance(mapping, dict):
            failures.append({"conversation": conversation_id or str(number), "error": "Missing conversation id or mapping"})
            continue
        if len(mapping) > MAX_MESSAGES_PER_CONVERSATION:
            failures.append({"conversation": conversation_id, "error": "Conversation exceeds the message limit"})
            continue
        item = dict(item)
        item["_validated_id"] = conversation_id
        item["_validated_title"] = str(item.get("title") or "Untitled conversation").strip()
        item["_validated_timestamp"] = _timestamp(item.get("update_time")) or _timestamp(item.get("create_time"))
        item["_validated_project"] = _conversation_project(item)
        if item["_validated_timestamp"]:
            timestamps.append(item["_validated_timestamp"])
        valid.append(item)
    observed = max(timestamps, key=parse_timestamp) if timestamps else iso_timestamp(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc))
    return ValidatedExport(path.resolve(), raw, valid, failures, observed)


def _matches(item: dict[str, Any], filters: ExportFilters) -> bool:
    if filters.conversations and item["_validated_id"] not in filters.conversations:
        return False
    if filters.projects:
        project_id, project_name = item["_validated_project"]
        wanted = {value.casefold() for value in filters.projects}
        if project_id.casefold() not in wanted and project_name.casefold() not in wanted:
            return False
    if filters.title and filters.title.casefold() not in item["_validated_title"].casefold():
        return False
    timestamp = item["_validated_timestamp"]
    if (filters.from_date or filters.to_date) and not timestamp:
        return False
    observed = parse_timestamp(timestamp).date() if timestamp else None
    if filters.from_date and observed and observed < filters.from_date:
        return False
    if filters.to_date and observed and observed > filters.to_date:
        return False
    return True


def _extract_selected(selected: list[dict[str, Any]]) -> ExtractedDocument:
    document = ExtractedDocument(is_conversation=True)
    for conversation in selected:
        for role, text in conversation_messages(conversation.get("mapping", {})):
            confidence = 0.88 if role.casefold() == "user" else 0.78
            extracted = extract_text(text, base_confidence=confidence, allow_unstructured=False)
            for node in extracted.nodes.values():
                document.add_node(node)
            for edge in extracted.edges.values():
                document.add_edge(edge)
    return document


def _summary(batch: dict[str, Any], validated: ValidatedExport, selected: list[dict[str, Any]]) -> dict[str, Any]:
    proposals = batch["proposals"]
    reasons = [reason for proposal in proposals for reason in proposal.get("reviewReasons", [])]
    project_keys = {project_id or project_name for project_id, project_name in (item["_validated_project"] for item in selected) if project_id or project_name}
    return {
        "conversations": len(selected),
        "availableConversations": len(validated.conversations),
        "projects": len(project_keys),
        "proposedNodes": sum(item.get("recordType") == "node" for item in proposals),
        "proposedEdges": sum(item.get("recordType") == "edge" for item in proposals),
        "duplicates": sum(item.get("kind") == "duplicate" for item in proposals),
        "contradictions": sum(item.get("kind") == "contradiction" for item in proposals),
        "sensitiveRecords": sum("sensitive-information" in item.get("reviewReasons", []) for item in proposals),
        "lowConfidenceRecords": reasons.count("low-confidence"),
        "failures": len(validated.failures),
        "failureDetails": validated.failures,
    }


def process_export(path: Path, config: IntakeConfig, filters: ExportFilters, *, stage: bool = False, authority_tier: str = "auto") -> WorkflowResult:
    if stage and not access_status(config).get("approved"):
        raise ValueError("Approve the private-access design before staging: python -m agent approve-access --mode local-only")
    validated = validate_export(path)
    if stage:
        _assert_private_location(validated.path, config)
        if validated.failures:
            raise ValueError(f"Export contains {len(validated.failures)} invalid conversation record(s); review the dry-run failures before staging")
    selected = [item for item in validated.conversations if _matches(item, filters)]
    if not selected:
        raise ValueError("No valid conversations match the selected filters")
    document = _extract_selected(selected)
    selection = {
        "filters": filters.as_dict(),
        "conversationIds": sorted(item["_validated_id"] for item in selected),
    }
    full_hash = hashlib.sha256(validated.raw).hexdigest()
    selection_hash = hashlib.sha256(json.dumps(selection, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    identity_hash = hashlib.sha256(f"{full_hash}:{selection_hash}".encode()).hexdigest()
    batch_metadata = {
        "exportFormat": "chatgpt-conversations-v1",
        "containerContentHash": full_hash,
        "selectionHash": selection_hash,
        "selectedConversationIds": selection["conversationIds"],
        "selection": filters.as_dict(),
    }
    result = import_export(
        validated.path,
        config,
        authority_tier,
        prepared=(validated.raw, document, validated.source_timestamp),
        identity_hash=identity_hash,
        batch_metadata=batch_metadata,
        dry_run=not stage,
    )
    batch = result.details.pop("batch", None)
    if batch is None:
        staging_batch = next(batch for batch in load_staging(config.staging_path)["batches"] if batch["id"] == result.batch_id)
        batch = staging_batch
    result.details["summary"] = _summary(batch, validated, selected)
    result.details["filters"] = filters.as_dict()
    result.details["privateGraphUnchanged"] = True
    result.details["publicGraphUnchanged"] = True
    return result


def validate_summary(path: Path) -> dict[str, Any]:
    validated = validate_export(path)
    projects = {project_id or project_name for project_id, project_name in (item["_validated_project"] for item in validated.conversations) if project_id or project_name}
    return {
        "valid": not validated.failures,
        "conversations": len(validated.conversations),
        "projects": len(projects),
        "failures": len(validated.failures),
        "failureDetails": validated.failures,
        "sourceTimestamp": validated.source_timestamp,
        "contentHash": hashlib.sha256(validated.raw).hexdigest(),
    }
