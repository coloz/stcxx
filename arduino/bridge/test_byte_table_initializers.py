"""Regression for full-width glyph rows emitted by LLVM-CBE (SDCC warning 147)."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('adapter', Path(__file__).with_name('audit_and_adapt.py'))
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)

TYPES = '''struct l_array_3_uint8_t {
  uint8_t array[3];
};
struct l_array_3_struct_AC_l_array_3_uint8_t {
  struct l_array_3_uint8_t array[3];
};
'''

def table(rows):
    return TYPES + ('static const struct l_array_3_struct_AC_l_array_3_uint8_t '
                    'font = { { ' + rows + ' } };\n')

class ByteTableTests(unittest.TestCase):
    def test_mixed_font_rows_and_embedded_nul(self):
        source = table(r'{ "\x00\xFF!" }, { "AB" }, { { 0, 0, 0 } }')
        result, symbols = adapter.normalize_cbe_exact_byte_array_initializers(source)
        self.assertEqual(symbols, ['font'])
        self.assertIn('{ { 0u, 255u, 33u } }', result)
        self.assertIn('{ "AB" }, { { 0, 0, 0 } }', result)
        self.assertEqual(adapter.normalize_cbe_exact_byte_array_initializers(result), (result, []))

    def test_escaped_quote_backslash_and_octal(self):
        source = table(r'{ "\"\\\101" }, { "AB" }, { "CD" }')
        result, _ = adapter.normalize_cbe_exact_byte_array_initializers(source)
        self.assertIn('{ { 34u, 92u, 65u } }', result)

    def test_terminated_rows_unchanged(self):
        source = table('{ "AB" }, { "CD" }, { "EF" }')
        self.assertEqual(adapter.normalize_cbe_exact_byte_array_initializers(source), (source, []))

    def test_one_dimensional_rule_preserved(self):
        source = TYPES + 'static const struct l_array_3_uint8_t bytes = { "ABC" };\n'
        result, symbols = adapter.normalize_cbe_exact_byte_array_initializers(source)
        self.assertEqual(symbols, ['bytes'])
        self.assertIn('{ { 65u, 66u, 67u } }', result)

    def test_reject_invalid_shape(self):
        for rows in ['{ "ABCD" }, { "EF" }, { "GH" }',
                     '{ "A" }, { "EF" }, { "GH" }',
                     '{ "ABC" }, { "EF" }, { "GH" }, { "IJ" }',
                     '{ "ABC" }, unknown, { "GH" }',
                     '{ "ABC" }, { { 0, 0, 256 } }, { "GH" }']:
            with self.subTest(rows=rows), self.assertRaises(adapter.AuditError):
                adapter.normalize_cbe_exact_byte_array_initializers(table(rows))

    def test_reject_mismatched_wrapper(self):
        source = table('{ "ABC" }, { "DE" }, { "FG" }').replace('uint8_t array[3]', 'uint8_t array[4]')
        with self.assertRaises(adapter.AuditError):
            adapter.normalize_cbe_exact_byte_array_initializers(source)

    def test_three_dimensions(self):
        source = TYPES + '''struct l_array_2_struct_AC_l_array_3_struct_AC_l_array_3_uint8_t {
  struct l_array_3_struct_AC_l_array_3_uint8_t array[2];
};
static const struct l_array_2_struct_AC_l_array_3_struct_AC_l_array_3_uint8_t cube = { { { { { "ABC" }, { "DE" }, { "FG" } } }, { { { "HIJ" }, { "KL" }, { "MN" } } } } };
'''
        result, names = adapter.normalize_cbe_exact_byte_array_initializers(source)
        self.assertEqual(names, ['cube'])
        self.assertIn('{ { 65u, 66u, 67u } }', result)
        self.assertIn('{ { 72u, 73u, 74u } }', result)

    def test_record_field_and_array_of_records(self):
        source = TYPES + '''struct Packet {
  uint16_t field0;
  struct l_array_3_uint8_t field1;
};
struct l_array_2_struct_AC_Packet {
  struct Packet array[2];
};
static const struct l_array_2_struct_AC_Packet records = { { { 12, { "ABC" } }, { 34, { "DEF" } } } };
'''
        result, names = adapter.normalize_cbe_exact_byte_array_initializers(source)
        self.assertEqual(names, ['records'])
        self.assertIn('{ 12, { { 65u, 66u, 67u } } }', result)

    def test_mutable_and_external_tables(self):
        source = table('{ "ABC" }, { "DE" }, { "FG" }')
        for prefix in ('static ', '', 'const '):
            with self.subTest(prefix=prefix):
                result, names = adapter.normalize_cbe_exact_byte_array_initializers(source.replace('static const ', prefix))
                self.assertEqual(names, ['font'])
                self.assertIn(prefix+'struct l_array_3_struct_AC_l_array_3_uint8_t font', result)

    def test_zero_aggregate_recursively_braced(self):
        source = TYPES + 'static const struct l_array_3_struct_AC_l_array_3_uint8_t zero;\n'
        result, names = adapter.normalize_cbe_exact_byte_array_initializers(source)
        self.assertEqual(names, ['zero'])
        self.assertIn('zero = { { { { 0 } } } };', result)
        self.assertEqual(adapter.normalize_cbe_exact_byte_array_initializers(result), (result, []))

    def test_forward_declaration_untouched(self):
        declaration = 'const static struct l_array_3_uint8_t zero;'
        source = TYPES+'\n/* Global Variable Declarations */\n'+declaration+'\n/* Global Variable Definitions and Initialization */\nstatic const struct l_array_3_uint8_t zero;\n/* LLVM Intrinsic Builtin Function Bodies */\n'
        result, names = adapter.normalize_cbe_exact_byte_array_initializers(source)
        self.assertEqual(names, ['zero'])
        self.assertIn(declaration, result)
        self.assertIn('zero = { { 0 } };', result)

    def test_string_braces_and_commas_are_not_delimiters(self):
        source = table(r'{ "},{" }, { "AB" }, { "CD" }')
        result, names = adapter.normalize_cbe_exact_byte_array_initializers(source)
        self.assertEqual(names, ['font'])
        self.assertIn('125u, 44u, 123u', result)


class LexicalTests(unittest.TestCase):
    def test_arithmetic_rewrite_preserves_data_and_comments(self):
        data = 'const char *s="llvm_udiv_u32(_1,  256)"; // llvm_urem_u32(_2, 16)\n'
        code = '_3 = llvm_udiv_u32(_1, 256);\n'
        result, records = adapter.normalize_cbe_u32_power_of_two_division(data+code)
        self.assertTrue(result.startswith(data))
        self.assertIn('(((uint32_t)_1) >> 8u)', result)
        self.assertEqual(len(records), 1)

    def test_trap_literal_and_comment_are_not_code(self):
        source = 'const char *s="__builtin_trap();"; /* __builtin_trap(); */\n__builtin_trap();\n'
        result = adapter.replace_c_token(source, '__builtin_trap();', 'panic();')
        self.assertEqual(result, source.rsplit('__builtin_trap();', 1)[0]+'panic();\n')

    def test_escaped_quotes_char_literals_and_newlines(self):
        source = '"a\\\"/*literal*/"; \'"\'; /* comment\n */\nreal();'
        masked = adapter.mask_c_data(source)
        self.assertEqual(len(masked), len(source))
        self.assertEqual(masked.count('\n'), source.count('\n'))
        self.assertNotIn('literal', masked)
        self.assertIn('real();', masked)

    def test_llvm_keywords_in_data_not_instructions(self):
        ir = '@s = constant [30 x i8] c"poison undef thread_local\\00"\n; poison\ndefine void @f() {\n ret void\n}\n'
        masked = adapter.mask_llvm_data(ir)
        for keyword in ('poison', 'undef', 'thread_local'):
            self.assertNotIn(keyword, masked)
        self.assertIn('ret void', masked)
        self.assertEqual(len(ir), len(masked))

    def test_real_poison_and_intrinsic_still_rejected(self):
        masked = adapter.mask_llvm_data('define i8 @f() { ret i8 poison }')
        self.assertIsNotNone(adapter.FORBIDDEN_IR_PATTERNS['unstable_value'].search(masked))
        with self.assertRaises(adapter.AuditError):
            adapter.collect_intrinsics(adapter.mask_llvm_data('call void @llvm.unknown()'))

    def test_memory_function_name_in_literal(self):
        headers, functions = adapter.extract_cbe_native_string_header('', 'static const char *s="memcpy(a,b,3)";')
        self.assertEqual((headers, functions), ([], []))

    def test_float_helper_names_in_literal(self):
        source = 'const char *s="llvm_fcmp_oeq(a,b) llvm_cbe_is_fpclass_f32(a,b) ConstantFloatTy ConstantFP128Ty";'
        for check in (adapter.extract_cbe_fcmp_helpers,
                      adapter.extract_cbe_fpclass_helpers,
                      adapter.extract_cbe_fp_constant_typedefs):
            self.assertEqual(check('', source), ([], []))

    def test_ir_collectors_ignore_literal_instructions(self):
        ir = '@text = constant [40 x i8] c"@llvm.fake ptrtoint inttoptr\\00"\n'
        self.assertEqual(adapter.collect_intrinsics(ir), [])
        self.assertEqual(adapter.audit_pointer_integer_conversions(ir)['count'], 0)

    def test_empty_function_typedef_block(self):
        source = '\n/* Function definitions */\n\n/* Types Definitions */\n'
        _, before, after = adapter.normalize_cbe_function_typedefs(source)
        self.assertEqual((before, after), ([], []))

    def test_malformed_function_typedef_still_rejected(self):
        source = '\n/* Function definitions */\nint surprise;\n/* Types Definitions */\n'
        with self.assertRaises(adapter.AuditError):
            adapter.normalize_cbe_function_typedefs(source)

    def test_negation_uses_unsigned_subtraction(self):
        for bits in (8, 16, 32, 64):
            source = f'static __forceinline uint{bits}_t llvm_neg_u{bits}(int{bits}_t a) {{\n  uint{bits}_t r = -a;\n  return r;\n}}'
            result, names = adapter.normalize_cbe_integer_negation(source)
            self.assertEqual(names, [f'llvm_neg_u{bits}'])
            self.assertIn(')0 - (uint', result)
            self.assertNotIn('= -a;', result)
            self.assertEqual(adapter.normalize_cbe_integer_negation(result), (result, []))

if __name__ == '__main__':
    unittest.main()
