use crate::{driver::Driver, link::public_defs, util::*};
use anyhow::{Context, Result, bail, ensure};
use fancy_regex::{Captures, Regex as Fancy};
use regex::Regex;
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};

pub struct Adapted {
    pub c: String,
    pub aligned: BTreeSet<String>,
    pub audit: Value,
}
pub fn mangle(symbol: &str) -> String {
    let mut result = String::new();
    for b in symbol.bytes() {
        if b.is_ascii_alphanumeric() || b == b'_' {
            result.push(b as char);
        } else {
            result.push('_');
            result.push((b'A' + (b & 15)) as char);
            result.push((b'A' + (b >> 4)) as char);
            result.push('_');
        }
    }
    result
}
// Preserve byte offsets while hiding comments, character constants and strings.
pub fn mask(c: &str) -> String {
    let mut bytes = c.as_bytes().to_vec();
    let mut i = 0;
    while i < bytes.len() {
        let start = i;
        let b = bytes[i];
        if b == b'"' || b == b'\'' {
            i += 1;
            while i < bytes.len() {
                if bytes[i] == b'\\' {
                    i = (i + 2).min(bytes.len());
                } else if bytes[i] == b {
                    i += 1;
                    break;
                } else {
                    i += 1;
                }
            }
        } else if bytes[i..].starts_with(b"//") {
            while i < bytes.len() && bytes[i] != b'\n' {
                i += 1;
            }
        } else if bytes[i..].starts_with(b"/*") {
            i += 2;
            while i < bytes.len() && !bytes[i..].starts_with(b"*/") {
                i += 1;
            }
            i = (i + 2).min(bytes.len());
        } else {
            i += 1;
            continue;
        }
        for b in &mut bytes[start..i] {
            if *b != b'\n' && *b != b'\r' {
                *b = b' ';
            }
        }
    }
    String::from_utf8(bytes).unwrap()
}
fn sub<F>(source: &str, pattern: &str, mut replace: F) -> Result<String>
where
    F: FnMut(&Captures<'_>) -> Result<String>,
{
    let re = Fancy::new(pattern)?;
    let mut result = String::new();
    let mut previous = 0;
    for capture in re.captures_iter(source) {
        let capture = capture?;
        let m = capture.get(0).unwrap();
        result.push_str(&source[previous..m.start()]);
        result.push_str(&replace(&capture)?);
        previous = m.end();
    }
    result.push_str(&source[previous..]);
    Ok(result)
}
fn token(source: &str, old: &str, new: &str) -> Result<String> {
    let masked = mask(source);
    let mut out = source.to_owned();
    let re = Regex::new(&regex::escape(old))?;
    let matches: Vec<_> = re.find_iter(&masked).collect();
    for m in matches.into_iter().rev() {
        out.replace_range(m.range(), new);
    }
    Ok(out)
}

pub fn adapt(
    d: &Driver,
    ir: &str,
    raw: &str,
    roots: &BTreeSet<String>,
    storage: &Value,
) -> Result<Adapted> {
    d.check_target(ir)?;
    audit_ir(ir, roots)?;
    let marker = "\n/* Global Declarations */\n";
    ensure!(
        raw.matches(marker).count() == 1,
        "invalid CBE global marker"
    );
    let (prefix, payload) = raw.split_once(marker).unwrap();
    let mut c = payload.to_owned();
    let symbol_re = Regex::new(r#"@(?:"([^"]+)"|([A-Za-z0-9_.$-]+))\("#)?;
    let mut aligned = BTreeSet::new();
    let alignment = Regex::new(r"\balign\s+(\d+)\s*\{$")?;
    for line in ir.lines().filter(|l| l.starts_with("define ")) {
        if let Some(a) = alignment.captures(line) {
            ensure!(&a[1] == "2", "unsupported function alignment");
            let m = symbol_re
                .captures(line)
                .context("invalid LLVM function name")?;
            aligned.insert(mangle(m.get(1).or_else(|| m.get(2)).unwrap().as_str()));
        }
    }
    let mut observed = BTreeSet::new();
    for m in Regex::new(r"(?m)^[^;\n]*?\b([A-Za-z_][A-Za-z0-9_]*)\([^;\n]*\)[^;\n]*\b__FUNCTIONALIGN__\((\d+)\)[ \t]*;")?.captures_iter(&c){ensure!(&m[2]=="2"&&observed.insert(m[1].to_owned()),"invalid CBE function alignment");}
    ensure!(
        observed == aligned,
        "function alignment changed between IR and CBE"
    );
    let constructors = constructors(ir)?;
    let expected: Vec<_> = constructors.iter().map(|(_, n)| mangle(n)).collect();
    let actual: Vec<_> = Regex::new(
        r"(?m)^static\s+void\s+([A-Za-z_][A-Za-z0-9_]*)\(void\)[^;\n]*\b__ATTRIBUTE_CTOR__[ \t]*;",
    )?
    .captures_iter(&c)
    .map(|m| m[1].to_owned())
    .collect();
    ensure!(
        actual == expected,
        "constructor order changed between LLVM and CBE"
    );
    c = token(&c, " __ATTRIBUTE_CTOR__", "")?;
    c = token(&c, "__builtin_trap();", "stcxx_runtime_panic(5);")?;
    c = sort_typedefs(&c)?;
    c = helpers(&c)?;
    c = address_roundtrips(&c)?;
    c = aggregate_initializers(&c)?;
    c = const_declarations(&c)?;
    c = vtable_addresses(&c)?;
    c = mark_unused_parameters(&c)?;
    c = remove_unused_locals(&c)?;
    let constants: BTreeSet<_> =
        Regex::new(r"(?m)^static const struct [A-Za-z_]\w* ([A-Za-z_]\w*)\s*=")?
            .captures_iter(&c)
            .map(|m| m[1].to_owned())
            .collect();
    for symbol in &constants {
        c = token(
            &c,
            &format!("((uint8_t*)(&{symbol}))"),
            &format!("((const uint8_t*)(&{symbol}))"),
        )?;
    }
    c = sub(
        &c,
        r"(?m)^static (?:const )?struct [A-Za-z_]\w* [A-Za-z_]\w* = [^\n]+;$",
        |m| {
            sub(
                &m[0],
                r"\(\(void\*\)\(const void\*\)&([A-Za-z_]\w*)\)",
                |a| {
                    Ok(if constants.contains(&a[1]) {
                        format!("((void*)&{})", &a[1])
                    } else {
                        a[0].into()
                    })
                },
            )
        },
    )?;
    let union = "typedef union {\n  uint32_t Int32;\n  uint64_t Int64;\n  float Float;\n  double Double;\n} llvmBitCastUnion;";
    if Regex::new(r"\bDouble\b")?.find_iter(&mask(&c)).count() == 1 {
        c = c.replace(union, &union.replace("  double Double;\n", ""));
    }
    let mut preamble = String::from(
        "/* Generated by native stcxx. */\n#include <stddef.h>\n#include <stdint.h>\n#ifndef __cplusplus\ntypedef unsigned char bool;\n#endif\n#define __forceinline inline\n#define __ATTRIBUTE_WEAK__\n#define __MSVC_INLINE__\n#define __noreturn _Noreturn\n#define __ATTRIBUTELIST__(x)\n#define __FUNCTIONALIGN__(x)\n#define __attribute__(x)\n#define __builtin_expect(value, expected) (value)\n#define __builtin_unreachable() do { } while (0)\n",
    );
    if prefix.lines().any(|l| l == "#include <string.h>") {
        preamble.push_str("#include <string.h>\n");
    }
    for (name, definition) in [
        ("ConstantFloatTy", "typedef uint32_t ConstantFloatTy;"),
        ("ConstantDoubleTy", "typedef uint64_t ConstantDoubleTy;"),
    ] {
        if c.contains(name) {
            ensure!(
                prefix.contains(definition),
                "missing floating constant type"
            );
            preamble.push_str(definition);
            preamble.push('\n');
        }
    }
    for m in Regex::new(
        r"(?ms)^static __forceinline bool llvm_cbe_is_fpclass_f(?:32|64)\([^\n]*\) \{\n.*?^\}",
    )?
    .find_iter(prefix)
    {
        preamble.push_str(m.as_str());
        preamble.push('\n');
    }
    preamble.push_str(&fcmp_helpers(prefix)?);
    let mut bridge = format!(
        "\nextern int main(void);\nvoid __stcxx_bridge_require_core_main(void) {{ (void)main(); }}\nvoid __stcxx_bridge_require_abi(void) {{ {}(); }}\nuint16_t __stcxx_bridge_ctor_count(void) {{ return (uint16_t){}u; }}\nvoid __stcxx_bridge_invoke_ctor(uint16_t index) {{\n  switch (index) {{\n",
        d.abi,
        expected.len()
    );
    for (i, name) in expected.iter().enumerate() {
        bridge.push_str(&format!("  case {i}u: {name}(); return;\n"));
    }
    bridge.push_str("  default: stcxx_runtime_panic(2); return;\n  }\n}\n");
    c = preamble + marker + &c + &bridge;
    let (shortened, mapping) = shorten(&c, roots)?;
    for (old, new) in &mapping {
        if aligned.remove(old) {
            aligned.insert(new.clone());
        }
    }
    c = shortened;
    ensure!(
        c.contains(&format!("{}(void)", d.abi)) && c.contains("stcxx_runtime_panic("),
        "CBE runtime ABI missing"
    );
    let audit = json!({"schema_version":1,"driver":"stcxx-native","outcome":"pass","constructors":constructors,"aligned_functions":aligned,"identifier_mapping":mapping,"roots":roots,"native_storage":storage,"raw_c_sha256":digest(raw),"adapted_c_sha256":digest(&c)});
    Ok(Adapted { c, aligned, audit })
}
fn constructors(ir: &str) -> Result<Vec<(u32, String)>> {
    let mut entries = Vec::new();
    let count_pattern = Regex::new(r"appending\s+global\s+\[(\d+)\s+x\s+\{")?;
    let re = Regex::new(
        r#"\{\s*i32,\s*ptr,\s*ptr\s*\}\s*\{\s*i32\s+(\d+),\s*ptr\s+@(?:"([^"]+)"|([A-Za-z0-9_.$-]+)),\s*ptr\s+null\s*\}"#,
    )?;
    for line in ir.lines().filter(|l| l.starts_with("@llvm.global_ctors")) {
        let count = count_pattern
            .captures(line)
            .context("invalid constructor list")?[1]
            .parse::<usize>()?;
        for m in re.captures_iter(line) {
            entries.push((
                m[1].parse()?,
                m.get(2).or_else(|| m.get(3)).unwrap().as_str().into(),
            ));
        }
        ensure!(entries.len() == count, "unsupported constructor entry");
    }
    ensure!(
        entries.windows(2).all(|p| p[0].0 <= p[1].0),
        "unordered constructor priorities"
    );
    Ok(entries)
}
pub(crate) fn audit_stack_allocation(ir: &str) -> Result<()> {
    let masked = Regex::new(r#"c"(?:\\.|[^"\\])*"|;[^\n]*"#)?.replace_all(ir, "");
    ensure!(
        !Regex::new(r"@llvm\.(?:stacksave|stackrestore)\b|\balloca\b[^\n]*,\s*i[0-9]+\s+%")?
            .is_match(&masked),
        "variable-length stack allocation (C++ VLA/alloca) is not supported by the MCS251 CBE/SDCC backend; use fixed-size buffers or checked heap allocation"
    );
    Ok(())
}
pub(crate) fn audit_ir(ir: &str, roots: &BTreeSet<String>) -> Result<()> {
    ensure!(
        roots.is_subset(&public_defs(ir)?),
        "optimized IR lost C ABI roots"
    );
    audit_stack_allocation(ir)?;
    let masked = Regex::new(r#"c"(?:\\.|[^"\\])*"|;[^\n]*"#)?.replace_all(ir, "");
    for pattern in [
        r"\baddrspace\s*\(\s*[1-9]",
        r"@llvm\.global_dtors\b",
        r"@(?:__cxa_atexit|__cxa_thread_atexit|atexit|__cxa_guard_acquire|__cxa_guard_release|__cxa_guard_abort)\b",
        r"\bthread_local\b",
        r"\bcomdat\b",
        r"(?m)^\s*@[^\n=]+=[^\n]*\b(?:alias|ifunc)\s+",
        r"\b(?:call|invoke)\s+[^\n]*\basm\b",
        r"<\s*(?:vscale|[1-9][0-9]*\s+x\s+[^{}>]+>)",
        r"\b(?:atomicrmw|cmpxchg|fence|landingpad|invoke|resume|callbr|va_arg)\b",
    ] {
        ensure!(
            !Regex::new(pattern)?.is_match(&masked),
            "unsupported LLVM IR construct: {pattern}"
        );
    }
    let aggregate_return = Regex::new(r"^declare\s+(?:\{|%)")?;
    for line in ir.lines().filter(|l| l.starts_with("declare ")) {
        if !line.contains("@llvm.") {
            ensure!(
                !line.contains("byval(")
                    && !line.contains("sret(")
                    && !aggregate_return.is_match(line),
                "unsupported native aggregate ABI: {line}"
            );
        }
    }
    let destructor = Regex::new(r"@_Z.+D[012]Ev\(")?;
    let call = Regex::new(r"\bcall\s+")?;
    for m in Regex::new(r"(?ms)^define [^\n]+\{\n.*?^\}")?.find_iter(ir) {
        let body = m.as_str();
        if body.contains("@llvm.trap(") {
            let head = body.lines().next().unwrap();
            ensure!(
                destructor.is_match(head)
                    && call.find_iter(body).count() == 1
                    && body.contains("unreachable"),
                "trap outside audited abstract destructor"
            );
        }
    }
    Ok(())
}
fn sort_typedefs(c: &str) -> Result<String> {
    let a = "\n/* Function definitions */\n";
    let b = "\n/* Types Definitions */\n";
    let (prefix, rest) = c.split_once(a).context("missing function typedef marker")?;
    let (block, suffix) = rest.split_once(b).context("missing type marker")?;
    let re = Regex::new(r"^typedef\s+.+\s+l_fptr_(\d+)\([^;]*\);\s*$")?;
    let mut types = BTreeMap::new();
    for line in block.lines().filter(|l| !l.trim().is_empty()) {
        let m = re.captures(line).context("unsupported function typedef")?;
        ensure!(
            types.insert(m[1].parse::<u32>()?, line).is_none(),
            "duplicate function typedef"
        );
    }
    Ok(format!(
        "{prefix}{a}\n{}\n{b}{suffix}",
        types.values().copied().collect::<Vec<_>>().join("\n")
    ))
}
fn helpers(input: &str) -> Result<String> {
    let mut c = unsigned_saturating_subtract(input);
    c = sub(
        &c,
        r"(?m)^static __forceinline (.+?) (llvm_[A-Za-z0-9_]+)\(([^\n]*)\) \{\n  \1 r;\n  r = ([^;\n]+);\n  return r;\n\}$",
        |m| {
            Ok(format!(
                "static __forceinline {} {}({}) {{\n  {} r = {};\n  return r;\n}}",
                &m[1], &m[2], &m[3], &m[1], &m[4]
            ))
        },
    )?;
    c = sub(
        &c,
        r"(?m)^static __forceinline (struct [A-Za-z_]\w*) (llvm_ctor_\w+)\(([^\n]*)\) \{\n  \1 r;\n((?:  r\.field[0-9]+ = [A-Za-z_]\w*;\n)+)  return r;\n\}$",
        |m| {
            let names = m[3]
                .split(',')
                .map(|a| a.split_whitespace().last().unwrap_or(""))
                .collect::<Vec<_>>();
            let expected = names
                .iter()
                .enumerate()
                .map(|(i, n)| format!("  r.field{i} = {n};\n"))
                .collect::<String>();
            ensure!(
                m[4] == expected,
                "aggregate helper initialization order mismatch"
            );
            Ok(format!(
                "static __forceinline {} {}({}) {{\n  {} r = {{ {} }};\n  return r;\n}}",
                &m[1],
                &m[2],
                &m[3],
                &m[1],
                names.join(", ")
            ))
        },
    )?;
    c = sub(
        &c,
        r"(?m)^static __forceinline uint(8|16|32|64)_t (llvm_(add|sub|mul|and|or|xor)_u\1)\(uint\1_t a, uint\1_t b\) \{\n  uint\1_t r = a ([+*&|^\-]) b;\n  return r;\n\}$",
        |m| {
            let op = match &m[3] {
                "add" => "+",
                "sub" => "-",
                "mul" => "*",
                "and" => "&",
                "or" => "|",
                _ => "^",
            };
            ensure!(&m[4] == op, "binary helper mismatch");
            let t = format!("uint{}_t", &m[1]);
            Ok(format!(
                "#define {}(a, b) (({t})((({t})(a)) {op} (({t})(b))))",
                &m[2]
            ))
        },
    )?;
    for (ret, param) in [
        ("uint32_t", "int32_t"),
        ("unsigned _BitInt(24)", "signed _BitInt(24)"),
    ] {
        let old = format!(
            "static __forceinline {ret} llvm_neg_u24({param} a) {{\n  {ret} r = (-a;\n  return r;\n}}"
        );
        c=c.replace(&old,"static __forceinline uint32_t llvm_neg_u24(int32_t a) {\n  uint32_t r = (0UL - ((uint32_t)a & 16777215UL)) & 16777215UL;\n  return r;\n}");
    }
    c = sub(
        &c,
        r"(?m)^static __forceinline uint(8|16|32|64)_t (llvm_neg_u\1)\(int\1_t a\) \{\n  uint\1_t r = -a;\n  return r;\n\}",
        |m| {
            let bits = &m[1];
            let width = if bits == "64" { "64" } else { "32" };
            Ok(format!(
                "static __forceinline uint{bits}_t {}(int{bits}_t a) {{\n  uint{bits}_t r = (uint{bits}_t)((uint{width}_t)0 - (uint{width}_t)a);\n  return r;\n}}",
                &m[2]
            ))
        },
    )?;
    c = sub(
        &c,
        r"\bllvm_(udiv|urem)_u32\((_[0-9]+),\s*((?:0[xX][0-9A-Fa-f]+|[0-9]+)[uUlL]{0,2})\)",
        |m| {
            let literal = m[3].trim_end_matches(['u', 'U', 'l', 'L']);
            let n = if literal.starts_with("0x") || literal.starts_with("0X") {
                u64::from_str_radix(&literal[2..], 16)?
            } else {
                literal.parse()?
            };
            if n > 0 && n <= 0x80000000 && n.is_power_of_two() {
                Ok(if &m[1] == "udiv" {
                    format!("(((uint32_t){}) >> {}u)", &m[2], n.trailing_zeros())
                } else {
                    format!("(((uint32_t){}) & {}UL)", &m[2], n - 1)
                })
            } else {
                Ok(m[0].into())
            }
        },
    )?;
    c = sub(
        &c,
        r"\(\((l_fptr_[0-9]+)\*\)\(void\*\)(_[0-9]+|\(\(\(void\*\)\(uintptr_t\)_[0-9]+\)\))\)(?=\()",
        |m| Ok(format!("(({}*)(uintptr_t){})", &m[1], &m[2])),
    )?;
    c=c.replace("static __forceinline float llvm_OC_fabs_OC_f32(float a) {\n  float r = fabsf(a);\n  return r;\n}","static __forceinline float llvm_OC_fabs_OC_f32(float a) {\n  union { float value; uint32_t bits; } repr;\n  repr.value = a;\n  repr.bits &= 0x7fffffffUL;\n  return repr.value;\n}");
    Ok(c)
}

// CBE 20 emits the invalid token UINT0 for llvm.usub.sat. Repair only its
// complete, known helper definition; never define UINT0 globally or rewrite
// application identifiers, comments, literals, or another intrinsic's body.
fn unsigned_saturating_subtract(input: &str) -> String {
    let mut c = input.to_owned();
    for bits in [8, 16, 32, 64] {
        let helper = format!(
            "static __forceinline uint{bits}_t llvm_OC_usub_OC_sat_OC_i{bits}(uint{bits}_t a, uint{bits}_t b) {{\n  uint{bits}_t r;\n  r = (a < b) ? UINT0 : a - b;\n  return r;\n}}"
        );
        let masked = mask(&c);
        let positions: Vec<_> = masked.match_indices(&helper).map(|(i, _)| i).collect();
        let offset = helper.find("UINT0").unwrap();
        for position in positions.into_iter().rev() {
            c.replace_range(
                position + offset..position + offset + 5,
                &format!("((uint{bits}_t)0)"),
            );
        }
    }
    c
}
fn address_roundtrips(c: &str) -> Result<String> {
    let c = sub(
        c,
        r"(?m)^([ \t]*)(_[0-9]+\s*=\s*)\*\(([A-Za-z_][A-Za-z0-9_]*\*+)\)\(\(\(&\(\(\3\)(_[0-9]+)\)\[(.+)\]\)\)\);[ \t]*$",
        |m| {
            Ok(format!(
                "{}{}(({}){})[{}];",
                &m[1], &m[2], &m[3], &m[4], &m[5]
            ))
        },
    )?;
    let c = sub(
        &c,
        r"(?m)^([ \t]*)(_[0-9]+\s*=\s*)\*\((void\*\*|(?:u?int(?:8|16|32|64)_t|float|double)\*)\)\(\(\(&\(\(uint8_t\*\)(_[0-9]+)\)\[(.+)\]\)\)\);[ \t]*$",
        |m| {
            Ok(format!(
                "{}{}*({})(((uint8_t*){}) + ({}));",
                &m[1], &m[2], &m[3], &m[4], &m[5]
            ))
        },
    )?;
    let c = sub(
        &c,
        r"(?m)^([ \t]*)\*\((void\*\*|(?:u?int(?:8|16|32|64)_t|float|double)\*)\)\(\(\(&\(\(uint8_t\*\)(_[0-9]+)\)\[(.+)\]\)\)\)\s*=\s*(.+);[ \t]*$",
        |m| {
            Ok(format!(
                "{}*({})(((uint8_t*){}) + ({})) = {};",
                &m[1], &m[2], &m[3], &m[4], &m[5]
            ))
        },
    )?;
    sub(
        &c,
        r"(?m)^([ \t]*)\*\(([A-Za-z_][A-Za-z0-9_]*\*+)\)\(\(\(&\(\(\2\)(_[0-9]+)\)\[(.+)\]\)\)\)\s*=\s*(.+);[ \t]*$",
        |m| {
            Ok(format!(
                "{}(({}){})[{}] = {};",
                &m[1], &m[2], &m[3], &m[4], &m[5]
            ))
        },
    )
}
fn const_declarations(c: &str) -> Result<String> {
    let marker = "\n/* Global Variable Declarations */\n";
    let end = "\n/* Function Declarations */\n";
    let (before, rest) = c
        .split_once(marker)
        .context("missing global declarations")?;
    let (decl, after) = rest
        .split_once(end)
        .context("missing function declarations")?;
    let decl = sub(
        decl,
        r"(?m)^const\s+static\s+.+\s+([A-Za-z_]\w*)\s*;[ \t]*$",
        |m| {
            ensure!(
                Regex::new(&format!(
                    r"(?m)^static\s+const\s+.+\s+{}(?:\s*=\s*.+)?;",
                    regex::escape(&m[1])
                ))?
                .is_match(after),
                "const declaration has no definition"
            );
            Ok(String::new())
        },
    )?;
    Ok(format!("{before}{marker}{decl}{end}{after}"))
}
type Types = BTreeMap<String, Vec<(String, Option<usize>)>>;
fn types(c: &str) -> Result<Types> {
    let mut types = Types::new();
    let fields = Regex::new(r"^\s*(.+?)\s+(?:array|field[0-9]+)(?:\[([0-9]+)\])?;\s*$")?;
    for m in Regex::new(r"(?ms)^struct (\w+) \{\n(.*?)^\};")?.captures_iter(c) {
        let mut entry = Vec::new();
        for line in m[2].lines() {
            if let Some(f) = fields.captures(line) {
                entry.push((
                    f[1].into(),
                    f.get(2).map(|m| m.as_str().parse()).transpose()?,
                ));
            } else {
                entry.clear();
                break;
            }
        }
        if !entry.is_empty() {
            ensure!(
                types.insert(m[1].into(), entry).is_none(),
                "duplicate aggregate type"
            );
        }
    }
    Ok(types)
}
fn split_items(value: &str) -> Result<Vec<String>> {
    let value = value.trim();
    ensure!(
        value.starts_with('{') && value.ends_with('}'),
        "expected C aggregate initializer: {value}"
    );
    let content = &value[1..value.len() - 1];
    let masked = mask(content);
    let mut depth = 0i32;
    let mut start = 0;
    let mut parts = Vec::new();
    for (i, b) in masked.bytes().enumerate() {
        match b {
            b'{' | b'(' | b'[' => depth += 1,
            b'}' | b')' | b']' => depth -= 1,
            b',' if depth == 0 => {
                parts.push(content[start..i].trim().into());
                start = i + 1;
            }
            _ => {}
        }
        ensure!(depth >= 0, "unbalanced initializer");
    }
    ensure!(depth == 0, "unbalanced initializer");
    if !content[start..].trim().is_empty() {
        parts.push(content[start..].trim().into());
    }
    Ok(parts)
}
fn bytes_literal(value: &str) -> Result<Vec<u8>> {
    ensure!(
        value.starts_with('"') && value.ends_with('"'),
        "invalid byte string"
    );
    let b = &value.as_bytes()[1..value.len() - 1];
    let mut out = Vec::new();
    let mut i = 0;
    while i < b.len() {
        let x = b[i];
        i += 1;
        if x != b'\\' {
            out.push(x);
            continue;
        }
        ensure!(i < b.len(), "truncated C escape");
        let x = b[i];
        i += 1;
        let value = match x {
            b'a' => 7,
            b'b' => 8,
            b'f' => 12,
            b'n' => 10,
            b'r' => 13,
            b't' => 9,
            b'v' => 11,
            b'\\' | b'\'' | b'"' | b'?' => x,
            b'0'..=b'7' => {
                let mut v = (x - b'0') as u16;
                let mut n = 1;
                while n < 3 && i < b.len() && (b'0'..=b'7').contains(&b[i]) {
                    v = v * 8 + (b[i] - b'0') as u16;
                    i += 1;
                    n += 1;
                }
                ensure!(v <= 255, "octal byte overflow");
                v as u8
            }
            b'x' => {
                let start = i;
                while i < b.len() && b[i].is_ascii_hexdigit() {
                    i += 1;
                }
                ensure!(i > start, "empty hex escape");
                u8::from_str_radix(std::str::from_utf8(&b[start..i])?, 16)?
            }
            _ => bail!("unsupported byte escape"),
        };
        out.push(value);
    }
    Ok(out)
}
fn zero(ty: &str, count: Option<usize>, types: &Types, depth: usize) -> Result<String> {
    ensure!(depth < 64, "cyclic aggregate type");
    if let Some(n) = count {
        ensure!(n > 0, "zero-sized array");
        return Ok(format!("{{ {} }}", zero(ty, None, types, depth + 1)?));
    }
    if let Some(name) = ty.strip_prefix("struct ") {
        let fields = types.get(name).context("unknown aggregate type")?;
        let children = fields
            .iter()
            .map(|(t, n)| zero(t, *n, types, depth + 1))
            .collect::<Result<Vec<_>>>()?;
        return Ok(format!("{{ {} }}", children.join(", ")));
    }
    Ok("0".into())
}
fn initializer(
    ty: &str,
    count: Option<usize>,
    value: &str,
    types: &Types,
    depth: usize,
) -> Result<(String, bool)> {
    ensure!(depth < 64, "aggregate nesting overflow");
    if let Some(n) = count
        && value.starts_with('"')
    {
        ensure!(ty == "uint8_t", "non-byte string initializer");
        let bytes = bytes_literal(value)?;
        ensure!(
            bytes.len() == n || bytes.len() + 1 == n,
            "byte initializer extent mismatch"
        );
        if bytes.len() == n {
            return Ok((
                format!(
                    "{{ {} }}",
                    bytes
                        .iter()
                        .map(|b| format!("{b}u"))
                        .collect::<Vec<_>>()
                        .join(", ")
                ),
                true,
            ));
        }
        return Ok((value.into(), false));
    }
    let fields = if let Some(n) = count {
        vec![(ty.to_owned(), None); n]
    } else if let Some(name) = ty.strip_prefix("struct ") {
        types
            .get(name)
            .context("unknown initialized aggregate")?
            .clone()
    } else {
        return Ok((value.into(), false));
    };
    let items = split_items(value)?;
    ensure!(
        !items.is_empty() && items.len() <= fields.len(),
        "aggregate initializer extent mismatch"
    );
    let mut children = Vec::new();
    let mut changed = false;
    for (item, (ty, n)) in items.iter().zip(fields) {
        let (v, c) = initializer(&ty, n, item, types, depth + 1)?;
        children.push(v);
        changed |= c;
    }
    Ok((
        if changed {
            format!("{{ {} }}", children.join(", "))
        } else {
            value.into()
        },
        changed,
    ))
}
fn aggregate_initializers(c: &str) -> Result<String> {
    let types = types(c)?;
    let marker = "\n/* Global Variable Definitions and Initialization */\n";
    let end = "\n/* LLVM Intrinsic Builtin Function Bodies */\n";
    let (before, rest) = c
        .split_once(marker)
        .context("missing global definition marker")?;
    let (defs, after) = rest.split_once(end).context("missing intrinsic marker")?;
    let defs = sub(
        defs,
        r"(?m)^((?:static )?(?:const )?)struct (\w+) (\w+)(?: = (.*))?;$",
        |m| {
            if !types.contains_key(&m[2]) {
                return Ok(m[0].into());
            }
            let ty = format!("struct {}", &m[2]);
            let (v, changed) = if let Some(v) = m.get(4) {
                initializer(&ty, None, v.as_str(), &types, 0)?
            } else if m[1].contains("const ") {
                (zero(&ty, None, &types, 0)?, true)
            } else {
                return Ok(m[0].into());
            };
            Ok(if changed {
                format!("{}struct {} {} = {};", &m[1], &m[2], &m[3], v)
            } else {
                m[0].into()
            })
        },
    )?;
    Ok(format!("{before}{marker}{defs}{end}{after}"))
}
fn vtable_addresses(c: &str) -> Result<String> {
    let types = types(c)?;
    let tables: BTreeMap<_, _> = Regex::new(r"(?m)^static const struct (\w+) (_ZT[VTC]\w+)\s*=")?
        .captures_iter(c)
        .map(|m| (m[2].to_owned(), m[1].to_owned()))
        .collect();
    let offset = |name: &str, field: usize, index: usize| -> Result<usize> {
        let fields = types
            .get(tables.get(name).context("unknown vtable")?)
            .context("missing vtable type")?;
        ensure!(field < fields.len(), "vtable field out of range");
        let mut bytes = 0;
        for (i, (ty, _)) in fields.iter().enumerate().take(field + 1) {
            let wrapper = types
                .get(
                    ty.strip_prefix("struct ")
                        .context("non-array vtable field")?,
                )
                .context("unknown vtable wrapper")?;
            ensure!(
                wrapper.len() == 1 && wrapper[0].0 == "void*",
                "vtable field not pointer array"
            );
            let n = wrapper[0].1.context("vtable array missing extent")?;
            if i == field {
                ensure!(index <= n, "vtable address out of range");
                bytes += 3 * index;
            } else {
                bytes += 3 * n;
            }
        }
        Ok(bytes)
    };
    let code = mask(c);
    let c = sub(
        c,
        r"\(\(\(&\(&\(&(_ZT[VC][A-Za-z0-9_]+)\)->field([0-9]+)\)->array\[\(\(int32_t\)([0-9]+)\)\]\)\)\)",
        |m| {
            if code[m.get(0).unwrap().range()] != m[0] {
                return Ok(m[0].into());
            }
            Ok(format!(
                "((void *)((const uint8_t __code *)&{} + {}))",
                &m[1],
                offset(&m[1], m[2].parse()?, m[3].parse()?)?
            ))
        },
    )?;
    // GlobalOpt can fold a constructor's vptr store into a byte-offset GEP
    // initializer. SDCC cannot treat CBE's _BitInt(24) subscript as a link-time
    // constant. Keep the same CODE address, validating it against the complete
    // table's 24-bit pointer layout before rewriting this exact CBE shape.
    let code = mask(&c);
    sub(
        &c,
        r"\(\(\(&\(\(uint8_t\*\)\(\(void\*\)(?:\(const void\*\))?&(_ZT[VTC][A-Za-z0-9_]+)\)\)\[\(\(signed _BitInt\(24\)\)([0-9]+)\)\]\)\)\)",
        |m| {
            if code[m.get(0).unwrap().range()] != m[0] {
                return Ok(m[0].into());
            }
            let fields = types
                .get(tables.get(&m[1]).context("unknown byte-offset vtable")?)
                .context("missing byte-offset vtable type")?;
            let last = fields.len().checked_sub(1).context("empty vtable")?;
            let wrapper = types
                .get(
                    fields[last]
                        .0
                        .strip_prefix("struct ")
                        .context("non-array vtable")?,
                )
                .context("missing vtable wrapper")?;
            let count = wrapper
                .first()
                .and_then(|f| f.1)
                .context("missing vtable extent")?;
            let bytes: usize = m[2].parse()?;
            ensure!(
                bytes <= offset(&m[1], last, count)?,
                "vtable byte address out of range"
            );
            Ok(format!(
                "((void *)((const uint8_t __code *)&{} + {bytes}))",
                &m[1]
            ))
        },
    )
}
fn shorten(c: &str, roots: &BTreeSet<String>) -> Result<(String, BTreeMap<String, String>)> {
    let masked = mask(c);
    let re = Regex::new(r"\b[A-Za-z_][A-Za-z0-9_]*\b")?;
    let mut mapping = BTreeMap::new();
    for m in re.find_iter(&masked) {
        if m.len() > 254 {
            ensure!(
                !roots.contains(m.as_str()),
                "external C identifier exceeds ASxxxx limit"
            );
            mapping.entry(m.as_str().to_owned()).or_insert_with(|| {
                format!(
                    "stcxx_cbe_id_{}",
                    digest(format!("stcxx-cbe-c-identifier-v1\0{}", m.as_str()))
                )
            });
        }
    }
    let mut out = c.to_owned();
    for m in re.find_iter(&masked).collect::<Vec<_>>().into_iter().rev() {
        if let Some(new) = mapping.get(m.as_str()) {
            out.replace_range(m.range(), new);
        }
    }
    Ok((out, mapping))
}
fn fcmp_helpers(prefix: &str) -> Result<String> {
    let conditions = BTreeMap::from([
        ("false", "0"),
        ("true", "1"),
        ("oeq", "r == 0"),
        ("ogt", "r == 1"),
        ("oge", "r == 0 || r == 1"),
        ("olt", "r == -1"),
        ("ole", "r <= 0"),
        ("one", "r != 0 && r != 2"),
        ("ord", "r != 2"),
        ("ueq", "r == 0 || r == 2"),
        ("ugt", "r > 0"),
        ("uge", "r >= 0"),
        ("ult", "r == -1 || r == 2"),
        ("ule", "r != 1"),
        ("une", "r != 0"),
        ("uno", "r == 2"),
    ]);
    let mut result = String::new();
    for m in Regex::new(r"(?m)^static __forceinline int llvm_fcmp_([a-z]+)\(double X, double Y\) \{ return [^\n{}]+; \}$")?.captures_iter(prefix){let condition=conditions.get(&m[1]).context("unknown floating predicate")?;result.push_str(&format!("static __forceinline int llvm_fcmp_{}(float X, float Y) {{ int r = __stcxx_fcmp_order32(X, Y); return {condition}; }}\n",&m[1]));}
    if !result.is_empty() {
        result = String::from(
            "static __forceinline int __stcxx_fcmp_order32(float X, float Y) {\n  union { float f; uint32_t u; } a, b;\n  uint32_t ax, bx;\n  a.f = X; b.f = Y; ax = a.u; bx = b.u;\n  if ((ax & 0x7fffffffUL) > 0x7f800000UL || (bx & 0x7fffffffUL) > 0x7f800000UL) return 2;\n  if (((ax | bx) & 0x7fffffffUL) == 0 || ax == bx) return 0;\n  if ((ax ^ bx) & 0x80000000UL) return (ax & 0x80000000UL) ? -1 : 1;\n  if (ax & 0x80000000UL) return ax > bx ? -1 : 1;\n  return ax < bx ? -1 : 1;\n}\n",
        ) + &result;
    }
    Ok(result)
}
pub fn audit_warnings(log: &str, c: &str) -> Result<()> {
    let warning = Regex::new(r"warning (\d+):\s*(.*)$")?;
    let uninitialized = Regex::new(r"(?m)^\s*(?:[A-Za-z_]\w*\s+)+(?:\*+\s*)?r\s*;\s*$")?;
    for line in log.lines().filter(|l| l.to_lowercase().contains("warning")) {
        if [
            "<command-line>: warning: \"__has_builtin\" redefined",
            "<command-line>: warning: \"__STDC_HOSTED__\" redefined",
        ]
        .contains(&line)
        {
            continue;
        }
        let m = warning
            .captures(line)
            .with_context(|| format!("unparsed compiler warning: {line}"))?;
        let code = m[1].parse::<u32>()?;
        let accepted = code == 84
            && m[2] == *"'auto' variable 'r' may be used before initialization"
            && !uninitialized
                .find_iter(&mask(c))
                .any(|m| !m.as_str().trim().starts_with("return "))
            || code == 85 && verified_unused(&m[2], c)?;
        ensure!(accepted, "unqualified bridge compiler warning: {line}");
    }
    Ok(())
}
// LLVM can retain an unused argument for an address-taken/virtual function's ABI.
// Mark only CBE-generated parameters that have no references in the body. This
// preserves the signature and avoids manufacturing SDCC warning 85 from valid IR.
fn mark_unused_parameters(c: &str) -> Result<String> {
    let function = Regex::new(r"(?ms)^[^\n;{}]+\b[A-Za-z_]\w*\(([^\n]*)\) \{\n.*?^\}")?;
    let parameter = Regex::new(r"\b(_[0-9]+)\s*$")?;
    let masked = mask(c);
    let mut result = c.to_owned();
    let functions: Vec<_> = function.captures_iter(&masked).collect();
    for capture in functions.into_iter().rev() {
        let definition = capture.get(0).unwrap();
        let mut uses = String::new();
        for argument in capture[1].split(',') {
            if let Some(name) = parameter.captures(argument) {
                let symbol = &name[1];
                let references = Regex::new(&format!(r"\b{symbol}\b"))?;
                if references.find_iter(definition.as_str()).count() == 1 {
                    uses.push_str(&format!("  (void){symbol};\n"));
                }
            }
        }
        let start = definition.start() + definition.as_str().find('{').unwrap() + 2;
        result.insert_str(start, &uses);
    }
    Ok(result)
}

// CBE can retain a dead alloca after optimization. Remove only an uninitialized
// generated scalar/struct declaration whose identifier has no other occurrence
// in its function. Preserve volatile objects, initializers and array bounds.
fn remove_unused_locals(c: &str) -> Result<String> {
    let function = Regex::new(r"(?ms)^[^\n;{}]+\b[A-Za-z_]\w*\([^\n]*\) \{\n.*?^\}")?;
    let local = Regex::new(
        r"(?m)^  (?:struct [A-Za-z_]\w*|u?int(?:8|16|24|32|64)_t|float|double|bool)(?:\s*\*)*\s+(_[0-9]+);[^\S\n]*$",
    )?;
    let masked = mask(c);
    let mut edits = Vec::new();
    for definition in function.find_iter(&masked) {
        for declaration in local.captures_iter(definition.as_str()) {
            let references = Regex::new(&format!(r"\b{}\b", &declaration[1]))?;
            if references.find_iter(definition.as_str()).count() == 1 {
                let span = declaration.get(0).unwrap();
                edits.push((
                    definition.start() + span.start(),
                    definition.start() + span.end(),
                ));
            }
        }
    }
    let mut result = c.to_owned();
    for (start, end) in edits.into_iter().rev() {
        result.replace_range(start..end, "");
    }
    Ok(result)
}

fn verified_unused(message: &str, c: &str) -> Result<bool> {
    let warning = Regex::new(
        r"^in function ([A-Za-z_]\w*) unreferenced (?:local variable|function parameter) : '(_[0-9]+)'$",
    )?;
    let Some(m) = warning.captures(message) else {
        return Ok(false);
    };
    let function = Regex::new(&format!(
        r"(?ms)^(?:static )?[^\n;{{}}]+\b{}\([^\n]*\) \{{\n.*?^\}}",
        regex::escape(&m[1])
    ))?;
    let functions: Vec<_> = function.find_iter(c).collect();
    if functions.len() != 1 {
        return Ok(false);
    }
    let body = mask(functions[0].as_str());
    let variable = Regex::new(&format!(r"\b{}\b", regex::escape(&m[2])))?;
    if variable.find_iter(&body).count() != 1 {
        return Ok(false);
    }
    if message.contains("function parameter") {
        return Ok(variable.is_match(body.lines().next().unwrap_or("")));
    }
    Ok(Regex::new(&format!(r"(?m)^\s*(?:struct\s+[A-Za-z_]\w*\s+|[A-Za-z_]\w*(?:\s+|\s*\*+\s*)){}(?:\s*\[[^\]]+\])?\s*;\s*$",regex::escape(&m[2])))?.is_match(&body))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn dynamic_stack_allocation_is_rejected_before_cbe() {
        let roots = BTreeSet::new();
        for ir in [
            "%p = alloca i8, i24 %size, align 1",
            "%p = alloca {i8, i32}, i32 %count, align 1",
            "%p = call ptr @llvm.stacksave.p0()",
            "call void @llvm.stackrestore.p0(ptr %saved)",
        ] {
            let message = audit_ir(ir, &roots).unwrap_err().to_string();
            assert!(message.contains("variable-length stack allocation"));
        }
        for ir in [
            "%p = alloca [128 x i8], align 1",
            "%p = alloca i8, i24 128, align 1",
            "; call ptr @llvm.stacksave.p0()\n%p = alloca i32, align 1",
            "@s = private constant [18 x i8] c\"@llvm.stacksave.p0\"",
        ] {
            audit_ir(ir, &roots).unwrap();
        }
    }
    #[test]
    fn cbe_unsigned_saturation_repairs_only_known_helper() {
        for bits in [8, 16, 32, 64] {
            let helper = format!(
                "static __forceinline uint{bits}_t llvm_OC_usub_OC_sat_OC_i{bits}(uint{bits}_t a, uint{bits}_t b) {{\n  uint{bits}_t r;\n  r = (a < b) ? UINT0 : a - b;\n  return r;\n}}"
            );
            let repaired = unsigned_saturating_subtract(&helper);
            assert_eq!(
                repaired,
                helper.replace("UINT0", &format!("((uint{bits}_t)0)"))
            );
            assert_eq!(unsigned_saturating_subtract(&repaired), repaired);
            for unrelated in [
                format!("/* {helper} */"),
                format!("const char *s = \"{}\";", helper.replace('\n', "\\n")),
                helper.replace("usub", "uadd"),
                helper.replace("a - b", "a + b"),
                helper.replace("uint", "int"),
                "int UINT0 = 3;".into(),
            ] {
                assert_eq!(unsigned_saturating_subtract(&unrelated), unrelated);
            }
        }
    }
    #[test]
    fn byte_offset_vtable_initializers_keep_code_address_and_bounds() {
        let declarations = "struct pointers {\n  void* array[9];\n};\nstruct table {\n  struct pointers field0;\n};\nstatic const struct table _ZTV4File = { { { 0 } } };\n";
        let expression = "(((&((uint8_t*)((void*)&_ZTV4File))[((signed _BitInt(24))6)])))";
        let c = format!("{declarations}void *object = {expression};\n");
        let rewritten = vtable_addresses(&c).unwrap();
        assert!(rewritten.contains("((void *)((const uint8_t __code *)&_ZTV4File + 6))"));
        assert!(!rewritten.contains("_BitInt"));
        assert_eq!(vtable_addresses(&rewritten).unwrap(), rewritten);
        // Raw CBE still has the const-preserving cast; it is simplified later
        // in adapt(), after the vtable-address pass has run.
        assert_eq!(
            vtable_addresses(&c.replace("((void*)&", "((void*)(const void*)&")).unwrap(),
            rewritten
        );
        assert!(vtable_addresses(&c.replace(")6)]", ")28)]")).is_err());
        assert!(vtable_addresses(&c.replace("array[9]", "array[1]")).is_err());
        assert!(vtable_addresses(&c.replace("void* array", "uint8_t array")).is_err());
        let literal =
            format!("{declarations}const char *message = \"{expression}\";\n/* {expression} */\n");
        assert_eq!(vtable_addresses(&literal).unwrap(), literal);
    }
    #[test]
    fn preserve_literals() {
        assert_eq!(
            token("a /* a */ \"a\" 'a' a", "a", "b").unwrap(),
            "b /* a */ \"a\" 'a' b"
        );
    }
    #[test]
    fn unused_virtual_parameters_preserve_signature_and_real_references() {
        let c = "static uint16_t method(void* _16, uint16_t _17) {\n  /* _16 is the unused this pointer. */\n  return _17;\n}\nuint16_t used(void* _18) {\n  return *(uint16_t*)_18;\n}\n";
        let marked = mark_unused_parameters(c).unwrap();
        assert!(marked.contains("method(void* _16, uint16_t _17) {\n  (void)_16;"));
        assert!(!marked.contains("(void)_17;") && !marked.contains("(void)_18;"));
        assert_eq!(mark_unused_parameters(&marked).unwrap(), marked);
    }
    #[test]
    fn exact_width_bytes() {
        assert_eq!(bytes_literal(r#""A\000\xff""#).unwrap(), vec![65, 0, 255]);
        assert!(bytes_literal(r#""\x100""#).is_err());
    }
    #[test]
    fn unused_generated_locals_preserve_effects_and_references() {
        let c = "void f(void) {\n  struct Empty _1; /* dead alloca */\n  uint16_t _2;\n  volatile uint16_t _3;\n  uint16_t _4 = effect();\n  uint8_t _5[effect()];\n  consume(_2);\n}\n";
        let reduced = remove_unused_locals(c).unwrap();
        assert!(!reduced.contains("struct Empty _1"));
        for name in ["_2", "_3", "_4", "_5"] {
            assert!(reduced.contains(name));
        }
        assert_eq!(remove_unused_locals(&reduced).unwrap(), reduced);
    }
    #[test]
    fn constructor_shape() {
        let ir = "@llvm.global_ctors = appending global [1 x { i32, ptr, ptr }] [{ i32, ptr, ptr } { i32 65535, ptr @_GLOBAL__sub_I_a.cpp, ptr null }]";
        assert_eq!(
            constructors(ir).unwrap(),
            vec![(65535, "_GLOBAL__sub_I_a.cpp".into())]
        );
    }
}
