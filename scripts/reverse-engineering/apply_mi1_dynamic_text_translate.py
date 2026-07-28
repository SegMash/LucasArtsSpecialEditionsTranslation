#!/usr/bin/env python3
"""Fix translation of dynamic-numbered phrases ("N pieces of eight") at draw time.

Runs *on top of* the merge-patched exe (stage 3 of 3):

    1. apply_mi1_verbline_rtl.py  -- reverse component order  (MISE.exe -> MISE.tmp.exe)
    2. apply_mi1_merge_to_object.py -- merge lamed/bet        (MISE.tmp.exe -> game folder MISE.exe)
    3. apply_mi1_dynamic_text_translate.py -- THIS SCRIPT     (game MISE.exe -> same file, in-place rewrite)

Why the engine's own translation fails after stage 1:
    The vanilla engine translates "378 pieces of eight" -> "378 מטבעות כסף" using a
    SUFFIX-MATCH comparator (FUN_0x0046E500) called from FUN_0x0041E588: it loads EDI
    with static "pieces of eight", calls the suffix comparator on the widget text so
    the numeric prefix is naturally skipped (the comparator only matches if the static
    key aligns exactly at the END of the widget string).

    Stage 1 reverses the four screen-order component buffers BEFORE this localizer
    runs, so the concatenated widget text no longer places the static key at the
    tail: the string sits inside an obj component that moved to a DIFFERENT slot,
    breaking the suffix-match pattern. The comparator returns NULL, so the widget
    keeps its English original and reaches the draw function (FUN_0x00485900) as pure
    ASCII.

What this patch does:
    We hijack 8 bytes at the VERY START of FUN_0x00485900 (the text draw entry,
    before the merge patch's own hook at 0x48590B, so both patches chain naturally).
    In the cave we:

        1. pushad/pushfd
        2. strlen(EDI)
        3. does string end with "pieces of eight" (15) or "piece of eight" (14)?
           (pure ASCII = all chars < 0x80, so Hebrew strings won't match by accident)
        4. YES -> overwrite that suffix with the pre-encoded Hebrew equivalent
                  (reversed-for-LTR, letters-only, no 0xF0 prefix -- identical
                  encoding to what inject_translation_mi1.py writes for line 143/144)
                  Any length delta is padded with spaces so the buffer size and the
                  original trailing NUL stay where the widget metadata expects them.
        5. NO  -> do nothing
        6. popfd/popad
        7. restore the 8 original bytes of FUN_0x00485900
        8. jmp 0x485908 (immediately after our 8-byte stolen window; the merge
           patch at 0x48590B then runs normally on the now-Hebrew string).

Requires: pefile, keystone-engine.
"""

from __future__ import annotations

import argparse
import importlib.util as _il
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
_mapping_path = os.path.join(BASE_DIR, "scripts", "fonts", "hebrew_mapping.py")
_spec = _il.spec_from_file_location("hebrew_mapping", _mapping_path)
_mod = _il.module_from_spec(_spec)
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
HEBREW_TO_CODE: dict[str, int] = _mod.HEBREW_TO_CODE

GAME_EXE = r"C:\GOG Games\Monkey Island 1 SE\MISE.exe"
DEFAULT_INPUT = GAME_EXE
DEFAULT_OUTPUT = GAME_EXE

# --- hooks ----------------------------------------------------------------
# Stage 3 hook: first 8 bytes of FUN_0x00485900 (draw entry).
# Occupies 3 + 1 + 4 = 8 bytes exactly; we JMP out in 5 bytes + 3 NOPs = 8 total.
HOOK_SITE = 0x00485900
HOOK_ORIG = bytes.fromhex("83ec34538b5c243c")   # sub esp,0x34 ; push ebx ; mov ebx,[esp+0x3c]
HOOK_RETURN = 0x00485908                        # push ebp  (instruction after the 8 bytes)

# Predecessor hooks (for cave avoidance + dependency sanity check).
REVERSAL_HOOK_SITE = 0x0047C3DB
REVERSAL_VANILLA_TARGET = 0x00497CE0
MERGE_HOOK_SITE = 0x0048590B
MERGE_ORIG = bytes.fromhex("396c244456")         # cmp [esp+0x44],ebp ; push esi

