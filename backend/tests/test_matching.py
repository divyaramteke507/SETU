"""
SETU Tests — Phase 4: Multilingual Embeddings + Explainable Match Score.

Comprehensive tests covering:
  A. Embedding generation, dimension, determinism, serialization, empty text handling
  B. Semantic similarity (cosine similarity, bounds, multilingual similarity, unrelated text)
  C. Geographic score (same location, Haversine decay, beyond max distance, unresolved, missing)
  D. Temporal score (0m, 5m, linear decay 5–90m, 90m, >90m, missing timestamps)
  E. Incident type score (same, related pairs, unrelated, missing)
  F. Match formula (exact weights from config, contribution sums, bounds, no premature rounding)
  G. Full explainable match result (schema validation, report pair matching, DB storage/retrieval)
"""

import os
import sys
from datetime import datetime, timezone

import pytest

# Ensure backend/ is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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
from database import Base, SessionLocal, engine
from models import Extraction, Report
from schemas import MatchScoreBreakdown
from seed_data import SEED_REPORTS
from services.embedding_service import (
    clear_embedding_model_cache,
    cosine_similarity,
    deserialize_embedding,
    generate_embedding,
    generate_embeddings,
    get_embedding_model,
    serialize_embedding,
)
from services.matcher import (
    calculate_geographic_distance,
    calculate_geographic_score,
    calculate_incident_type_score,
    calculate_match_score,
    calculate_temporal_score,
    match_reports,
)
from services.normalizer import normalize_text


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def shared_model():
    """Load the sentence transformer model once for the test module."""
    return get_embedding_model()


# ---------------------------------------------------------------------------
# A. Embedding Tests
# ---------------------------------------------------------------------------

class TestEmbeddingGeneration:
    """Verify sentence embedding generation and serialization."""

    def test_embedding_generation_dimension(self, shared_model):
        """paraphrase-multilingual-MiniLM-L12-v2 must output 384 dimensions."""
        emb = generate_embedding("Civil Lines flooding on the main road.", model=shared_model)
        assert isinstance(emb, list)
        assert len(emb) == 384
        assert all(isinstance(x, float) for x in emb)

    def test_embedding_is_deterministic(self, shared_model):
        """Identical text must yield identical embeddings across repeated calls."""
        text = "Water entered ground floor of residential buildings."
        emb1 = generate_embedding(text, model=shared_model)
        emb2 = generate_embedding(text, model=shared_model)
        assert emb1 == pytest.approx(emb2, abs=1e-6)

    def test_empty_or_none_text_handling(self, shared_model):
        """Empty string, whitespace, or None returns a safe 384-dimensional zero vector."""
        emb_none = generate_embedding(None, model=shared_model)
        emb_empty = generate_embedding("", model=shared_model)
        emb_ws = generate_embedding("   \n\t  ", model=shared_model)

        assert len(emb_none) == 384
        assert all(x == 0.0 for x in emb_none)
        assert emb_empty == emb_none
        assert emb_ws == emb_none

    def test_batch_embedding_generation(self, shared_model):
        """Batch embedding produces correct shape and aligns with single generation."""
        texts = [
            "Civil Lines waterlogging",
            "Kotwali trapped family",
            "",
        ]
        batch = generate_embeddings(texts, model=shared_model)
        assert len(batch) == 3
        assert len(batch[0]) == 384
        assert len(batch[1]) == 384
        assert len(batch[2]) == 384
        assert all(x == 0.0 for x in batch[2])

        single_0 = generate_embedding(texts[0], model=shared_model)
        assert batch[0] == pytest.approx(single_0, abs=1e-5)

    def test_embedding_serialization_roundtrip(self):
        """Embedding serializes to JSON string and deserializes without loss of precision."""
        original = [0.12345678, -0.98765432, 0.0, 1.0]
        serialized = serialize_embedding(original)
        assert isinstance(serialized, str)

        recovered = deserialize_embedding(serialized)
        assert recovered == pytest.approx(original, abs=1e-7)

    def test_deserialize_handles_invalid_input(self):
        """Deserializer handles None, empty string, and corrupted JSON safely."""
        assert deserialize_embedding(None) is None
        assert deserialize_embedding("") is None
        assert deserialize_embedding("not a json string") is None
        assert deserialize_embedding("{\"not\": \"a list\"}") is None

    def test_offline_only_model_loading_no_network_fallback(self):
        """get_embedding_model must load from local cache with local_files_only=True."""
        model = get_embedding_model()
        assert model is not None

    def test_offline_model_not_found_raises_actionable_error(self):
        """Missing model in local cache must raise actionable RuntimeError, not hit network."""
        clear_embedding_model_cache()
        try:
            with pytest.raises(RuntimeError) as exc_info:
                get_embedding_model("nonexistent_model_xyz_test")
            assert "is not available in the local Hugging Face cache" in str(exc_info.value)
            assert "SETU operates offline" in str(exc_info.value)
        finally:
            clear_embedding_model_cache()


