# Indian PEB practice levers: running the deterministic lever study on your own PEB job

The "Indian PEB practice levers" category of this module plans variations that each set one switch of the HR
engine's `cfg['peb_rules']` (steltic_india, `steel_engine/peb_levers.py`). A variation planned here is designed by the
HR design agent like any other variation. For a like-for-like answer to "how many kg/m² does this reading cost or
save on my shed", run the deterministic lever study instead: the same switches, each variant re-sized by the same
auto-sizer, with no language model in the loop.

## What the study does

For one PEB job, `scripts/peb_lever_study.py` (steltic_india) runs:

| row | what it is |
|---|---|
| gold as published | your job's own take-off (no run) |
| strict baseline | your job auto-sized under the strict rules: D-T1 C1 = 1.0, D-T4 no rods, D-T7 1.2 Mp joints, O1 1.2 Vd shear keys, H/240 with a brick dado, IS 811 purlins at Fy 240 MPa, collateral and PV out of the 0.9 DL rows |
| each lever alone | re-sized from the strict design with one switch set: `joint_demand` 'deliverable', `ltb_c1` 'gradient', `sway_limit` 'H150_with_dado_check', `base_shear_key` 'analysis', `bracing` 'tension_rods' (Zone II OCBF only), `purlins` 'thin_G350', `collateral_in_uplift` True, the seismic route OMRF R 3 (SMRF jobs) |
| cumulative ladder | one lever added per step, each step from the previous step's design (the waterfall): the compliant levers first, then the readings, then the not-compliant ones |
| all compliant levers | every switch marked compliant, together |
| market practice | NOT COMPLIANT: MBMA roof live load 0.57 kN/m² (IS 875-2: 0.75), ordinary frames R 3 / R 4 in any zone, the sheeting taken as bracing the purlin free flange, H/150 with no dado check, plus the lighter readings |
| solar-ready | IN_Ex1 only (`--solar`): the published design strengthened for 0.15 kN/m² of rooftop PV, nothing lighter than the gold |

A runner that offers `--ladder short` (the second-wave runner) can replace the one-lever-at-a-time ladder with three
steps for a study that would otherwise take too long: strict, every compliant lever together (sized from the strict
design), then the market bundle (sized from the all-compliant design). A resumed study keeps the ladder it was started
with. `--stop-after-min` cuts a long study into resumable chunks: no new variant starts after that many minutes, and the
next run with `--resume` continues where it stopped.

Every variant must pass everything the engine checks: every member and station, every joint, the Section 12 checks,
the analysis gates (sway, drift, rafter deflection), lambda_cr >= 5 and `india_seismic_gates.design_status`. A
variant that is not compliant passes only with its own non-compliance waived, and the study lists it. The auto-sizer
(`steel_engine/peb_autosize.py`) resizes the welded and tapered plates (depths at the member ends, flange width and
thickness, web thickness, from standard plates within the fabrication limits), the purlins and girts (with the CFS
engine), the knee / apex / splice end plates, the knee-zone column flange plate, the knee stiffeners and the base shear
keys. It is deterministic: the same job and switches give the same sizes.

## Running it from the Variations tab

Open the project in the hub, then **Design variations → Variations → PEB lever study** (the entry at the right of the
step bar). The Variations server runs the study runner itself, as one child process, in HR Steel's own checkout and
Python environment; the hub finds both through its registry (the module manifest's `uses`, which hands the server
`STELTIC_INDIA_DIR`, `STELTIC_INDIA_PYTHON` and `STELTIC_CFS_DIR`). No language model is involved and nothing leaves
the PC.

* **Does it apply?** The tab asks the runner itself: it reads the runner's own `--help` for the switches it offers and
  imports `scripts/peb_lever_study.py` in HR Steel's interpreter for the job specs and, for this project, the variant
  list and each variant's code status (`variants_for`; no analysis runs). The project name must be a job spec of
  `scripts/peb_lever_jobs.py` (the four PEB golds, or your own spec, below). Otherwise the tab says why the study does
  not apply — HR Steel or CFS Steel (IS 801, which re-designs the purlins and girts) not installed, an HR Steel without
  the study runner, a multistorey base brief, no job spec of that name — and hides the Run button.
