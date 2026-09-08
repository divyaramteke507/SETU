"""
SETU Matcher Service — explainable 4-factor pairwise emergency report matching.

Combines:
  1. Semantic similarity   (weight = 0.40) via multilingual sentence embeddings
  2. Geographic proximity  (weight = 0.30) via Haversine distance on resolved locations
  3. Temporal proximity    (weight = 0.15) via normalized UTC timestamp decay
  4. Incident-type match   (weight = 0.15) via exact or configured related-pair matches

Produces an explainable match score with individual factor scores, weights,
and contributions.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Optional, Union

from config import (
    GEO_MAX_DISTANCE_M,
    GEO_SAME_LOCATION_SCORE,
    GEO_UNRESOLVED_SCORE,
    MATCH_WEIGHT_GEOGRAPHIC,
    MATCH_WEIGHT_INCIDENT_TYPE,
    MATCH_WEIGHT_SEMANTIC,
    MATCH_WEIGHT_TEMPORAL,
    RELATED_INCIDENT_TYPES,
    TEMPORAL_DECAY_MINUTES,
    TEMPORAL_FULL_SCORE_MINUTES,
)
from schemas import MatchFactor, MatchScoreBreakdown
from services.embedding_service import cosine_similarity, generate_embedding, deserialize_embedding
from services.location_resolver import validate_coordinates


# ---------------------------------------------------------------------------
# 1. Geographic Distance & Score
# ---------------------------------------------------------------------------

def calculate_geographic_distance(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """
    Calculate the great-circle distance between two points in meters using Haversine.

    Args:
        lat1, lon1: First point coordinates in degrees.
        lat2, lon2: Second point coordinates in degrees.

    Returns:
        Distance in meters.
    """
    R = 6371000.0  # Mean Earth radius in meters

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

    return R * c


def calculate_geographic_score(
    location_id1: Optional[str] = None,
    lat1: Optional[float] = None,
    lon1: Optional[float] = None,
    location_id2: Optional[str] = None,
    lat2: Optional[float] = None,
    lon2: Optional[float] = None,
    max_distance_m: float = GEO_MAX_DISTANCE_M,
) -> float:
    """
    Calculate geographic proximity score between two reports.

    Rules:
      - Same resolved location: 1.0
      - Two valid coordinates within max_distance_m: linear decay from 1.0 to 0.0
      - Beyond max_distance_m: 0.0
      - If either report has unresolved location / invalid coordinates: 0.0
    """
    # If either location lacks valid coordinates, cannot compute proximity
    if not validate_coordinates(lat1, lon1) or not validate_coordinates(lat2, lon2):
        return GEO_UNRESOLVED_SCORE

    lat1_f = float(lat1)  # type: ignore[arg-type]
    lon1_f = float(lon1)  # type: ignore[arg-type]
    lat2_f = float(lat2)  # type: ignore[arg-type]
    lon2_f = float(lon2)  # type: ignore[arg-type]

    # Same gazetteer location_id or exact same coordinates
    if location_id1 and location_id2 and location_id1 == location_id2:
        return GEO_SAME_LOCATION_SCORE

    if lat1_f == lat2_f and lon1_f == lon2_f:
        return GEO_SAME_LOCATION_SCORE

    dist_m = calculate_geographic_distance(lat1_f, lon1_f, lat2_f, lon2_f)

    if dist_m > max_distance_m:
        return 0.0

    # Linear decay: 1.0 at distance 0, 0.0 at max_distance_m
    score = 1.0 - (dist_m / max_distance_m)
    return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------------
# 2. Temporal Score
# ---------------------------------------------------------------------------

def _parse_timestamp_utc(ts: Any) -> Optional[datetime]:
    """Parse various timestamp representations into a UTC datetime object."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
    if isinstance(ts, str):
        cleaned = ts.strip()
        if not cleaned:
            return None
        try:
            # Handle ISO format strings
            dt = datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (ValueError, TypeError):
            return None
    return None


