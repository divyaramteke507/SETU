"""
SETU Clustering Tests — Phase 5.

Verifies the Safe Incident Clustering Engine:
- Incremental complete-linkage clustering
- Strict prevention of transitive merging (transitive trap)
- Duplicate report ID rejection with ValueError
- Invalid match score rejection (<0, >1, NaN, inf, non-numeric, bool) with ValueError
- Exact threshold boundaries (>= 0.80 merge, 0.60–0.79 related, < 0.60 separate)
- Deterministic processing and tie-breaking
- Full pairwise evidence and membership decision preservation
- Database persistence roundtrip (Incident, IncidentReport, RelatedIncident)
- Integration with real reports
"""

import math
import os
import sys
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Ensure backend/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import CLUSTER_MERGE_THRESHOLD, CLUSTER_RELATED_THRESHOLD
from database import Base
from models import Incident, IncidentReport, RelatedIncident, Report
from schemas import (
    ClusterMembershipDecision,
    ClusterResult,
    ClusteringOutput,
    MatchFactor,
    MatchScoreBreakdown,
    PairwiseMatch,
    RelatedClusterPair,
)
from services.clusterer import (
    check_cluster_compatibility,
    cluster_reports,
    compute_all_pairwise_scores,
    compute_related_cluster_pairs,
    get_pairwise_match,
    persist_clustering_results,
    select_best_cluster,
    validate_match_score,
    validate_report_ids,
)


# ===========================================================================
# 1. Validation Tests: Duplicate IDs & Invalid Match Scores
# ===========================================================================

class TestClusteringValidation:
    """Test strict validation of inputs: duplicate IDs and invalid match scores."""

    def test_duplicate_report_ids_raises_value_error(self):
        """Duplicate report IDs must raise ValueError immediately and never deduplicate."""
        reports = [
            {"id": "R001", "raw_text": "Flood near bridge"},
            {"id": "R002", "raw_text": "Water rising fast"},
            {"id": "R001", "raw_text": "Duplicate report with same ID"},
        ]
        with pytest.raises(ValueError, match="Duplicate report ID detected: 'R001'"):
            cluster_reports(reports)

    def test_duplicate_report_ids_with_string_inputs(self):
        """Duplicate string IDs must also raise ValueError."""
        with pytest.raises(ValueError, match="Duplicate report ID detected: 'A'"):
            validate_report_ids(["A", "B", "A"])

    def test_valid_unique_report_ids(self):
        """Unique report IDs are extracted cleanly."""
        ids = validate_report_ids([{"id": "R001"}, {"id": "R002"}, {"id": "R003"}])
        assert ids == ["R001", "R002", "R003"]

    def test_invalid_score_negative_raises_value_error(self):
        """Negative scores (< 0.0) must raise ValueError and never be clamped."""
        with pytest.raises(ValueError, match="Invalid match score"):
            validate_match_score(-0.01)

    def test_invalid_score_greater_than_one_raises_value_error(self):
        """Scores > 1.0 must raise ValueError and never be clamped."""
        with pytest.raises(ValueError, match="Invalid match score"):
            validate_match_score(1.0001)

    def test_invalid_score_nan_raises_value_error(self):
        """NaN score must raise ValueError."""
        with pytest.raises(ValueError, match="Invalid match score"):
            validate_match_score(float("nan"))

    def test_invalid_score_infinity_raises_value_error(self):
        """Positive and negative infinity must raise ValueError."""
        with pytest.raises(ValueError, match="Invalid match score"):
            validate_match_score(float("inf"))
        with pytest.raises(ValueError, match="Invalid match score"):
            validate_match_score(float("-inf"))

    def test_invalid_score_boolean_raises_value_error(self):
        """Boolean True/False must raise ValueError (not coerced as 1.0/0.0)."""
        with pytest.raises(ValueError, match="Invalid match score"):
            validate_match_score(True)
        with pytest.raises(ValueError, match="Invalid match score"):
            validate_match_score(False)

    def test_invalid_score_non_numeric_raises_value_error(self):
        """String or None scores must raise ValueError."""
        with pytest.raises(ValueError, match="Invalid match score"):
            validate_match_score("0.85")
        with pytest.raises(ValueError, match="Invalid match score"):
            validate_match_score(None)

    def test_invalid_precomputed_scores_in_cluster_reports_raises_value_error(self):
        """cluster_reports must reject precomputed score mappings containing invalid scores."""
        scores = {("R001", "R002"): float("nan")}
        with pytest.raises(ValueError, match="Invalid match score"):
            cluster_reports(["R001", "R002"], precomputed_scores=scores)


