"""
SETU Phase 7 Test Suite — Incident Confidence, Severity & Priority Engine.

Comprehensive test suite verifying:
- Severity engine (base type, keywords, trapped/vulnerable modifiers, capping at 100, no double counting)
- People semantics (category isolation: trapped vs affected/nearby/at_risk, preserving qualitative facts)
- Confidence engine (discrete policies for source count, source diversity, consistency, extraction quality, info type)
- Extraction quality (does NOT penalize absent optional fields)
- Priority engine (formula, confidence adjustment, low-confidence cap, urgency boundaries)
- Explainability (structured breakdowns with evidence, formula, components)
- Determinism (order independence, repeat stability)
- Integration (P5 clusters, P6 contradictions affecting confidence but not severity, persistence)
- Safety (clamping, missing fields, no automatic responder decisions)
"""

import os
import sys
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Ensure backend/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import (
    CONFIDENCE_WEIGHT_CONSISTENCY,
    CONFIDENCE_WEIGHT_EXTRACTION,
    CONFIDENCE_WEIGHT_INFO_TYPE,
    CONFIDENCE_WEIGHT_SOURCE_COUNT,
    CONFIDENCE_WEIGHT_SOURCE_DIVERSITY,
    PRIORITY_FORMULA_BASE,
    PRIORITY_FORMULA_CONFIDENCE_FACTOR,
    PRIORITY_LOW_CONFIDENCE_CAP,
    PRIORITY_LOW_CONFIDENCE_THRESHOLD,
    SEVERITY_MAX,
    SEVERITY_TRAPPED_BONUS,
    SEVERITY_TYPE_BASE,
    SEVERITY_VULNERABLE_BONUS,
)
from database import Base
from models import Contradiction, Incident, Report
from schemas import (
    ContradictionOut,
    ContradictionOutput,
    ContradictionResult,
    ContradictionSide,
    ExtractionField,
    IncidentAssessment,
)
from services.incident_assessor import (
    assess_incident,
    calculate_confidence,
    calculate_priority,
    calculate_severity,
    determine_urgency_band,
    persist_incident_assessment,
)


# ============================================================================
# 1. SEVERITY TESTS (Tests 1–8)
# ============================================================================

class TestSeverityCalculation:
    """Verify physical danger scoring from incident types, keywords, and modifiers."""

    def test_flood_baseline(self):
        """1. Flood incident type produces baseline severity of 65."""
        rep = {"id": "R001", "raw_text": "flooding on the main road", "source": "whatsapp"}
        exts = [ExtractionField(field_name="incident_type", value="flood", confidence=0.85, evidence=["flooding"])]
        sev = calculate_severity([rep], extractions_by_report={"R001": exts})
        assert sev.base_type == "flood"
        assert sev.base_type_score == 65.0
        assert sev.final_severity == 65.0

    def test_rescue_needed_baseline(self):
        """2. Rescue needed produces baseline severity of 85."""
        rep = {"id": "R001", "raw_text": "rescue needed immediately", "source": "whatsapp"}
        exts = [ExtractionField(field_name="incident_type", value="rescue_needed", confidence=0.90, evidence=["rescue needed"])]
        sev = calculate_severity([rep], extractions_by_report={"R001": exts})
        assert sev.base_type == "rescue_needed"
        assert sev.base_type_score == 85.0
        assert sev.final_severity == 85.0

    def test_structural_damage_baseline(self):
        """3. Structural damage produces baseline severity of 60."""
        rep = {"id": "R001", "raw_text": "boundary wall leaning risk of collapse", "source": "web"}
        exts = [ExtractionField(field_name="incident_type", value="structural_damage", confidence=0.90, evidence=["risk of collapse"])]
        sev = calculate_severity([rep], extractions_by_report={"R001": exts})
        assert sev.base_type == "structural_damage"
        assert sev.base_type_score == 60.0
        assert sev.final_severity == 60.0

    def test_trapped_modifier_adds_15(self):
        """4. Trapped people evidence adds +15 modifier ONCE to baseline."""
        rep = {"id": "R001", "raw_text": "flood with 2 people trapped", "source": "whatsapp"}
        exts = [
            ExtractionField(field_name="incident_type", value="flood", confidence=0.85, evidence=["flood"]),
            ExtractionField(field_name="people_estimate", value={"count": 2, "category": "trapped"}, confidence=0.80, evidence=["2 people trapped"]),
        ]
        sev = calculate_severity([rep], extractions_by_report={"R001": exts})
        assert sev.baseline_severity == 65.0
        assert len(sev.modifiers) == 1
        assert sev.modifiers[0].name == "trapped_modifier"
        assert sev.modifiers[0].bonus == 15.0
        assert sev.final_severity == 80.0  # 65 + 15

    def test_vulnerable_modifier_adds_10(self):
        """5. Vulnerable persons evidence adds +10 modifier ONCE to baseline."""
        rep = {"id": "R001", "raw_text": "flood affecting elderly residents", "source": "sms"}
        exts = [
            ExtractionField(field_name="incident_type", value="flood", confidence=0.85, evidence=["flood"]),
            ExtractionField(field_name="vulnerable_persons", value=True, confidence=0.90, evidence=["elderly residents"]),
        ]
        sev = calculate_severity([rep], extractions_by_report={"R001": exts})
        assert sev.baseline_severity == 65.0
        assert len(sev.modifiers) == 1
        assert sev.modifiers[0].name == "vulnerable_modifier"
        assert sev.modifiers[0].bonus == 10.0
        assert sev.final_severity == 75.0  # 65 + 10

    def test_critical_keyword_sets_severity_90(self):
        """6. Explicit critical keyword produces severity of 90."""
        rep = {"id": "R001", "raw_text": "critical emergency situation", "source": "field_worker"}
        exts = [ExtractionField(field_name="severity_hint", value="critical", confidence=0.90, evidence=["critical"])]
        sev = calculate_severity([rep], extractions_by_report={"R001": exts})
        assert sev.keyword == "critical"
        assert sev.keyword_score == 90.0
        assert sev.final_severity == 90.0

    def test_cap_at_100(self):
        """7. Severity is capped at 100 even if baseline + modifiers exceeds 100."""
        rep = {"id": "R001", "raw_text": "rescue needed, children trapped in building", "source": "field_worker"}
        exts = [
            ExtractionField(field_name="incident_type", value="rescue_needed", confidence=0.90, evidence=["rescue needed"]),  # 85
            ExtractionField(field_name="people_estimate", value={"count": 3, "category": "trapped"}, confidence=0.85, evidence=["children trapped"]),  # +15
            ExtractionField(field_name="vulnerable_persons", value=True, confidence=0.90, evidence=["children trapped"]),  # +10
        ]
        sev = calculate_severity([rep], extractions_by_report={"R001": exts})
        # 85 + 15 + 10 = 110 -> capped at 100.0
        assert sev.baseline_severity == 85.0
        assert sev.final_severity == 100.0

    def test_no_double_counting_synonymous_signals(self):
        """8. 'Critical flood, severe waterlogging' receives 90 baseline, NOT 90+70+65+40."""
        rep = {"id": "R001", "raw_text": "Critical flood, severe waterlogging in area", "source": "web"}
        exts = [
            ExtractionField(field_name="incident_type", value="flood", confidence=0.85, evidence=["flood"]),  # 65
            ExtractionField(field_name="severity_hint", value="critical", confidence=0.90, evidence=["critical"]),  # 90
        ]
        # Adding second report in same cluster mentioning severe waterlogging
        rep2 = {"id": "R002", "raw_text": "severe waterlogging", "source": "whatsapp"}
        exts2 = [
            ExtractionField(field_name="incident_type", value="waterlogging", confidence=0.85, evidence=["waterlogging"]),  # 40
            ExtractionField(field_name="severity_hint", value="severe", confidence=0.80, evidence=["severe"]),  # 70
        ]
        sev = calculate_severity([rep, rep2], extractions_by_report={"R001": exts, "R002": exts2})
        assert sev.baseline_severity == 90.0  # max(65, 40, 90, 70)
        assert sev.final_severity == 90.0


