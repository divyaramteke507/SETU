/**
 * SETU Presentation Formatters — Phase 8 Final Polish
 * Pure display formatting utilities for responder clarity.
 * NEVER recalculates backend scores or algorithmic values.
 */

export const ZONE_METADATA = {
  civil_lines: {
    id: 'civil_lines',
    name: 'Civil Lines',
    shortName: 'Civil Lines',
    color: '#3b82f6',
    cx: 400,
    cy: 200,
    lat: 28.7950,
    lon: 79.0250,
    description: 'Central commercial & residential district'
  },
  kotwali: {
    id: 'kotwali',
    name: 'Kotwali',
    shortName: 'Kotwali',
    color: '#ef4444',
    cx: 220,
    cy: 130,
    lat: 28.8010,
    lon: 79.0180,
    description: 'North-west administrative & police district'
  },
  bilaspur_chowk: {
    id: 'bilaspur_chowk',
    name: 'Bilaspur Chowk',
    shortName: 'Bilaspur',
    color: '#f59e0b',
    cx: 580,
    cy: 440,
    lat: 28.7890,
    lon: 79.0320,
    description: 'South-east transit intersection & highway junction'
  },
  naya_mohalla: {
    id: 'naya_mohalla',
    name: 'Naya Mohalla',
    shortName: 'Naya Mohalla',
    color: '#8b5cf6',
    cx: 160,
    cy: 400,
    lat: 28.7930,
    lon: 79.0100,
    description: 'Western residential sector'
  },
  gps_outskirts: {
    id: 'gps_outskirts',
    name: 'GPS / Outskirts',
    shortName: 'GPS Outskirts',
    color: '#06b6d4',
    cx: 690,
    cy: 230,
    lat: 28.7940,
    lon: 79.0200,
    description: 'Direct GPS coordinates outside gazetteer zones'
  }
}

/**
 * Deterministically maps an incident to one of the 4 response zones or GPS/outskirts.
 */
export function getIncidentZone(incident) {
  if (!incident) return null
  const loc = (incident.location_resolved || '').toLowerCase()

  // GPS-derived locations outside named gazetteer
  if (loc.startsWith('gps') || (!loc && incident.location_lat && incident.location_lon)) {
    return 'gps_outskirts'
  }

  if (loc.includes('civil')) return 'civil_lines'
  if (loc.includes('kotwali')) return 'kotwali'
  if (loc.includes('bilaspur')) return 'bilaspur_chowk'
  if (loc.includes('naya')) return 'naya_mohalla'

  return 'gps_outskirts'
}

/**
 * Clean human-readable zone display name.
 */
export function formatZoneName(rawLocation) {
  if (!rawLocation) return 'Rampur (Unresolved)'
  const lower = rawLocation.toLowerCase()
  if (lower === 'civil_lines' || lower.includes('civil')) return 'Civil Lines'
  if (lower === 'kotwali') return 'Kotwali'
  if (lower === 'bilaspur_chowk' || lower.includes('bilaspur')) return 'Bilaspur Chowk'
  if (lower === 'naya_mohalla' || lower.includes('naya')) return 'Naya Mohalla'
  if (lower.startsWith('gps')) return rawLocation
  return rawLocation.replace(/_/g, ' ')
}

/**
 * Formats location for operational clarity:
 * - Suppresses misleading "(conf: 0%)"
 * - Labels GPS-derived as "GPS VERIFIED"
 * - Labels Gazetteer-derived as "LOCATION RESOLVED"
 */
export function formatLocationPresentation(incident) {
  if (!incident) {
    return { name: 'Unresolved', badgeText: null, badgeClass: null, coordsText: null }
  }

  const rawLoc = incident.location_resolved || ''
  const isGps = rawLoc.toLowerCase().includes('gps') || (!rawLoc && incident.location_lat && incident.location_lon)

  let name = formatZoneName(rawLoc)
  if (isGps && (!name || name.toLowerCase().includes('gps'))) {
    name = 'Rampur Sector (GPS)'
  }

  const badgeText = isGps ? 'GPS VERIFIED' : 'LOCATION RESOLVED'
  const badgeClass = isGps ? 'badge-geo-gps' : 'badge-geo-gazetteer'

  let coordsText = null
  if (incident.location_lat && incident.location_lon) {
    coordsText = `${incident.location_lat.toFixed(4)}°N, ${incident.location_lon.toFixed(4)}°E`
  }

  return { name, badgeText, badgeClass, coordsText }
}

