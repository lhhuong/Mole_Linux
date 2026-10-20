"""Application liveness uses executable identity, not a machine-wide open-file scan."""
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProcessState:
    running: tuple[tuple[int, str], ...] = ()
    unknown: tuple[int, ...] = ()


def probe(targets, proc_root=Path('/proc')):
    targets = {str(Path(path).resolve()) for path in targets}
    running, unknown = [], []
    for proc in proc_root.iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            executable = os.readlink(proc / 'exe').removesuffix(' (deleted)')
            if str(Path(executable).resolve()) in targets:
                running.append((int(proc.name), executable))
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError:
            # Sandboxed/non-dumpable desktop processes can deny exe readlink while
            # exposing comm/cmdline. Classify those instead of blocking every app.
            try:
                comm = (proc / 'comm').read_text().strip()
                argv = (proc / 'cmdline').read_bytes().split(b'\0')
                first = os.fsdecode(argv[0]) if argv else ''
                names = {Path(target).name for target in targets}
                if comm in {name[:15] for name in names} or (first and
                        (first in targets or Path(first).name in names)):
                    running.append((int(proc.name), first or comm))
                elif not comm or not first:
                    # Empty cmdline is normal for zombies; they cannot execute.
                    status = (proc / 'status').read_text()
                    if not any(line.startswith('State:') and line.split()[1:2] == ['Z'] for line in status.splitlines()):
                        unknown.append(int(proc.name))
            except (FileNotFoundError, ProcessLookupError):
                continue
            except OSError:
                unknown.append(int(proc.name))
    return ProcessState(tuple(running), tuple(unknown))


def require_stopped(targets):
    if not targets:
        return
    state = probe(targets)
    if state.running:
        details = ', '.join(f'PID {pid}: {path}' for pid, path in state.running)
        raise ValueError('Ứng dụng vẫn đang chạy (' + details + '). Đóng ứng dụng rồi thử lại; chưa gỡ package hoặc file liên quan.')
    if state.unknown:
        details = ', '.join(str(pid) for pid in state.unknown[:8])
        raise ValueError('Không đọc được executable của tiến trình PID ' + details +
                         '; chưa thể xác minh ứng dụng đã dừng. Không phải kết luận ứng dụng đang chạy.')
