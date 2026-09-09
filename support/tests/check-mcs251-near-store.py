#!/usr/bin/env python3
"""Compile and assemble DATA/IDATA stores that require fixed byte registers."""
import argparse
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdcc', type=Path, required=True)
    args = parser.parse_args()
    # Preserve the build/bin entry point: it may be a symlink, and SDCC finds
    # its preprocessor/assembler relative to the invoked tool directory.
    compiler = args.sdcc.absolute()
    source = Path(__file__).with_name('mcs251-near-store.c')
    with tempfile.TemporaryDirectory(prefix='mcs251-near-store-') as directory:
        for memory in ('__data', '__idata'):
            output = Path(directory) / f'{memory}.rel'
            # -c invokes the real assembler, rather than accepting any text
            # emitted by -S. The original defect fails at this stage.
            subprocess.run([
                str(compiler), '-mmcs251', '--model-large', '--stack-auto',
                '--opt-code-size', '--std-sdcc11', f'-DTEST_MEMORY={memory}',
                '-c', str(source), '-o', str(output),
            ], check=True)
            if not output.is_file() or not output.stat().st_size:
                raise RuntimeError(f'{memory}: compiler did not create an object')
            print(f'{memory}: compiled and assembled')


if __name__ == '__main__':
    main()