# English suffix keys (bytes). Compare is byte-for-byte against a tail of EDI.
PIECES_EN = b"pieces of eight"                   # 15 bytes
PIECE_EN = b"piece of eight"                     # 14 bytes
GUYBRUSH_EN = b"Guybrush"                        # 8 bytes (actor-name hover tooltip on map)
assert len(PIECES_EN) == 15
assert len(PIECE_EN) == 14
assert len(GUYBRUSH_EN) == 8


def encode_hebrew_reversed(heb: str) -> bytes:
    """Encode a Hebrew string using the custom font code page, letters reversed.

    This matches what inject_translation_mi1.py produces when the uitext line
    is flagged [REVERSE] (which is the default for Hebrew MI1 uitext rows):
    letters in each non-token segment are reversed for the LTR renderer, then
    each letter is mapped through HEBREW_TO_CODE, spaces stay 0x20, and no
    0xF0 prefix is added.
    """
    chars = list(heb)
    chars = chars[::-1]
    out = bytearray()
    for ch in chars:
        if ch in HEBREW_TO_CODE:
            out.append(HEBREW_TO_CODE[ch])
        elif ch == " ":
            out.append(0x20)
        else:
            raise ValueError(f"Character {ch!r} has no Hebrew code mapping")
    return bytes(out)


HEB_PLURAL = encode_hebrew_reversed("מטבעות כסף")            # line 144 (en: pieces of eight)
HEB_SINGULAR = encode_hebrew_reversed("מטבע של כסף")         # line 143 (en: piece of eight)
GUYBRUSH_HEB = encode_hebrew_reversed("ברנשצבע ")            # actor "Guybrush" map-hover tooltip (trailing space forces len=8 parity)
assert len(GUYBRUSH_HEB) == len(GUYBRUSH_EN), f"Guybrush enc parity fail: heb={len(GUYBRUSH_HEB)} != en={len(GUYBRUSH_EN)}"

# Buffer-length management: original English suffix tail is N bytes; we write
# M < N Hebrew bytes then pad with (N - M) spaces so the overall string length
# and the final NUL terminator keep their vanilla positions. This keeps the
# widget length field (which may be cached) consistent with storage.
PLURAL_PAD = len(PIECES_EN) - len(HEB_PLURAL)
SINGULAR_PAD = len(PIECE_EN) - len(HEB_SINGULAR)
assert PLURAL_PAD >= 0, f"Hebrew plural ({len(HEB_PLURAL)}B) longer than English {len(PIECES_EN)}B"
assert SINGULAR_PAD >= 0, f"Hebrew singular ({len(HEB_SINGULAR)}B) longer than English {len(PIECE_EN)}B"


def _asm(ks: Ks, asm_text: str, va: int) -> bytes:
    lines = [ln.split(";", 1)[0].rstrip() for ln in asm_text.splitlines()]
    lines = [ln for ln in lines if ln.strip()]
    encoding, _ = ks.asm("\n".join(lines), va)
    return bytes(encoding)


def robust_write(path: str, payload: bytes, attempts: int = 12) -> bool:
    """In-place overwrite with retry on transient antivirus locks."""
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


def find_code_cave(pe: pefile.PE, min_zeros: int, avoid_list) -> tuple:
    """First .text free run, clipping past every avoid region in avoid_list."""
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
            eff_va = run_va
            for (av_start, av_end) in avoid_list:
                if eff_va < av_end and run_end > av_start:
                    eff_va = max(eff_va, av_end)
            eff_len = run_end - eff_va
            if eff_va >= run_va and eff_len >= min_zeros:
                cave_off = section.PointerToRawData + (eff_va - base_va)
                return cave_off, eff_va, eff_len
            start = idx + run
    return None, None, 0


