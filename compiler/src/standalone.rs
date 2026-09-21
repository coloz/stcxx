use crate::{cache, driver::Driver, util::*};
use anyhow::{Context, Result, bail, ensure};
use serde_json::{Value, json};
use std::{
    fs,
    path::{Path, PathBuf},
};

pub const HELP: &str = "STCXX standalone C/C++ compiler
Usage: stcxx --chip <id> [options] main.cpp [other.c | object.o | library.a] -o firmware.hex
       stcxx --chip <id> -c source.cpp -o object.o
       stcxx --list-chips
Options:
  --toolchain <directory>  Native package containing frontend/ and sdcc/
  --sdk <directory>        STCXX SDK (normally found beside the installed driver)
  -I<directory> -D<macro>  Include directories and preprocessor definitions
  -O0 | -Oz               C++ optimization (default: Oz)
  -L<directory> -l<name>   Additional libraries at link time
  --interrupts <header>    SDCC ISR declarations included by the startup unit
  -v, --verbose           Show underlying compiler commands
The application entry is int main(void). C++11 is freestanding, without exceptions
or RTTI. Keep .o files together with their generated .rel and .stcxx.* sidecars.
Arduino recipe operations remain available through --platform <directory>.";

#[derive(Default, Debug)]
struct Options {
    chip: Option<String>,
    sdk: Option<PathBuf>,
    toolchain: Option<PathBuf>,
    output: Option<PathBuf>,
    inputs: Vec<PathBuf>,
    flags: Vec<String>,
    libraries: Vec<String>,
    interrupts: Option<PathBuf>,
    compile_only: bool,
    list: bool,
    verbose: bool,
    optimization: Option<String>,
}

fn parse(args: &[String]) -> Result<Options> {
    let mut o = Options::default();
    let mut i = 0;
    while i < args.len() {
        let a = &args[i];
        let mut next = || -> Result<&str> {
            i += 1;
            args.get(i)
                .map(String::as_str)
                .with_context(|| format!("missing value for {a}"))
        };
        match a.as_str() {
            "--chip" => {
                ensure!(o.chip.is_none(), "duplicate --chip");
                o.chip = Some(next()?.to_ascii_lowercase());
            }
            "--sdk" => {
                ensure!(o.sdk.is_none(), "duplicate --sdk");
                o.sdk = Some(absolute(next()?)?);
            }
            "--toolchain" => {
                ensure!(o.toolchain.is_none(), "duplicate --toolchain");
                o.toolchain = Some(absolute(next()?)?);
            }
            "-o" => {
                ensure!(o.output.is_none(), "duplicate -o");
                o.output = Some(absolute(next()?)?);
            }
            "--interrupts" => {
                ensure!(o.interrupts.is_none(), "duplicate --interrupts");
                o.interrupts = Some(absolute(next()?)?);
            }
            "-c" => o.compile_only = true,
            "--list-chips" => o.list = true,
            "-v" | "--verbose" => o.verbose = true,
            "-O0" | "-Oz" => {
                ensure!(o.optimization.is_none(), "duplicate optimization");
                o.optimization = Some(a[2..].into());
            }
            "-I" | "-D" | "-L" | "-l" => {
                let value = format!("{a}{}", next()?);
                add_flag(&mut o, &value)?;
            }
            "-std=c11" | "-std=gnu11" | "-std=c++11" | "-std=gnu++11" => {}
            "--" => {
                o.inputs.extend(
                    args[i + 1..]
                        .iter()
                        .map(absolute)
                        .collect::<Result<Vec<_>>>()?,
                );
                break;
            }
            _ if ["-I", "-D", "-L", "-l"].iter().any(|p| a.starts_with(p)) => add_flag(&mut o, a)?,
            _ if a.starts_with('-') => bail!("unsupported standalone option: {a}"),
            _ => o.inputs.push(absolute(a)?),
        }
        i += 1;
    }
    Ok(o)
}

fn add_flag(o: &mut Options, a: &str) -> Result<()> {
    ensure!(a.len() > 2, "missing flag value: {a}");
    if let Some(def) = a.strip_prefix("-D") {
        let name = def.split('=').next().unwrap();
        ensure!(
            !name.starts_with("STCXX_")
                && !name.starts_with("__")
                && !["main", "double", "F_CPU", "ARDUINO"].contains(&name),
            "reserved SDK definition: {name}"
        );
        o.flags.push(a.into());
    } else if a.starts_with("-I") {
        o.flags.push(format!("-I{}", s(absolute(&a[2..])?)));
    } else if a.starts_with("-L") {
        o.libraries.push(format!("-L{}", s(absolute(&a[2..])?)));
    } else {
        o.libraries.push(a.into());
    }
    Ok(())
}

