#!/usr/bin/env python3
"""Execute multiplication from an installed SDCC package, without source overrides.

The source-runtime checker supplies the fixed Python reference vectors only.
Each firmware links the installed liblong.lib through the compiler's normal
library search. An optional reference package audits every archive member.
"""
import argparse
from contextlib import redirect_stderr
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(root):
    return {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo')}


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--toolchain', type=Path, required=True)
    parser.add_argument('--reference-toolchain', type=Path)
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='new, nonexisting evidence directory')
    args = parser.parse_args(argv)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {'status': 'RUNNING', 'production_qualified': False, 'scope': __doc__,
              'started_utc': datetime.now(timezone.utc).isoformat(), 'results': [], 'commands': []}
    roots = {}
    inputs = {}

    def save():
        (out/'report.json.part').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
        (out/'report.json.part').replace(out/'report.json')

    def run(case, label, command, commands):
        command = list(map(str, command))
        item = {'name': label, 'command': command}
        commands.append(item)
        log_out, log_err = case/(label+'.stdout'), case/(label+'.stderr')
        try:
            with log_out.open('wb') as stdout, log_err.open('wb') as stderr:
                result = subprocess.run(command, cwd=case, stdout=stdout, stderr=stderr,
                                        env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'HOME': str(out)}, timeout=120)
            item['exit_code'] = result.returncode
            require(result.returncode == 0, label+': '+log_err.read_text(errors='replace')[-2500:])
            return log_out.read_bytes()
        except subprocess.TimeoutExpired:
            item['error'] = 'timeout after 120 seconds'
            raise
        finally:
            item['stdout_sha256'], item['stderr_sha256'] = sha(log_out), sha(log_err)

    def archive_members(root, model, case, label, commands):
        archive = root/'lib'/model/'liblong.lib'
        names = run(case, label+'-list', [root/'bin/sdar', '-t', archive], commands).decode().splitlines()
        require(names and len(names) == len(set(names)) and '_mullong.rel' in names, 'invalid liblong member inventory')
        require(all(re.fullmatch(r'[A-Za-z0-9_.-]+\.rel', name) for name in names), 'unsafe archive member name')
        members = {}
        for name in names:
            content = run(case, label+'-'+name, [root/'bin/sdar', '-p', archive, name], commands)
            members[name] = hashlib.sha256(content).hexdigest()
        return {'path': str(archive), 'sha256': sha(archive), 'members': members}

    save()
    try:
        require(sys.platform.startswith('linux'), 'run on Linux or WSL')
        current = args.toolchain.resolve()
        reference = args.reference_toolchain.resolve() if args.reference_toolchain else None
        require(reference != current, 'reference package must be separate')
        for root in (current, reference):
            if root is None:
                continue
            require(all((root/name).is_dir() for name in ('bin', 'include', 'lib')), 'incomplete package: '+str(root))
            require((root/'bin/sdcc').resolve().is_relative_to(root), 'compiler escapes package')
            roots[str(root)] = inventory(root)
        report['package_inventories'] = roots
        qemu = args.qemu.resolve()
        folder = Path(__file__).resolve().parent
        source_checker = folder/'check-mullong-runtime.py'
        qemu_checker = folder/'check-qemu.py'
        inputs = {str(p): sha(p) for p in (Path(__file__).resolve(), Path(sys.executable).resolve(),
                  source_checker, qemu_checker, folder/'qemu_trace.py', qemu)}
        report['input_files_sha256'] = inputs
        fixtures = load_module('mullong_source', source_checker)
        execution = load_module('mullong_qemu', qemu_checker)
        machine = execution.resolve_machine(qemu, None)
        rows = fixtures.vectors()
        fixture = out/'multiply.c'
        fixture.write_text(fixtures.fixture_source(rows), encoding='utf-8')
        inputs[str(fixture)] = sha(fixture)
        report.update(vectors_per_firmware=len(rows), comparisons_per_firmware=2*len(rows), qemu_machine=machine)
        run(out, 'search-paths', [current/'bin/sdcc', '--print-search-dirs'], report['commands'])
        save()
        for model in ('small', 'large'):
            for stack_auto in (False, True):
                name = 'mcs251-'+model+('-stack-auto' if stack_auto else '')
                case = out/name
                case.mkdir()
                row = {'status': 'RUNNING', 'model': name, 'commands': []}
                try:
                    archived = archive_members(current, name, case, 'candidate', row['commands'])
                    row['archive'] = archived
                    rel = (case/'candidate-_mullong.rel.stdout').read_text()
                    require(re.search(r'^S __mullong Def[0-9A-F]+$', rel, re.M), 'runtime does not define multiplication')
                    require(not re.search(r'^S __mullong Ref', rel, re.M), 'recursive runtime dependency')
                    area = re.findall(r'^A CSEG size ([0-9A-F]+)', rel, re.M)
                    require(len(area) == 1, 'missing runtime code area')
                    row['runtime_cseg_bytes'] = int(area[0], 16)
                    if reference:
                        previous = archive_members(reference, name, case, 'reference', row['commands'])
                        row['reference_archive'] = previous
                        require(archived['members'].keys() == previous['members'].keys(), 'archive membership changed')
                        changed = [n for n in archived['members'] if archived['members'][n] != previous['members'][n]]
                        require(changed == (['_mullong.rel'] if stack_auto or model == 'large' else []),
                                'unexpected changed arithmetic members: '+str(changed))
                        row['changed_members'] = changed
                    image = case/'multiply.hex'
                    flags = ['-mmcs251', '--model-'+model, '--std-sdcc11', '--no-xinit-opt', '--code-loc', '0xff0000']
                    if stack_auto:
                        flags.append('--stack-auto')
                    # No library/object overrides: qualification concerns normal
                    # lookup of the package's own installed arithmetic runtime.
                    run(case, 'build', [current/'bin/sdcc', *flags, fixture, '-o', image], row['commands'])
                    assembly = image.with_suffix('.asm').read_text()
                    require(re.search(r'^\s*ecall\s+__mullong\s*$', assembly, re.M), 'fixture did not call runtime')
                    link_map = image.with_suffix('.map')
                    map_text = link_map.read_text()
                    library_members = re.findall(r'^(.+\.lib)\n\s*\[\s*([^\]\n]+?)\s*\]\s*$', map_text, re.M)
                    multiplication = [Path(path).resolve() for path, member in library_members if member.strip() == '_mullong.rel']
                    require(multiplication == [(current/'lib'/name/'liblong.lib').resolve()],
                            'link map does not identify exactly one installed multiplication member')
                    require(all(Path(path).resolve().is_relative_to(current/'lib'/name) for path, _ in library_members),
                            'link map selected a library outside the installed model directory')
                    row['linked_library_members'] = [{'archive': str(Path(path).resolve()), 'member': member.strip()}
                                                     for path, member in library_members]
                    # Bind executable inputs before execution, including failed
                    # target runs for which run_qemu raises instead of returning.
                    row.update(firmware=str(image), firmware_sha256=sha(image), map_sha256=sha(link_map))
                    diagnostics = case/'qemu-diagnostics.log'
                    row['qemu_inputs'] = {'helper': str(qemu_checker), 'qemu': str(qemu), 'machine': machine, 'firmware': str(image)}
                    try:
                        with diagnostics.open('w', encoding='utf-8') as stream, redirect_stderr(stream):
                            uart = execution.run_qemu(qemu, machine, image)
                    finally:
                        row['qemu_diagnostics_sha256'] = sha(diagnostics)
                    (case/'uart.log').write_bytes(uart)
                    require(uart == b'PASS\n', 'unexpected target UART')
                    row.update(status='PASS', uart_sha256=sha(case/'uart.log'))
                except (Exception, SystemExit) as error:
                    row.update(status='FAIL', error=str(error))
                report['results'].append(row)
                save()
                print(row['status'], name, row.get('runtime_cseg_bytes'), row.get('error', ''), flush=True)
        require(len(report['results']) == 4 and all(row['status'] == 'PASS' for row in report['results']), 'incomplete/failed package runtime checks')
        report['status'] = 'PASS'
    except (Exception, SystemExit) as error:
        report.update(status='FAIL', error=str(error))
    finally:
        try:
            report['inputs_unchanged'] = bool(inputs) and all(sha(Path(path)) == digest for path, digest in inputs.items())
            report['packages_unchanged'] = bool(roots) and all(inventory(Path(root)) == files for root, files in roots.items())
            require(report['inputs_unchanged'] and report['packages_unchanged'], 'package verification inputs changed')
        except Exception as error:
            report.update(status='FAIL', input_error=str(error))
        save()
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
