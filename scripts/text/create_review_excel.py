"""
create_review_excel.py — Build a translation review spreadsheet.

For every line-synced  en.<stem>.txt  /  he.<stem>.txt  pair found in
translations/mi2/  this script writes a single Excel workbook with one
sheet per pair.  Each sheet has four columns:

    | Line | English | Hebrew | Is New |

`Line` is the 1-based record index (matches the .txt line number, which
is also what the inject_translation pipeline uses).  Rows where the
Hebrew column equals the English column — i.e. the build_translation
fallback never found a Hebrew for that record — are highlighted in pale
yellow so reviewers can see what still needs work.

`Is New` is "Yes" when the English string is NOT present in
translations/english.txt (the canonical list of previously-known English
strings) and "No" otherwise — useful for reviewers to focus on records
that did not exist in earlier builds.  Empty cells are blank.

Optionally uploads the workbook to Google Drive under a chosen title.
By default the upload aborts if a file with the same title already
exists in the target folder; pass `--override` to replace its contents
in place.

Usage
-----
    # 1. Local-only (writes  translations/mi2/translation_review.xlsx)
    python scripts/text/create_review_excel.py

    # 2. Custom output path
    python scripts/text/create_review_excel.py --out review.xlsx

    # 3. Upload to Google Drive after writing locally
    python scripts/text/create_review_excel.py --upload "MI2 Translation Review"

    # 4. Override an existing Drive file with the same title
    python scripts/text/create_review_excel.py --upload "MI2 Translation Review" --override

    # 5. Place the file inside a specific Drive folder (e.g. a shared review folder)
    python scripts/text/create_review_excel.py --upload "MI2 Translation Review" --folder <FOLDER_ID>

Setup for Google Drive uploads
------------------------------
1.  pip install openpyxl google-api-python-client google-auth-oauthlib
2.  In the Google Cloud Console (https://console.cloud.google.com):
      - create or pick a project
      - enable the "Google Drive API"
      - APIs & Services -> Credentials -> Create OAuth 2.0 Client ID,
        application type "Desktop app"
      - download the JSON and save it as scripts/text/credentials.json
        (or pass another path with --credentials).
3.  The first upload opens a browser asking you to authorise the app;
    the resulting token is cached in scripts/text/.gdrive_token.json
    so subsequent runs are silent.  Both files are gitignored.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable


# ---- openpyxl (mandatory) --------------------------------------------------
try:
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
except ImportError:
    print("ERROR: openpyxl is not installed.  Run:  pip install openpyxl",
          file=sys.stderr)
    sys.exit(2)


# ---- Google Drive client (optional, only needed for --upload) --------------
try:
    from googleapiclient.discovery import build as gdrive_build
    from googleapiclient.http import MediaFileUpload
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    _HAS_GDRIVE = True
except ImportError:
    _HAS_GDRIVE = False


# ---- Defaults --------------------------------------------------------------
THIS_DIR      = Path(__file__).resolve().parent
REPO_ROOT     = THIS_DIR.parent.parent
DEFAULT_SRC   = REPO_ROOT / "translations" / "mi2"
DEFAULT_OUT   = DEFAULT_SRC / "translation_review.xlsx"
DEFAULT_KNOWN = REPO_ROOT / "translations" / "english.txt"
DEFAULT_CRED  = THIS_DIR / "credentials.json"
DEFAULT_TOK   = THIS_DIR / ".gdrive_token.json"

GDRIVE_SCOPES   = ["https://www.googleapis.com/auth/drive.file"]
EXCEL_MIME_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

_EN_NAME_RE = re.compile(r"^en\.(?P<stem>.+)\.txt$", re.IGNORECASE)
_BAD_SHEET_NAME_CHARS = re.compile(r"[\\/?*\[\]:]")

# Excel's XML format forbids most ASCII control bytes in cell values; only
# \t (0x09), \n (0x0A) and \r (0x0D) are allowed.  Everything else needs to
# be stripped before writing or openpyxl raises IllegalCharacterError.
_ILLEGAL_XML_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")

# The MI2 english.txt file embeds game-engine metadata as 4-character escape
# sequences like  \xFF\x0A...  in front of (and sometimes inside) the actual
# message text.  scripts/text/create_mapping.py strips the same noise when it
# builds mapping.txt, and we mirror that logic here so the "Is New" check is
# consistent with how every other tool in the pipeline interprets english.txt.
_NOISE_RE = re.compile(r"\\x[0-9A-Fa-f]{2}")


def _normalize_for_match(s: str) -> str:
    """Lower-level normalisation used for the 'Is New' membership test.
    Mirrors create_mapping.clean() (strip \\xHH escapes, drop '@', expand '^'
    to '...') and additionally collapses runs of whitespace so trivial
    formatting differences (e.g. one-vs-two spaces after '?') do not flag a
    line as "new" when the canonical english.txt has the same wording."""
    s = _NOISE_RE.sub("", s)
    s = s.replace("@", "").replace("^", "...")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


# ---------------------------------------------------------------------------
# File-pair discovery
# ---------------------------------------------------------------------------

def discover_pairs(src_dir: Path) -> list[tuple[str, Path, Path]]:
    """Find every `en.<stem>.txt` in `src_dir` whose matching
    `he.<stem>.txt` also exists.  Returns a list of `(stem, en, he)`
    triples sorted alphabetically by stem.
    """
    pairs: list[tuple[str, Path, Path]] = []
    for en_path in src_dir.glob("en.*.txt"):
        m = _EN_NAME_RE.match(en_path.name)
        if not m:
            continue
        stem    = m.group("stem")
        he_path = src_dir / f"he.{stem}.txt"
        if he_path.exists():
            pairs.append((stem, en_path, he_path))
    return sorted(pairs, key=lambda p: p[0])


# ---------------------------------------------------------------------------
# Excel building
# ---------------------------------------------------------------------------

def _read_lines(path: Path) -> list[str]:
    """Read a UTF-8 text file as a list of records.  Each line == one
    record (the inject_translation pipeline keeps in-message newlines as
    literal `\\n` / `\\r` so this assumption holds).  Stray control
    bytes that Excel's XML format rejects are stripped here so the
    sheet always loads."""
    raw = path.read_text(encoding="utf-8").splitlines()
    return [_ILLEGAL_XML_CHARS_RE.sub("", line) for line in raw]


def _load_known_english(path: Path | None) -> set[str]:
    """Load the canonical list of already-known English strings from a
    plain UTF-8 file (one string per line).  Returns an empty set if
    `path` is None or the file is missing — the caller is responsible
    for warning when that means every row is labelled 'Yes'.

    Each line is normalised with `_normalize_for_match` so the resulting
    keys are directly comparable to similarly-normalised messages from
    the en.*.txt files."""
    if path is None or not path.is_file():
        return set()
    known: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        s = _normalize_for_match(_ILLEGAL_XML_CHARS_RE.sub("", line))
        if s:
            known.add(s)
    return known


def _safe_sheet_name(stem: str) -> str:
    """Excel sheet titles are limited to 31 chars and may not contain
    \\ / ? * [ ] :  — clean the stem accordingly."""
    cleaned = _BAD_SHEET_NAME_CHARS.sub(" ", stem).strip()
    if len(cleaned) > 31:
        cleaned = cleaned[:31]
    return cleaned or "sheet"


def build_workbook(pairs: Iterable[tuple[str, Path, Path]],
                   known_english: set[str]) -> openpyxl.Workbook:
    wb = openpyxl.Workbook()
    # openpyxl creates one default sheet — drop it; we add our own per pair.
    wb.remove(wb.active)

    header_font   = Font(bold=True, color="FFFFFF")
    header_fill   = PatternFill("solid", fgColor="4472C4")   # corporate blue
    header_align  = Alignment(horizontal="center", vertical="center")
    untrans_fill  = PatternFill("solid", fgColor="FFF2CC")   # pale yellow

    en_align     = Alignment(horizontal="left",  vertical="top", wrap_text=True)
    he_align     = Alignment(horizontal="right", vertical="top", wrap_text=True,
                             readingOrder=2)   # 2 = explicit RTL
    centre_align = Alignment(horizontal="center", vertical="top")

    for stem, en_path, he_path in pairs:
        en_lines = _read_lines(en_path)
        he_lines = _read_lines(he_path)
        if len(en_lines) != len(he_lines):
            raise ValueError(
                f"line-count mismatch in pair {stem!r}: "
                f"{en_path.name}={len(en_lines)} lines vs "
                f"{he_path.name}={len(he_lines)} lines"
            )

        ws = wb.create_sheet(title=_safe_sheet_name(stem))
        ws.append(["Line", "English", "Hebrew", "Is New"])
        for col in (1, 2, 3, 4):
            c = ws.cell(row=1, column=col)
            c.font, c.fill, c.alignment = header_font, header_fill, header_align

        n_untranslated = 0
        n_new          = 0
        for i, (en, he) in enumerate(zip(en_lines, he_lines), start=1):
            row = i + 1
            ws.cell(row=row, column=1, value=i ).alignment = centre_align
            ws.cell(row=row, column=2, value=en).alignment = en_align
            ws.cell(row=row, column=3, value=he).alignment = he_align

            en_key = _normalize_for_match(en)
            if not en_key:
                is_new_value = ""
            elif en_key in known_english:
                is_new_value = "No"
            else:
                is_new_value = "Yes"
                n_new += 1
            ws.cell(row=row, column=4, value=is_new_value).alignment = centre_align

            if en and he == en:
                n_untranslated += 1
                for col in (1, 2, 3, 4):
                    ws.cell(row=row, column=col).fill = untrans_fill

        ws.freeze_panes = "A2"
        ws.column_dimensions[get_column_letter(1)].width = 7
        ws.column_dimensions[get_column_letter(2)].width = 70
        ws.column_dimensions[get_column_letter(3)].width = 70
        ws.column_dimensions[get_column_letter(4)].width = 9

        total = len(en_lines)
        translated = total - n_untranslated
        print(f"    sheet {stem!r:>10}  {total} rows  "
              f"({translated} translated, {n_untranslated} untranslated, "
              f"{n_new} new)")

    return wb


# ---------------------------------------------------------------------------
# Google Drive helpers
# ---------------------------------------------------------------------------

def _gdrive_service(credentials_path: Path, token_path: Path):
    if not _HAS_GDRIVE:
        raise RuntimeError(
            "Google Drive client libraries are not installed.\n"
            "Run:  pip install google-api-python-client google-auth-oauthlib"
        )
    if not credentials_path.is_file():
        raise FileNotFoundError(
            f"OAuth credentials file not found at {credentials_path}.\n"
            "Create an OAuth 2.0 Client ID (Desktop app) in the Google Cloud "
            "Console and save the downloaded JSON at that path.\n"
            "See the script's docstring for full setup instructions."
        )

    creds: Credentials | None = None
    if token_path.is_file():
        creds = Credentials.from_authorized_user_file(
            str(token_path), GDRIVE_SCOPES
        )

    if creds is None or not creds.valid:
        if creds is not None and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(credentials_path), GDRIVE_SCOPES
            )
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    return gdrive_build("drive", "v3", credentials=creds, cache_discovery=False)


def _find_existing(service, title: str, folder_id: str | None) -> str | None:
    """Return the file ID of an existing non-trashed file with this title
    in the given folder (or My Drive root), or None if not found.
    Note: with the `drive.file` scope this only sees files this app
    created — files added by other apps are invisible.
    """
    safe_title = title.replace("\\", "\\\\").replace("'", "\\'")
    q = f"name = '{safe_title}' and trashed = false"
    if folder_id:
        q += f" and '{folder_id}' in parents"
    res = service.files().list(
        q=q, fields="files(id, name, parents)", pageSize=10
    ).execute()
    files = res.get("files", [])
    return files[0]["id"] if files else None


def upload_to_drive(local_path: Path, title: str, folder_id: str | None,
                    override: bool, credentials_path: Path,
                    token_path: Path) -> str:
    """Upload `local_path` to Drive with the given title.  Returns the
    file ID.  Raises FileExistsError if the title is taken and
    `override` is False.
    """
    service = _gdrive_service(credentials_path, token_path)

    existing_id = _find_existing(service, title, folder_id)

    if existing_id and not override:
        raise FileExistsError(
            f"a file titled {title!r} already exists in Drive "
            f"(id={existing_id}).  Re-run with --override to replace it."
        )

    media = MediaFileUpload(
        str(local_path), mimetype=EXCEL_MIME_TYPE, resumable=False
    )

    if existing_id:
        # Replace the existing file's contents (keeps its share settings).
        updated = service.files().update(
            fileId=existing_id, body={"name": title}, media_body=media,
            fields="id, name, webViewLink"
        ).execute()
        print(f"  Updated existing Drive file: {updated.get('webViewLink')}")
        return updated["id"]

    meta: dict = {"name": title, "mimeType": EXCEL_MIME_TYPE}
    if folder_id:
        meta["parents"] = [folder_id]
    created = service.files().create(
        body=meta, media_body=media, fields="id, name, webViewLink"
    ).execute()
    print(f"  Uploaded new Drive file:    {created.get('webViewLink')}")
    return created["id"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--src", type=Path, default=DEFAULT_SRC,
        help=f"Folder containing the en.*.txt + he.*.txt pairs "
             f"(default: {DEFAULT_SRC.relative_to(REPO_ROOT)}).",
    )
    p.add_argument(
        "--out", type=Path, default=DEFAULT_OUT,
        help=f"Where to write the workbook "
             f"(default: {DEFAULT_OUT.relative_to(REPO_ROOT)}).",
    )
    p.add_argument(
        "--known-english", type=Path, default=DEFAULT_KNOWN,
        help=f"Path to a UTF-8 text file listing already-known English "
             f"strings (one per line).  Drives the 'Is New' column "
             f"(default: {DEFAULT_KNOWN.relative_to(REPO_ROOT)}).  "
             f"Pass an empty/non-existent path to mark every row 'Yes'.",
    )
    p.add_argument(
        "--upload", metavar="TITLE", default=None,
        help="If given, upload the workbook to Google Drive under TITLE "
             "after writing it locally.",
    )
    p.add_argument(
        "--folder", default=None, metavar="FOLDER_ID",
        help="Drive folder ID to upload into (default: My Drive root).",
    )
    p.add_argument(
        "--override", action="store_true",
        help="Replace the existing Drive file if a file with the same "
             "title already exists.  Without this flag the upload aborts.",
    )
    p.add_argument(
        "--credentials", type=Path, default=DEFAULT_CRED,
        help=f"Path to OAuth client secrets JSON "
             f"(default: {DEFAULT_CRED.relative_to(REPO_ROOT)}).",
    )
    args = p.parse_args()

    src: Path = args.src
    if not src.is_dir():
        print(f"ERROR: source folder not found: {src}", file=sys.stderr)
        return 2

    pairs = discover_pairs(src)
    if not pairs:
        print(f"ERROR: no  en.*.txt + he.*.txt  pairs found in {src}",
              file=sys.stderr)
        return 2

    print(f"  Source: {src}")
    for stem, en_path, he_path in pairs:
        print(f"    pair {stem!r}: {en_path.name} + {he_path.name}")

    known_english = _load_known_english(args.known_english)
    if known_english:
        print(f"  Known English strings ({args.known_english.name}): "
              f"{len(known_english)} unique entries")
    else:
        print(f"  WARNING: no known-English file at {args.known_english} — "
              f"every row will be marked 'Yes' in the 'Is New' column.")

    print("  Building workbook ...")
    wb = build_workbook(pairs, known_english)

    out: Path = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    print(f"  Wrote: {out}  ({out.stat().st_size:,} bytes)")

    if args.upload:
        print(f"  Uploading to Google Drive as {args.upload!r} ...")
        try:
            upload_to_drive(out, args.upload, args.folder, args.override,
                            args.credentials, DEFAULT_TOK)
        except FileExistsError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 3
        except Exception as e:   # network / auth / quota errors
            print(f"ERROR uploading to Drive: {e}", file=sys.stderr)
            return 4

    return 0


if __name__ == "__main__":
    sys.exit(main())
