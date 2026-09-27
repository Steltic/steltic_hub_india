#!/usr/bin/env python3
"""Build-time corpus fixes (2026-09-26, K02-K05). Imported by build_index.py / build_section_tables.py.

* apply_recovered_tables  (K02) a *_recovered.md transcription REPLACES the converted grid in the
  table record: clean caption as the title, no Docling `markdown_excerpt`, and the FTS row is built
  from the transcription (build_index), so no search path serves the garbled grid any more.
* refresh_structured_rows (K03) structured_row records regenerated from structured/sections.csv on
  every build (the per-doc repair that used to do this needs the PDFs); IS 811 rows carry the
  engine label (CLR100X50X15X2 ...) as an exact key; quarantined rows say so in their title.
* is811_label / plate_area_issue / apply_section_checks (K03) engine labels for IS 811 and the
  plate-area consistency check for IS 808 I / channel rows.
* table1_records          (K04) IS 875 (Part 1) Table 1, one record per material.
* annex_sections / amendment_sections (K05) annex clause ids (D-1, E-1.1, "Annex D") and amendment
  sheet clause ids as exact sections.
* figure_records          (K07, 2026-09-26) figure transcriptions (tables/*_recovered.md entries of
  kind "figure_transcription" in recovered_index.json) become table records with table_id "Fig. N"
  (or "<clause>-shape"); figure_id() normalises "Figure 10" / "FIG. 10" / "Fig 10" to "Fig. 10".
"""
from __future__ import annotations

import csv
import json
import math
import re
from pathlib import Path
from typing import Any, Optional

# ----------------------------------------------------------------------------------------------
# K02: recovered transcriptions replace the converted grid
# ----------------------------------------------------------------------------------------------
_BOLD_CAPTION_RE = re.compile(r"\*\*((?:Table|TABLE)\s+[^*]+?)\*\*")


def _recovered_entries(doc_dir: Path) -> list[dict[str, Any]]:
    p = doc_dir / "tables" / "recovered_index.json"
    if not p.is_file():
        return []
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(d, dict):
        d = d.get("tables") if isinstance(d.get("tables"), list) else list(d.values())
    return [x for x in (d or []) if isinstance(x, dict) and x.get("table_id") and x.get("pdf_page") is not None]


def _caption(title: str) -> str:
    t = re.sub(r"\s+", " ", title or "").strip()
    t = re.sub(r"^TABLE\b", "Table", t)
    return t


def recovered_caption(md_text: str) -> Optional[str]:
    m = _BOLD_CAPTION_RE.search(md_text or "")
    return _caption(m.group(1)) if m else None


