"""
SETU Normalizer Service — text and metadata normalization.

Responsibilities:
  - Normalize whitespace and harmless formatting artifacts.
  - Normalize source channel labels to canonical enum values.
  - Parse and normalize timestamps to UTC.
  - Detect/confirm language script (en / hi / hi-Latn).

Guarantees:
  - raw_text is NEVER modified in the database.
  - normalized_text is a cleaned copy for downstream processing.
  - Deterministic: same input always produces same output.
  - Language detection is scoped to the three benchmark scripts only.

Not responsible for:
  - Extraction of structured fields.
  - Location resolution.
  - Embedding generation.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone, timedelta
from typing import Optional

from config import ROMANIZED_HINDI_KEYWORDS


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IST = timezone(timedelta(hours=5, minutes=30))

SOURCE_CANONICAL = {
    # Canonical → canonical (identity)
    "whatsapp": "whatsapp",
    "sms": "sms",
    "web": "web",
    "field_worker": "field_worker",
    # Common variations
    "whats app": "whatsapp",
    "wa": "whatsapp",
    "text": "sms",
    "website": "web",
    "online": "web",
    "field": "field_worker",
    "fieldworker": "field_worker",
    "field worker": "field_worker",
    "fw": "field_worker",
}

# Minimum number of Romanized Hindi keywords required
# to classify an ASCII-only text as hi-Latn
_ROMANIZED_HINDI_MIN_HITS = 3


# ---------------------------------------------------------------------------
# Timestamp normalization
# ---------------------------------------------------------------------------

def normalize_timestamp(timestamp_str: str) -> Optional[datetime]:
    """
    Parse an ISO 8601 timestamp string and convert to UTC datetime.

    Supports:
      - Full ISO 8601 with timezone offset (e.g. 2026-09-07T08:00:00+05:30)
      - Naive datetime (assumed IST)

    Returns None if parsing fails.
    """
    if not timestamp_str:
        return None

    try:
        dt = datetime.fromisoformat(timestamp_str)
        if dt.tzinfo is None:
            # Assume IST for naive timestamps (Rampur demo)
            dt = dt.replace(tzinfo=IST)
        return dt.astimezone(timezone.utc).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Source normalization
# ---------------------------------------------------------------------------

def normalize_source(source: str) -> str:
    """
    Normalize a source channel label to a canonical enum value.

    Returns the canonical value, or the original lowered string if unknown.
    """
    key = source.strip().lower().replace("_", " ").replace("-", " ")
    key = " ".join(key.split())  # Collapse multiple spaces
    # Try underscore-free first, then try with underscores
    canonical = SOURCE_CANONICAL.get(key)
    if canonical:
        return canonical
    key_underscored = key.replace(" ", "_")
    canonical = SOURCE_CANONICAL.get(key_underscored)
    if canonical:
        return canonical
    return source.strip().lower()


# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------

def normalize_text(raw_text: str) -> str:
    """
    Produce a cleaned copy of the raw report text for downstream processing.

    Operations:
      - Unicode NFC normalization (consistent representation)
      - Collapse multiple whitespace/newlines into single spaces
      - Strip leading/trailing whitespace
      - Remove zero-width characters
      - Normalize repeated punctuation (!!!! → !, ???? → ?)

    Does NOT:
      - Translate or transliterate
      - Remove meaningful content
      - Modify the stored raw_text
    """
    if not raw_text:
        return ""

    text = raw_text

    # Unicode NFC normalization for consistent Devanagari representation
    text = unicodedata.normalize("NFC", text)

    # Remove zero-width characters (ZWJ, ZWNJ, ZWSP, BOM)
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)

    # Collapse multiple whitespace/newlines into single space
    text = re.sub(r"\s+", " ", text)

    # Normalize repeated punctuation: !!!! → !, ???? → ?
    text = re.sub(r"!{2,}", "!", text)
    text = re.sub(r"\?{2,}", "?", text)
    text = re.sub(r"\.{4,}", "...", text)  # Preserve ... but cap at 3

    return text.strip()


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

def _has_devanagari(text: str) -> bool:
    """Check if text contains Devanagari Unicode characters."""
    for ch in text:
        if "\u0900" <= ch <= "\u097f":
            return True
    return False


def _count_romanized_hindi_keywords(text: str) -> int:
    """
    Count how many distinct controlled Romanized Hindi keywords appear in text.
    Uses word boundary matching to avoid false positives.
    """
    text_lower = text.lower()
    # Tokenize on whitespace and strip punctuation for matching
    words = set(re.findall(r"[a-zA-Z]+", text_lower))
    hits = 0
    for kw in ROMANIZED_HINDI_KEYWORDS:
        if kw in words:
            hits += 1
    return hits


def detect_language(text: str, declared_language: Optional[str] = None) -> str:
    """
    Detect or confirm the language/script of a report.

    Strategy:
      1. If text contains Devanagari script characters → "hi"
      2. If text is ASCII-ish and contains ≥ _ROMANIZED_HINDI_MIN_HITS
         controlled Hindi keywords → "hi-Latn"
      3. Otherwise → "en"

    If declared_language is provided and non-empty, it is trusted as a
    strong hint.  We only override it if script evidence clearly contradicts
    (e.g., declared "en" but text is full Devanagari).

    This is NOT general-purpose language detection.  It is scoped to the
    three benchmark scripts for the SIH demo.
    """
    if not text:
        return declared_language or "en"

    has_dev = _has_devanagari(text)
    romanized_hits = _count_romanized_hindi_keywords(text)

    # If declared language is available, verify consistency
    if declared_language:
        # Trust the declaration unless evidence contradicts
        if declared_language == "en" and has_dev:
            return "hi"  # Override: clearly Devanagari
        if declared_language == "hi" and not has_dev:
            # Declared Hindi but no Devanagari — might be Romanized
            if romanized_hits >= _ROMANIZED_HINDI_MIN_HITS:
                return "hi-Latn"
            return declared_language  # Trust the declaration
        return declared_language

    # No declaration — detect from content
    if has_dev:
        return "hi"
    if romanized_hits >= _ROMANIZED_HINDI_MIN_HITS:
        return "hi-Latn"
    return "en"


# ---------------------------------------------------------------------------
# Full normalization pipeline (single report)
# ---------------------------------------------------------------------------

class NormalizedReport:
    """Container for normalization output of a single report."""

    __slots__ = (
        "report_id",
        "normalized_text",
        "received_at_utc",
        "source",
        "language",
    )

    def __init__(
        self,
        report_id: str,
        normalized_text: str,
        received_at_utc: Optional[datetime],
        source: str,
        language: str,
    ):
        self.report_id = report_id
        self.normalized_text = normalized_text
        self.received_at_utc = received_at_utc
        self.source = source
        self.language = language

    def __repr__(self) -> str:
        return (
            f"NormalizedReport(id={self.report_id!r}, lang={self.language!r}, "
            f"source={self.source!r}, utc={self.received_at_utc})"
        )


def normalize_report(
    report_id: str,
    raw_text: str,
    received_at: str,
    source: str,
    language: str,
) -> NormalizedReport:
    """
    Normalize a single report's metadata and text.

    Parameters match the Report model fields.
    Returns a NormalizedReport with cleaned data ready for extraction.
    """
    return NormalizedReport(
        report_id=report_id,
        normalized_text=normalize_text(raw_text),
        received_at_utc=normalize_timestamp(received_at),
        source=normalize_source(source),
        language=detect_language(raw_text, declared_language=language),
    )
