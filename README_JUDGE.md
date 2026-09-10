# SETU — SIH 2026 Judge & Evaluator Guide

**Problem Statement 26206:** Student Innovation — Disaster Management  
**Project:** SETU — Structured Emergency Bridge System  
**Tagline:** *Bridging Chaotic Emergency Reports to Structured Incident Intelligence*  
**Team:** DeLuQy · **Repository:** [github.com/divyaramteke507/SETU](https://github.com/divyaramteke507/SETU)

---

## Zero-Install Demonstration Package

SETU provides a self-contained, offline-first Windows evaluation build that runs without installing any runtime dependencies.

### Official Package Download
👉 **[Download SETU-SIH-DEMO.zip — Google Drive](https://drive.google.com/drive/folders/1rDSoWYNGWK5bJQcXQCb6ceBJOlAFftHO?usp=sharing)**

- **Archive File:** `SETU-SIH-DEMO.zip`
- **File Size:** Approximately **711 MB**
- **Contents:** Pre-built React production frontend, FastAPI backend, bundled portable Python 3.14 runtime, and the offline multilingual embedding model.

> [!IMPORTANT]
> **Source Repository vs. Demo Package:**  
> The GitHub repository contains the raw developer source code. If you download GitHub's auto-generated source ZIP, it will not include the pre-built frontend or bundled Python runtime. **Please use the Google Drive package above for evaluation.**

---

## Target System Requirements

- **Operating System:** Windows 10 or Windows 11 (64-bit)
- **Host Dependencies:** **None.**
  - ❌ No Python installation required
  - ❌ No Node.js or npm required
  - ❌ No Git required
  - ❌ No internet connection required after downloading the package (100% offline execution)

---

## Step-by-Step Evaluation Walkthrough

### 1. Launch the Engine
1. Download `SETU-SIH-DEMO.zip` from the [Google Drive folder](https://drive.google.com/drive/folders/1rDSoWYNGWK5bJQcXQCb6ceBJOlAFftHO?usp=sharing).
2. Extract the ZIP to any location on your Windows computer.
3. Double-click **`START_SETU.bat`** in the extracted directory.
4. The batch launcher verifies the bundled environment, starts the local fusion engine on port 8000, and automatically opens your default web browser to:
   ```
   http://127.0.0.1:8000
   ```
5. You will see the **SETU Responder Console** in high-contrast emergency operations center (EOC) dark mode.

---

### 2. Run the Intelligence Pipeline
1. In the top command header, click **`⚡ Process Intelligence Pipeline`**.
2. The engine executes deterministic multi-factor information fusion over the preloaded 20-report Rampur dataset.
3. Observe the operational funnel update:
   ```
   20 Raw Reports  ──▶  13 Candidate Incidents  ──▶  4 Named Zones + GPS/Outskirts
   ```
4. Check the live status metrics in the ribbon:
   - **Critical (85–100):** 1 incident (`INC-011`, Priority 91.89)
   - **High (70–84):** 3 incidents (`INC-004`, `INC-005`, `INC-006`)
   - **Medium (40–69):** 8 incidents (`INC-001`, `INC-002`, `INC-003`, `INC-007`, `INC-008`, `INC-010`, `INC-012`, `INC-013`)
   - **Low (0–39):** 1 incident (`INC-009`, Priority 37.57)
   - **Conflicts Flagged:** 1 physical contradiction (`INC-006`: location resolution conflict)

---

### 3. Inspect Candidate Incidents & Explainable Scoring
1. In the left **Incident Queue**, click **`INC-006`** (Rescue Needed — Kotwali, Priority 85).
2. Look at the **Assessment Showcase** in the center workspace:
   - **Severity (100/100):** Base type score + trapped bonus ($+15$) + vulnerable bonus ($+10$).
   - **Confidence (75%):** 5-factor corroboration breakdown (source count, diversity, consistency, extraction quality, info type).
   - **Priority (85/100):** Calculated transparently via:
     $$\text{Priority} = \text{round}(0.40 \times \text{Severity} + 0.60 \times (\text{Severity} \times \text{Confidence}))$$
3. Click the **`Diagnostics`** button to inspect the complete arithmetic proof.

---

### 4. Review the Physical Contradiction Alert
1. On **`INC-006`**, observe the prominent amber **Active Contradiction Alert Panel**.
2. The panel contrasts **Side A** (Report R009: text location near Kotwali Thana) against **Side B** (Report R010: field worker direct GPS coordinates).
3. Responders are explicitly alerted to the location contradiction so operational decisions are never made on conflicting geographic assumptions.

---

### 5. Tactical GIS Map Synchronization
1. Observe the right-hand column featuring the **Tactical GIS Map of Rampur**.
2. Markers are color-coded by urgency (pulsing red for critical, orange for high, amber for medium, slate for low).
3. Clicking a marker on the map selects and focuses the incident in the queue and workspace.
4. Use the view toggles in the top bar (`[Split View]`, `[Incident Focus]`, `[Map Overview]`) to adapt the layout.

---

### 6. Test Human-in-the-Loop Actions
SETU enforces that human responders have the final authority on all operational actions:

- **Verify Incident:** Click **`✓ Verify Incident`** on `INC-006`. Enter responder ID (e.g., `commander-1`) and confirm. The incident status updates to `verified`, and the action is recorded in the append-only audit trail.
- **Reject Incident:** Select an unverified incident (e.g., `INC-002`). Click **`✕ Reject Incident`**, provide a reason (e.g., duplicate/drill), and confirm. Status updates to `rejected`.
- **Split Incident:** Select `INC-004` (which combines two reports). Check one report checkbox, click **`⚲ Split Incident`**, and confirm. The checked report is dynamically detached into a new canonical candidate incident (`INC-014`).
- **Audit Trail:** Scroll to the bottom of the center workspace to view the immutable audit trail detailing operator IDs, timestamps, and notes.

---

### 7. Stop the Engine Cleanly
Return to the terminal window where `START_SETU.bat` is running and press **`[ENTER]`**. The launcher will cleanly terminate the backend process and release port 8000.

---

## Demonstration Disclaimer
The demonstration data is synthetic and designed for reproducible SIH 2026 evaluation. It does not represent a live emergency feed. SETU operates as a decision-support and information-fusion layer; it does not autonomously dispatch emergency resources.
