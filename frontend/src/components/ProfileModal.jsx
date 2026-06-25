import { useEffect, useState, useCallback } from 'react'
import { api } from '../lib/api.js'
import { IconClose } from './icons.jsx'
import LoadingDots from './LoadingDots.jsx'
import { notionSignIn, getNotionStatus, notionDisconnect,
         googleConnect, getGoogleStatus, googleDisconnect,
         githubConnect } from '../lib/auth.js'

// Editable profile modal with public/private toggles per field.
// "Private" fields are only shared with a peer after they send an info-request
// and you approve it — at which point your Lumen DMs the value to their Lumen.

const PRIVATE_FIELDS = [
  { key: 'dob',        label: 'Date of birth',  placeholder: '1999-04-21' },
  { key: 'address',    label: 'Address',        placeholder: 'City, country' },
  { key: 'occupation', label: 'Occupation',     placeholder: 'Student · IIIT' },
  { key: 'phone',      label: 'Phone',          placeholder: '+91 …' },
]

export default function ProfileModal({ open, onClose, onSaved }) {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  const [p, setP] = useState({
    name: '', bio: '', expertise: '', interests: '',
    dob: '', address: '', occupation: '', phone: '',
    visibility: {
      bio: 'public', expertise: 'public', interests: 'public',
      dob: 'private', address: 'private', occupation: 'private', phone: 'private',
    },
    preferences: { pace: 'moderate', explanation: 'detailed' },
  })

  // Shareable Lumen link state
  const [username, setUsernameState] = useState('')
  const [shareUrl, setShareUrl]      = useState('')
  const [usernameInput, setUsernameInput] = useState('')
  const [shareBusy, setShareBusy]    = useState(false)
  const [shareErr, setShareErr]      = useState('')
  const [copied, setCopied]          = useState(false)

  // Lumen skills state
  const [skills, setSkills]          = useState([])
  const [agentsList, setAgentsList]  = useState([])
  const [skillName, setSkillName]    = useState('')
  const [skillAgent, setSkillAgent]  = useState('')
  const [skillBusy, setSkillBusy]    = useState(false)
  const [skillErr, setSkillErr]      = useState('')

  // GitHub portfolio state
  const [ghOwner, setGhOwner]             = useState('')
  const [ghStatus, setGhStatus]           = useState(null)
  const [ghDisconnecting, setGhDisconnecting] = useState(false)
  const [ghConnecting, setGhConnecting]   = useState(false)
  const [ghErr, setGhErr]                 = useState('')

  // IMAP/SMTP email connection state (managed via Comms Agent chat)
  const [emailConnected, setEmailConnected] = useState(false)
  const [emailAddress, setEmailAddress]     = useState('')

  // Notion connection state
  const [notionConnected, setNotionConnected] = useState(false)
  const [notionWorkspace, setNotionWorkspace] = useState('')
  const [notionBusy, setNotionBusy]           = useState(false)
  const [notionErr, setNotionErr]             = useState('')

  // Google (Drive + Gmail + Calendar) connection state
  const [googleConnected, setGoogleConnected] = useState(false)
  const [googleEmail, setGoogleEmail]         = useState('')
  const [googleGmail, setGoogleGmail]         = useState(false)
  const [googleDrive, setGoogleDrive]         = useState(false)
  const [googleCal, setGoogleCal]             = useState(false)
  const [googleConsent, setGoogleConsent]     = useState('always')
  const [googleBusy, setGoogleBusy]           = useState(false)
  const [googleErr, setGoogleErr]             = useState('')
  const [emailDisconnecting, setEmailDisconnecting] = useState(false)

  const loadGhStatus = useCallback(async () => {
    try {
      const s = await api.portfolioStatus()
      setGhStatus(s)
      if (s?.owner) setGhOwner(s.owner)
    } catch { setGhStatus(null) }
  }, [])

  useEffect(() => {
    if (!open) return
    setErr(''); setLoading(true)
    Promise.all([
      api.myProfile().then(r => {
        setP(cur => ({
          ...cur,
          name: r?.name || '',
          bio: r?.bio || '',
          expertise: r?.expertise || '',
          interests: r?.interests || '',
          dob: r?.dob || '',
          address: r?.address || '',
          occupation: r?.occupation || '',
          phone: r?.phone || '',
          visibility: { ...cur.visibility, ...(r?.visibility || {}) },
          preferences: { ...cur.preferences, ...(r?.preferences || {}) },
        }))
      }),
      api.myShare().then(s => {
        setUsernameState(s?.username || '')
        setShareUrl(s?.share_url || '')
        setUsernameInput(s?.username || '')
      }).catch(() => {}),
      api.mySkills().then(s => {
        setSkills(s?.skills || [])
        setAgentsList(s?.agents || [])
      }).catch(() => {}),
      loadGhStatus(),
      fetch('/lumen/email/connection-status', {
        headers: { Authorization: `Bearer ${localStorage.getItem('lumen.token')}` }
      }).then(r => r.ok ? r.json() : null).then(r => {
        setEmailConnected(!!r?.connected)
        setEmailAddress(r?.email || '')
      }).catch(() => {}),
      getNotionStatus().then(s => {
        setNotionConnected(!!s?.connected)
        setNotionWorkspace(s?.workspace_name || '')
      }).catch(() => {}),
      getGoogleStatus().then(s => {
        setGoogleConnected(!!s?.connected)
        setGoogleEmail(s?.email || '')
        setGoogleGmail(!!s?.gmail)
        setGoogleDrive(!!s?.drive)
        setGoogleCal(!!s?.calendar)
        setGoogleConsent(s?.consent || 'always')
      }).catch(() => {}),
    ])
      .catch(e => setErr(e?.message || 'Failed to load profile'))
      .finally(() => setLoading(false))
  }, [open, loadGhStatus])

  if (!open) return null

  const disconnectGitHub = async () => {
    setGhDisconnecting(true); setGhErr('')
    try {
      await api.portfolioDisconnect()
      setGhStatus(null)
      setGhOwner('')
    } catch (e) {
      setGhErr(e?.message || 'Disconnect failed')
    } finally { setGhDisconnecting(false) }
  }

  const connectGitHub = async () => {
    setGhErr('')
    setGhConnecting(true)
    try {
      const r = await githubConnect()
      setGhStatus(r?.portfolio || { connected: true, owner: r?.owner })
      setGhOwner(r?.owner || r?.portfolio?.owner || '')
    } catch (e) {
      setGhErr(e?.message || 'GitHub connect failed')
    } finally {
      setGhConnecting(false)
    }
  }

  const handleGoogleConnect = async () => {
    setGoogleBusy(true); setGoogleErr('')
    try {
      const r = await googleConnect('always')   // Profile connect = persistent
      setGoogleConnected(!!r?.connected)
      setGoogleEmail(r?.email || '')
      const scopes = r?.scopes || ''
      setGoogleGmail(scopes.includes('gmail'))
      setGoogleDrive(scopes.includes('drive'))
      setGoogleCal(scopes.includes('calendar'))
      setGoogleConsent(r?.consent || 'always')
    } catch (e) { setGoogleErr(e?.message || 'Connect failed') }
    finally { setGoogleBusy(false) }
  }

  const handleGoogleDisconnect = async () => {
    setGoogleBusy(true); setGoogleErr('')
    try {
      await googleDisconnect()
      setGoogleConnected(false); setGoogleEmail('')
      setGoogleGmail(false); setGoogleDrive(false); setGoogleCal(false)
      setGoogleConsent('always')
    } catch (e) { setGoogleErr(e?.message || 'Disconnect failed') }
    finally { setGoogleBusy(false) }
  }

  const handleNotionConnect = async () => {
    setNotionBusy(true); setNotionErr('')
    try {
      const r = await notionSignIn()
      setNotionConnected(!!r?.connected)
      setNotionWorkspace(r?.workspace_name || '')
    } catch (e) { setNotionErr(e?.message || 'Connect failed') }
    finally { setNotionBusy(false) }
  }

  const handleNotionDisconnect = async () => {
    setNotionBusy(true); setNotionErr('')
    try {
      await notionDisconnect()
      setNotionConnected(false); setNotionWorkspace('')
    } catch (e) { setNotionErr(e?.message || 'Disconnect failed') }
    finally { setNotionBusy(false) }
  }

  // Explicit user-initiated Graph consent — safe to popup here (not inside another popup)
  const disconnectEmail = async () => {
    setEmailDisconnecting(true)
    try {
      const token = localStorage.getItem('lumen.token')
      await fetch('/lumen/email/connect', {
        method: 'DELETE',
        headers: { 'Authorization': `Bearer ${token}` },
      })
      setEmailConnected(false)
      setEmailAddress('')
    } catch {}
    finally { setEmailDisconnecting(false) }
  }

  const saveUsername = async () => {
    const u = (usernameInput || '').trim().toLowerCase()
    if (!u || u === username) return
    setShareBusy(true); setShareErr('')
    try {
      const r = await api.setUsername(u)
      setUsernameState(r?.username || u)
      setShareUrl(r?.share_url || '')
      setUsernameInput(r?.username || u)
    } catch (e) {
      const m = (e?.message || '')
      setShareErr(m.includes('409') || m.toLowerCase().includes('taken')
        ? 'That username is already taken'
        : 'Use 3\u201330 lowercase letters, numbers or hyphens (not a reserved word).')
    } finally { setShareBusy(false) }
  }

  const copyShare = async () => {
    try { await navigator.clipboard.writeText(shareUrl); setCopied(true); setTimeout(() => setCopied(false), 1500) } catch {}
  }

  const addSkill = async () => {
    const n = (skillName || '').trim()
    if (!n) return
    setSkillBusy(true); setSkillErr('')
    try {
      const r = await api.addSkill(n, '', skillAgent || null)
      setSkills(r?.skills || [])
      setSkillName(''); setSkillAgent('')
    } catch (e) {
      setSkillErr((e?.message || '').includes('400') ? 'Could not add that skill' : 'Add failed')
    } finally { setSkillBusy(false) }
  }

  const removeSkill = async (name) => {
    try { const r = await api.removeSkill(name); setSkills(r?.skills || []) } catch {}
  }

  const save = async () => {
    setSaving(true); setErr('')
    try {
      const updated = await api.updateProfile(p)
      // Also apply UX preset if it was changed
      if (p.preferences?.ux_preset) {
        await api.uxSet(p.preferences.ux_preset)
      }
      onSaved?.(updated)
      onClose?.()
      // Reload page to apply visual preset changes
      if (p.preferences?.ux_preset) {
        window.location.reload()
      }
    } catch (e) {
      setErr(e?.message || 'Failed to save')
    } finally { setSaving(false) }
  }

  const patch = (k, v) => setP(cur => ({ ...cur, [k]: v }))
  const patchPref = (k, v) => setP(cur => ({ ...cur, preferences: { ...cur.preferences, [k]: v } }))
  const toggleVis = (k) => setP(cur => ({
    ...cur,
    visibility: { ...cur.visibility, [k]: cur.visibility?.[k] === 'public' ? 'private' : 'public' },
  }))

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div className="relative w-full max-w-md mx-4 bg-white rounded-card border border-beige-200 overflow-hidden flex flex-col max-h-[88vh]">
        <div className="px-5 py-4 flex items-center justify-between border-b border-beige-200 shrink-0">
          <div>
            <h3 className="text-[15px] font-medium text-ink">Your Lumen profile</h3>
            <p className="text-2xs text-ink-muted mt-0.5">Your Lumen shares what you mark <b>public</b>. Private fields require peer approval.</p>
          </div>
          <button onClick={onClose} className="w-8 h-8 rounded-inner hover:bg-beige-100 flex items-center justify-center"><IconClose /></button>
        </div>
        <div className="p-5 flex flex-col gap-4 overflow-y-auto">
          {loading && <LoadingDots />}
          {!loading && (
            <>
              <Field label="Display name">
                <input value={p.name} onChange={e => patch('name', e.target.value)}
                  className="w-full rounded-inner bg-beige-50 border border-beige-200 px-3 py-2 text-[13px] outline-none focus:border-amber" />
              </Field>

              {/* Shareable Lumen link */}
              <div className="pt-3 border-t border-beige-100">
                <div className="text-[12px] font-medium text-ink">Your Lumen link</div>
                <div className="text-2xs text-ink-muted mt-0.5">
                  Share this so others can reach your Lumen. Your own link opens your Lumen;
                  someone else opening it lands in peer-to-peer chat with you.
                </div>
                {shareUrl && (
                  <div className="mt-2 flex items-center gap-2">
                    <input readOnly value={shareUrl} onFocus={e => e.target.select()}
                      className="flex-1 rounded-inner bg-beige-50 border border-beige-200 px-3 py-2 text-[12px] text-ink-soft outline-none" />
                    <button onClick={copyShare}
                      className="px-3 py-2 rounded-inner bg-ink text-white text-[12px] shrink-0">
                      {copied ? 'Copied' : 'Copy'}
                    </button>
                  </div>
                )}
                <div className="mt-2 flex items-center gap-2">
                  <div className="flex-1 flex items-center rounded-inner bg-beige-50 border border-beige-200 px-3">
                    <span className="text-[12px] text-ink-muted select-none">@</span>
                    <input value={usernameInput}
                      onChange={e => setUsernameInput(e.target.value)}
                      placeholder="choose-a-username"
                      className="flex-1 bg-transparent py-2 text-[13px] outline-none" />
                  </div>
                  <button onClick={saveUsername}
                    disabled={shareBusy || !usernameInput.trim() || usernameInput.trim().toLowerCase() === username}
                    className="px-3 py-2 rounded-inner bg-ink text-white text-[12px] shrink-0 disabled:opacity-40">
                    {shareBusy ? '\u2026' : (username ? 'Update' : 'Claim')}
                  </button>
                </div>
                {shareErr && <div className="text-2xs text-rose-600 mt-1">{shareErr}</div>}
              </div>

              {/* Lumen skills */}
              <div className="pt-3 border-t border-beige-100">
                <div className="text-[12px] font-medium text-ink">Skills</div>
                <div className="text-2xs text-ink-muted mt-0.5">
                  Capabilities your Lumen advertises — optionally powered by one of its sub-agents.
                </div>
                {skills.length > 0 && (
                  <div className="mt-2 flex flex-col gap-1.5">
                    {skills.map(s => (
                      <div key={s.id || s.name}
                        className="flex items-center gap-2 rounded-inner bg-beige-50 border border-beige-200 px-3 py-1.5">
                        <div className="flex-1 min-w-0">
                          <span className="text-[13px] text-ink">{s.name}</span>
                          {s.agent && <span className="ml-2 text-2xs text-ink-muted">via {s.agent}</span>}
                        </div>
                        <button onClick={() => removeSkill(s.name)}
                          className="text-2xs text-rose-600 hover:underline shrink-0">remove</button>
                      </div>
                    ))}
                  </div>
                )}
                <div className="mt-2 flex items-center gap-2">
                  <input value={skillName} onChange={e => setSkillName(e.target.value)}
                    placeholder="add a skill…"
                    className="flex-1 rounded-inner bg-beige-50 border border-beige-200 px-3 py-2 text-[13px] outline-none focus:border-amber" />
                  <select value={skillAgent} onChange={e => setSkillAgent(e.target.value)}
                    className="rounded-inner bg-beige-50 border border-beige-200 px-2 py-2 text-[12px] text-ink outline-none focus:border-amber">
                    <option value="">no agent</option>
                    {agentsList.map(a => <option key={a} value={a}>{a}</option>)}
                  </select>
                  <button onClick={addSkill} disabled={skillBusy || !skillName.trim()}
                    className="px-3 py-2 rounded-inner bg-ink text-white text-[12px] shrink-0 disabled:opacity-40">
                    {skillBusy ? '\u2026' : 'Add'}
                  </button>
                </div>
                {skillErr && <div className="text-2xs text-rose-600 mt-1">{skillErr}</div>}
              </div>

              <FieldWithToggle
                label="Short bio" hint="One or two lines about you."
                vis={p.visibility?.bio} onToggle={() => toggleVis('bio')}>
                <textarea value={p.bio} onChange={e => patch('bio', e.target.value)} rows={2}
                  className="w-full rounded-inner bg-beige-50 border border-beige-200 px-3 py-2 text-[13px] outline-none focus:border-amber resize-none" />
              </FieldWithToggle>

              <FieldWithToggle
                label="What you're good at" hint="Comma-separated. Helps peers ask you the right things."
                vis={p.visibility?.expertise} onToggle={() => toggleVis('expertise')}>
                <input value={p.expertise} onChange={e => patch('expertise', e.target.value)}
                  placeholder="algebra, Python basics"
                  className="w-full rounded-inner bg-beige-50 border border-beige-200 px-3 py-2 text-[13px] outline-none focus:border-amber" />
              </FieldWithToggle>

              <FieldWithToggle
                label="Interests" hint="Topics you want to explore."
                vis={p.visibility?.interests} onToggle={() => toggleVis('interests')}>
                <input value={p.interests} onChange={e => patch('interests', e.target.value)}
                  placeholder="graph theory, web dev"
                  className="w-full rounded-inner bg-beige-50 border border-beige-200 px-3 py-2 text-[13px] outline-none focus:border-amber" />
              </FieldWithToggle>

              <div className="mt-1 pt-3 border-t border-beige-100">
                <div className="text-[12px] font-medium text-ink">Private details</div>
                <div className="text-2xs text-ink-muted mt-0.5">
                  Default is <b>private</b> — peers must request these and you approve each time.
                </div>
              </div>

              {PRIVATE_FIELDS.map(f => (
                <FieldWithToggle
                  key={f.key}
                  label={f.label}
                  vis={p.visibility?.[f.key]}
                  onToggle={() => toggleVis(f.key)}>
                  <input value={p[f.key]} onChange={e => patch(f.key, e.target.value)}
                    placeholder={f.placeholder}
                    className="w-full rounded-inner bg-beige-50 border border-beige-200 px-3 py-2 text-[13px] outline-none focus:border-amber" />
                </FieldWithToggle>
              ))}

              <div className="mt-1 pt-3 border-t border-beige-100">
                <div className="text-[12px] font-medium text-ink">Learning preferences</div>
                <div className="text-2xs text-ink-muted mt-0.5">
                  These tell your Lumen & TAs how to adapt to you.
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <Field
                  label="Pace"
                  hint="How fast new material is introduced. Slow = more practice per concept before moving on.">
                  <select value={p.preferences?.pace || 'moderate'} onChange={e => patchPref('pace', e.target.value)}
                    className="w-full rounded-inner bg-beige-50 border border-beige-200 px-2 py-2 text-[13px]">
                    <option value="slow">Slow</option>
                    <option value="moderate">Moderate</option>
                    <option value="fast">Fast</option>
                  </select>
                </Field>
                <Field
                  label="Explanations"
                  hint="Concise = short & to the point. Detailed = examples + why. Socratic = asks you questions first.">
                  <select value={p.preferences?.explanation || 'detailed'} onChange={e => patchPref('explanation', e.target.value)}
                    className="w-full rounded-inner bg-beige-50 border border-beige-200 px-2 py-2 text-[13px]">
                    <option value="concise">Concise</option>
                    <option value="detailed">Detailed</option>
                    <option value="socratic">Socratic</option>
                  </select>
                </Field>
              </div>
              <div className="mt-1 pt-3 border-t border-beige-100">
                <div className="text-[12px] font-medium text-ink">Experience preset</div>
                <div className="text-2xs text-ink-muted mt-0.5">
                  Controls how Lumen and your TAs present information. You can also say "switch to vision mode" in chat.
                </div>
              </div>

              <UxPresetSelector
                activeId={p.preferences?.ux_preset || 'standard'}
                onChange={(id) => patchPref('ux_preset', id)}
              />

              {/* Calendar provider preference */}
              <div className="mt-1 pt-3 border-t border-beige-100">
                <div className="text-[12px] font-medium text-ink">Calendar source</div>
                <div className="text-2xs text-ink-muted mt-0.5">
                  Which calendar Lumen reads/writes when you say "what's on my calendar" or "add an event".
                </div>
              </div>
              <Field
                label="Provider"
                hint="Auto picks the best available — prefers Google Calendar when connected, then Outlook (for Microsoft accounts), then Lumen's internal calendar.">
                <select value={p.preferences?.calendar_provider || 'auto'}
                  onChange={e => patchPref('calendar_provider', e.target.value)}
                  className="w-full rounded-inner bg-beige-50 border border-beige-200 px-2 py-2 text-[13px]">
                  <option value="auto">Auto (Google → Outlook → Lumen)</option>
                  <option value="google">Google Calendar</option>
                  <option value="outlook">Outlook Calendar</option>
                  <option value="lumen">Lumen's internal calendar</option>
                </select>
              </Field>

              {/* ── Connections & permissions ─────────────────────── */}
              <div className="mt-1 pt-3 border-t border-beige-100">
                <div className="text-[12px] font-medium text-ink">Connections & permissions</div>
                <div className="text-2xs text-ink-muted mt-0.5">
                  Allow or disallow what Lumen can access. Change anytime.
                </div>
              </div>

              <div className="flex flex-col gap-2">
                <ConnectionRow
                  icon="🌐"
                  name="Gmail · Drive · Calendar"
                  sub="Read/send mail, Drive docs, calendar events"
                  connected={googleConnected}
                  statusText={
                    (googleEmail || 'Connected')
                    + (googleConsent === 'once' ? ' · this session only' : '')
                    + (() => {
                        const svc = [googleGmail && 'Gmail', googleDrive && 'Drive', googleCal && 'Calendar'].filter(Boolean)
                        return svc.length ? ' · ' + svc.join(', ') : ''
                      })()
                  }
                  busy={googleBusy}
                  onConnect={handleGoogleConnect}
                  onDisconnect={handleGoogleDisconnect}
                  err={googleErr}
                />

                <ConnectionRow
                  icon="📁"
                  name="GitHub Portfolio"
                  sub="Store & manage your learning artifacts"
                  connected={!!ghStatus?.connected}
                  statusText={ghStatus?.owner ? `@${ghStatus.owner}` : 'Connected'}
                  busy={ghConnecting || ghDisconnecting}
                  onConnect={connectGitHub}
                  onDisconnect={disconnectGitHub}
                  err={ghErr}
                >
                  {ghStatus?.connected && ghStatus?.repo_exists && (
                    <a href={ghStatus.repo_url} target="_blank" rel="noopener noreferrer"
                       className="text-[11px] text-blue-600 underline mt-1.5 inline-block">
                      View portfolio repo →
                    </a>
                  )}
                  {ghStatus?.connected && !ghStatus?.repo_exists && (
                    <button type="button"
                      onClick={() => api.portfolioInit().then(loadGhStatus).catch(e => setGhErr(e?.message))}
                      className="text-[11px] text-amber underline text-left mt-1.5 block">
                      Initialize portfolio repo
                    </button>
                  )}
                </ConnectionRow>

                <ConnectionRow
                  icon="📓"
                  name="Notion"
                  sub="Read, create & summarize Notion pages"
                  connected={notionConnected}
                  statusText={notionWorkspace ? `Connected to ${notionWorkspace}` : 'Connected'}
                  busy={notionBusy}
                  onConnect={handleNotionConnect}
                  onDisconnect={handleNotionDisconnect}
                  err={notionErr}
                >
                  {!notionConnected && (
                    <div className="text-2xs text-ink-muted mt-1.5">
                      After allowing, share a page with the Lumen integration (page → ••• → Connections → Lumen).
                    </div>
                  )}
                </ConnectionRow>
              </div>

              {err && <div className="text-[12px] text-rose-600">{err}</div>}
            </>
          )}
        </div>
        <div className="px-5 py-3 border-t border-beige-200 flex justify-end gap-2 bg-beige-50 shrink-0">
          <button onClick={onClose}
            className="text-[13px] px-3 py-1.5 rounded-pill bg-beige-100 border border-beige-300 hover:bg-beige-200 text-ink-soft">
            Cancel
          </button>
          <button onClick={save} disabled={saving || loading}
            className="text-[13px] px-3 py-1.5 rounded-pill bg-amber text-white hover:opacity-90 disabled:opacity-50">
            {saving ? 'Saving…' : 'Save profile'}
          </button>
        </div>
      </div>
    </div>
  )
}

