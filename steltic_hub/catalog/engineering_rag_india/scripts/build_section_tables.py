#!/usr/bin/env python3
"""Build checked section-property tables (CORPUS-09).

Writes documents/standards/<STEM>/structured/sections.csv for IS_808_2021,
IS_811_1987 and IS_1161_2014 from the BIS PDF text layer (``pdftotext -layout``,
which de-rotates the landscape table pages), then runs consistency checks:

  * mass = 0.785 * A          (+-1 %)   (kg/m vs cm^2)
  * Z = I / c                 (+-1 %)   c = D/2, (B - Cy), (a - Cz) ... per shape
  * r = sqrt(I / A)           (+-1 %)
  * designation dimensions == dimension columns

Rows that fail are either repaired by a documented rule (thin-space printed as
"0" in IS 1161, radius printed in cm in IS 808 Table 1 WB rows, OCR letter/digit
confusions in the IS 811 scan) or listed in structured/sections_check.json as
unresolved.  Every repaired cell is recorded in the ``repairs`` column.

Usage:  python3 scripts/build_section_tables.py [--root ROOT] [--pdf-dir DIR]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bis_text import pdf_dirs  # noqa: E402

WATERMARK_RE = re.compile(r"Free Standard provided by BIS|BSB Edge|@[\w.-]+\.\w+", re.I)
TOL = 0.01


def find_pdf(name: str, extra: Optional[Path] = None) -> Path:
    dirs = ([extra] if extra else []) + pdf_dirs()
    for d in dirs:
        p = d / name
        if p.is_file():
            return p
    raise SystemExit(f"PDF not found: {name} -- put your licensed copy in one of: "
                     + ", ".join(str(d) for d in dirs) + " (or pass --pdf-dir)")


def page_text(pdf: Path, pno: int) -> str:
    out = subprocess.run(
        ["pdftotext", "-layout", "-f", str(pno), "-l", str(pno), str(pdf), "-"],
        capture_output=True,
        text=True,
    ).stdout
    return "\n".join(ln for ln in out.splitlines() if not WATERMARK_RE.search(ln))


def n_pages(pdf: Path) -> int:
    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
    m = re.search(r"Pages:\s+(\d+)", out)
    return int(m.group(1)) if m else 0


# --------------------------------------------------------------------------
# field splitting
# --------------------------------------------------------------------------
NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def split_numeric_fields(s: str) -> list[str]:
    """Split a run of numeric columns separated by >=2 spaces.

    A single space inside a field is a thousands separator when the right part
    is exactly three digits and the left part is an integer group.
    """
    fields: list[str] = []
    for f in re.split(r"\s{2,}", s.strip()):
        f = f.strip()
        if not f:
            continue
        toks = f.split(" ")
        cur = toks[0]
        for t in toks[1:]:
            if re.fullmatch(r"\d{3}(?:\.\d+)?", t) and re.fullmatch(r"\d{1,3}(?:\d{3})*", cur.replace(" ", "")):
                cur = cur + t
            else:
                fields.append(cur)
                cur = t
        fields.append(cur)
    return fields


def fnum(x: str) -> Optional[float]:
    x = (x or "").strip()
    if not x or x in ("-", "—"):
        return None
    try:
        return float(x)
    except ValueError:
        return None


def rel(a: Optional[float], b: Optional[float]) -> float:
    if a is None or b is None or b == 0:
        return float("inf")
    return abs(a - b) / abs(b)


# --------------------------------------------------------------------------
# IS 808:2021
# --------------------------------------------------------------------------
IS808_TABLES = {
    # table: (type, first pdf page, last pdf page, schema)
    "1": ("I-beam (MB/WB)", 7, 8, "I"),
    "2": ("I-beam (JB/LB)", 9, 10, "I"),
    "3": ("narrow parallel flange beam (NPB)", 11, 14, "I"),
    "4": ("wide parallel flange beam (WPB)", 15, 20, "I"),
    "5": ("column / heavy weight beam (SC/HB)", 21, 22, "I"),
    "6": ("sloping flange channel (MC)", 23, 24, "C"),
    "7": ("sloping flange channel (JC/LC)", 25, 26, "C"),
    "8": ("parallel flange channel (MPC)", 27, 28, "C"),
    "9": ("equal angle", 29, 32, "L"),
    "10": ("equal angle (supplementary)", 33, 34, "L"),
    "11": ("unequal angle", 35, 37, "L"),
    "12": ("unequal angle (supplementary)", 38, 39, "L"),
    "13": ("parallel flange bearing pile (PBP)", 40, 41, "I"),
}
# printed column order after designation
I_COLS = ["mass_kg_m", "A_cm2", "D", "B", "tw", "tf", "flange_slope_deg", "R1", "R2",
          "Ixx_cm4", "Iyy_cm4", "rxx", "ryy", "Zex_cm3", "Zey_cm3", "Zpx_cm3", "Zpy_cm3", "It_cm4", "Iw_cm6e6"]
C_COLS = ["mass_kg_m", "A_cm2", "D", "B", "tw", "tf", "flange_slope_deg", "R1", "R2", "Cy",
          "Ixx_cm4", "Iyy_cm4", "rxx", "ryy", "Zex_cm3", "Zey_cm3", "Zpx_cm3", "Zpy_cm3", "It_cm4", "Iw_cm6e6"]
L_COLS = ["mass_kg_m", "A_cm2", "a", "b", "t", "R1", "R2", "Cy", "Cz", "Izz_cm4", "Iyy_cm4",
          "alpha_rad", "Iuu_cm4", "Ivv_cm4", "rzz", "ryy", "ruu", "rvv", "Zez_cm3", "Zey_cm3",
          "Zpz_cm3", "Zpy_cm3", "It_cm4"]
SCHEMA = {"I": I_COLS, "C": C_COLS, "L": L_COLS}

IS808_OUT = [
    "designation", "table_id", "table", "type", "pdf_page", "mass_kg_m", "A_mm2", "D", "B", "tw", "tf",
    "flange_slope_deg", "R1", "R2", "Cy", "Cz", "Ixx_mm4", "Iyy_mm4", "rxx", "ryy", "Zex", "Zey", "Zpx", "Zpy",
    "It", "Iw", "alpha_rad", "Iuu_mm4", "Ivv_mm4", "ruu", "rvv", "units", "check", "repairs",
]

DESIG_RE = re.compile(
    r"^\s{0,12}((?:[A-Z]{2,4}\s*(?:\(P\)\s*)?\d{2,4}(?:\s*[X×]\s*\d{2,4})*\s*[X×]?\*?)|(?:×\s*\d+))\s{2,}(.*)$|^\s{0,12}(∠\s*\d+\s*×\s*\d+\s*×\s*\d+)\s+(\d.*)$"
)


# Text-layer / print glitches of individual rows, read from the page image of YOUR licensed copy.
# They are data about the standard, so none ships with the hub: the corpus-fix step (an LLM agent
# given your PDFs, see CORPUS_FIX_LLM_INSTRUCTIONS.md) writes them to scripts/is808_fixes.json, and
# without that file every row is taken as the text layer prints it and the consistency checks below
# flag the ones that do not add up. Format:
#   {"text_fixes":  [{"page": 10, "designation": "LB 600",
#                     "splice": {"start": -3, "end": null, "with": ["..."]}, "note": "why"}],
#    "token_fixes": [{"page": 29, "designation": "...", "map": {"<printed token>": "<read as>"},
#                     "note": "why"}]}
# `splice` replaces values[start:end] with `with` (Python slice semantics; start null = append).
def _load_is808_fixes() -> tuple[dict, dict]:
    p = Path(__file__).resolve().parent / "is808_fixes.json"
    if not p.is_file():
        return {}, {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"WARNING: {p.name} unreadable ({e}); no row fixes applied", file=sys.stderr)
        return {}, {}
    text: dict = {}
    for e in d.get("text_fixes") or []:
        sp = e.get("splice") or {}

        def fn(v, sp=sp):
            v = list(v)
            a = len(v) if sp.get("start") is None else int(sp["start"])
            b = None if sp.get("end") is None else int(sp["end"])
            if sp.get("start") is None:
                return v + list(sp.get("with") or [])
            return v[:a] + list(sp.get("with") or []) + (v[b:] if b is not None else [])
        text[(int(e["page"]), str(e["designation"]))] = (fn, str(e.get("note") or "row fix (is808_fixes.json)"))
    token = {(int(e["page"]), str(e["designation"])): (dict(e.get("map") or {}),
                                                      str(e.get("note") or "token fix (is808_fixes.json)"))
             for e in d.get("token_fixes") or []}
    return text, token


IS808_TEXT_FIXES, IS808_TOKEN_FIXES = _load_is808_fixes()


def parse_is808(pdf: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tno, (ttype, p0, p1, schema) in IS808_TABLES.items():
        cols = SCHEMA[schema]
        last_angle = None
        for pno in range(p0, p1 + 1):
            for ln in page_text(pdf, pno).splitlines():
                m = DESIG_RE.match(ln)
                if not m:
                    continue
                desig = re.sub(r"\s+", " ", m.group(1) or m.group(3)).strip()
                vals = split_numeric_fields(m.group(2) or m.group(4))
                pre = IS808_TOKEN_FIXES.get((pno, desig))
                if pre:
                    vals = [pre[0].get(v, v) for v in vals]
                dd = [v for v in vals if re.fullmatch(r"\d\.\d\.\d", v)]
                vals = [v.replace(".", "", 1) if re.fullmatch(r"\d\.\d\.\d", v) else v for v in vals]
                if not vals or not all(NUM_RE.match(v) for v in vals):
                    continue
                if schema == "L":
                    if desig.startswith("∠"):
                        last_angle = re.match(r"∠\s*(\d+)\s*×\s*(\d+)", desig)
                    elif desig.startswith("×") and last_angle:
                        desig = f"∠ {last_angle.group(1)} × {last_angle.group(2)} × {desig[1:].strip()}"
                    else:
                        continue
                rec: dict[str, Any] = {
                    "designation": desig,
                    "table": tno,
                    "type": ttype,
                    "pdf_page": pno,
                    "_raw": vals,
                    "_schema": schema,
                    "repairs": [f"value printed '{x}' (stray point) read as {x.replace('.', '', 1)}" for x in dd
                                if re.fullmatch(r"\d\.\d\.\d", x)],
                }
                if pre:
                    rec["repairs"].append(pre[1])
                fix = IS808_TEXT_FIXES.get((pno, desig))
                if fix:
                    vals = fix[0](vals)
                    rec["_raw"] = vals
                    rec["repairs"].append(fix[1])
                if len(vals) != len(cols):
                    rec["_ncols_error"] = f"{len(vals)} values, expected {len(cols)}"
                for c, v in zip(cols, vals):
                    rec[c] = v
                if re.search(r"[X×]$", desig):
                    rec["designation"] = f"{desig} {rec.get('mass_kg_m')}"
                rows.append(rec)
    return rows


def half_ulp(s: Any) -> float:
    """Half a unit of the last printed digit, relative to the value."""
    s = str(s or "").strip()
    v = fnum(s)
    if not v:
        return 0.0
    if "." in s:
        ulp = 10 ** (-len(s.split(".")[1]))
    else:
        digits = s.lstrip("-")
        trailing = len(digits) - len(digits.rstrip("0"))
        ulp = 10 ** trailing if trailing and len(digits) > 3 else 1
    return 0.5 * ulp / abs(v)


def cmp_field(r: dict[str, Any], label: str, printed_key: str, calc: Optional[float], inputs: list[str],
              power: float = 1.0) -> Optional[str]:
    """Compare printed field with value computed from other printed fields.

    Tolerance = 1 % + rounding of the printed field + propagated rounding of inputs.
    A printed value off by exactly x10 / x0.1 (lost or shifted decimal point, or cm
    printed under a mm header) is repaired and recorded.
    """
    ps = r.get(printed_key)
    p = fnum(str(ps)) if ps is not None else None
    if p is None or calc is None or calc == 0:
        return None
    tol = TOL + half_ulp(ps) + power * sum(half_ulp(r.get(k)) for k in inputs)
    if rel(p, calc) <= tol:
        return None
    for f in (10.0, 0.1):
        if rel(p * f, calc) <= tol:
            newv = round(p * f, 6)
            r[printed_key] = f"{newv:g}"
            r["repairs"].append(f"{label} printed {ps} -> {newv:g} (decimal/unit slip; {label} = {calc:.4g} from other columns)")
            return None
    return f"{label} printed {ps} vs computed {calc:.4g}"


def check_is808(r: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    if r.get("_ncols_error"):
        return [r["_ncols_error"]]
    g = lambda k: fnum(str(r.get(k))) if r.get(k) is not None else None  # noqa: E731
    schema = r["_schema"]
    A = g("A_cm2")
    e = cmp_field(r, "mass", "mass_kg_m", 0.785 * A if A else None, ["A_cm2"])
    if e:
        # arbitrate with plain geometry for parallel-flange I sections (R2 = 0)
        D, B, tw, tf, R1 = g("D"), g("B"), g("tw"), g("tf"), g("R1")
        if schema == "I" and g("R2") == 0 and None not in (D, B, tw, tf, R1):
            Ag = (2 * B * tf + (D - 2 * tf) * tw + (4 - math.pi) * R1 ** 2) / 100
            if rel(A, Ag) <= 0.02:
                e += f" (A {A} agrees with geometry {Ag:.1f} cm2: printed mass is the misprint; 0.785A = {0.785 * A:.2f})"
            elif rel((g('mass_kg_m') or 0) / 0.785, Ag) <= 0.02:
                e += f" (mass agrees with geometry A {Ag:.1f} cm2: printed A is the misprint)"
        errs.append(e)
    if schema in ("I", "C"):
        D, B = g("D"), g("B")
        cy = g("Cy") if schema == "C" else None
        for ax, Ik, rk, Zk, c, cin in (
            ("x", "Ixx_cm4", "rxx", "Zex_cm3", D / 2 if D else None, ["D"]),
            ("y", "Iyy_cm4", "ryy", "Zey_cm3", (B / 2 if schema == "I" else (B - (cy or 0))) if B else None,
             ["B"] + (["Cy"] if schema == "C" else [])),
        ):
            I = g(Ik)
            if I is None or A is None:
                continue
            e = cmp_field(r, f"r{ax}{ax}", rk, math.sqrt(I * 1e4 / (A * 100)), [Ik, "A_cm2"], power=0.5)
            if e:
                errs.append(e)
            if c:
                e = cmp_field(r, f"Ze{ax}", Zk, I * 1e4 / c / 1e3, [Ik] + cin)
                if e:
                    errs.append(e)
        m = re.search(r"(\d{2,4})", r["designation"])
        if m and D and not (0.85 <= D / float(m.group(1)) <= 1.25):
            errs.append(f"designation size {m.group(1)} vs D {D}")
    else:  # angles: Zz = Izz/(a - Cy), Zy = Iyy/(b - Cz)
        a, b, t = g("a"), g("b"), g("t")
        m = re.match(r"∠\s*(\d+)\s*×\s*(\d+)\s*×\s*(\d+)", r["designation"])
        if m and (float(m.group(1)), float(m.group(2)), float(m.group(3))) != (a, b, t):
            errs.append(f"designation {m.groups()} vs a,b,t {(a, b, t)}")
        for ax, Ik, rk in (("zz", "Izz_cm4", "rzz"), ("yy", "Iyy_cm4", "ryy"), ("uu", "Iuu_cm4", "ruu"),
                           ("vv", "Ivv_cm4", "rvv")):
            I = g(Ik)
            if I is None or A is None:
                continue
            e = cmp_field(r, f"r{ax}", rk, math.sqrt(I * 1e4 / (A * 100)), [Ik, "A_cm2"], power=0.5)
            if e:
                errs.append(e)
        for lab, Ik, Zk, c, cin in (("Zez", "Izz_cm4", "Zez_cm3", (a - (g("Cy") or 0)) if a else None, ["Cy"]),
                                    ("Zey", "Iyy_cm4", "Zey_cm3", (b - (g("Cz") or 0)) if b else None, ["Cz"])):
            I = g(Ik)
            if I is None or not c:
                continue
            e = cmp_field(r, lab, Zk, I * 1e4 / c / 1e3, [Ik] + cin)
            if e:
                errs.append(e)
    return errs


def finalize_is808(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    seen: dict[str, int] = {}
    for r in rows:
        seen[r["designation"]] = seen.get(r["designation"], 0) + 1
    count: dict[str, int] = {}
    for r in rows:
        errs = check_is808(r)
        g = {k: fnum(str(v)) for k, v in r.items() if not k.startswith("_") and isinstance(v, (str, int, float))}
        d = r["designation"]
        count[d] = count.get(d, 0) + 1
        tid = d if seen[d] == 1 else f"{d} {r.get('mass_kg_m')}"
        # the first (lightest) occurrence also answers the bare designation
        o: dict[str, Any] = {
            "designation": d,
            "table_id": tid,
            "table": r["table"],
            "type": r["type"],
            "pdf_page": r["pdf_page"],
            "mass_kg_m": g.get("mass_kg_m"),
            "A_mm2": round(g["A_cm2"] * 100, 3) if g.get("A_cm2") is not None else None,
            "units": "mm, mm2, mm4, mm3, mm6, kg/m, rad",
            "check": "PASS" if not errs else "FAIL: " + "; ".join(errs),
            "repairs": "; ".join(r["repairs"]),
        }
        sc = r["_schema"]
        if sc in ("I", "C"):
            for k in ("D", "B", "tw", "tf", "flange_slope_deg", "R1", "R2", "rxx", "ryy"):
                o[k] = g.get(k)
            o["Cy"] = g.get("Cy")
            for src, dst, f in (("Ixx_cm4", "Ixx_mm4", 1e4), ("Iyy_cm4", "Iyy_mm4", 1e4), ("Zex_cm3", "Zex", 1e3),
                                ("Zey_cm3", "Zey", 1e3), ("Zpx_cm3", "Zpx", 1e3), ("Zpy_cm3", "Zpy", 1e3),
                                ("It_cm4", "It", 1e4), ("Iw_cm6e6", "Iw", 1e6)):
                o[dst] = round(g[src] * f, 3) if g.get(src) is not None else None
        else:
            o.update({"D": g.get("a"), "B": g.get("b"), "tw": g.get("t"), "tf": g.get("t"), "R1": g.get("R1"),
                      "R2": g.get("R2"), "Cy": g.get("Cy"), "Cz": g.get("Cz"), "rxx": g.get("rzz"),
                      "ryy": g.get("ryy"), "ruu": g.get("ruu"), "rvv": g.get("rvv"), "alpha_rad": g.get("alpha_rad")})
            for src, dst, f in (("Izz_cm4", "Ixx_mm4", 1e4), ("Iyy_cm4", "Iyy_mm4", 1e4), ("Iuu_cm4", "Iuu_mm4", 1e4),
                                ("Ivv_cm4", "Ivv_mm4", 1e4), ("Zez_cm3", "Zex", 1e3), ("Zey_cm3", "Zey", 1e3),
                                ("Zpz_cm3", "Zpx", 1e3), ("Zpy_cm3", "Zpy", 1e3), ("It_cm4", "It", 1e4)):
                o[dst] = round(g[src] * f, 3) if g.get(src) is not None else None
        out.append(o)
    return out


# --------------------------------------------------------------------------
# IS 1161:2014 (CHS only; the standard has no RHS/SHS -- those are IS 4923)
# --------------------------------------------------------------------------
IS1161_OUT = ["designation", "table_id", "NB", "D", "t", "mass_kg_m", "A_mm2", "I_mm4", "Z_mm3", "r_mm",
              "pdf_page", "units", "check", "repairs"]


def chs_geom(D: float, t: float) -> dict[str, float]:
    d = D - 2 * t
    A = math.pi / 4 * (D ** 2 - d ** 2)  # mm2
    I = math.pi / 64 * (D ** 4 - d ** 4)  # mm4
    return {"A_cm2": A / 100, "I_cm4": I / 1e4, "Z_cm3": I / (D / 2) / 1e3, "r_cm": math.sqrt(I / A) / 10,
            "mass": A * 7.85e-3}


def thin_space_variants(raw: str) -> list[float]:
    """IS 1161 text layer renders the thin thousands space as '0' (1 053.42 -> 10053.42)."""
    out = []
    v = fnum(raw)
    if v is not None:
        out.append(v)
    m = re.fullmatch(r"(\d)0(\d{3}(?:\.\d+)?)", raw)
    if m:
        out.append(float(m.group(1) + m.group(2)))
    return out


def parse_is1161(pdf: Path) -> list[dict[str, Any]]:
    rows = []
    nb = None
    for pno in range(1, n_pages(pdf) + 1):
        txt = page_text(pdf, pno)
        if "Table 1" not in txt:
            continue
        for ln in txt.splitlines():
            m = re.match(r"^\s*(\d{1,3})?\s+(\d{2,3}(?:\.\d)?)\s+(\d{1,2}(?:\.\d)?)\s+(\d.*)$", ln)
            if not m:
                continue
            rest = m.group(4)
            # numbers here use a single space as thousands sep; columns >= 1 space apart
            fields = split_numeric_fields(re.sub(r"(?<=\d) (?=\d{3}\b)", "", rest))
            if len(fields) == 8 and fields[0].count(".") == 1 and " " not in rest[:0]:
                pass
            if len(fields) < 8:
                # mass column may be glued to thickness ("8 68.58")
                fields = re.split(r"\s+", rest.strip())
                fields = [f for f in fields if f]
            if len(fields) != 9:
                # recombine thousands groups: tokens of exactly 3 digits after 1-3 digit int
                toks = re.split(r"\s+", rest.strip())
                merged: list[str] = []
                for tk in toks:
                    if merged and re.fullmatch(r"\d{3}", tk) and re.fullmatch(r"\d{1,3}", merged[-1]):
                        merged[-1] += tk
                    else:
                        merged.append(tk)
                fields = merged
            if len(fields) != 9:
                continue
            if m.group(1):
                nb = m.group(1)
            D, t = float(m.group(2)), float(m.group(3))
            rows.append({"NB": m.group(1) or "", "D": D, "t": t, "raw": fields, "pdf_page": pno})
    # NB is printed on the middle row of each group: propagate within same D
    byD: dict[float, str] = {}
    for r in rows:
        if r["NB"]:
            byD[r["D"]] = r["NB"]
    out = []
    for r in rows:
        D, t = r["D"], r["t"]
        mass_s, A_s, _vol, _se, _si, I_s, Z_s, r_s, _r2 = r["raw"]
        g = chs_geom(D, t)
        repairs = []
        errs = []

        def pick(raw: str, target: float, label: str) -> Optional[float]:
            cands = thin_space_variants(raw)
            best = None
            for i, c in enumerate(cands):
                if rel(c, target) <= 0.015:
                    best = c
                    if i > 0:
                        repairs.append(f"{label} '{raw}' read as {c:g} (thin space printed as 0)")
                    break
            if best is None:
                errs.append(f"{label} printed {raw} vs geometry {target:.4g}")
                return cands[0] if cands else None
            return best

        mass = pick(mass_s, g["mass"], "mass")
        A = pick(A_s, g["A_cm2"], "A")
        I = pick(I_s, g["I_cm4"], "I")
        Z = pick(Z_s, g["Z_cm3"], "Z")
        rr = pick(r_s, g["r_cm"], "r")
        if mass and A and rel(mass, 0.785 * A) > 0.012:
            errs.append(f"mass {mass} vs 0.785A {0.785 * A:.3f}")
        if I and Z and rel(Z, I / (D / 20)) > TOL:
            errs.append(f"Z {Z} vs I/(D/2) {I / (D / 20):.2f}")
        if I and A and rr and rel(rr, math.sqrt(I / A)) > TOL:
            errs.append(f"r {rr} vs sqrt(I/A) {math.sqrt(I / A):.3f}")
        desig = f"CHS {D:g}x{t:g}"
        out.append({
            "designation": desig,
            "table_id": f"{D:g}x{t:g}",
            "NB": byD.get(D, ""),
            "D": D,
            "t": t,
            "mass_kg_m": mass,
            "A_mm2": round(A * 100, 2) if A else None,
            "I_mm4": round(I * 1e4, 1) if I else None,
            "Z_mm3": round(Z * 1e3, 1) if Z else None,
            "r_mm": round(rr * 10, 2) if rr else None,
            "pdf_page": r["pdf_page"],
            "units": "mm, mm2, mm4, mm3, kg/m",
            "check": "PASS" if not errs else "FAIL: " + "; ".join(errs),
            "repairs": "; ".join(repairs),
        })
    return out


# --------------------------------------------------------------------------
# IS 811:1987 (scanned; PDF text layer is OCR -> heavy repair + geometry check)
# --------------------------------------------------------------------------
from is811_sections import parse_is811, IS811_OUT  # noqa: E402


def write_csv(path: Path, rows: list[dict[str, Any]], cols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--pdf-dir", type=Path, default=None)
    ap.add_argument("--only", default=None)
    a = ap.parse_args(argv)
    std = a.root / "documents" / "standards"
    report: dict[str, Any] = {}
    jobs = {
        "IS_808_2021": lambda: (finalize_is808(parse_is808(find_pdf("IS_808_2021.pdf", a.pdf_dir))), IS808_OUT),
        "IS_1161_2014": lambda: (parse_is1161(find_pdf("IS_1161_2014.pdf", a.pdf_dir)), IS1161_OUT),
        "IS_811_1987": lambda: (parse_is811(find_pdf("IS_811_1987.pdf", a.pdf_dir), a.root), IS811_OUT),
    }
    for stem, fn in jobs.items():
        if a.only and stem != a.only:
            continue
        rows, cols = fn()
        write_csv(std / stem / "structured" / "sections.csv", rows, cols)
        fails = [r for r in rows if str(r.get("check", "")).startswith(("FAIL", "FLAG"))]
        rep = {
            "stem": stem,
            "rows": len(rows),
            "pass": len(rows) - len(fails),
            "fail": len(fails),
            "repaired_rows": sum(1 for r in rows if r.get("repairs")),
            "unresolved": [{"designation": r["designation"], "pdf_page": r.get("pdf_page"), "check": r["check"]}
                           for r in fails],
            "source": "BIS PDF text layer via pdftotext -layout; consistency-checked",
        }
        (std / stem / "structured" / "sections_check.json").write_text(
            json.dumps(rep, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        report[stem] = {k: v for k, v in rep.items() if k != "unresolved"} | {"unresolved_n": len(fails)}
    # K03: IS 811 engine labels and the IS 808 plate-area check (quarantines rows whose dimensions and
    # area/mass/inertia belong to different sections; nothing is corrected or invented)
    from corpus_fixes import apply_section_checks

    report["post_checks"] = apply_section_checks(a.root)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
