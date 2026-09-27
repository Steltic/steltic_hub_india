#!/usr/bin/env python3
"""Post-process a Docling conversion directory for retrieval.

Does NOT re-run Docling and NEVER modifies the original PDF.

What this pass adds (Docling does not):
  * body-only searchable markdown (furniture / running titles / AISI copyright
    stripped). All-layer markdown is kept as a furniture archive.
  * provision vs commentary flag (`part` in {standard, commentary}). This is
    NOT a Docling ContentLayer — it is detected from headers/cover text.
  * printed page labels (roman, arabic, C-qualified) alongside pdf_page.
  * equation IDs recovered from adjacent body text / PDF text, NOT from the
    letter-spaced LaTeX blob. OCR math typos are recorded, never auto-fixed.

Commentary detector is configurable per document family:

  aisi_s400  cover header ``AISI S400-20-C`` / running header
             ``Commentary on North American Standard...``
             (cover PDF p.91, commentary printed p.1 = PDF p.99)
  aisi_s100  cover header ``AISI S100-16-C`` / running header
             ``Commentary on the 2016 Edition...Cold-Formed Steel Specification``
             (cover PDF p.251, commentary printed p.1 = PDF p.263).
             Chapter C is DESIGN FOR STABILITY in BOTH halves — not ASCE C1/C11.
             family_cutoff = max(200, n_pages//3) so p3 "Commentary on the
             Specification" does not pin the cover.
  aisi_s240  cover header ``AISI S240-20-C`` / running header
             ``Commentary on the North American Standard for Cold-Formed Steel
             Structural Framing``. Cover PDF p.128, commentary printed p.1 =
             PDF p.136. Chapter C is INSTALLATION in BOTH halves — not ASCE
             C1/C11 and not the commentary divider. Do NOT fall through to
             S400 (cover p.91) or S100 (cover p.251). family_cutoff = 1
             (S240-20-C is unique; a max(200,...) cutoff would skip p.128).
  asce7      hook only: commentary is chapters C1/C11 after provisions 1–32.
             Do NOT treat AISI S400/S100 chapter C as ASCE-style commentary —
             that chapter exists in BOTH halves.
  aisc       hook only: look for a commentary cover / "COMMENTARY" divider
             (AISC 360 commentary starts ~pdf p.355).
  auto       pick from stem / observed headers.

Usage:
  python <corpus>/scripts/postprocess.py <corpus>/documents/standards/<STEM> --profile is_bis
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from pipeline_fixes import (
    inherit_continued_table_ids,
    orig_cites_other_eq,
    orig_is_phi_omega_only,
    table_id_from_title,
)

LOG = logging.getLogger("postprocess")

COPYRIGHT_LINE = "This document is copyrighted by AISI. Any redistribution is prohibited."

# AISI running titles that must not enter searchable text (header furniture).
RUNNING_TITLE_RES = [
    re.compile(
        r"^North American Standard for Seismic Design of Cold-Formed Steel "
        r"Structural Systems, 2020 Edition\s*\d*\s*$",
        re.I,
    ),
    re.compile(
        r"^Commentary on North American Standard for Seismic Design of "
        r"Cold-Formed Steel Structural Systems, 2020 Edition\s*\d*\s*$",
        re.I,
    ),
    re.compile(r"^AISI\s+S400-20(-C|-E)?\s*$", re.I),
]

# AISI S100-16 (R2020) w/S3-22 running titles. Two-line headers plus
# "{N} Chapter X" / "AISI S100-16…". S400 copyright line is NOT in this PDF.
AISI_S100_RUNNING_TITLE_RES = [
    re.compile(
        r"^The 2016 Edition \(Reaffirmed 2020\) of the North American "
        r"Specification for the Design of Cold-Formed Steel\s*$",
        re.I,
    ),
    re.compile(
        r"^Structural Members With Supplement 3\s*"
        r"(?:(?:[A-Z]-)?\d+(?:-\d+)?|[ivxlcdm]+)?\s*$",
        re.I,
    ),
    re.compile(
        r"^Commentary on the 2016 Edition \(Reaffirmed 2020\) of the "
        r"North American Cold-Formed Steel\s*$",
        re.I,
    ),
    re.compile(
        r"^Specification With Supplement 3\s*"
        r"(?:(?:[A-Z]-)?\d+(?:-\d+)?|[ivxlcdm]+)?\s*$",
        re.I,
    ),
    re.compile(
        r"^(?:[ivxlcdm]+\s+)?AISI\s*S100-16(?:-C)?(?:\s*\(R?2020\))?(?:\s*w/S3-22)?\s*$",
        re.I,
    ),
    re.compile(
        r"^Commentary on the 2016 Edition.*Cold-Formed Steel Specification\s*$",
        re.I,
    ),
]
AISI_S100_TRAILING_LABEL_RE = re.compile(
    r"With\s+Supplement\s+3\s+((?:[A-Z]-)?\d+(?:-\d+)?|[ivxlcdm]+)\s*$",
    re.I,
)
AISI_S100_LEADING_LABEL_RE = re.compile(
    r"^((?:[A-Z]-)?\d+(?:-\d+)?|[ivxlcdm]+)\s+"
    r"(Chapter|Appendix|AISI|Introduction|Disclaimer)\b",
    re.I,
)
AISI_S100_LABEL_TOKEN_RE = re.compile(
    r"^(?:[A-Z]-\d+|\d+-\d+|\d{1,4}|[ivxlcdm]+)$",
    re.I,
)
# S100 copyright page (pdf 3) — not the S400 redistribution line.
AISI_S100_COPYRIGHT_RE = re.compile(
    r"Copyright American Iron and Steel Institute(?: and CSA Group)? 2022",
    re.I,
)

# AISI S240-20 running titles. Same AISI redistribution footer as S400.
# Printed labels: roman front matter, arabic body (pdf 28 = printed 1),
# commentary arabic qualified C-1. NOT 16.1 / 9.1 / S100 1-1.
AISI_S240_RUNNING_TITLE_RES = [
    re.compile(
        r"^North American Standard [Ff]or Cold-Formed Steel Structural "
        r"Framing, 2020 Edition\s*\d*\s*$",
        re.I,
    ),
    re.compile(
        r"^Commentary on the North American Standard for Cold-Formed Steel "
        r"Structural Framing, 2020 Edition\s*\d*\s*$",
        re.I,
    ),
    re.compile(r"^AISI\s+S240-20(-C)?\s*$", re.I),
]
AISI_S240_LEADING_LABEL_RE = re.compile(
    r"^((?:[ivxlcdm]+|\d{1,4}))\s+AISI\s*S240-20(?:-C)?\s*$",
    re.I,
)

# AISI S230-19 running titles / headers.
AISI_S230_RUNNING_TITLE_RES = [
    re.compile(
        r"^Standard for Cold-Formed Steel Framing[—\-]Prescriptive Method "
        r"for One- and Two-Family Dwellings, 2019 Edition\s*\d*\s*$",
        re.I,
    ),
    re.compile(
        r"^Commentary on the Standard for Cold-Formed Steel Framing[—\-]?"
        r"Prescriptive Method for One- and Two-Family Dwellings, 2019 Edition\s*\d*\s*$",
        re.I,
    ),
    re.compile(r"^AISI\s+S230-19(-C)?\s*$", re.I),
]
AISI_S230_LEADING_LABEL_RE = re.compile(
    r"^((?:[ivxlcdm]+|\d{1,4}))\s+AISI\s*S230-19(?:-C)?\s*$",
    re.I,
)

# AISC 360/341 running footers (every content page). Do not treat the
# copyright *page* body (p.4 © AISC 2022) as leakage — only this footer.
# Same-line running footer only. Title pages put the date on the NEXT line
# ("Specification...\n\nAugust 1, 2022"); that is body, not furniture.
AISC_RUNNING_TITLE_RES = [
    re.compile(
        r"Specification for Structural Steel Buildings[ \t]*,?[ \t]*August 1,[ \t]*2022",
        re.I,
    ),
    re.compile(r"^Part 16\.1\b.*\.indd", re.I),
]
AISC_COPYRIGHT_RES = [
    re.compile(r"©\s*AISC\s*2022", re.I),
]
# AISC 358-22 running footers. Printed labels are 9.2-xxx (NOT 16.1-xxx).
# Title/cover pages split the name across lines; that is body, not furniture.
AISC_358_RUNNING_TITLE_RES = [
    re.compile(
        r"Prequalified Connections for Special and Intermediate Steel[ \t]+"
        r"Moment Frames for Seismic Applications[ \t]*,?[ \t]*August 18,[ \t]*2022",
        re.I,
    ),
    re.compile(
        r"^Prequalified Connections for Special and Intermediate Steel\s*$",
        re.I,
    ),
    re.compile(
        r"^Moment Frames for Seismic Applications,?\s*August 18,\s*2022\s*$",
        re.I,
    ),
]
AISC_358_PRINTED_LABEL_RE = re.compile(
    r"9\s*\.\s*2\s*[-–—]\s*([ivxlcdm]+|\d+)\b",
    re.I,
)
# AISC 341-22 running footers. Printed labels are 9.1-xxx (NOT 16.1-xxx / 9.2-xxx).
# Title/cover pages split the name across lines; that is body, not furniture.
AISC_341_RUNNING_TITLE_RES = [
    re.compile(
        r"Seismic Provisions for Structural Steel Buildings[ \t]*,?[ \t]*September 26,[ \t]*2022",
        re.I,
    ),
    re.compile(r"^Seismic Provisions for Structural Steel Buildings\s*$", re.I),
    re.compile(r"^American Institute of Steel Construction\s*$", re.I),
]
AISC_341_PRINTED_LABEL_RE = re.compile(
    r"9\s*\.\s*1\s*[-–—]\s*([ivxlcdm]+|\d+)\b",
    re.I,
)
# ASCE 7 ascelibrary watermark + running titles. Must not enter searchable text.
ASCE_WATERMARK_RES = [
    re.compile(r"Downloaded from ascelibrary\.org by[^\n]*", re.I),
    re.compile(
        r"Copyright ASCE; all rights reserved, including rights for text and data mining[^\n]*",
        re.I,
    ),
    re.compile(r"Copyright ASCE; all rights reserved[^\n]*", re.I),
]
ASCE_RUNNING_TITLE_RES = [
    re.compile(r"^STANDARD\s+ASCE/SEI\s+7-22\s*$", re.I),
    re.compile(r"^ASCE/SEI\s+7-22\s*$", re.I),
    re.compile(
        r"Minimum Design Loads and Associated Criteria for Buildings and Other Structures",
        re.I,
    ),
    re.compile(r"^COMMENTARY TO STANDARD ASCE/SEI 7-22\s*$", re.I),
    # ASCE/SEI 41-23 (anchored — body mentions of the title must not strip)
    re.compile(r"^STANDARD\s+ASCE/SEI\s+41-23\s*$", re.I),
    re.compile(r"^ASCE/SEI\s+41-23\s*$", re.I),
    re.compile(r"^Seismic Evaluation and Retrofit of Existing Buildings\s*$", re.I),
    re.compile(r"^Commentary to Standard ASCE/SEI 41-23\s*$", re.I),
]
ASCE_COPYRIGHT_RES = [
    re.compile(r"Copyright\s+ASCE;\s*all rights reserved", re.I),
    re.compile(r"Downloaded from ascelibrary\.org", re.I),
]
# Printed labels: "16.1-52", "16.1-viii", Docling often emits "16.1 -viii".
# Require a hyphen (16.1-53 / 16.1 -viii). Do NOT match "16.1 Comm" as 16.1-c
# (C is a roman numeral letter). AISC 358 uses 9.2-xxx; AISC 341 uses 9.1-xxx.
AISC_PRINTED_LABEL_RE = re.compile(
    r"16\s*\.\s*1\s*[-–—]\s*([ivxlcdm]+|\d+)\b",
    re.I,
)

EQ_PARENS_RE = re.compile(r"\(\s*Eq\.?\s*([^)]+?)\s*\)", re.I)
# AISC 360: (F2-1) / (C-F2-1) / (A-8-1) / (C-A-1-1) — no "Eq." prefix.
AISC_EQ_PARENS_RE = re.compile(
    r"\(\s*((?:C-)?(?:A-\d+-\d+[a-z]?|[A-Z]\d+[A-Za-z]?(?:\.\d+)*-\d+[a-z]?))\s*\)"
)
# AISI S100 commentary: (C-1-1) / (C-1.1-1) / (C-A3.3.2-1) / (C-2.3.1-1)
# without "Eq." — do not require a chapter letter after C-.
AISI_C_EQ_PARENS_RE = re.compile(
    r"\(\s*(C-(?:[A-Z])?\d+(?:\.\d+)*-\d+[a-z]?)\s*\)"
)
EQ_INNER_RE = re.compile(
    r"^(C-)?([A-Z])?(\d+(?:\.\d+)*[A-Za-z]?(?:\.\d+)*)-(\d+[a-z]?(?:\.SI)?)$",
    re.I,
)
AISC_APPENDIX_EQ_RE = re.compile(
    r"^(C-)?A-(\d+)-(\d+[a-z]?)$",
    re.I,
)
# ASCE/SEI 7: (12.8-3) / (12.4-4a) / (26.10-1.SI) / (C12.8-3) — no "Eq." prefix.
ASCE_EQ_PARENS_RE = re.compile(
    r"\(\s*((?:C-?)?\d+(?:\.\d+)+-\d+[a-z]?(?:\.SI)?)\s*\)"
)
TABLE_ID_RE = re.compile(
    r"Table\s+((?:C-)?[A-Z]?\d[\w.\-]*[0-9A-Za-z])",
    re.I,
)
# AISC headings are "F2. TITLE" (period after id); AISI S400 is "E3.4.2  Title".
SECTION_ID_RE = re.compile(
    r"^((?:C-)?[A-Z]\d+(?:\.\d+)*[a-z]?)(?:\.|\b)(?:\s+|$)(.*)$"
)
CHAPTER_DOT_RE = re.compile(r"^([A-Z])\.\s+(.+)$")
# ASCE/SEI 7: "CHAPTER 12 …" / "CHAPTER C1 GENERAL" / "12.4.3.2 Title" / "C1.1 SCOPE"
ASCE_CHAPTER_RE = re.compile(r"^CHAPTER\s+(C?\d+)\b(?:\s+(.*))?$", re.I)
ASCE_SECTION_RE = re.compile(r"^((?:C)?\d+(?:\.\d+)+[a-z]?)\s+(\S.*)$")
# AISC 358: "5.1. GENERAL" / "5.7. DESIGN PROCEDURE" (period after numeric id).
AISC_358_SECTION_RE = re.compile(r"^(\d+(?:\.\d+)+[a-z]?)\.\s+(\S.*)$")
AISC_NUMBERED_SUBHEAD_RE = re.compile(
    r"^(\d+[a-z]?)\.\s+(\S.*)$"
)
SKIP_HEADING_PREFIXES = (
    "user note",
    "disclaimer",
    "preface",
    "symbols",
    "table of contents",
    "this page is intentionally left blank",
    "aisi standard",
    "aisi committee",
    "lateral design subcommittee",
    "north american standard",
    "commentary on north american",
    "language is added",
    "references in section",
)

# BIS / IS watermark furniture (search markdown only — not commentary dividers).
BIS_WATERMARK_RES = [
    re.compile(
        r"^Free\s+Standard\s+provided\s+by\s+BIS(?:\s+via\s+BSB\s+Edge[^\n]*)?\s*$",
        re.I | re.M,
    ),
    re.compile(
        r"^.*Free\s+Standard\s+provided\s+by\s+BIS.*BSB\s+Edge.*$",
        re.I | re.M,
    ),
    re.compile(
        r"^.*provided\s+by\s+BIS\s+via\s+BSB\s+Edge.*$",
        re.I | re.M,
    ),
    re.compile(r"^.*BSB\s+Edge\s+Private\s+Limited.*$", re.I | re.M),
    re.compile(
        r"^Free\s+Standard\s+provided\s+by\s+BIS\s+via\s+BSB\s+Edge\s+"
        r"Private\s+Limited\s+to\s+.+?\d{1,3}(?:\.\d{1,3}){3}\.?\s*$",
        re.I | re.M,
    ),
]

# BIS clause headings: "SECTION 5 LIMIT STATE DESIGN", "1. SCOPE", "1 SCOPE",
# "3.2 Reduction…", "0. FOREWORD". Numeric with optional trailing period.
BIS_SECTION_RE = re.compile(
    r"^SECTION\s+(\d+)\b(?:\s+(.*))?$",
    re.I,
)
BIS_NUMBERED_DOT_RE = re.compile(
    r"^(\d+(?:\.\d+)*)\.\s+(\S.*)$"
)
BIS_NUMBERED_SPACE_RE = re.compile(
    r"^(\d+(?:\.\d+)*)\s+([A-Z][A-Za-z0-9][A-Za-z0-9 ,/\-]{0,100})$"
)
BIS_CLAUSE_MD_RE = re.compile(
    r"^#{1,3}\s*(?:SECTION\s+(\d+)\b(?:\s+(.*))?|"
    r"(\d+(?:\.\d+)*)\.?\s+(\S.*))$",
    re.I,
)

ROMAN_RE = re.compile(r"^[ivxlcdm]+$", re.I)
ARABIC_RE = re.compile(r"^\d{1,4}$")
TRAILING_NUM_RE = re.compile(r"(\d{1,4})\s*$")

OCR_ISSUE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"t\s*_\s*\{\s*s\s*u\s*d", re.I),
        "possible OCR typo: t_sud (source often t_stud); latex left unchanged",
    ),
    (
        re.compile(r"<_Python_>"),
        "false-positive formula (running title classified as formula)",
    ),
    (
        re.compile(r"E\s+q\s+\.\s+[A-Z0-9C]"),
        "letter-spaced equation ID in LaTeX blob; ID recovered from adjacent text",
    ),
]


# ---------------------------------------------------------------------------
# Commentary detector (configurable hook)
# ---------------------------------------------------------------------------


@dataclass
class CommentaryProfile:
    """How to find the commentary half of a spec.

    Docling has no commentary ContentLayer. Detection is always a post-pass.
    """

    name: str
    cover_header_regexes: list[str] = field(default_factory=list)
    running_header_regexes: list[str] = field(default_factory=list)
    body_heading_regexes: list[str] = field(default_factory=list)
    notes: str = ""

    def compiled(self) -> dict[str, list[re.Pattern[str]]]:
        return {
            "cover": [re.compile(p, re.I) for p in self.cover_header_regexes],
            "running": [re.compile(p, re.I) for p in self.running_header_regexes],
            "body": [re.compile(p, re.I) for p in self.body_heading_regexes],
        }


PROFILES: dict[str, CommentaryProfile] = {
    "aisi_s400": CommentaryProfile(
        name="aisi_s400",
        cover_header_regexes=[r"AISI\s*S400-20-C\b"],
        running_header_regexes=[
            r"Commentary on North American Standard for Seismic Design",
        ],
        body_heading_regexes=[
            r"^COMMENTARY ON NORTH AMERICAN STANDARD",
            r"^Commentary on North American Standard for Seismic Design",
        ],
        notes=(
            "AISI S400-20: commentary cover is PDF p.91 (AISI S400-20-C); "
            "commentary printed page 1 is PDF p.99. User heuristic ~p.101 was close. "
            "Same section numbers exist in both halves (E3.4.2 body p.59 vs "
            "commentary p.136)."
        ),
    ),
    "asce7": CommentaryProfile(
        name="asce7",
        cover_header_regexes=[
            r"COMMENTARY TO STANDARD ASCE/SEI\s*7",
            r"COMMENTARY TO STANDARD ASCE",
        ],
        running_header_regexes=[
            r"COMMENTARY TO STANDARD ASCE",
            r"^CHAPTER\s+C\d+\b",
        ],
        body_heading_regexes=[
            # ASCE 7 commentary chapters are C1, C11, ... after provisions 1–32.
            # Do not use this on AISI S400: S400 chapter C is "Seismic Load
            # Effects" and exists in BOTH the standard and the commentary.
            # Require "CHAPTER C12" / "C1.1" — never a bare Chapter C.
            r"^CHAPTER\s+C\d+\b",
            r"^C\d+\.\d+",
        ],
        notes=(
            "ASCE 7-22: provisions chapters 1–32 then commentary chapters C1/C11 "
            "(cover ~pdf p.542, CHAPTER C1 ~pdf p.544). Do not confuse with "
            "AISI S400 Chapter C."
        ),
    ),
    "aisc": CommentaryProfile(
        name="aisc",
        # Tight patterns: "Commentary" appears in User Notes throughout the
        # provisions. Do not use a bare \bCommentary\b cover match (that
        # would pin the boundary to the TOC / first user note).
        cover_header_regexes=[
            r"COMMENTARY\s+on the Specification for Structural Steel Buildings",
            r"^COMMENTARY$",
        ],
        running_header_regexes=[
            r"\[Comm\.",
            r"^Comm\.\s",
            r"COMMENTARY SYMBOLS",
            r"COMMENTARY GLOSSARY",
        ],
        body_heading_regexes=[
            r"^COMMENTARY$",
            r"^COMMENTARY\s+on the Specification",
            r"^COMMENTARY SYMBOLS",
            r"^COMMENTARY GLOSSARY",
        ],
        notes=(
            "AISC 360/341: commentary divider after the provisions "
            "(AISC 360 cover ~pdf p.355, printed 16.1-287) and reuses the "
            "same section numbers in both halves. Running headers look like "
            "[Comm. A1. / Comm. F2.]."
        ),
    ),
    "aisc_358": CommentaryProfile(
        name="aisc_358",
        # 358 is one chapter per connection type (RBS, BUEEP, WUF-W, …).
        # Do NOT use A360's "COMMENTARY on the Specification for Structural
        # Steel Buildings" — 358's cover is "COMMENTARY on Prequalified
        # Connections…". Bare \bCommentary\b would pin to TOC / user notes.
        cover_header_regexes=[
            r"COMMENTARY\s+on Prequalified Connections",
            r"^COMMENTARY$",
        ],
        running_header_regexes=[
            r"\[Comm\.",
            r"^Comm\.\s",
            r"Comm\.\s+\d",
        ],
        body_heading_regexes=[
            r"^COMMENTARY$",
            r"^COMMENTARY\s+on Prequalified Connections",
            r"^INTRODUCTION$",
        ],
        notes=(
            "AISC 358-22: commentary cover ~pdf p.211 (printed 9.2-181) "
            "'COMMENTARY on Prequalified Connections for Special and "
            "Intermediate Steel Moment Frames for Seismic Applications'. "
            "Chapter 1 commentary body ~pdf p.212. Same numeric chapter "
            "numbers in both halves (Ch. 5 RBS provision pdf ~45, "
            "commentary pdf ~229). Printed labels are 9.2-xxx, not 16.1-xxx."
        ),
    ),
    "aisc_341": CommentaryProfile(
        name="aisc_341",
        # 341 is Seismic Provisions. Do NOT use A360's "COMMENTARY on the
        # Specification for Structural Steel Buildings" — 341's cover is
        # "COMMENTARY on the Seismic Provisions for Structural Steel Buildings".
        # Bare \bCommentary\b would pin to TOC / user notes.
        cover_header_regexes=[
            r"COMMENTARY\s+on the Seismic Provisions",
            r"^COMMENTARY$",
        ],
        running_header_regexes=[
            r"\[Comm\.",
            r"^Comm\.\s",
            r"COMMENTARY SYMBOLS",
            r"COMMENTARY GLOSSARY",
        ],
        body_heading_regexes=[
            r"^COMMENTARY$",
            r"^COMMENTARY\s+on the Seismic Provisions",
            r"^COMMENTARY SYMBOLS",
            r"^INTRODUCTION$",
        ],
        notes=(
            "AISC 341-22: commentary cover ~pdf p.235 (printed 9.1-181) "
            "'COMMENTARY on the Seismic Provisions for Structural Steel "
            "Buildings'. Same letter-number section ids in both halves "
            "(E3.4a provision pdf ~99, commentary pdf ~315). Printed labels "
            "are 9.1-xxx, not 16.1-xxx (A360) or 9.2-xxx (A358)."
        ),
    ),
    "aisc_342": CommentaryProfile(
        name="aisc_342",
        # AISC 342-22 Seismic Provisions for Evaluation and Retrofit of Existing
        # Structural Steel Buildings. Commentary cover ~pdf p.167.
        cover_header_regexes=[
            r"COMMENTARY\s+on the Seismic Provisions for\s*Evaluation",
            r"COMMENTARY\s+on the Seismic Provisions",
            r"^COMMENTARY$",
        ],
        running_header_regexes=[
            r"\[Comm\.",
            r"^Comm\.\s",
            r"COMMENTARY SYMBOLS",
            r"COMMENTARY GLOSSARY",
        ],
        body_heading_regexes=[
            r"^COMMENTARY$",
            r"^COMMENTARY\s+on the Seismic Provisions",
            r"^COMMENTARY SYMBOLS",
            r"^INTRODUCTION$",
        ],
        notes=(
            "AISC 342-22: commentary cover ~pdf p.167 "
            "'COMMENTARY on the Seismic Provisions for Evaluation and Retrofit "
            "of Existing Structural Steel Buildings'. Same letter-number section "
            "ids in both halves. Printed page labels are plain arabic (not "
            "9.1-xxx / 16.1-xxx / 9.2-xxx)."
        ),
    ),
    "asce_41": CommentaryProfile(
        name="asce_41",
        # ASCE/SEI 41-23 Seismic Evaluation and Retrofit of Existing Buildings.
        # Provisions Ch 1–18 (+ appendices); commentary is Chapters C1–C18
        # starting ~pdf p.400 ("CHAPTER C1 GENERAL REQUIREMENTS").
        cover_header_regexes=[
            r"^CHAPTER\s+C1\b",
            r"CHAPTER\s+C1\s+GENERAL REQUIREMENTS",
            r"^C1\.1\s+SCOPE",
        ],
        running_header_regexes=[
            r"^C\d+\.\d+",
            r"CHAPTER\s+C\d+",
            r"Commentary",
        ],
        body_heading_regexes=[
            r"^CHAPTER\s+C1\b",
            r"^C1\.1\s+SCOPE",
        ],
        notes=(
            "ASCE/SEI 41-23: commentary Chapters C1–C18 begin ~pdf p.400 "
            "('CHAPTER C1 GENERAL REQUIREMENTS'). Standard provisions are "
            "Chapters 1–18; commentary section ids are C-prefixed (C1.1, …). "
            "Appendix A material appears before commentary in the PDF."
        ),
    ),
    "aisi_s100": CommentaryProfile(


        name="aisi_s100",
        cover_header_regexes=[r"AISI\s*S100-16-C"],
        running_header_regexes=[
            r"Commentary on the 2016 Edition.*Cold-Formed Steel Specification",
        ],
        body_heading_regexes=[
            r"^COMMENTARY ON THE NORTH AMERICAN SPECIFICATION",
        ],
        notes=(
            "AISI S100-16 (R2020) w/S3-22: commentary cover is PDF p.251 "
            "(AISI S100-16-C w/S3-22); commentary printed page 1 is PDF p.263. "
            "Chapter C is DESIGN FOR STABILITY in BOTH the specification and "
            "the commentary — do not treat it as ASCE C1/C11 commentary. "
            "family_cutoff = max(200, n_pages//3) so p3 'Commentary on the "
            "Specification' (disclaimer prose) does not pin the cover. "
            "S1/S2/S3 are incorporated in-place (5th Printing Sept 2022); "
            "no revision bars, no parallel base-vs-replacement text."
        ),
    ),
    "aisi_s230": CommentaryProfile(
        name="aisi_s230",
        cover_header_regexes=[r"AISI\s*S230-19-C\b"],
        running_header_regexes=[
            r"Commentary on the Standard for Cold-Formed Steel Framing.{0,40}Prescriptive Method",
            r"AISI\s*S230-19-C\b",
        ],
        body_heading_regexes=[
            r"^COMMENTARY ON THE STANDARD FOR COLD-FORMED",
            r"^COMMENTARY\s*$",
            r"^Commentary on the Standard for Cold-Formed Steel Framing",
        ],
        notes=(
            "AISI S230-19 Prescriptive Method for One- and Two-Family Dwellings: "
            "commentary cover is PDF ~p.227 (AISI S230-19-C). Chapter letters may "
            "appear in both standard and commentary — not ASCE C1/C11. "
            "family_cutoff = 1 like S240/S400: cover token S230-19-C is unique."
        ),
    ),
    "is_bis": CommentaryProfile(
        name="is_bis",
        # Cover/running regexes intentionally EMPTY for commentary detection.
        # BIS codes almost never ship a separate commentary volume; matching
        # "Indian Standard" / "IS NNN" / BIS watermarks on page 1 with
        # family_cutoff=1 previously tagged EVERY page as commentary.
        # Watermark stripping uses BIS_WATERMARK_RES (not these lists).
        cover_header_regexes=[],
        running_header_regexes=[],
        body_heading_regexes=[],
        notes=(
            "BIS / Indian Standard: NO separate commentary volume. "
            "detect_commentary early-returns cover=None. Strip Free Standard/"
            "BSB Edge watermarks via strip_bis_search_furniture. "
            "Jurisdiction india/is_bis."
        ),
    ),
    "aisi_s240": CommentaryProfile(
        name="aisi_s240",
        cover_header_regexes=[r"AISI\s*S240-20-C\b"],
        running_header_regexes=[
            r"Commentary on the North American Standard for Cold-Formed Steel Structural Framing",
        ],
        body_heading_regexes=[
            r"^COMMENTARY ON THE NORTH AMERICAN STANDARD",
            r"^Commentary on the North American Standard for Cold-Formed Steel Structural Framing",
        ],
        notes=(
            "AISI S240-20: commentary cover is PDF p.128 (AISI S240-20-C); "
            "commentary printed page 1 is PDF p.136. Chapter C is INSTALLATION "
            "in BOTH the standard (pdf 94) and the commentary (pdf 174) — not "
            "ASCE C1/C11 and not the commentary divider. Do NOT fall through "
            "to S400 (cover p.91) or S100 (cover p.251). family_cutoff = 1 "
            "like S400: cover token S240-20-C is unique and first appears at "
            "p.128; a max(200,...) cutoff would skip it. Printed labels are "
            "roman front matter then arabic (pdf 28 = printed 1), not 16.1/9.1 "
            "or S100 1-1/A-3."
        ),
    ),
}


def profile_for_stem(stem: str, requested: str) -> CommentaryProfile:
    s = (stem or "").lower()
    if requested == "is_bis" or s.startswith("is_"):
        return PROFILES["is_bis"]
    # S100 / S240 must NOT fall through to S400 (default) or AISC.
    if requested == "aisi_s100" or "s100" in s:
        return PROFILES["aisi_s100"]
    if requested == "aisi_s230" or "s230" in s:
        return PROFILES["aisi_s230"]
    if requested == "aisi_s240" or "s240" in s:
        return PROFILES["aisi_s240"]
    # AISC 358 / 341 must not use the A360 "Specification" commentary cover.
    # convert_pdf.py still passes --commentary-profile aisc; remap here.
    if requested == "aisc_358" or (
        "358" in s and requested in ("auto", "aisc", "", None)
    ):
        return PROFILES["aisc_358"]
    if requested == "aisc_342" or (
        "342" in s and requested in ("auto", "aisc", "", None)
    ):
        return PROFILES["aisc_342"]
    if requested == "asce_41" or (
        ("41" in s and "asce" in s) and requested in ("auto", "asce7", "asce", "", None)
    ):
        return PROFILES["asce_41"]
    if requested == "aisc_341" or (
        "341" in s and requested in ("auto", "aisc", "", None)
    ):
        return PROFILES["aisc_341"]
    if requested and requested != "auto":
        if requested not in PROFILES:
            raise ValueError(
                f"Unknown commentary profile {requested!r}; "
                f"known: {sorted(PROFILES)}"
            )
        return PROFILES[requested]
    if "s400" in s:
        return PROFILES["aisi_s400"]
    if "asce" in s and "41" in s:
        return PROFILES["asce_41"]
    if "asce" in s:
        return PROFILES["asce7"]
    if "a360" in s or "aisc" in s:
        return PROFILES["aisc"]
    return PROFILES["aisi_s400"]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def page_nos(item: dict[str, Any]) -> list[int]:
    out: list[int] = []
    for p in item.get("prov") or []:
        n = p.get("page_no")
        if n is not None:
            out.append(int(n))
    return out


def first_page(item: dict[str, Any]) -> Optional[int]:
    ps = page_nos(item)
    return ps[0] if ps else None


def first_bbox(item: dict[str, Any]) -> dict[str, float]:
    for p in item.get("prov") or []:
        b = p.get("bbox") or {}
        if b:
            return b
    return {}


def y_center(bbox: dict[str, Any]) -> float:
    t = float(bbox.get("t") or 0)
    b = float(bbox.get("b") or 0)
    return (t + b) / 2.0


def collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def collapse_spaced_tokens(text: str) -> str:
    """Join letter-spaced OCR ('E q . A 3 . 2 . 3 - 1') into Eq.A3.2.3-1."""
    return re.sub(r"\s+", "", text or "")


def is_page_number_token(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if ARABIC_RE.fullmatch(t):
        return True
    if ROMAN_RE.fullmatch(t) and t.lower() not in {"c"}:  # lone "C" is not a page
        return True
    return False


def is_aisc_358_doc(stem: str) -> bool:
    s = re.sub(r"[^a-z0-9]+", "", (stem or "").lower())
    return "358" in s and ("aisc" in s or s.startswith("a358") or "a358" in s)


def is_aisc_341_doc(stem: str) -> bool:
    s = re.sub(r"[^a-z0-9]+", "", (stem or "").lower())
    return "341" in s and ("aisc" in s or s.startswith("a341") or "a341" in s)


def is_aisc_342_doc(stem: str) -> bool:
    s = re.sub(r"[^a-z0-9]+", "", (stem or "").lower())
    return "342" in s and ("aisc" in s or s.startswith("a342") or "a342" in s)


def is_aisc_doc(stem: str) -> bool:
    s = (stem or "").lower()
    return "a360" in s or "a341" in s or "a342" in s or "a358" in s or s.startswith("aisc")


def is_asce7_doc(stem: str) -> bool:
    s = re.sub(r"[^a-z0-9]+", "", (stem or "").lower())
    return s.startswith("asce7") or "asce7" in s


def is_asce_41_doc(stem: str) -> bool:
    s = re.sub(r"[^a-z0-9]+", "", (stem or "").lower())
    return "asce" in s and "41" in s and "asce7" not in s


def is_aisi_s100_doc(stem: str) -> bool:
    s = re.sub(r"[^a-z0-9]+", "", (stem or "").lower())
    return "s100" in s


def is_aisi_s240_doc(stem: str) -> bool:
    s = re.sub(r"[^a-z0-9]+", "", (stem or "").lower())
    return "s240" in s


def is_aisi_s230_doc(stem: str) -> bool:
    s = re.sub(r"[^a-z0-9]+", "", (stem or "").lower())
    return "s230" in s


def is_bis_doc(stem: str) -> bool:
    s = (stem or "").lower()
    return s.startswith("is_") or s.startswith("is ") or "is_bis" in s


def strip_bis_search_furniture(text: str) -> str:
    """Remove BIS / BSB Edge free-standard watermark lines from searchable body."""
    out = text or ""
    for rx in BIS_WATERMARK_RES:
        out = rx.sub("", out)
    # Multi-line watermark often wraps after "to Name -"
    cleaned: list[str] = []
    skip_continuation = False
    for ln in out.splitlines():
        t = ln.strip()
        if re.search(r"Free\s+Standard\s+provided\s+by\s+BIS", t, re.I):
            skip_continuation = True
            continue
        if skip_continuation:
            if re.search(r"BSB\s+Edge|\d{1,3}(?:\.\d{1,3}){3}", t, re.I):
                skip_continuation = False
                continue
            if len(t) < 80 and not re.match(r"^(IS\s*\d+|SECTION\s+\d+|\d+\.\d+)", t, re.I):
                skip_continuation = False
                continue
            skip_continuation = False
        if re.fullmatch(r"IS\s*\d+\s*:\s*\d+(?:\s*\([^)]*\))?\s*", t, re.I):
            # Running header "IS 800 : 2007" alone — drop from search body
            continue
        # Wrapped watermark residue: email(ip) or bare IPv4
        if re.fullmatch(
            r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
            r"(?:\s*\(\d{1,3}(?:\.\d{1,3}){3}\))?\.?",
            t,
        ):
            continue
        if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}\.?", t):
            continue
        if re.fullmatch(
            r"[A-Za-z0-9_\-]+\([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\)\s*"
            r"\d{1,3}(?:\.\d{1,3}){3}\.?",
            t,
        ):
            continue
        cleaned.append(ln)
    # Collapse excess blank lines
    text2 = "\n".join(cleaned)
    text2 = re.sub(r"\n{3,}", "\n\n", text2)
    return text2


def looks_like_running_title(text: str) -> bool:
    t = collapse_ws(text)
    if not t:
        return False
    for rx in (
        RUNNING_TITLE_RES
        + AISC_RUNNING_TITLE_RES
        + AISC_358_RUNNING_TITLE_RES
        + AISC_341_RUNNING_TITLE_RES
        + ASCE_RUNNING_TITLE_RES
        + AISI_S100_RUNNING_TITLE_RES
        + AISI_S240_RUNNING_TITLE_RES
        + AISI_S230_RUNNING_TITLE_RES
    ):
        if rx.search(t) or rx.match(t):
            return True
    if t.lower().startswith("this document is copyrighted"):
        return True
    if t.lower().startswith("downloaded from ascelibrary"):
        return True
    if t.lower().startswith("copyright asce"):
        return True
    return False


def strip_aisc_358_search_furniture(text: str) -> str:
    """Remove 358 running titles / 9.2-xxx labels from searchable body."""
    out = text or ""
    for rx in AISC_358_RUNNING_TITLE_RES:
        if rx.pattern.startswith("^"):
            continue
        out = rx.sub("", out)
    lines: list[str] = []
    skip_next = False
    for ln in out.splitlines():
        t = ln.strip()
        if re.fullmatch(
            r"Prequalified Connections for Special and Intermediate Steel", t, re.I
        ):
            skip_next = True
            continue
        if skip_next and re.match(r"Moment Frames for Seismic Applications", t, re.I):
            skip_next = False
            continue
        skip_next = False
        if re.fullmatch(
            r"Moment Frames for Seismic Applications,?\s*August 18,\s*2022", t, re.I
        ):
            continue
        if re.fullmatch(r"9\.2\s*[-–—]\s*[ivxlcdm0-9]+", t, re.I):
            continue
        if re.match(r"^\[?Sect\.\s+", t, re.I) and len(t) < 48:
            continue
        if re.match(r"^Comm\.\s+\d", t, re.I) and len(t) < 56:
            continue
        if re.match(r"^\[Comm\.", t, re.I):
            continue
        lines.append(ln)
    return "\n".join(lines)


def strip_aisc_341_search_furniture(text: str) -> str:
    """Remove 341 running titles / 9.1-xxx labels from searchable body."""
    out = text or ""
    for rx in AISC_341_RUNNING_TITLE_RES:
        if rx.pattern.startswith("^"):
            continue
        out = rx.sub("", out)
    lines: list[str] = []
    skip_next = False
    for ln in out.splitlines():
        t = ln.strip()
        if re.fullmatch(r"Seismic Provisions for Structural Steel Buildings", t, re.I):
            skip_next = True
            continue
        if skip_next and re.match(r"September 26,\s*2022", t, re.I):
            skip_next = False
            continue
        skip_next = False
        if re.fullmatch(
            r"Seismic Provisions for Structural Steel Buildings,\s*September 26,\s*2022",
            t,
            re.I,
        ):
            continue
        if re.fullmatch(r"American Institute of Steel Construction", t, re.I):
            continue
        if re.fullmatch(r"9\.1\s*[-–—]\s*[ivxlcdm0-9]+", t, re.I):
            continue
        if re.match(r"^\[?Sect\.\s+", t, re.I) and len(t) < 48:
            continue
        if re.match(r"^Comm\.\s+[A-Z]", t, re.I) and len(t) < 56:
            continue
        if re.match(r"^\[Comm\.", t, re.I):
            continue
        lines.append(ln)
    return "\n".join(lines)


def strip_asce_search_furniture(text: str) -> str:
    """Remove ascelibrary watermarks / running titles from searchable body."""
    out = text or ""
    for rx in ASCE_WATERMARK_RES:
        out = rx.sub("", out)
    lines = []
    for ln in out.splitlines():
        t = ln.strip()
        if t and any(rx.fullmatch(t) for rx in ASCE_RUNNING_TITLE_RES if rx.pattern.startswith("^")):
            continue
        if t.lower().startswith("downloaded from ascelibrary"):
            continue
        if t.lower().startswith("copyright asce"):
            continue
        lines.append(ln)
    return "\n".join(lines)


def strip_aisi_s100_search_furniture(text: str) -> str:
    """Remove S100 two-line running titles / {N} Chapter X / AISI S100-16…"""
    out = text or ""
    lines: list[str] = []
    skip_next = False
    for ln in out.splitlines():
        t = ln.strip()
        if skip_next:
            skip_next = False
            if re.match(r"^Structural Members With Supplement 3\b", t, re.I):
                continue
            if re.match(r"^Specification With Supplement 3\b", t, re.I):
                continue
        if re.fullmatch(
            r"The 2016 Edition \(Reaffirmed 2020\) of the North American "
            r"Specification for the Design of Cold-Formed Steel",
            t,
            re.I,
        ):
            skip_next = True
            continue
        if re.fullmatch(
            r"Commentary on the 2016 Edition \(Reaffirmed 2020\) of the "
            r"North American Cold-Formed Steel",
            t,
            re.I,
        ):
            skip_next = True
            continue
        if re.match(r"^Structural Members With Supplement 3\b", t, re.I):
            continue
        if re.match(r"^Specification With Supplement 3\b", t, re.I):
            continue
        if re.fullmatch(
            r"(?:[ivxlcdm]+\s+)?AISI\s*S100-16(?:-C)?(?:\s*\(R?2020\))?(?:\s*w/S3-22)?",
            t,
            re.I,
        ):
            continue
        if AISI_S100_LEADING_LABEL_RE.match(t) and len(t) < 120:
            continue
        if AISI_S100_LABEL_TOKEN_RE.fullmatch(t) and t.lower() not in {"c"}:
            continue
        lines.append(ln)
    return "\n".join(lines)


def strip_aisi_s240_search_furniture(text: str) -> str:
    """Remove S240 running titles / AISI S240-20[-C] / copyright footer."""
    lines: list[str] = []
    for ln in (text or "").splitlines():
        t = ln.strip()
        if t.lower().startswith("this document is copyrighted"):
            continue
        if any(rx.match(t) for rx in AISI_S240_RUNNING_TITLE_RES):
            continue
        if AISI_S240_LEADING_LABEL_RE.fullmatch(t):
            continue
        if re.fullmatch(r"AISI\s*S240-20(?:-C)?", t, re.I):
            continue
        lines.append(ln)
    return "\n".join(lines)



def strip_aisi_s230_search_furniture(text: str) -> str:
    """Remove S230 running titles / AISI S230-19[-C] / copyright footer."""
    lines: list[str] = []
    for ln in (text or "").splitlines():
        t = ln.strip()
        if t.lower().startswith("this document is copyrighted"):
            continue
        if any(rx.match(t) for rx in AISI_S230_RUNNING_TITLE_RES):
            continue
        if AISI_S230_LEADING_LABEL_RE.fullmatch(t):
            continue
        if re.fullmatch(r"AISI\s*S230-19(?:-C)?", t, re.I):
            continue
        lines.append(ln)
    return "\n".join(lines)


def format_eq_display(eq_id: Optional[str]) -> Optional[str]:
    """AISI S400: (Eq. A3.2.3-1); AISC 360: (F2-1); ASCE 7: (12.8-3).

    AISI S100 commentary ids (C-1-1 / C-1.1-1 / C-A3.3.2-1) have no Eq. prefix.
    """
    if not eq_id:
        return None
    # S100 commentary: (C-1-1) / (C-1.1-1) — digit after C-, no Eq.
    # Do not steal S400 (Eq. C-E3.4.1-3) which has a chapter letter after C-.
    if re.match(r"^C-\d", eq_id):
        return f"({eq_id})"
    # AISI S400: chapter letter + dotted section (E3.3.1-1 / C-E3.4.1-1).
    if re.match(r"^(C-)?[A-Z]\d+\.\d+", eq_id):
        return f"(Eq. {eq_id})"
    return f"({eq_id})"



def _hint_numeric_matches_eq(section_hint: Optional[str], rest: str) -> bool:
    """True when section_hint's numeric tail is the same family as eq rest.

    AISI dropped-letter: hint E1.3.1.1 matches rest 1.3.1.1.
    AISC 358: hint W2.4 must NOT match rest 15.6 (that produced W15.6-1).
    """
    if not section_hint or not rest:
        return False
    hm = re.match(
        r"^(C-)?([A-Z])(\d+(?:\.\d+)*[A-Za-z]?(?:\.\d+)*)",
        section_hint,
    )
    if not hm:
        return False
    hint_rest = hm.group(3)
    if rest == hint_rest:
        return True
    # Same dotted family (E1.3.1.1 vs 1.3.1.1) only. Do NOT treat chapter
    # E2 as matching appendix (Eq. 2.3.3.1-5) via rest.startswith("2.").
    if "." not in hint_rest:
        return False
    if rest.startswith(hint_rest + ".") or hint_rest.startswith(rest + "."):
        return True
    return False


def normalize_eq_inner(raw: str, section_hint: Optional[str] = None) -> Optional[str]:
    """Turn the inside of '(Eq. ...)' / '(F2-1)' into a canonical id.

    AISI: E1.3.3-1 / C-E3.4.1-1
    AISC: F2-1 / C-F2-1 / A-8-1 / C-A-1-1
    """
    s = collapse_spaced_tokens(raw)
    s = s.replace("–", "-").replace("—", "-")
    s = re.sub(r"^\.?Eq\.?", "", s, flags=re.I)
    s = re.sub(r"^Equation", "", s, flags=re.I)
    s = s.strip("()[] .")
    if not s:
        return None
    # Appendix AISC: (A-8-1) / (C-A-1-1) — must run before chapter-letter parse
    # so 'A-8-1' is not rejected as letter A + non-digit rest.
    m_app = AISC_APPENDIX_EQ_RE.match(s)
    if m_app:
        c_prefix, app_n, num = m_app.group(1), m_app.group(2), m_app.group(3)
        if num[-1:].isalpha():
            num = num[:-1] + num[-1].lower()
        return f"{c_prefix or ''}A-{app_n}-{num}"
    m = re.match(
        r"^(C-)?([A-Z])?(\d+(?:\.\d+)*[A-Za-z]?(?:\.\d+)*)-(\d+[a-z]?(?:\.SI)?)$",
        s,
        re.I,
    )
    if not m:
        # PDF sometimes typesets '(Eq. 1.3.1.1-1)' dropping the chapter letter.
        # Only fill the letter when the hint's numeric tail matches the eq
        # (AISI E1.3.1.1 + 1.3.1.1-1). Never turn (15.6-1) into (W15.6-1)
        # because the page's live section_hint happens to start with W.
        m2 = re.match(r"^(\d+(?:\.\d+)+)-(\d+[a-z]?)$", s, re.I)
        if m2 and section_hint and _hint_numeric_matches_eq(section_hint, m2.group(1)):
            letter = re.match(r"^(C-)?([A-Z])", section_hint)
            if letter:
                prefix = (letter.group(1) or "") + letter.group(2)
                s = f"{prefix}{m2.group(1)}-{m2.group(2)}"
                m = re.match(
                    r"^(C-)?([A-Z])?(\d+(?:\.\d+)*[A-Za-z]?(?:\.\d+)*)-(\d+[a-z]?(?:\.SI)?)$",
                    s,
                    re.I,
                )
        if not m:
            return None
    c_prefix, letter, rest, num = m.group(1), m.group(2), m.group(3), m.group(4)
    letter = (letter or "").upper()
    rest = rest
    num = num.lower() if num[-1:].isalpha() else num
    # keep a/b suffix lowercase
    if num[-1:].isalpha():
        num = num[:-1] + num[-1].lower()
    if not letter and section_hint and _hint_numeric_matches_eq(section_hint, rest):
        hm = re.match(r"^(C-)?([A-Z])", section_hint)
        if hm:
            letter = hm.group(2)
            if hm.group(1) and not c_prefix:
                c_prefix = hm.group(1)
    if not letter:
        # ASCE/SEI 7 / AISC 358: (12.8-3) / (15.6-1) — numeric chapter, no letter.
        return f"{c_prefix or ''}{rest}-{num}"
    return f"{c_prefix or ''}{letter}{rest}-{num}"


def find_eq_ids_in_text(text: str, section_hint: Optional[str] = None) -> list[tuple[str, str]]:
    """Return list of (canonical_id, verbatim_match) from text."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    if not text:
        return out
    for m in EQ_PARENS_RE.finditer(text):
        canon = normalize_eq_inner(m.group(1), section_hint)
        if canon and canon not in seen:
            seen.add(canon)
            out.append((canon, m.group(0)))
    # AISC 360 parenthesized ids without "Eq." — (F2-1), (C-F2-1), (A-8-1)
    for m in AISC_EQ_PARENS_RE.finditer(text):
        canon = normalize_eq_inner(m.group(1), section_hint)
        if canon and canon not in seen:
            seen.add(canon)
            out.append((canon, m.group(0)))
    # ASCE/SEI 7 — (12.8-3), (12.4-4a), (26.10-1.SI)
    for m in ASCE_EQ_PARENS_RE.finditer(text):
        canon = normalize_eq_inner(m.group(1), section_hint)
        if canon and canon not in seen:
            seen.add(canon)
            out.append((canon, m.group(0)))
    # AISI S100 commentary — (C-1-1), (C-1.1-1), (C-A3.3.2-1) without Eq.
    # Do not prepend unrelated section_hint letters (normalize already
    # requires the numeric tail to share a dotted family).
    for m in AISI_C_EQ_PARENS_RE.finditer(text):
        canon = normalize_eq_inner(m.group(1), section_hint)
        if canon and canon not in seen:
            seen.add(canon)
            out.append((canon, m.group(0)))
    spaced = collapse_spaced_tokens(text)
    if spaced:
        for m in re.finditer(
            r"Eq\.?((?:C-)?[A-Z]?\d[\w.]*-\d+[a-z]?)",
            spaced,
            re.I,
        ):
            canon = normalize_eq_inner(m.group(1), section_hint)
            if canon and canon not in seen:
                seen.add(canon)
                out.append((canon, m.group(0)))
        for m in AISC_EQ_PARENS_RE.finditer(spaced):
            canon = normalize_eq_inner(m.group(1), section_hint)
            if canon and canon not in seen:
                seen.add(canon)
                out.append((canon, m.group(0)))
        for m in ASCE_EQ_PARENS_RE.finditer(spaced):
            canon = normalize_eq_inner(m.group(1), section_hint)
            if canon and canon not in seen:
                seen.add(canon)
                out.append((canon, m.group(0)))
        for m in AISI_C_EQ_PARENS_RE.finditer(spaced):
            canon = normalize_eq_inner(m.group(1), section_hint)
            if canon and canon not in seen:
                seen.add(canon)
                out.append((canon, m.group(0)))
    return out


