"""
SETU Tests — Location Resolver Service.

Tests for geographic resolution across:
  - Valid GPS resolution (confidence = 1.00)
  - GPS precedence over gazetteer
  - Invalid GPS rejection (never clamped)
  - Exact canonical gazetteer match (confidence = 0.90)
  - Gazetteer alias match across languages (en, hi, hi-Latn)
  - Case and whitespace normalization
  - Fuzzy matching with threshold enforcement (0.60–0.80 confidence)
  - Fuzzy below threshold -> unresolved
  - Ambiguity detection (close candidates flagged unresolved)
  - Unresolved locations (no city-center defaults, no fabricated coordinates)
  - All 20 benchmark reports resolution

Uses actual entries from backend/gazetteer/rampur.json.
"""

import sys
import os

# Ensure backend/ is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import math

from config import (
    GEO_CONFIDENCE_EXACT,
    GEO_CONFIDENCE_GPS,
    GEO_CONFIDENCE_UNRESOLVED,
    LOCATION_FUZZY_THRESHOLD,
)
from schemas import LocationResolution
from services.location_resolver import (
    exact_gazetteer_match,
    fuzzy_gazetteer_match,
    load_gazetteer,
    normalize_location_phrase,
    resolve_location,
    validate_coordinates,
)
from services.normalizer import normalize_text
from services.extractor import extract_location_raw
from seed_data import SEED_REPORTS


@pytest.fixture(scope="module")
def gazetteer():
    """Load actual Rampur gazetteer once for tests."""
    return load_gazetteer()


# ---------------------------------------------------------------------------
# 1. Coordinate Validation & GPS Resolution
# ---------------------------------------------------------------------------

class TestGPSResolution:
    """Verify GPS resolution, validation, and priority."""

    def test_valid_gps_resolution(self):
        """Valid GPS coordinates resolve with method='gps' and confidence=1.0."""
        res = resolve_location(gps_lat=28.7950, gps_lon=79.0250)
        assert res.location_resolved is True
        assert res.resolution_method == "gps"
        assert res.latitude == 28.7950
        assert res.longitude == 79.0250
        assert res.geo_confidence == GEO_CONFIDENCE_GPS
        assert res.match_score is None

    def test_gps_precedence_over_gazetteer(self):
        """
        GPS takes strict precedence over gazetteer.
        If GPS points to coordinate X while location_raw says a known place
        with coordinate Y, the GPS coordinate X must be used.
        """
        # Civil Lines in gazetteer is (28.7950, 79.0250)
        # GPS provided is deliberately different: (28.6000, 79.1000)
        res = resolve_location(
            location_raw="Civil Lines",
            gps_lat=28.6000,
            gps_lon=79.1000,
        )
        assert res.location_resolved is True
        assert res.resolution_method == "gps"
        assert res.latitude == 28.6000
        assert res.longitude == 79.1000
        assert res.geo_confidence == GEO_CONFIDENCE_GPS
        # location_raw is preserved
        assert res.location_raw == "Civil Lines"

    def test_gps_precedence_with_rampur_phrase(self):
        """location_raw = 'Rampur' does NOT override valid GPS."""
        res = resolve_location(
            location_raw="Rampur",
            gps_lat=28.7800,
            gps_lon=79.0500,
        )
        assert res.location_resolved is True
        assert res.resolution_method == "gps"
        assert res.latitude == 28.7800
        assert res.longitude == 79.0500
        assert res.geo_confidence == 1.0

    def test_invalid_latitude_high_rejected(self):
        """Latitude > 90 must be rejected, not clamped."""
        assert validate_coordinates(95.0, 79.0) is False
        res = resolve_location(gps_lat=95.0, gps_lon=79.0)
        assert res.location_resolved is False
        assert res.resolution_method == "unresolved"
        assert res.latitude is None

    def test_invalid_latitude_low_rejected(self):
        """Latitude < -90 must be rejected."""
        assert validate_coordinates(-95.0, 79.0) is False
        res = resolve_location(gps_lat=-95.0, gps_lon=79.0)
        assert res.location_resolved is False
        assert res.latitude is None

    def test_invalid_longitude_high_rejected(self):
        """Longitude > 180 must be rejected."""
        assert validate_coordinates(28.0, 185.0) is False
        res = resolve_location(gps_lat=28.0, gps_lon=185.0)
        assert res.location_resolved is False
        assert res.longitude is None

    def test_invalid_longitude_low_rejected(self):
        """Longitude < -180 must be rejected."""
        assert validate_coordinates(28.0, -185.0) is False
        res = resolve_location(gps_lat=28.0, gps_lon=-185.0)
        assert res.location_resolved is False
        assert res.longitude is None

    def test_invalid_gps_falls_back_to_gazetteer(self):
        """When GPS is invalid, resolver falls through to gazetteer."""
        res = resolve_location(
            location_raw="Civil Lines",
            gps_lat=120.0,     # invalid latitude
            gps_lon=79.0250,
        )
        assert res.location_resolved is True
        assert res.resolution_method == "exact"
        assert res.latitude == 28.7950
        assert res.longitude == 79.0250
        assert res.geo_confidence == GEO_CONFIDENCE_EXACT
        # Explanation notes rejection
        assert "Invalid GPS" in res.explanation

    def test_invalid_gps_with_no_location_is_unresolved(self):
        """Invalid GPS with no location_raw becomes unresolved."""
        res = resolve_location(gps_lat=120.0, gps_lon=79.0250)
        assert res.location_resolved is False
        assert res.resolution_method == "unresolved"
        assert res.latitude is None
        assert res.geo_confidence == 0.0

    def test_none_coordinates_rejected(self):
        assert validate_coordinates(None, None) is False
        assert validate_coordinates(28.0, None) is False
        assert validate_coordinates(None, 79.0) is False

    def test_boolean_coordinates_rejected(self):
        assert validate_coordinates(True, False) is False

    def test_nan_coordinates_rejected(self):
        assert validate_coordinates(float("nan"), 79.0) is False

    def test_inf_coordinates_rejected(self):
        assert validate_coordinates(28.0, float("inf")) is False

    def test_boundary_coordinates_valid(self):
        assert validate_coordinates(90.0, 180.0) is True
        assert validate_coordinates(-90.0, -180.0) is True
        assert validate_coordinates(0.0, 0.0) is True


