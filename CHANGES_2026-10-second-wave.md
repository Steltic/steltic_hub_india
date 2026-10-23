# steltic-hub-india — 2026-10: second wave (the PEB lever study from the Variations tab)

Branch `fix/2026-10-second-wave`, from origin/main (19b368e, the first wave). It goes with steltic_india's PEB
variations release: `scripts/peb_lever_study.py` (the study runner) and `steel_engine/peb_autosize.py` (the
deterministic auto-sizer), on `feat/2026-10-peb-variations` and due on steltic_india main after this round. With an HR
Steel that does not have the runner yet, the tab says so and offers nothing to run.

## Design variations: Run the PEB lever study (U1)

- **Where.** A **PEB lever study** entry at the right of the Variations step bar (`variations/ui/app.js`), with its own
  pane. It is dimmed, and its pane gives the reason instead of a Run button, when the study does not apply.
- **What runs.** The Variations server (`variations/peb_study.py`, routes in `variations/main.py`) runs HR Steel's
  study runner as one child process. It runs in HR Steel's own checkout (`cwd`) with HR Steel's own interpreter. No
  language model is involved and nothing leaves the PC. The command line is the runner's own:
  `--job <project> --jobs-root --work --cfs-root --published-root --variants --max-runs [--ladder] [--stop-after-min]
  [--resume] [--solar]`. The environment is the server's, plus `PYTHONHASHSEED=0` (as in every auto-sizer run),
  `PYTHONDONTWRITEBYTECODE=1` and `-B` (nothing is written into a linked working copy) and `STELTIC_CFS_ROOT`.
- **How the hub finds HR Steel.** Through its registry, as for any module command. The Variations manifest declares
  `"uses": ["steltic_india", "steltic_CFS_india"]` and the env vars `STELTIC_INDIA_DIR = {need.steltic_india}`,
  `STELTIC_INDIA_PYTHON = {python.steltic_india}` and `STELTIC_CFS_DIR = {need.steltic_CFS_india}` (CFS Steel
  re-designs the purlins and girts). It also sets `INDIA_CORPUS_ROOT`, as HR Steel's own manifest does.
- **The runner decides the variants.** A probe in HR Steel's interpreter reads them; nothing is analysed:
  - the runner's `--help` gives the switches it offers: the `--ladder` choices and `--stop-after-min`;
  - importing `scripts/peb_lever_study.py` gives the job specs and, for this project, `variants_for` for each ladder
    (name, label, status, note, n/a, the row a step copies, the row it starts from).
  - The answer is cached until one of the runner's files changes.
  - The tab offers exactly that list: the strict baseline (always), each lever alone, all compliant levers together,
    the cumulative ladder (as a whole, since each step starts from the previous one), the market-practice bundle and,
    for IN_Ex1, solar-ready.
  - Each variant carries the runner's own code status, read the way the runner's `_tag` reads it: compliant; reading
    (a reading an owner ruling rejected, the old unconservative treatment, a code conflict for the design engineer to
    decide); **NOT COMPLIANT**; or n/a, which is not selectable.
  - Where the runner offers them, the full / short ladder and a time budget (`--stop-after-min`, resumable chunks).
    An older runner without them is driven without them.
- **When it does not apply, the tab says why**:
  - HR Steel not installed, or no environment yet;
  - an HR Steel without the study runner;
  - the runner could not be read;
  - a multistorey base brief ("not a PEB job");
  - no job spec named after the project — the message lists the runner's specs and points to `PEB_LEVERS.md`.
  A PEB job is shown but not run while CFS Steel is not installed, or while there is no job folder.
- **Job folder.** It is only read: every variant runs in a copy under the work folder.
  - The project folder, when it holds `cfg.py` (a PEB job written the way the golds are, or HR Steel's package staged
    there by the Nonlinear module).
  - Otherwise HR Steel's design of the project. It is fetched through HR Steel's own `/api/download` when the study
    starts and unpacked under `variations/peb_study/input/<project>/` (zip-slip guarded; a package without `cfg.py`
    is refused).
