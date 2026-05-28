import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

export function AIMessage({ children, icon = '🤖' }) {
  return (
    <div style={{
      display: 'flex', gap: 14, maxWidth: 760, width: '100%',
      padding: '4px 0',
    }}>
      <div style={{
        width: 32, height: 32, borderRadius: '50%', background: 'var(--accent-dim)',
        border: '1px solid rgba(232,145,74,0.3)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 16, flexShrink: 0, marginTop: 2,
      }}>{icon}</div>
      <div style={{ flex: 1, paddingTop: 4 }}>
        {typeof children === 'string' ? (
          <div className="md-content">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
          </div>
        ) : children}
      </div>
    </div>
  )
}

export function UserMessage({ children }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'flex-end', padding: '4px 0',
    }}>
      <div style={{
        background: 'var(--bg-surface)', borderRadius: '18px 18px 4px 18px',
        padding: '10px 16px', maxWidth: 560,
        fontSize: 14, lineHeight: 1.6, color: 'var(--text)',
        border: '1px solid var(--border)',
      }}>
        {children}
      </div>
    </div>
  )
}

export function TypingIndicator() {
  return (
    <AIMessage>
      <div className="typing-dots">
        <span /><span /><span />
      </div>
    </AIMessage>
  )
}

export function Divider({ label }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12, padding: '8px 0',
    }}>
      <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
      {label && <span style={{ fontSize: 11, color: 'var(--text-3)', whiteSpace: 'nowrap' }}>{label}</span>}
      <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
    </div>
  )
}
