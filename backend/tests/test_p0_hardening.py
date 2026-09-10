"""
SETU Red-Team P0 Hardening Test Suite.

Verifies:
- P0-1: Duplicate / forwarded report confidence inflation guard
- P0-2: Contradiction distinct-disagreement deduplication for consistency penalty
- P0-4: GPS vs text location conflict detection and visibility
- P0-5: Ordinary English extraction grammar coverage with evidence grounding
"""

import math
import os
import sys
import pytest
from datetime import datetime, timezone

# Ensure backend/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import (
    CONFIDENCE_CONSISTENCY_POLICY,
    CONFIDENCE_SOURCE_COUNT_POLICY,
    CONFIDENCE_SOURCE_DIVERSITY_POLICY,
    GEO_MAX_DISTANCE_M,
)
from schemas import (
    ContradictionResult,
    ContradictionSide,
    ExtractionField,
)
from services.contradiction_detector import check_hazard_structural_contradictions
from services.embedding_service import generate_embedding, serialize_embedding
from services.extractor import extract_incident_type, extract_report, extract_trapped_rescue
from services.incident_assessor import (
    _cluster_corroboration_units,
    _count_distinct_disagreements,
    calculate_confidence,
    calculate_priority,
    calculate_severity,
    determine_urgency_band,
)
from services.location_resolver import resolve_location


# ============================================================================
# P0-1 TESTS: Duplicate / Forwarded Report Corroboration Guard
# ============================================================================

class TestP01DuplicateCorroborationGuard:
    """
    Key invariant:
    Three identical/near-identical forwarded reports must contribute the same
    corroboration count/source contribution as one independent observation.
    """

    def _make_report(self, rid: str, text: str, source: str, embedding=None):
        if embedding is None:
            embedding = generate_embedding(text)
        return {
            "id": rid,
            "raw_text": text,
            "normalized_text": text.lower().strip(),
            "source": source,
            "language": "en",
            "embedding": serialize_embedding(embedding),
        }

    def test_a_one_report_baseline(self):
        """Single report contributes 1 corroboration unit, 1 source."""
        text = "Civil Lines heavy flooding, water 3ft on main road."
        r1 = self._make_report("R1", text, "whatsapp")

        units = _cluster_corroboration_units([r1])
        assert len(units) == 1
        assert len(units[0]) == 1

        conf = calculate_confidence([r1])
        assert conf.source_count == CONFIDENCE_SOURCE_COUNT_POLICY[1]
        assert conf.source_diversity == CONFIDENCE_SOURCE_DIVERSITY_POLICY[1]

    def test_b_three_identical_reports_across_three_channels(self):
        """
        P0-1 Core Invariant:
        3 near-identical copies across 3 channels must contribute 1 corroboration unit,
        NOT 3 independent units. Duplicate forwarding must not push priority into Critical.
        """
        text = "Water is 4 feet deep near Civil Lines and residents are stranded inside homes."
        # Generate shared embedding for the identical message
        emb = generate_embedding(text)
        r1 = self._make_report("R1", text, "whatsapp", embedding=emb)
        r2 = self._make_report("R2", text, "sms", embedding=emb)
        r3 = self._make_report("R3", text, "web", embedding=emb)

        units = _cluster_corroboration_units([r1, r2, r3])
        # Crucial check: 3 copies collapse to 1 corroboration unit
        assert len(units) == 1
        assert len(units[0]) == 3

        conf = calculate_confidence([r1, r2, r3])
        # Contributes 1 corroboration unit and 1 effective source channel
        assert conf.source_count == CONFIDENCE_SOURCE_COUNT_POLICY[1]
        assert conf.source_diversity == CONFIDENCE_SOURCE_DIVERSITY_POLICY[1]
        assert "duplicate copy/copies grouped" in conf.source_count_detail

        # Calculate resulting priority: must NOT be Critical (>85) solely from duplicate copies
        sev = calculate_severity([r1, r2, r3])
        prio_breakdown = calculate_priority(sev.final_severity, conf.final)
        band = determine_urgency_band(prio_breakdown.final_priority)
        assert band != "critical", f"Duplicate copies should not produce Critical, got {band} ({prio_breakdown.final_priority})"

    def test_c_three_genuinely_different_reports_across_three_channels(self):
        """Genuinely different reports from different channels preserve independent corroboration."""
        r1 = self._make_report(
            "R1",
            "Severe waterlogging at Civil Lines main market, shops inundated.",
            "whatsapp",
        )
        r2 = self._make_report(
            "R2",
            "Electric transformer sparking in floodwater near Civil Lines corner.",
            "sms",
        )
        r3 = self._make_report(
            "R3",
            "Elderly person requires boat evacuation from ground floor in Civil Lines.",
            "field_worker",
        )

        units = _cluster_corroboration_units([r1, r2, r3])
        assert len(units) == 3

        conf = calculate_confidence([r1, r2, r3])
        assert conf.source_count == CONFIDENCE_SOURCE_COUNT_POLICY[3]
        assert conf.source_diversity == CONFIDENCE_SOURCE_DIVERSITY_POLICY[3]

    def test_d_two_duplicates_plus_one_genuinely_different_report(self):
        """2 duplicates + 1 different report -> exactly 2 corroboration units."""
        dup_text = "Boundary wall showing major cracks and tilting towards alley."
        emb_dup = generate_embedding(dup_text)
        r1 = self._make_report("R1", dup_text, "whatsapp", embedding=emb_dup)
        r2 = self._make_report("R2", dup_text, "web", embedding=emb_dup)
        r3 = self._make_report(
            "R3",
            "Submerged drains overflowing on the adjacent street.",
            "sms",
        )

        units = _cluster_corroboration_units([r1, r2, r3])
        assert len(units) == 2

        conf = calculate_confidence([r1, r2, r3])
        assert conf.source_count == CONFIDENCE_SOURCE_COUNT_POLICY[2]
        assert conf.source_diversity == CONFIDENCE_SOURCE_DIVERSITY_POLICY[2]

    def test_e_duplicate_reports_remain_visible_in_incident(self):
        """Reports are never deleted or excluded from an incident."""
        text = "Civil lines road blocked due to flooding."
        r1 = self._make_report("R1", text, "sms")
        r2 = self._make_report("R2", text, "whatsapp")

        incident_reports = [r1, r2]
        assert len(incident_reports) == 2
        assert {r["id"] for r in incident_reports} == {"R1", "R2"}

    def test_f_same_channel_distinct_reports_not_collapsed(self):
        """
        Clarification 2:
        Genuinely different reports arriving on the SAME channel remain separate
        corroboration units (they are NOT collapsed simply because they share a channel).
        """
        r1 = self._make_report("R1", "Bridge flooded completely.", "whatsapp")
        r2 = self._make_report("R2", "Power lines fell into the road water.", "whatsapp")

        units = _cluster_corroboration_units([r1, r2])
        assert len(units) == 2

        conf = calculate_confidence([r1, r2])
        # 2 distinct corroboration units -> source_count = policy for 2
        assert conf.source_count == CONFIDENCE_SOURCE_COUNT_POLICY[2]
        # 1 unique channel (whatsapp)
        assert conf.source_diversity == CONFIDENCE_SOURCE_DIVERSITY_POLICY[1]


