"""
String-Reversal Patch for DrawString  (ALL call-sites)
=======================================================

PROBLEM
-------
The game engine splits long Hebrew text into lines and passes each line
individually to DrawString.  If the translated string is stored in natural
(straight) Hebrew order the line *order* is correct (line 1 at top), but
each line is rendered left-to-right so it appears backwards visually.
This affects dialog text, tooltip/hover text, UI labels, and any other
string rendered through DrawString.

SOLUTION
--------
Scan the entire .text section for every CALL DrawString instruction and
replace each one with a CALL to a small wrapper that lives in a free NOP
region of .text.  The wrapper:
  1. Reads the string-pointer argument (arg3 = [ESP+0x1C] after 4 pushes).
  2. Advances a ring-buffer slot index (4 slots × 128 bytes in .data).
  3. Reverses the bytes into the next available ring slot.
  4. Replaces the string-pointer argument on the caller's stack with the
     slot address (so DrawString sees the reversed copy).
  5. Tail-calls DrawString normally.

Previously only the 2 call-sites inside the dialog renderer (0x004DD250)
were patched; mouse-pointer / tooltip sites were excluded.  Now ALL sites
are patched so every string type is reversed.

WHY A RING BUFFER?
------------------
The game uses batched (deferred) rendering.  DrawString adds quads to a
sprite batch during its call, but the GPU draw happens later when the batch
is flushed.  With a single static buffer, a second DrawString call (for
line 2) overwrites the buffer before line 1 has been drawn, causing line 1
to display line 2's reversed content.

Using a rotating ring of 4 slots (128 bytes each) ensures that at least 4
consecutive DrawString calls can coexist without colliding.  For a 2-line
dialog each rendered with shadow + foreground (= 4 calls total) this is
exactly sufficient.  Non-dialog strings (tooltips, UI labels) are typically
single calls whose batch is flushed before the next string, so they never
collide with each other or with dialog slots.

BUG FIXED (strlen → arg4)
--------------------------
The original wrapper used strlen to measure the string.  The engine passes
DrawString as a (ptr, char_count) pair; the ptr for line 1 points at the
very start of the full message buffer, which is null-terminated only at the
very end—not between lines.  strlen therefore returned the length of ALL
remaining lines concatenated, not just line 1.  The wrapper reversed the
entire buffer into line 1's slot; the result ended with the reversed first-
line text, producing the "second-line data inside first-line display" bug.

Fix: read the char_count from arg4 ([esp+0x20] after the four register
pushes) when it is in the range [1..127].  Fall back to strlen only when
arg4 is ≤ 0 or > 127 (handles the -1 "draw full string" sentinel and other
edge values).  The clamp now uses an unsigned JBE so that 0xFFFFFFFF is
treated as > 127 and triggers the strlen fallback rather than silently
passing as a small signed value.

KEY ADDRESSES
-------------
  DrawString       0x004DBFA0   renders one line of text
  Call-sites       auto-discovered by scanning .text for CALL DrawString
  Wrapper cave     0x00403851   252 free NOP bytes in .text (103 bytes used)
  Ring index       0x005350E8   4-byte ring slot counter in .data
  Ring buffers     0x005350EC   4 × 128 = 512 bytes of slot buffers in .data
  Total .data      516 bytes    (within the ~624-byte free run at 0x5350E8)

DrawString prologue (confirmed from binary):
  55 8B EC 83 E4 F8 83 EC 2C 53 8B 5D 08 33 C0 56 57 39 45 0C 0F 84 ...
  push ebp / mov ebp,esp / and esp,FFFFFFF8 / sub esp,2C / push ebx
  mov  ebx, [ebp+8]    <- 1st arg = SpriteFont object
  cmp  [ebp+0Ch], eax  <- 2nd arg null-check (parent context)
  ...
  mov  ecx, [ebp+10h]  <- 3rd arg = STRING POINTER  (at 0x4DC06F)
  movzx dx, byte [eax+ecx]  <- eax=char_index, ecx=string_ptr
  cmp  [ebp+14h]       <- 4th arg = char count  ← now used by wrapper

STACK LAYOUT in wrapper after push eax/ecx/esi/edi (16-byte frame):
  [esp+0x1C]  arg3 = string ptr   (read, then replaced with slot addr)
  [esp+0x20]  arg4 = char count   (used as string length when in 1..127)

WRAPPER CODE (103 bytes at 0x403851):
  offset  hex                      notes
  ------  -----------------------  ------------------------------------------
   0      50                       push eax
   1      51                       push ecx
   2      56                       push esi
   3      57                       push edi
   4      A1 xx xx xx xx           mov eax, [RING_IDX_VA]    ; current slot
   9      40                       inc eax
  10      83 E0 03                 and eax, 3                ; mod 4
  13      A3 xx xx xx xx           mov [RING_IDX_VA], eax    ; save back
  18      C1 E0 07                 shl eax, 7                ; × 128
  21      05 xx xx xx xx           add eax, RING_BUF_VA      ; slot address
  26      89 C7                    mov edi, eax
  28      8B 74 24 1C              mov esi, [esp+0x1C]       ; string ptr (arg3)
  32      8B 54 24 20              mov edx, [esp+0x20]       ; char count (arg4)
  36      85 D2                    test edx, edx
  38      7E 09                    jle do_strlen  (+9 => 49)
  40      83 FA 7F                 cmp edx, 127
  43      7F 04                    jg  do_strlen  (+4 => 49)
  45      89 D1                    mov ecx, edx              ; use arg4 as length
  47      EB 0B                    jmp count_ready  (+11 => 60)
  49      31 C9                    xor ecx, ecx              ; do_strlen:
  51      80 3C 0E 00              cmp byte [esi+ecx], 0     ; strlen_loop
  55      74 03                    je count_ready  (+3 => 60)
  57      41                       inc ecx
  58      EB F7                    jmp strlen_loop  (-9 => 51)
  60      85 C9                    test ecx, ecx             ; count_ready
  62      74 1E                    je skip_reversal  (+30 => 94)
  64      83 F9 7F                 cmp ecx, 127              ; unsigned clamp
  67      76 05                    jbe no_clamp  (+5 => 74)
  69      B9 7F000000              mov ecx, 127              ; full 32-bit clear
  74      89 44 24 1C              mov [esp+0x1C], eax       ; no_clamp: replace arg
  78      8D 74 0E FF              lea esi, [esi+ecx-1]      ; -> last char
  82      8A 06                    mov al, [esi]             ; copy_loop
  84      88 07                    mov [edi], al
  86      4E                       dec esi
  87      47                       inc edi
  88      49                       dec ecx
  89      75 F7                    jnz copy_loop  (-9 => 82)
  91      C6 07 00                 mov byte [edi], 0         ; null-terminate
  94      5F                       pop edi                   ; skip_reversal
  95      5E                       pop esi
  96      59                       pop ecx
  97      58                       pop eax
  98      E9 xx xx xx xx           jmp DrawString  (tail-call)
  Total: 103 bytes
"""

