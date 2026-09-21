use crate::util::*;
use anyhow::{Context, Result, ensure};
use serde_json::json;
use std::{collections::BTreeMap, fs, path::Path};
use zip::{CompressionMethod, ZipWriter, write::SimpleFileOptions};

fn copy_tree(source: &Path, destination: &Path) -> Result<()> {
    ensure!(
        source.is_dir(),
        "missing package input: {}",
        source.display()
    );
    for entry in walkdir::WalkDir::new(source).sort_by_file_name() {
        let entry = entry?;
        let path = entry.path();
        let relative = path.strip_prefix(source)?;
        if entry.file_type().is_dir() {
            continue;
        }
        ensure!(
            entry.file_type().is_file() || entry.file_type().is_symlink(),
            "non-regular package input: {}",
            path.display()
        );
        if entry.file_type().is_symlink() {
            ensure!(
                path.canonicalize()?.starts_with(source.canonicalize()?),
                "package link escapes component: {}",
                path.display()
            );
        }
        ensure!(
            !matches!(
                path.extension().and_then(|x| x.to_str()),
                Some("py" | "pyc" | "pyd")
            ),
            "interpreter payload in native package input: {}",
            path.display()
        );
        let target = destination.join(relative);
        write(&target, fs::read(path)?)?;
        fs::set_permissions(target, fs::metadata(path)?.permissions())?;
    }
    Ok(())
}
fn inventory(root: &Path) -> Result<BTreeMap<String, String>> {
    let mut result = BTreeMap::new();
    for entry in walkdir::WalkDir::new(root).sort_by_file_name() {
        let entry = entry?;
        if entry.file_type().is_file() {
            let name = s(entry.path().strip_prefix(root)?).replace('\\', "/");
            if name != "MANIFEST.sha256" {
                result.insert(name, hash(entry.path())?);
            }
        }
    }
    Ok(result)
}
fn manifest(root: &Path) -> Result<()> {
    write(
        root.join("MANIFEST.sha256"),
        inventory(root)?
            .iter()
            .map(|(name, hash)| format!("{hash}  {name}\n"))
            .collect::<String>(),
    )
}
fn archive(root: &Path, output: &Path, root_name: &str) -> Result<()> {
    ensure!(
        !output.exists(),
        "package output already exists: {}",
        output.display()
    );
    fs::create_dir_all(output.parent().context("missing output directory")?)?;
    manifest(root)?;
    let mut pending = tempfile::NamedTempFile::new_in(output.parent().unwrap())?;
    {
        let mut zip = ZipWriter::new(pending.as_file_mut());
        for entry in walkdir::WalkDir::new(root).sort_by_file_name() {
            let entry = entry?;
            if !entry.file_type().is_file() {
                continue;
            }
            let name = format!(
                "{root_name}/{}",
                s(entry.path().strip_prefix(root)?).replace('\\', "/")
            );
            safe_relative(&name)?;
            let mode = if entry.path().extension().is_some_and(|e| e == "exe")
                || entry.path().file_name().is_some_and(|n| n == "stcxx")
            {
                0o755
            } else {
                0o644
            };
            #[cfg(unix)]
            let mode = {
                use std::os::unix::fs::PermissionsExt;
                if entry.metadata()?.permissions().mode() & 0o111 != 0 {
                    0o755
                } else {
                    mode
                }
            };
            zip.start_file(
                name,
                SimpleFileOptions::default()
                    .compression_method(CompressionMethod::Deflated)
                    .compression_level(Some(6))
                    .unix_permissions(mode),
            )?;
            let mut input = fs::File::open(entry.path())?;
            std::io::copy(&mut input, &mut zip)?;
        }
        zip.finish()?;
    }
    pending.persist_noclobber(output).map_err(|e| e.error)?;
    write_json(
        suffix(output, ".json"),
        &json!({"archiveFileName":output.file_name().unwrap().to_string_lossy(),"archiveRoot":root_name,"size":fs::metadata(output)?.len(),"sha256":hash(output)?,"manifest_sha256":hash(root.join("MANIFEST.sha256"))?,"driver_version":env!("CARGO_PKG_VERSION"),"runtime_interpreters":[]}),
    )?;
    Ok(())
}
pub fn platform(source: &Path, output: &Path) -> Result<()> {
    let temp = tempfile::tempdir()?;
    let stage = temp.path().join("platform");
    fs::create_dir_all(&stage)?;
    for name in [
        "boards.txt",
        "platform.txt",
        "README.md",
        "COMPATIBILITY.md",
        "RELEASE_NOTES.md",
        "LICENSE",
    ] {
        write(stage.join(name), fs::read(source.join(name))?)?;
    }
    for name in ["cores", "variants", "libraries", "examples", "LICENSES"] {
        if source.join(name).is_dir() {
            copy_tree(&source.join(name), &stage.join(name))?;
        }
    }
    {
        let name = "devices.json";
        write(
            stage.join("tools/variants").join(name),
            fs::read(source.join("tools/variants").join(name))?,
        )?;
    }
    let driver = stage.join("tools/stcxx-driver");
    fs::create_dir_all(&driver)?;
    let current = std::env::current_exe()?;
    write(
        driver.join(current.file_name().unwrap()),
        fs::read(current)?,
    )?;
    for entry in fs::read_dir(source.join("tools/stcxx-driver"))? {
        let p = entry?.path();
        if p.file_name()
            .is_some_and(|n| n.to_string_lossy().starts_with("toolchain-lock."))
            && p.extension().is_some_and(|e| e == "json")
        {
            write(driver.join(p.file_name().unwrap()), fs::read(&p)?)?;
        }
    }
    write(
        driver.join("README.md"),
        fs::read(source.join("tools/stcxx-driver/README.md"))?,
    )?;
    copy_tree(
        &source.join("tools/stcxx-driver/LICENSES"),
        &driver.join("LICENSES"),
    )?;
    // Include the other host's binary when supplied by its native build job.
    let other = if cfg!(windows) { "stcxx" } else { "stcxx.exe" };
    let p = source.join("tools/stcxx-driver").join(other);
    if p.is_file() {
        write(driver.join(other), fs::read(p)?)?;
    }
    let version = text(source.join("platform.txt"))?
        .lines()
        .find_map(|l| l.strip_prefix("version="))
        .context("missing platform version")?
        .to_owned();
    archive(&stage, output, &format!("arduino-mcs251-{version}"))
}
pub fn stage_toolchain(source: &Path, destination: &Path) -> Result<()> {
    ensure!(!destination.exists(), "toolchain stage already exists");
    fs::create_dir_all(destination)?;
    // Explicit native components; no scripts, embedded interpreter, bytecode or build-input copies.
    for name in [
        "frontend/bin",
        "frontend/lib",
        "sdcc/bin",
        "sdcc/include",
        "sdcc/lib",
    ] {
        copy_tree(&source.join(name), &destination.join(name))?;
    }
    for component in ["frontend", "sdcc"] {
        // Older macOS locks bind the source component's manifest. Retain that
        // provenance separately while MANIFEST.sha256 inventories this payload.
        if !cfg!(windows) && component == "frontend" {
            let original = source.join(component).join("SOURCE-MANIFEST.sha256");
            let original = if original.is_file() {
                original
            } else {
                source.join(component).join("MANIFEST.sha256")
            };
            write(
                destination.join(component).join("SOURCE-MANIFEST.sha256"),
                fs::read(original)?,
            )?;
        }
        if source.join(component).join("libexec").is_dir() {
            copy_tree(
                &source.join(component).join("libexec"),
                &destination.join(component).join("libexec"),
            )?;
        }
        for name in ["COPYING.txt", "LICENSE", "LICENSE.txt"] {
            let path = source.join(component).join(name);
            if path.is_file() {
                write(destination.join(component).join(name), fs::read(path)?)?;
            }
        }
        let license = source.join(component).join("licenses");
        if license.is_dir() {
            for entry in fs::read_dir(&license)? {
                let p = entry?.path();
                if p.is_file()
                    && !p
                        .file_name()
                        .unwrap()
                        .to_string_lossy()
                        .to_lowercase()
                        .contains("python")
                {
                    write(
                        destination
                            .join(component)
                            .join("licenses")
                            .join(p.file_name().unwrap()),
                        fs::read(&p)?,
                    )?;
                }
            }
        }
        manifest(&destination.join(component))?;
    }
    install_sdk(destination)?;
    write_json(
        destination.join("toolchain.json"),
        &json!({"schema_version":3,"name":"stcxx-toolchain","version":env!("CARGO_PKG_VERSION"),"host":if cfg!(windows){"windows-x86_64"}else{"darwin-arm64"},"execution":"native","runtime_interpreters":[],"driver":format!("bin/stcxx{}", std::env::consts::EXE_SUFFIX),"sdk":"share/stcxx/sdk","targets":["mcs251"]}),
    )?;
    manifest(destination)
}

