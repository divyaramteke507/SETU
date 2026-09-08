"""
SETU Safe Incident Clustering Engine — Phase 5.

Groups processed emergency reports into candidate incidents using
INCREMENTAL COMPLETE-LINKAGE CLUSTERING.

Safety Invariants:
1. NEVER use connected-components or transitive closure.
2. A report may join a cluster ONLY when its match score with EVERY
   existing cluster member is >= CLUSTER_MERGE_THRESHOLD (0.80).
   Example: A-B=0.85, B-C=0.86, A-C=0.42 MUST produce [A, B] and [C],
   never [A, B, C].
3. Reject duplicate report IDs with ValueError. Never silently deduplicate.
4. Reject invalid match scores (<0, >1, NaN, inf, non-numeric) with ValueError.
   Never silently clamp.
5. Strict threshold semantics:
   - score >= 0.80 -> merge-compatible
   - score < 0.80  -> fails complete-linkage
   - 0.60 <= score < 0.80 -> possibly related, never merged automatically
   - score < 0.60  -> separate
6. Deterministic processing order (sorted by report ID) and deterministic tie-breaking.
7. Preserves complete pairwise evidence and membership decisions.
8. No duplicate reverse relationships (A->B only).
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Optional, Sequence, Union

from config import CLUSTER_MERGE_THRESHOLD, CLUSTER_RELATED_THRESHOLD
from models import Incident, IncidentReport, RelatedIncident
from schemas import (
    ClusterMembershipDecision,
    ClusterResult,
    ClusteringOutput,
    MatchScoreBreakdown,
    PairwiseMatch,
    RelatedClusterPair,
)
from services.matcher import match_reports


# ---------------------------------------------------------------------------
# Validation Helpers
# ---------------------------------------------------------------------------

def validate_match_score(score: Any) -> float:
    """
    Validate that a match score is a finite numeric float within [0.0, 1.0].

    Raises:
        ValueError: If score is non-numeric, boolean, NaN, infinite, < 0.0, or > 1.0.
    """
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ValueError(
            f"Invalid match score: {score!r} ({type(score).__name__}). "
            "Match scores must be finite numeric values in [0.0, 1.0]."
        )
    if math.isnan(score) or math.isinf(score):
        raise ValueError(
            f"Invalid match score: {score}. "
            "Match scores must be finite numeric values in [0.0, 1.0]."
        )
    if score < 0.0 or score > 1.0:
        raise ValueError(
            f"Invalid match score: {score}. "
            "Match scores must be within [0.0, 1.0]."
        )
    return float(score)


def _get_report_id(report: Any) -> str:
    """Extract report ID from a string, dict, or object."""
    if isinstance(report, str):
        return report
    if isinstance(report, dict):
        val = report.get("id")
        if val is not None:
            return str(val)
    val = getattr(report, "id", None)
    if val is not None:
        return str(val)
    raise ValueError(f"Report object missing id attribute: {report!r}")


def validate_report_ids(reports: Sequence[Any]) -> list[str]:
    """
    Extract and validate report IDs from input reports.

    Raises:
        ValueError: If duplicate report IDs are detected.
    """
    ids: list[str] = []
    seen: set[str] = set()
    for report in reports:
        rid = _get_report_id(report)
        if rid in seen:
            raise ValueError(
                f"Duplicate report ID detected: '{rid}'. All report IDs must be unique."
            )
        seen.add(rid)
        ids.append(rid)
    return ids


def _canonical_pair(id_a: str, id_b: str) -> tuple[str, str]:
    """Return lexicographically sorted pair (id_min, id_max)."""
    return (id_a, id_b) if id_a <= id_b else (id_b, id_a)


# ---------------------------------------------------------------------------
# Pairwise Score Computation and Lookup
# ---------------------------------------------------------------------------

def compute_all_pairwise_scores(
    reports: Sequence[Any],
    precomputed_scores: Optional[
        Union[
            dict[tuple[str, str], Union[float, MatchScoreBreakdown, PairwiseMatch]],
            list[PairwiseMatch],
        ]
    ] = None,
    embeddings: Optional[dict[str, list[float]]] = None,
) -> dict[tuple[str, str], PairwiseMatch]:
    """
    Compute or ingest all pairwise match scores between reports.

    Validates every match score strictly. Uses match_reports() when pairwise
    score is not precomputed and full report objects are available.

    Returns:
        Dictionary mapping canonical (id_a, id_b) where id_a < id_b to PairwiseMatch.
    """
    report_map: dict[str, Any] = {}
    for r in reports:
        rid = _get_report_id(r)
        report_map[rid] = r

    report_ids = sorted(report_map.keys())
    score_lookup: dict[tuple[str, str], PairwiseMatch] = {}

    # 1. Ingest precomputed scores
    if precomputed_scores is not None:
        if isinstance(precomputed_scores, list):
            for pm in precomputed_scores:
                score = validate_match_score(pm.score)
                pair = _canonical_pair(pm.report_a_id, pm.report_b_id)
                if pair[0] == pair[1]:
                    continue
                score_lookup[pair] = PairwiseMatch(
                    report_a_id=pair[0],
                    report_b_id=pair[1],
                    score=score,
                    breakdown=pm.breakdown,
                )
        elif isinstance(precomputed_scores, dict):
            for (id_a, id_b), val in precomputed_scores.items():
                pair = _canonical_pair(id_a, id_b)
                if pair[0] == pair[1]:
                    continue
                if isinstance(val, PairwiseMatch):
                    score = validate_match_score(val.score)
                    score_lookup[pair] = PairwiseMatch(
                        report_a_id=pair[0],
                        report_b_id=pair[1],
                        score=score,
                        breakdown=val.breakdown,
                    )
                elif isinstance(val, MatchScoreBreakdown):
                    score = validate_match_score(val.final_score)
                    score_lookup[pair] = PairwiseMatch(
                        report_a_id=pair[0],
                        report_b_id=pair[1],
                        score=score,
                        breakdown=val,
                    )
                else:
                    score = validate_match_score(val)
                    score_lookup[pair] = PairwiseMatch(
                        report_a_id=pair[0],
                        report_b_id=pair[1],
                        score=score,
                        breakdown=None,
                    )

    # 2. Compute missing pairs via match_reports if reports are rich objects
    n = len(report_ids)
    for i in range(n):
        id1 = report_ids[i]
        r1 = report_map[id1]
        for j in range(i + 1, n):
            id2 = report_ids[j]
            pair = (id1, id2)
            if pair in score_lookup:
                continue

            r2 = report_map[id2]
            if isinstance(r1, str) or isinstance(r2, str):
                score_lookup[pair] = PairwiseMatch(
                    report_a_id=id1,
                    report_b_id=id2,
                    score=0.0,
                    breakdown=None,
                )
                continue

            emb1 = embeddings.get(id1) if embeddings else None
            emb2 = embeddings.get(id2) if embeddings else None

            breakdown = match_reports(r1, r2, embedding1=emb1, embedding2=emb2)
            score = validate_match_score(breakdown.final_score)
            score_lookup[pair] = PairwiseMatch(
                report_a_id=id1,
                report_b_id=id2,
                score=score,
                breakdown=breakdown,
            )

    return score_lookup


def get_pairwise_match(
    id_a: str,
    id_b: str,
    score_lookup: dict[tuple[str, str], PairwiseMatch],
) -> PairwiseMatch:
    """Retrieve PairwiseMatch for any two reports, returning score 1.0 for identical reports."""
    if id_a == id_b:
        return PairwiseMatch(report_a_id=id_a, report_b_id=id_b, score=1.0, breakdown=None)
    pair = _canonical_pair(id_a, id_b)
    if pair in score_lookup:
        return score_lookup[pair]
    return PairwiseMatch(report_a_id=pair[0], report_b_id=pair[1], score=0.0, breakdown=None)


# ---------------------------------------------------------------------------
# Complete-Linkage Compatibility & Tie-Breaking
# ---------------------------------------------------------------------------

def check_cluster_compatibility(
    report_id: str,
    cluster_member_ids: Sequence[str],
    score_lookup: dict[tuple[str, str], PairwiseMatch],
    threshold: float = CLUSTER_MERGE_THRESHOLD,
) -> tuple[bool, float, Optional[str], dict[str, float]]:
    """
    Check if a report is compatible with an existing cluster under complete-linkage.

    A report may join a cluster ONLY when its match score with EVERY existing
    cluster member is >= threshold (0.80).

    Returns:
        (is_compatible, min_score, blocking_member_id, scores_with_members)
    """
    if not cluster_member_ids:
        return True, 1.0, None, {}

    scores: dict[str, float] = {}
    min_score = 1.0
    blocking_member: Optional[str] = None

    for member_id in cluster_member_ids:
        pm = get_pairwise_match(report_id, member_id, score_lookup)
        scores[member_id] = pm.score
        if pm.score < min_score:
            min_score = pm.score
        if pm.score < threshold and blocking_member is None:
            blocking_member = member_id

    is_compatible = (blocking_member is None)
    return is_compatible, min_score, blocking_member, scores


def select_best_cluster(
    report_id: str,
    compatible_clusters: list[dict[str, Any]],
    score_lookup: dict[tuple[str, str], PairwiseMatch],
) -> dict[str, Any]:
    """
    Select the best cluster among multiple compatible candidates.

    Tie-breaking rule:
    1. Highest average compatibility score with current members.
    2. Lowest cluster_id lexicographically for deterministic tie-breaking.
    """
    scored_candidates = []
    for c in compatible_clusters:
        member_ids = c["report_ids"]
        avg_score = sum(
            get_pairwise_match(report_id, m, score_lookup).score for m in member_ids
        ) / len(member_ids)
        scored_candidates.append((-avg_score, c["cluster_id"], c))

    scored_candidates.sort(key=lambda item: (item[0], item[1]))
    return scored_candidates[0][2]


# ---------------------------------------------------------------------------
# Inter-Cluster Related Pairs
# ---------------------------------------------------------------------------

def compute_related_cluster_pairs(
    clusters: Sequence[ClusterResult],
    score_lookup: dict[tuple[str, str], PairwiseMatch],
    min_threshold: float = CLUSTER_RELATED_THRESHOLD,
    max_threshold: float = CLUSTER_MERGE_THRESHOLD,
) -> list[RelatedClusterPair]:
    """
    Find distinct cluster pairs with inter-cluster match score in [min_threshold, max_threshold).

    Only stores one direction (lower cluster ID first) to prevent duplicate reverse links.
    """
    related_pairs: list[RelatedClusterPair] = []
    num_clusters = len(clusters)

    for i in range(num_clusters):
        c_a = clusters[i]
        for j in range(i + 1, num_clusters):
            c_b = clusters[j]

            best_score = -1.0
            best_pair: Optional[tuple[str, str]] = None

            for r_a in c_a.report_ids:
                for r_b in c_b.report_ids:
                    pm = get_pairwise_match(r_a, r_b, score_lookup)
                    if pm.score > best_score:
                        best_score = pm.score
                        best_pair = (r_a, r_b)

            if min_threshold <= best_score < max_threshold:
                cid_a, cid_b = (
                    (c_a.cluster_id, c_b.cluster_id)
                    if c_a.cluster_id <= c_b.cluster_id
                    else (c_b.cluster_id, c_a.cluster_id)
                )
                related_pairs.append(
                    RelatedClusterPair(
                        cluster_a_id=cid_a,
                        cluster_b_id=cid_b,
                        score=best_score,
                        best_pair=best_pair,
                    )
                )

    return related_pairs


# ---------------------------------------------------------------------------
# Main Clustering Function
# ---------------------------------------------------------------------------

def cluster_reports(
    reports: Sequence[Any],
    precomputed_scores: Optional[
        Union[
            dict[tuple[str, str], Union[float, MatchScoreBreakdown, PairwiseMatch]],
            list[PairwiseMatch],
        ]
    ] = None,
    embeddings: Optional[dict[str, list[float]]] = None,
    merge_threshold: float = CLUSTER_MERGE_THRESHOLD,
    related_threshold: float = CLUSTER_RELATED_THRESHOLD,
) -> ClusteringOutput:
    """
    Perform safe incremental complete-linkage clustering on emergency reports.

    Invariants:
    1. Duplicate report IDs raise ValueError immediately.
    2. Invalid match scores raise ValueError immediately.
    3. Complete-linkage: A report joins a cluster ONLY if score >= merge_threshold (0.80)
       with EVERY existing member. Never uses connected components or transitive closure.
    4. Deterministic processing order and tie-breaking.
    5. Preserves complete pairwise evidence and membership decisions.
    6. Inter-cluster links in [related_threshold, merge_threshold) recorded as 'possibly related'.
    """
    if not reports:
        return ClusteringOutput(
            clusters=[],
            related_pairs=[],
            processing_log=["Empty input reports. 0 clusters formed."],
            total_reports=0,
            total_clusters=0,
        )

    # 1. Validate report IDs (duplicate check)
    validate_report_ids(reports)

    # 2. Sort reports deterministically by report ID
    sorted_reports = sorted(reports, key=_get_report_id)

    # 3. Compute/ingest all pairwise scores (validates every score)
    score_lookup = compute_all_pairwise_scores(
        sorted_reports,
        precomputed_scores=precomputed_scores,
        embeddings=embeddings,
    )

    # 4. Incremental Complete-Linkage Clustering
    raw_clusters: list[dict[str, Any]] = []
    log: list[str] = []

    for report in sorted_reports:
        rid = _get_report_id(report)

        # Check compatibility with all existing clusters
        compatible_clusters: list[dict[str, Any]] = []
        cluster_evaluations: list[
            tuple[dict[str, Any], bool, float, Optional[str], dict[str, float]]
        ] = []

        for c in raw_clusters:
            compatible, min_s, blocker, member_scores = check_cluster_compatibility(
                rid, c["report_ids"], score_lookup, threshold=merge_threshold
            )
            cluster_evaluations.append((c, compatible, min_s, blocker, member_scores))
            if compatible:
                compatible_clusters.append(c)

        if not compatible_clusters:
            new_cid = f"INC-{len(raw_clusters) + 1:03d}"
            decision = ClusterMembershipDecision(
                report_id=rid,
                action="new_cluster",
                cluster_id=new_cid,
                reason=(
                    f"Created new cluster. No existing cluster compatible under complete-linkage "
                    f"(score >= {merge_threshold} required with all members)."
                    if raw_clusters else "Initial cluster created."
                ),
                scores_with_members={},
            )
            raw_clusters.append({
                "cluster_id": new_cid,
                "report_ids": [rid],
                "membership_decisions": [decision],
            })
            log.append(f"Report '{rid}' created new cluster {new_cid}.")

        elif len(compatible_clusters) == 1:
            target_cluster = compatible_clusters[0]
            eval_info = next(
                e for e in cluster_evaluations if e[0]["cluster_id"] == target_cluster["cluster_id"]
            )
            decision = ClusterMembershipDecision(
                report_id=rid,
                action="join",
                cluster_id=target_cluster["cluster_id"],
                reason=(
                    f"Joined cluster {target_cluster['cluster_id']}: compatible with all "
                    f"{len(target_cluster['report_ids'])} members (min score: {eval_info[2]:.4f} >= {merge_threshold})."
                ),
                scores_with_members=eval_info[4],
            )
            target_cluster["report_ids"].append(rid)
            target_cluster["report_ids"].sort()
            target_cluster["membership_decisions"].append(decision)
            log.append(f"Report '{rid}' joined cluster {target_cluster['cluster_id']}.")

        else:
            best_cluster = select_best_cluster(rid, compatible_clusters, score_lookup)
            eval_info = next(
                e for e in cluster_evaluations if e[0]["cluster_id"] == best_cluster["cluster_id"]
            )
            avg_score = sum(eval_info[4].values()) / len(eval_info[4])
            decision = ClusterMembershipDecision(
                report_id=rid,
                action="join",
                cluster_id=best_cluster["cluster_id"],
                reason=(
                    f"Joined cluster {best_cluster['cluster_id']} via tie-breaking among "
                    f"{len(compatible_clusters)} compatible candidates with highest average compatibility "
                    f"({avg_score:.4f})."
                ),
                scores_with_members=eval_info[4],
            )
            best_cluster["report_ids"].append(rid)
            best_cluster["report_ids"].sort()
            best_cluster["membership_decisions"].append(decision)
            log.append(
                f"Report '{rid}' joined cluster {best_cluster['cluster_id']} "
                f"(selected from {len(compatible_clusters)} candidates)."
            )

    # 5. Build ClusterResult objects
    result_clusters: list[ClusterResult] = []
    for c in raw_clusters:
        rids = sorted(c["report_ids"])
        size = len(rids)
        c_matches: list[PairwiseMatch] = []
        scores_in_cluster: list[float] = []

        for i in range(size):
            for j in range(i + 1, size):
                pm = get_pairwise_match(rids[i], rids[j], score_lookup)
                c_matches.append(pm)
                scores_in_cluster.append(pm.score)

        min_score = min(scores_in_cluster) if scores_in_cluster else 1.0
        avg_score = sum(scores_in_cluster) / len(scores_in_cluster) if scores_in_cluster else 1.0

        result_clusters.append(
            ClusterResult(
                cluster_id=c["cluster_id"],
                report_ids=rids,
                size=size,
                min_pairwise_score=min_score,
                avg_pairwise_score=avg_score,
                pairwise_matches=c_matches,
                membership_decisions=c["membership_decisions"],
            )
        )

    # 6. Compute inter-cluster related pairs (0.60–0.79 band)
    related_pairs = compute_related_cluster_pairs(
        result_clusters,
        score_lookup,
        min_threshold=related_threshold,
        max_threshold=merge_threshold,
    )

    return ClusteringOutput(
        clusters=result_clusters,
        related_pairs=related_pairs,
        processing_log=log,
        total_reports=len(sorted_reports),
        total_clusters=len(result_clusters),
    )


# ---------------------------------------------------------------------------
# Database Persistence (Collision-Safe)
# ---------------------------------------------------------------------------

def _resolve_unique_incident_id(candidate_id: str, used_ids: set[str]) -> str:
    """
    Ensure an incident ID does not collide with existing or already-allocated IDs.

    If candidate_id is not in used_ids, it is used as-is.
    If it collides, finds the next available sequential ID with the same prefix format
    (e.g., INC-001 -> INC-002 -> INC-003).
    """
    if candidate_id not in used_ids:
        used_ids.add(candidate_id)
        return candidate_id

    match = re.match(r"^([A-Za-z]+-?)(\d+)$", candidate_id)
    prefix = match.group(1) if match else "INC-"
    digits = len(match.group(2)) if match else 3
    num = int(match.group(2)) if match else 1

    while True:
        num += 1
        new_id = f"{prefix}{num:0{digits}d}"
        if new_id not in used_ids:
            used_ids.add(new_id)
            return new_id


def persist_clustering_results(
    session: Any,
    output: ClusteringOutput,
    reports_by_id: Optional[dict[str, Any]] = None,
) -> list[Incident]:
    """
    Persist clustering results into existing Incident, IncidentReport,
    and RelatedIncident ORM models.

    Safely detects and resolves ID collisions if the database already contains
    incidents with matching IDs (e.g. INC-001), ensuring no integrity errors,
    no overwriting or corruption of existing records, and valid foreign key
    links across IncidentReport and RelatedIncident.

    Args:
        session: Active SQLAlchemy session.
        output: ClusteringOutput from cluster_reports().
        reports_by_id: Optional dictionary of report_id -> Report model/dict.

    Returns:
        List of created Incident ORM objects with their actual persisted IDs.
    """
    created_incidents: list[Incident] = []

    # 1. Discover all existing incident IDs in DB to prevent collisions
    used_ids: set[str] = set()
    try:
        from models import Incident
        for (existing_id,) in session.query(Incident.id).all():
            used_ids.add(existing_id)
    except Exception:
        pass

    try:
        from models import Incident
        for obj in session.new:
            if isinstance(obj, Incident) and getattr(obj, "id", None):
                used_ids.add(obj.id)
    except Exception:
        pass

    # 2. Map candidate cluster IDs to guaranteed-unique persisted IDs
    id_mapping: dict[str, str] = {}
    for c in output.clusters:
        persisted_id = _resolve_unique_incident_id(c.cluster_id, used_ids)
        id_mapping[c.cluster_id] = persisted_id

    # 3. Cache reports for location metadata if not provided
    if reports_by_id is None:
        reports_by_id = {}
        from models import Report
        try:
            all_rids = [rid for c in output.clusters for rid in c.report_ids]
            if all_rids:
                db_reports = session.query(Report).filter(Report.id.in_(all_rids)).all()
                for r in db_reports:
                    reports_by_id[r.id] = r
        except Exception:
            pass

    # 4. Create Incident and IncidentReport records
    for c in output.clusters:
        persisted_id = id_mapping[c.cluster_id]
        loc_resolved = None
        loc_lat = None
        loc_lon = None
        geo_conf = 0.0

        for rid in c.report_ids:
            rep = reports_by_id.get(rid)
            if rep:
                lr = getattr(rep, "location_resolved", None) or (
                    rep.get("location_resolved") if isinstance(rep, dict) else None
                )
                if lr:
                    loc_resolved = lr
                    loc_lat = getattr(rep, "location_lat", None) or (
                        rep.get("location_lat") if isinstance(rep, dict) else None
                    )
                    loc_lon = getattr(rep, "location_lon", None) or (
                        rep.get("location_lon") if isinstance(rep, dict) else None
                    )
                    geo_conf = getattr(rep, "geo_confidence", 0.0) or (
                        rep.get("geo_confidence", 0.0) if isinstance(rep, dict) else 0.0
                    )
                    break

        incident = Incident(
            id=persisted_id,
            status="unverified",
            title=f"Incident {persisted_id} ({c.size} reports)",
            location_resolved=loc_resolved,
            location_lat=loc_lat,
            location_lon=loc_lon,
            geo_confidence=geo_conf,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(incident)
        created_incidents.append(incident)

        for rid in c.report_ids:
            scores = [
                pm.score
                for pm in c.pairwise_matches
                if pm.report_a_id == rid or pm.report_b_id == rid
            ]
            rep_score = sum(scores) / len(scores) if scores else 1.0

            match_details_dict = {
                "pairwise_matches": [
                    {
                        "with": pm.report_b_id if pm.report_a_id == rid else pm.report_a_id,
                        "score": pm.score,
                    }
                    for pm in c.pairwise_matches
                    if pm.report_a_id == rid or pm.report_b_id == rid
                ]
            }

            link = IncidentReport(
                incident_id=persisted_id,
                report_id=rid,
                match_score=rep_score,
                match_details=json.dumps(match_details_dict),
            )
            session.add(link)

    # 5. Create RelatedIncident records with remapped IDs and canonical ordering
    seen_related_pairs: set[tuple[str, str]] = set()
    for rel in output.related_pairs:
        remapped_a = id_mapping.get(rel.cluster_a_id, rel.cluster_a_id)
        remapped_b = id_mapping.get(rel.cluster_b_id, rel.cluster_b_id)
        final_a, final_b = (
            (remapped_a, remapped_b)
            if remapped_a <= remapped_b
            else (remapped_b, remapped_a)
        )
        pair_key = (final_a, final_b)
        if pair_key not in seen_related_pairs:
            seen_related_pairs.add(pair_key)
            rel_link = RelatedIncident(
                incident_a_id=final_a,
                incident_b_id=final_b,
                score=rel.score,
            )
            session.add(rel_link)

    session.commit()
    return created_incidents
