"""Batch removal and automatic maintenance; all mutations use an isolated HOME."""
import contextlib
import io
import os
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import test_molelinux


class BatchTests(unittest.TestCase):
    setUp = test_molelinux.MoleLinuxSafetyTests.setUp
    tearDown = test_molelinux.MoleLinuxSafetyTests.tearDown

    def app(self, name='sample'):
        return self.mod.Application(name, name, '', '/opt/' + name, name)

    def test_process_identity_is_exact_and_unrelated_files_do_not_block(self):
        from uninstall.processes import probe
        proc = self.home / 'proc'; proc.mkdir()
        for pid, target in [('10', '/opt/sample'), ('11', '/other/sample'), ('12', '/opt/sample (deleted)')]:
            directory = proc / pid; directory.mkdir()
            (directory / 'exe').symlink_to(target)
        self.assertEqual(sorted(pid for pid, _ in probe(['/opt/sample'], proc).running), [10, 12])
        self.assertEqual(probe(['/opt/sample'], proc).unknown, ())

    def test_unknown_and_running_have_distinct_messages(self):
        from uninstall.processes import ProcessState, require_stopped
        for state, text in [(ProcessState(((123, '/opt/sample'),)), 'vẫn đang chạy'),
                            (ProcessState(unknown=(456,)), 'Không đọc được executable')]:
            with patch('uninstall.processes.probe', return_value=state):
                with self.assertRaisesRegex(ValueError, text): require_stopped(['/opt/sample'])

    def test_uninstall_does_not_depend_on_global_open_file_probe(self):
        app = self.app()
        with patch.object(self.mod, 'uninstall_command', return_value=['remove', 'sample']), patch.object(self.mod, 'require_stopped') as stopped, patch('core.safety.open_files', return_value=None):
            plan = self.mod.make_uninstall_plan(app, inventory=[app])
        stopped.assert_called_once_with(('/opt/sample',))
        self.assertEqual(plan.commands, (('remove', 'sample'),))

    def test_docker_preflight_only_reads_and_schedules_stop(self):
        app = self.app('docker-desktop'); app.executable = '/opt/docker-desktop/bin/docker-desktop'
        replies = [SimpleNamespace(returncode=0, stdout=app.executable + '\n/opt/docker-desktop/bin/com.docker.backend\n'), SimpleNamespace(returncode=0, stdout='loaded\n')]
        with patch.object(self.mod, 'uninstall_command', return_value=['remove', 'docker-desktop']), patch.object(self.mod.shutil, 'which', side_effect=lambda value: value), patch.object(self.mod.subprocess, 'run', side_effect=replies) as run:
            plan = self.mod.make_uninstall_plan(app, inventory=[app])
        self.assertEqual(plan.prepare_commands, (('systemctl', '--user', 'stop', 'docker-desktop.service'),))
        self.assertIn('/opt/docker-desktop/bin/com.docker.backend', plan.process_targets)
        self.assertTrue(all('stop' not in call.args[0] for call in run.call_args_list))

    def test_stop_then_recheck_then_remove(self):
        from core.executor import execute
        from core.plan import OperationPlan
        events = []
        plan = OperationPlan('uninstall', commands=(('remove',),), prepare_commands=(('stop',),), process_targets=('/opt/sample',))
        with patch.object(self.mod, 'run_command', side_effect=lambda argv, *_: events.append(argv[0]) or True), patch('core.executor.require_stopped', side_effect=lambda _: events.append('check')), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(execute(self.mod, plan), 0)
        self.assertEqual(events, ['stop', 'check', 'remove', 'check'])

    def test_still_running_after_stop_prevents_remove(self):
        from core.executor import execute
        from core.plan import OperationPlan
        plan = OperationPlan('uninstall', commands=(('remove',),), prepare_commands=(('stop',),), process_targets=('/opt/sample',))
        with patch.object(self.mod, 'run_command', return_value=True) as run, patch('core.executor.require_stopped', side_effect=ValueError('still running')), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(execute(self.mod, plan), 1)
        self.assertEqual([call.args[0] for call in run.call_args_list], [('stop',)])

    def test_command_failure_continues_only_independent_optimize_tasks(self):
        from core.executor import execute
        from core.plan import OperationPlan
        for action, count in [('optimize', 2), ('uninstall', 1)]:
            with patch.object(self.mod, 'run_command', side_effect=[False, True]) as run, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(execute(self.mod, OperationPlan(action, commands=(('first',), ('second',)))), 1)
            self.assertEqual(run.call_count, count)

    def test_batch_deduplicates_packages(self):
        first = self.app(); second = self.app(); second.app_id = 'second-launcher'
        with patch.object(self.mod, 'make_uninstall_plan', return_value='plan') as make, contextlib.redirect_stdout(io.StringIO()):
            plans, blocked = self.mod.prepare_uninstall_batch([first, second])
        self.assertEqual(len(plans), 1); self.assertFalse(blocked); self.assertEqual(make.call_count, 1)

    def test_batch_confirms_once_and_continues_after_one_failure(self):
        from core.plan import OperationPlan
        plans = [(self.app(name), OperationPlan('uninstall', commands=((name,),))) for name in ['first', 'second']]
        with patch.object(self.mod, 'prepare_uninstall_batch', return_value=(plans, [])), patch.object(self.mod, 'confirm', return_value=True) as confirm, patch.object(self.mod, 'execute', side_effect=[1, 0]) as execute, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.mod.review_uninstall_batch([]), 1)
        self.assertEqual(confirm.call_count, 1); self.assertEqual(execute.call_count, 2)

    def test_cancelled_batch_does_not_start_next_app(self):
        from core.plan import OperationPlan
        plans = [(self.app(name), OperationPlan('uninstall', commands=((name,),))) for name in ['first', 'second']]
        with patch.object(self.mod, 'prepare_uninstall_batch', return_value=(plans, [])), patch.object(self.mod, 'confirm', return_value=True), patch.object(self.mod, 'execute', return_value=1) as execute, patch.object(self.mod.receipts, 'read', return_value={'status': 'cancelled'}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.mod.review_uninstall_batch([]), 130)
        self.assertEqual(execute.call_count, 1)

    def test_line_checkboxes_toggle_validate_and_cancel(self):
        from console.selection import line_select
        for answers, expected in [(['1 3', '1', '99', 'bad', ''], [2]), (['1', 'q'], [])]:
            with patch('builtins.input', side_effect=answers), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(line_select(['one', 'two', 'three']), expected)

    def test_removed_commands_rejected(self):
        for name in ['purge', 'installer']:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                self.mod.build_parser().parse_args([name])
            self.assertEqual(raised.exception.code, 2)

    def test_clean_defaults_to_all_known_roots(self):
        with patch.object(self.mod, 'scan') as scan:
            self.mod.make_clean_plan()
        self.assertEqual(set(scan.call_args.args[1]), set(self.mod.cache_roots(self.mod)))

    def test_menu_clean_and_optimize_have_no_scope_prompt(self):
        for number, command in [('1', 'clean'), ('3', 'optimize')]:
            with patch.object(self.mod.sys.stdin, 'isatty', return_value=True), patch('builtins.input', side_effect=[number, '', '0']), patch.object(self.mod, 'choose') as choose, patch.object(self.mod, 'main', return_value=0) as main, contextlib.redirect_stdout(io.StringIO()):
                self.mod.interactive(self.mod.build_parser())
            choose.assert_not_called()
            main.assert_called_once_with([command])

    def test_dry_run_never_stops_service_or_removes_app(self):
        from core.executor import execute
        from core.plan import OperationPlan
        plan = OperationPlan('uninstall', commands=(('remove',),), prepare_commands=(('stop',),))
        with patch.object(self.mod, 'run_command') as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(execute(self.mod, plan, dry_run=True), 0)
        run.assert_not_called()
        self.assertFalse(self.mod.STATE_DIR.exists())

    def test_clean_slow_scan_does_not_cancel_itself(self):
        import time
        path = self.home / '.cache/pip/old'
        path.parent.mkdir(parents=True); path.write_text('cache')
        os.utime(path, (time.time()-30*86400,)*2)
        with patch('clean.scanner.time.monotonic', side_effect=[0, 31, 62, 93]), patch('clean.scanner.open_files', return_value=set()):
            plan = self.mod.make_clean_plan(['pip'])
        self.assertEqual([item.path for item in plan.items], [path])

    def test_explicit_clean_cancel_produces_no_plan(self):
        import threading
        path = self.home / '.cache/pip'; path.mkdir(parents=True)
        event = threading.Event(); event.set()
        with patch('clean.scanner.open_files', return_value=set()), self.assertRaises(InterruptedError):
            self.mod.make_clean_plan(['pip'], cancel=event)
        self.assertFalse(self.mod.STATE_DIR.exists())

    def test_optimize_uses_one_auth_invocation(self):
        args = self.mod.build_parser().parse_args(['optimize', '--dry-run'])
        with patch.object(self.mod.shutil, 'which', return_value='/mock'), patch.object(self.mod, 'execute', return_value=0) as execute, contextlib.redirect_stdout(io.StringIO()):
            self.mod.command_optimize(args)
        self.assertEqual(execute.call_args.args[1].commands, (('pkexec', '/usr/lib/molelinux/system-helper', 'optimize', 'apt-clean', 'journal-vacuum', 'trim'),))

    def test_app_data_removed_to_trash_after_package_success(self):
        from core.executor import execute
        app = self.app()
        owned = self.home / '.config/sample/settings.json'
        owned.parent.mkdir(parents=True); owned.write_text('settings')
        other = self.home / '.config/sample-other/settings.json'
        other.parent.mkdir(parents=True); other.write_text('keep')
        with patch.object(self.mod, 'uninstall_command', return_value=['mock-remove']), patch.object(self.mod, 'require_stopped'):
            plan = self.mod.make_uninstall_plan(app, inventory=[app])
        with patch.object(self.mod, 'run_command', return_value=True), patch('core.executor.require_stopped'), patch('core.executor.open_files', return_value=set()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(execute(self.mod, plan), 0)
        self.assertFalse(owned.exists()); self.assertTrue(other.exists())
        self.assertEqual((self.mod.TRASH_FILES / 'settings.json').read_text(), 'settings')

    def test_unreadable_unrelated_exe_does_not_block_docker(self):
        from uninstall.processes import probe
        proc = self.home / 'proc'; task = proc / '6095'; task.mkdir(parents=True)
        (task / 'comm').write_text('chrome')
        (task / 'cmdline').write_bytes(b'/opt/google/chrome/chrome\0--type=renderer\0')
        with patch('uninstall.processes.os.readlink', side_effect=PermissionError):
            state = probe(['/opt/docker-desktop/bin/com.docker.backend'], proc)
        self.assertFalse(state.running); self.assertFalse(state.unknown)

    def test_unreadable_docker_exe_still_detected(self):
        from uninstall.processes import probe
        proc = self.home / 'proc'; task = proc / '6095'; task.mkdir(parents=True)
        (task / 'comm').write_text('com.docker.back')
        (task / 'cmdline').write_bytes(b'/opt/docker-desktop/bin/com.docker.backend\0')
        with patch('uninstall.processes.os.readlink', side_effect=PermissionError):
            state = probe(['/opt/docker-desktop/bin/com.docker.backend'], proc)
        self.assertEqual(state.running[0][0], 6095)

    def test_fully_unreadable_process_still_blocks(self):
        from uninstall.processes import probe
        proc = self.home / 'proc'; task = proc / '6095'; task.mkdir(parents=True)
        with patch('uninstall.processes.os.readlink', side_effect=PermissionError), patch('pathlib.Path.read_text', side_effect=PermissionError):
            state = probe(['/opt/docker-desktop/bin/com.docker.backend'], proc)
        self.assertEqual(state.unknown, (6095,))

    def test_removed_app_directories_restore_with_files(self):
        from core.executor import execute
        from core.trash import restore
        app = self.app()
        owned = self.home / '.config/sample/sub/settings'
        owned.parent.mkdir(parents=True); owned.write_text('settings')
        with patch.object(self.mod, 'uninstall_command', return_value=['mock']), patch.object(self.mod, 'require_stopped'):
            plan = self.mod.make_uninstall_plan(app, inventory=[app])
        with patch.object(self.mod, 'run_command', return_value=True), patch('core.executor.require_stopped'), patch('core.executor.open_files', return_value=set()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(execute(self.mod, plan), 0)
            self.assertFalse(owned.parent.parent.exists())
            receipt = self.mod.receipts.read(self.mod, plan.session_id)
            self.assertEqual(restore(self.mod, receipt), 0)
        self.assertEqual(owned.read_text(), 'settings')

    def test_residual_cleanup_keeps_new_files_and_changed_directories(self):
        from uninstall.residuals import finish
        root = self.home / '.config/sample'; root.mkdir(parents=True)
        st = root.stat()
        (root / 'new').write_text('keep')
        rows = finish(self.mod, [(str(root), st.st_dev, st.st_ino)])
        self.assertEqual(rows[0]['status'], 'remaining')
        self.assertTrue((root / 'new').exists())
        rows = finish(self.mod, [(str(root), st.st_dev, st.st_ino+1)])
        self.assertIn('changed', rows[0]['reason'])

    def test_docker_manifest_only_includes_desktop_subtree(self):
        from uninstall.residuals import extra_roots
        app = self.app('docker-desktop')
        self.assertEqual(extra_roots(self.mod, app), [self.home / '.docker/desktop'])
        self.assertEqual(extra_roots(self.mod, self.app('docker-engine')), [])

    def test_snap_snapshot_plan_targets_only_selected_app(self):
        from uninstall.backends import snapshot_commands
        app = self.app('example'); app.source = 'snap'
        response = SimpleNamespace(returncode=0, stdout='Set Snap Age Version\n12 example 2d 1\n12 other 2d 1\n13 other 1d 2\n')
        with patch('uninstall.backends.subprocess.run', return_value=response):
            commands = snapshot_commands(app)
        self.assertEqual(commands, (('pkexec', '/usr/lib/molelinux/system-helper', 'snap-forget', '12', 'example'),))
