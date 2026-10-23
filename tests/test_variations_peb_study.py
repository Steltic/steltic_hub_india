"""PEB lever study from the Variations tab (2026-10 second wave): the Variations server runs HR Steel's deterministic
lever study (steltic_india scripts/peb_lever_study.py) in HR Steel's checkout and interpreter, which the hub resolves
through its registry (manifest `uses` -> STELTIC_INDIA_DIR / STELTIC_INDIA_PYTHON / STELTIC_CFS_DIR).

No real analysis runs here: HR Steel's checkout is tests/fixtures/fake_steltic_india, a FAKE runner with the real
runner's module API (JOB_SPECS, LABELS, VARIANT_ORDER, label, variants_for -- the same variant names and status
strings) and command line, which prints the runner's progress lines and writes study.json / .csv / .html.

One optional test reads the real runner's variant list (variants_for is pure logic: nothing is analysed) when
STELTIC_INDIA_CHECKOUT names a steltic_india checkout that has scripts/peb_lever_study.py.

    python -m pytest tests/test_variations_peb_study.py -q
"""
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import time
import zipfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MOD = ROOT / "steltic_hub" / "catalog" / "steltic_variations"
FAKE = pathlib.Path(__file__).resolve().parent / "fixtures" / "fake_steltic_india"
sys.path.insert(0, str(MOD))
os.environ.setdefault("VARIATIONS_JOBS", tempfile.mkdtemp(prefix="variations-test-"))
os.environ.setdefault("STELTIC_URL", "http://127.0.0.1:1")

from variations import main as vm, peb_study as peb   # noqa: E402

EX1 = "IN_Ex1_PEB_factory_shed_tapered_Bengaluru"
EX4 = "IN_Ex4_PEB_warehouse_SMRF_Zone4_NCR"


# ---------------------------------------------------------------- fixtures
def _job_folder(d: pathlib.Path):
    (d / "secondary").mkdir(parents=True, exist_ok=True)
    (d / "design").mkdir(parents=True, exist_ok=True)
    (d / "cfg.py").write_text('COL_T = "TI300-1000X250X14X6"\nRAF_T = "TI1000-550X250X14X6"\n')
    (d / "secondary" / "secondary_is801.py").write_text('PURLIN = "LZ200x60x20x2.0"\nGIRT = "LZ200x60x20x2.0"\nFY = 240\n')
    (d / "design" / "calc_package.json").write_text(json.dumps({"peb": {"tonnage": {"kg": {"primary": 1.0}, "plan_area_m2": 1.0}}}))


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A hub as the Variations server sees it: the projects folder, HR Steel's checkout (the fake runner) and
    interpreter, CFS Steel's checkout -- the variables the hub sets from the manifest's `uses`."""
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    co = tmp_path / "steltic_india"
    shutil.copytree(FAKE, co)
    cfs = tmp_path / "steltic_CFS_india"
    (cfs / "steel_engine").mkdir(parents=True)
    monkeypatch.setattr(vm, "JOBS", jobs)
    monkeypatch.setattr(vm, "STELTIC_URL", "")
    monkeypatch.setenv("STELTIC_INDIA_DIR", str(co))
    monkeypatch.setenv("STELTIC_INDIA_PYTHON", sys.executable)
    monkeypatch.setenv("STELTIC_CFS_DIR", str(cfs))
    monkeypatch.setenv("INDIA_CORPUS_ROOT", str(tmp_path / "grokbot"))
    for k in ("FAKE_STUDY_SLEEP", "FAKE_STUDY_FAIL", "FAKE_OLD_RUNNER"):
        monkeypatch.delenv(k, raising=False)
    yield {"jobs": jobs, "co": co, "cfs": cfs, "tmp": tmp_path}
    run = peb.current()
    if run is not None and not run.finished:
        run.stop()
        run.join(timeout=20)
    peb._CURRENT = None


@pytest.fixture
def client(env):
    from fastapi.testclient import TestClient
    return TestClient(vm.app)


def _wait(timeout=30.0):
    run = peb.current()
    assert run is not None
    run.join(timeout=timeout)
    assert run.finished, "the study did not finish"
    return run


def _events(client, project, since=0):
    txt = client.get(f"/api/project/{project}/peb-study/events?since={since}").text
    return [json.loads(b[5:].strip()) for b in txt.split("\n\n") if b.startswith("data:")]


# ---------------------------------------------------------------- the runner's code status, its selection rules
def test_compliance_reads_the_runners_status_strings():
    """The labels mirror peb_lever_study._tag on the runner's own status strings."""
    c = peb.compliance
    assert c("compliant") == "compliant" and c("permitted") == "compliant"
    assert c("published (owner rulings)") == "compliant" and c("strict rules") == "compliant"
    assert c("not compliant") == "not compliant"
    assert c("market practice, not compliant") == "not compliant"
    assert c("NOT COMPLIANT (market practice)") == "not compliant"
    assert c("reading rejected by ruling O1") == "reading"
    assert c("old treatment (unconservative)") == "reading"
    assert c("conflict: permitted by IS 800 12.10.1.1, not by IS 1893 Table 9 Note 1") == "reading"
    assert c("n/a") == "n/a"


