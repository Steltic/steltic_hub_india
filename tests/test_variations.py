"""Design variations module (India): library, metrics, scoring, eligibility and the API flow.

The HR Steel (IS 800) client is monkeypatched: no design server, no LLM, no network. The package
fixtures mirror the real steltic_india layout (design/calc_package.json with drift_table /
seismic_calc / design_status / irregularity / gates, design/cfg_snapshot.json in N-mm,
design/member_schedule.csv with IS 808 designations and length_mm, load_plan.json, report.html,
STATUS.md, conversation.json) as written for the gold-standard package IN_Ex1_SCBF_5levels_Delhi.

    python -m pytest tests/test_variations.py -q
"""
import io, json, os, pathlib, sys, tempfile, time, zipfile
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MOD = ROOT / "steltic_hub" / "catalog" / "steltic_variations"
sys.path.insert(0, str(MOD))
os.environ["VARIATIONS_JOBS"] = tempfile.mkdtemp(prefix="variations-test-")
os.environ["STELTIC_URL"] = "http://127.0.0.1:1"          # never contacted: the client is patched

from variations import library, metrics as mx, scoring, steltic_client as hr, prompts   # noqa: E402
from variations import main as vm                                                       # noqa: E402


# ---------------------------------------------------------------- fixtures
REPORT = """<html><body>
<h2>Chapter 2 — Structural system &amp; load path</h2>
<p>Lateral system per direction: SMRF (IS 800 12.11 + IS 18168), R = 5.0 (IS 1893 Table 9 (i)(d))</p>
<h3>Governing lateral load</h3>
<table><tr><th>dir</th><th>V_B earthquake (kN)</th><th>V_B wind (kN)</th></tr>
<tr><td>X</td><td>1657.8</td><td>401.7</td></tr><tr><td>Y</td><td>1657.8</td><td>502.1</td></tr></table>
<h3>Storey drift (IS 1893 7.11.1.1)</h3><p>every storey within 0.004 h</p>
</body></html>"""

STATUS = "# B -- package\n\n**design_status: COMPLETE** (0 open reason(s))\n"


def calc_package(dc=0.90, dmax=0.00364, status="complete", n_reasons=0, torsion_irregular=False, system="SMF", R=5.0):
    """A calc_package.json shaped like steltic_india's: members/connections with DC, capacity_design,
    seismic_calc, seismic_analysis (modes, VB scaling), drift_table (per storey and direction, IS 1893
    7.11.1.1 limit 0.004), irregularity (Table 5/6), gates, design_status."""
    drift = []
    prof = [0.0022, 0.0031, dmax, 0.0026]
    for i, v in enumerate(prof, 1):
        for d, f in (("X", 1.0), ("Y", 0.9)):
            drift.append({"storey": i, "dir": d, "drift": round(v * f, 6), "limit": 0.004, "value": round(v * f, 6),
                          "dc": round(v * f / 0.004, 4), "ok": v * f <= 0.004, "clause": "IS 1893 7.11.1.1"})
    return {"building": "B", "code": "IS 800:2007 LSD", "unit_system": "N-mm", "stress_unit": "MPa",
            "units": {"force": "N", "length": "mm", "moment": "N-mm", "display": "kN, kN-m, m, mm, MPa"},
            "members": [{"id": "B1", "DC": 0.82, "limit_state": "IS 800:2007 8 (member_check_is800)"},
                        {"id": "C1", "DC": dc, "limit_state": "IS 800:2007 7-9 (member_check_is800)"}],
            "connections": [{"id": "MC-1", "DC": 0.77}],
            "capacity_design": {"system": system, "R": R, "section12": {"clause": "IS 800 12.11"}, "checks": {}},
            "seismic_analysis": {"method": "RSA", "cite": "IS 1893 7.6 / 7.7.1 / 7.7.3 / 7.7.5",
                                 "scale": {"X": {"VB_rsa_kN": 1302.1, "VBbar_kN": 1657.8, "scale": 1.27, "VB_scaled_kN": 1657.8},
                                           "Y": {"VB_rsa_kN": 1356.6, "VBbar_kN": 1657.8, "scale": 1.22, "VB_scaled_kN": 1657.8}},
                                 "modes": [{"mode": 1, "T": 1.115, "Sa_g": 1.22, "mass_x": 0.80, "mass_y": 0.0}]},
            "drift_table": drift,
            "irregularity": {"edition": "IS 1893 (Part 1):2016 + Amd 1 + Amd 2",
                             "torsion": {"ratio": 1.35 if torsion_irregular else 1.03, "band": "<= 1.2", "irregular": torsion_irregular,
                                         "verdict": "torsionally irregular" if torsion_irregular else "regular in torsion",
                                         "clause": "IS 1893 Table 5(i) (Amd 2)"},
                             "soft_storey": {"irregular": False, "clause": "IS 1893 Table 6(i) (Amd 2)"},
                             "reentrant": {"irregular": False, "clause": "IS 1893 Table 5(ii) (Amd 2)"}},
            "seismic_calc": {"system": system, "R": R, "Z": 0.24, "I": 1.2, "zone": "IV", "W_engine_kN": 13476.0, "W_design_kN": 13476.0},
            "load_plan": {"seismic_summary": {"zone": "IV", "Z": 0.24, "I": 1.2, "R": R, "soil": "II", "system": system,
                                              "site": "New Delhi", "Ta_s": 0.62, "Sa_g_x": 2.5, "Ah_x": 0.06,
                                              "W_kN": 13476.0, "VB_x_kN": 1657.8, "VB_y_kN": 1657.8},
                          "retrieval": [], "story_forces_units": "kN"},
            "gates": {"rsa_X": {"ok": True}, "modalmass_X": {"ok": True}, "stability": {"ok": True}, "drift_X": {"ok": True},
                      "drift_Y": {"ok": True}, "wind_defl_X": {"ok": True, "dc": 0.28}, "wind_defl_Y": {"ok": True, "dc": 0.21},
                      "beam_deflection": {"ok": True}, "model_complete": {"ok": True}},
            "design_status": {"status": status, "n_reasons": n_reasons,
                              "reasons": ["IS 18168 5.5 overstrength combination not evaluated"][:n_reasons],
                              "authority": "india_seismic_gates.design_status (spec WP0.2)"}}


