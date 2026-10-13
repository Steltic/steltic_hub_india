# steltic-hub-india — 2026-10: PEB release

Branch `fix/2026-10-peb-tapered`, one PR with the 2026-10 renumbering commit before it (`CHANGES_2026-10-peb-briefs.md`,
prepared as `fix/2026-10-peb-briefs`, never pushed on its own). It goes with the steltic_india PEB release,
which makes the five PEB briefs runnable (`docs/PEB_TAPERED_SCOPE.md` in steltic_india).

- **Example picker.** The HR tab (`catalog/steltic_india.json`) now offers in1 … in5, the PEB sheds, ahead of in6 …
  in20.
- **Variations metrics** (`variations/metrics.py`):
  - **Row mass.** `member_schedule.csv` rows that carry the engine's `mass_kg` use it. Stepped tapered segments and
    welded plate sections are weighed from their plates. `steel_t_basis` says how many rows did.
  - **Section names.** `section_mass_kg_m` reads welded plate I (`WI<d>X<bf>X<tf>X<tw>`) and web-tapered
    (`TI<d1>-<d2>X…`, at the mean depth) names.
  - **Whole-building tonnage.** A package with `calc_package['peb']['tonnage']` reports `peb_kg_m2` (primary, bracing,
    crane, canopy, secondary, connections, total) and `peb_steel_t`. `steel_kg_m2` is then the whole-building figure
    PEB engineers compare, including purlins, girts and connection plates. `steel_kg_m2_basis` says so.
  - **Tapered frames.** These are marked `tapered` and are not representable in the nonlinear tools (scope section 9:
    no tapered fibre section yet). The reason is given in `not_representable_because`.
- **Eligibility** (`variations/scoring.py`, Score tab). A new visible rule, `tapered_exempt`, is on by default:
  tapered PEB frames are not rejected by "Representable in the Nonlinear tools" (scope 6.2). The criteria pill says
  so. Switch the rule off to apply the nonlinear rule to them as well.
- **Tests.** `tests/test_variations_peb_mass.py` (4 tests) covers the plate mass, the welded / tapered names, the PEB
  tonnage and the tapered exemption. The suite is 147 passed (143 before).
