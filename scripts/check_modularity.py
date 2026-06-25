"""Functional modularity check — proves agents are real self-using classes that work."""
import asyncio, inspect
from app.agents.base import BaseAgent, registry
from app.agents.routing import matches, WOLFRAM_KW

import importlib
MODS = ["arxiv","calendar","communication","drive","gmail","graph","lumen",
        "notion","portfolio","shiksha","social","wolfram"]

print("=== class + self-method check ===")
for m in MODS:
    mod = importlib.import_module(f"app.agents.handlers.{m}")
    cls = next(c for _, c in inspect.getmembers(mod, inspect.isclass)
               if issubclass(c, BaseAgent) and c is not BaseAgent)
    inst = getattr(mod, "agent", None)
    # methods that take self
    self_methods = [n for n, f in inspect.getmembers(cls, inspect.isfunction)
                    if list(inspect.signature(f).parameters)[:1] == ["self"]]
    print(f"  {cls.__name__:<20} instance={type(inst).__name__:<16} self-methods={self_methods}")

print("\n=== actually CALL a self method (WolframAgent.handle, empty msg) ===")
from app.agents.handlers.wolfram import agent as wolfram_agent
res = asyncio.run(wolfram_agent.handle("u1", ""))
print("  returns dict:", isinstance(res, dict), "| intent:", res.get("intent"), "| agent_id:", res.get("agent_id"))

print("\n=== broker is a bound self-method ===")
print("  wolfram.broker bound to instance:", wolfram_agent.broker.__self__ is wolfram_agent)

print("\n=== registry wired from instances ===")
print("  agents:", sorted(registry.names()))
spec = registry.get("wolfram")
print("  wolfram spec.handler is instance.broker:", spec.handler == wolfram_agent.broker)

print("\n=== keyword matching demo ===")
print("  matches('integrate sin x', WOLFRAM_KW):", matches("integrate sin x", WOLFRAM_KW))
