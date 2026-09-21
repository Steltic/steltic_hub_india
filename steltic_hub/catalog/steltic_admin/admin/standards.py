"""The standards queue: a folder of licensed BIS specification PDFs -> one Convert run each, in turn.

Converting a specification through the IS corpus module takes hours (Docling at full accuracy, OCR on
the scanned BIS prints), and the India corpus holds seventeen documents. Normally nothing needs
converting: the private engineering_rag_india repo already carries the converted documents and the
hub's post-install copies them into the workspace. This queue is for a document the repo does not
carry, or for re-converting one from your own licensed copy: one `convert` step per PDF that is not
converted yet, each with its canonical stem, then `index` once, then `validate` once. Everything else
-- the retries on a native crash, the resume from the last finished chunk, the converter gate -- is
the hub's and the module's own behaviour; Admin only presses the buttons.

Canonical stems are fixed by the corpus (retrieval ids and the engineering_standards_IS* collection
map depend on them); the table below guesses one from a file name and the user confirms or corrects it
in the UI before anything is queued. The stems are those of the corpus README.
"""
from __future__ import annotations
import pathlib, re

# (regex over the lower-cased file name, canonical stem, label)
STEMS = [
    (r"is[\s_\-]*800\b|is800|general construction in steel", "IS_800_2007", "IS 800:2007"),
    (r"is[\s_\-]*875[\s_\-]*(part[\s_\-]*)?1\b|is875[\s_\-]*p?1\b|dead loads", "IS_875_Part_1_2026", "IS 875 (Part 1) dead loads"),
    (r"is[\s_\-]*875[\s_\-]*(part[\s_\-]*)?2\b|is875[\s_\-]*p?2\b|imposed loads", "IS_875_Part_2_1987", "IS 875 (Part 2):1987 imposed loads"),
    (r"is[\s_\-]*875[\s_\-]*(part[\s_\-]*)?3\b|is875[\s_\-]*p?3\b|wind loads", "IS_875_Part_3_2015", "IS 875 (Part 3):2015 wind loads"),
    (r"is[\s_\-]*875[\s_\-]*(part[\s_\-]*)?4\b|is875[\s_\-]*p?4\b|snow loads", "IS_875_Part_4_1987", "IS 875 (Part 4):2021 snow loads (stem keeps its historical name)"),
    (r"is[\s_\-]*875[\s_\-]*(part[\s_\-]*)?5\b|is875[\s_\-]*p?5\b|special loads|load combinations", "IS_875_Part_5_1987", "IS 875 (Part 5):1987 special loads and combinations"),
    (r"is[\s_\-]*1893|is1893|earthquake resistant design of structures|criteria for earthquake", "IS_1893_Part_1_2016", "IS 1893 (Part 1):2016 + Amd 1/2"),
    (r"is[\s_\-]*18168|is18168|earthquake resistant design and detailing of steel", "IS_18168_2023", "IS 18168:2023"),
    (r"is[\s_\-]*801\b|is801|cold[\s_\-]*formed light gauge", "IS_801_1975", "IS 801:1975"),
    (r"is[\s_\-]*811[\s_\-]*(amd|amendment)|is811[\s_\-]*a(md)?1", "IS_811_1987_Amd1_2011", "IS 811:1987 Amd 1 (2011)"),
    (r"is[\s_\-]*811\b|is811|cold formed light gauge structural steel sections", "IS_811_1987", "IS 811:1987"),
    (r"is[\s_\-]*808\b|is808|hot rolled steel beam|dimensions for hot rolled", "IS_808_2021", "IS 808:2021"),
    (r"is[\s_\-]*1161|is1161|steel tubes for structural", "IS_1161_2014", "IS 1161:2014"),
    (r"is[\s_\-]*2062|is2062|hot rolled medium and high tensile", "IS_2062_Part_1_2025", "IS 2062 (Part 1):2025"),
    (r"is[\s_\-]*816\b|is816|fillet weld", "IS_816_1969", "IS 816:1969"),
    (r"is[\s_\-]*9595|is9595|metal arc welding", "IS_9595_1996", "IS 9595:1996"),
    (r"is[\s_\-]*4000\b|is4000|high strength bolts", "IS_4000_1992", "IS 4000:1992"),
]
KNOWN = {s: label for _, s, label in STEMS}
CORPUS_MODULE = "engineering_rag_india"        # the hub module that holds the IS corpus


def guess_stem(filename: str) -> str:
    n = filename.lower()
    for rx, stem, _ in STEMS:
        if re.search(rx, n):
            return stem
    return ""


def converted_stems(grokbot_root: pathlib.Path) -> set[str]:
    """What the IS corpus workspace already holds: documents/standards/<STEM>/markdown/<STEM>.search.md
    (the India layout), plus any flat markdown/<STEM>.search.md (the older layout)."""
    out: set[str] = set()
    std = grokbot_root / "documents" / "standards"
    if std.is_dir():
        for d in std.iterdir():
            if d.is_dir() and (d / "markdown" / f"{d.name}.search.md").is_file():
                out.add(d.name)
    md = grokbot_root / "markdown"
    if md.is_dir():
        for p in md.glob("*.md"):
            stem = p.name
            for suffix in (".search.md", ".md"):
                if stem.endswith(suffix):
                    stem = stem[:-len(suffix)]
                    break
            out.add(stem)
    return out


def scan(folder: pathlib.Path, grokbot_root: pathlib.Path) -> dict:
    items = []
    if not folder.is_dir():
        return {"folder": str(folder), "exists": False, "items": [], "converted": sorted(converted_stems(grokbot_root))}
    have = converted_stems(grokbot_root)
    for p in sorted(folder.rglob("*.pdf")):
        stem = guess_stem(p.name)
        items.append({"pdf": str(p), "name": p.name, "bytes": p.stat().st_size, "stem": stem,
                      "label": KNOWN.get(stem, ""), "converted": bool(stem) and stem in have})
    return {"folder": str(folder), "exists": True, "items": items, "converted": sorted(have),
            "stems": [{"stem": s, "label": l} for _, s, l in STEMS]}


def build_steps(items: list[dict], folder: str, project: str = "Standards", rebuild_index: bool = True,
                audit: bool = True, chunk_pages: int = 5) -> list[dict]:
    steps = []
    for it in items:
        pdf, stem = str(it.get("pdf") or ""), str(it.get("stem") or "")
        if not pdf:
            continue
        if not stem:
            continue                                   # the India converter writes into documents/standards/<STEM>: no stem, no step
        fields = {"pdf": pdf, "stem": stem, "chunk_pages": chunk_pages, "profile": "is_bis"}
        steps.append({"project": project, "module": CORPUS_MODULE, "tab": "convert", "fields": fields,
                      "on_fail": "continue", "label": f"convert {pathlib.Path(pdf).name} as {stem}"})
    if rebuild_index and steps:
        steps.append({"project": project, "module": CORPUS_MODULE, "tab": "index", "fields": {},
                      "on_fail": "stop", "label": "rebuild the index"})
    if audit and steps:
        steps.append({"project": project, "module": CORPUS_MODULE, "tab": "validate", "fields": {},
                      "on_fail": "continue", "label": "validate the corpus (must-hit probes, watermark grep)"})
    return steps
