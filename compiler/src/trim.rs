//! Recompile the reachable native C source closure. Clang supplies source ranges;
//! SDCC still owns code generation and storage. No REL relocations are rewritten.
use crate::{
    driver::Driver,
    link::{archive_members, rel_symbols},
    util::*,
};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
};

#[derive(Clone)]
struct Decl {
    name: String,
    body: Option<(usize, usize)>,
    range: (usize, usize),
    refs: BTreeSet<String>,
    external: bool,
    removable_data: bool,
}
struct Unit {
    original: PathBuf,
    source: PathBuf,
    bytes: Vec<u8>,
    meta: Value,
    decls: Vec<Decl>,
    selected: BTreeSet<String>,
}
fn refs(node: &Value, out: &mut BTreeSet<String>) {
    if let Some(name) = node["referencedDecl"]["name"].as_str() {
        out.insert(name.into());
    }
    if let Some(children) = node["inner"].as_array() {
        for child in children {
            refs(child, out);
        }
    }
}
fn has_assembly(node: &Value) -> bool {
    matches!(
        node["kind"].as_str(),
        Some("GCCAsmStmt" | "MSAsmStmt" | "FileScopeAsmDecl")
    ) || node["inner"]
        .as_array()
        .is_some_and(|a| a.iter().any(has_assembly))
}
fn location(node: &Value, source: &Path, size: usize) -> Result<Option<(usize, usize)>> {
    if node.get("spellingLoc").is_some() || node.get("expansionLoc").is_some() {
        return Ok(None);
    }
    if node.get("includedFrom").is_some() {
        return Ok(None);
    }
    if let Some(file) = node["file"].as_str()
        && absolute(file)? != source
    {
        return Ok(None);
    }
    let Some(start) = node["offset"].as_u64() else {
        return Ok(None);
    };
    let len = node["tokLen"].as_u64().unwrap_or(0);
    ensure!(
        start + len <= size as u64,
        "Clang source range exceeds source"
    );
    Ok(Some((start as usize, len as usize)))
}
fn range(node: &Value, source: &Path, size: usize) -> Result<Option<(usize, usize)>> {
    let Some((begin, _)) = location(&node["range"]["begin"], source, size)? else {
        return Ok(None);
    };
    let Some((end, len)) = location(&node["range"]["end"], source, size)? else {
        return Ok(None);
    };
    ensure!(begin <= end + len, "reversed Clang source range");
    Ok(Some((begin, end + len)))
}
fn inspect(ast: &Value, source: &Path, bytes: &[u8]) -> Result<Vec<Decl>> {
    ensure!(
        ast["kind"] == "TranslationUnitDecl",
        "not a Clang translation unit"
    );
    let mut declarations = Vec::new();
    for node in ast["inner"]
        .as_array()
        .context("missing Clang declarations")?
    {
        if node["isImplicit"] == true {
            continue;
        }
        let kind = node["kind"].as_str().unwrap_or("");
        if !["FunctionDecl", "VarDecl", "FileScopeAsmDecl"].contains(&kind) {
            continue;
        }
        let children = node["inner"].as_array();
        let body = children.and_then(|a| a.iter().find(|n| n["kind"] == "CompoundStmt"));
        if kind == "FunctionDecl" && body.is_none() {
            continue;
        }
        let Some(decl_range) = range(node, source, bytes.len())? else {
            // Definitions in headers or macro expansions remain in the
            // recompiled TU. Their references must root source-owned helpers
            // too; omitting them can leave a retained header calling a removed
            // static function (e.g. SPIHardware.h interrupt guards).
            ensure!(
                !has_assembly(node),
                "assembly prevents safe C source trimming"
            );
            if kind == "VarDecl" || body.is_some() {
                let mut r = BTreeSet::new();
                refs(node, &mut r);
                if !r.is_empty() {
                    declarations.push(Decl {
                        name: String::new(),
                        body: None,
                        range: (0, 0),
                        refs: r,
                        external: false,
                        removable_data: false,
                    });
                }
            }
            continue;
        };
        ensure!(
            !has_assembly(node),
            "assembly prevents safe C source trimming"
        );
        let name = node["name"]
            .as_str()
            .context("unnamed C declaration")?
            .to_owned();
        let mut references = BTreeSet::new();
        refs(node, &mut references);
        let body_range = if let Some(body) = body {
            let (start, end) = range(body, source, bytes.len())?
                .context("macro-generated function body is not splittable")?;
            ensure!(
                decl_range.0 <= start
                    && end <= decl_range.1
                    && bytes.get(start) == Some(&b'{')
                    && bytes.get(end - 1) == Some(&b'}'),
                "invalid C function body range"
            );
            Some((start, end))
        } else {
            None
        };
        let ty = node["type"]["qualType"].as_str().unwrap_or("");
        // Discard only immutable byte arrays. All state and callback initializers
        // remain shared in the original translation unit.
        let mut declaration_end = decl_range.1;
        while bytes
            .get(declaration_end)
            .is_some_and(u8::is_ascii_whitespace)
        {
            declaration_end += 1;
        }
        let removable_data = kind == "VarDecl"
            && bytes.get(declaration_end) == Some(&b';')
            && node["storageClass"] != "extern"
            && node.get("init").is_some()
            && (ty.starts_with("const unsigned char[")
                || ty.starts_with("const uint8_t[")
                || ty.starts_with("const char["))
            && references.is_empty();
        declarations.push(Decl {
            name,
            body: body_range,
            range: if removable_data {
                (decl_range.0, declaration_end + 1)
            } else {
                decl_range
            },
            refs: references,
            external: node["storageClass"] != "static",
            removable_data,
        });
    }
    // Clang gives comma-separated variables overlapping declaration ranges.
    // Retain those declarations intact rather than deleting another variable.
    let overlaps: Vec<_> = declarations
        .iter()
        .map(|d| {
            declarations.iter().any(|other| {
                !std::ptr::eq(d, other) && d.range.0 < other.range.1 && other.range.0 < d.range.1
            })
        })
        .collect();
    for (decl, overlap) in declarations.iter_mut().zip(overlaps) {
        if overlap {
            decl.removable_data = false;
        }
    }
    ensure!(!declarations.is_empty(), "no source-owned C declarations");
    let mut ranges: Vec<_> = declarations.iter().filter_map(|d| d.body).collect();
    ranges.sort();
    ensure!(
        ranges.windows(2).all(|p| p[0].1 <= p[1].0),
        "overlapping C functions"
    );
    Ok(declarations)
}
fn oversized(bytes: &[u8], code_limit: u64, xram_limit: u64) -> Result<bool> {
    let text = std::str::from_utf8(bytes)?;
    let re = regex::Regex::new(r"(?m)^A (\S+) size ([0-9A-Fa-f]+) flags ")?;
    let mut code = 0;
    let mut xram = 0;
    for m in re.captures_iter(text) {
        let size = u64::from_str_radix(&m[2], 16)?;
        if m[1].starts_with("CSEG") || m[1].starts_with("CONST") {
            code += size;
        }
        if ["XSEG", "XISEG"].contains(&&m[1]) {
            xram += size;
        }
    }
    Ok(code > code_limit || xram > xram_limit)
}
fn strings(value: &Value) -> Result<Vec<String>> {
    value
        .as_array()
        .context("missing C argument vector")?
        .iter()
        .map(|v| Ok(string(v)?.into()))
        .collect()
}

