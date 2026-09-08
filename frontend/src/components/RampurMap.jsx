import React from 'react'
import { ZONE_METADATA, getIncidentZone } from '../utils/formatters'

/**
 * Rampur Offline Geographic Response Zone Map — Phase 9.1 Refined
 * Restrained, elegant SVG tactical visualizer showing:
 * 4 Named Response Zones + GPS/Outskirts and incident markers.
 * Clean, minimal markers with subtle selected-marker emphasis.
 * No excessive pulsing, glowing, or distracting decorative graphics.
 */
export default function RampurMap({
  incidents = [],
  selectedIncidentId = null,
  onSelectIncident = () => {},
  selectedZone = 'all',
  onSelectZone = () => {},
  expanded = false
}) {
  // Group incidents by zone
  const zoneBuckets = {
    civil_lines: [],
    kotwali: [],
    bilaspur_chowk: [],
    naya_mohalla: [],
    gps_outskirts: []
  }

  incidents.forEach((inc) => {
    const zoneKey = getIncidentZone(inc)
    if (zoneKey && zoneBuckets[zoneKey]) {
      zoneBuckets[zoneKey].push(inc)
    } else {
      zoneBuckets.gps_outskirts.push(inc)
    }
  })

  // Restrained semantic colors by urgency
  const urgencyColors = {
    critical: '#ef4444',
    high: '#f97316',
    medium: '#eab308',
    low: '#38bdf8'
  }

  /**
   * Calculates clean, non-overlapping coordinates for each incident marker
   * distributed around the zone anchor.
   */
  const renderIncidentMarkers = (zoneKey, zoneConfig) => {
    const zoneIncidents = zoneBuckets[zoneKey] || []
    if (zoneIncidents.length === 0) return null

    const cx = zoneConfig.cx
    const cy = zoneConfig.cy + 12
    const count = zoneIncidents.length
    const radius = count > 1 ? (zoneKey === 'gps_outskirts' ? 22 : 26) : 0

    return zoneIncidents.map((inc, idx) => {
      let x = cx
      let y = cy

      if (count > 1) {
        const angle = (2 * Math.PI * idx) / count - Math.PI / 2
        x = cx + Math.round(radius * Math.cos(angle))
        y = cy + Math.round(radius * Math.sin(angle))
      }

      const isSelected = inc.id === selectedIncidentId
      const fillColor = urgencyColors[inc.urgency] || '#94a3b8'
      const shortId = inc.id.replace('INC-', '').replace(/^0+/, '') || inc.id

      return (
        <g
          key={inc.id}
          className={`map-marker ${isSelected ? 'selected' : ''}`}
          onClick={(e) => {
            e.stopPropagation()
            onSelectIncident(inc.id)
          }}
          style={{ cursor: 'pointer' }}
        >
          <title>{`${inc.id}: ${inc.title} [${(inc.urgency || '').toUpperCase()}] Priority: ${Math.round(inc.priority || 0)}`}</title>

          {/* Subtle selected marker outer ring (clean, quiet, no strobe) */}
          {isSelected && (
            <circle
              cx={x}
              cy={y}
              r={13}
              fill="none"
              stroke="#ffffff"
              strokeWidth={1.5}
              opacity={0.9}
            />
          )}

          {/* Incident marker circle */}
          <circle
            cx={x}
            cy={y}
            r={8.5}
            fill={fillColor}
            stroke="#090b10"
            strokeWidth={1.5}
          />

          {/* Short incident ID label */}
          <text
            x={x}
            y={y + 3}
            fontFamily="ui-monospace, monospace"
            fontSize={7.5}
            fontWeight="bold"
            fill="#ffffff"
            textAnchor="middle"
            pointerEvents="none"
          >
            {shortId}
          </text>
        </g>
      )
    })
  }

  return (
    <div className={`rampur-map-wrapper ${expanded ? 'expanded' : ''}`}>
      <svg
        viewBox="0 0 800 580"
        className="rampur-map-svg"
        role="img"
        aria-label="4 Named Response Zones + GPS/Outskirts"
      >
        {/* Deep graphite canvas background */}
        <rect width="800" height="580" fill="#090c12" rx="8" />

        {/* Quiet Top Title Bar */}
        <rect x="0" y="0" width="800" height="32" fill="#0c1017" rx="8" />
        <rect x="0" y="24" width="800" height="8" fill="#0c1017" />
        <text
          x="18"
          y="21"
          fontFamily="system-ui, -apple-system, sans-serif"
          fontSize="11"
          fill="#94a3b8"
          fontWeight="600"
          letterSpacing="0.04em"
        >
          4 NAMED RESPONSE ZONES + GPS/OUTSKIRTS
        </text>
        <text
          x="782"
          y="21"
          fontFamily="system-ui, -apple-system, sans-serif"
          fontSize="9.5"
          fill="#475569"
          textAnchor="end"
          fontWeight="500"
          letterSpacing="0.02em"
        >
          OFFLINE STATIC GIS · RAMPUR
        </text>

        {/* Minimal grid markings */}
        <g stroke="rgba(255, 255, 255, 0.03)" strokeWidth="0.5">
          <line x1="0" y1="130" x2="800" y2="130" />
          <line x1="0" y1="230" x2="800" y2="230" />
          <line x1="0" y1="330" x2="800" y2="330" />
          <line x1="0" y1="430" x2="800" y2="430" />
          <line x1="0" y1="530" x2="800" y2="530" />
          <line x1="160" y1="32" x2="160" y2="580" />
          <line x1="320" y1="32" x2="320" y2="580" />
          <line x1="480" y1="32" x2="480" y2="580" />
          <line x1="640" y1="32" x2="640" y2="580" />
        </g>

        {/* River (Kosi Nala) — Restrained & Understated */}
        <path
          d="M0 380 Q120 340, 200 360 Q320 400, 440 350 Q560 300, 680 330 Q740 345, 800 320"
          stroke="#101d2d"
          strokeWidth="16"
          fill="none"
        />
        <path
          d="M0 380 Q120 340, 200 360 Q320 400, 440 350 Q560 300, 680 330 Q740 345, 800 320"
          stroke="#0369a1"
          strokeWidth="2.5"
          fill="none"
          opacity="0.35"
        />
        <text
          x="650"
          y="312"
          fontFamily="system-ui, -apple-system, sans-serif"
          fontSize="9"
          fill="#38bdf8"
          opacity="0.6"
        >
          Kosi Nala Drain
        </text>

        {/* Branch drainage line */}
        <path
          d="M400 32 Q380 150, 420 250 Q440 320, 440 350"
          stroke="#101d2d"
          strokeWidth="5"
          fill="none"
          opacity="0.7"
        />

        {/* Arterial Highways (NH-24) — Clean Hairline */}
        <g stroke="rgba(255, 255, 255, 0.08)" strokeWidth="1.5" fill="none">
          <line x1="0" y1="240" x2="800" y2="200" />
          <line x1="380" y1="32" x2="400" y2="570" />
          <line x1="100" y1="460" x2="700" y2="440" />
        </g>
        <text x="760" y="194" fontFamily="system-ui, -apple-system, sans-serif" fontSize="8" fill="#475569">
          NH-24
        </text>

        {/* ========================================================= */}
        {/* 4 NAMED RESPONSE ZONES + GPS/OUTSKIRTS */}
        {/* ========================================================= */}
        {Object.entries(ZONE_METADATA).map(([key, zone]) => {
          const isZoneActive = selectedZone === key
          const count = (zoneBuckets[key] || []).length
          const isGpsZone = key === 'gps_outskirts'

          return (
            <g
              key={key}
              className={`zone-group ${isZoneActive ? 'active-zone' : ''}`}
              onClick={() => onSelectZone(key)}
              style={{ cursor: 'pointer' }}
            >
              <title>{`${zone.name}: ${count} active incident(s)`}</title>

              {/* Zone boundary perimeter */}
              {isGpsZone ? (
                <rect
                  x={zone.cx - 56}
                  y={zone.cy - 50}
                  width="112"
                  height="96"
                  rx="6"
                  fill={isZoneActive ? 'rgba(6, 182, 212, 0.08)' : 'rgba(255, 255, 255, 0.015)'}
                  stroke={zone.color}
                  strokeWidth={isZoneActive ? 1.5 : 0.75}
                  strokeDasharray="4,3"
                  opacity={isZoneActive ? 0.9 : 0.45}
                />
              ) : (
                <circle
                  cx={zone.cx}
                  cy={zone.cy}
                  r={zone.id === 'civil_lines' ? 56 : zone.id === 'kotwali' ? 48 : 46}
                  fill={isZoneActive ? 'rgba(59, 130, 246, 0.08)' : 'rgba(255, 255, 255, 0.015)'}
                  stroke={zone.color}
                  strokeWidth={isZoneActive ? 1.75 : 0.85}
                  strokeDasharray={isZoneActive ? 'none' : '4,3'}
                  opacity={isZoneActive ? 0.9 : 0.45}
                />
              )}

              {/* Clean Understated Zone Label Badge */}
              <rect
                x={zone.cx - 50}
                y={zone.cy - 46}
                width="100"
                height="19"
                rx="4"
                fill="#0d1117"
                stroke={isZoneActive ? zone.color : 'rgba(255, 255, 255, 0.12)'}
                strokeWidth={isZoneActive ? 1.25 : 0.6}
              />
              <text
                x={zone.cx}
                y={zone.cy - 33}
                fontFamily="system-ui, -apple-system, sans-serif"
                fontSize="9"
                fill="#e2e8f0"
                fontWeight="600"
                letterSpacing="0.02em"
                textAnchor="middle"
              >
                {zone.shortName.toUpperCase()}
              </text>

              {/* Incident Count Indicator */}
              <rect
                x={zone.cx - 22}
                y={zone.cy - 24}
                width="44"
                height="12"
                rx="2.5"
                fill="#05070a"
                stroke="rgba(255, 255, 255, 0.08)"
                strokeWidth="0.5"
              />
              <text
                x={zone.cx}
                y={zone.cy - 15}
                fontFamily="ui-monospace, monospace"
                fontSize="7.5"
                fill={zone.color}
                fontWeight="bold"
                textAnchor="middle"
              >
                {count} {count === 1 ? 'INCIDENT' : 'INCIDENTS'}
              </text>

              {/* Dynamic incident markers inside this zone */}
              {renderIncidentMarkers(key, zone)}
            </g>
          )
        })}

        {/* ========================================================= */}
        {/* REFINED MINIMAL LEGEND */}
        {/* ========================================================= */}
        <g transform="translate(628, 38)">
          <rect
            x="0"
            y="0"
            width="158"
            height="136"
            rx="6"
            fill="#0c1017"
            stroke="rgba(255, 255, 255, 0.08)"
            strokeWidth="0.8"
          />

          <text
            x="10"
            y="14"
            fontFamily="system-ui, -apple-system, sans-serif"
            fontSize="8"
            fill="#64748b"
            fontWeight="600"
            letterSpacing="0.05em"
          >
            4 NAMED ZONES + GPS
          </text>
          <circle cx="15" cy="26" r="3" fill="#3b82f6" />
          <text x="24" y="29" fontFamily="system-ui, sans-serif" fontSize="7.5" fill="#cbd5e1">Civil Lines</text>

          <circle cx="86" cy="26" r="3" fill="#ef4444" />
          <text x="95" y="29" fontFamily="system-ui, sans-serif" fontSize="7.5" fill="#cbd5e1">Kotwali</text>

          <circle cx="15" cy="39" r="3" fill="#f59e0b" />
          <text x="24" y="42" fontFamily="system-ui, sans-serif" fontSize="7.5" fill="#cbd5e1">Bilaspur</text>

          <circle cx="86" cy="39" r="3" fill="#8b5cf6" />
          <text x="95" y="42" fontFamily="system-ui, sans-serif" fontSize="7.5" fill="#cbd5e1">Naya Mohalla</text>

          <circle cx="15" cy="52" r="3" fill="#06b6d4" />
          <text x="24" y="55" fontFamily="system-ui, sans-serif" fontSize="7.5" fill="#cbd5e1">GPS / Outskirts</text>

          <line x1="8" y1="62" x2="150" y2="62" stroke="rgba(255, 255, 255, 0.06)" strokeWidth="0.6" />

          {/* Urgency Section */}
          <text
            x="10"
            y="74"
            fontFamily="system-ui, -apple-system, sans-serif"
            fontSize="8"
            fill="#64748b"
            fontWeight="600"
            letterSpacing="0.05em"
          >
            URGENCY TIERS
          </text>
          <circle cx="15" cy="87" r="3.5" fill="#ef4444" />
          <text x="24" y="90" fontFamily="system-ui, sans-serif" fontSize="7.5" fill="#cbd5e1">Critical</text>

          <circle cx="86" cy="87" r="3.5" fill="#f97316" />
          <text x="95" y="90" fontFamily="system-ui, sans-serif" fontSize="7.5" fill="#cbd5e1">High</text>

          <circle cx="15" cy="102" r="3.5" fill="#eab308" />
          <text x="24" y="105" fontFamily="system-ui, sans-serif" fontSize="7.5" fill="#cbd5e1">Medium</text>

          <circle cx="86" cy="102" r="3.5" fill="#38bdf8" />
          <text x="95" y="105" fontFamily="system-ui, sans-serif" fontSize="7.5" fill="#cbd5e1">Low</text>

          <text
            x="10"
            y="126"
            fontFamily="ui-monospace, monospace"
            fontSize="6.5"
            fill="#475569"
          >
            Click marker to inspect
          </text>
        </g>

        {/* Scale & North Arrow */}
        <g transform="translate(16, 554)">
          <line x1="0" y1="0" x2="50" y2="0" stroke="#475569" strokeWidth="1" />
          <line x1="0" y1="-2" x2="0" y2="2" stroke="#475569" strokeWidth="1" />
          <line x1="50" y1="-2" x2="50" y2="2" stroke="#475569" strokeWidth="1" />
          <text x="25" y="10" fontFamily="system-ui, sans-serif" fontSize="7" fill="#475569" textAnchor="middle">
            ~1 km
          </text>
        </g>

        <g transform="translate(772, 550)">
          <polygon points="0,-10 -3,0 3,0" fill="#475569" opacity="0.8" />
          <text x="0" y="-12" fontFamily="system-ui, sans-serif" fontSize="7.5" fill="#475569" textAnchor="middle" fontWeight="600">
            N
          </text>
        </g>
      </svg>
    </div>
  )
}
