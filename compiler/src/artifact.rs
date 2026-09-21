//! A C++ REL is only a placeholder. Never link it without its verified payload.
use crate::util::*;
use anyhow::{Context, Result, ensure};
use std::{fs, path::Path};

pub fn placeholder(bytes: &[u8]) -> bool {
    bytes
        .windows(b"__stcxx_placeholder_".len())
        .any(|s| s == b"__stcxx_placeholder_")
}

pub fn validate(object: &Path) -> Result<bool> {
    let rel = object.with_extension("rel");
    let bytes =
        fs::read(object).with_context(|| format!("missing object: {}", object.display()))?;
    let rel_bytes = fs::read(&rel).with_context(|| {
        format!(
            "missing REL companion: {}; clean the build cache and rebuild",
            rel.display()
        )
    })?;
    ensure!(
        bytes == rel_bytes,
        "object/REL mismatch: {}; clean the build cache and rebuild",
        object.display()
    );
    let cpp = placeholder(&bytes)
        || [".stcxx.json", ".stcxx.bc", ".stcxx.ll", ".stcxx.module.cbe"]
            .iter()
            .any(|ext| suffix(object, ext).exists());
    if !cpp {
        return Ok(false);
    }
    (|| -> Result<()> {
        let meta = json(suffix(object, ".stcxx.json"))?;
        ensure!(
            meta["schema_version"] == 1,
            "unsupported C++ artifact version"
        );
        ensure!(placeholder(&bytes), "C++ object is not a placeholder");
        for (path, key, legacy) in [
            (object.to_path_buf(), "object_sha256", "object_sha256"),
            (rel, "object_sha256", "object_sha256"),
            (
                suffix(object, ".stcxx.bc"),
                "bitcode_sha256",
                "bitcode_sha256",
            ),
            (suffix(object, ".stcxx.ll"), "llvm_ir_sha256", "ir_sha256"),
            (
                suffix(object, ".stcxx.module.cbe"),
                "module_c_sha256",
                "cbe_sha256",
            ),
        ] {
            check_hash(path, string(meta.get(key).unwrap_or(&meta[legacy]))?)?;
        }
        Ok(())
    })()
    .with_context(|| {
        format!(
            "incomplete or corrupt C++ artifact: {}; clean the build cache and rebuild",
            object.display()
        )
    })?;
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn missing_payload_is_never_native_code() {
        let temp = tempfile::tempdir().unwrap();
        let obj = temp.path().join("constructor.cpp.o");
        fs::write(&obj, b"S ___stcxx_placeholder_123 Def000000\n").unwrap();
        fs::copy(&obj, obj.with_extension("rel")).unwrap();
        assert!(
            validate(&obj)
                .unwrap_err()
                .to_string()
                .contains("clean the build cache")
        );
        fs::write(&obj, b"S _native_function Def000000\n").unwrap();
        assert!(validate(&obj).is_err());
        fs::copy(&obj, obj.with_extension("rel")).unwrap();
        assert!(!validate(&obj).unwrap());
    }
}