# ===========================================================================
# 2. Critical Safety Invariant: Transitive Trap Prevention
# ===========================================================================

class TestTransitiveTrapPrevention:
    """
    CRITICAL INVARIANT:
    Incremental complete-linkage clustering MUST NEVER perform transitive closure.
    A-B=0.85, B-C=0.86, A-C=0.42 MUST produce [A, B] and [C], never [A, B, C].
    """

    def test_transitive_trap_complete_linkage_prevents_chain(self):
        """
        The classic transitive trap:
        A ↔ B = 0.85 (strong)
        B ↔ C = 0.86 (strong)
        A ↔ C = 0.42 (weak)

        Connected components would merge [A, B, C].
        Complete-linkage MUST produce [A, B] and [C].
        """
        reports = ["A", "B", "C"]
        scores = {
            ("A", "B"): 0.85,
            ("B", "C"): 0.86,
            ("A", "C"): 0.42,
        }

        output = cluster_reports(reports, precomputed_scores=scores)

        # MUST be exactly 2 clusters
        assert len(output.clusters) == 2, f"Expected 2 clusters, got {len(output.clusters)}"

        cluster_members = [c.report_ids for c in output.clusters]
        assert ["A", "B"] in cluster_members, f"Expected ['A', 'B'] in {cluster_members}"
        assert ["C"] in cluster_members, f"Expected ['C'] in {cluster_members}"

        # Ensure [A, B, C] is NOT present anywhere
        for c in output.clusters:
            assert len(c.report_ids) < 3, f"Transitive merge occurred: {c.report_ids}"

    def test_transitive_trap_rejection_reason_is_explainable(self):
        """The rejection decision for report C must explain that member A blocked it."""
        reports = ["A", "B", "C"]
        scores = {
            ("A", "B"): 0.85,
            ("B", "C"): 0.86,
            ("A", "C"): 0.42,
        }

        output = cluster_reports(reports, precomputed_scores=scores)
        c_cluster = next(c for c in output.clusters if "C" in c.report_ids)

        # C formed a new cluster
        decision = c_cluster.membership_decisions[0]
        assert decision.action == "new_cluster"
        assert decision.report_id == "C"

    def test_four_report_transitive_chain(self):
        """
        A ↔ B = 0.88, B ↔ C = 0.85, C ↔ D = 0.90
        A ↔ C = 0.40, B ↔ D = 0.45, A ↔ D = 0.30

        Chained similarities must NOT collapse into one mega-cluster.
        """
        reports = ["A", "B", "C", "D"]
        scores = {
            ("A", "B"): 0.88,
            ("A", "C"): 0.40,
            ("A", "D"): 0.30,
            ("B", "C"): 0.85,
            ("B", "D"): 0.45,
            ("C", "D"): 0.90,
        }

        output = cluster_reports(reports, precomputed_scores=scores)

        # A and B merge ([A, B]).
        # C cannot join [A, B] because A-C=0.40 < 0.80 -> C forms [C].
        # D arrives: check [A, B] (fails), check [C] (C-D=0.90 >= 0.80 -> joins [C, D]).
        # Result: [A, B] and [C, D].
        assert len(output.clusters) == 2
        cluster_members = [c.report_ids for c in output.clusters]
        assert ["A", "B"] in cluster_members
        assert ["C", "D"] in cluster_members


# ===========================================================================
# 3. Threshold Boundary Tests (>= 0.80, 0.60–0.79, < 0.60)
# ===========================================================================

