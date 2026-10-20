"""Exact application roots, empty-directory removal and honest postflight audit."""
import os
from pathlib import Path
from core.trash import open_directory


def extra_roots(api, app):
    # Docker documents this subtree as Desktop-owned; .docker itself is shared.
    if app.source == 'desktop' and app.package == 'docker-desktop':
        return [api.HOME / '.docker/desktop']
    if app.source == 'snap' and app.package:
        return [api.HOME / 'snap' / app.package]
    return []


def finish(api, directories, record=None):
    outcomes = []
    # Inode checks prevent removing a replacement directory after preview.
    for value, device, inode in sorted(directories, key=lambda item: len(Path(item[0]).parts), reverse=True):
        path = Path(value)
        try:
            api.validate_user_target(path)
            fd = open_directory(path.parent)
            try:
                st = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
                if (st.st_dev, st.st_ino) != (device, inode):
                    raise ValueError('Directory changed since preview')
                if record:
                    record({'path': value, 'status': 'empty-directory-prepared'})
                # SAFE: rmdir only removes an empty reviewed directory, never its contents.
                os.rmdir(path.name, dir_fd=fd)
            finally:
                os.close(fd)
            outcomes.append({'path': value, 'status': 'empty-directory-removed'})
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            outcomes.append({'path': value, 'status': 'remaining', 'reason': str(exc)})
    return outcomes
