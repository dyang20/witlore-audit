# witlore-audit: Desktop Audit pipeline

Generates a cell-tower Desktop Audit (.docx) from one row of a tranche release
sheet: ring geometry, every parcel in the ring, zoning by jurisdiction and
district, constraints, a disposition per parcel, the maps, and a provenance
appendix in which every factual claim traces to an input field, a dataset
record or a quoted ordinance passage.

**Deliverables in this repo**

| Assignment item | Where |
| --- | --- |
| CLI and codebase | `src/da/`, entry point `da` |
| Calibration reports (5 sites) | `calibration/` |
| Comparison table | `docs/comparison.md` |
| Material differences, and which result the evidence supports | `docs/material-differences.md` |
| Architecture and tradeoff note (2 pages) | `docs/architecture.md`, `docs/architecture.pdf` |
| What is checked, against which authority | `docs/compliance-inventory.md` |

## Install

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[web,dev]"
```

Python 3.9+. Dependencies: shapely, pyproj, requests, python-docx, matplotlib,
PyYAML, openpyxl; Flask for the optional UI; pytest for the tests.

**Credentials and services: none.** Every source is free and public and needs
no key or account. Network access is needed at run time to the county and city
ArcGIS servers, FEMA, USFWS, USGS and (once a week) the FCC bulk file. No model
or paid API is called when a report is generated.

## Quick start

```bash
# one site from the release sheet (tranche.csv by default; .xlsx also accepted)
da run --id TI-OPP-35820
da run --id TI-OPP-35820 --sheet "Tinlam Tranche Release.xlsx"

# a site that is in no sheet: the live-review path
da run --id DEMO-CITY --lat 41.2581 --lon -95.8608 --radius-ft 1500 --rad-ft 180

