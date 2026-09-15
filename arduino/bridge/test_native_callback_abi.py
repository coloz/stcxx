"""Aggregate callbacks require proven internal provenance; native ABI is explicit."""
import importlib.util
from pathlib import Path
import unittest

adapter_path = Path(__file__).with_name('audit_and_adapt.py')
if not adapter_path.is_file():
    adapter_path = Path(__file__).resolve().parents[1] / 'tools/cpp-core-pipeline/audit_and_adapt.py'
spec = importlib.util.spec_from_file_location('callback_adapter', adapter_path)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)

PAIR = '''%Pair = type {i16, i16}
define internal void @make(ptr sret(%Pair) %out, i16 %n) {
  ret void
}
'''


def function(name, body, parameters='', linkage=''):
    return f'define {linkage}void @{name}({parameters}) {{\n{body}\n  ret void\n}}\n'


class NativeCallbackAbiTest(unittest.TestCase):
    def reject(self, source, roots=('entry',)):
        with self.assertRaisesRegex(adapter.AuditError, 'native C aggregate callback ABI'):
            adapter.audit_native_callback_abi(source, roots)

    def test_direct_outgoing_callback(self):
        self.reject(PAIR + 'declare void @native(ptr)\n' +
                    function('entry', '  call void @native(ptr @make)'))

    def test_callback_encoded_as_integer(self):
        self.reject(PAIR + 'declare void @native(i24)\n' + function('entry',
            '  %bits = ptrtoint ptr @make to i24\n  call void @native(i24 %bits)'))

    def test_incoming_native_factory(self):
        self.reject('declare ptr @factory()\n' + function('entry',
            '  %f = call ptr @factory()\n  call void %f(ptr sret({i16,i16}) %out, i16 7)', 'ptr %out'))

    def test_incoming_native_parameter_and_global(self):
        self.reject(function('entry', '  call void %f(ptr sret({i16,i16}) %out, i16 7)', 'ptr %f, ptr %out'))
        self.reject('@hook = external global ptr\n' + function('entry',
            '  %f = load ptr, ptr @hook\n  call void %f(ptr sret({i16,i16}) %out, i16 7)', 'ptr %out'))

    def test_direct_call_after_erased_cast_is_rejected(self):
        self.reject('declare void @native(ptr, i16)\n' + function('entry',
            '  call void @native(ptr sret({i16,i16}) %out, i16 7)', 'ptr %out'))

    def test_literal_aggregate_call_without_sret_is_rejected(self):
        self.reject('declare ptr @factory()\n' + function('entry',
            '  %f = call ptr @factory()\n  %pair = call {i16,i16} %f()'))

    def test_native_registration_through_local_table(self):
        self.reject(PAIR + 'declare void @native(ptr)\n' + function('entry',
            '  %table = alloca [2 x ptr]\n'
            '  %slot = getelementptr inbounds [2 x ptr], ptr %table, i16 0, i16 1\n'
            '  store ptr @make, ptr %slot\n  call void @native(ptr %table)'))

    def test_global_initializer_tables_and_escaped_names(self):
        for table in ('@hooks = global [1 x ptr] [ptr @make]\n',
                      '@hooks = internal global [1 x ptr]\n  [ptr @make]\n'):
            with self.subTest(table=table):
                self.reject(PAIR + table + 'declare void @native(ptr)\n' +
                            function('entry', '  call void @native(ptr @hooks)'))
        self.reject(PAIR.replace('@make', '@"ma\\6Be"') + 'declare void @native(ptr)\n' +
                    function('entry', '  call void @native(ptr @make)'))

    def test_internal_factory_and_indirect_return_are_allowed(self):
        source = PAIR + '''define internal ptr @factory() {
  ret ptr @make
}
''' + function('entry', '  %out = alloca %Pair\n  %slot = alloca ptr\n'
            '  %f = call ptr @factory()\n  store ptr %f, ptr %slot\n'
            '  %loaded = load ptr, ptr %slot\n  call void %loaded(ptr sret(%Pair) %out, i16 7)')
        report = adapter.audit_native_callback_abi(source, ['entry'])
        self.assertEqual(report['indirect_aggregate_calls'][0]['targets'], ['make'])

    def test_internal_select_and_indexed_table_are_allowed(self):
        source = PAIR + PAIR[PAIR.index('define'):].replace('@make', '@second')
        source += '@hooks = internal constant [2 x ptr] [ptr @make, ptr @second]\n'
        source += function('entry', '  %out = alloca %Pair\n'
            '  %chosen = select i1 %flag, ptr @make, ptr @second\n'
            '  call void %chosen(ptr sret(%Pair) %out, i16 7)\n'
            '  %slot = getelementptr inbounds [2 x ptr], ptr @hooks, i16 0, i16 %index\n'
            '  %loaded = load ptr, ptr %slot\n  call void %loaded(ptr sret(%Pair) %out, i16 7)',
            'i1 %flag, i16 %index')
        report = adapter.audit_native_callback_abi(source, ['entry'])
        self.assertEqual(len(report['indirect_aggregate_calls']), 2)
        for call in report['indirect_aggregate_calls']:
            self.assertEqual(call['targets'], ['make', 'second'])

    def test_internal_callback_parameter_is_allowed(self):
        source = PAIR + function('invoke',
            '  call void %f(ptr sret(%Pair) %out, i16 7)', 'ptr %f, ptr %out', 'internal ')
        source += function('entry', '  %out = alloca %Pair\n  call void @invoke(ptr @make, ptr %out)')
        adapter.audit_native_callback_abi(source, ['entry'])

    def test_returned_alias_cannot_hide_registration(self):
        source = PAIR + function('safe', '', linkage='internal ')
        source += '@slot = internal global ptr @safe\ndeclare void @native(ptr)\n'
        source += 'define internal ptr @get_slot() {\n  ret ptr @slot\n}\n'
        source += function('entry', '  %alias = call ptr @get_slot()\n'
            '  store ptr @make, ptr %alias\n  call void @native(ptr @slot)')
        self.reject(source)

    def test_memory_copy_cannot_hide_registration(self):
        source = PAIR + 'declare void @native(ptr)\ndeclare void @llvm.memcpy.p0.p0.i32(ptr, ptr, i32, i1)\n'
        source += function('entry', '  %src = alloca ptr\n  %dst = alloca ptr\n'
            '  store ptr @make, ptr %src\n'
            '  call void @llvm.memcpy.p0.p0.i32(ptr %dst, ptr %src, i32 3, i1 false)\n'
            '  call void @native(ptr %dst)')
        self.reject(source)

    def test_scalar_and_explicit_result_callbacks_are_allowed(self):
        source = PAIR + function('explicit_result', '', 'ptr %out, i16 %n', 'internal ')
        source += 'declare void @native(ptr)\n' + function('entry', '  call void @native(ptr @explicit_result)')
        adapter.audit_native_callback_abi(source, ['entry'])

    def test_native_root_cannot_return_aggregate_callback(self):
        self.reject(PAIR + 'define ptr @entry() {\n  ret ptr @make\n}\n')

    def test_native_output_memory_cannot_receive_aggregate_callback(self):
        self.reject(PAIR + function('entry', '  store ptr @make, ptr %out', 'ptr %out'))

    def test_aggregate_argument_attributes_are_rejected(self):
        for attribute in ('byval', 'byref', 'inalloca', 'preallocated'):
            with self.subTest(attribute=attribute):
                self.reject('declare ptr @factory()\n' + function('entry',
                    '  %f = call ptr @factory()\n'
                    f'  call void %f(ptr {attribute}({{i16,i16}}) %out)', 'ptr %out'))

    def test_interprocedural_registration_cannot_hide_callback(self):
        source = PAIR + 'declare void @native(ptr)\n'
        source += function('register', '  call void @native(ptr %f)', 'ptr %f', 'internal ')
        source += function('entry', '  call void @register(ptr @make)')
        self.reject(source)

    def test_direct_native_gate_also_checks_callbacks(self):
        source = PAIR + 'declare void @native(ptr)\n' + function('entry', '  call void @native(ptr @make)')
        with self.assertRaisesRegex(adapter.AuditError, 'native C aggregate callback ABI'):
            adapter.audit_native_aggregate_abi(source, ['entry'])

    def test_escaped_scalar_callback_has_native_parameter_provenance(self):
        prefix = PAIR + function('invoke',
            '  call void %f(ptr sret(%Pair) %out, i16 7)', 'ptr %f, ptr %out', 'internal ')
        setup = '  %out = alloca %Pair\n  call void @invoke(ptr @make, ptr %out)\n'
        cases = [
            'declare void @native(ptr)\n' + function('entry', setup + '  call void @native(ptr @invoke)'),
            '@hook = global ptr @invoke\n' + function('entry', setup),
            'define ptr @entry() {\n' + setup + '  ret ptr @invoke\n}\n',
            function('entry', setup + '  store ptr @invoke, ptr %slot', 'ptr %slot'),
        ]
        for source in cases:
            with self.subTest(source=source):
                self.reject(prefix + source)

    def test_escaped_scalar_factory_cannot_return_aggregate_callback(self):
        for depth in (1, 5):
            source, target = PAIR, 'make'
            for index in range(depth):
                factory = 'factory' + str(index)
                source += f'define internal ptr @{factory}() {{\n  ret ptr @{target}\n}}\n'
                target = factory
            source += 'declare void @native(ptr)\n' + function('entry', f'  call void @native(ptr @{target})')
            with self.subTest(depth=depth):
                self.reject(source)

    def test_quoted_aggregate_type_cannot_hide_function_header(self):
        source = '''%"Pair@tag" = type {i16, i16}
define internal %"Pair@tag" @make() {
  ret %"Pair@tag" zeroinitializer
}
declare void @native(ptr)
''' + function('entry', '  call void @native(ptr @make)')
        self.reject(source)
        with self.assertRaisesRegex(adapter.AuditError, 'native C aggregate ABI'):
            adapter.audit_native_aggregate_abi('%"Pair@tag" = type {i16,i16}\ndeclare %"Pair@tag" @native()\n')


if __name__ == '__main__':
    unittest.main()