class TestThresholdBoundaries:
    """Test precise threshold behaviors at 0.80, 0.79, 0.60, and 0.59 boundaries."""

    def test_two_reports_merge_at_or_above_threshold(self):
        """Reports scoring 0.85 merge into the same cluster."""
        reports = ["R001", "R002"]
        scores = {("R001", "R002"): 0.85}

        output = cluster_reports(reports, precomputed_scores=scores)
        assert len(output.clusters) == 1
        assert output.clusters[0].report_ids == ["R001", "R002"]
        assert len(output.related_pairs) == 0

    def test_exact_threshold_boundary_0_80_merges(self):
        """Precision test: exact 0.80 boundary MUST merge (>= 0.80 is the gate)."""
        reports = ["R001", "R002"]
        scores = {("R001", "R002"): 0.80}

        output = cluster_reports(reports, precomputed_scores=scores)
        assert len(output.clusters) == 1
        assert output.clusters[0].report_ids == ["R001", "R002"]
        assert len(output.related_pairs) == 0

    def test_two_reports_score_0_79_separate_and_related(self):
        """Reports scoring 0.79 must NOT merge, but MUST produce a 'possibly related' link."""
        reports = ["R001", "R002"]
        scores = {("R001", "R002"): 0.79}

        output = cluster_reports(reports, precomputed_scores=scores)
        assert len(output.clusters) == 2
        assert output.clusters[0].report_ids == ["R001"]
        assert output.clusters[1].report_ids == ["R002"]

        assert len(output.related_pairs) == 1
        assert output.related_pairs[0].score == 0.79
        assert output.related_pairs[0].best_pair == ("R001", "R002")

    def test_two_reports_score_0_60_separate_and_related(self):
        """Reports scoring 0.60 (lower bound of related) must NOT merge, but MUST produce a related link."""
        reports = ["R001", "R002"]
        scores = {("R001", "R002"): 0.60}

        output = cluster_reports(reports, precomputed_scores=scores)
        assert len(output.clusters) == 2
        assert len(output.related_pairs) == 1
        assert output.related_pairs[0].score == 0.60

    def test_two_reports_score_0_59_separate_not_related(self):
        """Reports scoring 0.59 (< 0.60) must be completely separate with NO related link."""
        reports = ["R001", "R002"]
        scores = {("R001", "R002"): 0.59}

        output = cluster_reports(reports, precomputed_scores=scores)
        assert len(output.clusters) == 2
        assert len(output.related_pairs) == 0

    def test_related_band_0_60_to_0_79_never_merged(self):
        """Verify multiple points in 0.60–0.79 range never merge."""
        for test_score in [0.60, 0.65, 0.70, 0.75, 0.7999]:
            reports = ["R001", "R002"]
            scores = {("R001", "R002"): test_score}
            output = cluster_reports(reports, precomputed_scores=scores)
            assert len(output.clusters) == 2, f"Score {test_score} merged when it should not"
            assert len(output.related_pairs) == 1, f"Score {test_score} should be related"


# ===========================================================================
# 4. Multi-Report & Incremental Membership Tests
# ===========================================================================

