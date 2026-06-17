```mermaid
flowchart TD
    U[User / Browser] --> ChatAPI[FastAPI chat routes]
    ChatAPI --> IM[Interaction Manager<br/>app/agents/interaction_manager.py]

    IM --> A2AClient[Shared A2A Client<br/>app/agents/a2a_client.py]
    A2AClient --> InternalA2A[Internal Agent A2A Endpoint<br/>POST /a2a/{agent_id}]
    A2AClient --> PeerA2A[Peer Lumen A2A Endpoint<br/>POST /a2a/lumen/{user_id}]

    InternalA2A --> A2AAdapter[A2A Protocol Adapter<br/>app/protocols/a2a.py]
    A2AAdapter --> Calendar[Calendar Agent<br/>/a2a/calendar]
    A2AAdapter --> Communication[Communication Agent<br/>/a2a/communication]
    A2AAdapter --> Portfolio[Portfolio Agent<br/>/a2a/portfolio]
    A2AAdapter --> Shiksha[Shiksha Bridge<br/>/a2a/shiksha]
    A2AAdapter --> Graph[Microsoft Graph Agent<br/>/a2a/graph]
    A2AAdapter --> ExternalTA[External Registered Agent<br/>endpoint URL from registry]

    PeerA2A --> LumenA2A[Lumen-to-Lumen Protocol<br/>app/protocols/lumen_a2a.py]
    LumenA2A --> PeerStore[Peer Messages / Lumen State]

    Registry[Agent Registry<br/>app/orchestrator/registry.py] --> InternalA2A
    Registry --> ExternalTA
    Registry --> Cards[Agent Cards]

    Cards --> AgentCardSchema[AgentCard Models<br/>app/protocols/models.py]
    Cards --> WellKnown[/.well-known/agent-card.json]
    Cards --> AgentCardURL[/agents/{slug}/agent-card.json]
    Cards --> LumenCardURL[/a2a/lumen/{user_id}/agent-card.json]
    Cards --> Directory[/agents/directory]

    Cosmos[(Cosmos DB)]
    Disk[(Disk fallback JSON)]
    Registry --> Cosmos
    Registry --> Disk
    PeerStore --> Cosmos
    PeerStore --> Disk
```

# Lumen A2A Architecture Explainer

## The Files To Open

These are the files worth showing in order:

1. `app/main.py` - app startup, router mounting, well-known system card, public agent directory.
2. `app/protocols/models.py` - A2A card schema used by every agent.
3. `app/orchestrator/registry.py` - internal agent IDs, external registration, slug generation, and identity rules.
4. `app/protocols/a2a.py` - JSON-RPC endpoint for internal agents and external pass-through.
5. `app/agents/a2a_client.py` - common client used to send `tasks/send` calls.
6. `app/protocols/lumen_a2a.py` - every user Lumen as an addressable peer A2A agent.
7. `app/routes/lumen_social.py` - peer discovery, LITP cards, and peer connection flow.
8. `app/lumen/core.py` - Lumen identity, `lumen_id`, persistence, and discoverability defaults.
9. `app/db/cosmos.py` - Cosmos containers for Lumens, peer messages, and external agents.
10. Agent files with their cards:
    - `app/agents/calendar_agent.py`
    - `app/agents/communication_agent.py`
    - `app/agents/portfolio_agent.py`
    - `app/agents/shiksha_agent.py`
    - `app/agents/graph_agent.py`

## Big Picture

Lumen uses A2A in two layers:

1. Specialist internal agents are exposed as A2A endpoints under `/a2a/{agent_id}`.
2. Each user has a personal Lumen that is also exposed as an A2A peer under `/a2a/lumen/{user_id}`.

Discovery is card-driven. Agents describe themselves through A2A agent cards. The cards advertise each agent's name, supported JSON-RPC endpoint, skills, input/output modes, security requirements, and capabilities.

Routing is ID-driven:

