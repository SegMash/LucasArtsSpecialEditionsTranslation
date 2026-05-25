"""
hebrew_mapping.py — Custom Hebrew encoding for Monkey Island 2 SE

Strategy
--------
The font file already has glyph slots for European-language characters
(accented letters for French, German, Spanish, Italian, etc.) that are
not needed in a Hebrew translation.  We "reuse" those char codes: each
code is left intact in the .font char-table, but its glyph image is
replaced by the corresponding Hebrew letter.

The game engine looks up a char code → glyph index in the .font file,
then draws that glyph.  As long as we keep the char-code → glyph-index
mapping unchanged and only swap the *image* of the glyph, everything
works without touching any game binary.

Usage in other scripts
----------------------
    from hebrew_mapping import HEBREW_TO_CODE, CODE_TO_HEBREW

    code = HEBREW_TO_CODE['א']   # → 185
    letter = CODE_TO_HEBREW[219] # → 'ת'
"""

# ── Mapping: Hebrew letter  →  char code used in the font ────────────────────
#
# Char codes are taken from the extended glyph slots that exist in both
# MinisterT_24 and MinisterT_96 (codes 185-254, currently hold European
# accented characters).  All 27 slots below have confirmed glyph images
# in the original atlas.
#
# Letters are listed in standard Hebrew alphabet order,
# final forms (sofit) appear after the base alphabet.

HEBREW_TO_CODE: dict[str, int] = {
    # ── 22 standard letters ──────────────────────────────────────────────────
    "א": 109,   # Alef #28
    "ב": 111,   # Bet #28
    "ג": 223,   # Gimel #21
    "ד": 224,   # Dalet #18
    "ה": 225,   # He #18
    "ו": 110,   # Vav #18
    "ז": 228,   # Zayin  #18
    "ח": 229,   # Het #18
    "ט": 230,   # Tet #26
    "י": 231,   # Yod #17
    "כ": 232,   # Kaf #17
    "ל": 233,   # Lamed #17
    "מ": 234,   # Mem #17
    "נ": 235,   # Nun #17
    "ס": 236,   # Samekh #11
    "ע": 237,   # Ayin #11
    "פ": 238,   # Pe #12
    "צ": 112,   # Tsadi #12
    "ק": 241,   # Qof #22
    "ר": 242,   # Resh #19
    "ש": 243,   # Shin #19
    "ת": 113,   # Tav #19
    "ך": 114,   # Final Kaf #19
    "ם": 249,   # Final Mem #20
    "ן": 250,   # Final Nun #20
    "ף": 251,   # Final Pe #20
    "ץ": 115   # Final Tsadi #20
}

# ── Reverse mapping: char code → Hebrew letter ────────────────────────────────
CODE_TO_HEBREW: dict[int, str] = {v: k for k, v in HEBREW_TO_CODE.items()}

# ── Glyph index for each Hebrew letter ───────────────────────────────────────
# (Derived from glyph_manifest.csv — same for both MinisterT_24 and _96)
_CODE_TO_GLYPH: dict[int, int] = {
    109: 79, 111: 81, 223: 124, 224: 125, 225: 126,
    110: 80, 228: 128, 229: 129, 230: 130, 231: 131,
    232: 132, 233: 133, 234: 134, 235: 135, 236: 136,
    237: 137, 238: 138, 112: 82, 241: 140, 242: 141,
    243: 142, 113: 83, 114: 84, 249: 145, 250: 146,
    251: 147, 115: 85,
}

HEBREW_TO_GLYPH: dict[str, int] = {
    letter: _CODE_TO_GLYPH[code]
    for letter, code in HEBREW_TO_CODE.items()
}


def encode(text: str) -> list[int]:
    """
    Encode a Hebrew string as a list of char codes understood by the game.

    Example:
        encode("שלום")  →  [217, 201, 200, 203]   (right-to-left order preserved)
    """
    result = []
    for ch in text:
        if ch in HEBREW_TO_CODE:
            result.append(HEBREW_TO_CODE[ch])
        elif ch == " ":
            result.append(32)  # standard ASCII space
        else:
            raise ValueError(f"Character {ch!r} has no mapping in HEBREW_TO_CODE")
    return result


def decode(codes: list[int]) -> str:
    """Decode a list of char codes back to a Hebrew string."""
    return "".join(CODE_TO_HEBREW.get(c, chr(c)) for c in codes)


if __name__ == "__main__":
    print("Hebrew ↔ Char-code mapping")
    print(f"{'Letter':<8} {'Name':<16} {'Code':>5} {'Glyph':>6}")
    print("-" * 40)
    names = [
        "Alef","Bet","Gimel","Dalet","He","Vav","Zayin","Het","Tet","Yod",
        "Kaf","Lamed","Mem","Nun","Samekh","Ayin","Pe","Tsadi","Qof","Resh",
        "Shin","Tav","Final Kaf","Final Mem","Final Nun","Final Pe","Final Tsadi",
    ]
    for (letter, code), name in zip(HEBREW_TO_CODE.items(), names):
        glyph = HEBREW_TO_GLYPH[letter]
        print(f"  {letter}       {name:<16} {code:>5} {glyph:>6}")

    print()
    test = "שלום"
    encoded = encode(test)
    print(f'encode("{test}") = {encoded}')
    print(f'decode({encoded}) = "{decode(encoded)}"')
