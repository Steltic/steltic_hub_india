# steltic-hub-india — 2026-10 third wave: the site build in the repo (P4), the lever study on any PEB job (P11)

Branch `fix/2026-10-third-site`, from origin/main b6fc5f1. It goes with steltic_india's branch of the same name, which
holds the study runner's contract (`--list-variants --json`, `--cfs-python`, `--progress-json`) and its inferred job
specs. The hub app's code outside the Variations module is untouched. The version stays at 0.2.2.

## The site build, in the repo (handoff P4)

Until now the October publishes were built by scripts that lived outside every repo, with this workspace's paths in
them: `build_hr_wave2.py`, `patch_india2.py`, `build_nl2.sh`, `package2.sh` and `package2b.sh`. They are now one
parameterised tool, `site/`:

```
python site/build_site.py build --config site/publishes/<publish>.json [--self-contained] [--out <folder>]
```

- **Inputs** come from a publish config, with `${VARS}` that the environment or `--var` override:
  - the gold job folders;
  - the cold-formed publish folder;
  - the nonlinear run folders, in priority order. A source can be limited to listed ids, or to the ids in a marker
    folder: that is build_nl2.sh's re-run rule;
  - the NL checkout, its interpreter and the HR engine it uses;
  - the titles files;
  - the live base page and the live indexes, from a local mirror of public_html, the site, or both;
  - the exact-match page patches.
- **Outputs**:
  - the upload zip: `india.html` and `solutions_india/` only, folders 755, files 644, sorted entries; with
    `SOURCE_DATE_EPOCH` the same inputs give the same bytes;
  - the rollback zip: the live copy of every replaced file, plus `NEW_FILES_TO_DELETE_ON_ROLLBACK.txt`;
  - `UPLOAD_INSTRUCTIONS.txt`: cPanel upload, the check, the rollback, and the build's checks;
  - `MANIFEST.json`: every file with its size and SHA-256, the inputs, the repos' commits and the checks.
- **Self-contained option** (`--self-contained`): each published page (not the viewers) is read for its relative
  links: `href` / `src`, and the file names its scripts open, e.g. the viewer bundle's list of viewers. A missing
  target is copied from the page's source folder. This adds the hot-rolled report figures and every nonlinear
  viewer: `viewer_3d.html`, `pushover/pushover_viewer_3d.html` and `ddm_viewer_3d.html`. The zip alone then
  reproduces the India pages. A link found nowhere is a `MISSING` check (exit code 3). The site-wide files the page
  uses (`styles.css`, the icons, the logo, terms, privacy) are listed in the instructions, not carried.
- **Page patch**: `site/patches/*.patch` blocks (`@@@ FIND / @@@ REPLACE / @@@ END`). Each FIND must occur exactly
  once in the page as it stands, or the build stops: `rep()`'s discipline.
  - `india_2026-10_second_wave.patch` is `patch_india2.py` converted. Applied to the first-wave page it gives the
    live second-wave page byte for byte.
  - `india_2026-10_third_wave.patch` is empty until the third-wave text lands.
- **Checks** it prints:
  - `PROBLEM`: an example not complete, or an index link that does not resolve.
  - `CHECK page says ...`: the page's prose counts ("39 worked Indian designs", "20 Indian example briefs ... plus
    four sub-units", "24 nonlinear runs") no longer match what is published. Adding the MSME shed will need the page
    patch.
  - `CHECK public text`: a path, "EOR", "owner ruling", a ticket id, a raw dict, a cfg key, a JSON or script name,
    the old IS 875-1 edition, or "(was None)", in a page, report or index text. It reports only. The viewers and the
    HR reports' Appendix C (the run's activity log) are not read.
- **Post-upload check**: `python site/build_site.py verify --manifest <out>/MANIFEST.json`. Every file is compared
  with the live copy (size and SHA-256; a connection reset is retried one file at a time). Every relative link of the
  pages and indexes is asked for (HEAD).
- **First-wave cosmetic (P4)**: `hr.system_names {"SMRF": "SMF"}` in the third-wave config. IN_Ex9's meta then reads
  "SMF+SCBF", like every other example. Its title, which is owner text, keeps "SMRF + SCBF".
