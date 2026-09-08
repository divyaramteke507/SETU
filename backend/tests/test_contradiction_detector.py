"""
SETU Contradiction Detector Tests — Phase 6.

Comprehensive tests for:
- Numeric contradictions (same category only, exact vs range, category isolation)
- Severity contradictions (explicit severity concepts: critical/severe vs minor)
- Incident type contradictions (mutually exclusive types via config mapping)
- Location contradictions (resolved distinct locations in same incident; unresolved ignored)
- Hazard / structural contradictions (structural integrity: damaged vs intact; access: blocked vs passable)
- Strict absence-as-negation protection (omission is never negation)
- Canonical duplicate prevention (A-B and B-A produce 1 contradiction)
- Multi-field conflict preservation (same pair with distinct fields yields separate records)
- Deterministic detection under report shuffling
- Resolution state verification (strictly null until human resolves)
- Exact source evidence preservation on both sides
- Database persistence roundtrip and idempotency
- Incident isolation (reports in different incidents are never compared)
- Real Rampur flood seed reports integration
"""

import os
import sys
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Ensure backend/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from database import Base
from models import Contradiction, Incident, Report
from schemas import (
    ContradictionOut,
    ContradictionOutput,
    ContradictionResult,
    ContradictionSide,
    ExtractionField,
)
from services.contradiction_detector import (
    check_hazard_structural_contradictions,
    check_incident_type_contradictions,
    check_location_contradictions,
    check_numeric_contradictions,
    check_severity_contradictions,
    detect_all_contradictions,
    detect_incident_contradictions,
    detect_pairwise_contradictions,
    persist_contradictions,
)


# ============================================================================
# 1. NUMERIC CONTRADICTIONS
# ============================================================================

