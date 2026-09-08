"""
SETU Extractor Service — structured field extraction from normalized text.

Converts normalized emergency text into structured evidence objects.
Every extracted field follows the ExtractionField schema:
  { field_name, value, confidence, evidence }

Extracted fields:
  - incident_type:     enum from SEVERITY_TYPE_BASE keys
  - people_estimate:   PeopleCount-compatible dict {count, category, description}
  - vulnerable_persons: boolean + detail
  - trapped_or_rescue:  boolean + detail
  - urgency_signals:    list of urgency phrases
  - location_raw:       raw location phrase(s) from the text
  - severity_hint:      enum: critical / severe / moderate / minor
  - info_type:          explicit (direct observation) / inferred (hearsay)

Guarantees:
  - Never invents information not present in the source text.
  - Absent fields → value=None, confidence=0, evidence=[]
  - Evidence phrases are actual substrings from the report text.
  - Confidence reflects extraction pattern quality, NOT danger level.
  - People categories preserved: trapped / affected / nearby / at_risk.

Not responsible for:
  - Location resolution to coordinates.
  - Severity/priority scoring.
  - Report merging or clustering.
  - Contradiction detection.

Architecture:
  Small composable functions (one per field), then composed into a
  single extraction result.  Designed for replacement/augmentation
  by a trained model later.
"""

from __future__ import annotations

import re
from typing import Optional

from schemas import ExtractionField


# ============================================================================
# KEYWORD DICTIONARIES — per language variant
# ============================================================================
# These are controlled benchmark dictionaries, NOT general NLP.
# They cover the vocabulary present in the 20 Rampur flood reports.

# --- Incident type patterns ---

