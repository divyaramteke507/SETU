# Implementation Plan: Red-Team P0 Safety & Correctness Hardening

Target HEAD: `e17de5224a72313498b3e17863d5c8ad5e455b40`  
Objective: Implement ONLY the five P0 safety and correctness fixes identified in the fresh red-team review. No architectural redesign, no embedding model replacement, no LLMs, and no unnecessary UI changes.

---

## 1. Verified Baseline State

- **Active Test Suite Baseline**: Exactly **420 tests collected and passing** in `backend/tests/` prior to P0 implementation. Post-implementation total: **439 tests collected, 439 passed, 0 skipped** across all 10 test modules (+19 tests in `test_p0_hardening.py`).
- **Model Environment**: Fully bundled, local sentence-transformers model `paraphrase-multilingual-MiniLM-L12-v2` operating 100% offline.
- **Authoritative Demo Numbers**: Will NOT be assumed from documentation or unit-test mocks. The true count of candidate incidents, surfaced contradictions, and priority distribution will be empirically measured by running the complete pipeline with the bundled model on the 20 raw reports.

---

## 2. Detailed Technical Plan for P0 Fixes

### P0-1 — Duplicate / Forwarded Report Confidence Inflation
- **Problem**: 3 identical or word-for-word forwarded reports appearing across 3 channels currently get counted as 3 independent reports (`source_count=0.85`) and 3 independent sources (`source_diversity=1.00`), artificially pushing priority into Critical (>85).
- **Implementation in `backend/services/incident_assessor.py`**:
  - Implement `_cluster_corroboration_units(reports, cosine_threshold=0.95)`:
    1. For each report, retrieve normalized text and its embedding vector (from `report.embedding` or generated via `generate_embedding`).
    2. If embeddings are unavailable or fail, fall back safely to distinct units (or exact string match) without inventing similarity.
    3. Compute pairwise cosine similarity using existing `cosine_similarity`.
    4. Group near-duplicate reports into a single **corroboration unit** using complete-linkage clustering ($\ge 0.95$ pairwise similarity across all members of a cluster to prevent runaway transitivity).
  - In `calculate_confidence(reports, ...)`:
    1. `effective_report_count = len(corroboration_units)`. Use this for the `source_count` policy lookup.
    2. `effective_sources = {unit.primary_source for unit in corroboration_units}`. Each corroboration unit represents one independent reporting channel. Use `len(effective_sources)` for the `source_diversity` policy lookup.
    3. Detail strings (`sc_detail`, `sd_detail`) explicitly state the corroboration units and grouped duplicates (e.g., `"1 corroboration unit(s) (from 3 report(s), 2 duplicate(s) grouped) -> discrete score 0.40"`).
  - **Invariants Preserved**:
    - Reports are NEVER deleted from the incident.
    - Reports are NEVER removed from the database.
    - UI and audit trail continue to show all reports, all sources, and all evidence.
    - Genuinely different reports remain separate corroboration units.

### P0-2 — Contradiction Over-Counting for Confidence Consistency
- **Problem**: Pairwise contradiction detection compares report pairs. If 3 reports claim "road blocked" and 1 claims "passable slowly", 3 pairwise contradiction records are generated, dropping the consistency score from 0.75 to 0.25 (over-penalizing the incident for report volume).
- **Implementation in `backend/services/incident_assessor.py`**:
  - Separate raw pairwise contradiction evidence from the distinct disagreement count used for scoring.
  - Implement `_count_distinct_disagreements(contradictions)`:
    1. For each contradiction, extract `c_type`, `c_field`, `val_a`, `val_b`.
    2. Normalize values to lowercase stripped strings.
    3. Build a canonical unordered pair `tuple(sorted([norm_val_a, norm_val_b]))`.
    4. Stable disagreement identity key: `(c_type, c_field, (norm_val_a, norm_val_b))`.
    5. Count the number of unique disagreement keys.
  - In `calculate_confidence`:
    1. Use the distinct disagreement count for the `consistency` penalty lookup.
    2. Preserves all raw pairwise contradiction records in `ConfidenceBreakdown` and DB for responder review.
    3. Detail string `cs_detail` explains both: `"{distinct} distinct disagreement(s) ({raw} pairwise record(s)) -> discrete score {cs_val:.2f}"`.
  - **Invariants Preserved**:
    - Pairwise contradiction records in UI/DB are completely untouched.
    - Responders can see exactly which report pairs disagree.
    - Contradiction types, severities, and safety rules remain unchanged.

### P0-3 — Reconcile Documentation with Actual Live Demo Contradiction Count
- **Problem**: Documentation claimed 1 contradiction, while sandbox/mock environments without embeddings reported different figures.
- **Implementation**:
  - Run the REAL demo pipeline in our bundled-model environment:
    `20 raw reports → pipeline → candidate incidents → contradiction detection`.
  - Empirically record:
    1. Total candidate incident count.
    2. Total surfaced contradiction count.
    3. Priority distribution across Critical, High, Medium, Low.
  - Update `README.md`, `README_JUDGE.md`, `README_JUDGE.txt`, `walkthrough.md`, and `implementation_plan.md` to reflect the exact empirical results with honest, transparent explanations.

