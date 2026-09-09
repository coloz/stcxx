#!/usr/bin/env python3
"""Assemble and link MCS251 DR immediates, including startup's symbol - 1."""
import argparse
from pathlib import Path
import subprocess
import tempfile


def run(command, *, success=True):
    result = subprocess.run(command, text=True, capture_output=True)
    if (result.returncode == 0) != success:
        raise RuntimeError(f'{command}\n{result.stdout}{result.stderr}')
    return result


def read_hex(path):
    image = {}
    base = 0
    for line in path.read_text().splitlines():
        record = bytes.fromhex(line[1:])
        if sum(record) & 255:
            raise AssertionError('Invalid Intel HEX checksum')
        size, kind = record[0], record[3]
        address = int.from_bytes(record[1:3], 'big')
        if kind == 4:
            base = int.from_bytes(record[4:6], 'big') << 16
        elif kind == 0:
            image.update((base + address + i, value)
                         for i, value in enumerate(record[4:4 + size]))
    return bytes(image[address] for address in sorted(image))


def assemble_link(assembler, linker, directory, name, body):
    source = directory / f'{name}.asm'
    source.write_text('.module immediate_test\n'
                      '.optsdcc -mmcs251 --model-large\n'
                      '.globl __start__stack\n.area CSEG (CODE)\n' + body)
    run([str(assembler), '-plosgff', str(source)])
    run([str(linker), '-n', '-i', '-g', '__start__stack=0x0100',
         str(directory / name), str(source.with_suffix('.rel'))])
    return read_hex(source.with_suffix('.ihx'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assembler', type=Path, required=True)
    parser.add_argument('--linker', type=Path, required=True)
    parser.add_argument('--baseline', type=Path,
                        help='Optionally prove the old assembler emits the wrong opcode')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='mcs251-dr-immediate-') as name:
        directory = Path(name)
        startup = 'mov spx,#__start__stack - 1\n'
        expected = bytes.fromhex('7E F8 00 FF')
        if args.baseline:
            actual = assemble_link(args.baseline, args.linker, directory,
                                   'baseline', startup)
            assert actual == bytes.fromhex('7E FC 00 FF'), actual.hex()
            print('Old assembler defect reproduced: linked SPX opcode FC')
        actual = assemble_link(args.assembler, args.linker, directory,
                               'startup', startup)
        assert actual == expected, actual.hex()
        print('Relocated __start__stack=0100 minus 1: 7E F8 00 FF')

        constants = [('0', '7E 08 00 00'),
                     ('0x7fff', '7E 08 7F FF'),
                     ('0x8000', '7E 08 80 00'),
                     ('0xffff', '7E 08 FF FF'),
                     ('-1', '7E 0C FF FF'),
                     ('-32768', '7E 0C 80 00'),
                     ('-65536', '7E 0C 00 00')]
        actual = assemble_link(args.assembler, args.linker, directory,
                               'constants', ''.join(f'mov dr0,#{value}\n'
                                                   for value, _ in constants))
        expected = b''.join(bytes.fromhex(code) for _, code in constants)
        assert actual == expected, actual.hex()
        print('Absolute zero/one-filled high-word boundary encodings passed')
        for index, value in enumerate(('0x10000', '-65537')):
            source = directory / f'invalid_{index}.asm'
            source.write_text('.module invalid\n.area CSEG (CODE)\n'
                              f'mov dr0,#{value}\n')
            result = run([str(args.assembler), '-plosgff', str(source)],
                         success=False)
            assert 'zero-filled or one-filled' in result.stderr, result.stderr
        print('Unrepresentable absolute immediates rejected')


if __name__ == '__main__':
    main()
