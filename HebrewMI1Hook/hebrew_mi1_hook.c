/*
 * hebrew_mi1_hook.c
 * ----------------------------------------------------------------------------
 * 32-bit x86 Proxy DLL (version.dll) for Monkey Island 1: SE (MISE.exe).
 *
 * THE THREE PATCHES REPLICATED HERE (were translate_mi1.cmd python steps):
 *   1. apply_mi1_verbline_rtl.py
 *      Reverse the 4 component strings of the sentence line (verb / obj1 /
 *      prep / obj2) so Hebrew RTL reads left-to-right from the right.
 *      Hook site : 0x0047C3DB  (builder tail-jump `jmp 0x497CE0`)
 *      Component inserter : 0x00497CE0
 *   2. apply_mi1_merge_to_object.py
 *      Glue the proclitic preposition (lamed "to" / bet "in") onto its
 *      object by deleting the space before it.
 *      Hook site : 0x0048590B  (inside FUN_0x00485900)
 *      Return    : 0x00485910
 *   3. apply_mi1_dynamic_text_translate.py
 *      Translate dynamic-numbered phrases ("N pieces of eight") and the actor
 *      tooltip "Guybrush" at draw time.
 *      Hook site : 0x00485900  (start of FUN_0x00485900, 8 stolen bytes)
 *      Return    : 0x00485908
 *
 * BUILDS AS 32-BIT "version.dll".  Each version.dll export is a naked thunk
 * that tail-JMPs into the real system version.dll (dynamic proxying).  The
 * Hebrew hooks run on a worker thread started from DllMain so DLL load never
 * blocks the game.
 */

/* Disable MSVC's "unsafe function" errors for strcpy/strlen etc. */
#define _CRT_SECURE_NO_WARNINGS

#include <windows.h>
#include <stdio.h>
#include <string.h>

/* ------------------------------------------------------------------------- */
/* Config: addresses you must confirm in YOUR unpacked MISE.exe              */
/* ========================================================================= */
#define REVERSAL_HOOK      0x0047C3DB   /* builder tail-jump site (5 bytes)  */
#define COMPONENT_INSERTER 0x00497CE0   /* word/verb inserter (tail target)  */
#define MERGE_HOOK         0x0048590B   /* merge hook site (5 bytes)         */
#define MERGE_RETURN       0x00485910   /* resume after merge hook           */
#define DYNTEXT_HOOK       0x00485900   /* dynamic-translate hook (8 bytes)  */
#define DYNTEXT_RETURN     0x00485908   /* resume after dyntext hook         */

/* Screen buffer VAs (fixed game globals).  slot2 = prep, slot3 = obj2. */
#define SCN_VERB 0x0056FB30
#define SCN_OBJ1 0x0056FB70
#define SCN_PREP 0x0056FBF0
#define SCN_OBJ2 0x0056FBB0

/* Proclitic preposition font code bytes to glue onto the object. */
#define LAMED_CODE 0xEA
#define BET_CODE   0xE5
void* CALL_PATCH1_OFFSET = (void*)0x497CE0;

/* ========================================================================= */
/* SECTION 1 - DYNAMIC PROXY LAYER (stand-in for the system version.dll)     */
/* ========================================================================= */

static BOOL PathAppendW(wchar_t* out, const wchar_t* dir,
                        DWORD cap, const wchar_t* file) {
    size_t d = wcslen(dir);
    size_t f = wcslen(file);
    if (d + 1 + f + 1 > cap) return FALSE;
    memcpy(out, dir, d * sizeof(wchar_t));
    out[d] = L'\\';
    memcpy(out + d + 1, file, (f + 1) * sizeof(wchar_t));
    return TRUE;
}

static BOOL GetSystemDllPath(wchar_t* out, DWORD cap) {
    if (!out || cap < 1) return FALSE;

    wchar_t sysDir[MAX_PATH];
    DWORD   len = 0;

    HMODULE hKernel = GetModuleHandleW(L"kernel32.dll");
    typedef UINT(WINAPI *fnGetSysWow64)(LPWSTR, UINT);
    fnGetSysWow64 pGetSysWow64 = NULL;
    if (hKernel) {
        pGetSysWow64 = (fnGetSysWow64)GetProcAddress(
            hKernel, "GetSystemWow64DirectoryW");
    }
    if (pGetSysWow64) {
        len = pGetSysWow64(sysDir, MAX_PATH);
    }
    if (len == 0 || len >= MAX_PATH) {
        len = GetSystemDirectoryW(sysDir, MAX_PATH);
    }
    if (len == 0 || len >= MAX_PATH) return FALSE;

    return PathAppendW(out, sysDir, cap, L"version.dll");
}

