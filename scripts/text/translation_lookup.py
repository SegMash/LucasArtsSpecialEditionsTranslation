"""
Translation lookup cascade for English -> Hebrew.

This module encapsulates a robust multi-step lookup that handles many text
variations between the source game text and the manually-maintained mapping
files (case, spaces, periods, quotes, backticks, embedded {tokens}, and
non-printable noise characters).

Used by both:
  - inject_translation.py    (live runtime translation during .info rebuild)
  - build_translation.py     (one-shot generation of line-synced he.*.txt)

Lookup cascade (in order):
   1.  Exact match.
   2.  Period-toggled exact key (add/remove trailing '.').
   3.  lstrip whitespace.
   4.  lstrip + period-toggled.
   4b. Strip leading quote/apostrophe/backtick characters.
   5.  Collapse multi-spaces.
   6.  Collapse multi-spaces + period-toggled.
   6b. Strip non-ASCII / non-Hebrew noise characters and re-normalise.
   7.  Remove ALL spaces (case-sensitive).
   8.  Remove ALL spaces + case-insensitive.
   9.  Quote-swap (backtick <-> single-quote) applied to steps 1-8.
   10. Compound backtick split: translate "`Title`" and plain text separately.
   11. {game_code} token strip: remove {tokens}, translate plain text,
       reattach tokens at their original positions.
   12. Strip all backticks and retry steps 1-9.
   13. Fallback in the extra mapping file (multiple normalised variants).
"""

from __future__ import annotations

import re

# ── Regex helpers ────────────────────────────────────────────────────────────

# Two or more consecutive spaces -> collapse to one.
_MULTI_SPACE = re.compile(r' {2,}')

# Characters that are neither standard ASCII printable (0x20-0x7E) nor in the
# Hebrew Unicode block (U+05D0-U+05FF) — used to strip game-engine noise chars.
_SPECIAL_CHAR_RE = re.compile(r'[^\x20-\x7E\u05D0-\u05FF]')

# Matches "protected" segments which must NOT be reversed/touched:
#   {anything}   -> game-engine code token
#   `anything`   -> backtick-wrapped quote
_TOKEN_RE = re.compile(r'(\{[^}]+\}|`[^`]*`)')

# Leading non-word characters used as separators between compound parts,
# e.g. the ". " in  "`Title`. Some comment."
_LEADING_SEP_RE = re.compile(r'^[^\w`{(]+')


def strip_special(key: str) -> str:
    """Replace non-ASCII-printable / non-Hebrew chars with a space, then normalise."""
    return _MULTI_SPACE.sub(' ', _SPECIAL_CHAR_RE.sub(' ', key)).strip()


def _toggle_period(key: str) -> str:
    """Return key with trailing period removed if present, or added if absent."""
    return key[:-1] if key.endswith('.') else key + '.'


def _unescape_newlines(text: str) -> str:
    r"""Convert literal "\n" / "\r" sequences in mapping-file lines to real
    newline / carriage-return characters.

    Mapping files keep one entry per file line, so a multi-line message has
    to be written with literal "\n" / "\r" sequences (backslash + letter).
    The text we look up at runtime, however, comes from the .info binary or
    from `unescape_newlines`-processed extracts and contains REAL newlines.
    Without this conversion the two never match for multi-line keys.
    """
    return (text
            .replace(r"\r\n", "\r\n")
            .replace(r"\n",   "\n")
            .replace(r"\r",   "\r"))


# ── Main class ───────────────────────────────────────────────────────────────

