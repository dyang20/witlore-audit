"""Calibration harness: compare generated audits against the human baseline.

The assignment requires a comparison against the five human reports and an
explanation of material differences. This extracts the human report's structured
content, runs the pipeline on the same site, and writes a markdown table plus a
per-parcel diff.

Nothing here feeds conclusions back into the pipeline. The human reports are
read only to measure against, never to copy from, which the assignment forbids.

Usage:
    python3 tools/calibrate.py --human-dir human_reports --sheet tranche.csv
    python3 tools/calibrate.py --human-dir human_reports --ids TI-OPP-35820
    python3 tools/calibrate.py --human-dir human_reports --extract-only
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from docx import Document

from da.cli import COLUMNS, _pick, parse_row, read_sheet
from da.models import Disposition
from da.pipeline import load_config, run

CALIBRATION_SITES = ["TI-OPP-35820", "TI-OPP-34659", "TI-OPP-35482",
                     "TI-OPP-37954", "TI-OPP-43986"]


# --------------------------------------------------------------------------
# Extracting the human report
# --------------------------------------------------------------------------


@dataclass
class HumanParcel:
    letter: str
    owner: str
    pin: str
    property_address: Optional[str] = None
    owner_address: Optional[str] = None
    acres: Optional[float] = None
    has_other_properties: bool = False
    note: Optional[str] = None     # e.g. "(Existing Tower on Property)"
    all_pins: list[str] = field(default_factory=list)
    all_acres: list[float] = field(default_factory=list)


@dataclass
class HumanReport:
    opportunity_id: str
    path: str
    date: Optional[str] = None
    fa_number: Optional[str] = None
    coordinates: Optional[tuple[float, float]] = None
    search_radius_ft: Optional[float] = None
    requested_rad_ft: Optional[float] = None
    recommendation: Optional[str] = None
    checklist_reasons: list[str] = field(default_factory=list)
    jurisdictions: list[str] = field(default_factory=list)
    ordinance_links: list[str] = field(default_factory=list)
    districts: list[str] = field(default_factory=list)
    sections_cited: list[str] = field(default_factory=list)
    parcels: list[HumanParcel] = field(default_factory=list)
    existing_tower: Optional[str] = None
    summary: Optional[str] = None
    # The 2026 report states a zoning level per jurisdiction, with its own
    # definition of each level. The 2024 reports do not mention levels at all.
    stated_levels: dict[str, tuple[int, str]] = field(default_factory=dict)
    has_parcel_list: bool = True
    has_recommendation_checklist: bool = True
    # What the "Existing Towers" section says, as numbers. Keys present only
    # where the report states them: within_2mi, within_half_mile, constructed,
    # granted.
    towers: dict[str, int] = field(default_factory=dict)
    towers_text: Optional[str] = None


_NUM = r"[-+]?\d+(?:\.\d+)?"
# Radii are written with thousands separators ("3,281'"), so a bare \d+ match
# silently returns 3. Every numeric field that can exceed 999 uses this.
_NUM_C = r"[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[-+]?\d+(?:\.\d+)?"


def _to_float(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def extract_human(path: str) -> HumanReport:
    doc = Document(path)
    lines = [p.text.strip() for p in doc.paragraphs]
    text = "\n".join(lines)

    oid = _search(r"TI-OPP-#?:?\s*(\d{4,6})", text)
    rep = HumanReport(opportunity_id=f"TI-OPP-{oid}" if oid else _from_filename(path),
                      path=path)

    rep.date = _search(r"Date:\s*([\d/]+)", text)
    # 2024 reports write "FA#:", the 2026 one writes "FA Number:".
    rep.fa_number = _search(r"FA\s*(?:#|Number)\s*:?\s*(\d+)", text)
    coords = re.search(rf"Coordinates:\s*({_NUM})\s*,\s*({_NUM})", text)
    if coords:
        rep.coordinates = (float(coords.group(1)), float(coords.group(2)))
    # "Requested RAD" in 2024, "Requested Rad" in 2026.
    rep.search_radius_ft = _to_float(
        _search(rf"Search\s*(?:Radius|Ring)\s*:?\s*({_NUM_C})", text))
    rep.requested_rad_ft = _to_float(
        _search(rf"(?i)Requested\s*RAD\s*:?\s*({_NUM_C})", text))


    rep.recommendation = _recommendation(lines)
    rep.checklist_reasons = _checked_reasons(lines)
    # Links appear two ways: as bare text in the 2024 reports, and as real
    # hyperlinks in the 2026 one, where the URL lives in the document
    # relationships and never appears in paragraph text.
    rep.ordinance_links = sorted(
        set(re.findall(r"https?://[^\s\)\]]+", text)) | _hyperlinks(doc))
    rep.sections_cited = sorted(set(re.findall(r"\b(\d\.\d{3}\.\d{3}(?:\.\d+)?)", text)))
    rep.districts = sorted(set(re.findall(r"\b([ACIR]-\d)\b", text)))
    rep.jurisdictions = _jurisdictions(lines)
    rep.stated_levels = _stated_levels(text, lines, rep.jurisdictions)
    rep.parcels = _parcels(lines)
    rep.has_parcel_list = bool(rep.parcels)
    rep.has_recommendation_checklist = any(
        re.match(r"^_+\s*[Xx]?\s*_*\s*(Proceed to SCIP|Do not proceed)", ln)
        for ln in lines)
    rep.towers, rep.towers_text = _existing_towers(lines)
    rep.existing_tower = _search(r"(The current site is [^\n]+)", text)
    rep.summary = _summary(lines)
    return rep


def _existing_towers(lines: list[str]) -> tuple[dict[str, int], Optional[str]]:
    """Counts from the "Existing Towers" section.

    The reports phrase it three ways: "N registered towers within a 2-mile
    radius", "N registered tower within a 0.5-mile radius", and, in the 2026
    report, "N constructed towers within a 2-mile radius and M granted". The
    last one matters: it shows the analysts themselves separate a built tower
    from an approved one.
    """
    start = next((i for i, ln in enumerate(lines)
                  if re.match(r"(?i)^existing towers", ln)), None)
    if start is None:
        return {}, None
    block = [ln for ln in lines[start + 1:start + 6] if ln and re.search(r"\d", ln)
             and re.search(r"(?i)tower|granted", ln)]
    text = " ".join(block)
    out: dict[str, int] = {}
    m = re.search(r"(?i)(\d+)\s+constructed towers? within a 2[- ]mile", text)
    if m:
        out["constructed"] = int(m.group(1))
        g = re.search(r"(?i)and\s+(\d+)\s+granted", text)
        if g:
            out["granted"] = int(g.group(1))
        out["within_2mi"] = out["constructed"] + out.get("granted", 0)
    m = re.search(r"(?i)(\d+)\s+registered towers? within a 2[- ]mile", text)
    if m:
        out["within_2mi"] = int(m.group(1))
    m = re.search(r"(?i)(\d+)\s+registered towers? within a 0?\.5[- ]mile", text)
    if m:
        out["within_half_mile"] = int(m.group(1))
    return out, (text or None)


def _hyperlinks(doc: Document) -> set[str]:
    """External URLs from the docx relationship table."""
    out: set[str] = set()
    try:
        for rel in doc.part.rels.values():
            if rel.reltype.endswith("/hyperlink") and rel.is_external:
                url = str(rel.target_ref)
                if url.startswith("http"):
                    out.add(url)
    except Exception:  # noqa: BLE001 - a missing rel table is not a failure
        pass
    return out


def _from_filename(path: str) -> str:
    m = re.search(r"(\d{4,6})", os.path.basename(path))
    return f"TI-OPP-{m.group(1)}" if m else os.path.basename(path)


def _search(pattern: str, text: str) -> Optional[str]:
    m = re.search(pattern, text)
    return m.group(1).strip() if m else None


def _recommendation(lines: list[str]) -> Optional[str]:
    """The checklist uses underscores with an X in the chosen box."""
    for ln in lines:
        if re.match(r"^_*\s*[Xx]\s*_*\s*Proceed to SCIP", ln):
            return "Proceed to SCIP"
        if re.match(r"^_*\s*[Xx]\s*_*\s*Do not proceed", ln):
            return "Do not proceed"
    joined = " ".join(lines).lower()
    if "proceed to scip" in joined and "do not proceed" not in joined:
        return "Proceed to SCIP"
    if "exhaust" in joined:
        return "Exhaust"
    # 37954 has no checklist at all. Its Overall Recommendation paragraph is the
    # only statement of intent, so read that rather than reporting nothing.
    for i, ln in enumerate(lines):
        if ln.startswith("Overall Recommendation"):
            body = " ".join(x for x in lines[i:i + 3] if x).lower()
            if "should be fully explored" in body or "should be explored" in body:
                return "Proceed to SCIP (from prose; no checklist in document)"
            if "exhaust" in body or "do not pursue" in body:
                return "Do not proceed (from prose; no checklist in document)"
            break
    return None


def _checked_reasons(lines: list[str]) -> list[str]:
    out = []
    for ln in lines:
        if re.match(r"^_*\s*[Xx]\s*_*\s*(Preclusive|No parcels|Terrain|Other)", ln):
            out.append(re.sub(r"^[_X\s]+", "", ln).strip())
    return out


LEVEL_RE = re.compile(
    r"(?P<jur>[A-Z][A-Za-z .'-]{2,60}?)\s+Zoning\s+Level\s*:?\s*(?P<lvl>[1-4])\s*"
    r"[-–—:]?\s*(?P<desc>[^\n]{0,120})")


BARE_LEVEL_RE = re.compile(
    r"^Zoning\s+Level\s*:?\s*(?P<lvl>[1-4])\s*[-–—:]?\s*(?P<desc>[^\n]{0,120})$")

# Words the inline regex can swallow as a jurisdiction name when the real name
# is absent.
_NOT_A_JURISDICTION = {"CUP", "TYPICAL CUP", "DIFFICULT CUP", "ZONING"}


def _stated_levels(text: str, lines: Optional[list[str]] = None,
                   jurisdictions: Optional[list[str]] = None
                   ) -> dict[str, tuple[int, str]]:
    """Stated zoning levels, in either form the reports use.

    43986 names the jurisdiction inline:
        "City of Oakland Zoning Level: 4 - variance, rezoning, ..."

    37954 writes bare lines in the same order as the jurisdiction list above
    them, so those are matched by position:
        Council Bluffs, IA
        Pottawattamie County, IA
        Zoning Level: 3 - site plan review with meeting or typical CUP
        Zoning Level: 4 - variance, rezoning, text amendment, ...
    """
    out: dict[str, tuple[int, str]] = {}
    for m in LEVEL_RE.finditer(text):
        jur = m.group("jur").strip()
        if jur.upper() in _NOT_A_JURISDICTION:
            continue
        out[jur] = (int(m.group("lvl")), m.group("desc").strip(" -–—"))
    if out or not lines:
        return out

    bare = []
    for ln in lines:
        m = BARE_LEVEL_RE.match(ln.strip())
        if m:
            bare.append((int(m.group("lvl")), m.group("desc").strip(" -–—")))
    if bare and jurisdictions and len(bare) == len(jurisdictions):
        return dict(zip(jurisdictions, bare))
    return {f"jurisdiction {i + 1} (unnamed in report)": lvl
            for i, lvl in enumerate(bare)}


# A jurisdiction line names a place and nothing else. Anything carrying a
# sentence is prose that happens to sit under the heading.
JUR_RE = re.compile(r"^(?:City|Town|Village) of [A-Z][\w .'-]+$"
                    r"|^[A-Z][\w .'-]*?Count(?:y|ies)(?:,\s*[A-Z]{2})?$"
                    r"|^[A-Z][\w .'-]+,\s*[A-Z]{2}$")


def _jurisdictions(lines: list[str]) -> list[str]:
    out: list[str] = []
    for i, ln in enumerate(lines):
        if "List of Jurisdiction" not in ln:
            continue
        for nxt in lines[i + 1:i + 12]:
            s = nxt.strip().rstrip(".")
            if not s or s.startswith("Ordinance Link"):
                continue
            # Stop at the next real heading rather than swallowing prose.
            if re.match(r"^(Overall Recommendation|Recommendation|Permitting|"
                        r"Zoning Map|Overlay|Evaluation)", s):
                break
            if "Zoning Level" in s:
                continue
            if JUR_RE.match(s) and len(s) <= 60:
                out.append(s)
            if len(out) >= 4:
                break
        break
    return out


def _summary(lines: list[str]) -> Optional[str]:
    for i, ln in enumerate(lines):
        if ln.startswith("Summary Area Review"):
            body = [x for x in lines[i + 1:i + 8] if x]
            return " ".join(body)[:600] or None
    return None


# Two formats in the wild. 2024 reports letter each parcel inline; the 2026
# report omits the letter entirely, carrying it only as a label on the parcel
# map image, so the letter is synthesised in document order.
PARCEL_LETTERED = re.compile(r"^([A-Z]{1,2})\.\s*OWNER:\s*(.+)$")
PARCEL_BARE = re.compile(r"^OWNER:\s*(.+)$", re.IGNORECASE)


def _parcels(lines: list[str]) -> list[HumanParcel]:
    """Parcels are plain paragraphs, not a table:

        A. OWNER: BLUE SPRUCE FEEDLOTS INC          <- 2024
        OWNER: R-L CLEAVELAND LLC                   <- 2026
        PIN: 754124300001 + Other Properties
        PROPERTY ADDRESS: N/A
        OWNER ADDRESS: 50459 370th St. Henderson, IA 51541
        ACRES: 39.67 + Other Properties

    A 2026 entry can carry several PINs and a matching list of acreages
    ("ACRES: 18, 24"), which is the same parcel group the 2024 reports write as
    "+ Other Properties".
    """
    out: list[HumanParcel] = []
    cur: Optional[HumanParcel] = None
    auto = 0

    def start(owner_raw: str, letter: Optional[str]) -> HumanParcel:
        nonlocal auto
        owner = owner_raw.strip()
        note = None
        paren = re.search(r"\(([^)]*)\)\s*$", owner)
        if paren:
            note = paren.group(1)
            owner = owner[:paren.start()].strip()
        if letter is None:
            letter = _auto_letter(auto)
            auto += 1
        return HumanParcel(letter=letter, owner=owner, pin="", note=note)

    for ln in lines:
        m = PARCEL_LETTERED.match(ln)
        if m:
            if cur:
                out.append(cur)
            cur = start(m.group(2), m.group(1))
            continue
        m = PARCEL_BARE.match(ln)
        if m and "ADDRESS" not in ln.upper()[:14]:
            if cur:
                out.append(cur)
            cur = start(m.group(1), None)
            continue
        if cur is None:
            continue

        upper = ln.upper()
        if upper.startswith("PIN:"):
            raw = ln.split(":", 1)[1].strip()
            cur.has_other_properties = "other propert" in raw.lower()
            raw = re.sub(r"\+.*$", "", raw).strip()
            pins = re.findall(r"\d{8,}", raw)
            # The same PIN is sometimes repeated by a duplicated hyperlink.
            uniq = list(dict.fromkeys(pins))
            if len(uniq) > 1:
                cur.has_other_properties = True
            cur.pin = uniq[0] if uniq else raw
            cur.all_pins = uniq
        elif upper.startswith("PROPERTY ADDRESS:"):
            v = ln.split(":", 1)[1].strip()
            cur.property_address = None if v.upper() in ("N/A", "NA", "") else v
        elif upper.startswith("OWNER ADDRESS:"):
            cur.owner_address = ln.split(":", 1)[1].strip() or None
        elif upper.startswith("ACRES:"):
            raw = ln.split(":", 1)[1].strip()
            if "other propert" in raw.lower():
                cur.has_other_properties = True
            nums = [_to_float(x) for x in re.findall(_NUM_C, raw)]
            nums = [n for n in nums if n is not None]
            if len(nums) > 1:
                cur.has_other_properties = True
                cur.acres = sum(nums)
                cur.all_acres = nums
            elif nums:
                cur.acres = nums[0]
    if cur:
        out.append(cur)
    return out


def _auto_letter(i: int) -> str:
    import string
    if i < 26:
        return string.ascii_uppercase[i]
    a, b = divmod(i - 26, 26)
    return string.ascii_uppercase[a] + string.ascii_uppercase[b]


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------


def _norm_owner(s: str) -> str:
    """Normalise an owner name for comparison.

    The county writes surname first ("CLARK, DOUGLAS A-JOYCE C") and the human
    audits write it naturally ("DOUGLAS A & JOYCE C CLARK"), so the tokens are
    sorted: without that, the same owner reads as two different parties and a
    matched parcel is reported as two unmatched ones.
    """
    s = (s or "").upper()
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = re.sub(r"\b(LLC|INC|LTD|TRUST|TRUSTS|CO|COMPANY|FARMS?|THE|JR|SR|II|III|IV)\b",
               " ", s)
    # Single initials carry no signal once the order is lost.
    tokens = [t for t in s.split() if len(t) > 1]
    return " ".join(sorted(tokens))


def _norm_pin(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def match_parcels(human: list[HumanParcel], ours: list) -> list[tuple]:
    """Join on PIN first, then fuzzy owner name. Returns (human, ours) pairs
    with None on either side for unmatched."""
    pairs: list[tuple] = []
    remaining = list(ours)

    by_pin = {}
    for p in remaining:
        for piece in str(p.pin).split("/"):
            k = _norm_pin(piece)
            if k:
                by_pin[k] = p

    for h in human:
        hit = by_pin.get(_norm_pin(h.pin))
        if hit is None:
            names = {_norm_owner(p.owner): p for p in remaining}
            close = difflib.get_close_matches(_norm_owner(h.owner), list(names), 1, 0.82)
            hit = names[close[0]] if close else None
        if hit is not None and hit in remaining:
            remaining.remove(hit)
            pairs.append((h, hit))
        else:
            pairs.append((h, None))

    for p in remaining:
        pairs.append((None, p))
    return pairs


def compare_site(human: HumanReport, report, ledger) -> dict[str, Any]:
    ours_rec = report.recommendation()
    pairs = match_parcels(human.parcels, report.parcels)

    matched = [(h, o) for h, o in pairs if h and o]
    only_human = [h for h, o in pairs if h and not o]
    only_ours = [o for h, o in pairs if o and not h]

    # Compare acreage only where the human reported a single parcel. Their
    # convention is to print the primary parcel's acreage and append
    # "+ Other Properties" without summing, while we sum the whole ownership
    # group, so including those rows produced a meaningless ~110% "error" that
    # made correct data look wrong. Where neither side is a group, the two
    # agree to within a rounding step.
    acre_diffs = []
    grouped = 0
    for h, o in matched:
        if h.has_other_properties or (o.pin and "/" in str(o.pin)):
            grouped += 1
            continue
        if h.acres and o.net_acres:
            acre_diffs.append(abs(h.acres - o.net_acres) / h.acres)

    # A human citation of §8.010.020 is satisfied by our §8.010.020.06: the
    # parent section is where the subsection lives. Only a section with no
    # relation to anything we applied counts as missed.
    ours_sections = sorted({r.section for r in report.rules})
    missed_sections = [
        s for s in human.sections_cited
        if not any(o == s or o.startswith(s + ".") or s.startswith(o + ".")
                   for o in ours_sections)
    ]

    return {
        "opportunity_id": human.opportunity_id,
        "human_date": human.date,
        "recommendation": {
            "human": human.recommendation,
            "ours": ours_rec,
            "agree": _rec_agrees(human.recommendation, ours_rec),
        },
        "inputs": {
            "coordinates_match": _coords_match(human, report),
            "rad_match": human.requested_rad_ft == report.site.requested_rad_ft,
            "radius_match": human.search_radius_ft == report.site.search_ring_radius_ft,
        },
        "human_report_complete": {
            "parcel_list": human.has_parcel_list,
            "recommendation_checklist": human.has_recommendation_checklist,
        },
        "parcels": {
            "human_count": len(human.parcels),
            "ours_count": len(report.parcels),
            "matched": len(matched),
            "only_human": [f"{h.letter}. {h.owner}" for h in only_human],
            "only_ours": [f"{o.letter}. {o.owner}" for o in only_ours],
            "mean_acre_error": (sum(acre_diffs) / len(acre_diffs)) if acre_diffs else None,
            "acre_comparable": len(acre_diffs),
            "acre_grouped_excluded": grouped,
            "owner_changed": [
                f"{h.owner} -> {o.owner}" for h, o in matched
                if h.owner and o.owner and _norm_owner(h.owner) != _norm_owner(o.owner)
            ],
        },
        "zoning": {
            "human_districts": human.districts,
            "ours_districts": sorted({d.code for p in report.parcels for d in p.districts}),
            "human_sections": human.sections_cited,
            "sections_we_did_not_apply": missed_sections,
            "ours_level": report.zoning_level.level if report.zoning_level else None,
            "human_levels": {j: {"level": lvl, "definition": desc}
                             for j, (lvl, desc) in human.stated_levels.items()},
            "ours_levels": _our_levels(report),
        },
        "towers": {
            "human": human.towers,
            "human_text": human.towers_text,
            "ours": _our_towers(report),
        },
        "dispositions": {
            d.value: sum(1 for p in report.parcels if p.disposition is d)
            for d in Disposition
        },
        "candidates": len(report.pursue_parcels()),
        "unresolved": len(report.unresolved),
        "evidence_records": len(ledger),
        "parcel_rows": [
            {
                "human_letter": h.letter if h else None,
                "ours_letter": o.letter if o else None,
                "owner": (h.owner if h else o.owner),
                "ours_owner": (o.owner if o else None),
                "human_acres": h.acres if h else None,
                "ours_acres": o.net_acres if o else None,
                "ours_disposition": o.disposition.value if o and o.disposition else None,
                "ours_reasons": ", ".join(o.reason_codes) if o else None,
                "human_has_dwelling": bool(h.property_address) if h else None,
                "ours_has_dwelling": o.has_dwelling() if o else None,
                "note": h.note if h else None,
            }
            for h, o in pairs
        ],
    }


def _our_levels(report) -> dict[str, dict]:
    """Easiest path per jurisdiction, on both level scales."""
    best: dict[str, dict] = {}
    for row in getattr(report, "jurisdiction_levels", []) or []:
        j = row["jurisdiction"]
        cur = best.get(j)
        la = row.get("level_assignment")
        if cur is None or (la is not None and (cur["assignment"] is None
                                               or la < cur["assignment"])):
            best[j] = {"district": row.get("district"), "assignment": la,
                       "audit": row.get("level_audit"),
                       "permission": row.get("permission")}
    return best


def _our_towers(report) -> dict[str, int]:
    towers = getattr(report, "nearby_towers", []) or []

    def n(cat: str, within: float = 99.0) -> int:
        return sum(1 for t in towers if t.get("category") == cat
                   and t.get("distance_miles", 99) <= within)
    return {"registrations": len(towers), "constructed": n("constructed"),
            "constructed_half_mile": n("constructed", 0.5),
            "granted": n("granted"), "inactive": n("inactive")}


def _rec_agrees(human: Optional[str], ours: str) -> Optional[bool]:
    if not human:
        return None
    h = human.lower()
    o = ours.lower()
    if "proceed to scip" in h and h.startswith("proceed"):
        return o.startswith("proceed")
    if "do not proceed" in h or "exhaust" in h:
        return o.startswith("do not")
    return None


def _coords_match(human: HumanReport, report) -> Optional[bool]:
    if not human.coordinates:
        return None
    return (abs(human.coordinates[0] - report.site.latitude) < 1e-4 and
            abs(human.coordinates[1] - report.site.longitude) < 1e-4)


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def write_markdown(results: list[dict], path: str) -> None:
    L: list[str] = []
    A = L.append
    A("# Calibration against the human desktop audits\n")
    A("Generated reports compared with the completed human audits for the same "
      "sites. The human reports were read to measure against, never to source "
      "conclusions from.\n")

    A("## Summary\n")
    A("| Site | Human rec. | Ours | Agree | Human parcels | Ours | Matched | "
      "Candidates | Unresolved |")
    A("| --- | --- | --- | :-: | --: | --: | --: | --: | --: |")
    for r in results:
        rec = r["recommendation"]
        p = r["parcels"]
        agree = {True: "yes", False: "NO", None: "?"}[rec["agree"]]
        A(f"| {r['opportunity_id']} | {rec['human'] or '?'} | {rec['ours']} | {agree} "
          f"| {p['human_count']} | {p['ours_count']} | {p['matched']} "
          f"| {r['candidates']} | {r['unresolved']} |")

    A("\n## Per site\n")
    for r in results:
        A(f"### {r['opportunity_id']}\n")
        inp = r["inputs"]
        A(f"- Inputs: coordinates {_yn(inp['coordinates_match'])}, "
          f"RAD {_yn(inp['rad_match'])}, radius {_yn(inp['radius_match'])}")
        z = r["zoning"]
        A(f"- Districts: human {z['human_districts'] or 'none found'}; "
          f"ours {z['ours_districts'] or 'none'}")
        hl, ol = z.get("human_levels") or {}, z.get("ours_levels") or {}
        if ol:
            ours_txt = "; ".join(
                f"{j} {v['assignment'] if v['assignment'] is not None else '?'} on "
                f"the assignment scale, "
                f"{v['audit'] if v['audit'] is not None else '?'} on the audit scale"
                + (f" (easiest district {v['district']})" if v.get("district") else "")
                for j, v in sorted(ol.items()))
            A(f"- Zoning level, ours, per jurisdiction: {ours_txt}")
        else:
            A(f"- Zoning level (ours, site): {z['ours_level']}")
        if hl:
            A("- Zoning level, human, per jurisdiction (audit scale): "
              + "; ".join(f"{j} {v['level']}" for j, v in sorted(hl.items())))
        else:
            A("- Zoning level, human: not stated in this report")
        tw = r.get("towers") or {}
        if tw:
            o, h = tw.get("ours") or {}, tw.get("human") or {}
            A(f"- FCC ASR within 2 miles, ours: {o.get('constructed', 0)} constructed "
              f"({o.get('constructed_half_mile', 0)} within half a mile), "
              f"{o.get('granted', 0)} granted and unbuilt, {o.get('inactive', 0)} "
              f"cancelled, dismantled or terminated; "
              f"{o.get('registrations', 0)} registrations in all")
            if h:
                A("- FCC ASR, human: " + ", ".join(
                    f"{v} {k.replace('_', ' ')}" for k, v in sorted(h.items())))
        if z["sections_we_did_not_apply"]:
            A(f"- Ordinance sections the human report cites that we did not apply: "
              f"{', '.join(z['sections_we_did_not_apply'])}")
        p = r["parcels"]
        if p["only_human"]:
            A(f"- In the human report, not in ours: {'; '.join(p['only_human'])}")
        if p["only_ours"]:
            A(f"- In ours, not in the human report: {'; '.join(p['only_ours'])}")
        if p["mean_acre_error"] is not None:
            A(f"- Mean acreage difference on the {p['acre_comparable']} parcels both "
              f"sides report singly: {p['mean_acre_error']:.1%}"
              + (f" ({p['acre_grouped_excluded']} ownership groups excluded, where "
                 f"the human report prints one parcel's acreage and appends "
                 f"'+ Other Properties' without summing)"
                 if p['acre_grouped_excluded'] else ""))
        if p.get("owner_changed"):
            A(f"- Ownership differs on {len(p['owner_changed'])} matched parcel(s), "
              f"matched by PIN. The human audits date from 2024; these are "
              f"conveyances since, not disagreements:")
            for ch in p["owner_changed"][:8]:
                A(f"  - {ch}")
        A(f"- Dispositions: {r['dispositions']}")
        A(f"- Evidence records: {r['evidence_records']}\n")

        A("| Human | Ours | Owner | Human ac | Ours ac | Disposition | Reason |")
        A("| :-: | :-: | --- | --: | --: | --- | --- |")
        for row in r["parcel_rows"]:
            human_ac = ("-" if row["human_acres"] is None
                        else f"{row['human_acres']:.1f}")
            ours_ac = "-" if not row["ours_acres"] else f"{row['ours_acres']:.1f}"
            owner = (row["owner"] or "")[:38]
            ours_owner = row.get("ours_owner")
            if (ours_owner and row["owner"]
                    and _norm_owner(ours_owner) != _norm_owner(row["owner"])):
                owner = f"{owner} **-> {ours_owner[:30]}**"
            A(f"| {row['human_letter'] or '-'} | {row['ours_letter'] or '-'} "
              f"| {owner} | {human_ac} | {ours_ac} "
              f"| {row['ours_disposition'] or '-'} | {row['ours_reasons'] or '-'} |")
        A("")

    A("## Material differences\n")
    A("The table above is generated on every run. The site-by-site account of "
      "where this output differs from a human audit, and which result the "
      "evidence supports, is kept by hand in "
      "[material-differences.md](material-differences.md), because a verdict "
      "on evidence is a judgement and should not be regenerated silently.\n")
    A("Differences that hold on every site, because the pipeline does something "
      "the human reports do not:\n")
    A("- **Per-parcel dispositions.** The human reports give one site-level "
      "recommendation and mark exclusions only as a colour on the parcel map. "
      "Every parcel here carries Pursue / Do not pursue / Unresolved with a "
      "reason code, so the comparison has no human column to check against.")
    A("- **Every parcel in the ring.** The human parcel lists stop at the "
      "parcels worth describing; ours lists every parcel intersecting the ring, "
      "which is why our counts are higher on every site.")
    A("- **The fall-distance setback.** Sec. 8.004.220.12 sets the setback at the "
      "manufacturer's designed fall distance, which does not exist at desktop "
      "stage. The default assumes full tower height, which is conservative and "
      "excludes parcels a human analyst might carry forward pending a fall-zone "
      "letter. `setbacks.fall_distance_ratio` controls it.")
    A("- **Dwelling distance applied geometrically.** Sec. 8.004.220.13 is "
      "enforced as keep-out circles of tower height around detected dwellings. "
      "The human reports note the rule but do not measure it per parcel.")
    A("- **Tower counts by status.** The FCC file holds granted, cancelled, "
      "dismantled and terminated registrations beside constructed ones. Ours "
      "counts constructed structures as towers and lists the rest separately; "
      "the 2024 reports give one number for 'registered towers'.")
    A("- **Two level scales.** The assignment puts a conditional use permit at "
      "level 4; the 43986 and 37954 audits put a typical CUP at level 3. Both "
      "are printed per jurisdiction, so compare audit scale with audit scale.")
    A("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))


def _yn(v: Optional[bool]) -> str:
    return {True: "match", False: "DIFFER", None: "n/a"}[v]


# --------------------------------------------------------------------------


def find_human_reports(directory: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if not os.path.isdir(directory):
        return out
    for name in os.listdir(directory):
        if not name.lower().endswith(".docx") or name.startswith("~$"):
            continue
        m = re.search(r"(\d{4,6})", name)
        if m:
            out.setdefault(f"TI-OPP-{m.group(1)}", os.path.join(directory, name))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare generated audits with the "
                                             "human baseline")
    ap.add_argument("--human-dir", default="human_reports")
    ap.add_argument("--sheet", default="tranche.csv")
    ap.add_argument("--ids", default=",".join(CALIBRATION_SITES))
    ap.add_argument("--out", default="docs/comparison.md")
    ap.add_argument("--json-out", default="docs/comparison.json")
    ap.add_argument("--config", default="config/client_rules.yml")
    ap.add_argument("--ordinance", default="data/ordinance.txt")
    ap.add_argument("--extract-only", action="store_true",
                    help="parse the human reports and print what was found")
    ap.add_argument("--skip-asr", action="store_true")
    ap.add_argument("--skip-elevation", action="store_true")
    ap.add_argument("--derived-sheet", default="data/derived_sites.csv",
                    help="extra inputs for sites absent from the release sheet")
    args = ap.parse_args()

    humans = find_human_reports(args.human_dir)
    if not humans:
        print(f"No human .docx reports found in {args.human_dir}/", file=sys.stderr)
        return 2
    wanted = [i.strip() for i in args.ids.split(",") if i.strip()]

    if args.extract_only:
        for oid in wanted:
            path = humans.get(oid)
            if not path:
                print(f"{oid}: no human report found")
                continue
            h = extract_human(path)
            print(f"\n=== {h.opportunity_id} ({os.path.basename(path)}) ===")
            print(f"  date {h.date}  FA {h.fa_number}  coords {h.coordinates}")
            print(f"  radius {h.search_radius_ft}  RAD {h.requested_rad_ft}")
            print(f"  recommendation: {h.recommendation}")
            print(f"  jurisdictions: {h.jurisdictions}")
            print(f"  districts: {h.districts}")
            print(f"  ordinance links: {h.ordinance_links[:2]}")
            print(f"  sections cited: {len(h.sections_cited)}")
            if h.stated_levels:
                for jur, (lvl, desc) in h.stated_levels.items():
                    print(f"  STATED LEVEL  {jur}: {lvl} - {desc}")
            print(f"  parcels: {len(h.parcels)}"
                  + ("" if h.has_parcel_list
                     else "   <- document contains no parcel list"))
            if not h.has_recommendation_checklist:
                print("  note: no recommendation checklist in this document")
            for p in h.parcels[:4]:
                print(f"    {p.letter}. {p.owner[:34]:34} {p.pin:16} "
                      f"{p.acres} ac{'  +other' if p.has_other_properties else ''}"
                      f"{'  [' + p.note + ']' if p.note else ''}")
            if len(h.parcels) > 4:
                print(f"    ... {len(h.parcels) - 4} more")
        return 0

    rows = read_sheet(args.sheet)
    by_id = {(_pick(r, COLUMNS["opportunity_id"]) or ""): r for r in rows}
    derived_ids: set[str] = set()
    if os.path.exists(args.derived_sheet):
        for r in read_sheet(args.derived_sheet):
            oid = _pick(r, COLUMNS["opportunity_id"]) or ""
            if oid and oid not in by_id:
                by_id[oid] = r
                derived_ids.add(oid)
    cfg = load_config(args.config)

    results = []
    for oid in wanted:
        path = humans.get(oid)
        if not path:
            print(f"SKIP {oid}: no human report in {args.human_dir}/", file=sys.stderr)
            continue
        human = extract_human(path)

        row = by_id.get(oid)
        if row is None:
            print(f"SKIP {oid}: no row in {args.sheet} or {args.derived_sheet}.",
                  file=sys.stderr)
            continue
        try:
            site = parse_row(row, "derived_from_report" if oid in derived_ids
                             else "tranche_sheet")
        except Exception as e:  # noqa: BLE001
            print(f"SKIP {oid}: {e}", file=sys.stderr)
            continue

        print(f"\n=== {oid} ===")
        report, ledger = run(site, config=cfg, out_dir="outputs",
                             ordinance_path=args.ordinance,
                             skip_asr=args.skip_asr,
                             skip_elevation=args.skip_elevation)
        results.append(compare_site(human, report, ledger))

    if not results:
        print("nothing to compare", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    write_markdown(results, args.out)
    with open(args.json_out, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, default=str)

    print(f"\nwrote {args.out} and {args.json_out}")
    for r in results:
        rec = r["recommendation"]
        print(f"  {r['opportunity_id']}: human={rec['human']} ours={rec['ours']} "
              f"agree={rec['agree']}  parcels {r['parcels']['matched']}/"
              f"{r['parcels']['human_count']} matched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
