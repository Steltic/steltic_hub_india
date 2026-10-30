"""The gold before / after comparison (handoff section 5, step 3; ported from compare2.py).

For every hot-rolled job under BASE (a folder holding run_summary.json) it compares the re-run package under NEW:
member sizes, member ratios and checks, the strength headline, drift, kg/m2, status and reasons, connections,
Section 12 and (PEB) the IS 801 secondary results; it prints a markdown table and a list of every difference. A
report-only change must show no design difference (only tied-combination labels, until repeatable runs land); a
redesign is the before / after table for the repo's CHANGES file. The strength headline is read with the HR engine's
own report_second_wave.headline (--hr-engine <steltic_india>/steel_engine).
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys


def jobs(base):
    return [os.path.relpath(os.path.dirname(f), base)
            for f in sorted(glob.glob(os.path.join(base, "**", "run_summary.json"), recursive=True))]


def load(root, j, rel):
    p = os.path.join(root, j, rel)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def kgm2(root, j, pkg):
    peb = (pkg or {}).get("peb") or {}
    if peb.get("tonnage"):
        return (peb["tonnage"].get("kg_per_m2") or {}).get("total")
    tk = load(root, j, "design/takeoff.json")
    return (tk or {}).get("kg_per_m2")


def _r(x):
    if isinstance(x, float):
        return round(x, 6)
    if isinstance(x, (list, tuple)):
        return type(x)(_r(v) for v in x)
    if isinstance(x, dict):
        return {k: _r(v) for k, v in x.items()}
    return x


def _strip(t):
    return re.sub(r"\s*\((?:governing )?combination [^)]*(?:\)[^)]*)?\)", "", str(t or ""))


def sig(pkg):
    mem = {m["id"]: ((m.get("inputs") or {}).get("section"), m.get("DC"),
                     [(_strip(c.get("name")), c.get("dc"), c.get("value"), c.get("limit")) for c in m.get("checks") or []])
           for m in pkg.get("members") or []}
    con = {c.get("id"): (c.get("section"), c.get("DC")) for c in pkg.get("connections") or []}
    s12 = [(c.get("id"), c.get("member"), c.get("dc"), c.get("ok")) for c in
           (((pkg.get("capacity_design") or {}).get("section12") or {}).get("checks") or [])]
    drift = [(r.get("storey"), r.get("dir"), r.get("drift"), r.get("dc")) for r in pkg.get("drift_table") or []]
    peb = pkg.get("peb") or {}
    pebm = {k: (v.get("section"), v.get("L_mm")) for k, v in (peb.get("members") or {}).items()}
    sec = [(s.get("id"), s.get("section"), s.get("DC")) for s in pkg.get("secondary_members") or []]
    sc = pkg.get("seismic_calc") or {}
    return _r({"members": mem, "connections": con, "s12": s12, "drift": drift, "peb_members": pebm, "secondary": sec,
               "W": sc.get("W_engine_kN"), "gates": pkg.get("gates"),
               "status": (pkg.get("design_status") or {}).get("status"),
               "reasons": (pkg.get("design_status") or {}).get("reasons"),
               "scale": (pkg.get("seismic_analysis") or {}).get("scale")})


def compare(base, new, headline):
    rows, diffs = [], []
    for j in jobs(base):
        b, n = load(base, j, "design/calc_package.json"), load(new, j, "design/calc_package.json")
        if not n:
            diffs.append((j, "no new calc_package"))
            continue
        sb, sn = sig(b), sig(n)
        for k in sb:
            if sb[k] != sn[k]:
                if isinstance(sb[k], dict):
                    ks = sorted(set(sb[k]) | set(sn[k]))
                    bad = [x for x in ks if sb[k].get(x) != sn[k].get(x)]
                    diffs.append((j, "%s differ: %s" % (k, bad[:5])))
                else:
                    diffs.append((j, "%s differ" % k))
        bm = {x["id"]: x for x in b.get("members") or []}
        nm = {x["id"]: x for x in n.get("members") or []}
        lab = [m for m in sb["members"] if sb["members"][m] == sn["members"].get(m) and m in nm and
               [c.get("name") for c in bm[m].get("checks") or []] != [c.get("name") for c in nm[m].get("checks") or []]]
        if lab:
            diffs.append((j, "label only (tied combination, same values): %s" % lab))
        hb, hn = headline(b), headline(n)
        wb = (b.get("design_status") or {}).get("warnings") or []
        wn = (n.get("design_status") or {}).get("warnings") or []
        new_w = [w for w in wn if w not in wb]
        gone_w = [w for w in wb if w not in wn]
        if gone_w:
            diffs.append((j, "warnings removed: %s" % gone_w))
        kb, kn = kgm2(base, j, b), kgm2(new, j, n)
        if kb != kn:
            diffs.append((j, "kg/m2 %s -> %s" % (kb, kn)))
        sec_b, sec_n = load(base, j, "secondary/secondary_result.json"), load(new, j, "secondary/secondary_result.json")
        if sec_b is not None:
            def ss(x):
                x = x or {}
                return json.dumps({k: x.get(k) for k in ("records", "purlin_kN_per_m2_plan", "girt_kN_per_m2_wall",
                                                         "mass_kg")}, sort_keys=True, default=str)
            if ss(sec_b) != ss(sec_n):
                diffs.append((j, "secondary_result differs"))

        def dmax(p):
            return max([r.get("dc") or 0 for r in p.get("drift_table") or []] or [0])
        sl = hn.get("slenderness") or {}
        st_b = hb.get("strength") or {}
        rows.append([j, (b.get("design_status") or {}).get("status"), (n.get("design_status") or {}).get("status"),
                     st_b.get("dc") or (hb.get("member_dc_max") or {}).get("dc") or 0.0,
                     (hn.get("strength") or {}).get("dc") or 0.0, (hn.get("strength") or {}).get("member"),
                     sl.get("KL_r"), sl.get("limit"), sl.get("ratio"), dmax(b), dmax(n), kb, kn,
                     "; ".join(w.split(":")[0] for w in new_w)])
    return rows, diffs


def markdown(rows):
    md = ["| Job | Status before → after | Worst strength ratio before | Worst strength ratio after (member) | "
          "Slenderness KL/r / limit (ratio) | Drift D/C before → after | kg/m² before → after | New non-blocking point |",
          "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append("| %s | %s → %s | %.3f | %.3f (%s) | %s | %.3f → %.3f | %s → %s | %s |" % (
            r[0], r[1], r[2], r[3], r[4], r[5], ("%.1f / %s (%.3f)" % (r[6], r[7], r[8])) if r[6] else "—",
            r[9], r[10], ("%.1f" % r[11]) if r[11] else "—", ("%.1f" % r[12]) if r[12] else "—", r[13] or "—"))
    return "\n".join(md)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="build_site.py compare", description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", required=True, help="the gold job folders before (a snapshot)")
    ap.add_argument("--new", required=True, help="the re-run gold job folders")
    ap.add_argument("--hr-engine", required=True, help="steltic_india/steel_engine (report_second_wave.headline)")
    ap.add_argument("--md", help="also write the table to this markdown file")
    a = ap.parse_args(argv)
    sys.path.insert(0, a.hr_engine)
    import report_second_wave as RS  # noqa: E402
    rows, diffs = compare(a.base, a.new, RS.headline)
    md = markdown(rows)
    print(md)
    print("\nDIFFERENCES (%d):" % len(diffs))
    for d in diffs:
        print(" -", d)
    if a.md:
        open(a.md, "w", encoding="utf-8").write(md + "\n")
    return 0