class TestNumericContradictions:
    """Test numeric conflict detection, category isolation, and range handling."""

    def test_numeric_conflict_same_category_exact_counts(self):
        """1. 2 trapped vs 5 trapped must be flagged as a numeric contradiction."""
        ext_a = [
            ExtractionField(
                field_name="people_estimate",
                value={"count": 2, "category": "trapped"},
                confidence=0.85,
                evidence=["two people are trapped"],
            )
        ]
        ext_b = [
            ExtractionField(
                field_name="people_estimate",
                value={"count": 5, "category": "trapped"},
                confidence=0.85,
                evidence=["five people trapped"],
            )
        ]

        contrs = check_numeric_contradictions("R001", "R002", ext_a, ext_b, incident_id="INC-001")
        assert len(contrs) == 1
        c = contrs[0]
        assert c.contradiction_type == "numeric"
        assert c.field == "people_trapped"
        assert c.side_a.value == {"count": 2, "category": "trapped"}
        assert c.side_b.value == {"count": 5, "category": "trapped"}
        assert c.side_a.evidence == ["two people are trapped"]
        assert c.side_b.evidence == ["five people trapped"]
        assert c.resolution is None

    def test_numeric_category_isolation_no_contradiction(self):
        """2. 2 trapped vs 5 nearby must NOT be flagged as a contradiction."""
        ext_a = [
            ExtractionField(
                field_name="people_estimate",
                value={"count": 2, "category": "trapped"},
                confidence=0.85,
                evidence=["2 people trapped"],
            )
        ]
        ext_b = [
            ExtractionField(
                field_name="people_estimate",
                value={"count": 5, "category": "nearby"},
                confidence=0.85,
                evidence=["5 people nearby"],
            )
        ]

        contrs = check_numeric_contradictions("R001", "R002", ext_a, ext_b, incident_id="INC-001")
        assert len(contrs) == 0, "Different people-count categories must not be compared"

    def test_numeric_same_count_no_contradiction(self):
        """Identical counts in same category must not contradict."""
        ext_a = [
            ExtractionField(
                field_name="people_estimate",
                value={"count": 3, "category": "trapped"},
                confidence=0.85,
                evidence=["3 trapped"],
            )
        ]
        ext_b = [
            ExtractionField(
                field_name="people_estimate",
                value={"count": 3, "category": "trapped"},
                confidence=0.85,
                evidence=["3 people trapped"],
            )
        ]

        contrs = check_numeric_contradictions("R001", "R002", ext_a, ext_b, incident_id="INC-001")
        assert len(contrs) == 0

    def test_numeric_exact_vs_range_compatible_no_contradiction(self):
        """3a. Exact count inside a range (e.g. 4 vs 2-5) is compatible (no contradiction)."""
        ext_a = [
            ExtractionField(
                field_name="people_estimate",
                value={"count": 4, "category": "affected"},
                confidence=0.80,
                evidence=["4 residents affected"],
            )
        ]
        ext_b = [
            ExtractionField(
                field_name="people_estimate",
                value={"count_min": 2, "count_max": 5, "category": "affected"},
                confidence=0.75,
                evidence=["2-5 people affected"],
            )
        ]

        contrs = check_numeric_contradictions("R001", "R002", ext_a, ext_b, incident_id="INC-001")
        assert len(contrs) == 0

    def test_numeric_exact_vs_range_outside_contradiction(self):
        """3b. Exact count outside a range (e.g. 8 vs 2-5) must be flagged as a contradiction."""
        ext_a = [
            ExtractionField(
                field_name="people_estimate",
                value={"count": 8, "category": "affected"},
                confidence=0.80,
                evidence=["8 residents affected"],
            )
        ]
        ext_b = [
            ExtractionField(
                field_name="people_estimate",
                value={"count_min": 2, "count_max": 5, "category": "affected"},
                confidence=0.75,
                evidence=["2-5 people affected"],
            )
        ]

        contrs = check_numeric_contradictions("R001", "R002", ext_a, ext_b, incident_id="INC-001")
        assert len(contrs) == 1
        assert contrs[0].contradiction_type == "numeric"

    def test_numeric_non_overlapping_ranges_contradiction(self):
        """Non-overlapping ranges (e.g. 1-2 vs 5-6) must be flagged as a contradiction."""
        ext_a = [
            ExtractionField(
                field_name="people_estimate",
                value={"count_min": 1, "count_max": 2, "category": "affected"},
                confidence=0.75,
                evidence=["1-2 residents"],
            )
        ]
        ext_b = [
            ExtractionField(
                field_name="people_estimate",
                value={"count_min": 5, "count_max": 6, "category": "affected"},
                confidence=0.75,
                evidence=["5-6 residents"],
            )
        ]

        contrs = check_numeric_contradictions("R001", "R002", ext_a, ext_b, incident_id="INC-001")
        assert len(contrs) == 1


# ============================================================================
# 2. SEVERITY CONTRADICTIONS (EXPLICIT CONCEPTS ONLY)
# ============================================================================

class TestSeverityContradictions:
    """Test explicit severity concept conflicts (critical/severe vs minor)."""

    def test_severity_critical_vs_minor_contradiction(self):
        """5. Explicit 'critical' vs 'minor' is flagged as a severity contradiction."""
        ext_a = [
            ExtractionField(
                field_name="severity_hint",
                value="critical",
                confidence=0.90,
                evidence=["situation is critical"],
            )
        ]
        ext_b = [
            ExtractionField(
                field_name="severity_hint",
                value="minor",
                confidence=0.85,
                evidence=["minor flooding only"],
            )
        ]

        contrs = check_severity_contradictions("R001", "R002", ext_a, ext_b, incident_id="INC-001")
        assert len(contrs) == 1
        c = contrs[0]
        assert c.contradiction_type == "severity"
        assert c.field == "severity_hint"
        assert c.side_a.value == "critical"
        assert c.side_b.value == "minor"

    def test_severity_severe_vs_minor_contradiction(self):
        """Explicit 'severe' vs 'minor' is flagged as a severity contradiction."""
        ext_a = [
            ExtractionField(
                field_name="severity_hint",
                value="severe",
                confidence=0.90,
                evidence=["severe conditions"],
            )
        ]
        ext_b = [
            ExtractionField(
                field_name="severity_hint",
                value="minor",
                confidence=0.85,
                evidence=["minor flooding"],
            )
        ]

        contrs = check_severity_contradictions("R001", "R002", ext_a, ext_b, incident_id="INC-001")
        assert len(contrs) == 1

    def test_severity_same_level_no_contradiction(self):
        """6. Same severity level ('critical' vs 'critical') is NOT a contradiction."""
        ext_a = [ExtractionField(field_name="severity_hint", value="critical", confidence=0.90, evidence=["critical"])]
        ext_b = [ExtractionField(field_name="severity_hint", value="critical", confidence=0.90, evidence=["urgent/critical"])]

        contrs = check_severity_contradictions("R001", "R002", ext_a, ext_b, incident_id="INC-001")
        assert len(contrs) == 0

    def test_severity_absence_is_not_negation(self):
        """A report with severity 'critical' and a report with no severity hint must NOT contradict."""
        ext_a = [ExtractionField(field_name="severity_hint", value="critical", confidence=0.90, evidence=["critical"])]
        ext_b = []  # No severity hint

        contrs = check_severity_contradictions("R001", "R002", ext_a, ext_b, incident_id="INC-001")
        assert len(contrs) == 0


