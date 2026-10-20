"""Multi-selection with arrow/Space controls and a plain-terminal fallback."""
import curses
import os
import sys
from .style import safe


def line_select(labels):
    selected = set()
    while True:
        for index, label in enumerate(labels):
            print(f"  [{'x' if index in selected else ' '}] {index + 1}. {safe(label)}")
        answer = input('Số để tích/bỏ (vd 1 3), Enter để tiếp tục, q huỷ: ').strip().lower()
        if answer == 'q': return []
        if not answer: return sorted(selected)
        try:
            numbers = {int(value) - 1 for value in answer.split()}
            if not all(0 <= index < len(labels) for index in numbers): raise ValueError
            selected.symmetric_difference_update(numbers)
        except ValueError:
            print('Chọn số hợp lệ trong danh sách.')


def select_many(labels):
    if not labels: return []
    if not sys.stdin.isatty() or not sys.stdout.isatty() or os.environ.get('TERM', 'dumb') == 'dumb':
        return line_select(labels)
    selected = set()
    def screen(window):
        window.keypad(True)
        try: curses.curs_set(0)
        except curses.error: pass
        if curses.has_colors():
            curses.use_default_colors()
        if curses.has_colors() and not os.environ.get('NO_COLOR'):
            curses.start_color(); curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)
            curses.init_pair(2, curses.COLOR_GREEN, -1)
        filtered, cursor, query = list(range(len(labels))), 0, ''
        while True:
            window.erase()
            height, width = window.getmaxyx()
            def put(row, text, attribute=0):
                if 0 <= row < height - 1:
                    try: window.addnstr(row, 1, safe(text), max(0, width - 3), attribute)
                    except curses.error: pass
            put(0, 'Uninstall · Tích chọn ứng dụng', curses.A_BOLD)
            put(1, '↑/↓: di chuyển · Space: tích/bỏ · Enter: xem kế hoạch · /: tìm · Esc/q: huỷ')
            put(2, f'Đã chọn {len(selected)} / {len(labels)} · Bộ lọc: {query or "tất cả"}')
            capacity = max(1, height - 6)
            start = (cursor // capacity) * capacity
            for position in range(start, min(len(filtered), start + capacity)):
                index = filtered[position]
                attribute = curses.A_BOLD | curses.A_REVERSE if position == cursor else 0
                if curses.has_colors() and not os.environ.get('NO_COLOR'):
                    if position == cursor or index in selected:
                        attribute |= curses.color_pair(1 if position == cursor else 2)
                put(4 + position - start, f"{'›' if position == cursor else ' '} [{'x' if index in selected else ' '}] {labels[index]}", attribute)
            if not filtered: put(4, 'Không có kết quả. Nhấn / rồi Enter để bỏ lọc.')
            put(height - 2, f'Trang {start // capacity + 1}/{max(1, (len(filtered) + capacity - 1) // capacity)} · {len(selected)} đã chọn')
            window.refresh()
            key = window.getch()
            if key in (27, ord('q')): return []
            if key in (10, 13, curses.KEY_ENTER): return sorted(selected)
            if key in (curses.KEY_DOWN, ord('j')): cursor = min(max(0, len(filtered) - 1), cursor + 1)
            elif key in (curses.KEY_UP, ord('k')): cursor = max(0, cursor - 1)
            elif key == ord(' ') and filtered:
                index = filtered[cursor]
                if index in selected: selected.remove(index)
                else: selected.add(index)
            elif key == ord('/'):
                put(height - 2, 'Tìm: ')
                curses.echo()
                try:
                    query = window.getstr(height - 2, 6, max(1, min(80, width - 8))).decode('utf-8', errors='replace').casefold()
                finally: curses.noecho()
                filtered = [index for index, label in enumerate(labels) if query in label.casefold()]
                cursor = 0
    return curses.wrapper(screen)
