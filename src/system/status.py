"""Read-only Linux metrics with bounded sampling and missing-device handling."""
import datetime as dt
import os
import re
import shutil
import time
from pathlib import Path


def read_text(path, default=''):
    try:
        return path.read_text(encoding='utf-8', errors='replace').strip()
    except OSError:
        return default


def cpu_times():
    lines = read_text(Path('/proc/stat')).splitlines()
    try:
        values = [int(value) for value in lines[0].split()[1:9]]
        if len(values) < 4:
            raise ValueError
    except (IndexError, ValueError) as exc:
        raise ValueError('Không đọc được CPU từ /proc/stat') from exc
    # guest/guest_nice are already included in user/nice; do not double count.
    return sum(values), values[3] + (values[4] if len(values) > 4 else 0)


def network_bytes():
    rx = tx = 0
    for line in read_text(Path('/proc/net/dev')).splitlines():
        if ':' not in line:
            continue
        name, text = line.split(':', 1)
        values = text.split()
        if name.strip() == 'lo' or len(values) < 9:
            continue
        try:
            rx += int(values[0]); tx += int(values[8])
        except ValueError:
            continue
    return rx, tx


def status_snapshot(delay=.15):
    total1, idle1 = cpu_times()
    rx1, tx1 = network_bytes()
    started = time.monotonic()
    time.sleep(delay)
    total2, idle2 = cpu_times()
    rx2, tx2 = network_bytes()
    elapsed = max(.001, time.monotonic() - started)
    mem = {}
    for line in read_text(Path('/proc/meminfo')).splitlines():
        if ':' in line and (match := re.search(r'\d+', line)):
            mem[line.split(':', 1)[0]] = int(match.group()) * 1024
    disk = shutil.disk_usage('/')
    batteries = []
    for path in Path('/sys/class/power_supply').glob('*'):
        if read_text(path / 'type') != 'Battery':
            continue
        try:
            capacity = max(0, min(100, int(read_text(path / 'capacity'))))
        except ValueError:
            continue
        batteries.append({'name': path.name, 'capacity': capacity, 'status': read_text(path / 'status', 'Unknown')})
    delta = total2 - total1
    cpu = max(0, min(100, 100 * (delta - (idle2 - idle1)) / delta)) if delta > 0 else 0
    return {'timestamp': dt.datetime.now(dt.timezone.utc).isoformat(), 'hostname': os.uname().nodename,
            'kernel': os.uname().release, 'uptime_seconds': float(read_text(Path('/proc/uptime'), '0').split()[0]),
            'load': [round(value, 2) for value in os.getloadavg()], 'cpu_percent': round(cpu, 1),
            'memory': {'total': mem.get('MemTotal', 0), 'available': mem.get('MemAvailable', 0)},
            'swap': {'total': mem.get('SwapTotal', 0), 'free': mem.get('SwapFree', 0)},
            'disk': {'total': disk.total, 'used': disk.used, 'free': disk.free}, 'battery': batteries,
            'network': {'rx_bytes_per_second': max(0, int((rx2-rx1)/elapsed)), 'tx_bytes_per_second': max(0, int((tx2-tx1)/elapsed))}}