def _meta(env, job):
    pr = peb.probe(peb.paths(), job)
    return pr["job"]["variants"]


def test_probe_reads_the_runners_variants_in_its_interpreter(env):
    p = peb.paths()
    pr = peb.probe(p, EX1)
    assert pr["jobs"] == sorted([EX1, EX4])
    job = pr["job"]
    assert job["zone"] == "II" and job["route"] == "OMRF" and job["dado_height_mm"] == 3000.0 and job["solar"] is True
    by = {v["name"]: v for v in job["variants"]}
    assert by["strict"]["applies"] and by["strict"]["compliance"] == "compliant"
    assert by["omrf_r3"]["applies"] is False and by["omrf_r3"]["compliance"] == "n/a"      # already OMRF
    assert by["rods"]["compliance"] == "not compliant" and by["market"]["compliance"] == "not compliant"
    assert by["base_key_analysis"]["compliance"] == "reading"
    assert by["cum:ltb_gradient"]["alias"] == "ltb_gradient"           # the first ladder step copies the lever alone
    assert by["compliant"]["alias"] == "cum:thin_purlins"
    assert by["solar_ready"]["applies"]
    assert by["ltb_gradient"]["label"] == "LTB with the moment-gradient C1"
    # an SMRF Zone IV job: no deliverable-moment joints, no rods; the OMRF route is market practice
    by4 = {v["name"]: v for v in peb.probe(p, EX4)["job"]["variants"]}
    assert not by4["joint_deliverable"]["applies"] and not by4["rods"]["applies"]
    assert by4["omrf_r3"]["compliance"] == "not compliant" and "solar_ready" not in by4
    assert peb.probe(p, "Somebody_Elses_Job")["job"] is None
    assert not list((env["co"] / "scripts").glob("__pycache__/*")), "the probe wrote bytecode into HR Steel's checkout"
    # the switches are read from the runner's own --help
    cli = pr["cli"]
    assert {"--job", "--variants", "--resume", "--solar", "--ladder", "--stop-after-min"} <= set(cli["flags"])
    assert cli["ladders"] == ["full", "short"] and cli["stop_after_min"] is True
    short = {v["name"]: v for v in job["variants_by_ladder"]["short"]}
    assert not any(n.startswith("cum:") for n in short) and short["compliant"]["start_from"] == "strict"
    assert short["market"]["start_from"] == "compliant" and short["market"]["compliance"] == "not compliant"
    assert job["variants"] == job["variants_by_ladder"]["full"]


