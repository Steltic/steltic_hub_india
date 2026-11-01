"""Permissions, the upload zip, the rollback set, the manifest and the upload instructions.

* The upload zip holds paths starting india.html and solutions_india/ (extracted in public_html), folders 755 and
  files 644, entries in sorted order (the same inputs give the same zip).
* The rollback zip holds the live copy of every file the upload replaces (from the live mirror, else fetched from the
  site) and NEW_FILES_TO_DELETE_ON_ROLLBACK.txt, the files the upload adds (to delete on a rollback).
* MANIFEST.json lists every file with its size and SHA-256 (the post-upload check compares the site with it), what
  it replaces, the inputs and the build's checks.
* UPLOAD_INSTRUCTIONS.txt says how to upload, check and roll back (cPanel File Manager).
"""
from __future__ import annotations

import json
import os
import stat
import time
import zipfile

from .util import files_under, sha256

TOPS = ("india.html", "solutions_india")
NEW_LIST = "NEW_FILES_TO_DELETE_ON_ROLLBACK.txt"
NO_NEW = "No files are new in this publish: every file in the zip replaces a file already on the site.\n"


def set_permissions(root: str):
    """Folders 755, files 644 (cPanel)."""
    for dp, dn, fn in os.walk(root):
        os.chmod(dp, 0o755)
        for f in fn:
            os.chmod(os.path.join(dp, f), 0o644)


def _zinfo(name, mtime, is_dir):
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch and epoch.isdigit():
        mtime = min(mtime, int(epoch))                   # reproducible builds: no date later than the build epoch
    t = time.gmtime(max(mtime, 315532800)) if epoch else time.localtime(max(mtime, 315532800))   # zip dates: 1980+
    zi = zipfile.ZipInfo(name, date_time=t[:6])
    if is_dir:
        zi.external_attr = ((stat.S_IFDIR | 0o755) << 16) | 0x10
        zi.compress_type = zipfile.ZIP_STORED
    else:
        zi.external_attr = (stat.S_IFREG | 0o644) << 16
        zi.compress_type = zipfile.ZIP_DEFLATED
    zi.create_system = 3                                 # unix attributes
    return zi