# ============================================================================
# 2. PEOPLE SEMANTICS (Tests 9–13)
# ============================================================================

class TestPeopleSemantics:
    """Verify category isolation: only trapped triggers the +15 modifier."""

    def test_trapped_contributes_modifier(self):
        """9. Category 'trapped' triggers the trapped modifier."""
        rep = {"id": "R001", "raw_text": "4 people trapped"}
        exts = [
            ExtractionField(field_name="incident_type", value="flood", confidence=0.80),
            ExtractionField(field_name="people_estimate", value={"count": 4, "category": "trapped"}, evidence=["4 people trapped"]),
        ]
        sev = calculate_severity([rep], extractions_by_report={"R001": exts})
        assert any(m.name == "trapped_modifier" for m in sev.modifiers)
        assert sev.final_severity == 80.0

    def test_affected_does_not_contribute_trapped_modifier(self):
        """10. Category 'affected' does NOT trigger the trapped modifier."""
        rep = {"id": "R001", "raw_text": "20 families affected by flood"}
        exts = [
            ExtractionField(field_name="incident_type", value="flood", confidence=0.80),
            ExtractionField(field_name="people_estimate", value={"count": 20, "category": "affected"}, evidence=["20 families affected"]),
        ]
        sev = calculate_severity([rep], extractions_by_report={"R001": exts})
        assert not any(m.name == "trapped_modifier" for m in sev.modifiers)
        assert sev.final_severity == 65.0  # baseline flood only

    def test_nearby_does_not_contribute_trapped_modifier(self):
        """11. Category 'nearby' does NOT trigger the trapped modifier."""
        rep = {"id": "R001", "raw_text": "10 residents nearby"}
        exts = [
            ExtractionField(field_name="incident_type", value="flood", confidence=0.80),
            ExtractionField(field_name="people_estimate", value={"count": 10, "category": "nearby"}, evidence=["10 residents nearby"]),
        ]
        sev = calculate_severity([rep], extractions_by_report={"R001": exts})
        assert not any(m.name == "trapped_modifier" for m in sev.modifiers)
        assert sev.final_severity == 65.0

    def test_at_risk_does_not_contribute_trapped_modifier(self):
        """12. Category 'at_risk' does NOT trigger the trapped modifier."""
        rep = {"id": "R001", "raw_text": "5 people at risk near leaning wall"}
        exts = [
            ExtractionField(field_name="incident_type", value="structural_damage", confidence=0.80),
            ExtractionField(field_name="people_estimate", value={"count": 5, "category": "at_risk"}, evidence=["5 people at risk"]),
        ]
        sev = calculate_severity([rep], extractions_by_report={"R001": exts})
        assert not any(m.name == "trapped_modifier" for m in sev.modifiers)
        assert sev.final_severity == 60.0

    def test_qualitative_several_does_not_invent_number(self):
        """13. 'Several people trapped' preserves qualitative evidence without invented count."""
        rep = {"id": "R001", "raw_text": "several people trapped in flooded basement"}
        # Phase 2 extraction sets trapped_or_rescue=True with evidence 'trapped'
        exts = [
            ExtractionField(field_name="incident_type", value="flood", confidence=0.80, evidence=["flood"]),
            ExtractionField(field_name="trapped_or_rescue", value=True, confidence=0.90, evidence=["several people trapped"]),
        ]
        sev = calculate_severity([rep], extractions_by_report={"R001": exts})
        assert any(m.name == "trapped_modifier" for m in sev.modifiers)
        assert sev.final_severity == 80.0
        # Check that evidence preserves raw phrase and does not invent count
        modifier = next(m for m in sev.modifiers if m.name == "trapped_modifier")
        assert "several people trapped" in modifier.evidence[0]


