"""
RTL Dynamic-Shift Patch  -  dialog-only filter  -  per-string right-anchor

Cave moved to 0x00403851 (252 free bytes) to fit the larger per-string logic.
The old 77-byte cave at 0x004D3B0C is cleared to NOPs.

HOW IT WORKS
============
DrawString is TEXT-ONLY; sprites never go through this path.
At site 0x004DC1DB inside DrawString we intercept the two float stores that
prepare arg0 (cursor_X) and arg1 (draw_X) for the upcoming DrawChar call.

  original:
    fld  [esp+0x4C]  ;  draw_X
    fstp [esp+0x04]
    fld  [esp+0x3C]  ;  cursor_X
    fstp [esp]

We replace those 15 bytes with a JMP into a code cave that:
  1. Checks [EBP+4] against known dialog return addresses (whitelist filter).
  2. For non-dialog callers: does the original stores unchanged (passthrough).
  3. For dialog callers: computes a per-string SHIFT and adds it to both values.

PER-STRING DYNAMIC SHIFT
========================
  cursor_X increases by ~19 with every character within one DrawString call.
  We want ALL characters in the SAME string to receive the SAME shift so the
  word appears at a fixed right anchor without glyph pixel-flipping.

  Detection of "new string" = cursor_X < prev_cursor_X  (cursor reset means
  a new DrawString call started).

  On new-string:  shift = RIGHT_ANCHOR - cursor_X_initial
  On same-string: reuse the stored shift.

  Result: every string's FIRST character lands at x = RIGHT_ANCHOR; subsequent
  characters extend rightward.  Store Hebrew text pre-reversed in memory so
  that LTR rendering produces the correct RTL visual order.

SAVE-GAME SCREEN NOTE
=====================
  Both dialog text AND save-game slot-name text go through 0x004DD250 and
  therefore produce the same [EBP+4] values (0x4DD345 / 0x4DD381).  Until
  we find a runtime discriminator (Y coordinate or game-state variable), both
  are shifted.

  To find the save-game cursor_X: in x32dbg set a breakpoint at 0x004DC1DB
  while the save-game loading screen is open.  Read [ESP+0x3C].  Once known,
  add a coordinate range check here.

CAVE LAYOUT at 0x403851
=======================
  [0x403851]  float RIGHT_ANCHOR        (4 bytes, data)
  [0x403855]  float prev_cursor_x       (4 bytes, data, init 9999.0)
  [0x403859]  float stored_shift        (4 bytes, data, init 0.0)

  [0x40385D]  filter (17 bytes)
    8B 45 04          mov  eax, [ebp+4]
    3D 45 D3 4D 00    cmp  eax, 0x004DD345   ; dialog caller 1
    74 1B             je   apply_rtl  (0x403882)
    3D 81 D3 4D 00    cmp  eax, 0x004DD381   ; dialog caller 2
    74 14             je   apply_rtl  (0x403882)

  [0x40386E]  passthrough (20 bytes)
    D9 44 24 4C       fld  [esp+0x4C]
    D9 5C 24 04       fstp [esp+0x04]
    D9 44 24 3C       fld  [esp+0x3C]
    D9 1C 24          fstp [esp]
    E9 68 89 0D 00    jmp  0x004DC1EA

  [0x403882]  apply_rtl (76 bytes):
    fld cursor_X
    fld prev_cursor_x
    fucomip st(0),st(1)   ; prev < cursor_X ?
    jnc +8  ; new string
    -- same string --
    fstp [prev_cursor_x]
    jmp apply_shift
    -- new string --
    fsubr [RIGHT_ANCHOR]  ; RIGHT_ANCHOR - cursor_X -> shift
    fstp [stored_shift]
    fld cursor_X
    fstp [prev_cursor_x]
    -- apply_shift --
    fld cursor_X
    fadd [stored_shift]
    fstp [esp]
    fld draw_X
    fadd [stored_shift]
    fstp [esp+0x04]
    jmp 0x004DC1EA

Site patch at 0x004DC1DB (15 bytes):
  E9 7D 67 27 FF  90 90 90 90 90  90 90 90 90 90
  (JMP to 0x40385D + 10 NOPs)
"""

import struct, os, stat, sys, subprocess

EXE = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', 'Monkey2.exe'))
IMAGE_BASE = 0x00400000

