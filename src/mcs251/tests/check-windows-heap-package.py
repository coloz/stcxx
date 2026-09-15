#!/usr/bin/env python3
"""Compile the installed heap fixtures on Windows, then execute them in WSL.

The two phases keep separate reports. PASS_COMPILE is never a runtime pass.
MCS51 checks cover six compile/link configurations only; QEMU executes the
sixteen MCS251 programs. No compiler source, object or library override is used.
"""
import argparse
from contextlib import redirect_stderr
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PureWindowsPath
import re
import subprocess
import sys

sys.dont_write_bytecode = True
FOLDER = Path(__file__).resolve().parent
PROFILES = ('mcs251-small', 'mcs251-small-stack-auto',
            'mcs251-large', 'mcs251-large-stack-auto')
NAMES = ('init-only', 'allocator', 'exhaustion', 'default-arena')
MCS51 = ('small', 'small-stack-auto', 'medium', 'large', 'large-stack-auto', 'huge')
FIXTURES = ('check-heap-split-runtime.py', 'check-heap-split-package.py',
            'malloc-exhaustion-runtime.c')
MCS51_SOURCE = '''#include <stdlib.h>
#if defined(__SDCC_MODEL_HUGE)
#include <mcs51/C8051F120.h> /* PSBANK: this is a compile-only banked fixture. */
#endif
__sfr __at (0x99) SBUF;
void main(void) {
    unsigned char ok = 0;
    unsigned char *p = malloc(12);
    if (p) { p[0] = 37; ok = p[0] == 37; free(p); }
    SBUF = ok ? 80 : 70;
    for (;;) {}
}
'''


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(root):
    result = {}
    for path in sorted(root.rglob('*')):
        require(not path.is_symlink() and path.resolve().is_relative_to(root),
                'package link escapes the recorded file inventory: ' + str(path))
        if path.is_file():
            result[path.relative_to(root).as_posix()] = sha(path)
    require(result, 'empty package inventory')
    return result


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_manifest(package, files):
    manifest = package / 'MANIFEST.sha256'
    expected = {}
    for line in manifest.read_text(encoding='utf-8').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        require(match is not None, 'invalid package manifest record')
        digest, name = match.groups()
        require(name not in expected, 'duplicate package manifest record: ' + name)
        expected[name] = digest
    require(expected == {n: d for n, d in files.items() if n != 'MANIFEST.sha256'},
            'package manifest does not match the complete installed payload')
    for profile in (*MCS51, *PROFILES):
        target = 'mcs251' if profile in PROFILES else 'mcs51'
        for library in ('libsdcc', target, 'libint', 'liblong', 'liblonglong', 'libfloat'):
            require(f'lib/{profile}/{library}.lib' in files,
                    'missing runtime archive: ' + profile + '/' + library)
    require((package / 'bin/sdcc.exe').read_bytes()[:2] == b'MZ', 'not a Windows compiler')


def map_members(image):
    return [(Path(p).resolve(), name.strip()) for p, name in re.findall(
        r'^(.+\.lib)\n\s*\[\s*([^\]\n]+?)\s*\]\s*$',
        image.with_suffix('.map').read_text(encoding='utf-8'), re.M)]


def verify_files(files, translate=lambda p: Path(p)):
    require(files, 'missing input identities')
    for path, digest in files.items():
        require(sha(translate(path)) == digest, 'input changed: ' + path)


def windows_path(path, drive_root):
    value = PureWindowsPath(path)
    require(value.is_absolute() and re.fullmatch(r'[A-Za-z]:', value.drive) and
            '..' not in value.parts, 'expected an absolute Windows drive path: ' + path)
    return drive_root.resolve() / value.drive[0].lower() / Path(*value.parts[1:])


