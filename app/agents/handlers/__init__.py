"""Lumen agent handler modules.

Each module owns one agent domain: its `_handle_*` function(s) and the
co-located `@registry.agent` broker registration. Importing this package imports
every handler module, which registers all agents on the shared registry. The
dispatcher (`interaction_manager`) imports this package and then calls
`registry.wire(broker)` once, after every spec is registered.

Adding a new agent = add a module here and list it below. No other file changes.
"""

from app.agents.handlers import (  # noqa: F401  (import = registration side effect)
    arxiv,
    calendar,
    communication,
    drive,
    gmail,
    graph,
    lumen,
    notion,
    portfolio,
    shiksha,
    social,
    wolfram,
)

__all__ = [
    "arxiv",
    "calendar",
    "communication",
    "drive",
    "gmail",
    "graph",
    "lumen",
    "notion",
    "portfolio",
    "shiksha",
    "social",
    "wolfram",
]
