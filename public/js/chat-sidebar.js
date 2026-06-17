/**
 * Chat History Sidebar — reusable across Lumen and TA pages.
 * Provides: loadThreads(), newChat(), switchThread(), sidebar rendering.
 *
 * Requires: CHANNEL (string), api() function, addMessage() function, clearMessages() function
 * to be defined before loading this script.
 */

let threads = [];
let currentThreadId = null;

async function loadThreads() {
    try {
        const r = await api(`/chat/threads/${CHANNEL}`);
        threads = await r.json();
        renderThreadList();
    } catch {
        document.getElementById('threadList').innerHTML = '<div style="color:#666;font-size:12px;padding:8px">No conversations yet</div>';
    }
}

function renderThreadList() {
    const el = document.getElementById('threadList');
    if (!threads.length) {
        el.innerHTML = '<div style="color:#666;font-size:12px;padding:8px">No conversations yet. Start chatting!</div>';
        return;
    }
    el.innerHTML = threads.map(t => {
        const active = t.id === currentThreadId ? ' thread-active' : '';
        const time = t.updated_at ? new Date(t.updated_at).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}) : '';
        return `<div class="thread-item${active}" onclick="switchThread('${t.id}')">
            <div class="thread-title">${t.title || 'New Chat'}</div>
            <div class="thread-meta">${t.message_count} msgs · ${time}</div>
        </div>`;
    }).join('');
}

async function newChat() {
    try {
        const r = await api(`/chat/threads/${CHANNEL}`, { method: 'POST' });
        const data = await r.json();
        currentThreadId = data.id;
        clearMessages();
        await loadThreads();
    } catch (e) { console.error('New chat error:', e); }
}

async function switchThread(threadId) {
    currentThreadId = threadId;
    clearMessages();
    try {
        const r = await api(`/chat/thread/${threadId}`);
        const data = await r.json();
        if (data.messages?.length) {
            data.messages.forEach(m => {
                addMessage(m.role === 'user' ? 'user' : 'assistant', m.content, m.role === 'assistant' ? CHANNEL_META : '');
            });
        }
    } catch {}
    renderThreadList();
}
