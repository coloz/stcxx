#!/usr/bin/env python3
"""Check MCS251 stack word folds across hexadecimal boundaries."""

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


def require_instruction(body, instruction, description):
    pattern = rf"^[ \t]*{instruction}[ \t]*$"
    if not re.search(pattern, body, re.MULTILINE | re.IGNORECASE):
        raise AssertionError(f"missing {description}:\n{body}")


def forbid_instruction(body, instruction, description):
    pattern = rf"^[ \t]*{instruction}[ \t]*$"
    if re.search(pattern, body, re.MULTILINE | re.IGNORECASE):
        raise AssertionError(f"found {description}:\n{body}")


def instruction_lines(body):
    return [
        re.sub(r"\s+", " ", line.strip().lower())
        for line in body.splitlines()
        if line.strip() and not line.lstrip().startswith(";")
    ]


def instruction_sequence_index(body, instructions, start=0):
    lines = instruction_lines(body)
    patterns = [re.compile(rf"^(?:{item})$", re.IGNORECASE) for item in instructions]
    for index in range(start, len(lines) - len(patterns) + 1):
        if all(
            pattern.fullmatch(lines[index + offset])
            for offset, pattern in enumerate(patterns)
        ):
            return index
    return -1


def require_instruction_sequence(body, instructions, description, start=0):
    index = instruction_sequence_index(body, instructions, start)
    if index < 0:
        raise AssertionError(f"missing {description}:\n{body}")
    return index


def forbid_instruction_sequence(body, instructions, description):
    if instruction_sequence_index(body, instructions) >= 0:
        raise AssertionError(f"found {description}:\n{body}")


