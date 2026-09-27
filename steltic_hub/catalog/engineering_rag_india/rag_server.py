#!/usr/bin/env python3
"""IS corpus grounding bridge: the design agents' standards-search API, answered from the India corpus.

The HR Steel (IS 800) and CFS (IS 801) agents call ONE small HTTP API for spec grounding:

    POST /query   {"query": "...", "collection": "engineering_standards_IS1893",
                   "top_k": 5, "clause": "7.6.4", "stem": "IS_1893_Part_1_2016"}   -> {"results": [...]}

It was written for a hosted vector database. This server answers the same API from the IS corpus
workspace instead -- the full-text + exact-id index that the bundled `scripts/build_index.py` builds
over the BIS documents YOU converted from your own licensed PDFs. No embeddings, no vector store,
nothing leaves this PC. The hub ships no standard text: a fresh install is an empty corpus, and every
question is answered "not in the corpus" (an empty `results` list with a note) until you convert.

Collections the agents ask for (the names in steltic_india's `india_collections.py`) are mapped onto
the canonical corpus document stems:

    engineering_standards_IS800             -> IS_800_2007
    engineering_standards_IS801 / IS811     -> IS_801_1975 / IS_811_1987
    engineering_standards_IS808 / IS1161    -> IS_808_2021 / IS_1161_2014
    engineering_standards_IS875_P1 .. P5    -> IS_875_Part_1_2026 .. IS_875_Part_5_1987
    engineering_standards_IS1893            -> IS_1893_Part_1_2016 (+ Amd 1, Amd 2 consolidated)
    engineering_standards_IS2062            -> IS_2062_Part_1_2025
    engineering_standards_IS18168           -> IS_18168_2023
    engineering_standards_IS816 / IS9595 / IS4000  -> connections (IS 816, IS 9595, IS 4000)

Anything else goes through the corpus's own `resolve_doc` alias table (`IS 800`, `is_875_3`,
`IS 875 (Part 4):2021` ...). A request that carries `stem` / `doc` (steltic_india sends both) is
honoured when the collection name is unknown. The OpenSees / worked-example collections of the US
Query file manager are NOT part of the India corpus: they answer with an empty `results` list and a
note, which the agents treat as "no hit" (a non-2xx would pause their run).

A `clause` filter becomes an exact section / equation / table lookup first; `chapter` keeps hits
from that chapter; everything else is full-text search with the corpus's own alias expansion,
town lookup (IS 875-3 Annex A / IS 1893 Annex E) and US-term trap (`found:false` with the IS
equivalent). A document that is not in the corpus answers with an empty `results` list.

Runs in the module's own environment (stdlib + sqlite3, like the rest of the corpus scripts).
Started by the hub:

    rag_server.py --root <workspace> --port <port>

GET /          a status page (what is indexed, what the agents asked for lately)
GET /healthz   {"ok": true, ...}
"""
from __future__ import annotations
import argparse, html, json, re, sys, threading, time, traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

