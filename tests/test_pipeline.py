"""Offline test suite. No network: every external source is stubbed.

These cover the parts that decide a parcel's fate. The source modules
themselves are exercised against live services by spikes/probe.py, which is a
reachability check rather than a unit test.
"""

from __future__ import annotations

import pytest
import math
import os
import sys

import pytest
from shapely.geometry import Point, Polygon

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from da.analysis.evaluate import (RC_GUY_ANCHORS, RC_OK, RC_PROHIBITED,
                                  RC_TOO_SMALL, evaluate_parcel)
from da.analysis.letters import assign_letters, check_report
from da.cli import InputError, parse_row
from da.evidence import Ledger, MissingEvidence
from da.geo.buildable import best_compound_fit, buildable_region, fit_compound
from da.geo.ring import Ring
from da.geo.tower import derive, overall_height_ft, tower_type_for
from da.models import (AuditReport, District, Disposition, Parcel, ProcessType,
                       SiteInput, SourceType, TowerType, ZoningLevel)

RING = Ring(41.27277, -95.50833, 3281)
CX, CY = RING.center_x, RING.center_y


def square(dx: float, dy: float, half: float) -> Polygon:
    return Polygon([(CX + dx - half, CY + dy - half), (CX + dx + half, CY + dy - half),
                    (CX + dx + half, CY + dy + half), (CX + dx - half, CY + dy + half)])


def parcel(pin: str, geom, district: str = "A-2", dwelling: float = 0.0) -> Parcel:
    p = Parcel(pin=pin, owner=f"OWNER {pin}", geometry=geom, dwelling_value=dwelling)
    p.districts = [District(code=district, jurisdiction="Pottawattamie County",
                            source="both", evidence_ids=[])]
    return p


# -- geometry --------------------------------------------------------------

def test_ring_area_matches_pi_r_squared():
    assert RING.verify() == []
    assert RING.polygon.area == pytest.approx(math.pi * 3281 ** 2, rel=1e-3)


def test_ring_rejects_bad_input():
    with pytest.raises(ValueError):
        Ring(91.0, 0.0, 1000)
    with pytest.raises(ValueError):
        Ring(41.0, -95.0, 0)


# -- tower -----------------------------------------------------------------

@pytest.mark.parametrize("rad,height,ttype", [
    (131, 135, TowerType.MONOPOLE),
    (150, 150, TowerType.MONOPOLE),
    (151, 155, TowerType.SELF_SUPPORT),
    (270, 270, TowerType.SELF_SUPPORT),
    (275, 275, TowerType.GUYED),
    (450, 450, TowerType.GUYED),
])
def test_tower_type_boundaries(rad, height, ttype):
    h = overall_height_ft(rad)
    assert h == height
    assert tower_type_for(h) is ttype


def test_rounding_can_change_structure_type():
    """RAD 274 rounds up to 275, which crosses the guyed threshold. Real
    behaviour, and the reason the derivation is printed in the report."""
    assert overall_height_ft(274) == 275
    assert tower_type_for(overall_height_ft(274)) is TowerType.GUYED
    assert tower_type_for(274) is TowerType.SELF_SUPPORT


def test_guy_radius_only_for_guyed():
    led = Ledger()
    e = led.from_input(450, "RAD", "Requested RAD")
    assert derive(450, led, rad_evidence_id=e).guy_anchor_radius_ft == pytest.approx(360)
    assert derive(130, led, rad_evidence_id=e).guy_anchor_radius_ft is None


def test_ground_delta_raises_height():
    assert overall_height_ft(448, 7) == 455


# -- compound fitting ------------------------------------------------------

def test_compound_fits_in_large_parcel():
    region = buildable_region(square(0, 0, 1000), RING.polygon, setback_ft=75)
    assert best_compound_fit(region, 100, 80).size_ft == 100


def test_falls_back_to_exception_size():
    """A 90x90 parcel cannot take 100x100 but can take the 80x80 exception."""
    region = buildable_region(square(0, 0, 45), RING.polygon)
    fit = best_compound_fit(region, 100, 80)
    assert fit.fits and fit.size_ft == 80


def test_too_small_for_either_tier():
    region = buildable_region(square(0, 0, 35), RING.polygon)
    assert not best_compound_fit(region, 100, 80).fits


def test_compound_must_sit_inside_ring():
    """A parcel mostly outside the ring only counts for its in-ring part."""
    far = square(RING.radius_ft + 400, 0, 500)
    region = buildable_region(far, RING.polygon)
    fit = fit_compound(region, 100)
    if fit.fits:
        assert RING.polygon.buffer(1).contains(Point(*fit.center))


def test_wetland_removes_buildable_area():
    wet = square(0, 0, 400)
    region = buildable_region(square(0, 0, 420), RING.polygon, 0, [wet])
    assert not best_compound_fit(region, 100, 80).fits


# -- zoning ----------------------------------------------------------------

def test_prohibited_use_stops_before_geometry():
    """A-1 addresses towers in neither its permitted nor its conditional use
    list, so the use is genuinely unavailable there and geometry is moot."""
    led = Ledger()
    e = led.from_input(450, "RAD", "Requested RAD")
    tower = derive(450, led, rad_evidence_id=e)
    p = evaluate_parcel(parcel("P", square(0, 0, 2000), "A-1"), RING, tower, led)
    assert p.disposition is Disposition.DO_NOT_PURSUE
    assert RC_PROHIBITED in p.reason_codes
    assert p.buildable_sqft is None  # geometry was never computed