def make_zip(root: str, zip_path: str, tops=TOPS) -> dict:
    """Zip root/<tops> with folder entries, sorted. Returns {'files': n, 'bytes': uncompressed, 'zip_bytes': ..}."""
    if os.path.exists(zip_path):
        os.remove(zip_path)
    entries = []
    for top in tops:
        p = os.path.join(root, top)
        if os.path.isfile(p):
            entries.append((top, False))
            continue
        if not os.path.isdir(p):
            continue
        for dp, dn, fn in os.walk(p):
            dn.sort()
            rel = os.path.relpath(dp, root).replace(os.sep, "/")
            entries.append((rel + "/", True))
            for f in sorted(fn):
                entries.append((rel + "/" + f, False))
    n, size = 0, 0
    with zipfile.ZipFile(zip_path, "w", compresslevel=6) as z:
        for name, is_dir in sorted(entries):
            full = os.path.join(root, *name.rstrip("/").split("/"))
            zi = _zinfo(name, os.stat(full).st_mtime, is_dir)
            if is_dir:
                z.writestr(zi, b"")
            else:
                with open(full, "rb") as f:
                    data = f.read()
                z.writestr(zi, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
                n += 1
                size += len(data)
    return {"files": n, "bytes": size, "zip_bytes": os.path.getsize(zip_path)}


def rollback(pub_root: str, live, rb_root: str, log=print) -> dict:
    """The live copy of every file the publish replaces, under rb_root; the files the publish adds listed in
    NEW_FILES_TO_DELETE_ON_ROLLBACK.txt. Returns {'replaced': [...], 'new': [...]}."""
    replaced, new = [], []
    for rel in files_under(pub_root, *TOPS):
        if live.save(rel, os.path.join(rb_root, *rel.split("/"))):
            replaced.append(rel)
        else:
            new.append(rel)
    os.makedirs(rb_root, exist_ok=True)
    with open(os.path.join(rb_root, NEW_LIST), "w", encoding="utf-8") as f:
        f.write(("\n".join(new) + "\n") if new else NO_NEW)
    set_permissions(rb_root)
    log("rollback: %d live copies kept, %d file(s) new in this publish" % (len(replaced), len(new)))
    return {"replaced": replaced, "new": new}


def manifest(pub_root: str, *, name: str, info: dict, new: list, sources: dict | None = None) -> dict:
    files = []
    newset = set(new or [])
    for rel in files_under(pub_root, *TOPS):
        p = os.path.join(pub_root, *rel.split("/"))
        files.append({"path": rel, "bytes": os.path.getsize(p), "sha256": sha256(p), "new": rel in newset})
    return dict(info, name=name, files=files, n_files=len(files), bytes=sum(f["bytes"] for f in files),
                n_new=len(newset))


def _mb(n):
    return "%.1f MB" % (n / 1e6)


def instructions(*, name: str, zip_path: str, zip_info: dict, rb_path: str, rb_info: dict, man: dict,
                 checks: list[str], assets: list[str], site_url: str, self_contained: bool, verify_cmd: str) -> str:
    """UPLOAD_INSTRUCTIONS.txt (plain text)."""
    zip_sha = sha256(zip_path)
    rb_sha = sha256(rb_path)
    n_new = len(rb_info["new"])
    lines = [
        "Steltic India -- %s" % name,
        "Built %s" % man.get("built"),
        "",
        "WHAT IS IN IT",
        "  %s: %d files, %s unpacked (%s zipped), SHA-256 %s"
        % (os.path.basename(zip_path), zip_info["files"], _mb(zip_info["bytes"]), _mb(zip_info["zip_bytes"]), zip_sha),
        "  Paths start with india.html and solutions_india/ (folders 755, files 644): extract it in public_html.",
        "  %d file(s) replace files on the site; %d are new." % (len(rb_info["replaced"]), n_new),
        ("  Self-contained: the zip also carries the report figures and every viewer the pages link, so it alone "
         "reproduces the India pages." if self_contained else
         "  Overlay: report figures and viewers already on the site are not in the zip (they stay on the server)."),
    ]
    if assets:
        lines += ["  The page also uses these site files, which this publish does not carry (they must stay on the "
                  "site): " + ", ".join(assets) + "."]
    lines += [
        "",
        "UPLOAD (cPanel)",
        "  1. Keep %s (SHA-256 %s) until the site is checked: it is the way back." % (os.path.basename(rb_path), rb_sha),
        "  2. cPanel -> File Manager -> public_html -> Upload -> %s." % os.path.basename(zip_path),
        "  3. Select the zip in public_html -> Extract -> into public_html, overwriting existing files.",
        "     Do not delete any folder first: the zip overlays the site.",
        "  4. Delete the zip from public_html.",
        "",
        "CHECK THE LIVE SITE",
        "  %s" % verify_cmd,
        "  It compares every file with the live copy (size and SHA-256; a connection reset is retried one file at a "
        "time) and asks the site for every link of the pages and indexes.",
        "  Then open %s and check that the lists fill (hot-rolled, cold-formed, nonlinear) and the pages show no "
        "errors (site/tools/live_page_check.py does this in a headless browser)." % (site_url.rstrip("/") + "/india.html"),
        "",
        "ROLL BACK (only if the check fails)",
        "  1. Upload %s to public_html and extract it there, overwriting." % os.path.basename(rb_path),
        ("  2. Delete the %d file(s) listed in %s (new in this publish)." % (n_new, NEW_LIST) if n_new else
         "  2. Nothing to delete: no file is new in this publish."),
        "  3. Delete %s and the zip from public_html." % NEW_LIST,
    ]
    if checks:
        lines += ["", "BUILD CHECKS (read before uploading)"] + ["  " + c for c in checks]
    return "\n".join(lines) + "\n"


def write_json(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False, default=str)
