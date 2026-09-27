"""IS 811:1987 cold-formed section tables -> checked rows (CORPUS-09).

The IS 811 PDF is a 1987 scan: its text layer is OCR with digit/letter confusions,
dropped decimal points and merged rows.  Each cell is therefore read from two
independent OCR sources:

  (a) the PDF's own text layer (``pdftotext -layout``), and
  (b) Tesseract 5 on a 300-dpi render of the page,

and the value is chosen by agreement between the sources, then checked against
(i) exact table relations (M = 0.785 A, R = sqrt(I/A), Z = I/c, R_i = 1.5 t,
designation = dimension columns) and (ii) a thin-walled geometric model of the
section (centre-line with R_i = 1.5 t corners) for A, I, centroid, J, x0, Cw.

A cell whose sources disagree and which the checks cannot settle is left as the
PDF text-layer value and the row is listed as unresolved.
"""
from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

# --------------------------------------------------------------------------
# table schemas (column names after the designation, in printed order)
# --------------------------------------------------------------------------
TABLES: dict[str, dict[str, Any]] = {
    "1": {"shape": "equal_angle", "pages": (8, 9), "dims": ("h", "h", "t"),
          "cols": ["h", "t", "Ri", "M", "A", "Cx", "Cy", "Ixx", "Iuu", "Ivv", "Rxx", "Ruu", "Rvv", "Zxx", "Ixy"]},
    "2": {"shape": "unequal_angle", "pages": (10, 11), "dims": ("h", "b", "t"),
          # printed headers read 'Ivv' (col 12) and 'Iuu' (col 13); col 12 is the major value
          "cols": ["h", "b", "Ri", "t", "M", "A", "Cx", "Cy", "Ixx", "Iyy", "Iuu", "Ivv", "Rxx", "Ryy", "Rvv",
                   "tan_alpha", "Zxx", "Zyy", "Ixy"]},
    "3": {"shape": "channel", "pages": (12, 13), "dims": ("h", "h", "t"),
          "cols": ["h", "t", "Ri", "M", "A", "Cy", "Ixx", "Iyy", "Rxx", "Ryy", "Zxx", "Zyy", "X0", "J", "Cw"]},
    "4": {"shape": "channel", "pages": (14, 17), "dims": ("h", "b", "t"),
          "cols": ["h", "b", "t", "Ri", "M", "A", "Cy", "Ixx", "Iyy", "Rxx", "Ryy", "Zxx", "Zyy", "X0", "J", "Cw"]},
    "5": {"shape": "lipped_channel", "pages": (18, 19), "dims": ("h", "h", "c", "t"),
          "cols": ["h", "c", "t", "Ri", "M", "A", "Cy", "Ixx", "Iyy", "Rxx", "Ryy", "Zxx", "Zyy", "X0", "J", "Cw"]},
    "6": {"shape": "lipped_channel", "pages": (20, 22), "dims": ("h", "b", "c", "t"),
          "cols": ["h", "b", "c", "t", "Ri", "M", "A", "Cy", "Ixx", "Iyy", "Rxx", "Ryy", "Zxx", "Zyy", "X0", "J",
                   "Cw"]},
    "7": {"shape": "hat", "pages": (23, 23), "dims": ("h", "h", "c", "t"),
          "cols": ["h", "c", "t", "Ri", "M", "A", "Cy", "Ixx", "Iyy", "Rxx", "Ryy", "Zxx", "Zyy", "X0", "J", "Cw"]},
    "8": {"shape": "hat", "pages": (24, 24), "dims": ("h", "b", "c", "t"),
          "cols": ["h", "b", "c", "t", "Ri", "M", "A", "Cy", "Ixx", "Iyy", "Rxx", "Ryy", "Zxx", "Zyy", "X0", "J",
                   "Cw"]},
    "9": {"shape": "hat", "pages": (25, 25), "dims": ("h", "b", "c", "t"),
          "cols": ["h", "b", "c", "t", "Ri", "M", "A", "Cy", "Ixx", "Iyy", "Rxx", "Ryy", "Zxx", "Zyy", "X0", "J",
                   "Cw"]},
    "10": {"shape": "lipped_zed", "pages": (26, 30), "dims": ("h", "b", "c", "t"),
           "cols": ["M", "A", "Ixx", "Iyy", "Iuu", "Ivv", "Rvv_min", "tan_theta", "Zxx", "Zyy", "Zuu", "Zvv", "Ixy",
                    "J", "Cw"]},
}

IS811_OUT = [
    "designation", "table_id", "table", "type", "pdf_page", "h", "b", "c", "t", "Ri", "mass_kg_m", "A_mm2",
    "Cx", "Cy", "Ixx_mm4", "Iyy_mm4", "Iuu_mm4", "Ivv_mm4", "Ixy_mm4", "rxx", "ryy", "ruu", "rvv",
    "tan_alpha", "Zx", "Zy", "Zu", "Zv", "x0", "J_mm4", "Cw_mm6", "units", "check", "sources", "repairs",
    "label",   # engine label (CLR100X50X15X2 ...), filled by corpus_fixes.apply_section_checks
]
UNIT = {"A": 100.0, "Ixx": 1e4, "Iyy": 1e4, "Iuu": 1e4, "Ivv": 1e4, "Ixy": 1e4, "Zxx": 1e3, "Zyy": 1e3,
        "Zuu": 1e3, "Zvv": 1e3, "Rxx": 10.0, "Ryy": 10.0, "Ruu": 10.0, "Rvv": 10.0, "Rvv_min": 10.0,
        "Cx": 10.0, "Cy": 10.0, "X0": 10.0, "J": 1e4, "Cw": 1e6}
