#!/usr/bin/env python3
"""Fail-closed LLVM/LLVM-CBE audit and STC constructor bridge generator.

This adapter is intentionally scoped to the K246 C++ runtime canary.  It does
not claim to be a general C++ backend: every accepted LLVM instruction and
intrinsic is enumerated, the target layout is exact, and unsupported lifetime,
address-space, linkage, atomic, exception, and control-flow features fail.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


class AuditError(RuntimeError):
    pass


ALL_OPCODES = {
    "ret", "br", "switch", "indirectbr", "invoke", "callbr", "resume",
    "catchswitch", "catchret", "cleanupret", "unreachable", "fneg", "add",
    "fadd", "sub", "fsub", "mul", "fmul", "udiv", "sdiv", "fdiv", "urem",
    "srem", "frem", "shl", "lshr", "ashr", "and", "or", "xor",
    "extractelement", "insertelement", "shufflevector", "extractvalue",
    "insertvalue", "alloca", "load", "store", "fence", "cmpxchg", "atomicrmw",
    "getelementptr", "trunc", "zext", "sext", "fptrunc", "fpext", "fptoui",
    "fptosi", "uitofp", "sitofp", "ptrtoint", "inttoptr", "bitcast",
    "addrspacecast", "icmp", "fcmp", "phi", "select", "freeze", "call",
    "va_arg", "landingpad", "catchpad", "cleanuppad",
}

ALLOWED_OPCODES = {
    "ret", "br", "switch", "unreachable", "fneg", "add", "fadd", "sub",
    "fsub", "mul", "fmul", "udiv", "sdiv", "fdiv", "urem", "srem", "frem",
    "shl", "lshr", "ashr", "and", "or", "xor", "extractvalue",
    "insertvalue", "alloca", "load", "store", "getelementptr", "trunc",
    "zext", "sext", "fptrunc", "fpext", "fptoui", "fptosi", "uitofp",
    "sitofp", "ptrtoint", "inttoptr", "bitcast", "addrspacecast", "icmp",
    # The pinned CBE only lowers scalar freeze after proving its operand is
    # defined (including loop-carried PHIs); unknown/poison shapes fail there.
    "fcmp", "phi", "select", "freeze", "call",
}

ALLOWED_INTRINSIC_PREFIXES = (
    "llvm.lifetime.start.",
    "llvm.lifetime.end.",
    "llvm.memcpy.",
    "llvm.memmove.",
    "llvm.memset.",
    "llvm.fmuladd.",
    "llvm.trap",
)

# Metadata-only scope marker. LLVM-CBE emits no runtime C operation for this
# exact intrinsic. Keep an exact-name allow-list so unrelated experimental
# intrinsics still fail closed.
ALLOWED_INTRINSIC_NAMES = {
    "llvm.experimental.noalias.scope.decl",
    # The pinned CBE removes assume after LLVM has consumed its information.
    # collect_intrinsics separately verifies the scalar, bundle-free form.
    "llvm.assume",
    "llvm.invariant.start.p0",
    # LLVM 20 lowers the C/C++ isinf()/isnan() builtins retained by Arduino
    # Print::printFloat() to these exact scalar forms.  The pinned LLVM-CBE
    # implements both types and rejects malformed operands or other widths.
    "llvm.is.fpclass.f32",
    "llvm.is.fpclass.f64",
    "llvm.fabs.f32",
    *{f"llvm.{operation}.i{bits}" for operation in ("smin", "smax", "umin", "umax")
      for bits in (8, 16, 32, 64)},
    *{f"llvm.{operation}.i{bits}" for operation in ("fshl", "fshr")
      for bits in (8, 16, 24, 32, 64)},
    *{f"llvm.abs.i{bits}" for bits in (8, 16, 32, 64)},
}

FORBIDDEN_IR_PATTERNS = {
    "nonzero_address_space": re.compile(r"\baddrspace\s*\(\s*[1-9][0-9]*\s*\)"),
    "global_destructors": re.compile(r"@llvm\.global_dtors\b"),
    "destructor_registration": re.compile(
        r"@(?:__cxa_atexit|__cxa_thread_atexit|atexit)\b"
    ),
    "thread_local": re.compile(r"\bthread_local\b"),
    "comdat": re.compile(r"\bcomdat\b"),
    # A global alias/ifunc definition has a keyword after its '='. Metadata
    # attachments such as !alias.scope are not symbol aliases.
    "alias_or_ifunc": re.compile(r"^\s*@[^\n=]+=[^\n]*\b(?:alias|ifunc)\s+", re.M),
    "inline_assembly": re.compile(r"\b(?:call|invoke)\s+[^\n]*\basm\b"),
    "scalable_vector": re.compile(r"<\s*vscale\s+x\s+"),
    "fixed_vector": re.compile(r"<\s*[1-9][0-9]*\s+x\s+[^{}>]+>"),
    "unstable_value": re.compile(r"\b(?:poison|undef)\b"),
}

FORBIDDEN_CBE_PAYLOAD = {
    "global_destructor": re.compile(r"llvm\.global_dtors"),
    "destructor_registration": re.compile(
        r"\b(?:__cxa_atexit|__cxa_thread_atexit|atexit)\s*\("
    ),
    "thread_local": re.compile(r"\b(?:__thread|_Thread_local)\b"),
    "atomic": re.compile(r"\b(?:_Atomic|atomic_[A-Za-z0-9_]+)\b"),
    "inline_assembly": re.compile(r"\b(?:__asm__|asm)\s*\("),
    "unsupported_wide_integer": re.compile(r"\b(?:u?int128_t|__int128)\b"),
    "vector_extension": re.compile(r"\b(?:vector_size|ext_vector_type)\b"),
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditError(message)


_C_OPAQUE = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', re.DOTALL)
_LLVM_OPAQUE = re.compile(r'[@%]"(?:\\.|[^"\\])*"|"(?:\\.|[^"\\])*"|;[^\n]*')


def _blank_token(match: re.Match[str]) -> str:
    return ''.join('\n' if char == '\n' else ' ' for char in match.group(0))


def mask_c_data(payload: str) -> str:
    """Keep code offsets/newlines while making comments and literals opaque."""
    return _C_OPAQUE.sub(_blank_token, payload)


def mask_llvm_data(ir: str) -> str:
    """Mask LLVM literal data/comments, retaining quoted @/% identifiers."""
    return _LLVM_OPAQUE.sub(
        lambda m: m.group(0) if m.group(0).startswith(('@', '%')) else _blank_token(m), ir
    )


def audit_native_aggregate_abi(ir: str, native_roots=()) -> dict[str, object]:
    """Reject incompatible aggregate signatures at direct native C boundaries.

    SDCC's native struct return pushes a hidden stack argument and disables
    argument registers. CBE's explicit LLVM sret parameter uses the normal
    pointer ABI instead. Internal C++ calls share the latter convention.
    Aggregate callbacks must have proven internal provenance; opaque native
    boundaries use scalar callbacks or explicit pointer/result parameters.
    """
    source = mask_llvm_data(ir)
    identifier = r'"(?:[^"\\]|\\[0-9A-Fa-f]{2})*"|[-A-Za-z$._0-9]+'
    quoted = r'"(?:[^"\\]|\\[0-9A-Fa-f]{2})*"'
    header = re.compile(r'^\s*(define|declare)\s+((?:' + quoted + r'|[^@"])*?)@(' + identifier + r')\s*\(', re.M)
    roots = set(native_roots)
    checked = []
    rejected = []
    for match in header.finditer(source):
        raw_name = match[3]
        name = (re.sub(r'\\([0-9A-Fa-f]{2})', lambda m: chr(int(m[1], 16)), raw_name[1:-1])
                if raw_name.startswith('"') else raw_name)
        if name.startswith('llvm.') or (match[1] == 'define' and name not in roots):
            continue
        # The input is verified LLVM IR. Still parse balanced attribute/type
        # parentheses so multiline declarations and quoted names cannot hide
        # an sret/byval parameter from the boundary check.
        depth = 1
        end = None
        for token in re.finditer(r'"(?:[^"\\]|\\.)*"|[()]', source[match.end():]):
            if token[0] == '(':
                depth += 1
            elif token[0] == ')':
                depth -= 1
                if depth == 0:
                    end = match.end() + token.start()
                    break
        require(end is not None, f'cannot parse native function signature: {name}')
        parameters = source[match.end():end]
        # Quoted identifiers are opaque here; their spelling is not an ABI
        # attribute. Preserve the leading % so named aggregate types remain.
        signature = re.sub(r'"(?:[^"\\]|\\.)*"', '""', parameters)
        result = re.sub(r'"(?:[^"\\]|\\.)*"', '""', match[2])
        reasons = sorted(set(re.findall(r'\b(sret|byval|byref|inalloca|preallocated)\s*\(', signature)))
        if re.search(r'[%{\[<]', result):
            reasons.append('aggregate return')
        # Split only at the outermost comma (literal structs and arrays may
        # themselves contain commas). Attributes with type arguments stay intact.
        nesting = 0
        begin = 0
        parts = []
        for index, character in enumerate(signature + ','):
            if character in '({[<':
                nesting += 1
            elif character in ')}]>':
                nesting -= 1
            elif character == ',' and nesting == 0:
                parts.append(signature[begin:index].lstrip())
                begin = index + 1
        if any(part.startswith(('%', '{', '[', '<')) for part in parts):
            reasons.append('aggregate argument')
        if reasons:
            rejected.append(f'{name} ({", ".join(reasons)})')
        checked.append(name)
    require(not rejected, 'unsupported native C aggregate ABI: ' + '; '.join(rejected)
            + '; use an explicit pointer/result parameter at the C boundary')
    return {'policy': 'scalar-and-explicit-pointer-direct-native-boundaries',
            'checked_symbols': sorted(set(checked)),
            'opaque_native_callbacks_qualified': False,
            'callback_provenance': audit_native_callback_abi(ir, native_roots)}


def audit_native_callback_abi(ir, native_roots=()):
    """Conservatively prove that aggregate callbacks stay inside this IR module.

    The verified LLVM printer supplies the syntax. This flow-insensitive graph
    merges aliases/fields deliberately: an ambiguous aggregate callback fails,
    rather than guessing a native signature erased by opaque pointers.
    """
    from collections import defaultdict, deque

    source = mask_llvm_data(ir)
    identifier = r'"(?:[^"\\]|\\[0-9A-Fa-f]{2})*"|[-A-Za-z$._0-9]+'
    value_pattern = re.compile(r'[@%](?:' + identifier + r')')

    def decoded(raw):
        if raw.startswith('"'):
            return re.sub(r'\\([0-9A-Fa-f]{2})', lambda m: chr(int(m[1], 16)), raw[1:-1])
        return raw

    def split(text):
        parts, begin, depth = [], 0, 0
        for token in re.finditer(r'"(?:[^"\\]|\\.)*"|[(){}\[\]<>,]', text + ','):
            char = token[0]
            if char in ('(', '{', '[', '<'):
                depth += 1
            elif char in (')', '}', ']', '>'):
                depth -= 1
            elif char == ',' and depth == 0:
                parts.append(text[begin:token.start()].strip())
                begin = token.end()
        return [part for part in parts if part]

    def end_arguments(text, begin):
        depth = 1
        for token in re.finditer(r'"(?:[^"\\]|\\.)*"|[()]', text[begin:]):
            if token[0] == '(':
                depth += 1
            elif token[0] == ')':
                depth -= 1
                if depth == 0:
                    return begin + token.start()
        require(False, 'cannot parse native callback argument list')

    def aggregate(result, parameters):
        result = re.sub(r'"(?:[^"\\]|\\.)*"', '""', result)
        parameters = re.sub(r'"(?:[^"\\]|\\.)*"', '""', parameters)
        return bool(re.search(r'[%{\[<]', result) or
                    re.search(r'\b(?:sret|byval|byref|inalloca|preallocated)\s*\(', parameters) or
                    any(p.startswith(('%', '{', '[', '<')) for p in split(parameters)))

    functions, bodies = {}, []
    quoted = r'"(?:[^"\\]|\\[0-9A-Fa-f]{2})*"'
    header = re.compile(r'^\s*(define|declare)\s+((?:' + quoted + r'|[^@"])*?)@(' + identifier + r')\s*\(', re.M)
    for match in header.finditer(source):
        name = decoded(match[3])
        end = end_arguments(source, match.end())
        parameters = source[match.end():end]
        function = {'defined': match[1] == 'define', 'parameters': split(parameters),
                    'aggregate': aggregate(match[2], parameters), 'locals': set(), 'body': ''}
        if function['defined']:
            opening = source.find('{', end)
            require(opening >= 0, 'missing callback function body: ' + name)
            closing = re.search(r'^\s*\}\s*$', source[opening + 1:], re.M)
            require(closing is not None, 'cannot delimit callback function body: ' + name)
            finish = opening + 1 + closing.end()
            function['body'] = source[opening + 1:opening + 1 + closing.start()]
            bodies.append((match.start(), finish))
            for parameter in function['parameters']:
                names = value_pattern.findall(parameter)
                require(names and names[-1].startswith('%'), 'unnamed callback formal: ' + name)
                function['locals'].add(decoded(names[-1][1:]))
            for line in function['body'].splitlines():
                definition = re.match(r'\s*(%(?:' + identifier + r'))\s*=', line)
                if definition:
                    function['locals'].add(decoded(definition[1][1:]))
        functions[name] = function

    unsafe = {name for name, f in functions.items() if f['aggregate'] and not name.startswith('llvm.')}
    # A native factory may supply an aggregate callback even when there are
    # no aggregate function definitions in the C++ module.
    edges, origins = defaultdict(set), defaultdict(set)
    queue = deque()
    unknown = '<native-or-unresolved>'
    exposed, writes, calls = set(), set(), []
    roots = set(native_roots)

    def node(scope, token):
        kind, name = token[0], decoded(token[1:])
        if kind == '@':
            return ('', name)
        if name in functions[scope]['locals']:
            return (scope, name)
        return None  # named LLVM type or block label, not an SSA value

    def refs(scope, text):
        return {n for token in value_pattern.findall(text) if (n := node(scope, token)) is not None}

    def seed(n, values):
        new = set(values) - origins[n]
        if new:
            origins[n].update(new)
            queue.append(n)
            return True
        return False

    def edge(a, b):
        if b in edges[a]:
            return False
        edges[a].add(b)
        seed(b, origins[a])
        return True

    def connect(left, right, alias=False):
        changed = False
        for a in left:
            for b in right:
                changed |= edge(a, b)
                if alias:
                    changed |= edge(b, a)
        return changed

    def propagate():
        while queue:
            current = queue.popleft()
            for destination in edges[current]:
                seed(destination, origins[current])

    for name, function in functions.items():
        seed(('', name), [name])
        if function['defined'] and name in roots:
            for parameter in function['parameters']:
                for n in refs(name, parameter):
                    seed(n, [unknown])
            exposed.add(((name, None), 'native root return ' + name))

    # Globals, including initializer tables, are memory nodes. Internal
    # storage can hold callbacks; externally visible storage can escape to C.
    globals_source = source
    for begin, end in reversed(bodies):
        globals_source = globals_source[:begin] + '\n' * source[begin:end].count('\n') + globals_source[end:]
    global_pattern = re.compile(r'^\s*@(' + identifier + r')\s*=(.*?)'
        r'(?=^\s*(?:[@%!]|define\b|declare\b|target\b|source_filename\b|attributes\b)|\Z)', re.M | re.S)
    for match in global_pattern.finditer(globals_source):
        name, initializer = decoded(match[1]), match[2]
        destination = ('', name)
        for token in value_pattern.findall(initializer):
            if token.startswith('@'):
                connect({('', decoded(token[1:]))}, {destination}, alias=True)
        if not re.search(r'\b(?:internal|private)\b', initializer):
            seed(destination, [unknown])
            exposed.add((destination, 'externally visible global ' + name))

    for name, function in functions.items():
        if not function['defined']:
            continue
        for index, raw_line in enumerate(function['body'].splitlines()):
            line = raw_line.strip()
            if not line:
                continue
            definition = re.match(r'(%(?:' + identifier + r'))\s*=\s*(.*)', line)
            destination = {node(name, definition[1])} if definition else set()
            instruction = definition[2] if definition else line
            location = name + ':' + str(index + 1)
            call = re.search(r'\bcall\s+', instruction)
            if call:
                callee = re.search(r'([@%](?:' + identifier + r'))\s*\(', instruction[call.end():])
                require(callee is not None, 'unproven native callback call syntax: ' + location)
                start = call.end() + callee.end()
                end = end_arguments(instruction, start)
                prefix = instruction[call.end():call.end() + callee.start()]
                parameters = instruction[start:end]
                args = [refs(name, p) for p in split(parameters)]
                calls.append({'callee': node(name, callee[1]), 'args': args, 'result': destination,
                              'location': location, 'aggregate': aggregate(prefix, parameters),
                              'indirect': callee[1].startswith('%')})
            elif instruction.startswith('store '):
                operands = split(instruction[6:])
                require(len(operands) >= 2, 'unproven callback store: ' + location)
                target = refs(name, operands[1])
                connect(refs(name, operands[0]), target, alias=True)
                writes.update((n, location) for n in target)
            elif instruction.startswith('load '):
                operands = split(instruction[5:])
                require(len(operands) >= 2, 'unproven callback load: ' + location)
                connect(refs(name, operands[1]), destination, alias=True)
            elif instruction.startswith('ret '):
                connect(refs(name, instruction[4:]), {(name, None)}, alias=True)
            elif instruction.startswith('select '):
                operands = split(instruction[7:])
                require(len(operands) == 3, 'unproven callback select: ' + location)
                connect(refs(name, operands[1] + ', ' + operands[2]), destination, alias=True)
            elif instruction.startswith('getelementptr inbounds '):
                operands = split(instruction[len('getelementptr inbounds '):])
                require(len(operands) >= 2, 'unproven callback address: ' + location)
                # An inbounds GEP stays in its base object. Its integer index
                # selects a field/element; it does not supply a pointer origin.
                connect(refs(name, operands[1]), destination, alias=True)
            elif not instruction.startswith(('alloca ', 'icmp ', 'fcmp ')):
                # Copies, phi/select, aggregate operations and pointer/integer
                # conversions retain provenance. Alias both ways for stores
                # through derived addresses; merging fields is conservative.
                connect(refs(name, instruction), destination, alias=True)

    if not unsafe and not any(call['aggregate'] for call in calls):
        return {'policy': 'aggregate-callbacks-confined-to-verified-module',
                'aggregate_functions': [], 'indirect_aggregate_calls': [], 'analysis': 'not-required'}

    def native_call(call):
        changed = False
        for n in call['result']:
            changed |= seed(n, [unknown])
        for argument in call['args']:
            for n in argument:
                exposure = (n, 'native call ' + call['location'])
                if exposure not in exposed:
                    exposed.add(exposure)
                    changed = True
                # Opaque native code can replace pointers in accessible data.
                # A known function address itself is immutable code storage.
                if not origins[n] or unknown in origins[n] or not origins[n] <= functions.keys():
                    changed |= seed(n, [unknown])
        return changed

    unresolved_enabled = False
    while True:
        propagate()
        changed = False
        for call in calls:
            targets = origins[call['callee']]
            for target in sorted(targets - {unknown}):
                function = functions.get(target)
                if target.startswith(('llvm.memcpy.', 'llvm.memmove.')):
                    require(len(call['args']) >= 2, 'invalid memory intrinsic')
                    changed |= connect(call['args'][1], call['args'][0], alias=True)
                    writes.update((n, call['location']) for n in call['args'][0])
                elif target.startswith('llvm.'):
                    for n in call['result']:
                        changed |= seed(n, [unknown])
                elif function and function['defined'] and len(function['parameters']) == len(call['args']):
                    for argument, formal in zip(call['args'], function['parameters']):
                        changed |= connect(argument, refs(target, formal), alias=True)
                    changed |= connect({(target, None)}, call['result'], alias=True)
                else:
                    changed |= native_call(call)
            if unknown in targets or (unresolved_enabled and not targets):
                changed |= native_call(call)
        # A scalar callback escaping to native code is another entry point:
        # C may supply its pointer parameters and observe its return value.
        # Include these transitive entries in the fixed point, even if all
        # currently visible IR callers happen to pass an internal callback.
        escaping = {n for n, _ in exposed}
        escaping.update(n for n, _ in writes if unknown in origins[n])
        for n in escaping:
            for target in tuple(origins[n]):
                function = functions.get(target)
                if not function or not function['defined']:
                    continue
                for formal in function['parameters']:
                    for parameter in refs(target, formal):
                        changed |= seed(parameter, [unknown])
                exposure = ((target, None), 'native callback return ' + target)
                if exposure not in exposed:
                    exposed.add(exposure)
                    changed = True
        if not changed and not queue:
            if unresolved_enabled:
                break
            unresolved_enabled = True

    rejected = set()
    for n, location in exposed:
        callbacks = origins[n] & unsafe
        if callbacks:
            rejected.add(location + ': ' + ', '.join(sorted(callbacks)))
    for n, location in writes:
        if unknown in origins[n] and origins[n] & unsafe:
            rejected.add('store through native/unknown memory ' + location)
    checked = []
    for call in calls:
        if call['aggregate']:
            targets = origins[call['callee']]
            if targets and all(target.startswith('llvm.') for target in targets):
                continue  # Intrinsic signatures are verified by LLVM/the IR gate.
            valid = (targets and unknown not in targets and all(
                target in functions and functions[target]['defined'] and functions[target]['aggregate'] and
                len(functions[target]['parameters']) == len(call['args']) for target in targets))
            if not valid:
                rejected.add('unproven incoming aggregate callback ' + call['location'])
            if call['indirect']:
                checked.append({'site': call['location'], 'targets': sorted(targets)})
    require(not rejected, 'unsupported native C aggregate callback ABI: ' + '; '.join(sorted(rejected)) +
            '; use scalar callbacks or explicit pointer/result parameters at native boundaries')
    return {'policy': 'aggregate-callbacks-confined-to-verified-module',
            'aggregate_functions': sorted(unsafe), 'indirect_aggregate_calls': checked,
            'analysis': 'conservative-flow-insensitive-provenance', 'graph_nodes': len(origins)}


def audit_ignored_pointer_arguments(ir: str):
    """Audit CBE's null materialization of unused private pointer formals.

    Address-taken methods/callbacks retain their ABI even after LLVM proves a
    pointer argument unused. Accept only direct calls with matching scalar
    signatures and NO SSA uses of each ignored formal. ABI-affecting/unknown
    attributes and noundef on an ignored pointer remain rejected. Other
    arguments are preserved, and original IR/CBE output are never rewritten.
    """
    source = mask_llvm_data(ir)
    symbol = r'[-A-Za-z$._0-9]+'
    safe_pointer_attrs = r'(?:(?:nocapture|nonnull|readnone) |align [1-9][0-9]* )*'
    supported_attrs = (r'(?:(?:nocapture|nonnull|readnone|noundef|zeroext|signext|'
                       r'noalias|readonly|writeonly) |align [1-9][0-9]* |'
                       r'dereferenceable(?:_or_null)?\([0-9]+\) )*')
    argument = re.compile(rf'(?P<type>ptr|i(?:1|8|16|24|32|64)) '
                          rf'(?P<attrs>{supported_attrs})'
                          rf'(?P<value>[%@]{symbol}|-?[0-9]+|true|false|null|poison|undef)')

    def parse(text, formal=False):
        parts = text.split(', ')
        args = [argument.fullmatch(part) for part in parts]
        if not all(args):
            return None
        if formal and any(not a['value'].startswith('%') for a in args):
            return None
        return args

    definitions = re.finditer(
        rf'^define (?:internal|private) [^\n@]*@({symbol})'
        rf'\(([^\n]*)\)[^\n]*\{{\n(.*?)^\}}', source, re.M | re.S)
    ignored = {}
    for match in definitions:
        args = parse(match[2], formal=True)
        if args is None:
            continue
        unused = {}
        for index, arg in enumerate(args):
            if (arg['type'] == 'ptr' and re.fullmatch(safe_pointer_attrs, arg['attrs'])
                    and not re.search(re.escape(arg['value']) + r'(?![-A-Za-z$._0-9])', match[3])):
                unused[index] = arg['value']
        if unused:
            ignored[match[1]] = ([a['type'] for a in args], unused)

    records = []
    calls = re.compile(rf'(?P<head>\bcall [^\n@]*@(?P<callee>{symbol})\()'
                       r'(?P<args>[^\n]*)\)')

    def replace(match):
        if match['callee'] not in ignored:
            return match[0]
        args = parse(match['args'])
        types, unused = ignored[match['callee']]
        if args is None or [a['type'] for a in args] != types:
            return match[0]
        unstable = [i for i, arg in enumerate(args) if arg['value'] in ('poison', 'undef')]
        if not unstable or any(i not in unused or
                               not re.fullmatch(safe_pointer_attrs, args[i]['attrs'])
                               for i in unstable):
            return match[0]
        parts = match['args'].split(', ')
        for index in unstable:
            arg = args[index]
            parts[index] = parts[index][:arg.start('value')] + 'null'
            records.append(dict(callee=match['callee'], formal=unused[index],
                                argument_index=index,
                                line=source.count('\n', 0, match.start()) + 1,
                                value=arg['value'], cbe_materialization='null-unused'))
        return match['head'] + ', '.join(parts) + ')'

    return calls.sub(replace, source), records


def canonicalize_widened_pointer_differences(ir: str):
    """Audit-only equivalence for zext(ptrtoint i24) pairs subtracted in i32.

    LLVM can split a ptrtoint-to-i32 into a native-width conversion and zext.
    The subtraction must stay i32. Both operands are in [0, 2**24-1], so nsw
    is provable; nuw is not. No arithmetic or pointer representation is changed
    in the CBE input/output. Unrecognized or escaping shapes remain rejected.
    """
    ssa=r'%[-A-Za-z$._0-9]+'
    function=re.compile(r'^define[^\n]*\{\n(?P<body>.*?)^\}',re.M|re.S)
    cast=re.compile(rf'^\s*({ssa}) = ptrtoint ptr ({ssa}) to i24\s*$',re.M)
    extend=re.compile(rf'^\s*({ssa}) = (zext i24 ({ssa}) to i32|trunc i24 ({ssa}) to i16)\s*$',re.M)
    subtract=re.compile(rf'^\s*({ssa}) = sub(?P<flag> nsw)? i(?P<bits>32|16) ({ssa}), ({ssa})\s*$',re.M)
    records=[]
    def rewrite(match):
        body=match['body'];casts={m[1]:m for m in cast.finditer(body)}
        extensions={m[1]:m for m in extend.finditer(body)};edits=[];claimed=set()
        uses=lambda name:len(re.findall(r'(?<![-A-Za-z$._0-9])'+re.escape(name)+r'(?![-A-Za-z$._0-9])',body))
        for sub in subtract.finditer(body):
            lhs,rhs=sub[4],sub[5];left,right=extensions.get(lhs),extensions.get(rhs)
            if left is None or right is None or lhs==rhs:continue
            bits=int(sub['bits']);index=3 if bits==32 else 4
            if bits==16 and sub['flag']:continue
            a,b=casts.get(left[index]),casts.get(right[index])
            if a is None or b is None or left[index]==right[index]:continue
            names=(a[1],b[1],left[1],right[1]);comparison=None
            if bits==16:
                for cmp in re.finditer(rf'^\s*({ssa}) = icmp (eq|ne) i16 ({ssa}), ({ssa})\s*$',body,re.M):
                    if {cmp[3],cmp[4]}!={lhs,rhs} or cmp.start()<sub.end():continue
                    # The modular difference must dominate the comparison in
                    # the same basic block; never rewrite ordered comparisons.
                    if re.search(r'^\S.*:|^\s*(br|switch|ret|invoke|unreachable)\b',body[sub.end():cmp.start()],re.M):continue
                    comparison=cmp;break
            expected=(2,2,3 if comparison else 2,3 if comparison else 2)
            if any(uses(name)!=count or name in claimed for name,count in zip(names,expected)):continue
            claimed.update(names)
            result=sub[1];tail=''
            if bits==16:
                # (lo16(a)-lo16(b)) mod 2**16 == lo16(a-b). This does
                # not license arbitrary integer-to-pointer reconstruction.
                result=sub[1]+'.stcxx_wide'
                while re.search(re.escape(result)+r'(?![-A-Za-z$._0-9])',body):result+='_'
                tail=f'  {sub[1]} = trunc i32 {result} to i16\n'
                if comparison:
                    edits.append((comparison.start(),comparison.end(),f'\n  {comparison[1]} = icmp {comparison[2]} i16 {sub[1]}, 0\n'))
            edits.extend([(a.start(),a.end(),'\n'),(b.start(),b.end(),'\n'),
                          (left.start(),left.end(),f'\n  {left[1]} = ptrtoint ptr {a[2]} to i32\n'),
                          (right.start(),right.end(),f'\n  {right[1]} = ptrtoint ptr {b[2]} to i32\n'),
                          (sub.start(),sub.end(),f'\n  {result} = sub i32 {lhs}, {rhs}\n'+tail)])
            records.append(dict(result=sub[1],left=a[2],right=b[2],native_bits=24,result_bits=bits))
        for start,end,replacement in sorted(edits,reverse=True):body=body[:start]+replacement+body[end:]
        return match[0][:match.start('body')-match.start()]+body+'}'
    return function.sub(rewrite,ir),records


def replace_c_code(pattern: re.Pattern[str], replacement, payload: str) -> str:
    """Replace matches in executable C tokens, never in data or comments."""
    pieces: list[str] = []
    position = 0
    for match in pattern.finditer(mask_c_data(payload)):
        original = pattern.match(payload, match.start())
        require(original is not None and original.end() == match.end(),
                'C rewrite crosses an opaque token')
        pieces.extend((payload[position:match.start()],
                       replacement(original) if callable(replacement) else original.expand(replacement)))
        position = match.end()
    pieces.append(payload[position:])
    return ''.join(pieces)


def replace_c_token(payload: str, old: str, new: str) -> str:
    return replace_c_code(re.compile(re.escape(old)), lambda _match: new, payload)


def require_count(text: str, needle: str, expected: int, label: str) -> None:
    observed = text.count(needle)
    require(
        observed == expected,
        f"{label}: expected {expected} occurrence(s), observed {observed}",
    )


def read_target(ir: str) -> tuple[str, str]:
    triple = re.search(r'^target triple = "([^"]+)"$', ir, re.MULTILINE)
    layout = re.search(r'^target datalayout = "([^"]+)"$', ir, re.MULTILINE)
    require(triple is not None, "LLVM IR is missing target triple")
    require(layout is not None, "LLVM IR is missing target DataLayout")
    return triple.group(1), layout.group(1)


def collect_opcodes(ir: str) -> list[str]:
    ir = mask_llvm_data(ir)
    observed: set[str] = set()
    in_function = False
    for line in ir.splitlines():
        stripped = line.strip()
        if stripped.startswith("define "):
            in_function = True
            continue
        if in_function and stripped == "}":
            in_function = False
            continue
        if not in_function or not stripped or stripped.endswith(":"):
            continue
        if " = " in stripped and stripped.startswith("%"):
            stripped = stripped.split(" = ", 1)[1]
        tokens = stripped.split()
        if not tokens:
            continue
        first = tokens[0]
        if first in {"tail", "musttail", "notail"} and len(tokens) > 1:
            first = tokens[1]
        if first in ALL_OPCODES:
            observed.add(first)
    disallowed = sorted(observed - ALLOWED_OPCODES)
    require(not disallowed, "unsupported LLVM opcode(s): " + ", ".join(disallowed))
    return sorted(observed)


def collect_intrinsics(ir: str) -> list[str]:
    ir = mask_llvm_data(ir)
    invariant_calls=set()
    for function in re.finditer(r'^define[^\n]*\{\n(.*?)^\}',ir,re.M|re.S):
        body=function.group(1)
        for line in body.splitlines():
            if '@llvm.invariant.start.p0' not in line:
                continue
            match=re.fullmatch(
                r'\s*(%[-A-Za-z$._0-9]+) = (?:tail )?call(?: addrspace\(1\))? ptr '
                r'@llvm\.invariant\.start\.p0\(i64 [0-9]+, ptr (?:nonnull )?[@%][-A-Za-z$._0-9]+\)(?: #[0-9]+)?',line)
            require(match is not None,'unsupported invariant.start call shape')
            require(len(re.findall(re.escape(match[1])+r'(?![-A-Za-z$._0-9])',body))==1,
                    'invariant.start token must be unused')
            invariant_calls.add(line)
    for line in ir.splitlines():
        if '@llvm.invariant.start.p0' in line and line not in invariant_calls:
            require(re.fullmatch(r'\s*declare ptr @llvm\.invariant\.start\.p0\(i64 immarg, ptr(?: nocapture)?\)(?: addrspace\(1\))?(?: #[0-9]+)?\s*',line) is not None,
                    'unsupported invariant.start declaration or use')
        if '@llvm.assume' not in line:
            continue
        require(re.fullmatch(
            r'\s*(?:declare void @llvm\.assume\(i1(?: noundef)?\)(?: addrspace\(1\))?'
            r'|(?:tail )?call(?: addrspace\(1\))? void @llvm\.assume\(i1 (?:%[-A-Za-z$._0-9]+|true|false)\))'
            r'(?: #[0-9]+)?\s*', line) is not None,
            'unsupported llvm.assume signature, operand bundle or use')
    names = sorted(
        name for name in set(re.findall(r"@((?:llvm\.)[A-Za-z0-9_.$-]+)", ir))
        if not name.startswith("llvm.global_")
    )
    unsupported = [
        name for name in names
        if name not in ALLOWED_INTRINSIC_NAMES
        and not any(name.startswith(prefix) for prefix in ALLOWED_INTRINSIC_PREFIXES)
    ]
    require(
        not unsupported,
        "unsupported LLVM intrinsic(s): " + ", ".join(unsupported),
    )
    return names


def audit_pointer_integer_conversions(ir: str) -> dict[str, object]:
    """Permit only lossless default-address-space pointer-to-i24 conversion.

    Clang lowers ordinary pointer subtraction to two ``ptrtoint ... to i24``
    instructions followed by integer subtraction.  The STC MCS251 SDCC
    headers define ``uintptr_t`` as a 32-bit unsigned long, so llvm-cbe's
    zero-extension of the 24-bit value is representable.  Integer-to-pointer
    conversion and every non-i24 shape remain rejected by the opcode and
    address-space gates.
    """

    ir = mask_llvm_data(ir)
    conversions = re.findall(
        r"^\s*(?:%[^=]+\s*=\s*)?ptrtoint\s+ptr\s+.+\s+to\s+(i[0-9]+)\s*$",
        ir,
        re.MULTILINE,
    )
    opcode_count = len(re.findall(r"\bptrtoint\b", ir))
    integer_to_pointer_count = len(re.findall(r"\binttoptr\b", ir))
    require(
        integer_to_pointer_count == 0,
        "integer-to-pointer conversion is not part of the default canary "
        "profile",
    )
    require(
        len(conversions) == opcode_count,
        "unsupported pointer-to-integer conversion shape",
    )
    require(
        all(integer_type == "i24" for integer_type in conversions),
        "pointer-to-integer conversion must preserve the exact 24-bit pointer",
    )
    return {
        "count": opcode_count,
        "integer_to_pointer_count": 0,
        "integer_type": "i24" if opcode_count else None,
        "address_space": 0,
    }


def audit_canary_traps(ir: str, intrinsics: list[str]) -> list[str]:
    ir = mask_llvm_data(ir)
    if "llvm.trap" not in intrinsics:
        return []
    callers: list[str] = []
    function_pattern = re.compile(
        r"^define\s+[^\n]*@(?:\"([^\"]+)\"|([A-Za-z0-9_.$-]+))"
        r"\([^\n]*\)[^{]*\{\n(.*?)^\}",
        re.MULTILINE | re.DOTALL,
    )
    for quoted, plain, body in function_pattern.findall(ir):
        if re.search(r"\bcall\s+void\s+@llvm\.trap\(\)", body):
            callers.append(quoted or plain)
    expected = ["_ZN11VirtualBaseD1Ev", "_ZN11VirtualBaseD0Ev"]
    require(
        callers == expected,
        "llvm.trap is allowed only in the two unreachable abstract-base "
        f"destructor variants; observed {callers!r}",
    )
    require(
        len(re.findall(r"\bcall\s+void\s+@llvm\.trap\(\)", ir)) == 2,
        "unexpected llvm.trap call count",
    )
    return callers


def parse_global_ctors(ir: str) -> list[dict[str, object]]:
    match = re.search(
        r"^@llvm\.global_ctors\s*=\s*appending\s+global\s+.*$",
        ir,
        re.MULTILINE,
    )
    require(match is not None, "LLVM IR is missing llvm.global_ctors")
    line = match.group(0)
    entry_pattern = re.compile(
        r"\{\s*i32,\s*ptr,\s*ptr\s*\}\s*\{\s*i32\s+([0-9]+),\s*"
        r"ptr\s+@(?:\"([^\"]+)\"|([A-Za-z0-9_.$-]+)),\s*ptr\s+null\s*\}"
    )
    constructors = [
        {"priority": int(priority), "llvm_symbol": quoted or plain}
        for priority, quoted, plain in entry_pattern.findall(line)
    ]
    declared_count_match = re.search(
        r"appending\s+global\s+\[([0-9]+)\s+x\s+\{", line
    )
    require(declared_count_match is not None, "cannot parse llvm.global_ctors size")
    declared_count = int(declared_count_match.group(1))
    require(
        len(constructors) == declared_count,
        "llvm.global_ctors contains an unsupported entry shape or association",
    )
    require(constructors, "the K246 C++ canary must contain global constructors")
    priorities = [int(entry["priority"]) for entry in constructors]
    require(
        priorities == sorted(priorities),
        "llvm.global_ctors priority order is not monotonic",
    )
    symbols = [str(entry["llvm_symbol"]) for entry in constructors]
    for required_source in ("ConstructorA.cpp", "ConstructorB.cpp"):
        require(
            any(required_source in symbol for symbol in symbols),
            f"missing cross-TU canary constructor for {required_source}",
        )
    return constructors


def audit_ir(ir: str, expected_triple: str, expected_layout: str) -> dict[str, object]:
    triple, layout = read_target(ir)
    require(triple == expected_triple, f"unexpected target triple: {triple}")
    require(layout == expected_layout, f"unexpected target DataLayout: {layout}")
    native_aggregate_abi = audit_native_aggregate_abi(
        ir, ('setup', 'loop', '__stcxx_run_global_ctors'))

    value_audit_ir, ignored_pointer_arguments = audit_ignored_pointer_arguments(ir)
    forbidden = [
        name for name, pattern in FORBIDDEN_IR_PATTERNS.items()
        if pattern.search(value_audit_ir)
    ]
    require(not forbidden, "forbidden LLVM IR category: " + ", ".join(forbidden))

    constructors = parse_global_ctors(ir)
    opcodes = collect_opcodes(ir)
    intrinsics = collect_intrinsics(ir)
    pointer_integer_conversions = audit_pointer_integer_conversions(ir)
    trap_callers = audit_canary_traps(ir, intrinsics)
    required_symbols = {
        "setup": r"define\s+[^\n]*@setup\(",
        "loop": r"define\s+[^\n]*@loop\(",
        "runtime_ctor_entry": r"define\s+[^\n]*@__stcxx_run_global_ctors\(",
        "virtual_dispatch": r"\bcall\s+[^@\n]*%[A-Za-z0-9_.]+\(",
        "operator_new": r"@_Zn[am]",
        "string_constructor": r"define\s+[^\n]*@_ZN6StringC1EPKc\(",
        "string_concat": r"define\s+[^\n]*@_ZN6String6concatEPKc\(",
        "string_substring": r"define\s+[^\n]*@_ZNK6String9substringEjj\(",
        "string_replace": r"define\s+[^\n]*@_ZN6String7replaceERKS_S1_\(",
        "string_trim": r"define\s+[^\n]*@_ZN6String4trimEv\(",
        "string_to_int": r"define\s+[^\n]*@_ZNK6String5toIntEv\(",
        "print_string": r"define\s+[^\n]*@_ZN5Print5printERK6String\(",
        "print_virtual_buffer_write": r"define\s+[^\n]*@_ZN5Print5writeEPKhm\(",
        "string_runtime_token": r'c"K246-CPP:STRING_VALUE=',
    }
    missing = [
        name for name, pattern in required_symbols.items() if not re.search(pattern, ir)
    ]
    require(not missing, "missing C++ canary lowering evidence: " + ", ".join(missing))

    return {
        "target_triple": triple,
        "native_aggregate_abi": native_aggregate_abi,
        "ignored_pointer_arguments": ignored_pointer_arguments,
        "data_layout": layout,
        "constructors": constructors,
        "observed_opcodes": opcodes,
        "observed_intrinsics": intrinsics,
        "pointer_integer_conversions": pointer_integer_conversions,
        "audited_trap_callers": trap_callers,
        "forbidden_categories": [],
    }


def cbe_mangle(symbol: str) -> str:
    result: list[str] = []
    for character in symbol:
        code = ord(character)
        if character.isascii() and (character.isalnum() or character == "_"):
            result.append(character)
        else:
            result.append("_")
            result.append(chr(ord("A") + (code & 15)))
            result.append(chr(ord("A") + ((code >> 4) & 15)))
            result.append("_")
    return "".join(result)


def parse_cbe_ctor_declarations(payload: str) -> list[str]:
    return re.findall(
        r"^static\s+void\s+([A-Za-z_][A-Za-z0-9_]*)\(void\)"
        r"[^;\n]*\b__ATTRIBUTE_CTOR__\s*;\s*$",
        payload,
        re.MULTILINE,
    )


def extract_cbe_fcmp_helpers(
    raw_prefix: str, payload: str
) -> tuple[list[str], list[str]]:
    """Retain the pinned CBE's pure floating-comparison helper definitions."""

    expressions={'false':'0','true':'1','ord':'X == X && Y == Y','uno':'X != X || Y != Y',
                 'oeq':'X == Y','ogt':'X > Y','oge':'X >= Y','olt':'X < Y','ole':'X <= Y',
                 'one':'X != Y && llvm_fcmp_ord(X, Y)','une':'X != Y',
                 **{name:'X '+op+' Y || llvm_fcmp_uno(X, Y)' for name,op in
                    [('ueq','=='),('ugt','>'),('uge','>='),('ult','<'),('ule','<=')]}}
    helper_pattern = re.compile(
        r"^static __forceinline int (llvm_fcmp_[a-z0-9_]+)"
        r"\(double X, double Y\) \{ return ([^\n{}]+?); \}$",
        re.MULTILINE,
    )
    helpers: list[str] = []
    names: list[str] = []
    dependencies={}
    for match in helper_pattern.finditer(raw_prefix):
        name, expression = match.groups()
        require(name not in names, f"duplicate LLVM-CBE floating helper: {name}")
        expected=expressions.get(name.removeprefix('llvm_fcmp_'))
        expression=expression.rstrip(';')
        require(expected is not None and re.sub(r'\s+','',expression)==re.sub(r'\s+','',expected),
                f"malformed LLVM-CBE floating helper: {name}")
        dependencies[name]=set(re.findall(r'\bllvm_fcmp_[a-z]+',expression))
        expression=expression.replace('llvm_fcmp_ord(X, Y)','(X == X && Y == Y)').replace('llvm_fcmp_uno(X, Y)','(X != X || Y != Y)')
        names.append(name)
        # The qualified STC ABI defines both source-level float and double as
        # IEEE binary32.  Use float in the SDCC bridge to avoid its diagnostic
        # for an unsupported wider double spelling while preserving values.
        predicate=name.removeprefix('llvm_fcmp_')
        condition={'false':'0','true':'1','oeq':'r == 0','ogt':'r == 1',
                   'oge':'r == 0 || r == 1','olt':'r == -1','ole':'r <= 0',
                   'one':'r != 0 && r != 2','ord':'r != 2',
                   'ueq':'r == 0 || r == 2','ugt':'r > 0','uge':'r >= 0',
                   'ult':'r == -1 || r == 2','ule':'r != 1','une':'r != 0','uno':'r == 2'}[predicate]
        helpers.append(f"static __forceinline int {name}(float X, float Y) "
                       f"{{ int r = __stcxx_fcmp_order32(X, Y); return {condition}; }}")
    referenced = sorted(set(re.findall(r"\b(llvm_fcmp_[a-z0-9_]+)\s*\(", mask_c_data(payload))))
    required=set(referenced)
    for _ in names:
        required.update(dep for name in tuple(required) for dep in dependencies.get(name,()))
    require(
        required == set(names),
        "LLVM-CBE floating helper definitions do not match payload calls: "
        f"defined {sorted(names)!r}, referenced {referenced!r}",
    )
    residual = re.findall(r"\bllvm_fcmp_[A-Za-z0-9_]+\b", raw_prefix)
    require(
        sorted(set(residual)) == sorted(names),
        "unrecognized LLVM-CBE floating comparison helper shape",
    )
    if names:
        # Native SDCC floating comparisons do not preserve LLVM's NaN and
        # signed-zero predicates. Compare binary32 encodings using integers.
        # C union type-punning is supported by both locked SDCC profiles.
        helpers.insert(0,'''static __forceinline int __stcxx_fcmp_order32(float X, float Y) {
  union { float f; uint32_t u; } a, b;
  uint32_t ax, bx;
  a.f = X; b.f = Y; ax = a.u; bx = b.u;
  if ((ax & 0x7fffffffUL) > 0x7f800000UL ||
      (bx & 0x7fffffffUL) > 0x7f800000UL) return 2;
  if (((ax | bx) & 0x7fffffffUL) == 0 || ax == bx) return 0;
  if ((ax ^ bx) & 0x80000000UL) return (ax & 0x80000000UL) ? -1 : 1;
  if (ax & 0x80000000UL) return ax > bx ? -1 : 1;
  return ax < bx ? -1 : 1;
}''')
    return helpers, names


