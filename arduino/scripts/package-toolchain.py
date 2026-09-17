#!/usr/bin/env python3
"""Bundle locked native frontend and SDCC archives as one Arduino tool.

Component payloads, manifests, licenses and executable modes are preserved.
The output is deterministic and never overwrites an existing archive.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import stat
import tarfile
import tempfile
import zipfile

HOSTS = {
    'windows-x86_64': ('windows_frontend', 'windows_package_archive_sha256'),
    'darwin-arm64': ('macos_frontend', 'native_package_archive_sha256'),
}
EPOCH = 1788134400


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def unpack(archive_path, destination, expected_root):
    """Stage files and validate internal file links without creating host symlinks."""
    modes, seen, links = {}, set(), {}

    def target(name, directory):
        path = PurePosixPath(name)
        require(name and not path.is_absolute() and '\\' not in name and ':' not in name and
                all(part not in ('', '.', '..') for part in name.rstrip('/').split('/')),
                'Unsafe component path: ' + name)
        require(path.parts[0] == expected_root, 'Unexpected component root: ' + name)
        identity = path.as_posix().casefold()
        require(identity not in seen, 'Duplicate component path: ' + name)
        seen.add(identity)
        relative = PurePosixPath(*path.parts[1:])
        require(directory or relative.parts, 'Component root must be a directory')
        output = destination.joinpath(*relative.parts)
        if directory:
            output.mkdir(parents=True, exist_ok=True)
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
        return output, relative.as_posix()

    destination.mkdir()
    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path) as archive:
            for entry in archive.infolist():
                mode = entry.external_attr >> 16
                require(stat.S_IFMT(mode) in (0, stat.S_IFREG, stat.S_IFDIR),
                        'Unsupported ZIP entry: ' + entry.filename)
                output, relative = target(entry.filename, entry.is_dir())
                if not entry.is_dir():
                    with archive.open(entry) as source, output.open('xb') as sink:
                        shutil.copyfileobj(source, sink)
                    modes[relative] = stat.S_IMODE(mode) or 0o644
    else:
        with tarfile.open(archive_path) as archive:
            for entry in archive:
                require(entry.isfile() or entry.isdir() or entry.issym() or entry.islnk(),
                        'Unsupported tar entry: ' + entry.name)
                output, relative = target(entry.name, entry.isdir())
                if entry.isfile():
                    with archive.extractfile(entry) as source, output.open('xb') as sink:
                        shutil.copyfileobj(source, sink)
                    modes[relative] = entry.mode & 0o777
                elif entry.issym() or entry.islnk():
                    require(entry.linkname and not entry.linkname.startswith('/') and
                            '\\' not in entry.linkname and ':' not in entry.linkname,
                            'Unsafe component link: ' + entry.name)
                    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(relative), entry.linkname)
                                                 if entry.issym() else entry.linkname)
                    if entry.islnk():
                        require(resolved.startswith(expected_root + '/'), 'Hard link escapes component')
                        resolved = resolved[len(expected_root) + 1:]
                    require(resolved != '..' and not resolved.startswith('../'), 'Link escapes component')
                    links[relative] = (entry.type, entry.linkname, resolved)
                    modes[relative] = entry.mode & 0o777
    for name in modes:
        require(not any(parent.as_posix() in links for parent in PurePosixPath(name).parents),
                'Component entry traverses a link: ' + name)
    for name, (_, _, resolved) in links.items():
        visited = {name}
        while resolved in links:
            require(resolved not in visited, 'Cyclic component link')
            visited.add(resolved)
            resolved = links[resolved][2]
        require(resolved in modes and (destination / resolved).is_file(), 'Missing component link target')
        shutil.copyfile(destination / resolved, destination / name)
    return modes, links


def verify_manifest(root, expected):
    manifest = root / 'MANIFEST.sha256'
    require(digest(manifest) == expected, 'Component manifest differs from SDK lock: ' + str(root))
    rows = {}
    for line in manifest.read_text(encoding='utf-8').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        require(match is not None, 'Invalid component manifest row')
        checksum, name = match.groups()
        require(name not in rows, 'Duplicate component manifest row: ' + name)
        rows[name] = checksum
    actual = {p.relative_to(root).as_posix(): digest(p) for p in root.rglob('*')
              if p.is_file() and p != manifest}
    require(rows == actual, 'Component files differ from their manifest: ' + str(root))


def package(sdcc, frontend, lock_path, version, output):
    require(re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.+-]+)?', version), 'Invalid toolchain version')
    lock = json.loads(lock_path.read_text(encoding='utf-8'))
    host = lock['host']
    require(host in HOSTS, 'Unsupported native toolchain host')
    frontend_key, sdcc_key = HOSTS[host]
    sources = {'frontend': frontend, 'sdcc': sdcc}
    identities = {name: digest(path) for name, path in sources.items()}
    require(identities['frontend'] == lock[frontend_key]['archive_sha256'], 'Frontend archive differs from SDK lock')
    require(identities['sdcc'] == lock['tools']['sdcc'][sdcc_key], 'SDCC archive differs from SDK lock')
    suffix = 'macos-arm64' if host == 'darwin-arm64' else host
    archive_name = f'stcxx-toolchain-{version}-{suffix}.tar.bz2'
    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / archive_name
    report_path = output / (archive_name + '.json')
    require(not archive_path.exists() and not report_path.exists(), 'Toolchain output already exists')
    with tempfile.TemporaryDirectory(prefix='stcxx-package-', dir=output) as temporary:
        root = Path(temporary) / 'stcxx-toolchain'
        root.mkdir()
        modes, links = {}, {}
        for component, path in sources.items():
            original_root = 'stcxx-frontend' if component == 'frontend' else 'sdcc-mcs251'
            component_modes, component_links = unpack(path, root / component, original_root)
            expected = (lock[frontend_key]['manifest_sha256'] if component == 'frontend'
                        else lock['tools']['sdcc']['native_package_manifest_sha256'])
            verify_manifest(root / component, expected)
            modes.update({component + '/' + name: mode for name, mode in component_modes.items()})
            for name, (kind, target, resolved) in component_links.items():
                links[component + '/' + name] = (kind, target if kind == tarfile.SYMTYPE else
                                                 'stcxx-toolchain/' + component + '/' + resolved)
        metadata = {'schema_version': 1, 'name': 'stcxx-toolchain', 'version': version, 'host': host,
                    'components': {name: {'path': name, 'archive': path.name, 'archive_sha256': identities[name]}
                                   for name, path in sources.items()}}
        (root / 'toolchain.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8', newline='\n')
        modes['toolchain.json'] = 0o644
        inventory = ''.join(f'{digest(root / name)}  {name}\n' for name in sorted(modes))
        (root / 'MANIFEST.sha256').write_text(inventory, encoding='utf-8', newline='\n')
        modes['MANIFEST.sha256'] = 0o644
        staged = Path(temporary) / archive_name
        with tarfile.open(staged, 'w:bz2', format=tarfile.PAX_FORMAT) as archive:
            entry = tarfile.TarInfo('stcxx-toolchain')
            entry.type, entry.mode, entry.mtime = tarfile.DIRTYPE, 0o755, EPOCH
            archive.addfile(entry)
            for name in sorted(modes):
                path = root / name
                entry = tarfile.TarInfo('stcxx-toolchain/' + name)
                entry.mode, entry.mtime, entry.size = modes[name], EPOCH, path.stat().st_size
                if name in links:
                    entry.type, entry.linkname = links[name]
                    entry.size = 0
                    archive.addfile(entry)
                else:
                    with path.open('rb') as stream:
                        archive.addfile(entry, stream)
        require(all(digest(path) == identities[name] for name, path in sources.items()),
                'Component archive changed during packaging')
        report = {**metadata, 'archiveFileName': archive_name, 'archiveRoot': 'stcxx-toolchain',
                  'size': staged.stat().st_size, 'sha256': digest(staged),
                  'manifest_sha256': digest(root / 'MANIFEST.sha256')}
        # Reserve the destination exclusively; no previous release can be overwritten.
        with staged.open('rb') as source, archive_path.open('xb') as sink:
            shutil.copyfileobj(source, sink)
        with report_path.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(report, stream, indent=2)
            stream.write('\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('sdcc', 'frontend', 'lock', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    print(json.dumps(package(args.sdcc, args.frontend, args.lock, args.version, args.output), indent=2))


if __name__ == '__main__':
    main()
