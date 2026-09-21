use crate::{adapter, cache, driver::Driver, util::*};
use anyhow::{Context, Result, ensure};
use regex::Regex;
use serde_json::json;
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
};

fn optimization_passes(level: &str, link_level: Option<&str>) -> &'static str {
    if link_level.unwrap_or(if level == "0" { "0" } else { "z" }) == "0" {
        "internalize,deadargelim,globaldce"
    } else {
        // Keep code-size policy at the whole-program boundary; frontend Oz
        // also avoids O0's implicit noinline on every Arduino wrapper.
        "internalize,deadargelim,default<Oz>"
    }
}

pub fn public_defs(ir: &str) -> Result<BTreeSet<String>> {
    let name = Regex::new(r#"@(?:"([^"]+)"|([-A-Za-z$._0-9]+))\s*[=(]"#)?;
    let mut defs = BTreeSet::new();
    let local = Regex::new(r"\b(internal|private|external)\b")?;
    for line in ir.lines() {
        if (line.starts_with("define ") || line.starts_with('@') && line.contains('='))
            && !local.is_match(line)
            && let Some(m) = name.captures(line)
        {
            let n = m.get(1).or_else(|| m.get(2)).unwrap().as_str();
            if !n.starts_with("llvm.") {
                defs.insert(n.into());
            }
        }
    }
    Ok(defs)
}
pub fn rel_symbols(payload: &[u8]) -> Result<(BTreeSet<String>, BTreeSet<String>)> {
    let text = std::str::from_utf8(payload)?;
    let mut defs = BTreeSet::new();
    let mut refs = BTreeSet::new();
    for m in Regex::new(r"(?m)^S\s+(\S+)\s+(Def|Ref)[0-9A-Fa-f]+\r?$")?.captures_iter(text) {
        if &m[2] == "Def" {
            defs.insert(m[1].into());
        } else {
            refs.insert(m[1].into());
        }
    }
    ensure!(
        !defs.is_empty() || !refs.is_empty(),
        "missing REL symbol table"
    );
    Ok((defs, refs))
}
pub fn archive_members(path: &Path) -> Result<BTreeMap<String, Vec<u8>>> {
    // Read ar bytes directly: sdar -p on Windows translates LF to CRLF.
    let data = fs::read(path)?;
    ensure!(
        data.starts_with(b"!<arch>\n"),
        "unsupported native archive: {}",
        path.display()
    );
    let mut files = BTreeMap::new();
    let mut pos = 8;
    let mut names = Vec::new();
    while pos < data.len() {
        ensure!(pos + 60 <= data.len(), "truncated ar header");
        let header = &data[pos..pos + 60];
        ensure!(&header[58..] == b"`\n", "invalid ar header");
        let size = std::str::from_utf8(&header[48..58])?
            .trim()
            .parse::<usize>()?;
        pos += 60;
        ensure!(pos + size <= data.len(), "truncated ar payload");
        let mut body = &data[pos..pos + size];
        let raw = std::str::from_utf8(&header[..16])?.trim();
        let name = if raw == "//" {
            names = body.to_vec();
            None
        } else if raw == "/" || raw == "/SYM64/" {
            None
        } else if let Some(n) = raw.strip_prefix("#1/") {
            let n = n.parse::<usize>()?;
            ensure!(n <= body.len(), "invalid BSD ar name");
            let name = std::str::from_utf8(&body[..n])?
                .trim_end_matches('\0')
                .to_owned();
            body = &body[n..];
            Some(name)
        } else if let Some(n) = raw.strip_prefix('/') {
            let n = n.parse::<usize>()?;
            ensure!(n < names.len(), "invalid ar string offset");
            let value = std::str::from_utf8(&names[n..])?
                .split('\n')
                .next()
                .unwrap()
                .trim_end_matches('/');
            Some(value.into())
        } else {
            Some(raw.trim_end_matches('/').to_owned())
        };
        if let Some(name) = name {
            safe_relative(&name)?;
            ensure!(!name.contains('/'), "nested native archive member");
            ensure!(
                files.insert(name.clone(), body.to_vec()).is_none(),
                "duplicate native archive member: {name}"
            );
        }
        pos += size + (size % 2);
    }
    Ok(files)
}
fn sidecar(d: &Driver, bc: &Path) -> Result<()> {
    let obj = PathBuf::from(s(bc).trim_end_matches(".stcxx.bc"));
    let meta = json(suffix(&obj, ".stcxx.json"))?;
    ensure!(
        meta["schema_version"] == 1
            && meta["target_triple"] == d.triple
            && meta["data_layout"] == d.layout,
        "C++ sidecar ABI mismatch"
    );
    ensure!(
        absolute(string(&meta["object"])?)? == obj && absolute(string(&meta["bitcode"])?)? == bc,
        "C++ sidecar path mismatch"
    );
    for (p, k, legacy) in [
        (
            PathBuf::from(string(&meta["source"])?),
            "source_sha256",
            "source_sha256",
        ),
        (obj.clone(), "object_sha256", "object_sha256"),
        (bc.to_path_buf(), "bitcode_sha256", "bitcode_sha256"),
        (suffix(&obj, ".stcxx.ll"), "llvm_ir_sha256", "ir_sha256"),
        (
            suffix(&obj, ".stcxx.module.cbe"),
            "module_c_sha256",
            "cbe_sha256",
        ),
    ] {
        check_hash(p, string(meta.get(k).unwrap_or(&meta[legacy]))?)?;
    }
    check_hash(obj.with_extension("rel"), string(&meta["object_sha256"])?)?;
    Ok(())
}
fn ir_for(bc: &Path) -> PathBuf {
    PathBuf::from(s(bc).trim_end_matches(".bc").to_owned() + ".ll")
}
struct Candidate {
    bc: PathBuf,
    defs: BTreeSet<String>,
    stripped: PathBuf,
}
fn odr_definition(ir: &str, symbol: &str) -> Result<String> {
    let symbol = regex::escape(symbol);
    let function = Regex::new(&format!(
        r"(?ms)^define (?:linkonce_odr|weak_odr)[^\n]*@{symbol}\([^\n]*\).*?^\}}"
    ))?;
    let global = Regex::new(&format!(
        r"(?m)^@{symbol} = (?:linkonce_odr|weak_odr) [^\r\n]+"
    ))?;
    let definition = function
        .find(ir)
        .or_else(|| global.find(ir))
        .context("ambiguous non-ODR archive definition")?
        .as_str();
    let attributes = Regex::new(r" #[0-9]+|![a-z_.]+ ![0-9]+")?;
    Ok(attributes.replace_all(definition, "").into_owned())
}
fn select(
    d: &Driver,
    direct: &[PathBuf],
    candidate_paths: &[PathBuf],
    roots: &BTreeSet<String>,
    work: &Path,
) -> Result<PathBuf> {
    ensure!(
        !direct.is_empty() || d.standalone,
        "missing direct C++ input"
    );
    fs::create_dir_all(work)?;
    let mut candidates = Vec::new();
    let mut selected = BTreeSet::new();
    let ctor_line = Regex::new(r"(?m)^@llvm\.global_ctors[^\n]*\n")?;
    for (i, bc) in candidate_paths.iter().enumerate() {
        let ir = text(ir_for(bc))?;
        d.check_target(&ir)?;
        let defs = public_defs(&ir)?;
        if !defs.is_disjoint(roots) {
            selected.insert(i);
        }
        let stripped = work.join(format!("candidate-{i}-no-ctors.ll"));
        write(&stripped, ctor_line.replace_all(&ir, "").as_bytes())?;
        candidates.push(Candidate {
            bc: bc.clone(),
            defs,
            stripped,
        });
    }
    ensure!(
        !direct.is_empty() || !selected.is_empty(),
        "missing runtime IR roots"
    );
    for iteration in 0..=candidates.len() {
        let base = work.join(format!("iteration-{iteration}.bc"));
        let base_ir = base.with_extension("ll");
        let mut args: Vec<_> = direct.iter().map(s).collect();
        args.extend(selected.iter().map(|i| s(&candidates[*i].bc)));
        args.extend(["-o".into(), s(&base)]);
        d.call("llvm-link", &args, false)?;
        d.call("llvm-dis", &[s(&base), "-o".into(), s(&base_ir)], false)?;
        let definitions = public_defs(&text(&base_ir)?)?;
        let remaining: Vec<_> = (0..candidates.len())
            .filter(|i| !selected.contains(i))
            .collect();
        if remaining.is_empty() {
            ensure!(
                roots.is_subset(&definitions),
                "missing C ABI roots after archive selection"
            );
            return Ok(base);
        }
        let probe = work.join(format!("probe-{iteration}.bc"));
        let probe_ir = probe.with_extension("ll");
        let mut args = vec!["--only-needed".into(), s(&base)];
        args.extend(remaining.iter().map(|i| s(&candidates[*i].stripped)));
        args.extend(["-o".into(), s(&probe)]);
        d.call("llvm-link", &args, false)?;
        d.call("llvm-dis", &[s(&probe), "-o".into(), s(&probe_ir)], false)?;
        let observed = public_defs(&text(probe_ir)?)?;
        let added: BTreeSet<_> = observed.difference(&definitions).cloned().collect();
        let mut next = BTreeSet::new();
        let mut ambiguous = Vec::new();
        for symbol in &added {
            let owners: Vec<_> = remaining
                .iter()
                .copied()
                .filter(|i| candidates[*i].defs.contains(symbol))
                .collect();
            ensure!(
                !owners.is_empty(),
                "unowned C++ archive definition: {symbol}"
            );
            if owners.len() == 1 {
                next.insert(owners[0]);
            } else {
                ambiguous.push((symbol, owners));
            }
        }
        for (symbol, owners) in ambiguous {
            ensure!(
                owners.iter().any(|i| next.contains(i)),
                "ambiguous archive definition: {symbol}"
            );
            // Functions and shared C++ data (notably vtables) can both have
            // ODR linkage. Require an identical definition in every provider.
            let mut identities = BTreeSet::new();
            for i in owners {
                let ir = text(ir_for(&candidates[i].bc))?;
                identities.insert(odr_definition(&ir, symbol)?);
            }
            ensure!(
                identities.len() == 1,
                "conflicting ODR definition: {symbol}"
            );
        }
        if next.is_empty() {
            ensure!(
                added.is_empty() && roots.is_subset(&definitions),
                "incomplete C++ archive closure"
            );
            return Ok(base);
        }
        selected.extend(next);
    }
    anyhow::bail!("archive selection did not converge")
}
pub fn pipeline(d: &Driver) -> Result<()> {
    let output = d.output.as_ref().context("missing HEX output")?;
    ensure!(
        output.extension().is_some_and(|e| e == "hex"),
        "expected .hex output"
    );
    remove(output)?;
    let work = if d.standalone {
        output.with_extension("stcxx")
    } else {
        output.parent().unwrap().join("stcxx")
    };
    fs::create_dir_all(&work)?;
    remove(work.join("manifest.json"))?;
    d.verify()?;
    let result = pipeline_inner(d, &work, output);
    if result.is_err() {
        remove(output)?;
    }
    result
}
fn resolve_libraries(input: &[String]) -> Result<Vec<String>> {
    let mut paths = Vec::new();
    let mut args = input.iter();
    while let Some(arg) = args.next() {
        if arg == "-L" {
            paths.push(absolute(args.next().context("missing -L path")?)?);
        } else if let Some(path) = arg.strip_prefix("-L") {
            paths.push(absolute(path)?);
        }
    }
    let mut result = Vec::new();
    let mut args = input.iter();
    while let Some(arg) = args.next() {
        if arg == "-L" {
            result.push(format!("-L{}", args.next().context("missing -L path")?));
            continue;
        }
        let name = if arg == "-l" {
            Some(args.next().context("missing -l name")?.as_str())
        } else {
            arg.strip_prefix("-l")
        };
        if let Some(name) = name {
            let names = if let Some(exact) = name.strip_prefix(':') {
                vec![exact.into()]
            } else {
                vec![
                    format!("lib{name}.a"),
                    format!("{name}.a"),
                    format!("{name}.lib"),
                    format!("lib{name}.lib"),
                ]
            };
            let found = paths
                .iter()
                .flat_map(|p| names.iter().map(move |n| p.join(n)))
                .find(|p| p.is_file());
            result.push(found.map(s).unwrap_or_else(|| format!("-l{name}")));
        } else {
            result.push(arg.clone());
        }
    }
    Ok(result)
}
fn pipeline_inner(d: &Driver, work: &Path, output: &Path) -> Result<()> {
    let mut arguments = Vec::new();
    for arg in resolve_libraries(&d.arguments)? {
        arguments.push(if arg.ends_with(".a") || arg.ends_with(".lib") {
            s(cache::materialize(&absolute(arg)?, work)?)
        } else {
            arg.clone()
        });
    }
    let direct: Vec<_> = arguments
        .iter()
        .filter(|a| a.ends_with(".o"))
        .map(absolute)
        .collect::<Result<_>>()?;
    let archives: Vec<_> = arguments
        .iter()
        .filter(|a| a.ends_with(".a"))
        .map(absolute)
        .collect::<Result<_>>()?;
    let mut bitcodes = Vec::new();
    let mut native: Vec<(String, Vec<u8>)> = Vec::new();
    let mut native_rels = Vec::new();
    for obj in &direct {
        ensure!(
            obj.file_name().is_none_or(|n| n != "stcxx_heap.c.o"),
            "heap entered direct inputs"
        );
        let bc = suffix(obj, ".stcxx.bc");
        if crate::artifact::validate(obj)? {
            sidecar(d, &bc)?;
            bitcodes.push(bc);
        } else {
            let rel = obj.with_extension("rel");
            native.push((s(&rel), fs::read(&rel)?));
            native_rels.push(rel);
        }
    }
    let direct_bcs = bitcodes.clone();
    let mut candidates = Vec::new();
    let mut heaps = Vec::new();
    for archive in &archives {
        check_hash(archive.with_extension("lib"), &hash(archive)?)?;
        let members = archive_members(archive)?;
        ensure!(
            !members.contains_key("stcxx_heap.c.rel"),
            "heap stored in core archive"
        );
        let base = archive.parent().unwrap();
        let heap = base.join("stcxx_heap.c.rel");
        if heap.is_file() {
            let state = base.join("stcxx_heap_state.c.rel");
            ensure!(
                members.get("stcxx_heap_state.c.rel") == Some(&fs::read(&state)?),
                "heap state differs from archive"
            );
            check_heap(&heap, &state, d.constrained)?;
            heaps.push((heap, archive.clone()));
        }
        let mut excluded = BTreeSet::new();
        for entry in walkdir::WalkDir::new(base).sort_by_file_name() {
            let p = entry?.into_path();
            if !s(&p).ends_with(".stcxx.bc") {
                continue;
            }
            let obj = PathBuf::from(s(&p).trim_end_matches(".stcxx.bc"));
            let rel = obj.with_extension("rel");
            let name = s(Path::new(rel.file_name().unwrap()));
            if let Some(payload) = members.get(&name)
                && rel.is_file()
                && hash(&rel)? == digest(payload)
            {
                ensure!(
                    excluded.insert(name.clone()),
                    "multiple sidecars for archive member {name}"
                );
                sidecar(d, &p)?;
                bitcodes.push(p.clone());
                candidates.push(p);
            }
        }
        for (name, bytes) in members {
            if !excluded.contains(&name) {
                ensure!(
                    !crate::artifact::placeholder(&bytes),
                    "incomplete C++ archive member: {}({name}); clean the build cache and rebuild",
                    archive.display()
                );
                native.push((format!("{}({name})", s(archive)), bytes));
            }
        }
    }
    ensure!(
        heaps.len() == 1,
        "expected exactly one board-sized heap object"
    );
    let (heap, heap_archive) = &heaps[0];
    let mut all = bitcodes.iter().map(s).collect::<Vec<_>>();
    let all_bc = work.join("all-candidates-linked.bc");
    let all_ir = work.join("all-candidates-linked.ll");
    all.extend(["-o".into(), s(&all_bc)]);
    d.call("llvm-link", &all, false)?;
    d.call("llvm-dis", &[s(&all_bc), "-o".into(), s(&all_ir)], false)?;
    let defs = public_defs(&text(&all_ir)?)?;
    let mut native_defs = BTreeSet::new();
    let mut native_refs = BTreeSet::new();
    for (_, bytes) in &native {
        let (defs, refs) = rel_symbols(bytes)?;
        native_defs.extend(defs);
        native_refs.extend(refs);
    }
    let mut roots: BTreeSet<String> = ["__stcxx_run_global_ctors", &d.abi, "stcxx_runtime_panic"]
        .map(String::from)
        .into_iter()
        .collect();
    if d.standalone {
        ensure!(
            defs.contains("__stcxx_user_main") || native_defs.contains("___stcxx_user_main"),
            "application must define int main(void)"
        );
    } else {
        roots.extend(["setup".into(), "loop".into()]);
    }
    ensure!(roots.is_subset(&defs), "missing required C++ runtime roots");
    roots.extend(
        native_refs
            .difference(&native_defs)
            .map(|s| s.strip_prefix('_').unwrap_or(s))
            .filter(|s| defs.contains(*s))
            .map(String::from),
    );
    let preserve = work.join("c-abi-preserve.txt");
    write(
        &preserve,
        roots.iter().cloned().collect::<Vec<_>>().join("\n") + "\n",
    )?;
    let selected = select(
        d,
        &direct_bcs,
        &candidates,
        &roots,
        &work.join("archive-selection"),
    )?;
    let linked = work.join("linked.ll");
    fs::copy(selected, work.join("linked.bc"))?;
    d.call(
        "llvm-dis",
        &[s(work.join("linked.bc")), "-o".into(), s(&linked)],
        false,
    )?;
    let generic = work.join("optimized.generic.ll");
    let optimized = work.join("optimized.ll");
    let optimized_bc = work.join("optimized.bc");
    write_json(
        work.join("optimization.json"),
        &json!({
            "requested_frontend": d.optimization,
            "link_override": d.link_optimization,
            "passes": optimization_passes(&d.optimization, d.link_optimization.as_deref()),
            "verified_each_pass": true,
        }),
    )?;
    d.call(
        "opt",
        &[
            "-mtriple=unknown-unknown-unknown".into(),
            format!(
                "-passes={}",
                optimization_passes(&d.optimization, d.link_optimization.as_deref())
            ),
            "-verify-each".into(),
            format!(
                "-internalize-public-api-list={}",
                roots.iter().cloned().collect::<Vec<_>>().join(",")
            ),
            "-S".into(),
            s(&linked),
            "-o".into(),
            s(&generic),
        ],
        false,
    )?;
    let ir = text(&generic)?.replace(
        "target triple = \"unknown-unknown-unknown\"",
        &format!("target triple = \"{}\"", d.triple),
    );
    d.check_target(&ir)?;
    write(&optimized, &ir)?;
    // Reject unsupported stack/ABI constructs before invoking CBE, which may
    // otherwise emit warnings and invalid C for dynamic stack allocation.
    adapter::audit_ir(&ir, &roots)?;
    d.call(
        "llvm-link",
        &[s(&optimized), "-o".into(), s(&optimized_bc)],
        false,
    )?;
    let raw = work.join("raw.c");
    d.call("llvm-cbe", &[s(&optimized_bc), "-o".into(), s(&raw)], false)?;
    let storage = collect_storage(&ir, &native)?;
    let adapted = adapter::adapt(d, &ir, &text(&raw)?, &roots, &storage)?;
    write(work.join("adapted.c"), &adapted.c)?;
    write_json(work.join("audit.json"), &adapted.audit)?;
    let raw_asm = work.join("cpp-bridge.raw.asm");
    let asm = work.join("cpp-bridge.asm");
    let rel = work.join("cpp-bridge.rel");
    let mut args = vec![
        "-mmcs251".into(),
        "--model-large".into(),
        "--stack-auto".into(),
        "--std-sdcc11".into(),
        "--opt-code-size".into(),
        "--nogcse".into(),
        "--less-pedantic".into(),
    ];
    args.extend(d.sections.clone());
    args.extend([
        format!("-I{}", s(&d.includes)),
        format!("-I{}", s(d.includes.join("mcs51"))),
        "-S".into(),
        s(work.join("adapted.c")),
        "-o".into(),
        s(&raw_asm),
    ]);
    run(
        &d.sdcc,
        &args,
        Some(work),
        false,
        Some(&work.join("sdcc-bridge.log")),
    )?;
    adapter::audit_warnings(&text(work.join("sdcc-bridge.log"))?, &adapted.c)?;
    let mut link_args = vec![
        s(&rel),
        format!("-L{}", s(&d.runtime)),
        "--iram-size".into(),
        d.stack["IRAM_SIZE"].clone(),
        format!("-Wl-b SSEG={}", d.stack["STACK_LOC"]),
        "--stack-size".into(),
        d.stack["STACK_SIZE"].clone(),
    ];
    let mut skip = false;
    for a in &arguments {
        if skip {
            skip = false;
            continue;
        }
        if a == "-MF" {
            skip = true;
            continue;
        }
        if a.starts_with("-DSTCXX") || a == "-Ddouble=float" {
            continue;
        }
        if a.ends_with(".o") {
            if !suffix(a, ".stcxx.bc").is_file() {
                link_args.push(s(Path::new(a).with_extension("rel")));
            }
        } else if a.ends_with(".a") {
            if Path::new(a) == heap_archive {
                link_args.push(s(heap));
            }
            link_args.push(s(Path::new(a).with_extension("lib")));
        } else {
            link_args.push(a.clone());
        }
    }
    // Establish the bridge's native references before selecting C source bodies.
    write(
        &asm,
        align_assembly(&text(&raw_asm)?, &adapted.aligned, "even")?,
    )?;
    remove(&rel)?;
    run(
        &d.assembler,
        &["-plosgffw".into(), s(&rel), s(&asm)],
        Some(work),
        false,
        Some(&work.join("sdcc-bridge-assembly.log")),
    )?;
    link_args = crate::trim::trim(d, &link_args, work)?;
    write(
        work.join("sdcc-link-arguments.txt"),
        link_args.join("\n") + "\n",
    )?;
    let mut verified = false;
    for parity in ["even", "odd"] {
        let assembly = align_assembly(&text(&raw_asm)?, &adapted.aligned, parity)?;
        write(&asm, assembly)?;
        for path in [
            &rel,
            &rel.with_extension("lst"),
            &rel.with_extension("rst"),
            output,
            &output.with_extension("map"),
            &output.with_extension("mem"),
        ] {
            remove(path)?;
        }
        run(
            &d.assembler,
            &["-plosgffw".into(), s(&rel), s(&asm)],
            Some(work),
            false,
            Some(&work.join("sdcc-bridge-assembly.log")),
        )?;
        run(
            &d.sdcc,
            &link_args,
            Some(work),
            false,
            Some(&work.join("sdcc-link.log")),
        )?;
        nonempty(output)?;
        if verify_alignment(&text(rel.with_extension("rst"))?, &adapted.aligned)? {
            verified = true;
            break;
        }
    }
    ensure!(
        verified,
        "member-function pointers remain misaligned after relocation"
    );
    let map = text(output.with_extension("map"))?;
    verify_storage(&storage, &map)?;
    verify_heap_map(d, &map, heap)?;
    let log = text(work.join("sdcc-link.log"))?;
    ensure!(
        !log.contains("Definition of public symbol") && !log.contains("found more than once"),
        "duplicate public symbol in final link"
    );
    write_json(
        work.join("manifest.json"),
        &json!({"schema_version":1,"driver":"stcxx-native","version":env!("CARGO_PKG_VERSION"),"outcome":"pass","target_triple":d.triple,"data_layout":d.layout,"hex_sha256":hash(output)?,"ir_sha256":hash(&optimized)?,"adapted_c_sha256":hash(work.join("adapted.c"))?,"roots":roots,"aligned_functions":adapted.aligned,"native_storage":storage,"heap":s(heap),"interpreter":false}),
    )?;
    println!("STCXX_NATIVE_LINK=PASS");
    Ok(())
}
fn check_heap(heap: &Path, state: &Path, constrained: bool) -> Result<()> {
    let payload = fs::read(heap)?;
    let (defs, _) = rel_symbols(&payload)?;
    for name in ["___sdcc_heap", "___sdcc_heap_size32", "___stcxx_heap_init"] {
        ensure!(defs.contains(name), "heap missing {name}");
    }
    ensure!(!defs.contains("___sdcc_heap_size"), "wrong heap ABI");
    let area = Regex::new(r"(?m)^A XSEG size ([0-9A-Fa-f]+) flags ")?;
    let text = std::str::from_utf8(&payload)?;
    let size = u32::from_str_radix(&area.captures(text).context("heap missing XSEG")?[1], 16)?;
    ensure!(
        if constrained {
            size == 3584
        } else {
            size >= 4096
        },
        "invalid board heap allocation"
    );
    let text = crate::util::text(state)?;
    ensure!(
        &area.captures(&text).context("missing heap state")?[1] == "8",
        "heap state must occupy 8 bytes"
    );
    Ok(())
}
fn align_assembly(assembly: &str, aligned: &BTreeSet<String>, parity: &str) -> Result<String> {
    let mut output = String::new();
    let mut found = BTreeSet::new();
    for line in assembly.lines() {
        if let Some(label) = line.strip_suffix(':').and_then(|s| s.strip_prefix('_'))
            && aligned.contains(label)
        {
            ensure!(
                found.insert(label.to_owned()),
                "duplicate assembly function label"
            );
            output.push_str(&format!("\t.{parity}\n"));
        }
        output.push_str(line);
        output.push('\n');
    }
    ensure!(&found == aligned, "missing aligned function in assembly");
    Ok(output)
}
fn verify_alignment(listing: &str, aligned: &BTreeSet<String>) -> Result<bool> {
    let mut parity = BTreeSet::new();
    for name in aligned {
        let pattern = Regex::new(&format!(
            r"(?m)^[ \t]*([0-9A-Fa-f]{{6,8}})[ \t]+[0-9]+[ \t]+_{}:[ \t]*$",
            regex::escape(name)
        ))?;
        let rows: Vec<_> = pattern.captures_iter(listing).collect();
        ensure!(
            rows.len() == 1,
            "missing or ambiguous relocated function: {name}"
        );
        let address = u32::from_str_radix(&rows[0][1], 16)?;
        ensure!(address <= 0xffffff, "function address exceeds 24 bits");
        parity.insert(address % 2);
    }
    ensure!(
        parity.len() <= 1,
        "mixed function alignment after relocation"
    );
    Ok(!parity.contains(&1))
}
pub fn collect_storage(ir: &str, native: &[(String, Vec<u8>)]) -> Result<serde_json::Value> {
    let wanted: BTreeSet<_> = Regex::new(
        r"(?m)^@([A-Za-z_][A-Za-z_0-9]*)\s*=\s*external\b[^\n]*\b(?:global|constant)\b",
    )?
    .captures_iter(ir)
    .map(|m| m[1].to_owned())
    .collect();
    let mut objects = BTreeMap::<String, serde_json::Value>::new();
    let area_re =
        Regex::new(r"^A (\S+) size ([\dA-Fa-f]+) flags ([\dA-Fa-f]+) addr ([\dA-Fa-f]+)$")?;
    let symbol_re = Regex::new(r"^S _(\S+) Def([\dA-Fa-f]+)$")?;
    for (label, bytes) in native {
        let mut module = "";
        let mut area = None;
        for line in std::str::from_utf8(bytes)?.lines() {
            if let Some(m) = line.strip_prefix("M ") {
                module = m;
            } else if let Some(m) = area_re.captures(line) {
                area = Some((
                    m[1].to_owned(),
                    u32::from_str_radix(&m[2], 16)?,
                    u32::from_str_radix(&m[3], 16)?,
                ));
            } else if let Some(m) = symbol_re.captures(line)
                && wanted.contains(&m[1])
            {
                let (_, size, flags) = area.as_ref().context("unknown native storage")?;
                ensure!(
                    flags & 8 == 0 && u32::from_str_radix(&m[2], 16)? < *size,
                    "invalid native object storage"
                );
                let storage = match flags & 0x60 {
                    0x20 => "CODE",
                    0x40 => "XDATA",
                    _ => anyhow::bail!("unsupported native storage for {}", &m[1]),
                };
                let record = objects
                    .entry(m[1].into())
                    .or_insert(json!({"storage":storage,"providers":[]}));
                ensure!(record["storage"] == storage, "ambiguous native storage");
                record["providers"]
                    .as_array_mut()
                    .unwrap()
                    .push(json!({"module":module,"provider":label}));
            }
        }
    }
    ensure!(
        wanted.iter().all(|s| objects.contains_key(s)),
        "unresolved native external object storage: {:?}",
        wanted.difference(&objects.keys().cloned().collect())
    );
    Ok(json!(objects))
}
fn verify_storage(objects: &serde_json::Value, map: &str) -> Result<()> {
    for (symbol, record) in objects.as_object().unwrap() {
        let rows = Regex::new(&format!(
            r"(?m)^([CD]):\s+[0-9A-Fa-f]+\s+_{}\s+(\S+)\s*$",
            regex::escape(symbol)
        ))?;
        let rows: Vec<_> = rows.captures_iter(map).collect();
        ensure!(rows.len() == 1, "missing final native object: {symbol}");
        ensure!(
            &rows[0][1]
                == if record["storage"] == "CODE" {
                    "C"
                } else {
                    "D"
                },
            "native object storage changed"
        );
        ensure!(
            record["providers"]
                .as_array()
                .unwrap()
                .iter()
                .any(|p| p["module"] == rows[0][2]),
            "native provider changed"
        );
    }
    Ok(())
}
fn verify_heap_map(d: &Driver, map: &str, heap: &Path) -> Result<()> {
    let stack = u32::from_str_radix(&d.stack["STACK_LOC"][2..], 16)?;
    let pattern = Regex::new(r"(?m)^SSEG\s+([0-9A-Fa-f]+)\s+([0-9A-Fa-f]+)")?;
    let m = pattern.captures(map).context("missing final stack area")?;
    ensure!(
        u32::from_str_radix(&m[1], 16)? == stack,
        "stack location changed"
    );
    let size = u32::from_str_radix(d.stack["STACK_SIZE"].trim_start_matches("0x"), 16)?;
    ensure!(
        u32::from_str_radix(&m[2], 16)? == size,
        "stack size changed"
    );
    let heap_symbols = ["___sdcc_heap", "___sdcc_heap_size32", "___stcxx_heap_init"];
    for name in heap_symbols {
        ensure!(
            Regex::new(&format!(r"(?m)\b{}\s+stcxx_heap\s*$", regex::escape(name)))?
                .find_iter(map)
                .count()
                == 1,
            "missing or duplicate final heap symbol: {name}"
        );
    }
    nonempty(heap)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn optimization_zero_retains_conservative_pipeline() {
        assert_eq!(
            optimization_passes("0", None),
            "internalize,deadargelim,globaldce"
        );
        assert_eq!(
            optimization_passes("z", Some("0")),
            "internalize,deadargelim,globaldce"
        );
        assert_eq!(
            optimization_passes("0", Some("z")),
            "internalize,deadargelim,default<Oz>"
        );
        for level in ["1", "2", "s", "z"] {
            assert_eq!(
                optimization_passes(level, None),
                "internalize,deadargelim,default<Oz>"
            );
        }
    }
    #[test]
    fn shared_vtables_require_identical_odr_initializers() {
        let first =
            "@_ZTV6Stream = linkonce_odr constant [1 x ptr] [ptr @method], comdat, align 1\n";
        assert_eq!(odr_definition(first, "_ZTV6Stream").unwrap(), first.trim());
        assert!(odr_definition(&first.replace("linkonce_odr ", ""), "_ZTV6Stream").is_err());
        assert_ne!(
            odr_definition(first, "_ZTV6Stream").unwrap(),
            odr_definition(&first.replace("@method", "@other"), "_ZTV6Stream").unwrap()
        );
    }
}
