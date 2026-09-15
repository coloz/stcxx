import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('macho_frontend', Path(__file__).with_name('macho_frontend.py'))
macho = importlib.util.module_from_spec(spec); spec.loader.exec_module(macho)


class MacFrontendDependencyTests(unittest.TestCase):
    def test_install_identity_is_not_a_load_dependency(self):
        listing = '''/build path/tool:
Load command 0
          cmd LC_ID_DYLIB
      cmdsize 56
         name @rpath/libself.dylib (offset 24)
Load command 1
          cmd LC_LOAD_DYLIB
      cmdsize 56
         name /usr/lib/libSystem.B.dylib (offset 24)
Load command 2
          cmd LC_RPATH
      cmdsize 56
         path /build path/lib (offset 12)
'''
        result = macho.parse_load_commands(listing)
        self.assertEqual(result['install_name'], '@rpath/libself.dylib')
        self.assertEqual(result['dependencies'], ['/usr/lib/libSystem.B.dylib'])
        self.assertEqual(result['rpaths'], ['/build path/lib'])

    def test_rpath_order_and_loader_relative_resolution(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'bin').mkdir(); (root / 'lib').mkdir(); (root / 'other lib').mkdir()
            first = root / 'lib/impl.dylib'; first.write_bytes(b'locked')
            second = root / 'other lib/impl.dylib'; second.write_bytes(b'different')
            actual = macho.resolve_dependency('@rpath/impl.dylib', root / 'bin/clang', ['@loader_path/../lib', str(second.parent)])
            self.assertEqual(actual, first.resolve())
            first.unlink()
            with self.assertRaisesRegex(ValueError, 'unresolved'):
                macho.resolve_dependency('@loader_path/../lib/impl.dylib', root / 'bin/clang', [])

    def test_unknown_loader_context_and_system_traversal_not_accepted(self):
        for name in ('@executable_path/impl.dylib', 'relative/impl.dylib', '@rpath/../impl.dylib'):
            with self.subTest(name=name), self.assertRaises(ValueError): macho.resolve_dependency(name, Path('/tool'), [])
        self.assertFalse(macho.is_system_dependency('/usr/lib/../../opt/impl.dylib'))
        self.assertTrue(macho.is_system_dependency('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation'))

    def test_incomplete_or_empty_load_commands_are_rejected(self):
        for listing in ('tool:\n', 'tool:\nLoad command 0\n cmd LC_LOAD_DYLIB\n cmdsize 20\n'):
            with self.subTest(listing=listing), self.assertRaises(ValueError): macho.parse_load_commands(listing)


if __name__ == '__main__': unittest.main()