def test_probe_reads_an_older_runner_without_the_second_wave_switches(env, monkeypatch):
    """The runner of the first PEB variations release: no --ladder, no --stop-after-min, variants_for(spec, solar)."""
    monkeypatch.setenv("FAKE_OLD_RUNNER", "1")
    (env["co"] / "scripts" / "peb_lever_study.py").touch()       # a new stamp: not the cached answer of the new runner
    pr = peb.probe(peb.paths(), EX1)
    assert pr["cli"]["ladders"] == ["full"] and pr["cli"]["stop_after_min"] is False
    assert "--ladder" not in pr["cli"]["flags"] and list(pr["job"]["variants_by_ladder"]) == ["full"]
    assert any(v["name"].startswith("cum:") for v in pr["job"]["variants"])


def test_selection_follows_the_runners_rules(env):
    vs = _meta(env, EX1)
    sel = peb.expand_selection
    assert sel(vs, []) == ["strict"]
    assert sel(vs, ["ltb_gradient", "rods"]) == ["strict", "ltb_gradient", "rods"]
    ladder = [v["name"] for v in vs if v["name"].startswith("cum:")]
    s = sel(vs, ["cum:rods"])                 # one step brings the whole ladder, its first lever and the compliant row
    assert set(ladder) <= set(s) and "ltb_gradient" in s and "compliant" in s and "market" not in s
    s = sel(vs, ["compliant"])                # the compliant row alone: the ladder up to the step it copies
    assert "cum:thin_purlins" in s and "cum:base_key_analysis" not in s and "ltb_gradient" in s
    assert peb.sized_runs(vs, s) == [n for n in s if n not in ("compliant", "cum:ltb_gradient")]
    with pytest.raises(ValueError, match="does not apply"):
        sel(vs, ["omrf_r3"])
    with pytest.raises(ValueError, match="not a variant"):
        sel(vs, ["bogus"])
    vs4 = _meta(env, EX4)
    with pytest.raises(ValueError, match="does not apply"):
        sel(vs4, ["rods"])
    # the short ladder: no steps; 'all compliant levers' is sized from the strict design (Ex1 has four compliant
    # levers); the market bundle on its own
    short = peb.probe(peb.paths(), EX1)["job"]["variants_by_ladder"]["short"]
    assert sel(short, ["compliant", "market"]) == ["strict", "compliant", "market"]
    assert peb.sized_runs(short, ["strict", "compliant", "market"]) == ["strict", "compliant", "market"]
    with pytest.raises(ValueError, match="not a variant"):
        sel(short, ["cum:rods"])


# ---------------------------------------------------------------- does it apply, and why not
def test_status_says_why_the_study_does_not_apply(env, client, monkeypatch):
    # HR Steel not installed: the hub leaves the variables unset
    monkeypatch.delenv("STELTIC_INDIA_DIR")
    s = client.get("/api/project/Shed/peb-study").json()
    assert s["is_peb_job"] is False and "HR Steel (IS 800) is not installed" in s["reasons"][0]
    # an HR Steel without the study runner (main before the PEB variations release)
    old = env["tmp"] / "old_checkout"
    (old / "scripts").mkdir(parents=True)
    monkeypatch.setenv("STELTIC_INDIA_DIR", str(old))
    s = client.get("/api/project/Shed/peb-study").json()
    assert "no PEB lever study" in s["reasons"][0]
    monkeypatch.setenv("STELTIC_INDIA_DIR", str(env["co"]))
    # a project with no job spec: says which jobs the runner has and how to add one
    s = client.get("/api/project/Shed/peb-study").json()
    assert s["is_peb_job"] is False and "No job spec named Shed" in s["reasons"][0] and EX1 in s["reasons"][0]
    assert "PEB_LEVERS.md" in s["reasons"][0]
    # a multistorey brief: not a PEB job at all
    client.post("/api/project/Tower/base", json={"brief": "8-storey office, SMRF, 3 bays x 4 bays, Zone IV"})
    s = client.get("/api/project/Tower/peb-study").json()
    assert s["is_peb_job"] is False and s["reasons"][0].startswith("Not a PEB job")
    r = client.post("/api/project/Tower/peb-study/run", json={})
    assert r.status_code == 400 and "Not a PEB job" in r.json()["detail"]
    howto = client.get("/api/peb-study/howto")
    assert howto.status_code == 200 and "peb_lever_study.py" in howto.text


