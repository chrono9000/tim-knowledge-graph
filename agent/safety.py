"""Local OS locks and durable, replayable transactions. No network operations."""
from __future__ import annotations

import copy
import contextvars
import functools
import inspect
import json
import os
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

_transaction = contextvars.ContextVar('knowledge_transaction', default=None)
_held = contextvars.ContextVar('knowledge_locks', default=frozenset())
_mutex = threading.RLock()


def state_dir(config):
    return config.private_graph_path.parent / 'workflow'


def check_paths(config):
    paths = [config.public_graph_path, config.private_graph_path, config.staging_path]
    if len({p.resolve() for p in paths}) != len(paths):
        raise ValueError('Public graph, private graph, and staging must be distinct files')
    for path in [*paths, config.log_dir, state_dir(config)]:
        for part in [path, *path.parents]:
            if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
                raise ValueError('Runtime paths cannot traverse links or junctions')


@contextmanager
def locked(config):
    check_paths(config)
    key = str(config.public_graph_path.resolve())
    if key in _held.get():
        yield
        return
    # Lock is next to the public graph, so alternate private path options still contend.
    path = config.public_graph_path.parent / '.knowledge-workflow.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with _mutex, path.open('a+b') as handle:
        handle.seek(0)
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError('Another knowledge workflow is running; retry when it finishes') from error
        token = _held.set(_held.get() | {key})
        try:
            yield
        finally:
            _held.reset(token)
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def queue_write(path, value):
    tx = _transaction.get()
    if tx is None:
        return False
    tx['writes'][str(path.resolve())] = copy.deepcopy(value)
    return True


def queue_event(action, details):
    tx = _transaction.get()
    if tx is None:
        return False
    tx['events'].append({'action': action, **copy.deepcopy(details)})
    return True


def _write(path, value):
    from .ingest import atomic_json_write
    atomic_json_write(path, value)


def _finish(config, tx):
    allowed = {str(p.resolve()) for p in (config.public_graph_path, config.private_graph_path, config.staging_path,
               config.private_graph_path.parent / 'access-approval.json', state_dir(config) / 'manifest.json')}
    if not set(tx['writes']) <= allowed:
        raise ValueError('Transaction contains an unapproved destination')
    for name, value in tx['writes'].items():
        _write(Path(name), value)
    # Each event is an immutable journal entry; replay never appends a duplicate.
    event_path = state_dir(config) / 'audit' / (tx['id'] + '.json')
    if not event_path.exists():
        _write(event_path, {k: tx[k] for k in ('id', 'action', 'timestamp', 'events')})
    config.log_dir.mkdir(parents=True, exist_ok=True)
    for number, event in enumerate(tx['events']):
        log = config.log_dir / f"intake-{tx['id']}-{number}.jsonl"
        if not log.exists():
            with log.open('x', encoding='utf-8', newline='\n') as handle:
                handle.write(json.dumps({'timestamp': tx['timestamp'], **event}, sort_keys=True) + '\n')
                handle.flush()
                os.fsync(handle.fileno())
    _write(state_dir(config) / 'completed' / (tx['id'] + '.json'), {'id': tx['id']})


def recover(config, *, staging_only=False):
    with locked(config):
        count = 0
        for path in sorted((state_dir(config) / 'transactions').glob('*.json')):
            if (state_dir(config) / 'completed' / path.name).exists():
                continue
            tx = json.loads(path.read_text(encoding='utf-8'))
            if staging_only and any(name in tx['writes'] for name in (str(config.public_graph_path.resolve()), str(config.private_graph_path.resolve()))):
                raise RuntimeError('An approved graph update needs recovery; run python -m agent recover first')
            _finish(config, tx)
            count += 1
        return {'recoveredTransactions': count}


def assert_readable(config):
    for path in (state_dir(config) / 'transactions').glob('*.json'):
        if not (state_dir(config) / 'completed' / path.name).exists():
            raise RuntimeError('Workflow update is incomplete; run python -m agent recover before viewing')


def transactional(function):
    signature = inspect.signature(function)
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        config = bound.arguments['config']
        if bound.arguments.get('dry_run'):
            return function(*args, **kwargs)
        with locked(config):
            if _transaction.get() is not None:
                return function(*args, **kwargs)
            recover(config, staging_only=True)
            from .ingest import iso_timestamp
            tx = {'id': uuid.uuid4().hex, 'action': function.__name__, 'timestamp': iso_timestamp(config.clock()), 'writes': {}, 'events': []}
            token = _transaction.set(tx)
            try:
                result = function(*args, **kwargs)
            finally:
                _transaction.reset(token)
            if function.__name__ in {'approve', 'publish', 'import_export', 'stage_conversation'} and (state_dir(config) / 'STOP').exists():
                raise RuntimeError('Workflow is stopped; use resume after reviewing the reason')
            if tx['writes'] or tx['events']:
                tx['before'] = {name: json.loads(Path(name).read_text(encoding='utf-8')) if Path(name).exists() else None for name in tx['writes']}
                private = str(config.private_graph_path.resolve())
                if private in tx['before'] and tx['before'][private] is None:
                    tx['before'][private] = json.loads(config.public_graph_path.read_text(encoding='utf-8'))
                _write(state_dir(config) / 'transactions' / (tx['id'] + '.json'), tx)
                _finish(config, tx)
            return result
    return wrapped


def rollback_choices(config):
    choices = []
    for path in (state_dir(config) / 'transactions').glob('*.json'):
        tx = json.loads(path.read_text(encoding='utf-8'))
        if str(config.private_graph_path.resolve()) in tx['writes']:
            choices.append({'id': tx['id'], 'action': tx['action'], 'timestamp': tx['timestamp']})
    return {'updates': sorted(choices, key=lambda x: (x['timestamp'], x['id']))}


@transactional
def rollback(config, transaction_id):
    if len(transaction_id) != 32 or any(c not in '0123456789abcdef' for c in transaction_id):
        raise ValueError('Choose an update ID from rollback-list')
    path = state_dir(config) / 'transactions' / (transaction_id + '.json')
    tx = json.loads(path.read_text(encoding='utf-8'))
    private = str(config.private_graph_path.resolve())
    staging = str(config.staging_path.resolve())
    if private not in tx['writes'] or tx['action'] != 'approve':
        raise ValueError('Only a private approval update can be rolled back')
    current = json.loads(config.private_graph_path.read_text(encoding='utf-8'))
    if current != tx['writes'][private]:
        raise ValueError('Roll back later private updates first')
    queue = json.loads(config.staging_path.read_text(encoding='utf-8'))
    old_status = {p['id']: p for b in tx['before'][staging]['batches'] for p in b['proposals']}
    changed = {p['id'] for b in tx['writes'][staging]['batches'] for p in b['proposals'] if old_status.get(p['id']) != p}
    for batch in queue['batches']:
        for p in batch['proposals']:
            if p['id'] in changed:
                if p.get('publishedAt') or p['status'] == 'approved-public':
                    raise ValueError('Public-approved updates require separate public recovery planning')
                p.update(old_status[p['id']])
    from .ingest import validate_graph
    restored = tx['before'][private] or json.loads(config.public_graph_path.read_text(encoding='utf-8'))
    validate_graph(restored)
    _write(config.private_graph_path, restored)
    _write(config.staging_path, queue)
    queue_event('explicit-private-rollback', {'restoredUpdate': transaction_id})
    return {'rolledBack': transaction_id, 'publicGraphUnchanged': True, 'historyRetained': True}
