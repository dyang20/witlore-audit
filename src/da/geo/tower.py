"""Tower height and structure type, derived from the requested RAD.

From the Tillman DA training:
  - RAD is the antenna centreline, adjusted for ground elevation difference.
  - Tower height is rounded up to the nearest 5 ft.
  - monopole up to 150 ft, self-support 151-274 ft, guyed 275 ft and above.
  - Guy wires are typically 80% of tower height.

The type thresholds came from a meeting summary rather than a written standard,
so they live in client_rules.yml and every report prints the values it used.
Type matters because ordinances treat types differently: Sergeant Bluff requires
750 ft separation for monopoles and 1,500 ft for guyed or lattice, and
Pottawattamie's camouflage rule (Sec. 8.004.220.11) is unmeetable by a guyed
tower, which falls back to the galvanized-or-painted minimum.
"""

from __future__ import annotations

import math
from typing import Optional

from ..evidence import Ledger
from ..models import TowerSpec, TowerType

DEFAULT_THRESHOLDS = {"monopole_max_ft": 150, "self_support_max_ft": 274}
DEFAULT_GUY_RATIO = 0.80
DEFAULT_ROUND_TO_FT = 5


def overall_height_ft(rad_ft: float, ground_delta_ft: float = 0.0,
                      round_to: int = DEFAULT_ROUND_TO_FT) -> float:
    """H = round_to * ceil((RAD + dz) / round_to)."""
    if rad_ft <= 0:
        raise ValueError(f"RAD must be positive, got {rad_ft}")
    raw = rad_ft + ground_delta_ft
    if round_to <= 0:
        return float(raw)
    return float(round_to * math.ceil(raw / round_to))


def tower_type_for(height_ft: float, thresholds: Optional[dict] = None) -> TowerType:
    t = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    if height_ft <= t["monopole_max_ft"]:
        return TowerType.MONOPOLE
    if height_ft <= t["self_support_max_ft"]:
        return TowerType.SELF_SUPPORT
    return TowerType.GUYED


def guy_anchor_radius_ft(height_ft: float, tower_type: TowerType,
                         ratio: float = DEFAULT_GUY_RATIO) -> Optional[float]:
    """Only guyed towers have anchors. At 450 ft the anchor circle is ~360 ft,
    roughly 9 acres, which dwarfs the compound and drives parcel selection."""
    if tower_type is not TowerType.GUYED:
        return None
    return float(height_ft * ratio)


def derive(rad_ft: float, ledger: Ledger, *, ground_delta_ft: float = 0.0,
           rad_evidence_id: Optional[str] = None,
           elevation_evidence_id: Optional[str] = None,
           thresholds: Optional[dict] = None,
           guy_ratio: float = DEFAULT_GUY_RATIO,
           round_to: int = DEFAULT_ROUND_TO_FT) -> TowerSpec:
    """Build a TowerSpec and record the derivation in the ledger."""
    h = overall_height_ft(rad_ft, ground_delta_ft, round_to)
    ttype = tower_type_for(h, thresholds)
    r_g = guy_anchor_radius_ft(h, ttype, guy_ratio)

    parents = [i for i in (rad_evidence_id, elevation_evidence_id) if i]
    eids = []
    if parents:
        eids.append(ledger.computed(
            h,
            f"Overall tower height {h:.0f} ft = {round_to} x ceil(({rad_ft:.0f} ft RAD "
            f"{ground_delta_ft:+.0f} ft ground delta) / {round_to})",
            parents,
        ))
        eids.append(ledger.computed(
            ttype.value,
            f"Structure type {ttype.value} from height {h:.0f} ft "
            f"(monopole <= {(thresholds or DEFAULT_THRESHOLDS).get('monopole_max_ft', 150)} ft, "
            f"self-support <= {(thresholds or DEFAULT_THRESHOLDS).get('self_support_max_ft', 274)} ft, "
            f"guyed above)",
            [eids[0]],
        ))
        if r_g:
            eids.append(ledger.computed(
                r_g,
                f"Guy anchor radius {r_g:.0f} ft at {guy_ratio:.0%} of tower height",
                [eids[0]],
            ))

    return TowerSpec(
        rad_ft=float(rad_ft),
        ground_delta_ft=float(ground_delta_ft),
        overall_height_ft=h,
        tower_type=ttype,
        guy_anchor_radius_ft=r_g,
        evidence_ids=eids,
    )
