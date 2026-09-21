"""Scores, eligibility and rankings.

The score is a formula over the metrics of each variation. The default is the study's own:

    S = 0.35*(1 - modelled_cost/modelled_cost_max) + 0.20*drift_margin
      + 0.15*(1 - drift_concentration_ratio/2.0) + 0.15*(net_value/net_value_max)
      + 0.15*(1 - n_moment_conn/n_moment_conn_max)

Any metric name may be used; `<name>_max` / `<name>_min` are taken over the eligible rows. The
expression is evaluated with a small AST walker -- names, numbers, + - * / ** unary minus, min,
max, abs, sqrt, parentheses -- never with eval().

India edition: the metrics are SI (t, kg/m², m², kN, m/s) and read from the IS 800 package
(design_status, IS 1893 drift table, Table 5/6 irregularities, VB / W); the cost rates are in INR.
"""
from __future__ import annotations
import ast, math, re

DEFAULT_EQUATION = ("S = 0.35*(1 - modelled_cost/modelled_cost_max) + 0.20*drift_margin "
                    "+ 0.15*(1 - drift_concentration_ratio/2.0) + 0.15*(net_value/net_value_max) "
                    "+ 0.15*(1 - n_moment_conn/n_moment_conn_max)")

METRICS = [
    # (name, description, unit)
    ("modelled_cost", "steel tonnage x rate + moment connections x rate + braces x rate", "INR"),
    ("revenue_proxy", "gross floor area x revenue rate", "INR"),
    ("net_value", "revenue_proxy - modelled_cost", "INR"),
    ("steel_t", "structural steel from the member schedule (IS 808 masses, lengths in mm)", "t"),
    ("steel_kg_m2", "steel weight per square metre of gross floor area", "kg/m²"),
    ("floor_area_m2", "gross floor area, all levels", "m²"),
    ("drift_utilisation", "max storey drift / IS 1893 7.11.1.1 limit (0.004 h), from the package's drift table", "-"),
    ("drift_margin", "1 - drift_utilisation", "-"),
    ("drift_max", "max storey drift ratio (edges, 7.8.2 eccentricity)", "-"),
    ("drift_concentration_ratio", "max storey drift / mean storey drift (worst direction)", "-"),
    ("wind_drift_utilisation", "max wind sway / IS 800 Table 6 limit", "-"),
    ("dc_max", "highest member or connection demand/capacity ratio (IS 800)", "-"),
    ("n_over", "members or connections with D/C > 1.0", "count"),
    ("n_open_reasons", "open reasons in design_status (0 = COMPLETE)", "count"),
    ("n_moment_conn", "moment connections (framework count, LLM-read when not in the package)", "count"),
    ("n_braces", "braces in the member schedule", "count"),
    ("n_columns", "columns in the member schedule", "count"),
    ("n_beams", "beams in the member schedule", "count"),
    ("VB_kN", "design seismic base shear VB (larger direction, after 7.7.3 scaling)", "kN"),
    ("W_kN", "seismic weight W (IS 1893 7.4)", "kN"),
    ("VB_over_W", "VB / W (the design horizontal seismic coefficient realised)", "-"),
    ("Ah", "design horizontal seismic coefficient (Z/2)(I/R)(Sa/g)", "-"),
    ("T1_s", "fundamental period", "s"),
    ("wind_VB_kN", "larger wind base shear (IS 875 Part 3)", "kN"),
    ("Vb_mps", "basic wind speed Vb", "m/s"),
    ("torsion_ratio", "IS 1893 Table 5(i) torsion ratio (max edge / average displacement)", "-"),
    ("R", "response reduction factor (IS 1893 Table 9)", "-"),
    ("Z", "zone factor (IS 1893 Table 3)", "-"),
    ("I", "importance factor (IS 1893 Table 8)", "-"),
    ("scwb_ratio", "strong-column / weak-beam ratio (reported)", "-"),
    ("smf_share_pct", "moment-frame share of base shear (mixed systems)", "%"),
    ("wind_comfort_mg", "peak wind acceleration if reported", "milli-g"),
]
METRIC_NAMES = [m[0] for m in METRICS]

DEFAULT_ELIGIBILITY = {
    "require_done": True,
    "require_checks_pass": True,             # dc_max <= 1.0 and n_over == 0
    "require_design_complete": True,         # design_status COMPLETE (0 open reasons) -- the India gate authority
    "drift_utilisation_max": 1.0,            # IS 1893 7.11.1.1
    "smf_share_min_pct": 25.0,                # mixed moment-frame + braced systems only; null share = not checked
    "require_gates_pass": True,               # every analysis gate in the package ok (RSA scaling, drift, stability ...)
    "no_torsional_irregularity": True,        # IS 1893 Table 5(i): torsion ratio within the band
    "wind_comfort_max_mg": 15.0,              # only when reported
    "representable_only": True,               # an IS 800 Section 12 frame type the Nonlinear tools can model
}

