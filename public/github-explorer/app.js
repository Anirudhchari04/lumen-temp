/* ── GitHub Repo Explorer — Vanilla JS App ──────────────────────────────── */

const API = '/github-explorer';  // mounted under a sub-path inside Lumen

// ── State ─────────────────────────────────────────────────────────────────
let state = {
  token: localStorage.getItem('gh_token') || '',
  repos: [],
  selectedRepo: null,   // "owner/repo"
  browsePath: '',
  viewingFile: null,
};

// ── DOM helpers ───────────────────────────────────────────────────────────
const $ = (sel, ctx = document) => ctx.querySelector(sel);
const $$ = (sel, ctx = document) => [...ctx.querySelectorAll(sel)];
const show = el => el.classList.add('active');
const hide = el => el.classList.remove('active');
const showScreen = id => {
  $$('.screen').forEach(s => s.classList.remove('active'));
  document.getElementById(id).classList.add('active');
};

// ── API helpers ───────────────────────────────────────────────────────────
async function api(path, opts = {}) {
  const url = new URL(API + path, location.origin);
  if (state.token) url.searchParams.set('token', state.token);
  if (opts.params) Object.entries(opts.params).forEach(([k,v]) => { if(v) url.searchParams.set(k, v); });

  const fetchOpts = { headers: { 'Content-Type': 'application/json' } };
  if (opts.method) fetchOpts.method = opts.method;
  if (opts.body) fetchOpts.body = JSON.stringify(opts.body);

  const res = await fetch(url, fetchOpts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || 'API error');
  }
  return res.json();
}

// ── Lang icons ────────────────────────────────────────────────────────────
const LANG_ICONS = {
  Python:'🐍', JavaScript:'🟨', TypeScript:'🔷', Java:'☕',
  'C++':'⚙️', 'C#':'💜', Go:'🐹', Rust:'🦀', Ruby:'💎',
  HTML:'🌐', CSS:'🎨', Shell:'🐚', 'Jupyter Notebook':'📓',
  Kotlin:'🟣', Swift:'🍎', PHP:'🐘', R:'📊',
};
const EXT_ICONS = {
  '.py':'🐍','.js':'🟨','.ts':'🔷','.java':'☕','.cpp':'⚙️','.c':'⚙️',
  '.cs':'💜','.go':'🐹','.rs':'🦀','.rb':'💎','.html':'🌐','.css':'🎨',
  '.md':'📝','.json':'📋','.yaml':'📋','.yml':'📋','.xml':'📋','.txt':'📄',
  '.sh':'🐚','.ipynb':'📓','.toml':'📋','.sql':'🗃️',
};
function fileIcon(name) {
  for (const [ext, icon] of Object.entries(EXT_ICONS)) if (name.endsWith(ext)) return icon;
  return '📄';
}
function formatSize(bytes) {
  return bytes > 1024 ? (bytes / 1024).toFixed(1) + ' KB' : bytes + ' B';
}

// ══════════════════════════════════════════════════════════════════════════
// AUTH
// ══════════════════════════════════════════════════════════════════════════

function login(token) {
  state.token = token;
  localStorage.setItem('gh_token', token);
  loadRepos();
}

function logout() {
  state.token = '';
  state.repos = [];
  state.selectedRepo = null;
  localStorage.removeItem('gh_token');
  showScreen('login-screen');
}

$('#btn-token').onclick = () => {
  const t = $('#token-input').value.trim();
  if (t) login(t);
};

$('#btn-oauth').onclick = async () => {
  const statusEl = $('#oauth-status');
  statusEl.classList.remove('hidden');
  statusEl.innerHTML = '<div class="spinner"></div> Starting login…';
  try {
    const dc = await api('/api/auth/device-code', { method: 'POST', body: {} });
    statusEl.innerHTML = `
      <p><strong>1.</strong> Go to <a href="${dc.verification_uri}" target="_blank">${dc.verification_uri}</a></p>
      <p><strong>2.</strong> Enter code: <code>${dc.user_code}</code></p>
      <p style="margin-top:8px;color:var(--text-muted)"><span class="spinner"></span> Waiting for authorization…</p>
    `;
    const result = await api(`/api/auth/poll-token?device_code=${dc.device_code}&interval=${dc.interval}`, { method: 'POST' });
    login(result.access_token);
  } catch (e) {
    statusEl.innerHTML = `<span style="color:var(--red)">❌ ${e.message}</span>`;
  }
};

$('#btn-logout').onclick = logout;
$('#btn-logout2').onclick = logout;

// ══════════════════════════════════════════════════════════════════════════
// REPOS
// ══════════════════════════════════════════════════════════════════════════

async function loadRepos() {
  showScreen('repos-screen');
  $('#repos-grid').innerHTML = '<div class="loading-center"><div class="spinner"></div></div>';
  try {
    state.repos = await api('/api/repos');
    renderRepos();
  } catch (e) {
    $('#repos-grid').innerHTML = `<div class="empty">❌ ${e.message}</div>`;
  }
}

function renderRepos(filter = '') {
  const q = filter.toLowerCase();
  const filtered = q
    ? state.repos.filter(r =>
        r.full_name.toLowerCase().includes(q) ||
        (r.description || '').toLowerCase().includes(q) ||
        (r.language || '').toLowerCase().includes(q)
      )
    : state.repos;

  if (!filtered.length) {
    $('#repos-grid').innerHTML = '<div class="empty">No repositories found.</div>';
    return;
  }

  $('#repos-grid').innerHTML = filtered.map(r => {
    const lang = r.language || '—';
    const icon = LANG_ICONS[lang] || '📄';
    const vis = r.private ? '🔒 Private' : '🌍 Public';
    const desc = r.description || 'No description';
    return `
      <div class="repo-card" data-repo="${r.full_name}">
        <h3>${r.full_name.split('/')[1]}</h3>
        <div class="desc">${esc(desc)}</div>
        <div class="meta">
          <span>${icon} ${lang}</span>
          <span>⭐ ${r.stars}</span>
          <span>${vis}</span>
        </div>
      </div>`;
  }).join('');

  $$('.repo-card').forEach(card => {
    card.onclick = () => selectRepo(card.dataset.repo);
  });
}

