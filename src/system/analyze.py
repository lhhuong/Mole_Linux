"""Bounded, read-only disk exploration using allocated blocks."""
import os
import time
from pathlib import Path

VIRTUAL = {'/proc', '/sys', '/dev', '/run'}


def scan(root, cancel=None, seconds=30, progress=None):
    root = Path(root).resolve()
    device = root.stat().st_dev
    seen, entries, skipped = set(), [], 0
    deadline = time.monotonic() + seconds
    partial = False
    def stopped():
        return (cancel is not None and cancel.is_set()) or time.monotonic() > deadline
    def size(path):
        nonlocal skipped, partial
        allocated = logical = count = 0
        pending = [path]
        while pending:
            if stopped():
                partial = True
                break
            current = pending.pop()
            try:
                st = current.lstat()
                if str(current) in VIRTUAL or current.is_symlink() or st.st_dev != device:
                    skipped += 1
                    continue
                key = (st.st_dev, st.st_ino)
                if key in seen:
                    continue
                seen.add(key)
                allocated += st.st_blocks * 512
                if current.is_dir():
                    with os.scandir(current) as children:
                        pending.extend(Path(child.path) for child in children)
                else:
                    logical += st.st_size
                    count += 1
            except OSError:
                skipped += 1
        return allocated, logical, count
    with os.scandir(root) as children:
        paths = sorted((Path(child.path) for child in children), key=str)
    for index, path in enumerate(paths):
        if stopped():
            partial = True
            break
        allocated, logical, count = size(path)
        entries.append({'path': str(path), 'bytes': allocated, 'logical_bytes': logical,
                        'files': count, 'type': 'directory' if path.is_dir() else 'file'})
        if progress:
            progress(index + 1, len(paths))
    return {'root': str(root), 'entries': sorted(entries, key=lambda item: item['bytes'], reverse=True),
            'partial': partial, 'skipped': skipped, 'bytes': sum(item['bytes'] for item in entries)}


def volumes():
    """Root plus local mounted devices; exclude pseudo/network/container mounts."""
    result = [Path('/')]
    try:
        for line in Path('/proc/self/mounts').read_text().splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0].startswith('/dev/') and parts[2] not in {'squashfs', 'iso9660'}:
                mount = Path(parts[1].replace('\\040', ' ').replace('\\011', '\t'))
                if mount not in result and not str(mount).startswith(('/boot', '/snap/')):
                    result.append(mount)
    except OSError:
        pass
    return result
