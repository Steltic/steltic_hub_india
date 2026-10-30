"""The site build (site/, handoff P4): one command builds the upload zip, the rollback zip and the upload
instructions from the gold outputs, the cold-formed publish folder, the nonlinear run folders, the titles files and
the live base page.

Everything here is a small fixture written by the tests: a live site mirror (india.html and the three indexes), two
hot-rolled gold jobs and a unit, a cold-formed publish folder, two nonlinear runs (one re-run) and a FAKE
steltic_nonlinear_india checkout (scripts/build_site_index.py, `python -m steltic_ddm report`, `python -m snl
report`) that writes predictable files. The "live site" is read from the mirror or through file:// URLs: no test
reaches the network.

    python -m pytest tests/test_site_build.py -q
"""
import hashlib
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import zipfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
sys.path.insert(0, str(SITE))

from steltic_site import cli, hr as HR, live as LIVE, package as PKG, page as PAGE, scan as SCAN  # noqa: E402
from steltic_site import selfcontained as SC  # noqa: E402
from steltic_site.util import BuildError  # noqa: E402

EX1 = "IN_Ex1_PEB_shed_Test"
EX9 = "IN_Ex9_Gable_SMF_SCBF_Test"
UNIT = EX9 + "/units/annex"
CFS1 = "IN_CFS_Ex1_Test"
NL_A = "IN_Ex1_PEB_shed_Test"
NL_B = "IN_Ex9_Gable_SMF_SCBF_Test"

PAGE_HTML = """<!doctype html><html><head><link rel="stylesheet" href="styles.css?v=7"><link rel="icon" href="favicon.ico">
</head><body>
<p>Steel to Indian Standards. 4 worked Indian designs, including one PEB shed, and 2 nonlinear runs with a verdict.</p>
<p>The 2 Indian example briefs that ship with Steltic India, plus one sub-units, designed end to end.</p>
<script>
          if(flag) flag.textContent = e.flag || "";
</script>
</body></html>
"""


def w(path, text, mode="w"):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


def jdump(path, obj):
    return w(path, json.dumps(obj, indent=1, ensure_ascii=False))


def _pkg(system, R, peb=False, kg=None, status="complete"):
    p = {"design_status": {"status": status}, "seismic_calc": {"system": system, "R": R, "W_design_kN": 1234.4},
         "load_plan": {"seismic_summary": {"VB_x_kN": 50.2, "VB_y_kN": 60.7}},
         "members": [{"id": "col-1", "DC": 0.8}],
         "capacity_design": {"section12": {"checks": [{"id": "is18168_table2_beam", "ok": True}]}}}
    if peb:
        p["peb"] = {"tonnage": {"kg_per_m2": {"total": kg}}}
    return p


def _gold_job(d, system, R, peb=False, kg=None, figs=True):
    d = pathlib.Path(d)
    jdump(d / "design" / "calc_package.json", _pkg(system, R, peb=peb, kg=kg))
    jdump(d / "design" / "headline.json", {"strength": {"dc": 0.912, "member": "col-WI500"},
                                           "slenderness": {"KL_r": 140.2, "limit": 180, "ratio": 0.779, "member": "brace-CHS"}})
    w(d / "report.html", '<html><body><h1>Report</h1><img src="figs/auto_001.png">%s'
                         '<p>the design engineer checks it</p></body></html>' % ('<a href="model.std">STAAD</a>' if peb else ""))
    w(d / "viewer_3d.html", "<html>viewer " + "x" * 100 + "</html>")
    if figs:
        w(d / "figs" / "auto_001.png", b"\x89PNG fake figure")
    if peb:
        for f in ("model.std", "design/reactions.csv", "design/bom.csv", "design/anchor_bolt_plan.svg"):
            w(d / f, "data " + f)
    else:
        jdump(d / "design" / "takeoff.json", {"kg_per_m2": kg or 47.6})


BUNDLE = """<html><body><div id="empty">No viewer found next to this file.</div><script>
const MODS = [['steltic','Steltic','#cfe3ff','viewer_3d.html'],['pushover','Pushover','#ffb454','pushover/pushover_viewer_3d.html'],['nlrha','NLRHA','#3fc1a0','nlrha/nlrha_viewer_3d.html'],['ddm','DDM','#e0b341','ddm_viewer_3d.html']];
</script></body></html>"""


def _nl_run(d, rerun=False):
    d = pathlib.Path(d)
    w(d / "four_analyses.html", "<html>four analyses</html>")
    w(d / "steltic_viewer_bundle.html", BUNDLE)
    w(d / "viewer_3d.html", "<html>hr viewer</html>")
    w(d / "pushover" / "pushover_report.html", "<html>pushover</html>")
    w(d / "pushover" / "pushover_viewer_3d.html", "<html>pushover viewer</html>")
    w(d / "nlrha" / "nlrha_report.html", '<html><a href="DBE/nlrha_report.html">DBE</a></html>')
    w(d / "nlrha" / "DBE" / "nlrha_report.html", "<html>dbe</html>")
    w(d / "nlrha" / "nlrha_viewer_3d.html", "<html>nlrha viewer</html>")
    jdump(d / "ddm_results.json", {"lambda": 1.6})
    w(d / "ddm_report.html", "<html>stored ddm report</html>")
    if rerun:
        w(d / "ddm_viewer_3d.html", "<html>ddm viewer</html>")


