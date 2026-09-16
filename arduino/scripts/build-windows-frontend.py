#!/usr/bin/env python3
"""Build the pinned STC frontend with native Windows MSVC and LLVM 20.1.8.

Requires the official clang+llvm-20.1.8-x86_64-pc-windows-msvc development
archive, Visual Studio C++ Build Tools, CMake and Ninja. No POSIX shell is used.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from datetime import datetime, timezone


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--llvm-root', required=True, type=Path)
    parser.add_argument('--build-root', required=True, type=Path)
    parser.add_argument('--vs-root', required=True, type=Path)
    parser.add_argument('--jobs', type=int, default=6)
    args = parser.parse_args()
    if sys.platform != 'win32' or platform.machine().lower() not in ('amd64', 'x86_64'):
        parser.error('native Windows x64 is required')
    if not 1 <= args.jobs <= 32:
        parser.error('jobs must be between 1 and 32')
    root = Path(__file__).resolve().parents[2]
    work = args.build_root.resolve()
    work.mkdir(parents=True, exist_ok=True)
    lock = json.loads((root / 'arduino/toolchain-lock.json').read_text(encoding='utf-8'))
    clang_source = root / 'toolchain/llvm-project/clang'
    cbe_source = root / 'toolchain/llvm-cbe'
    inputs = {}
    for name, expected in lock['clang']['patched_source_normalized_sha256'].items():
        path = clang_source / name
        observed = hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
        if observed != expected:
            raise RuntimeError('STC Clang source differs: ' + name)
        inputs[str(path)] = observed
    for name, key in [('CBackend.cpp', 'patched_cbackend_normalized_sha256'),
                      ('CBackend.h', 'patched_cbackend_header_normalized_sha256')]:
        path = cbe_source / 'lib/Target/CBackend' / name
        observed = hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
        if observed != lock['llvm_cbe'][key]:
            raise RuntimeError('STC CBE source differs: ' + name)
        inputs[str(path)] = observed
    vcvars = args.vs_root / 'VC/Auxiliary/Build/vcvars64.bat'
    if any(c in str(vcvars) for c in '"%\r\n'):
        raise ValueError('unsupported Visual Studio path')
    result = subprocess.run('cmd.exe /d /s /c ""' + str(vcvars) + '" >nul && set"',
                            check=True, stdout=subprocess.PIPE)
    env = dict(os.environ)
    for line in result.stdout.decode('mbcs').splitlines():
        key, separator, value = line.partition('=')
        if separator and key:
            env[key] = value
    cmake_home = args.vs_root / 'Common7/IDE/CommonExtensions/Microsoft/CMake'
    cmake = cmake_home / 'CMake/bin/cmake.exe'
    ninja = cmake_home / 'Ninja/ninja.exe'
    llvm = args.llvm_root.resolve()
    # The official development archive exports its producer's absolute DIA
    # library path. Relocate that one build-only reference to this MSVC SDK.
    exports = llvm / 'lib/cmake/llvm/LLVMExports.cmake'
    original_dia = 'C:/Program Files (x86)/Microsoft Visual Studio/2019/Professional/DIA SDK/lib/amd64/diaguids.lib'
    dia = args.vs_root / 'DIA SDK/lib/amd64/diaguids.lib'
    if not dia.is_file():
        raise RuntimeError('Visual Studio DIA SDK is required')
    export_text = exports.read_text(encoding='utf-8')
    if original_dia in export_text:
        exports.write_text(export_text.replace(original_dia, dia.as_posix()), encoding='utf-8', newline='\n')
    env['PATH'] = str(llvm / 'bin') + os.pathsep + env.get('Path', env.get('PATH', ''))
    env.pop('Path', None)
    env['VSLANG'] = '1033'
    report = {'status': 'RUNNING', 'host': 'windows-x86_64', 'inputs': inputs,
              'started_utc': datetime.now(timezone.utc).isoformat(), 'commands': [],
              'source_lock_sha256': digest(root / 'arduino/toolchain-lock.json')}
    report['dia_sdk'] = {'path': str(dia), 'sha256': digest(dia)}
    report['relocated_llvm_exports_sha256'] = digest(exports)

    def save():
        (work / 'build-report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')

    def run(name, argv):
        command = list(map(str, argv))
        print(name, flush=True)
        log = work / (name + '.log')
        with log.open('wb') as output:
            result = subprocess.run(command, env=env, stdout=output, stderr=subprocess.STDOUT)
        report['commands'].append({'name': name, 'argv': command, 'status': result.returncode,
                                   'log_sha256': digest(log)})
        save()
        if result.returncode:
            raise RuntimeError(name + ': ' + log.read_text(errors='replace')[-6000:])

    save()
    try:
        run('llvm-version', [llvm / 'bin/llvm-config.exe', '--version'])
        if (work / 'llvm-version.log').read_text().strip() != '20.1.8':
            raise RuntimeError('LLVM 20.1.8 development files are required')
        common = ['-G', 'Ninja', '-DCMAKE_MAKE_PROGRAM=' + ninja.as_posix(),
                  '-DLLVM_DIR=' + (llvm / 'lib/cmake/llvm').as_posix(),
                  '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_C_COMPILER=cl', '-DCMAKE_CXX_COMPILER=cl',
                  '-DLLVM_INCLUDE_TESTS=OFF', '-DLLVM_PARALLEL_LINK_JOBS=1',
                  '-DCMAKE_C_FLAGS=/utf-8', '-DCMAKE_CXX_FLAGS=/utf-8',
                  '-DLLVM_ENABLE_ZLIB=OFF', '-DLLVM_ENABLE_ZSTD=OFF']
        run('configure-clang', [cmake, '-S', clang_source, '-B', work / 'clang', *common,
                               '-DCLANG_INCLUDE_TESTS=OFF', '-DCLANG_INCLUDE_DOCS=OFF',
                               '-DCLANG_ENABLE_STATIC_ANALYZER=OFF', '-DCLANG_ENABLE_ARCMT=OFF'])
        run('build-clang', [cmake, '--build', work / 'clang', '--target', 'clang', '--parallel', args.jobs])
        # Windows LLVM ships component libraries, not the monolithic LLVM
        # shared library assumed by upstream CBE's standalone CMake entry.
        # Supply an interface target in a separate wrapper project, keeping
        # the vendored CBE sources and their source-manifest hashes intact.
        wrapper = work / 'cbe-project'
        wrapper.mkdir(exist_ok=True)
        (wrapper / 'CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.20)
project(stcxx_native_cbe LANGUAGES C CXX)
find_package(LLVM 20.1.8 EXACT REQUIRED CONFIG)
list(APPEND CMAKE_MODULE_PATH "${LLVM_CMAKE_DIR}")
include(AddLLVM)
include_directories(${LLVM_INCLUDE_DIRS})
add_definitions(${LLVM_DEFINITIONS})
llvm_map_components_to_libnames(stc_llvm_libs ${LLVM_TARGETS_TO_BUILD}
  Analysis AsmParser AsmPrinter BitReader CodeGen Core IRReader MC ScalarOpts
  SelectionDAG Support Target)
add_library(LLVM INTERFACE)
target_link_libraries(LLVM INTERFACE ${stc_llvm_libs})
set(USE_SYSTEM_LLVM 1)
set(CMAKE_CXX_STANDARD 17)
add_subdirectory("''' + cbe_source.as_posix() + '''" cbe)
''', encoding='utf-8', newline='\n')
        run('configure-cbe', [cmake, '-S', wrapper, '-B', work / 'cbe-native', *common,
                             '-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded'])
        run('build-cbe', [cmake, '--build', work / 'cbe-native', '--target', 'llvm-cbe', '--parallel', args.jobs])
        paths = {'clang': work / 'clang/bin/clang.exe',
                 'llvm-cbe': work / 'cbe-native/cbe/tools/llvm-cbe/llvm-cbe.exe'}
        for name, path in paths.items():
            run(name + '-version', [path, '--version'])
        report['artifacts'] = {name: {'path': str(path), 'sha256': digest(path)} for name, path in paths.items()}
        report['status'] = 'PASS'
    except Exception as error:
        report.update(status='FAIL', error=str(error))
        raise
    finally:
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        save()


if __name__ == '__main__':
    main()