# ============================================================================
# 3. INCIDENT TYPE CONTRADICTIONS
# ============================================================================

class TestIncidentTypeContradictions:
    """Test mutually exclusive incident type detection."""

    def test_incident_type_same_type_no_contradiction(self):
        """7. Same incident type ('flood' vs 'flood') is NOT a contradiction."""
        ext_a = [ExtractionField(field_name="incident_type", value="flood", confidence=0.90, evidence=["flood"])]
        ext_b = [ExtractionField(field_name="incident_type", value="flood", confidence=0.85, evidence=["waterlogging"])]

        contrs = check_incident_type_contradictions("R001", "R002", ext_a, ext_b, incident_id="INC-001")
        assert len(contrs) == 0

    def test_incident_type_explicitly_incompatible_types_contradiction(self):
        """8. Explicitly incompatible types ('flood' vs 'drought') MUST contradict."""
        ext_a = [ExtractionField(field_name="incident_type", value="flood", confidence=0.90, evidence=["flooding"])]
        ext_b = [ExtractionField(field_name="incident_type", value="drought", confidence=0.90, evidence=["severe drought"])]

        contrs = check_incident_type_contradictions("R001", "R002", ext_a, ext_b, incident_id="INC-001")
        assert len(contrs) == 1
        assert contrs[0].contradiction_type == "incident_type"

    def test_incident_type_co_occurring_types_no_contradiction(self):
        """9. Complementary co-occurring disaster types ('flood' and 'road_blocked') do NOT contradict."""
        ext_a = [ExtractionField(field_name="incident_type", value="flood", confidence=0.90, evidence=["flooding"])]
        ext_b = [ExtractionField(field_name="incident_type", value="road_blocked", confidence=0.85, evidence=["road blocked"])]

        contrs = check_incident_type_contradictions("R001", "R002", ext_a, ext_b, incident_id="INC-001")
        assert len(contrs) == 0


# ============================================================================
# 4. LOCATION CONTRADICTIONS
# ============================================================================

class TestLocationContradictions:
    """Test location conflicts for resolved distinct gazetteer places within the same incident."""

    def test_location_distinct_resolved_places_contradiction(self):
        """10. Two reports in the same incident resolved to different gazetteer locations contradict."""
        rep_a = {"id": "R001", "location_resolved": "civil_lines", "raw_text": "Flooding at Civil Lines"}
        rep_b = {"id": "R002", "location_resolved": "bilaspur_chowk", "raw_text": "Flooding at Bilaspur Chowk"}

        contrs = check_location_contradictions(rep_a, rep_b, incident_id="INC-001")
        assert len(contrs) == 1
        c = contrs[0]
        assert c.contradiction_type == "location"
        assert c.side_a.value == "civil_lines"
        assert c.side_b.value == "bilaspur_chowk"

    def test_location_same_resolved_place_no_contradiction(self):
        """11. Reports resolved to the same gazetteer location do NOT contradict."""
        rep_a = {"id": "R001", "location_resolved": "civil_lines", "raw_text": "Flooding at Civil Lines"}
        rep_b = {"id": "R002", "location_resolved": "civil_lines", "raw_text": "Heavy water in Civil Lines"}

        contrs = check_location_contradictions(rep_a, rep_b, incident_id="INC-001")
        assert len(contrs) == 0

    def test_location_unresolved_location_never_invents_contradiction(self):
        """12. If either report has an unresolved location, NO contradiction is created."""
        rep_a = {"id": "R001", "location_resolved": "civil_lines", "raw_text": "Civil Lines area"}
        rep_b = {"id": "R002", "location_resolved": None, "raw_text": "Water rising near market"}

        contrs = check_location_contradictions(rep_a, rep_b, incident_id="INC-001")
        assert len(contrs) == 0


