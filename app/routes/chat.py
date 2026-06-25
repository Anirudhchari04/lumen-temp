"""Chat routes — multi-thread with sidebar history for Lumen and each TA.
Uses the Interaction Manager for centralized dispatch."""

from __future__ import annotations

import base64
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.auth import get_current_user
from app.agents.interaction_manager import dispatch, dispatch_to_ta, confirm_study_plan
from app.lumen.core import get_or_create_lumen, update_progress
from app.db.cosmos import (
    append_message, create_thread, get_thread,
    get_all_threads, update_thread_title,
)
from app.orchestrator.registry import get_all_agents
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


class ChatBody(BaseModel):
    message: str
    thread_id: str | None = None
    graph_token: str | None = None


class ProgressPushBody(BaseModel):
    ta_id: str
    ta_name: str
    progress_report: dict


@router.get("/tas")
async def list_tas():
    return get_all_agents()


# ── Thread Management (shared by Lumen + TAs) ───────────────

@router.get("/threads/{channel}")
async def list_channel_threads(channel: str, current_user: dict = Depends(get_current_user)):
    """List all threads for a channel (lumen, math-ta, cs-ta)."""
    from app.db.cosmos import get_threads_by_channel
    threads = await get_threads_by_channel(current_user["id"], channel)
    return [
        {"id": t["id"], "title": t.get("title", "New Chat"),
         "message_count": t.get("message_count", 0),
         "updated_at": t.get("updated_at", "")}
        for t in threads
    ]


@router.post("/threads/{channel}")
async def new_channel_thread(channel: str, current_user: dict = Depends(get_current_user)):
    """Create a new thread in a channel."""
    thread = await create_thread(current_user["id"], title="New Chat", channel=channel)
    return {"id": thread["id"], "title": thread["title"]}


@router.get("/thread/{thread_id}")
async def get_thread_messages(thread_id: str, current_user: dict = Depends(get_current_user)):
    """Get messages for a specific thread."""
    thread = await get_thread(current_user["id"], thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"id": thread["id"], "title": thread.get("title", ""),
            "messages": thread.get("messages", [])}


# ── Lumen Chat (via Interaction Manager) ─────────────────────

@router.post("")
async def lumen_chat_endpoint(body: ChatBody, current_user: dict = Depends(get_current_user)):
    import traceback
    try:
        await get_or_create_lumen(current_user["id"], current_user.get("name", ""), current_user.get("email", ""))

        thread_id = body.thread_id
        if not thread_id:
            thread = await create_thread(current_user["id"], title=body.message[:50], channel="lumen")
            thread_id = thread["id"]

        # Load recent conversation history for context
        thread_data = await get_thread(current_user["id"], thread_id)
        conversation_history = []
        if thread_data and thread_data.get("messages"):
            for m in thread_data["messages"][-20:]:  # last 10 exchanges
                role = m.get("role", "user")
                if role == "assistant": role = "assistant"
                conversation_history.append({"role": role, "content": m.get("content", "")})

        result = await dispatch(
            user_id=current_user["id"],
            message=body.message,
            thread_id=thread_id,
            user_info=current_user,
            conversation_history=conversation_history or None,
            graph_token=body.graph_token,
        )

        await append_message(current_user["id"], thread_id, "user", body.message)
        await append_message(current_user["id"], thread_id, "assistant", result["reply"])

        result["thread_id"] = thread_id
        return result
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Chat error: {e}\n{traceback.format_exc()}")
        return {"reply": f"Error: {str(e)[:200]}", "action": "error", "thread_id": body.thread_id or ""}


# ── Confirm study-plan proposal ──────────────────────────────

class ConfirmPlanBody(BaseModel):
    proposal: list[dict]


@router.post("/confirm-plan")
async def confirm_plan(body: ConfirmPlanBody, current_user: dict = Depends(get_current_user)):
    """Accept a study-plan proposal — schedules every event in one batch."""
    result = await confirm_study_plan(current_user["id"], body.proposal or [])
    return {
        "ok": True,
        "count": result.get("count", 0),
        "events": result.get("scheduled", []),
    }


# ── Independent TA Chat (via Interaction Manager) ────────────

@router.post("/ta/{ta_id}")
async def ta_chat(ta_id: str, body: ChatBody, current_user: dict = Depends(get_current_user)):
    await get_or_create_lumen(
        current_user["id"], current_user.get("name", ""), current_user.get("email", ""))

    thread_id = body.thread_id
    if not thread_id:
        thread = await create_thread(current_user["id"], title=body.message[:50], channel=ta_id)
        thread_id = thread["id"]

    try:
        result = await dispatch_to_ta(
            user_id=current_user["id"],
            ta_id=ta_id,
            message=body.message,
            thread_id=thread_id,
            user_info=current_user,
        )

        await append_message(current_user["id"], thread_id, "user", body.message, ta_id=ta_id)
        await append_message(current_user["id"], thread_id, "assistant", result["reply"],
                             ta_id=ta_id, progress_update=result.get("progress"))

        result["thread_id"] = thread_id
        return result
    except Exception as e:
        import traceback
        logger.error(f"TA chat error ({ta_id}): {e}\n{traceback.format_exc()}")
        return {"reply": f"Error: {str(e)[:200]}", "action": "error", "thread_id": thread_id}