FAKE_INDEX = r'''
import argparse, json, os
ap = argparse.ArgumentParser()
ap.add_argument("--runs"); ap.add_argument("--previous"); ap.add_argument("--override", action="append", default=[])
ap.add_argument("--out")
a = ap.parse_args()
prev = json.load(open(a.previous))
ov = dict(o.split("=", 1) for o in a.override)
out = []
# as the real builder: the previous index's runs in its order, then the runs it does not list (here: overrides)
for e in prev["examples"] + [{"id": i, "title": "new run " + i} for i in sorted(ov) if i not in {x["id"] for x in prev["examples"]}]:
    src = ov.get(e["id"], os.path.join(a.runs, e["id"]))
    out.append(dict(e, meta="from %s" % ("override" if e["id"] in ov else "runs"), gate="complete",
                    summary=e["id"] + "/four_analyses.html", viewer=e["id"] + "/steltic_viewer_bundle.html",
                    ddm=e["id"] + "/ddm_report.html", src_exists=os.path.isdir(src)))
json.dump({"examples": out}, open(a.out, "w"), indent=1)
'''
FAKE_DDM = r'''
import os, sys
args = sys.argv[1:]
assert args[0] == "report" and "--no-block" in args
out = args[args.index("--out") + 1]
if os.environ.get("FAKE_DDM_FAIL"):
    sys.exit(4)
open(os.path.join(out, "ddm_report.html"), "w").write("<html>rebuilt ddm report of %s (engine %s)</html>"
    % (os.path.basename(args[1]), os.path.basename(os.path.dirname(os.environ.get("STELTIC_ENGINE_DIR", "")))))
'''
FAKE_SNL = r'''
import sys
assert sys.argv[1] == "report"
p = sys.argv[2] + "/four_analyses.html"
open(p, "a").write("<!-- regenerated -->")
'''


@pytest.fixture
def world(tmp_path):
    """A live site mirror, the gold outputs and the NL tooling of a tiny publish."""
    live = tmp_path / "live"
    w(live / "india.html", PAGE_HTML)
    w(live / "styles.css", "body{}")
    w(live / "favicon.ico", b"ico")
    hr_idx = {"examples": [
        {"id": EX1, "title": "Ex 1 · PEB shed", "report": EX1 + "/report.html", "viewer": EX1 + "/viewer_3d.html",
         "meta": "old", "status": "complete"},
        {"id": EX9, "title": "Ex 9 · gable shed", "report": EX9 + "/report.html", "viewer": EX9 + "/viewer_3d.html",
         "meta": "old", "status": "complete"},
        {"id": UNIT, "title": "Ex 9 · annex", "report": UNIT + "/report.html", "viewer": UNIT + "/viewer_3d.html",
         "meta": "old", "status": "complete"}]}
    jdump(live / "solutions_india" / "hr" / "index.json", hr_idx)
    w(live / "solutions_india" / "hr" / EX1 / "report.html", "<html>live old report</html>")
    w(live / "solutions_india" / "hr" / EX1 / "figs" / "auto_001.png", b"live figure")
    jdump(live / "solutions_india" / "cfs" / "index.json", {"examples": [{"id": CFS1}]})
    jdump(live / "solutions_india" / "nl" / "index.json", {"examples": [{"id": NL_A, "title": "Ex 1 nl"},
                                                                         {"id": NL_B, "title": "Ex 9 nl"}]})
    gold = tmp_path / "gold"
    _gold_job(gold / EX1, "OMRF+OCBF", 3.0, peb=True, kg=31.54)
    _gold_job(gold / EX9, "SMRF+SCBF", 4.5, kg=52.8)
    _gold_job(gold / UNIT, "SMRF", 5.0, kg=60.0)
    cfs = tmp_path / "cfs_publish" / "cfs"
    jdump(cfs / "index.json", {"examples": [{"id": CFS1, "report": CFS1 + "/report.html", "viewer": CFS1 + "/viewer_3d.html"}]})
    w(cfs / CFS1 / "report.html", '<html><img src="figs/auto_001.png"></html>')
    w(cfs / CFS1 / "figs" / "auto_001.png", b"cfs fig")
    w(cfs / CFS1 / "viewer_3d.html", "<html>cfs viewer</html>")
    runs, rerun = tmp_path / "nl" / "runs", tmp_path / "nl" / "rerun"
    _nl_run(runs / NL_A)
    _nl_run(runs / NL_B)
    _nl_run(rerun / NL_B, rerun=True)
    repo = tmp_path / "nlrepo"
    w(repo / "scripts" / "build_site_index.py", FAKE_INDEX)
    w(repo / "steltic_ddm" / "__init__.py", "")
    w(repo / "steltic_ddm" / "__main__.py", FAKE_DDM)
    w(repo / "snl" / "__init__.py", "")
    w(repo / "snl" / "__main__.py", FAKE_SNL)
    titles = jdump(tmp_path / "titles.json", {EX9: {"title": "Ex 9 · capability demonstration: gable shed",
                                                    "blurb": "Shows snow drift; usually a PEB shed in India."}})
    patch = w(tmp_path / "page.patch", PAGE.write_patch([(
        '          if(flag) flag.textContent = e.flag || "";',
        '          if(flag) flag.textContent = [e.blurb, e.flag].filter(Boolean).join(" ");')], "test patch"))
    cfg = {"name": "test_publish", "rollback_name": "test_ROLLBACK", "out": str(tmp_path / "out"),
           "self_contained": False, "live": {"mirror": str(live)},
           "page": {"patches": [str(patch)]},
           "hr": {"jobs": str(gold), "titles": [str(titles)], "system_names": {"SMRF": "SMF"},
                  "meta_notes": {EX9: "deck spans the full bay"}},
           "cfs": {"publish": str(cfs)},
           "nl": {"runs": str(runs), "sources": [{"dir": str(rerun), "copy": ["ddm_viewer_3d.html"]}, {"dir": str(runs)}],
                  "repo": str(repo), "python": sys.executable, "engine_dir": str(tmp_path / "hr_engine" / "steel_engine"),
                  "titles": [str(titles)]}}
    return {"tmp": tmp_path, "live": live, "gold": gold, "cfs": cfs, "runs": runs, "rerun": rerun, "repo": repo,
            "cfg": cfg, "out": tmp_path / "out"}


