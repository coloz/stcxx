#!/usr/bin/env python3
"""Compile real SDCC objects and dependency files under paths containing spaces.

This is a host-driver regression, not a target-runtime or hardware qualification.
Both -MD and -MMD must preserve the dependency filename and quote the Make target.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdcc', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    compiler = args.sdcc.resolve(strict=True)
    output = args.output.resolve()
    if output.exists():
        parser.error('output must be a new directory')
    output.mkdir(parents=True)
    before = sha(compiler)
    report = {'status': 'RUNNING', 'scope': __doc__, 'compiler': str(compiler),
              'compiler_sha256': before, 'results': []}
    for target in ('mcs51', 'mcs251'):
        for flag in ('-MD', '-MMD'):
            case = output / (target + flag + ' path with spaces')
            case.mkdir()
            source, header = case / 'input file.c', case / 'header file.h'
            source.write_text('#include "header file.h"\nunsigned char value = TEST_VALUE;\n')
            header.write_text('#define TEST_VALUE 23\n')
            obj = case / 'result file.rel'
            command = [str(compiler), '-m' + target, '-c', flag, str(source), '-o', str(obj)]
            row = {'target': target, 'flag': flag, 'command': command, 'status': 'FAIL'}
            try:
                with (case / 'stdout.log').open('xb') as stdout, (case / 'stderr.log').open('xb') as stderr:
                    result = subprocess.run(command, cwd=case, stdout=stdout, stderr=stderr, timeout=60)
                row['exit_code'] = result.returncode
                if result.returncode:
                    raise RuntimeError('compiler failed; see stderr.log')
                dep = obj.with_suffix('.d')
                if not obj.is_file() or not dep.is_file():
                    raise RuntimeError('missing exact object or dependency filename')
                dependency = dep.read_text().replace('\\\n', '')
                if 'result\\ file.rel:' not in dependency or 'header\\ file.h' not in dependency:
                    raise RuntimeError('dependency target/header path is not Make-quoted')
                if source.read_text() != '#include "header file.h"\nunsigned char value = TEST_VALUE;\n' or header.read_text() != '#define TEST_VALUE 23\n':
                    raise RuntimeError('source input changed')
                row.update(status='PASS', object_sha256=sha(obj), dependency_sha256=sha(dep), dependency=dependency)
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                row['error'] = str(error)
            row['files'] = {p.name: sha(p) for p in case.iterdir() if p.is_file()}
            report['results'].append(row)
    report['compiler_unchanged'] = sha(compiler) == before
    report['status'] = 'PASS' if report['compiler_unchanged'] and all(r['status'] == 'PASS' for r in report['results']) else 'FAIL'
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(report['status'])
    return int(report['status'] != 'PASS')


if __name__ == '__main__':
    sys.exit(main())
