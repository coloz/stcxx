#!/usr/bin/env python3
"""Audit Arduino C++ IR and adapt LLVM-CBE output for SDCC MCS51/MCS251."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path


ADAPTER_SCHEMA_VERSION = 2
ASXXXX_SAFE_C_IDENTIFIER_MAX = 254
C_IDENTIFIER_SHORTENING_PREFIX = "stcxx_cbe_id_"
C_IDENTIFIER_SHORTENING_HASH = "sha256"
C_IDENTIFIER_SHORTENING_DOMAIN = "stcxx-cbe-c-identifier-v1"
C_IDENTIFIER_SHORTENING_POLICY = "final-c-preprocessing-token-sha256-v1"
C_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
LLVM_PLAIN_SYMBOL = re.compile(r"[-A-Za-z$._0-9]+\Z")
LLVM_SYMBOL_TOKEN = r'(?:"((?:[^"\\]|\\[0-9A-Fa-f]{2})*)"|([-A-Za-z$._0-9]+))'


class AdapterError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AdapterError(message)


def sha256_text(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def read_c_abi_preserve(path: Path) -> tuple[list[str], str]:
    """Read the exact, sorted root file consumed by LLVM ``internalize``."""

    payload = path.read_bytes()
    require(payload != b"", f"empty C ABI preserve file: {path}")
    require(b"\x00" not in payload, "C ABI preserve file contains a NUL byte")
    require(b"\r" not in payload, "C ABI preserve file must use LF line endings")
    require(payload.endswith(b"\n"), "C ABI preserve file lacks its final LF")
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as error:
        raise AdapterError(f"C ABI preserve file is not ASCII: {error}") from error
    symbols = text[:-1].split("\n")
    require(all(symbols), "C ABI preserve file contains an empty symbol")
    require(symbols == sorted(symbols), "C ABI preserve symbols are not sorted")
    require(len(symbols) == len(set(symbols)), "duplicate C ABI preserve symbol")
    require(
        all(LLVM_PLAIN_SYMBOL.fullmatch(symbol) is not None for symbol in symbols),
        "C ABI preserve file contains an unsupported LLVM symbol spelling",
    )
    return symbols, sha256_bytes(payload)


def decode_llvm_quoted_symbol(encoded: str) -> str:
    """Decode LLVM's quoted ``\\XX`` byte escapes, accepting ASCII only."""

    decoded = bytearray()
    index = 0
    while index < len(encoded):
        character = encoded[index]
        if character == "\\":
            require(
                index + 2 < len(encoded)
                and re.fullmatch(r"[0-9A-Fa-f]{2}", encoded[index + 1:index + 3])
                is not None,
                "unsupported escape in quoted LLVM symbol",
            )
            decoded.append(int(encoded[index + 1:index + 3], 16))
            index += 3
            continue
        require(ord(character) < 128, "non-ASCII quoted LLVM symbol")
        decoded.append(ord(character))
        index += 1
    require(b"\x00" not in decoded, "quoted LLVM symbol contains a NUL byte")
    try:
        return decoded.decode("ascii")
    except UnicodeDecodeError as error:
        raise AdapterError(f"non-ASCII quoted LLVM symbol: {error}") from error


def llvm_symbol_from_match(match: re.Match[str]) -> str:
    quoted, plain = match.groups()[:2]
    symbol = plain if plain is not None else decode_llvm_quoted_symbol(quoted)
    require(symbol != "", "empty LLVM symbol")
    return symbol


def audit_llvm_cbe_global_symbols(
    shared, ir: str, preserve_symbols: list[str]
) -> dict[str, object]:
    """Classify CBE global names before final token-level shortening.

    Declarations and definitions which survived LLVM ``internalize`` with
    external linkage form the protected C ABI set.  Internal/private globals
    may be shortened because the complete generated C bridge is one
    translation unit.  Reject CBE mangling aliases before looking at the C
    output; once aliased, a token-level pass could not recover intent.
    """

    function_pattern = re.compile(r"@" + LLVM_SYMBOL_TOKEN + r"\s*\(")
    global_pattern = re.compile(r"^@" + LLVM_SYMBOL_TOKEN + r"\s*=\s*(.*)$")
    records_by_symbol: dict[str, dict[str, object]] = {}
    for line_number, line in enumerate(ir.splitlines(), 1):
        if line.startswith("define ") or line.startswith("declare "):
            match = function_pattern.search(line)
            require(match is not None, f"cannot parse LLVM function at line {line_number}")
            symbol = llvm_symbol_from_match(match)
            kind = "function-definition" if line.startswith("define ") else "function-declaration"
            internal = kind == "function-definition" and re.search(
                r"\b(?:internal|private)\b", line[:match.start()]
            ) is not None
        elif line.startswith("@"):
            match = global_pattern.match(line)
            if match is None:
                continue
            symbol = llvm_symbol_from_match(match)
            kind = "global-definition"
            internal = re.search(
                r"^(?:dso_local\s+|dso_preemptable\s+)?(?:internal|private)\b",
                match.group(3),
            ) is not None
        else:
            continue
        require(
            symbol not in records_by_symbol,
            f"duplicate LLVM global declaration/definition: {symbol}",
        )
        c_symbol = shared.cbe_mangle(symbol)
        require(
            C_IDENTIFIER.fullmatch(c_symbol) is not None,
            f"LLVM-CBE global name is not a C identifier: {symbol!r} -> {c_symbol!r}",
        )
        records_by_symbol[symbol] = {
            "llvm_symbol": symbol,
            "c_symbol": c_symbol,
            "kind": kind,
            "linkage": "internal" if internal else "protected",
        }

    mangled: dict[str, list[str]] = {}
    for record in records_by_symbol.values():
        mangled.setdefault(str(record["c_symbol"]), []).append(
            str(record["llvm_symbol"])
        )
    collisions = [
        (c_symbol, sorted(llvm_symbols))
        for c_symbol, llvm_symbols in sorted(mangled.items())
        if len(llvm_symbols) != 1
    ]
    require(
        not collisions,
        "LLVM global names collide after LLVM-CBE mangling: "
        + "; ".join(
            f"{c_symbol} <- {', '.join(llvm_symbols)}"
            for c_symbol, llvm_symbols in collisions
        ),
    )

    missing_preserve = sorted(set(preserve_symbols) - set(records_by_symbol))
    require(
        not missing_preserve,
        "C ABI preserve root is absent from optimized LLVM IR: "
        + ", ".join(missing_preserve),
    )
    invalid_preserve = sorted(
        symbol
        for symbol in preserve_symbols
        if records_by_symbol[symbol]["kind"] not in (
            "function-definition", "global-definition"
        )
        or records_by_symbol[symbol]["linkage"] != "protected"
    )
    require(
        not invalid_preserve,
        "C ABI preserve root was not retained as a public definition: "
        + ", ".join(invalid_preserve),
    )
    protected_records = sorted(
        (
            record for record in records_by_symbol.values()
            if record["linkage"] == "protected"
        ),
        key=lambda record: str(record["llvm_symbol"]),
    )
    return {
        "policy": "protect-declarations-and-noninternal-definitions",
        "global_symbol_count": len(records_by_symbol),
        "cbe_mangling_collision_count": 0,
        "protected_llvm_symbols": [
            record["llvm_symbol"] for record in protected_records
        ],
        "protected_c_symbols": sorted(
            str(record["c_symbol"]) for record in protected_records
        ),
    }


def _is_c_identifier_start(character: str) -> bool:
    return character == "_" or "A" <= character <= "Z" or "a" <= character <= "z"


def _is_c_identifier_continue(character: str) -> bool:
    return _is_c_identifier_start(character) or "0" <= character <= "9"


def _consume_pp_number(source: str, start: int) -> int:
    index = start + 1
    while index < len(source):
        character = source[index]
        if character.isascii() and (
            character.isalnum() or character in "_."
        ):
            index += 1
            continue
        if character in "+-" and source[index - 1] in "eEpP":
            index += 1
            continue
        break
    return index


def rewrite_c_identifier_tokens(
    source: str, replacements: dict[str, str]
) -> tuple[str, dict[str, int], dict[str, int]]:
    """Rewrite only ordinary C identifier preprocessing tokens.

    Preprocessor directives are copied byte-for-byte and reported separately.
    Comments and character/string literals are also opaque.  Unsupported
    universal-character names and code-line splicing fail closed, since both
    can change token boundaries before this scanner would see them.
    """

    require("\x00" not in source, "generated C contains a NUL byte")
    require("\r" not in source, "generated C must use LF line endings")
    chunks: list[str] = []
    code_identifiers: dict[str, int] = {}
    preprocessor_identifiers: dict[str, int] = {}
    index = 0
    line_prefix = True
    in_directive = False

    def record(destination: dict[str, int], token: str) -> None:
        destination[token] = destination.get(token, 0) + 1

    while index < len(source):
        if source.startswith("\\\n", index):
            require(
                in_directive or line_prefix,
                "unsupported C line splicing outside a preprocessor directive",
            )
            chunks.append("\\\n")
            index += 2
            continue
        character = source[index]
        if character == "\n":
            chunks.append(character)
            index += 1
            in_directive = False
            line_prefix = True
            continue
        if character in " \t\v\f":
            chunks.append(character)
            index += 1
            continue
        if source.startswith("//", index):
            end = index + 2
            while end < len(source):
                if source.startswith("\\\n", end):
                    end += 2
                    continue
                if source[end] == "\n":
                    break
                end += 1
            chunks.append(source[index:end])
            index = end
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            require(end >= 0, "unterminated block comment in generated C")
            end += 2
            comment = source[index:end]
            chunks.append(comment)
            index = end
            logical_newlines = len(re.findall(r"(?<!\\)\n", comment))
            if logical_newlines:
                in_directive = False
                line_prefix = True
            continue
        if line_prefix and (
            character == "#" or source.startswith("%:", index)
        ):
            spelling = "%:" if source.startswith("%:", index) else "#"
            chunks.append(spelling)
            index += len(spelling)
            in_directive = True
            line_prefix = False
            continue
        if character in "\"'":
            quote = character
            end = index + 1
            while end < len(source):
                if source[end] == quote:
                    end += 1
                    break
                if source[end] == "\\":
                    require(end + 1 < len(source), "unterminated escape in C literal")
                    end += 2
                    continue
                require(source[end] != "\n", "unterminated C literal")
                end += 1
            else:
                raise AdapterError("unterminated C literal")
            chunks.append(source[index:end])
            index = end
            line_prefix = False
            continue
        if _is_c_identifier_start(character):
            end = index + 1
            while end < len(source) and _is_c_identifier_continue(source[end]):
                end += 1
            token = source[index:end]
            destination = (
                preprocessor_identifiers if in_directive else code_identifiers
            )
            emitted = token if in_directive else replacements.get(token, token)
            record(destination, emitted)
            chunks.append(emitted)
            index = end
            line_prefix = False
            continue
        if character.isascii() and (
            character.isdigit()
            or character == "." and index + 1 < len(source)
            and source[index + 1].isdigit()
        ):
            end = _consume_pp_number(source, index)
            chunks.append(source[index:end])
            index = end
            line_prefix = False
            continue
        require(
            not (
                character == "\\" and index + 1 < len(source)
                and source[index + 1] in "uU"
            ),
            "universal-character names in generated C tokens are unsupported",
        )
        require(
            ord(character) < 128,
            "non-ASCII character outside a C comment or literal",
        )
        chunks.append(character)
        index += 1
        line_prefix = False
    return "".join(chunks), code_identifiers, preprocessor_identifiers