LOAD_PLAN = {"jurisdiction": "india", "story_forces_units": "kN",
             "seismic_summary": {"zone": "IV", "Z": 0.24, "I": 1.2, "R": 5.0, "soil": "II", "site": "New Delhi",
                                 "W_kN": 13476.0, "VB_x_kN": 1657.8, "VB_y_kN": 1657.8, "Ah_x": 0.06, "Ta_s": 0.62},
             "wind_summary": {"code": "IS 875 (Part 3):2015", "Vb_mps": 47.0, "Vb_source": "Annex A: New Delhi", "terrain_category": 3,
                              "pz_kNm2": 1.3095, "pd_kNm2": 1.0874, "Kd": 0.9, "cyclone_belt": False,
                              "VB_x_kN": 401.66, "VB_y_kN": 502.07}}

# IS 808 designations carry the mass (kg/m) as the last number; lengths are mm
SCHEDULE = "ele_tag,member,role,section,length_mm,P_comp_kN,M_major_kNm,governing_combo\n" + "\n".join(
    [f"{i},col,smf_col,WPB300X300X88.34,4200.0,596.1,7.6,1.5DL+1.5LL" for i in range(1, 25)]
    + [f"{i},beam,smf_beam,NPB600X220X122.4,9000.0,0.0,210.5,1.2DL+1.2LL+1.2EQ_X" for i in range(25, 61)])

CFG_SNAPSHOT = {"name": "B", "system": "SMF", "jurisdiction": "india", "units": "N-mm", "metric": True, "si_native": True,
                "NX": 6, "NY": 4, "bay_x": 9000.0, "bay_y": 9000.0, "SX": 9000.0, "SY": 9000.0,
                "heights": [4800.0, 4200.0, 4200.0, 4200.0], "steel_grade": "E250 B0",
                "occupancy": {"use": "office", "area_m2": 7776.0}, "_units_converted": True}

CFG = "NX, NY, BAY_M = 6, 4, 9.0\n"


def make_package(building="B", dmax=0.00364, dc=0.90, scale=1.0, brief="4-storey office", extra=None, **calc_kw):
    buf = io.BytesIO()
    sched = SCHEDULE
    if scale != 1.0:
        lines = sched.split("\n")
        sched = "\n".join([lines[0]] + [",".join(x.split(",")[:4] + [f"{float(x.split(',')[4]) * scale:.1f}"] + x.split(",")[5:]) for x in lines[1:]])
    calc = calc_package(dc=dc, dmax=dmax, **calc_kw)
    files = {"report.html": REPORT, "viewer_3d.html": "<html>viewer</html>", "cfg.py": CFG, "STATUS.md": STATUS,
             "load_plan.json": json.dumps(LOAD_PLAN),
             "design/calc_package.json": json.dumps(calc), "design/member_schedule.csv": sched,
             "design/cfg_snapshot.json": json.dumps(CFG_SNAPSHOT),
             "conversation.json": json.dumps([{"role": "user", "content": f"HEAD\n\nBUILDING NAME: {building}\n\nDESIGN BRIEF:\n{brief}\n\nDesign this building now."}])}
    files.update(extra or {})
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in files.items():
            z.writestr(f"{building}/{k}", v)
    return buf.getvalue()


