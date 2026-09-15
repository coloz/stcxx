#!/usr/bin/env python3
"""Verify a relocated Mac frontend's complete manifest, load paths and ABI.

Optional sandbox denials prove it can execute without reading original build
and Homebrew dependency directories. This does not qualify Arduino or an MCU.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

scripts = Path(__file__).resolve().parent
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module
native = load('native_frontend_probes', scripts / 'verify-macos-frontend-build.py')
macho = load('frontend_macho', scripts / 'macho_frontend.py')
inventory, sha256 = native.abi.package.inventory, native.sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--deny-input-prefix', type=Path, action='append', default=[])
    args = parser.parse_args()
    if sys.platform != 'darwin' or os.uname().machine != 'arm64': parser.error('native ARM64 Mac required')
    root = args.package.resolve(); output = args.output
    if not output.is_absolute() or output.exists() or output.is_symlink(): parser.error('output must be an absolute new directory')
    if root == output.resolve() or root in output.resolve().parents: parser.error('output must be outside the package')
    manifest = root / 'MANIFEST.sha256'
    if sha256(manifest) != args.manifest_sha256: parser.error('manifest differs from expected package identity')
    observed = inventory(root); expected = {}
    for line in manifest.read_text().splitlines():
        digest, name = line.split('  ', 1)
        if name in expected or name not in observed or observed[name]['sha256'] != digest: parser.error('manifest mismatch: ' + name)
        expected[name] = digest
    if set(expected) != set(observed) - {'MANIFEST.sha256'}: parser.error('manifest does not cover exactly the package')
    provenance = json.loads((root / 'candidate-provenance.json').read_text())
    if provenance['status'] != 'PASS' or provenance['architecture'] != 'arm64': parser.error('package provenance did not pass')
    lock_path = scripts.parent / 'toolchain-lock.json'
    if sha256(lock_path) != provenance['source_lock_sha256']: parser.error('verifier source lock differs from package')
    output.mkdir(); (output / 'logs').mkdir()
    inputs = {str(p): sha256(p) for p in [Path(__file__), scripts / 'verify-macos-frontend-build.py', scripts / 'verify-linux-frontend.py',
              scripts / 'package-linux-frontend.py', scripts / 'macho_frontend.py', scripts / 'check-cbe-return-identity.py', lock_path]}
    denied = list(dict.fromkeys(str(p.resolve()) for p in args.deny_input_prefix))
    if any(root == Path(p) or Path(p) in root.parents for p in denied): parser.error('sandbox deny prefixes include the package')
    sandbox = []
    if denied:
        profile = '(version 1)(allow default)' + ''.join('(deny file-read* (subpath ' + json.dumps(p) + '))' for p in denied)
        (output / 'sandbox.sb').write_text(profile + '\n')
        sandbox = ['/usr/bin/sandbox-exec', '-p', profile]
    env = {k: v for k, v in os.environ.items() if not k.startswith(('DYLD_', 'LD_')) and k not in ('CPATH', 'CPLUS_INCLUDE_PATH', 'C_INCLUDE_PATH', 'SDKROOT', 'MACOSX_DEPLOYMENT_TARGET')}
    env.update(LC_ALL='C', TZ='UTC', PATH='/usr/bin:/bin:/usr/sbin:/sbin', PYTHONDONTWRITEBYTECODE='1')
    report = {'status': 'RUNNING', 'production_qualified': False, 'scope': __doc__, 'package_root': str(root),
              'manifest_sha256': args.manifest_sha256, 'inputs': inputs, 'denied_source_prefixes': denied, 'commands': [], 'profiles': {}}
    def save(): (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    def run(name, command, failure=None):
        executed = [*sandbox, *map(str, command)]
        p = subprocess.run(executed, env=env, capture_output=True, text=True, timeout=120)
        log = output / 'logs' / (name + '.log'); log.write_text(p.stdout + p.stderr)
        report['commands'].append({'name': name, 'command': list(map(str, command)), 'sandbox_applied': bool(sandbox), 'exit_code': p.returncode, 'log_sha256': sha256(log)}); save()
        if failure is None and p.returncode: raise RuntimeError(name + ' failed: ' + (p.stdout + p.stderr)[-3000:])
        if failure is not None and (not p.returncode or failure not in p.stdout + p.stderr): raise RuntimeError(name + ' did not reject as expected')
        return p.stdout
    save()
    try:
        # Show the denial is active, rather than only recording an intended rule.
        for i, path in enumerate(denied):
            sentinel = Path(path) / 'README.md'
            if not sentinel.is_file():
                candidates = [p for p in Path(path).iterdir() if p.is_file()]
                if not candidates: raise RuntimeError('no readable input sentinel for sandbox verification')
                sentinel = candidates[0]
            with sentinel.open('rb') as stream: stream.read(1)
            result = subprocess.run([*sandbox, '/bin/cat', str(sentinel)], env=env, capture_output=True, timeout=30)
            (output / 'logs' / ('deny-check-' + str(i) + '.log')).write_bytes(result.stderr)
            if result.returncode == 0 or result.stdout: raise RuntimeError('sandbox did not deny source access')
        binaries = [*sorted((root / 'bin').iterdir()), *sorted(p for p in (root / 'lib').iterdir() if p.is_file())]
        for i, path in enumerate(binaries):
            if run(str(i) + '-arch', ['/usr/bin/lipo', '-archs', path]).strip() != 'arm64': raise RuntimeError('non-ARM64 file in bundle')
            run(str(i) + '-signature', ['/usr/bin/codesign', '--verify', '--strict', path])
            record = macho.parse_load_commands(run(str(i) + '-loads', ['/usr/bin/otool', '-l', path]))
            if record['rpaths']: raise RuntimeError('package retains dynamic rpath search paths')
            for name in record['dependencies']:
                resolved = macho.resolve_dependency(name, path, [])
                if resolved is not None and resolved.parent != root / 'lib': raise RuntimeError('dependency escapes relocated lib directory')
        for name in ('clang', 'llvm-link', 'opt', 'llvm-dis', 'llvm-cbe'): run(name + '-version', [root / 'bin' / name, '--version'])
        resource = Path(run('resource', [root / 'bin/clang', '-print-resource-dir']).strip()).resolve()
        if root not in resource.parents or not (resource / 'include/stddef.h').is_file(): raise RuntimeError('resource headers escaped bundle')
        native.exercise_abi(root / 'bin/clang', root / 'bin/llvm-cbe', root / 'bin', resource, output, run, report)
        if inventory(root) != observed or any(sha256(Path(p)) != h for p, h in inputs.items()): raise RuntimeError('verification inputs changed')
        report.update(status='PASS_NATIVE_RELOCATED_FRONTEND', inputs_unchanged=True)
    except Exception as error: report.update(status='FAIL', error=str(error))
    save(); print(report['status'], report.get('error', ''))
    return 0 if report['status'] == 'PASS_NATIVE_RELOCATED_FRONTEND' else 1


if __name__ == '__main__': sys.exit(main())
