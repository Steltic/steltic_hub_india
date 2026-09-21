"""Metrics from one HR Steel (IS 800) design package (the zip its server produces).

Everything here is read from what the India framework writes: `design/calc_package.json` (the one
authoritative package: members and connections with their D/C, `capacity_design.system` / `R`,
`seismic_calc` (Z, I, zone, W), `seismic_analysis` (method, modes, VB scaling), `drift_table`
(IS 1893 7.11.1.1 per storey and direction), `irregularity` (Tables 5 and 6, Amd 2), `gates`,
`design_status`), `design/cfg_snapshot.json` (geometry, N-mm), `design/member_schedule.csv`
(IS 808 / IS 1161 designations, lengths in mm) and `load_plan.json` (IS 875 / IS 1893 summaries).
Units are SI throughout: kN, kN·m, m, mm, MPa, t (tonnes), m². Values that only exist as prose in
report.html are left to the LLM reader (main.py) and arrive tagged `llm_read` so the table can say
where a number came from.
"""
from __future__ import annotations
import csv, html, io, json, math, re, zipfile
from .library import NOT_REPRESENTABLE_WORDS


def unzip(data: bytes) -> dict[str, bytes]:
    """{relative path (the <building>/ prefix stripped): bytes}"""
    out: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [i.filename.replace("\\", "/") for i in z.infolist() if not i.is_dir()]
        tops = {n.split("/", 1)[0] for n in names}
        wrap = len(tops) == 1 and all("/" in n for n in names)
        for i in z.infolist():
            if i.is_dir():
                continue
            n = i.filename.replace("\\", "/")
            rel = n.split("/", 1)[1] if wrap else n
            if rel and ".." not in rel.split("/"):
                out[rel] = z.read(i)
    return out


def _text(files: dict, name: str) -> str:
    b = files.get(name)
    return b.decode("utf-8", "replace") if b else ""


