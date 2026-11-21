"""Nearby towers from the FCC Antenna Structure Registration bulk file.

The weekly r_tower.zip is downloaded once and cached, then filtered locally.
That beats scraping the ASR search page and, more importantly, cannot go down
during a live demo.

Documented limitation that belongs in every report: ASR registration is required
only for structures over 200 ft or near an airport glide slope, so shorter
towers are legitimately absent. A '4 towers within 0.5 mi' count is a count of
*registered* structures, not of all structures. The human reports do not say
this; saying it is more honest, and it matters for separation rules that count
nearby towers.
"""

from __future__ import annotations

import csv
import io
import os
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Optional

import requests

from ..evidence import Ledger
from ..geo.crs import point_to_working
from .base import USER_AGENT, SourceError

BULK_URL = "https://data.fcc.gov/download/pub/uls/complete/r_tower.zip"
MILES_TO_FT = 5280.0

# Pipe-delimited ULS files, positional. Column indexes per the ULS tower record
# layout: CO (coordinates), EN (entity/owner), RA (registration).
CO_REG_NUM, CO_LAT_D, CO_LAT_M, CO_LAT_S, CO_LAT_DIR = 3, 6, 7, 8, 9
CO_LON_D, CO_LON_M, CO_LON_S, CO_LON_DIR = 11, 12, 13, 14
# RA heights are metres. 29 + 30 = 31 in every row: ground AMSL plus overall
# AGL equals overall AMSL, which is how these were identified.
RA_REG_NUM, RA_HEIGHT_AGL_M, RA_STRUCTURE_TYPE = 3, 30, 32
EN_REG_NUM, EN_ENTITY_NAME = 3, 9
METRES_TO_FT = 3.28084
RA_STATUS = 8
RA_DATE_CONSTRUCTED = 12
RA_DATE_DISMANTLED = 13
# Field 8 is the registration status. The meanings below were confirmed
# empirically against the archive itself (198,069 RA rows, Nov 2026), because
# the status letter alone is easy to misread:
#
#   C  142,026 rows, 99.99% carry a construction date        -> constructed
#   G   18,441 rows, 12% carry a construction date           -> granted
#   A   15,925 rows, 0.2% carry a construction date          -> cancelled
#   I   12,753 rows, 98% carry a construction AND a
#                    dismantlement date (field 13)           -> dismantled
#   T    8,911 rows, most constructed, few dismantled        -> terminated
#
# Two consequences. A count of registrations is not a count of towers: only C
# is a structure known to be standing. And G is not always "unbuilt": a G row
# with a construction date is an existing structure with a newer grant (a
# modification), so it is treated as standing.
STATUS_CONSTRUCTED = "C"
STATUS_GRANTED = "G"
STATUS_LABELS = {"C": "constructed", "G": "granted, not yet built",
                 "A": "cancelled", "I": "dismantled", "T": "terminated"}
# Registrations that no longer describe a structure a carrier could use.
INACTIVE_STATUSES = {"A", "I", "T"}


@dataclass
class Tower:
    registration: str
    latitude: float
    longitude: float
    height_agl_ft: Optional[float]
    structure_type: Optional[str]
    owner: Optional[str]
    distance_ft: float = 0.0
    status: Optional[str] = None
    date_constructed: Optional[str] = None
    date_dismantled: Optional[str] = None

    @property
    def distance_miles(self) -> float:
        return self.distance_ft / MILES_TO_FT

    @property
    def is_constructed(self) -> bool:
        """A structure recorded as standing.

        Status C, or a grant that carries a construction date and no
        dismantlement date, which is a modification to a tower already built.
        """
        s = (self.status or "").upper()
        if s == STATUS_CONSTRUCTED:
            return True
        return (s == STATUS_GRANTED and bool(self.date_constructed)
                and not self.date_dismantled)

    @property
    def category(self) -> str:
        """constructed | granted | inactive | unknown, for counting."""
        if self.is_constructed:
            return "constructed"
        s = (self.status or "").upper()
        if s == STATUS_GRANTED:
            return "granted"
        if s in INACTIVE_STATUSES:
            return "inactive"
        return "unknown"

    @property
    def status_label(self) -> str:
        s = (self.status or "").upper()
        if s == STATUS_GRANTED and self.is_constructed:
            return "constructed, modification granted"
        return STATUS_LABELS.get(s, s or "unknown")


def ensure_bulk(cache_dir: str = "data/asr", max_age_days: int = 7) -> str:
    """Download r_tower.zip if missing or stale. Returns the local path."""
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, "r_tower.zip")
    if os.path.exists(path):
        age = (datetime.now() - datetime.fromtimestamp(os.path.getmtime(path))).days
        if age <= max_age_days:
            return path
    try:
        with requests.get(BULK_URL, stream=True, timeout=300,
                          headers={"User-Agent": USER_AGENT}) as r:
            r.raise_for_status()
            tmp = path + ".part"
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    fh.write(chunk)
            os.replace(tmp, path)
    except Exception as e:  # noqa: BLE001
        if os.path.exists(path):
            return path  # stale beats nothing; the caller reports the age
        raise SourceError(f"could not download ASR bulk file: {e}") from e
    return path


def _dms(d, m, s, direction) -> Optional[float]:
    try:
        val = float(d) + float(m) / 60.0 + float(s) / 3600.0
    except (TypeError, ValueError):
        return None
    if str(direction).strip().upper() in ("S", "W"):
        val = -val
    return val


