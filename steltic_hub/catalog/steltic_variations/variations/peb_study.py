"""PEB lever study, run from the Variations tab (2026-10 second wave).

HR Steel's deterministic lever study -- steltic_india `scripts/peb_lever_study.py` with the auto-sizer
`steel_engine/peb_autosize.py` -- re-sizes a PEB shed under each Indian PEB practice lever and tabulates the steel in
kg/m² of plan. No language model is involved. This module runs it for one project, as one child process, in HR Steel's
own checkout and interpreter. The hub resolves both from its registry and hands them over in the environment
(manifest `uses` + `env_vars`), the way it runs any module command:

  STELTIC_INDIA_DIR     {need.steltic_india}       HR Steel's checkout (scripts/, steel_engine/)
  STELTIC_INDIA_PYTHON  {python.steltic_india}     its interpreter (HR Steel's own venv)
  STELTIC_CFS_DIR       {need.steltic_CFS_india}   CFS Steel's checkout: the study re-designs the purlins and girts with it
  STELTIC_CFS_PYTHON    {python.steltic_CFS_india} CFS Steel's own interpreter (third wave): the runner's --cfs-python

A variable is unset while its module is not installed, and the tab then says what is missing.

The study runner itself decides which variants a job has and what each one's code status is. Third wave (2026-10,
handoff P11): the runner states it in its contract, `scripts/peb_lever_study.py --list-variants --json` (its switches,
where the job's spec comes from, the variants of each ladder with their code status; nothing is sized), and a project
with no hand-written job spec gets one inferred from its own cfg.py (the runner's scripts/peb_lever_infer.py), so an
engineer's own PEB job needs no spec. A run passes --cfs-python (the CFS engine in its own interpreter) and
--progress-json (one JSON line per progress event) when the runner offers them. A runner without the contract (the
PEB variations release) is still read the older way: its --help, and an import of the script for its job specs and
variants_for.

Inside the project folder (the hub's projects folder, VARIATIONS_JOBS/<project>):
  variations/peb_study/input/<project>/  HR Steel's design of this project (its package, unpacked) -- used when the
                                         project folder itself holds no cfg.py
  variations/peb_study/work/             the runner's --work: work/<project>/study.html, study.csv, study.json,
                                         work/index.html, study_all.json, study_tables.md, a folder per variant
  variations/peb_study/run.json          the last run: command, variants, job folder, input hash, start / end, exit code
  variations/peb_study/study.log         everything the runner printed
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Callable, Optional

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SCRIPT = ("scripts", "peb_lever_study.py")
JOB_SPECS_FILE = ("scripts", "peb_lever_jobs.py")
LEVERS_FILE = ("steel_engine", "peb_levers.py")
MAX_RUNS_DEFAULT = 8            # the runner's own default (--max-runs)
MAX_RUNS_CAP = 30
PROBE_TIMEOUT = 120.0

# What the tab lists, in this order: (file, label, where) -- 'job' = work/<job>/, 'work' = work/, 'base' = peb_study/
OUTPUTS = (
    ("study.html", "Study (kg/m² by group per variant, the cumulative waterfall, governing checks, member sizes)", "job"),
    ("study.csv", "Table (CSV)", "job"),
    ("study.json", "Study data (JSON)", "job"),
    ("index.html", "Every studied job on one page", "work"),
    ("study_tables.md", "Summary tables (Markdown)", "work"),
    ("study_all.json", "Every studied job (JSON)", "work"),
    ("study.log", "Run log", "base"),
)
_MEDIA = {".html": "text/html; charset=utf-8", ".csv": "text/csv; charset=utf-8", ".json": "application/json",
          ".md": "text/plain; charset=utf-8", ".log": "text/plain; charset=utf-8"}

# The runner's progress lines: "== <job> / <variant>" when a variant starts (peb_autosize.autosize) and
# "== <job> / <variant>: done (resume)" when --resume skips one that is done (peb_lever_study.run_job); with
# --stop-after-min, "== <job>: time budget of <n> min reached before '<variant>'; re-run with --resume to continue"
# or "== <job>: not started (time budget reached); ..." when it ends a resumable chunk.
_STEP = re.compile(r"^== (?P<job>\S+) / (?P<name>\S+?)(?P<resumed>: done \(resume\))?\s*$")
_BUDGET = re.compile(r"^== (?P<job>\S+): (?:time budget of .* reached before '(?P<before>[^']*)'|not started \(time budget reached\))")


class ProbeError(RuntimeError):
    pass


# ---------------------------------------------------------------- what the hub resolved
def paths() -> dict:
    """HR Steel's checkout, interpreter and study script, and CFS Steel's checkout and interpreter ('' when not
    installed)."""
    d = (os.environ.get("STELTIC_INDIA_DIR") or "").strip()
    return {"dir": d, "python": (os.environ.get("STELTIC_INDIA_PYTHON") or "").strip(),
            "cfs": (os.environ.get("STELTIC_CFS_DIR") or "").strip(),
            "cfs_python": (os.environ.get("STELTIC_CFS_PYTHON") or "").strip(),
            "script": str(pathlib.Path(d, *SCRIPT)) if d else ""}


def cfs_python_for_run(p: dict, cli: dict) -> str:
    """The interpreter to pass as --cfs-python: CFS Steel's own, when the runner takes the switch and that interpreter
    exists and is not HR Steel's ('' otherwise: the runner then uses its own, as the PEB variations release did)."""
    py = p.get("cfs_python") or ""
    if not cli.get("cfs_python") or not py or not pathlib.Path(py).exists():
        return ""
    try:
        if p.get("python") and pathlib.Path(py).resolve() == pathlib.Path(p["python"]).resolve():
            return ""
    except OSError:
        pass
    return py


