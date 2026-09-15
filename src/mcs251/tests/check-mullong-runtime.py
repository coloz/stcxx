#!/usr/bin/env python3
"""Verify low-32-bit multiplication in every MCS251 memory/stack model.

Build the supplied runtime source explicitly, so this checks that source
even before a new compiler distribution has been assembled.  A baseline
source additionally enables code-size and unchanged MCS51 object checks.
No installed compiler/runtime files are modified.
"""
import argparse
from contextlib import redirect_stderr
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import random
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
    return {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo')}


def vectors():
    edges = [0, 1, 2, 3, 0x7f, 0x80, 0xff, 0x100, 0x101, 0x7fff, 0x8000,
             0xffff, 0x10000, 0x10001, 0x7fffffff, 0x80000000, 0x80000001,
             0xfffffffe, 0xffffffff]
    pairs = [(a, b) for a in edges for b in edges]
    rng = random.Random(25132)
    pairs.extend((rng.getrandbits(32), rng.getrandbits(32)) for _ in range(256))
    return [(a, b, a*b & 0xffffffff, (a*b*a+a) & 0xffffffff) for a, b in pairs]


def fixture_source(rows):
    table = ',\n'.join('{' + ','.join(f'0x{v:08x}UL' for v in row) + '}' for row in rows)
    return '''__sfr __at (0x99) SBUF;
struct row { unsigned long a, b, product, nested; };
static __code const struct row rows[] = {
''' + table + '''
};
static volatile unsigned long a, b;
static unsigned char check(void) {
  unsigned int i;
  for (i=0; i<sizeof(rows)/sizeof(rows[0]); ++i) {
    a=rows[i].a; b=rows[i].b;
    if (a*b != rows[i].product || (a*b)*a+a != rows[i].nested)
      return 0;
  }
  return 1;
}
void main(void) {
  if (check()) { SBUF='P'; SBUF='A'; SBUF='S'; SBUF='S'; }
  else { SBUF='F'; SBUF='A'; SBUF='I'; SBUF='L'; }
  SBUF='\\n';
  for (;;) {}
}
'''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--toolchain', type=Path, required=True,
                        help='complete installed SDCC root with bin/include/lib')
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--source', type=Path,
                        default=Path(__file__).resolve().parents[3]/'device/lib/_mullong.c')
    parser.add_argument('--baseline-source', type=Path)
    parser.add_argument('--output', type=Path, required=True, help='new, nonexisting evidence directory')
    args = parser.parse_args(argv)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {'status': 'RUNNING', 'production_qualified': False,
              'scope': __doc__, 'started_utc': datetime.now(timezone.utc).isoformat(), 'results': []}

    def save():
        (out/'report.json.part').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
        (out/'report.json.part').replace(out/'report.json')

    def run(case, label, command, row):
        log = case/(label+'.log')
        record = {'name': label, 'command': list(map(str, command)), 'log': str(log)}
        row['commands'].append(record)
        try:
            with log.open('wb') as stream:
                cp = subprocess.run(record['command'], cwd=case, stdout=stream,
                                    stderr=subprocess.STDOUT, timeout=120)
            record['exit_code'] = cp.returncode
            require(cp.returncode == 0, label+': '+log.read_text(errors='replace')[-3000:])
        except subprocess.TimeoutExpired:
            record['error'] = 'timeout after 120 seconds'
            raise
        finally:
            record['sha256'] = sha(log)

    save()
    originals = {}
    toolchain = args.toolchain.resolve()
    before = None
    try:
        require(sys.platform.startswith('linux'), 'run this target verifier on Linux or WSL')
        sdcc = toolchain/'bin/sdcc'
        qemu = args.qemu.resolve()
        helper = Path(__file__).resolve().with_name('check-qemu.py')
        trace = helper.with_name('qemu_trace.py')
        for p in (sdcc, qemu, helper, trace, args.source.resolve()):
            require(p.is_file(), f'missing input: {p}')
        for name in ('include', 'lib'):
            require((toolchain/name).is_dir(), f'incomplete toolchain: {name}')
        require(sdcc.resolve().is_relative_to(toolchain), 'compiler escapes the supplied toolchain')
        before = inventory(toolchain)
        report['toolchain'] = str(toolchain)
        report['toolchain_sha256'] = before
        sources = [('candidate', args.source.resolve())]
        if args.baseline_source:
            sources.insert(0, ('baseline', args.baseline_source.resolve()))
        input_paths = [Path(__file__).resolve(), qemu, helper, trace, *[p for _, p in sources]]
        originals = {str(p): sha(p) for p in input_paths}
        report['input_files_sha256'] = originals
        spec = importlib.util.spec_from_file_location('mullong_qemu', helper)
        qemu_check = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(qemu_check)
        machine = qemu_check.resolve_machine(qemu, None)
        report['qemu_machine'] = machine
        rows = vectors()
        fixture = out/'multiply.c'
        fixture.write_text(fixture_source(rows), encoding='utf-8')
        vector_path = out/'vectors.json'
        vector_path.write_text(json.dumps(rows)+'\n', encoding='utf-8')
        report.update(vectors_per_firmware=len(rows), comparisons_per_firmware=2*len(rows),
                      fixture_sha256=sha(fixture), vectors_sha256=sha(vector_path))
        originals.update({str(fixture): sha(fixture), str(vector_path): sha(vector_path)})
        save()
        archs = ('mcs251', 'mcs51') if args.baseline_source else ('mcs251',)
        for arch in archs:
            for model in ('small', 'large'):
                for stack_auto in (False, True):
                    previous = None
                    for version, source in sources:
                        name = f'{arch}-{model}' + ('-stack-auto' if stack_auto else '') + '-'+version
                        case = out/name
                        case.mkdir()
                        row = {'name': name, 'arch': arch, 'model': model, 'stack_auto': stack_auto,
                               'version': version, 'status': 'RUNNING', 'commands': []}
                        try:
                            local_source = case/'_mullong.c'
                            shutil.copyfile(source, local_source)
                            require(sha(local_source) == originals[str(source)], 'source copy changed')
                            flags = ['-m'+arch, '--model-'+model, '--std-sdcc11', '--no-xinit-opt']
                            if stack_auto:
                                flags.append('--stack-auto')
                            obj = case/'_mullong.rel'
                            run(case, 'runtime', [sdcc, *flags, '-c', local_source, '-o', obj], row)
                            rel = obj.read_text()
                            assembly = obj.with_suffix('.asm')
                            areas = re.findall(r'^A CSEG size ([0-9A-F]+)', rel, re.M)
                            require(len(areas) == 1, 'expected one CSEG area')
                            size = int(areas[0], 16)
                            word_mul = len(re.findall(r'^\s*mul\s+wr', assembly.read_text(), re.M))
                            row.update(runtime_cseg_bytes=size, native_word_multiply_count=word_mul,
                                       runtime_sha256=sha(obj), assembly_sha256=sha(assembly))
                            require(not re.search(r'^S __mullong Ref', rel, re.M), 'recursive multiply dependency')
                            if version == 'candidate' and arch == 'mcs251':
                                require(word_mul == (3 if stack_auto or model == 'large' else 0),
                                        'wrong native multiply path for memory/stack profile')
                            if previous and version == 'candidate':
                                require(previous['status'] == 'PASS', 'baseline did not pass')
                                require(size <= previous['runtime_cseg_bytes'], 'runtime code size regressed')
                                row['cseg_bytes_saved'] = previous['runtime_cseg_bytes'] - size
                                if arch == 'mcs51' or (model == 'small' and not stack_auto):
                                    require(sha(obj) == previous['runtime_sha256'], 'unchanged profile REL differs')
                                    row['baseline_rel_identical'] = True
                            if arch == 'mcs251':
                                image = case/'multiply.hex'
                                run(case, 'link', [sdcc, *flags, '--code-loc', '0xff0000', fixture, obj, '-o', image], row)
                                # The volatile operands must exercise the runtime function.
                                require(re.search(r'^\s*ecall\s+__mullong\s*$', image.with_suffix('.asm').read_text(), re.M),
                                        'fixture did not call multiplication runtime')
                                diagnostics = case/'qemu-diagnostics.log'
                                row['qemu_inputs'] = {'helper': str(helper), 'qemu': str(qemu),
                                                      'machine': machine, 'firmware': str(image)}
                                try:
                                    # The shared helper prints the captured UART before
                                    # raising on FAIL/timeout. Retain those bytes as well.
                                    with diagnostics.open('w', encoding='utf-8') as stream, redirect_stderr(stream):
                                        uart = qemu_check.run_qemu(qemu, machine, image)
                                finally:
                                    row['qemu_diagnostics_sha256'] = sha(diagnostics)
                                (case/'uart.log').write_bytes(uart)
                                require(uart == b'PASS\n', 'unexpected QEMU UART bytes')
                                row.update(firmware=str(image), firmware_sha256=sha(image),
                                           uart_sha256=sha(case/'uart.log'), qemu_runtime='PASS')
                            else:
                                row['qemu_runtime'] = 'NOT_RUN_OBJECT_IDENTITY_ONLY'
                            row['status'] = 'PASS'
                        except (Exception, SystemExit) as error:
                            row.update(status='FAIL', error=str(error))
                        report['results'].append(row)
                        previous = row
                        save()
                        print(row['status'], name, row.get('runtime_cseg_bytes'), flush=True)
        report['status'] = 'PASS' if all(r['status'] == 'PASS' for r in report['results']) else 'FAIL'
    except (Exception, SystemExit) as error:
        report.update(status='FAIL', error=str(error))
    finally:
        try:
            report['inputs_unchanged'] = all(sha(Path(p)) == digest for p, digest in originals.items())
            report['toolchain_unchanged'] = before is not None and inventory(toolchain) == before
            require(report['inputs_unchanged'] and report['toolchain_unchanged'], 'inputs changed during verification')
        except Exception as error:
            report.update(status='FAIL', input_error=str(error))
        save()
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
