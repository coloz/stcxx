#!/usr/bin/env python3
"""Create a deterministic ZIP from a staged candidate with a full SHA-256 manifest."""
import argparse
import hashlib
from pathlib import Path, PurePosixPath
import re
import zipfile


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(directory):
    entries = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ValueError(f'symlinks are not supported: {path}')
        if path.is_file():
            entries[path.relative_to(directory).as_posix()] = sha(path)
    return entries


def archive(source, output):
    source, output = source.resolve(), output.resolve()
    if source == output or source in output.parents:
        raise ValueError('archive must be outside the staged directory')
    before = inventory(source)
    expected = {}
    for line in (source / 'MANIFEST.sha256').read_text(encoding='utf-8').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        if not match:
            raise ValueError('invalid SHA-256 manifest line')
        digest, name = match.groups()
        if (name in expected or name == 'MANIFEST.sha256' or '\\' in name or
                str(PurePosixPath(name)) != name or PurePosixPath(name).is_absolute() or
                '..' in PurePosixPath(name).parts):
            raise ValueError(f'invalid or duplicate manifest path: {name}')
        expected[name] = digest
    if not expected or expected != {k: v for k, v in before.items() if k != 'MANIFEST.sha256'}:
        raise ValueError('staged files do not match the complete SHA-256 manifest')
    # No timestamp, permissions, traversal order or absolute build path from
    # the filesystem is allowed to influence ZIP metadata.
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as result:
        for relative in before:
            info = zipfile.ZipInfo('sdcc-mcs251/' + relative, date_time=(2026, 8, 31, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o100755 if relative.endswith('.exe') else 0o100644) << 16
            result.writestr(info, (source / relative).read_bytes(),
                           compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    if inventory(source) != before:
        raise ValueError('staged candidate changed during archival; discard archive')
    with zipfile.ZipFile(output) as result:
        archived = {item.filename.removeprefix('sdcc-mcs251/'):
                    hashlib.sha256(result.read(item)).hexdigest() for item in result.infolist()}
    if archived != before:
        raise ValueError('archive round-trip differs from staged inputs; discard archive')
    return {'path': str(output), 'sha256': sha(output), 'size': output.stat().st_size,
            'files': len(before), 'status': 'PASS'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    import json
    print(json.dumps(archive(args.input, args.output), indent=2))


if __name__ == '__main__':
    main()
