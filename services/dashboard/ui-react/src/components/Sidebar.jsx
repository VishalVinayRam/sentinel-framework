import { useState } from 'react'

const SEV_ORDER = { P1: 0, P2: 1, P3: 2, P4: 3 }

function relTime(iso) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    const s = Math.floor((Date.now() - d.getTime()) / 1000)
    if (s < 60) return `${s}s ago`
    if (s < 3600) return `${Math.floor(s / 60)}m ago`
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`
    return `${Math.floor(s / 86400)}d ago`
  } catch { return '' }
}

export default function Sidebar({ incidents, selectedId, onSelect, onNew, stats }) {
  const [filter, setFilter] = useState('open')

  const filtered = incidents
    .filter(i => filter === 'all' || i.status !== 'RESOLVED')
    .sort((a, b) => (SEV_ORDER[a.severity] ?? 9) - (SEV_ORDER[b.severity] ?? 9))

  return (
    <aside style={{
      width: 260, flexShrink: 0, background: 'var(--bg-sidebar)',
      display: 'flex', flexDirection: 'column', borderRight: '1px solid var(--border)',
      height: '100%', overflow: 'hidden',
    }}>
      {/* Logo */}
      <div style={{
        padding: '18px 16px 14px', display: 'flex', alignItems: 'center', gap: 10,
        borderBottom: '1px solid var(--border)',
      }}>
        <div style={{
          width: 28, height: 28, borderRadius: 7, background: 'var(--accent)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 15, flexShrink: 0,
        }}>🛡️</div>
        <span style={{ fontWeight: 700, fontSize: 15, letterSpacing: '-0.01em' }}>Sentinel</span>
        {stats?.total_open > 0 && (
          <span style={{
            marginLeft: 'auto', background: 'var(--p1-bg)', color: 'var(--p1)',
            fontSize: 11, fontWeight: 700, padding: '1px 6px', borderRadius: 10,
          }}>{stats.total_open}</span>
        )}
      </div>

      {/* New incident */}
      <div style={{ padding: '10px 12px', borderBottom: '1px solid var(--border)' }}>
        <button onClick={onNew} style={{
          width: '100%', padding: '8px 12px', borderRadius: 8,
          background: 'rgba(255,255,255,0.10)', color: 'var(--text)',
          display: 'flex', alignItems: 'center', gap: 8,
          fontSize: 13, fontWeight: 500, transition: 'background 0.15s',
        }}
          onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.14)'}
          onMouseLeave={e => e.currentTarget.style.background = 'rgba(255,255,255,0.10)'}
        >
          <span style={{ fontSize: 16, lineHeight: 1 }}>+</span> New Incident
        </button>
      </div>

      {/* Filter tabs */}
      <div style={{
        display: 'flex', padding: '8px 12px', gap: 4,
        borderBottom: '1px solid var(--border)',
      }}>
        {['open', 'all'].map(f => (
          <button key={f} onClick={() => setFilter(f)} style={{
            flex: 1, padding: '5px 0', borderRadius: 6, fontSize: 12, fontWeight: 500,
            background: filter === f ? 'var(--bg-surface)' : 'transparent',
            color: filter === f ? 'var(--text)' : 'var(--text-2)',
            transition: 'all 0.15s', textTransform: 'capitalize',
          }}>{f === 'open' ? 'Open' : 'All'}</button>
        ))}
      </div>

      {/* Incident list */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '6px 8px' }}>
        {filtered.length === 0 && (
          <div style={{ padding: '24px 12px', color: 'var(--text-3)', fontSize: 13, textAlign: 'center' }}>
            No incidents
          </div>
        )}
        {filtered.map(inc => (
          <IncidentItem
            key={inc.incident_id}
            inc={inc}
            active={inc.incident_id === selectedId}
            onClick={() => onSelect(inc.incident_id)}
          />
        ))}
      </div>

      {/* Stats footer */}
      {stats && (
        <div style={{
          padding: '10px 14px', borderTop: '1px solid var(--border)',
          display: 'flex', gap: 12, fontSize: 11, color: 'var(--text-3)',
        }}>
          <span>P1 <b style={{ color: 'var(--p1)' }}>{stats.by_severity?.P1 ?? 0}</b></span>
          <span>P2 <b style={{ color: 'var(--p2)' }}>{stats.by_severity?.P2 ?? 0}</b></span>
          <span style={{ marginLeft: 'auto' }}>MTTR {stats.mttr_minutes ?? '—'}m</span>
        </div>
      )}
    </aside>
  )
}

function IncidentItem({ inc, active, onClick }) {
  const pending = inc.ai_pending
  const rc = inc.root_cause || ''
  const subtitle = pending
    ? 'Analyzing…'
    : rc.replace(/[⏳]/g, '').trim().slice(0, 60) || inc.title?.slice(0, 60) || inc.service

  return (
    <button onClick={onClick} style={{
      width: '100%', textAlign: 'left', padding: '9px 10px', borderRadius: 8,
      background: active ? 'var(--bg-active)' : 'transparent',
      display: 'flex', flexDirection: 'column', gap: 4,
      transition: 'background 0.12s', marginBottom: 1,
    }}
      onMouseEnter={e => { if (!active) e.currentTarget.style.background = 'var(--bg-hover)' }}
      onMouseLeave={e => { if (!active) e.currentTarget.style.background = 'transparent' }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
        <span className={`sev sev-${inc.severity}`}>{inc.severity}</span>
        <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--text)', flex: 1,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {inc.service}
        </span>
        {pending && <span className="spinner" style={{ width: 10, height: 10, borderWidth: 1.5 }} />}
      </div>
      <div style={{ fontSize: 11, color: 'var(--text-2)', paddingLeft: 1,
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '100%' }}>
        {subtitle}
      </div>
      <div style={{ fontSize: 11, color: 'var(--text-3)' }}>
        {relTime(inc.created_at)}
      </div>
    </button>
  )
}
