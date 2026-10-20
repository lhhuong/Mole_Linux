"""Shared fail-closed path and filesystem identity checks."""
import os
from pathlib import Path


def validate(path, home, protected):
    target = Path(os.path.abspath(path.expanduser()))
    if target.is_symlink():
        raise ValueError(f'refusing symlink target: {path}')
    real = target.resolve()
    if not target.is_relative_to(home) or not real.is_relative_to(home):
        raise ValueError(f"refusing path outside home: {path}")
    for root in protected:
        root = root.resolve()
        if real == root or root.is_relative_to(real):
            raise ValueError(f"refusing protected directory or ancestor: {path}")
    # Reject symlink ancestors, even those currently pointing inside home.
    for parent in target.parents:
        if parent == home:
            break
        if parent.is_symlink():
            raise ValueError(f"refusing symlink ancestor: {parent}")
    return target


def identity(path):
    stat = path.lstat()
    return (stat.st_dev, stat.st_ino, stat.st_mode, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def open_files():
    """All accessible processes belonging to this user; unknown means refuse."""
    found = set()
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            for fd in (proc / 'fd').iterdir():
                try:
                    found.add(str(fd.resolve(strict=True)))
                except FileNotFoundError:
                    continue
            for line in (proc / 'maps').read_text().splitlines():
                fields = line.split(None, 5)
                if len(fields) == 6 and fields[5].startswith('/'):
                    found.add(fields[5].removesuffix(' (deleted)'))
            for field in ('cwd', 'exe'):
                try:
                    found.add(str((proc / field).resolve(strict=True)))
                except FileNotFoundError:
                    continue
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (PermissionError, OSError):
            return None
    return found


def unused(path, opened):
    if opened is None:
        return False
    target = str(path.resolve())
    return not any(item == target or item.startswith(target + '/') for item in opened)
