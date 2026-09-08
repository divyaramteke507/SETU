import { useState, useEffect, useCallback, useMemo } from 'react'
import './App.css'
import RampurMap from './components/RampurMap'
import {
  ZONE_METADATA,
  getIncidentZone,
  formatZoneName,
  formatLocationPresentation,
  formatExtraction
} from './utils/formatters'

const API_BASE = 'http://127.0.0.1:8000/api'

export default function App() {
  // Primary State
  const [incidents, setIncidents] = useState([])
  const [selectedIncidentId, setSelectedIncidentId] = useState(null)
  const [incidentDetail, setIncidentDetail] = useState(null)
  const [selectedReportIds, setSelectedReportIds] = useState([])

  // Filters & Views
  const [urgencyFilter, setUrgencyFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')
  const [zoneFilter, setZoneFilter] = useState('all')
  const [searchQuery, setSearchQuery] = useState('')

  // View Modes: 'split' | 'focus' | 'map'
  // Auto-detect 1366x768 screens to default to 'focus' so intelligence workspace is never cramped
  const [viewMode, setViewMode] = useState(() => {
    return window.innerWidth < 1440 ? 'focus' : 'split'
  })

  // Loading & Action State
  const [loading, setLoading] = useState(false)
  const [processing, setProcessing] = useState(false)
  const [pipelineMessage, setPipelineMessage] = useState('')
  const [errorBanner, setErrorBanner] = useState('')

  // Confirmation Modal: 'verify' | 'reject' | 'split' | null
  const [activeModal, setActiveModal] = useState(null)
  const [responderId, setResponderId] = useState('responder-1')
  const [responderNotes, setResponderNotes] = useState('')

  // Responsive screen width listener
  useEffect(() => {
    const handleResize = () => {
      if (window.innerWidth < 1440 && viewMode === 'split') {
        setViewMode('focus')
      }
    }
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [viewMode])

  // Reset scroll positions when view mode changes
  useEffect(() => {
    const sidebar = document.querySelector('.tactical-map-sidebar')
    if (sidebar) sidebar.scrollTop = 0
    const workspace = document.querySelector('.intelligence-workspace')
    if (workspace) workspace.scrollTop = 0
  }, [viewMode])

  // Fetch incident list
  const fetchIncidents = useCallback(async () => {
    try {
      setLoading(true)
      const res = await fetch(`${API_BASE}/incidents`)
      if (!res.ok) throw new Error(`HTTP ${res.status}: Failed to fetch incidents`)
      const data = await res.json()
      setIncidents(data)
      setErrorBanner('')
      if (data.length > 0 && !selectedIncidentId) {
        setSelectedIncidentId(data[0].id)
      }
    } catch (err) {
      console.error(err)
      setErrorBanner('Could not connect to SETU backend. Ensure the backend server is running at http://127.0.0.1:8000.')
    } finally {
      setLoading(false)
    }
  }, [selectedIncidentId])

  // Fetch detail for selected incident
  const fetchIncidentDetail = useCallback(async (id) => {
    if (!id) {
      setIncidentDetail(null)
      return
    }
    try {
      const res = await fetch(`${API_BASE}/incidents/${id}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}: Failed to fetch incident ${id}`)
      const data = await res.json()
      setIncidentDetail(data)
      setSelectedReportIds([]) // reset split selection on incident change
      setErrorBanner('')
    } catch (err) {
      console.error(err)
      setErrorBanner(`Failed to load incident detail for ${id}.`)
    }
  }, [])

  // Initial load
  useEffect(() => {
    let ignore = false
    const initFetch = async () => {
      try {
        const res = await fetch(`${API_BASE}/incidents`)
        if (!res.ok) return
        const data = await res.json()
        if (!ignore) {
          setIncidents(data)
          if (data.length > 0) {
            setSelectedIncidentId((curr) => curr || data[0].id)
          }
        }
      } catch (err) {
        if (!ignore) {
          console.error(err)
          setErrorBanner('Could not connect to SETU backend. Ensure the backend server is running at http://127.0.0.1:8000.')
        }
      }
    }
    initFetch()
    return () => {
      ignore = true
    }
  }, [])

  // Load detail when selected ID changes
  useEffect(() => {
    if (!selectedIncidentId) return
    let ignore = false
    const loadDetail = async () => {
      try {
        const res = await fetch(`${API_BASE}/incidents/${selectedIncidentId}`)
        if (!res.ok) return
        const data = await res.json()
        if (!ignore) {
          setIncidentDetail(data)
          setSelectedReportIds([])
        }
      } catch (err) {
        if (!ignore) {
          console.error(err)
          setErrorBanner(`Failed to load incident detail for ${selectedIncidentId}.`)
        }
      }
    }
    loadDetail()
    return () => {
      ignore = true
    }
  }, [selectedIncidentId])

  // Process pipeline handler (idempotent reprocess)
  const handleProcessPipeline = async () => {
    try {
      setProcessing(true)
      setPipelineMessage('Executing SETU intelligence pipeline: Normalization → Extraction → Location → Clustering → Contradictions → Assessment...')
      const res = await fetch(`${API_BASE}/pipeline/process?reprocess=true`, { method: 'POST' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setPipelineMessage(data.message)
      await fetchIncidents()
      if (selectedIncidentId) {
        await fetchIncidentDetail(selectedIncidentId)
      }
    } catch (err) {
      console.error(err)
      setErrorBanner('Failed to process emergency reports through pipeline.')
    } finally {
      setProcessing(false)
    }
  }

  // Verify action
  const handleVerify = async () => {
    if (!selectedIncidentId) return
    try {
      const res = await fetch(`${API_BASE}/incidents/${selectedIncidentId}/verify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          responder_id: responderId.trim() || 'responder-1',
          notes: responderNotes.trim() || 'Operational verification confirmed by responder'
        }),
      })
      if (!res.ok) {
        const errData = await res.json()
        throw new Error(errData.detail || 'Verification failed')
      }
      setActiveModal(null)
      setResponderNotes('')
      await fetchIncidents()
      await fetchIncidentDetail(selectedIncidentId)
    } catch (err) {
      alert(`Action error: ${err.message}`)
    }
  }

  // Reject action
  const handleReject = async () => {
    if (!selectedIncidentId) return
    try {
      const res = await fetch(`${API_BASE}/incidents/${selectedIncidentId}/reject`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          responder_id: responderId.trim() || 'responder-1',
          notes: responderNotes.trim() || 'Incident marked as rejected by responder'
        }),
      })
      if (!res.ok) {
        const errData = await res.json()
        throw new Error(errData.detail || 'Rejection failed')
      }
      setActiveModal(null)
      setResponderNotes('')
      await fetchIncidents()
      await fetchIncidentDetail(selectedIncidentId)
    } catch (err) {
      alert(`Action error: ${err.message}`)
    }
  }

  // Split action
  const handleSplit = async () => {
    if (!selectedIncidentId || selectedReportIds.length === 0) return
    try {
      const res = await fetch(`${API_BASE}/incidents/${selectedIncidentId}/split`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          responder_id: responderId.trim() || 'responder-1',
          report_ids: selectedReportIds,
          notes: responderNotes.trim() || `Separated ${selectedReportIds.length} reports into independent candidate incident`,
        }),
      })
      if (!res.ok) {
        const errData = await res.json()
        throw new Error(errData.detail || 'Split failed')
      }
      const splitResult = await res.json()
      setActiveModal(null)
      setResponderNotes('')
      setSelectedReportIds([])
      await fetchIncidents()
      setSelectedIncidentId(splitResult.new_incident_id)
    } catch (err) {
      alert(`Split error: ${err.message}`)
    }
  }

  // Checkbox toggle for report split selection
  const toggleReportSelection = (rid) => {
    setSelectedReportIds((prev) =>
      prev.includes(rid) ? prev.filter((id) => id !== rid) : [...prev, rid]
    )
  }

  // Filtered incidents
  const filteredIncidents = useMemo(() => {
    return incidents.filter((inc) => {
      if (urgencyFilter !== 'all' && inc.urgency !== urgencyFilter) return false
      if (statusFilter !== 'all' && inc.status !== statusFilter) return false
      if (zoneFilter !== 'all' && getIncidentZone(inc) !== zoneFilter) return false
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase()
        const matchesId = inc.id.toLowerCase().includes(q)
        const matchesLoc = (inc.location_resolved || '').toLowerCase().includes(q)
        const matchesTitle = (inc.title || '').toLowerCase().includes(q)
        if (!matchesId && !matchesLoc && !matchesTitle) return false
      }
      return true
    })
  }, [incidents, urgencyFilter, statusFilter, zoneFilter, searchQuery])

  // Summary counts derived directly from live API data
  const summaryCounts = useMemo(() => {
    const total = incidents.length
    const critical = incidents.filter((i) => i.urgency === 'critical').length
    const high = incidents.filter((i) => i.urgency === 'high').length
    const medium = incidents.filter((i) => i.urgency === 'medium').length
    const low = incidents.filter((i) => i.urgency === 'low').length
    const unverified = incidents.filter((i) => i.status === 'unverified').length
    const conflicts = incidents.filter((i) => (i.contradiction_count || 0) > 0).length
    return { total, critical, high, medium, low, unverified, conflicts }
  }, [incidents])

  return (
    <div className="app-shell">
      {/* 1. Header & Command Bar */}
      <header className="command-bar">
        <div className="brand-group">
          <div className="brand-logo">SETU</div>
          <div className="brand-text">
            <div className="brand-title-line">
              <h1 className="brand-title">Emergency Information Fusion Engine</h1>
              <span className="live-status-badge">
                <span className="live-dot" /> LIVE OPS
              </span>
            </div>
            <p className="brand-tagline">
              Rampur Urban Flood Scenario · Multilingual Multi-Source Corroboration
            </p>
          </div>
        </div>

        <div className="header-controls">
          {/* Tactile Segmented View Switcher */}
          <div className="segmented-control" role="group" aria-label="Layout View Modes">
            <button
              className={`segmented-item ${viewMode === 'split' ? 'active' : ''}`}
              onClick={() => setViewMode('split')}
              title="3-Pane: Queue + Intelligence + Tactical Map"
            >
              Split View
            </button>
            <button
              className={`segmented-item ${viewMode === 'focus' ? 'active' : ''}`}
              onClick={() => setViewMode('focus')}
              title="2-Pane: Queue + Full-Width Intelligence Workspace"
            >
              Incident Focus
            </button>
            <button
              className={`segmented-item ${viewMode === 'map' ? 'active' : ''}`}
              onClick={() => setViewMode('map')}
              title="2-Pane: Queue + Expanded Tactical Map"
            >
              Map Overview
            </button>
          </div>

          <button
            className={`btn-pipeline ${processing ? 'running' : ''}`}
            onClick={handleProcessPipeline}
            disabled={processing}
          >
            {processing ? 'Fusing Intelligence...' : 'Process Intelligence Pipeline'}
          </button>
        </div>
      </header>

      {/* 2. Operational Funnel & KPI Status Strip */}
      <div className="ops-ribbon">
        {/* Pipeline Funnel: 20 -> 13 -> 4 Named Zones + GPS */}
        <div className="pipeline-funnel">
          <div className="funnel-step">
            <span className="funnel-num">20</span>
            <div className="funnel-text">
              <span className="funnel-title">RAW REPORTS</span>
              <span className="funnel-sub">WhatsApp · SMS · Field</span>
            </div>
          </div>

          <span className="funnel-chevron">→</span>

          <div className="funnel-step active">
            <span className="funnel-num">{summaryCounts.total || 13}</span>
            <div className="funnel-text">
              <span className="funnel-title">CANDIDATE INCIDENTS</span>
              <span className="funnel-sub">Deterministic Fusion</span>
            </div>
          </div>

          <span className="funnel-chevron">→</span>

          <div className="funnel-step">
            <span className="funnel-num">4 + GPS</span>
            <div className="funnel-text">
              <span className="funnel-title">4 NAMED ZONES + GPS</span>
              <span className="funnel-sub">Civil · Kotwali · Bilaspur · Naya</span>
            </div>
          </div>
        </div>

        {/* Real KPI Metrics */}
        <div className="kpi-stream">
          <div className="kpi-metric">
            <span className="kpi-label">Active</span>
            <span className="kpi-val">{summaryCounts.total}</span>
          </div>

          <div className="kpi-divider" />

          <div className="kpi-metric">
            <span className="kpi-dot critical" />
            <span className="kpi-label">Critical</span>
            <span className="kpi-val critical">{summaryCounts.critical}</span>
          </div>

          <div className="kpi-metric">
            <span className="kpi-dot high" />
            <span className="kpi-label">High</span>
            <span className="kpi-val high">{summaryCounts.high}</span>
          </div>

          <div className="kpi-metric">
            <span className="kpi-dot medium" />
            <span className="kpi-label">Medium</span>
            <span className="kpi-val medium">{summaryCounts.medium}</span>
          </div>

          <div className="kpi-divider" />

          <div className="kpi-metric">
            <span className="kpi-label">Unverified</span>
            <span className="kpi-val muted">{summaryCounts.unverified}</span>
          </div>

          {summaryCounts.conflicts > 0 && (
            <>
              <div className="kpi-divider" />
              <div className="kpi-metric conflict">
                <span className="kpi-dot conflict" />
                <span className="kpi-label">Contradictions</span>
                <span className="kpi-val conflict">{summaryCounts.conflicts}</span>
              </div>
            </>
          )}
        </div>
      </div>

      {/* Notifications */}
      {errorBanner && (
        <div className="banner-notice error">
          <span>{errorBanner}</span>
          <button className="btn-close-banner" onClick={() => setErrorBanner('')}>✕</button>
        </div>
      )}
      {pipelineMessage && (
        <div className="banner-notice info">
          <span>{pipelineMessage}</span>
          <button className="btn-close-banner" onClick={() => setPipelineMessage('')}>✕</button>
        </div>
      )}

      {/* 3. Main Operational Workspace */}
      <div className={`workspace-layout mode-${viewMode}`}>
        {/* Left Column: Incident Queue */}
        <aside className="queue-sidebar">
          <div className="queue-top-bar">
            <div className="queue-title-line">
              <h2 className="queue-heading">Incidents</h2>
              <span className="queue-count-tag">{filteredIncidents.length} of {incidents.length}</span>
            </div>

            {/* Quiet Search Box */}
            <div className="search-field">
              <span className="search-symbol">⌕</span>
              <input
                type="text"
                placeholder="Search ID, zone, or incident..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
              {searchQuery && (
                <button className="btn-clear-search" onClick={() => setSearchQuery('')}>✕</button>
              )}
            </div>

            {/* Understated Filters */}
            <div className="filter-shelf">
              <div className="filter-pill-row">
                {['all', 'critical', 'high', 'medium', 'low'].map((u) => (
                  <button
                    key={u}
                    className={`filter-tab ${urgencyFilter === u ? `active ${u}` : ''}`}
                    onClick={() => setUrgencyFilter(u)}
                  >
                    {u}
                  </button>
                ))}
              </div>

              <div className="filter-pill-row secondary">
                {['all', 'unverified', 'verified', 'rejected'].map((s) => (
                  <button
                    key={s}
                    className={`filter-tab ${statusFilter === s ? `active` : ''}`}
                    onClick={() => setStatusFilter(s)}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Incident Queue Scroll List */}
          <div className="queue-items-container">
            {loading && incidents.length === 0 ? (
              <div className="quiet-empty-state">Loading candidate incidents...</div>
            ) : filteredIncidents.length === 0 ? (
              <div className="quiet-empty-state">No incidents match current filters.</div>
            ) : (
              filteredIncidents.map((inc) => {
                const isSelected = inc.id === selectedIncidentId
                const zone = getIncidentZone(inc)
                const zoneMeta = ZONE_METADATA[zone]

                return (
                  <div
                    key={inc.id}
                    className={`queue-item ${isSelected ? 'selected' : ''} urgency-${inc.urgency || 'low'}`}
                    onClick={() => setSelectedIncidentId(inc.id)}
                  >
                    <div className="item-header">
                      <span className="item-id">{inc.id}</span>
                      <div className="item-status-cluster">
                        <span className={`status-pill ${inc.urgency}`}>
                          {inc.urgency?.toUpperCase()}
                        </span>
                        {inc.status !== 'unverified' && (
                          <span className={`status-pill ${inc.status}`}>
                            {inc.status?.toUpperCase()}
                          </span>
                        )}
                      </div>
                    </div>

                    <div className="item-title">{inc.title || 'Untitled Incident'}</div>

                    <div className="item-location">
                      <span className="zone-dot" style={{ backgroundColor: zoneMeta?.color || '#94a3b8' }} />
                      <span className="zone-text">{formatZoneName(inc.location_resolved)}</span>
                    </div>

                    <div className="item-metrics-line">
                      <div className="metric-tag">
                        <span className="lbl">PRIORITY</span>
                        <span className="val highlight">{inc.priority !== null ? Math.round(inc.priority) : '—'}</span>
                      </div>
                      <div className="metric-tag">
                        <span className="lbl">SEV</span>
                        <span className="val">{inc.severity !== null ? Math.round(inc.severity) : '—'}</span>
                      </div>
                      <div className="metric-tag">
                        <span className="lbl">CONF</span>
                        <span className="val">
                          {inc.confidence !== null ? Math.round(inc.confidence * 100) + '%' : '—'}
                        </span>
                      </div>

                      <span className="item-reports-count">
                        {inc.report_count} {inc.report_count === 1 ? 'report' : 'reports'}
                      </span>

                      {(inc.contradiction_count || 0) > 0 && (
                        <span className="item-conflict-dot" title="Active physical contradiction in this incident" />
                      )}
                    </div>
                  </div>
                )
              })
            )}
          </div>
        </aside>

        {/* Center Column: Incident Intelligence (The Visual Focal Point) */}
        {viewMode !== 'map' && (
          <main className="intelligence-workspace">
            {!incidentDetail ? (
              <div className="workspace-empty">
                <div className="empty-symbol">🛡️</div>
                <h3>Select a Candidate Incident</h3>
                <p>Choose an incident from the queue to inspect fused evidence, physical contradictions, explainability breakdowns, and audit history.</p>
              </div>
            ) : (
              <div className="intelligence-dossier">
                {/* 1. Incident Hero Section (Open, Breathing, Authoritative) */}
                <section className="dossier-hero">
                  <div className="hero-main-group">
                    <div className="hero-meta-row">
                      <span className="dossier-id">{incidentDetail.id}</span>
                      <span className={`status-tag ${incidentDetail.status}`}>
                        {incidentDetail.status.toUpperCase()}
                      </span>
                      <span className={`status-tag ${incidentDetail.urgency}`}>
                        {incidentDetail.urgency?.toUpperCase()} URGENCY
                      </span>
                    </div>

                    <h2 className="dossier-title">{incidentDetail.title}</h2>

                    {(() => {
                      const loc = formatLocationPresentation(incidentDetail)
                      return (
                        <div className="dossier-location-row">
                          <span className="loc-string">📍 <strong>{loc.name}</strong></span>
                          {loc.badgeText && (
                            <span className={`badge-geo-pill ${loc.badgeClass}`}>{loc.badgeText}</span>
                          )}
                          {loc.coordsText && (
                            <span className="coords-code">{loc.coordsText}</span>
                          )}
                          <span className="diversity-count">
                            Corroborated across <strong>{incidentDetail.source_diversity}</strong> source channels ({incidentDetail.report_count} reports)
                          </span>
                        </div>
                      )
                    })()}
                  </div>

                  {/* Deliberate Responder Action Controls */}
                  <div className="dossier-actions">
                    <button
                      className="btn-action verify"
                      disabled={incidentDetail.status === 'verified'}
                      onClick={() => {
                        setActiveModal('verify')
                        setResponderNotes('')
                      }}
                    >
                      ✓ Verify Incident
                    </button>

                    <button
                      className="btn-action reject"
                      disabled={incidentDetail.status === 'rejected'}
                      onClick={() => {
                        setActiveModal('reject')
                        setResponderNotes('')
                      }}
                    >
                      ✕ Reject
                    </button>

                    <button
                      className={`btn-action split ${selectedReportIds.length > 0 ? 'active' : ''}`}
                      disabled={
                        selectedReportIds.length === 0 ||
                        selectedReportIds.length === incidentDetail.report_count ||
                        incidentDetail.status === 'rejected'
                      }
                      onClick={() => {
                        setActiveModal('split')
                        setResponderNotes('')
                      }}
                      title={
                        selectedReportIds.length === 0
                          ? 'Select 1 or more reports in the source list below to split'
                          : selectedReportIds.length === incidentDetail.report_count
                          ? 'Cannot split all reports'
                          : `Split ${selectedReportIds.length} selected report(s)`
                      }
                    >
                      ⚲ Split ({selectedReportIds.length})
                    </button>
                  </div>
                </section>

                {/* 2. Premium Assessment Showcase (Severity, Confidence, Priority) */}
                <section className="assessment-deck">
                  {/* Severity Column */}
                  <div className="assessment-col severity">
                    <div className="col-top">
                      <div>
                        <span className="col-label">SEVERITY SCORE</span>
                        <p className="col-sub">Physical threat & hazard magnitude</p>
                      </div>
                      <div className="score-num severity">
                        {incidentDetail.severity !== null ? Math.round(incidentDetail.severity) : '—'}
                        <span className="score-denom">/100</span>
                      </div>
                    </div>

                    <div className="subtle-meter">
                      <div className="meter-fill severity" style={{ width: `${incidentDetail.severity || 0}%` }} />
                    </div>

                    <div className="tabular-breakdown">
                      <div className="table-line">
                        <span className="k">Base Category ({incidentDetail.severity_breakdown?.base_type || 'unclassified'})</span>
                        <span className="v">{incidentDetail.severity_breakdown?.baseline_severity || 0}</span>
                      </div>
                      {incidentDetail.severity_breakdown?.modifiers?.map((m) => (
                        <div key={m.name} className="table-line modifier">
                          <span className="k">+ {m.name.replace(/_/g, ' ')}</span>
                          <span className="v">+{m.bonus}</span>
                        </div>
                      ))}
                      <div className="table-line total">
                        <span className="k">Computed Severity</span>
                        <span className="v">{incidentDetail.severity}</span>
                      </div>
                    </div>
                  </div>

                  {/* Confidence Column */}
                  <div className="assessment-col confidence">
                    <div className="col-top">
                      <div>
                        <span className="col-label">CONFIDENCE SCORE</span>
                        <p className="col-sub">Evidence corroboration & consistency</p>
                      </div>
                      <div className="score-num confidence">
                        {incidentDetail.confidence !== null ? (incidentDetail.confidence * 100).toFixed(1) + '%' : '—'}
                      </div>
                    </div>

                    <div className="subtle-meter">
                      <div className="meter-fill confidence" style={{ width: `${(incidentDetail.confidence || 0) * 100}%` }} />
                    </div>

                    <div className="tabular-breakdown">
                      <div className="table-line">
                        <span className="k">Source Count (25%)</span>
                        <span className="v mono">
                          {incidentDetail.confidence_breakdown?.source_count !== null
                            ? (incidentDetail.confidence_breakdown.source_count * 100).toFixed(0) + '%'
                            : '—'}
                        </span>
                      </div>
                      <div className="table-line">
                        <span className="k">Source Diversity (20%)</span>
                        <span className="v mono">
                          {incidentDetail.confidence_breakdown?.source_diversity !== null
                            ? (incidentDetail.confidence_breakdown.source_diversity * 100).toFixed(0) + '%'
                            : '—'}
                        </span>
                      </div>
                      <div className="table-line">
                        <span className="k">Evidence Consistency (25%)</span>
                        <span className="v mono">
                          {incidentDetail.confidence_breakdown?.consistency !== null
                            ? (incidentDetail.confidence_breakdown.consistency * 100).toFixed(0) + '%'
                            : '—'}
                        </span>
                      </div>
                      <div className="table-line">
                        <span className="k">Extraction Quality (15%)</span>
                        <span className="v mono">
                          {incidentDetail.confidence_breakdown?.extraction_quality !== null
                            ? (incidentDetail.confidence_breakdown.extraction_quality * 100).toFixed(0) + '%'
                            : '—'}
                        </span>
                      </div>
                      <div className="table-line">
                        <span className="k">Information Completeness (15%)</span>
                        <span className="v mono">
                          {incidentDetail.confidence_breakdown?.information_type !== null
                            ? (incidentDetail.confidence_breakdown.information_type * 100).toFixed(0) + '%'
                            : '—'}
                        </span>
                      </div>
                    </div>
                  </div>

                  {/* Priority Column */}
                  <div className="assessment-col priority">
                    <div className="col-top">
                      <div>
                        <span className="col-label">DISPATCH PRIORITY</span>
                        <p className="col-sub">Calculated responder urgency</p>
                      </div>
                      <div className="score-num priority">
                        {incidentDetail.priority !== null ? Math.round(incidentDetail.priority) : '—'}
                        <span className="score-denom">/100</span>
                      </div>
                    </div>

                    <div className="subtle-meter">
                      <div className="meter-fill priority" style={{ width: `${incidentDetail.priority || 0}%` }} />
                    </div>

                    <div className="formula-container">
                      <span className="formula-label">MATHEMATICAL FORMULA</span>
                      <code className="formula-text">
                        {incidentDetail.priority_breakdown?.formula || 'Priority = Severity × (0.40 + 0.60 × Confidence)'}
                      </code>
                    </div>

                    {incidentDetail.priority_breakdown?.low_confidence_cap_applied && (
                      <div className="safety-cap-callout">
                        ⚠️ Low-confidence safety rule applied: Confidence &lt; 0.30 caps maximum priority at 69.
                      </div>
                    )}

                    <div className="urgency-footer-row">
                      <span className="urgency-caption">Assigned Urgency Band:</span>
                      <span className={`urgency-badge ${incidentDetail.urgency}`}>
                        {incidentDetail.urgency?.toUpperCase()}
                      </span>
                    </div>
                  </div>
                </section>

                {/* 3. Physical Contradiction Alert (Calm, Authoritative, Non-Alarmist) */}
                {incidentDetail.contradictions?.length > 0 && (
                  <section className="contradiction-dossier-card">
                    <div className="contradiction-title-row">
                      <div className="title-left">
                        <span className="amber-bullet" />
                        <div>
                          <h3 className="contradiction-title">
                            {incidentDetail.contradictions.length} Physical Contradiction Detected
                          </h3>
                          <p className="contradiction-desc">
                            Reports in this incident provide conflicting physical or quantitative evidence. Not resolved automatically.
                          </p>
                        </div>
                      </div>
                      <span className="unresolved-status-tag">UNRESOLVED BY ALGORITHM</span>
                    </div>

                    <div className="contradiction-comparisons">
                      {incidentDetail.contradictions.map((c) => (
                        <div key={c.id} className="conflict-entry">
                          <div className="conflict-meta-bar">
                            <span>TYPE: <strong>{c.contradiction_type.toUpperCase()}</strong></span>
                            <span>FIELD: <strong>{c.field}</strong></span>
                            {c.resolution && (
                              <span className="resolution-text">
                                ✓ Resolved by operator: {c.resolution}
                              </span>
                            )}
                          </div>

                          <div className="comparison-panes">
                            {/* Side A Document */}
                            <div className="source-doc-side side-a">
                              <span className="doc-side-label">Side A — Report {c.side_a.report_id}</span>
                              <div className="doc-extracted-val">
                                Extracted: <strong>{typeof c.side_a.value === 'object' ? JSON.stringify(c.side_a.value) : String(c.side_a.value)}</strong>
                              </div>
                              <blockquote className="doc-quote">
                                “{c.side_a.evidence || 'Direct report excerpt'}”
                              </blockquote>
                            </div>

                            <div className="vs-sign">VS</div>

                            {/* Side B Document */}
                            <div className="source-doc-side side-b">
                              <span className="doc-side-label">Side B — Report {c.side_b.report_id}</span>
                              <div className="doc-extracted-val">
                                Extracted: <strong>{typeof c.side_b.value === 'object' ? JSON.stringify(c.side_b.value) : String(c.side_b.value)}</strong>
                              </div>
                              <blockquote className="doc-quote">
                                “{c.side_b.evidence || 'Direct report excerpt'}”
                              </blockquote>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </section>
                )}

                {/* 4. Contributing Source Documents & Evidence */}
                <section className="dossier-section">
                  <div className="section-title-bar">
                    <div>
                      <h3 className="section-heading">Contributing Source Reports ({incidentDetail.reports.length})</h3>
                      <p className="section-description">
                        Authenticated field dispatches fused into this incident. Select checkboxes to split unrelated reports.
                      </p>
                    </div>
                    {selectedReportIds.length > 0 && (
                      <span className="selection-active-badge">
                        {selectedReportIds.length} of {incidentDetail.reports.length} marked for split
                      </span>
                    )}
                  </div>

                  <div className="source-documents-list">
                    {incidentDetail.reports.map((rep) => {
                      const isChecked = selectedReportIds.includes(rep.id)
                      return (
                        <article key={rep.id} className={`source-document-card ${isChecked ? 'marked-for-split' : ''}`}>
                          <div className="doc-top-bar">
                            <label className="checkbox-id-wrapper">
                              <input
                                type="checkbox"
                                checked={isChecked}
                                onChange={() => toggleReportSelection(rep.id)}
                              />
                              <span className="doc-report-id">{rep.id}</span>
                            </label>

                            <div className="doc-channel-tags">
                              <span className={`channel-pill ${rep.source}`}>
                                {rep.source.toUpperCase()}
                              </span>
                              <span className="lang-tag">{rep.language}</span>
                              <span className="timestamp-tag">
                                {rep.received_at?.split('T')[1]?.split('+')[0] || rep.received_at}
                              </span>
                            </div>
                          </div>

                          <div className="doc-body-quote">
                            “{rep.raw_text}”
                          </div>

                          {rep.extractions?.length > 0 && (() => {
                            const formattedList = rep.extractions
                              .map((ext) => formatExtraction(ext.field_name, ext.value))
                              .filter(Boolean)
                            if (formattedList.length === 0) return null
                            return (
                              <div className="doc-extractions-line">
                                {formattedList.map((fmt, idx) => (
                                  <span key={idx} className="extraction-badge">
                                    <span className="k">{fmt.label}:</span>{' '}
                                    <span className="v">{fmt.displayValue}</span>
                                  </span>
                                ))}
                              </div>
                            )
                          })()}
                        </article>
                      )
                    })}
                  </div>
                </section>

                {/* 5. Incident Audit Trail Timeline */}
                <section className="dossier-section audit">
                  <div className="section-title-bar">
                    <div>
                      <h3 className="section-heading">Operational Audit Trail</h3>
                      <p className="section-description">Immutable log of authorized operator actions (verify, reject, split).</p>
                    </div>
                  </div>

                  {incidentDetail.audit_logs?.length === 0 ? (
                    <div className="empty-audit-notice">
                      No operator actions recorded yet. Incident is in initial automated candidate status.
                    </div>
                  ) : (
                    <div className="audit-event-stream">
                      {incidentDetail.audit_logs.map((log) => (
                        <div key={log.id} className="audit-event-row">
                          <span className={`audit-pill ${log.action}`}>
                            {log.action.toUpperCase()}
                          </span>
                          <div className="audit-detail-block">
                            <div className="audit-line-one">
                              <span className="operator-id">Operator: <strong>{log.responder_id}</strong></span>
                              <span className="audit-time">{new Date(log.timestamp).toLocaleTimeString()}</span>
                            </div>
                            <div className="audit-notes-line">{log.notes || 'No operator notes entered.'}</div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </section>
              </div>
            )}
          </main>
        )}

        {/* Right Column / Map Panel: 4 Named Response Zones + GPS/Outskirts */}
        {(viewMode === 'split' || viewMode === 'map') && (
          <aside className={`tactical-map-sidebar ${viewMode === 'map' ? 'expanded-mode' : ''}`}>
            <div className="map-top-bar">
              <div className="map-heading-cluster">
                <span className="map-lead-icon">🗺️</span>
                <h3 className="map-lead-title">4 Named Response Zones + GPS/Outskirts</h3>
              </div>
              {viewMode === 'split' && (
                <button
                  className="btn-hide-map"
                  onClick={() => setViewMode('focus')}
                  title="Collapse map into Incident Focus view"
                >
                  Hide ✕
                </button>
              )}
            </div>

            <RampurMap
              incidents={incidents}
              selectedIncidentId={selectedIncidentId}
              onSelectIncident={setSelectedIncidentId}
              selectedZone={zoneFilter}
              onSelectZone={(z) => setZoneFilter((curr) => (curr === z ? 'all' : z))}
              expanded={viewMode === 'map'}
            />

            {/* Quick Zone Filter Under Map */}
            <div className="map-zone-bar">
              <span className="zone-bar-lead">Filter:</span>
              <button
                className={`zone-btn ${zoneFilter === 'all' ? 'active' : ''}`}
                onClick={() => setZoneFilter('all')}
              >
                All ({incidents.length})
              </button>
              {Object.entries(ZONE_METADATA).map(([key, meta]) => {
                const count = incidents.filter((i) => getIncidentZone(i) === key).length
                return (
                  <button
                    key={key}
                    className={`zone-btn ${zoneFilter === key ? `active ${key}` : ''}`}
                    onClick={() => setZoneFilter((curr) => (curr === key ? 'all' : key))}
                  >
                    {meta.shortName} ({count})
                  </button>
                )
              })}
            </div>

            {/* Zone Incident Distribution in Split View */}
            {viewMode === 'split' && (
              <div className="zone-roster-shelf">
                <div className="roster-header">
                  <span className="roster-title">ZONE INCIDENT DISTRIBUTION</span>
                  <span className="roster-meta">4 Named Zones + GPS</span>
                </div>
                <div className="roster-list">
                  {Object.entries(ZONE_METADATA).map(([key, meta]) => {
                    const zoneIncidents = incidents.filter((i) => getIncidentZone(i) === key)
                    const isZoneSelected = zoneFilter === key
                    return (
                      <div
                        key={key}
                        className={`zone-roster-card ${isZoneSelected ? 'selected' : ''}`}
                        onClick={() => setZoneFilter((curr) => (curr === key ? 'all' : key))}
                      >
                        <div className="roster-card-top">
                          <div className="zone-identity">
                            <span className="zone-indicator-dot" style={{ backgroundColor: meta.color }} />
                            <span className="zone-display-name">{meta.name}</span>
                          </div>
                          <span className="zone-incident-badge">
                            {zoneIncidents.length} {zoneIncidents.length === 1 ? 'incident' : 'incidents'}
                          </span>
                        </div>
                        {zoneIncidents.length > 0 && (
                          <div className="roster-incident-chips">
                            {zoneIncidents.map((inc) => (
                              <button
                                key={inc.id}
                                className={`chip-item ${inc.id === selectedIncidentId ? 'active' : ''} ${inc.urgency}`}
                                onClick={(e) => {
                                  e.stopPropagation()
                                  setSelectedIncidentId(inc.id)
                                }}
                                title={`${inc.id}: ${inc.title} (Priority ${Math.round(inc.priority || 0)})`}
                              >
                                {inc.id} · P{Math.round(inc.priority || 0)}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            )}
          </aside>
        )}
      </div>

      {/* 4. Action Confirmation Dialogs */}
      {activeModal && (
        <div className="modal-backdrop" onClick={() => setActiveModal(null)}>
          <div className="modal-sheet" onClick={(e) => e.stopPropagation()}>
            <div className="sheet-header">
              <h3>
                {activeModal === 'verify' && `Verify Incident ${selectedIncidentId}`}
                {activeModal === 'reject' && `Reject Incident ${selectedIncidentId}`}
                {activeModal === 'split' && `Split ${selectedReportIds.length} Report(s) from ${selectedIncidentId}`}
              </h3>
              <button className="btn-sheet-close" onClick={() => setActiveModal(null)}>✕</button>
            </div>

            <div className="sheet-body">
              <p className="sheet-explainer">
                {activeModal === 'verify' &&
                  'Human verification acknowledges this candidate incident as valid for emergency responder dispatch. All underlying reports and intelligence evidence are preserved.'}
                {activeModal === 'reject' &&
                  'Rejecting will mark this candidate incident as rejected. Underlying reports and historical extractions remain intact and are not deleted.'}
                {activeModal === 'split' &&
                  `The selected report(s) [${selectedReportIds.join(', ')}] will be moved to a newly created candidate incident. Remaining reports stay in ${selectedIncidentId}. Assessments and contradictions will be recalculated atomically.`}
              </p>

              <div className="input-field-group">
                <label>Operator / Responder ID</label>
                <input
                  type="text"
                  value={responderId}
                  onChange={(e) => setResponderId(e.target.value)}
                  placeholder="responder-1"
                />
              </div>

              <div className="input-field-group">
                <label>Operational Rationale / Dispatch Notes</label>
                <textarea
                  rows={3}
                  value={responderNotes}
                  onChange={(e) => setResponderNotes(e.target.value)}
                  placeholder="Enter operator rationale, verification details, or dispatch context..."
                />
              </div>
            </div>

            <div className="sheet-footer">
              <button className="btn-sheet-cancel" onClick={() => setActiveModal(null)}>
                Cancel
              </button>
              {activeModal === 'verify' && (
                <button className="btn-sheet-confirm verify" onClick={handleVerify}>
                  Confirm Verification
                </button>
              )}
              {activeModal === 'reject' && (
                <button className="btn-sheet-confirm reject" onClick={handleReject}>
                  Confirm Rejection
                </button>
              )}
              {activeModal === 'split' && (
                <button className="btn-sheet-confirm split" onClick={handleSplit}>
                  Confirm Incident Split
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
