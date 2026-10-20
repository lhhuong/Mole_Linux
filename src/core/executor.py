"""One execution path for reviewed console plans."""
import datetime as dt
from . import history
from .safety import open_files, unused
from .trash import put
from uninstall.processes import require_stopped


def execute(api, plan, dry_run=False, cancel=None, progress=None):
    if dry_run:
        api.notice('Chỉ xem trước — không có thay đổi.', 'info')
        return 0
    if not plan.items and not plan.commands and not plan.prepare_commands and not plan.directories:
        api.notice('Không có thao tác cần thực hiện.', 'info')
        return 0
    receipt = {'version': 1, 'id': plan.session_id, 'action': plan.action,
               'time': dt.datetime.now(dt.timezone.utc).isoformat(), 'steps': [], 'commands': [],
               'plan': [{'path': str(item.path), 'reason': item.reason, 'size': item.size,
                         'category': item.category, 'owner': item.owner, 'risk': item.risk,
                         'last_used': item.last_used, 'fingerprint': list(item.fingerprint)} for item in plan.items],
               'outcomes': [], 'planned_commands': [list(command) for command in (*plan.prepare_commands, *plan.commands)], 'process_targets': list(plan.process_targets), 'directories': list(plan.directories)}
    history.save(api, receipt)
    moved, failures = 0, 0
    free_before = api.shutil.disk_usage(api.HOME).free
    try:
        for command_index, command in enumerate((*plan.prepare_commands, *plan.commands)):
            if command_index >= len(plan.prepare_commands):
                require_stopped(plan.process_targets)
            if cancel and cancel.is_set():
                raise InterruptedError('Cancelled')
            step = {'argv': list(command), 'status': 'started'}
            receipt['commands'].append(step)
            history.save(api, receipt)
            try:
                ok = api.run_command(command, False, plan.action)
            except (OSError, ValueError) as exc:
                step['status'] = 'failed'
                step['error'] = str(exc)
                history.save(api, receipt)
                if plan.action == 'optimize':
                    failures += 1
                    api.notice(str(exc), 'error')
                    continue
                raise
            step['status'] = 'ok' if ok else 'failed'
            history.save(api, receipt)
            if not ok:
                if plan.action == 'optimize':
                    failures += 1
                    api.notice('Tác vụ thất bại; tiếp tục tác vụ bảo trì độc lập tiếp theo.', 'warn')
                    continue
                raise ValueError('Package/system command failed; related files kept')
        require_stopped(plan.process_targets)
        for index, item in enumerate(plan.items):
            if cancel and cancel.is_set():
                raise InterruptedError('Cancelled')
            try:
                item.validate()
                api.validate_user_target(item.path)
                if not unused(item.path, open_files()):
                    raise ValueError('File in use or process inspection unavailable')
                moved += put(api, item.path, receipt, item.fingerprint)
            except (OSError, ValueError) as exc:
                failures += 1
                receipt['outcomes'].append({'path': str(item.path), 'status': 'kept', 'reason': str(exc)})
                history.save(api, receipt)
                print(f'Kept {item.path}: {exc}')
            if progress:
                progress(index + 1, len(plan.items))
        if plan.directories:
            from uninstall.residuals import finish
            if cancel and cancel.is_set():
                raise InterruptedError('Cancelled')
            def record_directory(row):
                receipt['outcomes'].append(row)
                history.save(api, receipt)
            remaining = finish(api, plan.directories, record_directory)
            receipt['outcomes'].extend(remaining)
            leftovers = [row for row in remaining if row['status'] == 'remaining']
            failures += len(leftovers)
            for row in leftovers:
                api.notice(f"Còn lại: {row['path']} — {row['reason']}", 'warn')
            if not leftovers:
                api.notice('Các thư mục đã kiểm tra không còn dữ liệu. File đã chuyển vẫn nằm trong Trash.', 'ok')
    except (InterruptedError, KeyboardInterrupt):
        receipt['status'] = 'cancelled'
        failures += 1
    except (OSError, ValueError) as exc:
        receipt['error'] = str(exc)
        api.notice(str(exc), 'error')
        failures += 1
    receipt.setdefault('status', 'partial' if failures else 'complete')
    receipt['moved_bytes'] = moved
    receipt['free_before'] = free_before
    receipt['free_after'] = api.shutil.disk_usage(api.HOME).free
    history.save(api, receipt)
    api.notice(f"Trạng thái: {receipt['status']} · {len(receipt['steps'])} bước chuyển file · {len(receipt['commands'])} lệnh hệ thống", 'warn' if failures else 'ok')
    if plan.items:
        print(f'Đã chuyển vào Trash: {api.human_size(moved)} (chưa giải phóng dung lượng).')
    delta = receipt['free_after'] - free_before
    change = ('+' if delta >= 0 else '-') + api.human_size(abs(delta))
    print(f'Biến động dung lượng trống: {change} · Phiên: {plan.session_id}')
    return 1 if failures else 0