# the five calibration sites in one command
da batch --ids TI-OPP-35820,TI-OPP-34659,TI-OPP-35482,TI-OPP-37954,TI-OPP-43986
```

Output lands in `outputs/<opportunity_id>/`: the `.docx`, `evidence.json`, and a
`maps/` folder. `python -m da ...` works identically if `da` is not on PATH.

TI-OPP-43986 has no row in the release sheet. Its inputs were reconstructed
from its human audit header into `data/derived_sites.csv`; the CLI finds it
there on its own and tags the report `derived_from_report`.

| Flag | Effect |
| --- | --- |
| `--sheet PATH` | release sheet, `.csv` or `.xlsx`; headers are matched tolerantly |
| `--skip-asr` | skip the FCC tower lookup (first run downloads ~40 MB, then cached) |
| `--skip-elevation` | skip the USGS ground-elevation samples |
| `--no-basemap` | skip the USGS topo and street/aerial overview maps |
| `--out DIR` | output directory, default `outputs/` |

The ordinance snapshots in `data/ordinances/` are already stored. To re-snapshot
one: `da snapshot-ordinance --file data/ordinance.txt --jurisdiction "Pottawattamie County"`.

## Maps in every report

| Map | Source | Fails how |
| --- | --- | --- |
| Street and aerial overview, topo | USGS National Map tiles | skipped, and listed under Unresolved |
| Parcel map, letters matching the parcel table | fetched parcel geometry | cannot, drawn locally |
| Zoning map: districts by jurisdiction, city limits | county and city zoning layers | drawn empty with a note |
| Search ring and 2-mile radius with ASR towers by status | FCC bulk file | drawn empty with a note |
| Flood plain | FEMA NFHL | drawn empty, Unresolved raised |
| Wetlands | USFWS NWI | drawn empty, Unresolved raised |

## Web UI

Optional. The CLI above is the deliverable; this is a demo surface over the
same `pipeline.run()`, so there is no second implementation.

```bash
pip install -e ".[web]"
da serve --sheet "tranche.csv"
# http://127.0.0.1:5000
```

It runs a tranche row or an ad-hoc site, streams each stage as it completes,
and serves the same `.docx` the CLI writes. Three things it shows that the
document does not:

- **Binding constraint per parcel**, the single gate that decided it, so a
  rejection reads as a cause rather than a flat no.
- **Comparison against the human audit** for the calibration sites, if the
  `.docx` files are in `human_reports/`. Reuses `tools/calibrate.py`.
- **Fall-distance sensitivity.** A slider on `setbacks.fall_distance_ratio`
  that recomputes gates 3 to 6 against layers already fetched, so it costs no
  network. Sec. 8.004.220.12 sets the setback at a figure that does not exist
  at desktop stage, and this is the assumption that moves results most.

Localhost only, and not hostable: the pipeline needs the venv, network access
to county ArcGIS, the snapshots in `data/` and the ASR cache.

## Verify before a demo

```bash
python -m pytest -q                 # 80 offline tests, no network
python tests/smoke_offline.py       # full pipeline on stubbed sources, writes a .docx
python spikes/live_check.py --with-asr   # every live source, one line each
python spikes/basemap_check.py      # the USGS tile service the overview maps use
python spikes/elev_check.py         # why elevation is slow, if it is
```

`smoke_offline.py` passing plus `live_check.py` clean means a live run should work.

## How it decides

A parcel is evaluated in this order. Use permission comes before geometry,
because a prohibited use makes compound arithmetic beside the point.

1. **District** — spatial join against zoning polygons, cross-checked against
   the county's cached `PRIMARYRURALZONING` / `PRIMARYCBZONING` parcel
   attribute. Agreement raises confidence; a cached value matching none of the
   mapped districts is a conflict and the parcel becomes Unresolved.
2. **Use permission** — per-district tower clause and height cap. A parcel
   spanning districts is evaluated on its least restrictive one, which is what
   the 34659 audit did.
3. **Geometry** — buildable area and compound fit:

   ```
   B = (P ∩ R) ⊖ s − W − D
   ```

   P parcel, R search ring, s setback, W wetlands, D dwelling keep-outs. Flood
   is *not* subtracted: it is reported as a risk.
4. **Guy anchors** — for guyed towers, the anchor circle must fit on the parcel.
5. **Flood** — recorded as a risk on the parcel, never a disqualifier.
6. **Disposition** — Pursue / Do not pursue / Unresolved with a reason code.

Tower height and type:

```
H = 5 · ⌈(RAD + Δz) / 5⌉
monopole ≤ 150 ft, self-support 151–274 ft, guyed ≥ 275 ft
guy anchor radius ≈ 0.8 H
```

Rounding can change structure type (RAD 274 → 275 ft → guyed), which changes
which ordinance rules apply. The derivation is printed in every report.

## Evidence

Every factual claim points at a record in the ledger, rendered as `[E12]` in the
report and listed in the provenance appendix. Five source types: `input_field`,
`dataset_record`, `document_quote`, `computed`, `human_supplied`.

Ordinance quotes are verified as literal substrings of a stored snapshot before
use. A quote that does not match is **dropped with a warning**, not printed — a
fabricated section number fails automatically rather than reading as
authoritative. Typographic punctuation is folded first, because ordinance PDFs
use curly apostrophes.

Unresolved items always answer three questions: what is missing, why it
matters, how to resolve it. Nothing is filled with a silent default.

## Data sources

All free and public. Confirmed reachable by `spikes/probe.py`.

| What | Source |
| --- | --- |
| Parcels | `PublicDataEnterprise/PublicData_Parcels` |
| Zoning (county) | `Hosted/Zoning_Pottawattamie` |
| Zoning (city) | Council Bluffs `cbZoningDistricts_Public` |
| City tower rules | CBMC Ch. 15.31, official code platform |
| Oakland tower rules | City zoning ordinance PDF (partial extract) |
| State overlay | Iowa Code Ch. 8C, cited by section |
| Jurisdiction | `PublicData_Municipal_Boundaries` + parcel `CityCode` |
| Dwellings | `PublicData_Building_Footprints_2024` + `PublicData_Address_Points` + parcel `DWELLVALUE` |
| Wetlands | USFWS NWI, county USFS copy as fallback |
| Flood | FEMA NFHL |
| Towers | FCC ASR weekly bulk file |
| Elevation | USGS 3DEP: one batch request, point query as fallback |
| Overlays | Loess Hills, Airport Hazard, Two Mile Area, Slope Protection |
| Utilities, case history | `Hosted/Electric_Service_Areas`, `Zoning_Document_Index_View` |
| Basemaps | USGS National Map tiles (topo, imagery with roads) |
| Contacts | jurisdiction websites, recorded with URL and date in `config/jurisdictions/contacts.yml` |

Working CRS is EPSG:102676 / 3418 (Iowa South State Plane, feet), the native CRS
of the county services. Measurement happens in the units the ordinance uses.

### Two traps this code works around

**Non-spatial tables ignore spatial filters.** `Address Table` has
`geometryType: null`; querying it with an envelope returns all 35,087 county
rows with no error. `sources/base.py` checks geometry type before any spatial
query and sanity-checks result counts against ring area.

**Server areas are unreliable.** The same building footprint reports 1,019.8 in
one county service and 167.9 in another — a ratio consistent with one serving
Web Mercator, where distortion at latitude 41.3 is 1/cos(41.3) ≈ 1.33. Areas are
computed locally from geometry; `SHAPE__Area` is never read.

## Configuration

`config/client_rules.yml` holds every value that is a choice rather than a
finding. All are printed in the report's Assumptions section.

The one most worth attention is `setbacks.fall_distance_ratio`. Pottawattamie
§8.004.220.12 sets the setback for towers over 50 ft at "the manufacturer's
designed fall distance" — a figure that only exists once a structure is
engineered, so it is unknowable at desktop-audit stage. The default of 1.0 (full
tower height) is the conservative reading. A real fall-zone letter usually
certifies much less, because modern towers buckle rather than topple. At 450 ft
this single ratio is the difference between a handful of candidate parcels and
most of the ring.

## Known gaps

- **TI-OPP-43986** has no row in the tranche sheet. Its inputs are read from the
  human audit header into `data/derived_sites.csv` and tagged
  `derived_from_report`, so they are visibly not independent of the report being
  compared against. The calibration harness picks that file up automatically.
- **Ordinance currency.** The source URL is the one cited in the human audits
  (`pottcounty-ia.gov/files/county_code/zoning_ordinance_39864.pdf`, whose
  filename matches the supplied text), so citations are retrievable. But the
  newest internal amendment is #2015-05 (2015-12-18); confirm nothing has been
  amended since.
- **Council Bluffs is retrieved, with a caveat on how.** CBMC Chapter 15.31 was
  read from the city's official code platform, which is a client-side app, so
  the chapter was captured from the rendered page rather than fetched as a
  document. Section numbering, headings and enacting ordinances are as the
  official code presents them. The 2024 human audits cite a different host
  (`councilbluffs-ia.elaws.us`), so the city appears to have moved platforms.
- **Utility contacts** (police, fire, telco) are a human action item. The
  electric provider comes from `Hosted/Electric_Service_Areas` and planning
  contacts from `config/jurisdictions/contacts.yml`; the rest came from phone
  calls in the human workflow and a flaky scraper would be worse than an honest
  gap. The Council Bluffs phone number in that file is not yet confirmed
  against the city's own page and the report prints it with that caveat.
- **No ground-elevation adjustment to tower height.** Elevation is sampled
  across the ring and reported, but the height used is the RAD rounded up to
  5 ft. The adjustment the DA training describes is between the original site
  and each candidate location, and is the first thing to build next.
- **Access is assumed, not traced.** A 20 ft easement to the nearest public
  right of way is assumed and stated in every report.
- **ASR undercounts, and counts the wrong thing if read naively.**
  Registration is required only above 200 ft or near a glide slope. And the
  FCC file holds granted, cancelled, dismantled and terminated registrations
  beside constructed ones, so the report counts constructed structures as
  towers and lists the rest by status.
- **Slope Protection overlay** is fetched but its county-wide "0-30% Impact"
  polygon covers ~949 of the county's 959 sq mi, so a bare intersection means
  nothing. Only non-blanket features are treated as a real overlay.

## How the county ordinance actually works

Every district chapter that addresses towers uses the same two-tier structure:
a **principal (by-right) use** up to a height, and a **conditional use** above
it. Reading the by-right cap as a ceiling is the easy mistake, and it is wrong.

| District | By right | Conditional above | Section(s) |
| --- | --: | --- | --- |
| A-2, A-3 | 500 ft | yes | 8.010.020.06 / .030.20, 8.012.020.03 / .030.07 |
| A-4 Loess Hills | none | at any height | 8.014.030.15 |
| R-1, R-2, R-3 | 100 ft | **yes** | 8.015/8.020/8.025 .020 and .030 |
| C-1, C-2 | 150 ft | none | 8.045.020.09, 8.050.020 |
| C-3 | 150 ft | yes | 8.051.020.07 / .030.06 |
| I-1, I-2 | 200 ft | none | 8.055.020, 8.060.020 |
| A-1, R-5, R-6 | no tower clause at all | | |

Three consequences the human audits do not draw out:

- A tower above 100 ft in a **residential** district is a conditional use, not a
  prohibition. An earlier version of this rule set had that backwards and
  wrongly excluded 18 parcels on TI-OPP-43986 that the human audit had
  correctly carried forward. Calibration caught it.
- **C-1, C-2, I-1 and I-2** cap the by-right use and provide no clause above it.
  That is Unresolved, not refused: silence is neither permission nor refusal.
- **A-1, R-5 and R-6** list towers nowhere. In a permissive-list ordinance that
  means a text amendment, which is the only genuine prohibition in the county.

C-2, I-1 and I-2 number their permitted uses with letters ("I. Towers with a
height...") rather than digits, so a scan looking only for `.09`-style items
reports them as having no tower clause. They do.

### Citations are checked against their section, not just the document

`verify_quote` takes the cited section and requires the quotation to appear
within that section's extent, bounded by the next section heading. Substring
matching alone is not enough: identical wording appears in several district
chapters, so a quote attributed to an invented section number passed happily. A
citation pointing at the wrong place is worse than a missing one, because it
looks authoritative.

## The zoning level scale

Two definitions are in play and they disagree about conditional use permits.

The hiring team described the scale as 1 no zoning, 2 admin review, 3 admin
review with counsel, 4 CUP/SUP/variances.

Their own most recent audit (TI-OPP-43986, 2026-05-02) states it per
jurisdiction and puts an ordinary CUP a level lower:

> City of Oakland Zoning Level: **4 - variance, rezoning, text amendment, or
> difficult CUP process**
> Pottawattomie County Zoning Level: **3 - site plan review with meeting or
> typical CUP**

Every report prints **both**, per jurisdiction and per district, in the
"Application to the proposal and zoning level" table, so a reader never has to
guess which scale a bare number is on. They differ only on where a conditional
use permit sits.

The headline label uses the assignment's scale, because that is the stated
output format. `zoning_levels.cup_is_level_4: false` in
`config/client_rules.yml` switches the headline to the audits' scale; the table
shows both either way.

## Council Bluffs

Two calibration sites are in the city (35482, and the southern 60% of 37954),
so the chapter matters as much as the county ordinance. It is simple to apply
and hard to clear:

- Towers up to **200 ft are permitted in every district** subject to a
  conditional use permit (§15.31.020), so no city parcel is ever excluded on use
  grounds, and no site is below zoning level 3.
- Above 200 ft the chapter is **silent**. That is Unresolved, not prohibited:
  the code's silence is neither permission nor refusal.
- The binding constraint is the setback: **100% of tower height from the base to
  any adjoining residential dwelling**, whether or not that land is zoned
  residential (§15.31.050(4)). It measures structure to structure, not to a
  district boundary, which makes it stricter in practice than it reads. Twenty
  feet applies only where every adjoining lot is non-residential.
- There is **no numeric tower separation** requirement. §15.31.050 lists only
  "Proximity to other towers." as a consideration. Both human audits read this
  as making approval harder, since it removes the objective criterion a board
  would otherwise be bound by.
- **Height is measured to the antenna tip from natural undisturbed ground**
  (§15.31.050), not to the RAD centreline and not from finished grade. Neither
  human audit records this. It pushes the regulated height above the requested
  RAD, which matters against the 200 ft ceiling.

## City of Oakland, and a disagreement with the human audit

43986 straddles the City of Oakland and unincorporated Pottawattamie County, so
the city ordinance is implemented in `src/da/zoning/oakland.py`.

Reading it produced a material discrepancy. The human audit concludes that a
tower in the city portion would need a **text amendment**, and rates Oakland
zoning level 4 on that basis: towers appear as a permitted use only in M-1, the
ring's city portion is Agriculture and Single Family Residential, so the use
would have to be added.

The first half is right. The conclusion does not follow. Section 5(A) item 7
lists

> Radio tower or broadcasting station.

as a **special use**, and the special-use provisions are not district specific:
granted by the Mayor and Council after Planning and Zoning Commission review.
A path exists that is not a text amendment.

Whether it is available turns on one question the ordinance does not answer: is
a commercial wireless tower a "Radio tower"? Cellular service is radio service
and no narrower definition is given. Against that, an ordinance of this vintage
pairing "radio tower" with "broadcasting station" may have meant broadcast
facilities.

So the pipeline does not pick. `evaluate()` is tri-state: special use if it
qualifies (level 3), text amendment if it does not (level 4), Unresolved while
unanswered, with the specific question to put to the city. The human audit
assumed the narrower reading without citing Section 5.

Second finding, also absent from the human audit: Section 6 exempts
"transmission towers... radio and television towers... masts, aerials" from the
height regulations. There is no height cap on a tower in Oakland.

## Iowa Code Chapter 8C

Every human audit carries a "State of Iowa" section on the Iowa Cell Siting Act,
and it sits above every local ordinance in the state, so it is applied to every
Iowa site (`src/da/zoning/iowa_state.py`). It never filters a parcel; it
constrains what a jurisdiction may demand, caps fees, sets the review clock, and
may preempt specific local rules.

**This module is deliberately weaker than the others, and says so.** The Iowa
Legislature publishes Chapter 8C only as PDF and RTF: the PDF has no extractable
text layer and the RTF is binary, so no verbatim snapshot could be taken.
Provisions are therefore cited by section against the Legislature's published
Code and tagged `statute_reference`, not `document_quote`. Section numbers and
titles come from the Legislature's own chapter listing and are authoritative.
Every report raises an Unresolved recording the difference.

Where a local rule looks reachable by the statute (a fixed tower separation, a
collocation precondition, a consulting fee, a removal surety, an approval
duration limit), the report flags it as **a question for counsel**, never as a
settled preemption. Whether a specific local rule is preempted is a legal
conclusion, and the pipeline does not make legal conclusions. The 35989 human
audit reaches for exactly this argument against a 750 ft separation rule.

## Calibration

```bash
# the human .docx audits go in human_reports/ (not committed), then:
python tools/calibrate.py --human-dir human_reports --extract-only   # check parsing
python tools/calibrate.py --human-dir human_reports                  # full comparison
python tools/collect_calibration.py                                  # copy the 5 reports into calibration/
```

Writes `docs/comparison.md` and `docs/comparison.json`: recommendation
agreement, parcel-by-parcel match (joined on PIN, falling back to owner name),
acreage error, districts, zoning level per jurisdiction on both scales, FCC
tower counts by status against the human count, ordinance sections the human
cited that we did not apply, and dispositions.

`docs/material-differences.md` is the hand-kept account of where the output
differs from a human audit and which result the evidence supports.

The human reports store parcels as plain paragraphs rather than tables
(`A. OWNER: ...` / `PIN:` / `ACRES:`), which is what the extractor parses. They
are read only to measure against, never to source conclusions from.

## Layout

```
src/da/
  models.py        data contracts; evidence and conclusions kept separate
  evidence.py      the ledger; refuses unsourced claims
  pipeline.py      stage orchestration
  cli.py           da run / batch / serve / snapshot-ordinance
  geo/             crs, ring, tower derivation, buildable area
  sources/         one module per external source; never interprets
  zoning/          snapshots, quote verification, one rule set per jurisdiction
  analysis/        per-parcel evaluation, lettering, consistency checks
  maps/            matplotlib rendering
  report/          .docx assembly
  web/             optional local UI over the same pipeline
config/            client build parameters, jurisdiction contacts
data/              ordinance snapshots, derived site inputs, FCC cache (ignored)
tools/             calibration harness
spikes/            live diagnostics, one per source
tests/             offline suite and the stubbed end-to-end smoke run
calibration/       the five calibration reports, as submitted
docs/              comparison, material differences, architecture note, inventory
```
