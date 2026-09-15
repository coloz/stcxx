#!/usr/bin/env python3
"""Stage an ARM64 Mac frontend with private, relocatable Mach-O dependencies.

Only copies are rewritten and ad-hoc signed. This is not Developer ID signing,
notarization, corresponding-source distribution or Arduino/MCU qualification.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

scripts = Path(__file__).resolve().parent
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module
common = load('frontend_package_common', scripts / 'package-linux-frontend.py')
macho = load('frontend_macho', scripts / 'macho_frontend.py')
sha256, inventory = common.sha256, common.inventory


def canonical_build_paths(value, build_root):
    """Keep the build identity while moving its absolute directory to the audit.

    Match a complete path prefix, including the separator in prefix-map flags.
    Other inputs, compiler flags and hashes remain part of the package identity.
    """
    pattern = re.compile(re.escape(str(build_root)) + r'(?=$|/|=)')
    def canonical(item):
        if isinstance(item, str):
            return pattern.sub(lambda _: '${BUILD_ROOT}', item)
        if isinstance(item, list):
            return [canonical(child) for child in item]
        if isinstance(item, dict):
            result = {}
            for key, child in item.items():
                name = canonical(key)
                if name in result:
                    raise ValueError('build path normalization produced duplicate keys')
                result[name] = canonical(child)
            return result
        return item
    return canonical(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--zstd-prefix', type=Path, default=Path('/opt/homebrew/opt/zstd'))
    args = parser.parse_args()
    if sys.platform != 'darwin' or os.uname().machine != 'arm64': parser.error('native ARM64 Mac required')
    build_root = args.build.resolve(); root = args.output
    if not root.is_absolute() or root.exists() or root.is_symlink(): parser.error('output must be an absolute new directory')
    if root.resolve() == build_root or build_root in root.resolve().parents: parser.error('output must be outside build inputs')
    build_path = build_root / 'build-report.json'; build = json.loads(build_path.read_text()); build_hash = sha256(build_path)
    if build['status'] != 'PASS' or build['host'] != {'system': 'darwin', 'machine': 'arm64'}: parser.error('native build has not passed')
    llvm = Path(build['llvm_cmake_directory']).parents[2].resolve(); zstd = args.zstd_prefix.resolve()
    if any(root.resolve() == p or p in root.resolve().parents for p in (llvm, zstd)):
        parser.error('output must be outside dependency inputs')
    dependency_manifest = build_root / 'inputs/llvm-dependency-manifest.json'
    if sha256(dependency_manifest) != build['llvm_dependency_manifest_sha256']: parser.error('LLVM manifest differs')
    expected_llvm = json.loads(dependency_manifest.read_text())
    def llvm_inventory(): return {p.relative_to(llvm).as_posix(): sha256(p) for p in llvm.rglob('*') if p.is_file()}
    if llvm_inventory() != expected_llvm: parser.error('LLVM dependency changed since native build')
    snapshot = {name: row['sha256'] for name, row in build['inputs'].items()}
    snapshot.update({'llvm-cbe-base.tar': build['cbe_base_archive_sha256'], 'llvm-dependency-manifest.json': build['llvm_dependency_manifest_sha256']})
    def verify_snapshot():
        if set(p.name for p in (build_root / 'inputs').iterdir()) != set(snapshot): raise ValueError('unexpected build input files')
        for name, expected in snapshot.items():
            path = build_root / 'inputs' / name
            if Path(name).name != name or path.is_symlink() or sha256(path) != expected: raise ValueError('build input changed: ' + name)
    verify_snapshot()
    artifacts = {name: Path(row['path']).resolve() for name, row in build['artifacts'].items()}
    for name, path in artifacts.items():
        if sha256(path) != build['artifacts'][name]['sha256']: parser.error('built artifact changed: ' + name)
    tools = {'clang': artifacts['clang'], 'llvm-cbe': artifacts['llvm_cbe'],
             **{name: (llvm / 'bin' / name).resolve() for name in ('llvm-link', 'opt', 'llvm-dis')}}
    audit = root.with_name(root.name + '.audit.json')
    if audit.exists() or audit.is_symlink(): parser.error('existing audit destination')
    root.mkdir(); root = root.resolve()
    env = {k: v for k, v in os.environ.items() if not k.startswith(('DYLD_', 'LD_'))}
    env.update(LC_ALL='C', TZ='UTC', PATH='/usr/bin:/bin:/usr/sbin:/sbin')
    report = {'status': 'RUNNING', 'production_qualified': False, 'scope': __doc__, 'minimum_macos': '15.0',
              'architecture': 'arm64', 'source_lock_sha256': build['source_lock_sha256'], 'source_date_epoch': build['source_date_epoch'],
              'source_snapshot': snapshot, 'packager_sha256': sha256(Path(__file__)), 'original_inputs': {},
              'libraries': {}, 'tools': {}, 'native_files': {}, 'signature': 'ad-hoc, no timestamp; not Developer ID or notarization'}
    commands = []
    def run(command):
        result = subprocess.run(list(map(str, command)), env=env, capture_output=True, text=True, timeout=120)
        commands.append({'command': list(map(str, command)), 'exit_code': result.returncode, 'output': result.stdout + result.stderr})
        if result.returncode: raise RuntimeError(str(command[0]) + ' failed: ' + (result.stdout + result.stderr)[-4000:])
        return result.stdout.strip()
    def inspect(path): return macho.parse_load_commands(run(['/usr/bin/otool', '-l', path]))
    try:
        # Resolve each actual load command. A library's LC_ID_DYLIB is its
        # identity, not a dependency; only load commands enter this graph.
        graph = {}; queue = list(tools.values()); libraries = {}
        while queue:
            path = queue.pop(0)
            if path in graph: continue
            record = inspect(path); bindings = {}
            for name in record['dependencies']:
                resolved = macho.resolve_dependency(name, path, record['rpaths'])
                bindings[name] = resolved
                if resolved is None: continue
                if resolved != artifacts['libclang_cpp'] and llvm not in resolved.parents and zstd not in resolved.parents:
                    raise RuntimeError('dependency is outside recorded build/Homebrew inputs: ' + str(resolved))
                filename = resolved.name
                if filename in libraries and libraries[filename] != resolved: raise RuntimeError('ambiguous library filename: ' + filename)
                libraries[filename] = resolved; queue.append(resolved)
            graph[path] = {'load_commands': record, 'bindings': bindings}
        clang_bindings = set(graph[tools['clang']]['bindings'].values())
        if artifacts['libclang_cpp'] not in clang_bindings: raise RuntimeError('Clang does not load its source-built implementation')
        if (llvm / 'lib/libLLVM.dylib').resolve() not in libraries.values(): raise RuntimeError('LLVM implementation is absent')
        report['tools'] = {name: {'source': str(path), 'source_sha256': sha256(path)} for name, path in tools.items()}
        report['libraries'] = {name: {'source': str(path), 'source_sha256': sha256(path)} for name, path in libraries.items()}
        for name in ('bin', 'lib', 'licenses', 'build-inputs'): (root / name).mkdir()
        destinations = {}
        for name, source in tools.items(): destinations[source] = root / 'bin' / name
        for name, source in libraries.items(): destinations[source] = root / 'lib' / name
        for source, target in destinations.items():
            report['original_inputs'][str(source)] = sha256(source)
            shutil.copyfile(source, target); target.chmod(0o755)
        for source, target in destinations.items():
            record = graph[source]['load_commands']; arguments = ['/usr/bin/install_name_tool']
            for name, dependency in graph[source]['bindings'].items():
                if dependency is None: continue
                relative = ('@loader_path/../lib/' if target.parent == root / 'bin' else '@loader_path/') + dependency.name
                arguments.extend(['-change', name, relative])
            for rpath in dict.fromkeys(record['rpaths']): arguments.extend(['-delete_rpath', rpath])
            if record['install_name'] is not None: arguments.extend(['-id', '@loader_path/' + target.name])
            if len(arguments) > 1: run([*arguments, target])
            run(['/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none', '--identifier', 'org.stcxx.frontend.' + target.name, target])
            run(['/usr/bin/codesign', '--verify', '--strict', target])
            if run(['/usr/bin/lipo', '-archs', target]) != 'arm64': raise RuntimeError('wrong package architecture')
            version = run(['/usr/bin/vtool', '-show-build', target])
            versions = re.findall(r'\bminos\s+([0-9.]+)', version)
            if not versions or any(tuple(map(int, v.split('.'))) > (15, 0, 0) for v in versions): raise RuntimeError('dependency exceeds macOS 15 baseline')
            final = inspect(target)
            if final['rpaths']: raise RuntimeError('packaged file retains rpath search directories')
            for name in final['dependencies']:
                dependency = macho.resolve_dependency(name, target, [])
                if dependency is not None and dependency.parent != root / 'lib': raise RuntimeError('packaged dependency escapes private lib directory')
            name = target.relative_to(root).as_posix()
            report['native_files'][name] = {'source': str(source), 'source_sha256': sha256(source), 'sha256': sha256(target),
                                           'load_commands': final, 'minimum_macos_versions': versions}
        resource = Path(run([tools['clang'], '-print-resource-dir'])).resolve()
        if build_root not in resource.parents or not (resource / 'include/stddef.h').is_file(): raise RuntimeError('resource headers escape source build')
        resource_before = inventory(resource)
        shutil.copytree(resource, root / 'lib/clang' / resource.name, symlinks=True)
        if inventory(root / 'lib/clang' / resource.name) != resource_before: raise RuntimeError('resource header copy differs')
        notices = {'clang.txt': build_root / 'source/clang/LICENSE.TXT', 'llvm-cbe.txt': build_root / 'source/llvm-cbe/LICENSE',
                   'llvm.txt': llvm / 'LICENSE.TXT', 'llvm-support.txt': llvm / 'include/llvm/Support/LICENSE.TXT',
                   'zstd-LICENSE': zstd / 'LICENSE', 'zstd-COPYING': zstd / 'COPYING'}
        for name, source in notices.items():
            if not source.is_file() or not source.stat().st_size: raise RuntimeError('missing dependency notice: ' + str(source))
            report['original_inputs'][str(source)] = sha256(source); shutil.copyfile(source, root / 'licenses' / name)
        for name, prefix in [('llvm', llvm), ('zstd', zstd)]:
            for filename in ('INSTALL_RECEIPT.json', 'sbom.spdx.json'):
                source = prefix / filename; report['original_inputs'][str(source)] = sha256(source)
                shutil.copyfile(source, root / 'build-inputs' / (name + '-' + filename))
        for path in (build_root / 'inputs').iterdir():
            if path.suffix not in ('.xz', '.tar'): shutil.copyfile(path, root / 'build-inputs' / path.name)
        recipe = {**common.build_recipe(build), 'host': build['host'], 'apple_sdk': build['apple_sdk'],
                  'llvm_dependency_manifest_sha256': build['llvm_dependency_manifest_sha256']}
        (root / 'build-inputs/build-recipe.json').write_text(json.dumps(canonical_build_paths(recipe, build_root), indent=2, sort_keys=True) + '\n')
        for name in ('package-macos-frontend.py', 'macho_frontend.py', 'package-linux-frontend.py'):
            path = scripts / name; report['original_inputs'][str(path)] = sha256(path); shutil.copyfile(path, root / 'build-inputs' / name)
        for path in root.rglob('*'):
            if path.is_symlink(): continue
            if path.is_dir(): path.chmod(0o755)
            elif path.parent not in (root / 'bin', root / 'lib'): path.chmod(0o644)
        report['tool_versions'] = {name: run([root / 'bin' / name, '--version']).splitlines()[0] for name in tools}
        if Path(run([root / 'bin/clang', '-print-resource-dir'])).resolve() != root / 'lib/clang' / resource.name:
            raise RuntimeError('packaged Clang resource directory escapes package')
        verify_snapshot()
        if llvm_inventory() != expected_llvm or sha256(build_path) != build_hash: raise RuntimeError('native build/dependency input changed')
        if inventory(resource) != resource_before: raise RuntimeError('original resource headers changed')
        if any(sha256(Path(p)) != h for p, h in report['original_inputs'].items()): raise RuntimeError('original packaging input changed')
        report['payload'] = inventory(root); report['status'] = 'PASS'
    except Exception as error:
        report.update(status='FAIL', error=str(error)); print(report['error'], file=sys.stderr)
    report['build_path_placeholder'] = '${BUILD_ROOT}; actual build directory retained in the separate audit'
    provenance = root / 'candidate-provenance.json'; provenance.write_text(json.dumps(canonical_build_paths(report, build_root), indent=2, sort_keys=True) + '\n'); provenance.chmod(0o644)
    if report['status'] == 'PASS':
        files = inventory(root); manifest = root / 'MANIFEST.sha256'
        manifest.write_text(''.join(row['sha256'] + '  ' + name + '\n' for name, row in files.items())); manifest.chmod(0o644)
        print('MACOS_FRONTEND_FILES=' + str(len(files)))
    with audit.open('x') as stream:
        json.dump({'status': report['status'], 'build_root': str(build_root), 'build_report_sha256': build_hash, 'build_report': build, 'commands': commands,
                   'package_manifest_sha256': sha256(root / 'MANIFEST.sha256') if report['status'] == 'PASS' else None}, stream, indent=2); stream.write('\n')
    print(report['status']); return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__': sys.exit(main())
