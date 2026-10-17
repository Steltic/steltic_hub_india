"""The twelve variation categories, with generic variation templates -- India edition.

Generalised from a 100-model study of one tower and re-cast for IS 800:2007 / IS 1893 (Part
1):2016 + Amd 1/2 / IS 875 / IS 18168:2023: nothing here names a grid, a level or a member size
that a brief might not define, and every system named has an IS 1893 Table 9 row. The templates
do three jobs -- they are the offline (MOCK) plan, they are the examples the LLM is shown so its
variations are of this kind, and they document what each category means to the user.

Owner rulings the templates respect: no foreign design basis (D3) -- no BRB, SPSW, composite
walls or device chapters presented as a system; OMRF and OCBF are banned in Zones III-V (D4,
Table 9 Note 1); occupancy drives I (D8); office imposed load 4.0 kN/m2 (D9).

Building-aware (2026-10): a category may carry "applies_to" -- ["portal"] (single-storey sheds, warehouses and PEB
buildings) or ["multistorey"]; categories without it apply to both. building_kind() reads the base brief, and the
plan / prompt examples offer only the categories that apply when the user has not chosen categories himself (a user
may still tick any category). The "peb" category is drawn from what Indian PEB engineers vary in practice: layout,
loading basis and detailing choices that move steel between frames, purlins, bracing and foundations. Templates
marked "(tapered frames)" need the PEB release (tapered welded members); on a frame without tapered members the design
agent reports them as not applicable.

PEB-LEVERS (2026-10): the "peb_levers" category -- Indian PEB practice levers -- maps one-to-one to the switches of the
HR engine's cfg['peb_rules'] (steel_engine/peb_levers.py) and to the brief-level variants of the deterministic lever
study (scripts/peb_lever_study.py, steel_engine/peb_autosize.py).  Each template names the switch to set, the code basis
and whether the reading is compliant; the market-practice template is labelled NOT COMPLIANT.  PEB_LEVERS.md (next to
this package) explains how a design engineer runs the deterministic lever study on their own PEB job.
"""
from __future__ import annotations

