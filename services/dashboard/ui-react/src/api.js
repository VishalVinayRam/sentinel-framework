const API_KEY = window.__SENTINEL_API_KEY__ || ''

const headers = () => ({
  'Content-Type': 'application/json',
  ...(API_KEY ? { 'X-API-Key': API_KEY } : {}),
})

async function req(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: headers(),
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

export const api = {
  incidents: ()            => req('GET',  '/api/incidents'),
  incident:  (id)          => req('GET',  `/api/incidents/${id}`),
  stats:     ()            => req('GET',  '/api/stats'),
  fire:      (svc, sev)    => req('POST', '/api/demo/fire', { service: svc, severity: sev }),
  resolve:   (id)          => req('POST', `/api/incidents/${id}/resolve`),
  acknowledge: (id, who)   => req('POST', `/api/incidents/${id}/acknowledge`, { assigned_to: who }),
  chat:      (id, q)       => req('POST', `/api/incidents/${id}/chat`, { question: q }),
  integrations: ()         => req('GET',  '/api/integrations/status'),
}
