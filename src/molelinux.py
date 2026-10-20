#!/usr/bin/python3
"""MoleLinux: safety-first Ubuntu cleanup and maintenance."""
from __future__ import annotations

import argparse
import configparser
import datetime as dt
import json
import os
import re
import shutil
import signal
import shlex
import subprocess
import sys
import time
import urllib.parse
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

# Resolve the installed launcher symlink before loading sibling modules.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from core.safety import validate, open_files, unused
from core.plan import Candidate, OperationPlan
from core.config import load as load_config
from core.executor import execute
from core import history as receipts
from core import trash as trash_backend
from clean.scanner import scan, roots as cache_roots
from console.style import paint, safe, notice, activity
from uninstall.processes import require_stopped

VERSION = "1.0"
HOME = Path.home().resolve()


def xdg(variable: str, fallback: str) -> Path:
    return Path(os.environ.get(variable, str(HOME / fallback))).expanduser()


CONFIG_HOME = xdg("XDG_CONFIG_HOME", ".config")
CACHE_HOME = xdg("XDG_CACHE_HOME", ".cache")
DATA_HOME = xdg("XDG_DATA_HOME", ".local/share")
STATE_HOME = xdg("XDG_STATE_HOME", ".local/state")
CONFIG_DIR = CONFIG_HOME / "molelinux"
STATE_DIR = STATE_HOME / "molelinux"
LOG_FILE = STATE_DIR / "operations.jsonl"
WHITELIST_FILE = CONFIG_DIR / "whitelist"
TRASH_FILES = DATA_HOME / "Trash/files"
TRASH_INFO = DATA_HOME / "Trash/info"
PROTECTED_ROOTS = {HOME, CONFIG_HOME.resolve(), CACHE_HOME.resolve(), DATA_HOME.resolve(), STATE_HOME.resolve()}

COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"
BOLD, CYAN, GREEN, YELLOW, RED, RESET = (
    ("\033[1m", "\033[36m", "\033[32m", "\033[33m", "\033[31m", "\033[0m")
    if COLOR else ("", "", "", "", "", "")
)


def heading(text: str, color='cyan') -> None:
    from console.style import heading as render
    render(text, color)


def human_size(value: int) -> str:
    size = float(max(0, value))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if size < 1024 or unit == "PiB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{value}B"


def path_size(path: Path) -> int:
    try:
        if path.is_symlink() or path.is_file():
            return path.lstat().st_size
        total = 0
        for root, dirs, files in os.walk(path, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(root) / d).is_symlink()]
            for name in files:
                try:
                    total += (Path(root) / name).lstat().st_size
                except OSError:
                    pass
        return total
    except OSError:
        return 0


def resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def is_within(path: Path, parent: Path) -> bool:
    try:
        resolved(path).relative_to(resolved(parent))
        return True
    except ValueError:
        return False


def validate_user_target(path: Path) -> Path:
    protected = PROTECTED_ROOTS | {CONFIG_DIR, STATE_DIR, DATA_HOME / 'Trash',
                                   TRASH_FILES, TRASH_INFO}
    target = validate(path, HOME, protected)
    if is_within(target, STATE_DIR) or is_within(target, CONFIG_DIR) or is_within(target, DATA_HOME / 'Trash'):
        raise ValueError(f'refusing internal state or Trash: {path}')
    if is_whitelisted(target) or any(Path(item).is_relative_to(target.resolve()) for item in whitelist()):
        raise ValueError(f'protected by whitelist: {path}')
    return target


