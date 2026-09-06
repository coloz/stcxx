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
    "fcmp", "phi", "select", "call",
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
    # LLVM 20 lowers the C/C++ isinf()/isnan() builtins retained by Arduino
    # Print::printFloat() to these exact scalar forms.  The pinned LLVM-CBE
    # implements both types and rejects malformed operands or other widths.
    "llvm.is.fpclass.f32",
    "llvm.is.fpclass.f64",
}

FORBIDDEN_IR_PATTERNS = {
    "nonzero_address_space": re.compile(r"\baddrspace\s*\(\s*[1-9][0-9]*\s*\)"),
    "global_destructors": re.compile(r"@llvm\.global_dtors\b"),
    "destructor_registration": re.compile(
        r"@(?:__cxa_atexit|__cxa_thread_atexit|atexit)\b"
    ),
    "thread_local": re.compile(r"\bthread_local\b"),
    "comdat": re.compile(r"\bcomdat\b"),
    "alias_or_ifunc": re.compile(r"\b(?:alias|ifunc)\b"),
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

    forbidden = [
        name for name, pattern in FORBIDDEN_IR_PATTERNS.items() if pattern.search(ir)
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

    helper_pattern = re.compile(
        r"^static __forceinline int (llvm_fcmp_[a-z0-9_]+)"
        r"\(double X, double Y\) \{ return ([XY<>=!&| ()]+); \}$",
        re.MULTILINE,
    )
    helpers: list[str] = []
    names: list[str] = []
    for match in helper_pattern.finditer(raw_prefix):
        name, expression = match.groups()
        require(name not in names, f"duplicate LLVM-CBE floating helper: {name}")
        require("X" in expression and "Y" in expression,
                f"malformed LLVM-CBE floating helper: {name}")
        names.append(name)
        # The qualified STC ABI defines both source-level float and double as
        # IEEE binary32.  Use float in the SDCC bridge to avoid its diagnostic
        # for an unsupported wider double spelling while preserving values.
        helpers.append(
            f"static __forceinline int {name}(float X, float Y) "
            f"{{ return {expression}; }}"
        )
    referenced = sorted(set(re.findall(r"\b(llvm_fcmp_[a-z0-9_]+)\s*\(", payload)))
    require(
        referenced == sorted(names),
        "LLVM-CBE floating helper definitions do not match payload calls: "
        f"defined {sorted(names)!r}, referenced {referenced!r}",
    )
    residual = re.findall(r"\bllvm_fcmp_[A-Za-z0-9_]+\b", raw_prefix)
    require(
        sorted(set(residual)) == sorted(names),
        "unrecognized LLVM-CBE floating comparison helper shape",
    )
    return helpers, names


def extract_cbe_fp_constant_typedefs(
    raw_prefix: str, payload: str
) -> tuple[list[str], list[str]]:
    """Retain only the pinned CBE's exact binary32/binary64 bit containers."""

    specifications = (
        ("ConstantFloatTy", "typedef uint32_t ConstantFloatTy;"),
        ("ConstantDoubleTy", "typedef uint64_t ConstantDoubleTy;"),
    )
    unsupported = sorted(set(re.findall(
        r"\bConstant(?:FP80|FP128)Ty\b", raw_prefix + payload
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
        referenced = re.search(rf"\b{re.escape(name)}\b", payload) is not None
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
        r"\b(llvm_cbe_is_fpclass_f(?:32|64))\s*\(", payload
    )))
    require(
        referenced == sorted(observed),
        "LLVM-CBE fpclass helper definitions do not match payload calls: "
        f"defined {sorted(observed)!r}, referenced {referenced!r}",
    )
    residual = sorted(set(re.findall(
        r"\bllvm_cbe_is_fpclass_[A-Za-z0-9_]+\b", raw_prefix + payload
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
        rf"\b({name_pattern})\s*\(", payload
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

    function_marker = "/* Function definitions */"
    type_marker = "/* Types Definitions */"
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
    require(typedefs, "LLVM-CBE function typedef block is empty")
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


def remove_sdcc_duplicate_const_declarations(
    payload: str,
) -> tuple[str, list[str], list[str]]:
    declaration_marker = "/* Global Variable Declarations */"
    function_marker = "/* Function Declarations */"
    definition_marker = "/* Global Variable Definitions and Initialization */"
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
        r"\*\((?P<load_type>void\*\*)\)"
        r"\(\(\(&\(\((?P<element_type>uint8_t\*)\)(?P<base>_[0-9]+)\)"
        r"\[(?P<index>.+)\]\)\)\);\s*$"
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
        "  uint32_t r = ((uint32_t)(-a)) & 16777215UL;\n"
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

    return call_pattern.sub(rewrite, payload), rewrites


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


def normalize_cbe_exact_byte_array_initializers(
    payload: str,
) -> tuple[str, list[str]]:
    """Rewrite non-NUL LLVM byte arrays so SDCC does not append a byte.

    LLVM-CBE spells both C strings and arbitrary constant ``[N x i8]`` arrays
    as C string literals inside a wrapper struct.  When the LLVM array already
    contains exactly N non-NUL bytes, C adds an implicit terminator and SDCC
    diagnoses/truncates it.  Convert only that exact, validated shape to a
    nested numeric initializer; ordinary N-1-byte C strings remain unchanged.
    """

    pattern = re.compile(
        r'^static const struct (l_array_([0-9]+)_uint8_t) '
        r'([A-Za-z_][A-Za-z0-9_]*) = \{ "((?:\\.|[^"\\])*)" \};$',
        re.MULTILINE,
    )
    rewritten_symbols: list[str] = []

    def rewrite(match: re.Match[str]) -> str:
        type_name, count_text, symbol, contents = match.groups()
        count = int(count_text)
        values = decode_cbe_byte_string(contents)
        if len(values) == count - 1:
            return match.group(0)
        require(
            len(values) == count,
            f"LLVM-CBE byte initializer length mismatch for {symbol}: "
            f"type has {count}, literal has {len(values)}",
        )
        rewritten_symbols.append(symbol)
        initializer = ", ".join(f"{value}u" for value in values)
        return (
            f"static const struct {type_name} {symbol} = "
            f"{{ {{ {initializer} }} }};"
        )

    rewritten = pattern.sub(rewrite, payload)
    return rewritten, rewritten_symbols


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
    marker = "/* Global Declarations */"
    require_count(raw, marker, 1, "LLVM-CBE global declaration marker")
    raw_prefix, payload = raw.split(marker, 1)
    fcmp_helpers, fcmp_helper_names = extract_cbe_fcmp_helpers(raw_prefix, payload)

    forbidden = [
        name for name, pattern in FORBIDDEN_CBE_PAYLOAD.items()
        if pattern.search(payload)
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
    payload = payload.replace(" __ATTRIBUTE_CTOR__", "")
    require("__ATTRIBUTE_CTOR__" not in payload, "unconsumed constructor attribute")

    trap_count = payload.count("__builtin_trap();")
    require(
        trap_count == 2,
        f"expected two audited abstract-base traps in CBE output, got {trap_count}",
    )
    payload = payload.replace("__builtin_trap();", "stcxx_runtime_panic(5);")

    payload, function_typedef_order_before, function_typedef_order_after = (
        normalize_cbe_function_typedefs(payload)
    )
    payload, u24_negation_helpers_repaired = normalize_cbe_u24_negation(payload)
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