def test_a_peb_job_without_cfs_steel_or_a_job_folder_is_not_ready(env, client, monkeypatch):
    monkeypatch.delenv("STELTIC_CFS_DIR")
    s = client.get(f"/api/project/{EX1}/peb-study").json()
    assert s["is_peb_job"] is True and s["ready"] is False
    assert any("CFS Steel (IS 801) is not installed" in r for r in s["reasons"])
    assert any("No job folder" in r for r in s["reasons"])        # no cfg.py in the project, no HR Steel server
    assert {v["name"] for v in s["job"]["variants"]} >= {"strict", "market", "cum:rods"}
    r = client.post(f"/api/project/{EX1}/peb-study/run", json={})
    assert r.status_code == 400 and "CFS Steel" in r.json()["detail"]


# ---------------------------------------------------------------- the run
def test_run_streams_progress_and_lists_the_outputs(env, client):
    _job_folder(env["jobs"] / EX1)
    s = client.get(f"/api/project/{EX1}/peb-study").json()
    assert s["ready"] is True and s["source"]["kind"] == "project", s["reasons"]
    r = client.post(f"/api/project/{EX1}/peb-study/run",
                    json={"variants": ["ltb_gradient", "rods", "market"], "max_runs": 3, "resume": True})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["variants"] == ["strict", "ltb_gradient", "rods", "market"] and d["expected"] == 4
    run = _wait()
    assert run.rc == 0 and run.seen == ["strict", "ltb_gradient", "rods", "market"]

    # the command the hub-resolved interpreter ran, in HR Steel's checkout
    call = json.loads((env["jobs"] / EX1 / "variations" / "peb_study" / "work" / "fake_calls.jsonl").read_text().splitlines()[-1])
    argv = call["argv"]
    val = lambda flag: argv[argv.index(flag) + 1]
    assert val("--job") == EX1 and val("--jobs-root") == str(env["jobs"]) and val("--published-root") == str(env["jobs"])
    assert val("--work") == str(env["jobs"] / EX1 / "variations" / "peb_study" / "work")
    assert val("--cfs-root") == str(env["cfs"]) and val("--max-runs") == "3"
    assert val("--variants") == "strict,ltb_gradient,rods,market"
    assert "--resume" in argv and "--solar" not in argv
    assert pathlib.Path(call["cwd"]).resolve() == env["co"].resolve()
    assert call["env"]["PYTHONHASHSEED"] == "0" and call["env"]["STELTIC_CFS_ROOT"] == str(env["cfs"])
    assert call["env"]["PYTHONDONTWRITEBYTECODE"] == "1" and call["env"]["INDIA_CORPUS_ROOT"].endswith("grokbot")

    # progress: one event per variant started, then every line, then finished
    evs = _events(client, EX1)
    var = [e for e in evs if e["type"] == "variant"]
    assert [e["name"] for e in var] == ["strict", "ltb_gradient", "rods", "market"]
    assert var[-1]["n"] == 4 and var[-1]["of"] == 4 and var[1]["label"] == "LTB with the moment-gradient C1"
    assert any(e["type"] == "log" and "local sizing" in e["text"] for e in evs)
    assert evs[-1]["type"] == "finished" and evs[-1]["status"] == "finished" and evs[-1]["rc"] == 0

    # the rows done and the outputs, with links that serve them
    s = client.get(f"/api/project/{EX1}/peb-study").json()
    assert s["running"] is False and s["run"]["status"] == "finished" and s["run"]["rc"] == 0
    rows = {r["name"]: r for r in s["rows"]}
    assert rows["strict"]["kg_m2"] == 30.0 and rows["ltb_gradient"]["delta_vs_strict_kg_m2"] == -0.5
    assert rows["rods"]["compliance"] == "not compliant" and rows["market"]["compliance"] == "not compliant"
    assert rows["ltb_gradient"]["governing"].startswith("D/C 0.981")
    names = [o["name"] for o in s["outputs"]]
    assert names[:3] == ["study.html", "study.csv", "study.json"] and "study.log" in names and "index.html" in names
    h = client.get(f"/api/project/{EX1}/peb-study/file/study.html")
    assert h.status_code == 200 and h.headers["content-type"].startswith("text/html") and EX1 in h.text
    assert client.get(f"/api/project/{EX1}/peb-study/file/study.csv").text.startswith("variant,label,status")
    assert json.loads(client.get(f"/api/project/{EX1}/peb-study/file/study.json").text)["job"] == EX1
    assert "== %s / strict" % EX1 in client.get(f"/api/project/{EX1}/peb-study/file/study.log").text
    assert client.get(f"/api/project/{EX1}/peb-study/file/cfg.py").status_code == 404           # outputs only
    assert client.get(f"/api/project/{EX1}/peb-study/file/..%2F..%2Fcfg.py").status_code == 404

    # the job folder is only read
    assert sorted(p.name for p in (env["jobs"] / EX1).iterdir()) == ["cfg.py", "design", "secondary", "variations"]

    # again with the rows kept: the done ones are reported as resumed, the new one is sized
    r = client.post(f"/api/project/{EX1}/peb-study/run", json={"variants": ["ltb_gradient", "thin_purlins"]})
    assert r.status_code == 200, r.text
    run = _wait()
    var = [e for e in run.events if e["type"] == "variant"]
    assert [(e["name"], e["resumed"]) for e in var] == [("strict", True), ("ltb_gradient", True), ("thin_purlins", False)]