def shortened_c_identifier(original: str) -> str:
    require(C_IDENTIFIER.fullmatch(original) is not None, "unsafe C identifier")
    digest = hashlib.sha256(
        C_IDENTIFIER_SHORTENING_DOMAIN.encode("ascii")
        + b"\x00" + original.encode("ascii")
    ).hexdigest()
    result = C_IDENTIFIER_SHORTENING_PREFIX + digest
    require(
        len(result) <= ASXXXX_SAFE_C_IDENTIFIER_MAX,
        "configured C identifier shortening format exceeds the ASxxxx limit",
    )
    return result


def shorten_c_identifiers(
    source: str, protected_c_symbols: list[str]
) -> tuple[str, dict[str, object], dict[str, str]]:
    """Deterministically shorten overlong internal C identifiers."""

    require(
        protected_c_symbols == sorted(protected_c_symbols)
        and len(protected_c_symbols) == len(set(protected_c_symbols)),
        "protected C symbols are not sorted and unique",
    )
    require(
        all(C_IDENTIFIER.fullmatch(symbol) is not None for symbol in protected_c_symbols),
        "protected C symbol set contains an unsafe identifier",
    )
    unchanged, code_counts, directive_counts = rewrite_c_identifier_tokens(source, {})
    require(unchanged == source, "identity C token scan changed generated source")
    overlong_directives = sorted(
        symbol for symbol in directive_counts
        if len(symbol) > ASXXXX_SAFE_C_IDENTIFIER_MAX
    )
    require(
        not overlong_directives,
        "overlong identifier in preprocessor directive cannot be safely renamed: "
        + ", ".join(overlong_directives),
    )
    originals = sorted(
        symbol for symbol in code_counts
        if len(symbol) > ASXXXX_SAFE_C_IDENTIFIER_MAX
    )
    protected_overlong = sorted(set(originals) & set(protected_c_symbols))
    require(
        not protected_overlong,
        "externally visible C identifier exceeds the ASxxxx-safe limit: "
        + ", ".join(protected_overlong),
    )
    replacements = {
        original: shortened_c_identifier(original) for original in originals
    }
    require(
        all(
            C_IDENTIFIER.fullmatch(replacement) is not None
            and len(replacement) <= ASXXXX_SAFE_C_IDENTIFIER_MAX
            for replacement in replacements.values()
        ),
        "C identifier shortening produced an unsafe replacement",
    )
    by_replacement: dict[str, list[str]] = {}
    for original, replacement in replacements.items():
        by_replacement.setdefault(replacement, []).append(original)
    mapping_collisions = [
        (replacement, names)
        for replacement, names in sorted(by_replacement.items())
        if len(names) != 1
    ]
    require(
        not mapping_collisions,
        "C identifier shortening hash collision: "
        + "; ".join(
            f"{replacement} <- {', '.join(names)}"
            for replacement, names in mapping_collisions
        ),
    )
    existing_identifiers = (
        set(code_counts) | set(directive_counts) | set(protected_c_symbols)
    )
    existing_collisions = sorted(
        (original, replacement)
        for original, replacement in replacements.items()
        if replacement in existing_identifiers
    )
    require(
        not existing_collisions,
        "shortened C identifier collides with an existing identifier: "
        + "; ".join(
            f"{original} -> {replacement}"
            for original, replacement in existing_collisions
        ),
    )

    rewritten, final_code_counts, final_directive_counts = (
        rewrite_c_identifier_tokens(source, replacements)
    )
    require(
        final_directive_counts == directive_counts,
        "preprocessor tokens changed during C identifier shortening",
    )
    residual = sorted(
        symbol for symbol in final_code_counts
        if len(symbol) > ASXXXX_SAFE_C_IDENTIFIER_MAX
    )
    require(
        not residual,
        "overlong C identifier survived shortening: " + ", ".join(residual),
    )
    for original, replacement in replacements.items():
        require(
            original not in final_code_counts,
            f"original overlong C identifier survived: {original}",
        )
        require(
            final_code_counts.get(replacement) == code_counts[original],
            f"shortened C identifier occurrence count changed: {original}",
        )
    records = [
        {
            "original_identifier": original,
            "emitted_identifier": replacements[original],
            "original_length": len(original),
            "emitted_length": len(replacements[original]),
            "token_occurrences": code_counts[original],
        }
        for original in originals
    ]
    report: dict[str, object] = {
        "policy": C_IDENTIFIER_SHORTENING_POLICY,
        "max_c_identifier_length": ASXXXX_SAFE_C_IDENTIFIER_MAX,
        "asxxxx_global_prefix_length": 1,
        "asxxxx_symbol_identity_max": 255,
        "replacement_prefix": C_IDENTIFIER_SHORTENING_PREFIX,
        "hash_algorithm": C_IDENTIFIER_SHORTENING_HASH,
        "hash_domain": C_IDENTIFIER_SHORTENING_DOMAIN,
        "input_c_sha256": sha256_text(source),
        "output_c_sha256": sha256_text(rewritten),
        "code_identifier_count": len(code_counts),
        "preprocessor_identifier_count": len(directive_counts),
        "protected_identifier_count": len(protected_c_symbols),
        "rewritten_identifier_count": len(records),
        "rewritten_token_count": sum(
            int(record["token_occurrences"]) for record in records
        ),
        "preprocessor_tokens_rewritten": 0,
        "records": records,
    }
    return rewritten, report, replacements


def load_canary_adapter():
    path = Path(__file__).with_name("audit_and_adapt.py")
    require(path.is_file(), f"shared fail-closed adapter is missing: {path}")
    spec = importlib.util.spec_from_file_location("stcxx_canary_adapter", path)
    require(spec is not None and spec.loader is not None, "cannot load shared adapter")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_constructors(
    ir: str, program_address_space: int
) -> list[dict[str, object]]:
    match = re.search(
        r"^@llvm\.global_ctors\s*=\s*appending\s+global\s+.*$",
        ir,
        re.MULTILINE,
    )
    if match is None:
        return []
    line = match.group(0)
    pointer = (
        r"ptr" if program_address_space == 0
        else rf"ptr\s+addrspace\({program_address_space}\)"
    )
    entry_pattern = re.compile(
        rf"\{{\s*i32,\s*{pointer},\s*{pointer}\s*\}}\s*"
        rf"\{{\s*i32\s+([0-9]+),\s*{pointer}\s+"
        r"@(?:\"([^\"]+)\"|([A-Za-z0-9_.$-]+)),\s*"
        rf"{pointer}\s+null\s*\}}"
    )
    constructors = [
        {"priority": int(priority), "llvm_symbol": quoted or plain}
        for priority, quoted, plain in entry_pattern.findall(line)
    ]
    declared = re.search(r"appending\s+global\s+\[([0-9]+)\s+x\s+\{", line)
    require(declared is not None, "cannot parse llvm.global_ctors size")
    require(
        len(constructors) == int(declared.group(1)),
        "llvm.global_ctors contains an unsupported entry shape",
    )
    priorities = [int(entry["priority"]) for entry in constructors]
    require(priorities == sorted(priorities), "constructor priorities are not monotonic")
    return constructors




def audit_mcs51_program_address_space(ir: str) -> dict[str, object]:
    """Accept only Clang's exact MCS51 program/function address space.

    Address space 1 is a 16-bit code/function pointer.  Default address space
    zero remains SDCC's 24-bit tagged generic data pointer.  Accepted
    conversions are the constant expression used for a generic-pointer vtable
    slot and the exact reverse conversion made by the audited Itanium virtual
    member-call path.
    """

    spaces = sorted({int(value) for value in re.findall(
        r"\baddrspace\s*\(\s*([0-9]+)\s*\)", ir
    )})
    require(
        1 in spaces and all(value in (0, 1) for value in spaces),
        f"unsupported LLVM address spaces: {spaces!r}",
    )

    functions = []
    missing_program_space = []
    for line in ir.splitlines():
        if not line.startswith("define "):
            continue
        match = re.search(r'@(?:"([^"]+)"|([A-Za-z0-9_.$-]+))\(', line)
        require(match is not None, "cannot parse LLVM function definition")
        name = match.group(1) or match.group(2)
        functions.append(name)
        if re.search(r"\)\s+[^\{]*\baddrspace\(1\)", line) is None:
            missing_program_space.append(name)
    require(
        not missing_program_space,
        "MCS51 function definition escaped program address space 1: "
        + ", ".join(missing_program_space),
    )

    cast_pattern = re.compile(
        r'ptr\s+addrspacecast\s*\(\s*ptr\s+addrspace\(1\)\s+'
        r'@(?:"([^"]+)"|([A-Za-z0-9_.$-]+))\s+to\s+ptr\s*\)'
    )
    casts = [quoted or plain for quoted, plain in cast_pattern.findall(ir)]
    member_call_cast_pattern = re.compile(
        r"^\s*(%[-A-Za-z$._0-9]+)\s*=\s*addrspacecast\s+"
        r"ptr\s+(%[-A-Za-z$._0-9]+)\s+to\s+ptr\s+addrspace\(1\)\s*$",
        re.MULTILINE,
    )
    member_call_casts = member_call_cast_pattern.findall(ir)
    cast_count = len(re.findall(r"\baddrspacecast\b", ir))
    require(
        len(casts) + len(member_call_casts) == cast_count,
        "MCS51 permits only local program-function vtable constants and "
        "audited virtual-member-call address-space casts",
    )
    missing_cast_targets = sorted(set(casts) - set(functions))
    require(
        not missing_cast_targets,
        "MCS51 address-space cast references a non-local program function: "
        + ", ".join(missing_cast_targets),
    )
    return {
        "program_address_space": 1,
        "observed_explicit_address_spaces": spaces,
        "program_pointer_bits": 16,
        "generic_pointer_bits": 24,
        "defined_program_functions": len(functions),
        "vtable_program_to_generic_casts": len(casts),
        "vtable_cast_targets": sorted(casts),
        "virtual_member_generic_to_program_casts": len(member_call_casts),
        "virtual_member_program_values": sorted(
            result for result, _source in member_call_casts
        ),
    }


