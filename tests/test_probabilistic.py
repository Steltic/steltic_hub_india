"""Probabilistic analysis module: the sampler, the D/C recomputation with the design's own
capacities (both bases), the statistics, and the API flow.

The analyses themselves need openseespy and the HR Steel engine (the Nonlinear module's
environment); here the worker is replaced by a stub script that writes canned demand envelopes
derived from the sampled inputs, so the whole server path -- package, probe, spec, workers,
events, results, basis, report -- runs without either.

    python -m pytest tests/test_probabilistic.py -q
"""
import io, json, os, pathlib, sys, tempfile, textwrap, time, zipfile
import pytest
# The module's statistics run on numpy, which the hub's own environment does not carry (the module has its own
# env). Skip -- do not break collection -- where numpy is absent; the Windows self-test installs it alongside pytest.
np = pytest.importorskip("numpy", reason="numpy is a Probabilistic-analysis dependency, not a hub dependency")

ROOT = pathlib.Path(__file__).resolve().parent.parent
MOD = ROOT / "steltic_hub" / "catalog" / "steltic_probabilistic"
sys.path.insert(0, str(MOD))
TMP = pathlib.Path(tempfile.mkdtemp(prefix="probabilistic-test-"))
ENGINE = TMP / "engine"; ENGINE.mkdir()
(ENGINE / "engine3d.py").write_text("# stand-in for the HR Steel engine\n")
os.environ["PROB_JOBS"] = str(TMP / "jobs")
os.environ["STELTIC_ENGINE_DIR"] = str(ENGINE)
os.environ["DDM_PYTHON"] = sys.executable
os.environ["STELTIC_URL"] = "http://127.0.0.1:1"

from probabilistic import stats as S, variables as V     # noqa: E402
from probabilistic import main as pm                     # noqa: E402

# ---------------------------------------------------------------- a design package (IN_Ex1-like, tiny; N, N-mm)
# Shaped like steltic_india's calc_package.json: demands in inputs (P_comp_N, Mz_Nmm ...), the recorded IS 800
# design strengths under capacity.<check>.<Xd_N | Md_Nmm> (Rd = Rn / gamma_m), the recorded D/C = D/Rd.
MEMBERS = [
    {"id": "floor-NPB450X190X67.16", "inputs": {"kind": "beam", "role": "floor", "section": "NPB450X190X67.16", "P_comp_N": 0, "P_tens_N": 0, "Mz_Nmm": 303750000.0, "My_Nmm": 0, "V_N": 218962.9, "governing_combo": "1.5DL+1.5LL"},
     "limit_state": "IS 800:2007 7-9 (member_check_is800)", "capacity": {"Mdz_section": {"Md_Nmm": 338636363.6, "cite": "IS 800:2007 8.2.1.2"}, "shear_z": {"Vd_N": 445766.9, "cite": "IS 800:2007 8.4"}}, "DC": 0.897},
    {"id": "gravity_col-WPB300X300X88.34", "inputs": {"kind": "col", "role": "gravity_col", "section": "WPB300X300X88.34", "P_comp_N": 1100000.0, "P_tens_N": 0, "Mz_Nmm": 22000000.0, "My_Nmm": 6000000.0, "V_N": 5000.0, "governing_combo": "1.5DL+1.5LL"},
     "limit_state": "IS 800:2007 7-9 (member_check_is800)", "capacity": {"compression": {"Pd_N": 2085792.8}, "Mdz_section": {"Md_Nmm": 284090909.1}, "Mdy": {"Md_Nmm": 95454545.5}}, "DC": 0.668},
    {"id": "lateral_col-WPB360X300X163", "inputs": {"kind": "col", "role": "lateral_col", "section": "WPB360X300X163", "P_comp_N": 593800.0, "P_tens_N": 236600.0, "Mz_Nmm": 190000000.0, "My_Nmm": 77000000.0, "V_N": 197500.0, "governing_combo": "1.2DL+1.2LL+1.2EQ_X"},
     "limit_state": "IS 800:2007 7-9 (member_check_is800)", "capacity": {"compression": {"Pd_N": 3244222.4}}, "DC": 0.183},
    {"id": "brace-WPB250X250X73.15", "inputs": {"kind": "brace", "role": "brace", "section": "WPB250X250X73.15", "P_comp_N": 422114.0, "P_tens_N": 600000.0, "Mz_Nmm": 0, "My_Nmm": 0, "V_N": 0, "governing_combo": "1.2DL+1.2LL+1.2EQ_X"},
     "limit_state": "IS 800:2007 7-9 (member_check_is800)", "capacity": {"compression": {"Pd_N": 844228.0}, "tension": {"Td_N": 2115909.1}}, "DC": 0.5},
]
CONNS = [
    {"id": "conn-gravity-beam-col", "type": "beam-to-column", "demand": {"V_N": 110000.0}, "capacity": {}, "limit_state": "beam-end shear connection: bolts_10_3 (IS 800:2007 10.3.2)", "DC": 0.9},
    {"id": "conn-SMF-joint", "type": "moment connection (IS 800 12.11.2) + panel zone", "demand": {"M_1_2Mp_Nmm": 48967000.0, "V_overstrength_N": 394000.0}, "capacity": {}, "limit_state": "panel-zone shear 12.11.2.3 (yield, gamma_m0)", "DC": 0.792},
    {"id": "conn-SMF-col-base", "type": "column base plate (fixed)", "demand": {"P_N": 3900000.0, "V_N": 420000.0}, "capacity": {}, "limit_state": "base plate bending 8.2 / anchor bolts 10.3", "DC": 0.89},
]
GROUPS0 = {m["id"]: {"kind": m["inputs"]["kind"], "section": m["inputs"]["section"], "role": m["inputs"]["role"], "n": 10,
                     "comp": m["inputs"]["P_comp_N"], "tens": m["inputs"]["P_tens_N"], "Mz": m["inputs"]["Mz_Nmm"], "My": m["inputs"]["My_Nmm"],
                     "V": m["inputs"]["V_N"], "combo": m["inputs"]["governing_combo"], "story_max": 1} for m in MEMBERS}
