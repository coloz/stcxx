#!/usr/bin/env python3
"""Verify a relocated frontend package and its two STC IR profiles.

Run again in a clean host root without the original build or system LLVM.
This checks frontend execution and IR/C emission, not SDCC or target execution.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

spec = importlib.util.spec_from_file_location('frontend_package', Path(__file__).with_name('package-linux-frontend.py'))
package = importlib.util.module_from_spec(spec); spec.loader.exec_module(package)
PROFILES = {
    'mcs51': ('msp430-stc51-none-eabi', 'e-m:e-p:24:8-p1:16:8-P1-i8:8-i16:8-i32:8-i64:8-i128:8-f32:8-f64:8-f128:8-a:8-n8:16:32-S8'),
    'mcs251': ('msp430-stc-none-eabi', 'E-m:e-p:24:8-i8:8-i16:8-i32:8-i64:8-i128:8-f32:8-f64:8-f128:8-a:8-n8:16:32-S8'),
}
PROBE = r'''#include <stddef.h>
#include <stdint.h>
#ifndef __STC_CLANG_IR_ONLY__
#error missing STC IR-only profile
#endif
static_assert(sizeof(int) == 2 && sizeof(long) == 4 && sizeof(long long) == 8, "integers");
static_assert(sizeof(void*) == 3 && sizeof(ptrdiff_t) == 4, "data pointers");
static_assert(sizeof(float) == 4 && sizeof(double) == 4 && sizeof(long double) == 4, "floats");
static_assert(alignof(long long) == 1 && alignof(void*) == 1, "alignment");
struct Widget { int value; int apply(int x) { return value + x; } };
typedef unsigned int (*Callback)(unsigned int);
#ifdef __STC_MCS51__
static_assert(sizeof(size_t) == 2 && sizeof(Callback) == 2, "mcs51 pointers");
static_assert(sizeof(int Widget::*) == 2 && sizeof(int (Widget::*)(int)) == 4, "mcs51 members");
static_assert(__BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__, "mcs51 byte order");
#elif defined(__STC_MCS251__)
static_assert(sizeof(size_t) == 4 && sizeof(Callback) == 3, "mcs251 pointers");
static_assert(sizeof(int Widget::*) == 3 && sizeof(int (Widget::*)(int)) == 6, "mcs251 members");
static_assert(__BYTE_ORDER__ == __ORDER_BIG_ENDIAN__, "mcs251 byte order");
#else
#error missing STC target identity
#endif
struct Pair { unsigned char tag; unsigned long value; };
Pair make_pair(unsigned long x) { Pair p = {7, x}; return p; }
extern "C" Callback callback_identity(Callback value) { return value; }
extern "C" unsigned int invoke(Callback callback, unsigned int value) { return callback(value); }
int call_member(Widget* object, int (Widget::*member)(int), int value) { return (object->*member)(value); }
int (Widget::*selected_member)(int) = &Widget::apply;
'''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.package.resolve(); output = args.output
    if sys.platform != 'linux': parser.error('Linux required')
    if not output.is_absolute() or output.exists() or output.is_symlink(): parser.error('--output must be an absolute new directory')
    if root == output.resolve() or root in output.resolve().parents: parser.error('output must be outside the package')
    manifest = root / 'MANIFEST.sha256'; observed = package.inventory(root); expected = {}
    for line in manifest.read_text().splitlines():
        digest, name = line.split('  ', 1)
        if name in expected or name not in observed or observed[name]['sha256'] != digest: parser.error('manifest mismatch: ' + name)
        expected[name] = digest
    if set(expected) != set(observed) - {'MANIFEST.sha256'}: parser.error('manifest does not cover exactly the package')
    provenance = json.loads((root / 'candidate-provenance.json').read_text())
    if provenance['status'] != 'PASS': parser.error('package provenance did not pass')
    output.mkdir(); (output / 'logs').mkdir()
    report = {'status': 'RUNNING', 'production_qualified': False,
              'scope': 'package relocation and STC frontend IR/C emission; SDCC, firmware and hardware are separate',
              'package_root': str(root), 'manifest_sha256': package.sha256(manifest),
              'verifier_sha256': package.sha256(Path(__file__)), 'commands': [], 'profiles': {}}
    env = {k: v for k, v in os.environ.items() if k not in ('LD_LIBRARY_PATH', 'LD_PRELOAD', 'CPATH', 'CPLUS_INCLUDE_PATH', 'C_INCLUDE_PATH')}
    env.update(LC_ALL='C', TZ='UTC')

    def run(name, command, failure=None):
        result = subprocess.run([str(x) for x in command], env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, timeout=120)
        log = output / 'logs' / (name + '.log'); log.write_text(result.stdout)
        report['commands'].append({'name': name, 'command': [str(x) for x in command], 'exit_code': result.returncode,
                                   'log_sha256': package.sha256(log)})
        if failure is None:
            if result.returncode: raise RuntimeError(name + ': ' + result.stdout[-4000:])
        elif result.returncode == 0 or failure not in result.stdout:
            raise RuntimeError(name + ' did not reject with the expected diagnostic: ' + result.stdout[-4000:])
        return result.stdout

    try:
        for path in [*sorted((root / 'bin').iterdir()), *sorted(p for p in (root / 'lib').iterdir() if p.is_file())]:
            dependencies = package.dependencies(path, allow_leaf_library=path.parent == root / 'lib',
                runner=lambda command: run(path.name + '-' + str(command[0]) +
                    (str(command[1]) if str(command[1]).startswith('-') else ''), command))
            for name, dependency in dependencies.items():
                if name not in package.CORE_LIBRARIES and root not in dependency.resolve().parents:
                    raise RuntimeError('dependency escaped relocated package: ' + str(dependency))
        for name in ('clang', 'llvm-link', 'opt', 'llvm-dis', 'llvm-cbe'):
            run('version-' + name, [root / 'bin' / name, '--version'])
        resource = Path(run('resource', [root / 'bin/clang', '-print-resource-dir']).strip()).resolve()
        if root not in resource.parents or not (resource / 'include/stddef.h').is_file():
            raise RuntimeError('resource headers escaped relocated package')
        source = output / 'probe.cpp'; source.write_text(PROBE)
        second = output / 'second.cpp'; second.write_text('extern "C" unsigned int extra(unsigned int x) { return x + 3; }\n')
        for profile, (triple, layout) in PROFILES.items():
            rows = []
            for optimization in ('0', 'z'):
                work = output / (profile + '-O' + optimization); work.mkdir()
                clang = [root / 'bin/clang', '--target=' + triple, '-std=gnu++11', '-O' + optimization,
                         '-ffreestanding', '-funsigned-char', '-fno-exceptions', '-fno-rtti',
                         '-fno-vectorize', '-fno-slp-vectorize', '-nostdinc', '-isystem', resource / 'include',
                         '-Xclang', '-disable-O0-optnone']
                prefix = work.name + '-'
                for stem, path in [('probe', source), ('second', second)]:
                    run(prefix + stem, [*clang, '-emit-llvm', '-c', path, '-o', work / (stem + '.bc')])
                run(prefix + 'link', [root / 'bin/llvm-link', work / 'probe.bc', work / 'second.bc', '-o', work / 'linked.bc'])
                run(prefix + 'opt', [root / 'bin/opt', '-passes=mem2reg', work / 'linked.bc', '-o', work / 'optimized.bc'])
                run(prefix + 'dis', [root / 'bin/llvm-dis', work / 'optimized.bc', '-o', work / 'optimized.ll'])
                ir = (work / 'optimized.ll').read_text()
                if 'target triple = "' + triple + '"' not in ir or 'target datalayout = "' + layout + '"' not in ir:
                    raise RuntimeError('linked/optimized STC IR identity changed')
                if 'sret(' not in ir: raise RuntimeError('aggregate return no longer uses caller-owned storage')
                run(prefix + 'cbe', [root / 'bin/llvm-cbe', work / 'optimized.bc', '-o', work / 'module.c'])
                if 'callback_identity' not in (work / 'module.c').read_text(): raise RuntimeError('CBE output lacks the callback probe')
                rows.append({'optimization': optimization, 'ir_sha256': package.sha256(work / 'optimized.ll'),
                             'c_sha256': package.sha256(work / 'module.c')})
            run(profile + '-reject-object', [root / 'bin/clang', '--target=' + triple, '-c', second, '-o', output / (profile + '.o')],
                failure='assembly/object emission is unsupported')
            report['profiles'][profile] = rows
        stock = output / 'stock.cpp'
        stock.write_text('#ifdef __STC_CLANG_IR_ONLY__\n#error STC profile leaked\n#endif\nstatic_assert(sizeof(void*) == 2, "stock MSP430");\nint x;\n')
        run('stock-msp430', [root / 'bin/clang', '--target=msp430-none-none-elf', '-emit-llvm', '-c', stock, '-o', output / 'stock.bc'])
        if package.inventory(root) != observed: raise RuntimeError('package changed while executing qualification')
        report['status'] = 'PASS'
    except Exception as error:
        report.update(status='FAIL', error=str(error)); print(report['error'], file=sys.stderr)
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(report['status'], flush=True)
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__': sys.exit(main())