def audit_vtable_offset_constants(ir: str) -> list[dict[str, object]]:
    """Recognize pointer-sized signed offsets in local Itanium vtables.

    These are data, not reconstructed callable addresses. MCS251 uses
    24-bit pointer slots for both program and data pointers.
    Keep arbitrary integer-to-pointer casts outside this exception.
    """
    records = []
    for line in ir.splitlines():
        match = re.fullmatch(
            r'@(?P<symbol>_ZT[VC][A-Za-z0-9_.$]+)\s*=\s*'
            r'(?:internal|private)\s+(?:unnamed_addr\s+)?constant\s+'
            r'\{(?:\s*\[[0-9]+ x ptr\]\s*,?)+\}\s+\{.*\}, align 1', line)
        if match is None:
            continue
        for cast in re.finditer(r'\bptr inttoptr \(i24 (-?[0-9]+) to ptr\)', line):
            value = int(cast.group(1))
            require(-(1 << 23) <= value < (1 << 23),
                    'vtable offset is outside signed generic-pointer width')
            records.append({'symbol': match.group('symbol'), 'value': value,
                            'integer_bits': 24})
    return records


def audit_optimized_member_call(body: str, result: str, source: str,
                                bits: int, target: str, header: str = "") -> bool:
    """Recognize linked SSA operands of the optimized Itanium call forms.

    O2 splits (vptr + (member - 1)) into two byte GEPs, or removes the
    virtual branch after assuming the low bit is zero. In both forms the
    adjustment must come from the same member-pointer pair and the resulting
    pointer must be the actual call target, with the adjusted object as this.
    """
    ssa = r'%[-A-Za-z$._0-9]+'
    integer = 'i'+str(bits)
    pair = rf'\{{ {integer}, {integer} \}}'
    def definition(value, instruction):
        return re.search(rf'^\s*{re.escape(value)} = {instruction}(?:, ![^\n]*)?$', body, re.M)
    member = definition(source, rf'extractvalue {pair} (?P<pair>{ssa}), 0')
    if member is not None:
        adjustment = re.search(rf'^\s*(?P<value>{ssa}) = extractvalue {pair} {re.escape(member["pair"])}, 1$', body, re.M)
    elif target == 'mcs51' and bits == 16:
        # Oz scalarizes the MCS51 byval pair into two i16 loads. Bind both
        # fields to that exact parameter; unrelated integer loads do not
        # establish a member-pointer provenance.
        member = definition(source, rf'load i16, ptr (?P<pair>{ssa}), align 1')
        if member is None or re.search(
            rf'\bptr (?:(?:nocapture|noundef|readonly) )*byval\({pair}\) '
            rf'align 1 {re.escape(member["pair"])}(?=,|\))', header
        ) is None:
            return False
        field = re.search(
            rf'^\s*(?P<value>{ssa}) = getelementptr inbounds(?: nuw)? '
            rf'i8, ptr {re.escape(member["pair"])}, i24 2$', body, re.M
        )
        if field is None:
            return False
        adjustment = re.search(
            rf'^\s*(?P<value>{ssa}) = load i16, ptr {re.escape(field["value"])}, '
            rf'align 1(?:, ![^\n]*)?$', body, re.M
        )
    else:
        return False
    if adjustment is None:
        return False
    offset = adjustment['value']
    if bits == 16:
        extended = re.search(rf'^\s*(?P<value>{ssa}) = sext i16 {re.escape(offset)} to i24$', body, re.M)
        if extended is None:
            return False
        offset = extended['value']
    adjusted = re.search(rf'^\s*(?P<value>{ssa}) = getelementptr inbounds(?: nuw)? i8, ptr {ssa}, i24 {re.escape(offset)}$', body, re.M)
    if adjusted is None:
        return False
    object_pointer = adjusted['value']
    lowbit = re.search(rf'^\s*(?P<value>{ssa}) = and {integer} {re.escape(source)}, 1$', body, re.M)
    if lowbit is None:
        return False
    zero = re.search(rf'^\s*(?P<value>{ssa}) = icmp eq {integer} {re.escape(lowbit["value"])}, 0$', body, re.M)
    if zero is None:
        return False
    pointer_type = r'ptr addrspace\(1\)' if target == 'mcs51' else 'ptr'
    phi = re.search(rf'^\s*(?P<value>{ssa}) = phi {pointer_type} (?P<incoming>[^\n]*\[ {re.escape(result)},[^\n]*)$', body, re.M)
    def calls(value):
        return re.search(rf'\bcall\b[^\n]* {re.escape(value)}\(ptr [^,%\n]*{re.escape(object_pointer)}(?:,|\))',body) is not None
    if re.search(rf'\bcall(?: addrspace\(1\))? void @llvm\.assume\(i1 {re.escape(zero["value"])}\)',body):
        return calls(result)
    if phi is None or not calls(phi['value']):
        return False
    incoming = re.findall(rf'\[ ({ssa}), ({ssa}) \]',phi['incoming'])
    others = [v for v,_ in incoming if v != result]
    if len(incoming)!=2 or len(others)!=1:
        return False
    if target == 'mcs51':
        incoming_blocks = dict(incoming)
        if re.search(
            rf'^\s*br i1 {re.escape(zero["value"])}, '
            rf'label {re.escape(incoming_blocks[result])}, '
            rf'label {re.escape(incoming_blocks[others[0]])}$', body, re.M
        ) is None:
            return False
        # The zero low bit must select the direct-call block. Bind the PHI
        # predecessors to the blocks which actually define each alternative.
        current_block = None
        definitions_by_block = {}
        for line in body.splitlines():
            label = re.match(r'^([-A-Za-z$._0-9]+):', line)
            if label is not None:
                current_block = '%' + label[1]
            value = re.match(rf'^\s*({ssa}) = ', line)
            if value is not None:
                definitions_by_block[value[1]] = current_block
        if any(definitions_by_block.get(value) != label for value, label in incoming):
            return False
    virtual = others[0]
    if target == 'mcs51':
        cast = definition(virtual, rf'addrspacecast ptr (?P<generic>{ssa}) to ptr addrspace\(1\)')
        if cast is None:
            return False
        virtual = cast['generic']
    load=definition(virtual,rf'load ptr, ptr (?P<slot>{ssa}), align 1')
    if load is None:
        return False
    last=definition(load['slot'],rf'getelementptr i8, ptr (?P<base>{ssa}), i24 -1')
    if last is None:
        return False
    member_index=source
    if bits==16:
        widened=re.search(rf'^\s*(?P<value>{ssa}) = sext i16 {re.escape(source)} to i24$',body,re.M)
        if widened is None:
            return False
        member_index=widened['value']
    first=definition(last['base'],rf'getelementptr i8, ptr (?P<vptr>{ssa}), i24 {re.escape(member_index)}')
    if first is None or definition(first['vptr'],rf'load ptr, ptr {re.escape(object_pointer)}, align 1') is None:
        return False
    return re.search(rf'br i1 {re.escape(zero["value"])}, label {ssa}, label {ssa}',body) is not None