- Internal agent identity is `{base_url}/a2a/{agent_id}`.
- Internal routing slug is the same as `agent_id`, for example `calendar`.
- External agent identity is its full endpoint URL.
- External routing slug is derived from the last path segment of its endpoint URL.
- Lumen peer identity is the user's `lumen_id`, while the addressable A2A endpoint is `{base_url}/a2a/lumen/{user_id}`.

## App Startup And Route Mounting

`app/main.py` derives the service base URL, initializes Cosmos, hydrates the external agent registry, and mounts the A2A routers.

```python
# app/main.py
if not settings.app_base_url:
    _host = _os.environ.get("WEBSITE_HOSTNAME", "")
    settings.app_base_url = f"https://{_host}" if _host else f"http://localhost:{settings.port}"

cosmos_ok = await init_cosmos()

from app.orchestrator.registry import load_registry_from_cosmos
await load_registry_from_cosmos()
```

```python
# app/main.py
from app.orchestrator.registry import router as agents_router
from app.protocols.a2a import router as a2a_router
from app.protocols.lumen_a2a import router as lumen_a2a_router

app.include_router(agents_router, prefix="/agents")
app.include_router(a2a_router)
app.include_router(lumen_a2a_router)
```

The base URL matters because all cards are generated with absolute interface URLs such as `https://host/a2a/calendar`.

## A2A Card Schema

Every internal agent returns an `AgentCard` from its `get_agent_card()` function. The schema lives in `app/protocols/models.py`.

```python
class AgentInterface(BaseModel):
    url: str
    protocolBinding: str = "JSONRPC"
    protocolVersion: str = "1.0"

class AgentSkill(BaseModel):
    id: str
    name: str
    description: str
    tags: list[str] = []
    examples: list[str] = []
    inputModes: list[str] = []

class AgentCard(BaseModel):
    name: str
    description: str
    version: str = "1.0.0"
    documentationUrl: str = ""
    provider: AgentProvider
    supportedInterfaces: list[AgentInterface]
    capabilities: AgentCapabilities
    defaultInputModes: list[str] = ["text/plain"]
    defaultOutputModes: list[str] = ["text/plain"]
    securitySchemes: dict = {}
    securityRequirements: list[dict] = []
    skills: list[AgentSkill] = []
```

The most important field for routing is `supportedInterfaces`. It tells another agent where to send JSON-RPC.

## Internal Agent Definitions

Internal agents are registered in one hard-coded routing table.

```python
# app/orchestrator/registry.py
AGENT_ROUTES: dict[str, str] = {
    "calendar":      "app.agents.calendar_agent",
    "communication": "app.agents.communication_agent",
    "portfolio":     "app.agents.portfolio_agent",
    "shiksha":       "app.agents.shiksha_agent",
    "graph":         "app.agents.graph_agent",
}
```

For internal agents:

- `agent_id` is the routing slug.
- The URL is `{base_url}/a2a/{agent_id}`.
- The card URL is `{base_url}/agents/{agent_id}/agent-card.json`.

Example: the Calendar Agent card advertises `/a2a/calendar`.

```python
# app/agents/calendar_agent.py
return AgentCard(
    name="Calendar Agent",
    description="Study plan generation, event scheduling, and calendar management...",
    provider=AgentProvider(organization="Lumen Network", url=base_url),
    supportedInterfaces=[AgentInterface(url=f"{base_url}/a2a/calendar")],
    capabilities=AgentCapabilities(streaming=False, pushNotifications=True),
    defaultInputModes=["text/plain"],
    defaultOutputModes=["text/plain", "application/json"],
    skills=[
        AgentSkill(id="calendar.generate_study_plan", name="Generate Study Plan", ...),
        AgentSkill(id="calendar.schedule_event", name="Schedule Event", ...),
        AgentSkill(id="calendar.get_events", name="Get Events", ...),
    ],
)
```

