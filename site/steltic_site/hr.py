"""solutions_india/hr/: index.json and the per-example files, from the hot-rolled gold job folders.

Titles and order come from the live index (and the titles files: capability-demonstration titles and their one-line
blurbs); the meta line, the flag and the file links come from each re-run package; the member ratio is
design/headline.json (the strength headline, with the slenderness beside it). Ported from the second-wave
build_hr_wave2.py: with the same inputs it writes the same index.
"""
from __future__ import annotations

import os
import re

from .util import BuildError, copy, dump_index, load_json

PEB_FILES = (("model.std", "std"), ("design/reactions.csv", "reactions"), ("design/bom.csv", "bom"),
             ("design/anchor_bolt_plan.svg", "anchors"))


def fmt_int(x):
    return "{:,.0f}".format(x)


def worst_member(p):
    best = None
    for m in p.get("members") or []:
        dc = m.get("DC")
        if isinstance(dc, (int, float)) and (best is None or dc > best[0]):
            best = (dc, m.get("id"))
    return best


def headline(jd):
    """design/headline.json (written with every report): the worst STRENGTH ratio is the headline; the slenderness
    KL/r with its limit is shown beside it."""
    hp = os.path.join(jd, "design", "headline.json")
    if not os.path.exists(hp):
        return None
    h = load_json(hp)
    st, sl = h.get("strength") or {}, h.get("slenderness") or {}
    if not st:
        return None
    txt = "worst strength ratio %.3f — %s" % (st["dc"], st["member"])
    if sl.get("KL_r") is not None:
        txt += " (slenderness KL/r %.0f / %s = %.3f — %s)" % (sl["KL_r"], sl.get("limit"), sl["ratio"], sl["member"])
    return txt


def system_name(name, names):
    """The lateral system as the site writes it: names maps a word of the package's system string to the site's
    word ({'SMRF': 'SMF'} makes IN_Ex9's 'SMRF+SCBF' read 'SMF+SCBF' like every other example)."""
    out = name or ""
    for a, b in (names or {}).items():
        out = re.sub(r"(?<![A-Za-z])%s(?![A-Za-z])" % re.escape(a), b, out)
    return out


def pv_flag(cap, so):
    """The IN_Ex5 flag: a capacity check, not a design (the report is partial on purpose)."""
    c, c150, cst = cap.get("capacity_kNm2"), cap.get("capacity_H150_kNm2"), cap.get("capacity_strength_kNm2") or 0
    if c is not None and c <= 0.0:
        # the roof as built has no spare capacity for panels: say so (third wave, the new IN_Ex1)
        lim = cap.get("limiting_item") or cap.get("limiting_check") or ""
        txt = ("A capacity check, not a design: the report is partial on purpose. As built the roof has no spare "
               "capacity for rooftop panels%s." % ((": %s governs already without them" % lim) if lim else ""))
        if c150 is not None:
            txt += (" With the dado detailed for H/150 it carries %.2f kN/m², and %.2f on strength alone."
                    % (c150, cst))
    else:
        txt = ("A capacity check, not a design: the report is partial on purpose. Under 0.15 kN/m² of "
               "panels the knee columns, rafters and purlins go over 1.0. As built the roof carries "
               "%.2f kN/m²: the PV mass takes the frame below 1 Hz and the gust-factor sway then exceeds "
               "the H/240 limit for the brick dado. With the dado detailed for H/150 it carries %.2f kN/m², "
               "and %.2f on strength alone." % (c, c150, cst))
    if so:
        txt += (" Strengthened to carry %.2f kN/m² of panels: +%.2f kg/m² (%.2f t), largest check "
                "ratio %.3f; the report lists the members and joints that change."
                % (so["pv_kNm2"], so["added_kg_m2"], so["added_steel_t"], so["worst_strength_dc"]))
    return txt