def audit_stc_pointer_integer_conversions(
    ir: str, target_profile: str
) -> dict[str, object]:
    """Accept exact member-pointer and pointer-difference conversion shapes.

    The locked member-pointer representation uses one program-pointer-width
    integer for a data member and a pair for a method.  Clang materializes a
    non-virtual method as ``ptrtoint`` and reconstructs it on invocation with
    ``inttoptr``.  MCS51 uses AS1/i16; MCS251 uses default-AS/i24.

    C++ pointer subtraction is different: ``ptrdiff_t`` is a signed i32 on
    both locked profiles.  Accept only the compiler's exact SSA shape: two
    named default-address-space ``ptrtoint`` instructions whose sole uses are
    the two distinct operands of one unflagged ``sub i32`` in the same
    function.  The subtraction result can then flow normally (return, store,
    comparison, call argument, or exact scaling).  This deliberately rejects
    the former i24 lowering, which truncated/underwrote ``ptrdiff_t`` values.
    """

    ir, widened_pointer_pairs = load_canary_adapter().canonicalize_widened_pointer_differences(ir)
    member_bits = 16 if target_profile == "mcs51" else 24
    ptrtoint_count = len(re.findall(r"\bptrtoint\b", ir))
    ptrtoint_lines = [
        line.strip() for line in ir.splitlines() if "ptrtoint" in line
    ]
    require(
        sum(line.count("ptrtoint") for line in ptrtoint_lines)
        == ptrtoint_count,
        "multiple pointer-to-integer conversions on one IR line are "
        "unsupported",
    )

    defined_functions = {
        quoted or plain
        for quoted, plain in re.findall(
            r'^define\s+[^\n]*@(?:"([^"]+)"|([A-Za-z0-9_.$-]+))\(',
            ir,
            re.MULTILINE,
        )
    }

    member_pointer_type = (
        r"ptr\s+addrspace\(1\)" if target_profile == "mcs51" else r"ptr"
    )
    member_conversion_pattern = re.compile(
        rf'\bptrtoint\s*\(\s*{member_pointer_type}\s+'
        rf'@(?:"([^"]+)"|([A-Za-z0-9_.$-]+))\s+'
        rf"to\s+i{member_bits}\s*\)"
    )
    member_conversions = member_conversion_pattern.findall(ir)
    member_function_symbols = sorted({
        quoted or plain for quoted, plain in member_conversions
    })
    missing_program_symbols = sorted(
        set(member_function_symbols) - defined_functions
    )
    require(
        not missing_program_symbols,
        "member-pointer encoding references a non-local program function: "
        + ", ".join(missing_program_symbols),
    )

    function_pattern = re.compile(
        r"^define\s+[^\n]*@(?:\"([^\"]+)\"|([A-Za-z0-9_.$-]+))"
        r"\([^\n]*\)[^{]*\{\n(.*?)^\}",
        re.MULTILINE | re.DOTALL,
    )
    functions = [
        (quoted or plain, body)
        for quoted, plain, body in function_pattern.findall(ir)
    ]
    function_headers = {
        match.group(1) or match.group(2): match.group(0).splitlines()[0]
        for match in function_pattern.finditer(ir)
    }
    ssa_name = r"%[-A-Za-z$._0-9]+"
    ptrdiff_cast_pattern = re.compile(
        rf"^\s*(?P<result>{ssa_name})\s*=\s*ptrtoint\s+ptr\s+"
        rf"(?P<source>{ssa_name})\s+to\s+i32\s*$",
        re.MULTILINE,
    )
    ptrdiff_sub_pattern = re.compile(
        rf"^\s*(?P<result>{ssa_name})\s*=\s*sub\s+i32\s+"
        rf"(?P<left>{ssa_name})\s*,\s*(?P<right>{ssa_name})\s*$",
        re.MULTILINE,
    )

    pointer_difference_pairs: list[dict[str, str]] = []
    ptrdiff_cast_count = 0
    for function_name, body in functions:
        casts = list(ptrdiff_cast_pattern.finditer(body))
        if not casts:
            continue
        cast_sources: dict[str, str] = {}
        for match in casts:
            result = match.group("result")
            require(
                result not in cast_sources,
                f"duplicate pointer-difference SSA definition in {function_name}: "
                + result,
            )
            cast_sources[result] = match.group("source")
        ptrdiff_cast_count += len(casts)

        for result in cast_sources:
            token_pattern = re.compile(
                rf"(?<![-A-Za-z$._0-9]){re.escape(result)}"
                r"(?![-A-Za-z$._0-9])"
            )
            # One occurrence is the instruction definition.  Exactly one
            # other occurrence must be its sole SSA use.
            require(
                len(token_pattern.findall(body)) == 2,
                "pointer-difference cast must have exactly one SSA use in "
                f"{function_name}: {result}",
            )

        consuming_subs = []
        for match in ptrdiff_sub_pattern.finditer(body):
            left = match.group("left")
            right = match.group("right")
            if left not in cast_sources and right not in cast_sources:
                continue
            require(
                left != right
                and left in cast_sources
                and right in cast_sources,
                "pointer-difference subtraction must consume two distinct "
                f"audited i32 casts in {function_name}: {match.group(0).strip()}",
            )
            consuming_subs.append(match)

        consumed_casts = [
            operand
            for match in consuming_subs
            for operand in (match.group("left"), match.group("right"))
        ]
        require(
            len(consumed_casts) == len(cast_sources)
            and len(set(consumed_casts)) == len(cast_sources)
            and set(consumed_casts) == set(cast_sources),
            "i32 pointer-difference casts do not form unique subtraction "
            f"pairs in {function_name}",
        )
        for match in consuming_subs:
            left = match.group("left")
            right = match.group("right")
            pointer_difference_pairs.append({
                "function": function_name,
                "result": match.group("result"),
                "left_cast": left,
                "left_pointer": cast_sources[left],
                "right_cast": right,
                "right_pointer": cast_sources[right],
            })

    member_conversion_count = len(member_conversions)
    require(
        ptrtoint_count == member_conversion_count + ptrdiff_cast_count,
        "pointer-to-integer conversion escaped the closed member-pointer/"
        "i32-pointer-difference audit",
    )
    require(
        ptrdiff_cast_count == 2 * len(pointer_difference_pairs),
        "i32 pointer-difference cast/pair count is not closed",
    )

    inttoptr_count = len(re.findall(r"\binttoptr\b", ir))
    vtable_offsets = audit_vtable_offset_constants(ir)
    audited_inttoptr = 0
    int_type = f"i{member_bits}"
    pair_type = rf"\{{\s*{int_type},\s*{int_type}\s*\}}"
    callee_pointer_type = (
        r"ptr\s+addrspace\(1\)" if target_profile == "mcs51" else r"ptr"
    )
    program_addrspace_casts = 0
    for function_name, body in functions:
        for match in re.finditer(
            rf"^\s*(%[-A-Za-z$._0-9]+)\s*=\s*inttoptr\s+"
            rf"{int_type}\s+(%[-A-Za-z$._0-9]+)\s+to\s+"
            rf"{callee_pointer_type}\s*$",
            body,
            re.MULTILINE,
        ):
            result, source = match.groups()
            phi_match = re.search(
                rf"^\s*(%[-A-Za-z$._0-9]+)\s*=\s*phi\s+"
                rf"{callee_pointer_type}\s+(?P<incoming>[^\n]*"
                rf"\[\s*{re.escape(result)},[^\n]*)$",
                body,
                re.MULTILINE,
            )
            classic = (
                re.search(
                    rf"^\s*{re.escape(source)}\s*=\s*extractvalue\s+"
                    rf"{pair_type}\s+[^,]+,\s*0\s*$",
                    body,
                    re.MULTILINE,
                )
                is not None
                and re.search(
                    rf"\band\s+{int_type}\s+{re.escape(source)},\s*1\b",
                    body,
                )
                is not None
                and re.search(
                    rf"\bsub\s+{int_type}\s+{re.escape(source)},\s*1\b",
                    body,
                )
                is not None
                and phi_match is not None
            )
            require(
                classic or audit_optimized_member_call(
                    body, result, source, member_bits, target_profile,
                    function_headers[function_name],
                ),
                "inttoptr is not the compiler's audited Itanium member-call "
                f"shape in {function_name}",
            )
            if target_profile == "mcs51":
                assert phi_match is not None
                phi_result = phi_match.group(1)
                incoming_values = re.findall(
                    r"\[\s*(%[-A-Za-z$._0-9]+)\s*,",
                    phi_match.group("incoming"),
                )
                virtual_values = [
                    value for value in incoming_values if value != result
                ]
                require(
                    len(virtual_values) == 1
                    and re.search(
                        rf"^\s*{re.escape(virtual_values[0])}\s*=\s*"
                        rf"addrspacecast\s+ptr\s+%[-A-Za-z$._0-9]+\s+to\s+"
                        rf"ptr\s+addrspace\(1\)\s*$",
                        body,
                        re.MULTILINE,
                    )
                    is not None
                    and re.search(
                        rf"\bcall\b[^\n]*addrspace\(1\)[^\n]*"
                        rf"{re.escape(phi_result)}\(",
                        body,
                    )
                    is not None,
                    "MCS51 member call did not remain in program address "
                    f"space 1 in {function_name}",
                )
                program_addrspace_casts += 1
            audited_inttoptr += 1
    require(
        audited_inttoptr + len(vtable_offsets) == inttoptr_count,
        "integer-to-pointer conversion escaped the Itanium member-call audit",
    )
    return {
        "pointer_to_integer_count": ptrtoint_count,
        "member_pointer_to_integer_count": member_conversion_count,
        "integer_to_pointer_count": inttoptr_count,
        "member_integer_to_pointer_count": audited_inttoptr,
        "vtable_offset_constants": vtable_offsets,
        "member_pointer_integer_bits": member_bits,
        "member_function_symbols": member_function_symbols,
        "program_address_space_member_calls": program_addrspace_casts,
        "pointer_difference": {
            "optimized_widened_pairs": widened_pointer_pairs,
            "integer_bits": 32,
            "cast_count": ptrdiff_cast_count,
            "pair_count": len(pointer_difference_pairs),
            "functions": sorted({
                pair["function"] for pair in pointer_difference_pairs
            }),
            "pairs": sorted(
                pointer_difference_pairs,
                key=lambda pair: (pair["function"], pair["result"]),
            ),
        },
        "policy": (
            "exact-itanium-program-width-member-pointer-and-i32-ptrdiff"
        ),
    }