# ---------------------------------------------------------------------------
# 2. Exact Gazetteer Matching
# ---------------------------------------------------------------------------

class TestExactGazetteerResolution:
    """Verify exact canonical and alias matching from rampur.json."""

    def test_canonical_civil_lines(self, gazetteer):
        res = resolve_location("Civil Lines", gazetteer=gazetteer)
        assert res.location_resolved is True
        assert res.resolution_method == "exact"
        assert res.location_id == "civil_lines"
        assert res.resolved_name == "Civil Lines"
        assert res.latitude == 28.7950
        assert res.longitude == 79.0250
        assert res.geo_confidence == GEO_CONFIDENCE_EXACT

    def test_canonical_kotwali(self, gazetteer):
        res = resolve_location("Kotwali", gazetteer=gazetteer)
        assert res.location_resolved is True
        assert res.resolution_method == "exact"
        assert res.location_id == "kotwali"
        assert res.resolved_name == "Kotwali"
        assert res.latitude == 28.8010
        assert res.longitude == 79.0180
        assert res.geo_confidence == GEO_CONFIDENCE_EXACT

    def test_canonical_bilaspur_chowk(self, gazetteer):
        res = resolve_location("Bilaspur Chowk", gazetteer=gazetteer)
        assert res.location_resolved is True
        assert res.resolution_method == "exact"
        assert res.location_id == "bilaspur_chowk"
        assert res.latitude == 28.7890
        assert res.longitude == 79.0320

    def test_canonical_naya_mohalla(self, gazetteer):
        res = resolve_location("Naya Mohalla", gazetteer=gazetteer)
        assert res.location_resolved is True
        assert res.resolution_method == "exact"
        assert res.location_id == "naya_mohalla"
        assert res.latitude == 28.7930
        assert res.longitude == 79.0100

    def test_alias_civil_line_singular(self, gazetteer):
        res = resolve_location("civil line", gazetteer=gazetteer)
        assert res.location_resolved is True
        assert res.resolution_method == "exact"
        assert res.location_id == "civil_lines"

    def test_alias_kotwali_thana(self, gazetteer):
        res = resolve_location("kotwali thana", gazetteer=gazetteer)
        assert res.location_resolved is True
        assert res.resolution_method == "exact"
        assert res.location_id == "kotwali"

    def test_alias_bilaspur_chowk_intersection(self, gazetteer):
        res = resolve_location("bilaspur chowk intersection", gazetteer=gazetteer)
        assert res.location_resolved is True
        assert res.resolution_method == "exact"
        assert res.location_id == "bilaspur_chowk"

    def test_alias_near_kotwali(self, gazetteer):
        res = resolve_location("near kotwali", gazetteer=gazetteer)
        assert res.location_resolved is True
        assert res.resolution_method == "exact"
        assert res.location_id == "kotwali"

    def test_case_insensitivity(self, gazetteer):
        res = resolve_location("cIvIL lInEs", gazetteer=gazetteer)
        assert res.location_resolved is True
        assert res.resolution_method == "exact"
        assert res.location_id == "civil_lines"

    def test_whitespace_normalization(self, gazetteer):
        res = resolve_location("   Civil    Lines   ", gazetteer=gazetteer)
        assert res.location_resolved is True
        assert res.resolution_method == "exact"
        assert res.location_id == "civil_lines"

    def test_punctuation_handling(self, gazetteer):
        res = resolve_location("Kotwali, thana.", gazetteer=gazetteer)
        assert res.location_resolved is True
        assert res.location_id == "kotwali"