function ConnectionRow({ icon, name, sub, connected, statusText, busy, onConnect, onDisconnect, err, children }) {
  return (
    <div className={[
      'rounded-inner border px-3 py-2.5',
      connected ? 'border-emerald-200 bg-emerald-50/60' : 'border-beige-200 bg-beige-50',
    ].join(' ')}>
      <div className="flex items-center gap-2.5">
        <span className="text-[16px] shrink-0">{icon}</span>
        <div className="flex-1 min-w-0">
          <div className="text-[12.5px] font-medium text-ink flex items-center gap-1.5">
            <span className="truncate">{name}</span>
            <span className={[
              'w-1.5 h-1.5 rounded-full shrink-0',
              connected ? 'bg-emerald-500' : 'bg-beige-300',
            ].join(' ')} />
          </div>
          <div className="text-2xs text-ink-muted truncate">
            {connected ? statusText : (sub || 'Not connected')}
          </div>
        </div>
        {connected ? (
          <button type="button" onClick={onDisconnect} disabled={busy}
            className="text-[11px] px-2.5 py-1 rounded-pill border border-rose-300 text-rose-600 hover:bg-rose-50 disabled:opacity-50 shrink-0">
            {busy ? 'Working…' : 'Disallow'}
          </button>
        ) : (
          <button type="button" onClick={onConnect} disabled={busy}
            className="text-[11px] px-2.5 py-1 rounded-pill bg-ink text-white hover:opacity-90 disabled:opacity-50 shrink-0">
            {busy ? 'Working…' : 'Allow'}
          </button>
        )}
      </div>
      {children}
      {err && <div className="text-[11px] text-rose-600 mt-1.5">{err}</div>}
    </div>
  )
}

