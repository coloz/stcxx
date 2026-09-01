#!/usr/bin/env python3
"""Fail-closed regression for the standalone Arduino C++ bridge adapter."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / "arduino" / "bridge" / "audit_and_adapt.py"
SPEC = importlib.util.spec_from_file_location("stcxx_bridge_adapter", ADAPTER)
if SPEC is None or SPEC.loader is None:
    raise SystemExit(f"unable to load bridge adapter: {ADAPTER}")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def main() -> None:
    source = """
uint32_t q = llvm_udiv_u32(_123, 256UL);
uint32_t r = llvm_urem_u32(_124, 0x100u);
uint32_t keep_non_power = llvm_udiv_u32(_125, 255UL);
uint32_t keep_effect = llvm_urem_u32(load_next(), 256UL);
"""
    adapted, rewrites = MODULE.normalize_cbe_u32_power_of_two_division(source)

    require("(((uint32_t)_123) >> 8u)" in adapted,
            "power-of-two udiv was not rewritten to a shift")
    require("(((uint32_t)_124) & 255UL)" in adapted,
            "power-of-two urem was not rewritten to a mask")
    require("llvm_udiv_u32(_125, 255UL)" in adapted,
            "non-power-of-two division must remain untouched")
    require("llvm_urem_u32(load_next(), 256UL)" in adapted,
            "side-effecting dividend must remain untouched")
    require(rewrites == [
        {"operation": "udiv", "divisor": 256, "shift": 8},
        {"operation": "urem", "divisor": 256, "shift": 8},
    ], "unexpected bridge rewrite audit")

    print("BRIDGE_ADAPTER_REGRESSION=PASS")


if __name__ == "__main__":
    main()