- **Run.**
  - Full runs per variant (default 8, at most 30).
  - *Keep the rows already done* (`--resume`, on by default). A resumed run is refused when the job's `cfg.py` or
    secondary script changed since the rows were sized (sha1 recorded in `run.json`), and when it would switch ladder
    (the runner refuses that too; the page warns before Run).
  - One study at a time on the PC, whatever the project (409 otherwise).
  - Stop ends the runner's process group, so every full run with it (`taskkill /T` on Windows). The server also ends
    a live study when it shuts down.
- **Progress.**
  - Events are streamed and re-attachable from any sequence number, as for a variations study: every line the runner
    prints, and one progress step per variant ("== <job> / <variant>", including the variants a resumed run keeps).
  - The run ends as finished, failed (exit code), stopped, or paused (the runner's time-budget line: run again with
    the rows kept to continue). After a server restart it shows as interrupted.
- **Outputs.** The rows done so far come from `study.json`: kg/m², the change from strict, the governing check, the
  full runs, and the status pill. Not-compliant rows are tinted. Links are served by the module:
  - `study.html`, `study.csv`, `study.json` (work/<project>/);
  - `index.html`, `study_tables.md`, `study_all.json` (work/);
  - the run log.
  Everything is under `<project>/variations/peb_study/`, with `run.json` (command, variants, ladder, job folder, input
  hash, times, exit code).

## The hub: `uses`, a soft dependency (generic)

- `manifest.py`: a `uses` list, validated (module ids, not itself) and published in `/api/state`.
- `runners.py`:
  - `base_ctx` gives each installed `uses` module `{need.<id>}` (its checkout) and `{python.<id>}` (its interpreter,
    once its environment exists), exactly as `needs` does.
  - A variable that still names an absent module is left unset, for a server and for a CLI run (`_unresolved` now
    covers `{need.` beside `{server.` and `{python.`).
  - `ServerSupervisor` restarts a running server when a module it uses has been installed since it started.
  - Unlike `needs`, a missing `uses` module never refuses the start. Design variations still starts without HR Steel
    or CFS Steel. No hub code branches on a module id.

## Docs

- `PEB_LEVERS.md`:
  - the rows table gains the cumulative ladder and solar-ready, and notes `--ladder short` / `--stop-after-min`;
  - a new section, *Running it from the Variations tab*.
- README:
  - the module diagram and table;
  - *The module manifest* (`needs` vs `uses`, the templates);
  - *Design variations*: the PEB lever study paragraph; twelve categories, not eleven (the first wave's Indian PEB
    practice levers was missing).
- `windows/SelfTest.ps1`:
  - expects twelve categories (it still checked eleven);
  - checks that the PEB lever study answers with a reason for a project that is no job spec.

## Tests

- `tests/test_variations_peb_study.py` (16 tests + 1 optional) uses a FAKE runner,
  `tests/fixtures/fake_steltic_india/scripts/` (no analysis, no engine). The fake has the real runner's module API
  and command line, with the same variant names and status strings. `FAKE_OLD_RUNNER=1` makes it the first-release
  runner. The tests cover:
  - status mapping; probe and `--help` switches; no bytecode written;
  - the older runner; the selection rules (both ladders);
  - every "does not apply" reason; not ready without CFS Steel or a job folder;
  - the run: the exact argv, cwd and environment, progress events, rows, outputs and links, outputs only, and that
    the job folder is untouched;
  - resume; solar; short ladder and the ladder-switch refusal; the time budget, paused then continued;
  - one at a time; Stop ending the process tree; a failing runner;
  - HR Steel's package fetched and unpacked, the changed-design refusal, no design in HR Steel;
  - bad requests; the tab and manifest wiring.
  The optional test probes a real steltic_india checkout when `STELTIC_INDIA_CHECKOUT` names one. Run against
  `feat/2026-10-peb-variations` (1a6eb00) it passes, and it reads `variants_for` only.
- `tests/test_hub.py` (4 tests):
  - `uses` parsing and validation;
  - the templates only while installed, and the variables left unset otherwise;
  - a CLI run without an absent used module;
  - the supervisor restarting a server when a used module appears.
- Full suite: **170 passed, 1 skipped** (the optional real-runner probe; 171 passed with `STELTIC_INDIA_CHECKOUT`
  set). Before: 150 passed.
