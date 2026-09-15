"""Native aggregate boundaries must not silently change calling conventions."""
import importlib.util
from pathlib import Path
import unittest

adapter_path = Path(__file__).with_name('audit_and_adapt.py')
if not adapter_path.is_file():
    adapter_path = Path(__file__).resolve().parents[1] / 'tools/cpp-core-pipeline/audit_and_adapt.py'
spec = importlib.util.spec_from_file_location('adapter', adapter_path)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class NativeAggregateAbiTests(unittest.TestCase):
    def reject(self, ir, roots=()):
        with self.assertRaisesRegex(adapter.AuditError, 'unsupported native C aggregate ABI'):
            adapter.audit_native_aggregate_abi(ir, roots)

    def test_native_return(self):
        self.reject('declare void @make(ptr sret(%Pair) align 1, i16)\n')

    def test_native_argument(self):
        self.reject('declare i16 @sum(ptr byval(%Pair) align 1)\n')

    def test_native_calls_cpp(self):
        self.reject('define void @make(ptr sret(%Pair) %result, i16 %n) {\nret void\n}\n', ['make'])

    def test_internal_cpp_return_is_valid(self):
        report = adapter.audit_native_aggregate_abi(
            'define internal void @make(ptr sret(%Pair) %result) {\nret void\n}\n')
        self.assertEqual(report['checked_symbols'], [])

    def test_explicit_pointer_interface(self):
        report = adapter.audit_native_aggregate_abi(
            'declare void @make(ptr nocapture writeonly, i16 noundef)\n'
            'declare i16 @sum(ptr nocapture readonly)\n')
        self.assertEqual(report['checked_symbols'], ['make', 'sum'])

    def test_multiline_escaped_name_and_literal_struct(self):
        self.reject('declare void @"ma\\6Be"(\nptr noalias sret({i16, [2 x i8]}) align 1,\ni16)\n')
        self.reject('define void @"ma\\6Be"(ptr sret(%Pair) %p) {\nret void\n}\n', ['make'])

    def test_direct_aggregate_types(self):
        for signature in ('%Pair @f()', '{ i16, i8 } @f()',
                          'void @f(%Pair)', 'void @f({i16, i8})',
                          'void @f([2 x i8])'):
            with self.subTest(signature=signature):
                self.reject('declare ' + signature + '\n')

    def test_data_and_identifier_spelling_do_not_trigger(self):
        report = adapter.audit_native_aggregate_abi(
            '; declare void @bad(ptr sret(%Pair))\n'
            '@text = constant [5 x i8] c"sret\\00"\n'
            'declare void @"sret("(ptr %"byval(%Pair)", i16 %x)\n')
        self.assertEqual(report['checked_symbols'], ['sret('])

    def test_intrinsic_checked_separately(self):
        report = adapter.audit_native_aggregate_abi('declare {i16, i1} @llvm.uadd.with.overflow.i16(i16, i16)\n')
        self.assertEqual(report['checked_symbols'], [])


if __name__ == '__main__':
    unittest.main()