static HMODULE g_hRealVersion = NULL;

#define EXPORT_STR2(x) #x
#define EXPORT_STR(x)  EXPORT_STR2(x)

#define DEFINE_VERSION_PROXY(exportname)                                        \
    static FARPROC pfn_##exportname = NULL;                                     \
    __declspec(naked) void exportname##_fwd(void) {                             \
        __asm { jmp dword ptr [pfn_##exportname] }                              \
    }                                                                           \
    __pragma(comment(linker, "/export:" EXPORT_STR(exportname)                  \
                             "=_" EXPORT_STR(exportname) "_fwd"))

DEFINE_VERSION_PROXY(GetFileVersionInfoA)
DEFINE_VERSION_PROXY(GetFileVersionInfoByHandle)
DEFINE_VERSION_PROXY(GetFileVersionInfoExA)
DEFINE_VERSION_PROXY(GetFileVersionInfoExW)
DEFINE_VERSION_PROXY(GetFileVersionInfoSizeA)
DEFINE_VERSION_PROXY(GetFileVersionInfoSizeExA)
DEFINE_VERSION_PROXY(GetFileVersionInfoSizeExW)
DEFINE_VERSION_PROXY(GetFileVersionInfoSizeW)
DEFINE_VERSION_PROXY(GetFileVersionInfoW)
DEFINE_VERSION_PROXY(VerFindFileA)
DEFINE_VERSION_PROXY(VerFindFileW)
DEFINE_VERSION_PROXY(VerInstallFileA)
DEFINE_VERSION_PROXY(VerInstallFileW)
DEFINE_VERSION_PROXY(VerLanguageNameA)
DEFINE_VERSION_PROXY(VerLanguageNameW)
DEFINE_VERSION_PROXY(VerQueryValueA)
DEFINE_VERSION_PROXY(VerQueryValueW)

static BOOL InitVersionProxy(void) {
    wchar_t realPath[MAX_PATH];
    if (!GetSystemDllPath(realPath, MAX_PATH)) {
        return FALSE;
    }
    g_hRealVersion = LoadLibraryW(realPath);
    if (!g_hRealVersion) {
        return FALSE;
    }
#define BIND_VERSION_PROXY(exportname) do {                                    \
        pfn_##exportname = GetProcAddress(g_hRealVersion, #exportname);        \
    } while (0)
    BIND_VERSION_PROXY(GetFileVersionInfoA);
    BIND_VERSION_PROXY(GetFileVersionInfoByHandle);
    BIND_VERSION_PROXY(GetFileVersionInfoExA);
    BIND_VERSION_PROXY(GetFileVersionInfoExW);
    BIND_VERSION_PROXY(GetFileVersionInfoSizeA);
    BIND_VERSION_PROXY(GetFileVersionInfoSizeExA);
    BIND_VERSION_PROXY(GetFileVersionInfoSizeExW);
    BIND_VERSION_PROXY(GetFileVersionInfoSizeW);
    BIND_VERSION_PROXY(GetFileVersionInfoW);
    BIND_VERSION_PROXY(VerFindFileA);
    BIND_VERSION_PROXY(VerFindFileW);
    BIND_VERSION_PROXY(VerInstallFileA);
    BIND_VERSION_PROXY(VerInstallFileW);
    BIND_VERSION_PROXY(VerLanguageNameA);
    BIND_VERSION_PROXY(VerLanguageNameW);
    BIND_VERSION_PROXY(VerQueryValueA);
    BIND_VERSION_PROXY(VerQueryValueW);
#undef BIND_VERSION_PROXY
    return TRUE;
}

/* ========================================================================= */
/* SECTION 2 - Common hooking helpers                                        */
/* ========================================================================= */

/* Patch a 5-byte relative JMP at `site` (0xE9 rel32) to `targetVA`. */
static void PatchJmp5(DWORD site, DWORD_PTR targetVA) {
    BYTE* p = (BYTE*)(DWORD_PTR)site;
    DWORD oldProtect;
    if (!VirtualProtect(p, 5, PAGE_EXECUTE_READWRITE, &oldProtect)) return;
    p[0] = 0xE9;
    *(long*)(p + 1) = (long)((DWORD_PTR)targetVA - (site + 5));
    VirtualProtect(p, 5, oldProtect, &oldProtect);
}