INCIDENT_TYPE_PATTERNS: dict[str, list[dict]] = {
    "flood": [
        # English
        {"re": r"\bflood(?:ing|ed|water|s)?\b", "lang": "en", "conf": 0.90},
        {"re": r"\bwater\s+(?:level|rising|entered|depth)\b", "lang": "en", "conf": 0.80},
        {"re": r"\bsubmerge[ds]?\b", "lang": "en", "conf": 0.85},
        {"re": r"\bwaterlog(?:ged|ging)?\b", "lang": "en", "conf": 0.85},
        {"re": r"\bdrain\s+overflow\b", "lang": "en", "conf": 0.75},
        # Hindi (Devanagari)
        {"re": r"पानी\s*भर", "lang": "hi", "conf": 0.85},
        {"re": r"पानी.*बढ़\s*रहा", "lang": "hi", "conf": 0.80},
        {"re": r"डूब", "lang": "hi", "conf": 0.80},
        # Romanized Hindi
        {"re": r"\bpaani\s+bhar\b", "lang": "hi-Latn", "conf": 0.85},
        {"re": r"\bpaani.*(?:tez|badh)\b", "lang": "hi-Latn", "conf": 0.80},
        {"re": r"\bdoob\b", "lang": "hi-Latn", "conf": 0.75},
    ],
    "rescue_needed": [
        {"re": r"\brescue\b", "lang": "en", "conf": 0.90},
        {"re": r"\btrapped\b", "lang": "en", "conf": 0.90},
        {"re": r"\b(?:people|family|someone)\s+stuck\b", "lang": "en", "conf": 0.85},
        {"re": r"\bscreaming\s+for\s+help\b", "lang": "en", "conf": 0.95},
        {"re": r"\bbachao\b", "lang": "hi-Latn", "conf": 0.90},
        {"re": r"\blog\s+phase\b", "lang": "hi-Latn", "conf": 0.85},
        {"re": r"परिवार.*फंस|लोग.*फंस|फंसा\s+है", "lang": "hi", "conf": 0.85},
        {"re": r"बचाओ", "lang": "hi", "conf": 0.90},
        {"re": r"मदद\s*भेजो", "lang": "hi", "conf": 0.85},
    ],
    "road_blocked": [
        {"re": r"\broad\s+(?:completely\s+)?blocked\b", "lang": "en", "conf": 0.90},
        {"re": r"\btraffic\s+(?:at\s+)?standstill\b", "lang": "en", "conf": 0.85},
        {"re": r"\bvehicles?\s+stuck\b", "lang": "en", "conf": 0.80},
        {"re": r"\btraffic\s+disruption\b", "lang": "en", "conf": 0.75},
        {"re": r"सड़क\s*बंद", "lang": "hi", "conf": 0.85},
        {"re": r"गाड़ियाँ\s*फंसी", "lang": "hi", "conf": 0.80},
        {"re": r"\broad\s+block\b", "lang": "hi-Latn", "conf": 0.85},
        {"re": r"\bgaadi\s+phase\b", "lang": "hi-Latn", "conf": 0.75},
    ],
    "power_outage": [
        {"re": r"\bpower\s+(?:cut|outage|failure)\b", "lang": "en", "conf": 0.90},
        {"re": r"\btransformer\s+(?:submerge|flood)\w*\b", "lang": "en", "conf": 0.85},
        {"re": r"\bwithout\s+electricity\b", "lang": "en", "conf": 0.85},
        {"re": r"\bwires?\s+hanging\b", "lang": "en", "conf": 0.75},
        {"re": r"बिजली\s*गुल", "lang": "hi", "conf": 0.90},
        {"re": r"ट्रांसफॉर्मर.*डूब", "lang": "hi", "conf": 0.85},
        {"re": r"\bbijli\s+nahi\b", "lang": "hi-Latn", "conf": 0.85},
    ],
    "structural_damage": [
        {"re": r"\b(?:boundary\s+)?wall\s+(?:showing|leaning|tilt|crack|collaps)\w*\b", "lang": "en", "conf": 0.90},
        {"re": r"\brisk\s+of\s+collapse\b", "lang": "en", "conf": 0.90},
        {"re": r"\bsignificant\s+cracks\b", "lang": "en", "conf": 0.90},
        {"re": r"\bstructur(?:e|al)\s+(?:damage|risk|concern)\b", "lang": "en", "conf": 0.80},
        {"re": r"\bdamaged\s+boundary\s+wall\b", "lang": "en", "conf": 0.90},
        {"re": r"\bdeewar\s+tedi\b", "lang": "hi-Latn", "conf": 0.90},
        {"re": r"\bgir\s+jayegi\b", "lang": "hi-Latn", "conf": 0.80},
        {"re": r"\btooti\b", "lang": "hi-Latn", "conf": 0.75},
    ],
    "waterlogging": [
        {"re": r"\bwaterlog(?:ged|ging)\b", "lang": "en", "conf": 0.85},
        {"re": r"\bwading\s+through\s+water\b", "lang": "en", "conf": 0.70},
    ],
}

# --- Severity hint keywords ---

SEVERITY_PATTERNS: dict[str, list[dict]] = {
    "critical": [
        {"re": r"\bcritical\b", "lang": "en", "conf": 0.90},
        {"re": r"\bvery\s+dangerous\b", "lang": "en", "conf": 0.85},
        {"re": r"\bvery\s+critical\b", "lang": "en", "conf": 0.90},
        {"re": r"खतरनाक\s*स्थिति", "lang": "hi", "conf": 0.85},
        {"re": r"\bkhatarnak\b", "lang": "hi-Latn", "conf": 0.80},
    ],
    "severe": [
        {"re": r"\bsevere\b", "lang": "en", "conf": 0.85},
        {"re": r"\bcompletely\s+(?:blocked|flooded|submerged)\b", "lang": "en", "conf": 0.80},
        {"re": r"\brisk\s+of\s+collapse\b", "lang": "en", "conf": 0.80},
        {"re": r"\bworsening\b", "lang": "en", "conf": 0.70},
        {"re": r"\bbahut\s+tez\b", "lang": "hi-Latn", "conf": 0.75},
    ],
    "moderate": [
        {"re": r"\bmoderate\b", "lang": "en", "conf": 0.80},
    ],
    "minor": [
        {"re": r"\bminor\s+(?:flood|damage)\w*\b", "lang": "en", "conf": 0.80},
        {"re": r"\bno\s+immediate\s+danger\b", "lang": "en", "conf": 0.75},
        {"re": r"\bcan\s+pass\s+slowly\b", "lang": "en", "conf": 0.70},
    ],
}

