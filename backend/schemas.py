"""
SETU Pydantic Schemas — request/response validation.

Defines the API contract for reports, incidents, extractions,
contradictions, and audit logs.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator


# ---------------------------------------------------------------------------
# Extraction triplet & People semantics
# ---------------------------------------------------------------------------

class PeopleCount(BaseModel):
    """
    Structured people count distinguishing categories of human impact.
    Prevents confounding 'affected residents' with 'trapped people'.
    """
    count: int = Field(..., ge=0, description="Count of individuals or estimated number")
    category: str = Field(
        default="affected",
        description="Category: trapped | affected | nearby | at_risk",
    )
    description: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ExtractionField(BaseModel):
    """A single extracted field: value + confidence + evidence phrases."""
    field_name: str
    value: Optional[JsonValue] = None          # JSON-compatible: str, int, float, bool, list, dict, null
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)

    @field_validator("value", mode="before")
    @classmethod
    def parse_value(cls, v: Any) -> Any:
        """Parse JSON-encoded string from DB storage if applicable."""
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return v
        return v

    @field_validator("evidence", mode="before")
    @classmethod
    def parse_evidence(cls, v: Any) -> list[str]:
        """Parse evidence strings or JSON-serialized lists from DB."""
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed]
                return [v]
            except Exception:
                return [v] if v else []
        elif v is None:
            return []
        return v


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

class ReportBase(BaseModel):
    id: str
    source: str                          # whatsapp | sms | web | field_worker
    raw_text: str
    received_at: str                     # ISO 8601 with timezone
    language: str                        # en | hi | hi-Latn
    reporter_id: str
    gps_lat: Optional[float] = None
    gps_lon: Optional[float] = None


class ReportOut(ReportBase):
    """Report as returned by the API."""
    processed: bool = False
    normalized_text: Optional[str] = None
    received_at_utc: Optional[datetime] = None
    location_resolved: Optional[str] = None
    location_lat: Optional[float] = None
    location_lon: Optional[float] = None
    geo_confidence: float = 0.0
    extractions: list[ExtractionField] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class ReportSummary(BaseModel):
    """Minimal report info for listing."""
    id: str
    source: str
    language: str
    received_at: str
    processed: bool

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Location Resolution
# ---------------------------------------------------------------------------

class LocationCandidate(BaseModel):
    """Candidate location considered during fuzzy resolution."""
    location_id: str
    name: str
    similarity: float
    latitude: float
    longitude: float

    model_config = ConfigDict(from_attributes=True)


class LocationResolution(BaseModel):
    """Structured, explainable geographic resolution result."""
    location_resolved: bool = False
    location_raw: Optional[str] = None
    location_id: Optional[str] = None           # gazetteer location_id (e.g. "civil_lines")
    resolved_name: Optional[str] = None         # canonical name (e.g. "Civil Lines")
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    geo_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    resolution_method: str = "unresolved"       # gps | exact | fuzzy | unresolved
    match_score: Optional[float] = None         # similarity score for fuzzy match (0.0–1.0)
    candidates: list[LocationCandidate] = Field(default_factory=list)
    explanation: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Match Score Breakdown (Explainable pairwise matching)
# ---------------------------------------------------------------------------

class MatchFactor(BaseModel):
    """A single factor in the explainable match score."""
    score: float = Field(..., ge=0.0, le=1.0)
    weight: float = Field(..., ge=0.0, le=1.0)
    contribution: float = Field(..., ge=0.0, le=1.0)

    model_config = ConfigDict(from_attributes=True)


class MatchScoreBreakdown(BaseModel):
    """
    Explainable 4-factor match score between two emergency reports.
    Exposes individual factor scores, weights, contributions, and final score.
    """
    semantic: MatchFactor
    geographic: MatchFactor
    temporal: MatchFactor
    incident_type: MatchFactor
    final_score: float = Field(..., ge=0.0, le=1.0)

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Contradiction
# ---------------------------------------------------------------------------

class ContradictionSide(BaseModel):
    report_id: str
    value: Optional[JsonValue] = None
    evidence: Optional[Union[str, list[str]]] = None


class ContradictionOut(BaseModel):
    id: str
    incident_id: str
    contradiction_type: str              # numeric | severity | incident_type | location | hazard_structural
    field: str
    side_a: ContradictionSide
    side_b: ContradictionSide
    resolution: Optional[str] = None
    explanation: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ContradictionResult(BaseModel):
    """Detailed in-memory representation of a detected contradiction."""
    id: str
    incident_id: str
    contradiction_type: str              # numeric | severity | incident_type | location | hazard_structural
    field: str
    side_a: ContradictionSide
    side_b: ContradictionSide
    resolution: Optional[str] = None
    explanation: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ContradictionOutput(BaseModel):
    """Grouped contradiction detection results for an incident."""
    incident_id: str
    contradictions: list[ContradictionResult] = Field(default_factory=list)
    total_contradictions: int = 0

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Confidence breakdown
# ---------------------------------------------------------------------------

class ConfidenceComponent(BaseModel):
    value: float
    weight: float
    detail: str

    model_config = ConfigDict(from_attributes=True)


class ConfidenceBreakdown(BaseModel):
    final: float
    breakdown: dict[str, ConfidenceComponent] = Field(default_factory=dict)
    source_count: Optional[float] = None
    source_count_detail: Optional[str] = None
    source_diversity: Optional[float] = None
    source_diversity_detail: Optional[str] = None
    consistency: Optional[float] = None
    consistency_detail: Optional[str] = None
    extraction_quality: Optional[float] = None
    extraction_detail: Optional[str] = None
    information_type: Optional[float] = None
    information_type_detail: Optional[str] = None
    weights: Optional[dict[str, float]] = None

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Severity & Priority Breakdown (Phase 7)
# ---------------------------------------------------------------------------

class SeverityModifier(BaseModel):
    name: str
    bonus: float
    evidence: list[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class SeverityBreakdown(BaseModel):
    base_type: Optional[str] = None
    base_type_score: float = 0.0
    keyword: Optional[str] = None
    keyword_score: float = 0.0
    baseline_severity: float = 0.0
    modifiers: list[SeverityModifier] = Field(default_factory=list)
    final_severity: float = 0.0
    evidence: list[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class PriorityBreakdown(BaseModel):
    severity: float
    confidence: float
    raw_priority: float
    low_confidence_cap_applied: bool = False
    final_priority: float
    urgency: str
    formula: str

    model_config = ConfigDict(from_attributes=True)


class IncidentAssessment(BaseModel):
    incident_id: str
    severity: SeverityBreakdown
    confidence: ConfidenceBreakdown
    priority: PriorityBreakdown
    urgency: str

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Incident
# ---------------------------------------------------------------------------

class IncidentOut(BaseModel):
    """Candidate incident as returned by the API."""
    id: str
    status: str
    title: Optional[str] = None
    severity: Optional[float] = None
    confidence: Optional[float] = None
    confidence_breakdown: Optional[ConfidenceBreakdown] = None
    priority: Optional[float] = None
    urgency: Optional[str] = None
    location_resolved: Optional[str] = None
    location_lat: Optional[float] = None
    location_lon: Optional[float] = None
    geo_confidence: float = 0.0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    report_count: int = 0
    reports: list[ReportOut] = Field(default_factory=list)
    contradictions: list[ContradictionOut] = Field(default_factory=list)
    related_incident_ids: list[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class IncidentSummary(BaseModel):
    """Minimal incident info for the queue list."""
    id: str
    status: str
    title: Optional[str] = None
    severity: Optional[float] = None
    confidence: Optional[float] = None
    priority: Optional[float] = None
    urgency: Optional[str] = None
    location_resolved: Optional[str] = None
    report_count: int = 0
    contradiction_count: int = 0

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Verification actions & Requests
# ---------------------------------------------------------------------------

class VerifyAction(BaseModel):
    action: str = Field(..., pattern="^(verify|reject|split)$")
    incident_id: str
    responder_id: str = "demo-responder"
    notes: Optional[str] = None


class VerifyRequest(BaseModel):
    responder_id: str = "demo-responder"
    notes: Optional[str] = None


class RejectRequest(BaseModel):
    responder_id: str = "demo-responder"
    notes: Optional[str] = None


class SplitRequest(BaseModel):
    responder_id: str = "demo-responder"
    report_ids: list[str] = Field(..., description="Report IDs to split into a new incident")
    notes: Optional[str] = None


class SplitResponse(BaseModel):
    original_incident_id: str
    new_incident_id: str
    split_report_ids: list[str]
    remaining_report_ids: list[str]
    message: str


class AuditLogOut(BaseModel):
    id: int
    action: str
    incident_id: str
    responder_id: str
    timestamp: datetime
    notes: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ReportDetailOut(BaseModel):
    """Detailed report representation for responder inspection."""
    id: str
    source: str
    raw_text: str
    normalized_text: Optional[str] = None
    received_at: str
    received_at_utc: Optional[datetime] = None
    language: str
    reporter_id: str = "anonymized"
    gps_lat: Optional[float] = None
    gps_lon: Optional[float] = None
    location_resolved: Optional[str] = None
    location_lat: Optional[float] = None
    location_lon: Optional[float] = None
    geo_confidence: float = 0.0
    processed: bool = False
    match_score: Optional[float] = None
    extractions: list[ExtractionField] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class IncidentDetailOut(BaseModel):
    """Complete explainable incident representation for responder dashboard."""
    id: str
    status: str
    title: Optional[str] = None
    severity: Optional[float] = None
    confidence: Optional[float] = None
    priority: Optional[float] = None
    urgency: Optional[str] = None
    location_resolved: Optional[str] = None
    location_lat: Optional[float] = None
    location_lon: Optional[float] = None
    geo_confidence: float = 0.0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    report_count: int = 0
    source_diversity: int = 0
    severity_breakdown: Optional[SeverityBreakdown] = None
    confidence_breakdown: Optional[ConfidenceBreakdown] = None
    priority_breakdown: Optional[PriorityBreakdown] = None
    contradictions: list[ContradictionOut] = Field(default_factory=list)
    related_incident_ids: list[str] = Field(default_factory=list)
    reports: list[ReportDetailOut] = Field(default_factory=list)
    audit_logs: list[AuditLogOut] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class PipelineStatus(BaseModel):
    status: str                          # idle | running | completed | error
    total_reports: int = 0
    processed_reports: int = 0
    incidents_formed: int = 0
    contradictions_found: int = 0
    message: Optional[str] = None


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str = "ok"
    demo_mode: bool = True
    version: str = "0.1.0"
    total_reports: int = 0
    total_incidents: int = 0


# ---------------------------------------------------------------------------
# Clustering & Cluster Explainability (Phase 5)
# ---------------------------------------------------------------------------

class PairwiseMatch(BaseModel):
    """Pairwise match evidence between two reports."""
    report_a_id: str
    report_b_id: str
    score: float = Field(..., ge=0.0, le=1.0)
    breakdown: Optional[MatchScoreBreakdown] = None

    model_config = ConfigDict(from_attributes=True)


class ClusterMembershipDecision(BaseModel):
    """Explainable decision log for a report attempting to join a cluster."""
    report_id: str
    action: str = Field(..., description="join | reject | new_cluster")
    cluster_id: str
    reason: str
    scores_with_members: dict[str, float] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)


class RelatedClusterPair(BaseModel):
    """A 'possibly related' link between two distinct clusters (0.60–0.79 band)."""
    cluster_a_id: str
    cluster_b_id: str
    score: float = Field(..., ge=0.0, le=1.0)
    best_pair: Optional[tuple[str, str]] = None

    model_config = ConfigDict(from_attributes=True)


class ClusterResult(BaseModel):
    """A candidate incident cluster formed by incremental complete-linkage."""
    cluster_id: str
    report_ids: list[str] = Field(default_factory=list)
    size: int = 0
    min_pairwise_score: float = 1.0
    avg_pairwise_score: float = 1.0
    pairwise_matches: list[PairwiseMatch] = Field(default_factory=list)
    membership_decisions: list[ClusterMembershipDecision] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class ClusteringOutput(BaseModel):
    """Complete output of the safe clustering engine."""
    clusters: list[ClusterResult] = Field(default_factory=list)
    related_pairs: list[RelatedClusterPair] = Field(default_factory=list)
    processing_log: list[str] = Field(default_factory=list)
    total_reports: int = 0
    total_clusters: int = 0

    model_config = ConfigDict(from_attributes=True)
