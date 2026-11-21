"""Find out why the 3DEP batch elevation request is failing.

sample_points_batch swallows every exception and returns None so a run can
fall back, which is right for a demo and useless for diagnosis. This calls the
same endpoint with the same payload and prints what actually comes back.

    python spikes/elev_check.py
    python spikes/elev_check.py --lat 41.4935 --lon -95.7713056
"""

from __future__ import annotations

import argparse
import json
import time

import requests

from da.sources import elevation as E


def attempt(label: str, fn) -> None:
    t0 = time.monotonic()
    try:
        r = fn()
        dt = time.monotonic() - t0
        body = r.text[:400].replace("\n", " ")
        print(f"  {label:22} {r.status_code} in {dt:.1f}s  {body}")
    except Exception as e:  # noqa: BLE001 - the point is to see it
        dt = time.monotonic() - t0
        print(f"  {label:22} {type(e).__name__} in {dt:.1f}s: {str(e)[:220]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, default=41.4935)
    ap.add_argument("--lon", type=float, default=-95.7713056)
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args()

    pts = [[args.lon, args.lat], [args.lon + 0.004, args.lat + 0.004]]
    geometry = {"points": pts, "spatialReference": {"wkid": 4326}}
    payload = {"geometry": json.dumps(geometry),
               "geometryType": "esriGeometryMultipoint",
               "returnFirstValueOnly": "true",
               "interpolation": "RSP_BilinearInterpolation",
               "f": "json"}
    hdr = {"User-Agent": E.USER_AGENT}

    print(f"endpoint: {E.IMAGE_SERVER}")
    print(f"point:    {args.lat}, {args.lon}\n")

    print("service metadata")
    attempt("GET ?f=json", lambda: requests.get(
        E.IMAGE_SERVER.rsplit("/", 1)[0], params={"f": "json"},
        timeout=args.timeout, headers=hdr))

    print("\ngetSamples")
    attempt("POST", lambda: requests.post(
        E.IMAGE_SERVER, data=payload, timeout=args.timeout, headers=hdr))
    attempt("GET", lambda: requests.get(
        E.IMAGE_SERVER, params=payload, timeout=args.timeout, headers=hdr))
    attempt("POST no interpolation", lambda: requests.post(
        E.IMAGE_SERVER, data={k: v for k, v in payload.items()
                              if k != "interpolation"},
        timeout=args.timeout, headers=hdr))

    print("\nwhat the pipeline does with it")
    t0 = time.monotonic()
    got = E.sample_points_batch([(args.lat, args.lon),
                                 (args.lat + 0.004, args.lon + 0.004)])
    print(f"  sample_points_batch -> {got} in {time.monotonic() - t0:.1f}s")

    t0 = time.monotonic()
    one = E.elevation_ft(args.lat, args.lon)
    print(f"  elevation_ft (EPQS) -> {one} in {time.monotonic() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