- **IN_Ex5 with no spare capacity**: when the new Ex1 leaves the roof no capacity for panels (`capacity_kNm2` 0), the
  flag says so ("As built the roof has no spare capacity for rooftop panels: <the limiting item> governs already
  without them"). It no longer says "carries 0.00 kN/m²".
- **Tools moved**:
  - `site/tools/live_page_check.py`: the URL and the expected list counts are arguments; exit 1 on a page error, a
    failed request or a count mismatch;
  - `site/tools/heavy.sh`: `HEAVY_LOCKS` and `HEAVY_SLOTS` are settable;
  - `scan2.py` and `compare2.py` became `build_site.py scan` and `build_site.py compare` (`--base`, `--new`,
    `--hr-engine`).

  `run_ddm.sh` and `step.sh` are NL run wrappers that the NL agent's orchestrator replaces. `hr3_batch.sh` is the
  gold batch; with the gold harness it belongs with the gold set. Neither group is a site-build step, so they stay
  out.
- **Configs**:
  - `site/publishes/2026-10_second_wave.json` is the second-wave publish as it was built.
  - `site/publishes/2026-10_third_wave.json` is the next one: self-contained, the base is the live second-wave tree
    plus the site, and `SMRF -> SMF`.
- **Third-wave additions, prepared in the config and inactive until their inputs exist** (each prints a `NOTE` until
  then):
  - the angle-truss MSME shed `IN_Ex21_MSME_angle_truss_shed_Hosur`:
    - placed after IN_Ex20 (`hr.extra`, optional: left out while its gold job folder has no package);
    - its title and blurb from `titles_wave3.json` (optional titles file; the config's fallback title is used
      until it exists);
  - the page's example counts, patched only once the build publishes it:
    - `india_2026-10_third_wave_ex21.patch`: 25 worked buildings, 21 briefs plus the four sub-units, and 40 worked
      designs (description and introduction);
    - `india_2026-10_third_wave_ex21_nl.patch`: 25 nonlinear runs once its nonlinear run is published;
  - the nonlinear stage-2 re-runs, `NL_STAGE2`, first in the source order:
    - a run is taken once its `complete_gate.json` is written;
    - its DDM report is rebuilt with HR main's engine (the source's own `engine_dir`);
    - the MSME shed's run is an optional id;
    - the October re-runs keep build_nl2.sh's rule (`marker_dir`).
- **Count checks**: a page count is checked wherever the page states it: the description, the text, and the "N
  worked buildings" headings of the hot-rolled and cold-formed libraries.

### Proof

- **Second-wave publish rebuilt with the tool** (overlay, offline, from the same gold outputs):
  - 373 files, the same set as the published tree;
  - 349 files are byte-identical;
  - the 24 `ddm_report.html` differ only in their generation timestamp: they are rebuilt from `ddm_results.json`,
    as build_nl2.sh did;
  - zip 87.8 MB.