# ---------------------------------------------------------------- collection mapping
# engineering_standards_<KEY> -> corpus stem. Mirrors STEM_TO_COLLECTION in steltic_india's
# india_collections.py and the canonical stems of the Convert tab.
SPEC = {
    "IS800": "IS_800_2007", "IS_800": "IS_800_2007",
    "IS801": "IS_801_1975", "IS_801": "IS_801_1975",
    "IS811": "IS_811_1987", "IS_811": "IS_811_1987", "IS811_AMD1": "IS_811_1987_Amd1_2011",
    "IS808": "IS_808_2021", "IS_808": "IS_808_2021",
    "IS816": "IS_816_1969", "IS_816": "IS_816_1969",
    "IS1161": "IS_1161_2014", "IS_1161": "IS_1161_2014",
    "IS2062": "IS_2062_Part_1_2025", "IS_2062": "IS_2062_Part_1_2025", "IS2062_P1": "IS_2062_Part_1_2025",
    "IS4000": "IS_4000_1992", "IS_4000": "IS_4000_1992",
    "IS9595": "IS_9595_1996", "IS_9595": "IS_9595_1996",
    "IS875_P1": "IS_875_Part_1_2026", "IS_875_P1": "IS_875_Part_1_2026", "IS875_PART1": "IS_875_Part_1_2026",
    "IS875_P2": "IS_875_Part_2_1987", "IS_875_P2": "IS_875_Part_2_1987", "IS875_PART2": "IS_875_Part_2_1987",
    "IS875_P3": "IS_875_Part_3_2015", "IS_875_P3": "IS_875_Part_3_2015", "IS875_PART3": "IS_875_Part_3_2015",
    "IS875_P4": "IS_875_Part_4_1987", "IS_875_P4": "IS_875_Part_4_1987", "IS875_PART4": "IS_875_Part_4_1987",
    "IS875_P5": "IS_875_Part_5_1987", "IS_875_P5": "IS_875_Part_5_1987", "IS875_PART5": "IS_875_Part_5_1987",
    "IS1893": "IS_1893_Part_1_2016", "IS_1893": "IS_1893_Part_1_2016", "IS1893_P1": "IS_1893_Part_1_2016",
    "IS1893_PART1": "IS_1893_Part_1_2016",
    "IS18168": "IS_18168_2023", "IS_18168": "IS_18168_2023", "IS18168_2023": "IS_18168_2023",
}
# The stems themselves are accepted as collection names too.
for _stem in ("IS_800_2007", "IS_801_1975", "IS_808_2021", "IS_811_1987", "IS_811_1987_Amd1_2011", "IS_816_1969",
              "IS_1161_2014", "IS_2062_Part_1_2025", "IS_4000_1992", "IS_9595_1996", "IS_875_Part_1_2026",
              "IS_875_Part_2_1987", "IS_875_Part_3_2015", "IS_875_Part_4_1987", "IS_875_Part_5_1987",
              "IS_1893_Part_1_2016", "IS_18168_2023"):
    SPEC[_stem.upper()] = _stem
# The US Query file manager's bundled collections. Not in the India corpus; kept so a request for
# them is answered with a note instead of "unknown collection" (the agents' contract still names
# them for OpenSees API lookups). If a phase2_fts.sqlite is ever dropped into the workspace, the
# corpus's own retrieval serves it.
PHASE2 = {
    "steel_design_examples": ("examples", "steel_design_examples"),
    "cfs_design_examples": ("examples", "steel_design_examples"),
    "examples": ("examples", None),
    "opensees_buildings_3d": ("opensees", "opensees_buildings_3d"),
    "opensees_building_templates": ("opensees", "opensees_building_templates"),
    "openseespy_documentation": ("opensees", "openseespy_documentation"),
    "opensees_documentation": ("opensees", "opensees_documentation"),
    "opensees": ("opensees", None),
}
PREFIXES = ("engineering_standards_", "engineering_standard_")


def map_collection(name: str):
    """-> ("spec", doc_stem) | ("phase2", group, source_collection | None) | None"""
    n = (name or "").strip()
    if not n or n.lower() in ("specification", "spec", "standards"):
        return ("spec", None)
    if n in PHASE2:
        g, src = PHASE2[n]
        return ("phase2", g, src)
    key = n
    for pre in PREFIXES:
        if key.lower().startswith(pre):
            key = key[len(pre):]
            break
    key_u = key.upper().replace("-", "_")
    if key_u in SPEC:
        return ("spec", SPEC[key_u])
    # "IS 800", "is_875_3", "IS 875 (Part 4):2021", "engineering_standards_is875_p3" -- the corpus's
    # own alias table decides (retrieval.py DOC_ALIASES, present once sys.path is set)
    try:
        from retrieval import resolve_doc     # noqa: F401
        for cand in (n, key):
            r = resolve_doc(cand)
            if r and r != cand and r.upper() in SPEC:
                return ("spec", r)
    except Exception:
        pass
    return None


_COMMENT = re.compile(r"^\s*(<!--.*?-->\s*)+", re.S)
_EQ = re.compile(r"^(?:[A-Z]{1,2}\d+(?:\.\d+)*-\d+[a-z]?|\d+(?:\.\d+)*-eq\d+)$", re.I)