fn install_sdk(destination: &Path) -> Result<()> {
    let sdk = crate::standalone::sdk_root(None)?;
    let current = std::env::current_exe()?;
    let name = if cfg!(windows) { "stcxx.exe" } else { "stcxx" };
    let binary = destination.join("bin").join(name);
    write(&binary, fs::read(&current)?)?;
    fs::set_permissions(&binary, fs::metadata(&current)?.permissions())?;
    copy_tree(&sdk, &destination.join("share/stcxx/sdk"))?;
    let installed = current.parent().unwrap().join("../share/stcxx/LICENSES");
    let adjacent = current.parent().unwrap().join("LICENSES");
    let development = Path::new(env!("CARGO_MANIFEST_DIR")).join("LICENSES");
    let licenses = if installed.is_dir() {
        installed
    } else if adjacent.is_dir() {
        adjacent
    } else {
        development
    };
    ensure!(
        licenses.is_dir(),
        "missing driver dependency licenses; run node scripts/build-driver.mjs in stcxx before packaging"
    );
    copy_tree(&licenses, &destination.join("share/stcxx/LICENSES"))?;
    let readme = sdk.join("README.md");
    write(destination.join("README.md"), fs::read(readme)?)?;
    // Validate the actual staged compiler components against the SDK's host lock.
    let targets = json(sdk.join("targets.json"))?;
    let target = &targets["targets"][0];
    let flags = crate::standalone::verification_flags(target, &sdk)?;
    crate::driver::Driver::standalone(
        sdk,
        &s(exe(&destination.join("sdcc/bin"), "sdcc")),
        "-",
        "unused.hex",
        "link",
        flags,
    )?
    .verify()
}
pub fn toolchain(source: &Path, output: &Path) -> Result<()> {
    let temp = tempfile::tempdir()?;
    let stage = temp.path().join("toolchain");
    stage_toolchain(source, &stage)?;
    archive(&stage, output, "stcxx-toolchain")
}

pub fn run(args: &[String]) -> Result<()> {
    ensure!(
        args.len() == 3,
        "package operation requires source and destination"
    );
    let source = absolute(&args[1])?;
    let output = absolute(&args[2])?;
    match args[0].as_str() {
        "package-platform" => platform(&source, &output),
        "package-toolchain" => toolchain(&source, &output),
        "stage-toolchain" => stage_toolchain(&source, &output),
        _ => unreachable!(),
    }
}
