import os
import sys
import struct
import argparse

# ---------------------------------------------------------------------------
# Configuration (Tuned precisely for Monkey Island 2 SE / MISE New Version)
# ---------------------------------------------------------------------------
IMAGE_BASE    = 0x00400000
DRAWSTRING_VA = 0x00485900        # הכתובת המקורית של הרינדור הנמוך והחלק
CALL_SITE_LEN = 5

WRAPPER_VA    = 0x004D9CF7   # מערת האפסים שמצאת בסוף ה-.text
WRAPPER_GAME_VA = WRAPPER_VA + 300  
WRAPPER_LEN   = None         

# Ring buffer in .data (4 Slots - Exactly like the working version)
RING_IDX_VA   = 0x00511090   # הכתובת החוקית שמצאת ב-.data
RING_BUF_VA   = RING_IDX_VA + 4  
RING_SLOTS    = 4
RING_SLOT_SZ  = 128
RING_DATA_LEN = 4 + RING_SLOTS * RING_SLOT_SZ  

FORMAT_SWAPS = [] # מנוטרל זמנית כדי להתמקד אך ורק בתפריט

def _find_call_sites(data: bytearray) -> list[int]:
    """Return both high-level call-sites for the Menu and the Game Scene."""
    precise_sites = [0x00485CCC, 0x00486722]
    print(f"[*] Targeting {len(precise_sites)} precise high-level sites.")
    return precise_sites

def _va_to_off(data: bytearray, va: int) -> int:
    """Convert Virtual Address to Raw File Offset."""
    pe_off = struct.unpack_from('<I', data, 0x3C)[0]
    nsec   = struct.unpack_from('<H', data, pe_off + 6)[0]
    opt_sz = struct.unpack_from('<H', data, pe_off + 0x14)[0]
    sec_tb = pe_off + 0x18 + opt_sz

    for i in range(nsec):
        s    = sec_tb + i * 40
        name = data[s:s + 8].rstrip(b'\x00')
        v_sz = struct.unpack_from('<I', data, s + 8)[0]
        v_va = struct.unpack_from('<I', data, s + 12)[0]
        r_sz = struct.unpack_from('<I', data, s + 16)[0]
        r_off = struct.unpack_from('<I', data, s + 20)[0]

        sz = max(v_sz, r_sz)
        if v_va <= (va - IMAGE_BASE) < (v_va + sz):
            return r_off + ((va - IMAGE_BASE) - v_va)

    raise ValueError(f'VA 0x{va:08X} not found in any section')


def _rel32(src_va: int, dst_va: int) -> bytes:
    """Calculate the 32-bit relative offset for an E8/E9 instruction."""
    rel = dst_va - (src_va + 5)
    return struct.pack('<i', rel)


def _load_exe(path: str) -> bytearray:
    with open(path, 'rb') as f:
        return bytearray(f.read())


def _save_exe(data: bytearray, path: str) -> None:
    with open(path, 'wb') as f:
        f.write(data)


def _find_call_sites(data: bytearray) -> list[int]:
    """Isolate only the 100% proven and safe Menu call-site."""
    menu_site = [0x00485CCC, 0x00486722]
    print(f"[*] Targeting only the safe Menu call-site: {menu_site}")
    return menu_site


