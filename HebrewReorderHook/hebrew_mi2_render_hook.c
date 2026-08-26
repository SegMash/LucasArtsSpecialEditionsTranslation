/*
 * hebrew_mi2_render_hook.c
 * -----------------------------------------------------------------
 * DLL to be injected into Monkey Island 2: SE's process (Monkey2.exe).
 *
 * PURPOSE
 * -------
 * This is a C / in-memory reimplementation of the patch that
 * scripts/reverse-engineering/apply_reverse_patch.py used to apply to the
 * EXE (it redirected every `CALL DrawString` to a wrapper cave).  Instead of
 * editing the EXE on disk, this DLL does the same at runtime:
 *
 *   1. It scans .text for every `CALL DrawString` (E8 rel32) and redirects
 *      each one to our ReversalWrapper (exactly like python's per-site CALL
 *      redirect).  DrawString's own prologue is left untouched, so each
 *      caller keeps its own register conventions intact (the Python script
 *      does this too - it never patched DrawString's body).
 *   2. The wrapper reads arg3 (string ptr) and arg4 (char count) from the
 *      caller's stack, calls OnDrawString(), replaces arg3 with the reversed
 *      slot pointer, then tail-JMPs into the real DrawString.
 *   3. Default: the whole string is copied in reverse into the current ring
 *      slot (RTL display).  Unlike the original apply_reverse_patch.py there
 *      is NO digit content filter - every string is reversed.
 *   4. PATTERN REVERSAL: after reversing, the display-order buffer is checked
 *      against the byte-encoded patterns in g_patterns[].  When one matches,
 *      each embedded number is reversed a SECOND time so it displays in its
 *      correct digit order.  e.g. "תן 614 מטבעות כסף למוכר" -> whole-reverse
 *      flips "614" to "416", and the second pass restores it to "614" while
 *      the surrounding Hebrew text stays reversed for RTL.  Patterns match on
 *      the GAME FONT CODE PAGE bytes, not UTF-8 (see g_patterns[]).
 *   5. It also applies the runtime version of python's FORMAT_SWAPS on the
 *      save/load screen (length-preserving in-place rewrites).
 *   6. Hebrew grammar: the standalone preposition "ל" is re-attached to the
 *      following word (" ל " -> "ל ", removing one space), so "give X to
 *      someone" reads with the ל joined to the recipient.  This shortens the
 *      string, so it is compacted in place and null-terminated at the new end.
 *
 * Because this is a live code hook (not a static EXE patch) nothing on disk
 * is modified, and all redirections are reverted on DLL detach.
 *
 * DRAWSTRING SIGNATURE (only used for the tail-jump target)
 *   DrawString(SpriteFont* font, void* parent, const char* text, int charCount)
 *   VA: 0x004DBFA0
 *
 * !!! VALUE YOU MUST VERIFY YOURSELF BEFORE TRUSTING THIS CODE !!!
 *   - DRAWSTRING_VA (0x004DBFA0) must be confirmed in YOUR Monkey2.exe build.
 *     Re-extract it from your disassembler if it differs.
 *
 * Build: 32-bit DLL (matches the 32-bit game process).
 *   MSVC:   cl /LD hebrew_mi2_render_hook.c
 * -----------------------------------------------------------------
 */

/* Disable MSVC's "unsafe function" errors for strcpy/strlen etc.
   Must come before any #include that pulls in string.h. */
#define _CRT_SECURE_NO_WARNINGS

#include <windows.h>
#include <stdio.h>
#include <string.h>

/* ------------------------------------------------------------------ */
/* Config: addresses you must confirm in your debugger                */
/* ================================================================== */
#define DRAWSTRING_VA  0x004DBFA0   /* DrawString function entry point */

/* ------------------------------------------------------------------ */
/* Ring buffer for string slots (function-local statics in the DLL).  */
/* The game uses batched (deferred) rendering, so up to 4 consecutive  */
/* DrawString calls can coexist.  128 bytes per slot is plenty for a   */
/* single rendered line.                                              */
#define RING_SLOTS    4
#define RING_SLOT_SZ  128
static char  g_ringBuffer[RING_SLOTS][RING_SLOT_SZ];
static int   g_ringIndex = 0;   /* ring slot counter (rotates 0..3)    */

/* Every CALL DrawString call-site we redirect, with its original 5
   bytes saved so RemoveHook() can restore them (Python call-sites).   */