def normalize_cbe_string_array_arguments(payload):
    """Make audited global byte-array wrappers decay for native char APIs."""
    types=set(re.findall(r'^struct (l_array_([1-9][0-9]*)_uint8_t) \{\n  uint8_t array\[\2\];\n\};',payload,re.M))
    types={name for name,size in types}
    arrays={name:bool(const) for const,kind,name in re.findall(
        r'^static (const )?struct (l_array_[1-9][0-9]*_uint8_t) ([A-Za-z_]\w*)\s*(?:=|;)',payload,re.M) if kind in types}
    signatures={name:{0:True} for name in ('strlen','strchr','strrchr','atoi','atol','atof','strtol','strtoul')}
    signatures.update({name:{0:True,1:True} for name in ('strcmp','strncmp','strstr','strspn','strcspn')})
    signatures.update({name:{0:False,1:True} for name in ('strcpy','strncpy','strcat','strncat')})
    masked=mask_c_data(payload);edits=[];records=[]
    for match in re.finditer(r'\b('+'|'.join(signatures)+r')\s*\(',masked):
        depth=1;start=match.end();arguments=[];i=start
        while i<len(masked) and depth:
            char=masked[i]
            if char=='(':depth+=1
            elif char==')':
                depth-=1
                if not depth:arguments.append((start,i))
            elif char==',' and depth==1:arguments.append((start,i));start=i+1
            i+=1
        if depth:continue
        for index,is_const in signatures[match[1]].items():
            if index>=len(arguments):continue
            start,end=arguments[index];argument=masked[start:end].strip()
            reference=re.fullmatch(r'\(&([A-Za-z_]\w*)\)',argument)
            if not reference or reference[1] not in arrays:continue
            symbol=reference[1]
            if not is_const and arrays[symbol]:continue
            edits.append((start,end,'((%schar *)&%s)'%('const ' if is_const else '',symbol)))
            records.append(dict(callee=match[1],argument=index,symbol=symbol))
    for start,end,value in sorted(edits,reverse=True):payload=payload[:start]+value+payload[end:]
    return payload,records


