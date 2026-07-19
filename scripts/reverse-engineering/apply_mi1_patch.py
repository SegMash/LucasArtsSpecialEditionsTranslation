import struct
import pefile

# ==================== הגדרות המשתמש היציבות והמדויקות שלך ====================
INPUT_EXE = "MISE.exe"   
OUTPUT_EXE = "C:\\GOG Games\\Monkey Island 1 SE\\MISE.exe"

# התיקון הגאוני שלך: חוטפים 5 בתים שלמים החל משורת ה-cmp!
HIJACK_VA = 0x0048590B      
# ============================================================================

def main():
    print("[*] Loading Game EXE file...")
    try:
        pe = pefile.PE(INPUT_EXE)
    except Exception as e:
        print(f"[-] Error loading file: {e}")
        return

    file_image_base = 0x00400000
    hijack_rva = HIJACK_VA - file_image_base
    hijack_offset = pe.get_offset_from_rva(hijack_rva)
    print(f"[+] Hijack File Offset: {hex(hijack_offset)}")

    # 1. מציאת מערת קוד אוטומטית בתוך סקשן הקוד (.text) עבור המשחק
    print("[*] Scanning for a code cave...")
    cave_offset = None
    cave_va = None
    for section in pe.sections:
        if b'.text' in section.Name:
            data = section.get_data()
            cave_index = data.find(b'\x00' * 128)
            if cave_index != -1:
                cave_offset = section.PointerToRawData + cave_index
                cave_va = file_image_base + (section.VirtualAddress + cave_index)
                break

    if cave_offset is None:
        print("[-] Could not find a suitable Code Cave in the game EXE.")
        return

    print(f"[+] Found Code Cave at File Offset: {hex(cave_offset)} (VA: {hex(cave_va)})")

    # חישוב מרחק הקפיצה מה-main אל המערה (הפעם דורסים 5 בתים מושלמים ללא שאריות)
    jmp_to_cave_distance = cave_va - (HIJACK_VA + 5)

    # 2. בניית קוד המכונה (Opcodes) של המערה הדינמית שלך
    cave_code = bytearray()

    # =========================================================================
    
    # --- שלב א': הפילטר החכם שלך על EAX ---
    cave_code += b'\x85\xC0'                      # test eax, eax
    early_exit_patch_1  = len(cave_code)
    cave_code += b'\x75\x00'                      # jnz <will_patch_this_later>

    # ======= שלב א': צילום מצב הרמטי ומוחלט של כל המעבד (הגנה היקפית!) =======
    cave_code += b'\x60'                          # pushad (שומר את כל הרג'יסטרים: EAX, ECX, EDX...)
    cave_code += b'\x9C'                          # pushfd (שומר את כל דגלי המעבד)

    # ======= פילטר סימון ה-RTL: רק מחרוזות עם בית 0xF0 בהתחלה =======
    cave_code += b'\x80\x3F\xF0'                  # cmp byte ptr ds:[edi], 0xF0
    early_exit_patch_2 = len(cave_code)
    cave_code += b'\x75\x00'                      # jne <will_patch_this_later>

    # --- שלב ג': סריקה דינמית של אורך המחרוזת (0xF0 .. עד ה-NULL) ---
    cave_code += b'\x89\xFB'                      # mov ebx, edi (שומר תחילת המחרוזת)
    cave_code += b'\x8B\xF7'                      # mov esi, edi
    cave_code += b'\x33\xC9'                      # xor ecx, ecx

    # לולאת חישוב אורך המשפט
    scan_loop_start = len(cave_code)

    cave_code += b'\x80\x3E\x00'                  # cmp byte ptr ds:[esi], 0
    cave_code += b'\x74\x04'                      # jz exit_scan_loop
    cave_code += b'\x46'                          # inc esi
    cave_code += b'\x41'                          # inc ecx
    cave_code += b'\xEB\xF7'                      # jmp scan_loop_start

    cave_code += b'\x4E'                          # dec esi (מצביע לבית האחרון לפני ה-NULL)
    cave_code += b'\x89\xCA'                      # mov edx, ecx (שומר אורך מלא כולל 0xF0)

    # חישוב מספר סיבובי ההחלפה (אורך / 2)
    cave_code += b'\xD1\xE9'                      # shr ecx, 1
    cave_code += b'\x85\xC9'                      # test ecx, ecx
    skip_swap_patch = len(cave_code)
    cave_code += b'\x74\x00'                      # jz skip_reverse_loop

    # --- שלב ד': לולאת ה-In-Place Swap (כולל 0xF0; בסוף 0xF0 עובר לסוף הבלוק) ---
    reverse_loop_start = len(cave_code)
    cave_code += b'\x8A\x07'                      # mov al, byte ptr ds:[edi]
    cave_code += b'\x8A\x26'                      # mov ah, byte ptr ds:[esi]
    cave_code += b'\x88\x27'                      # mov byte ptr ds:[edi], ah
    cave_code += b'\x88\x06'                      # mov byte ptr ds:[esi], al
    cave_code += b'\x47'                          # inc edi
    cave_code += b'\x4E'                          # dec esi
    cave_code += b'\x49'                          # dec ecx
    cave_code += b'\x75\xF3'                      # jnz reverse_loop_start

    # --- שלב ד': החלפת בית הסימון 0xF0 ב-Space (נמצא ב-[ebx + edx - 1] אחרי ההיפוך) ---
    replace_marker_index = len(cave_code)
    cave_code += b'\x89\xD8'                      # mov eax, ebx
    cave_code += b'\x01\xD0'                      # add eax, edx
    cave_code += b'\xFF\xC8'                      # dec eax
    cave_code += b'\xC6\x00\x20'                  # mov byte ptr ds:[eax], 20

    end_of_swap_logic = len(cave_code)
    cave_code[skip_swap_patch + 1] = replace_marker_index - (skip_swap_patch + 2)

    # ======= שלב ה': שער שחזור המצב ההרמטי לכולם (ההצלה של ה-Full Screen) =======
    exit_and_restore_index = len(cave_code)
    # --- שלב ה': שחזור הרג'יסטרים ---
    cave_code += b'\x9D'                          # popfd
    cave_code += b'\x61'                          # popad (משחזר את כל הרג'יסטרים! כולל החזרת EAX ל-0 מוחלט!)

    # --- עדכון דינמי של מרחקי הקפיצות המוקדמות (Early Exits) ---
    # נקודת היציאה המוקדמת נוחתת בדיוק כאן, בשלב שחזור הפקודות של ה-main
    exit_main_direct_index = len(cave_code)
    
    # עדכון ה-jnz הראשון (אם EAX אינו אפס)
    #jump_offset_1 = exit_label_index - (early_exit_patch_1 + 2)
    #cave_code[early_exit_patch_1 + 1] = jump_offset_1
    
    # עדכון ה-jnz השני (אם [edi] אינו 0xF0) -> נוחת בשער השחזור
    #jump_offset_2 = exit_label_index - (early_exit_patch_2 + 2)
    #cave_code[early_exit_patch_2 + 1] = jump_offset_2

    # --- שלב ו': שיחזור שתי הפקודות המקוריות שדרסנו ב-main (ה-cmp וה-push edi) ---
    # 1. פקודת ה-cmp המקורית מ-0x0048590B (בגלל שהמחסנית לא השתנתה, esp+44h נשאר קדוש!)
    cave_code += b'\x39\x6C\x24\x44'              # cmp dword ptr ss:[esp+44h], ebp
    # 2. פקודת ה-push edi המקורית מ-0x00485910
    cave_code += b'\x56'                          # push edi

    
    # קפיצה חזרה אל השורה הבאה במשחק (0x00485911 - השורה שבאה מיד אחרי ה-push edi שדרסנו!)
    return_va = HIJACK_VA + 5
    jmp_instruction_va = cave_va + len(cave_code)
    jmp_back_distance = return_va - (jmp_instruction_va + 5)
    cave_code += b'\xE9' + struct.pack('<i', jmp_back_distance)

    # 3. קריאת בתים של הקובץ וביצוע ה-Patch הפיזי על הדיסק
    with open(INPUT_EXE, 'rb') as f:
        file_data = bytearray(f.read()) 

    # א. שתילת החטיפה בתוך ה-main של המשחק (תופס בדיוק את ה-cmp וה-push edi)
    file_data[hijack_offset] = 0xE9
    file_data[hijack_offset+1:hijack_offset+5] = struct.pack('<i', jmp_to_cave_distance)
    # --- עדכון דינמי של מרחקי הקפיצות של ה-Early Exits אל שער השחזור הסופי ---
    # --- עדכון דינמי ומדויק של מרחקי הקפיצות לפי שערי החזרה הנכונים! ---
    # ה-jnz הראשון (אם EAX אינו 0) -> מדלג הרמטית על הכל וקופץ ישירות לשער ב' (בלי לגעת ב-pop-ים!)
    cave_code[early_exit_patch_1 + 1] = exit_main_direct_index - (early_exit_patch_1 + 2)
    
    # ה-jnz השני (אם [edi] != 0xF0) -> נוחת בשער השחזור (popfd/popad)
    cave_code[early_exit_patch_2 + 1] = exit_and_restore_index - (early_exit_patch_2 + 2)
    
    # ב. שתילת קוד המערה המלא באפסים של ה-.text
    file_data[cave_offset:cave_offset+len(cave_code)] = cave_code

    with open(OUTPUT_EXE, 'wb') as f:
        f.write(file_data)
        

    print(f"[+] Advanced direct patch applied successfully starting at: {hex(HIJACK_VA)}")

if __name__ == "__main__":
    main()