def audit_ir(
    shared,
    ir: str,
    triple: str,
    layout: str,
    abi_symbol: str,
    target_profile: str,
    c_abi_preserve_symbols: list[str],
    c_abi_preserve_sha256: str,
):
    observed_triple, observed_layout = shared.read_target(ir)
    require(observed_triple == triple, f"unexpected target triple: {observed_triple}")
    require(observed_layout == layout, f"unexpected data layout: {observed_layout}")
    native_aggregate_abi = shared.audit_native_aggregate_abi(ir, c_abi_preserve_symbols)
    program_address_space = 1 if target_profile == "mcs51" else 0
    value_audit_ir, ignored_pointer_arguments = shared.audit_ignored_pointer_arguments(ir)
    forbidden = [
        name for name, pattern in shared.FORBIDDEN_IR_PATTERNS.items()
        if not (target_profile == "mcs51" and name == "nonzero_address_space")
        and pattern.search(value_audit_ir)
    ]
    require(not forbidden, "forbidden LLVM IR category: " + ", ".join(forbidden))
    opcodes = shared.collect_opcodes(shared.mask_llvm_data(ir))
    intrinsics = shared.collect_intrinsics(shared.mask_llvm_data(ir))
    pointer_integer_conversions = audit_stc_pointer_integer_conversions(
        shared.mask_llvm_data(ir), target_profile
    )
    required_preserve_symbols = {
        "setup", "loop", "__stcxx_run_global_ctors", abi_symbol,
        "stcxx_runtime_panic",
    }
    missing_preserve_symbols = sorted(
        required_preserve_symbols - set(c_abi_preserve_symbols)
    )
    require(
        not missing_preserve_symbols,
        "C ABI preserve file omits required Arduino bridge root(s): "
        + ", ".join(missing_preserve_symbols),
    )
    c_identifier_linkage = audit_llvm_cbe_global_symbols(
        shared, ir, c_abi_preserve_symbols
    )
    aligned_function_symbols: list[str] = []
    unsupported_function_alignments: list[str] = []
    for line in ir.splitlines():
        if not line.startswith("define "):
            continue
        symbol_match = re.search(
            r'@(?:"([^"]+)"|([A-Za-z0-9_.$-]+))\(', line
        )
        require(symbol_match is not None, "cannot parse LLVM function definition")
        alignment_match = re.search(r"\balign\s+([0-9]+)\s*\{$", line)
        if alignment_match is None:
            continue
        symbol = symbol_match.group(1) or symbol_match.group(2)
        alignment = int(alignment_match.group(1))
        if alignment != 2:
            unsupported_function_alignments.append(
                f"{symbol}={alignment}"
            )
        aligned_function_symbols.append(symbol)
    require(
        not unsupported_function_alignments,
        "unsupported LLVM function alignment(s): "
        + ", ".join(unsupported_function_alignments),
    )
    aligned_function_symbols = sorted(set(aligned_function_symbols))
    aligned_c_symbols = sorted(
        shared.cbe_mangle(symbol) for symbol in aligned_function_symbols
    )
    require(
        len(aligned_c_symbols) == len(set(aligned_c_symbols)),
        "LLVM function names collide after LLVM-CBE mangling",
    )
    unaligned_member_functions = sorted(
        set(pointer_integer_conversions["member_function_symbols"])
        - set(aligned_function_symbols)
    )
    require(
        not unaligned_member_functions,
        "member-function pointer target lacks LLVM align 2: "
        + ", ".join(unaligned_member_functions),
    )
    constructors = parse_constructors(ir, program_address_space)
    guard_objects: list[str] = []
    for line in ir.splitlines():
        guard_match = re.match(
            r'^@(?:"(_ZGV[^"]+)"|(_ZGV[-A-Za-z$._0-9]+))\s*=\s*(.*)$',
            line,
        )
        if guard_match is None:
            continue
        guard_name = guard_match.group(1) or guard_match.group(2)
        definition = guard_match.group(3)
        require(
            re.fullmatch(r"internal\s+(?:unnamed_addr\s+)?global\s+(?:i8\s+(?:0|zeroinitializer)|i1\s+(?:false|zeroinitializer))(?:,\s*align\s+1)?", definition)
            is not None,
            f"unsupported local-static guard representation: {line}",
        )
        guard_objects.append(guard_name)
    guard_hook_symbols = sorted(set(re.findall(
        r"@(__cxa_guard_(?:acquire|release|abort))\b", ir
    )))
    require(
        not guard_hook_symbols,
        "direct local-static guard profile references runtime hooks: "
        + ", ".join(guard_hook_symbols),
    )
    address_space_audit = (
        audit_mcs51_program_address_space(ir)
        if target_profile == "mcs51"
        else {
            "program_address_space": 0,
            "program_pointer_bits": 24,
            "generic_pointer_bits": 24,
            "vtable_program_to_generic_casts": 0,
            "vtable_cast_targets": [],
        }
    )
    required = {
        "setup": r"define\s+[^\n]*@setup\(",
        "loop": r"define\s+[^\n]*@loop\(",
        "runtime_ctor_entry": r"define\s+[^\n]*@__stcxx_run_global_ctors\(",
        "abi_identity": rf"define\s+[^\n]*@{re.escape(abi_symbol)}\(",
    }
    missing = [name for name, pattern in required.items() if not re.search(pattern, ir)]
    require(not missing, "missing Arduino C++ runtime symbol(s): " + ", ".join(missing))

    trap_callers: list[str] = []
    function_pattern = re.compile(
        r"^define\s+[^\n]*@(?:\"([^\"]+)\"|([A-Za-z0-9_.$-]+))"
        r"\([^\n]*\)[^{]*\{\n(.*?)^\}",
        re.MULTILINE | re.DOTALL,
    )
    for quoted, plain, body in function_pattern.findall(ir):
        body_trap_count = len(
            re.findall(r"\bcall\s+void\s+@llvm\.trap\(\)", body)
        )
        if not body_trap_count:
            continue
        caller = quoted or plain
        # Clang may emit a trap-only complete/deleting destructor for an
        # abstract base whose pure virtual destructor is unreachable.  Match
        # that Itanium ABI shape and its control flow, not a canary class name.
        require(
            re.fullmatch(r"_Z.+D[012]Ev", caller) is not None,
            f"llvm.trap appears outside an audited destructor: {caller}",
        )
        require(
            body_trap_count == 1
            and len(re.findall(r"\bcall\s+", body)) == 1
            and len(re.findall(r"\bunreachable\b", body)) == 1
            and re.search(
                r"\bcall\s+void\s+@llvm\.trap\(\)[^\n]*\n\s*unreachable\s*$",
                body,
            ) is not None,
            f"unsupported trap destructor body: {caller}",
        )
        trap_callers.append(caller)
    trap_call_count = len(
        re.findall(r"\bcall\s+void\s+@llvm\.trap\(\)", ir)
    )
    return {
        "target_triple": observed_triple,
        "native_aggregate_abi": native_aggregate_abi,
        "ignored_pointer_arguments": ignored_pointer_arguments,
        "data_layout": observed_layout,
        "constructors": constructors,
        "local_static_guard_audit": {
            "policy": "one-byte-direct-non-threadsafe",
            "required_compiler_flag": "-fno-threadsafe-statics",
            "object_count": len(guard_objects),
            "objects": sorted(guard_objects),
            "object_llvm_type": "i8-or-optimized-i1",
            "runtime_guard_hook_symbols": guard_hook_symbols,
        },
        "observed_opcodes": opcodes,
        "observed_intrinsics": intrinsics,
        "pointer_integer_conversions": pointer_integer_conversions,
        "c_abi_preservation": {
            "policy": "exact-internalize-public-api-list-file",
            "sha256": c_abi_preserve_sha256,
            "symbol_count": len(c_abi_preserve_symbols),
            "symbols": c_abi_preserve_symbols,
        },
        "c_identifier_linkage": c_identifier_linkage,
        "function_alignment": {
            "alignment_bytes": 2,
            "llvm_symbols": aligned_function_symbols,
            "original_c_symbols": aligned_c_symbols,
            "c_symbols": aligned_c_symbols,
        },
        "program_address_space_audit": address_space_audit,
        "audited_trap_callers": trap_callers,
        "audited_trap_call_count": trap_call_count,
        "audited_trap_policy": "trap-only-itanium-destructor",
        "forbidden_categories": [],
    }


def remove_duplicate_const_declarations(shared, payload: str):
    """Use the canary rewrite when needed, while accepting an empty rewrite set."""
    marker_a = "\n/* Global Variable Declarations */\n"
    marker_b = "\n/* Function Declarations */\n"
    marker_c = "\n/* Global Variable Definitions and Initialization */\n"
    require(payload.count(marker_a) == 1, "global declaration marker mismatch")
    require(payload.count(marker_b) == 1, "function declaration marker mismatch")
    require(payload.count(marker_c) == 1, "global definition marker mismatch")
    declaration_block = payload.split(marker_a, 1)[1].split(marker_b, 1)[0]
    if re.search(r"^const\s+static\s+.+\s+[A-Za-z_][A-Za-z0-9_]*\s*;\s*$",
                 declaration_block, re.MULTILINE):
        return shared.remove_sdcc_duplicate_const_declarations(payload)
    return payload, [], []


def normalize_cbe_select_helpers(payload: str) -> tuple[str, list[str]]:
    """Initialize LLVM-CBE select temporaries in their declaration.

    CBE emits a declaration followed immediately by an unconditional
    assignment. Patched SDCC still reports its generic warning 84 for some
    inlined callers, so retain the exact semantics while making the source's
    definite initialization mechanically auditable.
    """
    # LLVM-CBE spells non-native integer widths with the C23 ``_BitInt``
    # syntax (for example ``unsigned _BitInt(24)``).  Keep the accepted type
    # grammar deliberately narrow: a typedef/simple pointer, or one exact
    # signed/unsigned _BitInt spelling.  The repeated arguments must still use
    # the byte-for-byte same type through the named backreference below.
    select_type = (
        r"(?:[A-Za-z_][A-Za-z0-9_]*(?:\s*\*)?"
        r"|(?:signed|unsigned)\s+_BitInt\([1-9][0-9]*\)"
        r"|struct\s+[A-Za-z_][A-Za-z0-9_]*)"
    )
    pattern = re.compile(
        rf"^static __forceinline (?P<type>{select_type}) "
        r"(?P<name>llvm_select_[A-Za-z0-9_]+)"
        r"\(bool condition, (?P=type) iftrue, (?P=type) ifnot\) \{\n"
        r"  (?P=type) r;\n"
        r"  r = condition \? iftrue : ifnot;\n"
        r"  return r;\n"
        r"\}$",
        re.MULTILINE,
    )
    names: list[str] = []

    def rewrite(match: re.Match[str]) -> str:
        names.append(match.group("name"))
        return (
            f"static __forceinline {match.group('type')} {match.group('name')}"
            f"(bool condition, {match.group('type')} iftrue, "
            f"{match.group('type')} ifnot) {{\n"
            f"  {match.group('type')} r = condition ? iftrue : ifnot;\n"
            "  return r;\n"
            "}"
        )

    normalized = pattern.sub(rewrite, payload)
    # Fail closed on the complete helper, rather than looking only for an
    # immediately adjacent declaration.  A comment or another unfamiliar
    # statement between the header and ``r;`` must not make an unsupported
    # helper disappear from the audit.
    helper_headers = re.findall(
        r"^static __forceinline [^\n]+ "
        r"(?P<name>llvm_select_[A-Za-z0-9_]+)\([^\n]*\) \{$",
        normalized,
        re.MULTILINE,
    )
    normalized_pattern = re.compile(
        rf"^static __forceinline (?P<type>{select_type}) "
        r"(?P<name>llvm_select_[A-Za-z0-9_]+)"
        r"\(bool condition, (?P=type) iftrue, (?P=type) ifnot\) \{\n"
        r"  (?P=type) r = condition \? iftrue : ifnot;\n"
        r"  return r;\n"
        r"\}$",
        re.MULTILINE,
    )
    normalized_names = [
        match.group("name") for match in normalized_pattern.finditer(normalized)
    ]
    require(
        len(helper_headers) == len(normalized_names)
        and sorted(helper_headers) == sorted(normalized_names)
        and len(helper_headers) == len(set(helper_headers)),
        "unsupported LLVM-CBE select helper",
    )
    return normalized, names


def normalize_cbe_bitint_u24_negation(payload: str) -> tuple[str, int]:
    """Repair the pinned CBE's malformed ``_BitInt(24)`` negation helper.

    The Arduino CLI target keeps i24 as C23 ``_BitInt`` while the standalone
    bridge target can spell the same helper with 32-bit typedefs.  Convert only
    the exact malformed BitInt body to the already-qualified 32-bit modulo
    implementation.  The unsigned subtraction avoids signed-overflow UB and
    the mask preserves LLVM i24 wraparound semantics.
    """
    malformed = (
        "static __forceinline unsigned _BitInt(24) llvm_neg_u24("
        "signed _BitInt(24) a) {\n"
        "  unsigned _BitInt(24) r = (-a;\n"
        "  return r;\n"
        "}"
    )
    replacement = (
        "static __forceinline uint32_t llvm_neg_u24(int32_t a) {\n"
        "  uint32_t r = (0UL - ((uint32_t)a & 16777215UL)) "
        "& 16777215UL;\n"
        "  return r;\n"
        "}"
    )
    count = payload.count(malformed)
    require(count <= 1, "duplicate malformed llvm-cbe BitInt i24 negation helpers")
    rewritten = payload.replace(malformed, replacement)
    residual = re.compile(
        r"^static __forceinline [^\n]+\bllvm_neg_u24\([^\n]*\) \{\n"
        r"(?:[^\n]*\n){0,4}?\s+[^\n]*\br = \(-a;\s*$",
        re.MULTILINE,
    )
    require(
        residual.search(rewritten) is None,
        "unhandled malformed llvm-cbe BitInt i24 negation helper",
    )
    bitint_residual = re.compile(
        r"^static __forceinline (?=[^\n]*\bllvm_neg_u24\()"
        r"[^\n]*_BitInt\(24\)[^\n]* \{$",
        re.MULTILINE,
    )
    require(
        bitint_residual.search(rewritten) is None,
        "unsupported llvm-cbe BitInt i24 negation helper",
    )
    return rewritten, count