def runner_problem(p: dict) -> str:
    """Why the study runner cannot be used ('' when it can)."""
    if not p["dir"] or not pathlib.Path(p["dir"]).is_dir():
        return ("HR Steel (IS 800) is not installed: the lever study runs in its checkout and environment. "
                "Install it from the Modules tab.")
    if not p["python"] or not pathlib.Path(p["python"]).exists():
        return "HR Steel (IS 800) has no environment yet: install (or rebuild) it from the Modules tab."
    if not pathlib.Path(p["script"]).is_file():
        return ("The installed HR Steel (IS 800) has no PEB lever study (scripts/peb_lever_study.py arrives with the "
                "PEB variations release): update HR Steel from the Modules tab, or link a working copy that has it.")
    return ""


def cfs_problem(p: dict) -> str:
    if not p["cfs"] or not (pathlib.Path(p["cfs"]) / "steel_engine").is_dir():
        return ("CFS Steel (IS 801) is not installed: the study re-designs the purlins and girts with its engine "
                "(IS 811 zeds and the generic thin zeds). Install it from the Modules tab.")
    return ""


def child_env(p: dict) -> dict:
    """The environment of a runner process: this server's (which the hub built from the manifest, so it carries
    INDIA_CORPUS_ROOT) without a stray PYTHONPATH, unbuffered, hash seed 0 as in every run of the auto-sizer, no
    bytecode written into HR Steel's checkout (a linked working copy is never written to), the CFS engine named."""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env.update(PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1",
               MPLBACKEND="Agg")
    if p.get("cfs"):
        env["STELTIC_CFS_ROOT"] = p["cfs"]
    return env


# ---------------------------------------------------------------- the runner's own switches and variant list
# The variant list of a job for each --ladder the runner offers (a runner without --ladder: its one list).
PROBE = r'''
import inspect, json, os, sys
root, job, ladders = sys.argv[1], sys.argv[2], [x for x in sys.argv[3].split(",") if x]
sys.path[:0] = [os.path.join(root, "steel_engine"), os.path.join(root, "scripts")]
import peb_lever_study as S
label = getattr(S, "label", lambda n: S.LABELS.get(n, n))
takes_ladder = "ladder" in inspect.signature(S.variants_for).parameters
out = {"jobs": sorted(S.JOB_SPECS), "job": None, "takes_ladder": takes_ladder}
spec = S.JOB_SPECS.get(job)
if spec is not None:
    solar = job.startswith("IN_Ex1_")      # as peb_lever_study.main(): --solar adds its row to IN_Ex1 only
    by_ladder = {}
    for lad in (ladders if takes_ladder else ["full"]):
        rows = []
        for name, kw, status, note in S.variants_for(spec, solar=solar, **({"ladder": lad} if takes_ladder else {})):
            rows.append({"name": name, "label": label(name), "status": status, "note": note, "applies": kw is not None,
                         "alias": (kw or {}).get("alias"), "start_from": (kw or {}).get("start_from")})
        by_ladder[lad] = rows
    sz = spec.get("seismic") or {}
    out["job"] = {"name": job, "zone": sz.get("zone"), "I": sz.get("I"), "route": sz.get("route"),
                  "dado_height_mm": spec.get("dado_height_mm"), "base": spec.get("base"), "solar": solar,
                  "variant_order": list(getattr(S, "VARIANT_ORDER", ())), "variants_by_ladder": by_ladder}
print(json.dumps(out, default=str))
'''
_PROBE_CACHE: dict = {}
_FLAG = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")
CONTRACT_NAME = "peb_lever_study"
INFER_FILE = ("scripts", "peb_lever_infer.py")