def log_event(action: str, target: str, status: str, size: int = 0) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        record = {"time": dt.datetime.now(dt.timezone.utc).isoformat(), "action": action,
                  "target": target, "status": status, "bytes": size}
        with LOG_FILE.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def whitelist() -> set[str]:
    try:
        return {line.strip() for line in WHITELIST_FILE.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")}
    except OSError:
        return set()


def is_whitelisted(path: Path) -> bool:
    text = str(resolved(path))
    return any(text == item or text.startswith(item.rstrip("/") + "/") for item in whitelist())


def trash(path: Path, dry_run: bool = False) -> int:
    target = validate_user_target(path)
    if dry_run:
        return path_size(target)
    return trash_backend.put(sys.modules[__name__], target)


def confirm(message: str, yes: bool, dry_run: bool) -> bool:
    if dry_run or yes:
        return True
    if not sys.stdin.isatty():
        print("Refusing destructive operation without --yes in a non-interactive session.", file=sys.stderr)
        return False
    try:
        accepted = input(paint(message, 'yellow') + ' [y/N] ').strip().lower() in {'y', 'yes'}
    except EOFError:
        accepted = False
    if not accepted:
        notice('Đã huỷ; không thực hiện kế hoạch.', 'warn')
    return accepted


def run_command(command: Sequence[str], dry_run: bool, action: str) -> bool:
    shown = shlex.join(command)
    if dry_run:
        print(f"  {YELLOW}preview{RESET} {shown}")
        return True
    print(f"  {CYAN}run{RESET} {shown}")
    result = subprocess.run(command, check=False)
    status = "ok" if result.returncode == 0 else f"exit-{result.returncode}"
    log_event(action, shown, status)
    return result.returncode == 0


def children(path: Path) -> Iterable[Path]:
    try:
        yield from path.iterdir()
    except OSError:
        return


def clean_candidates(logs: bool, include_trash: bool) -> list[Path]:
    categories = ['thumbnails', 'pip', 'npm', 'yarn', 'cargo']
    if logs:
        categories.append('logs')
    return [item.path for item in scan(sys.modules[__name__], categories).items]


def preview_plan(plan):
    if plan.diagnostics:
        stats = dict(plan.diagnostics)
        names = {'scanned': 'đã xét', 'recent': 'còn mới', 'in_use': 'đang dùng/không xác định', 'protected': 'bảo vệ', 'unreadable': 'không đọc được', 'missing_roots': 'vùng chưa tồn tại'}
        print(paint('Kết quả quét: ', 'cyan') + ', '.join(f'{name}: {stats.get(key, 0)}' for key, name in names.items()))
        if stats.get('process_unknown'):
            print('Không kiểm tra được đầy đủ tiến trình; giữ lại file để bảo vệ dữ liệu.')
    print(paint(f'{len(plan.items)} file · {human_size(plan.bytes)} có thể chuyển vào Trash', 'green'))
    if plan.items:
        notice('Chuyển vào Trash chưa giải phóng dung lượng. Có thể khôi phục từ Lịch sử.')
    for item in plan.items:
        print(f'  {safe(item.path)} ' + paint(human_size(item.size), 'green') + f' [{item.risk}] — {safe(item.reason)}')
    if plan.directories:
        notice(f'{len(plan.directories)} thư mục sẽ được dọn khi rỗng và kiểm tra phần còn sót.')
    for command in plan.prepare_commands:
        notice('Dừng Docker Desktop trước khi gỡ (container đang chạy có thể bị dừng): ' + shlex.join(command), 'warn')
    for command in plan.commands:
        if 'snap-forget' in command:
            notice('Xoá vĩnh viễn snapshot của ứng dụng đã chọn; không thể khôi phục.', 'warn')
        print('  Command: ' + shlex.join(command))


def make_clean_plan(categories=None, logs=False, cancel=None):
    api = sys.modules[__name__]
    settings = load_config(CONFIG_DIR / 'config.json')
    selected = categories if categories is not None else list(cache_roots(api))
    if logs and 'logs' not in selected:
        selected = [*selected, 'logs']
    with activity('Đang quét cache và kiểm tra tiến trình…'):
        return scan(api, selected, settings['cache_age_days'], cancel)


def command_clean(args: argparse.Namespace) -> int:
    if args.trash:
        raise ValueError('Use the separate empty-trash command; clean never empties Trash')
    heading('Clean · Dọn cache an toàn', 'green')
    plan = make_clean_plan(args.category, args.logs)
    if args.system:
        from uninstall.backends import HELPER
        plan = OperationPlan('clean', plan.items, (('pkexec', HELPER, 'apt-clean'),
                                                   ('pkexec', HELPER, 'journal-vacuum')))
    preview_plan(plan)
    if not plan.items and not plan.commands:
        print('Không có file đủ điều kiện. Không thay đổi dữ liệu.')
        return 0
    if not confirm('Move only these reviewed files to Trash?', args.yes, args.dry_run):
        return 1
    return execute(sys.modules[__name__], plan, args.dry_run)


def command_empty_trash(args):
    command = ['gio', 'trash', '--empty']
    print('Permanently empty ALL desktop Trash entries; files cannot be restored afterwards.')
    print(shlex.join(command))
    if not confirm('Permanently empty Trash?', args.yes, args.dry_run):
        return 1
    return execute(sys.modules[__name__], OperationPlan('empty-trash', commands=(tuple(command),)), args.dry_run)


@dataclass
class Application:
    app_id: str
    name: str
    desktop_file: str
    executable: str
    package: str = ""
    source: str = "desktop"
    icon: str = 'application-x-executable'


def desktop_roots() -> list[Path]:
    roots = [DATA_HOME / "applications", Path("/usr/local/share/applications"), Path("/usr/share/applications"),
             Path("/var/lib/snapd/desktop/applications")]
    roots += [Path(item) / "applications" for item in os.environ.get("XDG_DATA_DIRS", "").split(":") if item]
    return list(dict.fromkeys(roots))


def dpkg_owner(path: Path) -> str:
    if not shutil.which("dpkg-query") or not path.is_absolute():
        return ""
    result = subprocess.run(["dpkg-query", "-S", str(path)], text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, check=False, timeout=15)
    return result.stdout.split(": ", 1)[0].strip() if result.returncode == 0 and ": " in result.stdout else ""


def parse_desktop(path: Path) -> Application | None:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read(path, encoding="utf-8")
        entry = parser["Desktop Entry"]
        if entry.get("Type", "Application") != "Application" or entry.getboolean("NoDisplay", fallback=False) or entry.getboolean("Hidden", fallback=False):
            return None
        raw_exec = entry.get("Exec", "").strip()
        executable = shlex.split(raw_exec)[0] if raw_exec else ""
        executable = re.sub(r"%[A-Za-z]", "", executable).strip()
        app_id, source, package = path.stem, "desktop", dpkg_owner(path)
        if is_within(path, Path("/var/lib/snapd/desktop/applications")):
            source, package = "snap", app_id.split("_", 1)[0]
        return Application(app_id, entry.get("Name", path.stem).strip(), str(path), executable, package, source, entry.get("Icon", "application-x-executable"))
    except (OSError, KeyError, ValueError, configparser.Error, subprocess.TimeoutExpired):
        return None


def installed_apps() -> list[Application]:
    found: dict[str, Application] = {}
    for root in desktop_roots():
        for path in sorted(root.glob("*.desktop")) if root.is_dir() else []:
            app = parse_desktop(path)
            if app and app.app_id not in found:
                found[app.app_id] = app
    if shutil.which("flatpak"):
        result = subprocess.run(["flatpak", "--user", "list", "--app", "--columns=application,name"], text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False, timeout=15)
        for line in result.stdout.splitlines():
            parts = line.split("\t", 1)
            if parts and parts[0]:
                previous = found.get(parts[0])
                found[parts[0]] = Application(parts[0], parts[-1], previous.desktop_file if previous else "",
                                                previous.executable if previous else "", parts[0], "flatpak")
    return sorted(found.values(), key=lambda app: app.name.casefold())


def find_app(query: str) -> list[Application]:
    needle = query.casefold()
    apps = installed_apps()
    exact = [app for app in apps if needle in {app.app_id.casefold(), app.name.casefold(), app.package.casefold()}]
    return exact or [app for app in apps if needle in app.app_id.casefold() or needle in app.name.casefold()]


def app_tokens(app: Application) -> set[str]:
    # Only authoritative IDs. Display names and executable basenames are not ownership evidence.
    return {value for value in (app.app_id, app.package) if
            re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{2,}', value)
            and value not in {'python', 'python3', 'java', 'electron', 'env'}}


def app_leftovers(app: Application, remove_config: bool = True) -> list[Path]:
    result = set()
    roots = [CACHE_HOME]
    if remove_config:
        roots.extend([CONFIG_HOME, DATA_HOME, STATE_HOME])
    for root in roots:
        for token in app_tokens(app):
            candidate = root / token
            if candidate.exists():
                try:
                    result.add(validate_user_target(candidate))
                except ValueError:
                    pass
    if remove_config and app.source == 'flatpak':
        candidate = HOME / '.var/app' / app.app_id
        if candidate.exists():
            result.add(validate_user_target(candidate))
    if remove_config:
        from uninstall.residuals import extra_roots
        for candidate in extra_roots(sys.modules[__name__], app):
            if candidate.exists():
                result.add(validate_user_target(candidate))
    # Never infer ownership of an executable from Exec alone: it may be shared.
    # Only the selected per-user launcher is removable without an app manifest.
    if app.desktop_file:
        desktop = Path(app.desktop_file)
        if desktop.parent == DATA_HOME / 'applications' and desktop.is_file():
            try:
                result.add(validate_user_target(desktop))
            except ValueError:
                pass
    return sorted(result, key=str)


def uninstall_command(app: Application) -> list[str] | None:
    from uninstall.backends import removal_command
    return removal_command(app)


def command_apps(args: argparse.Namespace) -> int:
    apps = installed_apps()
    if args.json:
        print(json.dumps([asdict(app) for app in apps], ensure_ascii=False, indent=2))
    else:
        heading("Applications · Ứng dụng đã cài", "purple")
        for app in apps:
            source = app.source if app.source != "desktop" else (f"deb:{app.package}" if app.package else "desktop")
            print(f"  {safe(app.name):<32}  [{safe(source)}]\n    " + paint(app.app_id, "gray"))
        print(f"\n{len(apps)} applications")
    return 0


def make_uninstall_plan(query, remove_config=True, inventory=None):
    matches = [query] if isinstance(query, Application) else find_app(query)
    if len(matches) != 1:
        raise ValueError('Use one exact application ID. Matches: ' + ', '.join(app.app_id for app in matches))
    app = matches[0]
    command = uninstall_command(app)
    if app.package and command is None:
        raise ValueError('This package backend requires the restricted Polkit helper, which is not installed')
    executable = Path(shutil.which(app.executable) or app.executable) if app.executable else None
    if executable and executable.name in {'env', 'sh', 'bash'}:
        raise ValueError('Ứng dụng dùng launcher trung gian; chưa xác minh được chương trình. Giữ nguyên ứng dụng.')
    targets = (str(executable),) if executable and executable.is_absolute() else ()
    preparation = ()
    if app.package == 'docker-desktop' and app.source == 'desktop':
        # Exact package-owned executables; do not stop Docker Engine or delete VM data.
        owned = subprocess.run(['dpkg-query', '-L', 'docker-desktop'], capture_output=True,
                               text=True, timeout=15, check=False)
        if owned.returncode:
            raise ValueError('Không xác minh được file của package docker-desktop.')
        known = {'/opt/docker-desktop/bin/docker-desktop', '/opt/docker-desktop/bin/com.docker.backend',
                 '/opt/docker-desktop/Docker Desktop'}
        targets = tuple(sorted(known.intersection(owned.stdout.splitlines()) | set(targets)))
        if shutil.which('systemctl'):
            service = subprocess.run(['systemctl', '--user', 'show', 'docker-desktop.service',
                                      '--property=LoadState', '--value'], capture_output=True,
                                     text=True, timeout=10, check=False)
            if service.returncode == 0 and service.stdout.strip() == 'loaded':
                preparation = (('systemctl', '--user', 'stop', 'docker-desktop.service'),)
    if not preparation:
        require_stopped(targets)
    # Refuse ambiguous IDs shared by another application.
    siblings = [other for other in (inventory if inventory is not None else installed_apps()) if other.app_id != app.app_id]
    shared = set().union(*(app_tokens(other) for other in siblings)) if siblings else set()
    items, directories = [], []
    for path in app_leftovers(app, remove_config):
        if path.name in shared:
            continue
        if path.is_dir():
            if remove_config:
                # Capture reviewed files individually so nested whitelist entries stay protected.
                def scan_error(error):
                    raise ValueError(f'Không đọc được dữ liệu ứng dụng: {error}; chưa gỡ ứng dụng')
                for directory, dirs, files in os.walk(path, followlinks=False, onerror=scan_error):
                    directory_path = Path(directory)
                    validate_user_target(directory_path)
                    stat = directory_path.lstat()
                    directories.append((str(directory_path), stat.st_dev, stat.st_ino))
                    dirs[:] = [name for name in dirs if not (Path(directory) / name).is_symlink()]
                    for name in files:
                        child = Path(directory) / name
                        try:
                            validate_user_target(child)
                            if child.is_symlink() or not child.is_file() or child.stat().st_nlink != 1:
                                continue
                            items.append(Candidate.capture(child, 'Dữ liệu/cấu hình thuộc ID ứng dụng đã chọn',
                                                           child.stat().st_size, 'application-data', app.app_id))
                        except (OSError, ValueError):
                            notice(f'Giữ file được bảo vệ/không đọc được: {child}', 'warn')
            elif is_within(path, CACHE_HOME):
                settings = load_config(CONFIG_DIR / 'config.json')
                items.extend(scan(sys.modules[__name__], ['application'], settings['cache_age_days'],
                                  extra_roots={'application': (path, app.app_id)}).items)
            continue
        items.append(Candidate.capture(path, 'Exact application ID / selected desktop entry',
                                       path_size(path), 'application', app.app_id))
    if (not app.package and executable and executable.suffix == '.AppImage'
            and executable.is_absolute() and executable.is_file()
            and not any(other.executable == app.executable for other in siblings)):
        validate_user_target(executable)
        items.append(Candidate.capture(executable, 'Exact AppImage executable from selected desktop entry',
                                       path_size(executable), 'program', app.app_id))
    if not command and not any(item.category == 'program' for item in items):
        raise ValueError('Program ownership is not proven; unmanaged application kept until a manifest is available')
    from uninstall.backends import snapshot_commands
    commands = ((tuple(command),) if command else ()) + snapshot_commands(app)
    return OperationPlan('uninstall', tuple(items), commands,
                         prepare_commands=preparation, process_targets=targets, directories=tuple(directories))


def prepare_uninstall_batch(apps, remove_config=True, inventory=None):
    plans, blocked, seen = [], [], set()
    for app in apps:
        key = (app.source, app.package or app.app_id)
        if key in seen:
            notice(f'{app.name}: đã có cùng package trong lô, chỉ gỡ một lần.')
            continue
        seen.add(key)
        try:
            plan = make_uninstall_plan(app, remove_config, inventory)
            plans.append((app, plan))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            blocked.append((app, str(exc)))
    return plans, blocked


def review_uninstall_batch(apps, yes=False, dry_run=False, remove_config=True, inventory=None):
    with activity('Đang xác minh các ứng dụng đã chọn…'):
        plans, blocked = prepare_uninstall_batch(apps, remove_config, inventory)
    for app, reason in blocked:
        notice(f'Giữ {app.name}: {reason}', 'warn')
    for app, plan in plans:
        heading(app.name, 'purple')
        preview_plan(plan)
    if not plans:
        notice('Không có ứng dụng đủ điều kiện gỡ. Chưa thay đổi hệ thống.', 'warn')
        return 1 if blocked else 0
    notice(f'{len(plans)} ứng dụng sẽ được xử lý; {len(blocked)} ứng dụng bị giữ lại. Cấu hình/dữ liệu riêng đã xác minh sẽ chuyển vào Trash; cấu hình package hệ thống sẽ bị xoá. Dữ liệu dùng chung/không xác minh được vẫn giữ.')
    if not confirm('Gỡ toàn bộ ứng dụng đủ điều kiện trong kế hoạch trên?', yes, dry_run):
        return 1
    failures = len(blocked)
    completed = 0
    for app, plan in plans:
        heading(f'Uninstall · {app.name}', 'purple')
        try:
            result = execute(sys.modules[__name__], plan, dry_run)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            notice(f'{app.name}: {exc}', 'error')
            result = 1
        if result:
            failures += 1
        else:
            completed += 1
        if not dry_run:
            try:
                record = receipts.read(sys.modules[__name__], plan.session_id)
                if record.get('status') == 'cancelled':
                    notice('Đã huỷ lô; các ứng dụng còn lại chưa được xử lý.', 'warn')
                    return 130
            except (OSError, ValueError):
                pass
    notice(f"{'Đã xem trước' if dry_run else 'Hoàn tất'} {completed}/{len(plans)} ứng dụng; {failures} mục bị giữ/lỗi.", 'warn' if failures else 'ok')
    return 1 if failures else 0


def command_uninstall(args):
    heading('Uninstall · Gỡ ứng dụng theo lô', 'purple')
    inventory = installed_apps()
    apps = []
    queries = args.app if isinstance(args.app, list) else [args.app]
    for query in queries:
        needle = query.casefold()
        matches = [app for app in inventory if needle in {app.app_id.casefold(), app.package.casefold(), app.name.casefold()}]
        if len(matches) != 1:
            raise ValueError(f'Chưa xác định duy nhất ứng dụng {query!r}; dùng molelinux apps để xem ID.')
        apps.append(matches[0])
    return review_uninstall_batch(apps, args.yes, args.dry_run, args.remove_config, inventory)


MAINTENANCE_TASKS = {
    'apt-clean': ('Dọn cache tải gói Ubuntu; không gỡ phần mềm', ('pkexec', '/usr/lib/molelinux/system-helper', 'apt-clean')),
    'journal-vacuum': ('Xoá vĩnh viễn journal cũ hơn 14 ngày; không thể khôi phục', ('pkexec', '/usr/lib/molelinux/system-helper', 'journal-vacuum')),
    'trim': ('TRIM vùng trống trên filesystem hỗ trợ; không xoá file', ('pkexec', '/usr/lib/molelinux/system-helper', 'trim')),
}


def command_optimize(args):
    heading('Optimize · Bảo trì Ubuntu', 'yellow')
    tasks = getattr(args, 'task', None) or list(MAINTENANCE_TASKS)
    commands = []
    required = {'apt-clean': 'apt-get', 'journal-vacuum': 'journalctl', 'trim': 'fstrim'}
    for task in dict.fromkeys(tasks):
        if not shutil.which(required[task]):
            notice(f'Bỏ qua {task}: hệ thống không có {required[task]}.', 'warn')
            continue
        description, command = MAINTENANCE_TASKS[task]
        print('  ' + paint(description, 'yellow'))
        print('    ' + paint(shlex.join(command), 'gray'))
        commands.append(command)
    plan = OperationPlan('optimize', commands=((commands[0][0], commands[0][1], 'optimize', *(command[-1] for command in commands)),) if commands else ())
    if not commands:
        notice('Không có tác vụ bảo trì phù hợp trên hệ thống này.')
        return 0
    if not confirm('Thực hiện đúng các tác vụ bảo trì trên?', args.yes, args.dry_run):
        return 1
    return execute(sys.modules[__name__], plan, args.dry_run)


def scan_one_level(root: Path) -> list[dict[str, object]]:
    from system.analyze import scan
    return scan(root)['entries']


def command_analyze(args):
    from system.analyze import scan
    with activity('Đang đo dung lượng thực tế…'):
        result = scan(Path(args.path).expanduser())
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        heading(f"Analyze · Dung lượng thực tế — {result['root']}", "blue")
        print('   Chiếm đĩa      Logic  Đường dẫn')
        for row in result['entries'][:args.limit]:
            print(paint(f"{human_size(row['bytes']):>12}", 'blue') + f" {human_size(row['logical_bytes']):>10}  {safe(row['path'])}")
        print(f"Tổng đã đọc: {human_size(result['bytes'])}; bỏ qua {result['skipped']} mục (quyền truy cập/mount/symlink).")
        if result['partial']:
            print('Kết quả chưa đầy đủ: đã hết thời gian hoặc bị huỷ.')
    return 0


from system.status import read_text, cpu_times, status_snapshot

def print_status(data: dict[str, object]) -> None:
    memory, disk = data["memory"], data["disk"]
    heading("Status · Trạng thái hệ thống", "cyan")
    print(f"Host       {data['hostname']} — kernel {data['kernel']}")
    print(f"Uptime     {dt.timedelta(seconds=int(float(data['uptime_seconds'])))}")
    print(f"Load       {' / '.join(str(value) for value in data['load'])}")
    print("CPU        " + paint(f"{data['cpu_percent']}%", "green" if data['cpu_percent'] < 80 else "yellow"))
    print(f"Memory     {human_size(int(memory['total']) - int(memory['available']))} / {human_size(int(memory['total']))}")
    print(f"Disk /     {human_size(int(disk['used']))} / {human_size(int(disk['total']))} ({human_size(int(disk['free']))} free)")
    if 'network' in data:
        print('Network    ↓ ' + human_size(data['network']['rx_bytes_per_second']) + '/s  ↑ ' + human_size(data['network']['tx_bytes_per_second']) + '/s')
    for battery in data["battery"]:
        print(f"Battery    {battery['capacity']}% — {battery['status']}")


def command_status(args: argparse.Namespace) -> int:
    from console.menu import available
    if args.watch and not args.json and available():
        from console.dashboard import watch
        return watch(status_snapshot, args.interval)
    try:
        while True:
            data = status_snapshot()
            if args.json or (args.watch and not sys.stdout.isatty()):
                print(json.dumps(data, ensure_ascii=False), flush=True)
            else:
                if args.watch:
                    print("\033[2J\033[H", end="")
                print_status(data)
            if not args.watch:
                return 0
            time.sleep(max(0.2, args.interval))
    except KeyboardInterrupt:
        return 0


def command_history(args: argparse.Namespace) -> int:
    records = []
    for path in sorted((STATE_DIR / 'receipts').glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:args.limit]:
        try:
            records.append(receipts.read(sys.modules[__name__], path.stem))
        except (OSError, ValueError):
            print(f'Bỏ qua lịch sử không đọc được: {path.name}', file=sys.stderr)
    if args.json:
        print(json.dumps(records, ensure_ascii=False, indent=2))
    else:
        for record in records:
            print(f"{record['id']}  {record['time']}  {record['action']}  {record.get('status', 'interrupted')}")
        if not records:
            print('No sessions recorded.')
    return 0


def command_restore(args):
    api = sys.modules[__name__]
    receipt = receipts.read(api, args.session)
    if not any(step['status'] in {'trashed', 'prepared'} for step in receipt['steps']):
        notice('Phiên này không có file cần khôi phục.', 'info')
        return 0
    result = trash_backend.restore(api, receipt, dry_run=True)
    if args.dry_run:
        return result
    if not confirm('Restore available files without overwriting conflicts?', args.yes, False):
        return 1
    return trash_backend.restore(api, receipt)


def command_retry(args):
    api = sys.modules[__name__]
    receipt = receipts.read(api, args.session)
    if (len(receipt.get('commands', [])) != len(receipt.get('planned_commands', []))
            or any(command['status'] != 'ok' for command in receipt.get('commands', []))):
        raise ValueError('Inspect the package manager state and create a new uninstall plan before retrying')
    completed = {step['original'] for step in receipt['steps'] if step['status'] in ('trashed', 'restored')}
    items = []
    for saved in receipt.get('plan', []):
        if saved['path'] in completed:
            continue
        values = dict(saved)
        values['path'] = Path(values['path'])
        values['fingerprint'] = tuple(values['fingerprint'])
        item = Candidate(**values)
        validate_user_target(item.path)
        item.validate()
        items.append(item)
    if not items:
        notice('Không còn file chưa xử lý để thử lại.', 'info')
        return 0
    plan = OperationPlan('retry', tuple(items), process_targets=tuple(receipt.get('process_targets', [])))
    preview_plan(plan)
    if not confirm('Retry only these unchanged files?', args.yes, args.dry_run):
        return 1
    return execute(api, plan, args.dry_run)


def command_whitelist(args: argparse.Namespace) -> int:
    items = whitelist()
    if args.add:
        items.add(str(validate_user_target(Path(args.add))))
    if args.remove:
        items.discard(str(resolved(Path(args.remove))))
    if args.add or args.remove:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        WHITELIST_FILE.write_text("".join(f"{item}\n" for item in sorted(items)), encoding="utf-8")
    heading("Protected Paths")
    for item in sorted(items):
        print(f"  • {item}")
    if not items:
        print("No custom protected paths.")
    return 0


def mutation_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dry-run", action="store_true", help="show the plan without changing anything")
    parser.add_argument("-y", "--yes", action="store_true", help="skip confirmation")


def positive_int(value):
    try:
        result = int(value)
        if result < 1:
            raise ValueError
        return result
    except ValueError as exc:
        raise argparse.ArgumentTypeError('Cần số nguyên lớn hơn 0') from exc


def positive_interval(value):
    import math
    try:
        result = float(value)
        if not math.isfinite(result) or result < 0.2:
            raise ValueError
        return result
    except ValueError as exc:
        raise argparse.ArgumentTypeError('Khoảng cập nhật phải hữu hạn và ít nhất 0.2 giây') from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="molelinux", description="Safe cleanup and maintenance for Ubuntu")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = parser.add_subparsers(dest="command")
    clean = sub.add_parser("clean", help="clean user caches and optional system caches")
    mutation_flags(clean); clean.add_argument("--logs", action="store_true"); clean.add_argument("--trash", action="store_true")
    clean.add_argument("--category", action="append", choices=list(cache_roots(sys.modules[__name__])))
    clean.add_argument("--system", action="store_true"); clean.set_defaults(func=command_clean)
    empty = sub.add_parser('empty-trash', help='permanently empty desktop Trash separately')
    mutation_flags(empty); empty.set_defaults(func=command_empty_trash)
    restore = sub.add_parser('restore', help='restore files from a session receipt')
    restore.add_argument('session'); mutation_flags(restore); restore.set_defaults(func=command_restore)
    retry = sub.add_parser('retry', help='retry unchanged files from an interrupted session')
    retry.add_argument('session'); mutation_flags(retry); retry.set_defaults(func=command_retry)
    apps = sub.add_parser("apps", help="list installed graphical applications")
    apps.add_argument("--json", action="store_true"); apps.set_defaults(func=command_apps)
    uninstall = sub.add_parser("uninstall", help="remove an app and exact related files")
    uninstall.add_argument("app", nargs="+"); mutation_flags(uninstall); uninstall.add_argument("--remove-config", action="store_true", default=True, help="xoá cấu hình/dữ liệu riêng đã xác minh (mặc định)")
    uninstall.set_defaults(func=command_uninstall)
    optimize = sub.add_parser("optimize", help="run bounded Ubuntu maintenance")
    mutation_flags(optimize); optimize.add_argument("--task", action="append", choices=list(MAINTENANCE_TASKS)); optimize.set_defaults(func=command_optimize)
    analyze = sub.add_parser("analyze", aliases=["analyse"], help="show disk usage")
    analyze.add_argument("path", nargs="?", default=str(HOME)); analyze.add_argument("--limit", type=positive_int, default=50)
    analyze.add_argument("--json", action="store_true"); analyze.set_defaults(func=command_analyze)
    status = sub.add_parser("status", help="show Linux system metrics")
    status.add_argument("--json", action="store_true"); status.add_argument("--watch", action="store_true")
    status.add_argument("--interval", type=positive_interval, default=1.0); status.set_defaults(func=command_status)
    history = sub.add_parser("history", help="show recoverable operation receipts")
    history.add_argument("--json", action="store_true"); history.add_argument("--limit", type=positive_int, default=100)
    history.set_defaults(func=command_history)
    protected = sub.add_parser("whitelist", help="manage protected paths")
    group = protected.add_mutually_exclusive_group(); group.add_argument("--add"); group.add_argument("--remove")
    protected.set_defaults(func=command_whitelist)
    return parser


