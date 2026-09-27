#!/usr/bin/env python3
"""IS 875 (Part 1):2026 Table 1 as one record per material (K04).

Reads the served text (documents/standards/IS_875_Part_1_2026/markdown/IS_875_Part_1_2026.search.md),
takes every grid row of Table 1 (from its caption to the caption of Table 2), collapses the cells the
converter duplicated across columns ("Asbestos cement sheeting | Asbestos cement sheeting"), and
writes structured/table1_unit_weights.csv: one row per material that carries a legible unit weight.

Rows are reproduced as printed (no value is computed or corrected). The group (Sl No. heading) and
the nearest sub-heading above a row are recorded as context; a row whose weight cell is not a
number or a range is not emitted (it stays in the page text). build_index.py turns each CSV row
into its own searchable record.

Usage: python3 scripts/build_is875_1_table1.py [--root ROOT]
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retrieval import find_root  # noqa: E402

STEM = "IS_875_Part_1_2026"
PAGE_RE = re.compile(r"<!--\s*pdf_page=(\d+)\b[^>]*-->")
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
PER_RE = re.compile(r"^(?:m\s*[23²³]|m|each|per\s+\S+|No\.?|kN)$", re.I)
NUM = r"\d+(?:[.,]\d+)?"
WEIGHT_RE = re.compile(rf"^(?:{NUM}(?:\s*(?:to|-|–)\s*{NUM})?)(?:\s*×\s*10\s*[-−]\s*\d+)?(?:\s*\(?[a-z ,]*\)?)?$",
                       re.I)
FIELDS = ["row", "sl_no", "group", "sub_heading", "material", "nominal_size", "weight_kN", "per", "unit",
          "pdf_page", "note"]


def pages(text: str) -> list[tuple[int, str]]:
    out = []
    marks = list(PAGE_RE.finditer(text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.append((int(m.group(1)), COMMENT_RE.sub("", text[m.end():end])))
    return out


def dedupe(cells: list[str]) -> list[str]:
    out: list[str] = []
    for c in cells:
        if c and out and c == out[-1]:
            continue
        out.append(c)
    return out


def unit_for(per: str) -> str:
    p = re.sub(r"\s+", "", per.lower())
    return {"m3": "kN/m³", "m³": "kN/m³", "m2": "kN/m²", "m²": "kN/m²", "m": "kN/m"}.get(p, f"kN per {per}")


def parse(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    in_table = False
    group = sl = sub = ""
    for pno, body in pages(text):
        for ln in body.splitlines():
            s = ln.strip()
            if re.search(r"Table\s+1\s+Unit Weight of Building Materials", s):
                in_table = True
                continue
            if in_table and re.match(r"^(?:#+\s*)?Table\s+2\b", s):
                return rows
            if not in_table or not s.startswith("|") or re.match(r"^\|[-\s|:]+\|$", s):
                continue
            cells = [c.strip() for c in s.strip("|").split("|")]
            if not cells:
                continue
            c0, rest = cells[0], dedupe([c for c in cells[1:]])
            if re.match(r"^(?:Sl|\(1\)|No\.?)", c0) or (rest and re.match(r"^(?:Materials|\(2\)|mm)$", rest[0])):
                continue                                     # repeated header rows
            m = re.match(r"^(\d+)\.?\s*(?:\(1\))?$", c0)
            filled = [c for c in rest if c]
            if not filled:
                continue
            if m and len(set(filled)) == 1:
                # one cell repeated across the whole row ("<material> (see under Sl No. <n>)"): a cross-reference
                sl, group, sub = m.group(1), filled[0], ""
                if re.search(r"see\s+(?:under\s+)?Sl\.?\s*No", filled[0], re.I):
                    rows.append({"sl_no": sl, "group": group, "sub_heading": "", "material": filled[0],
                                 "nominal_size": "", "weight_kN": "", "per": "", "unit": "", "pdf_page": str(pno),
                                 "note": "cross-reference row"})
                continue
            per = rest[-1] if rest and PER_RE.match(re.sub(r"\s+", " ", rest[-1])) else ""
            body_cells = rest[:-1] if per else rest
            weight = body_cells[-1] if body_cells and WEIGHT_RE.match(body_cells[-1]) else ""
            text_cells = body_cells[:-1] if weight else body_cells
            material = text_cells[0] if text_cells else ""
            size = " ".join(c for c in text_cells[1:] if c) if len(text_cells) > 1 else ""
            if m and m.group(1) != sl:
                sl, group, sub = m.group(1), re.sub(r"\s*\(2\)$", "", material), ""
                if not weight:
                    continue
            if not weight:
                if material and len(set(filled)) == 1 and (" shall " in f" {material} " or len(material) > 70):
                    # a provision printed across the row (a sentence with "shall", or a long line)
                    rows.append({"sl_no": sl, "group": group, "sub_heading": sub, "material": material,
                                 "nominal_size": "", "weight_kN": "", "per": "", "unit": "",
                                 "pdf_page": str(pno), "note": "text row"})
                elif material and not per:
                    sub = material                        # a sub-heading above a group of rows
                continue
            if not material:
                continue
            rows.append({"sl_no": sl, "group": group, "sub_heading": sub if sub != material else "",
                         "material": material, "nominal_size": size if size not in ("-", "–") else "",
                         "weight_kN": weight, "per": re.sub(r"\s+", "", per), "unit": unit_for(per) if per else "",
                         "pdf_page": str(pno), "note": ""})
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=None)
    a = ap.parse_args(argv)
    root = a.root or find_root()
    src = root / "documents" / "standards" / STEM / "markdown" / f"{STEM}.search.md"
    if not src.is_file():
        # optional step: nothing to do until IS 875 (Part 1) is converted under its canonical stem
        print(f"{src} not found -- IS 875 (Part 1) is not converted yet; nothing written")
        return 0
    rows = parse(src.read_text(encoding="utf-8"))
    for i, r in enumerate(rows, 1):
        r["row"] = str(i)
    out = root / "documents" / "standards" / STEM / "structured" / "table1_unit_weights.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"{out}: {len(rows)} rows ({sum(1 for r in rows if r['weight_kN'])} with a unit weight)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