def build_cave(cave_va: int) -> bytes:
    """Whole-word substring search in EDI, replace matches with Hebrew.

    Three independent passes (all fast-filter by first-char to avoid false work):
        PASS 1: PLURAL   "pieces of eight" (15B) -> Hebrew 10B + left-shift tail + 5 trailing SPACES (strlen preserved)
        PASS 2: SINGULAR "piece of eight"  (14B) -> Hebrew 11B + left-shift tail + 3 trailing SPACES (strlen preserved)
        PASS 3: NAME     "Guybrush"        (8B)  -> Hebrew 8B  (perfect parity, no shift/pad needed)

    The first two passes interact with the verb-line RTL concatenator: stage-1 reversed the
    component order BEFORE the engine's suffix-comparator localizer could fire, so the
    "pieces of eight" substring ends up in the MIDDLE of the widget string instead of at
    its tail (which the comparator requires). We fix that by strstr'ing anywhere, guarded
    by word-boundaries (space or NUL on both sides) so longer tokens like "xpieces of
    eighty" are not accidentally matched. The word-boundary guard also prevents "Xguybrush"
    or "GuybrushX" from matching PASS 3 (though unlikely to appear in practice).

    Pass 3 length is perfectly symmetric (8 == 8), so it's a pure memcpy with no buffer
    management concerns.
    """
    ks = Ks(KS_ARCH_X86, KS_MODE_32)
    STRIDE = 16
    P_FIRST = PIECES_EN[0]       # 'p' (first char common to both plural & singular)
    G_FIRST = GUYBRUSH_EN[0]     # 'G' = 0x47 (fast filter for Guybrush pass)

    dummy_asm = f"""
        pushad
        pushfd
        mov ebp, edi                  ; ebp = original draw-string base (for begin-of-string boundary check)

        ; ---- PASS 1: try PLURAL "pieces of eight" (15 chars) ----
        mov esi, ebp                  ; esi = scan cursor
    scan_pl:
        mov al, byte ptr [esi]
        test al, al
        jz scan_sing                  ; end-of-string -> move to singular pass
        cmp al, {P_FIRST}
        jne next_pl                   ; no match, advance
        ; First-char match -> compare 15 bytes
        push esi
        push edi
        lea edi, [0xDEADB000]         ; pieces_en label (dummy VA)
        mov ecx, {len(PIECES_EN)}
        cld
        repe cmpsb
        pop edi
        pop esi
        jne next_pl
        ; Word boundary BEFORE: esi == string start OR byte-at-[esi-1] == space
        cmp esi, ebp
        je wb_ok_pl
        cmp byte ptr [esi - 1], 0x20
        jne next_pl
    wb_ok_pl:
        ; Word boundary AFTER: byte at [esi + 15] is NUL or space
        mov al, byte ptr [esi + {len(PIECES_EN)}]
        test al, al
        je match_plural
        cmp al, 0x20
        je match_plural
        jne next_pl
    match_plural:
        ; Write Hebrew plural (10 bytes) starting at esi (match_start)
        push esi
        pop edi
        lea esi, [0xDEADB020]         ; heb_plural label
        mov ecx, {len(HEB_PLURAL)}
        cld
        rep movsb
        ; edi now = match_start + {len(HEB_PLURAL)}  (= where shifted tail should begin)
        ; src = match_start + {len(PIECES_EN)}  (= original tail position = start of text to LEFT-SHIFT)
        lea esi, [edi + {PLURAL_PAD}]   ; esi = src
        ; ---- merge-patch style shift: copy bytes src->dst until NUL reached ----
        ; Length is kept IDENTICAL: the N freed slots (= PLURAL_PAD) are filled with SPACE,
        ; and the original trailing NUL stays at its absolute old position. Thus strlen()
        ; returns the same value the engine's cached draw-count expects, so no NUL glyph
        ; boxes are rendered past the actual content.
    shift_pl:
        mov al, byte ptr [esi]
        test al, al
        jz pad_pl                    ; hit NUL -> pad the freed N slots, DON'T move the terminator
        mov byte ptr [edi], al
        inc esi
        inc edi
        jmp shift_pl
    pad_pl:
        ; Write PLURAL_PAD spaces at edi (edi = exactly where the original NUL was MINUS PAD,
        ; so the LAST space writes to edi+PAD-1, and the ORIGINAL NUL at edi+PAD remains untouched).
        mov ecx, {PLURAL_PAD}
        mov al, 0x20
        rep stosb
        jmp done_all
    next_pl:
        inc esi
        jmp scan_pl

        ; ---- PASS 2: try SINGULAR "piece of eight" (14 chars) ----
    scan_sing:
        mov esi, ebp
    sng_loop:
        mov al, byte ptr [esi]
        test al, al
        jz scan_guy                  ; end-of-string -> move to name (Guybrush) pass
        cmp al, {P_FIRST}
        jne next_sng
        push esi
        push edi
        lea edi, [0xDEADB010]         ; piece_en label (dummy)
        mov ecx, {len(PIECE_EN)}
        cld
        repe cmpsb
        pop edi
        pop esi
        jne next_sng
        cmp esi, ebp
        je wb_ok_sng
        cmp byte ptr [esi - 1], 0x20
        jne next_sng
    wb_ok_sng:
        mov al, byte ptr [esi + {len(PIECE_EN)}]
        test al, al
        je match_sing
        cmp al, 0x20
        je match_sing
        jne next_sng
    match_sing:
        push esi
        pop edi
        lea esi, [0xDEADB030]         ; heb_singular label (dummy)
        mov ecx, {len(HEB_SINGULAR)}
        cld
        rep movsb
        lea esi, [edi + {SINGULAR_PAD}]
    shift_sing:
        mov al, byte ptr [esi]
        test al, al
        jz pad_sing
        mov byte ptr [edi], al
        inc esi
        inc edi
        jmp shift_sing
    pad_sing:
        mov ecx, {SINGULAR_PAD}
        mov al, 0x20
        rep stosb
        jmp done_all
    next_sng:
        inc esi
        jmp sng_loop

        ; ---- PASS 3: try ACTOR NAME "Guybrush" (8 chars, perfect length parity) ----
    scan_guy:
        mov esi, ebp
    guy_loop:
        mov al, byte ptr [esi]
        test al, al
        jz done_all
        cmp al, {G_FIRST}              ; fast filter 'G' = 0x47
        jne next_guy
        push esi
        push edi
        lea edi, [0xDEADB040]          ; guybrush_en label (dummy)
        mov ecx, {len(GUYBRUSH_EN)}
        cld
        repe cmpsb
        pop edi
        pop esi
        jne next_guy
        ; Word boundary BEFORE
        cmp esi, ebp
        je wb_ok_guy
        cmp byte ptr [esi - 1], 0x20
        jne next_guy
    wb_ok_guy:
        mov al, byte ptr [esi + {len(GUYBRUSH_EN)}]
        test al, al
        je match_guy
        cmp al, 0x20
        je match_guy
        jne next_guy
    match_guy:
        ; 8 == 8 byte parity: just rep movsb, no pad/shift needed.
        push esi
        pop edi
        lea esi, [0xDEADB050]          ; guybrush_heb label (dummy)
        mov ecx, {len(GUYBRUSH_HEB)}
        cld
        rep movsb
        jmp done_all
    next_guy:
        inc esi
        jmp guy_loop

    done_all:
        popfd
        popad
        ; ---- restore 8 stolen bytes of FUN_0x00485900 ----
        sub esp, 0x34
        push ebx
        mov ebx, dword ptr [esp + 0x3C]
        jmp {HOOK_RETURN:#x}
    """
    dummy_code = _asm(ks, dummy_asm, cave_va)
    code_size = len(dummy_code)
    code_padded = (code_size + STRIDE - 1) & ~(STRIDE - 1)
    data_base = cave_va + code_padded
    pieces_en_va     = data_base + STRIDE * 0
    piece_en_va      = data_base + STRIDE * 1
    heb_plural_va    = data_base + STRIDE * 2
    heb_singular_va  = data_base + STRIDE * 3
    guybrush_en_va   = data_base + STRIDE * 4
    guybrush_heb_va  = data_base + STRIDE * 5

    asm = dummy_asm.replace("0xDEADB000", f"{pieces_en_va:#x}") \
                   .replace("0xDEADB010", f"{piece_en_va:#x}")  \
                   .replace("0xDEADB020", f"{heb_plural_va:#x}") \
                   .replace("0xDEADB030", f"{heb_singular_va:#x}") \
                   .replace("0xDEADB040", f"{guybrush_en_va:#x}") \
                   .replace("0xDEADB050", f"{guybrush_heb_va:#x}")
    code = _asm(ks, asm, cave_va)
    assert len(code) <= code_padded, f"code grew unexpectedly: {len(code)} > {code_padded}"
    total = code_padded + STRIDE * 6
    buf = bytearray(total)
    buf[:len(code)] = code
    off = code_padded
    buf[off:off + len(PIECES_EN)] = PIECES_EN
    off += STRIDE
    buf[off:off + len(PIECE_EN)] = PIECE_EN
    off += STRIDE
    buf[off:off + len(HEB_PLURAL)] = HEB_PLURAL
    off += STRIDE
    buf[off:off + len(HEB_SINGULAR)] = HEB_SINGULAR
    off += STRIDE
    buf[off:off + len(GUYBRUSH_EN)] = GUYBRUSH_EN
    off += STRIDE
    buf[off:off + len(GUYBRUSH_HEB)] = GUYBRUSH_HEB
    return bytes(buf)


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

    # --- Sanity: our 8-byte hook site is vanilla? ------
    site = off(HOOK_SITE)
    actual = bytes(data[site:site + len(HOOK_ORIG)])
    if actual != HOOK_ORIG:
        print(f"[-] {HOOK_SITE:#010x} has {actual.hex()} (expected {HOOK_ORIG.hex()}). "
              "Input already stage-3 patched or not a MISE.exe with stages 1+2 applied.",
              file=sys.stderr)
        return 1

    # --- Check that stages 1 AND 2 are present (required dependencies) ---
    rev_off = off(REVERSAL_HOOK_SITE)
    rev_bytes = bytes(data[rev_off:rev_off + 5])
    rev_target = None
    if rev_bytes[0] == 0xE9:
        rev_target = REVERSAL_HOOK_SITE + 5 + struct.unpack("<i", rev_bytes[1:5])[0]
    if rev_target is None or rev_target == REVERSAL_VANILLA_TARGET:
        print("[-] Stage 1 (reversal) patch NOT present. Run apply_mi1_verbline_rtl.py "
              "first, then apply_mi1_merge_to_object.py, then this script.",
              file=sys.stderr)
        return 1

    merge_off = off(MERGE_HOOK_SITE)
    merge_bytes = bytes(data[merge_off:merge_off + 5])
    merge_target = None
    if merge_bytes[0] == 0xE9:
        merge_target = MERGE_HOOK_SITE + 5 + struct.unpack("<i", merge_bytes[1:5])[0]
    if merge_target is None or bytes(data[merge_off:merge_off + len(MERGE_ORIG)]) == MERGE_ORIG:
        print("[-] Stage 2 (merge) patch NOT present. Run apply_mi1_merge_to_object.py "
              "on top of stage 1 first, then this script.", file=sys.stderr)
        return 1

    avoid = [
        (rev_target, rev_target + 0x80),       # stage 1 actual = ~109 bytes, margin 128 (generous, stays clear)
        (merge_target, merge_target + 0x48),   # stage 2 actual = ~68 bytes, margin 72 = actual + 4 (tight, since we need that slot for stage-3)
    ]
    print(f"[i] Stage 1 reversal cave @ {rev_target:#010x}")
    print(f"[i] Stage 2 merge cave @ {merge_target:#010x}")

    _probe = build_cave(0x00400000)
    min_cave = len(_probe)  # no extra padding; cave size exactly len(_probe), stride-padded internally
    cave_off, cave_va, cave_run = find_code_cave(pe, min_zeros=min_cave, avoid_list=avoid)
    if cave_va is None:
        print(f"[-] No suitable code cave found (need >= {min_cave} free bytes clipped past both prior caves).",
              file=sys.stderr)
        return 1
    print(f"[+] Code cave: VA={cave_va:#010x} file={cave_off:#010x} run={cave_run}")

    cave = build_cave(cave_va)
    print(f"[+] cave: {len(cave)} bytes @ {cave_va:#010x} (need {min_cave}, have {cave_run})")
    print(f"[+]   HEB_PLURAL len={len(HEB_PLURAL)} ({len(PIECES_EN)} English -> pad {PLURAL_PAD})")
    print(f"[+]   HEB_SINGULAR len={len(HEB_SINGULAR)} ({len(PIECE_EN)} English -> pad {SINGULAR_PAD})")
    print(f"[+]   GUYBRUSH len={len(GUYBRUSH_HEB)} (perfect parity with {len(GUYBRUSH_EN)} English)")
    if len(cave) > cave_run:
        print(f"[-] cave overflows free run by {len(cave) - cave_run} bytes.", file=sys.stderr)
        return 1

    if args.dry_run:
        print("[i] dry-run: no file written.")
        return 0

    data[cave_off:cave_off + len(cave)] = cave
    rel = cave_va - (HOOK_SITE + 5)
    data[site:site + 5] = b"\xE9" + struct.pack("<i", rel)
    # Stolen window = 8 bytes; JMP uses 5, fill remaining 3 with NOP.
    data[site + 5:site + 8] = b"\x90\x90\x90"

    if not robust_write(args.output, bytes(data)):
        print("[-] Could not write output (locked by antivirus/another process?).",
              file=sys.stderr)
        return 1
    print(f"[+] Stage 3 patched exe written: {args.output}")
    print("[+] Dynamic-numbered phrases (N pieces of eight) now translate to Hebrew at draw time.")
    print("[+] Actor tooltip (Guybrush hover map name) now Hebrew-replaced (8 byte parity, no null boxes).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