def preserve_cbe_const_byte_array_addresses(
    payload: str,
) -> tuple[str, list[dict[str, object]]]:
    """Retain constness when CBE forms an address inside a const byte array.

    Opaque LLVM pointers make CBE spell a GEP into every ``[N x i8]`` global
    as a cast to ``uint8_t *``.  For a C ``static const`` wrapper SDCC places
    the object in read-only code space and diagnoses that non-const cast.  Bind
    the rewrite to an exact local const byte-array definition and to CBE's
    exact base-cast spelling; mutable arrays and unfamiliar expressions remain
    untouched and therefore continue to fail the warning audit.
    """
    shared = load_canary_adapter()
    definition_pattern = re.compile(
        r"^static const struct l_array_[1-9][0-9]*_uint8_t "
        r"(?P<symbol>[A-Za-z_][A-Za-z0-9_]*)\s*=",
        re.MULTILINE,
    )
    symbols = [match.group("symbol") for match in definition_pattern.finditer(payload)]
    require(
        len(symbols) == len(set(symbols)),
        "duplicate LLVM-CBE const byte-array definitions",
    )

    rewritten = payload
    records: list[dict[str, object]] = []
    for symbol in symbols:
        old = f"((uint8_t*)(&{symbol}))"
        count = shared.mask_c_data(rewritten).count(old)
        if count == 0:
            continue
        new = f"((const uint8_t*)(&{symbol}))"
        rewritten = shared.replace_c_token(rewritten, old, new)
        records.append({"symbol": symbol, "occurrence_count": count})
        require(old not in shared.mask_c_data(rewritten), f"const byte-array cast remains for {symbol}")
    return rewritten, records


def normalize_cbe_unconditional_helper_initializers(
    payload: str,
) -> tuple[str, list[str]]:
    """Fold an immediately assigned CBE helper temporary into its declaration.

    The pinned CBE emits some intrinsic helpers (currently ``fmuladd``) as an
    uninitialized ``r`` declaration followed by one unconditional assignment.
    That source is well-defined, but patched SDCC can still diagnose warning 84
    after inlining it.  Restrict the rewrite to the complete, single-expression
    helper shape so a genuinely conditional or otherwise new form fails the
    later mechanical uninitialized-temporary audit.
    """
    aggregate_pattern = re.compile(
        r"^static __forceinline (?P<type>struct [A-Za-z_][A-Za-z0-9_]*) "
        r"(?P<name>llvm_ctor_[A-Za-z0-9_]+)"
        r"\((?P<arguments>[^\n]*)\) \{\n"
        r"  (?P=type) r;\n"
        r"(?P<assignments>(?:  r\.field[0-9]+ = [A-Za-z_][A-Za-z0-9_]*;\n)+)"
        r"  return r;\n"
        r"\}$",
        re.MULTILINE,
    )
    names: list[str] = []

    def rewrite_aggregate(match: re.Match[str]) -> str:
        arguments = [
            argument.strip()
            for argument in match.group("arguments").split(",")
            if argument.strip()
        ]
        argument_names = []
        for argument in arguments:
            argument_match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)$", argument)
            require(
                argument_match is not None,
                "unsupported LLVM-CBE aggregate helper argument",
            )
            argument_names.append(argument_match.group(1))
        assignments = re.findall(
            r"^  r\.field([0-9]+) = ([A-Za-z_][A-Za-z0-9_]*);$",
            match.group("assignments"),
            re.MULTILINE,
        )
        require(
            assignments
            == [(str(index), name) for index, name in enumerate(argument_names)],
            "LLVM-CBE aggregate helper does not initialize every field in order",
        )
        names.append(match.group("name"))
        return (
            f"static __forceinline {match.group('type')} {match.group('name')}"
            f"({match.group('arguments')}) {{\n"
            f"  {match.group('type')} r = {{ {', '.join(argument_names)} }};\n"
            "  return r;\n"
            "}"
        )

    normalized = aggregate_pattern.sub(rewrite_aggregate, payload)
    for bits in (8, 16, 24, 32, 64):
        kind = 'unsigned _BitInt(24)' if bits == 24 else f'uint{bits}_t'
        for operation in ('abs', 'fshl', 'fshr'):
            if operation == 'abs' and bits == 24:
                continue
            name = f'llvm_OC_{operation}_OC_i{bits}'
            if operation == 'abs':
                arguments = f'{kind} a, bool b'
                prefix = '  (void)b;\n'
                expression = f'(a >> {bits - 1}) ? (0u - a) : a'
            else:
                arguments = f'{kind} a, {kind} b, {kind} c'
                prefix = f'  c = c % {bits};\n'
                expression = (f'c == 0 ? a : ((a << c) | (b >> ({bits} - c)))'
                              if operation == 'fshl' else
                              f'c == 0 ? b : ((a << ({bits} - c)) | (b >> c))')
            header = f'static __forceinline {kind} {name}({arguments}) {{\n'
            body = header + f'  {kind} r;\n' + prefix + f'  r = {expression};\n  return r;\n}}'
            replacement = header + prefix + f'  {kind} r = {expression};\n  return r;\n}}'
            count = normalized.count(body)
            require(count <= 1, 'duplicate scalar intrinsic helper ' + name)
            if count:
                normalized = normalized.replace(body, replacement)
                names.append(name)
    pattern = re.compile(
        r"^static __forceinline (?P<type>[A-Za-z_][A-Za-z0-9_]*(?:\s*\*)?) "
        r"(?P<name>llvm_[A-Za-z0-9_]+)\((?P<arguments>[^\n]*)\) \{\n"
        r"  (?P=type) r;\n"
        r"  r = (?P<expression>[^;\n]+);\n"
        r"  return r;\n"
        r"\}$",
        re.MULTILINE,
    )
    def rewrite(match: re.Match[str]) -> str:
        names.append(match.group("name"))
        return (
            f"static __forceinline {match.group('type')} {match.group('name')}"
            f"({match.group('arguments')}) {{\n"
            f"  {match.group('type')} r = {match.group('expression')};\n"
            "  return r;\n"
            "}"
        )

    normalized = pattern.sub(rewrite, normalized)
    residual = re.findall(
        r"^static __forceinline [^\n]+ llvm_[A-Za-z0-9_]+"
        r"\([^\n]*\) \{\n\s+[^\n]+ r;\s*$",
        normalized,
        re.MULTILINE,
    )
    require(not residual, "unsupported uninitialized LLVM-CBE intrinsic helper")
    return normalized, names


def normalize_mcs51_vtable_address_point_stores(
    payload: str,
) -> tuple[str, list[dict[str, object]]]:
    """Preserve SDCC's CODE tag when a constructor writes its vptr.

    LLVM-CBE represents both an object's vptr and a vtable address point as
    generic ``void *`` values.  On MCS51, SDCC normally converts the address
    of a ``static const`` object to a tagged generic CODE pointer.  That
    conversion is lost specifically when the value is written indirectly
    through CBE's ``*(void **)object`` constructor spelling: SDCC emits the
    link-time address high byte instead of the mandatory 0x80 CODE tag.  An
    explicit code-space cast restores the same generic-pointer conversion.

    Match the complete current CBE/Itanium address-point expression and bind
    it to an exact local vtable definition.  Any other vtable store remains
    visible to the residual audit and fails closed instead of receiving a
    speculative rewrite.
    """
    candidate_pattern = re.compile(
        r"^[ \t]*(?:\*\(void\*\*\)_[0-9]+|\*\(\(void\*\*\)&_[0-9]+\))\s*="
        r"(?![ \t]*\(void \*\)\(const void __code \*\))"
        r"[ \t]*[^;]*"
        r"&_ZT[VC][A-Za-z0-9_]+[^;]*;[ \t]*$",
        re.MULTILINE,
    )
    store_pattern = re.compile(
        r"^(?P<indent>[ \t]*)\*\(void\*\*\)(?P<object>_[0-9]+) = "
        r"(?P<address>\(\(\(&\(&\(&(?P<symbol>_ZTV[A-Za-z0-9_]+)"
        r"\)->field0\)->array\[\(\(int32_t\)(?P<index>[0-9]+)\)\]"
        r"\)\)\));$",
        re.MULTILINE,
    )
    optimized_store_pattern = re.compile(
        r"^(?P<indent>[ \t]*)\*\(\(void\*\*\)&(?P<object>_[0-9]+)\) = "
        r"(?P<address>\(\(\(&\(\(uint8_t\*\)\(\(void\*\)\(const void\*\)&"
        r"(?P<symbol>_ZTV[A-Za-z0-9_]+)\)\)"
        r"\[\(\(signed _BitInt\(24\)\)(?P<offset>[0-9]+)\)\]\)\)\));$",
        re.MULTILINE,
    )
    definitions = set(re.findall(
        r"^static const struct [A-Za-z_][A-Za-z0-9_]* "
        r"(_ZTV[A-Za-z0-9_]+)\s*=",
        payload,
        re.MULTILINE,
    ))
    records: list[dict[str, object]] = []

    def rewrite(match: re.Match[str]) -> str:
        symbol = match.group("symbol")
        require(
            symbol in definitions,
            f"MCS51 vtable address-point store has no exact local definition: {symbol}",
        )
        records.append({
            "object_temporary": match.group("object"),
            "vtable_symbol": symbol,
            "address_point_index": int(match.group("index")),
        })
        return (
            f"{match.group('indent')}*(void**){match.group('object')} = "
            f"(void *)(const void __code *){match.group('address')};"
        )

    def rewrite_optimized(match: re.Match[str]) -> str:
        symbol = match.group("symbol")
        require(symbol in definitions,
                f"MCS51 vtable address-point store has no exact local definition: {symbol}")
        # Two generic-pointer slots precede the function slots in these local
        # primary vtables. Secondary/construction vtables need a separate audit.
        require(int(match.group("offset")) == 6,
                "unsupported MCS51 optimized primary-vtable byte offset")
        records.append({
            "object_temporary": match.group("object"),
            "vtable_symbol": symbol,
            "address_point_index": 2,
            "object_address_taken": True,
        })
        return (
            f"{match.group('indent')}*((void**)&{match.group('object')}) = "
            f"(void *)(const void __code *){match.group('address')};"
        )

    candidates_before = candidate_pattern.findall(payload)
    normalized = store_pattern.sub(rewrite, payload)
    normalized = optimized_store_pattern.sub(rewrite_optimized, normalized)
    require(
        len(records) == len(candidates_before),
        "unsupported LLVM-CBE MCS51 vtable address-point store shape",
    )
    require(
        candidate_pattern.search(normalized) is None,
        "unqualified LLVM-CBE MCS51 vtable address-point store survived",
    )
    return normalized, records


