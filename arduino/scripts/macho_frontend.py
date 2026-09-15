"""Inspect and resolve the Mach-O dependencies used by the native frontend.

Only explicit loader-relative, local rpath and absolute dependency forms are
accepted. Unsupported loader context fails rather than guessing another library.
"""
from pathlib import Path, PurePosixPath
import re

LOAD_COMMANDS = {'LC_LOAD_DYLIB', 'LC_LOAD_WEAK_DYLIB', 'LC_REEXPORT_DYLIB', 'LC_LOAD_UPWARD_DYLIB'}


def parse_load_commands(listing):
    result = {'dependencies': [], 'rpaths': [], 'install_name': None}
    for block in re.split(r'^Load command \d+\s*$', listing, flags=re.M)[1:]:
        match = re.search(r'^\s*cmd (LC_[A-Z0-9_]+)\s*$', block, re.M)
        if not match: raise ValueError('Mach-O load command has no command identity')
        command = match.group(1)
        if command not in LOAD_COMMANDS | {'LC_RPATH', 'LC_ID_DYLIB'}: continue
        field = 'path' if command == 'LC_RPATH' else 'name'
        value = re.search(r'^\s*' + field + r' (.+) \(offset [0-9]+\)\s*$', block, re.M)
        if not value: raise ValueError('Mach-O load command is missing its path')
        path = value.group(1)
        if '\r' in path or '\n' in path or '\\' in path: raise ValueError('unrepresentable Mach-O dependency path')
        if command == 'LC_RPATH': result['rpaths'].append(path)
        elif command == 'LC_ID_DYLIB':
            if result['install_name'] is not None: raise ValueError('multiple Mach-O library identities')
            result['install_name'] = path
        else: result['dependencies'].append(path)
    if not result['dependencies']: raise ValueError('Mach-O dependency listing is empty')
    return result


def is_system_dependency(name):
    path = PurePosixPath(name)
    return '..' not in path.parts and (name.startswith('/usr/lib/') or name.startswith('/System/Library/'))


def loader_path(name, loader):
    if name.startswith('@loader_path/'):
        return Path(loader).parent / name[len('@loader_path/'):]
    if name.startswith('/'):
        return Path(name)
    raise ValueError('unsupported Mach-O loader context: ' + name)


def resolve_dependency(name, loader, rpaths):
    if is_system_dependency(name): return None
    if name.startswith('@rpath/'):
        suffix = name[len('@rpath/'):]
        if not suffix or '..' in PurePosixPath(suffix).parts: raise ValueError('unsafe Mach-O rpath suffix')
        candidates = [loader_path(path, loader) / suffix for path in rpaths]
    else:
        candidates = [loader_path(name, loader)]
    # dyld searches LC_RPATH entries in order. Bind the first existing file;
    # callers additionally compare it to their locked build artifact identity.
    for candidate in candidates:
        if candidate.is_file(): return candidate.resolve(strict=True)
    raise ValueError('unresolved Mach-O dependency: ' + name + ' for ' + str(loader))
