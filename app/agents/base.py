"""Modular agent registry — a single declarative source of truth for agents.

Each specialist agent is described by an :class:`AgentSpec`: its topic name, the
async handler that answers requests, the intents that route to it, and any
back-compat aliases. Registering an agent is therefore a *one-line, co-located*
change instead of editing the broker wiring, the intent→agent map, and the alias
table in three different places.

The registry is transport-agnostic: it knows *what* the agents are and *how*
they are addressed, and can wire them onto any broker that exposes
``subscribe(topic, handler)`` and ``alias(topic, target)``.

Usage::

    from app.agents.base import registry

    @registry.agent("github", intents=[Intent.PORTFOLIO], aliases=["portfolio"])
    async def _broker_github(env: dict) -> dict:
        ...

    registry.wire(broker)                 # subscribe every agent + aliases
    INTENT_TO_AGENT = registry.intent_map()  # derived, never hand-maintained
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

logger = logging.getLogger("lumen.agents")

# An agent handler takes a request envelope (dict) and returns a response dict.
BrokerHandler = Callable[[dict], Awaitable[dict]]


@dataclass(frozen=True)
class AgentSpec:
    """Declarative description of a single specialist agent."""

    name: str
    handler: BrokerHandler
    intents: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    description: str = ""


class AgentRegistry:
    """Holds every :class:`AgentSpec` and derives routing tables from them."""

    def __init__(self) -> None:
        self._specs: dict[str, AgentSpec] = {}

    # ── Registration ─────────────────────────────────────────────────────
    def register(self, spec: AgentSpec) -> AgentSpec:
        if spec.name in self._specs:
            logger.warning("agent '%s' re-registered; overwriting", spec.name)
        self._specs[spec.name] = spec
        return spec

    def agent(
        self,
        name: str,
        *,
        intents: list[str] | tuple[str, ...] = (),
        aliases: list[str] | tuple[str, ...] = (),
        description: str = "",
    ) -> Callable[[BrokerHandler], BrokerHandler]:
        """Decorator that registers the decorated handler as an agent."""

        def _decorator(handler: BrokerHandler) -> BrokerHandler:
            self.register(AgentSpec(
                name=name,
                handler=handler,
                intents=tuple(intents),
                aliases=tuple(aliases),
                description=description,
            ))
            return handler

        return _decorator

    def register_agent(self, agent: "BaseAgent") -> "BaseAgent":
        """Register a class-based agent instance.

        Builds the :class:`AgentSpec` from the agent's declarative class
        attributes (``name``/``intents``/``aliases``/``description``) and uses
        the instance's ``broker`` coroutine as the handler.
        """
        self.register(agent.spec())
        return agent

    # ── Derived views ────────────────────────────────────────────────────
    @property
    def specs(self) -> list[AgentSpec]:
        return list(self._specs.values())

    def names(self) -> list[str]:
        return list(self._specs.keys())

    def get(self, name: str) -> AgentSpec | None:
        return self._specs.get(name)

    def intent_map(self) -> dict[str, str]:
        """Build the intent → agent-name routing table from the specs."""
        mapping: dict[str, str] = {}
        for spec in self._specs.values():
            for intent in spec.intents:
                if intent in mapping and mapping[intent] != spec.name:
                    logger.warning(
                        "intent '%s' claimed by both '%s' and '%s'",
                        intent, mapping[intent], spec.name,
                    )
                mapping[intent] = spec.name
        return mapping

    def capabilities(self) -> list[dict]:
        """Introspection — what agents exist and what they handle."""
        return [
            {
                "name": s.name,
                "description": s.description,
                "intents": list(s.intents),
                "aliases": list(s.aliases),
            }
            for s in self._specs.values()
        ]

    # ── Transport wiring ─────────────────────────────────────────────────
    def wire(self, broker) -> None:
        """Subscribe every agent (and its aliases) onto the given broker."""
        for spec in self._specs.values():
            broker.subscribe(spec.name, spec.handler)
            for alias in spec.aliases:
                broker.alias(alias, spec.name)


class BaseAgent:
    """Base class for a Lumen specialist agent.

    A concrete agent is a *class* (one per domain) that declares its identity via
    class attributes and implements its behaviour as ``self`` methods:

        class WolframAgent(BaseAgent):
            name = "wolfram"
            intents = (Intent.WOLFRAM,)
            description = "Wolfram Alpha computational queries"

            async def handle(self, user_id: str, message: str) -> dict:
                ...

            async def broker(self, env: dict) -> dict:
                return await self.handle(env["user_id"], env["message"])

    Instantiate it once and call ``registry.register_agent(WolframAgent())`` to
    wire it onto the broker. ``broker(env)`` is the registry entry point; the
    richer ``handle(...)`` method holds the actual logic and is what other modules
    call directly (via a thin back-compat alias).
    """

    name: str = ""
    intents: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    description: str = ""

    def spec(self) -> AgentSpec:
        """Build the registry :class:`AgentSpec` from this agent's attributes."""
        if not self.name:
            raise ValueError(f"{type(self).__name__} must set a non-empty `name`")
        return AgentSpec(
            name=self.name,
            handler=self.broker,
            intents=tuple(self.intents),
            aliases=tuple(self.aliases),
            description=self.description,
        )

    async def broker(self, env: dict) -> dict:
        """Registry entry point — adapt the broker envelope to ``handle``.

        Subclasses override this to unpack ``env`` and call their own method(s).
        """
        raise NotImplementedError


# Module-level singleton used across the app.
registry = AgentRegistry()
