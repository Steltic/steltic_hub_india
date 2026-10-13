"""PEB (2026-10): variations metrics read the engine's plate mass (member_schedule.csv mass_kg) and the
whole-building PEB take-off (calc_package peb.tonnage) instead of guessing welded / tapered masses."""
import io
import json
import zipfile

from steltic_hub.catalog.steltic_variations.variations import metrics


def _files(sched, calc=None):
    f = {"design/member_schedule.csv": sched.encode()}
    if calc is not None:
        f["design/calc_package.json"] = json.dumps(calc).encode()
    return f


def test_welded_and_tapered_designations_weigh():
    # WI550X220X10X5: A = 2 x 220 x 10 + 530 x 5 = 7,050 mm2 -> 55.34 kg/m
    assert abs(metrics.section_mass_kg_m("WI550X220X10X5") - 7050 * 0.00785) < 1e-9
    # TI300-1000X250X14X6: mean depth 650 -> A = 2 x 250 x 14 + 622 x 6
    assert abs(metrics.section_mass_kg_m("TI300-1000X250X14X6") - (7000 + 622 * 6) * 0.00785) < 1e-9


def test_schedule_mass_kg_column_wins():
    sched = ("ele_tag,member,role,section,length_mm,mass_kg\n"
             "1,col,lateral_col,WI344X250X14X6,1250.0,99.5\n"
             "2,beam,roof,WPB300X300X88.34,6000.0,\n")
    m = metrics.schedule_metrics(_files(sched))
    # row 1 from its mass_kg column (99.5 kg); row 2 from the IS 808 name (88.34 kg/m x 6 m = 530.04 kg)
    assert abs(m["steel_t"] - round((99.5 + 530.04) / 1000.0, 1)) < 1e-9
    assert "mass_kg column (1 of 2 rows)" in m["steel_t_basis"]


def test_peb_tonnage_sets_steel_kg_m2():
    sched = "ele_tag,member,role,section,length_mm,mass_kg\n1,col,lateral_col,WI344X250X14X6,1250.0,99.5\n"
    calc = {"members": [], "connections": [],
            "peb": {"tonnage": {"kg": {"primary": 39796.9, "secondary": 16918.3}, "total_kg": 69726.1,
                                "plan_area_m2": 2250.0,
                                "kg_per_m2": {"primary": 17.69, "secondary": 7.52, "total": 30.99}}}}
    m = metrics.extract(_files(sched, calc))
    assert m["peb_kg_m2"]["total"] == 30.99 and m["peb_steel_t"] == 69.7
    assert m["steel_kg_m2"] == 31.0 and "peb.tonnage" in m["steel_kg_m2_basis"]


def test_tapered_frames_not_representable_but_exempt_by_default():
    from steltic_hub.catalog.steltic_variations.variations import scoring
    sched = ("ele_tag,member,role,section,length_mm,start_depth_mm,end_depth_mm,mass_kg,peb_member\n"
             "1,col,lateral_col,WI344X250X14X6,1250.0,300.0,387.5,99.5,C1-0-m1\n")
    m = metrics.extract(_files(sched, {"members": [], "connections": [], "capacity_design": {"system": "OMRF"}}))
    assert m["tapered"] is True and m["representable"] is False
    assert "tapered" in m["not_representable_because"]
    row = {"status": "done", "metrics": dict(m, design_status="complete", n_over=0, dc_max=0.9, gates_ok=True)}
    assert not any("nonlinear" in w for w in scoring.eligibility(row, {}))                       # exempt by default
    assert any("tapered" in w for w in scoring.eligibility(row, {"tapered_exempt": False}))     # the rule, when chosen
