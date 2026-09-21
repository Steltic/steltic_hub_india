"""The ten variation categories, with generic variation templates -- India edition.

Generalised from a 100-model study of one tower and re-cast for IS 800:2007 / IS 1893 (Part
1):2016 + Amd 1/2 / IS 875 / IS 18168:2023: nothing here names a grid, a level or a member size
that a brief might not define, and every system named has an IS 1893 Table 9 row. The templates
do three jobs -- they are the offline (MOCK) plan, they are the examples the LLM is shown so its
variations are of this kind, and they document what each category means to the user.

Owner rulings the templates respect: no foreign design basis (D3) -- no BRB, SPSW, composite
walls or device chapters presented as a system; OMRF and OCBF are banned in Zones III-V (D4,
Table 9 Note 1); occupancy drives I (D8); office imposed load 4.0 kN/m2 (D9).
"""
from __future__ import annotations

CATEGORIES = [
    {"id": "problem", "label": "Establish the problem",
     "blurb": "Site (IS 1893 zone, soil type), importance factor, analysis method, drift and wind targets — what the reference design is up against.",
     "templates": [
         ("Benchmark: Zone II site", "Change the site to seismic Zone II (Z = 0.10) with the same soil type; everything else identical.", "Quantifies the high-seismic premium (a Zone II site also admits OMRF / OCBF)"),
         ("Benchmark: Zone V site", "Change the site to seismic Zone V (Z = 0.36) with the same soil type; everything else identical.", "The worst-case seismic premium"),
         ("Soft soil (Type III)", "Take the soil as Type III (soft) instead of the briefed type; Sa/g from IS 1893 6.4.2 accordingly.", "Spectrum plateau extends to 2.0 s"),
         ("Rock (Type I)", "Take the soil as Type I (rock or hard soil).", "Shorter plateau, lower long-period demand"),
         ("Equivalent static method", "Use the equivalent static method (IS 1893 7.6); if 7.6 / 7.7 requires dynamic analysis for this building, stop and cite the clause.", "Analysis-method gate"),
         ("Importance factor 1.5", "Take the importance factor I = 1.5 (IS 1893 Table 8 (i)) as if the building were an important / lifeline structure.", "Cost of the importance class"),
         ("Drift target 80 % of 0.004", "Design to a storey drift no greater than 80 % of the IS 1893 7.11.1.1 limit (0.0032 h).", "Stiffness margin for nonlinear checks"),
         ("Drift target 60 % of 0.004", "Design to a storey drift no greater than 60 % of the IS 1893 7.11.1.1 limit (0.0024 h).", "Stiffness margin for nonlinear checks"),
         ("Wind deflection H/500", "Limit the wind sway to H/500 (IS 800 Table 6 uses H/300 for elastic cladding); seismic unchanged.", "Does wind serviceability touch any member?"),
     ]},
    {"id": "core", "label": "Braced-frame configuration",
     "blurb": "Brace pattern, extent and placement of the braced bays; extra braced lines; vertical combinations (IS 800 12.7 / 12.8 / 12.9).",
     "templates": [
         ("Two-storey X braces", "Configure the braced bays as two-storey X bracing.", "Tier behaviour, brace tonnage"),
         ("Single-storey X braces", "Configure the braced bays as single-storey X bracing (IS 800 12.8.1.2 both tension and compression braces).", "Brace count vs unbalanced force"),
         ("Chevron (inverted V) braces", "Use chevron bracing with the beam designed for the 12.8.4 unbalanced force.", "Beam-critical braced bays"),
         ("Single diagonal, alternating", "Use single diagonal braces alternating direction storey by storey.", "Fewest braces; unbalanced force removed"),
         ("Braced bays extended in the weak direction", "Extend the braced lines to the full building depth in the weak direction.", "Weak-direction stiffness"),
         ("Braced bays extended in the strong direction", "Extend the braced lines to the full building length in the strong direction.", "Strong-direction stiffness"),
         ("Third braced line each direction", "Add one more braced line in each principal direction.", "Redundancy; torsional stiffness"),
         ("Braced bays moved to one end", "Move the braced bays to one end of the plan.", "Eccentric bracing lines: IS 1893 Table 5(i) torsion"),
         ("Two braced cores at the ends", "Split the braced bays into two groups at opposite ends of the plan.", "Torsional stiffness from separated cores"),
         ("Braced bays in one direction only", "Brace one direction only; resist the other with moment frames alone (mixed system, one R per direction, IS 1893 7.2.7).", "Per-direction system bookkeeping"),
         ("Braces terminate below the top floors", "Terminate the braces several storeys below the roof; moment frames alone above (declare the vertical combination and its R).", "Open upper floors — penalties?"),
     ]},
    {"id": "system", "label": "Lateral system type",
     "blurb": "The lateral load-resisting system with its IS 1893 Table 9 R: SMRF (R 5.0), SCBF (R 4.5), EBF (R 5.0), OMRF (R 3.0) / OCBF (R 4.0) in Zone II only, moment frames + braces per direction; IS 18168 where mandatory.",
     "templates": [
         ("SCBF only", "Use special concentrically braced frames alone (IS 800 12.8 with IS 18168, R = 4.5, Table 9 (ii)(b)).", "The braced building"),
         ("EBF with short links", "Use eccentrically braced frames (IS 1893 Table 9 (ii)(c), R = 5.0) with shear links designed to IS 18168 clause 11 / 12.3.", "Link-based ductility"),
         ("SMRF only, all perimeter bays", "Use special moment resisting frames alone on every perimeter bay (IS 800 12.11 with IS 18168, R = 5.0).", "The pure moment-frame building"),
         ("SMRF only, perimeter plus interior lines", "Use special moment resisting frames on the perimeter and on interior column lines.", "Space-frame-like SMRF"),
         ("SMRF perimeter + interior SCBF", "Perimeter SMRF with interior SCBF; take the SCBF R = 4.5 for both (no dual-system claim, IS 1893 Table 9 has no steel dual row).", "Mixed system at the lower R"),
         ("OMRF (Zone II only)", "Use ordinary moment resisting frames (IS 800 12.10, R = 3.0); if the site is in Zone III–V, stop: OMRF is not permitted (IS 1893 Table 9 Note 1).", "Zone II economy; the D4 gate"),
         ("OCBF (Zone II only)", "Use ordinary concentrically braced frames (IS 800 12.7, R = 4.0); if the site is in Zone III–V, stop: OCBF is not permitted (IS 1893 Table 9 Note 1).", "Zone II economy; the D4 gate"),
         ("Portal frames in the transverse direction", "Use rigid portal frames transversely and braced bays longitudinally (one R per direction).", "Industrial-building layout"),
     ]},
    {"id": "perimeter", "label": "Perimeter frames",
     "blurb": "Which faces carry moment frames, spandrel depth, column orientation and section, share of base shear.",
     "templates": [
         ("Moment frames on the short faces only", "Place moment frames on the short faces only; long faces gravity.", "Which faces earn their connections"),
         ("Moment frames on the long faces only", "Place moment frames on the long faces only; short faces gravity.", "Which faces earn their connections"),
         ("Framed tube", "Close perimeter column spacing with deep spandrels and rigid connections on all faces (framed tube).", "Tube action"),
         ("Deeper spandrels", "Use the deepest practical spandrel beams (NPB / WPB) on all faces.", "Depth for stiffness"),
         ("Spandrel depth capped", "Cap the spandrel depth to suit the window head.", "Architect's constraint"),
         ("Built-up box corner columns", "Use built-up welded box sections (IS 2062 plates) for the corner columns.", "Biaxial strong-column/weak-beam at corners"),
         ("Moment-frame columns strong axis in the weak direction", "Orient all moment-frame columns with their strong axis in the weak plan direction.", "Orientation lever"),
         ("Moment frames on the middle bays only", "Keep moment connections only on the middle bays of each face; pin the corner bays.", "Fewer connections"),
         ("Moment frames sized for 25 % of VB", "In a mixed system size the moment frames for at least 25 % of the design base shear (IS 1893 7.2.7 spirit); braces carry the rest.", "Minimum moment frame"),
     ]},
    {"id": "outriggers", "label": "Outriggers and belt trusses",
     "blurb": "Outrigger and belt levels for a tall braced core.",
     "templates": [
         ("Outriggers at the roof", "Add outrigger trusses core-to-perimeter at the roof.", "Roof outrigger"),
         ("Outriggers at about two-thirds height", "Add outrigger trusses core-to-perimeter at about 0.65 H.", "Optimum single outrigger"),
         ("Two outrigger levels", "Add outrigger trusses at mid-height and at the roof.", "Two-level outrigger"),
         ("Belt trusses only", "Add belt trusses at mid-height and at the roof (no outriggers).", "Belt action"),
         ("Outrigger plus belt at one level", "Add outriggers and a belt truss at the same level near 0.65 H.", "Combined"),
     ]},
    {"id": "geometry", "label": "Geometry and mass",
     "blurb": "Storey height, storey count, plan proportions, bay layout, floor and cladding weight (IS 875 Part 1), lobby, setbacks, penthouse, basement.",
     "templates": [
         ("Storey height reduced", "Reduce the typical storey height by about 5 %.", "Height lever"),
         ("Storey height increased", "Increase the typical storey height by about 5 %.", "Height lever"),
         ("Fewer storeys", "Remove two storeys.", "Height lever; crosses the 15 m IS 18168 / Table 9 gates?"),
         ("More storeys", "Add two storeys.", "Height lever; dynamic-analysis gate (IS 1893 7.7.1)"),
         ("Narrower plan", "Reduce the plan width in the weak direction by about 12 %.", "Slenderness"),
         ("Longer plan", "Increase the plan length in the strong direction by about 25 %.", "Aspect ratio"),
         ("Column-free interior", "Remove the interior column line in the weak direction (two wider bays).", "Column-free units"),
         ("Lighter floors", "Use a lighter floor system (reduce the floor dead load by about 0.75 kN/m²).", "Mass down: seismic down, wind unchanged"),
         ("Heavier slab", "Use a thicker normal-weight slab (add about 1.0 kN/m² dead load).", "Mass up"),
         ("Heavy cladding", "Use precast cladding at about 1.5 kN/m² of wall.", "Mass up at the perimeter"),
         ("Taller ground storey", "Increase the ground-floor height for a lobby; keep it stiffer than the storey above (IS 1893 Table 6(i), Amd 2).", "Soft-storey screen"),
         ("Setback at the top floors", "Reduce the top four floors to about 75 % of the plan.", "Vertical geometric irregularity / mass step"),
         ("Mechanical penthouse", "Add a framed mechanical penthouse on the core at the roof.", "Appendage"),
         ("One basement level", "Add one basement level with the lateral system continuous to it.", "Base definition"),
     ]},
    {"id": "members", "label": "Members and materials",
     "blurb": "Column family and grade (IS 808 WPB/NPB, built-up boxes, IS 2062 E250/E350), brace shapes (IS 808 / IS 1161 CHS), splice rhythm, beam standardisation.",
     "templates": [
         ("Columns from a shallower family", "Choose all columns from a shallower IS 808 WPB family.", "Column depth lever"),
         ("Columns from a deeper family", "Choose all columns from a deeper IS 808 WPB family (IS 800 Table 2 section class limits).", "Column depth lever"),
         ("Built-up box core columns", "Use built-up welded box sections for the core columns.", "Overturning axial"),
         ("Higher-strength columns", "Use IS 2062 E350 for all columns.", "Material lever"),
         ("E410 columns", "Use IS 2062 E410 for columns (verify plastic section class at the higher yield).", "Material lever"),
         ("WPB braces", "Use IS 808 WPB sections instead of tubes for braces.", "Brace shape"),
         ("CHS braces", "Use IS 1161 circular hollow sections for braces.", "b/t vs KL/r trade"),
         ("Built-up box braces at the base", "Use built-up box braces at the lower storeys.", "Heavy-brace zone"),
         ("Column splices every three storeys", "Change column sizes every three storeys.", "Splice rhythm"),
         ("Constant-depth moment-frame beams", "Use one NPB depth for all moment-frame beams at all levels.", "Connection standardisation"),
     ]},
    {"id": "bases", "label": "Bases and foundation stance",
     "blurb": "Pinned or fixed bases, grade beams, foundation-flexibility envelope.",
     "templates": [
         ("All bases pinned", "Pin all lateral-system column bases.", "Base condition"),
         ("Moment-frame bases pinned, braced-frame bases fixed", "Pin the moment-frame column bases; fix the braced-frame column bases.", "Mixed base condition"),
         ("Fixed bases with grade beams", "Fix all bases and tie the braced-frame columns with grade beams (uplift shared).", "Grade-beam tie"),
         ("Foundation-flexibility envelope", "Report pinned and fixed results as an envelope.", "Sensitivity, not a design"),
     ]},
    {"id": "seismic", "label": "Seismic design choices within the system",
     "blurb": "Strong-column/weak-beam ratios (IS 800 12.11.3.2, IS 18168 8.2), connection types, doubler avoidance, brace slenderness, chevron beam margin, brace sizing rhythm, capacity-protected columns (IS 18168 5.5 Ω), collectors, torsion.",
     "templates": [
         ("SCWB ratio at least 1.5", "Require a strong-column/weak-beam ratio of at least 1.5 at every moment-frame joint (IS 800 12.11.3.2 asks 1.2; IS 18168 8.2 1.4 with Ry).", "Mechanism control for nonlinear checks"),
         ("SCWB ratio at least 2.0", "Require a strong-column/weak-beam ratio of at least 2.0 at every moment-frame joint.", "Mechanism control"),
         ("Welded flange moment connections", "Use complete-penetration welded flange connections (IS 800 12.11.2, IS 9595) at every moment joint.", "Full beam stiffness; panel-zone demand"),
         ("Bolted end-plate connections", "Use bolted extended end-plate moment connections with IS 4000 HSFG bolts.", "Field-bolted option"),
         ("No doubler plates", "Choose columns so that no panel-zone doubler plates are needed (IS 800 12.11.2.3).", "Fabrication simplicity"),
         ("Stocky braces", "Size braces for a slenderness KL/r of about 50.", "Post-buckling reserve"),
         ("Slender braces", "Size braces for a slenderness KL/r close to the IS 800 12.8.2.1 limit.", "Light braces"),
         ("Chevron beam margin", "Size chevron beams with a 1.25 factor on the 12.8.4 unbalanced brace force.", "Beam robustness"),
         ("Brace sizes per storey", "Change brace sizes every storey (minimum weight).", "Minimum weight"),
         ("Brace sizes per four-storey tier", "Use one brace size per four-storey tier.", "Standardisation"),
         ("Capacity-protected columns at full Ω", "Design the braced-frame columns to the IS 18168 5.5 overstrength combination with no reduction.", "Conservative column chain"),
         ("Collectors to both bases", "Design collectors to the larger of the overstrength (IS 18168 5.5 Ω) and brace expected-strength forces; report both.", "Collector basis"),
         ("Accidental torsion with the design eccentricity", "Apply the IS 1893 7.8.2 design eccentricity (1.5 esi + 0.05 bi and esi − 0.05 bi) explicitly; report the torsion ratio.", "Torsion bookkeeping"),
     ]},
    {"id": "detailing", "label": "Detailing class and code gates",
     "blurb": "IS 18168 applied or not (Zone II optional, Zones III–V mandatory), IS 800 Section 12 class, diaphragm classification (7.6.4), separation joints (7.11.3).",
     "templates": [
         ("IS 18168 applied in Zone II", "Apply IS 18168:2023 in full even though the site is Zone II (where it is optional).", "Cost of the stricter detailing"),
         ("Rigid roof diaphragm declared", "Declare the roof diaphragm rigid with roof plan bracing (IS 1893 7.6.4) instead of flexible.", "Diaphragm classification lever"),
         ("Seismic joint between wings", "Split an irregular plan into structurally independent units with IS 1893 7.11.3 separation joints.", "Removes the re-entrant corner"),
         ("Composite floors modelled", "Model the composite metal-deck floors as rigid diaphragms with the deck spanning between secondary beams at 3 m.", "Diaphragm and beam scope"),
     ]},
]

