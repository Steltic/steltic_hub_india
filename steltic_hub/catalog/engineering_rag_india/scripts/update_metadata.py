#!/usr/bin/env python3
"""Document metadata + manifest (CORPUS-05 / -13 / -15).

* per-doc indexes/documents.json: edition read from the title page
  (``IS <n> (Part <p>) : <yyyy>``), amendments, quality, collection name,
  supersedes, amendment_pages. Existing fields written by other tools are kept.
* INDIA_MANIFEST.json regenerated from the built indexes (counts, status,
  collections map, decisions).

Usage:  python3 scripts/update_metadata.py [--root ROOT] [--manifest-only]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bis_text import pdf_layout_pages  # noqa: E402

TITLE_RE = re.compile(r"\b[I1l]S\s*:?\s*(\d{3,5})\s*(?:\(\s*Part\s*(\d+)\s*\))?\s*[:\-–]\s*((?:19|20)\d{2})\b", re.I)

COLLECTION = {
    "IS_800_2007": "engineering_standards_IS800",
    "IS_808_2021": "engineering_standards_IS808",
    "IS_816_1969": "engineering_standards_IS816",
    "IS_9595_1996": "engineering_standards_IS9595",
    "IS_4000_1992": "engineering_standards_IS4000",
    "IS_1161_2014": "engineering_standards_IS1161",
    "IS_2062_Part_1_2025": "engineering_standards_IS2062",
    "IS_875_Part_1_2026": "engineering_standards_IS875_P1",
    "IS_875_Part_2_1987": "engineering_standards_IS875_P2",
    "IS_875_Part_3_2015": "engineering_standards_IS875_P3",
    "IS_875_Part_4_1987": "engineering_standards_IS875_P4",
    "IS_875_Part_5_1987": "engineering_standards_IS875_P5",
    "IS_1893_Part_1_2016": "engineering_standards_IS1893",
    "IS_801_1975": "engineering_standards_IS801",
    "IS_811_1987": "engineering_standards_IS811",
    "IS_811_1987_Amd1_2011": "engineering_standards_IS811",
    "IS_18168_2023": "engineering_standards_IS18168",
}
FAMILY = {"IS_801_1975": "cfs", "IS_811_1987": "cfs", "IS_811_1987_Amd1_2011": "cfs"}

# Values that the title page alone cannot give (amendment state) -- each checked
# against the PDF (cover / amendment sheets) on 2026-09-20.
OVERRIDES: dict[str, dict[str, Any]] = {
    "IS_1893_Part_1_2016": {
        "edition": "2016+A1+A2",
        "base_edition": "2016",
        "amendments": ["Amd 1 (Sep 2017)", "Amd 2 (Nov 2020)"],
        "amendment_pages": list(range(49, 57)),
        "reaffirmed": "2021",
        "title": "IS 1893 (Part 1):2016 Criteria for Earthquake Resistant Design of Structures, Part 1 "
                 "General Provisions and Buildings (Sixth Revision)",
    },
    "IS_811_1987_Amd1_2011": {
        "edition": "2011",
        "amends": "IS_811_1987",
        "amendments": ["Amd 1 (Nov 2011)"],
        "title": "IS 811:1987 Amendment No. 1 (Nov 2011)",
    },
    "IS_875_Part_4_1987": {
        "edition": "2021",
        "title": "IS 875 (Part 4):2021 Design Loads (Other than Earthquake) for Buildings and Structures — Code of "
                 "Practice, Part 4 Snow Loads (Third Revision)",
        "standard": "IS 875 (Part 4):2021",
        "supersedes": "IS 875 (Part 4):1987",
        "stem_alias": ["IS_875_Part_4_2021"],
        "stem_note": "Directory keeps the historical stem IS_875_Part_4_1987; the document is "
                     "IS 875 (Part 4):2021 (Third Revision). Query with --doc IS_875_Part_4_2021 or this stem.",
        "image_only": ["Fig. 1, Fig. 2 and Fig. 3 are maps / drawings (image-only in a text conversion)"],
    },
    "IS_801_1975": {"amendments": ["Amd 1"], "reaffirmed": "2010"},
    "IS_875_Part_2_1987": {"amendments": ["Amd 1 (Dec 2006)"], "reaffirmed": "2013"},
    "IS_2062_Part_1_2025": {"edition": "2025"},
    "IS_18168_2023": {
        "edition": "2023",
        "title": "IS 18168:2023 Earthquake Resistant Design and Detailing of Steel Buildings — Code of Practice",
        "precedence": "Governs over IS 800:2007 Section 12 where in conflict (Foreword).",
        "amendments": [],
    },
}


def edition_from_title(pdf: Path, stem: str) -> Optional[str]:
    pages = pdf_layout_pages(str(pdf))
    num = re.match(r"IS_(\d+)", stem)
    years = []
    for txt in pages[:3]:
        for m in TITLE_RE.finditer(txt):
            if num and m.group(1) == num.group(1):
                years.append(int(m.group(3)))
    # the title page carries the current edition; forewords cite earlier ones
    return str(max(years)) if years else None


def load_quality(root: Path) -> dict[str, Any]:
    p = root / "scripts" / "quality.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def update_docs(root: Path) -> list[dict[str, Any]]:
    from postprocess import locate_source_pdf

    out = []
    quality = load_quality(root)
    std = root / "documents" / "standards"
    for ddir in sorted(d for d in std.iterdir() if d.is_dir()) if std.is_dir() else []:
        stem = ddir.name
        p = ddir / "indexes" / "documents.json"
        if not p.is_file():
            continue
        docs = json.loads(p.read_text(encoding="utf-8"))
        rec = docs[0] if isinstance(docs, list) else docs
        pdf = locate_source_pdf(rec, stem)
        ed = edition_from_title(pdf, stem) if pdf else None
        ov = OVERRIDES.get(stem, {})
        rec["edition_title_page"] = ed
        rec["edition"] = ov.get("edition") or ed or rec.get("edition")
        for k, v in ov.items():
            if k != "edition":
                rec[k] = v
        rec.setdefault("amendments", [])
        rec["collection_name"] = COLLECTION.get(stem)
        rec["family"] = FAMILY.get(stem, "hr")
        rec["jurisdiction"] = "india"
        q = quality.get(stem) or {}
        rec["quality"] = q.get("quality", "UNREVIEWED")
        rec["known_defects"] = q.get("known_defects", [])
        # stale /workspace paths are informative only; retrieval falls back to the local tree
        rec["searchable_markdown"] = f"documents/standards/{stem}/markdown/{stem}.search.md"
        if isinstance(docs, list):
            docs[0] = rec
        else:
            docs = [rec]
        p.write_text(json.dumps(docs, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        out.append(rec)
    return out


def write_manifest(root: Path, build_stats: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    idx = root / "indexes"
    secs = json.loads((idx / "sections.json").read_text(encoding="utf-8"))
    tbls = json.loads((idx / "tables.json").read_text(encoding="utf-8"))
    eqs = json.loads((idx / "equations.json").read_text(encoding="utf-8"))
    docs = json.loads((idx / "documents.json").read_text(encoding="utf-8"))
    old = {}
    mp = root / "INDIA_MANIFEST.json"
    if mp.is_file():
        try:
            old = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            old = {}
    old_docs = {d.get("stem"): d for d in old.get("docs") or []}
    rows = []
    for d in docs:
        stem = d.get("id")
        if not stem or d.get("collection") != "specification":
            continue
        o = old_docs.get(stem, {})
        ds = [s for s in secs if s.get("doc") == stem]
        dt = [t for t in tbls if t.get("doc") == stem and t.get("table_id")]
        de = [e for e in eqs if e.get("doc") == stem]
        rows.append(
            {
                "stem": stem,
                "edition": d.get("edition"),
                "collection": d.get("collection_name"),
                "pdf": d.get("source_pdf") or o.get("pdf"),
                "family": d.get("family") or o.get("family"),
                "pages": d.get("pdf_pages_total") or o.get("pages"),
                "status": d.get("quality"),
                "known_defects": d.get("known_defects") or [],
                "sections": len({s.get("section_id") for s in ds}),
                "tables_caption_verified": len({(t.get("table_id"), t.get("pdf_page")) for t in dt
                                                if t.get("caption_verified") and not t.get("structured_row")}),
                "section_property_rows": sum(1 for t in dt if t.get("structured_row")),
                "equations": len(de),
                "equations_real_id": sum(1 for e in de if e.get("eq_id") and not e.get("eq_id_synthetic")),
                "amendments": d.get("amendments") or [],
                "wall_s": o.get("wall_s"),
            }
        )
    man = {
        "jurisdiction": "india",
        "tag": "is_bis",
        "usa_corpus": old.get("usa_corpus", "/workspace/engineering_rag"),
        "hard_separation": True,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "see documents.json quality / known_defects per stem",
        "counts_source": "indexes/*.json after scripts/build_index.py",
        "collections": {r["collection"]: r["stem"] for r in rows if r.get("collection")},
        "stem_aliases": {"IS_875_Part_4_2021": "IS_875_Part_4_1987"},
        "decisions": {
            "IS_18168_2023": "collection engineering_standards_IS18168",
            "IS_1893_edition": "IS 1893 (Part 1):2016 + Amd 1 (2017) + Amd 2 (2020)",
        },
        "docs": rows,
        "skip": old.get("skip", []),
        "build_stats": build_stats or {},
    }
    mp.write_text(json.dumps(man, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return man


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--manifest-only", action="store_true")
    a = ap.parse_args(argv)
    if not a.manifest_only:
        for r in update_docs(a.root):
            print(r["id"], r["edition"], r.get("edition_title_page"), r["quality"])
    write_manifest(a.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