import re

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
    {"id": "peb", "label": "Portal shed (PEB) layout and economy", "applies_to": ["portal"],
     "blurb": "Frame spacing, span arrangement, roof slope, eave height, base fixity, taper and haunch, plate grade, restraint, bracing, end walls, cranes, roof add-ons, cladding and the IS 875 / IS 800 / IS 1893 basis choices that set the steel per square metre.",
     "templates": [
         ("Frame spacing 6 m", "Set the main frame spacing to 6 m (same building length; frames added).", "Lighter frames and purlins vs more frames, anchor bolts and footings"),
         ("Frame spacing 9 m", "Set the main frame spacing to 9 m (same building length; frames removed).", "Fewer frames and footings vs heavier purlins and girts"),
         ("Two spans with an interior column", "Split the clear span into two equal spans with one interior column line (where the use allows a column in the floor).", "Modular frame: lighter rafters vs a column line in the floor"),
         ("Roof slope 1:20", "Change the roof slope to 1:20 (same eave height).", "Flatter roof: roof Cpe, rafter bending, drainage and ponding"),
         ("Roof slope 1:6", "Change the roof slope to 1:6 (same eave height).", "Steeper roof: arch action, roof Cpe, taller gable"),
         ("Eave height plus 1.5 m", "Raise the eave height by 1.5 m (racking clearance).", "What the extra clear height costs in columns and wind"),
         ("Fixed column bases", "Fix the main frame column bases (bases and anchors designed for 1.2 Mp of the column, IS 800 12.12).", "Lighter frames and less sway vs bigger base plates, anchors and footings"),
         ("Pinned column bases", "Pin the main frame column bases (where the base brief has fixed bases).", "Smaller footings vs heavier frames and more sway"),
         ("Prismatic rolled frame", "Use IS 808 rolled sections for the columns and rafters (no plate fabrication), with haunches as connection details.", "The conventional-steel benchmark a PEB competes with"),
         ("All frame plates E250", "Use IS 2062 E250 for all frame plates (E250 B where IS 800 12.11.1 applies).", "Grade lever: E350 is the PEB norm, E250 B is what an IS 800 12.11 SMRF must use"),
         ("All frame plates E350", "Use IS 2062 E350 for all frame flanges and webs (where the lateral system's rules allow it).", "Grade lever the other way"),
         ("Flange braces at every second purlin", "Provide inner-flange braces at every second purlin and girt only, including the knee regions.", "Fewer braces vs lateral-torsional buckling capacity near the knees"),
         ("Purlin spacing 1.8 m", "Space the purlins and girts at about 1.8 m.", "Fewer purlins vs longer unbraced outer flange and heavier purlins"),
         ("Angle bracing in place of rods", "Use single angles or CHS for the longitudinal X-bracing instead of round bars.", "Compression-capable bracing (required where tension-only rods are not admitted)"),
         ("Portal bracing at a door bay", "Replace one end-bay X-brace with a portal (moment) frame to keep a door opening clear.", "Operational constraint vs bracing cost"),
         ("Expandable end wall", "Design one end wall as a full main frame (for a future extension) instead of post-and-beam.", "Cost of future expandability"),
         ("Post-and-beam end walls", "Design both end walls as post-and-beam bearing frames with wind columns.", "The usual economic end wall"),
         ("Add a 5 t EOT crane", "Add a 5 t pendant-operated EOT crane on column brackets along the full length (crane data per the EOR).", "Crane premium on columns, brackets, bases and sway"),
         ("Add a 10 t EOT crane", "Add a 10 t pendant-operated EOT crane on column brackets along the full length (crane data per the EOR).", "Crane premium"),
         ("Mezzanine along one side", "Add a 6 m wide storage mezzanine along one long side at about half the eave height.", "Frame interaction with a floor"),
         ("Lean-to on one side", "Add an attached lean-to along one long side, span about half the main span.", "A common add-on; wind and frame interaction"),
         ("Dock canopy", "Add a 6 m cantilever canopy over the loading docks along one long side, at about 5.5 m clear height.", "Canopy uplift and moment into the main columns"),
         ("Rooftop solar PV", "Add rooftop solar PV as a named roof dead load of 0.15 kN/m2 over the whole roof (owner value; the PV vendor confirms).", "Very common on Indian sheds now: rafters, purlins, seismic weight"),
         ("Insulated sandwich roof", "Replace single-skin sheeting with insulated sandwich panels (dead load per the manufacturer, EOR value).", "Cold stores and pharma: dead load and deflection"),
         ("Collateral load 0.25 kN/m2", "Raise the collateral (services, sprinklers, lighting) dead load to 0.25 kN/m2.", "Services loading basis"),
         ("Brick dado wall 3 m", "Add a 230 mm brick masonry dado wall to 3 m all round (sheeting above) and use the IS 800 Table 6 column limit for masonry / brittle cladding.", "Most Indian sheds have one: sway limit and wall wind"),
         ("Sway limit H/240", "Limit the column sway to H/240 (IS 800 Table 6, masonry / brittle cladding) instead of H/150.", "Serviceability basis"),
         ("Rafter limit span/240", "Limit the rafter vertical deflection to span/240 (IS 800 Table 6, plastered sheeting / false ceiling).", "Serviceability basis"),
         ("Terrain category 3", "Take terrain category 3 (IS 875-3 6.3.2: developed industrial area) instead of the briefed category.", "The most argued wind input on an industrial estate"),
         ("No Ka or Kc reduction", "Take Ka = 1.0 and Kc = 1.0 on the frames and purlins (no IS 875-3 7.2.2 area averaging or 7.3.3.13 combination reduction).", "What the IS 875-3:2015 reductions are worth"),
         ("Dominant opening", "Treat one long wall as having a dominant opening (large roller-shutter doors) and take Cpi per IS 875-3 7.3.2.", "Internal pressure drives uplift, purlins and anchors"),
         ("Cyclone-belt site", "Move the site to an east-coast cyclone-belt city (k4 per IS 875-3 6.3.4).", "The coastal wind premium"),
         ("Seismic Zone III site, IS 800 SMRF", "Move the site to Zone III: OMRF and ordinary braced frames are not permitted (IS 1893 Table 9 Note 1); design SMRF portals to IS 800 12.11 (E250 B plates, 12.11.1) and SCBF bracing.", "The D4 premium on a PEB"),
         ("Seismic Zone III site, IS 18168 SMRF", "Move the site to Zone III and design SMRF portals with IS 18168:2023 adopted voluntarily (1.2: optional for industrial buildings): E350 allowed (5.2), Table 2 limits, doubly symmetric sections; SCBF bracing.", "Which Zone III route is cheaper"),
         ("Prismatic welded columns (tapered frames)", "Use prismatic welded columns instead of tapered columns (rafters unchanged).", "Fabrication simplicity vs steel weight"),
         ("Longer haunch (tapered frames)", "Extend the rafter taper to 30 % of the span from each knee.", "Knee moment shared over a longer deep zone"),
         ("Shorter haunch (tapered frames)", "Limit the rafter taper to 10 % of the span from each knee.", "Lighter rafter vs deeper knee demand"),
         ("Minimum web 6 mm (tapered frames)", "Use a minimum web thickness of 6 mm in the welded frame members (instead of the briefed minimum).", "Fewer slender webs and stiffeners vs plate weight"),
     ]},
    {"id": "peb_levers", "label": "Indian PEB practice levers", "applies_to": ["portal"],
     "blurb": "The code readings and vendor practices that move steel on an Indian PEB, each one a switch of cfg['peb_rules'] (HR engine, peb_levers) on top of the strict gold rulings: joint demand, LTB C1, sway limit with a brick dado, base shear keys, rod bracing, thin purlins, collateral in uplift, the seismic route, and the market-practice bundle (not compliant).",
     "templates": [
         ("Joints for the deliverable moment", "Set cfg['peb_rules']['joint_demand'] = 'deliverable': design the knees and apex of the ordinary moment frames for the lesser of 1.2 Mp and 1.2 x the largest factored joint moment over all strength combinations including the IS 800 12.2.3 rows (IS 800 12.10.2.1); SMRF and IS 18168 joints stay at 1.2 Mp / 1.1 Ry Mp.", "Owner ruling D-T7 designs every PEB joint for 1.2 Mp of the face section; 12.10.2.1 allows the lesser value for an OMF"),
         ("LTB with the moment-gradient C1", "Set cfg['peb_rules']['ltb_c1'] = 'gradient': take C1 from the end moments of each restrained length (IS 800 Table 42, K = 1.0) instead of C1 = 1.0, where the moment diagram along the length is known from the stepped segments.", "Owner ruling D-T1 uses C1 = 1.0 on tapered members (conservative)"),
         ("Sway H/150 with a dado-top check", "Set cfg['peb_rules']['sway_limit'] = 'H150_with_dado_check' and 'dado_height_mm' to the brick dado height: eave sway H/150 for the metal cladding (IS 800 Table 6 elastic cladding) and the column displacement at the dado top <= dado height / 240 (masonry).", "H/240 at the eave for a 3 m dado is what forces fixed bases on many sheds"),
         ("Shear keys for the analysis base shear", "Set cfg['peb_rules']['base_shear_key'] = 'analysis': size the base shear transfer for the largest factored base shear over all strength combinations (12.2.3 rows included) instead of 1.2 Vd of the column (owner ruling O1); report it as a reading the O1 ruling rejected.", "1.2 Vd keys of 600-1,600 kN on pinned bases with 40-100 kN of thrust"),
         ("Tension-rod X-bracing (Zone II OCBF only)", "Set cfg['peb_rules']['bracing'] = 'tension_rods' with round-bar braces designed in tension only; accepted on a Zone II OCBF only and reported as NOT compliant with IS 800 Section 12 (12.1 all zones; 12.7.2.1 KL/r <= 120; 12.7.2.3 tension share 30-70 %).", "Owner ruling D-T4 uses CHS / WPB braces designed for compression; rods are vendor practice"),
         ("Thin G350 purlins and girts", "Set cfg['peb_rules']['purlins'] = 'thin_G350': design the purlins and girts as generic thin lipped zeds of IS 277 grade 350 coil (Fy 350 MPa, inside IS 801:1975 Table 2's 36 kgf/mm2 row) instead of IS 811 zeds at Fy 240 MPa; same spacing.", "IS 811 stops at 250 mm deep and the gold reads Fy 240 MPa"),
         ("Collateral and PV in the 0.9 DL rows", "Set cfg['peb_rules']['collateral_in_uplift'] = True: count D_roof_collateral and D_roof_pv in the 0.9 DL uplift rows too (the treatment before the 2026-10 first wave; the engine now leaves them out).", "Shows what the unconservative old treatment hid on a solar-ready roof"),
         ("Seismic route: OMRF R 3, no IS 18168", "Design the transverse frames as ordinary moment frames (R 3, IS 800 12.10) and the bracing as ordinary braced frames (R 4), without IS 18168, slender webs allowed: permitted in Zone II; in Zone III with I <= 1.0 permitted by IS 800 12.10.1.1 but not by IS 1893 Table 9 Note 1 (state the conflict); in Zones IV-V market practice, NOT compliant.", "Indian Zone III-IV PEB tenders still specify R 3 rigid portals"),
         ("All compliant levers together", "Apply every compliant switch at once: joint_demand 'deliverable' (OMF), ltb_c1 'gradient', sway_limit 'H150_with_dado_check' where a dado exists, purlins 'thin_G350'.", "The lightest code-compliant reading of the same shed"),
         ("Market practice bundle (NOT COMPLIANT)", "Design as many Indian PEB vendors do and label the result NOT COMPLIANT: roof imposed load 0.57 kN/m2 (MBMA) instead of IS 875-2 0.75, ordinary frames R 3 / R 4 in any zone, the sheeting taken as bracing the purlin free flange, H/150 sway with no dado check, with the deliverable joints, gradient C1, thin purlins, analysis base shear and (Zone II) rods.", "What a vendor quote is built on, so the design engineer can see the gap"),
     ]},
    {"id": "core", "label": "Braced-frame configuration", "applies_to": ["multistorey"],
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
    {"id": "perimeter", "label": "Perimeter frames", "applies_to": ["multistorey"],
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
    {"id": "outriggers", "label": "Outriggers and belt trusses", "applies_to": ["multistorey"],
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


def building_kind(brief: str) -> str | None:
    """'portal' (single-storey shed / warehouse / PEB), 'multistorey', or None when the brief does not say.

    Two or more storeys ('4-storey', '5levels', '8 storeys') make it multistorey; otherwise single-storey or shed
    words (portal, PEB, pre-engineered, shed, warehouse, crane bay, big-box) make it a portal building."""
    t = brief or ""
    n = [int(m.group(1)) for m in re.finditer(r"(?<![\d.])(\d{1,3})[- ]?(?:storey|story|level)s?(?![a-z])", t, re.I)]
    if n and max(n) >= 2:
        return "multistorey"
    if re.search(r"single[- ]stor(?:e)?y|\bportal|\bPEB\b|pre-engineered|\bshed\b|warehouse|crane[- ]bay|big-box", t, re.I):
        return "portal"
    if n:
        return "portal" if max(n) == 1 else "multistorey"
    return None


def applies(c: dict, kind: str | None) -> bool:
    """A category applies to a building kind when it has no 'applies_to' or lists the kind (unknown kind: all apply)."""
    return kind is None or not c.get("applies_to") or kind in c["applies_to"]


def _chosen(categories: list[str] | None, kind: str | None) -> list[dict]:
    if categories:
        return [c for c in CATEGORIES if c["id"] in categories] or CATEGORIES
    return [c for c in CATEGORIES if applies(c, kind)] or CATEGORIES


def offline_plan(n: int, categories: list[str] | None = None, instructions: str = "", kind: str | None = None) -> list[dict]:
    """A deterministic plan for MOCK / no-LLM use: M001 is the base, then templates round-robin
    over the chosen categories (when none are chosen: every category that applies to the building kind)."""
    chosen = _chosen(categories, kind)
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


def examples_for_prompt(categories: list[str] | None = None, per: int = 4, kind: str | None = None) -> str:
    chosen = _chosen(categories, kind)
    lines = []
    for c in chosen:
        lines.append(f"- {c['label']}: {c['blurb']}")
        for title, change, why in c["templates"][:per]:
            lines.append(f"    * {title} -- {change} (why: {why})")
    return "\n".join(lines)
