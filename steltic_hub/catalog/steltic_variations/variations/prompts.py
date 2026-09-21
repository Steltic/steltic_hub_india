"""Prompts. Kept short and literal: the model is asked for JSON with named keys, nothing else."""
from __future__ import annotations
import json
from .library import examples_for_prompt
from .scoring import METRICS, METRIC_NAMES

PLAN_SYSTEM = """You are a senior structural engineer planning a design-variation study of a steel building in India.
The building is described by a BASE BRIEF. You must propose N design variations. Each variation is designed as a
separate job by an IS 800:2007 steel-design agent (loads IS 875 Parts 1-5, seismic IS 1893 (Part 1):2016 + Amd 1/2,
ductile detailing IS 800 Section 12 and IS 18168:2023) that reads brief text only, so every variation must be
expressible as a short instruction that changes ONLY what it states relative to the base brief and keeps
everything else identical. Use SI units (m, mm, kN, kN/m2, MPa) and IS terms (zone II-V, Z, I, R from IS 1893
Table 9, soil type I-III, Vb, IS 2062 grades, IS 808 sections). Never reference grid names, levels or member sizes
that the base brief does not define. Propose only systems IS 1893 Table 9 covers (SMRF, SCBF, EBF, OMRF / OCBF in
Zone II only) -- no buckling-restrained braces, plate shear walls, composite walls or damping devices, which have no
Indian design basis. No duplicates. Each variation carries a one-line 'why' (what question it answers).
M001 is always the base brief unchanged (title 'Base design as briefed', group 'reference', change '').
Where a variation is likely NOT PERMITTED by IS 1893 / IS 800 / IS 18168 (Table 9 Note 1 in Zones III-V, the
15 m gates, dynamic-analysis requirements), still include it when it answers a question, and say in 'why' that the
design agent should confirm the clause.
Return ONLY JSON: {"variations": [{"id": "M001", "title": "...", "group": "...", "change": "...", "why": "..."}]}
Ids are M001, M002, ... in order. 'group' is the category label the variation belongs to."""


def plan_user(base_brief: str, n: int, mode: str, categories: list[str], instructions: str) -> str:
    parts = [f"BASE BRIEF:\n{base_brief.strip()}\n", f"N = {n} variations in total (including M001).\n"]
    if mode == "categories":
        parts.append("Draw the variations ONLY from these categories, spread evenly across them:\n" + examples_for_prompt(categories))
    elif mode == "instructions":
        parts.append("The designer's instructions for the study:\n" + instructions.strip() + "\n\n"
                     "For reference, the kinds of variation such studies use:\n" + examples_for_prompt(None, per=2))
    else:
        parts.append("Choose freely across all categories to give the widest useful spread for this building:\n" + examples_for_prompt(None, per=3))
    parts.append("\nReturn the JSON now.")
    return "\n".join(parts)


READ_SYSTEM = """You read an IS 800 steel-design report (and its STATUS.md) and answer with numbers that are STATED in
it. Never guess: use null when the report does not state a value. Return ONLY JSON with these keys:
{"system_X": "lateral system in the X direction with its IS 1893 Table 9 R (short)", "system_Y": "...",
 "is_dual": true/false (moment frames AND braced frames share a direction),
 "n_moment_conn": integer or null (total number of moment connections in the building; count from the frames'
   bays x levels x 2 ends when the report gives those, else null),
 "smf_share_pct": number or null (moment frames' share of base shear in a mixed system),
 "wind_comfort_mg": number or null (peak wind acceleration in milli-g, if reported),
 "not_permitted": true/false (the report says the system is NOT PERMITTED -- e.g. IS 1893 Table 9 Note 1 -- or
   the design_status is not COMPLETE because of a code gate),
 "np_clause": "clause cited" or "",
 "np_list": ["other scope items the report flags as not permitted, not verified or TODO(verify)"],
 "notes": "one sentence on anything a reviewer must know"}"""


def read_user(excerpt: str, metrics: dict) -> str:
    known = {k: metrics.get(k) for k in ("system", "system_declared", "design_status", "R", "zone", "drift_utilisation",
                                         "drift_limit", "VB_kN", "n_storeys", "plan_x_m", "plan_y_m", "n_beams",
                                         "n_columns", "n_braces") if metrics.get(k) is not None}
    return f"Already extracted from the package (do not contradict):\n{json.dumps(known)}\n\nREPORT EXCERPT:\n{excerpt}\n\nReturn the JSON now."


SCORE_SYSTEM = """You turn a designer's wording about how design variations should be ranked into ONE score
equation. Higher score = better. Use ONLY these metric names (a higher value of each is not necessarily better --
read the description) and, for normalisation, <name>_max or <name>_min taken over the eligible variations:
""" + "\n".join(f"- {n}: {d} [{u}]" for n, d, u in METRICS) + """
Allowed operators: + - * / ** parentheses, min(), max(), abs(), sqrt(). Numbers only as constants.
Return ONLY JSON: {"equation": "S = ...", "explanation": "one or two sentences on the weights chosen"}"""


def score_user(text: str, default_equation: str) -> str:
    return (f"The default equation is:\n{default_equation}\n\nThe designer wrote:\n{text.strip()}\n\n"
            "If the text is already an equation, return it normalised. Otherwise translate the intent into an "
            "equation with weights that sum to about 1. Return the JSON now.")
