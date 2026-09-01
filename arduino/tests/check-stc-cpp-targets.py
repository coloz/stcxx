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
            "llvm_ir_sha256": digest(ir_path),
            "bitcode_sha256": digest(bc_path),
            "llvm_cbe_c_sha256": digest(c_path),
            "native_backend": "BLOCKED_FAIL_CLOSED",
        }

    result_path = output / "result.json"
    result_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print("STC_CPP_DUAL_TARGET_FRONTEND=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