def calculate_temporal_score(
    time1: Any,
    time2: Any,
    full_score_minutes: float = TEMPORAL_FULL_SCORE_MINUTES,
    decay_minutes: float = TEMPORAL_DECAY_MINUTES,
) -> float:
    """
    Calculate temporal proximity score between two reports based on normalized UTC time.

    Rules:
      - <= full_score_minutes (5 min): 1.0
      - > full_score_minutes and <= decay_minutes (90 min): linear decay from 1.0 to 0.0
      - > decay_minutes: 0.0
      - Missing or unparseable timestamps: 0.0
    """
    dt1 = _parse_timestamp_utc(time1)
    dt2 = _parse_timestamp_utc(time2)

    if dt1 is None or dt2 is None:
        return 0.0

    delta_minutes = abs((dt1 - dt2).total_seconds()) / 60.0

    if delta_minutes <= full_score_minutes:
        return 1.0

    if delta_minutes > decay_minutes:
        return 0.0

    # Linear decay between full_score_minutes and decay_minutes
    decay_span = decay_minutes - full_score_minutes
    if decay_span <= 0.0:
        return 0.0

    score = 1.0 - ((delta_minutes - full_score_minutes) / decay_span)
    return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------------
# 3. Incident Type Score
# ---------------------------------------------------------------------------

def calculate_incident_type_score(
    type1: Optional[str],
    type2: Optional[str],
    related_types: Optional[set[frozenset[str]]] = None,
) -> float:
    """
    Calculate incident type relationship score.

    Rules:
      - Same incident type: 1.0
      - Configured related incident types: 0.5
      - Otherwise / missing: 0.0
    """
    if not type1 or not type2:
        return 0.0

    t1 = str(type1).strip().lower()
    t2 = str(type2).strip().lower()

    if not t1 or not t2:
        return 0.0

    if t1 == t2:
        return 1.0

    active_related = related_types if related_types is not None else RELATED_INCIDENT_TYPES
    pair = frozenset({t1, t2})
    if pair in active_related:
        return 0.5

    return 0.0


# ---------------------------------------------------------------------------
# 4. Explainable Match Score Combination
# ---------------------------------------------------------------------------

def calculate_match_score(
    semantic_score: float,
    geographic_score: float,
    temporal_score: float,
    incident_type_score: float,
    weight_semantic: float = MATCH_WEIGHT_SEMANTIC,
    weight_geographic: float = MATCH_WEIGHT_GEOGRAPHIC,
    weight_temporal: float = MATCH_WEIGHT_TEMPORAL,
    weight_incident_type: float = MATCH_WEIGHT_INCIDENT_TYPE,
) -> MatchScoreBreakdown:
    """
    Compute explainable match score from the 4 component factors.

    Formula:
      match_score =
          semantic_score      * 0.40
        + geographic_score    * 0.30
        + temporal_score      * 0.15
        + incident_type_score * 0.15

    Guarantees:
      - All weights sourced from config.py by default.
      - Full precision retained; no premature rounding.
      - Bounded strictly within [0.0, 1.0].
      - Returns complete explainability breakdown.
    """
    s_sem = max(0.0, min(1.0, float(semantic_score)))
    s_geo = max(0.0, min(1.0, float(geographic_score)))
    s_tmp = max(0.0, min(1.0, float(temporal_score)))
    s_typ = max(0.0, min(1.0, float(incident_type_score)))

    c_sem = s_sem * weight_semantic
    c_geo = s_geo * weight_geographic
    c_tmp = s_tmp * weight_temporal
    c_typ = s_typ * weight_incident_type

    final_score = max(0.0, min(1.0, c_sem + c_geo + c_tmp + c_typ))

    return MatchScoreBreakdown(
        semantic=MatchFactor(
            score=s_sem,
            weight=weight_semantic,
            contribution=c_sem,
        ),
        geographic=MatchFactor(
            score=s_geo,
            weight=weight_geographic,
            contribution=c_geo,
        ),
        temporal=MatchFactor(
            score=s_tmp,
            weight=weight_temporal,
            contribution=c_tmp,
        ),
        incident_type=MatchFactor(
            score=s_typ,
            weight=weight_incident_type,
            contribution=c_typ,
        ),
        final_score=final_score,
    )


# ---------------------------------------------------------------------------
# 5. High-Level Report Pair Matching
# ---------------------------------------------------------------------------

