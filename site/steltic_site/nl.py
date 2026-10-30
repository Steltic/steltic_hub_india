"""solutions_india/nl/: the nonlinear pages and index.json, from the nonlinear run folders.

Ported from the second-wave build_nl2.sh. For every run id of the live index (titles and order) and any extra id:
  * the run folder is the first of the configured sources that holds <id>/four_analyses.html and takes the id (re-runs
    first, then the PEB set, then the October gold set, as before; a source may be limited to listed 'ids' or to the
    ids present in a 'marker_dir'); a source may list extra files to publish (the re-runs' stored ddm_viewer_3d.html);
  * optionally `snl report <run>` first, which rewrites the stored report pages IN the run folder (regenerate; off by
    default: a build never writes into run folders unless asked);
  * four_analyses.html, the viewer bundle and the nlrha / pushover report pages are copied;
  * ddm_report.html is rebuilt from ddm_results.json with the current code into a temporary folder
    (`steltic_ddm report <run> --out <tmp> --no-block`, the stored results are not rewritten), or copied as stored;
  * the index is rebuilt by steltic_nonlinear_india's scripts/build_site_index.py (--runs <base>, --previous <the live
    index>, --override <id>=<run> for a run outside the base set), and the titles files set the capability titles.
The NL code runs from the NL checkout with STELTIC_ENGINE_DIR (the HR engine its gravity source uses).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from .util import BuildError, copy, dump_index, load_json

PAGES = ("four_analyses.html", "steltic_viewer_bundle.html")
OPTIONAL_PAGES = ("nlrha/nlrha_report.html", "nlrha/DBE/nlrha_report.html", "nlrha/MCE/nlrha_report.html",
                  "nlrha/nlrha_viewer_3d.html", "pushover/pushover_report.html")


def _env(engine_dir):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.update(MPLBACKEND="Agg", RAG_API_URL="", PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0")
    if engine_dir:
        env["STELTIC_ENGINE_DIR"] = engine_dir
    return env


def pick_source(rid, sources):
    """(run folder, source dict) of a run id: the first source holding <id>/four_analyses.html that takes the id --
    a source with 'ids' takes only those ids; one with 'marker_dir' only the ids for which <marker_dir>/<id> exists
    (the October re-runs: a work folder also holds copies of units that were not re-run, so the published re-runs
    are the ones copied to the re-run folder)."""
    for s in sources:
        if s.get("ids") is not None and rid not in s["ids"]:
            continue
        if s.get("marker_dir") and not os.path.isdir(os.path.join(s["marker_dir"], rid)):
            continue
        d = os.path.join(s["dir"], rid)
        if os.path.isfile(os.path.join(d, "four_analyses.html")):
            return d, s
    return None, None


def build(ids, out_dir: str, *, sources, runs, previous_index, nl_repo=None, python=None, engine_dir=None,
          prebuilt_index=None, rebuild_ddm=True, regenerate=False, titles=None, work_dir=None, log=print):
    """Write out_dir (= solutions_india/nl). Returns (entries, problems, sources_map)."""
    os.makedirs(out_dir, exist_ok=True)
    env = _env(engine_dir)
    if (rebuild_ddm or regenerate or not prebuilt_index) and not (nl_repo and python):
        raise BuildError("the nonlinear part needs the steltic_nonlinear_india checkout and its interpreter "
                         "(nl.repo, nl.python) to rebuild the DDM reports and the index; or give nl.index and "
                         "nl.rebuild_ddm false")
    problems, src_map, overrides = [], {}, []
    tmp = tempfile.mkdtemp(prefix=".ddm_tmp.", dir=work_dir or os.path.dirname(os.path.abspath(out_dir)))
    try:
        for rid in ids:
            src, s = pick_source(rid, sources)
            if not src:
                problems.append("%s: no run folder with four_analyses.html in %s" % (rid, ", ".join(x["dir"] for x in sources)))
                continue
            if regenerate:
                r = subprocess.run([python, "-m", "snl", "report", src], cwd=nl_repo, env=env, capture_output=True,
                                   text=True)
                if r.returncode:
                    log("WARN snl report failed for %s" % rid)
            d = os.path.join(out_dir, rid)
            for f in PAGES:
                if not os.path.isfile(os.path.join(src, f)):
                    raise BuildError("%s: no %s in %s" % (rid, f, src))
                copy(os.path.join(src, f), os.path.join(d, f))
            for f in OPTIONAL_PAGES + tuple(s.get("copy") or ()):
                if os.path.isfile(os.path.join(src, f)):
                    copy(os.path.join(src, f), os.path.join(d, f))
            if os.path.isfile(os.path.join(src, "ddm_results.json")):
                done = False
                if rebuild_ddm:
                    t = os.path.join(tmp, rid)
                    os.makedirs(t, exist_ok=True)
                    shutil.copy2(os.path.join(src, "ddm_results.json"), t)
                    with open(os.path.join(t, "report.log"), "w") as lf:
                        r = subprocess.run([python, "-m", "steltic_ddm", "report", src, "--out", t, "--no-block"],
                                           cwd=nl_repo, env=env, stdout=lf, stderr=subprocess.STDOUT)
                    if r.returncode == 0 and os.path.isfile(os.path.join(t, "ddm_report.html")):
                        copy(os.path.join(t, "ddm_report.html"), os.path.join(d, "ddm_report.html"))
                        done = True
                    else:
                        log("WARN steltic_ddm report failed for %s: the stored ddm_report.html is published" % rid)
                if not done and os.path.isfile(os.path.join(src, "ddm_report.html")):
                    copy(os.path.join(src, "ddm_report.html"), os.path.join(d, "ddm_report.html"))
            if os.path.abspath(s["dir"]) != os.path.abspath(runs):
                overrides.append("%s=%s" % (rid, src))
            src_map[rid] = src
            log("nl %s <- %s" % (rid, src))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out_index = os.path.join(out_dir, "index.json")
    if prebuilt_index:
        shutil.copy2(prebuilt_index, out_index)
    else:
        cmd = [python, os.path.join("scripts", "build_site_index.py"), "--runs", runs, "--previous", previous_index]
        for o in overrides:
            cmd += ["--override", o]
        cmd += ["--out", os.path.abspath(out_index)]
        r = subprocess.run(cmd, cwd=nl_repo, env=env, capture_output=True, text=True)
        if r.returncode:
            raise BuildError("the nonlinear index could not be built: %s"
                             % (((r.stderr or r.stdout or "").strip().splitlines() or ["exit %d" % r.returncode])[-1]))
    d = load_json(out_index)
    n = 0
    for e in d.get("examples") or []:
        if e["id"] in (titles or {}):
            t = titles[e["id"]]
            if t.get("title"):
                e["title"] = t["title"]
            if t.get("blurb"):
                e["blurb"] = t["blurb"]
            n += 1
    if titles is not None:
        dump_index(d, out_index)
        log("nl titles updated: %d" % n)
    entries = d.get("examples") or []
    for e in entries:
        for k in ("summary", "viewer", "pushover", "nlrha", "ddm"):
            v = e.get(k)
            if v and not os.path.isfile(os.path.join(out_dir, v)):
                problems.append("%s: %s link %s does not resolve" % (e["id"], k, v))
    log("nl examples: %d" % len(entries))
    for x in problems:
        log("PROBLEM %s" % x)
    return entries, problems, src_map