# ---------------------------------------------------------------- library
def test_library_has_ten_categories_and_generic_templates():
    assert len(library.CATEGORIES) == 10
    ids = [c["id"] for c in library.CATEGORIES]
    assert ids == ["problem", "core", "system", "perimeter", "outriggers", "geometry", "members", "bases", "seismic", "detailing"]
    for c in library.CATEGORIES:
        assert c["label"] and c["blurb"] and len(c["templates"]) >= 4
        for title, change, why in c["templates"]:
            assert title and change and why
            # generalised: no grid names / levels / member sizes of one building
            assert not any(tok in change for tok in ("Level 14", "grid C", "W14X730", "HR08"))
            # India design basis only (owner ruling D3): no US standards, units or systems in the templates
            assert not any(tok in (title + change + why) for tok in ("AISC", "ASCE", "AISI", "SDS", "SD1", "psf", "BRBF", "SPSW", "Cd ="))


def test_offline_plan_base_first_then_round_robin():
    plan = library.offline_plan(7, ["core", "system"])
    assert [v["id"] for v in plan] == [f"M{i:03d}" for i in range(1, 8)]
    assert plan[0]["change"] == "" and plan[0]["group"] == "reference"
    groups = [v["group"] for v in plan[1:]]
    assert groups[0] != groups[1] and set(groups) == {"Braced-frame configuration", "Lateral system type"}
    assert len({v["title"] for v in plan}) == 7
    assert len(library.offline_plan(1)) == 1
    assert len(library.offline_plan(300)) <= 1 + sum(len(c["templates"]) for c in library.CATEGORIES)


# ---------------------------------------------------------------- metrics
def test_metrics_read_from_a_package():
    files = mx.unzip(make_package())
    assert "report.html" in files and "design/calc_package.json" in files      # wrapping folder stripped
    m = mx.extract(files)
    # geometry from cfg_snapshot.json (N-mm -> m, m2)
    assert m["n_storeys"] == 4 and m["floor_area_m2"] == 6 * 9 * 4 * 9 * 4 and m["nx"] == 6 and m["ny"] == 4
    assert m["plan_x_m"] == 54.0 and m["height_m"] == pytest.approx(17.4) and m["steel_grade"] == "E250 B0"
    # IS 1893 storey drift from the package's drift table (limit 0.004 h)
    assert m["drift_limit"] == 0.004 and m["drift_max"] == 0.00364 and m["drift_utilisation"] == pytest.approx(0.91)
    assert m["drift_margin"] == pytest.approx(0.09) and m["drift_concentration_ratio"] > 1.0 and m["drift_ok"] is True
    assert m["wind_drift_utilisation"] == pytest.approx(0.28)
    assert (m["W_kN"], m["VB_kN"], m["wind_VB_kN"], m["T1_s"], m["Vb_mps"]) == (13476.0, 1657.8, 502.1, 1.115, 47.0)
    assert m["VB_over_W"] == pytest.approx(0.123, abs=1e-3) and m["Ah"] == 0.06
    assert (m["zone"], m["Z"], m["I"], m["R"], m["soil_type"], m["site"]) == ("IV", 0.24, 1.2, 5.0, "II", "New Delhi")
    assert m["dc_max"] == 0.9 and m["n_over"] == 0 and m["n_checked_members"] == 2
    assert m["design_status"] == "complete" and m["n_open_reasons"] == 0 and m["gates_ok"] is True
    assert m["torsion_ratio"] == 1.03 and m["torsion_irregular"] is False and m["irregularities"] == []
    assert m["n_columns"] == 24 and m["n_beams"] == 36 and m["n_braces"] == 0
    # 24 x WPB300X300X88.34 x 4.2 m + 36 x NPB600X220X122.4 x 9.0 m, kg -> t
    assert m["steel_t"] == pytest.approx((24 * 88.34 * 4.2 + 36 * 122.4 * 9.0) / 1000, abs=0.1)
    assert m["steel_kg_m2"] == pytest.approx(m["steel_t"] * 1000 / m["floor_area_m2"], abs=0.1)
    assert "unweighed_sections" not in m
    assert m["representable"] is True and m["is_mixed"] is False
    assert m["system"] == "SMF" and m["analysis_method"] == "RSA" and m["has_status"] is True


