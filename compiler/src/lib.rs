mod adapter;
mod artifact;
mod cache;
mod driver;
mod link;
mod package;
mod standalone;
mod trim;
mod util;
use anyhow::{Result, bail, ensure};
use std::path::PathBuf;
use util::*;

fn entry(mut args: Vec<String>) -> Result<()> {
    if args.is_empty() || args[0] == "--help" {
        println!("{}\n", standalone::HELP);
        println!(
            "stcxx {} — native STC MCS251 compiler driver\nUsage: stcxx --platform <directory> compile <sdcc> <source> <output> <re1|re2|re11|re12> [flags...]\n       stcxx --platform <directory> archive <sdar> <archive> <object> [flags...]\n       stcxx --platform <directory> link <sdcc> [flags...]\n       stcxx size <report.mem>\nNo script interpreter is used. Internal compiler commands are printed to stdout, except during preprocessing. Compiler diagnostics retain stderr.",
            env!("CARGO_PKG_VERSION")
        );
        return Ok(());
    }
    if args[0] == "--version" {
        println!(
            "stcxx {} (native {})",
            env!("CARGO_PKG_VERSION"),
            std::env::consts::ARCH
        );
        return Ok(());
    }
    if ["package-platform", "package-toolchain", "stage-toolchain"].contains(&args[0].as_str()) {
        return package::run(&args);
    }
    if args[0] == "advanced-size" {
        ensure!(args.len() == 2, "advanced-size requires one .mem report");
        return driver::advanced_size(&PathBuf::from(&args[1]));
    }
    if args[0] == "size" {
        ensure!(args.len() == 2, "size requires one .mem report");
        return driver::size(&PathBuf::from(&args[1]));
    }
    if !["--platform", "compile", "archive", "link"].contains(&args[0].as_str()) {
        return standalone::run(&args);
    }
    let platform = if args[0] == "--platform" {
        ensure!(args.len() > 2, "missing --platform directory");
        let p = absolute(&args[1])?;
        args.drain(..2);
        p
    } else {
        match std::env::var_os("STCXX_PLATFORM_ROOT") {
            Some(p) => absolute(p)?,
            None => {
                let exe = std::env::current_exe()?;
                let root = exe.parent().unwrap().join("../..").canonicalize()?;
                ensure!(
                    root.join("platform.txt").is_file(),
                    "specify --platform <Arduino platform directory>"
                );
                root
            }
        }
    };
    match args[0].as_str() {
        "compile" => {
            ensure!(
                args.len() >= 5,
                "compile requires sdcc, source, output, recipe marker"
            );
            // Arduino reads preprocessing stdout as source/dependency data.
            // Suppress command traces, including internal tool discovery calls.
            set_command_logging(!["re11", "re12"].contains(&args[4].as_str()));
            driver::Driver::new(
                platform,
                &args[1],
                &args[2],
                &args[3],
                &args[4],
                args[5..].to_vec(),
            )?
            .compile()
        }
        "archive" => {
            ensure!(args.len() >= 4, "archive requires sdar, archive, object");
            cache::archive(
                &PathBuf::from(&args[1]),
                &absolute(&args[2])?,
                &absolute(&args[3])?,
                &args[4..],
            )
        }
        "link" => {
            ensure!(args.len() > 2, "link requires sdcc and arguments");
            let output = command_flag(&args[2..], "-o")?;
            ensure!(
                PathBuf::from(&output)
                    .extension()
                    .is_some_and(|e| e == "hex"),
                "expected .hex output"
            );
            remove(&output)?;
            driver::Driver::new(platform, &args[1], "-", &output, "link", args[2..].to_vec())?
                .link()
        }
        "size" => {
            ensure!(args.len() == 2, "size requires one .mem report");
            driver::size(&PathBuf::from(&args[1]))
        }
        "advanced-size" => {
            ensure!(args.len() == 2, "advanced-size requires one .mem report");
            driver::advanced_size(&PathBuf::from(&args[1]))
        }
        other => bail!("unknown operation: {other}"),
    }
}
pub fn main_entry() {
    if let Err(error) = entry(std::env::args().skip(1).collect()) {
        eprintln!("stcxx: {error:#}");
        std::process::exit(
            error
                .downcast_ref::<ProcessError>()
                .map(|e| e.0)
                .unwrap_or(2),
        );
    }
}
