#!/usr/bin/env python3
"""Strip the BIS licence watermark (licensee name / email / IP line) from corpus text artefacts.

Decision D11 (Steltic India review): the BIS licence watermark ("Free Standard provided by BIS via
BSB Edge Private Limited to <licensee> - <user>(<email>) <IPv4>.") must not appear in any corpus
text artefact.

Usage:
    python3 scripts/strip_watermark.py                    # default: documents/standards/** (dry run)
    python3 scripts/strip_watermark.py --apply            # rewrite files in place
    python3 scripts/strip_watermark.py --apply --paths indexes cache   # other trees (relative to --root)
    python3 scripts/strip_watermark.py --check            # exit 1 if any watermark remains (CI grep)

What is removed:
  * the watermark phrase itself (any of its fragments: the "Free Standard/amendment provided by BIS via
    BSB Edge ..." sentence, and the "<user>(<email>) <IPv4>" tail), wherever it occurs - also when it is
    glued onto a line of standard text by the PDF converter (only the phrase is cut, the text is kept);
  * any other e-mail address that is not a BIS institutional address (bis.gov.in / bis.org.in /
    vsnl.com BIS office addresses printed on back covers are kept);
  * an IPv4 address that directly follows such an address.
Lines that become empty (or only markdown/list punctuation) after the removal are dropped in text files.
JSON / JSONL files are edited as raw text (the watermark never contains a quote character, so only the
inside of string literals changes and formatting is preserved) and are re-parsed afterwards to
guarantee they are still valid; a file that would become invalid is left untouched and reported.

It prints a before/after count report (files and occurrences per artefact type).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

SEP = r"(?:\s|\\[nrt])*"  # whitespace, or an escaped newline inside a JSON string literal
EMAIL = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
IPV4 = r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d])\.?"
KEEP_EMAIL = re.compile(r"@(?:[\w.-]*\bbis\.(?:gov|org)\.in|vsnl\.com)\b", re.I)

PHRASE = re.compile(
    r"(?:Free" + SEP + r"(?:Standard|amendment)" + SEP + r"provided" + SEP + r"by" + SEP + r"BIS" + SEP
    + r"via" + SEP + r"BSB" + SEP + r"Edge" + SEP + r"Private" + SEP + r"Limited" + SEP + r"to"
    + r"(?:" + SEP + r"[A-Z][a-z]+" + SEP + r"[A-Z][a-z]+)?" + SEP + r"-?" + SEP + r")"
    + r"(?:[\w.-]*" + SEP + r"\(" + SEP + EMAIL + SEP + r"\)" + SEP + r"(?:" + IPV4 + r")?)?",
    re.I,
)
TAIL = re.compile(r"[\w.-]*\(" + SEP + EMAIL + SEP + r"\)" + SEP + r"(?:" + IPV4 + r")?")
BARE_EMAIL = re.compile(EMAIL + r"(?:" + SEP + IPV4 + r")?")
FRAGMENT = re.compile(r"BSB\s*Edge|Free\s+Standard\s+provided\s+by\s+BIS", re.I)
EMPTYISH = re.compile(r"^[\s#>*\-'\"/|]*$")

TEXT_EXT = {".md", ".txt", ".csv", ".tsv", ".html", ".htm", ".xml", ".yaml", ".yml", ".bak"}
JSON_EXT = {".json", ".jsonl"}


def kind_of(path: Path) -> str:
    parts = path.parts
    for k in ("pages_recovered", "pages_search", "pages", "furniture", "chunks"):
        if k in parts:
            return f"markdown/{k}"
    for k in ("structured", "tables", "equations", "indexes", "markdown"):
        if k in parts:
            return k
    return "other"


def _strip_email(m: re.Match) -> str:
    return m.group(0) if KEEP_EMAIL.search(m.group(0)) else ""


def clean_string(s: str) -> tuple[str, int]:
    n = 0
    s, k = PHRASE.subn("", s); n += k
    n += sum(1 for m in TAIL.finditer(s) if not KEEP_EMAIL.search(m.group(0)))
    s = TAIL.sub(_strip_email, s)
    n += sum(1 for m in BARE_EMAIL.finditer(s) if not KEEP_EMAIL.search(m.group(0)))
    s = BARE_EMAIL.sub(_strip_email, s)
    return s, n


TRIGGER = re.compile(r"@|BSB|Free\s+(?:Standard|amendment)", re.I)


def count(text: str) -> int:
    if not TRIGGER.search(text):
        return 0
    text = "\n".join(l for l in text.split("\n") if TRIGGER.search(l))
    # occurrences = watermark sentences + licensee e-mail addresses (each counted once)
    c = len(FRAGMENT.findall(text))
    c += sum(1 for m in re.finditer(EMAIL, text) if not KEEP_EMAIL.search(m.group(0)))
    return c


def clean_text_file(text: str) -> str:
    out = []
    for line in text.split("\n"):
        if not TRIGGER.search(line):
            out.append(line)
            continue
        new, n = clean_string(line)
        if n and EMPTYISH.match(new):
            continue  # the whole line was the watermark
        out.append(new.rstrip() if n else new)
    return "\n".join(out)


def clean_json_file(text: str) -> str:
    # The watermark contains no quote or backslash-free-breaking characters, so it can be cut from the raw
    # JSON text line by line without touching the structure (formatting preserved).
    out = []
    for line in text.split("\n"):
        if TRIGGER.search(line):
            new, n = clean_string(line)
            if n:
                new = re.sub(r"(?:\\n){3,}", r"\\n\\n", new)
                new = re.sub(r'"(?:\s|\\n)+"', '""', new)
            line = new
        out.append(line)
    return "\n".join(out)


def iter_files(root: Path, rels: list[str]):
    for rel in rels:
        base = (root / rel)
        if base.is_file():
            yield base
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file() and not p.is_symlink():
                yield p


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--paths", nargs="*", default=["documents/standards"])
    ap.add_argument("--apply", action="store_true", help="rewrite files (default: dry run)")
    ap.add_argument("--check", action="store_true", help="exit 1 if any watermark remains")
    a = ap.parse_args()
    root = Path(a.root)

    before_files, before_occ, after_files, after_occ = Counter(), Counter(), Counter(), Counter()
    changed = 0
    bad_json = []
    for p in iter_files(root, a.paths):
        ext = p.suffix.lower()
        if ext not in TEXT_EXT | JSON_EXT and not p.name.endswith(".search.md.bak_ocr_2026-09-19"):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        c0 = count(text)
        k = kind_of(p.relative_to(root)) + f" ({ext or p.name})"
        if not c0:
            continue
        before_files[k] += 1
        before_occ[k] += c0
        new = clean_json_file(text) if ext in JSON_EXT else clean_text_file(text)
        if ext == ".json":
            try:
                json.loads(new)
            except Exception as e:  # pragma: no cover
                bad_json.append((str(p), str(e)))
                continue
        elif ext == ".jsonl":
            try:
                for ln in new.splitlines():
                    if ln.strip():
                        json.loads(ln)
            except Exception as e:  # pragma: no cover
                bad_json.append((str(p), str(e)))
                continue
        c1 = count(new)
        if c1:
            after_files[k] += 1
            after_occ[k] += c1
        if a.apply and new != text:
            p.write_text(new, encoding="utf-8")
            changed += 1

    print(f"{'artefact type':45s} {'files before':>12s} {'occ before':>10s} {'files after':>11s} {'occ after':>9s}")
    for k in sorted(before_files):
        print(f"{k:45s} {before_files[k]:12d} {before_occ[k]:10d} {after_files[k]:11d} {after_occ[k]:9d}")
    print(f"{'TOTAL':45s} {sum(before_files.values()):12d} {sum(before_occ.values()):10d} "
          f"{sum(after_files.values()):11d} {sum(after_occ.values()):9d}")
    print(f"mode={'apply' if a.apply else 'dry-run'}; files rewritten: {changed}; json refused: {len(bad_json)}")
    for f, e in bad_json:
        print("  JSON-INVALID-AFTER-STRIP (left untouched):", f, e)
    if a.check:
        return 1 if (sum(before_occ.values()) if not a.apply else sum(after_occ.values())) else 0
    return 0


if __name__ == "__main__":
    # Windows: a piped stdout / stderr is cp1252, which cannot print the IS symbols (Ω, →, ≤ ...) -- write UTF-8
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
