#!/usr/bin/env python3
"""FAKE peb_lever_study.py for the hub's tests -- no analysis, no engine.

It has the module API the Variations tab reads (JOB_SPECS, LABELS, VARIANT_ORDER, label, variants_for, with the
variant names and status strings of steltic_india's runner) and the same command line. Run, it prints the runner's
progress lines ("== <job> / <variant>"), writes <work>/<job>/study.json / study.csv / study.html and <work>/index.html,
study_all.json, study_tables.md, and records how it was called in <work>/fake_calls.jsonl.

It mirrors the runner of the PEB variations release with the second-wave switches (--ladder full|short,
--stop-after-min); FAKE_OLD_RUNNER=1 makes it the first release's runner (neither switch, variants_for(spec, solar)).

Test knobs (environment): FAKE_STUDY_SLEEP seconds per variant; FAKE_STUDY_FAIL=<variant> exits 3 at that variant.
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from peb_lever_jobs import JOB_SPECS   # noqa: E402

LEVER_ORDER = ("ltb_gradient", "joint_deliverable", "sway_H150_dado", "thin_purlins", "base_key_analysis",
               "collateral_in_uplift", "omrf_r3", "rods")
CUM_ORDER = LEVER_ORDER + ("market",)
VARIANT_ORDER = (("gold", "rerun", "strict") + LEVER_ORDER + tuple("cum:" + n for n in LEVER_ORDER) +
                 ("compliant", "market", "solar_ready"))
LABELS = {
    "gold": "Gold as published", "rerun": "Gold sizes, this engine (strict rules)", "strict": "Auto-sized strict baseline",
    "joint_deliverable": "Joints for the deliverable moment (12.10.2.1)", "ltb_gradient": "LTB with the moment-gradient C1",
    "sway_H150_dado": "Sway H/150 + dado-top H/240", "base_key_analysis": "Shear keys for the analysis base shear",
    "rods": "Tension-rod X-bracing", "thin_purlins": "Thin G350 purlins / girts",
    "collateral_in_uplift": "Collateral / PV counted in 0.9DL rows", "omrf_r3": "Seismic route: OMRF R 3, no IS 18168",
    "compliant": "All compliant levers together", "market": "Market practice bundle (every lever + market loads)",
    "solar_ready": "Solar-ready: gold strengthened for 0.15 kN/m2 PV",
}


def label(name):
    return "+ " + LABELS[name[4:]] if name.startswith("cum:") else LABELS[name]


def _worst(sts):
    for key in ("NOT", "not compliant", "conflict", "unconservative", "reading"):
        for st in sts:
            if key in str(st):
                return st
    return "compliant"


OLD = bool(os.environ.get("FAKE_OLD_RUNNER"))


def _variants(spec, solar=False, ladder="full"):
    sz = spec["seismic"]
    omrf, zone, dado = sz.get("route") == "OMRF", sz["zone"], spec.get("dado_height_mm")
    out = [("strict", {"rules": {}}, "compliant", "owner rulings D-T1, D-T4, D-T7, O1")]
    lev = {}
    if omrf:
        lev["joint_deliverable"] = "compliant"
    else:
        out.append(("joint_deliverable", None, "n/a", "SMRF joints: no deliverable-moment option; unchanged"))
    lev["ltb_gradient"] = "compliant"
    if dado:
        lev["sway_H150_dado"] = "compliant"
    else:
        out.append(("sway_H150_dado", None, "n/a", "no brick dado"))
    lev["base_key_analysis"] = "reading rejected by ruling O1"
    if zone == "II" and omrf:
        lev["rods"] = "not compliant"
    else:
        out.append(("rods", None, "n/a", "refused: tension rods are offered on a Zone II OCBF only (zone %s)" % zone))
    lev["thin_purlins"] = "compliant"
    lev["collateral_in_uplift"] = "old treatment (unconservative)"
    if omrf:
        out.append(("omrf_r3", None, "n/a", "the job is already an ordinary moment frame (route as briefed)"))
    else:
        lev["omrf_r3"] = "market practice, not compliant"
    for n in LEVER_ORDER:
        if n in lev:
            out.append((n, {"rules": {n: True}}, lev[n], "lever " + n))
    if ladder == "short":
        comp = [n for n in LEVER_ORDER if lev.get(n) == "compliant"]
        if len(comp) == 1:
            out.append(("compliant", {"alias": comp[0]}, "compliant", "= " + comp[0]))
        elif comp:
            out.append(("compliant", {"rules": {}, "start_from": "strict"}, "compliant", " + ".join(comp)))
        out.append(("market", {"rules": {}, "start_from": "compliant" if comp else "strict", "market": True},
                    "NOT COMPLIANT (market practice)", "market (short ladder)"))
        if solar:
            out.append(("solar_ready", {"rules": {}, "solar_pv": 0.15}, "compliant", "strengthen only"))
        return out
    prev, last, sts = "strict", None, []
    for n in LEVER_ORDER:
        if n not in lev:
            continue
        sts.append(lev[n])
        out.append(("cum:" + n, {"rules": {}, "start_from": prev, "alias": n if prev == "strict" else None}, _worst(sts),
                    "cumulative"))
        prev = "cum:" + n
        if _worst(sts) == "compliant":
            last = "cum:" + n
    out.append(("market", {"rules": {}, "start_from": prev, "market": True}, "NOT COMPLIANT (market practice)", "market"))
    if last:
        out.append(("compliant", {"alias": last}, "compliant", "every lever marked compliant (= %s)" % last))
    if solar:
        out.append(("solar_ready", {"rules": {}, "solar_pv": 0.15}, "compliant", "strengthen only"))
    return out


if OLD:
    def variants_for(spec, solar=False):
        return _variants(spec, solar)
else:
    def variants_for(spec, solar=False, ladder="full"):
        return _variants(spec, solar, ladder)

T_START = time.time()


def _render(work, study):
    rows = [study["rows"][k] for k in VARIANT_ORDER if k in study["rows"]]
    with open(os.path.join(work, "study.csv"), "w") as f:
        f.write("variant,label,status,total_kg_m2\n")
        for r in rows:
            f.write("%s,%s,%s,%s\n" % (r["name"], r["label"], r["status"], (r.get("kg_m2") or {}).get("total", "")))
    with open(os.path.join(work, "study.html"), "w") as f:
        f.write("<!doctype html><title>PEB lever study</title><h1>%s</h1>%d rows" % (study["job"], len(rows)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", action="append", default=[])
    ap.add_argument("--jobs-root")
    ap.add_argument("--work")
    ap.add_argument("--cfs-root")
    ap.add_argument("--published-root")
    ap.add_argument("--variants", default="")
    ap.add_argument("--max-runs", type=int, default=8)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--solar", action="store_true")
    if not OLD:
        ap.add_argument("--ladder", choices=("full", "short"), default="full")
    ap.add_argument("--render", action="store_true")
    if not OLD:
        ap.add_argument("--stop-after-min", type=float, default=0.0)
    a = ap.parse_args()
    ladder = getattr(a, "ladder", "full")
    budget = float(getattr(a, "stop_after_min", 0.0) or 0.0)
    os.makedirs(a.work, exist_ok=True)
    with open(os.path.join(a.work, "fake_calls.jsonl"), "a") as f:
        f.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "env": {k: os.environ.get(k) for k in (
            "PYTHONHASHSEED", "STELTIC_CFS_ROOT", "PYTHONDONTWRITEBYTECODE", "INDIA_CORPUS_ROOT")}}) + "\n")
    want = set(a.variants.split(",")) if a.variants else None
    for job in a.job:
        spec = JOB_SPECS[job]
        if not os.path.isfile(os.path.join(a.jobs_root, job, "cfg.py")):
            raise SystemExit("no cfg.py in %s" % os.path.join(a.jobs_root, job))
        work = os.path.join(a.work, job)
        os.makedirs(work, exist_ok=True)
        p = os.path.join(work, "study.json")
        study = json.load(open(p)) if (a.resume and os.path.exists(p)) else {"job": job, "rows": {}}
        if study.get("ladder") and study["ladder"] != ladder:
            raise SystemExit("%s: the study in %s was run with --ladder %s" % (job, work, study["ladder"]))
        study["ladder"] = ladder
        stopped = None
        for name, kw, status, note in _variants(spec, solar=a.solar and job.startswith("IN_Ex1_"), ladder=ladder):
            if want and name not in want:
                continue
            if a.resume and (study["rows"].get(name) or {}).get("done"):
                print("== %s / %s: done (resume)" % (job, name), flush=True)
                continue
            if budget > 0 and (time.time() - T_START) > 60.0 * budget and kw is not None and not kw.get("alias"):
                print("== %s: time budget of %s min reached before '%s'; re-run with --resume to continue"
                      % (job, budget, name), flush=True)
                stopped = name
                break
            if kw is None:
                study["rows"][name] = {"name": name, "label": label(name), "status": "n/a", "note": note, "done": True}
                continue
            if kw.get("alias"):
                src = study["rows"].get(kw["alias"])
                if src and src.get("done"):
                    study["rows"][name] = dict(src, name=name, label=label(name), status=status, alias_of=kw["alias"])
                continue
            print("== %s / %s" % (job, name), flush=True)
            print("    local sizing: fake, no analysis", flush=True)
            time.sleep(float(os.environ.get("FAKE_STUDY_SLEEP") or 0))
            if os.environ.get("FAKE_STUDY_FAIL") == name:
                print("Traceback (most recent call last): fake failure at %s" % name, flush=True)
                sys.exit(3)
            kg = round(30.0 - 0.5 * len(study["rows"]), 2)
            study["rows"][name] = {"name": name, "label": label(name), "status": status, "note": note,
                                   "kg_m2": {"primary": round(kg * 0.6, 2), "total": kg}, "n_full_runs": 2,
                                   "governing": [0.981, "C1-0-m1", "station", "IS 800 8.2.2 LTB"], "done": True}
            st = study["rows"].get("strict") or {}
            for r in study["rows"].values():
                if r.get("kg_m2") and st.get("kg_m2"):
                    r["delta_vs_strict_kg_m2"] = round(r["kg_m2"]["total"] - st["kg_m2"]["total"], 2)
            json.dump(study, open(p, "w"), indent=1)
            _render(work, study)
        if stopped:
            study["stopped_before"] = stopped
            study.pop("finished", None)
        else:
            study.pop("stopped_before", None)
            study["finished"] = time.strftime("%Y-%m-%d %H:%M")
        json.dump(study, open(p, "w"), indent=1)
        _render(work, study)
    open(os.path.join(a.work, "index.html"), "w").write("<!doctype html><title>PEB lever study</title>index")
    open(os.path.join(a.work, "study_all.json"), "w").write("{}")
    open(os.path.join(a.work, "study_tables.md"), "w").write("### tables\n")
    print("wrote %s" % os.path.join(a.work, "index.html"), flush=True)


if __name__ == "__main__":
    main()
