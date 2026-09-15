#!/usr/bin/env python3
"""Run MCS51 C/C++ pointer ABI fixtures through SDCC and STC8G1K08A QEMU.

The member-pointer cases vary the preceding CSEG size by one byte, so both
linker-base parities must produce verified even final method addresses.
This does not qualify Arduino MCS51 support, physical hardware or all C++ ABI.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend', type=Path, required=True)
    parser.add_argument('--sdcc-root', type=Path, required=True)
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='new output directory')
    args = parser.parse_args(argv)
    bridge = Path(__file__).resolve().parents[1] / 'bridge'
    fixtures = bridge / 'fixtures/mcs51-pointers'
    frontend, native, qemu, output = (p.resolve() for p in
                                     (args.frontend, args.sdcc_root, args.qemu, args.output))
    source_root = bridge.parent
    for source in (source_root, frontend, native, qemu.parent):
        if output == source or source in output.parents or output in source.parents:
            parser.error('output must be separate from sources and tool directories')
    output.mkdir(parents=True, exist_ok=False)
    report = {'status': 'RUNNING', 'production_qualified': False,
              'scope': __doc__, 'started_utc': datetime.now(timezone.utc).isoformat(),
              'results': []}

    def save():
        temporary = output / 'report.json.part'
        temporary.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        temporary.replace(output / 'report.json')

    env = os.environ.copy()
    for key in ('LD_LIBRARY_PATH', 'LD_PRELOAD', 'CPATH', 'C_INCLUDE_PATH',
                'CPLUS_INCLUDE_PATH', 'SDCC_HOME', 'SDCC_INCLUDE', 'SDCC_LIB'):
        env.pop(key, None)
    env.update(LC_ALL='C', TZ='UTC')

    def run(case, row, name, command, allowed=(0,)):
        command = [str(x) for x in command]
        log = case / (name + '.log')
        with log.open('xb') as stream:
            result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT,
                                    env=env, timeout=120)
        row['commands'].append({'name': name, 'command': command,
                                'exit_code': result.returncode, 'log_sha256': digest(log)})
        require(result.returncode in allowed, f'{name} failed; see {log}')
        return result.returncode

    save()
    try:
        lock_path = source_root / 'toolchain-lock.json'
        lock = json.loads(lock_path.read_text(encoding='utf-8'))
        profile = lock['profiles']['mcs51']
        abi = profile['abi_identity_symbol']
        roots = sorted(['setup', 'loop', '__stcxx_run_global_ctors', abi, 'stcxx_runtime_panic'])
        roots_path = output / 'roots.txt'
        roots_path.write_text('\n'.join(roots) + '\n', encoding='ascii')
        files = [Path(__file__).resolve(), lock_path, qemu, Path(sys.executable).resolve(), roots_path,
                 *(bridge / name for name in ('adapt.py', 'audit_and_adapt.py',
                                               'native-storage.py', 'align-member-functions.py')),
                 *sorted(fixtures.glob('*'))]
        for root in (frontend, native):
            require(root.is_dir(), f'tool directory missing: {root}')
            files.extend(sorted(p for p in root.rglob('*') if p.is_file()))
        identities = {str(path): digest(path) for path in files}
        report.update(inputs_sha256=identities, python_version=sys.version, target=profile)
        require((native / 'lib/large-stack-auto/libsdcc.lib').is_file(), 'MCS51 large-stack-auto runtime missing')
        save()
        sdcc = native / 'bin/sdcc'
        assembler = native / 'bin/sdas8051'
        aligner = bridge / 'align-member-functions.py'
        flags = [sdcc, '-mmcs51', '--model-large', '--stack-auto', '--std-sdcc11',
                 '--opt-code-size', '--no-xinit-opt', '-I' + str(native / 'include'),
                 '-I' + str(native / 'include/mcs51')]
        for kind in ('scalar', 'members'):
            for level in ('0', 'z'):
                for padding in ((0,) if kind == 'scalar' else (0, 1)):
                    case = output / f'{kind}-O{level}-pad{padding}'
                    case.mkdir()
                    row = {'status': 'RUNNING', 'kind': kind, 'optimization': level,
                           'preceding_code_bytes': padding, 'checks': 8, 'commands': []}
                    report['results'].append(row)
                    save()
                    run(case, row, 'clang', [frontend / 'bin/clang', '--target=' + profile['target_triple'],
                        '-std=gnu++11', '-O' + level, '-ffreestanding', '-funsigned-char', '-fno-exceptions',
                        '-fno-rtti', '-fno-threadsafe-statics', '-fno-use-cxa-atexit', '-fno-c++-static-destructors',
                        '-fno-unwind-tables', '-fno-asynchronous-unwind-tables', '-fno-vectorize', '-fno-slp-vectorize',
                        '-Xclang', '-mno-constructor-aliases', '-Xclang', '-disable-O0-optnone',
                        '-DABI_SYMBOL=' + abi, '-emit-llvm', '-c', fixtures / (kind + '.cpp'), '-o', case / 'probe.bc'])
                    run(case, row, 'opt', [frontend / 'bin/opt', '-passes=internalize,deadargelim,globaldce',
                        '-internalize-public-api-list=' + ','.join(roots), case / 'probe.bc', '-o', case / 'optimized.bc'])
                    run(case, row, 'dis', [frontend / 'bin/llvm-dis', case / 'optimized.bc', '-o', case / 'optimized.ll'])
                    run(case, row, 'cbe', [frontend / 'bin/llvm-cbe', case / 'optimized.bc', '-o', case / 'raw.c'])
                    run(case, row, 'adapter', [sys.executable, bridge / 'adapt.py', '--ir', case / 'optimized.ll',
                        '--raw-c', case / 'raw.c', '--c-abi-preserve', roots_path, '--output-c', case / 'adapted.c',
                        '--audit-json', case / 'audit.json', '--target-profile', 'mcs51',
                        '--expected-triple', profile['target_triple'], '--expected-layout', profile['data_layout'],
                        '--abi-identity-symbol', abi])
                    run(case, row, 'compile-bridge', [*flags, '-S', case / 'adapted.c', '-o', case / 'raw.asm'])
                    run(case, row, 'compile-native', [*flags, '-DPROBE_NAME="' + kind + '"',
                        '-c', fixtures / 'native.c', '-o', case / 'native.rel'])
                    # This object shifts the bridge's CSEG contribution without changing
                    # vectors, startup code, hardware capacity or executed instructions.
                    padding_source = case / 'padding.asm'
                    padding_source.write_text(f'.module padding\n.area CSEG (CODE)\n.ds {padding}\n', encoding='ascii')
                    row['padding_source_sha256'] = digest(padding_source)
                    run(case, row, 'assemble-padding', [assembler, '-plosgffw', case / 'padding.rel', padding_source])
                    for parity in ('even', 'odd'):
                        run(case, row, 'align-' + parity, [sys.executable, aligner, 'align',
                            '--input-assembly', case / 'raw.asm', '--output-assembly', case / 'adapted.asm',
                            '--adapter-audit', case / 'audit.json', '--target-profile', 'mcs51',
                            '--local-parity', parity, '--audit-json', case / 'alignment.json'])
                        run(case, row, 'assemble-' + parity, [assembler, '-plosgffw', case / 'adapted.rel', case / 'adapted.asm'])
                        run(case, row, 'link-' + parity, [*flags, '--iram-size', '256', '--xram-loc', '0x100',
                            '--xram-size', '0x300', '--code-size', '0x2000', '-L' + str(native / 'lib/large-stack-auto'),
                            case / 'native.rel', case / 'padding.rel', case / 'adapted.rel', '-o', case / ('probe-' + parity + '.hex')])
                        status = run(case, row, 'verify-' + parity, [sys.executable, aligner, 'verify',
                            '--audit-json', case / 'alignment.json', '--relocated-listing', case / 'adapted.rst',
                            '--target-profile', 'mcs51'], allowed=(0, 3) if parity == 'even' else (0,))
                        if status == 0:
                            firmware = case / ('probe-' + parity + '.hex')
                            break
                        prior = case / 'prior-even'
                        prior.mkdir()
                        for filename in ('alignment.json', 'adapted.asm', 'adapted.rel', 'adapted.lst', 'adapted.rst'):
                            shutil.copyfile(case / filename, prior / filename)
                    alignment = json.loads((case / 'alignment.json').read_text(encoding='utf-8'))
                    require(alignment['phase'] == 'verified' and alignment['outcome'] == 'pass', 'final alignment not verified')
                    row['alignment'] = alignment
                    command = [str(qemu), '-M', 'stc8g1k08a', '-bios', str(firmware), '-accel', 'tcg',
                               '-icount', 'shift=0,align=off,sleep=off', '-display', 'none', '-monitor', 'none', '-serial', 'stdio']
                    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
                    try:
                        stdout, stderr = process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        try:
                            stdout, stderr = process.communicate(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            stdout, stderr = process.communicate()
                    (case / 'uart.log').write_bytes(stdout)
                    (case / 'qemu.log').write_bytes(stderr)
                    row.update(qemu_command=command, uart=stdout.decode(errors='replace'),
                               firmware_sha256=digest(firmware), uart_sha256=digest(case / 'uart.log'))
                    require(stdout == f'PASS mcs51 {kind} pointers\n'.encode('ascii'), f'target UART mismatch: {row["uart"]!r}')
                    row['artifacts_sha256'] = {p.name: digest(p) for p in sorted(case.iterdir()) if p.is_file()}
                    row['status'] = 'PASS'
                    save()
                    print('PASS', case.name, flush=True)
        require(len(report['results']) == 6, 'incomplete pointer regression matrix')
        for level in ('0', 'z'):
            cases = [r for r in report['results'] if r['kind'] == 'members' and r['optimization'] == level]
            require({r['alignment']['assembly']['local_parity'] for r in cases} == {'even', 'odd'},
                    'member cases did not exercise both linker-base parities')
        report['inputs_unchanged'] = all(digest(Path(path)) == value for path, value in identities.items())
        require(report['inputs_unchanged'], 'source/tool inputs changed during regression')
        report['status'] = 'PASS'
    except (Exception, KeyboardInterrupt) as error:
        report.update(status='FAIL', error=str(error) or type(error).__name__)
        for row in report['results']:
            if row['status'] == 'RUNNING':
                row.update(status='FAIL', error=report['error'])
        print(report['error'], file=sys.stderr)
    finally:
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        save()
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
