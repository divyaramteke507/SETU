"""
SETU Responder Workflow & Incident Intelligence Router — Phase 8.

Exposes REST endpoints for:
  - Incident queue listing, filtering, and sorting
  - Explainable incident intelligence detail inspection
  - Linked reports and P6 contradiction sub-resources
  - Human responder verification, rejection, and splitting workflows
  - Pipeline triggering and repeat processing
  - Append-only audit trail exploration

Safety Invariants:
  - Human responder is the final authority. No automated dispatch decisions.
  - GET endpoints are strictly read-only and free of side-effects.
  - No direct ORM leakage: all payloads marshaled via explicit Pydantic schemas.
  - All responder actions (verify, reject, split) record append-only AuditLog entries.
  - Raw reports and historical evidence are never deleted.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from database import get_db
from models import (
    AuditLog,
    Contradiction,
    Extraction,
    Incident,
    IncidentReport,
    RelatedIncident,
    Report,
)
from schemas import (
    AuditLogOut,
    ConfidenceBreakdown,
    ConfidenceComponent,
    ContradictionOut,
    ContradictionSide,
    ExtractionField,
    IncidentDetailOut,
    IncidentSummary,
    PipelineStatus,
    PriorityBreakdown,
    RejectRequest,
    ReportDetailOut,
    SeverityBreakdown,
    SeverityModifier,
    SplitRequest,
    SplitResponse,
    VerifyRequest,
)
from services.clusterer import _resolve_unique_incident_id
from services.contradiction_detector import (
    detect_incident_contradictions,
    persist_contradictions,
)
from services.incident_assessor import (
    assess_incident,
    calculate_priority,
    calculate_severity,
    determine_urgency_band,
    persist_incident_assessment,
)
from services.pipeline_runner import run_pipeline

router = APIRouter(prefix="/api", tags=["incidents"])


# ---------------------------------------------------------------------------
# Helper Serialization Functions
# ---------------------------------------------------------------------------

def _build_extractions_map(reports: list[Report]) -> dict[str, list[ExtractionField]]:
    """Convert Report ORM extractions into Pydantic ExtractionField list map."""
    result: dict[str, list[ExtractionField]] = {}
    for r in reports:
        ext_list: list[ExtractionField] = []
        for e in (r.extractions or []):
            val = None
            if e.value:
                try:
                    val = json.loads(e.value)
                except Exception:
                    val = e.value
            ev_list: list[str] = []
            if e.evidence:
                try:
                    parsed = json.loads(e.evidence)
                    ev_list = parsed if isinstance(parsed, list) else [str(parsed)]
                except Exception:
                    ev_list = [e.evidence]
            ext_list.append(
                ExtractionField(
                    field_name=e.field_name,
                    value=val,
                    confidence=e.confidence,
                    evidence=ev_list,
                )
            )
        result[r.id] = ext_list
    return result


def _format_report_out(rep: Report, match_score: Optional[float] = None) -> ReportDetailOut:
    """Format an ORM Report into a safe ReportDetailOut schema minimizing PII."""
    exts: list[ExtractionField] = []
    for e in (rep.extractions or []):
        val = None
        if e.value:
            try:
                val = json.loads(e.value)
            except Exception:
                val = e.value
        ev_list: list[str] = []
        if e.evidence:
            try:
                parsed = json.loads(e.evidence)
                ev_list = parsed if isinstance(parsed, list) else [str(parsed)]
            except Exception:
                ev_list = [e.evidence]
        exts.append(
            ExtractionField(
                field_name=e.field_name,
                value=val,
                confidence=e.confidence,
                evidence=ev_list,
            )
        )

    return ReportDetailOut(
        id=rep.id,
        source=rep.source,
        raw_text=rep.raw_text,
        normalized_text=rep.normalized_text,
        received_at=rep.received_at,
        received_at_utc=rep.received_at_utc,
        language=rep.language,
        reporter_id=rep.reporter_id or "anonymized",
        gps_lat=rep.gps_lat,
        gps_lon=rep.gps_lon,
        location_resolved=rep.location_resolved,
        location_lat=rep.location_lat,
        location_lon=rep.location_lon,
        geo_confidence=rep.geo_confidence or 0.0,
        processed=rep.processed or False,
        match_score=match_score,
        extractions=exts,
    )


def _format_contradiction_out(c: Contradiction) -> ContradictionOut:
    """Format an ORM Contradiction into a ContradictionOut schema."""
    side_a_val = None
    if c.side_a_value:
        try:
            side_a_val = json.loads(c.side_a_value)
        except Exception:
            side_a_val = c.side_a_value

    side_b_val = None
    if c.side_b_value:
        try:
            side_b_val = json.loads(c.side_b_value)
        except Exception:
            side_b_val = c.side_b_value

    side_a_ev = None
    if c.side_a_evidence:
        try:
            side_a_ev = json.loads(c.side_a_evidence)
        except Exception:
            side_a_ev = c.side_a_evidence

    side_b_ev = None
    if c.side_b_evidence:
        try:
            side_b_ev = json.loads(c.side_b_evidence)
        except Exception:
            side_b_ev = c.side_b_evidence

    return ContradictionOut(
        id=c.id,
        incident_id=c.incident_id,
        contradiction_type=c.contradiction_type,
        field=c.field,
        side_a=ContradictionSide(
            report_id=c.side_a_report_id,
            value=side_a_val,
            evidence=side_a_ev,
        ),
        side_b=ContradictionSide(
            report_id=c.side_b_report_id,
            value=side_b_val,
            evidence=side_b_ev,
        ),
        resolution=c.resolution,
        explanation=f"Contradiction on '{c.field}' between {c.side_a_report_id} and {c.side_b_report_id}",
    )


def _format_incident_detail(inc: Incident, db: Session) -> IncidentDetailOut:
    """Serialize an Incident ORM model into a complete explainable IncidentDetailOut."""
    # Gather reports and pairwise match scores
    report_links = (
        db.query(IncidentReport)
        .filter(IncidentReport.incident_id == inc.id)
        .all()
    )
    formatted_reports: list[ReportDetailOut] = []
    source_channels: set[str] = set()
    raw_reports: list[Report] = []

    for link in report_links:
        if link.report:
            raw_reports.append(link.report)
            source_channels.add(link.report.source)
            formatted_reports.append(_format_report_out(link.report, match_score=link.match_score))

    # Gather contradictions
    contr_rows = db.query(Contradiction).filter(Contradiction.incident_id == inc.id).all()
    formatted_contrs = [_format_contradiction_out(c) for c in contr_rows]

    # Gather related incidents
    rel_rows = (
        db.query(RelatedIncident)
        .filter((RelatedIncident.incident_a_id == inc.id) | (RelatedIncident.incident_b_id == inc.id))
        .all()
    )
    related_ids: list[str] = []
    for rel in rel_rows:
        other_id = rel.incident_b_id if rel.incident_a_id == inc.id else rel.incident_a_id
        if other_id not in related_ids:
            related_ids.append(other_id)

    # Gather audit logs
    audit_rows = (
        db.query(AuditLog)
        .filter(AuditLog.incident_id == inc.id)
        .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
        .all()
    )
    formatted_audits = [
        AuditLogOut(
            id=a.id,
            action=a.action,
            incident_id=a.incident_id,
            responder_id=a.responder_id or "demo-responder",
            timestamp=a.timestamp,
            notes=a.notes,
        )
        for a in audit_rows
    ]

    # Reconstruct explainable breakdowns
    ext_map = _build_extractions_map(raw_reports)
    sev_breakdown = calculate_severity(raw_reports, extractions_by_report=ext_map) if raw_reports else None

    # Confidence breakdown
    conf_breakdown: Optional[ConfidenceBreakdown] = None
    if inc.confidence_breakdown:
        try:
            raw_cb = json.loads(inc.confidence_breakdown)
            conf_breakdown = ConfidenceBreakdown(**raw_cb)
        except Exception:
            pass

    # Priority breakdown
    prio_breakdown: Optional[PriorityBreakdown] = None
    if inc.severity is not None and inc.confidence is not None:
        prio_breakdown = calculate_priority(inc.severity, inc.confidence)

    return IncidentDetailOut(
        id=inc.id,
        status=inc.status,
        title=inc.title,
        severity=inc.severity,
        confidence=inc.confidence,
        priority=inc.priority,
        urgency=inc.urgency or determine_urgency_band(inc.priority or 0.0),
        location_resolved=inc.location_resolved,
        location_lat=inc.location_lat,
        location_lon=inc.location_lon,
        geo_confidence=inc.geo_confidence or 0.0,
        created_at=inc.created_at,
        updated_at=inc.updated_at,
        report_count=len(formatted_reports),
        source_diversity=len(source_channels),
        severity_breakdown=sev_breakdown,
        confidence_breakdown=conf_breakdown,
        priority_breakdown=prio_breakdown,
        contradictions=formatted_contrs,
        related_incident_ids=related_ids,
        reports=formatted_reports,
        audit_logs=formatted_audits,
    )


# ---------------------------------------------------------------------------
# 1. Incident Listing & Filtering
# ---------------------------------------------------------------------------

@router.get("/incidents", response_model=list[IncidentSummary])
def list_incidents(
    status: Optional[str] = Query(None, description="Filter by status: unverified | verified | rejected"),
    urgency: Optional[str] = Query(None, description="Filter by urgency: critical | high | medium | low"),
    min_priority: Optional[float] = Query(None, ge=0.0, le=100.0, description="Minimum priority (0-100)"),
    max_priority: Optional[float] = Query(None, ge=0.0, le=100.0, description="Maximum priority (0-100)"),
    location: Optional[str] = Query(None, description="Case-insensitive substring search on resolved location"),
    limit: int = Query(50, ge=1, le=200, description="Maximum number of incidents to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    db: Session = Depends(get_db),
):
    """
    List candidate incidents with sorting and optional filtering.

    Sort order: Priority descending (highest urgency first), stable by ID.
    """
    query = db.query(Incident)

    if status:
        query = query.filter(Incident.status == status.lower().strip())

    if urgency:
        query = query.filter(Incident.urgency == urgency.lower().strip())

    if min_priority is not None:
        query = query.filter(Incident.priority >= min_priority)

    if max_priority is not None:
        query = query.filter(Incident.priority <= max_priority)

    if location:
        query = query.filter(Incident.location_resolved.ilike(f"%{location.strip()}%"))

    # Order by priority descending (nulls last), then ID ascending
    query = query.order_by(Incident.priority.desc(), Incident.id.asc())

    results = query.offset(offset).limit(limit).all()

    summaries: list[IncidentSummary] = []
    for inc in results:
        rep_count = db.query(IncidentReport).filter(IncidentReport.incident_id == inc.id).count()
        contr_count = db.query(Contradiction).filter(Contradiction.incident_id == inc.id).count()
        summaries.append(
            IncidentSummary(
                id=inc.id,
                status=inc.status,
                title=inc.title,
                severity=inc.severity,
                confidence=inc.confidence,
                priority=inc.priority,
                urgency=inc.urgency or determine_urgency_band(inc.priority or 0.0),
                location_resolved=inc.location_resolved,
                report_count=rep_count,
                contradiction_count=contr_count,
            )
        )

    return summaries


# ---------------------------------------------------------------------------
# 2. Incident Detail & Sub-Resources
# ---------------------------------------------------------------------------

@router.get("/incidents/{incident_id}", response_model=IncidentDetailOut)
def get_incident_detail(
    incident_id: str,
    db: Session = Depends(get_db),
):
    """
    Retrieve complete explainable incident detail, including breakdowns,
    contradictions, linked reports, and audit trail.
    """
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{incident_id}' not found.",
        )

    return _format_incident_detail(incident, db)


@router.get("/incidents/{incident_id}/reports", response_model=list[ReportDetailOut])
def get_incident_reports(
    incident_id: str,
    db: Session = Depends(get_db),
):
    """Retrieve all reports constituting a candidate incident."""
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{incident_id}' not found.",
        )

    report_links = (
        db.query(IncidentReport)
        .filter(IncidentReport.incident_id == incident_id)
        .all()
    )
    return [
        _format_report_out(link.report, match_score=link.match_score)
        for link in report_links
        if link.report
    ]


@router.get("/incidents/{incident_id}/contradictions", response_model=list[ContradictionOut])
def get_incident_contradictions(
    incident_id: str,
    db: Session = Depends(get_db),
):
    """Retrieve all P6 contradiction records inside a candidate incident."""
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{incident_id}' not found.",
        )

    contr_rows = db.query(Contradiction).filter(Contradiction.incident_id == incident_id).all()
    return [_format_contradiction_out(c) for c in contr_rows]


# ---------------------------------------------------------------------------
# 3. Responder Actions: Verify, Reject, Split
# ---------------------------------------------------------------------------

@router.post("/incidents/{incident_id}/verify", response_model=IncidentDetailOut)
def verify_incident(
    incident_id: str,
    payload: VerifyRequest = VerifyRequest(),
    db: Session = Depends(get_db),
):
    """
    Human responder verifies a candidate incident.

    Valid transitions:
      - unverified -> verified
      - verified -> verified (idempotent)
    Invalid transitions:
      - rejected -> verified (raises 409 Conflict)
    """
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{incident_id}' not found.",
        )

    if incident.status == "rejected":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Incident '{incident_id}' has already been rejected and cannot be verified without re-review.",
        )

    # State update
    incident.status = "verified"
    incident.updated_at = datetime.now(timezone.utc)

    # Append-only audit record
    audit = AuditLog(
        action="verify",
        incident_id=incident_id,
        responder_id=payload.responder_id or "demo-responder",
        notes=payload.notes,
    )
    db.add(audit)
    db.commit()

    return _format_incident_detail(incident, db)


@router.post("/incidents/{incident_id}/reject", response_model=IncidentDetailOut)
def reject_incident(
    incident_id: str,
    payload: RejectRequest = RejectRequest(),
    db: Session = Depends(get_db),
):
    """
    Human responder rejects a candidate incident.

    Preserves all reports, cluster evidence, and contradictions without deletion.
    """
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{incident_id}' not found.",
        )

    incident.status = "rejected"
    incident.updated_at = datetime.now(timezone.utc)

    # Append-only audit record
    audit = AuditLog(
        action="reject",
        incident_id=incident_id,
        responder_id=payload.responder_id or "demo-responder",
        notes=payload.notes,
    )
    db.add(audit)
    db.commit()

    return _format_incident_detail(incident, db)


@router.post("/incidents/{incident_id}/split", response_model=SplitResponse)
def split_incident(
    incident_id: str,
    payload: SplitRequest,
    db: Session = Depends(get_db),
):
    """
    Human responder splits incorrectly grouped reports into a new candidate incident.

    Requirements & Invariants:
      - Incident must exist (404).
      - Incident must not be rejected (409).
      - report_ids must not be empty (400).
      - report_ids must be unique (400).
      - All report_ids must belong to specified incident (400).
      - report_ids must not include all reports (at least 1 must remain) (400).
      - New incident ID must be unique.
      - P6 contradictions and P7 assessment are re-evaluated for both incidents.
      - AuditLog records are written for both incidents.
    """
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{incident_id}' not found.",
        )

    if incident.status == "rejected":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot split rejected incident '{incident_id}'.",
        )

    if not payload.report_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Report IDs list cannot be empty for splitting.",
        )

    if len(payload.report_ids) != len(set(payload.report_ids)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Duplicate report IDs provided in split selection.",
        )

    current_links = (
        db.query(IncidentReport)
        .filter(IncidentReport.incident_id == incident_id)
        .all()
    )
    current_rids = {link.report_id for link in current_links}

    # Verify ownership
    for rid in payload.report_ids:
        if rid not in current_rids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Report '{rid}' does not belong to incident '{incident_id}'.",
            )

    # Verify not all reports are selected
    if set(payload.report_ids) == current_rids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot split all reports from an incident. At least one report must remain in the original incident.",
        )

    try:
        # 1. Allocate unique new Incident ID
        used_ids = {row[0] for row in db.query(Incident.id).all()}
        new_id = _resolve_unique_incident_id(f"INC-{len(used_ids)+1:03d}", used_ids)

        # 2. Create new Incident record
        new_inc = Incident(
            id=new_id,
            status="unverified",
            title=f"Incident {new_id} (Split from {incident_id})",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(new_inc)
        db.flush()

        # 3. Update IncidentReport foreign keys for selected reports
        split_set = set(payload.report_ids)
        for link in current_links:
            if link.report_id in split_set:
                link.incident_id = new_id
        db.flush()

        # 4. Re-fetch constituent reports for both incidents
        orig_reports = [
            link.report
            for link in db.query(IncidentReport).filter(IncidentReport.incident_id == incident_id).all()
            if link.report
        ]
        new_reports = [
            link.report
            for link in db.query(IncidentReport).filter(IncidentReport.incident_id == new_id).all()
            if link.report
        ]

        # Build extractions map
        all_reports = orig_reports + new_reports
        ext_map = _build_extractions_map(all_reports)

        # 5. Re-evaluate P6 contradictions for original incident
        # Capture existing human resolutions before re-detection
        existing_contrs = db.query(Contradiction).filter(
            Contradiction.incident_id == incident_id
        ).all()
        human_resolutions: dict[tuple, str] = {}
        for c in existing_contrs:
            if c.resolution is not None:
                pair = tuple(sorted([c.side_a_report_id, c.side_b_report_id]))
                key = (c.contradiction_type, c.field, pair)
                human_resolutions[key] = c.resolution

        db.query(Contradiction).filter(Contradiction.incident_id == incident_id).delete()
        orig_contr_output = detect_incident_contradictions(
            incident_id, orig_reports, extractions_map=ext_map
        )
        persist_contradictions(db, orig_contr_output)

        # Detect P6 contradictions for new incident
        new_contr_output = detect_incident_contradictions(
            new_id, new_reports, extractions_map=ext_map
        )
        persist_contradictions(db, new_contr_output)

        # Restore human resolutions for contradictions that still exist in either affected incident
        if human_resolutions:
            affected_contrs = db.query(Contradiction).filter(
                Contradiction.incident_id.in_([incident_id, new_id])
            ).all()
            for c in affected_contrs:
                pair = tuple(sorted([c.side_a_report_id, c.side_b_report_id]))
                key = (c.contradiction_type, c.field, pair)
                if key in human_resolutions:
                    c.resolution = human_resolutions[key]

        db.flush()

        # 6. Recalculate P7 assessment for original incident
        orig_contrs = db.query(Contradiction).filter(Contradiction.incident_id == incident_id).all()
        orig_assessment = assess_incident(
            incident_id=incident_id,
            reports=orig_reports,
            contradictions=orig_contrs,
            extractions_by_report=ext_map,
        )
        persist_incident_assessment(db, orig_assessment)

        # Recalculate P7 assessment for new incident
        new_contrs = db.query(Contradiction).filter(Contradiction.incident_id == new_id).all()
        new_assessment = assess_incident(
            incident_id=new_id,
            reports=new_reports,
            contradictions=new_contrs,
            extractions_by_report=ext_map,
        )
        persist_incident_assessment(db, new_assessment)

        # Set location on new incident
        for rep in new_reports:
            if rep.location_resolved:
                new_inc.location_resolved = rep.location_resolved
                new_inc.location_lat = rep.location_lat
                new_inc.location_lon = rep.location_lon
                new_inc.geo_confidence = rep.geo_confidence
                break

        # 7. Append-only AuditLog records for both incidents
        split_str = ", ".join(sorted(payload.report_ids))
        audit_orig = AuditLog(
            action="split",
            incident_id=incident_id,
            responder_id=payload.responder_id or "demo-responder",
            notes=f"Split reports [{split_str}] into new incident {new_id}. Responder notes: {payload.notes or 'None'}",
        )
        audit_new = AuditLog(
            action="split",
            incident_id=new_id,
            responder_id=payload.responder_id or "demo-responder",
            notes=f"Created via split from {incident_id} with reports [{split_str}]. Responder notes: {payload.notes or 'None'}",
        )
        db.add(audit_orig)
        db.add(audit_new)
        db.commit()
    except Exception:
        db.rollback()
        raise

    remaining_rids = sorted(list(current_rids - split_set))
    return SplitResponse(
        original_incident_id=incident_id,
        new_incident_id=new_id,
        split_report_ids=sorted(payload.report_ids),
        remaining_report_ids=remaining_rids,
        message=f"Successfully split {len(payload.report_ids)} reports from {incident_id} into {new_id}.",
    )


# ---------------------------------------------------------------------------
# 4. Reports, Pipeline Runner & Audit Trail Endpoints
# ---------------------------------------------------------------------------

@router.get("/reports", response_model=list[ReportDetailOut])
def list_reports(
    processed: Optional[bool] = Query(None, description="Filter by processed status"),
    source: Optional[str] = Query(None, description="Filter by source channel"),
    db: Session = Depends(get_db),
):
    """List incoming/raw emergency reports."""
    query = db.query(Report).order_by(Report.id.asc())
    if processed is not None:
        query = query.filter(Report.processed == processed)
    if source:
        query = query.filter(Report.source == source.lower().strip())

    reports = query.all()
    return [_format_report_out(r) for r in reports]


@router.post("/pipeline/process", response_model=PipelineStatus)
def process_pipeline(
    reprocess: bool = Query(False, description="Whether to re-process existing candidate incidents"),
    db: Session = Depends(get_db),
):
    """
    Trigger end-to-end execution of SETU intelligence fusion pipeline.

    Idempotent: Re-executing without reprocess=True returns existing candidate
    incidents without generating duplicates.
    """
    return run_pipeline(db, reprocess=reprocess, seed_if_empty=True)


@router.get("/audit-logs", response_model=list[AuditLogOut])
def list_audit_logs(
    incident_id: Optional[str] = Query(None, description="Filter audit logs by incident ID"),
    db: Session = Depends(get_db),
):
    """List append-only audit trail records."""
    query = db.query(AuditLog).order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
    if incident_id:
        query = query.filter(AuditLog.incident_id == incident_id)

    logs = query.all()
    return [
        AuditLogOut(
            id=a.id,
            action=a.action,
            incident_id=a.incident_id,
            responder_id=a.responder_id or "demo-responder",
            timestamp=a.timestamp,
            notes=a.notes,
        )
        for a in logs
    ]