# ============================================================================
# 5. HAZARD / STRUCTURAL CONTRADICTIONS
# ============================================================================

class TestHazardStructuralContradictions:
    """Test structural integrity claims and access/road-state claims under HAZARD_STRUCTURAL."""

    def test_structural_damaged_vs_intact_contradiction(self):
        """13. Wall collapsed/cracked vs Wall intact is flagged under hazard_structural."""
        rep_a = {
            "id": "R001",
            "raw_text": "Boundary wall showing significant cracks and leaning, risk of collapse is high.",
        }
        rep_b = {
            "id": "R002",
            "raw_text": "Inspected boundary wall and reports the wall appears fully intact with no visible cracks.",
        }

        contrs = check_hazard_structural_contradictions(rep_a, rep_b, [], [], incident_id="INC-001")
        assert len(contrs) == 1
        c = contrs[0]
        assert c.contradiction_type == "hazard_structural"
        assert c.field == "structural_integrity"

    def test_structural_damaged_vs_no_mention_no_contradiction(self):
        """14. Damaged wall vs report that makes no mention of wall must NOT contradict."""
        rep_a = {
            "id": "R001",
            "raw_text": "Boundary wall is leaning, risk of collapse.",
        }
        rep_b = {
            "id": "R002",
            "raw_text": "Water entered ground floor, power cut since morning.",
        }

        contrs = check_hazard_structural_contradictions(rep_a, rep_b, [], [], incident_id="INC-001")
        assert len(contrs) == 0

    def test_access_road_blocked_vs_passable_contradiction(self):
        """24. Road completely blocked vs vehicles can pass slowly is classified under HAZARD_STRUCTURAL."""
        rep_a = {
            "id": "R011",
            "raw_text": "Road completely blocked near Bilaspur Chowk due to heavy waterlogging. Traffic at standstill.",
        }
        rep_b = {
            "id": "R015",
            "raw_text": "Bilaspur Chowk area waterlogged. Vehicles can pass slowly on side road.",
        }

        contrs = check_hazard_structural_contradictions(rep_a, rep_b, [], [], incident_id="INC-001")
        assert len(contrs) == 1
        c = contrs[0]
        assert c.contradiction_type == "hazard_structural"
        assert c.field == "road_access"
        assert c.side_a.value == "completely_blocked"
        assert c.side_b.value == "passable_slowly"

    def test_access_blocked_vs_water_rising_no_contradiction(self):
        """Road blocked vs report mentioning water rising without road mention must NOT contradict."""
        rep_a = {"id": "R001", "raw_text": "Road completely blocked near bridge."}
        rep_b = {"id": "R002", "raw_text": "Water level is rising rapidly."}

        contrs = check_hazard_structural_contradictions(rep_a, rep_b, [], [], incident_id="INC-001")
        assert len(contrs) == 0


# ============================================================================
# 6. ABSENCE-AS-NEGATION PROTECTION
# ============================================================================

class TestAbsenceAsNegationProtection:
    """Verify that omission of a fact is never treated as the opposite."""

    def test_trapped_vs_report_with_no_trapped_statement(self):
        """15. 3 people trapped vs report with no mention of trapped people -> no contradiction."""
        rep_a = {"id": "R001", "raw_text": "3 people are trapped on the rooftop."}
        rep_b = {"id": "R002", "raw_text": "Water is 3 feet deep on main road."}

        contrs = detect_pairwise_contradictions(rep_a, rep_b, incident_id="INC-001")
        assert len(contrs) == 0

    def test_blocked_road_vs_report_with_no_blockage_statement(self):
        """16. Blocked road vs report with no road blockage mention -> no contradiction."""
        rep_a = {"id": "R001", "raw_text": "Road completely blocked due to fallen tree."}
        rep_b = {"id": "R002", "raw_text": "Heavy rain continues across the district."}

        contrs = detect_pairwise_contradictions(rep_a, rep_b, incident_id="INC-001")
        assert len(contrs) == 0


