"""Lumen v2 — a Magentic-One (autogen) orchestration layer over the existing
Lumen v1 specialist agents and tools.

This package is fully additive: it imports v1 (app.*) but v1 never imports v2.
Nothing here writes to v1 config, secrets, or Cosmos containers. All new state
lives in the `lumen_v2_sessions` Cosmos container owned by v2.ledger.
"""

__version__ = "2.0.0"