Other internal examples follow the same pattern:

```python
# app/agents/communication_agent.py
supportedInterfaces=[AgentInterface(url=f"{base_url}/a2a/communication")]
```

```python
# app/agents/portfolio_agent.py
supportedInterfaces=[AgentInterface(url=f"{base_url}/a2a/portfolio")]
```

```python
# app/agents/shiksha_agent.py
supportedInterfaces=[AgentInterface(url=f"{base_url}/a2a/shiksha")]
```

```python
# app/agents/graph_agent.py
supportedInterfaces=[AgentInterface(url=f"{base_url}/a2a/graph")]
```

## Agent Discovery

There are multiple discovery surfaces.

### 1. System Well-Known Card

`/.well-known/agent-card.json` exposes the top-level Lumen orchestrator card.

```python
# app/main.py
@app.get("/.well-known/agent-card.json")
async def lumen_system_card(request: Request):
    base = str(request.base_url).rstrip("/")
    return {
        "name": "Lumen",
        "provider": {"organization": "Lumen Network", "url": base},
        "supportedInterfaces": [
            {"url": f"{base}/a2a/lumen", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
        ],
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "extendedAgentCard": True,
        },
        "skills": [
            {"id": "lumen.progress-query", "name": "Learning Progress Query", ...},
            {"id": "lumen.ta-routing", "name": "Teaching Assistant Routing", ...},
            {"id": "lumen.peer-network", "name": "Lumen Network Peer Messaging", ...},
        ],
    }
```

This is the standard discovery entry point for the service as a whole.

### 2. Per-Agent Card

`/agents/{slug}/agent-card.json` returns a specific agent's card.

```python
# app/orchestrator/registry.py
@router.get("/{slug}/agent-card.json")
async def agent_card_endpoint(slug: str, request: Request):
    base = str(request.base_url).rstrip("/")
    card = get_agent_card(slug, base)
    if not card:
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' not found")
    return card
```

The card is loaded by importing the module and calling its `get_agent_card()`.

```python
# app/orchestrator/registry.py
def get_agent_card(slug: str, base_url: str = "") -> "AgentCard | None":
    module = get_agent_module(slug)
    if module and hasattr(module, "get_agent_card"):
        return module.get_agent_card(base_url)

    agent = get_external_by_slug(slug)
    if agent and agent.get("cached_card"):
        return AgentCard.model_validate(agent["cached_card"])
```

### 3. Public Directory

`/agents/directory` returns the system card URL, all internal/external agents, and all discoverable Lumens.

```python
# app/main.py
@app.get("/agents/directory")
async def public_agent_directory(request: Request):
    base = str(request.base_url).rstrip("/")

    for agent_id in AGENT_ROUTES:
        card = get_agent_card(agent_id, base)
        agents.append({
            "type": "agent",
            "id": agent_id,
            "name": card.name if card else agent_id,
            "card_url": f"{base}/agents/{agent_id}/agent-card.json",
            "subjects": [s.id for s in card.skills] if card else [],
        })

    for lumen in all_lumens:
        if not lumen.get("social", {}).get("discoverable", False):
            continue
        lumen_entries.append({
            "type": "lumen",
            "id": lumen["id"],
            "name": lumen.get("name", "Student"),
            "card_url": f"{base}/a2a/lumen/{lumen['id']}/agent-card.json",
            "subjects": list(progress.keys()),
        })
```

### 4. Lumen Peer Discovery

Authenticated users can discover peers through the Lumen social routes.

```python
# app/routes/lumen_social.py
@router.get("/discover")
async def discover_peers(current_user: dict = Depends(get_current_user)):
    all_lumens = await get_all_lumens_full()
    peers = []
    for lumen in all_lumens:
        if lumen["id"] == current_user["id"]:
            continue
        if not lumen.get("social", {}).get("discoverable", True):
            continue
        summary = _anonymize_peer(lumen)
        summary["card"] = build_lumen_card(lumen)
        peers.append(summary)

    return {"peers": peers, "count": len(peers), "protocol": "litp/1.0"}
```

