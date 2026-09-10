"""
SETU Pipeline Runner Service — Phase 8.

Orchestrates the complete emergency information fusion pipeline:
  Step 1: Normalization (P1)
  Step 2: Structured Field Extraction (P2)
  Step 3: Location Resolution (P3)
  Step 4: Pairwise Scoring & Safe Clustering (P4/P5)
  Step 5: Contradiction Detection (P6)
  Step 6: Incident Assessment Engine (P7)

Invariants:
  - Thin orchestration only. Consumes existing P1–P7 services directly.
  - Zero duplication of normalization, extraction, matching, clustering,
    contradiction, or assessment logic.
  - Deterministic and idempotent: Repeated executions without reprocess=True
    will not create duplicate incidents, memberships, or contradictions.
  - Preserves verified, rejected, or audited incidents from accidental deletion.
"""

from __future__ import annotations

import json
from typing import Any, Optional
from sqlalchemy.orm import Session

from models import (
    AuditLog,
    Contradiction,
    Extraction,
    Incident,
    IncidentReport,
    RelatedIncident,
    Report,
)
from schemas import ExtractionField, PipelineStatus
from seed_data import SEED_REPORTS, seed_database
from services.clusterer import (
    cluster_reports,
    compute_all_pairwise_scores,
    persist_clustering_results,
)
from services.contradiction_detector import (
    detect_all_contradictions,
    persist_contradictions,
)
from services.extractor import extract_report
from services.incident_assessor import assess_incident, persist_incident_assessment
from services.location_resolver import resolve_location
from services.normalizer import normalize_report


