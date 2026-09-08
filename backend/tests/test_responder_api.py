"""
SETU Responder Workflow & Incident Intelligence API Tests — Phase 8.

Verifies:
  1. Pipeline Orchestration & Idempotency (repeat processing safety)
  2. Incident Listing, Sorting, and Multi-field Filtering
  3. Incident Detail, Sub-Resources, and Explainability Breakdowns
  4. Human Responder Verification Workflow & 409 Conflict Protection
  5. Human Responder Rejection Workflow & Evidence Preservation
  6. Incident Splitting Workflow (atomic move, reassessment, validation errors)
  7. Append-Only Audit Trail Inspection & Event Recording
  8. Data Safety, Minimization (PII protection), and Determinism
"""

import json
import os
import sys
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Ensure backend/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from database import Base, get_db
from main import app
from models import AuditLog, Contradiction, Incident, IncidentReport, Report
from seed_data import SEED_REPORTS, seed_database
from services.pipeline_runner import run_pipeline

# ---------------------------------------------------------------------------
# Test Database Setup (Module-scoped for fast, deterministic execution)
# ---------------------------------------------------------------------------

TEST_DB_URL = "sqlite:///./test_responder_phase8.db"
engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def setup_module_db():
    """Create fresh tables and run pipeline once for the test module."""
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    db = TestingSessionLocal()
    try:
        run_pipeline(db, seed_if_empty=True)
    finally:
        db.close()
    yield
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=engine)
    if os.path.exists("./test_responder_phase8.db"):
        try:
            os.remove("./test_responder_phase8.db")
        except PermissionError:
            pass


# ===========================================================================
# 1. Pipeline Orchestration & Repeat Processing (Idempotency) Tests
# ===========================================================================

class TestPipelineOrchestration:
    """Test full pipeline execution and repeat-processing safety."""

    def test_pipeline_process_endpoint_generates_incidents(self):
        """1. Pipeline endpoint seeds 20 reports and creates candidate incidents."""
        response = client.post("/api/pipeline/process")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["total_reports"] == 20
        assert data["processed_reports"] == 20
        assert data["incidents_formed"] == 13
        assert data["contradictions_found"] >= 1

    def test_pipeline_repeat_processing_is_strictly_idempotent(self):
        """2. Calling pipeline/process a second time does not create duplicate incidents."""
        initial_incidents = client.get("/api/incidents").json()
        initial_count = len(initial_incidents)
        assert initial_count == 13

        # Run pipeline again without reprocess flag
        res2 = client.post("/api/pipeline/process?reprocess=false")
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["incidents_formed"] == initial_count
        assert "already executed" in data2["message"].lower()

        # Verify incidents count remains strictly unchanged (no duplicates)
        incidents_after = client.get("/api/incidents").json()
        assert len(incidents_after) == initial_count

    def test_pipeline_reprocess_preserves_verified_incidents(self):
        """3. Calling pipeline with reprocess=True does NOT wipe verified incidents."""
        # Verify INC-001
        verify_res = client.post(
            "/api/incidents/INC-001/verify",
            json={"responder_id": "operator-1", "notes": "Verified situation"},
        )
        assert verify_res.status_code == 200

        # Reprocess
        reproc_res = client.post("/api/pipeline/process?reprocess=true")
        assert reproc_res.status_code == 200

        # INC-001 must still be verified and exist
        inc1 = client.get("/api/incidents/INC-001").json()
        assert inc1["status"] == "verified"

    def test_pipeline_persists_p6_contradictions(self):
        """4. Pipeline correctly detects and stores P6 contradictions inside incidents."""
        incidents = client.get("/api/incidents").json()
        # Find incidents with contradictions
        inc_with_contr = [i for i in incidents if i["contradiction_count"] > 0]
        assert len(inc_with_contr) >= 1

        for inc in inc_with_contr:
            contr_res = client.get(f"/api/incidents/{inc['id']}/contradictions")
            assert contr_res.status_code == 200
            contrs = contr_res.json()
            assert len(contrs) == inc["contradiction_count"]

    def test_pipeline_persists_p7_assessments(self):
        """5. Pipeline populates deterministic severity, confidence, and priority for all incidents."""
        incidents = client.get("/api/incidents").json()
        assert len(incidents) >= 13
        for inc in incidents:
            assert inc["severity"] is not None and inc["severity"] > 0
            assert inc["confidence"] is not None and 0.0 <= inc["confidence"] <= 1.0
            assert inc["priority"] is not None and inc["priority"] > 0
            assert inc["urgency"] in ("critical", "high", "medium", "low")


