"""Package backend commands; privileged execution has a separate fixed helper."""
import re
import shutil
import subprocess

HELPER = '/usr/lib/molelinux/system-helper'


def apt_preview(package):
    if not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*(?::(?:amd64|arm64|all))?', package):
        raise ValueError('Invalid Debian package name')
    result = subprocess.run(['apt-get', '--simulate', 'remove', '--', package],
                            capture_output=True, text=True, timeout=60,
                            env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
    if result.returncode:
        raise ValueError('APT preview failed: ' + result.stderr.strip())
    removed = [line.split()[1] for line in result.stdout.splitlines() if line.startswith('Remv ')]
    if not removed or any(name.split(':')[0] != package.split(':')[0] for name in removed):
        raise ValueError('APT would remove dependent packages; review with the package manager')
    return result.stdout


def removal_command(app):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]*', app.app_id):
        raise ValueError('Invalid application ID')
    if app.source == 'flatpak':
        if not shutil.which('flatpak'):
            raise ValueError('Không tìm thấy Flatpak; không chuyển sang backend khác.')
        return ['flatpak', '--user', 'uninstall', '--noninteractive', app.app_id]
    if app.source == 'snap':
        if not shutil.which('snap'):
            raise ValueError('Không tìm thấy Snap; không chuyển sang backend khác.')
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', app.package):
            raise ValueError('Invalid Snap package name')
        return ['pkexec', HELPER, 'snap-purge', app.package]
    if app.package and shutil.which('apt-get'):
        apt_preview(app.package)
        return ['pkexec', HELPER, 'apt-purge', app.package]
    return None


def snapshot_commands(app):
    if app.source != 'snap':
        return ()
    result = subprocess.run(['snap', 'saved', app.package], capture_output=True, text=True,
                            timeout=30, check=False, env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
    if result.returncode:
        raise ValueError('Không liệt kê được snapshot Snap; chưa thể tạo kế hoạch gỡ sạch')
    ids = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0].isdigit() and fields[1] == app.package:
            ids.add(fields[0])
    return tuple(('pkexec', HELPER, 'snap-forget', identifier, app.package) for identifier in sorted(ids, key=int))
