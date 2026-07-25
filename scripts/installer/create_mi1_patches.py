#!/usr/bin/env python3
"""Build VPatch .pat files for the MI1 Hebrew installer.

Compares vanilla GOG backups under quickbms/ against the patched game files
and writes patch data into installer/mi1/patches/.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_ORIG_DIR = r"C:\GOG Games\Monkey Island 1 SE\quickbms"
DEFAULT_PATCHED_DIR = r"C:\GOG Games\Monkey Island 1 SE"
DEFAULT_OUT_DIR = os.path.join(BASE_DIR, "installer", "mi1", "patches")
DEFAULT_GENPAT = r"C:\Program Files (x86)\NSIS\Bin\GenPat.exe"

# Files included in the patch set. Uncomment "MISE.exe" when an exe patch is needed again.
PATCH_TARGETS = (
    # "MISE.exe",
    "Monkey1.pak",
)


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def create_patch(genpat: str, source: str, target: str, output: str) -> None:
    os.makedirs(os.path.dirname(output), exist_ok=True)
    if os.path.exists(output):
        os.remove(output)

    print(f"[*] GenPat: {os.path.basename(source)} -> {os.path.basename(target)} => {os.path.basename(output)}")
    cmd = [genpat, source, target, output, "/R"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"GenPat failed ({result.returncode}) for {source}")


def write_manifest(out_dir: str, entries: list[tuple[str, str, str, str]]) -> None:
    manifest_path = os.path.join(out_dir, "manifest.txt")
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write("# MI1 Hebrew patch manifest\n")
        f.write("# source_sha256  target_sha256  patch_file  direction\n")
        for source_hash, target_hash, patch_name, direction in entries:
            f.write(f"{source_hash}  {target_hash}  {patch_name}  {direction}\n")
    print(f"[+] Wrote {manifest_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create VPatch files for MI1 Hebrew installer.")
    parser.add_argument("--original-dir", default=DEFAULT_ORIG_DIR)
    parser.add_argument("--patched-dir", default=DEFAULT_PATCHED_DIR)
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--genpat", default=DEFAULT_GENPAT)
    args = parser.parse_args()

    if not os.path.isfile(args.genpat):
        print(f"[-] GenPat not found: {args.genpat}", file=sys.stderr)
        return 1

    manifest_entries: list[tuple[str, str, str, str]] = []

    for name in PATCH_TARGETS:
        original = os.path.join(args.original_dir, name)
        patched = os.path.join(args.patched_dir, name)

        for path in (original, patched):
            if not os.path.isfile(path):
                print(f"[-] Missing file: {path}", file=sys.stderr)
                return 1

        original_hash = sha256(original)
        patched_hash = sha256(patched)
        if original_hash == patched_hash:
            print(f"[-] {name}: original and patched files are identical.", file=sys.stderr)
            return 1

        install_patch = os.path.join(args.out_dir, f"{name}.pat")
        revert_patch = os.path.join(args.out_dir, f"{name}.revert.pat")

        print(f"[+] {name}: original={original_hash[:12]}... patched={patched_hash[:12]}...")
        create_patch(args.genpat, original, patched, install_patch)
        create_patch(args.genpat, patched, original, revert_patch)
        manifest_entries.append((original_hash, patched_hash, os.path.basename(install_patch), "install"))
        manifest_entries.append((patched_hash, original_hash, os.path.basename(revert_patch), "revert"))

    write_manifest(args.out_dir, manifest_entries)
    print("[+] Patch files created successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
