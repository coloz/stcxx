#!/usr/bin/env python3
"""Compare 16/32/64-bit target division against Python integer references."""
import argparse
import json
from pathlib import Path
import subprocess
import importlib.util
import tempfile


def cases(bits):
    limit = 1 << (bits - 1)
    numerators = [-limit, -limit+1, -257, -255, -1, 0, 1, 255, 257, limit-1]
    denominators = [-limit, -257, -4, -1, 1, 2, 255, limit-1]
    for x in numerators:
        for y in denominators:
            if x == -limit and y == -1:  # Undefined in C and LLVM.
                continue
            q = (abs(x) // abs(y)) * (-1 if (x < 0) != (y < 0) else 1)
            yield x, y, q, x-q*y


def source():
    parts = ['__sfr __at (0x99) SBUF;']
    for bits, ctype, suffix in ((16, 'int', 'U'), (32, 'long', 'UL'),
                                 (64, 'long long', 'ULL')):
        def literal(value):
            return f'({ctype})0x{value & ((1 << bits)-1):x}{suffix}'
        table = ',\n'.join('{' + ','.join(map(literal, row)) + '}' for row in cases(bits))
        parts.append(f'''
struct row{bits} {{ {ctype} x, y, q, r; }};
static const struct row{bits} rows{bits}[] = {{ {table} }};
static volatile {ctype} x{bits}, y{bits};
static unsigned char check{bits}(void) {{
  unsigned char i;
  for (i=0; i<sizeof(rows{bits})/sizeof(rows{bits}[0]); ++i) {{
    x{bits}=rows{bits}[i].x; y{bits}=rows{bits}[i].y;
    if (x{bits}/y{bits} != rows{bits}[i].q || x{bits}%y{bits} != rows{bits}[i].r)
      return 0;
  }}
  return 1;
}}
''')
    parts.append('''
void main(void) {
  if (check16() && check32() && check64()) {
    SBUF='P'; SBUF='A'; SBUF='S'; SBUF='S';
  } else {
    SBUF='F'; SBUF='A'; SBUF='I'; SBUF='L';
  }
  SBUF='\n';
  for (;;) {}
}
'''.replace("SBUF='\n'", "SBUF='\\n'"))
    return '\n'.join(parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdcc', type=Path, required=True)
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('check_qemu', Path(__file__).with_name('check-qemu.py'))
    qemu_check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qemu_check)
    machine = qemu_check.resolve_machine(args.qemu, None)
    with tempfile.TemporaryDirectory(prefix='signed-divmod-') as temporary:
        work = args.work_dir or Path(temporary)
        work.mkdir(parents=True, exist_ok=True)
        fixture = work/'signed-divmod.c'
        fixture.write_text(source())
        reports = []
        for model in ('small', 'large'):
            for stack_auto in (False, True):
                name = model + ('-stack-auto' if stack_auto else '')
                image = work/(name+'.hex')
                command = [str(args.sdcc.resolve()), '-mmcs251', '--model-'+model,
                           '--std-sdcc11', '--no-xinit-opt', '--code-loc', '0xff0000',
                           str(fixture), '-o', str(image)]
                if stack_auto:
                    command.append('--stack-auto')
                result = subprocess.run(command, capture_output=True, text=True)
                (work/(name+'.build.log')).write_text(result.stdout+result.stderr)
                if result.returncode:
                    raise AssertionError(f'{name}: {result.stdout}{result.stderr}')
                try:
                    output = qemu_check.run_qemu(args.qemu, machine, image)
                    status = 'PASS'
                except SystemExit as error:
                    output = str(error).encode()
                    status = 'FAIL'
                (work/(name+'.runtime.log')).write_bytes(output)
                reports.append({'model': name, 'status': status, 'vectors': 237,
                                'quotient_and_remainder_comparisons': 474})
        passed = all(row['status'] == 'PASS' for row in reports)
        report = {'status': 'PASS' if passed else 'FAIL', 'models': reports, 'comparisons': 1896}
        (work/'results.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report))
        if not passed:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
