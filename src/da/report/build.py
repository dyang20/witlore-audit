"""Word report, matching the structure of the human "COMPLETE" audits.

Section order follows the human reports (header block, recommendation checklist,
summary, maps, zoning, permitting, parcel data, ASR, floodplain, wetlands), with
three additions the assignment asks for and the human reports do not have:

  - a per-parcel disposition with a reason code
  - an Unresolved section saying what is missing, why it matters and how to fix
  - a provenance appendix: one row per evidence id

Narrative is templated rather than model-written. Templates cannot invent a
fact, which matters more here than prose that reads like the human analyst.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor

from ..evidence import Ledger
from ..geo.crs import SQFT_PER_ACRE
from ..models import AuditReport, Disposition, Parcel, ZoningLevel

GREEN = RGBColor(0x1B, 0x5E, 0x20)
RED = RGBColor(0xB7, 0x1C, 0x1C)
AMBER = RGBColor(0xE6, 0x51, 0x00)
GREY = RGBColor(0x60, 0x60, 0x60)

DISPOSITION_COLOR = {
    Disposition.PURSUE: GREEN,
    Disposition.DO_NOT_PURSUE: RED,
    Disposition.UNRESOLVED: AMBER,
}


def build(report: AuditReport, ledger: Ledger, out_path: str,
          assumptions: Optional[dict] = None) -> str:
    doc = Document()
    _styles(doc)

    _header(doc, report)
    _recommendation(doc, report)
    _unresolved(doc, report)
    _summary(doc, report)
    _maps(doc, report, ("street", "topo"))
    _zoning(doc, report)
    _maps(doc, report, ("zoning",))
    _overlays(doc, report)
    _permitting(doc, report)
    _parcels(doc, report)
    _maps(doc, report, ("parcel",))
    _towers(doc, report)
    _maps(doc, report, ("asr", "flood", "wetlands"))
    _assumptions(doc, report, assumptions or {})
    _provenance(doc, ledger)

    doc.save(out_path)
    return out_path


# --------------------------------------------------------------------------


def _styles(doc: Document) -> None:
    st = doc.styles["Normal"]
    st.font.name = "Calibri"
    st.font.size = Pt(10)


def _h(doc: Document, text: str, level: int = 1) -> None:
    doc.add_heading(text, level=level)


def _kv_table(doc: Document, rows: list[tuple[str, Any]]) -> None:
    t = doc.add_table(rows=0, cols=2)
    t.style = "Light Grid Accent 1"
    for k, v in rows:
        cells = t.add_row().cells
        cells[0].text = str(k)
        cells[1].text = "" if v is None else str(v)
        for p in cells[0].paragraphs:
            for r in p.runs:
                r.bold = True


def _note(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.italic = True
    r.font.size = Pt(8)
    r.font.color.rgb = GREY


def _cite(ids: list[str]) -> str:
    return f" [{', '.join(ids)}]" if ids else ""


# --------------------------------------------------------------------------


def _header(doc: Document, r: AuditReport) -> None:
    s = r.site
    doc.add_heading(f"Desktop Audit - {s.opportunity_id}", level=0)
    rows = [
        ("Opportunity ID", s.opportunity_id),
        ("Relo Site FA Number", s.relo_site_fa_number),
        ("Date", r.generated_at or date.today().isoformat()),
        ("Ring centre", f"{s.latitude}, {s.longitude}"),
        ("Search ring radius", f"{s.search_ring_radius_ft:.0f} ft"),
        ("Requested RAD", f"{s.requested_rad_ft:.0f} ft"),
        ("Customer market", s.customer_market),
        ("State", s.state),
    ]
    if r.tower:
        rows += [
            ("Overall tower height", f"{r.tower.overall_height_ft:.0f} ft"
                                     f"{_cite(r.tower.evidence_ids[:1])}"),
            ("Structure type", r.tower.tower_type.value.replace("_", "-")),
        ]
        if r.tower.guy_anchor_radius_ft:
            rows.append(("Guy anchor radius", f"{r.tower.guy_anchor_radius_ft:.0f} ft"))
    _kv_table(doc, rows)

    if s.input_provenance == "supplied_directly":
        _note(doc, "Ring inputs for this site were supplied directly at run time "
                   "rather than read from a release sheet.")
    if s.input_provenance == "derived_from_report":
        _note(doc, "Ring inputs for this site were read from the existing human "
                   "desktop audit, not from the tranche release sheet, which "
                   "contains no row for this opportunity. Treat the inputs as "
                   "derived rather than independently sourced.")


# Reason codes that mean "the zoning stops it" and "the land stops it", used to
# tick the same boxes the human audits tick.
_ZONING_REASONS = {"zoning_prohibits_use"}
_SPACE_REASONS = {"insufficient_buildable_area", "wetland_excludes_compound",
                  "dwelling_distance", "guy_anchors_do_not_fit"}


def _checklist(r: AuditReport) -> list[tuple[bool, str, int]]:
    """The recommendation checklist the human audits open with.

    Returns (ticked, label, indent). The boxes are derived from the parcel
    dispositions, never set by hand.
    """
    proceed = bool(r.pursue_parcels())
    stopped = [p for p in r.parcels if p.disposition is Disposition.DO_NOT_PURSUE]
    open_ = [p for p in r.parcels if p.disposition is Disposition.UNRESOLVED]
    zoning = bool(stopped) and all(_ZONING_REASONS & set(p.reason_codes) for p in stopped)
    any_zoning = any(_ZONING_REASONS & set(p.reason_codes) for p in stopped)
    any_space = any(_SPACE_REASONS & set(p.reason_codes) for p in stopped)
    no = not proceed
    other = no and (bool(open_) or not r.parcels)
    return [
        (proceed, "Proceed to SCIP", 0),
        (no, "Do not proceed to SCIP because:", 0),
        (no and zoning and not open_, "Preclusive zoning", 1),
        (no and any_zoning, "No parcels that meet zoning requirements", 1),
        (no and any_space, "No parcels with space available for a tower", 1),
        (False, "Terrain in the search area unsuitable for towers "
                "(not assessed at desktop stage)", 1),
        (other, "Other" + (
            "" if not other
            else ": unresolved items listed below prevent a recommendation" if open_
            else ": no parcels were returned for this ring"), 1),
    ]


def _recommendation(doc: Document, r: AuditReport) -> None:
    _h(doc, "Recommendation")
    rec = r.recommendation()
    p = doc.add_paragraph()
    run = p.add_run(rec)
    run.bold = True
    run.font.size = Pt(13)
    run.font.color.rgb = (GREEN if rec.startswith("Proceed")
                          else AMBER if rec.startswith("Unresolved") else RED)

    for ticked, label, indent in _checklist(r):
        line = doc.add_paragraph()
        line.paragraph_format.left_indent = Inches(0.3 * indent)
        line.paragraph_format.space_after = Pt(0)
        mark = line.add_run("[X] " if ticked else "[  ] ")
        mark.bold = True
        line.add_run(label).bold = ticked

    from ..analysis.letters import candidate_target_note
    doc.add_paragraph(candidate_target_note(r))

    counts = {d: 0 for d in Disposition}
    for parcel in r.parcels:
        if parcel.disposition:
            counts[parcel.disposition] += 1
    doc.add_paragraph(
        f"Parcels in ring: {len(r.parcels)}  |  "
        f"Pursue: {counts[Disposition.PURSUE]}  |  "
        f"Do not pursue: {counts[Disposition.DO_NOT_PURSUE]}  |  "
        f"Unresolved: {counts[Disposition.UNRESOLVED]}")

    if r.zoning_level:
        lvl = r.zoning_level.level
        doc.add_paragraph(
            f"Zoning level {lvl} - {ZoningLevel.ASSIGNMENT_LABELS[lvl]}. "
            f"Easiest path among the candidates: {r.zoning_level.rationale}"
            f"{_cite(r.zoning_level.evidence_ids)}. Levels for each jurisdiction, "
            f"on both scales in use, are in the Zoning Requirements section.")


def _collapse(items: list) -> list[tuple]:
    """Group identical findings so one data gap reads as one row.

    A missing municipal zoning layer produces an identical Unresolved on every
    parcel in that city: 215 of them on TI-OPP-43986. Printed one per parcel
    that is noise, and it buries the two or three findings a reviewer must act
    on. Grouping keeps the count visible without the repetition.
    """
    groups: dict[tuple[str, str], list] = {}
    for u in items:
        # Key on the why/how pair, which is what makes two findings the same
        # problem; what_is_missing carries the parcel id and so always differs.
        groups.setdefault((u.why_it_matters, u.how_to_resolve), []).append(u)
    out = []
    for (_why, _how), group in groups.items():
        first = group[0]
        if len(group) == 1:
            out.append((first.what_is_missing, first, 1))
            continue
        # Strip the parcel-specific tail to get the shared statement.
        head = first.what_is_missing.split(", so parcel")[0].split(" for parcel")[0]
        out.append((f"{head} ({len(group)} parcels affected)", first, len(group)))
    out.sort(key=lambda t: -t[2])
    return out


def _unresolved(doc: Document, r: AuditReport) -> None:
    items = list(r.unresolved)
    for p in r.parcels:
        for u in p.unresolved:
            items.append(u)
    if not items:
        return
    _h(doc, "Unresolved")
    doc.add_paragraph(
        "Each item below is a fact the pipeline could not establish. None has "
        "been filled with a default.")
    t = doc.add_table(rows=1, cols=3)
    t.style = "Light Grid Accent 1"
    for i, head in enumerate(("What is missing", "Why it matters", "How to resolve")):
        t.rows[0].cells[i].text = head
        for para in t.rows[0].cells[i].paragraphs:
            for run in para.runs:
                run.bold = True
    for statement, u, _n in _collapse(items):
        c = t.add_row().cells
        c[0].text = statement
        c[1].text = u.why_it_matters
        c[2].text = u.how_to_resolve


def _summary(doc: Document, r: AuditReport) -> None:
    _h(doc, "Summary Area Review")
    bits = []
    if r.jurisdictions:
        bits.append("The search ring falls within " + ", ".join(r.jurisdictions) + ".")
    terrain = r.assumptions.get("terrain")
    if terrain and terrain.get("range_ft") is not None:
        bits.append(
            f"Ground elevation across the ring runs {terrain['min_ft']:.0f} to "
            f"{terrain['max_ft']:.0f} ft, a range of {terrain['range_ft']:.0f} ft.")
    if r.nearby_towers:
        bits.append(_tower_sentence(r.nearby_towers))
    classes = {p.prop_class for p in r.parcels if p.prop_class}
    if classes:
        bits.append("Parcel property classes present: " + ", ".join(sorted(classes)) + ".")
    doc.add_paragraph(" ".join(bits) if bits else "No area description available.")


def _tower_sentence(towers: list[dict]) -> str:
    def n(cat: str, within: float = 99.0) -> int:
        return sum(1 for t in towers if t.get("category") == cat
                   and t.get("distance_miles", 99) <= within)
    built, half = n("constructed"), n("constructed", 0.5)
    out = (f"{built} constructed antenna structure{'s' if built != 1 else ''} "
           f"registered with the FCC lie{'s' if built == 1 else ''} within 2 miles "
           f"of the ring centre, {half} within half a mile.")
    granted, inactive = n("granted"), n("inactive")
    if granted:
        out += (f" {granted} further registration{'s are' if granted != 1 else ' is'} "
                f"granted but not built: an approved application, material to "
                f"collocation rather than to existing coverage.")
    if inactive:
        out += (f" {inactive} cancelled, dismantled or terminated "
                f"registration{'s' if inactive != 1 else ''} in that radius "
                f"{'are' if inactive != 1 else 'is'} listed for completeness and "
                f"not counted as towers.")
    return out


_PERMISSION_KIND = {
    "permitted": "Permitted by right. Building permit with the Sec. 8.004.220 "
                 "submittals; no hearing.",
    "conditional": "A constraint, not a prohibition: allowed with a conditional "
                   "(special) use permit after a public hearing.",
    "prohibited": "A prohibition under the current text: the use is not listed, "
                  "so only a text amendment would allow it.",
    "unknown": "Undetermined: the code does not address this case, so it is "
               "neither permitted nor prohibited on the text.",
}

# What each jurisdiction's tower provisions say on the points the assignment
# lists. Each line names its section; the quotations are in the table below.
_REQUIREMENTS = {
    "Pottawattamie County": [
        "Use and height: each district allows towers by right up to a stated "
        "height and, in most districts, by conditional use above it.",
        "Setback: towers over 50 ft are set back by the manufacturer's designed "
        "fall distance, measured from the tower base to the nearest boundary of "
        "the tract (Sec. 8.004.220.12).",
        "Dwellings: at least the tower's height from adjacent dwellings and "
        "structures not part of the facility (Sec. 8.004.220.13).",
        "Separation: the snapshotted text of Sec. 8.004.220 sets no "
        "tower-to-tower separation distance. It requires a sworn statement on "
        "why collocation was not chosen (Sec. 8.004.220.06).",
        "Exception: county emergency-communications towers are exempt from "
        "Sec. 8.004.220; that does not reach a commercial build.",
    ],
    "Council Bluffs": [
        "Use and height: towers up to 200 ft are allowed in every district by "
        "conditional use permit (CBMC 15.31.020). The chapter is silent above "
        "200 ft.",
        "Setback: 100% of tower height from an adjoining residential dwelling, "
        "20 ft from a non-residential lot (CBMC 15.31.050).",
        "Height is measured to the top of the antenna from natural undisturbed "
        "ground.",
        "Separation: the snapshotted text of Chapter 15.31 sets no numeric "
        "tower-to-tower separation, which leaves more to the board's discretion.",
    ],
    "City of Oakland": [
        "Use: a tower is permitted in M-1. Section 5(A) item 7 lists 'Radio tower "
        "or broadcasting station' as a special use; whether a wireless tower "
        "qualifies is not answered by the text.",
        "Height: Section 6 exempts towers from the height regulations.",
        "District map: the city publishes no zoning GIS, so parcels cannot be "
        "matched to districts without the map from the city clerk.",
    ],
}


def _canon(jurisdiction: str) -> str:
    """'Oakland' and 'City of Oakland' are the same jurisdiction."""
    j = (jurisdiction or "").strip()
    return "City of Oakland" if j.lower() in ("oakland", "city of oakland") else j


def _bold_header(table, heads) -> None:
    for i, head in enumerate(heads):
        table.rows[0].cells[i].text = head
        for para in table.rows[0].cells[i].paragraphs:
            for run in para.runs:
                run.bold = True


def _zoning(doc: Document, r: AuditReport) -> None:
    _h(doc, "Zoning Requirements")

    # -- i. jurisdictions and districts -----------------------------------
    _h(doc, "List of jurisdictions and districts", level=2)
    by_j: dict[str, dict[str, list[str]]] = {}
    for p in r.parcels:
        if p.jurisdiction:
            by_j.setdefault(_canon(p.jurisdiction), {})
        for d in p.districts:
            by_j.setdefault(_canon(d.jurisdiction), {}).setdefault(
                d.code, []).append(p.letter or p.pin)
    if by_j:
        t = doc.add_table(rows=1, cols=3)
        t.style = "Light Grid Accent 1"
        _bold_header(t, ("Jurisdiction", "District", "Parcels (letters match the "
                                                     "parcel table and maps)"))
        for j in sorted(by_j):
            if not by_j[j]:
                c = t.add_row().cells
                c[0].text = j
                c[1].text = "not mapped"
                c[2].text = ", ".join(sorted(
                    (p.letter or p.pin) for p in r.parcels
                    if _canon(p.jurisdiction or "") == j))[:600]
            for code in sorted(by_j[j]):
                c = t.add_row().cells
                c[0].text = j
                c[1].text = code
                c[2].text = ", ".join(sorted(set(by_j[j][code])))[:600]
    else:
        doc.add_paragraph("No jurisdiction or district could be determined.")

    # -- vi. level, per jurisdiction, on both scales ----------------------
    rows = r.jurisdiction_levels or []
    if rows and r.tower:
        _h(doc, "Application to the proposal and zoning level", level=2)
        doc.add_paragraph(
            f"What each district's provisions mean for a "
            f"{r.tower.overall_height_ft:.0f} ft "
            f"{r.tower.tower_type.value.replace('_', '-')} tower. The level is "
            f"given on both scales in use: the assignment's (1 no zoning, 2 admin "
            f"review, 3 admin review with counsel, 4 CUP, SUP, variances) and the "
            f"one the TI-OPP-43986 and 37954 audits use (3 typical CUP, 4 variance, "
            f"rezoning, text amendment or difficult CUP). They differ only on "
            f"where a conditional use permit sits.")
        t = doc.add_table(rows=1, cols=6)
        t.style = "Light Grid Accent 1"
        _bold_header(t, ("Jurisdiction", "District", "Outcome at this height",
                         "Constraint or prohibition", "Level (assignment)",
                         "Level (audit scale)"))
        for row in rows:
            c = t.add_row().cells
            c[0].text = _canon(row["jurisdiction"])
            c[1].text = row["district"] or "not mapped"
            c[2].text = row["why"][:1].upper() + row["why"][1:]
            c[3].text = _PERMISSION_KIND.get(row["permission"], row["permission"])
            la, lh = row.get("level_assignment"), row.get("level_audit")
            c[4].text = (f"{la} - {ZoningLevel.ASSIGNMENT_LABELS[la]}" if la
                         else "undetermined")
            c[5].text = (f"{lh} - {ZoningLevel.DESCRIPTIONS[lh]}" if lh
                         else "undetermined")

    # -- ii. relevant requirements ----------------------------------------
    present = sorted(by_j)
    if present:
        _h(doc, "Relevant requirements", level=2)
        for j in present:
            lines = _REQUIREMENTS.get(j)
            if not lines:
                doc.add_paragraph(
                    f"{j}: no rule set is held for this jurisdiction. See the "
                    f"Unresolved section.")
                continue
            doc.add_paragraph(j).runs[0].bold = True
            for line in lines:
                doc.add_paragraph(line, style="List Bullet")
        if (r.assumptions.get("iowa_chapter_8c") or {}).get("applies"):
            doc.add_paragraph("State of Iowa").runs[0].bold = True
            doc.add_paragraph(
                "Iowa Code Chapter 8C (the Iowa Cell Siting Act) sits above every "
                "local ordinance: it limits what an authority may require of an "
                "applicant, caps application fees and sets the review clock for a "
                "new tower. Cited by section in the provenance appendix; the "
                "statute is published without an extractable text layer, so those "
                "entries are references rather than verified quotations.",
                style="List Bullet")

    # -- iv. traceable evidence -------------------------------------------
    if not r.rules:
        doc.add_paragraph("No verified ordinance rules were loaded for this site.")
        _contacts(doc, r)
        return

    districts_by_j = {j: set(v) for j, v in by_j.items()}

    def relevant(rule) -> bool:
        j = _canon(rule.jurisdiction)
        if j not in districts_by_j:
            return False
        # A jurisdiction with no mapped districts keeps all its rules, since
        # any of them might apply once the district is known.
        if rule.district is None or not districts_by_j[j]:
            return True
        return rule.district in districts_by_j[j]

    shown = [x for x in r.rules if relevant(x)]
    _h(doc, "Ordinance provisions applied", level=2)
    t = doc.add_table(rows=1, cols=5)
    t.style = "Light Grid Accent 1"
    _bold_header(t, ("Jurisdiction", "Section", "Requirement", "Quoted text",
                     "Source and retrieval date"))
    seen = set()
    for rule in shown:
        key = (rule.jurisdiction, rule.section, rule.kind, rule.district)
        if key in seen:
            continue
        seen.add(key)
        c = t.add_row().cells
        c[0].text = _canon(rule.jurisdiction)
        c[1].text = f"§{rule.section}"
        c[2].text = (f"{rule.kind.replace('_', ' ')}"
                     + (f" ({rule.district})" if rule.district else "")
                     + (f": {rule.value} {rule.units or ''}".rstrip()
                        if rule.value not in (True, None) else ""))
        c[3].text = rule.quote
        c[4].text = (rule.url or "supplied document") + \
                    (f"\nretrieved {rule.retrieved_at}" if rule.retrieved_at else "")

    omitted = len(r.rules) - len(shown)
    _note(doc, "Every quotation above was verified as a literal substring of the "
               "stored ordinance snapshot, within the extent of the section it is "
               "cited to, before this report was generated. A quotation that did "
               "not match was dropped rather than printed."
               + (f" {omitted} further verified rules cover districts or "
                  f"jurisdictions that do not intersect this ring and are omitted."
                  if omitted > 0 else ""))
    _contacts(doc, r)


def _contacts(doc: Document, r: AuditReport) -> None:
    if not r.contacts:
        return
    _h(doc, "Zoning and building permit contacts", level=2)
    for c in r.contacts:
        doc.add_paragraph(c.get("office") or c["jurisdiction"]).runs[0].bold = True
        for label, key in (("Address", "address"), ("Phone", "phone"),
                           ("Building inspections", "building_inspections"),
                           ("Fax", "fax"), ("Hours", "hours"),
                           ("Hearing body", "hearing_body")):
            if c.get(key):
                doc.add_paragraph(f"{label}: {c[key]}", style="List Bullet")
        src = c.get("source_url") or "no source recorded"
        if c.get("verified") and c.get("retrieved"):
            _note(doc, f"Source: {src}, retrieved {c['retrieved']}.")
        else:
            _note(doc, f"Not read from an official page ({src}); confirm before "
                       f"relying on it.")


def _overlays(doc: Document, r: AuditReport) -> None:
    o = r.overlays or {}
    if not o:
        return
    _h(doc, "Overlays, Utilities and Case History")
    hits = [x for x in o.get("overlays", []) if x.get("polygons")]
    clear = [x["label"] for x in o.get("overlays", []) if not x.get("polygons")]
    if hits:
        t = doc.add_table(rows=1, cols=3)
        t.style = "Light Grid Accent 1"
        _bold_header(t, ("Overlay", "Parcels touched", "What it means"))
        for x in hits:
            c = t.add_row().cells
            c[0].text = x["label"]
            c[1].text = ", ".join(x["parcels"])[:500] or "in the ring, on no lettered parcel"
            c[2].text = x.get("meaning", "")
    if clear:
        doc.add_paragraph("Checked and not present in the ring: "
                          + ", ".join(clear) + ".")
    if o.get("electric"):
        doc.add_paragraph("Electric service provider(s) for the ring, from the "
                          "county's service-area layer: "
                          + ", ".join(o["electric"]) + ".")
    cases = o.get("zoning_cases") or []
    if cases:
        kinds = sorted({str(c.get("document_type")) for c in cases
                        if c.get("document_type")})
        doc.add_paragraph(
            f"{len(cases)} record(s) in the county Zoning Document Index touch the "
            f"ring ({', '.join(kinds) or 'type not recorded'}). The index records "
            f"that a case exists, not what it decided, so a candidate parcel named "
            f"in one is worth checking with the planning department.")
    if o.get("unavailable"):
        _note(doc, "Could not be read this run: " + "; ".join(o["unavailable"])[:400])
    _note(doc, "Overlays are advisory. None is used to exclude a parcel; each is "
               "recorded as a risk on the parcels it touches.")


def _permitting(doc: Document, r: AuditReport) -> None:
    _h(doc, "Permitting Requirements")
    conditions: list[str] = []
    for p in r.parcels:
        for c in p.conditions:
            if c not in conditions:
                conditions.append(c)
    if conditions:
        for c in conditions[:60]:
            doc.add_paragraph(c, style="List Bullet")
        if len(conditions) > 60:
            _note(doc, f"{len(conditions) - 60} further parcel-specific conditions "
                       f"are listed with their parcels.")
    else:
        doc.add_paragraph("No additional permitting conditions were identified.")


# Above this many parcels, detail blocks are printed for candidates and open
# questions only. An urban ring can return several hundred lots, and a block
# for each buries the handful a reviewer acts on; every parcel still has its
# row, disposition and reason in the table.
DETAIL_ALL_UP_TO = 60


def _parcels(doc: Document, r: AuditReport) -> None:
    _h(doc, "Parcel Data and Dispositions")
    doc.add_paragraph(
        "Every parcel intersecting the search ring is listed. Acres is the whole "
        "ownership; In ring is the part inside the search ring, which is the part "
        "assessed; Buildable is what remains of it after setbacks, wetlands and "
        "dwelling distances.")
    t = doc.add_table(rows=1, cols=9)
    t.style = "Light Grid Accent 1"
    _bold_header(t, ("", "Owner", "PIN", "Acres", "In ring", "District",
                     "Buildable", "Disposition", "Reason"))

    ordered = sorted(r.parcels, key=lambda q: (len(q.letter or "ZZ"), q.letter or "ZZ"))
    for p in ordered:
        c = t.add_row().cells
        c[0].text = p.letter or ""
        c[1].text = p.owner or ""
        c[2].text = p.pin
        c[3].text = f"{p.net_acres:.1f}" if p.net_acres else ""
        c[4].text = (f"{p.area_in_ring_sqft / SQFT_PER_ACRE:.2f} ac"
                     if p.area_in_ring_sqft else "")
        c[5].text = ", ".join(sorted({d.code for d in p.districts})) or "unknown"
        c[6].text = (f"{p.buildable_sqft / SQFT_PER_ACRE:.2f} ac"
                     if p.buildable_sqft is not None else "-")
        c[7].text = p.disposition.value.replace("_", " ") if p.disposition else ""
        c[8].text = ", ".join(rc.replace("_", " ") for rc in p.reason_codes)
        if p.disposition:
            for para in c[7].paragraphs:
                for run in para.runs:
                    run.bold = True
                    run.font.color.rgb = DISPOSITION_COLOR[p.disposition]

    brief = len(r.parcels) > DETAIL_ALL_UP_TO
    if brief:
        _note(doc, f"This ring holds {len(r.parcels)} parcels. Detail follows for "
                   f"the candidates only; every other parcel's disposition and "
                   f"reason is in the table above, and questions shared by many "
                   f"parcels are grouped in the Unresolved section.")
    for p in ordered:
        if brief and p.disposition is not Disposition.PURSUE:
            continue
        if not (p.findings or p.risks or p.conditions or p.unresolved):
            continue
        _h(doc, f"Parcel {p.letter} - {p.owner or p.pin}", level=3)
        head = doc.add_paragraph()
        d = head.add_run((p.disposition.value.replace("_", " ").upper()
                          if p.disposition else "NO DISPOSITION"))
        d.bold = True
        if p.disposition:
            d.font.color.rgb = DISPOSITION_COLOR[p.disposition]
        if p.site_address:
            doc.add_paragraph(f"Site address: {p.site_address}")
        if p.owner_address:
            doc.add_paragraph(f"Owner address: {p.owner_address}")
        for f in p.findings:
            mark = "PASS" if f.passed else ("FAIL" if f.passed is False else "NOTE")
            # The statement often names its sections already; add the deciding
            # one only when it is not there, rather than printing it twice.
            sec = (f" (§{f.rule_section})"
                   if f.rule_section and f.rule_section not in f.statement else "")
            doc.add_paragraph(f"[{mark}] {f.statement}{sec}{_cite(f.evidence_ids)}",
                              style="List Bullet")
        for u in p.unresolved:
            doc.add_paragraph(
                f"[UNRESOLVED] Missing: {u.what_is_missing}. Why it matters: "
                f"{u.why_it_matters} Next step: {u.how_to_resolve}",
                style="List Bullet")
        for risk in p.risks:
            doc.add_paragraph(f"[RISK] {risk}", style="List Bullet")
        for cond in p.conditions:
            doc.add_paragraph(f"[CONDITION] {cond}", style="List Bullet")


def _towers(doc: Document, r: AuditReport) -> None:
    _h(doc, "Existing Towers (FCC ASR)")
    if not r.nearby_towers:
        doc.add_paragraph(
            "No FCC ASR registration lies within 2 miles of the ring centre, or "
            "the lookup was skipped for this run; the provenance appendix says "
            "which.")
    else:
        doc.add_paragraph(_tower_sentence(r.nearby_towers))
        t = doc.add_table(rows=1, cols=6)
        t.style = "Light Grid Accent 1"
        _bold_header(t, ("Registration", "Distance (mi)", "Overall height AGL (ft)",
                         "Type", "Owner", "Status"))
        for tw in r.nearby_towers[:60]:
            c = t.add_row().cells
            c[0].text = str(tw.get("registration", ""))
            c[1].text = f"{tw.get('distance_miles', 0):.2f}"
            h = tw.get("height_agl_ft")
            c[2].text = f"{h:.0f}" if isinstance(h, (int, float)) else ""
            c[3].text = str(tw.get("structure_type") or "")
            c[4].text = str(tw.get("owner") or "")
            c[5].text = str(tw.get("status_label") or "")
        if len(r.nearby_towers) > 60:
            _note(doc, f"{len(r.nearby_towers) - 60} further registrations are in "
                       f"evidence.json.")
    _note(doc, "Height is the overall height above ground including appurtenances, "
               "which runs 10 to 20 ft above the structure height some audits "
               "quote. FCC ASR registration is required only for structures over "
               "200 ft or near an airport glide slope, so shorter structures may "
               "be present and unregistered. Status is the FCC's: constructed, "
               "granted (approved, not built), cancelled, dismantled or "
               "terminated.")


MAP_TITLES = {
    "street": "Street and Aerial Overview",
    "topo": "Topo Map",
    "zoning": "Zoning Map",
    "parcel": "Parcel Map",
    "asr": "Search Ring and 2-Mile Radius Map with ASR Towers",
    "flood": "Flood Plain Map",
    "wetlands": "Wetlands Map",
}


def _maps(doc: Document, r: AuditReport, keys: tuple[str, ...]) -> None:
    for key in keys:
        path = r.map_paths.get(key)
        if not path:
            continue
        _h(doc, MAP_TITLES.get(key, f"{key.replace('_', ' ').title()} Map"), level=2)
        try:
            doc.add_picture(path, width=Inches(6.4))
            doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        except Exception as e:  # noqa: BLE001
            doc.add_paragraph(f"[map unavailable: {e}]")


def _assumptions(doc: Document, r: AuditReport, assumptions: dict) -> None:
    _h(doc, "Assumptions")
    doc.add_paragraph(
        "These values are configuration, not findings. Changing any of them can "
        "change which parcels qualify.")
    doc.add_paragraph(
        "Compound: a {p:.0f} x {p:.0f} ft lease area is assumed, with {e:.0f} x "
        "{e:.0f} ft reported as an exception requiring approval. The compound must "
        "fit entirely within the parcel's in-ring portion after setbacks and "
        "environmental exclusions, with the tower base inside the search ring."
        .format(p=assumptions.get("compound", {}).get("primary_ft", 100),
                e=assumptions.get("compound", {}).get("exception_ft", 80)))
    doc.add_paragraph(
        "Access: a {w:.0f} ft access easement is assumed to be achievable from the "
        "nearest public right of way. No access route was traced, no driveway "
        "permit requirement was evaluated, and no check was made for access "
        "crossing wetlands, which the field guidance treats as equivalent to "
        "building in them. Access is confirmed during the ring drive."
        .format(w=assumptions.get("compound", {}).get("access_width_ft", 20)))
    ratio = float((assumptions.get("setbacks", {}) or {}).get(
        "fall_distance_ratio", 1.0))
    doc.add_paragraph(
        "Setbacks: towers over 50 ft in the county are set back by the "
        "manufacturer's designed fall distance (Sec. 8.004.220.12), a figure that "
        "does not exist at desktop stage. This run used {pct:.0f}% of overall tower "
        "height as the stand-in{note}. In Council Bluffs the setback applied is "
        "100% of tower height, the code's distance to an adjoining residential "
        "dwelling (CBMC 15.31.050), taken from every boundary because neighbouring "
        "uses are not established at desktop stage. Adjoining parcels in the same "
        "ownership are treated as one tract when measuring setbacks, as the human "
        "audits letter them."
        .format(pct=ratio * 100,
                note=(", the conservative reading; a fall-zone letter usually "
                      "certifies less" if ratio >= 1.0 else
                      ", which is less conservative than full height and must be "
                      "supported by a fall-zone letter")))
    if r.tower is not None:
        doc.add_paragraph(
            "Tower height: {h:.0f} ft overall, the requested RAD rounded up to the "
            "next 5 ft. No adjustment for ground elevation is applied, because the "
            "adjustment is specific to each candidate location and none has been "
            "chosen; ground elevation across the ring is reported in the summary "
            "so the ring drive can account for it."
            .format(h=r.tower.overall_height_ft))
    rows = []
    for k, v in assumptions.items():
        if str(k).startswith("_"):
            continue
        if isinstance(v, dict):
            for k2, v2 in v.items():
                rows.append((f"{k}.{k2}", v2))
        else:
            rows.append((k, v))
    if rows:
        _kv_table(doc, rows)


def _provenance(doc: Document, ledger: Ledger) -> None:
    doc.add_page_break()
    _h(doc, "Appendix: Provenance")
    doc.add_paragraph(
        f"{len(ledger)} evidence records. Every bracketed id in this report refers "
        f"to a row below.")
    t = doc.add_table(rows=1, cols=6)
    t.style = "Light Grid Accent 1"
    for i, head in enumerate(("ID", "Type", "Claim", "Source", "Section", "Retrieved")):
        t.rows[0].cells[i].text = head
        for para in t.rows[0].cells[i].paragraphs:
            for run in para.runs:
                run.bold = True
    for row in ledger.provenance_rows():
        c = t.add_row().cells
        c[0].text = row["id"]
        c[1].text = row["type"]
        c[2].text = row["claim"][:300]
        c[3].text = row["source"][:120]
        c[4].text = row["section"][:60]
        c[5].text = row["retrieved"]
