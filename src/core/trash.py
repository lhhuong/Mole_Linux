"""Recoverable same-filesystem moves. No recursive deletion or copy fallback."""
import ctypes
import datetime as dt
import errno
import os
from pathlib import Path
import urllib.parse
import uuid
from . import history
from .safety import identity


def open_directory(path):
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.absolute().parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def move(source, destination, expected):
    """Pin ancestors and atomically refuse an existing destination (Linux)."""
    source_fd = open_directory(source.parent)
    try:
        destination_fd = open_directory(destination.parent)
        try:
            st = os.stat(source.name, dir_fd=source_fd, follow_symlinks=False)
            actual = (st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
            if actual != tuple(expected):
                raise ValueError(f'changed since preview: {source}')
            libc = ctypes.CDLL(None, use_errno=True)
            rename = libc.renameat2
            rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            rename.restype = ctypes.c_int
            if rename(source_fd, os.fsencode(source.name), destination_fd, os.fsencode(destination.name), 1):
                error = ctypes.get_errno()
                if error == errno.EXDEV:
                    raise ValueError('Cross-filesystem Trash move refused; no copy/delete fallback')
                raise OSError(error, os.strerror(error), str(source))
            os.fsync(source_fd)
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)
    finally:
        os.close(source_fd)


def put(api, path, receipt=None, expected=None):
    target = api.validate_user_target(path)
    if api.is_whitelisted(target):
        raise ValueError(f'Protected by whitelist: {target}')
    for root in (api.TRASH_FILES, api.TRASH_INFO):
        # Verify every existing ancestor before mkdir follows anything.
        for parent in (root, *root.parents):
            if parent.is_symlink():
                raise ValueError(f'Unsafe Trash symlink: {parent}')
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    expected = expected or identity(target)
    size = api.path_size(target)
    receipt = receipt if receipt is not None else {
        'version': 1, 'id': uuid.uuid4().hex, 'action': 'trash',
        'time': dt.datetime.now(dt.timezone.utc).isoformat(), 'steps': []}
    name = target.name
    while os.path.lexists(api.TRASH_FILES / name) or os.path.lexists(api.TRASH_INFO / (name + '.trashinfo')):
        name = target.name + '.' + uuid.uuid4().hex
    destination = api.TRASH_FILES / name
    step = {'original': str(target), 'trash': str(destination), 'bytes': size,
            'identity': list(expected), 'status': 'prepared'}
    receipt['steps'].append(step)
    history.save(api, receipt)
    info = api.TRASH_INFO / (name + '.trashinfo')
    try:
        with info.open('x', encoding='utf-8') as stream:
            stream.write('[Trash Info]\nPath=' + urllib.parse.quote(str(target)) +
                         '\nDeletionDate=' + dt.datetime.now().strftime('%Y-%m-%dT%H:%M:%S') + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        move(target, destination, expected)
        step['status'] = 'trashed'
    except (OSError, ValueError) as exc:
        step['error'] = str(exc)
        # A prepared step can be reconciled by restore after an interrupted move.
        history.save(api, receipt)
        raise
    history.save(api, receipt)
    api.log_event('trash', str(target), 'ok', size)
    return size


def restore(api, receipt, dry_run=False):
    failures = 0
    for step in receipt['steps']:
        if step['status'] not in ('trashed', 'prepared'):
            continue
        try:
            source, target = Path(step['trash']), api.validate_user_target(Path(step['original']))
            if source.parent != api.TRASH_FILES or source.is_symlink():
                raise ValueError('Invalid Trash receipt path')
            current = identity(source)
            # rename changes ctime. The stable identity must still match.
            if current[:5] != tuple(step['identity'])[:5]:
                raise ValueError('Trash item changed since removal')
            if os.path.lexists(target):
                raise ValueError(f'Restore conflict: {target}')
            if not target.parent.is_dir():
                removed = {row.get('path') for row in receipt.get('outcomes', [])
                           if row.get('status') in {'empty-directory-removed', 'empty-directory-prepared'}}
                missing = []
                parent = target.parent
                while not parent.exists():
                    if str(parent) not in removed:
                        raise ValueError(f'Restore parent missing: {parent}')
                    api.validate_user_target(parent)
                    missing.append(parent)
                    parent = parent.parent
                if not dry_run:
                    for directory in reversed(missing):
                        fd = open_directory(directory.parent)
                        try:
                            os.mkdir(directory.name, mode=0o700, dir_fd=fd)
                        finally:
                            os.close(fd)
            print(f'  Restore {source} → {target}')
            if not dry_run:
                move(source, target, current)
                step['status'] = 'restored'
                history.save(api, receipt)
                info = api.TRASH_INFO / (source.name + '.trashinfo')
                info.unlink(missing_ok=True)
                api.log_event('restore', str(target), 'ok', step['bytes'])
        except (OSError, ValueError) as exc:
            print(f'  Kept: {exc}')
            failures += 1
    return 1 if failures else 0
