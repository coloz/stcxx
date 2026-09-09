#!/usr/bin/env python3
"""Verify that MCS251 object extents are not silently truncated at 64 KiB."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile


def run(command, success=True):
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    if (result.returncode == 0) != success:
        raise AssertionError(f"unexpected status {result.returncode}: {' '.join(command)}\n{result.stdout}")
    return result.stdout


def area_size(path, area):
    matches = re.findall(rf"^A {re.escape(area)} size ([0-9A-Fa-f]+) flags ",
                         path.read_text(), re.MULTILINE)
    if len(matches) != 1:
        raise AssertionError(f"expected one {area} area in {path}")
    return int(matches[0], 16)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdcc', required=True)
    parser.add_argument('--report')
    args = parser.parse_args()
    sdcc = str(Path(args.sdcc).resolve())
    source = Path(__file__).with_name('large-objects.c')
    checks = []
    with tempfile.TemporaryDirectory(prefix='mcs251-large-objects-') as temporary:
        work = Path(temporary)
        for model in ('small', 'large'):
            for stack_auto in (False, True):
                flags = ['-mmcs251', f'--model-{model}']
                if stack_auto:
                    flags.append('--stack-auto')
                for size in (65535, 65536, 65537, 131072):
                    name = f'{model}-{stack_auto}-{size}'
                    rel = work / f'{name}.rel'
                    run([sdcc, *flags, f'-DOBJECT_SIZE={size}UL', '-c', str(source), '-o', str(rel)])
                    actual = area_size(rel, 'XSEG')
                    assert actual == size, f'{name}: XSEG reserved {actual}, expected {size}'
                    # The same size must reach the linker's RAM overflow guard.
                    if size == 65536:
                        log = run([sdcc, *flags, '--xram-size', '65535', str(rel),
                                   '-o', str(work / f'{name}.ihx')], success=False)
                        assert 'Insufficient EXTERNAL RAM memory' in log, log
                    checks.append({'name': name, 'area': 'XSEG', 'size': actual})
        for kind, area in (('TEST_INITIALIZED', 'XISEG'), ('TEST_CODE', 'CONST')):
            rel = work / f'{kind}.rel'
            run([sdcc, '-mmcs251', '--model-large', f'-D{kind}',
                 '-DOBJECT_SIZE=65537UL', '-c', str(source), '-o', str(rel)])
            actual = area_size(rel, area)
            assert actual == 65537, f'{kind}: {area} reserved {actual}, expected 65537'
            if kind == 'TEST_INITIALIZED':
                assert area_size(rel, 'XINIT') == actual
            checks.append({'name': kind, 'area': area, 'size': actual})
        # Shared glue changes must not change the mature MCS51 path.
        rel = work / 'mcs51.rel'
        run([sdcc, '-mmcs51', '--model-large', '-DOBJECT_SIZE=257',
             '-c', str(source), '-o', str(rel)])
        assert area_size(rel, 'XSEG') == 257
        checks.append({'name': 'mcs51', 'area': 'XSEG', 'size': 257})
    report = {'status': 'PASS', 'sdcc': sdcc, 'checks': checks,
              'ram_overflow_rejections': 4}
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2) + '\n')
    print(f'PASS: {len(checks)} large-object checks and 4 RAM overflow rejections')


if __name__ == '__main__':
    main()
