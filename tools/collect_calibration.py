"""Copy the five calibration reports into calibration/, where they are committed.

outputs/ is ignored by git because it is regenerated on every run, so the
calibration reports the assignment asks to be submitted would otherwise never
reach the repository. Run this after `da batch` and `tools/calibrate.py`:

    da batch --ids TI-OPP-35820,TI-OPP-34659,TI-OPP-35482,TI-OPP-37954,TI-OPP-43986
    python tools/calibrate.py --human-dir human_reports
    python tools/collect_calibration.py
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

SITES = ["TI-OPP-35820", "TI-OPP-34659", "TI-OPP-35482", "TI-OPP-37954",
         "TI-OPP-43986"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="outputs")
    ap.add_argument("--dest", default="calibration")
    args = ap.parse_args()

    os.makedirs(args.dest, exist_ok=True)
    missing = []
    for oid in SITES:
        src = os.path.join(args.out, oid, f"Desktop Audit - {oid}.docx")
        if not os.path.exists(src):
            missing.append(oid)
            continue
        shutil.copy2(src, os.path.join(args.dest, os.path.basename(src)))
        ev = os.path.join(args.out, oid, "evidence.json")
        if os.path.exists(ev):
            shutil.copy2(ev, os.path.join(args.dest, f"{oid}.evidence.json"))
        print(f"  {oid}: copied")
    if missing:
        print(f"not found in {args.out}/, run `da batch` first: {', '.join(missing)}",
              file=sys.stderr)
        return 1
    print(f"{len(SITES)} calibration reports in {args.dest}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
