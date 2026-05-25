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
    "א": 192,   # Alef #28 #C0
    #"ב": 195,   # Bet #28 #C3
    "ג": 196,   # Gimel #21 #C4
    "ד": 197,   # Dalet #18 #C5
    "ה": 198,   # He #18 #C6
    "ו": 199,   # Vav #18 #C7
    "ז": 200,   # Zayin  #18 #C8
    "ח": 201,   # Het #18 #C9
    #"ט": 203,   # Tet #26 #CB
    "י": 204,   # Yod #17 #CC
    "כ": 205,   # Kaf #17 #CD
    #"ל": 208,   # Lamed #17 #CE
    "מ": 209,   # Mem #17 #CF
    "נ": 210,   # Nun #17 #D0
    #"ס": 213,   # Samekh #11 #D3
    #"ע": 216,   # Ayin #11 #D6
    "פ": 217,   # Pe #12 #D9
    #"צ": 219,   # Tsadi #12 #DD
    #"ק": 222,   # Qof #22 #DF
    "ר": 223,   # Resh #19 #E3
    "ש": 224,   # Shin #19 #E4
    "ת": 225,   # Tav #19 #E5
    #"ך": 227,   # Final Kaf #19 
    "ם": 228,   # Final Mem #20 #E4
    "ן": 229,   # Final Nun #20 #E5
    "ף": 230,   # Final Pe #20  #E6
    "ץ": 231,   # Final Tsadi #20 #E7
    "ב": 232,   # Bet #28 #E8
    "ט": 233, #E9
    "ל": 234, #EA
    "ע": 235,   # Ayin #11 #EB
    "ך": 236, #EC
    "ס": 237,   # Samekh #11 #ED
    "צ": 238,   # Tsadi #12  #EE
    #"ק": 240,   # Qof #22
    "ק": 241,   # Qof #22 #F1
}

# ── Reverse mapping: char code → Hebrew letter ────────────────────────────────
CODE_TO_HEBREW: dict[int, str] = {v: k for k, v in HEBREW_TO_CODE.items()}

# ── Glyph index for each Hebrew letter ───────────────────────────────────────
# (Derived from glyph_manifest.csv — same for both MinisterT_24 and _96)
_CODE_TO_GLYPH: dict[int, int] = {
    192: 105,
    #195: 106,
    196: 107,
    197: 108,
    198: 109,
    199: 110,
    200: 111,
    201: 112,
    #203: 113,
    204: 114,
    205: 115, 
    #208: 116, 
    209: 117, 
    210: 118, 
    #213: 119,
    #216: 120,
    217: 121,
    #219: 122, 
    #222: 123, 
    223: 124,
    224: 125, 
    225: 126, 
    #227: 127, 
    228: 128, 
    229: 129,
    230: 130, 
    231: 131,
    232: 132,
    233: 133,
    234: 134,
    235: 135,
    236: 136,
    237: 137,
    238: 138,
    241: 140,
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