def compile_phase(args, out, report, save):
    require(os.name == 'nt', 'compile must run under native Windows Python')
    package = args.toolchain.resolve()
    require(not out.is_relative_to(package), 'output must be outside the package')
    files = inventory(package)
    verify_manifest(package, files)
    inputs = {str(p): sha(p) for p in (Path(__file__).resolve(), Path(sys.executable).resolve(),
                                      *(FOLDER / n for n in FIXTURES))}
    report.update(package=str(package), package_inventory=files, inputs=inputs,
                  fixture_files={n: sha(FOLDER / n) for n in FIXTURES},
                  compiler_host=sys.platform, mcs51_compile_only=[])
    source = load_module('windows_heap_source', FOLDER / FIXTURES[0])
    default = load_module('windows_heap_default', FOLDER / FIXTURES[1])
    texts = {'init-only': source.INIT_ONLY, 'allocator': source.ALLOCATOR,
             'exhaustion': (FOLDER / FIXTURES[2]).read_text(encoding='utf-8'),
             'default-arena': default.DEFAULT_HEAP, 'mcs51-default-heap': MCS51_SOURCE}
    sources = out / 'source files'; sources.mkdir()
    for name, text in texts.items():
        path = sources / (name + '.c')
        path.write_text(text, encoding='utf-8', newline='\n')
        inputs[str(path)] = sha(path)
    system = Path(os.environ['SystemRoot'])
    env = {'SystemRoot': str(system), 'WINDIR': str(system),
           'PATH': str(package / 'bin') + os.pathsep + str(system / 'System32'),
           'TEMP': os.environ['TEMP'], 'TMP': os.environ.get('TMP', os.environ['TEMP'])}
    compiler = package / 'bin/sdcc.exe'
    save()
    for profile in (*PROFILES, *MCS51):
        target = 'mcs251' if profile in PROFILES else 'mcs51'
        model = profile.removeprefix('mcs251-').removesuffix('-stack-auto')
        flags = ['-m' + target, '--model-' + model, '--std-sdcc11', '--no-xinit-opt']
        if target == 'mcs251':
            flags += ['--code-loc', '0xff0000']
        if profile.endswith('-stack-auto'):
            flags.append('--stack-auto')
        case = out / 'build outputs' / profile; case.mkdir(parents=True)
        for name in NAMES if target == 'mcs251' else ('mcs51-default-heap',):
            row = {'profile': profile, 'name': name, 'status': 'RUNNING'}
            report['results' if target == 'mcs251' else 'mcs51_compile_only'].append(row)
            image = case / (name + '.hex')
            command = list(map(str, [compiler, *flags, sources / (name + '.c'), '-o', image]))
            row['command'] = command
            try:
                stdout, stderr = case / (name + '.stdout'), case / (name + '.stderr')
                with stdout.open('wb') as sout, stderr.open('wb') as serr:
                    result = subprocess.run(command, cwd=case, env=env, stdout=sout,
                                            stderr=serr, timeout=120)
                row.update(exit_code=result.returncode, stdout_sha256=sha(stdout), stderr_sha256=sha(stderr))
                require(result.returncode == 0, 'native Windows compile failed: ' +
                        stderr.read_text(encoding='utf-8', errors='replace')[-1800:])
                row.update(firmware=str(image), firmware_sha256=sha(image),
                           map_sha256=sha(image.with_suffix('.map')))
                members = map_members(image)
                root = package / 'lib' / profile
                require(members and all(p.is_relative_to(root) for p, _ in members),
                        'wrong installed runtime model selected')
                if target == 'mcs251':
                    wanted = ['_heap_init.rel'] if name == 'init-only' else ['malloc.rel', '_heap_init.rel']
                    selected = [(p, m) for p, m in members if m in ('malloc.rel', '_heap_init.rel')]
                    require(sorted(selected) == sorted((root / 'libsdcc.lib', m) for m in wanted),
                            'wrong installed allocator selection')
                    defaults = [(p, m) for p, m in members if m == '_heap.rel']
                    require(defaults == ([(root / 'libsdcc.lib', '_heap.rel')] if name == 'default-arena' else []),
                            'wrong default/custom heap provider')
                    row.update(selected_heap_members=wanted, default_heap_member_count=len(defaults))
                else:
                    require(any(m == 'malloc.rel' for _, m in members), 'MCS51 fixture did not select malloc')
                    row['scope'] = 'compile/link only; huge fixture defines C8051F120 PSBANK'
                row['status'] = 'PASS_COMPILE'
            except (Exception, SystemExit) as error:
                row.update(status='FAIL', error=str(error))
            save(); print(row['status'], profile, name, row.get('error', ''), flush=True)
    verify_compile_matrix(report)
    verify_files(inputs)
    require(inventory(package) == files, 'package changed during compilation')
    report.update(status='PASS_COMPILE', inputs_unchanged=True,
                  pending='Execute these exact MCS251 images; MCS51 remains compile/link only.')


def verify_compile_matrix(report):
    rows = report['results']
    require(len(rows) == 16 and {(r['profile'], r['name']) for r in rows} ==
            {(p, n) for p in PROFILES for n in NAMES}, 'incomplete or duplicate MCS251 matrix')
    rows51 = report['mcs51_compile_only']
    require(len(rows51) == 6 and {r['profile'] for r in rows51} == set(MCS51),
            'incomplete or duplicate MCS51 compile-only matrix')
    require(all(r['status'] == 'PASS_COMPILE' for r in (*rows, *rows51)), 'compilation matrix failed')