/**
 * Human-friendly field labels for evidence tags.
 */
const FIELD_LABELS = {
  incident_type: 'Incident Type',
  location_raw: 'Reported Location',
  vulnerable_persons: 'Vulnerable Persons',
  trapped_or_rescue: 'Rescue Needed',
  urgency_signals: 'Urgency Signals',
  severity_hint: 'Reported Severity',
  info_type: 'Information Type',
  people_estimate: 'Impact Estimate',
  infrastructure: 'Infrastructure',
  hazards: 'Hazards'
}

/**
 * Clean Extraction Display:
 * - Suppresses null, undefined, empty strings, "null", "None"
 * - Suppresses empty arrays and empty objects
 * - Converts structured values into human-readable responder phrases:
 *   {"count": 20, "category": "affected", "unit": "families"} -> "20 families affected"
 *   {"count": 5, "category": "trapped"} -> "5 people trapped"
 *   ["rising rapidly"] -> "rising rapidly"
 * - Suppresses false flags (e.g. vulnerable_persons: false) to prevent clutter
 */
export function formatExtraction(fieldName, rawValue) {
  if (rawValue === null || rawValue === undefined) return null

  let val = rawValue
  if (typeof val === 'string') {
    const trimmed = val.trim()
    if (trimmed === '' || trimmed.toLowerCase() === 'null' || trimmed.toLowerCase() === 'none') {
      return null
    }
    if ((trimmed.startsWith('{') && trimmed.endsWith('}')) || (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
      try {
        val = JSON.parse(trimmed)
      } catch {
        // preserve as string
      }
    }
  }

  if (val === null || val === undefined) return null

  // Arrays: suppress if empty, format if populated
  if (Array.isArray(val)) {
    const cleanItems = val.map(v => typeof v === 'string' ? v.trim() : v).filter(Boolean)
    if (cleanItems.length === 0) return null
    return {
      label: FIELD_LABELS[fieldName] || fieldName.replace(/_/g, ' '),
      displayValue: cleanItems.join(', ')
    }
  }

  // Objects: parse structured representations into responder language
  if (typeof val === 'object') {
    const keys = Object.keys(val)
    if (keys.length === 0) return null

    // Prefer explicit description if present (e.g. "20 families affected", "range 15-20")
    if (val.description && typeof val.description === 'string' && val.description.trim()) {
      return {
        label: FIELD_LABELS[fieldName] || 'Impact Estimate',
        displayValue: val.description
      }
    }

    // Structured count estimates
    if ('count' in val) {
      const count = val.count
      const unit = val.unit || ''
      const cat = val.category || ''
      let text = ''
      if (unit && unit.toLowerCase() === 'families') {
        text = `${count} families ${cat || 'affected'}`.trim()
      } else if (unit) {
        text = `${count} ${unit} ${cat}`.trim()
      } else if (cat) {
        text = `${count} people ${cat}`.trim()
      } else {
        text = `${count} affected`
      }
      return {
        label: FIELD_LABELS[fieldName] || 'Impact Estimate',
        displayValue: text
      }
    }

    // Range estimates
    if ('count_min' in val && 'count_max' in val) {
      const cat = val.category || 'affected'
      return {
        label: FIELD_LABELS[fieldName] || 'Impact Estimate',
        displayValue: `${val.count_min}–${val.count_max} people ${cat}`
      }
    }

    // Filter out null/empty inside object
    const nonNullEntries = Object.entries(val).filter(([, v]) => v !== null && v !== undefined && v !== '')
    if (nonNullEntries.length === 0) return null
    return {
      label: FIELD_LABELS[fieldName] || fieldName.replace(/_/g, ' '),
      displayValue: nonNullEntries.map(([k, v]) => `${k}: ${v}`).join(', ')
    }
  }

  // Booleans: show "Yes" for true, suppress false
  if (typeof val === 'boolean') {
    if (!val) return null
    return {
      label: FIELD_LABELS[fieldName] || fieldName.replace(/_/g, ' '),
      displayValue: 'Yes'
    }
  }

  // Strings / Numbers
  const str = String(val).trim()
  if (str === '' || str.toLowerCase() === 'null' || str.toLowerCase() === 'none') {
    return null
  }

  let formatted = str
  if (fieldName === 'incident_type') {
    formatted = str.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())
  }

  return {
    label: FIELD_LABELS[fieldName] || fieldName.replace(/_/g, ' '),
    displayValue: formatted
  }
}