#define MAX_CALLSITES  256
static DWORD g_callSites[MAX_CALLSITES];
static BYTE g_callSitesOrig[MAX_CALLSITES][5];
static int  g_callSiteCount = 0;

static int g_hookInstalled = 0;

/* ------------------------------------------------------------------ */
/* Numeric patterns (regex-like)                                       */
/* ------------------------------------------------------------------ */
/* A list of patterns that select which embedded numbers must SURVIVE the
   whole-string reversal intact.  After the string is reversed into the ring
   slot (display order), each pattern is matched against the slot content.
   When a pattern matches, every digit-run it captured via a `%d` placeholder
   is reversed a SECOND time, restoring those digits to their correct order.
   Any number NOT captured by the pattern stays reversed.
 *
 * PATTERN SYNTAX:
 *   %d   matches 1+ contiguous ASCII digits and CAPTURES that run (to be
 *        reversed).  e.g. `%d` on "81-80-6202" captures "81".
 *   .*   matches any number of bytes (lazy -- matches as few as possible,
 *        so each %d grabs the first digit-run after it).
 *   any other byte / byte-sequence must match literally (bytes are in the
 *        GAME FONT CODE PAGE for Hebrew, NOT UTF-8).
 *
 * EXAMPLES
 *   "%d-%d-%d %d:%d"  matches "81-80-6202 32:42" and reverses all 5 runs:
 *       81->18, 80->08, 6202->2026, 32->23, 42->24.
 *   ".*<מטבעות כסף> %d.*" matches the money counter and reverses only its
 *       single number.
 *   "%d%.*%d:%d:%d.*"  reverses the 4 numbers in a percent/time block.
 *
 * Patterns are specified as string literals in game-code-page bytes and are
 * tokenised once at startup by InitPatterns().  Add more by appending to the
 * `sources[]` table there.
 */
typedef enum { PAT_LITERAL, PAT_ANY, PAT_DIGITS } PatTokType;

typedef struct {
    PatTokType  type;
    const BYTE* lit;     /* PAT_LITERAL: pointer to the literal bytes  */
    int         litLen;  /* PAT_LITERAL: number of literal bytes       */
} PatToken;

typedef struct {
    const PatToken* tokens;
    int             tokenCount;
} PatternDef;

#define MAX_PATTERNS        8
#define MAX_TOKENS_PER_PAT  32
#define MAX_CAPTURES        16

static PatToken    g_tokenPool[MAX_PATTERNS][MAX_TOKENS_PER_PAT];
static PatternDef  g_patterns[MAX_PATTERNS];
static int         g_patternCount = 0;

/* A captured digit-run within the slot buffer (absolute byte indices). */
typedef struct { int start; int end; } DigitRun;

/*
 * Parse a pattern string into a token array.  Returns the number of tokens
 * (0 on failure).  `%d` -> PAT_DIGITS, `.*` -> PAT_ANY, everything else is
 * accumulated into literal PAT_LITERAL segments.
 */
static int ParsePattern(const char* pat, int patLen, PatToken* out, int maxOut) {
    int i = 0, n = 0;
    while (i < patLen && n < maxOut) {
        if (pat[i] == '%' && i + 1 < patLen && pat[i + 1] == 'd') {
            out[n].type = PAT_DIGITS; out[n].lit = NULL; out[n].litLen = 0;
            n++; i += 2;
        } else if (pat[i] == '.' && i + 1 < patLen && pat[i + 1] == '*') {
            out[n].type = PAT_ANY; out[n].lit = NULL; out[n].litLen = 0;
            n++; i += 2;
        } else {
            int start = i;
            while (i < patLen &&
                   !(pat[i] == '%' && i + 1 < patLen && pat[i + 1] == 'd') &&
                   !(pat[i] == '.' && i + 1 < patLen && pat[i + 1] == '*')) {
                i++;
            }
            out[n].type = PAT_LITERAL;
            out[n].lit = (const BYTE*)(pat + start);
            out[n].litLen = i - start;
            n++;
        }
    }
    return n;
}

/*
 * Recursive backtracking matcher.  Attempts to match tokens[tIdx..] starting
 * at byte `pos` of `buf`.  `.*` is LAZY (tries the shortest length first), so
 * each %d captures the first viable digit-run after it.  Digit-runs captured
 * by %d are appended to `caps`, but a capture is only KEPT if the whole rest
 * of the pattern also matches: on any backtracking failure the captures made
 * down that dead-end branch are rolled back (saved at each token, restored on
 * failure).  Without this, a failed attempt that already recorded %d runs
 * would leave stale/duplicate captures and corrupt the final result (e.g.
 * only the first %d of "%d%.*%d:%d:%d.*" would be reported).
 *
 * Returns 1 if the whole token list matched; on 1, `caps[0..*capCount)` holds
 * exactly the %d runs captured along the successful path.
 */