import os, struct, sys, ctypes

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
EXE_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'Monkey2.exe')
EXE_PATH = os.path.normpath(EXE_PATH)

IMAGE_BASE    = 0x00400000
DRAWSTRING_VA = 0x004DBFA0
CALL_SITE_LEN = 5

WRAPPER_VA    = 0x00403851   # 252-byte free NOP area in .text
WRAPPER_LEN   = None         # computed dynamically from _build_wrapper()

# Ring buffer in .data (writable)
RING_IDX_VA   = 0x005350E8   # 4 bytes: current ring slot counter (0..3)
RING_BUF_VA   = 0x005350EC   # 4 slots × 128 bytes = 512 bytes of slot buffers
RING_SLOTS    = 4
RING_SLOT_SZ  = 128
RING_DATA_LEN = 4 + RING_SLOTS * RING_SLOT_SZ  # 516 bytes total

# ---------------------------------------------------------------------------
# PE helpers
# ---------------------------------------------------------------------------
def _load_exe():
    with open(EXE_PATH, 'rb') as f:
        return bytearray(f.read())

def _save_exe(data: bytearray):
    attrs = ctypes.windll.kernel32.GetFileAttributesW(EXE_PATH)
    if attrs & 1:  # FILE_ATTRIBUTE_READONLY
        ctypes.windll.kernel32.SetFileAttributesW(EXE_PATH, attrs & ~1)
    with open(EXE_PATH, 'wb') as f:
        f.write(data)