pub fn sdk_root(explicit: Option<PathBuf>) -> Result<PathBuf> {
    if let Some(root) = explicit.or_else(|| std::env::var_os("STCXX_SDK_ROOT").map(PathBuf::from)) {
        let root = absolute(root)?;
        ensure!(
            root.join("targets.json").is_file(),
            "missing SDK targets.json: {}",
            root.display()
        );
        return Ok(root);
    }
    let exe = std::env::current_exe()?;
    let installed = exe.parent().unwrap().join("../share/stcxx/sdk");
    if installed.join("targets.json").is_file() {
        return absolute(installed);
    }
    let development = Path::new(env!("CARGO_MANIFEST_DIR")).join("../sdk");
    let development_layout = exe.parent().unwrap().join("../../Cargo.toml").is_file()
        || exe.parent().unwrap().join("Cargo.toml").is_file();
    ensure!(
        development_layout && development.join("targets.json").is_file(),
        "SDK not found; install the full STCXX package or specify --sdk"
    );
    absolute(development)
}

fn number(target: &Value, key: &str) -> Result<u64> {
    target[key]
        .as_u64()
        .with_context(|| format!("invalid target field: {key}"))
}

fn target_flags(target: &Value, sdk: &Path, optimization: &str) -> Result<Vec<String>> {
    ensure!(target["target"] == "mcs251", "unsupported SDK target");
    let edata = number(target, "edata_bytes")?;
    let heap = number(target, "cpp_heap_bytes")?;
    ensure!(
        (0x101..=0x10000).contains(&edata),
        "invalid target stack capacity"
    );
    ensure!(
        (4096..=32768).contains(&heap) && heap < number(target, "xdata_bytes")?,
        "invalid target heap capacity"
    );
    let mut flags = vec![
        "-mmcs251".into(),
        "--model-large".into(),
        "--stack-auto".into(),
        "-DSTCXX_CPP_CORE=1".into(),
        "-DSTCXX_STANDALONE=1".into(),
        "-DSTCXX_FLASH_STRINGS=0".into(),
        "-DSTCXX_ENFORCE_NO_EXCEPTIONS_RTTI=1".into(),
        format!("-DSTCXX_CPP_OPT={optimization}"),
        format!("-DSTCXX_HEAP_SIZE={heap}UL"),
        format!("-D{}", string(&target["macro"])?),
        format!("-DF_CPU={}UL", number(target, "default_clock_hz")?),
        format!("-I{}", s(sdk.join("runtime"))),
    ];
    if target["linker"]["layout"] == "segmented_home" {
        flags.extend(["--function-sections".into(), "--data-sections".into()]);
    }
    Ok(flags)
}

fn link_flags(target: &Value) -> Result<Vec<String>> {
    let edata = number(target, "edata_bytes")?;
    let linker = &target["linker"];
    let origin = number(linker, "code_loc")?;
    ensure!(origin == 0xff0000, "invalid reset origin");
    let mut flags = vec![
        "--code-loc".into(),
        format!("0x{origin:x}"),
        "--code-size".into(),
        number(target, "maximum_code_bytes")?.to_string(),
        "--xram-size".into(),
        number(target, "xdata_bytes")?.to_string(),
        format!("-DSTCXX_MCS251_IRAM_SIZE=0x{edata:x}"),
        "-DSTCXX_MCS251_STACK_LOC=0x100".into(),
        format!("-DSTCXX_MCS251_STACK_SIZE=0x{:x}", edata - 0x100),
    ];
    if linker["layout"] == "segmented_home" {
        let start = number(linker, "gsinit0_loc")?;
        ensure!(
            start < origin && start + number(target, "flash_bytes")? == 0x1000000,
            "invalid segmented Flash window"
        );
        flags.extend([
            format!("-Wl-b GSINIT0=0x{start:x}"),
            format!("-Wl--code-window=0x{start:x}:0x1000000"),
        ]);
    } else {
        ensure!(
            linker["layout"] == "post_home_contiguous"
                && origin + number(target, "flash_bytes")? <= 0x1000000,
            "invalid contiguous Flash window"
        );
    }
    Ok(flags)
}

pub fn verification_flags(target: &Value, sdk: &Path) -> Result<Vec<String>> {
    let mut flags = target_flags(target, sdk, "z")?;
    flags.extend(link_flags(target)?);
    Ok(flags)
}