# ---------------------------------------------------------------------------
# 3. Multilingual Support (Devanagari & Romanized Hindi)
# ---------------------------------------------------------------------------

class TestMultilingualResolution:
    """Verify resolution of Hindi (Devanagari) and Romanized Hindi aliases."""

    def test_devanagari_civil_lines(self, gazetteer):
        res = resolve_location("सिविल लाइन्स", gazetteer=gazetteer)
        assert res.location_resolved is True
        assert res.resolution_method == "exact"
        assert res.location_id == "civil_lines"
        assert res.latitude == 28.7950

    def test_devanagari_civil_line_singular(self, gazetteer):
        res = resolve_location("सिविल लाइन", gazetteer=gazetteer)
        assert res.location_resolved is True
        assert res.location_id == "civil_lines"

    def test_devanagari_kotwali(self, gazetteer):
        res = resolve_location("कोतवाली", gazetteer=gazetteer)
        assert res.location_resolved is True
        assert res.resolution_method == "exact"
        assert res.location_id == "kotwali"

    def test_devanagari_kotwali_thana(self, gazetteer):
        res = resolve_location("कोतवाली थाना", gazetteer=gazetteer)
        assert res.location_resolved is True
        assert res.location_id == "kotwali"

    def test_devanagari_bilaspur_chowk(self, gazetteer):
        res = resolve_location("बिलासपुर चौक", gazetteer=gazetteer)
        assert res.location_resolved is True
        assert res.resolution_method == "exact"
        assert res.location_id == "bilaspur_chowk"

    def test_devanagari_bilaspur_chauraha(self, gazetteer):
        res = resolve_location("बिलासपुर चौराहा", gazetteer=gazetteer)
        assert res.location_resolved is True
        assert res.resolution_method == "exact"
        assert res.location_id == "bilaspur_chowk"

    def test_devanagari_naya_mohalla(self, gazetteer):
        res = resolve_location("नया मोहल्ला", gazetteer=gazetteer)
        assert res.location_resolved is True
        assert res.resolution_method == "exact"
        assert res.location_id == "naya_mohalla"

    def test_devanagari_naya_mohalle(self, gazetteer):
        res = resolve_location("नया मोहल्ले", gazetteer=gazetteer)
        assert res.location_resolved is True
        assert res.location_id == "naya_mohalla"

    def test_romanized_sivil_lains(self, gazetteer):
        res = resolve_location("sivil lains", gazetteer=gazetteer)
        assert res.location_resolved is True
        assert res.resolution_method == "exact"
        assert res.location_id == "civil_lines"

    def test_romanized_bilaspur_chauk(self, gazetteer):
        res = resolve_location("bilaspur chauk", gazetteer=gazetteer)
        assert res.location_resolved is True
        assert res.resolution_method == "exact"
        assert res.location_id == "bilaspur_chowk"

    def test_romanized_kotwali_ke_paas(self, gazetteer):
        res = resolve_location("kotwali ke paas", gazetteer=gazetteer)
        assert res.location_resolved is True
        assert res.resolution_method == "exact"
        assert res.location_id == "kotwali"

    def test_romanized_naya_mohalle_mein(self, gazetteer):
        res = resolve_location("naya mohalle mein", gazetteer=gazetteer)
        assert res.location_resolved is True
        assert res.resolution_method == "exact"
        assert res.location_id == "naya_mohalla"


