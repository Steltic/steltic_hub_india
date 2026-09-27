#!/usr/bin/env python3
"""File-queue query daemon. Same backend as search.py.

  queue/in/<plan_id>.json  ->  queue/out/<plan_id>.json

In schema:
  {plan_id, submitted, queries: [{qid, doc, type, query, purpose,
    want_commentary, context_neighbors}]}

Out schema: per qid verbatim excerpt(s), ids, part, pages, neighbors,
collection, found:false never a guess, complete:true when done.

Usage:
  python scripts/serve_queue.py              # one-shot: drain queue/in
  python scripts/serve_queue.py --watch      # poll every --interval seconds
  python scripts/serve_queue.py --plan P     # process one plan file
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retrieval import Corpus, find_root  # noqa: E402


def process_plan(corpus: Corpus, plan_path: Path, out_dir: Path, done_dir: Path) -> Path:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan_id = plan.get("plan_id") or plan_path.stem
    queries = plan.get("queries") or []
    results = []
    for q in queries:
        r = corpus.run_query(q)
        # never guess
        if not r.get("found"):
            r["found"] = False
            r["hits"] = r.get("hits") or []
        results.append(r)
    out = {
        "plan_id": plan_id,
        "submitted": plan.get("submitted"),
        "processed": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "complete": True,
        "n_queries": len(queries),
        "n_found": sum(1 for r in results if r.get("found")),
        "n_cached": sum(1 for r in results if r.get("cached")),
        "results": results,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{plan_id}.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    done_dir.mkdir(parents=True, exist_ok=True)
    dest = done_dir / plan_path.name
    if plan_path.resolve() != dest.resolve():
        shutil.move(str(plan_path), str(dest))
    return out_path


def drain(corpus: Corpus, in_dir: Path, out_dir: Path, done_dir: Path) -> list[Path]:
    written = []
    for p in sorted(in_dir.glob("*.json")):
        written.append(process_plan(corpus, p, out_dir, done_dir))
        print(f"processed {p.name} -> {written[-1]}")
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description="Process retrieval queue/in -> queue/out")
    ap.add_argument("--root", type=Path, default=None)
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--plan", type=Path, default=None)
    args = ap.parse_args()
    root = args.root or find_root()
    in_dir = root / "queue" / "in"
    out_dir = root / "queue" / "out"
    done_dir = root / "queue" / "done"
    in_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    corpus = Corpus(root)
    if args.plan:
        p = args.plan if args.plan.is_file() else in_dir / args.plan
        if not p.is_file():
            print(f"plan not found: {p}", file=sys.stderr)
            return 2
        out = process_plan(corpus, p, out_dir, done_dir)
        print(out)
        return 0
    if args.watch:
        print(f"watching {in_dir} (interval={args.interval}s)")
        while True:
            drain(corpus, in_dir, out_dir, done_dir)
            time.sleep(args.interval)
    else:
        written = drain(corpus, in_dir, out_dir, done_dir)
        if not written:
            print(f"no plans in {in_dir}")
        return 0


if __name__ == "__main__":
    # Windows: a piped stdout / stderr is cp1252, which cannot print the IS symbols (Ω, →, ≤ ...) -- write UTF-8
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
