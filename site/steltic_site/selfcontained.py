"""The self-contained option: every file the published pages link that the overlay publish leaves out.

The overlay publishes of October 2026 left out the hot-rolled report figures (report.html -> figs/*.png) and most of
the nonlinear 3D viewers (steltic_viewer_bundle.html -> viewer_3d.html, pushover/pushover_viewer_3d.html,
ddm_viewer_3d.html), which only the files already on the server supplied. With this pass the zip alone reproduces
the pages: each published page (not the viewers themselves) is read for its relative links -- href / src attributes
and the quoted file names its scripts open -- and a target missing from the build is copied from the page's source
folder (the gold job folder, the nonlinear run folder, the cold-formed publish folder).

A link that resolves nowhere is reported: from an href / src it is a broken link (MISSING); a quoted name in a script
that exists in neither place is ignored (a script may name a file it only offers when present).
"""
from __future__ import annotations

import os
import re

from .util import copy

ATTR = re.compile(r'(?:href|src)\s*=\s*["\']([^"\'#?]+)')
LITERAL = re.compile(r'["\']([A-Za-z0-9_./-]+\.(?:html|png|svg|jpg|jpeg|gif|csv|std|json|js|css|webp))["\']')
SKIP_PREFIX = ("data:", "/", "mailto:", "javascript:", "#", "tel:")
MAX_PAGE_BYTES = 5_000_000          # a page larger than this is a viewer (its own data inside), not read for links


def relative_links(text: str):
    """[(link, from_attribute)] of a page's relative links."""
    out = []
    for kind, pat in ((True, ATTR), (False, LITERAL)):
        for m in pat.finditer(text):
            u = m.group(1).strip()
            if not u or "://" in u or u.startswith(SKIP_PREFIX) or "{" in u or "+" in u:
                continue
            out.append((u, kind))
    return out


def is_page(name: str) -> bool:
    return name.endswith(".html") and "viewer_3d" not in name


def source_of(rel_path: str, sources: dict):
    """The source file of a published path (relative to public_html): the longest published folder prefix in
    sources {published folder: source folder} that holds it."""
    best = None
    for pub, src in sources.items():
        if rel_path == pub or rel_path.startswith(pub.rstrip("/") + "/"):
            if best is None or len(pub) > len(best[0]):
                best = (pub, src)
    if not best:
        return None
    return os.path.join(best[1], os.path.relpath(rel_path, best[0]))


def complete(pub_root: str, sources: dict, tops=("solutions_india",), log=print):
    """Copy into pub_root every missing file the pages under pub_root/<tops> link. Returns (added, missing) --
    root-relative POSIX paths added, and [(page, link)] that resolve nowhere."""
    added, missing = [], []
    queue = []
    for top in tops:
        for dp, dn, fn in os.walk(os.path.join(pub_root, top)):
            dn.sort()
            for f in sorted(fn):
                if is_page(f):
                    queue.append(os.path.join(dp, f))
    seen = set()
    while queue:
        page = queue.pop(0)
        if page in seen:
            continue
        seen.add(page)
        if os.path.getsize(page) > MAX_PAGE_BYTES:
            continue
        with open(page, encoding="utf-8", errors="replace") as f:
            text = f.read()
        here = os.path.dirname(page)
        for link, attr in relative_links(text):
            tgt = os.path.normpath(os.path.join(here, link))
            rel = os.path.relpath(tgt, pub_root).replace(os.sep, "/")
            if rel.startswith("../") or os.path.exists(tgt):
                continue
            src = source_of(rel, sources)
            if src and os.path.isfile(src):
                copy(src, tgt)
                added.append(rel)
                if is_page(os.path.basename(tgt)):
                    queue.append(tgt)
            elif attr:
                missing.append((os.path.relpath(page, pub_root).replace(os.sep, "/"), link))
    added = sorted(set(added))
    log("self-contained: %d file(s) added, %d link(s) resolve nowhere" % (len(added), len(missing)))
    return added, missing


def page_assets(page_path: str):
    """The site files the page itself relies on (outside india.html and solutions_india/: stylesheet, icons, logo,
    terms and privacy pages), which a publish of the India pages does not carry."""
    with open(page_path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    out = []
    for link, attr in relative_links(text):
        if not attr:
            continue
        p = link.split("?")[0]
        if p.startswith("solutions_india/") or p in ("india.html",) or p.startswith("#"):
            continue
        if p not in out:
            out.append(p)
    return out