static int MatchTokens(const PatToken* tok, int tokCount, int tIdx,
                       const char* buf, int len, int pos,
                       DigitRun* caps, int* capCount) {
    if (tIdx >= tokCount) return 1;
    const PatToken* t = &tok[tIdx];

    switch (t->type) {
    case PAT_LITERAL:
        if (pos + t->litLen > len) return 0;
        if (memcmp(buf + pos, t->lit, t->litLen) != 0) return 0;
        return MatchTokens(tok, tokCount, tIdx + 1, buf, len,
                           pos + t->litLen, caps, capCount);
    case PAT_ANY: {
        /* Lazy '.*': try the shortest gap first so the following %d grabs the
           first digit-run.  Roll back captures on each failed attempt. */
        for (int k = 0; pos + k <= len; k++) {
            int saved = *capCount;
            if (MatchTokens(tok, tokCount, tIdx + 1, buf, len,
                            pos + k, caps, capCount)) {
                return 1;
            }
            *capCount = saved;   /* undo captures from this dead-end branch */
        }
        return 0;
    }
    case PAT_DIGITS: {
        if (pos >= len) return 0;
        if (!(buf[pos] >= '0' && buf[pos] <= '9')) return 0;
        int end = pos;
        while (end < len && buf[end] >= '0' && buf[end] <= '9') end++;
        if (*capCount >= MAX_CAPTURES) return 0;
        caps[(*capCount)++] = (DigitRun){ pos, end };
        if (MatchTokens(tok, tokCount, tIdx + 1, buf, len, end, caps, capCount)) {
            return 1;
        }
        (*capCount)--;          /* roll back this capture on failure */
        return 0;
    }
    }
    return 0;
}

/*
 * Match `p` anywhere in `buf` (tries every start position).  On success fills
 * `caps` with the captured %d runs and sets *capCount.  Returns 1 if matched.
 */
static int MatchPattern(const PatternDef* p, const char* buf, int len,
                        DigitRun* caps, int* capCount) {
    DigitRun tmp[MAX_CAPTURES];
    for (int s = 0; s <= len; s++) {
        int cc = 0;
        if (MatchTokens(p->tokens, p->tokenCount, 0, buf, len, s, tmp, &cc)) {
            for (int i = 0; i < cc; i++) caps[i] = tmp[i];
            *capCount = cc;
            return 1;
        }
    }
    *capCount = 0;
    return 0;
}

/*
 * Build g_patterns[] from string-literal pattern sources.  Called once at
 * DLL attach.  Each entry below is a pattern whose %d digit-runs should be
 * restored after the whole-string reversal.
 */
static void InitPatterns(void) {
    const BYTE* sources[MAX_PATTERNS] = {
        /* 1) money counter: "N מטבעות כסף" -> reverse just the N.
              (bytes are the game font code page sequence for "מטבעות כסף "
              followed by %d -- Hebrew is matched as bytes, not UTF-8.)     */
        (const BYTE*)"\x2E\x2A\xE6\xED\xCD\x20\xE1\xC7\xEB\xE8\xE9\xD1\x20\x25\x64\x2E\x2A",
        /* 2) save date/time: "81-80-6202 32:42" -> reverse all 5 numbers. */
        (const BYTE*)"\x25\x64\x2D\x25\x64\x2D\x25\x64\x20\x25\x64\x3A\x25\x64",
        /* 3) save percent+time block -> reverse percentage.           */
        (const BYTE*)"\x25\x64\x25\x2E\x2A",
        /* 4) plain time "74:51:22" -> reverse all 3 numbers.             */
        (const BYTE*)"\x2E\x2A\x25\x64\x3A\x25\x64\x3A\x25\x64\x2E\x2A",
        /* 5) map coordinates "מ%d צ%d"*/
        (const BYTE*)"\xD1\x25\x64\x20\xEE\x25\x64",
        /* 6) !508 !42 ..."*/
        (const BYTE*)"\x21\x25\x64"
    };
    /* Auto-count the entries actually listed above.  Each pattern MUST be
       added here, and the count is derived automatically so a newly added
       entry is not silently skipped (the old hard-coded `int n = 3` dropped
       any pattern added past the third). */
    int n = (int)(sizeof(sources) / sizeof(sources[0]));
    for (int i = 0; i < n && i < MAX_PATTERNS; i++) {
        if (sources[i] == NULL) break;   /* stop at any unused trailing slot */
        int tok = ParsePattern((const char*)sources[i], (int)strlen((const char*)sources[i]),
                               g_tokenPool[i], MAX_TOKENS_PER_PAT);
        if (tok > 0) {
            g_patterns[g_patternCount].tokens = g_tokenPool[i];
            g_patterns[g_patternCount].tokenCount = tok;
            g_patternCount++;
        }
    }
}

