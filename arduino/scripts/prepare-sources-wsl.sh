#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "${script_dir}/../.." && pwd)

# The tracked frontend snapshots already contain the locked STC patches.
# Verify their complete inventories without rewriting the source checkout.
python3 "${script_dir}/vendor_sources.py" --root "${repo_root}"
echo "PREPARE_SOURCES=PASS"
