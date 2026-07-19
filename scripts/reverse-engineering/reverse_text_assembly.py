import struct
import pefile

INPUT_EXE = "ReverseEnineeringApp.exe"   
OUTPUT_EXE = "ReverseEnineeringApp_Patched.exe" 

# עדכן את הכתובת המדויקת של ה-call printLine החדש מתוך ה-main ב-x32dbg
HIJACK_VA = 0x00401125

def main():
    print("[*] Loading EXE file...")
    try:
        pe = pefile.PE(INPUT_EXE)
    except Exception as e:
        print(f"[-] Error loading file: {e}")
        return

    # ביטול ה-ASLR כדי שהכתובות יהיו קבועות ב-0x00400000
    pe.OPTIONAL_HEADER.DllCharacteristics &= ~0x0040
    print("[+] ASLR disabled successfully in PE Header flags!")

    file_image_base = 0x00400000
    hijack_rva = HIJACK_VA - file_image_base
    hijack_offset = pe.get_offset_from_rva(hijack_rva)

    with open(INPUT_EXE, 'rb') as f:
        f.seek(hijack_offset)
        call_opcode = f.read(1)
        if call_opcode != b'\xE8':
            print("[-] Error: The instruction to hijack is not a standard CALL.")
            return
        original_relative_offset, = struct.unpack('<i', f.read(4))
        
    actual_print_line_va = HIJACK_VA + 5 + original_relative_offset

    # מציאת מערת קוד אוטומטית ב-.text
    cave_offset = None
    cave_va = None
    for section in pe.sections:
        if b'.text' in section.Name:
            data = section.get_data()
            cave_index = data.find(b'\x00' * 64)
            if cave_index != -1:
                cave_offset = section.PointerToRawData + cave_index
                cave_va = file_image_base + (section.VirtualAddress + cave_index)
                break

    if cave_offset is None:
        print("[-] Could not find a suitable Code Cave.")
        return

    # חישוב מרחק הקפיצה מה-main למערה
    jmp_to_cave_distance = cave_va - (HIJACK_VA + 5)

    # 5. בניית קוד המכונה (Opcodes) שיישתל במערה - גרסת ה-C-String הטהורה!
    cave_code = bytearray()
    
    # --- שלב א': שמירת הרג'יסטרים של הלולאה ---
    cave_code += b'\x51'                          # push ecx
    cave_code += b'\x53'                          # push ebx
    cave_code += b'\x52'                          # push edx
    cave_code += b'\x56'                          # push esi
    cave_code += b'\x57'                          # push edi
    
    # --- שלב b: שליפת המצביע הישיר למחרוזת מראש המחסנית (+14h בגלל 5 הפושים) ---
    cave_code += b'\x8B\x74\x24\x14'              # mov esi, dword ptr ss:[esp+14h] (טוען את כתובת הטקסט ל-ESI)
    
    # הכנת המצביעים ללולאת ה-Swap
    cave_code += b'\x8B\xFC'                      # mov edi, esi (EDI מתחיל בתחילת הטקסט)
    cave_code += b'\x81\xC7\x8E\x00\x00\x00'      # add edi, 8Eh (מביאים את EDI לתו האחרון, בית 142)
    cave_code += b'\xB9\x24\x00\x00\x00'          # mov ecx, 24h (36 סיבובים בדיוק כדי להגיע לאמצע)
    
    # --- תחילת לולאת ההיפוך הישירה (In-Place Swap) על המחסנית הפתוחה לכתיבה ---
    reverse_loop = len(cave_code)
    cave_code += b'\x66\x8B\x1E'                  # mov bx, word ptr ds:[esi]
    cave_code += b'\x66\x8B\x17'                  # mov dx, word ptr ds:[edi]
    cave_code += b'\x66\x89\x1F'                  # mov word ptr ds:[edi], bx (ה-1F המתוקן שלך)
    cave_code += b'\x66\x89\x16'                  # mov word ptr ds:[esi], dx
    cave_code += b'\x83\xC6\x02'                  # add esi, 2
    cave_code += b'\x83\xEF\x02'                  # sub edi, 2
    cave_code += b'\x49'                          # dec ecx
    cave_code += b'\x75\xEB'                      # jnz reverse_loop (ה-EB המדויק שאיזנת!)
    
    # --- שלב ג': שחזור הרג'יסטרים ---
    cave_code += b'\x5F'                          # pop edi
    cave_code += b'\x5E'                          # pop esi
    cave_code += b'\x5A'                          # pop edx
    cave_code += b'\x5B'                          # pop ebx
    cave_code += b'\x59'                          # pop ecx
    
    # --- שלב ד': איזון המחסנית מול ה-main ---
    cave_code += b'\x83\xC4\x04'                  # add esp, 4 (מניעת כפל ניקוי ב-main)
    
    # ======= חישוב אוטומטי ומדויק של מרחק ה-call לפונקציית ההדפסה =======
    call_instruction_va = cave_va + len(cave_code)
    distance_traveled_forward = jmp_to_cave_distance + len(cave_code)
    call_print_line_distance = original_relative_offset - (distance_traveled_forward + 5)
    
    cave_code += b'\xE8' + struct.pack('<i', call_print_line_distance) 
    # =====================================================================

    # ======= חזרה הביתה ל-main לשורה שאחרי ה-call =======
    return_va = HIJACK_VA + 5
    jmp_instruction_va = cave_va + len(cave_code)
    jmp_back_distance = return_va - (jmp_instruction_va + 5)
    cave_code += b'\xE9' + struct.pack('<i', jmp_back_distance)

    # 6. יצירת ה-Bytes של הקובץ הסופי ושתילת הפאץ'
    file_data = bytearray(pe.write()) 
    file_data[hijack_offset] = 0xE9
    file_data[hijack_offset+1:hijack_offset+5] = struct.pack('<i', jmp_to_cave_distance)
    file_data[cave_offset:cave_offset+len(cave_code)] = cave_code

    with open(OUTPUT_EXE, 'wb') as f:
        f.write(file_data)

    print(f"[+] Done! Patched file saved successfully as: {OUTPUT_EXE}")

if __name__ == "__main__":
    main()