$('#repo-search').oninput = e => renderRepos(e.target.value);
$('#btn-refresh-repos').onclick = loadRepos;

// ══════════════════════════════════════════════════════════════════════════
// REPO DETAIL
// ══════════════════════════════════════════════════════════════════════════

function selectRepo(fullName) {
  state.selectedRepo = fullName;
  state.browsePath = '';
  state.viewingFile = null;
  state.panelFile = null;
  chatMessages.length = 0;
  // Reset server-side agent session
  api('/api/chat/reset?session_id=default', { method: 'POST' }).catch(() => {});
  $('#detail-repo-name').textContent = fullName;
  showScreen('detail-screen');
  switchTab('files');
  loadFiles();
}

$('#btn-back-repos').onclick = () => {
  state.selectedRepo = null;
  api('/api/chat/reset?session_id=default', { method: 'POST' }).catch(() => {});
  showScreen('repos-screen');
};

// ── Tabs ──────────────────────────────────────────────────────────────────
$$('.tab').forEach(tab => {
  tab.onclick = () => switchTab(tab.dataset.tab);
});

function switchTab(name) {
  $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  $$('.tab-content').forEach(tc => tc.classList.toggle('active', tc.id === `tab-${name}`));
}

// ── Commit toast ──────────────────────────────────────────────────────────
function showCommitToast(result) {
  const toast = $('#commit-toast');
  const icons = { created: '🆕', updated: '✏️', deleted: '🗑️' };
  const icon = icons[result.action] || '✅';
  const shaLink = result.commit_url
    ? `<a href="${result.commit_url}" target="_blank">${result.sha}</a>`
    : result.sha;
  toast.innerHTML = `
    <span>${icon} <strong>${result.action}</strong> — ${esc(result.path)} · Commit: ${shaLink} — ${esc(result.message)}</span>
    <button class="btn-icon-only" onclick="this.parentElement.classList.add('hidden')">✕</button>
  `;
  toast.classList.remove('hidden');
}

// ══════════════════════════════════════════════════════════════════════════
// FILES TAB
// ══════════════════════════════════════════════════════════════════════════

async function loadFiles() {
  const container = $('#file-browser');
  container.innerHTML = '<div class="loading-center"><div class="spinner"></div></div>';
  const [owner, repo] = state.selectedRepo.split('/');

  try {
    const contents = await api(`/api/repos/${owner}/${repo}/contents`, { params: { path: state.browsePath } });
    renderFileBrowser(contents);
  } catch (e) {
    container.innerHTML = `<div class="empty">❌ ${e.message}</div>`;
  }
}

function renderFileBrowser(contents) {
  const container = $('#file-browser');
  const dirs = contents.filter(c => c.type === 'dir').sort((a,b) => a.name.localeCompare(b.name));
  const files = contents.filter(c => c.type !== 'dir').sort((a,b) => a.name.localeCompare(b.name));

  // Breadcrumb
  const parts = state.browsePath ? state.browsePath.split('/') : [];
  let breadcrumb = `<button onclick="navigateTo('')">🏠 root</button>`;
  parts.forEach((p, i) => {
    const path = parts.slice(0, i + 1).join('/');
    breadcrumb += `<span class="sep">/</span><button onclick="navigateTo('${path}')">${esc(p)}</button>`;
  });

  container.innerHTML = `
    <div class="file-toolbar">
      <div class="breadcrumb">${breadcrumb}</div>
      <button class="btn btn-sm btn-primary" onclick="openNewFileEditor()">➕ New File</button>
    </div>
    <div class="file-list">
      ${dirs.map(d => `
        <div class="file-row" onclick="navigateTo('${d.path}')">
          <span class="icon">📁</span>
          <span class="name dir">${esc(d.name)}/</span>
          <span class="size"></span>
          <span class="actions"></span>
        </div>
      `).join('')}
      ${files.map(f => `
        <div class="file-row" onclick="viewFile('${f.path}')">
          <span class="icon">${fileIcon(f.name)}</span>
          <span class="name">${esc(f.name)}</span>
          <span class="size">${formatSize(f.size)}</span>
        </div>
      `).join('')}
      ${!dirs.length && !files.length ? '<div class="empty">Empty directory</div>' : ''}
    </div>
  `;
}

// Expose to inline onclick
window.navigateTo = function(path) {
  state.browsePath = path;
  state.viewingFile = null;
  loadFiles();
};

window.viewFile = async function(path) {
  const container = $('#file-browser');
  container.innerHTML = '<div class="loading-center"><div class="spinner"></div></div>';
  const [owner, repo] = state.selectedRepo.split('/');

  try {
    const data = await api(`/api/repos/${owner}/${repo}/file`, { params: { path } });
    state.viewingFile = path;

    const lines = data.content.split('\n').map((line, i) =>
      `<span class="line-number">${i + 1}</span>${esc(line)}`
    ).join('\n');

    container.innerHTML = `
      <div class="file-viewer">
        <div class="file-viewer-header">
          <div>
            <button class="btn btn-sm" onclick="loadFiles()">← Back</button>
            <strong style="margin-left:8px">${esc(data.name)}</strong>
            <span style="color:var(--text-muted);margin-left:8px;font-size:.8rem">${formatSize(data.size)}</span>
          </div>
          <div style="display:flex;gap:6px">
            <button class="btn btn-sm" onclick="openEditFileEditor('${path}')">✏️ Edit</button>
            <button class="btn btn-sm btn-danger" onclick="confirmDelete('${path}')">🗑️ Delete</button>
          </div>
        </div>
        <pre><code>${lines}</code></pre>
      </div>
    `;
  } catch (e) {
    container.innerHTML = `<div class="empty">❌ ${e.message}</div>`;
  }
};