# Cave code lives in .text (read-only at runtime – cave only READS from here).
NEW_CAVE_VA  = 0x00403851   # 252 free NOP bytes in .text
OLD_CAVE_VA  = 0x004D3B0C   # old 77-byte cave – NOPed during apply
OLD_CAVE_LEN = 77

SITE_VA  = 0x004DC1DB
SITE_LEN = 15

# ------- TUNE THIS VALUE -----------------------------------------------
RIGHT_ANCHOR = 550.0
# The first character of each dialog string will land at x = RIGHT_ANCHOR.
# Subsequent characters extend to the right.
# With RIGHT_ANCHOR=550 and cursor_X_initial=250: shift=+300 (same as the
# manually-tested value that confirmed no glyph pixel-flip).
# Increase to push all dialog text further right; decrease to pull it left.
# -----------------------------------------------------------------------

# Dialog-only filter: apply RTL only when DrawString's return address is one
# of these values (i.e. the call was made from inside 0x004DD250).
DIALOG_RETADDRS = [0x004DD345, 0x004DD381]

# Read-only constant in .text cave (only fld reads it – safe in non-writable section)
RM_VA = NEW_CAVE_VA          # RIGHT_ANCHOR float (4 bytes at 0x403851)

# Mutable state variables MUST be in .data (writable section).
# We use two 4-byte slots from the 624-byte zero-run at 0x5350E8 in .data
# (.data is R=1, W=1, X=0 – confirmed by section flags 0xC0000040).
DATA_PREV_VA  = 0x005350E8   # float prev_cursor_x  (writable, persists across calls)
DATA_SHIFT_VA = 0x005350EC   # float stored_shift   (writable, persists across calls)
DATA_INIT_LEN = 8            # bytes to manage in .data

# Filter / code start inside the .text cave
FILTER_VA   = NEW_CAVE_VA + 12   # 0x40385D  (4 bytes RM + 8 bytes NOPs = 12)
PASSTHRU_VA = FILTER_VA + 17     # 0x40386E
RTL_VA      = PASSTHRU_VA + 20   # 0x403882

def _jmp32(from_va, target_va):
    """Return 4-byte little-endian relative offset for a near JMP / Jcc."""
    return struct.pack('<i', target_va - (from_va + 5)).hex()


def _build_cave():
    rm_le    = struct.pack('<I', RM_VA).hex()
    prev_le  = struct.pack('<I', DATA_PREV_VA).hex()   # points to .data (writable)
    shift_le = struct.pack('<I', DATA_SHIFT_VA).hex()  # points to .data (writable)

    # ---- cave header: 4 bytes RIGHT_ANCHOR + 8 bytes NOP padding = 12 bytes ----
    # Only the RIGHT_ANCHOR is stored here; prev_cursor_x and stored_shift live in .data.
    # This entire cave is in .text (read-only) – the cave code only READS from RM_VA.
    data = (
        struct.pack('<f', RIGHT_ANCHOR).hex()   # RIGHT_ANCHOR float at RM_VA (0x403851)
        + '90' * 8                              # 8 NOP padding bytes (0x403855..0x40385C)
    )

    # ---- filter (17 bytes) ----
    filter_code = '8B4504'   # mov eax, [ebp+4]
    for i, addr in enumerate(DIALOG_RETADDRS):
        filter_code += '3D' + struct.pack('<I', addr).hex()   # cmp eax, addr
        filter_code += '741B' if i == 0 else '7414'           # je apply_rtl
    # je offsets: apply_rtl is at filter+17+20 = filter+37
    #   from first  je (ends at filter+10):  37-10 = 27 = 0x1B  ✓
    #   from second je (ends at filter+17):  37-17 = 20 = 0x14  ✓

    # ---- passthrough (20 bytes) ----
    pt_jmp_off = _jmp32(PASSTHRU_VA + 15, 0x004DC1EA)
    passthrough = (
          'D944244C'        # fld  [esp+0x4C]
        + 'D95C2404'        # fstp [esp+0x04]
        + 'D944243C'        # fld  [esp+0x3C]
        + 'D91C24'          # fstp [esp]
        + 'E9' + pt_jmp_off # jmp  0x004DC1EA
    )

    # ---- apply_rtl (76 bytes) ----
    # jnc +8:  CF=0 means prev >= cursor_X  => new string => jump to NEW STRING block
    # jmp +0x16: from end of "same string" block -> jump to APPLY SHIFT block
    rtl_jmp_off = _jmp32(RTL_VA + 71, 0x004DC1EA)
    rtl = (
        # detect new string (offset 0..13)
          'D944243C'           # (+0)  fld  [esp+0x3C]   cursor_X -> st0
        + 'D905' + prev_le     # (+4)  fld  [PREV_VA]    prev -> st0, cursor_X -> st1
        + 'DFE9'               # (+10) fucomip: compare prev vs cursor_X; pop prev
        + '7308'               # (+12) jnc +8  -> NEW STRING at offset 22
        # SAME STRING (offset 14..21)
        + 'D91D' + prev_le     # (+14) fstp [PREV_VA]   prev = cursor_X
        + 'EB16'               # (+20) jmp +0x16  -> APPLY SHIFT at offset 44
        # NEW STRING (offset 22..43)
        + 'D82D' + rm_le       # (+22) fsubr [RM_VA]    st0 = RIGHT_ANCHOR - cursor_X
        + 'D91D' + shift_le    # (+28) fstp [SHIFT_VA]  stored_shift = shift
        + 'D944243C'           # (+34) fld  [esp+0x3C]  reload cursor_X
        + 'D91D' + prev_le     # (+38) fstp [PREV_VA]   prev = cursor_X
        # APPLY SHIFT (offset 44..75)
        + 'D944243C'           # (+44) fld  [esp+0x3C]  cursor_X
        + 'D805' + shift_le    # (+48) fadd [SHIFT_VA]  cursor_X + shift
        + 'D91C24'             # (+54) fstp [esp]        new cursor_X
        + 'D944244C'           # (+57) fld  [esp+0x4C]  draw_X
        + 'D805' + shift_le    # (+61) fadd [SHIFT_VA]  draw_X + shift
        + 'D95C2404'           # (+67) fstp [esp+0x04]  new draw_X
        + 'E9' + rtl_jmp_off   # (+71) jmp  0x004DC1EA
    )
    # RTL byte count: 4+6+2+2 + 6+2 + 6+6+4+6 + 4+6+3+4+6+4+5 = 76 ✓

    cave = bytes.fromhex(data + filter_code + passthrough + rtl)
    assert len(cave) == 125, 'Cave size mismatch: %d != 125' % len(cave)
    return cave


