"""Council Bluffs rule set, Chapter 15.31 Communication Towers.

Two of the five calibration sites are in Council Bluffs (35482 and the southern
60% of 37954), so this matters as much as the county rule set.

The chapter is unusually simple to apply and unusually hard to clear:

  - Towers up to 200 ft are permitted in EVERY zoning district, which means no
    parcel is ever excluded on use grounds. A tower over 200 ft has no clause at
    all, so it is Unresolved rather than prohibited: the code is silent, and
    silence is not permission or refusal.
  - Approval is always a conditional use permit through the zoning board of
    adjustment under Chapter 15.02. So every Council Bluffs site is at least
    zoning level 3, and there is no by-right path at any height.
  - The binding constraint is the setback: 100% of tower height from the base to
    any adjoining residential DWELLING, whether or not the land is zoned
    residential. That is a structure-to-structure measurement, not a district
    boundary measurement, which makes it stricter in practice than it reads.
  - There is no numeric tower separation requirement. Both human audits flag
    this as making approval harder rather than easier, because it removes the
    objective criterion a board would otherwise be bound by.

One rule the human audits miss entirely: §15.31.050 defines tower height as
measured "from the natural undisturbed ground surface below the center of the
base of the tower to the tip of the highest antenna or piece of equipment
attached". That is measured to the antenna TIP, not the RAD centreline, and
from NATURAL ground rather than finished grade. Both push the regulated height
above the requested RAD, which matters against the 200 ft ceiling.

Provenance: retrieved from the city's official code platform. See
data/council_bluffs_15.31.txt for the retrieval note.
"""

from __future__ import annotations

from typing import Optional

from ..models import ProcessType, UsePermission, ZoningRule
from .snapshot import Snapshot, verify_quote

JURISDICTION = "Council Bluffs"

# Cited by the human audits. The city appears to have moved platforms between
# them, so both are recorded and neither could be fetched.
ORDINANCE_URL = ("https://councilbluffs.municipalcodeonline.com/book?"
                 "type=ordinances#name=Chapter_15.31_-_COMMUNICATION_TOWERS")
# Cited by the 2024 audits; the city appears to have moved platforms since.
ORDINANCE_URL_LEGACY = "http://www.councilbluffs-ia.elaws.us/code/coor_title15"
# Enacting ordinances named in the chapter.
ENACTED = "Ord. No. 6053 § 1 (2010-03-08); Ord. 5458 § 3 (1999)"

MAX_HEIGHT_FT = 200.0

# Setback from the base of the tower to an adjoining residential dwelling, as a
# fraction of tower height.
RESIDENTIAL_DWELLING_RATIO = 1.00
# Setback from the base to an adjoining lot not zoned for residential use.
NONRESIDENTIAL_LOT_SETBACK_FT = 20.0

