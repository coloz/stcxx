#!/usr/bin/env python3
"""Audit generic Arduino C++ IR and adapt LLVM-CBE output for SDCC MCS251."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
from pathlib import Path


class AdapterError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AdapterError(message)


def sha256_text(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_canary_adapter():
    path = Path(__file__).resolve().parents[1] / "cpp-core-pipeline" / "audit_and_adapt.py"
    require(path.is_file(), f"shared fail-closed adapter is missing: {path}")
    spec = importlib.util.spec_from_file_location("stcxx_canary_adapter", path)
    require(spec is not None and spec.loader is not None, "cannot load shared adapter")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_constructors(ir: str) -> list[dict[str, object]]:
    match = re.search(
        r"^@llvm\.global_ctors\s*=\s*appending\s+global\s+.*$",
        ir,
        re.MULTILINE,
    )
    if match is None:
        return []
    line = match.group(0)
    entry_pattern = re.compile(
        r"\{\s*i32,\s*ptr,\s*ptr\s*\}\s*\{\s*i32\s+([0-9]+),\s*"
        r"ptr\s+@(?:\"([^\"]+)\"|([A-Za-z0-9_.$-]+)),\s*ptr\s+null\s*\}"
    )
    constructors = [
        {"priority": int(priority), "llvm_symbol": quoted or plain}
        for priority, quoted, plain in entry_pattern.findall(line)
    ]
    declared = re.search(r"appending\s+global\s+\[([0-9]+)\s+x\s+\{", line)
    require(declared is not None, "cannot parse llvm.global_ctors size")
    require(
        len(constructors) == int(declared.group(1)),
        "llvm.global_ctors contains an unsupported entry shape",
    )
    priorities = [int(entry["priority"]) for entry in constructors]
    require(priorities == sorted(priorities), "constructor priorities are not monotonic")
    return constructors


def audit_ir(shared, ir: str, triple: str, layout: str, abi_symbol: str):
    observed_triple, observed_layout = shared.read_target(ir)
    require(observed_triple == triple, f"unexpected target triple: {observed_triple}")
    require(observed_layout == layout, f"unexpected data layout: {observed_layout}")
    forbidden = [
        name for name, pattern in shared.FORBIDDEN_IR_PATTERNS.items()
        if pattern.search(ir)
    ]
    require(not forbidden, "forbidden LLVM IR category: " + ", ".join(forbidden))
    opcodes = shared.collect_opcodes(ir)
    intrinsics = shared.collect_intrinsics(ir)
    pointer_integer_conversions = shared.audit_pointer_integer_conversions(ir)
    constructors = parse_constructors(ir)
    required = {
        "setup": r"define\s+[^\n]*@setup\(",
        "loop": r"define\s+[^\n]*@loop\(",
        "runtime_ctor_entry": r"define\s+[^\n]*@__stcxx_run_global_ctors\(",
        "abi_identity": rf"define\s+[^\n]*@{re.escape(abi_symbol)}\(",
    }
    missing = [name for name, pattern in required.items() if not re.search(pattern, ir)]
    require(not missing, "missing Arduino C++ runtime symbol(s): " + ", ".join(missing))

    trap_callers: list[str] = []
    function_pattern = re.compile(
        r"^define\s+[^\n]*@(?:\"([^\"]+)\"|([A-Za-z0-9_.$-]+))"
        r"\([^\n]*\)[^{]*\{\n(.*?)^\}",
        re.MULTILINE | re.DOTALL,
    )
    for quoted, plain, body in function_pattern.findall(ir):
        body_trap_count = len(
            re.findall(r"\bcall\s+void\s+@llvm\.trap\(\)", body)
        )
        if not body_trap_count:
            continue
        caller = quoted or plain
        # Clang may emit a trap-only complete/deleting destructor for an
        # abstract base whose pure virtual destructor is unreachable.  Match
        # that Itanium ABI shape and its control flow, not a canary class name.
        require(
            re.fullmatch(r"_Z.+D[012]Ev", caller) is not None,
            f"llvm.trap appears outside an audited destructor: {caller}",
        )
        require(
            body_trap_count == 1
            and len(re.findall(r"\bcall\s+", body)) == 1
            and len(re.findall(r"\bunreachable\b", body)) == 1
            and re.search(
                r"\bcall\s+void\s+@llvm\.trap\(\)[^\n]*\n\s*unreachable\s*$",
                body,
            ) is not None,
            f"unsupported trap destructor body: {caller}",
        )
        trap_callers.append(caller)
    trap_call_count = len(
        re.findall(r"\bcall\s+void\s+@llvm\.trap\(\)", ir)
    )
    return {
        "target_triple": observed_triple,
        "data_layout": observed_layout,
        "constructors": constructors,
        "observed_opcodes": opcodes,
        "observed_intrinsics": intrinsics,
        "pointer_integer_conversions": pointer_integer_conversions,
        "audited_trap_callers": trap_callers,
        "audited_trap_call_count": trap_call_count,
        "audited_trap_policy": "trap-only-itanium-destructor",
        "forbidden_categories": [],
    }


def remove_duplicate_const_declarations(shared, payload: str):
    """Use the canary rewrite when needed, while accepting an empty rewrite set."""
    marker_a = "/* Global Variable Declarations */"
    marker_b = "/* Function Declarations */"
    marker_c = "/* Global Variable Definitions and Initialization */"
    require(payload.count(marker_a) == 1, "global declaration marker mismatch")
    require(payload.count(marker_b) == 1, "function declaration marker mismatch")
    require(payload.count(marker_c) == 1, "global definition marker mismatch")
    declaration_block = payload.split(marker_a, 1)[1].split(marker_b, 1)[0]
    if re.search(r"^const\s+static\s+.+\s+[A-Za-z_][A-Za-z0-9_]*\s*;\s*$",
                 declaration_block, re.MULTILINE):
        return shared.remove_sdcc_duplicate_const_declarations(payload)
    return payload, [], []


def normalize_cbe_select_helpers(payload: str) -> tuple[str, list[str]]:
    """Initialize LLVM-CBE select temporaries in their declaration.

    CBE emits a declaration followed immediately by an unconditional
    assignment. Patched SDCC still reports its generic warning 84 for some
    inlined callers, so retain the exact semantics while making the source's
    definite initialization mechanically auditable.
    """
    pattern = re.compile(
        r"^static __forceinline (?P<type>[A-Za-z_][A-Za-z0-9_]*(?:\s*\*)?) "
        r"(?P<name>llvm_select_[A-Za-z0-9_]+)"
        r"\(bool condition, (?P=type) iftrue, (?P=type) ifnot\) \{\n"
        r"  (?P=type) r;\n"
        r"  r = condition \? iftrue : ifnot;\n"
        r"  return r;\n"
        r"\}$",
        re.MULTILINE,
    )
    names: list[str] = []

    def rewrite(match: re.Match[str]) -> str:
        names.append(match.group("name"))
        return (
            f"static __forceinline {match.group('type')} {match.group('name')}"
            f"(bool condition, {match.group('type')} iftrue, "
            f"{match.group('type')} ifnot) {{\n"
            f"  {match.group('type')} r = condition ? iftrue : ifnot;\n"
            "  return r;\n"
            "}"
        )

    normalized = pattern.sub(rewrite, payload)
    residual = re.findall(
        r"^static __forceinline [^\n]+ llvm_select_[A-Za-z0-9_]+"
        r"\([^\n]*\) \{\n\s+[^\n]+ r;\s*$",
        normalized,
        re.MULTILINE,
    )
    require(not residual, "unsupported uninitialized LLVM-CBE select helper")
    return normalized, names


def normalize_cbe_unconditional_helper_initializers(
    payload: str,
) -> tuple[str, list[str]]:
    """Fold an immediately assigned CBE helper temporary into its declaration.

    The pinned CBE emits some intrinsic helpers (currently ``fmuladd``) as an
    uninitialized ``r`` declaration followed by one unconditional assignment.
    That source is well-defined, but patched SDCC can still diagnose warning 84
    after inlining it.  Restrict the rewrite to the complete, single-expression
    helper shape so a genuinely conditional or otherwise new form fails the
    later mechanical uninitialized-temporary audit.
    """
    pattern = re.compile(
        r"^static __forceinline (?P<type>[A-Za-z_][A-Za-z0-9_]*(?:\s*\*)?) "
        r"(?P<name>llvm_[A-Za-z0-9_]+)\((?P<arguments>[^\n]*)\) \{\n"
        r"  (?P=type) r;\n"
        r"  r = (?P<expression>[^;\n]+);\n"
        r"  return r;\n"
        r"\}$",
        re.MULTILINE,
    )
    names: list[str] = []

    def rewrite(match: re.Match[str]) -> str:
        names.append(match.group("name"))
        return (
            f"static __forceinline {match.group('type')} {match.group('name')}"
            f"({match.group('arguments')}) {{\n"
            f"  {match.group('type')} r = {match.group('expression')};\n"
            "  return r;\n"
            "}"
        )

    normalized = pattern.sub(rewrite, payload)
    residual = re.findall(
        r"^static __forceinline [^\n]+ llvm_[A-Za-z0-9_]+"
        r"\([^\n]*\) \{\n\s+[^\n]+ r;\s*$",
        normalized,
        re.MULTILINE,
    )
    require(not residual, "unsupported uninitialized LLVM-CBE intrinsic helper")
    return normalized, names


def adapt_cbe(
    shared,
    raw: str,
    constructors: list[dict[str, object]],
    abi_symbol: str,
    expected_trap_count: int,
):
    marker = "/* Global Declarations */"
    require(raw.count(marker) == 1, "LLVM-CBE global declaration marker mismatch")
    raw_prefix, payload = raw.split(marker, 1)
    fcmp_helpers, fcmp_helper_names = shared.extract_cbe_fcmp_helpers(
        raw_prefix, payload
    )
    forbidden = [
        name for name, pattern in shared.FORBIDDEN_CBE_PAYLOAD.items()
        if pattern.search(payload)
    ]
    require(not forbidden, "forbidden LLVM-CBE payload: " + ", ".join(forbidden))

    declared = shared.parse_cbe_ctor_declarations(payload)
    expected = [shared.cbe_mangle(str(entry["llvm_symbol"])) for entry in constructors]
    require(
        declared == expected,
        f"constructor order changed between LLVM and CBE: expected {expected!r}, got {declared!r}",
    )
    require(payload.count(" __ATTRIBUTE_CTOR__") == len(expected),
            "constructor attribute count mismatch")
    payload = payload.replace(" __ATTRIBUTE_CTOR__", "")

    trap_count = payload.count("__builtin_trap();")
    require(
        trap_count == expected_trap_count,
        "LLVM-CBE trap count differs from audited LLVM IR: "
        f"expected {expected_trap_count}, got {trap_count}",
    )
    payload = payload.replace("__builtin_trap();", "stcxx_runtime_panic(5);")
    payload, select_helpers_initialized = normalize_cbe_select_helpers(payload)
    payload, unconditional_helpers_initialized = (
        normalize_cbe_unconditional_helper_initializers(payload)
    )
    payload, typedefs_before, typedefs_after = shared.normalize_cbe_function_typedefs(payload)
    payload, u24_negation_helpers_repaired = shared.normalize_cbe_u24_negation(payload)
    payload, pointer_rewrites = shared.normalize_cbe_address_roundtrips(payload)
    payload, exact_byte_arrays_rewritten = (
        shared.normalize_cbe_exact_byte_array_initializers(payload)
    )
    payload, stateless_struct_returns_initialized = (
        shared.normalize_cbe_stateless_struct_returns(payload)
    )
    payload, single_block_pointer_temporaries_eliminated = (
        shared.normalize_cbe_single_block_pointer_temporaries(payload)
    )
    payload, removed_consts, zero_arrays = remove_duplicate_const_declarations(shared, payload)

    require(re.search(rf"\b{re.escape(abi_symbol)}\s*\(void\)", payload) is not None,
            "runtime ABI identity is absent from CBE output")
    require(re.search(r"\bstcxx_runtime_panic\s*\(", payload) is not None,
            "runtime panic implementation is absent from CBE output")

    preamble = """/* Generated by tools/cpp-cli/adapt.py. */
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
    bridge = [
        "", "/* Constructor bridge generated from llvm.global_ctors. */",
        "extern int main(void);", "",
        "void __stcxx_bridge_require_core_main(void)", "{", "  (void)main();", "}", "",
        "void __stcxx_bridge_require_abi(void)", "{", f"  {abi_symbol}();", "}", "",
        "uint16_t __stcxx_bridge_ctor_count(void)", "{",
        f"  return (uint16_t){len(expected)}u;", "}", "",
        "void __stcxx_bridge_invoke_ctor(uint16_t index)", "{", "  switch (index) {",
    ]
    for index, name in enumerate(expected):
        bridge.extend([f"  case {index}u:", f"    {name}();", "    return;"])
    bridge.extend([
        "  default:", "    stcxx_runtime_panic(2);", "    return;", "  }", "}", "",
    ])
    adapted = preamble + marker + payload + "\n".join(bridge)
    return adapted, {
        "raw_c_sha256": sha256_text(raw),
        "adapted_c_sha256": sha256_text(adapted),
        "constructor_c_symbols": expected,
        "core_main_archive_anchor": "main",
        "abstract_base_traps_mapped": trap_count,
        "function_typedef_order_before": typedefs_before,
        "function_typedef_order_after": typedefs_after,
        "u24_negation_helpers_repaired": u24_negation_helpers_repaired,
        "select_helpers_initialized": select_helpers_initialized,
        "unconditional_helpers_initialized": unconditional_helpers_initialized,
        "floating_comparison_helpers_preserved": fcmp_helper_names,
        "exact_byte_array_initializers_rewritten": exact_byte_arrays_rewritten,
        "stateless_struct_returns_initialized": stateless_struct_returns_initialized,
        "single_block_pointer_temporaries_eliminated": single_block_pointer_temporaries_eliminated,
        "generic_pointer_rewrites": pointer_rewrites,
        "duplicate_const_declarations_removed": removed_consts,
        "zero_initialized_const_arrays": zero_arrays,
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
    shared = load_canary_adapter()
    ir = args.ir.read_text(encoding="utf-8")
    raw = args.raw_c.read_text(encoding="utf-8")
    ir_report = audit_ir(shared, ir, args.expected_triple, args.expected_layout,
                         args.abi_identity_symbol)
    adapted, cbe_report = adapt_cbe(
        shared,
        raw,
        list(ir_report["constructors"]),
        args.abi_identity_symbol,
        int(ir_report["audited_trap_call_count"]),
    )
    report = {
        "schema_version": 1,
        "outcome": "pass",
        "qualification": "EXPERIMENTAL_K246_12MHZ_ARDUINO_CLI",
        "ir": ir_report,
        "llvm_cbe": cbe_report,
    }
    args.output_c.parent.mkdir(parents=True, exist_ok=True)
    args.output_c.write_text(adapted, encoding="utf-8", newline="\n")
    args.audit_json.write_text(json.dumps(report, indent=2) + "\n",
                               encoding="utf-8", newline="\n")
    print("STCXX_ARDUINO_CLI_ADAPTER=PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AdapterError, OSError, json.JSONDecodeError) as error:
        print(f"STCXX_ARDUINO_CLI_ADAPTER=FAIL: {error}")
        raise SystemExit(1)
