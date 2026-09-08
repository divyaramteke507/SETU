"""
SETU — Structured Emergency Bridge System

FastAPI application entry point.
Provides the health endpoint, seeds the database on startup,
and registers API routers.
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from config import DEMO_MODE, ALLOWED_ORIGINS, BASE_DIR
from database import init_db, get_db
from schemas import HealthResponse
from models import Report, Incident


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create tables and seed data."""
    init_db()

    # Seed the 20 demo reports on first run
    from database import SessionLocal
    from seed_data import seed_database
    db = SessionLocal()
    try:
        inserted = seed_database(db)
        if inserted > 0:
            print(f"[SETU] Seeded {inserted} reports into database.")
        else:
            print("[SETU] Database already seeded.")
    finally:
        db.close()

    yield  # Application runs here

    # Shutdown (nothing to clean up for SQLite)


app = FastAPI(
    title="SETU — Structured Emergency Bridge System",
    description=(
        "Emergency Information Fusion Engine. "
        "Converts heterogeneous emergency reports into explainable candidate incidents."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow configured frontend origins (defaults to local dev)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
from routers.incidents import router as incidents_router
app.include_router(incidents_router)


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["system"])
def health_check(db: Session = Depends(get_db)):
    """System health check with basic stats."""
    total_reports = db.query(Report).count()
    total_incidents = db.query(Incident).count()
    return HealthResponse(
        status="ok",
        demo_mode=DEMO_MODE,
        version="0.1.0",
        total_reports=total_reports,
        total_incidents=total_incidents,
    )


# ---------------------------------------------------------------------------
# Root & Frontend Serving
# ---------------------------------------------------------------------------

if os.getenv("SETU_JUDGE_MODE") == "1":
    dist_dir = os.getenv("FRONTEND_DIST_DIR", os.path.join(BASE_DIR, "..", "frontend", "dist"))
    dist_dir = os.path.abspath(dist_dir)
    if os.path.isdir(dist_dir):
        from fastapi.staticfiles import StaticFiles
        from fastapi.responses import FileResponse

        assets_dir = os.path.join(dist_dir, "assets")
        if os.path.isdir(assets_dir):
            app.mount("/assets", StaticFiles(directory=assets_dir), name="static_assets")

        @app.get("/", include_in_schema=False)
        def serve_judge_root():
            return FileResponse(os.path.join(dist_dir, "index.html"))

        @app.get("/{file_name:path}", include_in_schema=False)
        def serve_judge_static_or_spa(file_name: str):
            file_path = os.path.join(dist_dir, file_name)
            if os.path.isfile(file_path):
                return FileResponse(file_path)
            return FileResponse(os.path.join(dist_dir, "index.html"))
    else:
        @app.get("/", tags=["system"])
        def root():
            """Root endpoint — API info."""
            return {
                "name": "SETU — Structured Emergency Bridge System",
                "version": "0.1.0",
                "demo_mode": DEMO_MODE,
                "docs": "/docs",
            }
else:
    @app.get("/", tags=["system"])
    def root():
        """Root endpoint — API info."""
        return {
            "name": "SETU — Structured Emergency Bridge System",
            "version": "0.1.0",
            "demo_mode": DEMO_MODE,
            "docs": "/docs",
        }