/* ------------------------------------------------------------------ */
/* Save/load format-string swaps (RTL ordering)                        */
/* ------------------------------------------------------------------ */
/* These mirror the hard-coded FORMAT_SWAPS table in
   scripts/reverse-engineering/apply_reverse_patch.py (lines 607-625 of
   apply_patch()).  That script rewrites the .NET-style format strings stored
   in the EXE's .rdata on disk; here we perform the same length-preserving
   byte swap at RUNTIME, on the already-loaded process image, so the
   save/load percent / play-time / progress / date rows read in the Israeli
   (RTL) order while every swap keeps the original byte length (so no
   pointers or offsets break).
 *
 * Each entry: (virtual_address, expected_original_bytes, replacement_bytes,
 * description).  We only overwrite when the current bytes match the expected
 * original, like the python script's status check.  The strings below are
 * byte-for-byte identical to the python literals.
 */
typedef struct {
    DWORD         va;      /* virtual address of the format block      */
    int           len;     /* byte length (no trailing NUL)            */
    const BYTE*   orig;    /* expected original bytes                  */
    const BYTE*   patched; /* replacement bytes (same length)          */
    const char*   desc;    /* description for console logging          */
} FormatSwap;

/* --- swap 1: save/load TIME + PERCENT (two adjacent strings, 36B) --- */
static const BYTE fmt1_orig[] =
    "{0} {1:D2}:{2:D2}:{3:D2}\x00\x00\x00\x00{0} {1}%";
static const BYTE fmt1_patched[] =
    "{0} {3:D2}:{2:D2}:{1:D2}\x00\x00\x00\x00{0} %{1}";

/* --- swap 2: save/load PROGRESS (chapter line, 13B) ---------------- */
static const BYTE fmt2_orig[] =
    "{0} {1} - {2}";
static const BYTE fmt2_patched[] =
    "{0} {1} - {2}";

/* --- swap 3: save/load DATE+TIME (DD-MM-YYYY for Israel, 34B) ------ */
static const BYTE fmt3_orig[] =
    "{0:D2}-{1:D2}-{2:D4} {3:D2}:{4:D2}";
static const BYTE fmt3_patched[] =
    "{4:D2}:{3:D2} {2:D4}-{0:D2}-{1:D2}";

/* ------------------------------------------------------------------ */
/* Helpers                                                            */
/* ------------------------------------------------------------------ */

/* Reverse a contiguous ASCII digit-run at [start, start+runLen) in place. */
static void ReverseDigitRun(char* buf, int start, int runLen) {
    if (runLen <= 1) return;
    int lo = start;
    int hi = start + runLen - 1;
    while (lo < hi) {
        char t = buf[lo];
        buf[lo] = buf[hi];
        buf[hi] = t;
        lo++;
        hi--;
    }
}

/*
 * Hebrew grammar fix: a standalone preposition "ל" renders detached as
 * " ל " (space + lamed + space).  In Hebrew the "to/ל" must join the NEXT
 * word, so the leading space is removed:  " ל "  ->  "ל ".
 *
 * The string shrinks by exactly one byte per fix, so it is compacted in place
 * (read/write pointers, writing never ahead of reading) and the new, shorter
 * length is written back through *len; the caller must null-terminate at the
 * new end.  Runs on the already-reversed slot buffer, so it must be called
 * AFTER the %d-digit restoration (whose captured absolute positions would
 * otherwise be shifted by the compaction).
 *
 * ל in the game font code page is byte 0xEA, so the byte sequence we look for
 * is  0x20 0xEA 0x20  ->  0xEA 0x20  (space + lamed + space).
 *
 * Returns the number of spaces removed (how many bytes the buffer shrank),
 * or 0 if nothing changed.  `*len` is updated to the new (shorter) length.
 */
