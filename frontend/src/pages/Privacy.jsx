import { useEffect, useState } from 'react'
import TopStrip from '../components/TopStrip.jsx'
import LoadingDots from '../components/LoadingDots.jsx'
import ProfileModal from '../components/ProfileModal.jsx'
import { api } from '../lib/api.js'

const FIELD_LABEL = {
  phone: 'phone', address: 'address', dob: 'date of birth',
  occupation: 'occupation', bio: 'bio',
  expertise: 'expertise', interests: 'interests',
}

export default function Privacy({ user }) {
  const [profile, setProfile]   = useState(null)
  const [social, setSocial]     = useState({ discoverable: true, share_progress: true })
  const [history, setHistory]   = useState([])
  const [consents, setConsents] = useState([])
  const [audit, setAudit]       = useState([])
  const [loading, setLoading]   = useState(true)
  const [toast, setToast]       = useState('')
  const [editOpen, setEditOpen] = useState(false)

  const name = user?.name?.split(' ')[0] || 'there'
  const initials = (user?.name || user?.email || 'U')
    .split(/[\s@._-]+/).filter(Boolean).slice(0, 2).map(s => s[0]?.toUpperCase()).join('') || 'U'

  const refresh = async () => {
    const [p, h, c, a] = await Promise.all([
      api.myProfile().catch(() => null),
      api.infoRequestHistory().catch(() => ({ requests: [] })),
      api.consents().catch(() => ({ grants: [] })),
      api.audit().catch(() => ({ events: [] })),
    ])
    setProfile(p)
    setSocial({
      discoverable:   p?.social?.discoverable ?? true,
      share_progress: p?.social?.share_progress ?? true,
    })
    setHistory(h?.requests || [])
    setConsents(c?.grants || [])
    setAudit(a?.events || [])
    setLoading(false)
  }

  useEffect(() => { refresh() }, [])

  const flash = (s) => { setToast(s); setTimeout(() => setToast(''), 2500) }

  const toggleSocial = async (key) => {
    const next = { ...social, [key]: !social[key] }
    setSocial(next)
    try {
      await api.updateSocial(next)
      flash(`Saved · ${key === 'discoverable' ? 'Network visibility' : 'Progress sharing'} ${next[key] ? 'on' : 'off'}`)
    } catch { flash('Failed to save'); setSocial(social) }
  }

  const doRevoke = async (g) => {
    try {
      await api.revokeConsent(g.grantee, g.action)
      setConsents(cs => cs.filter(x => !(x.grantee === g.grantee && x.action === g.action)))
      flash('Consent revoked')
    } catch { flash('Revoke failed') }
  }

  const downloadMyData = () => {
    const blob = new Blob([JSON.stringify({
      profile, social, info_requests: history, consents, audit,
      exported_at: new Date().toISOString(),
    }, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = `lumen-export-${user?.email || 'me'}.json`
    a.click(); URL.revokeObjectURL(url)
    flash('Exported to downloads')
  }

  const publicFields   = Object.entries(profile?.visibility || {}).filter(([, v]) => v === 'public').map(([k]) => k)
  const privateFields  = Object.entries(profile?.visibility || {}).filter(([, v]) => v === 'private').map(([k]) => k)

  return (
    <div className="flex-1 min-w-0 flex flex-col">
      <TopStrip userName={name} userInitials={initials} eventCount={history.filter(h => h.status === 'pending').length} />
      <div className="px-6 pb-3">
        <h2 className="text-[15px] font-medium text-ink">Privacy & consent</h2>
        <p className="text-2xs text-ink-muted mt-0.5">
          Your Lumen only shares what you've explicitly consented to. Every share is logged.
        </p>
      </div>
      <div className="h-px bg-beige-200" />

      <div className="flex-1 overflow-y-auto p-6 flex flex-col gap-5">
        {loading && <LoadingDots label="Loading your privacy settings" />}

        {!loading && (
          <>
            {/* Network visibility toggles */}
            <Section
              title="Network visibility"
              desc="Controls whether other students' Lumens can discover yours on the LITP network.">
              <Toggle
                label="Discoverable on the network"
                hint="Off = your Lumen is hidden from peer discovery. You can still initiate contact."
                on={social.discoverable} onToggle={() => toggleSocial('discoverable')} />
              <Toggle
                label="Share progress summary with peers"
                hint="Your sessions / mastered-concepts counts show on your peer card. Topics are always anonymous."
                on={social.share_progress} onToggle={() => toggleSocial('share_progress')} />
            </Section>

            {/* Field visibility */}
            <Section
              title="What peers can see about you"
              desc="Public fields are shared with any peer Lumen. Private fields require your approval per request."
              action={<button onClick={() => setEditOpen(true)}
                className="text-[11.5px] rounded-pill bg-amber text-white px-3 py-1 hover:opacity-90">
                Edit in profile
              </button>}>
              <div className="flex flex-wrap gap-1.5">
                {publicFields.length === 0 && (
                  <span className="text-[11.5px] text-ink-muted italic">Nothing public yet.</span>
                )}
                {publicFields.map(f => (
                  <span key={f} className="text-[10.5px] rounded-pill bg-emerald-100 border border-emerald-300 text-emerald-800 px-2 py-0.5">
                    ● {FIELD_LABEL[f] || f}
                  </span>
                ))}
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {privateFields.map(f => (
                  <span key={f} className="text-[10.5px] rounded-pill bg-beige-100 border border-beige-300 text-ink-soft px-2 py-0.5">
                    ○ {FIELD_LABEL[f] || f}
                  </span>
                ))}
              </div>
            </Section>

            {/* Info-request history */}
            <Section
              title={`Info-sharing history · ${history.length}`}
              desc="Every private-field request to or from another peer's Lumen.">
              {history.length === 0 && (
                <div className="text-[12px] text-ink-muted italic">No requests yet.</div>
              )}
              <div className="flex flex-col gap-1.5">
                {history.slice(0, 20).map(r => (
                  <HistoryRow key={r.id} r={r} />
                ))}
              </div>
            </Section>

            {/* Active consents */}
            <Section
              title={`Active consents · ${consents.length}`}
              desc="Durable grants (separate from one-off info-requests) you've given TAs or peer Lumens.">
              {consents.length === 0 && (
                <div className="text-[12px] text-ink-muted italic">
                  No active consents. TAs read your progress under the built-in 'learning' tier by default.
                </div>
              )}
              <div className="flex flex-col gap-1.5">
                {consents.map((g, i) => (
                  <div key={i} className="flex items-center gap-2 rounded-inner bg-beige-50 border border-beige-200 px-3 py-2">
                    <div className="flex-1 min-w-0">
                      <div className="text-[12.5px] text-ink">
                        <b>{g.grantee}</b> can <b>{g.action}</b>
                        <span className="ml-1 text-[10.5px] rounded-pill bg-white border border-beige-300 px-1.5 py-0.5 text-ink-soft">{g.tier}</span>
                      </div>
                      <div className="text-[10.5px] text-ink-muted mt-0.5">
                        granted {relTime(g.granted_at)}
                        {g.expires_at && ` · expires ${relTime(g.expires_at)}`}
                      </div>
                    </div>
                    <button onClick={() => doRevoke(g)}
                      className="text-[11px] rounded-pill bg-white border border-beige-300 hover:border-rose-300 hover:text-rose-700 px-2.5 py-1">
                      Revoke
                    </button>
                  </div>
                ))}
              </div>
            </Section>

            {/* Audit trail */}
            <Section
              title={`Audit trail · last ${Math.min(audit.length, 50)}`}
              desc="Every read / write / share event on your Lumen, cryptographically scoped to your Entra identity.">
              {audit.length === 0 && (
                <div className="text-[12px] text-ink-muted italic">No events yet.</div>
              )}
              <div className="max-h-[260px] overflow-y-auto rounded-inner border border-beige-200 bg-beige-50 font-mono text-[11px] leading-relaxed">
                {audit.slice(0, 50).map((e, i) => (
                  <div key={i} className="px-3 py-1 border-b border-beige-100 last:border-0">
                    <span className="text-ink-muted">{shortTime(e.timestamp)}</span>
                    {'  '}
                    <span className="text-amber">{e.action || e.type || 'event'}</span>
                    {'  '}
                    <span className="text-ink-soft">{e.detail || e.actor || e.grantee || ''}</span>
                  </div>
                ))}
              </div>
            </Section>

            {/* Identity + data export */}
            <Section title="Your identity & data">
              <div className="text-[12.5px] text-ink-soft leading-relaxed">
                Signed in as <span className="text-ink font-medium">{user?.email || 'unknown'}</span>.
                Identity is managed by Microsoft Entra — revoke access at any time from your account settings.
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                <button onClick={downloadMyData}
                  className="text-[12px] rounded-pill bg-amber text-white px-3 py-1.5 hover:opacity-90">
                  Download my data (JSON)
                </button>
                <a href="https://myaccount.microsoft.com/" target="_blank" rel="noreferrer"
                  className="text-[12px] rounded-pill bg-beige-100 border border-beige-300 hover:bg-beige-200 text-ink-soft px-3 py-1.5">
                  Manage Entra access →
                </a>
              </div>
            </Section>
          </>
        )}
      </div>

      <ProfileModal open={editOpen} onClose={() => setEditOpen(false)} onSaved={refresh} />

      {toast && (
        <div className="fixed bottom-6 right-6 z-40 bg-ink text-white text-[12.5px] px-3 py-2 rounded-inner">
          {toast}
        </div>
      )}
    </div>
  )
}

function Section({ title, desc, children, action }) {
  return (
    <section className="rounded-card bg-white border border-beige-200 p-5">
      <div className="flex items-start justify-between gap-4 mb-3">
        <div>
          <h3 className="text-[13.5px] font-medium text-ink">{title}</h3>
          {desc && <p className="text-2xs text-ink-muted mt-0.5">{desc}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}

function Toggle({ label, hint, on, onToggle }) {
  return (
    <div className="flex items-start gap-3 py-2">
      <button
        onClick={onToggle} role="switch" aria-checked={on}
        className={`w-9 h-5 rounded-full shrink-0 relative transition-colors ${on ? 'bg-amber' : 'bg-beige-300'}`}>
        <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full transition-all ${on ? 'left-[18px]' : 'left-0.5'}`} />
      </button>
      <div className="flex-1 min-w-0">
        <div className="text-[12.5px] text-ink">{label}</div>
        {hint && <div className="text-[11px] text-ink-muted mt-0.5">{hint}</div>}
      </div>
    </div>
  )
}

function HistoryRow({ r }) {
  const dirLabel = r.direction === 'outgoing'
    ? `You asked ${r.to_name}'s Lumen for their ${r.field_label || r.field}`
    : `${r.from_name}'s Lumen asked for your ${r.field_label || r.field}`
  const statusColor = {
    approved: 'bg-emerald-100 border-emerald-300 text-emerald-800',
    denied:   'bg-rose-100 border-rose-300 text-rose-800',
    pending:  'bg-amber-light border-amber-border text-amber',
  }[r.status] || 'bg-beige-100 border-beige-300 text-ink-soft'
  return (
    <div className="flex items-center gap-2 rounded-inner bg-beige-50 border border-beige-200 px-3 py-2">
      <div className="flex-1 min-w-0">
        <div className="text-[12.5px] text-ink truncate">{dirLabel}</div>
        <div className="text-[10.5px] text-ink-muted mt-0.5">{relTime(r.created_at)}</div>
      </div>
      <span className={`text-[10.5px] rounded-pill border px-2 py-0.5 ${statusColor}`}>
        {r.status}
      </span>
    </div>
  )
}

function relTime(iso) {
  try {
    const d = new Date(iso); const s = Math.floor((Date.now() - d.getTime()) / 1000)
    if (s < 60)    return 'just now'
    if (s < 3600)  return Math.floor(s/60) + 'm ago'
    if (s < 86400) return Math.floor(s/3600) + 'h ago'
    return Math.floor(s/86400) + 'd ago'
  } catch { return '' }
}
function shortTime(iso) {
  try {
    const d = new Date(iso)
    return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  } catch { return iso || '' }
}
