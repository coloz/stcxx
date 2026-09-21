use crate::util::*;
use anyhow::{Context, Result, ensure};
use serde_json::json;
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    io::{Cursor, Read, Write},
    path::{Path, PathBuf},
};
use zip::{CompressionMethod, ZipArchive, ZipWriter, write::SimpleFileOptions};

fn unpack(archive: &Path) -> Result<BTreeMap<String, Vec<u8>>> {
    let mut packed = ZipArchive::new(fs::File::open(archive)?)?;
    unpack_checked(&mut packed)
}
fn unpack_checked(packed: &mut ZipArchive<fs::File>) -> Result<BTreeMap<String, Vec<u8>>> {
    let mut files = BTreeMap::new();
    for i in 0..packed.len() {
        let mut f = packed.by_index(i)?;
        safe_relative(f.name())?;
        ensure!(f.is_file(), "non-file core entry");
        let name = f.name().to_owned();
        let mut bytes = Vec::new();
        f.read_to_end(&mut bytes)?;
        ensure!(
            files.insert(name.clone(), bytes).is_none(),
            "duplicate core entry: {name}"
        );
    }
    let manifest: serde_json::Value = serde_json::from_slice(
        &files
            .remove("manifest.json")
            .context("missing core manifest")?,
    )?;
    ensure!(
        manifest["schema_version"] == 1,
        "unsupported core cache format"
    );
    let inventory = manifest["files"]
        .as_object()
        .context("missing core inventory")?;
    ensure!(
        files.contains_key("core.lib")
            && files.keys().collect::<BTreeSet<_>>() == inventory.keys().collect(),
        "incomplete core cache"
    );
    for (name, bytes) in &files {
        ensure!(
            digest(bytes) == string(&inventory[name])?,
            "core cache SHA-256 mismatch: {name}"
        );
    }
    Ok(files)
}
fn pack_updated(
    files: BTreeMap<String, Vec<u8>>,
    mut previous: Option<&mut ZipArchive<fs::File>>,
    changed: &BTreeSet<String>,
) -> Result<Vec<u8>> {
    let mut packed = ZipWriter::new(Cursor::new(Vec::new()));
    for (name, bytes) in files {
        if !changed.contains(&name)
            && let Some(previous) = previous.as_mut()
        {
            // unpack_checked validated the same open archive before mutation.
            // Preserve compressed data for untouched members, without Deflate.
            packed.raw_copy_file(previous.by_name(&name)?)?;
        } else {
            packed.start_file(
                name,
                SimpleFileOptions::default().compression_method(CompressionMethod::Deflated),
            )?;
            packed.write_all(&bytes)?;
        }
    }
    Ok(packed.finish()?.into_inner())
}
pub fn archive(sdar: &Path, archive: &Path, object: &Path, flags: &[String]) -> Result<()> {
    let sdar = if sdar.is_file() {
        sdar.to_path_buf()
    } else {
        suffix(sdar, std::env::consts::EXE_SUFFIX)
    };
    let rel = if object.extension().is_some_and(|e| e == "o") {
        object.with_extension("rel")
    } else {
        object.to_path_buf()
    };
    nonempty(&rel)?;
    let obj = rel.with_extension("o");
    let cpp = crate::artifact::validate(&obj)?;
    let library = archive.with_extension("lib");
    if archive.file_name().is_none_or(|n| n != "core.a") {
        ensure!(
            rel.file_name().is_none_or(|n| n != "stcxx_heap.c.rel"),
            "heap cannot enter a non-core archive"
        );
    }
    let base = archive.parent().context("missing archive directory")?;
    let mut previous = if archive.exists() {
        Some(ZipArchive::new(fs::File::open(archive)?)?)
    } else {
        None
    };
    let mut changed = BTreeSet::from(["core.lib".into(), "manifest.json".into()]);
    let mut files = if let Some(previous) = &mut previous {
        let p = unpack_checked(previous)?;
        write(&library, &p["core.lib"])?;
        p
    } else {
        remove(&library)?;
        BTreeMap::new()
    };
    if rel.file_name().is_none_or(|n| n != "stcxx_heap.c.rel") {
        let mut argv = flags.to_vec();
        argv.extend([s(&library), s(&rel)]);
        run(&sdar, &argv, None, false, None)?;
    }
    files.insert(
        "core.lib".into(),
        if library.exists() {
            fs::read(&library)?
        } else {
            b"!<arch>\n".to_vec()
        },
    );
    let mut paths = vec![rel.clone(), obj.clone()];
    if rel.with_extension("lst").is_file() {
        paths.push(rel.with_extension("lst"));
    }
    for ext in [".stcxx.json", ".stcxx.bc", ".stcxx.ll", ".stcxx.module.cbe"] {
        let p = suffix(&obj, ext);
        if p.exists() {
            paths.push(p);
        }
    }
    let metadata = suffix(&rel, ".stcxx-c.json");
    if metadata.exists() {
        paths.push(metadata);
    }
    for p in paths {
        let name = p
            .strip_prefix(base)
            .context("object outside core directory")?
            .to_string_lossy()
            .replace('\\', "/");
        safe_relative(&name)?;
        changed.insert(name.clone());
        files.insert(name, fs::read(p)?);
    }
    if cpp {
        // A precompiled library must survive removal of its original checkout.
        let meta = crate::util::json(suffix(&obj, ".stcxx.json"))?;
        let source = PathBuf::from(string(&meta["source"])?);
        check_hash(&source, string(&meta["source_sha256"])?)?;
        let name = suffix(&obj, ".stcxx.source")
            .strip_prefix(base)?
            .to_string_lossy()
            .replace('\\', "/");
        changed.insert(name.clone());
        files.insert(name, fs::read(source)?);
    }
    let inventory: BTreeMap<_, _> = files.iter().map(|(n, b)| (n.clone(), digest(b))).collect();
    files.insert(
        "manifest.json".into(),
        serde_json::to_vec_pretty(&json!({"schema_version":1,"files":inventory}))?,
    );
    let packed = pack_updated(files, previous.as_mut(), &changed)?;
    // Close the read handle before atomically replacing the ZIP on Windows.
    drop(previous);
    atomic_write(archive, &packed)
}
pub fn materialize(archive: &Path, work: &Path) -> Result<PathBuf> {
    if !fs::read(archive)?.starts_with(b"PK") {
        // SDAR archives containing native RELs are valid standalone libraries.
        crate::link::archive_members(archive)?;
        let destination = work.join("native-cache").join(hash(archive)?);
        let native = destination.join("native.a");
        write(&native, fs::read(archive)?)?;
        write(native.with_extension("lib"), fs::read(archive)?)?;
        return Ok(native);
    }
    let files = unpack(archive)?;
    let destination = work.join("core-cache").join(hash(archive)?);
    for (name, bytes) in &files {
        let path = destination.join(safe_relative(name)?);
        let mut bytes = bytes.clone();
        if name.ends_with(".o.stcxx.json") {
            let mut meta: serde_json::Value = serde_json::from_slice(&bytes)?;
            let obj = destination.join(name.trim_end_matches(".stcxx.json"));
            meta["object"] = json!(s(&obj));
            meta["bitcode"] = json!(s(suffix(obj, ".stcxx.bc")));
            let source = name.trim_end_matches(".stcxx.json").to_owned() + ".stcxx.source";
            if files.contains_key(&source) {
                meta["source"] = json!(s(destination.join(source)));
            }
            bytes = serde_json::to_vec_pretty(&meta)?;
        }
        if name.ends_with(".rel.stcxx-c.json") {
            let mut meta: serde_json::Value = serde_json::from_slice(&bytes)?;
            // Preserve the library classification before relocating its REL
            // from build/libraries into the content-addressed archive cache.
            meta["library_source"] = json!(crate::trim::is_library_source(&meta));
            meta["original_rel"] =
                json!(s(destination.join(name.trim_end_matches(".stcxx-c.json"))));
            bytes = serde_json::to_vec_pretty(&meta)?;
        }
        write(path, bytes)?;
    }
    let native = destination.join("core.a");
    write(&native, &files["core.lib"])?;
    Ok(native)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn archive_updates_preserve_unchanged_compressed_members() {
        let temp = tempfile::tempdir().unwrap();
        let archive = temp.path().join("core.a");
        let mut zip = ZipWriter::new(fs::File::create(&archive).unwrap());
        let files = BTreeMap::from([
            ("core.lib".to_owned(), b"old library".to_vec()),
            ("unchanged.o".to_owned(), b"unchanged object".repeat(100)),
        ]);
        let inventory: BTreeMap<_, _> = files.iter().map(|(n, b)| (n, digest(b))).collect();
        for (name, bytes) in &files {
            // Stored deliberately: recompressing this entry changes the method.
            zip.start_file(
                name,
                SimpleFileOptions::default().compression_method(CompressionMethod::Stored),
            )
            .unwrap();
            zip.write_all(bytes).unwrap();
        }
        zip.start_file("manifest.json", SimpleFileOptions::default())
            .unwrap();
        zip.write_all(&serde_json::to_vec(&json!({"schema_version":1,"files":inventory})).unwrap())
            .unwrap();
        zip.finish().unwrap();
        let mut previous = ZipArchive::new(fs::File::open(&archive).unwrap()).unwrap();
        let mut updated = unpack_checked(&mut previous).unwrap();
        updated.insert("core.lib".into(), b"new library".to_vec());
        updated.insert("new.o".into(), b"new object".to_vec());
        let inventory: BTreeMap<_, _> = updated
            .iter()
            .map(|(n, b)| (n.clone(), digest(b)))
            .collect();
        updated.insert(
            "manifest.json".into(),
            serde_json::to_vec(&json!({"schema_version":1,"files":inventory})).unwrap(),
        );
        let bytes = pack_updated(
            updated,
            Some(&mut previous),
            &BTreeSet::from(["core.lib".into(), "new.o".into(), "manifest.json".into()]),
        )
        .unwrap();
        drop(previous);
        atomic_write(&archive, &bytes).unwrap();
        let readback = unpack(&archive).unwrap();
        assert_eq!(readback["core.lib"], b"new library");
        assert_eq!(readback["new.o"], b"new object");
        assert_eq!(readback["unchanged.o"], files["unchanged.o"]);
        let mut zip = ZipArchive::new(fs::File::open(&archive).unwrap()).unwrap();
        assert_eq!(
            zip.by_name("unchanged.o").unwrap().compression(),
            CompressionMethod::Stored
        );
    }
    #[test]
    fn native_library_classification_survives_archive_relocation() {
        let temp = tempfile::tempdir().unwrap();
        let archive = temp.path().join("test.a");
        let mut files = BTreeMap::new();
        files.insert("core.lib", b"!<arch>\n".to_vec());
        files.insert(
            "test.c.rel.stcxx-c.json",
            serde_json::to_vec(&json!({
                "source":"/custom path/Test/test.c",
                "original_rel":"/build/libraries/Test/test.c.rel"
            }))
            .unwrap(),
        );
        let inventory: BTreeMap<_, _> = files.iter().map(|(n, b)| (*n, digest(b))).collect();
        files.insert(
            "manifest.json",
            serde_json::to_vec(&json!({"schema_version":1,"files":inventory})).unwrap(),
        );
        let mut zip = ZipWriter::new(fs::File::create(&archive).unwrap());
        for (name, bytes) in files {
            zip.start_file(name, SimpleFileOptions::default()).unwrap();
            zip.write_all(&bytes).unwrap();
        }
        zip.finish().unwrap();
        let output = materialize(&archive, &temp.path().join("cache")).unwrap();
        let meta =
            crate::util::json(output.parent().unwrap().join("test.c.rel.stcxx-c.json")).unwrap();
        assert_eq!(meta["library_source"], true);
        assert!(crate::trim::is_library_source(&meta));
        assert!(
            !string(&meta["original_rel"])
                .unwrap()
                .contains("/libraries/")
        );
        assert!(!crate::trim::is_library_source(&json!({
            "source":"/core/runtime.c", "original_rel":"/build/core/runtime.c.rel"
        })));
    }
    #[test]
    fn cache_rejects_tampered_payload_and_missing_members() {
        let temp = tempfile::tempdir().unwrap();
        for (name, payload, inventory) in [
            (
                "valid",
                b"original".as_slice(),
                json!({"core.lib":digest("original")}),
            ),
            (
                "tampered",
                b"changed".as_slice(),
                json!({"core.lib":digest("original")}),
            ),
            (
                "missing",
                b"original".as_slice(),
                json!({"core.lib":digest("original"),"missing.o":digest("x")}),
            ),
        ] {
            let path = temp.path().join(name);
            let mut zip = ZipWriter::new(fs::File::create(&path).unwrap());
            zip.start_file("core.lib", SimpleFileOptions::default())
                .unwrap();
            zip.write_all(payload).unwrap();
            zip.start_file("manifest.json", SimpleFileOptions::default())
                .unwrap();
            zip.write_all(
                &serde_json::to_vec(&json!({"schema_version":1,"files":inventory})).unwrap(),
            )
            .unwrap();
            zip.finish().unwrap();
            assert_eq!(unpack(&path).is_ok(), name == "valid");
        }
    }
}
