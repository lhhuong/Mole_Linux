import argparse
import contextlib
import io
import json
import os
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import test_molelinux


class EngineTests(unittest.TestCase):
    setUp = test_molelinux.MoleLinuxSafetyTests.setUp
    tearDown = test_molelinux.MoleLinuxSafetyTests.tearDown

    def file(self, relative='.cache/pip/old.bin', age=30):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fixture data')
        stamp = time.time() - age * 86400
        os.utime(path, (stamp, stamp))
        return path

    def scan(self, category='pip'):
        with patch('clean.scanner.open_files', return_value=set()):
            return self.mod.make_clean_plan([category])

    def execute(self, plan, **kwargs):
        with patch('core.executor.open_files', return_value=set()), contextlib.redirect_stdout(io.StringIO()):
            return self.mod.execute(self.mod, plan, **kwargs)

    def test_each_supported_cache_has_age_filter(self):
        for category, (root, _) in self.mod.cache_roots(self.mod).items():
            with self.subTest(category=category):
                old = self.file(str((root / 'old').relative_to(self.home)))
                fresh = self.file(str((root / 'fresh').relative_to(self.home)), 0)
                paths = [item.path for item in self.scan(category).items]
                self.assertIn(old, paths)
                self.assertNotIn(fresh, paths)

    def test_unlisted_cache_and_documents_kept(self):
        self.file('.cache/unknown/session')
        self.file('Documents/report')
        self.assertEqual(self.scan().items, ())

    def test_open_file_and_unknown_process_state_kept(self):
        path = self.file()
        for state in ({str(path)}, None):
            with patch('clean.scanner.open_files', return_value=state):
                self.assertEqual(self.mod.make_clean_plan(['pip']).items, ())

    def test_recent_access_keeps_old_modified_file(self):
        path = self.file()
        os.utime(path, (time.time(), path.stat().st_mtime))
        self.assertFalse(self.scan().items)

    def test_symlink_and_hardlink_kept(self):
        path = self.file()
        (path.parent / 'alias').symlink_to(path)
        os.link(path, path.parent / 'hardlink')
        self.assertFalse(self.scan().items)

    def test_symlink_ancestor_inside_home_refused(self):
        path = self.file()
        link = self.home / 'alias'
        link.symlink_to(path.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.mod.validate_user_target(link / path.name)

    def test_protect_state_ancestors_and_trash(self):
        for path in (self.home / '.local', self.mod.STATE_DIR / 'operations.jsonl', self.mod.TRASH_FILES):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.mod.validate_user_target(path)

    def test_whitelist_descendant_protects_parent(self):
        path = self.file()
        self.mod.CONFIG_DIR.mkdir(parents=True)
        self.mod.WHITELIST_FILE.write_text(str(path) + '\n')
        with self.assertRaises(ValueError):
            self.mod.validate_user_target(path.parent)

    def test_preview_writes_nothing(self):
        self.file()
        plan = self.scan()
        self.assertEqual(self.execute(plan, dry_run=True), 0)
        self.assertFalse(self.mod.STATE_DIR.exists())
        self.assertFalse(self.mod.TRASH_FILES.exists())

    def test_changed_candidate_kept(self):
        path = self.file()
        plan = self.scan()
        path.write_text('new content after preview')
        self.assertEqual(self.execute(plan), 1)
        self.assertTrue(path.exists())

    def test_new_file_after_preview_not_added(self):
        path = self.file()
        plan = self.scan()
        other = self.file('.cache/pip/later')
        self.assertEqual(self.execute(plan), 0)
        self.assertFalse(path.exists())
        self.assertTrue(other.exists())

    def test_file_opened_after_preview_kept(self):
        path = self.file()
        plan = self.scan()
        with patch('core.executor.open_files', return_value={str(path)}):
            self.assertEqual(self.mod.execute(self.mod, plan), 1)
        self.assertTrue(path.exists())

    def test_cancel_before_first_move_has_receipt(self):
        path = self.file()
        plan = self.scan()
        event = threading.Event(); event.set()
        self.assertEqual(self.execute(plan, cancel=event), 1)
        self.assertTrue(path.exists())
        receipt = self.mod.receipts.read(self.mod, plan.session_id)
        self.assertEqual(receipt['status'], 'cancelled')

    def test_restore_unicode_spaces_and_conflict(self):
        path = self.file('.cache/pip/Dữ liệu cũ')
        plan = self.scan()
        self.assertEqual(self.execute(plan), 0)
        receipt = self.mod.receipts.read(self.mod, plan.session_id)
        path.write_text('replacement')
        self.assertEqual(self.mod.trash_backend.restore(self.mod, receipt), 1)
        self.assertEqual(path.read_text(), 'replacement')
        path.unlink()
        self.assertEqual(self.mod.trash_backend.restore(self.mod, receipt), 0)
        self.assertEqual(path.read_bytes(), b'fixture data')

    def test_interrupted_prepared_receipt_recovers(self):
        path = self.file()
        plan = self.scan()
        self.execute(plan)
        receipt = self.mod.receipts.read(self.mod, plan.session_id)
        receipt['steps'][0]['status'] = 'prepared'
        self.assertEqual(self.mod.trash_backend.restore(self.mod, receipt), 0)
        self.assertTrue(path.exists())

    def test_failed_receipt_write_leaves_source(self):
        path = self.file()
        with patch('core.history.save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.mod.trash(path)
        self.assertTrue(path.exists())

    def test_failed_package_does_not_touch_related_files(self):
        path = self.file()
        plan = self.scan()
        plan = self.mod.OperationPlan('uninstall', plan.items, (('fake-package-manager',),))
        with patch.object(self.mod, 'run_command', return_value=False):
            self.assertEqual(self.execute(plan), 1)
        self.assertTrue(path.exists())

    def test_trash_symlink_refused(self):
        path = self.file()
        self.mod.TRASH_FILES.parent.mkdir(parents=True)
        self.mod.TRASH_FILES.symlink_to(self.home)
        with self.assertRaises(ValueError):
            self.mod.trash(path)
        self.assertTrue(path.exists())

    def test_collision_does_not_overwrite_trash(self):
        first = self.file('.cache/pip/first/file')
        second = self.file('.cache/pip/second/file')
        self.mod.trash(first); self.mod.trash(second)
        self.assertEqual(len(list(self.mod.TRASH_FILES.iterdir())), 2)

    def test_invalid_config_version_refused(self):
        self.mod.CONFIG_DIR.mkdir(parents=True)
        (self.mod.CONFIG_DIR / 'config.json').write_text('{"version":99}')
        with self.assertRaises(ValueError):
            self.scan()

    def test_flatpak_keeps_data(self):
        app = self.mod.Application('org.example.App', 'Example', '', '', 'org.example.App', 'flatpak')
        with patch('uninstall.backends.shutil.which', return_value='/usr/bin/flatpak'):
            command = self.mod.uninstall_command(app)
        self.assertIn('--user', command)
        self.assertNotIn('--delete-data', command)

    def test_root_escape_env_is_not_honored(self):
        with patch.object(self.mod.os, 'geteuid', return_value=0), patch.dict(os.environ, {'MOLELINUX_ALLOW_ROOT': '1'}):
            self.assertEqual(self.mod.main(['clean', '--dry-run']), 1)

    def test_twenty_app_ids_include_owned_config_and_data_in_preview(self):
        ids = ['org.mozilla.firefox', 'com.google.Chrome', 'com.visualstudio.code', 'org.gimp.GIMP',
               'org.libreoffice.LibreOffice', 'org.videolan.VLC', 'com.spotify.Client', 'com.slack.Slack',
               'com.discordapp.Discord', 'org.telegram.desktop', 'com.obsproject.Studio', 'org.blender.Blender',
               'org.inkscape.Inkscape', 'org.audacityteam.Audacity', 'org.kde.krita', 'org.gnome.Evolution',
               'org.keepassxc.KeePassXC', 'com.bitwarden.desktop', 'org.signal.Signal', 'org.gnome.Boxes']
        for app_id in ids:
            with self.subTest(app=app_id):
                app = self.mod.Application(app_id, 'Display name', '', '')
                config = self.file(f'.config/{app_id}/credentials')
                data = self.file(f'.local/share/{app_id}/documents')
                cache = self.file(f'.cache/{app_id}/old')
                with patch.object(self.mod, 'find_app', return_value=[app]), patch.object(self.mod, 'installed_apps', return_value=[app]), patch.object(self.mod, 'uninstall_command', return_value=['mock-remove', app_id]), patch('clean.scanner.open_files', return_value=set()):
                    plan = self.mod.make_uninstall_plan(app_id)
                self.assertEqual({item.path for item in plan.items}, {cache, config, data})
                self.assertTrue(config.exists()); self.assertTrue(data.exists())

    def test_cancelled_package_plan_cannot_retry_leftovers(self):
        path = self.file()
        plan = self.mod.OperationPlan('uninstall', self.scan().items, (('fake-package-manager',),))
        event = threading.Event(); event.set()
        self.assertEqual(self.execute(plan, cancel=event), 1)
        with self.assertRaises(ValueError):
            self.mod.command_retry(argparse.Namespace(session=plan.session_id, yes=True, dry_run=False))
        self.assertTrue(path.exists())

    def test_retry_cancelled_file_plan(self):
        path = self.file()
        plan = self.scan()
        event = threading.Event(); event.set()
        self.execute(plan, cancel=event)
        with patch('core.executor.open_files', return_value=set()):
            result = self.mod.command_retry(argparse.Namespace(session=plan.session_id, yes=True, dry_run=False))
        self.assertEqual(result, 0)
        self.assertFalse(path.exists())

    def test_recently_accessed_after_preview_kept(self):
        path = self.file()
        plan = self.scan()
        os.utime(path, (time.time(), path.stat().st_mtime))
        self.assertEqual(self.execute(plan), 1)
        self.assertTrue(path.exists())

    def test_destination_symlink_race_refused(self):
        path = self.file()
        destination = self.home / 'destination'
        destination.symlink_to(self.home / 'missing')
        with self.assertRaises(FileExistsError):
            self.mod.trash_backend.move(path, destination, self.mod.Candidate.capture(path, '', 12).fingerprint)
        self.assertTrue(path.exists())
