"""BIS (Indian Standard) text helpers shared by postprocess, build_index and retrieval.

* ``pdf_layout_pages``  -- ``pdftotext -layout`` per page (watermark removed)
* ``linearize_columns`` -- split a two-column layout page at the gutter and return
                          the lines in reading order (left column, then right column)
* ``parse_bis_headings`` / ``parse_bis_captions`` -- clause headings and table captions
  from both layout columns, with bogus-ID filtering (CORPUS-06)
* ``slice_section``     -- the text of one clause (heading to next heading)
* ``fts_normalize``     -- FTS normalisation: join subscripts (k 4 -> k4, K d -> Kd ...),
                          split "0.12h0.34", join "E 250" -> "E250" (CORPUS-11)
* city data for annex lookups (CORPUS-11/17)
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

# --------------------------------------------------------------------------
# where the licensed source PDFs are looked for (repair, section tables, metadata)
# --------------------------------------------------------------------------
def pdf_dirs(root: Optional[Path] = None) -> list[Path]:
    """Folders searched for a document's source PDF when its recorded path is gone: $INDIA_PDF_DIRS
    (os.pathsep-separated), <root>/pdfs, $INDIA_CORPUS_ROOT/pdfs. Nothing is searched outside them."""
    out: list[Path] = []
    for d in (os.environ.get("INDIA_PDF_DIRS") or "").split(os.pathsep):
        if d.strip():
            out.append(Path(d.strip()))
    if root is not None:
        out.append(Path(root) / "pdfs")
    env_root = os.environ.get("INDIA_CORPUS_ROOT")
    if env_root:
        out.append(Path(env_root) / "pdfs")
    out.append(Path(__file__).resolve().parent.parent / "pdfs")
    seen: list[Path] = []
    for d in out:
        if d not in seen:
            seen.append(d)
    return seen


# --------------------------------------------------------------------------
# poppler (pdftotext / pdfinfo / pdftoppm) -- optional
# --------------------------------------------------------------------------
_POPPLER_WARNED: set = set()


@lru_cache(maxsize=16)
def poppler_tool(name: str = "pdftotext") -> Optional[str]:
    """Path of a poppler command-line tool, or None when this PC has none.

    Looked for in $INDIA_POPPLER_BIN (a folder), then on PATH, then in the usual Windows install places
    (winget / scoop / chocolatey / a poppler-windows zip unpacked under Program Files or the user's folder).
    Every caller treats None as "no PDF text layer": steps that re-derive text from the PDF are skipped and
    the corpus text / indexes are used as they are -- nothing is deleted or degraded because of it.
    """
    exe = name + (".exe" if os.name == "nt" else "")
    env = os.environ.get("INDIA_POPPLER_BIN")
    if env and (Path(env) / exe).is_file():
        return str(Path(env) / exe)
    hit = shutil.which(name)
    if hit:
        return hit
    if os.name == "nt":
        bases = [os.environ.get(v) for v in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)", "USERPROFILE", "ProgramData")]
        pats = ["Microsoft/WinGet/Packages/*oppler*/*/Library/bin", "Microsoft/WinGet/Packages/*oppler*/Library/bin",
                "poppler*/Library/bin", "poppler*/bin", "*/poppler*/Library/bin", "scoop/apps/poppler/current/bin",
                "scoop/shims", "chocolatey/bin", "chocolatey/lib/poppler*/tools/*/Library/bin"]
        for b in filter(None, bases):
            for pat in pats:
                for d in sorted(Path(b).glob(pat), reverse=True):
                    if (d / exe).is_file():
                        return str(d / exe)
    return None


def poppler_missing_note(name: str = "pdftotext", what: str = "") -> None:
    """One warning per tool per process (stderr), when poppler_tool(name) is None."""
    if name in _POPPLER_WARNED:
        return
    _POPPLER_WARNED.add(name)
    print(f"[is-corpus] {name} (poppler) not found on this PC -- {what or 'PDF text-layer steps skipped'}; "
          "the corpus text and indexes are used as they are. Poppler is only needed to convert or repair from the "
          "PDFs (Windows: `winget install oschwartz10612.Poppler`, or set INDIA_POPPLER_BIN to its bin folder).",
          file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# watermark (licensee line) -- D11 / CORPUS-14
# --------------------------------------------------------------------------
WATERMARK_LINE_RE = re.compile(
    r"(Free\s+Standard\s+provided\s+by\s+BIS[^\n]*|"
    r"[^\s()]*\([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\)\s*\d{1,3}(?:\.\d{1,3}){3}\.?|"
    r"BSB\s+Edge\s+Private\s+Limited[^\n]*)",
    re.I,
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
BIS_EMAIL_OK = re.compile(r"(@(bis\.gov\.in|bis\.org\.in|standardsbis\.in)|^bis@vsnl\.com)$", re.I)


def strip_watermark(text: str) -> str:
    if not text:
        return text or ""
    out_lines = []
    for ln in text.splitlines():
        if re.search(r"Free\s+Standard\s+provided\s+by\s+BIS|BSB\s+Edge", ln, re.I):
            continue
        if re.search(r"\([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\)\s*\d{1,3}(?:\.\d{1,3}){3}", ln):
            continue
        out_lines.append(ln)
    out = "\n".join(out_lines)
    # residual e-mails (keep BIS addresses)
    out = EMAIL_RE.sub(lambda m: m.group(0) if BIS_EMAIL_OK.search(m.group(0)) else "", out)
    return out


def has_watermark(text: str) -> bool:
    if re.search(r"Free\s+Standard\s+provided\s+by\s+BIS|BSB\s+Edge\s+Private", text or "", re.I):
        return True
    for m in EMAIL_RE.finditer(text or ""):
        if not BIS_EMAIL_OK.search(m.group(0)):
            return True
    return False


# --------------------------------------------------------------------------
# PDF text layer
# --------------------------------------------------------------------------
@lru_cache(maxsize=64)
def pdf_layout_pages(pdf: str) -> tuple[str, ...]:
    """Per-page ``pdftotext -layout`` text, watermark stripped. Index 0 = pdf page 1."""
    p = Path(pdf)
    if not p.is_file():
        return tuple()
    exe = poppler_tool("pdftotext")
    if not exe:
        poppler_missing_note("pdftotext", "clauses are not re-sliced from the PDF text layer")
        return tuple()
    out = subprocess.run(
        [exe, "-layout", str(p), "-"], capture_output=True, text=True, errors="replace"
    ).stdout
    pages = out.split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    return tuple(strip_watermark(unicodedata.normalize("NFKC", pg)) for pg in pages)


# --------------------------------------------------------------------------
# column handling
# --------------------------------------------------------------------------
def find_gutter(lines: list[str]) -> Optional[int]:
    return _find_gutter_cached("\n".join(lines))


@lru_cache(maxsize=4096)
def _find_gutter_cached(joined: str) -> Optional[int]:
    lines = joined.split("\n")
    """Column index of a two-column gutter, or None for single-column pages."""
    body = [ln.rstrip() for ln in lines if ln.strip()]
    if len(body) < 8:
        return None
    width = max(len(ln) for ln in body)
    if width < 70:
        return None
    best, best_score = None, 0.0
    for c in range(int(width * 0.33), int(width * 0.67)):
        two_sided = 0
        blocked = 0
        for ln in body:
            left = ln[:c]
            right = ln[c + 1 :] if len(ln) > c + 1 else ""
            mid = ln[c - 1 : c + 2] if len(ln) > c else ""
            if mid.strip():
                blocked += 1
            elif left.strip() and right.strip():
                two_sided += 1
        score = two_sided - 3 * blocked
        if score > best_score:
            best, best_score = c, score
    if best is None or best_score < 0.25 * len(body):
        return None
    return best


@lru_cache(maxsize=4096)
def linearize_columns(text: str) -> str:
    """Reading-order text for a (possibly) two-column layout page.

    Full-width lines above the first two-column line stay on top (titles);
    everything else is split at the gutter; left column first.
    """
    lines = (text or "").splitlines()
    g = find_gutter(lines)
    if g is None:
        return text or ""
    left: list[str] = []
    right: list[str] = []
    for ln in lines:
        if len(ln) <= g:
            left.append(ln.rstrip())
            right.append("")
            continue
        mid = ln[g - 1 : g + 2]
        if mid.strip():
            # a line crossing the gutter (full-width title, table row): keep whole on left
            left.append(ln.rstrip())
            right.append("")
            continue
        left.append(ln[:g].rstrip())
        right.append(ln[g:].strip())
    # dedent each column
    def dedent(col: list[str]) -> list[str]:
        ind = [len(x) - len(x.lstrip()) for x in col if x.strip()]
        m = min(ind) if ind else 0
        return [x[m:] if len(x) >= m else x.strip() for x in col]

    return "\n".join(dedent(left)) + "\n" + "\n".join(right)


@lru_cache(maxsize=4096)
def looks_two_column(text: str) -> bool:
    return find_gutter((text or "").splitlines()) is not None


# --------------------------------------------------------------------------
# headings and captions
# --------------------------------------------------------------------------
HEAD_RE = re.compile(r"^\s{0,6}(?:#{1,4}\s*)?(?:\*\*)?[‘']?(\d{1,2}(?:\.\d{1,3}){0,5})(?:\*\*)?\.?\s+(\S.*)$")
SECTION_WORD_RE = re.compile(r"^\s{0,6}SECTION\s+(\d{1,2})\s+([A-Z].*)$")
TOC_HINT_RE = re.compile(r"^\s*(CONTENTS|C O N T E N T S)\s*$", re.M)
SYMBOL_WORDS = {
    "DL", "LL", "EL", "WL", "IL", "SL", "CL", "ER", "TL", "EQ", "IS", "BIS", "MPa", "kN", "mm", "Hz", "per",
}


def _title_ok(title: str, top_level: bool) -> bool:
    t = title.strip()
    t = re.sub(r"^\[[^\]]{0,120}\]\s*", "", t)  # "[Amd 2, Nov 2020: ...] The value ..."
    t = re.sub(r"^\*\*|\*\*", "", t).strip()
    if not t:
        return False
    m = re.match(r"([A-Za-z][A-Za-z'’]*(?:-[A-Za-z∆Δ]*)?)", t)
    if not m:
        return False
    w = m.group(1)
    if w in SYMBOL_WORDS:
        return False
    if top_level:
        # BIS top-level headings are upper case: "7 BUILDINGS", "5 GENERAL SPECIFICATIONS"
        return bool(re.match(r"[A-Z][A-Z]{2,}", w)) and not re.search(r"\d{3,}", t[:20])
    if not w[0].isupper():
        return False
    if len(w) == 1 and w not in ("A",):
        return False
    if len(w) <= 3 and w.isupper() and not re.search(r"[A-Za-z]{3,}", t[len(w):len(w) + 40]):
        return False
    if re.match(r"^(kN|mm|MPa|Nos?)\b", t):
        return False
    return True


def _is_toc_page(text: str) -> bool:
    if re.search(r"^\s*(CONTENTS|Contents|C O N T E N T S)\s*$", text[:3000], re.M):
        return True
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 10:
        return False
    toc = sum(
        1
        for ln in lines
        if re.match(r"^\s*(\d+(?:[.,]\d+)*|SECTION\s+\d+|ANNEX\s+[A-Z])\s+[A-Za-z].*?(\s{5,}|\.{4,}\s*)\d{1,3}\s*$", ln)
    )
    return toc >= 0.3 * len(lines)


def parse_bis_headings(
    pages: Iterable[tuple[int, str]],
    *,
    max_top: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Clause headings from layout pages (pdf_page, text), both columns, reading order.

    Filters: TOC pages skipped; top-level ids need an UPPER-CASE title; the top-level
    number never goes backwards (except a single step, for amendment sheets) and may
    advance by at most 2; a sub-clause number may exceed the last sibling by at most 3;
    ids with a first component 0 are kept only on the first 6 pages (foreword 0.x).
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    last_child: dict[str, int] = {}
    cur_top = 0
    pages = list(pages)
    if max_top is None:
        tops = []
        for pno, txt in pages:
            if _is_toc_page(txt):
                continue
            for ln in linearize_columns(txt).splitlines():
                m = HEAD_RE.match(ln)
                if m and "." not in m.group(1) and _title_ok(m.group(2), True):
                    tops.append(int(m.group(1)))
                m2 = SECTION_WORD_RE.match(ln)
                if m2:
                    tops.append(int(m2.group(1)))
        # dotted clause ids are frequent and reliable: a top-level number used by >= 5
        # sub-clauses exists even when its own heading line was not recognised
        from collections import Counter

        firsts: Counter = Counter()
        for pno, txt in pages:
            if _is_toc_page(txt):
                continue
            for ln in linearize_columns(txt).splitlines():
                m = HEAD_RE.match(ln)
                if m and "." in m.group(1) and _title_ok(m.group(2), False):
                    firsts[int(m.group(1).split(".")[0])] += 1
        tops += [k for k, v in firsts.items() if v >= 5 and k <= 40]
        max_top = max(tops) if tops else 30
    for pno, txt in pages:
        if _is_toc_page(txt):
            continue
        lin = linearize_columns(txt)
        cand_lines = lin.splitlines()
        if not looks_two_column(txt):
            cand_lines = [seg for ln0 in cand_lines for seg in re.split(r"\s{6,}", ln0)]
        for idx, ln in enumerate(cand_lines):
            m2 = SECTION_WORD_RE.match(ln)
            if m2:
                sid, title = m2.group(1), m2.group(2).strip()
                if int(sid) <= max_top and sid not in seen:
                    seen.add(sid)
                    cur_top = max(cur_top, int(sid))
                    out.append({"section_id": sid, "title": title[:160], "pdf_page": pno, "line": idx,
                                "source": "pdf_layout"})
                continue
            m = HEAD_RE.match(ln)
            if not m:
                continue
            sid, title = m.group(1), m.group(2).strip()
            parts = [int(x) for x in sid.split(".")]
            top_level = len(parts) == 1
            if not _title_ok(title, top_level):
                continue
            if parts[0] == 0:
                if pno > 6 or top_level:
                    continue
            elif parts[0] > max_top:
                continue
            if sid in seen:
                continue
            if parts[0] != 0:
                if parts[0] > cur_top + 2:
                    continue
                if parts[0] < cur_top - 1:
                    continue
            if not top_level:
                parent = ".".join(str(x) for x in parts[:-1])
                prev = last_child.get(parent, 0)
                if parts[-1] > prev + 3 and not (parent in seen and parts[-1] <= 3):
                    continue
                if len(parts) > 2 and parent not in seen and parts[-1] > 3:
                    continue
            # reject table-row / value lines: title with many numbers
            if len(re.findall(r"\d+(?:\.\d+)?", re.sub(r"^\[[^\]]{0,120}\]\s*", "", title)[:60])) > 3:
                continue
            seen.add(sid)
            if parts[0] != 0:
                cur_top = max(cur_top, parts[0])
            if not top_level:
                parent = ".".join(str(x) for x in parts[:-1])
                last_child[parent] = max(last_child.get(parent, 0), parts[-1])
            title = re.sub(r"\s{2,}", " ", title)
            # run-in heading: cut the title at the dash / first sentence
            t_short = re.split(r"\s+[—–-]\s+|(?<=[a-z)])\s*—", title)[0].strip()
            out.append({"section_id": sid, "title": t_short[:160], "pdf_page": pno, "line": idx,
                        "source": "pdf_layout"})
    return out


CAPTION_RE = re.compile(
    r"^\s*(?:TABLE|Table)\s+(\d{1,2}[A-Z]?(?:\s?\([a-z]\))?)(?![\w.])\s*(.*)$"
)


def parse_bis_captions(pages: Iterable[tuple[int, str]]) -> list[dict[str, Any]]:
    """Real table captions: 'Table N Title' followed within 4 lines by '(Clause ...)',
    or 'Table N (Continued|Concluded)'.  Text mentions ('Table 7 as the minimum...',
    'Table 5 (e)], ...') are rejected."""
    out: list[dict[str, Any]] = []
    for pno, txt in pages:
        lin = linearize_columns(txt).splitlines()
        for i, ln in enumerate(lin):
            m = CAPTION_RE.match(ln)
            if not m:
                continue
            tid, rest = m.group(1), m.group(2).strip()
            cont = bool(re.match(r"^[\(\[]?\s*(Continued|Concluded|Contd)", rest, re.I)) or bool(
                re.search(r"[-—–]\s*Cont(d|inued)\.?\s*$", rest, re.I))
            window = " ".join(x.strip() for x in lin[i + 1 : i + 7])
            has_clause = bool(re.search(r"\(\s*(Clauses?|Clause|Foreword|Section|see)\b", rest + " " + window, re.I))
            title_like = bool(re.match(r"^[—–-]?\s*[A-Z][A-Za-z]", rest)) and not re.match(
                r"^(of|and|as|to|for|shall|may|is|are|in|gives?|with)\b", rest)
            header_marker = bool(re.search(r"(^|\n)\s*(Sl|SI|S1|s\}|Sl\.)\s*(No|$)|\(1\)\s+\(2\)",
                                           "\n".join(lin[i + 1 : i + 10])))
            caps = len(re.findall(r"\b[A-Z][a-z]{2,}", rest)) >= 2 or rest.isupper()
            if not (cont or (title_like and (has_clause or header_marker or caps))):
                continue
            tid = tid.replace(" ", "")
            title_parts = [rest]
            for x in lin[i + 1 : i + 4]:
                xs = x.strip()
                if not xs or re.match(r"^\(\s*(Clauses?|Foreword)", xs, re.I):
                    break
                if len(xs) > 90 or re.match(r"^(Sl|SI)\b", xs):
                    break
                title_parts.append(xs)
            title = re.sub(r"\s{2,}", " ", " ".join(title_parts)).strip()
            clause = None
            mcl = re.search(r"\(\s*Clauses?\s+([^)]*)\)", rest + " " + window, re.I)
            if mcl:
                clause = mcl.group(1).strip()
            out.append({
                "table_id": tid,
                "title": f"Table {tid} {title}".strip()[:200],
                "pdf_page": pno,
                "continued": cont,
                "clause_ref": clause,
                "line": i,
            })
    return out


# --------------------------------------------------------------------------
# clause slicing
# --------------------------------------------------------------------------
@lru_cache(maxsize=20000)
def _heading_line_re(sid: str) -> re.Pattern[str]:
    return re.compile(rf"^\s{{0,6}}(?:[-*•]\s+)?(?:#{{1,4}}\s*)?(?:\*\*)?[‘']?{re.escape(sid)}\.?(?:\*\*)?(?![.\d])\s+\S")


def slice_section(
    page_texts: dict[int, str],
    start_page: int,
    sid: str,
    other_sids: list[str],
    *,
    max_pages: int = 4,
    max_chars: int = 12000,
) -> Optional[str]:
    """Text from the heading of ``sid`` to the next non-descendant heading.

    ``page_texts`` maps pdf page -> served text (layout or markdown). Two-column
    layout pages are linearised first. Returns None when the heading is not found.
    """
    hre = _heading_line_re(sid)
    others = [s for s in other_sids if s and s != sid and not s.startswith(sid + ".")]
    other_res = [_heading_line_re(s) for s in others]
    collected: list[str] = []
    started = False
    for pno in range(start_page, start_page + max_pages):
        txt = page_texts.get(pno)
        if txt is None:
            continue
        lines = linearize_columns(txt).splitlines() if looks_two_column(txt) else txt.splitlines()
        for ln in lines:
            if not started:
                if hre.match(ln):
                    started = True
                    collected.append(ln.strip())
                continue
            if any(r.match(ln) for r in other_res):
                return _tidy("\n".join(collected), max_chars)
            if re.match(r"^\s*(ANNEX|APPENDIX)\s+[A-Z]\b", ln):
                return _tidy("\n".join(collected), max_chars)
            collected.append(ln.rstrip())
        if not started:
            return None
        if sum(len(x) for x in collected) > max_chars:
            break
    return _tidy("\n".join(collected), max_chars) if started else None


def _tidy(s: str, max_chars: int) -> str:
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s[:max_chars]


# --------------------------------------------------------------------------
# FTS normalisation (index + query)
# --------------------------------------------------------------------------
_JOIN_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b([kK])\s+([1-4])\b"), r"\1\2"),            # k 4 -> k4
    (re.compile(r"\bK\s+([dacDAC])\b"), r"K\1"),              # K d -> Kd
    (re.compile(r"\bV\s+([zbBZ])\b"), r"V\1"),                # V z -> Vz, V b -> Vb
    (re.compile(r"\bp\s+([dz])\b"), r"p\1"),                  # p d -> pd
    (re.compile(r"\bC\s+(pe|pi|f|p|t)\b"), r"C\1"),           # C pe -> Cpe
    (re.compile(r"γ\s*m\s*([01])\b"), r"γm\1"),               # γ m0 -> γm0
    (re.compile(r"γ\s+f\b"), r"γf"),
    (re.compile(r"\bA\s+h\b"), r"Ah"),
    (re.compile(r"\bT\s+a\b"), r"Ta"),
    (re.compile(r"\bS\s*a\s*/\s*g\b"), r"Sa/g"),
    (re.compile(r"\b(E|Fe)\s+(165|250|275|300|330|350|410|450|550|600|650)\b"), r"\1\2"),  # E 250 -> E250
    (re.compile(r"\bf\s+([ycu])\b"), r"f\1"),                 # f y -> fy
    (re.compile(r"\bM\s+([dp])\b"), r"M\1"),
    (re.compile(r"(\d(?:\.\d+)?)([a-z]{1,2})(\d+\.\d+)"), r"\1 \2 \3"),   # 0.12h0.34 -> 0.12 h 0.34
    (re.compile(r"(?<![A-Za-z0-9])([a-z])(\d+\.\d+)\b"), r"\1 \2"),        # h0.34 -> h 0.34
]
_SPLIT_ALNUM = re.compile(r"(\d(?:\.\d+)?)([A-Za-z]{1,2})(\d+(?:\.\d+)?)")


def fts_normalize(text: str) -> str:
    s = unicodedata.normalize("NFKC", text or "")
    for rx, rep in _JOIN_RULES:
        s = rx.sub(rep, s)
    return s


def fts_extra_tokens(text: str) -> str:
    """Extra alias tokens: split forms of glued formula tokens ("0.12h0.34" ->
    "0.12 h 0.34") and E-grade joins, appended to the FTS aliases column."""
    s = unicodedata.normalize("NFKC", text or "")
    extra: list[str] = []
    for m in _SPLIT_ALNUM.finditer(s):
        extra.append(f"{m.group(1)} {m.group(2)} {m.group(3)}")
    for m in re.finditer(r"\b(E|Fe)\s+(\d{3})\b", s):
        extra.append(m.group(1) + m.group(2))
    for m in re.finditer(r"\b[kK]\s*([1-4])\b", s):
        extra.append("k" + m.group(1))
    return " ".join(dict.fromkeys(extra))


# --------------------------------------------------------------------------
# cities (annex lookups)
# --------------------------------------------------------------------------
CITY_GROUPS: list[list[str]] = [
    ["Bengaluru", "Bangalore"],
    ["Visakhapatnam", "Vishakhapatnam", "Vishakapatnam", "Vizag", "Visakapatnam"],
    ["Kochi", "Cochin"],
    ["Gurugram", "Gurgaon"],
    ["Pune", "Poona"],
    ["Mumbai", "Bombay"],
    ["Chennai", "Madras"],
    ["Kolkata", "Calcutta"],
    ["Thiruvananthapuram", "Trivandrum"],
    ["Mysuru", "Mysore"],
    ["Vadodara", "Baroda"],
    ["Kozhikode", "Calicut"],
    ["Puducherry", "Pondicherry"],
    ["Prayagraj", "Allahabad"],
    ["Varanasi", "Banaras", "Benaras"],
    ["Odisha", "Orissa"],
    ["Mangaluru", "Mangalore"],
    ["Belagavi", "Belgaum"],
    ["Kanpur", "Cawnpore"],
    ["Delhi", "New Delhi"],
]
# towns commonly asked for (the annex tables are the authority; this list only
# decides whether a query is a town lookup)
KNOWN_TOWNS = [
    "Agra", "Ahmedabad", "Ajmer", "Allahabad", "Almora", "Ambala", "Amritsar", "Asansol", "Aurangabad",
    "Bahraich", "Barauni", "Bareilly", "Belgaum", "Bhatinda", "Bhilai", "Bhopal", "Bhubaneswar", "Bhuj",
    "Bijapur", "Bikaner", "Bokaro", "Bulandshahr", "Burdwan", "Calicut", "Chandigarh", "Chitradurga",
    "Coimbatore", "Cuddalore", "Cuttack", "Darbhanga", "Darjeeling", "Dehradun", "Dharwad", "Dhanbad",
    "Durgapur", "Faridabad", "Gangtok", "Ghaziabad", "Goa", "Gorakhpur", "Gulbarga", "Guwahati", "Gwalior",
    "Hubli", "Hyderabad", "Imphal", "Indore", "Jabalpur", "Jaipur", "Jammu", "Jamnagar", "Jamshedpur",
    "Jhansi", "Jodhpur", "Jorhat", "Kakinada", "Kandla", "Kanpur", "Kohima", "Kurnool", "Lucknow", "Ludhiana",
    "Madurai", "Mandi", "Mangalore", "Meerut", "Moradabad", "Mysore", "Nagpur", "Nagarjunasagar", "Nainital",
    "Nasik", "Nashik", "Nellore", "Noida", "Panjim", "Patiala", "Patna", "Pondicherry", "Port Blair", "Raipur",
    "Rajkot", "Ranchi", "Roorkee", "Rourkela", "Sadiya", "Salem", "Shillong", "Shimla", "Silchar", "Siliguri",
    "Srinagar", "Surat", "Tezpur", "Thane", "Tiruchirappalli", "Trichy", "Tirupati", "Udaipur", "Ujjain",
    "Vijayawada", "Varanasi", "Vellore", "Warangal", "Bhiwandi", "Howrah", "Navi Mumbai", "Greater Noida",
    "Bhavnagar", "Kota", "Aligarh", "Kolhapur", "Solapur", "Tiruppur", "Guntur", "Amravati", "Jalandhar",
    "Dhule", "Nanded", "Sangli", "Kollam", "Thrissur", "Kannur", "Alappuzha", "Leh", "Kargil", "Port Louis",
]


def city_variants(name: str) -> list[str]:
    n = name.strip().casefold()
    for g in CITY_GROUPS:
        if n in [x.casefold() for x in g]:
            return list(g)
    return [name.strip()]


def all_city_names() -> list[str]:
    names = {x for g in CITY_GROUPS for x in g}
    names.update(KNOWN_TOWNS)
    return sorted(names, key=len, reverse=True)


def find_city_in_query(query: str) -> Optional[str]:
    q = unicodedata.normalize("NFKC", query or "")
    for name in all_city_names():
        if re.search(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])", q, re.I):
            return name
    return None
