#!/usr/bin/env python3
"""Unified search CLI (spec + phase-2). stdlib + sqlite3. No vector DB.

Usage:
  search.py id <id> [--doc D] [--commentary] [--neighbors N] [--limit N]
  search.py exact_section <id>  [...]
  search.py eq | exact_equation <id>  [...]
  search.py table | exact_table <id>  [...]
  search.py fts "<query>" [--doc D] [--collection C] [--limit N] [--commentary]
  search.py keyword "<query>" [...]
  search.py command <name> [--limit N]

Lookup order (auto / miss fallback): exact section/eq/table, then FTS, then
keyword, then alias expansion. Results are verbatim extracts, never paraphrased.
Default: provision only (want_commentary false).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retrieval import Corpus, format_cli_result, find_root  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    mode = argv[0]
    rest = argv[1:]
    # flags may appear after the query; collect known flags
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--doc", default=None)
    parser.add_argument("--collection", default=None)
    parser.add_argument("--commentary", action="store_true")
    parser.add_argument("--neighbors", type=int, default=1)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--root", default=None)
    parser.add_argument("--max-text", type=int, default=40000)
    parser.add_argument("--no-cache", action="store_true")
    # remaining positional = query tokens
    args, unknown = parser.parse_known_args(rest)
    query_parts = [u for u in unknown if not u.startswith("-")]
    query = " ".join(query_parts).strip()
    if not query:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    root = Path(args.root) if args.root else find_root()
    corpus = Corpus(root)
    result = corpus.run_query(
        {
            "type": mode,
            "query": query,
            "doc": args.doc,
            "want_commentary": bool(args.commentary),
            "context_neighbors": args.neighbors,
            "limit": args.limit,
            "collection": args.collection,
            "bypass_cache": bool(args.no_cache),
        }
    )
    print(json.dumps(format_cli_result(result, max_text=args.max_text), ensure_ascii=False, indent=1))
    return 0 if result.get("found") else 1


if __name__ == "__main__":
    # Windows: a piped stdout / stderr is cp1252, which cannot print the IS symbols (Ω, →, ≤ ...) -- write UTF-8
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
