"""City of Oakland, Iowa. Needed because TI-OPP-43986 straddles the city and
unincorporated Pottawattamie County.

THE INTERESTING PART: this rule set disagrees with the human audit.

The TI-OPP-43986 audit concludes that a tower in the city portion of the ring
would require a TEXT AMENDMENT, and rates the City of Oakland zoning level 4 on
that basis. Its reasoning: towers appear as a permitted use only in M-1, the
ring's city portion is Agriculture and Single Family Residential, so the use
would have to be added to the Agriculture district.

Reading the ordinance itself, the first half of that is right and the conclusion
does not follow. Section 5(A) item 7 lists

    "Radio tower or broadcasting station."

as a SPECIAL USE, and the ordinance's special-use provisions are not district
specific: a special use is granted by the Mayor and Council after Planning and
Zoning Commission review, subject to compliance with the district's other
regulations. So a path exists that is not a text amendment.

Whether it is available turns on one interpretive question: is a commercial
wireless tower a "radio tower"? Cellular service is radio service, and the
ordinance offers no narrower definition. Against that, an ordinance of this
vintage listing "radio tower or broadcasting station" together may have meant
broadcast facilities. The ordinance does not say.

So the pipeline does not pick a side. It reports the special-use path as
available-if-qualifying, records the text-amendment fallback, and marks the
question Unresolved with the specific thing to ask the city. That is a real
discrepancy with a human example, with the evidence for each reading stated,
which is what the calibration is for.

Second finding, also absent from the human audit: Section 6 exempts
"transmission towers... radio and television towers... masts, aerials" from the
height regulations entirely. There is no height cap on a tower in Oakland.

Provenance: partial extract from the city's published ordinance PDF. See
data/oakland_zoning.txt for what was and was not captured.
"""

from __future__ import annotations

from typing import Optional

from ..models import ProcessType, UsePermission, ZoningRule
from .snapshot import Snapshot, verify_quote

JURISDICTION = "City of Oakland"
ORDINANCE_URL = ("https://www.cityofoaklandiowa.com/sites/default/files/uploads/"
                 "ordinances/135310758.pdf")

# Districts in the ordinance. Note there is no A-1 or A-2 here; those belong to
# the county, and a report covering both must not conflate them.
DISTRICTS = {"AG", "R-1", "R-1M", "R-2", "R-3", "C-1", "CBD", "M-1", "F"}

# Towers are a permitted use by right only in M-1.
BY_RIGHT_DISTRICTS = {"M-1"}
# C-1 names communication services but carves towers out explicitly.
TOWERS_EXCLUDED_DISTRICTS = {"C-1"}

RULES: list[tuple[str, Optional[str], object, Optional[str], str, str]] = [
    ("tower_use", "M-1", UsePermission.PERMITTED.value, None,
     "M-1 Uses Permitted by Right, item 35",
     "Telephone and other communication services, including satellite stations and "
     "towers."),
    ("tower_use", "C-1", UsePermission.PROHIBITED.value, None,
     "C-1 Uses Permitted by Right, item 52",
     "Telephone and communication services, except satellite stations and towers."),
    ("special_use_available", None, UsePermission.CONDITIONAL.value, None,
     "Section 5(A), item 7",
     "Radio tower or broadcasting station."),
    ("special_use_compliance", None, True, None, "Section 5",
     "Any proposed special use shall otherwise comply with all the regulations set "
     "forth in the ordinance"),
    ("special_use_commencement", None, 1, "years", "Section 5(C)",
     "Special uses shall commence within one year from the date of approval, unless "
     "extended by action of the City Council."),
    ("special_use_revocable", None, True, None, "Section 5(D)",
     "Revocation proceedings may be initiated by a majority vote of the City Council "
     "or the Planning and Zoning Commission."),
    ("height_exempt", None, "no_height_limit", None, "Section 6, Height",
     "The height regulations prescribed herein shall not apply to chimneys, cupolas, "
     "tanks, church spires, belfries, domes, monuments, fire and hose towers, "
     "observation towers, transmission towers, flagpoles, radio and television "
     "towers, masts, aerials, cooling towers, water towers, elevator shafts, "
     "windmills, conveyors, and other similar projections."),
]

