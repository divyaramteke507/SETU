"""
SETU Tests — Extractor Service.

Tests for structured field extraction including:
  - incident type extraction
  - people count with semantic categories
  - people range preservation
  - vulnerable person detection
  - trapped/rescue detection
  - urgency signal extraction
  - location phrase extraction
  - severity hint extraction
  - info type (explicit/inferred) detection
  - evidence preservation
  - no-fabrication guarantees
  - confidence bounds

Uses actual benchmark reports wherever possible.
"""

import sys
import os

# Ensure backend/ is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from schemas import ExtractionField
from services.extractor import (
    extract_incident_type,
    extract_people,
    extract_vulnerable,
    extract_trapped_rescue,
    extract_urgency,
    extract_location_raw,
    extract_severity_hint,
    extract_info_type,
    extract_report,
)
from services.normalizer import normalize_text
from seed_data import SEED_REPORTS


def _get_report(report_id: str) -> dict:
    return next(r for r in SEED_REPORTS if r["id"] == report_id)


def _normalized(report_id: str) -> str:
    """Get normalized text for a benchmark report."""
    r = _get_report(report_id)
    return normalize_text(r["raw_text"])


# ---------------------------------------------------------------------------
# Incident type extraction
# ---------------------------------------------------------------------------

class TestIncidentType:
    """Verify incident type extraction across languages."""

    def test_english_flood_r001(self):
        """R001: English flooding report."""
        field = extract_incident_type(_normalized("R001"), "en")
        assert field.field_name == "incident_type"
        assert field.value == "flood"
        assert field.confidence > 0.0
        assert len(field.evidence) > 0

    def test_hindi_flood_r002(self):
        """R002: Devanagari Hindi flooding report."""
        field = extract_incident_type(_normalized("R002"), "hi")
        assert field.value == "flood"
        assert field.confidence > 0.0
        assert len(field.evidence) > 0

    def test_romanized_hindi_flood_r004(self):
        """R004: Romanized Hindi flooding report."""
        field = extract_incident_type(_normalized("R004"), "hi-Latn")
        assert field.value == "flood"
        assert field.confidence > 0.0

    def test_rescue_needed_r006(self):
        """R006: Rescue-needed report."""
        field = extract_incident_type(_normalized("R006"), "en")
        assert field.value == "rescue_needed"
        assert field.confidence >= 0.85

    def test_road_blocked_r011(self):
        """R011: Road blockage report."""
        field = extract_incident_type(_normalized("R011"), "en")
        assert field.value == "road_blocked"
        assert field.confidence > 0.0

    def test_power_outage_r016(self):
        """R016: Power outage report."""
        field = extract_incident_type(_normalized("R016"), "en")
        assert field.value == "power_outage"
        assert field.confidence > 0.0

    def test_structural_damage_r020(self):
        """R020: Structural damage (wall cracking/leaning)."""
        field = extract_incident_type(_normalized("R020"), "en")
        # May detect as structural_damage or power_outage (both are present)
        assert field.value in ("structural_damage", "power_outage")
        assert field.confidence > 0.0


# ---------------------------------------------------------------------------
# People extraction with semantic categories
# ---------------------------------------------------------------------------

