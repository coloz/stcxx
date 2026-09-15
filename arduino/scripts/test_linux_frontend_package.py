"""Regression checks for frontend provenance and dependency collection."""
import contextlib
import copy
import importlib.util
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('frontend_package', Path(__file__).with_name('package-linux-frontend.py'))
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class ReproducibleRecipeTests(unittest.TestCase):
    def build(self):
        return {'status': 'PASS', 'source_date_epoch': 1788134400, 'source_lock_sha256': 'source',
                'inputs': {'patch': {'sha256': 'patch'}}, 'build_tools': {'gcc': {'sha256': 'compiler'}},
                'llvm_cmake_directory': '/usr/lib/llvm-20/lib/cmake/llvm', 'cbe_base_archive_sha256': 'cbe',
                'artifacts': {'clang': {'sha256': 'binary'}},
                'commands': [{'name': 'build-clang', 'command': ['cmake', '--build', '/tmp/build'],
                              'exit_code': 0, 'log_sha256': 'nondeterministic-parallel-output'}],
                'started_utc': 'first', 'finished_utc': 'second'}

    def test_run_time_and_parallel_log_order_do_not_change_recipe(self):
        first = self.build(); second = copy.deepcopy(first)
        second.update(started_utc='another day', finished_utc='later', active_command={'pid': 12345})
        second['commands'][0]['log_sha256'] = 'another-valid-scheduling-order'
        self.assertEqual(package.build_recipe(first), package.build_recipe(second))

    def test_changed_binary_source_tool_or_command_changes_recipe(self):
        first = self.build()
        for key in ('inputs', 'build_tools', 'artifacts', 'commands'):
            second = copy.deepcopy(first)
            if key == 'commands': second[key][0]['command'].append('-DCHANGED=ON')
            else: second[key][next(iter(second[key]))]['sha256'] = 'changed'
            with self.subTest(key=key): self.assertNotEqual(package.build_recipe(first), package.build_recipe(second))


class DependencyTests(unittest.TestCase):
    def test_paths_with_spaces_and_loader(self):
        rows = package.parse_dependencies('''
linux-vdso.so.1 (0x00007fff)
libLLVM.so.20.1 => /tmp/frontend candidate/lib/libLLVM.so.20.1 (0x00007abc)
/lib64/ld-linux-x86-64.so.2 (0x00000001)
''')
        self.assertEqual(rows['libLLVM.so.20.1'], Path('/tmp/frontend candidate/lib/libLLVM.so.20.1'))
        self.assertEqual(rows['ld-linux-x86-64.so.2'], Path('/lib64/ld-linux-x86-64.so.2'))

    def test_missing_library_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            package.parse_dependencies('libLLVM.so.20.1 => not found')

    def test_duplicate_soname_rejected(self):
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            package.parse_dependencies('libx.so => /one/libx.so (0x1)\nlibx.so => /two/libx.so (0x2)')

    def test_static_or_invalid_executable_rejected(self):
        for output in ('', 'not a dynamic executable', 'statically linked'):
            with self.subTest(output=output), self.assertRaises(ValueError):
                package.parse_dependencies(output)

    @unittest.skipUnless(shutil.which('gcc') and shutil.which('readelf'), 'real ELF regression requires GCC/readelf')
    def test_data_only_shared_library_requires_elf_proof(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'data.c'; source.write_text('const char table[] = "fixture";\n')
            library = root / 'libtable.so.1'
            subprocess.run(['gcc', '-shared', '-nostdlib', '-fPIC', '-Wl,-soname,libtable.so.1',
                            str(source), '-o', str(library)], check=True, capture_output=True, timeout=30)
            self.assertEqual(package.dependencies(library, allow_leaf_library=True), {})
            with self.assertRaises(ValueError): package.dependencies(library)

    def test_false_static_listing_does_not_hide_needed_libraries(self):
        def runner(command):
            if command[0] == 'ldd': return 'statically linked'
            if command[1] == '-h': return 'Type: DYN (Shared object file)'
            return '(SONAME) Library soname: [libx.so]\n(NEEDED) Shared library: [missing.so]'
        with self.assertRaises(ValueError):
            package.dependencies(Path('/libx.so'), allow_leaf_library=True, runner=runner)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.inputs = self.root / 'inputs'; self.inputs.mkdir()
        for name in ('source-lock.json', 'clang-source.tar.xz', 'llvm-cbe-base.tar'):
            (self.inputs / name).write_text(name)
        self.build = {'inputs': {name: {'sha256': package.sha256(self.inputs / name)}
                                 for name in ('source-lock.json', 'clang-source.tar.xz')},
                      'source_lock_sha256': package.sha256(self.inputs / 'source-lock.json'),
                      'cbe_base_archive_sha256': package.sha256(self.inputs / 'llvm-cbe-base.tar')}

    def test_exact_snapshot_passes(self):
        self.assertEqual(len(package.verify_build_inputs(self.root, self.build)), 3)

    def test_modified_source_archive_rejected(self):
        (self.inputs / 'clang-source.tar.xz').write_bytes(b'changed after successful build')
        with self.assertRaisesRegex(ValueError, 'snapshot changed'):
            package.verify_build_inputs(self.root, self.build)

    def test_modified_cbe_archive_rejected(self):
        (self.inputs / 'llvm-cbe-base.tar').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'snapshot changed'):
            package.verify_build_inputs(self.root, self.build)

    def test_unrecorded_file_rejected(self):
        (self.inputs / 'unrecorded.patch').touch()
        with self.assertRaisesRegex(ValueError, 'unexpected'):
            package.verify_build_inputs(self.root, self.build)

    def test_missing_file_rejected(self):
        (self.inputs / 'source-lock.json').unlink()
        with self.assertRaisesRegex(ValueError, 'missing'):
            package.verify_build_inputs(self.root, self.build)

    def test_source_lock_identity_mismatch_rejected(self):
        self.build['source_lock_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'inconsistent'):
            package.verify_build_inputs(self.root, self.build)

    def test_snapshot_symlink_rejected_even_with_matching_bytes(self):
        source = self.inputs / 'source-lock.json'
        target = self.root / 'external-lock'; source.rename(target); source.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'snapshot changed'):
            package.verify_build_inputs(self.root, self.build)

    def test_existing_output_preserved_before_tool_lookup(self):
        sentinel = self.root / 'sentinel'; sentinel.write_bytes(b'keep')
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            package.main(['--build-root', '/missing', '--output', str(self.root),
                          '--patchelf', '/missing', '--patchelf-sha256', '0' * 64])
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(sentinel.read_bytes(), b'keep')


if __name__ == '__main__': unittest.main()