# ============================================================================
# 7. DUPLICATE HANDLING & CANONICAL PAIRING
# ============================================================================

class TestDuplicateHandling:
    """Test canonical ordering and multi-field conflict preservation."""

    def test_canonical_pairing_a_b_and_b_a_produce_single_record(self):
        """17. Comparing (A, B) and (B, A) results in strictly one contradiction with side_a=A, side_b=B."""
        rep_a = {"id": "R001", "raw_text": "2 people trapped"}
        rep_b = {"id": "R007", "raw_text": "5 people trapped"}

        contrs_ab = detect_pairwise_contradictions(rep_a, rep_b, incident_id="INC-001")
        contrs_ba = detect_pairwise_contradictions(rep_b, rep_a, incident_id="INC-001")

        assert len(contrs_ab) == 1
        assert len(contrs_ba) == 1
        assert contrs_ab[0].id == contrs_ba[0].id
        assert contrs_ab[0].side_a.report_id == "R001"
        assert contrs_ab[0].side_b.report_id == "R007"

    def test_same_pair_distinct_conflicting_fields_yields_separate_records(self):
        """18. Same report pair with both a numeric and a location contradiction yields 2 separate records."""
        rep_a = {
            "id": "R001",
            "raw_text": "2 people trapped in Civil Lines",
            "location_resolved": "civil_lines",
        }
        rep_b = {
            "id": "R002",
            "raw_text": "5 people trapped in Bilaspur Chowk",
            "location_resolved": "bilaspur_chowk",
        }

        contrs = detect_pairwise_contradictions(rep_a, rep_b, incident_id="INC-001")
        assert len(contrs) == 2
        types = {c.contradiction_type for c in contrs}
        assert types == {"numeric", "location"}


# ============================================================================
# 8. DETERMINISM
# ============================================================================

class TestDeterminism:
    """Test that contradiction detection is fully deterministic."""

    def test_shuffled_reports_produce_identical_output(self):
        """19. Shuffling report order within an incident produces identical contradiction records."""
        rep_1 = {"id": "R001", "raw_text": "2 people trapped", "location_resolved": "civil_lines"}
        rep_2 = {"id": "R002", "raw_text": "5 people trapped", "location_resolved": "civil_lines"}
        rep_3 = {"id": "R003", "raw_text": "Boundary wall leaning, risk of collapse", "location_resolved": "civil_lines"}
        rep_4 = {"id": "R004", "raw_text": "Boundary wall appears fully intact", "location_resolved": "civil_lines"}

        out1 = detect_incident_contradictions("INC-001", [rep_1, rep_2, rep_3, rep_4])
        out2 = detect_incident_contradictions("INC-001", [rep_4, rep_1, rep_3, rep_2])
        out3 = detect_incident_contradictions("INC-001", [rep_3, rep_2, rep_4, rep_1])

        ids1 = [c.id for c in out1.contradictions]
        ids2 = [c.id for c in out2.contradictions]
        ids3 = [c.id for c in out3.contradictions]

        assert ids1 == ids2 == ids3
        assert len(ids1) == 2  # 1 numeric (R001-R002) + 1 structural (R003-R004)


# ============================================================================
# 9. RESOLUTION & EVIDENCE PRESERVATION
# ============================================================================