def _json(files: dict, name: str):
    raw = _text(files, name)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _flat(html_text: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html_text, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    return html.unescape(re.sub(r"\s+", " ", t))


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


# ---------------------------------------------------------------- steel weight
STEEL_KG_PER_M_PER_MM2 = 0.00785        # 7850 kg/m3 -> kg per metre per mm2 of cross-section


def section_mass_kg_m(sec: str) -> float | None:
    """kg/m for an India section designation. IS 808 rolled sections carry the mass in the name
    (WPB300X300X88.34, NPB400X180X57.38, HB/MB/LB/SC/MC ...X<mass>); built-up boxes (BOX600X600X36),
    IS 1161 tubes (CHS165.1X5.9) and angles (ISA100X100X10 / L100X100X10) are computed from the
    plate geometry; a bare designation without its mass (MB300) and unknown shapes -> None."""
    s = (sec or "").strip().upper().replace(" ", "").replace("×", "X")
    m = re.match(r"^(?:IS)?(WPB|NPB|HB|MB|LB|SC|MC|WB|JB|JC|LC|PBP|SB)\d+(?:\.\d+)?X\d+(?:\.\d+)?X(\d+(?:\.\d+)?)$", s)
    if m:
        return float(m.group(2))
    m = re.match(r"^(?:IS)?(WPB|NPB|HB|MB|LB|SC|MC|WB)\d+(?:\.\d+)?X(\d+(?:\.\d+)?)$", s)
    if m and "." in m.group(2):                       # HB300X88.34 -- the mass is the fraction-bearing number
        return float(m.group(2))
    m = re.match(r"^BOX(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)$", s)
    if m:
        b, h, t = float(m.group(1)), float(m.group(2)), float(m.group(3))
        area = b * h - max(b - 2 * t, 0) * max(h - 2 * t, 0)
        return STEEL_KG_PER_M_PER_MM2 * area
    m = re.match(r"^(?:CHS|PIPE|TUBE)(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)$", s)
    if m:
        d, t = float(m.group(1)), float(m.group(2))
        return STEEL_KG_PER_M_PER_MM2 * math.pi * (d - t) * t
    m = re.match(r"^(?:RHS|SHS)(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)$", s)
    if m:
        b, h, t = float(m.group(1)), float(m.group(2)), float(m.group(3))
        return STEEL_KG_PER_M_PER_MM2 * (b * h - (b - 2 * t) * (h - 2 * t))
    m = re.match(r"^(?:ISA|L)(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)$", s)
    if m:
        a, b, t = float(m.group(1)), float(m.group(2)), float(m.group(3))
        return STEEL_KG_PER_M_PER_MM2 * t * (a + b - t)
    m = re.match(r"^(?:2|TWIN)?(?:ISA|L)(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)$", s)
    if m:
        a, b, t = float(m.group(1)), float(m.group(2)), float(m.group(3))
        return 2 * STEEL_KG_PER_M_PER_MM2 * t * (a + b - t)
    return None


def schedule_metrics(files: dict) -> dict:
    txt = _text(files, "design/member_schedule.csv")
    if not txt:
        return {}
    rows = list(csv.DictReader(io.StringIO(txt)))
    tonnes = 0.0; unknown = set(); kinds: dict[str, int] = {}
    for r in rows:
        kind = (r.get("member") or "").strip().lower()
        kinds[kind] = kinds.get(kind, 0) + 1
        sec = r.get("section") or ""
        try:
            L = float(r.get("length_mm") or 0) / 1000.0      # mm -> m
        except ValueError:
            L = 0.0
        w = section_mass_kg_m(sec)
        if w is None:
            unknown.add(sec)
        else:
            tonnes += w * L / 1000.0
    out = {"steel_t": round(tonnes, 1) if rows else None,
           "n_columns": kinds.get("col", 0) + kinds.get("column", 0),
           "n_beams": kinds.get("beam", 0) + kinds.get("girder", 0),
           "n_braces": sum(v for k, v in kinds.items() if "brace" in k or k in ("diag", "diagonal", "link")),
           "n_members": len(rows)}
    if unknown:
        out["unweighed_sections"] = sorted(unknown)[:10]
    return out


# ---------------------------------------------------------------- geometry (cfg_snapshot.json, else cfg.py)
def cfg_metrics(files: dict) -> dict:
    out: dict = {}
    cs = _json(files, "design/cfg_snapshot.json")
    if isinstance(cs, dict):
        units = str(cs.get("units") or "N-mm")
        to_m = 0.001 if units.upper().startswith("N") or cs.get("_units_converted") else 1.0   # N-mm -> mm; 'm' -> m
        nx, ny = _num(cs.get("NX")), _num(cs.get("NY"))
        sx, sy = _num(cs.get("SX")) or _num(cs.get("bay_x")), _num(cs.get("SY")) or _num(cs.get("bay_y"))
        heights = cs.get("heights") if isinstance(cs.get("heights"), list) else None
        if nx and ny:
            out["nx"] = int(nx); out["ny"] = int(ny)
        if nx and ny and sx and sy:
            out["plan_x_m"] = round(nx * sx * to_m, 2); out["plan_y_m"] = round(ny * sy * to_m, 2)
            if heights:
                out["floor_area_m2"] = round(nx * sx * to_m * ny * sy * to_m * len(heights), 0)
        if heights:
            try:
                hs = [float(h) for h in heights]
                out["n_storeys"] = len(hs)
                out["height_m"] = round(sum(hs) * to_m, 2)
            except (TypeError, ValueError):
                pass
        if cs.get("steel_grade"):
            out["steel_grade"] = str(cs["steel_grade"])[:40]
        occ = cs.get("occupancy")
        if isinstance(occ, dict) and occ.get("use"):
            out["occupancy"] = str(occ["use"])[:60]
        if cs.get("system"):
            out["system_cfg"] = str(cs["system"])[:60]
        return out
    # fallback: a cfg.py that states the grid at module level (the agent's own script)
    src = _text(files, "cfg.py")
    if not src:
        return out
    m = re.search(r"^\s*NX\s*,\s*NY\s*,\s*BAY_M\s*=\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)", src, re.M)
    if m:
        nx, ny, bay = int(m.group(1)), int(m.group(2)), float(m.group(3))
        out.update({"nx": nx, "ny": ny, "plan_x_m": round(nx * bay, 2), "plan_y_m": round(ny * bay, 2)})
    return out


# ---------------------------------------------------------------- report.html (prose only)
def report_metrics(files: dict) -> dict:
    t = _flat(_text(files, "report.html"))
    out: dict = {}
    if not t:
        return out
    m = re.search(r"NOT PERMITTED[^.]{0,200}", t)
    if m:
        out["np_text"] = m.group(0)[:220]
    m = re.search(r"(Table 9 Note 1[^.]{0,160})", t)
    if m and "banned" in m.group(1).lower() or (m and "not permitted" in m.group(1).lower()):
        out.setdefault("np_text", m.group(1)[:220])
    m = re.search(r"Lateral system[^:]{0,40}:\s*(.{0,200}?)\s(?:IS 1893|R\s*=|Table 9|$)", t)
    if m:
        out["system_declared"] = m.group(1).strip()[:200]
    return out


# ---------------------------------------------------------------- calc_package.json + load_plan.json
def package_metrics(files: dict) -> dict:
    cp = _json(files, "design/calc_package.json")
    if cp is None:
        return {"package_error": "calc_package.json missing or not valid JSON"} if "design/calc_package.json" in files else {}
    out: dict = {}
    dcs = [x.get("DC") for x in cp.get("members", []) if isinstance(x, dict) and _num(x.get("DC")) is not None]
    cdcs = [x.get("DC") for x in cp.get("connections", []) if isinstance(x, dict) and _num(x.get("DC")) is not None]
    if dcs or cdcs:
        out["dc_max"] = round(max(dcs + cdcs), 3)
        out["n_over"] = sum(1 for v in dcs + cdcs if v > 1.0)
        out["n_checked_members"] = len(dcs)
        out["n_checked_connections"] = len(cdcs)
    ds = cp.get("design_status") or {}
    if isinstance(ds, dict) and ds.get("status"):
        out["design_status"] = str(ds["status"]).lower()
        out["n_open_reasons"] = int(ds.get("n_reasons") or len(ds.get("reasons") or []))
        if ds.get("reasons"):
            out["open_reasons"] = [str(r)[:160] for r in ds["reasons"][:6]]
    cd = cp.get("capacity_design") or {}
    if isinstance(cd, dict):
        if cd.get("system"):
            out["system"] = str(cd["system"])[:200]
        if _num(cd.get("R")) is not None:
            out["R"] = float(cd["R"])
    sc = cp.get("seismic_calc") or {}
    if isinstance(sc, dict):
        for k_out, k_in in (("Z", "Z"), ("I", "I"), ("W_kN", "W_design_kN")):
            if _num(sc.get(k_in)) is not None:
                out[k_out] = float(sc[k_in])
        if sc.get("zone"):
            out["zone"] = str(sc["zone"])
        if "R" not in out and _num(sc.get("R")) is not None:
            out["R"] = float(sc["R"])
        if "system" not in out and sc.get("system"):
            out["system"] = str(sc["system"])[:200]
    sa = cp.get("seismic_analysis") or {}
    if isinstance(sa, dict):
        if sa.get("method"):
            out["analysis_method"] = str(sa["method"])
        modes = sa.get("modes") or []
        if modes and isinstance(modes[0], dict) and _num(modes[0].get("T")) is not None:
            out["T1_s"] = round(float(modes[0]["T"]), 3)
        scale = sa.get("scale") or {}
        vbs = [_num((scale.get(d) or {}).get("VB_scaled_kN")) for d in ("X", "Y") if isinstance(scale.get(d), dict)]
        vbs = [v for v in vbs if v is not None]
        if vbs:
            out["VB_kN"] = round(max(vbs), 1)
    lp = cp.get("load_plan") or {}
    ss = (lp.get("seismic_summary") if isinstance(lp, dict) else None) or {}
    lpj = _json(files, "load_plan.json")
    if isinstance(lpj, dict):
        ss = {**(lpj.get("seismic_summary") or {}), **ss} if isinstance(lpj.get("seismic_summary"), dict) else ss
        ws = lpj.get("wind_summary") or {}
        if isinstance(ws, dict):
            if _num(ws.get("Vb_mps")) is not None:
                out["Vb_mps"] = float(ws["Vb_mps"])
            if _num(ws.get("pd_kNm2")) is not None:
                out["pd_kNm2"] = float(ws["pd_kNm2"])
            if ws.get("cyclone_belt") is not None:
                out["cyclone_belt"] = bool(ws["cyclone_belt"]) if not isinstance(ws["cyclone_belt"], str) else ws["cyclone_belt"].lower().startswith(("y", "t"))
            wv = [_num(ws.get(k)) for k in ("VB_x_kN", "VB_y_kN")]
            wv = [v for v in wv if v is not None]
            if wv:
                out["wind_VB_kN"] = round(max(wv), 1)
    if isinstance(ss, dict):
        vb = [_num(ss.get(k)) for k in ("VB_x_kN", "VB_y_kN")]
        vb = [v for v in vb if v is not None]
        if vb and "VB_kN" not in out:
            out["VB_kN"] = round(max(vb), 1)
        for k_out, k_in in (("Ah", "Ah_x"), ("Ta_s", "Ta_s"), ("Sa_g", "Sa_g_x")):
            if _num(ss.get(k_in)) is not None:
                out[k_out] = float(ss[k_in])
        if ss.get("soil"):
            out["soil_type"] = str(ss["soil"])
        if ss.get("site"):
            out["site"] = str(ss["site"])[:60]
        for k in ("Z", "I", "zone", "R"):
            if k not in out and ss.get(k) is not None:
                out[k] = ss[k] if k == "zone" else _num(ss.get(k))
        if "W_kN" not in out and _num(ss.get("W_kN")) is not None:
            out["W_kN"] = float(ss["W_kN"])
    if out.get("VB_kN") and out.get("W_kN"):
        out["VB_over_W"] = round(out["VB_kN"] / out["W_kN"], 4)
    # storey drift, IS 1893 7.11.1.1: the package records value / limit / dc per storey and direction
    dt = cp.get("drift_table") or []
    rows = [r for r in dt if isinstance(r, dict) and _num(r.get("dc")) is not None]
    if rows:
        dcs_d = [float(r["dc"]) for r in rows]
        out["drift_utilisation"] = round(max(dcs_d), 3)
        out["drift_margin"] = round(1 - max(dcs_d), 3)
        lim = next((_num(r.get("limit")) for r in rows if _num(r.get("limit")) is not None), None)
        if lim is not None:
            out["drift_limit"] = lim
        vals = [_num(r.get("value")) if _num(r.get("value")) is not None else _num(r.get("drift")) for r in rows]
        if all(v is not None for v in vals):
            out["drift_max"] = round(max(vals), 5)
        by_dir: dict[str, list[float]] = {}
        for r in rows:
            by_dir.setdefault(str(r.get("dir") or "X"), []).append(float(r["dc"]))
        worst_dir = max(by_dir, key=lambda d: max(by_dir[d]))
        w = by_dir[worst_dir]
        mean = sum(w) / len(w)
        out["drift_concentration_ratio"] = round(max(w) / mean, 3) if mean else None
        for d, v in by_dir.items():
            out[f"drift_profile_{d}"] = v
        out["drift_ok"] = all(bool(r.get("ok")) for r in rows)
    irr = cp.get("irregularity") or {}
    if isinstance(irr, dict):
        tor = irr.get("torsion") or {}
        if isinstance(tor, dict):
            if _num(tor.get("ratio")) is not None:
                out["torsion_ratio"] = round(float(tor["ratio"]), 3)
            if tor.get("irregular") is not None:
                out["torsion_irregular"] = bool(tor["irregular"])
            if tor.get("verdict"):
                out["torsion_class"] = str(tor["verdict"])[:80]
        ssy = irr.get("soft_storey") or {}
        if isinstance(ssy, dict) and ssy.get("irregular") is not None:
            out["soft_storey"] = bool(ssy["irregular"])
        flagged = [k for k, v in irr.items() if isinstance(v, dict) and v.get("irregular") is True]
        out["irregularities"] = flagged
    gates = cp.get("gates") or {}
    if isinstance(gates, dict) and gates:
        oks = {k: (v.get("ok") if isinstance(v, dict) else v) for k, v in gates.items()}
        out["gates_ok"] = all(x is True for x in oks.values())
        out["gates_failed"] = sorted(k for k, v in oks.items() if v is not True)
    wserv = cp.get("wind_serviceability") or {}
    if isinstance(wserv, dict) and _num(wserv.get("dc")) is not None:
        out["wind_drift_utilisation"] = round(float(wserv["dc"]), 3)
    else:
        gw = [gates.get(k) for k in ("wind_defl_X", "wind_defl_Y") if isinstance(gates, dict) and isinstance(gates.get(k), dict)]
        dcw = [_num(g.get("dc")) for g in gw if _num(g.get("dc")) is not None]
        if dcw:
            out["wind_drift_utilisation"] = round(max(dcw), 3)
    return out


def representability(m: dict) -> dict:
    """Every IS 800 Section 12 frame type (SMF / SMRF, SCBF, OMRF, OCBF, EBF, and their per-direction
    mixes) can go through the Nonlinear (SNL-IN) tools; devices, buckling-restrained braces, plate
    shear walls and composite walls cannot (and have no IS 1893 Table 9 row)."""
    text = " ".join(str(m.get(k) or "") for k in ("system", "system_cfg", "system_declared", "system_llm")).lower()
    if not text.strip():
        return {"representable": None}
    bad = [w for w in NOT_REPRESENTABLE_WORDS if w.lower() in text]
    # a moment frame AND a braced frame named together ("SMF+SCBF", "OMF + OCBF", "dual ..."): one R per
    # direction or the lower R for both -- the moment-frame share rule applies
    mf, bf = r"(?:smf|smrf|omf|omrf|moment)", r"(?:scbf|ocbf|ebf|brb|braced)"
    is_mixed = "dual" in text or bool(re.search(mf + r"[^.;]{0,20}\+[^.;]{0,20}" + bf, text)
                                       or re.search(bf + r"[^.;]{0,20}\+[^.;]{0,20}" + mf, text))
    return {"representable": not bad, "is_mixed": is_mixed,
            "not_representable_because": ", ".join(sorted(set(bad))) if bad else ""}


def extract(files: dict) -> dict:
    """All deterministic metrics for one package."""
    m: dict = {}
    m.update(cfg_metrics(files))
    m.update(schedule_metrics(files))
    m.update(report_metrics(files))
    m.update(package_metrics(files))
    if m.get("steel_t") is not None and m.get("floor_area_m2"):
        m["steel_kg_m2"] = round(m["steel_t"] * 1000.0 / m["floor_area_m2"], 1)
    m.update(representability(m))
    m["has_report"] = "report.html" in files
    m["has_viewer"] = "viewer_3d.html" in files
    m["has_package"] = "design/calc_package.json" in files
    m["has_status"] = "STATUS.md" in files
    return m


def report_excerpt(files: dict, limit: int = 14000) -> str:
    """The parts of the report an LLM needs to answer the prose questions, trimmed."""
    t = _flat(_text(files, "report.html"))
    if not t:
        return ""
    keep = []
    for head in ("Design basis", "Structural system", "Lateral system", "Governing lateral load",
                 "Plan and vertical irregularit", "Storey drift", "Wind", "NOT PERMITTED", "Table 9 Note 1",
                 "Earthquake detailing", "Connections", "design_status"):
        i = t.find(head)
        if i >= 0:
            keep.append(t[i:i + 2200])
    status = _text(files, "STATUS.md")
    if status:
        keep.insert(0, "STATUS.md:\n" + status[:3000])
    out = "\n---\n".join(keep) if keep else t[:limit]
    return out[:limit]
