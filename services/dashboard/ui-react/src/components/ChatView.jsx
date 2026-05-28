import { useEffect, useRef, forwardRef, useState } from 'react'
import { api } from '../api'
import { AIMessage, UserMessage, TypingIndicator, Divider } from './MessageBubble'

function buildRcaMd(inc) {
  const rc = inc.root_cause || ''
  if (!rc || inc.ai_pending || rc.includes('⏳') || /analyzing|running/i.test(rc)) return null
  const parts = []
  if (rc) parts.push(`**Root cause:** ${rc}`)
  if (inc.summary) parts.push(`\n**Impact:** ${inc.summary}`)
  const la = inc.log_analysis || {}
  const factors = inc.contributing_factors || la.contributing_factors || []
  if (factors.length) { parts.push('\n**Contributing factors:**'); factors.forEach(f => parts.push(`- ${f}`)) }
  const conf = (la.confidence || '').toUpperCase()
  const trend = la.degradation_trend
  const comps = la.affected_components || []
  const meta = []
  if (conf) meta.push(`${conf === 'HIGH' ? '🟢' : conf === 'MEDIUM' ? '🟡' : '🔴'} Confidence: **${conf}**`)
  if (trend) meta.push(`Trend: **${trend}**`)
  if (comps.length) meta.push(`Affected: **${comps.join(', ')}**`)
  if (meta.length) parts.push('\n' + meta.join('  ·  '))
  return parts.join('\n') || null
}

function buildRunbookMd(inc) {
  const rb = inc.runbook || {}
  const steps = [1,2,3,4,5].map(i => rb[`step${i}`]).filter(Boolean)
  return steps.length ? steps.map((s, i) => `${i+1}. ${s}`).join('\n') : null
}

function buildCodeMd(inc) {
  const cc = inc.code_context
  if (!cc?.files?.length) return null
  const f = cc.files[0]
  const lines = (f.lines || []).slice(0, 15).join('\n')
  let md = `**\`${f.path}\`** (line ${Math.floor(f.start_line || 0)})\n\`\`\`${f.language || 'python'}\n${lines}\n\`\`\``
  if (f.fix) md += `\n\n💡 **Fix:** \`${f.fix.slice(0, 100)}\``
  return md
}

// ── InputBar ────────────────────────────────────────────────────────────────

const InputBar = forwardRef(function InputBar({ value, onChange, onSend, sending, disabled, placeholder }, ref) {
  function onKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onSend() }
  }
  const canSend = !disabled && !sending && value.trim()
  return (
    <div style={{ padding: '16px 32px 24px', flexShrink: 0 }}>
      <div style={{
        display: 'flex', alignItems: 'flex-end', gap: 10,
        background: 'var(--bg-surface)', border: '1px solid var(--border)',
        borderRadius: 16, padding: '10px 14px',
      }}>
        <textarea
          ref={ref}
          value={value}
          onChange={e => onChange(e.target.value)}
          onKeyDown={onKey}
          disabled={disabled}
          placeholder={placeholder}
          rows={1}
          style={{
            flex: 1, resize: 'none', background: 'none', color: 'var(--text)',
            fontSize: 14, lineHeight: 1.6, maxHeight: 160, overflowY: 'auto',
            opacity: disabled ? 0.4 : 1,
          }}
          onInput={e => {
            e.target.style.height = 'auto'
            e.target.style.height = Math.min(e.target.scrollHeight, 160) + 'px'
          }}
        />
        <button onClick={onSend} disabled={!canSend} style={{
          width: 34, height: 34, borderRadius: 10, flexShrink: 0,
          background: canSend ? 'var(--accent)' : 'var(--bg-hover)',
          color: canSend ? '#fff' : 'var(--text-3)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 16, transition: 'all 0.15s',
        }}>
          {sending ? <span className="spinner" style={{ width: 14, height: 14, borderTopColor: '#fff' }} /> : '↑'}
        </button>
      </div>
      <div style={{ fontSize: 11, color: 'var(--text-3)', textAlign: 'center', marginTop: 8 }}>
        Enter to send · Shift+Enter for new line
      </div>
    </div>
  )
})

