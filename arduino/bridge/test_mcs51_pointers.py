"""Regress real O0/Oz MCS51 vptr and member-call lowering failures."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('mcs51_cli', Path(__file__).with_name('adapt.py'))
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
shared = cli.load_canary_adapter()

HEADER = 'define internal i16 @invoke(ptr %0, ptr nocapture noundef readonly byval({ i16, i16 }) align 1 %1, i16 %2) addrspace(1) {'
BODY = '''
  %4 = load i16, ptr %1, align 1, !tbaa !2
  %5 = getelementptr inbounds nuw i8, ptr %1, i24 2
  %6 = load i16, ptr %5, align 1, !tbaa !2
  %7 = sext i16 %6 to i24
  %8 = getelementptr inbounds i8, ptr %0, i24 %7
  %9 = and i16 %4, 1
  %10 = icmp eq i16 %9, 0
  br i1 %10, label %18, label %11
11:
  %12 = load ptr, ptr %8, align 1, !tbaa !5
  %13 = sext i16 %4 to i24
  %14 = getelementptr i8, ptr %12, i24 %13
  %15 = getelementptr i8, ptr %14, i24 -1
  %16 = load ptr, ptr %15, align 1, !nosanitize !7
  %17 = addrspacecast ptr %16 to ptr addrspace(1)
  br label %20
18:
  %19 = inttoptr i16 %4 to ptr addrspace(1)
  br label %20
20:
  %21 = phi ptr addrspace(1) [ %17, %11 ], [ %19, %18 ]
  %22 = tail call noundef addrspace(1) i16 %21(ptr noundef nonnull align 1 dereferenceable(3) %8, i16 noundef %2)
  ret i16 %22
'''
TYPES = '''struct l_array_3_void_KC_ {
  void* array[3];
};
struct l_vtable {
  struct l_array_3_void_KC_ field0;
};
'''
DEFINITION = 'static const struct l_vtable _ZTV4Base = { { { ((void*)0), ((void*)0), ((void*)0) } } };\n'
CLASSIC = '  *(void**)_111 = (((&(&(&_ZTV4Base)->field0)->array[((int32_t)2)])));\n'
OPTIMIZED = '  *((void**)&_40) = (((&((uint8_t*)((void*)(const void*)&_ZTV4Base))[((signed _BitInt(24))6)])));\n'


class OptimizedMemberCall(unittest.TestCase):
    def accepted(self, body=BODY, header=HEADER):
        return cli.audit_optimized_member_call(body, '%19', '%4', 16, 'mcs51', header)

    def test_real_byval_member_call(self):
        self.assertTrue(self.accepted())
        report = cli.audit_stc_pointer_integer_conversions(HEADER + BODY + '}\n', 'mcs51')
        self.assertEqual(report['program_address_space_member_calls'], 1)

    def test_reject_missing_or_wrong_pair_contract(self):
        for header in (HEADER.replace('byval({ i16, i16 }) ', ''),
                       HEADER.replace('i16, i16', 'i16, i32'),
                       HEADER.replace('align 1 %1', 'align 1 %3')):
            with self.subTest(header=header):
                self.assertFalse(self.accepted(header=header))

    def test_reject_disconnected_operands(self):
        for old, new in [('load i16, ptr %1', 'load i16, ptr %3'),
                         ('i8, ptr %1, i24 2', 'i8, ptr %0, i24 2'),
                         ('i8, ptr %1, i24 2', 'i8, ptr %1, i24 3'),
                         ('sext i16 %6', 'sext i16 %4'),
                         ('and i16 %4, 1', 'and i16 %6, 1'),
                         ('addrspacecast ptr %16', 'addrspacecast ptr %12'),
                         ('i24 -1', 'i24 1'),
                         ('label %18, label %11', 'label %11, label %18'),
                         ('[ %17, %11 ], [ %19, %18 ]', '[ %17, %18 ], [ %19, %11 ]'),
                         ('dereferenceable(3) %8', 'dereferenceable(3) %0')]:
            with self.subTest(old=old, new=new):
                self.assertFalse(self.accepted(BODY.replace(old, new)))

    def test_reject_generic_phi_for_program_call(self):
        self.assertFalse(self.accepted(BODY.replace('phi ptr addrspace(1)', 'phi ptr')))


class VtableCodeTag(unittest.TestCase):
    def test_both_raw_cbe_stores_preserve_code_tag(self):
        for store in (CLASSIC, OPTIMIZED):
            with self.subTest(store=store):
                result, records = cli.normalize_mcs51_vtable_address_point_stores(TYPES + DEFINITION + store)
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]['address_point_index'], 2)
                self.assertIn('= (void *)(const void __code *)', result)
                self.assertEqual(cli.normalize_mcs51_vtable_address_point_stores(result), (result, []))

    def test_reject_missing_local_definition(self):
        for store in (CLASSIC, OPTIMIZED):
            with self.subTest(store=store), self.assertRaisesRegex(RuntimeError, 'no exact local definition'):
                cli.normalize_mcs51_vtable_address_point_stores(TYPES + store)

    def test_reject_unknown_store_shapes(self):
        for store in (CLASSIC.replace('field0', 'field1'),
                      OPTIMIZED.replace('))6)', '))9)'),
                      OPTIMIZED.replace('uint8_t', 'uint16_t')):
            with self.subTest(store=store), self.assertRaises(RuntimeError):
                cli.normalize_mcs51_vtable_address_point_stores(TYPES + DEFINITION + store)

    def test_full_adapter_qualifies_before_byte_gep_lowering(self):
        for store in (CLASSIC, OPTIMIZED):
            with self.subTest(store=store):
                raw = ('typedef void (*llvm_cbe_program_pointer)(void);\n'
                       '/* Global Declarations */\n'
                       '\n/* Function definitions */\n'
                       '\n/* Types Definitions */\n' + TYPES +
                       '\n/* Global Variable Declarations */\n'
                       '\n/* Function Declarations */\n'
                       '\n/* Global Variable Definitions and Initialization */\n' + DEFINITION +
                       '\n/* LLVM Intrinsic Builtin Function Bodies */\n'
                       'void abi(void) {}\nvoid stcxx_runtime_panic(uint16_t _0) {}\n'
                       'void setup(void) {\nvoid *_111;\nvoid *_40;\n' + store + '}\n')
                result, report = cli.adapt_cbe(shared, raw, [], 'abi', 0, 'mcs51', 0, 0, [], [])
                self.assertEqual(len(report['mcs51_vtable_address_point_stores']), 1)
                self.assertIn('= (void *)(const void __code *)', result)
                self.assertIn('const uint8_t __code *)&_ZTV4Base + 6', result)

    def test_tail_virtual_call_keeps_cast_counts(self):
        source = '  _35 =  /*tail*/ ((l_fptr_3*)(((llvm_cbe_program_pointer)_34)))(_31, _32);\n'
        result, records = cli.normalize_mcs51_program_pointer_casts(source, 1, 0)
        self.assertIn('/*tail*/', result)
        self.assertIn('((llvm_cbe_program_pointer)(uintptr_t)_34)', result)
        self.assertEqual(records[0]['kind'], 'direct-virtual-dispatch')
        with self.assertRaisesRegex(RuntimeError, 'subset differs'):
            cli.normalize_mcs51_program_pointer_casts(source, 1, 1)
        with self.assertRaisesRegex(RuntimeError, 'cast shape'):
            cli.normalize_mcs51_program_pointer_casts(source.replace('/*tail*/', 'unexpected'), 1, 0)


if __name__ == '__main__':
    unittest.main()
