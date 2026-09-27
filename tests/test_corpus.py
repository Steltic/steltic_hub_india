"""The IS corpus module: bundled tooling, an empty corpus the user builds from their own licensed PDFs,
the grounding server on an empty corpus, and Import fixed corpus.

No module install and no BIS text: every document here is synthetic.

    python -m pytest tests/test_corpus.py -q
"""
import json, os, pathlib, socket, subprocess, sys, tempfile, time, urllib.request, zipfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("STELTIC_HUB_DATA", tempfile.mkdtemp(prefix="stelticthub-test-"))

from steltic_hub import config                                            # noqa: E402
from steltic_hub.manifest import load_catalog                             # noqa: E402

MOD = config.CATALOG_DIR / "engineering_rag_india"
SCRIPTS = MOD / "scripts"
MANIFEST = config.CATALOG_DIR / "engineering_rag_india.json"


def _manifest_raw() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _post_install(data_dir: pathlib.Path) -> subprocess.CompletedProcess:
    """Run the module's post-install exactly as envs.install_into renders it."""
    code = _manifest_raw()["env"]["post_install"][0][1]
    code = code.replace("{data_dir}", str(data_dir)).replace("{module_dir}", str(MOD))
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300)


def _run(script: str, *args, root: pathlib.Path, **kw) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(root / "scripts" / script), *map(str, args)], capture_output=True,
                          text=True, timeout=300, cwd=str(root), **kw)


def _synthetic_doc(root: pathlib.Path, stem: str = "IS_816_1969") -> None:
    """A converted-looking document with made-up text (no standard content)."""
    d = root / "documents" / "standards" / stem
    (d / "indexes").mkdir(parents=True, exist_ok=True)
    (d / "markdown").mkdir(parents=True, exist_ok=True)
    (d / "markdown" / f"{stem}.search.md").write_text(
        "<!-- pdf_page=1 printed_label=1 printed_label_qualified=1 part=standard -->\n\n"
        "## 1. SCOPE\n\n1.1 Synthetic scope text for a hub test.\n\n"
        "<!-- pdf_page=2 printed_label=2 printed_label_qualified=2 part=standard -->\n\n"
        "## 2. ZEBRA WIDGETS\n\n2.1 A placeholder clause about zebra widgets and quokka fasteners.\n", encoding="utf-8")
    (d / "indexes" / "documents.json").write_text(json.dumps([{
        "id": stem, "title": "synthetic", "edition": "1969", "source_pdf": "/nowhere/x.pdf",
        "searchable_markdown": f"documents/standards/{stem}/markdown/{stem}.search.md"}]), encoding="utf-8")
    secs = [{"doc": stem, "section_id": sid, "title": t, "part": "standard", "pdf_page": p, "printed_label": str(p),
             "parent": None, "children": [], "synthetic": False, "source": "test"}
            for sid, t, p in (("1", "SCOPE", 1), ("2.1", "Zebra widgets", 2))]
    (d / "indexes" / "sections.json").write_text(json.dumps(secs), encoding="utf-8")
    (d / "indexes" / "equations.json").write_text("[]", encoding="utf-8")
    (d / "indexes" / "tables.json").write_text("[]", encoding="utf-8")


@pytest.fixture(scope="module")
def workspace(tmp_path_factory):
    data = tmp_path_factory.mktemp("hubdata")
    r = _post_install(data)
    assert r.returncode == 0, r.stdout + r.stderr
    return data, data / "grokbot", r.stdout


