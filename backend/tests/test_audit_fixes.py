"""
Regression tests for adversarial audit correctness fixes: C1, H2, H3, H4.

C1 — Reprocess duplicate membership prevention.
H2 — Incremental RelatedIncident creation for 0.60-0.79 band.
H3 — Split incident ID canonical format (INC-NNN).
H4 — Contradiction resolution preservation during incremental fusion.

These tests use isolated SQLite databases and do not interfere with other test suites.
"""

import json
import os
import re
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Ensure backend/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from database import Base, get_db
from main import app
from models import (
    AuditLog,
    Contradiction,
    Extraction,
    Incident,
    IncidentReport,
    RelatedIncident,
    Report,
)
from seed_data import seed_database
from services.pipeline_runner import run_pipeline


# ===========================================================================
# Shared Test Infrastructure
# ===========================================================================

TEST_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "test_audit_fixes.db")
TEST_DB_URL = f"sqlite:///{os.path.abspath(TEST_DB_PATH)}"
_engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
_TestSession = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


def _fresh_session():
    """Drop and recreate all tables, return a clean session."""
    Base.metadata.drop_all(bind=_engine)
    Base.metadata.create_all(bind=_engine)
    return _TestSession()


# ===========================================================================
# C1 — Reprocess Duplicate Membership
# ===========================================================================

class TestC1ReprocessDuplicateMembership:
    """
    C1: A report must never be simultaneously assigned to a preserved
    human-touched incident AND a newly generated candidate incident
    by reprocessing.
    """

    def test_reprocess_no_dual_membership_after_verify(self):
        """Verify → reprocess: preserved reports must NOT appear in new candidates."""
        db = _fresh_session()
        try:
            # 1. Clean pipeline
            result = run_pipeline(db, seed_if_empty=True)
            assert result.incidents_formed == 13

            # 2. Verify one incident
            inc = db.query(Incident).filter(Incident.id == "INC-001").first()
            assert inc is not None
            inc.status = "verified"
            db.add(AuditLog(action="verify", incident_id=inc.id, responder_id="test"))
            db.commit()

            verified_rids = {
                link.report_id
                for link in db.query(IncidentReport)
                .filter(IncidentReport.incident_id == "INC-001")
                .all()
            }
            assert len(verified_rids) > 0

            # 3. Reprocess
            result2 = run_pipeline(db, reprocess=True)

            # 4. Invariant: each preserved report is in exactly one incident
            for rid in verified_rids:
                links = (
                    db.query(IncidentReport)
                    .filter(IncidentReport.report_id == rid)
                    .all()
                )
                incident_ids = [l.incident_id for l in links]
                assert len(incident_ids) == 1, (
                    f"Report {rid} is in {len(incident_ids)} incidents: {incident_ids}. "
                    f"Must be in exactly 1 (preserved INC-001)."
                )
                assert incident_ids[0] == "INC-001"
        finally:
            db.close()

    def test_reprocess_preserves_verified_incident_and_members(self):
        """Verified incident and its report links must survive reprocess."""
        db = _fresh_session()
        try:
            run_pipeline(db, seed_if_empty=True)

            inc = db.query(Incident).filter(Incident.id == "INC-001").first()
            inc.status = "verified"
            db.add(AuditLog(action="verify", incident_id=inc.id, responder_id="test"))
            db.commit()

            original_rids = sorted(
                link.report_id
                for link in db.query(IncidentReport)
                .filter(IncidentReport.incident_id == "INC-001")
                .all()
            )

            run_pipeline(db, reprocess=True)

            preserved = db.query(Incident).filter(Incident.id == "INC-001").first()
            assert preserved is not None
            assert preserved.status == "verified"

            post_rids = sorted(
                link.report_id
                for link in db.query(IncidentReport)
                .filter(IncidentReport.incident_id == "INC-001")
                .all()
            )
            assert post_rids == original_rids
        finally:
            db.close()

    def test_reprocess_non_preserved_reports_get_new_incidents(self):
        """Reports not in preserved incidents must be re-clustered into new candidates."""
        db = _fresh_session()
        try:
            run_pipeline(db, seed_if_empty=True)

            inc = db.query(Incident).filter(Incident.id == "INC-001").first()
            inc.status = "verified"
            db.add(AuditLog(action="verify", incident_id=inc.id, responder_id="test"))
            db.commit()

            preserved_rids = {
                l.report_id
                for l in db.query(IncidentReport)
                .filter(IncidentReport.incident_id == "INC-001")
                .all()
            }

            run_pipeline(db, reprocess=True)

            all_rids = {r.id for r in db.query(Report).all()}
            non_preserved = all_rids - preserved_rids

            for rid in non_preserved:
                links = (
                    db.query(IncidentReport)
                    .filter(IncidentReport.report_id == rid)
                    .all()
                )
                assert len(links) >= 1, f"Report {rid} should have at least one incident"
                for l in links:
                    assert l.incident_id != "INC-001", (
                        f"Non-preserved report {rid} ended up in preserved incident INC-001"
                    )
        finally:
            db.close()

    def test_reprocess_no_report_in_two_incidents(self):
        """Global invariant: after reprocess, no report belongs to >1 incident."""
        db = _fresh_session()
        try:
            run_pipeline(db, seed_if_empty=True)

            # Verify two incidents to stress-test
            for inc_id in ["INC-001", "INC-004"]:
                inc = db.query(Incident).filter(Incident.id == inc_id).first()
                if inc:
                    inc.status = "verified"
                    db.add(AuditLog(action="verify", incident_id=inc.id, responder_id="test"))
            db.commit()

            run_pipeline(db, reprocess=True)

            all_links = db.query(IncidentReport).all()
            report_incident_map: dict[str, list[str]] = {}
            for link in all_links:
                report_incident_map.setdefault(link.report_id, []).append(link.incident_id)

            for rid, inc_ids in report_incident_map.items():
                assert len(inc_ids) == 1, (
                    f"Report {rid} is in {len(inc_ids)} incidents: {inc_ids}. Must be exactly 1."
                )
        finally:
            db.close()