// Source-backed archive members can use the same closure as direct C inputs.
// Keep the archive boundary: promoting its members to direct inputs would force
// unused globals into the final program and change normal extraction semantics.
pub fn source_dependencies(rel: &Path) -> Result<Value> {
    let path = rel.with_extension("d");
    if !path.is_file() {
        return Ok(Value::Null);
    }
    let dep = dependency_text(path)?
        .replace("\\\r\n", " ")
        .replace("\\\n", " ");
    let Some((_, paths)) = dep.split_once(": ") else {
        return Ok(Value::Null);
    };
    let mut files = BTreeMap::new();
    let mut token = String::new();
    let mut chars = paths.chars().chain(std::iter::once(' ')).peekable();
    while let Some(c) = chars.next() {
        if c == '\\'
            && chars
                .peek()
                .is_some_and(|c| c.is_whitespace() || *c == '#' || *c == '\\')
        {
            token.push(chars.next().unwrap());
        } else if c == '$' && chars.peek() == Some(&'$') {
            chars.next();
            token.push('$');
        } else if c.is_whitespace() {
            if !token.is_empty() {
                let file = PathBuf::from(&token);
                // Unknown make syntax or missing inputs disable optional
                // archive recompilation, not use of the original binary.
                if !file.is_absolute() || !file.is_file() {
                    return Ok(Value::Null);
                }
                files.insert(s(&file), hash(&file)?);
                token.clear();
            }
        } else {
            token.push(c);
        }
    }
    Ok(json!(files))
}
fn dependencies_current(meta: &Value) -> Result<bool> {
    let Some(files) = meta["source_dependencies"].as_object() else {
        return Ok(false);
    };
    if files.is_empty() {
        return Ok(false);
    }
    for (path, expected) in files {
        if !Path::new(path).is_file() || hash(path)? != string(expected)? {
            return Ok(false);
        }
    }
    Ok(true)
}
fn compilation_key(
    driver: &str,
    toolchain: &Value,
    meta: &Value,
    generated: &[u8],
    selected: &BTreeSet<String>,
    compiler: &Path,
    args: &[String],
) -> Result<String> {
    Ok(digest(serde_json::to_vec(&json!({
        "schema_version": 1, "driver_sha256": driver, "toolchain": toolchain,
        "metadata": meta, "generated_sha256": digest(generated),
        "selected": selected, "compiler": s(compiler), "arguments": args
    }))?))
}
fn compiled_cache_matches(manifest: &Path, key: &str, outputs: &[PathBuf]) -> bool {
    // This is a disposable local cache, not a precompiled input archive.
    // Missing, corrupt or outdated entries are rebuilt, never used unchecked.
    (|| -> Result<bool> {
        let entry = json(manifest)?;
        if entry["schema_version"] != 1 || entry["key"] != key {
            return Ok(false);
        }
        let files = entry["files"]
            .as_object()
            .context("missing cached outputs")?;
        if files.len() != outputs.len() {
            return Ok(false);
        }
        for path in outputs {
            check_hash(
                path,
                string(files.get(&s(path)).context("missing cached output")?)?,
            )?;
        }
        Ok(true)
    })()
    .unwrap_or(false)
}
fn record_compiled_cache(manifest: &Path, key: &str, outputs: &[PathBuf]) -> Result<()> {
    let files = outputs
        .iter()
        .map(|path| Ok((s(path), hash(path)?)))
        .collect::<Result<BTreeMap<_, _>>>()?;
    atomic_write(
        manifest,
        &serde_json::to_vec_pretty(&json!({"schema_version":1,"key":key,"files":files}))?,
    )
}
pub fn is_library_source(meta: &Value) -> bool {
    // --library may point outside a sketchbook's libraries directory. Arduino
    // still places its objects under build/libraries; core objects stay excluded.
    meta["library_source"] == true
        || ["source", "original_rel"].iter().any(|key| {
            meta[key]
                .as_str()
                .is_some_and(|p| p.replace('\\', "/").contains("/libraries/"))
        })
}
fn archive_source_member(archive: &Path, name: &str, bytes: &[u8]) -> Result<Option<PathBuf>> {
    let base = archive.parent().context("archive without directory")?;
    let mut candidates = Vec::new();
    for entry in walkdir::WalkDir::new(base).sort_by_file_name() {
        let entry = entry?;
        if !entry.file_type().is_file() || entry.file_name() != name {
            continue;
        }
        let path = entry.into_path();
        let metadata = suffix(&path, ".stcxx-c.json");
        if !metadata.is_file() || fs::read(&path)? != bytes {
            continue;
        }
        let meta = json(&metadata)?;
        let source = PathBuf::from(string(&meta["source"])?);
        if !is_library_source(&meta) {
            continue;
        }
        // A binary-only or relocated archive must still work without its old
        // checkout. Never recompile changed source against cached metadata.
        if !source.is_file()
            || hash(&source)? != string(&meta["source_sha256"])?
            || !dependencies_current(&meta)?
        {
            continue;
        }
        check_hash(&path, string(&meta["original_rel_sha256"])?)?;
        candidates.push(path);
    }
    ensure!(
        candidates.len() <= 1,
        "ambiguous native C archive member: {name}"
    );
    Ok(candidates.pop())
}

