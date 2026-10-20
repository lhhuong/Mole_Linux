"""Conservative cache allowlist. Scan individual old files, never cache roots."""
import os
import time
from core.plan import Candidate, OperationPlan
from core.safety import open_files, unused


def roots(api):
    return {
        'thumbnails': (api.CACHE_HOME / 'thumbnails', 'Desktop thumbnails'),
        'pip': (api.CACHE_HOME / 'pip', 'pip'),
        'npm': (api.HOME / '.npm/_cacache', 'npm'),
        'yarn': (api.CACHE_HOME / 'yarn', 'Yarn'),
        'cargo': (api.HOME / '.cargo/registry/cache', 'Cargo archives'),
        'firefox': (api.CACHE_HOME / 'mozilla/firefox', 'Firefox cache'),
        'chromium': (api.CACHE_HOME / 'chromium', 'Chromium cache'),
        'chrome': (api.CACHE_HOME / 'google-chrome', 'Chrome cache'),
        'edge': (api.CACHE_HOME / 'microsoft-edge', 'Edge cache'),
        'fontconfig': (api.CACHE_HOME / 'fontconfig', 'Font cache'),
        'mesa': (api.CACHE_HOME / 'mesa_shader_cache', 'Mesa shader cache'),
        'logs': (api.STATE_HOME / 'log', 'User logs'),
    }


def scan(api, categories, days=14, cancel=None, extra_roots=None):
    if days < 1:
        raise ValueError('cache age must be at least one day')
    opened, cutoff, items = open_files(), time.time() - days * 86400, []
    stats = {'scanned': 0, 'recent': 0, 'in_use': 0, 'protected': 0, 'unreadable': 0, 'missing_roots': 0, 'process_unknown': int(opened is None)}
    available = roots(api) | (extra_roots or {})
    for category in categories:
        root, owner = available[category]
        if not root.exists():
            stats['missing_roots'] += 1
            continue
        if root.is_symlink():
            continue
        def scan_error(error):
            stats['unreadable'] += 1
        for directory, dirs, files in os.walk(root, followlinks=False, onerror=scan_error):
            if cancel and cancel.is_set():
                raise InterruptedError('Đã huỷ quét; chưa tạo kế hoạch hoặc thay đổi file')
            dirs[:] = sorted(d for d in dirs if not (root.__class__(directory) / d).is_symlink())
            for name in sorted(files):
                if cancel and cancel.is_set():
                    raise InterruptedError('Đã huỷ quét; chưa tạo kế hoạch hoặc thay đổi file')
                path = root.__class__(directory) / name
                try:
                    stats['scanned'] += 1
                    st = path.lstat()
                    if not path.is_file() or path.is_symlink() or st.st_nlink != 1:
                        stats['protected'] += 1
                        continue
                    if max(st.st_atime, st.st_mtime) > cutoff:
                        stats['recent'] += 1
                        continue
                    api.validate_user_target(path)
                    if not unused(path, opened):
                        stats['in_use'] += 1
                        continue
                    items.append(Candidate.capture(path, f'{owner}: unused for at least {days} days',
                                                   st.st_size, category, owner, 'low'))
                except OSError:
                    stats['unreadable'] += 1
                except ValueError:
                    stats['protected'] += 1
    return OperationPlan('clean', tuple(items), diagnostics=tuple(stats.items()))
