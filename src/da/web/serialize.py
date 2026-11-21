"""Report objects to JSON for the browser.

Deliberately explicit rather than `dataclasses.asdict`: parcels carry shapely
geometry, which is not serialisable, and the UI wants a few derived fields
(binding constraint, in-ring acres) that the dataclasses do not store.
"""

from __future__ import annotations

from typing import Any, Optional

from ..analysis import evaluate as ev
from ..geo.crs import SQFT_PER_ACRE
from ..models import AuditReport, Disposition

# Why a parcel ended where it did, in the order the gates run. The first code
# present that appears here is the binding one; `meets_requirements` means
# nothing bound.
BINDING_LABELS: list[tuple[str, str]] = [
    (ev.RC_NO_DISTRICT, "No zoning district"),
    (ev.RC_DISTRICT_CONFLICT, "District sources conflict"),
    (ev.RC_PROHIBITED, "Use prohibited in district"),
    (ev.RC_HEIGHT_UNADDRESSED, "Height above code ceiling"),
    (ev.RC_USE_PATH_UNDETERMINED, "Approval path ambiguous"),
    (ev.RC_DWELLING, "Dwelling separation"),
    (ev.RC_WETLAND, "Wetlands"),
    (ev.RC_TOO_SMALL, "Buildable area too small"),
    (ev.RC_GUY_ANCHORS, "Guy anchors do not fit"),
]

GATE_FOR_CODE = {
    ev.RC_NO_DISTRICT: 1, ev.RC_DISTRICT_CONFLICT: 2,
    ev.RC_PROHIBITED: 3, ev.RC_HEIGHT_UNADDRESSED: 3,
    ev.RC_USE_PATH_UNDETERMINED: 3,
    ev.RC_TOO_SMALL: 4, ev.RC_WETLAND: 4, ev.RC_DWELLING: 4,
    ev.RC_GUY_ANCHORS: 5,
}


def _acres(sqft: Optional[float]) -> Optional[float]:
    return round(sqft / SQFT_PER_ACRE, 2) if sqft else None


def binding_constraint(parcel) -> Optional[str]:
    for code, label in BINDING_LABELS:
        if code in parcel.reason_codes:
            return label
    return None


def binding_gate(parcel) -> Optional[int]:
    for code, _ in BINDING_LABELS:
        if code in parcel.reason_codes:
            return GATE_FOR_CODE.get(code)
    return None


def parcel_json(p) -> dict[str, Any]:
    return {
        "letter": p.letter,
        "pin": p.pin,
        "owner": p.owner,
        "site_address": p.site_address,
        "net_acres": p.net_acres,
        "in_ring_acres": _acres(p.area_in_ring_sqft),
        "buildable_acres": _acres(p.buildable_sqft),
        "buildable_sqft": round(p.buildable_sqft, 1) if p.buildable_sqft else None,
        "compound_fit": p.compound_fit,
        "jurisdiction": p.jurisdiction,
        "districts": [{"code": d.code, "jurisdiction": d.jurisdiction,
                       "source": d.source} for d in p.districts],
        "disposition": p.disposition.value if p.disposition else None,
        "reason_codes": list(p.reason_codes),
        "binding_constraint": binding_constraint(p),
        "binding_gate": binding_gate(p),
        "has_dwelling": p.has_dwelling() if hasattr(p, "has_dwelling") else None,
        "findings": [{"statement": f.statement, "section": f.rule_section,
                      "passed": f.passed, "evidence_ids": f.evidence_ids}
                     for f in p.findings],
        "conditions": list(p.conditions),
        "risks": list(p.risks),
        "unresolved": [_unresolved(u) for u in p.unresolved],
        "evidence_ids": list(p.evidence_ids),
    }


def _unresolved(u) -> dict[str, Any]:
    return {"what_is_missing": u.what_is_missing,
            "why_it_matters": u.why_it_matters,
            "how_to_resolve": u.how_to_resolve,
            "blocking": u.blocking}


def _evidence(e) -> dict[str, Any]:
    return {"id": e.id,
            "value": str(e.value)[:400],
            "source_type": e.source_type.value,
            "description": e.description,
            "url": e.url, "section": e.section, "quote": e.quote,
            "retrieved_at": e.retrieved_at,
            "derived_from": list(e.derived_from)}


def report_json(report: AuditReport, ledger, *, run_id: str,
                docx_path: Optional[str] = None) -> dict[str, Any]:
    site = report.site
    tower = report.tower
    parcels = [parcel_json(p) for p in report.parcels]

    # Counts the UI leads with.
    dispositions = {d.value: sum(1 for p in report.parcels if p.disposition is d)
                    for d in Disposition}
    binding_counts: dict[str, int] = {}
    for p in report.parcels:
        label = binding_constraint(p)
        if label:
            binding_counts[label] = binding_counts.get(label, 0) + 1

    return {
        "run_id": run_id,
        "generated_at": report.generated_at,
        "site": {
            "opportunity_id": site.opportunity_id,
            "latitude": site.latitude,
            "longitude": site.longitude,
            "search_ring_radius_ft": site.search_ring_radius_ft,
            "requested_rad_ft": site.requested_rad_ft,
            "state": site.state,
            "input_provenance": site.input_provenance,
        },
        "recommendation": report.recommendation(),
        "candidates": len(report.pursue_parcels()),
        "candidate_target": getattr(report, "CANDIDATE_TARGET", 3),
        "meets_target": report.meets_candidate_target(),
        "tower": {
            "rad_ft": tower.rad_ft,
            "ground_delta_ft": tower.ground_delta_ft,
            "overall_height_ft": tower.overall_height_ft,
            "tower_type": tower.tower_type.value,
            "guy_anchor_radius_ft": tower.guy_anchor_radius_ft,
        } if tower else None,
        "zoning_level": {
            "level": report.zoning_level.level,
            "rationale": report.zoning_level.rationale,
        } if report.zoning_level else None,
        "jurisdictions": list(report.jurisdictions),
        "dispositions": dispositions,
        "binding_counts": binding_counts,
        "parcels": parcels,
        "rules": [{"kind": r.kind, "jurisdiction": r.jurisdiction,
                   "district": r.district, "value": r.value, "units": r.units,
                   "section": r.section, "quote": r.quote, "url": r.url}
                  for r in report.rules],
        "unresolved": [_unresolved(u) for u in report.unresolved],
        "nearby_towers": report.nearby_towers,
        "towers_constructed": sum(1 for t in report.nearby_towers
                                  if t.get("is_constructed")),
        "towers_granted": sum(1 for t in report.nearby_towers
                              if t.get("category") == "granted"),
        "jurisdiction_levels": _jsonable(report.jurisdiction_levels),
        "overlays": _jsonable(report.overlays),
        "contacts": _jsonable(report.contacts),
        "evidence": [_evidence(e) for e in report.evidence.values()],
        "evidence_count": len(report.evidence),
        "assumptions": _jsonable(report.assumptions),
        "maps": sorted(report.map_paths.keys()),
        "docx_available": bool(docx_path),
    }


def _jsonable(obj: Any) -> Any:
    """Config dicts hold only scalars, lists and dicts, but terrain may carry
    numpy floats from the elevation sampler."""
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()
                if not str(k).startswith("_")}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (str, bool, int)) or obj is None:
        return obj
    try:
        return float(obj)
    except (TypeError, ValueError):
        return str(obj)