CAVE_BYTES = _build_cave()

# Original 15 bytes at site 0x004DC1DB (four FP stores before DrawChar call)
SITE_ORIG = bytes.fromhex('D944244C' 'D95C2404' 'D944243C' 'D91C24')  # 15 bytes
assert len(SITE_ORIG) == 15

# New JMP: E9 + 4-byte offset to FILTER_VA (0x40385D), then 10 NOPs
_site_rel = struct.pack('<i', FILTER_VA - (SITE_VA + 5)).hex()
SITE_PATCHED     = bytes.fromhex('E9' + _site_rel + '90' * 10)
# Old site patch (JMP to old cave at 0x4D3B10)
SITE_PATCHED_OLD = bytes.fromhex('E9' + struct.pack('<i', 0x4D3B10 - (SITE_VA + 5)).hex() + '90' * 10)
SITE_OLD8        = bytes.fromhex('E9' + struct.pack('<i', 0x4D3B10 - (SITE_VA + 5)).hex()[:8] + '90' * 3)  # legacy 8-byte

assert len(SITE_PATCHED) == 15


# ── helpers ──────────────────────────────────────────────────────────────────

def check_game_not_running():
    r = subprocess.run(['tasklist'], capture_output=True, text=True)
    if 'Monkey2.exe' in r.stdout:
        print('ERROR: Monkey2.exe is still running – close it and retry.')
        sys.exit(1)


def va_to_off(data, va):
    pe_off = struct.unpack_from('<I', data, 0x3C)[0]
    nsec   = struct.unpack_from('<H', data, pe_off + 6)[0]
    opt_sz = struct.unpack_from('<H', data, pe_off + 0x14)[0]
    sec_tb = pe_off + 0x18 + opt_sz
    for i in range(nsec):
        s = sec_tb + i * 40
        virt_sz, vaddr, raw_sz, raw_off = struct.unpack_from('<IIII', data, s + 8)
        rva = va - IMAGE_BASE
        if vaddr <= rva < vaddr + max(virt_sz, raw_sz):
            return raw_off + (rva - vaddr)
    raise ValueError('VA 0x%08X not found in any section' % va)


# ── apply / restore ───────────────────────────────────────────────────────────

