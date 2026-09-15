"""Keep the standalone adapter's two target entry points and native ABI gate."""
import importlib.util
import json
from pathlib import Path
import unittest

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cli', Path(__file__).with_name('adapt.py'))
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
shared = cli.load_canary_adapter()
profiles = json.loads((root / 'toolchain-lock.json').read_text())['profiles']


class CliProfiles(unittest.TestCase):
    def fixture(self, target):
        p = profiles[target]
        roots = sorted(['setup', 'loop', '__stcxx_run_global_ctors',
                        'stcxx_runtime_panic', p['abi_identity_symbol']])
        qualifier = ' addrspace(1)' if target == 'mcs51' else ''
        ir = f'target triple = "{p["target_triple"]}"\ntarget datalayout = "{p["data_layout"]}"\n'
        ir += ''.join(f'define void @{name}(){qualifier} {{\n  ret void\n}}\n' for name in roots)
        return p, roots, ir

    def audit(self, target, extra=''):
        p, roots, ir = self.fixture(target)
        return cli.audit_ir(shared, ir + extra, p['target_triple'], p['data_layout'],
                            p['abi_identity_symbol'], target, roots, '0' * 64)

    def test_both_target_entry_points(self):
        for target, space in [('mcs51', 1), ('mcs251', 0)]:
            with self.subTest(target=target):
                result = self.audit(target)
                self.assertEqual(result['program_address_space_audit']['program_address_space'], space)
                self.assertIn('setup', result['native_aggregate_abi']['checked_symbols'])

    def test_native_return_rejected_at_cli_entry(self):
        for target in profiles:
            with self.subTest(target=target), self.assertRaisesRegex(RuntimeError, 'native C aggregate ABI'):
                self.audit(target, 'declare void @native_make(ptr sret({i16,i16}), i16)\n')

    def test_wrong_layout_rejected(self):
        p, roots, ir = self.fixture('mcs251')
        with self.assertRaisesRegex(RuntimeError, 'data layout'):
            cli.audit_ir(shared, ir, p['target_triple'], profiles['mcs51']['data_layout'],
                         p['abi_identity_symbol'], 'mcs251', roots, '0' * 64)


if __name__ == '__main__':
    unittest.main()
