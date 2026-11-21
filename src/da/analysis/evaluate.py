"""Per-parcel evaluation: rules meet geometry, and a disposition comes out.

This is the step the DA training assigns to the audit creator after zoning
returns: check whether the zoning recommendation makes sense for the land that
is actually there. The human reports do it by eye and record only a site-level
call; here every parcel gets Pursue, Do not pursue or Unresolved with a reason
code and the evidence behind it.

Ordering matters. Use permission is checked before geometry, because a
prohibited use makes compound arithmetic beside the point, and an Unresolved
district makes every downstream number unreliable.
"""

from __future__ import annotations

from typing import Any, Optional

from ..evidence import Ledger
from ..geo.buildable import (CompoundFit, best_compound_fit, buildable_region,
                             dwelling_buffers, guy_anchors_fit)
from ..geo.crs import SQFT_PER_ACRE, area_sqft
from ..models import (Disposition, Finding, Parcel, ProcessType, TowerSpec,
                      UsePermission, Unresolved)
from ..zoning import council_bluffs as cb
from ..zoning import oakland as oak
from ..zoning import pottawattamie as pott

# Reason codes are stable strings so the comparison table can group by them.
RC_PROHIBITED = "zoning_prohibits_use"
RC_NO_DISTRICT = "district_undetermined"
RC_DISTRICT_CONFLICT = "district_conflict"
RC_TOO_SMALL = "insufficient_buildable_area"
RC_DWELLING = "dwelling_distance"
RC_WETLAND = "wetland_excludes_compound"
RC_GUY_ANCHORS = "guy_anchors_do_not_fit"
RC_OK = "meets_requirements"
RC_EXCEPTION_SIZE = "requires_80x80_exception"
RC_FLOOD_RISK = "flood_zone_risk"
RC_HEIGHT_UNADDRESSED = "height_not_addressed_by_code"
RC_USE_PATH_UNDETERMINED = "use_path_undetermined"