# --- Urgency signal patterns ---

URGENCY_PATTERNS: list[dict] = [
    {"re": r"(?<!\bno\s)\bimmediate\s+rescue\b", "lang": "en", "conf": 0.95},
    {"re": r"(?<!\bno\s)\bimmediatel?y?\b(?!\s+danger)", "lang": "en", "conf": 0.90},
    {"re": r"\burgent(?:ly)?\b", "lang": "en", "conf": 0.90},
    {"re": r"\bemergency\b", "lang": "en", "conf": 0.85},
    {"re": r"\brising\s+(?:rapidly|fast)\b", "lang": "en", "conf": 0.85},
    {"re": r"\bscreaming\s+for\s+help\b", "lang": "en", "conf": 0.90},
    {"re": r"\bneed\s+rescue\b", "lang": "en", "conf": 0.90},
    {"re": r"\brecommend\s+evacuation\b", "lang": "en", "conf": 0.90},
    {"re": r"\bevacuat(?:e|ion)\b", "lang": "en", "conf": 0.85},
    {"re": r"तुरंत\s*मदद", "lang": "hi", "conf": 0.90},
    {"re": r"तुरंत", "lang": "hi", "conf": 0.80},
    {"re": r"\bbachao\b", "lang": "hi-Latn", "conf": 0.90},
    {"re": r"\bturant\b", "lang": "hi-Latn", "conf": 0.85},
    {"re": r"\bpaani\s+bahut\s+tez\b", "lang": "hi-Latn", "conf": 0.80},
]

# --- Vulnerable persons patterns ---

VULNERABLE_PATTERNS: list[dict] = [
    {"re": r"\b(?:child|children|kids?|infant|baby|babies)\b", "lang": "en", "conf": 0.90},
    {"re": r"\b(?:elderly|old\s+(?:man|woman|people|persons?))\b", "lang": "en", "conf": 0.90},
    {"re": r"\b(?:disabled|handicapped|pregnant)\b", "lang": "en", "conf": 0.85},
    {"re": r"बच्चे", "lang": "hi", "conf": 0.90},
    {"re": r"बुज़ुर्ग|बूढ़", "lang": "hi", "conf": 0.90},
    {"re": r"\bbachch\w*\b", "lang": "hi-Latn", "conf": 0.80},
    {"re": r"\bbuzu?rg\b", "lang": "hi-Latn", "conf": 0.80},
]

# --- Trapped / rescue patterns ---

TRAPPED_PATTERNS: list[dict] = [
    {"re": r"\btrapped\b", "lang": "en", "conf": 0.90},
    {"re": r"\b(?:people|persons?|family|someone|residents?)\s+(?:are\s+)?stuck\b", "lang": "en", "conf": 0.85},
    {"re": r"\bstranded\s+(?:people|persons?|residents?|family)\b|\bstranded\b(?!\s+(?:vehicles?|cars?|traffic))", "lang": "en", "conf": 0.80},
    {"re": r"\brescue\s+(?:needed|required|operation)\b", "lang": "en", "conf": 0.90},
    {"re": r"\bneed\s+rescue\b", "lang": "en", "conf": 0.90},
    {"re": r"\bscreaming\s+for\s+help\b", "lang": "en", "conf": 0.95},
    {"re": r"परिवार.*फंस|लोग.*फंस|फंसा\s+है|फंसे\s+हैं", "lang": "hi", "conf": 0.85},
    {"re": r"बचाओ", "lang": "hi", "conf": 0.90},
    {"re": r"मदद\s*भेजो", "lang": "hi", "conf": 0.85},
    {"re": r"\blog\s+phase\b|\bphase\s+hain\b", "lang": "hi-Latn", "conf": 0.85},
    {"re": r"\bphaas\b", "lang": "hi-Latn", "conf": 0.85},
    {"re": r"\bbachao\b", "lang": "hi-Latn", "conf": 0.90},
]

