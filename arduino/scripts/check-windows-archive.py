#!/usr/bin/env python3
"""Check deterministic Windows archiving and reject damaged staging inputs.

Uses a private package copy. The original package and reference ZIP are read
only. Rejections must happen before creating any ZIP, including under Python -O.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

sys.dont_write_bytecode = True


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--reference-archive', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    original, reference, out = args.package.resolve(), args.reference_archive.resolve(), args.output.resolve()
    if out.is_relative_to(original):
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
        archiver = Path(__file__).with_name('archive-windows-sdcc.py').resolve()
        spec = importlib.util.spec_from_file_location('windows_archiver', archiver)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        files = module.inventory(original); module.validate_package(original, files)
        with zipfile.ZipFile(reference) as stream:
            require(stream.namelist() == ['sdcc-mcs251/' + n for n in sorted(files)] and
                    stream.testzip() is None, 'reference ZIP inventory or CRC differs')
            for name, digest in files.items():
                require(hashlib.sha256(stream.read('sdcc-mcs251/' + name)).hexdigest() == digest,
                        'reference ZIP differs from package')
        inputs = {str(p): sha(p) for p in (Path(__file__).resolve(), archiver,
                                          Path(sys.executable).resolve(), reference)}
        report.update(inputs=inputs, original_package=str(original), package_inventory=files)
        package = out / 'private package'; shutil.copytree(original, package)
        require(module.inventory(package) == files, 'private copy differs')
        provenance_path = package / 'candidate-provenance.json'
        manifest_path = package / 'MANIFEST.sha256'
        before = {n: (package / n).read_bytes() for n in ('include/stdarg.h', 'candidate-provenance.json',
                  'MANIFEST.sha256', 'lib/medium/libsdcc.lib', 'lib/huge/libsdcc.lib')}

        def rebuild_manifest():
            inventory = module.inventory(package)
            manifest_path.write_text(''.join(f'{d}  {n}\n' for n, d in sorted(inventory.items())
                                             if n != 'MANIFEST.sha256'), encoding='utf-8', newline='\n')

        cases = [('changed-header', 'package manifest does not match'),
                 ('unlisted-file', 'package manifest does not match'),
                 ('duplicate-manifest-record', 'duplicate manifest record'),
                 ('incomplete-staging', 'package staging did not pass'),
                 ('missing-medium', 'missing runtime: medium/libsdcc'),
                 ('missing-huge', 'missing runtime: huge/libsdcc')]
        for name, error in cases:
            case = out / name; case.mkdir()
            try:
                if name == 'changed-header':
                    (package / 'include/stdarg.h').write_bytes(before['include/stdarg.h'] + b'\nchanged\n')
                elif name == 'unlisted-file':
                    (package / 'unlisted.txt').write_bytes(b'not in the manifest\n')
                elif name == 'duplicate-manifest-record':
                    manifest_path.write_bytes(before['MANIFEST.sha256'] + before['MANIFEST.sha256'].splitlines()[0] + b'\n')
                else:
                    provenance = json.loads(before['candidate-provenance.json'])
                    if name == 'incomplete-staging':
                        provenance['status'] = 'RUNNING'
                    else:
                        member = 'lib/' + name.removeprefix('missing-') + '/libsdcc.lib'
                        (package / member).unlink()
                        del provenance['inputs'][member]
                    provenance_path.write_text(json.dumps(provenance, indent=2) + '\n', encoding='utf-8', newline='\n')
                    rebuild_manifest()
                destination = case / 'archive'
                command = list(map(str, [sys.executable, '-O', archiver, '--package', package, '--output', destination]))
                stdout, stderr = case / 'stdout.log', case / 'stderr.log'
                with stdout.open('wb') as sout, stderr.open('wb') as serr:
                    result = subprocess.run(command, stdout=sout, stderr=serr, timeout=180)
                child_path = destination / 'report.json'
                child = json.loads(child_path.read_text(encoding='utf-8'))
                row = {'name': name, 'status': 'FAIL', 'command': command, 'exit_code': result.returncode,
                       'report_sha256': sha(child_path), 'stdout_sha256': sha(stdout), 'stderr_sha256': sha(stderr),
                       'error': child.get('error')}
                report['results'].append(row); save()
                require(result.returncode != 0 and child['status'] == 'FAIL' and error in child.get('error', '') and
                        not list(destination.glob('*.zip')), 'archiver did not reject correctly: ' + name)
                row.update(status='PASS_REJECTION', archive_absent=True)
                save(); print('PASS_REJECTION', name, flush=True)
            finally:
                for n, data in before.items():
                    (package / n).write_bytes(data)
                extra = package / 'unlisted.txt'
                if extra.exists():
                    extra.unlink()
            require(module.inventory(package) == files, 'private package was not restored')
        # Change only filesystem timestamps; the ZIP must retain fixed metadata.
        for name in files:
            os.utime(package / name, (946684800, 946684800))
        destination = out / 'different-filesystem-timestamps'
        command = list(map(str, [sys.executable, '-O', archiver, '--package', package, '--output', destination]))
        log = out / 'different-filesystem-timestamps.log'
        with log.open('wb') as stream:
            result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=180)
        positive_path = destination / 'report.json'; positive = json.loads(positive_path.read_text(encoding='utf-8'))
        require(result.returncode == 0 and positive['status'] == 'PASS' and
                positive['archive_sha256'] == sha(reference), 'filesystem timestamps changed ZIP bytes')
        report['timestamp_independence'] = {'status': 'PASS', 'command': command, 'log_sha256': sha(log),
                                          'report_sha256': sha(positive_path), 'archive_sha256': sha(reference)}
        require(module.inventory(original) == files and module.inventory(package) == files and
                all(sha(Path(p)) == d for p, d in inputs.items()), 'original input or private payload changed')
        report.update(status='PASS', inputs_unchanged=True)
    except (Exception, SystemExit) as error:
        report.update(status='FAIL', error=str(error))
    finally:
        report['finished_utc'] = datetime.now(timezone.utc).isoformat(); save()
    print(report['status'], report.get('error', ''), flush=True)
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
