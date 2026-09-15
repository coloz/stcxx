#!/usr/bin/env python3
"""Stage a relocatable Windows SDCC candidate from cross/native build outputs.

Run in WSL after both builds finish. This stages files and records provenance;
executing Windows compilation and target runtime tests is a separate gate.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--windows-build', required=True, type=Path)
    parser.add_argument('--native-build', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[2]
    win, native, output = (p.resolve() for p in
                          (args.windows_build, args.native_build, args.output))
    if output.exists() or output == Path('/'):
        parser.error('output must be a new directory')
    if (win / 'status.txt').read_text().strip() != 'PASS':
        parser.error('Windows build did not pass')

    inputs = {}
    for name in ('sdcc',):
        inputs[f'bin/{name}.exe'] = win / f'src/{name}.exe'
    for source_name, dest in (('cpp', 'sdcpp'), ('cc1', 'cc1')):
        directory = 'libexec/sdcc' if dest == 'cc1' else 'bin'
        inputs[f'{directory}/{dest}.exe'] = win / f'support/cpp/gcc/{source_name}.exe'
    for name in ('sdas251', 'sdas8051', 'sdld', 'sdldmcs251', 'packihx', 'makebin'):
        inputs[f'bin/{name}.exe'] = win / f'bin/{name}.exe'
    for name in ('sdar', 'sdnm', 'sdobjcopy', 'sdranlib'):
        inputs[f'bin/{name}.exe'] = win / f'support/sdbinutils/binutils/{name}.exe'
    for path in sorted((source / 'device/include').rglob('*')):
        if path.is_file():
            inputs['include/' + path.relative_to(source / 'device/include').as_posix()] = path
    for model in ('small', 'small-stack-auto', 'medium', 'large', 'large-stack-auto', 'huge',
                  'mcs251-small', 'mcs251-small-stack-auto',
                  'mcs251-large', 'mcs251-large-stack-auto'):
        directory = native / 'device/lib/build' / model
        target = 'mcs251' if model.startswith('mcs251-') else 'mcs51'
        for required in ('libsdcc.lib', f'{target}.lib', 'libint.lib', 'liblong.lib',
                         'liblonglong.lib', 'libfloat.lib'):
            if not (directory / required).is_file():
                parser.error(f'missing runtime archive: {directory / required}')
        for path in sorted(directory.glob('*.lib')):
            inputs[f'lib/{model}/{path.name}'] = path
    inputs['COPYING.txt'] = source / 'COPYING'
    for name in ('COPYING', 'COPYING3', 'COPYING.LIB', 'COPYING3.LIB'):
        inputs[f'licenses/binutils/{name}'] = source / 'support/sdbinutils' / name
    inputs['licenses/assembler/COPYING3'] = source / 'sdas/COPYING3'
    # Preserve the actual installed dependencies' notices, including the GCC
    # runtime exception. libboost-dev is only a meta-package: use the package
    # owning the headers that were copied into the cross build.
    owner = subprocess.run(['dpkg-query', '-S', '/usr/include/boost/version.hpp'],
                           check=True, capture_output=True, text=True).stdout
    boost_package = owner.split(':', 1)[0]
    if not re.fullmatch(r'libboost[0-9.]+-dev', boost_package):
        parser.error(f'unexpected Boost header owner: {owner.strip()}')
    if sha(win / 'host-include/boost/version.hpp') != sha(Path('/usr/include/boost/version.hpp')):
        parser.error('installed Boost version differs from the cross build')
    for package in (boost_package, 'mingw-w64-common', 'gcc-mingw-w64-base',
                    'gcc-mingw-w64-x86-64-posix-runtime', 'libz-mingw-w64'):
        inputs[f'licenses/dependencies/{package}.copyright'] = Path('/usr/share/doc') / package / 'copyright'
    for name in ('build-windows-sdcc-wsl.sh', 'publish-windows-sdcc.py'):
        inputs[f'build-scripts/{name}'] = Path(__file__).with_name(name)
    inputs['toolchain-lock.json'] = source / 'arduino/toolchain-lock.json'
    report = {'schema_version': 1, 'status': 'RUNNING',
              'windows_build': str(win), 'native_build': str(native),
              'runtime_execution_qualified': False, 'inputs': {}, 'imports': {}}
    for relative, path in inputs.items():
        if not path.is_file() or path.stat().st_size == 0:
            parser.error(f'missing/empty input: {path}')
        report['inputs'][relative] = {'source': str(path), 'sha256': sha(path)}
        if relative.endswith('.exe'):
            if path.read_bytes()[:2] != b'MZ':
                parser.error(f'not a Windows executable: {path}')
            headers = subprocess.run(['x86_64-w64-mingw32-objdump', '-p', str(path)],
                                     check=True, capture_output=True, text=True).stdout
            imports = sorted(set(re.findall(r'DLL Name:\s*(\S+)', headers)))
            unexpected = set(x.lower() for x in imports) - {
                'kernel32.dll', 'msvcrt.dll', 'user32.dll', 'advapi32.dll',
                'shell32.dll', 'shlwapi.dll', 'ole32.dll', 'ws2_32.dll'}
            if not imports or unexpected:
                parser.error(f'unbundled DLL dependency: {relative}: {imports}')
            report['imports'][relative] = imports
    output.mkdir(parents=True)
    report_path = output / 'candidate-provenance.json'
    def save():
        report_path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    save()
    try:
        for relative, path in inputs.items():
            destination = output / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            if sha(destination) != report['inputs'][relative]['sha256']:
                raise RuntimeError(f'input changed while copying: {path}')
        for relative, path in inputs.items():
            if sha(path) != report['inputs'][relative]['sha256']:
                raise RuntimeError(f'input changed during publication: {path}')
        (output / 'README.md').write_text(
            '# Windows SDCC local candidate\n\n'
            'Built from the local stcxx source identified by toolchain-lock.json.\n'
            'This is a locally modified candidate, not an unchanged upstream release.\n'
            'candidate-provenance.json records every staged input and PE DLL import.\n'
            'MANIFEST.sha256 covers the complete staged file set.\n\n'
            'The compiler supports MCS51 and MCS251; Arduino release qualification\n'
            'has a separate scope. Staging success does not qualify execution,\n'
            'Arduino compatibility or hardware. Keep external test reports with\n'
            'the archive checksum. A public release also needs the corresponding\n'
            'source and build inputs to accompany its distribution.\n\n'
            'COPYING.txt and licenses/ preserve project, binutils, assembler and\n'
            'cross-runtime notices. Source headers retain their individual terms.\n',
            encoding='utf-8')
        report['status'] = 'PASS'
        save()
        files = sorted(p for p in output.rglob('*') if p.is_file())
        (output / 'MANIFEST.sha256').write_text(''.join(
            f'{sha(p)}  {p.relative_to(output).as_posix()}\n' for p in files), encoding='utf-8')
        print(f'WINDOWS_SDCC_STAGED={output}')
    except Exception as error:
        report.update(status='FAIL', error=str(error))
        save()
        raise


if __name__ == '__main__':
    main()