// ── File Editor Modal ─────────────────────────────────────────────────────

window.openNewFileEditor = function() {
  const prefix = state.browsePath ? state.browsePath + '/' : '';
  $('#editor-title').textContent = '➕ Create New File';
  $('#editor-path').value = prefix;
  $('#editor-path').disabled = false;
  $('#editor-content').value = '';
  $('#editor-commit-msg').value = '';
  $('#editor-modal').classList.remove('hidden');
  $('#editor-save').dataset.mode = 'create';
};

window.openEditFileEditor = async function(path) {
  $('#editor-title').textContent = `✏️ Edit: ${path}`;
  $('#editor-path').value = path;
  $('#editor-path').disabled = true;
  $('#editor-content').value = 'Loading…';
  $('#editor-commit-msg').value = `Update ${path}`;
  $('#editor-modal').classList.remove('hidden');
  $('#editor-save').dataset.mode = 'edit';
  $('#editor-save').dataset.path = path;

  const [owner, repo] = state.selectedRepo.split('/');
  try {
    const data = await api(`/api/repos/${owner}/${repo}/file`, { params: { path } });
    $('#editor-content').value = data.content;
  } catch (e) {
    $('#editor-content').value = `Error loading file: ${e.message}`;
  }
};

$('#editor-close').onclick = $('#editor-cancel').onclick = () => {
  $('#editor-modal').classList.add('hidden');
};

