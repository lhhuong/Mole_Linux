import importlib.machinery
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

source = Path(__file__).parents[1] / 'packaging/system-helper'
loader = importlib.machinery.SourceFileLoader('molelinux_helper', str(source))
spec = importlib.util.spec_from_loader(loader.name, loader)
helper = importlib.util.module_from_spec(spec)
loader.exec_module(helper)


class HelperTests(unittest.TestCase):
    def test_no_arbitrary_path_or_option(self):
        for args in (['rm', '/home/user'], ['apt-remove', '--purge'], ['trim', '/tmp'],
                     ['apt-clean', '/etc'], ['snap-remove', '../etc'], ['snap-remove', 'a;id']):
            with self.subTest(args=args), self.assertRaises(ValueError):
                helper.command(args)

    def test_core_package_refused(self):
        with patch.object(helper.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'yes required', '')):
            with self.assertRaises(ValueError):
                helper.command(['apt-remove', 'libc6'])

    def test_dependent_removal_refused(self):
        responses = [subprocess.CompletedProcess([], 0, 'no optional', ''),
                     subprocess.CompletedProcess([], 0, 'Remv example [1]\nRemv other [2]\n', '')]
        with patch.object(helper.subprocess, 'run', side_effect=responses):
            with self.assertRaises(ValueError):
                helper.command(['apt-remove', 'example'])

    def test_apt_keeps_configuration(self):
        responses = [subprocess.CompletedProcess([], 0, 'no optional', ''),
                     subprocess.CompletedProcess([], 0, 'Remv example [1]\n', '')]
        with patch.object(helper.subprocess, 'run', side_effect=responses):
            result = helper.command(['apt-remove', 'example'])
        self.assertEqual(result, ['/usr/bin/dpkg', '--remove', '--', 'example'])

    def test_snap_keeps_snapshot(self):
        self.assertEqual(helper.command(['snap-remove', 'example']), ['/usr/bin/snap', 'remove', 'example'])

    def test_optimize_batches_fixed_tasks_and_continues_failure(self):
        responses = [subprocess.CompletedProcess([], 1), subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 0)]
        with patch.object(helper.subprocess, 'run', side_effect=responses) as run:
            self.assertEqual(helper.run_actions(['optimize', 'apt-clean', 'journal-vacuum', 'trim']), 1)
        self.assertEqual([call.args[0] for call in run.call_args_list], list(helper.FIXED.values()))

    def test_optimize_validates_whole_batch_before_running(self):
        for args in [['optimize'], ['optimize', 'trim', '/tmp'], ['optimize', 'trim', 'trim']]:
            with patch.object(helper.subprocess, 'run') as run, self.assertRaises(ValueError):
                helper.run_actions(args)
            run.assert_not_called()

    def test_purge_uses_same_dependency_guard(self):
        responses = [subprocess.CompletedProcess([], 0, 'no optional', ''),
                     subprocess.CompletedProcess([], 0, 'Remv example [1]\n', '')]
        with patch.object(helper.subprocess, 'run', side_effect=responses):
            self.assertEqual(helper.command(['apt-purge', 'example']), ['/usr/bin/dpkg', '--purge', '--', 'example'])
        self.assertEqual(helper.command(['snap-purge', 'example']), ['/usr/bin/snap', 'remove', '--purge', 'example'])

    def test_snapshot_forget_never_deletes_entire_shared_set(self):
        response = subprocess.CompletedProcess([], 0, 'Set Snap Age\n12 example 1d\n12 other 1d\n', '')
        with patch.object(helper.subprocess, 'run', return_value=response):
            self.assertEqual(helper.command(['snap-forget', '12', 'example']), ['/usr/bin/snap', 'forget', '12', 'example'])
            with self.assertRaises(ValueError): helper.command(['snap-forget', '12', 'missing'])
        for args in [['snap-forget', '12'], ['snap-forget', '../x', 'example'], ['snap-forget', '12', '--all']]:
            with self.assertRaises(ValueError): helper.command(args)
