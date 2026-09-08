"""
SETU ORM Models — SQLAlchemy table definitions.

Tables:
  Report           — raw incoming report
  Extraction       — structured field extraction per report (value/confidence/evidence)
  Incident         — candidate incident formed by clustering
  IncidentReport   — many-to-many: which reports belong to which incident
  RelatedIncident  — "possibly related" links between incidents (0.60–0.79 band)
  Contradiction    — typed contradictions within an incident
  AuditLog         — persistent audit records for verify/reject/split actions
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    String,
    Text,
    Float,
    Integer,
    Boolean,
    DateTime,
    ForeignKey,
)
from sqlalchemy.orm import relationship

from database import Base


class Report(Base):
    """A single raw emergency report from any source channel."""

    __tablename__ = "reports"

    id = Column(String, primary_key=True)                         # e.g. "R001"
    source = Column(String, nullable=False)                       # whatsapp | sms | web | field_worker
    raw_text = Column(Text, nullable=False)                       # original message body
    received_at = Column(String, nullable=False)                  # ISO 8601 with timezone
    received_at_utc = Column(DateTime, nullable=True)             # normalized UTC timestamp
    language = Column(String, nullable=False)                     # en | hi | hi-Latn
    reporter_id = Column(String, nullable=False)                  # anonymized fictional ID
    gps_lat = Column(Float, nullable=True)                        # only if source provides GPS
    gps_lon = Column(Float, nullable=True)

    # Processing state
    processed = Column(Boolean, default=False)
    normalized_text = Column(Text, nullable=True)                 # cleaned text after normalization

    # Embedding (stored as JSON list of floats, computed in Phase 4)
    embedding = Column(Text, nullable=True)

    # Resolved location (populated in Phase 3)
    location_resolved = Column(String, nullable=True)             # gazetteer location_id or null
    location_lat = Column(Float, nullable=True)
    location_lon = Column(Float, nullable=True)
    geo_confidence = Column(Float, default=0.0)

    # Relationships
    extractions = relationship("Extraction", back_populates="report", cascade="all, delete-orphan")
    incident_links = relationship("IncidentReport", back_populates="report", cascade="all, delete-orphan")


class Extraction(Base):
    """
    A single extracted field from a report.

    Each extraction is a triplet: value, confidence, evidence.
    field_name is one of: incident_type, people_estimate, people_trapped,
    people_affected, people_nearby, people_at_risk, vulnerable_persons,
    trapped_or_rescue, urgency_signals, location_raw, severity_hint, info_type.
    """

    __tablename__ = "extractions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_id = Column(String, ForeignKey("reports.id"), nullable=False)
    field_name = Column(String, nullable=False)
    value = Column(Text, nullable=True)         # JSON-encoded value (string, int, bool, list, dict)
    confidence = Column(Float, default=0.0)     # 0.0–1.0
    evidence = Column(Text, nullable=True)      # JSON array of evidence phrases from raw_text

    report = relationship("Report", back_populates="extractions")


class Incident(Base):
    """A candidate incident formed by clustering related reports."""

    __tablename__ = "incidents"

    id = Column(String, primary_key=True)                         # e.g. "INC-001"
    status = Column(String, default="unverified")                 # unverified | verified | rejected | split
    title = Column(String, nullable=True)                         # human-readable summary

    # Scoring
    severity = Column(Float, nullable=True)                       # 0–100
    confidence = Column(Float, nullable=True)                     # 0.0–1.0 (final)
    confidence_breakdown = Column(Text, nullable=True)            # JSON: explainable breakdown
    priority = Column(Float, nullable=True)                       # computed from severity × confidence formula
    urgency = Column(String, nullable=True)                       # critical | high | medium | low

    # Resolved location (representative for the cluster)
    location_resolved = Column(String, nullable=True)
    location_lat = Column(Float, nullable=True)
    location_lon = Column(Float, nullable=True)
    geo_confidence = Column(Float, default=0.0)

    # Timestamps
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    report_links = relationship("IncidentReport", back_populates="incident", cascade="all, delete-orphan")
    contradictions = relationship("Contradiction", back_populates="incident", cascade="all, delete-orphan")
    audit_logs = relationship("AuditLog", back_populates="incident", cascade="all, delete-orphan")
    related_a = relationship("RelatedIncident", foreign_keys="RelatedIncident.incident_a_id",
                             cascade="all, delete-orphan")
    related_b = relationship("RelatedIncident", foreign_keys="RelatedIncident.incident_b_id",
                             cascade="all, delete-orphan")


class IncidentReport(Base):
    """Many-to-many link: which reports constitute a candidate incident."""

    __tablename__ = "incident_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    incident_id = Column(String, ForeignKey("incidents.id"), nullable=False)
    report_id = Column(String, ForeignKey("reports.id"), nullable=False)
    match_score = Column(Float, nullable=True)          # overall match score when this report was added
    match_details = Column(Text, nullable=True)         # JSON: per-factor score breakdown

    incident = relationship("Incident", back_populates="report_links")
    report = relationship("Report", back_populates="incident_links")


class RelatedIncident(Base):
    """
    'Possibly related' link between two incidents (0.60–0.79 band).
    Kept separate from merging — shown as a UI hint.
    """

    __tablename__ = "related_incidents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    incident_a_id = Column(String, ForeignKey("incidents.id"), nullable=False)
    incident_b_id = Column(String, ForeignKey("incidents.id"), nullable=False)
    score = Column(Float, nullable=False)


class Contradiction(Base):
    """
    A typed contradiction between two reports within the same incident.

    Types: numeric, severity, incident_type, location, hazard_structural.
    Never silently resolved — resolution is null until a human acts.
    """

    __tablename__ = "contradictions"

    id = Column(String, primary_key=True)
    incident_id = Column(String, ForeignKey("incidents.id"), nullable=False)
    contradiction_type = Column(String, nullable=False)     # numeric | severity | incident_type | location | hazard_structural
    field = Column(String, nullable=False)                  # which extracted field conflicts

    side_a_report_id = Column(String, nullable=False)
    side_a_value = Column(Text, nullable=True)
    side_a_evidence = Column(Text, nullable=True)

    side_b_report_id = Column(String, nullable=False)
    side_b_value = Column(Text, nullable=True)
    side_b_evidence = Column(Text, nullable=True)

    resolution = Column(Text, nullable=True)                # null until human resolves

    incident = relationship("Incident", back_populates="contradictions")


class AuditLog(Base):
    """
    Persistent audit record for every verify/reject/split action.
    """

    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    action = Column(String, nullable=False)                 # verify | reject | split
    incident_id = Column(String, ForeignKey("incidents.id"), nullable=False)
    responder_id = Column(String, default="demo-responder")
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    notes = Column(Text, nullable=True)

    incident = relationship("Incident", back_populates="audit_logs")
