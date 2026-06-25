"""Print the full modularity structure — run from repo root."""
import importlib, inspect
from app.agents.base import BaseAgent, registry
from app.agents import routing as r
from v2.agents.registry import SPECIALISTS

print("=== v1 AGENTS (app/agents/handlers/) ===")
for mod_name in sorted(["wolfram","arxiv","notion","drive","gmail","graph","lumen",
                         "portfolio","communication","calendar","shiksha","social"]):
    mod = importlib.import_module(f"app.agents.handlers.{mod_name}")
    classes = [(n,c) for n,c in inspect.getmembers(mod, inspect.isclass)
               if issubclass(c, BaseAgent) and c is not BaseAgent]
    for cls_name, cls in classes:
        kw_attrs = [a for a in dir(cls) if "KEYWORDS" in a or a.endswith("_KW")]
        has_broker = hasattr(cls, "broker") and cls.broker is not BaseAgent.broker
        print(f"  {cls_name:<22} name={cls.name!r:<14} "
              f"intents={list(cls.intents)}  "
              f"kw_attrs={kw_attrs or ['--']}  broker={has_broker}")

print()
print("=== v2 SPECIALISTS (v2/agents/registry.py) ===")
for name, _ in SPECIALISTS:
    print(f"  {name}")

print()
print("=== ROUTING KEYWORD GROUPS (app/agents/routing.py) ===")
for k in sorted(v for v in dir(r) if v.endswith("_KW")):
    val = getattr(r, k)
    print(f"  {k:<22} {len(val)} terms")

print()
print("=== REGISTRY ===")
print("  agents:", sorted(registry.names()))
print("  intent_map:", {k: v for k, v in sorted(registry.intent_map().items())})