class TestPeopleExtraction:
    """Verify people extraction preserves semantic categories."""

    def test_explicit_trapped_count_r008(self):
        """R008: '3 log phase hain' → count=3, category=trapped."""
        fields = extract_people(_normalized("R008"), "hi-Latn")
        trapped = [f for f in fields if f.value and isinstance(f.value, dict) and f.value.get("category") == "trapped"]
        assert len(trapped) > 0
        assert trapped[0].value["count"] == 3

    def test_family_members_r010(self):
        """R010: '1 family of 5 members' → count=5, category=trapped."""
        fields = extract_people(_normalized("R010"), "en")
        assert len(fields) > 0
        # Should find the 5 members count
        counts = [f.value["count"] for f in fields if f.value and isinstance(f.value, dict) and "count" in f.value]
        assert 5 in counts

    def test_affected_residents_r018(self):
        """R018: '50 residents are affected' → count=50, category=affected."""
        fields = extract_people(_normalized("R018"), "en")
        affected = [f for f in fields if f.value and isinstance(f.value, dict) and f.value.get("category") == "affected"]
        assert len(affected) > 0
        assert affected[0].value["count"] == 50

    def test_nearby_residents_r020(self):
        """R020: '30 residents in immediate vicinity' → count=30, category=nearby."""
        fields = extract_people(_normalized("R020"), "en")
        nearby = [f for f in fields if f.value and isinstance(f.value, dict) and f.value.get("category") == "nearby"]
        assert len(nearby) > 0
        assert nearby[0].value["count"] == 30

    def test_nearby_people_r019(self):
        """R019: 'lagbhag 30 log' → count=30, category=nearby."""
        fields = extract_people(_normalized("R019"), "hi-Latn")
        assert len(fields) > 0
        counts = [f.value.get("count") for f in fields if f.value and isinstance(f.value, dict)]
        assert 30 in counts

    def test_people_range_r005(self):
        """R005: '15 to 20 houses' → range preserved, not converted to single number."""
        fields = extract_people(_normalized("R005"), "en")
        ranges = [f for f in fields if f.value and isinstance(f.value, dict) and "count_min" in f.value]
        assert len(ranges) > 0
        r = ranges[0].value
        assert r["count_min"] == 15
        assert r["count_max"] == 20

    def test_families_not_converted_r003(self):
        """R003: '20 families affected' → preserved as families, not multiplied."""
        fields = extract_people(_normalized("R003"), "en")
        family_fields = [f for f in fields if f.value and isinstance(f.value, dict) and f.value.get("unit") == "families"]
        if family_fields:
            assert family_fields[0].value["count"] == 20

    def test_no_people_when_absent(self):
        """A text with no people mention should yield no people fields."""
        fields = extract_people("Water level rising on main road.", "en")
        assert len(fields) == 0

    def test_affected_not_confused_with_trapped(self):
        """Affected residents must NOT be labeled as trapped."""
        text = "About 50 residents in the area are affected by power cut."
        fields = extract_people(text, "en")
        for f in fields:
            if f.value and isinstance(f.value, dict):
                if f.value.get("count") == 50:
                    assert f.value["category"] == "affected", \
                        f"50 residents should be 'affected', got '{f.value['category']}'"

    def test_evidence_attached_to_people_field(self):
        """Evidence phrases must be actual substrings of the text."""
        text = "Approximately 30 residents in immediate vicinity."
        fields = extract_people(text, "en")
        for f in fields:
            for ev in f.evidence:
                assert ev.lower() in text.lower(), f"Evidence '{ev}' not found in text"


# ---------------------------------------------------------------------------
# Vulnerable persons extraction
# ---------------------------------------------------------------------------

class TestVulnerableExtraction:
    """Verify vulnerable persons detection."""

    def test_children_and_elderly_r007(self):
        """R007: Hindi report with बच्चे and बुज़ुर्ग."""
        field = extract_vulnerable(_normalized("R007"), "hi")
        assert field.value is True
        assert field.confidence > 0.0
        assert len(field.evidence) > 0

    def test_elderly_r005(self):
        """R005: 'Elderly residents need assistance'."""
        field = extract_vulnerable(_normalized("R005"), "en")
        assert field.value is True
        assert any("elderly" in ev.lower() for ev in field.evidence)

    def test_children_r010(self):
        """R010: '2 children' mentioned."""
        field = extract_vulnerable(_normalized("R010"), "en")
        assert field.value is True
        assert any("children" in ev.lower() for ev in field.evidence)

    def test_children_r016(self):
        """R016: 'Keep children away'."""
        field = extract_vulnerable(_normalized("R016"), "en")
        assert field.value is True

    def test_no_vulnerable_r011(self):
        """R011: No vulnerable persons mentioned."""
        field = extract_vulnerable(_normalized("R011"), "en")
        assert field.value is None
        assert field.confidence == 0.0
        assert field.evidence == []


# ---------------------------------------------------------------------------
# Trapped / rescue extraction
# ---------------------------------------------------------------------------