SHAPE_NAME = {
    "equal_angle": "equal angle", "unequal_angle": "unequal angle", "channel": "channel without lips",
    "lipped_channel": "channel with lips", "hat": "hat section", "lipped_zed": "lipped zed (equal flanges)",
}

# --------------------------------------------------------------------------
# OCR sources
# --------------------------------------------------------------------------
WM_RE = re.compile(r"Free Standard provided by BIS|BSB Edge|@[\w.-]+\.\w+", re.I)


def pdf_text(pdf: Path, pno: int) -> str:
    from bis_text import poppler_tool
    out = subprocess.run([poppler_tool("pdftotext") or "pdftotext", "-layout", "-f", str(pno), "-l", str(pno), str(pdf), "-"],
                         capture_output=True, text=True, errors="replace").stdout
    return "\n".join(ln for ln in out.splitlines() if not WM_RE.search(ln))


def tess_text(pdf: Path, pno: int, cache: Path) -> str:
    cache.mkdir(parents=True, exist_ok=True)
    f = cache / f"p{pno:02d}.psm6.txt"
    if not f.is_file():
        png = cache / f"p{pno:02d}"
        from bis_text import poppler_tool
        subprocess.run([poppler_tool("pdftoppm") or "pdftoppm", "-f", str(pno), "-l", str(pno), "-r", "300", "-gray", "-png", "-singlefile",
                        str(pdf), str(png)], check=True)
        subprocess.run(["tesseract", str(png) + ".png", str(f)[:-4], "--psm", "6"], capture_output=True)
        try:
            (Path(str(png) + ".png")).unlink()
        except OSError:
            pass
    txt = f.read_text(encoding="utf-8", errors="replace") if f.is_file() else ""
    return "\n".join(ln for ln in txt.splitlines() if not WM_RE.search(ln))


_CONF = str.maketrans({"I": "1", "l": "1", "|": "1", "O": "0", "o": "0", "S": "5", "s": "5", "B": "8",
                       ",": ".", "·": ".", "Q": "0", "D": "0", "L": "1", "i": "1", "Z": "2", "z": "2", "J": "1",
                       "a": "0", "G": "6", "b": "6", "q": "9", "g": "9", "T": "7", "U": "0"})


def norm_token(tok: str) -> Optional[str]:
    t = tok.strip().strip(".:;'\"`‘’()[]{}_-—=~*")
    if not t:
        return None
    t = t.translate(_CONF)
    t = t.replace("..", ".")
    if re.fullmatch(r"\d+(\.\d+)?", t):
        return t
    return None


DESIG_RE = re.compile(
    r"^[^0-9A-Za-z]{0,4}([0-9OoIlSsz]{2,3})\s*[xX×*&K]\s*([0-9OoIlSsz]{2,3})\s*"
    r"(?:[xX×*&K]?\s*([0-9OoIlSs]{1,2})\s*)?[xX×*&K]\s*([0-9OoIlSs][.,·][0-9OoIlSsQ]{2})[-.]?\s+(.*)$"
)


def parse_rows(text: str, ndim: int) -> list[tuple[tuple, list[str], str]]:
    """(dims_from_designation, value tokens, raw line) for lines that start with a designation."""
    rows = []
    for ln in text.splitlines():
        s = re.sub(r"\s+", " ", ln.strip())
        s = s.replace("X", "x").replace("×", "x")
        m = DESIG_RE.match(s)
        if not m:
            continue
        g = [m.group(1), m.group(2), m.group(3), m.group(4)]
        dims = [norm_token(x) if x else None for x in g]
        if ndim == 3 and dims[2] is not None:
            # 3-part designation mis-split: skip
            continue
        if ndim == 4 and dims[2] is None:
            continue
        if any(d is None for d in (dims[0], dims[1], dims[3])):
            continue
        key = tuple(float(d) for d in dims if d is not None)
        toks = [t for t in (norm_token(x) for x in m.group(5).split(" ")) if t is not None]
        rows.append((key, toks, ln))
    return rows


# --------------------------------------------------------------------------
# thin-walled geometry (centre line, R_i = 1.5 t corners), units mm
# --------------------------------------------------------------------------
def fillet_path(pts: list[tuple[float, float]], rc: float, nseg: int = 8) -> list[tuple[float, float]]:
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        p0, p1, p2 = pts[i - 1], pts[i], pts[i + 1]
        v1 = (p0[0] - p1[0], p0[1] - p1[1])
        v2 = (p2[0] - p1[0], p2[1] - p1[1])
        l1, l2 = math.hypot(*v1), math.hypot(*v2)
        u1 = (v1[0] / l1, v1[1] / l1)
        u2 = (v2[0] / l2, v2[1] / l2)
        cosang = max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1]))
        ang = math.acos(cosang)
        if ang < 1e-6 or ang > math.pi - 1e-6:
            out.append(p1)
            continue
        d = rc / math.tan(ang / 2)
        d = min(d, 0.49 * l1, 0.49 * l2)
        a = (p1[0] + u1[0] * d, p1[1] + u1[1] * d)
        b = (p1[0] + u2[0] * d, p1[1] + u2[1] * d)
        bis = (u1[0] + u2[0], u1[1] + u2[1])
        lb = math.hypot(*bis)
        cdist = rc / math.sin(ang / 2)
        c = (p1[0] + bis[0] / lb * cdist, p1[1] + bis[1] / lb * cdist)
        a0 = math.atan2(a[1] - c[1], a[0] - c[0])
        a1 = math.atan2(b[1] - c[1], b[0] - c[0])
        da = a1 - a0
        while da > math.pi:
            da -= 2 * math.pi
        while da < -math.pi:
            da += 2 * math.pi
        for k in range(nseg + 1):
            th = a0 + da * k / nseg
            out.append((c[0] + rc * math.cos(th), c[1] + rc * math.sin(th)))
    out.append(pts[-1])
    return out


