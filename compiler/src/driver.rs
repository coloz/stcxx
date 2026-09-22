use crate::util::*;
use anyhow::{Context, Result, bail, ensure};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
};

pub struct Driver {
    pub platform: PathBuf,
    pub standalone: bool,
    pub source: PathBuf,
    pub output: Option<PathBuf>,
    pub mode: String,
    pub arguments: Vec<String>,
    pub lock: Value,
    pub frontend: PathBuf,
    pub sdcc: PathBuf,
    pub assembler: PathBuf,
    pub includes: PathBuf,
    pub runtime: PathBuf,
    pub triple: String,
    pub layout: String,
    pub abi: String,
    pub clang_args: Vec<String>,
    pub sdcc_args: Vec<String>,
    pub sections: Vec<String>,
    pub dependency: Option<PathBuf>,
    pub optimization: String,
    pub link_optimization: Option<String>,
    pub stack: BTreeMap<String, String>,
    pub clock: String,
    pub constrained: bool,
}
impl Driver {
    pub fn new(
        platform: PathBuf,
        sdcc: &str,
        source: &str,
        output: &str,
        mark: &str,
        arguments: Vec<String>,
    ) -> Result<Self> {
        Self::configured(platform, sdcc, source, output, mark, arguments, false)
    }
    pub fn standalone(
        sdk: PathBuf,
        sdcc: &str,
        source: &str,
        output: &str,
        mark: &str,
        arguments: Vec<String>,
    ) -> Result<Self> {
        Self::configured(sdk, sdcc, source, output, mark, arguments, true)
    }
    fn configured(
        platform: PathBuf,
        sdcc: &str,
        source: &str,
        output: &str,
        mark: &str,
        arguments: Vec<String>,
        standalone: bool,
    ) -> Result<Self> {
        let mode = match (mark, source.ends_with(".c")) {
            ("re1", true) => "compile-c",
            ("re2", false) => "compile-cpp",
            ("re3", _) => "compile-asm",
            ("re11", _) => "preprocess-deps",
            ("re12", _) => "preprocess-macros",
            ("link", _) => "link",
            _ => bail!("unsupported compile input: {mark} {source}"),
        };
        if mode != "link" {
            ensure!(
                source.ends_with(".c")
                    || source.ends_with(".cpp")
                    || source.ends_with(".cc")
                    || source.ends_with(".cxx")
                    || source.ends_with(".cpp.merged")
                    || source.ends_with(".S") && mode == "compile-asm",
                "unsupported source: {source}"
            );
            nonempty(source)?;
        }
        let sdcc = if standalone {
            sdcc.into()
        } else {
            std::env::var("STCXX_SDCC").unwrap_or(sdcc.into())
        };
        let sdcc = absolute(if Path::new(&sdcc).is_file() {
            sdcc
        } else {
            sdcc + std::env::consts::EXE_SUFFIX
        })?;
        let sdcc_root = sdcc
            .parent()
            .and_then(Path::parent)
            .context("invalid SDCC path")?;
        let package = (!standalone)
            .then(|| std::env::var_os("STCXX_TOOLS_ROOT"))
            .flatten()
            .map(PathBuf::from)
            .unwrap_or(
                sdcc_root
                    .parent()
                    .context("missing package root")?
                    .to_path_buf(),
            );
        let frontend = (!standalone)
            .then(|| std::env::var_os("STCXX_CPP_TOOLS_ROOT"))
            .flatten()
            .map(PathBuf::from)
            .unwrap_or(package.join("frontend"));
        let lock_dir = platform.join(if standalone {
            "locks"
        } else {
            "tools/stcxx-driver"
        });
        let lock = json(lock_dir.join(if cfg!(windows) {
            "toolchain-lock.windows-x86_64.json"
        } else {
            "toolchain-lock.macos-arm64.json"
        }))?;
        let target = &lock["targets"]["mcs251"];
        let mut d = Self {
            platform,
            standalone,
            source: absolute(source)?,
            output: if ["nul", "nul:", "/dev/null"].contains(&output.to_lowercase().as_str()) {
                None
            } else {
                Some(absolute(output)?)
            },
            mode: mode.into(),
            arguments,
            frontend,
            assembler: exe(&sdcc_root.join("bin"), "sdas251"),
            includes: sdcc_root.join("include"),
            runtime: sdcc_root.join("lib/mcs251-large-stack-auto"),
            triple: string(&target["target_triple"])?.into(),
            layout: string(&target["data_layout"])?.into(),
            abi: string(&target["abi_identity_symbol"])?.into(),
            sdcc,
            lock,
            clang_args: Vec::new(),
            sdcc_args: Vec::new(),
            sections: Vec::new(),
            dependency: None,
            optimization: "z".into(),
            link_optimization: None,
            stack: BTreeMap::new(),
            clock: String::new(),
            constrained: false,
        };
        d.parse()?;
        Ok(d)
    }
    fn parse(&mut self) -> Result<()> {
        let mut opt_count = 0;
        let mut clock_count = 0;
        let mut constrained_count = 0;
        let mut cpp = false;
        let mut target = false;
        let mut args = self.arguments.iter();
        while let Some(a) = args.next() {
            if a == "-MF" {
                self.dependency = Some(absolute(args.next().context("missing -MF destination")?)?);
                continue;
            }
            if let Some(level) = a.strip_prefix("--stcxx-warnings=") {
                if level == "none" {
                    // The locked SDCC has no -w. setWarningDisabled ignores
                    // error entries, so even this complete diagnostic range
                    // cannot suppress compiler errors. SDCpp has its own -w.
                    self.sdcc_args.push("-Wp-w".into());
                    for code in 0..=361 {
                        self.sdcc_args.push(format!("--disable-warning={code}"));
                    }
                }
                let flags: &[&str] = match level {
                    "none" => &["-w"],
                    "default" => &[],
                    "more" => &["-Wall"],
                    "all" => &["-Wall", "-Wextra"],
                    _ => bail!("invalid Arduino warning level: {level}"),
                };
                self.clang_args.extend(flags.iter().map(|s| (*s).into()));
                continue;
            }
            if a == "-DSTCXX_CPP_OPT" || a.starts_with("-DSTCXX_CPP_OPT=") {
                opt_count += 1;
                self.optimization = a.split_once('=').map(|p| p.1).unwrap_or("").into();
                ensure!(
                    opt_count == 1
                        && ["0", "1", "2", "s", "z"].contains(&self.optimization.as_str()),
                    "invalid STCXX_CPP_OPT"
                );
                continue;
            }
            if a == "-DSTCXX_LINK_OPT" || a.starts_with("-DSTCXX_LINK_OPT=") {
                let level = a.split_once('=').map(|p| p.1).unwrap_or("");
                ensure!(
                    self.link_optimization.is_none() && ["0", "z"].contains(&level),
                    "invalid STCXX_LINK_OPT"
                );
                self.link_optimization = Some(level.into());
                continue;
            }
            ensure!(
                !["-mmcs51", "-DSTCXX_TARGET_MCS51=1", "-DSTC16F40K128"].contains(&a.as_str())
                    && !a.starts_with("-DSTC_EXECUTION_MODE_MCS51"),
                "MCS51 target has been removed"
            );
            if let Some(kv) = a.strip_prefix("-DSTCXX_MCS251_")
                && let Some((key, value)) = kv.split_once('=')
                && ["IRAM_SIZE", "STACK_LOC", "STACK_SIZE"].contains(&key)
            {
                ensure!(
                    self.stack.insert(key.into(), value.into()).is_none(),
                    "duplicate stack setting"
                );
                continue;
            }
            if a.starts_with("-DSTCXX_MCS251_CONSTRAINED_HEAP=") {
                ensure!(a.ends_with("=1"), "invalid constrained heap");
                self.constrained = true;
                constrained_count += 1;
            }
            if let Some(value) = a.strip_prefix("-DF_CPU=") {
                clock_count += 1;
                self.clock = value.trim_end_matches(['U', 'L']).into();
            }
            if a == "-mmcs251" {
                target = true;
                self.sdcc_args.push(a.clone());
            } else if a == "-Ddouble=float" {
            } else if a.starts_with("-D") {
                cpp |= a == "-DSTCXX_CPP_CORE=1";
                self.clang_args.push(a.clone());
                self.sdcc_args.push(a.clone());
            } else if let Some(include) = a.strip_prefix("-I") {
                let p = absolute(include)?;
                let value = format!("-I{}", s(&p));
                if !p.starts_with(&self.includes) {
                    self.clang_args.push(value.clone());
                }
                self.sdcc_args.push(value);
            } else if ["--function-sections", "--data-sections"].contains(&a.as_str()) {
                self.sections.push(a.clone());
                self.sdcc_args.push(a.clone());
            } else if [
                "-c",
                "--std-sdcc11",
                "--opt-code-size",
                "--less-pedantic",
                "--stack-auto",
                "--model-large",
                "-MMD",
                "-Wp-Wall",
                "-V",
            ]
            .contains(&a.as_str())
            {
                self.sdcc_args.push(a.clone());
            } else if ["-M", "-MG", "-MP", "-E", "-dM"].contains(&a.as_str()) {
            } else if a == "-w" || a.starts_with("-W") && !a.starts_with("-Wp") {
                self.clang_args.push(a.clone());
                if self.mode == "compile-c" && a != "-w" {
                    self.sdcc_args.push(a.clone());
                }
            } else if self.source.extension().is_some_and(|e| e == "c") {
                self.sdcc_args.push(a.clone());
            } else if self.mode != "link" {
                bail!("unsupported Arduino compiler argument: {a}");
            }
        }
        ensure!(cpp && target, "MCS251 C++ profile is required");
        ensure!(
            clock_count == 1
                && (self.clock == "12000000"
                    || self.clock == "40000000"
                        && self.arguments.contains(&"-DAI8051U_34K64".into())
                    || self.clock == "48000000"
                        && self.arguments.contains(&"-DSTC32G144K246".into())),
            "unsupported C++ clock/profile"
        );
        ensure!(
            constrained_count <= 1,
            "duplicate constrained heap identity"
        );
        for flag in [
            "-DSTCXX_TARGET_ABI=1",
            "-DSTCXX_TARGET_MCS251=1",
            "-DSTCXX_TARGET_ENDIAN_LITTLE=0",
            "-DSTCXX_TARGET_ENDIAN_BIG=1",
        ] {
            self.clang_args.push(flag.into());
            self.sdcc_args.push(flag.into());
        }
        if self.mode == "link" {
            ensure!(self.stack.len() == 3, "missing extended stack layout");
            for v in self.stack.values() {
                ensure!(
                    v.starts_with("0x") && u32::from_str_radix(&v[2..], 16).is_ok(),
                    "invalid stack layout"
                );
            }
        }
        Ok(())
    }
    pub fn tool(&self, name: &str) -> PathBuf {
        exe(&self.frontend.join("bin"), name)
    }
    pub fn headers(&self) -> PathBuf {
        self.platform.join(if self.standalone {
            "runtime/include"
        } else {
            "cores/STC/runtime/include"
        })
    }
    pub fn call(&self, name: &str, args: &[String], capture: bool) -> Result<Vec<u8>> {
        run(&self.tool(name), args, None, capture, None)
    }
    pub fn verify(&self) -> Result<()> {
        let mut verifier = HashVerifier::default();
        self.verify_with(&mut verifier)
    }
    pub fn verify_with(&self, verifier: &mut HashVerifier) -> Result<()> {
        if let Some(files) = self.lock["frontend_files"].as_object() {
            for (name, expected) in files {
                safe_relative(name)?;
                verifier.check(self.frontend.join(name), string(expected)?)?;
            }
        } else if let Some(expected) = self.lock["frontend_manifest_sha256"].as_str() {
            let manifest = self.frontend.join("SOURCE-MANIFEST.sha256");
            let manifest = if manifest.is_file() {
                manifest
            } else {
                self.frontend.join("MANIFEST.sha256")
            };
            verifier.check(&manifest, expected)?;
            for line in text(&manifest)?.lines() {
                let (hash, name) = line.split_once("  ").context("invalid frontend manifest")?;
                safe_relative(name)?;
                if name.starts_with("bin/") || name.starts_with("lib/") {
                    verifier.check(self.frontend.join(name), hash)?;
                }
            }
        }
        for name in ["clang", "llvm-link", "opt", "llvm-dis", "llvm-cbe"] {
            verifier.check(
                self.tool(name),
                string(&self.lock["tools"][name.replace('-', "_")]["sha256"])?,
            )?;
        }
        let root = self.sdcc.parent().unwrap().parent().unwrap();
        for name in ["sdcc", "sdar", "sdas251", "sdld", "sdldmcs251", "sdcpp"] {
            verifier.check(
                exe(&root.join("bin"), name),
                string(&self.lock["tools"][name]["sha256"])?,
            )?;
        }
        if let Some(files) = self
            .lock
            .get("sdcc_files")
            .unwrap_or(&self.lock["windows_sdcc_files"])
            .as_object()
        {
            for (name, expected) in files {
                safe_relative(name)?;
                verifier.check(root.join(name), string(expected)?)?;
            }
        }
        for (name, key) in [("stddef.h", "stddef_sha256"), ("stdint.h", "stdint_sha256")] {
            verifier.check(
                self.includes.join(name),
                string(&self.lock["tools"]["sdcc_inputs"][key])?,
            )?;
        }
        for (name, key) in [
            ("libsdcc.lib", "libsdcc_sha256"),
            ("mcs251.lib", "target_runtime_sha256"),
        ] {
            verifier.check(
                self.runtime.join(name),
                string(&self.lock["targets"]["mcs251"]["sdcc_inputs"][key])?,
            )?;
        }
        Ok(())
    }
    pub fn clang_base(&self) -> Result<Vec<String>> {
        let resource =
            String::from_utf8(self.call("clang", &["--print-resource-dir".into()], true)?)?;
        let resource = PathBuf::from(resource.trim());
        let headers = self.headers();
        ensure!(
            resource.join("include").is_dir() && headers.is_dir(),
            "missing C++ headers"
        );
        let mut args = vec![
            format!("--target={}", self.triple),
            "-x".into(),
            "c++".into(),
            "-std=gnu++11".into(),
            format!("-O{}", self.optimization),
        ];
        args.extend(
            [
                "-fno-vectorize",
                "-fno-slp-vectorize",
                "-ffreestanding",
                "-fno-builtin",
                "-funsigned-char",
                "-fno-exceptions",
                "-fno-rtti",
                "-fno-threadsafe-statics",
                "-fno-use-cxa-atexit",
                "-fno-c++-static-destructors",
                "-fno-unwind-tables",
                "-fno-asynchronous-unwind-tables",
                "-Xclang",
                "-mno-constructor-aliases",
                "-Xclang",
                "-disable-O0-optnone",
                "-nostdinc",
            ]
            .map(String::from),
        );
        args.extend([
            format!("-I{}", s(headers)),
            format!("-isystem{}", s(resource.join("include"))),
        ]);
        args.extend(self.clang_args.clone());
        Ok(args)
    }
    pub fn check_target(&self, ir: &str) -> Result<()> {
        for (k, v) in [("triple", &self.triple), ("datalayout", &self.layout)] {
            ensure!(
                ir.lines()
                    .filter(|l| l.starts_with(&format!("target {k} = ")))
                    .collect::<Vec<_>>()
                    == vec![format!("target {k} = \"{v}\"")],
                "IR target {k} mismatch"
            );
        }
        Ok(())
    }
    pub fn compile(&self) -> Result<()> {
        self.verify()?;
        self.compile_inner()
    }
    pub fn compile_with(&self, verifier: &mut HashVerifier) -> Result<()> {
        self.verify_with(verifier)?;
        self.compile_inner()
    }
    fn compile_inner(&self) -> Result<()> {
        if let Some(p) = &self.output {
            fs::create_dir_all(p.parent().unwrap())?;
        }
        if self.source.extension().is_some_and(|e| e == "c") && self.mode.starts_with("preprocess-")
        {
            return self.preprocess_c();
        }
        if self.mode == "compile-asm" {
            return self.assemble();
        }
        if self.mode == "compile-c" {
            return self.compile_c();
        }
        let mut args = self.clang_base()?;
        match self.mode.as_str() {
            "preprocess-deps" => {
                args.extend(["-M".into(), "-MG".into(), "-MP".into(), s(&self.source)]);
                self.call("clang", &args, false)?;
            }
            "preprocess-macros" => {
                if let Some(dep) = &self.dependency {
                    args.extend([
                        "-M".into(),
                        "-MP".into(),
                        "-MF".into(),
                        s(dep),
                        s(&self.source),
                    ]);
                    self.call("clang", &args, false)?;
                    nonempty(dep)?;
                } else if let Some(out) = &self.output {
                    args.extend([
                        "-E".into(),
                        "-CC".into(),
                        s(&self.source),
                        "-o".into(),
                        s(out),
                    ]);
                    self.call("clang", &args, false)?;
                } else {
                    args.extend(["-dM".into(), "-E".into(), s(&self.source)]);
                    self.call("clang", &args, false)?;
                }
            }
            "compile-cpp" => {
                let out = self.output.as_ref().context("missing object")?;
                let bc = suffix(out, ".stcxx.bc");
                let ir = suffix(out, ".stcxx.ll");
                let cbe = suffix(out, ".stcxx.module.cbe");
                remove(out)?;
                remove(suffix(out, ".stcxx.json"))?;
                args.extend([
                    "-emit-llvm".into(),
                    "-c".into(),
                    s(&self.source),
                    "-o".into(),
                    s(&bc),
                ]);
                let dep = self
                    .dependency
                    .clone()
                    .unwrap_or_else(|| out.with_extension("d"));
                args.extend(["-MMD".into(), "-MF".into(), s(dep), "-MT".into(), s(out)]);
                write(
                    suffix(out, ".stcxx-command.txt"),
                    args.iter().map(|a| quote(a)).collect::<Vec<_>>().join(" ") + "\n",
                )?;
                self.call("clang", &args, false)?;
                self.call("llvm-dis", &[s(&bc), "-o".into(), s(&ir)], false)?;
                let module_ir = text(&ir)?;
                self.check_target(&module_ir)?;
                crate::adapter::audit_stack_allocation(&module_ir)?;
                self.call("llvm-cbe", &[s(&bc), "-o".into(), s(&cbe)], false)?;
                let placeholder = suffix(out, ".stcxx-placeholder.c");
                let rel = out.with_extension("rel");
                write(
                    &placeholder,
                    format!(
                        "void __stcxx_placeholder_{}(void) {{}}\n",
                        &digest(s(&self.source) + "\n" + &s(out))[..16]
                    ),
                )?;
                let result = run(
                    &self.sdcc,
                    &[
                        "-mmcs251".into(),
                        "--model-large".into(),
                        "--stack-auto".into(),
                        "--std-sdcc11".into(),
                        "--opt-code-size".into(),
                        "--less-pedantic".into(),
                        "-c".into(),
                        s(&placeholder),
                        "-o".into(),
                        s(&rel),
                    ],
                    out.parent(),
                    false,
                    None,
                );
                remove(placeholder)?;
                result?;
                fs::copy(&rel, out)?;
                write_json(
                    suffix(out, ".stcxx.json"),
                    &json!({"schema_version":1,"source":s(&self.source),"source_sha256":hash(&self.source)?,"object":s(out),"object_sha256":hash(out)?,"bitcode":s(&bc),"bitcode_sha256":hash(&bc)?,"ir_sha256":hash(&ir)?,"cbe_sha256":hash(&cbe)?,"target_triple":self.triple,"data_layout":self.layout,"optimization":self.optimization}),
                )?;
            }
            _ => bail!("invalid compile mode"),
        }
        Ok(())
    }
    fn compile_c(&self) -> Result<()> {
        let out = self.output.as_ref().context("missing C object")?;
        for extension in [".stcxx.json", ".stcxx.bc", ".stcxx.ll", ".stcxx.module.cbe"] {
            remove(suffix(out, extension))?;
        }
        let rel = out.with_extension("rel");
        let generated_dep = rel.with_extension("d");
        let dep = self.dependency.as_ref().unwrap_or(&generated_dep);
        for path in [out, &rel, &generated_dep, dep] {
            remove(path)?;
        }
        let mut flags = self.sdcc_args.clone();
        for flag in ["--stack-auto", "-MMD"] {
            if !flags.iter().any(|arg| arg == flag) {
                flags.push(flag.into());
            }
        }
        let compile_flags = flags.clone();
        flags.extend([s(&self.source), "-o".into(), s(&rel)]);
        run(&self.sdcc, &flags, rel.parent(), false, None)?;
        // SDCC names the .rel in its dependency rule. Arduino caches the .o;
        // keep the canonical .d for source-closure metadata and honor -MF too.
        let dependency = retarget_dependency(&fs::read(&generated_dep)?, out)?;
        atomic_write(&generated_dep, &dependency)?;
        if dep != &generated_dep {
            fs::create_dir_all(dep.parent().context("missing dependency directory")?)?;
            atomic_write(dep, &dependency)?;
        }
        write_json(
            suffix(&rel, ".stcxx-c.json"),
            &json!({"schema_version":1,"source":s(&self.source),"source_sha256":hash(&self.source)?,"source_dependencies":crate::trim::source_dependencies(&rel)?,"original_rel":s(&rel),"original_rel_sha256":hash(&rel)?,"sdcc":s(&self.sdcc),"sdcc_flags":compile_flags,"clang":s(self.tool("clang")),"clang_flags":self.clang_args,"target_triple":self.triple}),
        )?;
        fs::copy(&rel, out)?;
        Ok(())
    }
    fn preprocess_c(&self) -> Result<()> {
        let mut args = Vec::new();
        let mut input = self.arguments.iter();
        while let Some(arg) = input.next() {
            if arg == "-MF" {
                input.next();
                continue;
            }
            if !arg.starts_with("--stcxx-warnings=") {
                args.push(arg.clone());
            }
        }
        args.extend(["-x".into(), "c".into(), s(&self.source)]);
        let tmp = tempfile::tempdir()?;
        run(&self.sdcc, &args, Some(tmp.path()), false, None)?;
        if let Some(dep) = &self.dependency {
            let generated = tmp
                .path()
                .join(self.source.file_name().unwrap())
                .with_extension("d");
            nonempty(&generated)?;
            atomic_write(dep, &fs::read(generated)?)?;
        }
        Ok(())
    }
    fn assemble(&self) -> Result<()> {
        let out = self.output.as_ref().context("missing assembly object")?;
        let rel = out.with_extension("rel");
        let asm = out.with_extension("asm");
        remove(out)?;
        remove(&rel)?;
        let dep = self
            .dependency
            .clone()
            .unwrap_or_else(|| out.with_extension("d"));
        let mut args = vec![
            format!("--target={}", self.triple),
            "-x".into(),
            "assembler-with-cpp".into(),
            "-E".into(),
            "-P".into(),
        ];
        args.extend(self.clang_args.clone());
        args.extend([
            "-MMD".into(),
            "-MF".into(),
            s(dep),
            "-MT".into(),
            s(out),
            s(&self.source),
            "-o".into(),
            s(&asm),
        ]);
        self.call("clang", &args, false)?;
        run(
            &self.assembler,
            &["-plosgffw".into(), s(&rel), s(&asm)],
            out.parent(),
            false,
            None,
        )?;
        nonempty(&rel)?;
        fs::copy(rel, out)?;
        Ok(())
    }
    pub fn link(&self) -> Result<()> {
        crate::link::pipeline(self)
    }
}
fn retarget_dependency(dependency: &[u8], output: &Path) -> Result<Vec<u8>> {
    // A drive letter is followed by a slash, whereas the make rule separator
    // is followed by whitespace. Preserve all prerequisites and continuations.
    let separator = dependency
        .windows(2)
        .position(|p| p[0] == b':' && p[1].is_ascii_whitespace())
        .context("missing C dependency target")?;
    // SDCC's output path differs from Arduino's only in the extension. Keep
    // its exact encoding, slash style and make escaping: Arduino decodes native
    // Windows depfiles with the ANSI code page and compares paths literally.
    let mut target = dependency[..separator]
        .strip_suffix(b".rel")
        .context("unexpected C dependency target extension")?
        .to_vec();
    if let Some(extension) = output.extension() {
        let extension = extension.to_str().context("non-UTF-8 object extension")?;
        ensure!(
            extension.chars().all(|c| c.is_ascii_alphanumeric()),
            "invalid object extension"
        );
        target.push(b'.');
        target.extend_from_slice(extension.as_bytes());
    }
    target.extend_from_slice(&dependency[separator..]);
    Ok(target)
}