class TestMultiReportClustering:
    """Test multi-report cluster formation, full compatibility, and partial compatibility."""

    def test_three_reports_all_strongly_match_form_single_cluster(self):
        """When all three pairwise scores >= 0.80, all three form a single cluster [A, B, C]."""
        reports = ["A", "B", "C"]
        scores = {
            ("A", "B"): 0.85,
            ("B", "C"): 0.88,
            ("A", "C"): 0.82,
        }

        output = cluster_reports(reports, precomputed_scores=scores)
        assert len(output.clusters) == 1
        assert output.clusters[0].report_ids == ["A", "B", "C"]
        assert output.clusters[0].size == 3
        assert output.clusters[0].min_pairwise_score == 0.82
        assert len(output.clusters[0].pairwise_matches) == 3

    def test_new_report_compatible_with_all_members_joins(self):
        """Report C is compatible with both members of existing cluster [A, B] and joins."""
        reports = ["A", "B", "C"]
        scores = {
            ("A", "B"): 0.90,
            ("A", "C"): 0.85,
            ("B", "C"): 0.82,
        }

        output = cluster_reports(reports, precomputed_scores=scores)
        assert len(output.clusters) == 1
        assert output.clusters[0].report_ids == ["A", "B", "C"]

    def test_new_report_compatible_with_only_one_member_rejected(self):
        """Report C is compatible with A (0.85) but not B (0.75), so it cannot join [A, B]."""
        reports = ["A", "B", "C"]
        scores = {
            ("A", "B"): 0.90,
            ("A", "C"): 0.85,
            ("B", "C"): 0.75,
        }

        output = cluster_reports(reports, precomputed_scores=scores)
        assert len(output.clusters) == 2
        cluster_members = [c.report_ids for c in output.clusters]
        assert ["A", "B"] in cluster_members
        assert ["C"] in cluster_members

    def test_multiple_compatible_clusters_tie_breaking(self):
        """
        When report C is compatible with both Cluster 1 and Cluster 2,
        it must deterministically choose the cluster with higher average compatibility.
        """
        reports = ["A1", "A2", "B1", "B2", "C"]
        scores = {
            # Cluster 1: [A1, A2]
            ("A1", "A2"): 0.90,
            # Cluster 2: [B1, B2]
            ("B1", "B2"): 0.92,
            # C with Cluster 1 members: avg = 0.82
            ("A1", "C"): 0.81,
            ("A2", "C"): 0.83,
            # C with Cluster 2 members: avg = 0.89
            ("B1", "C"): 0.88,
            ("B2", "C"): 0.90,
            # Inter-cluster A and B: low
            ("A1", "B1"): 0.30,
            ("A1", "B2"): 0.30,
            ("A2", "B1"): 0.30,
            ("A2", "B2"): 0.30,
        }

        output = cluster_reports(reports, precomputed_scores=scores)
        assert len(output.clusters) == 2

        # C must join the B cluster because avg score with B (0.89) > avg score with A (0.82)
        c_cluster = next(c for c in output.clusters if "C" in c.report_ids)
        assert "B1" in c_cluster.report_ids
        assert "B2" in c_cluster.report_ids
        assert "A1" not in c_cluster.report_ids

    def test_no_duplicate_reverse_relationships(self):
        """Related cluster pairs must be stored in one direction only (lower cluster ID first)."""
        reports = ["R001", "R002", "R003"]
        scores = {
            ("R001", "R002"): 0.75,  # related
            ("R002", "R003"): 0.72,  # related
            ("R001", "R003"): 0.30,  # separate
        }

        output = cluster_reports(reports, precomputed_scores=scores)
        assert len(output.clusters) == 3

        # Check that related_pairs has strictly unique pairs and no reverse pairs
        seen_pairs = set()
        for rel in output.related_pairs:
            pair = (rel.cluster_a_id, rel.cluster_b_id)
            reverse_pair = (rel.cluster_b_id, rel.cluster_a_id)
            assert pair not in seen_pairs
            assert reverse_pair not in seen_pairs
            assert rel.cluster_a_id < rel.cluster_b_id
            seen_pairs.add(pair)


# ===========================================================================
# 5. Boundary & Edge Cases
# ===========================================================================

