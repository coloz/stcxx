import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('macos_packager', Path(__file__).with_name('package-macos-frontend.py'))
package = importlib.util.module_from_spec(spec); spec.loader.exec_module(package)


class MacPackageBuildIdentityTests(unittest.TestCase):
    def recipe(self, root, digest='locked-binary', flag='-O2'):
        return {'inputs': {root + '/input': {'sha256': 'locked-source'}},
                'artifact': {'path': root + '/clang/bin/clang', 'sha256': digest},
                'command': ['clang', '-ffile-prefix-map=' + root + '=.', flag],
                'outside': root + '-different/input', 'status': 'PASS'}

    def test_only_complete_build_prefixes_are_normalized(self):
        first = self.recipe('/first build'); second = self.recipe('/second build')
        first['outside'] = second['outside'] = '/unrelated/input'
        a = package.canonical_build_paths(first, '/first build')
        b = package.canonical_build_paths(second, '/second build')
        self.assertEqual(a, b)
        self.assertEqual(a['artifact']['path'], '${BUILD_ROOT}/clang/bin/clang')
        self.assertEqual(a['command'][1], '-ffile-prefix-map=${BUILD_ROOT}=.')
        neighbor = package.canonical_build_paths(self.recipe('/first build'), '/first build')
        self.assertEqual(neighbor['outside'], '/first build-different/input')
        self.assertEqual(first['artifact']['path'], '/first build/clang/bin/clang')

    def test_changed_code_or_flags_still_change_identity(self):
        baseline = package.canonical_build_paths(self.recipe('/build'), '/build')
        for changed in (self.recipe('/build', digest='different'), self.recipe('/build', flag='-O0')):
            self.assertNotEqual(baseline, package.canonical_build_paths(changed, '/build'))

    def test_ambiguous_normalized_keys_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'duplicate keys'):
            package.canonical_build_paths({'/build/input': 'a', '${BUILD_ROOT}/input': 'b'}, '/build')


if __name__ == '__main__': unittest.main()