def test_solar_ready_passes_the_solar_switch(env, client):
    _job_folder(env["jobs"] / EX1)
    r = client.post(f"/api/project/{EX1}/peb-study/run", json={"variants": ["solar_ready"]})
    assert r.status_code == 200, r.text
    _wait()
    call = json.loads((env["jobs"] / EX1 / "variations" / "peb_study" / "work" / "fake_calls.jsonl").read_text().splitlines()[-1])
    assert "--solar" in call["argv"] and call["argv"][call["argv"].index("--variants") + 1] == "strict,solar_ready"


def _last_call(env, job=EX1):
    return json.loads((env["jobs"] / job / "variations" / "peb_study" / "work" / "fake_calls.jsonl")
                      .read_text().splitlines()[-1])["argv"]


def test_short_ladder_and_a_resumed_study_keeps_its_ladder(env, client):
    _job_folder(env["jobs"] / EX1)
    r = client.post(f"/api/project/{EX1}/peb-study/run", json={"variants": ["compliant", "market"], "ladder": "short"})
    assert r.status_code == 200, r.text
    assert r.json()["variants"] == ["strict", "compliant", "market"] and r.json()["expected"] == 3
    run = _wait()
    assert run.rc == 0 and run.seen == ["strict", "compliant", "market"]
    argv = _last_call(env)
    assert argv[argv.index("--ladder") + 1] == "short"
    s = client.get(f"/api/project/{EX1}/peb-study").json()
    assert s["study"]["ladder"] == "short" and s["run"]["ladder"] == "short"
    # the rows belong to the short ladder: a resumed full-ladder run is refused, a fresh one is not
    r = client.post(f"/api/project/{EX1}/peb-study/run", json={"variants": ["cum:rods"], "ladder": "full"})
    assert r.status_code == 409 and "cannot switch ladder" in r.json()["detail"]
    r = client.post(f"/api/project/{EX1}/peb-study/run", json={"variants": ["ltb_gradient"], "ladder": "full", "resume": False})
    assert r.status_code == 200, r.text
    assert _wait().rc == 0
    assert client.post(f"/api/project/{EX1}/peb-study/run", json={"ladder": "long"}).status_code == 400


