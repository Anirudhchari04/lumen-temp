"""arXiv agent — search + fetch + summarize academic papers.

No auth required. Uses HTTPS Atom feed at export.arxiv.org.

This version is namespace-agnostic and has BOTH httpx (async) and urllib
(sync, threaded) paths — httpx is tried first, urllib fallback runs if
httpx fails (some Azure App Service plans have intermittent httpx + TLS
issues with arxiv's CDN).
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import httpx

logger = logging.getLogger(__name__)

# Per-process cache for arXiv responses — { url: (timestamp, status, text) }
# Search responses cached for 5 minutes; get_paper effectively forever (1h)
_HTTP_CACHE: dict[str, tuple[float, int, str]] = {}
_SEARCH_TTL = 300.0
_PAPER_TTL = 3600.0

ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_PDF = "https://arxiv.org/pdf/{id}.pdf"
# Semantic Scholar — free public API used as a fallback when arXiv rate-limits
# our shared Azure IP. Returns similar paper metadata.
SS_API = "https://api.semanticscholar.org/graph/v1/paper/search"

ATOM_NS = "http://www.w3.org/2005/Atom"
UA = "Lumen-Bot/1.0 (mailto:lumen@example.com; +https://lumen-demo.azurewebsites.net)"


# ── HTTP fetch with retries + fallback ──────────────────────────────────────

def _is_rate_limit_body(text: str) -> bool:
    """arXiv sometimes returns HTTP 200 with body 'Rate exceeded.' — detect it."""
    if not text:
        return False
    head = text.strip()[:80].lower()
    return ("rate exceeded" in head or "too many requests" in head)


async def _http_get(url: str, timeout: float = 25.0, max_retries: int = 3,
                    cache_ttl: float = 0.0) -> tuple[int, str]:
    """httpx with retries (including body-level rate-limit detection) + urllib fallback.
    Optional in-process caching via cache_ttl > 0 seconds.
    """
    # Check cache
    if cache_ttl > 0:
        cached = _HTTP_CACHE.get(url)
        if cached and (time.time() - cached[0]) < cache_ttl:
            return cached[1], cached[2]

    last_status = 0
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": UA, "Accept": "application/atom+xml,application/xml,text/xml,application/json"})
            last_status = resp.status_code
            logger.info(f"[arxiv httpx attempt={attempt}] {resp.status_code} len={len(resp.text)}")
            if resp.status_code == 200 and resp.text and not _is_rate_limit_body(resp.text):
                if cache_ttl > 0:
                    _HTTP_CACHE[url] = (time.time(), resp.status_code, resp.text)
                return resp.status_code, resp.text
            if resp.status_code in (429, 500, 502, 503, 504) or _is_rate_limit_body(resp.text or ""):
                logger.info(f"[arxiv httpx] rate-limited (body or status); backing off")
                await asyncio.sleep(3.0 * (attempt + 1))
                continue
            break
        except Exception as e:
            logger.warning(f"[arxiv httpx attempt={attempt}] {type(e).__name__}: {e}")
            await asyncio.sleep(2.0 * (attempt + 1))
            continue

    # Sync urllib fallback in a thread
    def _sync_get():
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")

    try:
        status, text = await asyncio.to_thread(_sync_get)
        logger.info(f"[arxiv urllib] {status} len={len(text)}")
        return status, text
    except Exception as e:
        logger.error(f"[arxiv urllib] failed {type(e).__name__}: {e} (last httpx={last_status})")
        return last_status, ""


# ── Semantic Scholar fallback ────────────────────────────────────────────────

async def _semantic_scholar_search(query: str, max_results: int = 10) -> list[dict]:
    """Fallback when arXiv rate-limits us. Returns Lumen-shaped paper dicts."""
    params = {
        "query": query,
        "limit": max(1, min(max_results, 25)),
        "fields": "title,abstract,authors,year,externalIds,url,openAccessPdf",
    }
    url = f"{SS_API}?{urllib.parse.urlencode(params)}"
    try:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": UA, "Accept": "application/json"})
        logger.info(f"[ss search] {resp.status_code} len={len(resp.text)}")
        if resp.status_code != 200:
            return []
        data = resp.json() or {}
    except Exception as e:
        logger.warning(f"[ss search] failed {type(e).__name__}: {e}")
        return []

    out: list[dict] = []
    for p in data.get("data", []) or []:
        ext = p.get("externalIds") or {}
        arxiv_id = (ext.get("ArXiv") or "").strip()
        pdf_url = ""
        if isinstance(p.get("openAccessPdf"), dict):
            pdf_url = p["openAccessPdf"].get("url", "") or ""
        if not pdf_url and arxiv_id:
            pdf_url = ARXIV_PDF.format(id=arxiv_id)
        out.append({
            "id": arxiv_id or p.get("paperId", ""),
            "title": (p.get("title") or "").strip(),
            "authors": [a.get("name", "") for a in (p.get("authors") or []) if a.get("name")],
            "abstract": (p.get("abstract") or "").strip(),
            "published": str(p.get("year") or ""),
            "updated": "",
            "categories": [],
            "url": p.get("url") or (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""),
            "pdf_url": pdf_url,
            "source": "semantic_scholar",
        })
    return out


# ── Namespace-agnostic helpers ──────────────────────────────────────────────

def _findall_local(element: ET.Element, local_name: str) -> list[ET.Element]:
    """Return immediate-child elements matching by local name regardless of namespace."""
    return [e for e in element if e.tag.endswith("}" + local_name) or e.tag == local_name]


def _find_local(element: ET.Element, local_name: str) -> ET.Element | None:
    items = _findall_local(element, local_name)
    return items[0] if items else None


def _text_of(element: ET.Element | None) -> str:
    return ((element.text if element is not None else "") or "").strip()


# ── Parse entry ──────────────────────────────────────────────────────────────

def _parse_entry(entry: ET.Element) -> dict:
    arxiv_url = _text_of(_find_local(entry, "id"))
    arxiv_id = arxiv_url.rsplit("/", 1)[-1]
    bare_id = re.sub(r"v\d+$", "", arxiv_id)

    title = " ".join(_text_of(_find_local(entry, "title")).split())
    summary = " ".join(_text_of(_find_local(entry, "summary")).split())
    published = _text_of(_find_local(entry, "published"))
    updated = _text_of(_find_local(entry, "updated"))

    authors = []
    for author in _findall_local(entry, "author"):
        name_el = _find_local(author, "name")
        nm = _text_of(name_el)
        if nm:
            authors.append(nm)

    categories = []
    for cat in _findall_local(entry, "category"):
        term = cat.get("term", "")
        if term:
            categories.append(term)

    pdf_link = ""
    for link in _findall_local(entry, "link"):
        if link.get("type") == "application/pdf":
            pdf_link = link.get("href", "")
            break
    if not pdf_link and bare_id:
        pdf_link = ARXIV_PDF.format(id=bare_id)

    return {
        "id": bare_id,
        "title": title,
        "authors": authors,
        "abstract": summary,
        "published": published,
        "updated": updated,
        "categories": categories,
        "url": f"https://arxiv.org/abs/{bare_id}",
        "pdf_url": pdf_link,
    }


# ── Search ───────────────────────────────────────────────────────────────────

async def search_arxiv(query: str, max_results: int = 10,
                       sort_by: str = "relevance") -> list[dict]:
    """Query arXiv. `sort_by`: relevance | lastUpdatedDate | submittedDate."""
    if not query or not query.strip():
        return []
    params = {
        "search_query": f"all:{query.strip()}",
        "start": 0,
        "max_results": max(1, min(max_results, 25)),
        "sortBy": sort_by,
        "sortOrder": "descending",
    }
    url = f"{ARXIV_API}?{urllib.parse.urlencode(params)}"
    status, text = await _http_get(url, cache_ttl=_SEARCH_TTL)
    if status == 200 and text and not _is_rate_limit_body(text):
        try:
            root = ET.fromstring(text)
            entries = _findall_local(root, "entry")
            logger.info(f"[arxiv search] query={query!r} parsed_entries={len(entries)}")
            if entries:
                return [_parse_entry(e) for e in entries]
        except ET.ParseError as e:
            logger.warning(f"[arxiv search] XML parse error: {e}; head={text[:200]}")

    # arXiv failed or returned 0 — fall back to Semantic Scholar
    logger.info(f"[arxiv search] falling back to Semantic Scholar for query={query!r}")
    return await _semantic_scholar_search(query, max_results=max_results)


# ── Single paper ─────────────────────────────────────────────────────────────

async def get_paper(arxiv_id: str) -> dict | None:
    """Fetch metadata for a single arXiv ID (e.g. '2406.01234').

    Falls back to Semantic Scholar's /paper/arXiv:<id> endpoint if arXiv fails.
    """
    bare = re.sub(r"v\d+$", "", arxiv_id.strip())
    params = {"id_list": bare}
    url = f"{ARXIV_API}?{urllib.parse.urlencode(params)}"
    status, text = await _http_get(url, cache_ttl=_PAPER_TTL)
    if status == 200 and text and not _is_rate_limit_body(text):
        try:
            root = ET.fromstring(text)
            entries = _findall_local(root, "entry")
            if entries:
                return _parse_entry(entries[0])
        except ET.ParseError:
            pass

    # Fallback: Semantic Scholar /paper/arXiv:{id}
    ss_url = f"https://api.semanticscholar.org/graph/v1/paper/arXiv:{bare}?fields=title,abstract,authors,year,externalIds,url,openAccessPdf"
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(ss_url, headers={"User-Agent": UA, "Accept": "application/json"})
        if resp.status_code != 200:
            return None
        p = resp.json() or {}
    except Exception as e:
        logger.warning(f"[ss get_paper] {type(e).__name__}: {e}")
        return None
    if not p:
        return None
    pdf_url = ""
    if isinstance(p.get("openAccessPdf"), dict):
        pdf_url = p["openAccessPdf"].get("url", "") or ""
    if not pdf_url:
        pdf_url = ARXIV_PDF.format(id=bare)
    return {
        "id": bare,
        "title": (p.get("title") or "").strip(),
        "authors": [a.get("name", "") for a in (p.get("authors") or []) if a.get("name")],
        "abstract": (p.get("abstract") or "").strip(),
        "published": str(p.get("year") or ""),
        "updated": "",
        "categories": [],
        "url": p.get("url") or f"https://arxiv.org/abs/{bare}",
        "pdf_url": pdf_url,
        "source": "semantic_scholar",
    }


# ── Full PDF text ────────────────────────────────────────────────────────────

async def fetch_paper_text(arxiv_id: str, max_chars: int = 30000) -> str:
    bare = re.sub(r"v\d+$", "", arxiv_id.strip())
    pdf_url = ARXIV_PDF.format(id=bare)
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(pdf_url, headers={"User-Agent": "Lumen-Bot/1.0"})
        if resp.status_code != 200:
            return ""
        try:
            from pdfminer.high_level import extract_text
            text = extract_text(io.BytesIO(resp.content))
        except ImportError:
            return "[pdfminer.six not installed]"
        return (text or "").strip()[:max_chars]
    except Exception as e:
        logger.warning(f"[arxiv fetch_paper_text] {e}")
        return ""


# ── Summarize ────────────────────────────────────────────────────────────────

async def summarize_paper(arxiv_id: str, instruction: str = "",
                           use_full_pdf: bool = False, user_id: str = "") -> str:
    paper = await get_paper(arxiv_id)
    if not paper:
        return f"⚠ Couldn't fetch arXiv paper {arxiv_id}."

    content = paper.get("abstract", "")
    source_note = "(based on abstract)"
    if use_full_pdf:
        body = await fetch_paper_text(arxiv_id, max_chars=20000)
        if body:
            content = body
            source_note = "(based on full paper)"

    if not content:
        return f"📄 **{paper.get('title', 'Untitled')}** — no readable content."

    from app.agents.calendar_agent import _get_client
    from app.agents.prompt_kit import build_agent_prompt
    sys = build_agent_prompt(
        role="arXiv Research Assistant",
        mission="Read an arXiv research paper and explain it to a student in clear, accessible terms.",
        capabilities=[
            "Distil a paper into problem, approach, results, and significance.",
            "Translate dense academic language into student-friendly terms.",
            "Follow a specific instruction about the paper when the user gives one.",
        ],
        rules=[
            "If the user gave a specific instruction, follow it exactly; otherwise default to a 4-paragraph summary.",
            "Cover, in order: (1) the problem, (2) the approach, (3) the main results, (4) why it matters.",
            "Stay faithful to the paper — never overstate or invent results.",
            "Be concise and well structured; skip preambles.",
        ],
        output_format="Plain text — four short paragraphs (problem / approach / results / significance), or whatever the user's instruction asks for.",
    )
    user = (
        f"PAPER TITLE: {paper.get('title', '')}\n"
        f"AUTHORS: {', '.join(paper.get('authors', []))}\n"
        f"CATEGORIES: {', '.join(paper.get('categories', []))}\n"
        f"PUBLISHED: {paper.get('published', '')[:10]}\n\n"
        f"CONTENT {source_note}:\n{content}\n\n"
        f"INSTRUCTION: {instruction or 'Summarize this paper for a student.'}"
    )
    client = _get_client()
    agent = client.as_agent(name="ArxivSummarizer", instructions=sys)
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
            await record_usage(user_id, p, c, model="agent_framework (estimated)", source="arxiv", latency_ms=_latency_ms)
        except Exception:
            pass

    return reply