class TestEdgeCases:
    """Test empty inputs, single reports, and determinism under shuffling."""

    def test_empty_input_returns_zero_clusters(self):
        """Empty report list must return 0 clusters and 0 related pairs."""
        output = cluster_reports([])
        assert len(output.clusters) == 0
        assert len(output.related_pairs) == 0
        assert output.total_reports == 0
        assert output.total_clusters == 0

    def test_single_report_returns_singleton_cluster(self):
        """A single report produces 1 cluster of size 1 with min/avg score 1.0."""
        output = cluster_reports(["R001"])
        assert len(output.clusters) == 1
        c = output.clusters[0]
        assert c.cluster_id == "INC-001"
        assert c.report_ids == ["R001"]
        assert c.size == 1
        assert c.min_pairwise_score == 1.0
        assert c.avg_pairwise_score == 1.0
        assert len(c.pairwise_matches) == 0
        assert len(output.related_pairs) == 0

    def test_deterministic_clustering_independent_of_input_order(self):
        """Shuffling input report order must yield identical cluster groupings."""
        reports_order1 = ["R001", "R002", "R003", "R004"]
        reports_order2 = ["R004", "R002", "R001", "R003"]
        reports_order3 = ["R003", "R001", "R004", "R002"]

        scores = {
            ("R001", "R002"): 0.85,
            ("R001", "R003"): 0.40,
            ("R001", "R004"): 0.35,
            ("R002", "R003"): 0.45,
            ("R002", "R004"): 0.30,
            ("R003", "R004"): 0.90,
        }

        out1 = cluster_reports(reports_order1, precomputed_scores=scores)
        out2 = cluster_reports(reports_order2, precomputed_scores=scores)
        out3 = cluster_reports(reports_order3, precomputed_scores=scores)

        members1 = sorted([c.report_ids for c in out1.clusters])
        members2 = sorted([c.report_ids for c in out2.clusters])
        members3 = sorted([c.report_ids for c in out3.clusters])

        assert members1 == members2 == members3
        assert members1 == [["R001", "R002"], ["R003", "R004"]]

    def test_cluster_result_preserves_pairwise_matches_and_decisions(self):
        """ClusterResult must preserve all pairwise matches and explainable decisions."""
        reports = ["R001", "R002"]
        breakdown = MatchScoreBreakdown(
            semantic=MatchFactor(score=0.9, weight=0.4, contribution=0.36),
            geographic=MatchFactor(score=1.0, weight=0.3, contribution=0.30),
            temporal=MatchFactor(score=0.8, weight=0.15, contribution=0.12),
            incident_type=MatchFactor(score=1.0, weight=0.15, contribution=0.15),
            final_score=0.93,
        )
        scores = {("R001", "R002"): breakdown}

        output = cluster_reports(reports, precomputed_scores=scores)
        c = output.clusters[0]

        assert len(c.pairwise_matches) == 1
        pm = c.pairwise_matches[0]
        assert pm.score == 0.93
        assert pm.breakdown is not None
        assert pm.breakdown.semantic.score == 0.9

        assert len(c.membership_decisions) == 2
        assert c.membership_decisions[0].action == "new_cluster"
        assert c.membership_decisions[1].action == "join"


# ===========================================================================
# 6. Database Persistence Tests
# ===========================================================================

