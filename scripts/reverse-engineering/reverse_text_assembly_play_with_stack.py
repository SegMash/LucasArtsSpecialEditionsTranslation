import struct
import pefile

INPUT_EXE = "ReverseEnineeringApp.exe"
OUTPUT_EXE = "ReverseEnineeringApp_Patched.exe"
# The address of the jump from main
HIJACK_VA = 0x004013D8
# original address call printLine
# 009013D8 | E8 C3FFFFFF | call <reverseenineeringapp.printLine>
# C3FFFFFF -> FFFFFFC3 -> FFFFFFFF - FFFFFFC3 = 61
# 0x009013DD (0x009013D8 + 5) - 61 - 0x009013A0 = 0x009013A0
PRINT_LINE_VA = 0x004031A0


def find_code_cave(pe, required_size=64):
    """Scan the text sections and find free 'cave'"""
    print(f"[*] Scanning for a code cave at least {required_size}")
    # Loop all text sections
    for section in pe.sections:
        if b'.text' in section.Name:
            data = section.get_data()
            cave_pattern = b'\x00' * required_size
            cave_index = data.find(cave_pattern)
            if cave_index != -1:
                # Get phisical address in file
                cave_offset = section.PointerToRawData + cave_index
                # Calcualte virtual address for debuging and for jumping
                cave_rva = section.VirtualAddress + cave_index
                cave_va = pe.OPTIONAL_HEADER.ImageBase + cave_rva
                print(f"[+] Found an automatic Code Cave!")
                print(f"    -> File Offset: {hex(cave_offset)}")
                print(f"    -> Virtual Address (VA): {hex(cave_va)}")
                return cave_offset, cave_va
    raise RuntimeError("[-] Could not find a suitable Code Cave in the .text section.")