# ---------------------------------------------------------------- the module ships with the hub
def test_corpus_module_is_bundled_and_names_no_corpus_repository():
    m = load_catalog(config.CATALOG_DIR)["engineering_rag_india"]
    assert m.bundled == "engineering_rag_india" and not m.git and not m.private
    assert m.name == "IS corpus (your own conversions)"
    raw = MANIFEST.read_text(encoding="utf-8")
    assert "github.com/Steltic/engineering_rag_india" not in raw and '"private"' not in raw and '"url"' not in raw
    assert "CORPUS_FIX_LLM_INSTRUCTIONS.md" in raw                      # the workflow is named in the module help
    tabs = {t.id: t for t in m.tabs}
    assert {"corpus", "convert", "index", "validate", "import", "grounding"} <= set(tabs)
    imp = tabs["import"]
    assert "import_corpus_zip.py" in imp.run.command[0] and "{hub_url}" in imp.run.command
    zipf = next(f for f in imp.fields if f.id == "zip")
    assert zipf.type == "file" and zipf.accept == ".zip" and zipf.required
    assert any(f.id == "no_repair" and f.arg == "--no-repair" for f in tabs["index"].fields)


def test_bundled_tooling_carries_no_licensed_data():
    """The corpus tooling is code: no transcriptions, section tables, figure data or per-stem notes."""
    names = {p.name for p in SCRIPTS.iterdir()}
    for must in ("retrieval.py", "build_index.py", "search.py", "convert_pdf.py", "postprocess.py", "validate.py",
                 "strip_watermark.py", "update_metadata.py", "query_cache.py", "bis_text.py", "import_corpus_zip.py"):
        assert must in names, must
    for banned in ("bis_manual_sections.json", "is811_manual.json", "quality.json", "is808_fixes.json",
                   "recover_image_pages.py"):
        assert banned not in names, banned
    assert not list(SCRIPTS.rglob("*.csv")) and not list(SCRIPTS.rglob("*_recovered.md"))
    aliases = json.loads((SCRIPTS / "aliases.json").read_text(encoding="utf-8"))
    assert set(aliases) == {"synonym_groups", "eq_id_aliases", "id_aliases", "conflicts", "notes"}
    for p in SCRIPTS.iterdir():
        t = p.read_text(encoding="utf-8").lower()
        assert "maroubra" not in t, p.name                            # no licensee detail of anyone's watermark


# ---------------------------------------------------------------- an empty corpus that works
def test_post_install_lays_out_an_empty_corpus(workspace):
    data, root, out = workspace
    for d in ("documents/standards", "queue/in", "cache", "indexes", "search", "scripts"):
        assert (root / d).is_dir(), d
    assert not any((root / "documents" / "standards").iterdir())
    assert (root / "search" / "spec_fts.sqlite").is_file() and (root / "indexes" / "documents.json").is_file()
    assert "EMPTY" in out and "CORPUS_FIX_LLM_INSTRUCTIONS.md" in out
    assert (root / "scripts" / "retrieval.py").read_bytes() == (SCRIPTS / "retrieval.py").read_bytes()


def test_post_install_refreshes_code_but_keeps_corpus_data(tmp_path):
    data = tmp_path / "d"
    assert _post_install(data).returncode == 0
    root = data / "grokbot"
    (root / "scripts" / "quality.json").write_text('{"IS_800_2007": {"quality": "PASS"}}', encoding="utf-8")
    (root / "scripts" / "aliases.json").write_text('{"synonym_groups": []}', encoding="utf-8")
    (root / "scripts" / "search.py").write_text("# stale\n", encoding="utf-8")
    _synthetic_doc(root)
    r = _post_install(data)
    assert r.returncode == 0, r.stderr
    assert (root / "scripts" / "search.py").read_bytes() == (SCRIPTS / "search.py").read_bytes()   # code wins
    assert "PASS" in (root / "scripts" / "quality.json").read_text()                                   # data kept
    assert (root / "scripts" / "aliases.json").read_text() == '{"synonym_groups": []}'
    assert (root / "documents" / "standards" / "IS_816_1969" / "indexes" / "sections.json").is_file()


