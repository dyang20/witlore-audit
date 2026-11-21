# Compliance inventory

What the pipeline checks, which authority each rule comes from, and where the
data comes from. Every rule listed here carries a verbatim quotation verified
against a stored ordinance snapshot, except Iowa Chapter 8C, which is cited by
section for the reason given below.

---

## The gate sequence

`evaluate_parcel()` runs six checks in a fixed order. Order matters: a
prohibited use makes compound arithmetic pointless, and an undetermined
district makes every downstream number unreliable.

| # | Gate | Fails to | Reason code |
| --- | --- | --- | --- |
| 1 | District determined? | Unresolved | `district_undetermined` |
| 2 | District signals agree? | Unresolved | `district_conflict` |
| 3 | Use permitted at this height? | Do not pursue / Unresolved | `zoning_prohibits_use`, `height_not_addressed_by_code`, `use_path_undetermined` |
| 4 | Compound fits buildable area? | Do not pursue | `insufficient_buildable_area`, `wetland_excludes_compound`, `dwelling_distance` |
| 5 | Guy anchors fit on parcel? | Do not pursue | `guy_anchors_do_not_fit` |
| 6 | Flood zone intersects? | Risk flag only | `flood_zone_risk` |

Gate 4 distinguishes three causes by re-running the geometry with each
exclusion removed in turn, so "too small" is never reported when wetlands or a
dwelling buffer is what actually bound. See `_wetland_is_binding()` and
`_dwelling_is_binding()`.

Reason codes are stable strings so the calibration table can group by them.

---

## Authorities and provisions checked

### Pottawattamie County Zoning Ordinance

Unincorporated land. Source: `pottcounty-ia.gov/files/county_code/_86947.pdf`,
snapshotted to `data/ordinance.txt`.

Every district uses a **two-tier** structure: a by-right height cap, then a
conditional-use clause above it. 11 districts carry tower clauses, 3 do not
(`A-1`, `R-5`, `R-6`).

| District | By-right cap | Above cap |
| --- | --- | --- |
| A-2, A-3 | 500 ft | Conditional use |
| A-4 | none | Conditional use only |
| R-1, R-2, R-3 | 100 ft | Conditional use |
| C-1 | 150 ft | no clause |
| C-2 | 150 ft | no clause |
| C-3 | 150 ft | Conditional use |
| I-1, I-2 | 200 ft | no clause |

"No clause" returns **Unresolved, not prohibited**. Silence in a zoning
ordinance is neither permission nor refusal, and treating it as refusal would
discard viable parcels.

General tower standards, Sec. 8.004.220, 13 subsections captured:

| Subsection | Requirement | Role |
| --- | --- | --- |
| .06 | Collocation statement | Condition of approval |
| .09 | Removal agreement | Condition of approval |
| .10 | Airport approval | Condition of approval |
| .11 | Camouflage | Condition of approval |
| .12 | Setback equal to the manufacturer's designed fall distance, towers over 50 ft | **Geometric gate** |
| .13 | Distance from adjacent dwellings equal to tower height | **Geometric gate** |
| .14 | Fencing | Condition of approval |
| .15 | Sheriff interference review | Condition of approval |
| .16 | Records disclosure | Condition of approval |
| .17 | Periodic inspection | Condition of approval |
| .18 | Abandonment | Condition of approval |

Subsections .16, .17 and .18 are truncated in the 2024 human audits.

Sec. 8.004.220.12 overrides the district dimensional setbacks for any tower
over 50 ft. County emergency-communications towers are exempt from Sec.
8.004.220 and that exemption is recorded in the rule set.

### Council Bluffs Municipal Code Ch. 15.31

City parcels. Source: `councilbluffs.municipalcodeonline.com`, read from the
rendered DOM because the code is published as a JavaScript application with no
text in the served HTML.

- Towers to 200 ft are permitted in **every** zoning district, so no Council
  Bluffs parcel is ever excluded on use grounds
- Above 200 ft the chapter has no clause, which returns **Unresolved** with the
  planning department contact in the resolution step
- Setback, 15.31.050(4): 100% of tower height to an adjoining residential
  dwelling, 20 ft to a non-residential lot. The dwelling case is the
  conservative default when neighbouring uses are not established
- Height is measured to the antenna tip from natural undisturbed ground
- No numeric separation requirement, which widens Board of Adjustment
  discretion rather than easing approval
- Conditional use is routed through the Zoning Board of Adjustment under Ch.
  15.02

### City of Oakland zoning ordinance

Source: `cityofoaklandiowa.com`. The city publishes no zoning GIS of any kind.

