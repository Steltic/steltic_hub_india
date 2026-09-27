#!/usr/bin/env python3
"""K07 (2026-09-26): put figure transcriptions into the served pages.

For every `kind: "figure_transcription"` entry of documents/standards/<STEM>/tables/recovered_index.json
the transcription (tables/*_recovered.md) is inserted into the served page it belongs to — both
markdown/<STEM>.search.md (the pdf_page segment) and markdown/pages_search/page_NNN.md — right after the
line matching the entry's `anchor` regex (after the following `<!-- image -->` placeholder when
`after_image` is true). The block is fenced by `<!-- CORPUS-K07:<stem>:<table_id>:start/end -->`, so a
rerun replaces it (idempotent). Lines matching any `supersede` regex on that page (conversion junk from
the figure image) are moved into a `<!-- SUPERSEDED ... -->` comment, as K02 does for converted grids.

Needs no PDF. Run before `build_index.py --no-repair`:
    python3 scripts/serve_figure_transcriptions.py [--root ROOT] [--check]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpus_fixes import figure_entries  # noqa: E402


def _block(stem: str, e: dict, md_text: str) -> str:
    tag = f"CORPUS-K07:{stem}:{e['table_id']}"
    return f"<!-- {tag}:start -->\n{md_text.strip()}\n<!-- {tag}:end -->"


def _insert(page: str, stem: str, e: dict, md_text: str) -> tuple[str, bool]:
    tag = re.escape(f"CORPUS-K07:{stem}:{e['table_id']}")
    page = re.sub(rf"\n?<!-- {tag}:start -->.*?<!-- {tag}:end -->\n?", "\n", page, flags=re.S)
    lines = page.split("\n")
    sup_res = [re.compile(x) for x in e.get("supersede") or []]
    if sup_res and "conversion noise from the figure image" not in page:
        keep, junk = [], []
        for ln in lines:
            (junk if any(r.search(ln) for r in sup_res) else keep).append(ln)
        if junk:
            lines = keep + ["<!-- SUPERSEDED TEXT (conversion noise from the figure image, replaced by the figure "
                            "transcription; kept for audit):"] + [j.replace("--", "- -") for j in junk] + ["-->"]
    anc = re.compile(e["anchor"])
    idx = next((i for i, ln in enumerate(lines) if anc.search(ln.strip())), None)
    if idx is None:
        return "\n".join(lines), False
    if e.get("after_image"):
        for j in range(idx + 1, min(idx + 6, len(lines))):
            if lines[j].strip() == "<!-- image -->":
                idx = j
                break
    new = lines[: idx + 1] + ["", _block(stem, e, md_text), ""] + lines[idx + 1:]
    out = "\n".join(new)
    out = re.sub(r"\n{4,}", "\n\n\n", out)
    return out, True


def _page_span(text: str, pno: int) -> tuple[int, int]:
    m = re.search(rf"<!--\s*pdf_page={pno}\b[^>]*-->", text)
    if not m:
        return -1, -1
    n = re.search(r"<!--\s*pdf_page=\d+\b[^>]*-->", text[m.end():])
    return m.end(), (m.end() + n.start()) if n else len(text)


def apply(root: Path, check: bool = False) -> list[str]:
    report = []
    std = root / "documents" / "standards"
    if not std.is_dir():
        return report
    for ddir in sorted(d for d in std.iterdir() if d.is_dir()):
        ents = figure_entries(ddir)
        if not ents:
            continue
        stem = ddir.name
        smd = ddir / "markdown" / f"{stem}.search.md"
        stext = smd.read_text(encoding="utf-8") if smd.is_file() else ""
        for e in ents:
            md_text = (ddir / (e.get("md") or e["path"])).read_text(encoding="utf-8")
            pno = int(e["pdf_page"])
            ok_all = True
            a, b = _page_span(stext, pno)
            if a >= 0:
                seg, ok = _insert(stext[a:b], stem, e, md_text)
                stext = stext[:a] + seg + stext[b:]
                ok_all &= ok
            pp = ddir / "markdown" / "pages_search" / f"page_{pno:03d}.md"
            if pp.is_file():
                seg, ok = _insert(pp.read_text(encoding="utf-8"), stem, e, md_text)
                ok_all &= ok
                if not check:
                    pp.write_text(seg if seg.endswith("\n") else seg + "\n", encoding="utf-8")
            report.append(f"{'OK  ' if ok_all else 'MISS'} {stem} {e['table_id']} -> pdf p.{pno}")
        if smd.is_file() and not check:
            smd.write_text(stext, encoding="utf-8")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--check", action="store_true", help="report anchors only, write nothing")
    a = ap.parse_args()
    rep = apply(a.root, a.check)
    print("\n".join(rep))
    return 0 if all(r.startswith("OK") for r in rep) else 1


if __name__ == "__main__":
    raise SystemExit(main())