def apply_recovered_tables(root: Path, stem: str, tbls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    doc_dir = root / "documents" / "standards" / stem
    entries = _recovered_entries(doc_dir)
    by_key = {(str(e["table_id"]).replace(" ", ""), int(e["pdf_page"])): e for e in entries}
    by_md = {}
    for e in entries:
        mdp = e.get("md") or e.get("path") or e.get("markdown_path")
        if mdp:
            by_md[Path(mdp).name] = e
    for t in tbls:
        if t.get("structured_row"):
            continue
        key = (str(t.get("table_id") or "").replace(" ", ""), int(t.get("pdf_page") or 0))
        e = by_key.get(key)
        if e is not None:
            mdp = e.get("md") or e.get("path") or e.get("markdown_path")
            if mdp and (doc_dir / mdp).is_file():
                t["md"] = f"documents/standards/{stem}/{mdp}"
                if e.get("csv"):
                    t["csv"] = f"documents/standards/{stem}/{e['csv']}"
                t["num_rows"] = e.get("num_rows") or t.get("num_rows")
                t["provenance"] = e.get("provenance") or e.get("method") or "recovered"
        md = str(t.get("md") or "")
        if not _is_recovered_table_md(md):
            continue                       # (pages_recovered/page_NNN.md is a page, not a table transcription)
        mdname = Path(md).name
        e = by_md.get(mdname) or e
        mdfile = doc_dir / "tables" / mdname
        text = mdfile.read_text(encoding="utf-8") if mdfile.is_file() else ""
        cap = (_caption(e["title"]) if e is not None and e.get("title") else None) or recovered_caption(text)
        if cap:
            if t.get("title") and t.get("title") != cap:
                t["converted_title"] = t.get("title")
            t["title"] = cap
        if t.get("markdown_excerpt"):
            t["markdown_excerpt"] = None
        t["docling_grid_superseded"] = True
        t["caption_verified"] = True
        t.pop("superseded", None)
        t.pop("superseded_by", None)
    return tbls


# ----------------------------------------------------------------------------------------------
# K07: figure transcriptions (digitised graphs / maps / printed-coefficient sketches) as table records
# ----------------------------------------------------------------------------------------------
FIG_KIND = "figure_transcription"


def figure_entries(doc_dir: Path) -> list[dict[str, Any]]:
    return [e for e in _recovered_entries(doc_dir) if e.get("kind") == FIG_KIND]


def figure_records(root: Path, stem: str, tbls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One table record per `kind: figure_transcription` entry of tables/recovered_index.json.

    Figures have no caption-verified table record of their own (the converted page holds only
    `<!-- image -->` and the caption), so the record is created here; apply_recovered_tables then
    gives it the transcription as its text and FTS row. Idempotent: records from a previous build
    (persisted in indexes/tables.json) are dropped and rebuilt."""
    doc_dir = root / "documents" / "standards" / stem
    ents = figure_entries(doc_dir)
    out = [t for t in tbls if not t.get("figure_transcription")]
    for k, e in enumerate(ents):
        mdp = e.get("md") or e.get("path")
        if not mdp or not (doc_dir / mdp).is_file():
            continue
        pno = int(e["pdf_page"])
        rec = {
            "doc": stem,
            "table_id": str(e["table_id"]),
            "title": _caption(e.get("title") or str(e["table_id"])),
            "section": e.get("section_id"),
            "clause_ref": e.get("section_id"),
            "part": "standard",
            "pdf_pages": [pno],
            "pdf_page": pno,
            "printed_label": e.get("printed_page"),
            "printed_label_qualified": None,
            "num_rows": e.get("num_rows"),
            "num_cols": e.get("num_cols"),
            "md": f"documents/standards/{stem}/{mdp}",
            "csv": f"documents/standards/{stem}/{e['csv']}" if e.get("csv") else None,
            "json": None,
            "markdown_excerpt": None,
            "index": 30000 + k,
            "caption_verified": True,
            "figure_transcription": True,
            "figure_kind": e.get("figure_kind"),
            "reading_uncertainty": e.get("uncertainty"),
            "public_copy_pdf_page": e.get("public_copy_pdf_page"),
            "verify": True,
            "provenance": e.get("provenance") or e.get("method") or FIG_KIND,
            "source": e.get("source") or "figure transcription (tables/recovered_index.json)",
        }
        out.append(rec)
    return out


FIG_ID_RE = re.compile(r"(?i)^\s*fig(?:ure)?\s*\.?\s*(\d+[A-Za-z]?)\s*(\(\s*[a-z]\s*\))?\s*\.?\s*$")


def figure_id(q: str) -> Optional[str]:
    """'Figure 10', 'Fig 10', 'FIG. 10', 'fig.4(a)' -> 'Fig. 10' / 'Fig. 4' (panel dropped); else None."""
    m = FIG_ID_RE.match(q or "")
    return f"Fig. {m.group(1)}" if m else None


def _is_recovered_table_md(md: str) -> bool:
    return "/tables/" in md.replace("\\", "/") and Path(md).name.endswith("_recovered.md")


def recovered_md_path(root: Path, t: dict[str, Any]) -> Optional[Path]:
    md = str(t.get("md") or "")
    if not _is_recovered_table_md(md):
        return None
    # a path recorded on another machine (a corpus built elsewhere, a fixed corpus zip): keep the
    # part from documents/ on, relative to this workspace
    md = re.sub(r"^.*?(?=documents/standards/)", "", md.replace("\\", "/"))
    p = Path(md) if Path(md).is_absolute() else root / md
    return p if p.is_file() else None


# ----------------------------------------------------------------------------------------------
# K03: section data
# ----------------------------------------------------------------------------------------------
IS811_PREFIX = {"1": "EA", "2": "UA", "3": "CWS", "4": "CWR", "5": "CLS", "6": "CLR", "7": "HS", "8": "HRH",
                "9": "HRB", "10": "LZ"}


def _dim(s: str) -> str:
    s = s.strip()
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def is811_label(designation: str, table: Any) -> str:
    """The engine label of an IS 811 row (steltic_CFS_india steel_engine/is811_shapes.csv):
    table prefix (EA, UA, CWS, CWR, CLS, CLR, HS, HRH, HRB, LZ) + dimensions joined by X, trailing
    zeros dropped: "100 x 50 x 15 x 2.00", Table 6 -> CLR100X50X15X2."""
    pfx = IS811_PREFIX.get(str(table).strip())
    if not pfx:
        return ""
    dims = [_dim(x) for x in re.split(r"\s*[x×X]\s*", str(designation).strip()) if x.strip()]
    return pfx + "X".join(dims)


def _f(x: Any) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


PLATE_AREA_TOL = 0.05


def plate_area(row: dict[str, Any]) -> Optional[float]:
    """Area of an IS 808 I or channel section from its plates and root fillets (mm2):
    2·B·tf + (D − 2tf)·tw + k·(1 − π/4)·R1², k = 4 (I) or 2 (channel). None for angles / missing data."""
    t = str(row.get("type") or "").lower()
    if "angle" in t:
        return None
    D, B, tw, tf = (_f(row.get(k)) for k in ("D", "B", "tw", "tf"))
    if None in (D, B, tw, tf):
        return None
    R1 = _f(row.get("R1")) or 0.0
    k = 2 if "channel" in t else 4
    return 2 * B * tf + (D - 2 * tf) * tw + k * (1 - math.pi / 4) * R1 ** 2


def plate_area_issue(row: dict[str, Any], tol: float = PLATE_AREA_TOL) -> Optional[str]:
    A = _f(row.get("A_mm2"))
    Ap = plate_area(row)
    if not A or not Ap:
        return None
    ratio = A / Ap
    if abs(ratio - 1.0) <= tol:
        return None
    return (f"plate-area check: printed A {A:g} mm2 vs 2·B·tf + (D − 2tf)·tw + root fillets = {Ap:.0f} mm2 "
            f"(ratio {ratio:.2f}, tolerance ±{tol:.0%}) — the dimensions and the area/mass/inertia of this row "
            "do not belong to the same section")


def apply_section_checks(root: Path) -> dict[str, Any]:
    """Idempotent post-pass over structured/sections.csv (run by build_section_tables.py and usable
    on its own): IS 811 engine labels; IS 808 plate-area check -> QUARANTINED rows (no value is
    changed or invented), listed in sections_check.json."""
    std = root / "documents" / "standards"
    out: dict[str, Any] = {}
    p = std / "IS_811_1987" / "structured" / "sections.csv"
    if p.is_file():
        with p.open(encoding="utf-8") as fh:
            rd = csv.DictReader(fh)
            cols = list(rd.fieldnames or [])
            rows = list(rd)
        if "label" not in cols:
            cols.append("label")
        for r in rows:
            r["label"] = is811_label(r.get("designation") or "", r.get("table"))
        _write(p, rows, cols)
        out["IS_811_1987"] = {"labels": sum(1 for r in rows if r["label"])}
    p = std / "IS_808_2021" / "structured" / "sections.csv"
    if p.is_file():
        with p.open(encoding="utf-8") as fh:
            rd = csv.DictReader(fh)
            cols = list(rd.fieldnames or [])
            rows = list(rd)
        q = []
        for r in rows:
            issue = plate_area_issue(r)
            if issue and not str(r.get("check") or "").startswith("QUARANTINED"):
                prev = str(r.get("check") or "")
                r["check"] = (f"QUARANTINED: {issue}; do not use this row -- correct it from the PDF page "
                              f"{r.get('pdf_page')} (was: {prev})")
            if str(r.get("check") or "").startswith("QUARANTINED"):
                q.append(r)
        _write(p, rows, cols)
        cj = std / "IS_808_2021" / "structured" / "sections_check.json"
        rep = json.loads(cj.read_text(encoding="utf-8")) if cj.is_file() else {"stem": "IS_808_2021"}
        unresolved = [u for u in (rep.get("unresolved") or []) if u.get("designation") not in
                      {r["designation"] for r in q}]
        unresolved += [{"designation": r["designation"], "pdf_page": _int(r.get("pdf_page")), "check": r["check"]}
                       for r in q]
        rep["unresolved"] = unresolved
        rep["fail"] = sum(1 for r in rows if not str(r.get("check") or "").startswith("PASS"))
        rep["pass"] = len(rows) - rep["fail"]
        rep["rows"] = len(rows)
        rep["quarantined"] = [r["designation"] for r in q]
        rep["plate_area_check"] = (f"A vs 2·B·tf + (D − 2tf)·tw + k(1 − π/4)R1² (k = 4 I, 2 channel), "
                                   f"tolerance ±{PLATE_AREA_TOL:.0%}; angles not checked")
        cj.write_text(json.dumps(rep, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        out["IS_808_2021"] = {"quarantined": [r["designation"] for r in q]}
    return out


def _int(x: Any) -> Any:
    try:
        return int(x)
    except (TypeError, ValueError):
        return x


def _write(p: Path, rows: list[dict[str, Any]], cols: list[str]) -> None:
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})


def refresh_structured_rows(root: Path, stem: str, tbls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Regenerate the structured_row records of `stem` from structured/sections.csv (same shape as
    postprocess.repair_bis_doc_indexes writes them), so CSV edits (labels, quarantines) reach the index
    without the PDF-dependent repair step."""
    sc = root / "documents" / "standards" / stem / "structured" / "sections.csv"
    if not sc.is_file():
        return tbls
    old = [t for t in tbls if t.get("structured_row")]
    if not old:
        return tbls
    section_of = {(str(t.get("table_id")), t.get("pdf_page")): t.get("section") for t in old}
    with sc.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    new = []
    std_no = stem.split("_")[1]
    for j, row in enumerate(rows):
        desig = row.get("designation") or ""
        tid = row.get("table_id") or desig
        label = row.get("label") or ""
        cols = [k for k in row.keys() if row.get(k) not in (None, "")]
        md = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n| " + " | ".join(
            str(row[k]) for k in cols) + " |"
        ascii_d = desig.replace("×", "x").replace("∠", "L")
        alt = re.sub(r"\s+", "", ascii_d)
        title = f"{desig} section properties ({ascii_d}; {alt}) IS {std_no} Table {row.get('table') or '1'}"
        if label:
            title += f" [engine label {label}]"
        chk = str(row.get("check") or "")
        if chk.startswith("QUARANTINED"):
            title += " [QUARANTINED -- inconsistent row, do not use]"
        pno = int(row["pdf_page"]) if row.get("pdf_page") else None
        rec = {
            "doc": stem,
            "table_id": tid,
            "title": title,
            "section": section_of.get((str(tid), pno)),
            "part": "standard",
            "pdf_pages": [pno] if pno else [],
            "pdf_page": pno,
            "printed_label": None,
            "printed_label_qualified": None,
            "num_rows": 1,
            "num_cols": len(cols),
            "md": None,
            "csv": f"documents/standards/{stem}/structured/sections.csv",
            "json": None,
            "markdown_excerpt": md,
            "index": 10000 + j,
            "structured_row": True,
            "caption_verified": True,
            "check": row.get("check"),
            "source": "structured/sections.csv (PDF text layer, consistency-checked)",
        }
        if label:
            rec["label"] = label
        if chk.startswith("QUARANTINED"):
            rec["quarantined"] = True
        new.append(rec)
    return [t for t in tbls if not t.get("structured_row")] + new


# ----------------------------------------------------------------------------------------------
# K04: IS 875 (Part 1) Table 1, one record per material
# ----------------------------------------------------------------------------------------------
def table1_records(root: Path, stem: str = "IS_875_Part_1_2026") -> list[dict[str, Any]]:
    p = root / "documents" / "standards" / stem / "structured" / "table1_unit_weights.csv"
    if not p.is_file():
        return []
    with p.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for r in rows:
        if not (r.get("weight_kN") or r.get("note") == "text row"):
            continue
        path = " — ".join(x for x in (r.get("group"), r.get("sub_heading"), r.get("material")) if x)
        size = f", nominal size/thickness {r['nominal_size']} mm" if r.get("nominal_size") else ""
        val = f": {r['weight_kN']} {r['unit']}" if r.get("weight_kN") else ""
        title = f"IS 875 (Part 1) Table 1 unit weight — Sl {r.get('sl_no')} {path}{size}{val}"
        excerpt = (f"| Sl No. | Group | Sub-heading above | Material | Nominal size (mm) | Weight (kN) | per |\n"
                   f"|---|---|---|---|---|---|---|\n| {r.get('sl_no')} | {r.get('group')} | {r.get('sub_heading')} | "
                   f"{r.get('material')} | {r.get('nominal_size')} | {r.get('weight_kN')} | {r.get('per')} |")
        out.append({
            "doc": stem,
            "table_id": "1",
            "row_of_table": "1",
            "title": title,
            "section": "3.2",
            "part": "standard",
            "pdf_pages": [int(r["pdf_page"])],
            "pdf_page": int(r["pdf_page"]),
            "printed_label": None,
            "printed_label_qualified": None,
            "num_rows": 1,
            "num_cols": 7,
            "md": None,
            "csv": f"documents/standards/{stem}/structured/table1_unit_weights.csv",
            "json": None,
            "markdown_excerpt": excerpt,
            "index": 20000 + int(r["row"]),
            "structured_row": True,
            "caption_verified": True,
            "check": "as printed (converted grid, duplicated cells collapsed)",
            "source": "structured/table1_unit_weights.csv (scripts/build_is875_1_table1.py from the served text)",
        })
    return out


# ----------------------------------------------------------------------------------------------
# K05: annex clause ids and amendment-sheet clause ids
# ----------------------------------------------------------------------------------------------
_ANNEX_RE = re.compile(r"^\s*(?:#{1,4}\s*)?ANNEX\s*([A-H])\b\s*(.*)$")
_ANNEX_ID_RE = re.compile(r"^\s*(?:[-*•]\s+)?(?:#{1,4}\s*)?(?:\*\*)?([A-H]-\d+(?:\.\d+)*)\.?(?:\*\*)?\s*(\S.*)?$")
_PAREN_RE = re.compile(r"^[(\[]\s*(?:Clause|Clauses|Foreword|Informative|Normative|see)\b.*[)\]]$", re.I)


def _clean_title(s: str) -> str:
    s = re.sub(r"^[#\s*-]+", "", s or "").strip()
    s = re.sub(r"\*\*", "", s)
    return s[:160]


def annex_sections(stem: str, pages: dict[int, dict[str, Any]], edition: Any) -> list[dict[str, Any]]:
    """Annex headings ("ANNEX D") and annex clause ids ("D-1", "D-2.1") found in the served text."""
    recs: list[dict[str, Any]] = []
    seen: set[str] = set()
    started = False
    order = sorted(pages)
    for pno in order:
        lines = (pages[pno].get("body") or "").splitlines()
        for i, ln in enumerate(lines):
            m = _ANNEX_RE.match(ln)
            if m:
                letter = m.group(1)
                sid = f"Annex {letter}"
                started = True
                if sid in seen:
                    continue
                title = _clean_title(m.group(2))
                if not title or _PAREN_RE.match(title):
                    for nxt in lines[i + 1:i + 8]:
                        n = _clean_title(nxt)
                        if n and not _PAREN_RE.match(n) and not _ANNEX_ID_RE.match(nxt):
                            title = n
                            break
                seen.add(sid)
                recs.append(_sec(stem, sid, title, pno, pages, edition, parent=None))
                continue
            if not started:
                continue
            m = _ANNEX_ID_RE.match(ln)
            if not m:
                continue
            sid = m.group(1)
            if sid in seen:
                continue
            rest = (m.group(2) or "").strip()
            seen.add(sid)
            parent = sid.rsplit(".", 1)[0] if "." in sid else f"Annex {sid[0]}"
            recs.append(_sec(stem, sid, _clean_title(rest)[:120], pno, pages, edition, parent=parent))
    return recs


def amendment_sections(stem: str, pages: dict[int, dict[str, Any]], edition: Any) -> list[dict[str, Any]]:
    """Clause ids named on an amendment sheet: "( Page 5, clause 8.5 ) - Substitute ..." -> section 8.5."""
    recs = []
    seen: set[str] = set()
    for pno in sorted(pages):
        body = pages[pno].get("body") or ""
        for m in re.finditer(r"\(\s*Page\s+\d+\s*,\s*clause\s+([\d.]+[a-z]?)\s*\)\s*[-–—]?\s*([^\n]+)", body, re.I):
            sid = m.group(1).rstrip(".")
            if sid in seen:
                continue
            seen.add(sid)
            recs.append(_sec(stem, sid, "Amendment: " + _clean_title(m.group(2))[:140], pno, pages, edition,
                             parent=None, source="amendment_sheet_scan"))
    return recs


def _sec(stem, sid, title, pno, pages, edition, parent=None, source="annex_heading_scan") -> dict[str, Any]:
    return {
        "doc": stem, "section_id": sid, "title": title, "part": "standard", "pdf_page": pno,
        "printed_label": pages[pno].get("printed_label"), "printed_label_qualified": None,
        "parent": parent, "self_ref": None, "chunk": None, "synthetic": False, "source": source,
        "children": [], "edition": edition, "collection": "specification", "corpus": "specification",
        "authoritative": True, "id": f"spec:{stem}:standard:{sid}:{pno}",
    }


def annex_slice(pages: dict[int, dict[str, Any]], start: int, sid: str, max_pages: int = 4,
                max_chars: int = 12000) -> str:
    """Text from an annex heading / annex clause id to the next annex heading or non-descendant id."""
    out: list[str] = []
    started = False
    for pno in range(start, start + max_pages):
        rec = pages.get(pno)
        if not rec:
            continue
        for ln in (rec.get("body") or "").splitlines():
            if not started:
                if sid.startswith("Annex "):
                    m = _ANNEX_RE.match(ln)
                    if m and m.group(1) == sid[-1]:
                        started = True
                        out.append(ln.strip())
                else:
                    m = _ANNEX_ID_RE.match(ln)
                    if m and m.group(1) == sid:
                        started = True
                        out.append(ln.strip())
                continue
            ma = _ANNEX_RE.match(ln)
            if ma:
                return "\n".join(out)[:max_chars]
            mi = _ANNEX_ID_RE.match(ln)
            if mi and not sid.startswith("Annex "):
                nid = mi.group(1)
                if nid != sid and not nid.startswith(sid + "."):
                    return "\n".join(out)[:max_chars]
            out.append(ln)
            if sum(len(x) for x in out) > max_chars:
                return "\n".join(out)[:max_chars]
    return "\n".join(out)[:max_chars]


CONTENTS_RE = re.compile(r"^\s*(?:#+\s*)?CONTENTS\s*$", re.M)


def is_contents_page(body: str, pno: int) -> bool:
    """A contents page in the front matter (pdf p. <= 12): a CONTENTS heading, or many lines ending in
    a page number (dotted leaders or a page-number column)."""
    if pno > 12:
        return False
    b = body or ""
    if CONTENTS_RE.search(b):
        return True
    lines = [ln for ln in b.splitlines() if ln.strip()]
    tail_num = sum(1 for ln in lines if re.search(r"(?:\.{3,}|…|\|)\s*\d{1,3}\s*\|?\s*$", ln))
    return len(lines) >= 8 and tail_num >= 6 and tail_num >= 0.6 * len(lines)
