"""india.html: the live page patched by exact-match patch files.

A patch file holds blocks, applied in order (the discipline of the earlier publishes' rep(): every FIND must occur
exactly once in the page as it stands when the block is applied, or the build stops):

    # a comment (outside the blocks)
    @@@ FIND
    <the exact text, any number of lines>
    @@@ REPLACE
    <its replacement>
    @@@ END

The text of a block is the lines between its markers, joined by newlines (the newline before the next marker is not
part of it). A marker is a whole line. Blocks never use regular expressions: the page base is the live page, so a
FIND that no longer matches means the live page is not the page the patch was written against.
"""
from __future__ import annotations

import re

from .util import BuildError

FIND, REPLACE, END = "@@@ FIND", "@@@ REPLACE", "@@@ END"


def parse_patch(text: str, name: str = "patch") -> list[tuple[str, str]]:
    """[(find, replace), ...] of a patch file's text."""
    blocks, state, buf, find = [], None, [], None
    for n, line in enumerate(text.split("\n"), 1):
        mark = line.rstrip("\r")
        if mark == FIND:
            if state is not None:
                raise BuildError(f"{name}:{n}: {FIND} inside a block")
            state, buf = "find", []
        elif mark == REPLACE:
            if state != "find":
                raise BuildError(f"{name}:{n}: {REPLACE} without {FIND}")
            find, state, buf = "\n".join(buf), "replace", []
        elif mark == END:
            if state != "replace":
                raise BuildError(f"{name}:{n}: {END} without {REPLACE}")
            if not find:
                raise BuildError(f"{name}:{n}: empty FIND")
            blocks.append((find, "\n".join(buf)))
            state = None
        elif state is None:
            if line.strip() and not line.lstrip().startswith("#"):
                raise BuildError(f"{name}:{n}: text outside a block (comments start with #)")
        else:
            buf.append(line)
    if state is not None:
        raise BuildError(f"{name}: the last block has no {END}")
    return blocks


def write_patch(blocks, header: str = "") -> str:
    out = [("# " + h if h else "#") for h in header.splitlines()] if header else []
    for a, b in blocks:
        for t in (a, b):
            if any(line in (FIND, REPLACE, END) for line in t.split("\n")):
                raise BuildError("a block's text holds a marker line")
        out += [FIND, a, REPLACE, b, END, ""]
    return "\n".join(out)


def apply(page: str, blocks, name: str = "patch") -> str:
    """The page with every block applied in order; each FIND must occur exactly once at its turn."""
    for k, (a, b) in enumerate(blocks, 1):
        n = page.count(a)
        if n != 1:
            raise BuildError("%s, block %d: the FIND text occurs %d times in the page (exactly once is required): "
                             "%r" % (name, k, n, a[:120]))
        page = page.replace(a, b)
    return page


def patch_page(base_path: str, patch_paths, out_path: str) -> list[str]:
    """Write the patched page; returns one line per patch file applied."""
    with open(base_path, encoding="utf-8") as f:
        page = f.read()
    done = []
    for p in patch_paths or []:
        with open(p, encoding="utf-8") as f:
            blocks = parse_patch(f.read(), p)
        page = apply(page, blocks, p)
        done.append(f"{p}: {len(blocks)} block(s)")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)
    return done


WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}


def count_checks(page: str, hr_ids, cfs_ids, nl_ids) -> list[str]:
    """The counts the page states in prose against the indexes this build publishes (a CHECK line per mismatch):
    '<n> worked Indian designs' (hot-rolled + cold-formed), '<n> nonlinear runs', '<n> Indian example briefs ...
    plus <k> sub-units' (an id with a '/' is a sub-unit)."""
    text = re.sub(r"<[^>]+>", " ", page).replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text)
    hr_ids, cfs_ids, nl_ids = list(hr_ids or []), list(cfs_ids or []), list(nl_ids or [])
    units = sum("/" in i for i in hr_ids)
    out = []

    def want(pattern, value, what):
        m = re.search(pattern, text)
        if m is None:
            return
        got = m.group(1)
        num = int(got) if got.isdigit() else next((k for k, w in WORDS.items() if w == got.lower()), None)
        if num != value:
            out.append(f"CHECK page says {m.group(0)!r} but the build publishes {value} {what}")

    if hr_ids and cfs_ids:
        want(r"(\d+) worked Indian designs", len(hr_ids) + len(cfs_ids), "worked designs (hot-rolled + cold-formed)")
    if nl_ids:
        want(r"(\d+) nonlinear runs", len(nl_ids), "nonlinear runs")
    if hr_ids:
        want(r"(\d+) Indian example briefs", len(hr_ids) - units, "hot-rolled briefs (sub-units apart)")
        want(r"plus (\w+) sub-units", units, "hot-rolled sub-units")
    return out