def test_section_masses_follow_is808_designations():
    assert mx.section_mass_kg_m("WPB300X300X88.34") == 88.34
    assert mx.section_mass_kg_m("NPB400X180X57.38") == 57.38
    assert mx.section_mass_kg_m("ISMB600X210X122.6") == 122.6
    # plate box 600x600x36: area = 600^2 - 528^2 mm2, x 0.00785 kg/m per mm2
    assert mx.section_mass_kg_m("BOX600X600X36") == pytest.approx((600 * 600 - 528 * 528) * 0.00785, rel=1e-6)
    assert mx.section_mass_kg_m("CHS165.1X5.9") == pytest.approx(3.1416 * (165.1 - 5.9) * 5.9 * 0.00785, rel=1e-3)
    assert mx.section_mass_kg_m("ISA100X100X10") == pytest.approx(10 * (100 + 100 - 10) * 0.00785, rel=1e-6)
    assert mx.section_mass_kg_m("MB300") is None and mx.section_mass_kg_m("W14X132") is None


def test_metrics_flag_failed_checks_and_unrepresentable_systems():
    m = mx.extract(mx.unzip(make_package(dc=1.07)))
    assert m["dc_max"] == 1.07 and m["n_over"] == 1
    m = mx.extract(mx.unzip(make_package(status="partial", n_reasons=1, torsion_irregular=True)))
    assert m["design_status"] == "partial" and m["n_open_reasons"] == 1 and m["open_reasons"][0].startswith("IS 18168 5.5")
    assert m["torsion_irregular"] is True and m["irregularities"] == ["torsion"]
    r = mx.representability({"system": "SMF + BRBF, R = 5"})
    assert r["representable"] is False and r["is_mixed"] is True and "BRB" in r["not_representable_because"]
    assert mx.representability({"system": "SMF+SCBF"})["representable"] is True and mx.representability({"system": "SMF+SCBF"})["is_mixed"] is True
    for sysname in ("SCBF", "SMRF", "OMRF", "OCBF", "EBF", "OMF+OCBF"):
        assert mx.representability({"system": sysname})["representable"] is True
    assert mx.representability({})["representable"] is None


def test_brief_is_read_back_from_the_agents_first_turn():
    files = mx.unzip(make_package(brief="6-storey office\n54 x 36 m"))
    assert hr.brief_from_package(files) == "6-storey office\n54 x 36 m"
    assert hr.brief_from_package({"conversation.json": b'{"messages":[{"role":"user","content":"plain"}]}'}) == "plain"
    assert hr.brief_from_package({}) == ""


# ---------------------------------------------------------------- scoring
def test_equation_parsing_is_whitelisted():
    assert scoring.looks_like_equation(scoring.DEFAULT_EQUATION)
    assert scoring.looks_like_equation("S = 0.5·drift_margin + 0.5×(1 − steel_t/steel_t_max)")
    assert not scoring.looks_like_equation("cost matters most, then drift")
    assert not scoring.looks_like_equation("S = foo + 1")
    with pytest.raises(scoring.EquationError):
        scoring.compile_equation("__import__('os').system('x')")
    with pytest.raises(scoring.EquationError):
        scoring.compile_equation("S = drift_margin.real")
    with pytest.raises(scoring.EquationError):
        scoring.compile_equation("S = 1 + unknown_metric")
    tree, names = scoring.compile_equation("S = max(0, 1 - modelled_cost/modelled_cost_max) ** 2")
    assert names == {"max", "modelled_cost", "modelled_cost_max"}


def _rows():
    def row(i, **m):
        base = {"steel_t": 1000.0, "floor_area_m2": 10000.0, "drift_utilisation": 0.8, "drift_margin": 0.2,
                "drift_concentration_ratio": 1.2, "n_moment_conn": 200, "n_braces": 0, "dc_max": 0.9, "n_over": 0,
                "design_status": "complete", "n_open_reasons": 0, "gates_ok": True, "gates_failed": [],
                "torsion_irregular": False, "representable": True, "is_mixed": False, "system": "SMF"}
        base.update(m)
        return {"id": f"M{i:03d}", "title": f"v{i}", "status": "done", "metrics": base}
    return [row(1), row(2, steel_t=800.0, n_moment_conn=120), row(3, dc_max=1.05, n_over=2),
            row(4, drift_utilisation=1.1, drift_margin=-0.1), row(5, system="SMF + BRBF", representable=False, is_mixed=True),
            row(6, is_mixed=True, smf_share_pct=18.0, system="SMF+SCBF"), row(7, torsion_irregular=True),
            row(8, design_status="partial", n_open_reasons=2), row(9, wind_comfort_mg=22.0),
            row(11, gates_ok=False, gates_failed=["rsa_X"]), {"id": "M010", "title": "np", "status": "np", "metrics": {}}]