/* Patch an 8-byte window (JMP rel32 + 3 NOPs). */
static void PatchJmp8(DWORD site, DWORD_PTR targetVA) {
    BYTE* p = (BYTE*)(DWORD_PTR)site;
    DWORD oldProtect;
    if (!VirtualProtect(p, 8, PAGE_EXECUTE_READWRITE, &oldProtect)) return;
    p[0] = 0xE9;
    *(long*)(p + 1) = (long)((DWORD_PTR)targetVA - (site + 5));
    p[5] = 0x90; p[6] = 0x90; p[7] = 0x90;
    VirtualProtect(p, 8, oldProtect, &oldProtect);
}

/* ========================================================================= */
/* SECTION 3 - PATCH 1: Sentence-line component-order reversal (RTL)         */
/* ========================================================================= */

/* The 4 component buffer VAs in screen order.  Referenced by symbol in the
   naked ReversalCave below (MSVC resolves the relocation). */
static const DWORD screenBufs[4] = { SCN_VERB, SCN_OBJ1, SCN_PREP, SCN_OBJ2 };

__declspec(naked) void ReversalCave(void) {
    __asm {
        /* 1) finish building the 4th component (calls inserter, returns).
           EAX is caller-saved scratch here; an indirect call keeps the
           register/stack state the builder set up for the inserter. */
        call dword ptr[CALL_PATCH1_OFFSET]

        pushad
        pushfd

        /* ebx = M = leading non-empty component count (screen order). */
        xor ebx, ebx
        mov eax, dword ptr [screenBufs + 0]
        cmp byte ptr [eax], 0
        je  mdone
        inc ebx
        mov eax, dword ptr [screenBufs + 4]
        cmp byte ptr [eax], 0
        je  mdone
        inc ebx
        mov eax, dword ptr [screenBufs + 8]
        cmp byte ptr [eax], 0
        je  mdone
        inc ebx
        mov eax, dword ptr [screenBufs + 12]
        cmp byte ptr [eax], 0
        je  mdone
        inc ebx
    mdone:

        /* reverse first M 64-byte blocks: i=ecx=0, j=edx=M-1 */
        xor ecx, ecx               /* i = 0 */
        lea edx, [ebx - 1]         /* j = M-1 */
    sloop:
        cmp ecx, edx
        jge sdone
        mov esi, dword ptr [screenBufs + ecx*4]
        mov edi, dword ptr [screenBufs + edx*4]
        push ecx
        push edx
        mov ecx, 16                /* 64 bytes = 16 dwords */
    b64:
        mov eax, dword ptr [esi]
        mov ebp, dword ptr [edi]
        mov dword ptr [edi], eax
        mov dword ptr [esi], ebp
        add esi, 4
        add edi, 4
        dec ecx
        jnz b64
        pop edx
        pop ecx
        inc ecx
        dec edx
        jmp sloop
    sdone:
        popfd
        popad
        ret                        /* returns to the builder's caller */
    }
}

/* ========================================================================= */
/* SECTION 4 - PATCH 2: Merge proclitic preposition onto its object          */
/* ========================================================================= */

__declspec(naked) void MergeCave(void) {
    __asm {
        pushad
        pushfd
        mov esi, edi            /* esi = scan pointer, edi = string base */
    mscan:
        mov al, byte ptr [esi]
        test al, al
        jz  mdone_scan
        cmp al, 0x20            /* want pattern 0x20 <prep> 0x20 */
        jne mnext
        mov al, byte ptr [esi + 1]
        cmp al, LAMED_CODE
        je  mchk2
        cmp al, BET_CODE
        jne mnext
    mchk2:
        cmp byte ptr [esi + 2], 0x20
        jne mnext
        /* match: [esi]=space, [esi+1]=prep, [esi+2]=space.
           shift [esi+1..] left by one byte over the leading space. */
        mov edx, esi            /* dst = esi (overwrite leading space) */
        lea ecx, [esi + 1]      /* src = esi+1 */
    mshift:
        mov al, byte ptr [ecx]
        test al, al
        jz  mpad                /* reached terminator -> pad */
        mov byte ptr [edx], al
        inc edx
        inc ecx
        jmp mshift
    mpad:
        mov byte ptr [edx], 0x20  /* fill vacated slot; keep old NUL */
        jmp mscan
    mnext:
        inc esi
        jmp mscan
    mdone_scan:
        popfd
        popad
        /* restore the two original instructions we hijacked */
        cmp dword ptr [esp + 0x44], ebp
        push esi
        /* absolute jump back into the game (register-indirect) */
        mov eax, MERGE_RETURN
        jmp eax
    }
}

