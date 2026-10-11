# steltic-hub-india — 2026-10: PEB variations, building-aware categories, renumbered example pickers

Branch `fix/2026-10-peb-briefs`.

## Design variations: a PEB category, and categories that fit the building

- New category **"Portal shed (PEB) layout and economy"** (`peb`, `applies_to: ["portal"]`) with 38 single-change
  templates drawn from what Indian PEB engineers vary: frame spacing 6 / 9 m, two spans with an interior column,
  roof slope 1:20 / 1:6, eave + 1.5 m, fixed / pinned bases, the rolled-frame benchmark, E250 (E250 B for an IS 800
  12.11 SMRF) / E350 plates, flange braces at every second purlin, purlin spacing, angle bracing, portal bracing,
  expandable / post-and-beam end walls, 5 t and 10 t EOT cranes, mezzanine, lean-to, dock canopy, rooftop solar PV,
  sandwich roof, collateral load, brick dado wall, Table 6 limits (H/240 masonry, span/240 plastered), terrain
  category 3, no Ka / Kc, dominant opening, cyclone belt, Zone III by both routes (IS 800 12.11 alone, or IS 18168
  adopted — optional for industrial buildings under its 1.2), and four "(tapered frames)" templates that need the PEB
  release.
- `core`, `perimeter` and `outriggers` carry `applies_to: ["multistorey"]`.
- `library.building_kind(brief)` reads the base brief: two or more storeys → multistorey; shed / warehouse / portal /
  PEB / crane-bay words → portal; else unknown. When the user has not ticked categories (auto and instructions modes,
  and the offline plan), only the categories that apply are used; a user's explicit choice is always honoured; an
  unknown kind keeps all eleven. The LLM plan prompt states the building kind and says what not to propose.
- `/api/library` reports `applies_to` per category. UI wording and `windows/SelfTest.ps1` updated (eleven categories).
- Still to come with the PEB engine release (`steltic_india/docs/PEB_TAPERED_SCOPE.md` 6.2): PEB metrics
  (`mass_kg`, kg/m², reactions), INR rates and the PEB default equation.

## Example pickers (the 2026-10 renumbering)

- HR Steel manifest: offers IN_Ex6 … IN_Ex20 (`in6` … `in20`) in the new order — single-storey industrial first;
  IN_Ex1 … IN_Ex5 (PEB) are added when the PEB release lands. CFS manifest: `in1` … `in15` in the new order.
- README, admin README and the batch grammar docs: inN = IN_ExN (HR, in1 … in20) / IN_CFS_ExN (CFS, in1 … in15).
- Tests: `tests/test_variations.py` — eleven categories; building kind; building-aware plans and prompts; the library
  API and an auto plan on a shed brief. Full suite: **143 passed**.