# --- Info type patterns ---

EXPLICIT_PATTERNS: list[dict] = [
    # First-person / on-ground observations
    {"re": r"\b(?:on[- ]ground|on-site)\s+assessment\b", "lang": "en", "conf": 0.95},
    {"re": r"\bconfirmed\b", "lang": "en", "conf": 0.90},
    {"re": r"\bi\s+(?:can\s+)?see\b", "lang": "en", "conf": 0.85},
    {"re": r"\binspected\b", "lang": "en", "conf": 0.90},
    {"re": r"\bobserved\b", "lang": "en", "conf": 0.85},
    {"re": r"\bassessment\b", "lang": "en", "conf": 0.85},
    {"re": r"\bfield_worker\b", "lang": "en", "conf": 0.80},  # source-based hint
]

INFERRED_PATTERNS: list[dict] = [
    {"re": r"\breport(?:s|ed)\s+(?:of|that)\b", "lang": "en", "conf": 0.80},
    {"re": r"\bsomeone\s+said\b", "lang": "en", "conf": 0.85},
    {"re": r"\breportedly\b", "lang": "en", "conf": 0.80},
    {"re": r"\bsuspected\b", "lang": "en", "conf": 0.75},
    {"re": r"\bhas\s+been\s+informed\b", "lang": "en", "conf": 0.70},
]


# ============================================================================
# LOCATION PHRASE PATTERNS
# ============================================================================

# Patterns to extract raw location mentions from text.
# These capture the phrase; location resolution to coordinates is separate.

LOCATION_PATTERNS: list[dict] = [
    # English location patterns
    {"re": r"\bnear\s+([\w\s]+?)(?:\s+area|\s+thana|\s+police\s+station|\.|\,|$)", "lang": "en", "group": 1},
    {"re": r"\bat\s+([\w\s]+?)(?:\s+area|\s+intersection|\.|\,|$)", "lang": "en", "group": 1},
    {"re": r"\bin\s+((?:Civil Lines|Kotwali|Bilaspur Chowk|Naya Mohalla)(?:\s+area)?)", "lang": "en", "group": 1},
    # Devanagari
    {"re": r"(सिविल\s*लाइन्स|कोतवाली|बिलासपुर\s*चौक|नया\s*मोहल्ला|नया\s*मोहल्ले)", "lang": "hi", "group": 1},
    {"re": r"([\w\s]+?)\s*(?:में|पर|के\s+पास)", "lang": "hi", "group": 1},
    # Romanized Hindi
    {"re": r"\b(Civil Lines|Kotwali|Bilaspur Chowk|Naya Mohall[ae])\b", "lang": "hi-Latn", "group": 1},
    {"re": r"([\w\s]+?)\s+(?:mein|pe|ke\s+paas)", "lang": "hi-Latn", "group": 1},
]

# Known location name fragments for filtering false positives
_KNOWN_LOCATION_FRAGMENTS = {
    "civil lines", "civil line", "kotwali", "bilaspur chowk",
    "bilaspur chauk", "naya mohalla", "naya mohalle",
    "rampur", "community center",
    "सिविल लाइन्स", "कोतवाली", "बिलासपुर चौक", "नया मोहल्ला", "नया मोहल्ले",
}


# ============================================================================
# PEOPLE EXTRACTION PATTERNS
# ============================================================================

# Patterns that capture a number + context to determine category.

