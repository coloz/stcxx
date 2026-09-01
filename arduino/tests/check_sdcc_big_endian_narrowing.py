#!/usr/bin/env python3
"""Verify MCS251 pointer offsets and overlapping multi-byte arithmetic."""

from __future__ import annotations

import argparse
import re
import subprocess
import tempfile
from pathlib import Path


FUNCTION_END = re.compile(r"(?m)^\s*\.area\s+")
OFFSET_GET = re.compile(
    r"(?im)^\s*mov\s+dr28\s*,\s*dpx\s*$"
    r"(?:\s*;[^\n]*\n)*"
    r"\s*inc\s+dpx\s*$"
    r"(?:\s*;[^\n]*\n)*"
    r"\s*ecall\s+__gptrget\s*$"
)
FAR_OFFSET_GET = re.compile(
    r"(?im)^\s*mov\s+dr28\s*,\s*dpx\s*$"
    r"(?:\s*;[^\n]*\n)*"
    r"\s*inc\s+dpx\s*$"
    r"(?:\s*;[^\n]*\n)*"
    r"\s*mov\s+a\s*,\s*@dpx\s*$"
)
FIELD_OFFSET_ADD = re.compile(
    r"(?im)^\s*mov\s+a\s*,\s*#0x05\s*$"
    r"(?:\s*;[^\n]*\n)*"
    r"\s*add\s+a\s*,\s*r7\s*$"
)


def function_body(assembly: str, symbol: str) -> str:
    match = re.search(rf"(?m)^_{re.escape(symbol)}:\s*$", assembly)
    if not match:
        raise AssertionError(f"assembly is missing function _{symbol}")
    tail = assembly[match.end() :]
    end = FUNCTION_END.search(tail)
    return tail[: end.start()] if end else tail


def compile_fixture(sdcc: Path, source: Path, output: Path) -> str:
    command = [
        str(sdcc),
        "-mmcs251",
        "--model-large",
        "--stack-auto",
        "--std-sdcc11",
        "--opt-code-size",
        "--less-pedantic",
        "-c",
        str(source),
        "-o",
        str(output),
    ]
    completed = subprocess.run(command, text=True, capture_output=True)
    if completed.returncode:
        raise SystemExit(completed.stdout + completed.stderr)
    return output.with_suffix(".asm").read_text(encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sdcc", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--far-source", required=True, type=Path)
    parser.add_argument("--overlap-source", required=True, type=Path)
    args = parser.parse_args()

    sdcc = args.sdcc.resolve()
    source = args.source.resolve()
    far_source = args.far_source.resolve()
    overlap_source = args.overlap_source.resolve()
    for path in (sdcc, source, far_source, overlap_source):
        if not path.exists():
            parser.error(f"required path does not exist: {path}")

    with tempfile.TemporaryDirectory(prefix="mcs251-be-narrowing-") as temp:
        temp_path = Path(temp)
        assembly = compile_fixture(
            sdcc, source, temp_path / "big-endian-narrowing.rel"
        )
        for symbol in ("mcs251_store_at_u32_low24", "mcs251_load_u16_low8"):
            body = function_body(assembly, symbol)
            if not OFFSET_GET.search(body):
                raise AssertionError(
                    f"_{symbol} did not apply the +1 displacement to DPX "
                    "before its first __gptrget"
                )

        far_assembly = compile_fixture(
            sdcc, far_source, temp_path / "far-memory-low-byte.rel"
        )
        far_body = function_body(far_assembly, "mcs251_load_far_u16_low8")
        if not FAR_OFFSET_GET.search(far_body):
            raise AssertionError(
                "_mcs251_load_far_u16_low8 did not apply the +1 "
                "displacement to DPX before its narrowed @dpx load"
            )

        overlap_assembly = compile_fixture(
            sdcc, overlap_source, temp_path / "overlap-generic-store.rel"
        )
        overlap_body = function_body(overlap_assembly, "mcs251_replace_byte")
        field_add = FIELD_OFFSET_ADD.search(overlap_body)
        if not field_add:
            raise AssertionError(
                "_mcs251_replace_byte no longer exercises object + 5 "
                "with the expected overlapping register allocation"
            )
        field_add_tail = overlap_body[field_add.end() :]
        high_source_read = re.search(
            r"(?im)^\s*addc\s+a\s*,\s*r5\s*$", field_add_tail
        )
        low_destination_write = re.search(
            r"(?im)^\s*mov\s+r5\s*,\s*a\s*$", field_add_tail
        )
        if not high_source_read or not low_destination_write:
            raise AssertionError(
                "_mcs251_replace_byte is missing the expected r5 overlap"
            )
        if low_destination_write.start() < high_source_read.start():
            raise AssertionError(
                "object + 5 overwrote destination r5 before reading "
                "the overlapping high source byte"
            )
        if not re.search(r"(?im)^\s*ecall\s+__gptrput\s*$", overlap_body):
            raise AssertionError(
                "_mcs251_replace_byte no longer reaches generic byte store"
            )

    print(
        "MCS251 pointer offsets and arithmetic overlap: PASS "
        "(generic u32->u24, generic u16->u8, far u16->u8, object+5 store)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