/* ========================================================================= */
/* SECTION 5 - PATCH 3: Dynamic-numbered-phrase translation at draw time     */
/* ========================================================================= */

static const char* DynamicTranslateC(const char* str);   /* forward decl */

__declspec(naked) void DynamicTranslateCave(void) {
    __asm {
        pushad
        pushfd
        push edi               /* arg: string base */
        call DynamicTranslateC
        add  esp, 4
        popfd
        popad
        /* restore the 8 stolen bytes of FUN_0x00485900 */
        sub esp, 0x34
        push ebx
        mov ebx, dword ptr [esp + 0x3C]
        /* absolute jump back into the game (register-indirect) */
        mov eax, DYNTEXT_RETURN
        jmp eax
    }
}

/* English keys. */
#define PIECES_EN_LEN   15   /* "pieces of eight" */
#define PIECE_EN_LEN    14   /* "piece of eight" */
#define GUYBRUSH_LEN     8   /* "Guybrush" */
static const char PIECES_EN[]   = "pieces of eight";
static const char PIECE_EN[]    = "piece of eight";
static const char GUYBRUSH_EN[] = "Guybrush";

/* Real (non-padded) Hebrew payload byte-lengths. */
#define HEB_PLURAL_SZ    10
#define HEB_SINGULAR_SZ  11

/* Hebrew (reversed-for-LTR) payloads -- byte-for-byte identical to
   apply_mi1_dynamic_text_translate.py. */
static const BYTE enc_plural_heb[HEB_PLURAL_SZ] = {
    0xE6, 0xF1, 0xC7, 0x20, 0xE1, 0xCD, 0xEB, 0xE5, 0xE9, 0xD1
};
static const BYTE enc_singular_heb[HEB_SINGULAR_SZ] = {
    0xE6, 0xF1, 0xC7, 0x20, 0xEA, 0xE0, 0x20, 0xEB, 0xE5, 0xE9, 0xD1
};
static const BYTE enc_guybrush_heb[GUYBRUSH_LEN] = {
    0x20, 0xEB, 0xE5, 0xBA, 0xE0, 0xD2, 0xDF, 0xE5
};

/* Pre-encoded (reversed-for-LTR) Hebrew byte strings, byte-for-byte identical
   to apply_mi1_dynamic_text_translate.py HEB_PLURAL / HEB_SINGULAR /
   GUYBRUSH_HEB.  Shorter than the English keys; LocalizeShiftPad() mirrors the
   Python cave: it writes the Hebrew, then LEFT-SHIFTS the trailing text flush
   against it (the `lea esi,[edi + PLURAL_PAD]` trick) and finally re-fills the
   reclaimed tail slots with SPACEs so the total byte count and the fixed NUL
   position are preserved. */

/* Localize an engLen English key at `start` to a shorter hebLen Hebrew payload,
   exactly like apply_mi1_dynamic_text_translate.py's PASS1/PASS2 assembly:
     "pieces of eight"(15)  -> 10 Hebrew bytes + left-shift + pad tail
     "piece of eight" (14)  -> 11 Hebrew bytes + left-shift + pad tail
   The engine draws this text with a FIXED length (see the merge patch notes:
   "the draw renders a fixed count, so a shorter string exposes the terminating
   NUL as a bogus glyph").  Therefore we must NOT shorten the line.  Instead:

     * write hebLen Hebrew bytes at `start`
       -> match_start .. match_start+hebLen-1
     * dst = start+hebLen, src = start+engLen
     * shift (copy) the text that FOLLOWED the English key leftwards so it sits
       flush right after the Hebrew  -- the "collapsing" step
     * finally write (engLen-hebLen) SPACEs at `dst` (the slots just before the
       ORIGINAL NUL).  The NUL never moves, so strlen()/cached draw count and
       the fixed-distance glyph reader all stay consistent and never render a
       NUL/box or a stale tail byte over the following word.
   Because we shifted the follow-on text flush first, these pad spaces sit only
   at the very end of the line -- there is no gap inserted *between* the Hebrew
   and the word that follows it. */