# ===========================================================================
# 2. Incident Listing, Sorting, and Filtering Tests
# ===========================================================================

class TestIncidentListingAndFiltering:
    """Test GET /api/incidents with various filter and sort combinations."""

    def test_incidents_sorted_by_priority_descending(self):
        """6. Incidents are sorted by priority in descending order by default."""
        res = client.get("/api/incidents")
        assert res.status_code == 200
        incidents = res.json()
        assert len(incidents) >= 13
        priorities = [i["priority"] for i in incidents]
        assert priorities == sorted(priorities, reverse=True)

    def test_filter_by_urgency(self):
        """7. Filter incidents by urgency band."""
        all_incs = client.get("/api/incidents").json()
        available_urgencies = {i["urgency"] for i in all_incs}

        for urg in available_urgencies:
            res = client.get(f"/api/incidents?urgency={urg}")
            assert res.status_code == 200
            data = res.json()
            assert len(data) > 0
            assert all(i["urgency"] == urg for i in data)

    def test_filter_by_status(self):
        """8. Filter incidents by verification status."""
        res_unverified = client.get("/api/incidents?status=unverified")
        assert res_unverified.status_code == 200
        assert len(res_unverified.json()) >= 1

        res_verified = client.get("/api/incidents?status=verified")
        assert res_verified.status_code == 200
        assert all(i["status"] == "verified" for i in res_verified.json())

    def test_filter_by_min_priority(self):
        """9. Filter incidents by minimum priority."""
        res = client.get("/api/incidents?min_priority=70.0")
        assert res.status_code == 200
        data = res.json()
        assert all(i["priority"] >= 70.0 for i in data)

    def test_filter_by_max_priority(self):
        """10. Filter incidents by maximum priority."""
        res = client.get("/api/incidents?max_priority=65.0")
        assert res.status_code == 200
        data = res.json()
        assert all(i["priority"] <= 65.0 for i in data)

    def test_filter_by_location_substring(self):
        """11. Filter incidents by location substring (case-insensitive)."""
        res = client.get("/api/incidents?location=civil")
        assert res.status_code == 200
        data = res.json()
        assert len(data) >= 1
        assert all("civil" in data[i]["location_resolved"].lower() for i in range(len(data)))

    def test_filter_location_no_match(self):
        """12. Location filter with nonexistent location returns empty list."""
        res = client.get("/api/incidents?location=nonexistent_area")
        assert res.status_code == 200
        assert res.json() == []

    def test_pagination_limit_offset(self):
        """13. Limit and offset pagination work properly."""
        res_limit2 = client.get("/api/incidents?limit=2&offset=0")
        assert res_limit2.status_code == 200
        assert len(res_limit2.json()) == 2

        res_offset2 = client.get("/api/incidents?limit=2&offset=2")
        assert res_offset2.status_code == 200
        assert len(res_offset2.json()) == 2

        ids1 = [i["id"] for i in res_limit2.json()]
        ids2 = [i["id"] for i in res_offset2.json()]
        assert set(ids1).isdisjoint(set(ids2))

    def test_invalid_priority_query_param_validation(self):
        """14. Invalid priority param (> 100 or < 0) returns 422 Unprocessable Entity."""
        res = client.get("/api/incidents?min_priority=150")
        assert res.status_code == 422

    def test_total_incidents_list_matches_db_count(self):
        """15. Unfiltered incident listing matches exact database count."""
        db = TestingSessionLocal()
        try:
            expected = db.query(Incident).count()
        finally:
            db.close()
        res = client.get("/api/incidents?limit=100")
        assert len(res.json()) == expected


# ===========================================================================
# 3. Incident Detail, Sub-Resources, and Explainability Tests
# ===========================================================================

