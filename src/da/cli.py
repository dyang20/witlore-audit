"""Command line interface.

    da run --id TI-OPP-35820
    da run --id DEMO --lat 41.2581 --lon -95.8608 --radius-ft 1500 --rad-ft 180
    da batch --ids TI-OPP-35820,TI-OPP-34659,TI-OPP-35482,TI-OPP-37954,TI-OPP-43986
    da run --id TI-OPP-35820 --sheet "Tinlam Tranche Release.xlsx"
    da snapshot-ordinance --file ordinance.txt --jurisdiction "Pottawattamie County"
    da probe
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import traceback
from typing import Iterable, Optional

from .models import SiteInput
from .pipeline import load_config, run
from .report.build import build

COLUMNS = {
    "opportunity_id": ["Tinlam Opportunity ID", "Opportunity ID", "TIOPP"],
    "relo_site_fa_number": ["Relo Site FA Number", "FA Number", "FA"],
    "latitude": ["Original Site Latitude", "Latitude", "Lat"],
    "longitude": ["Original Site Longitude", "Longitude", "Lon", "Long"],
    "requested_rad_ft": ["Requested RAD", "RAD"],
    "search_ring_radius_ft": ["Search Ring Radius", "Ring Radius", "Radius"],
    "state": ["State"],
    "customer_market": ["Customer Market", "Market"],
    "tta_region": ["TTA Region"],
    "tinlam_region": ["Tinlam  Region", "Tinlam Region", "Region"],
    "intake": ["Tinlam Intake", "Intake"],
}


class InputError(ValueError):
    pass


def _pick(row: dict, names: list[str]) -> Optional[str]:
    lowered = {k.strip().lower(): v for k, v in row.items() if k}
    for n in names:
        if n in row and str(row[n]).strip():
            return str(row[n]).strip()
        v = lowered.get(n.strip().lower())
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def parse_row(row: dict, provenance: str = "tranche_sheet") -> SiteInput:
    """Tolerant column mapping with real validation.

    Row TI-OPP-34620 of the supplied sheet has a blank latitude; that must stop
    the row with a clear message rather than produce a report centred on a
    default coordinate.
    """
    vals = {k: _pick(row, names) for k, names in COLUMNS.items()}
    oid = vals["opportunity_id"] or "UNKNOWN"

    missing = [k for k in ("latitude", "longitude", "requested_rad_ft",
                           "search_ring_radius_ft") if not vals[k]]
    if missing:
        raise InputError(f"{oid}: missing required field(s): {', '.join(missing)}")

    try:
        lat = float(vals["latitude"]); lon = float(vals["longitude"])
        rad = float(vals["requested_rad_ft"]); radius = float(vals["search_ring_radius_ft"])
    except ValueError as e:
        raise InputError(f"{oid}: non-numeric value: {e}") from e

    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        raise InputError(f"{oid}: coordinates out of range ({lat}, {lon})")
    if not (20 <= rad <= 2000):
        raise InputError(f"{oid}: RAD {rad} ft is outside a plausible range")
    if not (100 <= radius <= 52800):
        raise InputError(f"{oid}: ring radius {radius} ft is outside a plausible range")
    if lon > 0 and lat > 0 and vals["state"] == "IA":
        raise InputError(f"{oid}: longitude {lon} is positive for a US site; "
                         f"the sign may be missing")

    return SiteInput(
        opportunity_id=oid, latitude=lat, longitude=lon,
        search_ring_radius_ft=radius, requested_rad_ft=rad,
        state=vals["state"] or "", relo_site_fa_number=vals["relo_site_fa_number"],
        customer_market=vals["customer_market"], tta_region=vals["tta_region"],
        tinlam_region=vals["tinlam_region"], intake=vals["intake"],
        input_provenance=provenance,
    )


DERIVED_SHEET = os.path.join("data", "derived_sites.csv")


def read_sheet(path: str) -> list[dict]:
    """Rows of a release sheet, from .csv or .xlsx.

    The assignment supplies the tranche as an .xlsx workbook, so a workbook
    handed over during a review has to load without a detour through Excel.
    The first worksheet is read and the first non-empty row is the header.
    """
    if path.lower().endswith((".xlsx", ".xlsm")):
        try:
            from openpyxl import load_workbook
        except ImportError as e:
            raise InputError(
                f"reading {path} needs openpyxl: python -m pip install openpyxl "
                f"({e})") from e
        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb.worksheets[0]
        header: Optional[list[str]] = None
        rows: list[dict] = []
        for raw in ws.iter_rows(values_only=True):
            cells = ["" if c is None else str(c).strip() for c in raw]
            if not any(cells):
                continue
            if header is None:
                header = cells
                continue
            rows.append({h: v for h, v in zip(header, cells) if h})
        wb.close()
        return rows
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def _run_one(site: SiteInput, args) -> int:
    print(f"\n=== {site.opportunity_id} ===")
    cfg = load_config(args.config)
    if getattr(args, "no_basemap", False):
        cfg.setdefault("maps", {})["basemaps"] = False
    report, ledger = run(site, config=cfg, out_dir=args.out,
                         ordinance_path=args.ordinance,
                         skip_asr=args.skip_asr, skip_elevation=args.skip_elevation)
    site_dir = os.path.join(args.out, site.opportunity_id)
    os.makedirs(site_dir, exist_ok=True)
    docx_path = os.path.join(site_dir, f"Desktop Audit - {site.opportunity_id}.docx")
    build(report, ledger, docx_path, assumptions=report.assumptions)
    ledger.to_json(os.path.join(site_dir, "evidence.json"))

    print(f"  recommendation: {report.recommendation()}")
    print(f"  parcels: {len(report.parcels)}  "
          f"pursue: {len(report.pursue_parcels())}  "
          f"unresolved items: {len(report.unresolved)}")
    print(f"  written: {docx_path}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="da", description="Desktop Audit generator")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--sheet", default="tranche.csv")
        sp.add_argument("--out", default="outputs")
        sp.add_argument("--config", default="config/client_rules.yml")
        sp.add_argument("--ordinance", default="data/ordinance.txt",
                        help="ordinance text to snapshot if none is stored yet")
        sp.add_argument("--derived-sheet", default=DERIVED_SHEET,
                        help="sites whose inputs were reconstructed from a human "
                             "audit; searched when an id is not in --sheet")
        sp.add_argument("--no-basemap", action="store_true",
                        help="skip the USGS topo and street/aerial overview maps")
        sp.add_argument("--skip-asr", action="store_true")
        sp.add_argument("--skip-elevation", action="store_true")
        sp.add_argument("--derived", action="store_true",
                        help="inputs come from a human audit header rather than the "
                             "release sheet; tags the site derived_from_report")

    r = sub.add_parser("run", help="generate one audit")
    r.add_argument("--id", default=None,
                   help="tranche row id; optional when --lat and --lon are given")
    r.add_argument("--lat", type=float, default=None,
                   help="ad-hoc site latitude, bypassing the release sheet")
    r.add_argument("--lon", type=float, default=None)
    r.add_argument("--radius-ft", type=float, default=None,
                   help="search ring radius for an ad-hoc site")
    r.add_argument("--rad-ft", type=float, default=None,
                   help="requested RAD for an ad-hoc site")
    r.add_argument("--state", default="IA")
    common(r)

    b = sub.add_parser("batch", help="generate several audits")
    b.add_argument("--ids", default="", help="comma-separated; default is every row")
    common(b)

    s = sub.add_parser("snapshot-ordinance", help="store an ordinance snapshot")
    s.add_argument("--file", required=True)
    s.add_argument("--jurisdiction", required=True)
    s.add_argument("--url", default=None)

    w = sub.add_parser("serve", help="local web UI over the same pipeline")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=5000)
    w.add_argument("--sheet", default="tranche.csv")
    w.add_argument("--out", default="outputs")
    w.add_argument("--config", default="config/client_rules.yml")
    w.add_argument("--ordinance", default="data/ordinance.txt")
    w.add_argument("--human-reports", default="human_reports")
    w.add_argument("--derived-sheet", default="data/derived_sites.csv")
    w.add_argument("--debug", action="store_true")

    args = p.parse_args(argv)

    if args.cmd == "serve":
        try:
            from .web.server import create_app
        except ImportError as e:
            print(f"the web UI needs Flask: python -m pip install flask ({e})",
                  file=sys.stderr)
            return 1
        app = create_app(sheet=args.sheet, out_dir=args.out,
                         config_path=args.config, ordinance=args.ordinance,
                         human_reports=args.human_reports,
                         derived_sheet=args.derived_sheet)
        print(f"Desktop Audit UI on http://{args.host}:{args.port}")
        app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
        return 0

    if args.cmd == "snapshot-ordinance":
        from .zoning.snapshot import ingest_file
        snap = ingest_file(args.file, args.jurisdiction, url=args.url,
                           source_type="retrieved" if args.url else "supplied")
        print(f"stored {args.jurisdiction}: {len(snap.text):,} chars, "
              f"sha256 {snap.sha256[:16]}, retrieved {snap.retrieved_at}")
        if not snap.is_verifiable:
            print("  note: no source URL, so citations from this snapshot are "
                  "marked as supplied rather than retrieved")
        return 0

    # An ad-hoc site skips the sheet entirely. This is the live-review path:
    # coordinates are read out during the session, not looked up in a tranche.
    if args.cmd == "run" and args.lat is not None and args.lon is not None:
        if args.radius_ft is None or args.rad_ft is None:
            print("--lat/--lon also need --radius-ft and --rad-ft",
                  file=sys.stderr)
            return 2
        site = SiteInput(
            opportunity_id=args.id or "AD-HOC",
            latitude=args.lat, longitude=args.lon,
            search_ring_radius_ft=args.radius_ft,
            requested_rad_ft=args.rad_ft,
            state=args.state.upper(),
            # Neither the release sheet nor a human audit: the values were
            # typed in, and the report says so rather than naming a sheet.
            input_provenance="derived_from_report" if args.derived
                             else "supplied_directly",
        )
        return _run_one(site, args)

    if args.cmd == "run" and not args.id:
        print("run needs either --id or --lat/--lon", file=sys.stderr)
        return 2

    # (row, provenance). The release sheet first, then the derived sites, so
    # `da batch --ids` with all five calibration ids works in one command even
    # though TI-OPP-43986 has no row in the release sheet.
    forced = "derived_from_report" if getattr(args, "derived", False) else None
    try:
        sources = [(row, forced or "tranche_sheet") for row in read_sheet(args.sheet)]
    except FileNotFoundError:
        print(f"release sheet not found: {args.sheet}", file=sys.stderr)
        return 2
    except InputError as e:
        print(str(e), file=sys.stderr)
        return 2
    derived_path = getattr(args, "derived_sheet", DERIVED_SHEET)
    if derived_path and os.path.exists(derived_path) and \
            os.path.abspath(derived_path) != os.path.abspath(args.sheet):
        have = {_pick(r, COLUMNS["opportunity_id"]) for r, _ in sources}
        sources += [(row, "derived_from_report") for row in read_sheet(derived_path)
                    if _pick(row, COLUMNS["opportunity_id"]) not in have]

    wanted = {i.strip() for i in getattr(args, "ids", "").split(",") if i.strip()} \
        if args.cmd == "batch" else {args.id}

    failures = 0
    matched = 0
    for row, provenance in sources:
        oid = _pick(row, COLUMNS["opportunity_id"]) or ""
        if wanted and oid not in wanted:
            continue
        if not wanted and provenance == "derived_from_report" and not forced:
            continue      # a bare `da batch` runs the release sheet only
        matched += 1
        try:
            site = parse_row(row, provenance)
        except InputError as e:
            print(f"SKIPPED {e}", file=sys.stderr)
            failures += 1
            continue
        try:
            _run_one(site, args)
        except Exception as e:  # noqa: BLE001
            print(f"FAILED {oid}: {type(e).__name__}: {e}", file=sys.stderr)
            traceback.print_exc()
            failures += 1

    if not matched:
        print(f"no rows matched {sorted(wanted)} in {args.sheet} or "
              f"{derived_path}.", file=sys.stderr)
        return 2
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
