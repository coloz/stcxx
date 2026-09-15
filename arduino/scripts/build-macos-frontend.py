#!/usr/bin/env python3
"""Build the locked STC Clang/CBE sources on a native ARM64 Mac.

Use LLVM 20.1.8 development files, Apple Command Line Tools, CMake and Ninja.
The default deployment target is macOS 15 because the verified Homebrew LLVM
dependency requires it. This build alone does not qualify older macOS releases,
Intel hosts, a relocatable distribution, or Arduino target execution.
"""
import importlib.util
from pathlib import Path
import sys

path = Path(__file__).with_name('build-linux-frontend.py')
spec = importlib.util.spec_from_file_location('stcxx_frontend_build', path)
frontend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(frontend)

if __name__ == '__main__':
    sys.exit(frontend.main(host='darwin', entrypoint=__file__))
