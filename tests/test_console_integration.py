"""Exercise the actual CLI in subprocesses and pseudo-terminals, using isolated HOME."""
import json
import fcntl
import struct
import termios
import os
import pty
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SOURCE = Path(__file__).parents[1] / 'src/molelinux.py'


class Terminal:
    def __init__(self, env, argv=None):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 120, 0, 0))
        self.process = subprocess.Popen(argv or [sys.executable, '-B', str(SOURCE)], stdin=slave,
                                        stdout=slave, stderr=slave, env=env, start_new_session=True)
        os.close(slave)
        self.data = b''; self.cursor = 0

    def expect(self, text, timeout=12):
        needle = text.encode()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            index = self.data.find(needle, self.cursor)
            if index >= 0:
                self.cursor = index + len(needle)
                return
            if select.select([self.master], [], [], .1)[0]:
                try:
                    chunk = os.read(self.master, 65536)
                except OSError:
                    break
                self.data += chunk
                if not chunk: break
        raise AssertionError(f'Terminal never printed {text!r}: {self.data.decode(errors="replace")}')

    def send(self, text):
        os.write(self.master, text.encode())

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
        self.process.wait(timeout=5)
        os.close(self.master)


class ConsoleIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.env = dict(os.environ, HOME=str(self.home), XDG_CACHE_HOME=str(self.home / '.cache'),
                        XDG_CONFIG_HOME=str(self.home / '.config'), XDG_DATA_HOME=str(self.home / '.local/share'),
                        XDG_STATE_HOME=str(self.home / '.local/state'), TERM='xterm-256color', PYTHONDONTWRITEBYTECODE='1')
        self.env.pop('NO_COLOR', None)

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *arguments):
        return subprocess.run([sys.executable, '-B', str(SOURCE), *arguments], env=self.env,
                              capture_output=True, text=True, timeout=30)

    def file(self):
        path = self.home / '.cache/pip/Dữ liệu cũ.bin'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'original payload')
        old = time.time() - 30*86400
        os.utime(path, (old, old))
        return path

    def test_actual_clean_preview_move_restore_roundtrip(self):
        path = self.file()
        preview = self.cli('clean', '--category', 'pip', '--dry-run')
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertIn(str(path), preview.stdout)
        self.assertTrue(path.exists())
        self.assertFalse((self.home / '.local/state/molelinux').exists())
        run = self.cli('clean', '--category', 'pip', '--yes')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertFalse(path.exists(), run.stdout)
        listed = self.cli('history', '--json')
        receipts = json.loads(listed.stdout)
        self.assertEqual(len(receipts), 1)
        receipt = receipts[0]
        self.assertEqual(receipt['status'], 'complete')
        restored = self.cli('restore', receipt['id'], '--yes')
        self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
        self.assertEqual(path.read_bytes(), b'original payload')
        self.assertEqual(json.loads(self.cli('history', '--json').stdout)[0]['steps'][0]['status'], 'restored')

    def test_noninteractive_cleanup_requires_explicit_yes(self):
        path = self.file()
        result = self.cli('clean', '--category', 'pip')
        self.assertEqual(result.returncode, 1)
        self.assertTrue(path.exists())
        self.assertFalse((self.home / '.local/state/molelinux').exists())

    def test_json_status_is_plain_and_valid(self):
        result = self.cli('status', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('\x1b', result.stdout)
        data = json.loads(result.stdout)
        self.assertTrue(0 <= data['cpu_percent'] <= 100)
        self.assertGreater(data['disk']['total'], 0)
        self.assertIn('network', data)

    def test_gui_subcommand_removed(self):
        result = self.cli('gui')
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('gui', self.cli('--help').stdout)

    def test_invalid_numeric_arguments(self):
        for arguments in [('analyze', '--limit', '-1'), ('status', '--interval', '0'),
                          ('status', '--interval', 'nan'), ('history', '--limit', '0')]:
            with self.subTest(arguments=arguments):
                result = self.cli(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn('Traceback', result.stderr)

    def test_real_menu_arrows_dispatch_and_return(self):
        terminal = Terminal(self.env)
        try:
            terminal.expect('Enter mở')
            # Application-mode down/up arrows: select History from Clean by wrapping.
            terminal.send('\x1bOA\n')
            terminal.expect('Nhấn Enter để quay lại menu')
            terminal.send('\n'); terminal.expect('Enter mở')
            terminal.send('q'); terminal.process.wait(timeout=5)
            self.assertEqual(terminal.process.returncode, 0)
            self.assertIn(b'32m', terminal.data)
            self.assertIn(b'35m', terminal.data)
        finally: terminal.close()

    def test_watch_interrupt_returns_to_menu(self):
        terminal = Terminal(self.env)
        try:
            terminal.expect('Enter mở')
            terminal.send('5'); terminal.expect('Network')
            terminal.expect('Power')
            terminal.process.send_signal(signal.SIGINT)
            terminal.expect('Nhấn Enter để quay lại menu')
            terminal.send('\n'); terminal.expect('Enter mở')
            terminal.send('q'); terminal.process.wait(timeout=5)
            self.assertEqual(terminal.process.returncode, 0)
        finally: terminal.close()

    def test_no_color_and_dumb_terminal(self):
        import re
        for overrides in ({'NO_COLOR': '1'}, {'TERM': 'dumb'}):
            with self.subTest(overrides=overrides):
                terminal = Terminal(dict(self.env, **overrides))
                try:
                    terminal.expect('Chọn chức năng (0–6):' if 'TERM' in overrides else 'Enter mở')
                    self.assertIsNone(re.search(rb'\x1b\[(?:3[0-7]|9[0-7])m', terminal.data))
                    terminal.send('0\n' if 'TERM' in overrides else 'q'); terminal.process.wait(timeout=5)
                finally: terminal.close()

    def test_real_terminal_multi_selection_and_filter(self):
        driver = "import sys; sys.path.insert(0, sys.argv[1]); from console.selection import select_many; print('SELECTED=' + repr(select_many(['Alpha', 'Beta', 'Gamma'])))"
        terminal = Terminal(self.env, [sys.executable, '-B', '-c', driver, str(SOURCE.parent)])
        try:
            terminal.expect('Alpha')
            terminal.send(' /Gamma\n')
            terminal.expect('Gamma')
            terminal.send(' \n')
            terminal.expect('SELECTED=[0, 2]')
            terminal.process.wait(timeout=5)
            self.assertEqual(terminal.process.returncode, 0)
        finally:
            terminal.close()

    def test_dashboard_updates_resizes_and_quits(self):
        driver = """
import sys
sys.path.insert(0, sys.argv[1])
from console.dashboard import watch
counter = 0
def snapshot():
    global counter
    counter += 1
    return dict(hostname='fixture', kernel='test', uptime_seconds=counter*60,
                cpu_percent=10 if counter == 1 else 99.9, load=[0,0,0],
                memory=dict(total=100,available=40), disk=dict(total=100,used=50,free=50),
                battery=[], network=dict(rx_bytes_per_second=0,tx_bytes_per_second=0))
watch(snapshot, .2)
print('RESTORED')
"""
        terminal = Terminal(self.env, [sys.executable, '-B', '-c', driver, str(SOURCE.parent)])
        try:
            terminal.expect('10.0%')
            terminal.expect('99.9')
            fcntl.ioctl(terminal.master, termios.TIOCSWINSZ, struct.pack('HHHH', 20, 65, 0, 0))
            terminal.process.send_signal(signal.SIGWINCH)
            terminal.send('jjjjjjjjjjjj')
            terminal.expect('Power')
            terminal.send('q'); terminal.expect('RESTORED')
            terminal.process.wait(timeout=5)
            self.assertEqual(terminal.process.returncode, 0)
            attrs = termios.tcgetattr(terminal.master)
            self.assertTrue(attrs[3] & termios.ICANON)
            self.assertTrue(attrs[3] & termios.ECHO)
        finally: terminal.close()

    def test_process_death_after_move_can_restore_prepared_receipt(self):
        path = self.file()
        driver = '''
import sys, os
sys.path.insert(0, sys.argv[1])
import molelinux
from core import trash
original = trash.move
def crash_after_move(*args):
    original(*args)
    os._exit(99)
trash.move = crash_after_move
molelinux.main(['clean', '--category', 'pip', '--yes'])
'''
        result = subprocess.run([sys.executable, '-B', '-c', driver, str(SOURCE.parent)], env=self.env,
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 99, result.stderr.decode())
        self.assertFalse(path.exists())
        receipt = json.loads(self.cli('history', '--json').stdout)[0]
        self.assertEqual(receipt['steps'][0]['status'], 'prepared')
        restored = self.cli('restore', receipt['id'], '--yes')
        self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
        self.assertEqual(path.read_bytes(), b'original payload')