# INR. Rates are placeholders for the study, editable on the Score tab: fabricated and erected
# structural steel per tonne, per moment connection, per brace, and a revenue proxy per m² of
# gross floor area.
DEFAULT_RATES = {"steel_per_t": 110000.0, "per_moment_conn": 60000.0, "per_brace": 25000.0,
                 "revenue_per_m2": 45000.0}


class EquationError(ValueError):
    pass


_ALLOWED_FUNCS = {"min": min, "max": max, "abs": abs, "sqrt": math.sqrt, "log": math.log, "exp": math.exp}


def normalise_equation(text: str) -> str:
    """'S = ...' or a bare expression -> the expression. Accepts the unicode dot and x for times."""
    t = (text or "").strip()
    t = t.replace("·", "*").replace("×", "*").replace("−", "-").replace("–", "-").replace("^", "**")
    t = re.sub(r"^\s*[A-Za-z_]\w*\s*=\s*", "", t)          # strip "S ="
    return t.strip()


def looks_like_equation(text: str) -> bool:
    t = normalise_equation(text)
    if not t or "\n" in t.strip():
        return False
    try:
        tree = ast.parse(t, mode="eval")
    except SyntaxError:
        return False
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    ok = set(METRIC_NAMES) | {f"{m}_max" for m in METRIC_NAMES} | {f"{m}_min" for m in METRIC_NAMES} | set(_ALLOWED_FUNCS)
    return bool(names) and names <= ok


def compile_equation(text: str):
    """-> (expr_ast, names_used). Raises EquationError with a readable message."""
    t = normalise_equation(text)
    if not t:
        raise EquationError("empty equation")
    try:
        tree = ast.parse(t, mode="eval")
    except SyntaxError as e:
        raise EquationError(f"not a valid expression: {e.msg} at position {e.offset}")
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
                raise EquationError("only min(), max(), abs(), sqrt(), log(), exp() may be called")
        elif isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Load,
                               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd, ast.IfExp,
                               ast.Compare, ast.Gt, ast.Lt, ast.GtE, ast.LtE, ast.Eq, ast.NotEq, ast.BoolOp,
                               ast.And, ast.Or)):
            continue
        else:
            raise EquationError(f"'{type(node).__name__}' is not allowed in a score equation")
    base = set(METRIC_NAMES) | set(_ALLOWED_FUNCS)
    for n in names:
        stem = n[:-4] if n.endswith(("_max", "_min")) else n
        if n not in base and stem not in METRIC_NAMES:
            raise EquationError(f"unknown metric '{n}' (metrics: {', '.join(METRIC_NAMES)})")
    return tree, names


def _eval(node, env: dict):
    if isinstance(node, ast.Expression):
        return _eval(node.body, env)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise EquationError("only numbers are allowed as constants")
    if isinstance(node, ast.Name):
        if node.id in _ALLOWED_FUNCS:
            return _ALLOWED_FUNCS[node.id]
        v = env.get(node.id)
        if v is None:
            raise KeyError(node.id)
        return float(v)
    if isinstance(node, ast.BinOp):
        a, b = _eval(node.left, env), _eval(node.right, env)
        if isinstance(node.op, ast.Add): return a + b
        if isinstance(node.op, ast.Sub): return a - b
        if isinstance(node.op, ast.Mult): return a * b
        if isinstance(node.op, ast.Div): return a / b if b != 0 else float("nan")
        if isinstance(node.op, ast.Pow): return a ** b
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, env)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.Call):
        f = _ALLOWED_FUNCS[node.func.id]
        return float(f(*[_eval(a, env) for a in node.args]))
    if isinstance(node, ast.IfExp):
        return _eval(node.body, env) if _eval(node.test, env) else _eval(node.orelse, env)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, env)
        for op, comp in zip(node.ops, node.comparators):
            right = _eval(comp, env)
            ok = {ast.Gt: left > right, ast.Lt: left < right, ast.GtE: left >= right, ast.LtE: left <= right,
                  ast.Eq: left == right, ast.NotEq: left != right}[type(op)]
            if not ok:
                return 0.0
            left = right
        return 1.0
    if isinstance(node, ast.BoolOp):
        vals = [_eval(v, env) for v in node.values]
        return float(all(vals) if isinstance(node.op, ast.And) else any(vals))
    raise EquationError(f"unsupported expression element {type(node).__name__}")


def cost_metrics(m: dict, rates: dict) -> dict:
    r = {**DEFAULT_RATES, **(rates or {})}
    out = dict(m)
    tonnes, mc, br, area = m.get("steel_t"), m.get("n_moment_conn"), m.get("n_braces"), m.get("floor_area_m2")
    if tonnes is not None:
        out["modelled_cost"] = (tonnes * r["steel_per_t"] + (mc or 0) * r["per_moment_conn"]
                                + (br or 0) * r["per_brace"])
    else:
        out["modelled_cost"] = None
    out["revenue_proxy"] = area * r["revenue_per_m2"] if area is not None else None
    out["net_value"] = (out["revenue_proxy"] - out["modelled_cost"]
                        if out["revenue_proxy"] is not None and out["modelled_cost"] is not None else None)
    return out