def normalize_cbe_static_byte_geps(payload):
    """Preserve constant address relocations as pointer addition for SDCC.

    SDCC rejects &((uint8_t *)&object)[constant] in static initializers but
    accepts the equivalent byte-pointer addition. Only named mutable static
    objects and bounded signed i24/i32/i64 constant CBE spellings are recognized.
    """
    objects=set(re.findall(r'^static struct [A-Za-z_]\w* ([A-Za-z_]\w*)\s*(?:=|;)',payload,re.M))
    initializer=re.compile(r'^static (?:const )?struct [A-Za-z_]\w* [A-Za-z_]\w* = [^\n]+;$',re.M)
    gep=re.compile(r'\(\(\(&\(\(uint8_t\*\)\(&([A-Za-z_]\w*)\)\)\[\(\((?:signed _BitInt\(24\)|int32_t|int64_t)\)(-?[0-9]+)\)\]\)\)\)')
    records=[]
    def change(match):
        def address(m):
            symbol,offset=m[1],int(m[2])
            if symbol not in objects or not -(1<<23)<=offset<(1<<23):return m[0]
            records.append(dict(symbol=symbol,byte_offset=offset))
            return f'(((uint8_t*)(&{symbol})) + ({offset}L))'
        return replace_c_code(gep,address,match[0])
    return initializer.sub(change,payload),records