def centreline(shape: str, h: float, b: float, c: float, t: float) -> list[tuple[float, float]]:
    """Sharp-corner centre line (mm). x to the right, y up; h, b, c are outside dimensions."""
    hh, bb, cc = h - t, b - t / 2, c - t / 2
    if shape in ("equal_angle", "unequal_angle"):
        # vertical leg h (x = 0), horizontal leg b (y = 0)
        return [(0.0, h - t / 2), (0.0, 0.0), (b - t / 2, 0.0)]
    if shape == "channel":
        return [(bb, hh / 2), (0.0, hh / 2), (0.0, -hh / 2), (bb, -hh / 2)]
    if shape == "lipped_channel":
        bb2 = b - t
        return [(bb2, hh / 2 - cc), (bb2, hh / 2), (0.0, hh / 2), (0.0, -hh / 2), (bb2, -hh / 2),
                (bb2, -hh / 2 + cc)]
    if shape == "lipped_zed":
        bb2 = b - t
        return [(bb2, hh / 2 - cc), (bb2, hh / 2), (0.0, hh / 2), (0.0, -hh / 2), (-bb2, -hh / 2),
                (-bb2, -hh / 2 + cc)]
    if shape == "hat":
        # top-hat: crown width b at the top, webs of height h, outstands c at the bottom
        w = b - t
        return [(-w / 2 - cc, 0.0), (-w / 2, 0.0), (-w / 2, hh), (w / 2, hh), (w / 2, 0.0), (w / 2 + cc, 0.0)]
    raise ValueError(shape)


def section_props(shape: str, h: float, b: float, c: float, t: float, *, ri: Optional[float] = None,
                  kred: float = 1.0) -> dict[str, float]:
    ri = 1.5 * t if ri is None else ri
    sharp = centreline(shape, h, b, c, t)
    pts = fillet_path(sharp, ri + t / 2)
    # element list: (xa, ya, xb, yb, thickness)
    els = []
    for (xa, ya), (xb, yb) in zip(pts[:-1], pts[1:]):
        L = math.hypot(xb - xa, yb - ya)
        if L < 1e-9:
            continue
        els.append((xa, ya, xb, yb, t, L))
    A = sum(e[4] * e[5] for e in els)
    xc = sum(e[4] * e[5] * (e[0] + e[2]) / 2 for e in els) / A
    yc = sum(e[4] * e[5] * (e[1] + e[3]) / 2 for e in els) / A
    Ix = Iy = Ixy = 0.0
    for xa, ya, xb, yb, tt, L in els:
        xa_, xb_, ya_, yb_ = xa - xc, xb - xc, ya - yc, yb - yc
        Ix += tt * L * (ya_ ** 2 + ya_ * yb_ + yb_ ** 2) / 3
        Iy += tt * L * (xa_ ** 2 + xa_ * xb_ + xb_ ** 2) / 3
        Ixy += tt * L * (2 * xa_ * ya_ + xa_ * yb_ + xb_ * ya_ + 2 * xb_ * yb_) / 6
    J = sum(e[4] ** 3 * e[5] / 3 for e in els)

    # shear centre + warping constant (sectorial coordinates, pole at centroid)
    def sectorial(px: float, py: float) -> list[float]:
        om = [0.0]
        for xa, ya, xb, yb, tt, L in els:
            om.append(om[-1] + ((xa - px) * (yb - py) - (xb - px) * (ya - py)))
        return om

    om = sectorial(xc, yc)
    Iwx = Iwy = 0.0
    for i, (xa, ya, xb, yb, tt, L) in enumerate(els):
        wa, wb = om[i], om[i + 1]
        Iwx += tt * L * (wa * (2 * (ya - yc) + (yb - yc)) + wb * ((ya - yc) + 2 * (yb - yc))) / 6
        Iwy += tt * L * (wa * (2 * (xa - xc) + (xb - xc)) + wb * ((xa - xc) + 2 * (xb - xc))) / 6
    den = Ix * Iy - Ixy ** 2
    xs = xc + (Iy * Iwx - Ixy * Iwy) / den
    ys = yc - (Ix * Iwy - Ixy * Iwx) / den
    om_s = sectorial(xs, ys)
    wbar = sum(els[i][4] * els[i][5] * (om_s[i] + om_s[i + 1]) / 2 for i in range(len(els))) / A
    Cw = 0.0
    for i, (xa, ya, xb, yb, tt, L) in enumerate(els):
        wa, wb = om_s[i] - wbar, om_s[i + 1] - wbar
        Cw += tt * L * (wa * wa + wa * wb + wb * wb) / 3
    # principal
    avg = (Ix + Iy) / 2
    rad = math.sqrt(((Ix - Iy) / 2) ** 2 + Ixy ** 2)
    Iu, Iv = avg + rad, avg - rad
    theta = 0.5 * math.atan2(-2 * Ixy, Ix - Iy) if abs(Ix - Iy) > 1e-9 or abs(Ixy) > 1e-9 else 0.0
    # extreme fibres (outer faces) for Z
    xs_pts = [p[0] for p in sharp]
    ys_pts = [p[1] for p in sharp]
    xmin, xmax = min(xs_pts) - t / 2, max(xs_pts) + t / 2
    ymin, ymax = min(ys_pts) - t / 2, max(ys_pts) + t / 2
    # extreme fibre distances along principal axes (centre-line points +- t/2)
    ct, st = math.cos(theta), math.sin(theta)
    du = max(abs((p[0] - xc) * -st + (p[1] - yc) * ct) for p in pts) + t / 2   # distance from u axis
    dv = max(abs((p[0] - xc) * ct + (p[1] - yc) * st) for p in pts) + t / 2    # distance from v axis
    return {
        "du": du, "dv": dv,
        "A": A, "xc": xc, "yc": yc, "Ix": Ix, "Iy": Iy, "Ixy": Ixy, "Iu": Iu, "Iv": Iv, "theta": theta,
        "J": J, "Cw": Cw, "xs": xs, "ys": ys,
        "cx_from_left": xc - xmin, "cy_from_bottom": yc - ymin, "cx_from_right": xmax - xc,
        "cy_from_top": ymax - yc, "xmin": xmin, "xmax": xmax, "ymin": ymin, "ymax": ymax,
        "x0_centroid": abs(xs - xc), "x0_web_cl": abs(xs - 0.0),
    }