def evaluate_parcel(
    parcel: Parcel,
    ring,
    tower: TowerSpec,
    ledger: Ledger,
    *,
    wetlands: Optional[list[Any]] = None,
    flood: Optional[list[dict]] = None,
    dwelling_points: Optional[list[Any]] = None,
    compound_primary_ft: float = 100.0,
    compound_exception_ft: float = 80.0,
    apply_dwelling_rule: bool = True,
    radio_tower_qualifies: Optional[bool] = None,
    fall_distance_ratio: float = 1.0,
) -> Parcel:
    """Decide one parcel. Mutates and returns it."""

    # -- 1. district ------------------------------------------------------
    if not parcel.districts:
        parcel.disposition = Disposition.UNRESOLVED
        parcel.reason_codes.append(RC_NO_DISTRICT)
        if not parcel.unresolved:
            parcel.unresolved.append(Unresolved(
                what_is_missing=f"No zoning district determined for parcel {parcel.pin}",
                why_it_matters="Without a district there is no height cap, setback or "
                               "use permission to apply.",
                how_to_resolve="Confirm the district with the planning department.",
            ))
        return parcel

    if any(d.source == "conflict" for d in parcel.districts):
        parcel.disposition = Disposition.UNRESOLVED
        parcel.reason_codes.append(RC_DISTRICT_CONFLICT)
        return parcel

    # A parcel can span districts. The DA training says to pursue the least
    # restrictive first, which is what the 34659 audit did: A-2 by right, with
    # A-4 kept as a last resort because it needs a full CUP. So evaluate on the
    # most permissive district present and record which one was used.
    chosen = _most_permissive(parcel, tower)
    district = chosen.code
    is_county = chosen.jurisdiction == pott.JURISDICTION
    is_cb = chosen.jurisdiction == cb.JURISDICTION
    is_oak = chosen.jurisdiction == oak.JURISDICTION
    if len(parcel.districts) > 1:
        others = ", ".join(d.code for d in parcel.districts if d.code != district)
        parcel.conditions.append(
            f"Parcel spans more than one district; evaluated on {district} as the "
            f"least restrictive. Also within: {others}.")

    # -- 2. use permission ------------------------------------------------
    if is_county or is_cb or is_oak:
        if is_oak:
            permission, process, why = oak.evaluate(
                district, tower.overall_height_ft, radio_tower_qualifies)
            section = "Section 5(A), item 7"
            if permission is UsePermission.UNKNOWN:
                parcel.unresolved.append(oak.unresolved_question(district))
        elif is_cb:
            permission, process, why = cb.evaluate(district, tower.overall_height_ft)
            section = "15.31.020"
        else:
            permission, process, why = pott.evaluate(district, tower.overall_height_ft)
            section = _deciding_section(
                pott.TOWER_USE_BY_DISTRICT.get(district), tower.overall_height_ft)
        parcel.findings.append(Finding(
            statement=why,
            evidence_ids=chosen.evidence_ids,
            rule_section=section,
            passed=permission is not UsePermission.PROHIBITED,
        ))
        if permission is UsePermission.UNKNOWN:
            parcel.disposition = Disposition.UNRESOLVED
            # The county returns UNKNOWN for two different reasons, and they
            # are not the same finding. A district the ordinance never mentions
            # really is an undetermined district. A district that caps towers
            # by right and says nothing above that cap (C-1, C-2, I-1, I-2) is
            # a known district with a gap in its height clause, and labelling
            # that "no district" misreports what was actually found.
            county_knows_district = (
                not is_oak and not is_cb
                and pott.TOWER_USE_BY_DISTRICT.get(district) is not None)
            parcel.reason_codes.append(
                RC_USE_PATH_UNDETERMINED if is_oak else
                RC_HEIGHT_UNADDRESSED if (is_cb or county_knows_district)
                else RC_NO_DISTRICT)
            if is_oak:
                return parcel
            parcel.unresolved.append(Unresolved(
                what_is_missing=why if (is_cb or county_knows_district) else
                                f"No tower clause found for district {district}",
                why_it_matters=("The code permits towers in all districts only up to "
                                "200 ft and says nothing about taller ones, so neither "
                                "approval nor refusal can be inferred."
                                if is_cb else
                                "Cannot say whether a tower is permitted here."),
                how_to_resolve=(f"Ask Council Bluffs Planning & Zoning (712-890-5350) "
                                f"whether a variance or text amendment is the route for "
                                f"a tower above 200 ft."
                                if is_cb else
                                f"Read the {district} chapter of the county ordinance, "
                                f"or ask the planning department."),
            ))
            return parcel
        if permission is UsePermission.PROHIBITED:
            parcel.disposition = Disposition.DO_NOT_PURSUE
            parcel.reason_codes.append(RC_PROHIBITED)
            return parcel
        if process in (ProcessType.CONDITIONAL_USE, ProcessType.SPECIAL_USE):
            parcel.conditions.append(
                f"Conditional use permit required in {district}; routed through the "
                + ("Zoning Board of Adjustment under CBMC Chapter 15.02. The chapter "
                   "sets no numeric tower separation, which both human audits note "
                   "widens the board's discretion rather than easing approval."
                   if is_cb else
                   "Board of Adjustment (Chapter 8.096)."))
        if process is ProcessType.VARIANCE:
            parcel.conditions.append(
                f"A variance would be required for height in {district}.")
        if process is ProcessType.TEXT_AMENDMENT:
            parcel.conditions.append(
                f"Adding the use to {district} would require a text amendment, a "
                f"legislative act rather than a discretionary approval.")
    else:
        parcel.unresolved.append(Unresolved(
            what_is_missing=f"Tower rules for {parcel.districts[0].jurisdiction} "
                            f"district {district} are not in the rule library",
            why_it_matters="Height cap, setbacks and approval path are unknown, so the "
                           "geometry below is evaluated without them.",
            how_to_resolve=f"Snapshot the {parcel.districts[0].jurisdiction} code and "
                           f"add its rule set.",
            blocking=False,
        ))
        process = ProcessType.UNKNOWN

    # -- 3. geometry ------------------------------------------------------
    setback_ft = _setback_for(district, tower, is_county, is_cb, is_oak,
                              fall_distance_ratio)
    exclusions: list[Any] = list(wetlands or [])

    keep_out: list[Any] = []
    if apply_dwelling_rule and is_county and dwelling_points:  # noqa: E501 - CB handles dwellings through its setback instead
        keep_out = dwelling_buffers(dwelling_points, tower.overall_height_ft)
        exclusions.extend(keep_out)

    region = buildable_region(parcel.geometry, ring.polygon, setback_ft, exclusions)
    fit = best_compound_fit(region, compound_primary_ft, compound_exception_ft)
    parcel.buildable_sqft = fit.buildable_sqft
    parcel.compound_fit = f"{fit.size_ft:.0f}x{fit.size_ft:.0f}" if fit.fits else None

    eid = ledger.computed(
        round(fit.buildable_sqft, 1),
        f"Buildable area on parcel {parcel.pin}: {fit.buildable_sqft:,.0f} sq ft "
        f"({fit.buildable_sqft / SQFT_PER_ACRE:.2f} acres) after a {setback_ft:.0f} ft "
        f"setback, the search ring boundary"
        + (", wetlands" if wetlands else "")
        + (f", and {len(keep_out)} dwelling keep-out circles of "
           f"{tower.overall_height_ft:.0f} ft" if keep_out else ""),
        parcel.evidence_ids[:1] + (tower.evidence_ids[:1] or []),
    )
    parcel.evidence_ids.append(eid)

    if not fit.fits:
        parcel.disposition = Disposition.DO_NOT_PURSUE
        parcel.reason_codes.append(
            RC_DWELLING if keep_out and _dwelling_is_binding(
                parcel, ring, setback_ft, wetlands, compound_exception_ft)
            else (RC_WETLAND if wetlands and _wetland_is_binding(
                parcel, ring, setback_ft, keep_out, compound_exception_ft)
                else RC_TOO_SMALL))
        parcel.findings.append(Finding(
            statement=f"No {compound_exception_ft:.0f} x {compound_exception_ft:.0f} ft "
                      f"compound fits within the buildable area "
                      f"({fit.buildable_sqft:,.0f} sq ft).",
            evidence_ids=[eid],
            passed=False,
        ))
        return parcel

    # -- 4. guy anchors ---------------------------------------------------
    if tower.guy_anchor_radius_ft and fit.center:
        # The fall-distance setback applies to the TOWER, not to its guy
        # anchors: §8.004.220.12 measures "from the base of the tower to the
        # nearest boundary line". Anchors are separate ground hardware and the
        # ordinance sets no setback for them, so they need only sit on land the
        # lease controls and clear of wetlands, which the DA training asks about
        # directly. Eroding by the full fall distance here applied the setback
        # twice and rejected parcels the ordinance permits: at 450 ft the tower
        # base is already 450 ft inside the boundary, so a 360 ft anchor circle
        # cannot reach it.
        anchors_ok = guy_anchors_fit(parcel.geometry, fit.center,
                                     tower.guy_anchor_radius_ft,
                                     setback_ft=0.0,
                                     exclusions=wetlands)
        parcel.findings.append(Finding(
            statement=f"Guy anchor circle of {tower.guy_anchor_radius_ft:.0f} ft radius "
                      f"({(3.14159 * tower.guy_anchor_radius_ft ** 2) / SQFT_PER_ACRE:.1f} "
                      f"acres) {'fits' if anchors_ok else 'does not fit'} on the parcel.",
            evidence_ids=[eid] + (tower.evidence_ids[-1:] if tower.evidence_ids else []),
            passed=anchors_ok,
        ))
        if not anchors_ok:
            parcel.disposition = Disposition.DO_NOT_PURSUE
            parcel.reason_codes.append(RC_GUY_ANCHORS)
            return parcel

    # -- 5. flood is a risk, not a disqualifier ---------------------------
    for z in flood or []:
        if z.get("geometry") is not None and z["geometry"].intersects(parcel.geometry):
            zone = z.get("zone") or "unknown"
            if str(zone).upper() not in ("X", "AREA OF MINIMAL FLOOD HAZARD"):
                parcel.risks.append(f"FEMA flood zone {zone} covers part of this parcel")
                if RC_FLOOD_RISK not in parcel.reason_codes:
                    parcel.reason_codes.append(RC_FLOOD_RISK)

    # -- 6. pursue --------------------------------------------------------
    parcel.disposition = Disposition.PURSUE
    parcel.reason_codes.append(RC_OK)
    if fit.size_ft and fit.size_ft < compound_primary_ft:
        parcel.reason_codes.append(RC_EXCEPTION_SIZE)
        parcel.conditions.append(
            f"Only a {fit.size_ft:.0f} x {fit.size_ft:.0f} ft compound fits, not the "
            f"standard {compound_primary_ft:.0f} x {compound_primary_ft:.0f} ft. "
            f"Tillman has approved the smaller footprint on special occasions, so this "
            f"needs explicit approval rather than being assumed.")
    parcel.findings.append(Finding(
        statement=f"A {fit.size_ft:.0f} x {fit.size_ft:.0f} ft compound fits within the "
                  f"buildable area with the tower base inside the search ring.",
        evidence_ids=[eid],
        passed=True,
    ))
    return parcel


