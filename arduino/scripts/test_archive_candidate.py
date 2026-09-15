import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

SPEC = importlib.util.spec_from_file_location('candidate_archive',
    Path(__file__).with_name('archive-sdcc-candidate.py'))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ArchiveCandidateTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'staged'
        (self.source / 'bin').mkdir(parents=True)
        (self.source / 'bin/sdcc.exe').write_bytes(b'MZ fixture')
        self.manifest = self.source / 'MANIFEST.sha256'
        self.manifest.write_text(MODULE.sha(self.source / 'bin/sdcc.exe') + '  bin/sdcc.exe\n')

    def test_repeat_is_identical_and_round_trips(self):
        a = MODULE.archive(self.source, self.root / 'a.zip')
        b = MODULE.archive(self.source, self.root / 'b.zip')
        self.assertEqual(a['sha256'], b['sha256'])
        with zipfile.ZipFile(a['path']) as archive:
            self.assertEqual(archive.read('sdcc-mcs251/bin/sdcc.exe'), b'MZ fixture')
            self.assertEqual(len(archive.infolist()), 2)

    def test_unlisted_and_changed_files_rejected(self):
        extra = self.source / 'extra.dll'
        extra.write_bytes(b'unlisted')
        with self.assertRaisesRegex(ValueError, 'manifest'):
            MODULE.archive(self.source, self.root / 'extra.zip')
        extra.unlink()
        (self.source / 'bin/sdcc.exe').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'manifest'):
            MODULE.archive(self.source, self.root / 'changed.zip')

    def test_unsafe_duplicate_and_self_paths_rejected(self):
        original = self.manifest.read_text()
        for content in (original + original, original.replace('bin/sdcc.exe', '../escape'),
                        original.replace('bin/sdcc.exe', '/absolute'),
                        original.replace('bin/sdcc.exe', 'MANIFEST.sha256')):
            self.manifest.write_text(content)
            with self.assertRaisesRegex(ValueError, 'manifest path'):
                MODULE.archive(self.source, self.root / 'bad.zip')

    def test_existing_output_is_preserved(self):
        output = self.root / 'existing.zip'
        output.write_bytes(b'existing evidence')
        with self.assertRaises(FileExistsError):
            MODULE.archive(self.source, output)
        self.assertEqual(output.read_bytes(), b'existing evidence')


if __name__ == '__main__':
    unittest.main()
