#!/usr/bin/env python3
"""Create a deterministic ZIP from a fully inventoried Windows SDCC package.

The staging provenance is retained verbatim. Independent builds must therefore
use the same guest source/build paths to reproduce the archive. ZIP compression
also depends on the recorded Python/zlib versions. This does not qualify target
execution, source distribution, Arduino compatibility or physical hardware.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import re
import stat
import sys
import time
import zipfile
import zlib


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_name(name):
    parts = name.split('/')
    require(all(p not in ('', '.', '..') and not p.endswith((' ', '.')) and
                not re.search(r'[\x00-\x1f<>:"\\|?*]', p) and
                not re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', p)
                for p in parts), 'unsafe Windows archive name: ' + name)


def inventory(root):
    files = {}
    folded = set()
    for path in sorted(root.rglob('*')):
        name = path.relative_to(root).as_posix(); safe_name(name)
        require(not path.is_symlink() and path.resolve().is_relative_to(root), 'package contains a link: ' + name)
        require(name.casefold() not in folded, 'case-insensitive path collision: ' + name)
        folded.add(name.casefold())
        if path.is_file():
            files[name] = sha(path)
    require(files, 'empty package')
    return files


def validate_package(root, files):
    expected = {}
    for line in (root / 'MANIFEST.sha256').read_text(encoding='utf-8').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        require(match is not None, 'invalid manifest record')
        digest, name = match.groups(); safe_name(name)
        require(name not in expected, 'duplicate manifest record')
        expected[name] = digest
    require(expected == {n: d for n, d in files.items() if n != 'MANIFEST.sha256'},
            'package manifest does not match the complete payload')
    provenance = json.loads((root / 'candidate-provenance.json').read_text(encoding='utf-8'))
    require(provenance['status'] == 'PASS', 'package staging did not pass')
    for name, record in provenance['inputs'].items():
        require(files.get(name) == record['sha256'], 'staged provenance differs: ' + name)
    required_tools = ['bin/' + n + '.exe' for n in ('sdcc', 'sdcpp', 'sdas251', 'sdas8051',
                      'sdld', 'sdldmcs251', 'packihx', 'makebin', 'sdar', 'sdnm', 'sdobjcopy', 'sdranlib')]
    required_tools.append('libexec/sdcc/cc1.exe')
    require({n for n in files if n.endswith('.exe')} == set(required_tools), 'unexpected Windows tool inventory')
    for name in required_tools:
        require((root / name).read_bytes()[:2] == b'MZ', 'not a Windows executable: ' + name)
    for profile in ('small', 'small-stack-auto', 'medium', 'large', 'large-stack-auto', 'huge',
                    'mcs251-small', 'mcs251-small-stack-auto', 'mcs251-large', 'mcs251-large-stack-auto'):
        target = 'mcs251' if profile.startswith('mcs251-') else 'mcs51'
        for library in ('libsdcc', target, 'libint', 'liblong', 'liblonglong', 'libfloat'):
            require(f'lib/{profile}/{library}.lib' in files, 'missing runtime: ' + profile + '/' + library)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='new artifact directory')
    parser.add_argument('--name', default='sdcc-mcs251-windows-x86_64.zip', help='ZIP filename, not a path')
    parser.add_argument('--epoch', type=int, default=1788134400, help='fixed even UTC epoch for every entry')
    args = parser.parse_args(argv)
    root, out = args.package.resolve(), args.output.resolve()
    if out.is_relative_to(root):
        parser.error('output must be outside the package')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*\.zip', args.name):
        parser.error('name must be a simple .zip filename')
    if not 315532800 <= args.epoch < 4354819200 or args.epoch % 2:
        parser.error('epoch must be an even UTC second in the ZIP range 1980 through 2107')
    out.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': 1, 'status': 'RUNNING', 'production_qualified': False,
              'scope': __doc__, 'package': str(root), 'source_date_epoch': args.epoch,
              'python': platform.python_version(), 'zlib': zlib.ZLIB_RUNTIME_VERSION,
              'started_utc': datetime.now(timezone.utc).isoformat()}

    def save():
        pending = out / 'report.json.part'
        pending.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        pending.replace(out / 'report.json')

    save()
    try:
        inputs = {str(p): sha(p) for p in (Path(__file__).resolve(), Path(sys.executable).resolve())}
        files = inventory(root); validate_package(root, files)
        report.update(inputs=inputs, package_inventory=files); save()
        archive = out / args.name; timestamp = time.gmtime(args.epoch)[:6]
        with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as stream:
            for name in sorted(files):
                data = (root / name).read_bytes()
                require(hashlib.sha256(data).hexdigest() == files[name], 'package changed while archiving: ' + name)
                info = zipfile.ZipInfo('sdcc-mcs251/' + name, timestamp)
                info.create_system = 3; info.external_attr = (stat.S_IFREG | 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                stream.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        with zipfile.ZipFile(archive) as stream:
            require(stream.namelist() == ['sdcc-mcs251/' + n for n in sorted(files)], 'ZIP inventory differs')
            require(stream.testzip() is None and not stream.comment, 'ZIP integrity failure')
            for info in stream.infolist():
                require(info.date_time == timestamp and info.create_system == 3 and
                        info.external_attr == (stat.S_IFREG | 0o644) << 16 and not info.extra and not info.comment,
                        'unexpected ZIP entry metadata')
                require(hashlib.sha256(stream.read(info)).hexdigest() == files[info.filename.removeprefix('sdcc-mcs251/')],
                        'ZIP payload differs')
        require(inventory(root) == files and all(sha(Path(p)) == d for p, d in inputs.items()), 'archive inputs changed')
        report.update(status='PASS', inputs_unchanged=True, archive=str(archive),
                      archive_sha256=sha(archive), archive_size_bytes=archive.stat().st_size)
    except (Exception, SystemExit) as error:
        report.update(status='FAIL', error=str(error))
    finally:
        report['finished_utc'] = datetime.now(timezone.utc).isoformat(); save()
    print(report['status'], report.get('archive_sha256', report.get('error', '')), flush=True)
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