def _clean(text: str) -> str:
    text = _COMMENT.sub("", text or "")          # chunk_id / meta header comments carry no content
    return text.strip()


def shape_hit(h: dict) -> dict:
    """One corpus hit -> the shape the agents render (text + a few meta keys)."""
    doc = h.get("doc") or h.get("source_collection") or h.get("collection") or ""
    sid = h.get("section_id") or h.get("eq_id") or h.get("table_id") or ""
    title = h.get("title") or ""
    part = h.get("part") or ""
    head = " ".join(x for x in (doc, sid, f"({part})" if part and part != "n/a" else "") if x)
    body = _clean(h.get("text") or h.get("snippet") or "")
    lines = [f"[{head}] {title}".strip()] if head or title else []
    lines.append(body)
    for n in (h.get("neighbors") or [])[:2]:
        if isinstance(n, dict) and n.get("text"):
            lines.append("")
            lines.append(f"-- {n.get('section_id') or ''} {n.get('title') or ''}".rstrip())
            lines.append(_clean(n["text"]))
    return {
        "text": "\n".join(lines).strip(),
        "score": h.get("score"),
        "source": doc,
        "section": sid,
        "title": title,
        "page": h.get("printed_label") or h.get("pdf_page"),
        "id": h.get("id"),
        "part": part,
        "authoritative": bool(h.get("authoritative")),
    }


def _miss_note(r: dict) -> str:
    """The corpus's own explanation of an empty answer, in one line: the US-term trap ("SDS is not
    defined in IS 1893; use Z, I, Sa/g"), a town in neither annex (not_tabulated), nearest ids."""
    if not isinstance(r, dict) or r.get("found"):
        return ""
    parts = []
    if r.get("us_term"):
        parts.append(f"US term {r['us_term']!r}: {r.get('note') or 'not defined in the IS documents'}")
        if r.get("is_equivalent"):
            parts.append(f"IS equivalent: {r['is_equivalent']}")
    elif r.get("not_tabulated"):
        parts.append(r.get("note") or "town not tabulated in IS 875-3 Annex A / IS 1893 Annex E (read the map)")
    elif r.get("note"):
        parts.append(str(r["note"]))
    ids = r.get("nearest_ids") or []
    if ids:
        parts.append("nearest ids: " + ", ".join(str(i) for i in ids[:6]))
    return "; ".join(str(x) for x in parts)[:400]


