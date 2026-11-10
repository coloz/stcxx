//! Share a successful toolchain check between Arduino jobs in one build.
//!
//! The prebuild hook resets the session. An OS file lock serializes the first
//! check and is released even if a compiler process is terminated. Receipts are
//! only reused while every expected digest, resolved path and file metadata
//! still matches. Commands without a prepared session always check all hashes.
use crate::util::{HashVerifier, digest};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use std::{
    fs::{self, File, OpenOptions},
    io::ErrorKind,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

const SCHEMA: u32 = 1;

fn open_lock(directory: &Path, create: bool) -> Result<Option<File>> {
    let result = OpenOptions::new()
        .read(true)
        .write(true)
        .create(create)
        .truncate(false)
        .open(directory.join("verification.lock"));
    let file = match result {
        Ok(file) => file,
        Err(error) if !create && error.kind() == ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error).context("open toolchain verification lock"),
    };
    file.lock().context("lock toolchain verification session")?;
    Ok(Some(file))
}

pub fn begin_session(directory: &Path) -> Result<()> {
    fs::create_dir_all(directory)?;
    // Keep this file in place: replacing it could let two jobs lock different
    // files. Dropping the open handle releases the operating system lock.
    let _lock = open_lock(directory, true)?;
    fs::write(directory.join("verified.json"), b"null\n")?;
    Ok(())
}

fn timestamp(time: SystemTime) -> String {
    match time.duration_since(UNIX_EPOCH) {
        Ok(duration) => duration.as_nanos().to_string(),
        Err(error) => format!("-{}", error.duration().as_nanos()),
    }
}

fn identity(path: &Path) -> Result<Value> {
    let resolved = path
        .canonicalize()
        .with_context(|| format!("resolve toolchain file {}", path.display()))?;
    let metadata = fs::metadata(&resolved)?;
    ensure!(
        metadata.is_file(),
        "not a toolchain file: {}",
        path.display()
    );
    let mut value = json!({
        "path": resolved,
        "length": metadata.len(),
        "modified": timestamp(metadata.modified()?),
        "created": metadata.created().ok().map(timestamp),
    });
    // ctime also catches same-length edits that restore mtime on Unix.
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        value["changed"] = json!([metadata.ctime(), metadata.ctime_nsec()]);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        value["attributes"] = json!(metadata.file_attributes());
    }
    Ok(value)
}

fn fingerprint(inputs: &[(PathBuf, String)]) -> Result<String> {
    let mut files = Vec::with_capacity(inputs.len());
    for (path, expected) in inputs {
        files.push(json!([identity(path)?, expected]));
    }
    Ok(digest(serde_json::to_vec(&json!({
        "schema": SCHEMA,
        "driver": identity(&std::env::current_exe()?)?,
        "files": files,
    }))?))
}

fn check_files(inputs: &[(PathBuf, String)]) -> Result<()> {
    let mut verifier = HashVerifier::default();
    for (path, expected) in inputs {
        verifier.check(path, expected)?;
    }
    Ok(())
}

pub fn verify(session: Option<&Path>, inputs: &[(PathBuf, String)]) -> Result<()> {
    verify_using(session, inputs, || check_files(inputs)).map(|_| ())
}