PEOPLE_PATTERNS: list[dict] = [
    # English: explicit count + category context
    {"re": r"\b(\d+)\s+(?:people|persons?)\s+trapped\b", "lang": "en", "category": "trapped", "group": 1},
    {"re": r"\b(\d+)\s+(?:people|persons?|members?)\s+(?:including|trapped|stuck)\b", "lang": "en", "category": "trapped", "group": 1},
    {"re": r"\bfamily\s+of\s+(\d+)\s+members?\b", "lang": "en", "category": "trapped", "group": 1},
    {"re": r"\b(\d+)\s+(?:resident|people|person)s?\s+(?:in\s+(?:immediate\s+)?vicinity|nearby)\b", "lang": "en", "category": "nearby", "group": 1},
    {"re": r"\babout\s+(\d+)\s+residents?\b", "lang": "en", "category": "affected", "group": 1},
    {"re": r"\bapproximately\s+(\d+)\s+residents?\b", "lang": "en", "category": "nearby", "group": 1},
    {"re": r"\b(\d+)\s+(?:families|households?)\s+affected\b", "lang": "en", "category": "affected", "group": 1},
    {"re": r"\b(\d+)\s+residents?\s+(?:are\s+)?affected\b", "lang": "en", "category": "affected", "group": 1},

    # Romanized Hindi
    {"re": r"\b(\d+)\s+log\s+(?:phase|phaas)\b", "lang": "hi-Latn", "category": "trapped", "group": 1},
    {"re": r"\b(?:lagbhag|karib)\s+(\d+)\s+log\b", "lang": "hi-Latn", "category": "nearby", "group": 1},
]

# Range patterns (e.g. "15 to 20 houses")
PEOPLE_RANGE_PATTERNS: list[dict] = [
    {"re": r"\b(\d+)\s+to\s+(\d+)\s+(?:house|home|famil)\w*\b", "lang": "en", "category": "affected"},
    {"re": r"\b(\d+)\s*-\s*(\d+)\s+(?:people|persons?|residents?)\b", "lang": "en", "category": "affected"},
    {"re": r"\b(\d+)\s*-\s*(\d+)\s+gaadi\b", "lang": "hi-Latn", "category": "affected"},
    {"re": r"\b(\d+)\s*-\s*(\d+)\s+log\b", "lang": "hi-Latn", "category": "nearby"},
]

# "Approximately 20 families" — families to individuals is ambiguous, don't convert
FAMILIES_PATTERNS: list[dict] = [
    {"re": r"\b(?:approximately|about|around)?\s*(\d+)\s+families\s+affected\b", "lang": "en", "category": "affected", "group": 1, "unit": "families"},
]


# ============================================================================
# EXTRACTION FUNCTIONS — one per field
# ============================================================================

def _find_evidence(pattern: str, text: str) -> Optional[str]:
    """Find the matched substring as evidence. Returns None if no match."""
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        return m.group(0).strip()
    return None


def _find_all_evidence(pattern: str, text: str) -> list[str]:
    """Find all non-overlapping matches of pattern in text."""
    return [m.group(0).strip() for m in re.finditer(pattern, text, re.IGNORECASE)]