HANDLES0 = {"beam_V": 218962.9, "beam_M": 303750000.0, "gravity_beam_V": 218962.9, "col_P": 1100000.0, "col_T": 236600.0, "col_M": 190000000.0, "col_V": 197500.0, "brace_P": 600000.0, "V_base": 1657800.0}


def package_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("B/cfg.py", "cfg = dict(units='N-mm', jurisdiction='india', NX=5, NY=4, SX=6000.0, SY=6000.0, heights=[3600.0, 3600.0, 3600.0], Fy=250.0, steel_grade='E250 B0', seis={'zone': 'IV', 'Z': 0.24, 'I': 1.2, 'R': 4.5, 'soil': 'II'})\n")
        z.writestr("B/model_opensees.py", "# replay\n")
        z.writestr("B/design/member_schedule.csv", "ele_tag,member,role,section,length_mm\n1,col,gravity_col,WPB300X300X88.34,3600.0\n")
        z.writestr("B/design/calc_package.json", json.dumps({"members": MEMBERS, "connections": CONNS, "capacity_design": {"system": "SCBF", "R": 4.5},
                                                              "design_status": {"status": "complete", "n_reasons": 0, "reasons": []}}))
        z.writestr("B/report.html", "<html>report</html>")
    return buf.getvalue()


