"""Lumen v2 smoke test — import soundness + endpoint wiring, no Azure creds needed.

Run from repo root:  python -m v2.smoke_test
Verifies the Definition of Done items that don't require live Azure credentials:
  - v2 package imports cleanly
  - GET  /v2/health -> 200
  - v1 routes (/, /health, /chat) still registered and v1 /chat still auth-gated
  - POST /v2/chat is wired + auth-gated (runs end-to-end; without an Azure endpoint
    it returns a graceful error dict instead of crashing)
"""

from __future__ import annotations

import sys


def main() -> int:
    ok = True

    # 1. v2 imports
    import v2.config, v2.model_client, v2.ledger, v2.runtime, v2.orchestrator, v2.router  # noqa
    from v2.agents import (  # noqa
        communication_agent, calendar_agent, github_agent, shiksha_agent,
        graph_agent, gmail_agent, drive_agent, notion_agent, arxiv_agent,
        wolfram_agent, general_agent,
    )
    print("[ok] v2 package + all 11 agent modules import")

    # 2. app imports with v2 mounted
    from fastapi.testclient import TestClient
    from app.main import app
    paths = {r.path for r in app.routes}
    assert "/v2/health" in paths, "/v2/health not mounted"
    assert "/v2/chat" in paths, "/v2/chat not mounted"
    assert "/chat" in paths, "v1 /chat missing"
    assert "/health" in paths, "v1 /health missing"
    print("[ok] app mounts both v1 (/chat,/health) and v2 (/v2/chat,/v2/health)")

    with TestClient(app) as client:
        # 3. v2 health
        r = client.get("/v2/health")
        assert r.status_code == 200, f"/v2/health -> {r.status_code}"
        body = r.json()
        assert body["status"] == "ok" and body["type"] == "lumen-v2-magentic-one"
        assert len(body["specialists"]) == 12
        print(f"[ok] GET /v2/health -> 200 ({len(body['specialists'])} specialists, "
              f"cosmos_ready={body['cosmos_ready']})")

        # 4. v1 health still 200
        r = client.get("/health")
        assert r.status_code == 200 and r.json()["type"] == "lumen-demo"
        print("[ok] GET /health (v1) -> 200, unchanged")

        # 5. both chat endpoints are auth-gated (no token -> 401/403)
        r1 = client.post("/chat", json={"message": "hi"})
        r2 = client.post("/v2/chat", json={"message": "hi"})
        assert r1.status_code in (401, 403), f"v1 /chat auth -> {r1.status_code}"
        assert r2.status_code in (401, 403), f"v2 /chat auth -> {r2.status_code}"
        print(f"[ok] POST /chat and /v2/chat both auth-gated ({r1.status_code}/{r2.status_code})")

        # 6. v2 /chat end-to-end under a valid v1 JWT (wiring; may error w/o Azure)
        from app.middleware.auth import sign_token
        token = sign_token({"id": "smoke-user", "name": "Smoke", "email": "smoke@example.com"})
        r = client.post("/v2/chat", json={"message": "summarize my inbox"},
                        headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, f"/v2/chat -> {r.status_code}: {r.text[:200]}"
        body = r.json()
        assert "reply" in body and "agent_id" in body
        print(f"[ok] POST /v2/chat (authed) -> 200, agent_id={body.get('agent_id')}, "
              f"action={body.get('action')}")
        print(f"     reply preview: {str(body.get('reply'))[:140]!r}")

    print("\nSMOKE TEST PASSED" if ok else "\nSMOKE TEST FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