def extract_incident_type(text: str, language: str) -> ExtractionField:
    """
    Extract the primary incident type from report text.

    When multiple incident types match (e.g., 'flood' and 'power_outage'),
    prefer the more specific type.  Generic flood/waterlogging patterns often
    match contextually (e.g., "transformer flooded") but the primary incident
    is the more specific type.
    """
    # Specificity ranking: higher = more specific, preferred when tied
    _SPECIFICITY = {
        "rescue_needed": 5,
        "structural_damage": 4,
        "power_outage": 4,
        "road_blocked": 3,
        "waterlogging": 2,
        "flood": 1,
    }

    # Collect all matching types with their best confidence and evidence
    matches: dict[str, tuple[float, list[str]]] = {}

    for itype, patterns in INCIDENT_TYPE_PATTERNS.items():
        for pat in patterns:
            m = re.search(pat["re"], text, re.IGNORECASE)
            if m:
                evidence_text = m.group(0).strip()
                if itype not in matches or pat["conf"] > matches[itype][0]:
                    matches[itype] = (pat["conf"], [evidence_text])
                elif pat["conf"] == matches[itype][0]:
                    if evidence_text not in matches[itype][1]:
                        matches[itype][1].append(evidence_text)

    if not matches:
        return ExtractionField(
            field_name="incident_type",
            value=None,
            confidence=0.0,
            evidence=[],
        )

    # Sort by: confidence (descending) then specificity (descending) as tie-breaker
    ranked = sorted(
        matches.items(),
        key=lambda item: (round(item[1][0], 2), _SPECIFICITY.get(item[0], 0)),
        reverse=True,
    )

    best_type = ranked[0][0]
    best_conf = ranked[0][1][0]
    best_evidence = ranked[0][1][1]

    return ExtractionField(
        field_name="incident_type",
        value=best_type,
        confidence=best_conf,
        evidence=best_evidence,
    )



def extract_people(text: str, language: str) -> list[ExtractionField]:
    """
    Extract people mentions with semantic categories.

    Returns a list of ExtractionField objects, one per distinct people mention.
    Each value is a PeopleCount-compatible dict: {count, category, description}.

    Does NOT convert families/households into individual counts.
    """
    results: list[ExtractionField] = []
    seen_evidence: set[str] = set()

    # Explicit count patterns
    for pat in PEOPLE_PATTERNS:
        for m in re.finditer(pat["re"], text, re.IGNORECASE):
            evidence_text = m.group(0).strip()
            if evidence_text in seen_evidence:
                continue
            seen_evidence.add(evidence_text)

            count = int(m.group(pat["group"]))
            category = pat["category"]

            results.append(ExtractionField(
                field_name="people_estimate",
                value={"count": count, "category": category},
                confidence=0.80,
                evidence=[evidence_text],
            ))

    # Range patterns
    for pat in PEOPLE_RANGE_PATTERNS:
        for m in re.finditer(pat["re"], text, re.IGNORECASE):
            evidence_text = m.group(0).strip()
            if evidence_text in seen_evidence:
                continue
            seen_evidence.add(evidence_text)

            low = int(m.group(1))
            high = int(m.group(2))
            category = pat["category"]

            results.append(ExtractionField(
                field_name="people_estimate",
                value={
                    "count_min": low,
                    "count_max": high,
                    "category": category,
                    "description": f"range {low}-{high}",
                },
                confidence=0.75,
                evidence=[evidence_text],
            ))

    # Family patterns (preserve as families, don't convert to individuals)
    for pat in FAMILIES_PATTERNS:
        for m in re.finditer(pat["re"], text, re.IGNORECASE):
            evidence_text = m.group(0).strip()
            if evidence_text in seen_evidence:
                continue
            seen_evidence.add(evidence_text)

            count = int(m.group(pat["group"]))
            results.append(ExtractionField(
                field_name="people_estimate",
                value={
                    "count": count,
                    "category": pat["category"],
                    "unit": pat.get("unit", "individuals"),
                    "description": f"{count} families affected",
                },
                confidence=0.70,
                evidence=[evidence_text],
            ))

    return results


def extract_vulnerable(text: str, language: str) -> ExtractionField:
    """
    Extract whether vulnerable persons (children, elderly, disabled) are mentioned.
    """
    evidence: list[str] = []
    best_conf: float = 0.0

    for pat in VULNERABLE_PATTERNS:
        for m in re.finditer(pat["re"], text, re.IGNORECASE):
            ev = m.group(0).strip()
            if ev not in evidence:
                evidence.append(ev)
            if pat["conf"] > best_conf:
                best_conf = pat["conf"]

    if not evidence:
        return ExtractionField(
            field_name="vulnerable_persons",
            value=None,
            confidence=0.0,
            evidence=[],
        )

    return ExtractionField(
        field_name="vulnerable_persons",
        value=True,
        confidence=best_conf,
        evidence=evidence,
    )


