"""Google Drive agent — list/read/search/create/summarize for Docs, Sheets, PDFs.

Uses the same google_config storage and token-refresh helper as gmail_agent.
"""

from __future__ import annotations

import io
import logging
import time

import httpx

logger = logging.getLogger(__name__)

DRIVE_API = "https://www.googleapis.com/drive/v3"
DOCS_API = "https://docs.googleapis.com/v1"
SHEETS_API = "https://sheets.googleapis.com/v4"

# Default MIME filter for `list_files` / `search_drive` — only "document-like" files
# (excludes videos, images, folders, audio). Callers can override via mime_types param.
DOC_MIME = "application/vnd.google-apps.document"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
PDF_MIME = "application/pdf"
DEFAULT_DOC_MIMES = [DOC_MIME, SHEET_MIME, PDF_MIME]


# ── HTTP helper ──────────────────────────────────────────────────────────────

async def _request(token: str, method: str, url: str, params: dict | None = None,
                    json: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.request(method, url, headers=headers, params=params, json=json)
        if resp.status_code >= 400:
            logger.warning(f"Drive {method} {url} -> {resp.status_code}: {resp.text[:300]}")
            return {"error": resp.text, "status": resp.status_code}
        return resp.json() if resp.text else {}


async def _download_bytes(token: str, url: str, params: dict | None = None) -> bytes | None:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url, headers=headers, params=params)
        if resp.status_code >= 400:
            logger.warning(f"Drive download failed: {resp.status_code}: {resp.text[:200]}")
            return None
        return resp.content


# ── List / search ────────────────────────────────────────────────────────────

def _file_summary(f: dict) -> dict:
    return {
        "id": f.get("id"),
        "name": f.get("name", "Untitled"),
        "mime_type": f.get("mimeType", ""),
        "modified_time": f.get("modifiedTime"),
        "icon": f.get("iconLink"),
        "url": f.get("webViewLink"),
        "size": f.get("size"),
    }


async def list_files_raw(token: str, query: str = "", limit: int = 10,
                          mime_types: list[str] | None = None) -> dict:
    """List Drive files and return {files, error, status}. Use this when the caller
    needs to distinguish "no files" from "API error" (e.g. Drive API not enabled).
    """
    if mime_types is None:
        mime_types = DEFAULT_DOC_MIMES
    parts = ["trashed = false"]
    if mime_types:
        mime_clause = " or ".join(f"mimeType='{m}'" for m in mime_types)
        parts.append(f"({mime_clause})")
    if query:
        safe = query.replace("'", "\\'")
        parts.append(f"(name contains '{safe}' or fullText contains '{safe}')")
    q = " and ".join(parts)
    res = await _request(token, "GET", f"{DRIVE_API}/files", params={
        "q": q,
        "pageSize": min(limit, 50),
        "orderBy": "modifiedTime desc",
        "fields": "files(id,name,mimeType,modifiedTime,iconLink,webViewLink,size)",
    })
    if "error" in res:
        return {"files": [], "error": res.get("error"), "status": res.get("status")}
    return {"files": [_file_summary(f) for f in res.get("files", [])]}


async def list_files(token: str, query: str = "", limit: int = 10,
                      mime_types: list[str] | None = None) -> list[dict]:
    """List Drive files. Empty query = recent; non-empty applies a fullText match.

    `mime_types` defaults to DEFAULT_DOC_MIMES (Docs + Sheets + PDFs) to keep the
    list focused on document-like files. Pass `mime_types=[]` to disable filtering.
    """
    raw = await list_files_raw(token, query, limit, mime_types)
    return raw.get("files", [])


async def search_drive(token: str, query: str, limit: int = 10,
                        mime_types: list[str] | None = None) -> list[dict]:
    return await list_files(token, query=query, limit=limit, mime_types=mime_types)


async def search_drive_raw(token: str, query: str, limit: int = 10,
                            mime_types: list[str] | None = None) -> dict:
    return await list_files_raw(token, query=query, limit=limit, mime_types=mime_types)


# ── Read ─────────────────────────────────────────────────────────────────────

async def _read_doc(token: str, file_id: str) -> str:
    """Read a Google Doc → plain text."""
    res = await _request(token, "GET", f"{DOCS_API}/documents/{file_id}")
    if "error" in res:
        return ""
    out: list[str] = []
    for el in (res.get("body", {}) or {}).get("content", []) or []:
        para = el.get("paragraph")
        if not para:
            continue
        line = "".join(
            (e.get("textRun", {}) or {}).get("content", "")
            for e in para.get("elements", []) or []
        )
        out.append(line.rstrip("\n"))
    return "\n".join(out).strip()