$('#editor-save').onclick = async () => {
  const path = $('#editor-path').value.trim();
  const content = $('#editor-content').value;
  const message = $('#editor-commit-msg').value.trim();
  if (!path) return alert('Enter a file path');
  if (!message) return alert('Commit message is required.');

  const [owner, repo] = state.selectedRepo.split('/');
  const btn = $('#editor-save');
  btn.disabled = true;
  btn.textContent = 'Saving…';

  try {
    const result = await api(`/api/repos/${owner}/${repo}/file`, {
      method: 'POST',
      body: { path, content, message },
    });
    $('#editor-modal').classList.add('hidden');
    showCommitToast(result);
    loadFiles();
  } catch (e) {
    alert('Error: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '💾 Save & Commit';
  }
};

// ── Delete ────────────────────────────────────────────────────────────────

window.confirmDelete = function(path) {
  $('#delete-msg').textContent = `Are you sure you want to delete ${path}?`;
  $('#delete-commit-msg').value = `Delete ${path}`;
  $('#delete-modal').classList.remove('hidden');
  $('#delete-confirm').dataset.path = path;
};

$('#delete-close').onclick = $('#delete-cancel').onclick = () => {
  $('#delete-modal').classList.add('hidden');
};

$('#delete-confirm').onclick = async () => {
  const path = $('#delete-confirm').dataset.path;
  const message = $('#delete-commit-msg').value.trim();
  if (!message) return alert('Commit message is required.');
  const [owner, repo] = state.selectedRepo.split('/');

  const btn = $('#delete-confirm');
  btn.disabled = true;
  btn.textContent = 'Deleting…';

  try {
    const result = await api(`/api/repos/${owner}/${repo}/file`, {
      method: 'DELETE',
      body: { path, message },
    });
    $('#delete-modal').classList.add('hidden');
    showCommitToast(result);
    state.viewingFile = null;
    loadFiles();
  } catch (e) {
    alert('Error: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Delete';
  }
};

// ══════════════════════════════════════════════════════════════════════════
// COMMITS TAB
// ══════════════════════════════════════════════════════════════════════════

$('#btn-load-commits').onclick = async () => {
  const [owner, repo] = state.selectedRepo.split('/');
  const list = $('#commits-list');
  list.innerHTML = '<div class="loading-center"><div class="spinner"></div></div>';

  try {
    const commits = await api(`/api/repos/${owner}/${repo}/commits`, {
      params: {
        branch: $('#commit-branch').value || undefined,
        author: $('#commit-author').value || undefined,
        max_count: $('#commit-count').value,
      },
    });
    list.innerHTML = commits.map(c => {
      const msg = c.message.split('\n')[0];
      const mergeBadge = c.is_merge ? '<span class="badge badge-merge">merge</span>' : '';
      return `
        <div class="item-card">
          <a href="${c.url}" target="_blank" class="sha">${c.short_sha}</a>
          <div class="details">
            <div class="title">${esc(msg.slice(0, 80))}${mergeBadge}</div>
            <div class="info">👤 ${esc(c.author)} · 📅 ${String(c.date).slice(0, 10)}</div>
          </div>
        </div>`;
    }).join('') || '<div class="empty">No commits found.</div>';
  } catch (e) {
    list.innerHTML = `<div class="empty">❌ ${e.message}</div>`;
  }
};

// ══════════════════════════════════════════════════════════════════════════
// BRANCHES TAB
// ══════════════════════════════════════════════════════════════════════════

$('#btn-load-branches').onclick = async () => {
  const [owner, repo] = state.selectedRepo.split('/');
  const list = $('#branches-list');
  list.innerHTML = '<div class="loading-center"><div class="spinner"></div></div>';

  try {
    const branches = await api(`/api/repos/${owner}/${repo}/branches`);
    list.innerHTML = branches.map(b => {
      const badge = b.protected ? '<span class="badge badge-protected">protected</span>' : '';
      return `
        <div class="item-card">
          <span class="sha">${b.sha.slice(0, 7)}</span>
          <div class="details">
            <div class="title">🌿 ${esc(b.name)}${badge}</div>
          </div>
        </div>`;
    }).join('') || '<div class="empty">No branches found.</div>';
  } catch (e) {
    list.innerHTML = `<div class="empty">❌ ${e.message}</div>`;
  }
};

// ══════════════════════════════════════════════════════════════════════════
// PRS TAB
// ══════════════════════════════════════════════════════════════════════════

$('#btn-load-prs').onclick = async () => {
  const [owner, repo] = state.selectedRepo.split('/');
  const list = $('#prs-list');
  list.innerHTML = '<div class="loading-center"><div class="spinner"></div></div>';

  try {
    const prs = await api(`/api/repos/${owner}/${repo}/pulls`, {
      params: { state: $('#pr-state').value, max_count: $('#pr-count').value },
    });
    list.innerHTML = prs.map(pr => {
      const stateIcon = pr.state === 'open' ? '🟢' : (pr.merged ? '🟣' : '🔴');
      const badgeCls = pr.merged ? 'badge-merged' : (pr.state === 'open' ? 'badge-open' : 'badge-closed');
      const badgeText = pr.merged ? 'merged' : pr.state;
      return `
        <div class="item-card">
          <span class="sha">#${pr.number}</span>
          <div class="details">
            <div class="title">${stateIcon} ${esc(pr.title)} <span class="badge ${badgeCls}">${badgeText}</span></div>
            <div class="info">👤 ${esc(pr.author)} · 📅 ${pr.created_at.slice(0, 10)} · <a href="${pr.url}" target="_blank">View →</a></div>
          </div>
        </div>`;
    }).join('') || '<div class="empty">No pull requests found.</div>';
  } catch (e) {
    list.innerHTML = `<div class="empty">❌ ${e.message}</div>`;
  }
};

// ══════════════════════════════════════════════════════════════════════════
// CHAT TAB
// ══════════════════════════════════════════════════════════════════════════

const chatMessages = [];

function renderChat() {
  const container = $('#chat-messages');
  container.innerHTML = chatMessages.map((m, idx) => {
    // Pending action approval card
    if (m.role === 'action') {
      return `<div class="action-card" id="action-card-${idx}" data-action-id="${m.actionId}">
        <div class="action-icon">⚠️</div>
        <div class="action-body">
          <div class="action-title">Agent wants to make a commit</div>
          <div class="action-desc">${esc(m.description)}</div>
          <div class="action-msg">📝 ${esc(m.commitMessage)}</div>
          <div class="action-buttons">
            <button class="btn btn-sm btn-primary" onclick="approveAction('${m.actionId}', ${idx})">✅ Approve</button>
            <button class="btn btn-sm btn-danger" onclick="rejectAction('${m.actionId}', ${idx})">❌ Reject</button>
          </div>
        </div>
      </div>`;
    }

    // Approved/rejected status
    if (m.role === 'action_result') {
      const cls = m.approved ? 'action-approved' : 'action-rejected';
      const icon = m.approved ? '✅' : '❌';
      return `<div class="action-result ${cls}">${icon} ${esc(m.text)}</div>`;
    }

    const isUser = m.role === 'user';
    const avatar = isUser ? '👤' : '🐙';
    const label = isUser ? 'YOU' : 'AGENT';
    const isTyping = m.content === '__TYPING__';
    let body;
    if (isTyping) {
      body = '<div class="typing-dots"><span></span><span></span><span></span></div>';
    } else if (isUser) {
      body = `<div class="msg-body">${esc(m.content)}</div>`;
    } else {
      body = `<div class="msg-body">${marked.parse(m.content)}</div>`;
    }
    return `<div class="chat-row ${m.role}"><div class="chat-avatar">${avatar}</div><div class="chat-bubble"><div class="sender">${label}</div>${body}</div></div>`;
  }).join('');
  container.scrollTop = container.scrollHeight;
}

async function sendMessage() {
  const input = $('#chat-input');
  const msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  $('#btn-send').disabled = true;

  chatMessages.push({ role: 'user', content: msg });
  chatMessages.push({ role: 'assistant', content: '__TYPING__' });
  renderChat();

  try {
    const result = await api('/api/chat', {
      method: 'POST',
      body: { message: msg, repo: state.selectedRepo, model: $('#model-select').value },
    });
    chatMessages.pop();
    chatMessages.push({ role: 'assistant', content: result.response });
    // Show tool results in side panel
    if (result.tool_results && result.tool_results.length > 0) {
      console.log('Tool results received:', result.tool_results.length, result.tool_results.map(t => t.tool));
      
      // Check for pending approval actions FIRST
      const pendingActions = result.tool_results.filter(
        t => t.result && t.result.status === 'pending_approval'
      );
      console.log('Pending actions found:', pendingActions.length);
      if (pendingActions.length > 0) {
        const pa = pendingActions[pendingActions.length - 1];
        const toolCall = pendingActions[pendingActions.length - 1];
        console.log('Showing approval dialog for:', pa.result.action_id);
        showApprovalDialog(pa.result, toolCall);
      }

      // Then try rendering panel
      try {
        renderPanel(result.tool_results);
      } catch (panelErr) {
        console.error('renderPanel error:', panelErr);
      }
    } else {
      console.log('No tool results returned, keys:', Object.keys(result));
    }
  } catch (e) {
    chatMessages.pop();
    chatMessages.push({ role: 'assistant', content: `❌ Error: ${e.message}` });
  }
  $('#btn-send').disabled = false;
  renderChat();
}

$('#btn-send').onclick = sendMessage;
$('#chat-input').onkeydown = e => { if (e.key === 'Enter') sendMessage(); };

// ── Action approval/rejection ─────────────────────────────────────────────

let _currentApprovalActionId = null;

function showApprovalDialog(actionResult, toolCall) {
  _currentApprovalActionId = actionResult.action_id;
  const modal = $('#approval-modal');
  const args = toolCall ? toolCall.args : {};
  const toolName = toolCall ? toolCall.tool : '';

  // Determine action type
  let typeBadge = '', icon = '⚠️', title = 'Agent Action Requires Approval';
  let warningText = 'This action will modify the repository. Please review before approving.';

  if (toolName === 'create_or_update_file') {
    typeBadge = '<span class="approval-type-badge badge-update">📝 Create / Update File</span>';
    icon = '📝';
    title = 'File Change Approval';
    warningText = 'This will create or update a file in the repository.';
  } else if (toolName === 'delete_file') {
    typeBadge = '<span class="approval-type-badge badge-delete">🗑️ Delete File</span>';
    icon = '🗑️';
    title = 'File Deletion Approval';
    warningText = 'This will permanently delete a file from the repository. This cannot be undone easily.';
  } else if (toolName === 'create_repo') {
    typeBadge = '<span class="approval-type-badge badge-repo">📦 Create Repository</span>';
    icon = '📦';
    title = 'Repository Creation Approval';
    warningText = 'This will create a new repository under your GitHub account.';
  }

  $('#approval-icon').textContent = icon;
  $('#approval-title').textContent = title;
  $('#approval-type-badge').innerHTML = typeBadge;
  $('#approval-commit-msg').value = actionResult.message || '';
  $('#approval-warning-text').textContent = warningText;

  // Repo
  const repoRow = $('#approval-repo-row');
  if (args.repo_full_name) {
    repoRow.style.display = '';
    $('#approval-repo').textContent = args.repo_full_name;
  } else {
    repoRow.style.display = 'none';
  }

  // File path
  const pathRow = $('#approval-path-row');
  if (args.path) {
    pathRow.style.display = '';
    $('#approval-path').textContent = args.path;
  } else {
    pathRow.style.display = 'none';
  }

  // Repo name (for create_repo)
  const nameRow = $('#approval-name-row');
  if (args.name) {
    nameRow.style.display = '';
    $('#approval-name').textContent = args.name;
  } else {
    nameRow.style.display = 'none';
  }

  // Visibility (for create_repo)
  const visRow = $('#approval-visibility-row');
  if (toolName === 'create_repo') {
    visRow.style.display = '';
    $('#approval-visibility').textContent = args.private ? '🔒 Private' : '🌍 Public';
  } else {
    visRow.style.display = 'none';
  }

  // Content preview (for create/update)
  const previewEl = $('#approval-content-preview');
  if (args.content && toolName === 'create_or_update_file') {
    previewEl.classList.remove('hidden');
    const preview = args.content.length > 1000 ? args.content.slice(0, 1000) + '\n…(truncated)' : args.content;
    $('#approval-preview-code').querySelector('code').textContent = preview;
  } else {
    previewEl.classList.add('hidden');
  }

  console.log('Opening approval modal');
  modal.classList.remove('hidden');
  modal.style.display = 'flex';
}

$('#approval-close').onclick = () => {
  const m = $('#approval-modal');
  m.classList.add('hidden');
  m.style.display = 'none';
  _currentApprovalActionId = null;
};

$('#approval-approve').onclick = async () => {
  if (!_currentApprovalActionId) return;
  const commitMsg = $('#approval-commit-msg').value.trim();
  if (!commitMsg) return alert('Commit message is required.');

  const btn = $('#approval-approve');
  btn.disabled = true;
  btn.textContent = '⏳ Executing…';

  try {
    const result = await api(`/api/actions/${_currentApprovalActionId}/approve`, {
      method: 'POST',
      body: { commit_message: commitMsg },
    });
    chatMessages.push({ role: 'action_result', approved: true, text: 'Action approved and executed successfully.' });
    if (result.result) showCommitToast(result.result);
  } catch (e) {
    chatMessages.push({ role: 'action_result', approved: false, text: `Execution failed: ${e.message}` });
  }
  const m1 = $('#approval-modal'); m1.classList.add('hidden'); m1.style.display = 'none';
  _currentApprovalActionId = null;
  btn.disabled = false;
  btn.textContent = '✅ Approve & Execute';
  renderChat();
};

$('#approval-reject').onclick = async () => {
  if (!_currentApprovalActionId) return;
  try {
    await api(`/api/actions/${_currentApprovalActionId}/reject`, { method: 'POST' });
    chatMessages.push({ role: 'action_result', approved: false, text: 'Action rejected by user.' });
  } catch (e) {
    chatMessages.push({ role: 'action_result', approved: false, text: `Rejection failed: ${e.message}` });
  }
  const m2 = $('#approval-modal'); m2.classList.add('hidden'); m2.style.display = 'none';
  _currentApprovalActionId = null;
  renderChat();
};

window.approveAction = async function(actionId, msgIdx) {
  // Legacy inline approve — redirect to modal
  _currentApprovalActionId = actionId;
  $('#approval-approve').click();
};

window.rejectAction = async function(actionId, msgIdx) {
  // Legacy inline reject
  _currentApprovalActionId = actionId;
  $('#approval-reject').click();
};

// ══════════════════════════════════════════════════════════════════════════
// SIDE PANEL
// ══════════════════════════════════════════════════════════════════════════

$('#panel-close').onclick = () => {
  const panel = document.getElementById('chat-panel');
  panel.classList.remove('open');
  panel.style.display = 'none';
};

function openPanel() {
  const panel = document.getElementById('chat-panel');
  if (!panel) {
    console.error('chat-panel element not found!');
    return;
  }
  panel.classList.add('open');
  panel.style.display = 'flex';
  console.log('Panel opened, display:', panel.style.display, 'classes:', [...panel.classList]);
}

// ── Panel file view/edit ──────────────────────────────────────────────────

function renderPanelFileView() {
  const panel = $('#panel-body');
  const f = state.panelFile;
  if (!f) return;

  const lines = f.content.split('\n').map((line, i) =>
    `<span class="line-number">${i + 1}</span>${esc(line)}`
  ).join('\n');

  panel.innerHTML = `
    <div class="panel-file-header">
      <div>
        <span class="file-name">${esc(f.name)}</span>
        <span class="file-meta" style="margin-left:8px">${formatSize(f.size)}</span>
      </div>
      <div style="display:flex;gap:6px">
        <button class="btn btn-sm" onclick="panelEditFile()">✏️ Edit</button>
        <button class="btn btn-sm btn-danger" onclick="panelDeleteFile()">🗑️ Delete</button>
        ${f.url ? `<a href="${f.url}" target="_blank" class="btn btn-sm">GitHub →</a>` : ''}
      </div>
    </div>
    <pre><code>${lines}</code></pre>
  `;
}

window.panelEditFile = function() {
  const panel = $('#panel-body');
  const f = state.panelFile;
  if (!f) return;
  $('#panel-title').textContent = `✏️ Editing: ${f.name}`;

  const isDraft = f.isDraft ? ' (draft — unsaved changes)' : '';

  panel.innerHTML = `
    <div class="panel-file-header">
      <span class="file-name">${esc(f.path)}${isDraft}</span>
      <button class="btn btn-sm" onclick="panelCancelEdit()">Cancel</button>
    </div>
    <textarea id="panel-editor" style="width:100%;flex:1;min-height:300px;font-family:var(--font-mono);font-size:.83rem;background:var(--bg-input);color:var(--text);border:1px solid var(--border);border-radius:var(--radius);padding:12px;resize:vertical;tab-size:4">${esc(f.content)}</textarea>
    <div style="display:flex;flex-direction:column;gap:8px;margin-top:10px">
      <button class="btn btn-sm" id="panel-draft-btn" onclick="panelSaveDraft()" style="width:100%">
        💾 Save Draft (agent can see changes)
      </button>
      <div style="display:flex;gap:8px;align-items:center;border-top:1px solid var(--border);padding-top:8px">
        <input type="text" id="panel-commit-msg" placeholder="Commit message (required)" style="flex:1" />
        <button class="btn btn-primary btn-sm" id="panel-commit-btn" onclick="panelCommitFile()">
          ✅ Commit
        </button>
      </div>
    </div>
  `;
};

window.panelCancelEdit = function() {
  $('#panel-title').textContent = `📄 ${state.panelFile.name}`;
  renderPanelFileView();
};

window.panelSaveDraft = async function() {
  const content = $('#panel-editor').value;
  const [owner, repo] = state.selectedRepo.split('/');
  const btn = $('#panel-draft-btn');
  btn.disabled = true;
  btn.textContent = 'Saving…';

  try {
    await api(`/api/repos/${owner}/${repo}/draft`, {
      method: 'POST',
      body: { path: state.panelFile.path, content },
    });
    // Update local state
    state.panelFile.content = content;
    state.panelFile.isDraft = true;
    state.panelFile.size = new Blob([content]).size;
    btn.textContent = '✅ Draft Saved!';
    btn.style.borderColor = 'var(--green)';
    btn.style.color = 'var(--green)';
    setTimeout(() => {
      btn.textContent = '💾 Save Draft';
      btn.style.borderColor = '';
      btn.style.color = '';
      btn.disabled = false;
    }, 2000);
  } catch (e) {
    alert('Error saving draft: ' + e.message);
    btn.disabled = false;
    btn.textContent = '💾 Save Draft';
  }
};

window.panelAskReview = function() {
  // Switch to chat and auto-send a review request
  const f = state.panelFile;
  const content = $('#panel-editor')?.value || f.content;
  // Save draft first so agent can see it
  const [owner, repo] = state.selectedRepo.split('/');
  api(`/api/repos/${owner}/${repo}/draft`, {
    method: 'POST',
    body: { path: f.path, content },
  }).then(() => {
    // Send review message to chat
    const msg = `Please review my changes to ${f.path}. Here is the updated content:\n\n\`\`\`\n${content}\n\`\`\`\n\nGive me feedback, suggestions, and any issues you see.`;
    $('#chat-input').value = msg;
    sendMessage();
  }).catch(e => alert('Error: ' + e.message));
};

window.panelCommitFile = async function() {
  const content = $('#panel-editor').value;
  const message = $('#panel-commit-msg').value.trim();
  if (!message) return alert('Commit message is required.');

  const [owner, repo] = state.selectedRepo.split('/');
  const btn = $('#panel-commit-btn');
  btn.disabled = true;
  btn.textContent = 'Committing…';

  try {
    const result = await api(`/api/repos/${owner}/${repo}/draft/commit`, {
      method: 'POST',
      body: { path: state.panelFile.path, content, message },
    });
    state.panelFile.content = content;
    state.panelFile.isDraft = false;
    state.panelFile.size = new Blob([content]).size;
    $('#panel-title').textContent = `📄 ${state.panelFile.name}`;
    renderPanelFileView();
    showCommitToast(result);
  } catch (e) {
    alert('Error: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '✅ Commit to GitHub';
  }
};

window.panelDeleteFile = function() {
  const f = state.panelFile;
  if (!f) return;
  // Reuse the existing delete modal
  confirmDelete(f.path);
};

function renderPanel(toolResults) {
  // Pick the most interesting result to display (prefer file content, then list)
  const panel = $('#panel-body');
  const titleEl = $('#panel-title');
  openPanel();

  // Find the last successful file content result (not one with an error)
  const fileResults = toolResults.filter(t => t.tool === 'get_file_content' && t.result && t.result.type === 'file' && t.result.content);
  const fileResult = fileResults.length ? fileResults[fileResults.length - 1] : null;
  if (fileResult) {
    const r = fileResult.result;
    const filePath = r.path || fileResult.args.path;
    titleEl.textContent = `📄 ${r.name}`;

    // Store current file data for inline editing
    state.panelFile = { path: filePath, content: r.content, name: r.name, size: r.size, url: r.url };

    renderPanelFileView();
    return;
  }

  // Directory listing
  const dirResults = toolResults.filter(t => t.tool === 'list_repo_contents' && Array.isArray(t.result));
  const dirResult = dirResults.length ? dirResults[dirResults.length - 1] : null;
  if (dirResult) {
    const items = dirResult.result;
    const path = dirResult.args.path || '/';
    titleEl.textContent = `📁 ${path || 'root'}`;
    const dirs = items.filter(i => i.type === 'dir').sort((a,b) => a.name.localeCompare(b.name));
    const files = items.filter(i => i.type !== 'dir').sort((a,b) => a.name.localeCompare(b.name));
    panel.innerHTML = `
      <div class="file-list">
        ${dirs.map(d => `<div class="file-row"><span class="icon">📁</span><span class="name dir">${esc(d.name)}/</span></div>`).join('')}
        ${files.map(f => `<div class="file-row"><span class="icon">${fileIcon(f.name)}</span><span class="name">${esc(f.name)}</span><span class="size">${formatSize(f.size)}</span></div>`).join('')}
      </div>
    `;
    return;
  }

  // Commits
  const commitResults = toolResults.filter(t =>
    (t.tool === 'list_commits' || t.tool === 'list_merges' || t.tool === 'detect_rebases') && Array.isArray(t.result)
  );
  const commitResult = commitResults.length ? commitResults[commitResults.length - 1] : null;
  if (commitResult) {
    titleEl.textContent = `📝 ${commitResult.tool.replace('_', ' ')}`;
    panel.innerHTML = commitResult.result.map(c => {
      const msg = (c.message || '').split('\n')[0].slice(0, 80);
      return `
        <div class="item-card">
          <a href="${c.url || '#'}" target="_blank" class="sha">${c.short_sha}</a>
          <div class="details">
            <div class="title">${esc(msg)}</div>
            <div class="info">👤 ${esc(c.author)} · 📅 ${String(c.date).slice(0, 10)}</div>
          </div>
        </div>`;
    }).join('') || '<div class="empty">No results.</div>';
    return;
  }

  // Commit detail
  const detailResults = toolResults.filter(t => t.tool === 'get_commit_detail' && t.result && !t.result.error);
  const detailResult = detailResults.length ? detailResults[detailResults.length - 1] : null;
  if (detailResult) {
    const c = detailResult.result;
    titleEl.textContent = `📝 Commit ${c.short_sha}`;
    const filesHtml = (c.files || []).map(f =>
      `<div style="display:flex;gap:8px;font-size:.83rem;padding:4px 0">
        <span style="color:${f.status === 'added' ? 'var(--green)' : f.status === 'removed' ? 'var(--red)' : 'var(--orange)'}">${f.status}</span>
        <span>${esc(f.filename)}</span>
        <span style="color:var(--green)">+${f.additions}</span>
        <span style="color:var(--red)">-${f.deletions}</span>
      </div>`
    ).join('');
    panel.innerHTML = `
      <div style="margin-bottom:12px">
        <strong>${esc((c.message || '').split('\\n')[0])}</strong><br/>
        <span style="color:var(--text-muted);font-size:.83rem">👤 ${esc(c.author)} · 📅 ${String(c.date).slice(0, 10)}</span>
      </div>
      ${filesHtml}
    `;
    return;
  }

  // Branches
  const branchResults = toolResults.filter(t => t.tool === 'list_branches' && Array.isArray(t.result));
  const branchResult = branchResults.length ? branchResults[branchResults.length - 1] : null;
  if (branchResult) {
    titleEl.textContent = '🌿 Branches';
    panel.innerHTML = branchResult.result.map(b => `
      <div class="item-card">
        <span class="sha">${b.sha.slice(0, 7)}</span>
        <div class="details"><div class="title">🌿 ${esc(b.name)}${b.protected ? ' <span class="badge badge-protected">protected</span>' : ''}</div></div>
      </div>
    `).join('') || '<div class="empty">No branches.</div>';
    return;
  }

  // PRs
  const prResults = toolResults.filter(t => t.tool === 'list_pull_requests' && Array.isArray(t.result));
  const prResult = prResults.length ? prResults[prResults.length - 1] : null;
  if (prResult) {
    titleEl.textContent = '🔀 Pull Requests';
    panel.innerHTML = prResult.result.map(pr => {
      const icon = pr.state === 'open' ? '🟢' : (pr.merged ? '🟣' : '🔴');
      return `
        <div class="item-card">
          <span class="sha">#${pr.number}</span>
          <div class="details">
            <div class="title">${icon} ${esc(pr.title)}</div>
            <div class="info">👤 ${esc(pr.author)} · <a href="${pr.url}" target="_blank">View →</a></div>
          </div>
        </div>`;
    }).join('') || '<div class="empty">No PRs.</div>';
    return;
  }

  // Repo summary
  const summaryResults = toolResults.filter(t => t.tool === 'repo_summary' && t.result && !t.result.error);
  const summaryResult = summaryResults.length ? summaryResults[summaryResults.length - 1] : null;
  if (summaryResult) {
    const r = summaryResult.result;
    titleEl.textContent = `📊 ${r.full_name}`;
    panel.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:8px">
        <div><strong>${esc(r.full_name)}</strong></div>
        <div style="color:var(--text-muted)">${esc(r.description || 'No description')}</div>
        <div>⭐ ${r.stars} · 🍴 ${r.forks} · 🐛 ${r.open_issues}</div>
        <div>🌿 ${esc(r.default_branch)} · ${esc(r.language || '—')}</div>
        <a href="${r.url}" target="_blank">View on GitHub →</a>
      </div>
    `;
    return;
  }

  // Fallback: show raw JSON of last tool result
  const last = toolResults[toolResults.length - 1];
  titleEl.textContent = `⚙️ ${last.tool}`;
  panel.innerHTML = `<pre><code>${esc(JSON.stringify(last.result, null, 2))}</code></pre>`;
}

// ══════════════════════════════════════════════════════════════════════════
// CLASSROOM TAB
// ══════════════════════════════════════════════════════════════════════════

let _classroomState = { classrooms: [], currentClassroom: null, currentAssignment: null };

$('#btn-load-classrooms').onclick = async () => {
  const container = $('#classrooms-container');
  container.innerHTML = '<div class="loading-center"><div class="spinner"></div></div>';
  try {
    const classrooms = await api('/api/classrooms');
    _classroomState.classrooms = classrooms;
    if (!classrooms.length) {
      container.innerHTML = '<div class="empty">No classrooms found. You need to be a classroom admin.</div>';
      return;
    }
    container.innerHTML = classrooms.map(c => `
      <div class="item-card" style="cursor:pointer" onclick="openClassroom(${c.id})">
        <span style="font-size:1.5rem">🎓</span>
        <div class="details">
          <div class="title">${esc(c.name)}</div>
          <div class="info">${c.archived ? '📦 Archived' : '🟢 Active'} · ID: ${c.id}</div>
        </div>
      </div>
    `).join('');
  } catch (e) {
    container.innerHTML = `<div class="empty">❌ ${e.message}</div>`;
  }
};

window.openClassroom = async function(classroomId) {
  _classroomState.currentClassroom = classroomId;
  $('#classroom-list').classList.add('hidden');
  $('#assignment-detail').classList.add('hidden');
  const detail = $('#classroom-detail');
  detail.classList.remove('hidden');
  const container = $('#assignments-container');
  container.innerHTML = '<div class="loading-center"><div class="spinner"></div></div>';

  try {
    const assignments = await api(`/api/classrooms/${classroomId}/assignments`);
    if (!assignments.length) {
      container.innerHTML = '<div class="empty">No assignments in this classroom.</div>';
      return;
    }
    container.innerHTML = '<h3 style="margin-bottom:12px">📋 Assignments</h3>' +
      assignments.map(a => {
        const deadline = a.deadline ? `📅 ${a.deadline.slice(0, 10)}` : 'No deadline';
        return `
          <div class="item-card" style="cursor:pointer" onclick="openAssignment(${a.id})">
            <span style="font-size:1.3rem">📝</span>
            <div class="details">
              <div class="title">${esc(a.title)}</div>
              <div class="info">
                ${a.type} · ${esc(a.language || '—')} · ${deadline}
              </div>
              <div class="info" style="margin-top:4px">
                ✅ ${a.accepted} accepted · 📤 ${a.submitted} submitted · 🏆 ${a.passing} passing
              </div>
            </div>
          </div>`;
      }).join('');
  } catch (e) {
    container.innerHTML = `<div class="empty">❌ ${e.message}</div>`;
  }
};

$('#btn-back-classrooms').onclick = () => {
  $('#classroom-detail').classList.add('hidden');
  $('#classroom-list').classList.remove('hidden');
};

window.openAssignment = async function(assignmentId) {
  _classroomState.currentAssignment = assignmentId;
  $('#classroom-detail').classList.add('hidden');
  const detail = $('#assignment-detail');
  detail.classList.remove('hidden');
  const container = $('#submissions-container');
  container.innerHTML = '<div class="loading-center"><div class="spinner"></div></div>';

  try {
    const [submissions, grades] = await Promise.all([
      api(`/api/assignments/${assignmentId}/submissions`),
      api(`/api/assignments/${assignmentId}/grades`).catch(() => []),
    ]);

    // Build a grade lookup by username
    const gradeLookup = {};
    for (const g of grades) {
      gradeLookup[g.github_username] = g;
    }

    let html = '<h3 style="margin-bottom:12px">👩‍🎓 Student Submissions</h3>';

    if (!submissions.length) {
      html += '<div class="empty">No submissions yet.</div>';
    } else {
      html += submissions.map(s => {
        const student = s.students[0] || {};
        const grade = gradeLookup[student.login] || {};
        const pts = grade.points_awarded != null
          ? `${grade.points_awarded}/${grade.points_available}`
          : (s.grade || '—');
        const statusIcon = s.passing ? '✅' : (s.submitted ? '📤' : '⏳');
        const repoLink = s.repository.html_url
          ? `<a href="${s.repository.html_url}" target="_blank">${s.repository.full_name}</a>`
          : s.repository.full_name || '—';

        return `
          <div class="item-card">
            <img src="${student.avatar_url || ''}" style="width:36px;height:36px;border-radius:50%;border:1px solid var(--border)" />
            <div class="details">
              <div class="title">
                ${statusIcon}
                <a href="${student.html_url || '#'}" target="_blank">${esc(student.login || 'Unknown')}</a>
                <span style="float:right;font-family:var(--font-mono);font-size:.85rem">${pts}</span>
              </div>
              <div class="info">
                📂 ${repoLink} · 📝 ${s.commit_count} commits
                ${grade.submission_timestamp ? ' · 📅 ' + grade.submission_timestamp : ''}
              </div>
            </div>
          </div>`;
      }).join('');
    }

    container.innerHTML = html;
  } catch (e) {
    container.innerHTML = `<div class="empty">❌ ${e.message}</div>`;
  }
};

$('#btn-back-assignments').onclick = () => {
  $('#assignment-detail').classList.add('hidden');
  $('#classroom-detail').classList.remove('hidden');
};

// ── Escape HTML ───────────────────────────────────────────────────────────
function esc(str) {
  if (!str) return '';
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

// ── Init ──────────────────────────────────────────────────────────────────
if (state.token) {
  loadRepos();
} else {
  showScreen('login-screen');
}
