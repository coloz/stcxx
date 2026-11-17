//! Validate paths passed to the native SDCC assembler and linker tools.
use anyhow::Result;
use std::path::Path;

pub fn compatible(path: &Path) -> Result<bool> {
    #[cfg(windows)]
    {
        Ok(windows::incompatibility(path, false)?.is_none())
    }
    #[cfg(not(windows))]
    {
        let _ = path;
        Ok(true)
    }
}

pub fn check(path: &Path) -> Result<()> {
    #[cfg(windows)]
    {
        windows::check(path, false)
    }
    #[cfg(not(windows))]
    {
        let _ = path;
        Ok(())
    }
}

pub fn check_command(program: &Path, args: &[String], cwd: Option<&Path>) -> Result<()> {
    #[cfg(windows)]
    {
        windows::check_command(program, args, cwd)
    }
    #[cfg(not(windows))]
    {
        let _ = (program, args, cwd);
        Ok(())
    }
}

#[cfg(windows)]
mod windows {
    use super::*;
    use anyhow::{Context, bail};
    use std::{os::windows::ffi::OsStrExt, path::PathBuf};

    // asxxxx afilex() and aslink afile() require strlen(fn) <= FILSPC - 7.
    // Their Windows FILSPC is _MAX_PATH (260), reserving space for extensions.
    const MAX_BACKEND_BYTES: usize = 260 - 7;
    const MAX_WINDOWS_UNITS: usize = 260 - 1;
    const CP_UTF8: u32 = 65001;
    const WC_ERR_INVALID_CHARS: u32 = 0x80;
    const WC_NO_BEST_FIT_CHARS: u32 = 0x400;

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetACP() -> u32;
        fn WideCharToMultiByte(
            code_page: u32,
            flags: u32,
            wide: *const u16,
            wide_len: i32,
            bytes: *mut u8,
            bytes_len: i32,
            default_char: *const u8,
            used_default_char: *mut i32,
        ) -> i32;
    }

    fn encoded_length(wide: &[u16], code_page: u32) -> Result<Option<usize>> {
        let mut substituted = 0;
        let (flags, used_default) = if code_page == CP_UTF8 {
            // UTF-8 rejects default-character arguments and accepts this flag.
            (WC_ERR_INVALID_CHARS, std::ptr::null_mut())
        } else {
            (WC_NO_BEST_FIT_CHARS, &mut substituted as *mut i32)
        };
        // A null output buffer requests the encoded length. The live UTF-16
        // slice excludes a terminating NUL, matching the backend's strlen().
        let length = unsafe {
            WideCharToMultiByte(
                code_page,
                flags,
                wide.as_ptr(),
                i32::try_from(wide.len())?,
                std::ptr::null_mut(),
                0,
                std::ptr::null(),
                used_default,
            )
        };
        if length == 0 {
            return Err(std::io::Error::last_os_error()).context("encode native backend path");
        }
        Ok((substituted == 0).then_some(length as usize))
    }

    pub(super) fn incompatibility(path: &Path, allow_whitespace: bool) -> Result<Option<String>> {
        let path = std::path::absolute(path)?;
        if !allow_whitespace
            && path
                .as_os_str()
                .to_string_lossy()
                .chars()
                .any(char::is_whitespace)
        {
            return Ok(Some(format!(
                "native backend path contains whitespace: {}",
                path.display()
            )));
        }
        let wide: Vec<u16> = path.as_os_str().encode_wide().collect();
        if wide.len() > MAX_WINDOWS_UNITS {
            return Ok(Some(format!(
                "native backend path exceeds {MAX_WINDOWS_UNITS} UTF-16 units ({}): {}",
                wide.len(),
                path.display()
            )));
        }
        // The native tools receive narrow argv and use narrow filesystem APIs.
        // Check the active Windows ANSI code page, including UTF-8 systems.
        let code_page = unsafe { GetACP() };
        let Some(length) = encoded_length(&wide, code_page)? else {
            return Ok(Some(format!(
                "native backend path cannot be represented in Windows code page {code_page}: {}",
                path.display()
            )));
        };
        if length > MAX_BACKEND_BYTES {
            return Ok(Some(format!(
                "native backend path exceeds {MAX_BACKEND_BYTES} bytes in Windows code page {code_page} ({length}): {}",
                path.display()
            )));
        }
        Ok(None)
    }

    pub(super) fn check(path: &Path, allow_whitespace: bool) -> Result<()> {
        if let Some(reason) = incompatibility(path, allow_whitespace)? {
            bail!(reason);
        }
        Ok(())
    }

    fn resolve(path: &str, cwd: &Path) -> Result<PathBuf> {
        Ok(std::path::absolute(cwd.join(path))?)
    }

    fn file_argument(arg: &str) -> bool {
        matches!(
            Path::new(arg).extension().and_then(|ext| ext.to_str()),
            Some(
                "c" | "i"
                    | "asm"
                    | "rel"
                    | "lst"
                    | "o"
                    | "a"
                    | "lib"
                    | "ihx"
                    | "hex"
                    | "lk"
                    | "map"
                    | "mem"
                    | "rst"
                    | "d"
            )
        )
    }

    pub(super) fn check_command(program: &Path, args: &[String], cwd: Option<&Path>) -> Result<()> {
        let name = program
            .file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        if !matches!(
            name.as_str(),
            "sdcc" | "sdar" | "sdas251" | "sdas8051" | "sdld" | "sdldmcs251"
        ) {
            return Ok(());
        }
        // Process creation quotes the executable separately, so a tool under
        // Program Files can be launched even though generated command files
        // cannot safely represent whitespace in their data-file paths.
        check(program, true)?;
        let cwd = std::path::absolute(cwd.unwrap_or(Path::new(".")))?;
        check(&cwd, false)?;
        let mut arguments = args.iter();
        while let Some(arg) = arguments.next() {
            if arg == "-I" {
                // Search directories are not necessarily opened. In particular,
                // Arduino passes the sketch include root to every core source.
                // Rejecting an unused long -I would break otherwise valid C.
                arguments.next().context("missing -I directory")?;
            } else if arg.starts_with("-I") {
                continue;
            } else if matches!(arg.as_str(), "-o" | "-L" | "-MF") {
                let value = arguments
                    .next()
                    .with_context(|| format!("missing {arg} path"))?;
                check(&resolve(value, &cwd)?, false)?;
            } else if matches!(
                arg.as_str(),
                "-D" | "-U" | "-MT" | "-MQ" | "--codeseg" | "--constseg"
            ) {
                // Macro values and dependency-rule targets are not file IO.
                arguments.next();
            } else if let Some(value) = arg.strip_prefix("-L").or_else(|| arg.strip_prefix("-o")) {
                if !value.is_empty() {
                    check(&resolve(value, &cwd)?, false)?;
                }
            } else if !arg.starts_with('-') && file_argument(arg) {
                check(&resolve(arg, &cwd)?, false)?;
            }
        }
        Ok(())
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        fn arguments(values: &[&str]) -> Vec<String> {
            values.iter().map(|v| (*v).into()).collect()
        }

        #[test]
        fn native_byte_budget_accepts_boundary_and_rejects_overflow() {
            let accepted = PathBuf::from(format!("C:\\{}", "a".repeat(MAX_BACKEND_BYTES - 3)));
            assert!(super::super::compatible(&accepted).unwrap());
            let rejected = PathBuf::from(format!("{}a", accepted.display()));
            assert!(!super::super::compatible(&rejected).unwrap());
            assert!(
                super::super::check(&rejected)
                    .unwrap_err()
                    .to_string()
                    .contains("253 bytes")
            );
            let too_wide = PathBuf::from(format!("C:\\{}", "a".repeat(MAX_WINDOWS_UNITS - 2)));
            assert!(
                super::super::check(&too_wide)
                    .unwrap_err()
                    .to_string()
                    .contains("UTF-16")
            );
        }

        #[test]
        fn code_page_measurement_rejects_substitution_and_counts_encoded_bytes() {
            let chinese: Vec<u16> = r"C:\目录\input.c".encode_utf16().collect();
            assert!(encoded_length(&chinese, 1252).unwrap().is_none());
            let ascii: Vec<u16> = r"C:\input.c".encode_utf16().collect();
            assert_eq!(encoded_length(&ascii, 1252).unwrap(), Some(ascii.len()));
            let utf8 = encoded_length(&chinese, CP_UTF8).unwrap().unwrap();
            assert_eq!(utf8, r"C:\目录\input.c".len());
            assert!(utf8 > chinese.len());
        }

        #[test]
        fn command_checks_native_paths_and_preserves_macro_and_dependency_targets() {
            let long = format!("C:\\{}.c", "a".repeat(260));
            let sdcc = Path::new(r"C:\Program Files\tools\sdcc.exe");
            let cwd = Some(Path::new(r"C:\work"));
            let values = arguments(&[
                "-D",
                &long,
                "-MT",
                &long,
                "--codeseg",
                &long,
                "source.c",
                "-o",
                "out.rel",
            ]);
            check_command(sdcc, &values, cwd).unwrap();
            assert!(check_command(sdcc, &arguments(&["source.c", "-o", &long]), cwd).is_err());
            check_command(sdcc, &arguments(&["-I", &long]), cwd).unwrap();
            assert!(check_command(sdcc, &arguments(&[&format!("-L{long}")]), cwd).is_err());
            assert!(
                check_command(
                    sdcc,
                    &arguments(&["source.c"]),
                    Some(Path::new(r"C:\with space"))
                )
                .is_err()
            );
            check_command(Path::new(r"C:\tools\clang.exe"), &arguments(&[&long]), cwd).unwrap();
            check_command(Path::new(r"C:\tools\sdcpp.exe"), &arguments(&[&long]), cwd).unwrap();
        }

        #[test]
        fn relative_paths_use_the_child_working_directory() {
            let directory = format!("C:\\{}", "a".repeat(235));
            let sdar = Path::new(r"C:\tools\sdar.exe");
            assert!(
                check_command(
                    sdar,
                    &arguments(&["rcs", "core.lib", "source.rel"]),
                    Some(Path::new(&directory))
                )
                .is_ok()
            );
            assert!(
                check_command(
                    sdar,
                    &arguments(&["rcs", "core.lib", "long_file_name.rel"]),
                    Some(Path::new(&directory))
                )
                .is_err()
            );
        }
    }
}