# ---------------------------------------------------------------------------
# 4. Fuzzy Matching & Uncertainty Representation
# ---------------------------------------------------------------------------

class TestFuzzyResolution:
    """Verify fuzzy matching, threshold enforcement, and score reporting."""

    def test_fuzzy_above_threshold_bilaspur_chawk(self, gazetteer):
        """'Bilaspur Chawk' (spelling variant) resolves via fuzzy match."""
        res = resolve_location("Bilaspur Chawk", gazetteer=gazetteer)
        assert res.location_resolved is True
        assert res.resolution_method == "fuzzy"
        assert res.location_id == "bilaspur_chowk"
        assert res.match_score is not None
        assert res.match_score >= LOCATION_FUZZY_THRESHOLD
        # geo_confidence reflects uncertainty: between 0.60 and 0.80
        assert 0.60 <= res.geo_confidence <= 0.80

    def test_fuzzy_above_threshold_sivill_lines(self, gazetteer):
        """Typo 'sivill lines' resolves via fuzzy match."""
        res = resolve_location("sivill lines", gazetteer=gazetteer)
        assert res.location_resolved is True
        assert res.resolution_method == "fuzzy"
        assert res.location_id == "civil_lines"
        assert res.match_score >= LOCATION_FUZZY_THRESHOLD
        assert 0.60 <= res.geo_confidence <= 0.80

    def test_fuzzy_above_threshold_naya_mohlla(self, gazetteer):
        """Typo 'naya mohlla' resolves via fuzzy match."""
        res = resolve_location("naya mohlla", gazetteer=gazetteer)
        assert res.location_resolved is True
        assert res.resolution_method == "fuzzy"
        assert res.location_id == "naya_mohalla"
        assert res.match_score >= LOCATION_FUZZY_THRESHOLD

    def test_fuzzy_below_threshold_becomes_unresolved(self, gazetteer):
        """Phrase with weak similarity (< 0.75) must NOT resolve."""
        res = resolve_location("near the old bridge", gazetteer=gazetteer)
        assert res.location_resolved is False
        assert res.resolution_method == "unresolved"
        assert res.latitude is None
        assert res.longitude is None
        assert res.geo_confidence == 0.0

    def test_fuzzy_confidence_strictly_less_than_exact(self, gazetteer):
        """Fuzzy confidence must strictly be less than exact match confidence."""
        res_fuzzy = resolve_location("Bilaspur Chawk", gazetteer=gazetteer)
        assert res_fuzzy.geo_confidence < GEO_CONFIDENCE_EXACT


# ---------------------------------------------------------------------------
# 5. Ambiguity Handling
# ---------------------------------------------------------------------------

class TestAmbiguityHandling:
    """Verify that ambiguous close candidates are flagged rather than guessed."""

    def test_ambiguous_fuzzy_candidates_flagged_unresolved(self):
        """
        When two distinct gazetteer locations score within the ambiguity delta,
        the resolver must NOT guess one. It must mark as unresolved.
        """
        # Synthetic gazetteer with two deliberately similar locations
        mock_gaz = [
            {
                "location_id": "sector_alpha",
                "name_en": "Sector Alpha",
                "aliases": ["sector a"],
                "lat": 28.10,
                "lon": 79.10,
            },
            {
                "location_id": "sector_alphi",
                "name_en": "Sector Alphi",
                "aliases": ["sector b"],
                "lat": 28.20,
                "lon": 79.20,
            },
        ]
        # "Sector Alph" scores identically high on both Sector Alpha and Sector Alphi
        res = resolve_location("Sector Alph", gazetteer=mock_gaz)
        assert res.location_resolved is False
        assert res.resolution_method == "unresolved"
        assert res.latitude is None
        assert res.longitude is None
        assert res.geo_confidence == 0.0
        # Candidates are exposed for responder inspection
        assert len(res.candidates) >= 2
        assert "Ambiguous" in res.explanation

    def test_borderline_difference_uses_full_precision_not_rounded(self, monkeypatch):
        """
        Verify that ambiguity comparison uses full-precision scores.

        Borderline scenario:
          Candidate 1 similarity = 0.854 (85.4%)
          Candidate 2 similarity = 0.803 (80.3%)
          Ambiguity delta = 0.05

          Full-precision delta = 0.854 - 0.803 = 0.051 > 0.05.
          -> NOT ambiguous under full-precision (Candidate 1 clearly wins).

          If evaluated on 2-decimal rounded scores:
            round(0.854, 2) = 0.85
            round(0.803, 2) = 0.80
            0.85 - 0.80 = 0.05 <= 0.05
            -> Would be falsely flagged as ambiguous and left unresolved!

        The resolver must resolve Candidate 1 using the full-precision difference.
        """
        mock_gaz = [
            {
                "location_id": "place_a",
                "name_en": "Place A",
                "aliases": [],
                "lat": 28.10,
                "lon": 79.10,
            },
            {
                "location_id": "place_b",
                "name_en": "Place B",
                "aliases": [],
                "lat": 28.20,
                "lon": 79.20,
            },
        ]

        def mock_ratio(s1, s2):
            if "place a" in s2.lower():
                return 85.4
            elif "place b" in s2.lower():
                return 80.3
            return 0.0

        monkeypatch.setattr("services.location_resolver.fuzz.ratio", mock_ratio)
        monkeypatch.setattr("services.location_resolver.fuzz.token_sort_ratio", mock_ratio)

        res = resolve_location("test place query", gazetteer=mock_gaz)
        # Under full precision: delta = 0.051 > 0.05 -> NOT ambiguous
        assert res.location_resolved is True
        assert res.resolution_method == "fuzzy"
        assert res.location_id == "place_a"
        assert res.resolved_name == "Place A"
        assert res.latitude == 28.10
        assert res.longitude == 79.10
        assert res.match_score == 0.854