def _build_wrapper() -> bytes:
    """Build the clean string-reversal wrapper for the safe Menu hook (4 slots)."""
    idx_le = struct.pack('<I', RING_IDX_VA)
    buf_le = struct.pack('<I', RING_BUF_VA)

    code = bytearray()
    code += bytes([0x50, 0x51, 0x56, 0x57])  # push eax; push ecx; push esi; push edi

    # Ring slot management (Modulo 4)
    code += bytes([0xA1]) + idx_le            # mov eax, [RING_IDX_VA]
    code += bytes([0x40])                     # inc eax
    code += bytes([0x83, 0xE0, 0x03])         # and eax, 3
    code += bytes([0xA3]) + idx_le            # mov [RING_IDX_VA], eax
    code += bytes([0xC1, 0xE0, 0x07])         # shl eax, 7
    code += bytes([0x05]) + buf_le            # add eax, RING_BUF_VA
    code += bytes([0x89, 0xC7])               # mov edi, eax

    # Load string pointer from offset 0x1C
    code += bytes([0x8B, 0x74, 0x24, 0x1C])  # mov esi, [esp+0x1C]

    # Protect against Null
    code += bytes([0x85, 0xF6])              # test esi, esi
    je_skip = len(code)
    code += bytes([0x74, 0x00])              # je skip_reversal

    # Determine length
    code += bytes([0x8B, 0x54, 0x24, 0x20])  # mov edx, [esp+0x20]
    code += bytes([0x85, 0xD2])              # test edx, edx

    jle_strlen = len(code)
    code += bytes([0x7E, 0x00])              # jle do_strlen
    code += bytes([0x83, 0xFA, 0x7F])        # cmp edx, 127
    jg_strlen = len(code)
    code += bytes([0x7F, 0x00])              # jg  do_strlen
    code += bytes([0x89, 0xD1])              # mov ecx, edx
    jmp_ready = len(code)
    code += bytes([0xEB, 0x00])              # jmp count_ready

    # do_strlen
    do_strlen_off = len(code)
    code[jle_strlen + 1] = do_strlen_off - (jle_strlen + 2)
    code[jg_strlen  + 1] = do_strlen_off - (jg_strlen  + 2)
    code += bytes([0x31, 0xC9])              # xor ecx, ecx
    sloop = len(code)
    code += bytes([0x80, 0x3C, 0x0E, 0x00]) # cmp byte [esi+ecx], 0
    je_done = len(code)
    code += bytes([0x74, 0x00])              # je  count_ready
    code += bytes([0x41])                    # inc ecx
    back = -(len(code) + 2 - sloop)
    code += bytes([0xEB, back & 0xFF])       # jmp strlen_loop

    # count_ready
    count_ready_off = len(code)
    code[jmp_ready + 1] = count_ready_off - (jmp_ready + 2)
    code[je_done   + 1] = count_ready_off - (je_done   + 2)

    # Skip if empty
    code += bytes([0x85, 0xC9])              # test ecx, ecx
    je_skip_empty = len(code)
    code += bytes([0x74, 0x00])              # je skip_reversal

    # Digit filter
    code += bytes([0x50, 0x51, 0x89, 0xF2])  # push eax; push ecx; mov edx, esi
    sloop2 = len(code)
    code += bytes([0x85, 0xC9])              # test ecx, ecx
    jz_do_rev = len(code)
    code += bytes([0x74, 0x00])              # jz do_reverse
    code += bytes([0x8A, 0x02, 0x3C, 0x30])  # mov al, [edx]; cmp al, '0'
    jb_next = len(code)
    code += bytes([0x72, 0x00])              # jb scan_next
    code += bytes([0x3C, 0x39])              # cmp al, '9'
    jbe_no_rev = len(code)
    code += bytes([0x76, 0x00])              # jbe no_reverse
    scan_next_off = len(code)
    code[jb_next + 1] = scan_next_off - (jb_next + 2)
    code += bytes([0x42, 0x49])              # inc edx; dec ecx
    back2 = -(len(code) + 2 - sloop2)
    code += bytes([0xEB, back2 & 0xFF])      # jmp scan_loop

    # no_reverse
    no_reverse_off = len(code)
    code[jbe_no_rev + 1] = no_reverse_off - (jbe_no_rev + 2)
    code += bytes([0x59, 0x58])              # pop ecx; pop eax
    jmp_skip = len(code)
    code += bytes([0xEB, 0x00])              # jmp skip_reversal

    # do_reverse
    do_reverse_off = len(code)
    code[jz_do_rev + 1] = do_reverse_off - (jz_do_rev + 2)
    code += bytes([0x59, 0x58])              # pop ecx; pop eax

    # Clamp to 127
    code += bytes([0x83, 0xF9, 0x7F])        # cmp ecx, 127
    jbe_nc = len(code)
    code += bytes([0x76, 0x00])              # jbe no_clamp
    code += bytes([0xB9, 0x7F, 0x00, 0x00, 0x00]) # mov ecx, 127
    no_clamp_off = len(code)
    code[jbe_nc + 1] = no_clamp_off - (jbe_nc + 2)

    # Reconstruct stack
    code += bytes([0x89, 0x44, 0x24, 0x1C])  # mov [esp+0x1C], eax
    code += bytes([0x8D, 0x74, 0x0E, 0xFF])  # lea esi, [esi+ecx-1]

    # copy_loop
    cloop = len(code)
    code += bytes([0x8A, 0x06, 0x88, 0x07, 0x4E, 0x47, 0x49]) 
    back3 = -(len(code) + 2 - cloop)
    code += bytes([0x75, back3 & 0xFF])      # jnz copy_loop
    code += bytes([0xC6, 0x07, 0x00])        # mov byte [edi], 0

    # skip_reversal
    skip_off = len(code)
    code[je_skip       + 1] = skip_off - (je_skip + 2)
    code[je_skip_empty + 1] = skip_off - (je_skip_empty + 2)
    code[jmp_skip      + 1] = skip_off - (jmp_skip + 2)

    code += bytes([0x5F, 0x5E, 0x59, 0x58])  # pop edi; pop esi; pop ecx; pop eax

    # Tail-call JMP to the original DrawString function
    jmp_pos = len(code)
    ds_rel = struct.pack('<i', DRAWSTRING_VA - (WRAPPER_VA + jmp_pos + 5))
    code += bytes([0xE9]) + ds_rel

    return bytes(code)

