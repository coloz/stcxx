"""Unified bundles preserve executable payloads and reject invalid inputs."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location('bundle', Path(__file__).with_name('package-toolchain.py'))
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


def sha(data):
    return hashlib.sha256(data).hexdigest()


class PackageTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='stcxx package test ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def component(self, name, files, *, zipped=False, links=None, hardlinks=None):
        links = links or {}
        hardlinks = hardlinks or {}
        inventory = {**files, **{key: files[value] for key, value in {**links, **hardlinks}.items()}}
        manifest = ''.join(f'{sha(data)}  {key}\n' for key, data in sorted(inventory.items())).encode()
        files = {**files, 'MANIFEST.sha256': manifest}
        path = self.root / (name + ('.zip' if zipped else '.tar.bz2'))
        if zipped:
            with zipfile.ZipFile(path, 'w') as archive:
                for key, data in files.items():
                    archive.writestr(name + '/' + key, data)
        else:
            with tarfile.open(path, 'w:bz2') as archive:
                for key, data in files.items():
                    entry = tarfile.TarInfo(name + '/' + key)
                    entry.size, entry.mode = len(data), 0o755 if key.startswith('bin/') else 0o644
                    archive.addfile(entry, io.BytesIO(data))
                for key, value in links.items():
                    entry = tarfile.TarInfo(name + '/' + key)
                    entry.type, entry.linkname = tarfile.SYMTYPE, '../' + value
                    archive.addfile(entry)
                for key, value in hardlinks.items():
                    entry = tarfile.TarInfo(name + '/' + key)
                    entry.type, entry.linkname, entry.mode = tarfile.LNKTYPE, name + '/' + value, 0o755
                    archive.addfile(entry)
        return path, sha(manifest)

    def inputs(self, mac=False):
        frontend, fm = self.component('stcxx-frontend', {'bin/clang': b'frontend', 'licenses/LLVM': b'llvm license'})
        sdcc, sm = self.component('sdcc-mcs251', {'bin/sdcc': b'compiler', 'COPYING': b'gpl'},
                                  zipped=not mac, links={'bin/license': 'COPYING'} if mac else None)
        frontend_key, sdcc_key = bundle.HOSTS['darwin-arm64' if mac else 'windows-x86_64']
        lock = {'host': 'darwin-arm64' if mac else 'windows-x86_64',
                frontend_key: {'archive_sha256': bundle.digest(frontend), 'manifest_sha256': fm},
                'tools': {'sdcc': {sdcc_key: bundle.digest(sdcc), 'native_package_manifest_sha256': sm}}}
        lock_path = self.root / 'lock.json'
        lock_path.write_text(json.dumps(lock))
        return sdcc, frontend, lock_path

    def test_both_hosts_preserve_payloads_and_are_reproducible(self):
        for mac in (False, True):
            with self.subTest(mac=mac):
                inputs = self.inputs(mac)
                first = bundle.package(*inputs, '0.1.0', self.root / ('one' + str(mac)))
                second = bundle.package(*inputs, '0.1.0', self.root / ('two' + str(mac)))
                self.assertEqual(first, second)
                with tarfile.open(self.root / ('one' + str(mac)) / first['archiveFileName']) as archive:
                    prefix = 'stcxx-toolchain/'
                    self.assertEqual(archive.extractfile(prefix + 'frontend/bin/clang').read(), b'frontend')
                    self.assertEqual(archive.extractfile(prefix + 'sdcc/bin/sdcc').read(), b'compiler')
                    self.assertEqual(archive.extractfile(prefix + 'sdcc/COPYING').read(), b'gpl')
                    self.assertEqual(archive.getmember(prefix + 'frontend/bin/clang').mode, 0o755)
                    if mac:
                        link = archive.getmember(prefix + 'sdcc/bin/license')
                        self.assertTrue(link.issym())
                        self.assertEqual(link.linkname, '../COPYING')
                    for row in archive.extractfile(prefix + 'MANIFEST.sha256').read().decode().splitlines():
                        expected, name = row.split('  ', 1)
                        self.assertEqual(sha(archive.extractfile(prefix + name).read()), expected)

    def test_component_archive_tampering_is_rejected(self):
        inputs = self.inputs()
        with inputs[0].open('ab') as stream:
            stream.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'SDCC archive differs'):
            bundle.package(*inputs, '0.1.0', self.root / 'out')

    def test_macos_hard_links_are_rebased_inside_the_bundle(self):
        _, frontend, lock_path = self.inputs(mac=True)
        sdcc, sm = self.component('sdcc-mcs251', {'aarch64/bin/sdar': b'archiver'},
                                  hardlinks={'bin/sdar': 'aarch64/bin/sdar'})
        lock = json.loads(lock_path.read_text())
        lock['tools']['sdcc'].update(native_package_archive_sha256=bundle.digest(sdcc), native_package_manifest_sha256=sm)
        lock_path.write_text(json.dumps(lock))
        report = bundle.package(sdcc, frontend, lock_path, '0.1.0', self.root / 'out')
        with tarfile.open(self.root / 'out' / report['archiveFileName']) as archive:
            link = archive.getmember('stcxx-toolchain/sdcc/bin/sdar')
            self.assertTrue(link.islnk())
            self.assertEqual(link.linkname, 'stcxx-toolchain/sdcc/aarch64/bin/sdar')
            self.assertEqual(archive.extractfile(link).read(), b'archiver')

    def test_stale_component_manifest_is_rejected(self):
        inputs = self.inputs()
        lock = json.loads(inputs[2].read_text())
        lock['windows_frontend']['manifest_sha256'] = '0' * 64
        inputs[2].write_text(json.dumps(lock))
        with self.assertRaisesRegex(ValueError, 'Component manifest differs'):
            bundle.package(*inputs, '0.1.0', self.root / 'out')

    def test_existing_release_is_not_overwritten(self):
        inputs = self.inputs()
        bundle.package(*inputs, '0.1.0', self.root / 'out')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            bundle.package(*inputs, '0.1.0', self.root / 'out')

    def test_unsafe_paths_duplicate_names_and_links_are_rejected(self):
        cases = [('pkg/../escape', None), ('/pkg/file', None), ('pkg/bin/link', '../../../escape'),
                 ('pkg/bin/link', '/tmp/escape'), ('duplicate', None)]
        for index, (name, link) in enumerate(cases):
            with self.subTest(name=name, link=link):
                path = self.root / f'unsafe{index}.tar'
                with tarfile.open(path, 'w') as archive:
                    for filename in (['pkg/File', 'pkg/file'] if name == 'duplicate' else [name]):
                        entry = tarfile.TarInfo(filename)
                        if link:
                            entry.type, entry.linkname = tarfile.SYMTYPE, link
                        archive.addfile(entry, None if link else io.BytesIO(b''))
                with self.assertRaises(ValueError):
                    bundle.unpack(path, self.root / f'unpack{index}', 'pkg')


if __name__ == '__main__':
    unittest.main()