def test_eligibility_reasons_match_the_study_rule():
    rows = _rows()
    out = scoring.score_rows(rows, scoring.DEFAULT_EQUATION, scoring.DEFAULT_ELIGIBILITY, scoring.DEFAULT_RATES)
    by = {r["id"]: r for r in rows}
    assert by["M001"]["eligible"] and by["M002"]["eligible"]
    assert "exceeds 1.0" in by["M003"]["ineligible"][0] and "2 check(s) fail" in by["M003"]["ineligible"][1]
    assert "drift utilisation 1.10 > 1.0" in by["M004"]["ineligible"][0]
    assert "not verifiable" in by["M005"]["ineligible"][0]
    assert "moment-frame share 18% < 25%" in by["M006"]["ineligible"][0]
    assert "torsionally irregular" in by["M007"]["ineligible"][0]
    assert "design_status PARTIAL (2 open reason(s))" in by["M008"]["ineligible"][0]
    assert "wind comfort 22 mg > 15 mg" in by["M009"]["ineligible"][0]
    assert "gate(s) not ok: rsa_X" in by["M011"]["ineligible"][0]
    assert by["M010"]["ineligible"][0] == "not permitted"
    assert out["n_eligible"] == 2 and out["n_scored"] == 2
    # cheaper + fewer connections wins; rates are INR per tonne / connection / brace / m2
    assert by["M002"]["rank"] == 1 and by["M001"]["rank"] == 2 and by["M003"]["rank"] is None
    assert by["M002"]["metrics"]["modelled_cost"] == 800 * 110000 + 120 * 60000
    assert by["M002"]["metrics"]["net_value"] == 10000 * 45000 - by["M002"]["metrics"]["modelled_cost"]
    assert out["rankings"]["modelled_cost"][0] == "M002"


def test_rules_can_be_relaxed_and_equation_reweighted():
    rows = _rows()
    rules = {**scoring.DEFAULT_ELIGIBILITY, "representable_only": False, "wind_comfort_max_mg": None, "smf_share_min_pct": None}
    out = scoring.score_rows(rows, "S = 1 - steel_t/steel_t_max", rules, {"steel_per_t": 1.0})
    by = {r["id"]: r for r in rows}
    assert by["M005"]["eligible"] and by["M006"]["eligible"] and by["M009"]["eligible"]
    assert out["n_eligible"] == 5
    assert by["M002"]["score"] == pytest.approx(0.2) and by["M001"]["score"] == 0.0


def test_missing_metric_is_reported_not_crashed():
    rows = [{"id": "M001", "title": "a", "status": "done", "metrics": {"steel_t": 10.0, "floor_area_m2": 1.0}}]
    scoring.score_rows(rows, "S = drift_margin", {}, {})
    assert rows[0]["eligible"] and rows[0]["score"] is None and "drift_margin not available" in rows[0]["score_note"]


# ---------------------------------------------------------------- prompts
def test_prompts_carry_the_brief_and_the_chosen_mode():
    u = prompts.plan_user("brief text", 12, "categories", ["core"], "")
    assert "brief text" in u and "N = 12" in u and "Braced-frame configuration" in u and "Lateral system type" not in u
    u = prompts.plan_user("b", 5, "instructions", [], "compare cores")
    assert "compare cores" in u
    assert "M001" in prompts.PLAN_SYSTEM and "n_moment_conn" in prompts.READ_SYSTEM
    assert all(n in prompts.SCORE_SYSTEM for n in scoring.METRIC_NAMES)
    # India design basis in the prompts, none of the US one
    assert "IS 1893" in prompts.PLAN_SYSTEM and "IS 800" in prompts.PLAN_SYSTEM and "Table 9" in prompts.PLAN_SYSTEM
    for text in (prompts.PLAN_SYSTEM, prompts.READ_SYSTEM, prompts.SCORE_SYSTEM):
        assert not any(tok in text for tok in ("AISC", "ASCE", "AISI", "kip", "psf"))


