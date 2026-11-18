async function req(path, opts = {}) {
  const r = await fetch('/api' + path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  })
  const text = await r.text()
  let data = null
  try { data = text ? JSON.parse(text) : null } catch { data = text }
  if (!r.ok) {
    const detail = (data && data.detail) || text || r.statusText
    const message = typeof detail === 'string' ? detail : (detail.message || JSON.stringify(detail))
    const err = new Error(message)
    err.status = r.status
    err.payload = typeof detail === 'object' ? detail : null
    throw err
  }
  return data
}

export const api = {
  dashboard: () => req('/dashboard'),
  campaigns: () => req('/campaigns'),
  events: (day) => req('/events' + (day ? `?business_day=${encodeURIComponent(day)}` : '')),
  addEvent: (body) => req('/events', { method: 'POST', body: JSON.stringify(body) }),
  facts: (day) => req('/facts' + (day ? `?business_day=${encodeURIComponent(day)}` : '')),
  preview: (day) => req('/redeem/preview' + (day ? `?business_day=${encodeURIComponent(day)}` : '')),
  seal: (body) => req('/redeem/seal', { method: 'POST', body: JSON.stringify(body) }),
  batches: () => req('/batches'),
  batchDetail: (id) => req(`/batches/${id}`),
  chainBreaks: () => req('/chain/breaks'),
  chainRuns: () => req('/chain/runs'),
  chainRun: (id) => req(`/chain/runs/${id}`),
  getDay: () => req('/settings/day'),
  setDay: (business_day) => req('/settings/day', { method: 'PUT', body: JSON.stringify({ business_day }) }),
  getChainPolicy: () => req('/settings/chain-policy'),
  setChainPolicy: (policy) => req('/settings/chain-policy', { method: 'PUT', body: JSON.stringify({ policy }) }),
}
