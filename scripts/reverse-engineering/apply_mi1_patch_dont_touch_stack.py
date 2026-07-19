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
    
    # הכתובת הווירטואלית המדויקת שבה נשמור את הטקסט ההפוך (בסוף המערה, בהזחה של 0x90)
    # המרחב הסטטי הזה מבודד לחלוטין, לעולם לא זז, ולא נדרס על ידי המחסנית!
    static_buffer_va = cave_va + 0x90
    
    # --- שלב א': הפילטר המקורי של המשחק על EAX ---
    cave_code += b'\x85\xC0'                      # test eax, eax
    early_exit_patch_1 = len(cave_code)
    cave_code += b'\x75\x00'                      # jnz <will_patch_this_later> (קפיצה לסוף)

    # ======= מערכת הפילטרים הדו-שלבית הטהורה והמוגנת שלך! =======
    
    # 1. מציצים בבית שלפני EDI (תוכן בגודל בית אחד לתוך AL)
    cave_code += b'\x8A\x47\xFF'                  # mov al, byte ptr ds:[edi-1]
    cave_code += b'\x84\xC0'                      # test al, al (בודקים אם הבית הקודם הוא NULL)
    
    # אם [edi-1] == 0 (NULL), אנחנו מדלגים מעל התנאי השני וממשיכים ישר להיפוך!
    skip_second_cond_patch = len(cave_code)
    cave_code += b'\x74\x0A'                      # jz קפיצה קדימה של 10 בתים (מדלג בדיוק מעל התנאי השני)
    
    # 2. התנאי השני שלך: טוענים את 2 הבתים ב-[edi-2] לתוך AX ומקבלים פילטר חסין קריסות
    cave_code += b'\x66\x8B\x47\xFE'              # mov ax, word ptr ds:[edi-2]
    cave_code += b'\x66\x3D\xC8\x41'              # cmp ax, 0x41C8 (הקבוע המתוקן והמדויק שלך!)
    
    # אם התנאי השני לא מתקיים (לא שווה לקבוע), קופצים מיד לסוף הרוטינה!
    early_exit_patch_2 = len(cave_code)
    cave_code += b'\x75\x00'                      # jnz <will_patch_this_later> (קפיצה לסוף)
    
    # נקודת הציון שאליה מגיעים אם התנאי הראשון היה NULL (המשך לקוד ההיפוך)
    skip_cond_target_index = len(cave_code)
    jump_offset_skip = skip_cond_target_index - (skip_second_cond_patch + 2)
    cave_code[skip_second_cond_patch + 1] = jump_offset_skip
    # =========================================================================

    # --- שלב b': הגנה על משתני העזר (ECX, ESI, EDI) של הלולאה בלבד ---
    cave_code += b'\x51'                          # push ecx
    cave_code += b'\x56'                          # push esi
    cave_code += b'\x57'                          # push edi (שומר את תחילת המחרוזת המקורית)

    # מכינים את המצביעים למלאכת ההעתקה הסטטית:
    cave_code += b'\x8B\xF7'                      # mov esi, edi (ESI יסרוק את הטקסט המקורי מהמשחק)
    
    # EDI יצביע על הכתובת הקבועה והמאובטחת בסוף המערה
    cave_code += b'\xBF' + struct.pack('<I', static_buffer_va) # mov edi, static_buffer_va
    
    # --- שלב ג': סריקה דינמית של אורך המחרוזת (עד ה-NULL) ---
    cave_code += b'\x33\xC9'                      # xor ecx, ecx (מאפסים את מונה האורך)
    
    scan_loop_start = len(cave_code)
    cave_code += b'\x80\x3E\x00'                  # cmp byte ptr ds:[esi], 0
    cave_code += b'\x74\x04'                      # jz exit_scan_loop (ה-04 המדויק שאיזנת!)
    cave_code += b'\x46'                          # inc esi
    cave_code += b'\x41'                          # inc ecx
    cave_code += b'\xEB\xF7'                      # jmp scan_loop_start (קפיצה מדויקת לאחור של 9 בתים / 0xF7)

    # שורת היציאה מהסריקה: ESI נעמד על ה-NULL, נביא את EDI (הבאפר הסטטי) לסוף מרחב המשפט
    cave_code += b'\x03\xF9'                      # add edi, ecx
    cave_code += b'\xC6\x07\x00'                  # mov byte ptr ds:[edi], 0 (שותלים NULL מגן בסוף הבאפר הקבוע שלנו)
    cave_code += b'\x4F'                          # dec edi (נסוגים בית אחד מה-NULL אל התו האחרון)
    
    # נחזיר את ESI (הטקסט המקורי) לנקודת ההתחלה הטהורה שלו (שמורה בראש המחסנית ב-[esp])
    cave_code += b'\x8B\x34\x24'                  # mov esi, dword ptr ss:[esp]

    # --- שלב ד': לולאת העתקה הפוכה (קריאה בטוחה מהמשחק, כתיבה לבאפר הגלובלי הקבוע) ---
    copy_reverse_loop_start = len(cave_code)
    cave_code += b'\x8A\x06'                      # mov al, byte ptr ds:[esi] (קריאה בלבד)
    cave_code += b'\x88\x07'                      # mov byte ptr ds:[edi], al (כתיבה לבאפר המוגן)
    cave_code += b'\x46'                          # inc esi (מתקדמים ימינה בטקסט המקורי)
    cave_code += b'\x4F'                          # dec edi (נסוגים שמאלה בבאפר המהופך)
    cave_code += b'\x49'                          # dec ecx
    cave_code += b'\x75\xF4'                      # jnz copy_reverse_loop_start (קפיצה מדויקת לאחור של 12 בתים / 0xF4)

    # ======= הקסם הגדול: עדכון ה-EDI המקורי שבמחסנית לכתובת הגלובלית החדשה! =======
    # אנחנו דורסים את ה-EDI ששמרנו ב-push (שנמצא ב-esp) בכתובת הקבועה של הבאפר המאובטח!
    cave_code += b'\xC7\x04\x24' + struct.pack('<I', static_buffer_va) # mov dword ptr ss:[esp], static_buffer_va

    # --- שלב ה': שחזור הרג'יסטרים של הלולאה ---
    cave_code += b'\x5F'                          # pop edi (כאן נשלף ה-EDI החדש שמוביל לבאפר הסטטי החסין!)
    cave_code += b'\x5E'                          # pop esi
    cave_code += b'\x59'                          # pop ecx

    # --- עדכון דינמי של מרחקי הקפיצות המוקדמות (Early Exits) אל נקודת היציאה הסופית ---
    exit_label_index = len(cave_code)
    
    # עדכון ה-jnz הראשון (אם EAX אינו אפס)
    jump_offset_1 = exit_label_index - (early_exit_patch_1 + 2)
    cave_code[early_exit_patch_1 + 1] = jump_offset_1
    
    # עדכון ה-jnz השני (אם 2 הבתים ב-[edi-2] אינם שווים ל-0x41C8)
    jump_offset_2 = exit_label_index - (early_exit_patch_2 + 2)
    cave_code[early_exit_patch_2 + 1] = jump_offset_2
    # ======= התיקון הגאוני שלך: ניקוי ה-EAX לפני החזרה למסך המלא! =======
    # מאפסים את EAX ב-100% כדי להשמיד את הזבל של ה-AL/AX שלכלכנו,
    # ולהחזיר אותו למצב ה-0 המקורי שה-DirectX הבלעדי של ה-Full Screen צריך!
    cave_code += b'\x33\xC0'

    # --- שלב ו': שיחזור שתי הפקודות המקוריות שנדרסו וחזרה ל-main במשחק ---
    # 1. פקודת ה-cmp המקורית מ-0x0048590B (המחסנית נקייה לחלוטין, esp+44h מדויק פיקס!)
    cave_code += b'\x39\x6C\x24\x44'              # cmp dword ptr ss:[esp+44h], ebp
    # 2. התיקון הגיאומטרי המדויק שלך: משחזרים את ה-push esi המקורי שנמחק מ-0x0048590F!
    cave_code += b'\x56'                          # push esi
    
    
    # קפיצה חזרה אל שורת ה-push edi המקורית ב-main (0x00485910) שלא נדרסה!
    return_va = 0x00485910
    jmp_instruction_va = cave_va + len(cave_code)
    jmp_back_distance = return_va - (jmp_instruction_va + 5)
    cave_code += b'\xE9' + struct.pack('<i', jmp_back_distance)


    # 3. קריאת בתים של הקובץ וביצוע ה-Patch הפיזי על הדיסק
    with open(INPUT_EXE, 'rb') as f:
        file_data = bytearray(f.read()) 

    # א. שתילת החטיפה בתוך ה-main של המשחק (תופס בדיוק את ה-cmp וה-push edi)
    file_data[hijack_offset] = 0xE9
    file_data[hijack_offset+1:hijack_offset+5] = struct.pack('<i', jmp_to_cave_distance)
    
    # ב. שתילת קוד המערה המלא באפסים של ה-.text
    file_data[cave_offset:cave_offset+len(cave_code)] = cave_code
        # ======= הפיכת סקשן הקוד (.text) לניתן לכתיבה (Writable) ב-PE Header =======
    # אנחנו רצים על הסקשנים ומחפשים את .text שבו המערה נמצאת
    for section in pe.sections:
        if b'.text' in section.Name:
            # הדגל 0x80000000 מייצג את IMAGE_SCN_MEM_WRITE
            section.Characteristics |= 0x80000000
            print(f"[+] Made section {section.Name.decode().strip()} WRITABLE in PE Header!")


    with open(OUTPUT_EXE, 'wb') as f:
        f.write(file_data)
        

    print(f"[+] Advanced direct patch applied successfully starting at: {hex(HIJACK_VA)}")

if __name__ == "__main__":
    main()