def parse_section_heading(text: str) -> Optional[tuple[str, str]]:
    t = collapse_ws(text)
    if not t:
        return None
    low = t.lower().rstrip(":")
    if any(low.startswith(p) for p in SKIP_HEADING_PREFIXES):
        return None
    if low.startswith("table "):
        return None
    # Strip leading markdown heading marks from pages_search lines.
    t_md = re.sub(r"^#{1,6}\s*", "", t).strip()
    m = CHAPTER_DOT_RE.match(t_md)
    if m:
        return m.group(1), m.group(2).strip()
    m = SECTION_ID_RE.match(t_md)
    if m:
        return m.group(1), (m.group(2) or "").strip()
    m = AISC_358_SECTION_RE.match(t_md)
    if m:
        return m.group(1), (m.group(2) or "").strip()
    m = ASCE_CHAPTER_RE.match(t_md)
    if m:
        return m.group(1), (m.group(2) or "").strip()
    m = ASCE_SECTION_RE.match(t_md)
    if m:
        return m.group(1), (m.group(2) or "").strip()
    # BIS / IS numeric clauses (after ASCE so dotted "12.4.3 Title" still wins)
    m = BIS_SECTION_RE.match(t_md)
    if m:
        return m.group(1), (m.group(2) or "").strip()
    m = BIS_NUMBERED_DOT_RE.match(t_md)
    if m:
        return m.group(1), m.group(2).strip()
    m = BIS_NUMBERED_SPACE_RE.match(t_md)
    if m:
        # Require a title-like second token (avoid "1 800" / "5 65 S0")
        title = m.group(2).strip()
        if re.search(r"[A-Za-z]{2,}", title):
            return m.group(1), title
    return None