This is the user-facing peer discovery flow. It filters out the current user, demo accounts, and non-discoverable peers.

## A2A JSON-RPC Flow

All internal A2A calls use JSON-RPC 2.0 and the `tasks/send` method.

### Client Side

`app/agents/a2a_client.py` builds and sends the request.

```python
async def a2a_tasks_send(
    agent_path: str,
    message: str,
    user_id: str,
    user_name: str = "Student",
    skill: str | None = None,
    base_url: str | None = None,
) -> dict:
    base = (base_url or settings.app_base_url or "http://localhost:8000").rstrip("/")
    endpoint = f"{base}{agent_path}"

    params = {
        "message": {"parts": [{"type": "text", "text": message}]},
        "metadata": {"user": {"id": user_id, "name": user_name}},
    }
    if skill:
        params["skill"] = skill
        params["message"]["skill"] = skill

    body = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4())[:8],
        "method": "tasks/send",
        "params": params,
    }

    r = await client.post(endpoint, json=body)
```

The client prefers the structured `application/json` artifact, because that preserves rich UI cards, redirects, actions, proposals, and other data.

```python
for art in artifacts:
    for part in art.get("parts", []):
        if part.get("type") == "application/json":
            data = part.get("data") or {}
            if isinstance(data, dict):
                return data
```

### Server Side

`app/protocols/a2a.py` exposes the JSON-RPC endpoint.

```python
@router.post("/a2a/{agent_id}")
async def a2a_jsonrpc(agent_id: str, body: dict):
    req_id = body.get("id")
    if body.get("jsonrpc") != "2.0":
        return _rpc_error(req_id, -32600, "jsonrpc must be '2.0'")

    method = body.get("method")
    params = body.get("params") or {}

    if agent_id not in AGENT_ROUTES and get_external_by_slug(agent_id) is None:
        return _rpc_error(req_id, -32601, f"Agent {agent_id} not found")

    if method == "tasks/send":
        result = await _handle_tasks_send(agent_id, params)
    elif method == "tasks/get":
        result = await _handle_tasks_get(params)
    elif method == "tasks/cancel":
        result = await _handle_tasks_cancel(params)
    else:
        return _rpc_error(req_id, -32601, f"Method not found: {method}")

    return _rpc_ok(req_id, result)
```

The handler creates a task, moves it to `working`, routes it to the correct agent, and completes it with artifacts.

```python
async def _handle_tasks_send(agent_id: str, params: dict) -> dict:
    message = params.get("message")
    if not message or "parts" not in message:
        raise JSONRPCError(-32602, "message.parts required")

    task = _new_task(agent_id, message, params.get("sessionId"))
    _transition(task, "working")

    if agent_id == "calendar":
        return await _handle_calendar(params, task)
    elif agent_id == "communication":
        return await _handle_communication_a2a(params, task)
    elif agent_id == "portfolio":
        return await _handle_portfolio_a2a(params, task)
    elif agent_id == "shiksha":
        return await _handle_shiksha_a2a(params, task)
    elif agent_id == "graph":
        return await _handle_graph_a2a(params, task)
    elif get_external_by_slug(agent_id) is not None:
        return await _handle_external_ta(agent_id, params, task)
```

The completed task contains both a human-readable text part and a structured JSON part.

```python
def _complete_with_result(task: dict, result: dict) -> dict:
    text = (result or {}).get("reply", "")
    return _transition(task, "completed", artifact={
        "name": "reply",
        "parts": [
            {"type": "text", "text": text},
            {"type": "application/json", "data": result or {}},
        ],
    })
```

That dual artifact shape is important: external A2A callers can read plain text, while Lumen's own UI can unpack structured cards and actions.

## External Agent Registration