# ---------------------------------------------------------------------------
# B. Semantic Similarity Tests
# ---------------------------------------------------------------------------

class TestSemanticSimilarity:
    """Verify cosine similarity calculation and clamping to [0.0, 1.0] matching range."""

    def test_identical_text_similarity(self, shared_model):
        """Identical text must yield a semantic similarity of ~1.0."""
        text = "Severe flooding in Civil Lines, Rampur with rising water level."
        emb1 = generate_embedding(text, model=shared_model)
        emb2 = generate_embedding(text, model=shared_model)

        sim = cosine_similarity(emb1, emb2)
        assert sim == pytest.approx(1.0, abs=1e-4)

    def test_semantically_related_higher_than_unrelated(self, shared_model):
        """Semantically related flood reports must score higher than unrelated text."""
        t1 = "Water depth approximately 3 feet on main road, houses waterlogged."
        t2 = "Severe waterlogging on streets, flood water entered homes."
        t_unrelated = "Hospital cafeteria serves lunch daily from 12 to 2 PM."

        emb1 = generate_embedding(t1, model=shared_model)
        emb2 = generate_embedding(t2, model=shared_model)
        emb_un = generate_embedding(t_unrelated, model=shared_model)

        sim_related = cosine_similarity(emb1, emb2)
        sim_unrelated = cosine_similarity(emb1, emb_un)

        assert sim_related > 0.65
        assert sim_unrelated < 0.40
        assert sim_related > sim_unrelated

    def test_multilingual_semantic_similarity(self, shared_model):
        """
        Multilingual MiniLM must recognize semantic similarity across languages:
        English vs Devanagari Hindi vs Romanized Hindi on the same incident.
        """
        en_text = "Family trapped in basement near Kotwali, need rescue immediately!"
        hi_text = "कोतवाली के पास एक परिवार बेसमेंट में फंसा है। तुरंत मदद भेजो।"
        rom_text1 = "Kotwali ke paas basement mein log phase hain, bachao!"
        rom_text2 = "Kotwali ke paas ghar mein 3 log phase hain bachao"
        unrelated = "Sunny weather and clear skies today in the market."

        emb_en = generate_embedding(en_text, model=shared_model)
        emb_hi = generate_embedding(hi_text, model=shared_model)
        emb_rom1 = generate_embedding(rom_text1, model=shared_model)
        emb_rom2 = generate_embedding(rom_text2, model=shared_model)
        emb_un = generate_embedding(unrelated, model=shared_model)

        sim_en_hi = cosine_similarity(emb_en, emb_hi)
        sim_rom1_rom2 = cosine_similarity(emb_rom1, emb_rom2)
        sim_en_rom = cosine_similarity(emb_en, emb_rom1)
        sim_unrelated = cosine_similarity(emb_en, emb_un)

        # Cross-lingual English vs Devanagari Hindi should be very high (>0.80)
        assert sim_en_hi > 0.80
        # Romanized Hindi reports on same incident should align (>0.50)
        assert sim_rom1_rom2 > 0.50
        # Even cross-script Romanized vs English exceeds unrelated
        assert sim_en_rom > sim_unrelated

    def test_missing_or_empty_embeddings_return_zero(self):
        """Missing or empty embedding vectors safely return 0.0 similarity."""
        valid_vec = [1.0] * 384
        assert cosine_similarity(None, valid_vec) == 0.0
        assert cosine_similarity(valid_vec, None) == 0.0
        assert cosine_similarity([], valid_vec) == 0.0
        assert cosine_similarity([0.0] * 384, valid_vec) == 0.0
        assert cosine_similarity([1.0] * 10, [1.0] * 20) == 0.0

    def test_similarity_strictly_bounded(self, shared_model):
        """Cosine similarity must strictly remain within [0.0, 1.0]."""
        v1 = [1.0, 0.0, -1.0]
        v2 = [-1.0, 0.0, 1.0]
        # Opposing vectors give negative cosine; must be clamped to 0.0
        sim = cosine_similarity(v1, v2)
        assert sim == 0.0