class TestDatabasePersistence:
    """Test persisting clusters into Incident, IncidentReport, and RelatedIncident models."""

    @pytest.fixture
    def db_session(self):
        """Create a fresh in-memory SQLite session."""
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        session = Session()

        # Seed reports
        r1 = Report(
            id="R001",
            source="whatsapp",
            raw_text="Flood near bridge",
            received_at="2026-09-01T10:00:00+05:30",
            language="en",
            reporter_id="user1",
            location_resolved="civil_lines",
            location_lat=28.805,
            location_lon=79.025,
            geo_confidence=1.0,
        )
        r2 = Report(
            id="R002",
            source="sms",
            raw_text="Water level rising near bridge",
            received_at="2026-09-01T10:10:00+05:30",
            language="en",
            reporter_id="user2",
            location_resolved="civil_lines",
            location_lat=28.805,
            location_lon=79.025,
            geo_confidence=1.0,
        )
        r3 = Report(
            id="R003",
            source="field_worker",
            raw_text="Road blocked due to fallen tree in Jwala Nagar",
            received_at="2026-09-01T10:20:00+05:30",
            language="en",
            reporter_id="worker1",
            location_resolved="jwala_nagar",
            location_lat=28.815,
            location_lon=79.010,
            geo_confidence=1.0,
        )
        session.add_all([r1, r2, r3])
        session.commit()

        yield session

        session.close()

    def test_persistence_roundtrip_to_database(self, db_session):
        """Verify that persist_clustering_results correctly populates all ORM tables."""
        scores = {
            ("R001", "R002"): 0.88,  # merge
            ("R001", "R003"): 0.35,  # separate
            ("R002", "R003"): 0.65,  # related
        }

        output = cluster_reports(["R001", "R002", "R003"], precomputed_scores=scores)
        assert len(output.clusters) == 2
        assert len(output.related_pairs) == 1

        incidents = persist_clustering_results(db_session, output)
        assert len(incidents) == 2

        # Check Incidents in DB
        db_incidents = db_session.query(Incident).all()
        assert len(db_incidents) == 2
        inc_ids = [inc.id for inc in db_incidents]
        assert "INC-001" in inc_ids
        assert "INC-002" in inc_ids

        # Check IncidentReports in DB
        db_links = db_session.query(IncidentReport).all()
        assert len(db_links) == 3
        r1_link = next(l for l in db_links if l.report_id == "R001")
        assert r1_link.incident_id == "INC-001"
        assert r1_link.match_score is not None

        # Check RelatedIncidents in DB
        db_related = db_session.query(RelatedIncident).all()
        assert len(db_related) == 1
        assert db_related[0].incident_a_id == "INC-001"
        assert db_related[0].incident_b_id == "INC-002"
        assert db_related[0].score == 0.65

    def test_persistence_empty_database_generates_standard_ids(self, db_session):
        """1. Empty database -> INC-001, INC-002... work as before."""
        scores = {("R001", "R002"): 0.88, ("R001", "R003"): 0.35, ("R002", "R003"): 0.30}
        output = cluster_reports(["R001", "R002", "R003"], precomputed_scores=scores)
        incidents = persist_clustering_results(db_session, output)
        persisted_ids = [inc.id for inc in incidents]
        assert persisted_ids == ["INC-001", "INC-002"]

    def test_persistence_collision_with_existing_inc_001_resolves_to_inc_002(self, db_session):
        """2. Database already contains INC-001 -> new cluster assigned non-conflicting ID (INC-002) and succeeds."""
        existing = Incident(
            id="INC-001",
            status="verified",
            title="Pre-existing Incident INC-001",
        )
        db_session.add(existing)
        db_session.commit()

        output = cluster_reports(["R001"])
        assert output.clusters[0].cluster_id == "INC-001"

        new_incidents = persist_clustering_results(db_session, output)
        assert len(new_incidents) == 1
        assert new_incidents[0].id == "INC-002"

        db_inc_ids = [inc.id for inc in db_session.query(Incident).all()]
        assert "INC-001" in db_inc_ids
        assert "INC-002" in db_inc_ids

    def test_persistence_collision_with_existing_inc_001_and_002_assigns_inc_003_and_004(self, db_session):
        """3. Database contains INC-001 and INC-002 -> both new clusters receive non-conflicting IDs (INC-003, INC-004)."""
        existing1 = Incident(id="INC-001", status="verified", title="Existing 1")
        existing2 = Incident(id="INC-002", status="verified", title="Existing 2")
        db_session.add_all([existing1, existing2])
        db_session.commit()

        scores = {("R001", "R002"): 0.35}
        output = cluster_reports(["R001", "R002"], precomputed_scores=scores)
        assert [c.cluster_id for c in output.clusters] == ["INC-001", "INC-002"]

        new_incidents = persist_clustering_results(db_session, output)
        new_ids = [inc.id for inc in new_incidents]
        assert new_ids == ["INC-003", "INC-004"]

        all_ids = sorted([inc.id for inc in db_session.query(Incident).all()])
        assert all_ids == ["INC-001", "INC-002", "INC-003", "INC-004"]

    def test_existing_incidents_remain_unmodified_after_persistence(self, db_session):
        """4. Existing incidents are not modified or deleted during persistence."""
        existing = Incident(
            id="INC-001",
            status="verified",
            title="Untouched Original Incident",
            geo_confidence=0.95,
        )
        db_session.add(existing)
        db_session.commit()

        output = cluster_reports(["R001"])
        persist_clustering_results(db_session, output)

        reloaded = db_session.query(Incident).filter(Incident.id == "INC-001").first()
        assert reloaded is not None
        assert reloaded.title == "Untouched Original Incident"
        assert reloaded.status == "verified"
        assert reloaded.geo_confidence == 0.95

    def test_incident_reports_reference_actual_newly_persisted_incident_ids(self, db_session):
        """5. IncidentReport references point to the actual newly persisted Incident IDs after remapping."""
        existing = Incident(id="INC-001", status="verified", title="Existing")
        db_session.add(existing)
        db_session.commit()

        output = cluster_reports(["R001"])
        persist_clustering_results(db_session, output)

        ir = db_session.query(IncidentReport).filter(IncidentReport.report_id == "R001").first()
        assert ir is not None
        assert ir.incident_id == "INC-002"

    def test_related_incident_references_remain_valid_and_ordered_after_remapping(self, db_session):
        """6. RelatedIncident references remain valid and correctly ordered after ID remapping."""
        existing = Incident(id="INC-001", status="verified", title="Existing")
        db_session.add(existing)
        db_session.commit()

        scores = {("R001", "R002"): 0.75}
        output = cluster_reports(["R001", "R002"], precomputed_scores=scores)
        assert len(output.related_pairs) == 1

        persist_clustering_results(db_session, output)

        db_rel = db_session.query(RelatedIncident).all()
        assert len(db_rel) == 1
        rel = db_rel[0]
        assert rel.incident_a_id == "INC-002"
        assert rel.incident_b_id == "INC-003"
        assert rel.incident_a_id < rel.incident_b_id
        assert rel.score == 0.75

    def test_clustering_output_remains_unchanged_after_persistence(self, db_session):
        """7. Existing Phase 5 clustering output remains unchanged when persistence is called."""
        existing = Incident(id="INC-001", status="verified", title="Existing")
        db_session.add(existing)
        db_session.commit()

        scores = {("R001", "R002"): 0.85}
        output = cluster_reports(["R001", "R002"], precomputed_scores=scores)
        assert output.clusters[0].cluster_id == "INC-001"
        assert output.clusters[0].report_ids == ["R001", "R002"]

        persist_clustering_results(db_session, output)

        assert output.clusters[0].cluster_id == "INC-001"
        assert output.clusters[0].report_ids == ["R001", "R002"]


