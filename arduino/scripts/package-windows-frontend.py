#!/usr/bin/env python3
"""Package a native Windows STC Clang/LLVM-CBE frontend and embedded Python."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import subprocess
import tarfile
import zipfile

PYTHON_SHA256 = '4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3'
LLVM_SHA256 = 'f229769f11d6a6edc8ada599c0cda964b7dee6ab1a08c6cf9dd7f513e85b107f'
TOOLS = ('clang', 'llvm-link', 'opt', 'llvm-dis', 'llvm-cbe')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('build-root', 'llvm-root', 'llvm-archive', 'python-archive', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    for name, value in vars(args).items():
        setattr(args, name, value.resolve())
    source = Path(__file__).resolve().parents[2]
    report = json.loads((args.build_root / 'build-report.json').read_text(encoding='utf-8'))
    require(report['status'] == 'PASS' and report['host'] == 'windows-x86_64', 'native build did not pass')
    require(digest(args.python_archive) == PYTHON_SHA256, 'Python archive SHA-256 mismatch')
    require(digest(args.llvm_archive) == LLVM_SHA256, 'LLVM development archive SHA-256 mismatch')
    args.output.mkdir(parents=True, exist_ok=True)
    root = args.output / 'stcxx-frontend'
    require(not root.exists(), 'package staging already exists')
    (root / 'bin').mkdir(parents=True)
    for name in TOOLS:
        if name in report['artifacts']:
            row = report['artifacts'][name]
            path = Path(row['path'])
            require(digest(path) == row['sha256'], 'built artifact changed: ' + name)
        else:
            path = args.llvm_root / 'bin' / (name + '.exe')
        shutil.copyfile(path, root / 'bin' / (name + '.exe'))
    shutil.copytree(args.llvm_root / 'lib/clang/20/include', root / 'lib/clang/20/include')
    python = root / 'python'
    python.mkdir()
    with zipfile.ZipFile(args.python_archive) as archive:
        for info in archive.infolist():
            path = PurePosixPath(info.filename)
            require(len(path.parts) == 1 and not path.is_absolute() and '\\' not in info.filename and ':' not in info.filename,
                    'unsafe embedded Python member')
            (python / info.filename).write_bytes(archive.read(info))
    # The interpreter uses only its private standard library. Driver helpers
    # explicitly load their adjacent modules; no system site-packages are used.
    write = lambda path, text: Path(path).write_text(text, encoding='utf-8', newline='\n')
    licenses = root / 'licenses'
    licenses.mkdir()
    shutil.copyfile(source / 'toolchain/llvm-project/LICENSE.TXT', licenses / 'LLVM.txt')
    cbe_licenses = list((source / 'toolchain/llvm-cbe').glob('*LICENSE*'))
    require(cbe_licenses, 'missing LLVM-CBE license')
    shutil.copyfile(cbe_licenses[0], licenses / 'LLVM-CBE.txt')
    shutil.copyfile(python / 'LICENSE.txt', licenses / 'Python.txt')
    inputs = root / 'build-inputs'
    inputs.mkdir()
    for path in (Path(__file__), Path(__file__).with_name('build-windows-frontend.py'),
                 source / 'arduino/toolchain-lock.json', args.build_root / 'build-report.json'):
        shutil.copyfile(path, inputs / path.name)
    # Resolve imports without relying on the packaging machine's PATH. The
    # allow-list consists of Windows system libraries; everything else must
    # be bundled alongside the executable that imports it.
    system = {'kernel32.dll', 'advapi32.dll', 'ntdll.dll', 'shell32.dll', 'ole32.dll',
              'oleaut32.dll', 'user32.dll', 'ws2_32.dll', 'bcrypt.dll', 'crypt32.dll',
              'rpcrt4.dll', 'version.dll', 'shlwapi.dll', 'psapi.dll', 'secur32.dll',
              'userenv.dll', 'iphlpapi.dll', 'normaliz.dll', 'ucrtbase.dll', 'msvcrt.dll',
              'winmm.dll', 'cabinet.dll', 'propsys.dll', 'msi.dll'}
    imports = {}
    reader = args.llvm_root / 'bin/llvm-readobj.exe'
    for directory in (root / 'bin', python):
        names = {p.name.lower() for p in directory.iterdir()}
        for path in directory.iterdir():
            if path.suffix.lower() not in ('.exe', '.dll', '.pyd'):
                continue
            with path.open('rb') as stream:
                require(stream.read(2) == b'MZ', 'not a native PE image: ' + str(path))
                stream.seek(60)
                offset = struct.unpack('<I', stream.read(4))[0]
                stream.seek(offset)
                require(stream.read(6) == b'PE\0\0d\x86', 'not Windows x64: ' + str(path))
            text = subprocess.check_output([str(reader), '--coff-imports', str(path)], text=True)
            dependencies = re.findall(r'^  Name: (.+)$', text, re.M)
            for dll in dependencies:
                name = dll.lower()
                require(name in names or name in system or name.startswith(('api-ms-win-', 'ext-ms-win-')),
                        'unbundled native dependency: ' + path.name + ' -> ' + dll)
            imports[path.relative_to(root).as_posix()] = dependencies
    provenance = {'host': 'windows-x86_64', 'tools': TOOLS, 'imports': imports,
                  'build_report_sha256': digest(args.build_root / 'build-report.json'),
                  'llvm_archive_sha256': LLVM_SHA256, 'python_archive_sha256': PYTHON_SHA256,
                  'python_version': '3.12.10', 'execution': 'native PE x64; no WSL or POSIX shell'}
    write(root / 'candidate-provenance.json', json.dumps(provenance, indent=2) + '\n')
    files = {p.relative_to(root).as_posix(): digest(p) for p in sorted(root.rglob('*')) if p.is_file()}
    write(root / 'MANIFEST.sha256', ''.join(value + '  ' + name + '\n' for name, value in files.items()))
    archive_path = args.output / 'stcxx-frontend-20.1.8-windows-x86_64-r1.tar.bz2'
    with tarfile.open(archive_path, 'w:bz2', format=tarfile.USTAR_FORMAT) as archive:
        for path in [root, *sorted(root.rglob('*'))]:
            info = archive.gettarinfo(str(path), arcname='stcxx-frontend' + ('/' + path.relative_to(root).as_posix() if path != root else ''))
            info.uid = info.gid = 0
            info.uname = info.gname = 'root'
            info.mtime = 1788134400
            info.mode = 0o755 if path.is_dir() or path.suffix in ('.exe', '.dll', '.pyd') else 0o644
            if path.is_file():
                with path.open('rb') as stream:
                    archive.addfile(info, stream)
            else:
                archive.addfile(info)
    result = {'host': 'windows-x86_64', 'archive': str(archive_path), 'archive_sha256': digest(archive_path),
              'manifest_sha256': digest(root / 'MANIFEST.sha256'),
              'bootstrap_files': {name: value for name, value in files.items() if name.startswith('python/')},
              'tools': {name.replace('-', '_'): {'sha256': digest(root / 'bin' / (name + '.exe'))} for name in TOOLS}}
    write(args.output / 'windows-frontend-package.json', json.dumps(result, indent=2) + '\n')
    print('PASS:', archive_path)


if __name__ == '__main__':
    main()
