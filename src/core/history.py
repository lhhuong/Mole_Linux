"""Durable receipts, saved before the first filesystem mutation."""
import json
import os
import re
import tempfile


def directory(api):
    root = api.STATE_DIR / 'receipts'
    for path in (root, *root.parents):
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError(f'Unsafe history directory: {path}')
    if not root.resolve().is_relative_to(api.HOME):
        raise ValueError('History must remain inside HOME')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def save(api, receipt):
    root = directory(api)
    target = root / (receipt['id'] + '.json')
    fd, name = tempfile.mkstemp(prefix='.receipt-', dir=root)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(receipt, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read(api, session):
    if not re.fullmatch(r'[0-9a-f]{32}', session):
        raise ValueError('Invalid session ID')
    path = api.STATE_DIR / 'receipts' / (session + '.json')
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError('Symlink receipt refused')
    result = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(result, dict) or result.get('version') != 1 or result.get('id') != session:
        raise ValueError('Invalid receipt')
    if not isinstance(result.get('action'), str) or not isinstance(result.get('time'), str) or not isinstance(result.get('steps'), list):
        raise ValueError('Incomplete receipt')
    for step in result['steps']:
        if not isinstance(step, dict) or not all(isinstance(step.get(key), str) for key in ('original', 'trash', 'status')):
            raise ValueError('Invalid receipt step')
        if (type(step.get('bytes')) is not int or not isinstance(step.get('identity'), list)
                or len(step['identity']) != 6 or not all(type(value) is int for value in step['identity'])):
            raise ValueError('Invalid receipt identity')
    for key in ('commands', 'planned_commands', 'plan', 'outcomes'):
        if key in result and not isinstance(result[key], list):
            raise ValueError('Invalid receipt list')
    if 'process_targets' in result and (not isinstance(result['process_targets'], list) or not all(isinstance(path, str) for path in result['process_targets'])):
        raise ValueError('Invalid process targets')
    for command in result.get('commands', []):
        if (not isinstance(command, dict) or not isinstance(command.get('status'), str)
                or not isinstance(command.get('argv'), list) or not all(isinstance(arg, str) for arg in command['argv'])):
            raise ValueError('Invalid receipt command')
    for candidate in result.get('plan', []):
        if not isinstance(candidate, dict) or not all(isinstance(candidate.get(key), str) for key in ('path', 'reason', 'category', 'owner', 'risk')):
            raise ValueError('Invalid receipt candidate')
        if (type(candidate.get('size')) is not int or not isinstance(candidate.get('last_used'), (int, float))
                or not isinstance(candidate.get('fingerprint'), list) or len(candidate['fingerprint']) != 6
                or not all(type(value) is int for value in candidate['fingerprint'])):
            raise ValueError('Invalid candidate identity')
    return result