# systems the Nonlinear (SNL-IN) tools can represent (finalist eligibility): every IS 800 Section 12
# frame type; everything else is listed as "not verifiable with the current nonlinear tools" (and,
# by owner ruling D3, has no India design basis in the first place)
REPRESENTABLE = ("SMRF / SMF", "SCBF", "OMRF", "OCBF", "EBF", "moment frames + braces per direction")
NOT_REPRESENTABLE_WORDS = ("BRB", "buckling-restrained", "SPSW", "plate shear wall", "C-PSW", "composite plate",
                           "isolat", "damper", "tuned mass", "TMD", "viscous")


def category(cid: str) -> dict | None:
    return next((c for c in CATEGORIES if c["id"] == cid), None)


def offline_plan(n: int, categories: list[str] | None = None, instructions: str = "") -> list[dict]:
    """A deterministic plan for MOCK / no-LLM use: M001 is the base, then templates round-robin
    over the chosen categories (all ten when none are chosen)."""
    chosen = [c for c in CATEGORIES if not categories or c["id"] in categories] or CATEGORIES
    out = [{"id": "M001", "title": "Base design as briefed", "group": "reference",
            "change": "", "why": "Reference"}]
    pools = [list(c["templates"]) for c in chosen]
    labels = [c["label"] for c in chosen]
    i = 0
    while len(out) < max(1, n) and any(pools):
        k = i % len(pools)
        if pools[k]:
            title, change, why = pools[k].pop(0)
            out.append({"id": f"M{len(out) + 1:03d}", "title": title, "group": labels[k],
                        "change": change, "why": why})
        i += 1
    return out[:max(1, n)]


def examples_for_prompt(categories: list[str] | None = None, per: int = 4) -> str:
    chosen = [c for c in CATEGORIES if not categories or c["id"] in categories] or CATEGORIES
    lines = []
    for c in chosen:
        lines.append(f"- {c['label']}: {c['blurb']}")
        for title, change, why in c["templates"][:per]:
            lines.append(f"    * {title} -- {change} (why: {why})")
    return "\n".join(lines)
