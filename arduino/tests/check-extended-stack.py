#!/usr/bin/env python3
"""Strict regression coverage for the MCS251 16-bit SPX/SSEG linker path."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path


CASES = (
    (0x0800, 0x0100, 0x0700),
    (0x1000, 0x0100, 0x0F00),
    (0x4000, 0x0100, 0x3F00),
)


def run(command: list[str], cwd: Path, expect_success: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, check=False)
    if expect_success != (result.returncode == 0):
        outcome = "succeed" if expect_success else "fail"
        raise AssertionError(
            f"command was expected to {outcome}, rc={result.returncode}:\n"
            f"{' '.join(command)}\n{result.stdout}"
        )
    return result


def require(pattern: str, text: str, context: str) -> None:
    if re.search(pattern, text, re.MULTILINE) is None:
        raise AssertionError(f"missing {pattern!r} in {context}:\n{text}")


def forbid(pattern: str, text: str, context: str) -> None:
    if re.search(pattern, text, re.MULTILINE) is not None:
        raise AssertionError(f"unexpected {pattern!r} in {context}:\n{text}")


def compile_case(sdcc: Path, runtime_lib: Path, source: Path, output_dir: Path,
                 iram: int, base: int, size: int) -> None:
    case_dir = output_dir / f"mcs251-{iram:04x}"
    case_dir.mkdir(parents=True)
    image = case_dir / "smoke.ihx"
    result = run([
        str(sdcc), "-V", "-mmcs251", "--model-large", "--stack-auto",
        "--iram-size", f"0x{iram:x}",
        "--stack-loc", f"0x{base:04x}",
        "--stack-size", f"0x{size:x}",
        "-L", str(runtime_lib), "--out-fmt-ihx", str(source),
        "-o", str(image),
    ], case_dir)
    (case_dir / "driver.log").write_text(result.stdout, encoding="utf-8")

    prefix = image.with_suffix("")
    lk = prefix.with_suffix(".lk").read_text(encoding="utf-8")
    link_map = prefix.with_suffix(".map").read_text(encoding="utf-8")
    mem = prefix.with_suffix(".mem").read_text(encoding="utf-8")

    require(r"(?:^|[\\/\s])sdldmcs251(?:\s|$)", result.stdout, "MCS251 driver trace")
    require(rf"^-I 0x{iram:04x}$", lk, ".lk iram size")
    require(rf"^-S 0x{size:x}$", lk, ".lk stack size")
    require(rf"^-b SSEG = 0x{base:04x}$", lk, ".lk explicit SSEG base")
    require(rf"^C:\s+{iram:08X}\s+l_IRAM\s*$", link_map, ".map l_IRAM")
    require(rf"^C:\s+{base:08X}\s+s_SSEG\s*$", link_map, ".map s_SSEG")
    require(rf"^C:\s+{size:08X}\s+l_SSEG\s*$", link_map, ".map l_SSEG")
    require(rf"^\s*{base:08X}\s+__start__stack\s+", link_map,
            ".map __start__stack")
    require(rf"^SSEG\s+{base:08X}\s+{size:08X}\s+=\s+{size}\.",
            link_map, ".map SSEG area")
    require(
        rf"Stack starts at: 0x{base:04x} \(spx set to 0x{base - 1:04x}\) "
        rf"with {size} bytes available\.",
        mem,
        ".mem extended stack summary",
    )
    forbid(r"No clue|ERROR:|outside|overflow", mem, ".mem diagnostics")
    if not image.is_file() or image.stat().st_size == 0:
        raise AssertionError(f"missing Intel HEX output: {image}")


def check_overflow(sdcc: Path, runtime_lib: Path, source: Path, output_dir: Path) -> None:
    case_dir = output_dir / "mcs251-overflow"
    case_dir.mkdir(parents=True)
    result = run([
        str(sdcc), "-mmcs251", "--model-large", "--stack-auto",
        "--iram-size", "0x4000", "--stack-loc", "0x0200",
        "--stack-size", "0x3f00", "-L", str(runtime_lib), str(source),
        "-o", str(case_dir / "overflow.ihx"),
    ], case_dir, expect_success=False)
    (case_dir / "driver.log").write_text(result.stdout, encoding="utf-8")
    require(r"Could not get 16128 consecutive bytes in internal RAM for area SSEG",
            result.stdout, "MCS251 overflow rejection")


def check_mcs51_isolation(sdcc: Path, runtime_lib: Path, source: Path,
                          output_dir: Path) -> None:
    case_dir = output_dir / "mcs51-legacy-limit"
    case_dir.mkdir(parents=True)
    result = run([
        str(sdcc), "-V", "-mmcs51", "--model-large", "--stack-auto",
        "--iram-size", "0x4000", "--stack-loc", "0x0100",
        "--stack-size", "0x3f00", "-L", str(runtime_lib), str(source),
        "-o", str(case_dir / "legacy.ihx"),
    ], case_dir, expect_success=False)
    (case_dir / "driver.log").write_text(result.stdout, encoding="utf-8")
    require(r"(?:^|[\\/\s])sdld(?:\s|$)", result.stdout, "MCS51 driver trace")
    forbid(r"sdldmcs251", result.stdout, "MCS51 driver trace")
    require(r"Could not get 256 consecutive bytes in internal RAM for area SSEG",
            result.stdout, "MCS51 legacy 256-byte rejection")
    lk = (case_dir / "legacy.lk").read_text(encoding="utf-8")
    forbid(r"^-b SSEG = 0x0100$", lk, "MCS51 .lk")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sdcc", type=Path, required=True)
    parser.add_argument("--mcs251-runtime-lib", type=Path, required=True)
    parser.add_argument("--mcs51-runtime-lib", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()

    sdcc = args.sdcc.resolve()
    source = args.source.resolve()
    mcs251_lib = args.mcs251_runtime_lib.resolve()
    mcs51_lib = args.mcs51_runtime_lib.resolve()
    for path in (sdcc, source, mcs251_lib, mcs51_lib):
        if not path.exists():
            raise SystemExit(f"required path does not exist: {path}")

    work_dir = args.work_dir.resolve()
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    search = run([str(sdcc), "-mmcs251", "--model-large", "--stack-auto",
                  "--print-search-dirs"], work_dir)
    (work_dir / "search-dirs.log").write_text(search.stdout, encoding="utf-8")
    require(r"^programs:\n.+", search.stdout, "--print-search-dirs")
    require(r"^includedir:\n.+", search.stdout, "--print-search-dirs")
    require(r"^libdir:\n.+", search.stdout, "--print-search-dirs")

    for iram, base, size in CASES:
        compile_case(sdcc, mcs251_lib, source, work_dir, iram, base, size)
    check_overflow(sdcc, mcs251_lib, source, work_dir)
    check_mcs51_isolation(sdcc, mcs51_lib, source, work_dir)
    print("extended-stack regression: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