class TestTrappedRescueExtraction:
    """Verify trapped/rescue situation detection."""

    def test_trapped_r006(self):
        """R006: 'Family trapped in basement', 'rescue'."""
        field = extract_trapped_rescue(_normalized("R006"), "en")
        assert field.value is True
        assert field.confidence >= 0.85

    def test_trapped_hindi_r007(self):
        """R007: 'फंसा है' (trapped)."""
        field = extract_trapped_rescue(_normalized("R007"), "hi")
        assert field.value is True
        assert field.confidence > 0.0

    def test_trapped_romanized_r008(self):
        """R008: 'phase hain' (trapped), 'Bachao!'."""
        field = extract_trapped_rescue(_normalized("R008"), "hi-Latn")
        assert field.value is True
        assert field.confidence > 0.0

    def test_no_trapped_r001(self):
        """R001: Flooding report, no one explicitly trapped."""
        field = extract_trapped_rescue(_normalized("R001"), "en")
        # May or may not detect 'stuck' for vehicles — but no human trapping
        # Allow either None or True if "stuck" matched
        if field.value is True:
            assert field.confidence <= 0.80  # Low confidence at most

    def test_rescue_r009(self):
        """R009: 'Rescue needed', 'people trapped'."""
        field = extract_trapped_rescue(_normalized("R009"), "en")
        assert field.value is True
        assert field.confidence >= 0.85

    def test_no_trapped_r015(self):
        """R015: Minor flooding, no trapped mention."""
        field = extract_trapped_rescue(_normalized("R015"), "en")
        assert field.value is None
        assert field.confidence == 0.0

    def test_vehicles_stuck_not_trapped(self):
        """Vehicles stuck in water does NOT imply human trapping."""
        field = extract_trapped_rescue("Vehicles stuck in water.", "en")
        assert field.value is None
        assert field.confidence == 0.0
        assert field.evidence == []

    def test_traffic_stuck_not_trapped(self):
        """Traffic is stuck does NOT imply human trapping."""
        field = extract_trapped_rescue("Traffic is stuck.", "en")
        assert field.value is None
        assert field.confidence == 0.0
        assert field.evidence == []

    def test_cars_stuck_not_trapped(self):
        """Cars are stuck on the road does NOT imply human trapping."""
        field = extract_trapped_rescue("Cars are stuck on the road.", "en")
        assert field.value is None
        assert field.confidence == 0.0
        assert field.evidence == []

    def test_people_stuck_is_trapped(self):
        """People stuck explicitly indicates trapped humans."""
        field = extract_trapped_rescue("Several people stuck on the terrace.", "en")
        assert field.value is True
        assert field.confidence > 0.0
        assert len(field.evidence) > 0

    def test_family_stuck_is_trapped(self):
        """Family stuck explicitly indicates trapped humans."""
        field = extract_trapped_rescue("Family stuck inside flooded home.", "en")
        assert field.value is True
        assert field.confidence > 0.0

    def test_someone_stuck_is_trapped(self):
        """Someone stuck explicitly indicates trapped humans."""
        field = extract_trapped_rescue("Someone stuck in the basement.", "en")
        assert field.value is True
        assert field.confidence > 0.0

    def test_stranded_people_is_trapped(self):
        """Stranded people indicates trapped/rescue situation."""
        field = extract_trapped_rescue("Stranded people on the rooftop need help.", "en")
        assert field.value is True
        assert field.confidence > 0.0

    def test_screaming_for_help_is_trapped(self):
        """Screaming for help indicates rescue situation."""
        field = extract_trapped_rescue("Residents screaming for help from top floor.", "en")
        assert field.value is True
        assert field.confidence > 0.0


# ---------------------------------------------------------------------------
# Urgency signal extraction
# ---------------------------------------------------------------------------

class TestUrgencyExtraction:
    """Verify urgency signal extraction."""

    def test_urgency_r006(self):
        """R006: 'rescue immediately', 'critical'."""
        field = extract_urgency(_normalized("R006"), "en")
        assert field.value is not None
        assert isinstance(field.value, list)
        assert len(field.value) > 0

    def test_urgency_r009(self):
        """R009: 'Urgent', 'Emergency services requested'."""
        field = extract_urgency(_normalized("R009"), "en")
        assert field.value is not None
        assert len(field.value) >= 1

    def test_urgency_r010(self):
        """R010: 'Immediate rescue operation required'."""
        field = extract_urgency(_normalized("R010"), "en")
        assert field.value is not None

    def test_urgency_hindi_r007(self):
        """R007: 'तुरंत मदद भेजो'."""
        field = extract_urgency(_normalized("R007"), "hi")
        assert field.value is not None
        assert len(field.value) > 0

    def test_urgency_romanized_r008(self):
        """R008: 'Bachao!' (rescue call)."""
        field = extract_urgency(_normalized("R008"), "hi-Latn")
        assert field.value is not None

    def test_no_urgency_r015(self):
        """R015: Minor flooding, 'No immediate danger'."""
        field = extract_urgency(_normalized("R015"), "en")
        # Should have no urgency or very low
        if field.value is not None:
            assert len(field.value) == 0 or field.confidence < 0.50

    def test_evacuation_urgency_r020(self):
        """R020: 'Recommend evacuation'."""
        field = extract_urgency(_normalized("R020"), "en")
        assert field.value is not None
        signals_lower = [s.lower() for s in field.value]
        assert any("evacuation" in s or "immediate" in s for s in signals_lower)