def test_a_time_budget_ends_a_resumable_chunk(env, client, monkeypatch):
    """--stop-after-min: the runner starts no new variant after the budget; the tab reports the chunk as paused and
    the next run with the rows kept continues where it stopped."""
    _job_folder(env["jobs"] / EX1)
    monkeypatch.setenv("FAKE_STUDY_SLEEP", "0.3")
    r = client.post(f"/api/project/{EX1}/peb-study/run",
                    json={"variants": ["ltb_gradient", "thin_purlins"], "stop_after_min": 0.002})
    assert r.status_code == 200, r.text
    run = _wait()
    argv = _last_call(env)
    assert argv[argv.index("--stop-after-min") + 1] == "0.002"
    assert run.rc == 0 and run.seen == ["strict"]
    s = client.get(f"/api/project/{EX1}/peb-study").json()
    assert s["run"]["status"] == "paused" and "time budget" in s["run"]["note"]
    assert s["study"]["stopped_before"] == "ltb_gradient"
    assert _events(client, EX1)[-1]["status"] == "paused"
    monkeypatch.setenv("FAKE_STUDY_SLEEP", "0")
    r = client.post(f"/api/project/{EX1}/peb-study/run", json={"variants": ["ltb_gradient", "thin_purlins"]})
    assert r.status_code == 200, r.text
    run = _wait()
    assert [(e["name"], e["resumed"]) for e in run.events if e["type"] == "variant"] == \
        [("strict", True), ("ltb_gradient", False), ("thin_purlins", False)]
    assert client.get(f"/api/project/{EX1}/peb-study").json()["run"]["status"] == "finished"
    assert client.post(f"/api/project/{EX1}/peb-study/run", json={"stop_after_min": -1}).status_code == 400


def test_an_older_runner_is_driven_without_the_new_switches(env, client, monkeypatch):
    monkeypatch.setenv("FAKE_OLD_RUNNER", "1")
    (env["co"] / "scripts" / "peb_lever_study.py").touch()
    _job_folder(env["jobs"] / EX1)
    s = client.get(f"/api/project/{EX1}/peb-study").json()
    assert s["cli"]["ladders"] == ["full"] and s["cli"]["stop_after_min"] is False
    r = client.post(f"/api/project/{EX1}/peb-study/run", json={"ladder": "short"})
    assert r.status_code == 400 and "no --ladder switch" in r.json()["detail"]
    r = client.post(f"/api/project/{EX1}/peb-study/run", json={"stop_after_min": 30})
    assert r.status_code == 400 and "no --stop-after-min" in r.json()["detail"]
    r = client.post(f"/api/project/{EX1}/peb-study/run", json={"variants": ["ltb_gradient"]})
    assert r.status_code == 200, r.text
    assert _wait().rc == 0
    argv = _last_call(env)
    assert "--ladder" not in argv and "--stop-after-min" not in argv


def test_one_study_at_a_time_and_stop_ends_the_process_tree(env, client, monkeypatch):
    _job_folder(env["jobs"] / EX1)
    _job_folder(env["jobs"] / EX4)
    monkeypatch.setenv("FAKE_STUDY_SLEEP", "30")
    r = client.post(f"/api/project/{EX1}/peb-study/run", json={"variants": ["ltb_gradient"]})
    assert r.status_code == 200, r.text
    run = peb.current()
    t0 = time.time()
    while not run.seen and time.time() - t0 < 20:
        time.sleep(0.05)
    assert run.seen == ["strict"]
    s = client.get(f"/api/project/{EX1}/peb-study").json()
    assert s["running"] is True and s["progress"]["variant"] == "strict" and s["progress"]["of"] == 2
    r = client.post(f"/api/project/{EX1}/peb-study/run", json={})
    assert r.status_code == 409 and "already running" in r.json()["detail"]
    r = client.post(f"/api/project/{EX4}/peb-study/run", json={})
    assert r.status_code == 409 and f"already running for {EX1}" in r.json()["detail"]
    assert client.get(f"/api/project/{EX4}/peb-study").json()["busy_with"] == EX1
    t0 = time.time()
    assert client.post(f"/api/project/{EX1}/peb-study/stop").json()["running"] is True
    run.join(timeout=15)
    assert run.finished and time.time() - t0 < 15
    assert run.proc.poll() is not None
    s = client.get(f"/api/project/{EX1}/peb-study").json()
    assert s["run"]["status"] == "stopped" and s["running"] is False
    assert _events(client, EX1)[-1]["status"] == "stopped"


