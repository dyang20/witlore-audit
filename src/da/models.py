"""Shared data contracts.

Every stage passes these objects. Two rules hold throughout:

1. Evidence and conclusion are separate. A Finding carries evidence ids; it never
   carries prose that smuggles in an unsourced claim.
2. Anything the pipeline could not establish becomes an Unresolved, never a
   silent default. Unresolved says what is missing, why it matters, and how to
   resolve it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Optional


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------


@dataclass
class SiteInput:
    """One row of the Tinlam tranche release sheet."""

    opportunity_id: str
    latitude: float
    longitude: float
    search_ring_radius_ft: float
    requested_rad_ft: float
    state: str
    relo_site_fa_number: Optional[str] = None
    customer_market: Optional[str] = None
    tta_region: Optional[str] = None
    tinlam_region: Optional[str] = None
    intake: Optional[str] = None
    # TI-OPP-43986 has no row in the release sheet; its inputs are read off the
    # human DA header and must be labelled as such everywhere downstream.
    input_provenance: str = "tranche_sheet"  # | "derived_from_report" | "supplied_directly"

    def __post_init__(self) -> None:
        if self.input_provenance not in ("tranche_sheet", "derived_from_report",
                                         "supplied_directly"):
            raise ValueError(f"bad input_provenance: {self.input_provenance}")


# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------


class SourceType(str, Enum):
    INPUT = "input_field"          # a column of the tranche sheet
    DATASET = "dataset_record"     # a feature from a GIS service
    DOCUMENT = "document_quote"    # a verbatim passage from an ordinance
    COMPUTED = "computed"          # derived from other evidence, with inputs named
    HUMAN = "human_supplied"       # an analyst told us; not independently verified


@dataclass
class Evidence:
    """One traceable fact. Nothing reaches the report without one of these."""

    id: str
    value: Any
    source_type: SourceType
    description: str
    url: Optional[str] = None
    section: Optional[str] = None      # ordinance section, layer id, column name
    quote: Optional[str] = None        # verbatim, for DOCUMENT only
    retrieved_at: Optional[str] = None  # ISO date
    snapshot_hash: Optional[str] = None
    derived_from: list[str] = field(default_factory=list)  # evidence ids, for COMPUTED

    def citation(self) -> str:
        bits = [self.description]
        if self.section:
            bits.append(f"§{self.section}" if self.source_type is SourceType.DOCUMENT
                        else self.section)
        if self.retrieved_at:
            bits.append(f"retrieved {self.retrieved_at}")
        return " | ".join(bits)


# --------------------------------------------------------------------------
# Zoning
# --------------------------------------------------------------------------


class UsePermission(str, Enum):
    PERMITTED = "permitted"          # by right, building permit only
    CONDITIONAL = "conditional"      # CUP/SUP, hearing required
    PROHIBITED = "prohibited"        # not allowed; needs a text amendment
    UNKNOWN = "unknown"


class ProcessType(str, Enum):
    NONE = "none"                    # no zoning
    BUILDING_PERMIT = "building_permit"
    ADMIN_REVIEW = "administrative_review"
    CONDITIONAL_USE = "conditional_use_permit"
    SPECIAL_USE = "special_use_permit"
    VARIANCE = "variance"
    TEXT_AMENDMENT = "text_amendment"
    UNKNOWN = "unknown"


@dataclass
class ZoningRule:
    """One extracted requirement. `quote` must be a literal substring of the snapshot."""

    kind: str                 # height_cap | setback | dwelling_distance | separation | ...
    jurisdiction: str
    district: Optional[str]
    value: Any
    units: Optional[str]
    section: str
    quote: str
    url: Optional[str] = None
    retrieved_at: Optional[str] = None
    snapshot_hash: Optional[str] = None
    applies_to_tower_types: Optional[list[str]] = None  # None = all
    notes: Optional[str] = None


@dataclass
class District:
    code: str                            # "A-2"
    name: Optional[str] = None
    jurisdiction: str = ""
    source: str = "spatial_join"         # spatial_join | parcel_attribute | both | conflict
    evidence_ids: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Tower
# --------------------------------------------------------------------------


class TowerType(str, Enum):
    MONOPOLE = "monopole"
    SELF_SUPPORT = "self_support"
    GUYED = "guyed"


@dataclass
class TowerSpec:
    """Derived from RAD. Thresholds come from the Tillman DA training and are
    configurable in client_rules.yml, because they came from a meeting summary
    rather than a written standard."""

    rad_ft: float
    ground_delta_ft: float
    overall_height_ft: float
    tower_type: TowerType
    guy_anchor_radius_ft: Optional[float]
    evidence_ids: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Parcels and dispositions
# --------------------------------------------------------------------------


class Disposition(str, Enum):
    PURSUE = "pursue"
    DO_NOT_PURSUE = "do_not_pursue"
    UNRESOLVED = "unresolved"


@dataclass
class Unresolved:
    """Never a silent default. Every field here is required."""

    what_is_missing: str
    why_it_matters: str
    how_to_resolve: str
    blocking: bool = True


@dataclass
class Finding:
    """A conclusion, bound to the evidence it rests on."""

    statement: str
    evidence_ids: list[str]
    rule_section: Optional[str] = None
    passed: Optional[bool] = None


@dataclass
class Parcel:
    pin: str
    letter: Optional[str] = None
    owner: Optional[str] = None
    site_address: Optional[str] = None
    owner_address: Optional[str] = None
    net_acres: Optional[float] = None
    prop_class: Optional[str] = None
    dwelling_value: Optional[float] = None
    city_code: Optional[str] = None
    legal: Optional[str] = None
    deed_url: Optional[str] = None

    # geometry is a shapely Polygon in EPSG:102676; untyped here to keep models
    # importable without shapely.
    geometry: Any = None
    area_in_ring_sqft: Optional[float] = None
    buildable_sqft: Optional[float] = None
    compound_fit: Optional[str] = None       # "100x100" | "80x80" | None

    jurisdiction: Optional[str] = None
    districts: list[District] = field(default_factory=list)

    disposition: Optional[Disposition] = None
    reason_codes: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    unresolved: list[Unresolved] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)   # e.g. flood zone AE
    conditions: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)

    def has_dwelling(self) -> Optional[bool]:
        """Three independent signals; None when they disagree or are absent."""
        if self.dwelling_value is None:
            return None
        return self.dwelling_value > 0


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


@dataclass
class ZoningLevel:
    """The zoning level scale.

    Two definitions are in play and they disagree about where a CUP sits.

    The hiring team described the scale as:
        1 no zoning | 2 admin review | 3 admin review with counsel
        4 CUP, SUP, variances

    Their own most recent audit (TI-OPP-43986, 2026-05-02) states it per
    jurisdiction, and puts an ordinary CUP a level lower:
        3 - "site plan review with meeting or typical CUP"
        4 - "variance, rezoning, text amendment, or difficult CUP process"

    Both are printed in every report, per jurisdiction and district, so a bare
    number never has to be guessed at. `from_process` computes either: the
    default is the audits' scale, `cup_is_level_4=True` is the assignment's.
    Which one the report's headline uses is `zoning_levels.cup_is_level_4` in
    client_rules.yml, and defaults to the assignment's, since that is the
    stated output format.
    """

    level: int
    rationale: str
    evidence_ids: list[str] = field(default_factory=list)

    DEFINITION_SOURCE = ("TI-OPP-43986 Desktop Audit, 2026-05-02: level 3 is "
                         "'site plan review with meeting or typical CUP', level 4 is "
                         "'variance, rezoning, text amendment, or difficult CUP "
                         "process'.")
    # The scale as the assignment states it. Printed beside the audit scale in
    # every report, because the two disagree about a conditional use permit and
    # a reader should not have to guess which one a bare number means.
    ASSIGNMENT_LABELS = {
        1: "No zoning",
        2: "Admin review",
        3: "Admin review with counsel",
        4: "CUP, SUP, variances",
    }
    DESCRIPTIONS = {
        1: "no zoning, or the use is exempt",
        2: "administrative or building-permit review, no hearing",
        3: "site plan review with meeting, or a typical CUP",
        4: "variance, rezoning, text amendment, or a difficult CUP process",
    }

    def __post_init__(self) -> None:
        if self.level not in (1, 2, 3, 4):
            raise ValueError(f"zoning level must be 1-4, got {self.level}")

    @property
    def description(self) -> str:
        return self.DESCRIPTIONS[self.level]

    @staticmethod
    def from_process(process: ProcessType, needs_counsel: bool = False,
                     cup_is_level_4: bool = False) -> int:
        if process is ProcessType.NONE:
            return 1
        # Relief from a rule, or changing the rule, is always the top level.
        if process in (ProcessType.VARIANCE, ProcessType.TEXT_AMENDMENT):
            return 4
        if process in (ProcessType.CONDITIONAL_USE, ProcessType.SPECIAL_USE):
            # Level 3 under the 43986 definition; 4 under the hiring team's.
            if cup_is_level_4:
                return 4
            return 4 if needs_counsel else 3
        if process in (ProcessType.BUILDING_PERMIT, ProcessType.ADMIN_REVIEW):
            return 3 if needs_counsel else 2
        return 4  # unknown process: assume the slow path, say so in the rationale


@dataclass
class AuditReport:
    site: SiteInput
    tower: Optional[TowerSpec] = None
    parcels: list[Parcel] = field(default_factory=list)
    jurisdictions: list[str] = field(default_factory=list)
    zoning_level: Optional[ZoningLevel] = None
    rules: list[ZoningRule] = field(default_factory=list)
    evidence: dict[str, Evidence] = field(default_factory=dict)
    unresolved: list[Unresolved] = field(default_factory=list)
    assumptions: dict[str, Any] = field(default_factory=dict)
    nearby_towers: list[dict] = field(default_factory=list)
    generated_at: Optional[str] = None
    map_paths: dict[str, str] = field(default_factory=dict)
    # One row per jurisdiction-and-district present in the ring: permission,
    # process, the reason, and the level on both scales.
    jurisdiction_levels: list[dict] = field(default_factory=list)
    # Overlay districts, utilities and zoning case history touching the ring.
    overlays: dict[str, Any] = field(default_factory=dict)
    contacts: list[dict] = field(default_factory=list)

    # The DA training sets a target of three candidate parcels per ring.
    CANDIDATE_TARGET = 3

    def pursue_parcels(self) -> list[Parcel]:
        return [p for p in self.parcels if p.disposition is Disposition.PURSUE]

    def meets_candidate_target(self) -> bool:
        return len(self.pursue_parcels()) >= self.CANDIDATE_TARGET

    def recommendation(self) -> str:
        if self.pursue_parcels():
            return "Proceed to SCIP"
        if any(p.disposition is Disposition.UNRESOLVED for p in self.parcels):
            return "Unresolved - cannot recommend without further information"
        return "Do not proceed"