# ---------------------------------------------------------------------------
# C. Geographic Score Tests
# ---------------------------------------------------------------------------

class TestGeographicScore:
    """Verify geographic proximity scoring using Haversine distance and rules."""

    def test_same_canonical_location_id_scores_one(self):
        """Matching location_id scores 1.0 even without coordinate diff."""
        score = calculate_geographic_score(
            location_id1="civil_lines",
            lat1=28.7950,
            lon1=79.0250,
            location_id2="civil_lines",
            lat2=28.7950,
            lon2=79.0250,
        )
        assert score == GEO_SAME_LOCATION_SCORE

    def test_exact_same_coordinates_scores_one(self):
        """Identical coordinates score 1.0."""
        score = calculate_geographic_score(
            location_id1=None,
            lat1=28.7950,
            lon1=79.0250,
            location_id2=None,
            lat2=28.7950,
            lon2=79.0250,
        )
        assert score == 1.0

    def test_haversine_distance_calculation(self):
        """Verify Haversine distance accuracy between known coordinates."""
        # Civil Lines (28.7950, 79.0250) to Kotwali (28.8010, 79.0180) in Rampur
        dist = calculate_geographic_distance(28.7950, 79.0250, 28.8010, 79.0180)
        # Expected distance between these points is ~950–1000 meters
        assert 900 <= dist <= 1100

    def test_nearby_coordinates_decay_linearly(self):
        """Distance within GEO_MAX_DISTANCE_M (2000m) decays linearly: 1 - d/2000."""
        lat1, lon1 = 28.7950, 79.0250
        lat2, lon2 = 28.8010, 79.0180
        dist = calculate_geographic_distance(lat1, lon1, lat2, lon2)

        expected_score = 1.0 - (dist / GEO_MAX_DISTANCE_M)
        score = calculate_geographic_score(
            location_id1="loc1",
            lat1=lat1,
            lon1=lon1,
            location_id2="loc2",
            lat2=lat2,
            lon2=lon2,
        )
        assert score == pytest.approx(expected_score, abs=1e-5)
        assert 0.0 < score < 1.0

    def test_coordinates_beyond_max_distance_score_zero(self):
        """Distance > 2000m must receive a geographic score of exactly 0.0."""
        # 0.05 degrees latitude is ~5.5 km apart, well beyond 2000m
        score = calculate_geographic_score(
            location_id1="loc1",
            lat1=28.7950,
            lon1=79.0250,
            location_id2="loc2",
            lat2=28.8500,
            lon2=79.0250,
        )
        assert score == 0.0

    def test_unresolved_location_scores_zero(self):
        """If either location is unresolved (None coordinates), score is 0.0."""
        assert calculate_geographic_score(location_id1="loc1", lat1=None, lon1=None,
                                          location_id2="loc2", lat2=28.795, lon2=79.025) == 0.0
        assert calculate_geographic_score(location_id1="loc1", lat1=28.795, lon1=79.025,
                                          location_id2="loc2", lat2=None, lon2=None) == 0.0
        assert calculate_geographic_score(location_id1=None, lat1=None, lon1=None,
                                          location_id2=None, lat2=None, lon2=None) == 0.0

    def test_invalid_coordinate_range_scores_zero(self):
        """Invalid coordinate values (e.g. lat > 90) safely return 0.0."""
        score = calculate_geographic_score(
            lat1=95.0, lon1=79.0,
            lat2=28.0, lon2=79.0,
        )
        assert score == 0.0


