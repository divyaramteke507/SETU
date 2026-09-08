"""
SETU Configuration — thresholds, weights, scoring constants.

All tunable parameters for the fusion pipeline live here.
"""

import os

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DATABASE_URL = "sqlite:///./setu.db"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GAZETTEER_PATH = os.path.join(BASE_DIR, "gazetteer", "rampur.json")

# ---------------------------------------------------------------------------
# Embedding model
# ---------------------------------------------------------------------------
EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

# ---------------------------------------------------------------------------
# Match score weights (must sum to 1.0)
# ---------------------------------------------------------------------------
MATCH_WEIGHT_SEMANTIC = 0.40
MATCH_WEIGHT_GEOGRAPHIC = 0.30
MATCH_WEIGHT_TEMPORAL = 0.15
MATCH_WEIGHT_INCIDENT_TYPE = 0.15

# ---------------------------------------------------------------------------
# Clustering thresholds
# ---------------------------------------------------------------------------
CLUSTER_MERGE_THRESHOLD = 0.80        # >= 0.80 → merge into same candidate
CLUSTER_RELATED_THRESHOLD = 0.60      # 0.60–0.79 → possibly related, keep separate
# < 0.60 → separate, no automatic relationship

# ---------------------------------------------------------------------------
# Geographic proximity
# ---------------------------------------------------------------------------
GEO_MAX_DISTANCE_M = 2000            # Beyond 2 km → proximity = 0.0
GEO_SAME_LOCATION_SCORE = 1.0        # Same gazetteer entry
GEO_UNRESOLVED_SCORE = 0.0           # Either location unresolved → 0.0

# ---------------------------------------------------------------------------
# Temporal proximity
# ---------------------------------------------------------------------------
TEMPORAL_FULL_SCORE_MINUTES = 5       # <= 5 min apart → 1.0
TEMPORAL_DECAY_MINUTES = 90           # Linear decay to 0.0 at 90 min

# ---------------------------------------------------------------------------
# Incident type relationships (related pairs get 0.5 instead of 0.0)
# ---------------------------------------------------------------------------
RELATED_INCIDENT_TYPES = {
    frozenset({"flood", "waterlogging"}),
    frozenset({"flood", "rescue_needed"}),
    frozenset({"waterlogging", "road_blocked"}),
    frozenset({"power_outage", "structural_damage"}),
    frozenset({"flood", "structural_damage"}),
}

# Mutually exclusive incident types (deterministic incompatibility mapping for contradiction detection)
INCOMPATIBLE_INCIDENT_TYPES = {
    frozenset({"drought", "flood"}),
    frozenset({"wildfire", "flood"}),
    frozenset({"drought", "waterlogging"}),
}

# ---------------------------------------------------------------------------
# Location resolution
# ---------------------------------------------------------------------------
LOCATION_FUZZY_THRESHOLD = 0.75       # Minimum string similarity for fuzzy match
LOCATION_AMBIGUITY_DELTA = 0.05       # Ambiguity window: candidates within this delta are ambiguous
GEO_CONFIDENCE_GPS = 1.00             # GPS coordinates provided directly
GEO_CONFIDENCE_EXACT = 0.90           # Exact gazetteer name or alias match
GEO_CONFIDENCE_UNRESOLVED = 0.00      # Unresolved location

# ---------------------------------------------------------------------------
# Severity base weights by incident type
# ---------------------------------------------------------------------------
SEVERITY_TYPE_BASE = {
    "rescue_needed": 85,
    "flood": 65,
    "structural_damage": 60,
    "waterlogging": 40,
    "road_blocked": 35,
    "power_outage": 30,
}

# Severity keyword weights
SEVERITY_KEYWORD_MAP = {
    "critical": 90,
    "severe": 70,
    "moderate": 45,
    "minor": 20,
}

# Severity bonuses
SEVERITY_TRAPPED_BONUS = 15
SEVERITY_VULNERABLE_BONUS = 10
SEVERITY_MAX = 100