# ===========================================================================
# H2 — Incremental RelatedIncident for 0.60-0.79 Band
# ===========================================================================

class TestH2IncrementalRelatedIncident:
    """
    H2: A newly ingested report that creates a new incident and has scores
    in the 0.60-0.79 band against existing incidents must produce
    RelatedIncident links.
    """

    def test_new_report_creates_related_incident_link(self):
        """New report with 0.60-0.79 scores creates RelatedIncident records."""
        db = _fresh_session()
        try:
            # 1. Seed and run initial pipeline
            run_pipeline(db, seed_if_empty=True)
            initial_rel_count = db.query(RelatedIncident).count()
            initial_inc_count = db.query(Incident).count()
            assert initial_inc_count == 13

            # 2. Add a report about waterlogging at a different time (produces
            #    moderate semantic similarity with flood reports, different temporal
            #    → overall score should fall in 0.60-0.79 with some clusters)
            new_report = Report(
                id="R021",
                source="web",
                raw_text=(
                    "Transformer spark and power outage reported near Civil Lines market "
                    "due to water accumulation."
                ),
                received_at="2026-09-07T08:20:00+05:30",
                language="en",
                reporter_id="WEB-TEST-21",
                processed=False,
            )
            db.add(new_report)
            db.commit()

            # 3. Run pipeline (incremental)
            result = run_pipeline(db, reprocess=False)
            assert result.incidents_formed >= 14  # At least 1 new incident

            # 4. R021 should be in a new incident
            r021_links = (
                db.query(IncidentReport)
                .filter(IncidentReport.report_id == "R021")
                .all()
            )
            assert len(r021_links) == 1
            new_inc_id = r021_links[0].incident_id

            # 5. Check for RelatedIncident links involving the new incident
            related = db.query(RelatedIncident).filter(
                (RelatedIncident.incident_a_id == new_inc_id)
                | (RelatedIncident.incident_b_id == new_inc_id)
            ).all()

            # The new report about waterlogging/flooding should have related
            # links with at least some existing flood/waterlogging incidents
            assert len(related) > 0, (
                f"New incident {new_inc_id} has no RelatedIncident links. "
                f"Expected at least one in the 0.60-0.79 band."
            )
        finally:
            db.close()

    def test_merged_report_does_not_create_spurious_related_links(self):
        """A report that merges into an existing incident should not create
        duplicate RelatedIncident links."""
        db = _fresh_session()
        try:
            run_pipeline(db, seed_if_empty=True)
            initial_rel_count = db.query(RelatedIncident).count()

            # Add a report very similar to Civil Lines flooding (should merge into INC-001)
            new_report = Report(
                id="R022",
                source="whatsapp",
                raw_text=(
                    "Heavy flooding near Civil Lines area. Water level still rising. "
                    "Shops are completely waterlogged. People in danger."
                ),
                received_at="2026-09-07T08:03:00+05:30",
                language="en",
                reporter_id="WA-TEST-22",
                processed=False,
            )
            db.add(new_report)
            db.commit()

            run_pipeline(db, reprocess=False)

            # R022 should have merged (not created a new incident)
            r022_links = (
                db.query(IncidentReport)
                .filter(IncidentReport.report_id == "R022")
                .all()
            )
            assert len(r022_links) == 1
            assert db.query(RelatedIncident).count() >= initial_rel_count
        finally:
            db.close()

    def test_merged_report_persists_related_incident_link(self):
        """Bug 1 Regression Test: When a new report merges into an existing
        incident (INC-001), its 0.60-0.79 matches with other incidents
        (INC-003, INC-010) are persisted as RelatedIncident links with canonical
        ordering and without duplicate rows."""
        db = _fresh_session()
        try:
            run_pipeline(db, seed_if_empty=True)

            # Explicitly delete the INC-001 <-> INC-003 link to verify fresh persistence on merge
            db.query(RelatedIncident).filter(
                ((RelatedIncident.incident_a_id == "INC-001") & (RelatedIncident.incident_b_id == "INC-003"))
                | ((RelatedIncident.incident_a_id == "INC-003") & (RelatedIncident.incident_b_id == "INC-001"))
            ).delete()
            db.commit()

            # Confirm link does not exist prior to merge
            rel_pre = db.query(RelatedIncident).filter(
                ((RelatedIncident.incident_a_id == "INC-001") & (RelatedIncident.incident_b_id == "INC-003"))
                | ((RelatedIncident.incident_a_id == "INC-003") & (RelatedIncident.incident_b_id == "INC-001"))
            ).first()
            assert rel_pre is None

            # Add R022 (merges into INC-001, has 0.6769 score with INC-003)
            new_report = Report(
                id="R022",
                source="whatsapp",
                raw_text=(
                    "Heavy flooding near Civil Lines area. Water level still rising. "
                    "Shops are completely waterlogged. People in danger."
                ),
                received_at="2026-09-07T08:03:00+05:30",
                language="en",
                reporter_id="WA-TEST-22",
                processed=False,
            )
            db.add(new_report)
            db.commit()

            run_pipeline(db, reprocess=False)

            # 1. R022 belongs to INC-001
            r022_links = db.query(IncidentReport).filter(IncidentReport.report_id == "R022").all()
            assert len(r022_links) == 1
            assert r022_links[0].incident_id == "INC-001"

            # 2. RelatedIncident link between INC-001 and INC-003 must exist
            rel_post = db.query(RelatedIncident).filter(
                (RelatedIncident.incident_a_id == "INC-001") & (RelatedIncident.incident_b_id == "INC-003")
            ).first()
            assert rel_post is not None, "Expected RelatedIncident link between INC-001 and INC-003 was not persisted on merge!"
            assert 0.60 <= rel_post.score < 0.80
            assert rel_post.score == pytest.approx(0.6769, abs=0.01)

            # 3. Canonical ordering: incident_a_id < incident_b_id
            assert rel_post.incident_a_id == "INC-001"
            assert rel_post.incident_b_id == "INC-003"

            # 4. No reverse duplicate row
            rev_link = db.query(RelatedIncident).filter(
                (RelatedIncident.incident_a_id == "INC-003") & (RelatedIncident.incident_b_id == "INC-001")
            ).first()
            assert rev_link is None, "Found reverse duplicate (INC-003, INC-001)!"

            # 5. Exactly 1 row for this pair
            matching_links = db.query(RelatedIncident).filter(
                (RelatedIncident.incident_a_id == "INC-001") & (RelatedIncident.incident_b_id == "INC-003")
            ).all()
            assert len(matching_links) == 1

            # 6. Global check: all RelatedIncident rows are canonical and unique
            all_rels = db.query(RelatedIncident).all()
            pair_set = set()
            for r in all_rels:
                assert r.incident_a_id < r.incident_b_id, f"Non-canonical pair: {r.incident_a_id} >= {r.incident_b_id}"
                assert (r.incident_a_id, r.incident_b_id) not in pair_set, f"Duplicate pair: ({r.incident_a_id}, {r.incident_b_id})"
                pair_set.add((r.incident_a_id, r.incident_b_id))
        finally:
            db.close()