External agents are not identified by local IDs. They are identified by their endpoint URL.

```python
# app/orchestrator/registry.py
class AgentRegistration(BaseModel):
    name: str
    endpoint: str
    description: str = ""
    card_url: str = ""
    keywords: list[str] = []
    capabilities: dict = {}
    icon: str = "agent"
```

When an external agent registers:

1. The canonical identity is `endpoint`.
2. The routing slug is the last path segment of the endpoint URL.
3. The registry may fetch the card from `card_url`.
4. The record is persisted to disk and Cosmos.

```python
def _slug_from_url(endpoint: str) -> str:
    path = urlparse(endpoint).path.rstrip("/")
    return path.split("/")[-1] if "/" in path else path
```

```python
@router.post("/register")
async def register_agent(reg: AgentRegistration, request: Request):
    slug = _slug_from_url(reg.endpoint)

    if reg.card_url:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(reg.card_url)
            if r.status_code == 200:
                cached_card = r.json()

    agent_record = {
        "id": reg.endpoint,
        "slug": slug,
        "name": reg.name,
        "endpoint": reg.endpoint,
        "card_url": reg.card_url,
        "cached_card": cached_card,
    }

    _external_agents[reg.endpoint] = agent_record
    _flush_registry()
    await _cosmos_upsert_agent(agent_record)

    return {"ok": True, "slug": slug, "url": reg.endpoint}
```

Example:

- Endpoint: `https://shiksha.example.com/a2a/blockchain`
- Canonical identity: `https://shiksha.example.com/a2a/blockchain`
- Derived slug: `blockchain`
- Local route used by Lumen: `/a2a/blockchain`
- Actual forwarding target: `https://shiksha.example.com/a2a/blockchain`

External forwarding is handled by `_handle_external_ta()`.

```python
async def _handle_external_ta(agent_id: str, params: dict, task: dict) -> dict:
    agent = get_external_by_slug(agent_id)
    endpoint = agent.get("endpoint", "")
    body = {"jsonrpc": "2.0", "id": task["id"], "method": "tasks/send", "params": params}
    r = await client.post(endpoint, json=body)
```

## How Agents Are Identified

### Internal agents

Internal agents are identified by local slug:

| Agent | Slug | A2A endpoint | Card endpoint |
| --- | --- | --- | --- |
| Calendar | `calendar` | `/a2a/calendar` | `/agents/calendar/agent-card.json` |
| Communication | `communication` | `/a2a/communication` | `/agents/communication/agent-card.json` |
| Portfolio | `portfolio` | `/a2a/portfolio` | `/agents/portfolio/agent-card.json` |
| Shiksha | `shiksha` | `/a2a/shiksha` | `/agents/shiksha/agent-card.json` |
| Microsoft Graph | `graph` | `/a2a/graph` | `/agents/graph/agent-card.json` |

### External agents

External agents are identified by endpoint URL:

| Field | Meaning |
| --- | --- |
| `endpoint` | Canonical A2A identity and actual forwarding URL |
| `slug` | Last segment of endpoint URL, used for local routing |
| `card_url` | Optional URL used to fetch and cache the remote agent card |
| `cached_card` | Remote card cached locally for discovery |

### Lumen peers

A Lumen peer has both a persistent identity and an addressable endpoint.

```python
# app/lumen/core.py
return {
    "id": user_id,
    "lumen_id": f"lumen://{kwargs.get('tenant_id', 'default')}/{user_id}",
    "name": name,
    "email": email,
    "social": {"discoverable": True, "share_progress": True},
}
```

The `id` is the app/user ID. The `lumen_id` is the protocol-level person-centric identity. The A2A URL is:

```text
{base_url}/a2a/lumen/{user_id}
```

## Lumen Peer A2A Cards

Each Lumen user exposes an A2A card at `/a2a/lumen/{user_id}/agent-card.json`.