STUB_WORKER = textwrap.dedent('''
    """Stub worker: probe from calc_package.json; run = canned demands scaled by the sample."""
    import argparse, json, os, sys
    GROUPS0 = %s
    HANDLES0 = %s
    def probe(a):
        cp = json.load(open(os.path.join(a.package, "design", "calc_package.json")))
        members = [{"id": m["id"], "kind": m["inputs"]["kind"], "role": m["inputs"]["role"], "section": m["inputs"]["section"], "DC": m["DC"],
                    "limit_state": m["limit_state"], "capacity": m["capacity"], "demands": {}} for m in cp["members"]]
        conns = [{"id": c["id"], "type": c["type"], "section": None, "DC": c["DC"], "limit_state": c["limit_state"], "demand": c["demand"], "capacity": c["capacity"]} for c in cp["connections"]]
        out = {"name": "B", "members": 40, "stories": 3, "levels": [0.0, 3600.0, 7200.0, 10800.0], "NX": 5, "NY": 4, "Fy_nominal": 250.0, "system": "SCBF",
               "steel_grade": "E250 B0", "units": "N-mm", "seismic": {"zone": "IV", "Z": 0.24, "I": 1.2, "R": 4.5, "soil": "II"}, "design_status": "complete",
               "groups": [{"id": g, "section": v["section"], "kind": v["kind"], "role": v["role"], "n": 10} for g, v in GROUPS0.items()],
               "sections": sorted({v["section"] for v in GROUPS0.values()}), "combos": [{"label": "1.5DL+1.5LL", "col_only": False}, {"label": "1.2DL+1.2LL+1.2EQ_X", "col_only": False}],
               "design": {"members": members, "connections": conns, "n_members_dc": len(members), "n_connections_dc": len(conns)}, "drift_limit": 0.02}
        json.dump(out, open(a.out, "w")); print("probe ok", flush=True)
    def run(a):
        spec = json.load(open(a.spec)); want = {r["id"]: r for r in spec["realisations"]}
        ids = [int(x) for x in a.ids.split(",") if x.strip()] if a.ids else list(want)
        os.makedirs(a.out, exist_ok=True)
        for i in ids:
            r = want[i]; s = r.get("sample") or {}
            print(json.dumps({"event": "start", "id": i, "nominal": bool(r.get("nominal"))}), flush=True)
            if i == 7:
                out = {"id": i, "ok": False, "error": "RuntimeError: singular", "seconds": 0.1}
            else:
                f = float(s.get("dead", 1.0)) if s else 1.0
                lean = float((s.get("summary") or {}).get("lean_top", 0.0)) if s else 0.0
                thk = s.get("groups") or {}
                groups = {}
                for gid, g in GROUPS0.items():
                    gg = dict(g); k = f * (1.0 + 40.0 * lean) * (1.0 / float((thk.get(g["section"]) or {}).get("thk", 1.0))) ** 0.5
                    for q in ("comp", "tens", "Mz", "My", "V"): gg[q] = g[q] * k
                    groups[gid] = gg
                handles = {k: v * f for k, v in HANDLES0.items()}
                out = {"id": i, "ok": True, "nominal": bool(r.get("nominal")), "sample_summary": s.get("summary"), "T1": 1.1 / f ** 0.5, "VB_kN": 1657.8 * f, "W_kN": 20722.5, "Ah": 0.08,
                       "drift": {"mdx": 0.0018 * f, "mdy": 0.0014, "limit": 0.004, "ratio": 0.45 * f}, "groups": groups, "handles": handles, "seconds": 0.2}
            json.dump(out, open(os.path.join(a.out, "r%%04d.json" %% i), "w"))
            print(json.dumps({"event": "done", "id": i, "ok": out["ok"], "T1": out.get("T1"), "VB_kN": out.get("VB_kN"), "seconds": out["seconds"], "error": out.get("error")}), flush=True)
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("probe"); p.add_argument("--package"); p.add_argument("--engine"); p.add_argument("--out")
    r = sub.add_parser("run"); r.add_argument("--package"); r.add_argument("--engine"); r.add_argument("--spec"); r.add_argument("--out"); r.add_argument("--ids", default=""); r.add_argument("--force", action="store_true")
    a = ap.parse_args(); probe(a) if a.cmd == "probe" else run(a)
''') % (json.dumps(GROUPS0), json.dumps(HANDLES0))
STUB = TMP / "stub_worker.py"
STUB.write_text(STUB_WORKER)


# ---------------------------------------------------------------- sampler
def _probe():
    return {"stories": 3, "levels": [0.0, 3600.0, 7200.0, 10800.0], "sections": ["NPB450X190X67.16", "WPB300X300X88.34"], "Fy_nominal": 250.0}