def eligibility(row: dict, rules: dict) -> list[str]:
    """-> reasons the row is NOT eligible (empty list = eligible). Missing numbers count as 'not
    shown', which fails only the rules that need them."""
    r = {**DEFAULT_ELIGIBILITY, **(rules or {})}
    m = row.get("metrics") or {}
    why = []
    if r.get("require_done") and row.get("status") != "done":
        why.append({"failed": "run failed", "np": "not permitted", "paused": "run paused", "pending": "not designed yet",
                    "running": "still running"}.get(row.get("status"), f"status {row.get('status')}"))
    if r.get("require_checks_pass"):
        if m.get("dc_max") is not None and m["dc_max"] > 1.0:
            why.append(f"a member/connection D/C of {m['dc_max']:.2f} exceeds 1.0")
        if (m.get("n_over") or 0) > 0:
            why.append(f"{m['n_over']} check(s) fail")
    du = m.get("drift_utilisation")
    if r.get("drift_utilisation_max") is not None and du is not None and du > r["drift_utilisation_max"]:
        why.append(f"drift utilisation {du:.2f} > {r['drift_utilisation_max']}")
    if r.get("require_design_complete") and row.get("status") == "done":
        ds = str(m.get("design_status") or "")
        if ds and ds != "complete":
            n = m.get("n_open_reasons")
            why.append(f"design_status {ds.upper()}" + (f" ({n} open reason(s))" if n else ""))
    share = m.get("smf_share_pct")
    if r.get("smf_share_min_pct") is not None and (m.get("is_mixed") or m.get("is_dual")) and share is not None and share < r["smf_share_min_pct"]:
        why.append(f"moment-frame share {share:.0f}% < {r['smf_share_min_pct']:.0f}%")
    if r.get("require_gates_pass") and row.get("status") == "done" and m.get("gates_ok") is False:
        why.append("analysis gate(s) not ok: " + ", ".join(m.get("gates_failed") or [])[:120])
    if r.get("no_torsional_irregularity") and m.get("torsion_irregular") is True:
        why.append("torsionally irregular (IS 1893 Table 5(i))")
    wc = m.get("wind_comfort_mg")
    if r.get("wind_comfort_max_mg") is not None and wc is not None and wc > r["wind_comfort_max_mg"]:
        why.append(f"wind comfort {wc:.0f} mg > {r['wind_comfort_max_mg']:.0f} mg")
    if r.get("representable_only") and m.get("representable") is False:
        why.append("not verifiable with the current nonlinear tools (" + (m.get("system") or "system") + ")")
    return why


def score_rows(rows: list[dict], equation: str, rules: dict, rates: dict) -> dict:
    """Annotate every row with cost metrics, eligibility, score and ranks."""
    tree, names = compile_equation(equation)
    for row in rows:
        row["metrics"] = cost_metrics(row.get("metrics") or {}, rates)
        row["ineligible"] = eligibility(row, rules)
        row["eligible"] = not row["ineligible"]
    elig = [r for r in rows if r["eligible"]]
    agg: dict = {}
    for n in names:
        if n.endswith("_max") or n.endswith("_min"):
            stem, fn = n[:-4], (max if n.endswith("_max") else min)
            vals = [r["metrics"].get(stem) for r in elig if isinstance(r["metrics"].get(stem), (int, float))]
            agg[n] = fn(vals) if vals else None
    for row in rows:
        row["score"] = None
        row["score_note"] = ""
        if not row["eligible"]:
            continue
        env = {**{k: v for k, v in row["metrics"].items() if isinstance(v, (int, float))}, **agg}
        try:
            s = _eval(tree, env)
            row["score"] = None if (s != s) else round(float(s), 4)     # NaN -> None
            if row["score"] is None:
                row["score_note"] = "division by zero in the equation"
        except KeyError as e:
            row["score_note"] = f"{e.args[0]} not available for this variation"
        except Exception as e:
            row["score_note"] = f"{type(e).__name__}: {e}"
    scored = sorted([r for r in rows if r["score"] is not None], key=lambda r: -r["score"])
    for i, r in enumerate(scored, 1):
        r["rank"] = i
    for r in rows:
        r.setdefault("rank", None)
    rankings = {}
    for key, reverse in (("modelled_cost", False), ("steel_kg_m2", False), ("drift_margin", True),
                         ("drift_concentration_ratio", False), ("n_moment_conn", False), ("net_value", True)):
        have = [r for r in rows if isinstance(r["metrics"].get(key), (int, float))]
        rankings[key] = [r["id"] for r in sorted(have, key=lambda r: r["metrics"][key], reverse=reverse)]
    return {"equation": normalise_equation(equation), "names": sorted(names), "aggregates": agg,
            "rankings": rankings, "n_eligible": len(elig), "n_scored": len(scored)}