# --------------------------------------------------------------------------
# expected values per column (printed units: cm-based) and tolerances
# --------------------------------------------------------------------------
def expected(shape: str, dims: dict[str, float], g: dict[str, float]) -> dict[str, list[float]]:
    """Candidate expected values (several conventions) per column, in printed units."""
    h, b, c, t = dims["h"], dims["b"], dims.get("c", 0.0), dims["t"]
    e: dict[str, list[float]] = {
        "h": [h], "b": [b], "c": [c], "t": [t], "Ri": [round(1.5 * t + 1e-9, 2)],
        "A": [g["A"] / 100], "M": [g["A"] / 100 * 0.785],
        "J": [g["J"] / 1e4], "Cw": [g["Cw"] / 1e6],
    }
    Ix, Iy = g["Ix"] / 1e4, g["Iy"] / 1e4
    ctop, cbot = g["cy_from_top"] / 10, g["cy_from_bottom"] / 10
    clef, crig = g["cx_from_left"] / 10, g["cx_from_right"] / 10
    e["Zxx"] = [Ix / ctop, Ix / cbot, Ix / max(ctop, cbot), Iy / clef, Iy / crig]
    e["Zyy"] = [Iy / clef, Iy / crig, Iy / max(clef, crig), Ix / ctop, Ix / cbot]
    e["Zuu"] = [g["Iu"] / 1e4 / (g["du"] / 10)]
    e["Zvv"] = [g["Iv"] / 1e4 / (g["dv"] / 10)]
    if shape in ("equal_angle", "unequal_angle", "lipped_zed"):
        e["Ixx"] = [g["Ix"] / 1e4, g["Iy"] / 1e4] if shape == "equal_angle" else [Ix]
        e["Iyy"] = [Iy]
        e["Iuu"] = [g["Iu"] / 1e4]
        e["Ivv"] = [g["Iv"] / 1e4]
        e["Ixy"] = [abs(g["Ixy"]) / 1e4]
        e["tan_alpha"] = [abs(math.tan(g["theta"])), abs(1 / math.tan(g["theta"])) if g["theta"] else 0.0]
        e["tan_theta"] = e["tan_alpha"]
        e["Cx"] = [g["cx_from_left"] / 10, g["cy_from_bottom"] / 10]
        e["Cy"] = [g["cy_from_bottom"] / 10, g["cx_from_left"] / 10]
    else:
        e["Ixx"] = [Ix]
        e["Iyy"] = [Iy]
        e["Cy"] = [g["cx_from_left"] / 10, g["cy_from_bottom"] / 10, g["cy_from_top"] / 10, g["cx_from_right"] / 10]
        e["X0"] = [g["x0_centroid"] / 10, g["x0_web_cl"] / 10, (g["x0_web_cl"] + t / 2) / 10]
        if shape == "hat":
            # symmetric about the vertical axis: Cy and X0 are vertical distances
            e["Cy"] = [g["cy_from_top"] / 10, g["cy_from_bottom"] / 10]
            e["X0"] = [abs(g["ys"] - g["yc"]) / 10, abs(g["ys"] - g["ymax"]) / 10, abs(g["ys"] - g["ymin"]) / 10]
    A = g["A"] / 100
    e["Rxx"] = [math.sqrt(Ix / A), math.sqrt(Iy / A)]
    e["Ryy"] = [math.sqrt(Iy / A), math.sqrt(Ix / A)]
    e["Ruu"] = [math.sqrt(g["Iu"] / 1e4 / A)]
    e["Rvv"] = [math.sqrt(max(g["Iv"], 1e-9) / 1e4 / A)]
    return e


TOL = {"A": 0.04, "M": 0.04, "Ixx": 0.05, "Iyy": 0.06, "Iuu": 0.05, "Ivv": 0.08, "Ixy": 0.08, "J": 0.12,
       "Cw": 0.15, "Cx": 0.06, "Cy": 0.06, "X0": 0.10, "tan_alpha": 0.06, "tan_theta": 0.06}


def rel(a: float, b: float) -> float:
    return abs(a - b) / abs(b) if b else (0.0 if a == 0 else float("inf"))


def decimal_variants(tok: str) -> list[float]:
    out = []
    try:
        out.append(float(tok))
    except ValueError:
        return out
    if "." not in tok and len(tok) >= 2:
        for k in range(1, len(tok)):
            out.append(float(tok[:k] + "." + tok[k:]))
        out.append(float("0." + tok))
    return out