def _va_to_off(data: bytearray, va: int) -> int:
    pe_off = struct.unpack_from('<I', data, 0x3C)[0]
    nsec   = struct.unpack_from('<H', data, pe_off + 6)[0]
    opt_sz = struct.unpack_from('<H', data, pe_off + 0x14)[0]
    sec_tb = pe_off + 0x18 + opt_sz
    rva = va - IMAGE_BASE
    for i in range(nsec):
        s = sec_tb + i * 40
        vs, vaddr, rs, roff = struct.unpack_from('<IIII', data, s + 8)
        if vaddr <= rva < vaddr + max(vs, rs):
            return roff + (rva - vaddr)
    raise ValueError(f'VA 0x{va:08X} not found in any section')

def _rel32(src_va: int, dst_va: int) -> bytes:
    """32-bit relative offset for a JMP/CALL instruction at src_va (5-byte)."""
    offset = dst_va - (src_va + 5)
    return struct.pack('<i', offset)


def _find_call_sites(data: bytearray) -> list[int]:
    """Scan the .text section for every CALL DrawString (E8 rel32) instruction.

    Returns a sorted list of virtual addresses of matching CALL instructions.
    Only the .text section is scanned to avoid data-section false positives.
    """
    pe_off = struct.unpack_from('<I', data, 0x3C)[0]
    nsec   = struct.unpack_from('<H', data, pe_off + 6)[0]
    opt_sz = struct.unpack_from('<H', data, pe_off + 0x14)[0]
    sec_tb = pe_off + 0x18 + opt_sz

    text_va = text_raw_off = text_raw_sz = None
    for i in range(nsec):
        s    = sec_tb + i * 40
        name = data[s:s + 8].rstrip(b'\x00')
        if name == b'.text':
            text_va      = struct.unpack_from('<I', data, s + 12)[0]
            text_raw_sz  = struct.unpack_from('<I', data, s + 16)[0]
            text_raw_off = struct.unpack_from('<I', data, s + 20)[0]
            break

    if text_raw_off is None:
        raise ValueError('.text section not found in PE headers')

    sites = []
    limit = text_raw_sz - 5
    for i in range(limit):
        if data[text_raw_off + i] != 0xE8:
            continue
        rel    = struct.unpack_from('<i', data, text_raw_off + i + 1)[0]
        src_va = IMAGE_BASE + text_va + i
        if src_va + 5 + rel == DRAWSTRING_VA:
            sites.append(src_va)

    return sorted(sites)


def _orig_call_bytes(site_va: int) -> bytes:
    """Reconstruct the original CALL DrawString bytes for a given call-site VA."""
    return b'\xE8' + _rel32(site_va, DRAWSTRING_VA)


def _patched_call_bytes(site_va: int) -> bytes:
    """Return the patched CALL Wrapper bytes for a given call-site VA."""
    return b'\xE8' + _rel32(site_va, WRAPPER_VA)