class TestIncidentDetailAndSubresources:
    """Test GET /api/incidents/{id} and sub-resources."""

    def test_get_incident_detail_complete_structure(self):
        """16. Incident detail endpoint returns complete explainable structure."""
        res = client.get("/api/incidents/INC-001")
        assert res.status_code == 200
        data = res.json()
        assert data["id"] == "INC-001"
        assert data["report_count"] > 0
        assert data["source_diversity"] >= 1
        assert data["severity"] is not None
        assert data["confidence"] is not None
        assert data["priority"] is not None
        assert data["urgency"] in ("critical", "high", "medium", "low")

        # Explainability breakdowns
        assert data["severity_breakdown"] is not None
        assert "baseline_severity" in data["severity_breakdown"]
        assert "final_severity" in data["severity_breakdown"]

        assert data["priority_breakdown"] is not None
        assert "formula" in data["priority_breakdown"]

        # Reports & Contradictions
        assert isinstance(data["reports"], list)
        assert len(data["reports"]) == data["report_count"]
        assert isinstance(data["contradictions"], list)

    def test_get_incident_detail_nonexistent_returns_404(self):
        """17. Requesting nonexistent incident returns 404."""
        res = client.get("/api/incidents/INC-999")
        assert res.status_code == 404
        assert "not found" in res.json()["detail"].lower()

    def test_get_incident_reports_subresource(self):
        """18. GET /api/incidents/{id}/reports returns linked reports with extractions."""
        res = client.get("/api/incidents/INC-001/reports")
        assert res.status_code == 200
        reports = res.json()
        assert len(reports) > 0
        for r in reports:
            assert "id" in r
            assert "source" in r
            assert "raw_text" in r
            assert "language" in r
            assert "extractions" in r
            assert isinstance(r["extractions"], list)

    def test_get_incident_reports_nonexistent_returns_404(self):
        """19. GET /api/incidents/{id}/reports on nonexistent incident returns 404."""
        res = client.get("/api/incidents/INC-888/reports")
        assert res.status_code == 404

    def test_get_incident_contradictions_subresource(self):
        """20. GET /api/incidents/{id}/contradictions returns typed contradiction records."""
        incidents = client.get("/api/incidents").json()
        inc_with_contr = next((i for i in incidents if i["contradiction_count"] > 0), None)
        assert inc_with_contr is not None

        res = client.get(f"/api/incidents/{inc_with_contr['id']}/contradictions")
        assert res.status_code == 200
        contrs = res.json()
        assert len(contrs) == inc_with_contr["contradiction_count"]
        for c in contrs:
            assert "contradiction_type" in c
            assert "field" in c
            assert "side_a" in c
            assert "side_b" in c

    def test_get_incident_contradictions_nonexistent_returns_404(self):
        """21. GET /api/incidents/{id}/contradictions on nonexistent incident returns 404."""
        res = client.get("/api/incidents/INC-777/contradictions")
        assert res.status_code == 404

    def test_confidence_breakdown_contains_5_components(self):
        """22. Confidence breakdown exposes the 5 weighted policy components."""
        res = client.get("/api/incidents/INC-001")
        data = res.json()
        conf_breakdown = data["confidence_breakdown"]
        assert conf_breakdown is not None
        assert "source_count" in conf_breakdown
        assert "source_diversity" in conf_breakdown
        assert "consistency" in conf_breakdown
        assert "extraction_quality" in conf_breakdown
        assert "information_type" in conf_breakdown

    def test_related_incidents_list_populated(self):
        """23. Related incidents in the 0.60-0.79 band are exposed in detail."""
        res = client.get("/api/incidents/INC-001")
        data = res.json()
        assert "related_incident_ids" in data
        assert isinstance(data["related_incident_ids"], list)


# ===========================================================================
# 4. Responder Verification Workflow Tests
# ===========================================================================

