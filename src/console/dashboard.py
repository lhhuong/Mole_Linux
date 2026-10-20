"""Compact read-only dashboard. Missing metrics are explicitly unavailable."""
import curses
import os
import time
from .style import safe


def size(value):
    for unit in ['B', 'KiB', 'MiB', 'GiB', 'TiB']:
        if abs(value) < 1024: return f'{value:.1f} {unit}'
        value /= 1024
    return f'{value:.1f} PiB'


def bar(label, used, total=100):
    if total <= 0: return f'{label:<8} Không có số liệu'
    percent = min(100, max(0, used / total * 100))
    filled = round(percent / 100 * 18)
    return f'{label:<8} {"█" * filled}{"░" * (18-filled)} {percent:5.1f}%'


def panels(data):
    memory, disk = data['memory'], data['disk']
    used = max(0, memory['total'] - memory['available'])
    swap = data.get('swap', {'total': 0, 'free': 0})
    network = data['network']
    return [
        ('CPU', [bar('Total', data['cpu_percent']), 'Load     ' + ' / '.join(map(str, data['load'])),
                 'CPU      ' + str(os.cpu_count() or '?') + ' luồng logic']),
        ('Memory', [bar('Used', used, memory['total']), f"Used     {size(used)} / {size(memory['total'])}",
                    f"Avail    {size(memory['available'])}", bar('Swap', swap['total']-swap['free'], swap['total']) if swap['total'] else 'Swap     Không cấu hình']),
        ('Disk · /', [bar('Used', disk['used'], disk['total']), f"Used     {size(disk['used'])} / {size(disk['total'])}", f"Free     {size(disk['free'])}"]),
        ('Network', [f"↓ Down   {size(network['rx_bytes_per_second'])}/s", f"↑ Up     {size(network['tx_bytes_per_second'])}/s", 'Tổng giao diện mạng, trừ loopback']),
        ('Power', [bar(battery['name'], battery['capacity']) + ' ' + battery['status'] for battery in data['battery']] or ['Không có số liệu pin'])]


def watch(snapshot, interval):
    def screen(window):
        window.keypad(True)
        try: curses.curs_set(0)
        except curses.error: pass
        if curses.has_colors():
            curses.use_default_colors()
        colors = curses.has_colors() and not os.environ.get('NO_COLOR')
        if colors:
            curses.start_color(); curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_MAGENTA, -1)
            curses.init_pair(2, curses.COLOR_GREEN, -1)
        data, deadline, scroll = None, 0, 0
        while True:
            if data is None or time.monotonic() >= deadline:
                data = snapshot()
                deadline = time.monotonic() + interval
            window.erase()
            height, width = window.getmaxyx()
            def put(y, x, text, attr=0, limit=None):
                if 0 <= y < height - 1 and x < width - 1:
                    try: window.addnstr(y, x, safe(text), max(0, min(limit or width, width - x - 1)), attr)
                    except curses.error: pass
            uptime = int(data['uptime_seconds'])
            put(0, 1, f"Status · {data['hostname']} · up {uptime//86400}d {uptime//3600%24}h {uptime//60%60}m", curses.A_BOLD)
            put(1, 1, 'Linux ' + data['kernel'], curses.A_DIM)
            columns = 2 if width >= 100 else 1
            cell = width // columns
            blocks = panels(data)
            max_scroll = max(0, ((len(blocks) + columns - 1) // columns) * 6 - max(1, height - 6))
            scroll = min(scroll, max_scroll)
            for index, (title, rows) in enumerate(blocks):
                y, x = 3 + (index // columns) * 6 - scroll, 1 + (index % columns) * cell
                if 3 <= y < height-2: put(y, x, title + ' ' + '─' * max(0, cell- len(title)-4), curses.A_BOLD | (curses.color_pair(1) if colors else 0), cell-2)
                for offset, row in enumerate(rows, 1):
                    if 3 <= y+offset < height-2: put(y+offset, x, row, curses.color_pair(2) if colors and '█' in row else 0, cell-2)
            put(height-2, 1, 'q/Esc: quay lại · ↑/↓: cuộn · Tự cập nhật', curses.A_DIM)
            window.refresh()
            window.timeout(100)
            key = window.getch()
            if key in (ord('q'), 27): return 0
            if key in (curses.KEY_DOWN, ord('j')): scroll = min(max_scroll, scroll + 1)
            elif key in (curses.KEY_UP, ord('k')): scroll = max(0, scroll - 1)
    try: return curses.wrapper(screen)
    except KeyboardInterrupt: return 0