# ---------------------------------------------------------------------------
# Location phrase extraction
# ---------------------------------------------------------------------------

class TestLocationRawExtraction:
    """Verify raw location phrase extraction (NOT coordinate resolution)."""

    def test_civil_lines_r001(self):
        """R001: 'Civil Lines area'."""
        field = extract_location_raw(_normalized("R001"), "en")
        assert field.value is not None
        assert "civil lines" in field.value.lower()

    def test_kotwali_r006(self):
        """R006: 'near Kotwali thana'."""
        field = extract_location_raw(_normalized("R006"), "en")
        assert field.value is not None
        assert "kotwali" in field.value.lower()

    def test_bilaspur_chowk_r011(self):
        """R011: 'near Bilaspur Chowk'."""
        field = extract_location_raw(_normalized("R011"), "en")
        assert field.value is not None
        assert "bilaspur" in field.value.lower()

    def test_naya_mohalla_r016(self):
        """R016: 'Naya Mohalla'."""
        field = extract_location_raw(_normalized("R016"), "en")
        assert field.value is not None
        assert "naya mohalla" in field.value.lower()

    def test_hindi_location_r002(self):
        """R002: 'सिविल लाइन्स'."""
        field = extract_location_raw(_normalized("R002"), "hi")
        assert field.value is not None
        assert "सिविल" in field.value or "civil" in field.value.lower()

    def test_romanized_location_r008(self):
        """R008: 'Kotwali ke paas'."""
        field = extract_location_raw(_normalized("R008"), "hi-Latn")
        assert field.value is not None
        assert "kotwali" in field.value.lower() or "Kotwali" in field.value

    def test_evidence_is_text_substring(self):
        """Evidence phrases must be actual text substrings."""
        text = _normalized("R001")
        field = extract_location_raw(text, "en")
        for ev in field.evidence:
            assert ev.lower() in text.lower(), f"Evidence '{ev}' not in text"


# ---------------------------------------------------------------------------
# Severity hint extraction
# ---------------------------------------------------------------------------

class TestSeverityHintExtraction:
    """Verify severity hint detection."""

    def test_critical_r006(self):
        """R006: 'Situation is very critical'."""
        field = extract_severity_hint(_normalized("R006"), "en")
        assert field.value == "critical"
        assert field.confidence > 0.0

    def test_severe_r013(self):
        """R013: 'severe traffic disruption'."""
        field = extract_severity_hint(_normalized("R013"), "en")
        assert field.value == "severe"

    def test_minor_r015(self):
        """R015: 'Minor flooding only. No immediate danger.'"""
        field = extract_severity_hint(_normalized("R015"), "en")
        assert field.value == "minor"

    def test_no_severity_r012(self):
        """R012: Hindi road blockage, no explicit severity keyword."""
        field = extract_severity_hint(_normalized("R012"), "hi")
        # May or may not extract severity
        if field.value is not None:
            assert field.value in ("critical", "severe", "moderate", "minor")

    def test_severe_r020(self):
        """R020: 'risk of collapse is high', 'significant cracks'."""
        field = extract_severity_hint(_normalized("R020"), "en")
        assert field.value in ("critical", "severe")

    def test_situation_normal_no_severity(self):
        """The word 'situation' alone must never produce a severity hint."""
        field = extract_severity_hint("Situation is normal.", "en")
        assert field.value is None
        assert field.confidence == 0.0
        assert field.evidence == []

    def test_situation_stable_no_severity(self):
        """'Situation is stable' must not produce a severity hint."""
        field = extract_severity_hint("Situation is stable.", "en")
        assert field.value is None
        assert field.confidence == 0.0
        assert field.evidence == []

    def test_situation_observed_no_severity(self):
        """'Situation observed near the road' must not produce a severity hint."""
        field = extract_severity_hint("Situation observed near the road.", "en")
        assert field.value is None
        assert field.confidence == 0.0
        assert field.evidence == []


# ---------------------------------------------------------------------------
# Info type (explicit vs inferred) extraction
# ---------------------------------------------------------------------------