# ============================================================================
# 3. CONFIDENCE TESTS (Tests 14–25)
# ============================================================================

class TestConfidenceCalculation:
    """Verify confidence calculation using discrete policies and explainable components."""

    def test_one_report_source_count(self):
        """14. Exactly 1 report produces source count score of 0.40."""
        reps = [{"id": "R001", "source": "whatsapp"}]
        conf = calculate_confidence(reps)
        assert conf.source_count == 0.40
        assert conf.breakdown["source_count"].value == 0.40

    def test_two_reports_source_count(self):
        """15. Exactly 2 reports produce source count score of 0.70."""
        reps = [{"id": "R001", "source": "whatsapp"}, {"id": "R002", "source": "whatsapp"}]
        conf = calculate_confidence(reps)
        assert conf.source_count == 0.70

    def test_three_reports_source_count(self):
        """16. Exactly 3 reports produce source count score of 0.85."""
        reps = [{"id": "R001", "source": "whatsapp"}, {"id": "R002", "source": "whatsapp"}, {"id": "R003", "source": "whatsapp"}]
        conf = calculate_confidence(reps)
        assert conf.source_count == 0.85

    def test_four_plus_reports_source_count(self):
        """17. 4 or more reports produce source count score of 1.00."""
        reps = [
            {"id": "R001", "source": "whatsapp"},
            {"id": "R002", "source": "whatsapp"},
            {"id": "R003", "source": "whatsapp"},
            {"id": "R004", "source": "whatsapp"},
            {"id": "R005", "source": "whatsapp"},
        ]
        conf = calculate_confidence(reps)
        assert conf.source_count == 1.00

    def test_one_source_channel(self):
        """18. Exactly 1 unique source channel produces source diversity score of 0.40."""
        reps = [{"id": "R001", "source": "whatsapp"}, {"id": "R002", "source": "whatsapp"}]
        conf = calculate_confidence(reps)
        assert conf.source_diversity == 0.40

    def test_two_source_channels(self):
        """19. Exactly 2 unique source channels produce source diversity score of 0.75."""
        reps = [{"id": "R001", "source": "whatsapp"}, {"id": "R002", "source": "sms"}]
        conf = calculate_confidence(reps)
        assert conf.source_diversity == 0.75

    def test_three_plus_source_channels(self):
        """20. 3 or more unique source channels produce source diversity score of 1.00."""
        reps = [
            {"id": "R001", "source": "whatsapp"},
            {"id": "R002", "source": "sms"},
            {"id": "R003", "source": "field_worker"},
        ]
        conf = calculate_confidence(reps)
        assert conf.source_diversity == 1.00

    def test_contradictions_reduce_consistency_policy(self):
        """21. Contradictions reduce consistency according to discrete policy."""
        reps = [{"id": "R001", "source": "whatsapp"}, {"id": "R002", "source": "sms"}]
        # 0 contradictions -> 1.00
        assert calculate_confidence(reps, contradictions=[]).consistency == 1.00

        # 1 contradiction -> 0.75
        c1 = ContradictionResult(
            id="C1", incident_id="INC-001", contradiction_type="numeric", field="people_estimate",
            side_a=ContradictionSide(report_id="R001", value="2"),
            side_b=ContradictionSide(report_id="R002", value="5"),
        )
        assert calculate_confidence(reps, contradictions=[c1]).consistency == 0.75

        # 2 contradictions -> 0.50
        c2 = ContradictionResult(
            id="C2", incident_id="INC-001", contradiction_type="severity", field="severity_hint",
            side_a=ContradictionSide(report_id="R001", value="critical"),
            side_b=ContradictionSide(report_id="R002", value="minor"),
        )
        assert calculate_confidence(reps, contradictions=[c1, c2]).consistency == 0.50

        # 3 contradictions -> 0.25
        c3 = ContradictionResult(
            id="C3", incident_id="INC-001", contradiction_type="hazard_structural", field="road_access",
            side_a=ContradictionSide(report_id="R001", value="blocked"),
            side_b=ContradictionSide(report_id="R002", value="passable"),
        )
        assert calculate_confidence(reps, contradictions=[c1, c2, c3]).consistency == 0.25

        # 4 contradictions -> 0.00
        c4 = ContradictionResult(
            id="C4", incident_id="INC-001", contradiction_type="location", field="location_resolved",
            side_a=ContradictionSide(report_id="R001", value="Civil Lines"),
            side_b=ContradictionSide(report_id="R002", value="Kotwali"),
        )
        assert calculate_confidence(reps, contradictions=[c1, c2, c3, c4]).consistency == 0.00

    def test_extraction_quality_ignores_absent_optional_fields(self):
        """22. Extraction quality measures only actual evidence-backed fields; does not penalize absent fields."""
        rep = {"id": "R001", "source": "sms"}
        # Report only mentions incident type (0.90) and urgency (0.90).
        # Optional fields like vulnerable_persons or people_estimate are absent.
        exts = [
            ExtractionField(field_name="incident_type", value="flood", confidence=0.90, evidence=["flood"]),
            ExtractionField(field_name="urgency_signals", value=["urgent"], confidence=0.90, evidence=["urgent"]),
            # Empty placeholder should NOT drag down the average
            ExtractionField(field_name="vulnerable_persons", value=None, confidence=0.0, evidence=[]),
            ExtractionField(field_name="people_estimate", value=None, confidence=0.0, evidence=[]),
        ]
        conf = calculate_confidence([rep], extractions_by_report={"R001": exts})
        assert conf.extraction_quality == 0.90

    def test_high_quality_extraction(self):
        """23. Evidence-backed extractions produce high extraction quality."""
        rep = {"id": "R001", "source": "field_worker"}
        exts = [
            ExtractionField(field_name="incident_type", value="flood", confidence=0.95, evidence=["flood"]),
            ExtractionField(field_name="info_type", value="explicit", confidence=0.95, evidence=["confirmed"]),
        ]
        conf = calculate_confidence([rep], extractions_by_report={"R001": exts})
        assert conf.extraction_quality == 0.95

    def test_weak_or_inferred_extraction(self):
        """24. Inferred report produces lower info_type score (0.40)."""
        rep = {"id": "R001", "source": "sms"}
        exts = [
            ExtractionField(field_name="incident_type", value="flood", confidence=0.70, evidence=["water"]),
            ExtractionField(field_name="info_type", value="inferred", confidence=0.75, evidence=["reportedly"]),
        ]
        conf = calculate_confidence([rep], extractions_by_report={"R001": exts})
        assert conf.information_type == 0.40

    def test_final_confidence_strictly_bounded_0_to_1(self):
        """25. Final confidence is strictly bounded within [0.0, 1.0]."""
        # Worst case: empty / 4 contradictions
        c_worst = [ContradictionResult(id=f"C{i}", incident_id="INC", contradiction_type="numeric", field="num",
                   side_a=ContradictionSide(report_id="R1"), side_b=ContradictionSide(report_id="R2")) for i in range(5)]
        conf_min = calculate_confidence([{"id": "R001", "source": "sms"}], contradictions=c_worst)
        assert 0.0 <= conf_min.final <= 1.0

        # Best case: 4 reports, 3 sources, 0 contradictions, 1.0 extraction quality, explicit info
        reps = [
            {"id": "R001", "source": "whatsapp"},
            {"id": "R002", "source": "sms"},
            {"id": "R003", "source": "field_worker"},
            {"id": "R004", "source": "web"},
        ]
        best_exts = {
            f"R00{i}": [
                ExtractionField(field_name="incident_type", value="flood", confidence=1.0, evidence=["flood"]),
                ExtractionField(field_name="info_type", value="explicit", confidence=1.0, evidence=["on ground"]),
            ] for i in range(1, 5)
        }
        conf_max = calculate_confidence(reps, contradictions=[], extractions_by_report=best_exts)
        assert conf_max.final == 1.00


