#!/usr/bin/env python3
"""Verify MCS251 heap initialization/allocator archive separation from source.

Uses an isolated source-built archive before the installed runtime libraries.
The installed package stays unchanged; this does not qualify a rebuilt package.
With --baseline-source, also run the original implementation and require
byte-identical MCS51 malloc objects in all four memory/stack configurations.
"""
import argparse
from contextlib import redirect_stderr
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(root):
    return {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob('*')) if p.is_file()}


def fixture(body):
    return '''#include <stdlib.h>
#include <stdint.h>
__sfr __at (0x99) SBUF;
__xdata unsigned char __sdcc_heap[128];
const size_t __sdcc_heap_size32 = sizeof(__sdcc_heap);
typedef struct header __xdata header_t;
struct header { header_t *next; header_t *next_free; };
extern header_t * __xdata __sdcc_heap_free;
extern unsigned char __sdcc_heap_initialized;
extern void __sdcc_heap_init(void);
static unsigned char check(void) {
''' + body + '''
}
void main(void) {
  const char *p = check() ? "PASS\\n" : "FAIL\\n";
  while (*p) SBUF = *p++;
  for (;;) {}
}
'''


INIT_ONLY = fixture('''
  if (__sdcc_heap_initialized || __sdcc_heap_free) return 0;
  __sdcc_heap_init();
  if (!__sdcc_heap_initialized || __sdcc_heap_free != (header_t *)__sdcc_heap) return 0;
  if (__sdcc_heap_free->next != (header_t *)(__sdcc_heap+127) || __sdcc_heap_free->next_free) return 0;
  __sdcc_heap_free = 0;
  __sdcc_heap_init();
  return __sdcc_heap_initialized && __sdcc_heap_free == (header_t *)__sdcc_heap
      && __sdcc_heap_free->next == (header_t *)(__sdcc_heap+127) && !__sdcc_heap_free->next_free;
''')

ALLOCATOR = fixture('''
  unsigned char __xdata *a, *b, *grown;
  unsigned int i;
  volatile size_t huge = SIZE_MAX;
  free(0);
  a = malloc(0); b = calloc(4, 3);
  if (!a || !b || a == b || !__sdcc_heap_initialized) return 0;
  for (i=0; i<12; ++i) { if (b[i]) return 0; b[i] = (unsigned char)(i ^ 0xa5); }
  if (malloc(huge) || calloc(huge, 2)) return 0;
  grown = realloc(b, 40);
  if (!grown) return 0;
  b = grown;
  for (i=0; i<12; ++i) if (b[i] != (unsigned char)(i ^ 0xa5)) return 0;
  if (realloc(b, huge)) return 0;
  for (i=0; i<12; ++i) if (b[i] != (unsigned char)(i ^ 0xa5)) return 0;
  free(a); free(b);
  a = realloc(0, 124); // All usable bytes, less the three-byte payload offset.
  if (!a || __sdcc_heap_free || malloc(1)) return 0;
  a[0]=0x13; a[123]=0x79;
  if (malloc(0) || a[0]!=0x13 || a[123]!=0x79) return 0;
  free(a);
  a=malloc(124);
  if (!a || __sdcc_heap_free || malloc(1)) return 0;
  free(a);
  return __sdcc_heap_free == (header_t *)__sdcc_heap;
''')


