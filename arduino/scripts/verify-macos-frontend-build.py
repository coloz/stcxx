#!/usr/bin/env python3
"""Exercise a source-built native Mac frontend; this is not package relocation
or Arduino/MCU qualification. Reuse the Linux verifier's exact ABI probe.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

scripts = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('frontend_abi_probe', scripts / 'verify-linux-frontend.py')
abi = importlib.util.module_from_spec(spec); spec.loader.exec_module(abi)
sha256 = abi.package.sha256


def exercise_abi(clang, cbe, llvm_bin, resource, output, run, report):
    """Run the same native ABI/CBE probes for build and relocated package tools."""
    source = output / 'probe.cpp'; source.write_text(abi.PROBE)
    second = output / 'second.cpp'; second.write_text('extern "C" unsigned int extra(unsigned int x) { return x + 3; }\n')
    for profile, (triple, layout) in abi.PROFILES.items():
        rows = []
        for level in ('0', 'z'):
            work = output / (profile + '-O' + level); work.mkdir(); prefix = work.name + '-'
            flags = [clang, '--target=' + triple, '-std=gnu++11', '-O' + level, '-ffreestanding', '-funsigned-char',
                     '-fno-exceptions', '-fno-rtti', '-fno-vectorize', '-fno-slp-vectorize', '-nostdinc', '-isystem',
                     resource / 'include', '-Xclang', '-disable-O0-optnone']
            for stem, path in [('probe', source), ('second', second)]:
                run(prefix + stem, [*flags, '-emit-llvm', '-c', path, '-o', work / (stem + '.bc')])
            run(prefix + 'link', [llvm_bin / 'llvm-link', work / 'probe.bc', work / 'second.bc', '-o', work / 'linked.bc'])
            run(prefix + 'opt', [llvm_bin / 'opt', '-passes=mem2reg', work / 'linked.bc', '-o', work / 'optimized.bc'])
            run(prefix + 'dis', [llvm_bin / 'llvm-dis', work / 'optimized.bc', '-o', work / 'optimized.ll'])
            ir = (work / 'optimized.ll').read_text()
            if 'target triple = "' + triple + '"' not in ir or 'target datalayout = "' + layout + '"' not in ir or 'sret(' not in ir:
                raise RuntimeError('STC ABI identity or caller-owned result storage changed')
            run(prefix + 'cbe', [cbe, work / 'optimized.bc', '-o', work / 'module.c'])
            if 'callback_identity' not in (work / 'module.c').read_text(): raise RuntimeError('CBE callback probe missing')
            rows.append({'optimization': level, 'ir_sha256': sha256(work / 'optimized.ll'), 'c_sha256': sha256(work / 'module.c')})
        run(profile + '-reject-object', [clang, '--target=' + triple, '-c', second, '-o', output / (profile + '.o')], 'assembly/object emission is unsupported')
        report['profiles'][profile] = rows
    stock = output / 'stock.cpp'
    stock.write_text('#ifdef __STC_CLANG_IR_ONLY__\n#error STC profile leaked\n#endif\nstatic_assert(sizeof(void*) == 2, "stock MSP430");\nint x;\n')
    run('stock-msp430', [clang, '--target=msp430-none-none-elf', '-emit-llvm', '-c', stock, '-o', output / 'stock.bc'])
    run('return-identity', [sys.executable, '-B', scripts / 'check-cbe-return-identity.py', '--cbe', cbe, '--cc', '/usr/bin/clang', '--work', output / 'return-identity'])
    identity_path = output / 'return-identity/return-identity.json'; identity = json.loads(identity_path.read_text())
    if identity['status'] != 'PASS' or len(identity['checks']) != 4: raise RuntimeError('incomplete return-identity execution')
    report['return_identity'] = identity; report['return_identity_sha256'] = sha256(identity_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'darwin' or os.uname().machine != 'arm64': parser.error('native ARM64 Mac required')
    build = args.build.resolve(); output = args.output
    if not output.is_absolute() or output.exists() or output.is_symlink(): parser.error('output must be an absolute new directory')
    if build == output.resolve() or build in output.resolve().parents: parser.error('output must be outside build inputs')
    build_report = build / 'build-report.json'; built = json.loads(build_report.read_text())
    if built['status'] != 'PASS' or built['host'] != {'system': 'darwin', 'machine': 'arm64'}: parser.error('native build did not pass')
    if sha256(scripts.parent / 'toolchain-lock.json') != built['source_lock_sha256']: parser.error('verifier source lock differs from native build')
    artifacts = {name: Path(row['path']) for name, row in built['artifacts'].items()}
    for name, path in artifacts.items():
        if sha256(path) != built['artifacts'][name]['sha256']: parser.error('built artifact changed: ' + name)
    llvm = Path(built['llvm_cmake_directory']).parents[2]
    manifest = build / 'inputs/llvm-dependency-manifest.json'
    if sha256(manifest) != built['llvm_dependency_manifest_sha256']: parser.error('LLVM manifest changed')
    expected = json.loads(manifest.read_text())
    def inventory(): return {p.relative_to(llvm).as_posix(): sha256(p) for p in llvm.rglob('*') if p.is_file()}
    if inventory() != expected: parser.error('LLVM dependency changed')
    output.mkdir(); (output / 'logs').mkdir()
    inputs = {str(p): sha256(p) for p in [build_report, manifest, Path(__file__), scripts / 'verify-linux-frontend.py',
              scripts / 'package-linux-frontend.py', scripts / 'check-cbe-return-identity.py', scripts.parent / 'toolchain-lock.json', *artifacts.values()]}
    report = {'status': 'RUNNING', 'production_qualified': False, 'scope': __doc__, 'inputs': inputs, 'commands': [], 'profiles': {}}
    env = {k: v for k, v in os.environ.items() if not k.startswith(('DYLD_', 'LD_')) and k not in ('CPATH', 'CPLUS_INCLUDE_PATH', 'C_INCLUDE_PATH', 'SDKROOT', 'MACOSX_DEPLOYMENT_TARGET')}
    env.update(LC_ALL='C', TZ='UTC', PYTHONDONTWRITEBYTECODE='1', PATH='/usr/bin:/bin:/usr/sbin:/sbin')
    def save(): (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    def run(name, command, failure=None):
        p = subprocess.run([str(x) for x in command], env=env, capture_output=True, text=True, timeout=120)
        log = output / 'logs' / (name + '.log'); log.write_text(p.stdout + p.stderr)
        report['commands'].append({'name': name, 'command': list(map(str, command)), 'exit_code': p.returncode, 'log_sha256': sha256(log)}); save()
        if failure is None and p.returncode: raise RuntimeError(name + ' failed: ' + (p.stdout + p.stderr)[-3000:])
        if failure is not None and (not p.returncode or failure not in p.stdout + p.stderr): raise RuntimeError(name + ' failed to reject as expected')
        return p.stdout
    save()
    try:
        clang = artifacts['clang']; cbe = artifacts['llvm_cbe']
        resource = Path(run('resource', [clang, '-print-resource-dir']).strip()).resolve()
        if build not in resource.parents or not (resource / 'include/stddef.h').is_file(): raise RuntimeError('headers escaped native build')
        exercise_abi(clang, cbe, llvm / 'bin', resource, output, run, report)
        if any(sha256(Path(p)) != h for p, h in inputs.items()) or inventory() != expected: raise RuntimeError('verification inputs changed')
        report.update(status='PASS_NATIVE_FRONTEND_IR_HOST_EXECUTION', inputs_unchanged=True)
    except Exception as error: report.update(status='FAIL', error=str(error))
    save(); print(report['status'], report.get('error', ''))
    return 0 if report['status'] == 'PASS_NATIVE_FRONTEND_IR_HOST_EXECUTION' else 1


if __name__ == '__main__': sys.exit(main())