# ── Async Progress Push ──────────────────────────────────────

@router.post("/progress")
async def push_progress(body: ProgressPushBody, current_user: dict = Depends(get_current_user)):
    await update_progress(current_user["id"], body.ta_id, body.ta_name, body.progress_report)

    from app.events.bus import publish, PROGRESS_UPDATED
    await publish(PROGRESS_UPDATED, {
        "user_id": current_user["id"], "ta_id": body.ta_id,
        "ta_name": body.ta_name, "progress": body.progress_report,
    })

    return {"ok": True}


# ── File + Message endpoint ──────────────────────────────────

async def _analyze_image(message: str, file_b64: str, mime_type: str) -> str:
    """Call Azure OpenAI vision to analyze an image."""
    try:
        from azure.identity.aio import DefaultAzureCredential, ManagedIdentityCredential
        import httpx as _httpx

        cred = (
            ManagedIdentityCredential(client_id=settings.azure_managed_identity_client_id)
            if settings.azure_managed_identity_client_id
            else DefaultAzureCredential()
        )
        token = await cred.get_token("https://cognitiveservices.azure.com/.default")
        await cred.close()

        url = (
            f"{settings.azure_openai_endpoint}/openai/deployments/"
            f"{settings.azure_openai_deployment}/chat/completions"
            f"?api-version={settings.azure_openai_api_version}"
        )
        payload = {
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": message or "Describe this image in detail."},
                    {"type": "image_url", "image_url": {
                        "url": f"data:{mime_type};base64,{file_b64}"
                    }},
                ],
            }],
            "max_tokens": 800,
        }
        async with _httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {token.token}",
                         "Content-Type": "application/json"},
                json=payload,
            )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
            logger.warning(f"Vision API error {resp.status_code}: {resp.text[:200]}")
            return f"I couldn't analyze that image (status {resp.status_code})."
    except Exception as e:
        logger.error(f"Image analysis failed: {e}")
        return "I ran into an error analyzing that image — please try again."


def _extract_text(file_bytes: bytes, mime_type: str, filename: str) -> str:
    """Best-effort text extraction for summarization. PDFs via pdfminer, plain
    text/code files decoded directly. Returns '' if the type isn't extractable."""
    name = (filename or "").lower()
    if mime_type == "application/pdf" or name.endswith(".pdf"):
        try:
            import io
            from pdfminer.high_level import extract_text
            return (extract_text(io.BytesIO(file_bytes)) or "").strip()
        except Exception as e:
            logger.warning(f"PDF text extraction failed: {e}")
            return ""
    text_exts = (".txt", ".md", ".markdown", ".csv", ".json", ".log", ".rtf",
                 ".py", ".js", ".ts", ".java", ".c", ".cpp", ".html", ".css", ".xml", ".yaml", ".yml")
    if mime_type.startswith("text/") or name.endswith(text_exts):
        try:
            return file_bytes.decode("utf-8", "ignore").strip()
        except Exception:
            return ""
    return ""


async def _summarize_file_text(message: str, text: str, filename: str, user_id: str) -> str:
    """Summarize (or answer a question about) an uploaded document's text via the LLM."""
    from app.agents.calendar_agent import _get_client
    from app.agents.prompt_kit import build_agent_prompt
    sys = build_agent_prompt(
        role="Document Reading Assistant",
        mission="Read an uploaded document and either summarize it or answer the student's question about it.",
        capabilities=[
            "Summarize PDFs, notes, resumes, and text/code files.",
            "Answer a specific question or follow an instruction about the document.",
            "Pull out structure, key points, and any action items.",
        ],
        rules=[
            "If the user asked a specific question or gave an instruction, answer it directly using the document.",
            "Otherwise give a concise, well-structured summary of the key points.",
            "Stay faithful to the document — never invent content that isn't there.",
            "Skip preambles like 'Here is the summary'.",
        ],
        output_format="Plain text — a short summary or a direct answer, with bullet points where they help.",
    )
    user = (
        f"FILE: {filename}\n\n"
        f"CONTENT:\n{text[:12000]}\n\n"
        f"INSTRUCTION: {message or 'Summarize this document for me.'}"
    )
    try:
        agent = _get_client().as_agent(name="DocSummarizer", instructions=sys)
        result = await agent.run(user)
        out = str(result).strip()
        try:
            from app.lumen.token_tracker import record_usage, estimate_tokens
            await record_usage(user_id, estimate_tokens(sys + user), estimate_tokens(out),
                               model="agent_framework (estimated)", source="file-summary")
        except Exception:
            pass
        return out or "I couldn't produce a summary for that file."
    except Exception as e:
        logger.warning(f"file summary failed: {e}")
        return "I ran into an error summarizing that file — please try again."


