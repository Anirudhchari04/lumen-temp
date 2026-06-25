"""Object-oriented Lumen — a class over the persistent Lumen document.

A `Lumen` is a person's agent: identity + persona + learning memory, plus the
team of specialist **sub-agents** and **skills** that act on their behalf.

This wraps the dict produced by `core._default_lumen` so a Lumen can be used as
a real object:

    me = await Lumen.get_or_create("u1", "Ana", "ana@x.com")
    me.add_skill("calculus", "comfortable with integrals", agent="wolfram")
    await me.save()
    me.agents            # the sub-agents available to this Lumen
    await me.dispatch("what's on my calendar?")   # human → this Lumen

Storage is unchanged (Cosmos / in-memory), so every existing dict-based call site
keeps working via `.to_dict()` / `Lumen.from_dict(doc)`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

from app.lumen import core
from app.agents.base import registry, BaseAgent

UTC = timezone.utc


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _ensure_agents_registered() -> None:
    """Import the agent handler package so every sub-agent registers itself.

    Idempotent (Python caches the import). Lets a Lumen list/use its sub-agents
    regardless of whether the dispatcher has been imported yet.
    """
    import app.agents.handlers  # noqa: F401  (import = registration side-effect)


@dataclass
class Skill:
    """A capability the Lumen has, optionally powered by a sub-agent."""
    name: str
    description: str = ""
    agent: str | None = None                      # links to a registered sub-agent
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    added_at: str = field(default_factory=_now)


class Lumen:
    """A person's Lumen — an addressable agent (`lumen://tenant/user`) composed of
    persona, learning memory, specialist sub-agents, and skills."""

    def __init__(self, doc: dict):
        self._doc = doc
        self._doc.setdefault("skills", [])

    def __repr__(self) -> str:
        return f"<Lumen {self.lumen_id!r} name={self.name!r} agents={len(self.agents)} skills={len(self.skills)}>"

    # ── construction / retrieval / persistence ───────────────────────────
    @classmethod
    def from_dict(cls, doc: dict) -> "Lumen":
        """Wrap an existing Lumen document."""
        return cls(doc)

    @classmethod
    def new(cls, user_id: str, name: str = "", email: str = "", **kwargs) -> "Lumen":
        """Build a brand-new (unsaved) Lumen."""
        return cls(core._default_lumen(user_id, name, email, **kwargs))

    @classmethod
    async def retrieve(cls, user_id: str) -> "Lumen | None":
        """Load an existing Lumen from the store (Cosmos → memory)."""
        doc = await core.get_lumen(user_id)
        return cls(doc) if doc else None

    @classmethod
    async def get_or_create(cls, user_id: str, name: str = "", email: str = "", **kwargs) -> "Lumen":
        """Load, or create-and-persist a new Lumen."""
        return cls(await core.get_or_create_lumen(user_id, name, email, **kwargs))

    @classmethod
    async def by_username(cls, username: str) -> "Lumen | None":
        """Resolve a Lumen by its public username (used for share links)."""
        doc = await core.get_lumen_by_username(username)
        return cls(doc) if doc else None

    async def save(self) -> "Lumen":
        """Persist this Lumen back to the store."""
        self._doc = await core.save_lumen(self._doc)
        return self

    def to_dict(self) -> dict:
        """The underlying document (for code that still expects a dict)."""
        return self._doc

    # ── identity / persona ───────────────────────────────────────────────
    @property
    def id(self) -> str: return self._doc["id"]

    @property
    def lumen_id(self) -> str: return self._doc.get("lumen_id", "")

    @property
    def username(self) -> str: return self._doc.get("username", "")

    @property
    def name(self) -> str: return self._doc.get("name", "")

    @property
    def email(self) -> str: return self._doc.get("email", "")

    @property
    def bio(self) -> str: return self._doc.get("bio", "")

    @property
    def expertise(self) -> str: return self._doc.get("expertise", "")

    @property
    def interests(self) -> str: return self._doc.get("interests", "")

    @property
    def preferences(self) -> dict: return self._doc.setdefault("preferences", {})

    @property
    def discoverable(self) -> bool:
        return self._doc.get("social", {}).get("discoverable", True)

    @property
    def share_url(self) -> str:
        """The public shareable link for this Lumen."""
        return core.build_share_url(self.username or self.id)

    # ── sub-agents (the specialist team under this Lumen) ────────────────
    @property
    def agents(self) -> list[str]:
        """Names of the specialist sub-agents this Lumen can act through."""
        _ensure_agents_registered()
        return registry.names()

    def agent(self, name: str) -> BaseAgent | None:
        """Get a specialist sub-agent instance by name (e.g. 'calendar')."""
        _ensure_agents_registered()
        spec = registry.get(name)
        return getattr(spec.handler, "__self__", None) if spec else None

    def capabilities(self) -> list[dict]:
        """What every sub-agent can do (name / description / intents)."""
        _ensure_agents_registered()
        return registry.capabilities()

    # ── skills ───────────────────────────────────────────────────────────
    @property
    def skills(self) -> list[dict]:
        return self._doc.setdefault("skills", [])

    def has_skill(self, name: str) -> bool:
        return any(s.get("name") == name for s in self.skills)

    def add_skill(self, name: str, description: str = "", agent: str | None = None) -> Skill:
        """Add a skill/capability to this Lumen.

        If `agent` names a registered sub-agent, the skill is linked to it.
        Changes are in-memory until `.save()` is called.
        """
        existing = next((s for s in self.skills if s.get("name") == name), None)
        if existing:
            existing.update({"description": description or existing.get("description", ""),
                             "agent": agent if agent is not None else existing.get("agent")})
            self._doc["updated_at"] = _now()
            return Skill(**{k: existing.get(k) for k in ("name", "description", "agent", "id", "added_at")})
        skill = Skill(name=name, description=description, agent=agent)
        self.skills.append(asdict(skill))
        self._doc["updated_at"] = _now()
        return skill

    def remove_skill(self, name: str) -> bool:
        before = len(self.skills)
        self._doc["skills"] = [s for s in self.skills if s.get("name") != name]
        if len(self._doc["skills"]) < before:
            self._doc["updated_at"] = _now()
            return True
        return False

    # ── learning memory ──────────────────────────────────────────────────
    @property
    def progress(self) -> dict:
        return self._doc.setdefault("curriculum_progress", {})

    @property
    def tc_inventory(self) -> dict:
        return self._doc.setdefault("tc_inventory", {"mastered": [], "in_progress": []})

    @property
    def session_history(self) -> list:
        return self._doc.setdefault("session_history", [])

    @property
    def artifacts(self) -> list:
        return self._doc.setdefault("artifacts", [])

    # ── interaction surfaces ─────────────────────────────────────────────
    async def dispatch(self, message: str, **ctx) -> dict:
        """HUMAN → this Lumen.

        Routes the owner's message through the Interaction Manager, which
        classifies intent and delegates to the right sub-agent.
        """
        from app.agents.interaction_manager import dispatch as _dispatch
        return await _dispatch(self.id, message, user_info=self.to_dict(), **ctx)

    def public_card(self) -> dict:
        """The public-facing card other Lumens read when interacting peer-to-peer."""
        from app.routes.lumen_social import build_lumen_card  # local import: avoid cycle
        return build_lumen_card(self._doc)
