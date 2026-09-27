#!/usr/bin/env python3
"""Local-only retrieval result cache (stdlib sqlite3).

Stores past Query file manager results so a repeat (doc, type, query,
commentary, neighbors, collection) returns the stored pack without
re-searching indexes/FTS.

DB path: <root>/cache/query_cache.sqlite  — never Drive, never a laptop.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retrieval import DOC_ALIASES, TYPE_MAP, find_root  # noqa: E402

WS_RE = re.compile(r"\s+")
EXACT_TYPES = frozenset({"exact_section", "exact_equation", "exact_table", "id"})
FTS_TYPES = frozenset({"fts", "keyword", "command"})
FINGERPRINT_RELPATHS = (
    "indexes/sections.json",
    "indexes/tables.json",
    "indexes/equations.json",
    "indexes/aliases.json",
    "search/spec_fts.sqlite",
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def normalize_doc(doc: Optional[str]) -> str:
    raw = (doc or "").strip()
    if not raw:
        return ""
    return DOC_ALIASES.get(raw.lower(), raw)


def normalize_type(type_: Optional[str]) -> str:
    t = (type_ or "auto").lower().strip()
    return TYPE_MAP.get(t, t)


def normalize_query(query: Optional[str], type_: str) -> str:
    raw = query or ""
    if type_ in EXACT_TYPES:
        # squash-ish: NFKC, lower, strip, drop whitespace; keep punctuation (12.8-2)
        s = _nfkc(raw).strip().lower()
        return re.sub(r"\s+", "", s)
    # fts / keyword / command / auto: NFKC, lower, collapse whitespace
    s = WS_RE.sub(" ", _nfkc(raw).lower()).strip()
    return s


def _neighbors_of(q: dict[str, Any]) -> int:
    if q.get("neighbors") is not None:
        try:
            return int(q.get("neighbors") or 0)
        except (TypeError, ValueError):
            return 0
    if q.get("context_neighbors") is not None:
        try:
            return int(q.get("context_neighbors") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def canonical_fields(q: dict[str, Any]) -> dict[str, Any]:
    type_ = normalize_type(q.get("type") or q.get("kind"))
    return {
        "collection": str(q.get("collection") or ""),
        "doc": normalize_doc(q.get("doc")),
        "neighbors": _neighbors_of(q),
        "query": normalize_query(q.get("query"), type_),
        "type": type_,
        "want_commentary": bool(q.get("want_commentary")),
    }


def canonical_key(q: dict[str, Any]) -> str:
    fields = canonical_fields(q)
    blob = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# Served text also invalidates the cache (CORPUS-15): a changed page markdown,
# pages_search page, structured sections table or retrieval code must not return
# stale cached hits.
FINGERPRINT_GLOBS = (
    "documents/standards/*/markdown/*.search.md",
    "documents/standards/*/markdown/pages_search/*.md",
    "documents/standards/*/structured/sections.csv",
    "scripts/retrieval.py",
    "scripts/bis_text.py",
    "scripts/aliases.json",
    "indexes/us_terms_not_in_IS.json",
    "indexes/documents.json",
)


def corpus_fingerprint(root: Path) -> str:
    parts: list[str] = []
    for rel in FINGERPRINT_RELPATHS:
        p = root / rel
        if p.is_file():
            parts.append(str(int(p.stat().st_mtime)))
    h = hashlib.sha256()
    for pat in FINGERPRINT_GLOBS:
        for p in sorted(root.glob(pat)):
            try:
                st = p.stat()
            except OSError:
                continue
            h.update(f"{p.relative_to(root)}:{st.st_mtime_ns}:{st.st_size};".encode("utf-8"))
    parts.append(h.hexdigest()[:16])
    return "".join(parts)


class QueryCache:
    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root) if root else find_root()
        self.path = self.root / "cache" / "query_cache.sqlite"
        # SQLite connections are thread-bound (NEW-9): one connection per thread.
        self._tl = threading.local()

    def connect(self) -> sqlite3.Connection:
        con = getattr(self._tl, "con", None)
        if con is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(str(self.path), timeout=30)
            con.row_factory = sqlite3.Row
            self._init(con)
            self._tl.con = con
        return con

    def close(self) -> None:
        con = getattr(self._tl, "con", None)
        if con is not None:
            con.close()
            self._tl.con = None

    def _init(self, con: sqlite3.Connection) -> None:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS query_cache (
                key TEXT PRIMARY KEY,
                doc TEXT,
                type TEXT,
                query TEXT,
                want_commentary INT,
                neighbors INT,
                collection TEXT,
                found INT,
                result_json TEXT,
                corpus_fp TEXT,
                hits INT DEFAULT 1,
                created TEXT,
                last_hit TEXT
            )
            """
        )
        con.commit()

    def get(self, q_dict: dict[str, Any]) -> Optional[dict[str, Any]]:
        key = canonical_key(q_dict)
        fp = corpus_fingerprint(self.root)
        con = self.connect()
        row = con.execute(
            "SELECT result_json, corpus_fp FROM query_cache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        if (row["corpus_fp"] or "") != fp:
            return None
        try:
            result = json.loads(row["result_json"] or "null")
        except json.JSONDecodeError:
            return None
        if not isinstance(result, dict):
            return None
        now = _now()
        con.execute(
            "UPDATE query_cache SET hits = hits + 1, last_hit = ? WHERE key = ?",
            (now, key),
        )
        con.commit()
        return result

    def put(self, q_dict: dict[str, Any], result: dict[str, Any]) -> str:
        fields = canonical_fields(q_dict)
        key = canonical_key(q_dict)
        fp = corpus_fingerprint(self.root)
        stored = json.loads(json.dumps(result, ensure_ascii=False))
        now = _now()
        found = 1 if stored.get("found") else 0
        con = self.connect()
        existing = con.execute(
            "SELECT hits, created FROM query_cache WHERE key = ?", (key,)
        ).fetchone()
        hits = int(existing["hits"]) if existing else 1
        created = existing["created"] if existing else now
        if existing is None:
            hits = 1
            created = now
        con.execute(
            """
            INSERT OR REPLACE INTO query_cache (
                key, doc, type, query, want_commentary, neighbors, collection,
                found, result_json, corpus_fp, hits, created, last_hit
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                fields["doc"],
                fields["type"],
                fields["query"],
                1 if fields["want_commentary"] else 0,
                fields["neighbors"],
                fields["collection"],
                found,
                json.dumps(stored, ensure_ascii=False),
                fp,
                hits,
                created,
                now,
            ),
        )
        con.commit()
        return key

    def clear(self) -> None:
        con = self.connect()
        con.execute("DELETE FROM query_cache")
        con.commit()

    def stats(self) -> dict[str, int]:
        con = self.connect()
        row = con.execute(
            """
            SELECT
                COUNT(*) AS n_entries,
                COALESCE(SUM(CASE WHEN found = 1 THEN 1 ELSE 0 END), 0) AS n_found,
                COALESCE(SUM(CASE WHEN found = 0 THEN 1 ELSE 0 END), 0) AS n_misses_cached,
                COALESCE(SUM(hits), 0) AS total_hits
            FROM query_cache
            """
        ).fetchone()
        return {
            "n_entries": int(row["n_entries"]),
            "n_found": int(row["n_found"]),
            "n_misses_cached": int(row["n_misses_cached"]),
            "total_hits": int(row["total_hits"]),
        }

    def seed_from_queue_out(self, root: Optional[Path] = None) -> dict[str, int]:
        root = Path(root) if root else self.root
        out_dir = root / "queue" / "out"
        n_files = 0
        n_skipped_files = 0
        n_items = 0
        n_skipped = 0
        n_inserted = 0
        n_updated = 0
        seen_keys: set[str] = set()
        con = self.connect()
        if not out_dir.is_dir():
            return {
                "files": 0,
                "skipped_files": 0,
                "items": 0,
                "skipped": 0,
                "inserted": 0,
                "updated": 0,
                "collapsed": 0,
            }
        for path in sorted(out_dir.glob("*.json")):
            if "pack" in path.name.lower():
                n_skipped_files += 1
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                n_skipped_files += 1
                continue
            n_files += 1
            results = payload.get("results") if isinstance(payload, dict) else None
            if not isinstance(results, list):
                continue
            for item in results:
                if not isinstance(item, dict):
                    n_skipped += 1
                    continue
                typ = item.get("type") or item.get("kind")
                query = item.get("query")
                if not typ or query is None or query == "":
                    n_skipped += 1
                    continue
                n_items += 1
                q_dict = _q_from_result(item)
                key = canonical_key(q_dict)
                existed = key in seen_keys
                row = con.execute(
                    "SELECT 1 FROM query_cache WHERE key = ?", (key,)
                ).fetchone()
                self.put(q_dict, item)
                if row is not None or existed:
                    n_updated += 1
                else:
                    n_inserted += 1
                seen_keys.add(key)
        collapsed = n_items - len(seen_keys)
        return {
            "files": n_files,
            "skipped_files": n_skipped_files,
            "items": n_items,
            "skipped": n_skipped,
            "inserted": n_inserted,
            "updated": n_updated,
            "collapsed": collapsed,
        }


def _q_from_result(item: dict[str, Any]) -> dict[str, Any]:
    doc = item.get("doc")
    if not doc:
        hits = item.get("hits") or []
        if hits and isinstance(hits[0], dict):
            doc = hits[0].get("doc")
    neighbors = item.get("neighbors")
    if not isinstance(neighbors, int):
        neighbors = item.get("context_neighbors")
    if not isinstance(neighbors, int):
        neighbors = 0
    return {
        "doc": doc or "",
        "type": item.get("type") or item.get("kind"),
        "query": item.get("query"),
        "want_commentary": bool(item.get("want_commentary")),
        "neighbors": neighbors,
        "collection": item.get("collection") or "",
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Local retrieval result cache")
    ap.add_argument("command", choices=("seed", "stats", "clear"))
    ap.add_argument("--root", type=Path, default=None)
    args = ap.parse_args(argv)
    root = args.root or find_root()
    cache = QueryCache(root)
    if args.command == "clear":
        cache.clear()
        print(f"cleared {cache.path}")
        return 0
    if args.command == "seed":
        info = cache.seed_from_queue_out(root)
        print(json.dumps(info, indent=2))
        st = cache.stats()
        print(json.dumps(st, indent=2))
        print(f"cache={cache.path}")
        return 0
    st = cache.stats()
    print(json.dumps(st, indent=2))
    print(f"cache={cache.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
