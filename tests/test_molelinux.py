import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).parents[1] / "src" / "molelinux.py"


def load_module(home: Path):
    os.environ.update({"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
                       "XDG_CACHE_HOME": str(home / ".cache"), "XDG_DATA_HOME": str(home / ".local/share"),
                       "XDG_STATE_HOME": str(home / ".local/state")})
    spec = importlib.util.spec_from_file_location("molelinux_tested", SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class MoleLinuxSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.mod = load_module(self.home)

    def tearDown(self):
        self.temp.cleanup()

    def test_refuses_home_and_outside_paths(self):
        with self.assertRaises(ValueError):
            self.mod.validate_user_target(self.home)
        with self.assertRaises(ValueError):
            self.mod.validate_user_target(Path("/etc"))

    def test_refuses_symlink_that_escapes_home(self):
        link = self.home / ".cache/escape"
        link.parent.mkdir(parents=True)
        link.symlink_to("/etc")
        with self.assertRaises(ValueError):
            self.mod.trash(link, dry_run=True)

    def test_trash_moves_file_and_writes_info(self):
        item = self.home / "Downloads/old.deb"
        item.parent.mkdir(); item.write_bytes(b"payload")
        self.assertEqual(self.mod.trash(item), 7)
        self.assertFalse(item.exists())
        self.assertTrue((self.mod.TRASH_FILES / "old.deb").exists())
        self.assertTrue((self.mod.TRASH_INFO / "old.deb.trashinfo").exists())

    def test_dry_run_does_not_move(self):
        item = self.home / ".cache/sample/cache.bin"
        item.parent.mkdir(parents=True); item.write_bytes(b"1234")
        self.assertEqual(self.mod.trash(item.parent, dry_run=True), 4)
        self.assertTrue(item.exists())

    def test_leftovers_only_use_exact_top_level_names(self):
        exact = self.home / ".config/org.example.App"
        broad = self.home / ".config/org.example.App-backup"
        exact.mkdir(parents=True); broad.mkdir(parents=True)
        app = self.mod.Application("org.example.App", "Example", "", "")
        self.assertIn(exact, self.mod.app_leftovers(app))
        self.assertNotIn(broad, self.mod.app_leftovers(app))


if __name__ == "__main__":
    unittest.main()
