#!/usr/bin/env python3
"""Union per-doc spec indexes + phase-2 indexes; build spec FTS5.

Does not modify original PDFs, per-doc chunk/index files, or phase-2 markdown.
Writes <root>/indexes/ and <root>/search/spec_fts.sqlite.
Reuses search/phase2_fts.sqlite as-is when one exists (the India corpus has none).

An empty corpus (no converted document yet) builds empty indexes and an empty FTS index, so the
grounding server can start and answer "not in the corpus".

Usage:
  python scripts/build_index.py --root <corpus workspace>
  python scripts/build_index.py --root <corpus workspace> --no-repair
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpus_fixes import (  # noqa: E402
    amendment_sections,
    annex_sections,
    annex_slice,
    apply_recovered_tables,
    figure_records,
    is_contents_page,
    recovered_md_path,
    refresh_structured_rows,
    table1_records,
)
from retrieval import (  # noqa: E402
    HTML_COMMENT_RE,
    PAGE_MARK_RE,
    find_root,
    nfkc,
    normalize_eq_id,
)
from pipeline_fixes import (  # noqa: E402
    build_example_id_aliases,
    inherit_continued_table_ids,
    orig_cites_other_eq,
    orig_is_phi_omega_only,
    pdf_page_text,
)

SPEC_STEMS = [
    "IS_800_2007",
    "IS_875_Part_1_2026",
    "IS_875_Part_2_1987",
    "IS_875_Part_3_2015",
    "IS_875_Part_4_1987",
    "IS_875_Part_5_1987",
    "IS_1893_Part_1_2016",
    "IS_2062_Part_1_2025",
    "IS_808_2021",
    "IS_816_1969",
    "IS_9595_1996",
    "IS_4000_1992",
    "IS_1161_2014",
    "IS_801_1975",
    "IS_811_1987",
    "IS_811_1987_Amd1_2011",
    "IS_18168_2023",
]

# India alias layer (CORPUS-12). The former ASCE 7 / AISC 341 groups (Omega0, Cd,
# SDS, SD1, exposure B, Cs, response modification, RBS/WUF-W/BUEEP/STMF/BRBF/SPSW,
# Steel02/forceBeamColumn) were removed: they are not IS terms and made alias
# expansion "find" IS 1893 for US concepts. US terms live in
# indexes/us_terms_not_in_IS.json and return found:false.
SEED_GROUPS = [
    # --- IS 875 (Part 3) wind ---
    ["k1", "k 1", "k_1", "probability factor", "risk coefficient"],
    ["k2", "k 2", "k_2", "terrain roughness and height factor", "terrain and height factor"],
    ["k3", "k 3", "k_3", "topography factor"],
    ["k4", "k 4", "k_4", "importance factor for cyclonic region", "cyclonic importance factor"],
    ["Kd", "K d", "K_d", "wind directionality factor"],
    ["Ka", "K a", "K_a", "area averaging factor"],
    ["Kc", "K c", "K_c", "combination factor"],
    ["Vb", "V b", "V_b", "basic wind speed"],
    ["Vz", "V z", "V_z", "design wind velocity", "design wind speed"],
    ["pz", "p z", "p_z", "wind pressure"],
    ["pd", "p d", "p_d", "design wind pressure"],
    ["Cpe", "C pe", "C_pe", "external pressure coefficient"],
    ["Cpi", "C pi", "C_pi", "internal pressure coefficient"],
    ["Cf", "C f", "C_f", "force coefficient"],
    ["gust factor", "G", "gust effectiveness"],
    ["terrain category", "terrain categories"],
    # --- IS 1893 (Part 1) seismic ---
    ["zone factor", "Z", "seismic zone factor"],
    ["importance factor", "I", "importance factor (I)"],
    ["response reduction factor", "R", "response reduction factor (R)"],
    ["design horizontal acceleration coefficient", "Ah", "A h", "A_h"],
    ["design acceleration coefficient", "Sa/g", "S a /g", "spectral acceleration coefficient"],
    ["fundamental natural period", "Ta", "T a", "approximate fundamental period", "natural period"],
    ["design seismic base shear", "VB", "V B", "base shear"],
    ["seismic weight", "W"],
    ["storey drift", "story drift", "drift", "inter-storey drift"],
    ["minimum design earthquake horizontal lateral force", "rho", "ρ"],
    ["ordinary moment resisting frame", "OMRF", "ordinary moment frame", "OMF"],
    ["special moment resisting frame", "SMRF", "special moment frame", "SMF"],
    ["ordinary braced frame", "OBF", "ordinary concentrically braced frame", "OCBF"],
    ["special braced frame", "SBF", "special concentrically braced frame", "SCBF"],
    ["eccentrically braced frame", "EBF"],
    ["shear link", "link", "links"],
    ["overstrength factor", "Ω", "overstrength"],
    ["soft storey", "soft story"],
    ["torsional irregularity", "torsion irregularity"],
    # --- IS 800 steel ---
    # R04: γm0 (yielding), γm1 (ultimate) and γf (loads) are three different factors, not synonyms of
    # each other or of the generic "partial safety factor" (that pairing reworded "partial safety
    # factors for loads" into "gamma_m0s for loads").
    ["partial safety factor for material", "partial safety factors for materials", "γm", "γ m"],
    ["partial safety factor for loads", "partial safety factors for loads", "load factor", "γf", "γ f"],
    ["γm0", "γ m0", "gamma_m0"],
    ["γm1", "γ m1", "gamma_m1"],
    ["design compressive stress", "fcd", "f cd"],
    ["stress reduction factor", "χ", "chi"],
    ["lateral torsional buckling", "LTB", "lateral-torsional buckling", "χLT", "chi_LT"],
    ["imperfection factor", "α", "alpha"],
    ["effective length", "KL", "effective length factor"],
    ["slenderness ratio", "KL/r", "λ"],
    ["yield stress", "fy", "f y"],
    ["ultimate tensile stress", "fu", "f u"],
    ["E250", "E 250", "Fe 410", "Fe410"],
    ["E350", "E 350", "Fe 490"],
    ["plastic section modulus", "Zp", "Z p"],
    ["elastic section modulus", "Ze", "Z e"],
    ["ISMB", "MB", "medium weight beam"],
    ["ISHB", "HB", "heavy weight beam"],
    ["ISMC", "MC", "medium channel"],
    ["ISA", "angle", "equal angle"],
    ["deflection limit", "deflection limits", "span/"],
    ["strong column weak beam", "column to beam strength ratio", "SCWB"],
    ["panel zone", "joint panel zone"],
    ["demand critical weld", "demand critical welds"],
    ["protected zone", "protected zones"],
    # --- IS 801 / IS 811 cold-formed ---
    ["effective design width", "effective width"],
    ["basic design stress", "basic allowable design stress"],
    ["flat-width ratio", "flat width ratio", "w/t"],
    ["cold formed", "cold-formed", "light gauge"],
    ["lipped channel", "channel with lips"],
    ["zed section", "Z section", "lipped zed"],
    # --- IS 875 (Part 2) imposed loads ---
    ["imposed load", "live load", "IL", "LL"],
    ["warehouse", "warehouses", "storage", "godown"],
    ["office", "offices"],
    ["partition", "partitions"],
    # --- IS 875 (Part 5) combinations ---
    ["load combination", "load combinations"],
]
# city spelling variants (CORPUS-11)
SEED_GROUPS += [list(g) for g in __import__("bis_text").CITY_GROUPS]

# US design terms that IS 1893 / IS 800 / IS 875 do not define (CORPUS-12).
US_TERMS_NOT_IN_IS = {
    "note": "US term — not defined in IS 1893/IS 800. Use the IS concept named in 'is_equivalent' (if any).",
    "terms": {
        "Ω0": "IS 18168:2023 cl. 5.5 overstrength factor Ω; IS 800 12.2.3",
        "Omega0": "IS 18168:2023 cl. 5.5 Ω",
        "omega_0": "IS 18168:2023 cl. 5.5 Ω",
        "Om0": "IS 18168:2023 cl. 5.5 Ω",
        "Cd": "none — IS 1893 7.11.1 storey drift",
        "deflection amplification factor": "none — IS 1893 7.11.1",
        "deflection amplification": "none — IS 1893 7.11.1",
        "SDS": "IS 1893 6.4.2 Ah (Z, I, R, Sa/g)",
        "S_DS": "IS 1893 6.4.2",
        "SD1": "IS 1893 6.4.2",
        "S_D1": "IS 1893 6.4.2",
        "Cs": "IS 1893 6.4.2 Ah",
        "seismic response coefficient": "IS 1893 6.4.2 Ah",
        "exposure B": "IS 875-3 6.3.2 terrain category",
        "exposure C": "IS 875-3 6.3.2 terrain category",
        "exposure D": "IS 875-3 6.3.2 terrain category",
        "response modification coefficient": "IS 1893 7.2.6 response reduction factor R (Table 9)",
        "response modification factor": "IS 1893 7.2.6 R (Table 9)",
        "Ie": "IS 1893 7.2.3 importance factor I (Table 8)",
        "RBS": "none (AISC 358 prequalified connection)",
        "reduced beam section": "none (AISC 358)",
        "WUF-W": "none (AISC 358)",
        "BUEEP": "none (AISC 358)",
        "STMF": "none (AISC 341 special truss moment frame)",
        "BRBF": "none — not an IS 1893 Table 9 / IS 800 / IS 18168 system",
        "buckling-restrained braced frame": "none — not an IS system",
        "SPSW": "none — not an IS 1893 Table 9 / IS 800 / IS 18168 system",
        "steel plate shear wall": "none — not an IS system",
        "Steel02": "none (OpenSees material, not a code term)",
        "forceBeamColumn": "none (OpenSees element, not a code term)",
        "ASCE 7": "none — US standard",
        "AISC 341": "none — US standard; see IS 18168 / IS 800 Section 12",
        "AISC 360": "none — US standard; see IS 800",
        "AISI S100": "none — US standard; see IS 801",
    },
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sanitize_fts(text: Any) -> str:
    if text is None:
        return ""
    s = nfkc(str(text))
    s = s.replace(chr(0), " ")
    s = s.replace("", " ")
    return s


def dump_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def find_search_md(root: Path, stem: str, doc_rec: dict[str, Any]) -> Optional[Path]:
    p = Path(doc_rec.get("searchable_markdown") or "")
    if p.is_file():
        return p
    for cand in (
        root / "documents" / "standards" / stem / "markdown" / f"{stem}.search.md",
        root / "documents" / "standards" / stem / f"{stem}.search.md",
        root / "documents" / "standards" / stem / "complete" / f"{stem}.search.md",
    ):
        if cand.is_file():
            return cand
    return None


def parse_pages(search_md: Path, pages_dir: Optional[Path]) -> dict[int, dict[str, Any]]:
    pages: dict[int, dict[str, Any]] = {}
    if search_md.is_file():
        text = search_md.read_text(encoding="utf-8")
        parts = re.split(r"(?=<!--\s*pdf_page=\d+)", text)
        for part in parts:
            m = PAGE_MARK_RE.search(part)
            if not m:
                m2 = re.match(r"<!--\s*pdf_page=(\d+)\b", part)
                if not m2:
                    continue
                pdf = int(m2.group(1))
                pm = re.search(r"part=(\S+)", part)
                part_flag = pm.group(1) if pm else "standard"
                printed = None
                printed_q = None
            else:
                pdf = int(m.group(1))
                printed = None if m.group(2) in ("None", "none") else m.group(2)
                printed_q = None if m.group(3) in ("None", "none") else m.group(3)
                part_flag = m.group(4)
            body = nfkc(HTML_COMMENT_RE.sub("", part))
            pages[pdf] = {
                "pdf_page": pdf,
                "printed_label": printed,
                "printed_label_qualified": printed_q,
                "part": part_flag,
                "body": body,
            }
    if pages_dir and pages_dir.is_dir():
        for fp in pages_dir.glob("page_*.md"):
            m = re.search(r"page_(\d+)", fp.name)
            if not m:
                continue
            pdf = int(m.group(1))
            body = nfkc(HTML_COMMENT_RE.sub("", fp.read_text(encoding="utf-8")))
            if pdf not in pages:
                pages[pdf] = {
                    "pdf_page": pdf,
                    "printed_label": None,
                    "printed_label_qualified": None,
                    "part": None,
                    "body": body,
                }
            elif len(body) > len(pages[pdf].get("body") or ""):
                pages[pdf]["body"] = body
    return pages


_WORD_RE = re.compile(r"[\w/Ωγχλαρ∆Δ.\-]+", re.U)


def alias_tokens_for_text(text: str, groups: list[list[str]]) -> str:
    """Alias tokens for an FTS row: a group fires when one of its terms of >= 2
    characters occurs as a whole word/phrase (terms of <= 4 chars case-sensitive).
    Single-letter terms (Z, R, I, W, G) never fire and are never emitted."""
    blob = nfkc(text)
    low = " " + re.sub(r"\s+", " ", blob.casefold()) + " "
    words = set(_WORD_RE.findall(blob))
    words |= {w.strip(".-") for w in words}
    words_cf = {w.casefold() for w in words}
    extra: list[str] = []
    for g in groups:
        fired = False
        for t in g:
            if not t or len(t) < 2:
                continue
            if " " in t:
                if f" {t.casefold()} " in low or f" {t.casefold()}" in low:
                    fired = True
            elif len(t) <= 4:
                fired = t in words
            else:
                fired = t.casefold() in words_cf
            if fired:
                break
        if fired:
            extra.extend(t for t in g if len(t) >= 2)
    from bis_text import fts_extra_tokens

    return " ".join(dict.fromkeys(extra)) + " " + fts_extra_tokens(blob)


BACKMATTER_RE = re.compile(
    r"Regional\s+Offices|Branch(es|\s+Offices)\s*:|Manak\s+Bhavan,\s*9\s+Bahadur|"
    r"Published\s+by\s+BIS|Bureau of Indian Standards Act|Amendments Issued Since Publication",
    re.I,
)
COMMITTEE_RE = re.compile(
    r"Representative\(?s\)?|COMMITTEE\s+COMPOSITION|Sectional\s+Committee,\s*CED|\(Alternate\)", re.I
)


def page_flags(body: str, pno: int, last_page: int, meta: dict[str, Any]) -> str:
    """Space-separated flags for ranking: backmatter / committee / cover / amendment_sheet / annex_town_table."""
    flags = []
    if BACKMATTER_RE.search(body or "") or pno >= last_page - 1 and len(body or "") < 6000 and re.search(
        r"BIS|Bureau", body or ""
    ):
        flags.append("backmatter")
    if len(COMMITTEE_RE.findall(body or "")) >= 3:
        flags.append("committee")
    if pno <= 2:
        flags.append("cover")
    if is_contents_page(body or "", pno):
        flags.append("contents")
    if pno in set(meta.get("amendment_pages") or []):
        flags.append("amendment_sheet")
    if re.search(r"ZONE FACTORS FOR SOME IMPORTANT TOWNS|Zone Factors for Some Important Towns|"
                 r"TOWNS WITH POPULATION MORE THAN|THEIR SEISMIC ZONE FACTOR|"
                 r"BASIC WIND SPEED AT 10 m HEIGHT FOR SOME IMPORTANT CITIES|"
                 r"Basic Wind Speed at 10 m Height for Some Important Cities", body or "", re.I):
        flags.append("annex_town_table")
    return " ".join(flags)


def dropped_letter_aliases(eq_ids: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for eid in eq_ids:
        if not eid:
            continue
        m = re.match(r"^([A-Z])(\d[\d.]*-?\d*[A-Za-z]?)$", eid)
        if m:
            dropped = m.group(2)
            out.setdefault(dropped, [])
            if eid not in out[dropped]:
                out[dropped].append(eid)
            out.setdefault(eid, [])
            if dropped not in out[eid]:
                out[eid].append(dropped)
        # Do NOT alias C-prefixed commentary eqs as standard ids (F2-1 != C-F2-1).
    return out


def pick_body_occurrence(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return max(
        rows,
        key=lambda s: (
            int(bool(s.get("children"))),
            int(bool(s.get("synthetic"))),
            int(s.get("pdf_page") or 0),
            len(s.get("title") or ""),
        ),
    )


def build(root: Path, repair: bool = True) -> dict[str, Any]:
    std_root = root / "documents" / "standards"
    if repair and std_root.is_dir():
        from bis_text import poppler_missing_note, poppler_tool
        if poppler_tool("pdftotext") is None:
            # the repair re-derives the per-document indexes from the PDF text layer; without pdftotext it
            # could only lose records, so every document keeps the indexes it has
            poppler_missing_note("pdftotext", "the per-document index repair is skipped")
            repair = False
    if repair and std_root.is_dir():
        from postprocess import locate_source_pdf, repair_bis_doc_indexes
        from update_metadata import update_docs

        update_docs(root)  # editions/amendments first: repair reads amendment_pages
        for stem in SPEC_STEMS:
            ddir = std_root / stem
            if (ddir / "indexes" / "documents.json").is_file():
                # The repair re-derives the per-document indexes from the PDF text layer. Without the
                # PDF it would only lose clause records (a corpus fixed elsewhere, then imported, keeps
                # the indexes it came with): skip it and say where the PDF is looked for.
                d0 = load_json(ddir / "indexes" / "documents.json")
                d0 = (d0[0] if isinstance(d0, list) and d0 else d0) or {}
                if locate_source_pdf(d0, stem) is None:
                    print(f"repair skipped for {stem}: source PDF not found (recorded path, $INDIA_PDF_DIRS or "
                          f"<corpus>/pdfs/{stem}.pdf); its indexes are used as they are", file=sys.stderr)
                    continue
                repair_bis_doc_indexes(ddir)
    p2_root = root / "engineering_rag_phase2"
    out_idx = root / "indexes"
    out_search = root / "search"
    out_idx.mkdir(parents=True, exist_ok=True)
    out_search.mkdir(parents=True, exist_ok=True)

    documents: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    equations: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    spec_pages: dict[str, dict[int, dict[str, Any]]] = {}
    collisions: list[str] = []

    # ----- specs -----
    _qp = root / "scripts" / "quality.json"
    quality_notes: dict[str, Any] = json.loads(_qp.read_text(encoding="utf-8")) if _qp.is_file() else {}
    for stem in SPEC_STEMS:
        ddir = std_root / stem
        idx = ddir / "indexes"
        if not (idx / "documents.json").is_file():
            if ddir.is_dir():                     # a stem not converted yet is simply not in the corpus
                print(f"WARNING: missing spec indexes for {stem}: {idx} (skipped; validate.py will fail)",
                      file=sys.stderr)
            continue
        docs = load_json(idx / "documents.json")
        secs = load_json(idx / "sections.json")
        eqs = load_json(idx / "equations.json")
        tbls = load_json(idx / "tables.json")
        doc0 = docs[0] if isinstance(docs, list) else docs
        edition = doc0.get("edition")
        search_md = find_search_md(root, stem, doc0)
        pages_dir = ddir / "markdown" / "pages_search"
        if search_md:
            spec_pages[stem] = parse_pages(search_md, pages_dir if pages_dir.is_dir() else None)
            doc0 = dict(doc0)
            try:
                doc0["searchable_markdown"] = str(search_md.resolve().relative_to(root.resolve()))
            except ValueError:
                doc0["searchable_markdown"] = str(search_md)
        doc0["collection"] = "specification"
        doc0["corpus"] = "specification"
        doc0["authoritative"] = True
        _q = quality_notes.get(stem) or {}
        if _q:                                   # scripts/quality.json is the one list of known defects
            doc0 = dict(doc0)
            doc0["quality"] = _q.get("quality", doc0.get("quality"))
            doc0["known_defects"] = _q.get("known_defects", doc0.get("known_defects") or [])
        documents.append(doc0)

        # K02-K05 (2026-09-26): recovered transcriptions replace converted grids in the table
        # records; structured rows are regenerated from structured/*.csv; annex and amendment-sheet
        # clause ids become sections; figure transcriptions become table records (K07). None of this
        # needs the source PDFs.
        tbls = figure_records(root, stem, [dict(t) for t in tbls])          # K07: figure transcriptions
        tbls = apply_recovered_tables(root, stem, tbls)
        tbls = refresh_structured_rows(root, stem, tbls)
        if stem == "IS_875_Part_1_2026":
            tbls = [t for t in tbls if not t.get("row_of_table")] + table1_records(root, stem)
        if search_md:
            have = {str(x.get("section_id")) for x in secs}
            extra_secs = annex_sections(stem, spec_pages[stem], edition)
            if "_Amd" in stem:
                extra_secs += amendment_sections(stem, spec_pages[stem], edition)
            secs = list(secs) + [x for x in extra_secs if str(x["section_id"]) not in have]

        for s in secs:
            rec = dict(s)
            rec["edition"] = edition
            rec["collection"] = "specification"
            rec["corpus"] = "specification"
            rec["authoritative"] = True
            rec["id"] = f"spec:{stem}:{rec.get('part')}:{rec.get('section_id')}:{rec.get('pdf_page')}"
            sections.append(rec)

        for e in eqs:
            rec = dict(e)
            rec["edition"] = edition
            rec["collection"] = "specification"
            rec["corpus"] = "specification"
            rec["authoritative"] = True
            rec["id"] = (
                f"spec-eq:{stem}:{rec.get('part')}:{rec.get('eq_id')}:{rec.get('pdf_page')}"
            )
            equations.append(rec)

        for t in tbls:
            rec = dict(t)
            rec["edition"] = edition
            rec["collection"] = "specification"
            rec["corpus"] = "specification"
            rec["authoritative"] = True
            rec["id"] = (
                f"spec-tbl:{stem}:{rec.get('part')}:{rec.get('table_id')}:{rec.get('pdf_page')}:{rec.get('index')}"
            )
            tables.append(rec)

    # ----- phase-2 (optional for India corpus; empty if absent) -----
    p2_idx = p2_root / "indexes"
    if (p2_idx / "documents.json").is_file():
        p2_docs = load_json(p2_idx / "documents.json")
        p2_secs = load_json(p2_idx / "sections.json")
        p2_eqs = load_json(p2_idx / "equations.json")
        p2_toc = load_json(p2_idx / "master_toc.json") if (p2_idx / "master_toc.json").is_file() else {}
    else:
        p2_docs, p2_secs, p2_eqs, p2_toc = [], [], {}, {}

    for d in p2_docs:
        rec = dict(d)
        group = rec.get("group") or ("examples" if rec.get("collection") == "steel_design_examples" else "opensees")
        rec["id"] = rec.get("collection")
        rec["collection"] = group
        rec["source_collection"] = d.get("collection")
        rec["corpus"] = group
        rec["authoritative"] = False
        rec["edition"] = None
        rec["part"] = "n/a"
        documents.append(rec)

    p2_ids = set()
    for s in p2_secs:
        rec = dict(s)
        group = rec.get("group") or (
            "examples" if rec.get("collection") == "steel_design_examples" else "opensees"
        )
        src = s.get("collection")
        rec["source_collection"] = src
        rec["collection"] = group
        rec["corpus"] = group
        rec["authoritative"] = False
        rec["edition"] = None
        rec["doc"] = src
        rec["section_id"] = rec.get("id")
        rec["part"] = "n/a"
        rec["pdf_page"] = None
        rec["printed_label"] = None
        sections.append(rec)
        p2_ids.add(rec["id"])

    spec_raw_ids = {
        s["section_id"]
        for s in sections
        if s.get("collection") == "specification" and s.get("section_id")
    }
    overlap = spec_raw_ids & p2_ids
    if overlap:
        collisions.append(f"raw section_id overlap with phase-2 ids: {sorted(overlap)[:20]}")
    spec_constructed = {s["id"] for s in sections if s.get("collection") == "specification"}
    overlap2 = spec_constructed & p2_ids
    if overlap2:
        collisions.append(f"constructed spec id overlap: {sorted(overlap2)[:20]}")
    if collisions:
        raise SystemExit("ID COLLISION during merge:\n" + "\n".join(collisions))

    # phase-2 equations: map eq_id -> chunk ids
    if isinstance(p2_eqs, dict):
        byid = {s["id"]: s for s in p2_secs}
        for eid, cids in p2_eqs.items():
            for cid in cids:
                chunk = byid.get(cid) or {}
                group = chunk.get("group") or "examples"
                equations.append(
                    {
                        "id": f"p2-eq:{eid}:{cid}",
                        "doc": chunk.get("collection") or "steel_design_examples",
                        "edition": None,
                        "eq_id": eid,
                        "eq_id_display": f"({eid})",
                        "section": cid,
                        "part": "n/a",
                        "pdf_page": None,
                        "printed_label": None,
                        "collection": group if group in ("opensees", "examples") else "examples",
                        "corpus": "examples" if group == "examples" else "opensees",
                        "authoritative": False,
                        "chunk_id": cid,
                        "orig": None,
                        "latex": None,
                        "file": chunk.get("file"),
                        "title": chunk.get("title"),
                    }
                )
    # lightweight table pointers from phase-2 chunks
    for s in p2_secs:
        for tid in s.get("tables") or []:
            tables.append(
                {
                    "id": f"p2-tbl:{s.get('id')}:{tid}",
                    "doc": s.get("collection"),
                    "edition": None,
                    "table_id": str(tid),
                    "title": s.get("title"),
                    "section": s.get("id"),
                    "part": "n/a",
                    "pdf_page": None,
                    "printed_label": None,
                    "collection": s.get("group") or "examples",
                    "corpus": s.get("group") or "examples",
                    "authoritative": False,
                    "chunk_id": s.get("id"),
                    "file": s.get("file"),
                    "markdown_excerpt": None,
                    "md": None,
                    "num_rows": None,
                }
            )

    # Multi-page tables: inherit adjacent null-id pages (ASCE 12.2-1 gold).
    tables = inherit_continued_table_ids(tables)

    # Rebound orig when it is φ/Ω-only or the previous formula (J10-9..12 gold).
    try:
        from postprocess import orig_from_context  # noqa: E402
    except Exception:
        orig_from_context = None
    if orig_from_context:
        pdf_cache: dict[tuple, str] = {}
        meta_by = {d.get("id"): d for d in documents}
        for e in equations:
            if e.get("collection") != "specification" or not e.get("eq_id"):
                continue
            orig = e.get("orig") or ""
            if not (orig_is_phi_omega_only(orig) or orig_cites_other_eq(orig, e.get("eq_id") or "")):
                continue
            meta = meta_by.get(e.get("doc")) or {}
            pdfp = Path(meta.get("source_pdf") or "")
            pno = e.get("pdf_page")
            if not pdfp.is_file() or not pno:
                continue
            key = (str(pdfp), int(pno))
            if key not in pdf_cache:
                pdf_cache[key] = pdf_page_text(pdfp, int(pno))
            filled = orig_from_context(pdf_cache[key], e.get("eq_id"), e.get("eq_id_display"))
            if filled and not orig_is_phi_omega_only(filled) and not orig_cites_other_eq(filled, e.get("eq_id") or ""):
                e["orig"] = filled
                issues = list(e.get("issues") or [])
                msg = "orig rebound from PDF clause next to id token"
                if msg not in issues:
                    issues.append(msg)
                e["issues"] = issues

    # persist repaired spec tables/equations into per-doc indexes
    std_root = root / "documents" / "standards"
    for stem in SPEC_STEMS:
        idx = std_root / stem / "indexes"
        if not idx.is_dir():
            continue
        spec_eq = [e for e in equations if e.get("doc") == stem and e.get("collection") == "specification"]
        spec_tbl = [tb for tb in tables if tb.get("doc") == stem and tb.get("collection") == "specification"]
        if spec_eq:
            dump_json(idx / "equations.json", spec_eq)
        if spec_tbl:
            dump_json(idx / "tables.json", spec_tbl)

    # ----- aliases -----
    eq_ids = [e.get("eq_id") for e in equations if e.get("eq_id")]
    eq_aliases = dropped_letter_aliases(eq_ids)
    # no US eq-id aliases (AISC F2-1 / ASCE 12.8-3 collide with IS 800 12.8.3)
    eq_aliases = {}
    id_aliases = build_example_id_aliases(sections)
    aliases = {
        "synonym_groups": SEED_GROUPS,
        "eq_id_aliases": eq_aliases,
        "id_aliases": id_aliases,
        "conflicts": [
            {
                "trigger": ["response reduction factor", "R factor", "R value", "R for", "Table 9", "Table 23"],
                "prefer": {"doc": "IS_1893_Part_1_2016", "table_id": "9", "section_id": "7.2.6"},
                "flag": {"doc": "IS_800_2007", "table_id": "23",
                         "note": "IS 800 Table 23 R values differ from IS 1893 (Part 1):2016 Table 9; "
                                 "superseded_by: IS 1893:2016 Table 9 (verify precedence)"},
            }
        ],
        "notes": (
            "India alias layer (IS symbols with spacing variants, city spellings, "
            "zone factor<->Z, response reduction factor<->R). US ASCE/AISC groups removed; "
            "see us_terms_not_in_IS.json. No eq-id aliases."
        ),
    }

    # ----- master TOC -----
    spec_toc: dict[str, Any] = {}
    for stem in SPEC_STEMS:
        spec_toc[stem] = {"standard": [], "commentary": []}
    for s in sections:
        if s.get("collection") != "specification":
            continue
        part = s.get("part") if s.get("part") in ("standard", "commentary") else "standard"
        spec_toc[s["doc"]][part].append(
            {
                "id": s["id"],
                "section_id": s.get("section_id"),
                "title": s.get("title"),
                "pdf_page": s.get("pdf_page"),
                "printed_label": s.get("printed_label"),
                "parent": s.get("parent"),
            }
        )
    master_toc = {
        "specification": spec_toc,
        "opensees": {},
        "examples": {},
        "phase2_nav": p2_toc,
    }
    for coll, groups in (p2_toc or {}).items():
        bucket = "examples" if coll == "steel_design_examples" else "opensees"
        master_toc[bucket][coll] = groups

    toc_md_lines = [
        "# Unified master TOC",
        "",
        "Specification sections are authoritative. OpenSees / examples are **not** authoritative.",
        "",
    ]
    for stem in SPEC_STEMS:
        toc_md_lines.append(f"## {stem} (specification)")
        for part in ("standard", "commentary"):
            toc_md_lines.append(f"\n### {part}")
            rows = spec_toc[stem][part]
            # unique by section_id keeping later page
            seen = {}
            for r in rows:
                seen[r["section_id"]] = r
            for r in list(seen.values())[:400]:
                toc_md_lines.append(
                    f"- `{r['section_id']}` — {r.get('title') or ''} (pdf {r.get('pdf_page')}, {r.get('printed_label')})"
                )
            if len(seen) > 400:
                toc_md_lines.append(f"- … {len(seen) - 400} more")
        toc_md_lines.append("")
    toc_md_lines.append("## Phase-2 collections (not authoritative)")
    for coll, groups in (p2_toc or {}).items():
        toc_md_lines.append(f"\n### {coll}")
        for nav in sorted(groups):
            toc_md_lines.append(f"- **{nav}**: {len(groups[nav])} chunks")

    dump_json(out_idx / "documents.json", documents)
    dump_json(out_idx / "sections.json", sections)
    dump_json(out_idx / "equations.json", equations)
    dump_json(out_idx / "tables.json", tables)
    dump_json(out_idx / "master_toc.json", master_toc)
    dump_json(out_idx / "aliases.json", aliases)
    dump_json(out_idx / "us_terms_not_in_IS.json", US_TERMS_NOT_IN_IS)
    (out_idx / "master_toc.md").write_text("\n".join(toc_md_lines) + "\n", encoding="utf-8")
    # copy aliases next to scripts for the staging list
    dump_json(root / "scripts" / "aliases.json", aliases)

    # ----- spec FTS -----
    fts_path = out_search / "spec_fts.sqlite"
    # A rebuild on a machine without the source PDFs cannot re-slice clauses from the PDF text layer;
    # keep the previous build's body for those rows instead of degrading them to whole pages.
    prior_bodies: dict[str, str] = {}
    if fts_path.exists():
        try:
            _pc = sqlite3.connect(f"file:{fts_path}?mode=ro", uri=True)
            for _rid, _body in _pc.execute("SELECT rec_id, body FROM spec_fts WHERE kind = 'section'"):
                prior_bodies[_rid] = _body
            _pc.close()
        except sqlite3.Error:
            prior_bodies = {}
        fts_path.unlink()
    con = sqlite3.connect(fts_path, timeout=60)
    con.execute("PRAGMA journal_mode=DELETE")
    con.execute("PRAGMA synchronous=OFF")
    con.execute("PRAGMA temp_store=MEMORY")
    con.execute(
        """
        CREATE VIRTUAL TABLE spec_fts USING fts5(
          rec_id UNINDEXED,
          kind UNINDEXED,
          doc UNINDEXED,
          edition UNINDEXED,
          section_id,
          eq_id,
          table_id,
          part UNINDEXED,
          collection UNINDEXED,
          pdf_page UNINDEXED,
          printed_label UNINDEXED,
          title,
          body,
          aliases,
          flags UNINDEXED,
          tokenize = 'porter unicode61'
        )
        """
    )
    fts_rows: list[tuple] = []

    # section-level: one row per (doc, part, section_id); the FTS body is the
    # clause SLICE (heading to next heading), not the whole page (CORPUS-11).
    from bis_text import fts_normalize, pdf_layout_pages, slice_section
    from postprocess import locate_source_pdf

    by_key: dict[tuple, list] = defaultdict(list)
    for s in sections:
        if s.get("collection") != "specification":
            continue
        by_key[(s["doc"], s.get("part"), s.get("section_id"))].append(s)
    doc_order: dict[str, list[str]] = defaultdict(list)
    for (d, _p, sid), rows in sorted(
        by_key.items(), key=lambda kv: (kv[0][0], int(kv[1][0].get("pdf_page") or 0))
    ):
        doc_order[d].append(sid)
    layout_cache: dict[str, dict[int, str]] = {}

    def layout_for(doc: str) -> dict[int, str]:
        if doc not in layout_cache:
            meta = next((d for d in documents if d.get("id") == doc), {}) or {}
            pdfp = locate_source_pdf(meta, doc)
            layout_cache[doc] = (
                {i: t for i, t in enumerate(pdf_layout_pages(str(pdfp)), 1)} if pdfp else {}
            )
        return layout_cache[doc]

    slice_src = {"served": 0, "pdf_layout": 0, "page": 0, "annex": 0, "prior_build": 0}
    for key, rows in by_key.items():
        s = pick_body_occurrence(rows)
        doc, part, sid = key
        pages = spec_pages.get(doc) or {}
        pdf = s.get("pdf_page")
        printed = s.get("printed_label")
        body = ""
        if pdf:
            order = doc_order.get(doc) or []
            try:
                i0 = order.index(sid)
            except ValueError:
                i0 = 0
            others = order[i0 + 1 : i0 + 40] + order[max(0, i0 - 5) : i0]
            served = {p: r.get("body") or "" for p, r in pages.items()}
            if s.get("source") in ("annex_heading_scan", "amendment_sheet_scan"):
                body = annex_slice(pages, int(pdf), str(sid)) if s.get("source") == "annex_heading_scan" else ""
                if body:
                    slice_src["annex"] += 1
            else:
                body = slice_section(served, int(pdf), sid, others) or ""
                if body:
                    slice_src["served"] += 1
            if not body and s.get("source") not in ("annex_heading_scan", "amendment_sheet_scan"):
                layout = layout_for(doc)
                body = slice_section(layout, int(pdf), sid, others) or "" if layout else ""
                if body:
                    slice_src["pdf_layout"] += 1
                elif not layout and prior_bodies.get(s["id"]):
                    slice_src["prior_build"] += 1
                    prior = prior_bodies[s["id"]]
                    title0 = s.get("title") or ""
                    fts_rows.append((s["id"], "section", doc, s.get("edition"), sid, None, None, part,
                                     "specification", pdf, printed, title0, prior,
                                     alias_tokens_for_text(prior, SEED_GROUPS),
                                     "amendment_sheet" if s.get("amendment_sheet") else ""))
                    continue
            if not body and int(pdf) in pages:
                body = (pages[int(pdf)].get("body") or "")[:4000]
                slice_src["page"] += 1
            if not printed and int(pdf) in pages:
                printed = pages[int(pdf)].get("printed_label")
        title = s.get("title") or ""
        # prepend id + title so BM25 weights headings (annex clauses also carry their annex name)
        annex_tag = f" Annex {str(sid)[0]}" if s.get("source") == "annex_heading_scan" and "-" in str(sid) else ""
        fts_body = fts_normalize(nfkc(f"{sid} {title}{annex_tag}\n\n{body}"))
        aliases_s = alias_tokens_for_text(fts_body, SEED_GROUPS)
        fts_rows.append(
            (
                s["id"],
                "section",
                doc,
                s.get("edition"),
                sid,
                None,
                None,
                part,
                "specification",
                pdf,
                printed,
                title,
                fts_body,
                aliases_s,
                "amendment_sheet" if s.get("amendment_sheet") else "",
            )
        )

    # equations (real or synthetic ids); garbled Docling latex is not indexed
    for e in equations:
        if e.get("collection") != "specification" or not e.get("eq_id"):
            continue
        if e.get("quality") == "garbled" and not e.get("orig"):
            continue
        body = nfkc(
            "\n".join(
                x
                for x in (
                    e.get("eq_id_display"),
                    e.get("orig"),
                    e.get("latex"),
                    (e.get("nearby_text") or "")[:2000],
                )
                if x
            )
        )
        fts_rows.append(
            (
                e["id"],
                "equation",
                e.get("doc"),
                e.get("edition"),
                e.get("section"),
                e.get("eq_id"),
                None,
                e.get("part"),
                "specification",
                e.get("pdf_page"),
                e.get("printed_label"),
                e.get("eq_id"),
                fts_normalize(body),
                alias_tokens_for_text(body, SEED_GROUPS),
                "synthetic_eq_id" if e.get("eq_id_synthetic") else "",
            )
        )

    # tables with ids — include markdown so row values are searchable
    seen_tbl = set()
    for t in tables:
        if t.get("collection") != "specification" or not t.get("table_id"):
            continue
        if t.get("superseded"):
            continue
        key = (t.get("doc"), t.get("table_id"), t.get("part"), t.get("pdf_page"),
               t.get("index") if t.get("row_of_table") else None)
        if key in seen_tbl:
            continue
        seen_tbl.add(key)
        rec_md = recovered_md_path(root, t)
        if rec_md is not None:
            # K02: the transcription IS the table; the converted grid is not indexed on any path
            blobs = [t.get("title") or "", rec_md.read_text(encoding="utf-8")[:80000]]
        else:
            blobs = [t.get("title") or "", t.get("markdown_excerpt") or ""]
        md = t.get("md") if rec_md is None else None
        if not t.get("structured_row") and t.get("pdf_page") and (spec_pages.get(t.get("doc")) or {}).get(
            int(t["pdf_page"])
        ):
            # caption page text so table cells are searchable even without a grid
            blobs.append((spec_pages[t["doc"]][int(t["pdf_page"])].get("body") or "")[:12000])
        if t.get("row_of_table"):
            blobs = [t.get("title") or "", t.get("markdown_excerpt") or ""]
        mdp = Path(md) if md else None
        if md and not mdp.is_file():
            s = str(md)
            # a path recorded on another machine (a corpus built elsewhere, a fixed corpus zip): keep the
            # part from documents/ on, relative to this workspace
            s = re.sub(r"^.*?(?=documents/standards/)", "", s.replace("\\", "/"))
            mdp = root / s
        if md and mdp.is_file():
            try:
                blobs.append(mdp.read_text(encoding="utf-8")[:80000])
            except OSError:
                pass
        body = nfkc("\n".join(blobs))
        fts_rows.append(
            (
                t["id"],
                "table",
                t.get("doc"),
                t.get("edition"),
                t.get("section"),
                None,
                t.get("table_id"),
                t.get("part"),
                "specification",
                t.get("pdf_page"),
                t.get("printed_label"),
                t.get("title") or t.get("table_id"),
                fts_normalize(body),
                alias_tokens_for_text(body, SEED_GROUPS),
                "structured_row" if t.get("structured_row") else ("caption" if t.get("caption_verified") else ""),
            )
        )

    # Page-level FTS so commentary figures/prose (341 C-F2.18 / pdf 371 "2 t")
    # are searchable even when they are not a section start. Keep "2 t" tokens.
    for stem, pages in spec_pages.items():
        meta = next((d for d in documents if d.get("id") == stem), {}) or {}
        edition = meta.get("edition")
        for pno, rec in pages.items():
            body = rec.get("body") or ""
            if len(body.strip()) < 40:
                continue
            part = rec.get("part") or "standard"
            printed = rec.get("printed_label")
            title = f"{stem} pdf {pno} {printed or ''}".strip()
            flags = page_flags(body, pno, max(pages) if pages else pno, meta)
            fts_rows.append(
                (
                    f"spec-page:{stem}:{part}:{pno}",
                    "page",
                    stem,
                    edition,
                    None,
                    None,
                    None,
                    part,
                    "specification",
                    pno,
                    printed,
                    title,
                    fts_normalize(nfkc(body)),
                    alias_tokens_for_text(body, SEED_GROUPS),
                    flags,
                )
            )

    clean_rows = []
    for row in fts_rows:
        cr = []
        for i, v in enumerate(row):
            if v is None:
                cr.append("" if i not in {9} else None)  # pdf_page may be int/None
            elif isinstance(v, str):
                cr.append(sanitize_fts(v))
            else:
                cr.append(v)
        # pdf_page as text for UNINDEXED safety
        if cr[9] is None:
            cr[9] = ""
        else:
            cr[9] = str(cr[9])
        clean_rows.append(tuple(cr))
    con.executemany(
        "INSERT INTO spec_fts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        clean_rows,
    )
    con.commit()
    con.close()

    # ensure phase2 FTS symlink (skip if India has no phase2)
    p2_fts_src = p2_root / "search" / "phase2_fts.sqlite"
    p2_fts_dst = out_search / "phase2_fts.sqlite"
    if p2_fts_src.is_file() and not p2_fts_dst.exists():
        p2_fts_dst.symlink_to(p2_fts_src)

    spec_docs = [d for d in documents if d.get("collection") == "specification"]
    p2d = [d for d in documents if d.get("collection") != "specification"]
    spec_sec = [s for s in sections if s.get("collection") == "specification"]
    p2_sec = [s for s in sections if s.get("collection") != "specification"]
    spec_eq = [e for e in equations if e.get("collection") == "specification" and e.get("eq_id")]
    p2_eq = [e for e in equations if e.get("collection") != "specification"]
    spec_tbl = [t for t in tables if t.get("collection") == "specification" and t.get("table_id")]
    p2_tbl = [t for t in tables if t.get("collection") != "specification"]
    stats = {
        "documents_spec": len(spec_docs),
        "documents_phase2": len(p2d),
        "sections_spec": len(spec_sec),
        "sections_phase2": len(p2_sec),
        "equations_spec_with_id": len(spec_eq),
        "equations_phase2": len(p2_eq),
        "tables_spec_with_id": len(spec_tbl),
        "tables_phase2": len(p2_tbl),
        "fts_rows": len(fts_rows),
        "section_body_source": slice_src,
        "spec_fts_bytes": fts_path.stat().st_size,
        "phase2_fts_bytes": p2_fts_src.stat().st_size if p2_fts_src.is_file() else 0,
        "id_collisions": collisions,
    }
    dump_json(out_idx / "build_stats.json", stats)
    from update_metadata import write_manifest

    write_manifest(root, stats)
    print(json.dumps(stats, indent=2))
    return stats


def rows_same_part(by_key: dict, doc: str, part: str) -> list[dict[str, Any]]:
    out = []
    for (d, p, _sid), rows in by_key.items():
        if d == doc and p == part:
            out.extend(rows)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Build unified indexes + spec FTS")
    ap.add_argument("--root", type=Path, default=None)
    ap.add_argument("--no-repair", action="store_true", help="skip per-doc BIS index repair")
    args = ap.parse_args()
    root = args.root or find_root()
    build(root, repair=not args.no_repair)


if __name__ == "__main__":
    main()
