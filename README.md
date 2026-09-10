# SETU — Structured Emergency Bridge System

**Emergency Information Fusion Engine**  
*Bridging Chaotic Emergency Reports to Structured Incident Intelligence*

**SIH 2026** · Problem Statement **26206** · Student Innovation — Disaster Management  
**Team:** DeLuQy · **Repository:** [github.com/divyaramteke507/SETU](https://github.com/divyaramteke507/SETU)

---

> [!IMPORTANT]
> **Source Code vs. Zero-Install Demo Distribution**
> - **This GitHub Repository** contains the complete source code, test suite (448+ automated tests), FastAPI backend, React 19 frontend, and build utilities.
> - **For Judges & Evaluators:** Do NOT download the raw GitHub source ZIP expecting a plug-and-play build. GitHub source archives do not include the pre-compiled frontend bundle, offline embedding model, or portable Python runtime. Instead, use the official zero-install demonstration package linked below.

---

## Zero-Install Demo Package

SETU provides a pre-packaged, standalone Windows demonstration build specifically prepared for SIH 2026 judges and evaluators.

[![Download Demo Package](https://img.shields.io/badge/Google%20Drive-Download%20SETU--SIH--DEMO.zip-4285F4?style=for-the-badge&logo=googledrive&logoColor=white)](https://drive.google.com/drive/folders/1rDSoWYNGWK5bJQcXQCb6ceBJOlAFftHO?usp=sharing)

**Official Distribution Folder:**  
👉 **[Download SETU-SIH-DEMO.zip — Google Drive](https://drive.google.com/drive/folders/1rDSoWYNGWK5bJQcXQCb6ceBJOlAFftHO?usp=sharing)**

### Package Highlights
- **Package Name:** `SETU-SIH-DEMO.zip`
- **Package Size:** Approximately **711 MB**
- **Self-Contained:** Contains the entire FastAPI backend, pre-built production React console, bundled portable Python 3.14 runtime with all dependencies, and the offline multilingual embedding model (`paraphrase-multilingual-MiniLM-L12-v2`).
- **Zero Host Requirements:**
  - ❌ **No Python required** on the target Windows machine.
  - ❌ **No Node.js or npm required** on the target Windows machine.
  - ❌ **No Git required** on the target Windows machine.
  - ❌ **No internet access required** after the package has been downloaded (100% offline-first execution).
- **Target OS:** Windows 10 or Windows 11 (64-bit).

### Quick Demo Launch (Zero-Install)
1. **Download:** Obtain `SETU-SIH-DEMO.zip` from the [Google Drive folder](https://drive.google.com/drive/folders/1rDSoWYNGWK5bJQcXQCb6ceBJOlAFftHO?usp=sharing).
2. **Extract:** Extract the ZIP file to any folder on your Windows machine (e.g. Desktop or Downloads).
3. **Run:** Double-click `START_SETU.bat` inside the extracted folder.
4. **Interact:** The launcher will verify the environment, start the local SETU engine, and automatically open your default web browser to:
   ```
   http://127.0.0.1:8000
   ```
5. **Process Pipeline:** In the top command header, click **⚡ Process Intelligence Pipeline**. The engine deterministically fuses the preloaded Rampur emergency reports into structured candidate incidents.
6. **Stop:** Press `[ENTER]` in the launcher terminal window to cleanly stop the engine.

> [!NOTE]
> **Reproducibility Note:** The demo uses a preloaded, deterministic Rampur emergency dataset designed for reproducible SIH evaluation. Demonstration data is synthetic and intended for evaluation; it does not represent a live emergency feed.

---

## Problem Statement & Operational Context

During acute disaster events (such as urban flooding), Emergency Operations Centers (EOCs) and district control rooms are inundated with unstructured, multi-channel reports from citizens, field workers, SMS feeds, and messaging apps.

Responders face three severe cognitive bottlenecks:
1. **Information Fragmentation:** Multiple people report the same incident from different vantage points, with varying wording, languages, and levels of detail.
2. **Physical Contradictions:** Reports frequently present conflicting information (e.g., one report says "water is 2 ft and receding", while another from the same block claims "water is 6 ft and rising; 10 people trapped").
3. **Lack of Explainability:** Black-box prioritization algorithms obscure *why* a particular incident was deemed critical, preventing dispatchers from trusting or verifying automated suggestions.

---

## What SETU Is and Is Not

SETU is an **Emergency Information Fusion Engine** operating as a responder-side information-fusion layer.

| What SETU Is | What SETU Is NOT |
|---|---|
| **Responder-side fusion layer** for EOC staff and designated commanders | **Not a citizen emergency reporting app** |
| **Explainable incident synthesizer** preserving uncertainty & contradictions | **Not an autonomous dispatch system** (no automatic asset deployment) |
| **Deterministic decision-support system** with full evidence provenance | **Not a predictive flood or weather simulation model** |
| **Human-in-the-loop triage console** (Verify, Reject, Split) | **Not an automated resource allocator** |

> [!IMPORTANT]
> **Human Authority Invariant:** SETU does not autonomously dispatch emergency services. A human responder is always the final authority on all operational actions.

---

## Core Intelligence Pipeline & Evaluation Flow

SETU processes raw, chaotic emergency inputs through an 8-stage deterministic fusion pipeline:

```
20 Raw Reports
   │
   ▼
1. Report Ingestion & Text Normalization
   │
   ▼
2. Structured Entity Extraction (Damage, Infrastructure, Urgency, Needs)
   │
   ▼
3. Location Resolution (Gazetteer Alias Matching + GPS Binding)
   │
   ▼
4. Multi-Factor Correlation (Semantic + Geographic + Temporal + Incident Type)
   │
   ▼
5. Candidate Incident Clustering (Agglomerative Correlation Graph)
   │
   ▼
6. Physical Contradiction Detection (Numeric, Severity, Location Incompatibilities)
   │
   ▼
7. Tri-Partite Assessment (Explainable Severity, Corroborative Confidence, Priority)
   │
   ▼
8. Human Responder Verification (Verify, Reject, Split with Append-Only Audit Trail)
```

### Pipeline Details

1. **Text Normalization & Code-Mixing:** Cleans noise, standardizes whitespace, and normalizes romanized Hindi emergency keywords (e.g., *paani*, *bachao*, *phase*, *doob*, *madad*).
2. **Structured Extraction:** Extracts incident types, infrastructure tags, casualty/trapped counts, urgency markers, and sentiment indicators without destructive information loss.
3. **Location Resolution:** Matches colloquial place names against a local gazetteer (Rampur) using tiered exact and fuzzy matching, or binds direct GPS coordinates with geographic confidence ratings.
4. **Multi-Factor Correlation Scoring:** Computes pairwise similarity across four orthogonal dimensions:
   - **Semantic Similarity ($0.40$):** Cosine distance between multilingual MiniLM embeddings.
   - **Geographic Proximity ($0.30$):** Haversine distance with linear decay up to 2,000 meters.
   - **Temporal Proximity ($0.15$):** Full score within 5 minutes, decaying over 90 minutes.
   - **Incident Type Compatibility ($0.15$):** Exact type match ($1.0$) or domain-related category ($0.5$).
5. **Candidate Incident Clustering:** Merges reports with pairwise scores $\ge 0.80$ into candidate incidents, and flags related incidents ($0.60 \le \text{score} < 0.80$) for contextual awareness.
6. **Physical Contradiction Detection:** Evaluates merged reports for mutual incompatibilities across numbers (trapped count discrepancies), severity levels, locations, and mutually exclusive incident types (e.g., drought vs. flood). Contradictions are surfaced side-by-side (Side A vs. Side B) with exact text quotes.
7. **Tri-Partite Assessment:**
   - **Severity ($0–100$):** Type baseline + keyword intensity + trapped bonus ($+15$) + vulnerable bonus ($+10$).
   - **Confidence ($0–100\%$):** 5-factor corroboration: Source Count ($25\%$), Source Diversity ($20\%$), Consistency ($25\%$), Extraction Quality ($15\%$), Information Type ($15\%$).
   - **Priority ($0–100$):** Computed as $\text{round}(0.40 \times \text{Severity} + 0.60 \times (\text{Severity} \times \text{Confidence}))$. Includes a **low-confidence safety cap** ($\le 69$) if confidence $< 0.30$.
8. **Human Responder Verification:** Responders review full evidence, resolve contradictions, and execute deliberate actions (**Verify**, **Reject**, or **Split**). Every action is permanently recorded in an immutable, append-only audit log.

---

## Explainability & Evidence Trail

SETU enforces absolute transparency for all automated calculations:

- **Evidence Provenance:** Every candidate incident links directly to its constituent raw reports with source channel tags (WhatsApp, SMS, Field Worker, Web) and timestamps.
- **Side-by-Side Contradictions:** Conflicting reports are displayed with highlighted differences and verbatim quotes so dispatchers never make decisions on contradictory data without awareness.
- **Breakdown Diagnostics:** The UI provides complete arithmetic breakdowns for Severity, Confidence corroboration factors, and Priority calculation.

---

## Human-in-the-Loop Responder Workflow

All operational state transitions require explicit human authorization:

- **Verify Incident:** Confirms that the candidate incident is genuine and ready for operational dispatch. The responder enters their ID and operational notes.
- **Reject Incident:** Flags an incident as false alarm, spam, or duplicate, requiring a justification note.
- **Split Incident:** If disparate reports were merged into one incident, the responder can check individual reports and split them out into a new canonical candidate incident (e.g. `INC-014`).
- **Append-Only Audit Trail:** An immutable audit log records every triage decision with timestamps, operator identity, action types, and notes for post-disaster operational reviews.

---

## Verified Deterministic Demo Results

The preloaded Rampur urban flood dataset produces the following verified, deterministic results upon pipeline execution:

| Metric | Benchmark Result | Operational Meaning |
|---|---|---|
| **Raw Reports Ingested** | **20** | Multi-channel inputs (WhatsApp, SMS, Field Worker, Web) |
| **Candidate Incidents Formed** | **13** | Redundant and correlated reports fused into distinct incidents |
| **Physical Contradictions Surfaced** | **1** | Conflicting water-depth reports in Kotwali flagged for human review |
| **Geographic Groupings** | **5** | 4 Named Response Zones + 1 GPS/Outskirts grouping |
| **Named Response Zones** | **4** | Civil Lines, Kotwali, Bilaspur Chowk, Naya Mohalla |
| **Critical Incidents (85–100)** | **1** | Basement flooding with trapped residents (`INC-006`) |
| **High Incidents (70–84)** | **3** | Severe structural damage and major road submergence |
| **Medium Incidents (40–69)** | **8** | Localized waterlogging and minor power disruptions |
| **Low Incidents (0–39)** | **1** | Isolated low-urgency status check |

---

## Technology Stack

```
┌─────────────────────────────────────────────────────────────┐
│                 SETU RESPONDER CONSOLE                       │
│       React 19 · Vite · Vanilla CSS (EOC Dark Theme)         │
│     Tactical Rampur SVG GIS Map · Bidirectional Sync         │
└──────────────────────────────┬──────────────────────────────┘
                               │ HTTP / JSON API
┌──────────────────────────────▼──────────────────────────────┐
│                  FASTAPI BACKEND ENGINE                      │
│     FastAPI · Uvicorn · Pydantic v2 · SQLAlchemy ORM        │
│          SQLite (Embedded WAL Mode) · Lifespan Seeder       │
└──────────────┬───────────────────────────────┬──────────────┘
               │                               │
┌──────────────▼──────────────┐ ┌──────────────▼──────────────┐
│    LOCAL GAZETTEER & GIS    │ │  OFFLINE EMBEDDING RUNTIME  │
│   Rampur JSON Spatial Data  │ │  paraphrase-multilingual-   │
│  Haversine · Fuzzy Alias    │ │       MiniLM-L12-v2         │
└─────────────────────────────┘ └─────────────────────────────┘
```

- **Backend:** FastAPI (Python 3.11+ / bundled 3.14), Uvicorn, Pydantic, SQLAlchemy, SQLite
- **Frontend:** React 19, Vite, Vanilla CSS (high-contrast emergency operations center design)
- **NLP / Embeddings:** `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (offline cached)
- **GIS / Mapping:** Offline vector SVG map of Rampur with interactive zone boundaries and markers
- **Packaging:** Portable zero-install CPython 3.14 runtime bundle with pre-compiled wheels

---

## How to Run (Developer Setup)

If you wish to develop, test, or inspect the source code directly from this repository:

### Prerequisites
- Python 3.11+ (tested on Python 3.11–3.14)
- Node.js 18+ and npm
- Windows, macOS, or Linux

### 1. Backend Setup
```bash
cd backend
python -m venv venv

# Windows:
venv\Scripts\activate
# Linux / macOS:
# source venv/bin/activate

pip install -r requirements.txt

# Pre-cache the embedding model (requires internet for first download):
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')"

# Start the development API server:
uvicorn main:app --reload --port 8000
```

### 2. Frontend Setup
```bash
cd frontend
npm install
npm run dev
```
Open `http://localhost:5173` in your browser.

### 3. Automated Test Suite
The project includes a comprehensive test suite covering normalizers, extractors, location resolvers, matchers, clusterers, contradiction detection, assessors, and audit trails:
```bash
cd backend
venv\Scripts\pytest.exe tests/ -v
```
*Current benchmark: 448 passing tests, 1 intentionally skipped fixture test.*

### 4. Assembling the Zero-Install Release Package
To build the standalone zero-install Windows demo package from source:
```bash
# From repository root:
python tools/package_judge_demo.py --zip
```
This script compiles the production React frontend, validates generated asset hashes, packages the backend, bundles the portable Python runtime, copies the offline embedding model, and produces `release/SETU-SIH-DEMO.zip`.

---

## Limitations & Production Considerations

While SETU demonstrates robust information fusion for evaluation purposes, operational production deployment requires additional considerations:

- **Security & Privacy:** Production deployment should enforce data minimization, PII redaction, role-based access control (RBAC), encrypted storage and transmission, retention controls, and auditable responder actions.
- **Scale:** The current embedded SQLite storage is optimized for district-level single-EOC demonstration. High-throughput state-level deployment would benefit from PostgreSQL with PostGIS extensions.
- **Model Tuning:** The multilingual MiniLM model effectively handles English and romanized Hindi. Production deployment in diverse regions should incorporate localized regional language models.
- **Synthetic Benchmark:** SIH demonstration data is synthetic and intended for reproducible evaluation. It does not represent a live emergency feed.

---

## Research & References

1. **JDL Data Fusion Model:** White, F. E. (1991). *Data Fusion Lexicon*. Joint Directors of Laboratories, Technical Panel for C3.
2. **Crisis Informatics:** Palen, L., & Anderson, K. M. (2016). *Crisis informatics—New data for extraordinary times*. Science, 353(6296), 224-225.
3. **Sentence-BERT:** Reimers, N., & Gurevych, I. (2019). *Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks*. EMNLP 2019.
4. **Explainable AI in Decision Support:** Miller, T. (2019). *Explanation in artificial intelligence: Insights from the social sciences*. Artificial Intelligence, 267, 1-38.

---

## Team DeLuQy

Developed for the **Smart India Hackathon (SIH) 2026** under Problem Statement **26206** (*Student Innovation — Disaster Management*).

- **Project:** SETU — Structured Emergency Bridge System
- **Tagline:** *Bridging Chaotic Emergency Reports to Structured Incident Intelligence*
- **Demo Distribution:** [Google Drive Demo Folder](https://drive.google.com/drive/folders/1rDSoWYNGWK5bJQcXQCb6ceBJOlAFftHO?usp=sharing)
- **License:** Prepared for SIH 2026 evaluation purposes.