async def _read_sheet(token: str, file_id: str) -> str:
    """Read a Google Sheet → markdown-ish text."""
    # First, get the sheet metadata to find sheet titles
    meta = await _request(token, "GET", f"{SHEETS_API}/spreadsheets/{file_id}",
                          params={"fields": "sheets(properties(title))"})
    if "error" in meta:
        return ""
    sheets = meta.get("sheets", []) or []
    out: list[str] = []
    for s in sheets[:3]:  # cap to 3 sheets per file
        title = s.get("properties", {}).get("title", "Sheet1")
        vals = await _request(token, "GET",
                              f"{SHEETS_API}/spreadsheets/{file_id}/values/{title}!A1:Z500")
        if "error" in vals:
            continue
        rows = vals.get("values", []) or []
        if not rows:
            continue
        out.append(f"## {title}\n")
        header = rows[0]
        out.append("| " + " | ".join(str(c) for c in header) + " |")
        out.append("|" + "|".join(["---"] * len(header)) + "|")
        for r in rows[1:50]:
            cells = list(r) + [""] * (len(header) - len(r))
            out.append("| " + " | ".join(str(c) for c in cells) + " |")
        if len(rows) > 50:
            out.append(f"_(showing 50 of {len(rows)} rows)_")
        out.append("")
    return "\n".join(out).strip()


async def _read_pdf(token: str, file_id: str) -> str:
    """Download a PDF file and extract text with pdfminer."""
    data = await _download_bytes(token, f"{DRIVE_API}/files/{file_id}",
                                  params={"alt": "media"})
    if not data:
        return ""
    try:
        from pdfminer.high_level import extract_text
        text = extract_text(io.BytesIO(data))
        return (text or "").strip()
    except ImportError:
        return "[Cannot read PDF — pdfminer.six not installed]"
    except Exception as e:
        logger.warning(f"PDF extract failed: {e}")
        return ""


async def _export_as_text(token: str, file_id: str, mime: str) -> str:
    """For non-google-doc text files (markdown, plain text), download and decode."""
    data = await _download_bytes(token, f"{DRIVE_API}/files/{file_id}",
                                  params={"alt": "media"})
    if not data:
        return ""
    try:
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


async def read_file(token: str, file_id: str) -> dict:
    """Read a Drive file → {name, mime_type, content, url}. Auto-dispatches by MIME."""
    meta = await _request(token, "GET", f"{DRIVE_API}/files/{file_id}",
                          params={"fields": "id,name,mimeType,webViewLink"})
    if "error" in meta:
        return {"error": meta.get("error", "Could not fetch file")}
    mime = meta.get("mimeType", "")
    name = meta.get("name", "")
    content = ""
    if mime == "application/vnd.google-apps.document":
        content = await _read_doc(token, file_id)
    elif mime == "application/vnd.google-apps.spreadsheet":
        content = await _read_sheet(token, file_id)
    elif mime == "application/pdf":
        content = await _read_pdf(token, file_id)
    elif mime.startswith("text/") or mime in ("application/json", "application/xml"):
        content = await _export_as_text(token, file_id, mime)
    elif mime == "application/vnd.google-apps.presentation":
        # Export slides as plain text
        data = await _download_bytes(
            token, f"{DRIVE_API}/files/{file_id}/export",
            params={"mimeType": "text/plain"},
        )
        if data:
            content = data.decode("utf-8", errors="replace")
    else:
        content = f"[Unsupported MIME type for read: {mime}]"

    return {
        "id": file_id,
        "name": name,
        "mime_type": mime,
        "content": content,
        "url": meta.get("webViewLink"),
    }


# ── Create ───────────────────────────────────────────────────────────────────

async def create_doc(token: str, title: str, content_lines: list[str] | None = None) -> dict:
    """Create a new Google Doc with the given title, optionally pre-filled with lines."""
    res = await _request(token, "POST", f"{DOCS_API}/documents",
                          json={"title": title or "Untitled"})
    if "error" in res:
        return {"error": res.get("error", "Could not create doc")}
    doc_id = res.get("documentId")

    if content_lines:
        # Build a single batchUpdate that inserts all the text at the start of the body (index 1)
        text = "\n".join(line for line in content_lines if line.strip())
        if text:
            await _request(
                token, "POST", f"{DOCS_API}/documents/{doc_id}:batchUpdate",
                json={"requests": [
                    {"insertText": {"location": {"index": 1}, "text": text + "\n"}},
                ]},
            )
    return {
        "id": doc_id,
        "title": title,
        "url": f"https://docs.google.com/document/d/{doc_id}/edit",
    }


# ── Docs editing ─────────────────────────────────────────────────────────────

async def append_to_doc(token: str, file_id: str, content: str) -> dict:
    """Append text to the end of a Google Doc body."""
    if not content.strip():
        return {"error": "No content to append"}
    # Lead with a newline so the new text starts on its own line
    text = "\n" + content if not content.startswith("\n") else content
    res = await _request(
        token, "POST", f"{DOCS_API}/documents/{file_id}:batchUpdate",
        json={"requests": [
            {"insertText": {"endOfSegmentLocation": {}, "text": text}}
        ]},
    )
    if "error" in res:
        return {"error": res.get("error", "Append failed")}
    return {"ok": True, "appended_chars": len(text)}


