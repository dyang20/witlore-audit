"""Pottawattamie County rule set, extracted from the supplied ordinance.

Every rule carries its section number and a verbatim quote. The quotes are
verified against the stored snapshot at load time; one that does not match is
dropped with a warning rather than used. That is what stops a wrong section
number from reaching a report.

Two things the human reports got subtly wrong, visible once the whole ordinance
is read rather than the pasted excerpts:

1. Sec. 8.010.060 establishes NO maximum height for buildings and structures in
   A-2. The 500 ft figure is a use-specific cap on towers from Sec. 8.010.020.06,
   not the district's building height limit. Conflating them produces wrong
   reasoning about what the constraint actually is.

2. Tower height caps are per-district and are a real filter: 500 ft in A-2 and
   A-3, 100 ft in the R-1/R-2/R-3 residential districts, 150 ft in C-1/C-2 with
   taller towers moved to a conditional clause, 200 ft in the industrial
   districts, and A-4 Loess Hills permits towers with no height figure at all,
   as a conditional use.
"""

from __future__ import annotations

from typing import Optional

from ..models import ProcessType, UsePermission, ZoningRule
from .snapshot import Snapshot, verify_quote

JURISDICTION = "Pottawattamie County"

# Cited in the human desktop audits for 35820 and 34659. The filename matches
# the ordinance text supplied by the hiring team, so citations can point at a
# retrievable source rather than an unverifiable local file. Still worth
# confirming nothing has been amended since #2015-05 (2015-12-18).
# The 2026 audit cites a newer path, labelled "CHAPTER 8", and the supplied
# text matches its content (it contains .16-.18, which the 2024 audits omit).
# Both are recorded; the newer one is cited.
ORDINANCE_URL = "https://www.pottcounty-ia.gov/files/county_code/_86947.pdf"
ORDINANCE_URL_2024 = ("https://www.pottcounty-ia.gov/files/county_code/"
                      "zoning_ordinance_39864.pdf")

# The ordinance uses a consistent two-tier structure in every district that
# addresses towers at all: a principal (by-right) use up to a height, and a
# conditional use above it. Both clauses are recorded, because the gap between
# them is the whole question. A district that lists towers in neither list does
# not permit them: this is a permissive-list ordinance.
#
# Every section number and quote below was located by position in the stored
# snapshot, not matched by text alone. An earlier version of this table carried
# invented section numbers for C-2, I-1 and I-2 that passed a substring check
# because identical wording exists elsewhere in the ordinance, which is why
# verification is now section-aware.
#
# district -> (by-right cap ft or None, by-right section, by-right quote,
#              conditional-above section or None, conditional quote or None)
TOWER_USE_BY_DISTRICT: dict[
    str, tuple[Optional[float], Optional[str], Optional[str], Optional[str], Optional[str]]
] = {
    "A-2": (500.0, "8.010.020.06",
            "Tower with a height not exceeding five hundred (500) feet, subject to the",
            "8.010.030.20",
            "Towers with a height exceeding five hundred (500) feet, subject to the requirements"),
    "A-3": (500.0, "8.012.020.03",
            "Towers with a height not exceeding five hundred (500) feet, subject to the",
            "8.012.030.07",
            "Towers with a height exceeding five hundred (500) feet, subject to the requirements"),
    # Loess Hills: conditional at any height, no by-right tier.
    "A-4": (None, None, None, "8.014.030.15",
            "Towers, subject to the requirements of Section 8.004.220."),
    "R-1": (100.0, "8.015.020.09",
            "Towers with a height not exceeding one hundred (100) feet, subject to the",
            "8.015.030.10",
            "Towers with a height exceeding one hundred (100) feet, subject to the requirements"),
    # Note the missing "a": the ordinance's own wording differs here.
    "R-2": (100.0, "8.020.020.09",
            "Towers with height not exceeding one hundred (100) feet, subject to the",
            "8.020.030.10",
            "Towers with a height exceeding one hundred (100) feet, subject to the requirements"),
    "R-3": (100.0, "8.025.020.08",
            "Towers with a height not exceeding one hundred (100) feet, subject to the",
            "8.025.030.04",
            "Towers with a height exceeding one hundred (100) feet, subject to the requirements"),
    # The commercial and industrial districts cap the by-right use and provide
    # NO conditional clause above it, so a taller tower there is unaddressed
    # rather than refused. C-3 is the exception.
    #
    # C-2, I-1 and I-2 number their permitted uses with letters rather than
    # digits ("I. Towers with a height..."), which is why an earlier scan
    # reported them as having no tower clause at all. They do.
    "C-1": (150.0, "8.045.020.09",
            "Towers with a height not exceeding one hundred fifty (150) feet, subject to the",
            None, None),
    "C-2": (150.0, "8.050.020",
            "Towers with a height not exceeding one hundred fifty (150) feet, subject to the",
            None, None),
    "C-3": (150.0, "8.051.020.07",
            "Towers with a height not exceeding one hundred fifty (150) feet, subject to the",
            "8.051.030.06",
            "Towers with a height exceeding one hundred fifty (150) feet, subject to the"),
    "I-1": (200.0, "8.055.020",
            "Towers with a height not exceeding two hundred (200) feet, subject to the",
            None, None),
    "I-2": (200.0, "8.060.020",
            "Towers with a height not exceeding two hundred (200) feet, subject to the",
            None, None),
}