`evaluate()` is tri-state because the ordinance is genuinely ambiguous:

- `M-1` permits towers by right
- Section 5(A) item 7 lists "Radio tower or broadcasting station" as a special
  use. Whether a wireless telecommunications tower qualifies is a question the
  ordinance does not answer
- Section 6 exempts towers from the height regulations

Where the human audit concludes a text amendment is required, the special-use
path is a different and much faster route. The pipeline refuses to pick and
states both readings with the specific question to put to the city.

### Iowa Code Ch. 8C

State preemption. Advisory only. Source:
`legis.iowa.gov/law/iowaCode/sections?codeChapter=8C`.

The PDF has no extractable text layer, so Chapter 8C is cited by section and
tagged `statute_reference` rather than `document_quote`. Every report raises an
Unresolved item recording that this is weaker evidence than the rest.

| Section | Subject |
| --- | --- |
| 8C.3 | Uniform rules and limitations, applications |
| 8C.4 | Uniform rules, new tower applications |
| 8C.6 | Use of public lands for towers and transmission equipment |

Separation, collocation, fee, surety and duration requirements found in local
ordinances are flagged as **potential preemption questions for counsel**. The
pipeline never resolves them.

---

## Geometry

The buildable region on a parcel:

$$B = (P \cap R) \ominus s - W - D$$

where $P$ is the parcel, $R$ the search ring, $s$ the setback, $W$ wetlands and
$D$ the dwelling keep-out circles. Flood is deliberately not subtracted; it is
a risk flag.

The $P \cap R$ term is what assesses the in-ring portion specifically rather
than the parcel as a whole, and the compound fit requires the tower base to lie
inside the search ring.

### Setback selection

| Jurisdiction | Setback | Authority |
| --- | --- | --- |
| County, height > 50 ft | Full tower height | Sec. 8.004.220.12, conservative stand-in |
| County, height $\le$ 50 ft | max(front, side, rear, road) | District dimensional standards |
| Council Bluffs | 100% of height to adjoining residential dwelling | CBMC 15.31.050(4) |
| Oakland | Full tower height | Section 6 exempts height; AG dimensional standards absent from the extract |

The fall distance in Sec. 8.004.220.12 is a manufacturer figure that does not
exist until a structure is engineered, so it is unknowable at desktop stage.
Full tower height is the conservative reading and is stated as an assumption in
every report. A real fall-zone letter usually certifies much less, which is why
`setbacks.fall_distance_ratio` is the single configured value that moves
results most.

### Compound and tower

- Compound 100 $\times$ 100 ft primary, 80 $\times$ 80 ft as an exception
  flagged for explicit approval rather than assumed
- Overall height $H = 5\lceil (\text{RAD} + \Delta z)/5 \rceil$, with
  $\Delta z = 0$ in the current build. Ground elevation is sampled across the
  ring and reported, but the adjustment between the original site and each
  candidate location is not yet applied, and every report says so
- Monopole $\le$ 150 ft, self-support 151 to 274 ft, guyed $\ge$ 275 ft
- Guy anchor radius $\approx 0.8H$

Guy anchors are tested against the parcel boundary and wetlands with
`setback_ft=0.0`. Sec. 8.004.220.12 measures the setback "from the base of the
tower to the nearest boundary line"; anchors are separate ground hardware for
which the ordinance sets no setback, so they need only sit on land the lease
controls and clear of wetlands. Eroding by the full fall distance applied the
setback twice and rejected parcels the ordinance permits.

### Multi-district parcels

A parcel spanning districts is evaluated on the **most permissive** district
present, ranked by whether the use is prohibited and then by zoning level. The
districts not used are recorded as a condition on the parcel.

---

## Data sources

All free and public. No paid parcel service; the human workflow uses ReportAll,
visible in the hyperlinks of the TI-OPP-43986 audit, and the same data came
from county GIS at no cost.

| Need | Endpoint | Layer |
| --- | --- | --- |
| Parcels | `gis.pottcounty-ia.gov/arcgis/rest/services/PublicDataEnterprise/PublicData_Parcels/FeatureServer` | 0 |
| Zoning, county | `.../Hosted/Zoning_Pottawattamie/FeatureServer` | 0 |
| Zoning, city | `gispublic.councilbluffs-ia.gov/publicserver/rest/services/cbZoningDistricts_Public` | 0 |
| Jurisdiction | `.../PublicDataEnterprise/PublicData_Municipal_Boundaries/FeatureServer` plus parcel `CityCode` | two signals, cross-checked |
| Dwellings | `PublicData_Building_Footprints_2024`, `PublicData_Address_Points`, parcel `DWELLVALUE` | three signals, one rule |
| Wetlands | `fws.gov/wetlandsmapservice/rest/services/Wetlands/MapServer`, county USFS copy as fallback | |
| Flood | `hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer` | 28 |
| Towers | `data.fcc.gov/download/pub/uls/complete/r_tower.zip` | CO / RA / EN |
| Elevation | `epqs.nationalmap.gov/v1/json` | |

