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
| all compliant levers | every switch marked compliant, together |
| market practice | NOT COMPLIANT: MBMA roof live load 0.57 kN/m² (IS 875-2: 0.75), ordinary frames R 3 / R 4 in any zone, the sheeting taken as bracing the purlin free flange, H/150 with no dado check, plus the lighter readings |

Every variant must pass everything the engine checks: every member and station, every joint, the Section 12 checks,
the analysis gates (sway, drift, rafter deflection), lambda_cr >= 5 and `india_seismic_gates.design_status`. A
variant that is not compliant passes only with its own non-compliance waived, and the study lists it. The auto-sizer
(`steel_engine/peb_autosize.py`) resizes the welded and tapered plates (depths at the member ends, flange width and
thickness, web thickness, from standard plates within the fabrication limits), the purlins and girts (with the CFS
engine), the knee / apex / splice end plates, the knee-zone column flange plate, the knee stiffeners and the base shear
keys. It is deterministic: the same job and switches give the same sizes.

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
   the levers start from it); `--render --work <folder>` re-writes the outputs.
4. Read `<work>/<job>/study.html` (stacked bar of kg/m² by group per variant, the governing check of each, the
   change from the strict baseline, the member sizes and purlins of each variant), `study.csv` and `study.json`.
   `<work>/index.html` puts all the studied jobs on one page.

## What the study does not change

Base plates, anchors and base gussets keep the gold sizes (only shear keys are resized); the member taper layout,
splice positions, purlin spacing and fly-brace layout are the job's; bolts and welds are not weighed (as in the gold
take-off). Plate thicknesses come from a commercial list (IS 1730 is not in the corpus).