def _deciding_section(entry, height_ft: float) -> Optional[str]:
    """The ordinance section that actually decided the use question.

    An entry is (cap, by-right section, by-right quote, conditional section,
    conditional quote). An earlier version read index 2, which is the QUOTE,
    so every county finding printed a sentence of ordinance text where the
    section number belongs. Below the cap the by-right clause decides; above
    it the conditional clause does, where one exists.
    """
    if not entry:
        return None
    cap, by_sec, _by_quote, cond_sec, _cond_quote = entry
    if cap is not None and height_ft <= cap and by_sec:
        return by_sec
    return cond_sec or by_sec


def _most_permissive(parcel: Parcel, tower: TowerSpec):
    """Rank a parcel's districts by how easy the approval path is."""
    from ..models import ZoningLevel

    def rank(d) -> tuple[int, int]:
        if d.jurisdiction == oak.JURISDICTION:
            permission, process, _ = oak.evaluate(d.code, tower.overall_height_ft)
        elif d.jurisdiction == cb.JURISDICTION:
            permission, process, _ = cb.evaluate(d.code, tower.overall_height_ft)
        elif d.jurisdiction == pott.JURISDICTION:
            permission, process, _ = pott.evaluate(d.code, tower.overall_height_ft)
        else:
            return (5, 0)
        prohibited = 1 if permission is UsePermission.PROHIBITED else 0
        return (prohibited, ZoningLevel.from_process(process))

    return min(parcel.districts, key=rank)