# The interpretive question the whole city analysis turns on.
RADIO_TOWER_QUESTION = (
    "Does the City of Oakland treat a commercial wireless tower as a "
    "\"Radio tower or broadcasting station\" under Section 5(A) item 7? If it does, "
    "a special use permit is available in any district and no text amendment is "
    "needed. If it does not, the use exists only in M-1 and reaching any other "
    "district requires a text amendment. Cellular service is radio service and the "
    "ordinance defines the term no more narrowly, but an ordinance of this vintage "
    "pairing \"radio tower\" with \"broadcasting station\" may have meant broadcast "
    "facilities. The TI-OPP-43986 human audit assumed the narrower reading without "
    "citing Section 5."
)


def build_rules(snapshot: Snapshot) -> tuple[list[ZoningRule], list[str]]:
    rules: list[ZoningRule] = []
    warnings: list[str] = []
    for kind, district, value, units, section, quote in RULES:
        if not verify_quote(quote, snapshot):
            warnings.append(
                f"dropped {kind} rule for {district or 'all districts'}: quote for "
                f"{section} not found in the Oakland extract")
            continue
        rules.append(ZoningRule(
            kind=kind, jurisdiction=JURISDICTION, district=district, value=value,
            units=units, section=section, quote=quote,
            url=snapshot.url or ORDINANCE_URL,
            retrieved_at=snapshot.retrieved_at, snapshot_hash=snapshot.sha256,
            notes="Partial extract of the ordinance; see data/oakland_zoning.txt.",
        ))
    return rules, warnings


def evaluate(district: str, height_ft: float,
             radio_tower_qualifies: Optional[bool] = None
             ) -> tuple[UsePermission, ProcessType, str]:
    """Permission and path for a tower in an Oakland district.

    `radio_tower_qualifies` is deliberately tri-state. None means the question
    has not been answered by the city, which is the real state of knowledge, and
    produces a conditional answer rather than a guess in either direction.

    Height never enters: Section 6 exempts towers from the height regulations.
    """
    d = (district or "").strip().upper()

    if d in BY_RIGHT_DISTRICTS:
        return (UsePermission.PERMITTED, ProcessType.BUILDING_PERMIT,
                f"towers are a permitted use by right in {d} (M-1 Uses Permitted by "
                f"Right, item 35); Section 6 exempts towers from the height "
                f"regulations, so {height_ft:.0f} ft is not capped by the ordinance")

    if radio_tower_qualifies is True:
        return (UsePermission.CONDITIONAL, ProcessType.SPECIAL_USE,
                f"towers are not a listed use in {d}, but a \"Radio tower or "
                f"broadcasting station\" is available as a special use under Section "
                f"5(A) item 7, granted by the Mayor and Council after Planning and "
                f"Zoning Commission review")

    if radio_tower_qualifies is False:
        return (UsePermission.PROHIBITED, ProcessType.TEXT_AMENDMENT,
                f"towers are a listed use only in M-1, and the city does not treat a "
                f"wireless tower as a \"Radio tower\" under Section 5(A) item 7, so "
                f"adding the use to {d} would require a text amendment")

    return (UsePermission.UNKNOWN, ProcessType.UNKNOWN,
            f"towers are a listed use only in M-1, so the path in {d} depends on "
            f"whether the city reads a wireless tower as a \"Radio tower or "
            f"broadcasting station\" under Section 5(A) item 7: special use if it "
            f"does, text amendment if it does not")


def unresolved_question(district: str):
    """The Unresolved entry every non-M-1 Oakland parcel carries."""
    from ..models import Unresolved
    return Unresolved(
        what_is_missing=(f"Whether the City of Oakland treats a commercial wireless "
                         f"tower as a \"Radio tower or broadcasting station\" under "
                         f"Section 5(A) item 7, which decides the path in {district}"),
        why_it_matters=("It is the difference between a special use permit and a text "
                        "amendment: a discretionary approval the city grants "
                        "routinely, versus a legislative change to the ordinance. "
                        "That is a zoning level 3 versus a level 4, and months of "
                        "difference in the schedule."),
        how_to_resolve=("Ask City of Oakland Administration (712-482-6811) whether a "
                        "wireless communications tower has been or would be processed "
                        "as a special use under Section 5(A) item 7, or ask for any "
                        "prior tower approval in the city and how it was handled."),
    )