def test_a_failing_runner_is_reported(env, client, monkeypatch):
    _job_folder(env["jobs"] / EX1)
    monkeypatch.setenv("FAKE_STUDY_FAIL", "ltb_gradient")
    assert client.post(f"/api/project/{EX1}/peb-study/run", json={"variants": ["ltb_gradient"]}).status_code == 200
    run = _wait()
    assert run.rc == 3
    s = client.get(f"/api/project/{EX1}/peb-study").json()
    assert s["run"]["status"] == "failed" and s["run"]["rc"] == 3
    assert [r["name"] for r in s["rows"]] == ["strict"]               # what was done before the failure is kept
    assert any("fake failure" in e.get("text", "") for e in _events(client, EX1))


def test_hr_steel_design_is_fetched_when_the_project_folder_has_no_job(env, client, monkeypatch):
    """No cfg.py in the project folder: HR Steel's design of this project is downloaded through its own API and
    unpacked under variations/peb_study/input/, which becomes the runner's --jobs-root."""
    src = env["tmp"] / "hrjob"
    _job_folder(src)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                z.write(p, f"{EX1}/{p.relative_to(src).as_posix()}")
        z.writestr(f"{EX1}/../escape.txt", "x")
    monkeypatch.setattr(vm, "STELTIC_URL", "http://127.0.0.1:1")
    calls = []
    monkeypatch.setattr(vm.hr, "download", lambda url, b: calls.append(b) or buf.getvalue())
    s = client.get(f"/api/project/{EX1}/peb-study").json()
    assert s["ready"] and s["source"]["kind"] == "hr" and "HR Steel's design" in s["source"]["label"]
    r = client.post(f"/api/project/{EX1}/peb-study/run", json={"variants": []})
    assert r.status_code == 200, r.text
    _wait()
    base = env["jobs"] / EX1 / "variations" / "peb_study"
    assert calls == [EX1] and (base / "input" / EX1 / "cfg.py").is_file()
    assert not (base / "input" / "escape.txt").exists() and not (env["jobs"] / EX1 / "escape.txt").exists()
    call = json.loads((base / "work" / "fake_calls.jsonl").read_text().splitlines()[-1])["argv"]
    assert call[call.index("--jobs-root") + 1] == str(base / "input")
    # the design changed since the rows were sized: keeping them is refused, starting again is not
    (src / "cfg.py").write_text('COL_T = "TI300-1100X250X14X6"\n')
    buf2 = io.BytesIO()
    with zipfile.ZipFile(buf2, "w") as z:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                z.write(p, f"{EX1}/{p.relative_to(src).as_posix()}")
    monkeypatch.setattr(vm.hr, "download", lambda url, b: buf2.getvalue())
    r = client.post(f"/api/project/{EX1}/peb-study/run", json={"resume": True})
    assert r.status_code == 409 and "has changed" in r.json()["detail"]
    r = client.post(f"/api/project/{EX1}/peb-study/run", json={"resume": False})
    assert r.status_code == 200, r.text
    _wait()
    # no design in HR Steel: says so
    def none(url, b):
        raise vm.hr.DesignError("no package for %s (404)" % b)
    monkeypatch.setattr(vm.hr, "download", none)
    r = client.post(f"/api/project/{EX1}/peb-study/run", json={"resume": False})
    assert r.status_code == 404 and "HR Steel has no design for this project" in r.json()["detail"]


