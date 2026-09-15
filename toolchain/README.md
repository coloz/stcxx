# Vendored frontend sources

These are ordinary files in the STCXX repository. A normal clone obtains the
sources; no submodules or upstream Git histories are required for the frontend.

| Directory | Upstream | Imported scope |
| --- | --- | --- |
| `llvm-project/` | LLVM `llvmorg-20.1.8`, commit `87f0227cb60147a26a1eeb4fb06e3b505e9c7261` | `clang/`, `cmake/`, and root files |
| `llvm-cbe/` | JuliaHubOSS LLVM-CBE, commit `83f1bea66c7415c701925470a2f7596b37153197` | Complete source tree |

STC patches from `arduino/patches/` are already applied to both directories.
Upstream license files remain in place. LLVM's other projects and both upstream
Git histories are excluded. The LLVM snapshot is not a complete LLVM monorepo
build; the frontend builds still require the documented LLVM 20 development
environment and use the locked Clang/CMake source archives.

`source-manifest.json` lists every imported file, its exact SHA-256 and its Git
file mode. Modified files also retain their upstream SHA-256. The manifest's
SHA-256 is pinned in `arduino/toolchain-lock.json`. Source bytes are excluded
from automatic line-ending conversion so binary and newline test fixtures
remain intact. Git's regular-file symlink placeholders on Windows and actual
symlinks on POSIX are both verified against their original link targets.

From the repository root, verify the complete imported inventory:

```sh
python3 arduino/scripts/vendor_sources.py
```

Build scripts do not modify these directories. Native frontend builds reconstruct
the original CBE archive in a temporary copy, reverse the locked STC patch, check
every upstream file hash, and then apply the existing patch/build pipeline.
The SDCC source checker still requires the main STCXX Git history.

When changing vendored production code, update the matching STC patch, affected
manifest hashes, patched-source hashes and patch hashes in the source lock.
Keep upstream hashes tied to the recorded upstream commit. Update the manifest
and helper hashes in the source lock when either changes, and the preparation
or checker hashes if those scripts change. Run the source checker and frontend
source tests before committing the combined update.