# ===========================================================================
# 7. End-to-End Integration with Real Processed Reports
# ===========================================================================

class TestRealReportClusteringIntegration:
    """Test clustering with real report models and the real match_reports() scorer."""

    def test_full_pipeline_with_real_processed_reports(self):
        """
        Verify that real Report models can be passed to cluster_reports()
        and the match_reports() function evaluates their pairwise scores.
        """
        r1 = Report(
            id="R001",
            source="whatsapp",
            raw_text="Heavy water logging at Civil Lines near district hospital. 4 feet water.",
            normalized_text="Heavy water logging at Civil Lines near district hospital. 4 feet water.",
            received_at="2026-09-01T10:00:00+05:30",
            language="en",
            reporter_id="user1",
            location_resolved="civil_lines",
            location_lat=28.805,
            location_lon=79.025,
            geo_confidence=1.0,
        )
        r2 = Report(
            id="R002",
            source="sms",
            raw_text="Civil Lines hospital road completely submerged. Need water pump.",
            normalized_text="Civil Lines hospital road completely submerged. Need water pump.",
            received_at="2026-09-01T10:05:00+05:30",
            language="en",
            reporter_id="user2",
            location_resolved="civil_lines",
            location_lat=28.805,
            location_lon=79.025,
            geo_confidence=1.0,
        )
        r3 = Report(
            id="R003",
            source="field_worker",
            raw_text="Building collapsed in Old City near Jama Masjid. 5 people trapped.",
            normalized_text="Building collapsed in Old City near Jama Masjid. 5 people trapped.",
            received_at="2026-09-01T10:00:00+05:30",
            language="en",
            reporter_id="worker1",
            location_resolved="old_city",
            location_lat=28.815,
            location_lon=79.035,
            geo_confidence=1.0,
        )

        output = cluster_reports([r1, r2, r3])

        # R1 and R2 describe the same incident in Civil Lines and should cluster together.
        # R3 is an unrelated incident in Old City and must be in a separate cluster.
        assert len(output.clusters) >= 2
        r1_cluster = next(c for c in output.clusters if "R001" in c.report_ids)
        r3_cluster = next(c for c in output.clusters if "R003" in c.report_ids)

        assert "R001" in r1_cluster.report_ids
        assert "R003" not in r1_cluster.report_ids
        assert r1_cluster.cluster_id != r3_cluster.cluster_id