def extract_cbe_fp_constant_typedefs(
    raw_prefix: str, payload: str
) -> tuple[list[str], list[str]]:
    """Retain only the pinned CBE's exact binary32/binary64 bit containers."""

    specifications = (
        ("ConstantFloatTy", "typedef uint32_t ConstantFloatTy;"),
        ("ConstantDoubleTy", "typedef uint64_t ConstantDoubleTy;"),
    )
    unsupported = sorted(set(re.findall(
        r"\bConstant(?:FP80|FP128)Ty\b", mask_c_data(raw_prefix + payload)
    )))
    require(
        not unsupported,
        "unsupported LLVM-CBE floating constant container(s): "
        + ", ".join(unsupported),
    )

    declarations: list[str] = []
    names: list[str] = []
    for name, expected in specifications:
        declaration_pattern = re.compile(
            rf"^typedef[^;\n]*\b{re.escape(name)}\s*;\s*$", re.MULTILINE
        )
        observed = [match.group(0).strip()
                    for match in declaration_pattern.finditer(raw_prefix)]
        referenced = re.search(rf"\b{re.escape(name)}\b", mask_c_data(payload)) is not None
        require(
            len(observed) == (1 if referenced else 0),
            f"LLVM-CBE {name} declaration does not match payload use",
        )
        if not referenced:
            require(
                re.search(rf"\b{re.escape(name)}\b", raw_prefix) is None,
                f"unrecognized LLVM-CBE {name} declaration shape",
            )
            continue
        require(
            observed == [expected],
            f"unsupported LLVM-CBE {name} declaration: {observed!r}",
        )
        require(
            len(re.findall(rf"\b{re.escape(name)}\b", raw_prefix)) == 1,
            f"unexpected LLVM-CBE {name} prefix reference",
        )
        declarations.append(expected)
        names.append(name)
    return declarations, names


_CBE_FPCLASS_HELPERS = {
    "llvm_cbe_is_fpclass_f32": """static __forceinline bool llvm_cbe_is_fpclass_f32(float value, uint32_t mask) {
  union { float fp; uint32_t bits; } repr;
  uint32_t magnitude;
  uint32_t class_mask;
  repr.fp = value;
  magnitude = repr.bits & UINT32_C(0x7fffffff);
  if (magnitude > UINT32_C(0x7f800000))
    class_mask = (repr.bits & UINT32_C(0x00400000)) ? UINT32_C(0x002) : UINT32_C(0x001);
  else if (magnitude == UINT32_C(0x7f800000))
    class_mask = (repr.bits & UINT32_C(0x80000000)) ? UINT32_C(0x004) : UINT32_C(0x200);
  else if (magnitude == 0)
    class_mask = (repr.bits & UINT32_C(0x80000000)) ? UINT32_C(0x020) : UINT32_C(0x040);
  else if (magnitude < UINT32_C(0x00800000))
    class_mask = (repr.bits & UINT32_C(0x80000000)) ? UINT32_C(0x010) : UINT32_C(0x080);
  else
    class_mask = (repr.bits & UINT32_C(0x80000000)) ? UINT32_C(0x008) : UINT32_C(0x100);
  return (mask & class_mask) != 0;
}""",
    "llvm_cbe_is_fpclass_f64": """static __forceinline bool llvm_cbe_is_fpclass_f64(double value, uint32_t mask) {
  union { double fp; uint64_t bits; } repr;
  uint64_t magnitude;
  uint32_t class_mask;
  repr.fp = value;
  magnitude = repr.bits & UINT64_C(0x7fffffffffffffff);
  if (magnitude > UINT64_C(0x7ff0000000000000))
    class_mask = (repr.bits & UINT64_C(0x0008000000000000)) ? UINT32_C(0x002) : UINT32_C(0x001);
  else if (magnitude == UINT64_C(0x7ff0000000000000))
    class_mask = (repr.bits & UINT64_C(0x8000000000000000)) ? UINT32_C(0x004) : UINT32_C(0x200);
  else if (magnitude == 0)
    class_mask = (repr.bits & UINT64_C(0x8000000000000000)) ? UINT32_C(0x020) : UINT32_C(0x040);
  else if (magnitude < UINT64_C(0x0010000000000000))
    class_mask = (repr.bits & UINT64_C(0x8000000000000000)) ? UINT32_C(0x010) : UINT32_C(0x080);
  else
    class_mask = (repr.bits & UINT64_C(0x8000000000000000)) ? UINT32_C(0x008) : UINT32_C(0x100);
  return (mask & class_mask) != 0;
}""",
}