function Field({ label, hint, children }) {
  return (
    <label className="block">
      <div className="text-[11.5px] font-medium text-ink mb-1">{label}</div>
      {children}
      {hint && <div className="text-[10.5px] text-ink-muted mt-0.5">{hint}</div>}
    </label>
  )
}

function FieldWithToggle({ label, hint, vis = 'private', onToggle, children }) {
  const isPublic = vis === 'public'
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <div className="text-[11.5px] font-medium text-ink">{label}</div>
        <button
          type="button" onClick={onToggle}
          className={[
            'text-[10.5px] px-2 py-0.5 rounded-full border transition-colors',
            isPublic
              ? 'bg-emerald-100 border-emerald-300 text-emerald-800'
              : 'bg-beige-100 border-beige-300 text-ink-soft',
          ].join(' ')}
          title={isPublic ? 'Any peer Lumen can read this' : 'Only shared with peer approval'}
        >
          {isPublic ? '● Public' : '○ Private'}
        </button>
      </div>
      {children}
      {hint && <div className="text-[10.5px] text-ink-muted mt-0.5">{hint}</div>}
    </div>
  )
}

const UX_PRESETS = [
  { id: 'standard', name: 'Standard', icon: '✦', desc: 'Default — concise text + inline cards' },
  { id: 'vision', name: 'Vision Accessible', icon: '👁️', desc: 'Large fonts, high contrast, voice I/O' },
  { id: 'audio-first', name: 'Audio First', icon: '🎧', desc: 'Responses read aloud, minimal text' },
  { id: 'data-focused', name: 'Data Focused', icon: '📊', desc: 'Tables and numbers, no prose' },
  { id: 'minimal', name: 'Minimal', icon: '⚡', desc: 'Ultra-short, bullet points only' },
]

function UxPresetSelector({ activeId, onChange }) {
  return (
    <div className="flex flex-col gap-1.5">
      {UX_PRESETS.map(p => (
        <button key={p.id} type="button" onClick={() => onChange(p.id)}
          className={[
            'flex items-center gap-2.5 px-3 py-2 rounded-inner border text-left transition-colors',
            activeId === p.id
              ? 'border-amber bg-amber/10'
              : 'border-beige-200 bg-beige-50 hover:bg-beige-100',
          ].join(' ')}>
          <span className="text-[16px] shrink-0">{p.icon}</span>
          <div className="flex-1 min-w-0">
            <div className="text-[12px] font-medium text-ink">{p.name}</div>
            <div className="text-[10.5px] text-ink-muted truncate">{p.desc}</div>
          </div>
          {activeId === p.id && <span className="text-amber text-[11px] font-medium shrink-0">Active</span>}
        </button>
      ))}
    </div>
  )
}