class TranslationLookup:
    """Encapsulates the primary mapping table, an extra-mapping fallback, and
    helper indices for fuzzy matching.  All lookup methods are instance methods
    so multiple independent tables can coexist if ever needed.
    """

    def __init__(self) -> None:
        self.table: dict[str, str] = {}
        self.extra_table: dict[str, str] = {}
        # key.replace(' ', '')          -> hebrew  (case-sensitive)
        self._nospace_index: dict[str, str] = {}
        # key.replace(' ', '').lower()  -> hebrew  (case-insensitive)
        self._nospace_lower_index: dict[str, str] = {}

    # ── Loaders ─────────────────────────────────────────────────────────────

    def load_mapping(self, path: str, encoding: str = "windows-1255") -> int:
        """Load the primary mapping file (lines: 'english === hebrew').

        Literal "\\n" / "\\r" in the file are converted to real newline /
        carriage-return characters in both keys and values, so multi-line
        messages match against decoded .info text.

        Returns the number of entries inserted into the primary table.
        Subsequent calls add to the existing table (later entries do NOT
        override earlier exact-key duplicates in the auxiliary indices).
        """
        loaded = 0
        with open(path, encoding=encoding, errors="replace") as f:
            for line in f:
                line = line.rstrip("\r\n")
                if " === " not in line:
                    continue
                en, _, he = line.partition(" === ")
                he = _unescape_newlines(he.strip())
                # Do NOT strip en — leading/trailing spaces are part of the key
                # and must match the exact text stored in the game's binary.
                en = _unescape_newlines(en)
                if not en:
                    continue
                self.table[en] = he
                ns_key = en.replace(' ', '')
                if ns_key:
                    if ns_key not in self._nospace_index:
                        self._nospace_index[ns_key] = he
                    ns_lower = ns_key.lower()
                    if ns_lower not in self._nospace_lower_index:
                        self._nospace_lower_index[ns_lower] = he
                loaded += 1
        return loaded

    def load_extra_mapping(self, path: str, encoding: str = "utf-8") -> int:
        """Load a secondary mapping file (UTF-8, exact-match fallback).

        Literal "\\n" / "\\r" in the file are converted to real newline /
        carriage-return characters in both keys and values (same convention
        as `load_mapping`).

        First-loaded entries take priority on duplicate keys (call order matters
        when loading multiple extra files).  Returns the number of new entries.
        """
        loaded = 0
        with open(path, encoding=encoding) as f:
            for line in f:
                line = line.rstrip("\r\n")
                if " === " not in line:
                    continue
                en, _, he = line.partition(" === ")
                en = _unescape_newlines(en)
                he = _unescape_newlines(he.strip())
                if en and en not in self.extra_table:
                    self.extra_table[en] = he
                    loaded += 1
        return loaded

    # ── Direct lookup helpers ───────────────────────────────────────────────

    def _simple_lookup_no_swap(self, key: str) -> str | None:
        """Steps 1-8: space/case/period lookups — no quote-swap (avoids recursion)."""
        t = self.table
        # 1. Exact match
        if key in t and t[key]:
            return t[key]
        # 2. Period-toggled exact key
        toggled = _toggle_period(key)
        if toggled in t and t[toggled]:
            return t[toggled]
        # 3. lstrip (whitespace)
        stripped = key.lstrip()
        if stripped != key and stripped in t and t[stripped]:
            return t[stripped]
        # 4. lstrip + period-toggled
        toggled_stripped = _toggle_period(stripped)
        if stripped != key and toggled_stripped in t and t[toggled_stripped]:
            return t[toggled_stripped]
        # 4b. Strip leading quote/apostrophe/backtick characters
        quote_stripped = stripped.lstrip("'`\"")
        if quote_stripped != stripped:
            if quote_stripped in t and t[quote_stripped]:
                return t[quote_stripped]
            toggled_qs = _toggle_period(quote_stripped)
            if toggled_qs in t and t[toggled_qs]:
                return t[toggled_qs]
        # 5. Collapse multi-spaces
        normalized = _MULTI_SPACE.sub(' ', key).strip()
        if normalized != key and normalized != stripped and normalized in t and t[normalized]:
            return t[normalized]
        # 6. Collapse multi-spaces + period-toggled
        toggled_normalized = _toggle_period(normalized)
        if (normalized != key and normalized != stripped
                and toggled_normalized in t and t[toggled_normalized]):
            return t[toggled_normalized]
        # 6b. Strip special characters and re-normalise
        clean = strip_special(key)
        if clean != key and clean != normalized:
            if clean in t and t[clean]:
                return t[clean]
            toggled_clean = _toggle_period(clean)
            if toggled_clean in t and t[toggled_clean]:
                return t[toggled_clean]
        # 7. Remove all spaces (case-sensitive)
        nospace = key.replace(' ', '')
        if nospace in self._nospace_index:
            return self._nospace_index[nospace]
        # 8. Remove all spaces + case-insensitive
        nospace_lower = nospace.lower()
        if nospace_lower in self._nospace_lower_index:
            return self._nospace_lower_index[nospace_lower]
        return None

    def _simple_lookup(self, key: str) -> str | None:
        """Steps 1-10: all direct lookups including quote-swap variants."""
        result = self._simple_lookup_no_swap(key)
        if result:
            return result
        # 9. Swap backtick -> single-quote
        if '`' in key:
            result = self._simple_lookup_no_swap(key.replace('`', "'"))
            if result:
                return result
        # 10. Swap single-quote -> backtick
        if "'" in key:
            result = self._simple_lookup_no_swap(key.replace("'", '`'))
            if result:
                return result
        return None

    def _resolve_token(self, token: str) -> str | None:
        """Try to find the Hebrew for a backtick-wrapped token.

        Tries the full token (with backticks), then the inner content (without),
        re-wrapping the result in backticks if found via the inner lookup.
        """
        he = self._simple_lookup(token)
        if he:
            return he
        inner = token[1:-1]  # strip surrounding backticks
        he_inner = self._simple_lookup(inner)
        if he_inner:
            return '`' + he_inner + '`'
        return None

    def _resolve_other(self, other: str) -> tuple[str, str] | None:
        """Try to find the Hebrew for the non-token (plain-text) side."""
        other_stripped = other.strip()
        if not other_stripped:
            return None
        candidates = [other_stripped]
        stripped_punct = _LEADING_SEP_RE.sub('', other_stripped)
        if stripped_punct and stripped_punct != other_stripped:
            candidates.append(stripped_punct)
        for candidate in candidates:
            he = self._simple_lookup(candidate)
            if he:
                return candidate, he
        return None

    def _try_split(self, key: str, token: str, prefix: str, suffix: str) -> str | None:
        """Given a specific (token, prefix, suffix) split, attempt compound translation."""
        he_token = self._resolve_token(token)
        if not he_token:
            return None

        # Case A: backtick first, plain text after
        if suffix.strip():
            result = self._resolve_other(suffix)
            if result:
                chosen_msg, he_other = result
                token_end = key.find(token) + len(token)
                msg_pos = key.find(chosen_msg, token_end)
                connector = key[token_end:msg_pos] if msg_pos >= 0 else ''
                return prefix + he_token + connector + he_other

        # Case B: plain text first, backtick last
        if prefix.strip():
            result = self._resolve_other(prefix)
            if result:
                chosen_msg, he_other = result
                msg_pos = key.find(chosen_msg)
                msg_end = msg_pos + len(chosen_msg) if msg_pos >= 0 else len(prefix)
                token_pos = key.find(token)
                connector = key[msg_end:token_pos] if token_pos >= 0 else ''
                return he_other + connector + he_token

        return None

    def _compound_lookup(self, key: str) -> str | None:
        """Step 10: handle messages composed of a backtick part + a plain-text part."""
        # Shortest-match tokenisation
        parts = _TOKEN_RE.split(key)
        token_indices = [
            i for i, p in enumerate(parts)
            if _TOKEN_RE.fullmatch(p) and p.startswith('`')
        ]
        if token_indices:
            ti = token_indices[0]
            token  = parts[ti]
            prefix = ''.join(parts[:ti])
            suffix = ''.join(parts[ti + 1:])
            result = self._try_split(key, token, prefix, suffix)
            if result:
                return result

        # Greedy tokenisation: first ` to last `
        first_bt = key.find('`')
        last_bt  = key.rfind('`')
        if first_bt >= 0 and last_bt > first_bt:
            greedy_token  = key[first_bt:last_bt + 1]
            greedy_prefix = key[:first_bt]
            greedy_suffix = key[last_bt + 1:]
            if not token_indices or greedy_token != parts[token_indices[0]]:
                result = self._try_split(key, greedy_token, greedy_prefix, greedy_suffix)
                if result:
                    return result

        return None

    def _var_token_lookup(self, key: str) -> str | None:
        """Step 11: handle messages that contain {game_code} tokens mixed with plain text."""
        parts = _TOKEN_RE.split(key)
        curly_indices = {
            i for i, p in enumerate(parts)
            if _TOKEN_RE.fullmatch(p) and p.startswith('{')
        }
        if not curly_indices:
            return None

        text_only = ''.join(p for i, p in enumerate(parts) if i not in curly_indices)
        if not text_only.strip():
            return None

        he = self._simple_lookup(text_only)
        if not he:
            return None

        text_indices = {i for i, p in enumerate(parts) if i not in curly_indices and p}
        if not text_indices:
            return None
        text_start = min(text_indices)
        text_end   = max(text_indices)

        prefix = ''.join(parts[i] for i in sorted(curly_indices) if i < text_start)
        middle = ''.join(parts[i] for i in sorted(curly_indices) if text_start <= i <= text_end)
        suffix = ''.join(parts[i] for i in sorted(curly_indices) if i > text_end)

        return prefix + he + middle + suffix

    # ── Public lookup ───────────────────────────────────────────────────────

    def lookup(self, key: str) -> str | None:
        """Return the Hebrew translation for `key` using the full fallback cascade.

        Returns None if no match is found in either the primary table or the
        extra mapping (after all normalised variants have been tried).
        """
        result = self._simple_lookup(key)
        if result:
            return result
        result = self._compound_lookup(key)
        if result:
            return result
        result = self._var_token_lookup(key)
        if result:
            return result
        # 12. Strip all backticks and retry direct lookups
        if '`' in key:
            stripped = key.replace('`', '')
            if stripped.strip():
                result = self._simple_lookup(stripped)
                if result:
                    return result
        # 13. Fallback against the extra mapping (multiple key variants)
        ex = self.extra_table
        if not ex:
            return None
        if key in ex:
            return ex[key]
        clean_key = strip_special(key)
        if clean_key != key and clean_key in ex:
            return ex[clean_key]
        if '`' in key:
            bt_key = key.replace('`', '')
            if bt_key.strip() and bt_key in ex:
                return ex[bt_key]
            clean_bt = strip_special(bt_key)
            if clean_bt != bt_key and clean_bt in ex:
                return ex[clean_bt]
        return None