# ---------------------------------------------------------------------------
# Confidence breakdown weights (must sum to 1.0)
# ---------------------------------------------------------------------------
CONFIDENCE_WEIGHT_SOURCE_COUNT = 0.25
CONFIDENCE_WEIGHT_SOURCE_DIVERSITY = 0.20
CONFIDENCE_WEIGHT_CONSISTENCY = 0.25
CONFIDENCE_WEIGHT_EXTRACTION = 0.15
CONFIDENCE_WEIGHT_INFO_TYPE = 0.15

# Discrete confidence calibration policies
CONFIDENCE_SOURCE_COUNT_POLICY = {
    1: 0.40,
    2: 0.70,
    3: 0.85,
    4: 1.00,  # 4+ reports -> 1.00
}

CONFIDENCE_SOURCE_DIVERSITY_POLICY = {
    1: 0.40,
    2: 0.75,
    3: 1.00,  # 3+ unique sources -> 1.00
}

CONFIDENCE_CONSISTENCY_POLICY = {
    0: 1.00,
    1: 0.75,
    2: 0.50,
    3: 0.25,
    4: 0.00,  # 4+ contradictions -> 0.00
}

CONFIDENCE_INFO_TYPE_POLICY = {
    "explicit": 1.00,
    "inferred": 0.40,
    "null": 0.20,
    "unknown": 0.20,
}

# ---------------------------------------------------------------------------
# Priority
# ---------------------------------------------------------------------------
PRIORITY_FORMULA_BASE = 0.40
PRIORITY_FORMULA_CONFIDENCE_FACTOR = 0.60
PRIORITY_LOW_CONFIDENCE_THRESHOLD = 0.30
PRIORITY_LOW_CONFIDENCE_CAP = 69

# ---------------------------------------------------------------------------
# Urgency bands
# ---------------------------------------------------------------------------
URGENCY_BANDS = [
    {"min": 85, "max": 100, "label": "critical", "color": "#DC2626"},
    {"min": 70, "max": 84,  "label": "high",     "color": "#EA580C"},
    {"min": 40, "max": 69,  "label": "medium",   "color": "#CA8A04"},
    {"min": 0,  "max": 39,  "label": "low",      "color": "#64748B"},
]

# ---------------------------------------------------------------------------
# Contradiction types
# ---------------------------------------------------------------------------
CONTRADICTION_TYPES = [
    "numeric",
    "severity",
    "incident_type",
    "location",
    "hazard_structural",
]

# ---------------------------------------------------------------------------
# People mention categories (distinguishes trapped vs affected vs nearby)
# ---------------------------------------------------------------------------
PEOPLE_CATEGORIES = [
    "trapped",         # Physically trapped / rescue required (e.g. basement, rooftop)
    "affected",        # Residents impacted (e.g. power cut, waterlogging, displaced)
    "nearby",          # Residents/bystanders in general vicinity
    "at_risk",         # In path of potential hazard (e.g. leaning wall, hanging wire)
]

# ---------------------------------------------------------------------------
# Romanized Hindi keyword list (controlled benchmark vocabulary)
# ---------------------------------------------------------------------------
ROMANIZED_HINDI_KEYWORDS = [
    "paani", "barish", "bachao", "sadak", "bijli", "madad",
    "mein", "hai", "hain", "nahi", "bahut", "log", "ghar",
    "wajah", "se", "pe", "ke", "ka", "ki", "ko",
    "tez", "aao", "phase", "phaas", "doob", "bhar",
    "mushkil", "khatra", "khatarnak", "tedi", "tooti",
    "mohalla", "chowk", "gaadi", "deewar",
]

# ---------------------------------------------------------------------------
# Demo mode
# ---------------------------------------------------------------------------
DEMO_RESPONDER_ID = "demo-responder"
DEMO_MODE = True
