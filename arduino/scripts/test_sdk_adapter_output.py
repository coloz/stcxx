"""A mismatched reference or failed rerun must never retain a passing comparison."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('check-sdk-adapter-output.py')
spec = importlib.util.spec_from_file_location('comparison', SCRIPT)
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


class AdapterComparisonTest(unittest.TestCase):
    def test_changed_reference_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ('optimized.ll', 'raw.c', 'native-storage.json', 'c-abi-preserve.txt', 'adapted.c'):
                (root / name).write_text(name)
            manifest = {'outcome': 'pass', 'target': {'profile': 'mcs251'},
                        'bridge_artifacts': {key: comparison.digest(root / name) for name, key in (
                            ('optimized.ll', 'optimized_ir_sha256'), ('raw.c', 'llvm_cbe_raw_c_sha256'),
                            ('adapted.c', 'adapted_c_sha256'))},
                        'c_abi_preserve_sha256': comparison.digest(root / 'c-abi-preserve.txt')}
            (root / 'manifest.json').write_text(json.dumps(manifest))
            comparison.reference_inputs(root)
            for name in ('optimized.ll', 'raw.c', 'adapted.c', 'c-abi-preserve.txt'):
                with self.subTest(name=name):
                    (root / name).write_text('modified')
                    with self.assertRaises(RuntimeError):
                        comparison.reference_inputs(root)
                    (root / name).write_text(name)

    def test_missing_input_replaces_stale_pass_even_with_python_optimized(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / 'result'
            output.mkdir()
            (output / 'comparison.json').write_text('{"status":"PASS"}')
            result = subprocess.run([sys.executable, '-O', str(SCRIPT), '--bridge', str(root / 'missing'),
                                     '--output', str(output)], capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads((output / 'comparison.json').read_text())['status'], 'FAIL')

    def test_output_cannot_overwrite_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bridge = root / 'reference'
            bridge.mkdir()
            original = '{"untouched": true}'
            (bridge / 'comparison.json').write_text(original)
            result = subprocess.run([sys.executable, str(SCRIPT), '--bridge', str(bridge),
                                     '--output', str(bridge)], capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertEqual((bridge / 'comparison.json').read_text(), original)


if __name__ == '__main__':
    unittest.main()