# ============================================================================
# P0-2 TESTS: Contradiction Deduplication for Consistency
# ============================================================================

class TestP02ContradictionDeduplicationForConsistency:
    """
    Tests separation of raw pairwise contradiction evidence from the distinct
    disagreement count used for confidence consistency.
    """

    def _make_contradiction(self, cid: str, c_type: str, field: str, rep_a: str, val_a: str, rep_b: str, val_b: str):
        return ContradictionResult(
            id=cid,
            incident_id="INC-TEST",
            contradiction_type=c_type,
            field=field,
            side_a=ContradictionSide(report_id=rep_a, value=val_a, evidence=[val_a]),
            side_b=ContradictionSide(report_id=rep_b, value=val_b, evidence=[val_b]),
            resolution=None,
            explanation=f"{val_a} vs {val_b}",
        )

    def test_three_blocked_vs_one_passable_is_one_distinct_disagreement(self):
        """
        R1, R2, R3 report 'completely_blocked'; R4 reports 'passable_slowly'.
        3 pairwise records exist, but represent 1 distinct underlying disagreement.
        """
        c1 = self._make_contradiction("C1", "hazard_structural", "road_access", "R1", "completely_blocked", "R4", "passable_slowly")
        c2 = self._make_contradiction("C2", "hazard_structural", "road_access", "R2", "completely_blocked", "R4", "passable_slowly")
        c3 = self._make_contradiction("C3", "hazard_structural", "road_access", "R3", "completely_blocked", "R4", "passable_slowly")

        distinct_count, raw = _count_distinct_disagreements([c1, c2, c3])
        assert distinct_count == 1
        assert len(raw) == 3

        reps = [
            {"id": "R1", "source": "whatsapp", "raw_text": "road blocked"},
            {"id": "R2", "source": "sms", "raw_text": "road blocked"},
            {"id": "R3", "source": "web", "raw_text": "road blocked"},
            {"id": "R4", "source": "field_worker", "raw_text": "vehicles can pass slowly"},
        ]
        conf = calculate_confidence(reps, contradictions=[c1, c2, c3])
        # Penalty corresponds to 1 disagreement (0.75), NOT 3 disagreements (0.25)
        assert conf.consistency == CONFIDENCE_CONSISTENCY_POLICY[1]
        assert "1 distinct disagreement(s)" in conf.consistency_detail
        assert "3 pairwise" in conf.consistency_detail

    def test_one_blocked_vs_one_passable_is_one_distinct_disagreement(self):
        """1 blocked vs 1 passable is 1 distinct disagreement."""
        c1 = self._make_contradiction("C1", "hazard_structural", "road_access", "R1", "completely_blocked", "R2", "passable_slowly")

        distinct_count, raw = _count_distinct_disagreements([c1])
        assert distinct_count == 1
        assert len(raw) == 1

        reps = [
            {"id": "R1", "source": "whatsapp", "raw_text": "road blocked"},
            {"id": "R2", "source": "field_worker", "raw_text": "vehicles can pass slowly"},
        ]
        conf = calculate_confidence(reps, contradictions=[c1])
        assert conf.consistency == CONFIDENCE_CONSISTENCY_POLICY[1]

    def test_blocked_vs_passable_and_damaged_vs_intact_are_two_distinct_disagreements(self):
        """Two genuinely different conflict topics yield 2 distinct disagreements."""
        c1 = self._make_contradiction("C1", "hazard_structural", "road_access", "R1", "completely_blocked", "R2", "passable_slowly")
        c2 = self._make_contradiction("C2", "hazard_structural", "road_access", "R3", "completely_blocked", "R2", "passable_slowly")
        c3 = self._make_contradiction("C3", "hazard_structural", "structural_integrity", "R1", "damaged_or_collapse_risk", "R4", "intact")

        distinct_count, raw = _count_distinct_disagreements([c1, c2, c3])
        assert distinct_count == 2
        assert len(raw) == 3

        reps = [
            {"id": "R1", "source": "whatsapp", "raw_text": "road blocked"},
            {"id": "R2", "source": "sms", "raw_text": "road blocked"},
            {"id": "R3", "source": "web", "raw_text": "road blocked"},
            {"id": "R4", "source": "field_worker", "raw_text": "structure intact"},
        ]
        conf = calculate_confidence(reps, contradictions=[c1, c2, c3])
        assert conf.consistency == CONFIDENCE_CONSISTENCY_POLICY[2]

    def test_canonical_ordering_symmetry(self):
        """Side A/B vs B/A map to the same disagreement identity."""
        c1 = self._make_contradiction("C1", "severity", "severity_hint", "R1", "critical", "R2", "minor")
        c2 = self._make_contradiction("C2", "severity", "severity_hint", "R3", "minor", "R4", "critical")

        distinct_count, _ = _count_distinct_disagreements([c1, c2])
        assert distinct_count == 1