def test_variables_have_sources_and_sampler_is_deterministic():
    assert [v["id"] for v in V.VARIABLES] == ["E", "thk", "psi", "dead"]
    for v in V.VARIABLES:
        assert v["source"] and v["what"] and v["level"] in ("building", "group", "story")
    assert V.variable("dead")["enabled"] is False and V.variable("E")["enabled"] is True
    a = V.build_spec(_probe(), 5, 11, {"nseg": 6}, V.merged(None))
    b = V.build_spec(_probe(), 5, 11, {"nseg": 6}, V.merged(None))
    assert json.dumps(a["realisations"]) == json.dumps(b["realisations"])
    assert a["realisations"][0] == {"id": 0, "nominal": True} and len(a["realisations"]) == 6
    s = a["realisations"][1]["sample"]
    assert set(s["groups"]) == {"NPB450X190X67.16", "WPB300X300X88.34"} and all(0.8 <= g["thk"] <= 1.2 for g in s["groups"].values())
    assert set(s["lean"]) == {"1", "2", "3"} and all(len(v) == 2 for v in s["lean"].values())
    assert s["dead"] == 1.0                                              # off by default
    assert 0.7 * 200000 <= s["E"] <= 1.3 * 200000 and "lean_top" in s["summary"] and "thk_mean" in s["summary"]     # E in MPa, IS 800 2.2.4.1
    assert V.NOMINAL_E == 200000.0 and V.GAMMA_M0 == 1.10 and V.GAMMA_M1 == 1.25
    for v in V.VARIABLES:
        assert not any(tok in (v["source"] + v["what"] + v["unit"]) for tok in ("ksi", "AISC", "ASCE", "psf"))
    c = V.build_spec(_probe(), 3, 1, {}, V.merged({"dead": {"enabled": True, "mean": 1.05, "cov": 0.1}, "psi": {"enabled": False}}))
    s2 = c["realisations"][1]["sample"]
    assert s2["dead"] != 1.0 and s2["lean"] == {}


def test_lognormal_mean_and_cov_are_honoured():
    rng = np.random.default_rng(0)
    x = V._draw(rng, {"id": "thk", "dist": "lognormal", "mean": 1.0, "cov": 0.05}, 20000)
    assert abs(x.mean() - 1.0) < 0.003 and abs(x.std() / x.mean() - 0.05) < 0.004
    y = V._draw(rng, {"id": "psi", "dist": "normal", "mean": 0.0, "std": 0.001}, 20000)
    assert abs(y.mean()) < 5e-5 and abs(y.std() - 0.001) < 5e-5


# ---------------------------------------------------------------- D/C with the design's capacities
def test_exact_check_reproduces_the_recorded_dc_and_removes_gamma():
    m = MEMBERS[0]; c = S.caps_of(m)
    assert c["Mcx"] == 338636363.6 and c["Vc"] == 445766.9 and c["Pc"] is None          # Mdz (8.2), Vd (8.4) as recorded
    assert S.dc_exact("beam", GROUPS0[m["id"]], c) == pytest.approx(0.897, abs=0.001)
    col = MEMBERS[1]; cc = S.caps_of(col)
    assert cc["Pc"] == 2085792.8 and cc["Mcy"] == 95454545.5
    assert S.dc_exact("col", GROUPS0[col["id"]], cc) == pytest.approx(0.668, abs=0.005)      # 9.3.1.3 linear interaction
    # nominal basis: gamma_m0 = 1.10 comes out of every member strength (Rn = 1.10 Rd)
    cn = S.caps_basis(c, "nominal")
    assert cn["Mcx"] == pytest.approx(338636363.6 * 1.10) and cn["Vc"] == pytest.approx(445766.9 * 1.10)
    assert S.dc_exact("beam", GROUPS0[m["id"]], cn) == pytest.approx(0.897 / 1.10, abs=0.001)
    br = MEMBERS[3]; cb = S.caps_of(br)
    assert cb["Pt"] == 2115909.1 and S.dc_exact("brace", GROUPS0[br["id"]], cb) == pytest.approx(0.5)
    assert S.gamma_member("IS 800:2007 7-9 (member_check_is800)") == 1.10
    assert S.gamma_member("net section rupture 6.3.1") == 1.25
    assert [v for _, v, _ in S.GAMMA_NOTES] == [1.10, 1.25, 1.25, 1.25, 1.50, 1.10]
    assert all("IS 800" in src for _, _, src in S.GAMMA_NOTES)


