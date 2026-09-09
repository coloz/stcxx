#!/usr/bin/env python3
"""Regression checks for relocatable native instructions and Flash islands."""

import argparse
from contextlib import nullcontext
from pathlib import Path
import re
import subprocess
import tempfile


def run(command, cwd, success=True):
    result = subprocess.run(command, cwd=cwd, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if (result.returncode == 0) != success:
        raise AssertionError(f"unexpected exit {result.returncode}: {' '.join(command)}\n{result.stdout}")
    return result.stdout


def read_hex(path):
    result = {}
    base = 0
    for line in path.read_text().splitlines():
        data = bytes.fromhex(line[1:])
        assert sum(data) % 256 == 0
        count, address, kind = data[0], int.from_bytes(data[1:3], 'big'), data[3]
        if kind == 4:
            base = int.from_bytes(data[4:6], 'big') << 16
        elif kind == 0:
            for index, value in enumerate(data[4:4 + count]):
                assert base + address + index not in result, 'overlapping Intel HEX payload'
                result[base + address + index] = value
    return result


def check_case(assembler, linker, directory, sources, options=(), expected=(), error=None):
    directory.mkdir()
    for name, source in sources.items():
        (directory / f'{name}.asm').write_text(source, encoding='ascii')
        run([assembler, '-plosg', f'{name}.asm'], directory)
    commands = ['-mwxu', '-r', '-i image.ihx']
    if not any(option.startswith('--code-window=') for option in options):
        commands.append('--code-window=0xFE0000:0x1000000')
    commands.extend(options)
    commands.extend(f'{name}.rel' for name in sources)
    (directory / 'link.lk').write_text('\n'.join(commands) + '\n', encoding='ascii')
    output = run([linker, '-nf', 'link.lk'], directory, success=error is None)
    if error:
        assert error in output, output
        return
    payload = read_hex(directory / 'image.ihx')
    for address, values in expected:
        actual = bytes(payload.get(address + i, 0xFF) for i in range(len(values)))
        assert actual == values, f'{address:06X}: expected {values.hex()}, got {actual.hex()}'
    # Every emitted byte must be covered by the native allocation ledger.
    ledger = []
    for line in (directory / 'image.map').read_text().splitlines():
        match = re.fullmatch(r'Code Window Area: (\S+) 0x([0-9A-Fa-f]+) 0x([0-9A-Fa-f]+)', line)
        if match:
            start, size = int(match[2], 16), int(match[3], 16)
            ledger.append((start, start + size))
    assert ledger
    assert all(left[1] <= right[0] for left, right in zip(ledger, ledger[1:]))
    assert all(any(start <= address < end for start, end in ledger) for address in payload)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('assembler')
    parser.add_argument('linker')
    parser.add_argument('--work-dir', type=Path, help='retain generated objects and diagnostics')
    args = parser.parse_args()
    assembler, linker = str(Path(args.assembler).resolve()), str(Path(args.linker).resolve())
    cases = [
        ('absolute-control-target', {'a': '''.module a
.area TEXT (REL,CON,CODE)
.source
lcall 0xFF0100
acall 0xFF0100
'''}, ['-b TEXT=0xFF0000'], [(0xFF0000, bytes.fromhex('12 01 00 31 00'))], None),
        ('wide-control-addend', {'a': '''.module a
.area TEXT (REL,CON,CODE)
.source
.ds 0x10000
target:
lcall target
acall target
'''}, ['-b TEXT=0xFE0000'], [(0xFF0000, bytes.fromhex('12 00 00 11 00'))], None),
        ('absolute-control-wrong-region', {'a': '''.module a
.area TEXT (REL,CON,CODE)
lcall 0xFF0100
'''}, ['-b TEXT=0xFE0000'], [], '64K Region relocation error'),
        ('absolute-control-wrong-page', {'a': '''.module a
.area TEXT (REL,CON,CODE)
acall 0xFF0800
'''}, ['-b TEXT=0xFF0000'], [], '2K Page relocation error'),
        ('shifted-region-control', {'a': '''.module a
.area TEXT (REL,CON,CODE)
.source
.ds 0xFFFD
lcall target
target:
nop
'''}, ['-b TEXT=0xFE0002'], [(0xFEFFFF, bytes.fromhex('12 00 02 00'))], None),
        ('indexed-symbol', {'a': '''.module a
.globl displacement
.area TEXT (REL,CON,CODE)
.source
mov r10,@dr16+displacement
mov @wr6+displacement,r10
''', 'b': '''.module b
.globl displacement
displacement=0x1234
'''}, ['-b TEXT=0xFE0000'], [(0xFE0000, bytes.fromhex('29 a4 12 34 19 a3 12 34'))], None),
        ('indexed-symbol-overflow', {'a': '''.module a
.globl displacement
.area TEXT (REL,CON,CODE)
.source
mov r10,@dr16+displacement
''', 'b': '''.module b
.globl displacement
displacement=0x10000
'''}, [], [], '16-bit displacement relocation error'),
        ('indexed-symbol-underflow', {'a': '''.module a
.globl displacement
.area TEXT (REL,CON,CODE)
mov r10,@dr16+displacement
''', 'b': '''.module b
.globl displacement
displacement=-32769
'''}, [], [], '16-bit displacement relocation error'),
        ('indexed-symbol-negative', {'a': '''.module a
.globl displacement
.area TEXT (REL,CON,CODE)
.source
mov r10,@dr16+displacement-1
''', 'b': '''.module b
.globl displacement
displacement=0x1234
'''}, ['-b TEXT=0xFE0000'], [(0xFE0000, bytes.fromhex('29 a4 12 33'))], None),
        ('direct-symbol-overflow', {'a': '''.module a
.globl sfr
.area TEXT (REL,CON,CODE)
mov a,S:sfr
''', 'b': '''.module b
.globl sfr
sfr=0x100
'''}, [], [], 'Page0 relocation error'),
        ('direct-symbol-valid', {'a': '''.module a
.globl sfr
.area TEXT (REL,CON,CODE)
mov a,S:sfr
''', 'b': '''.module b
.globl sfr
sfr=0x90
'''}, ['-b TEXT=0xFE0000'], [(0xFE0000, bytes.fromhex('e5 90'))], None),
        ('startup-packing-retry', {'a': '''.module a
.area HOME (REL,CON,CODE)
.byte 0xA1
.area GSINIT0 (REL,CON,CODE)
.byte 0xB1
.area GSFINAL (REL,CON,CODE)
.byte 0xB2
.area TEXT (REL,CON,CODE)
.byte 0xC1
.ds 0xFFFF
'''}, ['-b HOME=0xFF0000'], [(0xFE0000, b'\xC1'), (0xFF0000, bytes.fromhex('a1 b1 b2'))], None),
        ('k246-startup-packing-retry', {'a': '''.module a
.area HOME (REL,CON,CODE)
.byte 0xA1
.area GSINIT0 (REL,CON,CODE)
.byte 0xB1
.area GSFINAL (REL,CON,CODE)
.byte 0xB2
.area TEXT (REL,CON,CODE)
.byte 0xC1
.ds 0x2D7FF
'''}, ['--code-window=0xFC2800:0x1000000', '-b HOME=0xFF0000'], [(0xFC2800, b'\xC1'), (0xFF0000, bytes.fromhex('a1 b1 b2'))], None),
        ('fixed-startup-anchor', {'a': '''.module a
.area GSINIT0 (REL,CON,CODE)
.byte 0xB1
.area GSFINAL (REL,CON,CODE)
.byte 0xB2
'''}, ['-b GSFINAL=0xFF0001'], [(0xFF0000, bytes.fromhex('b1 b2'))], None),
        ('conflicting-startup-anchors', {'a': '''.module a
.area GSINIT0 (REL,CON,CODE)
.byte 0xB1
.area GSFINAL (REL,CON,CODE)
.byte 0xB2
'''}, ['-b GSINIT0=0xFE0000', '-b GSFINAL=0xFF0001'], [], 'inconsistent fixed startup stages'),
        ('relocatable-call-relaxation', {'a': '''.module a
.area TEXT (REL,CON,CODE)
.source
call target
.ds 2
target:
nop
'''}, ['-b TEXT=0xFE07FD'], [(0xFE07FD, bytes.fromhex('9a fe 08 03')), (0xFE0803, b'\x00')], None),
        ('mixed-area-flags', {'a': '''.module a
.area TEXT (REL,CON,CODE)
.byte 0x11
''', 'b': '''.module b
.area TEXT (REL,OVR,CODE)
.byte 0x22
'''}, [], [], 'Conflicting flags'),
        ('code-data-area-flags', {'a': '''.module a
.area TEXT (REL,CON,CODE)
.byte 0x11
''', 'b': '''.module b
.area TEXT (REL,CON)
.byte 0x22
'''}, [], [], 'Conflicting flags'),
        ('compiler-main-stack-area', {'a': '''.module a
; The ordinary compiler glue for a TU containing main selects bare SSEG.
.area SSEG
__start__stack:
.ds 1
.area CSEG (CODE)
_main::
ret
'''}, [], [(0xFE0000, b'\x22')], None),
        ('stack-code-area-flags', {'a': '''.module a
.area SSEG (CODE)
ret
'''}, [], [], 'Conflicting flags'),
        ('absolute-overlay', {'a': '''.module a
.area FIXED (ABS,OVR,CODE)
.org 0xFE0100
.byte 0xA1
'''}, [], [(0xFE0100, b'\xA1')], None),
        ('function-fragment-alignment', {'a': '''.module a
.source
.globl first,second,even_first,odd_second
.globl s_CSEG_F_SHARED,l_CSEG_F_SHARED,l_CSEG_F_EVEN
.area CSEG_F_SHARED (REL,CON,CODE)
first::
ret
.area CSEG_F_EVEN (REL,CON,CODE)
even_first::
nop
ret
.area TABLE (REL,CON,CODE)
.3byte first,second,s_CSEG_F_SHARED,l_CSEG_F_SHARED
.3byte even_first,odd_second,l_CSEG_F_EVEN
''', 'b': '''.module b
.source
.area CSEG_F_SHARED (REL,CON,CODE)
second::
nop
ret
.area CSEG_F_EVEN (REL,CON,CODE)
odd_second::
ret
'''}, ['-b TABLE=0xFF0100'], [
            (0xFE0000, b'\x22'),
            (0xFE0002, bytes.fromhex('00 22 00 22 22')),
            (0xFF0100, bytes.fromhex('fe0000 fe0002 fe0000 000004 fe0004 fe0006 000003')),
        ], None),
        ('function-fragment-padding-bounds', {'a': '''.module a
.area CSEG_F_SHARED (REL,CON,CODE)
ret
''', 'b': '''.module b
.area CSEG_F_SHARED (REL,CON,CODE)
ret
'''}, ['-b CSEG_F_SHARED=0xFFFFFE'], [], 'allocation outside Flash bounds'),
        ('multiple-tu-code', {'a': '''.module a
.area TEXT (REL,CON,CODE)
.byte 0x11,0x22
.area GSINIT0 (REL,CON,CODE)
.byte 0x31
.area FIXED (ABS,CODE)
.org 0xFF0000
.byte 0x41
''', 'b': '''.module b
.area TEXT (REL,CON,CODE)
.byte 0x55
.area GSINIT0 (REL,CON,CODE)
.byte 0x32
.area GSFINAL (REL,CON,CODE)
.byte 0x33
.area FIXED (ABS,CODE)
.org 0xFFFFFF
.byte 0x42
'''}, [], [(0xFE0000, bytes.fromhex('31 32 33 11 22 55')), (0xFF0000, b'\x41'), (0xFFFFFF, b'\x42')], None),
    ]
    failures = []
    if args.work_dir:
        args.work_dir.mkdir(parents=True, exist_ok=True)
    with (nullcontext(args.work_dir) if args.work_dir else
          tempfile.TemporaryDirectory(prefix='sdas251-audit.')) as tmp:
        for name, sources, options, expected, error in cases:
            try:
                check_case(assembler, linker, Path(tmp) / name, sources, options, expected, error)
                print(f'PASS {name}')
            except AssertionError as exc:
                failures.append(name)
                print(f'FAIL {name}: {exc}')
    if failures:
        raise SystemExit(f'{len(failures)} audit regression(s) failed: {", ".join(failures)}')
    print(f'MCS251 assembler/linker audit: {len(cases)} checks passed')


if __name__ == '__main__':
    main()