def run_pipeline(
    db: Session,
    reprocess: bool = False,
    seed_if_empty: bool = True,
) -> PipelineStatus:
    """
    Execute the complete SETU intelligence pipeline deterministically.

    Args:
        db: Active SQLAlchemy database session.
        reprocess: If True, purges only unverified/non-audited candidate
                   incidents before re-clustering.
        seed_if_empty: If True and reports table is empty, seeds the 20 demo reports.

    Returns:
        PipelineStatus indicating the result and counts.
    """
    # 0. Seed reports if empty
    report_count = db.query(Report).count()
    if report_count == 0 and seed_if_empty:
        inserted = seed_database(db)
        report_count = db.query(Report).count()

    # Step 1: Normalization, Extraction, Location Resolution for any unparsed/unprocessed reports
    all_reports = db.query(Report).order_by(Report.id).all()
    unprocessed_found = False
    for rep in all_reports:
        # Run P1 Normalization if not processed or missing normalized_text
        if not rep.processed or not rep.normalized_text:
            unprocessed_found = True
            norm = normalize_report(
                report_id=rep.id,
                raw_text=rep.raw_text,
                received_at=rep.received_at,
                source=rep.source,
                language=rep.language,
            )
            rep.normalized_text = norm.normalized_text
            rep.received_at_utc = norm.received_at_utc
            rep.source = norm.source
            rep.language = norm.language

            # Run P2 Extraction
            extracted_fields = extract_report(
                norm.normalized_text,
                language=norm.language,
                source=norm.source,
            )
            # Remove any prior extractions for this report
            db.query(Extraction).filter(Extraction.report_id == rep.id).delete()
            for ef in extracted_fields:
                db_ext = Extraction(
                    report_id=rep.id,
                    field_name=ef.field_name,
                    value=json.dumps(ef.value) if ef.value is not None else None,
                    confidence=ef.confidence,
                    evidence=json.dumps(ef.evidence) if ef.evidence else None,
                )
                db.add(db_ext)

            # Run P3 Location Resolution
            raw_loc = None
            for ef in extracted_fields:
                if ef.field_name == "location_raw" and ef.value:
                    raw_loc = str(ef.value)
                    break

            loc_res = resolve_location(
                location_raw=raw_loc,
                gps_lat=rep.gps_lat,
                gps_lon=rep.gps_lon,
                language=norm.language,
            )
            if loc_res.location_resolved:
                rep.location_resolved = loc_res.location_id or loc_res.resolved_name
                rep.location_lat = loc_res.latitude
                rep.location_lon = loc_res.longitude
            rep.location_conflict = getattr(loc_res, "location_conflict", False) or False
            rep.location_conflict_text = getattr(loc_res, "location_conflict_text", None)
            rep.location_conflict_distance_m = getattr(loc_res, "location_conflict_distance_m", None)
            # Pre-compute and store embedding once to avoid 190x redundant generation in pairwise comparisons
            if not rep.embedding:
                from services.embedding_service import generate_embedding
                emb = generate_embedding(rep.normalized_text)
                rep.embedding = json.dumps(emb)

            rep.processed = True

    if unprocessed_found:
        db.commit()

    # Idempotency / Incremental Fusion check: if candidate incidents already exist and not reprocess
    existing_incidents = db.query(Incident).all()
    if existing_incidents and not reprocess:
        # Check if any reports are unassigned to any incident
        assigned_rids = {row[0] for row in db.query(IncidentReport.report_id).all()}
        unassigned_reports = [r for r in all_reports if r.id not in assigned_rids]

        if not unassigned_reports:
            processed_count = db.query(Report).filter(Report.processed == True).count()
            contr_count = db.query(Contradiction).count()
            return PipelineStatus(
                status="completed",
                total_reports=report_count,
                processed_reports=processed_count,
                incidents_formed=len(existing_incidents),
                contradictions_found=contr_count,
                message="Pipeline already executed for demo reports. Candidate incidents are active.",
            )

        # Incrementally fuse unassigned reports into existing or new incidents
        from config import CLUSTER_MERGE_THRESHOLD, CLUSTER_RELATED_THRESHOLD
        from services.clusterer import _resolve_unique_incident_id
        from services.matcher import match_reports
        from services.contradiction_detector import detect_incident_contradictions

        # Refresh all extractions map
        all_exts = db.query(Extraction).all()
        ext_map_all: dict[str, list[ExtractionField]] = {}
        for e in all_exts:
            v = None
            if e.value:
                try:
                    v = json.loads(e.value)
                except Exception:
                    v = e.value
            ev_list = []
            if e.evidence:
                try:
                    p = json.loads(e.evidence)
                    ev_list = p if isinstance(p, list) else [str(p)]
                except Exception:
                    ev_list = [e.evidence]
            ext_map_all.setdefault(e.report_id, []).append(
                ExtractionField(field_name=e.field_name, value=v, confidence=e.confidence, evidence=ev_list)
            )

        new_incidents_count = 0
        for rep in unassigned_reports:
            compatible_candidates = []
            related_candidates = []  # H2: track 0.60-0.79 band candidates
            for inc in existing_incidents:
                if inc.status == "rejected":
                    continue
                m_reps = [l.report for l in inc.report_links if l.report]
                if not m_reps:
                    continue
                scores = [match_reports(rep, m).final_score for m in m_reps]
                if all(s >= CLUSTER_MERGE_THRESHOLD for s in scores):
                    avg_score = sum(scores) / len(scores)
                    compatible_candidates.append((avg_score, inc.id, inc))
                else:
                    # H2: Check for related-incident band (0.60-0.79)
                    max_score = max(scores)
                    if CLUSTER_RELATED_THRESHOLD <= max_score < CLUSTER_MERGE_THRESHOLD:
                        related_candidates.append((max_score, inc.id))

            if compatible_candidates:
                # Sort by highest avg score, lowest ID
                compatible_candidates.sort(key=lambda x: (-x[0], x[1]))
                best_avg, _, target_inc = compatible_candidates[0]
                db.add(IncidentReport(incident_id=target_inc.id, report_id=rep.id, match_score=best_avg))
                db.flush()

                # Re-evaluate contradictions and assessment
                all_inc_reps = [l.report for l in target_inc.report_links if l.report]
                # H4: Preserve human-set contradiction resolutions before re-detection
                existing_contrs = db.query(Contradiction).filter(
                    Contradiction.incident_id == target_inc.id
                ).all()
                human_resolutions: dict[tuple, str] = {}
                for c in existing_contrs:
                    if c.resolution is not None:
                        pair = tuple(sorted([c.side_a_report_id, c.side_b_report_id]))
                        key = (c.contradiction_type, c.field, pair)
                        human_resolutions[key] = c.resolution
                db.query(Contradiction).filter(Contradiction.incident_id == target_inc.id).delete()
                c_out = detect_incident_contradictions(target_inc.id, all_inc_reps, extractions_map=ext_map_all)
                persist_contradictions(db, c_out)
                # H4: Restore human resolutions for contradictions that still exist
                if human_resolutions:
                    new_contrs = db.query(Contradiction).filter(
                        Contradiction.incident_id == target_inc.id
                    ).all()
                    for c in new_contrs:
                        pair = tuple(sorted([c.side_a_report_id, c.side_b_report_id]))
                        key = (c.contradiction_type, c.field, pair)
                        if key in human_resolutions:
                            c.resolution = human_resolutions[key]
                # Update location conflict on merged incident
                conflicting_reps = [r for r in all_inc_reps if getattr(r, "location_conflict", False)]
                if conflicting_reps:
                    target_inc.location_conflict = True
                    target_inc.location_conflict_text = getattr(conflicting_reps[0], "location_conflict_text", None)
                    target_inc.location_conflict_distance_m = getattr(conflicting_reps[0], "location_conflict_distance_m", None)
                inc_contrs = db.query(Contradiction).filter(Contradiction.incident_id == target_inc.id).all()
                assessment = assess_incident(target_inc.id, all_inc_reps, inc_contrs, extractions_by_report=ext_map_all)
                persist_incident_assessment(db, assessment)

                # Bug 1 Fix: Persist RelatedIncident links for 0.60-0.79 band when merging into target_inc
                for rel_score, rel_inc_id in related_candidates:
                    if rel_inc_id == target_inc.id:
                        continue
                    a_id, b_id = (target_inc.id, rel_inc_id) if target_inc.id <= rel_inc_id else (rel_inc_id, target_inc.id)
                    existing_rel = db.query(RelatedIncident).filter(
                        ((RelatedIncident.incident_a_id == a_id) & (RelatedIncident.incident_b_id == b_id))
                        | ((RelatedIncident.incident_a_id == b_id) & (RelatedIncident.incident_b_id == a_id))
                    ).first()
                    if existing_rel:
                        if rel_score > existing_rel.score:
                            existing_rel.score = rel_score
                    else:
                        db.add(RelatedIncident(incident_a_id=a_id, incident_b_id=b_id, score=rel_score))
                db.flush()
            else:
                # Create new candidate incident
                used_ids = {row[0] for row in db.query(Incident.id).all()}
                new_id = _resolve_unique_incident_id(f"INC-{len(used_ids)+1:03d}", used_ids)
                new_inc = Incident(
                    id=new_id,
                    status="unverified",
                    title=f"Incident {new_id}",
                    location_resolved=rep.location_resolved,
                    location_lat=rep.location_lat,
                    location_lon=rep.location_lon,
                    geo_confidence=rep.geo_confidence or 0.0,
                    location_conflict=getattr(rep, "location_conflict", False) or False,
                    location_conflict_text=getattr(rep, "location_conflict_text", None),
                    location_conflict_distance_m=getattr(rep, "location_conflict_distance_m", None),
                )
                db.add(new_inc)
                db.flush()
                db.add(IncidentReport(incident_id=new_id, report_id=rep.id, match_score=1.0))
                db.flush()

                c_out = detect_incident_contradictions(new_id, [rep], extractions_map=ext_map_all)
                persist_contradictions(db, c_out)
                assessment = assess_incident(new_id, [rep], [], extractions_by_report=ext_map_all)
                persist_incident_assessment(db, assessment)

                b_type = assessment.severity.base_type
                type_str = b_type.replace("_", " ").title() if b_type else "Incident"
                loc_str = new_inc.location_resolved or "Rampur"
                new_inc.title = f"{type_str} — {loc_str}"
                # H2: Create RelatedIncident links for 0.60-0.79 band
                for rel_score, rel_inc_id in related_candidates:
                    a_id, b_id = (new_id, rel_inc_id) if new_id <= rel_inc_id else (rel_inc_id, new_id)
                    existing_rel = db.query(RelatedIncident).filter(
                        ((RelatedIncident.incident_a_id == a_id) & (RelatedIncident.incident_b_id == b_id))
                        | ((RelatedIncident.incident_a_id == b_id) & (RelatedIncident.incident_b_id == a_id))
                    ).first()
                    if existing_rel:
                        if rel_score > existing_rel.score:
                            existing_rel.score = rel_score
                    else:
                        db.add(RelatedIncident(incident_a_id=a_id, incident_b_id=b_id, score=rel_score))
                existing_incidents.append(new_inc)
                new_incidents_count += 1

        db.commit()
        refreshed_incidents = db.query(Incident).all()
        contr_count = db.query(Contradiction).count()
        return PipelineStatus(
            status="completed",
            total_reports=len(all_reports),
            processed_reports=len(all_reports),
            incidents_formed=len(refreshed_incidents),
            contradictions_found=contr_count,
            message=f"Fused {len(unassigned_reports)} new report(s) into active candidate incidents ({new_incidents_count} new incident(s) formed).",
        )

    # C1 Fix: Track reports assigned to preserved (human-touched) incidents
    preserved_report_ids: set[str] = set()

    # If reprocess is requested, safely purge ONLY unverified, non-audited candidate incidents
    if reprocess and existing_incidents:
        for inc in existing_incidents:
            audit_count = db.query(AuditLog).filter(AuditLog.incident_id == inc.id).count()
            if inc.status == "unverified" and audit_count == 0:
                db.query(IncidentReport).filter(IncidentReport.incident_id == inc.id).delete()
                db.query(Contradiction).filter(Contradiction.incident_id == inc.id).delete()
                db.query(RelatedIncident).filter(
                    (RelatedIncident.incident_a_id == inc.id) | (RelatedIncident.incident_b_id == inc.id)
                ).delete()
                db.delete(inc)
        db.commit()
        # C1: Collect report IDs still assigned to preserved incidents
        for link in db.query(IncidentReport).all():
            preserved_report_ids.add(link.report_id)

    # Step 2: Build extractions lookup for clustering, contradictions, and assessor
    refreshed_reports = db.query(Report).order_by(Report.id).all()
    extractions_map: dict[str, list[ExtractionField]] = {}
    reports_by_id: dict[str, Report] = {}

    for r in refreshed_reports:
        reports_by_id[r.id] = r
        ext_list: list[ExtractionField] = []
        for e in r.extractions:
            v = None
            if e.value:
                try:
                    v = json.loads(e.value)
                except Exception:
                    v = e.value
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
                    value=v,
                    confidence=e.confidence,
                    evidence=ev_list,
                )
            )
        extractions_map[r.id] = ext_list

    # Step 3: Compute Pairwise Scores and Safe Complete-Linkage Clustering (P4/P5)
    # C1 Fix: Exclude reports still assigned to preserved (human-touched) incidents
    cluster_eligible_reports = [r for r in refreshed_reports if r.id not in preserved_report_ids]
    if cluster_eligible_reports:
        pairwise_lookup = compute_all_pairwise_scores(cluster_eligible_reports)
        clustering_output = cluster_reports(cluster_eligible_reports, precomputed_scores=pairwise_lookup)
        persisted_incidents = persist_clustering_results(
            db, clustering_output, reports_by_id=reports_by_id
        )
    else:
        persisted_incidents = []
    db.commit()

    # Step 4: Detect Contradictions inside each Candidate Incident (P6)
    incidents_with_reports = []
    for inc in persisted_incidents:
        inc_reports = [link.report for link in inc.report_links if link.report is not None]
        incidents_with_reports.append((inc.id, inc_reports))

    contradiction_outputs = detect_all_contradictions(
        incidents_with_reports, extractions_map=extractions_map
    )
    persist_contradictions(db, contradiction_outputs)
    db.commit()

    # Step 5: Incident Assessment (Severity, Confidence, Priority) (P7)
    total_contradictions = 0
    for inc in persisted_incidents:
        inc_reports = [link.report for link in inc.report_links if link.report is not None]
        inc_contrs = db.query(Contradiction).filter(Contradiction.incident_id == inc.id).all()
        total_contradictions += len(inc_contrs)

        assessment = assess_incident(
            incident_id=inc.id,
            reports=inc_reports,
            contradictions=inc_contrs,
            extractions_by_report=extractions_map,
        )
        persist_incident_assessment(db, assessment)

        # Enhance title with extracted incident type and location
        b_type = assessment.severity.base_type
        type_str = b_type.replace("_", " ").title() if b_type else "Incident"
        loc_str = inc.location_resolved or "Rampur"
        inc.title = f"{type_str} — {loc_str}"

    db.commit()

    # C1: Report total counts including any preserved incidents from reprocess
    total_incident_count = db.query(Incident).count()
    total_contradiction_count = db.query(Contradiction).count()

    return PipelineStatus(
        status="completed",
        total_reports=len(refreshed_reports),
        processed_reports=len(refreshed_reports),
        incidents_formed=total_incident_count,
        contradictions_found=total_contradiction_count,
        message=(
            f"Successfully processed {len(refreshed_reports)} reports into "
            f"{total_incident_count} candidate incidents with {total_contradiction_count} contradictions."
        ),
    )