### P0-4 — GPS vs Text Location Conflict Visibility
- **Problem**: When valid GPS is present, `resolve_location` immediately returns GPS coordinates without inspecting `location_raw`. If text says "Kotwali ke paas" and GPS points to Delhi, the disagreement is invisible.
- **Implementation**:
  - In `backend/services/location_resolver.py`:
    1. If GPS coordinates are valid, continue using GPS as primary resolved location (`resolution_method="gps"`).
    2. If `location_raw` is present, attempt gazetteer resolution (`exact_gazetteer_match`, then `fuzzy_gazetteer_match`).
    3. If text resolves to coordinates `(text_lat, text_lon)`:
       - Calculate Haversine distance using `calculate_geographic_distance`.
       - If distance $\ge 2000$ meters (`GEO_MAX_DISTANCE_M`):
         - `location_conflict = True`
         - `location_conflict_text = f"{text_name} ({text_lat:.4f}, {text_lon:.4f})"`
         - `location_conflict_distance_m = dist_m`
         - Explanation notes GPS coordinates conflict with text-implied location.
       - If distance $< 2000$m: `location_conflict = False`.
    4. If text cannot be resolved: `location_conflict = False` (do not flag conflict merely because text is unresolvable).
  - In `backend/schemas.py`: Add `location_conflict: bool = False`, `location_conflict_text: Optional[str] = None`, `location_conflict_distance_m: Optional[float] = None` to `LocationResolution`, `ReportDetailOut`, and `IncidentDetailOut`.
  - In `backend/models.py`: Add corresponding columns to `Report` and `Incident`.
  - In `backend/database.py`: Safe migration in `init_db()` to ensure SQLite compatibility.
  - In `backend/routers/incidents.py`: Pass conflict fields through in `_format_report_out` and `_format_incident_detail`.
  - In `frontend/src/App.jsx`: Render a minimal amber conflict warning in the Incident Intelligence Workspace hero when `incidentDetail.location_conflict` is True.

### P0-5 — Ordinary English Extraction Grammar Gaps
- **Problem**: Narrow regex patterns fail on standard English grammatical constructs like `"A family is stuck inside."` (lacks auxiliary `is`) and `"The road is blocked and vehicles cannot pass."` (lacks auxiliary `is` and `vehicles cannot pass` pattern).
- **Implementation in `backend/services/extractor.py` and `backend/services/contradiction_detector.py`**:
  - `INCIDENT_TYPE_PATTERNS["rescue_needed"]`:
    Update to `r"\b(?:people|persons?|family|families|someone|residents?)\s+(?:is|are|was|were|got)?\s*stuck\b"`.
  - `TRAPPED_PATTERNS`:
    Update to `r"\b(?:people|persons?|family|families|someone|residents?)\s+(?:is|are|was|were|got)?\s*stuck\b"`.
  - `INCIDENT_TYPE_PATTERNS["road_blocked"]`:
    Update to `r"\broads?\s+(?:is|are|was|were)?\s*(?:completely|totally|partially)?\s*blocked\b"`.
    Add `r"\bvehicles?\s+(?:cannot|can't)\s+pass\b"`.
    Update `r"\bvehicles?\s+(?:is|are|was|were|got)?\s*stuck\b"`.
  - Audit nearby patterns: Update `PEOPLE_PATTERNS` to handle `(?:is|are|was|were)?\s*affected`.
  - In `contradiction_detector.py`: Add `vehicles?\s+(?:cannot|can't)\s+pass` to `_ROAD_BLOCKED_RE`.
  - **Invariants Preserved**: Strict evidence grounding — every extracted field must match an exact text span. No uncontrolled fuzzy matching or hallucinations.

---

## 3. Exact Files Expected to Change

| File | Change Description |
|------|--------------------|
| `backend/services/incident_assessor.py` | P0-1: `_cluster_corroboration_units` & effective source count/diversity; P0-2: `_count_distinct_disagreements` for consistency penalty |
| `backend/services/location_resolver.py` | P0-4: GPS vs text gazetteer divergence comparison ($\ge 2000$m) and additive conflict flag |
| `backend/schemas.py` | P0-4: Additive `location_conflict*` fields on `LocationResolution`, `ReportDetailOut`, `IncidentDetailOut` |
| `backend/models.py` | P0-4: Additive `location_conflict*` columns on `Report` and `Incident` |
| `backend/database.py` | P0-4: Safe column migration in `init_db()` for SQLite |
| `backend/services/pipeline_runner.py` | P0-4: Store location conflict fields on reports and incidents |
| `backend/routers/incidents.py` | P0-4: Pass location conflict fields through API detail serializers |
| `backend/services/extractor.py` | P0-5: English grammar pattern fixes for `is stuck`, `is blocked`, `vehicles cannot pass` |
| `backend/services/contradiction_detector.py` | P0-5: Add `vehicles cannot pass` to road blocked contradiction pattern |
| `frontend/src/App.jsx` | P0-4: Minimal, non-intrusive amber conflict alert banner in Incident Intelligence Workspace |
| `backend/tests/test_p0_hardening.py` [NEW] | Comprehensive unit tests for P0-1 (A–G), P0-2, P0-4 (A–E), P0-5 |
| Documentation (`README*.md`, etc.) | P0-3: Exact alignment with live bundled-model demo execution |