def _rows(zf: zipfile.ZipFile, name: str) -> Iterable[list[str]]:
    target = f"{name}.dat".upper()
    candidates = [n for n in zf.namelist() if n.upper().endswith(target)]
    if not candidates:
        raise SourceError(
            f"no {name}.dat member in the ASR archive; members are "
            f"{sorted(zf.namelist())[:8]}")
    with zf.open(candidates[0]) as fh:
        text = io.TextIOWrapper(fh, encoding="latin-1", errors="replace")
        for row in csv.reader(text, delimiter="|"):
            if row:
                yield row


def towers_near(ring, ledger: Ledger, *, radius_miles: float = 2.0,
                cache_dir: str = "data/asr") -> tuple[list[Tower], Optional[str]]:
    """Registered structures within `radius_miles` of the ring centre."""
    try:
        path = ensure_bulk(cache_dir)
    except SourceError as e:
        return [], str(e)

    age_days = (datetime.now() - datetime.fromtimestamp(os.path.getmtime(path))).days
    limit_ft = radius_miles * MILES_TO_FT
    # Degrees of latitude are ~364,000 ft; used only to pre-filter cheaply.
    deg_pad = (limit_ft / 364_000.0) * 1.2

    coords: dict[str, tuple[float, float]] = {}
    try:
        with zipfile.ZipFile(path) as zf:
            for row in _rows(zf, "CO"):
                if len(row) <= CO_LON_DIR:
                    continue
                lat = _dms(row[CO_LAT_D], row[CO_LAT_M], row[CO_LAT_S], row[CO_LAT_DIR])
                lon = _dms(row[CO_LON_D], row[CO_LON_M], row[CO_LON_S], row[CO_LON_DIR])
                if lat is None or lon is None:
                    continue
                if abs(lat - ring.lat) > deg_pad or abs(lon - ring.lon) > deg_pad * 1.4:
                    continue
                coords[row[CO_REG_NUM]] = (lat, lon)

            if not coords:
                # A real zero is still a finding, and it needs a record: without
                # one the report says nothing at all about towers, which reads
                # the same as the lookup never having run.
                _record(ledger, [], age_days)
                return [], None

            heights: dict[str, tuple[Optional[float], Optional[str]]] = {}
            for row in _rows(zf, "RA"):
                if len(row) <= max(RA_HEIGHT_AGL_M, RA_STRUCTURE_TYPE, RA_STATUS):
                    continue
                reg = row[RA_REG_NUM]
                if reg not in coords:
                    continue
                try:
                    raw = row[RA_HEIGHT_AGL_M].strip()
                    h = float(raw) * METRES_TO_FT if raw else None
                except ValueError:
                    h = None
                heights[reg] = (h, (row[RA_STRUCTURE_TYPE] or "").strip() or None,
                                (row[RA_STATUS] or "").strip().upper() or None,
                                (row[RA_DATE_CONSTRUCTED] or "").strip() or None,
                                (row[RA_DATE_DISMANTLED] or "").strip() or None)

            owners: dict[str, str] = {}
            for row in _rows(zf, "EN"):
                if len(row) <= EN_ENTITY_NAME:
                    continue
                reg = row[EN_REG_NUM]
                if reg in coords and row[EN_ENTITY_NAME].strip():
                    owners.setdefault(reg, row[EN_ENTITY_NAME].strip())
    except zipfile.BadZipFile as e:
        return [], f"ASR bulk file is corrupt: {e}"

    out: list[Tower] = []
    for reg, (lat, lon) in coords.items():
        x, y = point_to_working(lon, lat)
        dist = ((x - ring.center_x) ** 2 + (y - ring.center_y) ** 2) ** 0.5
        if dist > limit_ft:
            continue
        h, stype, status, built_on, gone_on = heights.get(
            reg, (None, None, None, None, None))
        out.append(Tower(reg, lat, lon, h, stype, owners.get(reg), dist,
                         status=status, date_constructed=built_on,
                         date_dismantled=gone_on))
    out.sort(key=lambda t: t.distance_ft)

    _record(ledger, out, age_days)
    return out, None


def summarise(towers: list[Tower]) -> dict[str, int]:
    """Counts by category, the form every consumer of this module wants."""
    def n(cat: str, within: Optional[float] = None) -> int:
        return sum(1 for t in towers if t.category == cat
                   and (within is None or t.distance_miles <= within))
    return {
        "registrations_within_2mi": len(towers),
        "constructed_within_2mi": n("constructed"),
        "constructed_within_0.5mi": n("constructed", 0.5),
        "granted_not_built_within_2mi": n("granted"),
        "inactive_within_2mi": n("inactive"),
        "unknown_status_within_2mi": n("unknown"),
    }


def _record(ledger: Ledger, towers: list[Tower], age_days: int) -> str:
    c = summarise(towers)
    return ledger.from_dataset(
        c,
        f"FCC ASR registrations within 2 miles of the ring centre (bulk file "
        f"{age_days} day(s) old): {c['registrations_within_2mi']} in total. "
        f"{c['constructed_within_2mi']} are constructed structures "
        f"({c['constructed_within_0.5mi']} within half a mile), "
        f"{c['granted_not_built_within_2mi']} are granted but not built, and "
        f"{c['inactive_within_2mi']} are cancelled, dismantled or terminated "
        f"registrations. Registration is required only for structures over 200 ft "
        f"or near an airport glide slope, so shorter structures may be absent.",
        url=BULK_URL, layer="r_tower CO/RA/EN",
    )