def check_rule_definitions(rule_text):
    load_pairs = (("7", "6"), ("5", "4"), ("3", "2"), ("1", "0"))
    for high, low in load_pairs:
        load_rule = re.compile(
            rf"replace\s*\{{\s*"
            rf"mov\s+a,@spx-%1\s*"
            rf"mov\s+r{high},a\s*"
            rf"mov\s+a,@spx-%2\s*"
            rf"mov\s+r{low},a\s*"
            rf"\}}\s*by\s*\{{\s*"
            rf"mov\s+wr{low},@spx-%2\s*"
            rf"mov\s+a,r{low}\s*"
            rf"\}}\s*if\s+inSequence\('1'\s+%1\s+%2\)",
            re.IGNORECASE,
        )
        store_rule = re.compile(
            rf"replace\s*\{{\s*"
            rf"mov\s+a,r{high}\s*"
            rf"mov\s+@spx-%1,a\s*"
            rf"mov\s+a,r{low}\s*"
            rf"mov\s+@spx-%2,a\s*"
            rf"\}}\s*by\s*\{{\s*"
            rf"mov\s+@spx-%2,wr{low}\s*"
            rf"mov\s+a,r{low}\s*"
            rf"\}}\s*if\s+inSequence\('1'\s+%1\s+%2\)",
            re.IGNORECASE,
        )
        if not load_rule.search(rule_text):
            raise AssertionError(
                f"WR{low} stack-load rule does not retain the 0x prefix"
            )
        if not store_rule.search(rule_text):
            raise AssertionError(
                f"WR{low} stack-store rule does not retain the 0x prefix"
            )

    if "@spx-0x%1" in rule_text or "@spx-0x%2" in rule_text:
        raise AssertionError("a stack word rule still captures bare hex digits")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sdcc", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--printf-source", required=True)
    parser.add_argument("--peephole-source", required=True)
    parser.add_argument("--device-include", required=True)
    args = parser.parse_args()

    sdcc = Path(args.sdcc).resolve()
    source = Path(args.source).resolve()
    printf_source = Path(args.printf_source).resolve()
    peephole_source = Path(args.peephole_source).resolve()
    device_include = Path(args.device_include).resolve()
    for path in (
        sdcc,
        source,
        printf_source,
        peephole_source,
        device_include,
    ):
        if not path.exists():
            parser.error(f"required path does not exist: {path}")

    check_rule_definitions(peephole_source.read_text())

    with tempfile.TemporaryDirectory(
        prefix="sdcc-mcs251-stack-word-peephole-"
    ) as temporary:
        workspace = Path(temporary)
        probe_asm = workspace / "stack-word-peephole.asm"
        probe_rel = workspace / "stack-word-peephole.rel"
        probe_flags = [
            str(sdcc),
            "-mmcs251",
            "--model-small",
            "--stack-auto",
            "--peep-asm",
            "--fverbose-asm",
        ]
        run([*probe_flags, "-S", "-o", str(probe_asm), str(source)])
        run([*probe_flags, "-c", "-o", str(probe_rel), str(source)])
        probe_text = probe_asm.read_text()

        false_offsets = {
            "wr6": ("003f", "004f", "7", "6"),
            "wr4": ("0039", "0040", "5", "4"),
            "wr2": ("003f", "004f", "3", "2"),
            "wr0": ("0039", "0040", "1", "0"),
        }
        for word, (first, second, high, low) in false_offsets.items():
            load_body = function_body(probe_text, f"false_load_{word}")
            require_instruction_sequence(
                load_body,
                (
                    rf"mov\s+a,@spx-0x{first}",
                    rf"mov\s+r{high},a",
                    rf"mov\s+a,@spx-0x{second}",
                    rf"mov\s+r{low},a",
                ),
                f"complete {word} nonconsecutive byte-load sequence",
            )
            require_instruction(
                load_body,
                rf"mov\s+a,@spx-0x{first}",
                f"{word} first nonconsecutive byte load",
            )
            require_instruction(
                load_body,
                rf"mov\s+r{high},a",
                f"{word} first loaded register",
            )
            require_instruction(
                load_body,
                rf"mov\s+a,@spx-0x{second}",
                f"{word} second nonconsecutive byte load",
            )
            require_instruction(
                load_body,
                rf"mov\s+r{low},a",
                f"{word} second loaded register",
            )
            forbid_instruction(
                load_body,
                rf"mov\s+{word},@spx-0x[0-9a-f]+",
                f"false {word} stack-load fold",
            )

            store_body = function_body(probe_text, f"false_store_{word}")
            require_instruction_sequence(
                store_body,
                (
                    rf"mov\s+a,r{high}",
                    rf"mov\s+@spx-0x{first},a",
                    rf"mov\s+a,r{low}",
                    rf"mov\s+@spx-0x{second},a",
                ),
                f"complete {word} nonconsecutive byte-store sequence",
            )
            require_instruction(
                store_body,
                rf"mov\s+a,r{high}",
                f"{word} first nonconsecutive store source",
            )
            require_instruction(
                store_body,
                rf"mov\s+@spx-0x{first},a",
                f"{word} first nonconsecutive byte store",
            )
            require_instruction(
                store_body,
                rf"mov\s+a,r{low}",
                f"{word} second nonconsecutive store source",
            )
            require_instruction(
                store_body,
                rf"mov\s+@spx-0x{second},a",
                f"{word} second nonconsecutive byte store",
            )
            forbid_instruction(
                store_body,
                rf"mov\s+@spx-0x[0-9a-f]+,{word}",
                f"false {word} stack-store fold",
            )

        true_load = function_body(probe_text, "true_load_wr4")
        require_instruction(
            true_load,
            r"mov\s+wr4,@spx-0x0040",
            "true WR4 stack-load fold across 0x3f/0x40",
        )
        forbid_instruction_sequence(
            true_load,
            (
                r"mov\s+a,@spx-0x003f",
                r"mov\s+r5,a",
                r"mov\s+a,@spx-0x0040",
                r"mov\s+r4,a",
            ),
            "unfolded true WR4 byte-load sequence",
        )
        true_store = function_body(probe_text, "true_store_wr4")
        require_instruction_sequence(
            true_load,
            (r"mov\s+wr4,@spx-0x0040", r"mov\s+a,r4", r"xrl\s+a,#0x5a"),
            "stack load preserves live accumulator",
        )
        require_instruction_sequence(
            true_store,
            (r"mov\s+@spx-0x0040,wr4", r"mov\s+a,r4", r"xrl\s+a,#0x5a"),
            "stack store preserves live accumulator",
        )
        require_instruction(
            true_store,
            r"mov\s+@spx-0x0040,wr4",
            "true WR4 stack-store fold across 0x3f/0x40",
        )
        forbid_instruction_sequence(
            true_store,
            (
                r"mov\s+a,r5",
                r"mov\s+@spx-0x003f,a",
                r"mov\s+a,r4",
                r"mov\s+@spx-0x0040,a",
            ),
            "unfolded true WR4 byte-store sequence",
        )

        printf_asm = workspace / "printf-stack-auto.asm"
        run([
            str(sdcc),
            "-mmcs251",
            "--model-small",
            "--stack-auto",
            "--nostdinc",
            "--std=c23",
            "--fverbose-asm",
            f"-I{device_include}",
            f"-I{device_include / 'mcs51'}",
            "-S",
            "-o",
            str(printf_asm),
            str(printf_source),
        ])
        printf_text = printf_asm.read_text()
        case_start = printf_text.find("PTR = va_arg(ap,ptr_t)")
        case_end = printf_text.find("OUTPUT_CHAR('0', p)", case_start)
        if case_start < 0 or case_end < 0:
            raise AssertionError("could not locate printf %p assembly")
        pointer_case = printf_text[case_start:case_end]
        require_instruction(
            pointer_case,
            r"mov\s+wr6,@spx-0x003e",
            "valid printf local-address word load",
        )
        high_byte_index = require_instruction_sequence(
            pointer_case,
            (r"mov\s+a,@spx-0x003f", r"mov\s+r5,a"),
            "consecutive printf high-byte load into r5",
        )
        require_instruction_sequence(
            pointer_case,
            (r"mov\s+dpxl,r5",),
            "printf high byte transfer from r5 into dpxl",
            high_byte_index + 2,
        )
        forbid_instruction(
            pointer_case,
            r"mov\s+wr4,@spx-0x004f",
            "nonconsecutive printf stack-load fold",
        )

    print("PASS: MCS251 hexadecimal stack word peepholes")


if __name__ == "__main__":
    main()
