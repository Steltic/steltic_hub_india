#!/usr/bin/env python3
"""Unified spec + phase-2 retrieval backend (stdlib + sqlite3). No vector DB.

Indexes live in engineering_rag/indexes/. Spec FTS is search/spec_fts.sqlite;
phase-2 FTS is reused as-is at search/phase2_fts.sqlite.

Every hit carries corpus/collection: specification | opensees | examples.
Spec results are authoritative and tagged provision|commentary.
Phase-2 results are NOT authoritative (part='n/a').
Extracts are verbatim; this module never paraphrases.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import unicodedata
from difflib import get_close_matches
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from pipeline_fixes import (
    adjacent_null_table_pages,
    earliest_next_heading,
    example_family_ids,
    example_id_matches,
    find_heading_pos,
    heading_positions,
    inherit_continued_table_ids,
    is_continued_table,
    is_descendant,
    is_toc_nomenclature_table,
    looks_like_example_id,
    orig_cites_other_eq,
    orig_is_phi_omega_only,
    section_depth,
    section_window_end_page,
    table_id_from_title,
)

from bis_text import (  # noqa: E402
    city_variants,
    find_city_in_query,
    fts_normalize,
    looks_two_column,
    slice_section,
)

PAGE_MARK_RE = re.compile(
    r"<!--\s*pdf_page=(\d+)\s+printed_label=(\S+)\s+"
    r"printed_label_qualified=(\S+)\s+part=(\S+)\s+-->"
)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
WS_RE = re.compile(r"\s+")
SPEC_PAGE_ID_RE = re.compile(
    r"^spec-page:(?P<doc>[^:]+):(?:(?P<part>standard|commentary):)?(?P<page>\d+)$",
    re.I,
)

DOC_ALIASES = {
    "is_800": "IS_800_2007",
    "is 800": "IS_800_2007",
    "is800": "IS_800_2007",
    "is_800_2007": "IS_800_2007",
    "is 800:2007": "IS_800_2007",
    "is_875_1": "IS_875_Part_1_2026",
    "is_875_part_1": "IS_875_Part_1_2026",
    "is_875_part_1_2026": "IS_875_Part_1_2026",
    "is_875_2": "IS_875_Part_2_1987",
    "is_875_part_2": "IS_875_Part_2_1987",
    "is_875_3": "IS_875_Part_3_2015",
    "is_875_part_3": "IS_875_Part_3_2015",
    "is_875_4": "IS_875_Part_4_1987",
    "is_875_part_4": "IS_875_Part_4_1987",
    # the stem directory keeps its historical name; the document is IS 875 (Part 4):2021
    "is_875_part_4_2021": "IS_875_Part_4_1987",
    "is_875_part_4_1987": "IS_875_Part_4_1987",
    "is 875 (part 4):2021": "IS_875_Part_4_1987",
    "is875_p4": "IS_875_Part_4_1987",
    "is_875_5": "IS_875_Part_5_1987",
    "is_875_part_5": "IS_875_Part_5_1987",
    "is_1893": "IS_1893_Part_1_2016",
    "is_1893_part_1": "IS_1893_Part_1_2016",
    "is_1893_2016": "IS_1893_Part_1_2016",
    "is_2062": "IS_2062_Part_1_2025",
    "is_2062_part_1": "IS_2062_Part_1_2025",
    "is_808": "IS_808_2021",
    "is_808_2021": "IS_808_2021",
    "is_816": "IS_816_1969",
    "is_9595": "IS_9595_1996",
    "is_4000": "IS_4000_1992",
    "is_1161": "IS_1161_2014",
    "is_801": "IS_801_1975",
    "is_811": "IS_811_1987",
    "is_811_amd1": "IS_811_1987_Amd1_2011",
    "is811_amd1": "IS_811_1987_Amd1_2011",
    "is_811_1987_amd1": "IS_811_1987_Amd1_2011",
    "is_18168": "IS_18168_2023",
    "is_18168_2023": "IS_18168_2023",
    "is 18168": "IS_18168_2023",
    "is18168": "IS_18168_2023",
    "is 18168:2023": "IS_18168_2023",
    # hosted collection names (engineering_standards_IS*) -> stems
    "engineering_standards_is800": "IS_800_2007",
    "engineering_standards_is808": "IS_808_2021",
    "engineering_standards_is816": "IS_816_1969",
    "engineering_standards_is9595": "IS_9595_1996",
    "engineering_standards_is4000": "IS_4000_1992",
    "engineering_standards_is1161": "IS_1161_2014",
    "engineering_standards_is2062": "IS_2062_Part_1_2025",
    "engineering_standards_is875_p1": "IS_875_Part_1_2026",
    "engineering_standards_is875_p2": "IS_875_Part_2_1987",
    "engineering_standards_is875_p3": "IS_875_Part_3_2015",
    "engineering_standards_is875_p4": "IS_875_Part_4_1987",
    "engineering_standards_is875_p5": "IS_875_Part_5_1987",
    "engineering_standards_is1893": "IS_1893_Part_1_2016",
    "engineering_standards_is801": "IS_801_1975",
    "engineering_standards_is811": "IS_811_1987",
    "engineering_standards_is18168": "IS_18168_2023",
    "specification": "specification",
    "spec": "specification",
    "standards": "specification",
    "india": "specification",
    "is_bis": "specification",
}

TYPE_MAP = {
    "id": "id",
    "exact_section": "exact_section",
    "section": "exact_section",
    "eq": "exact_equation",
    "exact_equation": "exact_equation",
    "equation": "exact_equation",
    "table": "exact_table",
    "exact_table": "exact_table",
    "fts": "fts",
    "keyword": "keyword",
    "command": "command",
    "auto": "auto",
}

CORPUS_RANK = {"specification": 0, "opensees": 1, "examples": 2}

# query topic -> governing standard (applied as a small BM25 bonus in _fts_spec)
TOPIC_PRIOR = [
    (re.compile(r"imposed load|live load|occupanc|warehouse|storage|office|partition", re.I), "IS_875_Part_2_1987"),
    (re.compile(r"\bwind\b|cyclon|terrain|\bk[1-4]\b|gust|pressure coefficient|cpe|cpi", re.I), "IS_875_Part_3_2015"),
    (re.compile(r"\bsnow\b", re.I), "IS_875_Part_4_1987"),
    (re.compile(r"seismic|earthquake|\bzone\b|response reduction|importance factor|spectrum|base shear|drift", re.I),
     "IS_1893_Part_1_2016"),
    (re.compile(r"\blink\b|overstrength|\bEBF\b|\bSCBF\b|\bSMRF\b|ductile", re.I), "IS_18168_2023"),
    (re.compile(r"effective width|cold.formed|light gauge|flat.width", re.I), "IS_801_1975"),
    (re.compile(r"load combination", re.I), "IS_875_Part_5_1987"),
]


def find_root(start: Optional[Path] = None) -> Path:
    if start is None:
        start = Path(__file__).resolve().parent
    p = start if start.is_dir() else start.parent
    env = os.environ.get("INDIA_CORPUS_ROOT")
    cands = [p, p.parent, p.parent.parent] + ([Path(env)] if env else [])
    for cand in cands:
        if (cand / "documents" / "standards").is_dir() and (cand / "scripts").is_dir():
            return cand
    # the workspace layout is <root>/scripts/retrieval.py: its parent is the corpus root, even empty
    return Path(env) if env else Path(__file__).resolve().parent.parent


def nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def squash(text: str) -> str:
    s = nfkc(text or "")
    s = s.replace("\\mathrm", "").replace("\\text", "")
    s = re.sub(r"[_\\{}\s]+", "", s)
    return s.lower()


def collapse_ws(text: str) -> str:
    return WS_RE.sub(" ", nfkc(text or "")).strip()


def norm_key(text: str) -> str:
    return nfkc(text or "").strip().casefold()


def strip_comments(text: str) -> str:
    return HTML_COMMENT_RE.sub("", text or "")


def resolve_doc(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    raw = token.strip()
    if raw in (
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
    ):
        return raw
    # hosted collection names: engineering_standards_IS811_Amd1 / engineering_standard_IS800 -> IS811_Amd1 / IS800
    m = re.match(r"(?i)^engineering_standards?_(.+)$", raw)
    if m and norm_key(raw) not in DOC_ALIASES:
        return resolve_doc(m.group(1))
    key = norm_key(raw).replace("_", " ").replace("-", " ")
    key = re.sub(r"\s+", " ", key)
    compact = key.replace(" ", "")
    for k, v in DOC_ALIASES.items():
        kk = k.replace("_", " ").replace("-", " ")
        if key == kk or compact == k.replace(" ", "").replace("_", "").replace("-", ""):
            return v
    # last resort: exact case-insensitive against known ids
    return DOC_ALIASES.get(norm_key(raw), raw)


def normalize_eq_id(q: str) -> str:
    s = nfkc(q or "").strip()
    s = re.sub(r"^\(?\s*(eq\.?|equation)\s*", "", s, flags=re.I)
    s = s.strip("()[] ").replace(" ", "")
    return s


def normalize_table_id(q: str) -> str:
    s = nfkc(q or "").strip()
    from corpus_fixes import figure_id   # K07: "Figure 10" / "FIG. 10" / "Fig 10" -> "Fig. 10"
    fid = figure_id(s)
    if fid:
        return fid
    s = re.sub(r"^\s*(table|tbl\.?)\s*", "", s, flags=re.I)
    return s.strip()


def normalize_section_id(q: str) -> str:
    s = nfkc(q or "").strip()
    s = re.sub(r"^\s*(section|sec\.?|§)\s*", "", s, flags=re.I)
    return s.strip().rstrip(".")


def fts_escape(query: str) -> str:
    """Build a reasonably safe FTS5 MATCH query; keep quoted phrases.

    Hyphens are FTS5 NOT operators, so hyphenated terms are quoted or split.
    """
    q = nfkc(query).strip()
    if not q:
        return q
    if any(tok in q.upper() for tok in (" AND ", " OR ", " NOT ", " NEAR ")):
        return q
    parts: list[str] = []
    for m in re.finditer(r'"[^"]+"|\S+', q):
        tok = m.group(0)
        if tok.startswith('"') and tok.endswith('"') and len(tok) >= 2:
            parts.append(tok)
            continue
        # split hyphen/en-dash so 'lateral-torsional' matches 'lateral torsional'
        bits = [b for b in re.split(r'[-–—/]+', tok) if b]
        for b in bits:
            cleaned = re.sub(r'[^\w.ΩωφΦλΛαΑβγδεΔπΠσΣμΜ°0-9]', '', b, flags=re.U)
            if not cleaned:
                continue
            # FTS5 treats unquoted 1.00 as column syntax; OCR latex is '1 . 0 0'
            if re.fullmatch(r"\d+\.\d+", cleaned):
                spaced = " ".join(cleaned)
                parts.append(f'("{cleaned}" OR "{spaced}")')
            elif "." in cleaned:
                parts.append(f'"{cleaned}"')
            else:
                parts.append(cleaned)
    if not parts:
        return q
    # parenthesized OR groups need explicit AND; implicit AND is a syntax error
    if any(" OR " in p for p in parts):
        return " AND ".join(parts)
    return " ".join(parts)


class Corpus:
    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = find_root(root)
        self.indexes = self.root / "indexes"
        self.search_dir = self.root / "search"
        self._docs: Optional[list[dict[str, Any]]] = None
        self._sections: Optional[list[dict[str, Any]]] = None
        self._equations: Optional[list[dict[str, Any]]] = None
        self._tables: Optional[list[dict[str, Any]]] = None
        self._aliases: Optional[dict[str, Any]] = None
        self._alias_map: Optional[dict[str, list[str]]] = None
        self._eq_alias: Optional[dict[str, list[str]]] = None
        self._id_alias: Optional[dict[str, list[str]]] = None
        self._pages: dict[str, dict[int, dict[str, Any]]] = {}
        self._doc_meta: Optional[dict[str, dict[str, Any]]] = None
        # SQLite connections are thread-bound: one per thread (K01). A Corpus shared by a threaded
        # server (scripts/serve_http.py) opens its own read-only connection in each worker thread.
        self._tl = threading.local()

    # ----- loaders -----
    def _load_json(self, name: str) -> Any:
        path = self.indexes / name
        if not path.is_file():
            raise FileNotFoundError(f"Missing index {path}; run scripts/build_index.py")
        return json.loads(path.read_text(encoding="utf-8"))

    @property
    def documents(self) -> list[dict[str, Any]]:
        if self._docs is None:
            self._docs = self._load_json("documents.json")
        return self._docs

    @property
    def sections(self) -> list[dict[str, Any]]:
        if self._sections is None:
            self._sections = self._load_json("sections.json")
        return self._sections

    @property
    def equations(self) -> list[dict[str, Any]]:
        if self._equations is None:
            self._equations = self._load_json("equations.json")
        return self._equations

    @property
    def tables(self) -> list[dict[str, Any]]:
        if self._tables is None:
            self._tables = inherit_continued_table_ids(self._load_json("tables.json"))
        return self._tables

    @property
    def aliases(self) -> dict[str, Any]:
        if self._aliases is None:
            p = self.indexes / "aliases.json"
            if not p.is_file():
                p = Path(__file__).resolve().parent / "aliases.json"
            data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
            self._build_alias_maps(data)
            self._aliases = data              # published LAST: another thread never sees half-built maps
        return self._aliases

    def _build_alias_maps(self, data: Optional[dict[str, Any]] = None) -> None:
        if data is None:
            data = self._aliases or {}
        am: dict[str, list[str]] = {}
        for group in data.get("synonym_groups") or []:
            terms = [str(t) for t in group if t]
            keys = list(dict.fromkeys(terms))
            for t in keys:
                am.setdefault(norm_key(t), [])
                for u in keys:
                    if u not in am[norm_key(t)]:
                        am[norm_key(t)].append(u)
        extra = data.get("terms") or {}
        for k, vs in extra.items():
            vals = vs if isinstance(vs, list) else [vs]
            bucket = [k, *[str(v) for v in vals]]
            for t in bucket:
                am.setdefault(norm_key(t), [])
                for u in bucket:
                    if u not in am[norm_key(t)]:
                        am[norm_key(t)].append(u)
        self._alias_map = am
        eqm: dict[str, list[str]] = {}
        for a, b in (data.get("eq_id_aliases") or {}).items():
            targets = b if isinstance(b, list) else [b]
            eqm.setdefault(a, [])
            for t in targets:
                if t not in eqm[a]:
                    eqm[a].append(t)
            for t in targets:
                eqm.setdefault(t, [])
                if a not in eqm[t]:
                    eqm[t].append(a)
        self._eq_alias = eqm
        ida: dict[str, list[str]] = {}
        for a, b in (data.get("id_aliases") or {}).items():
            targets = b if isinstance(b, list) else [b]
            ida.setdefault(a, [])
            for tgt in targets:
                if tgt not in ida[a]:
                    ida[a].append(str(tgt))
        self._id_alias = ida

    @property
    def alias_map(self) -> dict[str, list[str]]:
        _ = self.aliases
        return self._alias_map or {}

    @property
    def eq_alias(self) -> dict[str, list[str]]:
        _ = self.aliases
        return self._eq_alias or {}

    @property
    def doc_meta(self) -> dict[str, dict[str, Any]]:
        if self._doc_meta is None:
            dm: dict[str, dict[str, Any]] = {}
            for d in self.documents:
                did = d.get("id") or d.get("collection")
                if did:
                    dm[str(did)] = d
            self._doc_meta = dm               # published complete (thread-safe lazy load)
        return self._doc_meta

    def spec_fts(self) -> sqlite3.Connection:
        con = getattr(self._tl, "spec_fts", None)
        if con is None:
            p = self.search_dir / "spec_fts.sqlite"
            if not p.is_file():
                raise FileNotFoundError(f"Missing {p}; run scripts/build_index.py")
            con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            self._tl.spec_fts = con
        return con

    def p2_fts(self):
        """Return phase-2 FTS connection, or None if India corpus has no phase2."""
        con = getattr(self._tl, "p2_fts", None)
        if con is False:
            return None
        if con is None:
            p = self.search_dir / "phase2_fts.sqlite"
            if not p.is_file():
                p = self.root / "engineering_rag_phase2" / "search" / "phase2_fts.sqlite"
            if not p.is_file():
                self._tl.p2_fts = False
                return None
            con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            self._tl.p2_fts = con
        return con

    def warm(self) -> "Corpus":
        """Load every shared index once (before worker threads start using this instance)."""
        _ = (self.documents, self.sections, self.equations, self.tables, self.aliases, self.alias_map,
             self.eq_alias, self.doc_meta)
        for d in list(self.doc_meta):
            self.pages_for_doc(d)
        return self

    def unknown_doc(self, doc: Optional[str]) -> Optional[str]:
        """The resolved stem when `doc` names a document this corpus does not hold, else None."""
        if not doc:
            return None
        rd = resolve_doc(doc)
        if rd in self.doc_meta or rd in ("specification", "opensees", "examples"):
            return None
        return rd

    def edition_for(self, doc: Optional[str]) -> Optional[str]:
        if not doc:
            return None
        meta = self.doc_meta.get(doc) or {}
        return meta.get("edition")

    # ----- page text -----
    def pages_for_doc(self, doc: str) -> dict[int, dict[str, Any]]:
        if doc in self._pages:
            return self._pages[doc]
        pages: dict[int, dict[str, Any]] = {}
        meta = self.doc_meta.get(doc) or {}
        search_md = meta.get("searchable_markdown")
        path = Path(search_md) if search_md else None
        if path is None or not path.is_file():
            # fall back to conventional locations
            for cand in (
                self.root / "documents" / "standards" / doc / "markdown" / f"{doc}.search.md",
                self.root / "documents" / "standards" / doc / f"{doc}.search.md",
                self.root / "documents" / "standards" / doc / "complete" / f"{doc}.search.md",
            ):
                if cand.is_file():
                    path = cand
                    break
        if path and path.is_file():
            text = path.read_text(encoding="utf-8")
            parts = re.split(r"(?=<!--\s*pdf_page=\d+)", text)
            for part in parts:
                m = PAGE_MARK_RE.search(part) or re.match(
                    r"<!--\s*pdf_page=(\d+)\b.*?part=(\S+)\s+-->", part
                )
                if not m:
                    continue
                if m.lastindex and m.lastindex >= 4:
                    pdf = int(m.group(1))
                    printed = m.group(2)
                    printed_q = m.group(3)
                    part_flag = m.group(4)
                else:
                    pdf = int(m.group(1))
                    part_flag = m.group(2) if m.lastindex >= 2 else "standard"
                    printed = None
                    printed_q = None
                body = strip_comments(part)
                pages[pdf] = {
                    "pdf_page": pdf,
                    "printed_label": None if printed in (None, "None") else printed,
                    "printed_label_qualified": None
                    if printed_q in (None, "None")
                    else printed_q,
                    "part": part_flag,
                    "body": nfkc(body),
                    "raw": part,
                }
        # fill gaps from pages_search (S400 may miss some)
        ps = self.root / "documents" / "standards" / doc / "markdown" / "pages_search"
        if ps.is_dir():
            for fp in ps.glob("page_*.md"):
                m = re.search(r"page_(\d+)", fp.name)
                if not m:
                    continue
                pdf = int(m.group(1))
                body = nfkc(strip_comments(fp.read_text(encoding="utf-8")))
                if pdf not in pages or len(body) > len(pages[pdf].get("body") or ""):
                    rec = pages.get(pdf) or {
                        "pdf_page": pdf,
                        "printed_label": None,
                        "printed_label_qualified": None,
                        "part": None,
                        "body": body,
                        "raw": body,
                    }
                    rec["body"] = body
                    pages[pdf] = rec
        self._pages[doc] = pages
        return pages

    def page_text(self, doc: str, pdf_page: Optional[int]) -> str:
        if not pdf_page:
            return ""
        rec = self.pages_for_doc(doc).get(int(pdf_page))
        return (rec or {}).get("body") or ""

    def section_extract(
        self,
        rec: dict[str, Any],
        neighbors: int = 0,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Verbatim page text for a spec section, plus neighbor pages."""
        if rec.get("collection") not in (None, "specification") and rec.get("corpus") not in (
            None,
            "specification",
        ):
            return self._phase2_body(rec), []
        doc = rec.get("doc")
        pdf = rec.get("pdf_page")
        if not doc or not pdf:
            return "", []
        pages = self.pages_for_doc(doc)
        start = int(pdf)
        sid = rec.get("section_id") or ""
        if str(doc).startswith("IS_") and sid:
            # BIS: clause slice from the served text (both layout columns), heading to next heading
            order = [
                s.get("section_id")
                for s in sorted(
                    (s for s in self.sections if s.get("doc") == doc and s.get("pdf_page")),
                    key=lambda s: int(s.get("pdf_page") or 0),
                )
            ]
            try:
                i0 = order.index(sid)
            except ValueError:
                i0 = 0
            others = [x for x in order[i0 + 1 : i0 + 40] + order[max(0, i0 - 5) : i0] if x]
            served = {p: r.get("body") or "" for p, r in pages.items()}
            sl = slice_section(served, start, sid, others)
            if (not sl or len(sl) < 40) and (rec.get("source") == "annex_heading_scan" or sid.startswith("Annex ")):
                from corpus_fixes import annex_slice

                sl = annex_slice(pages, start, sid)
            if sl and len(sl) >= 40:
                neighbor_hits = self._neighbor_sections(rec, neighbors) if neighbors else []
                return sl, neighbor_hits
        pool = [
            s
            for s in self.sections
            if s.get("doc") == doc
            and s.get("part") == rec.get("part")
            and s.get("collection") == "specification"
            and s.get("pdf_page")
        ]
        pool.sort(key=lambda s: (int(s["pdf_page"]), s.get("section_id") or ""))
        try:
            idx = next(
                i
                for i, s in enumerate(pool)
                if s.get("section_id") == sid and int(s.get("pdf_page") or 0) == start
            )
        except StopIteration:
            idx = None
        # Same-page TOC leftovers must not close the window; continue onto the
        # next pdf page until the next non-descendant heading (G2.1(a) pdf 144).
        end_page, next_sids = section_window_end_page(pool, idx, sid, start)
        if pages:
            end_page = min(end_page, max(pages))
        end_page = max(end_page, start)
        start_body = (pages.get(start) or {}).get("body") or ""
        pos = find_heading_pos(start_body, sid, which="last")
        pieces = [start_body[pos:] if pos is not None else start_body]
        for pno in range(start + 1, end_page + 1):
            if pno in pages:
                pieces.append(pages[pno]["body"])
        text = "\n\n".join(pieces)
        cut = earliest_next_heading(text, sid, next_sids)
        if cut is not None:
            text = text[:cut]
        text = text.strip()
        if len(text) < 40:
            # do not drop body if heading slice collapsed to a TOC stub
            chunks = []
            for pno in range(start, end_page + 1):
                if pno in pages:
                    chunks.append(pages[pno]["body"])
            text = "\n\n".join(chunks).strip()
        neighbor_hits: list[dict[str, Any]] = []
        if neighbors:
            neighbor_hits = self._neighbor_sections(rec, neighbors)
        return text, neighbor_hits

    @staticmethod
    def _section_depth(sid: Optional[str]) -> int:
        return section_depth(sid)

    def _neighbor_sections(self, rec: dict[str, Any], n: int) -> list[dict[str, Any]]:
        doc = rec.get("doc")
        part = rec.get("part")
        pdf = rec.get("pdf_page") or 0
        pool = [
            s
            for s in self.sections
            if s.get("doc") == doc
            and s.get("part") == part
            and s.get("collection") == "specification"
            and s.get("pdf_page")
        ]
        pool.sort(key=lambda s: (int(s["pdf_page"]), s.get("section_id") or ""))
        try:
            idx = next(
                i
                for i, s in enumerate(pool)
                if s.get("section_id") == rec.get("section_id")
                and int(s.get("pdf_page") or 0) == int(pdf)
            )
        except StopIteration:
            return []
        out = []
        for s in pool[max(0, idx - n) : idx] + pool[idx + 1 : idx + 1 + n]:
            body = self.page_text(s["doc"], s.get("pdf_page"))
            out.append(
                {
                    "section_id": s.get("section_id"),
                    "title": s.get("title"),
                    "pdf_page": s.get("pdf_page"),
                    "printed_label": s.get("printed_label"),
                    "part": s.get("part"),
                    "text": body[:4000],
                }
            )
        return out

    def _phase2_body(self, rec: dict[str, Any]) -> str:
        rel = rec.get("file")
        if not rel:
            return ""
        path = self.root / rel
        if not path.is_file():
            path = self.root / "engineering_rag_phase2" / rel
        if path.is_file():
            return path.read_text(encoding="utf-8")
        return ""

    def _path(self, p: Optional[str]) -> Optional[Path]:
        """Resolve an index path: absolute, recorded on another machine (.../documents/...), or root-relative."""
        if not p:
            return None
        pp = Path(p)
        if pp.is_absolute() and pp.is_file():
            return pp
        s = str(p)
        # a path recorded on another machine (a corpus built elsewhere, a fixed corpus zip): keep the
        # part from documents/ on, relative to this workspace
        s = re.sub(r"^.*?(?=documents/standards/)", "", s.replace("\\", "/"))
        cand = self.root / s
        return cand if cand.is_file() else None

    def table_text(self, rec: dict[str, Any]) -> str:
        if rec.get("structured_row"):
            return nfkc(
                (rec.get("title") or "")
                + "\n\n"
                + (rec.get("markdown_excerpt") or "")
                + f"\n\n[source: {rec.get('csv')}; consistency check: {rec.get('check')}]"
            )
        blobs: list[str] = []
        mdp = self._path(rec.get("md"))
        if mdp is not None:
            blobs.append(mdp.read_text(encoding="utf-8"))
        if rec.get("markdown_excerpt"):
            blobs.append(rec["markdown_excerpt"])
        page = rec.get("pdf_page")
        doc = rec.get("doc")
        if doc and page:
            blobs.append(self.page_text(doc, page))
        # stitch other pages of the same table, including adjacent continued pages
        tid = rec.get("table_id")
        if tid and doc:
            extras = [
                t
                for t in self.tables
                if t.get("doc") == doc
                and t.get("table_id") == tid
                and t.get("pdf_page") != page
                and not t.get("structured_row")
            ]
            extras.extend(adjacent_null_table_pages(self.tables, rec))
            seen_p = {page}
            for t in extras:
                pno = t.get("pdf_page")
                if pno in seen_p:
                    continue
                seen_p.add(pno)
                if self._path(t.get("md")) is not None:
                    blobs.append(self._path(t.get("md")).read_text(encoding="utf-8"))
                elif t.get("markdown_excerpt"):
                    blobs.append(t["markdown_excerpt"])
                elif doc and pno:
                    blobs.append(self.page_text(doc, pno))
        return nfkc("\n\n".join(blobs))

    def equation_text(self, rec: dict[str, Any]) -> str:
        parts = [
            rec.get("orig") or "",
            rec.get("eq_id_display") or "",
            rec.get("latex") or "",
            rec.get("nearby_text") or "",
        ]
        doc = rec.get("doc")
        page = rec.get("pdf_page")
        if doc and page and rec.get("collection") == "specification":
            parts.append(self.page_text(doc, page))
        if rec.get("chunk_id"):
            sec = self._phase2_by_id(rec["chunk_id"])
            if sec:
                parts.append(self._phase2_body(sec))
        return nfkc("\n\n".join(p for p in parts if p))

    def _phase2_by_id(self, cid: str) -> Optional[dict[str, Any]]:
        for s in self.sections:
            if s.get("id") == cid or s.get("section_id") == cid:
                return s
        return None

    # ----- hit shaping -----
    def _hit(
        self,
        *,
        kind: str,
        rec: dict[str, Any],
        text: str,
        neighbors: Optional[list] = None,
        snippet: Optional[str] = None,
        score: Optional[float] = None,
        found: bool = True,
    ) -> dict[str, Any]:
        collection = rec.get("collection") or (
            "specification" if rec.get("doc") in self.doc_meta else rec.get("group")
        )
        if collection in (
            "openseespy_documentation",
            "opensees_documentation",
            "opensees_buildings_3d",
            "opensees_building_templates",
        ):
            collection = "opensees"
        if collection == "steel_design_examples":
            collection = "examples"
        corpus = rec.get("corpus") or collection
        if corpus not in ("specification", "opensees", "examples"):
            corpus = collection if collection in ("specification", "opensees", "examples") else "opensees"
        part = rec.get("part")
        if corpus != "specification":
            part_out = "n/a"
        else:
            part_out = part if part in ("standard", "commentary") else "standard"
        edition = rec.get("edition") or self.edition_for(rec.get("doc"))
        return {
            "found": found,
            "kind": kind,
            "doc": rec.get("doc") or rec.get("source_collection") or rec.get("collection"),
            "edition": edition,
            "section_id": rec.get("section_id") or rec.get("section") or rec.get("id"),
            "eq_id": rec.get("eq_id"),
            "table_id": rec.get("table_id"),
            "part": part_out,
            "pdf_page": rec.get("pdf_page"),
            "printed_label": rec.get("printed_label") or rec.get("printed_label_qualified"),
            "printed_label_qualified": rec.get("printed_label_qualified"),
            "collection": collection,
            "corpus": corpus,
            "authoritative": corpus == "specification",
            "title": rec.get("title"),
            "id": rec.get("id") or rec.get("chunk_id"),
            "text": text,
            "snippet": snippet,
            "neighbors": neighbors or [],
            "score": score,
            "source_collection": rec.get("source_collection") or rec.get("collection"),
            "file": rec.get("file") or rec.get("md"),
            "orig": rec.get("orig"),
            "latex": rec.get("latex"),
        }

    def _miss(
        self,
        query: str,
        kind: str,
        suggestions: Optional[list] = None,
        aliases: Optional[list] = None,
    ) -> dict[str, Any]:
        return {
            "found": False,
            "kind": kind,
            "query": query,
            "nearest_ids": suggestions or [],
            "alias_suggestions": aliases or [],
            "hits": [],
            "collection": None,
            "corpus": None,
            "text": None,
        }

    def _suggest_ids(self, query: str, kind: str, n: int = 8) -> list[str]:
        q = norm_key(query)
        if kind in ("section", "id"):
            pool = list(
                dict.fromkeys(
                    str(s.get("section_id") or s.get("id"))
                    for s in self.sections
                    if s.get("section_id") or s.get("id")
                )
            )
        elif kind == "equation":
            pool = list(dict.fromkeys(str(e.get("eq_id")) for e in self.equations if e.get("eq_id")))
        else:
            pool = list(dict.fromkeys(str(t.get("table_id")) for t in self.tables if t.get("table_id")))
        return get_close_matches(query, pool, n=n, cutoff=0.5) or get_close_matches(
            q, [p.lower() for p in pool], n=n, cutoff=0.5
        )

    def _alias_suggestions(self, query: str) -> list[str]:
        hits = self.alias_map.get(norm_key(query), [])
        # also token-wise
        extra = []
        for tok in re.findall(r"[A-Za-zΩωφΦ0-9_\-]+", query):
            extra.extend(self.alias_map.get(norm_key(tok), []))
        return list(dict.fromkeys([*hits, *extra]))

    def _expand_query_terms(self, query: str) -> list[str]:
        _ = self.aliases
        variants = [query]
        nk = norm_key(query)
        if nk in self.alias_map:
            variants.extend(self.alias_map[nk])
        tokens = re.findall(r"[A-Za-zΩωφΦλΛ0-9._\-]+", query)
        expanded_tokens = []
        changed = False

        def is_abbrev(tok: str) -> bool:
            if any(ord(ch) > 127 for ch in tok):
                return True
            if re.search(r"\d", tok) and re.search(r"[A-Za-z]", tok) and len(tok) <= 12:
                return True
            if len(tok) <= 6 and tok == tok.upper():
                return True
            return len(tok) <= 4

        for tok in tokens:
            al = self.alias_map.get(norm_key(tok), []) if is_abbrev(tok) else []
            if al:
                changed = True
                expanded_tokens.append((tok, al))
            else:
                expanded_tokens.append((tok, [tok]))
        if changed:
            # cartesian is too big; OR each token's aliases independently
            fts_parts = []
            for tok, al in expanded_tokens:
                uniq = list(dict.fromkeys([tok, *al]))
                if len(uniq) == 1:
                    fts_parts.append(uniq[0] if " " not in uniq[0] else f'"{uniq[0]}"')
                else:
                    bits = []
                    for u in uniq:
                        bits.append(f'"{u}"' if (" " in u or "-" in u) else u)
                    fts_parts.append("(" + " OR ".join(bits) + ")")
            variants.append(" ".join(fts_parts))
        return list(dict.fromkeys(variants))

    def _eq_ids_to_try(self, query: str, want_commentary: bool = False) -> list[str]:
        nid = normalize_eq_id(query)
        ids = [nid]
        if nid in self.eq_alias:
            for a in self.eq_alias[nid]:
                # want_commentary=false must not alias a standard id to C-prefix
                if (not want_commentary) and str(a).startswith("C-") and not nid.startswith("C-"):
                    continue
                ids.append(a)
        # dropped leading letter
        m = re.match(r"^([A-Z])(\d[\d.]*-?\d*[A-Za-z]?)$", nid)
        if m:
            ids.append(m.group(2))
        else:
            m2 = re.match(r"^(\d[\d.]*-?\d*[A-Za-z]?)$", nid)
            if m2:
                for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                    cand = letter + nid
                    ids.append(cand)
        # commentary C- prefix only when asked, or when the query itself is C-
        if nid.startswith("C-"):
            ids.append(nid[2:])
        elif want_commentary:
            ids.append("C-" + nid)
        ids.append(nid.upper())
        ids.append(nid.lower())
        return list(dict.fromkeys(ids))

    def _doc_ok(self, rec: dict[str, Any], doc: Optional[str]) -> bool:
        if not doc:
            return True
        resolved = resolve_doc(doc)
        if rec.get("doc") == resolved:
            return True
        if rec.get("collection") == resolved:
            return True
        if rec.get("source_collection") == resolved:
            return True
        if rec.get("group") == resolved:
            return True
        if resolved == "specification" and rec.get("collection") == "specification":
            return True
        if resolved in ("opensees", "examples") and rec.get("collection") == resolved:
            return True
        return False

    def _part_ok(self, rec: dict[str, Any], want_commentary: bool, exact: bool) -> bool:
        if rec.get("collection") != "specification":
            return True
        part = rec.get("part")
        if exact:
            if want_commentary:
                return part == "commentary"
            return part != "commentary"
        return True

    def _best_spec_sections(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Dedupe TOC vs body: keep the later / child-bearing occurrence per (doc, part, id)."""
        buckets: dict[tuple, dict[str, Any]] = {}
        for s in rows:
            key = (s.get("doc"), s.get("part"), s.get("section_id"))
            prev = buckets.get(key)
            if prev is None:
                buckets[key] = s
                continue
            score = (
                int(bool(s.get("children"))),
                int(bool(s.get("synthetic"))),
                int(s.get("pdf_page") or 0),
                len(s.get("title") or ""),
            )
            pscore = (
                int(bool(prev.get("children"))),
                int(bool(prev.get("synthetic"))),
                int(prev.get("pdf_page") or 0),
                len(prev.get("title") or ""),
            )
            if score >= pscore:
                buckets[key] = s
        return list(buckets.values())

    # ----- lookups -----
    def exact_section(
        self,
        query: str,
        doc: Optional[str] = None,
        want_commentary: bool = False,
        neighbors: int = 1,
        limit: int = 20,
    ) -> dict[str, Any]:
        from corpus_fixes import figure_id
        if figure_id(query):
            # K07: a figure id ("Fig. 10", "Figure 4") is answered by its figure-transcription record
            return self.exact_table(query, doc=doc, want_commentary=want_commentary, neighbors=neighbors,
                                    limit=limit)
        sid = normalize_section_id(query)
        # also try phase-2 chunk id
        hits_recs = []
        for s in self.sections:
            if not self._doc_ok(s, doc):
                continue
            if s.get("collection") == "specification":
                if (s.get("section_id") or "") == sid or (s.get("id") == query):
                    if self._part_ok(s, want_commentary, exact=True):
                        hits_recs.append(s)
            else:
                if s.get("id") == query or s.get("section_id") == sid or s.get("command") == query:
                    hits_recs.append(s)
        spec = [s for s in hits_recs if s.get("collection") == "specification"]
        other = [s for s in hits_recs if s.get("collection") != "specification"]
        spec = self._best_spec_sections(spec)
        ordered = spec + other
        if not ordered and not want_commentary:
            retry = self.exact_section(query, doc=doc, want_commentary=True, neighbors=neighbors, limit=limit)
            if retry.get("found"):
                return retry
        if not ordered:
            # try alias expansion (SCWB etc. is not a section id — fall through)
            return {
                "found": False,
                **self._miss(
                    query,
                    "exact_section",
                    suggestions=self._suggest_ids(sid, "section"),
                    aliases=self._alias_suggestions(query),
                ),
            }
        hits = []
        for rec in ordered[:limit]:
            text, neigh = self.section_extract(rec, neighbors=neighbors)
            hits.append(self._hit(kind="section", rec=rec, text=text, neighbors=neigh))
        return {"found": True, "query": query, "type": "exact_section", "hits": hits}

    def exact_equation(
        self,
        query: str,
        doc: Optional[str] = None,
        want_commentary: bool = False,
        neighbors: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        ids = self._eq_ids_to_try(query, want_commentary=want_commentary)
        recs = []
        seen = set()
        for e in self.equations:
            if not self._doc_ok(e, doc):
                continue
            eid = e.get("eq_id") or ""
            disp = (e.get("eq_id_display") or "").strip("()")
            if eid not in ids and normalize_eq_id(eid) not in ids and normalize_eq_id(disp) not in ids:
                continue
            if e.get("collection") == "specification" and not self._part_ok(
                e, want_commentary, exact=True
            ):
                # explicit C-prefixed query still returns that commentary eq
                qn = normalize_eq_id(query)
                if not (qn.startswith("C-") and str(e.get("eq_id") or "").startswith("C-")):
                    continue
            key = (
                e.get("doc"),
                e.get("eq_id"),
                e.get("part"),
                e.get("pdf_page"),
                e.get("chunk_id"),
            )
            if key in seen:
                continue
            seen.add(key)
            recs.append(e)
        # Do NOT alias F2-1 -> C-F2-1 when want_commentary is false.
        if not recs:
            return {
                "found": False,
                **self._miss(
                    query,
                    "exact_equation",
                    suggestions=self._suggest_ids(normalize_eq_id(query), "equation"),
                    aliases=self._alias_suggestions(query),
                ),
            }
        # spec first, then examples
        recs.sort(
            key=lambda r: (
                CORPUS_RANK.get(r.get("collection") or "", 9),
                r.get("doc") or "",
                int(r.get("pdf_page") or 0),
            )
        )
        hits = []
        for rec in recs[:limit]:
            text = self.equation_text(rec)
            neigh = []
            if neighbors and rec.get("collection") == "specification" and rec.get("section"):
                # neighbor = parent section
                parent = next(
                    (
                        s
                        for s in self.sections
                        if s.get("doc") == rec.get("doc")
                        and s.get("section_id") == rec.get("section")
                        and s.get("part") == rec.get("part")
                    ),
                    None,
                )
                if parent:
                    t, n = self.section_extract(parent, neighbors=0)
                    neigh = [{"section_id": parent.get("section_id"), "text": t[:4000]}]
            hits.append(self._hit(kind="equation", rec=rec, text=text, neighbors=neigh))
        return {"found": True, "query": query, "type": "exact_equation", "hits": hits}

    def exact_table(
        self,
        query: str,
        doc: Optional[str] = None,
        want_commentary: bool = False,
        neighbors: int = 0,
        limit: int = 20,
    ) -> dict[str, Any]:
        tid = normalize_table_id(query)
        tid_cmp = tid.replace(" ", "")
        is_fig = tid.startswith("Fig. ")
        recs_real = []
        recs_null = []
        qlow = tid_cmp.lower()
        for t in self.tables:
            if not self._doc_ok(t, doc):
                continue
            rid = (t.get("table_id") or "").replace(" ", "")
            title = t.get("title") or ""
            excerpt = t.get("markdown_excerpt") or ""
            blob_cmp = (title + " " + excerpt).replace(" ", "").lower()
            rid_l = rid.lower()
            match = False
            lab = (t.get("label") or "").replace(" ", "").lower()
            if lab and lab == qlow:
                match = True                      # IS 811 engine label (CLR100X50X15X2) is an exact key
            elif rid_l and rid_l == qlow:
                match = True
            elif is_fig:
                # K07: "Fig. 1" must not match "Fig. 10" / "Fig. 11" by substring
                match = bool(re.search(re.escape(qlow) + r"(?![\d])", blob_cmp))
            elif rid_l and (qlow.startswith(rid_l) or rid_l.startswith(qlow)) and qlow in blob_cmp:
                match = True
            elif qlow in blob_cmp:
                match = True
            if not match:
                continue
            if t.get("collection") == "specification" and not self._part_ok(
                t, want_commentary, exact=True
            ):
                continue
            if rid:
                recs_real.append(t)
            else:
                recs_null.append(t)
        # never rank table_id=null TOC/nomenclature above a real table_id
        recs = recs_real or recs_null
        live = [t for t in recs if not t.get("superseded")]
        if live:
            recs = live
        verified = [t for t in recs if t.get("caption_verified") or t.get("structured_row")]
        if verified:
            recs = verified + [t for t in recs if t not in verified and t.get("continued")]
        if not recs and not want_commentary:
            return self.exact_table(query, doc=doc, want_commentary=True, neighbors=neighbors, limit=limit)
        if not recs:
            return {
                "found": False,
                **self._miss(
                    query,
                    "exact_table",
                    suggestions=self._suggest_ids(tid, "table"),
                    aliases=self._alias_suggestions(query),
                ),
            }
        # Short/numeric BIS ids (e.g. "4") must not lose to substring hits like "24"
        # a table's caption record answers "Table 1"; its per-row records (IS 875-1 Table 1) do not
        if any(not t.get("row_of_table") for t in recs):
            recs = [t for t in recs if not t.get("row_of_table")]
        exact_id = [
            t
            for t in recs
            if (t.get("table_id") or "").replace(" ", "").lower() == qlow
            or (t.get("label") or "").replace(" ", "").lower() == qlow
        ]
        if exact_id:
            recs = exact_id
        # stitch same table_id+doc into one hit preferring real grids
        recs.sort(
            key=lambda r: (
                0 if (r.get("table_id") or "").replace(" ", "").lower() == qlow else 1,
                CORPUS_RANK.get(r.get("collection") or "", 9),
                0 if r.get("table_id") else 1,
                1 if is_toc_nomenclature_table(r) else 0,
                1 if is_continued_table(r) else 0,
                0 if r.get("num_rows") else 1,
                -(int(r.get("num_rows") or 0)),
                int(r.get("pdf_page") or 0),
            )
        )
        grouped: dict[tuple, list] = {}
        for t in recs:
            key_tid = t.get("table_id") or ("title:" + (t.get("title") or "")[:40])
            grouped.setdefault((t.get("doc"), key_tid, t.get("part")), []).append(t)
        hits = []
        for _, group in grouped.items():
            primary = group[0]
            text = self.table_text(primary)
            h = self._hit(kind="table", rec=primary, text=text)
            if primary.get("structured_row"):
                h["structured_row"] = True
                h["check"] = primary.get("check")
            hits.append(h)
            if len(hits) >= limit:
                break
        return {"found": True, "query": query, "type": "exact_table", "hits": hits}

    def _spec_page_id(self, query: str, doc: Optional[str] = None) -> Optional[dict[str, Any]]:
        """Resolve FTS rec_id spec-page:DOC:standard:N to that page's markdown."""
        m = SPEC_PAGE_ID_RE.match((query or "").strip())
        if not m:
            return None
        stem = resolve_doc(m.group("doc"))
        if doc and not self._doc_ok({"doc": stem, "collection": "specification"}, doc):
            return None
        part = (m.group("part") or "standard").lower()
        try:
            pno = int(m.group("page"))
        except (TypeError, ValueError):
            return None
        pages = self.pages_for_doc(stem)
        rec = pages.get(pno)
        if not rec:
            return None
        body = rec.get("body") or ""
        if not body.strip():
            return None
        got_part = (rec.get("part") or part or "standard").lower()
        if m.group("part") and got_part not in (part, "standard", "commentary"):
            # keep the page if part metadata is missing
            pass
        hit_rec = {
            "id": f"spec-page:{stem}:{got_part}:{pno}",
            "doc": stem,
            "part": got_part if got_part in ("standard", "commentary") else part,
            "pdf_page": pno,
            "printed_label": rec.get("printed_label"),
            "printed_label_qualified": rec.get("printed_label_qualified"),
            "collection": "specification",
            "corpus": "specification",
            "title": f"{stem} pdf {pno} {rec.get('printed_label') or ''}".strip(),
            "section_id": None,
        }
        return self._hit(kind="page", rec=hit_rec, text=body)

    def exact_id(
        self,
        query: str,
        doc: Optional[str] = None,
        want_commentary: bool = False,
        neighbors: int = 1,
        limit: int = 20,
    ) -> dict[str, Any]:
        # phase-2 chunk id + _p1 family aliases (F.1 -> F.1-1A_p1, not F.12 / spec F9.1)
        p2 = example_family_ids(self.sections, query)
        if not p2:
            p2 = [s for s in self.sections if s.get("id") == query and s.get("collection") != "specification"]
        _ = self.aliases
        extra = (self._id_alias or {}).get(query) or []
        if extra:
            have = {s.get("id") for s in p2}
            for s in self.sections:
                if s.get("id") in extra and s.get("id") not in have:
                    p2.append(s)
        if p2:
            hits = []
            for rec in p2[:limit]:
                hits.append(
                    self._hit(kind="id", rec=rec, text=self._phase2_body(rec))
                )
            return {"found": True, "query": query, "type": "id", "hits": hits}
        if looks_like_example_id(query):
            return {
                "found": False,
                **self._miss(
                    query,
                    "id",
                    suggestions=self._suggest_ids(query, "section"),
                    aliases=self._alias_suggestions(query),
                ),
            }
        page_hit = self._spec_page_id(query, doc=doc)
        if page_hit is not None:
            return {"found": True, "query": query, "type": "id", "hits": [page_hit]}
        # spec constructed id or section id
        sec = self.exact_section(
            query, doc=doc, want_commentary=want_commentary, neighbors=neighbors, limit=limit
        )
        if sec.get("found"):
            return sec
        eq = self.exact_equation(
            query, doc=doc, want_commentary=want_commentary, neighbors=neighbors, limit=limit
        )
        if eq.get("found"):
            return eq
        tbl = self.exact_table(
            query, doc=doc, want_commentary=want_commentary, neighbors=neighbors, limit=limit
        )
        if tbl.get("found"):
            return tbl
        return {
            "found": False,
            **self._miss(
                query,
                "id",
                suggestions=self._suggest_ids(query, "section")
                + self._suggest_ids(query, "equation")[:4],
                aliases=self._alias_suggestions(query),
            ),
        }

    def command(
        self,
        query: str,
        doc: Optional[str] = None,
        limit: int = 8,
        **_kw: Any,
    ) -> dict[str, Any]:
        q = query.lower()
        hits_recs = [
            s
            for s in self.sections
            if s.get("command") and q in str(s.get("command")).lower()
        ]
        if doc:
            hits_recs = [s for s in hits_recs if self._doc_ok(s, doc)]
        if not hits_recs:
            return {
                "found": False,
                **self._miss(query, "command", suggestions=[], aliases=self._alias_suggestions(query)),
            }
        hits = [
            self._hit(kind="command", rec=s, text=self._phase2_body(s))
            for s in hits_recs[:limit]
        ]
        return {"found": True, "query": query, "type": "command", "hits": hits}

    # ----- India-specific query handling (CORPUS-11/12/17) -----
    @lru_cache(maxsize=1)
    def us_terms(self) -> dict[str, Any]:
        p = self.indexes / "us_terms_not_in_IS.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}

    def us_term_miss(self, query: str) -> Optional[dict[str, Any]]:
        data = self.us_terms() or {}
        terms = data.get("terms") or {}
        q = nfkc(query).strip()
        for term, equiv in sorted(terms.items(), key=lambda kv: -len(kv[0])):
            t = nfkc(term)
            if len(t) <= 3:
                hit = re.search(rf"(?<![\w]){re.escape(t)}(?![\w])", q)
            else:
                hit = re.search(rf"(?<![\w]){re.escape(t)}(?![\w])", q, re.I)
            if hit:
                miss = self._miss(query, "us_term")
                miss.update(
                    {
                        "found": False,
                        "us_term": term,
                        "note": data.get("note") or "US term — not defined in IS 1893/IS 800",
                        "is_equivalent": equiv,
                    }
                )
                return miss
        return None

    def _annex_town_pages(self) -> list[dict[str, Any]]:
        con = self.spec_fts()
        rows = con.execute(
            "SELECT doc, pdf_page, body, edition, printed_label FROM spec_fts "
            "WHERE kind='page' AND flags LIKE '%annex_town_table%'"
        ).fetchall()
        out = []
        for r in rows:
            out.append(dict(r))
            # the town table continues on the next page (no title line there)
            try:
                nxt = int(r["pdf_page"]) + 1
            except (TypeError, ValueError):
                continue
            r2 = con.execute(
                "SELECT doc, pdf_page, body, edition, printed_label, flags FROM spec_fts "
                "WHERE kind='page' AND doc=? AND pdf_page=?",
                (r["doc"], str(nxt)),
            ).fetchone()
            if r2 is not None and "backmatter" not in (r2["flags"] or "") and "committee" not in (r2["flags"] or ""):
                out.append(dict(r2))
        return out

    # ------------------------------------------------------------------------------------------
    # K07 (2026-09-26): figure transcriptions answer map questions the annex tables cannot
    # ------------------------------------------------------------------------------------------
    def _figure_record(self, doc: str, table_id: str) -> Optional[dict[str, Any]]:
        for t in self.tables:
            if t.get("doc") == doc and t.get("figure_transcription") and str(t.get("table_id")) == table_id:
                return t
        return None

    def _figure_lines(self, rec: dict[str, Any]) -> list[str]:
        mdp = self._path(rec.get("md"))
        return mdp.read_text(encoding="utf-8").splitlines() if mdp is not None else []

    def _figure_town_row(self, rec: dict[str, Any], query: str) -> Optional[tuple[str, str]]:
        """(town, table row) of a figure record's town table whose first cell names a town in the query."""
        best = None
        for ln in self._figure_lines(rec):
            if not ln.startswith("| "):
                continue
            cell = ln.split("|")[1].strip()
            for name in re.split(r"\s*[()]\s*", cell):
                name = name.strip()
                if len(name) < 3 or name.lower() in ("town", "town (not in annex a)", "nubra", "zanskar", "dawar"):
                    continue
                for v in city_variants(name):
                    if re.search(rf"(?<![A-Za-z]){re.escape(v)}(?![A-Za-z])", query, re.I):
                        if best is None or len(v) > len(best[0]):
                            best = (v, ln)
        return best

    def snow_town_lookup(self, query: str, doc: Optional[str] = None, **kw: Any) -> Optional[dict[str, Any]]:
        """'snow load Srinagar', 'Fig. 1 snow zone map Shimla zone number', 'snow load Chandigarh': the
        IS 875 (Part 4) Fig. 1 figure record (zone table) is the first hit, with the town's row (or the
        line naming towns outside the mapped area) as the snippet; the full-text hits follow."""
        stem = "IS_875_Part_4_1987"
        if not re.search(r"\bsnow\b", query, re.I):
            return None
        if doc and resolve_doc(doc) not in (stem, "specification"):
            return None
        rec = self._figure_record(stem, "Fig. 1")
        if rec is None:
            return None
        got = self._figure_town_row(rec, query)
        if got is None:
            city = find_city_in_query(query)
            if not city:
                return None
            out = [ln for ln in self._figure_lines(rec) if "outside the mapped area" in ln]
            if not out or not any(re.search(rf"(?<![A-Za-z]){re.escape(v)}(?![A-Za-z])", out[0], re.I)
                                  for v in city_variants(city)):
                return None
            got = (city, out[0].lstrip("- ").strip())
        town, row = got
        base = self.fts(query, doc=doc or stem, **kw)
        hit = self._hit(kind="table", rec=rec, text=self.table_text(rec), snippet=row, score=-100.0)
        hit["town_matched"] = town
        hit["figure_transcription"] = True
        hits = [hit] + [h for h in (base.get("hits") or []) if h.get("id") != rec.get("id")]
        return {"found": True, "query": query, "type": "fts", "hits": hits,
                "figure_lookup": "IS 875 (Part 4):2021 Fig. 1 snow zone map (figure transcription, verify)"}

    def wind_map_reading(self, town: str) -> Optional[str]:
        """The IS 875-3 Fig. 1 figure-reading row for a town absent from Annex A, if one was read."""
        rec = self._figure_record("IS_875_Part_3_2015", "Fig. 1")
        if rec is None:
            return None
        got = self._figure_town_row(rec, town)
        return got[1] if got else None

    def city_lookup(self, query: str, doc: Optional[str] = None) -> Optional[dict[str, Any]]:
        """Town lookups go to IS 875-3 Annex A / IS 1893 Annex E, never to back covers.
        A town absent from both annexes returns an explicit not_tabulated miss."""
        city = find_city_in_query(query)
        if not city:
            return None
        rest = re.sub(re.escape(city), " ", query, flags=re.I)
        rest_tokens = [t for t in re.findall(r"[A-Za-z0-9]+", rest.lower())]
        allowed = {"zone", "seismic", "factor", "basic", "wind", "speed", "vb", "z", "earthquake", "for", "of",
                   "in", "at", "the", "city", "town", "is", "1893", "875", "part", "3", "annex", "a", "e",
                   "value", "what", "m/s", "ms", "velocity", "design", "and"}
        if len(rest_tokens) > 6 or any(t not in allowed for t in rest_tokens):
            return None
        variants = city_variants(city)
        pages = self._annex_town_pages()
        resolved = resolve_doc(doc) if doc else None
        order = ["IS_1893_Part_1_2016", "IS_875_Part_3_2015"] if re.search(
            r"zone|seismic|earthquake|1893", query, re.I) else ["IS_875_Part_3_2015", "IS_1893_Part_1_2016"]
        hits, not_tab = [], []
        for d in order:
            if resolved and resolved not in (d, "specification"):
                continue
            found = None
            for pg in pages:
                if pg["doc"] != d:
                    continue
                body = pg["body"] or ""
                for v in variants:
                    m = re.search(rf"(?<![A-Za-z]){re.escape(v)}(?![A-Za-z])", body, re.I)
                    if m:
                        found = (pg, v, m)
                        break
                if found:
                    break
            if not found:
                not_tab.append(d)
                continue
            pg, v, m = found
            line_start = body_start = pg["body"].rfind("\n", 0, m.start()) + 1
            line_end = pg["body"].find("\n", m.end())
            snippet = pg["body"][line_start : line_end if line_end > 0 else None].strip()
            rec = {
                "id": f"spec-page:{d}:standard:{pg['pdf_page']}",
                "doc": d,
                "edition": pg.get("edition"),
                "part": "standard",
                "collection": "specification",
                "corpus": "specification",
                "pdf_page": int(pg["pdf_page"]),
                "printed_label": pg.get("printed_label"),
                "title": "IS 1893 (Part 1) Annex E — zone factors for some important towns"
                if d.startswith("IS_1893")
                else "IS 875 (Part 3) Annex A — basic wind speed at 10 m height for some important cities/towns",
                "section_id": "Annex E" if d.startswith("IS_1893") else "Annex A",
            }
            h = self._hit(kind="annex_town", rec=rec, text=pg["body"], snippet=snippet, score=-100.0)
            h["town_matched"] = v
            hits.append(h)
        if not hits:
            miss = self._miss(query, "fts")
            miss.update(
                {
                    "found": False,
                    "not_tabulated": True,
                    "town": city,
                    "note": "Town not listed in IS 1893 (Part 1) Annex E or IS 875 (Part 3) Annex A: "
                    "use the zone map (IS 1893 Fig. 1) / basic wind speed map (IS 875-3 Fig. 1) — "
                    "image only, not in the text corpus. Towns on a zone boundary take the higher zone.",
                }
            )
            if not re.search(r"zone|seismic|earthquake|1893", query, re.I) and resolved != "IS_1893_Part_1_2016":
                row = self.wind_map_reading(city)
                if row:
                    # K07: an EOR-grade reading of the IS 875-3 Fig. 1 map exists for this town
                    miss["figure_reading"] = row
                    cells = [c.strip() for c in row.strip().strip("|").split("|")]
                    desc = (f"{cells[0]}: Vb = {cells[2]} m/s ({cells[1]}; {cells[3]})" if len(cells) >= 4
                            else "; ".join(cells))
                    miss["note"] += (" IS 875 (Part 3) Fig. 1 figure reading (official BIS text, verify): " + desc
                                     + " — exact_table \"Fig. 1\" --doc IS_875_Part_3_2015.")
            return miss
        res = {"found": True, "query": query, "type": "fts", "hits": hits}
        if not_tab:
            res["not_tabulated_in"] = not_tab
        return res

    def apply_conflicts(self, query: str, result: dict[str, Any]) -> dict[str, Any]:
        """R-factor queries: IS 1893 Table 9 first; IS 800 Table 23 flagged."""
        conf = (self.aliases or {}).get("conflicts") or []
        if not result.get("found"):
            return result
        for c in conf:
            trig = c.get("trigger") or []
            if not any(re.search(rf"(?<![\w]){re.escape(t)}(?![\w])", query, re.I) for t in trig):
                continue
            if re.search(r"response reduction|\bR\b", query, re.I) is None:
                continue
            pref = c.get("prefer") or {}
            flag = c.get("flag") or {}
            hits = result.get("hits") or []
            pref_hits = [h for h in hits if h.get("doc") == pref.get("doc") and (
                str(h.get("table_id")) == str(pref.get("table_id")) or str(h.get("pdf_page")) in ("22",))]
            if not pref_hits:
                t9 = self.exact_table(pref.get("table_id") or "9", doc=pref.get("doc"))
                pref_hits = (t9.get("hits") or [])[:1]
            others = [h for h in hits if h not in pref_hits]
            for h in others:
                if h.get("doc") == flag.get("doc") and (
                    str(h.get("table_id")) == str(flag.get("table_id")) or "Table 23" in (h.get("text") or "")[:4000]
                ):
                    h["conflict"] = flag.get("note")
                    h["superseded_by"] = "IS 1893 (Part 1):2016 Table 9 (verify precedence)"
            result["hits"] = pref_hits + others
            result["conflict_rule"] = "IS 1893 Table 9 governs R; IS 800 Table 23 flagged"
        return result

    def fts(
        self,
        query: str,
        doc: Optional[str] = None,
        want_commentary: bool = False,
        neighbors: int = 0,
        limit: int = 12,
        collection: Optional[str] = None,
    ) -> dict[str, Any]:
        # "... IS 800" / "IS 801 ..." in the query text becomes a document filter
        if not doc:
            m_doc = re.search(r"\bIS\s*:?\s*(\d{3,5})(?:\s*\(?\s*Part\s*(\d)\s*\)?)?(?:\s*[:-]\s*(\d{4}))?\b", query)
            if m_doc:
                key = f"is_{m_doc.group(1)}" + (f"_part_{m_doc.group(2)}" if m_doc.group(2) else "")
                cand = DOC_ALIASES.get(key)
                if cand and cand != "specification" and (m_doc.group(1) != "875" or m_doc.group(2)):
                    doc = cand
                    stripped = (query[: m_doc.start()] + " " + query[m_doc.end() :]).strip()
                    if stripped:
                        query = stripped
        self._table_hint = None
        m_t = re.search(r"\bTable\s+(\d{1,2}[A-Z]?)\b", query, re.I)
        if m_t:
            self._table_hint = m_t.group(1)
        variants = self._expand_query_terms(query)
        spec_rows: list[dict[str, Any]] = []
        p2_rows: list[dict[str, Any]] = []
        coll = resolve_doc(collection) if collection else None
        doc_r = resolve_doc(doc) if doc else None
        OPENSEES = {
            "opensees",
            "openseespy_documentation",
            "opensees_documentation",
            "opensees_buildings_3d",
            "opensees_building_templates",
        }
        EXAMPLES = {"examples", "steel_design_examples"}
        # doc=examples / steel_design_examples must not leak opensees (B31 etc.)
        if not coll:
            if doc_r in EXAMPLES:
                coll = "examples"
            elif doc_r in OPENSEES:
                coll = "opensees"
        if coll in EXAMPLES:
            coll = "examples"
        if coll in OPENSEES:
            coll = "opensees"
        want_spec = coll in (None, "specification") and doc_r not in (OPENSEES | EXAMPLES)
        want_p2 = coll in (None, "opensees", "examples") or (coll in OPENSEES | EXAMPLES)
        spec_ids = {
            "IS_800_2007",  # india
            "AISC_341_22",
            "AISC_358_22",
            "AISI_S100",
            "AISI_S230_19",
        "AISI_S240",
            "AISI_S400_20",
            "ASCE7",
            "specification",
        }
        if want_spec and (not doc_r or doc_r not in (OPENSEES | EXAMPLES)):
            spec_rows = self._fts_spec(variants, doc=doc, want_commentary=want_commentary, limit=limit)
        if want_p2 and (not doc_r or doc_r not in spec_ids or coll in ("opensees", "examples")):
            p2_rows = self._fts_phase2(variants, collection=coll, limit=limit)
        # merge: specification always ranks above opensees/examples when both match
        merged = []
        for r in spec_rows:
            r["_corpus_rank"] = 0
            merged.append(r)
        for r in p2_rows:
            r["_corpus_rank"] = 1 if r.get("corpus") == "opensees" else 2
            merged.append(r)
        merged.sort(key=lambda r: (r["_corpus_rank"], r.get("score", 0)))  # bm25 more negative is better; we store raw
        # actually sqlite bm25 is more-negative=better; keep that and sort ascending within corpus
        merged.sort(key=lambda r: (r["_corpus_rank"], r.get("score") if r.get("score") is not None else 0))
        hits = []
        for r in merged[:limit]:
            rec = r.get("rec") or {}
            text = r.get("text") or ""
            if neighbors and rec:
                _, neigh = self.section_extract(rec, neighbors=neighbors) if rec.get("section_id") else ([], [])
            else:
                neigh = []
            hit = self._hit(
                kind="fts",
                rec=rec if rec else r,
                text=text,
                neighbors=neigh,
                snippet=r.get("snippet"),
                score=r.get("score"),
            )
            # fill from fts row if rec is thin
            for k in ("doc", "section_id", "eq_id", "table_id", "part", "pdf_page", "printed_label", "title"):
                if hit.get(k) in (None, "") and r.get(k) not in (None, ""):
                    hit[k] = r[k]
            hit["corpus"] = r.get("corpus") or hit["corpus"]
            hit["collection"] = r.get("collection") or hit["collection"]
            hits.append(hit)
        if not hits:
            # retry keyword as last FTS miss before caller tries keyword
            return {
                "found": False,
                **self._miss(
                    query,
                    "fts",
                    suggestions=self._suggest_ids(query, "section"),
                    aliases=self._alias_suggestions(query),
                ),
            }
        return {"found": True, "query": query, "type": "fts", "hits": hits}

    def _fts_spec(
        self,
        variants: list[str],
        doc: Optional[str],
        want_commentary: bool,
        limit: int,
    ) -> list[dict[str, Any]]:
        con = self.spec_fts()
        rows: list[dict[str, Any]] = []
        seen = set()
        resolved = resolve_doc(doc) if doc else None
        for q in variants:
            match = fts_escape(fts_normalize(q))
            if not match:
                continue
            sql = (
                "SELECT rec_id, kind, doc, edition, section_id, eq_id, table_id, part, "
                "collection, pdf_page, printed_label, title, body, flags, "
                "snippet(spec_fts, 12, '>>', '<<', '...', 40) AS snip, "
                "bm25(spec_fts) AS score "
                "FROM spec_fts WHERE spec_fts MATCH ? "
            )
            args: list[Any] = [match]
            if resolved and resolved not in ("specification", "opensees", "examples"):
                sql += " AND doc = ? "
                args.append(resolved)
            if not want_commentary:
                # still allow commentary but we rank standard first via part
                pass
            sql += " ORDER BY bm25(spec_fts) LIMIT ?"
            args.append(max(limit * 8, 60))
            try:
                cur = con.execute(sql, args)
            except sqlite3.OperationalError:
                continue
            for r in cur:
                rec_id = r["rec_id"]
                if rec_id in seen:
                    continue
                if not want_commentary and r["part"] == "commentary":
                    # keep but de-prioritize: add offset to score (more positive = worse)
                    score = (r["score"] or 0) + 20.0
                else:
                    score = r["score"]
                tid = (r["table_id"] or "") if "table_id" in r.keys() else ""
                qjoin = " ".join(variants).lower()
                if tid == "12.2-1" and any(
                    k in qjoin
                    for k in (
                        "moment frame",
                        "braced frame",
                        "wood structural",
                        "flat strap",
                        "cold-formed steel",
                        "buckling-restrained",
                    )
                ):
                    score = (score or 0) - 15.0
                # India BIS: lightly demote cover/foreword pages so body clauses
                # outrank title-page boilerplate when both match (bm25 more
                # negative = better; add positive offset to demote).
                title_l = (r["title"] or "").lower()
                page_no = r["pdf_page"] or 0
                try:
                    page_no = int(page_no)
                except Exception:
                    page_no = 0
                if page_no and page_no <= 3:
                    score = (score or 0) + 4.0
                if any(
                    k in title_l
                    for k in ("foreword", "contents", "committee", "cover")
                ):
                    score = (score or 0) + 6.0
                flags = (r["flags"] or "") if "flags" in r.keys() else ""
                # BIS back matter (regional/branch office lists, "Published by BIS")
                # and committee-composition pages are demoted hard (CORPUS-11)
                if "backmatter" in flags:
                    score = (score or 0) + 40.0
                if "committee" in flags:
                    score = (score or 0) + 25.0
                if "amendment_sheet" in flags:
                    score = (score or 0) + 3.0
                if "contents" in flags:
                    score = (score or 0) + 8.0     # K05: a contents page is never the clause asked for
                if r["kind"] in ("section", "table"):
                    score = (score or 0) - 1.5
                # topical prior: the governing IS for the query topic ranks first
                for rx, dstem in TOPIC_PRIOR:
                    if r["doc"] == dstem and rx.search(qjoin):
                        score = (score or 0) - 4.0
                        break
                hint = getattr(self, "_table_hint", None)
                if hint and r["kind"] == "table" and str(r["table_id"] or "") == hint:
                    score = (score or 0) - 8.0
                seen.add(rec_id)
                rec = {
                    "id": rec_id,
                    "doc": r["doc"],
                    "edition": r["edition"],
                    "section_id": r["section_id"],
                    "eq_id": r["eq_id"],
                    "table_id": r["table_id"],
                    "part": r["part"],
                    "collection": "specification",
                    "corpus": "specification",
                    "pdf_page": r["pdf_page"],
                    "printed_label": r["printed_label"],
                    "title": r["title"],
                }
                rows.append(
                    {
                        "rec": rec,
                        "corpus": "specification",
                        "collection": "specification",
                        "text": r["body"] or "",
                        "snippet": r["snip"],
                        "score": score,
                        "doc": r["doc"],
                        "section_id": r["section_id"],
                        "eq_id": r["eq_id"],
                        "table_id": r["table_id"],
                        "part": r["part"],
                        "pdf_page": r["pdf_page"],
                        "printed_label": r["printed_label"],
                        "title": r["title"],
                        "kind": r["kind"],
                        "flags": flags,
                    }
                )
        rows.sort(key=lambda r: r.get("score") or 0)
        # collapse hits by (doc, pdf_page): one page must not fill the top-k (CORPUS-11)
        best: dict[tuple, dict[str, Any]] = {}
        kind_rank = {"table": 0, "section": 1, "equation": 2, "page": 3}
        for r in rows:
            k = (r.get("doc"), str(r.get("pdf_page")))
            cur = best.get(k)
            if cur is None:
                best[k] = r
                r["collapsed"] = 0
                continue
            cur["collapsed"] = cur.get("collapsed", 0) + 1
            # a table/section row within 1.0 of the best page score is more useful than the page
            if kind_rank.get(r.get("kind"), 9) < kind_rank.get(cur.get("kind"), 9) and (r.get("score") or 0) <= (
                cur.get("score") or 0
            ) + 1.0:
                r["collapsed"] = cur["collapsed"]
                best[k] = r
        out = sorted(best.values(), key=lambda r: r.get("score") or 0)
        # back matter / committee pages only when nothing else matched
        main = [r for r in out if not re.search(r"backmatter|committee", r.get("flags") or "")]
        if main:
            out = main + [r for r in out if r not in main]
        return out[:limit]

    def _fts_phase2(
        self, variants: list[str], collection: Optional[str], limit: int
    ) -> list[dict[str, Any]]:
        con = self.p2_fts()
        if con is None:
            return []
        rows: list[dict[str, Any]] = []
        seen = set()
        byid = {s.get("id"): s for s in self.sections if s.get("collection") != "specification"}
        for q in variants:
            match = fts_escape(q)
            if not match:
                continue
            sql = (
                "SELECT chunk_id, collection, title, nav, "
                "snippet(chunks, 6, '>>', '<<', '...', 40) AS snip, "
                "bm25(chunks) AS score FROM chunks WHERE chunks MATCH ?"
            )
            args: list[Any] = [match]
            coll_filter = collection
            if coll_filter in ("opensees", "examples"):
                # filter after, using group
                pass
            elif coll_filter:
                sql += " AND collection = ?"
                args.append(coll_filter)
            sql += " ORDER BY bm25(chunks) LIMIT ?"
            args.append(limit * 2)
            try:
                cur = con.execute(sql, args)
            except sqlite3.OperationalError:
                continue
            for r in cur:
                cid = r["chunk_id"]
                if cid in seen:
                    continue
                coll = r["collection"]
                group = "examples" if coll == "steel_design_examples" else "opensees"
                if collection == "opensees" and group != "opensees":
                    continue
                if collection == "examples" and group != "examples":
                    continue
                seen.add(cid)
                rec = byid.get(cid) or {
                    "id": cid,
                    "collection": group,
                    "source_collection": coll,
                    "title": r["title"],
                    "part": "n/a",
                    "corpus": group,
                    "doc": coll,
                }
                rec = dict(rec)
                rec["collection"] = group
                rec["corpus"] = group
                rec["source_collection"] = coll
                rec["doc"] = rec.get("doc") or coll
                rec["part"] = "n/a"
                body = self._phase2_body(rec) if rec.get("file") else ""
                rows.append(
                    {
                        "rec": rec,
                        "corpus": group,
                        "collection": group,
                        "text": body,
                        "snippet": r["snip"],
                        "score": r["score"],
                        "doc": rec.get("doc"),
                        "section_id": rec.get("id"),
                        "title": rec.get("title"),
                        "part": "n/a",
                    }
                )
            if len(rows) >= limit:
                break
        rows.sort(key=lambda r: r.get("score") or 0)
        return rows[:limit]

    def keyword(
        self,
        query: str,
        doc: Optional[str] = None,
        want_commentary: bool = False,
        neighbors: int = 0,
        limit: int = 12,
        collection: Optional[str] = None,
    ) -> dict[str, Any]:
        terms = [nfkc(t).casefold() for t in re.findall(r"\S+", query) if t]
        if not terms:
            return {"found": False, **self._miss(query, "keyword")}
        # expand aliases into alternative term sets (OR of a term's aliases)
        alt = []
        for t in terms:
            al = [nfkc(x).casefold() for x in self.alias_map.get(t, [])]
            alt.append(list(dict.fromkeys([t, *al])))

        def matches_text(text: str) -> bool:
            blob = nfkc(text).casefold()
            return all(any(a in blob for a in group) for group in alt)

        hits: list[dict[str, Any]] = []
        # titles first
        for s in self.sections:
            if not self._doc_ok(s, doc):
                continue
            if collection and not self._doc_ok(s, collection) and s.get("collection") != resolve_doc(collection):
                continue
            if s.get("collection") == "specification" and not self._part_ok(s, want_commentary, exact=True):
                continue
            title = s.get("title") or ""
            sid = s.get("section_id") or s.get("id") or ""
            cmd = s.get("command") or ""
            if matches_text(f"{title} {sid} {cmd}"):
                if s.get("collection") == "specification":
                    text, neigh = self.section_extract(s, neighbors=neighbors)
                else:
                    text, neigh = self._phase2_body(s), []
                hits.append(self._hit(kind="keyword", rec=s, text=text, neighbors=neigh))
            if len(hits) >= limit:
                break
        if len(hits) < limit:
            for e in self.equations:
                if not e.get("eq_id"):
                    continue
                if not self._doc_ok(e, doc):
                    continue
                if e.get("collection") == "specification" and not self._part_ok(
                    e, want_commentary, exact=True
                ):
                    continue
                blob = " ".join(
                    str(x) for x in (e.get("eq_id"), e.get("orig"), e.get("nearby_text"), e.get("latex")) if x
                )
                if matches_text(blob):
                    hits.append(self._hit(kind="keyword", rec=e, text=self.equation_text(e)))
                if len(hits) >= limit:
                    break
        if len(hits) < limit:
            for t in self.tables:
                if not t.get("table_id"):
                    continue
                if not self._doc_ok(t, doc):
                    continue
                blob = " ".join(str(x) for x in (t.get("table_id"), t.get("title"), t.get("markdown_excerpt")) if x)
                if matches_text(blob):
                    hits.append(self._hit(kind="keyword", rec=t, text=self.table_text(t)))
                if len(hits) >= limit:
                    break
        if not hits:
            return {
                "found": False,
                **self._miss(
                    query,
                    "keyword",
                    suggestions=self._suggest_ids(query, "section"),
                    aliases=self._alias_suggestions(query),
                ),
            }
        return {"found": True, "query": query, "type": "keyword", "hits": hits}

    def search(
        self,
        type_: str,
        query: str,
        doc: Optional[str] = None,
        want_commentary: bool = False,
        neighbors: int = 1,
        limit: int = 12,
        collection: Optional[str] = None,
        purpose: Optional[str] = None,
    ) -> dict[str, Any]:
        """Lookup order: exact section/eq/table, then FTS, then keyword, then alias expansion."""
        t = (type_ or "auto").lower().strip()
        t = TYPE_MAP.get(t, t)
        missing = self.unknown_doc(doc) or (
            self.unknown_doc(collection)
            if collection and re.match(r"(?i)^(engineering_standards?_)?(IS|BIS)[ _-]?\d", collection) else None)
        if missing:
            # NEW-3: a document this corpus never held is not a term absent from a document it holds
            out = self._miss(query, t)
            out.update(type=t, document_not_in_corpus=True, document=missing,
                       indexed_docs=sorted(self.doc_meta), purpose=purpose, want_commentary=want_commentary,
                       note=f"{missing} is not in the corpus")
            return out
        if t in ("fts", "keyword", "auto", "id", "exact_section"):
            us = self.us_term_miss(query)
            if us is not None:
                us["purpose"] = purpose
                us["want_commentary"] = want_commentary
                return us
        if t in ("fts", "auto", "keyword"):
            sn = self.snow_town_lookup(query, doc=doc, want_commentary=want_commentary, neighbors=neighbors,
                                       limit=limit, collection=collection)
            if sn is not None:
                sn["purpose"] = purpose
                sn["want_commentary"] = want_commentary
                return sn
            cl = self.city_lookup(query, doc=doc)
            if cl is not None:
                cl["purpose"] = purpose
                cl["want_commentary"] = want_commentary
                return cl
        kw = dict(
            doc=doc,
            want_commentary=want_commentary,
            neighbors=neighbors,
            limit=limit,
        )
        if t == "exact_section":
            result = self.exact_section(query, **kw)
        elif t == "exact_equation":
            kw["limit"] = max(int(kw.get("limit") or 12), 40)
            result = self.exact_equation(query, **kw)
        elif t == "exact_table":
            result = self.exact_table(query, **kw)
        elif t == "id":
            result = self.exact_id(query, **kw)
        elif t == "command":
            result = self.command(query, doc=doc, limit=limit)
        elif t == "fts":
            result = self.fts(query, collection=collection, **kw)
        elif t == "keyword":
            result = self.keyword(query, collection=collection, **kw)
        else:
            # auto pipeline
            result = self.exact_id(query, **kw)
            if not result.get("found"):
                result = self.fts(query, collection=collection, **kw)
            if not result.get("found"):
                result = self.keyword(query, collection=collection, **kw)
        if not result.get("found"):
            # alias expansion retry
            al = self._alias_suggestions(query)
            if al and t in ("fts", "keyword", "auto"):
                for a in al[:6]:
                    if a == query:
                        continue
                    retry = self.fts(a, collection=collection, **kw) if t != "keyword" else self.keyword(
                        a, collection=collection, **kw
                    )
                    if retry.get("found"):
                        retry["alias_expanded_from"] = query
                        retry["alias_used"] = a
                        result = retry
                        break
            if not result.get("found") and t in ("exact_section", "id"):
                # maybe the query is an alias for a phrase — try FTS on aliases
                al = self._alias_suggestions(query)
                if al:
                    phrase = al[0]
                    retry = self.fts(phrase, collection=collection, **kw)
                    if retry.get("found"):
                        retry["alias_expanded_from"] = query
                        retry["alias_used"] = phrase
                        result = retry
        if t in ("fts", "auto", "keyword"):
            result = self.apply_conflicts(query, result)
        result["purpose"] = purpose
        result["want_commentary"] = want_commentary
        return result

    def run_query(self, q: dict[str, Any]) -> dict[str, Any]:
        bypass = bool(q.get("bypass_cache")) or os.environ.get("QFM_NO_CACHE") == "1"
        neighbors = q.get("context_neighbors")
        if neighbors is None:
            neighbors = q.get("neighbors") or 0
        try:
            neighbors = int(neighbors or 0)
        except (TypeError, ValueError):
            neighbors = 0
        q_for_cache = dict(q)
        q_for_cache["neighbors"] = neighbors
        if not bypass:
            from query_cache import QueryCache

            cache = QueryCache(self.root)
            hit = cache.get(q_for_cache)
            if hit is not None:
                result = json.loads(json.dumps(hit, ensure_ascii=False))
                result["cached"] = True
                result["qid"] = q.get("qid")
                result["purpose"] = q.get("purpose")
                return result
        result = self.search(
            type_=q.get("type") or "auto",
            query=q.get("query") or "",
            doc=q.get("doc"),
            want_commentary=bool(q.get("want_commentary")),
            neighbors=neighbors,
            limit=int(q.get("limit") or 12),
            collection=q.get("collection"),
            purpose=q.get("purpose"),
        )
        result["qid"] = q.get("qid")
        if not bypass:
            from query_cache import QueryCache

            cache = QueryCache(self.root)
            cache.put(q_for_cache, result)
            result["cached"] = False
        return result


def format_cli_result(result: dict[str, Any], max_text: int = 40000) -> dict[str, Any]:
    """Copy result with truncated verbatim text so stdout stays usable."""
    out = dict(result)
    hits = []
    for h in result.get("hits") or []:
        hh = dict(h)
        text = hh.get("text") or ""
        if len(text) > max_text:
            hh["text"] = text[:max_text]
            hh["text_truncated"] = True
            hh["text_chars"] = len(text)
        hits.append(hh)
    out["hits"] = hits
    return out