def choose(items, title):
    if not items:
        notice('Không có mục nào để chọn.', 'info')
        return None
    filtered, page, page_size = list(items), 0, 15
    while True:
        heading(title)
        pages = max(1, (len(filtered) + page_size - 1) // page_size)
        page = min(page, pages - 1)
        for index in range(page * page_size, min(len(filtered), (page + 1) * page_size)):
            print(paint(f'  {index + 1:>2}. ', 'cyan') + safe(filtered[index][0]))
        print(paint(f'  Trang {page + 1}/{pages} · n/p: trang sau/trước · /từ khoá: tìm · /: tất cả · 0: quay lại', 'gray'))
        answer = input('Chọn mục: ').strip()
        if answer.lower() in {'0', 'q', ''}:
            return None
        if answer.lower() in {'n', 'p'}:
            page = max(0, min(pages - 1, page + (1 if answer.lower() == 'n' else -1)))
            continue
        if answer.startswith('/'):
            query = answer[1:].casefold()
            filtered = [item for item in items if query in item[0].casefold()]
            page = 0
            if not filtered: notice('Không có kết quả phù hợp; nhập / để xem tất cả.', 'warn')
            continue
        try:
            number = int(answer)
            if 1 <= number <= len(filtered):
                return filtered[number - 1][1]
        except ValueError:
            pass
        notice('Lựa chọn không hợp lệ. Chọn số trong danh sách.', 'warn')


def console_apps():
    from console.selection import select_many
    with activity('Đang tải danh sách ứng dụng…'):
        apps = installed_apps()
    labels = [f'{app.name} [{app.source if app.source != "desktop" else app.package or "thủ công"}]' for app in apps]
    selected = select_many(labels)
    if not selected:
        notice('Không chọn ứng dụng nào; quay lại menu.')
        return
    review_uninstall_batch([apps[index] for index in selected], inventory=apps)


def console_analyze():
    from system.analyze import scan, volumes
    root = Path('/')
    while True:
        with activity(f'Đang phân tích {root}…'):
            result = scan(root)
        heading(f'Analyze · {root}', 'blue')
        print(paint(f"Đã đo {human_size(result['bytes'])} chiếm đĩa", 'blue'))
        if result['partial']:
            notice('Kết quả một phần; chọn thư mục nhỏ hơn để phân tích tiếp.', 'warn')
        if result['skipped']:
            notice(f"Bỏ qua {result['skipped']} mục do quyền truy cập, symlink hoặc mount khác.", 'warn')
        navigation = []
        if root != Path('/'):
            navigation.append(('↑ Thư mục cha', root.parent))
        else:
            navigation.append(('⌂ Thư mục cá nhân', HOME))
            navigation.extend((f'Ổ đĩa {mount}', mount) for mount in volumes() if mount != Path('/'))
        for row in result['entries']:
            path = Path(row['path'])
            navigation.append((f"{human_size(row['bytes']):>10} thực / {human_size(row['logical_bytes'])} logic · {path.name}" + (' /' if row['type'] == 'directory' else ''), path))
        while True:
            selected = choose(navigation, 'Chọn thư mục để đi sâu; 0 trở về menu')
            if selected is None:
                return
            if selected.is_symlink():
                notice('Không theo symlink trong trình phân tích.', 'warn')
            elif selected.is_dir():
                root = selected
                break
            else:
                notice(f'File: {selected}. Đây là chế độ phân tích chỉ đọc.', 'info')


def console_history():
    records = []
    for path in sorted((STATE_DIR / 'receipts').glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            item = receipts.read(sys.modules[__name__], path.stem)
            records.append((f"{item['time']} — {item['action']} — {item.get('status', 'interrupted')}", item))
        except (OSError, ValueError):
            continue
    receipt = choose(records, 'Lịch sử thao tác')
    if receipt is None:
        return
    action = choose([('Xem chi tiết', 'details'), ('Khôi phục file', 'restore'), ('Thử lại file chưa xử lý', 'retry')], 'Thao tác với phiên')
    if action == 'details':
        heading(f"{receipt['action']} · {receipt.get('status', 'interrupted')}", 'purple')
        print(paint(f"{receipt['time']} · Phiên {receipt['id']}", 'gray'))
        for step in receipt['steps']:
            print(f"  {safe(step['original'])} · {human_size(step['bytes'])} · " + paint(step['status'], 'green' if step['status'] == 'restored' else 'yellow'))
        for command in receipt.get('commands', []):
            print('  ' + safe(shlex.join(command['argv'])) + ' · ' + safe(command['status']))
        if receipt.get('error'):
            notice(receipt['error'], 'error')
    elif action:
        main([action, receipt['id']])


def interactive(parser: argparse.ArgumentParser) -> int:
    if not sys.stdin.isatty():
        parser.print_help()
        return 0
    from console.menu import available, select
    cursor = 0
    while True:
        keyboard = available()
        if not keyboard: heading('MoleLinux — ' + VERSION)
        entries = [('1', 'Clean', 'Dọn cache an toàn', 'green'),
                   ('2', 'Uninstall', 'Chọn và gỡ ứng dụng', 'purple'),
                   ('3', 'Optimize', 'Bảo trì có xác nhận', 'yellow'),
                   ('4', 'Analyze', 'Khám phá dung lượng thực tế', 'blue'),
                   ('5', 'Status', 'Theo dõi CPU, RAM, ổ đĩa', 'cyan'),
                   ('6', 'History', 'Lịch sử / Khôi phục', 'purple')]
        for number, name, description, color in ([] if keyboard else entries):
            print('  ' + paint(f'{number}. {name:<11}', color) + paint(description, 'gray'))
        if not keyboard: print('  ' + paint('0. Thoát   ·   Ctrl+C: huỷ / về menu', 'gray'))
        try:
            answer = select(entries, VERSION, cursor) if keyboard else input('Chọn chức năng (0–6): ').strip().lower()
            if answer in {'1', '2', '3', '4', '5', '6'}: cursor = int(answer) - 1
            if answer in {'0', 'q', 'quit'}:
                return 0
            if answer not in {str(i) for i in range(1, 7)}:
                notice('Lựa chọn không hợp lệ. Chọn 0–6.', 'warn')
                continue
            if answer == '1':
                main(['clean'])
            elif answer == '2':
                console_apps()
            elif answer == '3':
                main(['optimize'])
            elif answer == '4':
                console_analyze()
            elif answer == '5':
                print('Ctrl+C để quay lại menu.')
                main(['status', '--watch'])
            elif answer == '6':
                console_history()
            input('\nNhấn Enter để quay lại menu…')
        except EOFError:
            return 0
        except KeyboardInterrupt:
            print('\nĐã huỷ. Quay lại menu.')
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            notice(f'Không thể hoàn tất: {exc}', 'error')



def main(argv: Sequence[str] | None = None) -> int:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    if os.geteuid() == 0:
        print("Run MoleLinux without sudo; it requests administrator access only when needed.", file=sys.stderr)
        return 1
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        return interactive(parser)
    try:
        return int(args.func(args))
    except (KeyboardInterrupt, EOFError):
        print("\nĐã huỷ.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        notice(f'Không thể hoàn tất: {exc}', 'error', sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