# ---------------------------------------------------------------- corpus access
class Bridge:
    def __init__(self, root: Path, scripts: Path):
        self.root = root
        self.scripts = scripts
        self.lock = threading.Lock()
        self._corpus = None
        self._stamp = None
        self.recent: list[dict] = []
        self.started = time.time()
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        self._preload()

    def _preload(self):
        """The status page survives a restart: the last queries come back from the log."""
        try:
            p = self.root / "queue" / "agent_queries.jsonl"
            if p.is_file():
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
                for ln in lines:
                    try:
                        rec = json.loads(ln)
                        if isinstance(rec, dict) and "query" in rec:
                            self.recent.append(rec)
                    except Exception:
                        pass
        except Exception:
            pass

    # the indexes change whenever the user converts a PDF or rebuilds: reload on any mtime change.
    # So does the retrieval code, whenever the hub's Update copies a new scripts/ into the workspace:
    # Python caches the imported module, so without this the server answered with the OLD query
    # builder until someone restarted it -- while the Modules page reported the module up to date.
    def _index_stamp(self):
        out = []
        for rel in ("indexes/sections.json", "indexes/documents.json", "indexes/equations.json",
                    "indexes/tables.json", "search/spec_fts.sqlite", "search/phase2_fts.sqlite"):
            p = self.root / rel
            try:
                out.append((rel, p.stat().st_mtime_ns))
            except OSError:
                out.append((rel, None))
        for name in ("retrieval.py", "pipeline_fixes.py"):
            p = self.scripts / name
            try:
                out.append((name, p.stat().st_mtime_ns))
            except OSError:
                out.append((name, None))
        return tuple(out)

    def _code_changed(self, old_stamp, new_stamp):
        return any(a != b for a, b in zip(old_stamp or (), new_stamp) if a[0].endswith(".py"))

    def corpus(self):
        stamp = self._index_stamp()
        with self.lock:
            if self._corpus is None or stamp != self._stamp:
                if self._corpus is not None and self._code_changed(self._stamp, stamp):
                    import importlib
                    for name in ("pipeline_fixes", "retrieval"):
                        if name in sys.modules:
                            importlib.reload(sys.modules[name])
                from retrieval import Corpus
                old = self._corpus
                self._corpus = Corpus(self.root)
                self._stamp = stamp
                if old is not None:
                    try:
                        old.close()          # the replaced corpus still holds the old FTS files open
                    except Exception:
                        pass
            return self._corpus

    def release(self):
        """Drop the FTS file handles, keeping the parsed JSON indexes (the expensive part) cached.

        Held between requests they make `Rebuild index` impossible on Windows: an open sqlite
        handle blocks the unlink, and the rebuild dies with WinError 32. Re-opening costs about a
        millisecond, and this server serves one request at a time, so it is released the moment a
        query is answered. Older corpora without close() are simply left alone."""
        c = self._corpus
        if c is None:
            return
        try:
            c.close_fts()
        except AttributeError:
            pass
        except Exception:
            pass

    # ----- what is on this machine -----
    def status(self) -> dict:
        std = self.root / "documents" / "standards"
        pdfs = sorted(p.name for p in std.rglob("*.pdf")) if std.is_dir() else []
        # a converted document is documents/standards/<STEM>/markdown/<STEM>.search.md
        converted = sorted(d.name for d in std.iterdir()
                           if d.is_dir() and (d / "markdown" / f"{d.name}.search.md").is_file()) if std.is_dir() else []
        spec_index = (self.root / "search" / "spec_fts.sqlite").is_file()
        p2_index = (self.root / "search" / "phase2_fts.sqlite").is_file()
        docs, quality = [], {}
        try:
            if not spec_index or not (self.root / "indexes" / "documents.json").is_file():
                raise FileNotFoundError("empty corpus")
            meta = self.corpus().doc_meta
            docs = sorted(str(k) for k in meta)
            quality = {k: (v.get("quality") or "") for k, v in meta.items() if isinstance(v, dict)}
        except Exception:
            pass
        self.release()
        return {"ok": True, "root": str(self.root), "jurisdiction": "india", "empty": not docs,
                "pdfs": pdfs, "converted": converted,
                "spec_index": spec_index, "phase2_index": p2_index, "indexed_docs": docs, "quality": quality,
                "queries": len(self.recent), "uptime_s": int(time.time() - self.started)}

    # ----- the agents' call -----
    def query(self, body: dict) -> dict:
        q = str(body.get("query") or "").strip()
        collection = str(body.get("collection") or body.get("doc") or "")
        clause = str(body.get("clause") or "").strip()
        chapter = str(body.get("chapter") or "").strip()
        # the retrieval policy's fields (QUERYING_IS_CORPUS.md in the HR / CFS / nonlinear repositories): an exact type with the id
        # alone in `query`, provisions unless commentary is asked for, and how much context around it
        qtype = str(body.get("type") or "").strip().lower()
        if qtype in ("exact_section", "exact_equation", "exact_table", "id") and q and not clause:
            clause, q = q, ""
        want_commentary = bool(body.get("want_commentary"))
        try:
            neighbors = max(0, min(int(body.get("context_neighbors")), 2)) if body.get("context_neighbors") is not None else None
        except (TypeError, ValueError):
            neighbors = None
        try:
            top_k = max(1, min(int(body.get("top_k") or 5), 20))
        except (TypeError, ValueError):
            top_k = 5
        t0 = time.time()
        note = ""
        matched = ""
        hits: list[dict] = []
        target = map_collection(collection)
        if target is None and collection:
            # steltic_india sends the stem beside the collection name ("stem" / "doc"); a document the
            # user converted under a stem the fixed table does not know is still in the corpus, and
            # /healthz lists it under indexed_docs, so an agent that read /healthz may ask by that name.
            key = collection
            for pre in PREFIXES:
                if key.lower().startswith(pre):
                    key = key[len(pre):]
                    break
            try:
                meta = self.corpus().doc_meta
                for cand in (str(body.get("stem") or ""), str(body.get("doc") or ""), key, collection):
                    if cand and cand in meta:
                        target = ("spec", cand)
                        break
            except Exception:
                pass
        try:
            if target is None:
                note = (f"unknown collection {collection!r} -- the India corpus serves engineering_standards_IS800, "
                        f"IS801, IS808, IS811, IS816, IS1161, IS2062, IS4000, IS9595, IS875_P1..P5, IS1893, IS18168")
            elif target[0] == "spec":
                hits, note, matched = self._spec(q, target[1], clause, chapter, top_k, qtype=qtype,
                                                 want_commentary=want_commentary, neighbors=neighbors)
            else:
                hits, note = self._phase2(q, target[1], target[2], top_k)
        except Exception as e:                       # never a 5xx: that pauses the agent's run
            note = f"{type(e).__name__}: {e}"
            traceback.print_exc()
        self.release()                     # never hold the index between queries -- see release()
        results = [shape_hit(h) for h in hits[:top_k]]
        rec = {"t": time.time(), "collection": collection, "query": q, "clause": clause, "chapter": chapter,
               "type": qtype, "matched": matched,
               "hits": len(results), "ms": int((time.time() - t0) * 1000), "note": note}
        self.recent.append(rec)
        del self.recent[:-200]
        self._log(rec)
        out = {"results": results, "collection": collection, "count": len(results),
               "matched": matched}   # exact_section / exact_equation / exact_table / fts / auto / keyword / ""
        if note:
            out["note"] = note
        return out

    def _spec(self, q: str, doc: str | None, clause: str, chapter: str, top_k: int,
              qtype: str = "", want_commentary: bool = False, neighbors=None):
        # An empty corpus is the normal state of a fresh install (the hub ships no standard text):
        # answer "not in the corpus" without touching the retrieval code, which needs built indexes.
        if not (self.root / "search" / "spec_fts.sqlite").is_file() or \
                not (self.root / "indexes" / "documents.json").is_file():
            return [], (f"{doc or 'the IS documents'}: not in the corpus -- the IS corpus on this PC is empty "
                        f"(convert your licensed BIS PDFs: Admin → Standards, or the IS corpus Convert tab, "
                        f"then Rebuild index); cite from memory and flag every value to verify"), ""
        c = self.corpus()
        if doc and doc not in c.doc_meta:
            return [], (f"{doc} is not in the corpus on this PC -- convert your licensed copy on the IS corpus "
                        f"module's Convert tab with the canonical stem {doc}, then Rebuild index"), ""
        if not c.doc_meta:
            return [], ("not in the corpus -- the IS corpus on this PC holds no converted document yet "
                        "(Admin → Standards, or the IS corpus Convert tab, then Rebuild index)"), ""
        hits: list[dict] = []
        matched = ""                                # which lookup answered: the caller must know
        miss_note = ""                              # the corpus's own reason for an empty answer
        if clause:
            if qtype in ("exact_section", "exact_equation", "exact_table"):
                kinds = (qtype,)                     # the policy named the kind: no guessing
            else:
                kinds = (("exact_equation",) if _EQ.match(clause) else ()) + ("exact_section", "exact_table")
            for kind in kinds:
                r = c.search(kind, clause, doc=doc, want_commentary=want_commentary,
                             neighbors=1 if neighbors is None else neighbors, limit=top_k)
                if r.get("found") and r.get("hits"):
                    hits = r["hits"]
                    matched = kind
                    break
        if not hits and q:
            first = "keyword" if qtype == "keyword" else ("fts" if qtype == "fts" else "auto")
            r = c.search(first, q, doc=doc, want_commentary=want_commentary,
                         neighbors=0 if neighbors is None else neighbors, limit=max(top_k * 3, 12))
            hits = r.get("hits") or []
            matched = first if hits else ""
            if not hits:
                miss_note = _miss_note(r)
            if not hits and first != "keyword":
                r = c.search("keyword", q, doc=doc, want_commentary=want_commentary, neighbors=0, limit=max(top_k * 3, 12))
                hits = r.get("hits") or []
                matched = "keyword" if hits else ""
            pref = (clause or chapter).upper()
            if pref:
                narrowed = [h for h in hits if str(h.get("section_id") or "").upper().startswith(pref)]
                if narrowed:
                    hits = narrowed
        if not hits and clause and not q:
            r = c.search("keyword", clause, doc=doc, neighbors=0, limit=top_k)
            hits = r.get("hits") or []
            matched = "keyword" if hits else ""
        if want_commentary:
            return hits, ("" if hits else miss_note), matched
        # provisions only, unless the corpus has nothing else for this query (BIS documents carry
        # no separate commentary: every India hit is part=standard)
        prov = [h for h in hits if h.get("part") in (None, "", "standard", "n/a")]
        return (prov or hits), ("" if hits else miss_note), matched

    def _phase2(self, q: str, group: str, source: str | None, top_k: int):
        c = self.corpus()
        if not (self.root / "search" / "phase2_fts.sqlite").is_file():
            return [], (f"the {group} collection is not part of the India corpus (the IS corpus serves the IS "
                        f"documents only) -- no hit; use the OpenSees documentation from memory and say so")
        want = max(top_k * 3, 12)
        r = c.search("fts", q, collection=group, neighbors=0, limit=want)
        hits = r.get("hits") or []
        if not hits:
            r = c.search("keyword", q, collection=group, neighbors=0, limit=want)
            hits = r.get("hits") or []
        if source:
            narrowed = [h for h in hits if (h.get("source_collection") or h.get("doc")) == source]
            if narrowed:
                hits = narrowed
        return hits, ""

    def _log(self, rec: dict):
        try:
            d = self.root / "queue"
            d.mkdir(parents=True, exist_ok=True)
            with open(d / "agent_queries.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception:
            pass


# ---------------------------------------------------------------- http
PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>IS corpus — grounding</title><style>
body{{margin:0;padding:22px 26px;background:#14171c;color:#dde3ea;font:13px/1.55 'Segoe UI',system-ui,sans-serif}}
h1{{font-size:17px;margin:0 0 4px}} h2{{font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:#5d6774;margin:22px 0 8px}}
.dim{{color:#8b95a3}} .ok{{color:#3fc1a0}} .bad{{color:#e06b5e}} .warn{{color:#e0b341}}
code,td{{font-family:ui-monospace,Consolas,monospace;font-size:11.5px}}
table{{border-collapse:collapse;width:100%}} td,th{{padding:5px 8px;border-bottom:1px solid #22272f;text-align:left;vertical-align:top}}
th{{font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:#8b95a3}}
.pill{{display:inline-block;font-size:10px;letter-spacing:.07em;text-transform:uppercase;padding:2px 8px;border-radius:4px;border:1px solid #3a4656;color:#8b95a3;margin-right:6px}}
.pill.ok{{border-color:#3fc1a0;color:#3fc1a0}} .pill.warn{{border-color:#e0b341;color:#e0b341}}
</style></head><body>
<h1>IS corpus — grounding</h1>
<div class="dim">The design agents' standards-search endpoint (IS 800 / IS 801 / IS 875 / IS 1893 / IS 18168 …), answered from the corpus you converted on this PC from your own licensed BIS PDFs. Workspace: <code>{root}</code></div>
<h2>Corpus</h2>
<p><span class="pill {speccls}">IS documents (spec_fts)</span> <span class="pill {p2cls}">OpenSees docs + worked examples (not part of the India corpus)</span></p>
<p class="dim">Indexed documents: {converted}</p>
{hint}
<h2>Recent agent queries ({n})</h2>
<table><tr><th>when</th><th>collection</th><th>query</th><th>clause / chapter</th><th>hits</th><th>ms</th><th>note</th></tr>{rows}</table>
</body></html>"""


def render_page(b: Bridge) -> str:
    st = b.status()
    rows = []
    for r in reversed(b.recent[-50:]):
        when = time.strftime("%H:%M:%S", time.localtime(r["t"]))
        cls = "ok" if r["hits"] else ("warn" if not r["note"] else "bad")
        rows.append(f"<tr><td>{when}</td><td>{html.escape(r['collection'])}</td><td>{html.escape(r['query'][:120])}</td>"
                    f"<td>{html.escape(' / '.join(x for x in (r['clause'], r['chapter']) if x))}</td>"
                    f"<td class='{cls}'>{r['hits']}</td><td>{r['ms']}</td><td class='dim'>{html.escape(r['note'][:120])}</td></tr>")
    hint = ""
    if not st["spec_index"] or not st["indexed_docs"]:
        hint = ("<p class='warn'>The IS corpus on this PC is empty. The hub ships no standard text: convert your own "
                "licensed BIS PDFs (Admin → Standards, or this module's Convert tab), then Rebuild index and Validate "
                "corpus; the corpus-fix step (CORPUS_FIX_LLM_INSTRUCTIONS.md) and Import fixed corpus come after. "
                "Until then every question answers \"not in the corpus\", and the agents cite the IS documents from "
                "memory and flag every value to verify.</p>")
    docs = ", ".join(f"{d} ({st['quality'][d]})" if st.get("quality", {}).get(d) else d for d in st["indexed_docs"])
    return PAGE.format(root=html.escape(st["root"]),
                       p2cls="ok" if st["phase2_index"] else "warn",
                       speccls="ok" if st["spec_index"] else "warn",
                       converted=html.escape(docs or "(none)"),
                       hint=hint, n=len(b.recent), rows="".join(rows) or "<tr><td colspan=7 class='dim'>none yet</td></tr>")


def make_handler(bridge: Bridge):
    class H(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str = "application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/healthz", "/api/status"):
                self._send(200, json.dumps(bridge.status()).encode("utf-8"))
            elif path == "/":
                self._send(200, render_page(bridge).encode("utf-8"), "text/html")
            elif path == "/api/recent":
                self._send(200, json.dumps(bridge.recent[-50:]).encode("utf-8"))
            else:
                self._send(404, b'{"error":"not found"}')

        def do_POST(self):
            path = urlparse(self.path).path
            if path not in ("/query", "/api/query"):
                self._send(404, b'{"error":"not found"}')
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
                if not isinstance(body, dict):
                    body = {}
            except Exception as e:
                self._send(400, json.dumps({"results": [], "error": f"bad JSON body: {e}"}).encode("utf-8"))
                return
            out = bridge.query(body)
            self._send(200, json.dumps(out, ensure_ascii=False).encode("utf-8"))

        def log_message(self, fmt, *args):      # one line per request in the hub's server log
            sys.stdout.write("%s - %s\n" % (self.address_string(), fmt % args))
            sys.stdout.flush()
    return H


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="IS corpus grounding bridge")
    ap.add_argument("--root", required=True, help="corpus workspace (the folder with documents/, indexes/, search/, scripts/)")
    ap.add_argument("--scripts", default=None, help="folder holding retrieval.py (default: <root>/scripts)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    scripts = Path(a.scripts).resolve() if a.scripts else root / "scripts"
    if not (scripts / "retrieval.py").is_file():
        print(f"[is-corpus] retrieval.py not found in {scripts} -- install the IS corpus module (its post-install lays the empty workspace out)")
        return 2
    for d in ("documents/standards", "queue"):
        (root / d).mkdir(parents=True, exist_ok=True)
    bridge = Bridge(root, scripts)
    # Single-threaded on purpose: the corpus holds sqlite connections, which refuse to be used from
    # a thread other than the one that opened them. Queries take milliseconds; the agents wait.
    srv = HTTPServer((a.host, a.port), make_handler(bridge))
    print(f"[is-corpus] grounding bridge on http://{a.host}:{a.port}  workspace={root}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
