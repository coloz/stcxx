#!/usr/bin/env python3
"""Fail-closed mechanical verifier for the Clang STC MCS251 IR-only profile.

Passing this checker proves only the frontend data-model shape and the native
code-generation guard.  It deliberately leaves the production C/C++ ABI,
address-space mapping, and SDCC compatibility as NOT_PROVEN.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any


TARGET = "msp430-stc-none-eabi"
REJECTED_DIGIT_TARGET = "msp430-unknown-none-stcsdcc251"
EXPECTED_DATALAYOUT = (
    "E-m:e-p:24:8-i8:8-i16:8-i32:8-i64:8-i128:8-f32:8-f64:8-"
    "f128:8-a:8-n8:16:32-S8"
)
STOCK_DATALAYOUT = (
    "e-m:e-p:16:16-i32:16-i64:16-f32:16-f64:16-a:8-n8:16-S16"
)
NATIVE_GATE_TEXT = "experimental LLVM IR-only profile"

SCRIPT_DIR = Path(__file__).resolve().parent
PROBE_DIR = SCRIPT_DIR / "probe"
PROBE = PROBE_DIR / "target_info_probe.cpp"
STOCK_PROBE = PROBE_DIR / "stock_msp430_probe.cpp"
PATCH = SCRIPT_DIR / "clang-20.1.8-stcsdcc-ir-only.patch"


class CheckFailure(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def parse_macros(text: str) -> dict[str, str]:
    macros: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^#define\s+(\S+)(?:\s+(.*))?$", line)
        if match:
            macros[match.group(1)] = (match.group(2) or "").strip()
    return macros


def extract_ir_header(ir: str, key: str) -> str:
    match = re.search(rf'^target {key} = "([^"]+)"$', ir, re.MULTILINE)
    if not match:
        raise CheckFailure(f"LLVM IR has no target {key}")
    return match.group(1)


class Recorder:
    def __init__(self, output: Path) -> None:
        self.output = output
        self.commands: list[dict[str, Any]] = []

    def run(self, label: str, argv: list[str], cwd: Path = PROBE_DIR) -> subprocess.CompletedProcess[str]:
        index = len(self.commands) + 1
        environment = os.environ.copy()
        environment["LC_ALL"] = "C"
        process = subprocess.run(
            argv,
            cwd=str(cwd),
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        stdout_path = self.output / f"{index:02d}-{label}.stdout.txt"
        stderr_path = self.output / f"{index:02d}-{label}.stderr.txt"
        stdout_path.write_text(process.stdout, encoding="utf-8")
        stderr_path.write_text(process.stderr, encoding="utf-8")
        self.commands.append(
            {
                "id": index,
                "label": label,
                "argv": argv,
                "cwd": str(cwd),
                "returncode": process.returncode,
                "stdout": stdout_path.name,
                "stdout_sha256": sha256_file(stdout_path),
                "stderr": stderr_path.name,
                "stderr_sha256": sha256_file(stderr_path),
            }
        )
        return process


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clang", required=True,
                        help="patched Clang 20.1.8 driver")
    parser.add_argument("--clang-source", type=Path,
                        help="patched Clang 20.1.8 source root for ABI audit")
    parser.add_argument("--output", type=Path,
                        default=SCRIPT_DIR / ".build" / "check")
    args = parser.parse_args()

    clang_lookup = shutil.which(args.clang)
    clang = Path(clang_lookup if clang_lookup else args.clang).resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    recorder = Recorder(output)
    checks: list[dict[str, Any]] = []
    failure: str | None = None

    def require(check_id: str, condition: bool, evidence: str) -> None:
        status = "PASS" if condition else "FAIL"
        checks.append({"id": check_id, "status": status, "evidence": evidence})
        if not condition:
            raise CheckFailure(f"{check_id}: {evidence}")

    common = [
        str(clang),
        f"--target={TARGET}",
        "-x", "c++",
        "-std=gnu++11",
        "-ffreestanding",
        "-fno-exceptions",
        "-fno-rtti",
        "-fno-threadsafe-statics",
        "-fno-c++-static-destructors",
        "-fno-unwind-tables",
        "-fno-asynchronous-unwind-tables",
        "-nostdinc++",
    ]

    metadata: dict[str, Any] = {
        "schema_version": 1,
        "target": TARGET,
        "clang": str(clang),
        "clang_sha256": None,
        "clang_version": None,
        "patch_sha256": sha256_file(PATCH) if PATCH.exists() else None,
        "probe_sha256": sha256_file(PROBE),
        "stock_probe_sha256": sha256_file(STOCK_PROBE),
    }

    try:
        require("clang.exists", clang.is_file(), f"resolved compiler: {clang}")
        metadata["clang_sha256"] = sha256_file(clang)

        version = recorder.run("clang-version", [str(clang), "--version"])
        version_line = version.stdout.splitlines()[0] if version.stdout else ""
        metadata["clang_version"] = version_line
        require("clang.version", version.returncode == 0 and
                re.search(r"\b20\.1\.8\b", version_line) is not None,
                f"expected 20.1.8, got {version_line!r}")

        macro_run = recorder.run(
            "target-macros", common + ["-dM", "-E", PROBE.name]
        )
        require("target.macros.command", macro_run.returncode == 0,
                "target macro preprocessing succeeds")
        macros = parse_macros(macro_run.stdout)
        expected_macros = {
            "__STC_MCS251__": "1",
            "__STC_CLANG_IR_ONLY__": "1",
            "__CHAR_UNSIGNED__": "1",
            "__CHAR_BIT__": "8",
            "__SIZEOF_SHORT__": "2",
            "__SIZEOF_INT__": "2",
            "__SIZEOF_LONG__": "4",
            "__SIZEOF_POINTER__": "3",
            "__SIZEOF_SIZE_T__": "4",
            "__SIZEOF_PTRDIFF_T__": "4",
            "__SIZEOF_FLOAT__": "4",
            "__SIZEOF_DOUBLE__": "4",
            "__SIZEOF_LONG_DOUBLE__": "4",
            "__BIGGEST_ALIGNMENT__": "1",
        }
        for name, expected in expected_macros.items():
            require(f"macro.{name}", macros.get(name) == expected,
                    f"expected {name}={expected}, got {macros.get(name)!r}")
        require("macro.big_endian",
                macros.get("__BYTE_ORDER__") == "__ORDER_BIG_ENDIAN__",
                f"__BYTE_ORDER__={macros.get('__BYTE_ORDER__')!r}")
        for forbidden in ("MSP430", "__MSP430__", "__SDCC_mcs251"):
            require(f"macro.absent.{forbidden}", forbidden not in macros,
                    f"forbidden impersonation macro {forbidden} is absent")

        ir_path = output / "target_info_probe.ll"
        ir_run = recorder.run(
            "target-ir", common + ["-O0", "-emit-llvm", "-S", PROBE.name,
                                    "-o", str(ir_path)]
        )
        require("target.ir.command", ir_run.returncode == 0 and ir_path.is_file(),
                "C++ probe emits textual LLVM IR")
        ir = ir_path.read_text(encoding="utf-8")
        datalayout = extract_ir_header(ir, "datalayout")
        triple = extract_ir_header(ir, "triple")
        require("target.ir.triple", triple == TARGET,
                f"target triple is exactly {triple}")
        require("target.ir.datalayout", datalayout == EXPECTED_DATALAYOUT,
                f"data layout is exactly {datalayout}")

        scalar_line = next((line for line in ir.splitlines()
                            if "@bridge_scalars(" in line and
                            line.lstrip().startswith("define ")), "")
        require("target.ir.scalar_signature",
                "i16" in scalar_line and "i32" in scalar_line and
                "ptr" in scalar_line and scalar_line.count("float") >= 3,
                scalar_line)
        require("target.ir.vtable", "@_ZTV4Base" in ir and
                "@bridge_virtual(" in ir,
                "Itanium-style vtable and virtual dispatch are present")
        require("target.ir.data_member_pointer",
                re.search(r"define .* i24 @bridge_data_member\(", ir) is not None and
                "@observed_data_member = dso_local global i24" in ir,
                "CodeGen lowers a data-member pointer to i24 while the AST "
                "probe reports sizeof=4")
        require("target.ir.method_member_pointer",
                re.search(r"define .* void @bridge_(plain|virtual)_member\("
                          r"ptr .*sret\(\{ i24, i24 \}\)", ir) is not None and
                "@observed_plain_member = dso_local global { i24, i24 }" in ir,
                "CodeGen lowers a method-member pointer to an sret i24 pair "
                "while the AST probe reports sizeof=8")
        require("target.ir.member_function_ptrtoint",
                re.search(r"ptrtoint \(ptr .* to i24\)", ir) is not None,
                "non-virtual member function address is encoded in an i24")
        require("target.ir.explicit_address_space",
                "ptr addrspace(1)" in ir,
                "numeric address_space(1) survives in IR")
        for forbidden in ("llvm.global_dtors", "__cxa_atexit", "@atexit"):
            require(f"target.ir.absent.{forbidden}", forbidden not in ir,
                    f"forbidden destructor registration {forbidden} is absent")

        bitcode_path = output / "target_info_probe.bc"
        bitcode_run = recorder.run(
            "target-bitcode", common + ["-O0", "-emit-llvm", "-c", PROBE.name,
                                         "-o", str(bitcode_path)]
        )
        bitcode_magic = bitcode_path.read_bytes()[:4] if bitcode_path.exists() else b""
        require("target.bitcode", bitcode_run.returncode == 0 and
                bitcode_magic == b"BC\xc0\xde",
                f"LLVM bitcode magic: {bitcode_magic.hex()}")

        native_outputs = [
            ("target-native-object", ["-c"], output / "forbidden.o"),
            ("target-native-assembly", ["-S"], output / "forbidden.s"),
        ]
        for label, mode, native_output in native_outputs:
            if native_output.exists():
                native_output.unlink()
            native_run = recorder.run(
                label, common + mode + [PROBE.name, "-o", str(native_output)]
            )
            diagnostic = native_run.stdout + native_run.stderr
            require(f"{label}.fails_closed",
                    native_run.returncode != 0 and NATIVE_GATE_TEXT in diagnostic,
                    "machine backend rejected with the IR-only diagnostic")
            require(f"{label}.no_payload",
                    not native_output.exists() or native_output.stat().st_size == 0,
                    "no non-empty native payload was produced")

        signed_macros_run = recorder.run(
            "signed-char-macros",
            common + ["-fsigned-char", "-dM", "-E", STOCK_PROBE.name],
        )
        signed_macros = parse_macros(signed_macros_run.stdout)
        require("signed_char.override_detected",
                signed_macros_run.returncode == 0 and
                "__CHAR_UNSIGNED__" not in signed_macros,
                "an explicit -fsigned-char overrides the driver default")
        signed_gate = recorder.run(
            "signed-char-probe-gate",
            common + ["-fsigned-char", "-fsyntax-only", PROBE.name],
        )
        require("signed_char.probe_rejects_override",
                signed_gate.returncode != 0 and
                "plain char must be unsigned" in
                (signed_gate.stdout + signed_gate.stderr),
                "ABI probe rejects explicit signed plain char")

        rejected_target = recorder.run(
            "digit-environment-rejected",
            [str(clang), f"--target={REJECTED_DIGIT_TARGET}", "-x", "c++",
             "-fsyntax-only", STOCK_PROBE.name],
        )
        require("triple.digit_suffix_rejected",
                rejected_target.returncode != 0 and
                "is invalid" in (rejected_target.stdout + rejected_target.stderr),
                f"{REJECTED_DIGIT_TARGET} is deliberately not the profile name")

        stock_common = [
            str(clang), "--target=msp430-unknown-none", "-x", "c++",
            "-std=gnu++11", "-ffreestanding", "-fno-exceptions", "-fno-rtti",
        ]
        stock_macro_run = recorder.run(
            "stock-msp430-macros",
            stock_common + ["-dM", "-E", STOCK_PROBE.name],
        )
        stock_macros = parse_macros(stock_macro_run.stdout)
        require("stock_msp430.macros", stock_macro_run.returncode == 0 and
                stock_macros.get("__MSP430__") == "1" and
                stock_macros.get("__SIZEOF_POINTER__") == "2",
                "ordinary MSP430 macros and 16-bit pointer model are unchanged")
        stock_ir_path = output / "stock_msp430_probe.ll"
        stock_ir_run = recorder.run(
            "stock-msp430-ir",
            stock_common + ["-emit-llvm", "-S", STOCK_PROBE.name,
                            "-o", str(stock_ir_path)],
        )
        require("stock_msp430.ir.command",
                stock_ir_run.returncode == 0 and stock_ir_path.is_file(),
                "ordinary MSP430 still emits IR")
        stock_ir = stock_ir_path.read_text(encoding="utf-8")
        require("stock_msp430.datalayout",
                extract_ir_header(stock_ir, "datalayout") == STOCK_DATALAYOUT,
                "ordinary MSP430 data layout is unchanged")
        stock_asm_path = output / "stock_msp430_probe.s"
        stock_asm_run = recorder.run(
            "stock-msp430-native-assembly",
            stock_common + ["-S", STOCK_PROBE.name, "-o", str(stock_asm_path)],
        )
        require("stock_msp430.native_scope",
                stock_asm_run.returncode == 0 and stock_asm_path.is_file() and
                stock_asm_path.stat().st_size > 0,
                "native-code guard does not block the ordinary MSP430 target")

        if args.clang_source is not None:
            source_root = args.clang_source.resolve()
            audit_files = {
                "target_info": source_root / "lib/Basic/TargetInfo.cpp",
                "codegen_dispatch": source_root / "lib/CodeGen/CodeGenModule.cpp",
                "msp430_abi": source_root / "lib/CodeGen/Targets/MSP430.cpp",
                "patched_target": source_root / "lib/Basic/Targets/MSP430.h",
                "patched_backend": source_root / "lib/CodeGen/BackendUtil.cpp",
                "patched_driver": source_root / "lib/Driver/ToolChains/Clang.cpp",
            }
            for name, path in audit_files.items():
                require(f"source.{name}.exists", path.is_file(), str(path))
            audit_text = {name: path.read_text(encoding="utf-8")
                          for name, path in audit_files.items()}
            require("source.cxxabi.generic_itanium",
                    "TargetCXXABI::GenericItanium" in audit_text["target_info"],
                    "TargetInfo defaults this non-MSVC triple to Generic Itanium")
            require("source.codegen.msp430_dispatch",
                    re.search(r"case llvm::Triple::msp430:\s*return "
                              r"createMSP430TargetCodeGenInfo",
                              audit_text["codegen_dispatch"]) is not None,
                    "CodeGen dispatch remains arch-based MSP430")
            require("source.codegen.default_abi",
                    "class MSP430ABIInfo : public DefaultABIInfo" in
                    audit_text["msp430_abi"],
                    "IR calling convention is inherited from MSP430ABIInfo")
            require("source.address_space.default_map",
                    "AddrSpaceMap = &DefaultAddrSpaceMap" in
                    audit_text["target_info"],
                    "language address spaces still use the default collapsed map")
            require("source.native_gate",
                    'getVendorName() == "stc"' in
                    audit_text["patched_backend"] and
                    NATIVE_GATE_TEXT in audit_text["patched_backend"],
                    "backend contains the synthetic-environment machine-code gate")
            require("source.char_driver_default",
                    'getVendorName() == "stc"' in
                    audit_text["patched_driver"] and
                    'CmdArgs.push_back("-fno-signed-char")' in
                    audit_text["patched_driver"],
                    "driver explicitly injects unsigned plain-char mode")
            metadata["source_root"] = str(source_root)
            metadata["source_audit_sha256"] = {
                name: sha256_file(path) for name, path in audit_files.items()
            }
        else:
            checks.append({
                "id": "source.abi_audit",
                "status": "NOT_RUN",
                "evidence": "pass --clang-source to audit inherited ABI sources",
            })

    except (CheckFailure, OSError, subprocess.SubprocessError) as error:
        failure = str(error)

    commands_path = output / "commands.json"
    write_json(commands_path, recorder.commands)
    artifacts = {}
    for path in sorted(output.iterdir(), key=lambda item: item.name):
        if path.is_file() and path.name != "result.json":
            artifacts[path.name] = {
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }

    result = {
        **metadata,
        "overall": "PASS_MECHANICAL_IR_ONLY" if failure is None else "FAIL",
        "failure": failure,
        "checks": checks,
        "claims": {
            "targetinfo_scalar_data_model": "PASS" if failure is None else "FAIL",
            "driver_plain_char_default": "PASS" if failure is None else "FAIL",
            "native_machine_codegen": "BLOCKED_FAIL_CLOSED" if failure is None else "FAIL",
            "llvm_ir_emission": "PASS" if failure is None else "FAIL",
            "production_c_calling_abi": "NOT_PROVEN_MSP430_ABIINFO",
            "production_cxx_abi": "NOT_PROVEN_GENERIC_ITANIUM",
            "member_pointers": "NOT_SUPPORTED_AST_32_64_VS_IR_I24_AND_I24X2",
            "vtable_and_function_pointer_abi": "NOT_PROVEN",
            "sdcc_address_space_mapping": "NOT_PROVEN_DEFAULT_MAP_COLLAPSES",
            "varargs_abi": "NOT_PROVEN_CHAR_PTR_VA_LIST",
            "sdcc_object_or_link_compatibility": "NOT_TESTED",
        },
        "artifacts": artifacts,
    }
    write_json(output / "result.json", result)

    if failure is not None:
        print(f"TARGETINFO_IR_SHAPE=FAIL: {failure}", file=sys.stderr)
        print("PRODUCTION_ABI=NOT_PROVEN", file=sys.stderr)
        return 1
    print("TARGETINFO_IR_SHAPE=PASS")
    print("NATIVE_MACHINE_CODE=BLOCKED_FAIL_CLOSED")
    print("PRODUCTION_ABI=NOT_PROVEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