def test_district_two_tier_structure():
    """The ordinance pairs a principal use up to a height with a conditional use
    above it. An earlier reading treated the by-right cap as a ceiling and
    marked residential districts prohibited above 100 ft, which wrongly excluded
    18 parcels on TI-OPP-43986 that the human audit had carried forward."""
    from da.models import UsePermission
    from da.zoning.pottawattamie import evaluate

    # Below the cap: by right.
    assert evaluate("A-2", 450)[1] is ProcessType.BUILDING_PERMIT
    assert evaluate("R-2", 95)[1] is ProcessType.BUILDING_PERMIT
    # Above it: conditional, not prohibited.
    assert evaluate("A-2", 510)[1] is ProcessType.CONDITIONAL_USE
    for d in ("R-1", "R-2", "R-3"):
        permission, process, _ = evaluate(d, 215)
        assert permission is UsePermission.CONDITIONAL
        assert process is ProcessType.CONDITIONAL_USE
    # A-4 is conditional at any height, with no by-right tier.
    assert evaluate("A-4", 450)[1] is ProcessType.CONDITIONAL_USE


def test_districts_with_no_clause_above_the_cap_are_unknown():
    """C-1, C-2, I-1 and I-2 cap the by-right use and provide no conditional
    clause above it. Silence is neither permission nor refusal."""
    from da.models import UsePermission
    from da.zoning.pottawattamie import evaluate
    for d, cap in (("C-1", 150), ("C-2", 150), ("I-1", 200), ("I-2", 200)):
        assert evaluate(d, cap - 10)[1] is ProcessType.BUILDING_PERMIT
        assert evaluate(d, cap + 50)[0] is UsePermission.UNKNOWN
    # C-3 is the exception: it does have a conditional clause.
    assert evaluate("C-3", 215)[1] is ProcessType.CONDITIONAL_USE


def test_districts_without_any_tower_clause_need_a_text_amendment():
    from da.models import UsePermission
    from da.zoning.pottawattamie import evaluate
    for d in ("A-1", "R-5", "R-6"):
        permission, process, _ = evaluate(d, 150)
        assert permission is UsePermission.PROHIBITED
        assert process is ProcessType.TEXT_AMENDMENT


def test_section_aware_verification_rejects_a_misattributed_quote():
    """A quote must appear at the section it is cited to, not merely somewhere
    in the ordinance. Without this, invented section numbers passed because
    identical wording exists in several district chapters."""
    from da.zoning.snapshot import ingest_file, verify_quote
    if not os.path.exists(ORDINANCE):
        pytest.skip("ordinance text not present")
    snap = ingest_file(ORDINANCE, "Pottawattamie County", source_type="supplied",
                       directory="/tmp/da_snap_test")
    dwelling = "All towers shall be located a minimum distance of equal to or greater than"
    assert verify_quote(dwelling, snap, section="8.004.220.13")
    assert not verify_quote(dwelling, snap, section="8.010.070")
    assert verify_quote(dwelling, snap)  # still found without a section


def test_unknown_district_is_not_silently_permitted():
    from da.zoning.pottawattamie import evaluate
    from da.models import UsePermission
    assert evaluate("ZZ", 200)[0] is UsePermission.UNKNOWN


def test_zoning_levels_follow_the_43986_definition():
    """TI-OPP-43986 (2026-05-02) states level 3 as 'site plan review with meeting
    or typical CUP' and level 4 as 'variance, rezoning, text amendment, or
    difficult CUP process'. A routine CUP is therefore 3, not 4."""
    assert ZoningLevel.from_process(ProcessType.NONE) == 1
    assert ZoningLevel.from_process(ProcessType.BUILDING_PERMIT) == 2
    assert ZoningLevel.from_process(ProcessType.ADMIN_REVIEW, needs_counsel=True) == 3
    assert ZoningLevel.from_process(ProcessType.CONDITIONAL_USE) == 3
    assert ZoningLevel.from_process(ProcessType.SPECIAL_USE) == 3
    # A CUP that needs counsel is the "difficult CUP process" of level 4.
    assert ZoningLevel.from_process(ProcessType.CONDITIONAL_USE,
                                    needs_counsel=True) == 4
    assert ZoningLevel.from_process(ProcessType.VARIANCE) == 4
    assert ZoningLevel.from_process(ProcessType.TEXT_AMENDMENT) == 4
    with pytest.raises(ValueError):
        ZoningLevel(5, "nope")


def test_hiring_team_definition_available_via_switch():
    """The hiring team described level 4 as covering every CUP. One flag
    restores that reading, so the disagreement is configuration, not a rewrite."""
    assert ZoningLevel.from_process(ProcessType.CONDITIONAL_USE,
                                    cup_is_level_4=True) == 4
    assert ZoningLevel.from_process(ProcessType.BUILDING_PERMIT,
                                    cup_is_level_4=True) == 2


def test_level_descriptions_are_complete():
    for lvl in (1, 2, 3, 4):
        assert ZoningLevel(lvl, "r").description


# -- quote verification ----------------------------------------------------

ORDINANCE = os.path.join(os.path.dirname(__file__), "..", "data", "ordinance.txt")


@pytest.mark.skipif(not os.path.exists(ORDINANCE), reason="ordinance text not present")
def test_fabricated_quote_is_rejected():
    from da.zoning.snapshot import ingest_file, verify_quote
    snap = ingest_file(ORDINANCE, "Pottawattamie County", source_type="supplied",
                       directory="/tmp/da_snap_test")
    assert verify_quote("All towers shall be located a minimum distance", snap)
    assert not verify_quote("Towers shall not exceed three hundred (300) feet.", snap)


@pytest.mark.skipif(not os.path.exists(ORDINANCE), reason="ordinance text not present")
def test_smart_quotes_still_match():
    """The ordinance uses a curly apostrophe in "manufacturer's"; a straight one
    in the rule definition must still match."""
    from da.zoning.snapshot import ingest_file, verify_quote
    snap = ingest_file(ORDINANCE, "Pottawattamie County", source_type="supplied",
                       directory="/tmp/da_snap_test")
    assert verify_quote("equal to the manufacturer's designed fall distance", snap)


