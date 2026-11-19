"""Bundled task inputs remain specifications, with checked bytes and clear states."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from finlab.common import ROOT, LabError, file_hash, read, write
from finlab.task_packages import TASK_PACKAGE_PATH, verify


class BundledTaskPackageTests(unittest.TestCase):
    def copy(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        target=Path(temp.name)/'tasks';shutil.copytree(ROOT/TASK_PACKAGE_PATH,target)
        return target

    def test_twelve_bundled_packages_have_entries_and_true_pending_states(self):
        result=verify()
        self.assertTrue(result['passed']);self.assertEqual(result['count'],12)
        self.assertFalse(result['patch_application_required'])
        self.assertFalse(result['human_approval_required'])
        self.assertFalse(result['formal_backtest_completed'])

    def test_changed_public_content_is_refused(self):
        target=self.copy();row=read(target/'任务清单.json')['tasks'][0]
        (target/row['directory']/'public/TASK.md').write_text('changed after manifest',encoding='utf-8')
        with self.assertRaisesRegex(LabError,'TASK_PACKAGE_FILE_HASH_CHANGED'):verify(target)

    def test_entry_cannot_claim_formal_data_ready_even_when_local_hash_is_updated(self):
        target=self.copy();row=read(target/'任务清单.json')['tasks'][0]
        folder=target/row['directory'];file=folder/'public/task_input.draft.json'
        entry=read(file);entry['data_status']='frozen';write(file,entry)
        manifest=read(folder/'MANIFEST.json');manifest['files']['public/task_input.draft.json']=file_hash(file)
        write(folder/'MANIFEST.json',manifest)
        with self.assertRaisesRegex(LabError,'TASK_PACKAGE_STATUS_INVALID'):verify(target)


if __name__=='__main__':unittest.main()