# Districts that exist in the ordinance but address towers in neither their
# principal nor their conditional use list. Verified by reading each chapter's
# use lists in both numbering styles.
NO_TOWER_CLAUSE = {"A-1", "R-5", "R-6"}

# district -> (front, side, rear, abutting-road) in feet
SETBACKS: dict[str, tuple[float, float, float, float]] = {
    "A-2": (50.0, 25.0, 50.0, 75.0),
}

GENERAL_TOWER_SECTION = "8.004.220"

GENERAL_RULES = [
    ("setback_fall_distance", "8.004.220.12",
     "Towers in excess of fifty (50) feet in height shall be set back a distance "
     "equal to the manufacturer's designed fall distance."),
    ("dwelling_distance", "8.004.220.13",
     "All towers shall be located a minimum distance of equal to or greater than "
     "the height of the tower from adjacent dwellings or structures other than "
     "those associated with the tower facility."),
    ("camouflage", "8.004.220.11",
     "All towers and telecommunication facilities shall be of camouflage design "
     "standards."),
    ("fence", "8.004.220.14",
     "a fence six (6) feet in height with a locking portal or an anti-climbing "
     "device is required around the tower base"),
    ("collocation_statement", "8.004.220.06",
     "Written evidence by the applicant in regards to the reasoning for choosing "
     "the proposed location and the reason the applicant did not choose collocation."),
    ("airport_approval", "8.004.220.10",
     "Documentation that the proposed tower site and height have been approved by "
     "the appropriate Airport authority."),
    ("sheriff_interference", "8.004.220.15",
     "No tower shall be constructed in a manner which will interfere with emergency "
     "communication transmissions of the County."),
    ("removal_agreement", "8.004.220.09",
     "A written agreement to remove the tower and/or antenna within one hundred "
     "eighty (180) days after cessation of use"),
    # The 2024 human audits truncate their quotes at .15. These three exist in
    # the ordinance and appear in the 2026 audit, so they are applied here.
    ("records_disclosure", "8.004.220.16",
     "All records, documents, and electronic data in the possession or"),
    ("periodic_inspection", "8.004.220.17",
     "At least every twenty-four (24) months, every tower shall be"),
    ("abandonment", "8.004.220.18",
     "In the event the use of any tower has been discontinued for a"),
]

# Sec. 8.004.220 exempts County emergency-communications towers from these
# standards; irrelevant to a commercial build but recorded so the exemption is
# not mistaken for a loophole.
EXEMPTION_QUOTE = ("Towers and the associated equipment for emergency communications "
                   "or other County systems are exempt from the development standards "
                   "of this Section")