---

## 4. Test Plan

1. **P0-1 Test Suite (`TestDuplicateCorroborationGuard`)**:
   - A. 1 report $\rightarrow$ baseline single-report confidence.
   - B. 3 identical/near-identical forwarded reports across 3 channels $\rightarrow$ effective corroboration count = 1 and effective source contribution = 1; duplicate forwarding must not receive independent corroboration credit.
   - C. 3 genuinely different reports across 3 channels $\rightarrow$ 3 corroboration units, 3 sources, full corroboration preserved.
   - D. 2 duplicates + 1 different report across 3 channels $\rightarrow$ 2 corroboration units, 2 sources.
   - E. Verify all duplicate reports remain visible in incident report list.
   - F. Verify evidence and audit trail intact.
   - G. Verify duplicate forwarding cannot create Critical solely from repeated copies.
2. **P0-2 Test Suite (`TestContradictionDeduplicationForConsistency`)**:
   - 3 blocked + 1 passable $\rightarrow$ exactly 1 distinct disagreement (`cs_val = 0.75`).
   - 1 blocked + 1 passable $\rightarrow$ exactly 1 distinct disagreement (`cs_val = 0.75`).
   - Blocked vs passable AND damaged vs intact $\rightarrow$ 2 distinct disagreements (`cs_val = 0.50`).
   - Verify pairwise evidence records remain intact in output.
   - Verify contradiction typing is unaffected.
3. **P0-4 Test Suite (`TestLocationConflictSignal`)**:
   - A. GPS and text agree ($<2000$m) $\rightarrow$ `location_conflict=False`.
   - B. GPS and text far apart ($\ge 2000$m) $\rightarrow$ `location_conflict=True`, GPS retained as primary, divergence explained.
   - C. GPS valid + text unresolvable $\rightarrow$ `location_conflict=False`.
   - D. Invalid GPS + valid text $\rightarrow$ text fallback behavior preserved.
   - E. All existing location tests pass.
4. **P0-5 Test Suite (`TestEnglishGrammarExtraction`)**:
   - `"A family is stuck inside."` $\rightarrow$ extracts `rescue_needed` and `trapped_or_rescue=True` with exact text evidence.
   - `"The road is blocked and vehicles cannot pass."` $\rightarrow$ extracts `road_blocked` with exact text evidence.
   - Variations: `"Two families are stuck"`, `"A person was stuck"`, `"Road was blocked"`, `"Vehicles can't pass"`.
5. **Full Regression Suite**:
   - Run all 439 tests (420 pre-existing + 19 new P0 tests across 10 modules) to ensure 100% pass rate.
   - Run Python `compileall` across backend.
   - Run frontend `oxlint` and `npm run build`.
   - Run live pipeline on 20 reports with bundled model and record empirical counts.

---

## 5. Risks & Mitigation

1. **Risk of runaway clustering transitivity (P0-1)**:
   - *Mitigation*: Use strict pairwise cosine threshold $\ge 0.95$ with complete linkage (every member of a corroboration unit must have similarity $\ge 0.95$ to all other members in the unit).
2. **Risk of altering UI contradiction evidence (P0-2)**:
   - *Mitigation*: Distinct disagreement grouping is applied ONLY to the consistency score calculation in `calculate_confidence`. The list of pairwise contradiction objects returned and displayed in the UI remains completely intact.
3. **Risk of breaking existing GPS precedence (P0-4)**:
   - *Mitigation*: GPS coordinates remain the authoritative resolved location (`resolution_method="gps"`). `location_conflict` is purely additive and non-blocking.
4. **Risk of extraction hallucination (P0-5)**:
   - *Mitigation*: Patterns remain strict regexes matching actual words. Every extraction must have a non-empty, verbatim text evidence span.

---

## 6. Frontend Change Assessment

**Are any frontend changes genuinely required?**
- **Yes, but strictly minimal and additive**: Under P0-4, the requirement states: *"The responder should be able to see: GPS location, Text-implied location, Conflict warning without having to manually discover it."*
- Therefore, exactly **one minimal change** in `frontend/src/App.jsx` is required: rendering a clean, non-intrusive amber conflict alert banner in the Incident Dossier Hero section when `incidentDetail.location_conflict` is true.
- **No other frontend changes will be made**: No layout restructuring, no styling overhauls, no font changes, no component rewrites.