static void LocalizeShiftPad(BYTE* start, int engLen,
                             const BYTE* heb, int hebLen) {
    int k;
    for (k = 0; k < hebLen; k++) start[k] = heb[k];   /* Hebrew letters */

    {
        BYTE* dst       = start + hebLen;   /* start; advanced by the shift    */
        const BYTE* src = start + engLen;   /* text right after the key        */
        while (*src != 0) {                 /* LEFT-SHIFT tail flush to Hebrew */
            *dst = *src;
            ++src;
            ++dst;
        }
        /* Reclaim the (engLen-hebLen) freed bytes with SPACE, keeping the NUL:
           dst now sits exactly (engLen-hebLen) bytes before the original NUL. */
        k = engLen - hebLen;
        while (k-- > 0) { *dst = 0x20; ++dst; }
    }
}

static const char* DynamicTranslateC(const char* str) {
    size_t slen = strnlen(str, 1024);

    if (slen >= PIECES_EN_LEN) {
        for (size_t i = 0; i + PIECES_EN_LEN <= slen; i++) {
            if (str[i] == 'p' && memcmp(str + i, PIECES_EN, PIECES_EN_LEN) == 0) {
                int leftOk  = (i == 0) || str[i - 1] == ' ';
                char after  = (i + PIECES_EN_LEN < slen) ? str[i + PIECES_EN_LEN] : 0;
                int rightOk = (after == 0 || after == ' ');
                if (leftOk && rightOk) {
                    LocalizeShiftPad((BYTE*)(str + i),
                                     PIECES_EN_LEN,
                                     enc_plural_heb,
                                     HEB_PLURAL_SZ);
                    printf("[+] MI1 dyntext: replaced 'pieces of eight'\n");
                    return str;
                }
            }
        }
    }
    if (slen >= PIECE_EN_LEN) {
        for (size_t i = 0; i + PIECE_EN_LEN <= slen; i++) {
            if (str[i] == 'p' && memcmp(str + i, PIECE_EN, PIECE_EN_LEN) == 0) {
                int leftOk  = (i == 0) || str[i - 1] == ' ';
                char after  = (i + PIECE_EN_LEN < slen) ? str[i + PIECE_EN_LEN] : 0;
                int rightOk = (after == 0 || after == ' ');
                if (leftOk && rightOk) {
                    LocalizeShiftPad((BYTE*)(str + i),
                                     PIECE_EN_LEN,
                                     enc_singular_heb,
                                     HEB_SINGULAR_SZ);
                    printf("[+] MI1 dyntext: replaced 'piece of eight'\n");
                    return str;
                }
            }
        }
    }
    if (slen >= GUYBRUSH_LEN) {
        for (size_t i = 0; i + GUYBRUSH_LEN <= slen; i++) {
            if (str[i] == 'G' && memcmp(str + i, GUYBRUSH_EN, GUYBRUSH_LEN) == 0) {
                int leftOk  = (i == 0) || str[i - 1] == ' ';
                char after  = (i + GUYBRUSH_LEN < slen) ? str[i + GUYBRUSH_LEN] : 0;
                int rightOk = (after == 0 || after == ' ');
                if (leftOk && rightOk) {
                    BYTE* p = (BYTE*)(str + i);
                    memcpy(p, enc_guybrush_heb, GUYBRUSH_LEN);
                    printf("[+] MI1 dyntext: replaced 'Guybrush'\n");
                    return str;
                }
            }
        }
    }
    return str;
}

/* ========================================================================= */
/* SECTION 6 - Install / remove the three in-memory hooks                    */
/* ========================================================================= */

static int  g_hooksInstalled = 0;
static BYTE g_savedReversal[5];
static BYTE g_savedMerge[5];
static BYTE g_savedDyntext[8];