```python
# app/protocols/lumen_a2a.py
@router.get("/a2a/lumen/{user_id}/agent-card.json")
async def lumen_a2a_card_v2(user_id: str, request: Request):
    lumen = await get_lumen(user_id)
    if not lumen:
        lumen = {"id": user_id, "name": "Student"}
    base_url = str(request.base_url).rstrip("/")
    return build_lumen_a2a_card(lumen, base_url)
```

The card advertises peer-specific skills.

```python
def build_lumen_a2a_card(lumen: dict, base_url: str = "") -> dict:
    user_id = lumen["id"]
    name = lumen.get("name", "Student")

    return {
        "name": f"{name}'s Lumen",
        "description": "Personal learning agent for ...",
        "provider": {"organization": "Lumen Network", "url": base_url},
        "supportedInterfaces": [
            {"url": f"{base_url}/a2a/lumen/{user_id}", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
        ],
        "skills": [
            {"id": "message", "name": "Send Message", ...},
            {"id": "schedule_meeting", "name": "Propose Meeting", ...},
            {"id": "info_request", "name": "Request Profile Info", ...},
            {"id": "remind", "name": "Send Reminder", ...},
        ],
    }
```

There is also an older LITP-style peer card in `app/routes/lumen_social.py`.

```python
def build_lumen_card(lumen: dict) -> dict:
    return {
        "id": lumen["id"],
        "lumen_id": lumen.get("lumen_id", f"lumen://default/{lumen['id']}"),
        "name": lumen.get("name", "Student"),
        "type": "lumen",
        "protocol": "litp/1.0",
        "endpoint": f"/lumen/connect/{lumen['id']}",
        "card_url": f"/lumen/cards/{lumen['id']}",
        "discoverable": lumen.get("social", {}).get("discoverable", True),
        "capabilities": {
            "subjects": subjects,
            "tcs_mastered": mastered_ids,
            "can_receive": ["message", "compare"],
        },
    }
```

Think of these as two views of the same peer:

- A2A card: standards-oriented JSON-RPC interface under `/a2a/lumen/{user_id}`.
- LITP card: Lumen-specific learning/social card under `/lumen/cards/{peer_id}`.

## Lumen-To-Lumen Message Flow

When a user says something like "message Priya: want to study?", the interaction manager finds the peer and sends an A2A message to that peer's Lumen endpoint.

```python
# app/agents/interaction_manager.py
result = await a2a_tasks_send(
    agent_path=f"/a2a/lumen/{peer['id']}",
    message=body_text,
    user_id=user_id,
    user_name=sender_name,
    skill="message",
)
```

The target peer's A2A endpoint receives `tasks/send`.

```python
# app/protocols/lumen_a2a.py
@router.post("/a2a/lumen/{user_id}")
async def lumen_a2a_jsonrpc(user_id: str, body: dict):
    method = body.get("method", "")
    params = body.get("params", {})

    if method != "tasks/send":
        return _jsonrpc_error(rpc_id, -32601, f"Method not found: {method}")

    skill_id = message.get("skill", params.get("skill", "message"))

    if skill_id == "message":
        result = await _handle_a2a_message(...)
    elif skill_id == "schedule_meeting":
        result = await _handle_a2a_schedule(...)
    elif skill_id == "info_request":
        result = await _handle_a2a_info_request(...)
    elif skill_id == "remind":
        result = await _handle_a2a_remind(...)
```

For messages, the target persists a peer message and may trigger an auto-reply.

```python
msg = {
    "id": str(uuid.uuid4())[:8],
    "kind": "chat",
    "from_id": sender_id,
    "from_name": sender_name,
    "to_id": user_id,
    "to_name": target_name,
    "message": text,
    "read": False,
    "protocol": "litp/1.0",
    "created_at": datetime.now(UTC).isoformat(),
}
await _persist_peer_message(msg)
```

## Where Discovery Data Lives

