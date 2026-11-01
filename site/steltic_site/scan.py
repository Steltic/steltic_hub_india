"""The public-text scan of every published page, report and index (handoff section 5, step 4).

Public text is plain engineer's English: no file paths, module or script names, JSON or cfg keys, ticket ids, "EOR",
"owner ruling" as bare jargon, raw dicts, the old IS 875 (Part 1):1987 edition, "(was None)". The scan reads the
visible text of each page (scripts, styles and images left out, tags removed) and every index.json, and prints one
CHECK line per hit with its context. It reports; the build does not fail on it (a hit is for a person to judge).
The 3D viewers are not read (their data carries engine keys by design).
"""
from __future__ import annotations

import html
import os
import re

PATTERNS = (
    ("path", r"/home/|/tmp/|[A-Z]:\\Users\\"),
    ("EOR", r"\bEOR\b"),
    ("owner ruling", r"(?i)\bowner ruling"),
    ("ticket id", r"\bNL-\d+\b"),
    ("old IS 875-1", r"\(Part 1\):1987"),
    ("was None", r"\(was None\)"),
    ("None", r"(?<![\w-])None(?![\w-])"),
    ("raw dict", r"\{\s*['\"][A-Za-z_]+['\"]\s*:"),
    ("cfg key", r"\bcfg\[|\bpeb_build\b|\bpeb_rules\b"),
    ("json file", r"\b[a-z_]+\.json\b"),
    ("nl internals", r"hinge_params_used|nl_plan\.|gm_selection|Inputs \(sha256\)"),
    ("script name", r"\b[a-z_]+\.py\b"),
)
VISIBLE_DROP = re.compile(r"<script.*?</script>|<style.*?</style>|<img[^>]*>", re.S | re.I)


LINK_KEYS = {"id", "report", "viewer", "summary", "pushover", "nlrha", "ddm", "std", "reactions", "bom", "anchors"}


def visible(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", VISIBLE_DROP.sub(" ", text)))


def index_text(text: str) -> str:
    """The text an index shows on the page: every string value except the ids and file links, one per line."""
    import json
    out = []

    def walk(o, key=None):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, k)
        elif isinstance(o, list):
            for v in o:
                walk(v, key)
        elif isinstance(o, str) and key not in LINK_KEYS:
            out.append(o)
    try:
        walk(json.loads(text))
    except ValueError:
        return text
    return "\n".join(out)


def scan(root: str, tops=("india.html", "solutions_india"), patterns=PATTERNS, max_lines: int = 60, log=print,
         skip_appendix_c: bool = True):
    """Hits [(file, pattern name, context)] under root/<tops>."""
    pats = [(n, re.compile(p)) for n, p in patterns]
    hits = []
    files = []
    for top in tops:
        p = os.path.join(root, top)
        if os.path.isfile(p):
            files.append(p)
        for dp, dn, fn in os.walk(p):
            dn.sort()
            for f in sorted(fn):
                if (f.endswith(".html") and "viewer" not in f) or f == "index.json":
                    files.append(os.path.join(dp, f))
    for path in files:
        with open(path, encoding="utf-8", errors="replace") as f:
            t = f.read()
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        if path.endswith(".html"):
            if skip_appendix_c:
                # the HR reports' Appendix C is the activity log of the run (a record, kept as written)
                t = re.split(r"<h2[^>]*>\s*Appendix C\b", t)[0]
            t = visible(t)
        else:
            t = index_text(t)
        for name, pat in pats:
            if name == "json file" and path.endswith("index.json"):
                continue                     # an index names its own files by design
            if name == "None" and path.endswith("index.json"):
                continue
            for m in pat.finditer(t):
                ctx = t[max(0, m.start() - 50):m.end() + 30].replace("\n", " ")
                hits.append((rel, name, re.sub(r"\s+", " ", ctx)))
    for h in hits[:max_lines]:
        log("CHECK %s [%s]: ...%s..." % h)
    log("public-text scan: %d file(s), %d hit(s)" % (len(files), len(hits)))
    return hits