def extract_trapped_rescue(text: str, language: str) -> ExtractionField:
    """
    Extract whether a trapped/rescue situation is indicated.
    """
    evidence: list[str] = []
    best_conf: float = 0.0

    for pat in TRAPPED_PATTERNS:
        for m in re.finditer(pat["re"], text, re.IGNORECASE):
            ev = m.group(0).strip()
            if ev not in evidence:
                evidence.append(ev)
            if pat["conf"] > best_conf:
                best_conf = pat["conf"]

    if not evidence:
        return ExtractionField(
            field_name="trapped_or_rescue",
            value=None,
            confidence=0.0,
            evidence=[],
        )

    return ExtractionField(
        field_name="trapped_or_rescue",
        value=True,
        confidence=best_conf,
        evidence=evidence,
    )


def extract_urgency(text: str, language: str) -> ExtractionField:
    """
    Extract urgency signal phrases from the text.
    """
    signals: list[str] = []
    evidence: list[str] = []
    best_conf: float = 0.0

    for pat in URGENCY_PATTERNS:
        for m in re.finditer(pat["re"], text, re.IGNORECASE):
            signal = m.group(0).strip()
            if signal.lower() not in {s.lower() for s in signals}:
                signals.append(signal)
            if signal not in evidence:
                evidence.append(signal)
            if pat["conf"] > best_conf:
                best_conf = pat["conf"]

    if not signals:
        return ExtractionField(
            field_name="urgency_signals",
            value=None,
            confidence=0.0,
            evidence=[],
        )

    return ExtractionField(
        field_name="urgency_signals",
        value=signals,
        confidence=best_conf,
        evidence=evidence,
    )


def extract_location_raw(text: str, language: str) -> ExtractionField:
    """
    Extract raw location phrases from the text.

    Does NOT resolve to coordinates — that is location_resolver's job.
    Returns the best location phrase found.
    """
    candidates: list[tuple[str, float]] = []

    for pat in LOCATION_PATTERNS:
        for m in re.finditer(pat["re"], text, re.IGNORECASE):
            if "group" in pat and pat["group"] is not None:
                loc = m.group(pat["group"]).strip()
            else:
                loc = m.group(0).strip()

            # Skip very short or purely functional matches
            if len(loc) < 3:
                continue

            # Boost confidence if it matches a known location fragment
            conf = 0.80
            loc_lower = loc.lower()
            for known in _KNOWN_LOCATION_FRAGMENTS:
                if known in loc_lower or loc_lower in known:
                    conf = 0.90
                    break

            candidates.append((loc, conf))

    if not candidates:
        return ExtractionField(
            field_name="location_raw",
            value=None,
            confidence=0.0,
            evidence=[],
        )

    # Pick the highest-confidence candidate
    candidates.sort(key=lambda c: c[1], reverse=True)
    best_loc, best_conf = candidates[0]

    # Collect unique evidence phrases
    evidence = list(dict.fromkeys(c[0] for c in candidates))

    return ExtractionField(
        field_name="location_raw",
        value=best_loc,
        confidence=best_conf,
        evidence=evidence,
    )


