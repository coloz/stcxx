#!/usr/bin/env python3
"""Bind native REL object storage to CBE declarations and audit the final map.

Every possible non-C++ archive provider is inspected. Conflicting storage is
rejected, so member selection cannot change the address-space decision.
The actual final provider and CODE/XDATA class are then checked in ASlink.
"""
import argparse, hashlib, json, re, subprocess
from pathlib import Path

def sha(data): return hashlib.sha256(data).hexdigest()

def external_objects(ir):
    result = set()
    for line in ir.splitlines():
        match = re.match(r'^@(?P<name>"(?:[^"\\]|\\[0-9A-Fa-f]{2})*"|[-\w.$]+)\s*=\s*(.*)', line)
        if match and re.search(r'\bexternal\b.*\b(?:global|constant)\b', match[2]):
            name = match['name']
            if name.startswith('"'):
                name = re.sub(r'\\([0-9A-Fa-f]{2})', lambda m: chr(int(m[1], 16)), name[1:-1])
            result.add(name)
    return result

def rel_objects(payload, wanted, label):
    text = payload.decode('ascii')
    if not re.match(r'^X[HL][234]\r?\nH ', text):
        raise ValueError('unsupported REL format: ' + label)
    area = None; module = None; result = []
    for line in text.splitlines():
        if line.startswith('M '): module = line[2:]
        elif line.startswith('A '):
            m = re.fullmatch(r'A (\S+) size ([\dA-Fa-f]+) flags ([\dA-Fa-f]+) addr ([\dA-Fa-f]+)', line)
            if not m: raise ValueError('invalid REL area: ' + label)
            area = (m[1], int(m[2], 16), int(m[3], 16))
        elif line.startswith('S '):
            m = re.fullmatch(r'S (\S+) (Def|Ref)([\dA-Fa-f]+)', line)
            if not m: raise ValueError('invalid REL symbol: ' + label)
            if m[2] != 'Def' or not m[1].startswith('_') or m[1][1:] not in wanted: continue
            if not area or not module: raise ValueError('native object has absolute/unknown storage: ' + m[1])
            name, size, flags = area
            if flags & 0x08: raise ValueError('absolute native object area is unsupported: ' + name)
            if int(m[3], 16) >= size: raise ValueError('native object outside area: ' + m[1])
            # Functions are excluded by the LLVM external-object set. Only
            # explicit CODE or XDATA objects currently cross this boundary.
            storage = 'CODE' if flags & 0x20 and not flags & 0x40 else 'XDATA' if flags & 0x40 and not flags & 0x20 else None
            if storage is None: raise ValueError('unsupported native object area: ' + name)
            if len(m[1]) > 32: raise ValueError('native object exceeds unambiguous ASlink display: ' + m[1])
            result.append(dict(symbol=m[1][1:], sdcc_symbol=m[1], storage=storage,
                               area=name, module=module, provider=label))
    return result

def collect(ir, direct, archives, sdar, cpp_members):
    wanted = external_objects(ir)
    inputs = []; definitions = []
    def bind(path):
        data = path.read_bytes()
        inputs.append(dict(path=str(path.resolve()), sha256=sha(data)))
        return data
    exclusions = {}
    for line in bind(cpp_members).decode().splitlines():
        if not line: continue
        archive, member = line.split('\t')
        key = str(Path(archive).resolve())
        if member in exclusions.setdefault(key, set()): raise ValueError('duplicate C++ exclusion')
        exclusions[key].add(member)
    bind(sdar)
    for path in direct:
        definitions += rel_objects(bind(path), wanted, str(path.resolve()))
    members = []
    for path in archives:
        bind(path)
        names = subprocess.check_output([str(sdar), '-t', str(path)], text=True).splitlines()
        if len(names) != len(set(names)): raise ValueError('duplicate native archive member: ' + str(path))
        excluded = exclusions.get(str(path.resolve()), set())
        if excluded - set(names): raise ValueError('missing C++ excluded member: ' + str(path))
        for name in names:
            if name in excluded: continue
            payload = subprocess.check_output([str(sdar), '-p', str(path), name])
            label = str(path.resolve()) + '(' + name + ')'
            members.append(dict(provider=label, sha256=sha(payload)))
            definitions += rel_objects(payload, wanted, label)
    objects = {}
    for d in definitions:
        entry = objects.setdefault(d['symbol'], dict(storage=d['storage'], providers=[]))
        if entry['storage'] != d['storage']: raise ValueError('ambiguous native storage for ' + d['symbol'])
        entry['providers'].append(d)
    # Unknown external objects may be supplied by explicit SDCC runtime libs;
    # do not invent CODE provenance for them. The report keeps this boundary.
    for item in inputs:
        if sha(Path(item['path']).read_bytes()) != item['sha256']: raise ValueError('native input changed during collection')
    return dict(schema=1, ir_sha256=sha(ir.encode()), objects=objects, inputs=inputs,
                members=members, unresolved_objects=sorted(wanted - objects.keys()),
                direct=[str(p.resolve()) for p in direct], archives=[str(p.resolve()) for p in archives],
                sdar=str(sdar.resolve()), cpp_members=str(cpp_members.resolve()),
                policy='all-possible-native-providers-storage-consensus-plus-final-map')