# ---------------------------------------------------------------------------
# D. Temporal Score Tests
# ---------------------------------------------------------------------------

class TestTemporalScore:
    """Verify temporal proximity calculation based on UTC timestamps."""

    def test_identical_time_scores_one(self):
        """Reports at the same minute score 1.0."""
        t1 = "2026-09-07T08:15:00+00:00"
        t2 = "2026-09-07T08:15:00+00:00"
        assert calculate_temporal_score(t1, t2) == 1.0

    def test_within_5_minutes_scores_one(self):
        """Reports <= 5 minutes apart score 1.0."""
        t1 = "2026-09-07T08:00:00Z"
        t2 = "2026-09-07T08:05:00Z"  # exactly 5 min
        t3 = "2026-09-07T08:03:30Z"  # 3.5 min
        assert calculate_temporal_score(t1, t2) == 1.0
        assert calculate_temporal_score(t1, t3) == 1.0

    def test_linear_decay_between_5_and_90_minutes(self):
        """Decays linearly from 1.0 at 5m to 0.0 at 90m."""
        t_base = "2026-09-07T08:00:00Z"

        # Midpoint: (90 - 5) / 2 = 42.5 min after 5 min = 47.5 min apart
        # Expected score: 0.50
        t_mid = "2026-09-07T08:47:30Z"
        assert calculate_temporal_score(t_base, t_mid) == pytest.approx(0.50, abs=1e-4)

        # 26.25 min apart: delta=26.25, score = 1 - (21.25/85) = 1 - 0.25 = 0.75
        t_quarter = "2026-09-07T08:26:15Z"
        assert calculate_temporal_score(t_base, t_quarter) == pytest.approx(0.75, abs=1e-4)

    def test_at_90_minutes_scores_zero(self):
        """Reports exactly 90 minutes apart score 0.0."""
        t1 = "2026-09-07T08:00:00Z"
        t2 = "2026-09-07T09:30:00Z"
        assert calculate_temporal_score(t1, t2) == 0.0

    def test_beyond_90_minutes_scores_zero(self):
        """Reports > 90 minutes apart score 0.0."""
        t1 = "2026-09-07T08:00:00Z"
        t2 = "2026-09-07T10:00:00Z"  # 120 min
        assert calculate_temporal_score(t1, t2) == 0.0

    def test_missing_or_invalid_timestamps(self):
        """Missing or unparseable timestamps return 0.0."""
        assert calculate_temporal_score(None, "2026-09-07T08:00:00Z") == 0.0
        assert calculate_temporal_score("2026-09-07T08:00:00Z", None) == 0.0
        assert calculate_temporal_score("invalid", "2026-09-07T08:00:00Z") == 0.0
        assert calculate_temporal_score("", "") == 0.0

    def test_datetime_objects_supported(self):
        """Direct datetime objects with tzinfo work correctly."""
        dt1 = datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc)
        dt2 = datetime(2026, 9, 7, 8, 4, tzinfo=timezone.utc)
        assert calculate_temporal_score(dt1, dt2) == 1.0


