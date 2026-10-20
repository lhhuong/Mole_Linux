"""Keyboard menu; curses restores terminal state before dispatching actions."""
import curses
import os
import sys
from .style import safe


def available():
    return sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get('TERM', 'dumb') != 'dumb'


def select(entries, version, initial=0):
    def screen(window):
        window.keypad(True)
        try: curses.curs_set(0)
        except curses.error: pass
        if curses.has_colors():
            curses.use_default_colors()
        colors = curses.has_colors() and not os.environ.get('NO_COLOR')
        if colors:
            curses.start_color(); curses.use_default_colors()
            for index, color in enumerate([curses.COLOR_GREEN, curses.COLOR_MAGENTA, curses.COLOR_YELLOW,
                                           curses.COLOR_BLUE, curses.COLOR_CYAN, curses.COLOR_MAGENTA], 1):
                curses.init_pair(index, color, -1)
        cursor = initial
        while True:
            window.erase()
            height, width = window.getmaxyx()
            def put(row, text, attr=0):
                if row < height - 1:
                    try: window.addnstr(row, 2, safe(text), max(0, width - 4), attr)
                    except curses.error: pass
            put(1, 'MoleLinux — ' + version, curses.A_BOLD)
            put(2, 'Dọn dẹp · Bảo trì · Theo dõi hệ thống', curses.A_DIM)
            for index, (number, name, description, _) in enumerate(entries):
                active = index == cursor
                attr = curses.A_BOLD if active else curses.A_DIM
                if active: attr |= curses.color_pair(index + 1) if colors else curses.A_REVERSE
                put(4 + index, f'{"›" if active else " "} {number}. {name:<12} {description}', attr)
            put(12, '↑/↓ chọn · Enter mở · 1–6 mở nhanh · q/0 thoát')
            window.refresh()
            key = window.getch()
            if key in (ord('q'), ord('0'), 27): return '0'
            if key in (curses.KEY_DOWN, ord('j')): cursor = (cursor + 1) % len(entries)
            elif key in (curses.KEY_UP, ord('k')): cursor = (cursor - 1) % len(entries)
            elif key in (10, 13, curses.KEY_ENTER): return entries[cursor][0]
            elif ord('1') <= key <= ord('6'): return chr(key)
    return curses.wrapper(screen)