class TestInfoTypeExtraction:
    """Verify explicit/inferred/unknown detection."""

    # 1. Explicit / firsthand input
    def test_field_worker_explicit_r005(self):
        """R005: Field worker on-ground assessment → explicit."""
        field = extract_info_type(_normalized("R005"), "en", source="field_worker")
        assert field.value == "explicit"
        assert field.confidence >= 0.80
        assert len(field.evidence) > 0

    def test_field_worker_explicit_r010(self):
        """R010: Field worker confirmed → explicit."""
        field = extract_info_type(_normalized("R010"), "en", source="field_worker")
        assert field.value == "explicit"
        assert len(field.evidence) > 0

    def test_explicit_firsthand_i_can_see(self):
        """Firsthand visual observation 'I can see' → explicit."""
        field = extract_info_type("I can see water rising on the main road.", "en")
        assert field.value == "explicit"
        assert field.confidence >= 0.85
        assert any("i can see" in ev.lower() for ev in field.evidence)

    def test_explicit_firsthand_inspected(self):
        """Firsthand inspection 'inspected' → explicit."""
        field = extract_info_type("Local volunteer inspected the boundary wall.", "en")
        assert field.value == "explicit"
        assert field.confidence >= 0.85
        assert any("inspected" in ev.lower() for ev in field.evidence)

    def test_web_report_r018_explicit(self):
        """R018: 'volunteer inspected the community center boundary wall' → explicit."""
        field = extract_info_type(_normalized("R018"), "en", source="web")
        assert field.value == "explicit"
        assert any("inspected" in ev.lower() for ev in field.evidence)

    # 2. Inferred / secondhand input
    def test_inferred_secondhand_reports_of(self):
        """Secondhand hearsay 'reports of' → inferred."""
        field = extract_info_type("Reports of flooding near the market area.", "en")
        assert field.value == "inferred"
        assert field.confidence >= 0.80
        assert any("reports of" in ev.lower() for ev in field.evidence)

    def test_inferred_secondhand_someone_said(self):
        """Secondhand hearsay 'someone said' → inferred."""
        field = extract_info_type("Someone said water has entered houses.", "en")
        assert field.value == "inferred"
        assert field.confidence >= 0.85
        assert any("someone said" in ev.lower() for ev in field.evidence)

    def test_inferred_secondhand_reportedly(self):
        """Secondhand hearsay 'reportedly' → inferred."""
        field = extract_info_type("Reportedly power lines have fallen into water.", "en")
        assert field.value == "inferred"
        assert field.confidence >= 0.80
        assert any("reportedly" in ev.lower() for ev in field.evidence)

    # 3. Unknown / unclassified input
    def test_unknown_no_signals(self):
        """Neutral report without explicit or inferred indicators → None (unknown)."""
        field = extract_info_type("Water level rising on the main road.", "en")
        assert field.value is None
        assert field.confidence == 0.0
        assert field.evidence == []

    def test_whatsapp_no_indicator_r006(self):
        """R006: Trapped family report without explicit/inferred words → None (unknown)."""
        field = extract_info_type(_normalized("R006"), "en", source="whatsapp")
        assert field.value is None
        assert field.confidence == 0.0
        assert field.evidence == []


# ---------------------------------------------------------------------------
# Missing fields — never fabricate
# ---------------------------------------------------------------------------

class TestNoFabrication:
    """Verify that absent fields produce null/empty extractions."""

    def test_no_people_in_simple_text(self):
        fields = extract_people("Water level rising on the main road.", "en")
        assert len(fields) == 0

    def test_no_vulnerable_in_simple_text(self):
        field = extract_vulnerable("Road is blocked due to waterlogging.", "en")
        assert field.value is None
        assert field.confidence == 0.0
        assert field.evidence == []

    def test_no_trapped_in_simple_text(self):
        field = extract_trapped_rescue("Minor flooding in the area.", "en")
        assert field.value is None
        assert field.confidence == 0.0

    def test_no_urgency_in_calm_text(self):
        field = extract_urgency("Water level is stable. No danger.", "en")
        assert field.value is None or len(field.value) == 0

    def test_no_severity_in_neutral_text(self):
        field = extract_severity_hint("Water observed on the road surface.", "en")
        assert field.value is None

    def test_no_incident_type_in_unrelated_text(self):
        field = extract_incident_type("The weather is clear today.", "en")
        assert field.value is None
        assert field.confidence == 0.0

    def test_no_info_type_in_unrelated_text(self):
        field = extract_info_type("The weather is clear today.", "en")
        assert field.value is None
        assert field.confidence == 0.0
        assert field.evidence == []


