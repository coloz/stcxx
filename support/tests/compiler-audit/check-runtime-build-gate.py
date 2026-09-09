#!/usr/bin/env python3
"""The MCS251 runtime build must propagate assembler errors to make."""

import argparse
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--makefile', required=True)
    args = parser.parse_args()
    makefile = Path(args.makefile).resolve()
    assert makefile.is_file(), makefile
    with tempfile.TemporaryDirectory(prefix='mcs251-runtime-build-gate-') as temporary:
        for target in ('crtxinit.rel', 'crtstart.rel'):
            work = Path(temporary) / target
            work.mkdir()
            result = subprocess.run(
                ['make', '-f', str(makefile), 'AS=false', target], cwd=work,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            assert result.returncode != 0, f'{target}: assembler failure was ignored\n{result.stdout}'
            assert 'false -plosgff' in result.stdout, result.stdout
            assert not (work / target).exists(), target
    print('PASS: native and generated MCS251 runtime assembler errors stop the build')


if __name__ == '__main__':
    main()