static int FixLamedAttachment(char* buf, int* len) {
    int n = *len;
    if (n < 3) return 0;

    /* DEBUG: print the whole buffer as hex bytes (before compaction). */
    /*printf("Check Lamed (len=%d): ", n);
    for (int i = 0; i < n; i++)
        printf("%02X ", (BYTE)buf[i]);
    printf("\n");*/

    int r = 0;   /* read pointer  */
    int w = 0;   /* write pointer (w <= r, so compaction is safe) */
    int removed = 0;

    /* NOTE: buf is `char`, which on MSVC/x86 is SIGNED, and 0xEA = 234 is
       >= 0x80.  Comparing a signed char holding byte 0xEA against the int
       literal 234 would sign-extend to -22 and FAIL even when the byte really
       is 0xEA.  So every byte must be cast to BYTE (unsigned) before the
       comparison -- otherwise the 0xEA match would never succeed. */
    while (r < n) {
        if (r + 2 < n
            && (BYTE)buf[r]     == 0x20
            && (BYTE)buf[r + 1] == 0xEA
            && (BYTE)buf[r + 2] == 0x20) {
            buf[w++] = 0xEA;   /* keep lamed */
            buf[w++] = 0x20;   /* keep trailing space */
            r += 3;
            removed++;
        } else {
            buf[w++] = buf[r++];
        }
    }
    *len = w;   /* new (shorter) length */
    return removed;
}

/*
 * Reverse a DrawString string into a ring slot.
 *
 * The engine renders text left-to-right, so a whole-string byte reversal
 * turns a naturally-stored (straight) Hebrew line into the correct RTL/visual
 * order.  We ALWAYS reverse - there is deliberately no longer any digit
 * content filter (unlike the original apply_reverse_patch.py cave wrapper).
 *
 * The pattern in g_patterns[] is checked AFTER the reversal, on the
 * display-order buffer (the DrawString input is in internal LTR order, so
 * only the reversed buffer carries the phrase in renderable form).  When it
 * matches, the embedded number was also flipped by the whole-string reversal;
 * we then reverse every digit-run a SECOND time so the number displays
 * correctly.  e.g. input "תן 614 מטבעות כסף למוכר"
 *        1. whole-reverse -> "רמוכל ףסה תועבטמ 416 ןת"
 *        2. pattern found in reversed buffer, digits again -> "רמוכל ףסה תועבטמ 614 ןת"
 *
 *   - Prefer the char-count (arg4) when it is in [1..127]; otherwise fall
 *     back to strlen (arg4 == -1 is the "draw full string" sentinel).
 *   - Clamp to 127 bytes (slot is 128, need 1 byte for the NUL).
 *
 * Returns the pointer the caller should use in place of the string arg.
 */
static const char* ReverseStringIfNeeded(const char* src, int len, char* slotAddr) {
    if (len <= 0 || len > 127) {
        /* Fall back to strlen for the sentinel / bad values. */
        len = (int)strlen(src);
        if (len > 127) len = 127;
    }

    if (len == 0) {
        return src;              /* empty string: nothing to do */
    }

    /* The string is NOT reversed (returned as-is) when either:
         - it has no byte above 190 (no Hebrew glyphs), or
         - it contains at least one English letter (a-z / A-Z, upper or
           lower case).
       So we only reverse when the string carries Hebrew content (a byte
       > 190) AND has no English letter at all.  English letter test uses
       the ASCII ranges; Hebrew glyphs (bytes > 190) and digits/punctuation
       never count as English letters. */
    {
        int hasRtlByte = 0;
        int hasLetter  = 0;
        const unsigned char* s = (const unsigned char*)src;
        for (int i = 0; i < len; i++) {
            BYTE b = s[i];
            if (b > 190) {
                hasRtlByte = 1;
            } else if ((b >= 'a' && b <= 'z') || (b >= 'A' && b <= 'Z')) {
                hasLetter = 1;
            }
        }
        if (!hasRtlByte && hasLetter) {
            return src;   /* condition to NOT reverse is met: keep original */
        }
    }

    /* 1) Whole-string reverse copy into the slot. */
    const char* end = src + len - 1;
    char* p = slotAddr;
    for (int i = 0; i < len; i++) {
        *p++ = *end--;
    }

    /* 2) Check the patterns on the REVERSED content (what is actually
          rendered to screen).  Matching happens AFTER the reversal, on the
          display-order buffer.  When a pattern matches, reverse each digit
          run it captured via a %d placeholder (a second reversal that
          restores those numbers).  Only the first matching pattern applies. */
    {
        DigitRun caps[MAX_CAPTURES];
        int capCount = 0;
        for (int i = 0; i < g_patternCount; i++) {
            if (MatchPattern(&g_patterns[i], slotAddr, len, caps, &capCount)) {
                for (int c = 0; c < capCount; c++) {
                    ReverseDigitRun(slotAddr, caps[c].start,
                                    caps[c].end - caps[c].start);
                }
                break;   /* only the first matching pattern applies */
            }
        }
    }

    /* 3) Hebrew grammar: attach the preposition "ל" to the next word by
          removing the space before it (" ל " -> "ל ").  This shrinks the
          string by one byte per removed space; FixLamedAttachment() updates
          `len` to the new (shorter) length and returns how many bytes were
          removed. */
    {
        int origLen = len;                 /* length the renderer will draw  */
        int removed  = FixLamedAttachment(slotAddr, &len);

        if (removed > 0) {
            /* The л fix shortened the string, but the game renders a fixed
               number of bytes (the original length - arg4 is not updated).
               The bytes that fell out of the string (now at [len, origLen))
               would render as NUL / empty-glyph shapes.  Fill that tail with
               spaces instead so it looks like a real trailing space. */
            for (int i = len; i < origLen; i++)
                slotAddr[i] = 0x20;                  /* space, not NUL */
            slotAddr[origLen] = '\0';                /* true terminator */
        } else {
            /* Nothing was shortened: normal null-termination. */
            slotAddr[len] = '\0';
        }
        return slotAddr;
    }
}