def _build_game_wrapper() -> bytes:
    """Build the clean string-reversal wrapper for the active Game Hook.
    Uses a direct byte-content content whitelist scan that is immune to UTF-16 layout half-nulls.
    Eliminates 'strlen' filters entirely to guarantee short and long text reversal with zero flickering.
    """
    idx_le = struct.pack('<I', RING_IDX_VA)
    buf_le = struct.pack('<I', RING_BUF_VA)

    code = bytearray()
    
    # --- Prologue: save registers (adds 16 bytes to ESP) ---
    code += bytes([0x50, 0x51, 0x56, 0x57])  # push eax; push ecx; push esi; push edi

    # --- Load string pointer DIRECTLY from the saved ECX register ---
    code += bytes([0x8B, 0x74, 0x24, 0x08])  # mov esi, [esp+0x08]

    # Protect against Null
    code += bytes([0x85, 0xF6])              # test esi, esi
    je_skip = len(code)
    code += bytes([0x74, 0x00])              # je skip_reversal

    # --- ADVANCED CONTENT WHITELIST SCAN (Immune to UTF-16 Nulls) ---
    code += bytes([0x89, 0xF7])              # mov edi, esi (use edi as scratch scanner)
    code += bytes([0xB9, 64, 0x00, 0x00, 0x00]) # mov ecx, 64 (scan maximum 64 bytes for safety)

    scan_loop_off = len(code)                # scan_loop:
    code += bytes([0x85, 0xC9])              # test ecx, ecx
    jz_no_text = len(code)
    code += bytes([0x74, 0x00])              # jz no_text_found
    
    code += bytes([0x8A, 0x07])              # mov al, [edi]
    
    # Check if byte is a valid character (32 <= AL <= 254)
    code += bytes([0x3C, 32])                # cmp al, 32
    jb_scan_next = len(code)
    code += bytes([0x72, 0x00])              # jb scan_next
    
    code += bytes([0x3C, 255])               # cmp al, 255
    je_scan_next = len(code)
    code += bytes([0x74, 0x00])              # je scan_next
    
    # Found a genuine text character! Break loop and trigger reversal
    jmp_do_rev = len(code)
    code += bytes([0xEB, 0x00])              # jmp do_reverse

    scan_next_off = len(code)                # scan_next:
    code[jb_scan_next + 1] = scan_next_off - (jb_scan_next + 2)
    code[je_scan_next + 1] = scan_next_off - (je_scan_next + 2)
    code += bytes([0x47, 0x49])              # inc edi; dec ecx
    back_scan = -(len(code) + 2 - scan_loop_off)
    code += bytes([0xEB, back_scan & 0xFF])  # jmp scan_loop

    # no_text_found: Strictly binary sprite data or pure layout padding -> SKIP REVERSAL
    no_text_off = len(code)
    code[jz_no_text + 1] = no_text_off - (jz_no_text + 2)
    jmp_skip = len(code)
    code += bytes([0xEB, 0x00])              # jmp skip_reversal

    # do_reverse: Text confirmed! Now compute true UTF-16 aware strlen
    do_reverse_off = len(code)
    code[jmp_do_rev + 1] = do_reverse_off - (jmp_do_rev + 2)
    
    # Calculate text string length cleanly into ECX
    code += bytes([0x31, 0xC9])              # xor ecx, ecx
    sloop_real = len(code)                   # strlen_loop:
    code += bytes([0x80, 0x3C, 0x0E, 0x00]) # cmp byte [esi+ecx], 0
    je_real_done = len(code)
    code += bytes([0x74, 0x00])              # je check_double_null (patched below)
    code += bytes([0x41])                    # inc ecx
    code += bytes([0x83, 0xF9, 127])         # cmp ecx, 127
    jl_back_real = len(code)
    code += bytes([0x7C, 0x00])              # jl strlen_loop
    
    # התיקון המתמטי: שימוש ב-& 0xFF עבור קפיצות שליליות לאחור
    back_real_offset = sloop_real - (jl_back_real + 2)
    code[jl_back_real + 1] = back_real_offset & 0xFF
    
    jmp_real_ready = len(code)
    code += bytes([0xEB, 0x00])              # jmp real_ready

    # check_double_null: If next byte is also 0, or we are at layout end, it's a true termination
    check_dnull_off = len(code)
    code[je_real_done + 1] = check_dnull_off - (je_real_done + 2)
    code += bytes([0x85, 0xC9])              # test ecx, ecx
    jz_real_ready = len(code)
    code += bytes([0x74, 0x00])              # jz real_ready
    code += bytes([0x80, 0x7C, 0x0E, 0x01, 0x00]) # cmp byte [esi+ecx+1], 0
    je_real_ready2 = len(code)
    code += bytes([0x74, 0x00])              # je real_ready
    code += bytes([0x41])                    # inc ecx (It was a UTF-16 inner zero, keep going!)
    back_continue = -(len(code) + 2 - sloop_real)
    code += bytes([0xEB, back_continue & 0xFF]) # jmp strlen_loop

    # real_ready: ECX now holds the complete calculated string length
    real_ready_off = len(code)
    code[jmp_real_ready + 1] = real_ready_off - (jmp_real_ready + 2)
    code[jz_real_ready  + 1] = real_ready_off - (jz_real_ready  + 2)
    code[je_real_ready2 + 1] = real_ready_off - (je_real_ready2 + 2)

    # --- Ring slot management (Modulo 4) ---
    code += bytes([0xA1]) + idx_le            # mov eax, [RING_IDX_VA]
    code += bytes([0x40])                     # inc eax
    code += bytes([0x83, 0xE0, 0x03])         # and eax, 3
    code += bytes([0xA3]) + idx_le            # mov [RING_IDX_VA], eax
    code += bytes([0xC1, 0xE0, 0x07])         # shl eax, 7
    code += bytes([0x05]) + buf_le            # add eax, RING_BUF_VA
    code += bytes([0x89, 0xC7])               # mov edi, eax

    # Clamp to 127
    code += bytes([0x83, 0xF9, 0x7F])        # cmp ecx, 127
    jbe_nc = len(code)
    code += bytes([0x76, 0x00])              # jbe no_clamp
    code += bytes([0xB9, 0x7F, 0x00, 0x00, 0x00]) # mov ecx, 127
    no_clamp_off = len(code)
    code[jbe_nc + 1] = no_clamp_off - (jbe_nc + 2)

    # Setup string pointer alignment
    code += bytes([0x8D, 0x74, 0x0E, 0xFF])  # lea esi, [esi+ecx-1]

    # copy_loop
    cloop = len(code)
    code += bytes([0x8A, 0x06, 0x88, 0x07, 0x4E, 0x47, 0x49]) 
    back3 = -(len(code) + 2 - cloop)
    code += bytes([0x75, back3 & 0xFF])      # jnz copy_loop
    code += bytes([0xC6, 0x07, 0x00])        # mov byte [edi], 0

    # Write reversed buffer address back into BOTH ECX and EDI stack slots for the engine
    code += bytes([0x89, 0x7C, 0x24, 0x08])  # mov [esp+0x08], edi (update ECX)
    code += bytes([0x89, 0x3C, 0x24])        # mov [esp], edi      (update EDI)

    # skip_reversal
    skip_off = len(code)
    code[je_skip       + 1] = skip_off - (je_skip + 2)
    code[jmp_skip      + 1] = skip_off - (jmp_skip + 2)

    # --- Epilogue: restore registers ---
    code += bytes([0x5F, 0x5E, 0x59, 0x58])  # pop edi; pop esi; pop ecx; pop eax

    # Tail-call JMP back to DRAWSTRING_VA (0x00485900)
    jmp_pos = len(code)
    ds_rel = struct.pack('<i', DRAWSTRING_VA - (WRAPPER_GAME_VA + jmp_pos + 5))
    code += bytes([0xE9]) + ds_rel

    return bytes(code)