@pytest.mark.skipif(not os.path.exists(ORDINANCE), reason="ordinance text not present")
def test_all_rules_verify():
    from da.zoning.pottawattamie import build_rules
    from da.zoning.snapshot import ingest_file
    snap = ingest_file(ORDINANCE, "Pottawattamie County", source_type="supplied",
                       directory="/tmp/da_snap_test")
    rules, warnings = build_rules(snap)
    assert warnings == []
    assert len(rules) > 25


# -- evidence ledger -------------------------------------------------------

def test_document_evidence_requires_a_quote():
    led = Ledger()
    with pytest.raises(ValueError):
        led.add("x", SourceType.DOCUMENT, "no quote", url="http://e.com")


def test_computed_evidence_must_name_its_parents():
    led = Ledger()
    with pytest.raises(ValueError):
        led.computed(5, "from nowhere", [])
    with pytest.raises(MissingEvidence):
        led.computed(5, "from a ghost", ["E999"])


def test_unsourced_claim_is_caught():
    led = Ledger()
    led.from_input(1, "a", "col")
    with pytest.raises(MissingEvidence):
        led.require(["E1", "E42"])


# -- input validation ------------------------------------------------------

def test_blank_latitude_is_rejected():
    row = {"Tinlam Opportunity ID": "TI-OPP-34620", "Original Site Latitude": "",
           "Original Site Longitude": "-95.5", "Requested RAD": "300",
           "Search Ring Radius": "3281", "State": "IA"}
    with pytest.raises(InputError, match="latitude"):
        parse_row(row)


def test_implausible_rad_is_rejected():
    row = {"Tinlam Opportunity ID": "X", "Original Site Latitude": "41.2",
           "Original Site Longitude": "-95.5", "Requested RAD": "9999",
           "Search Ring Radius": "3281", "State": "IA"}
    with pytest.raises(InputError, match="RAD"):
        parse_row(row)


def test_positive_longitude_in_iowa_is_rejected():
    row = {"Tinlam Opportunity ID": "X", "Original Site Latitude": "41.2",
           "Original Site Longitude": "95.5", "Requested RAD": "300",
           "Search Ring Radius": "3281", "State": "IA"}
    with pytest.raises(InputError, match="longitude"):
        parse_row(row)


# -- lettering and consistency ---------------------------------------------

def test_letters_run_clockwise_from_north():
    ps = [parcel("S", square(0, -2000, 300)), parcel("N", square(0, 2000, 300)),
          parcel("E", square(2000, 0, 300)), parcel("W", square(-2000, 0, 300))]
    ordered = assign_letters(ps, RING)
    assert [p.pin for p in ordered] == ["N", "E", "S", "W"]


def test_consistency_catches_missing_disposition():
    p = parcel("P", square(0, 0, 500))
    p.letter = "A"
    report = AuditReport(site=SiteInput("X", 41.27, -95.5, 3281, 450, "IA"),
                         parcels=[p])
    problems = check_report(report, RING)
    assert any("no disposition" in x for x in problems)
    assert any("no tower specification" in x for x in problems)


def test_consistency_catches_unsourced_finding():
    from da.models import Finding
    led = Ledger()
    e = led.from_input(450, "RAD", "Requested RAD")
    tower = derive(450, led, rad_evidence_id=e)
    p = parcel("P", square(0, 0, 2000))
    p.letter = "A"
    p.disposition = Disposition.PURSUE
    p.findings.append(Finding("claims something", ["E999"]))
    report = AuditReport(site=SiteInput("X", 41.27, -95.5, 3281, 450, "IA"),
                         parcels=[p], tower=tower, evidence=led.all())
    assert any("unknown evidence" in x for x in check_report(report, RING))


# -- end to end ------------------------------------------------------------

def test_guyed_tower_needs_a_large_parcel():
    """At 450 ft the fall-distance setback is 450 ft, so a parcel must be well
    over 900 ft across before anything is buildable. The binding constraint is
    the setback, not the anchors: once the base sits 450 ft inside the boundary
    a 360 ft anchor circle is already clear of it."""
    led = Ledger()
    e = led.from_input(450, "RAD", "Requested RAD")
    tower = derive(450, led, rad_evidence_id=e)

    # 800 x 800 ft: the 450 ft setback erodes it to nothing.
    small = evaluate_parcel(parcel("S", square(900, -900, 400)), RING, tower, led)
    assert small.disposition is Disposition.DO_NOT_PURSUE
    assert RC_TOO_SMALL in small.reason_codes

    big = evaluate_parcel(parcel("B", square(0, 0, 1000)), RING, tower, led)
    assert big.disposition is Disposition.PURSUE
    assert RC_OK in big.reason_codes


def test_monopole_is_far_less_demanding():
    """The same small parcel passes for a 135 ft monopole: no guy anchors and a
    135 ft setback instead of 450."""
    led = Ledger()
    e = led.from_input(131, "RAD", "Requested RAD")
    tower = derive(131, led, rad_evidence_id=e)
    p = evaluate_parcel(parcel("S", square(900, -900, 600)), RING, tower, led)
    assert p.disposition is Disposition.PURSUE


def test_unresolved_entries_are_complete():
    """Every Unresolved must answer all three questions."""
    led = Ledger()
    e = led.from_input(450, "RAD", "Requested RAD")
    tower = derive(450, led, rad_evidence_id=e)
    p = Parcel(pin="NO-DISTRICT", geometry=square(0, 0, 1000))
    evaluate_parcel(p, RING, tower, led)
    assert p.disposition is Disposition.UNRESOLVED
    for u in p.unresolved:
        assert u.what_is_missing and u.why_it_matters and u.how_to_resolve


# -- Council Bluffs --------------------------------------------------------

def test_cb_permits_towers_in_every_district_up_to_200ft():
    """CBMC 15.31.020 permits towers in all districts up to 200 ft, so no
    Council Bluffs parcel is ever excluded on use grounds."""
    from da.models import UsePermission
    from da.zoning.council_bluffs import evaluate
    for district in ("R-1", "R-3", "A-2", "C-2", "M-1"):
        permission, process, _ = evaluate(district, 135)
        assert permission is UsePermission.CONDITIONAL
        assert process is ProcessType.CONDITIONAL_USE