def build(live_index: dict, jobs: str, out_dir: str, *, titles=None, meta_notes=None, system_names=None,
          extra=None, log=print):
    """Write <out_dir>/index.json and the example folders. Returns (entries, problems, sources) -- sources maps each
    published example folder (relative to out_dir) to its gold job folder (for the self-contained pass)."""
    titles, meta_notes = titles or {}, meta_notes or {}
    examples = [dict(e) for e in live_index.get("examples") or []]
    known = {e["id"] for e in examples}
    for x in extra or []:
        # a new example (not on the live site yet): placed after `after` (else at the end), links in the usual form
        if x["id"] in known:
            continue
        e = {"id": x["id"], "title": x.get("title") or (titles.get(x["id"]) or {}).get("title") or x["id"],
             "report": "%s/report.html" % x["id"], "viewer": "%s/viewer_3d.html" % x["id"]}
        pos = next((k + 1 for k, y in enumerate(examples) if y["id"] == x.get("after")), len(examples))
        examples.insert(pos, e)
        known.add(x["id"])
    os.makedirs(out_dir, exist_ok=True)
    ex_out, problems, sources = [], [], {}
    for e in examples:
        jid = e["id"]
        jd = os.path.join(jobs, jid)
        pk = os.path.join(jd, "design", "calc_package.json")
        if not os.path.exists(pk):
            problems.append("%s: no calc_package" % jid)
            continue
        p = load_json(pk)
        st = (p.get("design_status") or {}).get("status")
        sc = p.get("seismic_calc") or {}
        ss = (p.get("load_plan") or {}).get("seismic_summary") or {}
        W = sc.get("W_design_kN") or ss.get("W_kN")
        vbx, vby = ss.get("VB_x_kN", ss.get("VB_kN")), ss.get("VB_y_kN", ss.get("VB_kN"))
        peb = p.get("peb") or {}
        kgm2 = None
        if peb.get("tonnage"):
            kgm2 = (peb["tonnage"].get("kg_per_m2") or {}).get("total")
            kg_txt = "%.1f kg/m²" % kgm2 if kgm2 else ""
        else:
            tk = os.path.join(jd, "design", "takeoff.json")
            if os.path.exists(tk):
                kgm2 = load_json(tk).get("kg_per_m2")
            kg_txt = ("%.1f kg/m² (modelled members)" % kgm2) if kgm2 else ""
        wm = worst_member(p)
        hl = headline(jd)
        sysn = system_name(sc.get("system") or "", system_names)
        R = sc.get("R")
        head = ("PEB, " if peb else "") + "%s, R %.1f" % (sysn, R) if R is not None else sysn
        _s12 = ((p.get("capacity_design") or {}).get("section12") or {}).get("checks") or []
        if any(c.get("id") in ("is18168_table2_beam", "is18168_table2_column") and c.get("ok") is not None for c in _s12):
            head += ", IS 18168"
        entry = dict(e)
        if jid.startswith("IN_Ex5_"):
            cap = {}
            cp = os.path.join(jd, "design", "pv_capacity.json")
            if os.path.exists(cp):
                cap = load_json(cp)
            parts = ["Ex 1 as built with 0.15 kN/m² of rooftop PV", "W %s kN" % fmt_int(W)]
            if hl:
                parts.append(hl)
            elif wm:
                parts.append("worst D/C %.3f — %s" % wm)
            if cap.get("capacity_kNm2") is not None:
                t = "the roof as built carries %.2f kN/m²" % cap["capacity_kNm2"]
                if cap.get("capacity_H150_kNm2") is not None:
                    t += " (%.2f with an H/150 sway reading, %.2f on strength alone)" % (
                        cap["capacity_H150_kNm2"], cap.get("capacity_strength_kNm2") or 0)
                parts.append(t)
            entry["meta"] = " · ".join(parts)
            if cap.get("capacity_H150_kNm2") is not None:
                so_p = os.path.join(jd, "strengthening_option.json")
                entry["flag"] = pv_flag(cap, load_json(so_p) if os.path.exists(so_p) else None)
        else:
            parts = [head, "W %s kN" % fmt_int(W), "VB %s / %s kN (x / y)" % (fmt_int(vbx), fmt_int(vby))]
            if kg_txt:
                parts.append(kg_txt)
            if hl:
                parts.append(hl)
            elif wm:
                parts.append("governing member D/C %.3f — %s" % wm)
            entry["meta"] = " · ".join(parts)
        note = meta_notes.get(jid) or meta_notes.get(jid.split("/")[0])      # a unit carries its building's note
        if note:
            entry["meta"] += " · " + note
        if jid in titles:
            if titles[jid].get("title"):
                entry["title"] = titles[jid]["title"]
            if titles[jid].get("blurb"):
                entry["blurb"] = titles[jid]["blurb"]
        entry["status"] = st
        if st != "complete" and not jid.startswith("IN_Ex5_"):
            problems.append("%s: status %s" % (jid, st))
        # files
        dst = os.path.join(out_dir, jid)
        os.makedirs(os.path.join(dst, "design"), exist_ok=True)
        for f in ("report.html", "viewer_3d.html"):
            src = os.path.join(jd, f)
            if not os.path.exists(src):
                raise BuildError("%s: no %s in the gold job folder %s" % (jid, f, jd))
            copy(src, os.path.join(dst, f))
        if peb:
            for f, key in PEB_FILES:
                src = os.path.join(jd, f)
                if os.path.exists(src):
                    copy(src, os.path.join(dst, f))
                    entry[key] = "%s/%s" % (jid, f)
        if not os.listdir(os.path.join(dst, "design")):
            os.rmdir(os.path.join(dst, "design"))
        sources[jid] = jd
        ex_out.append(entry)
    dump_index({"examples": ex_out}, os.path.join(out_dir, "index.json"))
    log("hr examples: %d" % len(ex_out))
    for x in problems:
        log("PROBLEM %s" % x)
    return ex_out, problems, sources