* **The job folder** it reads (only reads: every variant runs in a copy): the project folder when it holds `cfg.py`
  (a PEB job written the way the golds are — put there by you, or staged by the Nonlinear module); otherwise HR
  Steel's design of this project, fetched through HR Steel's own API when the study starts and unpacked under
  `variations/peb_study/input/`.
* **Variants**: the strict baseline (always), each lever alone, all compliant levers together, the cumulative ladder
  (as a whole: each step starts from the previous one), the market-practice bundle and, for IN_Ex1, solar-ready —
  exactly the list the runner gives this job, with its status: compliant, reading (a reading an owner ruling rejected,
  the old unconservative treatment, a code conflict for the design engineer to decide), **NOT COMPLIANT**, or n/a
  (does not apply to this job, not selectable). The full / short ladder and the time budget are offered when the
  runner has `--ladder` / `--stop-after-min`.
* **Run**: full runs per variant (`--max-runs`, default 8) and *Keep the rows already done* (`--resume`, on by
  default; untick to start again). A resumed run is refused when the job's `cfg.py` or secondary script has changed
  since the rows were sized, or when it would switch ladder. One study at a time on the PC; Stop ends the runner and
  every full run it started. The tab streams the runner's output, one progress step per variant, and re-attaches when
  reopened.
* **Outputs**, listed with links as they are written (after each variant): `study.html`, `study.csv`, `study.json`,
  `index.html`, `study_tables.md`, `study_all.json` and the run log, under
  `<project>/variations/peb_study/work/` (`run.json` there records the command, the variants and the exit code).

## Running it on your own PEB job

1. Your job folder must be written the way the PEB golds are written:
   * `cfg.py` with the frame sections as module-level constants (`COL_T = "TI300-1000X250X14X6"`,
     `RAF_T = "TI1000-550X250X14X6"`, `RAF_P = "WI550X220X10X5"`, ...), so the sizer can rewrite them; everything
     cfg.py derives from them (knee face, end-plate geometry, base keys) then follows;
   * the declared joints in `cfg['connections']` (knee in `beam_column`, `knee_panel`, `rafter_splice` 'apex' /
     'rafter_splice', `column_base` with `shear_key`);
   * `secondary/secondary_is801.py` with `PURLIN`, `GIRT` and `FY` constants (the CFS engine design of the purlins and
     girts), as in the golds.
2. Add a job spec for your job to `scripts/peb_lever_jobs.py` (copy `TEMPLATE`): the member constants and their group,
   the dimensions that must stay equal (`links`: knee depth of column and rafter, matching depths at a splice), the
   fabrication limits, the knee column constant, the constant each end plate is built from (`splice_faces`), the
   column constants whose base section sets the 1.2 Vd demand, a typical frame line, the base fixity, the brick dado
   height, the seismic zone / R constants / route, the roof imposed-load constant and the secondary script constants.
3. Run (one heavy analysis at a time; every full run is one child process):

   ```
   python scripts/peb_lever_study.py --job <your job folder name> \
       --jobs-root <folder that holds your job> --work <output folder> \
       --cfs-root <steltic_CFS_india checkout with cfs_generic_sections> [--max-runs 8] [--resume]
   ```

   `--variants strict,joint_deliverable` runs only the named rows (the strict baseline is always run first, since
   the levers start from it); `--render --work <folder>` re-writes the outputs. From the hub, put the job folder in
   the project folder, name the project after the job spec and use the Variations tab instead (above).
4. Read `<work>/<job>/study.html` (stacked bar of kg/m² by group per variant, the governing check of each, the
   change from the strict baseline, the member sizes and purlins of each variant), `study.csv` and `study.json`.
   `<work>/index.html` puts all the studied jobs on one page.

## What the study does not change

Base plates, anchors and base gussets keep the gold sizes (only shear keys are resized); the member taper layout,
splice positions, purlin spacing and fly-brace layout are the job's; bolts and welds are not weighed (as in the gold
take-off). Plate thicknesses come from a commercial list (IS 1730 is not in the corpus).
