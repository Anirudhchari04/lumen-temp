# Lumen for Outlook — Chrome Extension

AI-powered email reading and composing in Outlook Web, powered by your Lumen agent. No app passwords, no admin consent, no Mail.* scopes. Works on any Outlook account because it operates inside your already-authenticated browser session.

---

## How It Works

```
You on Outlook Web (signed in)
   │
   ├── Extension reads the email DOM
   ├── Sends content + your instruction to Lumen (your existing backend)
   ├── Lumen generates a reply via Azure OpenAI
   └── Extension injects the reply into the Outlook compose window
       You click "Send" inside Outlook — Outlook sends it as you
```

Lumen never connects to your mailbox. It only sees email text you explicitly send through "Read" or "Generate" buttons.

---

## Install (30 seconds)

1. Open Chrome → go to **`chrome://extensions`**
2. Toggle **Developer mode** (top-right)
3. Click **Load unpacked**
4. Select this `extension/` folder
5. Pin the extension to your toolbar (puzzle icon → pin)

---

## First-Time Setup

1. **Sign in to Lumen** at [lumen-demo.azurewebsites.net](https://lumen-demo.azurewebsites.net) using any account type (Microsoft, Google, or email+password). Leave that tab open.
2. **Open Outlook Web** at [outlook.office.com](https://outlook.office.com) or [outlook.live.com](https://outlook.live.com).
3. Click the **Lumen extension icon** in your toolbar — the side panel opens.
4. The header should say **"✓ {your name}"** — that means the extension auto-detected your Lumen session.
   - If it says "sign in →", click it; the extension opens Lumen's login page for you.

---

## Using the Extension

### Read & Reply tab

1. Open any email in Outlook
2. Click **📥 Read Current Email** — the subject, sender, and body fill in the panel
3. Type an instruction like *"Reply formally declining the meeting and suggest Friday instead"*
4. Click **🤖 Generate Reply**
5. The AI reply appears below; click **💉 Inject into Compose** to drop it straight into Outlook's reply window
6. Review, edit if needed, then click **Send** inside Outlook

### Compose tab

1. Fill **To** + **Subject**
2. Describe the email in plain English: *"Let Sarah know I'll be late to the standup tomorrow because of a doctor's appointment"*
3. Click **🤖 Generate Email**
4. Click **📬 Open & Fill Compose** — Outlook opens a new mail with all three fields populated
5. Review, click Send

---

## What Lumen Knows About Your Emails

Every email you generate and inject through the extension is logged to your Lumen outbox. You can ask Lumen things like:

- *"What emails did I send today?"*
- *"Show my sent messages from this week"*
- *"What did I reply to the professor?"*

The extension calls `POST /lumen/comm/extension/log-sent` after each successful inject, so this works automatically.

---

## Configuration

The side panel has a **Backend** input at the top. By default it points to `https://lumen-demo.azurewebsites.net`. If you're running Lumen locally, change it to `http://localhost:8000` — the setting persists across sessions.

---

## Troubleshooting

### "Sign in →" stays red even though I'm signed in to Lumen
- The extension needs to find an open Lumen tab. Make sure `lumen-demo.azurewebsites.net` is open in any tab in your current Chrome window.
- Try refreshing the Lumen tab, then click the connection indicator.

### "Open this on outlook.office.com first"
- The extension only reads DOM on Outlook URLs. Native Outlook desktop, Teams, or other tabs won't work — only the web app.

### "Could not find compose editor" when I inject
- Outlook's UI may have changed. Open the reply pane manually first, then click Inject.
- File a bug — DOM selectors live in `content.js` and may need an update.

### Generated reply is too long / off-topic
- Improve the instruction. The model uses your instruction + the email body as context.
- For sensitive replies, always review the text before clicking Send in Outlook.

### Backend returns 401 / "Session expired"
- Your Lumen JWT expired (24h lifetime). Refresh the Lumen tab to sign in again.

---

## Outlook DOM Fragility

Outlook Web uses obfuscated class names that change frequently. We rely on `aria-label`, `role`, and `data-testid` attributes which Microsoft tends to keep stable, but they can change too. If something stops working:

1. Open Outlook DevTools → Elements
2. Inspect the element that's failing (e.g. the compose body)
3. Find a stable attribute (look for `aria-label`, `data-testid`, `role`)
4. Update the matching selector in `content.js`
5. Reload the extension at `chrome://extensions`

The selector lists are at the top of each helper function in [content.js](content.js) — easy to extend without rewriting logic.

---

## File Structure

| File | Purpose |
|------|---------|
| `manifest.json` | MV3 permissions, host access, side panel registration |
| `background.js` | Service worker; opens side panel + reads Lumen JWT from open Lumen tabs |
| `content.js` | Runs on Outlook pages; DOM reader + injector |
| `sidebar.html` + `sidebar.js` | Side panel UI + backend calls |
| `icon.png` | 128×128 toolbar icon |

---

## Privacy

- The extension sends email content to your Lumen backend **only** when you click a "Read", "Generate", or "Inject" button.
- Nothing is sent automatically.
- The Lumen JWT is auto-read from your Lumen tab (same as if you opened the page yourself).
- No third-party services. The only network targets are: your Outlook tab (DOM only), your Lumen backend, and chrome storage.