def extract_cbe_fpclass_helpers(
    raw_prefix: str, payload: str
) -> tuple[list[str], list[str]]:
    """Retain exact helpers emitted by the locked scalar fpclass lowering."""

    helper_pattern = re.compile(
        r"^static __forceinline bool (llvm_cbe_is_fpclass_f(?:32|64))"
        r"\([^\n]*\) \{\n.*?^\}\s*$",
        re.MULTILINE | re.DOTALL,
    )
    observed: dict[str, str] = {}
    for match in helper_pattern.finditer(raw_prefix):
        name = match.group(1)
        require(name not in observed, f"duplicate LLVM-CBE fpclass helper: {name}")
        observed[name] = match.group(0).strip()

    referenced = sorted(set(re.findall(
        r"\b(llvm_cbe_is_fpclass_f(?:32|64))\s*\(", mask_c_data(payload)
    )))
    require(
        referenced == sorted(observed),
        "LLVM-CBE fpclass helper definitions do not match payload calls: "
        f"defined {sorted(observed)!r}, referenced {referenced!r}",
    )
    residual = sorted(set(re.findall(
        r"\bllvm_cbe_is_fpclass_[A-Za-z0-9_]+\b", mask_c_data(raw_prefix + payload)
    )))
    require(
        residual == referenced,
        f"unrecognized LLVM-CBE fpclass helper shape or width: {residual!r}",
    )
    for name, helper in observed.items():
        require(
            helper == _CBE_FPCLASS_HELPERS[name],
            f"unsupported LLVM-CBE fpclass helper body: {name}",
        )
    names = sorted(observed)
    return [_CBE_FPCLASS_HELPERS[name] for name in names], names


def extract_cbe_native_string_header(
    raw_prefix: str, payload: str
) -> tuple[list[str], list[str]]:
    """Bind lowered memory intrinsics to the target C library's native ABI."""

    memory_names = ("memcpy", "memmove", "memset")
    # Once <string.h> is present, every declaration owned by that header must
    # come from SDCC.  LLVM IR types intentionally erase pointee qualifiers and
    # may spell signed return types as unsigned integers, so retaining even a
    # seemingly ABI-compatible CBE prototype can conflict with the native one.
    string_names = (
        "memccpy", "memchr", "memcmp", "memcpy", "memmove", "memset",
        "memset_explicit", "strcat", "strchr", "strcmp", "strcoll",
        "strcpy", "strcspn", "strdup", "strlen", "strncat", "strncmp",
        "strncpy", "strndup", "strnlen", "strpbrk", "strrchr", "strsep",
        "strspn", "strstr", "strtok", "strxfrm",
    )
    name_pattern = "|".join(string_names)
    referenced = sorted(set(re.findall(
        rf"\b({name_pattern})\s*\(", mask_c_data(payload)
    )))
    declaration_pattern = re.compile(
        rf"^(?:extern\s+)?(?:void\s*\*|[A-Za-z_][A-Za-z0-9_]*)\s+"
        rf"({name_pattern})\([^;{{}}\n]*\)\s*;\s*$",
        re.MULTILINE,
    )
    definitions_pattern = re.compile(
        rf"^(?:static\s+)?(?:void\s*\*|[A-Za-z_][A-Za-z0-9_]*)\s+"
        rf"({name_pattern})\([^;{{}}\n]*\)\s*\{{\s*$",
        re.MULTILINE,
    )
    declarations = sorted(set(declaration_pattern.findall(payload)))
    definitions = sorted(set(definitions_pattern.findall(payload)))

    exact_include = "#include <string.h>"
    exact_count = len(re.findall(
        r"^#include <string\.h>\s*$", raw_prefix, re.MULTILINE
    ))
    include_spellings = re.findall(
        r"^\s*#\s*include\s*[<\"]string\.h[>\"]\s*$",
        raw_prefix,
        re.MULTILINE,
    )
    referenced_memory = sorted(set(referenced).intersection(memory_names))
    require(
        exact_count <= 1 and len(include_spellings) == exact_count,
        "LLVM-CBE native <string.h> marker is not exact and unique: "
        f"exact includes {exact_count}, all spellings {len(include_spellings)}",
    )

    if exact_count == 0:
        # CBE emits ordinary source-level string calls and their IR-derived
        # prototypes even when no memory intrinsic was lowered.  Those
        # declarations must remain in the payload: synthesizing <string.h>
        # here would replace their audited LLVM ABI with the target libc ABI.
        # A direct call with neither a declaration nor a definition is instead
        # the fail-closed signature of a missing intrinsic header marker.
        locally_bound = set(declarations).union(definitions)
        unbound = sorted(set(referenced).difference(locally_bound))
        require(
            not unbound,
            "LLVM-CBE string calls lack both the native <string.h> marker "
            f"and local declarations/definitions: {unbound!r}",
        )
        return [], []

    # The exact include is CBE's positive marker that this translation unit
    # lowered at least one llvm.mem* intrinsic.  In this mode the target
    # string header owns every standard declaration, including ordinary
    # source-level calls that happen to share the same translation unit.
    require(
        bool(referenced_memory),
        "LLVM-CBE native <string.h> marker has no lowered memory call",
    )
    require(
        not declarations and not definitions,
        "LLVM-CBE emitted native memory function declaration/definition(s): "
        f"declarations {declarations!r}, definitions {definitions!r}",
    )
    return [exact_include], referenced


def normalize_cbe_function_typedefs(
    payload: str,
) -> tuple[str, list[str], list[str]]:
    """Sort llvm-cbe's semantically unordered `l_fptr_*` typedef block.

    LLVM-CBE can iterate the function-type set in a different order between
    otherwise identical processes.  Restrict normalization to its dedicated
    one-line typedef block and reject any unfamiliar nonblank line so the
    adapter cannot silently reorder arbitrary C declarations.
    """

    function_marker = "\n/* Function definitions */\n"
    type_marker = "\n/* Types Definitions */\n"
    require_count(payload, function_marker, 1, "function typedef marker")
    require_count(payload, type_marker, 1, "type definition marker")
    prefix, after_function_marker = payload.split(function_marker, 1)
    typedef_block, suffix = after_function_marker.split(type_marker, 1)
    typedef_pattern = re.compile(
        r"^typedef\s+.+\s+(l_fptr_([0-9]+))\([^;]*\);\s*$"
    )
    typedefs: list[tuple[int, str, str]] = []
    unfamiliar: list[str] = []
    for line in typedef_block.splitlines():
        if not line.strip():
            continue
        match = typedef_pattern.fullmatch(line)
        if match is None:
            unfamiliar.append(line)
            continue
        typedefs.append((int(match.group(2)), match.group(1), line.rstrip()))
    require(not unfamiliar, f"unexpected function typedef block line(s): {unfamiliar!r}")
    # Programs with no indirect calls legitimately have no function typedefs.
    names = [name for _, name, _ in typedefs]
    require(len(names) == len(set(names)), "duplicate LLVM-CBE function typedef alias")
    ordered = sorted(typedefs, key=lambda entry: (entry[0], entry[1], entry[2]))
    before = [line for _, _, line in typedefs]
    after = [line for _, _, line in ordered]
    rewritten = (
        prefix
        + function_marker
        + "\n"
        + "\n".join(after)
        + "\n\n"
        + type_marker
        + suffix
    )
    return rewritten, before, after


def normalize_mcs251_indirect_calls(payload: str) -> tuple[str, list[str]]:
    """Use SDCC's integer bridge between equal-width program/data pointers."""
    types=set(re.findall(r'^typedef [^\n]+ (l_fptr_[0-9]+)\(',payload,re.M))
    pattern=re.compile(r'\(\((?P<type>l_fptr_[0-9]+)\*\)\(void\*\)'
                       r'(?P<value>_[0-9]+|\(\(\(void\*\)\(uintptr_t\)_[0-9]+\)\))\)(?=\()')
    records=[]
    def rewrite(match):
        require(match['type'] in types,'indirect call has no function typedef')
        records.append(match['type']+':'+match['value'])
        return '(('+match['type']+'*)(uintptr_t)'+match['value']+')'
    return replace_c_code(pattern,rewrite,payload),records


def normalize_cbe_fabs_helpers(payload: str) -> tuple[str, list[str]]:
    """Clear the IEEE binary32 sign bit, preserving NaN payloads and +0."""
    name='llvm_OC_fabs_OC_f32'
    pattern=re.compile(r'^static __forceinline float '+name+r'\(float a\) \{\n  float r = fabsf\(a\);\n  return r;\n\}',re.M)
    replacement='''static __forceinline float llvm_OC_fabs_OC_f32(float a) {
  union { float value; uint32_t bits; } repr;
  repr.value = a;
  repr.bits &= 0x7fffffffUL;
  return repr.value;
}'''
    rewritten,count=pattern.subn(replacement,payload)
    definitions=re.findall(r'^static __forceinline float llvm_OC_fabs_OC_[A-Za-z0-9_]+\(',mask_c_data(payload),re.M)
    require(count==len(definitions) and count<=1,'unsupported CBE fabs helper body or width')
    return rewritten, [name] if count else []


def remove_sdcc_duplicate_const_declarations(
    payload: str,
) -> tuple[str, list[str], list[str]]:
    declaration_marker = "\n/* Global Variable Declarations */\n"
    function_marker = "\n/* Function Declarations */\n"
    definition_marker = "\n/* Global Variable Definitions and Initialization */\n"
    require_count(payload, declaration_marker, 1, "global variable declaration marker")
    require_count(payload, function_marker, 1, "function declaration marker")
    require_count(payload, definition_marker, 1, "global variable definition marker")

    prefix, after_declaration = payload.split(declaration_marker, 1)
    declaration_block, after_functions = after_declaration.split(function_marker, 1)
    function_declarations, definitions = after_functions.split(
        definition_marker, 1
    )
    removed: list[str] = []
    kept_lines: list[str] = []
    declaration_pattern = re.compile(
        r"^const\s+static\s+.+\s+([A-Za-z_][A-Za-z0-9_]*)\s*;\s*$"
    )
    for line in declaration_block.splitlines():
        match = declaration_pattern.match(line)
        if match is None:
            kept_lines.append(line)
            continue
        symbol = match.group(1)
        definition_pattern = re.compile(
            rf"^static\s+const\s+.+\s+{re.escape(symbol)}(?:\s*=\s*.+)?;\s*$",
            re.MULTILINE,
        )
        require(
            definition_pattern.search(definitions) is not None,
            f"const forward declaration has no matching definition: {symbol}",
        )
        removed.append(symbol)
    require(removed, "expected at least one LLVM-CBE const forward declaration")
    zero_initialized: list[str] = []
    zero_array_pattern = re.compile(
        r"^static\s+const\s+struct\s+(l_array_([0-9]+)_uint8_t)\s+"
        r"([A-Za-z_][A-Za-z0-9_]*)\s*;\s*$",
        re.MULTILINE,
    )

    def initialize_zero_array(match: re.Match[str]) -> str:
        type_name, element_count, symbol = match.groups()
        type_pattern = re.compile(
            rf"^struct\s+{re.escape(type_name)}\s*\{{\s*"
            rf"uint8_t\s+array\[{element_count}\];\s*\}};\s*$",
            re.MULTILINE,
        )
        require(
            type_pattern.search(payload) is not None,
            f"cannot validate zero aggregate type for {symbol}: {type_name}",
        )
        zero_initialized.append(symbol)
        # SDCC MCS251 rejects the standard `{ 0 }` spelling for this wrapper;
        # retain the nested array braces emitted for non-empty constants.
        return f"static const struct {type_name} {symbol} = {{ {{ 0 }} }};"

    definitions = zero_array_pattern.sub(initialize_zero_array, definitions)

    rewritten = (
        prefix
        + declaration_marker
        + "\n".join(kept_lines)
        + "\n"
        + function_marker
        + function_declarations
        + definition_marker
        + definitions
    )
    return rewritten, removed, zero_initialized