def printed_decimals(v: float) -> int:
    s = f"{v:.6g}"
    return len(s.split(".")[1]) if "." in s else 0


def parse_is811(pdf: Path, root: Path, ocr_cache: Optional[Path] = None) -> list[dict[str, Any]]:
    cache = ocr_cache or (root / "cache" / "is811_ocr")
    out_rows: list[dict[str, Any]] = []
    for tno, spec in TABLES.items():
        shape = spec["shape"]
        cols = spec["cols"]
        ndim = len(spec["dims"])
        p0, p1 = spec["pages"]
        merged: dict[tuple, dict[str, Any]] = {}
        order: list[tuple] = []
        for pno in range(p0, p1 + 1):
            for src_name, txt in (("pdf_text_layer", pdf_text(pdf, pno)), ("tesseract", tess_text(pdf, pno, cache))):
                for key, toks, raw in parse_rows(txt, ndim):
                    # t in the designation is often OCR-misread; normalise via the Ri/t columns later
                    rec = merged.get(key)
                    if rec is None:
                        # merge rows whose h,b(,c) match and t differs by an OCR slip
                        for k2 in order:
                            if k2[:-1] == key[:-1] and merged[k2]["pdf_page"] == pno and src_name not in merged[k2]["src"]:
                                if abs(k2[-1] - key[-1]) < 1.0 and str(k2[-1])[-1] == str(key[-1])[-1]:
                                    rec = merged[k2]
                                    break
                    if rec is None:
                        rec = {"key": key, "pdf_page": pno, "src": {}}
                        merged[key] = rec
                        order.append(key)
                    rec["src"].setdefault(src_name, toks)
        calib = calibrate(shape, cols, spec["dims"], [merged[k] for k in order])
        for key in order:
            rec = merged[key]
            row = resolve_row(tno, shape, cols, spec["dims"], rec, calib)
            if row:
                out_rows.append(row)
    # de-duplicate designations (keep the row with fewer problems)
    best: dict[str, dict[str, Any]] = {}
    for r in out_rows:
        k = (r["table"], r["designation"])
        cur = best.get(k)
        if cur is None or (str(cur["check"]).startswith("FAIL") and not str(r["check"]).startswith("FAIL")):
            best[k] = r
    rows = list(best.values())
    # a designation misread by one OCR source creates a phantom row that duplicates
    # the values of the real row on the same page: drop it
    real = [r for r in rows if "|" in r["sources"] and "," in r["sources"].split("|")[0]]
    keep = []
    for r in rows:
        single = "," not in r["sources"].split("|")[0]
        if single and str(r["check"]).startswith("FAIL"):
            twin = [x for x in real if x is not r and x["table"] == r["table"] and x["pdf_page"] == r["pdf_page"]
                    and x.get("mass_kg_m") == r.get("mass_kg_m") and x.get("A_mm2") == r.get("A_mm2")]
            if twin:
                continue
        keep.append(r)
    rows = keep
    rows = apply_manual(rows)
    rows.sort(key=lambda r: (int(r["table"]), r["pdf_page"]))
    return rows


def _dims_of(key: tuple, dimnames: tuple) -> dict[str, float]:
    dv = list(key)
    if len(dimnames) == 3:
        return {"h": dv[0], "b": dv[1], "t": dv[2], "c": 0.0}
    return {"h": dv[0], "b": dv[1], "c": dv[2], "t": dv[3]}


def calibrate(shape: str, cols: list[str], dimnames: tuple, recs: list[dict[str, Any]]) -> dict[str, tuple]:
    """Per column: (convention index, scale, tolerance) fitted on cells where both OCR
    sources agree -- absorbs the table's own conventions (corner thinning, x0 datum)."""
    import statistics

    n = len(cols)
    samples: dict[str, list[list[float]]] = {}
    for rec in recs:
        srcs = [v for v in rec["src"].values() if len(v) in (n, n + 1)]
        if len(srcs) < 2:
            continue
        a, b = srcs[0][:n], srcs[1][:n]
        dims = _dims_of(rec["key"], dimnames)
        tcol = dict(zip(cols, a)).get("t")
        if tcol and re.fullmatch(r"\d\.\d\d", tcol) and tcol == dict(zip(cols, b)).get("t"):
            dims["t"] = float(tcol)
        try:
            g = section_props(shape, dims["h"], dims["b"], dims.get("c", 0.0), dims["t"])
        except Exception:
            continue
        ex = expected(shape, dims, g)
        for c, x, y in zip(cols, a, b):
            if x != y or c not in ex or c in ("h", "b", "c", "t", "Ri"):
                continue
            v = float(x)
            samples.setdefault(c, []).append([v / e if e else float("nan") for e in ex[c]])
    out: dict[str, tuple] = {}
    for c, rows in samples.items():
        if len(rows) < 3:
            continue
        best = None
        for j in range(len(rows[0])):
            rs = [r[j] for r in rows if r[j] == r[j] and r[j] > 0]
            if len(rs) < 3:
                continue
            med = statistics.median(rs)
            mad = statistics.median([abs(x / med - 1) for x in rs])
            if best is None or mad < best[2]:
                best = (j, med, mad)
        if best:
            out[c] = (best[0], best[1], max(0.025, 5 * best[2]))
    return out


DERIVED = ("M", "Rxx", "Ryy", "Ruu", "Rvv", "Rvv_min", "Zxx", "Zyy", "Zuu", "Zvv")
DIMCOLS = ("h", "b", "c", "t", "Ri")