# ============================================================================
# P0-4 TESTS: GPS vs Text Location Conflict
# ============================================================================

class TestP04LocationConflictSignal:
    """
    Tests additive GPS vs text-implied location conflict signal.
    GPS remains primary resolved location.
    """

    def test_a_gps_and_text_agree_no_conflict(self):
        """GPS coordinates match the text gazetteer location within 2000m -> no conflict."""
        # Civil Lines coordinates in Rampur: lat=28.7950, lon=79.0250
        res = resolve_location(
            location_raw="Civil Lines",
            gps_lat=28.7955,
            gps_lon=79.0248,
        )
        assert res.location_resolved is True
        assert res.resolution_method == "gps"
        assert res.location_conflict is False
        assert res.location_conflict_text is None
        assert res.latitude == pytest.approx(28.7955, rel=1e-4)

    def test_b_gps_and_text_far_apart_conflict(self):
        """
        Text says 'Kotwali ke paas' (Rampur, ~28.805, 79.030),
        GPS is Delhi (~28.6139, 77.2090) > 180 km away.
        GPS is preserved as resolved location, and conflict flag is set.
        """
        res = resolve_location(
            location_raw="Kotwali ke paas",
            gps_lat=28.6139,
            gps_lon=77.2090,
        )
        assert res.location_resolved is True
        assert res.resolution_method == "gps"
        assert res.latitude == pytest.approx(28.6139, rel=1e-4)
        assert res.longitude == pytest.approx(77.2090, rel=1e-4)
        assert res.location_conflict is True
        assert res.location_conflict_text is not None
        assert "Kotwali" in res.location_conflict_text
        assert res.location_conflict_distance_m is not None
        assert res.location_conflict_distance_m > GEO_MAX_DISTANCE_M
        assert "CONFLICT WARNING" in (res.explanation or "")

    def test_c_gps_valid_unresolved_text_no_conflict(self):
        """GPS valid + text unresolvable in gazetteer -> no conflict created."""
        res = resolve_location(
            location_raw="Some random unknown shop name 123",
            gps_lat=28.7950,
            gps_lon=79.0250,
        )
        assert res.location_resolved is True
        assert res.resolution_method == "gps"
        assert res.location_conflict is False
        assert res.location_conflict_text is None

    def test_d_invalid_gps_valid_text_fallback(self):
        """Invalid GPS + valid text -> existing text fallback behavior preserved."""
        res = resolve_location(
            location_raw="Civil Lines",
            gps_lat=120.0,   # Invalid latitude (>90)
            gps_lon=79.0250,
        )
        assert res.location_resolved is True
        assert res.resolution_method in ("exact", "fuzzy")
        assert res.resolved_name == "Civil Lines"
        assert res.location_conflict is False

    def test_e_no_coordinates_invented(self):
        """When text and GPS are missing, coordinates remain None."""
        res = resolve_location(location_raw=None, gps_lat=None, gps_lon=None)
        assert res.location_resolved is False
        assert res.latitude is None
        assert res.longitude is None
        assert res.location_conflict is False


