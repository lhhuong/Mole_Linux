"""Mole's terminal palette: cyan selection, green success, yellow warning, red error."""
import contextlib
import os
import sys
import threading
import unicodedata

CODES = {'green': '0;32', 'blue': '1;34', 'cyan': '0;36', 'yellow': '0;33',
         'purple': '0;35', 'red': '0;31', 'gray': '0;38;5;244', 'bold': '1'}


def enabled(stream=None):
    return (stream or sys.stdout).isatty() and not os.environ.get('NO_COLOR') and os.environ.get('TERM') != 'dumb'


def safe(value):
    # Escape control sequences in filenames and third-party desktop names.
    return ''.join(char if char not in '\r\n\t' and unicodedata.category(char) not in {'Cc', 'Cf', 'Cs'}
                   else repr(char)[1:-1] for char in str(value))


def paint(value, color='cyan'):
    text = safe(value)
    return f'\033[{CODES[color]}m{text}\033[0m' if enabled() else text


def heading(text, color='cyan'):
    print('\n' + paint(text, color) + '\n')


def notice(text, kind='info', stream=None):
    color, mark = {'info': ('cyan', '•'), 'ok': ('green', '✓'),
                   'warn': ('yellow', '!'), 'error': ('red', '✗')}[kind]
    print(paint(mark + ' ' + str(text), color), file=stream or sys.stdout)


@contextlib.contextmanager
def activity(label):
    """TTY-only progress on stderr; never contaminate JSON/stdout."""
    if not sys.stderr.isatty() or os.environ.get('TERM') == 'dumb':
        yield
        return
    done = threading.Event()
    def spin():
        frames = ('⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏')
        index = 0
        while not done.wait(.12):
            sys.stderr.write('\r' + frames[index % len(frames)] + ' ' + safe(label))
            sys.stderr.flush(); index += 1
    worker = threading.Thread(target=spin, daemon=True)
    worker.start()
    try:
        yield
    finally:
        done.set(); worker.join()
        sys.stderr.write('\r\033[2K'); sys.stderr.flush()
