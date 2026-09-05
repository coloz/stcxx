#!/usr/bin/env python3
"""Compatibility entry point for the authoritative STC C++ ABI checker."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import runpy
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
        rf"^define\b[^{{]*@{re.escape(symbol)}\([^)]*\)[^{{]*\{{\n"
        r"(?P<body>.*?)^\}",
        re.MULTILINE | re.DOTALL,
    )
    matches = list(pattern.finditer(ir))
    require(len(matches) == 1,
            f"expected one LLVM definition for {symbol}, got {len(matches)}")
    return matches[0].group(0)


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

        ir = ir_path.read_text(encoding="utf-8")
        generated_c = c_path.read_text(encoding="utf-8")
        bits = int(profile["member_bits"])
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
        require("@bridge_function_pointer" in ir and "@bridge_indirect" in ir,
                f"{name} ordinary function-pointer probes are absent")
        require("@bridge_data_member_apply" in ir
                and "@bridge_method_member_apply" in ir,
                f"{name} member-pointer apply probes are absent")

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
            require(body.count("ptrtoint ptr ") == 2
                    and body.count(" to i32") >= 2
                    and "sub i32" in body
                    and "sub i24" not in body,
                    f"{name} {symbol} did not compute ptrdiff_t in i32")
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
        call_address_space = " addrspace(1)" if name == "mcs51" else ""
        require(
            re.search(
                rf"\bcall{call_address_space} i32 "
                rf"@bridge_pointer_difference_consume\(i32 noundef "
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
            require("zext i24" not in ir,
                    "MCS51 contains an invalid i24-to-i16 zero extension")
        if name == "mcs51":
            require("inttoptr i16" in ir and "to ptr addrspace(1)" in ir,
                    "MCS51 nonvirtual member call escaped program AS1")
            require("addrspacecast ptr" in ir and "to ptr addrspace(1)" in ir,
                    "MCS51 virtual member call escaped program AS1")
            require("call noundef addrspace(1)" in ir,
                    "MCS51 member indirect call is not a program call")
            require("llvm_cbe_program_pointer" in generated_c,
                    "LLVM-CBE lost the MCS51 program-pointer type")
        else:
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
            "data_member_pointer_bytes": profile["data_member_bytes"],
            "method_member_pointer_bytes": profile["method_member_bytes"],
            "pointer_difference_ir_width": 32,
            "size_t_ir_width": size_bits,
            "array_cookie_bytes": cookie_bytes,
            "bitcode_verifier": "PASS",
            "llvm_ir_sha256": digest(ir_path),
            "bitcode_sha256": digest(bc_path),
            "verified_llvm_ir_sha256": digest(verified_ir_path),
            "llvm_cbe_c_sha256": digest(c_path),
            "native_backend": "BLOCKED_FAIL_CLOSED",
        }

    result_path = output / "result.json"
    result_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print("STC_CPP_DUAL_TARGET_FRONTEND=PASS")
    return 0


if __name__ == "__main__":
    # Keep historical callers fail-closed by delegating to the one maintained
    # checker instead of carrying a weaker, drifting second implementation.
    runpy.run_path(
        str(Path(__file__).with_name("check-stc-cpp-targets.py")),
        run_name="__main__",
    )