/*
 * Hook handler: called from the naked ReversalWrapper with the string
 * pointer and char count as its two cdecl arguments.  Picks the current
 * ring slot, runs the reversal logic, and returns the slot address the
 * wrapper should substitute for the original string argument.
 */
static const char* __cdecl OnDrawString(const char* text, int charCount) {
    int slot = (g_ringIndex++) & (RING_SLOTS - 1);
    return ReverseStringIfNeeded(text, charCount, g_ringBuffer[slot]);
}

/* ------------------------------------------------------------------ */
/* Call-site wrapper / hook plumbing                                  */
/* ------------------------------------------------------------------ */

/*
 * ReversalWrapper
 * ---------------
 * Stands in for DrawString at every redirected call-site.  This is the C /
 * naked-function equivalent of the python _build_wrapper() bytecode.  We
 * patch each `CALL DrawString` in .text to `CALL ReversalWrapper`, and here
 * we:
 *   1. Save eax/ecx/esi/edi (exactly like the python wrapper).
 *   2. Read arg3 (text) and arg4 (charCount) from the caller's stack.
 *   3. Call OnDrawString -> returns the pointer to render (a ring slot).
 *   4. Overwrite arg3 on the caller's stack with that pointer.
 *   5. Restore the 4 saved registers and tail-JMP to the real DrawString.
 *
 * Because DrawString's own (untouched) prologue then runs, each caller's
 * register conventions are honored exactly as the engine wrote them -- the
 * crucial difference from hooking DrawString's prologue with a pushad/popad
 * trampoline (which clashed with the differing registers different callers
 * rely on).
 *
 * STACK LAYOUT after `push eax; push ecx; push esi; push edi` (16 bytes):
 *   [esp+0x10] return address
 *   [esp+0x14] arg1  SpriteFont*
 *   [esp+0x18] arg2  parent context
 *   [esp+0x1C] arg3  string ptr     <- read + replaced with slot addr
 *   [esp+0x20] arg4  char count     <- used as length when valid
 */
__declspec(naked) void ReversalWrapper(void) {
    __asm {
        push eax
        push ecx
        push esi
        push edi

        /* --- build the two cdecl args (right-to-left) ---------------- */
        push [esp + 0x20]   /* arg4 = char count  -> OnDrawString */
        push [esp + 0x20]   /* arg3 = string ptr  -> OnDrawString */
        call OnDrawString
        add  esp, 8                   /* drop the two pushed args           */

        /* --- store the returned pointer back into arg3 on the stack ----- */
        mov  [esp + 0x1C], eax

        pop edi
        pop esi
        pop ecx
        pop eax

        /* --- tail-call the real DrawString (returns to the original
                caller, exactly like the python wrapper) --------------- */
        mov eax, DRAWSTRING_VA
        jmp eax
    }
}

/*
 * Enumerate every `CALL DrawString` (E8 rel32) instruction in .text, like
 * python's _find_call_sites().  Returns the count and fills `outSites` with
 * the virtual addresses of those instructions (at most maxSites).
 */
