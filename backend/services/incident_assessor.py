"""
SETU Incident Assessment Engine — Phase 7.

Deterministic, explainable calculation of:
1. SEVERITY (0–100): Physical danger and hazard scale.
2. CONFIDENCE (0.00–1.00): Evidence strength across 5 explainable components.
3. PRIORITY (0–100): Urgency of responder attention.

Safety Invariants:
- SETU does NOT make operational decisions. Responders remain the sole authority.
- Report count and source diversity belong strictly to CONFIDENCE, not SEVERITY.
  Five duplicate low-quality reports will never artificially increase danger.
- Contradictions reduce CONFIDENCE (consistency component), NOT SEVERITY.
  Contested severe incidents remain high severity with lower confidence.
- People categories are strictly isolated: only 'trapped' triggers the trapped modifier.
  'affected', 'nearby', and 'at_risk' do NOT trigger trapped modifiers.
- Qualitative facts (e.g. 'several people trapped') are preserved without inventing numbers.
- Low-confidence safety cap: if confidence < 0.30, priority <= 69 (remains <= Medium).
- Urgency bands:
    critical: 85 <= priority <= 100
    high:     70 <= priority < 85
    medium:   40 <= priority < 70
    low:       0 <= priority < 40
- All calculations are 100% deterministic, without random numbers, external APIs, or LLMs.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional, Sequence, Union

from config import (
    CONFIDENCE_CONSISTENCY_POLICY,
    CONFIDENCE_INFO_TYPE_POLICY,
    CONFIDENCE_SOURCE_COUNT_POLICY,
    CONFIDENCE_SOURCE_DIVERSITY_POLICY,
    CONFIDENCE_WEIGHT_CONSISTENCY,
    CONFIDENCE_WEIGHT_EXTRACTION,
    CONFIDENCE_WEIGHT_INFO_TYPE,
    CONFIDENCE_WEIGHT_SOURCE_COUNT,
    CONFIDENCE_WEIGHT_SOURCE_DIVERSITY,
    PRIORITY_FORMULA_BASE,
    PRIORITY_FORMULA_CONFIDENCE_FACTOR,
    PRIORITY_LOW_CONFIDENCE_CAP,
    PRIORITY_LOW_CONFIDENCE_THRESHOLD,
    SEVERITY_KEYWORD_MAP,
    SEVERITY_MAX,
    SEVERITY_TRAPPED_BONUS,
    SEVERITY_TYPE_BASE,
    SEVERITY_VULNERABLE_BONUS,
    URGENCY_BANDS,
)
from models import Contradiction, Incident, Report
from schemas import (
    ConfidenceBreakdown,
    ConfidenceComponent,
    ContradictionOutput,
    ExtractionField,
    IncidentAssessment,
    PriorityBreakdown,
    SeverityBreakdown,
    SeverityModifier,
)
from services.embedding_service import (
    cosine_similarity,
    deserialize_embedding,
    generate_embedding,
)
from services.extractor import extract_report
from services.normalizer import normalize_text


# ---------------------------------------------------------------------------
# Extraction & Attribute Helpers
# ---------------------------------------------------------------------------

def _get_report_attr(report: Any, attr: str, default: Any = None) -> Any:
    """Safely extract an attribute or dict key from a Report object/dict."""
    if isinstance(report, dict):
        return report.get(attr, default)
    return getattr(report, attr, default)


def _get_report_extractions(
    report: Any,
    provided_extractions: Optional[Sequence[Any]] = None,
) -> list[ExtractionField]:
    """Retrieve or compute structured extractions for a single report."""
    if provided_extractions is not None:
        result: list[ExtractionField] = []
        for e in provided_extractions:
            if isinstance(e, ExtractionField):
                result.append(e)
            elif isinstance(e, dict):
                result.append(ExtractionField(**e))
            elif hasattr(e, "field_name"):
                result.append(
                    ExtractionField(
                        field_name=e.field_name,
                        value=getattr(e, "value", None),
                        confidence=getattr(e, "confidence", 0.0),
                        evidence=getattr(e, "evidence", []) or [],
                    )
                )
        return result

    # Check report object's own extractions
    attached = _get_report_attr(report, "extractions")
    if attached:
        return _get_report_extractions(report, provided_extractions=attached)

    # Compute via Phase 2 extract_report
    norm_text = _get_report_attr(report, "normalized_text") or normalize_text(
        _get_report_attr(report, "raw_text", "")
    )
    lang = _get_report_attr(report, "language", "en")
    source = _get_report_attr(report, "source", "")
    return extract_report(norm_text, language=lang, source=source)


# ---------------------------------------------------------------------------
# Vulnerability & Trapped Validation Helpers
# ---------------------------------------------------------------------------

# Specific vulnerability signals (ignoring generic family/resident/person)
_VULNERABLE_REGEX = re.compile(
    r"\b(child|children|kids?|infant|baby|babies|elderly|old\s+man|old\s+woman|"
    r"old\s+people|old\s+person|disabled|handicapped|pregnant|बच्चे|बुज़ुर्ग|बूढ़|"
    r"bachch\w*|buzu?rg)\b",
    re.IGNORECASE,
)

# Specific trapped/stranded signals (ignoring generic rescue needed or ordinary English words like 'phase')
_TRAPPED_REGEX = re.compile(
    r"\b(trapped|stuck|stranded|phaas|log\s+phase|phase\s+hain|फंस)\b",
    re.IGNORECASE,
)


def _contains_actual_vulnerability(evidence_list: list[str]) -> bool:
    """Verify that evidence contains specific vulnerable person terms."""
    for ev in evidence_list:
        if _VULNERABLE_REGEX.search(ev):
            return True
    return False


def _normalize_contradictions(contradictions: Optional[Union[Sequence[Any], Any]]) -> list[Any]:
    """
    Extract and deduplicate logical contradictions so neither both sides
    nor duplicate representations of the same conflict are double-counted.
    """
    if not contradictions:
        return []

    # If ContradictionOutput or container with .contradictions passed directly
    if isinstance(contradictions, ContradictionOutput):
        raw_items = contradictions.contradictions
    elif hasattr(contradictions, "contradictions"):
        raw_items = getattr(contradictions, "contradictions")
    elif isinstance(contradictions, (list, tuple, set)):
        raw_items = list(contradictions)
    else:
        raw_items = [contradictions]

    deduped: list[Any] = []
    seen_keys: set[tuple] = set()

    for c in raw_items:
        c_id = getattr(c, "id", None) or (c.get("id") if isinstance(c, dict) else None)
        c_type = getattr(c, "contradiction_type", None) or (c.get("contradiction_type") if isinstance(c, dict) else None)
        c_field = getattr(c, "field", None) or (c.get("field") if isinstance(c, dict) else None)

        side_a = getattr(c, "side_a", None) or (c.get("side_a") if isinstance(c, dict) else None)
        side_b = getattr(c, "side_b", None) or (c.get("side_b") if isinstance(c, dict) else None)

        side_a_id = getattr(side_a, "report_id", None) if side_a else (side_a.get("report_id") if isinstance(side_a, dict) else None)
        side_b_id = getattr(side_b, "report_id", None) if side_b else (side_b.get("report_id") if isinstance(side_b, dict) else None)

        # Also support ORM model Contradiction fields
        if not side_a_id:
            side_a_id = getattr(c, "side_a_report_id", None)
        if not side_b_id:
            side_b_id = getattr(c, "side_b_report_id", None)

        if c_type and c_field and side_a_id and side_b_id:
            pair = tuple(sorted([str(side_a_id), str(side_b_id)]))
            key = (str(c_type), str(c_field), pair)
        elif c_id:
            key = ("id", str(c_id))
        else:
            key = ("obj_id", id(c))

        if key not in seen_keys:
            seen_keys.add(key)
            deduped.append(c)

    return deduped


def _count_distinct_disagreements(contradictions: Optional[Union[Sequence[Any], Any]]) -> tuple[int, list[Any]]:
    """
    P0-2: Deduplicate contradiction records into distinct underlying disagreements
    for confidence consistency penalty calculation, while preserving all raw
    normalized contradiction records for responder review and evidence displays.

    Disagreement identity:
      (contradiction_type, field, tuple(sorted([norm_side_a, norm_side_b])))

    Ordering of sides is canonicalized so A/B and B/A represent the same disagreement.
    Genuinely different fields, types, or opposing states remain distinct.
    """
    raw_deduped = _normalize_contradictions(contradictions)
    if not raw_deduped:
        return 0, []

    distinct_keys: set[tuple] = set()
    for c in raw_deduped:
        c_type = str(
            getattr(c, "contradiction_type", "")
            or (c.get("contradiction_type") if isinstance(c, dict) else "")
            or ""
        ).lower().strip()
        c_field = str(
            getattr(c, "field", "")
            or (c.get("field") if isinstance(c, dict) else "")
            or ""
        ).lower().strip()

        side_a = getattr(c, "side_a", None) or (c.get("side_a") if isinstance(c, dict) else None)
        side_b = getattr(c, "side_b", None) or (c.get("side_b") if isinstance(c, dict) else None)

        val_a = getattr(side_a, "value", None) if side_a else None
        if val_a is None:
            val_a = getattr(c, "side_a_value", None) or (c.get("side_a_value") if isinstance(c, dict) else None)

        val_b = getattr(side_b, "value", None) if side_b else None
        if val_b is None:
            val_b = getattr(c, "side_b_value", None) or (c.get("side_b_value") if isinstance(c, dict) else None)

        norm_a = str(val_a).lower().strip() if val_a is not None else ""
        norm_b = str(val_b).lower().strip() if val_b is not None else ""

        canonical_values = tuple(sorted([norm_a, norm_b]))
        key = (c_type, c_field, canonical_values)
        distinct_keys.add(key)

    return len(distinct_keys), raw_deduped


def _cluster_corroboration_units(
    reports: Sequence[Any],
    cosine_threshold: float = 0.95,
) -> list[list[Any]]:
    """
    P0-1: Cluster reports within an incident into corroboration units to prevent duplicate
    or forwarded copies of the same underlying message from inflating source count and
    source diversity in the confidence calculation.

    Near-duplicate criteria:
    - Conservative cosine similarity >= 0.95 using normalized report embeddings.
    - Complete-linkage clustering: a report is only grouped into an existing unit if it
      has similarity >= 0.95 with ALL members of that unit (preventing runaway transitivity).
    - If embeddings are unavailable, falls back safely to exact normalized text equality
      or distinct units, preserving existing behavior without inventing similarity.
    - Reports and their channels/evidence are NEVER deleted or dropped; this is strictly
      an evidence-fusion confidence guard.
    """
    if not reports:
        return []

    report_items = []
    for rep in reports:
        norm_text = _get_report_attr(rep, "normalized_text", "")
        if not norm_text:
            raw_text = _get_report_attr(rep, "raw_text", "")
            norm_text = normalize_text(raw_text) if raw_text else ""

        emb = None
        raw_emb = _get_report_attr(rep, "embedding", None)
        if raw_emb:
            emb = deserialize_embedding(raw_emb)

        if emb is None and norm_text:
            try:
                emb = generate_embedding(norm_text)
            except Exception:
                emb = None

        report_items.append({
            "report": rep,
            "norm_text": norm_text.strip().lower(),
            "embedding": emb,
        })

    units: list[list[dict]] = []

    for item in report_items:
        placed = False
        item_emb = item["embedding"]
        item_text = item["norm_text"]

        for unit in units:
            # Complete-linkage check: item must be near-duplicate with EVERY member of the unit
            matches_all = True
            for member in unit:
                mem_emb = member["embedding"]
                mem_text = member["norm_text"]

                is_duplicate = False
                if item_emb and mem_emb:
                    sim = cosine_similarity(item_emb, mem_emb)
                    if sim >= cosine_threshold:
                        is_duplicate = True
                elif item_text and mem_text and item_text == mem_text:
                    is_duplicate = True

                if not is_duplicate:
                    matches_all = False
                    break

            if matches_all:
                unit.append(item)
                placed = True
                break

        if not placed:
            units.append([item])

    return [[item["report"] for item in u] for u in units]


# ---------------------------------------------------------------------------
# 1. SEVERITY ENGINE
# ---------------------------------------------------------------------------

def calculate_severity(
    reports: Sequence[Any],
    extractions_by_report: Optional[dict[str, list[Any]]] = None,
) -> SeverityBreakdown:
    """
    Calculate explainable physical danger / severity (0–100) for a candidate incident.

    Steps:
    1. Incident type baseline: Strongest applicable SEVERITY_TYPE_BASE across reports.
    2. Explicit severity keyword: Strongest applicable keyword in SEVERITY_KEYWORD_MAP.
    3. Baseline severity = max(strongest_type_base, strongest_keyword_score).
       Strictly avoids summing keywords with incident types (no double counting).
    4. Trapped modifier (+15): Applied ONCE per incident if explicit evidence of category 'trapped' exists.
       'affected', 'nearby', 'at_risk' do NOT trigger the trapped modifier.
    5. Vulnerable modifier (+10): Applied ONCE per incident if genuine vulnerable-person evidence exists.
       Generic words like 'family', 'resident', 'person' are rejected.
    6. Final severity = min(baseline_severity + modifiers, 100).
    """
    if not reports:
        return SeverityBreakdown(
            base_type=None,
            base_type_score=0.0,
            keyword=None,
            keyword_score=0.0,
            baseline_severity=0.0,
            modifiers=[],
            final_severity=0.0,
            evidence=[],
        )

    # Sort reports deterministically by ID
    sorted_reports = sorted(reports, key=lambda r: str(_get_report_attr(r, "id", "")))

    all_evidence: list[str] = []

    # STEP 1: Find strongest incident type across all reports
    best_type: Optional[str] = None
    best_type_score: float = 0.0
    type_evidence: list[str] = []

    # STEP 2: Find strongest explicit severity keyword across all reports
    best_kw: Optional[str] = None
    best_kw_score: float = 0.0
    kw_evidence: list[str] = []

    # Evidence trackers for trapped and vulnerable
    trapped_evidence: list[str] = []
    vulnerable_evidence: list[str] = []

    for rep in sorted_reports:
        rid = str(_get_report_attr(rep, "id", ""))
        provided_exts = extractions_by_report.get(rid) if extractions_by_report else None
        exts = _get_report_extractions(rep, provided_extractions=provided_exts)

        raw_text = _get_report_attr(rep, "raw_text", "")
        norm_text = _get_report_attr(rep, "normalized_text", "") or normalize_text(raw_text)

        # 1. Incident type extractions
        for ext in exts:
            if ext.field_name == "incident_type" and ext.value:
                t_val = str(ext.value).lower().strip()
                t_score = float(SEVERITY_TYPE_BASE.get(t_val, 0.0))
                if t_score > best_type_score:
                    best_type_score = t_score
                    best_type = t_val
                    type_evidence = list(ext.evidence) if ext.evidence else [t_val]
                elif t_score == best_type_score and t_score > 0:
                    # Deterministic tie-breaking
                    if best_type is None or t_val < best_type:
                        best_type = t_val
                        type_evidence = list(ext.evidence) if ext.evidence else [t_val]

            # 2. Severity hint extractions
            if ext.field_name == "severity_hint" and ext.value:
                kw_val = str(ext.value).lower().strip()
                kw_score = float(SEVERITY_KEYWORD_MAP.get(kw_val, 0.0))
                if kw_score > best_kw_score:
                    best_kw_score = kw_score
                    best_kw = kw_val
                    kw_evidence = list(ext.evidence) if ext.evidence else [kw_val]
                elif kw_score == best_kw_score and kw_score > 0:
                    if best_kw is None or kw_val < best_kw:
                        best_kw = kw_val
                        kw_evidence = list(ext.evidence) if ext.evidence else [kw_val]

            # 3. People estimate extractions (category check)
            if ext.field_name == "people_estimate" and ext.value:
                val = ext.value
                if isinstance(val, str):
                    try:
                        val = json.loads(val)
                    except Exception:
                        val = {}
                if isinstance(val, dict):
                    cat = str(val.get("category", "")).lower().strip()
                    if cat == "trapped":
                        ev_list = ext.evidence or [f"{val.get('count', 'people')} trapped"]
                        for ev in ev_list:
                            if ev not in trapped_evidence:
                                trapped_evidence.append(ev)

            # 4. Trapped or rescue flag (only if evidence indicates actual trapped/stuck/stranded persons)
            if ext.field_name == "trapped_or_rescue" and ext.value is True:
                ev_list = ext.evidence or []
                for ev in ev_list:
                    if _TRAPPED_REGEX.search(ev):
                        if ev not in trapped_evidence:
                            trapped_evidence.append(ev)

            # 5. Vulnerable persons extraction
            if ext.field_name == "vulnerable_persons" and ext.value:
                ev_list = ext.evidence or []
                if _contains_actual_vulnerability(ev_list):
                    for ev in ev_list:
                        if ev not in vulnerable_evidence:
                            vulnerable_evidence.append(ev)

        # Also inspect direct text for explicit keywords if severity_hint missed it
        for kw, score in SEVERITY_KEYWORD_MAP.items():
            if re.search(rf"\b{kw}\b", norm_text, re.IGNORECASE):
                if float(score) > best_kw_score:
                    best_kw_score = float(score)
                    best_kw = kw
                    kw_evidence = [kw]

    # STEP 3: Baseline = max(type_base, keyword_score). No summing!
    baseline_severity = max(best_type_score, best_kw_score)

    if type_evidence:
        for ev in type_evidence:
            if ev not in all_evidence:
                all_evidence.append(ev)
    if kw_evidence:
        for ev in kw_evidence:
            if ev not in all_evidence:
                all_evidence.append(ev)

    modifiers: list[SeverityModifier] = []

    # STEP 4: Trapped modifier (+15 ONCE per incident)
    if trapped_evidence:
        modifiers.append(
            SeverityModifier(
                name="trapped_modifier",
                bonus=float(SEVERITY_TRAPPED_BONUS),
                evidence=trapped_evidence,
            )
        )
        for ev in trapped_evidence:
            if ev not in all_evidence:
                all_evidence.append(ev)

    # STEP 5: Vulnerable modifier (+10 ONCE per incident)
    if vulnerable_evidence:
        modifiers.append(
            SeverityModifier(
                name="vulnerable_modifier",
                bonus=float(SEVERITY_VULNERABLE_BONUS),
                evidence=vulnerable_evidence,
            )
        )
        for ev in vulnerable_evidence:
            if ev not in all_evidence:
                all_evidence.append(ev)

    # STEP 6: Final severity clamped to [0, 100]
    total_mod_bonus = sum(m.bonus for m in modifiers)
    raw_final = baseline_severity + total_mod_bonus
    final_severity = min(max(float(raw_final), 0.0), float(SEVERITY_MAX))

    return SeverityBreakdown(
        base_type=best_type,
        base_type_score=best_type_score,
        keyword=best_kw,
        keyword_score=best_kw_score,
        baseline_severity=baseline_severity,
        modifiers=modifiers,
        final_severity=final_severity,
        evidence=all_evidence,
    )


# ---------------------------------------------------------------------------
# 2. CONFIDENCE ENGINE
# ---------------------------------------------------------------------------

def calculate_confidence(
    reports: Sequence[Any],
    contradictions: Optional[Sequence[Any]] = None,
    extractions_by_report: Optional[dict[str, list[Any]]] = None,
) -> ConfidenceBreakdown:
    """
    Calculate explainable evidence confidence (0.00–1.00) across 5 components:

    A. source_count (25%): Discrete policy (1->0.40, 2->0.70, 3->0.85, 4+->1.00).
    B. source_diversity (20%): Unique channels (1->0.40, 2->0.75, 3+->1.00).
    C. consistency (25%): Contradiction penalty (0->1.00, 1->0.75, 2->0.50, 3->0.25, 4+->0.00).
    D. extraction_quality (15%): Mean confidence of fields actually extracted with evidence.
       Does NOT penalize absent optional fields.
    E. information_type (15%): Mean info_type score (explicit=1.00, inferred=0.40, null=0.20).
    """
    if not reports:
        return ConfidenceBreakdown(
            final=0.0,
            breakdown={},
            source_count=0.0,
            source_count_detail="No reports",
            source_diversity=0.0,
            source_diversity_detail="No sources",
            consistency=1.0,
            consistency_detail="No contradictions",
            extraction_quality=0.0,
            extraction_detail="No extractions",
            information_type=0.20,
            information_type_detail="No info_type",
            weights={},
        )

    # Sort reports deterministically
    sorted_reports = sorted(reports, key=lambda r: str(_get_report_attr(r, "id", "")))
    num_reports = len(sorted_reports)

    # -----------------------------------------------------------------------
    # P0-1: Near-duplicate Corroboration Guard
    # Cluster reports into corroboration units (threshold >= 0.95 cosine similarity).
    # -----------------------------------------------------------------------
    corroboration_units = _cluster_corroboration_units(sorted_reports, cosine_threshold=0.95)
    effective_report_count = len(corroboration_units)

    # -----------------------------------------------------------------------
    # Component A: Source Count (25%)
    # -----------------------------------------------------------------------
    if effective_report_count >= 4:
        sc_val = CONFIDENCE_SOURCE_COUNT_POLICY[4]
    else:
        sc_val = CONFIDENCE_SOURCE_COUNT_POLICY.get(effective_report_count, 0.40)

    if effective_report_count < num_reports:
        duplicates_grouped = num_reports - effective_report_count
        sc_detail = (
            f"{effective_report_count} corroboration unit(s) "
            f"(from {num_reports} report(s), {duplicates_grouped} duplicate copy/copies grouped) "
            f"-> discrete score {sc_val:.2f}"
        )
    else:
        sc_detail = f"{num_reports} report(s) -> discrete score {sc_val:.2f}"

    # -----------------------------------------------------------------------
    # Component B: Source Diversity (20%)
    # Genuinely different reports remain separate corroboration units even if
    # on the same channel. Repeated forwarded copies of the same underlying
    # observation belong to 1 unit and represent at most 1 channel.
    # -----------------------------------------------------------------------
    effective_sources: set[str] = set()
    for unit in corroboration_units:
        # Primary source channel representing this corroboration unit
        unit_primary_rep = unit[0]
        src = str(_get_report_attr(unit_primary_rep, "source", "unknown")).lower().strip()
        if src:
            effective_sources.add(src)

    num_effective_sources = len(effective_sources)
    if num_effective_sources >= 3:
        sd_val = CONFIDENCE_SOURCE_DIVERSITY_POLICY[3]
    else:
        sd_val = CONFIDENCE_SOURCE_DIVERSITY_POLICY.get(num_effective_sources, 0.40)
    sorted_src_names = ", ".join(sorted(effective_sources)) if effective_sources else "none"

    if effective_report_count < num_reports:
        sd_detail = (
            f"{num_effective_sources} effective source channel(s) ({sorted_src_names}) "
            f"across {effective_report_count} corroboration unit(s) -> discrete score {sd_val:.2f}"
        )
    else:
        sd_detail = f"{num_effective_sources} unique source channel(s) ({sorted_src_names}) -> discrete score {sd_val:.2f}"

    # -----------------------------------------------------------------------
    # Component C: Consistency / Contradictions (25%)
    # P0-2: Use distinct disagreement count for confidence consistency penalty,
    # while preserving all raw pairwise contradiction records for UI review.
    # -----------------------------------------------------------------------
    distinct_disagreements, norm_contradictions = _count_distinct_disagreements(contradictions)
    num_contradictions = len(norm_contradictions)
    if distinct_disagreements >= 4:
        cs_val = CONFIDENCE_CONSISTENCY_POLICY[4]
    else:
        cs_val = CONFIDENCE_CONSISTENCY_POLICY.get(distinct_disagreements, 0.00)

    if num_contradictions > distinct_disagreements:
        cs_detail = (
            f"{distinct_disagreements} distinct disagreement(s) "
            f"(across {num_contradictions} pairwise contradiction record(s)) -> discrete score {cs_val:.2f}"
        )
    else:
        cs_detail = f"{distinct_disagreements} contradiction(s) detected -> discrete score {cs_val:.2f}"

    # -----------------------------------------------------------------------
    # Component D: Extraction Quality (15%)
    # CRITICAL: Only evaluate fields that were actually extracted with evidence.
    # Do NOT penalize absent optional fields.
    # -----------------------------------------------------------------------
    valid_confidences: list[float] = []
    for rep in sorted_reports:
        rid = str(_get_report_attr(rep, "id", ""))
        provided_exts = extractions_by_report.get(rid) if extractions_by_report else None
        exts = _get_report_extractions(rep, provided_extractions=provided_exts)

        for ext in exts:
            # Field must have an actual value and non-empty evidence
            if ext.value is not None and ext.value != "" and ext.value is not False and ext.evidence:
                valid_confidences.append(float(ext.confidence))

    if valid_confidences:
        eq_val = sum(valid_confidences) / len(valid_confidences)
        eq_detail = f"{len(valid_confidences)} evidence-backed extraction(s), mean confidence {eq_val:.2f}"
    else:
        # Safe deterministic fallback without inventing high score
        eq_val = 0.20
        eq_detail = "No evidence-backed extractions; default baseline 0.20"

    eq_val = min(max(float(eq_val), 0.0), 1.0)

    # -----------------------------------------------------------------------
    # Component E: Information Type (15%)
    # explicit=1.00, inferred=0.40, null/unknown=0.20
    # -----------------------------------------------------------------------
    info_type_scores: list[float] = []
    for rep in sorted_reports:
        rid = str(_get_report_attr(rep, "id", ""))
        provided_exts = extractions_by_report.get(rid) if extractions_by_report else None
        exts = _get_report_extractions(rep, provided_extractions=provided_exts)

        rep_info_type = None
        for ext in exts:
            if ext.field_name == "info_type" and ext.value:
                rep_info_type = str(ext.value).lower().strip()
                break

        if rep_info_type in CONFIDENCE_INFO_TYPE_POLICY:
            info_type_scores.append(CONFIDENCE_INFO_TYPE_POLICY[rep_info_type])
        else:
            info_type_scores.append(CONFIDENCE_INFO_TYPE_POLICY["null"])

    if info_type_scores:
        it_val = sum(info_type_scores) / len(info_type_scores)
        it_detail = f"Mean info_type score across {len(info_type_scores)} report(s): {it_val:.2f}"
    else:
        it_val = CONFIDENCE_INFO_TYPE_POLICY["null"]
        it_detail = "No info_type available; default baseline 0.20"

    it_val = min(max(float(it_val), 0.0), 1.0)

    # -----------------------------------------------------------------------
    # Weighted Sum
    # -----------------------------------------------------------------------
    raw_final = (
        sc_val * CONFIDENCE_WEIGHT_SOURCE_COUNT
        + sd_val * CONFIDENCE_WEIGHT_SOURCE_DIVERSITY
        + cs_val * CONFIDENCE_WEIGHT_CONSISTENCY
        + eq_val * CONFIDENCE_WEIGHT_EXTRACTION
        + it_val * CONFIDENCE_WEIGHT_INFO_TYPE
    )
    final_conf = min(max(float(raw_final), 0.0), 1.0)

    weights_dict = {
        "source_count": CONFIDENCE_WEIGHT_SOURCE_COUNT,
        "source_diversity": CONFIDENCE_WEIGHT_SOURCE_DIVERSITY,
        "consistency": CONFIDENCE_WEIGHT_CONSISTENCY,
        "extraction_quality": CONFIDENCE_WEIGHT_EXTRACTION,
        "information_type": CONFIDENCE_WEIGHT_INFO_TYPE,
    }

    components = {
        "source_count": ConfidenceComponent(value=sc_val, weight=CONFIDENCE_WEIGHT_SOURCE_COUNT, detail=sc_detail),
        "source_diversity": ConfidenceComponent(value=sd_val, weight=CONFIDENCE_WEIGHT_SOURCE_DIVERSITY, detail=sd_detail),
        "consistency": ConfidenceComponent(value=cs_val, weight=CONFIDENCE_WEIGHT_CONSISTENCY, detail=cs_detail),
        "extraction_quality": ConfidenceComponent(value=eq_val, weight=CONFIDENCE_WEIGHT_EXTRACTION, detail=eq_detail),
        "information_type": ConfidenceComponent(value=it_val, weight=CONFIDENCE_WEIGHT_INFO_TYPE, detail=it_detail),
    }

    return ConfidenceBreakdown(
        final=final_conf,
        breakdown=components,
        source_count=sc_val,
        source_count_detail=sc_detail,
        source_diversity=sd_val,
        source_diversity_detail=sd_detail,
        consistency=cs_val,
        consistency_detail=cs_detail,
        extraction_quality=eq_val,
        extraction_detail=eq_detail,
        information_type=it_val,
        information_type_detail=it_detail,
        weights=weights_dict,
    )


# ---------------------------------------------------------------------------
# 3. PRIORITY & URGENCY ENGINE
# ---------------------------------------------------------------------------

def determine_urgency_band(priority: float) -> str:
    """
    Map priority into explicit urgency bands without premature rounding:

    85.0 <= priority <= 100.0 -> critical
    70.0 <= priority < 85.0   -> high
    40.0 <= priority < 70.0   -> medium
    0.0  <= priority < 40.0   -> low
    """
    p = float(priority)
    if p >= 85.0:
        return "critical"
    elif p >= 70.0:
        return "high"
    elif p >= 40.0:
        return "medium"
    else:
        return "low"


def calculate_priority(severity: float, confidence: float) -> PriorityBreakdown:
    """
    Calculate explainable responder attention priority (0–100) and urgency band.

    Formula:
        raw_priority = severity * (0.40 + 0.60 * confidence)

    Low-confidence safety cap:
        If confidence < 0.30:
            final_priority = min(raw_priority, 69.0)
            (Ensures uncorroborated reports remain at most Medium urgency).
    """
    sev = min(max(float(severity), 0.0), 100.0)
    conf = min(max(float(confidence), 0.0), 1.0)

    # Priority formula
    multiplier = PRIORITY_FORMULA_BASE + (PRIORITY_FORMULA_CONFIDENCE_FACTOR * conf)
    raw_priority = min(max(sev * multiplier, 0.0), 100.0)

    formula_str = (
        f"severity ({sev:.1f}) * ({PRIORITY_FORMULA_BASE:.2f} + "
        f"{PRIORITY_FORMULA_CONFIDENCE_FACTOR:.2f} * confidence ({conf:.2f})) = {raw_priority:.2f}"
    )

    # Low-confidence safety cap
    low_conf_cap_applied = False
    if conf < PRIORITY_LOW_CONFIDENCE_THRESHOLD:
        if raw_priority > float(PRIORITY_LOW_CONFIDENCE_CAP):
            final_priority = float(PRIORITY_LOW_CONFIDENCE_CAP)
            low_conf_cap_applied = True
            formula_str += f" -> capped at {PRIORITY_LOW_CONFIDENCE_CAP} (confidence < {PRIORITY_LOW_CONFIDENCE_THRESHOLD})"
        else:
            final_priority = raw_priority
    else:
        final_priority = raw_priority

    urgency = determine_urgency_band(final_priority)

    return PriorityBreakdown(
        severity=sev,
        confidence=conf,
        raw_priority=raw_priority,
        low_confidence_cap_applied=low_conf_cap_applied,
        final_priority=final_priority,
        urgency=urgency,
        formula=formula_str,
    )


# ---------------------------------------------------------------------------
# 4. INCIDENT ASSESSMENT ORCHESTRATOR
# ---------------------------------------------------------------------------

def assess_incident(
    incident_id: str,
    reports: Sequence[Any],
    contradictions: Optional[Sequence[Any]] = None,
    extractions_by_report: Optional[dict[str, list[Any]]] = None,
) -> IncidentAssessment:
    """
    Perform complete deterministic assessment for a candidate incident.
    """
    sev_breakdown = calculate_severity(reports, extractions_by_report=extractions_by_report)
    conf_breakdown = calculate_confidence(
        reports, contradictions=contradictions, extractions_by_report=extractions_by_report
    )
    prio_breakdown = calculate_priority(sev_breakdown.final_severity, conf_breakdown.final)

    return IncidentAssessment(
        incident_id=incident_id,
        severity=sev_breakdown,
        confidence=conf_breakdown,
        priority=prio_breakdown,
        urgency=prio_breakdown.urgency,
    )


# ---------------------------------------------------------------------------
# 5. DATABASE PERSISTENCE
# ---------------------------------------------------------------------------

def persist_incident_assessment(
    session: Any,
    assessment: IncidentAssessment,
) -> Optional[Incident]:
    """
    Persist assessment results into the existing Incident ORM record.

    Updates ONLY:
    - severity
    - confidence
    - confidence_breakdown (JSON string)
    - priority
    - urgency

    Does NOT alter incident relationships, report links, or contradictions.
    """
    inc_id = assessment.incident_id

    # 1. Search existing in session
    incident = None
    try:
        incident = session.query(Incident).filter(Incident.id == inc_id).first()
    except Exception:
        pass

    if incident is None:
        # Check session.new
        try:
            for obj in session.new:
                if isinstance(obj, Incident) and getattr(obj, "id", None) == inc_id:
                    incident = obj
                    break
        except Exception:
            pass

    if incident is None:
        return None

    # Update ONLY Phase 7 assessment fields
    incident.severity = assessment.severity.final_severity
    incident.confidence = assessment.confidence.final
    incident.confidence_breakdown = assessment.confidence.model_dump_json()
    incident.priority = assessment.priority.final_priority
    incident.urgency = assessment.urgency

    return incident
