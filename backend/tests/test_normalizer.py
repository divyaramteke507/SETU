"""
SETU Tests — Normalizer Service.

Tests for timestamp normalization, source normalization,
text normalization, and language detection.

Uses actual benchmark reports wherever possible.
"""

import sys
import os

# Ensure backend/ is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from datetime import datetime, timezone, timedelta

from services.normalizer import (
    normalize_timestamp,
    normalize_source,
    normalize_text,
    detect_language,
    normalize_report,
    NormalizedReport,
)
from seed_data import SEED_REPORTS


IST = timezone(timedelta(hours=5, minutes=30))


# ---------------------------------------------------------------------------
# Timestamp normalization
# ---------------------------------------------------------------------------

class TestTimestampNormalization:
    """Verify timestamp parsing and IST → UTC conversion."""

    def test_ist_to_utc_conversion(self):
        """2026-09-07T08:00:00+05:30 → 2026-09-07T02:30:00 UTC."""
        result = normalize_timestamp("2026-09-07T08:00:00+05:30")
        assert result is not None
        assert result.tzinfo == timezone.utc
        assert result.hour == 2
        assert result.minute == 30

    def test_already_utc(self):
        result = normalize_timestamp("2026-09-07T02:30:00+00:00")
        assert result is not None
        assert result.hour == 2
        assert result.minute == 30

    def test_naive_assumed_ist(self):
        """Naive datetime is assumed IST."""
        result = normalize_timestamp("2026-09-07T08:00:00")
        assert result is not None
        assert result.tzinfo == timezone.utc
        assert result.hour == 2
        assert result.minute == 30

    def test_empty_string_returns_none(self):
        assert normalize_timestamp("") is None

    def test_invalid_string_returns_none(self):
        assert normalize_timestamp("not-a-timestamp") is None

    def test_all_20_reports_parse(self):
        """Every benchmark report timestamp must parse successfully."""
        for r in SEED_REPORTS:
            result = normalize_timestamp(r["received_at"])
            assert result is not None, f"{r['id']} timestamp failed to parse"
            assert result.tzinfo == timezone.utc

    def test_time_window_90_minutes(self):
        """All 20 reports fit within a 90-minute window."""
        utc_times = []
        for r in SEED_REPORTS:
            t = normalize_timestamp(r["received_at"])
            utc_times.append(t)
        earliest = min(utc_times)
        latest = max(utc_times)
        delta = (latest - earliest).total_seconds() / 60
        assert delta <= 90, f"Time window is {delta} minutes, expected ≤ 90"


# ---------------------------------------------------------------------------
# Source normalization
# ---------------------------------------------------------------------------

class TestSourceNormalization:
    """Verify source channel label normalization."""

    def test_canonical_passthrough(self):
        assert normalize_source("whatsapp") == "whatsapp"
        assert normalize_source("sms") == "sms"
        assert normalize_source("web") == "web"
        assert normalize_source("field_worker") == "field_worker"

    def test_case_insensitive(self):
        assert normalize_source("WhatsApp") == "whatsapp"
        assert normalize_source("SMS") == "sms"
        assert normalize_source("WEB") == "web"

    def test_variations(self):
        assert normalize_source("wa") == "whatsapp"
        assert normalize_source("text") == "sms"
        assert normalize_source("website") == "web"
        assert normalize_source("fw") == "field_worker"
        assert normalize_source("field worker") == "field_worker"

    def test_whitespace_handling(self):
        assert normalize_source("  whatsapp  ") == "whatsapp"
        assert normalize_source("field  worker") == "field_worker"

    def test_unknown_source_preserved_lowered(self):
        assert normalize_source("RadioHam") == "radioham"

    def test_all_20_reports_normalize(self):
        valid = {"whatsapp", "sms", "web", "field_worker"}
        for r in SEED_REPORTS:
            result = normalize_source(r["source"])
            assert result in valid, f"{r['id']} source '{result}' is not canonical"


# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------

class TestTextNormalization:
    """Verify text cleaning without losing meaningful content."""

    def test_whitespace_collapse(self):
        result = normalize_text("Hello   World\n\nFoo")
        assert result == "Hello World Foo"

    def test_strip_leading_trailing(self):
        result = normalize_text("  hello world  ")
        assert result == "hello world"

    def test_repeated_punctuation(self):
        assert normalize_text("Help!!!!") == "Help!"
        assert normalize_text("What????") == "What?"

    def test_preserves_single_punctuation(self):
        assert normalize_text("Help!") == "Help!"
        assert normalize_text("What?") == "What?"

    def test_preserves_ellipsis(self):
        assert normalize_text("Water rising...") == "Water rising..."

    def test_caps_long_ellipsis(self):
        result = normalize_text("Water.....rising")
        assert result == "Water...rising"

    def test_empty_string(self):
        assert normalize_text("") == ""

    def test_english_report_r001(self):
        """R001: English text should normalize cleanly."""
        r = next(r for r in SEED_REPORTS if r["id"] == "R001")
        result = normalize_text(r["raw_text"])
        assert "Heavy flooding near Civil Lines" in result
        assert len(result) > 50  # Meaningful content preserved

    def test_hindi_report_r002(self):
        """R002: Devanagari Hindi text preserved."""
        r = next(r for r in SEED_REPORTS if r["id"] == "R002")
        result = normalize_text(r["raw_text"])
        assert "सिविल लाइन्स" in result
        assert "पानी" in result

    def test_romanized_hindi_report_r004(self):
        """R004: Romanized Hindi text preserved."""
        r = next(r for r in SEED_REPORTS if r["id"] == "R004")
        result = normalize_text(r["raw_text"])
        assert "paani bhar gaya" in result
        assert "Civil Lines" in result

    def test_preserves_numbers(self):
        """Numbers must survive normalization."""
        r = next(r for r in SEED_REPORTS if r["id"] == "R010")
        result = normalize_text(r["raw_text"])
        assert "5 members" in result
        assert "2 children" in result
        assert "4 feet" in result

    def test_all_20_reports_non_empty(self):
        for r in SEED_REPORTS:
            result = normalize_text(r["raw_text"])
            assert len(result) > 10, f"{r['id']} normalized text too short"


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