# ---------------------------------------------------------------------------
# Build wrapper bytecode
# ---------------------------------------------------------------------------
def _build_wrapper() -> bytes:
    """Build the string-reversal wrapper.

    ROOT CAUSE OF THE ORIGINAL strlen BUG
    --------------------------------------
    The engine passes DrawString a (ptr, char_count) pair.  The ptr for the
    first line points at the very start of the full message buffer, which is
    only null-terminated at the very end—not between lines.  Using strlen
    therefore overcounts: for a 10-char first line followed by 82 more chars,
    strlen returns 92, and the wrapper reversed the *entire* buffer into the
    first line's slot.  The reversed 92-char string ends with the reversed
    first-line text, which is exactly the "second-line data injected into the
    first line" symptom.

    FIX: prefer the char_count from arg4 ([esp+0x20] after the 4 pushes).
    Fall back to strlen only when arg4 is outside [1..127] (handles arg4=-1
    "draw full string" sentinel and other edge cases).  The clamp now uses an
    unsigned JBE so that 0xFFFFFFFF (-1) is correctly treated as > 127.

    STACK LAYOUT after push eax/ecx/esi/edi (16 bytes below entry ESP):
      [esp+0x00] saved edi
      [esp+0x04] saved esi
      [esp+0x08] saved ecx
      [esp+0x0C] saved eax
      [esp+0x10] return address
      [esp+0x14] arg1  SpriteFont*
      [esp+0x18] arg2  parent context
      [esp+0x1C] arg3  string ptr        ← read + replaced with slot addr
      [esp+0x20] arg4  char count        ← used as length when valid
    """
    idx_le = struct.pack('<I', RING_IDX_VA)
    buf_le = struct.pack('<I', RING_BUF_VA)

    code = bytearray()

    # --- Prologue: save registers (identical caller-side ABI as before) ---
    code += bytes([0x50, 0x51, 0x56, 0x57])  # push eax; push ecx; push esi; push edi

    # --- Ring slot management ---
    code += bytes([0xA1]) + idx_le            # mov eax, [RING_IDX_VA]
    code += bytes([0x40])                     # inc eax
    code += bytes([0x83, 0xE0, 0x03])         # and eax, 3  (mod 4)
    code += bytes([0xA3]) + idx_le            # mov [RING_IDX_VA], eax
    code += bytes([0xC1, 0xE0, 0x07])         # shl eax, 7  (× 128)
    code += bytes([0x05]) + buf_le            # add eax, RING_BUF_VA  (slot address)
    code += bytes([0x89, 0xC7])               # mov edi, eax

    # --- Load string pointer (arg3) ---
    code += bytes([0x8B, 0x74, 0x24, 0x1C])  # mov esi, [esp+0x1C]

    # --- Determine string length ---
    # Try arg4 (char count) first; fall back to strlen if arg4 is not in [1..127].
    # EDX is caller-saved (volatile), so no push/pop needed.
    code += bytes([0x8B, 0x54, 0x24, 0x20])  # mov edx, [esp+0x20]  (arg4)
    code += bytes([0x85, 0xD2])              # test edx, edx

    jle_strlen = len(code)
    code += bytes([0x7E, 0x00])              # jle do_strlen  (arg4 <= 0)

    code += bytes([0x83, 0xFA, 0x7F])        # cmp edx, 127

    jg_strlen = len(code)
    code += bytes([0x7F, 0x00])              # jg  do_strlen  (arg4 > 127, incl. -1)

    code += bytes([0x89, 0xD1])              # mov ecx, edx   (use arg4 as length)

    jmp_ready = len(code)
    code += bytes([0xEB, 0x00])              # jmp count_ready

    # do_strlen: (fallback — only reached when arg4 is unusable)
    do_strlen_off = len(code)
    code[jle_strlen + 1] = do_strlen_off - (jle_strlen + 2)
    code[jg_strlen  + 1] = do_strlen_off - (jg_strlen  + 2)

    code += bytes([0x31, 0xC9])              # xor ecx, ecx

    sloop = len(code)                        # strlen_loop:
    code += bytes([0x80, 0x3C, 0x0E, 0x00]) # cmp byte [esi+ecx], 0

    je_done = len(code)
    code += bytes([0x74, 0x00])              # je  count_ready

    code += bytes([0x41])                    # inc ecx
    back = -(len(code) + 2 - sloop)
    code += bytes([0xEB, back & 0xFF])       # jmp strlen_loop

    # count_ready: (ecx = string length, from arg4 or strlen)
    count_ready_off = len(code)
    code[jmp_ready + 1] = count_ready_off - (jmp_ready + 2)
    code[je_done   + 1] = count_ready_off - (je_done   + 2)

    # --- Skip if empty ---
    code += bytes([0x85, 0xC9])              # test ecx, ecx

    je_skip = len(code)
    code += bytes([0x74, 0x00])              # je skip_reversal

    # --- Clamp to 127 (unsigned JBE so 0xFFFFFFFF is treated as > 127) ---
    code += bytes([0x83, 0xF9, 0x7F])        # cmp ecx, 127

    jbe_nc = len(code)
    code += bytes([0x76, 0x00])              # jbe no_clamp

    code += bytes([0xB9, 0x7F, 0x00, 0x00, 0x00])  # mov ecx, 127  (full 32-bit clear)

    # no_clamp:
    no_clamp_off = len(code)
    code[jbe_nc + 1] = no_clamp_off - (jbe_nc + 2)

    # Replace the string-ptr argument on the stack with the slot address
    code += bytes([0x89, 0x44, 0x24, 0x1C])  # mov [esp+0x1C], eax

    # Point ESI at the last byte of the source string (copy backwards)
    code += bytes([0x8D, 0x74, 0x0E, 0xFF])  # lea esi, [esi+ecx-1]

    # copy_loop:
    cloop = len(code)
    code += bytes([0x8A, 0x06])              # mov al, [esi]
    code += bytes([0x88, 0x07])              # mov [edi], al
    code += bytes([0x4E])                    # dec esi
    code += bytes([0x47])                    # inc edi
    code += bytes([0x49])                    # dec ecx
    back2 = -(len(code) + 2 - cloop)
    code += bytes([0x75, back2 & 0xFF])      # jnz copy_loop

    code += bytes([0xC6, 0x07, 0x00])        # mov byte [edi], 0  (null-terminate)

    # skip_reversal:
    skip_off = len(code)
    code[je_skip + 1] = skip_off - (je_skip + 2)

    # --- Epilogue ---
    code += bytes([0x5F, 0x5E, 0x59, 0x58])  # pop edi; pop esi; pop ecx; pop eax

    # Tail-call DrawString (JMP, not CALL, so it returns directly to the original caller)
    jmp_pos = len(code)
    ds_rel = struct.pack('<i', DRAWSTRING_VA - (WRAPPER_VA + jmp_pos + 5))
    code += bytes([0xE9]) + ds_rel

    return bytes(code)


