import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import test_molelinux


class ExperienceTests(unittest.TestCase):
    setUp = test_molelinux.MoleLinuxSafetyTests.setUp
    tearDown = test_molelinux.MoleLinuxSafetyTests.tearDown

    def test_menu_returns_after_action_and_exits_only_on_zero(self):
        real_main = self.mod.main
        with patch.object(self.mod.sys.stdin, 'isatty', return_value=True), patch('builtins.input', side_effect=['3', '', '0']), patch.object(self.mod, 'main', return_value=0) as dispatch, contextlib.redirect_stdout(io.StringIO()) as output:
            result = self.mod.interactive(self.mod.build_parser())
        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue().count('MoleLinux —'), 2)
        dispatch.assert_called_once_with(['optimize'])

    def test_negative_menu_input_does_not_dispatch(self):
        with patch.object(self.mod.sys.stdin, 'isatty', return_value=True), patch('builtins.input', side_effect=['-1', 'bad', '0']), patch.object(self.mod, 'main') as dispatch, contextlib.redirect_stdout(io.StringIO()):
            self.mod.interactive(self.mod.build_parser())
        dispatch.assert_not_called()

    def test_empty_clean_does_not_confirm_or_write_history(self):
        args = self.mod.build_parser().parse_args(['clean'])
        with patch('clean.scanner.open_files', return_value=set()), patch.object(self.mod, 'confirm') as confirm, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.mod.command_clean(args), 0)
        confirm.assert_not_called()
        self.assertFalse(self.mod.STATE_DIR.exists())

    def test_sparse_file_reports_allocated_and_logical_separately(self):
        from system.analyze import scan
        file = self.home / 'sparse.img'
        with file.open('wb') as stream: stream.truncate(1024**3)
        result = scan(self.home)
        row = next(row for row in result['entries'] if row['path'] == str(file))
        self.assertEqual(row['logical_bytes'], 1024**3)
        self.assertEqual(row['bytes'], file.stat().st_blocks * 512)
        self.assertFalse(result['partial'])

    def test_disk_scan_deduplicates_hardlinks_and_skips_symlink(self):
        import os
        from system.analyze import scan
        file = self.home / 'data'; file.write_bytes(b'123')
        os.link(file, self.home / 'hardlink')
        (self.home / 'link').symlink_to(file)
        result = scan(self.home)
        self.assertEqual(result['bytes'], file.stat().st_blocks * 512)
        self.assertEqual(result['skipped'], 1)

    def test_cancelled_disk_scan_is_marked_partial(self):
        import threading
        from system.analyze import scan
        (self.home / 'file').touch()
        event = threading.Event(); event.set()
        result = scan(self.home, event)
        self.assertTrue(result['partial'])

    def test_clean_reports_unknown_process_state(self):
        with patch('clean.scanner.open_files', return_value=None):
            plan = self.mod.make_clean_plan(['pip'])
        self.assertEqual(dict(plan.diagnostics)['process_unknown'], 1)

    def test_hidden_desktop_entry_is_not_listed(self):
        desktop = self.home / 'hidden.desktop'
        desktop.write_text('[Desktop Entry]\nType=Application\nName=Hidden\nHidden=true\nExec=hidden\n')
        self.assertIsNone(self.mod.parse_desktop(desktop))

    def test_paged_selection_and_search(self):
        items = [(f'Item {number}', number) for number in range(1, 32)]
        with patch('builtins.input', side_effect=['n', '/Item 30', '1']), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.mod.choose(items, 'test'), 30)

    def test_terminal_control_characters_are_escaped(self):
        from console.style import safe
        shown = safe('file\x1b[2J\nname')
        self.assertNotIn('\x1b', shown)
        self.assertNotIn('\n', shown)
        self.assertIn('name', shown)

    def test_broken_history_record_does_not_hide_good_records(self):
        import json
        root = self.mod.STATE_DIR / 'receipts'; root.mkdir(parents=True)
        (root / ('a'*32 + '.json')).write_text('[]')
        (root / ('b'*32 + '.json')).write_text(json.dumps({'version': 1, 'id': 'b'*32, 'action': 'clean', 'time': 'now', 'steps': []}))
        args = self.mod.build_parser().parse_args(['history', '--json'])
        with contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()):
            self.mod.command_history(args)
        self.assertEqual(len(json.loads(output.getvalue())), 1)

    def test_missing_snap_never_falls_back_to_apt(self):
        app = self.mod.Application('firefox_firefox', 'Firefox', '', '/snap/bin/firefox', 'firefox', 'snap')
        with patch('uninstall.backends.shutil.which', side_effect=lambda cmd: None if cmd == 'snap' else '/usr/bin/' + cmd):
            with self.assertRaises(ValueError):
                self.mod.uninstall_command(app)

    def test_cpu_guest_times_not_double_counted(self):
        from system import status
        with patch.object(status, 'read_text', return_value='cpu 10 2 5 20 3 1 1 0 7 2'):
            self.assertEqual(status.cpu_times(), (42, 23))

    def test_cpu_missing_proc_is_actionable(self):
        from system import status
        with patch.object(status, 'read_text', return_value=''):
            with self.assertRaisesRegex(ValueError, '/proc/stat'):
                status.cpu_times()

    def test_single_maintenance_selection(self):
        args = self.mod.build_parser().parse_args(['optimize', '--task', 'trim', '--dry-run'])
        with patch.object(self.mod, 'execute', return_value=0) as execute, contextlib.redirect_stdout(io.StringIO()):
            self.mod.command_optimize(args)
        self.assertEqual(execute.call_args.args[1].commands, (('pkexec', '/usr/lib/molelinux/system-helper', 'optimize', 'trim'),))

    def test_console_analyze_navigates_without_typing_paths(self):
        results = {'root': '/', 'entries': [], 'partial': False, 'skipped': 0, 'bytes': 0}
        with patch('system.analyze.scan', return_value=results) as scan, patch('system.analyze.volumes', return_value=[Path('/')]), patch.object(self.mod, 'choose', side_effect=[self.home, None]), contextlib.redirect_stdout(io.StringIO()):
            self.mod.console_analyze()
        self.assertEqual([call.args[0] for call in scan.call_args_list], [Path('/'), self.home])

    def test_config_non_object_is_rejected(self):
        self.mod.CONFIG_DIR.mkdir(parents=True)
        (self.mod.CONFIG_DIR / 'config.json').write_text('[]')
        with self.assertRaises(ValueError):
            self.mod.make_clean_plan(['pip'])

    def test_dashboard_missing_swap_and_battery_are_explicit(self):
        from console.dashboard import panels
        result = dict(cpu_percent=0, load=[0, 0, 0], memory=dict(total=0, available=0),
                      disk=dict(total=0, used=0, free=0), battery=[],
                      network=dict(rx_bytes_per_second=0, tx_bytes_per_second=0))
        text = str(panels(result))
        self.assertIn('Không có số liệu', text)
        self.assertIn('Không cấu hình', text)
        self.assertIn('Không có số liệu pin', text)

    def test_dashboard_bars_clamp_out_of_range(self):
        from console.dashboard import bar
        self.assertIn('100.0%', bar('CPU', 200))
        self.assertIn('0.0%', bar('CPU', -10))
        self.assertIn('Không có số liệu', bar('RAM', 0, 0))

    def test_free_space_change_uses_signed_human_units(self):
        from core.executor import execute
        from core.plan import OperationPlan
        from types import SimpleNamespace
        for delta, expected in [(1211846656, '+1.1GiB'), (-1048576, '-1.0MiB')]:
            with patch.object(self.mod.shutil, 'disk_usage', side_effect=[SimpleNamespace(free=2000000000), SimpleNamespace(free=2000000000+delta)]), patch.object(self.mod, 'run_command', return_value=True), contextlib.redirect_stdout(io.StringIO()) as output:
                execute(self.mod, OperationPlan('optimize', commands=(('mock',),)))
            self.assertIn(expected, output.getvalue())
            self.assertNotIn(' byte ·', output.getvalue())
