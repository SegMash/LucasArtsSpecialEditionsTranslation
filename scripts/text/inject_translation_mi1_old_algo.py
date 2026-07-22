import argparse
import os
import sys
import re

import importlib.util as _il

REVERSE_FOR_LTR = False

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_mapping_path = os.path.join(BASE_DIR, "scripts", "fonts", "hebrew_mapping.py")
_spec = _il.spec_from_file_location("hebrew_mapping", _mapping_path)
_mod = _il.module_from_spec(_spec)
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
HEBREW_TO_CODE: dict[str, int] = _mod.HEBREW_TO_CODE
_TOKEN_RE = re.compile(r'(\{[^}]+\}|`[^`]*`)')
_REVERSE_PREFIX = "[REVERSE]"
_encode_warnings: list[str] = []


def auto_int(x):
    return int(x, 0)


def encode_he_text(text: str, reverse_for_ltr: bool = REVERSE_FOR_LTR) -> bytes:
    """Encode a Hebrew/mixed string to the game's custom single-byte encoding.

    A leading [REVERSE] marker forces reverse_for_ltr=True for that string
    (reverse at inject time, no 0xF0 prefix) and is stripped before encoding.
    """
    if text.startswith(_REVERSE_PREFIX):
        text = text[len(_REVERSE_PREFIX):]
        print(f"REVERSE: {text}")
        reverse_for_ltr = True

    #print(text)
    segments = _TOKEN_RE.split(text)

    if reverse_for_ltr:
        segments = segments[::-1]

    def _encode_chars(chars: list[str]) -> None:
        for ch in chars:
            if ch in HEBREW_TO_CODE:
                result.append(HEBREW_TO_CODE[ch])
            elif 0x20 <= ord(ch) <= 0x7E:
                result.append(ord(ch))
            elif ord(ch) == 0x0A:
                result.append(0x0A)
            elif ord(ch) != 0:
                _encode_warnings.append(ch)

    result = bytearray()
    for seg in segments:
        if _TOKEN_RE.fullmatch(seg):
            if seg.startswith('{'):
                # Game-engine code token — ASCII only, copy as-is
                for ch in seg:
                    code = ord(ch)
                    if 0x20 <= code <= 0x7E:
                        result.append(code)
            else:
                # Backtick-wrapped text — keep delimiters, encode inner content
                # normally so Hebrew characters are preserved
                result.append(ord('`'))
                inner = list(seg[1:-1])
                if reverse_for_ltr:
                    inner = inner[::-1]
                _encode_chars(inner)
                result.append(ord('`'))
        else:
            chars = list(seg)
            if reverse_for_ltr:
                chars = chars[::-1]
            _encode_chars(chars)

    #if not reverse_for_ltr:
    #    return bytes([240]) + bytes(result)
    return bytes(result)


def inject_strings(txt_path, bin_path, out_path, start_offset, step, reverse_for_ltr=REVERSE_FOR_LTR):
    if not os.path.exists(txt_path):
        print(f"Error: Text file '{txt_path}' does not exist.")
        sys.exit(1)
    if not os.path.exists(bin_path):
        print(f"Error: Binary file '{bin_path}' does not exist.")
        sys.exit(1)

    BUFFER_SIZE = 0x100  # 256 bytes

    try:
        # Read all translated strings from the text file
        with open(txt_path, 'r', encoding='utf-8') as txt_file:
            # strip('\r\n') keeps spaces inside the string but removes line endings
            strings = [line.rstrip('\r\n') for line in txt_file]

        # Read the original binary file into memory to modify it
        with open(bin_path, 'rb') as bin_file:
            binary_data = bytearray(bin_file.read())

        current_offset = start_offset
        file_size = len(binary_data)

        for i, text in enumerate(strings):
            if current_offset >= file_size:
                print(f"Warning: Reached end of file. Only {i} strings were injected.")
                break

            # Encode the string to bytes (using windows-1255 for Hebrew support)
            #encoded_heb = text.encode('windows-1255', errors='replace')
            encoded_str = encode_he_text(text, reverse_for_ltr=reverse_for_ltr)
            
            # Ensure the string plus the null terminator fits in the buffer
            if len(encoded_str) >= BUFFER_SIZE:
                print(f"Warning: String at line {i+1} is too long. Truncating to fit buffer.")
                encoded_str = encoded_str[:BUFFER_SIZE - 1]

            # Construct the 256-byte (0x100) buffer
            new_buffer = bytearray()
            new_buffer.extend(encoded_str)               # Add the text bytes
            new_buffer.append(0x00)                      # Add the Null terminator
            
            # Fill the remaining space with spaces (0x20)
            remaining_space = BUFFER_SIZE - len(new_buffer)
            if remaining_space > 0:
                new_buffer.extend(b'\x20' * remaining_space)

            # Inject the buffer into the binary data at the current offset
            end_offset = current_offset + BUFFER_SIZE
            if end_offset <= file_size:
                binary_data[current_offset:end_offset] = new_buffer
            else:
                # If we are near the very end of the file and the step overflows
                print(f"Warning: Step at offset {hex(current_offset)} exceeds file boundaries.")
                break

            # Jump to the next fixed offset
            current_offset += step

        # Write the modified binary data to the output file
        with open(out_path, 'wb') as out_file:
            out_file.write(binary_data)

        print(f"Injection completed successfully. (REVERSE_FOR_LTR={reverse_for_ltr})")

    except Exception as e:
        print(f"An error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inject strings back into a binary file at fixed hex steps.")
    parser.add_argument('--txt', required=True, help="Path to the input text file containing translated strings")
    parser.add_argument('--bin', required=True, help="Path to the original source binary file")
    parser.add_argument('--out', required=True, help="Path to the output modified binary file")
    parser.add_argument('--start', type=auto_int, required=True, help="Starting hex offset (e.g., 0x10 or 0x110)")
    parser.add_argument('--jump', type=auto_int, required=True, help="Jump step in hex (e.g., 0x530)")
    parser.add_argument(
        '--reverse-for-ltr',
        action='store_true',
        help="Reverse Hebrew at injection time; default omits reversal and adds 0xF0 marker prefix",
    )

    args = parser.parse_args()
    inject_strings(
        args.txt, args.bin, args.out, args.start, args.jump,
        reverse_for_ltr=args.reverse_for_ltr,
    )