def _wrapper_len() -> int:
    """Return the byte length of the compiled wrapper (cached after first call)."""
    global WRAPPER_LEN
    if WRAPPER_LEN is None:
        WRAPPER_LEN = len(_build_wrapper())
    return WRAPPER_LEN

# ---------------------------------------------------------------------------
# Verify patch state helpers
# ---------------------------------------------------------------------------
def _site_status(data: bytearray, site_va: int) -> str:
    """Return 'patched', 'original', or 'unknown' for a call-site VA."""
    off = _va_to_off(data, site_va)
    b   = bytes(data[off:off + 5])
    if b == _patched_call_bytes(site_va):
        return 'patched'
    if b == _orig_call_bytes(site_va):
        return 'original'
    return f'unknown({b.hex()})'


def _any_patched(data: bytearray, sites: list[int]) -> bool:
    return any(_site_status(data, s) == 'patched' for s in sites)


# ---------------------------------------------------------------------------
# Apply patch
# ---------------------------------------------------------------------------
def apply_patch():
    print('=== apply_reverse_patch  (string reversal for ALL DrawString calls) ===')
    data = _load_exe()

    sites = _find_call_sites(data)
    if not sites:
        print('[!] No CALL DrawString instructions found — wrong EXE?')
        return
    print(f'[*] Found {len(sites)} DrawString call-site(s):')
    for s in sites:
        print(f'      0x{s:08X}  ({_site_status(data, s)})')
    print()

    # Check whether all sites are already patched
    statuses = [_site_status(data, s) for s in sites]
    if all(st == 'patched' for st in statuses):
        print('[!] All call-sites already patched.')
        return

    # Warn about any site that is neither original nor already patched
    unexpected = [(s, st) for s, st in zip(sites, statuses)
                  if st not in ('original', 'patched')]
    if unexpected:
        for va, st in unexpected:
            print(f'[!] Unexpected bytes at 0x{va:08X}: {st}')
        if '--force' not in sys.argv:
            print('    (run with --force to patch anyway)')
            return

    # Verify cave area is clean NOPs (or already our wrapper)
    wlen     = _wrapper_len()
    cave_off = _va_to_off(data, WRAPPER_VA)
    cave_region = data[cave_off:cave_off + wlen]
    if any(b != 0x90 for b in cave_region) and cave_region != _build_wrapper():
        print(f'[!] Cave at 0x{WRAPPER_VA:08X} is not clean NOPs.')
        if '--force' not in sys.argv:
            print('    (run with --force to overwrite anyway)')
            return

    # Write wrapper
    wrapper = _build_wrapper()
    data[cave_off:cave_off + wlen] = wrapper
    print(f'[+] Wrapper written at 0x{WRAPPER_VA:08X} ({wlen} bytes)')
    print(f'    {wrapper.hex()}')

    # Zero ring buffer area in .data
    ring_off = _va_to_off(data, RING_IDX_VA)
    data[ring_off:ring_off + RING_DATA_LEN] = b'\x00' * RING_DATA_LEN
    print(f'[+] Ring buffer zeroed at 0x{RING_IDX_VA:08X} ({RING_DATA_LEN} bytes)')

    # Patch every call-site
    patched_count = 0
    for site_va in sites:
        if _site_status(data, site_va) == 'patched':
            print(f'    0x{site_va:08X}  already patched, skipping')
            continue
        off      = _va_to_off(data, site_va)
        new_bytes = _patched_call_bytes(site_va)
        data[off:off + 5] = new_bytes
        print(f'[+] Patched 0x{site_va:08X}  -> {new_bytes.hex()}')
        patched_count += 1

    _save_exe(data)
    print(f'[+] Monkey2.exe saved.  ({patched_count} site(s) patched)')
    print()
    print('EXPECTED BEHAVIOUR')
    print('  Every DrawString call goes through the reversal wrapper.')
    print('  Each call gets its own ring slot (4 slots × 128 bytes).')
    print('  Multi-line dialog: each line reversed independently, no overlap.')
    print('  Tooltip / UI text: also reversed, displayed correctly RTL.')


