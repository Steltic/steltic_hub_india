"""PEB-LEVERS (2026-10): the 'Indian PEB practice levers' category maps to the HR engine's cfg['peb_rules'] switches
and to the brief-level variants of the deterministic lever study; the non-compliant readings say so."""
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "steltic_hub" / "catalog" / "steltic_variations"))
from variations import library   # noqa: E402

SWITCHES = {"joint_demand": "deliverable", "ltb_c1": "gradient", "sway_limit": "H150_with_dado_check",
            "base_shear_key": "analysis", "bracing": "tension_rods", "purlins": "thin_G350",
            "collateral_in_uplift": "True"}


def test_peb_levers_category_maps_to_the_switches():
    c = library.category("peb_levers")
    assert c and c["label"] == "Indian PEB practice levers" and c["applies_to"] == ["portal"]
    text = " ".join(" ".join(t) for t in c["templates"])
    for k, v in SWITCHES.items():
        assert "cfg['peb_rules']['%s']" % k in text and v in text, k
    assert "OMRF R 3" in text and "Table 9 Note 1" in text and "12.10.1.1" in text
    mk = [t for t in c["templates"] if t[0].startswith("Market practice")]
    assert mk and "NOT COMPLIANT" in mk[0][0] + mk[0][1] and "0.57" in mk[0][1] and "0.75" in mk[0][1]
    rods = [t for t in c["templates"] if "rod" in t[0].lower()]
    assert rods and "NOT compliant" in rods[0][1] and "Zone II" in rods[0][0]


def test_peb_levers_offered_to_sheds_only():
    groups = {v["group"] for v in library.offline_plan(400, kind="portal")}
    assert "Indian PEB practice levers" in groups
    groups = {v["group"] for v in library.offline_plan(400, kind="multistorey")}
    assert "Indian PEB practice levers" not in groups


def test_lever_study_how_to_is_shipped():
    here = os.path.dirname(os.path.abspath(library.__file__))
    p = os.path.join(os.path.dirname(here), "PEB_LEVERS.md")
    txt = open(p, encoding="utf-8").read()
    for s in ("peb_lever_study.py", "peb_lever_jobs.py", "cfg['peb_rules']", "--job", "study.html"):
        assert s in txt, s