def normalize_cbe_address_roundtrips(payload: str) -> tuple[str, list[str]]:
    """Canonicalize CBE's `*(&array[index])` without changing C semantics.

    SDCC MCS251 selects a near `@r1` load for the redundant spelling even
    when the base is a generic pointer.  The canonical `array[index]` form
    selects `__gptrget`/`__gptrput`, preserving code/xdata pointer tags.
    Restrict the rewrite to complete one-line CBE temporaries and reject any
    residual instance so a new output shape cannot silently bypass the gate.
    """

    load_pattern = re.compile(
        r"^(?P<indent>\s*)(?P<destination>_[0-9]+\s*=\s*)"
        r"\*\((?P<type>[A-Za-z_][A-Za-z0-9_]*\*+)\)"
        r"\(\(\(&\(\((?P=type)\)(?P<base>_[0-9]+)\)"
        r"\[(?P<index>.+)\]\)\)\);\s*$"
    )
    byte_offset_pointer_load_pattern = re.compile(
        r"^(?P<indent>\s*)(?P<destination>_[0-9]+\s*=\s*)"
        r"\*\((?P<load_type>void\*\*|(?:u?int(?:8|16|32|64)_t|float|double)\*)\)"
        r"\(\(\(&\(\((?P<element_type>uint8_t\*)\)(?P<base>_[0-9]+)\)"
        r"\[(?P<index>.+)\]\)\)\);\s*$"
    )
    byte_offset_pointer_store_pattern = re.compile(
        r"^(?P<indent>\s*)\*\((?P<store_type>void\*\*|(?:u?int(?:8|16|32|64)_t|float|double)\*)\)"
        r"\(\(\(&\(\(uint8_t\*\)(?P<base>_[0-9]+)\)"
        r"\[(?P<index>.+)\]\)\)\)\s*=\s*(?P<value>.+);\s*$"
    )
    store_pattern = re.compile(
        r"^(?P<indent>\s*)"
        r"\*\((?P<type>[A-Za-z_][A-Za-z0-9_]*\*+)\)"
        r"\(\(\(&\(\((?P=type)\)(?P<base>_[0-9]+)\)"
        r"\[(?P<index>.+)\]\)\)\)\s*=\s*(?P<value>.+);\s*$"
    )
    rewritten_lines: list[str] = []
    rewrites: list[str] = []
    for line_number, line in enumerate(payload.splitlines(), start=1):
        load_match = load_pattern.match(line)
        if load_match is not None:
            fields = load_match.groupdict()
            rewritten_lines.append(
                f"{fields['indent']}{fields['destination']}"
                f"(({fields['type']}){fields['base']})[{fields['index']}];"
            )
            rewrites.append(f"line {line_number}: generic array load")
            continue
        byte_offset_pointer_load_match = byte_offset_pointer_load_pattern.match(line)
        if byte_offset_pointer_load_match is not None:
            fields = byte_offset_pointer_load_match.groupdict()
            rewritten_lines.append(
                f"{fields['indent']}{fields['destination']}"
                f"*({fields['load_type']})"
                f"((({fields['element_type']}){fields['base']}) + "
                f"({fields['index']}));"
            )
            rewrites.append(
                f"line {line_number}: generic byte-offset pointer load"
            )
            continue
        byte_store = byte_offset_pointer_store_pattern.match(line)
        if byte_store is not None:
            fields = byte_store.groupdict()
            rewritten_lines.append(
                f"{fields['indent']}*({fields['store_type']})(((uint8_t*){fields['base']}) + "
                f"({fields['index']})) = {fields['value']};"
            )
            rewrites.append(f"line {line_number}: generic byte-offset pointer store")
            continue
        store_match = store_pattern.match(line)
        if store_match is not None:
            fields = store_match.groupdict()
            rewritten_lines.append(
                f"{fields['indent']}(({fields['type']}){fields['base']})"
                f"[{fields['index']}] = {fields['value']};"
            )
            rewrites.append(f"line {line_number}: generic array store")
            continue
        rewritten_lines.append(line)

    rewritten = "\n".join(rewritten_lines)
    residual = re.compile(
        r"\*\([A-Za-z_][A-Za-z0-9_]*\*+\)\(\(\(&\(\("
        r"[A-Za-z_][A-Za-z0-9_]*\*+\)_[0-9]+\)\["
    )
    require(
        residual.search(rewritten) is None,
        "unhandled LLVM-CBE *(&generic_array[index]) output shape",
    )
    return rewritten, rewrites


def normalize_cbe_vtable_addresses(payload: str) -> tuple[str, list[str]]:
    """Express audited vtable/VTT addresses as pointer arithmetic.

    A vtable without methods can have its address point exactly one past the
    last entry. SDCC diagnoses CBE's redundant &array[N] as a load. Array + N
    is an address expression and also permits an explicit generic-pointer
    conversion where opaque LLVM pointers erased pointee constness.
    """
    structures = dict(re.findall(r'^struct ([A-Za-z_][A-Za-z0-9_]*) \{\n(.*?)^\};',
                                 payload, re.MULTILINE | re.DOTALL))
    tables = {name: typename for typename, name in re.findall(
        r'^static const struct ([A-Za-z_][A-Za-z0-9_]*) (_ZT[VTC][A-Za-z0-9_]+)\s*=',
        payload, re.MULTILINE)}
    # Propagate const along the exact read-only VTT argument aliases in base
    # constructors before materializing calls from read-only code memory.
    vtt_callees = set(re.findall(
        r'^  ([A-Za-z_][A-Za-z0-9_]*)\([^;\n]*&_ZTT[A-Za-z0-9_]+[^;\n]*\);$',
        payload, re.MULTILINE))
    for callee in sorted(vtt_callees):
        function = re.search(
            rf'^static void {re.escape(callee)}\(void\* _[0-9]+, void\* (?P<arg>_[0-9]+)\) \{{\n(?P<body>.*?)^\}}',
            payload, re.MULTILINE | re.DOTALL)
        require(function is not None, 'unsupported VTT constructor signature')
        aliases = {function.group('arg')}
        body = function.group('body')
        copies = re.findall(r'^  (_[0-9]+) = (_[0-9]+);$', body, re.MULTILINE)
        for _ in range(len(copies)+1):
            aliases.update(dst for dst, src in copies if src in aliases)
        for line in body.splitlines():
            if not any(re.search(r'\b'+re.escape(alias)+r'\b', line) for alias in aliases):
                continue
            require(
                re.fullmatch(r'  void\* _[0-9]+;(?:\s*/\*.*\*/)?', line) is not None
                or re.fullmatch(r'  _[0-9]+ = _[0-9]+;', line) is not None
                or re.fullmatch(r'  _[0-9]+ = \*\(void\*\*\)_[0-9]+;', line) is not None,
                'VTT argument escapes the read-only constructor alias chain')
        for alias in aliases:
            body = re.sub(r'\bvoid\* '+re.escape(alias)+r'\b', 'const void* '+alias, body)
            body = body.replace('*(void**)'+alias+';', '*(void* const*)'+alias+';')
        replacement = function.group(0).replace(function.group('body'), body)
        replacement = replacement.replace(', void* '+function.group('arg')+')', ', const void* '+function.group('arg')+')')
        payload = payload[:function.start()] + replacement + payload[function.end():]
        payload = re.sub(rf'^(static void {re.escape(callee)}\(void\* _[0-9]+, )void\* (_[0-9]+\))',
                         r'\1const void* \2', payload, flags=re.MULTILINE)
    pattern = re.compile(
        r'\(\(\(&\(&\(&(?P<table>_ZT[VC][A-Za-z0-9_]+)\)->field(?P<field>[0-9]+)'
        r'\)->array\[\(\(int32_t\)(?P<index>[0-9]+)\)\]\)\)\)')
    records = []

    # LLVM's optimizer can flatten a vtable GEP to a byte address. Validate
    # the entire table layout and slot boundary before restoring __code.
    flat_pattern = re.compile(
        r'\(\(\(&\(\(uint8_t\*\)\(\(void\*\)\(const void\*\)&'
        r'(?P<table>_ZT[VC][A-Za-z0-9_]+)\)\)\['
        r'\(\(signed _BitInt\(24\)\)(?P<offset>[0-9]+)\)\]\)\)\)')

    def rewrite_flat(match):
        name, offset = match.group('table'), int(match.group('offset'))
        require(name in tables, 'flattened vtable address lacks local definition')
        fields = re.findall(r'  struct ([A-Za-z_][A-Za-z0-9_]*) field([0-9]+);\n',
                            structures.get(tables[name], ''))
        require(fields and ''.join(f'  struct {t} field{n};\n' for t,n in fields)
                == structures[tables[name]], 'unsupported flattened vtable layout')
        size = 0
        for typename, _ in fields:
            array = re.fullmatch(r'  void\* array\[([0-9]+)\];\n', structures.get(typename, ''))
            require(array is not None, 'flattened vtable contains a non-pointer array')
            size += 3 * int(array.group(1))
        require(offset % 3 == 0 and offset <= size, 'flattened vtable address is outside its pointer slots')
        records.append(name+'+bytes:'+str(offset))
        return f'((void *)((const uint8_t __code *)&{name} + {offset}))'

    payload = flat_pattern.sub(rewrite_flat, payload)

    def rewrite(match):
        name, field, index = match.group('table', 'field', 'index')
        require(name in tables, 'vtable address lacks local definition')
        member = re.search(r'  struct ([A-Za-z_][A-Za-z0-9_]*) field'+field+r';',
                           structures.get(tables[name], ''))
        require(member is not None, 'vtable address references unknown field')
        array = re.fullmatch(r'  void\* array\[([0-9]+)\];\n', structures.get(member.group(1), ''))
        require(array is not None and int(index) <= int(array.group(1)),
                'vtable address point exceeds its array')
        records.append(name+'.field'+field+'+'+index)
        offset = int(index) * 3
        for previous in range(int(field)):
            entry = re.search(r'  struct ([A-Za-z_][A-Za-z0-9_]*) field'+str(previous)+r';', structures[tables[name]])
            require(entry is not None, 'vtable layout contains a non-pointer-array field')
            dim = re.fullmatch(r'  void\* array\[([0-9]+)\];\n', structures.get(entry.group(1), ''))
            require(dim is not None, 'vtable layout contains a non-pointer-array field')
            offset += int(dim.group(1)) * 3
        return f'((void *)((const uint8_t __code *)&{name} + {offset}))'

    payload = pattern.sub(rewrite, payload)
    vtt_pattern = re.compile(
        r'\(\(\(&\(&(?P<table>_ZTT[A-Za-z0-9_]+)\)->array'
        r'\[\(\(int64_t\)(?P<index>[0-9]+)\)\]\)\)\)')

    def rewrite_vtt(match):
        name, index = match.group('table', 'index')
        require(name in tables, 'VTT address lacks local definition')
        array = re.fullmatch(r'  void\* array\[([0-9]+)\];\n', structures.get(tables[name], ''))
        require(array is not None and int(index) < int(array.group(1)),
                'VTT argument exceeds its array')
        records.append(name+'+'+index)
        return f'((const void *)((const uint8_t __code *)&{name} + {int(index)*3}))'

    return vtt_pattern.sub(rewrite_vtt, payload), records


def normalize_cbe_integer_negation(payload: str) -> tuple[str, list[str]]:
    """Keep LLVM's modular negation defined at each signed minimum.

    CBE emits a signed parameter and unary minus even for LLVM `sub 0, x`
    originating from unsigned C++. In C, negating INT_MIN is undefined.
    Calculate in an unsigned type at least as wide, then truncate normally.
    """
    pattern = re.compile(
        r'^static __forceinline uint(?P<bits>8|16|32|64)_t '
        r'(?P<name>llvm_neg_u(?P=bits))\(int(?P=bits)_t a\) \{\n'
        r'  uint(?P=bits)_t r = -a;\n  return r;\n\}', re.MULTILINE,
    )
    names: list[str] = []

    def rewrite(match: re.Match[str]) -> str:
        bits = match['bits']
        math_bits = '64' if bits == '64' else '32'
        names.append(match['name'])
        return (f'static __forceinline uint{bits}_t {match["name"]}(int{bits}_t a) {{\n'
                f'  uint{bits}_t r = (uint{bits}_t)((uint{math_bits}_t)0 - (uint{math_bits}_t)a);\n'
                '  return r;\n}')

    result = replace_c_code(pattern, rewrite, payload)
    require(len(names) == len(set(names)), 'duplicate CBE integer negation helper')
    return result, names


def normalize_cbe_u24_negation(payload: str) -> tuple[str, int]:
    """Repair llvm-cbe's malformed helper for a non-native 24-bit integer.

    The helper is emitted only when IR contains arithmetic on the exact i24
    representation of a target pointer.  LLVM arithmetic is modulo 2^24;
    spell that operation with SDCC's 32-bit ``uint32_t`` and an explicit mask.
    Accept only the byte-for-byte helper shape observed from the pinned CBE.
    """

    malformed = (
        "static __forceinline uint32_t llvm_neg_u24(int32_t a) {\n"
        "  uint32_t r = (-a;\n"
        "  return r;\n"
        "}"
    )
    replacement = (
        "static __forceinline uint32_t llvm_neg_u24(int32_t a) {\n"
        "  uint32_t r = (0UL - (uint32_t)a) & 16777215UL;\n"
        "  return r;\n"
        "}"
    )
    count = payload.count(malformed)
    require(count <= 1, "duplicate malformed llvm-cbe i24 negation helpers")
    rewritten = payload.replace(malformed, replacement)
    require(
        "uint32_t r = (-a;" not in rewritten,
        "unhandled malformed llvm-cbe integer negation helper",
    )
    return rewritten, count


def normalize_cbe_u32_power_of_two_division(
    payload: str,
) -> tuple[str, list[dict[str, object]]]:
    """Lower CBE's u32 division/remainder by powers of two explicitly.

    LLVM-CBE deliberately keeps integer operations in small inline helpers.
    At ``-O0`` SDCC MCS251 can therefore lower a constant divisor through its
    generic 32-bit division runtime instead of selecting a shift or mask.  In
    particular, ArduinoJson's second memory-pool ID uses ``/ 256`` and
    ``% 256``; the lowered division produces the wrong pool index on the
    qualified target, which later prevents document traversal from terminating.

    Restrict this semantics-preserving normalization to the exact CBE call
    shape with a numeric SSA temporary and an unsigned-32 helper.  Other
    expressions and non-power-of-two divisors remain untouched, so this pass
    cannot duplicate side effects or silently broaden the accepted C shape.
    """

    call_pattern = re.compile(
        r"\bllvm_(?P<operation>udiv|urem)_u32\("
        r"(?P<dividend>_[0-9]+),\s*"
        r"(?P<divisor>(?:0[xX][0-9A-Fa-f]+|[0-9]+)(?:[uUlL]{0,2}))\)"
    )
    rewrites: list[dict[str, object]] = []

    def rewrite(match: re.Match[str]) -> str:
        literal = match.group("divisor")
        digits = literal.rstrip("uUlL")
        divisor = int(digits, 0)
        if (divisor < 1 or divisor > 0x80000000 or
                divisor & (divisor - 1)):
            return match.group(0)

        shift = divisor.bit_length() - 1
        dividend = match.group("dividend")
        operation = match.group("operation")
        rewrites.append({
            "operation": operation,
            "divisor": divisor,
            "shift": shift,
        })
        if operation == "udiv":
            return f"(((uint32_t){dividend}) >> {shift}u)"
        mask = divisor - 1
        return f"(((uint32_t){dividend}) & {mask}UL)"

    return replace_c_code(call_pattern, rewrite, payload), rewrites


