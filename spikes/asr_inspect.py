"""Dump the raw ASR records near a site, field by field.

Why this exists: a human audit for TI-OPP-34659 reported one existing tower
where the pipeline reported two. The pipeline currently counts every
registration in r_tower.zip, and that archive holds granted-but-unbuilt,
cancelled and dismissed registrations alongside constructed ones, so a count
of registrations is not a count of towers standing on the ground.

This prints every field of the CO, RA and EN records for each nearby
registration so the field that distinguishes them can be identified by
inspection rather than guessed at.

    python spikes/asr_inspect.py --lat 41.4935 --lon -95.7713056
    python spikes/asr_inspect.py --lat 41.4935 --lon -95.7713056 --miles 0.5

Column constants are imported from da.sources.asr, so this always agrees with
whatever the pipeline is actually using.
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import os
import zipfile

from da.sources import asr


def rows(zf: zipfile.ZipFile, name: str):
    target = f"{name}.dat".upper()
    members = [n for n in zf.namelist() if n.upper().endswith(target)]
    if not members:
        raise SystemExit(f"no {name}.dat in the archive: {zf.namelist()[:8]}")
    with zf.open(members[0]) as fh:
        for row in csv.reader(io.TextIOWrapper(fh, encoding="latin-1",
                                               errors="replace"),
                              delimiter="|"):
            if row:
                yield row


def dms(d, m, s, direction):
    try:
        v = float(d) + float(m) / 60.0 + float(s) / 3600.0
    except (TypeError, ValueError):
        return None
    return -v if str(direction).strip().upper() in ("S", "W") else v


def haversine_miles(lat1, lon1, lat2, lon2):
    r = 3958.7613
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def show(label: str, row: list[str]) -> None:
    print(f"    {label} ({len(row)} fields)")
    for i, v in enumerate(row):
        v = (v or "").strip()
        if v:
            print(f"      [{i:>2}] {v[:72]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--miles", type=float, default=2.0)
    ap.add_argument("--cache", default="data/asr/r_tower.zip")
    args = ap.parse_args()

    if not os.path.exists(args.cache):
        raise SystemExit(
            f"no ASR archive at {args.cache}. Run any audit without "
            f"--skip-asr once to populate the cache, then retry.")

    pad = (args.miles / 69.0) * 1.5
    near: dict[str, tuple[float, float, float, list[str]]] = {}

    with zipfile.ZipFile(args.cache) as zf:
        for row in rows(zf, "CO"):
            if len(row) <= asr.CO_LON_DIR:
                continue
            lat = dms(row[asr.CO_LAT_D], row[asr.CO_LAT_M],
                      row[asr.CO_LAT_S], row[asr.CO_LAT_DIR])
            lon = dms(row[asr.CO_LON_D], row[asr.CO_LON_M],
                      row[asr.CO_LON_S], row[asr.CO_LON_DIR])
            if lat is None or lon is None:
                continue
            if abs(lat - args.lat) > pad or abs(lon - args.lon) > pad * 1.4:
                continue
            d = haversine_miles(args.lat, args.lon, lat, lon)
            if d <= args.miles:
                reg = row[asr.CO_REG_NUM]
                # One registration can carry several CO rows; keep the nearest.
                if reg not in near or d < near[reg][2]:
                    near[reg] = (lat, lon, d, row)

        print(f"{len(near)} registration(s) within {args.miles} mi of "
              f"{args.lat}, {args.lon}\n")
        if not near:
            return 0

        ra = {r[asr.RA_REG_NUM]: r for r in rows(zf, "RA")
              if len(r) > asr.RA_REG_NUM and r[asr.RA_REG_NUM] in near}
        en = {}
        for r in rows(zf, "EN"):
            if len(r) > asr.EN_REG_NUM and r[asr.EN_REG_NUM] in near:
                en.setdefault(r[asr.EN_REG_NUM], r)

    for reg, (lat, lon, d, co) in sorted(near.items(), key=lambda kv: kv[1][2]):
        print(f"=== registration {reg}  {d:.2f} mi  {lat:.5f}, {lon:.5f}")
        show("CO", co)
        if reg in ra:
            show("RA", ra[reg])
        else:
            print("    RA  (no record)")
        if reg in en:
            show("EN", en[reg])
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
