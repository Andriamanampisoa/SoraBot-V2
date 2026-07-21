##
## SORABOT, 2026
## discord_event_tools.py
## File description:
## Helpers for Discord scheduled events: validation, time parsing, and web research.
##

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = ZoneInfo("Europe/Paris")

ENTITY_TYPE_EXTERNAL = "external"
ENTITY_TYPE_VOICE = "voice"
ENTITY_TYPE_STAGE = "stage"

VALID_ENTITY_TYPES = {ENTITY_TYPE_EXTERNAL, ENTITY_TYPE_VOICE, ENTITY_TYPE_STAGE}

def parse_iso_datetime(value: Optional[str], *, assume_tz: ZoneInfo = DEFAULT_TIMEZONE) -> Optional[datetime]:
    """
    Parse an ISO-8601 datetime string into an aware datetime.
    """
    if not value or not str(value).strip():
        return None

    raw = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=assume_tz)
    return parsed


def normalize_event_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """
    Normalize a free-form LLM event payload into a stable schema.
    """
    if not isinstance(payload, dict):
        return {}

    entity_type = str(payload.get("entity_type") or ENTITY_TYPE_EXTERNAL).strip().lower()
    if entity_type not in VALID_ENTITY_TYPES:
        entity_type = ENTITY_TYPE_EXTERNAL

    name = (payload.get("name") or "").strip()
    description = (payload.get("description") or "").strip() or None
    location = (payload.get("location") or "").strip() or None
    channel_id = payload.get("channel_id")
    try:
        channel_id = int(channel_id) if channel_id not in (None, "") else None
    except (TypeError, ValueError):
        channel_id = None

    return {
        "name": name,
        "description": description,
        "start_time": (payload.get("start_time") or "").strip() or None,
        "end_time": (payload.get("end_time") or "").strip() or None,
        "entity_type": entity_type,
        "location": location,
        "channel_id": channel_id,
        "confirmed": bool(payload.get("confirmed")),
        "needs_research": bool(payload.get("needs_research")),
        "research_query": (payload.get("research_query") or name or "").strip() or None,
    }


def missing_event_fields(payload: dict[str, Any]) -> list[str]:
    """
    Return the list of required fields still missing before Discord creation.
    """
    missing: list[str] = []
    if not payload.get("name"):
        missing.append("name")
    if not payload.get("start_time") or parse_iso_datetime(payload.get("start_time")) is None:
        missing.append("start_time")

    entity_type = payload.get("entity_type") or ENTITY_TYPE_EXTERNAL
    if entity_type == ENTITY_TYPE_EXTERNAL:
        if not payload.get("location"):
            missing.append("location")
        if not payload.get("end_time") or parse_iso_datetime(payload.get("end_time")) is None:
            missing.append("end_time")
    elif entity_type in (ENTITY_TYPE_VOICE, ENTITY_TYPE_STAGE):
        if not payload.get("channel_id"):
            missing.append("channel_id")

    start = parse_iso_datetime(payload.get("start_time"))
    end = parse_iso_datetime(payload.get("end_time"))
    if start and end and end <= start:
        missing.append("end_time_after_start")

    return missing


def is_event_ready(payload: dict[str, Any]) -> bool:
    """
    True when the payload has everything needed to create a Discord scheduled event.
    """
    return not missing_event_fields(payload)


def describe_missing_fields(missing: list[str]) -> str:
    """
    Human-readable hints for missing event fields.
    """
    labels = {
        "name": "event title",
        "start_time": "start date and time",
        "end_time": "end date and time",
        "location": "location (external event)",
        "channel_id": "Discord voice or stage channel",
        "end_time_after_start": "an end time after the start time",
    }
    return ", ".join(labels.get(item, item) for item in missing)


def format_event_summary(payload: dict[str, Any]) -> str:
    """
    Compact summary of an event draft.
    """
    lines = [
        f"- Title: {payload.get('name') or '(missing)'}",
        f"- Start: {payload.get('start_time') or '(missing)'}",
        f"- End: {payload.get('end_time') or '(not set)'}",
        f"- Type: {payload.get('entity_type') or ENTITY_TYPE_EXTERNAL}",
    ]
    if payload.get("location"):
        lines.append(f"- Location: {payload['location']}")
    if payload.get("channel_id"):
        lines.append(f"- Channel: {payload['channel_id']}")
    if payload.get("description"):
        lines.append(f"- Description: {payload['description']}")
    return "\n".join(lines)


def search_web(query: str, *, max_results: int = 5, timeout: int = 12) -> str:
    """
    Lightweight web research helper for real-world events.

    Tries Wikipedia, then DuckDuckGo Instant Answer, then DuckDuckGo HTML.
    """
    query = (query or "").strip()
    if not query:
        return "No search query provided."

    errors: list[str] = []
    wiki = _search_wikipedia(query, max_results=max_results, timeout=timeout)
    if wiki.get("lines"):
        return "\n".join([f"Results for: {query}", *wiki["lines"]])
    if wiki.get("error"):
        errors.append(f"wikipedia: {wiki['error']}")

    instant = _search_duckduckgo_instant(query, timeout=timeout)
    if instant.get("lines"):
        return "\n".join([f"Results for: {query}", *instant["lines"]])
    if instant.get("error"):
        errors.append(f"duckduckgo-instant: {instant['error']}")

    html_results = _search_duckduckgo_html(query, max_results=max_results, timeout=timeout)
    if html_results.get("results"):
        lines = [f"Results for: {query}"]
        for index, item in enumerate(html_results["results"], start=1):
            lines.append(f"{index}. {item['title']}")
            if item.get("snippet"):
                lines.append(f"   {item['snippet']}")
            if item.get("url"):
                lines.append(f"   URL: {item['url']}")
        return "\n".join(lines)
    if html_results.get("error"):
        errors.append(f"duckduckgo-html: {html_results['error']}")

    if errors:
        return "Web search unavailable: " + " | ".join(errors)
    return "No usable search results."