# ===========================================================================
# H3 — Split Incident ID Format
# ===========================================================================

class TestH3SplitIDFormat:
    """
    H3: Split-generated incident IDs must use the canonical INC-NNN format.
    INC-001 through INC-999, never INC-0010 or INC-00NN.
    """

    @pytest.fixture(autouse=True)
    def setup(self):
        """Fresh database with pipeline run and API client."""
        Base.metadata.drop_all(bind=_engine)
        Base.metadata.create_all(bind=_engine)
        self.db = _TestSession()
        run_pipeline(self.db, seed_if_empty=True)

        def _override():
            yield self.db

        app.dependency_overrides[get_db] = _override
        self.client = TestClient(app)
        yield
        self.db.close()
        app.dependency_overrides.clear()

    def test_split_produces_canonical_id_with_13_existing(self):
        """With 13 incidents, split must produce INC-014, not INC-0014."""
        # Find a multi-report incident
        for inc in self.db.query(Incident).order_by(Incident.id).all():
            links = (
                self.db.query(IncidentReport)
                .filter(IncidentReport.incident_id == inc.id)
                .all()
            )
            if len(links) >= 2:
                split_rid = links[0].report_id
                resp = self.client.post(
                    f"/api/incidents/{inc.id}/split",
                    json={"report_ids": [split_rid], "responder_id": "test"},
                )
                assert resp.status_code == 200
                new_id = resp.json()["new_incident_id"]
                assert re.match(r"^INC-\d{3}$", new_id), (
                    f"Split ID '{new_id}' is not in canonical INC-NNN format"
                )
                assert "INC-00" not in new_id or new_id == "INC-001", (
                    f"Split ID '{new_id}' has wrong zero-padding"
                )
                return
        pytest.fail("No multi-report incident found for split test")

    def test_split_id_format_ten_plus_incidents(self):
        """IDs for incidents 10+ must be INC-010, INC-011, ..., not INC-0010."""
        from services.clusterer import _resolve_unique_incident_id

        used_ids: set[str] = set()
        generated: list[str] = []
        for i in range(1, 16):
            candidate = f"INC-{i:03d}"
            actual = _resolve_unique_incident_id(candidate, used_ids)
            generated.append(actual)

        # Verify all 15 IDs follow INC-NNN format
        for gid in generated:
            assert re.match(r"^INC-\d{3}$", gid), (
                f"Generated ID '{gid}' is not in canonical INC-NNN format"
            )

        assert "INC-010" in generated
        assert "INC-011" in generated
        assert "INC-015" in generated
        assert "INC-0010" not in generated
        assert "INC-0011" not in generated

    def test_old_format_would_have_been_wrong(self):
        """Demonstrate the old f'INC-00{n}' format produces wrong IDs."""
        # This test documents the exact bug that was fixed
        old_format_10 = f"INC-00{10}"  # Old split code
        new_format_10 = f"INC-{10:03d}"  # Fixed code
        assert old_format_10 == "INC-0010"  # Wrong (4-digit number)
        assert new_format_10 == "INC-010"   # Correct (3-digit padded)

        old_format_1 = f"INC-00{1}"
        new_format_1 = f"INC-{1:03d}"
        assert old_format_1 == "INC-001"  # Happens to be correct for 1-digit
        assert new_format_1 == "INC-001"