// ── Header ───────────────────────────────────────────────────────────────────

function IncidentHeader({ inc, onResolve, onAck, onRefresh }) {
  const [busy, setBusy] = useState(false)
  const act = async fn => { setBusy(true); try { await fn() } finally { setBusy(false) } }
  return (
    <div style={{
      padding: '14px 32px', borderBottom: '1px solid var(--border)',
      display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0,
    }}>
      <span className={`sev sev-${inc.severity}`}>{inc.severity}</span>
      <div style={{ flex: 1, overflow: 'hidden' }}>
        <div style={{ fontWeight: 600, fontSize: 14, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {inc.title || inc.service}
        </div>
        <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 1 }}>
          {inc.service} · {inc.incident_id?.slice(0, 20)}
        </div>
      </div>
      <span className={`status status-${inc.status}`}>{inc.status}</span>
      {inc.status === 'OPEN' && <>
        <GhostBtn disabled={busy} onClick={() => act(() => onAck(inc.incident_id))}>Acknowledge</GhostBtn>
        <GhostBtn disabled={busy} onClick={() => act(() => onResolve(inc.incident_id))}>Resolve</GhostBtn>
      </>}
      <GhostBtn onClick={onRefresh} style={{ padding: '5px 9px' }}>↻</GhostBtn>
    </div>
  )
}

function GhostBtn({ children, onClick, disabled, style: sx }) {
  return (
    <button onClick={onClick} disabled={disabled} style={{
      padding: '5px 12px', borderRadius: 8, fontSize: 12, fontWeight: 500,
      background: 'transparent', color: 'var(--text-2)', border: '1px solid var(--border)',
      transition: 'all 0.15s', opacity: disabled ? 0.5 : 1, ...sx,
    }}
      onMouseEnter={e => { if (!disabled) e.currentTarget.style.background = 'var(--bg-hover)' }}
      onMouseLeave={e => { if (!disabled) e.currentTarget.style.background = 'transparent' }}
    >{children}</button>
  )
}

// ── Placeholder screens ──────────────────────────────────────────────────────

function Welcome() {
  return (
    <div style={{
      flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center',
      justifyContent: 'center', gap: 16, color: 'var(--text-3)', padding: 40,
    }}>
      <div style={{ fontSize: 52 }}>🛡️</div>
      <div style={{ fontSize: 18, fontWeight: 600, color: 'var(--text-2)' }}>Sentinel</div>
      <div style={{ fontSize: 14, textAlign: 'center', maxWidth: 360, lineHeight: 1.7 }}>
        Select an incident from the sidebar to view the AI root cause analysis,
        or click <b>+ New Incident</b> to fire a demo.
      </div>
      <div style={{ display: 'flex', gap: 24, marginTop: 8, fontSize: 12, color: 'var(--text-3)' }}>
        <span>🚨 P1–P4 severity</span>
        <span>🤖 AI RCA</span>
        <span>📋 Auto-runbook</span>
        <span>🔍 Code context</span>
      </div>
    </div>
  )
}

function CenteredSpinner() {
  return (
    <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <span className="spinner" style={{ width: 24, height: 24 }} />
    </div>
  )
}

function ErrorState({ msg }) {
  return (
    <div style={{
      flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
      flexDirection: 'column', gap: 10, color: 'var(--text-2)',
    }}>
      <div style={{ fontSize: 28 }}>⚠️</div>
      <div style={{ fontSize: 13 }}>{msg}</div>
    </div>
  )
}

// ── Main ChatView ────────────────────────────────────────────────────────────

