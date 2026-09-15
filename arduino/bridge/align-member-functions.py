#!/usr/bin/env python3
"""Preserve Clang's two-byte function alignment through locked ASxxxx.

SDCC accepts the LLVM-CBE ``__FUNCTIONALIGN__(2)`` spelling but does not
carry it into ASxxxx assembly.  The Itanium member-function-pointer ABI uses
bit zero as its virtual-call discriminator, so every function which Clang
marked ``align 2`` must remain even after the SDCC bridge is assembled and
relocated.  This helper inserts the native ASxxxx ``.even`` or ``.odd`` local
parity directive and then audits the relocated ``.rst`` listing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import NoReturn


SCHEMA_VERSION = 2
ALIGNMENT_BYTES = 2
EXIT_REALIGN_REQUIRED = 3
SDCC_SYMNAME_MAX = 256
ASXXXX_NCPS = 256
ASXXXX_SYMBOL_MAX = ASXXXX_NCPS - 1
ASXXXX_GLOBAL_PREFIX = "_"
ADAPTER_SCHEMA_VERSION = 2
ASXXXX_SAFE_C_IDENTIFIER_MAX = 254
C_IDENTIFIER_SHORTENING_PREFIX = "stcxx_cbe_id_"
C_IDENTIFIER_SHORTENING_HASH = "sha256"
C_IDENTIFIER_SHORTENING_DOMAIN = "stcxx-cbe-c-identifier-v1"
C_IDENTIFIER_SHORTENING_POLICY = "final-c-preprocessing-token-sha256-v1"
SUPPORTED_PROFILES = ("mcs51", "mcs251")
SUPPORTED_LOCAL_PARITIES = ("even", "odd")
SAFE_SYMBOL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
SAFE_LLVM_SYMBOL = re.compile(r"[A-Za-z0-9_.$-]+\Z")
MARKER_PREFIX = "; STCXX_FUNCTION_ALIGNMENT "


class AlignmentError(RuntimeError):
    """An input escaped the deliberately narrow alignment audit."""


def fail(message: str) -> NoReturn:
    raise AlignmentError(message)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def read_file(path: Path, description: str) -> bytes:
    require(path.is_file(), f"missing {description}: {path}")
    try:
        return path.read_bytes()
    except OSError as error:
        fail(f"could not read {description} {path}: {error}")


def read_json(path: Path, description: str) -> tuple[dict[str, object], bytes]:
    payload = read_file(path, description)
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"invalid {description} {path}: {error}")
    require(isinstance(value, dict), f"{description} root is not an object")
    return value, payload


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", suffix=".tmp",
            dir=path.parent, delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    except OSError as error:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass
        fail(f"could not atomically write {path}: {error}")


def write_json(path: Path, value: dict[str, object]) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    atomic_write(path, payload)


def validate_profile(profile: object) -> str:
    require(
        isinstance(profile, str) and profile in SUPPORTED_PROFILES,
        f"unsupported target profile: {profile!r}",
    )
    return profile


def validate_symbol_list(
    value: object,
    field: str,
    pattern: re.Pattern[str] = SAFE_SYMBOL,
) -> list[str]:
    require(isinstance(value, list), f"{field} is not a list")
    symbols: list[str] = []
    for symbol in value:
        require(isinstance(symbol, str), f"{field} contains a non-string symbol")
        require(
            pattern.fullmatch(symbol) is not None,
            f"{field} contains an unsafe C/LLVM identifier: {symbol!r}",
        )
        symbols.append(symbol)
    require(len(symbols) == len(set(symbols)), f"{field} contains duplicates")
    require(symbols == sorted(symbols), f"{field} is not deterministically sorted")
    return symbols


def nested_object(parent: dict[str, object], key: str, field: str) -> dict[str, object]:
    value = parent.get(key)
    require(isinstance(value, dict), f"{field} is not an object")
    return value


def sdcc_assembly_label(c_symbol: str) -> str:
    """Map a C identifier to the global label emitted by SDCC.

    SDCC limits the C identifier itself to ``SDCC_SYMNAME_MAX`` characters;
    the MCS251 backend then prepends the ASxxxx global-symbol prefix.
    ``SAFE_SYMBOL`` restricts identifiers to ASCII, so character and byte
    truncation are identical here.
    """

    return ASXXXX_GLOBAL_PREFIX + c_symbol[:SDCC_SYMNAME_MAX]


def asxxxx_symbol_identity(c_symbol: str) -> str:
    """Return the symbol identity parsed by locked ASxxxx/ASlink.

    The raw SDCC label remains visible in ``.lst``/``.rst``, but ASxxxx's
    ``NCPS`` buffer accepts at most ``NCPS - 1`` characters for its internal
    and REL-file symbol identity.
    """

    return sdcc_assembly_label(c_symbol)[:ASXXXX_SYMBOL_MAX]


def expected_shortened_c_identifier(original: str) -> str:
    digest = hashlib.sha256(
        C_IDENTIFIER_SHORTENING_DOMAIN.encode("ascii")
        + b"\x00" + original.encode("ascii")
    ).hexdigest()
    return C_IDENTIFIER_SHORTENING_PREFIX + digest


def function_alignment_records(
    c_symbols: list[str], directive: str
) -> list[dict[str, str]]:
    """Build the audited C-symbol/ASxxxx-label mapping, rejecting aliases."""

    labels: dict[str, list[str]] = {}
    identities: dict[str, list[str]] = {}
    for symbol in c_symbols:
        labels.setdefault(sdcc_assembly_label(symbol), []).append(symbol)
        identities.setdefault(asxxxx_symbol_identity(symbol), []).append(symbol)
    collisions = [
        (label, symbols)
        for label, symbols in sorted(labels.items())
        if len(symbols) != 1
    ]
    require(
        not collisions,
        f"SDCC {SDCC_SYMNAME_MAX}-character C identifier truncation collision: "
        + "; ".join(
            f"{label} <- {', '.join(symbols)}" for label, symbols in collisions
        ),
    )
    identity_collisions = [
        (identity, symbols)
        for identity, symbols in sorted(identities.items())
        if len(symbols) != 1
    ]
    require(
        not identity_collisions,
        f"ASxxxx {ASXXXX_SYMBOL_MAX}-character symbol truncation collision: "
        + "; ".join(
            f"{identity} <- {', '.join(symbols)}"
            for identity, symbols in identity_collisions
        ),
    )
    return [
        {
            "assembly_label": sdcc_assembly_label(symbol),
            "asxxxx_symbol": asxxxx_symbol_identity(symbol),
            "c_symbol": symbol,
            "directive": directive,
        }
        for symbol in c_symbols
    ]


def alignment_contract(
    adapter_audit: dict[str, object], target_profile: str
) -> tuple[list[str], list[str], list[str]]:
    require(
        type(adapter_audit.get("schema_version")) is int
        and adapter_audit.get("schema_version") == ADAPTER_SCHEMA_VERSION,
        "unsupported adapter audit schema",
    )
    require(adapter_audit.get("outcome") == "pass", "adapter audit did not pass")

    ir = nested_object(adapter_audit, "ir", "adapter audit ir")
    preservation = nested_object(
        ir, "c_abi_preservation", "adapter audit ir.c_abi_preservation"
    )
    require(
        preservation.get("policy") == "exact-internalize-public-api-list-file",
        "unsupported C ABI preservation policy",
    )
    preserve_symbols = validate_symbol_list(
        preservation.get("symbols"),
        "adapter audit ir.c_abi_preservation.symbols",
        SAFE_LLVM_SYMBOL,
    )
    require(
        type(preservation.get("symbol_count")) is int
        and preservation.get("symbol_count") == len(preserve_symbols),
        "C ABI preservation symbol count mismatch",
    )
    require(
        isinstance(preservation.get("sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", str(preservation.get("sha256")))
        is not None,
        "invalid C ABI preservation SHA-256",
    )
    linkage = nested_object(
        ir, "c_identifier_linkage", "adapter audit ir.c_identifier_linkage"
    )
    require(
        linkage.get("policy")
        == "protect-declarations-and-noninternal-definitions",
        "unsupported C identifier linkage policy",
    )
    require(
        type(linkage.get("global_symbol_count")) is int
        and int(linkage.get("global_symbol_count", -1)) >= 0,
        "invalid LLVM global-symbol count",
    )
    require(
        type(linkage.get("cbe_mangling_collision_count")) is int
        and linkage.get("cbe_mangling_collision_count") == 0,
        "LLVM-CBE global-name mangling collision audit did not pass",
    )
    protected_llvm_symbols = validate_symbol_list(
        linkage.get("protected_llvm_symbols"),
        "adapter audit ir.c_identifier_linkage.protected_llvm_symbols",
        SAFE_LLVM_SYMBOL,
    )
    protected_c_symbols = validate_symbol_list(
        linkage.get("protected_c_symbols"),
        "adapter audit ir.c_identifier_linkage.protected_c_symbols",
    )
    require(
        len(protected_llvm_symbols) == len(protected_c_symbols),
        "protected LLVM/C symbol counts differ",
    )
    require(
        not sorted(set(preserve_symbols) - set(protected_llvm_symbols)),
        "C ABI preserve roots are absent from protected LLVM symbols",
    )
    function_alignment = nested_object(
        ir, "function_alignment", "adapter audit ir.function_alignment"
    )
    require(
        function_alignment.get("alignment_bytes") == ALIGNMENT_BYTES,
        "adapter audit function alignment is not exactly two bytes",
    )
    llvm_symbols = validate_symbol_list(
        function_alignment.get("llvm_symbols"),
        "adapter audit ir.function_alignment.llvm_symbols",
        SAFE_LLVM_SYMBOL,
    )
    original_c_symbols = validate_symbol_list(
        function_alignment.get("original_c_symbols"),
        "adapter audit ir.function_alignment.original_c_symbols",
    )
    c_symbols = validate_symbol_list(
        function_alignment.get("c_symbols"),
        "adapter audit ir.function_alignment.c_symbols",
    )
    require(
        len(llvm_symbols) == len(original_c_symbols) == len(c_symbols),
        "LLVM/original/emitted C function-alignment symbol counts differ",
    )
    require(
        all(len(symbol) <= ASXXXX_SAFE_C_IDENTIFIER_MAX for symbol in c_symbols),
        "emitted function-alignment symbol exceeds the ASxxxx-safe limit",
    )

    pointer_conversions = nested_object(
        ir,
        "pointer_integer_conversions",
        "adapter audit ir.pointer_integer_conversions",
    )
    member_symbols = validate_symbol_list(
        pointer_conversions.get("member_function_symbols"),
        "adapter audit ir.pointer_integer_conversions.member_function_symbols",
        SAFE_LLVM_SYMBOL,
    )
    missing_members = sorted(set(member_symbols) - set(llvm_symbols))
    require(
        not missing_members,
        "member-function-pointer targets lack LLVM align-2 evidence: "
        + ", ".join(missing_members),
    )

    llvm_cbe = nested_object(adapter_audit, "llvm_cbe", "adapter audit llvm_cbe")
    require(
        llvm_cbe.get("target_profile") == target_profile,
        "adapter audit target profile does not match requested profile",
    )
    cbe_original_symbols = validate_symbol_list(
        llvm_cbe.get("original_function_alignment_symbols"),
        "adapter audit llvm_cbe.original_function_alignment_symbols",
    )
    cbe_symbols = validate_symbol_list(
        llvm_cbe.get("function_alignment_symbols"),
        "adapter audit llvm_cbe.function_alignment_symbols",
    )
    require(
        cbe_original_symbols == original_c_symbols,
        "LLVM-CBE original function-alignment symbols do not match LLVM audit",
    )
    shortening = nested_object(
        llvm_cbe,
        "c_identifier_shortening",
        "adapter audit llvm_cbe.c_identifier_shortening",
    )
    require(
        shortening.get("policy") == C_IDENTIFIER_SHORTENING_POLICY,
        "unsupported C identifier shortening policy",
    )
    exact_fields = {
        "max_c_identifier_length": ASXXXX_SAFE_C_IDENTIFIER_MAX,
        "asxxxx_global_prefix_length": len(ASXXXX_GLOBAL_PREFIX),
        "asxxxx_symbol_identity_max": ASXXXX_SYMBOL_MAX,
        "preprocessor_tokens_rewritten": 0,
    }
    for field, expected in exact_fields.items():
        require(
            type(shortening.get(field)) is int
            and shortening.get(field) == expected,
            f"invalid C identifier shortening {field}",
        )
    require(
        shortening.get("replacement_prefix") == C_IDENTIFIER_SHORTENING_PREFIX
        and shortening.get("hash_algorithm") == C_IDENTIFIER_SHORTENING_HASH
        and shortening.get("hash_domain") == C_IDENTIFIER_SHORTENING_DOMAIN,
        "C identifier shortening hash contract mismatch",
    )
    for field in ("input_c_sha256", "output_c_sha256"):
        require(
            isinstance(shortening.get(field), str)
            and re.fullmatch(r"[0-9a-f]{64}", str(shortening.get(field)))
            is not None,
            f"invalid C identifier shortening {field}",
        )
    require(
        shortening.get("input_c_sha256")
        == llvm_cbe.get("unshortened_adapted_c_sha256")
        and shortening.get("output_c_sha256")
        == llvm_cbe.get("adapted_c_sha256"),
        "C identifier shortening hashes do not bind the adapted C audit",
    )
    for field in (
        "code_identifier_count", "preprocessor_identifier_count",
        "protected_identifier_count", "rewritten_identifier_count",
        "rewritten_token_count",
    ):
        require(
            type(shortening.get(field)) is int
            and int(shortening.get(field, -1)) >= 0,
            f"invalid C identifier shortening {field}",
        )
    require(
        shortening.get("protected_identifier_count") == len(protected_c_symbols),
        "protected C identifier count mismatch",
    )
    record_values = shortening.get("records")
    require(isinstance(record_values, list), "C identifier shortening records are not a list")
    identifier_mapping: dict[str, str] = {}
    token_count = 0
    previous_original: str | None = None
    emitted_names: set[str] = set()
    expected_record_fields = {
        "original_identifier", "emitted_identifier", "original_length",
        "emitted_length", "token_occurrences",
    }
    for index, value in enumerate(record_values):
        require(isinstance(value, dict), f"C identifier shortening record {index} is not an object")
        require(
            set(value) == expected_record_fields,
            f"C identifier shortening record {index} has an unsupported shape",
        )
        original = value.get("original_identifier")
        emitted = value.get("emitted_identifier")
        require(
            isinstance(original, str) and SAFE_SYMBOL.fullmatch(original) is not None,
            f"unsafe original C identifier in shortening record {index}",
        )
        require(
            isinstance(emitted, str) and SAFE_SYMBOL.fullmatch(emitted) is not None,
            f"unsafe emitted C identifier in shortening record {index}",
        )
        require(
            previous_original is None or previous_original < original,
            "C identifier shortening records are not sorted and unique",
        )
        previous_original = original
        require(
            type(value.get("original_length")) is int
            and value.get("original_length") == len(original)
            and len(original) > ASXXXX_SAFE_C_IDENTIFIER_MAX,
            f"invalid original C identifier length in shortening record {index}",
        )
        require(
            type(value.get("emitted_length")) is int
            and value.get("emitted_length") == len(emitted)
            and len(emitted) <= ASXXXX_SAFE_C_IDENTIFIER_MAX,
            f"invalid emitted C identifier length in shortening record {index}",
        )
        require(
            emitted == expected_shortened_c_identifier(original),
            f"non-deterministic emitted C identifier in shortening record {index}",
        )
        require(
            emitted not in emitted_names,
            "C identifier shortening records contain an emitted-name collision",
        )
        emitted_names.add(emitted)
        require(
            original not in protected_c_symbols,
            "C identifier shortening record renames a protected symbol",
        )
        require(
            type(value.get("token_occurrences")) is int
            and int(value.get("token_occurrences", 0)) > 0,
            f"invalid token count in C identifier shortening record {index}",
        )
        token_count += int(value["token_occurrences"])
        identifier_mapping[original] = emitted
    require(
        shortening.get("rewritten_identifier_count") == len(identifier_mapping)
        and shortening.get("rewritten_token_count") == token_count,
        "C identifier shortening aggregate counts differ from records",
    )
    require(
        all(
            len(symbol) <= ASXXXX_SAFE_C_IDENTIFIER_MAX
            or symbol in identifier_mapping
            for symbol in original_c_symbols
        ),
        "overlong original function-alignment symbol lacks a shortening record",
    )
    expected_emitted_symbols = sorted(
        identifier_mapping.get(symbol, symbol) for symbol in original_c_symbols
    )
    require(
        cbe_symbols == c_symbols == expected_emitted_symbols,
        "LLVM-CBE emitted function-alignment symbols do not match shortening records",
    )
    return llvm_symbols, c_symbols, member_symbols


def decode_assembly(payload: bytes) -> tuple[str, str]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        fail(f"assembly is not UTF-8/ASCII: {error}")
    require("\x00" not in text, "assembly contains a NUL byte")
    require("\r" not in text.replace("\r\n", ""), "assembly has unsupported bare CR")
    has_crlf = "\r\n" in text
    has_lf = "\n" in text.replace("\r\n", "")
    require(not (has_crlf and has_lf), "assembly has mixed line endings")
    return text, "\r\n" if has_crlf else "\n"


def validate_realign_request(
    prior_audit: dict[str, object],
    input_assembly: Path,
    output_assembly: Path,
    adapter_audit_path: Path,
    target_profile: str,
    original: bytes,
    adapter_payload: bytes,
    c_symbols: list[str],
) -> None:
    """Validate the complete even-local attempt before permitting odd retry."""

    require(
        prior_audit.get("schema_version") == SCHEMA_VERSION,
        "unsupported prior alignment audit schema",
    )
    require(
        prior_audit.get("outcome") == "realign_required"
        and prior_audit.get("phase") == "realign_required",
        "prior audit is not a realignment request",
    )
    require(
        prior_audit.get("target_profile") == target_profile,
        "realignment target profile changed",
    )
    require(
        prior_audit.get("alignment_bytes") == ALIGNMENT_BYTES,
        "prior audit alignment width changed",
    )
    require(prior_audit.get("directive") == ".even", "prior attempt was not even-local")
    require(
        prior_audit.get("policy")
        == "asxxxx-local-parity-for-all-clang-align-2-functions",
        "prior alignment policy changed",
    )
    prior_tool = nested_object(prior_audit, "tool", "prior alignment audit tool")
    require(
        prior_tool.get("path") == str(Path(__file__).resolve())
        and prior_tool.get("sha256") == sha256_path(Path(__file__).resolve()),
        "prior alignment helper provenance changed",
    )

    prior_assembly = nested_object(
        prior_audit, "assembly", "prior alignment audit assembly"
    )
    require(
        prior_assembly.get("local_parity") == "even",
        "realignment did not follow an even-local attempt",
    )
    require(
        prior_assembly.get("input_path") == str(input_assembly)
        and prior_assembly.get("input_sha256") == sha256_bytes(original),
        "realignment raw assembly changed",
    )
    require(
        prior_assembly.get("output_path") == str(output_assembly)
        and prior_assembly.get("output_sha256") == sha256_path(output_assembly),
        "even-local output assembly changed before realignment",
    )
    require(
        prior_assembly.get("mutation_count") == len(c_symbols) and c_symbols,
        "realignment symbol count is invalid",
    )

    prior_adapter = nested_object(
        prior_audit, "adapter_audit", "prior alignment audit adapter"
    )
    require(
        prior_adapter.get("path") == str(adapter_audit_path)
        and prior_adapter.get("sha256") == sha256_bytes(adapter_payload),
        "realignment adapter audit changed",
    )

    prior_functions = nested_object(
        prior_audit, "function_alignment", "prior function alignment"
    )
    require(
        prior_functions.get("c_symbols") == c_symbols,
        "realignment C function set changed",
    )
    require(
        type(prior_functions.get("sdcc_symname_max")) is int
        and prior_functions.get("sdcc_symname_max") == SDCC_SYMNAME_MAX,
        "prior SDCC symbol-name limit changed",
    )
    require(
        type(prior_functions.get("asxxxx_ncps")) is int
        and prior_functions.get("asxxxx_ncps") == ASXXXX_NCPS,
        "prior ASxxxx symbol-buffer limit changed",
    )
    expected_records = function_alignment_records(c_symbols, ".even")
    require(
        prior_functions.get("records") == expected_records,
        "prior even-local assembly records changed",
    )

    relocation = nested_object(
        prior_audit, "relocation_verification", "prior relocation verification"
    )
    require(
        relocation.get("outcome") == "realign_required"
        and relocation.get("recommended_local_parity") == "odd",
        "prior relocation did not request odd-local realignment",
    )
    require(
        relocation.get("checked_symbol_count") == len(c_symbols)
        and relocation.get("zero_symbol_verified_noop") is False,
        "prior relocation symbol count changed",
    )
    relocation_records = relocation.get("records")
    require(
        isinstance(relocation_records, list)
        and len(relocation_records) == len(c_symbols),
        "prior relocation records are incomplete",
    )
    for expected, record in zip(expected_records, relocation_records):
        symbol = expected["c_symbol"]
        require(isinstance(record, dict), "prior relocation record is not an object")
        require(
            record.get("c_symbol") == symbol
            and record.get("assembly_label") == expected["assembly_label"]
            and record.get("asxxxx_symbol") == expected["asxxxx_symbol"]
            and record.get("even") is False
            and type(record.get("address")) is int
            and record["address"] % ALIGNMENT_BYTES == 1,
            f"prior relocation record is not uniformly odd: {symbol}",
        )
    listing_path_value = relocation.get("relocated_listing")
    require(
        isinstance(listing_path_value, str)
        and relocation.get("relocated_listing_sha256")
        == sha256_path(Path(listing_path_value)),
        "prior relocated listing changed before realignment",
    )


def align_assembly(
    input_assembly: Path,
    output_assembly: Path,
    adapter_audit_path: Path,
    target_profile: str,
    local_parity: str,
    audit_path: Path,
) -> dict[str, object]:
    """Generate assembly with a local parity directive at audited labels."""

    target_profile = validate_profile(target_profile)
    require(
        local_parity in SUPPORTED_LOCAL_PARITIES,
        f"unsupported local parity: {local_parity!r}",
    )
    directive = f".{local_parity}"
    input_assembly = input_assembly.resolve()
    output_assembly = output_assembly.resolve()
    adapter_audit_path = adapter_audit_path.resolve()
    audit_path = audit_path.resolve()
    require(
        len({input_assembly, output_assembly, adapter_audit_path, audit_path}) == 4,
        "input assembly, output assembly, adapter audit, and output audit must be distinct",
    )

    adapter_audit, adapter_payload = read_json(adapter_audit_path, "adapter audit")
    llvm_symbols, c_symbols, member_symbols = alignment_contract(
        adapter_audit, target_profile
    )
    records = function_alignment_records(c_symbols, directive)
    original = read_file(input_assembly, "raw SDCC assembly")
    text, newline = decode_assembly(original)
    require(MARKER_PREFIX not in text, "assembly was already processed by this helper")

    retry_evidence: dict[str, object] | None = None
    if audit_path.is_file():
        prior_audit, prior_payload = read_json(audit_path, "prior alignment audit")
        if prior_audit.get("outcome") == "realign_required":
            require(
                local_parity == "odd",
                "realign-required audit may only be retried with odd local parity",
            )
            validate_realign_request(
                prior_audit,
                input_assembly,
                output_assembly,
                adapter_audit_path,
                target_profile,
                original,
                adapter_payload,
                c_symbols,
            )
            retry_evidence = {
                "from_local_parity": "even",
                "prior_audit_sha256": sha256_bytes(prior_payload),
                "reason": "uniform-odd-final-addresses",
            }

    label_to_record = {
        f"{record['assembly_label']}:": record for record in records
    }
    lines = text.splitlines(keepends=True)
    positions: dict[str, list[int]] = {label: [] for label in label_to_record}
    for index, line in enumerate(lines):
        content = line[:-2] if line.endswith("\r\n") else (
            line[:-1] if line.endswith("\n") else line
        )
        if content in positions:
            positions[content].append(index)

    for label, indices in positions.items():
        require(
            len(indices) == 1,
            f"ASxxxx assembly label {label} occurs {len(indices)} times, expected once",
        )

    if c_symbols:
        output_lines: list[str] = []
        for line in lines:
            content = line[:-2] if line.endswith("\r\n") else (
                line[:-1] if line.endswith("\n") else line
            )
            record = label_to_record.get(content)
            if record is not None:
                marker_symbol = record["c_symbol"][:SDCC_SYMNAME_MAX]
                output_lines.append(f"{MARKER_PREFIX}{marker_symbol}{newline}")
                output_lines.append(f"\t{directive}{newline}")
            output_lines.append(line)
        aligned = "".join(output_lines).encode("utf-8")
    else:
        # A bridge with no align-2 definitions is valid, but it must be a true
        # byte-for-byte no-op rather than a rewrite hidden behind an empty set.
        aligned = original
    atomic_write(output_assembly, aligned)

    audit: dict[str, object] = {
        "alignment_bytes": ALIGNMENT_BYTES,
        "assembly": {
            "input_path": str(input_assembly),
            "input_sha256": sha256_bytes(original),
            "local_parity": local_parity,
            "mutation_count": len(c_symbols),
            "output_path": str(output_assembly),
            "output_sha256": sha256_bytes(aligned),
            "zero_symbol_byte_for_byte_noop": not c_symbols,
        },
        "adapter_audit": {
            "path": str(adapter_audit_path),
            "sha256": sha256_bytes(adapter_payload),
        },
        "directive": directive,
        "function_alignment": {
            "asxxxx_ncps": ASXXXX_NCPS,
            "c_symbols": c_symbols,
            "llvm_symbols": llvm_symbols,
            "member_function_symbols": member_symbols,
            "records": records,
            "sdcc_symname_max": SDCC_SYMNAME_MAX,
        },
        "outcome": "pass",
        "phase": "aligned",
        "policy": "asxxxx-local-parity-for-all-clang-align-2-functions",
        "relocation_verification": {"outcome": "not_run"},
        "schema_version": SCHEMA_VERSION,
        "target_profile": target_profile,
        "tool": {
            "path": str(Path(__file__).resolve()),
            "sha256": sha256_path(Path(__file__).resolve()),
        },
    }
    if retry_evidence is not None:
        audit["realignment"] = retry_evidence
    write_json(audit_path, audit)
    return audit


def validate_alignment_audit(
    audit: dict[str, object], target_profile: str
) -> tuple[dict[str, object], list[dict[str, str]]]:
    require(
        audit.get("schema_version") == SCHEMA_VERSION,
        "unsupported alignment audit schema",
    )
    require(audit.get("outcome") == "pass", "alignment audit did not pass")
    require(
        audit.get("phase") == "aligned",
        "alignment audit is not awaiting relocation",
    )
    require(
        audit.get("policy")
        == "asxxxx-local-parity-for-all-clang-align-2-functions",
        "unexpected alignment policy",
    )
    require(
        audit.get("alignment_bytes") == ALIGNMENT_BYTES,
        "unexpected alignment width",
    )
    require(
        audit.get("target_profile") == target_profile,
        "alignment audit target profile mismatch",
    )

    tool = nested_object(audit, "tool", "alignment audit tool")
    require(
        tool.get("path") == str(Path(__file__).resolve()),
        "alignment helper path mismatch",
    )
    require(
        tool.get("sha256") == sha256_path(Path(__file__).resolve()),
        "alignment helper hash mismatch",
    )

    adapter = nested_object(audit, "adapter_audit", "alignment audit adapter_audit")
    adapter_path_value = adapter.get("path")
    require(isinstance(adapter_path_value, str), "alignment audit adapter path is invalid")
    adapter_path = Path(adapter_path_value)
    require(adapter.get("sha256") == sha256_path(adapter_path), "adapter audit hash mismatch")
    current_adapter, _adapter_payload = read_json(adapter_path, "adapter audit")
    expected_llvm, expected_c, expected_members = alignment_contract(
        current_adapter, target_profile
    )

    assembly = nested_object(audit, "assembly", "alignment audit assembly")
    local_parity = assembly.get("local_parity")
    require(local_parity in SUPPORTED_LOCAL_PARITIES, "alignment audit local parity is invalid")
    directive = f".{local_parity}"
    require(audit.get("directive") == directive, "unexpected assembler directive")
    input_path_value = assembly.get("input_path")
    output_path_value = assembly.get("output_path")
    require(isinstance(input_path_value, str), "alignment audit input assembly path is invalid")
    require(isinstance(output_path_value, str), "alignment audit output assembly path is invalid")
    input_path = Path(input_path_value)
    output_path = Path(output_path_value)
    require(
        assembly.get("input_sha256") == sha256_path(input_path),
        "raw assembly hash mismatch",
    )
    require(
        assembly.get("output_sha256") == sha256_path(output_path),
        "aligned assembly hash mismatch",
    )

    functions = nested_object(audit, "function_alignment", "alignment audit function_alignment")
    llvm_symbols = validate_symbol_list(
        functions.get("llvm_symbols"), "alignment audit LLVM symbols", SAFE_LLVM_SYMBOL
    )
    c_symbols = validate_symbol_list(functions.get("c_symbols"), "alignment audit C symbols")
    member_symbols = validate_symbol_list(
        functions.get("member_function_symbols"),
        "alignment audit member symbols",
        SAFE_LLVM_SYMBOL,
    )
    require(llvm_symbols == expected_llvm, "alignment audit LLVM symbols changed")
    require(c_symbols == expected_c, "alignment audit C symbols changed")
    require(member_symbols == expected_members, "alignment audit member symbols changed")
    require(len(llvm_symbols) == len(c_symbols), "alignment audit LLVM/C symbol counts differ")
    require(
        not (set(member_symbols) - set(llvm_symbols)),
        "alignment audit member symbol is not align-2",
    )
    require(
        type(functions.get("sdcc_symname_max")) is int
        and functions.get("sdcc_symname_max") == SDCC_SYMNAME_MAX,
        "alignment audit SDCC symbol-name limit changed",
    )
    require(
        type(functions.get("asxxxx_ncps")) is int
        and functions.get("asxxxx_ncps") == ASXXXX_NCPS,
        "alignment audit ASxxxx symbol-buffer limit changed",
    )
    require(
        assembly.get("mutation_count") == len(c_symbols),
        "alignment mutation count mismatch",
    )
    require(
        assembly.get("zero_symbol_byte_for_byte_noop") is (not c_symbols),
        "zero-symbol no-op claim mismatch",
    )
    if not c_symbols:
        require(
            assembly.get("input_sha256") == assembly.get("output_sha256"),
            "zero-symbol alignment changed assembly bytes",
        )

    expected_records = function_alignment_records(c_symbols, directive)
    require(
        functions.get("records") == expected_records,
        "alignment records do not match C symbols",
    )
    require(
        audit.get("relocation_verification") == {"outcome": "not_run"},
        "alignment audit already has unexpected relocation state",
    )
    return assembly, expected_records


def verify_relocated_listing(
    audit_path: Path,
    relocated_listing: Path,
    target_profile: str,
) -> dict[str, object]:
    """Require every aligned function's final ASlink address to be even."""

    target_profile = validate_profile(target_profile)
    audit_path = audit_path.resolve()
    relocated_listing = relocated_listing.resolve()
    require(audit_path != relocated_listing, "audit and relocated-listing paths alias")
    audit, original_audit_payload = read_json(audit_path, "alignment audit")
    assembly, alignment_records = validate_alignment_audit(audit, target_profile)
    listing_payload = read_file(relocated_listing, "relocated ASxxxx listing")
    try:
        listing = listing_payload.decode("utf-8")
    except UnicodeDecodeError as error:
        fail(f"relocated listing is not UTF-8/ASCII: {error}")
    require("\x00" not in listing, "relocated listing contains a NUL byte")

    address_digits = 4 if target_profile == "mcs51" else 6
    maximum_address = (1 << (4 * address_digits)) - 1
    relocated_records: list[dict[str, object]] = []
    for alignment_record in alignment_records:
        symbol = alignment_record["c_symbol"]
        label = alignment_record["assembly_label"]
        pattern = re.compile(
            rf"^[ \t]*([0-9A-Fa-f]{{{address_digits},8}})[ \t]+[0-9]+[ \t]+"
            rf"{re.escape(label)}:[ \t]*$",
            re.MULTILINE,
        )
        matches = pattern.findall(listing)
        require(
            len(matches) == 1,
            f"relocated ASxxxx label {label} occurs {len(matches)} times, expected once",
        )
        address = int(matches[0], 16)
        require(
            address <= maximum_address,
            f"relocated address exceeds {target_profile} program width: {matches[0]}",
        )
        relocated_records.append(
            {
                "address": address,
                "address_hex": f"{address:0{address_digits}X}",
                "assembly_label": label,
                "asxxxx_symbol": alignment_record["asxxxx_symbol"],
                "c_symbol": symbol,
                "even": address % ALIGNMENT_BYTES == 0,
            }
        )

    parities = {record["address"] % ALIGNMENT_BYTES for record in relocated_records}
    if parities == {1} and assembly["local_parity"] == "even":
        outcome = "realign_required"
    else:
        require(
            not parities or parities == {0},
            "relocated function parity is mixed or remains odd after odd-local alignment",
        )
        outcome = "pass"

    audit["outcome"] = outcome
    audit["phase"] = "verified" if outcome == "pass" else "realign_required"
    audit["relocation_verification"] = {
        "alignment_audit_sha256_before_verification": sha256_bytes(original_audit_payload),
        "checked_symbol_count": len(alignment_records),
        "outcome": outcome,
        "records": relocated_records,
        "relocated_listing": str(relocated_listing),
        "relocated_listing_sha256": sha256_bytes(listing_payload),
        "recommended_local_parity": "odd" if outcome == "realign_required" else None,
        "zero_symbol_verified_noop": not alignment_records,
    }
    write_json(audit_path, audit)
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    align_parser = subparsers.add_parser("align", help="insert ASxxxx local-parity directives")
    align_parser.add_argument("--input-assembly", type=Path, required=True)
    align_parser.add_argument("--output-assembly", type=Path, required=True)
    align_parser.add_argument("--adapter-audit", type=Path, required=True)
    align_parser.add_argument("--target-profile", choices=SUPPORTED_PROFILES, required=True)
    align_parser.add_argument("--local-parity", choices=SUPPORTED_LOCAL_PARITIES, required=True)
    align_parser.add_argument("--audit-json", type=Path, required=True)

    verify_parser = subparsers.add_parser("verify", help="verify final relocated addresses")
    verify_parser.add_argument("--audit-json", type=Path, required=True)
    verify_parser.add_argument("--relocated-listing", type=Path, required=True)
    verify_parser.add_argument("--target-profile", choices=SUPPORTED_PROFILES, required=True)

    arguments = parser.parse_args()
    try:
        if arguments.command == "align":
            align_assembly(
                arguments.input_assembly,
                arguments.output_assembly,
                arguments.adapter_audit,
                arguments.target_profile,
                arguments.local_parity,
                arguments.audit_json,
            )
        else:
            audit = verify_relocated_listing(
                arguments.audit_json,
                arguments.relocated_listing,
                arguments.target_profile,
            )
            if audit["outcome"] == "realign_required":
                return EXIT_REALIGN_REQUIRED
    except (AlignmentError, OSError) as error:
        raise SystemExit(f"STC function alignment failed: {error}") from None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