def test_member_dcs_exact_scaled_and_bases():
    d = S.member_dcs(MEMBERS, GROUPS0, GROUPS0, "design")
    assert d["floor-NPB450X190X67.16"]["method"] == "exact" and d["floor-NPB450X190X67.16"]["dc"] == pytest.approx(0.897)
    assert d["gravity_col-WPB300X300X88.34"]["method"] == "exact" and d["gravity_col-WPB300X300X88.34"]["dc"] == pytest.approx(0.668)
    assert d["lateral_col-WPB360X300X163"]["method"] == "scaled" and d["lateral_col-WPB360X300X163"]["dc"] == pytest.approx(0.183)   # only Pd recorded
    n = S.member_dcs(MEMBERS, GROUPS0, GROUPS0, "nominal")
    assert n["floor-NPB450X190X67.16"]["dc"] == pytest.approx(0.897 / 1.10) and n["floor-NPB450X190X67.16"]["dc_recorded"] == 0.897
    assert n["lateral_col-WPB360X300X163"]["dc"] == pytest.approx(0.183 / 1.10) and n["lateral_col-WPB360X300X163"]["phi"] == 1.10
    # demands up 10% -> exact ratios up 10%; the scaled group follows its largest component
    up = {g: {**v, "comp": v["comp"] * 1.1, "Mz": v["Mz"] * 1.1, "My": v["My"] * 1.1, "V": v["V"] * 1.05, "tens": v["tens"]} for g, v in GROUPS0.items()}
    u = S.member_dcs(MEMBERS, GROUPS0, up, "design")
    assert u["floor-NPB450X190X67.16"]["dc"] == pytest.approx(0.897 * 1.1, rel=1e-3)
    assert u["lateral_col-WPB360X300X163"]["dc"] == pytest.approx(0.183 * 1.1, rel=1e-3)
    assert u["gravity_col-WPB300X300X88.34"]["dc"] > 0.668 * 1.09


def test_connection_scaling_and_gamma():
    c = S.connection_dcs(CONNS, HANDLES0, HANDLES0, "design")
    assert c["conn-gravity-beam-col"]["dc"] == 0.9 and c["conn-gravity-beam-col"]["mapped"] == [("V_N", "gravity_beam_V")]
    assert c["conn-SMF-joint"]["mapped"] == [("M_1_2Mp_Nmm", "capacity-designed, unchanged"), ("V_overstrength_N", "capacity-designed, unchanged")]
    assert c["conn-SMF-col-base"]["mapped"] == [("P_N", "col_P"), ("V_N", "V_base")]
    n = S.connection_dcs(CONNS, HANDLES0, HANDLES0, "nominal")
    assert n["conn-gravity-beam-col"]["phi"] == 1.25 and n["conn-gravity-beam-col"]["dc"] == pytest.approx(0.9 / 1.25)     # bolts: gamma_mb
    assert n["conn-SMF-joint"]["phi"] == 1.10 and n["conn-SMF-col-base"]["phi"] == 1.25                                 # panel zone yield: gamma_m0; anchors: gamma_m1
    assert S.gamma_connection("site weld 10.5", "moment") == 1.50
    h = {**HANDLES0, "V_base": HANDLES0["V_base"] * 1.2, "gravity_beam_V": HANDLES0["gravity_beam_V"] * 1.05}
    u = S.connection_dcs(CONNS, HANDLES0, h, "design")
    assert u["conn-SMF-col-base"]["dc"] == pytest.approx(0.89 * 1.2) and u["conn-SMF-joint"]["dc"] == 0.792
    assert u["conn-gravity-beam-col"]["dc"] == pytest.approx(0.9 * 1.05)


# ---------------------------------------------------------------- statistics
def _results(n=30, seed=2):
    rng = np.random.default_rng(seed)
    res = [{"id": 0, "ok": True, "nominal": True, "groups": GROUPS0, "handles": HANDLES0, "drift": {"ratio": 0.45}, "T1": 1.1, "VB_kN": 1657.8, "seconds": 1}]
    for i in range(1, n + 1):
        f = float(rng.lognormal(0.05, 0.08))
        groups = {g: {**v, **{q: v[q] * f for q in ("comp", "tens", "Mz", "My", "V")}} for g, v in GROUPS0.items()}
        res.append({"id": i, "ok": True, "groups": groups, "handles": {k: v * f for k, v in HANDLES0.items()}, "drift": {"ratio": 0.45 * f},
                    "T1": 1.1, "VB_kN": 1657.8 * f, "seconds": 1, "sample_summary": {"E_factor": 1.0 / f, "thk_mean": 1.0, "lean_top": 0.001 * f, "dead": f}})
    res.append({"id": 99, "ok": False, "error": "boom"})
    return res