def parent_section_id(sid: str) -> Optional[str]:
    if "." in sid:
        return sid.rsplit(".", 1)[0]
    m = re.match(r"^((?:C-)?[A-Z])\d+", sid)
    if m and m.group(0) != m.group(1):
        # E3 -> E ; C-E3 -> C-E  (commentary eq ids aren't section ids)
        letter = m.group(1)
        return letter.rstrip("-") if len(letter) == 1 or letter.endswith("-") else letter
    return None


def latex_issues(latex: str, orig: str) -> list[str]:
    blob = f"{latex}\n{orig}"
    issues = []
    for rx, msg in OCR_ISSUE_PATTERNS:
        if rx.search(blob):
            issues.append(msg)
    return issues


# ---------------------------------------------------------------------------
# Load conversion
# ---------------------------------------------------------------------------


@dataclass
class TextItem:
    chunk: str
    self_ref: str
    label: str
    layer: str
    text: str
    orig: str
    pages: list[int]
    bbox: dict[str, Any]


@dataclass
class LoadedDoc:
    doc_dir: Path
    stem: str
    source_pdf: Optional[Path]
    meta: dict[str, Any]
    chunk_paths: list[Path]
    texts: list[TextItem]
    tables_raw: list[dict[str, Any]]
    formulas: list[TextItem]


def load_convert_meta(doc_dir: Path) -> dict[str, Any]:
    p = doc_dir / "convert_meta.json"
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def load_chunks(doc_dir: Path) -> LoadedDoc:
    meta = load_convert_meta(doc_dir)
    stem = meta.get("stem") or doc_dir.name
    src = meta.get("source_pdf")
    source_pdf = Path(src) if src else None
    chunks_dir = doc_dir / "structured" / "chunks"
    if not chunks_dir.is_dir():
        raise FileNotFoundError(f"No structured/chunks in {doc_dir}")
    chunk_paths = sorted(chunks_dir.glob("*.json"))
    texts: list[TextItem] = []
    formulas: list[TextItem] = []
    tables_raw: list[dict[str, Any]] = []
    for cp in chunk_paths:
        data = json.loads(cp.read_text(encoding="utf-8"))
        for t in data.get("texts") or []:
            item = TextItem(
                chunk=cp.name,
                self_ref=t.get("self_ref") or "",
                label=t.get("label") or "",
                layer=t.get("content_layer") or "",
                text=t.get("text") or "",
                orig=t.get("orig") or "",
                pages=page_nos(t),
                bbox=first_bbox(t),
            )
            texts.append(item)
            if item.label == "formula":
                formulas.append(item)
        for tb in data.get("tables") or []:
            rec = dict(tb)
            rec["_chunk"] = cp.name
            rec["_pages"] = page_nos(tb)
            tables_raw.append(rec)
    return LoadedDoc(
        doc_dir=doc_dir,
        stem=stem,
        source_pdf=source_pdf,
        meta=meta,
        chunk_paths=chunk_paths,
        texts=texts,
        tables_raw=tables_raw,
        formulas=formulas,
    )


# ---------------------------------------------------------------------------
# Printed labels + commentary
# ---------------------------------------------------------------------------


def extract_printed_label(header_texts: list[str]) -> Optional[str]:
    """Pick a printed page label out of page_header strings."""
    # AISI S100: trailing "With Supplement 3 1-1|A-3|B-5|roman|arabic"
    # and leading "{label} Chapter|Appendix|AISI|Introduction|Disclaimer".
    # Do NOT collapse "1-1" to "1" via a trailing-digits grab.
    for raw in header_texts:
        t = collapse_ws(raw)
        m = AISI_S100_TRAILING_LABEL_RE.search(t)
        if m:
            token = m.group(1)
            if ROMAN_RE.fullmatch(token) and not re.search(r"\d", token):
                token = token.lower()
            return token
    for raw in header_texts:
        t = collapse_ws(raw)
        m = AISI_S100_LEADING_LABEL_RE.match(t)
        if m:
            token = m.group(1)
            if ROMAN_RE.fullmatch(token) and not re.search(r"\d", token):
                token = token.lower()
            if token.lower() in {"c"}:
                continue
            return token
    # AISI S240 even-page header: "78 AISI S240-20" / "ii AISI S240-20-C"
    for raw in header_texts:
        t = collapse_ws(raw)
        m = AISI_S240_LEADING_LABEL_RE.match(t)
        if m:
            token = m.group(1)
            if ROMAN_RE.fullmatch(token) and not re.search(r"\d", token):
                token = token.lower()
            return token
    # Docling often splits "With Supplement 3" and "1-1"/"A-3" onto two items.
    # Prefer the standalone compound token over leftover "Supplement 3" -> 3.
    for raw in header_texts:
        t = collapse_ws(raw)
        if AISI_S100_LABEL_TOKEN_RE.fullmatch(t) and t.lower() not in {"c"}:
            if ROMAN_RE.fullmatch(t) and not re.search(r"\d", t):
                return t.lower()
            return t
    # AISC 358: "9.2-15" / "9.2-iii" (NOT 16.1-xxx).
    for raw in header_texts:
        m = AISC_358_PRINTED_LABEL_RE.search(raw or "")
        if m:
            token = m.group(1)
            if ROMAN_RE.fullmatch(token):
                token = token.lower()
            return f"9.2-{token}"
    # AISC 341: "9.1-45" / "9.1-iii" (NOT 16.1-xxx or 9.2-xxx).
    for raw in header_texts:
        m = AISC_341_PRINTED_LABEL_RE.search(raw or "")
        if m:
            token = m.group(1)
            if ROMAN_RE.fullmatch(token):
                token = token.lower()
            return f"9.1-{token}"
    # AISC 360: "16.1-52" / "16.1-viii" (Docling may emit "16.1 -viii").
    for raw in header_texts:
        m = AISC_PRINTED_LABEL_RE.search(raw or "")
        if m:
            token = m.group(1)
            if ROMAN_RE.fullmatch(token):
                token = token.lower()
            return f"16.1-{token}"
    # Commentary headers sometimes split "16.1 Comm. F2.]" and "-377".
    has_161 = any(re.search(r"16\s*\.\s*1\b", raw or "", re.I) for raw in header_texts)
    if has_161:
        for raw in header_texts:
            t = collapse_ws(raw)
            m = re.fullmatch(r"-?(\d{1,4})", t)
            if m:
                return f"16.1-{m.group(1)}"
            m = re.fullmatch(r"-?([ivxlcdm]+)", t, re.I)
            if m and t.lower().lstrip("-") not in {"c", "d", "l", "m", "v", "x", "i"}:
                return f"16.1-{m.group(1).lower()}"
    has_91 = any(re.search(r"9\s*\.\s*1\b", raw or "", re.I) for raw in header_texts)
    if has_91 and not any(re.search(r"9\s*\.\s*2\b", raw or "", re.I) for raw in header_texts):
        for raw in header_texts:
            t = collapse_ws(raw)
            m = re.fullmatch(r"-?(\d{1,4})", t)
            if m:
                return f"9.1-{m.group(1)}"
            m = re.fullmatch(r"-?([ivxlcdm]+)", t, re.I)
            if m and t.lower().lstrip("-") not in {"c", "d", "l", "m", "v", "x", "i"}:
                return f"9.1-{m.group(1).lower()}"
    leftover: list[str] = []
    for raw in header_texts:
        t = collapse_ws(raw)
        if not t or looks_like_running_title(t):
            # long running title may have a glued page number: "...Edition 59"
            if looks_like_running_title(t):
                m = TRAILING_NUM_RE.search(t)
                # only if the title regex allowed a trailing number
                if m and not ARABIC_RE.fullmatch(t) and not ROMAN_RE.fullmatch(t):
                    # e.g. "Commentary on ... Edition 59"
                    if re.search(r"Edition\s+\d+\s*$", t, re.I):
                        leftover.append(m.group(1))
            continue
        leftover.append(t)
    for t in leftover:
        if is_page_number_token(t):
            return t.lower() if ROMAN_RE.fullmatch(t) else t
    for t in leftover:
        m = TRAILING_NUM_RE.search(t)
        if m and not re.search(r"S400|S100|S240|Supplement", t, re.I):
            return m.group(1)
        if AISI_S100_LABEL_TOKEN_RE.fullmatch(t) and t.lower() not in {"c"}:
            return t.lower() if ROMAN_RE.fullmatch(t) and not re.search(r"\d", t) else t
    return None


