#!/usr/bin/env python3
"""Same-basename translation units must not share split static sections."""

import argparse
from pathlib import Path
import re
import subprocess
import tempfile


SOURCE = '''
static const __code unsigned char data[] = { VALUE };
static unsigned char function(unsigned char i) __attribute__((noinline))
{
    return data[i];
}
unsigned char ENTRY(unsigned char i) { return function(i); }
'''


def compile_sections(sdcc, source, output, cwd, flags=()):
    result = subprocess.run([sdcc, '-mmcs251', '--model-large',
                             '--function-sections', '--data-sections',
                             *flags, '-S', str(source), '-o', str(output)],
                            cwd=cwd, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    assert result.returncode == 0, result.stdout
    return set(re.findall(r'^\s*\.area\s+((?:CSEG_F|CONST_D)_\S+)',
                          output.read_text(), re.MULTILINE))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdcc', required=True)
    args = parser.parse_args()
    sdcc = str(Path(args.sdcc).resolve())
    with tempfile.TemporaryDirectory(prefix='mcs251-section-identity-') as temporary:
        work = Path(temporary)
        sections = []
        for index in (1, 2):
            directory = work / f'tu{index}'
            directory.mkdir()
            source = directory / 'foo.c'
            source.write_text(SOURCE.replace('VALUE', str(index)).replace('ENTRY', f'entry{index}'))
            relative = compile_sections(sdcc, 'foo.c', directory / 'relative.asm', directory)
            absolute = compile_sections(sdcc, source, directory / 'absolute.asm', directory)
            assert relative == absolute, 'absolute and relative names of one TU must agree'
            assert any(x.startswith('CSEG_F__function_') for x in relative), relative
            assert any(x.startswith('CONST_D__data_') for x in relative), relative
            sections.append(relative)
            custom = compile_sections(sdcc, source, directory / 'custom.asm', directory,
                                      ('--codeseg', 'CUSTOM_CODE', '--constseg', 'CUSTOM_CONST'))
            assert not custom, 'explicit section names must not be split'
        assert not (sections[0] & sections[1]), f'colliding TU sections: {sections[0] & sections[1]}'
    print('PASS: relative/absolute TU identities, independent same-basename TUs, custom sections')


if __name__ == '__main__':
    main()