# ============================================================================
# 4. PRIORITY & URGENCY TESTS (Tests 26–34)
# ============================================================================

class TestPriorityCalculation:
    """Verify formula, low-confidence safety cap, and exact urgency band boundaries."""

    def test_exact_priority_formula(self):
        """26. Verify formula: raw_priority = severity * (0.40 + 0.60 * confidence)."""
        # Severity = 80, Confidence = 0.50 -> 80 * (0.40 + 0.30) = 80 * 0.70 = 56.0
        prio = calculate_priority(severity=80.0, confidence=0.50)
        assert prio.raw_priority == pytest.approx(56.0, rel=1e-5)
        assert prio.final_priority == pytest.approx(56.0, rel=1e-5)
        assert not prio.low_confidence_cap_applied

    def test_confidence_adjustment(self):
        """27. Higher confidence increases priority for the same severity."""
        prio_low = calculate_priority(severity=70.0, confidence=0.40)  # 70 * 0.64 = 44.8
        prio_high = calculate_priority(severity=70.0, confidence=0.90)  # 70 * 0.94 = 65.8
        assert prio_high.final_priority > prio_low.final_priority

    def test_low_confidence_cap(self):
        """28. If confidence < 0.30, priority is capped at 69 (Medium priority)."""
        # Under normal formula: severity=100, confidence=0.20 -> 100 * (0.40 + 0.12) = 52.0 <= 69
        # Test case where raw_priority would exceed 69 if not capped:
        # Suppose confidence is 0.25: multiplier is 0.40 + 0.15 = 0.55.
        # But if raw_priority could reach > 69 (e.g. simulated extreme or direct cap check):
        # We test that any priority with confidence < 0.30 never exceeds 69.
        for sev in [50, 70, 85, 100]:
            prio = calculate_priority(severity=sev, confidence=0.20)
            assert prio.final_priority <= float(PRIORITY_LOW_CONFIDENCE_CAP)
            assert prio.urgency in ("low", "medium")

    def test_boundary_39_is_low(self):
        """29. Priority of 39.999 maps to 'low' urgency band."""
        assert determine_urgency_band(39.999) == "low"
        prio = calculate_priority(severity=39.999 / 0.40, confidence=0.0)  # raw 39.999
        assert prio.urgency == "low"

    def test_boundary_40_is_medium(self):
        """30. Priority of 40.0 maps to 'medium' urgency band."""
        assert determine_urgency_band(40.0) == "medium"

    def test_boundary_69_is_medium(self):
        """31. Priority of 69.999 maps to 'medium' urgency band."""
        assert determine_urgency_band(69.999) == "medium"

    def test_boundary_70_is_high(self):
        """32. Priority of 70.0 maps to 'high' urgency band."""
        assert determine_urgency_band(70.0) == "high"

    def test_boundary_84_is_high(self):
        """33. Priority of 84.999 maps to 'high' urgency band."""
        assert determine_urgency_band(84.999) == "high"

    def test_boundary_85_is_critical(self):
        """34. Priority of 85.0 maps to 'critical' urgency band."""
        assert determine_urgency_band(85.0) == "critical"
        assert determine_urgency_band(100.0) == "critical"


