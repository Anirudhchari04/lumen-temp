// REST client for Lumen backend. Uses the Entra-issued JWT from auth.js.

import { getStoredToken } from './auth.js'

async function authed(path, init = {}) {
  const token = getStoredToken()
  if (!token) {
    const err = new Error('Not authenticated')
    err.code = 'NO_AUTH'
    throw err
  }
  const headers = {
    'Content-Type': 'application/json',
    ...(init.headers || {}),
    Authorization: 'Bearer ' + token,
  }
  const res = await fetch(path, { ...init, headers })
  if (res.status === 401) {
    localStorage.removeItem('lumen.token')
    localStorage.removeItem('lumen.user')
    const err = new Error('Session expired')
    err.code = 'NO_AUTH'
    throw err
  }
  if (!res.ok) {
    const txt = await res.text().catch(() => '')
    throw new Error(`${init.method || 'GET'} ${path} -> ${res.status} ${txt}`)
  }
  if (res.status === 204) return null
  return res.json()
}

export const api = {
  profile:         () => authed('/auth/profile'),
  agents:          () => authed('/chat/tas'),

  // graph_token is optional — caller acquires it via getGraphToken() when needed
  lumenChat:       (message, thread_id, graph_token) =>
    authed('/chat', { method: 'POST', body: JSON.stringify({ message, thread_id, graph_token: graph_token || null }) }),
  // Lumen v2 (Magentic-One) — same request shape, different orchestrator.
  lumenChatV2:     (message, thread_id, graph_token) =>
    authed('/v2/chat', { method: 'POST', body: JSON.stringify({ message, thread_id, graph_token: graph_token || null }) }),
  taChat:          (taId, message, thread_id) =>
    authed(`/chat/ta/${taId}`, { method: 'POST', body: JSON.stringify({ message, thread_id }) }),

  lumenState:      (taId) =>
    authed(`/lumen/state${taId ? `?ta_id=${encodeURIComponent(taId)}` : ''}`),
  threads:         (channel) => authed(`/chat/threads/${encodeURIComponent(channel)}`),
  thread:          (id) => authed(`/chat/thread/${encodeURIComponent(id)}`),
  newThread:       (channel) => authed(`/chat/threads/${encodeURIComponent(channel)}`, { method: 'POST' }),

  peers:           () => authed('/lumen/peers'),
  comparePeer:     (peerId) => authed(`/lumen/compare/${peerId}`),

  calEvents:       () => authed('/agents/calendar/events'),
  calPrefs:        () => authed('/agents/calendar/preferences'),
  setCalPrefs:     (body) =>
    authed('/agents/calendar/preferences', { method: 'POST', body: JSON.stringify(body) }),
  calNotifs:       () => authed('/agents/calendar/notifications?unread_only=false'),
  markNotifs:      (ids) =>
    authed('/agents/calendar/notifications/read', { method: 'POST', body: JSON.stringify({ ids }) }),
  scheduleNatural: (message) =>
    authed('/agents/calendar/schedule-natural', { method: 'POST', body: JSON.stringify({ message }) }),
  deleteEvent:     (id) => authed(`/agents/calendar/events/${id}`, { method: 'DELETE' }),
  updateEvent:     (id, status) =>
    authed(`/agents/calendar/events/${id}`, { method: 'PUT', body: JSON.stringify({ status }) }),
  updateEventStatus: (id, status) =>
    authed(`/agents/calendar/events/${id}`, { method: 'PUT', body: JSON.stringify({ status }) }),

  // Study-plan proposal accept
  confirmPlan:     (proposal) =>
    authed('/chat/confirm-plan', { method: 'POST', body: JSON.stringify({ proposal }) }),

  // Owner profile
  myProfile:       () => authed('/lumen/profile'),
  updateProfile:   (patch) =>
    authed('/lumen/profile', { method: 'PUT', body: JSON.stringify(patch) }),

  // Token usage / cost dashboard
  tokenUsage:        () => authed('/lumen/usage/tokens'),
  tokenUsageAll:     () => authed('/lumen/usage/tokens/all'),
  resetTokenSession: () => authed('/lumen/usage/tokens/reset-session', { method: 'POST' }),

  // Peer messaging (LITP message on the social channel)
  sendPeerMessage: (to_id, message) =>
    authed('/lumen/message', { method: 'POST', body: JSON.stringify({ to_id, message }) }),
  peerMessages:    () => authed('/lumen/messages'),

  // Private-info requests (Lumen-mediated)
  requestInfo:     (to_id, field, reason) =>
    authed('/lumen/info-request', { method: 'POST', body: JSON.stringify({ to_id, field, reason }) }),
  pendingInfoRequests: () => authed('/lumen/info-requests/pending'),
  respondInfoRequest:  (request_id, accept) =>
    authed('/lumen/info-request/respond', { method: 'POST', body: JSON.stringify({ request_id, accept }) }),
  infoRequestHistory:  () => authed('/lumen/info-requests/history'),

  // Privacy / consent / audit
  updateSocial:  (body) =>
    authed('/lumen/social-settings', { method: 'PUT', body: JSON.stringify(body) }),
  consents:      () => authed('/lumen/consent'),
  revokeConsent: (grantee, action) =>
    authed(`/lumen/consent?grantee=${encodeURIComponent(grantee)}&action=${encodeURIComponent(action)}`, { method: 'DELETE' }),
  audit:         () => authed('/lumen/audit'),

  // Unified proactive feed for the NotificationsBell
  notificationsFeed: () => authed('/lumen/notifications/feed'),
  markFeedRead:      (type, ids) =>
    authed('/lumen/notifications/mark-read', { method: 'POST', body: JSON.stringify({ type, ids }) }),

  // Communication Agent
  commInbox:    (from) => authed(`/lumen/comm/inbox${from ? `?from_filter=${encodeURIComponent(from)}` : ''}`),
  commOutbox:   () => authed('/lumen/comm/outbox'),
  commMarkRead: (ids) =>
    authed('/lumen/comm/read', { method: 'POST', body: JSON.stringify({ ids }) }),
  commSendReal: (draft, graphToken, provider = '') =>
    authed('/lumen/comm/send-real', { method: 'POST', body: JSON.stringify({ draft, graph_token: graphToken, provider }) }),

  // Send via WorkIQ MCP — no MSAL token needed, uses Entra token passthrough
  commSendWorkIQ: (draft) =>
    authed('/lumen/comm/send-workiq', { method: 'POST', body: JSON.stringify({ draft }) }),

  // UX Agent
  uxGet:    () => authed('/lumen/ux'),
  uxSet:    (preset_id) => authed('/lumen/ux', { method: 'PUT', body: JSON.stringify({ preset_id }) }),

  // A2UI Actions (button clicks, form submissions)
  a2uiAction: (action, data = {}, threadId = '') =>
    authed('/a2ui/action', { method: 'POST', body: JSON.stringify({ action, data, thread_id: threadId }) }),

  // Widget Zone
  widgetsGet:      () => authed('/lumen/widgets'),
  widgetAdd:       (template) => authed('/lumen/widgets', { method: 'POST', body: JSON.stringify({ template }) }),
  widgetRemove:    (id) => authed(`/lumen/widgets/${id}`, { method: 'DELETE' }),
  widgetTemplates: () => authed('/lumen/widget-templates'),

  // AG-UI streaming (returns a ReadableStream Response)
  aguiChat: async (message, threadId) => {
    const token = localStorage.getItem('lumen.token') || localStorage.getItem('lumen_token')
    const resp = await fetch('/ag-ui/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ message, thread_id: threadId }),
    })
    return resp
  },

  // File-message: AI image analysis or portfolio upload
  fileMessage: (file, message, taId, threadId) => {
    const token = localStorage.getItem('lumen.token') || localStorage.getItem('lumen_token')
    const form = new FormData()
    form.append('file', file)
    form.append('message', message || '')
    if (taId) form.append('ta_id', taId)
    if (threadId) form.append('thread_id', threadId)
    return fetch('/chat/file-message', {
      method: 'POST',
      headers: { Authorization: 'Bearer ' + token },
      body: form,
    }).then(r => r.ok ? r.json() : r.text().then(t => Promise.reject(new Error(t))))
  },

  // Portfolio Agent
  portfolioStatus:     () => authed('/portfolio/status'),
  portfolioDisconnect: () => authed('/portfolio/disconnect', { method: 'POST' }),
  portfolioInit:    () => authed('/portfolio/init', { method: 'POST' }),
  portfolioFiles:   (path = '') => authed(`/portfolio/files${path ? `?path=${encodeURIComponent(path)}` : ''}`),
  portfolioGetFile: (path) => authed(`/portfolio/files/get?path=${encodeURIComponent(path)}`),
  portfolioDelete:  (path) =>
    authed('/portfolio/files', { method: 'DELETE', body: JSON.stringify({ path }) }),
  portfolioUpload:  (file, taHint = '', studentName = '') => {
    const token = localStorage.getItem('lumen.token') || localStorage.getItem('lumen_token')
    const form = new FormData()
    form.append('file', file)
    form.append('ta_hint', taHint)
    form.append('student_name', studentName)
    return fetch('/portfolio/upload', {
      method: 'POST',
      headers: { Authorization: 'Bearer ' + token },
      body: form,
    }).then(r => r.ok ? r.json() : r.text().then(t => Promise.reject(new Error(t))))
  },
  // Staged commit flow — stage files, review, then commit on click.
  portfolioStage: (file, taHint = '', studentName = '') => {
    const token = localStorage.getItem('lumen.token') || localStorage.getItem('lumen_token')
    const form = new FormData()
    form.append('file', file)
    form.append('ta_hint', taHint)
    form.append('student_name', studentName)
    return fetch('/portfolio/stage', {
      method: 'POST',
      headers: { Authorization: 'Bearer ' + token },
      body: form,
    }).then(r => r.ok ? r.json() : r.text().then(t => Promise.reject(new Error(t))))
  },
  portfolioStaged:      () => authed('/portfolio/staged'),
  portfolioUnstage:     (id) => authed(`/portfolio/staged/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  portfolioClearStaged: () => authed('/portfolio/staged', { method: 'DELETE' }),
  portfolioCommit:      (message = '') =>
    authed('/portfolio/commit', { method: 'POST', body: JSON.stringify({ message: message || null }) }),
  portfolioActions:     (limit = 10) => authed(`/portfolio/actions?limit=${limit}`),
  portfolioFileContent: (path) => authed(`/portfolio/file-content?path=${encodeURIComponent(path)}`),

  // Coding TA — generate artifacts that auto-save to the portfolio
  codingGenerate:  (prompt, artifact_type = '') =>
    authed('/coding-ta/generate', { method: 'POST', body: JSON.stringify({ prompt, artifact_type: artifact_type || null }) }),
  codingArtifacts: () => authed('/coding-ta/artifacts'),

  // ── Shiksha (Ekalaiva) read-only bridge ──────────────────────
  shikshaAgents:   () => authed('/shiksha/agents'),
  shikshaProgress: () => authed('/shiksha/progress'),
  shikshaThreads:  (agentId = null) =>
    authed(`/shiksha/threads${agentId ? `?agent_id=${encodeURIComponent(agentId)}` : ''}`),

  // ── External Graph token (college / personal Outlook) ────────
  externalGraphStatus:     () => authed('/auth/external-graph-token-status'),
  seedExternalGraphToken:  (access_token) =>
    authed('/auth/external-graph-token', { method: 'POST', body: JSON.stringify({ access_token }) }),
  clearExternalGraphToken: () =>
    authed('/auth/external-graph-token', { method: 'DELETE' }),
}
