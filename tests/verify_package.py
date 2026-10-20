"""Inspect and execute an extracted package without installing or needing root."""
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

package = Path(sys.argv[1]).resolve()
control = subprocess.check_output(['dpkg-deb', '--field', str(package), 'Depends'], text=True)
assert 'python3-gi' not in control and 'gir1.2-' not in control
assert 'pkexec | policykit-1' in control and 'polkitd | policykit-1' in control
with tempfile.TemporaryDirectory() as temporary:
    subprocess.run(['dpkg-deb', '--extract', str(package), temporary], check=True)
    root = Path(temporary)
    app = root / 'usr/bin/molelinux'
    assert app.is_symlink() and app.resolve().is_file()
    assert not (root / 'usr/share/molelinux/ui').exists()
    assert not list(root.rglob('*.pyc'))
    assert not (root / 'usr/share/applications/molelinux.desktop').exists()
    assert not (root / 'usr/share/metainfo/org.molelinux.MoleLinux.metainfo.xml').exists()
    helper = root / 'usr/lib/molelinux/system-helper'
    assert helper.is_file() and helper.stat().st_mode & 0o111
    ET.parse(root / 'usr/share/polkit-1/actions/org.molelinux.maintenance.policy')
    result = subprocess.run([str(app), '--version'], env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
                            capture_output=True, text=True, check=True)
    assert '1.0' in result.stdout
    help_text = subprocess.check_output([str(app), '--help'], text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
    assert all(name not in help_text for name in ('gui', 'purge', 'installer')) and 'restore' in help_text
subprocess.run(['sha256sum', '-c', package.name + '.sha256'], cwd=package.parent, check=True)
print('Package validation passed: console launcher, dependencies, modules, helper, XML, version and SHA-256')
