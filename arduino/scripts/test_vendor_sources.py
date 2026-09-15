"""Vendored sources must remain complete, reproducible and usable without Git."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('vendor_sources', Path(__file__).with_name('vendor_sources.py'))
vendor = importlib.util.module_from_spec(spec); spec.loader.exec_module(vendor)


class VendorSourceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / 'toolchain/llvm-cbe'; self.source.mkdir(parents=True)
        self.original = b'int base;\n'; self.patched = b'int stc;\n'
        self.payloads = {'backend.c': self.patched, 'binary.fixture': b'\x00\r\n\xff', 'run.sh': b'#!/bin/sh\nexit 0\n'}
        rows = {}
        for name, data in self.payloads.items():
            (self.source / name).write_bytes(data)
            rows[name] = {'mode': '100755' if name == 'run.sh' else '100644', 'sha256': vendor.digest(data)}
        rows['backend.c']['upstream_sha256'] = vendor.digest(self.original)
        self.component = {'path': 'toolchain/llvm-cbe', 'repository': 'https://example.test/source.git',
                          'commit': 'a' * 40, 'files': rows}
        self.patch = self.root / 'stc.patch'
        self.patch.write_bytes(b'--- a/backend.c\n+++ b/backend.c\n@@ -1 +1 @@\n-int base;\n+int stc;\n')
        self.lock = {name: {'repository': self.component['repository'], 'commit': self.component['commit']}
                     for name in ('clang', 'llvm_cbe')}
        self.lock['llvm_cbe'].update(patch='stc.patch', patch_sha256=vendor.digest(self.patch.read_bytes()))
        self.manifest = {'schema_version': 1, 'components': {'clang': copy.deepcopy(self.component), 'llvm_cbe': self.component}}
        self.manifest_path = self.root / 'manifest.json'
        self.lock_path = self.root / 'lock.json'
        self.save_identity()

    def save_identity(self):
        self.manifest_path.write_text(json.dumps(self.manifest), encoding='utf-8')
        self.lock['vendored_sources'] = {'manifest': 'manifest.json',
                                        'manifest_sha256': vendor.digest(self.manifest_path.read_bytes())}
        self.lock_path.write_text(json.dumps(self.lock), encoding='utf-8')

    def test_complete_checkout_with_exact_binary_bytes_needs_no_git(self):
        self.assertFalse((self.root / '.git').exists())
        lock, manifest = vendor.load_manifest(self.root, self.lock_path)
        self.assertEqual(vendor.verify_component(self.root, manifest['components']['llvm_cbe']), 3)

    def test_changed_missing_and_extra_files_are_rejected(self):
        path = self.source / 'binary.fixture'
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'source differs'):
            vendor.verify_component(self.root, self.component)
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'inventory differs'):
            vendor.verify_component(self.root, self.component)
        path.write_bytes(self.payloads['binary.fixture'])
        (self.source / 'unexpected.h').write_bytes(b'new include')
        with self.assertRaisesRegex(ValueError, 'inventory differs'):
            vendor.verify_component(self.root, self.component)

    def test_manifest_hash_and_upstream_identity_are_checked(self):
        self.manifest_path.write_bytes(self.manifest_path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'manifest differs'):
            vendor.load_manifest(self.root, self.lock_path)
        self.component['commit'] = 'b' * 40
        self.save_identity()
        with self.assertRaisesRegex(ValueError, 'upstream identity differs'):
            vendor.load_manifest(self.root, self.lock_path)

    def test_invalid_paths_are_rejected_even_in_a_relocked_manifest(self):
        for name in ('../outside', '/absolute', 'a/../outside', 'a\\b', 'a/.git/config', 'C:/outside'):
            with self.subTest(name=name):
                self.component['files'][name] = {'mode': '100644', 'sha256': '0' * 64}
                self.save_identity()
                with self.assertRaisesRegex(ValueError, 'invalid manifest path'):
                    vendor.load_manifest(self.root, self.lock_path)
                del self.component['files'][name]

    def test_windows_link_placeholder_is_verified_as_link_target(self):
        (self.source / 'link').write_bytes(b'backend.c')
        self.component['files']['link'] = {'mode': '120000', 'sha256': vendor.digest(b'backend.c')}
        self.assertEqual(vendor.verify_component(self.root, self.component), 4)
        (self.source / 'link').write_bytes(b'elsewhere')
        with self.assertRaisesRegex(ValueError, 'source differs'):
            vendor.verify_component(self.root, self.component)

    @unittest.skipIf(os.name == 'nt', 'POSIX symlink materialization')
    def test_real_symlink_matches_placeholder_and_unexpected_symlink_fails(self):
        (self.source / 'link').symlink_to('backend.c')
        self.component['files']['link'] = {'mode': '120000', 'sha256': vendor.digest(b'backend.c')}
        self.assertEqual(vendor.verify_component(self.root, self.component), 4)
        (self.source / 'backend.c').unlink()
        (self.source / 'backend.c').symlink_to('run.sh')
        with self.assertRaisesRegex(ValueError, 'unexpected source symlink'):
            vendor.verify_component(self.root, self.component)

    def test_nested_git_repository_is_rejected(self):
        (self.source / '.git').mkdir()
        with self.assertRaisesRegex(ValueError, 'nested Git repository'):
            vendor.verify_component(self.root, self.component)

    @unittest.skipUnless(shutil.which('patch'), 'requires patch')
    def test_upstream_archive_is_repeatable_and_checkout_stays_patched(self):
        archives = [self.root / 'one.tar', self.root / 'two.tar']
        for destination in archives:
            vendor.archive_cbe(self.root, self.lock, self.manifest, destination)
        self.assertEqual(archives[0].read_bytes(), archives[1].read_bytes())
        with tarfile.open(archives[0]) as archive:
            self.assertEqual(set(archive.getnames()), set(self.payloads))
            self.assertEqual(archive.extractfile('backend.c').read(), self.original)
            self.assertEqual(archive.extractfile('binary.fixture').read(), self.payloads['binary.fixture'])
            self.assertEqual(archive.getmember('run.sh').mode, 0o755)
        for name, data in self.payloads.items(): self.assertEqual((self.source / name).read_bytes(), data)

    @unittest.skipUnless(shutil.which('patch'), 'requires patch')
    def test_wrong_upstream_hash_does_not_create_archive(self):
        self.component['files']['backend.c']['upstream_sha256'] = '0' * 64
        destination = self.root / 'bad.tar'
        with self.assertRaisesRegex(ValueError, 'upstream source differs'):
            vendor.archive_cbe(self.root, self.lock, self.manifest, destination)
        self.assertFalse(destination.exists())
        self.assertEqual((self.source / 'backend.c').read_bytes(), self.patched)

    def test_existing_archive_and_unlocked_patch_are_preserved_or_rejected(self):
        destination = self.root / 'base.tar'; destination.write_bytes(b'preserve')
        with self.assertRaises(FileExistsError):
            vendor.archive_cbe(self.root, self.lock, self.manifest, destination)
        self.assertEqual(destination.read_bytes(), b'preserve')
        self.patch.write_bytes(b'wrong patch')
        with self.assertRaisesRegex(ValueError, 'patch differs'):
            vendor.archive_cbe(self.root, self.lock, self.manifest, self.root / 'new.tar')


if __name__ == '__main__': unittest.main()