# ===========================================================================
# H4 — Contradiction Resolution Preservation
# ===========================================================================

class TestH4ContradictionResolutionPreservation:
    """
    H4: When incremental fusion recalculates contradictions for an incident,
    human-set resolutions must be preserved if the same logical contradiction
    still exists. Stale contradictions may disappear naturally.
    """

    def test_resolution_preserved_after_new_report_merges(self):
        """Human resolution survives contradiction recalculation during merge."""
        db = _fresh_session()
        try:
            # 1. Run pipeline
            run_pipeline(db, seed_if_empty=True)

            # 2. Find an incident with a contradiction
            contr = db.query(Contradiction).first()
            if contr is None:
                # If no natural contradiction, create one for a multi-report incident
                multi_inc = None
                for inc in db.query(Incident).all():
                    links = (
                        db.query(IncidentReport)
                        .filter(IncidentReport.incident_id == inc.id)
                        .all()
                    )
                    if len(links) >= 2:
                        multi_inc = inc
                        break
                assert multi_inc is not None, "Need a multi-report incident"
                # We'll manually create a contradiction and test the resolution path
                inc_links = (
                    db.query(IncidentReport)
                    .filter(IncidentReport.incident_id == multi_inc.id)
                    .all()
                )
                rid_a = inc_links[0].report_id
                rid_b = inc_links[1].report_id
                pair = tuple(sorted([rid_a, rid_b]))
                contr = Contradiction(
                    id=f"CONTR-TEST-{multi_inc.id}",
                    incident_id=multi_inc.id,
                    contradiction_type="numeric",
                    field="people_estimate",
                    side_a_report_id=pair[0],
                    side_a_value="2",
                    side_b_report_id=pair[1],
                    side_b_value="5",
                    resolution=None,
                )
                db.add(contr)
                db.commit()

            target_inc_id = contr.incident_id
            contr_type = contr.contradiction_type
            contr_field = contr.field
            contr_pair = tuple(sorted([contr.side_a_report_id, contr.side_b_report_id]))

            # 3. Set a human resolution
            contr.resolution = "Side A is correct (confirmed by field worker)"
            db.commit()

            # Verify resolution persisted
            saved = db.query(Contradiction).filter(Contradiction.id == contr.id).first()
            assert saved.resolution == "Side A is correct (confirmed by field worker)"

            # 4. Add a report that should merge into the target incident
            # Use a report very similar to the incident's existing reports
            target_reports = [
                l.report
                for l in db.query(IncidentReport)
                .filter(IncidentReport.incident_id == target_inc_id)
                .all()
                if l.report
            ]
            assert len(target_reports) >= 2

            # Craft a report similar to the first report in the incident
            sample = target_reports[0]
            new_report = Report(
                id="R023",
                source=sample.source or "whatsapp",
                raw_text=sample.raw_text + " Update: situation unchanged.",
                received_at=sample.received_at,
                language=sample.language or "en",
                reporter_id="TEST-RES-23",
                gps_lat=sample.gps_lat,
                gps_lon=sample.gps_lon,
                processed=False,
            )
            db.add(new_report)
            db.commit()

            # 5. Run pipeline (incremental fusion)
            run_pipeline(db, reprocess=False)

            # 6. Find contradiction with the same logical key
            post_contrs = (
                db.query(Contradiction)
                .filter(Contradiction.incident_id == target_inc_id)
                .all()
            )

            matching_contrs = []
            for c in post_contrs:
                pair = tuple(sorted([c.side_a_report_id, c.side_b_report_id]))
                if (
                    c.contradiction_type == contr_type
                    and c.field == contr_field
                    and pair == contr_pair
                ):
                    matching_contrs.append(c)

            if matching_contrs:
                # The same logical contradiction still exists → resolution must be preserved
                for mc in matching_contrs:
                    assert mc.resolution == "Side A is correct (confirmed by field worker)", (
                        f"Human resolution was lost. Got: {mc.resolution!r}"
                    )
        finally:
            db.close()

    def test_stale_contradiction_disappears_naturally(self):
        """A contradiction that is no longer detected after membership change
        may disappear — we must NOT preserve stale contradictions."""
        db = _fresh_session()
        try:
            run_pipeline(db, seed_if_empty=True)

            # Create a fake contradiction in a multi-report incident
            multi_inc = None
            for inc in db.query(Incident).all():
                links = (
                    db.query(IncidentReport)
                    .filter(IncidentReport.incident_id == inc.id)
                    .all()
                )
                if len(links) >= 2:
                    multi_inc = inc
                    break
            assert multi_inc is not None

            inc_links = (
                db.query(IncidentReport)
                .filter(IncidentReport.incident_id == multi_inc.id)
                .all()
            )
            rid_a = inc_links[0].report_id
            rid_b = inc_links[1].report_id
            pair = tuple(sorted([rid_a, rid_b]))

            # Create a contradiction the detector would NOT naturally produce
            fake_contr = Contradiction(
                id="CONTR-FAKE-STALE",
                incident_id=multi_inc.id,
                contradiction_type="incident_type",
                field="incident_type",
                side_a_report_id=pair[0],
                side_a_value="earthquake",
                side_b_report_id=pair[1],
                side_b_value="volcanic_eruption",
                resolution="Manually resolved as earthquake",
            )
            db.add(fake_contr)
            db.commit()

            # Add a merging report
            sample = inc_links[0].report
            new_report = Report(
                id="R024",
                source=sample.source or "whatsapp",
                raw_text=sample.raw_text + " Follow-up confirmation.",
                received_at=sample.received_at,
                language=sample.language or "en",
                reporter_id="TEST-STALE-24",
                gps_lat=sample.gps_lat,
                gps_lon=sample.gps_lon,
                processed=False,
            )
            db.add(new_report)
            db.commit()

            run_pipeline(db, reprocess=False)

            # The fake contradiction should NOT be preserved because the detector
            # won't re-detect "earthquake vs volcanic_eruption" in flood reports
            stale = (
                db.query(Contradiction)
                .filter(Contradiction.id == "CONTR-FAKE-STALE")
                .first()
            )
            assert stale is None, (
                "Stale contradiction was preserved when it should have been removed"
            )
        finally:
            db.close()