- **Third-wave dry run** (self-contained, offline; the base is the second-wave tree; today's golds):
  - 532 files, 391.8 MB unpacked, 118.7 MB zipped;
  - the self-contained pass added 168 files: 96 hot-rolled report figures, plus 24 each of `viewer_3d.html`,
    `pushover_viewer_3d.html` and `ddm_viewer_3d.html`;
  - 0 links resolve nowhere; no PROBLEM, MISSING or page-count check;
  - the prepared additions stayed inactive (NOTEs).
  - 1 public-text hit, already on the live site: the IN_Ex17 report prints a raw dict (`{"X": 0.002, "Y": 0.002}`)
    in its across-wind sentence. It needs a steltic_india report fix.
  - Offline, the rollback counts 159 files as new because the mirror (the second-wave tree) lacks them. On the site
    most exist (the figures and viewers of the earlier publishes). A build that may read the site fetches their live
    copies.

## The PEB lever study on any PEB job (handoff P11)

- **The runner's contract.** The tab reads `scripts/peb_lever_study.py --list-variants --json --job <project>
  [--jobs-root <job folder>]`:
  - the switches;
  - where the job spec comes from;
  - the variants of each ladder with their code status.

  It no longer parses `--help` or imports the script. A runner without the contract (the PEB variations release, and
  the first release) is still read the old way. Both ways give the same variants (tested).
- **No hand-written spec needed.** A project that is not one of the runner's specs has its spec inferred by the
  runner from the project's `cfg.py`. The pane shows "Job spec: inferred from this job's cfg.py", the resized
  constants, and the notes on how each field was read. A spec that cannot be inferred gives the runner's reasons.
  For a project whose job is HR Steel's design (no `cfg.py` in the project folder), **Read HR Steel's design of this
  project** fetches the package first (`POST /api/project/<p>/peb-study/stage`), so that the spec can be inferred
  before the variants are chosen.
- **`--cfs-python`.** The manifest hands the server CFS Steel's interpreter (`STELTIC_CFS_PYTHON =
  {python.steltic_CFS_india}`). A run passes it as `--cfs-python` when the runner takes the switch, the interpreter
  exists and it is not HR Steel's own. The purlins and girts are then designed in CFS Steel's environment. This
  removes HUB_RESULT2's open assumption that the CFS engine runs under HR Steel's interpreter.
- **`--progress-json`.** The progress (variant, resumed, row with kg/m², budget stop) comes from the runner's JSON
  lines. These are kept in `study.log` and left out of the live log. Each finished row now shows in the log with its
  kg/m² and refreshes the results table. `run.json` records the plan, the spec source and `--cfs-python`.
- **Docs**: `PEB_LEVERS.md` ("Does it apply?", the job folder, Run, and step 2 of "Running it on your own PEB job")
  and the README.
- **Base plates and anchors** (with the PEB agent's third-wave sizer, steltic_india `fix/2026-10-third-peb-golds`):
  `PEB_LEVERS.md` and the pane's lead text now say what the sizer does:
  - it sizes the plate thickness (12 mm up) and the anchor diameter (M20–M36) of each slab base, itemised in the
    take-off;
  - the plan size, the anchor layout and the base gussets stay as declared;
  - `size_base_plates: false` keeps them;
  - with an older sizer only the shear keys are resized.

## Tests

- `tests/test_site_build.py`, 21 tests, all on fixtures written by the tests: a live mirror, gold jobs, a CFS
  publish folder, NL runs and a fake NL checkout. Live access goes through file:// URLs, so there is no network.
  They cover:
  - the patch format and the exact-once rule;
  - the repo's patch files;
  - the count checks;
  - the overlay build: zip layout and permissions, the HR index (SMRF -> SMF, titles, notes, PEB files), the CFS
    copy, the NL sources, the re-run viewer, the DDM rebuild, the index titles, the rollback, the manifest and the
    instructions;
  - the self-contained build;
  - zips identical from the same inputs;
  - a new example and the page counts;
  - stopped builds;
  - NL regenerate and a failed DDM rebuild;
  - the NL source rules;
  - the site via file:// URLs;
  - the post-upload verify;
  - the CLI with a config file and its vars;
  - the repo's publish configs;
  - the Ex5 flag, the public-text scan and the compare tool;
  - the prepared third-wave additions before and after their inputs exist;
  - the Ex21 count patches against the live page's own sentences, in either order.
- `tests/test_variations_peb_study.py`: 6 new tests and 1 new optional one. They cover the contract against the old
  way, an engineer's own job with an inferred spec, a spec that cannot be inferred, `--cfs-python` with the JSON
  progress, a budget stop from the JSON progress, and HR Steel's design read before inference. The optional test is
  the real runner inferring a copy of IN_Ex4 under another name. The fake runner now has the contract
  (`FAKE_SECOND_WAVE_RUNNER=1` gives the PEB variations release's runner).
- **UI smoke test**: headless Chromium against the Variations server with the fake runner:
  - an inferred-spec project ran to the end, with the rows in the log;
  - no JSON lines leaked into the log;
  - a project with no spec showed its reasons;
  - no page errors; the only 404 was favicon.ico.
- **Full suite**: **197 passed, 2 skipped** (baseline 170 passed, 1 skipped). The two skips are the optional
  real-runner tests; with `STELTIC_INDIA_CHECKOUT` (steltic_india 1075c70) and `STELTIC_PEB_GOLDS` set, both pass.

## Not done here

- `windows/SelfTest.ps1` (P11) was not run: there is no Windows or PowerShell on this box.
- The study page's own footer (steltic_india `scripts/peb_lever_study.py`, `html_page`) still says that base plates
  and anchors keep the gold sizes. It belongs with the PEB agent's sizer PR, not with this branch.