class TestResolutionAndEvidence:
    """Test that resolution is always None and source evidence is preserved."""

    def test_newly_detected_contradictions_have_null_resolution(self):
        """20. Newly detected contradictions must have resolution=None (responder decision-maker)."""
        rep_a = {"id": "R001", "raw_text": "2 people trapped"}
        rep_b = {"id": "R002", "raw_text": "5 people trapped"}

        contrs = detect_pairwise_contradictions(rep_a, rep_b, incident_id="INC-001")
        assert len(contrs) == 1
        assert contrs[0].resolution is None

    def test_exact_source_evidence_preserved_on_both_sides(self):
        """21. Both sides must preserve exact source text evidence without paraphrase."""
        rep_a = {"id": "R001", "raw_text": "2 people trapped in basement"}
        rep_b = {"id": "R002", "raw_text": "5 people trapped in building"}

        contrs = detect_pairwise_contradictions(rep_a, rep_b, incident_id="INC-001")
        assert len(contrs) == 1
        c = contrs[0]
        assert "2 people trapped" in c.side_a.evidence[0]
        assert "5 people trapped" in c.side_b.evidence[0]


# ============================================================================
# 10. DATABASE PERSISTENCE & IDEMPOTENCY
# ============================================================================

class TestDatabasePersistence:
    """Test persisting contradictions into Contradiction ORM table."""

    @pytest.fixture
    def db_session(self):
        """Create fresh in-memory SQLite session with an Incident."""
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        session = Session()

        inc = Incident(id="INC-001", status="unverified", title="Test Incident")
        session.add(inc)
        session.commit()

        yield session

        session.close()

    def test_persistence_roundtrip(self, db_session):
        """22. Contradiction records persist to database and are queryable."""
        rep_a = {"id": "R001", "raw_text": "2 people trapped"}
        rep_b = {"id": "R002", "raw_text": "5 people trapped"}

        output = detect_incident_contradictions("INC-001", [rep_a, rep_b])
        assert output.total_contradictions == 1

        persisted = persist_contradictions(db_session, output)
        assert len(persisted) == 1

        db_records = db_session.query(Contradiction).all()
        assert len(db_records) == 1
        rec = db_records[0]
        assert rec.incident_id == "INC-001"
        assert rec.contradiction_type == "numeric"
        assert rec.field == "people_trapped"
        assert rec.resolution is None

    def test_repeated_persistence_is_idempotent_no_duplicates(self, db_session):
        """23. Calling persist_contradictions repeatedly does NOT insert duplicate records."""
        rep_a = {"id": "R001", "raw_text": "2 people trapped"}
        rep_b = {"id": "R002", "raw_text": "5 people trapped"}

        output = detect_incident_contradictions("INC-001", [rep_a, rep_b])
        persist_contradictions(db_session, output)

        # Second persistence call with same output
        second_call = persist_contradictions(db_session, output)
        assert len(second_call) == 0

        total_db_records = db_session.query(Contradiction).count()
        assert total_db_records == 1


# ============================================================================
# 11. INCIDENT ISOLATION
# ============================================================================

class TestIncidentIsolation:
    """Test that reports in different candidate incidents are never compared."""

    def test_reports_in_different_incidents_are_never_compared(self):
        """25. Reports belonging to distinct incidents must never be evaluated against each other."""
        rep_inc1_a = {"id": "R001", "raw_text": "2 people trapped"}
        rep_inc1_b = {"id": "R002", "raw_text": "Water rising"}

        rep_inc2_c = {"id": "R003", "raw_text": "5 people trapped"}

        # Even though R001 and R003 have 2 vs 5 trapped, they are in different incidents
        incidents = [
            ("INC-001", [rep_inc1_a, rep_inc1_b]),
            ("INC-002", [rep_inc2_c]),
        ]

        outputs = detect_all_contradictions(incidents)
        assert len(outputs) == 2
        assert outputs[0].total_contradictions == 0
        assert outputs[1].total_contradictions == 0


# ============================================================================
# 12. REAL RAMPUR FLOOD SEED REPORTS INTEGRATION
# ============================================================================

