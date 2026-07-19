import sys
import os

def convert_utf8_to_windows1255(input_path, output_path):
    # בדיקה שהקובץ המקורי באמת קיים
    if not os.path.exists(input_path):
        print(f"שגיאה: הקובץ '{input_path}' לא נמצא.")
        sys.exit(1)
        
    try:
        # קריאת קובץ ה-UTF-8
        with open(input_path, 'r', encoding='utf-8') as infile:
            content = infile.read()
        
        # כתיבת קובץ ה-Windows-1255
        with open(output_path, 'w', encoding='windows-1255', errors='replace') as outfile:
            outfile.write(content)
            
        print(f"File is saved: '{output_path}'")
        
    except Exception as e:
        print(f"There was a problem: {e}")

if __name__ == "__main__":
    # בדיקה שהמשתמש סיפק את שני הפרמטרים הנדרשים
    if len(sys.argv) < 3:
        print("שגיאה: חסרים פרמטרים.")
        print("שימוש נכון: python convert.py <קובץ_קלט> <קובץ_פלט>")
        sys.exit(1)
        
    # קבלת הפרמטרים משורת הפקודה
    input_file = sys.argv[1]
    output_file = sys.argv[2]
    
    convert_utf8_to_windows1255(input_file, output_file)