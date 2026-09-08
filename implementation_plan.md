# Implementation Plan: Phase 9 — Responder Console UI/UX Overhaul

## Problem Statement
The SETU emergency information fusion engine logic (P1–P8) is verified and frozen. However, the current frontend layout suffers from information density issues: too much content is compressed into one small viewport, font sizes are small (11–13px), the offline map is squeezed inside the queue sidebar, and incident assessment and evidence lack visual hierarchy when projected onto large displays (1366×768 or 1920×1080) for SIH judges.

## Goals & Constraints
- **Preserve Intelligence Invariants**: Zero changes to Python files, scoring formulas, clustering, contradiction detection, or API schemas.
- **Frontend Only**: React 19 + Vanilla CSS. No external network dependencies (must remain 100% offline capable).
- **Presentation Readiness**: Optimized for 1366×768 and 1920×1080 desktop displays and projector visibility.
- **Accurate Scope**: 20 demo reports → 13 candidate incidents → 1 contradiction across 4 geographic response zones plus GPS outskirts.

---

## Proposed UI Architecture

### 1. Unified Command Header & Metrics Bar (Reduced Vertical Height)
- **Top Command Bar (52px)**:
  - SETU brand identity + operational tagline.
  - Active disaster scenario: `Rampur Urban Flood · 20 Reports Fused`.
  - View layout mode toggles: `[Split 3-Pane]` / `[Incident Focus]` / `[Map Overview]`.
  - Primary pipeline trigger: `⚡ Process Intelligence Pipeline`.
- **Operational KPI Ribbon (42px)**:
  - Pipeline Funnel: `20 Raw Reports → 13 Candidate Incidents → 4 Zones`.
  - Live metric gauges: Critical (1), High (3), Medium (8), Low (1), Conflicts (1), Pending Verification (13).

### 2. Left Column: Incident Queue (350px)
- Dedicated full vertical height for queue triage.
- Quick filter chips: Urgency (`All`, `Critical`, `High`, `Medium`, `Low`) and Status (`All`, `Unverified`, `Verified`, `Rejected`).
- Search input with clear button.
- Redesigned incident cards:
  - Prominent Incident ID (`INC-006`), status badge, and urgency band badge.
  - Large title (15px) and location name.
  - High-visibility priority badge (`85`), severity (`100`), confidence (`75%`).
  - Report count tag and contradiction warning tag (`⚠ 1 Conflict`).
  - Active card highlighted with glowing accent border.

### 3. Center Column: Incident Intelligence Workspace (Flex: 1)
- **Incident Hero Banner**:
  - Large incident ID and title (`INC-006: Rescue Needed — kotwali`).
  - Location badge (Gazetteer / GPS Verified with coordinates).
  - Source diversity and report count summary.
  - **Deliberate Action Control Deck**:
    - `✓ Verify Incident` (primary green action with confirmation).
    - `✕ Reject Incident` (deliberate red outline action).
    - `⚲ Split Incident` (amber/blue action, dynamically reflects selected reports).
- **Assessment Showcase (Prominent 3-Card Grid)**:
  - **Severity Card**: Large score (`100/100`), colored meter, baseline type, modifiers breakdown.
  - **Confidence Card**: Large percentage (`75.0%`), multi-factor corroboration breakdown (Source Count, Diversity, Consistency, Extraction Quality, Information Type).
  - **Priority Card**: Large priority score (`85/100`), urgency badge, exact formula breakdown, and low-confidence safety cap alert if triggered.
- **Active Contradiction Alert Panel**:
  - High-visibility amber panel showing physical contradiction details.
  - Clean side-by-side comparison: Side A vs Side B (field, value, exact quote).
  - Resolution status indicator (Unresolved vs Human-Resolved).
- **Contributing Source Reports & Evidence**:
  - Generous card layout with clear typography (15px quotes).
  - Channel pills (WhatsApp, SMS, Field Worker, Web) with distinct colors.
  - Formatted extraction tags (Impact Estimates, Infrastructure, Reported Location, etc.).
  - Checkboxes for report splitting with clear visual selection highlight.
- **Audit Trail Timeline**:
  - Clean chronological log with timestamps, operator IDs, action badges, and operator notes.

### 4. Right Column: Dedicated Tactical GIS Map (400px)
- Offline SVG GIS Map of Rampur:
  - 4 named response zones (Civil Lines, Kotwali, Bilaspur Chowk, Naya Mohalla) + river and highways.
  - Incident markers colored by urgency (critical pulsing red, high orange, medium yellow, low blue).
  - Two-way interactive synchronization:
    - Clicking a map marker selects the incident in the queue and workspace.
    - Selecting an incident in the queue highlights the marker on the map.
  - Zone quick filter buttons.
  - Collapsible toggle to give 100% width to the incident workspace when needed.

### 5. Action Confirmation Modals & System States
- Deliberate confirmation dialogs for Verify, Reject, and Split actions with operator notes and responder ID input.
- High-visibility notification banners for errors, loading, and pipeline completion.

---

## Proposed File Changes

### [MODIFY] [frontend/src/index.css](file:///h:/SETU/frontend/src/index.css)
- Update design tokens: typography scale (from 11–13px to 13–18px), EOC color scheme (high-contrast surfaces `#0a0e17`, `#121826`, `#182234`, borders `#223048`), spacing, and custom scrollbars.

### [MODIFY] [frontend/src/App.css](file:///h:/SETU/frontend/src/App.css)
- Implement modern 3-pane / responsive EOC grid layout.
- Style the consolidated command header, metric ribbon, incident cards, assessment showcase cards, contradiction panels, report evidence cards, and confirmation modals.

### [MODIFY] [frontend/src/App.jsx](file:///h:/SETU/frontend/src/App.jsx)
- Structure the application into the 3-pane layout (Queue, Intelligence Workspace, Tactical Map).
- Add view layout toggles (`[Split View]`, `[Incident Focus]`, `[Map Overview]`).
- Enhance confirmation modals with responder notes and clear action descriptions.

### [MODIFY] [frontend/src/components/RampurMap.jsx](file:///h:/SETU/frontend/src/components/RampurMap.jsx)
- Enhance SVG map presentation, zone labels, marker interactivity, and responsive sizing.

---

## Verification Plan

### Automated Checks
1. `npm run lint` in `frontend/` (oxlint: 0 errors, 0 warnings).
2. `npm run build` in `frontend/` (Vite build: clean production bundle).
3. Backend verification: Ensure no Python files were touched (`backend/venv/Scripts/python.exe -m compileall -q -x ".*venv.*" backend`).
4. Full backend regression suite: `backend/venv/Scripts/pytest.exe test_phase1.py tests/ -q` (all 453 tests must continue to pass).

### Visual & Interactive Verification
1. Verify live local app at `http://localhost:5173/` using browser subagent or URL content check.
2. Verify incident selection, filtering by urgency, filtering by zone, and search.
3. Test Verify, Reject, and Split actions via the UI modals against the live backend.