def _setback_for(district: str, tower: TowerSpec, is_county: bool,
                 is_cb: bool = False, is_oak: bool = False,
                 fall_distance_ratio: float = 1.0) -> float:
    """Pottawattamie Sec. 8.004.220.12: towers over 50 ft are set back by the
    manufacturer's designed fall distance, which overrides the district's
    dimensional setbacks.

    The fall distance is a manufacturer figure that only exists once a structure
    is engineered, so it is unknowable at desktop-audit stage. Using the tower's
    full height is the conservative stand-in and is stated as an assumption in
    every report; the real figure usually comes from a fall-zone letter and is
    often much less than full height.

    `fall_distance_ratio` scales that stand-in and applies to the county case
    only. Council Bluffs' 100% figure is an actual code requirement rather than
    a stand-in (CBMC 15.31.050(4)), and Oakland's is a stand-in for absent
    dimensional standards rather than for a fall distance, so scaling either by
    a fall-distance assumption would misrepresent what the ordinance says.
    """
    if is_oak:
        # Oakland Section 6 exempts towers from the height regulations, and the
        # AG district's dimensional standards were not captured in the extract,
        # so the tower's own height is used as a conservative stand-in and the
        # gap is recorded.
        return float(tower.overall_height_ft)
    if is_cb:
        # CBMC 15.31.050(4): 100% of tower height to an adjoining residential
        # dwelling, 20 ft to a non-residential lot. The dwelling case is the
        # conservative default when neighbouring uses are not established.
        return cb.setback_ft(tower.overall_height_ft,
                             adjoins_residential_dwelling=True)
    if is_county and tower.overall_height_ft > 50:
        return float(tower.overall_height_ft) * float(fall_distance_ratio)
    sb = pott.SETBACKS.get(district)
    return float(max(sb)) if sb else 0.0


def _dwelling_is_binding(parcel, ring, setback_ft, wetlands, size_ft) -> bool:
    """Would the parcel have passed without the dwelling rule? If so the
    dwelling distance is the actual cause, not raw size."""
    region = buildable_region(parcel.geometry, ring.polygon, setback_ft,
                              list(wetlands or []))
    return best_compound_fit(region, size_ft, 0).fits


def _wetland_is_binding(parcel, ring, setback_ft, keep_out, size_ft) -> bool:
    region = buildable_region(parcel.geometry, ring.polygon, setback_ft,
                              list(keep_out or []))
    return best_compound_fit(region, size_ft, 0).fits