async def replace_doc_content(token: str, file_id: str, content: str) -> dict:
    """Wipe the entire body of a Google Doc and insert new content.

    Destructive — formatting (tables, images, embeds, font choices) is lost.
    Callers should require explicit user confirmation.
    """
    # 1. Get the current doc to find the end index
    doc = await _request(token, "GET", f"{DOCS_API}/documents/{file_id}")
    if "error" in doc:
        return {"error": f"Could not read doc: {doc.get('error', 'unknown')}"}
    body_content = (doc.get("body", {}) or {}).get("content", []) or []
    if not body_content:
        # Empty body — just insert
        end_idx = 1
    else:
        # endIndex of last element minus 1 (the doc always has a trailing newline at endIndex-1)
        end_idx = body_content[-1].get("endIndex", 2) - 1
    if end_idx < 2:
        end_idx = 2  # Nothing meaningful to delete

    requests: list[dict] = []
    if end_idx > 1:
        requests.append({
            "deleteContentRange": {
                "range": {"startIndex": 1, "endIndex": end_idx}
            }
        })
    if content.strip():
        requests.append({
            "insertText": {"location": {"index": 1}, "text": content}
        })

    if not requests:
        return {"ok": True, "deleted_chars": 0, "inserted_chars": 0}

    res = await _request(
        token, "POST", f"{DOCS_API}/documents/{file_id}:batchUpdate",
        json={"requests": requests},
    )
    if "error" in res:
        return {"error": res.get("error", "Replace failed")}
    return {
        "ok": True,
        "deleted_chars": max(end_idx - 1, 0),
        "inserted_chars": len(content),
    }


async def find_replace_doc(token: str, file_id: str, find_text: str,
                            replace_text: str, match_case: bool = False) -> dict:
    """Find all instances of `find_text` and replace with `replace_text`."""
    if not find_text:
        return {"error": "Search text is required"}
    res = await _request(
        token, "POST", f"{DOCS_API}/documents/{file_id}:batchUpdate",
        json={"requests": [{
            "replaceAllText": {
                "containsText": {"text": find_text, "matchCase": match_case},
                "replaceText": replace_text,
            }
        }]},
    )
    if "error" in res:
        return {"error": res.get("error", "Find/replace failed")}
    replies = res.get("replies", []) or []
    occurrences = 0
    if replies:
        rt = replies[0].get("replaceAllText", {}) or {}
        occurrences = rt.get("occurrencesChanged", 0)
    return {"ok": True, "occurrences": occurrences}


# ── Summarize ────────────────────────────────────────────────────────────────

async def summarize_file(token: str, file_id: str, instruction: str = "", user_id: str = "") -> str:
    f = await read_file(token, file_id)
    if f.get("error"):
        return f"Could not read file: {f['error']}"
    content = f.get("content", "") or ""
    if not content.strip():
        return f"📄 **{f.get('name', 'Untitled')}** is empty or could not be parsed."

    from app.agents.calendar_agent import _get_client
    from app.agents.prompt_kit import build_agent_prompt
    sys = build_agent_prompt(
        role="Google Drive Reading Assistant",
        mission="Read a single Google Drive file (Doc, Sheet, PDF, …) and give the student a clear, accurate summary of its contents.",
        capabilities=[
            "Summarize documents, notes, and reports.",
            "Pull out the key takeaways, structure, and any conclusions.",
            "Follow a specific instruction about the file when the user gives one.",
        ],
        rules=[
            "If the user gave a specific instruction, follow it exactly; otherwise default to a 2-3 paragraph summary.",
            "Highlight the most important takeaways first.",
            "Stay faithful to the file — never invent content that isn't there.",
            "Be concise and well structured; skip preambles.",
        ],
        output_format="Plain text — 2-3 short paragraphs, or whatever shape the user's instruction asks for.",
    )
    user = (
        f"FILE TITLE: {f.get('name', '')}\n"
        f"FILE TYPE: {f.get('mime_type', '')}\n\n"
        f"CONTENT:\n{content[:10000]}\n\n"
        f"INSTRUCTION: {instruction or 'Summarize this file.'}"
    )
    client = _get_client()
    agent = client.as_agent(name="DriveSummarizer", instructions=sys)
    _t0 = time.perf_counter()
    result = await agent.run(user)
    _latency_ms = (time.perf_counter() - _t0) * 1000
    reply = str(result).strip()

    # Best-effort token accounting for sub-agent usage.
    if user_id:
        try:
            from app.lumen.token_tracker import record_usage, estimate_tokens
            p = estimate_tokens(sys + "\n" + user)
            c = estimate_tokens(reply)
            await record_usage(user_id, p, c, model="agent_framework (estimated)", source="drive", latency_ms=_latency_ms)
        except Exception:
            pass

    return reply