pub fn trim(d: &Driver, arguments: &[String], work: &Path) -> Result<Vec<String>> {
    let code_limit = command_flag(arguments, "--code-size")?.parse::<u64>()?;
    let xram_limit = command_flag(arguments, "--xram-size")?.parse::<u64>()?;
    let mut units = Vec::new();
    let mut native = Vec::new();
    let resource = String::from_utf8(d.call("clang", &["--print-resource-dir".into()], true)?)?;
    let mut classifications = Vec::new();
    let mut inputs = Vec::new();
    let mut archives = BTreeMap::new();
    for arg in arguments {
        if arg.ends_with(".lib") {
            let members = archive_members(Path::new(arg))?;
            let mut sources = BTreeMap::new();
            for (name, bytes) in &members {
                if let Some(path) = archive_source_member(Path::new(arg), name, bytes)? {
                    sources.insert(name.clone(), path.clone());
                    inputs.push(path);
                } else {
                    native.push(bytes.clone());
                }
            }
            if !sources.is_empty() {
                archives.insert(arg.clone(), (members, sources));
            }
        } else if arg.ends_with(".rel") {
            inputs.push(PathBuf::from(arg));
        }
    }
    for path in inputs {
        let bytes = fs::read(&path)?;
        let metadata = suffix(&path, ".stcxx-c.json");
        if !metadata.is_file() {
            native.push(bytes);
            continue;
        }
        let meta = json(&metadata)?;
        let source = PathBuf::from(string(&meta["source"])?);
        if !is_library_source(&meta) {
            native.push(bytes);
            continue;
        }
        check_hash(&source, string(&meta["source_sha256"])?)?;
        check_hash(&path, string(&meta["original_rel_sha256"])?)?;
        let source_bytes = fs::read(&source)?;
        let mut args = vec![
            "-x".into(),
            "c".into(),
            "-fsyntax-only".into(),
            "-fno-color-diagnostics".into(),
            "-Xclang".into(),
            "-ast-dump=json".into(),
            format!("--target={}", d.triple),
            "-std=gnu11".into(),
            "-ffreestanding".into(),
            "-funsigned-char".into(),
            "-nostdinc".into(),
            format!("-I{}", s(d.headers())),
            format!("-isystem{}", s(Path::new(resource.trim()).join("include"))),
            "-D__code=".into(),
            "-D__reentrant=".into(),
            "-D__xdata=".into(),
            "-D__idata=".into(),
            "-D__pdata=".into(),
            "-D__data=".into(),
            "-D__sfr=volatile unsigned char".into(),
            "-D__sbit=volatile unsigned char".into(),
            "-D__at(x)=".into(),
            // Select the same hardware branches as the backend. Unsupported
            // SFR/assembly constructs make the optional probe retain the TU.
            "-D__SDCC=1".into(),
            "-D__SDCC_mcs251=1".into(),
        ];
        let flags = meta.get("clang_flags").unwrap_or(&meta["clang_arguments"]);
        args.extend(strings(flags)?);
        args.push(s(&source));
        let analysis = (|| -> Result<Vec<Decl>> {
            let output = probe(&d.tool("clang"), &args)?;
            ensure!(
                output.status.success(),
                "Clang source analysis unavailable: {}",
                String::from_utf8_lossy(&output.stderr)
            );
            inspect(
                &serde_json::from_slice(&output.stdout)?,
                &source,
                &source_bytes,
            )
        })();
        match analysis {
            Ok(decls) => {
                classifications.push(json!({"source":s(&source),"mode":"source-closure"}));
                units.push(Unit {
                    original: path,
                    source,
                    bytes: source_bytes,
                    meta,
                    decls,
                    selected: BTreeSet::new(),
                });
            }
            Err(error) => {
                ensure!(
                    !oversized(&bytes, code_limit, xram_limit)?,
                    "oversized C object cannot be safely trimmed: {}: {error:#}",
                    source.display()
                );
                classifications.push(
                    json!({"source":s(source),"mode":"original","reason":format!("{error:#}")}),
                );
                native.push(bytes);
            }
        }
    }
    if units.is_empty() {
        write_json(
            work.join("native-source-closure.json"),
            &json!({"schema_version":1,"classifications":classifications,"units":[]}),
        )?;
        return Ok(arguments.to_vec());
    }
    let mut needed = BTreeSet::new();
    for bytes in &native {
        let (_, refs) = rel_symbols(bytes)?;
        needed.extend(
            refs.into_iter()
                .map(|n| n.strip_prefix('_').unwrap_or(&n).to_owned()),
        );
    }
    for unit in &units {
        for decl in &unit.decls {
            if decl.body.is_none() && !decl.removable_data {
                needed.extend(decl.refs.clone());
            }
        }
    }
    loop {
        let mut changed = false;
        for unit in &mut units {
            let mut pending: Vec<_> = unit
                .decls
                .iter()
                .filter(|decl| {
                    decl.external && needed.contains(&decl.name)
                        || decl.body.is_none() && !decl.removable_data
                })
                .map(|d| d.name.clone())
                .collect();
            // Private functions referenced by retained file-scope initializers.
            for decl in &unit.decls {
                if decl.body.is_none() && !decl.removable_data {
                    pending.extend(decl.refs.clone());
                }
            }
            while let Some(name) = pending.pop() {
                if unit.selected.contains(&name) {
                    continue;
                }
                if let Some(decl) = unit.decls.iter().find(|d| d.name == name) {
                    unit.selected.insert(name);
                    changed = true;
                    pending.extend(decl.refs.clone());
                    for r in &decl.refs {
                        if !unit.decls.iter().any(|d| &d.name == r && !d.external)
                            && needed.insert(r.clone())
                        {
                            changed = true;
                        }
                    }
                }
            }
        }
        if !changed {
            break;
        }
    }
    let mut replacements = BTreeMap::new();
    let mut audits = Vec::new();
    let driver_hash = hash(std::env::current_exe()?)?;
    for unit in units {
        let mut edits = Vec::new();
        let mut removed = BTreeSet::new();
        let mut expected = BTreeSet::new();
        for decl in &unit.decls {
            if let Some(range) = decl.body {
                if !unit.selected.contains(&decl.name) {
                    edits.push((range, ";"));
                    removed.insert(format!("_{}", decl.name));
                } else if decl.external {
                    expected.insert(format!("_{}", decl.name));
                }
            } else if decl.removable_data && !unit.selected.contains(&decl.name) {
                edits.push((decl.range, ""));
                removed.insert(format!("_{}", decl.name));
            }
        }
        if edits.is_empty() {
            continue;
        }
        edits.sort_by_key(|(r, _)| r.0);
        ensure!(
            edits.windows(2).all(|p| p[0].0.1 <= p[1].0.0),
            "overlapping source edits"
        );
        let mut generated = unit.bytes.clone();
        for ((start, end), replacement) in edits.into_iter().rev() {
            generated.splice(start..end, replacement.bytes());
        }
        let directory = work
            .join("native-source-closure")
            .join(&digest(s(&unit.original))[..16]);
        fs::create_dir_all(&directory)?;
        let source = directory.join(unit.source.file_name().unwrap());
        let rel = directory.join(unit.original.file_name().unwrap());
        let flags = unit
            .meta
            .get("sdcc_flags")
            .unwrap_or(&unit.meta["sdcc_arguments"]);
        let mut args = strings(flags)?;
        args.extend([
            format!("-I{}", s(unit.source.parent().unwrap())),
            s(&source),
            "-o".into(),
            s(&rel),
        ]);
        let key = compilation_key(
            &driver_hash,
            &d.lock,
            &unit.meta,
            &generated,
            &unit.selected,
            &d.sdcc,
            &args,
        )?;
        let cache_manifest = directory.join("compiled-cache.json");
        let outputs = [rel.clone(), rel.with_extension("lst")];
        let cacheable = dependencies_current(&unit.meta)?;
        let cache_hit = cacheable && compiled_cache_matches(&cache_manifest, &key, &outputs);
        // Keep the generated source available for diagnostics, even on a hit.
        if fs::read(&source).ok().as_deref() != Some(generated.as_slice()) {
            write(&source, &generated)?;
        }
        if !cache_hit {
            remove(&cache_manifest)?;
            for output in &outputs {
                remove(output)?;
            }
            run(
                &d.sdcc,
                &args,
                Some(&directory),
                false,
                Some(&directory.join("compile.log")),
            )?;
        }
        let (defs, refs) = rel_symbols(&fs::read(&rel)?)?;
        ensure!(
            expected.is_subset(&defs) && defs.is_disjoint(&removed),
            "C source closure changed exported definitions"
        );
        let missing: Vec<_> = refs.intersection(&removed).collect();
        ensure!(
            missing.is_empty(),
            "C closure still references discarded symbols: {missing:?}"
        );
        if !cache_hit && cacheable && dependencies_current(&unit.meta)? {
            record_compiled_cache(&cache_manifest, &key, &outputs)?;
        }
        audits.push(json!({"source":s(&unit.source),"source_sha256":digest(&unit.bytes),"generated":s(&source),"generated_sha256":digest(&generated),"original_rel":s(&unit.original),"output_rel":s(&rel),"output_sha256":hash(&rel)?,"retained":unit.selected,"discarded_symbols":removed,"compile_cache_hit":cache_hit}));
        replacements.insert(s(&unit.original), s(&rel));
    }
    let mut archive_audits = Vec::new();
    for (archive, (members, sources)) in archives {
        if !sources.values().any(|p| replacements.contains_key(&s(p))) {
            continue;
        }
        let directory = work
            .join("native-source-archives")
            .join(&digest(&archive)[..16]);
        fs::create_dir_all(&directory)?;
        let output = directory.join("trimmed.lib");
        remove(&output)?;
        let mut args = vec!["rcs".into(), s(&output)];
        let mut changed = Vec::new();
        for (name, bytes) in members {
            let replacement = sources.get(&name).and_then(|p| replacements.get(&s(p)));
            let member = directory.join(&name);
            if let Some(replacement) = replacement {
                write(&member, fs::read(replacement)?)?;
                changed.push(name);
            } else {
                write(&member, bytes)?;
            }
            args.push(s(member));
        }
        let sdar = d
            .sdcc
            .parent()
            .context("SDCC without directory")?
            .join(format!("sdar{}", std::env::consts::EXE_SUFFIX));
        run(
            &sdar,
            &args,
            Some(&directory),
            false,
            Some(&directory.join("archive.log")),
        )?;
        archive_audits.push(json!({"original":archive,"output":s(&output),"output_sha256":hash(&output)?,"recompiled_members":changed}));
        replacements.insert(archive, s(output));
    }
    write_json(
        work.join("native-source-closure.json"),
        &json!({"schema_version":1,"policy":"Clang-source-ranges-SDCC-single-TU-recompile","classifications":classifications,"units":audits,"archives":archive_audits}),
    )?;
    Ok(arguments
        .iter()
        .map(|a| replacements.get(a).unwrap_or(a).clone())
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn compiled_cache_invalidates_changed_inputs_and_damaged_outputs() {
        let temp = tempfile::tempdir().unwrap();
        let rel = temp.path().join("source.rel");
        let lst = temp.path().join("source.lst");
        let manifest = temp.path().join("compiled-cache.json");
        write(&rel, "object").unwrap();
        write(&lst, "listing").unwrap();
        let outputs = [rel.clone(), lst.clone()];
        let toolchain = json!({"sdcc":"original"});
        let meta =
            json!({"source_sha256":"original","source_dependencies":{"header.h":"original"}});
        let selected = BTreeSet::from(["used".into()]);
        let compiler = Path::new("/compiler/sdcc");
        let args = vec!["--model-large".into()];
        let key = compilation_key(
            "driver",
            &toolchain,
            &meta,
            b"generated",
            &selected,
            compiler,
            &args,
        )
        .unwrap();
        assert!(!compiled_cache_matches(&manifest, &key, &outputs));
        record_compiled_cache(&manifest, &key, &outputs).unwrap();
        assert!(compiled_cache_matches(&manifest, &key, &outputs));
        write_json(
            &manifest,
            &json!({"schema_version":1,"key":key,"files":{"wrong.rel":"hash","wrong.lst":"hash"}}),
        )
        .unwrap();
        assert!(!compiled_cache_matches(&manifest, &key, &outputs));
        record_compiled_cache(&manifest, &key, &outputs).unwrap();
        for changed in [
            compilation_key(
                "new driver",
                &toolchain,
                &meta,
                b"generated",
                &selected,
                compiler,
                &args,
            ),
            compilation_key(
                "driver",
                &json!({"sdcc":"changed"}),
                &meta,
                b"generated",
                &selected,
                compiler,
                &args,
            ),
            compilation_key(
                "driver",
                &toolchain,
                &json!({"source_sha256":"changed"}),
                b"generated",
                &selected,
                compiler,
                &args,
            ),
            compilation_key(
                "driver",
                &toolchain,
                &json!({"source_dependencies":{"header.h":"changed"}}),
                b"generated",
                &selected,
                compiler,
                &args,
            ),
            compilation_key(
                "driver", &toolchain, &meta, b"changed", &selected, compiler, &args,
            ),
            compilation_key(
                "driver",
                &toolchain,
                &meta,
                b"generated",
                &BTreeSet::from(["other".into()]),
                compiler,
                &args,
            ),
            compilation_key(
                "driver",
                &toolchain,
                &meta,
                b"generated",
                &selected,
                compiler,
                &["--model-small".into()],
            ),
        ] {
            assert!(!compiled_cache_matches(
                &manifest,
                &changed.unwrap(),
                &outputs
            ));
        }
        write(&rel, "tampered").unwrap();
        assert!(!compiled_cache_matches(&manifest, &key, &outputs));
        write(&rel, "object").unwrap();
        remove(&lst).unwrap();
        assert!(!compiled_cache_matches(&manifest, &key, &outputs));
        write(&manifest, "invalid JSON").unwrap();
        assert!(!compiled_cache_matches(&manifest, &key, &outputs));
    }
    #[test]
    fn native_archive_sources_require_matching_live_source_and_payload() {
        let temp = tempfile::tempdir().unwrap();
        let base = temp.path().join("cache");
        fs::create_dir_all(&base).unwrap();
        let source = temp.path().join("custom path/Test/src/test.c");
        write(&source, "void used(void) {}\n").unwrap();
        let member = base.join("test.c.rel");
        let bytes = b"XH3\nS _used Def000000\n";
        write(&member, bytes).unwrap();
        let header = temp.path().join("libraries/Test/src/shared header.h");
        write(&header, "original header").unwrap();
        write(
            member.with_extension("d"),
            format!(
                "test.rel: \\\n {} \\\n {}\n",
                s(&source).replace('\\', "/").replace(' ', "\\ "),
                s(&header).replace('\\', "/").replace(' ', "\\ ")
            ),
        )
        .unwrap();
        write_json(
            suffix(&member, ".stcxx-c.json"),
            &json!({
                "source":s(&source), "source_sha256":hash(&source).unwrap(),
                "original_rel":s(temp.path().join("build/libraries/Test/test.c.rel")),
                "original_rel_sha256":digest(bytes), "source_dependencies":source_dependencies(&member).unwrap()
            }),
        )
        .unwrap();
        let archive = base.join("test.lib");
        assert_eq!(
            archive_source_member(&archive, "test.c.rel", bytes).unwrap(),
            Some(member)
        );
        assert!(
            archive_source_member(&archive, "test.c.rel", b"different")
                .unwrap()
                .is_none()
        );
        write(&header, "changed header").unwrap();
        assert!(
            archive_source_member(&archive, "test.c.rel", bytes)
                .unwrap()
                .is_none()
        );
        write(&header, "original header").unwrap();
        write(&source, "changed source").unwrap();
        assert!(
            archive_source_member(&archive, "test.c.rel", bytes)
                .unwrap()
                .is_none()
        );
        remove(&source).unwrap();
        assert!(
            archive_source_member(&archive, "test.c.rel", bytes)
                .unwrap()
                .is_none()
        );
    }
    #[test]
    fn retained_header_functions_root_their_source_helpers() {
        let ast = json!({"kind":"TranslationUnitDecl", "inner":[{
            "kind":"FunctionDecl", "name":"header_function", "storageClass":"static",
            "range":{"begin":{"offset":0,"includedFrom":{"file":"header.h"}},"end":{"offset":1}},
            "inner":[{"kind":"CompoundStmt","inner":[{"referencedDecl":{"name":"source_helper"}}]}]
        }]});
        let declarations = inspect(&ast, Path::new("source.c"), b" ").unwrap();
        assert_eq!(declarations.len(), 1);
        assert!(declarations[0].body.is_none() && !declarations[0].removable_data);
        assert!(declarations[0].refs.contains("source_helper"));
    }
}
