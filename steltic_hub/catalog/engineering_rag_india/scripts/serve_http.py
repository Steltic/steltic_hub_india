#!/usr/bin/env python3
"""HTTP query server for the India corpus -- the API the Steltic engines' standards search tool calls.

    python3 scripts/serve_http.py                    # 127.0.0.1:8765, corpus = the folder above scripts/
    python3 scripts/serve_http.py --port 8765 --host 127.0.0.1 --root <corpus workspace>
    export RAG_API_URL=http://127.0.0.1:8765/query  # in the engine's environment

stdlib only (http.server.ThreadingHTTPServer). One shared, pre-warmed `retrieval.Corpus`; its SQLite
connections are per thread, so concurrent requests are safe.

POST /query  payload (steltic job_tools._rag_post, HR and CFS):
    {"query": str, "collection": str, "top_k": int,
     "clause"?: str, "chapter"?: str,           # clause = an exact id: "7.3.6", "Table 10", "D-1", "HB 300"
     "stem"?: str, "doc"?: str,                  # corpus stem (IS_800_2007 ...); else derived from collection
     "type"?: str,                               # exact_section | exact_table | exact_equation | id | fts | keyword | auto
     "want_commentary"?: bool, "context_neighbors"?: int}
reply:
    {"results": [{text, source, doc, section, section_id, table_id, eq_id, page, id, score, title, kind,
                  edition}],
     "found": bool, "note": str, "not_tabulated": bool|None,
     "matched": "exact_section" | "exact_table" | "exact_equation" | "exact_annex_town" | "fts" | "keyword" | "",
     "type": <the lookup the corpus actually ran>, "doc": <stem searched or null>}
    `matched` starts with "exact_" only when the exact lookup of the id itself found it (the engines
    treat such a hit as final). A server-side exception is reported as note "server error: ..." with
    "error": true, which the engines retry and never read as absence.

GET /healthz -> {"ok": true, "spec_index": bool, "indexed_docs": [stems], "root": str}
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retrieval import Corpus, resolve_doc  # noqa: E402

GROUPS = {"", "specification", "spec", "standards", "india", "is_bis", "all"}
_DOCLIKE = re.compile(r"(?i)^(?:engineering_standards?_)?(?:IS|BIS)[ _-]?\d")
_TABLE = re.compile(r"(?i)^\s*(?:table|tab\.?|tbl\.?|fig(?:ure)?\.?)\s*\S+")   # K07: Fig. 10 / Figure 4
_SECTION = re.compile(r"^(?:[A-Z]-?)?\d+(?:\.\d+)*(?:\([a-z0-9]+\))?\.?$")   # 7.3.6, D-1, E-1.1, 8.1(d)
_EQUATION = re.compile(r"(?i)^\s*(?:eq\.?|equation)\s*\S+")


def resolve_target(payload: dict, corpus: Corpus) -> tuple[str | None, str | None]:
    """-> (stem to search or None for all documents, error note or None)."""
    s = (payload.get("stem") or payload.get("doc") or "").strip()
    coll = (payload.get("collection") or "").strip()
    if s:
        return resolve_doc(s), None
    if coll.lower() in GROUPS:
        return None, None
    rd = resolve_doc(coll)
    if rd in corpus.doc_meta:
        return rd, None
    if _DOCLIKE.match(coll):
        return rd, None                     # a document name the corpus does not hold -> Corpus.search notes it
    return None, f"unknown collection {coll!r}"


def clause_type(clause: str, requested: str) -> str:
    t = (requested or "").strip().lower()
    if t and t not in ("auto", "fts", "keyword", "id"):
        return t
    if _TABLE.match(clause):
        return "exact_table"
    if _EQUATION.match(clause):
        return "exact_equation"
    if _SECTION.match(clause.strip()):
        return "exact_section"
    return "auto"                           # designations ("HB 300", "CLR100X50X15X2") and other ids


def _hit_out(h: dict) -> dict:
    kind = h.get("kind") or ""
    tid, sid, eid = h.get("table_id"), h.get("section_id"), h.get("eq_id")
    if kind == "table" and tid:
        section = str(tid) if (str(tid).startswith("Fig. ") or str(tid).endswith("-shape")) else f"Table {tid}"
    elif kind == "equation" and eid:
        section = eid
    else:
        section = sid or (f"Table {tid}" if tid else None) or eid
    return {"text": h.get("text") or "", "source": h.get("doc"), "doc": h.get("doc"), "section": section,
            "section_id": sid, "table_id": tid, "eq_id": eid, "page": h.get("pdf_page"),
            "printed_page": h.get("printed_label"), "id": h.get("id"), "score": h.get("score"),
            "title": h.get("title"), "kind": kind, "edition": h.get("edition")}


def answer(corpus: Corpus, payload: dict) -> dict:
    q = str(payload.get("query") or "")
    clause = str(payload.get("clause") or "").strip()
    try:
        top = max(1, min(int(payload.get("top_k") or 5), 50))
    except (TypeError, ValueError):
        top = 5
    try:
        nb = max(0, min(int(payload.get("context_neighbors") or 0), 2))
    except (TypeError, ValueError):
        nb = 0
    wc = bool(payload.get("want_commentary"))
    stem, err = resolve_target(payload, corpus)
    if err:
        return {"results": [], "found": False, "note": err, "not_tabulated": None, "matched": "", "type": "",
                "doc": None}
    req_t = str(payload.get("type") or "").strip().lower()
    matched = ""
    if clause:
        ct = clause_type(clause, req_t)
        r = corpus.search(ct, clause, doc=stem, limit=top, neighbors=nb, want_commentary=wc)
        if r.get("found") and str(r.get("type") or "").startswith("exact_"):
            matched = str(r.get("type"))
        elif not r.get("found") and not r.get("document_not_in_corpus") and q.strip():
            r = corpus.search("auto", q, doc=stem, limit=top, neighbors=nb, want_commentary=wc)
    else:
        r = corpus.search(req_t or "auto", q, doc=stem, limit=top, neighbors=nb, want_commentary=wc)
        if r.get("found") and str(r.get("type") or "").startswith("exact_"):
            matched = str(r.get("type"))
    hits = r.get("hits") or []
    if not matched and hits and all(h.get("kind") == "annex_town" for h in hits):
        matched = "exact_annex_town"        # the town's own row in IS 1893 Annex E / IS 875-3 Annex A
    if not matched and r.get("found"):
        matched = str(r.get("type") or "")
    res = [_hit_out(h) for h in (r.get("hits") or []) if h.get("found", True)][:top]
    note = r.get("note") or r.get("message") or ""
    if not res and r.get("found") is False and not note and r.get("us_term"):
        note = "US term, not in IS"
    return {"results": res, "found": bool(res), "note": note, "not_tabulated": r.get("not_tabulated"),
            "matched": matched, "type": r.get("type") or "", "doc": stem,
            **({"document_not_in_corpus": True, "indexed_docs": r.get("indexed_docs")}
               if r.get("document_not_in_corpus") else {})}


def make_handler(corpus: Corpus, log_path: str | None = None, token: str = ""):
    lock = threading.Lock()

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, code: int, obj: dict) -> None:
            b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _authorised(self) -> bool:
            if not token:
                return True
            return self.headers.get("Authorization", "") == "Bearer " + token

        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0].rstrip("/")
            if path in ("/healthz", "/health", ""):
                p = corpus.search_dir / "spec_fts.sqlite"
                self._send(200, {"ok": True, "spec_index": p.is_file(), "indexed_docs": sorted(corpus.doc_meta),
                                 "root": str(corpus.root)})
            else:
                self._send(404, {"error": "not found", "endpoints": ["POST /query", "GET /healthz"]})

        def do_POST(self):  # noqa: N802
            path = self.path.split("?", 1)[0].rstrip("/")
            if path not in ("/query", "/api/query"):
                self._send(404, {"error": "not found", "endpoints": ["POST /query", "GET /healthz"]})
                return
            if not self._authorised():
                self._send(401, {"error": "unauthorised"})
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("payload must be a JSON object")
            except Exception as e:  # malformed request: the caller's error, not the corpus's
                self._send(400, {"error": f"bad request: {e}"})
                return
            try:
                out = answer(corpus, payload)
            except Exception as e:  # reported, never mistaken for an empty answer
                out = {"results": [], "found": False, "error": True, "matched": "", "type": "",
                       "note": f"server error: {type(e).__name__}: {e}"}
                sys.stderr.write(traceback.format_exc())
            if log_path:
                try:
                    rec = {"q": payload.get("query"), "coll": payload.get("collection"),
                           "clause": payload.get("clause"), "doc": out.get("doc"), "n": len(out["results"]),
                           "matched": out.get("matched"), "note": str(out.get("note") or "")[:200]}
                    with lock, open(log_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                except Exception:
                    pass
            self._send(200, out)

        def log_message(self, *a):  # quiet
            pass

    return H


def make_server(host: str = "127.0.0.1", port: int = 8765, root: str | None = None,
                log_path: str | None = None, token: str = "") -> ThreadingHTTPServer:
    corpus = Corpus(Path(root) if root else None).warm()
    srv = ThreadingHTTPServer((host, port), make_handler(corpus, log_path, token))
    srv.daemon_threads = True
    srv.corpus = corpus  # type: ignore[attr-defined]
    return srv


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", default=os.environ.get("RAG_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("RAG_PORT", "8765")))
    ap.add_argument("--root", default=os.environ.get("INDIA_CORPUS_ROOT") or None,
                    help="corpus root (default: $INDIA_CORPUS_ROOT, else the folder above scripts/)")
    ap.add_argument("--log", default=os.environ.get("RAG_QUERY_LOG") or None, help="append one JSON line per query")
    ap.add_argument("--token", default=os.environ.get("RAG_API_TOKEN", ""),
                    help="require 'Authorization: Bearer <token>' on POST /query")
    a = ap.parse_args(argv)
    srv = make_server(a.host, a.port, a.root, a.log, a.token)
    print(f"India corpus RAG on http://{a.host}:{srv.server_address[1]}/query  "
          f"(root {srv.corpus.root}, {len(srv.corpus.doc_meta)} documents)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    # Windows: a piped stdout / stderr is cp1252, which cannot print the IS symbols (Ω, →, ≤ ...) -- write UTF-8
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