def verify(report, ir):
    observed = collect(ir, list(map(Path, report['direct'])), list(map(Path, report['archives'])),
                       Path(report['sdar']), Path(report['cpp_members']))
    if observed != report: raise ValueError('stale or inconsistent native storage map')

def apply_storage(raw, ir, report, target, mangle):
    verify(report, ir)
    if report['unresolved_objects']:
        raise ValueError('unresolved native external object storage: ' + ', '.join(report['unresolved_objects']))
    # Reject writes through direct and SSA-derived CODE addresses. Opaque
    # callees and casting away const remain outside the supported contract.
    roots = {'@'+s for s,r in report['objects'].items() if r['storage']=='CODE'}
    for function in re.findall(r'^define .*?^}', ir, re.M | re.S):
        tainted = set(roots)
        lines = function.splitlines()
        for _ in range(len(lines)):
            previous = len(tainted)
            for line in lines:
                m = re.match(r'\s*(%[-\w.$]+) = (.*)', line)
                if m and not re.match(r'(?:load|(?:tail )?call)\b', m[2]):
                    if set(re.findall(r'[@%][-\w.$]+', m[2])) & tainted: tainted.add(m[1])
            if len(tainted) == previous: break
        for line in lines:
            if re.match(r'\s*store\b', line):
                destination = re.split(r',\s*ptr\b', line, maxsplit=1)
                if len(destination) == 2 and set(re.findall(r'[@%][-\w.$]+', destination[1])) & tainted:
                    raise ValueError('write to native CODE object: ' + line.strip())
            if re.search(r'@llvm\.mem(?:cpy|move|set)', line):
                destination = re.split(r'@llvm\.mem(?:cpy|move|set)[^(]*\(', line)[1].split(',',1)[0]
                if set(re.findall(r'[@%][-\w.$]+', destination)) & tainted:
                    raise ValueError('memory intrinsic writes native CODE object')
    changed = []
    if target == 'mcs51':
        for symbol, record in report['objects'].items():
            qualifier = '__code' if record['storage'] == 'CODE' else '__xdata'
            name = mangle(symbol)
            pattern = re.compile(r'^(const )?extern ([^;\n]*\b' + re.escape(name) + r'(?:\s*\[[^\]\n]*\])?\s*);$', re.M)
            matches = list(pattern.finditer(raw))
            if len(matches) != 1: raise ValueError('unsupported CBE native storage declaration: ' + symbol)
            decl = matches[0][2]
            if '(' in decl or '__code' in decl: raise ValueError('ambiguous CBE native object declaration: ' + symbol)
            def declaration(m):
                value = (m[1] or '') + m[2]
                if record['storage'] == 'CODE':
                    # SDCC requires CODE objects themselves to be const. For
                    # pointer objects this is after the final '*', rather than
                    # qualifying the pointee. Native storage supplies the fact
                    # Clang's external-global spelling does not preserve.
                    start = re.search(r'\b' + re.escape(name) + r'\b', value).start()
                    object_type = value[:start]
                    qualifiers = object_type.rsplit('*', 1)[-1]
                    if not re.search(r'\bconst\b', qualifiers):
                        value = object_type + 'const ' + value[start:]
                return 'extern ' + qualifier + ' ' + value + ';'
            raw = pattern.sub(declaration, raw)
            changed.append(symbol)
    return raw, dict(**report, storage_declarations_rewritten=changed)

def final_map(report, payload):
    checked = []
    for symbol, record in report['objects'].items():
        rows = re.findall(r'^([CD]):\s+[0-9A-Fa-f]+\s+_' + re.escape(symbol) + r'\s+(\S+)\s*$', payload, re.M)
        if len(rows) != 1: raise ValueError('missing/ambiguous final native object: ' + symbol)
        storage, module = rows[0]
        expected = 'C' if record['storage'] == 'CODE' else 'D'
        if storage != expected or module not in {p['module'] for p in record['providers']}:
            raise ValueError('final native provider/storage changed: ' + symbol)
        checked.append(symbol)
    return checked

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ir', type=Path, required=True)
    p.add_argument('--output', type=Path)
    p.add_argument('--verify', type=Path)
    p.add_argument('--map', type=Path)
    p.add_argument('--sdar', type=Path)
    p.add_argument('--cpp-members', type=Path)
    p.add_argument('--direct-rel', type=Path, action='append', default=[])
    p.add_argument('--archive', type=Path, action='append', default=[])
    a = p.parse_args(); ir = a.ir.read_text()
    if a.verify:
        report = json.loads(a.verify.read_text()); verify(report, ir)
        if a.map: final_map(report, a.map.read_text())
    else:
        report = collect(ir, a.direct_rel, a.archive, a.sdar, a.cpp_members)
        a.output.write_text(json.dumps(report, indent=2) + '\n')
    print('STCXX_NATIVE_STORAGE=PASS')
if __name__ == '__main__': main()
