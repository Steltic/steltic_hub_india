"""Small helpers shared by the site build parts."""
from __future__ import annotations

import hashlib
import json
import os
import shutil


class BuildError(RuntimeError):
    """A build step cannot go on (a missing input, a patch that does not match the page exactly once, ...)."""


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def copy(src, dst):
    """Copy a file (with its modification time), making the folders on the way."""
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    shutil.copy2(src, dst)


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump_index(obj, path):
    """An index.json as the site has always written them: indent 1, UTF-8, no trailing newline."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)


def files_under(root, *tops):
    """Every file under root/<top> (or root/<top> itself when it is a file), as sorted root-relative POSIX paths."""
    out = []
    for top in tops:
        p = os.path.join(root, top)
        if os.path.isfile(p):
            out.append(top.replace(os.sep, "/"))
            continue
        for dp, dn, fn in os.walk(p):
            dn.sort()
            for f in fn:
                out.append(os.path.relpath(os.path.join(dp, f), root).replace(os.sep, "/"))
    return sorted(out)


def titles(paths, notes=None):
    """{example id: {'title', 'blurb'}} from titles files (titles_wave2.json style); a later file wins. An entry is a
    path, or {"path": ..., "optional": true} for a file that may not exist yet (noted in `notes`, then skipped)."""
    out = {}
    for p in paths or []:
        opt = isinstance(p, dict) and bool(p.get("optional"))
        p = p["path"] if isinstance(p, dict) else p
        if opt and not os.path.isfile(p):
            if notes is not None:
                notes.append("NOTE titles file %s does not exist (yet): skipped" % p)
            continue
        for k, v in load_json(p).items():
            out[k] = dict(out.get(k) or {}, **v)
    return out