def test_analyse_statistics_modes_and_assessment():
    spec = {"n": 30, "seed": 2}
    probe = {"design": {"members": MEMBERS, "connections": CONNS}}
    a = S.analyse(spec, _results(), probe, "nominal")
    assert a["basis"] == "nominal" and a["ratio_label"] == "D/Rₙ" and a["n_done"] == 30 and a["n_failed"] == 1
    b = a["base"]
    assert b["member_max"] == pytest.approx(0.897 / 1.10) and b["member_max_recorded"] == 0.897 and b["governing"] == "floor-NPB450X190X67.16"
    st = a["members"]["stats"]
    assert st["n"] == 30 and st["p_over_limit"] > 0 and st["cov"] > 0.05 and "fit_lognormal" in st and st["fit_lognormal"]["ks"]["p"] > 0.01
    assert a["governing"]["same_as_base_share"] == 1.0
    g = {x["id"]: x for x in a["groups"]}
    assert g["floor-NPB450X190X67.16"]["p_over_1"] > 0 and g["floor-NPB450X190X67.16"]["method"] == "exact" and g["lateral_col-WPB360X300X163"]["phi"] == 1.10
    assert a["conn_table"][0]["phi"] in (1.10, 1.25, 1.50)
    assert any(c["key"] == "dead" and c["rho"] > 0.9 for c in a["correlations"])
    text = " ".join(a["assessment"])
    assert "FACTORED demand" in text and "NOMINAL" in text and "second look" in text and "0.897" in text
    assert "IS 800" in text and "γm" in text and not any(tok in text for tok in ("AISC", "ASCE", "LRFD", "φ"))
    d = S.analyse(spec, _results(), probe, "design")
    assert d["base"]["member_max"] == 0.897 and d["ratio_label"] == "D/Rd"
    p = S.plots(a)
    assert all(k in p for k in ("members", "connections", "drift", "groups")) and "<svg" in p["members"] and "D/R" in p["groups"]
    out = TMP / "out"; S.write_outputs(out, {**a, "variables": []}, "T", probe)
    assert (out / "report.html").is_file() and (out / "results.csv").is_file() and (out / "dc_groups.svg").is_file()
    rep = (out / "report.html").read_text(encoding="utf-8")
    assert "NOMINAL strength" in rep and "IS 800 Table 5" in rep and "IS 1893" in rep
    assert not any(tok in rep for tok in ("AISC", "ASCE", "LRFD", "kip"))


def test_invariant_maximum_is_reported_not_fitted():
    res = _results(12)
    for r in res:
        if r.get("ok") and r["id"] not in (0, 99):
            r["groups"]["floor-NPB450X190X67.16"] = dict(GROUPS0["floor-NPB450X190X67.16"])           # determinate gravity beam: never moves
            r["groups"]["gravity_col-WPB300X300X88.34"] = {**GROUPS0["gravity_col-WPB300X300X88.34"], "Mz": 100.0}
    a = S.analyse({"n": 12}, res, {"design": {"members": MEMBERS[:2], "connections": []}}, "nominal")
    st = a["members"]["stats"]
    assert st["invariant"] and st["std"] == 0.0 and "fit_lognormal" not in st
    assert any("does not move at all" in s for s in a["assessment"])
    assert a["correlations"] == []


# ---------------------------------------------------------------- API flow with the stub worker
@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(pm, "WORKER", STUB)
    monkeypatch.setattr(pm, "DDM_PYTHON", sys.executable)
    monkeypatch.setattr(pm, "ENGINE_DIR", str(ENGINE))
    return TestClient(pm.app)