def _get_report_attr(report: Any, attr: str, default: Any = None) -> Any:
    """Extract an attribute from a dict or object safely."""
    if isinstance(report, dict):
        return report.get(attr, default)
    return getattr(report, attr, default)


def _get_report_incident_type(report: Any) -> Optional[str]:
    """Extract incident_type from a Report ORM object, dict, or extractions list."""
    # Direct field check
    direct = _get_report_attr(report, "incident_type")
    if direct is not None:
        if isinstance(direct, dict):
            return direct.get("value")
        return str(direct)

    # Check extractions
    extractions = _get_report_attr(report, "extractions")
    if extractions:
        for ext in extractions:
            f_name = _get_report_attr(ext, "field_name")
            if f_name == "incident_type":
                val = _get_report_attr(ext, "value")
                if val:
                    return str(val)
    return None


def match_reports(
    report1: Any,
    report2: Any,
    embedding1: Optional[list[float]] = None,
    embedding2: Optional[list[float]] = None,
) -> MatchScoreBreakdown:
    """
    Calculate the explainable match score between two reports.

    Extracts required fields (text embeddings, location, timestamps, incident type)
    from dicts or ORM models and evaluates all 4 scoring factors.

    Args:
        report1: First report (dict or Report model instance).
        report2: Second report (dict or Report model instance).
        embedding1: Optional pre-computed embedding vector for report1.
        embedding2: Optional pre-computed embedding vector for report2.

    Returns:
        MatchScoreBreakdown with factor scores, weights, contributions, and final score.
    """
    # 1. Semantic Score
    emb1 = embedding1
    if emb1 is None:
        stored = _get_report_attr(report1, "embedding")
        emb1 = deserialize_embedding(stored)
        if emb1 is None:
            text1 = _get_report_attr(report1, "normalized_text") or _get_report_attr(report1, "raw_text", "")
            emb1 = generate_embedding(text1)

    emb2 = embedding2
    if emb2 is None:
        stored = _get_report_attr(report2, "embedding")
        emb2 = deserialize_embedding(stored)
        if emb2 is None:
            text2 = _get_report_attr(report2, "normalized_text") or _get_report_attr(report2, "raw_text", "")
            emb2 = generate_embedding(text2)

    semantic_score = cosine_similarity(emb1, emb2)

    # 2. Geographic Score
    loc_id1 = _get_report_attr(report1, "location_resolved")
    loc_lat1 = _get_report_attr(report1, "location_lat")
    lat1 = loc_lat1 if loc_lat1 is not None else _get_report_attr(report1, "gps_lat")
    loc_lon1 = _get_report_attr(report1, "location_lon")
    lon1 = loc_lon1 if loc_lon1 is not None else _get_report_attr(report1, "gps_lon")

    loc_id2 = _get_report_attr(report2, "location_resolved")
    loc_lat2 = _get_report_attr(report2, "location_lat")
    lat2 = loc_lat2 if loc_lat2 is not None else _get_report_attr(report2, "gps_lat")
    loc_lon2 = _get_report_attr(report2, "location_lon")
    lon2 = loc_lon2 if loc_lon2 is not None else _get_report_attr(report2, "gps_lon")

    geographic_score = calculate_geographic_score(
        location_id1=loc_id1,
        lat1=lat1,
        lon1=lon1,
        location_id2=loc_id2,
        lat2=lat2,
        lon2=lon2,
    )

    # 3. Temporal Score
    time1 = _get_report_attr(report1, "received_at_utc") or _get_report_attr(report1, "received_at")
    time2 = _get_report_attr(report2, "received_at_utc") or _get_report_attr(report2, "received_at")
    temporal_score = calculate_temporal_score(time1, time2)

    # 4. Incident Type Score
    type1 = _get_report_incident_type(report1)
    type2 = _get_report_incident_type(report2)
    incident_type_score = calculate_incident_type_score(type1, type2)

    # 5. Combined Explainable Match Score
    return calculate_match_score(
        semantic_score=semantic_score,
        geographic_score=geographic_score,
        temporal_score=temporal_score,
        incident_type_score=incident_type_score,
    )