fn verify_using(
    session: Option<&Path>,
    inputs: &[(PathBuf, String)],
    check: impl FnOnce() -> Result<()>,
) -> Result<bool> {
    let Some(directory) = session else {
        check()?;
        return Ok(false);
    };
    let Some(_lock) = open_lock(directory, false)? else {
        // A manually invoked recipe without the prebuild hook stays strict.
        check()?;
        return Ok(false);
    };
    let receipt = directory.join("verified.json");
    let before = fingerprint(inputs)?;
    let previous = fs::read(&receipt)
        .ok()
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok());
    if previous.as_ref() == Some(&json!({"schema": SCHEMA, "fingerprint": before})) {
        return Ok(true);
    }
    // A failure, interrupted write or terminated checker must not leave a
    // successful receipt. Other jobs hold this same lock before reading it.
    fs::write(&receipt, b"null\n")?;
    check()?;
    ensure!(
        fingerprint(inputs)? == before,
        "toolchain changed during verification; retry the build"
    );
    fs::write(
        receipt,
        serde_json::to_vec(&json!({"schema": SCHEMA, "fingerprint": before}))?,
    )?;
    Ok(false)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        io::Write,
        process::{Command, Stdio},
        sync::atomic::{AtomicUsize, Ordering},
        thread,
        time::{Duration, Instant},
    };

    fn fixture(root: &Path) -> Vec<(PathBuf, String)> {
        let path = root.join("tool.exe");
        fs::write(&path, b"original").unwrap();
        vec![(path, digest(b"original"))]
    }

    #[test]
    fn session_reuses_success_and_prebuild_resets_it() {
        let temp = tempfile::tempdir().unwrap();
        let inputs = fixture(temp.path());
        let session = temp.path().join("session");
        let count = AtomicUsize::new(0);
        let verify = || {
            verify_using(Some(&session), &inputs, || {
                count.fetch_add(1, Ordering::SeqCst);
                check_files(&inputs)
            })
            .unwrap()
        };
        assert!(!verify()); // No prebuild: no persistent reuse.
        assert!(!verify());
        begin_session(&session).unwrap();
        assert!(!verify());
        assert!(verify());
        begin_session(&session).unwrap();
        assert!(!verify());
        assert_eq!(count.load(Ordering::SeqCst), 4);
    }

    #[test]
    fn changed_missing_and_conflicting_inputs_are_rejected() {
        let temp = tempfile::tempdir().unwrap();
        let inputs = fixture(temp.path());
        let session = temp.path().join("session");
        begin_session(&session).unwrap();
        verify(Some(&session), &inputs).unwrap();
        fs::write(&inputs[0].0, b"tampered").unwrap();
        // Force a distinct timestamp even on a filesystem with coarse times.
        File::options()
            .write(true)
            .open(&inputs[0].0)
            .unwrap()
            .set_modified(SystemTime::now() + Duration::from_secs(2))
            .unwrap();
        assert!(verify(Some(&session), &inputs).is_err());
        assert!(verify(Some(&session), &inputs).is_err());
        assert_eq!(fs::read(session.join("verified.json")).unwrap(), b"null\n");
        fs::write(&inputs[0].0, b"original").unwrap();
        verify(Some(&session), &inputs).unwrap();
        let mut conflict = inputs.clone();
        conflict.push((inputs[0].0.clone(), digest(b"different")));
        assert!(verify(Some(&session), &conflict).is_err());
        fs::remove_file(&inputs[0].0).unwrap();
        assert!(verify(Some(&session), &inputs).is_err());
    }

    #[test]
    fn damaged_receipt_and_changed_lock_expectations_are_rechecked() {
        let temp = tempfile::tempdir().unwrap();
        let inputs = fixture(temp.path());
        let session = temp.path().join("session");
        begin_session(&session).unwrap();
        verify(Some(&session), &inputs).unwrap();
        fs::write(session.join("verified.json"), b"{interrupted").unwrap();
        assert!(!verify_using(Some(&session), &inputs, || check_files(&inputs)).unwrap());
        let changed = vec![(inputs[0].0.clone(), digest(b"changed lock"))];
        assert!(verify(Some(&session), &changed).is_err());
    }

    #[test]
    fn changes_during_verification_do_not_publish_success() {
        let temp = tempfile::tempdir().unwrap();
        let inputs = fixture(temp.path());
        let session = temp.path().join("session");
        begin_session(&session).unwrap();
        let result = verify_using(Some(&session), &inputs, || {
            check_files(&inputs)?;
            fs::write(&inputs[0].0, b"changed while checking")?;
            Ok(())
        });
        assert!(result.is_err());
        assert_eq!(fs::read(session.join("verified.json")).unwrap(), b"null\n");
    }

    fn process(root: &Path, mode: &str) -> std::process::Child {
        Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "--ignored",
                "--nocapture",
                "verification::tests::process_fixture",
            ])
            .env("STCXX_VERIFICATION_TEST_ROOT", root)
            .env("STCXX_VERIFICATION_TEST_MODE", mode)
            .stdout(Stdio::null())
            .spawn()
            .unwrap()
    }

    #[test]
    #[ignore = "subprocess fixture"]
    fn process_fixture() {
        let root = PathBuf::from(std::env::var_os("STCXX_VERIFICATION_TEST_ROOT").unwrap());
        let session = root.join("session");
        if std::env::var("STCXX_VERIFICATION_TEST_MODE").unwrap() == "hold" {
            let _lock = open_lock(&session, false).unwrap().unwrap();
            fs::write(root.join("ready"), b"locked").unwrap();
            thread::sleep(Duration::from_secs(15));
        } else {
            let inputs = vec![(root.join("tool.exe"), digest(b"original"))];
            verify_using(Some(&session), &inputs, || {
                check_files(&inputs)?;
                let mut audit = OpenOptions::new()
                    .append(true)
                    .create(true)
                    .open(root.join("audit"))?;
                writeln!(audit, "full verification")?;
                thread::sleep(Duration::from_millis(100));
                Ok(())
            })
            .unwrap();
        }
    }

    #[test]
    fn parallel_processes_only_hash_once() {
        let temp = tempfile::tempdir().unwrap();
        fixture(temp.path());
        begin_session(&temp.path().join("session")).unwrap();
        let mut children: Vec<_> = (0..8).map(|_| process(temp.path(), "verify")).collect();
        for child in &mut children {
            assert!(child.wait().unwrap().success());
        }
        assert_eq!(
            fs::read_to_string(temp.path().join("audit"))
                .unwrap()
                .lines()
                .count(),
            1
        );
    }

    #[test]
    fn terminated_process_releases_verification_lock() {
        let temp = tempfile::tempdir().unwrap();
        let inputs = fixture(temp.path());
        let session = temp.path().join("session");
        begin_session(&session).unwrap();
        let mut child = process(temp.path(), "hold");
        let deadline = Instant::now() + Duration::from_secs(10);
        while !temp.path().join("ready").is_file() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        let ready = temp.path().join("ready").is_file();
        child.kill().unwrap();
        child.wait().unwrap();
        assert!(ready, "child did not acquire the lock");
        verify(Some(&session), &inputs).unwrap();
    }
}
