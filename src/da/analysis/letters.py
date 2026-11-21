"""Parcel lettering and run-level consistency checks.

Lettering follows the DA training: letters run geographically rather than in
whatever order the data arrived, and parcels with the same owner share a letter.
The training also says every parcel in the ring must be accounted for, shaded or
labelled, which is what check_all_accounted enforces.
"""

from __future__ import annotations

import math
import string
from typing import Any

from ..models import AuditReport, Disposition, Parcel, Unresolved


def assign_letters(parcels: list[Parcel], ring) -> list[Parcel]:
    """Letter parcels clockwise from north, by centroid bearing from the ring
    centre. Ties break on distance from the centre, so a parcel nearer the
    middle is lettered first."""
    def bearing(p: Parcel) -> tuple[float, float]:
        c = p.geometry.centroid
        dx, dy = c.x - ring.center_x, c.y - ring.center_y
        # atan2(dx, dy) gives 0 at north, increasing clockwise.
        ang = math.degrees(math.atan2(dx, dy)) % 360.0
        return (ang, math.hypot(dx, dy))

    ordered = sorted(parcels, key=bearing)
    for i, p in enumerate(ordered):
        p.letter = _letter(i)
    return ordered


def _letter(i: int) -> str:
    """A..Z then AA, AB, ... for rings with more than 26 parcels."""
    if i < 26:
        return string.ascii_uppercase[i]
    first, second = divmod(i - 26, 26)
    return string.ascii_uppercase[first] + string.ascii_uppercase[second]


def check_report(report: AuditReport, ring) -> list[str]:
    """Run-level assertions. Returns a list of problems, empty when clean.

    These run as the last task in every job. A report that fails them is still
    produced, with the problems printed at the top, because a visible failure is
    more useful than a silent one.
    """
    problems: list[str] = []

    for p in report.parcels:
        if p.disposition is None:
            problems.append(f"parcel {p.letter or p.pin} has no disposition")
        if p.letter is None:
            problems.append(f"parcel {p.pin} was never lettered")
        if p.disposition is Disposition.DO_NOT_PURSUE and not p.reason_codes:
            problems.append(f"parcel {p.letter} is Do not pursue with no reason code")
        if p.disposition is Disposition.UNRESOLVED and not p.unresolved:
            problems.append(f"parcel {p.letter} is Unresolved but says nothing about "
                            f"what is missing")
        for u in p.unresolved:
            if not (u.what_is_missing and u.why_it_matters and u.how_to_resolve):
                problems.append(f"parcel {p.letter} has an incomplete Unresolved entry")

    letters = [p.letter for p in report.parcels if p.letter]
    if len(set(letters)) != len(letters):
        problems.append("duplicate parcel letters")

    # Every claim must resolve to a recorded piece of evidence.
    for p in report.parcels:
        for f in p.findings:
            missing = [e for e in f.evidence_ids if e not in report.evidence]
            if missing:
                problems.append(f"parcel {p.letter} finding cites unknown evidence "
                                f"{missing}: {f.statement[:60]}")

    if report.tower is None:
        problems.append("no tower specification was derived")

    return problems


def check_all_accounted(parcels: list[Parcel], ring, tolerance: float = 0.05) -> list[str]:
    """Do the lettered parcels cover the ring? A large unaccounted share usually
    means the parcel layer is incomplete for this area, which would quietly
    shrink the candidate list."""
    from shapely.ops import unary_union

    if not parcels:
        return ["no parcels were found in the ring"]
    covered = unary_union([p.geometry.intersection(ring.polygon) for p in parcels])
    share = covered.area / ring.polygon.area if ring.polygon.area else 0.0
    if share < 1.0 - tolerance:
        return [f"lettered parcels cover only {share:.0%} of the ring; "
                f"{1 - share:.0%} is unaccounted for. Check the parcel layer for gaps."]
    return []


def candidate_target_note(report: AuditReport) -> str:
    """The DA training sets a goal of three candidates per ring, or a documented
    reason why that was not possible."""
    n = len(report.pursue_parcels())
    if n >= report.CANDIDATE_TARGET:
        return f"{n} candidate parcels identified, meeting the target of " \
               f"{report.CANDIDATE_TARGET}."
    blockers: dict[str, int] = {}
    for p in report.parcels:
        if p.disposition is Disposition.PURSUE:
            continue
        for rc in p.reason_codes:
            blockers[rc] = blockers.get(rc, 0) + 1
    reasons = ", ".join(f"{k.replace('_', ' ')} ({v})"
                        for k, v in sorted(blockers.items(), key=lambda kv: -kv[1]))
    return (f"{n} candidate parcel(s) identified, short of the target of "
            f"{report.CANDIDATE_TARGET}. Parcels were excluded for: {reasons or 'none recorded'}.")
