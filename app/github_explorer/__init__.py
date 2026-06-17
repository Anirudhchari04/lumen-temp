"""Standalone GitHub Repo Explorer agent — ported from the demo app.

Served as a separate page outside the Lumen SPA at ``/github-explorer`` and
backed by the router in :mod:`app.routes.github_explorer`. The agent reuses
Lumen's Entra-authenticated Azure OpenAI client instead of the demo's
Azure AI Foundry project client, so it works inside the Lumen deployment.
"""
