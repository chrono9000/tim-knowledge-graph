"""Incremental local intake. Scheduling and remote extraction are never activated here."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Protocol

from .chatgpt_export import validate_export, access_status, _timestamp
from .extraction import Message, Extractor, DeterministicExtractor, extract_document
from .harness import evaluate_proposal
from .ingest import atomic_json_write, canonical_text, iso_timestamp
from .intake import import_export, load_staging, load_private_master, _write_log, SENSITIVE_PATTERN
from .safety import locked, transactional, state_dir, recover


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


class SourceProvider(Protocol):
    """Future authorized connectors return normalized messages, never graph mutations."""
    name: str
    def conversations(self, path: Path): ...


class PrivateExportSource:
    name = 'private-export-v1'
    def conversations(self, path):
        if path.suffix.casefold() in {'.md', '.txt'}:
            if path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError('Private prose file exceeds 2 MiB')
            text = path.read_text(encoding='utf-8-sig')
            # Untagged standalone prose has unknown authorship; do not assume it is Tim.
            stamp = _timestamp(path.stat().st_mtime)
            yield {'id': 'prose-' + digest(text), 'timestamp': stamp, 'project': [],
                   'messages': (Message('document', 'assistant', text, stamp),)}
            return
        export = validate_export(path)
        if export.failures:
            raise ValueError('Invalid export records; use validate-chatgpt to review locally')
        for item in export.conversations:
            mapping = item['mapping']
            # Use the selected branch if provided; do not combine alternate edited replies.
            current = item.get('current_node')
            nodes = list(mapping)
            if current:
                nodes, seen = [], set()
                while current:
                    if current in seen or current not in mapping:
                        raise ValueError('Invalid conversation branch')
                    seen.add(current)
                    nodes.append(current)
                    current = mapping[current].get('parent')
                nodes.reverse()
            messages = []
            for key in nodes:
                entry = mapping[key]
                if not isinstance(entry, dict):
                    raise ValueError('Invalid message mapping')
                message = entry.get('message')
                if not message:
                    continue
                if not isinstance(message, dict):
                    raise ValueError('Invalid message')
                content = message.get('content') or {}
                parts = content.get('parts', [])
                if not isinstance(parts, list):
                    raise ValueError('Invalid message content')
                # Attachments, images, audio and tool payloads are not read.
                text = '\n'.join(p for p in parts if isinstance(p, str))
                if text:
                    messages.append(Message(str(message.get('id') or key), str((message.get('author') or {}).get('role') or 'unknown'), text,
                                            _timestamp(message.get('create_time')) or item['_validated_timestamp'] or export.source_timestamp))
            yield {'id': item['_validated_id'], 'timestamp': item['_validated_timestamp'] or export.source_timestamp,
                   'project': item['_validated_project'], 'messages': tuple(messages), 'branchUncertain': not bool(item.get('current_node'))}


@transactional
def stage_conversation(config, path, conversation, provider):
    messages = conversation['messages']
    identity = digest({'conversation': conversation['id'], 'messages': [m.__dict__ for m in messages],
                       'project': conversation['project'], 'provider': provider.name, 'version': provider.version})
    existing = next((b for b in load_staging(config.staging_path)['batches'] if b.get('contentHash') == identity), None)
    if existing:
        return {'processed': 0, 'skipped': 1, 'proposals': 0}
    document, evidence = extract_document(messages, provider)
    # The production boundary never accepts authority from conversation text or a model.
    normalized = json.dumps([m.__dict__ for m in messages], sort_keys=True, ensure_ascii=False).encode()
    result = import_export(path, config, 'unknown', prepared=(normalized, document, conversation['timestamp']),
                           identity_hash=identity, dry_run=True)
    batch = result.details['batch']
    if conversation.get('containerHash'):
        batch['containerContentHash'] = conversation['containerHash']
    batch['conversation'] = {'id': conversation['id'], 'project': conversation['project'], 'revision': identity,
                             'provider': provider.name, 'providerVersion': provider.version,
                             'branchUncertain': conversation.get('branchUncertain', False)}
    from .harness import load_harness
    harness = load_harness(config.harness_path)
    prior_nodes = load_private_master(config)['nodes']
    queue = load_staging(config.staging_path)
    prior_nodes += [p['record'] for b in queue['batches'] for p in b['proposals'] if p['recordType'] == 'node' and p['status'] != 'rejected']
    for p in batch['proposals']:
        record = p['record']
        p['extractionEvidence'] = evidence.get(canonical_text(record.get('label', '')), [])
        p['provenance'].update({'conversationId': conversation['id'], 'conversationRevision': identity,
                                'contentHash': batch['source']['contentHash'], 'retrievedAt': batch['importedAt']})
        if p['extractionEvidence']:
            times = [e['sourceTimestamp'] for e in p['extractionEvidence']]
            p['provenance']['firstSeen'] = min(times)
            p['provenance']['lastSeen'] = max(times)
            p['provenance']['sourceTimestamp'] = max(times)
            record['timestamps']['firstSeen'] = min(record['timestamps']['firstSeen'], min(times))
            record['timestamps']['lastSeen'] = max(record['timestamps']['lastSeen'], max(times))
        text = record.get('description', '')
        reasons = set(p['reviewReasons'])
        if conversation.get('branchUncertain'):
            reasons.add('branch-not-identified')
        if SENSITIVE_PATTERN.search(text):
            reasons.add('sensitive-information')
        # Conservative cross-label conflict candidates: never silently reconcile prose.
        words = set(canonical_text(text).split()) - {'the', 'a', 'to', 'is', 'for', 'in', 'of', 'i'}
        conflicts = []
        if p['recordType'] == 'node':
            for old in prior_nodes:
                old_words = set(canonical_text(old.get('description', '')).split()) - {'the', 'a', 'to', 'is', 'for', 'in', 'of', 'i'}
                if old['id'] != record['id'] and len(words & old_words) >= 2 and len(words & old_words) / max(1, min(len(words), len(old_words))) >= 0.5:
                    conflicts.append(old)
        if conflicts:
            p['relatedClaims'] = conflicts
            reasons.add('possible-contradiction')
            p['kind'] = 'contradiction'
            if any(old.get('authorityLevel') != 'unknown' for old in conflicts):
                reasons.add('authority-conflict')
        if record.get('statementType') == 'superseded':
            reasons.add('possible-supersession')
            p['kind'] = 'supersession'
        p['reviewReasons'] = sorted(reasons)
        p['status'] = 'needs-review' if reasons else 'pending'
        previous = p.get('previousRecord') or (max(conflicts, key=lambda x: {'unknown': 0, 'tertiary': 1, 'secondary': 2, 'primary': 3, 'owner': 4}.get(x.get('authorityLevel'), 0)) if conflicts else None)
        p['policyDecision'] = evaluate_proposal(harness, record, batch['source'], reasons, previous, p.get('proposedRecord'))
        p['policyDecision']['evaluatedRuleIds'] = [rule['id'] for rule in harness['rules']]
    queue['batches'].append(batch)
    atomic_json_write(config.staging_path, queue)
    _write_log(config, 'stage-conversation', {'batchId': batch['id'], 'proposalIds': [p['id'] for p in batch['proposals']],
                                             'ruleIds': [r['id'] for r in harness['rules']]})
    return {'processed': 1, 'skipped': 0, 'proposals': len(batch['proposals'])}


@transactional
def save_manifest(config, manifest):
    atomic_json_write(state_dir(config) / 'manifest.json', manifest)


def run_daily(config, provider: Extractor | None = None, source: SourceProvider | None = None):
    provider, source = provider or DeterministicExtractor(), source or PrivateExportSource()
    with locked(config):
        if (state_dir(config) / 'STOP').exists():
            return {'stopped': True, 'processed': 0, 'skipped': 0, 'failures': []}
        if not access_status(config).get('approved'):
            raise ValueError('First approve the private access design: python -m agent approve-access --mode local-only')
        recover(config, staging_only=True)
        root = config.public_graph_path.parent / 'raw'
        manifest_path = state_dir(config) / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {'files': {}}
        report = {'processed': 0, 'skipped': 0, 'proposals': 0, 'unchangedFiles': 0, 'failures': [], 'stopped': False}
        for path in sorted(root.rglob('*')):
            if path.name in {'README.md', '.gitkeep'} or not path.is_file() or path.suffix.casefold() not in {'.zip', '.json', '.txt', '.md'}:
                continue
            if (state_dir(config) / 'STOP').exists():
                report['stopped'] = True
                break
            key = str(path.relative_to(root))
            try:
                for part in [path, *path.parents]:
                    if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
                        raise ValueError('Linked intake paths are forbidden')
                if path.stat().st_size > 512 * 1024 * 1024:
                    raise ValueError('Source exceeds size limit')
                with path.open('rb') as handle:
                    hasher = hashlib.sha256()
                    for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                        hasher.update(chunk)
                fingerprint = hasher.hexdigest() + ':' + provider.name + ':' + provider.version
                if manifest['files'].get(key) == fingerprint:
                    report['unchangedFiles'] += 1
                    continue
                for conversation in source.conversations(path):
                    conversation['containerHash'] = hasher.hexdigest()
                    if (state_dir(config) / 'STOP').exists():
                        report['stopped'] = True
                        break
                    result = stage_conversation(config, path, conversation, provider)
                    for count in ('processed', 'skipped', 'proposals'):
                        report[count] += result[count]
                if not report['stopped']:
                    # Detect an export replaced during processing; requeue it on next run.
                    with path.open('rb') as handle:
                        verify = hashlib.sha256()
                        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                            verify.update(chunk)
                    if verify.hexdigest() != hasher.hexdigest():
                        raise ValueError('Source changed during processing; rerun')
                    manifest['files'][key] = fingerprint
                    save_manifest(config, manifest)
            except (ValueError, OSError, RuntimeError, TypeError, KeyError) as error:
                # Do not persist model errors or parser snippets containing source text.
                report['failures'].append({'privateFile': key, 'errorType': type(error).__name__, 'nextAction': 'Validate this source locally and rerun; no failed revision was marked complete.'})
        report['privateGraphUnchanged'] = True
        report['publicGraphUnchanged'] = True
        return report


def set_stopped(config, stopped):
    # Deliberately independent of the run lock: a running scan sees this between conversations.
    path = state_dir(config) / 'STOP'
    path.parent.mkdir(parents=True, exist_ok=True)
    if stopped:
        atomic_json_write(path, {'stopped': True})
    else:
        with locked(config):
            path.unlink(missing_ok=True)
    import uuid
    atomic_json_write(state_dir(config) / 'audit' / (uuid.uuid4().hex + '.json'),
                      {'action': 'stop' if stopped else 'resume', 'timestamp': iso_timestamp(config.clock()),
                       'ruleIds': ['AUDIT-001', 'RECOVERY-001']})
    return {'stopped': stopped}