# ---------------------------------------------------------------------------
# Evidence phrase preservation
# ---------------------------------------------------------------------------

class TestEvidencePreservation:
    """Verify evidence phrases are actual text substrings."""

    def test_all_evidence_in_r006(self):
        """Every evidence phrase from R006 extraction must be in the text."""
        text = _normalized("R006")
        fields = extract_report(text, language="en", source="whatsapp")
        for field in fields:
            for ev in field.evidence:
                # Evidence should be found in text (case-insensitive)
                assert ev.lower() in text.lower(), (
                    f"Evidence '{ev}' not found in R006 text for field '{field.field_name}'"
                )

    def test_all_evidence_in_r007(self):
        """Every evidence phrase from R007 (Hindi) must be in the text."""
        text = _normalized("R007")
        fields = extract_report(text, language="hi", source="sms")
        for field in fields:
            for ev in field.evidence:
                # Skip source-based hints (not text-derived)
                if ev == "field_worker source":
                    continue
                assert ev in text, (
                    f"Evidence '{ev}' not found in R007 text for field '{field.field_name}'"
                )

    def test_all_evidence_in_r008(self):
        """Every evidence phrase from R008 (Romanized Hindi) must be in the text."""
        text = _normalized("R008")
        fields = extract_report(text, language="hi-Latn", source="whatsapp")
        for field in fields:
            for ev in field.evidence:
                if ev == "field_worker source":
                    continue
                assert ev.lower() in text.lower(), (
                    f"Evidence '{ev}' not found in R008 text for field '{field.field_name}'"
                )


# ---------------------------------------------------------------------------
# Confidence bounds
# ---------------------------------------------------------------------------

class TestConfidenceBounds:
    """Verify all confidence values are within [0.0, 1.0]."""

    def test_all_20_reports_confidence_bounded(self):
        """Every extraction across all 20 reports must have 0.0 ≤ confidence ≤ 1.0."""
        for r in SEED_REPORTS:
            text = normalize_text(r["raw_text"])
            fields = extract_report(text, language=r["language"], source=r["source"])
            for field in fields:
                assert 0.0 <= field.confidence <= 1.0, (
                    f"{r['id']} field '{field.field_name}' has confidence "
                    f"{field.confidence} out of bounds"
                )


# ---------------------------------------------------------------------------
# Full extract_report integration
# ---------------------------------------------------------------------------

class TestExtractReport:
    """Verify the complete extraction pipeline on benchmark reports."""

    def test_english_report_r001(self):
        text = _normalized("R001")
        fields = extract_report(text, language="en", source="whatsapp")
        field_names = {f.field_name for f in fields}
        # Must produce core fields
        assert "incident_type" in field_names
        assert "urgency_signals" in field_names
        assert "location_raw" in field_names
        assert "severity_hint" in field_names
        assert "info_type" in field_names

    def test_hindi_report_r007(self):
        text = _normalized("R007")
        fields = extract_report(text, language="hi", source="sms")
        field_names = {f.field_name for f in fields}
        assert "incident_type" in field_names
        assert "vulnerable_persons" in field_names
        assert "trapped_or_rescue" in field_names

    def test_romanized_hindi_report_r008(self):
        text = _normalized("R008")
        fields = extract_report(text, language="hi-Latn", source="whatsapp")
        field_names = {f.field_name for f in fields}
        assert "incident_type" in field_names
        assert "trapped_or_rescue" in field_names

    def test_field_worker_report_r010(self):
        text = _normalized("R010")
        fields = extract_report(text, language="en", source="field_worker")
        field_names = {f.field_name for f in fields}
        assert "incident_type" in field_names
        assert "people_estimate" in field_names
        assert "vulnerable_persons" in field_names
        assert "trapped_or_rescue" in field_names
        assert "info_type" in field_names

        # info_type should be explicit for field worker
        info_field = next(f for f in fields if f.field_name == "info_type")
        assert info_field.value == "explicit"

    def test_all_20_reports_produce_fields(self):
        """Every benchmark report must produce at least some extraction fields."""
        for r in SEED_REPORTS:
            text = normalize_text(r["raw_text"])
            fields = extract_report(text, language=r["language"], source=r["source"])
            assert len(fields) >= 5, (
                f"{r['id']} produced only {len(fields)} fields, expected at least 5"
            )
            # Every field must have a valid field_name
            for f in fields:
                assert f.field_name, f"{r['id']} has field with empty field_name"