Overlays queried on every run and never used as gates: Loess Hills special
landscape areas, slope protection overlay district, airport hazard polygons and
the two-mile city review area. Each is attached as a risk to the parcels it
touches and listed in the report's "Overlays, Utilities and Case History"
section, with the electric service provider and any Zoning Document Index
records touching the ring. A layer that cannot be read is named in a
non-blocking Unresolved rather than passed over.

Basemaps for the topo and street/aerial overview maps are USGS National Map
tiles. They are orientation only; no finding depends on them.

### Two traps worth recording

A non-spatial table silently ignores a spatial filter. `Address Table` has
`geometryType: null` and returned all 35,087 county rows with no error, so
geometry type is checked before every spatial query.

Server-reported areas are unreliable. The same building footprint reports
1,019.8 in one county service and 167.9 in another, consistent with one serving
Web Mercator. Areas are computed locally from geometry and `SHAPE__Area` is
never read.

Working CRS is EPSG:3418, NAD83 Iowa South State Plane in US survey feet, the
native CRS of the county services, so setbacks are compared in the units the
ordinance uses.

---

## Advisory, not gates

Reported in full but never used to reject a parcel:

- FEMA flood zone, recorded as a risk per client instruction
- FCC ASR registrations within 2 miles, counted by status
- Loess Hills, slope protection, airport hazard, two-mile area
- Electric service provider, zoning case history
- All of Sec. 8.004.220 except .12 and .13, which surface as conditions of
  approval

ASR needs two cautions, and every report states both. Registration is required
only for structures over 200 ft or near an airport glide slope, so shorter
structures are legitimately absent. And the file holds every registration ever
made, not only towers that stand: the status field separates constructed (C)
from granted and unbuilt (G), cancelled (A), dismantled (I) and terminated (T).
Those meanings were confirmed from the data: 98% of I rows carry a dismantlement
date, and almost no A rows carry a construction date. Only constructed
structures are counted as towers; the rest are listed with their status.

---

## Not checked

Stated plainly so a reviewer does not assume coverage that is absent.

- **Per-candidate ground elevation.** Tower height is the RAD rounded up to
  5 ft; it is not adjusted for the difference in ground level between the
  original site and a candidate
- **Access routing.** A 20 ft easement is assumed achievable from the nearest
  public right of way. No route is traced, no driveway permit requirement is
  evaluated, and no check is made for access crossing wetlands
- Soil conditions, wind load, structural engineering
- RF propagation and whether the site meets the coverage objective
- Title, easements, existing encumbrances
- Utility availability beyond which company serves the area
- Airspace beyond the county's hazard polygon layer. No FAA 7460 screen
- Tribal consultation and historic or cultural resource review
- Utility and emergency-services contacts (police, fire, telco), which remain a
  human action item

---

## Zoning level

Stated per jurisdiction and per district, on both scales in use, because they
disagree about a conditional use permit.

| Process | Assignment scale | Scale in the 43986 and 37954 audits |
| --- | --- | --- |
| No zoning | 1 | 1 |
| Building permit, admin review | 2 | 2 |
| Admin review needing counsel | 3 | 3 |
| Conditional or special use | 4 | 3 |
| Variance, text amendment, unknown | 4 | 4 |

The headline label uses the assignment's scale. A jurisdiction with no mapped
district (Oakland) is reported as undetermined rather than given a number.

---

## Where the pipeline refuses to decide

Three cases produce Unresolved rather than a guess, each naming what is
missing, why it matters and how to resolve it:

1. **No zoning district mapped.** Oakland publishes no zoning GIS, so 215
   parcels in the TI-OPP-43986 ring have no mapped district. The human workflow
   hit the same wall and obtained the map by email from the Assistant City
   Clerk. The pipeline reports one finding naming the city and the remedy
   rather than repeating itself 215 times
2. **Height above the code's stated ceiling.** Council Bluffs above 200 ft, and
   county districts C-1, C-2, I-1, I-2 above their by-right caps
3. **Genuine ordinance ambiguity.** Oakland's special use versus text amendment
   question

Nothing is filled with a silent default.