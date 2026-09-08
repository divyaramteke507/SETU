"""
SETU Location Resolver Service — geographic resolution with strict priority.

Converts extracted location phrases and/or report GPS coordinates into
structured, explainable geographic resolutions with calibrated confidence.

Resolution Priority Chain:
  1. VALID GPS           (geo_confidence = 1.00, resolution_method = "gps")
  2. EXACT GAZETTEER     (geo_confidence = 0.90, resolution_method = "exact")
  3. FUZZY GAZETTEER     (geo_confidence = 0.60–0.80, resolution_method = "fuzzy")
  4. UNRESOLVED          (geo_confidence = 0.00, resolution_method = "unresolved")

Guarantees:
  - NEVER invents coordinates (no default city center, no guessing).
  - Valid GPS ALWAYS takes precedence over text-derived locations.
  - Invalid GPS coordinates are rejected and NEVER silently clamped.
  - Multilingual support: English, Devanagari Hindi, Romanized Hindi aliases.
  - Exposes resolution uncertainty and fuzzy match similarity scores.
  - Ambiguous close fuzzy candidates are flagged as unresolved / review-required.
  - Offline-first: zero external geocoding API or network calls.
  - Deterministic: identical inputs always yield identical outputs.
"""

from __future__ import annotations

import json
import math
import os
import re
import unicodedata
from typing import Any, Optional

from thefuzz import fuzz

from config import (
    GAZETTEER_PATH,
    GEO_CONFIDENCE_EXACT,
    GEO_CONFIDENCE_GPS,
    GEO_CONFIDENCE_UNRESOLVED,
    LOCATION_AMBIGUITY_DELTA,
    LOCATION_FUZZY_THRESHOLD,
)
from schemas import LocationCandidate, LocationResolution


# Module-level gazetteer cache
_GAZETTEER_CACHE: Optional[list[dict]] = None


# ---------------------------------------------------------------------------
# Coordinate Validation
# ---------------------------------------------------------------------------

def validate_coordinates(lat: Any, lon: Any) -> bool:
    """
    Validate geographic coordinates.

    Coordinates must:
      - Be non-null numbers (float or int, not bool)
      - Not be NaN or infinite
      - Fall strictly within valid ranges:
          -90.0 <= latitude <= 90.0
          -180.0 <= longitude <= 180.0

    Returns True if valid, False otherwise. Never raises or clamps.
    """
    if lat is None or lon is None:
        return False

    if isinstance(lat, bool) or isinstance(lon, bool):
        return False

    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        try:
            lat = float(lat)
            lon = float(lon)
        except (ValueError, TypeError):
            return False

    if math.isnan(lat) or math.isnan(lon) or math.isinf(lat) or math.isinf(lon):
        return False

    if not (-90.0 <= lat <= 90.0):
        return False

    if not (-180.0 <= lon <= 180.0):
        return False

    return True


# ---------------------------------------------------------------------------
# Location Phrase Normalization
# ---------------------------------------------------------------------------

def normalize_location_phrase(phrase: Optional[str]) -> str:
    """
    Produce a clean, normalized version of a location string for matching.

    Operations:
      - Unicode NFC normalization
      - Strip leading/trailing whitespace and punctuation
      - Collapse internal whitespace
      - Convert to lowercase
    """
    if not phrase:
        return ""

    text = unicodedata.normalize("NFC", str(phrase))
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" \t\n\r,.;:'\"-()[]{}")
    return text.lower()


