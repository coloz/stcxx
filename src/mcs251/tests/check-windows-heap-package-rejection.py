#!/usr/bin/env python3
"""Verify that the Windows heap execution gate rejects damaged compile evidence.

Run in WSL after a successful native Windows compile phase. Each negative case
uses a new report/artifact copy; the original package and firmware stay intact.
All subprocesses run with Python -O and must reject before starting QEMU.
"""
import argparse
from datetime import datetime, timezone
import copy
import hashlib
import importlib.util
import json
from pathlib import Path, PureWindowsPath
import re
import subprocess
import sys

sys.dont_write_bytecode = True


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiled', type=Path, required=True)
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--drive-root', type=Path, default=Path('/mnt'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    compiled_path = args.compiled.resolve()
    gate = Path(__file__).with_name('check-windows-heap-package.py').resolve()
    spec = importlib.util.spec_from_file_location('windows_heap_gate', gate)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    try:
        compiled = json.loads(compiled_path.read_text(encoding='utf-8'))
        input_package = module.windows_path(compiled['package'], args.drive_root).resolve()
    except (Exception, SystemExit) as error:
        parser.error('cannot establish input package location: ' + str(error))
    out = args.output.resolve()
    if out.is_relative_to(input_package):
        parser.error('output must be outside the package')
    out.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': 1, 'status': 'RUNNING', 'production_qualified': False,
              'scope': __doc__, 'results': [], 'started_utc': datetime.now(timezone.utc).isoformat()}

    def save():
        pending = out / 'report.json.part'
        pending.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        pending.replace(out / 'report.json')

    save()
    try:
        require(sys.platform.startswith('linux'), 'run in WSL')
        require(compiled['status'] == 'PASS_COMPILE' and compiled['inputs_unchanged'] and
                compiled['checker_sha256'] == sha(gate), 'need a complete current native compile report')
        module.verify_compile_matrix(compiled)

        def local(path):
            return module.windows_path(path, args.drive_root)

        def windows(path):
            parts = path.resolve().relative_to(args.drive_root.resolve()).parts
            require(len(parts) > 1 and re.fullmatch(r'[a-z]', parts[0]), 'output must be on a mounted Windows drive')
            return str(PureWindowsPath(parts[0].upper() + ':\\', *parts[1:]))

        windows(out)
        package = local(compiled['package']).resolve()
        require(not out.is_relative_to(package), 'output must be outside the package')
        package_files = module.inventory(package)
        require(package_files == compiled['package_inventory'], 'original package already changed')
        module.verify_manifest(package, package_files)
        module.verify_files(compiled['inputs'], local)
        artifacts = {}
        for row in (*compiled['results'], *compiled['mcs51_compile_only']):
            image = local(row['firmware'])
            artifacts[str(image)] = row['firmware_sha256']
            artifacts[str(image.with_suffix('.map'))] = row['map_sha256']
        module.verify_files(artifacts)
        report['inputs'] = {str(p): sha(p) for p in (Path(__file__).resolve(), gate, compiled_path,
                                                    args.qemu.resolve(), Path(sys.executable).resolve())}
        report['package_inventory'] = package_files
        report['original_artifacts'] = artifacts
        cases = [('missing-mcs251-case', 'incomplete or duplicate MCS251 matrix'),
                 ('duplicate-mcs251-case', 'incomplete or duplicate MCS251 matrix'),
                 ('missing-mcs51-model', 'incomplete or duplicate MCS51 compile-only matrix'),
                 ('changed-firmware', 'input changed:'), ('changed-map', 'input changed:'),
                 ('changed-package-identity', 'Windows package changed'),
                 ('changed-checker-identity', 'compile and execute checkers differ')]
        for name, expected_error in cases:
            case = out / name; case.mkdir()
            altered = copy.deepcopy(compiled)
            if name == 'missing-mcs251-case':
                altered['results'].pop()
            elif name == 'duplicate-mcs251-case':
                altered['results'][-1] = copy.deepcopy(altered['results'][0])
            elif name == 'missing-mcs51-model':
                altered['mcs51_compile_only'].pop()
            elif name in ('changed-firmware', 'changed-map'):
                row = altered['results'][0]; original = local(row['firmware'])
                image = case / 'copied firmware.hex'; mp = image.with_suffix('.map')
                image.write_bytes(original.read_bytes()); mp.write_bytes(original.with_suffix('.map').read_bytes())
                damaged = image if name == 'changed-firmware' else mp
                damaged.write_bytes(damaged.read_bytes() + b'\nchanged after compilation\n')
                row['firmware'] = windows(image)  # Keep the original digest to expose the changed copy.
            elif name == 'changed-package-identity':
                altered['package_inventory']['bin/sdcc.exe'] = '0' * 64
            elif name == 'changed-checker-identity':
                altered['checker_sha256'] = '0' * 64
            fixture = case / 'compiled.json'
            fixture.write_text(json.dumps(altered, indent=2) + '\n', encoding='utf-8')
            destination = case / 'execution'
            command = list(map(str, [sys.executable, '-O', gate, 'execute', '--compiled', fixture,
                                     '--qemu', args.qemu.resolve(), '--drive-root', args.drive_root.resolve(),
                                     '--output', destination]))
            stdout, stderr = case / 'stdout.log', case / 'stderr.log'
            with stdout.open('wb') as sout, stderr.open('wb') as serr:
                result = subprocess.run(command, stdout=sout, stderr=serr, timeout=180)
            child_path = destination / 'report.json'
            child = json.loads(child_path.read_text(encoding='utf-8'))
            row = {'name': name, 'status': 'FAIL', 'command': command, 'exit_code': result.returncode,
                   'altered_report_sha256': sha(fixture), 'report_sha256': sha(child_path),
                   'stdout_sha256': sha(stdout), 'stderr_sha256': sha(stderr), 'error': child.get('error')}
            report['results'].append(row); save()
            require(result.returncode != 0 and child['status'] == 'FAIL' and
                    expected_error in child.get('error', '') and not child['results'] and
                    not list(destination.rglob('*-qemu.log')), 'wrong rejection: ' + name)
            row.update(status='PASS_REJECTION', qemu_not_started=True)
            save(); print('PASS_REJECTION', name, flush=True)
        module.verify_files(report['inputs']); module.verify_files(artifacts)
        module.verify_files(compiled['inputs'], local)
        require(module.inventory(package) == package_files, 'original package changed during negative tests')
        report.update(status='PASS', inputs_unchanged=True)
    except (Exception, SystemExit) as error:
        report.update(status='FAIL', error=str(error))
    finally:
        report['finished_utc'] = datetime.now(timezone.utc).isoformat(); save()
    print(report['status'], report.get('error', ''), flush=True)
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
