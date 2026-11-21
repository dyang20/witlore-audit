#!/usr/bin/env python3
"""Exercise each source module against a live ring, one at a time.

A full pipeline run fails as a single traceback and tells you little about which
layer broke. This calls every source module separately against the TI-OPP-35820
ring and reports pass/fail per module with the real error and a sample of what
came back, so a broken layer id or a renamed field is obvious immediately.

Run this before the first `da run`.

    python spikes/live_check.py                 # everything except the ASR download
    python spikes/live_check.py --with-asr      # include the 100 MB+ FCC file
    python spikes/live_check.py --only parcels,zoning
    python spikes/live_check.py --id TI-OPP-35482 --sheet tranche.csv

Exit code is the number of failed critical modules, so CI can gate on it.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from typing import Any, Callable, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from da.evidence import Ledger
from da.geo.crs import SQFT_PER_ACRE
from da.geo.ring import Ring

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
if os.name == "nt" and not os.environ.get("WT_SESSION"):
    GREEN = RED = YELLOW = DIM = RESET = ""   # old consoles mangle ANSI


class Check:
    def __init__(self, name: str, critical: bool, note: str) -> None:
        self.name, self.critical, self.note = name, critical, note
        self.ok: Optional[bool] = None
        self.detail = ""
        self.error = ""
        self.ms = 0


def run_check(c: Check, fn: Callable[[], str]) -> Check:
    print(f"  {c.name:22} ", end="", flush=True)
    t0 = time.perf_counter()
    try:
        c.detail = fn()
        c.ok = True
    except Exception as e:  # noqa: BLE001 - the point is to report, not raise
        c.ok = False
        c.error = f"{type(e).__name__}: {e}"
        c.detail = ""
    c.ms = round((time.perf_counter() - t0) * 1000)
    mark = f"{GREEN}ok{RESET}" if c.ok else f"{RED}FAIL{RESET}"
    print(f"{mark:>14}  {c.ms:>6} ms  {c.detail or c.error[:88]}")
    return c


def main() -> int:
    ap = argparse.ArgumentParser(description="Live source-module check")
    ap.add_argument("--sheet", default="tranche.csv")
    ap.add_argument("--id", default="TI-OPP-35820")
    ap.add_argument("--only", default="")
    ap.add_argument("--with-asr", action="store_true",
                    help="include the FCC ASR bulk download (100 MB+, slow)")
    ap.add_argument("--verbose", action="store_true",
                    help="print a full traceback for each failure")
    args = ap.parse_args()

    # -- ring ----------------------------------------------------------------
    from da.cli import COLUMNS, _pick, parse_row, read_sheet

    rows = read_sheet(args.sheet)
    row = next((r for r in rows
                if _pick(r, COLUMNS["opportunity_id"]) == args.id), None)
    if row is None:
        derived = "data/derived_sites.csv"
        if os.path.exists(derived):
            row = next((r for r in read_sheet(derived)
                        if _pick(r, COLUMNS["opportunity_id"]) == args.id), None)
    if row is None:
        print(f"{RED}no row for {args.id} in {args.sheet}{RESET}")
        return 1

    site = parse_row(row)
    ring = Ring(site.latitude, site.longitude, site.search_ring_radius_ft)
    ledger = Ledger()

    print()
    print(f"Live source check: {site.opportunity_id}")
    print(f"  ring {ring.lat}, {ring.lon}  r={ring.radius_ft:.0f} ft  "
          f"({ring.area_sqft() / SQFT_PER_ACRE:.0f} acres)")
    print(f"  RAD {site.requested_rad_ft:.0f} ft")
    problems = ring.verify()
    print(f"  geometry self-check: "
          f"{GREEN + 'clean' + RESET if not problems else RED + str(problems) + RESET}")
    print()

    wanted = {s.strip() for s in args.only.split(",") if s.strip()}
    checks: list[Check] = []

    def should(name: str) -> bool:
        return not wanted or name in wanted

    # -- parcels -------------------------------------------------------------
    parcels_holder: dict[str, Any] = {}

    if should("parcels"):
        c = Check("parcels", True, "owner, PIN, acres, cached district")

        def _parcels() -> str:
            from da.sources import parcels as src
            ps, res = src.fetch(ring, ledger)
            if not res.ok:
                raise RuntimeError(res.error)
            parcels_holder["parcels"] = ps
            if not ps:
                raise RuntimeError("layer reachable but returned 0 parcels in ring")
            owned = sum(1 for p in ps if p.owner)
            acred = sum(1 for p in ps if p.net_acres)
            dwell = sum(1 for p in ps if (p.dwelling_value or 0) > 0)
            warn = f"  {YELLOW}warnings: {res.warnings}{RESET}" if res.warnings else ""
            return (f"{len(ps)} parcels, {owned} with owner, {acred} with acres, "
                    f"{dwell} with assessed dwelling{warn}")

        checks.append(run_check(c, _parcels))

    # -- zoning --------------------------------------------------------------
    if should("zoning"):
        c = Check("zoning districts", True, "county + Council Bluffs polygons")

        def _zoning() -> str:
            from da.sources import zoning as src
            ds, results = src.fetch_districts(ring, ledger)
            dead = [r.error for r in results if not r.ok]
            if not ds and dead:
                raise RuntimeError("; ".join(str(d) for d in dead))
            codes = sorted({d["code"] for d in ds})
            msg = f"{len(ds)} polygons, districts {codes}"
            if dead:
                msg += f"  {YELLOW}({len(dead)} service(s) failed){RESET}"
            if ds and not codes:
                msg += f"  {RED}<- codes empty: district field name is wrong{RESET}"
            return msg

        checks.append(run_check(c, _zoning))

    # -- jurisdiction --------------------------------------------------------
    if should("boundaries"):
        c = Check("municipal bounds", True, "city vs unincorporated")

        def _bounds() -> str:
            from da.sources import context as ctx
            bs, res = ctx.municipal_boundaries(ring, ledger)
            if not res.ok:
                raise RuntimeError(res.error)
            names = sorted({b["city"] for b in bs if b.get("city")})
            return f"{len(bs)} boundaries{', ' + str(names) if names else ' (none in ring, expected for a rural site)'}"

        checks.append(run_check(c, _bounds))

    # -- dwellings -----------------------------------------------------------
    if should("dwellings"):
        c = Check("dwellings", False, "footprints + address points")

        def _dwell() -> str:
            from da.sources import context as ctx
            data, results = ctx.dwellings(ring, ledger)
            dead = [r.error for r in results if not r.ok]
            if dead and not any(data.values()):
                raise RuntimeError("; ".join(str(d) for d in dead))
            return (f"{len(data['footprints'])} footprints "
                    f"({len(data['residential'])} residential), "
                    f"{len(data['addresses'])} address points")

        checks.append(run_check(c, _dwell))

    # -- wetlands ------------------------------------------------------------
    if should("wetlands"):
        c = Check("wetlands", True, "NWI, county fallback")

        def _wet() -> str:
            from da.sources import context as ctx
            polys, res = ctx.wetlands(ring, ledger)
            if not res.ok:
                raise RuntimeError(res.error)
            acres = sum(p.area for p in polys) / SQFT_PER_ACRE if polys else 0
            fb = f"  {YELLOW}{res.warnings[0][:60]}{RESET}" if res.warnings else ""
            return f"{len(polys)} polygons, {acres:.1f} acres{fb}"

        checks.append(run_check(c, _wet))

    # -- flood ---------------------------------------------------------------
    if should("flood"):
        c = Check("FEMA flood", True, "NFHL layer id is a guess")

        def _flood() -> str:
            from da.sources import context as ctx
            zones, res = ctx.flood_zones(ring, ledger)
            if not res.ok:
                raise RuntimeError(
                    f"{res.error}  <- if this is a layer-id error, find the Flood "
                    f"Hazard Zones layer and set NFHL_FLOOD_ZONE_LAYER in "
                    f"src/da/sources/context.py")
            codes = sorted({z["zone"] for z in zones if z.get("zone")})
            if zones and not codes:
                return (f"{len(zones)} polygons but no zone codes "
                        f"{RED}<- wrong layer or wrong field{RESET}")
            return f"{len(zones)} polygons, zones {codes or '(none in ring)'}"

        checks.append(run_check(c, _flood))

    # -- overlays ------------------------------------------------------------
    if should("overlays"):
        c = Check("overlays", False, "Loess Hills, airport, two-mile")

        def _ov() -> str:
            from da.sources import context as ctx
            out, results = ctx.overlays(ring, ledger)
            dead = sum(1 for r in results if not r.ok)
            bits = ", ".join(f"{k}={len(v)}" for k, v in out.items())
            return bits + (f"  {YELLOW}({dead} failed){RESET}" if dead else "")

        checks.append(run_check(c, _ov))

    # -- utilities -----------------------------------------------------------
    if should("electric"):
        c = Check("electric service", False, "utility provider")

        def _el() -> str:
            from da.sources import context as ctx
            names, res = ctx.electric_providers(ring, ledger)
            if not res.ok:
                raise RuntimeError(res.error)
            return f"{names or '(none)'}"

        checks.append(run_check(c, _el))

    # -- elevation -----------------------------------------------------------
    if should("elevation"):
        c = Check("elevation", True, "USGS 3DEP, ~17 calls")

        def _elev() -> str:
            from da.sources import elevation as src
            centre, stats = src.sample_ring(ring, ledger, rings=1, per_ring=4)
            if centre is None:
                raise RuntimeError(stats.get("error", "no samples"))
            return (f"centre {centre:.0f} ft, range {stats['range_ft']:.0f} ft "
                    f"over {stats['n_samples']} samples")

        checks.append(run_check(c, _elev))

    # -- ASR -----------------------------------------------------------------
    if should("asr") and args.with_asr:
        c = Check("FCC ASR", True, "100 MB+ download on first run")

        def _asr() -> str:
            from da.sources import asr as src
            towers, err = src.towers_near(ring, ledger)
            if err:
                raise RuntimeError(err)
            half = sum(1 for t in towers if t.distance_miles <= 0.5)
            heights = [t.height_agl_ft for t in towers if t.height_agl_ft]
            if towers and not heights:
                return (f"{len(towers)} towers but no heights "
                        f"{RED}<- RA column positions are wrong{RESET}")
            return f"{len(towers)} within 2 mi, {half} within 0.5 mi"

        checks.append(run_check(c, _asr))
    elif should("asr"):
        print(f"  {'FCC ASR':22} {DIM}{'skipped':>14}  "
              f"pass --with-asr to include it{RESET}")

    # -- end-to-end on one parcel -------------------------------------------
    if parcels_holder.get("parcels") and should("evaluate"):
        c = Check("evaluate parcel", True, "geometry + rules on real data")

        def _eval() -> str:
            from da.analysis.evaluate import evaluate_parcel
            from da.geo import tower as T
            from da.sources import zoning as zsrc
            from da.sources import context as ctx

            e = ledger.from_input(site.requested_rad_ft, "RAD", "Requested RAD")
            tw = T.derive(site.requested_rad_ft, ledger, rad_evidence_id=e)
            ds, _ = zsrc.fetch_districts(ring, ledger)
            bs, _ = ctx.municipal_boundaries(ring, ledger)
            p = max(parcels_holder["parcels"],
                    key=lambda q: q.area_in_ring_sqft or 0)
            p.jurisdiction = ctx.jurisdiction_for(p, bs, ledger)
            zsrc.assign(p, ds, ring, ledger)
            evaluate_parcel(p, ring, tw, ledger)
            b = (f"{p.buildable_sqft:,.0f} sq ft"
                 if p.buildable_sqft is not None else "n/a")
            return (f"{tw.overall_height_ft:.0f} ft {tw.tower_type.value}; largest "
                    f"parcel {p.pin} -> {p.disposition.value} "
                    f"({', '.join(p.reason_codes) or 'no codes'}), buildable {b}")

        checks.append(run_check(c, _eval))

    # -- summary -------------------------------------------------------------
    failed = [c for c in checks if c.ok is False]
    crit = [c for c in failed if c.critical]
    print()
    print(f"  {len(checks) - len(failed)}/{len(checks)} passed, "
          f"{len(ledger)} evidence records recorded")
    if failed:
        print()
        for c in failed:
            tag = f"{RED}CRITICAL{RESET}" if c.critical else f"{YELLOW}non-critical{RESET}"
            print(f"  [{tag}] {c.name}: {c.error}")
            print(f"            ({c.note})")
        if args.verbose:
            print(f"\n{DIM}re-run the failing module alone with "
                  f"--only <name> for a full traceback{RESET}")
    if crit:
        print(f"\n{RED}Fix the critical failures before running `da run`.{RESET}")
    elif not failed:
        print(f"\n{GREEN}All clear. `da run --id {site.opportunity_id}` "
              f"should work.{RESET}")
    print()
    return len(crit)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