# ---------------------------------------------------------------- API flow (HR Steel patched)
@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    calls = {"runs": [], "stops": []}

    def fake_run(base_url, building, brief, on_event, should_stop, resume=False):
        calls["runs"].append((building, brief))
        on_event({"type": "status", "text": f"designing {building}"})
        on_event({"type": "token", "text": "hello "}); on_event({"type": "token", "text": "world"})
        on_event({"type": "tool", "name": "run_python", "title": "run_python: model"})
        on_event({"type": "tool_result", "name": "run_python", "summary": "ok", "ms": 100})
        if building.endswith("M003"):
            on_event({"type": "paused", "reason": "NOT PERMITTED: IS 1893 Table 9 Note 1 -- OMRF in Zone IV"})
            return {"status": "paused", "reason": "NOT PERMITTED: IS 1893 Table 9 Note 1 -- OMRF in Zone IV"}
        if building.endswith("M004"):
            on_event({"type": "error", "text": "engine crashed"})
            return {"status": "failed", "reason": "engine crashed"}
        if should_stop():
            return {"status": "stopped", "reason": "stopped by user"}
        on_event({"type": "done"})
        return {"status": "done", "reason": ""}

    def fake_download(base_url, building):
        if building.endswith("M004"):
            raise hr.DesignError("no package")
        k = int(building[-3:]) if building[-3:].isdigit() else 0
        return make_package(building, scale=1.0 + (k % 3) * 0.1, brief="4-storey office brief")

    monkeypatch.setattr(vm.hr, "run_design", fake_run)
    monkeypatch.setattr(vm.hr, "download", fake_download)
    monkeypatch.setattr(vm.hr, "healthy", lambda url: True)
    monkeypatch.setattr(vm.hr, "stop", lambda url, b: calls["stops"].append(b))
    vm.llm.set_creds({"model": "MOCK", "base_url": "x", "api_key": "k"})
    c = TestClient(vm.app)
    c.calls = calls
    return c