class TestLanguageDetection:
    """Verify script/language detection for benchmark reports."""

    def test_english_detected(self):
        assert detect_language("Heavy flooding near Civil Lines area.") == "en"

    def test_devanagari_hindi_detected(self):
        assert detect_language("सिविल लाइन्स में बहुत पानी भर गया है।") == "hi"

    def test_romanized_hindi_detected(self):
        text = "Civil Lines mein bahut paani bhar gaya hai. Sadak pe chalna mushkil hai."
        assert detect_language(text) == "hi-Latn"

    def test_romanized_hindi_not_misclassified_as_english(self):
        """Romanized Hindi with enough keywords must NOT be classified as English."""
        r = next(r for r in SEED_REPORTS if r["id"] == "R004")
        result = detect_language(r["raw_text"])
        assert result == "hi-Latn"

    def test_english_not_misclassified_as_romanized_hindi(self):
        """Plain English without Hindi keywords stays English."""
        result = detect_language("Road blocked near intersection. Vehicles stuck.")
        assert result == "en"

    def test_declared_language_trusted(self):
        """If declared language is consistent with content, trust it."""
        result = detect_language("Hello world", declared_language="en")
        assert result == "en"

    def test_declared_overridden_when_contradicted(self):
        """If declared 'en' but content is Devanagari, override."""
        result = detect_language("सिविल लाइन्स में पानी", declared_language="en")
        assert result == "hi"

    def test_all_20_reports_language_correct(self):
        """Every benchmark report's language detection matches its label."""
        for r in SEED_REPORTS:
            text = r["raw_text"]
            declared = r["language"]
            detected = detect_language(text, declared_language=declared)
            assert detected == declared, (
                f"{r['id']}: declared={declared}, detected={detected}"
            )

    def test_empty_text(self):
        assert detect_language("") == "en"
        assert detect_language("", declared_language="hi") == "hi"


# ---------------------------------------------------------------------------
# Full normalize_report pipeline
# ---------------------------------------------------------------------------

class TestNormalizeReport:
    """Verify the full normalization pipeline on benchmark reports."""

    def test_english_report_r001(self):
        r = next(r for r in SEED_REPORTS if r["id"] == "R001")
        result = normalize_report(
            report_id=r["id"],
            raw_text=r["raw_text"],
            received_at=r["received_at"],
            source=r["source"],
            language=r["language"],
        )
        assert isinstance(result, NormalizedReport)
        assert result.report_id == "R001"
        assert result.source == "whatsapp"
        assert result.language == "en"
        assert result.received_at_utc is not None
        assert result.received_at_utc.tzinfo == timezone.utc
        assert "flooding" in result.normalized_text.lower()

    def test_hindi_report_r007(self):
        r = next(r for r in SEED_REPORTS if r["id"] == "R007")
        result = normalize_report(
            report_id=r["id"],
            raw_text=r["raw_text"],
            received_at=r["received_at"],
            source=r["source"],
            language=r["language"],
        )
        assert result.language == "hi"
        assert "कोतवाली" in result.normalized_text
        assert result.source == "sms"

    def test_romanized_hindi_report_r008(self):
        r = next(r for r in SEED_REPORTS if r["id"] == "R008")
        result = normalize_report(
            report_id=r["id"],
            raw_text=r["raw_text"],
            received_at=r["received_at"],
            source=r["source"],
            language=r["language"],
        )
        assert result.language == "hi-Latn"
        assert "Kotwali" in result.normalized_text
        assert "paani" in result.normalized_text.lower()

    def test_field_worker_report_r010(self):
        r = next(r for r in SEED_REPORTS if r["id"] == "R010")
        result = normalize_report(
            report_id=r["id"],
            raw_text=r["raw_text"],
            received_at=r["received_at"],
            source=r["source"],
            language=r["language"],
        )
        assert result.source == "field_worker"
        assert result.language == "en"
        # Verify numbers preserved
        assert "5 members" in result.normalized_text

    def test_all_20_reports_normalize_successfully(self):
        for r in SEED_REPORTS:
            result = normalize_report(
                report_id=r["id"],
                raw_text=r["raw_text"],
                received_at=r["received_at"],
                source=r["source"],
                language=r["language"],
            )
            assert result.normalized_text, f"{r['id']} empty normalized text"
            assert result.received_at_utc is not None, f"{r['id']} null UTC timestamp"
            assert result.language in {"en", "hi", "hi-Latn"}, f"{r['id']} bad language"
            assert result.source in {"whatsapp", "sms", "web", "field_worker"}, f"{r['id']} bad source"