def test_cb_above_200ft_is_unresolved_not_prohibited():
    """The chapter has no clause above 200 ft. Silence is not a refusal."""
    from da.models import UsePermission
    from da.zoning.council_bluffs import evaluate
    permission, process, why = evaluate("R-1", 215)
    assert permission is UsePermission.UNKNOWN
    assert process is ProcessType.UNKNOWN
    assert "undetermined rather than prohibited" in why


def test_cb_setback_is_full_height_to_a_dwelling():
    from da.zoning.council_bluffs import setback_ft
    assert setback_ft(135) == 135.0
    assert setback_ft(135, adjoins_residential_dwelling=False) == 20.0


def test_cb_every_site_is_at_least_level_3():
    """There is no by-right path at any height in Council Bluffs."""
    from da.zoning.council_bluffs import evaluate
    for h in (50, 120, 199, 200):
        _, process, _ = evaluate("R-1", h)
        assert ZoningLevel.from_process(process) >= 3


CB_TEXT = os.path.join(os.path.dirname(__file__), "..", "data",
                       "council_bluffs_15.31.txt")


@pytest.mark.skipif(not os.path.exists(CB_TEXT), reason="CB working text absent")
def test_cb_rules_all_verify():
    from da.zoning.council_bluffs import build_rules
    from da.zoning.snapshot import ingest_file
    from da.zoning.council_bluffs import ORDINANCE_URL
    snap = ingest_file(CB_TEXT, "Council Bluffs", url=ORDINANCE_URL,
                       source_type="retrieved", directory="/tmp/da_snap_test")
    rules, warnings = build_rules(snap)
    assert warnings == []
    assert len(rules) == 11
    assert snap.is_verifiable


def test_cb_height_is_measured_to_the_antenna_tip():
    """15.31.050 measures height from natural undisturbed ground to the tip of
    the highest antenna, not to the RAD centreline. Neither human audit records
    this, and it pushes the regulated height above the requested RAD."""
    from da.zoning.council_bluffs import build_rules, ORDINANCE_URL
    from da.zoning.snapshot import ingest_file
    snap = ingest_file(CB_TEXT, "Council Bluffs", url=ORDINANCE_URL,
                       source_type="retrieved", directory="/tmp/da_snap_test")
    rules, _ = build_rules(snap)
    rule = next(r for r in rules if r.kind == "height_measurement")
    assert "natural undisturbed" in rule.quote
    assert "highest antenna" in rule.quote


# -- City of Oakland -------------------------------------------------------

OAK_TEXT = os.path.join(os.path.dirname(__file__), "..", "data", "oakland_zoning.txt")


def test_oakland_m1_is_by_right():
    from da.models import UsePermission
    from da.zoning.oakland import evaluate
    permission, process, _ = evaluate("M-1", 215)
    assert permission is UsePermission.PERMITTED
    assert process is ProcessType.BUILDING_PERMIT


def test_oakland_path_outside_m1_is_genuinely_open():
    """The 43986 human audit concludes a text amendment is required and rates
    Oakland level 4. Section 5(A) item 7 lists "Radio tower or broadcasting
    station" as a special use, so a different path exists if a wireless tower
    qualifies. The ordinance does not say, so the pipeline refuses to pick."""
    from da.models import UsePermission
    from da.zoning.oakland import evaluate
    unknown = evaluate("AG", 215)
    assert unknown[0] is UsePermission.UNKNOWN
    assert unknown[1] is ProcessType.UNKNOWN

    qualifies = evaluate("AG", 215, radio_tower_qualifies=True)
    assert qualifies[1] is ProcessType.SPECIAL_USE
    assert ZoningLevel.from_process(qualifies[1]) == 3

    does_not = evaluate("AG", 215, radio_tower_qualifies=False)
    assert does_not[1] is ProcessType.TEXT_AMENDMENT
    assert ZoningLevel.from_process(does_not[1]) == 4


def test_oakland_c1_excludes_towers():
    """C-1 item 52 permits communication services 'except satellite stations and
    towers', so C-1 is not a route even though it names the use."""
    from da.models import UsePermission
    from da.zoning.oakland import evaluate
    assert evaluate("C-1", 215)[0] is not UsePermission.PERMITTED


def test_oakland_has_no_height_cap():
    """Section 6 exempts transmission and radio towers from the height
    regulations, so height never decides an Oakland parcel."""
    from da.zoning.oakland import evaluate
    for h in (120, 215, 450):
        assert evaluate("M-1", h)[0].value == "permitted"


@pytest.mark.skipif(not os.path.exists(OAK_TEXT), reason="Oakland extract absent")
def test_oakland_rules_verify():
    from da.zoning.oakland import ORDINANCE_URL, build_rules
    from da.zoning.snapshot import ingest_file
    snap = ingest_file(OAK_TEXT, "City of Oakland", url=ORDINANCE_URL,
                       source_type="retrieved", directory="/tmp/da_snap_test")
    rules, warnings = build_rules(snap)
    assert warnings == []
    assert len(rules) == 7


# -- Iowa Code Chapter 8C --------------------------------------------------

def test_8c_applies_only_in_iowa():
    from da.zoning.iowa_state import provisions_for_site
    assert provisions_for_site("IA")
    assert provisions_for_site("NE") == []
    assert provisions_for_site("") == []


def test_8c_records_references_not_quotations():
    """The Legislature publishes Chapter 8C only as PDF and RTF, so these are
    section citations rather than verified quotations, and the evidence says so
    rather than presenting them as quotes."""
    from da.evidence import Ledger
    from da.models import SourceType
    from da.zoning.iowa_state import record
    led = Ledger()
    ids = record(led, "IA")
    assert ids
    for i in ids:
        assert led[i].source_type is not SourceType.DOCUMENT
        assert led[i].quote is None


