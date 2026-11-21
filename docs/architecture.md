# Desktop Audit pipeline: architecture and tradeoffs

One tranche row in, one Desktop Audit (.docx) out: every parcel in the ring with a
disposition, zoning by jurisdiction and district, seven maps, and a provenance appendix.
Calibration on the five sites: 5 of 5 recommendations agree with the human audits, and
every parcel the humans list is matched (16/16, 13/13, 8/8, 7/7; the fifth report has no
parcel list). Differences are in `docs/material-differences.md`.

## 1. Data sources and why

All free, public and keyless. The human workflow uses ReportAll (paid); the same parcel
data comes from the county at no cost.

| Need | Source | Why this one |
| --- | --- | --- |
| Parcels, owners | County ArcGIS `PublicData_Parcels` | Authoritative; carries the county's own district attribute |
| Districts | County and Council Bluffs zoning layers | Spatial join, cross-checked against that attribute |
| Jurisdiction | Municipal boundaries + parcel `CityCode` | Two independent signals; disagreement is surfaced |
| Tower rules | County ordinance, CBMC 15.31, Oakland ordinance, Iowa Code 8C / 335.2 / 354.9 | Primary text, snapshotted with URL, date and hash |
| Dwellings | Footprints + address points + assessed dwelling value | A footprint alone cannot tell a house from a barn |
| Wetlands, flood | USFWS NWI (county copy as fallback), FEMA NFHL | Federal sources of record |
| Towers | FCC ASR weekly bulk file | Cached, so it cannot fail mid-run; has registration status |
| Elevation, basemaps | USGS 3DEP, National Map tiles | Public domain |

Work is done in Iowa South State Plane feet, the units the ordinances use. Areas are
computed from geometry; server-reported areas disagreed by a factor of six between two
county services.

## 2. Code, models, and people

**No model is called when a report is generated.** An AI coding agent was used during
development to read ordinances and write code; what it produced is frozen into rule
tables that a person can review and that the code re-verifies on every run.

| Step | Done by | What was verified | What can fail | How failure shows |
| --- | --- | --- | --- | --- |
| Ring, tower, buildable area, compound and guy-anchor fit, dispositions | Code | 80 offline tests; five calibration sites | An assumption is wrong (fall distance) | Assumptions printed in every report; a sensitivity control re-runs the geometry |
| Fetching ten live sources | Code | Per-source live check; geometry-type and feature-count guards | Service down, layer renumbered, spatial filter ignored | An Unresolved row naming the layer; the run continues; a zero is recorded as a finding, never left blank |
| Ordinance text to rules | Agent at build time, person reviews | Each quotation must appear inside the section it cites, checked every run | Misreading (it happened, see section 4); ordinance amended | Rule dropped with a warning; calibration diff |
| Internal consistency | Code | Every citation resolves; every parcel has a disposition; parcels cover the ring | Parcel layer incomplete | "Consistency check" row at the top of the report |
| Judgement calls | Person | n/a | n/a | Each is an Unresolved item: what is missing, why it matters, the next step |

People are needed to confirm a "no zoning" finding, obtain a district map that exists
only on paper, settle a real ambiguity in ordinance wording, supply a fall-zone letter,
and answer the legal questions flagged for counsel.

## 3. Where scrapers hit walls

- **The county's main map service was stopped.** A crawl of its 27 service folders found
  the same layers elsewhere. Endpoints are constants, so rerouting is one line.
- **Council Bluffs publishes its code as a JavaScript app.** A plain fetch returns an
  empty shell; the chapter was read from the rendered page. A third-party mirror had the
  right text under invented section headings, which would have produced false citations.
- **Iowa publishes its Code as PDF with no text layer.** Those provisions are cited by
  section, tagged as references rather than quotations, and every report says they are
  weaker evidence.
- **Oakland publishes no zoning GIS.** The human audit got the map by e-mail. The report
  raises one grouped finding and marks that jurisdiction's level undetermined.
- **USGS point elevation takes 8 to 20 s per point.** Seventeen points go in one batch
  request, cross-checked against a single point query because the two services return
  different units; on disagreement the batch is discarded.
- **FCC bulk file columns are not what the documentation suggests.** Positions were
  derived from identities in the data (ground elevation + height = height above sea
  level) and status codes from which rows carry construction and dismantlement dates.
- **FEMA resets connections; map tiles can be unreachable.** Transient errors are
  retried, permanent ones are not. Flood is only ever a risk flag, and a missing basemap
  costs two orientation maps, which the report lists.

## 4. Zoning and ordinance interpretation

Every rule carries a verbatim quotation and is dropped if the quotation is not found
within its cited section. That check was tightened after three invented section numbers
passed a looser one, because identical wording recurs across district chapters.

Reading the whole ordinance, not just passages the audits quote, changed results. The
county uses a two-tier structure: a tower is a principal use up to a height and a
conditional use above it. An early rule set read the residential cap as a prohibition and
wrongly excluded parcels the human had kept; the human was right. Districts that cap the
by-right use and say nothing above it are reported as undetermined, since silence is
neither permission nor refusal, while districts that list towers nowhere are a
prohibition under a permissive-list code. The report states, per district, whether a
provision is a constraint or a prohibition, and gives the zoning level per jurisdiction
on both scales in use, because the assignment puts a conditional use permit at level 4
and the human audits put a typical one at level 3.

Where the text is ambiguous the code does not pick. Oakland lists "Radio tower or
broadcasting station" as a special use; whether a wireless tower qualifies decides
between a special use and a text amendment, so both readings are stated with the
question to put to the city.

## 5. Left unfinished, and next

- **Tower height is the RAD rounded up to 5 ft.** Ground elevation is sampled and
  reported but not yet applied per candidate, which is what the training describes.
  First thing to build.
- **Access is assumed, not traced.** Next: distance to the nearest road, and a landlocked
  flag.
- **Fall distance is assumed at 100% of height**, the conservative reading; a fall-zone
  letter usually certifies less.
- **Three jurisdictions only.** A new one is a snapshot plus a rule module: human work,
  not a prompt. That is the cost of making every run reproducible and reviewable.
- **No FAA 7460 screen, title, soils or RF.** Utility contacts beyond the electric
  provider remain a phone call.
- **Next for speed:** cache fetched layers by ring, so a re-run is instant and a source
  outage during a review does not matter.
