import { useEffect, useRef, useState } from 'react'
import { api } from '../api'

export default function NewIncidentModal({ onClose, onFired }) {
  const [service, setService] = useState('')
  const [severity, setSeverity] = useState('P2')
  const [firing, setFiring] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef()

  useEffect(() => { inputRef.current?.focus() }, [])

  async function fire() {
    if (!service.trim()) { setError('Service name is required'); return }
    setFiring(true); setError('')
    try {
      const resp = await api.fire(service.trim(), severity)
      onFired(resp.incident_id)
      onClose()
    } catch (e) {
      setError(e.message)
      setFiring(false)
    }
  }

  function onKey(e) { if (e.key === 'Enter') fire() }

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 100, backdropFilter: 'blur(4px)',
    }} onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div style={{
        background: '#1e1e1e', border: '1px solid var(--border)',
        borderRadius: 16, padding: '28px 28px 24px', width: 400,
        boxShadow: '0 24px 60px rgba(0,0,0,0.5)',
      }}>
        <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 20 }}>
          🚨 Fire Incident
        </div>

        <label style={{ fontSize: 12, color: 'var(--text-2)', display: 'block', marginBottom: 6 }}>
          Service name
        </label>
        <input
          ref={inputRef}
          value={service}
          onChange={e => setService(e.target.value)}
          onKeyDown={onKey}
          placeholder="auth-service"
          style={{
            width: '100%', padding: '10px 14px', borderRadius: 10,
            background: 'var(--bg-surface)', border: '1px solid var(--border)',
            color: 'var(--text)', fontSize: 14, marginBottom: 16,
          }}
        />

        <label style={{ fontSize: 12, color: 'var(--text-2)', display: 'block', marginBottom: 8 }}>
          Severity
        </label>
        <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
          {['P1','P2','P3','P4'].map(s => (
            <button key={s} onClick={() => setSeverity(s)} style={{
              flex: 1, padding: '8px 0', borderRadius: 8, fontSize: 13, fontWeight: 600,
              border: `1.5px solid ${severity === s ? 'currentColor' : 'var(--border)'}`,
              background: severity === s ? 'var(--bg-active)' : 'transparent',
              transition: 'all 0.15s',
            }} className={`sev-${s}`}>{s}</button>
          ))}
        </div>

        {error && (
          <div style={{ fontSize: 12, color: 'var(--p1)', marginBottom: 12 }}>{error}</div>
        )}

        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button onClick={onClose} style={{
            padding: '9px 18px', borderRadius: 10, fontSize: 13,
            background: 'var(--bg-hover)', color: 'var(--text-2)',
          }}>Cancel</button>
          <button onClick={fire} disabled={firing} style={{
            padding: '9px 22px', borderRadius: 10, fontSize: 13, fontWeight: 600,
            background: 'var(--accent)', color: '#fff', opacity: firing ? 0.7 : 1,
            transition: 'opacity 0.15s', display: 'flex', alignItems: 'center', gap: 8,
          }}>
            {firing && <span className="spinner" style={{ width: 12, height: 12, borderTopColor: '#fff' }} />}
            Fire
          </button>
        </div>
      </div>
    </div>
  )
}