def build_rules(snapshot: Snapshot) -> tuple[list[ZoningRule], list[str]]:
    """Returns (verified rules, warnings about dropped quotes).

    Verification is section-aware: a quote must appear near the section it is
    attributed to, not merely somewhere in the ordinance.
    """
    rules: list[ZoningRule] = []
    warnings: list[str] = []

    def add(kind: str, district: Optional[str], value, units, section, quote,
            notes: Optional[str] = None) -> None:
        if not verify_quote(quote, snapshot, section=section):
            warnings.append(
                f"dropped {kind} rule for {district or 'all districts'}: quote for "
                f"§{section} does not appear at that section in the "
                f"{snapshot.jurisdiction} snapshot")
            return
        rules.append(ZoningRule(
            kind=kind, jurisdiction=JURISDICTION, district=district, value=value,
            units=units, section=section, quote=quote, url=snapshot.url,
            retrieved_at=snapshot.retrieved_at, snapshot_hash=snapshot.sha256,
            notes=notes,
        ))

    for district, (cap, by_sec, by_q, cond_sec, cond_q) in TOWER_USE_BY_DISTRICT.items():
        if by_sec and by_q:
            add("tower_use", district, UsePermission.PERMITTED.value, None,
                by_sec, by_q, notes="principal (by-right) use")
            add("height_cap", district, cap, "ft", by_sec, by_q,
                notes="by-right ceiling; a conditional clause may allow more")
        if cond_sec and cond_q:
            add("tower_use_conditional", district, UsePermission.CONDITIONAL.value,
                None, cond_sec, cond_q,
                notes=("conditional at any height" if cap is None
                       else f"conditional use above {cap:.0f} ft"))

    for district, (front, side, rear, road) in SETBACKS.items():
        add("setback", district,
            {"front": front, "side": side, "rear": rear, "abutting_road": road},
            "ft", "8.010.070",
            "The front yard setback shall be a minimum of fifty (50) feet.")

    for kind, section, quote in GENERAL_RULES:
        add(kind, None, True, None, section, quote)

    add("exemption", None, "county_emergency_communications", None,
        GENERAL_TOWER_SECTION, EXEMPTION_QUOTE,
        notes="Does not apply to a commercial carrier build.")

    return rules, warnings


def evaluate(district: str, height_ft: float) -> tuple[UsePermission, ProcessType, str]:
    """Permission, approval path and the reason, decided together.

    Follows the ordinance's two-tier structure: a principal use up to a height,
    a conditional use above it. Which tier applies is what decides the schedule,
    so both are checked rather than treating the by-right cap as a ceiling.

    This replaces an earlier reading that treated the residential districts as
    prohibiting towers above 100 ft. They do not: §8.015.030.10, §8.020.030.10
    and §8.025.030.04 each make a tower exceeding 100 ft a conditional use. The
    earlier reading wrongly excluded 18 parcels on TI-OPP-43986 that the human
    audit had correctly carried forward.
    """
    d = (district or "").strip().upper()

    if d in NO_TOWER_CLAUSE:
        return (UsePermission.PROHIBITED, ProcessType.TEXT_AMENDMENT,
                f"the {d} district addresses towers in neither its principal nor its "
                f"conditional use list; this is a permissive-list ordinance, so the "
                f"use would have to be added by text amendment")

    entry = TOWER_USE_BY_DISTRICT.get(d)
    if entry is None:
        return (UsePermission.UNKNOWN, ProcessType.UNKNOWN,
                f"district {district} does not appear in the ordinance's district "
                f"chapters, so no tower clause could be located for it")

    cap, by_right_sec, _q1, cond_sec, _q2 = entry

    # No by-right tier: conditional at any height (A-4 Loess Hills).
    if cap is None:
        return (UsePermission.CONDITIONAL, ProcessType.CONDITIONAL_USE,
                f"towers are a conditional use in {d} at any height "
                f"(§{cond_sec}), with no by-right alternative")

    if height_ft <= cap:
        return (UsePermission.PERMITTED, ProcessType.BUILDING_PERMIT,
                f"towers up to {cap:.0f} ft are a principal use in {d} "
                f"(§{by_right_sec}); {height_ft:.0f} ft is within that")

    if cond_sec:
        return (UsePermission.CONDITIONAL, ProcessType.CONDITIONAL_USE,
                f"{height_ft:.0f} ft exceeds the {cap:.0f} ft by-right limit in {d} "
                f"(§{by_right_sec}), but a tower above that height is a conditional "
                f"use (§{cond_sec})")

    # A by-right cap with no clause above it: the ordinance is silent, which is
    # neither permission nor refusal.
    return (UsePermission.UNKNOWN, ProcessType.UNKNOWN,
            f"{height_ft:.0f} ft exceeds the {cap:.0f} ft by-right limit in {d} "
            f"(§{by_right_sec}), and the ordinance provides no conditional clause "
            f"above that height in this district, so the outcome is undetermined "
            f"rather than prohibited")


def process_for(district: str, height_ft: float) -> ProcessType:
    return evaluate(district, height_ft)[1]


def permission_for(district: str, height_ft: float) -> UsePermission:
    return evaluate(district, height_ft)[0]
