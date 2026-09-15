#!/usr/bin/env python3
"""Verify that &pointer[index] retains its extended address space."""

import argparse
from pathlib import Path
import re
import subprocess
import tempfile


def run(command):
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        check=False,
    )
    if completed.returncode:
        raise AssertionError(
            f"command failed ({completed.returncode}): "
            f"{' '.join(command)}\n{completed.stdout}"
        )


def function_body(assembly, name):
    match = re.search(
        rf"^_{re.escape(name)}:$\n(.*?)(?=^;-{{20,}}$|\Z)",
        assembly,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        raise AssertionError(f"function {name} is missing from assembly")
    return match.group(1)


def require_path(assembly, lane, functions, marker, operation):
    for function in functions:
        body = function_body(assembly, function)
        if marker not in body or not re.search(operation, body, re.MULTILINE):
            raise AssertionError(
                f"{lane}:{function} did not retain the {marker} path"
            )
        if "genNearPointerGet" in body or "genNearPointerSet" in body:
            raise AssertionError(
                f"{lane}:{function} incorrectly selected a near pointer"
            )


def check_lane(sdcc, source, workspace, lane, port, stack_auto):
    assembly_path = workspace / f"{lane}.asm"
    object_path = workspace / f"{lane}.rel"
    flags = [f"-m{port}", "--model-large", "--std=c17"]
    if stack_auto:
        flags.append("--stack-auto")

    run([
        str(sdcc), *flags, "--fverbose-asm", "-S", "-o",
        str(assembly_path), str(source),
    ])
    assembly = assembly_path.read_text()

    require_path(
        assembly,
        lane,
        ("generic_load_redundant", "generic_load_canonical"),
        "genGenPointerGet",
        (r"^[ \t]*mov[ \t]+a,@dpx[ \t]*$" if port == "mcs251"
         else r"\b__gptrget\b"),
    )
    require_path(
        assembly,
        lane,
        ("generic_store_redundant", "generic_store_canonical"),
        "genGenPointerSet",
        (r"^[ \t]*mov[ \t]+@dpx,a[ \t]*$" if port == "mcs251"
         else r"\b__gptrput\b"),
    )

    if port == "mcs251":
        far_load = r"^[ \t]*mov[ \t]+a,@dpx[ \t]*$"
        far_store = r"^[ \t]*mov[ \t]+@dpx,a[ \t]*$"
        code_load = far_load
    else:
        far_load = r"^[ \t]*movx[ \t]+a,@dptr[ \t]*$"
        far_store = r"^[ \t]*movx[ \t]+@dptr,a[ \t]*$"
        code_load = r"^[ \t]*movc[ \t]+a,@a\+dptr[ \t]*$"

    require_path(
        assembly,
        lane,
        ("xdata_load_redundant", "xdata_load_canonical"),
        "genFarPointerGet",
        far_load,
    )
    require_path(
        assembly,
        lane,
        ("xdata_store_redundant", "xdata_store_canonical"),
        "genFarPointerSet",
        far_store,
    )
    require_path(
        assembly,
        lane,
        ("code_load_redundant", "code_load_canonical"),
        "genCodePointerGet",
        code_load,
    )

    run([
        str(sdcc), *flags, "-c", "-o", str(object_path), str(source),
    ])
    if not object_path.is_file():
        raise AssertionError(f"{lane} did not produce an object file")

    print(f"PASS: {lane} retained generic/code/xdata pointer paths")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sdcc", required=True)
    parser.add_argument("--source", required=True)
    args = parser.parse_args()

    sdcc = Path(args.sdcc).resolve()
    source = Path(args.source).resolve()
    for path in (sdcc, source):
        if not path.exists():
            parser.error(f"required path does not exist: {path}")

    with tempfile.TemporaryDirectory(
        prefix="sdcc-generic-address-roundtrip-"
    ) as temporary:
        workspace = Path(temporary)
        configurations = (
            ("mcs251", "mcs251", False),
            ("mcs251-stack-auto", "mcs251", True),
            ("mcs51", "mcs51", False),
            ("mcs51-stack-auto", "mcs51", True),
        )
        for lane, port, stack_auto in configurations:
            check_lane(
                sdcc, source, workspace, lane, port, stack_auto
            )


if __name__ == "__main__":
    main()
