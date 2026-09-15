#!/usr/bin/env python3
"""Stage a Linux STC frontend with its actual non-glibc shared dependencies.

Run in the same dependency environment as build-linux-frontend.py. Input tools
remain untouched. The new directory is a candidate for relocation/ABI testing.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

CORE_LIBRARIES = {'ld-linux-x86-64.so.2', 'libc.so.6', 'libm.so.6', 'libdl.so.2',
                  'libpthread.so.0', 'librt.so.1'}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''): digest.update(chunk)
    return digest.hexdigest()


def run(command):
    result = subprocess.run([str(value) for value in command], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError(str(command[0]) + ' failed: ' + result.stdout[-6000:])
    return result.stdout.strip()


def verify_build_inputs(build_root, build):
    """Bind every retained source snapshot to the successful build report."""
    inputs = build_root / 'inputs'
    expected = {name: row['sha256'] for name, row in build['inputs'].items()}
    expected['llvm-cbe-base.tar'] = build['cbe_base_archive_sha256']
    if expected.get('source-lock.json') != build['source_lock_sha256']:
        raise ValueError('source lock identity is inconsistent')
    if set(path.name for path in inputs.iterdir()) != set(expected):
        raise ValueError('build input snapshot contains missing or unexpected entries')
    for name, digest in expected.items():
        if Path(name).name != name or '\\' in name or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('invalid build input identity: ' + name)
        path = inputs / name
        if path.is_symlink() or not path.is_file() or sha256(path) != digest:
            raise ValueError('build input snapshot changed: ' + name)
    return expected


def build_recipe(build):
    """Keep reproducible build identities apart from per-run audit details."""
    return {
        'status': build['status'], 'source_date_epoch': build['source_date_epoch'],
        'source_lock_sha256': build['source_lock_sha256'], 'inputs': build['inputs'],
        'build_tools': build['build_tools'], 'llvm_cmake_directory': build['llvm_cmake_directory'],
        'cbe_base_archive_sha256': build['cbe_base_archive_sha256'], 'artifacts': build['artifacts'],
        'commands': [{'name': row['name'], 'command': row['command'], 'exit_code': row['exit_code']}
                     for row in build['commands']],
    }


def parse_dependencies(text):
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('linux-vdso.so.1 '): continue
        if '=>' in line:
            name, path = line.split('=>', 1)
            name = name.strip(); path = re.sub(r'\s+\(0x[0-9a-fA-F]+\)$', '', path.strip())
        else:
            path = re.sub(r'\s+\(0x[0-9a-fA-F]+\)$', '', line)
            name = Path(path).name
        if not re.fullmatch('[A-Za-z0-9_.+-]+', name) or not Path(path).is_absolute() or name in result:
            raise ValueError('unresolved or ambiguous dynamic dependency: ' + line)
        result[name] = Path(path)
    if not result: raise ValueError('dynamic dependency listing is empty')
    return result


def dependencies(path, allow_leaf_library=False, runner=run):
    listing = runner(['ldd', path])
    if allow_leaf_library and listing.strip() == 'statically linked':
        # Data-only DSOs such as ICU's data tables have no DT_NEEDED entries.
        # Accept that only after independently inspecting the ELF metadata.
        dynamic = runner(['readelf', '-d', path])
        header = runner(['readelf', '-h', path])
        if re.search(r'Type:\s+DYN\b', header) and '(SONAME)' in dynamic and '(NEEDED)' not in dynamic:
            return {}
    try: return parse_dependencies(listing)
    except ValueError as error: raise ValueError(str(path) + ': ' + str(error)) from error


def inventory(root):
    result = {}; root = Path(root).resolve()
    for path in sorted(root.rglob('*')):
        name = path.relative_to(root).as_posix()
        if any(c in name for c in ('\n', '\r', '\\')): raise ValueError('unrepresentable package filename')
        if path.is_symlink():
            if Path(os.readlink(path)).is_absolute() or root not in path.resolve().parents or not path.is_file():
                raise ValueError('unsafe package link: ' + name)
        elif path.is_dir(): continue
        elif not path.is_file(): raise ValueError('unsupported package entry: ' + name)
        result[name] = {'sha256': sha256(path), 'bytes': path.stat().st_size,
                        'mode': path.lstat().st_mode & 0o777, 'link': os.readlink(path) if path.is_symlink() else None}
    return result


def owner(path):
    for candidate in dict.fromkeys((str(path), str(Path(path).resolve()))):
        result = subprocess.run(['dpkg-query', '-S', candidate], capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            owners = {line.partition(': ')[0] for line in result.stdout.splitlines() if ': ' in line}
            if len(owners) == 1: return owners.pop()
    raise RuntimeError('cannot identify Debian dependency owner: ' + str(path))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--patchelf', type=Path, required=True)
    parser.add_argument('--patchelf-sha256', required=True)
    args = parser.parse_args(argv)
    if sys.platform != 'linux' or os.uname().machine != 'x86_64': parser.error('native Linux x86_64 required')
    if not args.output.is_absolute() or args.output.exists() or args.output.is_symlink():
        parser.error('--output must be an absolute new directory')
    attestation = args.output.with_name(args.output.name + '.build-attestation.json')
    if attestation.exists() or attestation.is_symlink(): parser.error('build attestation already exists')
    if not re.fullmatch('[0-9a-f]{64}', args.patchelf_sha256) or sha256(args.patchelf) != args.patchelf_sha256:
        parser.error('RPATH editor hash mismatch')
    build_root = args.build_root.resolve()
    build_path = build_root / 'build-report.json'; build = json.loads(build_path.read_text())
    if build['status'] != 'PASS': parser.error('frontend build did not pass')
    build_hash = sha256(build_path)
    recipe_text = json.dumps(build_recipe(build), indent=2) + '\n'
    recipe_hash = hashlib.sha256(recipe_text.encode()).hexdigest()
    input_hashes = verify_build_inputs(build_root, build)
    for row in build['artifacts'].values():
        if build_root not in Path(row['path']).resolve().parents: parser.error('artifact is outside build root')
        if sha256(Path(row['path'])) != row['sha256']: parser.error('built frontend artifact changed: ' + row['path'])
    llvm_config = Path(build['build_tools']['llvm-config-20']['path'])
    if sha256(llvm_config) != build['build_tools']['llvm-config-20']['sha256']: parser.error('LLVM environment changed')
    llvm_bin = llvm_config.resolve().parent
    sources = {'clang': Path(build['artifacts']['clang']['path']), 'llvm-cbe': Path(build['artifacts']['llvm_cbe']['path']),
               'llvm-link': llvm_bin / 'llvm-link', 'opt': llvm_bin / 'opt', 'llvm-dis': llvm_bin / 'llvm-dis'}
    expected_clang_library = build['artifacts']['libclang_cpp']['sha256']
    root = args.output.resolve(); root.mkdir(mode=0o755)
    report = {'status': 'RUNNING', 'production_qualified': False, 'scope': 'relocatable frontend package candidate; target and clean-host qualification separate',
              'build_recipe_sha256': recipe_hash, 'source_date_epoch': build['source_date_epoch'],
              'packager_sha256': sha256(Path(__file__)), 'patchelf_sha256': args.patchelf_sha256,
              'source_input_sha256': input_hashes,
              'original_inputs': {}, 'libraries': {}, 'core_libraries': {}, 'dependency_packages': {}}
    try:
        libraries = {}; package_owners = set()
        for name, executable in sources.items():
            digest = sha256(executable); report['original_inputs'][str(executable)] = digest
            resolved = dependencies(executable)
            if 'libLLVM.so.20.1' not in resolved: raise RuntimeError('missing LLVM 20.1 implementation: ' + name)
            if name == 'clang' and ('libclang-cpp.so.20.1' not in resolved or sha256(resolved['libclang-cpp.so.20.1']) != expected_clang_library):
                raise RuntimeError('Clang is not using its freshly built shared implementation')
            for soname, path in resolved.items():
                library_hash = sha256(path)
                record = {'source': str(path), 'sha256_before_packaging': library_hash}
                if soname in CORE_LIBRARIES:
                    report['core_libraries'][soname] = record
                    continue
                if soname in libraries and sha256(libraries[soname]) != library_hash:
                    raise RuntimeError('inconsistent shared implementations: ' + soname)
                libraries[soname] = path
                if soname == 'libclang-cpp.so.20.1': record['source_kind'] = 'locked STC Clang build'
                else:
                    record['package'] = owner(path); package_owners.add(record['package'])
                report['libraries'][soname] = record
        for name in ('llvm-link', 'opt', 'llvm-dis'): package_owners.add(owner(sources[name]))
        for directory in ('bin', 'lib', 'licenses', 'build-inputs'): (root / directory).mkdir()
        for name, source in sources.items():
            target = root / 'bin' / name; shutil.copyfile(source, target); target.chmod(0o755)
            run([args.patchelf, '--set-rpath', '$ORIGIN/../lib', target])
        for soname, source in libraries.items():
            target = root / 'lib' / soname; shutil.copyfile(source, target); target.chmod(0o755)
            run([args.patchelf, '--set-rpath', '$ORIGIN', target])
        resource = Path(run([sources['clang'], '-print-resource-dir'])).resolve()
        if build_root not in resource.parents or not (resource / 'include/stddef.h').is_file():
            raise RuntimeError('Clang resource headers are missing or outside the build')
        shutil.copytree(resource, root / 'lib/clang' / resource.name, symlinks=True)
        for component, path in [('clang', build_root / 'source/clang/LICENSE.TXT'), ('llvm-cbe', build_root / 'source/llvm-cbe/LICENSE')]:
            shutil.copyfile(path, root / 'licenses' / (component + '.txt'))
        for package in sorted(package_owners):
            path = Path('/usr/share/doc') / package.split(':')[0] / 'copyright'
            if not path.is_file(): raise RuntimeError('dependency copyright missing: ' + package)
            shutil.copyfile(path, root / 'licenses' / (package.replace(':', '_') + '.copyright'))
            report['dependency_packages'][package] = run(['dpkg-query', '-W', '-f=${Version}', package])
        common = root / 'licenses/common'; common.mkdir()
        for path in Path('/usr/share/common-licenses').iterdir():
            if path.is_file(): shutil.copyfile(path, common / path.name)
        (root / 'build-inputs/build-recipe.json').write_text(recipe_text)
        for path in sorted((build_root / 'inputs').iterdir()):
            if not path.is_file(): raise RuntimeError('unexpected build input directory')
            # Full source archives stay in the separately retained source materials.
            if path.suffix not in ('.xz', '.tar'): shutil.copyfile(path, root / 'build-inputs' / path.name)
        shutil.copyfile(Path(__file__), root / 'build-inputs/package-linux-frontend.py')
        for path in root.rglob('*'):
            if path.is_symlink(): continue
            if path.is_dir(): path.chmod(0o755)
            elif path.parent not in (root / 'bin', root / 'lib'): path.chmod(0o644)
        elf_files = [*sorted((root / 'bin').iterdir()), *sorted(p for p in (root / 'lib').iterdir() if p.is_file())]
        observed_glibc, observed_glibcxx = [], []
        for path in elf_files:
            versions = run(['readelf', '--version-info', path])
            if 'GLIBC_PRIVATE' in versions: raise RuntimeError('package depends on private glibc ABI: ' + str(path))
            for prefix, observed in [('GLIBC', observed_glibc), ('GLIBCXX', observed_glibcxx)]:
                observed.extend(tuple(map(int, version.split('.'))) for version in re.findall(prefix + r'_([0-9]+(?:\.[0-9]+)+)', versions))
            expected_rpath = '$ORIGIN/../lib' if path.parent == root / 'bin' else '$ORIGIN'
            if run([args.patchelf, '--print-rpath', path]) != expected_rpath: raise RuntimeError('incorrect RPATH')
            for soname, dependency in dependencies(path, allow_leaf_library=path.parent == root / 'lib').items():
                if soname not in CORE_LIBRARIES and root not in dependency.resolve().parents:
                    raise RuntimeError('unbundled dependency after relocation: ' + str(dependency))
        if max(observed_glibc, default=(0,)) > (2, 31) or max(observed_glibcxx, default=(0,)) > (3, 4, 28):
            raise RuntimeError('frontend exceeds the GLIBC 2.31 / GLIBCXX 3.4.28 distribution floor')
        report['maximum_symbol_versions'] = {'GLIBC': max(observed_glibc, default=(0,)), 'GLIBCXX': max(observed_glibcxx, default=(0,))}
        report['tool_versions'] = {name: run([root / 'bin' / name, '--version']).splitlines()[0] for name in sources}
        if Path(run([root / 'bin/clang', '-print-resource-dir'])).resolve() != root / 'lib/clang' / resource.name:
            raise RuntimeError('packaged Clang resolves resource headers outside the package')
        for path, expected in report['original_inputs'].items():
            if sha256(Path(path)) != expected: raise RuntimeError('original tool changed during packaging')
        for row in [*report['libraries'].values(), *report['core_libraries'].values()]:
            if sha256(Path(row['source'])) != row['sha256_before_packaging']: raise RuntimeError('original dependency changed during packaging')
        verify_build_inputs(build_root, build)
        if sha256(build_path) != build_hash or sha256(args.patchelf) != args.patchelf_sha256:
            raise RuntimeError('packaging inputs changed')
        report['payload'] = inventory(root); report['status'] = 'PASS'
    except Exception as error:
        report.update(status='FAIL', error=str(error))
        print(report['error'], file=sys.stderr)
    (root / 'candidate-provenance.json').write_text(json.dumps(report, indent=2) + '\n')
    (root / 'candidate-provenance.json').chmod(0o644)
    if report['status'] == 'PASS':
        files = inventory(root)
        (root / 'MANIFEST.sha256').write_text(''.join(row['sha256'] + '  ' + name + '\n' for name, row in files.items()))
        (root / 'MANIFEST.sha256').chmod(0o644)
        print('FRONTEND_PACKAGE_FILES=' + str(len(files)), flush=True)
    # Timestamps, PIDs and parallel-build log hashes are audit evidence, but do
    # not belong inside a byte-reproducible distribution archive.
    audit = {'status': report['status'], 'build_report_sha256': build_hash, 'build_report': build,
             'build_recipe_sha256': recipe_hash, 'packager_sha256': report['packager_sha256'],
             'package_manifest_sha256': sha256(root / 'MANIFEST.sha256') if report['status'] == 'PASS' else None}
    with attestation.open('x') as stream: stream.write(json.dumps(audit, indent=2) + '\n')
    attestation.chmod(0o644)
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__': sys.exit(main())