def _wait_idle(client, project, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not client.get(f"/api/project/{project}").json()["running"]:
            return
        time.sleep(0.05)
    raise AssertionError("study did not finish")


def test_api_flow_plan_run_score_select(client):
    p = "Study1"
    assert client.get("/api/me").json()["has_creds"] is True
    # base brief: from the project's design (patched download) or typed
    d = client.get(f"/api/project/{p}/base/from-design").json()
    assert d["brief"] == "4-storey office brief"
    r = client.post(f"/api/project/{p}/base", json={"brief": "4-storey office, SMRF", "source": "typed"})
    assert r.status_code == 200 and r.json()["base_brief"] == "4-storey office, SMRF"
    # plan without an LLM: library, note says so in instructions mode
    r = client.post(f"/api/project/{p}/plan", json={"n": 5, "mode": "categories", "categories": ["core", "system"]})
    assert r.status_code == 200 and len(r.json()["plan"]) == 5 and "library" in r.json()["plan_source"]
    assert client.post(f"/api/project/{p}/plan", json={"n": 5, "mode": "categories", "categories": []}).status_code == 400
    r = client.post(f"/api/project/{p}/plan", json={"n": 5, "mode": "instructions", "instructions": "compare cores"})
    assert "without an LLM" in r.json()["plan_note"]
    # edit the plan
    plan = r.json()["plan"]
    plan[1]["title"] = "Edited title"
    r = client.put(f"/api/project/{p}/plan", json={"plan": plan})
    assert r.json()["plan"][1]["title"] == "Edited title" and "edited" in r.json()["plan_source"]
    # run all pending
    r = client.post(f"/api/project/{p}/run", json={})
    assert r.status_code == 200 and r.json()["ids"] == ["M001", "M002", "M003", "M004", "M005"]
    assert client.post(f"/api/project/{p}/run", json={}).status_code in (400, 409)     # already running or nothing left
    _wait_idle(client, p)
    st = client.get(f"/api/project/{p}").json()
    by = {r["id"]: r for r in st["rows"]}
    assert by["M001"]["status"] == "done" and by["M002"]["status"] == "done" and by["M005"]["status"] == "done"
    assert by["M003"]["status"] == "np" and "NOT PERMITTED" in by["M003"]["reason"]
    assert by["M004"]["status"] == "failed" and by["M004"]["reason"] == "engine crashed"
    assert by["M001"]["metrics"]["steel_t"] > 0 and by["M001"]["files"] == ["package.zip", "report.html", "viewer_3d.html"]
    assert by["M001"]["metrics"]["n_moment_conn_estimated"] is True          # no LLM: estimated and flagged
    # the variation brief carries the base + the change block, M001 the base only
    runs = dict(client.calls["runs"])
    assert runs[f"{p}_M001"] == "4-storey office, SMRF"
    assert runs[f"{p}_M002"].startswith("4-storey office, SMRF\n\n=== DESIGN VARIATION M002: Edited title ===")
    # events are re-attachable after the fact
    ev = client.get(f"/api/project/{p}/events?since=0").text
    assert '"type": "start"' in ev and '"type": "finished"' in ev and "hello world" in ev
    # files
    assert client.get(f"/api/project/{p}/file/M001/report.html").status_code == 200
    assert client.get(f"/api/project/{p}/file/M004/package.zip").status_code == 404
    assert client.get(f"/api/project/{p}/file/../M001/report.html").status_code in (400, 404)
    # score: default, typed, words without LLM, bad
    r = client.post(f"/api/project/{p}/score", json={"text": ""})
    assert r.status_code == 200
    sc = r.json()["scoring"]
    assert sc["equation_source"] == "default" and sc["summary"]["n_eligible"] == 3 and sc["summary"]["n_scored"] == 3
    ranked = sorted([x for x in r.json()["rows"] if x["rank"]], key=lambda x: x["rank"])
    assert ranked[0]["metrics"]["modelled_cost"] <= ranked[-1]["metrics"]["modelled_cost"]
    r = client.post(f"/api/project/{p}/score", json={"text": "S = 0.5*drift_margin + 0.5*(1 - steel_t/steel_t_max)",
                                                     "rules": {"drift_utilisation_max": 0.5}, "rates": {"steel_per_t": 95000}})
    assert r.status_code == 200 and r.json()["scoring"]["equation_source"] == "typed"
    assert r.json()["scoring"]["summary"]["n_eligible"] == 0 and r.json()["scoring"]["rates"]["steel_per_t"] == 95000.0
    assert client.post(f"/api/project/{p}/score", json={"text": "cost matters most"}).status_code == 400
    assert client.post(f"/api/project/{p}/score", json={"text": "S = 1 + nope"}).status_code == 400
    client.post(f"/api/project/{p}/score", json={"text": ""})
    # the study folder holds the results for people and other modules
    sd = vm.study_dir(p)
    assert (sd / "results.csv").is_file() and (sd / "results.json").is_file() and (sd / "state.json").is_file()
    # select
    r = client.post(f"/api/project/{p}/select", json={"ids": ["M002", "M001"], "note": "two lightest"})
    assert r.status_code == 200
    sel = r.json()["selection"]["selected"]
    assert [s["id"] for s in sel] == ["M002", "M001"] and all(s["rank"] for s in sel)
    assert (sd / "selected" / pathlib.Path(sel[0]["package"]).name).is_file()
    rec = json.loads((vm.JOBS / p / "selected_variations.json").read_text())
    assert rec["note"] == "two lightest" and rec["selected"][0]["package"].startswith("variations/selected/")
    assert client.post(f"/api/project/{p}/select", json={"ids": ["M099"]}).status_code == 400
    assert client.get(f"/api/project/{p}").json()["selection"]["ids"] == ["M002", "M001"]
    # re-run only what is not done
    r = client.post(f"/api/project/{p}/run", json={})
    assert r.json()["ids"] == ["M003", "M004"]
    _wait_idle(client, p)
    # drop a result
    assert client.delete(f"/api/project/{p}/variation/M005").status_code == 200
    assert client.get(f"/api/project/{p}").json()["rows"][4]["status"] == "pending"


def test_run_refuses_without_plan_or_creds(client):
    p = "Study2"
    assert client.post(f"/api/project/{p}/run", json={}).status_code == 400
    client.post(f"/api/project/{p}/base", json={"brief": "x"})
    client.post(f"/api/project/{p}/plan", json={"n": 2, "mode": "auto"})
    vm.llm.set_creds({})
    r = client.post(f"/api/project/{p}/run", json={})
    assert r.status_code == 400 and "connection" in r.json()["detail"]


def test_stop_ends_the_study_early(client, monkeypatch):
    p = "Study3"
    client.post(f"/api/project/{p}/base", json={"brief": "x"})
    client.post(f"/api/project/{p}/plan", json={"n": 6, "mode": "auto"})
    gate = {"go": False}
    orig = vm.hr.run_design

    def slow_run(base_url, building, brief, on_event, should_stop, resume=False):
        for _ in range(200):
            if should_stop():
                return {"status": "stopped", "reason": "stopped by user"}
            time.sleep(0.01)
        return orig(base_url, building, brief, on_event, should_stop, resume)
    monkeypatch.setattr(vm.hr, "run_design", slow_run)
    client.post(f"/api/project/{p}/run", json={})
    time.sleep(0.1)
    r = client.post(f"/api/project/{p}/stop")
    assert r.json()["running"] is True and client.calls["stops"] == [f"{p}_M001"]
    _wait_idle(client, p)
    rows = client.get(f"/api/project/{p}").json()["rows"]
    assert rows[0]["status"] == "stopped" and all(x["status"] == "pending" for x in rows[1:])
    assert "skipped" in rows[1]["reason"]


def test_llm_paths_plan_read_and_interpret(client, monkeypatch):
    """With a model: the plan comes from it (validated), the report is read for the values the
    package does not state, and a description in words becomes an equation shown back."""
    p = "Study4"
    canned = {"plan": json.dumps({"variations": [
        {"id": "M001", "title": "Base design as briefed", "group": "reference", "change": "", "why": "Reference"},
        {"id": "M002", "title": "Two-storey X braces", "group": "core", "change": "Configure the braced bays as two-storey X.", "why": "tonnage"},
        {"id": "M003", "title": "Two-storey X braces", "group": "core", "change": "Configure the braced bays as two-storey X.", "why": "duplicate"},
        {"id": "M004", "title": "SCBF only", "group": "system", "change": "Use SCBF alone (R = 4.5).", "why": "IS 18168 gate"}]}),
        "read": json.dumps({"system_X": "SMRF R 5.0", "system_Y": "SMRF R 5.0", "is_dual": False, "n_moment_conn": 96, "smf_share_pct": None,
                            "wind_comfort_mg": 9.5, "not_permitted": False, "np_clause": "", "np_list": [], "notes": ""}),
        "score": '```json\n{"equation": "S = 0.7*(1 - modelled_cost/modelled_cost_max) + 0.3*drift_margin", "explanation": "cost first"}\n```'}
    seen = []

    def fake_chat(system, user, **kw):
        seen.append(system[:40])
        if system.startswith("You are a senior structural engineer"):
            return canned["plan"]
        if system.startswith("You read an IS 800 steel-design report"):
            return canned["read"]
        return canned["score"]
    monkeypatch.setattr(vm.llm, "available", lambda: True)
    monkeypatch.setattr(vm.llm, "chat", fake_chat)
    vm.llm.set_creds({"model": "test-model", "base_url": "http://x", "api_key": "k"})
    client.post(f"/api/project/{p}/base", json={"brief": "4-storey office"})
    r = client.post(f"/api/project/{p}/plan", json={"n": 4, "mode": "instructions", "instructions": "cores vs frames"})
    d = r.json()
    assert d["plan_source"] == "test-model" and [v["id"] for v in d["plan"]] == ["M001", "M002", "M003"]   # duplicate dropped
    assert d["plan"][2]["title"] == "SCBF only" and "3 distinct" in d["plan_note"]
    client.post(f"/api/project/{p}/run", json={"ids": ["M001"]})
    _wait_idle(client, p)
    row = client.get(f"/api/project/{p}").json()["rows"][0]
    assert row["status"] == "done" and row["metrics"]["n_moment_conn"] == 96 and "n_moment_conn_estimated" not in row["metrics"]
    assert row["metrics"]["wind_comfort_mg"] == 9.5 and row["llm_read"]["system_X"] == "SMRF R 5.0"
    r = client.post(f"/api/project/{p}/score", json={"text": "cost matters most, then drift margin"})
    assert r.status_code == 200
    sc = r.json()["scoring"]
    assert sc["equation"] == "0.7*(1 - modelled_cost/modelled_cost_max) + 0.3*drift_margin"
    assert sc["equation_source"] == "interpreted by test-model" and sc["explanation"] == "cost first"
    assert sc["source_text"] == "cost matters most, then drift margin"
    assert r.json()["rows"][0]["score"] == pytest.approx(0.7 * 0 + 0.3 * 0.09, abs=1e-3)
    assert any(s.startswith("You turn a designer") for s in seen)


def test_state_write_survives_windows_replace_race(monkeypatch):
    """On Windows os.replace raises PermissionError while the UI holds state.json open for a poll; save_state must
    retry rather than lose the write (the study thread's final status is what the stop test reads back)."""
    real = os.replace; calls = {"n": 0}
    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(5, "Access is denied")
        return real(src, dst)
    monkeypatch.setattr(os, "replace", flaky)
    vm.update_state("RaceStudy", lambda st: st.__setitem__("base_brief", "x"))
    assert calls["n"] == 3 and vm.load_state("RaceStudy")["base_brief"] == "x"
