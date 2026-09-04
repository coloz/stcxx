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


def check_stateless_struct_return() -> None:
    stateless_type = (
        "l_struct_struct_OC_ArduinoJson_KD__KD_V743JB42_KD__KD_detail_KD__KD_"
        "integral_constant_OC_10"
    )
    stateless_symbol = (
        "_ZNK11ArduinoJson8V743JB426detail14AllowAllFilterixImEES2_RKT_"
    )
    source = (
        f"struct {stateless_type} {{\n"
        "  uint8_t field0;\n"
        "};\n"
        f"static struct {stateless_type} {stateless_symbol}"
        "(void* _1, void* _2) {\n"
        f"  struct {stateless_type} StructReturn;  "
        "/* Struct return temporary */\n"
        f"  struct {stateless_type}* _3 = &StructReturn;\n"
        "  void* _4;\n\n"
        "  _4 = _1;\n"
        "  return StructReturn;\n"
        "}\n"
    )
    adapted, symbols = MODULE.normalize_cbe_stateless_struct_returns(source)
    require(symbols == [stateless_symbol],
            "locked ArduinoJson stateless return symbol was not audited")
    require("StructReturn = { 0 };" in adapted,
            "locked ArduinoJson stateless return was not initialized")

    initialized_symbol = (
        "_ZNK11ArduinoJson8V743JB4221DeserializationOption12NestingLimit"
        "9decrementEv"
    )
    initialized_source = (
        f"struct {stateless_type} {{\n"
        "  uint8_t field0;\n"
        "};\n"
        f"static struct {stateless_type} {initialized_symbol}(void* _10) {{\n"
        f"  struct {stateless_type} StructReturn;  "
        "/* Struct return temporary */\n"
        f"  struct {stateless_type}* _11 = &StructReturn;\n"
        "  void* _12;\n\n"
        "  _12 = _10;\n"
        "  initialize_nesting_limit(_11, 9);\n"
        "  return StructReturn;\n"
        "}\n"
    )
    preserved, preserved_symbols = (
        MODULE.normalize_cbe_stateless_struct_returns(initialized_source)
    )
    require(not preserved_symbols,
            "constructor-initialized non-target return was audited as stateless")
    require(preserved == initialized_source,
            "constructor-initialized non-target return was rewritten")

    rejection_cases = {
        "wrong_return_type": source.replace(
            stateless_type, "l_struct_struct_OC_std_KD__KD_nothrow_t"
        ),
        "non_placeholder_type": source.replace(
            "  uint8_t field0;\n", "  uint8_t field0;\n  uint8_t field1;\n"
        ),
        "wrong_use_count": source.replace(
            "  return StructReturn;\n",
            "  StructReturn.field0 = 1;\n  return StructReturn;\n",
        ),
        "non_target_function": source.replace(
            stateless_symbol,
            "_ZNK11ArduinoJson8V743JB426detail15RejectAllFilterixImEES2_RKT_",
        ),
    }
    rejected = []
    for label, candidate in rejection_cases.items():
        try:
            MODULE.normalize_cbe_stateless_struct_returns(candidate)
        except MODULE.AuditError:
            rejected.append(label)
    require(rejected == list(rejection_cases),
            f"stateless return rejection mismatch: {rejected!r}")


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

    check_stateless_struct_return()
    print("BRIDGE_ADAPTER_REGRESSION=PASS")


if __name__ == "__main__":
    main()
