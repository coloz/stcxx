import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('macos_archive', Path(__file__).with_name('archive-macos-frontend.py'))
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)


class MacFrontendArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'frontend package'
        (self.root / 'bin').mkdir(parents=True)
        (self.root / 'bin/clang').write_bytes(b'locked executable')
        (self.root / 'bin/clang').chmod(0o755)
        (self.root / 'candidate-provenance.json').write_text(json.dumps(
            {'status': 'PASS', 'architecture': 'arm64', 'source_date_epoch': 1788134400}))
        self.pin = self.manifest()

    def manifest(self, append=''):
        manifest = self.root / 'MANIFEST.sha256'
        manifest.write_text(''.join(archive.sha256(p) + '  ' + p.relative_to(self.root).as_posix() + '\n'
                                   for p in sorted(self.root.rglob('*')) if p.is_file() and p != manifest) + append)
        return archive.sha256(manifest)

    def test_archive_stream_and_filesystem_metadata_reproduction(self):
        first = self.base / 'first.tar.bz2'
        report = archive.archive_package(self.root, first, self.pin)
        for path in [self.root, *self.root.rglob('*')]:
            os.utime(path, (999999, 999999))
            if path.is_dir():
                path.chmod(0o700)
        stream = io.BytesIO()
        streamed = archive.archive_package(self.root, '-', self.pin, stream)
        self.assertEqual(first.read_bytes(), stream.getvalue())
        self.assertEqual(report, streamed)
        with tarfile.open(fileobj=io.BytesIO(stream.getvalue())) as result:
            self.assertEqual(result.extractfile('stcxx-frontend/bin/clang').read(), b'locked executable')
            self.assertTrue(all(m.uid == m.gid == 0 and m.mtime == 1788134400 for m in result))

    def test_changed_missing_and_extra_payload_are_refused(self):
        tool = self.root / 'bin/clang'
        for kind in ('changed', 'missing', 'extra'):
            with self.subTest(kind=kind):
                if kind == 'changed':
                    tool.write_bytes(b'changed')
                elif kind == 'missing':
                    tool.unlink()
                else:
                    (self.root / 'extra').write_bytes(b'extra')
                with self.assertRaisesRegex(ValueError, 'complete manifest'):
                    archive.archive_package(self.root, self.base / 'invalid.tar.bz2', self.pin)
                self.assertFalse((self.base / 'invalid.tar.bz2').exists())
                tool.write_bytes(b'locked executable')
                if kind == 'extra':
                    (self.root / 'extra').unlink()

    def test_manifest_pin_duplicate_paths_and_failed_provenance(self):
        with self.assertRaisesRegex(ValueError, 'pinned identity'):
            archive.validate(self.root, '0' * 64)
        duplicate = self.manifest(archive.sha256(self.root / 'bin/clang') + '  bin/clang\n')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            archive.validate(self.root, duplicate)
        (self.root / 'candidate-provenance.json').write_text('{"status":"FAIL","architecture":"arm64"}')
        with self.assertRaisesRegex(ValueError, 'provenance'):
            archive.validate(self.root, self.manifest())

    def test_existing_output_and_output_inside_package_are_refused(self):
        output = self.base / 'existing.tar.bz2'
        output.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            archive.archive_package(self.root, output, self.pin)
        self.assertEqual(output.read_bytes(), b'keep')
        with self.assertRaisesRegex(ValueError, 'outside package'):
            archive.archive_package(self.root, self.root / 'new.tar.bz2', self.pin)
        self.assertFalse((self.root / 'new.tar.bz2').exists())

    @unittest.skipIf(os.name == 'nt', 'symlink privilege is not assumed on Windows')
    def test_symlink_payload_is_refused(self):
        (self.root / 'link').symlink_to(self.root / 'bin/clang')
        with self.assertRaisesRegex(ValueError, 'unsupported package entry'):
            archive.validate(self.root, self.pin)

    def test_cli_stream_separates_archive_and_report_with_python_optimized(self):
        command = [sys.executable, '-B', '-O', spec.origin, '--package', str(self.root),
                   '--manifest-sha256', self.pin, '--output', '-']
        result = subprocess.run(command, capture_output=True, check=True, timeout=30)
        report = json.loads(result.stderr)
        self.assertEqual(report['archive_sha256'], hashlib.sha256(result.stdout).hexdigest())
        self.assertEqual(report['archive_bytes'], len(result.stdout))
        (self.root / 'bin/clang').write_bytes(b'changed')
        failed = subprocess.run(command, capture_output=True, timeout=30)
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(failed.stdout, b'')


if __name__ == '__main__':
    unittest.main()