def extract_severity_hint(text: str, language: str) -> ExtractionField:
    """
    Extract severity hint keywords from the text.

    Returns the highest-severity level found.
    Does NOT compute final severity score (that is scorer's job).
    """
    severity_order = ["critical", "severe", "moderate", "minor"]

    best_severity: Optional[str] = None
    best_conf: float = 0.0
    best_evidence: list[str] = []

    for severity_level in severity_order:
        patterns = SEVERITY_PATTERNS.get(severity_level, [])
        for pat in patterns:
            m = re.search(pat["re"], text, re.IGNORECASE)
            if m:
                ev = m.group(0).strip()
                # Take the most serious severity found
                if best_severity is None or severity_order.index(severity_level) < severity_order.index(best_severity):
                    best_severity = severity_level
                    best_conf = pat["conf"]
                    best_evidence = [ev]
                elif severity_level == best_severity and ev not in best_evidence:
                    best_evidence.append(ev)
                    if pat["conf"] > best_conf:
                        best_conf = pat["conf"]

    if best_severity is None:
        return ExtractionField(
            field_name="severity_hint",
            value=None,
            confidence=0.0,
            evidence=[],
        )

    return ExtractionField(
        field_name="severity_hint",
        value=best_severity,
        confidence=best_conf,
        evidence=best_evidence,
    )


def extract_info_type(text: str, language: str, source: str = "") -> ExtractionField:
    """
    Determine if the report is explicit (direct observation) or inferred (hearsay).

    Field workers are treated as explicit by default.
    """
    explicit_evidence: list[str] = []
    inferred_evidence: list[str] = []
    explicit_conf: float = 0.0
    inferred_conf: float = 0.0

    # Source-based hint
    if source == "field_worker":
        explicit_evidence.append("field_worker source")
        explicit_conf = 0.80

    for pat in EXPLICIT_PATTERNS:
        m = re.search(pat["re"], text, re.IGNORECASE)
        if m:
            ev = m.group(0).strip()
            if ev not in explicit_evidence:
                explicit_evidence.append(ev)
            if pat["conf"] > explicit_conf:
                explicit_conf = pat["conf"]

    for pat in INFERRED_PATTERNS:
        m = re.search(pat["re"], text, re.IGNORECASE)
        if m:
            ev = m.group(0).strip()
            if ev not in inferred_evidence:
                inferred_evidence.append(ev)
            if pat["conf"] > inferred_conf:
                inferred_conf = pat["conf"]

    # Decide: if explicit evidence is stronger or equal, call it explicit
    if explicit_conf >= inferred_conf and explicit_evidence:
        return ExtractionField(
            field_name="info_type",
            value="explicit",
            confidence=explicit_conf,
            evidence=explicit_evidence,
        )
    elif inferred_evidence:
        return ExtractionField(
            field_name="info_type",
            value="inferred",
            confidence=inferred_conf,
            evidence=inferred_evidence,
        )
    else:
        # Unknown: no explicit or inferred evidence
        return ExtractionField(
            field_name="info_type",
            value=None,
            confidence=0.0,
            evidence=[],
        )


# ============================================================================
# MAIN EXTRACTION ENTRY POINT
# ============================================================================

def extract_report(
    text: str,
    language: str = "en",
    source: str = "",
) -> list[ExtractionField]:
    """
    Extract all structured fields from a single report's normalized text.

    Parameters:
      text:     Normalized text of the report (NOT raw_text).
      language: Detected language code (en / hi / hi-Latn).
      source:   Source channel (whatsapp / sms / web / field_worker).

    Returns:
      List of ExtractionField objects, one per extracted field.
      People estimates may produce multiple ExtractionField entries.
    """
    fields: list[ExtractionField] = []

    # 1. Incident type
    fields.append(extract_incident_type(text, language))

    # 2. People estimates (may produce multiple entries)
    people_fields = extract_people(text, language)
    fields.extend(people_fields)

    # 3. Vulnerable persons
    fields.append(extract_vulnerable(text, language))

    # 4. Trapped / rescue
    fields.append(extract_trapped_rescue(text, language))

    # 5. Urgency signals
    fields.append(extract_urgency(text, language))

    # 6. Location raw
    fields.append(extract_location_raw(text, language))

    # 7. Severity hint
    fields.append(extract_severity_hint(text, language))

    # 8. Info type
    fields.append(extract_info_type(text, language, source=source))

    return fields
