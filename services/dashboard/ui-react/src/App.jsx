import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import Sidebar from './components/Sidebar'
import ChatView from './components/ChatView'
import NewIncidentModal from './components/NewIncidentModal'

export default function App() {
  const [incidents, setIncidents]     = useState([])
  const [stats, setStats]             = useState(null)
  const [selectedId, setSelectedId]   = useState(null)
  const [showNewModal, setShowNewModal] = useState(false)
  const [connected, setConnected]     = useState(true)

  const refresh = useCallback(async () => {
    try {
      const [incs, st] = await Promise.all([api.incidents(), api.stats()])
      setIncidents(Array.isArray(incs) ? incs : incs.incidents || [])
      setStats(st)
      setConnected(true)
    } catch {
      setConnected(false)
    }
  }, [])

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 15000)
    return () => clearInterval(t)
  }, [refresh])

  async function handleResolve(id) {
    await api.resolve(id).catch(() => {})
    refresh()
  }

  async function handleAck(id) {
    await api.acknowledge(id, 'on-call').catch(() => {})
    refresh()
  }

  function handleFired(newId) {
    refresh()
    setSelectedId(newId)
  }

  return (
    <div style={{ display: 'flex', height: '100%', background: 'var(--bg)' }}>
      {/* Offline banner */}
      {!connected && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, zIndex: 50,
          background: 'rgba(255,77,79,0.15)', borderBottom: '1px solid rgba(255,77,79,0.3)',
          padding: '6px 20px', fontSize: 12, color: '#ff7875', textAlign: 'center',
        }}>
          ⚠️ Cannot reach dashboard API — retrying…
        </div>
      )}

      <Sidebar
        incidents={incidents}
        selectedId={selectedId}
        onSelect={setSelectedId}
        onNew={() => setShowNewModal(true)}
        stats={stats}
      />

      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <ChatView
          incidentId={selectedId}
          onResolve={handleResolve}
          onAck={handleAck}
        />
      </main>

      {showNewModal && (
        <NewIncidentModal
          onClose={() => setShowNewModal(false)}
          onFired={handleFired}
        />
      )}
    </div>
  )
}
