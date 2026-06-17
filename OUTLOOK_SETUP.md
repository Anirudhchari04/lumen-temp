# Outlook Email Integration — Setup Guide

Lumen connects to Outlook/Office 365 email via **IMAP + SMTP with app passwords** — no Entra app registration, no admin consent, no corporate tenant policy issues.

---

## How It Works

```
User chats with Comms Agent: "connect my email"
    ↓
ConnectEmailCard appears in chat
    ↓
User enters email + app password
    ↓
Backend tests IMAP connection, encrypts password with Fernet, stores in Cosmos
    ↓
User chats: "check my inbox" / "email Dr. Smith about..."
    ↓
Comms Agent fetches via IMAP / sends via SMTP using stored creds
```

**Zero Microsoft compliance touch points** — your code never sees Entra/Graph.

---

## User Setup (Per User, One Time)

### 1. Get an App Password from Microsoft

1. Sign in to **[account.microsoft.com](https://account.microsoft.com)**
2. Go to **Security → Advanced security options**
3. Under **App passwords**, click **Create a new app password**
4. Name it "Lumen" → click **Next**
5. **Copy the 16-character password** (you won't see it again)

> If you don't see "App passwords":
> - You may need to enable **two-step verification** first
> - For org accounts (`@college.edu`), your admin may need to allow app passwords in Entra ID

### 2. Connect in Lumen

In the chat with Lumen:

```
You: connect my email
Lumen: [shows ConnectEmailCard]

You: [Fill in]
     Email: yourname@college.edu
     App password: xxxxxxxxxxxxxxxx
     [Click Connect]

Lumen: ✓ Email connected: yourname@college.edu
```

### 3. Start Using It

```
You: check my inbox
You: any unread emails?
You: search my email for blockchain
You: any email from professor smith?
You: send an email to alex about the project deadline
```

---

## Server Settings (Auto-Detected by Default)

For most Microsoft/Outlook accounts, defaults work:

| Setting | Default |
|---------|---------|
| IMAP host | `outlook.office365.com` |
| IMAP port | `993` (SSL) |
| SMTP host | `smtp.office365.com` |
| SMTP port | `587` (STARTTLS) |

The ConnectEmailCard has an "Advanced" section to override these for other providers:

| Provider | IMAP | SMTP |
|----------|------|------|
| **Outlook/M365** | `outlook.office365.com:993` | `smtp.office365.com:587` |
| **Gmail** | `imap.gmail.com:993` | `smtp.gmail.com:587` |
| **Yahoo** | `imap.mail.yahoo.com:993` | `smtp.mail.yahoo.com:587` |
| **iCloud** | `imap.mail.me.com:993` | `smtp.mail.me.com:587` |

---

## Security

| Aspect | Implementation |
|--------|---------------|
| **Password encryption** | Fernet symmetric encryption (key derived from `JWT_SECRET` via SHA256) |
| **Storage** | Cosmos DB `lumens` container, field `email_config.password_encrypted` |
| **Transit** | Plain only over HTTPS to backend; SMTP/IMAP use STARTTLS/SSL |
| **Frontend** | Cleared from React state immediately after successful connect |
| **Decryption** | Backend only, never returned to client |
| **Rotation** | Changing `JWT_SECRET` invalidates all stored passwords; users must reconnect |

⚠️ **Important**: Set a strong `JWT_SECRET` (≥32 chars) in Azure App Service config **before** users connect email. If you rotate it later, stored passwords become unreadable.

```powershell
# Generate and set
$secret = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 48 | ForEach-Object {[char]$_})
az webapp config appsettings set --name lumen-demo --resource-group nexus-rg --settings JWT_SECRET=$secret
```

---

## Backend Endpoints

All require `Authorization: Bearer <lumen-jwt>` header.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/lumen/email/connect` | POST | Test connection + store encrypted credentials |
| `/lumen/email/connection-status` | GET | Is email connected? (returns `{connected, email}`) |
| `/lumen/email/connect` | DELETE | Remove stored credentials |
| `/lumen/email/imap/inbox?limit=N&unread_only=true` | GET | Fetch inbox |
| `/lumen/email/imap/{msg_id}` | GET | Full email body |
| `/lumen/email/imap/search?query=X` | GET | Search by subject or `from:X` |
| `/lumen/email/imap/send` | POST | Send email `{to, subject, body}` |

These are **direct REST endpoints** — most users won't use them. The Comms Agent (chat) calls them under the hood.

---

## Comms Agent Triggers

Natural-language phrases the agent recognizes:

| User says | Action |
|-----------|--------|
| "connect my email" / "set up outlook" | Show ConnectEmailCard |
| "disconnect my email" | Delete stored credentials |
| "check my inbox" / "check email" | List recent emails via IMAP |
| "any unread emails" / "new emails" | List unread only |
| "search my email for X" / "find emails about X" | IMAP search by subject |
| "any email from X" | IMAP search by sender |
| "send email to X about Y" | Compose draft → user says "send" → sends via SMTP |

---

## Troubleshooting

### "Could not connect to IMAP server"
- Wrong email or app password
- App passwords not enabled for the account
- Org admin has blocked app passwords (try a personal account instead)
- Firewall blocking outbound 993/587

### "SMTP send failed"
- Same as above — check the same things
- Some orgs require sender = authenticated user; the From: header must match your email

### "Could not decrypt credentials"
- `JWT_SECRET` was changed — reconnect email
- Check Azure App Service config didn't get reset

### Org blocks app passwords entirely
- Ask admin: "Enable per-user app passwords in Entra ID under Security → Authentication methods"
- Or: use a different account (personal Microsoft account, Gmail, etc.)

---

## Architecture Notes

**Why IMAP/SMTP instead of Microsoft Graph?**

Microsoft Graph requires an Entra app registration with admin-granted `Mail.Read`/`Mail.Send` scopes. Many corporate tenants (including the one Lumen was originally built for) have policies that block creating multi-tenant or public-client apps. IMAP/SMTP with app passwords sidesteps this entirely — it's an account-level feature, not a tenant-level one.

**Why Fernet instead of just hashing?**

Passwords need to be *decryptable* to use them for IMAP/SMTP auth on every email read/send. We can't hash them. Fernet (AES-128-CBC + HMAC-SHA256) is the simplest symmetric encryption that's hard to misuse.

**Why derive the Fernet key from JWT_SECRET?**

One secret, one secret to rotate, one secret to lose. App Service already has this set securely. Adding a separate `EMAIL_ENCRYPTION_KEY` doubles operational burden without a meaningful security gain for this threat model (a backend compromise gives an attacker both keys anyway).