async def _recent_portfolio_context(user_id: str, thread_id: str | None) -> bool:
    """True if the recent conversation in this thread was about the GitHub portfolio.
    Used to decide whether a file dropped with no instruction should also be offered
    for portfolio saving."""
    if not thread_id:
        return False
    try:
        thread = await get_thread(user_id, thread_id)
        for m in (thread or {}).get("messages", [])[-6:]:
            action = (m.get("action") or "")
            content = (m.get("content") or "").lower()
            if action.startswith("portfolio") or "portfolio" in content or "staged" in content \
                    or "github" in content or "commit" in content:
                return True
    except Exception:
        pass
    return False


@router.post("/file-message")
async def file_message(
    file: UploadFile = File(...),
    message: str = Form(""),
    ta_id: str | None = Form(None),
    thread_id: str | None = Form(None),
    current_user: dict = Depends(get_current_user),
):
    """Handle a message with an attached file.

    Intent-aware:
      - explicit "save/upload to portfolio" → stage the file (commit later).
      - image → Azure OpenAI vision analysis.
      - any other document → summarize / answer about its text content.
    Only when the conversation is already about the portfolio AND the file arrives
    with no instruction do we both summarize it and stage it (and say so).
    """
    file_bytes = await file.read()
    mime_type = file.content_type or "application/octet-stream"
    filename = file.filename or "upload"

    msg_lower = (message or "").lower().strip()
    is_image = mime_type.startswith("image/")
    file_alone = not msg_lower

    _portfolio_kw = [
        "upload to", "save to", "add to", "store in", "push to", "commit this",
        "github", "portfolio", "my repo", "the repo",
        "save this", "upload this", "keep this", "same place", "same folder",
    ]
    explicit_portfolio = any(kw in msg_lower for kw in _portfolio_kw)

    async def _stage_file():
        from app.agents.portfolio_agent import (
            stage_artifact, ensure_portfolio_repo, get_portfolio_status, get_staged,
        )
        status = await get_portfolio_status(current_user["id"])
        if not status.get("connected"):
            return {
                "reply": "📁 Connect your GitHub to add files to your portfolio. It opens GitHub, you click **Authorize**, and you're done — no token to paste.",
                "action": "portfolio_not_connected",
                "cards": [{"type": "connect_github", "data": {}}],
            }
        await ensure_portfolio_repo(current_user["id"])
        entry = stage_artifact(current_user["id"], filename, file_bytes, ta_hint=ta_id or message)
        staged_count = len(get_staged(current_user["id"]))
        return {
            "reply": (
                f"🟡 Staged **{filename}** under `{entry.get('folder', 'general')}/` — not committed yet "
                f"({staged_count} staged). Say **commit staged** to push to GitHub, **discard staged** to drop it, "
                f"or open the **Portfolio** page."
            ),
            "action": "portfolio_staged",
            "portfolio": entry,
        }

    # 1. Explicit "save/upload to portfolio" → stage only.
    if explicit_portfolio:
        return await _stage_file()

    # 2. Image → vision analysis (also handles "summarize/describe this image").
    if is_image:
        file_b64 = base64.b64encode(file_bytes).decode()
        reply = await _analyze_image(message, file_b64, mime_type)
        return {"reply": reply, "action": "image_analysis"}

    # 3. Any other document → summarize / answer about its text content.
    text = _extract_text(file_bytes, mime_type, filename)
    if text:
        summary = await _summarize_file_text(message, text, filename, current_user["id"])
        # Mid-portfolio-workflow + file dropped with no instruction → also offer to save it.
        if file_alone and await _recent_portfolio_context(current_user["id"], thread_id):
            staged = await _stage_file()
            if staged.get("action") == "portfolio_staged":
                summary += (
                    "\n\n📁 Since you're working with your portfolio, I also **staged** this file "
                    "(not committed). Say **commit staged** to save it, or **discard staged** to drop it."
                )
        return {"reply": f"📄 **{filename}**\n\n{summary}", "action": "file_summary"}

    # 4. Couldn't read it to summarize → offer to save instead.
    return {
        "reply": (
            f"I couldn't read **{filename}** to summarize it (unsupported type for text extraction). "
            f"I can still save it to your portfolio — say **save this to my portfolio** and re-attach the file."
        ),
        "action": "inline_answer",
    }