def apply_patch(exe_path: str, force: bool = False) -> None:
    exe_name = os.path.basename(exe_path)
    print('=== apply_reverse_patch  (Dual Tail-Call High Level Patch) ===')
    print(f'    target: {exe_path}')
    data = _load_exe(exe_path)

    # אנחנו מוודאים שקריאת ה-Sites מחזירה את שתי הכתובות הראשיות שמצאת
    precise_sites = [0x00485CCC, 0x00486722]
    print()

    # כתיבת ה-Wrapper הראשון (לתפריט)
    wlen     = len(_build_wrapper())
    cave_off = _va_to_off(data, WRAPPER_VA)
    data[cave_off:cave_off + wlen] = _build_wrapper()
    print(f'[+] Menu Reversal Wrapper written at 0x{WRAPPER_VA:08X}')

    # כתיבת ה-Wrapper השני (למשחק)
    wlen_game     = len(_build_game_wrapper())
    cave_game_off = _va_to_off(data, WRAPPER_GAME_VA)
    data[cave_game_off:cave_game_off + wlen_game] = _build_game_wrapper()
    print(f'[+] Game Reversal Wrapper written at 0x{WRAPPER_GAME_VA:08X}')

    # איפוס ה-Ring Buffer ב-.data (מורחב ל-32 סלוטים)
    ring_off = _va_to_off(data, RING_IDX_VA)
    data[ring_off:ring_off + RING_DATA_LEN] = b'\x00' * RING_DATA_LEN
    print(f'[+] Ring buffer zeroed at 0x{RING_IDX_VA:08X}')

    # הזרקת ה-CALL-ים הראשיים
    patched_count = 0
    for site_va in precise_sites:
        off = _va_to_off(data, site_va)
        
        if site_va == 0x00485CCC:
            new_bytes = b'\xE8' + _rel32(site_va, WRAPPER_VA)
            data[off:off + 5] = new_bytes
            print(f'[+] Patched Menu Hook at 0x{site_va:08X} -> CALL 0x{WRAPPER_VA:08X}')
            patched_count += 1
            
        elif site_va == 0x00486722:
            new_bytes = b'\xE8' + _rel32(site_va, WRAPPER_GAME_VA)
            data[off:off + 5] = new_bytes
            print(f'[+] Patched Game Hook at 0x{site_va:08X} -> CALL 0x{WRAPPER_GAME_VA:08X}')
            patched_count += 1

    _save_exe(data, exe_path)
    print(f'[+] {exe_name} successfully saved with isolated high-level wrappers.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('exe_path')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()

    if args.apply:
        apply_patch(args.exe_path, force=args.force)
