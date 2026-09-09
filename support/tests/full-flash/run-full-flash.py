#!/usr/bin/env python3
"""Compile, link, and optionally execute MCS251 full-Flash regressions."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import time
from typing import Iterable, Optional


HOME = 0xFF0000
ADDRESS_END = 0x1000000


@dataclass(frozen=True)
class Profile:
    name: str
    flash_start: int
    flash_end: int
    old_limit: int
    blob_sizes: tuple[int, ...]
    switch_boundary_test: bool = False

    @property
    def capacity(self) -> int:
        return self.flash_end - self.flash_start

    @property
    def blob_count(self) -> int:
        return len(self.blob_sizes)


PROFILES = (
    Profile("stc32g12k128", 0xFE0000, ADDRESS_END, 0x10000, (60000, 60000)),
    Profile(
        "stc32g144k246",
        0xFC2800,
        ADDRESS_END,
        0x2D800,
        (52000, 63000, 63000, 63000),
        True,
    ),
)


class TestFailure(RuntimeError):
    pass


def hexadecimal(value: int) -> str:
    return f"0x{value:06x}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def command_text(command: Iterable[object]) -> str:
    return " ".join(str(item) for item in command)


def run_command(
    command: list[str],
    *,
    expected_success: bool = True,
    env: Optional[dict[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        env=env,
        check=False,
    )
    if expected_success and result.returncode != 0:
        combined = (result.stdout + result.stderr)[-12000:]
        raise TestFailure(
            f"command failed ({result.returncode}): {command_text(command)}\n"
            f"{combined}"
        )
    if not expected_success and result.returncode == 0:
        raise TestFailure(
            f"command unexpectedly succeeded: {command_text(command)}"
        )
    return result


def compile_source(
    sdcc: Path,
    source: Path,
    output_stem: Path,
    extra_flags: list[str],
) -> tuple[Path, Path, str]:
    assembly = output_stem.with_suffix(".asm")
    object_file = output_stem.with_suffix(".rel")
    common = [
        str(sdcc),
        "-mmcs251",
        "--model-large",
        "--stack-auto",
        "--std-sdcc11",
        "--opt-code-size",
        *extra_flags,
    ]
    run_command([*common, "-S", str(source), "-o", str(assembly)])
    run_command([*common, "-c", str(source), "-o", str(object_file)])
    return assembly, object_file, assembly.read_text(errors="replace")


def assembly_areas(text: str) -> tuple[set[str], dict[str, str]]:
    areas: set[str] = set()
    symbols: dict[str, str] = {}
    current_area: Optional[str] = None
    area_pattern = re.compile(r"^\s*\.area\s+([^\s(;]+)")
    label_pattern = re.compile(r"^\s*([A-Za-z_.$][A-Za-z0-9_.$]*)(?:::|:)\s*$")

    for line in text.splitlines():
        area_match = area_pattern.match(line)
        if area_match:
            current_area = area_match.group(1)
            areas.add(current_area)
            continue
        label_match = label_pattern.match(line)
        if label_match and current_area is not None:
            symbols[label_match.group(1)] = current_area
    return areas, symbols


def function_assembly(assembly: str, symbol: str) -> str:
    match = re.search(
        rf"^\s*{re.escape(symbol)}:\s*$\n(.*?)(?=^;-{{20,}}\s*$|\Z)",
        assembly,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        raise TestFailure(f"could not isolate assembly for {symbol}")
    return match.group(1)


def require_generated_areas(
    assembly: str, blob_count: int, switch_boundary_test: bool = False
) -> tuple[dict[str, str], set[str]]:
    areas, symbols = assembly_areas(assembly)
    required_code = (
        "_full_flash_low_leaf",
        "_full_flash_low_direct",
        "_full_flash_high_leaf",
        "_full_flash_high_direct",
    )
    if switch_boundary_test:
        required_code += (
            "_full_flash_dense_switch",
            "_full_flash_ejmp_switch",
        )
    required_data = ["_full_flash_ctor_table"] + [
        f"_full_flash_blob{index}" for index in range(blob_count)
    ]
    for symbol in required_code:
        area = symbols.get(symbol)
        if not area or not area.startswith("CSEG_F_"):
            raise TestFailure(
                f"{symbol} was not emitted in an independent CSEG_F_ area: {area!r}"
            )
    for symbol in required_data:
        area = symbols.get(symbol)
        if not area or not area.startswith("CONST_D_"):
            raise TestFailure(
                f"{symbol} was not emitted in an independent CONST_D_ area: {area!r}"
            )
    if symbols.get("_full_flash_last_byte") != "CABS":
        raise TestFailure("absolute top-Flash sentinel was not emitted in CABS")
    selected = {symbol: symbols[symbol] for symbol in (*required_code, *required_data)}
    if len(set(selected.values())) != len(selected):
        raise TestFailure("two full-Flash fixture symbols unexpectedly share an area")

    lowered = assembly.lower()
    required_calls = (
        r"\becall\s+_full_flash_high_leaf\b",
        r"\becall\s+_full_flash_low_leaf\b",
        r"\becall\s+@dr28\b",
    )
    for pattern in required_calls:
        if re.search(pattern, lowered) is None:
            raise TestFailure(f"missing required 24-bit call form in assembly: {pattern}")

    if switch_boundary_test:
        dense = function_assembly(assembly, "_full_flash_dense_switch").lower()
        ejmp = function_assembly(assembly, "_full_flash_ejmp_switch").lower()
        if re.search(r"\bmov\s+a,@dpx\b", dense) is None:
            raise TestFailure("dense switch does not read its component tables through DPX")
        if ">>16" not in dense or ".db" not in dense:
            raise TestFailure("dense switch lacks compact 24-bit component tables")
        if re.search(r"\bejmp\s+@dr28\b", dense) is None:
            raise TestFailure("dense switch lacks flat indirect dispatch")
        if re.search(r"\bejmp\s+@dr28\b", ejmp) is None:
            raise TestFailure("small switch lacks indirect dispatch to its EJMP table")
        direct_ejmps = re.findall(r"^\s*ejmp\s+\d+\$\s*$", ejmp, re.MULTILINE)
        if len(direct_ejmps) < 6:
            raise TestFailure("small switch lacks six inline EJMP table entries")
    return selected, areas


def runtime_library_flag(sdcc: Path) -> list[str]:
    build_root = sdcc.parent.parent
    source_root = Path(__file__).resolve().parents[3]
    candidates = (
        build_root / "share" / "sdcc" / "lib" / "mcs251-large-stack-auto",
        build_root / "device" / "lib" / "build" / "mcs251-large-stack-auto",
        build_root.parent / "device" / "lib" / "build" / "mcs251-large-stack-auto",
        source_root / "out" / "share" / "sdcc" / "lib" / "mcs251-large-stack-auto",
        source_root / "device" / "lib" / "build" / "mcs251-large-stack-auto",
    )
    for candidate in candidates:
        if candidate.is_dir():
            return [f"-L{candidate}"]
    return []


def anchor_addresses(profile: Profile) -> dict[str, int]:
    anchors = {
        "_full_flash_low_leaf": profile.flash_start + 0x400,
        "_full_flash_low_direct": profile.flash_start + 0x500,
        "_full_flash_high_leaf": HOME + 0x400,
        "_full_flash_high_direct": HOME + 0x500,
        "_full_flash_ctor_table": HOME + 0x600,
    }
    if profile.switch_boundary_test:
        anchors.update(
            {
                "_full_flash_dense_switch": 0xFCFFF0,
                "_full_flash_ejmp_switch": 0xFDFFF0,
            }
        )
    return anchors


def link_command(
    sdcc: Path,
    profile: Profile,
    object_file: Path,
    image: Path,
    symbol_areas: dict[str, str],
    *,
    code_size: Optional[int] = None,
    code_window: Optional[str] = None,
    anchor_overrides: Optional[dict[str, int]] = None,
) -> list[str]:
    anchors = anchor_addresses(profile)
    if anchor_overrides:
        anchors.update(anchor_overrides)
    flags = [
        str(sdcc),
        "-mmcs251",
        "--model-large",
        "--stack-auto",
        "--code-loc",
        hexadecimal(HOME),
        "--code-size",
        str(profile.capacity if code_size is None else code_size),
        f"-Wl-b GSINIT0={hexadecimal(profile.flash_start)}",
    ]
    if code_window is not None:
        flags.append(f"-Wl--code-window={code_window}")
    for symbol, address in anchors.items():
        flags.append(f"-Wl-b {symbol_areas[symbol]}={hexadecimal(address)}")
    return [
        *flags,
        *runtime_library_flag(sdcc),
        "-o",
        str(image),
        str(object_file),
    ]


def parse_code_window_ledger(map_text: str) -> tuple[tuple[int, int], list[dict[str, int | str]]]:
    window_pattern = re.compile(
        r"^Code Window:\s+0x([0-9A-Fa-f]+):0x([0-9A-Fa-f]+)\s*$"
    )
    area_pattern = re.compile(
        r"^Code Window Area:\s+(\S+)\s+0x([0-9A-Fa-f]+)\s+"
        r"0x([0-9A-Fa-f]+)\s*$"
    )
    window: Optional[tuple[int, int]] = None
    areas: list[dict[str, int | str]] = []
    for line in map_text.splitlines():
        match = window_pattern.match(line.strip())
        if match:
            candidate = (int(match.group(1), 16), int(match.group(2), 16))
            if window is not None and window != candidate:
                raise TestFailure("link map contains inconsistent Code Window headers")
            window = candidate
            continue
        match = area_pattern.match(line.strip())
        if match:
            areas.append(
                {
                    "name": match.group(1),
                    "address": int(match.group(2), 16),
                    "size": int(match.group(3), 16),
                }
            )
    if window is None:
        raise TestFailure("link map has no Code Window ledger header")
    if not areas:
        raise TestFailure("link map has no Code Window Area ledger rows")
    return window, areas


def parse_standard_map_areas(map_text: str) -> list[dict[str, int | str]]:
    pattern = re.compile(
        r"^([A-Za-z_][A-Za-z0-9_]*)\s+([0-9A-Fa-f]{8})\s+"
        r"([0-9A-Fa-f]{8})\s+=\s+\d+\. bytes \([^)]*CODE\)\s*$"
    )
    unique: dict[tuple[str, int, int], dict[str, int | str]] = {}
    for line in map_text.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        item = {
            "name": match.group(1),
            "address": int(match.group(2), 16),
            "size": int(match.group(3), 16),
        }
        unique[(str(item["name"]), int(item["address"]), int(item["size"]))] = item
    return list(unique.values())


def parse_symbols(map_text: str) -> dict[str, int]:
    pattern = re.compile(r"^\s*C:\s+([0-9A-Fa-f]{8})\s+(\S+)")
    symbols: dict[str, int] = {}
    for line in map_text.splitlines():
        match = pattern.match(line)
        if match:
            name = match.group(2)
            value = int(match.group(1), 16)
            if name in symbols and symbols[name] != value:
                raise TestFailure(f"map symbol {name} has conflicting values")
            symbols[name] = value
    return symbols


def read_intel_hex(path: Path) -> dict[int, int]:
    image: dict[int, int] = {}
    base = 0
    eof_seen = False
    for line_number, raw_line in enumerate(path.read_text().splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        if eof_seen or not line.startswith(":") or (len(line) - 1) % 2:
            raise TestFailure(f"malformed Intel HEX record at line {line_number}")
        try:
            record = bytes.fromhex(line[1:])
        except ValueError as error:
            raise TestFailure(f"malformed Intel HEX byte at line {line_number}") from error
        if len(record) < 5 or len(record) != record[0] + 5 or sum(record) & 0xFF:
            raise TestFailure(f"invalid Intel HEX record at line {line_number}")
        count = record[0]
        offset = (record[1] << 8) | record[2]
        kind = record[3]
        payload = record[4 : 4 + count]
        if kind == 0:
            for index, value in enumerate(payload):
                address = base + offset + index
                if address in image and image[address] != value:
                    raise TestFailure(f"conflicting Intel HEX byte at {hexadecimal(address)}")
                image[address] = value
        elif kind == 1:
            if count or offset:
                raise TestFailure("invalid Intel HEX EOF record")
            eof_seen = True
        elif kind == 2:
            if count != 2 or offset:
                raise TestFailure("invalid Intel HEX segment-address record")
            base = int.from_bytes(payload, "big") << 4
        elif kind == 4:
            if count != 2 or offset:
                raise TestFailure("invalid Intel HEX linear-address record")
            base = int.from_bytes(payload, "big") << 16
        else:
            raise TestFailure(f"unsupported Intel HEX record type {kind}")
    if not eof_seen or not image:
        raise TestFailure("Intel HEX image is empty or lacks EOF")
    return image


def image_byte(image: dict[int, int], address: int, description: str) -> int:
    try:
        return image[address]
    except KeyError as error:
        raise TestFailure(f"missing {description} at {hexadecimal(address)}") from error


def validate_startup(
    profile: Profile,
    map_text: str,
    symbols: dict[str, int],
    image: dict[int, int],
) -> dict[str, int]:
    required = ("__sdcc_mcs251_reset_trampoline", "__sdcc_gsinit_startup")
    for symbol in required:
        if symbol not in symbols:
            raise TestFailure(f"required startup symbol is absent: {symbol}")
    if symbols["__sdcc_gsinit_startup"] != profile.flash_start:
        raise TestFailure("GSINIT0 does not begin at the Flash floor")

    if image_byte(image, HOME, "reset opcode") != 0x02:
        raise TestFailure("reset vector is not an MCS251 LJMP")
    reset_target = (
        (HOME & 0xFF0000)
        | (image_byte(image, HOME + 1, "reset target") << 8)
        | image_byte(image, HOME + 2, "reset target")
    )
    if reset_target != symbols["__sdcc_mcs251_reset_trampoline"]:
        raise TestFailure("reset LJMP does not target the HOME trampoline")
    if image_byte(image, reset_target, "reset trampoline opcode") != 0x8A:
        raise TestFailure("reset trampoline is not an MCS251 EJMP")
    startup_target = (
        (image_byte(image, reset_target + 1, "startup target") << 16)
        | (image_byte(image, reset_target + 2, "startup target") << 8)
        | image_byte(image, reset_target + 3, "startup target")
    )
    if startup_target != symbols["__sdcc_gsinit_startup"]:
        raise TestFailure("HOME trampoline does not target GSINIT0")

    startup_areas = [
        area
        for area in parse_standard_map_areas(map_text)
        if int(area["size"]) and (
            str(area["name"]).startswith("GSINIT") or area["name"] == "GSFINAL"
        )
    ]
    startup_areas.sort(key=lambda item: (int(item["address"]), str(item["name"])))
    if not startup_areas or int(startup_areas[0]["address"]) != profile.flash_start:
        raise TestFailure("startup area group does not begin at the Flash floor")
    expected = profile.flash_start
    for area in startup_areas:
        if int(area["address"]) != expected:
            raise TestFailure("GSINIT/GSFINAL startup areas are not contiguous")
        expected += int(area["size"])
    return {
        "reset_trampoline": reset_target,
        "gsinit0": startup_target,
        "startup_group_end": expected,
    }


def area_for_name(
    ledger: list[dict[str, int | str]], name: str
) -> dict[str, int | str]:
    matches = [item for item in ledger if item["name"] == name]
    if len(matches) != 1:
        raise TestFailure(f"expected exactly one ledger row for {name}, found {len(matches)}")
    return matches[0]


def validate_profile(
    profile: Profile,
    symbol_areas: dict[str, str],
    image_path: Path,
) -> dict[str, object]:
    map_path = image_path.with_suffix(".map")
    map_text = map_path.read_text(errors="replace")
    window, ledger = parse_code_window_ledger(map_text)
    if window != (profile.flash_start, profile.flash_end):
        raise TestFailure(f"wrong code window in {map_path}: {window!r}")

    nonempty = [item for item in ledger if int(item["size"]) > 0]
    ordered = sorted(nonempty, key=lambda item: (int(item["address"]), str(item["name"])))
    previous_end = profile.flash_start
    previous_name = "Flash floor"
    for item in ordered:
        start = int(item["address"])
        end = start + int(item["size"])
        if start < profile.flash_start or end > profile.flash_end:
            raise TestFailure(f"ledger area {item['name']} lies outside physical Flash")
        if start < previous_end:
            raise TestFailure(f"ledger areas {previous_name} and {item['name']} overlap")
        previous_end = end
        previous_name = str(item["name"])

    mapped_bytes = sum(int(item["size"]) for item in nonempty)
    if mapped_bytes <= profile.old_limit:
        raise TestFailure(
            f"mapped code {mapped_bytes} did not exceed old limit {profile.old_limit}"
        )
    if mapped_bytes > profile.capacity:
        raise TestFailure("mapped code exceeds physical Flash capacity")

    symbols = parse_symbols(map_text)
    image = read_intel_hex(image_path)
    if len(image) <= profile.old_limit:
        raise TestFailure("materialized Intel HEX bytes did not exceed the old limit")
    if min(image) < profile.flash_start or max(image) >= profile.flash_end:
        raise TestFailure("Intel HEX contains a byte outside physical Flash")
    if max(image) != profile.flash_end - 1:
        raise TestFailure("Intel HEX does not materialize the top physical Flash byte")
    if image_byte(image, profile.flash_end - 1, "top-Flash sentinel") != 0x5A:
        raise TestFailure("top-Flash sentinel has the wrong value")

    last_byte_address = symbols.get("_full_flash_last_byte")
    if last_byte_address != profile.flash_end - 1:
        raise TestFailure("top-Flash sentinel symbol is not at 0xFFFFFF")
    absolute_rows = [
        item
        for item in ledger
        if int(item["address"]) == profile.flash_end - 1 and int(item["size"]) == 1
    ]
    if len(absolute_rows) != 1:
        raise TestFailure(
            "Code Window ledger does not account for exactly one-byte CABS sentinel"
        )

    anchors = anchor_addresses(profile)
    for symbol, expected in anchors.items():
        if symbols.get(symbol) != expected:
            raise TestFailure(
                f"{symbol} is {hexadecimal(symbols.get(symbol, -1))}, expected "
                f"{hexadecimal(expected)}"
            )
        area = area_for_name(ledger, symbol_areas[symbol])
        if int(area["address"]) != expected:
            raise TestFailure(f"anchored area for {symbol} starts at the wrong address")

    home_area = area_for_name(ledger, "HOME")
    if int(home_area["address"]) != HOME:
        raise TestFailure("HOME area does not begin at reset")
    home_end = HOME + int(home_area["size"])

    blob_locations: list[dict[str, object]] = []
    for index, blob_size in enumerate(profile.blob_sizes):
        symbol = f"_full_flash_blob{index}"
        expected_first = 0x10 + index
        expected_last = 0xA0 + index
        address = symbols.get(symbol)
        if address is None:
            raise TestFailure(f"map lacks {symbol}")
        area = area_for_name(ledger, symbol_areas[symbol])
        if int(area["size"]) != blob_size:
            raise TestFailure(f"{symbol} area does not occupy exactly {blob_size} bytes")
        if image_byte(image, address, f"{symbol} first byte") != expected_first:
            raise TestFailure(f"{symbol} first sentinel is wrong")
        if image_byte(image, address + blob_size - 1, f"{symbol} last byte") != expected_last:
            raise TestFailure(f"{symbol} last sentinel is wrong")
        blob_locations.append(
            {
                "symbol": symbol,
                "area": symbol_areas[symbol],
                "address": hexadecimal(address),
                "end": hexadecimal(address + blob_size),
                "size": blob_size,
            }
        )

    below_blobs = [
        item for item in blob_locations if int(str(item["end"]), 16) <= HOME
    ]
    above_blobs = [
        item for item in blob_locations if int(str(item["address"]), 16) >= home_end
    ]
    if not below_blobs or not above_blobs:
        raise TestFailure("large const payload was not placed on both sides of HOME")

    switch_ranges: dict[str, dict[str, object]] = {}
    if profile.switch_boundary_test:
        for symbol, boundary in (
            ("_full_flash_dense_switch", 0xFD0000),
            ("_full_flash_ejmp_switch", 0xFE0000),
        ):
            area = area_for_name(ledger, symbol_areas[symbol])
            start = int(area["address"])
            end = start + int(area["size"])
            if not start < boundary < end:
                raise TestFailure(
                    f"{symbol} does not straddle {hexadecimal(boundary)}: "
                    f"{hexadecimal(start)}:{hexadecimal(end)}"
                )
            switch_ranges[symbol] = {
                "address": hexadecimal(start),
                "end": hexadecimal(end),
                "boundary": hexadecimal(boundary),
            }

    table_address = symbols["_full_flash_ctor_table"]
    expected_table = bytearray()
    for target in (symbols["_full_flash_low_direct"], symbols["_full_flash_high_direct"]):
        expected_table.extend(((target >> 16) & 0xFF, (target >> 8) & 0xFF, target & 0xFF))
    actual_table = bytes(
        image_byte(image, table_address + offset, "constructor table")
        for offset in range(len(expected_table))
    )
    if actual_table != bytes(expected_table):
        raise TestFailure(
            "constructor table does not contain the expected big-endian 24-bit relocations"
        )

    startup = validate_startup(profile, map_text, symbols, image)
    return {
        "window": [hexadecimal(window[0]), hexadecimal(window[1])],
        "capacity": profile.capacity,
        "old_limit": profile.old_limit,
        "mapped_code_bytes": mapped_bytes,
        "intel_hex_bytes": len(image),
        "home_end": hexadecimal(home_end),
        "blob_locations": blob_locations,
        "switch_ranges": switch_ranges,
        "anchors": {name: hexadecimal(value) for name, value in anchors.items()},
        "startup": {name: hexadecimal(value) for name, value in startup.items()},
        "constructor_table_bytes": actual_table.hex(),
        "top_flash_byte": {
            "address": hexadecimal(profile.flash_end - 1),
            "value": "0x5a",
            "ledger_area": str(absolute_rows[0]["name"]),
        },
    }


def resolve_machine(qemu: Path, requested: Optional[str]) -> str:
    if requested:
        return requested
    result = run_command([str(qemu), "-machine", "help"])
    available = {
        line.split()[0]
        for line in (result.stdout + result.stderr).splitlines()
        if line.strip()
    }
    for candidate in ("stc32g144k246", "stc32g144k246-evb"):
        if candidate in available:
            return candidate
    raise TestFailure("QEMU has no stc32g144k246 MCS251 machine")


def run_qemu(qemu: Path, machine: str, image: Path) -> str:
    command = [
        str(qemu),
        "-M",
        machine,
        "-bios",
        str(image),
        "-accel",
        "tcg",
        "-icount",
        "shift=0,align=off,sleep=off",
        "-display",
        "none",
        "-monitor",
        "none",
        "-serial",
        "stdio",
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    output = bytearray()
    selector = selectors.DefaultSelector()
    assert process.stdout is not None
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + 8
    try:
        while time.monotonic() < deadline:
            events = selector.select(min(0.1, deadline - time.monotonic()))
            if events:
                chunk = os.read(process.stdout.fileno(), 4096)
                if not chunk:
                    break
                output.extend(chunk)
                normalized = bytes(output).replace(b"\r\n", b"\n")
                if b"PASS\n" in normalized or b"FAIL\n" in normalized:
                    break
            elif process.poll() is not None:
                break
    finally:
        selector.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    normalized = bytes(output).replace(b"\r\n", b"\n")
    if b"PASS\n" not in normalized or b"FAIL\n" in normalized:
        raise TestFailure(
            f"QEMU did not report PASS for {image.name}:\n"
            f"{normalized.decode(errors='replace')}"
        )
    return normalized.decode(errors="replace")


def expect_link_failure(
    label: str,
    command: list[str],
    required_fragment: Optional[str],
) -> dict[str, object]:
    result = run_command(command, expected_success=False)
    diagnostic = result.stdout + result.stderr
    if required_fragment and required_fragment.lower() not in diagnostic.lower():
        raise TestFailure(
            f"{label} did not emit {required_fragment!r}:\n{diagnostic[-8000:]}"
        )
    return {
        "name": label,
        "returncode": result.returncode,
        "required_diagnostic": required_fragment,
        "diagnostic_tail": diagnostic[-1000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdcc", required=True, type=Path)
    parser.add_argument("--qemu", type=Path)
    parser.add_argument("--machine")
    parser.add_argument("--work-dir", required=True, type=Path)
    args = parser.parse_args()

    sdcc = args.sdcc.resolve()
    qemu = args.qemu.resolve() if args.qemu else None
    work_dir = args.work_dir.resolve()
    source_dir = Path(__file__).resolve().parent
    source = source_dir / "full-flash.c"
    legacy_source = source_dir / "legacy-probe.c"
    for required in (sdcc, source, legacy_source):
        if not required.is_file():
            parser.error(f"required file does not exist: {required}")
    if qemu is not None and not qemu.is_file():
        parser.error(f"QEMU does not exist: {qemu}")
    work_dir.mkdir(parents=True, exist_ok=True)

    version = run_command([str(sdcc), "--version"])
    report: dict[str, object] = {
        "schema_version": 1,
        "status": "RUNNING",
        "sdcc": {
            "path": str(sdcc),
            "sha256": sha256(sdcc),
            "version": (version.stdout + version.stderr).splitlines()[0],
        },
        "qemu": None,
        "legacy_opt_in": {},
        "profiles": {},
        "negative_tests": [],
    }

    legacy_dir = work_dir / "legacy"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    legacy_asm, legacy_object, legacy_text = compile_source(
        sdcc, legacy_source, legacy_dir / "legacy-probe", []
    )
    legacy_areas, legacy_symbols = assembly_areas(legacy_text)
    if legacy_symbols.get("_full_flash_legacy_function") != "CSEG":
        raise TestFailure("legacy function no longer uses CSEG without opt-in")
    if legacy_symbols.get("_full_flash_legacy_constant") != "CONST":
        raise TestFailure("legacy constant no longer uses CONST without opt-in")
    if any(name.startswith(("CSEG_F_", "CONST_D_")) for name in legacy_areas):
        raise TestFailure("per-symbol areas appeared without an opt-in option")
    legacy_image = legacy_dir / "legacy-probe.hex"
    run_command(
        [
            str(sdcc),
            "-mmcs251",
            "--model-large",
            "--stack-auto",
            "--code-loc",
            hexadecimal(HOME),
            "--code-size",
            str(0x10000),
            *runtime_library_flag(sdcc),
            "-o",
            str(legacy_image),
            str(legacy_object),
        ]
    )
    legacy_map_text = legacy_image.with_suffix(".map").read_text(errors="replace")
    if "Code Window:" in legacy_map_text:
        raise TestFailure("legacy link unexpectedly enabled code-window allocation")
    legacy_map_areas = [
        area
        for area in parse_standard_map_areas(legacy_map_text)
        if int(area["size"]) > 0
    ]
    if not legacy_map_areas:
        raise TestFailure("legacy link map contains no code areas")
    for area in legacy_map_areas:
        start = int(area["address"])
        if start < HOME or start + int(area["size"]) > ADDRESS_END:
            raise TestFailure(f"legacy area {area['name']} lies outside its 64 KiB window")
    report["legacy_opt_in"] = {
        "assembly": str(legacy_asm),
        "image": str(legacy_image),
        "map": str(legacy_image.with_suffix(".map")),
        "function_area": "CSEG",
        "constant_area": "CONST",
        "code_window_ledger": False,
    }

    machine = resolve_machine(qemu, args.machine) if qemu else None
    if qemu:
        qemu_version = run_command([str(qemu), "--version"])
        report["qemu"] = {
            "path": str(qemu),
            "sha256": sha256(qemu),
            "version": (qemu_version.stdout + qemu_version.stderr).splitlines()[0],
            "machine": machine,
        }
        cast_legacy = report["legacy_opt_in"]
        assert isinstance(cast_legacy, dict)
        cast_legacy["qemu_output"] = run_qemu(qemu, machine, legacy_image)

    built: dict[str, tuple[Profile, Path, dict[str, str]]] = {}
    for profile in PROFILES:
        profile_dir = work_dir / profile.name
        profile_dir.mkdir(parents=True, exist_ok=True)
        fixture_defines = [
            f"-DFULL_FLASH_BLOB_COUNT={profile.blob_count}",
            f"-DFULL_FLASH_SWITCH_BOUNDARY_TEST={int(profile.switch_boundary_test)}",
        ]
        fixture_defines.extend(
            f"-DFULL_FLASH_BLOB{index}_BYTES={size}UL"
            for index, size in enumerate(profile.blob_sizes)
        )
        assembly, object_file, assembly_text = compile_source(
            sdcc,
            source,
            profile_dir / "full-flash",
            [
                "--function-sections",
                "--data-sections",
                *fixture_defines,
            ],
        )
        symbol_areas, _ = require_generated_areas(
            assembly_text, profile.blob_count, profile.switch_boundary_test
        )
        image = profile_dir / "full-flash.hex"
        window = f"{hexadecimal(profile.flash_start)}:{hexadecimal(profile.flash_end)}"
        run_command(
            link_command(
                sdcc,
                profile,
                object_file,
                image,
                symbol_areas,
                code_window=window,
            )
        )
        profile_result = validate_profile(profile, symbol_areas, image)
        profile_result["assembly"] = str(assembly)
        profile_result["image"] = str(image)
        profile_result["map"] = str(image.with_suffix(".map"))
        if qemu is not None and machine is not None:
            profile_result["qemu_output"] = run_qemu(qemu, machine, image)
        else:
            profile_result["qemu_output"] = None
        cast_profiles = report["profiles"]
        assert isinstance(cast_profiles, dict)
        cast_profiles[profile.name] = profile_result
        built[profile.name] = (profile, object_file, symbol_areas)

    negative_tests = report["negative_tests"]
    assert isinstance(negative_tests, list)
    k128, k128_object, k128_areas = built["stc32g12k128"]
    k128_window = f"{hexadecimal(k128.flash_start)}:{hexadecimal(k128.flash_end)}"

    negative_tests.append(
        expect_link_failure(
            "physical-image-rejected-by-old-code-size",
            link_command(
                sdcc,
                k128,
                k128_object,
                work_dir / "negative-old-code-size.hex",
                k128_areas,
                code_size=k128.old_limit,
                code_window=k128_window,
            ),
            None,
        )
    )
    negative_tests.append(
        expect_link_failure(
            "fixed-area-overlaps-home",
            link_command(
                sdcc,
                k128,
                k128_object,
                work_dir / "negative-overlap.hex",
                k128_areas,
                code_window=k128_window,
                anchor_overrides={"_full_flash_low_leaf": HOME},
            ),
            "code window overlap",
        )
    )
    negative_tests.append(
        expect_link_failure(
            "fixed-area-outside-window",
            link_command(
                sdcc,
                k128,
                k128_object,
                work_dir / "negative-outside.hex",
                k128_areas,
                code_window=k128_window,
                anchor_overrides={"_full_flash_low_leaf": k128.flash_start - 2},
            ),
            "code window allocation outside Flash bounds",
        )
    )
    negative_tests.append(
        expect_link_failure(
            "exclusive-window-end-rejects-top-byte",
            link_command(
                sdcc,
                k128,
                k128_object,
                work_dir / "negative-exclusive-end.hex",
                k128_areas,
                code_window=(
                    f"{hexadecimal(k128.flash_start)}:"
                    f"{hexadecimal(k128.flash_end - 1)}"
                ),
            ),
            "code window allocation outside Flash bounds",
        )
    )

    no_fit_dir = work_dir / "negative-no-contiguous-space"
    no_fit_dir.mkdir(parents=True, exist_ok=True)
    _, no_fit_object, no_fit_text = compile_source(
        sdcc,
        source,
        no_fit_dir / "no-fit",
        [
            "--function-sections",
            "--data-sections",
            "-DFULL_FLASH_BLOB_COUNT=1",
            "-DFULL_FLASH_BLOB0_BYTES=65000UL",
        ],
    )
    no_fit_areas, _ = require_generated_areas(no_fit_text, 1)
    negative_tests.append(
        expect_link_failure(
            "fragmented-window-has-no-fit",
            link_command(
                sdcc,
                k128,
                no_fit_object,
                no_fit_dir / "no-fit.hex",
                no_fit_areas,
                code_window=k128_window,
            ),
            "code window has no contiguous space for",
        )
    )

    report["status"] = "PASS"
    report_path = work_dir / "full-flash-report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"MCS251 full-Flash regression: PASS ({report_path})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TestFailure as error:
        print(f"MCS251 full-Flash regression: FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
