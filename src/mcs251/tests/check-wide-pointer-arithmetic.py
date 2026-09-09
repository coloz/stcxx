#!/usr/bin/env python3
"""Check 24-bit pointer arithmetic with 32-bit offsets and shared spills."""

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


def compile_source(sdcc, source, output, model_flags, assemble=False):
    run([
        str(sdcc), "-mmcs251", *model_flags, "--std=c17",
        "--fverbose-asm", "-c" if assemble else "-S",
        "-o", str(output), str(source),
    ])


def function_body(assembly, name):
    match = re.search(
        rf"^_{re.escape(name)}:$\n(.*?)(?=^;-{{20,}}$|\Z)",
        assembly,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        raise AssertionError(f"function {name} is missing from assembly")
    return match.group(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sdcc", required=True)
    parser.add_argument("--mixed-source", required=True)
    parser.add_argument("--remat-source", required=True)
    args = parser.parse_args()

    sdcc = Path(args.sdcc).resolve()
    mixed_source = Path(args.mixed_source).resolve()
    remat_source = Path(args.remat_source).resolve()
    for path in (sdcc, mixed_source, remat_source):
        if not path.exists():
            parser.error(f"required path does not exist: {path}")

    with tempfile.TemporaryDirectory(
        prefix="sdcc-mcs251-wide-pointer-arithmetic-"
    ) as temporary:
        workspace = Path(temporary)

        mixed_asm = workspace / "mixed-large.asm"
        compile_source(
            sdcc, mixed_source, mixed_asm, ("--model-large",)
        )
        mixed_text = mixed_asm.read_text()
        marker = "MCS251 deferred overlapping arithmetic result"
        for function in (
            "loop_split_plus",
            "loop_split_plus_reversed",
            "loop_split_minus",
        ):
            if marker not in function_body(mixed_text, function):
                raise AssertionError(
                    f"{function} did not defer its overlapping result"
                )
        compile_source(
            sdcc, mixed_source, workspace / "mixed-large.rel",
            ("--model-large",), assemble=True,
        )

        for model_name, model_flags in (
            ("small", ()),
            ("large", ("--model-large",)),
        ):
            remat_asm = workspace / f"remat-{model_name}.asm"
            compile_source(sdcc, remat_source, remat_asm, model_flags)
            remat_text = remat_asm.read_text()
            expected = "(_remat_storage + 0x11170)"
            truncated = "(_remat_storage + 0x1170)"
            if remat_text.count(expected) < 2 or truncated in remat_text:
                raise AssertionError(
                    f"{model_name} truncated the 0x11170 symbol addend"
                )
            if not re.search(r"^\s*\.ds\s+70016\b", remat_text,
                             re.MULTILINE):
                raise AssertionError(
                    f"{model_name} truncated the backing XDATA object"
                )
            compile_source(
                sdcc, remat_source,
                workspace / f"remat-{model_name}.rel",
                model_flags, assemble=True,
            )

    print("PASS: 24-bit pointer addends and mixed-width spills")


if __name__ == "__main__":
    main()