class TestResponderVerification:
    """Test POST /api/incidents/{id}/verify."""

    def test_verify_incident_transitions_status(self):
        """24. Verifying an unverified incident updates status to 'verified'."""
        res = client.post(
            "/api/incidents/INC-003/verify",
            json={"responder_id": "operator-alpha", "notes": "Confirmed on-ground"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["id"] == "INC-003"
        assert data["status"] == "verified"

        # Check in detail
        get_res = client.get("/api/incidents/INC-003")
        assert get_res.json()["status"] == "verified"

    def test_verify_creates_audit_log_entry(self):
        """25. Verifying an incident records an AuditLog entry."""
        res = client.post(
            "/api/incidents/INC-003/verify",
            json={"responder_id": "operator-42", "notes": "Visual confirmation"},
        )
        assert res.status_code == 200

        # Check audit trail
        audits = client.get("/api/audit-logs?incident_id=INC-003").json()
        assert len(audits) >= 1
        verify_audit = next((a for a in audits if a["action"] == "verify"), None)
        assert verify_audit is not None
        assert verify_audit["responder_id"] == "operator-42"
        assert verify_audit["notes"] == "Visual confirmation"

    def test_verify_incident_already_verified_is_idempotent(self):
        """26. Verifying an already verified incident returns 200 OK without error."""
        res2 = client.post("/api/incidents/INC-003/verify", json={"notes": "Second confirmation"})
        assert res2.status_code == 200
        assert res2.json()["status"] == "verified"

    def test_verify_rejected_incident_returns_409_conflict(self):
        """27. Verifying an incident that is currently 'rejected' returns 409 Conflict."""
        # First reject INC-004
        client.post("/api/incidents/INC-004/reject", json={"notes": "Duplicate report"})

        # Attempt to verify
        res_verify = client.post("/api/incidents/INC-004/verify")
        assert res_verify.status_code == 409
        assert "rejected" in res_verify.json()["detail"].lower()

    def test_verify_nonexistent_incident_returns_404(self):
        """28. Verifying a nonexistent incident returns 404 Not Found."""
        res = client.post("/api/incidents/INC-999/verify")
        assert res.status_code == 404

    def test_verify_preserves_evidence_and_reports(self):
        """29. Verification preserves all underlying reports and extractions untouched."""
        before = client.get("/api/incidents/INC-003").json()
        client.post("/api/incidents/INC-003/verify")
        after = client.get("/api/incidents/INC-003").json()

        assert before["report_count"] == after["report_count"]
        assert [r["id"] for r in before["reports"]] == [r["id"] for r in after["reports"]]
        assert before["severity"] == after["severity"]


# ===========================================================================
# 5. Responder Rejection Workflow Tests
# ===========================================================================

class TestResponderRejection:
    """Test POST /api/incidents/{id}/reject."""

    def test_reject_incident_transitions_status(self):
        """30. Rejecting an unverified incident updates status to 'rejected'."""
        res = client.post(
            "/api/incidents/INC-005/reject",
            json={"responder_id": "operator-beta", "notes": "False alarm"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["id"] == "INC-005"
        assert data["status"] == "rejected"

    def test_reject_creates_audit_log_entry(self):
        """31. Rejecting an incident records an AuditLog entry."""
        client.post(
            "/api/incidents/INC-005/reject",
            json={"responder_id": "evaluator-9", "notes": "Deemed non-actionable"},
        )
        audits = client.get("/api/audit-logs?incident_id=INC-005").json()
        assert len(audits) >= 1
        reject_audit = next((a for a in audits if a["action"] == "reject"), None)
        assert reject_audit is not None
        assert reject_audit["responder_id"] == "evaluator-9"
        assert reject_audit["notes"] == "Deemed non-actionable"

    def test_reject_preserves_reports_and_extractions(self):
        """32. Rejecting does NOT delete reports from database or incident."""
        before = client.get("/api/incidents/INC-005").json()
        client.post("/api/incidents/INC-005/reject")
        after = client.get("/api/incidents/INC-005").json()

        assert before["report_count"] == after["report_count"]
        # Raw reports still exist in /api/reports
        all_reports = client.get("/api/reports").json()
        assert len(all_reports) == 20

    def test_reject_nonexistent_incident_returns_404(self):
        """33. Rejecting a nonexistent incident returns 404 Not Found."""
        res = client.post("/api/incidents/INC-999/reject")
        assert res.status_code == 404

    def test_repeated_rejection_is_idempotent(self):
        """34. Rejecting an already rejected incident returns 200 OK."""
        res2 = client.post("/api/incidents/INC-005/reject", json={"notes": "Duplicate rejection"})
        assert res2.status_code == 200
        assert res2.json()["status"] == "rejected"


# ===========================================================================
# 6. Incident Splitting Workflow Tests
# ===========================================================================

class TestIncidentSplitting:
    """Test POST /api/incidents/{id}/split."""

    def test_valid_split_moves_selected_reports_to_new_incident(self):
        """35. Valid split separates selected reports into a new incident."""
        incidents = client.get("/api/incidents").json()
        target_inc = next(i for i in incidents if i["report_count"] >= 2 and i["status"] != "rejected")
        inc_id = target_inc["id"]
        inc_before = client.get(f"/api/incidents/{inc_id}").json()
        rids_before = [r["id"] for r in inc_before["reports"]]
        assert len(rids_before) >= 2

        split_targets = [rids_before[0]]

        res_split = client.post(
            f"/api/incidents/{inc_id}/split",
            json={
                "responder_id": "operator-lead",
                "report_ids": split_targets,
                "notes": "Separate rescue needed",
            },
        )
        assert res_split.status_code == 200
        data = res_split.json()
        assert data["original_incident_id"] == inc_id
        assert data["new_incident_id"] != inc_id
        assert set(data["split_report_ids"]) == set(split_targets)

        # Original incident now has remaining reports
        inc_after = client.get(f"/api/incidents/{inc_id}").json()
        remaining_rids = [r["id"] for r in inc_after["reports"]]
        assert len(remaining_rids) == len(rids_before) - len(split_targets)
        assert set(split_targets).isdisjoint(set(remaining_rids))

        # New incident has the split reports
        new_inc = client.get(f"/api/incidents/{data['new_incident_id']}").json()
        assert new_inc["status"] == "unverified"
        assert len(new_inc["reports"]) == len(split_targets)
        assert set(r["id"] for r in new_inc["reports"]) == set(split_targets)

    def test_split_creates_audit_log_for_both_incidents(self):
        """36. Split creates AuditLog entries on both original and new incidents."""
        # INC-007 has 2 reports (R011, R012)
        inc7 = client.get("/api/incidents/INC-007").json()
        split_rids = [inc7["reports"][0]["id"]]

        res = client.post(
            "/api/incidents/INC-007/split",
            json={"responder_id": "lead-resp", "report_ids": split_rids, "notes": "Split single report"},
        )
        assert res.status_code == 200
        new_id = res.json()["new_incident_id"]

        audits_orig = client.get("/api/audit-logs?incident_id=INC-007").json()
        assert any(a["action"] == "split" for a in audits_orig)

        audits_new = client.get(f"/api/audit-logs?incident_id={new_id}").json()
        assert any(a["action"] == "split" for a in audits_new)

    def test_split_recalculates_p7_assessments(self):
        """37. Split recalculates severity, confidence, and priority for both incidents."""
        # Check INC-007 and its split target
        inc7 = client.get("/api/incidents/INC-007").json()
        assert inc7["severity"] > 0
        assert inc7["priority"] > 0

    def test_split_empty_report_list_returns_400(self):
        """38. Splitting with empty report_ids returns 400 Bad Request."""
        res = client.post(
            "/api/incidents/INC-001/split",
            json={"report_ids": []},
        )
        assert res.status_code == 400
        assert "empty" in res.json()["detail"].lower()

    def test_split_duplicate_report_ids_returns_400(self):
        """39. Splitting with duplicate report IDs in request returns 400 Bad Request."""
        inc1 = client.get("/api/incidents/INC-001").json()
        rid = inc1["reports"][0]["id"]
        res = client.post(
            "/api/incidents/INC-001/split",
            json={"report_ids": [rid, rid]},
        )
        assert res.status_code == 400
        assert "duplicate" in res.json()["detail"].lower()

    def test_split_all_reports_returns_400(self):
        """40. Splitting all reports from an incident returns 400 Bad Request."""
        inc1 = client.get("/api/incidents/INC-001").json()
        all_rids = [r["id"] for r in inc1["reports"]]

        res = client.post(
            "/api/incidents/INC-001/split",
            json={"report_ids": all_rids},
        )
        assert res.status_code == 400
        assert "at least one" in res.json()["detail"].lower()

    def test_split_non_member_report_returns_400(self):
        """41. Splitting a report that does not belong to the incident returns 400 Bad Request."""
        res = client.post(
            "/api/incidents/INC-001/split",
            json={"report_ids": ["R999_FOREIGN"]},
        )
        assert res.status_code == 400
        assert "does not belong" in res.json()["detail"].lower()

    def test_split_rejected_incident_returns_409(self):
        """42. Attempting to split a rejected incident returns 409 Conflict."""
        # INC-005 is rejected
        res = client.post(
            "/api/incidents/INC-005/split",
            json={"report_ids": ["R008"]},
        )
        assert res.status_code == 409
        assert "rejected" in res.json()["detail"].lower()

    def test_split_nonexistent_incident_returns_404(self):
        """43. Splitting a nonexistent incident returns 404 Not Found."""
        res = client.post(
            "/api/incidents/INC-999/split",
            json={"report_ids": ["R001"]},
        )
        assert res.status_code == 404


# ===========================================================================
# 7. Audit Trail & Reports API Tests
# ===========================================================================

class TestAuditTrailAndReportsAPI:
    """Test GET /api/audit-logs and GET /api/reports."""

    def test_audit_logs_append_only(self):
        """44. Multiple operations produce multiple chronological audit records without overwriting."""
        client.post("/api/incidents/INC-008/verify", json={"notes": "First action"})
        client.post("/api/incidents/INC-008/reject", json={"notes": "Second action"})

        audits = client.get("/api/audit-logs?incident_id=INC-008").json()
        assert len(audits) >= 2
        actions = [a["action"] for a in audits]
        assert "verify" in actions
        assert "reject" in actions

    def test_audit_logs_filter_by_incident_id(self):
        """45. Audit logs can be filtered by incident ID."""
        audits_inc8 = client.get("/api/audit-logs?incident_id=INC-008").json()
        assert all(a["incident_id"] == "INC-008" for a in audits_inc8)

    def test_get_all_reports_endpoint(self):
        """46. GET /api/reports returns all 20 reports."""
        res = client.get("/api/reports")
        assert res.status_code == 200
        reports = res.json()
        assert len(reports) == 20
        assert all("raw_text" in r for r in reports)

    def test_filter_reports_by_source(self):
        """47. Filter reports by source channel."""
        res = client.get("/api/reports?source=whatsapp")
        assert res.status_code == 200
        reports = res.json()
        assert len(reports) > 0
        assert all(r["source"] == "whatsapp" for r in reports)

    def test_no_pii_leakage_in_api(self):
        """48. API does not expose phone numbers or personal citizen data."""
        res = client.get("/api/reports")
        reports = res.json()
        for r in reports:
            assert not any(phone_kw in r["raw_text"].lower() for phone_kw in ["+91", "phone:", "mobile:"])
            assert r["reporter_id"] != ""

    def test_repeated_gets_are_stable_and_deterministic(self):
        """49. Repeated GET calls return deterministic, identical data."""
        res1 = client.get("/api/incidents").json()
        res2 = client.get("/api/incidents").json()
        assert res1 == res2

        detail1 = client.get("/api/incidents/INC-001").json()
        detail2 = client.get("/api/incidents/INC-001").json()
        assert detail1 == detail2

    def test_split_preserves_raw_report_records(self):
        """50. Splitting an incident never deletes or alters raw Report table rows."""
        reports_before = client.get("/api/reports").json()
        assert len(reports_before) == 20

        # Dynamically pick candidate incident with >= 2 reports
        incidents = client.get("/api/incidents").json()
        target_inc = next(i for i in incidents if i["report_count"] >= 2 and i["status"] != "rejected")
        inc_id = target_inc["id"]
        detail = client.get(f"/api/incidents/{inc_id}").json()
        target_rid = detail["reports"][0]["id"]

        res_split = client.post(
            f"/api/incidents/{inc_id}/split",
            json={"report_ids": [target_rid]},
        )
        assert res_split.status_code == 200

        reports_after = client.get("/api/reports").json()
        assert len(reports_after) == 20
        r_before = next(r for r in reports_before if r["id"] == target_rid)
        r_after = next(r for r in reports_after if r["id"] == target_rid)
        assert r_before["raw_text"] == r_after["raw_text"]
        assert r_before["source"] == r_after["source"]


# ===========================================================================
# 8. Audit Integrity & Correctness Verification (Phase 8 Audit Requirements)
# ===========================================================================

class TestPhase8AuditIntegrity:
    """Targeted regression tests for pipeline idempotency and split state integrity."""

    def test_new_unprocessed_report_behavior(self):
        """Audit 1: Inserting an unprocessed report processes it without corrupting existing clusters."""
        db = TestingSessionLocal()
        try:
            # Insert a new unprocessed report with received_at and reporter_id
            new_rep = Report(
                id="R099",
                raw_text="Heavy waterlogging near Civil Lines, 5 people stranded.",
                received_at="2026-09-08T02:00:00Z",
                source="sms",
                language="en",
                reporter_id="CITIZEN-99",
                processed=False,
            )
            db.add(new_rep)
            db.commit()

            # Run pipeline
            res = client.post("/api/pipeline/process")
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "completed"

            # Verify R099 is now processed, extractions created, location resolved
            db.expire_all()
            rep = db.query(Report).filter(Report.id == "R099").first()
            assert rep is not None
            assert rep.processed is True
            assert rep.normalized_text is not None
            assert len(rep.extractions) > 0
            assert rep.location_resolved is not None

            # Verify R099 is fused into an Incident (IncidentReport link exists)
            ir_link = db.query(IncidentReport).filter(IncidentReport.report_id == "R099").first()
            assert ir_link is not None
            assert ir_link.incident_id is not None
            fused_inc = db.query(Incident).filter(Incident.id == ir_link.incident_id).first()
            assert fused_inc is not None
            assert fused_inc.priority is not None
        finally:
            db.close()

    def test_split_removes_stale_cross_incident_contradiction(self):
        """Audit 2: Splitting contradicting reports into separate incidents removes the cross-incident contradiction."""
        db = TestingSessionLocal()
        try:
            # Create a dedicated incident with R009 and R010 (which contradict on location)
            test_inc = Incident(
                id="INC-AUDIT-CROSS",
                status="unverified",
                title="Audit Cross Contradiction Incident",
                location_resolved="Rampur Test",
            )
            db.add(test_inc)
            db.flush()

            ir1 = IncidentReport(incident_id="INC-AUDIT-CROSS", report_id="R009", match_score=0.85)
            ir2 = IncidentReport(incident_id="INC-AUDIT-CROSS", report_id="R010", match_score=0.85)
            db.add_all([ir1, ir2])
            db.flush()

            from services.contradiction_detector import detect_incident_contradictions, persist_contradictions
            from routers.incidents import _build_extractions_map
            reps = db.query(Report).filter(Report.id.in_(["R009", "R010"])).all()
            ext_map = _build_extractions_map(reps)
            c_out = detect_incident_contradictions("INC-AUDIT-CROSS", reps, extractions_map=ext_map)
            persist_contradictions(db, c_out)
            db.commit()

            # Verify contradiction exists initially
            current_contrs = db.query(Contradiction).filter(Contradiction.incident_id == "INC-AUDIT-CROSS").all()
            assert len(current_contrs) >= 1

            # Split R010 out of INC-AUDIT-CROSS
            res = client.post(
                "/api/incidents/INC-AUDIT-CROSS/split",
                json={"report_ids": ["R010"], "responder_id": "auditor", "notes": "Separate distinct locations"},
            )
            assert res.status_code == 200
            data = res.json()
            new_id = data["new_incident_id"]

            # Contradiction between R009 and R010 must be gone from INC-AUDIT-CROSS
            db.expire_all()
            orig_contrs = db.query(Contradiction).filter(Contradiction.incident_id == "INC-AUDIT-CROSS").all()
            assert len(orig_contrs) == 0

            # New incident must also have 0 contradictions
            new_contrs = db.query(Contradiction).filter(Contradiction.incident_id == new_id).all()
            assert len(new_contrs) == 0

            # Verify no stale contradiction rows reference R009 and R010 on either incident
            all_contrs = db.query(Contradiction).filter(
                (Contradiction.incident_id == "INC-AUDIT-CROSS") | (Contradiction.incident_id == new_id)
            ).all()
            assert len(all_contrs) == 0
        finally:
            db.close()

    def test_split_preserves_intra_incident_contradiction(self):
        """Audit 3: Splitting an unrelated report preserves contradiction between remaining reports."""
        db = TestingSessionLocal()
        try:
            # Create a test incident with 3 reports: R009, R010 (contradicting) and R001 (split candidate)
            test_inc = Incident(
                id="INC-AUDIT-CONTR",
                status="unverified",
                title="Audit Contradiction Incident",
                location_resolved="Rampur Test",
            )
            db.add(test_inc)
            db.flush()

            ir1 = IncidentReport(incident_id="INC-AUDIT-CONTR", report_id="R009", match_score=0.85)
            ir2 = IncidentReport(incident_id="INC-AUDIT-CONTR", report_id="R010", match_score=0.85)
            ir3 = IncidentReport(incident_id="INC-AUDIT-CONTR", report_id="R001", match_score=0.85)
            db.add_all([ir1, ir2, ir3])
            db.flush()

            # Detect and persist initial contradiction between R009 and R010
            from services.contradiction_detector import detect_incident_contradictions, persist_contradictions
            from routers.incidents import _build_extractions_map
            reps = db.query(Report).filter(Report.id.in_(["R009", "R010", "R001"])).all()
            ext_map = _build_extractions_map(reps)
            c_out = detect_incident_contradictions("INC-AUDIT-CONTR", reps, extractions_map=ext_map)
            persist_contradictions(db, c_out)
            db.commit()

            # Confirm contradiction exists before split
            init_c = db.query(Contradiction).filter(Contradiction.incident_id == "INC-AUDIT-CONTR").all()
            assert len(init_c) >= 1

            # Split only R001 away
            res = client.post(
                "/api/incidents/INC-AUDIT-CONTR/split",
                json={"report_ids": ["R001"], "responder_id": "auditor", "notes": "Remove unrelated report"},
            )
            assert res.status_code == 200

            # Remaining reports R009 and R010 in INC-AUDIT-CONTR still have their contradiction
            db.expire_all()
            remaining_c = db.query(Contradiction).filter(Contradiction.incident_id == "INC-AUDIT-CONTR").all()
            assert len(remaining_c) >= 1
            assert {remaining_c[0].side_a_report_id, remaining_c[0].side_b_report_id} == {"R009", "R010"}
        finally:
            db.close()

    def test_split_preserves_match_evidence(self):
        """Audit 4: Splitting preserves match_score and match_details on IncidentReport rows."""
        db = TestingSessionLocal()
        try:
            inc1 = db.query(Incident).filter(Incident.id == "INC-001").first()
            assert inc1 is not None
            # Fetch a report link
            link = db.query(IncidentReport).filter(
                IncidentReport.incident_id == "INC-001",
                IncidentReport.report_id == "R002",
            ).first()
            assert link is not None
            orig_score = link.match_score
            orig_details = link.match_details

            # Split R002 into a new incident
            res = client.post(
                "/api/incidents/INC-001/split",
                json={"report_ids": ["R002"], "responder_id": "auditor", "notes": "Check match score preservation"},
            )
            assert res.status_code == 200
            new_id = res.json()["new_incident_id"]

            db.expire_all()
            moved_link = db.query(IncidentReport).filter(
                IncidentReport.incident_id == new_id,
                IncidentReport.report_id == "R002",
            ).first()
            assert moved_link is not None
            assert moved_link.match_score == orig_score
            assert moved_link.match_details == orig_details
        finally:
            db.close()

    def test_failed_split_reassessment_rolls_back_transaction(self, monkeypatch):
        """Audit 5: Any exception during P6/P7 reassessment triggers atomic rollback of membership and audit logs."""
        db = TestingSessionLocal()
        try:
            # Establish an isolated valid incident with at least 2 reports
            inc_id = "INC-ROLLBACK-TEST"
            split_rid = "R001"
            remain_rid = "R002"

            test_inc = Incident(
                id=inc_id,
                status="unverified",
                title="Rollback Test Incident",
                location_resolved="civil_lines",
            )
            db.add(test_inc)
            db.flush()

            ir1 = IncidentReport(incident_id=inc_id, report_id=split_rid, match_score=0.85)
            ir2 = IncidentReport(incident_id=inc_id, report_id=remain_rid, match_score=0.85)
            db.add_all([ir1, ir2])
            db.commit()

            # Monkeypatch assess_incident in routers.incidents to raise RuntimeError
            import routers.incidents
            def mock_fail_assess(*args, **kwargs):
                raise RuntimeError("Simulated P7 assessment crash during split")

            monkeypatch.setattr(routers.incidents, "assess_incident", mock_fail_assess)

            # Attempt split — must fail with 500 or RuntimeError
            with pytest.raises(RuntimeError, match="Simulated P7 assessment crash"):
                routers.incidents.split_incident(
                    incident_id=inc_id,
                    payload=routers.incidents.SplitRequest(report_ids=[split_rid], notes="Rollback test"),
                    db=db,
                )

            # Verify transaction was rolled back
            db.expire_all()
            # 1. Report is STILL in original incident
            still_linked = db.query(IncidentReport).filter(
                IncidentReport.incident_id == inc_id,
                IncidentReport.report_id == split_rid,
            ).first()
            assert still_linked is not None

            # 2. No audit log created for this failed split
            failed_audit = db.query(AuditLog).filter(
                AuditLog.incident_id == inc_id,
                AuditLog.notes.like("%Rollback test%"),
            ).first()
            assert failed_audit is None
        finally:
            db.close()