# ============================================================================
# P0-5 TESTS: Ordinary English Extraction Grammar Gaps
# ============================================================================

class TestP05EnglishGrammarExtraction:
    """
    Tests ordinary English grammar coverage improvements with strict evidence grounding.
    """

    def test_a_family_is_stuck_inside(self):
        """'A family is stuck inside.' must extract rescue_needed and trapped_or_rescue=True."""
        text = "A family is stuck inside."
        ext_type = extract_incident_type(text, language="en")
        assert ext_type.value == "rescue_needed"
        assert "family is stuck" in ext_type.evidence

        ext_trapped = extract_trapped_rescue(text, language="en")
        assert ext_trapped.value is True
        assert "family is stuck" in ext_trapped.evidence

    def test_the_road_is_blocked_and_vehicles_cannot_pass(self):
        """'The road is blocked and vehicles cannot pass.' extracts road_blocked."""
        text = "The road is blocked and vehicles cannot pass."
        ext_type = extract_incident_type(text, language="en")
        assert ext_type.value == "road_blocked"
        assert any("road is blocked" in ev or "vehicles cannot pass" in ev for ev in ext_type.evidence)

    def test_variations_with_auxiliaries(self):
        """Verify singular/plural past/present auxiliaries."""
        # rescue_needed variations
        for phrase in [
            "Two families are stuck on the upper floor.",
            "A person was stuck near the canal.",
            "Residents were stuck in the alley.",
            "Someone got stuck in the mud.",
        ]:
            ext = extract_incident_type(phrase, language="en")
            assert ext.value == "rescue_needed", f"Failed on '{phrase}'"
            assert ext.evidence, f"Missing evidence on '{phrase}'"

        # road_blocked variations
        for phrase in [
            "Road was blocked by fallen tree.",
            "Roads are completely blocked.",
            "Vehicles can't pass due to rising water.",
            "Multiple vehicles were stuck in the intersection.",
        ]:
            ext = extract_incident_type(phrase, language="en")
            assert ext.value == "road_blocked", f"Failed on '{phrase}'"
            assert ext.evidence, f"Missing evidence on '{phrase}'"

    def test_vehicles_cannot_pass_triggers_road_access_contradiction(self):
        """'vehicles cannot pass' opposes 'vehicles can pass slowly' in contradiction detector."""
        rep_a = {
            "id": "RA",
            "raw_text": "Water is deep and vehicles cannot pass.",
            "normalized_text": "water is deep and vehicles cannot pass",
        }
        rep_b = {
            "id": "RB",
            "raw_text": "Water receding slowly, vehicles can pass slowly on the shoulder.",
            "normalized_text": "water receding slowly vehicles can pass slowly on the shoulder",
        }

        contrs = check_hazard_structural_contradictions(rep_a, rep_b, [], [], incident_id="INC-TEST")
        access_contrs = [c for c in contrs if c.field == "road_access"]
        assert len(access_contrs) == 1
        assert access_contrs[0].side_a.value == "completely_blocked"
        assert access_contrs[0].side_b.value == "passable_slowly"