def normalize_mcs51_program_pointer_casts(
    payload: str,
    expected_casts: int,
    expected_member_pointer_casts: int,
) -> tuple[str, list[dict[str, object]]]:
    """Lower audited generic-to-program casts without an SDCC type warning.

    Clang's MCS51 IR uses an explicit ``addrspacecast`` when a generic vtable
    slot is invoked as a 16-bit program function pointer.  LLVM-CBE spells
    that operation as a direct cast from ``void *`` to a function pointer.
    SDCC implements the intended conversion but diagnoses warning 244.  Going
    through ``uintptr_t`` states the ABI operation exactly: retain the 16-bit
    address and discard the generic-pointer space tag before constructing the
    program pointer.

    Accept only the two complete CBE shapes produced by the audited IR: a
    direct virtual dispatch, or the virtual branch of an Itanium member-pointer
    call.  Both the total and member-pointer subset are bound to independent IR
    audit counts.  A new CBE spelling therefore fails closed instead of being
    rewritten speculatively.
    """

    require(expected_casts >= 0, "negative MCS51 program-pointer cast count")
    require(
        0 <= expected_member_pointer_casts <= expected_casts,
        "invalid MCS51 member-pointer cast subset",
    )
    unqualified = re.compile(
        r"\(\(llvm_cbe_program_pointer\)(?P<source>_[0-9]+)\)"
    )
    qualified = re.compile(
        r"\(\(llvm_cbe_program_pointer\)\(uintptr_t\)(?P<source>_[0-9]+)\)"
    )
    assignment = re.compile(
        r"^(?P<indent>[ \t]*)(?P<destination>_[0-9]+)\s*=\s*"
        r"\(\(llvm_cbe_program_pointer\)(?P<source>_[0-9]+)\);[ \t]*$"
    )
    direct_call = re.compile(
        r"^(?P<indent>[ \t]*)(?:(?P<destination>_[0-9]+)\s*=\s*)?"
        r"(?:/\*tail\*/[ \t]*)?"
        r"\(\((?P<function_type>l_fptr_[0-9]+)\*\)"
        r"\(\(\(llvm_cbe_program_pointer\)(?P<source>_[0-9]+)\)\)\)"
        r"\((?P<arguments>[^;\n]*)\);[ \t]*$"
    )

    candidate_count = len(unqualified.findall(payload))
    require(
        candidate_count == expected_casts,
        "LLVM-CBE MCS51 generic-to-program cast count differs from audited IR: "
        f"expected {expected_casts}, got {candidate_count}",
    )
    qualified_before = len(qualified.findall(payload))
    records: list[dict[str, object]] = []
    normalized_lines: list[str] = []
    for line in payload.splitlines(keepends=True):
        line_payload = line.rstrip("\r\n")
        endings = line[len(line_payload):]
        candidates = list(unqualified.finditer(line_payload))
        if not candidates:
            normalized_lines.append(line)
            continue
        require(
            len(candidates) == 1,
            "multiple MCS51 generic-to-program casts on one CBE line",
        )
        match = assignment.fullmatch(line_payload)
        if match is not None:
            kind = "member-pointer-virtual-branch"
            function_type = None
        else:
            match = direct_call.fullmatch(line_payload)
            require(
                match is not None,
                "unsupported LLVM-CBE MCS51 generic-to-program cast shape",
            )
            kind = "direct-virtual-dispatch"
            function_type = match.group("function_type")
        source = match.group("source")
        records.append({
            "kind": kind,
            "source_temporary": source,
            "destination_temporary": match.group("destination"),
            "function_type": function_type,
        })
        rewritten, count = unqualified.subn(
            f"((llvm_cbe_program_pointer)(uintptr_t){source})",
            line_payload,
        )
        require(count == 1, "MCS51 program-pointer cast rewrite was not unique")
        normalized_lines.append(rewritten + endings)

    member_pointer_count = sum(
        record["kind"] == "member-pointer-virtual-branch"
        for record in records
    )
    require(
        member_pointer_count == expected_member_pointer_casts,
        "LLVM-CBE MCS51 member-pointer cast subset differs from audited IR: "
        f"expected {expected_member_pointer_casts}, got {member_pointer_count}",
    )
    require(
        len(records) - member_pointer_count
        == expected_casts - expected_member_pointer_casts,
        "LLVM-CBE MCS51 direct virtual-dispatch cast subset differs from audited IR",
    )
    normalized = "".join(normalized_lines)
    require(
        unqualified.search(normalized) is None,
        "unqualified LLVM-CBE MCS51 generic-to-program cast survived",
    )
    require(
        len(qualified.findall(normalized)) == qualified_before + expected_casts,
        "normalized LLVM-CBE MCS51 program-pointer cast count differs",
    )
    return normalized, records