# ---------------------------------------------------------------------------
# E. Incident Type Score Tests
# ---------------------------------------------------------------------------

class TestIncidentTypeScore:
    """Verify incident type relationship evaluation."""

    def test_same_type_scores_one(self):
        """Identical incident types score 1.0."""
        assert calculate_incident_type_score("flood", "flood") == 1.0
        assert calculate_incident_type_score("rescue_needed", "rescue_needed") == 1.0
        assert calculate_incident_type_score("power_outage", "power_outage") == 1.0

    def test_case_and_whitespace_insensitivity(self):
        """Case variations and whitespace do not affect matching."""
        assert calculate_incident_type_score(" Flood ", "flood") == 1.0
        assert calculate_incident_type_score("RESCUE_NEEDED", "rescue_needed") == 1.0

    def test_configured_related_types_score_half(self):
        """Pairs in RELATED_INCIDENT_TYPES score 0.5."""
        assert calculate_incident_type_score("flood", "waterlogging") == 0.5
        assert calculate_incident_type_score("waterlogging", "flood") == 0.5
        assert calculate_incident_type_score("flood", "rescue_needed") == 0.5
        assert calculate_incident_type_score("waterlogging", "road_blocked") == 0.5
        assert calculate_incident_type_score("power_outage", "structural_damage") == 0.5
        assert calculate_incident_type_score("flood", "structural_damage") == 0.5

    def test_unrelated_types_score_zero(self):
        """Unrelated pairs score 0.0."""
        assert calculate_incident_type_score("rescue_needed", "power_outage") == 0.0
        assert calculate_incident_type_score("road_blocked", "power_outage") == 0.0
        assert calculate_incident_type_score("road_blocked", "structural_damage") == 0.0

    def test_missing_or_none_types_score_zero(self):
        """Missing or empty incident types score 0.0."""
        assert calculate_incident_type_score(None, "flood") == 0.0
        assert calculate_incident_type_score("flood", None) == 0.0
        assert calculate_incident_type_score("", "") == 0.0


# ---------------------------------------------------------------------------
# F. Match Formula & Explainability Tests
# ---------------------------------------------------------------------------

class TestMatchScoreFormula:
    """Verify the 4-factor match formula weights, contributions, and bounds."""

    def test_weights_sum_to_one(self):
        """The 4 weights must sum to 1.0."""
        total = (
            MATCH_WEIGHT_SEMANTIC
            + MATCH_WEIGHT_GEOGRAPHIC
            + MATCH_WEIGHT_TEMPORAL
            + MATCH_WEIGHT_INCIDENT_TYPE
        )
        assert total == pytest.approx(1.0, abs=1e-7)

    def test_configured_weights_used(self):
        """Formula must use the exact configured weights: 0.40, 0.30, 0.15, 0.15."""
        res = calculate_match_score(
            semantic_score=1.0,
            geographic_score=1.0,
            temporal_score=1.0,
            incident_type_score=1.0,
        )
        assert res.semantic.weight == 0.40
        assert res.geographic.weight == 0.30
        assert res.temporal.weight == 0.15
        assert res.incident_type.weight == 0.15
        assert res.final_score == pytest.approx(1.0, abs=1e-6)

    def test_weighted_contributions_sum_to_final_score(self):
        """Sum of contributions must equal final_score."""
        res = calculate_match_score(
            semantic_score=0.91,
            geographic_score=1.00,
            temporal_score=0.83,
            incident_type_score=1.00,
        )
        expected_sem = 0.91 * 0.40
        expected_geo = 1.00 * 0.30
        expected_tmp = 0.83 * 0.15
        expected_typ = 1.00 * 0.15
        expected_final = expected_sem + expected_geo + expected_tmp + expected_typ

        assert res.semantic.contribution == pytest.approx(expected_sem, abs=1e-6)
        assert res.geographic.contribution == pytest.approx(expected_geo, abs=1e-6)
        assert res.temporal.contribution == pytest.approx(expected_tmp, abs=1e-6)
        assert res.incident_type.contribution == pytest.approx(expected_typ, abs=1e-6)
        assert res.final_score == pytest.approx(expected_final, abs=1e-6)

    def test_final_score_bounded_zero_to_one(self):
        """Final score must be bounded within [0.0, 1.0]."""
        zero_res = calculate_match_score(0.0, 0.0, 0.0, 0.0)
        assert zero_res.final_score == 0.0

        max_res = calculate_match_score(1.0, 1.0, 1.0, 1.0)
        assert max_res.final_score == 1.0

    def test_no_premature_rounding(self):
        """Scores maintain floating-point precision during calculation."""
        res = calculate_match_score(
            semantic_score=0.854321,
            geographic_score=0.765432,
            temporal_score=0.654321,
            incident_type_score=0.500000,
        )
        raw_sum = (
            0.854321 * 0.40
            + 0.765432 * 0.30
            + 0.654321 * 0.15
            + 0.500000 * 0.15
        )
        assert res.final_score == pytest.approx(raw_sum, abs=1e-7)


