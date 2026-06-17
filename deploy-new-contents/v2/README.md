# Lumen v2 — Magentic-One Orchestration

Lumen v2 replaces v1's custom intent-router + A2A-HTTP orchestration with
**Magentic-One** (Microsoft's planner, via the `autogen-agentchat` /
`autogen-ext` packages). It keeps **all** of v1's tool implementations,
integrations, and API logic intact — v2 only swaps the *orchestration layer*.

It is fully additive: everything lives under `/v2`, v2 imports v1 (never the
reverse), and the only change to a v1 file is a single guarded `include_router`
block in `app/main.py`.

---

## Architecture

```
POST /v2/chat
   │
   ▼
v2/router.py ──► v2/orchestrator.run_chat()
                    │  builds an Azure OpenAI model client (Entra ID, v1 creds)
                    │  + a MagenticOneGroupChat over the specialists
                    ▼
              v2/runtime.build_team()   ← AutoGen runtime + agent registration
                    │
   ┌────────────────┴───────────────────────────────────────────┐
   ▼            ▼            ▼            ▼          ▼      ...     ▼
 general   communication  calendar   portfolio  shiksha        wolfram
   │            │            │            │          │             │
   └─ each is a thin AutoGen AssistantAgent whose ONE tool calls the matching
      v1 `_handle_*` function in app/agents/interaction_manager.py AS-IS.

         (Task Ledger + Progress Ledger) ──► Cosmos `lumen_v2_sessions`  (v2/ledger.py)
```

- **`MagenticOneGroupChat`** *is* the Magentic-One orchestrator — it instantiates
  autogen's internal `MagenticOneOrchestrator`, which owns the **Task Ledger**
  (facts + plan) and **Progress Ledger** (who-acts-next) and drives the agents
  sequentially until the task is done.
- **Specialist agents** (`v2/agents/*.py`) are 1:1 with v1's agents and keep v1's
  names (`communication`, `calendar`, `portfolio`, `shiksha`, `graph`, `gmail`,
  `drive`, `notion`, `arxiv`, `wolfram`, `social`, `general`). Each registers one
  or two tools that are thin closures over a v1 `_handle_*` handler — so token
  resolution, the v1 LLM calls, multi-turn draft state, and the real tool
  functions (Gmail/Outlook/GitHub/Notion/Drive/…) are all **reused, not copied**.
- **AutoGen's runtime** (inside the group chat) replaces v1's A2A HTTP self-calls
  for inter-agent messaging within a v2 turn.
- **Ledgers** are mirrored to the new Cosmos container `lumen_v2_sessions`
  (`v2/ledger.py`), with an in-memory fallback when Cosmos is absent. No v1
  container is ever touched.

### Why not `autogen-ext[magentic-one]`?
The `[magentic-one]` extra only pulls the built-in `MultimodalWebSurfer` /
`FileSurfer` / `Coder` agents and their heavy deps (playwright, markitdown,
onnxruntime). v2 brings its own specialists (the v1 agents), so we install the
lean `autogen-ext[openai]` instead. See `v2/requirements.txt`.

---

## Files

| File | Purpose |
|------|---------|
| `v2/config.py` | Reads v1's loaded settings + optional v2-only env vars. Writes nothing. |
| `v2/model_client.py` | `AzureOpenAIChatCompletionClient` via Entra ID token provider (same auth as v1). |
| `v2/agents/*.py` | One thin AutoGen `AssistantAgent` wrapper per v1 specialist. |
| `v2/runtime.py` | Registers all specialists into a `MagenticOneGroupChat`. |
| `v2/orchestrator.py` | Runs one task, streams turns, mirrors ledgers, returns a v1-shaped dict. |
| `v2/ledger.py` | Task/Progress Ledger persistence to the `lumen_v2_sessions` container. |
| `v2/router.py` | `GET /v2/health`, `POST /v2/chat` (same auth + request schema as v1). |
| `v2/requirements.txt` | autogen deps, kept separate from root `requirements.txt`. |
| `v2/.env.example` | v2-relevant + v2-specific env vars. |

---

## Running locally

1. **Install v2 deps** (into the same venv as v1):
   ```bash
   pip install -r v2/requirements.txt
   ```
2. **Use the same `.env` as v1.** v2 needs no new secrets. The only required
   values are the ones v1 already uses: `AZURE_OPENAI_ENDPOINT`,
   `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_VERSION`, a working Azure
   credential (managed identity client id or a logged-in `az login` /
   `DefaultAzureCredential`), `JWT_SECRET`, and (optionally) `COSMOS_ENDPOINT`.
   Optional v2 knobs are in `v2/.env.example`.
3. **Run the app exactly as for v1** (the v2 router auto-mounts):
   ```bash
   python -m app.main          # or: uvicorn app.main:app --reload
   ```
4. **Smoke test:**
   ```bash
   curl http://localhost:8000/v2/health
   # POST needs the same Bearer JWT as v1:
   curl -X POST http://localhost:8000/v2/chat \
        -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
        -d '{"message":"summarize my inbox"}'
   ```

`GET /v2/health` returns 200 with no credentials. `POST /v2/chat` requires a valid
v1 JWT and a reachable Azure OpenAI deployment (it makes real model calls).

---

## Switching the frontend between v1 and v2

Both endpoints accept the **same request body** (`{message, thread_id,
graph_token}`) and return the same core fields (`reply`, `action`, `intent`,
`agent_id`, `thread_id`). v2 adds `session_id` and a `turns` array.

So the only change the frontend needs is the URL:

```js
// one flag decides the orchestration backend; no other change
const CHAT_URL = useV2 ? "/v2/chat" : "/chat";
const res = await fetch(CHAT_URL, {
  method: "POST",
  headers: { Authorization: `Bearer ${jwt}`, "Content-Type": "application/json" },
  body: JSON.stringify({ message, thread_id, graph_token }),
});
```

Per the constraints, the flag must come from app state / config — **not**
`localStorage` or any browser storage.

---

## Known limitations vs v1

- **Streaming:** v1's `/ag-ui/chat` SSE endpoint is not reproduced. `/v2/chat`
  mirrors v1's non-streaming `POST /chat` (returns a JSON dict). The orchestrator
  already streams turns internally (`run_stream`); exposing an SSE `/v2/chat/stream`
  is a future add.
- **Rich cards / A2UI:** v2 returns the specialist's `reply` **text**. v1's
  structured `cards`, `a2ui`, `proposal`, and `redirect_url` payloads are produced
  inside the `_handle_*` handlers but are currently collapsed to text by the
  AutoGen tool boundary. Surfacing them requires threading the full dict through
  (planned).
- **Multi-turn confirmation flows:** v1 keeps per-user pending state (email draft
  refine/confirm, study-plan "yes/no") in module globals. Those still work
  *within* a handler call, but Magentic-One re-plans each `/v2/chat` request fresh,
  so a "send it" confirmation isn't yet wired across v2 turns.
- **Latency / cost:** Magentic-One adds an orchestration-planning LLM pass on top
  of each specialist's own LLM call, so a v2 turn costs more tokens than the v1
  fast-path. `LUMEN_V2_MAX_TURNS` bounds worst-case turns.
- **Sequential only:** agents run one-at-a-time (Magentic-One's design and an
  explicit v2 constraint). No parallel agent execution.
- **Deployment:** the Azure deploy must also run `pip install -r v2/requirements.txt`
  (root `requirements.txt` is intentionally left untouched).