# ============================================================================
# 5. EXPLAINABILITY TESTS (Tests 35–39)
# ============================================================================

class TestExplainability:
    """Verify structured explainability outputs for responders."""

    def test_severity_evidence_preserved(self):
        """35. Severity breakdown includes verbatim extracted evidence."""
        rep = {"id": "R001", "raw_text": "severe flood water rising fast"}
        exts = [
            ExtractionField(field_name="incident_type", value="flood", confidence=0.90, evidence=["flood water"]),
            ExtractionField(field_name="severity_hint", value="severe", confidence=0.85, evidence=["severe flood"]),
        ]
        sev = calculate_severity([rep], extractions_by_report={"R001": exts})
        assert "flood water" in sev.evidence
        assert "severe flood" in sev.evidence

    def test_severity_modifiers_detailed(self):
        """36. Modifiers list includes name, bonus, and specific evidence."""
        rep = {"id": "R001", "raw_text": "flood with children trapped"}
        exts = [
            ExtractionField(field_name="incident_type", value="flood", confidence=0.90, evidence=["flood"]),
            ExtractionField(field_name="people_estimate", value={"count": 2, "category": "trapped"}, evidence=["children trapped"]),
            ExtractionField(field_name="vulnerable_persons", value=True, evidence=["children"]),
        ]
        sev = calculate_severity([rep], extractions_by_report={"R001": exts})
        mod_names = {m.name for m in sev.modifiers}
        assert "trapped_modifier" in mod_names
        assert "vulnerable_modifier" in mod_names

    def test_confidence_five_components_exposed(self):
        """37. Confidence breakdown contains all five explainable components with details."""
        reps = [{"id": "R001", "source": "sms"}, {"id": "R002", "source": "whatsapp"}]
        conf = calculate_confidence(reps)
        assert "source_count" in conf.breakdown
        assert "source_diversity" in conf.breakdown
        assert "consistency" in conf.breakdown
        assert "extraction_quality" in conf.breakdown
        assert "information_type" in conf.breakdown
        for comp in conf.breakdown.values():
            assert comp.detail
            assert comp.weight > 0

    def test_priority_formula_exposed(self):
        """38. Priority explanation exposes exact formula with numerical inputs."""
        prio = calculate_priority(severity=75.0, confidence=0.60)
        assert "75.0" in prio.formula
        assert "0.60" in prio.formula
        assert "0.40" in prio.formula
        assert "0.60" in prio.formula

    def test_low_confidence_cap_flag(self):
        """39. Low-confidence cap status is explicitly flagged in PriorityBreakdown."""
        prio_normal = calculate_priority(severity=80.0, confidence=0.50)
        assert not prio_normal.low_confidence_cap_applied

        prio_low = calculate_priority(severity=80.0, confidence=0.20)
        # Even if not exceeding 69, boolean field exists and is False or True
        assert isinstance(prio_low.low_confidence_cap_applied, bool)


# ============================================================================
# 6. DETERMINISM TESTS (Tests 40–41)
# ============================================================================

class TestDeterminism:
    """Verify that calculations are completely deterministic and order-independent."""

    def test_repeated_input_identical(self):
        """40. Calling assess_incident multiple times with identical inputs produces identical results."""
        rep = {"id": "R001", "raw_text": "flood with 3 people trapped", "source": "whatsapp"}
        a1 = assess_incident("INC-001", [rep])
        a2 = assess_incident("INC-001", [rep])
        assert a1.severity.final_severity == a2.severity.final_severity
        assert a1.confidence.final == a2.confidence.final
        assert a1.priority.final_priority == a2.priority.final_priority
        assert a1.urgency == a2.urgency

    def test_shuffled_reports_identical(self):
        """41. Shuffled report list produces identical assessment."""
        r1 = {"id": "R001", "raw_text": "flood in civil lines", "source": "sms"}
        r2 = {"id": "R002", "raw_text": "rescue needed 2 trapped", "source": "field_worker"}
        r3 = {"id": "R003", "raw_text": "waterlogging passable", "source": "web"}

        a_forward = assess_incident("INC-001", [r1, r2, r3])
        a_reverse = assess_incident("INC-001", [r3, r1, r2])
        assert a_forward.severity.final_severity == a_reverse.severity.final_severity
        assert a_forward.confidence.final == a_reverse.confidence.final
        assert a_forward.priority.final_priority == a_reverse.priority.final_priority
        assert a_forward.urgency == a_reverse.urgency


# ============================================================================
# 7. INTEGRATION TESTS (Tests 42–45)
# ============================================================================