def _strip_common_prepositions(phrase: str) -> str:
    """Strip common English/Hindi location prefixes or suffixes for matching."""
    cleaned = phrase
    # English prefixes
    for prefix in ("near ", "at ", "in ", "around "):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            break
    # English suffixes
    for suffix in (" area", " rampur", " intersection"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[:-len(suffix)].strip()
            break
    # Hindi/Romanized suffixes
    for suffix in (" ke paas", " mein", " pe", " par", " me"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[:-len(suffix)].strip()
            break
    return cleaned


# ---------------------------------------------------------------------------
# Gazetteer Management
# ---------------------------------------------------------------------------

def load_gazetteer(path: Optional[str] = None) -> list[dict]:
    """
    Load the location gazetteer from JSON file.

    Caches the default gazetteer in memory.
    Returns the list of location definitions.
    """
    global _GAZETTEER_CACHE

    target_path = path or GAZETTEER_PATH

    # Return cached instance if using default path and already loaded
    if path is None and _GAZETTEER_CACHE is not None:
        return _GAZETTEER_CACHE

    if not os.path.exists(target_path):
        raise FileNotFoundError(f"Gazetteer file not found at: {target_path}")

    with open(target_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    locations = data.get("locations", [])

    if path is None:
        _GAZETTEER_CACHE = locations

    return locations


def clear_gazetteer_cache() -> None:
    """Clear the cached gazetteer instance (primarily for testing)."""
    global _GAZETTEER_CACHE
    _GAZETTEER_CACHE = None


# ---------------------------------------------------------------------------
# Exact Gazetteer Matching
# ---------------------------------------------------------------------------

def exact_gazetteer_match(
    phrase: str,
    gazetteer: list[dict],
) -> Optional[tuple[dict, str]]:
    """
    Check if normalized phrase matches any gazetteer canonical name or alias.

    Checks:
      1. Direct equality against normalized canonical names and aliases.
      2. Equality after stripping common prepositions ("near ", "in ", "area").

    Returns (matched_location_dict, matched_phrase) or None.
    """
    norm_phrase = normalize_location_phrase(phrase)
    if not norm_phrase:
        return None

    stripped_phrase = _strip_common_prepositions(norm_phrase)

    for loc in gazetteer:
        # Canonical names
        canonicals = [
            loc.get("name_en", ""),
            loc.get("name_hi", ""),
            loc.get("name_hi_latn", ""),
            loc.get("location_id", ""),
        ]
        aliases = loc.get("aliases", [])
        all_targets = canonicals + aliases

        for target in all_targets:
            norm_target = normalize_location_phrase(target)
            if not norm_target:
                continue

            # 1. Direct exact match
            if norm_phrase == norm_target:
                return loc, target

            # 2. Match with stripped prepositions
            if stripped_phrase and stripped_phrase == norm_target:
                return loc, target

            # 3. Target stripped match (e.g. target is "civil lines area" and phrase is "civil lines")
            if norm_phrase == _strip_common_prepositions(norm_target):
                return loc, target

    # 4. Contained phrase match (e.g. "Bilaspur Chowk due to heavy waterlogging" containing "Bilaspur Chowk")
    # Sort targets by length descending so longer/more specific phrases match first
    all_loc_targets: list[tuple[dict, str, str]] = []
    for loc in gazetteer:
        canonicals = [
            loc.get("name_en", ""),
            loc.get("name_hi", ""),
            loc.get("name_hi_latn", ""),
        ]
        aliases = loc.get("aliases", [])
        for target in canonicals + aliases:
            norm_t = normalize_location_phrase(target)
            if norm_t and len(norm_t) >= 4:
                all_loc_targets.append((loc, target, norm_t))

    all_loc_targets.sort(key=lambda item: len(item[2]), reverse=True)

    for loc, original_target, norm_target in all_loc_targets:
        pattern = r"(?:\b|^|\s)" + re.escape(norm_target) + r"(?:\b|$|\s)"
        if re.search(pattern, norm_phrase):
            return loc, original_target

    return None


# ---------------------------------------------------------------------------
# Fuzzy Gazetteer Matching
# ---------------------------------------------------------------------------

def fuzzy_gazetteer_match(
    phrase: str,
    gazetteer: list[dict],
    threshold: float = LOCATION_FUZZY_THRESHOLD,
    ambiguity_delta: float = LOCATION_AMBIGUITY_DELTA,
) -> tuple[Optional[dict], float, list[LocationCandidate], bool]:
    """
    Perform fuzzy matching of location phrase against gazetteer locations.

    Groups matches by location_id and takes the maximum similarity per location.
    Checks for ambiguity when multiple locations have scores >= threshold and
    differ by <= ambiguity_delta.

    Returns:
      (best_location_dict, best_score, candidates_list, is_ambiguous)
    """
    norm_phrase = normalize_location_phrase(phrase)
    if not norm_phrase:
        return None, 0.0, [], False

    # Also test stripped phrase for cleaner matching
    stripped_phrase = _strip_common_prepositions(norm_phrase)

    location_best_scores: dict[str, tuple[dict, float, str]] = {}

    for loc in gazetteer:
        loc_id = loc["location_id"]
        canonicals = [
            loc.get("name_en", ""),
            loc.get("name_hi", ""),
            loc.get("name_hi_latn", ""),
        ]
        aliases = loc.get("aliases", [])
        all_targets = canonicals + aliases

        max_loc_score = 0.0
        best_target_name = loc.get("name_en", loc_id)

        for target in all_targets:
            norm_target = normalize_location_phrase(target)
            if not norm_target:
                continue

            # Standard Levenshtein ratio
            score_full = fuzz.ratio(norm_phrase, norm_target) / 100.0
            # Token sort ratio for word reordering
            score_sort = fuzz.token_sort_ratio(norm_phrase, norm_target) / 100.0
            score = max(score_full, score_sort)

            # If stripped phrase differs, compare that as well
            if stripped_phrase and stripped_phrase != norm_phrase:
                s_strip = max(
                    fuzz.ratio(stripped_phrase, norm_target) / 100.0,
                    fuzz.token_sort_ratio(stripped_phrase, norm_target) / 100.0,
                )
                score = max(score, s_strip)

            if score > max_loc_score:
                max_loc_score = score
                best_target_name = target

        # Retain full-precision score internally (no intermediate rounding)
        location_best_scores[loc_id] = (loc, max_loc_score, best_target_name)

    # Filter candidates meeting threshold using full-precision score
    qualifying: list[tuple[str, dict, float, str]] = []
    for loc_id, (loc, score, target_name) in location_best_scores.items():
        if score >= threshold:
            qualifying.append((loc_id, loc, score, target_name))

    if not qualifying:
        return None, 0.0, [], False

    # Rank candidates strictly by full-precision score (descending)
    qualifying.sort(key=lambda item: item[2], reverse=True)

    # Ambiguity check: evaluate using full-precision difference
    top_score = qualifying[0][2]
    is_ambiguous = False
    if len(qualifying) > 1:
        second_score = qualifying[1][2]
        if (top_score - second_score) <= ambiguity_delta:
            is_ambiguous = True

    # Build presentation/serialization candidates list (rounded for presentation)
    candidates: list[LocationCandidate] = [
        LocationCandidate(
            location_id=loc_id,
            name=loc.get("name_en", loc_id),
            similarity=round(score, 4),
            latitude=loc["lat"],
            longitude=loc["lon"],
        )
        for loc_id, loc, score, _ in qualifying
    ]

    if is_ambiguous:
        return None, top_score, candidates, True

    best_loc = qualifying[0][1]
    return best_loc, top_score, candidates, False


# ---------------------------------------------------------------------------
# Main Resolver Pipeline
# ---------------------------------------------------------------------------

def resolve_location(
    location_raw: Optional[str] = None,
    gps_lat: Optional[float] = None,
    gps_lon: Optional[float] = None,
    language: Optional[str] = None,
    gazetteer_path: Optional[str] = None,
    gazetteer: Optional[list[dict]] = None,
) -> LocationResolution:
    """
    Resolve emergency report location following strict priority:
      1. Valid GPS
      2. Exact Gazetteer Match
      3. Fuzzy Gazetteer Match (>= threshold, non-ambiguous)
      4. Unresolved

    Guarantees:
      - Valid GPS always wins and is never replaced by gazetteer coordinates.
      - Never fabricates or defaults coordinates for unknown places.
      - Returns an explainable LocationResolution schema.
    """
    clean_raw = location_raw.strip() if location_raw else None
    if clean_raw == "":
        clean_raw = None

    # -----------------------------------------------------------------------
    # Step 1: GPS Resolution
    # -----------------------------------------------------------------------
    if validate_coordinates(gps_lat, gps_lon):
        lat_val = float(gps_lat)
        lon_val = float(gps_lon)
        return LocationResolution(
            location_resolved=True,
            location_raw=clean_raw,
            location_id=None,
            resolved_name=f"GPS ({lat_val:.4f}, {lon_val:.4f})",
            latitude=lat_val,
            longitude=lon_val,
            geo_confidence=GEO_CONFIDENCE_GPS,
            resolution_method="gps",
            match_score=None,
            candidates=[],
            explanation=f"Direct GPS coordinates verified (lat={lat_val:.4f}, lon={lon_val:.4f})",
        )

    # If coordinates were provided but invalid, log in explanation and continue
    invalid_gps_note: Optional[str] = None
    if gps_lat is not None or gps_lon is not None:
        invalid_gps_note = f"Invalid GPS coordinates rejected (lat={gps_lat}, lon={gps_lon})"

    # If no raw location text is available, cannot resolve via gazetteer
    if not clean_raw:
        return LocationResolution(
            location_resolved=False,
            location_raw=None,
            location_id=None,
            resolved_name=None,
            latitude=None,
            longitude=None,
            geo_confidence=GEO_CONFIDENCE_UNRESOLVED,
            resolution_method="unresolved",
            match_score=None,
            candidates=[],
            explanation=invalid_gps_note or "No location or GPS provided",
        )

    # Load gazetteer
    gaz = gazetteer if gazetteer is not None else load_gazetteer(gazetteer_path)

    # -----------------------------------------------------------------------
    # Step 2: Exact Gazetteer Match
    # -----------------------------------------------------------------------
    exact_result = exact_gazetteer_match(clean_raw, gaz)
    if exact_result is not None:
        loc, matched_target = exact_result
        explanation = f"Exact gazetteer match for '{clean_raw}' matched '{matched_target}'"
        if invalid_gps_note:
            explanation = f"{invalid_gps_note}; {explanation}"

        return LocationResolution(
            location_resolved=True,
            location_raw=clean_raw,
            location_id=loc["location_id"],
            resolved_name=loc["name_en"],
            latitude=float(loc["lat"]),
            longitude=float(loc["lon"]),
            geo_confidence=GEO_CONFIDENCE_EXACT,
            resolution_method="exact",
            match_score=1.0,
            candidates=[],
            explanation=explanation,
        )

    # -----------------------------------------------------------------------
    # Step 3: Fuzzy Gazetteer Match
    # -----------------------------------------------------------------------
    fuzzy_loc, score, candidates, is_ambiguous = fuzzy_gazetteer_match(
        clean_raw,
        gaz,
        threshold=LOCATION_FUZZY_THRESHOLD,
        ambiguity_delta=LOCATION_AMBIGUITY_DELTA,
    )

    if is_ambiguous:
        cand_names = [f"'{c.name}' ({c.similarity:.2f})" for c in candidates[:3]]
        explanation = (
            f"Ambiguous location for '{clean_raw}': close similarity between "
            f"{', '.join(cand_names)}"
        )
        if invalid_gps_note:
            explanation = f"{invalid_gps_note}; {explanation}"

        return LocationResolution(
            location_resolved=False,
            location_raw=clean_raw,
            location_id=None,
            resolved_name=None,
            latitude=None,
            longitude=None,
            geo_confidence=GEO_CONFIDENCE_UNRESOLVED,
            resolution_method="unresolved",
            match_score=round(score, 4),
            candidates=candidates,
            explanation=explanation,
        )

    if fuzzy_loc is not None:
        # Confidence scaled linearly from 0.60 to 0.80 based on score in [0.75, 1.0]
        denom = 1.0 - LOCATION_FUZZY_THRESHOLD
        if denom > 0:
            scaled = 0.60 + ((score - LOCATION_FUZZY_THRESHOLD) / denom) * 0.20
        else:
            scaled = 0.70
        geo_conf = round(min(0.80, max(0.60, scaled)), 2)

        explanation = (
            f"Fuzzy gazetteer match for '{clean_raw}' -> '{fuzzy_loc['name_en']}' "
            f"(similarity: {score:.2f})"
        )
        if invalid_gps_note:
            explanation = f"{invalid_gps_note}; {explanation}"

        return LocationResolution(
            location_resolved=True,
            location_raw=clean_raw,
            location_id=fuzzy_loc["location_id"],
            resolved_name=fuzzy_loc["name_en"],
            latitude=float(fuzzy_loc["lat"]),
            longitude=float(fuzzy_loc["lon"]),
            geo_confidence=geo_conf,
            resolution_method="fuzzy",
            match_score=round(score, 4),
            candidates=candidates,
            explanation=explanation,
        )

    # -----------------------------------------------------------------------
    # Step 4: Unresolved
    # -----------------------------------------------------------------------
    explanation = f"Location '{clean_raw}' could not be matched to any gazetteer entry"
    if invalid_gps_note:
        explanation = f"{invalid_gps_note}; {explanation}"

    return LocationResolution(
        location_resolved=False,
        location_raw=clean_raw,
        location_id=None,
        resolved_name=None,
        latitude=None,
        longitude=None,
        geo_confidence=GEO_CONFIDENCE_UNRESOLVED,
        resolution_method="unresolved",
        match_score=None,
        candidates=[],
        explanation=explanation,
    )
