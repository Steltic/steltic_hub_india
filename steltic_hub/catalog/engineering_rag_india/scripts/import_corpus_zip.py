#!/usr/bin/env python3
"""Replace the workspace corpus with a fixed corpus zip (the corpus-fix step's result).

    python3 scripts/import_corpus_zip.py FIXED.zip --root <corpus workspace>
                                         [--rebuild] [--hub-url URL --module ID] [--dry-run]

The corpus-fix step (CORPUS_FIX_LLM_INSTRUCTIONS.md at the root of the hub repository) sends your
first-pass corpus, your licensed PDFs and the instructions to an LLM agent, which returns a fixed
corpus as a zip. This script puts that zip in place of the workspace corpus:

1. Checks the zip: the corpus folders may sit at the top or under one folder (`grokbot/`, `corpus/` ...);
   it needs documents/standards/<STEM>/; absolute paths, `..`, links and oversized archives are refused.
2. Unpacks it into a staging folder beside the workspace.
3. Asks the hub to stop the IS corpus grounding server (--hub-url; it holds the index open), then
   moves the current documents/, indexes/, search/, cache/, INDIA_MANIFEST.json, FIX_REPORT.md and the
   data files of scripts/ (*.json) into <workspace>_backups/<timestamp>/, and the staged ones into their
   place (the layout is the one CORPUS_FIX_LLM_INSTRUCTIONS.md section 1.2 asks the agent for).
   If a move fails (a file still open), everything moved so far is put back and nothing changes.
4. The workspace code is never replaced: scripts/*.py from the zip are NOT applied (the hub's own
   copies stay; the zip's are kept in the backup folder under zip_scripts_not_applied/ for review).
   The zip's scripts/*.json (quality.json, bis_manual_sections.json, is811_manual.json,
   is808_fixes.json ...) ARE applied: they are the corpus data the scripts read. PDFs in the zip
   are ignored.
5. With --rebuild: scripts/build_index.py --no-repair (the fixed corpus's indexes are kept as they came),
   then scripts/validate.py --corpus. Then the grounding server is started again.

Exit 0 = imported (and, with --rebuild, validated); 1 = validation failed after the import;
2 = nothing changed (bad zip, or the swap could not be done).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

DATA_DIRS = ("documents", "indexes", "search")   # replaced from the zip
DATA_FILES = ("INDIA_MANIFEST.json", "FIX_REPORT.md")   # FIX_REPORT.md: what the corpus-fix step did
MAX_UNCOMPRESSED = 20 * 1024 ** 3                  # 20 GB: a real corpus is well under 1 GB


def log(msg: str) -> None:
    print(f"[import] {msg}", flush=True)


def corpus_prefix(names: list[str]) -> str:
    """The folder inside the zip that holds documents/standards/ ('' when it is at the top)."""
    prefixes = set()
    for n in names:
        p = n.replace("\\", "/")
        i = p.find("documents/standards/")
        if i >= 0 and (i == 0 or p[i - 1] == "/"):
            prefixes.add(p[:i])
    if not prefixes:
        raise SystemExit("the zip holds no documents/standards/<STEM>/ folder -- is it a corpus zip?")
    if len(prefixes) > 1:
        raise SystemExit(f"the zip holds more than one corpus ({', '.join(sorted(prefixes)) or '(top)'}) -- "
                         "zip one corpus folder")
    return prefixes.pop()


def check_member(info: zipfile.ZipInfo) -> None:
    n = info.filename.replace("\\", "/")
    pp = PurePosixPath(n)
    if n.startswith("/") or (len(n) > 1 and n[1] == ":") or ".." in pp.parts:
        raise SystemExit(f"unsafe path in the zip: {info.filename!r}")
    mode = (info.external_attr >> 16) & 0o170000
    if mode == 0o120000:
        raise SystemExit(f"the zip holds a symbolic link ({info.filename!r}) -- refused")


def plan(zf: zipfile.ZipFile) -> tuple[str, list[zipfile.ZipInfo], list[str], list[str]]:
    """-> (prefix, members to extract, stems, ignored names)"""
    infos = zf.infolist()
    total = 0
    for i in infos:
        check_member(i)
        total += i.file_size
    if total > MAX_UNCOMPRESSED:
        raise SystemExit(f"the zip unpacks to {total / 1024 ** 3:.1f} GB -- refused (limit {MAX_UNCOMPRESSED // 1024 ** 3} GB)")
    prefix = corpus_prefix([i.filename for i in infos])
    take, ignored = [], []
    stems = set()
    for i in infos:
        n = i.filename.replace("\\", "/")
        if not n.startswith(prefix) or i.is_dir():
            if not i.is_dir():
                ignored.append(n)
            continue
        rel = n[len(prefix):]
        top = rel.split("/", 1)[0]
        if rel.lower().endswith(".pdf"):
            ignored.append(n)
        elif top in DATA_DIRS or rel in DATA_FILES or (top == "scripts" and "/" in rel):
            take.append(i)
            parts = rel.split("/")
            if len(parts) > 3 and parts[0] == "documents" and parts[1] == "standards":
                stems.add(parts[2])
        else:
            ignored.append(n)
    if not stems:
        raise SystemExit("the zip holds no documents/standards/<STEM>/ document folder")
    return prefix, take, sorted(stems), ignored


def hub_call(hub_url: str, module: str, what: str) -> bool:
    if not hub_url or not module or "{" in hub_url:
        return False
    url = f"{hub_url.rstrip('/')}/api/modules/{module}/server/{what}"
    try:
        req = urllib.request.Request(url, data=b"{}", method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return 200 <= r.status < 300
    except Exception as e:                                # the hub may not be there (a terminal run)
        log(f"could not ask the hub to {what} the grounding server ({type(e).__name__}); continuing")
        return False


def swap(root: Path, stage: Path, backup: Path) -> list[str]:
    """Move the current data aside and the staged data in; roll back on any failure."""
    moved: list[tuple[Path, Path]] = []        # (from, to) in the order done
    try:
        backup.mkdir(parents=True, exist_ok=False)
        for name in DATA_DIRS + ("cache",) + DATA_FILES:
            cur = root / name
            if cur.exists():
                shutil.move(str(cur), str(backup / name))
                moved.append((cur, backup / name))
        sdata = root / "scripts"
        for f in sorted(sdata.glob("*.json")) if sdata.is_dir() else []:
            (backup / "scripts").mkdir(exist_ok=True)
            shutil.move(str(f), str(backup / "scripts" / f.name))
            moved.append((f, backup / "scripts" / f.name))
        for name in DATA_DIRS + DATA_FILES:
            src = stage / name
            if src.exists():
                shutil.move(str(src), str(root / name))
                moved.append((src, root / name))
        ssrc = stage / "scripts"
        applied = []
        for f in sorted(ssrc.iterdir()) if ssrc.is_dir() else []:
            if f.is_file() and f.suffix.lower() == ".json":
                (root / "scripts").mkdir(exist_ok=True)
                shutil.move(str(f), str(root / "scripts" / f.name))
                moved.append((f, root / "scripts" / f.name))
                applied.append(f.name)
        (root / "cache").mkdir(exist_ok=True)
        return applied
    except OSError as e:
        for a, b in reversed(moved):
            try:
                shutil.move(str(b), str(a))
            except OSError:
                pass
        raise SystemExit(f"could not replace the corpus ({e}); nothing was changed. A file is probably still "
                         "open: stop the IS corpus server (Modules page) and close anything reading the "
                         "workspace, then import again")


def run_step(root: Path, *args: str) -> int:
    cmd = [sys.executable, str(root / "scripts" / args[0]), *args[1:]]
    log("running " + " ".join(Path(c).name if i < 2 else c for i, c in enumerate(cmd)))
    return subprocess.call(cmd, cwd=str(root))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("zip", type=Path, help="the fixed corpus zip")
    ap.add_argument("--root", type=Path, required=True, help="the corpus workspace (<hub data>/grokbot)")
    ap.add_argument("--backup-dir", type=Path, default=None,
                    help="where the replaced corpus goes (default: <root>_backups/<timestamp>)")
    ap.add_argument("--rebuild", action="store_true", help="Rebuild index (--no-repair) and Validate afterwards")
    ap.add_argument("--hub-url", default=os.environ.get("STELTIC_HUB_URL", ""))
    ap.add_argument("--module", default="engineering_rag_india", help="the hub module whose server holds the index")
    ap.add_argument("--dry-run", action="store_true", help="check the zip and report; change nothing")
    a = ap.parse_args(argv)

    root = a.root.resolve()
    zp = a.zip.resolve()
    if not zp.is_file():
        log(f"no such file: {zp}")
        return 2
    try:
        with zipfile.ZipFile(zp) as zf:
            prefix, take, stems, ignored = plan(zf)
            log(f"{zp.name}: corpus folder {prefix or '(top level)'}; {len(take)} files; documents: {', '.join(stems)}")
            if ignored:
                log(f"ignored ({len(ignored)}): " + ", ".join(ignored[:8]) + (" ..." if len(ignored) > 8 else ""))
            if a.dry_run:
                log("dry run: nothing changed")
                return 0
            stage = root.parent / f".{root.name}_import_{time.strftime('%Y%m%d-%H%M%S')}"
            shutil.rmtree(stage, ignore_errors=True)
            stage.mkdir(parents=True)
            for i in take:
                rel = i.filename.replace("\\", "/")[len(prefix):]
                out = stage / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(i) as src, open(out, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    except zipfile.BadZipFile as e:
        log(f"not a readable zip: {e}")
        return 2
    except SystemExit as e:
        log(str(e))
        return 2

    backup = (a.backup_dir or (root.parent / f"{root.name}_backups")) / time.strftime("%Y%m%d-%H%M%S")
    stopped = hub_call(a.hub_url, a.module, "stop")
    if stopped:
        log("grounding server stopped")
    try:
        applied = swap(root, stage, backup)
    except SystemExit as e:
        log(str(e))
        shutil.rmtree(stage, ignore_errors=True)
        if stopped:
            hub_call(a.hub_url, a.module, "start")
        return 2
    zs = stage / "scripts"
    if zs.is_dir() and any(zs.iterdir()):
        shutil.move(str(zs), str(backup / "zip_scripts_not_applied"))
        log(f"the zip's scripts/*.py were NOT applied (the hub's code stays); kept for review in "
            f"{backup / 'zip_scripts_not_applied'}")
    shutil.rmtree(stage, ignore_errors=True)
    (backup / "IMPORT.json").write_text(json.dumps({
        "zip": str(zp), "imported_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "documents": stems,
        "data_files_applied": applied, "root": str(root)}, indent=2) + "\n", encoding="utf-8")
    log(f"imported {len(stems)} documents into {root}")
    if applied:
        log("data files applied to scripts/: " + ", ".join(applied))
    log(f"the previous corpus is in {backup} (move it back to undo)")

    rc = 0
    if a.rebuild:
        if run_step(root, "build_index.py", "--root", str(root), "--no-repair") != 0:
            log("Rebuild index FAILED -- the imported corpus is in place; see the log above")
            rc = 1
        elif run_step(root, "validate.py", "--corpus", "--root", str(root)) != 0:
            log("Validate reported failures -- see above")
            rc = 1
    else:
        log("next: Rebuild index (tick 'Skip the per-document repair'), then Validate corpus")
    if stopped:
        hub_call(a.hub_url, a.module, "start")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