def half_ulp_rel(tok: str) -> float:
    try:
        v = float(tok)
    except ValueError:
        return 0.0
    if not v or "." not in tok:
        return 0.0  # integer tokens are mostly OCR fragments ("1" of "1.49"): no rounding credit
    dec = len(tok.split(".")[1])
    return min(0.5 * 10 ** (-dec) / abs(v), 0.13)


def _col_expect(c: str, exp: dict[str, list[float]], calib: dict[str, tuple]) -> tuple[list[float], float]:
    """Check values for column c: the calibrated convention, or every convention when
    the table has too few agreeing rows to calibrate."""
    ev = exp.get(c.replace("Rvv_min", "Rvv"))
    if not ev:
        return [], 0.0
    if c in calib:
        j, scale, ctol = calib[c]
        if j < len(ev):
            return [ev[j] * scale], max(ctol, 0.035)
    return [x for x in ev if x], TOL.get(c, 0.06) + 0.02


def _near(v: float, targets: list[float]) -> Optional[float]:
    return min(targets, key=lambda e0: rel(v, e0)) if targets else None


def align_tokens(toks: list[str], cols: list[str], exp: dict[str, list[float]], calib: dict[str, tuple],
                 other: Optional[list[Optional[str]]] = None) -> tuple[list[Optional[str]], float]:
    """Best mapping of a source's tokens onto the columns (drop junk tokens / pad
    missing cells), scored against the geometric expectation and the other source."""
    from itertools import combinations

    n = len(cols)

    def score(seq: list[Optional[str]]) -> float:
        s = 0.0
        for i, (c, tk) in enumerate(zip(cols, seq)):
            if tk is None:
                continue
            if other is not None and i < len(other) and other[i] == tk:
                s += 1.0
                continue
            es, tol = _col_expect(c, exp, calib)
            if c in DIMCOLS:
                es = [x for x in exp.get(c, []) if x is not None]
                tol = 0.02
            if es:
                if any(rel(v, e) <= tol + half_ulp_rel(tk) for v in decimal_variants(tk) for e in es):
                    s += 1.0 if any(rel(float(tk), e) <= tol + half_ulp_rel(tk) for e in es) else 0.6
        return s

    # variants where a lost decimal point split one number into two tokens ("1" "49" -> "1.49")
    variants: list[tuple[list[str], int]] = [(list(toks), 0)]
    mergeable = [i for i in range(len(toks) - 1)
                 if re.fullmatch(r"\d{1,3}", toks[i]) and re.fullmatch(r"\d{1,3}", toks[i + 1])]
    for i in mergeable:
        v = toks[:i] + [toks[i] + "." + toks[i + 1]] + toks[i + 2:]
        variants.append((v, 1))
        for j in mergeable:
            if j > i + 1:
                jj = j - 1
                variants.append((v[:jj] + [v[jj] + "." + v[jj + 1]] + v[jj + 2:], 2))
    best_all: tuple[float, list[Optional[str]]] = (-1.0, [None] * n)
    for vt, pen in variants:
        seq, sc = _align_core(vt, cols, n, score)
        sc -= 0.3 * pen
        if sc > best_all[0]:
            best_all = (sc, seq)
    return best_all[1], best_all[0]


def _align_core(toks: list[str], cols: list[str], n: int, score) -> tuple[list[Optional[str]], float]:
    from itertools import combinations

    cands: list[list[Optional[str]]] = []
    L = len(toks)
    if L == n:
        cands.append(list(toks))
    if n < L <= n + 2:
        for drop in combinations(range(L), L - n):
            cands.append([t for i, t in enumerate(toks) if i not in drop])
    if n - 2 <= L < n:
        for ins in combinations(range(n), n - L):
            seq: list[Optional[str]] = []
            it = iter(toks)
            for i in range(n):
                seq.append(None if i in ins else next(it))
            cands.append(seq)
    if L == n:
        # one junk token + one missing cell
        for d in range(L):
            rest = [t for i, t in enumerate(toks) if i != d]
            for ins in range(n):
                cands.append(rest[:ins] + [None] + rest[ins:])
    if not cands:
        return [None] * n, 0.0
    best = max(cands, key=score)
    return best, score(best)