fn compile(
    sdk: &Path,
    sdcc: &Path,
    source: &Path,
    object: &Path,
    flags: &[String],
    application: bool,
    verifier: &mut HashVerifier,
) -> Result<()> {
    let cpp = matches!(
        source.extension().and_then(|x| x.to_str()),
        Some("cpp" | "cc" | "cxx")
    );
    ensure!(
        cpp || source.extension().is_some_and(|x| x == "c"),
        "unsupported source file: {}",
        source.display()
    );
    let mut args = flags.to_vec();
    args.extend([
        "-c".into(),
        "--std-sdcc11".into(),
        "--opt-code-size".into(),
        "--less-pedantic".into(),
    ]);
    let mut driver = Driver::standalone(
        sdk.into(),
        &s(sdcc),
        &s(source),
        &s(object),
        if cpp { "re2" } else { "re1" },
        args,
    )?;
    if application {
        if cpp {
            driver
                .clang_args
                .extend(["-include".into(), s(sdk.join("entry.h"))]);
        } else {
            driver.sdcc_args.push("-Dmain=__stcxx_user_main".into());
        }
    }
    fs::create_dir_all(object.parent().context("object has no directory")?)?;
    driver.compile_with(verifier)
}

pub fn run(args: &[String]) -> Result<()> {
    let o = parse(args)?;
    set_command_logging(o.verbose);
    let sdk = sdk_root(o.sdk)?;
    let catalog = json(sdk.join("targets.json"))?;
    ensure!(
        catalog["schema_version"] == 1,
        "unsupported SDK catalog version"
    );
    let targets = catalog["targets"]
        .as_array()
        .context("missing SDK targets")?;
    if o.list {
        ensure!(
            o.inputs.is_empty(),
            "--list-chips does not accept source files"
        );
        for t in targets {
            println!(
                "{}\t{}\t{} bytes Flash",
                string(&t["id"])?,
                string(&t["model"])?,
                number(t, "flash_bytes")?
            );
        }
        return Ok(());
    }
    let chip = o
        .chip
        .context("specify --chip; use --list-chips for supported models")?;
    let target = targets
        .iter()
        .find(|t| t["id"].as_str() == Some(&chip))
        .with_context(|| format!("unknown chip: {chip}"))?;
    ensure!(!o.inputs.is_empty(), "no input files");
    let output = o.output.context("specify -o <object.o or firmware.hex>")?;
    ensure!(
        output
            .extension()
            .is_some_and(|x| x == if o.compile_only { "o" } else { "hex" }),
        "use .o with -c and .hex when linking"
    );
    ensure!(
        !o.inputs.contains(&output),
        "output would overwrite an input"
    );
    for input in &o.inputs {
        nonempty(input)?;
    }
    let exe = std::env::current_exe()?;
    let tools = absolute(
        o.toolchain
            .or_else(|| std::env::var_os("STCXX_TOOLS_ROOT").map(PathBuf::from))
            .unwrap_or_else(|| exe.parent().unwrap().join("..")),
    )?;
    let sdcc = exe_path(&tools.join("sdcc/bin"), "sdcc");
    ensure!(
        sdcc.is_file() && tools.join("frontend/bin").is_dir(),
        "native compiler package not found; specify --toolchain <directory>"
    );
    // SDCC's generated assembler/linker command files do not quote every path.
    for p in o.inputs.iter().chain([&output, &tools, &sdk]) {
        ensure!(
            !s(p).chars().any(char::is_whitespace),
            "SDCC requires paths without whitespace: {}",
            p.display()
        );
    }
    let mut common = target_flags(target, &sdk, o.optimization.as_deref().unwrap_or("z"))?;
    let mut verifier = HashVerifier::default();
    if o.compile_only {
        ensure!(
            o.inputs.len() == 1 && o.libraries.is_empty() && o.interrupts.is_none(),
            "-c accepts one source and no link options"
        );
        common.extend(o.flags);
        return compile(
            &sdk,
            &sdcc,
            &o.inputs[0],
            &output,
            &common,
            true,
            &mut verifier,
        );
    }
    // Invalidate previous success before any compiler can fail.
    remove(&output)?;
    remove(output.with_extension("stcxx").join("manifest.json"))?;
    remove(output.with_extension("build.json"))?;
    fs::create_dir_all(output.parent().unwrap())?;
    let build = tempfile::Builder::new()
        .prefix("stcxx-build-")
        .tempdir_in(output.parent().unwrap())?
        .keep();
    let runtime_dir = build.join("runtime");
    fs::create_dir_all(&runtime_dir)?;
    let archive = runtime_dir.join("core.a");
    let sdar = exe_path(&tools.join("sdcc/bin"), "sdar");
    let files = json(sdk.join("runtime-files.json"))?;
    for name in files["files"]
        .as_array()
        .context("missing runtime source inventory")?
    {
        let name = string(name)?;
        safe_relative(name)?;
        if !name.ends_with(".c") && !name.ends_with(".cpp") {
            continue;
        }
        let source = sdk.join("runtime").join(name);
        let object = runtime_dir.join(format!(
            "{}.o",
            source.file_name().unwrap().to_string_lossy()
        ));
        compile(&sdk, &sdcc, &source, &object, &common, false, &mut verifier)?;
        cache::archive(&sdar, &archive, &object, &["rcs".into()])?;
    }
    let console = runtime_dir.join("console.c.o");
    compile(
        &sdk,
        &sdcc,
        &sdk.join("console.c"),
        &console,
        &common,
        false,
        &mut verifier,
    )?;
    cache::archive(&sdar, &archive, &console, &["rcs".into()])?;
    let mut startup = text(sdk.join("startup.c"))?;
    if let Some(header) = o.interrupts {
        nonempty(&header)?;
        let header = s(header).replace('\\', "/");
        ensure!(
            !header.contains(['"', '\n', '\r']),
            "invalid interrupt header path"
        );
        startup = format!("#include \"{header}\"\n{startup}");
    }
    let startup_source = build.join("startup.c");
    write(&startup_source, startup)?;
    let startup_object = build.join("startup.c.o");
    let mut application_flags = common.clone();
    application_flags.extend(o.flags);
    compile(
        &sdk,
        &sdcc,
        &startup_source,
        &startup_object,
        &application_flags,
        false,
        &mut verifier,
    )?;
    let mut objects = vec![s(&startup_object)];
    for (i, source) in o.inputs.iter().enumerate() {
        match source.extension().and_then(|x| x.to_str()) {
            Some("o" | "a" | "lib") => objects.push(s(source)),
            Some("c" | "cpp" | "cc" | "cxx") => {
                let object = build.join(format!("unit_{i}.o"));
                compile(
                    &sdk,
                    &sdcc,
                    source,
                    &object,
                    &application_flags,
                    true,
                    &mut verifier,
                )?;
                objects.push(s(object));
            }
            _ => bail!("unsupported input: {}", source.display()),
        }
    }
    common.extend(link_flags(target)?);
    common.extend(objects);
    common.push(s(&archive));
    common.extend(o.libraries);
    common.extend(["--out-fmt-ihx".into(), "-o".into(), s(&output)]);
    Driver::standalone(sdk.clone(), &s(&sdcc), "-", &s(&output), "link", common)?.link()?;
    write_json(
        output.with_extension("build.json"),
        &json!({"schema_version":1,"chip":chip,"sdk":s(&sdk),"toolchain":s(&tools),"build_directory":s(&build),"output":s(&output),"hex_sha256":hash(&output)?,"entry":"int main(void)"}),
    )?;
    println!("Firmware: {}", output.display());
    crate::driver::size(&output.with_extension("mem"))
}