def test_8c_preemption_flags_are_questions_not_conclusions():
    from da.zoning.iowa_state import preemption_flags
    flags = preemption_flags({"separation", "collocation_statement", "height_cap"})
    kinds = [k for k, _ in flags]
    assert "separation" in kinds
    assert "height_cap" not in kinds       # not something 8C reaches
    for _, note in flags:
        assert any(w in note.lower() for w in ("counsel", "worth testing", "can "))


# -- regressions found on the first live run -------------------------------

def test_merged_owner_parcel_uses_the_union_not_the_largest_tract():
    """A candidate holding several adjoining tracts must be evaluated on all of
    them. Keeping only the largest understated buildable area and flipped a
    real parcel to Do not pursue on the first live run."""
    from da.sources.parcels import _merge_same_owner

    a = Parcel(pin="1", owner="ACME FARMS LLC", geometry=square(0, 0, 400),
               area_in_ring_sqft=square(0, 0, 400).area)
    b = Parcel(pin="2", owner="ACME FARMS LLC", geometry=square(800, 0, 400),
               area_in_ring_sqft=square(800, 0, 400).area)
    merged = _merge_same_owner([a, b])

    assert len(merged) == 1
    m = merged[0]
    assert "1" in m.pin and "2" in m.pin
    # Union area, not the larger of the two.
    assert m.geometry.area == pytest.approx(square(0, 0, 400).area * 2, rel=1e-6)
    assert m.area_in_ring_sqft == pytest.approx(m.geometry.area, rel=1e-6)


def test_guy_anchors_are_not_charged_the_tower_setback():
    """§8.004.220.12 measures the fall-distance setback from the base of the
    tower to the boundary. Applying it to the anchor circle as well rejected
    parcels the ordinance permits."""
    led = Ledger()
    e = led.from_input(450, "RAD", "Requested RAD")
    tower = derive(450, led, rad_evidence_id=e)
    # ~39 acres, the size the 35820 human audit carried forward.
    p = evaluate_parcel(parcel("P", square(0, 0, 650)), RING, tower, led)
    assert RC_GUY_ANCHORS not in p.reason_codes


def test_anchors_still_blocked_by_wetland():
    """Dropping the setback must not drop the wetland test: the DA training
    asks specifically whether anchors can be placed without entering wetland."""
    from da.geo.buildable import guy_anchors_fit
    big = square(0, 0, 1200)
    centre = (CX, CY)
    assert guy_anchors_fit(big, centre, 360.0)
    wet = square(300, 0, 150)
    assert not guy_anchors_fit(big, centre, 360.0, exclusions=[wet])


def test_identical_unresolved_items_collapse():
    """A missing municipal zoning layer produces the same finding on every
    parcel in that city (215 of them on TI-OPP-43986). The report groups them
    so the handful of findings a reviewer must act on stay visible."""
    from da.models import Unresolved
    from da.report.build import _collapse

    same = [Unresolved(
        what_is_missing=f"No zoning district layer is available for City of Oakland, "
                        f"so parcel {i} has no mapped district",
        why_it_matters="District sets the use permission.",
        how_to_resolve="Request the map from the city.",
    ) for i in range(215)]
    other = Unresolved("A different gap", "Different reason", "Different fix")

    rows = _collapse(same + [other])
    assert len(rows) == 2
    top, _u, n = rows[0]
    assert n == 215
    assert "215 parcels affected" in top
    assert "so parcel" not in top


def test_asr_member_lookup_is_case_insensitive():
    """The archive member is "CO.dat". Comparing n.upper() against a pattern
    ending ".dat" never matched, so the parser returned zero towers in two
    milliseconds and looked like a success."""
    import io
    import zipfile

    from da.sources.asr import _rows
    from da.sources.base import SourceError

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CO.dat", "CO|||| 1234567|x|41|16|22|N|95|30|30|W\n")
    buf.seek(0)
    with zipfile.ZipFile(buf) as zf:
        assert len(list(_rows(zf, "CO"))) == 1

    # A missing member must raise, not quietly yield nothing.
    empty = io.BytesIO()
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("readme.txt", "no data here")
    empty.seek(0)
    with zipfile.ZipFile(empty) as zf:
        with pytest.raises(SourceError, match="no CO.dat member"):
            list(_rows(zf, "CO"))


def test_ledger_ids_are_unique_under_concurrent_writes():
    """Sources are fetched on several threads and all of them record evidence.

    Without a lock around id minting, two records get the same id and one
    silently overwrites the other, so a citation resolves to the wrong source.
    """
    from concurrent.futures import ThreadPoolExecutor
    from da.evidence import Ledger

    ledger = Ledger()
    n = 400

    def record(i):
        return ledger.from_dataset(i, f"row {i}", url="test://", layer="0")

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(record, range(n)))

    assert len(set(ids)) == n
    assert len(ledger.all()) == n


def test_ag_exemption_only_fires_for_county_agricultural_land():
    """Iowa Code 335.2 binds counties, so a city parcel never raises it."""
    from da.zoning import iowa_state as ia

    assert ia.ag_exemption_in_play("IA", "Pottawattamie County", {"A-2", "R-1"})
    assert not ia.ag_exemption_in_play("IA", "Pottawattamie County", {"R-1", "C-2"})
    assert not ia.ag_exemption_in_play("IA", "City of Council Bluffs", {"A-2"})
    assert not ia.ag_exemption_in_play("NE", "Pottawattamie County", {"A-2"})
    assert not ia.ag_exemption_in_play("IA", None, {"A-2"})


def test_ag_exemption_is_recorded_as_non_blocking_with_its_basis():
    from da.evidence import Ledger
    from da.zoning import iowa_state as ia

    ledger = Ledger()
    ids = ia.record_ag_exemption(ledger, "IA", "Pottawattamie County", {"A-2"})
    assert len(ids) == 2
    records = ledger.all()
    # The conclusion must derive from the statute record, not stand alone.
    assert records[ids[1]].derived_from == [ids[0]]
    assert "335.2" in records[ids[0]].section

    note = ia.ag_exemption_unresolved("IA", "Pottawattamie County", {"A-2"})
    assert note is not None and not note.blocking
    assert "Kuehl" in note.why_it_matters