static void InstallHooks(void) {
    /* Patch 1: reversal (5 bytes at 0x0047C3DB) */
    {
        BYTE* p = (BYTE*)(DWORD_PTR)REVERSAL_HOOK;
        memcpy(g_savedReversal, p, 5);
        if (p[0] == 0xE9) {
            long origRel = *(long*)(p + 1);
            DWORD target = (REVERSAL_HOOK + 5) + origRel;
            if (target == COMPONENT_INSERTER) {
                PatchJmp5(REVERSAL_HOOK, (DWORD_PTR)ReversalCave);
                printf("[+] MI1 hook: sentence-line RTL reversal installed @ 0x%08X\n",
                       (DWORD)REVERSAL_HOOK);
            } else {
                printf("[!] MI1 hook: reversal target 0x%08X != 0x%08X - skipping\n",
                       (DWORD)target, (DWORD)COMPONENT_INSERTER);
            }
        } else {
            printf("[!] MI1 hook: reversal site not a JMP - skipping\n");
        }
    }

    /* Patch 2: merge (5 bytes at 0x0048590B) */
    {
        BYTE* p = (BYTE*)(DWORD_PTR)MERGE_HOOK;
        memcpy(g_savedMerge, p, 5);
        if (p[0] == 0x39 && p[1] == 0x6C && p[2] == 0x24 &&
            p[3] == 0x44 && p[4] == 0x56) {
            PatchJmp5(MERGE_HOOK, (DWORD_PTR)MergeCave);
            printf("[+] MI1 hook: lamed/bet merge installed @ 0x%08X\n",
                   (DWORD)MERGE_HOOK);
        } else {
            printf("[!] MI1 hook: merge site bytes unexpected - skipping\n");
        }
    }

    /* Patch 3: dynamic translate (8 bytes at 0x00485900) */
    {
        BYTE* p = (BYTE*)(DWORD_PTR)DYNTEXT_HOOK;
        memcpy(g_savedDyntext, p, 8);
        if (p[0] == 0x83 && p[1] == 0xEC && p[2] == 0x34 &&
            p[3] == 0x53 && p[4] == 0x8B && p[5] == 0x5C &&
            p[6] == 0x24 && p[7] == 0x3C) {
            PatchJmp8(DYNTEXT_HOOK, (DWORD_PTR)DynamicTranslateCave);
            printf("[+] MI1 hook: dynamic-phrase translate installed @ 0x%08X\n",
                   (DWORD)DYNTEXT_HOOK);
        } else {
            printf("[!] MI1 hook: dyntext site bytes unexpected - skipping\n");
        }
    }

    g_hooksInstalled = 1;
}

static void RemoveHooks(void) {
    if (!g_hooksInstalled) return;

    {
        DWORD oldProtect;
        BYTE* p = (BYTE*)(DWORD_PTR)REVERSAL_HOOK;
        if (VirtualProtect(p, 5, PAGE_EXECUTE_READWRITE, &oldProtect)) {
            memcpy(p, g_savedReversal, 5);
            VirtualProtect(p, 5, oldProtect, &oldProtect);
        }
    }
    {
        DWORD oldProtect;
        BYTE* p = (BYTE*)(DWORD_PTR)MERGE_HOOK;
        if (VirtualProtect(p, 5, PAGE_EXECUTE_READWRITE, &oldProtect)) {
            memcpy(p, g_savedMerge, 5);
            VirtualProtect(p, 5, oldProtect, &oldProtect);
        }
    }
    {
        DWORD oldProtect;
        BYTE* p = (BYTE*)(DWORD_PTR)DYNTEXT_HOOK;
        if (VirtualProtect(p, 8, PAGE_EXECUTE_READWRITE, &oldProtect)) {
            memcpy(p, g_savedDyntext, 8);
            VirtualProtect(p, 8, oldProtect, &oldProtect);
        }
    }
    g_hooksInstalled = 0;
}

/* ========================================================================= */
/* SECTION 7 - Entry point                                                    */
/* ========================================================================= */

static DWORD WINAPI HookWorkerThread(LPVOID lpParam) {
    (void)lpParam;
    printf("[+] HebrewMI1Hook: worker thread started\n");
    InstallHooks();
    printf("[+] HebrewMI1Hook: hooks installed\n");
    return 0;
}

BOOL APIENTRY DllMain(HMODULE hModule, DWORD reason, LPVOID lpReserved) {
    switch (reason) {
    case DLL_PROCESS_ATTACH:
        DisableThreadLibraryCalls(hModule);
        if (InitVersionProxy()) {
            HANDLE hThread = CreateThread(NULL, 0, HookWorkerThread, NULL, 0, NULL);
            if (hThread) CloseHandle(hThread);
        }
        break;
    case DLL_PROCESS_DETACH:
        RemoveHooks();
        break;
    }
    return TRUE;
}
