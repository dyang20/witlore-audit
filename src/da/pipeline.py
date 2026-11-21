"""Stage orchestration: one tranche row in, one audit out.

Source failures never stop the run. Each becomes an Unresolved on the report, so
a dead endpoint produces a report that says what is missing rather than a report
that quietly omits a constraint.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date
from typing import Any, Optional

import yaml

from .analysis.evaluate import evaluate_parcel
from .analysis.letters import (assign_letters, candidate_target_note,
                               check_all_accounted, check_report)
from .evidence import Ledger
from .geo import tower as tower_mod
from .geo.ring import Ring
from .models import (AuditReport, Disposition, ProcessType, SiteInput,
                     Unresolved, ZoningLevel)
from .sources import asr as asr_src
from .sources import context as ctx
from .sources import elevation as elev
from .sources import parcels as parcels_src
from .sources import zoning as zoning_src
from .zoning import council_bluffs as cb
from .zoning import iowa_state as ia
from .zoning import oakland as oak
from .zoning import pottawattamie as pott
from .zoning.snapshot import ingest_file, load as load_snapshot

DEFAULT_CONFIG = "config/client_rules.yml"
CB_ORDINANCE_PATH = "data/council_bluffs_15.31.txt"
OAKLAND_ORDINANCE_PATH = "data/oakland_zoning.txt"


def load_config(path: str = DEFAULT_CONFIG) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@contextmanager
def _stage(log, label: str):
    """Log a stage with how long it took.

    Worth having permanently rather than as a debugging aid: when a run is
    slow during a live review, the operator needs to say which service is
    slow, and when a source is degraded the latency is usually the first sign.
    """
    t0 = time.monotonic()
    log(f"  {label}...")
    try:
        yield
    finally:
        log(f"  {label}: {time.monotonic() - t0:.1f}s")


def run(site: SiteInput, *, config: Optional[dict] = None,
        out_dir: str = "outputs", ordinance_path: Optional[str] = None,
        skip_asr: bool = False, skip_elevation: bool = False,
        log=print, collect=None) -> tuple[AuditReport, Ledger]:
    """Run one audit.

    `collect`, if given, is called once with the fetched geometry and the
    derived tower before the report is finished. It exists so a caller can
    re-run the geometry stage under a different assumption without re-fetching
    every layer; nothing in the pipeline reads back from it.
    """
    cfg = config or load_config()
    ledger = Ledger()
    report = AuditReport(site=site, generated_at=date.today().isoformat())

    # -- ring ------------------------------------------------------------
    ring = Ring(site.latitude, site.longitude, site.search_ring_radius_ft)
    for problem in ring.verify():
        report.unresolved.append(Unresolved(
            what_is_missing=f"Ring geometry check failed: {problem}",
            why_it_matters="Ring drivers navigate to these coordinates and every "
                           "distance in this report is measured from them.",
            how_to_resolve="Re-check the coordinates on the tranche release sheet.",
        ))
    centre_eid = ledger.from_input(
        f"{site.latitude}, {site.longitude}", "Search ring centre coordinates",
        "Original Site Latitude / Longitude")
    ledger.from_input(site.search_ring_radius_ft, "Search ring radius (ft)",
                      "Search Ring Radius")
    rad_eid = ledger.from_input(site.requested_rad_ft, "Requested RAD (ft)",
                                "Requested RAD")

    # -- elevation and tower ----------------------------------------------
    terrain: dict = {}
    elev_eid = None
    ground_delta = 0.0
    if not skip_elevation:
        with _stage(log, "elevation"):
            centre_elev, terrain = elev.sample_ring(ring, ledger)
            if terrain.get("method"):
                log(f"    via {terrain['method']}")
        if centre_elev is None:
            report.unresolved.append(Unresolved(
                what_is_missing="Ground elevation could not be retrieved",
                why_it_matters="Tower height is the RAD adjusted for ground elevation "
                               "difference; without it the height is the raw RAD and "
                               "any district height cap is compared against that.",
                how_to_resolve="Retry the USGS 3DEP query, or supply elevations from "
                               "the county LiDAR index.",
                blocking=False,
            ))
    tcfg = cfg.get("tower", {})
    tower = tower_mod.derive(
        site.requested_rad_ft, ledger, ground_delta_ft=ground_delta,
        rad_evidence_id=rad_eid, elevation_evidence_id=elev_eid,
        thresholds={"monopole_max_ft": tcfg.get("monopole_max_ft", 150),
                    "self_support_max_ft": tcfg.get("self_support_max_ft", 274)},
        guy_ratio=tcfg.get("guy_anchor_ratio", 0.80),
        round_to=tcfg.get("round_up_to_ft", 5),
    )
    report.tower = tower
    log(f"  tower: {tower.overall_height_ft:.0f} ft {tower.tower_type.value}")

    # -- ordinance ---------------------------------------------------------
    snap = load_snapshot(pott.JURISDICTION)
    if snap is None and ordinance_path and os.path.exists(ordinance_path):
        snap = ingest_file(
            ordinance_path, pott.JURISDICTION,
            url=pott.ORDINANCE_URL, source_type="retrieved",
            note="Text supplied by the hiring team. The source URL is the one cited "
                 "in the human audits, and its filename matches the supplied file. "
                 "Newest internal amendment is #2015-05 (2015-12-18), so confirm "
                 "nothing has been amended since.")
    if snap:
        rules, warns = pott.build_rules(snap)
        report.rules = rules
        log(f"  ordinance: {len(rules)} verified rules, {len(warns)} dropped")
        for w in warns:
            report.unresolved.append(Unresolved(
                what_is_missing=w,
                why_it_matters="A rule whose quotation could not be verified is not "
                               "applied, so the analysis may be missing a constraint.",
                how_to_resolve="Correct the quotation against the ordinance text.",
                blocking=False,
            ))
        if not snap.is_verifiable:
            report.unresolved.append(Unresolved(
                what_is_missing=f"The {pott.JURISDICTION} ordinance has no source URL "
                                f"or retrieval date",
                why_it_matters="Citations cannot be independently re-verified, and the "
                               "newest amendment in the supplied text is from 2015, so "
                               "it may be superseded.",
                how_to_resolve="Obtain the live ordinance URL from the county and "
                               "re-snapshot.",
                blocking=False,
            ))
    else:
        report.unresolved.append(Unresolved(
            what_is_missing="No zoning ordinance snapshot is available",
            why_it_matters="Without ordinance text there are no height caps, setbacks "
                           "or use permissions to apply, so no parcel can be cleared.",
            how_to_resolve="Run `da snapshot-ordinance` with the ordinance file or URL.",
        ))

    # Council Bluffs, where two of the five calibration sites sit. The text is
    # Chapter 15.31 as rendered by the city's official code platform; see
    # zoning/council_bluffs.py for how it was retrieved.
    cb_snap = load_snapshot(cb.JURISDICTION)
    if cb_snap is None and os.path.exists(CB_ORDINANCE_PATH):
        cb_snap = ingest_file(CB_ORDINANCE_PATH, cb.JURISDICTION,
                              url=cb.ORDINANCE_URL, source_type="retrieved",
                              note=f"Council Bluffs Municipal Code Ch. 15.31, "
                                   f"enacted by {cb.ENACTED}.")
    if cb_snap:
        cb_rules, cb_warns = cb.build_rules(cb_snap)
        report.rules.extend(cb_rules)
        log(f"  Council Bluffs: {len(cb_rules)} rules, {len(cb_warns)} dropped")

    # City of Oakland, needed for 43986, which straddles the city and the county.
    oak_snap = load_snapshot(oak.JURISDICTION)
    if oak_snap is None and os.path.exists(OAKLAND_ORDINANCE_PATH):
        oak_snap = ingest_file(OAKLAND_ORDINANCE_PATH, oak.JURISDICTION,
                               url=oak.ORDINANCE_URL, source_type="retrieved",
                               note="Partial extract of the city's published "
                                    "ordinance PDF; see data/oakland_zoning.txt.")
    if oak_snap:
        oak_rules, oak_warns = oak.build_rules(oak_snap)
        report.rules.extend(oak_rules)
        log(f"  City of Oakland: {len(oak_rules)} rules, {len(oak_warns)} dropped")

    # Iowa Code Chapter 8C sits above every local ordinance in the state.
    ia_ids = ia.record(ledger, site.state, {r.kind for r in report.rules})
    if ia_ids:
        log(f"  Iowa Chapter 8C: {len(ia_ids)} statutory references")
        report.assumptions["iowa_chapter_8c"] = {
            "applies": True,
            "cited_sections": [p.section for p in ia.provisions_for_site(site.state)],
            "note": "Cited by section against the Legislature's published Code. The "
                    "full statutory text is not snapshotted because Iowa publishes "
                    "Chapter 8C only as PDF and RTF, so these are references rather "
                    "than verified quotations.",
        }
        report.unresolved.append(Unresolved(
            what_is_missing="Iowa Code Chapter 8C is cited by section rather than "
                            "quoted verbatim",
            why_it_matters="Every other rule in this report carries a quotation "
                           "verified against a stored snapshot. These provisions "
                           "cannot be checked the same way, so a reader must follow "
                           "the citation to confirm wording.",
            how_to_resolve="Obtain a text-extractable copy of Chapter 8C from the "
                           "Iowa Legislature and snapshot it.",
            blocking=False,
        ))

    # -- parcels -----------------------------------------------------------
    with _stage(log, "parcels"):
        parcels, p_res = parcels_src.fetch(ring, ledger)
    if not p_res.ok:
        report.unresolved.append(p_res.as_unresolved(
            "Parcel layer unavailable",
            "Parcels are the backbone of the report; without them no candidate can "
            "be identified.",
            "Re-run the stage 0 probe to confirm the service is up, or load a cached "
            "parcel extract from the Iowa GIS Data Repository."))
        report.parcels = []
        return _finish(report, ledger, ring, cfg, out_dir, [], None, None, log)
    log(f"  {len(parcels)} parcels in ring")

    # -- jurisdiction and districts ---------------------------------------
    # Boundaries and districts are independent reads against the same two
    # servers, so they overlap rather than queue.
    with _stage(log, "boundaries, zoning districts"), \
            ThreadPoolExecutor(max_workers=2) as pool:
        f_bnd = pool.submit(ctx.municipal_boundaries, ring, ledger)
        f_dis = pool.submit(zoning_src.fetch_districts, ring, ledger)
        boundaries, _ = f_bnd.result()
        districts, z_res = f_dis.result()
    for res in z_res:
        if not res.ok:
            report.unresolved.append(res.as_unresolved(
                "A zoning district layer could not be read",
                "District determines use permission, height cap and setbacks.",
                "Confirm the service is up; otherwise obtain the zoning map from the "
                "jurisdiction."))
    for p in parcels:
        p.jurisdiction = ctx.jurisdiction_for(p, boundaries, ledger)
        zoning_src.assign(p, districts, ring, ledger)
    report.jurisdictions = sorted({p.jurisdiction for p in parcels if p.jurisdiction})

    # -- constraints -------------------------------------------------------
    # Three independent services, and FEMA is both the slowest and the one
    # that retries, so in series it set the pace for all three. The ledger is
    # the only shared state and its writes are append-only under the GIL.
    with _stage(log, "wetlands, flood, dwellings, overlays"), \
            ThreadPoolExecutor(max_workers=6) as pool:
        f_wet = pool.submit(ctx.wetlands, ring, ledger)
        f_fld = pool.submit(ctx.flood_zones, ring, ledger)
        f_dwl = pool.submit(ctx.dwellings, ring, ledger)
        f_ovl = pool.submit(_safely, ctx.overlays, ring, ledger)
        f_elc = pool.submit(_safely, ctx.electric_providers, ring, ledger)
        f_cas = pool.submit(_safely, ctx.zoning_cases, ring, ledger)
        wetlands, w_res = f_wet.result()
        flood, f_res = f_fld.result()
        dwelling_data, _ = f_dwl.result()
        overlay_data = (f_ovl.result(), f_elc.result(), f_cas.result())
    dwelling_pts = ctx.dwelling_points_for(None, dwelling_data)

    for res, label, why in (
        (w_res, "Wetlands layer", "Wetlands are subtracted from the buildable area "
                                  "and constrain guy anchor placement."),
        (f_res, "FEMA flood layer", "Flood zones are reported as a risk on each parcel."),
    ):
        if not res.ok:
            report.unresolved.append(res.as_unresolved(
                f"{label} unavailable", why,
                "Retry the service, or confirm the area manually via NEPAssist."))

    # -- evaluate ----------------------------------------------------------
    ccfg = cfg.get("compound", {})
    ratio = float((cfg.get("setbacks", {}) or {}).get("fall_distance_ratio", 1.0))
    for p in parcels:
        evaluate_parcel(
            p, ring, tower, ledger,
            wetlands=wetlands, flood=flood, dwelling_points=dwelling_pts,
            compound_primary_ft=ccfg.get("primary_ft", 100.0),
            compound_exception_ft=ccfg.get("exception_ft", 80.0),
            apply_dwelling_rule=cfg.get("dwellings", {}).get("apply_rule", True),
            fall_distance_ratio=ratio,
        )
    parcels = assign_letters(parcels, ring)
    report.parcels = parcels

    _apply_overlays(report, overlay_data, ring, log)
    report.jurisdiction_levels = _jurisdiction_levels(report)
    report.contacts = _contacts_for(report.jurisdictions)

    # Iowa Code 335.2 exempts agricultural use from county zoning, and the
    # county's most permissive districts are agricultural, so the question of
    # whether the county reaches a tower there is worth answering explicitly.
    # Once per report rather than once per agricultural parcel.
    ag_codes = {d.code for p in parcels for d in p.districts}
    ag_jur = next((j for j in report.jurisdictions if "County" in j), None)
    ag_ids = ia.record_ag_exemption(ledger, site.state, ag_jur, ag_codes)
    if ag_ids:
        log(f"  Iowa Code 335.2: agricultural exemption analysed "
            f"({len(ag_ids)} references)")
        ag_note = ia.ag_exemption_unresolved(site.state, ag_jur, ag_codes)
        if ag_note:
            report.unresolved.append(ag_note)

    # -- towers ------------------------------------------------------------
    if not skip_asr:
        with _stage(log, "ASR towers"):
            towers, err = asr_src.towers_near(ring, ledger)
        if err:
            report.unresolved.append(Unresolved(
                what_is_missing=f"Nearby registered structures could not be listed ({err})",
                why_it_matters="Separation rules in some jurisdictions count nearby "
                               "towers, and collocation must be addressed in the "
                               "application.",
                how_to_resolve="Download the FCC ASR bulk file, or search the ASR web "
                               "interface manually.",
                blocking=False,
            ))
        report.nearby_towers = [{
            "registration": t.registration, "latitude": t.latitude,
            "longitude": t.longitude, "height_agl_ft": t.height_agl_ft,
            "structure_type": t.structure_type, "owner": t.owner,
            "distance_miles": t.distance_miles,
            "status": t.status, "status_label": t.status_label,
            "is_constructed": t.is_constructed, "category": t.category,
            "date_constructed": t.date_constructed,
            "date_dismantled": t.date_dismantled,
        } for t in towers]

    # Merge rather than replace: the Chapter 8C note was written into
    # report.assumptions earlier and a plain assignment here discarded it.
    report.assumptions = {**cfg, **report.assumptions, "terrain": terrain}
    if collect is not None:
        collect({"ring": ring, "tower": tower, "wetlands": wetlands,
                 "flood": flood, "dwelling_points": dwelling_pts,
                 "districts": districts, "config": cfg})
    return _finish(report, ledger, ring, cfg, out_dir, districts, wetlands, flood,
                   log, boundaries=boundaries)


def _finish(report, ledger, ring, cfg, out_dir, districts, wetlands, flood, log,
            boundaries=None):
    from .maps import render

    report.zoning_level = _zoning_level(
        report, ledger,
        cup_is_level_4=(cfg.get("zoning_levels", {}) or {}).get("cup_is_level_4", False))

    map_dir = os.path.join(out_dir, report.site.opportunity_id, "maps")
    if report.parcels:
        with _stage(log, "maps"):
            # Each map on its own, so one that fails costs one map rather than
            # every map after it.
            jobs = [
                ("parcel", lambda: render.parcel_map(ring, report.parcels, map_dir)),
                ("zoning", lambda: render.zoning_map(
                    ring, report.parcels, districts or [], map_dir,
                    boundaries=boundaries)),
                ("flood", lambda: render.flood_map(
                    ring, report.parcels, map_dir, flood=flood)),
                ("wetlands", lambda: render.wetlands_map(
                    ring, report.parcels, map_dir, wetlands=wetlands)),
                # Drawn even when empty: "no registered structure within two
                # miles" is a finding, and the assignment asks for the map.
                ("asr", lambda: render.asr_map(ring, report.nearby_towers, map_dir)),
            ]
            for name, job in jobs:
                try:
                    report.map_paths[name] = job()
                except Exception as e:  # noqa: BLE001 - a failed map is not a failed audit
                    log(f"  {name} map failed: {type(e).__name__}: {e}")
            if (cfg.get("maps", {}) or {}).get("basemaps", True):
                _overview_maps(report, ledger, ring, map_dir, render, log)

    # Evidence must be attached before the consistency check, which verifies
    # that every finding's citations resolve.
    report.evidence = ledger.all()
    problems = check_report(report, ring) + check_all_accounted(report.parcels, ring)
    for p in problems:
        report.unresolved.append(Unresolved(
            what_is_missing=f"Consistency check: {p}",
            why_it_matters="A failed internal check means some part of this report is "
                           "incomplete or contradictory.",
            how_to_resolve="Review the named parcel or layer before relying on this "
                           "report.",
            blocking=False,
        ))
    report.evidence = ledger.all()
    log(f"  {candidate_target_note(report)}")
    return report, ledger


def _safely(fn, *args):
    """Run an advisory fetch so that it cannot fail the run.

    Overlays, utilities and case history inform the report but never gate a
    parcel, so an exception in one of them is reported as a gap, not raised.
    """
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001
        return e


OVERLAY_TEXT = {
    "loess_hills": (
        "Loess Hills special landscape area",
        "The Loess Hills carry their own district (A-4), where a tower is a "
        "conditional use at any height (Sec. 8.014.030.15)."),
    "airport_hazard": (
        "Airport hazard area",
        "Sec. 8.004.220.10 requires documentation that the site and height have "
        "been approved by the appropriate airport authority."),
    "two_mile_area": (
        "City two-mile review area",
        "Iowa Code 354.9 lets a city review subdivision plats within two miles "
        "of its limits. It is plat review, not zoning: a ground lease does not "
        "trigger it, a fee parcel split off for the tower does."),
    "slope_protection": (
        "Slope protection overlay",
        "Grading and construction on protected slopes carries additional "
        "review under the overlay district."),
}


def _apply_overlays(report: AuditReport, overlay_data, ring, log) -> None:
    """Record overlays, the electric provider and zoning case history.

    Advisory only. Each overlay is attached to the parcels it touches as a
    risk, so a reviewer sees it where the decision is made.
    """
    ovl, elc, cas = overlay_data
    out: dict[str, Any] = {"overlays": [], "electric": [], "zoning_cases": [],
                           "unavailable": []}

    if isinstance(ovl, Exception):
        out["unavailable"].append(f"overlay districts ({type(ovl).__name__}: {ovl})")
    else:
        polys_by_key, results = ovl
        for res in results:
            if not res.ok:
                out["unavailable"].append(f"{res.url or 'an overlay layer'}: {res.error}")
        for key, polys in polys_by_key.items():
            label, meaning = OVERLAY_TEXT.get(key, (key.replace("_", " "), ""))
            touched = []
            for p in report.parcels:
                in_ring = p.geometry.intersection(ring.polygon)
                if any(g.intersects(in_ring) for g in polys):
                    touched.append(p.letter or p.pin)
                    p.risks.append(f"{label} covers part of this parcel. {meaning}")
            out["overlays"].append({"key": key, "label": label, "meaning": meaning,
                                    "polygons": len(polys), "parcels": touched})

    if isinstance(elc, Exception):
        out["unavailable"].append(f"electric service areas ({type(elc).__name__})")
    else:
        names, res = elc
        out["electric"] = list(names)
        if not res.ok:
            out["unavailable"].append(f"electric service areas: {res.error}")

    if isinstance(cas, Exception):
        out["unavailable"].append(f"zoning case index ({type(cas).__name__})")
    else:
        cases, res = cas
        out["zoning_cases"] = list(cases)
        if not res.ok:
            out["unavailable"].append(f"zoning case index: {res.error}")

    report.overlays = out
    hits = [o["label"] for o in out["overlays"] if o["polygons"]]
    log(f"  overlays: {', '.join(hits) if hits else 'none intersect the ring'}"
        + (f"; electric: {', '.join(out['electric'])}" if out["electric"] else ""))
    if out["unavailable"]:
        report.unresolved.append(Unresolved(
            what_is_missing="Some overlay or utility layers could not be read: "
                            + "; ".join(out["unavailable"])[:400],
            why_it_matters="Overlay districts add approvals (airport authority, "
                           "Loess Hills conditional use, city plat review) that "
                           "this report would not mention if the layer is missing.",
            how_to_resolve="Re-run, or check the county's zoning map viewer for "
                           "overlays on the candidate parcels.",
            blocking=False,
        ))


def _jurisdiction_levels(report: AuditReport) -> list[dict]:
    """Permission, process and level for every district present, per jurisdiction.

    The human audits state a level per jurisdiction, and a ring that crosses a
    city limit has two answers, so a single site-wide number loses the point.
    Both scales are given because they disagree about a conditional use permit:
    the assignment puts CUPs at level 4, the 43986 and 37954 audits put a
    typical CUP at level 3.
    """
    if not report.tower:
        return []
    h = report.tower.overall_height_ft
    seen: dict[tuple[str, str], dict] = {}
    for p in report.parcels:
        for d in p.districts:
            key = (d.jurisdiction, d.code)
            row = seen.get(key)
            if row is None:
                if d.jurisdiction == oak.JURISDICTION:
                    permission, process, why = oak.evaluate(d.code, h)
                elif d.jurisdiction == cb.JURISDICTION:
                    permission, process, why = cb.evaluate(d.code, h)
                elif d.jurisdiction == pott.JURISDICTION:
                    permission, process, why = pott.evaluate(d.code, h)
                else:
                    continue
                row = seen[key] = {
                    "jurisdiction": d.jurisdiction, "district": d.code,
                    "permission": permission.value, "process": process.value,
                    "why": why,
                    "level_assignment": ZoningLevel.from_process(
                        process, cup_is_level_4=True),
                    "level_audit": ZoningLevel.from_process(
                        process, cup_is_level_4=False),
                    "parcels": [], "pursue": 0,
                }
            row["parcels"].append(p.letter or p.pin)
            if p.disposition is Disposition.PURSUE:
                row["pursue"] += 1

    rows = sorted(seen.values(),
                  key=lambda r: (r["jurisdiction"], r["level_assignment"], r["district"]))
    # A jurisdiction in the ring with no district at all (a city that publishes
    # no zoning GIS) still gets a row, because "undetermined" is the finding.
    have = {r["jurisdiction"] for r in rows}
    for j in report.jurisdictions:
        aliases = {j, f"City of {j}"}
        if not (aliases & have):
            n = sum(1 for p in report.parcels if p.jurisdiction == j)
            rows.append({
                "jurisdiction": j, "district": None, "permission": "unknown",
                "process": "unknown",
                "why": f"no zoning district could be mapped for the {n} parcel(s) "
                       f"in {j}, so the approval path there is undetermined",
                "level_assignment": None, "level_audit": None,
                "parcels": [p.letter or p.pin for p in report.parcels
                            if p.jurisdiction == j],
                "pursue": 0,
            })
    return rows


CONTACTS_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "config",
                             "jurisdictions", "contacts.yml")


def _contacts_for(jurisdictions: list[str]) -> list[dict]:
    """Planning and building contacts for the jurisdictions in the ring.

    Read from config, where each entry carries the page it was taken from and
    the date, because a phone number is a claim like any other.
    """
    path = CONTACTS_PATH if os.path.exists(CONTACTS_PATH) else os.path.join(
        "config", "jurisdictions", "contacts.yml")
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001 - contacts are a convenience
        return []
    out = []
    for j in jurisdictions:
        entry = data.get(j) or data.get(f"City of {j}")
        if entry:
            out.append({"jurisdiction": j, **entry})
    return out


def _overview_maps(report, ledger, ring, map_dir, render, log) -> None:
    """Topo and street/aerial overviews on USGS tiles, or a stated gap."""
    from .sources import basemap

    titles = {"topo": "Topo Map (USGS)",
              "street": "Street and Aerial Overview (USGS imagery)"}
    half_width = ring.radius_ft * 1.6
    missing = []
    for kind, title in titles.items():
        mosaic = basemap.fetch(kind, ring.lon, ring.lat, half_width)
        if mosaic is None:
            missing.append(f"{title}: {basemap.last_failure(kind) or 'unavailable'}")
            continue
        try:
            report.map_paths[kind] = render.overview_map(
                ring, report.parcels, mosaic, map_dir, kind, title,
                towers=report.nearby_towers)
            ledger.from_dataset(
                f"zoom {mosaic.zoom}",
                f"{title}: USGS National Map tiles around the ring centre"
                + (f" ({mosaic.note})" if mosaic.note else ""),
                url=mosaic.url, layer="cached map tiles")
        except Exception as e:  # noqa: BLE001
            missing.append(f"{title}: {type(e).__name__}: {e}")
    if missing:
        log("  basemaps unavailable: " + "; ".join(missing)[:300])
        report.unresolved.append(Unresolved(
            what_is_missing="Overview map(s) could not be drawn: "
                            + "; ".join(missing)[:400],
            why_it_matters="The overview maps are orientation for the ring drive. "
                           "No finding in this report depends on them, and the "
                           "parcel, zoning, flood, wetlands and tower maps are "
                           "drawn from fetched geometry and are unaffected.",
            how_to_resolve="Re-run when the USGS National Map tile service is "
                           "reachable, or open the ring centre in any map viewer.",
            blocking=False,
        ))


def _zoning_level(report: AuditReport, ledger: Ledger,
                  cup_is_level_4: bool = False) -> Optional[ZoningLevel]:
    """Site level is the easiest path among parcels worth pursuing.

    If nothing is pursuable the level describes the best available district, so
    the report still says what the approval path would be.
    """
    candidates = report.pursue_parcels() or report.parcels
    if not candidates:
        return None

    best: Optional[tuple[int, str, list[str]]] = None
    for p in candidates:
        for d in p.districts:
            if not report.tower:
                continue
            if d.jurisdiction == oak.JURISDICTION:
                permission, process, why = oak.evaluate(
                    d.code, report.tower.overall_height_ft)
            elif d.jurisdiction == cb.JURISDICTION:
                permission, process, why = cb.evaluate(
                    d.code, report.tower.overall_height_ft)
            elif d.jurisdiction == pott.JURISDICTION:
                permission, process, why = pott.evaluate(
                    d.code, report.tower.overall_height_ft)
            else:
                continue
            lvl = ZoningLevel.from_process(process, cup_is_level_4=cup_is_level_4)
            if best is None or lvl < best[0]:
                best = (lvl, f"{d.code}: {why}", d.evidence_ids)
    if best is None:
        return ZoningLevel(4, "Approval path could not be determined from the available "
                              "ordinance rules; assuming the slowest path.")
    return ZoningLevel(best[0], best[1], best[2])