# ---------------------------------------------------------------------------
# 6. Unresolved & No Fabrication Invariants
# ---------------------------------------------------------------------------

class TestUnresolvedAndNoFabrication:
    """Verify that missing/unknown locations never receive fabricated coordinates."""

    def test_unknown_place_xyz(self, gazetteer):
        """'completely unknown place xyz' must remain unresolved."""
        res = resolve_location("completely unknown place xyz", gazetteer=gazetteer)
        assert res.location_resolved is False
        assert res.resolution_method == "unresolved"
        assert res.latitude is None
        assert res.longitude is None
        assert res.geo_confidence == 0.0
        assert res.location_raw == "completely unknown place xyz"

    def test_no_default_to_city_center(self, gazetteer):
        """Resolver must NEVER assign Rampur city center (28.7945, 79.0213)."""
        res = resolve_location("completely unknown place xyz", gazetteer=gazetteer)
        assert res.latitude != 28.7945
        assert res.longitude != 79.0213
        assert res.latitude is None

    def test_rampur_alone_does_not_invent_coords(self, gazetteer):
        """
        'Rampur' alone does not match a specific hazard location,
        so it must NOT invent coordinates or default to city center.
        """
        res = resolve_location("Rampur", gazetteer=gazetteer)
        assert res.location_resolved is False
        assert res.latitude is None
        assert res.longitude is None
        assert res.geo_confidence == 0.0

    def test_none_location_unresolved(self, gazetteer):
        res = resolve_location(None, gazetteer=gazetteer)
        assert res.location_resolved is False
        assert res.resolution_method == "unresolved"
        assert res.latitude is None
        assert res.longitude is None
        assert res.geo_confidence == 0.0

    def test_empty_string_unresolved(self, gazetteer):
        res = resolve_location("", gazetteer=gazetteer)
        assert res.location_resolved is False
        assert res.latitude is None
        assert res.geo_confidence == 0.0

    def test_whitespace_only_unresolved(self, gazetteer):
        res = resolve_location("   ", gazetteer=gazetteer)
        assert res.location_resolved is False
        assert res.latitude is None
        assert res.geo_confidence == 0.0

    def test_original_location_raw_preserved(self, gazetteer):
        raw = "random alley near railway"
        res = resolve_location(raw, gazetteer=gazetteer)
        assert res.location_raw == raw


# ---------------------------------------------------------------------------
# 7. Benchmark Reports Resolution (All 20 Seed Reports)
# ---------------------------------------------------------------------------