def test_bad_requests_are_refused(env, client):
    _job_folder(env["jobs"] / EX1)
    assert client.post(f"/api/project/{EX1}/peb-study/run", json={"variants": ["omrf_r3"]}).status_code == 400
    assert client.post(f"/api/project/{EX1}/peb-study/run", json={"variants": "strict"}).status_code == 400
    assert client.post(f"/api/project/{EX1}/peb-study/run", json={"max_runs": 0}).status_code == 400
    assert client.post(f"/api/project/{EX1}/peb-study/run", json={"max_runs": 99}).status_code == 400
    assert client.post(f"/api/project/{EX1}/peb-study/run", json={"max_runs": "many"}).status_code == 400
    assert client.post(f"/api/project/{EX1}/peb-study/stop").json() == {"ok": True, "running": False}
    assert _events(client, EX1) == [{"type": "finished", "stopped": False, "idle": True}]
    assert peb.current() is None


# ---------------------------------------------------------------- the tab
def test_the_tab_offers_the_action_and_labels_the_not_compliant_variants():
    js = (MOD / "variations" / "ui" / "app.js").read_text(encoding="utf-8")
    assert "Run the PEB lever study" in js and "/peb-study/run" in js and "/peb-study/events" in js
    assert "'not compliant': ['NOT COMPLIANT', 'bad']" in js
    assert "Does not apply to this project" in js and "PEB_LEVERS.md" in js
    for f in ("study.html", "/peb-study/file/"):
        assert f in js
    man = json.loads((MOD / "steltic_module.json").read_text(encoding="utf-8"))
    assert man["uses"] == ["steltic_india", "steltic_CFS_india"]
    assert man["env_vars"]["STELTIC_INDIA_DIR"] == "{need.steltic_india}"
    assert man["env_vars"]["STELTIC_INDIA_PYTHON"] == "{python.steltic_india}"
    assert man["env_vars"]["STELTIC_CFS_DIR"] == "{need.steltic_CFS_india}"


# ---------------------------------------------------------------- the real runner (optional: no analysis either)
REAL = os.environ.get("STELTIC_INDIA_CHECKOUT", "")


@pytest.mark.skipif(not (REAL and (pathlib.Path(REAL) / "scripts" / "peb_lever_study.py").is_file()),
                    reason="set STELTIC_INDIA_CHECKOUT to a steltic_india checkout with scripts/peb_lever_study.py")
def test_probe_reads_the_real_runner(monkeypatch):
    """The probe against steltic_india's own runner: its four PEB golds, and per job the variants and the code
    status the runner gives them (variants_for only -- nothing is analysed)."""
    monkeypatch.setenv("STELTIC_INDIA_DIR", REAL)
    monkeypatch.setenv("STELTIC_INDIA_PYTHON", os.environ.get("STELTIC_INDIA_PYTHON_REAL") or sys.executable)
    p = peb.paths()
    pr = peb.probe(p, EX1)
    assert EX1 in pr["jobs"] and EX4 in pr["jobs"]
    by = {v["name"]: v for v in pr["job"]["variants"]}
    assert by["market"]["compliance"] == "not compliant" and by["rods"]["compliance"] == "not compliant"
    assert by["strict"]["compliance"] == "compliant" and by["solar_ready"]["applies"]
    by4 = {v["name"]: v for v in peb.probe(p, EX4)["job"]["variants"]}
    assert by4["omrf_r3"]["compliance"] == "not compliant" and not by4["joint_deliverable"]["applies"]
    s = peb.expand_selection(pr["job"]["variants"], ["cum:rods"])
    assert "compliant" in s and "market" not in s
    if "short" in pr["cli"]["ladders"]:                  # the second-wave runner
        short = {v["name"]: v for v in pr["job"]["variants_by_ladder"]["short"]}
        assert not any(n.startswith("cum:") for n in short) and short["market"]["compliance"] == "not compliant"
        assert pr["cli"]["stop_after_min"] is True