def furniture_by_page(loaded: LoadedDoc) -> dict[int, dict[str, Any]]:
    pages: dict[int, dict[str, Any]] = {}
    pdf_total = int(loaded.meta.get("pdf_pages_total") or 0)
    observed = {p for t in loaded.texts for p in t.pages}
    n = max([pdf_total, *observed], default=0)
    for pno in range(1, n + 1):
        pages[pno] = {
            "pdf_page": pno,
            "headers": [],
            "footers": [],
            "header_texts": [],
            "footer_texts": [],
        }
    for t in loaded.texts:
        if t.label not in ("page_header", "page_footer") and t.layer != "furniture":
            continue
        for pno in t.pages:
            rec = pages.setdefault(
                pno,
                {
                    "pdf_page": pno,
                    "headers": [],
                    "footers": [],
                    "header_texts": [],
                    "footer_texts": [],
                },
            )
            blob = {
                "label": t.label,
                "text": t.text,
                "self_ref": t.self_ref,
            }
            if t.label == "page_footer" or "copyright" in t.text.lower():
                rec["footers"].append(blob)
                rec["footer_texts"].append(t.text)
            else:
                rec["headers"].append(blob)
                rec["header_texts"].append(t.text)
    for pno, rec in pages.items():
        rec["printed_label"] = extract_printed_label(
            (rec["header_texts"] or []) + (rec["footer_texts"] or [])
        )
    return pages