def execute_phase(args, out, report, save):
    require(sys.platform.startswith('linux'), 'execute must run in Linux/WSL with the Windows files mounted')
    compiled_path = args.compiled.resolve()
    inputs = {str(p): sha(p) for p in (Path(__file__).resolve(), Path(sys.executable).resolve(),
                                      compiled_path, args.qemu.resolve(), FOLDER / 'check-qemu.py', FOLDER / 'qemu_trace.py')}
    report['inputs'] = inputs
    compiled = json.loads(compiled_path.read_text(encoding='utf-8'))
    require(compiled['schema_version'] == 1 and compiled['phase'] == 'compile' and
            compiled['compiler_host'] == 'win32' and compiled['status'] == 'PASS_COMPILE' and
            compiled['inputs_unchanged'], 'native Windows compilation is incomplete')
    verify_compile_matrix(compiled)
    require(compiled['checker_sha256'] == sha(Path(__file__).resolve()), 'compile and execute checkers differ')
    require(compiled['fixture_files'] == {n: sha(FOLDER / n) for n in FIXTURES}, 'maintained fixtures changed')

    def local(path):
        return windows_path(path, args.drive_root)

    package = local(compiled['package']).resolve()
    require(not out.is_relative_to(package), 'output must be outside the package')
    require(inventory(package) == compiled['package_inventory'], 'Windows package changed')
    verify_manifest(package, compiled['package_inventory'])
    verify_files(compiled['inputs'], local)
    # Validate every image and map before starting QEMU, including compile-only MCS51.
    artifacts = {}
    for row in (*compiled['results'], *compiled['mcs51_compile_only']):
        image = local(row['firmware'])
        for path, digest in ((image, row['firmware_sha256']), (image.with_suffix('.map'), row['map_sha256'])):
            require(str(path) not in artifacts, 'duplicate target artifact')
            artifacts[str(path)] = digest
    verify_files(artifacts)
    report.update(compiled_report_sha256=sha(compiled_path), artifact_files_sha256=artifacts,
                  package=str(package), package_inventory=compiled['package_inventory'],
                  mcs51_scope='Six configurations compiled/linked; no MCS51 execution.')
    execution = load_module('windows_heap_qemu', FOLDER / 'check-qemu.py')
    machine = execution.resolve_machine(args.qemu.resolve(), None)
    report['qemu_machine'] = machine
    save()
    for source in compiled['results']:
        row = {'profile': source['profile'], 'name': source['name'], 'status': 'RUNNING'}
        report['results'].append(row)
        image = local(source['firmware']); case = out / source['profile']; case.mkdir(exist_ok=True)
        diagnostics = case / (source['name'] + '-qemu.log')
        row.update(firmware=str(image), firmware_sha256=sha(image), map_sha256=source['map_sha256'])
        try:
            try:
                with diagnostics.open('w', encoding='utf-8') as stream, redirect_stderr(stream):
                    uart = execution.run_qemu(args.qemu.resolve(), machine, image)
            finally:
                row['qemu_diagnostics_sha256'] = sha(diagnostics)
            log = case / (source['name'] + '-uart.log'); log.write_bytes(uart)
            row['uart_sha256'] = sha(log)
            require(uart == b'PASS\n', 'unexpected target UART')
            row['status'] = 'PASS'
        except (Exception, SystemExit) as error:
            row.update(status='FAIL', error=str(error))
        save(); print(row['status'], row['profile'], row['name'], row.get('error', ''), flush=True)
    require(len(report['results']) == 16 and all(r['status'] == 'PASS' for r in report['results']),
            'Windows firmware execution matrix failed')
    verify_files(inputs); verify_files(compiled['inputs'], local); verify_files(artifacts)
    require(inventory(package) == compiled['package_inventory'], 'package changed during execution')
    report.update(status='PASS', inputs_unchanged=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    phases = parser.add_subparsers(dest='phase', required=True)
    compile_parser = phases.add_parser('compile', help='native Windows: compile/link all ten model configurations')
    compile_parser.add_argument('--toolchain', type=Path, required=True)
    execute_parser = phases.add_parser('execute', help='WSL: check identities and execute exactly sixteen MCS251 images')
    execute_parser.add_argument('--compiled', type=Path, required=True, help='compile phase report.json')
    execute_parser.add_argument('--qemu', type=Path, required=True)
    execute_parser.add_argument('--drive-root', type=Path, default=Path('/mnt'),
                                help='WSL drive mounts parent, default /mnt (Windows D: maps to /mnt/d)')
    for phase in (compile_parser, execute_parser):
        phase.add_argument('--output', type=Path, required=True, help='new evidence directory; never reused')
    args = parser.parse_args(argv)
    out = args.output.resolve()
    # Reject a misplaced output before writing even the first report. Otherwise
    # the evidence directory itself would contaminate the input package.
    try:
        if args.phase == 'compile':
            input_package = args.toolchain.resolve()
        else:
            metadata = json.loads(args.compiled.read_text(encoding='utf-8'))
            input_package = windows_path(metadata['package'], args.drive_root).resolve()
    except (Exception, SystemExit) as error:
        parser.error('cannot establish input package location: ' + str(error))
    if out.is_relative_to(input_package):
        parser.error('output must be outside the package')
    out.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': 1, 'phase': args.phase, 'status': 'RUNNING',
              'production_qualified': False, 'scope': __doc__, 'results': [],
              'checker_sha256': sha(Path(__file__).resolve()),
              'started_utc': datetime.now(timezone.utc).isoformat()}

    def save():
        pending = out / 'report.json.part'
        pending.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        pending.replace(out / 'report.json')

    save()
    try:
        (compile_phase if args.phase == 'compile' else execute_phase)(args, out, report, save)
    except (Exception, SystemExit) as error:
        report.update(status='FAIL', error=str(error))
    finally:
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        save()
    print(report['status'], report.get('error', ''), flush=True)
    return 0 if report['status'] == ('PASS_COMPILE' if args.phase == 'compile' else 'PASS') else 1


if __name__ == '__main__':
    sys.exit(main())
