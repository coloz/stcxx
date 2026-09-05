#!/usr/bin/env python3
"""Mechanical dual-target Clang/LLVM-CBE regression for the STC C++ ABI."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


PROFILES = {
    "mcs51": {
        "triple": "msp430-stc51-none-eabi",
        "layout": (
            "e-m:e-p:24:8-p1:16:8-P1-i8:8-i16:8-i32:8-i64:8-i128:8-"
            "f32:8-f64:8-f128:8-a:8-n8:16:32-S8"
        ),
        "probe": "mcs51_target_info_probe.cpp",
        "member_bits": 16,
        "data_member_bytes": 2,
        "method_member_bytes": 4,
        "size_bits": 16,
        "array_cookie_bytes": 2,
        "array_new_symbol": "_Znaj",
    },
    "mcs251": {
        "triple": "msp430-stc-none-eabi",
        "layout": (
            "E-m:e-p:24:8-i8:8-i16:8-i32:8-i64:8-i128:8-f32:8-f64:8-"
            "f128:8-a:8-n8:16:32-S8"
        ),
        "probe": "target_info_probe.cpp",
        "member_bits": 24,
        "data_member_bytes": 3,
        "method_member_bytes": 6,
        "size_bits": 32,
        "array_cookie_bytes": 4,
        "array_new_symbol": "_Znam",
    },
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        argv, cwd=cwd, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    if process.returncode:
        raise RuntimeError(
            f"command failed ({process.returncode}): {argv!r}\n"
            + process.stdout + process.stderr
        )
    return process


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def llvm_function(ir: str, symbol: str) -> str:
    pattern = re.compile(
        rf"^define\b[^\n]*@{re.escape(symbol)}\([^\n]*\)[^\n]*\{{\n"
        r"(?P<body>.*?)^\}",
        re.MULTILINE | re.DOTALL,
    )
    matches = list(pattern.finditer(ir))
    require(len(matches) == 1,
            f"expected one LLVM definition for {symbol}, got {len(matches)}")
    return matches[0].group(0)


SSA = r"%[-A-Za-z$._0-9]+"


def require_vtable_offset_flow(
    body: str, symbol: str, *, require_minus_two_slot: bool = False,
) -> None:
    """Require one signed i24 vtable offset to feed an i32 pointer GEP."""
    if require_minus_two_slot:
        slots = re.findall(
            rf"^\s*({SSA})\s*=\s*getelementptr inbounds i24, ptr {SSA}, "
            r"i64 -2\s*$",
            body,
            re.MULTILINE,
        )
        require(len(slots) == 1,
                f"{symbol} does not address exactly one i24 vtable slot -2")
        load_pattern = (
            rf"^\s*({SSA})\s*=\s*load i24, ptr "
            rf"{re.escape(slots[0])}\b[^\n]*$"
        )
    else:
        load_pattern = rf"^\s*({SSA})\s*=\s*load i24, ptr {SSA}\b[^\n]*$"
    raw_offsets = re.findall(load_pattern, body, re.MULTILINE)
    require(len(raw_offsets) == 1,
            f"{symbol} does not load exactly one i24 vtable offset")
    extended_offsets = re.findall(
        rf"^\s*({SSA})\s*=\s*sext i24 "
        rf"{re.escape(raw_offsets[0])} to i32\s*$",
        body,
        re.MULTILINE,
    )
    require(len(extended_offsets) == 1,
            f"{symbol} does not sign-extend its i24 vtable offset to i32")
    require(
        re.search(
            rf"getelementptr inbounds i8, ptr {SSA}, i32 "
            rf"{re.escape(extended_offsets[0])}\b",
            body,
        ) is not None,
        f"{symbol} does not consume the extended i32 vtable offset",
    )
    require("load i32, ptr" not in body,
            f"{symbol} still loads a four-byte vtable offset slot")


def require_mcs51_program_virtual_call(body: str, symbol: str) -> None:
    casts = re.findall(
        rf"^\s*({SSA})\s*=\s*addrspacecast ptr ({SSA}) "
        r"to ptr addrspace\(1\)\s*$",
        body,
        re.MULTILINE,
    )
    require(len(casts) == 1,
            f"{symbol} does not cast exactly one vtable callee to AS1")
    cast_result, loaded_callee = casts[0]
    require(
        re.search(
            rf"^\s*{re.escape(loaded_callee)}\s*=\s*load ptr, ptr "
            rf"{SSA}\b[^\n]*$",
            body,
            re.MULTILINE,
        ) is not None,
        f"{symbol} AS1 cast input is not a vtable pointer load",
    )
    require(
        re.search(
            rf"\bcall(?:\s+noundef)?\s+addrspace\(1\)\s+"
            rf"(?:void|i[0-9]+)\s+{re.escape(cast_result)}\(",
            body,
        ) is not None,
        f"{symbol} does not call the AS1 virtual callee",
    )


def require_pointer_difference_flow(body: str, symbol: str) -> None:
    pointer_integers = re.findall(
        rf"^\s*({SSA})\s*=\s*ptrtoint ptr {SSA} to i32\s*$",
        body,
        re.MULTILINE,
    )
    require(len(pointer_integers) == 2,
            f"{symbol} does not convert exactly two pointers to i32")
    subtractions = re.findall(
        rf"^\s*({SSA})\s*=\s*sub i32 "
        rf"{re.escape(pointer_integers[0])}, "
        rf"{re.escape(pointer_integers[1])}\s*$",
        body,
        re.MULTILINE,
    )
    require(len(subtractions) == 1 and "sub i24" not in body,
            f"{symbol} does not subtract the two converted i32 pointers")


def require_relative_vtable_delete_flow(body: str) -> None:
    slots = re.findall(
        rf"^\s*({SSA})\s*=\s*getelementptr inbounds i32, ptr {SSA}, "
        r"i64 -2\s*$",
        body,
        re.MULTILINE,
    )
    require(len(slots) == 1,
            "relative global delete does not address one i32 slot -2")
    loads = re.findall(
        rf"^\s*({SSA})\s*=\s*load i32, ptr {re.escape(slots[0])}, "
        r"align 4\s*$",
        body,
        re.MULTILINE,
    )
    require(len(loads) == 1,
            "relative global delete does not load its i32 slot at align 4")
    extensions = re.findall(
        rf"^\s*({SSA})\s*=\s*sext i32 {re.escape(loads[0])} to i64\s*$",
        body,
        re.MULTILINE,
    )
    require(len(extensions) == 1,
            "relative global delete does not sign-extend its i32 offset")
    require(
        re.search(
            rf"^\s*{SSA}\s*=\s*getelementptr inbounds i8, ptr {SSA}, "
            rf"i64 {re.escape(extensions[0])}\s*$",
            body,
            re.MULTILINE,
        ) is not None,
        "relative global delete does not consume its extended offset",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clang", type=Path, required=True)
    parser.add_argument("--llvm-cbe", type=Path, required=True)
    parser.add_argument("--probe-dir", type=Path,
                        default=Path(__file__).with_name("probe"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    clang = args.clang.resolve()
    cbe = args.llvm_cbe.resolve()
    probe_dir = args.probe_dir.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    results: dict[str, object] = {
        "schema_version": 1,
        "outcome": "PASS",
        "clang": {"path": str(clang), "sha256": digest(clang)},
        "llvm_cbe": {"path": str(cbe), "sha256": digest(cbe)},
        "profiles": {},
    }

    for name, profile in PROFILES.items():
        probe = probe_dir / str(profile["probe"])
        require(probe.is_file(), f"missing {name} probe: {probe}")
        common = [
            str(clang), f"--target={profile['triple']}", "-x", "c++",
            "-std=c++11", "-O0", "-ffreestanding", "-funsigned-char",
            "-fno-exceptions", "-fno-rtti", "-fno-threadsafe-statics",
            "-fno-use-cxa-atexit", "-fno-c++-static-destructors",
            "-Xclang", "-mno-constructor-aliases",
        ]
        macros = run(common + ["-dM", "-E", str(probe)], probe_dir).stdout
        identity = "__STC_MCS51__" if name == "mcs51" else "__STC_MCS251__"
        require(f"#define {identity} 1" in macros,
                f"{name} target identity macro is missing")

        ir_path = output / f"{name}.ll"
        bc_path = output / f"{name}.bc"
        c_path = output / f"{name}.c"
        run(common + ["-emit-llvm", "-S", str(probe), "-o", str(ir_path)],
            probe_dir)
        run(common + ["-emit-llvm", "-c", str(probe), "-o", str(bc_path)],
            probe_dir)
        verified_ir_path = output / f"{name}.verified.ll"
        run([
            str(clang), f"--target={profile['triple']}", "-x", "ir",
            "-emit-llvm", "-S", str(bc_path), "-o", str(verified_ir_path),
        ], probe_dir)
        run([str(cbe), str(bc_path), "-o", str(c_path)], probe_dir)

        emitted_ir = ir_path.read_text(encoding="utf-8")
        # All ABI assertions below intentionally inspect the IR reconstructed
        # from bitcode.  This binds the checks to the exact artifact consumed
        # by LLVM-CBE and rejects malformed writer-only textual output.
        ir = verified_ir_path.read_text(encoding="utf-8")
        generated_c = c_path.read_text(encoding="utf-8")
        bits = int(profile["member_bits"])
        require(f'target triple = "{profile["triple"]}"' in emitted_ir,
                f"{name} emitted text IR target triple drift")
        require(f'target datalayout = "{profile["layout"]}"' in emitted_ir,
                f"{name} emitted text IR data layout drift")
        require(f'target triple = "{profile["triple"]}"' in ir,
                f"{name} target triple drift")
        require(f'target datalayout = "{profile["layout"]}"' in ir,
                f"{name} data layout drift")
        require(f"@observed_data_member = dso_local global i{bits} " in ir,
                f"{name} data-member representation drift")
        require(f"@observed_plain_member = dso_local global {{ i{bits}, i{bits} }}" in ir,
                f"{name} method-member representation drift")
        require(f"ptrtoint (ptr{' addrspace(1)' if name == 'mcs51' else ''} " in ir
                and f" to i{bits})" in ir,
                f"{name} nonvirtual member encoding drift")
        function_pointer = llvm_function(ir, "bridge_function_pointer")
        indirect_call = llvm_function(ir, "bridge_indirect")
        pointer_ir = r"ptr addrspace\(1\)" if name == "mcs51" else r"ptr"
        returned_pointers = re.findall(
            rf"^\s*({SSA})\s*=\s*load {pointer_ir}, ptr {SSA}\b[^\n]*$",
            function_pointer,
            re.MULTILINE,
        )
        require(
            len(returned_pointers) == 1
            and re.search(
                rf"\bret {pointer_ir} {re.escape(returned_pointers[0])}\b",
                function_pointer,
            ) is not None,
            f"{name} function-pointer return is not bound to its ABI type",
        )
        indirect_callees = re.findall(
            rf"^\s*({SSA})\s*=\s*load {pointer_ir}, ptr {SSA}\b[^\n]*$",
            indirect_call,
            re.MULTILINE,
        )
        require(len(indirect_callees) == 1,
                f"{name} indirect callee is not loaded in its ABI type")
        call_prefix = (
            r"call(?:\s+noundef)?\s+addrspace\(1\)\s+i16"
            if name == "mcs51"
            else r"call(?:\s+noundef)?\s+i16"
        )
        require(
            re.search(
                rf"\b{call_prefix}\s+{re.escape(indirect_callees[0])}\(",
                indirect_call,
            ) is not None,
            f"{name} indirect call is not bound to the loaded function pointer",
        )
        if name == "mcs51":
            require("ptr addrspace(1) @bridge_function_pointer(" in
                    function_pointer
                    and "@bridge_function_pointer(ptr addrspace(1) " in
                    function_pointer,
                    "MCS51 function-pointer boundary escaped program AS1")
        else:
            require("addrspace(1)" not in function_pointer + indirect_call,
                    "MCS251 function pointer unexpectedly uses MCS51 AS1")
        function_reference = llvm_function(ir, "bridge_function_reference")
        if name == "mcs51":
            require(
                re.search(
                    r"@bridge_function_reference\(ptr addrspace\(1\) ",
                    function_reference,
                ) is not None
                and re.search(
                    rf"\bcall(?:\s+noundef)?\s+addrspace\(1\)\s+i16\s+"
                    rf"{SSA}\(",
                    function_reference,
                ) is not None,
                "MCS51 function reference escaped program AS1",
            )
        else:
            require(
                "addrspace(1)" not in function_reference
                and re.search(
                    rf"\bcall(?:\s+noundef)?\s+i16\s+{SSA}\(",
                    function_reference,
                ) is not None,
                "MCS251 function reference ABI drift",
            )
        require("@bridge_data_member_apply" in ir
                and "@bridge_method_member_apply" in ir,
                f"{name} member-pointer apply probes are absent")

        require(
            re.search(
                rf"^@observed_right_data_as_derived = .*global i{bits} 2,",
                ir,
                re.MULTILINE,
            ) is not None,
            f"{name} constant data-member conversion uses the wrong width",
        )
        converted_method_lines = re.findall(
            r"^@observed_right_method_as_derived = .*?$", ir, re.MULTILINE,
        )
        require(
            len(converted_method_lines) == 1
            and f"{{ i{bits}, i{bits} }}" in converted_method_lines[0]
            and re.search(
                rf"\bi{bits} 2\s*\}}", converted_method_lines[0]
            ) is not None
            and "i32 2" not in converted_method_lines[0],
            f"{name} constant method-member conversion uses the wrong width",
        )
        for symbol, operation in (
            ("bridge_data_member_base_to_derived", "add"),
            ("bridge_data_member_derived_to_base", "sub"),
            ("bridge_method_member_base_to_derived", "add"),
            ("bridge_method_member_derived_to_base", "sub"),
        ):
            conversion = llvm_function(ir, symbol)
            require(
                re.search(
                    rf"\b{operation} nsw i{bits} {SSA}, 2\b", conversion
                ) is not None,
                f"{name} {symbol} adjustment escaped i{bits}",
            )
            require(
                re.search(
                    rf"\b{operation} nsw i{bits} {SSA}, i32\b", conversion
                ) is None,
                f"{name} {symbol} contains a mixed-width adjustment",
            )

        diamond_vtables = re.findall(r"^@_ZTV8VDiamond = .*?$", ir,
                                     re.MULTILINE)
        require(
            len(diamond_vtables) == 1
            and re.search(
                r"inttoptr \(i24 -?[1-9][0-9]* to ptr\)",
                diamond_vtables[0],
            ) is not None
            and "inttoptr (i32" not in diamond_vtables[0],
            f"{name} VDiamond vtable offsets are not stored in i24 slots",
        )
        ordinary_virtual_call = llvm_function(ir, "bridge_virtual")
        virtual_base_cast = llvm_function(ir, "bridge_virtual_base_cast")
        offset_to_top = llvm_function(ir, "bridge_offset_to_top")
        virtual_root_call = llvm_function(ir, "bridge_virtual_root_call")
        virtual_global_delete = llvm_function(
            ir, "bridge_virtual_global_delete"
        )
        require_vtable_offset_flow(
            virtual_base_cast, f"{name} bridge_virtual_base_cast"
        )
        require_vtable_offset_flow(
            offset_to_top, f"{name} bridge_offset_to_top",
            require_minus_two_slot=True,
        )
        require_vtable_offset_flow(
            virtual_global_delete, f"{name} bridge_virtual_global_delete",
            require_minus_two_slot=True,
        )
        virtual_thunks = list(re.finditer(
            r"^define\b[^@]*@(?P<name>_ZTv[^ (]+)\([^)]*\)[^{]*\{\n"
            r"(?P<body>.*?)^\}",
            ir,
            re.MULTILINE | re.DOTALL,
        ))
        require(virtual_thunks,
                f"{name} virtual-inheritance thunk probes are absent")
        for thunk in virtual_thunks:
            require_vtable_offset_flow(
                thunk.group("body"), f"{name} {thunk.group('name')}"
            )

        pointer_difference = llvm_function(ir, "bridge_pointer_difference")
        scaled_pointer_difference = llvm_function(
            ir, "bridge_scaled_pointer_difference"
        )
        pointer_difference_store = llvm_function(
            ir, "bridge_pointer_difference_store"
        )
        pointer_difference_comparison = llvm_function(
            ir, "bridge_pointer_difference_is_four"
        )
        pointer_difference_negative = llvm_function(
            ir, "bridge_pointer_difference_negative"
        )
        pointer_difference_argument = llvm_function(
            ir, "bridge_pointer_difference_argument"
        )
        for symbol, body in (
            ("bridge_pointer_difference", pointer_difference),
            ("bridge_scaled_pointer_difference", scaled_pointer_difference),
            ("bridge_pointer_difference_store", pointer_difference_store),
            ("bridge_pointer_difference_is_four", pointer_difference_comparison),
            ("bridge_pointer_difference_negative", pointer_difference_negative),
            ("bridge_pointer_difference_argument", pointer_difference_argument),
        ):
            require_pointer_difference_flow(body, f"{name} {symbol}")
        require("sdiv exact i32" in scaled_pointer_difference,
                f"{name} scaled pointer subtraction lost its i32 division")
        require("store i32" in pointer_difference_store
                and "store i24" not in pointer_difference_store,
                f"{name} pointer-difference store underwrites ptrdiff_t")
        require("icmp eq i32" in pointer_difference_comparison
                and "icmp eq i24" not in pointer_difference_comparison,
                f"{name} pointer-difference comparison mixes integer widths")
        argument_subtractions = re.findall(
            r"^\s*(%[-A-Za-z$._0-9]+)\s*=\s*sub i32\s+"
            r"%[-A-Za-z$._0-9]+,\s*%[-A-Za-z$._0-9]+\s*$",
            pointer_difference_argument,
            re.MULTILINE,
        )
        require(len(argument_subtractions) == 1,
                f"{name} pointer-difference argument has an ambiguous i32 sub")
        call_address_space = r" addrspace\(1\)" if name == "mcs51" else ""
        require(
            re.search(
                rf"\bcall{call_address_space} i32 "
                rf"@bridge_pointer_difference_consume\(i32 (?:noundef )?"
                rf"{re.escape(argument_subtractions[0])}\)",
                pointer_difference_argument,
            ) is not None,
            f"{name} pointer difference is not passed directly as i32 ptrdiff_t",
        )

        size_bits = int(profile["size_bits"])
        cookie_bytes = int(profile["array_cookie_bytes"])
        array_new_symbol = str(profile["array_new_symbol"])
        array_new = llvm_function(ir, "bridge_array_new")
        array_delete = llvm_function(ir, "bridge_array_delete")
        require(
            re.search(
                rf"@bridge_array_new\(i{size_bits} noundef %[-A-Za-z$._0-9]+\)",
                array_new,
            ) is not None,
            f"{name} new[] count is not the target size_t width",
        )
        require(
            f"@llvm.umul.with.overflow.i{size_bits}" in array_new
            and f"@llvm.uadd.with.overflow.i{size_bits}" in array_new,
            f"{name} new[] size arithmetic escaped size_t width",
        )
        allocation = re.search(
            rf"^\s*(%[-A-Za-z$._0-9]+)\s*=\s*call[^\n]*"
            rf"@{array_new_symbol}\(i{size_bits} noundef ",
            array_new,
            re.MULTILINE,
        )
        require(allocation is not None,
                f"{name} new[] did not call the size_t-width allocator")
        assert allocation is not None
        allocation_value = allocation.group(1)
        require(
            re.search(
                rf"^\s*store\s+i{size_bits}\s+%[-A-Za-z$._0-9]+,\s*"
                rf"ptr\s+{re.escape(allocation_value)}\b",
                array_new,
                re.MULTILINE,
            ) is not None,
            f"{name} new[] cookie store has the wrong width",
        )
        require(
            re.search(
                rf"getelementptr inbounds i8, ptr {re.escape(allocation_value)}, "
                rf"i{size_bits} {cookie_bytes}\b",
                array_new,
            ) is not None,
            f"{name} new[] data pointer does not skip the exact cookie size",
        )
        require(
            re.search(
                rf"getelementptr inbounds i8, ptr %[-A-Za-z$._0-9]+, "
                rf"i{size_bits} -{cookie_bytes}\b",
                array_delete,
            ) is not None
            and re.search(
                rf"load i{size_bits}, ptr %[-A-Za-z$._0-9]+",
                array_delete,
            ) is not None,
            f"{name} delete[] did not recover the exact size_t cookie",
        )
        require(
            "overflow.i24" not in array_new
            and "store i24" not in array_new
            and "load i24" not in array_delete
            and not re.search(r"\bi24\s+-?3\b", array_new + array_delete),
            f"{name} retained the historical 24-bit/3-byte array cookie",
        )
        if name == "mcs51":
            require(
                re.search(r"\bzext i24\s+[^\n]+\s+to i16\b", ir) is None,
                "MCS51 contains an invalid i24-to-i16 zero extension",
            )
        if name == "mcs51":
            require("inttoptr i16" in ir and "to ptr addrspace(1)" in ir,
                    "MCS51 nonvirtual member call escaped program AS1")
            require("addrspacecast ptr" in ir and "to ptr addrspace(1)" in ir,
                    "MCS51 virtual member call escaped program AS1")
            require("call noundef addrspace(1)" in ir,
                    "MCS51 member indirect call is not a program call")
            for symbol, body in (
                ("bridge_virtual", ordinary_virtual_call),
                ("bridge_virtual_root_call", virtual_root_call),
                ("bridge_virtual_global_delete", virtual_global_delete),
            ):
                require_mcs51_program_virtual_call(body, f"MCS51 {symbol}")
            require("llvm_cbe_program_pointer" in generated_c,
                    "LLVM-CBE lost the MCS51 program-pointer type")
        else:
            for symbol, body in (
                ("bridge_virtual", ordinary_virtual_call),
                ("bridge_virtual_root_call", virtual_root_call),
                ("bridge_virtual_global_delete", virtual_global_delete),
            ):
                require("addrspace(1)" not in body,
                        f"MCS251 {symbol} unexpectedly uses MCS51 program AS1")
            require("unsigned _BitInt(24)" in generated_c,
                    "LLVM-CBE widened the locked MCS251 i24 representation")

        native = subprocess.run(
            common + ["-S", str(probe), "-o", str(output / f"{name}.s")],
            cwd=probe_dir, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False,
        )
        require(native.returncode != 0
                and "experimental LLVM IR-only profile" in
                (native.stdout + native.stderr),
                f"{name} native backend did not fail closed")

        results["profiles"][name] = {
            "outcome": "PASS",
            "target_triple": profile["triple"],
            "data_layout": profile["layout"],
            "ordinary_function_pointer": "PASS",
            "function_reference_storage_bytes": (
                2 if name == "mcs51" else 3
            ),
            "data_member_pointer_bytes": profile["data_member_bytes"],
            "method_member_pointer_bytes": profile["method_member_bytes"],
            "member_pointer_conversion_width": bits,
            "vtable_offset_storage_width": 24,
            "vtable_offset_arithmetic_width": 32,
            "virtual_call_program_address_space": 1 if name == "mcs51" else 0,
            "pointer_difference_ir_width": 32,
            "size_t_ir_width": size_bits,
            "array_cookie_bytes": cookie_bytes,
            "bitcode_verifier": "PASS",
            "abi_assertion_artifact": "verified_llvm_ir",
            "llvm_ir_sha256": digest(ir_path),
            "bitcode_sha256": digest(bc_path),
            "verified_llvm_ir_sha256": digest(verified_ir_path),
            "llvm_cbe_c_sha256": digest(c_path),
            "native_backend": "BLOCKED_FAIL_CLOSED",
        }

    stock_probe = probe_dir / "stock_msp430_probe.cpp"
    require(stock_probe.is_file(), f"missing ordinary MSP430 probe: {stock_probe}")
    stock_common = [
        str(clang), "--target=msp430-unknown-unknown", "-x", "c++",
        "-std=c++11", "-O0", "-ffreestanding", "-fno-exceptions",
        "-fno-rtti", "-fno-threadsafe-statics", "-fno-use-cxa-atexit",
        "-fno-c++-static-destructors", "-Xclang",
        "-mno-constructor-aliases",
    ]
    stock_macros = run(
        stock_common + ["-dM", "-E", str(stock_probe)], probe_dir
    ).stdout
    require("#define __MSP430__ 1" in stock_macros,
            "ordinary MSP430 target identity macro is missing")
    for forbidden in ("__STC_MCS51__", "__STC_MCS251__",
                      "__STC_CLANG_IR_ONLY__"):
        require(f"#define {forbidden} " not in stock_macros,
                f"ordinary MSP430 leaked {forbidden}")

    stock_ir_path = output / "stock-msp430.ll"
    stock_bc_path = output / "stock-msp430.bc"
    stock_verified_path = output / "stock-msp430.verified.ll"
    stock_assembly_path = output / "stock-msp430.s"
    run(stock_common + ["-emit-llvm", "-S", str(stock_probe), "-o",
                        str(stock_ir_path)], probe_dir)
    run(stock_common + ["-emit-llvm", "-c", str(stock_probe), "-o",
                        str(stock_bc_path)], probe_dir)
    run([
        str(clang), "--target=msp430-unknown-unknown", "-x", "ir",
        "-emit-llvm", "-S", str(stock_bc_path), "-o",
        str(stock_verified_path),
    ], probe_dir)
    run(stock_common + ["-S", str(stock_probe), "-o",
                        str(stock_assembly_path)], probe_dir)
    require(stock_assembly_path.stat().st_size > 0,
            "ordinary MSP430 native backend emitted an empty assembly file")

    stock_ir = stock_verified_path.read_text(encoding="utf-8")
    require('target triple = "msp430-unknown-unknown"' in stock_ir,
            "ordinary MSP430 triple drift")
    require("@stock_msp430_probe" in stock_ir
            and "@stock_msp430_virtual" in stock_ir
            and "@_ZTV12StockVirtual" in stock_ir,
            "ordinary MSP430 C++/vtable probes are absent")
    stock_virtual = llvm_function(stock_ir, "stock_msp430_virtual")
    require("addrspace(1)" not in stock_virtual,
            "ordinary MSP430 virtual call inherited STC MCS51 AS1")
    stock_ptrdiff = llvm_function(
        stock_ir, "stock_msp430_pointer_difference"
    )
    require(stock_ptrdiff.count("ptrtoint ptr ") == 2
            and stock_ptrdiff.count(" to i16") >= 2
            and "sub i16" in stock_ptrdiff,
            "ordinary MSP430 ptrdiff_t no longer uses its native i16 model")
    results["ordinary_msp430"] = {
        "outcome": "PASS",
        "target_triple": "msp430-unknown-unknown",
        "stc_profile_macros": "ABSENT",
        "bitcode_verifier": "PASS",
        "native_backend": "PASS",
        "llvm_ir_sha256": digest(stock_ir_path),
        "bitcode_sha256": digest(stock_bc_path),
        "verified_llvm_ir_sha256": digest(stock_verified_path),
        "assembly_sha256": digest(stock_assembly_path),
    }

    relative_probe = probe_dir / "stock_relative_vtable_probe.cpp"
    require(relative_probe.is_file(),
            f"missing relative-vtable probe: {relative_probe}")
    relative_common = [
        str(clang), "--target=x86_64-unknown-linux-gnu", "-x", "c++",
        "-std=c++11", "-O0", "-ffreestanding", "-fno-exceptions",
        "-fno-rtti", "-fno-threadsafe-statics", "-fno-use-cxa-atexit",
        "-fno-c++-static-destructors",
        "-fexperimental-relative-c++-abi-vtables",
    ]
    relative_ir_path = output / "stock-relative-vtable.ll"
    relative_bc_path = output / "stock-relative-vtable.bc"
    relative_verified_path = output / "stock-relative-vtable.verified.ll"
    relative_assembly_path = output / "stock-relative-vtable.s"
    run(relative_common + ["-emit-llvm", "-S", str(relative_probe), "-o",
                           str(relative_ir_path)], probe_dir)
    run(relative_common + ["-emit-llvm", "-c", str(relative_probe), "-o",
                           str(relative_bc_path)], probe_dir)
    run([
        str(clang), "--target=x86_64-unknown-linux-gnu", "-x", "ir",
        "-emit-llvm", "-S", str(relative_bc_path), "-o",
        str(relative_verified_path),
    ], probe_dir)
    run(relative_common + ["-S", str(relative_probe), "-o",
                           str(relative_assembly_path)], probe_dir)
    require(relative_assembly_path.stat().st_size > 0,
            "relative-vtable native backend emitted empty assembly")

    relative_ir = relative_verified_path.read_text(encoding="utf-8")
    require('target triple = "x86_64-unknown-linux-gnu"' in relative_ir,
            "relative-vtable ordinary target triple drift")
    relative_vtables = re.findall(
        r"^@_ZTV(?:12RelativeBase|15RelativeDerived) = .*"
        r"\[5 x i32\].*align 4$",
        relative_ir,
        re.MULTILINE,
    )
    require(len(relative_vtables) == 2,
            "relative vtables are not two aligned i32 component arrays")
    require("@llvm.load.relative.i32" in relative_ir,
            "relative virtual call lowering is absent")
    require_relative_vtable_delete_flow(
        llvm_function(relative_ir, "relative_global_delete")
    )
    results["ordinary_x86_64_relative_vtables"] = {
        "outcome": "PASS",
        "target_triple": "x86_64-unknown-linux-gnu",
        "component_width": 32,
        "component_alignment": 4,
        "global_delete_offset_flow": "i32-load-sext-i64-gep",
        "bitcode_verifier": "PASS",
        "native_backend": "PASS",
        "llvm_ir_sha256": digest(relative_ir_path),
        "bitcode_sha256": digest(relative_bc_path),
        "verified_llvm_ir_sha256": digest(relative_verified_path),
        "assembly_sha256": digest(relative_assembly_path),
    }

    result_path = output / "result.json"
    result_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print("STC_CPP_DUAL_TARGET_FRONTEND=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