class TestRealSeedReportsContradictions:
    """Verify detection on the built-in contradiction scenarios from Rampur seed data."""

    def test_seed_cluster_2_kotwali_numeric_contradiction(self):
        """26. R008 (3 people trapped) vs R010 (5 members trapped) in Kotwali detects numeric conflict."""
        r008 = {
            "id": "R008",
            "source": "whatsapp",
            "raw_text": "Kotwali ke paas ek ghar mein 3 log phase hain. Paani bahut tez aa raha hai. Bachao!",
            "normalized_text": "Kotwali ke paas ek ghar mein 3 log phase hain. Paani bahut tez aa raha hai. Bachao!",
            "language": "hi-Latn",
            "location_resolved": "kotwali",
        }
        r010 = {
            "id": "R010",
            "source": "field_worker",
            "raw_text": (
                "Kotwali area: Confirmed 1 family of 5 members including 2 children and 1 elderly woman "
                "trapped in ground floor. Water level at 4 feet and rising."
            ),
            "normalized_text": (
                "Kotwali area: Confirmed 1 family of 5 members including 2 children and 1 elderly woman "
                "trapped in ground floor. Water level at 4 feet and rising."
            ),
            "language": "en",
            "location_resolved": "kotwali",
        }

        output = detect_incident_contradictions("INC-002", [r008, r010])
        assert output.total_contradictions >= 1
        num_c = next(c for c in output.contradictions if c.contradiction_type == "numeric")
        assert num_c.field == "people_trapped"
        assert num_c.side_a.report_id == "R008"
        assert num_c.side_b.report_id == "R010"

    def test_seed_cluster_3_bilaspur_access_contradiction(self):
        """27. R011 (completely blocked) vs R015 (vehicles pass slowly) in Bilaspur Chowk detects access conflict."""
        r011 = {
            "id": "R011",
            "source": "whatsapp",
            "raw_text": (
                "Road completely blocked near Bilaspur Chowk due to heavy waterlogging. "
                "Vehicles stuck in water. Traffic at standstill."
            ),
            "normalized_text": (
                "Road completely blocked near Bilaspur Chowk due to heavy waterlogging. "
                "Vehicles stuck in water. Traffic at standstill."
            ),
            "language": "en",
            "location_resolved": "bilaspur_chowk",
        }
        r015 = {
            "id": "R015",
            "source": "sms",
            "raw_text": "Bilaspur Chowk area waterlogged. Minor flooding only. Vehicles can pass slowly on side road.",
            "normalized_text": "Bilaspur Chowk area waterlogged. Minor flooding only. Vehicles can pass slowly on side road.",
            "language": "en",
            "location_resolved": "bilaspur_chowk",
        }

        output = detect_incident_contradictions("INC-003", [r011, r015])
        assert output.total_contradictions >= 1
        # Road access conflict under hazard_structural
        haz_c = next(c for c in output.contradictions if c.field == "road_access")
        assert haz_c.contradiction_type == "hazard_structural"
        assert haz_c.side_a.report_id == "R011"
        assert haz_c.side_b.report_id == "R015"

    def test_seed_cluster_4_naya_mohalla_structural_contradiction(self):
        """28. R018 (wall fully intact) vs R020 (wall significant cracks and leaning) in Naya Mohalla detects structural conflict."""
        r018 = {
            "id": "R018",
            "source": "web",
            "raw_text": (
                "Naya Mohalla residents report power outage. Local volunteer inspected community center "
                "boundary wall and reports the wall appears fully intact with no visible cracks or damage."
            ),
            "normalized_text": (
                "Naya Mohalla residents report power outage. Local volunteer inspected community center "
                "boundary wall and reports the wall appears fully intact with no visible cracks or damage."
            ),
            "language": "en",
            "location_resolved": "naya_mohalla",
        }
        r020 = {
            "id": "R020",
            "source": "field_worker",
            "raw_text": (
                "Naya Mohalla assessment: Power transformer completely flooded. "
                "One boundary wall showing significant cracks and leaning, risk of collapse is high."
            ),
            "normalized_text": (
                "Naya Mohalla assessment: Power transformer completely flooded. "
                "One boundary wall showing significant cracks and leaning, risk of collapse is high."
            ),
            "language": "en",
            "location_resolved": "naya_mohalla",
        }

        output = detect_incident_contradictions("INC-004", [r018, r020])
        assert output.total_contradictions >= 1
        struct_c = next(c for c in output.contradictions if c.field == "structural_integrity")
        assert struct_c.contradiction_type == "hazard_structural"
        assert struct_c.side_a.report_id == "R018"
        assert struct_c.side_b.report_id == "R020"