class TestIntegration:
    """Verify integration across Phase 5 clusters, Phase 6 contradictions, and ORM persistence."""

    def test_phase_5_candidate_incident_assessment(self):
        """42. P5 candidate incident can be assessed end-to-end."""
        reps = [
            {"id": "R001", "raw_text": "severe flood near civil lines", "source": "whatsapp"},
            {"id": "R002", "raw_text": "water level high civil lines area", "source": "sms"},
        ]
        assessment = assess_incident("INC-001", reps)
        assert assessment.incident_id == "INC-001"
        assert assessment.severity.final_severity > 0.0
        assert assessment.confidence.final > 0.0
        assert assessment.priority.final_priority > 0.0
        assert assessment.urgency in ("critical", "high", "medium", "low")

    def test_phase_6_contradictions_lower_confidence(self):
        """43. Presence of P6 contradictions lowers confidence."""
        reps = [
            {"id": "R001", "raw_text": "2 people trapped", "source": "whatsapp"},
            {"id": "R002", "raw_text": "5 people trapped", "source": "sms"},
        ]
        # Baseline without contradictions
        assessment_no_conflict = assess_incident("INC-001", reps, contradictions=[])

        # With 1 contradiction from P6
        c1 = ContradictionResult(
            id="C1", incident_id="INC-001", contradiction_type="numeric", field="people_estimate",
            side_a=ContradictionSide(report_id="R001", value="2"),
            side_b=ContradictionSide(report_id="R002", value="5"),
        )
        assessment_conflict = assess_incident("INC-001", reps, contradictions=[c1])

        assert assessment_conflict.confidence.final < assessment_no_conflict.confidence.final
        assert assessment_conflict.confidence.consistency == 0.75
        assert assessment_no_conflict.confidence.consistency == 1.00

    def test_phase_6_contradictions_do_not_lower_severity(self):
        """44. P6 contradictions reduce confidence, but do NOT reduce severity."""
        reps = [
            {"id": "R001", "raw_text": "critical flood, 2 people trapped", "source": "whatsapp"},
            {"id": "R002", "raw_text": "critical flood, 5 people trapped", "source": "sms"},
        ]
        c1 = ContradictionResult(
            id="C1", incident_id="INC-001", contradiction_type="numeric", field="people_estimate",
            side_a=ContradictionSide(report_id="R001", value="2"),
            side_b=ContradictionSide(report_id="R002", value="5"),
        )
        assessment_no_conflict = assess_incident("INC-001", reps, contradictions=[])
        assessment_conflict = assess_incident("INC-001", reps, contradictions=[c1])

        # Severity must remain identical!
        assert assessment_conflict.severity.final_severity == assessment_no_conflict.severity.final_severity
        # But priority decreases because confidence decreased!
        assert assessment_conflict.priority.final_priority < assessment_no_conflict.priority.final_priority

    def test_database_persistence_roundtrip(self):
        """45. Persisting assessment updates Incident ORM record without corrupting other fields."""
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        session = Session()

        # Insert pre-existing incident
        inc = Incident(
            id="INC-001",
            status="unverified",
            title="Candidate Incident INC-001",
            location_resolved="Civil Lines",
        )
        session.add(inc)
        session.commit()

        # Assess and persist
        reps = [{"id": "R001", "raw_text": "flood in civil lines", "source": "whatsapp"}]
        assessment = assess_incident("INC-001", reps)
        persisted = persist_incident_assessment(session, assessment)
        session.commit()

        # Query back
        reloaded = session.query(Incident).filter(Incident.id == "INC-001").first()
        assert reloaded is not None
        assert reloaded.severity == assessment.severity.final_severity
        assert reloaded.confidence == assessment.confidence.final
        assert reloaded.priority == assessment.priority.final_priority
        assert reloaded.urgency == assessment.urgency
        assert reloaded.status == "unverified"  # Untouched
        assert reloaded.location_resolved == "Civil Lines"  # Untouched
        session.close()


# ============================================================================
# 8. SAFETY TESTS (Tests 46–50)
# ============================================================================

class TestSafety:
    """Verify bounds, missing fields, and non-operational safety guarantees."""

    def test_missing_fields_handled_gracefully(self):
        """46. Empty reports and missing attributes do not crash the assessor."""
        empty_rep = {}
        sev = calculate_severity([empty_rep])
        assert sev.final_severity == 0.0

        conf = calculate_confidence([])
        assert conf.final == 0.0

        prio = calculate_priority(0.0, 0.0)
        assert prio.final_priority == 0.0
        assert prio.urgency == "low"

    def test_negative_score_protection(self):
        """47. Negative inputs are safely clamped to 0.0."""
        prio = calculate_priority(severity=-20.0, confidence=-0.50)
        assert prio.severity == 0.0
        assert prio.confidence == 0.0
        assert prio.final_priority == 0.0

    def test_over_100_protection(self):
        """48. Values over 100 are safely clamped to 100."""
        prio = calculate_priority(severity=150.0, confidence=2.5)
        assert prio.severity == 100.0
        assert prio.confidence == 1.0
        assert prio.final_priority == 100.0

    def test_no_invented_people_counts(self):
        """49. Assessor preserves qualitative facts without fabricating numeric counts."""
        rep = {"id": "R001", "raw_text": "several trapped people in building"}
        exts = [
            ExtractionField(field_name="incident_type", value="flood", confidence=0.80),
            ExtractionField(field_name="trapped_or_rescue", value=True, confidence=0.90, evidence=["several trapped people"]),
        ]
        sev = calculate_severity([rep], extractions_by_report={"R001": exts})
        # Verifies trapped modifier is applied with exact phrase
        modifier = next(m for m in sev.modifiers if m.name == "trapped_modifier")
        assert "several trapped people" in modifier.evidence[0]
        # No artificial count attribute generated in assessment
        assert not hasattr(sev, "count")

    def test_no_automatic_responder_decision(self):
        """50. Assessor leaves incident status as unverified; responder remains decision maker."""
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        session = Session()

        inc = Incident(id="INC-999", status="unverified", title="Critical Cluster")
        session.add(inc)
        session.commit()

        # Multiple reports with high corroboration produce critical urgency
        reps = [
            {"id": "R001", "raw_text": "critical rescue needed immediately", "source": "whatsapp"},
            {"id": "R002", "raw_text": "rescue needed trapped people", "source": "sms"},
            {"id": "R003", "raw_text": "critical situation", "source": "field_worker"},
            {"id": "R004", "raw_text": "immediate help", "source": "web"},
        ]
        exts_map = {
            "R001": [ExtractionField(field_name="incident_type", value="rescue_needed", confidence=0.95, evidence=["rescue needed"]), ExtractionField(field_name="info_type", value="explicit", confidence=0.95, evidence=["confirmed"])],
            "R002": [ExtractionField(field_name="incident_type", value="rescue_needed", confidence=0.95, evidence=["rescue needed"]), ExtractionField(field_name="info_type", value="explicit", confidence=0.95, evidence=["on site"])],
            "R003": [ExtractionField(field_name="severity_hint", value="critical", confidence=0.95, evidence=["critical"]), ExtractionField(field_name="info_type", value="explicit", confidence=0.95, evidence=["field_worker"])],
            "R004": [ExtractionField(field_name="incident_type", value="rescue_needed", confidence=0.95, evidence=["rescue needed"]), ExtractionField(field_name="info_type", value="explicit", confidence=0.95, evidence=["confirmed"])],
        }
        assessment = assess_incident("INC-999", reps, extractions_by_report=exts_map)
        assert assessment.severity.final_severity >= 85.0
        assert assessment.urgency == "critical"

        persist_incident_assessment(session, assessment)
        session.commit()

        reloaded = session.query(Incident).filter(Incident.id == "INC-999").first()
        # Status MUST remain unverified: human responder decides verification
        assert reloaded.status == "unverified"
        assert reloaded.urgency == "critical"
        session.close()