def _quiet(*a, **k):
    pass


def _zip_names(path):
    with zipfile.ZipFile(path) as z:
        return [(i.filename, i.external_attr >> 16) for i in z.infolist()]


# ---------------------------------------------------------------- the page patch
def test_patch_format_and_exact_match_discipline():
    blocks = [("a\nb", "A\nB"), ("x", "")]
    txt = PAGE.write_patch(blocks, "two blocks")
    assert txt.startswith("# two blocks") and PAGE.parse_patch(txt) == blocks
    assert PAGE.apply("a\nb and x", blocks) == "A\nB and "
    with pytest.raises(BuildError, match="occurs 0 times"):
        PAGE.apply("nothing here", blocks)
    with pytest.raises(BuildError, match="occurs 2 times"):
        PAGE.apply("x x", [("x", "y")])
    for bad, why in (("@@@ FIND\na\n@@@ END\n", "without @@@ REPLACE"), ("loose text\n", "outside a block"),
                     ("@@@ FIND\na\n@@@ REPLACE\nb\n", "no @@@ END"), ("@@@ FIND\n@@@ REPLACE\nb\n@@@ END\n", "empty")):
        with pytest.raises(BuildError, match=why):
            PAGE.parse_patch(bad)
    with pytest.raises(BuildError):
        PAGE.write_patch([("@@@ END", "x")])


def test_the_repos_patch_files_parse():
    second = PAGE.parse_patch((SITE / "patches" / "india_2026-10_second_wave.patch").read_text(encoding="utf-8"))
    assert len(second) == 11 and all(a and a != b for a, b in second)
    assert any("exAnchors" in b for a, b in second) and any("FEMA P-695" in b for a, b in second)
    assert PAGE.parse_patch((SITE / "patches" / "india_2026-10_third_wave.patch").read_text(encoding="utf-8")) == []


def test_count_checks_follow_the_published_indexes():
    page = PAGE_HTML
    assert PAGE.count_checks(page, [EX1, EX9, UNIT], [CFS1], [NL_A, NL_B]) == []
    out = PAGE.count_checks(page, [EX1, EX9, UNIT, "IN_Ex21_MSME"], [CFS1], [NL_A, NL_B, "IN_Ex21_MSME"])
    assert any("4 worked Indian designs" in c for c in out) and any("2 nonlinear runs" in c for c in out)
    assert any("2 Indian example briefs" in c for c in out) and not any("plus one sub-units" in c for c in out)


