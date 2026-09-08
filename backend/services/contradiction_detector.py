"""
SETU Contradiction Detector — Phase 6.

Surfaces contradictory evidence INSIDE candidate incidents and exposes
both sides of the conflict with evidence for human responder review.

Core Principles & Invariants:
1. Scoped strictly within candidate incidents: Only compares reports that
   belong to the same candidate incident/cluster.
2. No absence-as-negation: If Report A states a fact and Report B omits it,
   Report B does NOT imply negation. No contradiction is created.
3. No automatic resolution: Newly detected contradictions always have resolution=None.
   The responder remains the decision-maker.
4. Preserves source evidence: Both Side A and Side B retain their exact values
   and extracted evidence phrases from the source reports.
5. Canonical report pairs: Pairs are ordered (min(id_a, id_b), max(id_a, id_b))
   so R001 vs R007 and R007 vs R001 produce exactly one contradiction per field.
6. Typed contradictions:
   - numeric: Same people category only (trapped vs trapped; 2 vs 5). Different
     categories (trapped vs nearby) are never compared.
   - severity: Explicit severity concepts only (critical/severe vs minor).
   - incident_type: Explicitly incompatible types (from INCOMPATIBLE_INCIDENT_TYPES).
   - location: Distinct resolved gazetteer locations within the same incident.
     If either is unresolved, no contradiction is invented.
   - hazard_structural: Opposing physical states — structural integrity
     (damaged/leaning/collapsed vs intact/stable/no damage) and access/road-state
     (completely blocked vs passable/vehicles moving slowly).
7. Determinism: Same incident and reports always yield the exact same contradiction records.
8. Idempotent database persistence: Repeated persistence calls for the same incident
   never insert duplicate contradiction records.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional, Sequence, Union

from config import CONTRADICTION_TYPES, INCOMPATIBLE_INCIDENT_TYPES
from models import Contradiction, Extraction, Incident, Report
from schemas import (
    ContradictionOut,
    ContradictionOutput,
    ContradictionResult,
    ContradictionSide,
    ExtractionField,
)
from services.extractor import extract_report


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _get_report_attr(report: Any, attr: str, default: Any = None) -> Any:
    """Extract an attribute from a dict or object safely."""
    if isinstance(report, dict):
        return report.get(attr, default)
    return getattr(report, attr, default)


def _canonical_report_pair(rep_a: Any, rep_b: Any) -> tuple[Any, Any]:
    """Return report pair sorted deterministically by report ID (id_min, id_max)."""
    id_a = str(_get_report_attr(rep_a, "id", ""))
    id_b = str(_get_report_attr(rep_b, "id", ""))
    return (rep_a, rep_b) if id_a <= id_b else (rep_b, rep_a)


def _format_evidence(evidence: Any) -> str:
    """Format evidence into a clean string representation."""
    if isinstance(evidence, list):
        return "; ".join(str(e) for e in evidence if e)
    if evidence is None:
        return ""
    return str(evidence)


def _get_report_extractions(
    report: Any,
    provided_extractions: Optional[Sequence[Any]] = None,
) -> list[ExtractionField]:
    """
    Retrieve or compute structured extraction fields for a report.

    Consumes existing Extraction records if available, or invokes extract_report().
    """
    if provided_extractions is not None:
        result = []
        for ext in provided_extractions:
            if isinstance(ext, ExtractionField):
                result.append(ext)
            elif isinstance(ext, dict):
                result.append(ExtractionField(**ext))
            else:
                # ORM model
                val = getattr(ext, "value", None)
                ev = getattr(ext, "evidence", [])
                if isinstance(ev, str):
                    try:
                        ev = json.loads(ev)
                    except Exception:
                        ev = [ev] if ev else []
                result.append(
                    ExtractionField(
                        field_name=getattr(ext, "field_name", ""),
                        value=val,
                        confidence=getattr(ext, "confidence", 0.0),
                        evidence=ev if isinstance(ev, list) else [str(ev)],
                    )
                )
        return result

    # Check report object's own extractions
    attached = _get_report_attr(report, "extractions")
    if attached:
        return _get_report_extractions(report, provided_extractions=attached)

    # Compute via Phase 2 extract_report
    norm_text = _get_report_attr(report, "normalized_text") or _get_report_attr(report, "raw_text", "")
    lang = _get_report_attr(report, "language", "en")
    source = _get_report_attr(report, "source", "")
    return extract_report(norm_text, language=lang, source=source)


# ============================================================================
# TYPED CONTRADICTION DETECTORS
# ============================================================================

def check_numeric_contradictions(
    rep_a_id: str,
    rep_b_id: str,
    extractions_a: Sequence[ExtractionField],
    extractions_b: Sequence[ExtractionField],
    incident_id: str = "INC-UNKNOWN",
) -> list[ContradictionResult]:
    """
    Detect NUMERIC contradictions between two reports within the same incident.

    Safety rules:
    - Compare counts ONLY when the categories are identical (e.g. trapped vs trapped).
    - Different categories (trapped vs nearby vs affected) are NEVER compared.
    - Handles exact counts vs ranges properly (overlapping ranges are NOT contradictory).
    - Absence of count in one report is NOT evidence of zero (no contradiction).
    """
    contradictions: list[ContradictionResult] = []

    # Extract all people_estimate fields
    people_a = [e for e in extractions_a if e.field_name == "people_estimate" and e.value is not None]
    people_b = [e for e in extractions_b if e.field_name == "people_estimate" and e.value is not None]

    for ext_a in people_a:
        val_a = ext_a.value
        if isinstance(val_a, str):
            try:
                val_a = json.loads(val_a)
            except Exception:
                continue
        if not isinstance(val_a, dict):
            continue

        cat_a = val_a.get("category")
        if not cat_a:
            continue

        for ext_b in people_b:
            val_b = ext_b.value
            if isinstance(val_b, str):
                try:
                    val_b = json.loads(val_b)
                except Exception:
                    continue
            if not isinstance(val_b, dict):
                continue

            cat_b = val_b.get("category")

            # Invariant: Different categories are NEVER contradictory
            if cat_a != cat_b:
                continue

            # Extract numeric counts or ranges
            count_a = val_a.get("count")
            min_a = val_a.get("count_min")
            max_a = val_a.get("count_max")

            count_b = val_b.get("count")
            min_b = val_b.get("count_min")
            max_b = val_b.get("count_max")

            is_conflict = False
            details = ""

            # Case 1: Both exact counts
            if count_a is not None and count_b is not None:
                if count_a != count_b:
                    is_conflict = True
                    details = f"Exact counts differ: {count_a} vs {count_b} for category '{cat_a}'"

            # Case 2: A is exact, B is range
            elif count_a is not None and min_b is not None and max_b is not None:
                if count_a < min_b or count_a > max_b:
                    is_conflict = True
                    details = f"Count {count_a} is outside range [{min_b}, {max_b}] for category '{cat_a}'"

            # Case 3: A is range, B is exact
            elif count_b is not None and min_a is not None and max_a is not None:
                if count_b < min_a or count_b > max_a:
                    is_conflict = True
                    details = f"Count {count_b} is outside range [{min_a}, {max_a}] for category '{cat_a}'"

            # Case 4: Both are ranges
            elif min_a is not None and max_a is not None and min_b is not None and max_b is not None:
                if max_a < min_b or max_b < min_a:
                    is_conflict = True
                    details = f"Non-overlapping ranges [{min_a}, {max_a}] vs [{min_b}, {max_b}] for category '{cat_a}'"

            if is_conflict:
                cid = f"CONTR-{incident_id}-{rep_a_id}-{rep_b_id}-NUM-{cat_a}"
                contradictions.append(
                    ContradictionResult(
                        id=cid,
                        incident_id=incident_id,
                        contradiction_type="numeric",
                        field=f"people_{cat_a}",
                        side_a=ContradictionSide(
                            report_id=rep_a_id,
                            value=val_a,
                            evidence=ext_a.evidence,
                        ),
                        side_b=ContradictionSide(
                            report_id=rep_b_id,
                            value=val_b,
                            evidence=ext_b.evidence,
                        ),
                        resolution=None,
                        explanation=details,
                    )
                )

    return contradictions


def check_severity_contradictions(
    rep_a_id: str,
    rep_b_id: str,
    extractions_a: Sequence[ExtractionField],
    extractions_b: Sequence[ExtractionField],
    incident_id: str = "INC-UNKNOWN",
) -> list[ContradictionResult]:
    """
    Detect SEVERITY contradictions between two reports within the same incident.

    Semantic rule:
    - Compares explicit severity concepts (critical/severe vs minor).
    - Road blockage / passability is NOT severity; it is handled under hazard_structural.
    - If either report lacks an explicit severity hint, no contradiction is created.
    """
    contradictions: list[ContradictionResult] = []

    sev_a_field = next((e for e in extractions_a if e.field_name == "severity_hint"), None)
    sev_b_field = next((e for e in extractions_b if e.field_name == "severity_hint"), None)

    if not sev_a_field or not sev_b_field:
        return contradictions

    sev_a = str(sev_a_field.value).lower() if sev_a_field.value else None
    sev_b = str(sev_b_field.value).lower() if sev_b_field.value else None

    if not sev_a or not sev_b or sev_a == sev_b:
        return contradictions

    high_severity = {"critical", "severe"}
    low_severity = {"minor"}

    is_conflict = False
    if (sev_a in high_severity and sev_b in low_severity) or (sev_b in high_severity and sev_a in low_severity):
        is_conflict = True

    if is_conflict:
        cid = f"CONTR-{incident_id}-{rep_a_id}-{rep_b_id}-SEV"
        contradictions.append(
            ContradictionResult(
                id=cid,
                incident_id=incident_id,
                contradiction_type="severity",
                field="severity_hint",
                side_a=ContradictionSide(
                    report_id=rep_a_id,
                    value=sev_a,
                    evidence=sev_a_field.evidence,
                ),
                side_b=ContradictionSide(
                    report_id=rep_b_id,
                    value=sev_b,
                    evidence=sev_b_field.evidence,
                ),
                resolution=None,
                explanation=f"Explicit severity conflict: '{sev_a}' vs '{sev_b}'",
            )
        )

    return contradictions


def check_incident_type_contradictions(
    rep_a_id: str,
    rep_b_id: str,
    extractions_a: Sequence[ExtractionField],
    extractions_b: Sequence[ExtractionField],
    incident_id: str = "INC-UNKNOWN",
) -> list[ContradictionResult]:
    """
    Detect INCIDENT_TYPE contradictions between two reports within the same incident.

    Semantic rule:
    - Flags only types that are explicitly mutually incompatible (from INCOMPATIBLE_INCIDENT_TYPES).
    - Different co-occurring disaster types (e.g. flood and road_blocked) are NOT contradictory.
    """
    contradictions: list[ContradictionResult] = []

    type_a_field = next((e for e in extractions_a if e.field_name == "incident_type"), None)
    type_b_field = next((e for e in extractions_b if e.field_name == "incident_type"), None)

    if not type_a_field or not type_b_field:
        return contradictions

    type_a = str(type_a_field.value).lower() if type_a_field.value else None
    type_b = str(type_b_field.value).lower() if type_b_field.value else None

    if not type_a or not type_b or type_a == type_b:
        return contradictions

    # Check deterministic incompatibility mapping
    pair = frozenset({type_a, type_b})
    if pair in INCOMPATIBLE_INCIDENT_TYPES:
        cid = f"CONTR-{incident_id}-{rep_a_id}-{rep_b_id}-TYPE"
        contradictions.append(
            ContradictionResult(
                id=cid,
                incident_id=incident_id,
                contradiction_type="incident_type",
                field="incident_type",
                side_a=ContradictionSide(
                    report_id=rep_a_id,
                    value=type_a,
                    evidence=type_a_field.evidence,
                ),
                side_b=ContradictionSide(
                    report_id=rep_b_id,
                    value=type_b,
                    evidence=type_b_field.evidence,
                ),
                resolution=None,
                explanation=f"Mutually exclusive incident types: '{type_a}' vs '{type_b}'",
            )
        )

    return contradictions


def check_location_contradictions(
    rep_a: Any,
    rep_b: Any,
    incident_id: str = "INC-UNKNOWN",
) -> list[ContradictionResult]:
    """
    Detect LOCATION contradictions between two reports within the same incident.

    Semantic rule:
    - Flagged ONLY when both reports have resolved, distinct gazetteer locations
      within the same candidate incident.
    - If either location is unresolved, NO contradiction is created.
    - Never invents coordinates or conflicts from vague text.
    """
    contradictions: list[ContradictionResult] = []

    rep_a_id = str(_get_report_attr(rep_a, "id", ""))
    rep_b_id = str(_get_report_attr(rep_b, "id", ""))

    loc_a = _get_report_attr(rep_a, "location_resolved")
    loc_b = _get_report_attr(rep_b, "location_resolved")

    # Invariant: If either location is unresolved, do NOT invent a contradiction
    if not loc_a or not loc_b:
        return contradictions

    loc_a_str = str(loc_a).strip().lower()
    loc_b_str = str(loc_b).strip().lower()

    if loc_a_str != loc_b_str:
        cid = f"CONTR-{incident_id}-{rep_a_id}-{rep_b_id}-LOC"
        raw_a = _get_report_attr(rep_a, "raw_text", "")
        raw_b = _get_report_attr(rep_b, "raw_text", "")

        contradictions.append(
            ContradictionResult(
                id=cid,
                incident_id=incident_id,
                contradiction_type="location",
                field="location_resolved",
                side_a=ContradictionSide(
                    report_id=rep_a_id,
                    value=loc_a,
                    evidence=[loc_a] if not raw_a else [loc_a],
                ),
                side_b=ContradictionSide(
                    report_id=rep_b_id,
                    value=loc_b,
                    evidence=[loc_b] if not raw_b else [loc_b],
                ),
                resolution=None,
                explanation=f"Conflicting resolved locations in same incident: '{loc_a}' vs '{loc_b}'",
            )
        )

    return contradictions


# Patterns for structural integrity opposing claims
_STRUCTURAL_DAMAGED_RE = re.compile(
    r"\b(?:collapsed?|leaning|tilted?|showing\s+(?:significant\s+)?cracks?|crack(?:ed|ing)?|"
    r"risk\s+of\s+collapse|tooti|tedi|gir\s+jayegi|damaged?\s+boundary\s+wall)\b",
    re.IGNORECASE,
)
_STRUCTURAL_INTACT_RE = re.compile(
    r"\b(?:fully\s+intact|appears\s+fully\s+intact|intact|no\s+visible\s+(?:cracks?|damage)|"
    r"structure\s+appears\s+stable|building\s+structure\s+appears\s+stable|no\s+structural\s+damage)\b",
    re.IGNORECASE,
)

# Patterns for access/road-state opposing claims (handled under HAZARD_STRUCTURAL)
_ROAD_BLOCKED_RE = re.compile(
    r"\b(?:completely\s+blocked|road\s+(?:is\s+)?(?:completely\s+)?blocked|traffic\s+at\s+standstill|"
    r"सड़क\s*बंद|road\s+block(?:ed)?|sadak\s+band)\b",
    re.IGNORECASE,
)
_ROAD_PASSABLE_RE = re.compile(
    r"\b(?:vehicles?\s+can\s+pass(?:\s+slowly)?|vehicles?\s+passing\s+slowly|slowly\s+passable|"
    r"can\s+pass\s+slowly|passable\s+on\s+side\s+road)\b",
    re.IGNORECASE,
)


def check_hazard_structural_contradictions(
    rep_a: Any,
    rep_b: Any,
    extractions_a: Sequence[ExtractionField],
    extractions_b: Sequence[ExtractionField],
    incident_id: str = "INC-UNKNOWN",
) -> list[ContradictionResult]:
    """
    Detect HAZARD_STRUCTURAL contradictions between two reports within the same incident.

    Semantic rule:
    Detects explicit opposing physical state claims:
    1. Structural integrity: Damaged / leaning / collapse risk vs Intact / stable / no damage.
    2. Access / Road-state: Completely blocked / standstill vs Passable / vehicles passing slowly.
    Absence of a mention is NEVER treated as opposite.
    """
    contradictions: list[ContradictionResult] = []

    rep_a_id = str(_get_report_attr(rep_a, "id", ""))
    rep_b_id = str(_get_report_attr(rep_b, "id", ""))

    text_a = _get_report_attr(rep_a, "normalized_text") or _get_report_attr(rep_a, "raw_text", "")
    text_b = _get_report_attr(rep_b, "normalized_text") or _get_report_attr(rep_b, "raw_text", "")

    # 1. Structural Integrity Check
    m_dmg_a = _STRUCTURAL_DAMAGED_RE.search(text_a)
    m_dmg_b = _STRUCTURAL_DAMAGED_RE.search(text_b)
    m_int_a = _STRUCTURAL_INTACT_RE.search(text_a)
    m_int_b = _STRUCTURAL_INTACT_RE.search(text_b)

    structural_conflict = False
    side_a_val, side_b_val = "", ""
    side_a_ev, side_b_ev = [], []

    if m_dmg_a and m_int_b:
        structural_conflict = True
        side_a_val = "damaged_or_collapse_risk"
        side_a_ev = [m_dmg_a.group(0).strip()]
        side_b_val = "intact_or_stable"
        side_b_ev = [m_int_b.group(0).strip()]
    elif m_int_a and m_dmg_b:
        structural_conflict = True
        side_a_val = "intact_or_stable"
        side_a_ev = [m_int_a.group(0).strip()]
        side_b_val = "damaged_or_collapse_risk"
        side_b_ev = [m_dmg_b.group(0).strip()]

    if structural_conflict:
        cid = f"CONTR-{incident_id}-{rep_a_id}-{rep_b_id}-HAZ-STRUCT"
        contradictions.append(
            ContradictionResult(
                id=cid,
                incident_id=incident_id,
                contradiction_type="hazard_structural",
                field="structural_integrity",
                side_a=ContradictionSide(
                    report_id=rep_a_id,
                    value=side_a_val,
                    evidence=side_a_ev,
                ),
                side_b=ContradictionSide(
                    report_id=rep_b_id,
                    value=side_b_val,
                    evidence=side_b_ev,
                ),
                resolution=None,
                explanation=f"Explicit structural integrity conflict: '{side_a_val}' vs '{side_b_val}'",
            )
        )

    # 2. Road-state / Access Check
    m_blk_a = _ROAD_BLOCKED_RE.search(text_a)
    m_blk_b = _ROAD_BLOCKED_RE.search(text_b)
    m_pas_a = _ROAD_PASSABLE_RE.search(text_a)
    m_pas_b = _ROAD_PASSABLE_RE.search(text_b)

    access_conflict = False
    side_a_acc_val, side_b_acc_val = "", ""
    side_a_acc_ev, side_b_acc_ev = [], []

    if m_blk_a and m_pas_b:
        access_conflict = True
        side_a_acc_val = "completely_blocked"
        side_a_acc_ev = [m_blk_a.group(0).strip()]
        side_b_acc_val = "passable_slowly"
        side_b_acc_ev = [m_pas_b.group(0).strip()]
    elif m_pas_a and m_blk_b:
        access_conflict = True
        side_a_acc_val = "passable_slowly"
        side_a_acc_ev = [m_pas_a.group(0).strip()]
        side_b_acc_val = "completely_blocked"
        side_b_acc_ev = [m_blk_b.group(0).strip()]

    if access_conflict:
        cid = f"CONTR-{incident_id}-{rep_a_id}-{rep_b_id}-HAZ-ACCESS"
        contradictions.append(
            ContradictionResult(
                id=cid,
                incident_id=incident_id,
                contradiction_type="hazard_structural",
                field="road_access",
                side_a=ContradictionSide(
                    report_id=rep_a_id,
                    value=side_a_acc_val,
                    evidence=side_a_acc_ev,
                ),
                side_b=ContradictionSide(
                    report_id=rep_b_id,
                    value=side_b_acc_val,
                    evidence=side_b_acc_ev,
                ),
                resolution=None,
                explanation=f"Explicit road access conflict: '{side_a_acc_val}' vs '{side_b_acc_val}'",
            )
        )

    return contradictions


# ============================================================================
# HIGH-LEVEL CONTRADICTION DETECTION ENTRY POINTS
# ============================================================================

def detect_pairwise_contradictions(
    rep_a: Any,
    rep_b: Any,
    extractions_a: Optional[Sequence[Any]] = None,
    extractions_b: Optional[Sequence[Any]] = None,
    incident_id: str = "INC-UNKNOWN",
) -> list[ContradictionResult]:
    """
    Evaluate all 5 typed contradiction categories between two reports within the same incident.

    Report pair is canonicalized (id_min, id_max) to prevent duplicate reverse records.
    """
    # Canonicalize report pair by ID
    rep_1, rep_2 = _canonical_report_pair(rep_a, rep_b)
    rep_1_id = str(_get_report_attr(rep_1, "id", ""))
    rep_2_id = str(_get_report_attr(rep_2, "id", ""))

    if rep_1_id == rep_2_id:
        return []

    # Align extractions with canonical order
    if rep_1 is rep_a:
        exts_1 = _get_report_extractions(rep_1, extractions_a)
        exts_2 = _get_report_extractions(rep_2, extractions_b)
    else:
        exts_1 = _get_report_extractions(rep_1, extractions_b)
        exts_2 = _get_report_extractions(rep_2, extractions_a)

    contradictions: list[ContradictionResult] = []

    # 1. Numeric
    contradictions.extend(
        check_numeric_contradictions(rep_1_id, rep_2_id, exts_1, exts_2, incident_id=incident_id)
    )

    # 2. Severity
    contradictions.extend(
        check_severity_contradictions(rep_1_id, rep_2_id, exts_1, exts_2, incident_id=incident_id)
    )

    # 3. Incident Type
    contradictions.extend(
        check_incident_type_contradictions(rep_1_id, rep_2_id, exts_1, exts_2, incident_id=incident_id)
    )

    # 4. Location
    contradictions.extend(
        check_location_contradictions(rep_1, rep_2, incident_id=incident_id)
    )

    # 5. Hazard / Structural (Structural integrity + road access)
    contradictions.extend(
        check_hazard_structural_contradictions(rep_1, rep_2, exts_1, exts_2, incident_id=incident_id)
    )

    return contradictions


def detect_incident_contradictions(
    incident_id: str,
    reports: Sequence[Any],
    extractions_map: Optional[dict[str, list[Any]]] = None,
) -> ContradictionOutput:
    """
    Detect all contradictions among reports belonging to a single candidate incident.

    Reports are compared pairwise in deterministic order.
    """
    if not reports or len(reports) < 2:
        return ContradictionOutput(incident_id=incident_id, contradictions=[], total_contradictions=0)

    # Deterministically sort reports by ID
    sorted_reps = sorted(reports, key=lambda r: str(_get_report_attr(r, "id", "")))
    all_contradictions: list[ContradictionResult] = []
    seen_keys: set[tuple[str, str, str, str]] = set()

    n = len(sorted_reps)
    for i in range(n):
        rep_i = sorted_reps[i]
        id_i = str(_get_report_attr(rep_i, "id", ""))
        exts_i = extractions_map.get(id_i) if extractions_map else None

        for j in range(i + 1, n):
            rep_j = sorted_reps[j]
            id_j = str(_get_report_attr(rep_j, "id", ""))
            exts_j = extractions_map.get(id_j) if extractions_map else None

            pair_contrs = detect_pairwise_contradictions(
                rep_i, rep_j, extractions_a=exts_i, extractions_b=exts_j, incident_id=incident_id
            )

            for c in pair_contrs:
                dedup_key = (c.contradiction_type, c.field, c.side_a.report_id, c.side_b.report_id)
                if dedup_key not in seen_keys:
                    seen_keys.add(dedup_key)
                    all_contradictions.append(c)

    return ContradictionOutput(
        incident_id=incident_id,
        contradictions=all_contradictions,
        total_contradictions=len(all_contradictions),
    )


def detect_all_contradictions(
    incidents_with_reports: Sequence[tuple[str, Sequence[Any]]],
    extractions_map: Optional[dict[str, list[Any]]] = None,
) -> list[ContradictionOutput]:
    """
    Detect contradictions across multiple candidate incidents.

    Enforces incident isolation: reports in different incidents are never compared.
    """
    outputs: list[ContradictionOutput] = []
    for inc_id, reps in incidents_with_reports:
        out = detect_incident_contradictions(inc_id, reps, extractions_map=extractions_map)
        outputs.append(out)
    return outputs


# ============================================================================
# DATABASE PERSISTENCE
# ============================================================================

def persist_contradictions(
    session: Any,
    contradictions_or_outputs: Sequence[Union[ContradictionResult, ContradictionOut, ContradictionOutput]],
) -> list[Contradiction]:
    """
    Persist contradiction records into the existing Contradiction ORM model.

    Idempotent: Prevents duplicate records if called repeatedly for the same
    incident and report pair.
    All persisted contradictions maintain resolution=None.
    """
    # Normalize input into a list of items
    if isinstance(contradictions_or_outputs, (ContradictionOutput, ContradictionResult, ContradictionOut)):
        items = [contradictions_or_outputs]
    elif isinstance(contradictions_or_outputs, (list, tuple, set)):
        items = list(contradictions_or_outputs)
    else:
        items = [contradictions_or_outputs]

    # Flatten outputs into individual results
    flat_results: list[Union[ContradictionResult, ContradictionOut]] = []
    for item in items:
        if isinstance(item, ContradictionOutput):
            flat_results.extend(item.contradictions)
        else:
            flat_results.append(item)

    if not flat_results:
        return []

    # 1. Discover existing contradictions in DB to ensure idempotency
    existing_keys: set[tuple[str, str, str, str, str]] = set()
    try:
        db_rows = session.query(
            Contradiction.incident_id,
            Contradiction.contradiction_type,
            Contradiction.field,
            Contradiction.side_a_report_id,
            Contradiction.side_b_report_id,
        ).all()
        for r in db_rows:
            existing_keys.add((r[0], r[1], r[2], r[3], r[4]))
    except Exception:
        pass

    # Check pending in session.new
    try:
        for obj in session.new:
            if isinstance(obj, Contradiction):
                existing_keys.add(
                    (obj.incident_id, obj.contradiction_type, obj.field, obj.side_a_report_id, obj.side_b_report_id)
                )
    except Exception:
        pass

    created: list[Contradiction] = []
    seen_in_batch: set[tuple[str, str, str, str, str]] = set()

    for item in flat_results:
        inc_id = item.incident_id
        c_type = item.contradiction_type
        c_field = item.field
        side_a_id = item.side_a.report_id
        side_b_id = item.side_b.report_id

        key = (inc_id, c_type, c_field, side_a_id, side_b_id)
        if key in existing_keys or key in seen_in_batch:
            continue
        seen_in_batch.add(key)

        val_a = item.side_a.value
        val_b = item.side_b.value
        str_val_a = json.dumps(val_a) if isinstance(val_a, (dict, list)) else (str(val_a) if val_a is not None else None)
        str_val_b = json.dumps(val_b) if isinstance(val_b, (dict, list)) else (str(val_b) if val_b is not None else None)

        ev_a = item.side_a.evidence
        ev_b = item.side_b.evidence
        str_ev_a = json.dumps(ev_a) if isinstance(ev_a, list) else (str(ev_a) if ev_a is not None else None)
        str_ev_b = json.dumps(ev_b) if isinstance(ev_b, list) else (str(ev_b) if ev_b is not None else None)

        record = Contradiction(
            id=item.id,
            incident_id=inc_id,
            contradiction_type=c_type,
            field=c_field,
            side_a_report_id=side_a_id,
            side_a_value=str_val_a,
            side_a_evidence=str_ev_a,
            side_b_report_id=side_b_id,
            side_b_value=str_val_b,
            side_b_evidence=str_ev_b,
            resolution=None,
        )
        session.add(record)
        created.append(record)

    if created:
        session.commit()

    return created