def main():
    print("[*] Loading EXE file...")
    try:
        pe = pefile.PE(INPUT_EXE)
    except Exception as e:
        print(f"[-] Error loading file: {e}")
        return
    
    pe.OPTIONAL_HEADER.DllCharacteristics &= ~0x0040
    print("[+] ASLR disabled successfully in PE Header flags!")

    file_image_base = 0x00400000

    hijack_rva = HIJACK_VA - file_image_base
    hijack_offset = pe.get_offset_from_rva(hijack_rva)
    print(f"[+] Hijack File Offset: {hex(hijack_offset)}")

    with open(INPUT_EXE, 'rb') as f:
        f.seek(hijack_offset)
        call_opcode = f.read(1)
        if call_opcode != b'\xE8':
            print("[-] Error: The instruction to hijack is not a standard CALL. Make sure HIJACK_VA is correct.")
            return
        # קריאת המרחק היחסי המקורי שהקומפיילר חישב ל-printLine בקובץ החדש
        original_relative_offset = struct.unpack('<i', f.read(4))[0]

    actual_print_line_va = HIJACK_VA + 5 + original_relative_offset
    print(f"original_relative_offset={original_relative_offset}")
    print(f"actual_print_line_va={actual_print_line_va}")
    print(f"[+] Automatically resolved printLine VA in the new file: {hex(actual_print_line_va)}")


    print(f"[+] File Embedded Image Base: {hex(file_image_base)}")

    # 1. Find automatically cave code address
    try:
        cave_offset, cave_va = find_code_cave(pe, required_size=64)
    except Exception as e:
        print(e)
        return

    
    # 2. Calcuate address to return
    return_va = HIJACK_VA + 5 

    # 3. Calcuate distance for jumps
    jmp_to_cave_distance = cave_va - (HIJACK_VA + 5)


    # 4. The cave code
    # 3. בניית קוד המכונה (Opcodes) - הגרסה המינימליסטית והמושלמת שלך!
    cave_code = bytearray()
    
    # --- שלב א': שליפת כתובת האובייקט המקורי ישירות מראש המחסנית ---
    # מכיוון שאין push-ים, ראש המחסנית [esp] מכיל בדיוק את מה שה-main השאיר!
    cave_code += b'\x8B\x34\x24'                  # mov esi, dword ptr ss:[esp]
    
    # חילוץ הכתובת האמיתית של ה-Heap מתוך האובייקט (ה-Deref שגילית!)
    cave_code += b'\x8B\x36'                      # mov esi, dword ptr ds:[esi] 
    
    # --- שלב ב': פינוי מקום למחרוזת ההפוכה על המחסנית ---
    cave_code += b'\x81\xEC\x90\x00\x00\x00'      # sub esp, 90h
    cave_code += b'\x8B\xFC'                      # mov edi, esp
    cave_code += b'\x81\xC7\x8E\x00\x00\x00'      # add edi, 8Eh
    cave_code += b'\xB9\x48\x00\x00\x00'          # mov ecx, 48h
    
    # לולאת ההיפוך (נשארת קבועה ויציבה)
    cave_code += b'\x66\x8B\x1E'                  # mov bx, word ptr ds:[esi] 
    cave_code += b'\x66\x89\x1F'                  # mov word ptr ds:[edi], bx 
    cave_code += b'\x83\xC6\x02'                  # add esi, 2
    cave_code += b'\x83\xEF\x02'                  # sub edi, 2
    cave_code += b'\x49'                          # dec ecx
    cave_code += b'\x75\xF1'                      # jnz reverse_loop (-15 bytes)
    
    # סיום המחרוזת עם תו NULL
    cave_code += b'\x66\xC7\x84\x24\x8E\x00\x00\x00\x00\x00' # mov word ptr ss:[esp+90h-2], 0
    
    # --- שלב ג': בניית רמות ההצבעה עם מרווח ביטחון של 4 בתים (הפיתרון שלך לבאג הדריסה) ---
    cave_code += b'\x8B\xDC'                      # mov ebx, esp (EBX מחזיק את הכתובת של האותיות ההפוכות Z)
    
    # 1. יצירת האובייקט המדומה בקומה מוגנת ב-esp+90h (מופרד ומרוחק מהטקסט ומאיזור ה-push-ים)
    cave_code += b'\x89\x9C\x24\x90\x00\x00\x00'  # mov dword ptr ss:[esp+90h], ebx
    
    # 2. עדכון משבצת ה-main המקורית (שנמצאת כעת ב-esp+C0h) בכתובת של האובייקט המדומה (esp+90h)
    cave_code += b'\x8D\x9C\x24\x90\x00\x00\x00'  # lea ebx, dword ptr ss:[esp+90h]
    cave_code += b'\x89\x9C\x24\xD0\x00\x00\x00'  # mov dword ptr ss:[esp+C0h], ebx
    
    # --- שלב ד': הגבהת ה-ESP למעלה אל משבצת ה-main המקורית (esp+C0h) ל-פ-נ-י ה-call ---
    cave_code += b'\x81\xC4\xd0\x00\x00\x00'      # add esp, C0h
    

    
    print(f"cave_va={hex(cave_va)}")
    print(f"len(cave_code)={hex(len(cave_code))}")
    call_instruction_va = cave_va + len(cave_code)
    print(f"call_instruction_va={hex(call_instruction_va)}")
    call_print_line_distance = actual_print_line_va - (call_instruction_va + 5)
    # Call print line call 
    distance_traveled_forward = jmp_to_cave_distance + len(cave_code)
    call_print_line_distance = original_relative_offset - (distance_traveled_forward + 5)
    print(f"call_print_line_distance={hex(call_print_line_distance)}")
    cave_code += b'\xE8' + struct.pack('<i', call_print_line_distance) 
    
    cave_code += b'\x81\xEC\x3c\x00\x00\x00'      # sub esp, 28h (הצעד שלך!)

    jmp_instruction_va = call_instruction_va + 5 + 6
    jmp_back_distance = return_va - (jmp_instruction_va + 5)
    # jmp RETURN_VA (קפיצה חזרה הביתה ל-main)
    jmp_back_distance = hijack_offset - (cave_offset + len(cave_code) - 3)

    cave_code += b'\xE9' + struct.pack('<i', jmp_back_distance)
    

    pe.OPTIONAL_HEADER.DllCharacteristics &= ~0x0040
    print("[+] ASLR disabled successfully in PE Header flags!")
    file_data = bytearray(pe.write()) 

    print(f"Inject jump in address:{hijack_offset}")
    # 0xE9 = call
    file_data[hijack_offset] = 0xE9
    file_data[hijack_offset+1:hijack_offset+5] = struct.pack('<i', jmp_to_cave_distance)
    # Inject cave code
    file_data[cave_offset:cave_offset+len(cave_code)] = cave_code
    # 5. Inject patch into exe

    # 6. Save exe
    with open(OUTPUT_EXE, 'wb') as f:
        f.write(file_data)

    print(f"[+] Done! Patched file saved successfully as: {OUTPUT_EXE}")

if __name__ == "__main__":
    main()