def test_validate_on_an_empty_corpus_says_what_to_do(workspace):
    _, root, _ = workspace
    r = _run("validate.py", "--corpus", "--root", root, "--json-out", root / "v.json", root=root)
    assert r.returncode == 1
    assert "the corpus is empty" in r.stdout and "IS_456_2000 is not in the corpus" in r.stdout
    rep = json.loads((root / "v.json").read_text(encoding="utf-8"))
    assert rep["passed"] is False and any(p["status"] == "SKIP" for p in rep["probes"])


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 200
        return json.loads(r.read().decode("utf-8"))


@pytest.mark.parametrize("built", [True, False])
def test_grounding_server_starts_on_an_empty_corpus(tmp_path, built):
    """A fresh install has no document: the server starts and every question is 'not in the corpus', never a 5xx
    (which would pause the agents' run). Also before any index exists at all."""
    data = tmp_path / "d"
    assert _post_install(data).returncode == 0
    root = data / "grokbot"
    if not built:
        for f in ("search/spec_fts.sqlite", "indexes/documents.json"):
            (root / f).unlink()
    port = _free_port()
    proc = subprocess.Popen([sys.executable, str(MOD / "rag_server.py"), "--root", str(root), "--port", str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                st = _get(base + "/healthz")
                break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("grounding server did not start")
        assert st["ok"] and st["empty"] and st["indexed_docs"] == []
        for body in ({"query": "basic wind speed Chennai", "collection": "engineering_standards_IS875_P3"},
                     {"query": "", "clause": "7.2.6", "collection": "engineering_standards_IS1893"},
                     {"query": "anything", "collection": "specification"}):
            out = _post(base + "/query", body)
            assert out["results"] == [] and "not in the corpus" in out["note"], out
        with urllib.request.urlopen(base + "/", timeout=10) as r:
            assert "The IS corpus on this PC is empty" in r.read().decode("utf-8")
    finally:
        proc.terminate()
        proc.wait(timeout=10)


# ---------------------------------------------------------------- Import fixed corpus
def _fixed_zip(tmp: pathlib.Path) -> pathlib.Path:
    """What the corpus-fix step returns: a corpus folder (here under grokbot/), data files in scripts/,
    and things that must not be applied (a .py, a PDF)."""
    src = tmp / "fixed" / "grokbot"
    _synthetic_doc(src)
    (src / "scripts").mkdir(parents=True)
    (src / "scripts" / "quality.json").write_text('{"IS_816_1969": {"quality": "REPAIRED", "known_defects": []}}',
                                                  encoding="utf-8")
    (src / "scripts" / "retrieval.py").write_text("raise SystemExit('the zip code must never run')\n", encoding="utf-8")
    zp = tmp / "fixed_corpus.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        for p in src.rglob("*"):
            if p.is_file():
                zf.write(p, "grokbot/" + p.relative_to(src).as_posix())
        zf.writestr("my_pdfs/IS_816_1969.pdf", b"%PDF-1.4 not really")
        zf.writestr("CORPUS_FIX_LLM_INSTRUCTIONS.md", "instructions")
        zf.writestr("grokbot/FIX_REPORT.md", "# Fix report\nsynthetic")
    return zp


def test_import_fixed_corpus_zip_backs_up_replaces_and_rebuilds(tmp_path):
    data = tmp_path / "d"
    assert _post_install(data).returncode == 0
    root = data / "grokbot"
    (root / "documents" / "standards" / "OLD_MARKER").mkdir()
    zp = _fixed_zip(tmp_path)

    dry = _run("import_corpus_zip.py", zp, "--root", root, "--dry-run", root=root)
    assert dry.returncode == 0 and "IS_816_1969" in dry.stdout and "nothing changed" in dry.stdout
    assert (root / "documents" / "standards" / "OLD_MARKER").is_dir()

    r = _run("import_corpus_zip.py", zp, "--root", root, "--rebuild", root=root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "CORPUS: PASS" in r.stdout
    std = root / "documents" / "standards"
    assert (std / "IS_816_1969").is_dir() and not (std / "OLD_MARKER").exists()
    # the hub's code stays; the zip's data file is applied; the PDF is not copied
    assert (root / "scripts" / "retrieval.py").read_bytes() == (SCRIPTS / "retrieval.py").read_bytes()
    assert "REPAIRED" in (root / "scripts" / "quality.json").read_text(encoding="utf-8")
    assert "Fix report" in (root / "FIX_REPORT.md").read_text(encoding="utf-8")     # what the fix step did, kept
    assert not list(root.rglob("*.pdf"))
    backups = list((data / "grokbot_backups").iterdir())
    assert len(backups) == 1
    b = backups[0]
    assert (b / "documents" / "standards" / "OLD_MARKER").is_dir()
    assert (b / "zip_scripts_not_applied" / "retrieval.py").is_file()
    assert json.loads((b / "IMPORT.json").read_text())["documents"] == ["IS_816_1969"]
    # the imported corpus answers
    s = _run("search.py", "exact_section", "2.1", "--doc", "IS_816_1969", "--root", root, root=root)
    hit = json.loads(s.stdout)
    assert hit["found"] and "quokka fasteners" in hit["hits"][0]["text"]
    docs = json.loads((root / "indexes" / "documents.json").read_text(encoding="utf-8"))
    assert [d["id"] for d in docs] == ["IS_816_1969"]


def test_import_refuses_a_zip_that_is_not_a_safe_corpus(tmp_path):
    data = tmp_path / "d"
    assert _post_install(data).returncode == 0
    root = data / "grokbot"
    (root / "documents" / "standards" / "KEEP").mkdir()
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("documents/standards/IS_800_2007/markdown/x.md", "x")
        zf.writestr("../escape.txt", "x")
    none = tmp_path / "none.zip"
    with zipfile.ZipFile(none, "w") as zf:
        zf.writestr("readme.txt", "no corpus here")
    for zp, why in ((bad, "unsafe path"), (none, "no documents/standards")):
        r = _run("import_corpus_zip.py", zp, "--root", root, root=root)
        assert r.returncode == 2 and why in r.stdout, r.stdout
    assert (root / "documents" / "standards" / "KEEP").is_dir()
    assert not (data / "grokbot_backups").exists() and not (tmp_path / "escape.txt").exists()


def test_rebuild_skips_the_pdf_repair_when_the_pdf_is_not_found(tmp_path):
    """The per-document repair re-derives the indexes from the PDF text layer; without the PDF it could only
    lose records, so it is skipped and the document keeps the indexes it has."""
    data = tmp_path / "d"
    assert _post_install(data).returncode == 0
    root = data / "grokbot"
    _synthetic_doc(root)
    r = _run("build_index.py", "--root", root, root=root)
    assert r.returncode == 0, r.stderr
    assert "repair skipped for IS_816_1969: source PDF not found" in r.stderr
    secs = json.loads((root / "indexes" / "sections.json").read_text(encoding="utf-8"))
    assert {s["section_id"] for s in secs if s["doc"] == "IS_816_1969"} >= {"1", "2.1"}


def _env_without_poppler(tmp_path: pathlib.Path) -> dict:
    """This PC as a Windows machine without Poppler: no pdftotext on PATH or in the usual install places."""
    empty = tmp_path / "no_poppler"
    empty.mkdir(exist_ok=True)
    env = dict(os.environ, PATH=str(empty))
    for v in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)", "USERPROFILE", "ProgramData"):
        env[v] = str(empty)
    env.pop("INDIA_POPPLER_BIN", None)
    return env


def test_rebuild_and_import_work_without_poppler_when_the_pdfs_are_present(tmp_path):
    """Windows without Poppler, the user's licensed PDFs in <corpus>/pdfs: Rebuild index (with and without the
    repair) and Import fixed corpus must not crash on the missing pdftotext; they keep the corpus text and the
    indexes as they are and say why."""
    data = tmp_path / "d"
    assert _post_install(data).returncode == 0
    root = data / "grokbot"
    _synthetic_doc(root)
    (root / "pdfs" / "IS_816_1969.pdf").write_bytes(b"%PDF-1.4 synthetic, not a real PDF")
    # a clause whose heading is not in the served text: the rebuild then asks the PDF text layer for it
    # (build_index layout_for -> bis_text.pdf_layout_pages -- the call that crashed on Windows)
    sp = root / "documents" / "standards" / "IS_816_1969" / "indexes" / "sections.json"
    secs = json.loads(sp.read_text(encoding="utf-8"))
    secs.append({"doc": "IS_816_1969", "section_id": "2.7", "title": "Heading only in the PDF", "part": "standard",
                 "pdf_page": 2, "printed_label": "2", "parent": None, "children": [], "synthetic": False, "source": "test"})
    sp.write_text(json.dumps(secs), encoding="utf-8")
    env = _env_without_poppler(tmp_path)
    for extra in ((), ("--no-repair",)):
        r = _run("build_index.py", "--root", root, *extra, root=root, env=env)
        assert r.returncode == 0, r.stderr[-2000:]
        assert "Traceback" not in r.stderr
        assert "pdftotext (poppler) not found" in r.stderr
        if not extra:        # the repair would read the PDF text layer: skipped, and said so
            assert "index repair is skipped" in r.stderr
        else:
            assert "not re-sliced from the PDF text layer" in r.stderr
        secs = json.loads((root / "indexes" / "sections.json").read_text(encoding="utf-8"))
        assert {x["section_id"] for x in secs if x["doc"] == "IS_816_1969"} >= {"1", "2.1"}
    s = _run("search.py", "exact_section", "2.1", "--doc", "IS_816_1969", "--root", root, root=root, env=env)
    assert "quokka fasteners" in json.loads(s.stdout)["hits"][0]["text"]
    # Import fixed corpus on the same PC
    r = _run("import_corpus_zip.py", _fixed_zip(tmp_path), "--root", root, "--rebuild", root=root, env=env)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "CORPUS: PASS" in r.stdout and "Rebuild index FAILED" not in r.stdout


def test_poppler_tool_is_found_through_INDIA_POPPLER_BIN(tmp_path):
    exe = tmp_path / "bin" / ("pdftotext.exe" if os.name == "nt" else "pdftotext")
    exe.parent.mkdir()
    exe.write_text("")
    env = dict(_env_without_poppler(tmp_path), INDIA_POPPLER_BIN=str(exe.parent))
    code = "import sys; sys.path.insert(0, sys.argv[1]); import bis_text; print(bis_text.poppler_tool('pdftotext'))"
    r = subprocess.run([sys.executable, "-B", "-c", code, str(SCRIPTS)], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0 and r.stdout.strip() == str(exe), r.stdout + r.stderr
    env.pop("INDIA_POPPLER_BIN")
    r = subprocess.run([sys.executable, "-B", "-c", code, str(SCRIPTS)], capture_output=True, text=True, env=env, timeout=60)
    assert r.stdout.strip() == "None", r.stdout + r.stderr


def test_no_reference_to_a_corpus_repository_remains():
    """The corpus is the user's own: nothing in the hub points at a corpus repository to clone or link."""
    root = pathlib.Path(__file__).resolve().parent.parent
    bad = ("github.com/Steltic/engineering_rag_india", "Steltic/engineering_rag_india", "private engineering_rag_india",
           "engineering_rag_india repo", "corpus repo", "private corpus")
    hits = []
    for p in [root / "README.md", root / "CHANGES_2026-09-review.md", *(root / "steltic_hub").rglob("*")]:
        if not p.is_file() or "__pycache__" in p.parts or p.suffix not in (".md", ".py", ".js", ".json", ".html"):
            continue
        t = p.read_text(encoding="utf-8", errors="ignore")
        hits += [f"{p.relative_to(root)}: {b}" for b in bad if b in t]
    assert not hits, hits
