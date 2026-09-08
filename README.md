# SETU — Structured Emergency Bridge System

**Emergency Information Fusion Engine**

SIH 2026 · Problem Statement 26206 · Student Innovation — Disaster Management

---

## What SETU Is

A responder-side emergency information fusion system that converts heterogeneous, unstructured emergency reports into explainable candidate incidents while preserving uncertainty, contradictions, and source evidence.

**Primary user:** Authorized district/state emergency operations staff or designated responders.

## What SETU Is Not

- Not a citizen emergency reporting app
- Not an autonomous dispatch system
- Not a prediction engine
- Not a resource allocator

## SIH Demo Scope

| Parameter | Value |
|-----------|-------|
| Disaster type | Urban flooding |
| Location | Rampur, India |
| Time window | 90 minutes |
| Reports | 20 fictional |
| Sources | WhatsApp, SMS, Web, Field Worker |
| Locations | 4 geographic areas |
| Expected output | 4 candidate incidents |
| Decision maker | Human responder (always) |

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI (Python 3.11+) |
| Frontend | React 19 + Vite |
| Database | SQLite |
| Embeddings | paraphrase-multilingual-MiniLM-L12-v2 |
| Map | Static SVG (offline) + optional Leaflet |

## Setup

### Backend

```bash
cd backend
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/Mac
pip install -r requirements.txt
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')"
uvicorn main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

## Core Pipeline

```
RAW REPORTS → normalization → structured extraction → location resolution
→ candidate matching → clustering → contradiction detection
→ severity/confidence/priority → evidence display → human verification
```

## License

For SIH 2026 evaluation purposes.
