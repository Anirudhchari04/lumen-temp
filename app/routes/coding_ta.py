"""Coding TA routes — generate learning artifacts that auto-save to the
GitHub portfolio (organized by type/date/title)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth import get_current_user
from app.agents.coding_ta import generate_and_save, ARTIFACT_TYPES, TA_FOLDER
from app.agents.portfolio_agent import list_artifacts

logger = logging.getLogger(__name__)
router = APIRouter(tags=["coding-ta"])


class GenerateBody(BaseModel):
    prompt: str
    artifact_type: str | None = None


@router.get("/types")
async def coding_types():
    """List the artifact types the Coding TA can produce."""
    return {"types": list(ARTIFACT_TYPES.keys())}


@router.post("/generate")
async def coding_generate(body: GenerateBody, current_user: dict = Depends(get_current_user)):
    """Generate an artifact and silently commit it to the portfolio."""
    if not (body.prompt or "").strip():
        return {"ok": False, "error": "Tell the Coding TA what to create."}
    return await generate_and_save(current_user["id"], body.prompt, body.artifact_type)


@router.get("/artifacts")
async def coding_artifacts(path: str = TA_FOLDER, current_user: dict = Depends(get_current_user)):
    """List artifacts the Coding TA has saved to the portfolio."""
    return await list_artifacts(current_user["id"], path)