def decode_cbe_byte_string(contents: str) -> list[int]:
    """Decode the restricted C string spelling emitted for LLVM i8 arrays."""

    simple_escapes = {
        "a": 7,
        "b": 8,
        "f": 12,
        "n": 10,
        "r": 13,
        "t": 9,
        "v": 11,
        "\\": 92,
        "'": 39,
        '"': 34,
        "?": 63,
    }
    decoded: list[int] = []
    index = 0
    while index < len(contents):
        character = contents[index]
        if character != "\\":
            value = ord(character)
            require(value <= 255, "non-byte character in LLVM-CBE i8 initializer")
            decoded.append(value)
            index += 1
            continue
        index += 1
        require(index < len(contents), "truncated LLVM-CBE byte escape")
        escape = contents[index]
        if escape in simple_escapes:
            decoded.append(simple_escapes[escape])
            index += 1
            continue
        if escape == "x":
            index += 1
            start = index
            while index < len(contents) and contents[index] in "0123456789abcdefABCDEF":
                index += 1
            require(index > start, "empty hexadecimal LLVM-CBE byte escape")
            value = int(contents[start:index], 16)
            require(value <= 255, "wide hexadecimal LLVM-CBE byte escape")
            decoded.append(value)
            continue
        if escape in "01234567":
            start = index
            index += 1
            while (index < len(contents) and index - start < 3 and
                   contents[index] in "01234567"):
                index += 1
            value = int(contents[start:index], 8)
            require(value <= 255, "wide octal LLVM-CBE byte escape")
            decoded.append(value)
            continue
        raise AuditError(f"unsupported LLVM-CBE byte escape: \\{escape}")
    return decoded


def _c_initializer_items(text: str) -> list[str]:
    """Split one brace level, keeping nested expressions and literals intact."""
    require(text.startswith('{') and text.endswith('}'), 'aggregate initializer needs braces')
    inner = text[1:-1]
    masked = mask_c_data(inner)
    stack: list[str] = []
    pairs = {')': '(', ']': '[', '}': '{'}
    result: list[str] = []
    start = 0
    for index, char in enumerate(masked):
        if char in '([{':
            stack.append(char)
        elif char in ')]}':
            require(bool(stack) and stack.pop() == pairs[char], 'unbalanced C initializer')
        elif char == ',' and not stack:
            result.append(inner[start:index].strip())
            start = index + 1
    require(not stack, 'unbalanced C initializer')
    tail = inner[start:].strip()
    if tail:
        result.append(tail)
    require(all(result), 'empty C initializer element')
    return result


def normalize_cbe_exact_byte_array_initializers(payload: str) -> tuple[str, list[str]]:
    """Normalize byte literals and implicit zeroes using actual CBE types.

    Descend through array wrappers and records at any depth. A full-width
    byte literal must not acquire an extra NUL in SDCC. Uninitialized const
    aggregates need explicit, recursively braced zero initialization there.
    Only CBE global definitions are visited; expressions/literal contents
    elsewhere are never interpreted as declarations.
    """
    types: dict[str, list[tuple[str, int | None]]] = {}
    for match in re.finditer(r'^struct (\w+) \{\n(.*?)^\};', payload, re.MULTILINE | re.DOTALL):
        fields: list[tuple[str, int | None]] = []
        for line in match.group(2).splitlines():
            field = re.fullmatch(r'\s*(.+?)\s+(?:array|field[0-9]+)(?:\[([0-9]+)\])?;\s*', line)
            if field is None:
                fields = []
                break
            fields.append((field[1], int(field[2]) if field[2] else None))
        if fields:
            require(match[1] not in types, f'duplicate CBE aggregate type: {match[1]}')
            wrapper = re.fullmatch(r'l_array_([0-9]+)_(.+)', match[1])
            if wrapper:
                element = wrapper[2]
                if element.startswith('struct_AC_'):
                    element = 'struct ' + element[len('struct_AC_'):]
                require(len(fields) == 1 and fields[0][1] == int(wrapper[1]) and
                        (element != 'uint8_t' or fields[0][0] == 'uint8_t') and
                        (not element.startswith('struct ') or fields[0][0] == element),
                        f'CBE array wrapper name/layout mismatch: {match[1]}')
            types[match[1]] = fields
    literal = re.compile(r'"((?:\\.|[^"\\])*)"\Z', re.DOTALL)

    def zero(type_name: str, count: int | None, active: tuple[str, ...] = ()) -> str:
        if count is not None:
            require(count > 0, 'zero-sized CBE array is unsupported')
            return '{ ' + zero(type_name, None, active) + ' }'
        if type_name.startswith('struct '):
            name = type_name[7:]
            require(name in types and name not in active, f'unsupported/cyclic CBE aggregate: {name}')
            return '{ ' + ', '.join(zero(t, n, active + (name,)) for t, n in types[name]) + ' }'
        return '0'

    def visit(type_name: str, count: int | None, value: str, symbol: str) -> tuple[str, bool]:
        if count is not None:
            text_match = literal.fullmatch(value)
            if text_match:
                require(type_name == 'uint8_t', f'non-byte CBE string initializer in {symbol}')
                values = decode_cbe_byte_string(text_match[1])
                require(len(values) in (count - 1, count), f'LLVM-CBE byte initializer length mismatch for {symbol}')
                if len(values) == count:
                    return '{ ' + ', '.join(f'{byte}u' for byte in values) + ' }', True
                return value, False
            items = _c_initializer_items(value)
            require(0 < len(items) <= count, f'CBE array initializer extent mismatch for {symbol}')
            if type_name == 'uint8_t':
                for item in items:
                    numeric = re.fullmatch(r'(0[xX][0-9a-fA-F]+|[0-9]+)[uUlL]*', item)
                    require(numeric is not None, f'unsupported CBE byte value in {symbol}')
                    digits = numeric[1]
                    number = int(digits, 16 if digits.lower().startswith('0x') else 10)
                    require(number <= 255, f'invalid CBE byte value in {symbol}')
            children = [visit(type_name, None, item, symbol) for item in items]
        elif type_name.startswith('struct '):
            name = type_name[7:]
            require(name in types, f'unsupported CBE aggregate definition: {name}')
            items = _c_initializer_items(value)
            fields = types[name]
            require(0 < len(items) <= len(fields), f'CBE record initializer extent mismatch for {symbol}')
            children = [visit(t, n, item, symbol) for (t, n), item in zip(fields, items)]
        else:
            return value, False
        changed = any(change for _, change in children)
        return ('{ ' + ', '.join(item for item, _ in children) + ' }' if changed else value), changed

    rewritten: list[str] = []
    definition = re.compile(r'^((?:static )?(?:const )?)struct (\w+) (\w+)(?: = (.*))?;$', re.MULTILINE)

    def rewrite(match: re.Match[str]) -> str:
        prefix, type_name, symbol, value = match.groups()
        if type_name not in types:
            # Not a validated CBE record shape; later compile/audit rejects it.
            return match[0]
        if value is None:
            if 'const ' not in prefix:
                return match[0]  # Mutable zero storage is initialized by startup.
            value = zero('struct ' + type_name, None)
            changed = True
        else:
            value, changed = visit('struct ' + type_name, None, value, symbol)
        if not changed:
            return match[0]
        rewritten.append(symbol)
        return f'{prefix}struct {type_name} {symbol} = {value};'

    marker = '\n/* Global Variable Definitions and Initialization */\n'
    if marker in payload:
        prefix, definitions = payload.split(marker, 1)
        end_marker = '\n/* LLVM Intrinsic Builtin Function Bodies */\n'
        require(end_marker in definitions, 'missing CBE end of global definitions')
        definitions, suffix = definitions.split(end_marker, 1)
        result = prefix + marker + definition.sub(rewrite, definitions) + end_marker + suffix
    else:
        # Also support isolated regression fragments containing only types and
        # definitions. Never include a CBE declaration section in this mode.
        require('/* Global Variable Declarations */' not in payload, 'missing CBE global definition marker')
        result = definition.sub(rewrite, payload)
    return result, rewritten


def normalize_cbe_nested_byte_array_initializers(payload: str) -> tuple[str, list[str]]:
    """Compatibility entry point; the type-driven pass handles every depth."""
    return normalize_cbe_exact_byte_array_initializers(payload)


def normalize_cbe_stateless_struct_returns(
    payload: str,
) -> tuple[str, list[str]]:
    """Give CBE's synthetic storage for an empty C++ return object a value.

    LLVM has no observable payload for an empty C++ tag, but LLVM-CBE must
    materialize one byte when it represents that value as a C struct.  The
    pinned CBE consequently emits an uninitialized ``StructReturn`` for
    ArduinoJson's ``AllowAllFilter::operator[]``.  Returning an uninitialized
    C object is undefined even though the corresponding C++ object is
    stateless.  Normalize only the exact empty-tag shape and fail closed if
    its generated body changes.
    """

    empty_type = (
        "l_struct_struct_OC_ArduinoJson_KD__KD_V743JB42_KD__KD_detail_KD__KD_"
        "integral_constant_OC_10"
    )
    type_definition = re.compile(
        rf"^struct {re.escape(empty_type)} \{{\n"
        r"  uint8_t field0;\n"
        r"\};$",
        re.MULTILINE,
    )
    target_symbol = re.compile(
        r"_ZNK11ArduinoJson8V743JB426detail14AllowAllFilterixI"
        r"[A-Za-z0-9_]+EES2_RKT_"
    )
    function_pattern = re.compile(
        r"^static struct (?P<type>[A-Za-z_][A-Za-z0-9_]*) "
        r"(?P<symbol>[A-Za-z_][A-Za-z0-9_]*)"
        r"\((?P<arguments>[^\n]*)\) \{\n(?P<body>.*?)^\}$",
        re.MULTILINE | re.DOTALL,
    )
    normalized_symbols: list[str] = []

    def rewrite(match: re.Match[str]) -> str:
        return_type = match.group("type")
        symbol = match.group("symbol")
        body = match.group("body")
        declaration = (
            f"  struct {empty_type} StructReturn;  "
            "/* Struct return temporary */"
        )
        pointer_aliases = list(
            re.finditer(
                rf"^  struct {re.escape(return_type)}\* "
                r"(?P<alias>_[0-9]+) = &StructReturn;$",
                body,
                re.MULTILINE,
            )
        )
        placeholder_pointer_is_unobserved = (
            len(pointer_aliases) == 1
            and len(
                re.findall(
                    rf"\b{re.escape(pointer_aliases[0].group('alias'))}\b",
                    body,
                )
            )
            == 1
        )
        target_hint = "AllowAllFilterix" in symbol
        placeholder_hint = (
            return_type == empty_type
            and body.count(declaration) == 1
            and len(re.findall(r"\bStructReturn\b", body)) == 3
            and placeholder_pointer_is_unobserved
            and re.search(r"^  return StructReturn;$", body, re.MULTILINE)
            is not None
        )
        if not target_hint and not placeholder_hint:
            return match.group(0)

        require(
            target_symbol.fullmatch(symbol) is not None,
            f"unsupported stateless StructReturn function: {symbol}",
        )
        require(
            return_type == empty_type,
            f"unsupported stateless StructReturn type in {symbol}: {return_type}",
        )
        require(
            len(type_definition.findall(payload)) == 1,
            f"unsupported one-byte stateless return type definition in {symbol}",
        )
        require(
            re.fullmatch(
                r"void\* _[0-9]+, void\* _[0-9]+",
                match.group("arguments"),
            )
            is not None,
            f"unsupported stateless StructReturn arguments in {symbol}",
        )
        require(
            body.count(declaration) == 1,
            f"unsupported stateless StructReturn declaration in "
            f"{symbol}",
        )
        struct_return_uses = len(re.findall(r"\bStructReturn\b", body))
        return_is_final = (
            re.search(r"^  return StructReturn;\n?\Z", body, re.MULTILINE)
            is not None
        )
        require(
            struct_return_uses == 3
            and len(pointer_aliases) == 1
            and return_is_final,
            f"unsupported stateless StructReturn use in {symbol}: "
            f"uses={struct_return_uses}, pointer_aliases={len(pointer_aliases)}, "
            f"return_is_final={return_is_final}",
        )
        pointer_alias = pointer_aliases[0].group("alias")
        require(
            len(re.findall(rf"\b{re.escape(pointer_alias)}\b", body)) == 1,
            f"stateless StructReturn placeholder is observable in {symbol}",
        )
        normalized_symbols.append(symbol)
        initialized = declaration.replace(
            "StructReturn;", "StructReturn = { 0 };"
        )
        body = body.replace(declaration, initialized, 1)
        return match.group(0).replace(match.group("body"), body, 1)

    normalized = function_pattern.sub(rewrite, payload)
    residual = re.findall(
        r"^static struct [A-Za-z_][A-Za-z0-9_]* "
        r"[A-Za-z_][A-Za-z0-9_]*AllowAllFilterix[A-Za-z0-9_]*"
        r"\([^\n]*\) \{\n(?:(?!^\}$).)*?"
        r"StructReturn;  /\* Struct return temporary \*/",
        normalized,
        re.MULTILINE | re.DOTALL,
    )
    require(not residual, "uninitialized stateless StructReturn remains")
    return normalized, normalized_symbols