class _FakeRing:
    lat, lon = 41.2581, -95.8608
    radius_ft = 1500.0
    center_x = center_y = 0.0


def _stub_3dep(monkeypatch, *, ok=True, metres=True, drop=(), body=None):
    """Stand in for the 3DEP ImageServer, which tests must not call."""
    import json
    from types import SimpleNamespace
    import da.sources.elevation as E

    true_ft = 1020.0

    def post(url, data=None, timeout=None, headers=None):
        if not ok:
            raise RuntimeError("service down")
        n = len(json.loads(data["geometry"])["points"])
        if body is not None:
            out = body
        else:
            v = true_ft / E.METRES_TO_FT if metres else true_ft
            out = {"samples": [{"locationId": i, "value": f"{v:.4f}"}
                               for i in range(n) if i not in drop]}
        return SimpleNamespace(json=lambda: out, raise_for_status=lambda: None)

    monkeypatch.setattr(E.requests, "post", post)
    monkeypatch.setattr(E, "elevation_ft", lambda lat, lon, timeout=30: true_ft)
    monkeypatch.setattr(E, "point_to_wgs84", lambda x, y: (-95.8608, 41.2581))
    return E


def test_elevation_uses_one_batch_request_when_the_service_agrees(monkeypatch):
    from da.evidence import Ledger
    import da.sources.elevation as _E; _E._BATCH_FAILED = None
    E = _stub_3dep(monkeypatch)
    centre, stats = E.sample_ring(_FakeRing(), Ledger())
    assert round(centre) == 1020
    assert stats["n_samples"] == 17
    assert "ImageServer" in stats["method"]


def test_elevation_drops_nodata_samples_by_location_id(monkeypatch):
    from da.evidence import Ledger
    import da.sources.elevation as _E; _E._BATCH_FAILED = None
    E = _stub_3dep(monkeypatch, drop=(3, 7))
    _, stats = E.sample_ring(_FakeRing(), Ledger())
    assert stats["n_samples"] == 15


def test_elevation_discards_a_batch_in_the_wrong_units(monkeypatch):
    """The ImageServer returns metres and EPQS feet.

    If the batch ever comes back already in feet, converting it again
    overstates ground level by 3.28x, which lands straight on tower height.
    The centre cross-check must catch that and fall back.
    """
    from da.evidence import Ledger
    import da.sources.elevation as _E; _E._BATCH_FAILED = None
    E = _stub_3dep(monkeypatch, metres=False)
    centre, stats = E.sample_ring(_FakeRing(), Ledger())
    assert round(centre) == 1020
    assert "discarded" in stats["method"]


@pytest.mark.parametrize("kwargs", [
    {"ok": False},
    {"body": {"error": {"code": 400, "message": "bad"}}},
    {"body": {"samples": []}},
])
def test_elevation_falls_back_when_the_batch_is_unusable(monkeypatch, kwargs):
    from da.evidence import Ledger
    import da.sources.elevation as _E; _E._BATCH_FAILED = None
    E = _stub_3dep(monkeypatch, **kwargs)
    centre, stats = E.sample_ring(_FakeRing(), Ledger())
    assert round(centre) == 1020
    assert "point query" in stats["method"]


# ---------------------------------------------------------------------------
# Added in the pre-submission review. Each test pins a defect that was found
# by reading the output against the assignment, so it cannot quietly return.
# ---------------------------------------------------------------------------


def test_finding_cites_a_section_number_not_the_quotation():
    """The county finding once printed '(§Tower with a height not exceeding...)'.

    The district table is (cap, by-right section, by-right quote, conditional
    section, conditional quote) and the code read index 2, the quote.
    """
    from da.analysis.evaluate import _deciding_section
    from da.zoning import pottawattamie as pott

    entry = pott.TOWER_USE_BY_DISTRICT["R-1"]
    assert _deciding_section(entry, 80) == "8.015.020.09"     # by right, under 100 ft
    assert _deciding_section(entry, 180) == "8.015.030.10"    # conditional above it
    assert _deciding_section(pott.TOWER_USE_BY_DISTRICT["A-4"], 60) == "8.014.030.15"
    assert _deciding_section(None, 100) is None
    for district, e in pott.TOWER_USE_BY_DISTRICT.items():
        sec = _deciding_section(e, 120)
        assert sec and sec[0].isdigit(), f"{district}: {sec!r} is not a section number"


def test_asr_status_separates_standing_towers_from_paper_ones():
    """Status letters as the FCC archive actually uses them.

    C constructed, G granted, A cancelled, I dismantled, T terminated. A grant
    that carries a construction date is a modification to a standing tower.
    """
    from da.sources.asr import Tower, summarise

    def t(status, built=None, gone=None, miles=1.0):
        return Tower("1", 41.0, -95.0, 300.0, "TOWER", "X", miles * 5280.0,
                     status=status, date_constructed=built, date_dismantled=gone)

    assert t("C", "01/01/2000").category == "constructed"
    assert t("G").category == "granted" and not t("G").is_constructed
    assert t("G", "01/01/2000").is_constructed
    assert t("G", "01/01/2000").status_label == "constructed, modification granted"
    assert t("I", "01/01/1990", "01/01/2010").category == "inactive"
    assert t("A").category == "inactive" and t("A").status_label == "cancelled"
    assert t("T", "01/01/1990").category == "inactive"
    assert t(None).category == "unknown"

    c = summarise([t("C", "x", miles=0.2), t("C", "x"), t("G"), t("I", "x", "y"),
                   t("T", "x"), t("A")])
    assert c["constructed_within_2mi"] == 2
    assert c["constructed_within_0.5mi"] == 1
    assert c["granted_not_built_within_2mi"] == 1
    assert c["inactive_within_2mi"] == 3


