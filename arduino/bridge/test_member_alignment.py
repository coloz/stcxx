"""Check final address width and parity with real adapter audit contracts."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cli = load('alignment_cli', 'adapt.py')
aligner = load('alignment_helper', 'align-member-functions.py')
shared = cli.load_canary_adapter()
profiles = json.loads((Path(__file__).resolve().parents[1] / 'toolchain-lock.json').read_text())['profiles']


class FinalMethodAddress(unittest.TestCase):
    def fixture(self, root, target):
        profile = profiles[target]
        abi = profile['abi_identity_symbol']
        roots = sorted(['setup', 'loop', '__stcxx_run_global_ctors', 'stcxx_runtime_panic', abi])
        space = ' addrspace(1)' if target == 'mcs51' else ''
        ir = f'target triple = "{profile["target_triple"]}"\ntarget datalayout = "{profile["data_layout"]}"\n'
        ir += ''.join(f'define void @{name}(){space} {{\n  ret void\n}}\n' for name in roots)
        ir += f'define internal void @method(){space} align 2 {{\n  ret void\n}}\n'
        ir_report = cli.audit_ir(shared, ir, profile['target_triple'], profile['data_layout'], abi,
                                 target, roots, '0' * 64)
        raw = ('typedef void (*llvm_cbe_program_pointer)(void);\n' if target == 'mcs51' else '')
        raw += ('\n/* Global Declarations */\n\n/* Function definitions */\n\n/* Types Definitions */\n'
                '\n/* Global Variable Declarations */\n\n/* Function Declarations */\n'
                'static void method(void) __FUNCTIONALIGN__(2);\n'
                '\n/* Global Variable Definitions and Initialization */\n'
                '\n/* LLVM Intrinsic Builtin Function Bodies */\n')
        raw += ''.join(f'void {name}(void) {{}}\n' for name in roots) + 'static void method(void) {}\n'
        _, cbe_report = cli.adapt_cbe(shared, raw, [], abi, 0, target, 0, 0, ['method'],
                                     ir_report['c_identifier_linkage']['protected_c_symbols'])
        ir_report['function_alignment']['original_c_symbols'] = ['method']
        audit = {'schema_version': 2, 'outcome': 'pass', 'ir': ir_report, 'llvm_cbe': cbe_report}
        (root / 'adapter.json').write_text(json.dumps(audit))
        (root / 'raw.asm').write_text('.area CSEG (CODE)\n_method:\n\tret\n')
        aligner.align_assembly(root / 'raw.asm', root / 'aligned.asm', root / 'adapter.json',
                               target, 'even', root / 'alignment.json')

    def verify(self, root, target, listing):
        with (root / 'aligned.rst').open('w', newline='\n') as stream:
            stream.write(listing)
        return aligner.verify_relocated_listing(root / 'alignment.json', root / 'aligned.rst', target)

    def test_program_width_boundaries(self):
        for target, valid, invalid in [('mcs51', 'FFFE', '010000'), ('mcs251', 'FFFFFE', '01000000')]:
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.fixture(root, target)
                result = self.verify(root, target, f'  {valid}  12 _method:\n')
                self.assertEqual(result['outcome'], 'pass')
                overflow = root / 'overflow'
                overflow.mkdir()
                self.fixture(overflow, target)
                with self.assertRaisesRegex(RuntimeError, 'exceeds .* program width'):
                    self.verify(overflow, target, f'  {invalid}  12 _method:\n')

    def test_uniform_odd_requires_audited_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root, 'mcs51')
            result = self.verify(root, 'mcs51', '  000101  12 _method:\n')
            self.assertEqual(result['outcome'], 'realign_required')
            retry = aligner.align_assembly(root / 'raw.asm', root / 'aligned.asm', root / 'adapter.json',
                                           'mcs51', 'odd', root / 'alignment.json')
            self.assertEqual(retry['realignment']['reason'], 'uniform-odd-final-addresses')
            self.assertEqual(self.verify(root, 'mcs51', '  000102  12 _method:\n')['outcome'], 'pass')

    def test_reject_duplicate_or_missing_final_label(self):
        for listing in ('', '  000100  12 _method:\n  000102  14 _method:\n'):
            with self.subTest(listing=listing), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.fixture(root, 'mcs51')
                with self.assertRaisesRegex(RuntimeError, 'occurs .* expected once'):
                    self.verify(root, 'mcs51', listing)

    def test_changed_raw_assembly_cannot_reuse_alignment_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root, 'mcs51')
            with (root / 'raw.asm').open('a') as stream:
                stream.write('\tnop\n')
            with self.assertRaisesRegex(RuntimeError, 'raw assembly hash mismatch'):
                self.verify(root, 'mcs51', '  000100  12 _method:\n')


if __name__ == '__main__':
    unittest.main()
