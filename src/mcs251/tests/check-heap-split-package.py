#!/usr/bin/env python3
"""Execute the installed MCS251 heap runtime through normal package lookup.

No source/object/library override is used. The source regression provides the
same three custom-heap fixtures; a fourth verifies the package's default heap.
An optional r5 reference audits every libsdcc
member, permitting only malloc replacement and the new initialization member.
"""
import argparse
from contextlib import redirect_stderr
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys


def require(condition, message):
    if not condition: raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(root):
    return {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob('*')) if p.is_file()}


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


DEFAULT_HEAP = '''#include <stdlib.h>
__sfr __at (0x99) SBUF;
extern const size_t __sdcc_heap_size32;
extern unsigned char __sdcc_heap_initialized;
static unsigned char check(void) {
    unsigned char __xdata *p;
    unsigned int i;
    if (__sdcc_heap_size32 != 1024UL) return 0;
    p = malloc(1020);
    if (!p || !__sdcc_heap_initialized) return 0;
    p[0] = 0x13; p[1019] = 0x79;
    if (malloc(1) || p[0] != 0x13 || p[1019] != 0x79) return 0;
    free(p);
    p = calloc(255, 4);
    if (!p) return 0;
    for (i=0; i<1020; ++i) if (p[i]) return 0;
    free(p);
    if (malloc(1021)) return 0;
    p = malloc(1020);
    if (!p || malloc(1)) return 0;
    free(p);
    return 1;
}
void main(void) {
    const char *s = check() ? "PASS\\n" : "FAIL\\n";
    while (*s) SBUF = *s++;
    for (;;) {}
}
'''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--toolchain', type=Path, required=True)
    parser.add_argument('--reference-toolchain', type=Path)
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=False)
    report = {'status': 'RUNNING', 'production_qualified': False, 'scope': __doc__,
              'started_utc': datetime.now(timezone.utc).isoformat(), 'results': []}
    inputs, packages = {}, {}

    def save():
        pending = out / 'report.json.part'; pending.write_text(json.dumps(report, indent=2) + '\n'); pending.replace(out / 'report.json')

    def run(case, label, command, row):
        command = list(map(str, command)); record = {'name': label, 'command': command}; row['commands'].append(record)
        stdout, stderr = case / (label + '.stdout'), case / (label + '.stderr')
        try:
            with stdout.open('wb') as sout, stderr.open('wb') as serr:
                result = subprocess.run(command, cwd=case, stdout=sout, stderr=serr,
                                        env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}, timeout=120)
            record['exit_code'] = result.returncode
            require(result.returncode == 0, label + ': ' + stderr.read_text(errors='replace')[-2200:])
            return stdout.read_bytes()
        finally: record.update(stdout_sha256=sha(stdout), stderr_sha256=sha(stderr))

    def archive(package, profile, case, label, row):
        path = package / 'lib' / profile / 'libsdcc.lib'; sdar = package / 'bin/sdar'
        names = run(case, label + '-list', [sdar, '-t', path], row).decode().splitlines()
        require(names and len(names) == len(set(names)) and 'malloc.rel' in names, 'invalid archive inventory')
        require(all(re.fullmatch(r'[A-Za-z0-9_.-]+\.rel', n) for n in names), 'unsafe archive member')
        members = {}; areas = {}
        for name in names:
            content = run(case, label + '-' + name, [sdar, '-p', path, name], row)
            members[name] = hashlib.sha256(content).hexdigest()
            if name in ('malloc.rel', '_heap_init.rel'):
                areas[name] = {n: int(v, 16) for n, v in re.findall(r'^A (\S+) size ([0-9A-F]+)', content.decode(), re.M)}
        return {'path': str(path), 'sha256': sha(path), 'members': members, 'heap_areas': areas}

    save()
    try:
        require(sys.platform.startswith('linux'), 'run on Linux/WSL')
        current = args.toolchain.resolve(); reference = args.reference_toolchain.resolve() if args.reference_toolchain else None
        require(reference != current, 'reference must be separate')
        for package in (current, reference):
            if package is None: continue
            require(all((package / n).is_dir() for n in ('bin', 'include', 'lib')), 'incomplete package')
            require((package / 'bin/sdcc').resolve().is_relative_to(package), 'compiler escapes package')
            packages[str(package)] = inventory(package)
        qemu = args.qemu.resolve(); folder = Path(__file__).resolve().parent
        source = folder / 'check-heap-split-runtime.py'; helper = folder / 'check-qemu.py'; exhaustion = folder / 'malloc-exhaustion-runtime.c'
        inputs = {str(p): sha(p) for p in (Path(__file__).resolve(), Path(sys.executable).resolve(), source, helper, folder / 'qemu_trace.py', exhaustion, qemu)}
        fixtures = module('heap_source', source); execution = module('heap_qemu', helper); machine = execution.resolve_machine(qemu, None)
        texts = {'init-only': fixtures.INIT_ONLY, 'allocator': fixtures.ALLOCATOR,
                 'exhaustion': exhaustion.read_text(), 'default-arena': DEFAULT_HEAP}
        for name, text in texts.items():
            path = out / (name + '.c'); path.write_text(text); inputs[str(path)] = sha(path)
        report.update(input_files_sha256=inputs, package_inventories=packages, qemu_machine=machine)
        for model in ('small', 'large'):
            for stack_auto in (False, True):
                profile = 'mcs251-' + model + ('-stack-auto' if stack_auto else '')
                case = out / profile; case.mkdir()
                row = {'status': 'RUNNING', 'profile': profile, 'commands': [], 'cases': []}
                try:
                    candidate = archive(current, profile, case, 'candidate', row); row['archive'] = candidate
                    require('_heap_init.rel' in candidate['members'], 'package has no separate heap initialization member')
                    if reference:
                        baseline = archive(reference, profile, case, 'reference', row); row['reference_archive'] = baseline
                        require('_heap_init.rel' not in baseline['members'], 'reference is not the unsplit runtime')
                        require(set(candidate['members']) == set(baseline['members']) | {'_heap_init.rel'}, 'unexpected archive membership change')
                        changed = [n for n in baseline['members'] if baseline['members'][n] != candidate['members'][n]]
                        require(changed == ['malloc.rel'], 'unexpected changed runtime members: ' + str(changed)); row['changed_members'] = changed
                        old_size = baseline['heap_areas']['malloc.rel']['CSEG']
                        new_size = sum(v['CSEG'] for v in candidate['heap_areas'].values())
                        require(new_size == old_size, 'allocating runtime code size differs from reference')
                    flags = ['-mmcs251', '--model-' + model, '--std-sdcc11', '--no-xinit-opt', '--code-loc', '0xff0000']
                    if stack_auto: flags.append('--stack-auto')
                    for name in texts:
                        test = {'name': name, 'status': 'RUNNING'}; row['cases'].append(test); image = case / (name + '.hex')
                        run(case, name, [current / 'bin/sdcc', *flags, out / (name + '.c'), '-o', image], row)
                        mp = image.with_suffix('.map'); text = mp.read_text()
                        members = [(Path(p).resolve(), m.strip()) for p, m in re.findall(r'^(.+\.lib)\n\s*\[\s*([^\]\n]+?)\s*\]\s*$', text, re.M)]
                        expected = ['_heap_init.rel'] if name == 'init-only' else ['malloc.rel', '_heap_init.rel']
                        selected = [(p, m) for p, m in members if m in ('malloc.rel', '_heap_init.rel')]
                        required = [(current / 'lib' / profile / 'libsdcc.lib', m) for m in expected]
                        require(sorted(selected) == sorted(required), 'wrong installed allocator selection')
                        require(all(p.is_relative_to(current / 'lib' / profile) for p, _ in members), 'wrong model selected')
                        default_heap = [(p, m) for p, m in members if m == '_heap.rel']
                        expected_default = [(current / 'lib' / profile / 'libsdcc.lib', '_heap.rel')] if name == 'default-arena' else []
                        require(default_heap == expected_default, 'wrong default/custom heap provider')
                        test.update(firmware=str(image), firmware_sha256=sha(image), map_sha256=sha(mp), selected_heap_members=expected,
                                    default_heap_member_count=len(default_heap),
                                    code_bytes=sum(candidate['heap_areas'][m]['CSEG'] for m in expected))
                        diagnostics = case / (name + '-qemu.log')
                        try:
                            with diagnostics.open('w') as stream, redirect_stderr(stream): uart = execution.run_qemu(qemu, machine, image)
                        finally: test['qemu_diagnostics_sha256'] = sha(diagnostics)
                        uart_path = case / (name + '-uart.log'); uart_path.write_bytes(uart); require(uart == b'PASS\n', 'unexpected target UART')
                        test.update(status='PASS', uart_sha256=sha(uart_path))
                    row['status'] = 'PASS'
                except (Exception, SystemExit) as error:
                    row.update(status='FAIL', error=str(error))
                    for test in row['cases']:
                        if test['status'] == 'RUNNING': test.update(status='FAIL', error=str(error))
                report['results'].append(row); save(); print(row['status'], profile, row.get('error', ''), flush=True)
        require(len(report['results']) == 4 and all(r['status'] == 'PASS' and len(r['cases']) == len(texts) for r in report['results']), 'incomplete package execution')
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