# ---------------------------------------------------------------------------
# Restore / revert
# ---------------------------------------------------------------------------
def restore_patch():
    print('=== restore_reverse_patch  (reverting to original) ===')
    data = _load_exe()

    sites = _find_call_sites(data)

    # Also scan for sites currently pointing at the wrapper (patched sites whose
    # CALL target is WRAPPER_VA — they won't appear in _find_call_sites which
    # looks for calls to DRAWSTRING_VA, so find them separately).
    pe_off = struct.unpack_from('<I', data, 0x3C)[0]
    nsec   = struct.unpack_from('<H', data, pe_off + 6)[0]
    opt_sz = struct.unpack_from('<H', data, pe_off + 0x14)[0]
    sec_tb = pe_off + 0x18 + opt_sz
    for i in range(nsec):
        s    = sec_tb + i * 40
        name = data[s:s + 8].rstrip(b'\x00')
        if name == b'.text':
            text_va      = struct.unpack_from('<I', data, s + 12)[0]
            text_raw_sz  = struct.unpack_from('<I', data, s + 16)[0]
            text_raw_off = struct.unpack_from('<I', data, s + 20)[0]
            break
    for i in range(text_raw_sz - 5):
        if data[text_raw_off + i] != 0xE8:
            continue
        rel    = struct.unpack_from('<i', data, text_raw_off + i + 1)[0]
        src_va = IMAGE_BASE + text_va + i
        if src_va + 5 + rel == WRAPPER_VA and src_va not in sites:
            sites.append(src_va)
    sites.sort()

    if not sites:
        print('[!] No relevant call-sites found.')
        return

    restored_count = 0
    for site_va in sites:
        st = _site_status(data, site_va)
        if st == 'patched':
            off       = _va_to_off(data, site_va)
            orig_bytes = _orig_call_bytes(site_va)
            data[off:off + 5] = orig_bytes
            print(f'[+] Restored 0x{site_va:08X}  -> {orig_bytes.hex()}')
            restored_count += 1
        elif st == 'original':
            print(f'    0x{site_va:08X}  already original, skipping')
        else:
            print(f'[!] 0x{site_va:08X}  unexpected state: {st} — skipping')

    wlen     = _wrapper_len()
    cave_off = _va_to_off(data, WRAPPER_VA)
    data[cave_off:cave_off + wlen] = b'\x90' * wlen
    print(f'[+] Cave NOP-ed at 0x{WRAPPER_VA:08X} ({wlen} bytes)')

    ring_off = _va_to_off(data, RING_IDX_VA)
    data[ring_off:ring_off + RING_DATA_LEN] = b'\x00' * RING_DATA_LEN
    print(f'[+] Ring buffer zeroed at 0x{RING_IDX_VA:08X}')

    _save_exe(data)
    print(f'[+] Monkey2.exe restored.  ({restored_count} site(s) reverted)')


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------
def diagnose():
    data = _load_exe()
    print('=== diagnose ===')
    print(f'EXE: {EXE_PATH}  ({len(data)} bytes)')
    print()

    def dump(label, va, n=16):
        off = _va_to_off(data, va)
        b   = data[off:off + n]
        print(f'  {label} (0x{va:08X}): {b.hex()}')

    print('DrawString prologue:')
    dump('0x4DBFA0', DRAWSTRING_VA, 24)
    print()

    sites = _find_call_sites(data)
    print(f'CALL DrawString sites found in .text: {len(sites)}')
    for s in sites:
        st = _site_status(data, s)
        dump(f'0x{s:08X}  [{st}]', s, 10)
    print()

    wlen = _wrapper_len()
    print(f'Cave area (first {wlen} bytes, should be 90 NOPs when clean):')
    dump(f'0x{WRAPPER_VA:X}', WRAPPER_VA, wlen)
    print()
    print('Ring index (4 bytes) + ring buffer start (16 bytes):')
    dump(f'0x{RING_IDX_VA:X}', RING_IDX_VA, 20)
    print()

    all_patched = _any_patched(data, sites)
    print(f'Patch status: {"APPLIED (at least one site)" if all_patched else "NOT applied"}')
    if all_patched:
        wrapper  = _build_wrapper()
        cave_off = _va_to_off(data, WRAPPER_VA)
        actual   = bytes(data[cave_off:cave_off + wlen])
        if actual == wrapper:
            print('Cave code matches expected wrapper exactly.')
        else:
            print('WARNING: cave code does NOT match expected wrapper!')
            print(f'  expected: {wrapper.hex()}')
            print(f'  actual:   {actual.hex()}')

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def _check_exe_not_running():
    import subprocess
    r = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq Monkey2.exe', '/NH'],
                       capture_output=True, text=True)
    if 'Monkey2.exe' in r.stdout:
        print('[!] Monkey2.exe is currently running.  Close the game first.')
        sys.exit(1)

if __name__ == '__main__':
    _check_exe_not_running()
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'apply'
    if cmd == 'apply':
        apply_patch()
    elif cmd in ('restore', 'revert'):
        restore_patch()
    elif cmd in ('diagnose', 'diag'):
        diagnose()
    else:
        print('Usage:')
        print('  python apply_reverse_patch.py apply      # apply the reversal patch')
        print('  python apply_reverse_patch.py restore    # revert to original')
        print('  python apply_reverse_patch.py diagnose   # show key bytes + patch status')
