#!/usr/bin/env python3
"""Archive a pinned Mac frontend; use --output - to stream without a disk copy.

This verifies package integrity and deterministic metadata. Native load-path,
ABI and Arduino qualification remain separate checks. A streamed archive is
usable only after successful exit and verification of the stderr JSON digest.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def inventory(root):
    result = {}
    for path in [root, *sorted(root.rglob('*'))]:
        info = path.lstat()
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ValueError('unsupported package entry: ' + str(path))
        name = path.relative_to(root).as_posix()
        if '\\' in name or '\n' in name or '\r' in name:
            raise ValueError('unsupported package path: ' + repr(name))
        result[name] = {'directory': stat.S_ISDIR(info.st_mode), 'mode': stat.S_IMODE(info.st_mode)}
        if not result[name]['directory']:
            result[name].update(bytes=info.st_size, sha256=sha256(path))
    return result


def validate(root, expected_manifest):
    if not root.is_dir() or root.is_symlink():
        raise ValueError('package must be a real directory')
    before = inventory(root)
    manifest = root / 'MANIFEST.sha256'
    if not re.fullmatch('[0-9a-f]{64}', expected_manifest) or sha256(manifest) != expected_manifest:
        raise ValueError('manifest differs from pinned identity')
    expected = {}
    for line in manifest.read_text(encoding='utf-8').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        if not match:
            raise ValueError('invalid manifest record')
        digest, name = match.groups()
        path = PurePosixPath(name)
        if (name in expected or name == 'MANIFEST.sha256' or path.is_absolute() or
                path.as_posix() != name or '..' in path.parts or '\\' in name):
            raise ValueError('invalid or duplicate manifest path: ' + name)
        expected[name] = digest
    actual = {name: row['sha256'] for name, row in before.items()
              if not row['directory'] and name != 'MANIFEST.sha256'}
    if not expected or actual != expected:
        raise ValueError('files differ from complete manifest')
    provenance = json.loads((root / 'candidate-provenance.json').read_text(encoding='utf-8'))
    if provenance.get('status') != 'PASS' or provenance.get('architecture') != 'arm64':
        raise ValueError('package provenance did not pass for ARM64')
    epoch = provenance.get('source_date_epoch')
    if type(epoch) is not int or not 0 <= epoch < 2**33:
        raise ValueError('invalid source_date_epoch')
    return before, epoch


class DigestWriter:
    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()
        self.size = 0

    def write(self, data):
        written = self.stream.write(data)
        if written != len(data):
            raise OSError('short archive write')
        self.digest.update(data)
        self.size += written
        return written


class DigestReader:
    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()

    def read(self, size):
        data = self.stream.read(size)
        self.digest.update(data)
        return data


def write_archive(root, stream, before, epoch):
    writer = DigestWriter(stream)
    with tarfile.open(fileobj=writer, mode='w|bz2', format=tarfile.GNU_FORMAT) as archive:
        for name, row in before.items():
            info = tarfile.TarInfo('stcxx-frontend' + ('/' + name if name != '.' else ''))
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            info.mtime = epoch
            # Directory creation umasks do not belong to a distribution identity.
            info.mode = 0o755 if row['directory'] else row['mode']
            if row['directory']:
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            else:
                info.size = row['bytes']
                with (root / name).open('rb') as content:
                    reader = DigestReader(content)
                    archive.addfile(info, reader)
                    if reader.digest.hexdigest() != row['sha256']:
                        raise ValueError('file changed while reading; discard output: ' + name)
    stream.flush()
    if inventory(root) != before:
        raise ValueError('package changed during archival; discard output')
    return {'status': 'PASS_ARCHIVE', 'archive_sha256': writer.digest.hexdigest(),
            'archive_bytes': writer.size, 'files': sum(not r['directory'] for r in before.values())}


def archive_package(source, destination, expected_manifest, stream=None):
    # Check the supplied root before resolving it, to reject a root symlink.
    before, epoch = validate(source, expected_manifest)
    root = source.resolve()
    if stream is not None:
        report = write_archive(root, stream, before, epoch)
    else:
        destination = Path(destination)
        if destination.is_symlink() or destination.exists():
            raise ValueError('archive destination already exists')
        output = destination.resolve()
        if output == root or root in output.parents:
            raise ValueError('archive must be outside package')
        with output.open('xb') as target:
            report = write_archive(root, target, before, epoch)
        observed = {}
        with tarfile.open(output) as result:
            for member in result:
                if member.isdir():
                    continue
                if not member.isfile() or member.name in observed:
                    raise ValueError('unexpected archive entry; discard output')
                with result.extractfile(member) as content:
                    digest = hashlib.file_digest(content, 'sha256').hexdigest()
                observed[member.name] = (digest, member.size, member.mode)
        expected = {'stcxx-frontend/' + n: (r['sha256'], r['bytes'], r['mode'])
                    for n, r in before.items() if not r['directory']}
        if observed != expected or sha256(output) != report['archive_sha256']:
            raise ValueError('archive round-trip differs; discard output')
    report.update(manifest_sha256=expected_manifest, source_date_epoch=epoch)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--output', required=True, help='new archive path, or - for stdout')
    args = parser.parse_args()
    streaming = args.output == '-'
    try:
        report = archive_package(args.package, args.output, args.manifest_sha256,
                                 sys.stdout.buffer if streaming else None)
    except (ValueError, OSError, KeyError) as error:
        parser.exit(2, str(error) + '\n')
    print(json.dumps(report), file=sys.stderr if streaming else sys.stdout)


if __name__ == '__main__':
    main()