def resolve_row(tno: str, shape: str, cols: list[str], dimnames: tuple, rec: dict[str, Any],
                calib: Optional[dict[str, tuple]] = None) -> Optional[dict[str, Any]]:
    calib = calib or {}
    n = len(cols)
    dims = _dims_of(rec["key"], dimnames)
    repairs: list[str] = []
    # thickness: designation vs t / Ri columns (Ri = 1.5 t), standard IS 811 thicknesses
    std_t = [1.25, 1.60, 2.00, 2.30, 2.50, 2.55, 3.15, 4.00, 5.00, 6.00, 8.00]
    votes = [dims["t"]]
    for toks in rec["src"].values():
        for c, tk in zip(cols, toks):
            if c == "t" and re.fullmatch(r"\d\.\d\d", tk):
                votes.append(float(tk))
            if c == "Ri" and re.fullmatch(r"\d\.\d\d", tk):
                votes.append(round(float(tk) / 1.5, 2))
    t_best = max(std_t, key=lambda s: sum(1 for v in votes if abs(v - s) < 0.03))
    if sum(1 for v in votes if abs(v - t_best) < 0.03) >= 2 and abs(t_best - dims["t"]) > 1e-6:
        repairs.append(f"designation t read {dims['t']} -> {t_best} (t / Ri columns)")
        dims["t"] = t_best
    geom = section_props(shape, dims["h"], dims["b"], dims.get("c", 0.0), dims["t"])
    exp = expected(shape, dims, geom)
    # align each OCR source onto the columns
    srcs = list(rec["src"].items())
    aligned: dict[str, list[Optional[str]]] = {}
    solo = {name: align_tokens(toks, cols, exp, calib, None) for name, toks in srcs}
    for name, toks in srcs:
        others = [solo[o][0] for o in solo if o != name]
        seq, sc = align_tokens(toks, cols, exp, calib, others[0] if others else None)
        if sc < solo[name][1]:
            seq, sc = solo[name]
        if sc >= 0.5 * n:
            aligned[name] = seq
    if not aligned:
        return None
    chosen: dict[str, float] = {}
    notes: list[str] = []
    used: dict[str, str] = {}

    def pick(c: str, target, tol: float) -> None:
        targets = target if isinstance(target, list) else ([target] if target is not None else [])
        target = targets[0] if len(targets) == 1 else None
        cells = [(s, seq[i]) for s, seq in aligned.items() for i in [cols.index(c)] if seq[i] is not None]
        toks = [tk for _, tk in cells]
        agree = len(toks) >= 2 and len(set(toks)) == 1
        if targets and target is None and toks:
            target = _near(float(toks[0]), targets)
        if agree:
            v = float(toks[0])
            gross = target is not None and rel(v, target) > max(0.2, 3 * tol) + half_ulp_rel(toks[0])
            chosen[c] = v
            used[c] = "both"
            if gross:
                notes.append(f"{c}={toks[0]} (both OCR) far from check value {target:.4g}")
            return
        best = None
        for s, tk in cells:
            for k, vv in enumerate(decimal_variants(tk)):
                if not targets:
                    err = 0.0 if k == 0 else 1.0
                else:
                    err = min(rel(vv, t0) for t0 in targets) - half_ulp_rel(tk) + (0.002 if k else 0)
                if best is None or err < best[0]:
                    best = (err, vv, s, tk, k)
        if best is not None and targets:
            target = _near(best[1], targets)
        if best is None:
            notes.append(f"{c}: no OCR value")
            return
        err, vv, s, tk, k = best
        if target is None:
            if len(set(toks)) > 1:
                notes.append(f"{c}: OCR disagree {toks}, no check available")
            chosen[c] = vv
            used[c] = s
            return
        if err <= tol:
            chosen[c] = vv
            used[c] = s
            if k:
                repairs.append(f"{c} OCR '{tk}' -> {vv:g} (decimal point restored; check value {target:.4g})")
            elif len(set(toks)) > 1:
                repairs.append(f"{c} OCR {toks} -> {vv:g} (closest to check value {target:.4g})")
        else:
            chosen[c] = float(toks[0]) if toks else vv
            used[c] = cells[0][0]
            notes.append(f"{c}: OCR {toks} vs check value {target:.4g}")

    # 1) dimensions from the (corrected) designation
    for c in cols:
        if c in DIMCOLS:
            v = round(1.5 * dims["t"] + 1e-9, 2) if c == "Ri" else dims.get(c)
            chosen[c] = v
            used[c] = "designation"
    # 2) primary columns against the calibrated geometric model
    for c in cols:
        if c in DIMCOLS or c in DERIVED:
            continue
        es, tol = _col_expect(c, exp, calib)
        pick(c, es, tol)
    # 3) derived columns against exact relations with the chosen primaries
    A = chosen.get("A")
    for c in cols:
        if c not in DERIVED:
            continue
        target, tol = None, 0.012
        if c == "M" and A:
            target = 0.785 * A
        elif c.startswith("R") and A:
            iname = {"Rxx": "Ixx", "Ryy": "Iyy", "Ruu": "Iuu", "Rvv": "Ivv", "Rvv_min": "Ivv"}[c]
            if shape == "equal_angle" and c == "Rxx":
                iname = "Ixx"
            if chosen.get(iname):
                target = math.sqrt(chosen[iname] / A)
        elif c.startswith("Z"):
            target = z_target(shape, c, chosen, dims, geom)
            if target is None:
                target = z_geom_target(shape, c, chosen, exp, calib)
                tol = max(calib[c][2], 0.035) if c in calib else 0.06
        if target is None:
            target, tol = _col_expect(c, exp, calib)
        pick(c, target, tol + 0.004)
    if chosen.get("M") and chosen.get("A") and rel(chosen["M"], 0.785 * chosen["A"]) > 0.02:
        notes.append(f"M {chosen['M']} inconsistent with 0.785A = {0.785 * chosen['A']:.3f}")
    desig_parts = [dims["h"], dims["b"]] + ([dims["c"]] if len(dimnames) == 4 else [])
    desig = " x ".join(f"{int(v)}" if float(v).is_integer() else f"{v:g}" for v in desig_parts)
    desig = f"{desig} x {dims['t']:.2f}"
    ixx_iyy_same = shape == "equal_angle"
    row = {
        "designation": desig,
        "table_id": desig.replace(" ", ""),
        "table": tno,
        "type": SHAPE_NAME[shape],
        "pdf_page": rec["pdf_page"],
        "h": dims["h"], "b": dims["b"], "c": dims.get("c") or None, "t": dims["t"],
        "Ri": chosen.get("Ri", round(1.5 * dims["t"] + 1e-9, 2)),
        "mass_kg_m": chosen.get("M"),
        "A_mm2": _mm(chosen, "A"),
        "Cx": _mm(chosen, "Cx"), "Cy": _mm(chosen, "Cy"),
        "Ixx_mm4": _mm(chosen, "Ixx"),
        "Iyy_mm4": _mm(chosen, "Iyy") if "Iyy" in cols else (_mm(chosen, "Ixx") if ixx_iyy_same else None),
        "Iuu_mm4": _mm(chosen, "Iuu"), "Ivv_mm4": _mm(chosen, "Ivv"), "Ixy_mm4": _mm(chosen, "Ixy"),
        "rxx": _mm(chosen, "Rxx"),
        "ryy": _mm(chosen, "Ryy") if "Ryy" in cols else (_mm(chosen, "Rxx") if ixx_iyy_same else None),
        "ruu": _mm(chosen, "Ruu"), "rvv": _mm(chosen, "Rvv") or _mm(chosen, "Rvv_min"),
        "tan_alpha": chosen.get("tan_alpha") or chosen.get("tan_theta"),
        "Zx": _mm(chosen, "Zxx"),
        "Zy": _mm(chosen, "Zyy") if "Zyy" in cols else (_mm(chosen, "Zxx") if ixx_iyy_same else None),
        "Zu": _mm(chosen, "Zuu"), "Zv": _mm(chosen, "Zvv"),
        "x0": _mm(chosen, "X0"), "J_mm4": _mm(chosen, "J"), "Cw_mm6": _mm(chosen, "Cw"),
        "units": "mm, mm2, mm4, mm3, mm6, kg/m (printed cm units converted)",
        "check": "PASS" if not notes else "FAIL: " + "; ".join(notes),
        "sources": ",".join(sorted(aligned)) + " | " + ";".join(f"{k}:{v}" for k, v in used.items()
                                                               if v not in ("designation", "both")),
        "repairs": "; ".join(repairs),
    }
    return row