def test_zero_towers_is_still_recorded_as_a_finding():
    from da.sources.asr import _record
    ledger = Ledger()
    eid = _record(ledger, [], 3)
    assert ledger.all()[eid].value["registrations_within_2mi"] == 0


def test_permanent_arcgis_errors_are_not_retried(monkeypatch):
    """'Layer not found' will not change on a second attempt.

    Retrying it with backoff cost seven seconds per call and taught nothing.
    """
    from types import SimpleNamespace
    from da.sources import base

    calls = {"n": 0}

    def get(url, params=None, timeout=None, headers=None):
        calls["n"] += 1
        return SimpleNamespace(
            status_code=200, raise_for_status=lambda: None,
            json=lambda: {"error": {"code": 404, "message": "Layer not found"}})

    monkeypatch.setattr(base.requests, "get", get)
    monkeypatch.setattr(base.time, "sleep", lambda s: pytest.fail("slept on a 404"))
    with pytest.raises(base.SourceError, match="Layer not found"):
        base.http_json("https://example.test/FeatureServer/0", {"f": "json"})
    assert calls["n"] == 1


def test_transient_failures_are_still_retried(monkeypatch):
    from types import SimpleNamespace
    from da.sources import base

    calls = {"n": 0}

    def get(url, params=None, timeout=None, headers=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionResetError(10054, "reset")
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                               json=lambda: {"ok": True})

    monkeypatch.setattr(base.requests, "get", get)
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    assert base.http_json("https://example.test/x") == {"ok": True}
    assert calls["n"] == 3


def test_release_sheet_loads_from_xlsx(tmp_path):
    """The assignment supplies the tranche as a workbook."""
    openpyxl = pytest.importorskip("openpyxl")
    from da.cli import read_sheet

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Tinlam Opportunity ID", "State", "Original Site Latitude",
               "Original Site Longitude", "Requested RAD", "Search Ring Radius"])
    ws.append(["TI-OPP-1", "IA", 41.2581, -95.8608, 180, 1500])
    ws.append([None, None, None, None, None, None])
    path = tmp_path / "release.xlsx"
    wb.save(path)

    rows = read_sheet(str(path))
    assert len(rows) == 1
    site = parse_row(rows[0])
    assert (site.opportunity_id, site.latitude, site.requested_rad_ft,
            site.search_ring_radius_ft) == ("TI-OPP-1", 41.2581, 180.0, 1500.0)


def test_basemap_stitches_tiles_and_reports_its_extent(monkeypatch):
    import io
    from types import SimpleNamespace
    PIL = pytest.importorskip("PIL.Image")
    from da.sources import basemap

    asked = []

    def get(url, timeout=None, headers=None):
        asked.append(url)
        buf = io.BytesIO()
        PIL.new("RGB", (256, 256), (10, 120, 60)).save(buf, format="PNG")
        return SimpleNamespace(status_code=200, content=buf.getvalue(),
                               raise_for_status=lambda: None)

    monkeypatch.setattr(basemap, "_FAILED", {})
    monkeypatch.setattr(basemap.requests, "get", get)
    m = basemap.fetch("topo", -95.8608, 41.2581, 2400.0)
    assert m is not None and asked
    assert all("/USGSTopo/MapServer/tile/" in u for u in asked)
    cx, cy = basemap.lonlat_to_mercator(-95.8608, 41.2581)
    xmin, xmax, ymin, ymax = m.extent
    assert xmin < cx < xmax and ymin < cy < ymax          # the ring centre is on the sheet
    assert m.image.size[0] % 256 == 0 and m.image.size[0] <= 256 * 5


def test_basemap_failure_is_remembered_and_never_raises(monkeypatch):
    from da.sources import basemap

    calls = {"n": 0}

    def get(url, timeout=None, headers=None):
        calls["n"] += 1
        raise ConnectionError("no route")

    monkeypatch.setattr(basemap, "_FAILED", {})
    monkeypatch.setattr(basemap.requests, "get", get)
    assert basemap.fetch("topo", -95.86, 41.25, 2400.0) is None
    assert basemap.last_failure("topo")
    before = calls["n"]
    assert basemap.fetch("topo", -95.86, 41.25, 2400.0) is None
    assert calls["n"] == before          # a repeat costs nothing


def test_levels_are_given_per_jurisdiction_on_both_scales():
    """A ring across the city limit has two answers, and two scales are in use."""
    from da.pipeline import _jurisdiction_levels
    from da.zoning import council_bluffs as cb, pottawattamie as pott

    ledger = Ledger()
    tower = derive(180, ledger, rad_evidence_id=ledger.from_input(180, "RAD", "RAD"))
    a = parcel("1", square(0, 0, 600), "A-2")
    a.districts[0].jurisdiction = pott.JURISDICTION
    b = parcel("2", square(1500, 0, 300), "R-1")
    b.districts[0].jurisdiction = cb.JURISDICTION
    report = AuditReport(site=SiteInput("X", 41.27, -95.5, 3281, 180, state="IA"),
                         tower=tower, parcels=[a, b],
                         jurisdictions=[cb.JURISDICTION, pott.JURISDICTION])
    rows = {(r["jurisdiction"], r["district"]): r for r in _jurisdiction_levels(report)}

    county = rows[(pott.JURISDICTION, "A-2")]
    assert county["permission"] == "permitted"
    assert (county["level_assignment"], county["level_audit"]) == (2, 2)

    city = rows[(cb.JURISDICTION, "R-1")]
    assert city["permission"] == "conditional"
    # The only place the two scales part company.
    assert (city["level_assignment"], city["level_audit"]) == (4, 3)