def normalize_cbe_single_block_pointer_temporaries(
    payload: str,
) -> tuple[str, list[str]]:
    """Eliminate pointer temporaries whose assignment dominates their use.

    SDCC warning 84 can lose the basic-block fact for a CBE pointer that is
    assigned and dereferenced in the same labelled block.  Prove the exact
    three-occurrence shape (declaration, assignment, dereference), reject any
    intervening label/control transfer, then forward the assigned expression
    into the existing destination.  This removes the redundant local so SDCC
    no longer needs to infer that fact.
    """

    function_pattern = re.compile(
        r"^(?P<header>(?:static )?[^\n]+ [A-Za-z_][A-Za-z0-9_]*"
        r"\([^\n]*\) \{\n)(?P<body>.*?)^\}$",
        re.MULTILINE | re.DOTALL,
    )
    eliminated: list[str] = []

    def rewrite_function(match: re.Match[str]) -> str:
        body = match.group("body")
        declarations = re.findall(r"^  void\* (_[0-9]+);\s*$", body, re.MULTILINE)
        for variable in declarations:
            occurrences = list(re.finditer(rf"\b{re.escape(variable)}\b", body))
            if len(occurrences) != 4:
                continue
            declaration = re.search(
                rf"^  void\* {re.escape(variable)};\s*$", body, re.MULTILINE
            )
            assignment = re.search(
                rf"^  {re.escape(variable)} = "
                r"\(\(&\(\(uint8_t\*\)_[0-9]+\)"
                r"\[\(\(int32_t\)-1\)\]\)\);\s*$",
                body,
                re.MULTILINE,
            )
            dereference = re.search(
                rf"^  \*\(uint8_t\*\){re.escape(variable)} = ",
                body,
                re.MULTILINE,
            )
            if declaration is None or assignment is None or dereference is None:
                continue
            if not (
                occurrences[0].start() == declaration.start() + declaration.group(0).find(variable)
                and occurrences[1].start() == assignment.start() + assignment.group(0).find(variable)
                and occurrences[2].start() > assignment.end()
                and occurrences[3].start() == dereference.start() + dereference.group(0).find(variable)
                and assignment.end() < dereference.start()
            ):
                continue
            between = body[assignment.end():dereference.start()]
            require(
                re.fullmatch(
                    rf"\n  (?P<destination>_[0-9]+) = {re.escape(variable)};\n",
                    between,
                ) is not None,
                f"control flow changed around {variable}",
            )
            forwarding = re.fullmatch(
                rf"\n  (?P<destination>_[0-9]+) = {re.escape(variable)};\n",
                between,
            )
            assert forwarding is not None
            destination = forwarding.group("destination")
            assignment_rhs = assignment.group(0).split(" = ", 1)[1]
            dereference_line = dereference.group(0).replace(
                variable, destination, 1
            )
            body = (
                body[:dereference.start()]
                + dereference_line
                + body[dereference.end():]
            )
            body = (
                body[:assignment.start()]
                + f"  {destination} = {assignment_rhs}\n"
                + body[dereference.start():]
            )
            body = (
                body[:declaration.start()]
                + body[declaration.end():]
            )
            eliminated.append(variable)
        return match.group("header") + body + "}"

    normalized = function_pattern.sub(rewrite_function, payload)
    return normalized, eliminated


def audit_and_adapt_cbe(
    raw: str,
    constructors: list[dict[str, object]],
    abi_identity_symbol: str,
) -> tuple[str, dict[str, object]]:
    marker = "\n/* Global Declarations */\n"
    require_count(raw, marker, 1, "LLVM-CBE global declaration marker")
    raw_prefix, payload = raw.split(marker, 1)
    fcmp_helpers, fcmp_helper_names = extract_cbe_fcmp_helpers(raw_prefix, payload)

    forbidden = [
        name for name, pattern in FORBIDDEN_CBE_PAYLOAD.items()
        if pattern.search(mask_c_data(payload))
    ]
    require(not forbidden, "forbidden LLVM-CBE payload: " + ", ".join(forbidden))

    required_cbe_symbols = {
        "string_concat": "_ZN6String6concatEPKc",
        "string_substring": "_ZNK6String9substringEjj",
        "string_replace": "_ZN6String7replaceERKS_S1_",
        "string_trim": "_ZN6String4trimEv",
        "string_to_int": "_ZNK6String5toIntEv",
        "print_string": "_ZN5Print5printERK6String",
        "print_virtual_buffer_write": "_ZN5Print5writeEPKhm",
    }
    missing_cbe_symbols = [
        label
        for label, symbol in required_cbe_symbols.items()
        if re.search(
            rf"^static\s+[^;\n]*\b{re.escape(symbol)}\([^;\n]*\)\s*\{{",
            payload,
            re.MULTILINE,
        )
        is None
    ]
    require(
        not missing_cbe_symbols,
        "LLVM-CBE omitted required String/Print canary body: "
        + ", ".join(missing_cbe_symbols),
    )

    declared_ctors = parse_cbe_ctor_declarations(payload)
    expected_c_names = [
        cbe_mangle(str(entry["llvm_symbol"])) for entry in constructors
    ]
    require(
        declared_ctors == expected_c_names,
        "LLVM-CBE constructor declarations do not preserve llvm.global_ctors order: "
        f"expected {expected_c_names!r}, observed {declared_ctors!r}",
    )
    require_count(
        payload,
        " __ATTRIBUTE_CTOR__",
        len(expected_c_names),
        "host constructor attribute removal",
    )
    payload = replace_c_token(payload, " __ATTRIBUTE_CTOR__", "")
    require("__ATTRIBUTE_CTOR__" not in mask_c_data(payload), "unconsumed constructor attribute")

    trap_count = mask_c_data(payload).count("__builtin_trap();")
    require(
        trap_count == 2,
        f"expected two audited abstract-base traps in CBE output, got {trap_count}",
    )
    payload = replace_c_token(payload, "__builtin_trap();", "stcxx_runtime_panic(5);")

    payload, function_typedef_order_before, function_typedef_order_after = (
        normalize_cbe_function_typedefs(payload)
    )
    payload, u24_negation_helpers_repaired = normalize_cbe_u24_negation(payload)
    payload, integer_negation_helpers_repaired = normalize_cbe_integer_negation(payload)
    payload, u32_power_of_two_division_rewrites = (
        normalize_cbe_u32_power_of_two_division(payload)
    )
    payload, generic_array_roundtrips = normalize_cbe_address_roundtrips(payload)
    payload, exact_byte_arrays_rewritten = (
        normalize_cbe_exact_byte_array_initializers(payload)
    )
    payload, stateless_struct_returns_initialized = (
        normalize_cbe_stateless_struct_returns(payload)
    )
    payload, single_block_pointer_temporaries_eliminated = (
        normalize_cbe_single_block_pointer_temporaries(payload)
    )

    (
        payload,
        removed_const_declarations,
        zero_initialized_const_arrays,
    ) = remove_sdcc_duplicate_const_declarations(payload)

    require(
        re.search(rf"\b{re.escape(abi_identity_symbol)}\s*\(void\)", payload)
        is not None,
        "runtime ABI identity symbol is absent from LLVM-CBE output",
    )
    require(
        re.search(r"\bstcxx_runtime_panic\s*\(", payload) is not None,
        "runtime panic function is absent from LLVM-CBE output",
    )

    preamble = """/* Generated by tools/cpp-core-pipeline/audit_and_adapt.py. */
#include <stddef.h>
#include <stdint.h>
#ifndef __cplusplus
typedef unsigned char bool;
#endif
#define __forceinline inline
#define __ATTRIBUTE_WEAK__
#define __MSVC_INLINE__
#define __ATTRIBUTELIST__(x)
#define __FUNCTIONALIGN__(x)
#define __attribute__(x)
#define __builtin_expect(value, expected) (value)
#define __builtin_unreachable() do { } while (0)

""" + ("\n".join(fcmp_helpers) + "\n\n" if fcmp_helpers else "")

    bridge_lines = [
        "",
        "/* Versioned runtime bridge generated from audited llvm.global_ctors. */",
        "void __stcxx_bridge_require_abi(void)",
        "{",
        f"  {abi_identity_symbol}();",
        "}",
        "",
        "uint16_t __stcxx_bridge_ctor_count(void)",
        "{",
        f"  return (uint16_t){len(expected_c_names)}u;",
        "}",
        "",
        "void __stcxx_bridge_invoke_ctor(uint16_t index)",
        "{",
        "  switch (index) {",
    ]
    for index, name in enumerate(expected_c_names):
        bridge_lines.extend(
            [f"  case {index}u:", f"    {name}();", "    return;"]
        )
    bridge_lines.extend(
        [
            "  default:",
            "    stcxx_runtime_panic(2);",
            "    return;",
            "  }",
            "}",
            "",
        ]
    )

    adapted = preamble + marker + payload + "\n".join(bridge_lines)
    return adapted, {
        "raw_c_sha256": sha256_text(raw),
        "adapted_c_sha256": sha256_text(adapted),
        "constructor_c_symbols": expected_c_names,
        "constructor_attribute_count": len(expected_c_names),
        "required_string_print_bodies": sorted(required_cbe_symbols),
        "abstract_base_traps_mapped_to_runtime_panic": trap_count,
        "function_typedef_order_before": function_typedef_order_before,
        "function_typedef_order_after": function_typedef_order_after,
        "function_typedefs_sorted": (
            function_typedef_order_before != function_typedef_order_after
        ),
        "generic_array_address_roundtrips_normalized": generic_array_roundtrips,
        "u24_negation_helpers_repaired": u24_negation_helpers_repaired,
        "u32_power_of_two_division_rewrites": (
            u32_power_of_two_division_rewrites
        ),
        "exact_byte_array_initializers_rewritten": exact_byte_arrays_rewritten,
        "integer_negation_helpers_repaired": integer_negation_helpers_repaired,
        "stateless_struct_returns_initialized": stateless_struct_returns_initialized,
        "single_block_pointer_temporaries_eliminated": single_block_pointer_temporaries_eliminated,
        "floating_comparison_helpers_preserved": fcmp_helper_names,
        "sdcc_duplicate_const_declarations_removed": removed_const_declarations,
        "sdcc_zero_initialized_const_arrays": zero_initialized_const_arrays,
        "automatic_adaptations": [
            "replace LLVM-CBE host preamble with an SDCC-neutral preamble",
            "remove audited host constructor attributes",
            "generate versioned ctor count/invoke/ABI bridge",
            "erase host-only attributes after whole-program internalization",
            "map two audited unreachable abstract-base traps to runtime panic",
            "sort the dedicated LLVM-CBE l_fptr typedef block by numeric alias",
            "repair the pinned CBE i24 negation helper with modulo-2^24 semantics",
            "lower audited u32 division/remainder by powers of two to shifts/masks",
            "rewrite exact-length LLVM i8 string initializers as numeric byte arrays",
            "zero-initialize synthetic storage returned for audited stateless C++ tags",
            "eliminate audited same-basic-block CBE pointer temporaries",
            "preserve audited pure floating-comparison helpers from the pinned CBE preamble",
            "canonicalize CBE *(&generic_array[index]) loads/stores for SDCC generic pointers",
            "remove const forward declarations that SDCC treats as duplicate definitions",
            "spell zero-initialized CBE byte-array wrappers with nested braces for SDCC",
        ],
        "forbidden_payload_categories": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ir", required=True, type=Path)
    parser.add_argument("--raw-c", required=True, type=Path)
    parser.add_argument("--output-c", required=True, type=Path)
    parser.add_argument("--audit-json", required=True, type=Path)
    parser.add_argument("--expected-triple", required=True)
    parser.add_argument("--expected-layout", required=True)
    parser.add_argument("--abi-identity-symbol", required=True)
    args = parser.parse_args()

    ir = args.ir.read_text(encoding="utf-8")
    raw = args.raw_c.read_text(encoding="utf-8")
    ir_report = audit_ir(ir, args.expected_triple, args.expected_layout)
    adapted, cbe_report = audit_and_adapt_cbe(
        raw,
        list(ir_report["constructors"]),
        args.abi_identity_symbol,
    )

    report = {
        "schema_version": 1,
        "outcome": "pass",
        "qualification": "EXPERIMENTAL_FREESTANDING_CPP_BRIDGE_MCS51_MCS251",
        "production_status": "EXPERIMENTAL_COMPILE_LINK_SUPPORTED",
        "ir": ir_report,
        "llvm_cbe": cbe_report,
        "remaining_production_gates": [
            "all-profile runtime qualification is supplied by the external build and QEMU manifests",
            "varargs, complex aggregate/bitfield and weak/COMDAT edge cases remain fail-closed or unqualified",
            "allocator placement and per-variant stack bounds are qualified by external capacity manifests",
            "Arduino library corpus compatibility is tracked by the external library matrix",
        ],
    }

    args.output_c.parent.mkdir(parents=True, exist_ok=True)
    args.audit_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_c.write_text(adapted, encoding="utf-8", newline="\n")
    args.audit_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print("CPP_CORE_IR_AUDIT=PASS")
    print("CPP_CORE_CBE_ADAPT=PASS")
    print("PRODUCTION_STATUS=EXPERIMENTAL_COMPILE_LINK_SUPPORTED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AuditError, OSError, json.JSONDecodeError) as error:
        print(f"CPP_CORE_PIPELINE_AUDIT=FAIL: {error}")
        raise SystemExit(1)
