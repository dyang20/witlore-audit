"""Can the USGS National Map tile service be reached, and how fast?

The topo and street/aerial overview maps depend on it. Nothing else does. If
this fails, run with --no-basemap, or leave it on: the pipeline gives up after
one short attempt and lists the two maps under Unresolved.

    python spikes/basemap_check.py
    python spikes/basemap_check.py --lat 41.2581 --lon -95.8608 --radius-ft 1500
"""

from __future__ import annotations

import argparse
import os
import time

from da.sources import basemap


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, default=41.4935)
    ap.add_argument("--lon", type=float, default=-95.7713056)
    ap.add_argument("--radius-ft", type=float, default=3281.0)
    ap.add_argument("--save", default="outputs/basemap_check")
    args = ap.parse_args()

    os.makedirs(args.save, exist_ok=True)
    got = []
    for kind in basemap.SERVICES:
        t0 = time.monotonic()
        m = basemap.fetch(kind, args.lon, args.lat, args.radius_ft * 1.6)
        dt = time.monotonic() - t0
        if m is None:
            print(f"  {kind:7} FAILED in {dt:.1f}s: {basemap.last_failure(kind)}")
            continue
        path = os.path.join(args.save, f"{kind}.png")
        m.image.save(path)
        got.append(kind)
        print(f"  {kind:7} ok in {dt:.1f}s  {m.url.split('/')[-2]}  zoom {m.zoom}  "
              f"{m.image.size[0]}x{m.image.size[1]} px  -> {path}"
              + (f"\n          note: {m.note}" if m.note else ""))
    missing = [k for k in basemap.SERVICES if k not in got]
    if not missing:
        print("both overview maps available")
    else:
        print(f"available: {', '.join(got) or 'none'}; reports will omit: "
              f"{', '.join(missing)} (listed under Unresolved, nothing else affected)")
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
