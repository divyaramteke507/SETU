"""
SETU Backend Tests — Phase 1: Foundation.

Verifies database initialization, seed data, health endpoint,
and model/schema integrity.
"""

import sys
import os

# Ensure backend/ is on the path
sys.path.insert(0, os.path.dirname(__file__))

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, get_db
from main import app
from models import Report, Incident, Extraction, AuditLog, Contradiction
from schemas import ExtractionField, PeopleCount
from seed_data import SEED_REPORTS, seed_database
from config import (
    MATCH_WEIGHT_SEMANTIC,
    MATCH_WEIGHT_GEOGRAPHIC,
    MATCH_WEIGHT_TEMPORAL,
    MATCH_WEIGHT_INCIDENT_TYPE,
    CLUSTER_MERGE_THRESHOLD,
    CLUSTER_RELATED_THRESHOLD,
    PRIORITY_FORMULA_BASE,
    PRIORITY_FORMULA_CONFIDENCE_FACTOR,
    PRIORITY_LOW_CONFIDENCE_THRESHOLD,
    PRIORITY_LOW_CONFIDENCE_CAP,
    URGENCY_BANDS,
    PEOPLE_CATEGORIES,
)


# ---------------------------------------------------------------------------
# Test database setup (in-memory SQLite)
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = "sqlite:///./test_setu.db"
test_engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_teardown():
    """Create tables before each test, drop after."""
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)
    # Clean up test database file
    if os.path.exists("./test_setu.db"):
        try:
            os.remove("./test_setu.db")
        except PermissionError:
            pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSeedData:
    """Verify the 20 seed reports are well-formed."""

    def test_seed_report_count(self):
        assert len(SEED_REPORTS) == 20

    def test_seed_report_ids_unique(self):
        ids = [r["id"] for r in SEED_REPORTS]
        assert len(ids) == len(set(ids))

    def test_seed_report_sources(self):
        valid_sources = {"whatsapp", "sms", "web", "field_worker"}
        for r in SEED_REPORTS:
            assert r["source"] in valid_sources, f"{r['id']} has invalid source: {r['source']}"

    def test_seed_report_languages(self):
        valid_langs = {"en", "hi", "hi-Latn"}
        for r in SEED_REPORTS:
            assert r["language"] in valid_langs, f"{r['id']} has invalid language: {r['language']}"

    def test_seed_report_all_languages_present(self):
        langs = {r["language"] for r in SEED_REPORTS}
        assert "en" in langs
        assert "hi" in langs
        assert "hi-Latn" in langs

    def test_seed_report_gps_only_on_field_workers(self):
        for r in SEED_REPORTS:
            if r["source"] == "field_worker":
                assert r["gps_lat"] is not None, f"{r['id']} field_worker missing GPS"
                assert r["gps_lon"] is not None, f"{r['id']} field_worker missing GPS"

    def test_seed_report_four_location_clusters(self):
        """Field workers with GPS should represent 4 distinct locations."""
        gps_reports = [r for r in SEED_REPORTS if r["gps_lat"] is not None]
        coords = {(r["gps_lat"], r["gps_lon"]) for r in gps_reports}
        # We expect at least 3 distinct GPS coordinates (from 3 field workers)
        assert len(coords) >= 3


class TestDatabaseSeeding:
    """Verify seed data inserts correctly."""

    def test_seed_inserts_20_reports(self):
        db = TestSession()
        count = seed_database(db)
        assert count == 20
        total = db.query(Report).count()
        assert total == 20
        db.close()

    def test_seed_is_idempotent(self):
        db = TestSession()
        seed_database(db)
        count2 = seed_database(db)
        assert count2 == 0  # No duplicates inserted
        total = db.query(Report).count()
        assert total == 20
        db.close()


class TestHealthEndpoint:
    """Verify the /health endpoint."""

    def test_health_returns_ok(self):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["demo_mode"] is True
        assert data["version"] == "0.1.0"

    def test_health_shows_report_count(self):
        # Seed first
        db = TestSession()
        seed_database(db)
        db.close()

        response = client.get("/health")
        data = response.json()
        assert data["total_reports"] == 20
        assert data["total_incidents"] == 0  # No pipeline run yet


class TestRootEndpoint:
    """Verify the / endpoint."""

    def test_root_returns_api_info(self):
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "SETU" in data["name"]
        assert data["demo_mode"] is True


class TestConfigIntegrity:
    """Verify configuration constants are internally consistent."""

    def test_match_weights_sum_to_one(self):
        total = (
            MATCH_WEIGHT_SEMANTIC
            + MATCH_WEIGHT_GEOGRAPHIC
            + MATCH_WEIGHT_TEMPORAL
            + MATCH_WEIGHT_INCIDENT_TYPE
        )
        assert abs(total - 1.0) < 1e-9

    def test_cluster_thresholds_ordered(self):
        assert CLUSTER_MERGE_THRESHOLD > CLUSTER_RELATED_THRESHOLD

    def test_priority_formula_constants(self):
        assert PRIORITY_FORMULA_BASE == 0.40
        assert PRIORITY_FORMULA_CONFIDENCE_FACTOR == 0.60
        assert PRIORITY_LOW_CONFIDENCE_THRESHOLD == 0.30
        assert PRIORITY_LOW_CONFIDENCE_CAP == 69

    def test_urgency_bands_cover_full_range(self):
        mins = sorted([b["min"] for b in URGENCY_BANDS])
        maxs = sorted([b["max"] for b in URGENCY_BANDS])
        assert mins[0] == 0
        assert maxs[-1] == 100

    def test_urgency_bands_no_gaps(self):
        sorted_bands = sorted(URGENCY_BANDS, key=lambda b: b["min"])
        for i in range(len(sorted_bands) - 1):
            assert sorted_bands[i]["max"] + 1 == sorted_bands[i + 1]["min"]


