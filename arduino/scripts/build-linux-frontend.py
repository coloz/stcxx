#!/usr/bin/env python3
"""Build the locked STC Clang/CBE frontend in a fresh native Linux directory.

Run this inside the qualified dependency environment. The result is a build
candidate; runtime packaging, relocation and target regression are separate.
The source worktree and previously built tools are never rewritten.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import subprocess
import sys
import tarfile


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def unpack_source(archive_path, destination, prefix=''):
    """Extract into a new directory, creating links only after regular files.

    Python 3.9 in the Debian baseline lacks tarfile's data filter. No archive
    links are followed while writing; all final links must stay inside the new
    tree. Device entries, hardlinks, duplicate names and traversal are rejected.
    """
    destination = Path(destination).absolute()
    destination.mkdir(parents=True, exist_ok=False)
    # macOS temporary paths commonly traverse /var -> /private/var. Compare
    # resolved links against the same canonical root after exclusive creation.
    destination = destination.resolve(strict=True)
    members, names, links = [], set(), []
    with tarfile.open(archive_path) as archive:
        for item in archive:
            path = PurePosixPath(item.name)
            if path.is_absolute() or '..' in path.parts or '\\' in item.name:
                raise ValueError('unsafe source archive path: ' + item.name)
            parts = path.parts
            if prefix:
                if not parts or parts[0] != prefix:
                    raise ValueError('unexpected source archive root: ' + item.name)
                parts = parts[1:]
            if not parts:
                if not item.isdir(): raise ValueError('non-directory archive root')
                continue
            name = PurePosixPath(*parts).as_posix()
            if name in names or not (item.isdir() or item.isfile() or item.issym()):
                raise ValueError('duplicate or unsupported archive entry: ' + name)
            names.add(name); members.append((item, destination / name))
        for item, path in members:
            if item.issym():
                if PurePosixPath(item.linkname).is_absolute() or '\\' in item.linkname:
                    raise ValueError('absolute or unrepresentable source link: ' + item.name)
                links.append((item, path))
                continue
            if item.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(item) as source, path.open('xb') as target:
                    shutil.copyfileobj(source, target)
                path.chmod(item.mode & 0o777)
        for item, path in links:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(item.linkname)
        for item, path in links:
            resolved = path.resolve()
            if resolved != destination and destination not in resolved.parents:
                raise ValueError('source link escapes extraction root: ' + item.name)


def main(argv=None, *, host='linux', entrypoint=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-root', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--cmake', default='cmake')
    if host == 'darwin':
        parser.add_argument('--llvm-config', default='/opt/homebrew/opt/llvm@20/bin/llvm-config')
        parser.add_argument('--cc', default='/usr/bin/clang')
        parser.add_argument('--cxx', default='/usr/bin/clang++')
        parser.add_argument('--deployment-target', default='15.0')
    args = parser.parse_args(argv)
    machine = {'linux': 'x86_64', 'darwin': 'arm64'}.get(host)
    if machine is None or sys.platform != host or os.uname().machine != machine:
        parser.error('native ' + host + ' ' + str(machine) + ' is required')
    if host == 'darwin' and not re.fullmatch(r'[1-9][0-9]*\.[0-9]+(?:\.[0-9]+)?', args.deployment_target):
        parser.error('--deployment-target must be a macOS version')
    if not args.build_root.is_absolute() or args.build_root.exists() or args.build_root.is_symlink():
        parser.error('--build-root must be an absolute new directory')
    if not 1 <= args.jobs <= 64:
        parser.error('--jobs must be between 1 and 64')
    root = Path(__file__).resolve().parents[2]
    lock_path = root / 'arduino/toolchain-lock.json'
    lock = json.loads(lock_path.read_text())
    inputs = {'source-lock.json': lock_path, 'build-linux-frontend.py': Path(__file__).resolve()}
    if entrypoint is not None:
        inputs[Path(entrypoint).name] = Path(entrypoint).resolve()
    for section, keys in [('clang', ('source_archive', 'cmake_archive', 'patch')), ('llvm_cbe', ('patch',))]:
        for key in keys:
            path = root / lock[section][key]
            expected = lock[section][key + '_sha256']
            if not re.fullmatch('[0-9a-f]{64}', expected) or sha256(path) != expected:
                parser.error('locked input differs: ' + str(path))
            inputs[section + '-' + path.name] = path
    cbe_commit = lock['llvm_cbe']['commit']
    if not re.fullmatch('[0-9a-f]{40}', cbe_commit): parser.error('invalid CBE source commit')
    tools = {}
    requested_tools = {name: name for name in ('gcc', 'g++', 'git', 'patch', 'ninja', 'llvm-config-20', args.cmake)}
    if host == 'darwin':
        requested_tools.update({'gcc': args.cc, 'g++': args.cxx, 'llvm-config-20': args.llvm_config,
                                'xcrun': '/usr/bin/xcrun', 'otool': '/usr/bin/otool'})
    for name, executable in requested_tools.items():
        found = shutil.which(executable)
        if not found: parser.error('missing build tool: ' + name)
        # clang++ may be a symlink to clang: preserve the C++ driver name.
        tools[name] = Path(found).absolute() if host == 'darwin' and name in ('gcc', 'g++') else Path(found).resolve()
    work = args.build_root
    epoch = os.environ.get('SOURCE_DATE_EPOCH', '1788134400')
    if not epoch.isdecimal(): parser.error('SOURCE_DATE_EPOCH must be an unsigned integer')
    os.environ.update(SOURCE_DATE_EPOCH=epoch, LC_ALL='C', TZ='UTC')
    work.mkdir(mode=0o700)
    logs = work / 'logs'; logs.mkdir()
    input_root = work / 'inputs'; input_root.mkdir()
    report = {'status': 'RUNNING', 'scope': 'locked native frontend build candidate; distribution and target qualification remain separate',
              'started_utc': datetime.now(timezone.utc).isoformat(), 'source_date_epoch': int(epoch),
              'source_lock_sha256': sha256(lock_path),
              'inputs': {name: {'path': str(path), 'sha256': sha256(path)} for name, path in inputs.items()},
              'build_tools': {name: {'path': str(path), 'sha256': sha256(path)} for name, path in tools.items()}, 'commands': []}
    report['host'] = {'system': sys.platform, 'machine': os.uname().machine}

    def save(): (work / 'build-report.json').write_text(json.dumps(report, indent=2) + '\n')

    def run(name, command, timeout=120, stdout_path=None):
        log = logs / (name + '.log')
        with log.open('wb') as error_stream:
            output_stream = Path(stdout_path).open('xb') if stdout_path else error_stream
            try:
                process = subprocess.Popen([str(value) for value in command], stdout=output_stream,
                                           stderr=error_stream, start_new_session=True)
                report['active_command'] = {'name': name, 'pid': process.pid}; save()
                try: code = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    try: process.wait(timeout=10)
                    except subprocess.TimeoutExpired: os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=10)
                    raise RuntimeError(name + ' timed out')
            finally:
                if stdout_path: output_stream.close()
        report['commands'].append({'name': name, 'command': [str(value) for value in command],
                                   'exit_code': code, 'log_sha256': sha256(log)})
        report.pop('active_command', None); save()
        if code: raise RuntimeError(name + ' failed: ' + log.read_text(errors='replace')[-6000:])
        print('PASS', name, flush=True)
        return log.read_text()

    save()
    try:
        for name, path in inputs.items():
            shutil.copyfile(path, input_root / name)
            if sha256(input_root / name) != report['inputs'][name]['sha256']:
                raise RuntimeError('input changed while snapshotting: ' + name)
        if run('llvm-version', [tools['llvm-config-20'], '--version']).strip() != '20.1.8':
            raise RuntimeError('LLVM 20.1.8 development tools are required')
        llvm_dir = run('llvm-cmake-directory', [tools['llvm-config-20'], '--cmakedir']).strip()
        report['llvm_cmake_directory'] = llvm_dir
        if host == 'darwin':
            sdk = run('apple-sdk-path', [tools['xcrun'], '--sdk', 'macosx', '--show-sdk-path']).strip()
            sdk_version = run('apple-sdk-version', [tools['xcrun'], '--sdk', 'macosx', '--show-sdk-version']).strip()
            report['apple_sdk'] = {'path': sdk, 'version': sdk_version, 'deployment_target': args.deployment_target}
            # Bind the installed dependency tree, including LLVM headers, CMake
            # files, runtime libraries and Homebrew's receipt, before compiling.
            llvm_prefix = Path(run('llvm-prefix', [tools['llvm-config-20'], '--prefix']).strip()).resolve()
            def llvm_inventory():
                return {path.relative_to(llvm_prefix).as_posix(): sha256(path)
                        for path in sorted(llvm_prefix.rglob('*')) if path.is_file()}
            llvm_before = llvm_inventory()
            (input_root / 'llvm-dependency-manifest.json').write_text(json.dumps(llvm_before, indent=2) + '\n')
            report['llvm_dependency_manifest_sha256'] = sha256(input_root / 'llvm-dependency-manifest.json')
            report['llvm_dependency_file_count'] = len(llvm_before)
        run('compiler-version', [tools['g++'], '--version'])
        run('cmake-version', [tools[args.cmake], '--version'])
        clang_source = work / 'source/clang'; cmake_source = work / 'source/cmake'
        unpack_source(input_root / ('clang-' + Path(lock['clang']['source_archive']).name), clang_source, 'clang-20.1.8.src')
        unpack_source(input_root / ('clang-' + Path(lock['clang']['cmake_archive']).name), cmake_source, 'cmake-20.1.8.src')
        clang_patch = input_root / ('clang-' + Path(lock['clang']['patch']).name)
        run('clang-patch-check', ['patch', '--dry-run', '--batch', '-d', clang_source, '-p1', '-i', clang_patch])
        run('clang-patch', ['patch', '--batch', '-d', clang_source, '-p1', '-i', clang_patch])
        run('clang-reverse-check', ['patch', '--reverse', '--dry-run', '--batch', '-d', clang_source, '-p1', '-i', clang_patch])
        for name, expected in lock['clang']['patched_source_normalized_sha256'].items():
            normalized = (clang_source / name).read_bytes().replace(b'\r\n', b'\n')
            if hashlib.sha256(normalized).hexdigest() != expected: raise RuntimeError('patched Clang source differs: ' + name)
        cbe_repository = root / 'toolchain/llvm-cbe'
        git = ['git', '-c', 'safe.directory=' + str(cbe_repository), '-C', cbe_repository]
        observed = run('cbe-commit', git + ['rev-parse', cbe_commit + '^{commit}']).strip()
        if observed != cbe_commit: raise RuntimeError('CBE commit mismatch')
        cbe_archive = input_root / 'llvm-cbe-base.tar'
        run('cbe-archive', git + ['archive', '--format=tar', cbe_commit], stdout_path=cbe_archive)
        report['cbe_base_archive_sha256'] = sha256(cbe_archive)
        cbe_source = work / 'source/llvm-cbe'
        unpack_source(cbe_archive, cbe_source)
        cbe_patch = input_root / ('llvm_cbe-' + Path(lock['llvm_cbe']['patch']).name)
        run('cbe-patch-check', ['patch', '--dry-run', '--batch', '-d', cbe_source, '-p1', '-i', cbe_patch])
        run('cbe-patch', ['patch', '--batch', '-d', cbe_source, '-p1', '-i', cbe_patch])
        run('cbe-reverse-check', ['patch', '--reverse', '--dry-run', '--batch', '-d', cbe_source, '-p1', '-i', cbe_patch])
        for filename, key in [('CBackend.cpp', 'patched_cbackend_normalized_sha256'), ('CBackend.h', 'patched_cbackend_header_normalized_sha256')]:
            normalized = (cbe_source / 'lib/Target/CBackend' / filename).read_bytes().replace(b'\r\n', b'\n')
            if hashlib.sha256(normalized).hexdigest() != lock['llvm_cbe'][key]: raise RuntimeError('patched CBE source differs: ' + filename)
        cmake = str(tools[args.cmake])
        common = ['-G', 'Ninja', '-DLLVM_DIR=' + llvm_dir, '-DCMAKE_BUILD_TYPE=Release',
                  '-DCMAKE_C_COMPILER=' + str(tools['gcc']), '-DCMAKE_CXX_COMPILER=' + str(tools['g++']),
                  '-DCMAKE_C_FLAGS=-ffile-prefix-map=' + str(work) + '=.',
                  '-DCMAKE_CXX_FLAGS=-ffile-prefix-map=' + str(work) + '=.',
                  '-DLLVM_PARALLEL_LINK_JOBS=1', '-DLLVM_LINK_LLVM_DYLIB=ON']
        if host == 'darwin':
            common.extend(['-DCMAKE_OSX_SYSROOT=' + sdk,
                           '-DCMAKE_OSX_DEPLOYMENT_TARGET=' + args.deployment_target,
                           '-DCMAKE_OSX_ARCHITECTURES=arm64'])
        run('configure-clang', [cmake, '-S', clang_source, '-B', work / 'clang', *common,
                               '-DCLANG_LINK_CLANG_DYLIB=ON', '-DLLVM_INCLUDE_TESTS=OFF', '-DCLANG_INCLUDE_TESTS=OFF',
                               '-DCLANG_INCLUDE_DOCS=OFF', '-DCLANG_ENABLE_STATIC_ANALYZER=OFF', '-DCLANG_ENABLE_ARCMT=OFF'])
        run('build-clang', [cmake, '--build', work / 'clang', '--target', 'clang', '--parallel', str(args.jobs)], timeout=7200)
        run('configure-cbe', [cmake, '-S', cbe_source, '-B', work / 'llvm-cbe', *common])
        run('build-cbe', [cmake, '--build', work / 'llvm-cbe', '--target', 'llvm-cbe', '--parallel', str(args.jobs)], timeout=7200)
        artifacts = {'clang': work / 'clang/bin/clang', 'libclang_cpp': work / 'clang/lib/libclang-cpp.so.20.1',
                     'llvm_cbe': work / 'llvm-cbe/tools/llvm-cbe/llvm-cbe'}
        if host == 'darwin':
            libraries = {path.resolve() for path in (work / 'clang/lib').glob('libclang-cpp*.dylib') if path.is_file()}
            if len(libraries) != 1: raise RuntimeError('expected one built libclang-cpp Mach-O library')
            artifacts['libclang_cpp'] = libraries.pop()
            for name, path in artifacts.items():
                run('macho-dependencies-' + name, [tools['otool'], '-L', path])
        report['artifacts'] = {name: {'path': str(path), 'sha256': sha256(path), 'bytes': path.stat().st_size} for name, path in artifacts.items()}
        run('built-clang-version', [artifacts['clang'], '--version'])
        run('built-cbe-version', [artifacts['llvm_cbe'], '--version'])
        for name, row in report['inputs'].items():
            if sha256(Path(row['path'])) != row['sha256'] or sha256(input_root / name) != row['sha256']:
                raise RuntimeError('source input changed: ' + name)
        for row in report['build_tools'].values():
            if sha256(Path(row['path'])) != row['sha256']: raise RuntimeError('build tool changed: ' + row['path'])
        if host == 'darwin':
            report['llvm_dependency_unchanged'] = llvm_inventory() == llvm_before
            if not report['llvm_dependency_unchanged']: raise RuntimeError('LLVM dependency changed during build')
        report['status'] = 'PASS'
    except (Exception, KeyboardInterrupt) as error:
        report.update(status='FAIL', error=str(error) or type(error).__name__)
        print(report['error'], file=sys.stderr, flush=True)
    finally:
        report['finished_utc'] = datetime.now(timezone.utc).isoformat(); save()
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