def _search_wikipedia(query: str, *, max_results: int = 5, timeout: int = 12) -> dict[str, Any]:
    """
    Search Wikipedia (FR then EN) and return short extracts.
    """
    lines: list[str] = []
    last_error: Exception | None = None

    for lang in ("fr", "en"):
        try:
            search_url = f"https://{lang}.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
                {
                    "action": "query",
                    "list": "search",
                    "srsearch": query,
                    "srlimit": max_results,
                    "format": "json",
                    "utf8": 1,
                }
            )
            search_body = _http_get_text(search_url, timeout=timeout)
            search_data = json.loads(search_body)
            hits = ((search_data.get("query") or {}).get("search") or [])[:max_results]
            if not hits:
                continue

            titles = [hit.get("title") for hit in hits if hit.get("title")]
            summary_url = f"https://{lang}.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
                {
                    "action": "query",
                    "prop": "extracts|info",
                    "exintro": 1,
                    "explaintext": 1,
                    "inprop": "url",
                    "titles": "|".join(titles),
                    "format": "json",
                    "utf8": 1,
                }
            )
            summary_body = _http_get_text(summary_url, timeout=timeout)
            summary_data = json.loads(summary_body)
            pages = ((summary_data.get("query") or {}).get("pages") or {}).values()
            for index, page in enumerate(pages, start=1):
                title = (page.get("title") or "").strip()
                extract = re.sub(r"\s+", " ", (page.get("extract") or "").strip())
                if len(extract) > 320:
                    extract = extract[:317] + "..."
                url = (page.get("fullurl") or "").strip()
                if not title:
                    continue
                lines.append(f"{index}. {title}")
                if extract:
                    lines.append(f"   {extract}")
                if url:
                    lines.append(f"   URL: {url}")
            if lines:
                return {"lines": lines, "error": None}
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            continue

    return {"lines": [], "error": last_error}


def _http_get_text(url: str, *, timeout: int = 12) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; SoraBot/1.1; +https://github.com/) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _search_duckduckgo_instant(query: str, *, timeout: int = 12) -> dict[str, Any]:
    """
    Query DuckDuckGo Instant Answer API.
    """
    url = "https://api.duckduckgo.com/?" + urllib.parse.urlencode(
        {
            "q": query,
            "format": "json",
            "no_html": 1,
            "skip_disambig": 1,
        }
    )
    try:
        body = _http_get_text(url, timeout=timeout)
        data = json.loads(body)
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return {"lines": [], "error": exc}

    lines: list[str] = []
    heading = (data.get("Heading") or "").strip()
    abstract = (data.get("AbstractText") or "").strip()
    abstract_url = (data.get("AbstractURL") or "").strip()
    answer = (data.get("Answer") or "").strip()

    if heading:
        lines.append(f"1. {heading}")
        if abstract:
            lines.append(f"   {abstract}")
        if abstract_url:
            lines.append(f"   URL: {abstract_url}")
    elif abstract:
        lines.append(f"1. {abstract}")
        if abstract_url:
            lines.append(f"   URL: {abstract_url}")

    if answer:
        lines.append(f"Direct answer: {answer}")

    related = data.get("RelatedTopics") or []
    count = 2 if lines else 1
    for item in related:
        if count > 5:
            break
        if not isinstance(item, dict):
            continue
        text = (item.get("Text") or "").strip()
        first_url = ""
        if isinstance(item.get("FirstURL"), str):
            first_url = item["FirstURL"]
        if not text and isinstance(item.get("Topics"), list):
            continue
        if text:
            lines.append(f"{count}. {text}")
            if first_url:
                lines.append(f"   URL: {first_url}")
            count += 1

    return {"lines": lines, "error": None}


def _search_duckduckgo_html(query: str, *, max_results: int, timeout: int) -> dict[str, Any]:
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    try:
        body = _http_get_text(url, timeout=timeout)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {"results": [], "error": exc}

    return {"results": _parse_duckduckgo_results(body, max_results=max_results), "error": None}


def _parse_duckduckgo_results(body: str, *, max_results: int) -> list[dict[str, str]]:
    """
    Parse title/snippet/url blocks from DuckDuckGo HTML results.
    """
    results: list[dict[str, str]] = []
    pattern = re.compile(
        r'class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>'
        r'.*?(?:class="result__snippet"[^>]*>(?P<snippet>.*?)</(?:a|td|div)>)?',
        flags=re.IGNORECASE | re.DOTALL,
    )

    for match in pattern.finditer(body):
        title = _clean_html_text(match.group("title"))
        snippet = _clean_html_text(match.group("snippet") or "")
        href = html.unescape(match.group("href") or "")
        url = _unwrap_duckduckgo_url(href)
        if not title:
            continue
        results.append({"title": title, "snippet": snippet, "url": url})
        if len(results) >= max_results:
            break
    return results


def _unwrap_duckduckgo_url(href: str) -> str:
    """
    Extract the real destination URL from a DuckDuckGo redirect link.
    """
    if not href:
        return ""
    parsed = urllib.parse.urlparse(html.unescape(href))
    if "duckduckgo.com" in (parsed.netloc or "") and parsed.path.startswith("/l/"):
        query = urllib.parse.parse_qs(parsed.query)
        target = query.get("uddg") or query.get("u")
        if target:
            return urllib.parse.unquote(target[0])
    return href


def _clean_html_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def infer_default_end_time(start: datetime, *, hours: int = 8) -> datetime:
    """
    Provide a reasonable default end time when only the start is known.
    """
    return start + timedelta(hours=hours)