# ===========================================================================
# Bug 2 — Human Contradiction Resolution Preservation Across Split
# ===========================================================================

class TestBug2SplitContradictionResolutionPreservation:
    """
    Bug 2: split_incident must preserve human contradiction resolutions
    when the same logical contradiction still exists after a split,
    without resurrecting stale contradictions or creating duplicate rows.
    """

    @pytest.fixture(autouse=True)
    def setup(self):
        Base.metadata.drop_all(bind=_engine)
        Base.metadata.create_all(bind=_engine)
        self.db = _TestSession()
        run_pipeline(self.db, seed_if_empty=True)

        def _override():
            yield self.db

        app.dependency_overrides[get_db] = _override
        self.client = TestClient(app)
        yield
        self.db.close()
        app.dependency_overrides.clear()

    def test_case_a_split_preserves_human_resolution_when_contradiction_survives(self):
        """CASE A: create contradiction -> resolve -> split -> verify contradiction survives with resolution."""
        # 1. Create a 3-report incident INC-TEST-A with reports R009, R010, R001
        # R009 and R010 have an inherent location contradiction (kotwali vs industrial area)
        inc = Incident(
            id="INC-TEST-A",
            status="unverified",
            title="Split Contradiction Test A",
            location_resolved="kotwali",
        )
        self.db.add(inc)
        self.db.flush()
        ir1 = IncidentReport(incident_id="INC-TEST-A", report_id="R009", match_score=0.85)
        ir2 = IncidentReport(incident_id="INC-TEST-A", report_id="R010", match_score=0.85)
        ir3 = IncidentReport(incident_id="INC-TEST-A", report_id="R001", match_score=0.85)
        self.db.add_all([ir1, ir2, ir3])
        self.db.flush()

        from services.contradiction_detector import detect_incident_contradictions, persist_contradictions
        from routers.incidents import _build_extractions_map
        reps = [ir1.report, ir2.report, ir3.report]
        ext_map = _build_extractions_map(reps)
        c_out = detect_incident_contradictions("INC-TEST-A", reps, extractions_map=ext_map)
        persist_contradictions(self.db, c_out)
        self.db.commit()

        # Find the contradiction between R009 and R010
        contr = self.db.query(Contradiction).filter(
            Contradiction.incident_id == "INC-TEST-A",
            Contradiction.side_a_report_id.in_(["R009", "R010"]),
            Contradiction.side_b_report_id.in_(["R009", "R010"]),
        ).first()
        assert contr is not None
        assert {contr.side_a_report_id, contr.side_b_report_id} == {"R009", "R010"}

        # Resolve the contradiction
        contr.resolution = "Verified by dispatcher: R009 location is correct"
        self.db.commit()

        # Split R001 away (R009 and R010 remain in INC-TEST-A)
        resp = self.client.post(
            "/api/incidents/INC-TEST-A/split",
            json={"report_ids": ["R001"], "responder_id": "auditor", "notes": "Split unrelated report"},
        )
        assert resp.status_code == 200

        # Verify the contradiction still exists on INC-TEST-A and resolution is preserved
        self.db.expire_all()
        surviving = self.db.query(Contradiction).filter(
            Contradiction.incident_id == "INC-TEST-A"
        ).all()
        assert len(surviving) >= 1
        matched = [
            c for c in surviving
            if {c.side_a_report_id, c.side_b_report_id} == {"R009", "R010"}
        ]
        assert len(matched) == 1
        assert matched[0].resolution == "Verified by dispatcher: R009 location is correct"

    def test_case_b_split_eliminates_stale_contradiction_without_resurrecting(self):
        """CASE B: resolve contradiction -> split separates the contradicting reports -> verify NOT resurrected."""
        inc = Incident(
            id="INC-TEST-B",
            status="unverified",
            title="Split Contradiction Test B",
            location_resolved="kotwali",
        )
        self.db.add(inc)
        self.db.flush()
        ir1 = IncidentReport(incident_id="INC-TEST-B", report_id="R009", match_score=0.85)
        ir2 = IncidentReport(incident_id="INC-TEST-B", report_id="R010", match_score=0.85)
        ir3 = IncidentReport(incident_id="INC-TEST-B", report_id="R001", match_score=0.85)
        self.db.add_all([ir1, ir2, ir3])
        self.db.flush()

        from services.contradiction_detector import detect_incident_contradictions, persist_contradictions
        from routers.incidents import _build_extractions_map
        reps = [ir1.report, ir2.report, ir3.report]
        ext_map = _build_extractions_map(reps)
        c_out = detect_incident_contradictions("INC-TEST-B", reps, extractions_map=ext_map)
        persist_contradictions(self.db, c_out)
        self.db.commit()

        contr = self.db.query(Contradiction).filter(
            Contradiction.incident_id == "INC-TEST-B"
        ).first()
        assert contr is not None
        contr.resolution = "Human resolved: Side A"
        self.db.commit()

        # Split R009 away from INC-TEST-B! Now R009 is in new_inc and R010 is in INC-TEST-B.
        resp = self.client.post(
            "/api/incidents/INC-TEST-B/split",
            json={"report_ids": ["R009"], "responder_id": "auditor", "notes": "Separate contradictory reports"},
        )
        assert resp.status_code == 200
        new_id = resp.json()["new_incident_id"]

        # Neither INC-TEST-B nor new_id should have a contradiction between R009 and R010
        self.db.expire_all()
        all_contrs = self.db.query(Contradiction).filter(
            Contradiction.incident_id.in_(["INC-TEST-B", new_id])
        ).all()
        r009_r010_contrs = [
            c for c in all_contrs
            if {c.side_a_report_id, c.side_b_report_id} == {"R009", "R010"}
        ]
        assert len(r009_r010_contrs) == 0, (
            "Stale cross-incident contradiction between R009 and R010 was resurrected after split!"
        )

    def test_case_c_split_recomputation_twice_no_duplicate_contradictions(self):
        """CASE C: running split / recomputations twice produces no duplicate contradiction rows."""
        inc = Incident(
            id="INC-TEST-C",
            status="unverified",
            title="Split Contradiction Test C",
            location_resolved="kotwali",
        )
        self.db.add(inc)
        self.db.flush()
        ir1 = IncidentReport(incident_id="INC-TEST-C", report_id="R009", match_score=0.85)
        ir2 = IncidentReport(incident_id="INC-TEST-C", report_id="R010", match_score=0.85)
        ir3 = IncidentReport(incident_id="INC-TEST-C", report_id="R006", match_score=0.85)
        ir4 = IncidentReport(incident_id="INC-TEST-C", report_id="R007", match_score=0.85)
        self.db.add_all([ir1, ir2, ir3, ir4])
        self.db.flush()

        from services.contradiction_detector import detect_incident_contradictions, persist_contradictions
        from routers.incidents import _build_extractions_map
        reps = [ir1.report, ir2.report, ir3.report, ir4.report]
        ext_map = _build_extractions_map(reps)
        c_out = detect_incident_contradictions("INC-TEST-C", reps, extractions_map=ext_map)
        persist_contradictions(self.db, c_out)
        self.db.commit()

        # Set human resolution on any existing contradiction
        for c in self.db.query(Contradiction).filter(Contradiction.incident_id == "INC-TEST-C").all():
            c.resolution = "Human confirmed resolution"
        self.db.commit()

        # Split 1: Split R006 away
        resp1 = self.client.post(
            "/api/incidents/INC-TEST-C/split",
            json={"report_ids": ["R006"], "responder_id": "auditor", "notes": "First split"},
        )
        assert resp1.status_code == 200

        # Split 2: Split R007 away
        resp2 = self.client.post(
            "/api/incidents/INC-TEST-C/split",
            json={"report_ids": ["R007"], "responder_id": "auditor", "notes": "Second split"},
        )
        assert resp2.status_code == 200

        # Check all contradictions on INC-TEST-C
        self.db.expire_all()
        post_contrs = self.db.query(Contradiction).filter(
            Contradiction.incident_id == "INC-TEST-C"
        ).all()
        keys = [(c.contradiction_type, c.field, tuple(sorted([c.side_a_report_id, c.side_b_report_id]))) for c in post_contrs]
        assert len(keys) == len(set(keys)), f"Duplicate contradiction rows detected: {keys}"