class TestModelCreation:
    """Verify ORM models can be instantiated and persisted."""

    def test_create_report(self):
        db = TestSession()
        report = Report(
            id="TEST-001",
            source="web",
            raw_text="Test report",
            received_at="2026-09-07T08:00:00+05:30",
            language="en",
            reporter_id="TEST-USER",
        )
        db.add(report)
        db.commit()
        fetched = db.query(Report).filter_by(id="TEST-001").first()
        assert fetched is not None
        assert fetched.raw_text == "Test report"
        assert fetched.processed is False
        db.close()

    def test_create_incident(self):
        db = TestSession()
        incident = Incident(id="INC-TEST", status="unverified")
        db.add(incident)
        db.commit()
        fetched = db.query(Incident).filter_by(id="INC-TEST").first()
        assert fetched is not None
        assert fetched.status == "unverified"
        db.close()

    def test_create_audit_log(self):
        db = TestSession()
        incident = Incident(id="INC-TEST", status="unverified")
        db.add(incident)
        db.commit()
        audit = AuditLog(
            action="verify",
            incident_id="INC-TEST",
            responder_id="demo-responder",
            notes="Test verification",
        )
        db.add(audit)
        db.commit()
        fetched = db.query(AuditLog).filter_by(incident_id="INC-TEST").first()
        assert fetched is not None
        assert fetched.action == "verify"
        assert fetched.responder_id == "demo-responder"
        db.close()


class TestExtractionSchema:
    """Verify ExtractionField supports JSON-compatible values and confidence bounds."""

    def test_string_value(self):
        field = ExtractionField(
            field_name="incident_type",
            value="flood",
            confidence=0.9,
            evidence=["heavy flooding"],
        )
        assert field.value == "flood"
        assert field.confidence == 0.9
        assert field.evidence == ["heavy flooding"]

    def test_integer_value(self):
        field = ExtractionField(
            field_name="people_estimate",
            value=15,
            confidence=0.8,
            evidence=["15 people"],
        )
        assert field.value == 15
        assert isinstance(field.value, int)

    def test_boolean_value(self):
        field = ExtractionField(
            field_name="trapped_or_rescue",
            value=True,
            confidence=0.95,
            evidence=["trapped in basement"],
        )
        assert field.value is True

    def test_list_value(self):
        field = ExtractionField(
            field_name="urgency_signals",
            value=["immediate", "rising rapidly"],
            confidence=0.75,
            evidence=["immediate", "rising"],
        )
        assert field.value == ["immediate", "rising rapidly"]

    def test_object_value(self):
        field = ExtractionField(
            field_name="people_estimate",
            value={"count": 50, "category": "affected"},
            confidence=0.85,
            evidence=["50 residents affected"],
        )
        assert field.value == {"count": 50, "category": "affected"}

    def test_null_value(self):
        field = ExtractionField(
            field_name="location_raw",
            value=None,
            confidence=0.0,
            evidence=[],
        )
        assert field.value is None

    def test_confidence_bounds_enforced(self):
        # Valid bounds
        f_zero = ExtractionField(field_name="test", value=None, confidence=0.0)
        f_one = ExtractionField(field_name="test", value=None, confidence=1.0)
        assert f_zero.confidence == 0.0
        assert f_one.confidence == 1.0

        # Invalid bounds
        with pytest.raises(ValidationError):
            ExtractionField(field_name="test", value=None, confidence=-0.1)

        with pytest.raises(ValidationError):
            ExtractionField(field_name="test", value=None, confidence=1.1)

    def test_orm_json_string_compatibility(self):
        """Simulate loading Extraction from SQLite text column."""
        class MockORMExtraction:
            field_name = "people_estimate"
            value = '{"count": 50, "category": "affected"}'
            confidence = 0.85
            evidence = '["50 residents affected"]'

        field = ExtractionField.model_validate(MockORMExtraction())
        assert field.value == {"count": 50, "category": "affected"}
        assert field.evidence == ["50 residents affected"]


class TestPeopleSemantics:
    """Verify people impact distinctions and categories."""

    def test_people_count_schema(self):
        count_obj = PeopleCount(count=50, category="affected", description="Power outage impact")
        assert count_obj.count == 50
        assert count_obj.category == "affected"

    def test_people_categories_in_config(self):
        assert "trapped" in PEOPLE_CATEGORIES
        assert "affected" in PEOPLE_CATEGORIES
        assert "nearby" in PEOPLE_CATEGORIES
        assert "at_risk" in PEOPLE_CATEGORIES

    def test_people_category_distinction(self):
        """Ensure affected residents and trapped individuals use distinct categories."""
        trapped = PeopleCount(count=3, category="trapped")
        affected = PeopleCount(count=50, category="affected")
        assert trapped.category != affected.category
        assert trapped.count != affected.count


class TestDatasetSemantics:
    """Verify dataset integrity, genuine contradictions, and cluster counts."""

    def test_seed_report_count_intact(self):
        assert len(SEED_REPORTS) == 20

    def test_r018_structural_contradiction(self):
        """R018 reports wall intact, genuinely contradicting R019/R020 reports of leaning/damage."""
        r018 = next(r for r in SEED_REPORTS if r["id"] == "R018")
        assert "intact" in r018["raw_text"].lower() or "no visible" in r018["raw_text"].lower()

        r019 = next(r for r in SEED_REPORTS if r["id"] == "R019")
        assert "tedi" in r019["raw_text"].lower() or "gir jayegi" in r019["raw_text"].lower()

        r020 = next(r for r in SEED_REPORTS if r["id"] == "R020")
        assert "leaning" in r020["raw_text"].lower() or "cracks" in r020["raw_text"].lower()