def detect_commentary(
    loaded: LoadedDoc,
    furn: dict[int, dict[str, Any]],
    profile: CommentaryProfile,
) -> dict[str, Any]:
    # Most BIS / Indian Standards have no separate commentary volume. Cover
    # tokens like "Indian Standard" / "IS 800" appear on page 1; with
    # family_cutoff=1 that previously marked every page as commentary.
    if profile.name == "is_bis":
        return {
            "profile": profile.name,
            "notes": (
                "BIS: no separate commentary volume; all pages standard"
            ),
            "cover_pdf_page": None,
            "body_pdf_page": None,
            "running_header_pages_sample": [],
            "body_heading_pages_sample": [],
        }
    rx = profile.compiled()
    cover_pdf: Optional[int] = None
    running_pages: list[int] = []
    body_heading_pages: list[int] = []

    for pno, rec in sorted(furn.items()):
        blob = "\n".join(rec.get("header_texts") or [])
        if cover_pdf is None and any(r.search(blob) for r in rx["cover"]):
            cover_pdf = pno
        if any(r.search(blob) for r in rx["running"]):
            running_pages.append(pno)

    for t in loaded.texts:
        if t.layer != "body":
            continue
        if t.label not in ("section_header", "title", "text"):
            continue
        txt = t.text.strip()
        if any(r.search(txt) for r in rx["body"]):
            body_heading_pages.extend(t.pages)

    n_pages = max(furn) if furn else 0
    # AISC / ASCE / S100: TOC or disclaimer lists the commentary title near
    # the front. Restrict cover detection so we don't pin to the TOC.
    # AISI S400/S100 Chapter C is NOT ASCE commentary — never use asce7.
    # S100 p3 "Commentary on the Specification" must not pin the cover.
    if profile.name in ("aisc", "aisc_358", "aisc_341", "aisc_342", "aisi_s100"):
        family_cutoff = max(200, n_pages // 3)
    elif profile.name in ("asce7", "asce_41"):
        family_cutoff = max(400, n_pages // 2)
    else:
        family_cutoff = 1
    aisc_cutoff = family_cutoff
    cutoff_profiles = ("aisc", "aisc_358", "aisc_341", "aisc_342", "asce7", "asce_41", "aisi_s100")

    # Fallback: first page whose furniture/body mentions the cover regex
    if cover_pdf is None or (profile.name in cutoff_profiles and cover_pdf < aisc_cutoff):
        cover_pdf = None if (profile.name in cutoff_profiles and cover_pdf is not None and cover_pdf < aisc_cutoff) else cover_pdf
        if cover_pdf is None:
            hits: list[int] = []
            for t in loaded.texts:
                blob = f"{t.text}\n{t.orig}"
                if any(r.search(blob) for r in rx["cover"] + rx["body"]):
                    for p in t.pages:
                        if p >= aisc_cutoff:
                            hits.append(p)
            if hits:
                cover_pdf = min(hits)

    if profile.name in cutoff_profiles:
        running_pages = [p for p in running_pages if p >= aisc_cutoff]
        body_heading_pages = [p for p in body_heading_pages if p >= aisc_cutoff]
        if cover_pdf is None and running_pages:
            first_run = min(running_pages)
            cover_pdf = first_run
            for pno in range(max(aisc_cutoff, first_run - 20), first_run):
                rec = furn.get(pno) or {}
                blob = "\n".join((rec.get("header_texts") or []) + (rec.get("footer_texts") or []))
                if re.search(r"COMMENTARY", blob, re.I):
                    cover_pdf = pno
                    break
        # Prefer a body COMMENTARY heading just before the first running header
        # (358 cover is pdf 211; running "Comm. n.]" starts ~213).
        if body_heading_pages:
            first_body = min(body_heading_pages)
            if first_body >= aisc_cutoff and (cover_pdf is None or first_body < int(cover_pdf)):
                cover_pdf = first_body

    body_pdf: Optional[int] = None
    if cover_pdf is not None:
        if profile.name in ("aisc", "aisc_358", "aisc_341", "asce7"):
            # AISC 360 labels stay 16.1-xxx; 358=9.2-xxx; 341=9.1-xxx; ASCE
            # commentary continues arabic so do not hunt for a restarted "1".
            body_pdf = cover_pdf
            after = [p for p in body_heading_pages if p >= cover_pdf]
            if after:
                body_pdf = min(after)
        else:
            # first printed arabic "1" at or after the cover, with commentary headers
            for pno in range(cover_pdf, max(furn) + 1):
                rec = furn.get(pno) or {}
                label = rec.get("printed_label")
                headers = "\n".join(rec.get("header_texts") or [])
                is_comm_header = any(r.search(headers) for r in rx["running"] + rx["cover"])
                if label == "1" and (is_comm_header or pno in running_pages or pno in body_heading_pages):
                    body_pdf = pno
                    break
            if body_pdf is None:
                after = [p for p in body_heading_pages if p >= cover_pdf]
                body_pdf = min(after) if after else cover_pdf

    return {
        "profile": profile.name,
        "notes": profile.notes,
        "cover_pdf_page": cover_pdf,
        "body_pdf_page": body_pdf,
        "running_header_pages_sample": running_pages[:8],
        "body_heading_pages_sample": sorted(set(body_heading_pages))[:8],
    }


def part_for_page(pno: int, boundary: dict[str, Any]) -> str:
    cover = boundary.get("cover_pdf_page")
    if cover is None:
        return "standard"
    return "commentary" if pno >= int(cover) else "standard"


def qualify_printed_label(label: Optional[str], part: str) -> Optional[str]:
    if not label:
        return None
    if part != "commentary":
        return label
    if ARABIC_RE.fullmatch(label) or ROMAN_RE.fullmatch(label):
        return f"C-{label}"
    return label


def finalize_page_map(
    furn: dict[int, dict[str, Any]],
    boundary: dict[str, Any],
) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for pno, rec in sorted(furn.items()):
        part = part_for_page(pno, boundary)
        printed = rec.get("printed_label")
        out[pno] = {
            "pdf_page": pno,
            "printed_label": printed,
            "printed_label_qualified": qualify_printed_label(printed, part),
            "part": part,
            "headers": rec.get("header_texts") or [],
            "footers": rec.get("footer_texts") or [],
        }
    return out


def nearest_section_hint(
    page: int, sections_by_page: dict[int, list[str]], current: Optional[str]
) -> Optional[str]:
    if current:
        return current
    for p in range(page, 0, -1):
        ids = sections_by_page.get(p) or []
        if ids:
            return ids[-1]
    return None


# ---------------------------------------------------------------------------
# PDF equation census
# ---------------------------------------------------------------------------


def pdf_text_pages(pdf_path: Path) -> list[str]:
    import subprocess

    r = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        LOG.warning("pdftotext failed rc=%s err=%s", r.returncode, r.stderr[:300])
        return []
    pages = r.stdout.split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    return pages


def census_pdf_eq_ids(
    pdf_path: Path,
    page_map: dict[int, dict[str, Any]],
    section_at_page: dict[int, Optional[str]],
) -> list[dict[str, Any]]:
    pages = pdf_text_pages(pdf_path)
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for i, pg in enumerate(pages, 1):
        hint = section_at_page.get(i)
        for canon, verbatim in find_eq_ids_in_text(pg, hint):
            key = (canon, i)
            if key in seen:
                continue
            seen.add(key)
            found.append(
                {
                    "eq_id": canon,
                    "verbatim": collapse_ws(verbatim),
                    "pdf_page": i,
                    "printed_label": (page_map.get(i) or {}).get("printed_label"),
                    "part": (page_map.get(i) or {}).get("part") or "standard",
                }
            )
    return found


# ---------------------------------------------------------------------------
# Equation ID recovery from adjacent text
# ---------------------------------------------------------------------------


def recover_equation_ids(
    loaded: LoadedDoc,
    page_map: dict[int, dict[str, Any]],
    section_at_page: dict[int, Optional[str]],
    pdf_census: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    # body items by page for neighbor search
    body_by_page: dict[int, list[TextItem]] = {}
    for t in loaded.texts:
        if t.layer != "body":
            continue
        for p in t.pages:
            body_by_page.setdefault(p, []).append(t)

    census_by_page: dict[int, list[str]] = {}
    for rec in pdf_census:
        census_by_page.setdefault(rec["pdf_page"], []).append(rec["eq_id"])

    used: set[tuple[str, int]] = set()
    out: list[dict[str, Any]] = []

    for idx, f in enumerate(loaded.formulas, 1):
        pno = f.pages[0] if f.pages else None
        hint = section_at_page.get(pno) if pno else None
        nearby_bits: list[str] = []
        nearby_bits.append(f.orig)
        nearby_bits.append(f.text)
        same_line: list[str] = []
        if pno:
            fy = y_center(f.bbox)
            neighbors: list[tuple[float, TextItem]] = []
            for t in body_by_page.get(pno, []):
                if t.self_ref == f.self_ref:
                    continue
                ty = y_center(t.bbox)
                dy = abs(ty - fy)
                neighbors.append((dy, t))
                if dy < 14 or (
                    f.bbox
                    and t.bbox
                    and not (
                        float(t.bbox.get("t") or 0) < float(f.bbox.get("b") or 0) - 2
                        or float(t.bbox.get("b") or 0) > float(f.bbox.get("t") or 0) + 2
                    )
                ):
                    same_line.append(t.orig or t.text)
            neighbors.sort(key=lambda x: x[0])
            for dy, t in neighbors[:6]:
                nearby_bits.append(t.orig or t.text)
        nearby_text = " ".join(x for x in (same_line + nearby_bits) if x)
        recovered: list[tuple[str, str]] = find_eq_ids_in_text(nearby_text, hint)
        # Prefer an unused census id on this page if recovered is empty
        eq_id = None
        verbatim = None
        for canon, verb in recovered:
            if pno is None or (canon, pno) not in used:
                eq_id = canon
                verbatim = verb
                break
        if eq_id is None and pno:
            for canon in census_by_page.get(pno, []):
                if (canon, pno) not in used:
                    # only assign if formula looks like real math or orig mentions Eq
                    if "Eq" in (f.orig or "") or "Eq" in (f.text or "") or "\\" in (f.text or ""):
                        eq_id = canon
                        verbatim = format_eq_display(canon) or f"({canon})"
                        break
        if eq_id and pno:
            used.add((eq_id, pno))
        pm = page_map.get(pno or -1) or {}
        rec = {
            "index": idx,
            "eq_id": eq_id,
            "eq_id_display": format_eq_display(eq_id),
            "self_ref": f.self_ref,
            "chunk": f.chunk,
            "pdf_page": pno,
            "printed_label": pm.get("printed_label"),
            "printed_label_qualified": pm.get("printed_label_qualified"),
            "part": pm.get("part") or "standard",
            "section_hint": hint,
            "latex": f.text,
            "orig": f.orig,
            "nearby_text": collapse_ws(nearby_text)[:500],
            "issues": latex_issues(f.text, f.orig),
            "source": "formula+adjacent" if eq_id else "formula_unidentified",
        }
        out.append(rec)

    # PDF census IDs with no matching formula still belong in the index
    for rec in pdf_census:
        key = (rec["eq_id"], rec["pdf_page"])
        if key in used:
            continue
        pm = page_map.get(rec["pdf_page"]) or {}
        out.append(
            {
                "index": None,
                "eq_id": rec["eq_id"],
                "eq_id_display": format_eq_display(rec["eq_id"]),
                "self_ref": None,
                "chunk": None,
                "pdf_page": rec["pdf_page"],
                "printed_label": pm.get("printed_label"),
                "printed_label_qualified": pm.get("printed_label_qualified"),
                "part": rec.get("part") or pm.get("part") or "standard",
                "section_hint": section_at_page.get(rec["pdf_page"]),
                "latex": None,
                "orig": None,
                "nearby_text": rec.get("verbatim"),
                "issues": [
                    "PDF text has this Eq. id but no Docling formula item was matched"
                ],
                "source": "pdf_text",
            }
        )
        used.add(key)
    return out


# ---------------------------------------------------------------------------
# Sections / tables
# ---------------------------------------------------------------------------



def _aisc_chapter_prefix(sid: str) -> Optional[str]:
    """D1.2 / D1.2a / E3.4a -> D1 / E3. Bare D1 / E3 unchanged."""
    m = re.match(r"^((?:C-)?[A-Z]\d+[a-z]?)", sid or "")
    return m.group(1) if m else None


def _is_toc_style_title(title: str) -> bool:
    """TOC listings are title-case ('H-Piles'); body headings are ALL CAPS."""
    letters = [c for c in (title or "") if c.isalpha()]
    if len(letters) < 4:
        return False
    return (sum(c.isupper() for c in letters) / len(letters)) < 0.75


def synthesize_aisc_subsections(
    loaded: LoadedDoc,
    page_map: dict[int, dict[str, Any]],
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """F2 + '2. Lateral-Torsional Buckling' -> F2.2 (both halves).

    AISC numbers subsections as 1. / 2. / 1a. / 2a. under F2, D1, B4, …
    rather than printing 'F2.2' as a heading. AISI S400 already prints
    E3.4.2, so this is AISC-only.

    Implied prefix is the last *body* chapter-level heading in page order
    (D1, D4, E3). Dotted headings like D1.2 keep parent D1 so that '2a.'
    becomes D1.2a and '3.' becomes D1.3, not D1.2.3. TOC listings
    ('D4. H-Piles' on the chapter opener) must not remain the live parent
    through a later D1 body — that is what produced D4.2a for D1.2a.
    """
    if not is_aisc_doc(loaded.stem):
        return sections
    existing = {(s["section_id"], s["part"]) for s in sections}
    current_parent: dict[str, Optional[str]] = {"standard": None, "commentary": None}
    items = [
        t
        for t in loaded.texts
        if t.layer == "body" and t.label in ("section_header", "title", "text")
    ]
    # Page order, then top-to-bottom (Docling bbox t is PDF y, origin bottom-left).
    items.sort(
        key=lambda t: (
            t.pages[0] if t.pages else 10**9,
            -(y_center(t.bbox) if t.bbox else 0.0),
            t.self_ref or "",
        )
    )
    for t in items:
        pno = t.pages[0] if t.pages else None
        pm = page_map.get(pno or -1) or {}
        part = pm.get("part") or "standard"
        parsed = parse_section_heading(t.text)
        if parsed:
            sid, title = parsed
            if re.match(r"^[A-Z]\d+[a-z]?$", sid):
                # D1 / D4 / E3. Skip TOC title-case so D4. H-Piles on the
                # chapter outline does not overwrite live body parent D1.
                if _is_toc_style_title(title):
                    continue
                current_parent[part] = sid
            elif re.match(r"^[A-Z]\d+", sid):
                # D1.2 / E3.4a printed in full: keep the chapter prefix (D1),
                # not the dotted id, as the implied parent for 2a./3./4a.
                prefix = _aisc_chapter_prefix(sid)
                if prefix:
                    current_parent[part] = prefix
            elif is_aisc_358_doc(loaded.stem) and re.match(r"^\d+\.\d+", sid):
                # 5.3 + "1. Beam Limitations" -> 5.3.1. Do not use bare
                # chapter "5" as parent (5.1 is already a printed heading).
                current_parent[part] = sid
            continue
        m = AISC_NUMBERED_SUBHEAD_RE.match(collapse_ws(t.text))
        if not m:
            continue
        parent = current_parent.get(part)
        if not parent:
            continue
        num, title = m.group(1), (m.group(2) or "").strip()
        if not title or len(title) > 140:
            continue
        sid = f"{parent}.{num}"
        if (sid, part) in existing:
            continue
        existing.add((sid, part))
        sections.append(
            {
                "doc": loaded.stem,
                "section_id": sid,
                "title": title,
                "part": part,
                "pdf_page": pno,
                "printed_label": pm.get("printed_label"),
                "printed_label_qualified": pm.get("printed_label_qualified"),
                "parent": parent_section_id(sid) or parent,
                "self_ref": t.self_ref,
                "chunk": t.chunk,
                "synthetic": True,
            }
        )
    return sections


def synthesize_asce_subsections(
    loaded: LoadedDoc,
    page_map: dict[int, dict[str, Any]],
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """ASCE 7 often emits 12.4.3.2 / C1.1 as list_item or paragraph, not section_header."""
    if not (is_asce7_doc(loaded.stem) or is_asce_41_doc(loaded.stem)):
        return sections
    existing = {(s["section_id"], s["part"]) for s in sections}
    for t in loaded.texts:
        if t.layer != "body":
            continue
        if t.label not in ("section_header", "title", "text", "list_item", "paragraph"):
            continue
        # Docling list_item often strips the marker ("12.4.3.2") from text;
        # orig keeps "12.4.3.2 Capacity-Limited...".
        parsed = None
        for candidate in (t.orig, t.text):
            raw = collapse_ws(candidate or "")
            raw = re.sub(r"^[-*•]\s*", "", raw)
            parsed = parse_section_heading(raw)
            if parsed:
                break
        if not parsed:
            continue
        sid, title = parsed
        if not re.match(r"^C?\d+", sid):
            continue
        if len(title) > 200:
            title = title[:200].rstrip()
        pno = t.pages[0] if t.pages else None
        pm = page_map.get(pno or -1) or {}
        part = pm.get("part") or "standard"
        if (sid, part) in existing:
            continue
        existing.add((sid, part))
        sections.append(
            {
                "doc": loaded.stem,
                "section_id": sid,
                "title": title,
                "part": part,
                "pdf_page": pno,
                "printed_label": pm.get("printed_label"),
                "printed_label_qualified": pm.get("printed_label_qualified"),
                "parent": parent_section_id(sid),
                "self_ref": t.self_ref,
                "chunk": t.chunk,
                "synthetic": True,
            }
        )
    return sections




def synthesize_aisi_inline_subsections(
    loaded: LoadedDoc,
    page_map: dict[int, dict[str, Any]],
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """S240 (AISI letter.number) often emits D5.1.2 as list_item/paragraph.

    Docling keeps 'D5.1 Quality Control Inspector' as a section_header but
    run-on provisions ('D5.1.2 Quality control welding inspection personnel…')
    land in list_item / text. Same pattern as ASCE 12.4.3.2. TOC preview
    listings are dropped later by drop_children_before_parent_root.
    """
    if not is_aisi_s240_doc(loaded.stem):
        return sections
    existing = {(s["section_id"], s["part"]) for s in sections}
    for t in loaded.texts:
        if t.layer != "body":
            continue
        if t.label not in ("section_header", "title", "text", "list_item", "paragraph"):
            continue
        parsed = None
        for candidate in (t.orig, t.text):
            raw = collapse_ws(candidate or "")
            raw = re.sub(r"^[-*•]\s*", "", raw)
            parsed = parse_section_heading(raw)
            if parsed:
                break
        if not parsed:
            continue
        sid, title = parsed
        # Require a dotted AISI id (D5.1.2 / B5.2.2.3.4). Bare D5 / A. stay
        # as Docling section_headers.
        if not re.match(r"^[A-Z]\d+(?:\.\d+)+[a-z]?$", sid):
            continue
        if "...." in (t.text or "") or "…" in (t.text or ""):
            continue
        if len(title) > 200:
            title = title[:200].rstrip()
        pno = t.pages[0] if t.pages else None
        pm = page_map.get(pno or -1) or {}
        part = pm.get("part") or "standard"
        if (sid, part) in existing:
            continue
        existing.add((sid, part))
        sections.append(
            {
                "doc": loaded.stem,
                "section_id": sid,
                "title": title,
                "part": part,
                "pdf_page": pno,
                "printed_label": pm.get("printed_label"),
                "printed_label_qualified": pm.get("printed_label_qualified"),
                "parent": parent_section_id(sid),
                "self_ref": t.self_ref,
                "chunk": t.chunk,
                "synthetic": True,
            }
        )
    return sections


def drop_children_before_parent_root(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop chapter-opener / embedded cross-refs that precede the parent-root body.

    S100 Ch.E opener lists "I1.2 Compression Members…" on pdf 102, but I1 body
    is pdf 134. Those preview listings are not the provision.

    ASCE/SEI 41: Chapter 10 incorporates ACI 369 section numbers (12.1.2 Deep
    Concrete Foundations on pdf ~180) before ASCE Chapter 12 WOOD body (~208).
    Prefer ALL-CAPS body chapter titles when choosing the parent root page so
    those embedded ACI ids are dropped; TOC empty-title stubs do not count.
    """
    std_by_id: dict[str, list[dict[str, Any]]] = {}
    for s in sections:
        if s.get("part") != "standard" or s.get("pdf_page") is None:
            continue
        sid = str(s.get("section_id") or "")
        std_by_id.setdefault(sid, []).append(s)

    def root_id(sid: str) -> Optional[str]:
        m = re.match(r"^([A-Z]\d+[a-z]?)", sid or "")
        if m:
            return m.group(1)
        # ASCE numeric / C-prefixed chapters: 12.1.2 -> 12; C1.1 -> C1
        m = re.match(r"^(C?\d+)", sid or "")
        return m.group(1) if m else None

    def _body_heading(title: str) -> bool:
        letters = [c for c in (title or "") if c.isalpha()]
        if len(letters) < 3:
            return False
        return (sum(c.isupper() for c in letters) / len(letters)) >= 0.75

    def root_body_page(root: str) -> Optional[int]:
        hits = std_by_id.get(root) or []
        body = [s for s in hits if _body_heading(s.get("title") or "")]
        use = body or hits
        pages = [int(s["pdf_page"]) for s in use if s.get("pdf_page") is not None]
        return min(pages) if pages else None

    kept: list[dict[str, Any]] = []
    for s in sections:
        if s.get("part") != "standard" or s.get("pdf_page") is None:
            kept.append(s)
            continue
        sid = str(s.get("section_id") or "")
        root = root_id(sid)
        if not root or sid == root:
            kept.append(s)
            continue
        bp = root_body_page(root)
        if bp is not None and int(s["pdf_page"]) < bp:
            continue
        kept.append(s)
    return kept



def synthesize_bis_sections(
    loaded: LoadedDoc,
    page_map: dict[int, dict[str, Any]],
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Recover BIS/IS numeric + SECTION N headings from body texts.

    Docling often labels "1. SCOPE" / "1 SCOPE" / "SECTION 5 …" as
    section_header but parse_section_heading previously only accepted
    AISC letter-ids and dotted ASCE clauses. Also scan text/title when
    section_header set is sparse.
    """
    if not is_bis_doc(loaded.stem):
        return sections
    existing = {(s["section_id"], s["part"]) for s in sections}
    labels = ("section_header", "title", "text", "list_item", "paragraph")
    # Prefer section_header first pass, then broaden if still sparse
    for pass_labels in (("section_header",), labels):
        for t in loaded.texts:
            if t.layer != "body":
                continue
            if t.label not in pass_labels:
                continue
            parsed = None
            for candidate in (t.orig, t.text):
                raw = collapse_ws(candidate or "")
                raw = re.sub(r"^[-*•]\s*", "", raw)
                parsed = parse_section_heading(raw)
                if parsed:
                    break
            if not parsed:
                continue
            sid, title = parsed
            # BIS ids are numeric (optional SECTION mapped to bare number)
            if not re.match(r"^\d+(?:\.\d+)*$", sid):
                continue
            if len(title) > 200:
                title = title[:200].rstrip()
            # Skip obvious TOC / page-number garbage
            if not title and sid.count(".") >= 2:
                continue
            pno = t.pages[0] if t.pages else None
            pm = page_map.get(pno or -1) or {}
            part = pm.get("part") or "standard"
            if (sid, part) in existing:
                continue
            existing.add((sid, part))
            sections.append(
                {
                    "doc": loaded.stem,
                    "section_id": sid,
                    "title": title,
                    "part": part,
                    "pdf_page": pno,
                    "printed_label": pm.get("printed_label"),
                    "printed_label_qualified": pm.get("printed_label_qualified"),
                    "parent": parent_section_id(sid),
                    "self_ref": t.self_ref,
                    "chunk": t.chunk,
                    "synthetic": True,
                    "source": "bis_text",
                }
            )
        if len(sections) >= 5:
            break
    return sections


def recover_bis_sections_from_pages_search(
    loaded: LoadedDoc,
    page_map: dict[int, dict[str, Any]],
    sections: list[dict[str, Any]],
    pages_search_dir: Path,
) -> list[dict[str, Any]]:
    """Backfill BIS sections by scanning pages_search / pages_recovered markdown.

    Used when Docling section_header texts are CID-mojibake (e.g. IS 1893) or
    headings only appear cleanly after pdftotext recovery.
    """
    if not is_bis_doc(loaded.stem):
        return sections
    if not pages_search_dir.is_dir():
        return sections
    existing = {(s["section_id"], s["part"]) for s in sections}
    # Also accept pages_recovered if pages_search still garbled
    dirs = [pages_search_dir]
    recovered = pages_search_dir.parent / "pages_recovered"
    if recovered.is_dir():
        dirs.append(recovered)

    heading_line = re.compile(
        r"^(?:#{1,3}\s*)?(?:"
        r"SECTION\s+(\d+)\b(?:\s+(.*))?|"
        r"(\d+(?:\.\d+)*)\.\s+(\S.*)|"
        r"(\d+(?:\.\d+)*)\s+([A-Z][A-Za-z0-9][A-Za-z0-9 ,/\-]{0,100})"
        r")\s*$"
    )

    added = 0
    for d in dirs:
        for pfile in sorted(d.glob("page_*.md")):
            m_p = re.search(r"page_(\d+)", pfile.name)
            if not m_p:
                continue
            pno = int(m_p.group(1))
            # Skip pages that are still mostly CID garbage
            try:
                blob = pfile.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            sample = blob[:2000]
            if sample.count("G6") + sample.count("G4") + sample.count("cid:") > 20:
                continue
            pm = page_map.get(pno) or {}
            part = pm.get("part") or "standard"
            for ln in blob.splitlines():
                t = collapse_ws(ln)
                if not t or t.startswith("<!--"):
                    continue
                # strip markdown bullets
                t = re.sub(r"^[-*•]\s*", "", t)
                parsed = parse_section_heading(t)
                if not parsed:
                    m = heading_line.match(t)
                    if not m:
                        continue
                    if m.group(1):
                        sid, title = m.group(1), (m.group(2) or "").strip()
                    elif m.group(3):
                        sid, title = m.group(3), (m.group(4) or "").strip()
                    else:
                        sid, title = m.group(5), (m.group(6) or "").strip()
                        if not re.search(r"[A-Za-z]{2,}", title):
                            continue
                    parsed = (sid, title)
                sid, title = parsed
                if not re.match(r"^\d+(?:\.\d+)*$", sid):
                    continue
                if len(title) > 200:
                    title = title[:200].rstrip()
                if (sid, part) in existing:
                    continue
                # Avoid table-row false positives: very long titles with digits
                if title.count(" ") > 18:
                    continue
                existing.add((sid, part))
                sections.append(
                    {
                        "doc": loaded.stem,
                        "section_id": sid,
                        "title": title,
                        "part": part,
                        "pdf_page": pno,
                        "printed_label": pm.get("printed_label"),
                        "printed_label_qualified": pm.get("printed_label_qualified"),
                        "parent": parent_section_id(sid),
                        "self_ref": None,
                        "chunk": None,
                        "synthetic": True,
                        "source": "bis_pages_search",
                    }
                )
                added += 1
        if added:
            break  # prefer first usable dir
    if added:
        LOG.info("BIS pages_search recovery: +%s sections for %s", added, loaded.stem)
    return sections


def _relink_section_children(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {(s["section_id"], s["part"]): s for s in sections}
    for s in sections:
        s["children"] = []
    for s in sections:
        parent = s.get("parent")
        if parent:
            rec = by_key.get((parent, s["part"]))
            if rec is not None and s["section_id"] not in rec["children"]:
                rec["children"].append(s["section_id"])
    return sections


def build_sections(
    loaded: LoadedDoc,
    page_map: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for t in loaded.texts:
        if t.layer != "body" or t.label != "section_header":
            continue
        parsed = parse_section_heading(t.text)
        if not parsed:
            continue
        sid, title = parsed
        pno = t.pages[0] if t.pages else None
        pm = page_map.get(pno or -1) or {}
        sections.append(
            {
                "doc": loaded.stem,
                "section_id": sid,
                "title": title,
                "part": pm.get("part") or "standard",
                "pdf_page": pno,
                "printed_label": pm.get("printed_label"),
                "printed_label_qualified": pm.get("printed_label_qualified"),
                "parent": parent_section_id(sid),
                "self_ref": t.self_ref,
                "chunk": t.chunk,
            }
        )
    sections = synthesize_aisc_subsections(loaded, page_map, sections)
    sections = synthesize_asce_subsections(loaded, page_map, sections)
    sections = synthesize_aisi_inline_subsections(loaded, page_map, sections)
    sections = synthesize_bis_sections(loaded, page_map, sections)
    sections = drop_children_before_parent_root(sections)
    sections = _relink_section_children(sections)
    return sections


def section_at_page_map(sections: list[dict[str, Any]]) -> dict[int, Optional[str]]:
    """Latest section id seen at or before each pdf page (per running pass)."""
    by_page: dict[int, str] = {}
    for s in sections:
        p = s.get("pdf_page")
        if p:
            by_page[p] = s["section_id"]
    out: dict[int, Optional[str]] = {}
    last = None
    if not by_page:
        return out
    for p in range(1, max(by_page) + 1):
        if p in by_page:
            last = by_page[p]
        out[p] = last
    return out


# Two-line / in-grid AISC titles: "TABLE A3.2" then "Ry and Rt Values…".
# TABLE_ID_RE requires a trailing alnum after a greedy [\w.-]* which can
# miss a bare "TABLE A3.2" line; this pattern stops at the id.
TABLE_ID_LINE_RE = re.compile(
    r"\bTABLE\s+("
    r"(?:C-)?"  # C-N5.6-2
    r"(?:[A-Z]-)?"  # A-3.1
    r"(?:[A-Z]?\d+(?:\.\d+)*[A-Za-z]?)"  # A3.2 / 26.11 / C26.5 / B4.1a
    r"(?:-\d+[A-Za-z]?)?"  # -1 / -3 / -1A  (ASCE 26.11-1, C26.5-3)
    r")\b",
    re.I,
)


def recover_table_id(caption: str, nearby: str) -> Optional[str]:
    blob = f"{caption}\n{nearby}"
    m = TABLE_ID_LINE_RE.search(blob)
    if not m:
        m = TABLE_ID_RE.search(blob)
    if not m:
        return None
    tid = re.sub(r"\s+", "", m.group(1))
    tid = re.sub(r"-{2,}", "-", tid)
    return tid


def table_title_from_blob(blob: str, tid: Optional[str] = None) -> Optional[str]:
    if not blob:
        return None
    if tid:
        m = re.search(
            rf"TABLE\s+{re.escape(tid)}\b[^\n|]{{0,160}}",
            blob,
            re.I,
        )
        if m:
            return collapse_ws(m.group(0))
    m = re.search(r"TABLE\s+[A-Z]?\d[^\n|]{0,80}", blob, re.I)
    if m:
        return collapse_ws(m.group(0))
    return None


def build_tables(
    loaded: LoadedDoc,
    page_map: dict[int, dict[str, Any]],
    section_at_page: dict[int, Optional[str]],
) -> list[dict[str, Any]]:
    # existing convert tables index (markdown/csv paths)
    idx_path = loaded.doc_dir / "tables" / f"{loaded.stem}_tables_index.json"
    convert_index: list[dict[str, Any]] = []
    if idx_path.is_file():
        convert_index = json.loads(idx_path.read_text(encoding="utf-8"))

    # nearby captions from body text
    captions_by_page: dict[int, list[str]] = {}
    for t in loaded.texts:
        if t.layer != "body":
            continue
        if t.label in ("caption", "text", "section_header") and re.search(
            r"Table\s+", t.text, re.I
        ):
            for p in t.pages:
                captions_by_page.setdefault(p, []).append(t.text)

    recovered_idx_path = loaded.doc_dir / "tables" / "recovered_index.json"
    recovered_list: list[dict[str, Any]] = []
    if recovered_idx_path.is_file():
        try:
            recovered_list = json.loads(recovered_idx_path.read_text(encoding="utf-8"))
            if not isinstance(recovered_list, list):
                recovered_list = []
        except Exception:
            recovered_list = []
    recovered_pages = {
        int(r["pdf_page"])
        for r in recovered_list
        if r.get("pdf_page") is not None
    }
    recovered_ids = {
        (r.get("table_id") or "").replace(" ", "")
        for r in recovered_list
        if r.get("table_id")
    }

    out: list[dict[str, Any]] = []
    for i, rec in enumerate(convert_index, 1):
        pages = rec.get("pages") or []
        pno = pages[0] if pages else None
        if pno is not None and int(pno) in recovered_pages:
            # Replaced by raster/TableFormer or pdftotext recovery on this page.
            continue
        pm = page_map.get(pno or -1) or {}
        caption = rec.get("caption") or ""
        nearby = " ".join(captions_by_page.get(pno or -1) or [])
        md_path = rec.get("md")
        md_text = ""
        if md_path and Path(md_path).is_file():
            md_text = Path(md_path).read_text(encoding="utf-8")
        # Two-line "TABLE A3.2 / Ry and Rt Values" often lives in the grid,
        # not in Docling's caption field — scan markdown too.
        tid = recover_table_id(caption, nearby + "\n" + md_text[:2000])
        title = collapse_ws(caption) or table_title_from_blob(md_text or nearby, tid)
        out.append(
            {
                "doc": loaded.stem,
                "table_id": tid,
                "title": title or None,
                "section": section_at_page.get(pno or -1),
                "part": pm.get("part") or "standard",
                "pdf_pages": pages,
                "pdf_page": pno,
                "printed_label": pm.get("printed_label"),
                "printed_label_qualified": pm.get("printed_label_qualified"),
                "num_rows": rec.get("num_rows"),
                "num_cols": rec.get("num_cols"),
                "md": rec.get("md"),
                "csv": rec.get("csv"),
                "json": rec.get("json"),
                "markdown_excerpt": md_text[:400],
                "index": rec.get("index") or i,
            }
        )
    # Recovered tables (image-page TableFormer / pdftotext) — real num_rows.
    extra_i = len(out)
    for rec in recovered_list:
        extra_i += 1
        pno = rec.get("pdf_page")
        pm = page_map.get(pno or -1) or {}
        md_path = rec.get("md")
        md_text = ""
        if md_path and Path(md_path).is_file():
            md_text = Path(md_path).read_text(encoding="utf-8")
        elif rec.get("markdown"):
            md_text = rec.get("markdown") or ""
        tid = rec.get("table_id") or recover_table_id(
            rec.get("caption") or rec.get("title") or "",
            md_text[:2000],
        )
        title = rec.get("title") or collapse_ws(rec.get("caption") or "") or table_title_from_blob(md_text, tid)
        out.append(
            {
                "doc": loaded.stem,
                "table_id": tid,
                "title": title or None,
                "section": section_at_page.get(pno or -1),
                "part": rec.get("part") or pm.get("part") or "standard",
                "pdf_pages": rec.get("pdf_pages") or ([pno] if pno else []),
                "pdf_page": pno,
                "printed_label": rec.get("printed_label") or pm.get("printed_label"),
                "printed_label_qualified": rec.get("printed_label_qualified")
                or pm.get("printed_label_qualified"),
                "num_rows": rec.get("num_rows"),
                "num_cols": rec.get("num_cols"),
                "md": rec.get("md"),
                "csv": rec.get("csv"),
                "json": rec.get("json"),
                "markdown_excerpt": (md_text or "")[:400],
                "index": rec.get("index") or extra_i,
                "source": rec.get("source") or "recovered",
            }
        )

    # Caption-only rows only when no grid exists. caption_only with null
    # num_rows is a conversion FAILURE — skip ids already recovered.
    have_ids = {(t.get("table_id") or "").replace(" ", "") for t in out if t.get("table_id")}
    extra_i = len(out)
    seen_extra: set[tuple[str, int]] = set()
    for t in loaded.texts:
        if t.layer != "body":
            continue
        blob = t.text or ""
        if not re.search(r"Table\s+", blob, re.I):
            continue
        # Prefer real captions / title lines, not "see Table B4.1a" prose.
        ws = collapse_ws(blob)
        if t.label != "caption" and not re.match(r"^(TABLE|Table)\s+", ws):
            continue
        tid = recover_table_id(blob, "")
        if not tid:
            continue
        key = tid.replace(" ", "")
        pno = t.pages[0] if t.pages else None
        if not pno:
            continue
        if key in have_ids:
            continue
        if (key, pno) in seen_extra:
            continue
        seen_extra.add((key, pno))
        # caption_only with num_rows null is a conversion FAILURE. Skip.
        # Recovery should have produced a grid; if not, omit rather than fail validate.
        continue
    out = inherit_continued_table_ids(out)
    return out



# ---------------------------------------------------------------------------
# Equation husk backfill (empty latex/orig or id-echo latex)
# ---------------------------------------------------------------------------


HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
ID_ECHO_LATEX_RE = re.compile(r"E\s+q\s+\.", re.I)
# orig that is only the id token, e.g. "(Eq. 1.3.1.1-1)" or ". A3.2.3-1)"
ID_TOKEN_ONLY_RE = re.compile(
    r"^\s*[.\(\[]?\s*(?:Eq\.?\s*)?(?:C-)?[A-Z]?\d[\dA-Za-z.\s\-]*[\)\]]?\s*$",
    re.I,
)


def _is_letter_spaced_echo(text: str) -> bool:
    """True for letter-spaced id echoes such as E q . A 3 . 2 . 3 - 1."""
    t = (text or "").strip()
    if not t:
        return False
    body = re.sub(r"(?i)\bEq\.?\b", "", t)
    if re.search(r"[=≤≥]", t) and re.search(r"[A-Za-z]{2,}", body):
        return False
    if ID_ECHO_LATEX_RE.search(t):
        return True
    if re.search(r"(?:[A-Z]\s+)?\d\s+\.\s+\d\s+\.\s+\d", t) and not re.search(r"[=≤≥]", t):
        return True
    return False


def _eq_id_search_variants(eq_id: Optional[str], display: Optional[str]) -> list[tuple[str, str]]:
    """Needles as (kind, token). kind is 'full' or 'dropped'.

    Dropped chapter-letter forms must not match inside the full id
    (A3.2.3-1 must not be sliced at '3.2.3-1').
    """
    if not eq_id and not display:
        return []
    seen: set[str] = set()
    out: list[tuple[str, str]] = []

    def add(kind: str, x: Optional[str]) -> None:
        x = (x or "").strip()
        if x and x not in seen:
            seen.add(x)
            out.append((kind, x))

    add("full", display)
    ids_full: list[str] = []
    dropped: list[str] = []
    if eq_id:
        ids_full.append(eq_id)
        if eq_id.startswith("C-"):
            ids_full.append(eq_id[2:])
        for i in list(ids_full):
            m = re.match(r"^([A-Z])(\d.*)$", i)
            if m:
                dropped.append(m.group(2))
            m2 = re.match(r"^(.+-\d+)[a-z]$", i)
            if m2:
                dropped.append(m2.group(1))
                m3 = re.match(r"^([A-Z])(\d.*)$", m2.group(1))
                if m3:
                    dropped.append(m3.group(2))
    for i in ids_full:
        add("full", f"(Eq. {i})")
        add("full", f"(Eq.{i})")
        add("full", f"({i})")
        add("full", i)
    for i in dropped:
        add("dropped", f"(Eq. {i})")
        add("dropped", f"(Eq.{i})")
        add("dropped", f"({i})")
        add("dropped", i)
    return out


def _flex_needle(needle: str, *, dropped: bool = False) -> str:
    """Allow spaces around '.' / '-' / parens so '( Eq . 1.3.1.1-1)' matches."""
    parts: list[str] = []
    for ch in needle:
        if ch in ".·":
            parts.append(r"\s*\.\s*")
        elif ch == "-":
            parts.append(r"\s*-\s*")
        elif ch in "()[]":
            parts.append(r"\s*" + re.escape(ch) + r"\s*")
        elif ch.isspace():
            parts.append(r"\s*")
        else:
            parts.append(re.escape(ch) + r"\s*")
    body = "".join(parts)
    if dropped:
        # do not match the numeric tail of E1.3.1.1-1 / A3.2.3-1
        return r"(?<![A-Za-z0-9])" + body
    return body


def orig_is_id_husk(
    orig: str,
    eq_id: Optional[str],
    display: Optional[str],
) -> bool:
    """True when orig is empty, the id token itself, or a letter-spaced echo.

    Mashed OCR that still contains an identifier '=' math sentence is NOT a husk.
    """
    o = collapse_ws(orig or "")
    if not o:
        return True
    body = re.sub(r"(?i)\bEq\.?\b", "", o)
    has_math = bool(re.search(r"[=≤≥]", o)) and bool(re.search(r"[A-Za-z]{2,}", body))
    if has_math:
        return False
    if _is_letter_spaced_echo(o):
        return True
    remainder = o
    for _kind, tok in _eq_id_search_variants(eq_id, display):
        remainder = re.sub(re.escape(tok), " ", remainder, flags=re.I)
    remainder = re.sub(r"(?i)\bEq\.?\b", " ", remainder)
    remainder = re.sub(r"[\(\)\[\].,;:\-–—]", " ", remainder)
    remainder = collapse_ws(remainder)
    if len(remainder) < 8:
        return True
    compact_o = re.sub(r"[^A-Za-z0-9]", "", o).lower().replace("eq", "")
    compact_id = re.sub(r"[^A-Za-z0-9]", "", eq_id or "").lower()
    if compact_id and compact_id in compact_o and len(compact_o) <= len(compact_id) + 4:
        return True
    if ID_TOKEN_ONLY_RE.fullmatch(o):
        return True
    return False


def latex_is_id_echo(latex: str, eq_id: Optional[str]) -> bool:
    """True when latex is a letter-spaced equation id, not real math."""
    if not (latex or "").strip():
        return False
    if _is_letter_spaced_echo(latex):
        if not re.search(r"[=≤≥]|\\frac|\\sqrt|\\times|\\sum", latex):
            return True
        compact = re.sub(r"[\s\\\\_{}()]+", "", latex)
        if eq_id and eq_id.replace("-", "") in compact.replace("-", ""):
            if len(re.sub(r"\s+", "", latex)) < 50:
                return True
    if eq_id:
        compact_alnum = re.sub(r"[^A-Za-z0-9]", "", latex).lower().replace("eq", "")
        eid_alnum = re.sub(r"[^A-Za-z0-9]", "", eq_id).lower()
        if eid_alnum and eid_alnum in compact_alnum:
            extra = compact_alnum.replace(eid_alnum, "", 1)
            if extra in ("", "q", "e") and len(latex) < 80:
                return True
            if not re.search(r"[=≤≥]|\\frac|\\sqrt|\\times|\\sum", latex) and len(latex) < 80:
                return True
        compact = re.sub(r"[\s\\\\_{}()]+", "", latex).lower().replace("eq.", "").replace("eq", "")
        eid = eq_id.lower().replace("-", "")
        if compact.replace("-", "").replace(".", "") == eid.replace(".", "") and len(latex) < 60:
            return True
    return False


def orig_from_context(
    text: str,
    eq_id: Optional[str],
    display: Optional[str],
) -> Optional[str]:
    """Copy the clause BEFORE the id token from section/search/PDF text.

    Never return the id token itself. Never return a letter-spaced latex echo.
    Does not invent math.
    """
    if not text or not eq_id:
        return None
    cleaned = HTML_COMMENT_RE.sub(" ", text)
    variants = _eq_id_search_variants(eq_id, display)
    if not variants:
        return None
    candidates: list[tuple[int, int, str]] = []
    skip_ref = re.compile(
        r"(specified in|in accordance with|see\b|refer(?:red)? to|figure)\s*$",
        re.I,
    )

    def consider(clause: str, extra_score: int = 0) -> None:
        clause = collapse_ws(clause)
        clause = re.sub(r"\(?\s*Eq\.?\s*$", "", clause, flags=re.I).rstrip(" ([")
        if not clause or _is_letter_spaced_echo(clause):
            return
        if orig_is_id_husk(clause, eq_id, display):
            return
        score = extra_score
        if re.search(r"[=≤≥]", clause):
            score += 10
        if re.search(r"[A-Za-z]{2,}\s*=", clause):
            score += 8
        if orig_is_phi_omega_only(clause):
            score -= 40
        if orig_cites_other_eq(clause, eq_id or ""):
            score -= 30
        if re.search(r"\b[RMPVH]n\s*=", clause, re.I):
            score += 16
        if skip_ref.search(clause):
            score -= 20
        if clause.startswith("=") or re.match(r"^=\s*", clause):
            score -= 3
        if len(clause) >= 8:
            score += 1
        candidates.append((score, min(len(clause), 200), clause[:500]))

    lines = cleaned.splitlines()
    for i, line in enumerate(lines):
        if _is_letter_spaced_echo(line):
            continue
        for kind, needle in variants:
            flex = _flex_needle(needle, dropped=(kind == "dropped"))
            m = re.search(flex, line, re.I)
            if not m:
                continue
            before = line[: m.start()]
            before = re.sub(r"\(?\s*Eq\.?\s*$", "", before, flags=re.I).rstrip(" ([")
            clause = collapse_ws(before)
            if (
                not clause
                or orig_is_id_husk(clause, eq_id, display)
                or _is_letter_spaced_echo(clause)
                or clause.startswith("=")
                or len(clause) < 8
            ):
                for j in range(i - 1, max(-1, i - 5), -1):
                    prev = collapse_ws(lines[j])
                    if not prev or _is_letter_spaced_echo(prev):
                        continue
                    if orig_is_id_husk(prev, eq_id, display):
                        continue
                    if orig_is_phi_omega_only(prev):
                        continue
                    if orig_cites_other_eq(prev, eq_id or ""):
                        continue
                    if clause.startswith("=") and prev:
                        clause = collapse_ws(prev + " " + clause)
                    else:
                        clause = prev
                    break
            extra = 2 if kind == "full" else 0
            consider(clause, extra)

    if not candidates:
        blob = cleaned
        for kind, needle in variants:
            flex = _flex_needle(needle, dropped=(kind == "dropped"))
            for m in re.finditer(flex, blob, re.I):
                window = blob[max(0, m.start() - 300) : m.start()]
                if _is_letter_spaced_echo(window[-100:] if len(window) > 100 else window):
                    continue
                last = ""
                for ln in window.splitlines()[::-1]:
                    ln_c = collapse_ws(ln)
                    if ln_c and not _is_letter_spaced_echo(ln_c):
                        last = ln_c
                        break
                consider(last, 2 if kind == "full" else 0)

    if not candidates:
        return None
    candidates.sort(key=lambda x: (-x[0], -x[1]))
    return candidates[0][2]


def parse_search_md_pages(search_md: Path) -> dict[int, str]:
    if not search_md.is_file():
        return {}
    blob = search_md.read_text(encoding="utf-8")
    parts = re.split(r"(?=<!--\s*pdf_page=\d+)", blob)
    out: dict[int, str] = {}
    for part in parts:
        m = re.match(r"<!--\s*pdf_page=(\d+)\b", part)
        if not m:
            continue
        out[int(m.group(1))] = part
    return out

def splice_eq_ids_into_search(
    search_pages: dict[int, str],
    pdf_census: list[dict[str, Any]],
    pages_search_dir: Path,
    search_md: Path,
) -> int:
    """Standard postprocess step: every PDF-text eq id must be FTS-findable.

    Docling's formula model often swallows the '(15.6-1)' tag into LaTeX so
    search.md never contains the token even though equations.json has the id.
    Splice missing display tokens into the page's searchable body (and the
    combined search.md). Does not invent math.
    """
    by_page: dict[int, list[dict[str, Any]]] = {}
    for rec in pdf_census:
        pno = rec.get("pdf_page")
        if pno:
            by_page.setdefault(int(pno), []).append(rec)

    n_spliced = 0
    for pno, recs in by_page.items():
        page_fp = pages_search_dir / f"page_{pno:03d}.md" if pages_search_dir else None
        page_body = ""
        if page_fp and page_fp.is_file():
            page_body = page_fp.read_text(encoding="utf-8")
        block = search_pages.get(pno) or ""
        blob = f"{block}\n{page_body}"
        missing: list[str] = []
        seen_tok: set[str] = set()
        for rec in recs:
            eq = rec.get("eq_id")
            if not eq:
                continue
            token = rec.get("verbatim") or format_eq_display(eq) or f"({eq})"
            token = collapse_ws(token)
            if not token.startswith("("):
                token = f"({eq})"
            if eq in blob or token in blob or f"({eq})" in blob:
                continue
            if token in seen_tok:
                continue
            seen_tok.add(token)
            missing.append(token)
        if not missing:
            continue
        addition = "\n" + " ".join(missing) + "\n"
        if page_fp:
            page_fp.parent.mkdir(parents=True, exist_ok=True)
            page_fp.write_text((page_body.rstrip() + addition), encoding="utf-8")
        if pno in search_pages:
            search_pages[pno] = search_pages[pno].rstrip() + addition
        else:
            search_pages[pno] = addition
        n_spliced += len(missing)

    if n_spliced and search_md:
        parts = [search_pages[k].rstrip() + "\n" for k in sorted(search_pages)]
        search_md.write_text("\n\n".join(parts), encoding="utf-8")
    LOG.info("spliced %s missing PDF eq-id tokens into search markdown", n_spliced)
    return n_spliced



def backfill_equation_husks(
    equations: list[dict[str, Any]],
    search_pages: dict[int, str],
    pdf_pages: list[str],
) -> list[dict[str, Any]]:
    """Fill id-token / empty orig from the section/search/PDF sentence.

    orig is the clause BEFORE the id token, never the id token itself and
    never a letter-spaced latex echo. latex is set null for id-echoes.
    Does not invent math. Does not fall back to eq_id_display.
    """
    n_orig = 0
    n_latex_null = 0
    for e in equations:
        latex = (e.get("latex") or "").strip()
        orig = (e.get("orig") or "").strip()
        echo = latex_is_id_echo(latex, e.get("eq_id"))
        husk = orig_is_id_husk(orig, e.get("eq_id"), e.get("eq_id_display"))
        shifted = orig_is_phi_omega_only(orig) or orig_cites_other_eq(orig, e.get("eq_id") or "")
        needs_orig = husk or shifted
        needs_latex_null = echo
        if not needs_orig and not needs_latex_null and latex:
            continue
        if not needs_orig and not needs_latex_null and not latex:
            e["latex"] = None
            n_latex_null += 1
            continue
        pno = e.get("pdf_page")
        pdf_txt = ""
        search_txt = ""
        if pno and 1 <= int(pno) <= len(pdf_pages):
            pdf_txt = pdf_pages[int(pno) - 1]
        if pno and int(pno) in search_pages:
            search_txt = search_pages[int(pno)]
        nearby = e.get("nearby_text") or ""
        filled = orig_from_context(pdf_txt, e.get("eq_id"), e.get("eq_id_display"))
        if not filled:
            filled = orig_from_context(search_txt, e.get("eq_id"), e.get("eq_id_display"))
        if not filled:
            filled = orig_from_context(nearby, e.get("eq_id"), e.get("eq_id_display"))
        # nearby dump is last-resort only when it is a real math sentence
        if not filled and nearby and not orig_is_id_husk(nearby, e.get("eq_id"), e.get("eq_id_display")):
            filled = collapse_ws(nearby)[:500]
        if needs_orig and filled:
            e["orig"] = filled
            n_orig += 1
        # NEVER set orig to eq_id_display / the id token
        if needs_latex_null:
            e["latex"] = None
            n_latex_null += 1
            issues = list(e.get("issues") or [])
            msg = "id-echo latex cleared; orig backfilled from adjacent verbatim text"
            if msg not in issues:
                issues.append(msg)
            e["issues"] = issues
        elif not latex:
            e["latex"] = None
    LOG.info("eq husk backfill orig=%s latex_cleared=%s", n_orig, n_latex_null)
    return equations


# ---------------------------------------------------------------------------
# Markdown export
# ---------------------------------------------------------------------------


def export_body_markdown(
    loaded: LoadedDoc,
    page_map: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    from docling_core.types.doc.document import ContentLayer, DEFAULT_CONTENT_LAYERS, DoclingDocument

    body_layers = set(DEFAULT_CONTENT_LAYERS) or {ContentLayer.BODY}
    md_dir = loaded.doc_dir / "markdown"
    pages_search = md_dir / "pages_search"
    pages_search.mkdir(parents=True, exist_ok=True)

    parts: list[str] = []
    per_page: dict[int, str] = {}
    n_ok = 0
    errors: list[str] = []

    for cp in loaded.chunk_paths:
        try:
            doc = DoclingDocument.load_from_json(cp)
        except Exception as exc:
            errors.append(f"{cp.name}: load failed: {exc}")
            continue
        # pages in this window
        try:
            page_keys = sorted(int(k) for k in doc.pages.keys())
        except Exception:
            page_keys = []
        recovered_dir = md_dir / "pages_recovered"
        for pno in page_keys:
            recovered = recovered_dir / f"page_{pno:03d}.md"
            if recovered.is_file() and recovered.stat().st_size > 0:
                text = recovered.read_text(encoding="utf-8")
            else:
                try:
                    text = doc.export_to_markdown(
                        page_no=pno,
                        page_break_placeholder=None,
                        included_content_layers=body_layers,
                    )
                except Exception as exc:
                    errors.append(f"page {pno}: {exc}")
                    text = ""
            text = (text or "").rstrip() + "\n"
            if is_asce7_doc(loaded.stem) or is_asce_41_doc(loaded.stem):
                text = strip_asce_search_furniture(text).rstrip() + "\n"
            if is_aisc_358_doc(loaded.stem):
                text = strip_aisc_358_search_furniture(text).rstrip() + "\n"
            if is_aisc_341_doc(loaded.stem):
                text = strip_aisc_341_search_furniture(text).rstrip() + "\n"
            if is_aisi_s100_doc(loaded.stem):
                text = strip_aisi_s100_search_furniture(text).rstrip() + "\n"
            if is_aisi_s240_doc(loaded.stem):
                text = strip_aisi_s240_search_furniture(text).rstrip() + "\n"
            if is_aisi_s230_doc(loaded.stem):
                text = strip_aisi_s230_search_furniture(text).rstrip() + "\n"
            if is_bis_doc(loaded.stem):
                text = strip_bis_search_furniture(text).rstrip() + "\n"
            per_page[pno] = text
            (pages_search / f"page_{pno:03d}.md").write_text(text, encoding="utf-8")
            pm = page_map.get(pno) or {}
            header = (
                f"<!-- pdf_page={pno} printed_label={pm.get('printed_label')!s} "
                f"printed_label_qualified={pm.get('printed_label_qualified')!s} "
                f"part={pm.get('part')} -->"
            )
            parts.append(f"{header}\n\n{text}")
            n_ok += 1

    search_path = md_dir / f"{loaded.stem}.search.md"
    search_path.write_text("\n\n".join(parts), encoding="utf-8")

    # Furniture archive: keep original all-layer markdown if present; also
    # write an explicit .furniture.md copy so retrieval never has to guess.
    orig_md = md_dir / f"{loaded.stem}.md"
    furn_path = md_dir / f"{loaded.stem}.furniture.md"
    if orig_md.is_file() and not furn_path.is_file():
        furn_path.write_text(orig_md.read_text(encoding="utf-8"), encoding="utf-8")
    elif orig_md.is_file() and furn_path.is_file():
        pass
    elif orig_md.is_file():
        furn_path.write_bytes(orig_md.read_bytes())

    copyright_search = 0
    running_search = 0
    if search_path.is_file():
        blob = search_path.read_text(encoding="utf-8")
        copyright_search = blob.count(COPYRIGHT_LINE)
        for rx in RUNNING_TITLE_RES:
            running_search += len(rx.findall(blob))
        if is_aisc_358_doc(loaded.stem):
            for rx in AISC_358_RUNNING_TITLE_RES:
                running_search += len(rx.findall(blob))
        elif is_aisc_341_doc(loaded.stem):
            for rx in AISC_341_RUNNING_TITLE_RES:
                running_search += len(rx.findall(blob))
        elif is_aisc_doc(loaded.stem):
            for rx in AISC_RUNNING_TITLE_RES:
                running_search += len(rx.findall(blob))
        if is_asce7_doc(loaded.stem) or is_asce_41_doc(loaded.stem):
            for rx in ASCE_RUNNING_TITLE_RES + ASCE_WATERMARK_RES:
                running_search += len(rx.findall(blob))
        if is_aisi_s100_doc(loaded.stem):
            for rx in AISI_S100_RUNNING_TITLE_RES:
                running_search += len(rx.findall(blob))
        if is_aisi_s240_doc(loaded.stem):
            for rx in AISI_S240_RUNNING_TITLE_RES:
                running_search += len(rx.findall(blob))
        if is_aisi_s230_doc(loaded.stem):
            for rx in AISI_S230_RUNNING_TITLE_RES:
                running_search += len(rx.findall(blob))

    copyright_orig = 0
    if orig_md.is_file():
        copyright_orig = orig_md.read_text(encoding="utf-8").count(COPYRIGHT_LINE)

    return {
        "search_md": str(search_path),
        "furniture_md": str(furn_path) if furn_path.is_file() else None,
        "pages_search_dir": str(pages_search),
        "pages_exported": n_ok,
        "errors": errors,
        "copyright_hits_search": copyright_search,
        "copyright_hits_original_md": copyright_orig,
        "running_title_hits_search": running_search,
    }


def write_blocks(
    loaded: LoadedDoc,
    page_map: dict[int, dict[str, Any]],
) -> Path:
    out_path = loaded.doc_dir / "structured" / "blocks.jsonl"
    n = 0
    with out_path.open("w", encoding="utf-8") as fh:
        for t in loaded.texts:
            if t.layer != "body":
                continue
            pno = t.pages[0] if t.pages else None
            pm = page_map.get(pno or -1) or {}
            rec = {
                "doc": loaded.stem,
                "pdf_page": pno,
                "printed_label": pm.get("printed_label"),
                "printed_label_qualified": pm.get("printed_label_qualified"),
                "part": pm.get("part") or "standard",
                "label": t.label,
                "content_layer": t.layer,
                "self_ref": t.self_ref,
                "chunk": t.chunk,
                "text": t.text,
            }
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    return out_path


def write_indexes(
    loaded: LoadedDoc,
    page_map: dict[int, dict[str, Any]],
    boundary: dict[str, Any],
    sections: list[dict[str, Any]],
    equations: list[dict[str, Any]],
    tables: list[dict[str, Any]],
    indexes_dir: Path,
    md_stats: dict[str, Any],
) -> dict[str, str]:
    indexes_dir.mkdir(parents=True, exist_ok=True)
    title = None
    edition = None
    # pull a title from page 1 body if possible
    for t in loaded.texts:
        if t.pages == [1] and t.label == "section_header":
            if "North American" in t.text or "STANDARD" in t.text:
                continue
        if 1 in t.pages and "North American Standard" in t.text and t.layer == "body":
            title = collapse_ws(t.text)
            break
    if is_aisc_358_doc(loaded.stem):
        title = title or (
            "ANSI/AISC 358-22 Prequalified Connections for Special and "
            "Intermediate Steel Moment Frames for Seismic Applications"
        )
        edition = "2022"
        standard = "ANSI/AISC 358-22"
        page_scheme = (
            "printed labels 9.2-xxx (roman front matter, then arabic) in both "
            "the provisions and the commentary; PDF page is separate. "
            "Not 16.1-xxx (that is AISC 360)."
        )
    elif is_aisc_341_doc(loaded.stem):
        title = title or (
            "ANSI/AISC 341-22 Seismic Provisions for Structural Steel Buildings"
        )
        edition = "2022"
        standard = "ANSI/AISC 341-22"
        page_scheme = (
            "printed labels 9.1-xxx (roman front matter, then arabic) in both "
            "the provisions and the commentary; PDF page is separate. "
            "Not 16.1-xxx (AISC 360) or 9.2-xxx (AISC 358)."
        )
    elif is_aisc_342_doc(loaded.stem):
        title = title or (
            "ANSI/AISC 342-22 Seismic Provisions for Evaluation and Retrofit "
            "of Existing Structural Steel Buildings"
        )
        edition = "2022"
        standard = "ANSI/AISC 342-22"
        page_scheme = (
            "printed labels plain arabic (roman front matter, then arabic) in "
            "both the provisions and the commentary; PDF page is separate. "
            "Not 16.1-xxx (AISC 360), 9.1-xxx (AISC 341), or 9.2-xxx (AISC 358)."
        )
    elif is_aisc_doc(loaded.stem):
        title = title or "ANSI/AISC 360-22 Specification for Structural Steel Buildings"
        edition = "2022"
        standard = "ANSI/AISC 360-22"
        page_scheme = (
            "printed labels 16.1-xxx (roman front matter, then arabic) in both "
            "the specification and the commentary; PDF page is separate"
        )
    elif is_asce_41_doc(loaded.stem):
        title = title or (
            "ASCE/SEI 41-23 Seismic Evaluation and Retrofit of Existing Buildings"
        )
        edition = "2023"
        standard = "ASCE/SEI 41-23"
        page_scheme = (
            "provisions chapters 1–18 (+ appendices A/B/C) then commentary "
            "chapters C1–C18 (~pdf p.400); printed arabic continues across both "
            "halves; PDF page is separate"
        )
    elif is_asce7_doc(loaded.stem):
        title = title or "ASCE/SEI 7-22 Minimum Design Loads and Associated Criteria for Buildings and Other Structures"
        edition = "2022"
        standard = "ASCE/SEI 7-22"
        page_scheme = (
            "provisions chapters 1–32 then commentary chapters C1/C11; "
            "printed arabic continues across both halves; PDF page is separate"
        )
    elif is_aisi_s100_doc(loaded.stem):
        title = title or (
            "North American Specification for the Design of Cold-Formed Steel "
            "Structural Members, 2016 Edition (Reaffirmed 2020) With Supplement 3"
        )
        edition = "2016 (R2020) w/S3-22"
        standard = "AISI S100-16 (2020) w/S3-22"
        page_scheme = (
            "standard: roman front matter then arabic (pdf 62 = printed 1); "
            "appendices 1-1 / A-3 / B-2; commentary roman then arabic "
            "(cover pdf 251, printed 1 = pdf 263, qualified C-1). "
            "S1/S2/S3 incorporated in-place; no revision bars."
        )
    elif is_aisi_s240_doc(loaded.stem):
        title = title or (
            "AISI S240-20 North American Standard for Cold-Formed Steel "
            "Structural Framing"
        )
        edition = "2020"
        standard = "AISI S240-20"
        page_scheme = (
            "standard: roman front matter then arabic (pdf 28 = printed 1); "
            "commentary: roman then arabic (cover pdf 128 AISI S240-20-C, "
            "printed 1 = pdf 136, qualified C-1). Not 16.1-xxx / 9.1-xxx / "
            "S100 1-1. Chapter C is INSTALLATION in both halves."
        )
    elif is_aisi_s230_doc(loaded.stem):
        title = title or (
            "AISI S230-19 Standard for Cold-Formed Steel Framing — "
            "Prescriptive Method for One- and Two-Family Dwellings"
        )
        edition = "2019"
        standard = "AISI S230-19"
        page_scheme = (
            "standard: roman front matter then arabic (pdf ~20 = printed 1); "
            "commentary: roman then arabic (cover pdf 227 AISI S230-19-C, "
            "printed 1 = pdf ~235, qualified C-1). Chapter letters A–G in "
            "both halves — not ASCE C1/C11."
        )
    elif is_bis_doc(loaded.stem):
        # Derive a readable title from the stem; never fall through to AISI S400.
        stem = loaded.stem
        pretty = stem.replace("_", " ")
        title = title or pretty
        # Edition token: trailing year in stem if present
        import re as _re
        ym = _re.search(r"(19|20)\d{2}", stem)
        edition = ym.group(0) if ym else ""
        standard = pretty
        page_scheme = (
            "BIS / Indian Standard: single volume (no separate commentary). "
            "Printed labels typically roman front matter then arabic body; "
            "PDF page is authoritative. Jurisdiction india/is_bis."
        )
    else:
        title = title or "AISI S400-20 North American Standard for Seismic Design of Cold-Formed Steel Structural Systems"
        edition = "2020"
        standard = "AISI S400-20" if "S400" in loaded.stem else loaded.stem
        page_scheme = (
            "standard: roman front matter then arabic 1–71; "
            "commentary: roman front matter then arabic 1–… (qualified C-1 etc.)"
        )

    doc_rec = {
        "id": loaded.stem,
        "title": title,
        "edition": edition,
        "standard": standard,
        "file": loaded.source_pdf.name if loaded.source_pdf else None,
        "source_pdf": str(loaded.source_pdf) if loaded.source_pdf else None,
        "pdf_pages_total": loaded.meta.get("pdf_pages_total"),
        "page_label_scheme": page_scheme,
        "commentary_boundary": boundary,
        "part_values": (
            ["standard"] if is_bis_doc(loaded.stem) else ["standard", "commentary"]
        ),
        "searchable_markdown": md_stats.get("search_md"),
        "furniture_markdown": md_stats.get("furniture_md"),
    }
    if is_aisi_s100_doc(loaded.stem):
        doc_rec["supplements"] = {
            "incorporated": ["S1", "S2", "S3"],
            "printing": "5th Printing – September 2022",
            "form": "as-amended",
            "revision_marks": False,
            "separate_supplement_pages": False,
            "note": (
                "S1/S2/S3 incorporated in-place. No base-vs-replacement "
                "parallel text. Index points at governing (S3) text."
            ),
        }
        for rec in sections:
            rec.setdefault("status", "current")
            rec.setdefault("governing", True)
        for rec in equations:
            rec.setdefault("status", "current")
            rec.setdefault("governing", True)
        for rec in tables:
            rec.setdefault("status", "current")
            rec.setdefault("governing", True)

    def merge_list(path: Path, key: str, records: list[dict[str, Any]]) -> None:
        existing: list[dict[str, Any]] = []
        if path.is_file():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(existing, list):
                    existing = []
            except Exception:
                existing = []
        kept = [r for r in existing if r.get(key) != loaded.stem and r.get("doc") != loaded.stem]
        path.write_text(
            json.dumps(kept + records, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    docs_path = indexes_dir / "documents.json"
    merge_list(docs_path, "id", [doc_rec])
    sections_path = indexes_dir / "sections.json"
    merge_list(sections_path, "doc", sections)
    eq_index = []
    for e in equations:
        row = {
            "doc": loaded.stem,
            "eq_id": e.get("eq_id"),
            "eq_id_display": e.get("eq_id_display"),
            "section": e.get("section_hint"),
            "part": e.get("part"),
            "pdf_page": e.get("pdf_page"),
            "printed_label": e.get("printed_label"),
            "printed_label_qualified": e.get("printed_label_qualified"),
            "latex": e.get("latex"),
            "orig": e.get("orig"),
            "nearby_text": e.get("nearby_text"),
            "issues": e.get("issues") or [],
            "source": e.get("source"),
        }
        if is_aisi_s100_doc(loaded.stem):
            row["status"] = e.get("status") or "current"
            row["governing"] = e.get("governing", True)
        eq_index.append(row)
    equations_path = indexes_dir / "equations.json"
    merge_list(equations_path, "doc", eq_index)
    tables_path = indexes_dir / "tables.json"
    merge_list(tables_path, "doc", tables)

    # also copy lite indexes next to the document for one-doc use
    local = loaded.doc_dir / "indexes"
    local.mkdir(parents=True, exist_ok=True)
    (local / "documents.json").write_text(
        json.dumps([doc_rec], indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (local / "sections.json").write_text(
        json.dumps(sections, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (local / "equations.json").write_text(
        json.dumps(eq_index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (local / "tables.json").write_text(
        json.dumps(tables, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return {
        "documents": str(docs_path),
        "sections": str(sections_path),
        "equations": str(equations_path),
        "tables": str(tables_path),
        "local_indexes": str(local),
    }


def update_equation_files(loaded: LoadedDoc, equations: list[dict[str, Any]]) -> None:
    eq_dir = loaded.doc_dir / "equations"
    eq_dir.mkdir(parents=True, exist_ok=True)
    all_path = eq_dir / f"{loaded.stem}_all_equations.json"
    # preserve original formula entries and attach recovered ids
    original: list[dict[str, Any]] = []
    if all_path.is_file():
        try:
            original = json.loads(all_path.read_text(encoding="utf-8"))
        except Exception:
            original = []
    formula_eqs = [e for e in equations if e.get("self_ref")]
    # zip by order as a fallback
    by_ref: dict[str, dict[str, Any]] = {}
    for e in formula_eqs:
        if e.get("self_ref"):
            by_ref.setdefault(f"{e.get('chunk')}|{e['self_ref']}|{e.get('pdf_page')}", e)
    # Original all_equations.json has window-local index/self_ref and pages.
    # Attach by (pages[0], self_ref) then by sequential formula order.
    seq = [e for e in formula_eqs]
    attached: list[dict[str, Any]] = []
    si = 0
    for rec in original:
        pages = rec.get("pages") or []
        pno = pages[0] if pages else None
        ref = rec.get("self_ref")
        match = None
        for e in seq:
            if e.get("pdf_page") == pno and e.get("self_ref") == ref and not e.get("_used"):
                match = e
                e["_used"] = True
                break
        if match is None and si < len(seq):
            match = seq[si]
            si += 1
        else:
            si += 1
        new = dict(rec)
        if match:
            new["eq_id"] = match.get("eq_id")
            new["eq_id_display"] = match.get("eq_id_display")
            new["nearby_text"] = match.get("nearby_text")
            new["issues"] = match.get("issues") or []
            new["part"] = match.get("part")
            new["pdf_page"] = match.get("pdf_page")
            new["printed_label"] = match.get("printed_label")
            new["printed_label_qualified"] = match.get("printed_label_qualified")
        attached.append(new)
    all_path.write_text(json.dumps(attached, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    issues = [
        {
            "eq_id": e.get("eq_id"),
            "pdf_page": e.get("pdf_page"),
            "issues": e.get("issues"),
            "latex": e.get("latex"),
            "orig": e.get("orig"),
        }
        for e in equations
        if e.get("issues")
    ]
    (eq_dir / "equation_issues.json").write_text(
        json.dumps(issues, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------



def _resolve_search_md(doc_dir: Path, stem: str) -> Path:
    for cand in (
        doc_dir / "markdown" / f"{stem}.search.md",
        doc_dir / "complete" / f"{stem}.search.md",
        doc_dir / f"{stem}.search.md",
    ):
        if cand.is_file():
            return cand
    return doc_dir / "markdown" / f"{stem}.search.md"


def _load_local_equations(doc_dir: Path, stem: str) -> list[dict[str, Any]]:
    for cand in (
        doc_dir / "indexes" / "equations.json",
        doc_dir / "complete" / f"{stem}.equations.json",
        doc_dir / "complete" / "equations.json",
    ):
        if cand.is_file():
            data = json.loads(cand.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    return []


def write_complete_pack(
    doc_dir: Path,
    stem: str,
    *,
    search_md: Optional[Path] = None,
    documents: Optional[list] = None,
    sections: Optional[list] = None,
    equations: Optional[list] = None,
    tables: Optional[list] = None,
    validate_report: Optional[Path] = None,
    test_report: Optional[Path] = None,
) -> Path:
    """Handoff dir: search.md, json indexes, tables.zip, TEST_REPORT, validate_report."""
    complete = doc_dir / "complete"
    complete.mkdir(parents=True, exist_ok=True)
    local = doc_dir / "indexes"
    if documents is None and (local / "documents.json").is_file():
        documents = json.loads((local / "documents.json").read_text(encoding="utf-8"))
    if sections is None and (local / "sections.json").is_file():
        sections = json.loads((local / "sections.json").read_text(encoding="utf-8"))
    if equations is None and (local / "equations.json").is_file():
        equations = json.loads((local / "equations.json").read_text(encoding="utf-8"))
    if tables is None and (local / "tables.json").is_file():
        tables = json.loads((local / "tables.json").read_text(encoding="utf-8"))
    if search_md is None:
        search_md = _resolve_search_md(doc_dir, stem)
    if search_md.is_file():
        (complete / f"{stem}.search.md").write_bytes(search_md.read_bytes())
    if documents is not None:
        (complete / f"{stem}.documents.json").write_text(
            json.dumps(documents, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    if sections is not None:
        (complete / f"{stem}.sections.json").write_text(
            json.dumps(sections, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    if equations is not None:
        (complete / f"{stem}.equations.json").write_text(
            json.dumps(equations, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        # S400 complete historically used unprefixed names too
        (complete / "equations.json").write_text(
            json.dumps(equations, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    if tables is not None:
        (complete / f"{stem}.tables.json").write_text(
            json.dumps(tables, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (complete / "tables.json").write_text(
            json.dumps(tables, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    tables_dir = doc_dir / "tables"
    zip_path = complete / f"{stem}.tables.zip"
    if tables_dir.is_dir():
        import zipfile
        md_files = sorted(tables_dir.glob("*.md"))
        if md_files:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for fp in md_files:
                    zf.write(fp, fp.name)
    vr = validate_report or (doc_dir / "validate_report.json")
    if vr.is_file():
        (complete / "validate_report.json").write_bytes(vr.read_bytes())
    if test_report and test_report.is_file():
        (complete / test_report.name).write_bytes(test_report.read_bytes())
    return complete


def run_husk_backfill_existing(
    doc_dir: Path,
    indexes_dir: Optional[Path] = None,
    pdf: Optional[Path] = None,
) -> dict[str, Any]:
    """Postprocess path when Docling chunks are absent (S400 indexes-lite rebuild).

    Re-runs only equation husk backfill against existing search.md + PDF text.
    Does not reconvert, does not rewrite sections/tables.
    """
    meta = load_convert_meta(doc_dir)
    stem = meta.get("stem") or doc_dir.name
    src = pdf or (Path(meta["source_pdf"]) if meta.get("source_pdf") else None)
    equations = _load_local_equations(doc_dir, stem)
    if not equations:
        raise FileNotFoundError(f"No equations.json under {doc_dir}")
    search_md = _resolve_search_md(doc_dir, stem)
    search_pages = parse_search_md_pages(search_md)
    # also ingest per-page search files if present
    pages_search = doc_dir / "markdown" / "pages_search"
    if pages_search.is_dir():
        for fp in pages_search.glob("page_*.md"):
            m = re.match(r"page_(\d+)\.md", fp.name)
            if not m:
                continue
            pno = int(m.group(1))
            search_pages[pno] = (search_pages.get(pno) or "") + "\n" + fp.read_text(encoding="utf-8")
    pdf_pages: list[str] = []
    if src and Path(src).is_file():
        pdf_pages = pdf_text_pages(Path(src))
    n_before = sum(
        1
        for e in equations
        if orig_is_id_husk(e.get("orig") or "", e.get("eq_id"), e.get("eq_id_display"))
    )
    equations = backfill_equation_husks(equations, search_pages, pdf_pages)
    n_after = sum(
        1
        for e in equations
        if orig_is_id_husk(e.get("orig") or "", e.get("eq_id"), e.get("eq_id_display"))
    )
    LOG.info("husk-only backfill %s before=%s after=%s", stem, n_before, n_after)

    if indexes_dir is None:
        indexes_dir = _default_indexes_lite(doc_dir)
    local = doc_dir / "indexes"
    local.mkdir(parents=True, exist_ok=True)
    (local / "equations.json").write_text(
        json.dumps(equations, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    # merge into indexes-lite
    lite_eq = indexes_dir / "equations.json"
    existing: list[dict[str, Any]] = []
    if lite_eq.is_file():
        try:
            existing = json.loads(lite_eq.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []
    kept = [r for r in existing if r.get("doc") != stem]
    lite_eq.write_text(
        json.dumps(kept + equations, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_complete_pack(doc_dir, stem, search_md=search_md, equations=equations)
    result = {
        "stem": stem,
        "mode": "husk_backfill_existing",
        "husk_orig_before": n_before,
        "husk_orig_after": n_after,
        "equations": len(equations),
        "source_pdf": str(src) if src else None,
    }
    (doc_dir / "postprocess_meta.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return result


def run(
    doc_dir: Path,
    profile_name: str = "auto",
    indexes_dir: Optional[Path] = None,
    pdf: Optional[Path] = None,
) -> dict[str, Any]:
    chunks_dir = doc_dir / "structured" / "chunks"
    if not chunks_dir.is_dir() or not any(chunks_dir.glob("*.json")):
        LOG.info("No Docling chunks in %s; husk-backfill existing indexes", doc_dir)
        return run_husk_backfill_existing(doc_dir, indexes_dir=indexes_dir, pdf=pdf)
    loaded = load_chunks(doc_dir)
    if pdf:
        loaded.source_pdf = pdf
    profile = profile_for_stem(loaded.stem, profile_name)
    LOG.info("Post-processing %s profile=%s chunks=%s", loaded.stem, profile.name, len(loaded.chunk_paths))

    furn = furniture_by_page(loaded)
    boundary = detect_commentary(loaded, furn, profile)
    page_map = finalize_page_map(furn, boundary)
    LOG.info(
        "Commentary boundary cover=%s body=%s",
        boundary.get("cover_pdf_page"),
        boundary.get("body_pdf_page"),
    )

    sections = build_sections(loaded, page_map)
    section_at = section_at_page_map(sections)

    pdf_census: list[dict[str, Any]] = []
    if loaded.source_pdf and loaded.source_pdf.is_file():
        pdf_census = census_pdf_eq_ids(loaded.source_pdf, page_map, section_at)
        LOG.info("PDF Eq. census: %s unique page-hits", len(pdf_census))
    else:
        LOG.warning("Source PDF not found; equation census skipped")

    equations = recover_equation_ids(loaded, page_map, section_at, pdf_census)
    tables = build_tables(loaded, page_map, section_at)
    md_stats = export_body_markdown(loaded, page_map)
    pages_search_dir = Path(
        md_stats.get("pages_search_dir")
        or (loaded.doc_dir / "markdown" / "pages_search")
    )
    if is_bis_doc(loaded.stem):
        n_before = len(sections)
        sections = recover_bis_sections_from_pages_search(
            loaded, page_map, sections, pages_search_dir
        )
        sections = drop_children_before_parent_root(sections)
        sections = _relink_section_children(sections)
        if len(sections) != n_before:
            LOG.info(
                "BIS section recovery %s: %s -> %s",
                loaded.stem,
                n_before,
                len(sections),
            )
            section_at = section_at_page_map(sections)
            # Rebuild tables so section hints use recovered ids
            tables = build_tables(loaded, page_map, section_at)
    search_pages = parse_search_md_pages(Path(md_stats.get("search_md") or ""))
    pdf_page_texts: list[str] = []
    if loaded.source_pdf and loaded.source_pdf.is_file():
        pdf_page_texts = pdf_text_pages(loaded.source_pdf)
    # Every PDF-text-layer eq id must exist in equations.json (recover_equation_ids
    # already backfills unmatched census rows as source=pdf_text) AND the display
    # token must be in searchable markdown (formula model often swallows it).
    pages_search_dir = Path(md_stats.get("pages_search_dir") or (loaded.doc_dir / "markdown" / "pages_search"))
    n_splice = splice_eq_ids_into_search(
        search_pages,
        pdf_census,
        pages_search_dir,
        Path(md_stats.get("search_md") or ""),
    )
    md_stats["eq_id_tokens_spliced"] = n_splice
    search_pages = parse_search_md_pages(Path(md_stats.get("search_md") or ""))
    equations = backfill_equation_husks(equations, search_pages, pdf_page_texts)
    blocks_path = write_blocks(loaded, page_map)
    update_equation_files(loaded, equations)

    if indexes_dir is None:
        indexes_dir = _default_indexes_lite(doc_dir)
    index_paths = write_indexes(
        loaded, page_map, boundary, sections, equations, tables, indexes_dir, md_stats
    )

    (doc_dir / "structured" / "furniture_by_page.json").write_text(
        json.dumps(furn, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (doc_dir / "structured" / "page_map.json").write_text(
        json.dumps({str(k): v for k, v in page_map.items()}, indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )

    recovered = sum(1 for e in equations if e.get("eq_id") and e.get("source") != "pdf_text")
    pdf_only = sum(1 for e in equations if e.get("source") == "pdf_text")
    unidentified = sum(1 for e in equations if not e.get("eq_id"))
    unique_ids = sorted({e["eq_id"] for e in equations if e.get("eq_id")})

    result = {
        "stem": loaded.stem,
        "profile": profile.name,
        "commentary_boundary": boundary,
        "markdown": md_stats,
        "indexes": index_paths,
        "blocks": str(blocks_path),
        "counts": {
            "sections": len(sections),
            "tables": len(tables),
            "formulas_docling": len(loaded.formulas),
            "pdf_eq_census": len(pdf_census),
            "equations_index_rows": len(equations),
            "eq_ids_recovered_on_formulas": recovered,
            "eq_ids_pdf_only": pdf_only,
            "formulas_unidentified": unidentified,
            "unique_eq_ids": len(unique_ids),
            "pages": len(page_map),
        },
        "copyright_hits_searchable": md_stats.get("copyright_hits_search"),
        "copyright_hits_original_md": md_stats.get("copyright_hits_original_md"),
        "finished_utc": _now_iso(),
    }
    meta_path = doc_dir / "postprocess_meta.json"
    meta_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    LOG.info("Wrote %s", meta_path)
    return result


# ---------------------------------------------------------------------------
# BIS index repair (CORPUS-06 / -09 / -14 / -15): rebuild per-doc sections,
# tables and equations indexes from the PDF text layer (both layout columns)
# plus the served page text, WITHOUT touching any text artefact.
# ---------------------------------------------------------------------------
def _default_indexes_lite(doc_dir: Path) -> Path:
    """<root>/indexes-lite for a document at <root>/documents/standards/<STEM>."""
    doc_dir = Path(doc_dir).resolve()
    if doc_dir.parent.name == "standards" and doc_dir.parent.parent.name == "documents":
        return doc_dir.parent.parent.parent / "indexes-lite"
    return doc_dir / "indexes-lite"


def _local_pdf_dirs() -> list[Path]:
    from bis_text import pdf_dirs
    return pdf_dirs()


def locate_source_pdf(doc_rec: dict[str, Any], stem: str) -> Optional[Path]:
    names = [doc_rec.get("file"), Path(doc_rec.get("source_pdf") or "").name, f"{stem}.pdf"]
    cands = []
    if doc_rec.get("source_pdf"):
        cands.append(Path(doc_rec["source_pdf"]))
    for d in _local_pdf_dirs():
        for n in names:
            if n:
                cands.append(d / n)
    for c in cands:
        if c.is_file():
            return c
    return None


def served_pages(doc_dir: Path, stem: str) -> tuple[dict[int, str], dict[int, Optional[str]]]:
    """pdf page -> served text (search.md, filled/extended by pages_search), and printed labels."""
    from bis_text import strip_watermark

    pages: dict[int, str] = {}
    labels: dict[int, Optional[str]] = {}
    smd = doc_dir / "markdown" / f"{stem}.search.md"
    if smd.is_file():
        for pno, part in parse_search_md_pages(smd).items():
            m = re.search(r"printed_label=(\S+)", part)
            if m and m.group(1) not in ("None", "none"):
                labels[pno] = m.group(1)
            pages[pno] = strip_watermark(re.sub(r"<!--.*?-->", "", part, flags=re.S))
    ps = doc_dir / "markdown" / "pages_search"
    if ps.is_dir():
        for fp in ps.glob("page_*.md"):
            m = re.search(r"page_(\d+)", fp.name)
            if not m:
                continue
            body = strip_watermark(re.sub(r"<!--.*?-->", "", fp.read_text(encoding="utf-8"), flags=re.S))
            pno = int(m.group(1))
            if pno not in pages or len(body) > len(pages[pno]):
                pages[pno] = body
    return pages, labels


def _load_manual_sections() -> dict[str, list[dict[str, Any]]]:
    p = Path(__file__).resolve().parent / "bis_manual_sections.json"
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def _bad_latex(latex: str) -> bool:
    s = latex or ""
    if len(s) > 1500 and len(set(s.split())) < 40:
        return True
    if re.search(r"(\b[cl] ){40,}", s):
        return True
    if s.count("G6") + s.count("G4") > 10:
        return True
    return False


def repair_bis_doc_indexes(doc_dir: Path, *, pdf: Optional[Path] = None, write: bool = True) -> dict[str, Any]:
    """Rebuild <doc>/indexes/{sections,tables,equations}.json for a BIS stem."""
    from bis_text import (
        parse_bis_captions,
        parse_bis_headings,
        pdf_layout_pages,
        strip_watermark,
    )

    stem = doc_dir.name
    idx = doc_dir / "indexes"
    idx.mkdir(parents=True, exist_ok=True)

    def _load(name: str) -> Any:
        p = idx / name
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []

    docs = _load("documents.json")
    doc0 = (docs[0] if isinstance(docs, list) and docs else docs) or {"id": stem}
    old_secs = _load("sections.json")
    old_tbls = _load("tables.json")
    old_eqs = _load("equations.json")
    pdf = pdf or locate_source_pdf(doc0, stem)
    layout = list(pdf_layout_pages(str(pdf))) if pdf else []
    served, labels = served_pages(doc_dir, stem)
    amd_pages = set(doc0.get("amendment_pages") or [])

    # ---- sections ----
    pdf_heads = parse_bis_headings(list(enumerate(layout, 1))) if layout else []
    pdf_max_top = max((int(h["section_id"].split(".")[0]) for h in pdf_heads), default=None)
    served_heads = parse_bis_headings(sorted(served.items()), max_top=pdf_max_top)
    merged: dict[str, dict[str, Any]] = {}
    for h in pdf_heads:
        merged.setdefault(h["section_id"], h)
    for h in served_heads:
        cur = merged.get(h["section_id"])
        if cur is not None and cur.get("pdf_page") in amd_pages and h.get("pdf_page") not in amd_pages:
            # consolidated amendment text on the base page wins over the amendment sheet
            h = dict(h)
            h["source"] = "served_text (amendment consolidated)"
            h["amendment_sheet_page"] = cur.get("pdf_page")
            merged[h["section_id"]] = h
            continue
        if h["section_id"] not in merged:
            h = dict(h)
            h["source"] = "served_text"
            merged[h["section_id"]] = h
    for h in (_load_manual_sections().get(stem) or []):
        rec = dict(h)
        rec.setdefault("source", "manual (verified against PDF page image)")
        merged[rec["section_id"]] = rec
    dropped: list[str] = []
    valid_tops = {int(s.split(".")[0]) for s in merged}
    for s in old_secs:
        sid = str(s.get("section_id") or "")
        if sid in merged:
            continue
        ok = bool(re.fullmatch(r"\d{1,2}(\.\d{1,3}){0,5}", sid))
        if ok and "." not in sid:
            ok = int(sid) in valid_tops and int(sid) != 0
        elif ok:
            parts = [int(x) for x in sid.split(".")]
            parent = ".".join(sid.split(".")[:-1])
            ok = parts[0] in valid_tops and parts[0] != 0 and (parent in merged) and parts[-1] <= 40
        if not ok:
            dropped.append(sid)
            continue
        merged[sid] = {"section_id": sid, "title": s.get("title") or "", "pdf_page": s.get("pdf_page"),
                       "source": s.get("source") or "docling"}
    order = sorted(merged.values(), key=lambda r: (int(r.get("pdf_page") or 0), r.get("line") or 0))
    sections: list[dict[str, Any]] = []
    for r in order:
        sid = r["section_id"]
        pno = r.get("pdf_page")
        rec = {
            "doc": stem,
            "section_id": sid,
            "title": strip_watermark(r.get("title") or "")[:200],
            "part": "standard",
            "pdf_page": pno,
            "printed_label": labels.get(pno) if pno else None,
            "printed_label_qualified": None,
            "parent": parent_section_id(sid),
            "self_ref": None,
            "chunk": None,
            "synthetic": True,
            "source": r.get("source") or "pdf_layout",
            "children": [],
        }
        if pno in amd_pages:
            rec["amendment_sheet"] = True
        if r.get("amendment_sheet_page"):
            rec["amendment_sheet_page"] = r["amendment_sheet_page"]
        sections.append(rec)
    sections = _relink_section_children(sections)
    sec_by_page: list[tuple[int, str]] = sorted(
        ((int(s["pdf_page"]), s["section_id"]) for s in sections if s.get("pdf_page")),
    )

    def section_at(pno: Optional[int]) -> Optional[str]:
        best = None
        for p, sid in sec_by_page:
            if pno is not None and p <= pno:
                best = sid
        return best

    # ---- tables ----
    caps = parse_bis_captions(list(enumerate(layout, 1))) if layout else []
    cap_keys = {(c["table_id"], c["pdf_page"]) for c in caps}
    for c in parse_bis_captions(sorted(served.items())):
        if (c["table_id"], c["pdf_page"]) not in cap_keys:
            c = dict(c)
            c["source"] = "served_text"
            caps.append(c)
            cap_keys.add((c["table_id"], c["pdf_page"]))
    # CORPUS-A manual/pdftotext recoveries (tables/recovered_index.json) are
    # attached to the caption-verified record with the same (table_id, page)
    recovered_by_key: dict[tuple[str, int], dict[str, Any]] = {}
    rpath = doc_dir / "tables" / "recovered_index.json"
    if rpath.is_file():
        try:
            rdata = json.loads(rpath.read_text(encoding="utf-8"))
        except Exception:
            rdata = []
        rlist = rdata if isinstance(rdata, list) else list((rdata.get("tables") if isinstance(rdata, dict) and isinstance(rdata.get("tables"), list) else rdata.values()) if isinstance(rdata, dict) else [])
        for r in rlist:
            if isinstance(r, dict) and r.get("table_id") and r.get("pdf_page") is not None:
                recovered_by_key[(str(r["table_id"]).replace(" ", ""), int(r["pdf_page"]))] = r
    by_page: dict[int, list[dict[str, Any]]] = {}
    for t in old_tbls:
        if t.get("structured_row"):
            continue
        by_page.setdefault(int(t.get("pdf_page") or 0), []).append(t)
    tables: list[dict[str, Any]] = []
    used: set[int] = set()
    for i, c in enumerate(sorted(caps, key=lambda c: (c["pdf_page"], c.get("line") or 0))):
        pno = c["pdf_page"]
        grid = None
        cands = [t for t in by_page.get(pno, []) if id(t) not in used]
        same = [t for t in cands if str(t.get("table_id") or "").replace(" ", "") == c["table_id"]
                and not t.get("continuation_of_page")]
        pool = same or [t for t in cands if not t.get("table_id")]
        if pool:
            grid = max(pool, key=lambda t: int(t.get("num_rows") or 0))
            used.add(id(grid))
        rrec = recovered_by_key.get((c["table_id"], pno))
        if rrec is not None:
            mdp = rrec.get("md") or rrec.get("path") or rrec.get("markdown_path")
            mdabs = (doc_dir / mdp) if mdp and not Path(mdp).is_absolute() else (Path(mdp) if mdp else None)
            if mdabs is not None and mdabs.is_file():
                grid = dict(grid or {})
                root_dir = doc_dir.parents[2]
                try:
                    grid["md"] = str(mdabs.resolve().relative_to(root_dir.resolve()))
                except ValueError:
                    grid["md"] = str(mdabs)
                if rrec.get("csv"):
                    grid["csv"] = f"documents/standards/{stem}/{rrec['csv']}"
                grid["num_rows"] = rrec.get("num_rows") or grid.get("num_rows")
                grid["provenance"] = rrec.get("provenance") or rrec.get("method") or "recovered"
        rec = {
            "doc": stem,
            "table_id": c["table_id"],
            "provenance": (grid or {}).get("provenance"),
            "title": strip_watermark(c["title"]),
            "section": section_at(pno),
            "part": "standard",
            "pdf_pages": [pno],
            "pdf_page": pno,
            "printed_label": labels.get(pno),
            "printed_label_qualified": None,
            "num_rows": (grid or {}).get("num_rows"),
            "num_cols": (grid or {}).get("num_cols"),
            "md": (grid or {}).get("md"),
            "csv": (grid or {}).get("csv"),
            "json": (grid or {}).get("json"),
            "markdown_excerpt": strip_watermark((grid or {}).get("markdown_excerpt") or ""),
            "index": i + 1,
            "caption_verified": True,
            "continued": bool(c.get("continued")),
            "clause_ref": c.get("clause_ref"),
            "source": c.get("source") or "pdf_layout_caption",
        }
        tables.append(rec)
    n_demoted = 0
    cap_pages = sorted({c["pdf_page"] for c in caps})
    grid_pages = {int(t.get("pdf_page") or 0) for t in old_tbls if t.get("num_rows")}

    def continuation_of(pno: int) -> Optional[dict[str, Any]]:
        """Caption of a multi-page table this caption-less grid page continues."""
        prev = [cp for cp in cap_pages if cp < pno]
        if not prev or pno in cap_pages:
            return None
        cp = prev[-1]
        if any(q not in grid_pages for q in range(cp + 1, pno + 1)):
            return None
        last = [c for c in caps if c["pdf_page"] == cp]
        return max(last, key=lambda c: c.get("line") or 0) if last else None

    for t in old_tbls:
        if id(t) in used or t.get("structured_row"):
            continue
        rec = dict(t)
        cont = continuation_of(int(rec.get("pdf_page") or 0)) if rec.get("num_rows") else None
        if cont is not None:
            rec["table_id"] = cont["table_id"]
            rec["title"] = strip_watermark(cont["title"]) + " (continued)"
            rec["continued"] = True
            rec["caption_verified"] = False
            rec["continuation_of_page"] = cont["pdf_page"]
            rec["markdown_excerpt"] = strip_watermark(rec.get("markdown_excerpt") or "")
            rec["section"] = section_at(rec.get("pdf_page"))
            tables.append(rec)
            continue
        if rec.get("table_id"):
            rec["demoted_table_id"] = rec["table_id"]
            n_demoted += 1
        rec["table_id"] = None
        rec["caption_verified"] = False
        rec["markdown_excerpt"] = strip_watermark(rec.get("markdown_excerpt") or "")
        rec["section"] = section_at(rec.get("pdf_page"))
        tables.append(rec)

    # structured section-property rows (CORPUS-09)
    sc = doc_dir / "structured" / "sections.csv"
    if sc.is_file():
        import csv as _csv

        # the OCR/Docling grids of these section tables are not to be served (CORPUS-09)
        for t in tables:
            if t.get("md") or t.get("num_rows"):
                t["superseded"] = True
                t["superseded_by"] = f"documents/standards/{stem}/structured/sections.csv"
        with sc.open(encoding="utf-8") as fh:
            rows = list(_csv.DictReader(fh))
        for j, row in enumerate(rows):
            desig = row.get("designation") or ""
            tid = row.get("table_id") or desig
            cols = [k for k in row.keys() if row.get(k) not in (None, "")]
            md = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n| " + " | ".join(
                str(row[k]) for k in cols) + " |"
            ascii_d = desig.replace("×", "x").replace("∠", "L")
            alt = re.sub(r"\s+", "", ascii_d)
            tables.append({
                "doc": stem,
                "table_id": tid,
                "title": f"{desig} section properties ({ascii_d}; {alt}) IS {stem.split('_')[1]} Table {row.get('table') or '1'}",
                "section": section_at(int(row["pdf_page"])) if row.get("pdf_page") else None,
                "part": "standard",
                "pdf_pages": [int(row["pdf_page"])] if row.get("pdf_page") else [],
                "pdf_page": int(row["pdf_page"]) if row.get("pdf_page") else None,
                "printed_label": None,
                "printed_label_qualified": None,
                "num_rows": 1,
                "num_cols": len(cols),
                "md": None,
                "csv": f"documents/standards/{stem}/structured/sections.csv",
                "json": None,
                "markdown_excerpt": md,
                "index": 10000 + j,
                "structured_row": True,
                "caption_verified": True,
                "check": row.get("check"),
                "source": "structured/sections.csv (PDF text layer, consistency-checked)",
            })

    # ---- equations ----
    equations: list[dict[str, Any]] = []
    counters: dict[str, int] = {}
    for e in old_eqs:
        rec = dict(e)
        for k in ("nearby_text", "orig", "latex"):
            if isinstance(rec.get(k), str):
                rec[k] = strip_watermark(rec[k])
        if rec.get("eq_id_synthetic"):
            rec["eq_id"] = None
        if not rec.get("eq_id"):
            sec = rec.get("section") if rec.get("section") in merged else section_at(rec.get("pdf_page"))
            base = sec or f"p{rec.get('pdf_page')}"
            counters[base] = counters.get(base, 0) + 1
            rec["section"] = sec
            rec["eq_id"] = f"{base}-eq{counters[base]}"
            rec["eq_id_display"] = f"[{rec['eq_id']}]"
            rec["eq_id_synthetic"] = True
        rec["quality"] = "garbled" if _bad_latex(rec.get("latex") or "") else rec.get("quality") or "ok"
        equations.append(rec)

    stats = {
        "stem": stem,
        "pdf": str(pdf) if pdf else None,
        "sections": len(sections),
        "sections_dropped_bogus": dropped,
        "tables_caption_verified": sum(1 for t in tables if t.get("caption_verified") and not t.get("structured_row")),
        "tables_structured_rows": sum(1 for t in tables if t.get("structured_row")),
        "tables_demoted_mentions": n_demoted,
        "equations": len(equations),
        "equations_garbled": sum(1 for e in equations if e.get("quality") == "garbled"),
    }
    if write:
        for name, obj in (("sections.json", sections), ("tables.json", tables), ("equations.json", equations)):
            (idx / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (idx / "repair_stats.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False) + "\n",
                                                encoding="utf-8")
    return stats


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    p = argparse.ArgumentParser(description="Post-process a Docling conversion dir")
    p.add_argument("doc_dir", type=Path)
    p.add_argument(
        "--profile",
        default="auto",
        help="Commentary detector: auto|is_bis|aisi_s400|aisi_s100|aisi_s230|aisi_s240|asce7|asce_41|aisc|aisc_358|aisc_341|aisc_342",
    )
    p.add_argument(
        "--indexes-dir",
        type=Path,
        default=None,
        help="Where to write indexes-lite (default: <engineering_rag>/indexes-lite)",
    )
    p.add_argument("--pdf", type=Path, default=None, help="Override source PDF path")
    p.add_argument(
        "--repair-bis-indexes",
        action="store_true",
        help="Only rebuild indexes/{sections,tables,equations}.json from the PDF text layer "
        "and served pages (no text artefact is modified)",
    )
    args = p.parse_args(argv)
    if args.repair_bis_indexes:
        print(json.dumps(repair_bis_doc_indexes(args.doc_dir.resolve(), pdf=args.pdf), indent=2))
        return 0
    doc_dir = args.doc_dir.resolve()
    if not doc_dir.is_dir():
        LOG.error("Not a directory: %s", doc_dir)
        return 2
    indexes_dir = args.indexes_dir
    if indexes_dir is None:
        indexes_dir = _default_indexes_lite(doc_dir)
    run(doc_dir, profile_name=args.profile, indexes_dir=indexes_dir, pdf=args.pdf)
    return 0


if __name__ == "__main__":
    sys.exit(main())