pub fn size(path: &Path) -> Result<()> {
    let report = text(path)?;
    let re = regex::Regex::new(
        r"(?m)^\s*(ROM/EPROM/FLASH|PAGED EXT\. RAM|EXTERNAL RAM)\s+\S+\s+\S+\s+(\d+)",
    )?;
    let mut program = None;
    let mut ram = 0u64;
    for row in re.captures_iter(&report) {
        let n = row[2].parse::<u64>()?;
        if &row[1] == "ROM/EPROM/FLASH" {
            program = Some(n);
        } else {
            ram += n;
        }
    }
    let stack = regex::Regex::new(r"Stack starts at: 0x([0-9A-Fa-f]+)")?;
    if let Some(m) = stack.captures(&report) {
        ram += u64::from_str_radix(&m[1], 16)?;
    } else {
        for line in report.lines().filter(|l| l.starts_with("0x")) {
            ram += line
                .split('|')
                .skip(1)
                .filter(|s| !s.trim().is_empty())
                .count() as u64;
        }
    }
    println!(
        "STC_PROGRAM_BYTES {}\nSTC_RAM_BYTES {ram}",
        program.context("unrecognized memory report")?
    );
    Ok(())
}

pub fn advanced_size(path: &Path) -> Result<()> {
    let report = text(path)?;
    let mut sections = Vec::new();
    let mut output = String::new();
    let mut exceeded = false;
    let rows = regex::Regex::new(
        r"(?m)^\s*(ROM/EPROM/FLASH|PAGED EXT\. RAM|EXTERNAL RAM)\s+(?:0x[0-9a-fA-F]+\s+0x[0-9a-fA-F]+\s+)?(\d+)\s+(\d+)",
    )?;
    for row in rows.captures_iter(&report) {
        let size = row[2].parse::<u64>()?;
        let max = row[3].parse::<u64>()?;
        let (name, label) = match &row[1] {
            "ROM/EPROM/FLASH" => ("text", "Program Flash"),
            "EXTERNAL RAM" => ("xdata", "XDATA (including reserved heap)"),
            _ => ("pdata", "PDATA"),
        };
        exceeded |= size > max;
        sections.push(json!({"name":name,"size":size,"max_size":max}));
        if name == "text" {
            output.insert_str(0, &format!("Sketch uses {size} bytes ({}%) of program storage space. Maximum is {max} bytes.\n", (size * 100).checked_div(max).unwrap_or(0)));
        } else if size != 0 {
            output += &format!("{label}: {size} / {max} bytes.\n");
        }
    }
    ensure!(
        sections.iter().any(|v| v["name"] == "text"),
        "unrecognized memory report"
    );
    let internal: u64 = report
        .lines()
        .filter(|l| l.starts_with("0x"))
        .map(|l| {
            l.split('|')
                .skip(1)
                .filter(|s| !s.trim().is_empty() && s.trim() != "S")
                .count() as u64
        })
        .sum();
    sections.push(json!({"name":"internal-static","size":internal,"max_size":256}));
    output += &format!("Internal DATA/IDATA static allocation: {internal} bytes.\n");
    let stack = regex::Regex::new(r"Stack starts at: 0x[0-9A-Fa-f]+.*with (\d+) bytes available")?;
    if let Some(row) = stack.captures(&report) {
        let size = row[1].parse::<u64>()?;
        sections.push(json!({"name":"stack-reserved","size":size,"max_size":size}));
        output += &format!("Stack reservation: {size} bytes; runtime peak is not measured.\n");
    }
    let manifest = path
        .parent()
        .context("missing report directory")?
        .join("stcxx/manifest.json");
    if manifest.is_file() {
        let meta = crate::util::json(manifest)?;
        let heap = text(string(&meta["heap"])?)?;
        let area = regex::Regex::new(r"(?m)^A XSEG size ([0-9A-Fa-f]+) flags ")?;
        if let Some(row) = area.captures(&heap) {
            let size = u64::from_str_radix(&row[1], 16)?;
            sections.push(json!({"name":"heap-reserved","size":size,"max_size":size}));
            output += &format!(
                "Heap reservation: {size} bytes within XDATA; runtime free heap is not measured.\n"
            );
        }
    }
    let mut result =
        json!({"output":output,"severity":if exceeded {"error"} else {"info"},"sections":sections});
    if exceeded {
        result["error"] = json!("Program exceeds a target memory region");
    }
    println!("{}", serde_json::to_string(&result)?);
    Ok(())
}

#[cfg(test)]
mod dependency_tests {
    use super::*;
    #[test]
    fn c_dependencies_preserve_path_spelling_and_make_escaping() {
        let prerequisites = ": \\\n C:/中文\\ path/source.c \\\n C:/headers/value.h\n";
        for target in [r"C:\中文\a.c", r"C:/中文\ path/a\#$$b.c", "/tmp/source.c"] {
            let input = format!("{target}.rel{prerequisites}");
            assert_eq!(
                retarget_dependency(input.as_bytes(), Path::new("out.o")).unwrap(),
                format!("{target}.o{prerequisites}").as_bytes()
            );
        }
        assert_eq!(
            retarget_dependency(b"C:\\legacy\xb2\xe2.rel: source.c\n", Path::new("out.o")).unwrap(),
            b"C:\\legacy\xb2\xe2.o: source.c\n"
        );
        assert!(retarget_dependency(b"invalid", Path::new("out.o")).is_err());
    }
}