def test_a_city_with_no_mapped_district_still_gets_a_level_row():
    from da.pipeline import _jurisdiction_levels

    ledger = Ledger()
    tower = derive(215, ledger, rad_evidence_id=ledger.from_input(215, "RAD", "RAD"))
    p = parcel("9", square(0, 0, 300), "A-2")
    p.districts = []
    p.jurisdiction = "Oakland"
    report = AuditReport(site=SiteInput("X", 41.3, -95.38, 3281, 215, state="IA"),
                         tower=tower, parcels=[p], jurisdictions=["Oakland"])
    rows = _jurisdiction_levels(report)
    assert len(rows) == 1 and rows[0]["district"] is None
    assert rows[0]["level_assignment"] is None and "undetermined" in rows[0]["why"]


def test_cb_attribute_district_is_not_filed_under_the_county():
    """A city parcel with no polygon must not be judged by the county's code."""
    from da.sources.zoning import _no_spatial_district

    p = parcel("5", square(0, 0, 200), "A-2")
    p.districts = []
    _no_spatial_district(p, {"rural": None, "council_bluffs": "R-1"}, Ledger())
    assert p.districts[0].jurisdiction == "Council Bluffs"

    q = parcel("6", square(0, 0, 200), "A-2")
    q.districts = []
    _no_spatial_district(q, {"rural": "A-3", "council_bluffs": None}, Ledger())
    assert q.districts[0].jurisdiction == "Pottawattamie County"


def test_report_shows_only_rules_for_jurisdictions_in_the_ring(tmp_path, monkeypatch):
    """End to end on stubbed sources: structure, relevance and no stray text.

    A county-only ring once printed every Council Bluffs and Oakland provision
    under 'Ordinance provisions applied'.
    """
    import importlib.util
    import docx

    root = os.path.join(os.path.dirname(__file__), "..")
    spec = importlib.util.spec_from_file_location(
        "smoke_offline", os.path.join(os.path.dirname(__file__), "smoke_offline.py"))
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)

    from da import pipeline
    from da.report.build import build
    from da.sources import basemap

    monkeypatch.chdir(root)
    for target, name, fake in (
            (pipeline.parcels_src, "fetch", smoke.fake_parcels),
            (pipeline.zoning_src, "fetch_districts", smoke.fake_districts),
            (pipeline.ctx, "wetlands", smoke.fake_wetlands),
            (pipeline.ctx, "flood_zones", smoke.fake_flood),
            (pipeline.ctx, "dwellings", smoke.fake_dwellings),
            (pipeline.ctx, "municipal_boundaries", smoke.fake_boundaries),
            (pipeline.ctx, "overlays", smoke.fake_overlays),
            (pipeline.ctx, "electric_providers", smoke.fake_electric),
            (pipeline.ctx, "zoning_cases", smoke.fake_cases),
            (pipeline.elev, "sample_ring", smoke.fake_elevation),
            (pipeline.asr_src, "towers_near", smoke.fake_towers),
            (basemap, "fetch", smoke.fake_basemap)):
        monkeypatch.setattr(target, name, fake)

    report, ledger = pipeline.run(smoke.SITE, out_dir=str(tmp_path),
                                  ordinance_path="data/ordinance.txt",
                                  log=lambda *_: None)
    out = build(report, ledger, str(tmp_path / "audit.docx"),
                assumptions=report.assumptions)
    doc = docx.Document(out)
    text = "\n".join(p.text for p in doc.paragraphs)
    cells = "\n".join(c.text for t in doc.tables for r in t.rows for c in r.cells)

    for heading in ("Recommendation", "Summary Area Review", "Zoning Requirements",
                    "Parcel Data and Dispositions", "Existing Towers (FCC ASR)",
                    "Flood Plain Map", "Wetlands Map", "Topo Map",
                    "Street and Aerial Overview", "Zoning Map", "Parcel Map",
                    "Appendix: Provenance"):
        assert heading in text, f"missing section: {heading}"
    assert "[X] Proceed to SCIP" in text
    # every map the assignment lists was rendered and embedded
    assert set(report.map_paths) >= {"topo", "street", "parcel", "zoning", "asr",
                                     "flood", "wetlands"}
    assert len(doc.inline_shapes) >= 7
    # relevance: a county-only ring carries no city provisions
    assert "15.31" not in cells and "Council Bluffs" not in cells
    # the citation defect
    assert "(§Tower" not in text
    # an unresolved parcel says what is missing, where the reader will look
    assert "[UNRESOLVED] Missing:" in text
    # status-aware tower wording
    assert "granted but not built" in text


def test_a_missing_tile_steps_down_and_falls_back(monkeypatch):
    """USGSImageryTopo returned 404 at zoom 15 over the county; USGSTopo did not.

    A missing tile must drop to a coarser level, then to the second service,
    and must never cost the other kind of map.
    """
    import io, re
    from types import SimpleNamespace
    PIL = pytest.importorskip("PIL.Image")
    from da.sources import basemap

    def png():
        buf = io.BytesIO()
        PIL.new("RGB", (256, 256), (90, 90, 90)).save(buf, format="PNG")
        return buf.getvalue()

    asked = []

    def get(url, timeout=None, headers=None):
        asked.append(url)
        z = int(re.search(r"/tile/(\d+)/", url).group(1))
        # ImageryTopo has nothing cached here; ImageryOnly stops at zoom 14
        if "USGSImageryTopo" in url or ("USGSImageryOnly" in url and z > 14):
            return SimpleNamespace(status_code=404, content=b"",
                                   raise_for_status=lambda: None)
        return SimpleNamespace(status_code=200, content=png(),
                               raise_for_status=lambda: None)

    monkeypatch.setattr(basemap, "_FAILED", {})
    monkeypatch.setattr(basemap.requests, "get", get)
    street = basemap.fetch("street", -95.8608, 41.2581, 2400.0)
    assert street is not None and "USGSImageryOnly" in street.url
    assert street.zoom == 14 and street.note
    topo = basemap.fetch("topo", -95.8608, 41.2581, 2400.0)
    assert topo is not None and topo.note is None