def main(argv=None):
    root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--toolchain', type=Path, required=True)
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, default=root / 'device/lib')
    parser.add_argument('--baseline-source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=False)
    report = {'status': 'RUNNING', 'production_qualified': False, 'scope': __doc__,
              'started_utc': datetime.now(timezone.utc).isoformat(), 'results': [], 'mcs51_comparisons': []}
    inputs, packages = {}, {}

    def save():
        pending = out / 'report.json.part'
        pending.write_text(json.dumps(report, indent=2) + '\n')
        pending.replace(out / 'report.json')

    def run(case, label, command, row):
        command = list(map(str, command)); log = case / (label + '.log')
        record = {'name': label, 'command': command}; row['commands'].append(record)
        try:
            with log.open('wb') as stream:
                result = subprocess.run(command, cwd=case, stdout=stream, stderr=subprocess.STDOUT,
                                        env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}, timeout=120)
            record['exit_code'] = result.returncode
            require(result.returncode == 0, label + ': ' + log.read_text(errors='replace')[-2200:])
        finally:
            record['log_sha256'] = sha(log)

    save()
    try:
        require(sys.platform.startswith('linux'), 'run on Linux/WSL')
        toolchain = args.toolchain.resolve(); qemu = args.qemu.resolve(); sources = args.source_root.resolve()
        sdcc, sdar = toolchain / 'bin/sdcc', toolchain / 'bin/sdar'
        require(sdcc.resolve().is_relative_to(toolchain), 'compiler escapes toolchain')
        packages[str(toolchain)] = inventory(toolchain)
        folder = Path(__file__).resolve().parent; helper = folder / 'check-qemu.py'
        paths = [Path(__file__).resolve(), Path(sys.executable).resolve(), qemu, helper, folder / 'qemu_trace.py',
                 folder / 'malloc-exhaustion-runtime.c', sources / 'malloc.c', sources / '_heap_init.c', sources / 'mcs251/heap.h']
        if args.baseline_source:
            args.baseline_source = args.baseline_source.resolve(); paths.append(args.baseline_source)
        inputs = {str(p): sha(p) for p in paths}
        spec = importlib.util.spec_from_file_location('heap_qemu', helper)
        execution = importlib.util.module_from_spec(spec); spec.loader.exec_module(execution)
        machine = execution.resolve_machine(qemu, None)
        report.update(input_files_sha256=inputs, package_inventories=packages, qemu_machine=machine)
        fixtures = {'init-only': INIT_ONLY, 'allocator': ALLOCATOR, 'exhaustion': (folder / 'malloc-exhaustion-runtime.c').read_text()}
        for name, content in fixtures.items():
            path = out / (name + '.c'); path.write_text(content); inputs[str(path)] = sha(path)
        versions = [('baseline', args.baseline_source)] if args.baseline_source else []
        versions.append(('candidate', sources / 'malloc.c'))
        for model in ('small', 'large'):
            for stack_auto in (False, True):
                profile = 'mcs251-' + model + ('-stack-auto' if stack_auto else '')
                for version, source in versions:
                    case = out / (profile + '-' + version); case.mkdir()
                    row = {'profile': profile, 'version': version, 'status': 'RUNNING', 'commands': [], 'cases': []}
                    try:
                        flags = ['-mmcs251', '--model-' + model, '--std-sdcc11', '--no-xinit-opt']
                        if stack_auto: flags.append('--stack-auto')
                        shutil.copyfile(source, case / 'malloc.c')
                        units = ['malloc']
                        if version == 'candidate':
                            shutil.copyfile(sources / '_heap_init.c', case / '_heap_init.c')
                            (case / 'mcs251').mkdir(); shutil.copyfile(sources / 'mcs251/heap.h', case / 'mcs251/heap.h')
                            units.append('_heap_init')
                        row['objects'] = {}
                        for unit in units:
                            obj = case / (unit + '.rel')
                            run(case, unit, [sdcc, *flags, '-c', case / (unit + '.c'), '-o', obj], row)
                            text = obj.read_text()
                            row['objects'][unit] = {'sha256': sha(obj), 'areas': {n: int(v, 16) for n, v in re.findall(r'^A (\S+) size ([0-9A-F]+)', text, re.M)}}
                        archive = case / 'heap-runtime.lib'
                        run(case, 'archive', [sdar, '-rcD', archive, *[case / (unit + '.rel') for unit in units]], row)
                        row['archive_sha256'] = sha(archive)
                        for name in fixtures:
                            test = {'name': name, 'status': 'RUNNING'}; row['cases'].append(test)
                            image = case / (name + '.hex')
                            run(case, name, [sdcc, *flags, '--code-loc', '0xff0000', out / (name + '.c'), archive, '-o', image], row)
                            mp = image.with_suffix('.map'); text = mp.read_text()
                            members = [(Path(p).resolve(), m.strip()) for p, m in re.findall(r'^(.+\.lib)\n\s*\[\s*([^\]\n]+?)\s*\]\s*$', text, re.M)]
                            expected = ['malloc.rel'] if version == 'baseline' else (['_heap_init.rel'] if name == 'init-only' else ['malloc.rel', '_heap_init.rel'])
                            selected = [(p, m) for p, m in members if m in ('malloc.rel', '_heap_init.rel')]
                            require(sorted(selected) == sorted((archive.resolve(), m) for m in expected), 'wrong allocator archive selection')
                            require(all(p == archive.resolve() or p.is_relative_to(toolchain / 'lib' / profile) for p, _ in members), 'library outside selected model')
                            test.update(firmware=str(image), firmware_sha256=sha(image), map_sha256=sha(mp),
                                        selected_heap_members=expected, code_bytes=sum(row['objects'][m.removesuffix('.rel')]['areas'].get('CSEG', 0) for m in expected))
                            diagnostics = case / (name + '-qemu.log')
                            try:
                                with diagnostics.open('w') as stream, redirect_stderr(stream):
                                    uart = execution.run_qemu(qemu, machine, image)
                            finally: test['qemu_diagnostics_sha256'] = sha(diagnostics)
                            uart_path = case / (name + '-uart.log'); uart_path.write_bytes(uart)
                            require(uart == b'PASS\n', 'unexpected target UART')
                            test.update(status='PASS', uart_sha256=sha(uart_path))
                        row['status'] = 'PASS'
                    except (Exception, SystemExit) as error:
                        row.update(status='FAIL', error=str(error))
                        for test in row['cases']:
                            if test['status'] == 'RUNNING': test.update(status='FAIL', error=str(error))
                    report['results'].append(row); save()
                    print(row['status'], profile, version, row.get('error', ''), flush=True)
                if args.baseline_source:
                    hashes = []
                    for version, source in versions:
                        case = out / (profile.replace('mcs251', 'mcs51') + '-' + version); case.mkdir()
                        row = {'profile': profile.replace('mcs251', 'mcs51'), 'version': version, 'commands': []}
                        shutil.copyfile(source, case / 'malloc.c')
                        flags = ['-mmcs51', '--model-' + model, '--std-sdcc11', '--no-xinit-opt']
                        if stack_auto: flags.append('--stack-auto')
                        obj = case / 'malloc.rel'; run(case, 'malloc', [sdcc, *flags, '-c', case / 'malloc.c', '-o', obj], row)
                        hashes.append(sha(obj)); row['sha256'] = sha(obj); report['mcs51_comparisons'].append(row)
                    require(hashes[0] == hashes[1], 'MCS51 malloc object changed')
        require(len(report['results']) == 4 * len(versions) and all(r['status'] == 'PASS' and len(r['cases']) == 3 for r in report['results']), 'incomplete source/runtime checks')
        report['status'] = 'PASS'
    except (Exception, SystemExit) as error: report.update(status='FAIL', error=str(error))
    finally:
        report['inputs_unchanged'] = bool(inputs) and all(sha(Path(p)) == d for p, d in inputs.items())
        report['packages_unchanged'] = bool(packages) and all(inventory(Path(p)) == value for p, value in packages.items())
        if not report['inputs_unchanged'] or not report['packages_unchanged']: report.update(status='FAIL', input_error='inputs changed')
        report['finished_utc'] = datetime.now(timezone.utc).isoformat(); save()
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