fn exe_path(root: &Path, name: &str) -> PathBuf {
    crate::util::exe(root, name)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn command_line_rejects_abi_overrides_and_incomplete_options() {
        for args in [
            vec!["--chip"],
            vec!["-DSTCXX_TARGET_MCS51=1"],
            vec!["-Dmain=other"],
            vec!["-fexceptions"],
            vec!["-O0", "-Oz"],
        ] {
            assert!(parse(&args.into_iter().map(String::from).collect::<Vec<_>>()).is_err());
        }
        let o = parse(
            &[
                "--chip",
                "STC32G8K64",
                "-I",
                ".",
                "-D",
                "VALUE=7",
                "-c",
                "main.cpp",
                "-o",
                "main.o",
            ]
            .map(String::from),
        )
        .unwrap();
        assert_eq!(o.chip.as_deref(), Some("stc32g8k64"));
        assert!(o.compile_only);
        assert!(o.flags.contains(&"-DVALUE=7".into()));
    }
    #[test]
    fn all_shipped_targets_have_consistent_memory_layouts() {
        let catalog: Value = serde_json::from_str(include_str!("../../sdk/targets.json")).unwrap();
        let targets = catalog["targets"].as_array().unwrap();
        assert_eq!(targets.len(), 10);
        for target in targets {
            let flags = link_flags(target).unwrap();
            target_flags(target, Path::new("sdk"), "z").unwrap();
            assert!(flags.contains(&"--code-size".into()));
        }
    }
}