export default function ChatView({ incidentId, onResolve, onAck }) {
  const [inc, setInc]       = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]   = useState(null)
  const [chatMsgs, setChatMsgs] = useState([])
  const [typing, setTyping] = useState(false)
  const [question, setQuestion] = useState('')
  const [sending, setSending] = useState(false)
  const bottomRef = useRef(null)
  const inputRef  = useRef(null)
  const pollRef   = useRef(null)

  useEffect(() => {
    if (!incidentId) return
    setLoading(true); setError(null); setChatMsgs([])
    clearInterval(pollRef.current)
    load(incidentId)
    inputRef.current?.focus()
    return () => clearInterval(pollRef.current)
  }, [incidentId])

  async function load(id) {
    try {
      const data = await api.incident(id)
      setInc(data); setLoading(false)
      if (data.ai_pending) {
        pollRef.current = setInterval(async () => {
          const fresh = await api.incident(id).catch(() => null)
          if (!fresh) return
          setInc(fresh)
          if (!fresh.ai_pending) clearInterval(pollRef.current)
        }, 5000)
      }
    } catch (e) { setError(e.message); setLoading(false) }
  }

  useEffect(() => {
    setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: 'smooth' }), 60)
  }, [inc, chatMsgs, typing])

  async function send() {
    if (!question.trim() || sending) return
    const q = question.trim()
    setQuestion('')
    setChatMsgs(prev => [...prev, { role: 'user', text: q, id: Date.now() }])
    setTyping(true); setSending(true)
    try {
      const resp = await api.chat(incidentId, q)
      setChatMsgs(prev => [...prev, { role: 'ai', text: resp.answer || resp.text || '…', id: Date.now()+1 }])
    } catch (e) {
      setChatMsgs(prev => [...prev, { role: 'ai', text: `Error: ${e.message}`, id: Date.now()+1 }])
    } finally { setTyping(false); setSending(false); inputRef.current?.focus() }
  }

  if (!incidentId) return <Welcome />
  if (loading) return <CenteredSpinner />
  if (error) return <ErrorState msg={error} />

  const rcaMd     = buildRcaMd(inc)
  const runbookMd = buildRunbookMd(inc)
  const codeMd    = buildCodeMd(inc)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <IncidentHeader inc={inc} onResolve={onResolve} onAck={onAck} onRefresh={() => load(incidentId)} />

      <div style={{ flex: 1, overflowY: 'auto', padding: '24px 0' }}>
        <div style={{
          maxWidth: 800, margin: '0 auto', padding: '0 32px',
          display: 'flex', flexDirection: 'column', gap: 20,
        }}>
          {/* Incident opened */}
          <AIMessage icon="🚨">
            {`Incident opened: **${inc.title || inc.service}**\n\nService **${inc.service}** · Severity **${inc.severity}** · source: ${inc.source || 'manual'}`}
          </AIMessage>

          {/* RCA */}
          {inc.ai_pending ? (
            <><Divider label="AI analysis in progress" /><TypingIndicator /></>
          ) : rcaMd ? (
            <><Divider label={`AI analysis · ${inc.rca_source || 'llm'}`} /><AIMessage>{rcaMd}</AIMessage></>
          ) : null}

          {/* Runbook */}
          {runbookMd && !inc.ai_pending && (
            <AIMessage icon="📋">{`**Remediation runbook**\n\n${runbookMd}`}</AIMessage>
          )}

          {/* Code context */}
          {codeMd && !inc.ai_pending && (
            <AIMessage icon="🔍">{`**Relevant source code**\n\n${codeMd}`}</AIMessage>
          )}

          {/* Chat history */}
          {chatMsgs.length > 0 && <Divider label="follow-up" />}
          {chatMsgs.map(m =>
            m.role === 'user'
              ? <UserMessage key={m.id}>{m.text}</UserMessage>
              : <AIMessage key={m.id}>{m.text}</AIMessage>
          )}
          {typing && <TypingIndicator />}
          <div ref={bottomRef} />
        </div>
      </div>

      <InputBar
        ref={inputRef}
        value={question}
        onChange={setQuestion}
        onSend={send}
        sending={sending}
        disabled={!!inc.ai_pending}
        placeholder={inc.ai_pending ? 'Waiting for AI analysis…' : `Ask about ${inc.service}…`}
      />
    </div>
  )
}
