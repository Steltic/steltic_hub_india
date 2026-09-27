#!/usr/bin/env python3
"""Corpus validation: must-hit probes over the corpus you built from your own licensed BIS PDFs.

    python3 scripts/validate.py --corpus [--root ROOT] [--json-out FILE] [--strict]

What it checks (every probe is an id lookup or a short key phrase -- no standard text is bundled):

* REQUIRED, for every canonical stem that is converted and indexed (a stem you have not converted is
  SKIPPED, not failed): the clause ids and table ids the design agents ask for most answer an exact
  lookup, the table hit carries its caption, and a few clauses carry a short key phrase; the town
  lookup, the "not in the corpus" note, the US-term trap (Omega0 -> found:false with the IS
  equivalent), the edition labels, the alias layer without US groups, and a grep that no BIS licence
  watermark / licensee e-mail survives in documents/ or indexes/.
* ADVISORY (reported as WARN, never a failure unless --strict): what a corpus-fix pass normally adds
  on top of a first-pass Docling conversion -- figure transcriptions ("Fig. 1" ...), section-property
  rows (IS 808 / IS 811 / IS 1161 designations as exact keys), IS 875 (Part 1) Table 1 row records.
  See CORPUS_FIX_LLM_INSTRUCTIONS.md at the root of the hub repository.

Exit 1 = the build is not usable (a REQUIRED probe failed, or the corpus is empty). A first-pass
conversion often fails some probes; that is what the corpus-fix step is for.

The per-document gold-probe mode of the original corpus tooling (US AISC / ASCE / AISI probes) is
not part of the hub.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

# ---------------------------------------------------------------------------------------------
# REQUIRED probes (only for stems present in the corpus)
# ---------------------------------------------------------------------------------------------
MUST_SECTIONS = {
    "IS_1893_Part_1_2016": ["6.4.2", "7.2.1", "7.2.6", "7.3.6", "7.6.2", "7.6.2.1", "7.7.1", "7.7.3", "7.8.2",
                            "7.11.1.1"],
    "IS_800_2007": ["5.3.3", "7.1.2.1", "8.2.2", "12.2.3", "12.7.2.1", "12.8.3.1", "12.11.3.2", "D-1", "D-2",
                    "Annex D", "E-1.1"],
    "IS_875_Part_4_1987": ["5.2.4"],
    "IS_875_Part_5_1987": ["8.1"],
    "IS_811_1987_Amd1_2011": ["8.5"],
    "IS_875_Part_3_2015": ["6.3.1", "6.3.2", "6.3.3", "6.3.4", "7.2"],
    "IS_875_Part_2_1987": ["3.1.2"],
    "IS_801_1975": ["5.2.1.1", "6.1", "6.6.1.1"],
    "IS_18168_2023": ["1", "5.5", "11.3", "12.3.3.1"],
}
MUST_TABLES = {
    "IS_1893_Part_1_2016": ["3", "7", "8", "9", "10"],
    "IS_800_2007": ["4", "5", "6", "10"],
    "IS_875_Part_3_2015": ["1", "2", "4", "5", "6"],
    "IS_875_Part_2_1987": ["1", "2"],
}
# a short key phrase the returned clause must contain (a content check, not only an id hit)
MUST_PHRASE = {
    ("IS_18168_2023", "5.5"): ["Overstrength"],
    ("IS_18168_2023", "1"): ["SCOPE"],
    ("IS_1893_Part_1_2016", "7.2.6"): ["Table 9"],
    ("IS_800_2007", "D-1"): ["effective length"],
    ("IS_800_2007", "Annex D"): ["EFFECTIVE LENGTH"],
    ("IS_811_1987_Amd1_2011", "8.5"): ["IS 1852"],
}
EDITIONS = (("IS_800_2007", "2007"), ("IS_2062_Part_1_2025", "2025"), ("IS_811_1987_Amd1_2011", "2011"),
            ("IS_1893_Part_1_2016", "2016+A1+A2"), ("IS_875_Part_4_1987", "2021"), ("IS_18168_2023", "2023"))

# ---------------------------------------------------------------------------------------------
# ADVISORY probes: what the corpus-fix pass adds (ids only)
# ---------------------------------------------------------------------------------------------
FIX_FIGURES = [
    ("IS_875_Part_3_2015", "Fig. 1"), ("IS_875_Part_3_2015", "Fig. 2"), ("IS_875_Part_3_2015", "Fig. 4"),
    ("IS_875_Part_3_2015", "Fig. 10"), ("IS_875_Part_3_2015", "Fig. 11"), ("IS_875_Part_3_2015", "Fig. 14"),
    ("IS_875_Part_3_2015", "Fig. 15"), ("IS_875_Part_4_1987", "Fig. 1"), ("IS_875_Part_4_1987", "5.2.1-shape"),
    ("IS_875_Part_4_1987", "5.2.2-shape"), ("IS_875_Part_4_1987", "5.2.3-shape"),
]
FIX_ROWS = [("IS_808_2021", "HB 300"), ("IS_811_1987", "CLR100X50X15X2"), ("IS_1161_2014", "168.3x6.3")]


def corpus_probes(root: Path, strict: bool = False) -> tuple[list[dict[str, Any]], bool]:
    from bis_text import has_watermark

    rows: list[dict[str, Any]] = []

    def add(name: str, status: str, detail: str = "") -> None:
        rows.append({"name": name, "status": status, "passed": status in ("PASS", "SKIP", "WARN"),
                     "detail": detail})

    def check(name: str, ok: bool, detail: str = "", advisory: bool = False) -> None:
        if ok:
            add(name, "PASS", detail)
        elif advisory and not strict:
            add(name, "WARN", detail)
        else:
            add(name, "FAIL", detail)

    fts = root / "search" / "spec_fts.sqlite"
    if not (root / "indexes" / "documents.json").is_file() or not fts.is_file():
        add("indexes built (indexes/documents.json, search/spec_fts.sqlite)", "FAIL",
            "run Rebuild index (scripts/build_index.py) first")
        return rows, False

    from retrieval import Corpus

    c = Corpus(root)
    have = {str(d.get("id")) for d in c.documents if d.get("id")}
    check("at least one converted document is indexed", bool(have),
          "the corpus is empty: convert your licensed BIS PDFs (Admin → Standards, or the IS corpus Convert tab), then Rebuild index")

    def q(t: str, query: str, doc: Optional[str] = None) -> dict[str, Any]:
        return c.run_query({"type": t, "query": query, "doc": doc, "bypass_cache": True, "limit": 12})

    def per_doc(doc: str, name: str, fn: Callable[[], tuple[bool, str]], advisory: bool = False) -> None:
        if doc not in have:
            add(name, "SKIP", f"{doc} not converted")
            return
        ok, detail = fn()
        check(name, ok, detail, advisory=advisory)

    # --- clause and table ids
    for doc, sids in MUST_SECTIONS.items():
        for sid in sids:
            def fn(doc=doc, sid=sid):
                r = q("exact_section", sid, doc)
                hits = [h for h in r.get("hits") or [] if h.get("doc") == doc and h.get("section_id") == sid]
                txt = (hits[0].get("text") or "") if hits else ""
                miss = [n for n in MUST_PHRASE.get((doc, sid), []) if n.lower() not in txt.lower()]
                return (bool(r.get("found")) and bool(hits) and not miss,
                        f"found={r.get('found')} pdf={hits[0].get('pdf_page') if hits else None} missing_phrase={miss}")
            per_doc(doc, f"exact_section {sid} --doc {doc}", fn)
    for doc, tids in MUST_TABLES.items():
        for tid in tids:
            def fn(doc=doc, tid=tid):
                r = q("exact_table", tid, doc)
                hits = [h for h in r.get("hits") or [] if h.get("doc") == doc and str(h.get("table_id")) == tid]
                txt = (hits[0].get("text") or "") if hits else ""
                cap = bool(re.search(rf"Table\s+{re.escape(tid)}\b", txt, re.I))
                return bool(hits) and cap, f"pdf={hits[0].get('pdf_page') if hits else None} caption_on_page={cap}"
            per_doc(doc, f"exact_table {tid} --doc {doc}", fn)

    # --- retrieval behaviour
    def k4():
        top = (q("fts", "k4").get("hits") or [{}])[0]
        return top.get("doc") == "IS_875_Part_3_2015", f"top={top.get('doc')} sec={top.get('section_id')}"
    per_doc("IS_875_Part_3_2015", "fts k4 -> IS 875 (Part 3) first", k4)

    def annex_d():
        top = (q("auto", "Annex D", "IS_800_2007").get("hits") or [{}])[0]
        sid = str(top.get("section_id") or "")
        return (sid == "Annex D" or sid.startswith("D-")) and "contents" not in str(top.get("flags") or ""), \
            f"top sec={sid} p{top.get('pdf_page')}"
    per_doc("IS_800_2007", '"Annex D" --doc IS_800_2007 -> the annex, not the contents page', annex_d)

    r = c.search("fts", "development length", doc="IS_456_2000")
    check('search --doc IS_456_2000 -> "IS_456_2000 is not in the corpus"',
          (not r.get("found")) and r.get("note") == "IS_456_2000 is not in the corpus", str(r.get("note")))
    r = q("auto", "Ω0")
    check('auto "Ω0" -> found:false with the US-term note', (not r.get("found")) and bool(r.get("us_term")),
          str(r.get("note")))

    if {"IS_875_Part_3_2015", "IS_1893_Part_1_2016"} <= have:
        top2 = [(h.get("doc"), str(h.get("section_id"))) for h in (q("fts", "Bengaluru").get("hits") or [])[:2]]
        check('fts "Bengaluru" top-2 = IS 875-3 Annex A, IS 1893 Annex E',
              {d for d, _ in top2} == {"IS_875_Part_3_2015", "IS_1893_Part_1_2016"}, f"top2={top2}")
    else:
        add('fts "Bengaluru" top-2 = IS 875-3 Annex A, IS 1893 Annex E', "SKIP",
            "needs IS_875_Part_3_2015 and IS_1893_Part_1_2016")

    def noida():
        r = q("fts", "Noida zone")
        return (not r.get("found")) and bool(r.get("not_tabulated")), str(r.get("note"))[:80]
    per_doc("IS_1893_Part_1_2016", 'fts "Noida zone" -> not_tabulated miss', noida)

    def rrf():
        top = (q("fts", "response reduction factor").get("hits") or [{}])[0]
        return top.get("doc") == "IS_1893_Part_1_2016", f"top={top.get('doc')} p{top.get('pdf_page')}"
    per_doc("IS_1893_Part_1_2016", 'fts "response reduction factor" -> IS 1893 first', rrf)

    def p4():
        top = (q("fts", "characteristic ground snow load", "IS_875_Part_4_2021").get("hits") or [{}])[0]
        return top.get("edition") == "2021", f"edition={top.get('edition')}"
    per_doc("IS_875_Part_4_1987", "--doc IS_875_Part_4_2021 resolves, edition 2021", p4)

    ed = {d.get("id"): d.get("edition") for d in c.documents}
    for stem, want in EDITIONS:
        per_doc(stem, f"edition {stem} = {want}", lambda stem=stem, want=want: (ed.get(stem) == want,
                                                                                f"edition={ed.get(stem)}"))

    ap = root / "indexes" / "aliases.json"
    blob = ap.read_text(encoding="utf-8") if ap.is_file() else ""
    check("indexes/aliases.json present, no US groups (SDS/Cd/Steel02)",
          bool(blob) and not re.search(r'"(SDS|S_DS|Cd|Steel02|forceBeamColumn)"', blob), "")

    # --- licence watermark / licensee e-mail anywhere in the corpus text / index artefacts
    bad = []
    for p in list((root / "documents").rglob("*")) + list((root / "indexes").glob("*.json")):
        if not p.is_file() or p.suffix.lower() not in (".md", ".json", ".jsonl", ".txt", ".csv"):
            continue
        try:
            s = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if has_watermark(s):
            bad.append(str(p.relative_to(root)))
    check("no licence watermark / licensee e-mail in documents/ and indexes/", not bad,
          f"files={bad[:10]} -- run scripts/strip_watermark.py --apply, then Rebuild index")

    # --- ADVISORY: what the corpus-fix pass adds
    for doc, fid in FIX_FIGURES:
        def fn(doc=doc, fid=fid):
            hits = [h for h in q("exact_table", fid, doc).get("hits") or []
                    if h.get("doc") == doc and str(h.get("table_id")) == fid]
            return bool(hits), f"n={len(hits)}"
        per_doc(doc, f'[corpus-fix] exact_table "{fid}" --doc {doc}: figure transcription', fn, advisory=True)
    for doc, key in FIX_ROWS:
        def fn(doc=doc, key=key):
            hits = [h for h in q("exact_table", key, doc).get("hits") or [] if h.get("doc") == doc]
            return bool(hits), f"n={len(hits)} table_id={hits[0].get('table_id') if hits else None}"
        per_doc(doc, f'[corpus-fix] exact_table "{key}" --doc {doc}: section-property row', fn, advisory=True)

    def t1():
        n = sum(1 for t in c.tables if t.get("doc") == "IS_875_Part_1_2026" and t.get("row_of_table"))
        return n > 0, f"row records={n}"
    per_doc("IS_875_Part_1_2026", "[corpus-fix] IS 875 (Part 1) Table 1 one record per material", t1, advisory=True)

    return rows, all(r["passed"] for r in rows)


def corpus_main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Corpus-level must-hit probes")
    ap.add_argument("--corpus", action="store_true")
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--json-out", type=Path, default=None)
    ap.add_argument("--strict", action="store_true", help="advisory (corpus-fix) probes fail too")
    ap.add_argument("--verbose", action="store_true", help="list every skipped probe")
    a = ap.parse_args(argv)
    rows, ok = corpus_probes(a.root, strict=a.strict)
    skipped: dict[str, int] = {}
    for r in rows:
        if r["status"] == "SKIP" and not a.verbose:
            skipped[r["detail"]] = skipped.get(r["detail"], 0) + 1
            continue
        print(f"{r['status']:4} {r['name']}" + ("" if r["status"] == "PASS" else f"  [{r['detail']}]"))
    for why, k in sorted(skipped.items()):
        print(f"SKIP {k} probe{'s' if k != 1 else ''}: {why}")
    out = a.json_out or (a.root / "indexes" / "validate_corpus.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"passed": ok, "probes": rows}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    n = {s: sum(1 for r in rows if r["status"] == s) for s in ("PASS", "FAIL", "SKIP", "WARN")}
    print(f"\nCORPUS: {'PASS' if ok else 'FAIL'}  (pass {n['PASS']}, fail {n['FAIL']}, skipped {n['SKIP']}, "
          f"corpus-fix advisories {n['WARN']})  report={out}")
    if not ok:
        print("A first-pass conversion usually fails some probes. Next: the corpus-fix step "
              "(CORPUS_FIX_LLM_INSTRUCTIONS.md), then Import fixed corpus, Rebuild index, Validate.")
    return 0 if ok else 1


def main(argv: Optional[list[str]] = None) -> int:
    argv_l = list(sys.argv[1:] if argv is None else argv)
    if "--corpus" in argv_l:
        return corpus_main(argv_l)
    print("usage: validate.py --corpus [--root ROOT] [--json-out FILE] [--strict]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