# ============================================================================
# 9. MANDATORY AUDIT EDGE TESTS
# ============================================================================

class TestMandatoryAuditEdges:
    """Explicit verification of high-risk edge cases from the Phase 7 audit."""

    def test_generic_rescue_needed_does_not_trigger_trapped_modifier(self):
        """Generic 'rescue needed' produces baseline 85 and does NOT add +15 trapped modifier."""
        rep = {"id": "R001", "raw_text": "rescue needed immediately in central area"}
        exts = [
            ExtractionField(field_name="incident_type", value="rescue_needed", confidence=0.90, evidence=["rescue needed"]),
            ExtractionField(field_name="trapped_or_rescue", value=True, confidence=0.90, evidence=["rescue needed"]),
        ]
        sev = calculate_severity([rep], extractions_by_report={"R001": exts})
        assert sev.base_type == "rescue_needed"
        assert sev.baseline_severity == 85.0
        # No trapped modifier because evidence has no trapped/stuck/stranded persons
        assert not any(m.name == "trapped_modifier" for m in sev.modifiers)
        assert sev.final_severity == 85.0

    def test_critical_keyword_beats_lower_type(self):
        """Explicit 'critical' keyword (90) overrides lower incident type waterlogging (40)."""
        rep = {"id": "R001", "raw_text": "critical waterlogging on streets"}
        exts = [
            ExtractionField(field_name="incident_type", value="waterlogging", confidence=0.85, evidence=["waterlogging"]),
            ExtractionField(field_name="severity_hint", value="critical", confidence=0.90, evidence=["critical"]),
        ]
        sev = calculate_severity([rep], extractions_by_report={"R001": exts})
        assert sev.base_type == "waterlogging"
        assert sev.base_type_score == 40.0
        assert sev.keyword == "critical"
        assert sev.keyword_score == 90.0
        assert sev.baseline_severity == 90.0  # max(40, 90)
        assert sev.final_severity == 90.0

    def test_multiple_trapped_reports_only_add_modifier_once(self):
        """Five reports all stating 'trapped' only add +15 ONCE per incident, never +75."""
        reps = [
            {"id": f"R00{i}", "raw_text": f"person trapped in house {i}", "source": "whatsapp"}
            for i in range(1, 6)
        ]
        exts_map = {
            f"R00{i}": [
                ExtractionField(field_name="incident_type", value="flood", confidence=0.85, evidence=["flood"]),
                ExtractionField(field_name="people_estimate", value={"count": 1, "category": "trapped"}, evidence=["person trapped"]),
            ]
            for i in range(1, 6)
        }
        sev = calculate_severity(reps, extractions_by_report=exts_map)
        trapped_mods = [m for m in sev.modifiers if m.name == "trapped_modifier"]
        assert len(trapped_mods) == 1
        assert trapped_mods[0].bonus == 15.0
        assert sev.final_severity == 80.0  # 65 + 15 = 80, not 65 + 75

    def test_vulnerable_generic_family_person_resident_rejected(self):
        """Generic terms 'family', 'resident', 'person', 'people' do NOT trigger vulnerable modifier."""
        rep = {"id": "R001", "raw_text": "large family and local residents affected by water"}
        exts = [
            ExtractionField(field_name="incident_type", value="flood", confidence=0.85, evidence=["flood"]),
            ExtractionField(field_name="vulnerable_persons", value=True, confidence=0.70, evidence=["large family", "local residents"]),
        ]
        sev = calculate_severity([rep], extractions_by_report={"R001": exts})
        assert not any(m.name == "vulnerable_modifier" for m in sev.modifiers)
        assert sev.final_severity == 65.0

    def test_corroboration_does_not_increase_severity(self):
        """10 duplicate reports of waterlogging have the same severity as 1 report (40.0)."""
        single_rep = [{"id": "R001", "raw_text": "waterlogging on road", "source": "sms"}]
        ten_reps = [
            {"id": f"R{i:03d}", "raw_text": "waterlogging on road", "source": "sms"}
            for i in range(1, 11)
        ]
        exts_single = {"R001": [ExtractionField(field_name="incident_type", value="waterlogging", confidence=0.85)]}
        exts_ten = {
            f"R{i:03d}": [ExtractionField(field_name="incident_type", value="waterlogging", confidence=0.85)]
            for i in range(1, 11)
        }
        sev_1 = calculate_severity(single_rep, extractions_by_report=exts_single)
        sev_10 = calculate_severity(ten_reps, extractions_by_report=exts_ten)
        assert sev_1.final_severity == 40.0
        assert sev_10.final_severity == 40.0

    def test_contradiction_output_and_duplicates_deduplicated(self):
        """Duplicate representations of the same contradiction are counted only once in consistency."""
        reps = [{"id": "R001", "source": "sms"}, {"id": "R002", "source": "whatsapp"}]
        c1 = ContradictionResult(
            id="C1", incident_id="INC-001", contradiction_type="numeric", field="people_estimate",
            side_a=ContradictionSide(report_id="R001", value="2"),
            side_b=ContradictionSide(report_id="R002", value="5"),
        )
        # Duplicate of c1 with swapped sides
        c1_dup = ContradictionResult(
            id="C1_DUP", incident_id="INC-001", contradiction_type="numeric", field="people_estimate",
            side_a=ContradictionSide(report_id="R002", value="5"),
            side_b=ContradictionSide(report_id="R001", value="2"),
        )
        # Wrap inside ContradictionOutput
        contr_output = ContradictionOutput(incident_id="INC-001", contradictions=[c1, c1_dup], total_contradictions=2)

        conf = calculate_confidence(reps, contradictions=contr_output)
        # Exactly 1 logical conflict -> score 0.75, not 0.50
        assert conf.consistency == 0.75

    def test_confidence_exactly_0_30_does_not_cap(self):
        """At confidence == 0.30, the low-confidence cap (< 0.30) MUST NOT apply."""
        # Severity = 100, confidence = 0.30
        # raw_priority = 100 * (0.40 + 0.60 * 0.30) = 100 * 0.58 = 58.0
        # If cap applied, it would flag low_confidence_cap_applied; at exactly 0.30, cap does not trigger.
        prio = calculate_priority(severity=100.0, confidence=0.30)
        assert prio.raw_priority == pytest.approx(58.0, rel=1e-5)
        assert not prio.low_confidence_cap_applied
        assert prio.final_priority == pytest.approx(58.0, rel=1e-5)

    def test_zero_evidence_backed_extraction_fallback_0_20(self):
        """When an incident has zero evidence-backed fields, extraction quality is exactly 0.20."""
        rep = {"id": "R001", "source": "sms"}
        # All extractions are empty placeholders
        exts = [
            ExtractionField(field_name="incident_type", value=None, confidence=0.0, evidence=[]),
            ExtractionField(field_name="severity_hint", value=None, confidence=0.0, evidence=[]),
        ]
        conf = calculate_confidence([rep], extractions_by_report={"R001": exts})
        assert conf.extraction_quality == 0.20
        assert "default baseline 0.20" in conf.extraction_detail

    def test_ordinary_english_word_phase_does_not_trigger_trapped_modifier(self):
        """Ordinary English sentences containing 'phase' MUST NOT trigger trapped modifier (+15)."""
        rep = {
            "id": "R001",
            "raw_text": "During the initial phase of flood response, rescue needed immediately",
            "source": "web",
        }
        exts = [
            ExtractionField(field_name="incident_type", value="rescue_needed", confidence=0.90, evidence=["rescue needed"]),
            ExtractionField(field_name="trapped_or_rescue", value=True, confidence=0.80, evidence=["initial phase of flood response", "rescue needed"]),
        ]
        sev = calculate_severity([rep], extractions_by_report={"R001": exts})
        assert sev.base_type == "rescue_needed"
        assert sev.baseline_severity == 85.0
        # Standalone 'phase' does NOT trigger trapped_modifier
        assert not any(m.name == "trapped_modifier" for m in sev.modifiers)
        assert sev.final_severity == 85.0

    def test_romanized_hindi_log_phase_and_phaas_trigger_trapped_modifier(self):
        """Legitimate Romanized Hindi 'log phase' and 'phaas' correctly trigger trapped modifier (+15)."""
        rep_hi1 = {
            "id": "R001",
            "raw_text": "paani bhara hai aur 3 log phase hain",
            "source": "whatsapp",
        }
        exts1 = [
            ExtractionField(field_name="incident_type", value="flood", confidence=0.85, evidence=["paani bhara"]),
            ExtractionField(field_name="trapped_or_rescue", value=True, confidence=0.85, evidence=["log phase"]),
        ]
        sev1 = calculate_severity([rep_hi1], extractions_by_report={"R001": exts1})
        assert any(m.name == "trapped_modifier" for m in sev1.modifiers)
        assert sev1.final_severity == 80.0  # 65 + 15

        rep_hi2 = {
            "id": "R002",
            "raw_text": "ghar mein phaas gaye",
            "source": "whatsapp",
        }
        exts2 = [
            ExtractionField(field_name="incident_type", value="flood", confidence=0.85, evidence=["ghar mein"]),
            ExtractionField(field_name="trapped_or_rescue", value=True, confidence=0.85, evidence=["phaas"]),
        ]
        sev2 = calculate_severity([rep_hi2], extractions_by_report={"R002": exts2})
        assert any(m.name == "trapped_modifier" for m in sev2.modifiers)
        assert sev2.final_severity == 80.0  # 65 + 15
