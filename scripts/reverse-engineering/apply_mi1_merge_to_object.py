#!/usr/bin/env python3
"""Attach the proclitic preposition (lamed "to" / bet "in") to its object.

Runs *on top of* the RTL component-reversal patch (apply_mi1_verbline_rtl.py).

After reversal the whole verb sentence reaches the low-level text draw
(FUN_0x00485900) as a single, fully-assembled Hebrew string in EDI, e.g.

    [chnvani(obj2)] 20 EA 20 [mehamem] 20 [pesel] 20 [latet]   (bytes, LTR)
     ^object                 ^^ standalone lamed with a space on each side

Because the text is stored reversed-for-LTR, the object sits immediately to
the *left* of the standalone lamed. So we scan the string for the pattern

    0x20 <PREP> 0x20      PREP in {0xEA lamed, 0xE5 bet}

and delete the space *before* PREP by shifting PREP..NUL back one byte. That
glues the proclitic onto the preceding object (=> "lamed+object", no gap) and
keeps the trailing space as the word separator. A lamed/bet that is part of a
word (e.g. "pesel" = ...EA F1...) is followed by a letter, not a space, so it
never matches -- only the engine-inserted standalone preposition does.

We hijack the 5 bytes at 0x0048590B (cmp [esp+44],ebp ; push esi) into a code
cave that does the scan, then restores those two instructions and jumps back
to 0x00485910 (push edi). The cave is placed so it does not overlap the
reversal patch's cave.

Requires: pefile, keystone-engine.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
import time

import pefile

try:
    from keystone import Ks, KS_ARCH_X86, KS_MODE_32
except ImportError:
    print("[-] keystone-engine is required:  pip install keystone-engine", file=sys.stderr)
    raise

IMAGE_BASE = 0x00400000
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GAME_EXE = r"C:\GOG Games\Monkey Island 1 SE\MISE.exe"

# Stage 2 of 2: read the local reversal-patched intermediate produced by
# apply_mi1_verbline_rtl.py and perform the single write into the installed
# game folder.
DEFAULT_INPUT = os.path.join(BASE_DIR, "MISE.tmp.exe")
DEFAULT_OUTPUT = GAME_EXE

# Proclitic prepositions (single Hebrew letter) to glue onto the next word.
LAMED = 0xEA          # "to"
BET = 0xE5            # "in"

# hijack: low-level text draw, FUN_0x00485900 body
HOOK_SITE = 0x0048590B
HOOK_ORIG = bytes.fromhex("396c244456")   # cmp [esp+0x44], ebp ; push esi
HOOK_RETURN = 0x00485910                   # push edi (next original instruction)

# reversal patch hook (to detect + avoid its cave). In a vanilla exe this site
# holds the builder's original tail-jump to 0x497CE0; the reversal patch
# replaces it with a jump to its own cave, so target != 0x497CE0 => patched.
REVERSAL_HOOK_SITE = 0x0047C3DB
REVERSAL_VANILLA_TARGET = 0x00497CE0


def _asm(ks: Ks, asm: str, va: int) -> bytes:
    lines = [ln.split(";", 1)[0].rstrip() for ln in asm.splitlines()]
    lines = [ln for ln in lines if ln.strip()]
    encoding, _ = ks.asm("\n".join(lines), va)
    return bytes(encoding)


def robust_write(path: str, payload: bytes, attempts: int = 12) -> bool:
    """Overwrite an existing file in place (r+b), retrying transient locks.

    Antivirus real-time scanning frequently holds a freshly-touched exe open
    for a short window; a temp-file + rename gets denied (WinError 5) because
    the target is locked, and a fresh 'wb' create can hit EINVAL. Rewriting the
    existing file in place avoids creating a new file for AV to grab and simply
    waits for the lock to clear.
    """
    delay = 0.5
    for i in range(attempts):
        try:
            mode = "r+b" if os.path.exists(path) else "wb"
            with open(path, mode) as f:
                f.seek(0)
                f.write(payload)
                f.truncate(len(payload))
                f.flush()
                os.fsync(f.fileno())
            return True
        except OSError as exc:
            if i == attempts - 1:
                print(f"[-] write failed after {attempts} tries: {exc}", file=sys.stderr)
                return False
            print(f"[!] write attempt {i + 1} failed ({exc}); retrying in {delay:.1f}s...",
                  file=sys.stderr)
            time.sleep(delay)
            delay = min(delay * 2, 5.0)
    return False


def find_code_cave(pe: pefile.PE, min_zeros: int, avoid=(0, 0)):
    """First .text run of >= min_zeros NUL bytes, clipped past [avoid).

    A run that overlaps the avoid region is not discarded: if it extends
    beyond avoid[1] we place the cave at avoid[1] and use the tail. This lets
    the merge cave share the same big free block as the reversal patch,
    starting right after that patch's reserved bytes.
    """
    needle = b"\x00" * min_zeros
    for section in pe.sections:
        if b".text" not in section.Name:
            continue
        data = section.get_data()
        base_va = IMAGE_BASE + section.VirtualAddress
        start = 0
        while True:
            idx = data.find(needle, start)
            if idx == -1:
                break
            run_va = base_va + idx
            run = len(data[idx:]) - len(data[idx:].lstrip(b"\x00"))
            run_end = run_va + run
            # Clip out the avoid region.
            eff_va = run_va
            if run_va < avoid[1] and run_end > avoid[0]:
                eff_va = avoid[1]
            eff_len = run_end - eff_va
            if eff_va >= run_va and eff_len >= min_zeros:
                cave_off = section.PointerToRawData + (eff_va - base_va)
                return cave_off, eff_va, eff_len
            start = idx + run
    return None, None, 0


def build_cave(cave_va: int) -> bytes:
    ks = Ks(KS_ARCH_X86, KS_MODE_32)
    asm = f"""
        pushad
        pushfd
        mov esi, edi                 ; esi = scan pointer, edi = string base

    scan:
        mov al, byte ptr [esi]
        test al, al
        jz done
        cmp al, 0x20                 ; want pattern: 0x20 <prep> 0x20
        jne next
        mov al, byte ptr [esi + 1]
        cmp al, {LAMED}
        je chk2
        cmp al, {BET}
        jne next
    chk2:
        cmp byte ptr [esi + 2], 0x20
        jne next

        ; match: [esi]=space, [esi+1]=prep, [esi+2]=space.
        ; delete the space before the prep by shifting [esi+1..] back one over
        ; it. To keep the overall length unchanged (the draw renders a fixed
        ; count, so a shorter string exposes the terminating NUL as a bogus
        ; glyph), the freed slot is filled with a space instead of shortening:
        ; the original trailing NUL one byte further along stays the terminator.
        mov edx, esi                 ; dst
        lea ecx, [esi + 1]           ; src
    shift:
        mov al, byte ptr [ecx]
        test al, al
        jz pad                       ; reached terminator -> pad, don't shorten
        mov byte ptr [edx], al
        inc edx
        inc ecx
        jmp shift
    pad:
        mov byte ptr [edx], 0x20     ; fill vacated slot; [edx+1] keeps old NUL
        jmp scan

    next:
        inc esi
        jmp scan

    done:
        popfd
        popad
        cmp dword ptr [esp + 0x44], ebp   ; restored original instruction
        push esi                          ; restored original instruction
        jmp {HOOK_RETURN:#x}
    """
    return _asm(ks, asm, cave_va)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        pe = pefile.PE(args.input)
    except Exception as exc:  # noqa: BLE001
        print(f"[-] Could not load {args.input}: {exc}", file=sys.stderr)
        return 1
    with open(args.input, "rb") as f:
        data = bytearray(f.read())

    def off(va: int) -> int:
        return pe.get_offset_from_rva(va - IMAGE_BASE)

    site = off(HOOK_SITE)
    actual = bytes(data[site:site + len(HOOK_ORIG)])
    if actual != HOOK_ORIG:
        print(f"[-] {HOOK_SITE:#010x} has {actual.hex()} (expected {HOOK_ORIG.hex()}). "
              "Input already merge-patched or not a MISE.exe.", file=sys.stderr)
        return 1

    # Detect the reversal patch's cave so we don't overlap it. The reversal
    # patch is required: it reverses component order so the object precedes the
    # preposition in memory, which is what makes the space-before-prep the one
    # to delete.
    rev_off = off(REVERSAL_HOOK_SITE)
    rev_bytes = bytes(data[rev_off:rev_off + 5])
    rev_target = None
    if rev_bytes[0] == 0xE9:
        rev_target = REVERSAL_HOOK_SITE + 5 + struct.unpack("<i", rev_bytes[1:5])[0]

    if rev_target is None or rev_target == REVERSAL_VANILLA_TARGET:
        print("[-] Reversal patch NOT present (0x0047C3DB still targets the "
              "vanilla builder 0x497CE0). This merge must layer on top of the "
              "reversal patch. Run apply_mi1_verbline_rtl.py first, then point "
              "--input at the reversal-patched exe.", file=sys.stderr)
        return 1

    avoid = (rev_target, rev_target + 0x120)
    print(f"[i] Reversal patch detected; cave @ {rev_target:#010x} (avoiding).")

    cave_off, cave_va, cave_run = find_code_cave(pe, min_zeros=96, avoid=avoid)
    if cave_va is None:
        print("[-] No suitable code cave found.", file=sys.stderr)
        return 1
    print(f"[+] Code cave: VA={cave_va:#010x} file={cave_off:#010x} run={cave_run}")

    cave = build_cave(cave_va)
    print(f"[+] cave: {len(cave)} bytes @ {cave_va:#010x}")
    if len(cave) > cave_run:
        print("[-] cave code overflows the free run.", file=sys.stderr)
        return 1

    if args.dry_run:
        print("[i] dry-run: no file written.")
        return 0

    data[cave_off:cave_off + len(cave)] = cave
    rel = cave_va - (HOOK_SITE + 5)
    data[site:site + 5] = b"\xE9" + struct.pack("<i", rel)

    if not robust_write(args.output, bytes(data)):
        print("[-] Could not write output (locked by antivirus/another process?).",
              file=sys.stderr)
        return 1
    print(f"[+] Patched exe written: {args.output}")
    print("[+] Standalone lamed/bet now attaches to its object (no space).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
