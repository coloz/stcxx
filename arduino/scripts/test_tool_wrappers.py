"""Published compiler wrappers must work through directory and file aliases."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

WRAPPERS = Path(__file__).resolve().parents[1] / 'wrappers'


@unittest.skipUnless(sys.platform.startswith('linux'), 'published wrappers run on Linux/WSL')
class ToolWrapperTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='stcxx wrapper test ')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / 'real toolchain'
        (self.root / 'bin').mkdir(parents=True)
        (self.root / 'libexec').mkdir()
        (self.root / 'share/sdcc').mkdir(parents=True)
        for name in ('sdcc', 'sdcpp'):
            shutil.copyfile(WRAPPERS / name, self.root / 'bin' / name)
            (self.root / 'bin' / name).chmod(0o755)
            executable = self.root / 'libexec' / name
            executable.write_text('#!' + sys.executable + '\n'
                'import json, os, sys\n'
                'print(json.dumps({"args": sys.argv[1:], "path": os.environ["PATH"].split(os.pathsep), '
                '"sdcc_home": os.environ.get("SDCC_HOME"), "gcc_prefix": os.environ.get("GCC_EXEC_PREFIX")}))\n')
            executable.chmod(0o755)

    def check_wrapper(self, command, name, env=None):
        arguments = ['--one=two words', 'literal;$(no-execution)', '', 'apostrophe\'quote', '-I/path with spaces']
        result = subprocess.run([str(command), *arguments], cwd=self.base, env=env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output['args'], arguments)
        if name == 'sdcc':
            self.assertEqual(output['sdcc_home'], str(self.root / 'share/sdcc'))
            self.assertEqual(output['path'][:2], [str(self.root / 'bin'), str(self.root / 'libexec')])
        else:
            self.assertEqual(output['gcc_prefix'], str(self.root / 'libexec') + '/')
            self.assertEqual(output['path'][:2], [str(self.root / 'libexec'), str(self.root / 'bin')])

    def test_direct_execution_from_another_directory(self):
        for name in ('sdcc', 'sdcpp'):
            with self.subTest(tool=name):
                self.check_wrapper(self.root / 'bin' / name, name)

    def test_bin_directory_symlink(self):
        alias = self.base / 'installed candidate'
        alias.mkdir()
        (alias / 'bin').symlink_to(self.root / 'bin', target_is_directory=True)
        for name in ('sdcc', 'sdcpp'):
            with self.subTest(tool=name):
                self.check_wrapper(alias / 'bin' / name, name)

    def test_absolute_wrapper_symlink(self):
        for name in ('sdcc', 'sdcpp'):
            alias = self.base / ('alias ' + name)
            alias.symlink_to(self.root / 'bin' / name)
            with self.subTest(tool=name):
                self.check_wrapper(alias, name)

    def test_relative_wrapper_symlink_chain(self):
        directory = self.base / 'links'
        directory.mkdir()
        for name in ('sdcc', 'sdcpp'):
            (directory / (name + '-next')).symlink_to('../real toolchain/bin/' + name)
            (directory / name).symlink_to(name + '-next')
            with self.subTest(tool=name):
                self.check_wrapper(Path('links') / name, name)

    def test_path_lookup_through_alias(self):
        directory = self.base / 'on path'
        directory.mkdir()
        env = os.environ.copy()
        env['PATH'] = str(directory) + os.pathsep + env['PATH']
        for name in ('sdcc', 'sdcpp'):
            (directory / name).symlink_to(self.root / 'bin' / name)
            with self.subTest(tool=name):
                self.check_wrapper(name, name, env)


if __name__ == '__main__':
    unittest.main()