PRINTED_TO_CSV = {
    "M": ("mass_kg_m", 1.0), "A": ("A_mm2", 100.0), "Cx": ("Cx", 10.0), "Cy": ("Cy", 10.0),
    "Ixx": ("Ixx_mm4", 1e4), "Iyy": ("Iyy_mm4", 1e4), "Iuu": ("Iuu_mm4", 1e4), "Ivv": ("Ivv_mm4", 1e4),
    "Ixy": ("Ixy_mm4", 1e4), "Rxx": ("rxx", 10.0), "Ryy": ("ryy", 10.0), "Ruu": ("ruu", 10.0), "Rvv": ("rvv", 10.0),
    "tan_alpha": ("tan_alpha", 1.0), "tan_theta": ("tan_alpha", 1.0), "Zxx": ("Zx", 1e3), "Zyy": ("Zy", 1e3),
    "Zuu": ("Zu", 1e3), "Zvv": ("Zv", 1e3), "X0": ("x0", 10.0), "J": ("J_mm4", 1e4), "Cw": ("Cw_mm6", 1e6),
    "h": ("h", 1.0), "t": ("t", 1.0), "Ri": ("Ri", 1.0), "designation": ("designation", None),
}


def apply_manual(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cells read from the 300-dpi page image where both OCR sources fail (is811_manual.json)."""
    import json

    p = Path(__file__).resolve().parent / "is811_manual.json"
    if not p.is_file():
        return rows
    man = json.loads(p.read_text(encoding="utf-8"))

    def put(r: dict[str, Any], fix: dict[str, Any]) -> None:
        for k, v in fix.items():
            if k.startswith("_"):
                continue
            col, f = PRINTED_TO_CSV[k]
            r[col] = v if f is None else round(v * f, 6)
        if r.get("t"):
            r["table_id"] = r["designation"].replace(" ", "")
        r["repairs"] = (r["repairs"] + "; " if r["repairs"] else "") + "manual image read: " + ", ".join(
            f"{k}={v}" for k, v in fix.items() if not k.startswith("_"))
        r["check"] = fix.get("_check", "PASS (manual image read)")

    for r in rows:
        key = f"{r['table']}|{r['designation']}"
        if key in man.get("rename", {}):
            put(r, man["rename"][key])
        elif key in man.get("cells", {}):
            put(r, man["cells"][key])
    return rows


def _mm(chosen: dict[str, float], k: str) -> Optional[float]:
    if k not in chosen or chosen[k] is None:
        return None
    return round(chosen[k] * UNIT.get(k, 1.0), 6)


def z_geom_target(shape: str, c: str, chosen: dict[str, float], exp: dict[str, list[float]],
                  calib: dict[str, tuple]) -> Optional[float]:
    """Z from the calibrated geometric model, scaled by printed/geometric I."""
    evz = exp.get(c)
    if not evz or c not in calib:
        return None
    j, scale, _ = calib[c]
    if j >= len(evz):
        return None
    zg = evz[j] * scale
    if c in ("Zuu", "Zvv"):
        iname = "Iuu" if c == "Zuu" else "Ivv"
    elif (c == "Zxx") == (j < 3):
        iname = "Ixx"
    else:
        iname = "Iyy"
    ig = (exp.get(iname) or exp.get("Ixx") or [None])[0]
    iv = chosen.get(iname) or (chosen.get("Ixx") if iname == "Iyy" and shape == "equal_angle" else None)
    if not ig or not iv:
        return zg
    return zg * iv / ig


def z_target(shape: str, c: str, chosen: dict[str, float], dims: dict[str, float], g: dict[str, float]):
    """Exact Z datums that follow from printed columns (None when the datum is uncertain)."""
    h = dims["h"] / 10
    if c == "Zxx" and chosen.get("Ixx") and shape in ("channel", "lipped_channel", "lipped_zed"):
        return chosen["Ixx"] / (h / 2)
    if c == "Zxx" and chosen.get("Ixx") and shape == "hat" and chosen.get("Cy"):
        return chosen["Ixx"] / max(chosen["Cy"], h - chosen["Cy"])
    return None
