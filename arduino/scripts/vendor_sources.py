#!/usr/bin/env python3
"""Verify vendored frontend sources and reconstruct the upstream CBE archive.

The manifest binds the complete imported file inventory, exact bytes and Git
modes to the source lock. Git's Windows symlink placeholders are also accepted.
No nested repository, network access or upstream Git objects are needed.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(value):
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or path.as_posix() != value
            or any(part in ('.', '..', '.git') for part in path.parts)
            or '\\' in value or ':' in value):
        raise ValueError('invalid manifest path: ' + value)
    return path


def load_manifest(root, lock_path=None, manifest_path=None):
    root = Path(root)
    lock = json.loads(Path(lock_path or root / 'arduino/toolchain-lock.json').read_text(encoding='utf-8'))
    identity = lock['vendored_sources']
    path = Path(manifest_path) if manifest_path else root / safe_path(identity['manifest'])
    payload = path.read_bytes()
    if digest(payload) != identity['manifest_sha256']:
        raise ValueError('vendored source manifest differs from source lock')
    manifest = json.loads(payload)
    if manifest['schema_version'] != 1 or set(manifest['components']) != {'clang', 'llvm_cbe'}:
        raise ValueError('unsupported vendored source manifest')
    for name, component in manifest['components'].items():
        if any(component[key] != lock[name][key] for key in ('repository', 'commit')):
            raise ValueError('upstream identity differs: ' + name)
        safe_path(component['path'])
        for filename, row in component['files'].items():
            safe_path(filename)
            if row['mode'] not in ('100644', '100755', '120000'):
                raise ValueError('unsupported source mode: ' + filename)
            for key in ('sha256', 'upstream_sha256'):
                if key in row and not re.fullmatch('[0-9a-f]{64}', row[key]):
                    raise ValueError('invalid source hash: ' + filename)
    return lock, manifest


def inventory(root):
    """List files and links without following directory symlinks."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError('source directory is missing or is a symlink: ' + str(root))
    result = set()
    for directory, directories, files in os.walk(root):
        for name in list(directories):
            path = Path(directory) / name
            if path.is_symlink():
                result.add(path.relative_to(root).as_posix())
                directories.remove(name)
            elif name == '.git':
                raise ValueError('nested Git repository in vendored source: ' + str(path))
        result.update((Path(directory) / name).relative_to(root).as_posix() for name in files)
    return result


def read_entry(path, row):
    if path.is_symlink():
        if row['mode'] != '120000':
            raise ValueError('unexpected source symlink: ' + str(path))
        return os.readlink(path).encode('utf-8')
    # With core.symlinks=false Git stores the link target in a regular file.
    if not path.is_file():
        raise ValueError('source is not a regular file: ' + str(path))
    return path.read_bytes()


def verify_component(root, component):
    source = Path(root) / safe_path(component['path'])
    rows = component['files']
    observed = inventory(source)
    if observed != set(rows):
        raise ValueError('source inventory differs: missing=' + repr(sorted(set(rows) - observed)[:5])
                         + ', unexpected=' + repr(sorted(observed - set(rows))[:5]))
    for name, row in rows.items():
        if digest(read_entry(source / name, row)) != row['sha256']:
            raise ValueError('vendored source differs: ' + str(source / name))
    return len(rows)


def write_archive(source, rows, destination):
    """Write a deterministic upstream archive after verifying every base file."""
    payloads = {}
    for name, row in rows.items():
        data = read_entry(source / name, row)
        if digest(data) != row.get('upstream_sha256', row['sha256']):
            raise ValueError('reconstructed upstream source differs: ' + name)
        payloads[name] = data
    with tarfile.open(destination, 'x', format=tarfile.GNU_FORMAT) as archive:
        for name in sorted(rows):
            row = rows[name]; data = payloads[name]
            item = tarfile.TarInfo(name)
            item.mode = 0o755 if row['mode'] == '100755' else 0o644
            item.mtime = 0
            if row['mode'] == '120000':
                item.type = tarfile.SYMTYPE
                item.linkname = data.decode('utf-8')
                archive.addfile(item)
            else:
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))


def archive_cbe(root, lock, manifest, destination, patch_path=None):
    """Reverse the retained STC patch in a private copy, never in the checkout."""
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('source archive already exists: ' + str(destination))
    component = manifest['components']['llvm_cbe']
    verify_component(root, component)
    patch_path = Path(patch_path or Path(root) / lock['llvm_cbe']['patch'])
    patch = patch_path.read_bytes()
    if digest(patch) != lock['llvm_cbe']['patch_sha256']:
        raise ValueError('CBE patch differs from source lock')
    source = Path(root) / component['path']
    with tempfile.TemporaryDirectory(prefix='stcxx-cbe-base-') as directory:
        base = Path(directory) / 'source'; base.mkdir()
        patch_copy = Path(directory) / 'stc.patch'; patch_copy.write_bytes(patch)
        for name, row in component['files'].items():
            path = base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            # Links remain placeholders while patch writes files. The archive
            # restores their original Git type without traversing any target.
            data = read_entry(source / name, row)
            if digest(data) != row['sha256']:
                raise ValueError('source changed while snapshotting: ' + name)
            path.write_bytes(data)
        subprocess.run(['patch', '--reverse', '--batch', '--fuzz=0', '-p1',
                        '-d', str(base), '-i', str(patch_copy)], check=True)
        if inventory(base) != set(component['files']):
            raise ValueError('unexpected files after reversing CBE patch')
        write_archive(base, component['files'], destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--lock', type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--component', choices=('clang', 'llvm_cbe'), action='append')
    parser.add_argument('--cbe-archive', type=Path)
    parser.add_argument('--cbe-patch', type=Path)
    args = parser.parse_args()
    try:
        lock, manifest = load_manifest(args.root, args.lock, args.manifest)
        if args.cbe_archive:
            archive_cbe(args.root, lock, manifest, args.cbe_archive, args.cbe_patch)
            print('LLVM_CBE_BASE_ARCHIVE=PASS')
        else:
            for name in args.component or ('clang', 'llvm_cbe'):
                count = verify_component(args.root, manifest['components'][name])
                print(name.upper() + '_VENDORED_FILES=' + str(count), flush=True)
                print(name.upper() + '_UPSTREAM_COMMIT=' + lock[name]['commit'])
            print('VENDORED_SOURCES=PASS')
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
