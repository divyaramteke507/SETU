"""
SETU Scorer Service — Phase 7.

Aliases and re-exports the deterministic Incident Assessor engine.
"""

from services.incident_assessor import (
    assess_incident,
    calculate_confidence,
    calculate_priority,
    calculate_severity,
    determine_urgency_band,
    persist_incident_assessment,
)

__all__ = [
    "assess_incident",
    "calculate_confidence",
    "calculate_priority",
    "calculate_severity",
    "determine_urgency_band",
    "persist_incident_assessment",
]