Lumen state and external agent registry records are stored in Cosmos when available, with disk/in-memory fallback.

```python
# app/db/cosmos.py
CONTAINERS = {
    "lumens": "/id",
    "chat_threads": "/user_id",
    "graph_tokens": "/id",
    "peer_messages": "/channel_id",
    "agents": "/id",
}
```

The `agents` container is keyed by endpoint URL.

```python
# app/orchestrator/registry.py
agent_record = {
    "id": reg.endpoint,
    "slug": slug,
    "endpoint": reg.endpoint,
    "card_url": reg.card_url,
}
```

The `lumens` container is keyed by user ID.

```python
# app/lumen/core.py
async def get_lumen(user_id: str) -> dict | None:
    if is_cosmos_ready():
        doc = await _cosmos_get(user_id)
        if doc:
            return doc
    return _lumens.get(user_id)
```

## Request And Response Example

A call to the calendar agent looks like this:

```json
POST /a2a/calendar
{
  "jsonrpc": "2.0",
  "id": "abc123",
  "method": "tasks/send",
  "params": {
    "message": {
      "skill": "calendar.get_events",
      "parts": [
        { "type": "text", "text": "What is on my calendar this week?" }
      ]
    },
    "metadata": {
      "user": {
        "id": "user-123",
        "name": "Anirudh"
      }
    }
  }
}
```

The response is a JSON-RPC result containing a completed task:

```json
{
  "jsonrpc": "2.0",
  "id": "abc123",
  "result": {
    "id": "task-id",
    "agentId": "calendar",
    "status": { "state": "completed" },
    "artifacts": [
      {
        "name": "reply",
        "parts": [
          { "type": "text", "text": "Here are your upcoming events..." },
          {
            "type": "application/json",
            "data": {
              "reply": "Here are your upcoming events...",
              "action": "calendar_query",
              "cards": []
            }
          }
        ]
      }
    ]
  }
}
```

## End-To-End Routing Summary

### Internal agent call

1. User asks for something.
2. `interaction_manager.py` classifies the intent.
3. It calls `a2a_tasks_send("/a2a/calendar", ...)` or another internal path.
4. `a2a_client.py` POSTs JSON-RPC to `{settings.app_base_url}/a2a/calendar`.
5. `app/protocols/a2a.py` validates JSON-RPC and routes by `agent_id`.
6. The agent handler runs.
7. The response returns as a completed task with text plus structured JSON.
8. The UI consumes the structured JSON for cards/actions.

### External agent call

1. External agent is registered through `/agents/register`.
2. Registry stores it by endpoint URL and derives a slug.
3. A local call to `/a2a/{slug}` reaches `app/protocols/a2a.py`.
4. `_handle_external_ta()` forwards JSON-RPC to the external endpoint.
5. The external response is converted back into Lumen's task/artifact shape.

### Peer Lumen call

1. Peer discovery returns discoverable Lumens and their cards.
2. The caller sends `tasks/send` to `/a2a/lumen/{peer_user_id}`.
3. `app/protocols/lumen_a2a.py` routes by skill: `message`, `schedule_meeting`, `info_request`, or `remind`.
4. The target Lumen persists the message/action against the target user.
5. The response is returned as an A2A task artifact.

## The Short Version To Say Out Loud

Lumen treats every capability as an addressable agent. Internal agents are known by local slugs like `calendar` and expose cards at `/agents/calendar/agent-card.json`, with JSON-RPC endpoints at `/a2a/calendar`. External agents are known by their endpoint URL; Lumen derives a slug only so they can be routed through local paths. Every signed-in user also gets a personal Lumen identity, `lumen://tenant/user`, and a peer A2A endpoint at `/a2a/lumen/{user_id}`. Discovery happens by reading cards from well-known, directory, per-agent, and peer-card endpoints. Execution happens through JSON-RPC `tasks/send`, and results come back as task artifacts with both plain text and structured JSON for the UI.
