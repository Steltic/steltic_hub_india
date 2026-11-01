"""solutions_india/cfs/: the cold-formed publish folder, copied as built.

steltic_CFS_india builds it from its re-run golds (index.json and one folder per example: report.html, viewer_3d.html,
the report figures and the lateral-system figures); this part only copies it and checks that every link of its index
resolves.
"""
from __future__ import annotations

import os
import shutil

from .util import BuildError, load_json

LINK_KEYS = ("report", "viewer")


def build(publish: str, out_dir: str, log=print):
    """Copy the publish folder to out_dir. Returns (entries, problems, sources)."""
    idx = os.path.join(publish, "index.json")
    if not os.path.isfile(idx):
        raise BuildError("the cold-formed publish folder %s has no index.json" % publish)
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    shutil.copytree(publish, out_dir, copy_function=shutil.copy2)
    entries = load_json(idx).get("examples") or []
    problems, sources = [], {}
    for e in entries:
        for k in LINK_KEYS:
            v = e.get(k)
            if v and not os.path.isfile(os.path.join(out_dir, v)):
                problems.append("%s: %s link %s does not resolve" % (e.get("id"), k, v))
        sources[e["id"]] = os.path.join(publish, e["id"])
    log("cfs examples: %d" % len(entries))
    for x in problems:
        log("PROBLEM %s" % x)
    return entries, problems, sources