def apply_patch():
    check_game_not_running()
    os.chmod(EXE, os.stat(EXE).st_mode | stat.S_IWRITE)

    with open(EXE, 'rb') as f:
        raw = bytearray(f.read())

    off_nc   = va_to_off(raw, NEW_CAVE_VA)
    off_oc   = va_to_off(raw, OLD_CAVE_VA)
    off_s    = va_to_off(raw, SITE_VA)
    off_prev = va_to_off(raw, DATA_PREV_VA)
    off_shft = va_to_off(raw, DATA_SHIFT_VA)

    current = bytes(raw[off_s:off_s + SITE_LEN])

    if current == SITE_PATCHED:
        print('Site already patched (new cave) - re-writing cave with RIGHT_ANCHOR=%.0f ...' % RIGHT_ANCHOR)
    elif current == SITE_PATCHED_OLD:
        print('Upgrading from old cave (0x4D3B0C) to new dynamic cave ...')
    elif current == SITE_ORIG:
        print('Applying dynamic shift patch from clean state ...')
    else:
        print('ERROR: Unexpected bytes at site: ' + current.hex())
        print('Expected original: ' + SITE_ORIG.hex())
        sys.exit(1)

    # Write .text cave (read-only at runtime – cave only reads from here)
    raw[off_nc:off_nc + len(CAVE_BYTES)] = CAVE_BYTES

    # Clear old .text cave (NOP it out)
    raw[off_oc:off_oc + OLD_CAVE_LEN] = b'\x90' * OLD_CAVE_LEN

    # Initialise mutable state in .data (writable at runtime)
    # prev_cursor_x = 9999.0 so the very first DrawString call is always detected
    # as "new string" and immediately computes the correct shift.
    raw[off_prev:off_prev + 4] = struct.pack('<f', 9999.0)
    raw[off_shft:off_shft + 4] = struct.pack('<f', 0.0)

    # Patch the site
    raw[off_s:off_s + SITE_LEN] = SITE_PATCHED

    with open(EXE, 'wb') as f:
        f.write(raw)

    print('RTL dynamic-shift patch applied!')
    print('  Cave (text)  @ 0x%08X (%d bytes)' % (NEW_CAVE_VA, len(CAVE_BYTES)))
    print('  prev_cursor_x@ 0x%08X (data, writable)' % DATA_PREV_VA)
    print('  stored_shift @ 0x%08X (data, writable)' % DATA_SHIFT_VA)
    print('  Site         @ 0x%08X -> %s' % (SITE_VA, SITE_PATCHED.hex()))
    print()
    print('RIGHT_ANCHOR = %.0f' % RIGHT_ANCHOR)
    print('Formula: shift = RIGHT_ANCHOR - cursor_X_initial  (per string, once)')
    print('         new_cursor_X = cursor_X + shift')
    print('         new_draw_X   = draw_X   + shift')
    print()
    ex = 250.0
    print('Example: cursor_X_initial=250 -> shift=%.0f  first_char_at_x=%.0f' % (
        RIGHT_ANCHOR - ex, RIGHT_ANCHOR))


def restore():
    check_game_not_running()
    os.chmod(EXE, os.stat(EXE).st_mode | stat.S_IWRITE)

    with open(EXE, 'rb') as f:
        raw = bytearray(f.read())

    off_nc   = va_to_off(raw, NEW_CAVE_VA)
    off_oc   = va_to_off(raw, OLD_CAVE_VA)
    off_s    = va_to_off(raw, SITE_VA)
    off_prev = va_to_off(raw, DATA_PREV_VA)
    off_shft = va_to_off(raw, DATA_SHIFT_VA)

    current = bytes(raw[off_s:off_s + SITE_LEN])
    if current in (SITE_PATCHED, SITE_PATCHED_OLD):
        raw[off_s:off_s + SITE_LEN] = SITE_ORIG
        print('Site restored to original bytes.')
    else:
        print('Site already original: ' + current.hex())

    raw[off_nc:off_nc + len(CAVE_BYTES)] = b'\x90' * len(CAVE_BYTES)
    raw[off_oc:off_oc + OLD_CAVE_LEN]    = b'\x90' * OLD_CAVE_LEN
    print('Caves cleared.')

    # Restore .data state slots to zeros
    raw[off_prev:off_prev + DATA_INIT_LEN] = b'\x00' * DATA_INIT_LEN
    print('.data state slots zeroed.')

    with open(EXE, 'wb') as f:
        f.write(raw)
    print('Restore complete.')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'restore':
        restore()
    else:
        apply_patch()
