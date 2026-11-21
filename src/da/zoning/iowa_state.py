"""Iowa Code Chapter 8C, the Iowa Cell Siting Act, as a statewide overlay.

Every human desktop audit carries a "State of Iowa" section covering this
chapter, and 43986 leans on it to argue a conclusion ("outside the requirements
of the state statutes and stands a better chance to be denied"). It sits above
every local ordinance in Iowa, so it is applied to every site in the state.

It never filters a parcel. Nothing in it changes geometry. What it does is
constrain what a jurisdiction may demand, cap the fees, set the review clock,
and in some cases preempt a local rule outright. That makes it narrative and
process context, and it is attached to the report rather than to parcels.

PROVENANCE, AND WHY THIS MODULE IS DIFFERENT
--------------------------------------------
Every other rule set in this pipeline carries verbatim quotations verified as
literal substrings of a stored snapshot. This one does not, and the reason is
recorded rather than hidden.

The Iowa Legislature publishes Chapter 8C only as PDF and RTF. The PDF has no
extractable text layer through any route available here, and the RTF is binary.
Secondary sources paraphrase rather than reproduce. So the chapter is cited by
section number against the official URL, with the substance of each section
summarised, and each record is tagged `statute_reference` rather than
`document_quote`.

The section list and titles below ARE authoritative: they were read from the
Legislature's own chapter listing for the 2025 Code.

The honest consequence: a reader can verify WHICH provision is being relied on
and go read it, but cannot check wording against a snapshot held here. That is
weaker than the county and city rule sets, and the report says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

CHAPTER = "Iowa Code Chapter 8C"
SHORT_TITLE = "Iowa Cell Siting Act"
ENACTED = 2015
CODE_YEAR = 2025
CHAPTER_URL = "https://www.legis.iowa.gov/law/iowaCode/sections?codeChapter=8C"
SECTION_URL = "https://www.legis.iowa.gov/docs/code/{year}/{section}.pdf"

# Section numbers and titles as the Legislature's chapter listing presents them.
SECTIONS: dict[str, str] = {
    "8C.1": "Short title.",
    "8C.2": "Definitions.",
    "8C.3": "Uniform rules and limitations - applications.",
    "8C.4": "Uniform rules - new tower applications.",
    "8C.5": "Uniform rules for certain changes.",
    "8C.6": "Use of public lands for towers and transmission equipment.",
    "8C.7": "Utility poles.",
    "8C.7A": "Uniform rules for small wireless facilities - permit approval.",
    "8C.7B": "Small wireless facilities - violation and removal.",
    "8C.7C": "Height limitations.",
    "8C.8": "Application and construction.",
    "8C.9": "Repeal.",
}


@dataclass
class StatuteProvision:
    """A provision relied on, cited by section rather than quoted."""

    section: str
    title: str
    summary: str
    bears_on: str                 # what it affects in a desktop audit
    preempts_local: bool = False  # may override a local requirement

    @property
    def url(self) -> str:
        return SECTION_URL.format(year=CODE_YEAR, section=self.section)


# The provisions that actually bear on a new-tower desktop audit. 8C.7, 8C.7A,
# 8C.7B and 8C.7C govern small wireless facilities and utility poles, which are
# a different scope of work, so they are listed above but not applied here.
PROVISIONS: list[StatuteProvision] = [
    StatuteProvision(
        section="8C.3",
        title=SECTIONS["8C.3"],
        summary=(
            "Bars an authority from, among other things: requiring or evaluating an "
            "applicant's business decisions, service design or customer demand "
            "(propagation maps may be required only to show a coverage gap, or for a "
            "new tower in a residentially zoned area); evaluating an application on "
            "the availability of other locations or requiring collocation instead of "
            "a new tower, though for a new tower the applicant must explain the site "
            "choice and why it did not collocate, with a sworn statement; dictating "
            "equipment or technology; requiring removal of existing towers as a "
            "condition of approval; imposing RF emissions testing on categorically "
            "excluded equipment; regulating RF signal strength or service quality; "
            "rejecting an application over alleged RF health effects; prohibiting "
            "compliant emergency power systems; charging more than actual, direct and "
            "reasonable administrative cost, capped at $500 for an eligible facilities "
            "request and $3,000 for other applications, with contingency-based "
            "consultant fees barred; imposing removal surety unless competitively "
            "neutral, nondiscriminatory and reasonable; conditioning approval on "
            "below-market space or services; limiting the duration of an approval, "
            "except that construction must begin within two years of final approval "
            "and be diligently pursued; or discriminating on property or tower "
            "ownership."
        ),
        bears_on=("The collocation statement and economic-burden affidavit the local "
                  "application requires, the fee the jurisdiction may charge, and "
                  "several conditions a board might otherwise attach."),
        preempts_local=True,
    ),
    StatuteProvision(
        section="8C.4",
        title=SECTIONS["8C.4"],
        summary=(
            "Sets the uniform review process for new tower applications, including the "
            "review period the human audits describe as a 150-day shot clock, tolled "
            "only by a notice of incompleteness served within a short window after "
            "filing, and not tolled where the jurisdiction asks for information the "
            "chapter does not permit it to require."
        ),
        bears_on="The entitlement timeframe stated in the report.",
        preempts_local=True,
    ),
    StatuteProvision(
        section="8C.6",
        title=SECTIONS["8C.6"],
        summary=(
            "Governs use of public land for towers and transmission equipment, "
            "including that a jurisdiction leasing its own land does so at market "
            "rate."
        ),
        bears_on=("Only where a candidate parcel is publicly owned, which the parcel "
                  "owner field will show."),
    ),
    StatuteProvision(
        section="8C.2",
        title=SECTIONS["8C.2"],
        summary="Defines the terms the chapter's limitations operate on.",
        bears_on="Whether a given application falls inside the chapter at all.",
    ),
]

# Local requirements this chapter plausibly reaches. Flagged as questions for
# counsel, never asserted as settled: whether a specific local rule is preempted
# is a legal conclusion, and the pipeline does not make legal conclusions.
POTENTIAL_PREEMPTION_CHECKS: list[tuple[str, str]] = [
    ("separation",
     "A fixed minimum separation between towers can function as evaluating an "
     "application on the availability of other locations, which §8C.3 bars. The "
     "35989 audit reached for exactly this argument against a 750 ft separation "
     "rule. Whether it succeeds is a question for counsel."),
    ("collocation_statement",
     "§8C.3 allows a jurisdiction to require an explanation for choosing the site "
     "over collocation, with a sworn statement, but not to require that collocation "
     "be ruled out as a precondition. Local wording that goes further is worth "
     "testing."),
    ("consulting_fee",
     "Fees above actual, direct and reasonable administrative cost, over $3,000 for "
     "a new tower, or charged on a contingency basis, run against §8C.3."),
    ("surety",
     "A removal bond or letter of credit must be competitively neutral, "
     "nondiscriminatory and reasonable in amount under §8C.3."),
    ("approval_duration",
     "A local rule limiting how long an approval lasts runs against §8C.3, subject "
     "to the requirement that construction begin within two years."),
]


# ---------------------------------------------------------------------------
# Iowa Code Chapter 335: the agricultural exemption from COUNTY zoning.
#
# Separate statute, separate question from Chapter 8C. 8C constrains what a
# jurisdiction may demand of an applicant. Section 335.2 asks whether a county
# has zoning authority over the structure at all.
#
# It matters here because the most permissive districts in Pottawattamie County
# are agricultural, so most candidate parcels are farmland. If the exemption
# reached a tower, the county's height caps and setbacks would not apply to it.
#
# It does not reach a tower, for three reasons, and the reasoning is recorded
# rather than assumed:
#
#   1. Section 335.2 exempts land and structures "primarily adapted, by reason
#      of nature and area, for use for agricultural purposes, WHILE SO USED".
#      The exemption attaches to each structure and its actual use, not to the
#      parcel, so a farm can hold exempt barns and a regulated tower at once.
#      The leased compound is also taken out of production, so the land limb of
#      the exemption lapses for that footprint.
#   2. Kuehl is the controlling test and asks whether the structure itself is
#      primarily adapted for agriculture, defined as cultivation of the ground
#      including crop harvesting and livestock rearing. Kuehl BROADENED the
#      exemption, so the risk runs toward reading it generously, and a
#      telecommunications tower still fails that definition on its face.
#   3. The county's own ordinance writes tower provisions into the A districts,
#      500 ft by right in A-2 and A-3, which only makes sense if the county
#      believes it has authority there. Other Iowa counties do the same.
#
# The honest gap: no Iowa appellate decision squarely addresses a
# communications tower under 335.2. This is statute plus Kuehl plus consistent
# county practice, not a holding on point, so it is surfaced as a question
# counsel should confirm rather than presented as settled.
# ---------------------------------------------------------------------------

AG_EXEMPTION_SECTION = "335.2"
AG_EXEMPTION_TITLE = "Farms exempt."
AG_EXEMPTION_URL = SECTION_URL.format(year=CODE_YEAR, section="335.2")
KUEHL_CITE = "Kuehl v. Cass County, 555 N.W.2d 686 (Iowa 1996)"

# District prefixes the county treats as agricultural.
AG_DISTRICT_PREFIXES = ("A-",)


def ag_exemption_in_play(state: str, jurisdiction: Optional[str],
                         district_codes: set[str]) -> bool:
    """True where the question arises: Iowa, county zoning, agricultural land.

    A city's zoning authority is not affected by 335.2, which by its terms
    binds counties only, so this never fires inside Council Bluffs or Oakland.
    """
    if (state or "").strip().upper() != "IA":
        return False
    if not jurisdiction or "County" not in jurisdiction:
        return False
    return any(code.upper().startswith(AG_DISTRICT_PREFIXES)
               for code in district_codes)


def record_ag_exemption(ledger, state: str, jurisdiction: Optional[str],
                        district_codes: set[str]) -> list[str]:
    """Record the analysis once per report, not once per agricultural parcel."""
    from ..models import SourceType

    if not ag_exemption_in_play(state, jurisdiction, district_codes):
        return []

    ag = sorted(c for c in district_codes
                if c.upper().startswith(AG_DISTRICT_PREFIXES))
    base = ledger.add(
        f"Iowa Code §{AG_EXEMPTION_SECTION} exempts land and structures "
        f"'primarily adapted, by reason of nature and area, for use for "
        f"agricultural purposes, while so used' from county zoning.",
        SourceType.DATASET,
        f"§{AG_EXEMPTION_SECTION} {AG_EXEMPTION_TITLE} Cited by section against "
        f"the Legislature's published Code; Chapter 335 is published as PDF "
        f"without an extractable text layer, so this is a reference rather than "
        f"a verified quotation.",
        url=AG_EXEMPTION_URL, section=AG_EXEMPTION_SECTION,
    )
    analysis = ledger.add(
        f"The exemption does not reach a commercial telecommunications tower on "
        f"{', '.join(ag)} land. It attaches to each structure and its actual use "
        f"rather than to the parcel, and lapses for land no longer in "
        f"agricultural production, so the leased compound falls outside it. "
        f"{KUEHL_CITE} asks whether the structure itself is primarily adapted for "
        f"agriculture, defined as cultivation of the ground including crop "
        f"harvesting and livestock rearing and management; a tower is neither. "
        f"The county ordinance's own tower provisions for the A districts assume "
        f"county authority over them.",
        SourceType.COMPUTED,
        "County zoning authority over a tower on agricultural land",
        derived_from=[base],
    )
    return [base, analysis]


def ag_exemption_unresolved(state: str, jurisdiction: Optional[str],
                            district_codes: set[str]):
    """The residual question, once per report. Non-blocking.

    Not blocking because the analysis is sound enough to proceed on and the
    county plainly asserts authority. Recorded because it has not been tested
    in an Iowa appellate court, and a reviewer should know which parts of the
    determination rest on a holding and which on reasoning from the statute.
    """
    from ..models import Unresolved

    if not ag_exemption_in_play(state, jurisdiction, district_codes):
        return None
    return Unresolved(
        what_is_missing=(
            f"No Iowa appellate decision applies the §{AG_EXEMPTION_SECTION} "
            f"agricultural exemption to a communications tower"),
        why_it_matters=(
            "Candidate parcels sit in agricultural districts. If the exemption "
            "reached a tower, the county's height caps, setbacks and conditional "
            "use process would not apply to it. The analysis here is that it does "
            "not, because the exemption is use-based and a tower is not an "
            "agricultural use, but that rests on the statutory text and "
            f"{KUEHL_CITE} rather than on a decision about towers."),
        how_to_resolve=(
            "Confirm with counsel, and confirm with the county that it asserts "
            "zoning authority over towers on agricultural land, which its own "
            "A-district tower provisions indicate it does."),
        blocking=False,
    )


def provisions_for_site(state: str) -> list[StatuteProvision]:
    """Chapter 8C applies to every site in Iowa and nowhere else."""
    return list(PROVISIONS) if (state or "").strip().upper() == "IA" else []


def preemption_flags(local_rule_kinds: set[str]) -> list[tuple[str, str]]:
    """Which local rules in play may be reached by the statute."""
    return [(kind, note) for kind, note in POTENTIAL_PREEMPTION_CHECKS
            if kind in local_rule_kinds]


def record(ledger, state: str, local_rule_kinds: Optional[set[str]] = None
           ) -> list[str]:
    """Add the chapter's provisions to the evidence ledger. Returns evidence ids.

    Recorded as statute references, not quotations: see the module docstring.
    """
    from ..models import SourceType

    provisions = provisions_for_site(state)
    if not provisions:
        return []

    ids = [ledger.add(
        f"{CHAPTER} ({SHORT_TITLE}, enacted {ENACTED}) applies to this site",
        SourceType.DATASET,
        f"{CHAPTER} applies statewide to wireless siting in Iowa. Cited by section "
        f"against the Legislature's published Code; full statutory text is not "
        f"snapshotted here because the Legislature publishes Chapter 8C only as PDF "
        f"and RTF, so these are references rather than verified quotations.",
        url=CHAPTER_URL,
        section=f"Chapter 8C ({CODE_YEAR} Code)",
    )]
    for p in provisions:
        ids.append(ledger.add(
            p.summary, SourceType.DATASET,
            f"§{p.section} {p.title} Bears on: {p.bears_on}",
            url=p.url, section=p.section,
        ))
    for kind, note in preemption_flags(local_rule_kinds or set()):
        ids.append(ledger.add(
            note, SourceType.COMPUTED,
            f"Possible state preemption of the local '{kind}' requirement",
            derived_from=ids[:1],
        ))
    return ids