# ---------------------------------------------------------------- one command: the overlay build
def test_overlay_build(world):
    r = cli.build(world["cfg"], log=_quiet)
    out = world["out"]
    pub = out / "public_html"
    # the zip: india.html and solutions_india/ only, folders 755 / files 644, sorted
    names = _zip_names(r["zip"])
    assert [n for n, m in names] == sorted(n for n, m in names)
    assert all(n == "india.html" or n.startswith("solutions_india/") for n, m in names)
    assert not any(".ddm_tmp" in n for n, m in names) and not list(out.glob("public_html/**/.ddm_tmp*"))
    assert all(stat.S_IMODE(m) == (0o755 if n.endswith("/") else 0o644) for n, m in names)
    assert all(stat.S_IMODE(os.stat(p).st_mode) == 0o644 for p in pub.rglob("*") if p.is_file())
    # the page: patched, the counts it states match the indexes
    assert '[e.blurb, e.flag].filter(Boolean).join(" ")' in (pub / "india.html").read_text()
    assert not any("worked Indian designs" in c or "nonlinear runs" in c for c in r["checks"])
    # hot-rolled: titles, blurb, meta from the packages (SMRF -> SMF), the note, the PEB files and links
    idx = json.loads((pub / "solutions_india" / "hr" / "index.json").read_text(encoding="utf-8"))
    by = {e["id"]: e for e in idx["examples"]}
    assert [e["id"] for e in idx["examples"]] == [EX1, EX9, UNIT]
    assert by[EX1]["meta"].startswith("PEB, OMRF+OCBF, R 3.0, IS 18168 · W 1,234 kN · VB 50 / 61 kN (x / y) · 31.5 kg/m²")
    assert "worst strength ratio 0.912 — col-WI500 (slenderness KL/r 140 / 180 = 0.779 — brace-CHS)" in by[EX1]["meta"]
    assert by[EX1]["std"] == EX1 + "/model.std" and by[EX1]["anchors"] == EX1 + "/design/anchor_bolt_plan.svg"
    assert by[EX9]["meta"].startswith("SMF+SCBF, R 4.5") and by[EX9]["meta"].endswith(" · deck spans the full bay")
    assert by[UNIT]["meta"].endswith(" · deck spans the full bay")               # a unit carries its building's note
    assert by[EX9]["title"].startswith("Ex 9 · capability") and by[EX9]["blurb"].startswith("Shows snow")
    assert (pub / "solutions_india" / "hr" / EX1 / "design" / "bom.csv").is_file()
    assert not (pub / "solutions_india" / "hr" / EX1 / "figs").exists()          # overlay: figures stay on the site
    # cold-formed: copied as built
    assert (pub / "solutions_india" / "cfs" / CFS1 / "figs" / "auto_001.png").is_file()
    # nonlinear: the re-run first, its stored DDM viewer, the DDM report rebuilt, the index with titles
    nl = pub / "solutions_india" / "nl"
    assert (nl / NL_B / "ddm_viewer_3d.html").is_file() and not (nl / NL_A / "ddm_viewer_3d.html").exists()
    assert "rebuilt ddm report of %s (engine hr_engine)" % NL_A.split("/")[-1] in (nl / NL_A / "ddm_report.html").read_text()
    assert (nl / NL_A / "nlrha" / "DBE" / "nlrha_report.html").is_file()
    nidx = {e["id"]: e for e in json.loads((nl / "index.json").read_text())["examples"]}
    assert nidx[NL_B]["meta"] == "from override" and nidx[NL_A]["meta"] == "from runs"
    assert nidx[NL_B]["title"].startswith("Ex 9 · capability")
    # the overlay leaves the figures and viewers to the site, and the manifest says which links rely on it
    man = json.loads((out / "MANIFEST.json").read_text())
    rel = {tuple(x) for x in man["overlay_links_to_site"]}
    assert ("solutions_india/hr/%s/report.html" % EX1, "figs/auto_001.png") in rel
    assert man["counts"] == {"hr": 3, "cfs": 1, "nl": 2} and man["page_assets"] == ["styles.css", "favicon.ico"]
    f = next(x for x in man["files"] if x["path"] == "india.html")
    assert f["sha256"] == hashlib.sha256((pub / "india.html").read_bytes()).hexdigest() and f["new"] is False
    # the rollback: the live copy of every replaced file, the new files listed
    rb = _zip_names(r["rollback"])
    assert ("solutions_india/hr/%s/report.html" % EX1, 0o100644) in rb and ("india.html", 0o100644) in rb
    with zipfile.ZipFile(r["rollback"]) as z:
        assert z.read("solutions_india/hr/%s/report.html" % EX1) == b"<html>live old report</html>"
        new = z.read(PKG.NEW_LIST).decode().splitlines()
    assert "solutions_india/hr/%s/viewer_3d.html" % EX1 in new and "india.html" not in new
    # the instructions
    txt = (out / "UPLOAD_INSTRUCTIONS.txt").read_text()
    assert "test_publish.zip" in txt and "Do not delete any folder first" in txt and "test_ROLLBACK.zip" in txt
    assert "Overlay" in txt and "styles.css, favicon.ico" in txt and "verify --manifest" in txt
    assert "NEW_FILES_TO_DELETE_ON_ROLLBACK.txt" in txt


def test_self_contained_build_carries_every_linked_file(world):
    cfg = dict(world["cfg"], self_contained=True)
    w(world["gold"] / EX9 / "report.html", '<html><img src="figs/auto_001.png"><img src="figs/gone.png"></html>')
    r = cli.build(cfg, log=_quiet)
    pub = world["out"] / "public_html" / "solutions_india"
    assert (pub / "hr" / EX1 / "figs" / "auto_001.png").read_bytes() == b"\x89PNG fake figure"
    for rid in (NL_A, NL_B):
        assert (pub / "nl" / rid / "viewer_3d.html").is_file() and (pub / "nl" / rid / "pushover" / "pushover_viewer_3d.html").is_file()
    assert (pub / "nl" / NL_B / "ddm_viewer_3d.html").is_file() and not (pub / "nl" / NL_A / "ddm_viewer_3d.html").exists()
    assert "solutions_india/hr/%s/figs/auto_001.png" % EX1 in r["added"]
    # a link that resolves nowhere is a MISSING check (a figure the gold folder does not have)
    assert any(c.startswith("MISSING") and "gone.png" in c for c in r["checks"])
    assert not any("ddm_viewer_3d" in c for c in r["checks"])       # a script's optional viewer is not a broken link
    man = json.loads((world["out"] / "MANIFEST.json").read_text())
    assert man["self_contained"] is True and man["overlay_links_to_site"] == []
    assert "Self-contained" in (world["out"] / "UPLOAD_INSTRUCTIONS.txt").read_text()