static int EnumerateCallSites(DWORD* outSites, int maxSites) {
    DWORD base = (DWORD)(DWORD_PTR)GetModuleHandle(NULL);
    if (!base) return 0;

    IMAGE_DOS_HEADER* dos = (IMAGE_DOS_HEADER*)base;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return 0;
    IMAGE_NT_HEADERS* nt = (IMAGE_NT_HEADERS*)(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return 0;
    IMAGE_SECTION_HEADER* sec = IMAGE_FIRST_SECTION(nt);
    WORD nsec = nt->FileHeader.NumberOfSections;

    int count = 0;
    for (WORD i = 0; i < nsec; i++) {
        if (memcmp(sec[i].Name, ".text", 5) != 0) continue;

        DWORD textAddr = base + sec[i].VirtualAddress;
        DWORD textSize = sec[i].Misc.VirtualSize;
        if (textSize <= 5) continue;

        for (DWORD j = 0; j < textSize - 5; j++) {
            BYTE* p = (BYTE*)(textAddr + j);
            if (*p != 0xE8) continue;
            int rel = *(int*)(p + 1);
            DWORD srcVA = (DWORD)(DWORD_PTR)p;          /* of the E8        */
            if (srcVA + 5 + rel == DRAWSTRING_VA) {     /* calls DrawString */
                if (count < maxSites) outSites[count++] = srcVA;
            }
        }
    }
    return count;
}

/*
 * Redirect every CALL DrawString site to ReversalWrapper (mirrors python's
 * per-site patch).  Original 5 bytes of each site are saved so RemoveHook()
 * can restore them.
 */
static void RedirectCallSites(void) {
    g_callSiteCount = EnumerateCallSites(g_callSites, MAX_CALLSITES);

    for (int i = 0; i < g_callSiteCount; i++) {
        DWORD site = g_callSites[i];
        BYTE* p = (BYTE*)(DWORD_PTR)site;

        memcpy(g_callSitesOrig[i], p, 5);

        DWORD oldProtect;
        VirtualProtect(p, 5, PAGE_EXECUTE_READWRITE, &oldProtect);
        p[0] = 0xE8;
        *(int*)(p + 1) = (int)((DWORD_PTR)ReversalWrapper - (site + 5));
        VirtualProtect(p, 5, oldProtect, &oldProtect);
    }

    printf("[+] Redirected %d drawString call-site(s) to ReversalWrapper\n",
           g_callSiteCount);
}

/* Restore every redirected call-site to its original CALL DrawString. */
static void RestoreCallSites(void) {
    for (int i = 0; i < g_callSiteCount; i++) {
        DWORD site = g_callSites[i];
        BYTE* p = (BYTE*)(DWORD_PTR)site;

        DWORD oldProtect;
        VirtualProtect(p, 5, PAGE_EXECUTE_READWRITE, &oldProtect);
        memcpy(p, g_callSitesOrig[i], 5);
        VirtualProtect(p, 5, oldProtect, &oldProtect);
    }
    g_callSiteCount = 0;
}

/*
 * ApplyFormatSwaps
 * ---------------
 * Runtime equivalent of the format-string IN-PLACE rewrites that the python
 * script (apply_reverse_patch.py, apply_patch(), lines 607-625) performs
 * against the EXE file.  Here we patch the SAME addresses inside the already
 * loaded process image so the save/load percent / play-time / progress / date
 * rows use the Israeli (RTL) placeholder order.
 *
 * Each swap is length-preserving, so no pointer or offset changes.  We only
 * overwrite a block when its current bytes equal its expected original
 * (mirroring the python "original -> patched" status check); anything already
 * patched is skipped.
 */
static void ApplyFormatSwaps(void) {
    static const FormatSwap swaps[] = {
        { 0x0052CABC, (int)(sizeof(fmt1_orig) - 1), fmt1_orig, fmt1_patched,
          "save/load TIME + PERCENT" },
        { 0x0052CA7C, (int)(sizeof(fmt2_orig) - 1), fmt2_orig, fmt2_patched,
          "save/load PROGRESS (chapter line)" },
        { 0x0052CA98, (int)(sizeof(fmt3_orig) - 1), fmt3_orig, fmt3_patched,
          "save/load DATE+TIME (DD-MM-YYYY for Israel)" },
    };
    const int n = (int)(sizeof(swaps) / sizeof(swaps[0]));

    for (int i = 0; i < n; i++) {
        const FormatSwap* s = &swaps[i];
        BYTE* dst = (BYTE*)(DWORD_PTR)s->va;

        /* Already in its patched state?  Then nothing to do. */
        if (memcmp(dst, s->patched, s->len) == 0) {
            printf("[i] format swap 0x%08X already patched (%s) - skipping\n",
                   s->va, s->desc);
            continue;
        }

        /* Verify the original bytes are present before overwriting. */
        if (memcmp(dst, s->orig, s->len) != 0) {
            printf("[!] format block 0x%08X unexpected bytes (%s) - skipping\n",
                   s->va, s->desc);
            continue;
        }

        DWORD oldProtect;
        if (!VirtualProtect(dst, s->len, PAGE_READWRITE, &oldProtect)) {
            printf("[!] format swap 0x%08X VirtualProtect failed (%s)\n",
                   s->va, s->desc);
            continue;
        }
        memcpy(dst, s->patched, s->len);
        VirtualProtect(dst, s->len, oldProtect, &oldProtect);
        printf("[+] format swap applied 0x%08X (%d bytes - %s)\n",
               s->va, s->len, s->desc);
    }
}

/*
 * RestoreFormatSwaps
 * ------------------
 * Runtime inverse of ApplyFormatSwaps(): write the original bytes back at
 * the same addresses (mirrors the python restore_patch() for the format
 * blocks).  Only restores a block that is currently in its patched state.
 */
static void RestoreFormatSwaps(void) {
    static const FormatSwap swaps[] = {
        { 0x0052CABC, (int)(sizeof(fmt1_orig) - 1), fmt1_orig, fmt1_patched,
          "save/load TIME + PERCENT" },
        { 0x0052CA7C, (int)(sizeof(fmt2_orig) - 1), fmt2_orig, fmt2_patched,
          "save/load PROGRESS (chapter line)" },
        { 0x0052CA98, (int)(sizeof(fmt3_orig) - 1), fmt3_orig, fmt3_patched,
          "save/load DATE+TIME (DD-MM-YYYY for Israel)" },
    };
    const int n = (int)(sizeof(swaps) / sizeof(swaps[0]));

    for (int i = 0; i < n; i++) {
        const FormatSwap* s = &swaps[i];
        BYTE* dst = (BYTE*)(DWORD_PTR)s->va;

        if (memcmp(dst, s->orig, s->len) == 0) {
            continue; /* already original */
        }
        if (memcmp(dst, s->patched, s->len) != 0) {
            printf("[!] format block 0x%08X unexpected bytes (%s) - not restored\n",
                   s->va, s->desc);
            continue;
        }

        DWORD oldProtect;
        if (!VirtualProtect(dst, s->len, PAGE_READWRITE, &oldProtect)) {
            continue;
        }
        memcpy(dst, s->orig, s->len);
        VirtualProtect(dst, s->len, oldProtect, &oldProtect);
        printf("[+] format swap restored 0x%08X (%s)\n", s->va, s->desc);
    }
}

static void InstallHook(void) {
    /* Parse the regex-like numeric patterns once. */
    InitPatterns();

    /* Mirror the python script's on-disk format-string rewrites, now at
       runtime on the loaded image. */
    ApplyFormatSwaps();

    /* Like python's apply_patch(): redirect every CALL DrawString site to
       our wrapper.  This leaves DrawString's own prologue untouched so each
       caller keeps its own register conventions. */
    RedirectCallSites();

    g_hookInstalled = 1;
}

static void RemoveHook(void) {
    if (!g_hookInstalled) return;

    /* Mirror python's restore_patch(): restore call-sites + format swaps. */
    RestoreCallSites();
    RestoreFormatSwaps();

    g_hookInstalled = 0;
}

void CreateDebugConsole(void) {
    if (AllocConsole()) {
        FILE* fp;
        freopen_s(&fp, "CONOUT$", "w", stdout);
        freopen_s(&fp, "CONOUT$", "w", stderr);
        SetConsoleTitleW(L"MI2 Render Hook (DrawString reversal)");
    }
}

void FreeDebugConsole(void) {
    FreeConsole();
}

BOOL APIENTRY DllMain(HMODULE hModule, DWORD reason, LPVOID lpReserved) {
    switch (reason) {
    case DLL_PROCESS_ATTACH:
        DisableThreadLibraryCalls(hModule);
        //CreateDebugConsole();
        InstallHook();
        break;
    case DLL_PROCESS_DETACH:
        RemoveHook();
        //FreeDebugConsole();
        break;
    }
    return TRUE;
}
