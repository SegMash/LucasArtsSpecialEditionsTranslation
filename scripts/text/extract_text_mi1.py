import argparse
import os
import sys

def auto_int(x):
    return int(x, 0)

def extract_strings(input_path, output_path, start_offset, step):
    if not os.path.exists(input_path):
        print(f"Error: Input file '{input_path}' does not exist.")
        sys.exit(1)

    try:
        with open(input_path, 'rb') as infile, open(output_path, 'w', encoding='utf-8') as outfile:
            file_size = os.path.getsize(input_path)
            current_offset = start_offset

            while current_offset < file_size:
                infile.seek(current_offset)
                
                # Read bytes until null terminator (0x00)
                string_bytes = bytearray()
                while True:
                    byte = infile.read(1)
                    if not byte or byte == b'\x00':
                        break
                    string_bytes.extend(byte)

                # Decode to string (ignoring non-ascii characters if any exist)
                extracted_str = string_bytes.decode('ascii', errors='ignore')
                
                # Write only the extracted string followed by a newline
                outfile.write(extracted_str + '\n')

                # Jump to the next fixed offset
                current_offset += step

        print("Extraction completed successfully.")

    except Exception as e:
        print(f"An error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract strings from binary file at customizable hex steps.")
    parser.add_argument('--in', dest='input_file', required=True, help="Path to the input binary file")
    parser.add_argument('--out', dest='output_file', required=True, help="Path to the output text file")
    parser.add_argument('--start', type=auto_int, required=True, help="Starting hex offset (e.g., 0x10 or 0x110)")
    parser.add_argument('--jump', type=auto_int, required=True, help="Jump step in hex (e.g., 0x530)")
    
    args = parser.parse_args()
    extract_strings(args.input_file, args.output_file, args.start, args.jump)