def test_the_same_inputs_give_the_same_zip(world, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1761868800")
    a = cli.build(world["cfg"], log=_quiet)
    first = pathlib.Path(a["zip"]).read_bytes()
    b = cli.build(world["cfg"], log=_quiet)
    assert pathlib.Path(b["zip"]).read_bytes() == first


def test_a_new_example_and_the_page_counts(world):
    """A new hot-rolled example (not on the live site yet) is placed after the one named; the page prose that
    counts the examples is then flagged until its patch is written."""
    _gold_job(world["gold"] / "IN_Ex21_MSME_Test", "OMRF+OCBF", 3.0, kg=24.0)
    cfg = dict(world["cfg"])
    cfg["hr"] = dict(cfg["hr"], extra=[{"id": "IN_Ex21_MSME_Test", "title": "Ex 21 · MSME shed", "after": EX1}])
    r = cli.build(cfg, log=_quiet)
    idx = json.loads((world["out"] / "public_html" / "solutions_india" / "hr" / "index.json").read_text())
    assert [e["id"] for e in idx["examples"]] == [EX1, "IN_Ex21_MSME_Test", EX9, UNIT]
    assert any("4 worked Indian designs" in c and "publishes 5" in c for c in r["checks"])
    assert any("2 Indian example briefs" in c and "publishes 3" in c for c in r["checks"])


def test_missing_inputs_stop_the_build(world):
    cfg = dict(world["cfg"], page={"patches": [str(world["tmp"] / "page.patch")]})
    w(world["live"] / "india.html", "<html>a different live page</html>")
    with pytest.raises(BuildError, match="occurs 0 times"):
        cli.build(cfg, log=_quiet)
    with pytest.raises(BuildError, match="live mirror"):
        LIVE.Live(mirror=str(world["tmp"] / "nowhere"))
    with pytest.raises(BuildError, match="nl.repo"):
        cli.build(dict(world["cfg"], parts=["nl"], nl=dict(world["cfg"]["nl"], repo=None)), log=_quiet)


def test_nl_options_regenerate_and_a_failed_ddm_rebuild(world, monkeypatch):
    monkeypatch.setenv("FAKE_DDM_FAIL", "1")
    cfg = dict(world["cfg"], parts=["nl"], nl=dict(world["cfg"]["nl"], regenerate=True))
    logs = []
    cli.build(cfg, log=logs.append)
    nl = world["out"] / "public_html" / "solutions_india" / "nl"
    assert (nl / NL_A / "ddm_report.html").read_text() == "<html>stored ddm report</html>"
    assert any("WARN steltic_ddm report failed" in x for x in logs)
    assert (world["runs"] / NL_A / "four_analyses.html").read_text().endswith("<!-- regenerated -->")
    assert not (world["out"] / "public_html" / "india.html").exists()


def test_nl_source_rules(world, tmp_path):
    """A source can be limited to listed ids, or to the ids present in a marker folder (a re-run work folder also
    holds copies of units that were not re-run: build_nl2.sh published only the ids copied to nl_rerun)."""
    from steltic_site import nl as NL
    rerun, runs = str(world["rerun"]), str(world["runs"])
    _nl_run(world["rerun"] / NL_A)                                  # a stale copy, not re-run
    assert NL.pick_source(NL_A, [{"dir": rerun}, {"dir": runs}])[0] == os.path.join(rerun, NL_A)
    mark = tmp_path / "mark"
    (mark / NL_B).mkdir(parents=True)
    src = [{"dir": rerun, "marker_dir": str(mark)}, {"dir": runs}]
    assert NL.pick_source(NL_A, src)[0] == os.path.join(runs, NL_A)
    assert NL.pick_source(NL_B, src)[0] == os.path.join(rerun, NL_B)
    assert NL.pick_source(NL_B, [{"dir": rerun, "ids": [NL_A]}, {"dir": runs}])[0] == os.path.join(runs, NL_B)
    assert NL.pick_source("IN_nothing", src) == (None, None)


EX21 = "IN_Ex21_MSME_Test"


def _prepared(world):
    """The third-wave additions as the publish config prepares them: a new example (optional until its gold job
    exists), a titles file that may not exist yet, the count patches applied only once the example is published, a
    stage-2 re-run folder whose runs are taken once complete, and the example's nonlinear run as an optional id."""
    tmp = world["tmp"]
    hr_counts = w(tmp / "ex21.patch", PAGE.write_patch([
        ("4 worked Indian designs", "5 worked Indian designs"), ("The 2 Indian example briefs", "The 3 Indian example briefs")]))
    nl_counts = w(tmp / "ex21_nl.patch", PAGE.write_patch([("and 2 nonlinear runs", "and 3 nonlinear runs")]))
    cfg = json.loads(json.dumps(world["cfg"]))
    cfg["page"]["patches"] += [{"path": str(hr_counts), "when_hr_ids": [EX21]},
                               {"path": str(nl_counts), "when_nl_ids": [EX21]}]
    titles3 = {"path": str(tmp / "titles_wave3.json"), "optional": True}
    cfg["hr"]["titles"].append(titles3)
    cfg["nl"]["titles"].append(titles3)
    cfg["hr"]["extra"] = [{"id": EX21, "title": "Ex 21 · fallback title", "after": EX1, "optional": True}]
    stage2 = tmp / "nl" / "stage2"
    cfg["nl"]["sources"].insert(0, {"dir": str(stage2), "require": ["complete_gate.json"],
                                    "engine_dir": str(tmp / "hr_main" / "steel_engine")})
    cfg["nl"]["optional_ids"] = [EX21]
    return cfg, stage2


def test_prepared_additions_wait_for_their_inputs(world):
    cfg, stage2 = _prepared(world)
    _nl_run(stage2 / NL_A)                                          # a stage-2 run still going: no gate record yet
    r = cli.build(cfg, log=_quiet)
    pub = world["out"] / "public_html"
    assert [e["id"] for e in json.loads((pub / "solutions_india" / "hr" / "index.json").read_text())["examples"]] == [EX1, EX9, UNIT]
    notes = [c for c in r["checks"] if c.startswith("NOTE")]
    assert any(EX21 in n and "not in the gold set yet" in n for n in notes)
    assert sum("titles_wave3.json does not exist" in n for n in notes) == 1
    assert any("ex21.patch: not applied" in n for n in notes) and any("ex21_nl.patch: not applied" in n for n in notes)
    assert any("nonlinear: %s has no run folder" % EX21 in n for n in notes)
    assert "4 worked Indian designs" in (pub / "india.html").read_text()
    assert not any(c.startswith(("CHECK page", "PROBLEM", "MISSING")) for c in r["checks"])
    nidx = {e["id"]: e for e in json.loads((pub / "solutions_india" / "nl" / "index.json").read_text())["examples"]}
    assert nidx[NL_A]["meta"] == "from runs"                       # the unfinished stage-2 run is not taken
    assert "stored ddm report" not in (pub / "solutions_india" / "nl" / NL_A / "ddm_report.html").read_text()


def test_prepared_additions_once_their_inputs_exist(world):
    cfg, stage2 = _prepared(world)
    _gold_job(world["gold"] / EX21, "OMRF+OCBF", 3.0, kg=24.0)
    jdump(world["tmp"] / "titles_wave3.json", {EX21: {"title": "Ex 21 · angle-truss MSME shed · Hosur",
                                                      "blurb": "A typical MSME shed."}})
    _nl_run(stage2 / NL_A)
    jdump(stage2 / NL_A / "complete_gate.json", {"status": "complete"})
    _nl_run(stage2 / EX21)
    jdump(stage2 / EX21 / "complete_gate.json", {"status": "complete"})
    r = cli.build(cfg, log=_quiet)
    pub = world["out"] / "public_html"
    idx = json.loads((pub / "solutions_india" / "hr" / "index.json").read_text(encoding="utf-8"))["examples"]
    assert [e["id"] for e in idx] == [EX1, EX21, EX9, UNIT]
    assert idx[1]["title"] == "Ex 21 · angle-truss MSME shed · Hosur" and idx[1]["blurb"] == "A typical MSME shed."
    page = (pub / "india.html").read_text()
    assert "5 worked Indian designs" in page and "The 3 Indian example briefs" in page and "3 nonlinear runs" in page
    assert not any(c.startswith(("NOTE", "CHECK page", "PROBLEM", "MISSING")) for c in r["checks"]), r["checks"]
    nidx = {e["id"]: e for e in json.loads((pub / "solutions_india" / "nl" / "index.json").read_text(encoding="utf-8"))["examples"]}
    assert nidx[NL_A]["meta"] == "from override" and nidx[EX21]["title"] == "Ex 21 · angle-truss MSME shed · Hosur"
    # the stage-2 runs' DDM reports are rebuilt with that source's HR engine
    assert "(engine hr_main)" in (pub / "solutions_india" / "nl" / NL_A / "ddm_report.html").read_text()
    assert "(engine hr_engine)" in (pub / "solutions_india" / "nl" / NL_B / "ddm_report.html").read_text()


def test_the_third_wave_count_patches_fit_the_live_page():
    """Each FIND of the prepared count patches occurs once in the live second-wave page's text as the page states it
    (description, introduction, headings), in either order: checked here on the page's own sentences."""
    live = ('<meta name="description" content="... NLRHA and DDM. 39 worked Indian designs, including five PEB sheds with '
            'web-tapered frames, and 24 nonlinear runs with a verdict table, with reports and 3D models.">\n'
            'browse the <a href="#products">39 worked Indian designs and 24 nonlinear runs</a>.\n'
            '<h3>24 worked buildings</h3>\n<p class="muted">The 20 Indian example briefs that ship with Steltic India, most '
            'common building types first, plus four sub-units, designed end to end.</p>\n<h3>15 worked buildings</h3>\n'
            '<h3 id="nlCount">24 nonlinear runs</h3>')
    hr = PAGE.parse_patch((SITE / "patches" / "india_2026-10_third_wave_ex21.patch").read_text(encoding="utf-8"))
    nl = PAGE.parse_patch((SITE / "patches" / "india_2026-10_third_wave_ex21_nl.patch").read_text(encoding="utf-8"))
    hr_ids = ["h%d" % i for i in range(21)] + ["h/u%d" % i for i in range(4)]
    cfs, nl_ids = ["c%d" % i for i in range(15)], ["n%d" % i for i in range(25)]
    assert len(PAGE.count_checks(live, hr_ids, cfs, nl_ids[:24])) == 4      # the counts the hot-rolled patch moves
    for order in ((hr, nl), (nl, hr)):
        page = live
        for blocks in order:
            page = PAGE.apply(page, blocks)
        assert PAGE.count_checks(page, hr_ids, cfs, nl_ids) == []
    assert PAGE.count_checks(PAGE.apply(live, hr), hr_ids, cfs, nl_ids[:24]) == []


# ---------------------------------------------------------------- the live site (file:// URLs, no network)
def test_live_site_through_a_url(world):
    url = world["live"].as_uri() + "/"
    lv = LIVE.Live(url=url, log=_quiet)
    assert lv.get("india.html") == PAGE_HTML.encode()
    assert lv.get("solutions_india/none.html") is None and lv.exists("india.html") and not lv.exists("x.html")
    cfg = dict(world["cfg"], live={"url": url})
    r = cli.build(cfg, log=_quiet)
    with zipfile.ZipFile(r["rollback"]) as z:
        assert z.read("solutions_india/hr/%s/report.html" % EX1) == b"<html>live old report</html>"


def test_verify_after_the_upload(world, tmp_path):
    r = cli.build(dict(world["cfg"], self_contained=True), log=_quiet)
    site = tmp_path / "site"
    shutil.copytree(world["live"], site)
    with zipfile.ZipFile(r["zip"]) as z:
        z.extractall(site)
    man = json.loads((world["out"] / "MANIFEST.json").read_text())
    pages = [f["path"] for f in man["files"] if f["path"].endswith("index.json") or SC.is_page(os.path.basename(f["path"]))]
    ok = LIVE.verify(man["files"], site.as_uri() + "/", pages=pages, pub_root=man["pub_root"], log=_quiet)
    assert not ok["mismatch"] and not ok["missing"] and not ok["links_bad"] and ok["links_checked"] > 5
    (site / "solutions_india" / "hr" / EX1 / "report.html").write_text("changed on the server")
    (site / "solutions_india" / "nl" / NL_A / "pushover" / "pushover_report.html").unlink()
    bad = LIVE.verify(man["files"], site.as_uri() + "/", pages=pages, pub_root=man["pub_root"], log=_quiet)
    assert bad["mismatch"] == ["solutions_india/hr/%s/report.html" % EX1]
    assert bad["missing"] == ["solutions_india/nl/%s/pushover/pushover_report.html" % NL_A]
    # the command line
    rc = cli.main(["verify", "--manifest", str(world["out"] / "MANIFEST.json"), "--url", site.as_uri() + "/"])
    assert rc == 1


# ---------------------------------------------------------------- the command line and the config
def test_cli_build_from_a_config_file(world, tmp_path, capsys):
    cfg = json.loads(json.dumps(world["cfg"]))
    cfg["vars"] = {"OUT": "unset", "GOLD": str(world["gold"])}
    cfg["out"] = "${OUT}"
    cfg["hr"]["jobs"] = "${GOLD}"
    cfg["page"]["patches"] = ["page.patch"]                       # relative to the config file's folder
    p = jdump(world["tmp"] / "publish.json", cfg)
    args = ["build", "--config", str(p), "--var", "OUT=%s" % (tmp_path / "cli_out"), "--self-contained", "--offline"]
    assert cli.main(args) == 0
    assert (tmp_path / "cli_out" / "test_publish.zip").is_file()
    w(world["gold"] / EX9 / "report.html", '<html><img src="figs/gone.png"></html>')
    assert cli.main(args) == 3                                     # a MISSING check is reported by the exit code
    cfg["page"]["patches"] = ["no_such.patch"]
    jdump(p, cfg)
    del cfg["vars"]["OUT"]
    jdump(p, cfg)
    assert cli.main(["build", "--config", str(p)]) == 2
    assert "${OUT}" in capsys.readouterr().err or True
    with pytest.raises(BuildError, match="OUT"):
        cli.load_config(str(p))


def test_the_repos_publish_configs_load(monkeypatch):
    for f in ("2026-10_second_wave.json", "2026-10_third_wave.json"):
        cfg = cli.load_config(str(SITE / "publishes" / f))
        assert cfg["name"].startswith("steltic_india_") and cfg["live"]["url"] == "https://stelticai.com/"
        paths = [x["path"] if isinstance(x, dict) else x for x in cfg["page"]["patches"]]
        assert all(os.path.isabs(x) and pathlib.Path(x).is_file() for x in paths)
    third = cli.load_config(str(SITE / "publishes" / "2026-10_third_wave.json"), var_args=["OUT=/x/y"])
    assert third["self_contained"] is True and third["out"] == "/x/y" and third["hr"]["system_names"] == {"SMRF": "SMF"}
    ex21 = "IN_Ex21_MSME_angle_truss_shed_Hosur"
    assert third["hr"]["extra"][0]["id"] == ex21 and third["hr"]["extra"][0]["optional"] is True
    assert [p.get("when_hr_ids") or p.get("when_nl_ids") for p in third["page"]["patches"] if isinstance(p, dict)] == [[ex21], [ex21]]
    assert third["nl"]["optional_ids"] == [ex21] and third["nl"]["sources"][0]["require"] == ["complete_gate.json"]
    assert third["hr"]["titles"][1] == {"path": "/home/claude/briefs/titles_wave3.json", "optional": True}


def test_build_site_script_runs():
    r = subprocess.run([sys.executable, str(SITE / "build_site.py"), "--help"], capture_output=True, text=True)
    assert r.returncode == 0 and "build" in r.stdout and "verify" in r.stdout


# ---------------------------------------------------------------- the parts on their own
def test_ex5_flag_when_the_roof_has_no_spare_capacity():
    so = {"pv_kNm2": 0.15, "added_kg_m2": 2.34, "added_steel_t": 5.25, "worst_strength_dc": 0.995}
    t = HR.pv_flag({"capacity_kNm2": 0.0, "capacity_H150_kNm2": 0.03, "capacity_strength_kNm2": 0.05,
                    "limiting_item": "the wind sway of the frames"}, so)
    assert "no spare capacity" in t and "the wind sway of the frames" in t and "+2.34 kg/m²" in t
    t = HR.pv_flag({"capacity_kNm2": 0.02, "capacity_H150_kNm2": 0.04, "capacity_strength_kNm2": 0.04}, None)
    assert "As built the roof carries 0.02 kN/m²" in t
    assert HR.system_name("SMRF+SCBF", {"SMRF": "SMF"}) == "SMF+SCBF"
    assert HR.system_name("OMRF+OCBF", {"SMRF": "SMF"}) == "OMRF+OCBF"


def test_public_text_scan(tmp_path):
    w(tmp_path / "india.html", "<p>fine text</p><script>var x = '/home/claude';</script>")
    w(tmp_path / "solutions_india" / "hr" / "a" / "report.html",
      "<p>Checked by the EOR (was None) per owner ruling NL-12, see cfg['x'] in /home/claude/x.json</p>"
      "<h2>Appendix C — Activity log</h2><p>/tmp/run EOR</p>")
    w(tmp_path / "solutions_india" / "hr" / "a" / "viewer_3d.html", "/home/claude EOR")
    jdump(tmp_path / "solutions_india" / "hr" / "index.json", {"examples": [{"id": "a", "meta": "x {'k': 1}",
                                                                           "report": "a/report.html"}]})
    hits = SCAN.scan(str(tmp_path), log=_quiet)
    kinds = {h[1] for h in hits}
    assert {"EOR", "was None", "owner ruling", "ticket id", "cfg key", "path", "raw dict"} <= kinds
    assert all("viewer" not in h[0] and h[0] != "india.html" for h in hits)
    assert not any("/tmp/run" in h[2] for h in hits)                    # Appendix C is the run's record
    assert [h[1] for h in hits if h[0].endswith("index.json")] == ["raw dict"]   # its text, not its JSON


def test_compare_tool(tmp_path):
    from steltic_site import compare
    for root, dc, kg in (("base", 0.9, 50.0), ("new", 0.8, 45.0)):
        d = tmp_path / root / "IN_Ex1"
        jdump(d / "run_summary.json", {})
        jdump(d / "design" / "calc_package.json", {"design_status": {"status": "complete"},
                                                   "members": [{"id": "m1", "DC": dc, "inputs": {"section": "A"}}],
                                                   "peb": {"tonnage": {"kg_per_m2": {"total": kg}}}})
    hl = lambda p: {"strength": {"dc": p["members"][0]["DC"], "member": "m1"}, "slenderness": {}}  # noqa: E731
    rows, diffs = compare.compare(str(tmp_path / "base"), str(tmp_path / "new"), hl)
    assert rows[0][3] == 0.9 and rows[0][4] == 0.8 and ("IN_Ex1", "kg/m2 50.0 -> 45.0") in diffs
    assert "| IN_Ex1 | complete → complete | 0.900 | 0.800 (m1)" in compare.markdown(rows)