def _wait(client, p, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not client.get(f"/api/project/{p}").json()["running"]:
            return
        time.sleep(0.05)
    raise AssertionError("study did not finish")


def test_api_package_run_results_basis(client):
    p = "Study1"
    me = client.get("/api/me").json()
    assert me["ddm_python_ok"] and me["engine_ok"]
    lib = client.get("/api/library").json()
    assert [v["id"] for v in lib["variables"]] == ["E", "thk", "psi", "dead"] and lib["default_n"] == 100 and len(lib["not_varied"]) == 4
    assert client.post(f"/api/project/{p}/run", json={}).status_code == 400            # no package yet
    r = client.post(f"/api/project/{p}/package/upload", files={"file": ("B.zip", package_zip(), "application/zip")})
    assert r.status_code == 200, r.text
    pr = r.json()["probe"]
    assert pr["design"]["n_members_dc"] == 4 and pr["n_combos"] == 2 and len(pr["groups"]) == 4
    assert r.json()["package"]["source"] == "upload"
    # a zip that is not a design is refused
    bad = io.BytesIO(); zipfile.ZipFile(bad, "w").writestr("x.txt", "hi")
    assert client.post(f"/api/project/{p}/package/upload", files={"file": ("x.zip", bad.getvalue(), "application/zip")}).status_code == 400
    # run 9 realisations (id 7 fails in the stub) with the dead load on
    r = client.post(f"/api/project/{p}/run", json={"n": 9, "seed": 4, "workers": 2, "variables": {"dead": {"enabled": True}}})
    assert r.status_code == 200 and r.json()["ids"] == 10 and r.json()["workers"] == 2
    assert client.post(f"/api/project/{p}/run", json={}).status_code == 409
    _wait(client, p)
    ev = client.get(f"/api/project/{p}/events?since=0").text
    assert '"type": "start"' in ev and '"type": "finished"' in ev and '"id": 0' in ev
    st = client.get(f"/api/project/{p}").json()
    assert st["results"]["n_ok"] == 8 and st["results"]["n_failed"] == 1 and st["results"]["base"]["ok"] and st["run"]["status"] == "finished"
    res = client.get(f"/api/project/{p}/results").json()
    a = res["analysis"]
    assert a["basis"] == "nominal" and a["n_done"] == 8 and a["n_failed"] == 1 and a["base"]["member_max"] == pytest.approx(0.897 / 1.10)
    assert st["results"]["base"]["VB_kN"] == pytest.approx(1657.8) and pr["seismic"]["zone"] == "IV" and pr["units"] == "N-mm"
    assert a["members"]["stats"]["n"] == 8 and a["members"]["stats"]["cov"] > 0
    assert "members" in res["plots"] and "groups" in res["plots"]
    assert client.get(f"/api/project/{p}/plot/members.svg").status_code == 200
    assert client.get(f"/api/project/{p}/file/report.html").status_code == 200
    assert client.get(f"/api/project/{p}/file/results.csv").status_code == 200
    # the design's own IS 800 basis (D/Rd) on request, and back
    assert client.post(f"/api/project/{p}/basis", json={"basis": "design"}).json()["basis"] == "design"
    a2 = client.get(f"/api/project/{p}/results").json()["analysis"]
    assert a2["basis"] == "design" and a2["base"]["member_max"] == 0.897
    client.post(f"/api/project/{p}/basis", json={"basis": "nominal"})
    # continue: only the failed one is re-run
    r = client.post(f"/api/project/{p}/run", json={"continue": True})
    assert r.status_code == 200 and r.json()["ids"] == 1
    _wait(client, p)
    # a realisation's inputs can be read back
    one = client.get(f"/api/project/{p}/realisation/3").json()
    assert one["result"]["ok"] and one["sample"]["dead"] != 1.0
    # a new package invalidates the study
    r = client.post(f"/api/project/{p}/package/upload", files={"file": ("B.zip", package_zip(), "application/zip")})
    assert r.status_code == 200 and client.get(f"/api/project/{p}").json()["results"]["n_total"] == 0


def test_stop_kills_the_workers(client, monkeypatch):
    p = "Study2"
    slow = TMP / "slow_worker.py"
    slow.write_text(STUB_WORKER.replace('print(json.dumps({"event": "start"', 'import time; time.sleep(0.4); print(json.dumps({"event": "start"'))
    monkeypatch.setattr(pm, "WORKER", slow)
    client.post(f"/api/project/{p}/package/upload", files={"file": ("B.zip", package_zip(), "application/zip")})
    assert client.post(f"/api/project/{p}/run", json={"n": 30, "workers": 1}).status_code == 200
    time.sleep(0.6)
    assert client.post(f"/api/project/{p}/stop").json()["running"] is True
    _wait(client, p, 20)
    st = client.get(f"/api/project/{p}").json()
    assert st["run"]["status"] == "stopped" and st["results"]["n_total"] < 30


def test_state_write_survives_windows_replace_race(monkeypatch):
    """Windows: os.replace raises PermissionError while the page polls state.json; the write must retry, not vanish."""
    real = os.replace; calls = {"n": 0}
    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(5, "Access is denied")
        return real(src, dst)
    monkeypatch.setattr(os, "replace", flaky)
    pm.update_state("RaceStudy", lambda st: st["settings"].__setitem__("n", 7))
    assert calls["n"] == 3 and pm.load_state("RaceStudy")["settings"]["n"] == 7