def adapt_cbe(
    shared,
    raw: str,
    constructors: list[dict[str, object]],
    abi_symbol: str,
    expected_trap_count: int,
    target_profile: str,
    expected_program_pointer_casts: int,
    expected_member_pointer_casts: int,
    expected_function_alignment_symbols: list[str],
    protected_c_symbols: list[str],
):
    marker = "\n/* Global Declarations */\n"
    require(raw.count(marker) == 1, "LLVM-CBE global declaration marker mismatch")
    raw_prefix, payload = raw.split(marker, 1)
    program_pointer_typedef = "typedef void (*llvm_cbe_program_pointer)(void);"
    expected_program_typedefs = 1 if target_profile == "mcs51" else 0
    require(
        raw_prefix.count(program_pointer_typedef) == expected_program_typedefs,
        "LLVM-CBE program-pointer typedef does not match the selected target",
    )
    fcmp_helpers, fcmp_helper_names = shared.extract_cbe_fcmp_helpers(
        raw_prefix, payload
    )
    fp_constant_typedefs, fp_constant_typedef_names = (
        shared.extract_cbe_fp_constant_typedefs(raw_prefix, payload)
    )
    fpclass_helpers, fpclass_helper_names = shared.extract_cbe_fpclass_helpers(
        raw_prefix, payload
    )
    native_string_headers, native_memory_functions = (
        shared.extract_cbe_native_string_header(raw_prefix, payload)
    )
    forbidden = [
        name for name, pattern in shared.FORBIDDEN_CBE_PAYLOAD.items()
        if pattern.search(shared.mask_c_data(payload))
    ]
    require(not forbidden, "forbidden LLVM-CBE payload: " + ", ".join(forbidden))

    function_alignment_pattern = re.compile(
        r"^[^;\n]*?\b(?P<symbol>[A-Za-z_][A-Za-z0-9_]*)"
        r"\([^;\n]*\)[^;\n]*\b__FUNCTIONALIGN__"
        r"\((?P<alignment>[0-9]+)\)\s*;\s*$",
        re.MULTILINE,
    )
    function_alignment_records = [
        (match.group("symbol"), int(match.group("alignment")))
        for match in function_alignment_pattern.finditer(payload)
    ]
    require(
        shared.mask_c_data(payload).count("__FUNCTIONALIGN__(") == len(function_alignment_records),
        "unsupported LLVM-CBE function-alignment declaration shape",
    )
    require(
        all(alignment == 2 for _symbol, alignment in function_alignment_records),
        "LLVM-CBE emitted an unsupported function alignment",
    )
    observed_function_alignment_symbols = sorted(
        symbol for symbol, _alignment in function_alignment_records
    )
    require(
        len(observed_function_alignment_symbols)
        == len(set(observed_function_alignment_symbols)),
        "LLVM-CBE emitted duplicate aligned function declarations",
    )
    require(
        observed_function_alignment_symbols
        == expected_function_alignment_symbols,
        "function alignment changed between LLVM IR and LLVM-CBE",
    )

    declared = shared.parse_cbe_ctor_declarations(payload)
    expected = [shared.cbe_mangle(str(entry["llvm_symbol"])) for entry in constructors]
    require(
        declared == expected,
        f"constructor order changed between LLVM and CBE: expected {expected!r}, got {declared!r}",
    )
    require(shared.mask_c_data(payload).count(" __ATTRIBUTE_CTOR__") == len(expected),
            "constructor attribute count mismatch")
    payload = shared.replace_c_token(payload, " __ATTRIBUTE_CTOR__", "")

    trap_count = shared.mask_c_data(payload).count("__builtin_trap();")
    require(
        trap_count == expected_trap_count,
        "LLVM-CBE trap count differs from audited LLVM IR: "
        f"expected {expected_trap_count}, got {trap_count}",
    )
    payload = shared.replace_c_token(payload, "__builtin_trap();", "stcxx_runtime_panic(5);")
    payload, select_helpers_initialized = normalize_cbe_select_helpers(payload)
    payload, unconditional_helpers_initialized = (
        normalize_cbe_unconditional_helper_initializers(payload)
    )
    payload, typedefs_before, typedefs_after = shared.normalize_cbe_function_typedefs(payload)
    payload, fabs_helpers = shared.normalize_cbe_fabs_helpers(payload)
    mcs251_indirect_calls = []
    if target_profile == "mcs251":
        payload, mcs251_indirect_calls = shared.normalize_mcs251_indirect_calls(payload)
    payload, bitint_u24_negation_helpers_repaired = (
        normalize_cbe_bitint_u24_negation(payload)
    )
    payload, legacy_u24_negation_helpers_repaired = (
        shared.normalize_cbe_u24_negation(payload)
    )
    u24_negation_helpers_repaired = (
        bitint_u24_negation_helpers_repaired
        + legacy_u24_negation_helpers_repaired
    )
    require(
        u24_negation_helpers_repaired <= 1,
        "duplicate malformed llvm-cbe i24 negation helpers across spellings",
    )
    payload, integer_negation_helpers_repaired = shared.normalize_cbe_integer_negation(payload)
    payload, u32_power_of_two_division_rewrites = (
        shared.normalize_cbe_u32_power_of_two_division(payload)
    )
    payload, pointer_rewrites = shared.normalize_cbe_address_roundtrips(payload)
    payload, string_array_arguments = shared.normalize_cbe_string_array_arguments(payload)
    mcs51_vtable_address_point_stores = []
    if target_profile == "mcs51":
        # Bind the CODE-space qualification to the original CBE vtable shape.
        # Static byte-GEP lowering below replaces that shape with a byte offset;
        # the explicit address-space cast must survive that later rewrite.
        payload, mcs51_vtable_address_point_stores = (
            normalize_mcs51_vtable_address_point_stores(payload)
        )
    payload, static_byte_geps = shared.normalize_cbe_static_byte_geps(payload)
    payload, exact_byte_arrays_rewritten = (
        shared.normalize_cbe_exact_byte_array_initializers(payload)
    )
    payload, const_byte_array_address_casts_rewritten = (
        preserve_cbe_const_byte_array_addresses(payload)
    )
    payload, stateless_struct_returns_initialized = (
        shared.normalize_cbe_stateless_struct_returns(payload)
    )
    payload, single_block_pointer_temporaries_eliminated = (
        shared.normalize_cbe_single_block_pointer_temporaries(payload)
    )
    payload, removed_consts, zero_arrays = remove_duplicate_const_declarations(shared, payload)
    payload, vtable_addresses = shared.normalize_cbe_vtable_addresses(payload)
    if target_profile == "mcs51":
        payload, mcs51_program_pointer_casts = (
            normalize_mcs51_program_pointer_casts(
                payload,
                expected_program_pointer_casts,
                expected_member_pointer_casts,
            )
        )
    else:
        require(
            expected_program_pointer_casts == 0
            and expected_member_pointer_casts == 0,
            "MCS251 unexpectedly requested MCS51 program-pointer rewrites",
        )
        mcs51_program_pointer_casts = []

    require(re.search(rf"\b{re.escape(abi_symbol)}\s*\(void\)", payload) is not None,
            "runtime ABI identity is absent from CBE output")
    require(re.search(r"\bstcxx_runtime_panic\s*\(", payload) is not None,
            "runtime panic implementation is absent from CBE output")

    preamble = """/* Generated by tools/cpp-cli/adapt.py. */
#include <stddef.h>
#include <stdint.h>
""" + ("\n".join(native_string_headers) + "\n"
       if native_string_headers else "") + """#ifndef __cplusplus
typedef unsigned char bool;
#endif
""" + (program_pointer_typedef + "\n" if target_profile == "mcs51" else "") + (
        "\n".join(fp_constant_typedefs) + "\n"
        if fp_constant_typedefs else ""
    ) + """
#define __forceinline inline
#define __ATTRIBUTE_WEAK__
#define __MSVC_INLINE__
#define __noreturn _Noreturn
#define __ATTRIBUTELIST__(x)
#define __FUNCTIONALIGN__(x)
#define __attribute__(x)
#define __builtin_expect(value, expected) (value)
#define __builtin_unreachable() do { } while (0)

""" + (
        "\n\n".join(fpclass_helpers + fcmp_helpers) + "\n\n"
        if fpclass_helpers or fcmp_helpers else ""
    )
    bridge = [
        "", "/* Constructor bridge generated from llvm.global_ctors. */",
        "extern int main(void);", "",
        "void __stcxx_bridge_require_core_main(void)", "{", "  (void)main();", "}", "",
        "void __stcxx_bridge_require_abi(void)", "{", f"  {abi_symbol}();", "}", "",
        "uint16_t __stcxx_bridge_ctor_count(void)", "{",
        f"  return (uint16_t){len(expected)}u;", "}", "",
        "void __stcxx_bridge_invoke_ctor(uint16_t index)", "{", "  switch (index) {",
    ]
    for index, name in enumerate(expected):
        bridge.extend([f"  case {index}u:", f"    {name}();", "    return;"])
    bridge.extend([
        "  default:", "    stcxx_runtime_panic(2);", "    return;", "  }", "}", "",
    ])
    unshortened_adapted = preamble + marker + payload + "\n".join(bridge)
    adapted, shortening_report, identifier_mapping = shorten_c_identifiers(
        unshortened_adapted, protected_c_symbols
    )
    emitted_constructors = [
        identifier_mapping.get(symbol, symbol) for symbol in expected
    ]
    emitted_function_alignment_symbols = sorted(
        identifier_mapping.get(symbol, symbol)
        for symbol in observed_function_alignment_symbols
    )
    require(
        len(emitted_constructors) == len(set(emitted_constructors)),
        "constructor symbols collide after C identifier shortening",
    )
    require(
        len(emitted_function_alignment_symbols)
        == len(set(emitted_function_alignment_symbols)),
        "function-alignment symbols collide after C identifier shortening",
    )
    return adapted, {
        "raw_c_sha256": sha256_text(raw),
        "adapted_c_sha256": sha256_text(adapted),
        "unshortened_adapted_c_sha256": sha256_text(unshortened_adapted),
        "original_constructor_c_symbols": expected,
        "constructor_c_symbols": emitted_constructors,
        "core_main_archive_anchor": "main",
        "abstract_base_traps_mapped": trap_count,
        "function_typedef_order_before": typedefs_before,
        "function_typedef_order_after": typedefs_after,
        "fp_constant_typedefs_preserved": fp_constant_typedef_names,
        "fpclass_helpers_preserved": fpclass_helper_names,
        "fabs_helpers_normalized": fabs_helpers,
        "mcs251_indirect_calls_normalized": mcs251_indirect_calls,
        "native_memory_header_preserved": bool(native_string_headers),
        "native_memory_functions": native_memory_functions,
        "u24_negation_helpers_repaired": u24_negation_helpers_repaired,
        "u32_power_of_two_division_rewrites": (
            u32_power_of_two_division_rewrites
        ),
        "select_helpers_initialized": select_helpers_initialized,
        "unconditional_helpers_initialized": unconditional_helpers_initialized,
        "floating_comparison_helpers_preserved": fcmp_helper_names,
        "native_string_array_arguments": string_array_arguments,
        "static_byte_geps": static_byte_geps,
        "exact_byte_array_initializers_rewritten": exact_byte_arrays_rewritten,
        "integer_negation_helpers_repaired": integer_negation_helpers_repaired,
        "const_byte_array_address_casts_rewritten": (
            const_byte_array_address_casts_rewritten
        ),
        "stateless_struct_returns_initialized": stateless_struct_returns_initialized,
        "single_block_pointer_temporaries_eliminated": single_block_pointer_temporaries_eliminated,
        "mcs51_program_pointer_casts": mcs51_program_pointer_casts,
        "mcs51_vtable_address_point_stores": mcs51_vtable_address_point_stores,
        "vtable_addresses_normalized": vtable_addresses,
        "original_function_alignment_symbols": (
            observed_function_alignment_symbols
        ),
        "function_alignment_symbols": emitted_function_alignment_symbols,
        "function_alignment_bytes": 2,
        "c_identifier_shortening": shortening_report,
        "generic_pointer_rewrites": pointer_rewrites,
        "duplicate_const_declarations_removed": removed_consts,
        "zero_initialized_const_arrays": zero_arrays,
        "forbidden_payload_categories": [],
        "target_profile": target_profile,
        "program_pointer_typedef_preserved": expected_program_typedefs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ir", required=True, type=Path)
    parser.add_argument("--raw-c", required=True, type=Path)
    parser.add_argument("--native-storage", type=Path)
    parser.add_argument("--c-abi-preserve", required=True, type=Path)
    parser.add_argument("--output-c", required=True, type=Path)
    parser.add_argument("--audit-json", required=True, type=Path)
    parser.add_argument("--expected-triple", required=True)
    parser.add_argument("--expected-layout", required=True)
    parser.add_argument("--abi-identity-symbol", required=True)
    parser.add_argument("--target-profile", choices=("mcs51", "mcs251"), required=True)
    args = parser.parse_args()
    shared = load_canary_adapter()
    ir = args.ir.read_text(encoding="utf-8")
    raw = args.raw_c.read_text(encoding="utf-8")
    storage_report = None
    if args.native_storage:
        spec = importlib.util.spec_from_file_location("native_storage", Path(__file__).with_name("native-storage.py"))
        storage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(storage)
        raw, storage_report = storage.apply_storage(raw, ir,
            json.loads(args.native_storage.read_text()), args.target_profile, shared.cbe_mangle)
    c_abi_preserve_symbols, c_abi_preserve_sha256 = read_c_abi_preserve(
        args.c_abi_preserve
    )
    ir_report = audit_ir(
        shared,
        ir,
        args.expected_triple,
        args.expected_layout,
        args.abi_identity_symbol,
        args.target_profile,
        c_abi_preserve_symbols,
        c_abi_preserve_sha256,
    )
    original_function_alignment_symbols = list(
        ir_report["function_alignment"]["c_symbols"]
    )
    adapted, cbe_report = adapt_cbe(
        shared,
        raw,
        list(ir_report["constructors"]),
        args.abi_identity_symbol,
        int(ir_report["audited_trap_call_count"]),
        args.target_profile,
        int(ir_report["program_address_space_audit"].get(
            "virtual_member_generic_to_program_casts", 0
        )),
        int(ir_report["pointer_integer_conversions"].get(
            "program_address_space_member_calls", 0
        )),
        original_function_alignment_symbols,
        list(ir_report["c_identifier_linkage"]["protected_c_symbols"]),
    )
    ir_report["function_alignment"]["original_c_symbols"] = (
        original_function_alignment_symbols
    )
    ir_report["function_alignment"]["c_symbols"] = list(
        cbe_report["function_alignment_symbols"]
    )
    report = {
        "schema_version": ADAPTER_SCHEMA_VERSION,
        "outcome": "pass",
        "qualification": "EXPERIMENTAL_STC_MCS51_MCS251_12MHZ_ARDUINO_CLI",
        "ir": ir_report,
        "llvm_cbe": cbe_report,
        "native_storage": storage_report,
    }
    args.output_c.parent.mkdir(parents=True, exist_ok=True)
    args.output_c.write_text(adapted, encoding="utf-8", newline="\n")
    args.audit_json.write_text(json.dumps(report, indent=2) + "\n",
                               encoding="utf-8", newline="\n")
    print("STCXX_ARDUINO_CLI_ADAPTER=PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError) as error:
        print(f"STCXX_ARDUINO_CLI_ADAPTER=FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