RULES: list[tuple[str, Optional[str], object, Optional[str], str, str]] = [
    ("tower_use", None, UsePermission.CONDITIONAL.value, None, "15.31.020 Applicability",
     "Communication towers exceeding the maximum height allowed in the underlying "
     "zoning district to a maximum height of two hundred (200) feet are permitted "
     "in all zoning districts, subject to approval of a conditional use permit"),
    ("height_cap", None, MAX_HEIGHT_FT, "ft", "15.31.020 Applicability",
     "to a maximum height of two hundred (200) feet"),
    ("height_measurement", None, "antenna_tip_from_natural_grade", None,
     "15.31.050 Standards And Conditions For Approval",
     "The height of the proposed tower as measured from the natural undisturbed "
     "ground surface below the center of the base of the tower to the tip of the "
     "highest antenna or piece of equipment attached."),
    ("setback_residential_dwelling", None, RESIDENTIAL_DWELLING_RATIO,
     "ratio_of_height", "15.31.050 Standards And Conditions For Approval",
     "One hundred percent (100%) of the tower height from the base of the tower to "
     "any adjoining residential dwelling"),
    ("setback_nonresidential_lot", None, NONRESIDENTIAL_LOT_SETBACK_FT, "ft",
     "15.31.050 Standards And Conditions For Approval",
     "Twenty (20) feet from the base of the tower to any adjoining lot which is "
     "not zoned for residential use."),
    ("separation", None, None, None,
     "15.31.050 Standards And Conditions For Approval",
     "Proximity to other towers."),
    ("accessory_exempt", None, True, None, "15.31.020 Applicability",
     "are exempt from the development standards of this chapter and are allowed in "
     "any zoning district, if accessory to a permitted use"),
    ("permit_validity", None, 2, "years", "15.31.060 Additional Regulations",
     "A conditional use permit shall become void two years after the date of "
     "approval unless the applicant has obtained a building permit"),
    ("permit_runs_with_land", None, True, None,
     "15.31.060 Additional Regulations",
     "shall run with the land and continue to be valid regardless of change of "
     "ownership"),
    ("permit_revocable", None, True, None, "15.31.060 Additional Regulations",
     "the zoning board of adjustment may revoke a conditional use if it finds that "
     "the use is in violation of the terms of approval"),
    ("removal_obsolete", None, 12, "months", "15.31.060 Additional Regulations",
     "All obsolete or unused towers and accessory facilities shall be removed at "
     "owner's expense within twelve (12) months of cessation of operation"),
]


def build_rules(snapshot: Snapshot) -> tuple[list[ZoningRule], list[str]]:
    """Returns (verified rules, warnings about dropped quotes)."""
    rules: list[ZoningRule] = []
    warnings: list[str] = []
    for kind, district, value, units, section, quote in RULES:
        if not verify_quote(quote, snapshot):
            warnings.append(
                f"dropped {kind} rule: quote for §{section} not found in the "
                f"{snapshot.jurisdiction} working text")
            continue
        rules.append(ZoningRule(
            kind=kind, jurisdiction=JURISDICTION, district=district, value=value,
            units=units, section=section, quote=quote,
            url=snapshot.url or ORDINANCE_URL,
            retrieved_at=snapshot.retrieved_at, snapshot_hash=snapshot.sha256,
            notes=f"Enacted by {ENACTED}.",
        ))
    return rules, warnings


def evaluate(district: str, height_ft: float) -> tuple[UsePermission, ProcessType, str]:
    """Permission and approval path. District is irrelevant below 200 ft.

    Above 200 ft the chapter has no clause. That is UNKNOWN, not PROHIBITED: the
    code's silence is not a refusal, and calling it one would be a conclusion the
    evidence does not support.
    """
    if height_ft > MAX_HEIGHT_FT:
        return (UsePermission.UNKNOWN, ProcessType.UNKNOWN,
                f"{height_ft:.0f} ft exceeds the 200 ft maximum in "
                f"§15.31.020 Applicability, which "
                f"permits towers in all districts only up to that height. The chapter "
                f"provides no path for a taller tower, so the outcome is undetermined "
                f"rather than prohibited")
    return (UsePermission.CONDITIONAL, ProcessType.CONDITIONAL_USE,
            f"towers up to {MAX_HEIGHT_FT:.0f} ft are permitted in all districts "
            f"subject to a conditional use permit (§15.31.020 Applicability); "
            f"{height_ft:.0f} ft is "
            f"within that, and {district} does not change the outcome")


def setback_ft(height_ft: float, *, adjoins_residential_dwelling: bool = True) -> float:
    """The binding setback.

    Defaults to the residential-dwelling case, which is the conservative reading
    when the neighbouring use is unknown. The 20 ft figure applies only where
    every adjoining lot is non-residential.
    """
    if adjoins_residential_dwelling:
        return float(height_ft * RESIDENTIAL_DWELLING_RATIO)
    return NONRESIDENTIAL_LOT_SETBACK_FT