# ---------------------------------------------------------------------------
# G. Full Report Pair Matching Tests
# ---------------------------------------------------------------------------

class TestFullReportMatching:
    """Verify high-level report pair matching on actual benchmark reports."""

    def test_same_cluster_reports_match_highly(self, shared_model):
        """
        R001 and R002 from Civil Lines cluster should produce a high match score
        (same location, flood incident, close in time).
        """
        r1 = next(r for r in SEED_REPORTS if r["id"] == "R001")
        r2 = next(r for r in SEED_REPORTS if r["id"] == "R002")

        # Mock normalized and resolved fields as pipeline would produce
        rep1 = {
            "id": "R001",
            "normalized_text": normalize_text(r1["raw_text"]),
            "location_resolved": "civil_lines",
            "location_lat": 28.7950,
            "location_lon": 79.0250,
            "received_at": r1["received_at"],
            "incident_type": "flood",
        }
        rep2 = {
            "id": "R002",
            "normalized_text": normalize_text(r2["raw_text"]),
            "location_resolved": "civil_lines",
            "location_lat": 28.7950,
            "location_lon": 79.0250,
            "received_at": r2["received_at"],
            "incident_type": "flood",
        }

        result = match_reports(rep1, rep2)
        assert isinstance(result, MatchScoreBreakdown)
        assert result.geographic.score == 1.0
        assert result.incident_type.score == 1.0
        assert result.temporal.score == 1.0  # within 5 minutes
        assert result.semantic.score > 0.60
        # Total match score should easily meet or exceed cluster merge threshold (0.80)
        assert result.final_score >= 0.80

    def test_different_cluster_reports_match_poorly(self, shared_model):
        """
        R001 (Civil Lines, Flood) and R016 (Naya Mohalla, Power Outage)
        must produce a low match score.
        """
        r1 = next(r for r in SEED_REPORTS if r["id"] == "R001")
        r16 = next(r for r in SEED_REPORTS if r["id"] == "R016")

        rep1 = {
            "id": "R001",
            "normalized_text": normalize_text(r1["raw_text"]),
            "location_resolved": "civil_lines",
            "location_lat": 28.7950,
            "location_lon": 79.0250,
            "received_at": r1["received_at"],
            "incident_type": "flood",
        }
        rep16 = {
            "id": "R016",
            "normalized_text": normalize_text(r16["raw_text"]),
            "location_resolved": "naya_mohalla",
            "location_lat": 28.7930,
            "location_lon": 79.0100,
            "received_at": r16["received_at"],
            "incident_type": "power_outage",
        }

        result = match_reports(rep1, rep16)
        # Different location (~1.5 km apart, decayed geo), unrelated type (0.0)
        assert result.incident_type.score == 0.0
        assert result.final_score < 0.60

    def test_database_embedding_persistence(self, shared_model):
        """
        Embeddings can be saved to Report.embedding in SQLite,
        queried, and deserialized back to float vectors.
        """
        from database import init_db
        init_db()

        db = SessionLocal()
        try:
            # Query an existing report or seed if needed
            report = db.query(Report).filter(Report.id == "R001").first()
            if report is None:
                from seed_data import seed_database
                seed_database(db)
                report = db.query(Report).filter(Report.id == "R001").first()

            assert report is not None
            emb = generate_embedding(report.raw_text, model=shared_model)
            report.embedding = serialize_embedding(emb)
            db.commit()

            # Refresh from DB
            db.refresh(report)
            assert report.embedding is not None
            recovered = deserialize_embedding(report.embedding)
            assert recovered is not None
            assert len(recovered) == 384
            assert recovered == pytest.approx(emb, abs=1e-6)
        finally:
            db.close()

    def test_zero_coordinates_not_replaced_by_gps(self):
        """
        Verify that 0.0 coordinates (e.g. equator / prime meridian) are not treated as falsy
        and incorrectly replaced by GPS coordinates.
        """
        rep1 = {
            "id": "R_ZERO_1",
            "normalized_text": "Flooding at equator post",
            "location_resolved": None,
            "location_lat": 0.0,
            "location_lon": 0.0,
            "gps_lat": 28.7950,
            "gps_lon": 79.0250,
            "received_at": "2026-09-07T08:00:00Z",
            "incident_type": "flood",
        }
        rep2 = {
            "id": "R_ZERO_2",
            "normalized_text": "Flooding at equator post second report",
            "location_resolved": None,
            "location_lat": 0.0,
            "location_lon": 0.0,
            "gps_lat": 12.3456,
            "gps_lon": 56.7890,
            "received_at": "2026-09-07T08:01:00Z",
            "incident_type": "flood",
        }

        result = match_reports(rep1, rep2)
        # Both resolved locations are at (0.0, 0.0). Geographic score must be 1.0.
        # If 0.0 were falsy, it would fall through to disparate GPS coords, yielding 0.0.
        assert result.geographic.score == 1.0

    def test_stored_embedding_preferred_over_on_demand_generation(self):
        """
        Verify that match_reports prefers stored embeddings when present,
        and falls back to on-demand generation from text when missing.
        """
        # rep1 has a custom stored embedding (unit vector along dim 0)
        custom_emb = [1.0] + [0.0] * 383
        rep1 = {
            "id": "R_STORED_1",
            "normalized_text": "Severe water logging",
            "embedding": serialize_embedding(custom_emb),
            "location_lat": 28.7950,
            "location_lon": 79.0250,
            "received_at": "2026-09-07T08:00:00Z",
            "incident_type": "flood",
        }
        # rep2 has different text but identical stored embedding
        rep2 = {
            "id": "R_STORED_2",
            "normalized_text": "Completely different text that would normally have low similarity",
            "embedding": serialize_embedding(custom_emb),
            "location_lat": 28.7950,
            "location_lon": 79.0250,
            "received_at": "2026-09-07T08:00:00Z",
            "incident_type": "flood",
        }
        result = match_reports(rep1, rep2)
        # Because both used custom_emb, cosine similarity is exactly 1.0
        assert result.semantic.score == pytest.approx(1.0, abs=1e-5)

        # rep3 has no embedding: match_reports must fall back to generating from text
        rep3_no_emb = {
            "id": "R_NO_EMB",
            "raw_text": "Water depth 3 feet on road",
            "location_lat": 28.7950,
            "location_lon": 79.0250,
            "received_at": "2026-09-07T08:00:00Z",
            "incident_type": "flood",
        }
        result_fallback = match_reports(rep3_no_emb, rep3_no_emb)
        # Self-match generates embedding on demand and yields ~1.0
        assert result_fallback.semantic.score == pytest.approx(1.0, abs=1e-4)

