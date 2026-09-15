#!/usr/bin/env python3
"""Compare the standalone MCS251 adapter with source-bound Arduino SDK build artifacts.

Run on the host that produced the artifacts: their storage manifests contain
absolute paths. This checks exact adapted-C output, not hardware readiness or
equivalence to a lost historical adapter revision.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def reference_inputs(bridge):
    names = ('manifest.json', 'optimized.ll', 'raw.c', 'native-storage.json',
             'c-abi-preserve.txt', 'adapted.c')
    identities = {name: digest(bridge / name) for name in names}
    manifest = json.loads((bridge / 'manifest.json').read_text(encoding='utf-8'))
    require(manifest.get('outcome') == 'pass', 'reference build did not pass')
    require(manifest['target']['profile'] == 'mcs251',
            'this comparison accepts actual MCS251 SDK build artifacts only')
    artifacts = manifest['bridge_artifacts']
    for filename, key in (('optimized.ll', 'optimized_ir_sha256'),
                          ('raw.c', 'llvm_cbe_raw_c_sha256'),
                          ('adapted.c', 'adapted_c_sha256')):
        require(identities[filename] == artifacts[key],
                f'reference manifest hash mismatch: {filename}')
    require(identities['c-abi-preserve.txt'] == manifest['c_abi_preserve_sha256'],
            'reference native ABI roots changed')
    return manifest, identities


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bridge', type=Path, action='append', required=True,
                        help='stcxx/ subdirectory of a successful Arduino build; repeatable')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    standalone = Path(__file__).resolve().parents[1] / 'bridge'
    output = args.output.resolve()
    bridges = [p.resolve() for p in args.bridge]
    for source in [standalone, *bridges]:
        if output == source or source in output.parents or output in source.parents:
            parser.error('output must be separate from adapter sources and reference builds')
    output.mkdir(parents=True, exist_ok=True)
    report = {'status': 'RUNNING', 'scope': 'exact adapted-C output comparison; no historical recovery, MCS51 or hardware qualification',
              'started_utc': datetime.now(timezone.utc).isoformat(), 'results': []}

    def save():
        temporary = output / 'comparison.json.part'
        temporary.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        temporary.replace(output / 'comparison.json')

    save()
    try:
        require(len(set(bridges)) == len(bridges), 'duplicate reference build')
        adapter_files = ('adapt.py', 'audit_and_adapt.py', 'native-storage.py')
        report['adapter_sha256'] = {name: digest(standalone / name) for name in adapter_files}
        report['runner_sha256'] = digest(Path(__file__))
        report['python'] = {'path': sys.executable, 'version': sys.version,
                            'sha256': digest(Path(sys.executable))}
        for index, bridge in enumerate(bridges):
            entry = {'reference': str(bridge), 'status': 'RUNNING'}
            report['results'].append(entry)
            save()
            manifest, identities = reference_inputs(bridge)
            entry['reference_sha256'] = identities
            target = manifest['target']
            case = output / str(index)
            case.mkdir(exist_ok=True)
            command = [sys.executable, str(standalone / 'adapt.py'),
                       '--ir', str(bridge / 'optimized.ll'), '--raw-c', str(bridge / 'raw.c'),
                       '--native-storage', str(bridge / 'native-storage.json'),
                       '--c-abi-preserve', str(bridge / 'c-abi-preserve.txt'),
                       '--output-c', str(case / 'adapted.c'), '--audit-json', str(case / 'audit.json'),
                       '--target-profile', 'mcs251', '--expected-triple', target['target_triple'],
                       '--expected-layout', target['data_layout'],
                       '--abi-identity-symbol', target['abi_identity_symbol']]
            entry['command'] = command
            log = case / 'adapter.log'
            with log.open('wb') as stream:
                result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=60)
            entry.update(exit_code=result.returncode, log_sha256=digest(log))
            require(result.returncode == 0, f'adapter failed; see {log}')
            entry['output_sha256'] = digest(case / 'adapted.c')
            entry['exact_output_match'] = entry['output_sha256'] == identities['adapted.c']
            require(entry['exact_output_match'], 'standalone adapted C differs from SDK output')
            audit = json.loads((case / 'audit.json').read_text(encoding='utf-8'))
            require(audit.get('outcome') == 'pass', 'standalone audit did not pass')
            entry['audit_sha256'] = digest(case / 'audit.json')
            require(reference_inputs(bridge)[1] == identities, 'reference inputs changed during comparison')
            entry['status'] = 'PASS'
            save()
            print('PASS', bridge, flush=True)
        require(report['adapter_sha256'] == {name: digest(standalone / name) for name in adapter_files},
                'standalone adapter changed during comparison')
        require(digest(Path(__file__)) == report['runner_sha256'], 'comparison runner changed')
        report['status'] = 'PASS'
    except (Exception, KeyboardInterrupt) as error:
        report.update(status='FAIL', error=str(error) or type(error).__name__)
        for entry in report['results']:
            if entry['status'] == 'RUNNING':
                entry.update(status='FAIL', error=report['error'])
        print(report['error'], file=sys.stderr)
    finally:
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        save()
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