def _run(p: dict, args: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([p["python"], "-I", "-B", *args], cwd=p["dir"], env=child_env(p), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=PROBE_TIMEOUT,
                              stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ProbeError(f"{type(e).__name__}: {e}")


def read_cli(p: dict) -> dict:
    """The runner's command line, read from its own --help (argparse prints it and exits; nothing runs): the flags
    it takes and the --ladder choices it offers (['full'] for a runner without the switch)."""
    r = _run(p, [p["script"], "--help"])
    if r.returncode != 0 or "--job" not in (r.stdout or ""):
        raise ProbeError(((r.stderr or "").strip().splitlines() or ["--help gave exit code %s" % r.returncode])[-1][:400])
    text = r.stdout
    m = re.search(r"--ladder \{([^}]*)\}", text)
    return {"flags": sorted(set(_FLAG.findall(text))),
            "ladders": [x.strip() for x in m.group(1).split(",") if x.strip()] if m else ["full"]}


def _stamp(p: dict) -> tuple:
    out = []
    for rel in (SCRIPT, JOB_SPECS_FILE, LEVERS_FILE, INFER_FILE):
        try:
            out.append(pathlib.Path(p["dir"], *rel).stat().st_mtime)
        except OSError:
            out.append(None)
    return tuple(out)


def _job_stamp(jobs_root: str, job: str):
    """What an inferred spec depends on: the job's cfg.py and secondary scripts (mtime and size)."""
    if not jobs_root:
        return None
    d = pathlib.Path(jobs_root) / job
    out = []
    for f in [d / "cfg.py"] + sorted((d / "secondary").glob("*.py")):
        try:
            st = f.stat()
            out.append((f.name, st.st_mtime, st.st_size))
        except OSError:
            out.append((f.name, None, None))
    return tuple(out)


def read_contract(p: dict, job: str, jobs_root: str = "") -> Optional[dict]:
    """The runner's contract (scripts/peb_lever_study.py --list-variants --json --job <job> [--jobs-root <dir>]): its
    switches and, for the job, where its spec comes from and its variants per ladder. None for a runner without the
    switch (the PEB variations release), which argparse refuses with 'unrecognized arguments'."""
    args = [p["script"], "--list-variants", "--json", "--job", job]
    if jobs_root:
        args += ["--jobs-root", str(jobs_root)]
    r = _run(p, args)
    if r.returncode != 0:
        if "unrecognized arguments" in (r.stderr or "") and "--list-variants" in (r.stderr or ""):
            return None
        raise ProbeError(((r.stderr or "").strip().splitlines() or ["--list-variants gave exit code %s" % r.returncode])[-1][:400])
    for line in reversed((r.stdout or "").strip().splitlines()):
        try:
            c = json.loads(line)
        except ValueError:
            continue
        if isinstance(c, dict) and c.get("contract") == CONTRACT_NAME:
            return c
    raise ProbeError("the runner's --list-variants answer is not its contract")


def _from_contract(c: dict, job: str) -> dict:
    """The probe's answer from the runner's contract (the same shape as the older probe's)."""
    o = c.get("options") or {}
    flags = ["--job", "--jobs-root", "--work", "--cfs-root", "--published-root", "--variants", "--max-runs",
             "--resume", "--solar", "--render", "--list-variants", "--json"]
    for k, f in (("ladders", "--ladder"), ("stop_after_min", "--stop-after-min"), ("cfs_python", "--cfs-python"),
                 ("progress_json", "--progress-json"), ("control", "--control"), ("init_margins", "--init-margins")):
        if o.get(k):
            flags.append(f)
    cli = {"flags": sorted(set(flags)), "ladders": list(o.get("ladders") or ["full"]),
           "stop_after_min": bool(o.get("stop_after_min")), "cfs_python": bool(o.get("cfs_python")),
           "progress_json": bool(o.get("progress_json")), "spec_from_cfg": bool(o.get("spec_from_cfg")),
           "contract": c.get("version"), "progress_prefix": (c.get("progress") or {}).get("prefix") or ""}
    out = {"jobs": sorted(c.get("specs") or []), "job": None, "takes_ladder": True, "cli": cli, "contract": True,
           "job_error": []}
    j = (c.get("jobs") or {}).get(job) or {}
    if j and not j.get("error"):
        by = {}
        for lad, rows in (j.get("variants_by_ladder") or {}).items():
            by[lad] = [dict(v, compliance=v.get("compliance") or compliance(v.get("status"))) for v in rows]
        out["job"] = {"name": job, "zone": j.get("zone"), "I": j.get("I"), "route": j.get("route"),
                      "dado_height_mm": j.get("dado_height_mm"), "base": j.get("base"), "solar": bool(j.get("solar")),
                      "variant_order": list(c.get("variant_order") or ()), "variants_by_ladder": by,
                      "spec_source": j.get("spec_source"), "spec_notes": list(j.get("spec_notes") or []),
                      "members": list(j.get("members") or []),
                      "inferred": bool(j.get("spec_source")) and job not in (c.get("specs") or [])}
        out["job"]["variants"] = by.get("full") or next(iter(by.values()), [])
    else:
        out["job_error"] = list(j.get("error") or [])
    return out


def probe(p: dict, job: str, jobs_root: str = "") -> dict:
    """The runner's command line, its job specs and, for `job`, its variants for each --ladder it offers (name, label,
    status, note, applies, alias, start_from): from the runner's contract (--list-variants --json; a job with no
    hand-written spec gets one inferred from <jobs_root>/<job>/cfg.py), or, from a runner without it, by running
    --help and importing the script in HR Steel's interpreter (variants_for is pure: no analysis runs). Cached until
    one of the runner's files, or the job's cfg.py, changes. job['variants'] is the full ladder's list."""
    key = (p["python"], p["dir"], job, str(jobs_root or ""), _stamp(p), _job_stamp(jobs_root, job))
    if key in _PROBE_CACHE:
        return _PROBE_CACHE[key]
    c = read_contract(p, job, jobs_root)
    if c is not None:
        out = _from_contract(c, job)
        _PROBE_CACHE[key] = out
        return out
    cli = read_cli(p)
    r = _run(p, ["-c", PROBE, p["dir"], job, ",".join(cli["ladders"])])
    lines = (r.stdout or "").strip().splitlines()
    if r.returncode != 0 or not lines:
        raise ProbeError(((r.stderr or "").strip().splitlines() or ["exit code %s" % r.returncode])[-1][:400])
    try:
        out = json.loads(lines[-1])
    except ValueError as e:
        raise ProbeError(f"unreadable answer ({e})")
    if not out.get("takes_ladder"):
        cli["ladders"] = ["full"]
    out["cli"] = dict(cli, stop_after_min="--stop-after-min" in cli["flags"], cfs_python=False, progress_json=False,
                      spec_from_cfg=False, contract=None, progress_prefix="")
    out["contract"] = False
    job_meta = out.get("job")
    if job_meta:
        for rows in job_meta["variants_by_ladder"].values():
            for v in rows:
                v["compliance"] = compliance(v.get("status"))
        job_meta["variants"] = job_meta["variants_by_ladder"].get("full") or next(iter(job_meta["variants_by_ladder"].values()))
    _PROBE_CACHE[key] = out
    return out


def compliance(status) -> str:
    """The runner's own reading of a status (peb_lever_study._tag): 'not compliant' when it says NOT or 'not
    compliant'; 'compliant' for compliant / permitted / published / strict; 'n/a'; anything else is a 'reading' --
    a reading an owner ruling rejected, the old unconservative treatment, a code conflict for the engineer to decide."""
    s = str(status or "")
    if "NOT" in s or "not compliant" in s:
        return "not compliant"
    if s == "n/a":
        return "n/a"
    if s.startswith(("compliant", "published", "strict", "permitted")):
        return "compliant"
    return "reading"


def expand_selection(variants: list[dict], chosen: list[str]) -> list[str]:
    """The --variants list for the runner, from what the engineer ticked, in the runner's own order:

    * the strict baseline is always in (every lever starts from it; the runner runs it first anyway);
    * a step of the cumulative ladder brings the whole ladder, since each step starts from the previous one, and
      'all compliant levers' (a copy of the last compliant step);
    * 'all compliant levers' alone brings the ladder up to the step it copies;
    * a row the runner fills by copying another (its 'alias': the first ladder step copies its lever alone; on the
      short ladder, 'all compliant levers' with a single compliant lever copies that lever) brings the row it copies.
    Raises ValueError for a name the runner does not offer for this job, or one it marks n/a."""
    by = {v["name"]: v for v in variants}
    unknown = [c for c in chosen if c not in by]
    if unknown:
        raise ValueError("not a variant of this job's lever study: " + ", ".join(unknown))
    na = [c for c in chosen if not by[c]["applies"]]
    if na:
        raise ValueError("does not apply to this job: " + "; ".join(f"{c} ({by[c].get('note') or 'n/a'})" for c in na))
    want = {"strict"} | set(chosen)
    ladder = [v["name"] for v in variants if v["name"].startswith("cum:") and v["applies"]]
    if any(n.startswith("cum:") for n in chosen):
        want |= set(ladder)
        if by.get("compliant", {}).get("applies"):
            want.add("compliant")
    elif "compliant" in want and by["compliant"].get("alias") in ladder:
        want |= set(ladder[:ladder.index(by["compliant"]["alias"]) + 1])
    grew = True
    while grew:
        grew = False
        for n in list(want):
            src = (by.get(n) or {}).get("alias")
            if src and src in by and src not in want:
                want.add(src)
                grew = True
    return [v["name"] for v in variants if v["name"] in want and v["applies"]]


def sized_runs(variants: list[dict], selection: list[str]) -> list[str]:
    """The variants of a selection the auto-sizer actually sizes (an alias row is copied, not run)."""
    by = {v["name"]: v for v in variants}
    return [n for n in selection if not by.get(n, {}).get("alias")]


# ---------------------------------------------------------------- the job folder
def job_source(project: str, jobs_dir: pathlib.Path, base: pathlib.Path, hr_url: str) -> dict:
    """Which job folder the study reads (it only reads it: every variant runs in a copy under work/).

    The project folder, when it holds cfg.py -- a PEB job written the way the golds are, put there by the engineer
    or staged by the Nonlinear module. Otherwise HR Steel's design of this project: its package, fetched through HR
    Steel's own API when the study starts (as "Load from this project's HR Steel design" does) and unpacked under
    variations/peb_study/input/."""
    proj = pathlib.Path(jobs_dir) / project
    if (proj / "cfg.py").is_file():
        return {"kind": "project", "dir": str(proj), "jobs_root": str(pathlib.Path(jobs_dir)),
                "label": "the project folder (cfg.py, secondary/, design/)"}
    if hr_url:
        return {"kind": "hr", "dir": str(base / "input" / project), "jobs_root": str(base / "input"),
                "label": "HR Steel's design of this project (its package, fetched when the study starts)"}
    return {"kind": None, "dir": "", "jobs_root": "", "label": ""}


def stage_package(files: dict, dest: pathlib.Path) -> int:
    """Unpack HR Steel's package ({relative path: bytes}, the <building>/ prefix already stripped) into `dest`,
    replacing what was there. Refuses a package without cfg.py; never writes outside `dest`."""
    if "cfg.py" not in files:
        raise ValueError("HR Steel's package of this project holds no cfg.py: there is no job to re-size")
    dest = pathlib.Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    root = dest.resolve()
    n = 0
    for rel, data in sorted(files.items()):
        target = (root / rel).resolve()
        if root not in target.parents:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        n += 1
    return n


def input_hash(job_dir) -> str:
    """sha1 of what the sizer reads from the job: cfg.py and the secondary design script(s)."""
    d = pathlib.Path(job_dir)
    h = hashlib.sha1()
    for p in [d / "cfg.py"] + sorted((d / "secondary").glob("*.py")):
        if p.is_file():
            h.update(p.relative_to(d).as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()[:16]


def command(p: dict, job: str, src: dict, work, variants: list[str], max_runs: int, resume: bool,
            solar: bool, ladder: Optional[str] = None, stop_after_min: float = 0.0, cfs_python: str = "",
            progress_json: bool = False) -> list[str]:
    """The runner's command line (its contract, or its --help): the job, the folder that holds it (also the
    'published' root, so the job's own take-off is the 'as published' row), the work folder, the CFS checkout;
    --ladder, --stop-after-min, --cfs-python and --progress-json only when given (a runner that offers them)."""
    cmd = [p["python"], "-u", "-B", p["script"], "--job", job, "--jobs-root", src["jobs_root"], "--work", str(work),
           "--cfs-root", p["cfs"], "--published-root", src["jobs_root"], "--variants", ",".join(variants),
           "--max-runs", str(int(max_runs))]
    if ladder:
        cmd += ["--ladder", ladder]
    if stop_after_min and stop_after_min > 0:
        cmd += ["--stop-after-min", ("%g" % stop_after_min)]
    if cfs_python:
        cmd += ["--cfs-python", cfs_python]
    if progress_json:
        cmd.append("--progress-json")
    if resume:
        cmd.append("--resume")
    if solar:
        cmd.append("--solar")
    return cmd


# ---------------------------------------------------------------- results on disk
def base_dir(jobs_dir, project: str) -> pathlib.Path:
    return pathlib.Path(jobs_dir) / project / "variations" / "peb_study"


def read_record(base) -> dict:
    try:
        return json.loads((pathlib.Path(base) / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_record(base, rec: dict):
    base = pathlib.Path(base)
    base.mkdir(parents=True, exist_ok=True)
    tmp = base / "run.json.tmp"
    tmp.write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, base / "run.json")


def study_meta(work, job: str) -> dict:
    """What work/<job>/study.json says about the study as a whole: its ladder (a resumed study cannot switch it),
    the variant a --stop-after-min budget stopped before, when it started and finished."""
    try:
        st = json.loads((pathlib.Path(work) / job / "study.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: st.get(k) for k in ("ladder", "stopped_before", "started", "finished")}


def study_rows(work, job: str, order: list[str] | None = None) -> list[dict]:
    """The rows of work/<job>/study.json, in the runner's variant order: what is done so far."""
    try:
        st = json.loads((pathlib.Path(work) / job / "study.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = st.get("rows") or {}
    names = [n for n in (order or []) if n in rows] + [n for n in rows if n not in (order or [])]
    out = []
    for n in names:
        r = rows[n] or {}
        g = r.get("governing")
        gov = ""
        if isinstance(g, (list, tuple)) and g and isinstance(g[0], (int, float)):
            gov = "D/C %.3f · %s" % (g[0], " ".join(str(x) for x in g[2:4] if x))
        out.append({"name": n, "label": r.get("label") or n, "status": r.get("status") or "",
                    "compliance": compliance(r.get("status")), "kg_m2": (r.get("kg_m2") or {}).get("total"),
                    "delta_vs_strict_kg_m2": r.get("delta_vs_strict_kg_m2") if n != "strict" else None,
                    "governing": gov, "error": r.get("error") or "", "note": r.get("note") or "",
                    "done": bool(r.get("done")), "n_full_runs": r.get("n_full_runs"), "elapsed_s": r.get("elapsed_s"),
                    "alias_of": r.get("alias_of")})
    return out


def output_path(base, job: str, name: str) -> Optional[pathlib.Path]:
    base = pathlib.Path(base)
    for f, _label, where in OUTPUTS:
        if f == name:
            return {"job": base / "work" / job, "work": base / "work", "base": base}[where] / f
    return None


def outputs(base, job: str) -> list[dict]:
    out = []
    for f, label, _where in OUTPUTS:
        p = output_path(base, job, f)
        if p is not None and p.is_file():
            st = p.stat()
            out.append({"name": f, "label": label, "bytes": st.st_size, "mtime": st.st_mtime})
    return out


def media_type(name: str) -> str:
    return _MEDIA.get(pathlib.Path(name).suffix.lower(), "application/octet-stream")


# ---------------------------------------------------------------- the status the tab shows
def status(project: str, jobs_dir, base_brief: str, hr_url: str, building_kind: Callable[[str], Optional[str]]) -> dict:
    """Everything the PEB lever study pane needs: does the study apply to this project (and if not, why), is it
    ready to run, the runner's variants with their code status, the job folder it would read, the live / last run,
    the rows done so far and the output files."""
    p = paths()
    base = base_dir(jobs_dir, project)
    rec = read_record(base)
    run = current()
    live = bool(run and run.project == project and not run.finished)
    if rec.get("status") == "running" and not live:
        rec["status"] = "interrupted"          # the module server stopped under it; --resume picks the rows up
    out = {"project": project, "is_peb_job": False, "ready": False, "reasons": [], "job": None, "source": None,
           "spec_jobs": [], "runner": {k: p[k] for k in ("dir", "python", "cfs", "cfs_python", "script")},
           "cli": {"flags": [], "ladders": ["full"], "stop_after_min": False}, "can_stage": False,
           "running": live, "busy_with": (run.project if run and not run.finished and not live else None),
           "progress": run.progress() if live else None, "run": rec or None,
           "max_runs_default": MAX_RUNS_DEFAULT, "max_runs_cap": MAX_RUNS_CAP,
           "study": study_meta(base / "work", project), "rows": [], "outputs": outputs(base, project)}
    problem = runner_problem(p)
    if problem:
        out["reasons"].append(problem)
        out["rows"] = study_rows(base / "work", project)
        return out
    # the job folder first: a runner with the contract infers the spec of a project that has none from its cfg.py
    src = job_source(project, jobs_dir, base, hr_url)
    staged = bool(src["kind"]) and (pathlib.Path(src["dir"]) / "cfg.py").is_file()
    try:
        pr = probe(p, project, src["jobs_root"] if staged else "")
    except ProbeError as e:
        out["reasons"].append(f"The study runner in HR Steel's checkout could not be read: {e}")
        out["rows"] = study_rows(base / "work", project)
        return out
    out["spec_jobs"] = pr.get("jobs") or []
    out["cli"] = pr.get("cli") or out["cli"]
    job = pr.get("job")
    if not job:
        out["source"] = src if src["kind"] else None
        if building_kind(base_brief or "") == "multistorey":
            out["reasons"].append("Not a PEB job: the base brief describes a multistorey building. The lever study "
                                  "re-sizes single-storey PEB sheds (tapered / welded portal frames).")
        elif pr.get("contract") and (out["cli"] or {}).get("spec_from_cfg"):
            if src["kind"] == "hr" and not staged:
                out["can_stage"] = True
                out["reasons"].append(
                    f"No hand-written job spec named {project} in the study runner, so it infers one from the job's own "
                    "cfg.py: read HR Steel's design of this project first (the button below fetches its package into "
                    "this project's folder).")
            elif not src["kind"]:
                out["reasons"].append(
                    f"No hand-written job spec named {project} in the study runner (scripts/peb_lever_jobs.py holds "
                    f"{', '.join(out['spec_jobs']) or 'none'}), and no job folder to infer one from: put the PEB job "
                    "folder (cfg.py, secondary/, design/) in the project folder, or design the project with HR Steel "
                    "first (PEB_LEVERS.md, 'Your own PEB job').")
            else:
                out["reasons"].append(
                    f"No hand-written job spec named {project} in the study runner, and none could be inferred from "
                    f"{src['label']}:")
                out["reasons"] += [r for r in pr.get("job_error") or [] if not r.startswith("no job spec in")]
                out["reasons"].append("The study re-sizes PEB sheds written the way the PEB golds are (PEB_LEVERS.md, "
                                      "'Your own PEB job').")
        else:
            out["reasons"].append(
                f"No job spec named {project} in the study runner (scripts/peb_lever_jobs.py holds "
                f"{', '.join(out['spec_jobs']) or 'none'}). The study runs on a PEB job written the way the PEB golds "
                "are: name the project after one of those and put its job folder in the project folder, or add a "
                "spec for your own PEB job (PEB_LEVERS.md, step 2). An HR Steel with the third-wave runner infers "
                "the spec from the job's cfg.py.")
        out["rows"] = study_rows(base / "work", project)
        return out
    out["is_peb_job"] = True
    out["job"] = job
    out["rows"] = study_rows(base / "work", project, job.get("variant_order"))
    problem = cfs_problem(p)
    if problem:
        out["reasons"].append(problem)
    if not src["kind"]:
        out["reasons"].append("No job folder: the project folder holds no cfg.py and HR Steel's server is not "
                              "available to fetch this project's design.")
    out["source"] = src
    out["ready"] = not out["reasons"]
    return out


# ---------------------------------------------------------------- the run
class StudyRun(threading.Thread):
    """One run of the study runner: a child process in HR Steel's checkout, its output streamed as events
    (re-attachable from any sequence number) and kept in study.log. Stop ends the whole process tree."""

    def __init__(self, project: str, cmd: list[str], cwd: str, env: dict, base, record: dict, labels: dict,
                 expected: int, progress_prefix: str = ""):
        super().__init__(daemon=True, name=f"peb-study-{project}")
        self.project, self.cmd, self.cwd, self.env = project, cmd, cwd, env
        # third wave: with --progress-json the runner's JSON lines drive the progress (they are kept in study.log and
        # left out of the live log); without it the human lines are parsed (_STEP / _BUDGET)
        self.prefix = progress_prefix or ""
        self.plan: Optional[list] = None
        self.base = pathlib.Path(base)
        self.record, self.labels, self.expected = record, labels, max(1, int(expected))
        self.events: list[dict] = []
        self.seq = 0
        self.cv = threading.Condition()
        self.proc: Optional[subprocess.Popen] = None
        self.stop_flag = False
        self.finished = False
        self.rc: Optional[int] = None
        self.seen: list[str] = []
        self.current_variant: Optional[str] = None
        self.budget_line = ""               # the runner ended a --stop-after-min chunk (resume to continue)
        self.started_at = time.time()

    def emit(self, ev: dict):
        with self.cv:
            self.seq += 1
            self.events.append({"seq": self.seq, "t": time.time(), **ev})
            if len(self.events) > 6000:
                del self.events[:1500]
            self.cv.notify_all()

    def _step(self, name: str, resumed: bool):
        if name in self.seen:
            return
        self.seen.append(name)
        self.current_variant = name
        self.emit({"type": "variant", "name": name, "label": self.labels.get(name, name), "resumed": resumed,
                   **self.progress()})

    def _json_event(self, ev: dict):
        kind = ev.get("event")
        if kind == "plan":
            self.plan = list(ev.get("variants") or [])
        elif kind in ("variant", "resumed") and ev.get("variant"):
            self._step(str(ev["variant"]), kind == "resumed")
        elif kind == "row":
            self.emit({"type": "row", "name": ev.get("variant"), "label": self.labels.get(ev.get("variant") or "", ""),
                       "kg_m2": ev.get("kg_m2"), "error": ev.get("error")})
        elif kind == "budget":
            self.budget_line = ("%s: time budget of %s min reached before '%s'; re-run with --resume to continue"
                                % (ev.get("job"), ev.get("minutes"), ev.get("before")) if ev.get("before") else
                                "%s: not started (time budget reached); re-run with --resume" % ev.get("job"))

    def progress(self) -> dict:
        return {"n": len(self.seen), "of": max(self.expected, len(self.seen)), "variant": self.current_variant,
                "label": self.labels.get(self.current_variant or "", self.current_variant),
                "elapsed_s": round(time.time() - self.started_at, 1)}

    def run(self):
        status = "failed"
        try:
            self.base.mkdir(parents=True, exist_ok=True)
            with open(self.base / "study.log", "a", encoding="utf-8") as logf:
                logf.write(f"\n=== {time.ctime()} :: {' '.join(self.cmd)}\n")
                kw = dict(cwd=self.cwd, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", bufsize=1)
                if sys.platform == "win32":
                    kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | _NO_WINDOW
                else:
                    kw["start_new_session"] = True      # its own process group: Stop takes every full run with it
                self.proc = subprocess.Popen(self.cmd, **kw)
                self.emit({"type": "start", "text": "PEB lever study: %d variant(s) to size -- %s"
                           % (self.expected, " ".join(self.cmd[1:])), "expected": self.expected})
                for raw in self.proc.stdout:
                    line = raw.rstrip("\r\n")
                    logf.write(line + "\n")
                    logf.flush()
                    if self.prefix:
                        if line.startswith(self.prefix):
                            try:
                                ev = json.loads(line[len(self.prefix):])
                            except ValueError:
                                ev = None
                            if isinstance(ev, dict):
                                self._json_event(ev)
                            continue
                        self.emit({"type": "log", "text": line})
                        continue
                    # one --job per run, so every progress line is this job's (the sizer prints the spec's own
                    # 'job' field, which an engineer's spec need not keep equal to the folder name)
                    m = _STEP.match(line)
                    if m:
                        self._step(m.group("name"), bool(m.group("resumed")))
                    if _BUDGET.match(line):
                        self.budget_line = line[3:]
                    self.emit({"type": "log", "text": line})
                self.rc = self.proc.wait()
            status = "stopped" if self.stop_flag else ("failed" if self.rc != 0 else
                                                       ("paused" if self.budget_line else "finished"))
        except Exception as e:
            self.emit({"type": "log", "text": f"the study could not run: {type(e).__name__}: {e}"})
            status = "stopped" if self.stop_flag else "failed"
        finally:
            self.record.update(status=status, rc=self.rc, finished_at=time.time(), variants_started=self.seen,
                               note=self.budget_line or None)
            if self.plan is not None:
                self.record["plan"] = self.plan
            try:
                write_record(self.base, self.record)
            except OSError:
                pass
            self.finished = True
            self.emit({"type": "finished", "status": status, "rc": self.rc, "stopped": self.stop_flag,
                       "note": self.budget_line or None})
            with self.cv:
                self.cv.notify_all()

    def stop(self):
        self.stop_flag = True
        kill_tree(self.proc)


def kill_tree(pr: Optional[subprocess.Popen], grace: float = 5.0):
    """End the runner and every full run it started (they share its process group / tree)."""
    if pr is None or pr.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pr.pid), "/T", "/F"], capture_output=True,
                           creationflags=_NO_WINDOW, timeout=30)
        else:
            os.killpg(pr.pid, signal.SIGTERM)
            try:
                pr.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                os.killpg(pr.pid, signal.SIGKILL)
    except Exception:
        try:
            pr.kill()
        except Exception:
            pass


# One study at a time on this PC, whatever the project: every full run is a whole analysis of the shed, and the
# runner itself runs them one after another for the same reason.
_CURRENT: Optional[StudyRun] = None
_GUARD = threading.Lock()


def current() -> Optional[StudyRun]:
    return _CURRENT


def start(run: StudyRun) -> StudyRun:
    global _CURRENT
    with _GUARD:
        if _CURRENT is not None and not _CURRENT.finished:
            raise RuntimeError(f"a PEB lever study is already running ({_CURRENT.project})")
        _CURRENT = run
    write_record(run.base, run.record)
    run.start()
    return run


@atexit.register
def _stop_at_exit():
    """The hub stops a module server with SIGTERM; take the study (a process group of its own) with it."""
    run = _CURRENT
    if run is not None and not run.finished:
        run.stop_flag = True
        kill_tree(run.proc, grace=2.0)
