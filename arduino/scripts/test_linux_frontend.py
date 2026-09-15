"""Authenticated source extraction must protect paths and existing outputs."""
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('build-linux-frontend.py')
spec = importlib.util.spec_from_file_location('linux_frontend', SCRIPT)
frontend = importlib.util.module_from_spec(spec); spec.loader.exec_module(frontend)


class FrontendSourceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.archive = self.root / 'source.tar'; self.output = self.root / 'unpacked'

    def write_archive(self, entries):
        with tarfile.open(self.archive, 'w') as archive:
            for name, content, link in entries:
                item = tarfile.TarInfo(name)
                if link is not None:
                    item.type = tarfile.SYMTYPE; item.linkname = link
                    archive.addfile(item)
                else:
                    item.size = len(content); item.mode = 0o644
                    archive.addfile(item, io.BytesIO(content))

    @unittest.skipIf(os.name == 'nt', 'POSIX source symlinks')
    def test_forward_internal_link_and_file_contents(self):
        self.write_archive([('source/nested/link', b'', '../value'), ('source/value', b'compiler source', None)])
        frontend.unpack_source(self.archive, self.output, 'source')
        self.assertEqual((self.output / 'nested/link').read_bytes(), b'compiler source')
        self.assertEqual(os.readlink(self.output / 'nested/link'), '../value')

    @unittest.skipIf(os.name == 'nt', 'POSIX directory aliases')
    def test_internal_source_link_under_aliased_parent(self):
        real = self.root / 'real'; real.mkdir()
        alias = self.root / 'alias'; alias.symlink_to('real', target_is_directory=True)
        self.write_archive([('source/nested/link', b'', '../value'), ('source/value', b'source', None)])
        frontend.unpack_source(self.archive, alias / 'unpacked', 'source')
        self.assertEqual((real / 'unpacked/nested/link').read_bytes(), b'source')

    @unittest.skipIf(os.name == 'nt', 'POSIX directory aliases')
    def test_existing_destination_link_cannot_create_external_directory(self):
        outside = self.root / 'outside'
        self.output.symlink_to('outside', target_is_directory=True)
        self.write_archive([('source/value', b'source', None)])
        with self.assertRaises(FileExistsError): frontend.unpack_source(self.archive, self.output, 'source')
        self.assertFalse(outside.exists())

    def test_traversal_cannot_modify_existing_external_file(self):
        outside = self.root / 'outside'; outside.write_bytes(b'preserve')
        self.write_archive([('source/../outside', b'changed', None)])
        with self.assertRaisesRegex(ValueError, 'unsafe source archive path'):
            frontend.unpack_source(self.archive, self.output, 'source')
        self.assertEqual(outside.read_bytes(), b'preserve')

    @unittest.skipIf(os.name == 'nt', 'POSIX source symlinks')
    def test_escaping_link_chain_rejected(self):
        self.write_archive([('source/one', b'', 'two'), ('source/two', b'', '../outside')])
        with self.assertRaisesRegex(ValueError, 'escapes extraction root'):
            frontend.unpack_source(self.archive, self.output, 'source')
        self.assertFalse((self.root / 'outside').exists())

    def test_absolute_link_rejected(self):
        self.write_archive([('source/link', b'', '/outside')])
        with self.assertRaisesRegex(ValueError, 'absolute or unrepresentable source link'):
            frontend.unpack_source(self.archive, self.output, 'source')

    def test_duplicate_archive_path_rejected_before_writing(self):
        self.write_archive([('source/file', b'first', None), ('source/file', b'second', None)])
        with self.assertRaisesRegex(ValueError, 'duplicate or unsupported'):
            frontend.unpack_source(self.archive, self.output, 'source')
        self.assertFalse((self.output / 'file').exists())

    def test_existing_output_is_preserved(self):
        self.output.mkdir(); sentinel = self.output / 'keep'; sentinel.write_bytes(b'original')
        self.write_archive([('source/file', b'new', None)])
        with self.assertRaises(FileExistsError): frontend.unpack_source(self.archive, self.output, 'source')
        self.assertEqual(sentinel.read_bytes(), b'original')

    @unittest.skipUnless(sys.platform == 'linux', 'Linux build entry')
    def test_build_entry_rejects_existing_directory_before_tools_run(self):
        self.output.mkdir(); sentinel = self.output / 'keep'; sentinel.write_bytes(b'original')
        result = subprocess.run([sys.executable, str(SCRIPT), '--build-root', str(self.output)], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b'absolute new directory', result.stderr)
        self.assertEqual(sentinel.read_bytes(), b'original')

    @unittest.skipIf(sys.platform == 'darwin', 'requires a non-Mac host')
    def test_macos_entry_rejects_non_native_host_before_creating_output(self):
        script = SCRIPT.with_name('build-macos-frontend.py')
        result = subprocess.run([sys.executable, str(script), '--build-root', str(self.output)], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b'native darwin arm64 is required', result.stderr)
        self.assertFalse(self.output.exists())

    @unittest.skipUnless(sys.platform == 'darwin', 'native Mac build entry')
    def test_macos_entry_preserves_existing_directory(self):
        self.output.mkdir(); sentinel = self.output / 'keep'; sentinel.write_bytes(b'original')
        script = SCRIPT.with_name('build-macos-frontend.py')
        result = subprocess.run([sys.executable, str(script), '--build-root', str(self.output)], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b'absolute new directory', result.stderr)
        self.assertEqual(sentinel.read_bytes(), b'original')


if __name__ == '__main__': unittest.main()