class TestBenchmarkReportsResolution:
    """Verify geographic resolution for all 20 Rampur flood benchmark reports."""

    def test_all_20_reports_resolve_to_expected_clusters(self, gazetteer):
        """
        Every benchmark report must resolve accurately:
          Cluster 1 (R001–R005) → Civil Lines (28.7950, 79.0250)
          Cluster 2 (R006–R010) → Kotwali (28.8010, 79.0180)
          Cluster 3 (R011–R015) → Bilaspur Chowk (28.7890, 79.0320)
          Cluster 4 (R016–R020) → Naya Mohalla (28.7930, 79.0100)
        """
        expected_clusters = {
            "R001": ("civil_lines", 28.7950, 79.0250),
            "R002": ("civil_lines", 28.7950, 79.0250),
            "R003": ("civil_lines", 28.7950, 79.0250),
            "R004": ("civil_lines", 28.7950, 79.0250),
            "R005": (None, 28.7950, 79.0250),  # GPS
            "R006": ("kotwali", 28.8010, 79.0180),
            "R007": ("kotwali", 28.8010, 79.0180),
            "R008": ("kotwali", 28.8010, 79.0180),
            "R009": ("kotwali", 28.8010, 79.0180),
            "R010": (None, 28.8010, 79.0180),  # GPS
            "R011": ("bilaspur_chowk", 28.7890, 79.0320),
            "R012": ("bilaspur_chowk", 28.7890, 79.0320),
            "R013": ("bilaspur_chowk", 28.7890, 79.0320),
            "R014": ("bilaspur_chowk", 28.7890, 79.0320),
            "R015": ("bilaspur_chowk", 28.7890, 79.0320),
            "R016": ("naya_mohalla", 28.7930, 79.0100),
            "R017": ("naya_mohalla", 28.7930, 79.0100),
            "R018": ("naya_mohalla", 28.7930, 79.0100),
            "R019": ("naya_mohalla", 28.7930, 79.0100),
            "R020": (None, 28.7930, 79.0100),  # GPS
        }

        for r in SEED_REPORTS:
            t = normalize_text(r["raw_text"])
            loc_field = extract_location_raw(t, r["language"])
            res = resolve_location(
                location_raw=loc_field.value,
                gps_lat=r["gps_lat"],
                gps_lon=r["gps_lon"],
                language=r["language"],
                gazetteer=gazetteer,
            )

            assert res.location_resolved is True, f"{r['id']} failed to resolve location"
            assert res.latitude is not None, f"{r['id']} latitude is None"
            assert res.longitude is not None, f"{r['id']} longitude is None"

            expected_id, exp_lat, exp_lon = expected_clusters[r["id"]]
            assert math.isclose(res.latitude, exp_lat, abs_tol=1e-3), (
                f"{r['id']} expected lat {exp_lat}, got {res.latitude}"
            )
            assert math.isclose(res.longitude, exp_lon, abs_tol=1e-3), (
                f"{r['id']} expected lon {exp_lon}, got {res.longitude}"
            )

    def test_field_worker_reports_use_gps(self, gazetteer):
        """R005, R010, R020 have field worker GPS, which must be used directly."""
        for report_id in ("R005", "R010", "R020"):
            r = next(item for item in SEED_REPORTS if item["id"] == report_id)
            t = normalize_text(r["raw_text"])
            loc_field = extract_location_raw(t, r["language"])
            res = resolve_location(
                location_raw=loc_field.value,
                gps_lat=r["gps_lat"],
                gps_lon=r["gps_lon"],
                language=r["language"],
                gazetteer=gazetteer,
            )
            assert res.resolution_method == "gps"
            assert res.geo_confidence == 1.0
            assert res.latitude == r["gps_lat"]
            assert res.longitude == r["gps_lon"]


# ---------------------------------------------------------------------------
# 8. Bounds & Determinism Invariants
# ---------------------------------------------------------------------------

class TestBoundsAndDeterminism:
    """Verify confidence bounds and determinism."""

    def test_geo_confidence_strictly_bounded(self, gazetteer):
        """geo_confidence must always be within [0.0, 1.0]."""
        test_inputs = [
            ("Civil Lines", None, None),
            (None, 28.795, 79.025),
            ("Bilaspur Chawk", None, None),
            ("completely unknown place", None, None),
            (None, None, None),
            ("Rampur", None, None),
        ]
        for raw, lat, lon in test_inputs:
            res = resolve_location(raw, lat, lon, gazetteer=gazetteer)
            assert 0.0 <= res.geo_confidence <= 1.0

    def test_determinism(self, gazetteer):
        """Repeated resolution of same input must produce identical results."""
        for _ in range(5):
            res1 = resolve_location("Bilaspur Chawk", gazetteer=gazetteer)
            res2 = resolve_location("Bilaspur Chawk", gazetteer=gazetteer)
            assert res1.model_dump() == res2.model_dump()
