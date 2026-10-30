from .config import load_environment
load_environment()

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
import os
import logging
import time
import asyncio
import ipaddress
import socket
import re
import json
import secrets
import unicodedata
from html.parser import HTMLParser
from urllib.parse import urljoin, parse_qs
import xml.etree.ElementTree as ET
from urllib.parse import urlparse, urlunparse
import httpx
from .public_https import PublicHTTPSClient
from .social_evidence import (evidence_for_sources, load_review_candidates, discover_party_review_candidates,
                              discover_personal_homepage_review_candidates)
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("ch_parlament")
# Scheduler is owned by the ASGI application lifecycle.
_scheduler_task = None


@asynccontextmanager
async def lifespan(application: FastAPI):
    global _scheduler_task
    from .social_check_scheduler import interval_hours, loop
    if interval_hours():
        _scheduler_task = asyncio.create_task(loop())
    try:
        yield
    finally:
        if _scheduler_task is not None:
            _scheduler_task.cancel()
            try:
                await _scheduler_task
            except asyncio.CancelledError:
                pass
            _scheduler_task = None


app = FastAPI(title="CH_Parlament", lifespan=lifespan)
ROOT = Path(__file__).resolve().parent
API = os.getenv("PARLIAMENT_API", "https://ws.parlament.ch/odata.svc")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
from .admin import router as admin_router
app.include_router(admin_router)


def pick(row, *keys):
    for key in keys:
        if row.get(key) is not None:
            return row[key]
    return ""


def social_media(row):
    platforms = {"X": ("TwitterUrl", "TwitterURL", "XUrl"), "Facebook": ("FacebookUrl", "FacebookURL"), "Instagram": ("InstagramUrl", "InstagramURL"), "LinkedIn": ("LinkedInUrl", "LinkedinUrl"), "YouTube": ("YouTubeUrl", "YoutubeUrl")}
    links = {}
    for name, keys in platforms.items():
        url = pick(row, *keys)
        if isinstance(url, str):
            url = url.strip()
            parsed = urlparse(url)
            if parsed.scheme == "https" and parsed.netloc and not parsed.username and not parsed.password:
                links[name] = url
    return links


def normalize(row):
    first = pick(row, "FirstName", "FirstNameDe")
    last = pick(row, "LastName", "LastNameDe")
    name = pick(row, "FullName", "PersonName") or f"{first} {last}".strip()
    return {
        "id": str(pick(row, "PersonNumber", "PersonId", "ID", "Id")),
        "name": name,
        "rat_code": str(pick(row, "Council", "CouncilAbbreviation")),
        "rat": str(pick(row, "CouncilName", "CouncilNameDe", "CouncilAbbreviation", "Council")),
        "partei": str(pick(row, "PartyAbbreviation", "PartyName", "PartyNameDe", "Party")),
        "kanton": str(pick(row, "CantonName", "CantonNameDe", "CantonAbbreviation", "Canton")),
        "fraktion": str(pick(row, "ParlGroupName", "ParlGroupNameDe", "ParlGroupAbbreviation", "ParlGroup")),
        "geschlecht": str(pick(row, "GenderAsString", "Gender", "Sex")),
        "socialmedia": social_media(row),
    }


def is_current(row):
    # Official MemberCouncil records expose membership status or mandate end.
    active = pick(row, "Active", "IsActive")
    if active != "":
        return active is True or str(active).lower() in ("true", "1")
    end = pick(row, "DateLeaving", "DateTo", "EndDate", "MembershipEndDate")
    if end:
        return False
    return True


async def fetch_members(language="DE"):
    rows, url, seen = [], f"{API.rstrip('/')}/MemberCouncil", set()
    logger.info("API fetch started endpoint=%s", url)
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
        while url:
            if url in seen or len(seen) >= 30:
                raise ValueError("API pagination loop or limit exceeded")
            seen.add(url)
            response = await client.get(url, params={"$format": "json", "$filter": f"Active eq true and Language eq '{language}'", "$top": 1000} if len(seen) == 1 else None)
            logger.info("API response page=%d status=%d bytes=%d", len(seen), response.status_code, len(response.content))
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict) and "d" in payload:
                data = payload["d"]
                page = data.get("results", []) if isinstance(data, dict) else data
                next_url = data.get("__next") if isinstance(data, dict) else None
            else:
                page = payload.get("value", []) if isinstance(payload, dict) else []
                next_url = payload.get("@odata.nextLink") if isinstance(payload, dict) else None
            if not isinstance(page, list) or any(not isinstance(item, dict) for item in page):
                raise ValueError("Unexpected MemberCouncil page structure")
            logger.info("API parsed page=%d rows=%d keys=%s", len(seen), len(page), list(page[0].keys()) if page else [])
            rows.extend(page)
            url = str(httpx.URL(url).join(next_url)) if next_url else None
    unique_rows = {}
    for row in rows:
        identity = (row.get("ID"), row.get("Language"))
        if identity[0] is None:
            raise ValueError("MemberCouncil row missing ID")
        unique_rows[identity] = row
    if len(unique_rows) != len(rows):
        logger.warning("MemberCouncil duplicate rows=%d", len(rows) - len(unique_rows))
    rows = list(unique_rows.values())
    logger.info("API fetch completed rows=%d duration_ms=%d", len(rows), round((time.monotonic()-started)*1000))
    return rows


async def fetch_communications(language, person_numbers=None):
    """Page through communications, retaining only records for current members."""
    url = f"{API.rstrip('/')}/PersonCommunication"
    rows, seen = [], set()
    page_size, max_pages = 1000, 20
    next_url = None
    visited_pages = set()
    diagnostic = {"http_status": None, "raw_count": 0, "languages": {}, "communication_types": {}, "error": None, "pages": 0, "truncated": False, "matched_members": 0}
    allowed = {str(n) for n in person_numbers} if person_numbers is not None else None
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            for page_number in range(max_pages):
                page_url = next_url or url
                page_key = (page_url, page_number if not next_url else -1)
                if page_key in visited_pages:
                    raise ValueError("PersonCommunication pagination loop")
                visited_pages.add(page_key)
                params = None if next_url else {"$format": "json", "$top": page_size, "$skip": page_number * page_size}
                response = await client.get(page_url, params=params)
                diagnostic["http_status"] = response.status_code
                response.raise_for_status()
                payload = response.json()
                data = payload.get("d", payload)
                page = data if isinstance(data, list) else data.get("results", data.get("value", [])) if isinstance(data, dict) else []
                if not isinstance(page, list):
                    raise ValueError("Unexpected PersonCommunication response structure")
                diagnostic["pages"] += 1
                diagnostic["raw_count"] += len(page)
                for row in page:
                    if not isinstance(row, dict):
                        raise ValueError("Unexpected PersonCommunication record")
                    identity = row.get("ID")
                    if identity is not None:
                        if identity in seen:
                            diagnostic["duplicates"] = diagnostic.get("duplicates", 0) + 1
                            continue
                        seen.add(identity)
                    lang = str(row.get("Language", ""))
                    kind = str(row.get("CommunicationTypeText") or row.get("CommunicationType") or "")
                    diagnostic["languages"][lang] = diagnostic["languages"].get(lang, 0) + 1
                    diagnostic["communication_types"][kind] = diagnostic["communication_types"].get(kind, 0) + 1
                    if lang.upper() == language and (allowed is None or str(row.get("PersonNumber")) in allowed):
                        rows.append(row)
                next_link = data.get("__next") or data.get("@odata.nextLink") if isinstance(data, dict) else None
                next_url = str(httpx.URL(next_url or url).join(next_link)) if next_link else None
                if not next_url and len(page) < page_size:
                    break
            else:
                diagnostic["truncated"] = True
            if diagnostic["truncated"]:
                diagnostic["error"] = "PersonCommunication page limit reached; results may be incomplete"
        diagnostic["matched_members"] = len({str(row.get("PersonNumber")) for row in rows})
        logger.info("PersonCommunication diagnostic=%s selected=%d", diagnostic, len(rows))
        return rows, diagnostic
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        diagnostic["error"] = str(exc)[:300]
        logger.warning("PersonCommunication diagnostic=%s", diagnostic)
        return rows, diagnostic


def communication_profiles(rows):
    """Match only explicit social-network URLs and a person number."""
    domains = {
        "X": ("x.com", "twitter.com"),
        "Facebook": ("facebook.com", "fb.com"),
        "Instagram": ("instagram.com",),
        "LinkedIn": ("linkedin.com",),
        "YouTube": ("youtube.com", "youtu.be"),
    }
    result = {}
    for row in rows:
        number = pick(row, "PersonNumber")
        if not number:
            continue
        for key in ("Address",):
            value = row.get(key)
            if not isinstance(value, str):
                continue
            parsed = urlparse(value.strip())
            host = (parsed.hostname or "").lower()
            if parsed.scheme != "https" or not host or parsed.username or parsed.password:
                continue
            for platform, allowed in domains.items():
                if any(host == domain or host.endswith("." + domain) for domain in allowed):
                    result.setdefault(str(number), {})[platform] = value.strip()
    return result


class SocialLinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.data_links = []
        self.navigation_links = []
        self._anchor = None
        self.json_documents = []
        self._json_ld = False
        self._json_chunks = []
        self._excluded_tags = []
        self.link_diagnostics = {"anchors_total": 0, "excluded_links": 0, "social_candidates": 0}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        excluded = tag in ("footer", "nav") or (tag in ("div", "section") and any(word in (attrs.get("class", "")+" "+attrs.get("id", "")).lower().split() for word in ("footer", "site-footer", "main-footer")))
        if self._excluded_tags or excluded:
            if tag == "a" and attrs.get("href"):
                self.link_diagnostics["excluded_links"] += 1
            if tag not in ("meta", "link", "img", "input", "br", "hr", "source", "area"):
                self._excluded_tags.append(tag)
            return
        if tag in ("a", "button", "span", "div"):
            for field in ("data-href", "data-url", "data-social-url"):
                value = attrs.get(field, "")
                if isinstance(value, str) and value.startswith(("https://", "http://", "//")):
                    self.data_links.append(value)
        if tag == "a" and attrs.get("href"):
            self._anchor = {"href": attrs["href"], "text": attrs.get("title", "") + " " + attrs.get("aria-label", "")}
            self.navigation_links.append(self._anchor)
            self.link_diagnostics["anchors_total"] += 1
            self.links.append(attrs["href"])
        elif tag == "link" and "me" in attrs.get("rel", "").lower().split() and attrs.get("href"):
            self.links.append(attrs["href"])
        elif tag == "meta" and (attrs.get("property", "") or attrs.get("name", "")).lower() in ("og:see_also", "article:author", "twitter:creator", "twitter:site", "fediverse:creator") and attrs.get("content"):
            value = attrs["content"].strip()
            if value.startswith("@") and (attrs.get("property", "") or attrs.get("name", "")).lower().startswith("twitter:"):
                value = "https://twitter.com/" + value[1:]
            self.links.append(value)
        elif tag == "script" and attrs.get("type", "").lower() == "application/ld+json":
            self._json_ld = True
            self._json_chunks = []

    def handle_data(self, data):
        if self._anchor is not None:
            self._anchor["text"] += " " + data
        if self._json_ld:
            self._json_chunks.append(data)

    def handle_endtag(self, tag):
        if tag == "a":
            self._anchor = None
        if self._excluded_tags:
            if tag == self._excluded_tags[-1]:
                self._excluded_tags.pop()
            return
        if tag == "script" and self._json_ld:
            self._json_ld = False
            try:
                self.json_documents.append(json.loads("".join(self._json_chunks)))
            except ValueError:
                pass

    def social_references(self):
        def walk(node):
            if isinstance(node, dict):
                kind = node.get("@type", "")
                if isinstance(kind, list):
                    is_person = "Person" in kind
                else:
                    is_person = kind == "Person" or str(kind).endswith("/Person")
                links = node.get("sameAs", []) if is_person else []
                if isinstance(links, str):
                    links = [links]
                if isinstance(links, list):
                    for link in links:
                        if isinstance(link, str):
                            yield link
                for value in node.values():
                    yield from walk(value)
            elif isinstance(node, list):
                for value in node:
                    yield from walk(value)
        for document in self.json_documents:
            yield from walk(document)


_homepage_cache = {}
_homepage_cache_ttl = 86400
# Cache successful HTTP responses separately from extracted social-profile links.
_http_response_cache = {}
_http_response_cache_ttl = 3600
_http_response_cache_max_entries = 256
_http_response_cache_max_bytes = 2_000_000
_http_response_cache_total_max_bytes = 50_000_000
_http_response_cache_path = Path(os.getenv("HTTP_RESPONSE_CACHE_PATH", str(ROOT.parent / ".http_response_cache.json")))
_http_response_inflight = {}
_http_response_cache_lock = None
_http_cache_stats = {"hits": 0, "misses": 0, "coalesced": 0, "writes": 0, "write_ms": 0.0, "miss_new": 0, "miss_expired": 0, "miss_evicted": 0, "miss_uncacheable": 0, "miss_fetch_error": 0, "miss_response_rejected": 0, "reject_http_status": 0, "reject_content_type": 0, "reject_no_store": 0, "reject_private": 0, "reject_set_cookie": 0, "reject_oversize": 0, "negative_hits": 0}
_http_cache_flush_task = None
_http_cache_seen_urls = set()
_http_cache_evicted_urls = set()
_http_cache_rejections = {}
_http_not_found_cache = {}
_http_not_found_ttl = 300
_http_not_found_max_entries = 256


def http_cache_diagnostics():
    return {**_http_cache_stats, "entries": len(_http_response_cache),
            "inflight": len(_http_response_inflight)}


async def flush_http_response_cache():
    """Batch persistence outside the request path and event loop."""
    global _http_cache_flush_task
    try:
        await asyncio.sleep(1.0)
        started = time.monotonic()
        await asyncio.to_thread(save_http_response_cache)
        elapsed = round((time.monotonic() - started) * 1000, 1)
        _http_cache_stats["writes"] += 1
        _http_cache_stats["write_ms"] += elapsed
        logger.info("HTTP cache diagnostics: %s", http_cache_diagnostics())
    finally:
        _http_cache_flush_task = None


def schedule_http_cache_flush():
    global _http_cache_flush_task
    if _http_cache_flush_task is None or _http_cache_flush_task.done():
        _http_cache_flush_task = asyncio.create_task(flush_http_response_cache())



def cached_http_response(url):
    entry = _http_response_cache.get(url)
    if not entry:
        return None
    checked_at, status, headers, content = entry
    if time.time() - checked_at >= _http_response_cache_ttl:
        _http_response_cache.pop(url, None)
        _http_cache_evicted_urls.add(url)
        return None
    return httpx.Response(status, headers=headers, content=content, request=httpx.Request("GET", url))


def store_http_response(url, response):
    """Cache only safe public HTML; record all rejection reasons."""
    reasons = []
    if response.status_code != 200:
        reasons.append("reject_http_status")
    if len(response.content) > _http_response_cache_max_bytes:
        reasons.append("reject_oversize")
    if "text/html" not in response.headers.get("content-type", "").lower():
        reasons.append("reject_content_type")
    cache_control = response.headers.get("cache-control", "").lower()
    if "no-store" in cache_control:
        reasons.append("reject_no_store")
    if "private" in cache_control:
        reasons.append("reject_private")
    if "set-cookie" in response.headers:
        reasons.append("reject_set_cookie")
    for reason in reasons:
        _http_cache_stats[reason] += 1
    if reasons:
        from urllib.parse import urlsplit
        host = (urlsplit(url).hostname or "unknown").lower()
        key = (host, response.status_code)
        record = _http_cache_rejections.setdefault(key, {"count": 0, "reasons": {}})
        record["count"] += 1
        for reason in reasons:
            record["reasons"][reason] = record["reasons"].get(reason, 0) + 1
    if reasons:
        return
    # Bound both entry count and aggregate memory usage.
    existing_size = len(_http_response_cache[url][3]) if url in _http_response_cache else 0
    total_size = sum(len(entry[3]) for entry in _http_response_cache.values()) - existing_size
    while _http_response_cache and (len(_http_response_cache) >= _http_response_cache_max_entries and url not in _http_response_cache or total_size + len(response.content) > _http_response_cache_total_max_bytes):
        oldest = min((key for key in _http_response_cache if key != url), key=lambda key: _http_response_cache[key][0], default=None)
        if oldest is None:
            break
        total_size -= len(_http_response_cache[oldest][3])
        _http_response_cache.pop(oldest, None)
        _http_cache_evicted_urls.add(oldest)
    _http_cache_seen_urls.add(url)
    _http_cache_evicted_urls.discard(url)
    _http_response_cache[url] = (
        time.time(), response.status_code,
        {"content-type": response.headers.get("content-type", "")},
        bytes(response.content))


def load_http_response_cache():
    import base64
    try:
        entries = json.loads(_http_response_cache_path.read_text(encoding="utf-8"))
        if not isinstance(entries, dict):
            return
        total_bytes = 0
        for url, entry in list(entries.items())[:_http_response_cache_max_entries]:
            if (not isinstance(url, str) or not url.startswith("https://") or
                time.time() - entry["checked_at"] >= _http_response_cache_ttl):
                continue
            content = base64.b64decode(entry["body"], validate=True)
            if len(content) > _http_response_cache_max_bytes or total_bytes + len(content) > _http_response_cache_total_max_bytes:
                continue
            total_bytes += len(content)
            _http_response_cache[url] = (
                entry["checked_at"], 200,
                {"content-type": entry["content_type"]}, content)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        logger.info("No usable persistent HTTP response cache")


def save_http_response_cache():
    import base64
    try:
        entries = {
            url: {"checked_at": timestamp, "content_type": headers["content-type"],
                  "body": base64.b64encode(content).decode("ascii")}
            for url, (timestamp, status, headers, content) in _http_response_cache.items()
            if status == 200 and time.time() - timestamp < _http_response_cache_ttl
        }
        temporary = _http_response_cache_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(entries), encoding="utf-8")
        temporary.replace(_http_response_cache_path)
    except OSError as exc:
        logger.warning("HTTP response cache write failed: %s", exc)


async def cached_html_get(client, url, **kwargs):
    """Coalesce concurrent identical HTML GETs; never cache errors or API JSON."""
    global _http_response_cache_lock
    # Only safe, parameter-free public HTTPS pages are eligible.
    if (not url.startswith("https://") or kwargs.get("params") is not None or
        kwargs.get("headers") or kwargs.get("auth")):
        _http_cache_stats["miss_uncacheable"] += 1
        return await client.get(url, **kwargs), False
    if _http_response_cache_lock is None:
        _http_response_cache_lock = asyncio.Lock()
    async with _http_response_cache_lock:
        negative_until = _http_not_found_cache.get(url, 0)
        if negative_until > time.monotonic():
            _http_cache_stats["negative_hits"] += 1
            return httpx.Response(404, request=httpx.Request("GET", url)), True
        _http_not_found_cache.pop(url, None)
        cached = cached_http_response(url)
        if cached is not None:
            _http_cache_stats["hits"] += 1
            return cached, True
        task = _http_response_inflight.get(url)
        if task is None:
            _http_cache_stats["misses"] += 1
            if url in _http_cache_evicted_urls:
                _http_cache_stats["miss_evicted"] += 1
            elif url in _http_cache_seen_urls:
                _http_cache_stats["miss_expired"] += 1
            else:
                _http_cache_stats["miss_new"] += 1
            _http_cache_seen_urls.add(url)
            async def fetch():
                try:
                    response = await client.get(url, **kwargs)
                except Exception:
                    _http_cache_stats["miss_fetch_error"] += 1
                    raise
                if response.status_code == 404:
                    if len(_http_not_found_cache) >= _http_not_found_max_entries:
                        _http_not_found_cache.pop(next(iter(_http_not_found_cache)))
                    _http_not_found_cache[url] = time.monotonic() + _http_not_found_ttl
                store_http_response(url, response)
                if cached_http_response(url) is None:
                    _http_cache_stats["miss_response_rejected"] += 1
                if cached_http_response(url) is not None:
                    schedule_http_cache_flush()
                return response
            task = asyncio.create_task(fetch())
            _http_response_inflight[url] = task
            task.add_done_callback(
                lambda done, key=url: _http_response_inflight.pop(key, None)
                if _http_response_inflight.get(key) is done else None)
        else:
            _http_cache_stats["coalesced"] += 1
    return await asyncio.shield(task), False


load_http_response_cache()

_cache_path = Path(os.getenv("SOCIAL_CACHE_PATH", str(ROOT.parent / ".social_profiles_cache.json")))

def load_homepage_cache():
    try:
        entries = json.loads(_cache_path.read_text(encoding="utf-8"))
        for url, entry in entries.items():
            if time.time() - entry["checked_at"] < _homepage_cache_ttl:
                _homepage_cache[url] = (entry["checked_at"], entry["links"])
    except (OSError, ValueError, TypeError, KeyError):
        logger.info("No usable social-profile disk cache")

def save_homepage_cache():
    try:
        entries = {url: {"checked_at": timestamp, "links": links} for url, (timestamp, links) in _homepage_cache.items() if time.time() - timestamp < _homepage_cache_ttl}
        temporary = _cache_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        temporary.replace(_cache_path)
    except OSError as exc:
        logger.warning("Social-profile cache write failed: %s", exc)

load_homepage_cache()
_social_domains = {
    "X": ("x.com", "twitter.com"),
    "Facebook": ("facebook.com", "fb.com"),
    "Instagram": ("instagram.com",),
    "LinkedIn": ("linkedin.com",),
    "YouTube": ("youtube.com", "youtu.be"),
}


def verified_social_url(value):
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return None
    for platform, domains in _social_domains.items():
        if any(host == domain or host.endswith("." + domain) for domain in domains):
            if parsed.path.strip("/") and not parsed.path.startswith(("/share", "/intent", "/sharer")):
                return platform
    return None


def canonical_homepage_url(value):
    """Normalize Unicode hostnames without changing the URL path or query."""
    try:
        parsed = urlparse(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Malformed homepage URL authority") from exc
    if parsed.scheme != "https" or not host or parsed.username or parsed.password or port not in (None, 443):
        raise ValueError("Invalid HTTPS homepage authority")
    host = host.encode("idna").decode("ascii").lower()
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    netloc = host + (f":{port}" if port is not None else "")
    return urlunparse(parsed._replace(netloc=netloc))


def homepage_url(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or re.search(r"\\s", value):
        return None
    if value.startswith("www.") or ("://" not in value and "." in value):
        value = "https://" + value
    elif value.startswith("http://"):
        value = "https://" + value[len("http://"):]
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
        return None
    return canonical_homepage_url(value)


def public_hostname(host):
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(info[4][0]).is_global for info in addresses)
    except (OSError, ValueError):
        return False


def same_site_www_redirect(original_host, target_host):
    """Only allow exact hostnames differing by the optional www prefix."""
    if not original_host or not target_host:
        return False
    def bare(host):
        host = host.lower().rstrip(".")
        return host[4:] if host.startswith("www.") else host
    return bare(original_host.encode("idna").decode("ascii")) == bare(target_host.encode("idna").decode("ascii"))


def approved_homepage_redirect(source_host, target_host):
    """Permit explicitly reviewed cross-domain moves only (never suffix matching)."""
    try:
        # Verified public personal sites. Exact source -> target pairs only.
        verified_pairs = {
            "andrisilberschmidt.ch": "silberschmidt2027.ch",
            "marc-jost.ch": "marcjost.ch",
            "jauslin.com": "mjauslin.ch",
            "andreasmichaelmeier.ch": "andreasmeier.net",
            "susanne-vincenz-stauffacher.ch": "vincenz-stauffacher.ch",
        }
        pairs = json.loads(os.getenv("APPROVED_HOMEPAGE_REDIRECTS", "{}"))
        if not isinstance(pairs, dict):
            return False
        source = source_host.encode("idna").decode("ascii").lower().rstrip(".")
        target = target_host.encode("idna").decode("ascii").lower().rstrip(".")
        def bare(host):
            return host[4:] if host.startswith("www.") else host
        approved = pairs.get(source)
        return (isinstance(approved, str) and approved.lower().rstrip(".") == target
                or verified_pairs.get(bare(source)) == bare(target))
    except (ValueError, UnicodeError, AttributeError):
        return False


def bounded_homepage_html(content, diagnostic=None):
    """Return at most 1 MB of HTML; reject responses larger than 3 MB."""
    if len(content) > 3_000_000:
        if diagnostic is not None:
            diagnostic.update({"status": "html_too_large", "html_bytes": len(content)})
        return None
    if len(content) > 1_000_000:
        if diagnostic is not None:
            diagnostic.update({"html_bytes": len(content), "html_truncated_for_parsing": True,
                               "parsed_html_bytes": 1_000_000})
        return content[:1_000_000]
    return content


def homepage_error_category(exc):
    """Classify transport failures for diagnostics only."""
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return "http_status"
    if isinstance(exc, httpx.ConnectError):
        message = str(exc).lower()
        if any(word in message for word in ("certificate", "ssl", "tls", "hostname mismatch")):
            return "tls_certificate"
        if any(word in message for word in ("getaddrinfo", "name resolution", "name or service not known", "nodename nor servname")):
            return "dns_resolution"
        return "tcp_connection"
    if isinstance(exc, httpx.HTTPError):
        return "http_transport"
    return "invalid_url_or_data"


async def scan_homepage(client, url, semaphore, diagnostic=None, timeout_retry_semaphore=None):
    now = time.time()
    cached = _homepage_cache.get(url)
    if cached and now - cached[0] < _homepage_cache_ttl and diagnostic is None:
        return cached[1]
    async with semaphore:
        found = {}
        if os.getenv("ENABLE_EXTERNAL_HOMEPAGE_SCAN") != "1":
            if diagnostic is not None:
                diagnostic["status"] = "external_scan_disabled"
            return found
        if diagnostic is not None:
            diagnostic.update({"status": "pending", "http_status": None, "final_url": url, "redirects": [], "cache_hit": bool(cached and now - cached[0] < _homepage_cache_ttl), "http_response_cache_hit": False})
        try:
            current = canonical_homepage_url(url)
            original = urlparse(current)
            if not await asyncio.to_thread(public_hostname, original.hostname):
                if diagnostic is not None:
                    diagnostic["status"] = "hostname_not_public"
                return found
            stage = "request"
            alternate_tried = set()
            for _ in range(3):
                response = None
                # Fail fast for persistent connection errors. Retry only once on
                # certificate hostname mismatch using the verified alternate host.
                for attempt in range(2):
                    try:
                        stage = "http_get"
                        if diagnostic is not None:
                            diagnostic.setdefault("tls_attempts", []).append({"url": current, "attempt": attempt + 1})
                        if attempt and timeout_retry_semaphore is not None:
                            async with timeout_retry_semaphore:
                                response, cache_hit = await cached_html_get(client, current, follow_redirects=False, timeout=12)
                        else:
                            response, cache_hit = await cached_html_get(client, current, follow_redirects=False, timeout=12 if attempt else 8)
                        if diagnostic is not None:
                            diagnostic["http_response_cache_hit"] = cache_hit
                        if diagnostic is not None:
                            diagnostic["tls_attempts"][-1].update({"outcome": "http_response", "http_status": response.status_code})
                        break
                    except httpx.ConnectError as exc:
                        if diagnostic is not None:
                            diagnostic.setdefault("retry_errors", []).append(type(exc).__name__)
                        if diagnostic is not None:
                            attempts = diagnostic.setdefault("tls_attempts", [])
                            if attempts and attempts[-1].get("url") == current and "outcome" not in attempts[-1]:
                                entry = attempts[-1]
                            else:
                                entry = {"url": current, "attempt": attempt + 1}
                                attempts.append(entry)
                            entry.update({"outcome": "connect_error", "error_category": homepage_error_category(exc), "error_detail": str(exc)[:240]})
                        detail = str(exc).lower()
                        certificate_error = any(token in detail for token in ("certificate", "hostname mismatch", "tls", "ssl", "handshake"))
                        parsed_current = urlparse(current)
                        host = parsed_current.hostname or ""
                        # Only explicitly verified personal-site hosts are retried
                        # after TLS failures; never disable certificate checks.
                        verified_alternate = None
                        if original.hostname in ("jauslin.com", "www.jauslin.com") and host == original.hostname:
                            verified_alternate = "www.mjauslin.ch"
                        elif original.hostname in ("andreasmichaelmeier.ch", "www.andreasmichaelmeier.ch") and host == original.hostname and certificate_error:
                            verified_alternate = "www.andreasmeier.net"
                        elif original.hostname == "www.nicolo-paganini.die-mitte.ch" and host == original.hostname:
                            verified_alternate = "nicolo-paganini.die-mitte.ch"
                        if verified_alternate and attempt == 0:
                            alternate = canonical_homepage_url(
                                urlunparse(parsed_current._replace(netloc=verified_alternate))
                            )
                            if await asyncio.to_thread(public_hostname, verified_alternate):
                                alternate_tried.add(alternate)
                                if diagnostic is not None:
                                    diagnostic.setdefault("tls_fallbacks", []).append(
                                        {"from": current, "to": alternate, "reason": "verified_personal_site"}
                                    )
                                current = alternate
                                continue
                        alternate_host = host[4:] if host.startswith("www.") else "www." + host
                        if not certificate_error or attempt > 0 or not same_site_www_redirect(host, alternate_host):
                            if diagnostic is not None:
                                diagnostic["tls_fallback_decision"] = "not_eligible_or_already_retried"
                            raise
                        alternate = canonical_homepage_url(urlunparse(parsed_current._replace(netloc=alternate_host)))
                        if alternate in alternate_tried or not await asyncio.to_thread(public_hostname, alternate_host):
                            raise
                        alternate_tried.add(alternate)
                        if diagnostic is not None:
                            diagnostic.setdefault("tls_fallbacks", []).append({"from": current, "to": alternate, "reason": "certificate_validation_failed"})
                        current = alternate
                    except httpx.TimeoutException as exc:
                        if diagnostic is not None:
                            diagnostic.setdefault("retry_errors", []).append(type(exc).__name__)
                            diagnostic.setdefault("timeout_retries", []).append(
                                {"url": current, "attempt": attempt + 1,
                                 "error": type(exc).__name__,
                                 "action": "retry_same_verified_url" if attempt == 0 else "stop"})
                        # One bounded retry, with a slightly longer timeout.
                        # No certificate verification or hostname checks are bypassed.
                        if attempt == 0:
                            continue
                        raise
                if response is None:
                    if diagnostic is not None:
                        diagnostic["status"] = "tls_fallback_failed"
                    return found
                if response is not None:
                    if diagnostic is not None:
                        diagnostic.update({"http_status": response.status_code, "final_url": current})
                    if response.status_code in (301, 302, 303, 307, 308):
                        stage = "redirect_location"
                        location = response.headers.get("location", "").strip()
                        if not location or any(ord(c) < 32 for c in location):
                            if diagnostic is not None:
                                diagnostic["status"] = "redirect_invalid_location"
                            return found
                        try:
                            stage = "redirect_urljoin"
                            joined = urljoin(current, location)
                            stage = "redirect_canonicalize"
                            target = canonical_homepage_url(joined)
                        except (ValueError, UnicodeError) as exc:
                            if diagnostic is not None:
                                diagnostic["status"] = "redirect_invalid_location"
                                diagnostic["error_detail"] = str(exc)[:240]
                            return found
                        stage = "redirect_validate"
                        parsed = urlparse(target)
                        if diagnostic is not None:
                            diagnostic["redirects"].append(target)
                            diagnostic["redirect_target_host"] = parsed.hostname
                        if parsed.scheme != "https" or not (same_site_www_redirect(original.hostname, parsed.hostname) or approved_homepage_redirect(original.hostname, parsed.hostname)) or parsed.port not in (None, 443) or parsed.username or parsed.password:
                            if diagnostic is not None:
                                diagnostic["status"] = "redirect_blocked"
                                diagnostic["redirect_reason"] = "cross_domain_not_approved"
                            return found
                        if not await asyncio.to_thread(public_hostname, parsed.hostname):
                            if diagnostic is not None:
                                diagnostic["status"] = "redirect_hostname_not_public"
                                diagnostic["redirect_reason"] = "target_hostname_not_public"
                            return found
                        current = target
                        continue
                    if response.status_code != 200 or "text/html" not in response.headers.get("content-type", "").lower():
                        if diagnostic is not None:
                            diagnostic["status"] = "http_error_or_not_html"
                        return found
                    content = response.content
                    content = bounded_homepage_html(content, diagnostic)
                    if content is None:
                        return found
                stage = "parse_html"
                parser = SocialLinkParser()
                html_text = content.decode("utf-8", errors="replace")
                parser.feed(html_text)
                # SP and other CMS pages can serialize personal links inside
                # HTML entities or JSON-escaped script strings.
                from html import unescape
                normalized_markup = unescape(html_text).replace(r"\u002F", "/").replace(r"\u002f", "/").replace(r"\/", "/")
                raw_social = []
                for match in re.findall(r'https?://[^\s"<>]{5,250}', normalized_markup, flags=re.I):
                    candidate = match.rstrip("),;")
                    try:
                        valid_social = verified_social_url(candidate)
                    except (ValueError, UnicodeError):
                        if diagnostic is not None:
                            diagnostic['invalid_social_link_count'] = diagnostic.get('invalid_social_link_count', 0) + 1
                        continue
                    if valid_social and candidate not in raw_social:
                        raw_social.append(candidate)
                if diagnostic is not None:
                    diagnostic["data_social_urls"] = [urljoin(current, item) for item in parser.data_links][:100]
                    diagnostic["raw_social_urls"] = raw_social[:40]
                    diagnostic["raw_social_count"] = len(raw_social)
                if diagnostic is not None:
                    diagnostic["html_bytes"] = len(content)
                    diagnostic["parser"] = dict(parser.link_diagnostics)
                    diagnostic["parser"]["candidate_urls"] = len(parser.links) + len(list(parser.social_references()))
                if diagnostic is not None:
                    keywords = ("kontakt", "contact", "contatto", "about", "about-me",
                                "ueber-mich", "uber-mich", "über-mich", "social-media",
                                "socialmedia", "links", "person", "profil", "profile",
                                "biografie", "biography", "politique", "politik",
                                "à propos", "a propos", "chi sono", "über mich")
                    ranked = []
                    rejected = 0
                    for entry in parser.navigation_links:
                        try:
                            target = urljoin(current, entry["href"])
                            parsed_target = urlparse(target)
                            path = parsed_target.path.lower().rstrip("/")
                            label = entry["text"].strip().lower()
                            if (parsed_target.scheme != "https" or
                                parsed_target.hostname != urlparse(current).hostname or
                                parsed_target.port not in (None, 443) or
                                parsed_target.username or parsed_target.password or
                                parsed_target.query or parsed_target.fragment or
                                target == current):
                                rejected += 1
                                continue
                            path_match = any(path.endswith("/" + word.replace(" ", "-")) for word in keywords)
                            label_match = any(word in label for word in keywords)
                            if not (path_match or label_match):
                                rejected += 1
                                continue
                            score = (2 if path_match else 0) + (1 if label_match else 0)
                            if not any(item[1] == target for item in ranked):
                                ranked.append((score, target))
                        except (ValueError, UnicodeError):
                            rejected += 1
                    ranked.sort(key=lambda item: -item[0])
                    diagnostic["internal_page_candidates"] = [url for _, url in ranked[:2]]
                    diagnostic["subpage_candidate_count"] = len(ranked)
                    diagnostic["subpage_rejected_link_count"] = rejected
                candidate_sources = [(item, "anchor_or_metadata") for item in parser.links]
                candidate_sources += [(item, "json_ld_person_sameAs") for item in parser.social_references()]
                candidate_sources += [(item, "raw_html_or_script") for item in raw_social]
                candidate_sources += [(item, "cms_data_attribute") for item in parser.data_links]
                # Data attributes are diagnostic-only here; their acceptance
                # still requires a personal handle match in homepage_profiles.
                candidate_links = [item for item, source in candidate_sources if source != "cms_data_attribute"]
                if diagnostic is not None:
                    diagnostic["candidate_sources"] = []
                    for href, source in candidate_sources:
                        try:
                            target = urljoin(current, href)
                            platform = verified_social_url(target)
                        except (ValueError, UnicodeError):
                            continue
                        if platform and len(diagnostic["candidate_sources"]) < 60 and not any(x["url"] == target and x["source"] == source for x in diagnostic["candidate_sources"]):
                            diagnostic["candidate_sources"].append({"url": target, "source": source, "platform": platform})
                if diagnostic is not None:
                    diagnostic["social_url_candidates"] = []
                for href in candidate_links:
                    stage = "social_link_urljoin"
                    try:
                        target = urljoin(current, href)
                    except (ValueError, UnicodeError):
                        if diagnostic is not None:
                            diagnostic["invalid_social_link_count"] = diagnostic.get("invalid_social_link_count", 0) + 1
                        continue
                    try:
                        platform = verified_social_url(target)
                    except (ValueError, UnicodeError):
                        if diagnostic is not None:
                            diagnostic['invalid_social_link_count'] = diagnostic.get('invalid_social_link_count', 0) + 1
                        continue
                    if platform:
                        if diagnostic is not None:
                            diagnostic["parser"]["social_candidates"] += 1
                        if diagnostic is not None:
                            if target not in diagnostic["social_url_candidates"]:
                                diagnostic["social_url_candidates"].append(target)
                        found.setdefault(platform, target)
                if diagnostic is not None:
                    diagnostic["status"] = "parsed"
                break
            else:
                if diagnostic is not None:
                    diagnostic["status"] = "too_many_redirects"
        except (httpx.HTTPError, ValueError, UnicodeError) as exc:
            if diagnostic is not None:
                diagnostic["status"] = type(exc).__name__
                diagnostic["error_category"] = homepage_error_category(exc)
                diagnostic["error_detail"] = str(exc)[:240]
                diagnostic["error_stage_url"] = current if "current" in locals() else url
                diagnostic["error_stage"] = stage if "stage" in locals() else "initial_url"
                if "certificate" in str(exc).lower():
                    diagnostic["ssl_hint"] = "check_valid_certificate_on_alternate_www_hostname"
        except Exception as exc:
            # One malformed external site must not fail the entire refresh.
            logger.exception("Homepage scan failed at stage %s for %s", stage if "stage" in locals() else "initial_url", url)
            if diagnostic is not None:
                diagnostic["status"] = type(exc).__name__
                diagnostic["error_detail"] = str(exc)[:240]
                diagnostic["error_stage"] = stage if "stage" in locals() else "initial_url"
                diagnostic["error_stage_url"] = current if "current" in locals() else url
        _homepage_cache[url] = (time.time(), found)
        return found


def personal_handle_matches(url, person_name):
    """Conservative fallback for links embedded in homepage scripts or markup."""
    if not verified_social_url(url) or not person_name:
        return False
    def key(value):
        return "".join(ch for ch in unicodedata.normalize("NFKD", value.casefold()) if ch.isascii() and ch.isalnum())
    surname = key(person_name.split()[-1])
    return len(surname) >= 4 and surname in key(urlparse(url).path)


def classify_unverified_social_candidate(url):
    """Conservative classification; unknown handles always require review."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.strip("/").lower()
    handle = path.split("/")[0] if path else ""
    if host.endswith("linkedin.com") and handle in ("company", "school", "showcase"):
        return "organization", "organization_profile_path"
    if host.endswith("youtube.com") and handle in ("watch", "live", "playlist"):
        return "content", "content_not_account"
    if any(token in handle.replace("-", "").replace("_", "") for token in
           ("spschweiz", "svpch", "fdpschweiz", "fdpliberalen",
            "grueneschweiz", "glpschweiz", "diemitte", "evppev")):
        return "organization", "known_party_handle"
    return "requires_identity_verification", "no_confirmed_personal_identity"


def prioritize_missing_homepages(details):
    """Rank actionable failures ahead of pages needing additional sources."""
    technical = {"fetch_error"}
    def priority(item):
        status = item.get("fetch_status", "")
        if status == "redirect_blocked":
            return 0
        if item.get("reason") in technical:
            return 1
        if item.get("reason") == "no_verified_personal_link":
            return 2
        return 3
    return sorted(details, key=lambda item: (priority(item), item.get("name", "")))


async def homepage_profiles(communications, member_names=None):
    homepages = {}
    for row in communications:
        if str(row.get("CommunicationTypeText", "")).lower() not in ("homepage", "page d'accueil", "sito internet"):
            continue
        url = homepage_url(row.get("Address"))
        if url:
            homepages.setdefault(str(row.get("PersonNumber")), url)
    semaphore = asyncio.Semaphore(8)
    timeout_retry_semaphore = asyncio.Semaphore(2)
    # Inspect raw social URLs as well: many personal sites embed their links in
    # script data rather than anchors. Accept extras only with a surname match.
    diagnostics = [{} for _ in homepages]
    async with PublicHTTPSClient(timeout=8, headers={"User-Agent": "CH-Parlament/1.0"}) as client:
        results = await asyncio.gather(*(scan_homepage(client, url, semaphore, diagnostics[i], timeout_retry_semaphore) for i, url in enumerate(homepages.values())))
    # Follow only two relevant, same-host subpages for otherwise empty homepages.
    subpage_results = {}
    async with PublicHTTPSClient(timeout=8, headers={"User-Agent": "CH-Parlament/1.0"}) as client:
        jobs = []
        for (number, _), links, diagnostic in zip(homepages.items(), results, diagnostics):
            diagnostic["subpage_scans"] = []
            if diagnostic.get("status") != "parsed" or any(personal_handle_matches(url, (member_names or {}).get(number, "")) for url in list(links.values()) + diagnostic.get("raw_social_urls", []) + diagnostic.get("data_social_urls", [])):
                diagnostic["subpage_scan_status"] = "skipped"
                continue
            candidates = diagnostic.get("internal_page_candidates", [])[:2]
            diagnostic["subpage_scan_status"] = "queued" if candidates else "no_candidate"
            for suburl in candidates:
                detail = {"source_url": suburl}
                diagnostic["subpage_scans"].append(detail)
                jobs.append((number, suburl, detail))
        if jobs:
            fetched = await asyncio.gather(*(scan_homepage(client, url, semaphore, detail, timeout_retry_semaphore) for _, url, detail in jobs))
            for (number, suburl, detail), links in zip(jobs, fetched):
                subpage_results.setdefault(number, []).append((suburl, links, detail))
    enriched = {}
    for (number, _), links, diagnostic in zip(homepages.items(), results, diagnostics):
        combined = dict(links)
        for candidate in diagnostic.get("raw_social_urls", []) + diagnostic.get("data_social_urls", []):
            try:
                platform = verified_social_url(candidate)
            except (ValueError, UnicodeError):
                continue
            if platform and platform not in combined and personal_handle_matches(candidate, (member_names or {}).get(number, "")):
                combined[platform] = candidate
        for suburl, sublinks, subdiag in subpage_results.get(number, []):
            for candidate in list(sublinks.values()) + subdiag.get("raw_social_urls", []) + subdiag.get("data_social_urls", []):
                try:
                    platform = verified_social_url(candidate)
                except (ValueError, UnicodeError):
                    continue
                if platform and platform not in combined and personal_handle_matches(candidate, (member_names or {}).get(number, "")):
                    combined[platform] = candidate
                    subdiag.setdefault("accepted_personal_links", {})[platform] = candidate
        if combined:
            enriched[number] = combined
    # Unmatched handles are review candidates, never automatically attributed.
    # Provenance and rejection reason allow manual identity verification.
    for (number, homepage), diagnostic in zip(homepages.items(), diagnostics):
        if number in enriched:
            continue
        review = []
        organizations = []
        seen = set()
        sources = [(homepage, diagnostic)]
        sources.extend((url, detail) for url, _, detail in subpage_results.get(number, []))
        for source_url, detail in sources:
            for item in detail.get("candidate_sources", []):
                candidate = item.get("url", "")
                if candidate in seen:
                    continue
                seen.add(candidate)
                try:
                    platform = verified_social_url(candidate)
                except (ValueError, UnicodeError):
                    continue
                if not platform or personal_handle_matches(candidate, (member_names or {}).get(number, "")):
                    continue
                status, reason = classify_unverified_social_candidate(candidate)
                record = {"url": candidate, "platform": platform,
                          "source_page": source_url,
                          "extraction_source": item.get("source"),
                          "status": status, "classification_reason": reason,
                          "auto_accept": False}
                if status == "requires_identity_verification":
                    review.append(record)
                else:
                    organizations.append(record)
        diagnostic["unverified_personal_candidates"] = review[:30]
        diagnostic["nonpersonal_social_candidates"] = organizations[:30]
    from collections import Counter
    statuses = Counter()
    missing_details = []
    for (number, url), diagnostic in zip(homepages.items(), diagnostics):
        if number in enriched:
            continue
        status = diagnostic.get("status", "unknown")
        candidates = diagnostic.get("social_url_candidates", [])
        if status != "parsed":
            reason = "fetch_error"
        elif candidates:
            reason = "no_verified_personal_link"
        else:
            reason = "no_social_links"
        statuses[reason] += 1
        missing_details.append({
            "person_number": number,
            "name": (member_names or {}).get(number, ""),
            "homepage": url,
            "reason": reason,
            "fetch_status": status,
            "retry_errors": diagnostic.get("retry_errors", []),
            "timeout_retries": diagnostic.get("timeout_retries", []),
            "tls_attempts": diagnostic.get("tls_attempts", []),
            "tls_fallbacks": diagnostic.get("tls_fallbacks", []),
            "tls_fallback_decision": diagnostic.get("tls_fallback_decision"),
            "error_detail": diagnostic.get("error_detail"),
        "error_category": diagnostic.get("error_category"),
            "ssl_hint": diagnostic.get("ssl_hint"),
            "error_stage_url": diagnostic.get("error_stage_url"),
            "redirect_reason": diagnostic.get("redirect_reason"),
            "redirect_target_host": diagnostic.get("redirect_target_host"),
            "redirect_chain": diagnostic.get("redirects", []),
            "candidate_count": len(candidates),
            "unverified_personal_candidates": diagnostic.get("unverified_personal_candidates", []),
            "nonpersonal_social_candidates": diagnostic.get("nonpersonal_social_candidates", []),
            "candidate_samples": candidates[:5],
            "candidate_diagnostics": [
                {**item, "personal_handle_match": personal_handle_matches(item["url"], (member_names or {}).get(number, "")),
                 "rejection_reason": "no_matching_personal_handle" if not personal_handle_matches(item["url"], (member_names or {}).get(number, "")) else "not_selected_or_duplicate"}
                for item in diagnostic.get("candidate_sources", [])[:40]
            ],
            "parser_diagnostics": diagnostic.get("parser", {}),
            "data_attribute_count": len(diagnostic.get("data_social_urls", [])),
            "subpage_scan_status": diagnostic.get("subpage_scan_status"),
            "internal_page_candidates": diagnostic.get("internal_page_candidates", []),
            "subpage_candidate_count": diagnostic.get("subpage_candidate_count", 0),
            "subpage_rejected_link_count": diagnostic.get("subpage_rejected_link_count", 0),
            "subpage_scans": diagnostic.get("subpage_scans", []),
        })
    homepage_report = {
        "checked": len(homepages),
        "with_personal_links": len(enriched),
        "without_personal_links": len(missing_details),
        "reasons": dict(statuses),
        "fetch_statuses": dict(Counter(d.get("status", "unknown") for d in diagnostics)),
        "missing_details": missing_details,
        "prioritized_missing_details": prioritize_missing_homepages(missing_details),
        "redirects_blocked": [
            {"name": item["name"], "homepage": item["homepage"],
             "target_host": item.get("redirect_target_host"),
             "reason": item.get("redirect_reason")}
            for item in missing_details if item.get("fetch_status") == "redirect_blocked"
        ],
    }
    save_homepage_cache()
    return enriched, len(homepages), homepage_report


_party_sources_path = Path(os.getenv("PARTY_PROFILE_SOURCES", str(ROOT.parent / "party_profile_sources.json")))

def configured_party_profiles(member_rows):
    """Only accept manually verified person-specific party profile URLs."""
    try:
        entries = json.loads(_party_sources_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, {"configured": 0, "accepted": 0, "rejected": 0}
    if not isinstance(entries, list):
        return {}, {"configured": 0, "accepted": 0, "rejected": 0}
    members = {str(pick(row, "PersonNumber")): row for row in member_rows if is_current(row)}
    accepted = {}
    rejected = 0
    for entry in entries:
        if not isinstance(entry, dict):
            rejected += 1
            continue
        number = str(entry.get("person_number", ""))
        member = members.get(number)
        if not member or entry.get("verified") is not True:
            rejected += 1
            continue
        expected = normalize(member)["name"].casefold().strip()
        supplied = str(entry.get("name", "")).casefold().strip()
        page = homepage_url(entry.get("profile_url"))
        if not expected or expected != supplied or not page:
            rejected += 1
            continue
        if number in accepted:
            rejected += 1
            continue
        accepted[number] = page
    return accepted, {"configured": len(entries), "accepted": len(accepted), "rejected": rejected}


async def discover_sp_party_profiles(member_rows):
    """Discover named profiles from the SP's official parliamentary roster."""
    roster_url = "https://www.sp-ps.ch/menschen/national-und-staenderat/"
    diagnostics = {"roster_url": roster_url, "status": "pending", "matched": 0}
    members = {}
    for row in member_rows:
        if is_current(row) and str(pick(row, "PartyAbbreviation", "PartyName")).upper() in ("SP", "PS"):
            number = str(pick(row, "PersonNumber"))
            name = normalize(row)["name"]
            slug = "-".join(re.findall(r"[a-z0-9]+", "".join(ch for ch in unicodedata.normalize("NFKD", name.casefold()) if ch.isascii())))
            if slug:
                members[slug] = (number, name)
    found = {}
    try:
        async with httpx.AsyncClient(timeout=8, trust_env=False, follow_redirects=False) as client:
            response, _ = await cached_html_get(client, roster_url)
            response.raise_for_status()
            if "text/html" not in response.headers.get("content-type", "").lower() or len(response.content) > 400_000:
                diagnostics["status"] = "invalid_html"
                return found, diagnostics
            parser = SocialLinkParser()
            parser.feed(response.text)
            for href in parser.links:
                target = urljoin(roster_url, href)
                parsed = urlparse(target)
                if parsed.scheme != "https" or parsed.hostname not in ("www.sp-ps.ch", "sp-ps.ch"):
                    continue
                match = re.fullmatch(r"/person/([a-z0-9-]+)/?", parsed.path)
                if match and match.group(1) in members:
                    number, _ = members[match.group(1)]
                    found[number] = target.split("?", 1)[0].split("#", 1)[0]
            diagnostics.update({"status": "parsed", "matched": len(found)})
    except (httpx.HTTPError, ValueError) as exc:
        diagnostics.update({"status": "fetch_failed", "error": str(exc)[:150]})
    return found, diagnostics


class FdpRosterLinkParser(HTMLParser):
    """Capture FDP modal links together with their visible anchor labels."""
    def __init__(self):
        super().__init__()
        self.entries = []
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.entries.append((self._href, " ".join(" ".join(self._text).split())))
            self._href = None
            self._text = []


def fdp_roster_person_name(label):
    """Remove office labels from FDP roster link text before matching names."""
    label = " ".join(label.split())
    office = r"(?:Nationalrätin|Nationalrat|Ständerätin|Ständerat|Conseillère nationale|Conseiller national|Conseillère aux États|Conseiller aux États)"
    return re.sub(r"\s+" + office + r"(?:\s+.*)?$", "", label, flags=re.IGNORECASE).strip()


def evp_member_candidates(profile_key, active_members_by_key):
    """Resolve only explicitly verified EVP public-name aliases."""
    aliases = {"nikgugger": "niklaussamuelgugger"}
    canonical = aliases.get(profile_key, profile_key)
    return active_members_by_key.get(canonical, [])


async def discover_other_party_profiles(member_rows):
    """Match official party roster links to active members, without guessing URLs."""
    rosters = {
        "FDP": [
            ("https://www.fdp.ch/partei/organe/fraktion/", "fdp.ch", "/partei/organe/fraktion/fraktion-detail/members/user/"),
            ("https://www.fdp.ch/personen/nationalraete", "fdp.ch", "/personen/nationalraete/nationalrat-detail/members/user/"),
            ("https://www.fdp.ch/personen/staenderaete", "fdp.ch", "/personen/staenderaete/staenderat-detail/members/user/"),
        ],
        "SVP": [("https://www.svp.ch/menschen/personen/", "svp.ch", "/menschen/portrait/")],
        "EVP": [("https://www.evppev.ch/menschen/nationalrat-und-fraktion", "evppev.ch", "/menschen/nationalrat-und-fraktion/")],
        "Mitte": [("https://die-mitte.ch/die-mitte/menschen/", "die-mitte.ch", "/")],
        "Gruene": [("https://gruene.ch/gruene-im-bundeshaus", "gruene.ch", "/")],
        "GLP": [
            ("https://glp.swiss/position-person/nationalrat/", "glp.swiss", "/personen/"),
            ("https://glp.swiss/partei/menschen/praesidium-geschaeftsleitung/", "glp.swiss", "/personen/"),
            ("https://zh.glp.swiss/partei/menschen/mandatstraeger-innen/", "zh.glp.swiss", "/personen/"),
            ("https://be.glp.swiss/partei/menschen/mandatstraeger-innen/", "be.glp.swiss", "/personen/"),
        ],
    }
    def key(value):
        return "".join(c for c in unicodedata.normalize("NFKD", value.casefold()) if c.isascii() and c.isalnum())
    members = {}
    for row in member_rows:
        if not is_current(row):
            continue
        name = normalize(row)["name"]
        party = str(pick(row, "PartyAbbreviation", "PartyName")).upper()
        group = "FDP" if party in ("FDP", "PLR") else "SVP" if party in ("SVP", "UDC") else "Mitte" if party in ("M-E", "MITTE", "CVP", "PDC") else "Gruene" if party in ("GPS", "PES", "GRÜNE", "GRUENE") else "GLP" if party in ("GLP", "PVL") else "EVP" if party in ("EVP", "PEV") else None
        if group:
            members.setdefault(group, []).append((str(pick(row, "PersonNumber")), name))
    # FDP abbreviations vary across Parliament API records. A unique full-name
    # match against all active members is safer than guessing another party code.
    active_members_by_key = {}
    for row in member_rows:
        if is_current(row):
            active_members_by_key.setdefault(key(normalize(row)["name"]), []).append(
                (str(pick(row, "PersonNumber")), normalize(row)["name"])
            )
    discovered, diagnostics = {}, {}
    async with httpx.AsyncClient(timeout=8, trust_env=False, follow_redirects=False) as client:
        for group, roster_list in rosters.items():
            for roster_url, host, prefix in roster_list:
                report = {"status": "pending", "matched": 0, "url": roster_url}
                diagnostics.setdefault(group, {"status": "pending", "matched": 0, "rosters": []})["rosters"].append(report)
                try:
                    response = await client.get(roster_url)
                    response.raise_for_status()
                    if "text/html" not in response.headers.get("content-type", "").lower() or len(response.content) > 500_000:
                        report["status"] = "invalid_html"
                        continue
                    parser = SocialLinkParser()
                    parser.feed(response.text)
                    matches = {}
                    fdp_profile_samples = []
                    if group == "FDP":
                        fdp_parser = FdpRosterLinkParser()
                        fdp_parser.feed(response.text)
                        modal_samples = []
                        match_diagnostics = []
                        for href, label in fdp_parser.entries:
                            parsed_modal = urlparse(urljoin(roster_url, href))
                            query = parse_qs(parsed_modal.query)
                            if parsed_modal.scheme != "https" or parsed_modal.hostname not in ("fdp.ch", "www.fdp.ch") or not any("showModal" in v for values in query.values() for v in values):
                                continue
                            if len(modal_samples) < 12:
                                modal_samples.append({"url": urljoin(roster_url, href)[:400], "label": label[:100]})
                            clean_name = fdp_roster_person_name(label)
                            candidate_records = active_members_by_key.get(key(clean_name), [])
                            candidates = [number for number, name in candidate_records]
                            candidate_names = [name for number, name in candidate_records]
                            if len(candidates) == 1:
                                matches[candidates[0]] = urljoin(roster_url, href)
                            if len(match_diagnostics) < 20:
                                match_diagnostics.append({
                                    "label": label[:100],
                                    "clean_name": clean_name[:100],
                                    "normalized_key": key(clean_name),
                                    "candidate_count": len(candidates),
                                    "candidate_names": candidate_names[:3],
                                    "reason": "matched" if len(candidates) == 1 else "ambiguous" if candidates else "no_exact_name_match",
                                })
                        report["modal_match_diagnostics"] = match_diagnostics
                        report["modal_matched"] = len(matches)
                        report["modal_link_samples"] = modal_samples
                    evp_samples = []
                    evp_match_diagnostics = []
                    for href in parser.links:
                        url = urljoin(roster_url, href)
                        parsed = urlparse(url)
                        if parsed.scheme != "https" or not parsed.hostname:
                            continue
                        on_party_site = parsed.hostname in (host, "www." + host) or (group == "GLP" and parsed.hostname.endswith(".glp.swiss"))
                        if group == "FDP" and on_party_site and "/members/user/" in parsed.path and len(fdp_profile_samples) < 15:
                            sample = url.split("?", 1)[0].split("#", 1)[0]
                            if sample not in fdp_profile_samples:
                                fdp_profile_samples.append(sample)
                        if group == "Gruene":
                            # The Greens' parliamentary roster links directly to personal
                            # websites, not to individual pages on gruene.ch.
                            if on_party_site or parsed.port not in (None, 443):
                                continue
                            if parsed.username or parsed.password or parsed.hostname in ("localhost",):
                                continue
                            path_key = key(parsed.hostname.removeprefix("www.").split(".")[0])
                        else:
                            if not on_party_site or not parsed.path.startswith(prefix):
                                continue
                            path_key = key(parsed.path.rstrip("/").split("/")[-1])
                        if group == "EVP":
                            # EVP profile paths use nr-<full-name>; Parliament's
                            # party abbreviations may differ, so compare against
                            # every active member with a unique full-name match.
                            if len(evp_samples) < 20:
                                evp_samples.append({"url": url[:300], "path_key": path_key})
                            if path_key.startswith("nr"):
                                path_key = path_key[2:]
                            candidate_records = evp_member_candidates(path_key, active_members_by_key)
                            if len(evp_match_diagnostics) < 20:
                                evp_match_diagnostics.append({
                                    "url": url[:300], "normalized_key": path_key,
                                    "candidate_count": len(candidate_records),
                                    "candidate_names": [name for _, name in candidate_records[:3]],
                                })
                            if len(candidate_records) == 1:
                                matches[candidate_records[0][0]] = url.split("?", 1)[0].split("#", 1)[0]
                            continue
                        if not path_key:
                            continue
                        # GLP uses /personen/<name>/; only accept exact unique
                        # names of current GLP members, never inferred URLs.
                        candidates = [number for number, name in members.get(group, []) if key(name) == path_key]
                        if len(candidates) == 1:
                            matches[candidates[0]] = url.split("?", 1)[0].split("#", 1)[0]
                    # Preserve verified FDP modal URLs including query parameters.
                    # Exact full-name matches were established from roster labels.
                    if group == "FDP":
                        modal_matches = {
                            number: url for number, url in matches.items()
                            if parse_qs(urlparse(url).query).get("tx_wngfeusers_members[action]") == ["showModal"]
                        }
                        report["modal_sources"] = len(modal_matches)
                        for number, modal_url in modal_matches.items():
                            discovered.setdefault(number, modal_url)
                        for number, profile_url in matches.items():
                            if number not in modal_matches:
                                discovered[number] = profile_url
                    else:
                        discovered.update(matches)
                    report.update({"status": "parsed", "matched": len(matches), "links": len(parser.links)})
                    if group == "EVP":
                        report["profile_url_samples"] = evp_samples
                        report["match_diagnostics"] = evp_match_diagnostics
                        report["active_evp_members"] = [name for _, name in members.get("EVP", [])]
                    if group == "FDP":
                        report["profile_url_samples"] = fdp_profile_samples
                        report["link_samples"] = [urljoin(roster_url, href)[:250] for href in parser.links[:12]]
                    diagnostics[group]["matched"] = len([number for number in discovered if number in {n for n, _ in members.get(group, [])}])
                    diagnostics[group]["status"] = "parsed"
                except (httpx.HTTPError, ValueError) as exc:
                    report.update({"status": "fetch_failed", "error": str(exc)[:150]})
                    if diagnostics[group]["status"] != "parsed":
                        diagnostics[group]["status"] = "fetch_failed"
    return discovered, diagnostics


def party_personal_handle_matches(url, person_name):
    """Match personal handles with Swiss German umlaut transliterations."""
    from urllib.parse import unquote
    def normalize_handle(value):
        value = unquote(value).casefold()
        value = value.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
        return "".join(c for c in unicodedata.normalize("NFKD", value) if c.isascii() and c.isalnum())
    parts = person_name.split()
    if len(parts) < 2:
        return False
    surname = normalize_handle(parts[-1])
    handle = normalize_handle(urlparse(url).path)
    return len(surname) >= 4 and surname in handle


async def party_profile_links(member_rows, fresh_profiles=None):
    # A complete, valid per-member snapshot needs no roster discovery or HTTP.
    # Include empty dictionaries: they represent checked members with no verified links.
    active_ids = {str(pick(row, "PersonNumber")) for row in member_rows if is_current(row)}
    fresh_profiles = fresh_profiles or {}
    if active_ids and active_ids.issubset(fresh_profiles) and all(
        isinstance(fresh_profiles[number], dict) for number in active_ids
    ):
        return ({number: dict(fresh_profiles[number]) for number in active_ids}, {
            "snapshot_reused_members": len(active_ids),
            "snapshot_scanned_members": 0,
            "discovery_skipped": True,
            "discovery": {"status": "skipped_complete_snapshot"},
            "pages": [],
        })
    sources, diagnostic = configured_party_profiles(member_rows)
    discovered, discovery_diagnostic = await discover_sp_party_profiles(member_rows)
    other_discovered, other_diagnostics = await discover_other_party_profiles(member_rows)
    diagnostic["discovery"] = {"SP": discovery_diagnostic, "other_parties": other_diagnostics}
    discovered.update(other_discovered)
    # Additional official person pages verified independently. Only exact active
    # member names are eligible; no URLs or social handles are guessed.
    official_additional_pages = {
        "Nik Gugger": "https://www.evppev.ch/menschen/nationalrat-und-fraktion/nr-nik-gugger/",
        "Martine Docourt": "https://www.sp-ps.ch/person/martine-docourt-ducommun/",
    }
    extra_matched = {}
    for number, name in ((str(pick(row, "PersonNumber")), normalize(row)["name"]) for row in member_rows if is_current(row)):
        page = official_additional_pages.get(name)
        if page and number not in sources:
            extra_matched[number] = page
            discovered.setdefault(number, page)
    diagnostic["additional_official_pages"] = {"matched": len(extra_matched), "members": list(extra_matched.values())}
    for number, url in discovered.items():
        sources.setdefault(number, url)
    fresh_profiles = fresh_profiles or {}
    reused = {number: links for number, links in fresh_profiles.items()
              if number in sources and isinstance(links, dict)}
    sources = {number: url for number, url in sources.items() if number not in reused}
    diagnostic["snapshot_reused_members"] = len(reused)
    diagnostic["snapshot_scanned_members"] = len(sources)
    if not sources:
        diagnostic["pages"] = []
        return dict(reused), diagnostic
    # Diagnose repeated FDP profile URLs without logging query parameters or personal data.
    from collections import Counter
    fdp_urls = [url for url in sources.values()
                if urlparse(url).hostname in ("fdp.ch", "www.fdp.ch")]
    fdp_exact_counts = Counter(fdp_urls)
    fdp_path_counts = Counter((urlparse(url).hostname, urlparse(url).path.rstrip("/"))
                              for url in fdp_urls)
    diagnostic["fdp_url_reuse"] = {
        "total": len(fdp_urls),
        "unique_exact": len(fdp_exact_counts),
        "duplicate_exact": sum(count - 1 for count in fdp_exact_counts.values()),
        "unique_paths": len(fdp_path_counts),
        "same_path_different_query": sum(count - 1 for count in fdp_path_counts.values()),
    }
    semaphore = asyncio.Semaphore(8)
    members = {str(pick(row, "PersonNumber")): normalize(row)["name"] for row in member_rows if is_current(row)}
    def ascii_key(value):
        return "".join(ch for ch in unicodedata.normalize("NFKD", value.casefold()) if ch.isascii() and ch.isalnum())
    async with PublicHTTPSClient(timeout=5, headers={"User-Agent": "CH-Parlament/1.0"}) as client:
        page_diagnostics = [{} for _ in sources]
        results = await asyncio.gather(*(scan_homepage(client, url, semaphore, page_diagnostics[i]) for i, url in enumerate(sources.values())))
    save_homepage_cache()
    verified = {}
    pages = []
    for (number, page_url), links, fetch_diagnostic in zip(sources.items(), results, page_diagnostics):
        surname = ascii_key(members[number].split()[-1])
        accepted = {}
        rejected = {}
        candidates = fetch_diagnostic.get("social_url_candidates", list(links.values()))
        for link in candidates:
            try:
                platform = verified_social_url(link)
                if not platform:
                    continue
                handle = ascii_key(urlparse(link).path)
            except (ValueError, UnicodeError):
                fetch_diagnostic["invalid_social_link_count"] = fetch_diagnostic.get("invalid_social_link_count", 0) + 1
                continue
            # A personal profile on the named party page is acceptable if the
            # handle contains the surname; never accept generic party accounts.
            is_personal = party_personal_handle_matches(link, members[number])
            # This abbreviated handle is explicitly linked on the named FDP
            # profile page, unlike generic party accounts in the footer.
            if number == "4290" and platform == "X" and urlparse(link).path.strip("/").lower() == "andrisilbi_":
                is_personal = True
            if is_personal:
                accepted.setdefault(platform, link)
            else:
                rejected.setdefault(platform, {"url": link, "reason": "Kein persönlicher Namensbezug im Profilpfad"})
        if accepted:
            verified[number] = accepted
        fetch_status = fetch_diagnostic.get("status", "unknown")
        if fetch_status != "parsed":
            outcome = "fetch_or_parse_failed"
        elif accepted:
            outcome = "personal_links_found"
        elif candidates:
            outcome = "only_unverified_or_party_links"
        else:
            outcome = "no_social_links_in_html"
        pages.append({
            "person_number": number,
            "name": members[number],
            "profile_url": page_url,
            "outcome": outcome,
            "found_links": len(links),
            "fetch": fetch_diagnostic,
            "accepted": accepted,
            "rejected": rejected,
            "note": "Keine Links gefunden: Seite nicht erreichbar, kein HTML, Cache leer oder keine Social-Links" if not links else None,
        })
    diagnostic["outcome_counts"] = {status: sum(page["outcome"] == status for page in pages) for status in ("personal_links_found", "only_unverified_or_party_links", "no_social_links_in_html", "fetch_or_parse_failed")}
    diagnostic["matched_personal_handles"] = len(verified)
    diagnostic["pages"] = pages
    verified.update(reused)
    return verified, diagnostic


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/members")
async def members(language: str = Query("DE", pattern="^(DE|FR|IT|RM)$")):
    try:
        rows = await fetch_members(language)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.exception("Member API request failed")
        raise HTTPException(status_code=502, detail=f"Parlamentsdienst nicht erreichbar: {exc}") from exc
    result = [normalize(row) for row in rows if is_current(row)]
    communications, communication_debug = await fetch_communications(language, {member["id"] for member in result})
    profiles = communication_profiles(communications)
    for member in result:
        member["socialmedia"].update(profiles.get(member["id"], {}))
    logger.info("Members normalized raw=%d current=%d sample=%s", len(rows), len(result), result[0] if result else None)
    # One member can appear in several records; preserve distinct council memberships.
    unique = {(item["id"], item["rat"]): item for item in result if item["name"]}
    logger.info("Members returned unique=%d", len(unique))
    return {
        "debug": {"raw_count": len(rows), "current_count": len(result), "unique_count": len(unique), "sample_keys": list(rows[0].keys()) if rows else [], "active_sample": [{"Active": r.get("Active"), "DateLeaving": r.get("DateLeaving")} for r in rows[:3]], "social_profiles": sum(bool(m["socialmedia"]) for m in unique.values()), "communication_count": len(communications), "communication_sample_keys": list(communications[0]) if communications else [], "communication_diagnostic": communication_debug},
        "data_language": language,
        "source": "Parlamentsdienste der Bundesversammlung, Bern",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "members": sorted(unique.values(), key=lambda m: m["name"].casefold()),
    }


_profile_snapshot_path = Path(os.getenv("SOCIAL_SNAPSHOT_PATH", str(ROOT.parent / ".social_profiles_snapshot.json")))

def read_profile_snapshot():
    try:
        data = json.loads(_profile_snapshot_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}

def save_profile_snapshot(snapshot):
    try:
        temporary = _profile_snapshot_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        temporary.replace(_profile_snapshot_path)
    except OSError as exc:
        logger.warning("Unable to save social snapshot: %s", exc)

@app.get("/api/social-profiles")
async def social_profiles_cached(language: str = Query("DE", pattern="^(DE|FR|IT|RM)$")):
    """Return the last saved profiles without network requests."""
    all_snapshots = read_profile_snapshot()
    from .social_review import apply_decisions
    apply_decisions(all_snapshots, language)
    snapshot = all_snapshots.get(language, {})
    return {"profiles": snapshot.get("profiles", {}), "profiles_by_source": snapshot.get("profiles_by_source", {}), "profile_evidence": snapshot.get("profile_evidence", {}), "review_candidates": snapshot.get("review_candidates", {}), "updated_at": snapshot.get("updated_at"), "cached": True, "debug": snapshot.get("debug", {})}

_refresh_lock = asyncio.Lock()

@app.post("/api/social-profiles/refresh")
async def social_profiles_refresh(request: Request, language: str = Query("DE", pattern="^(DE|FR|IT|RM)$"), force: bool = Query(False)):
    configured_token = os.getenv("SOCIAL_REFRESH_TOKEN")
    if not configured_token:
        raise HTTPException(status_code=503, detail="Set SOCIAL_REFRESH_TOKEN to enable refresh")
    if not secrets.compare_digest(request.headers.get("X-Refresh-Token", ""), configured_token):
        raise HTTPException(status_code=403, detail="Refresh authorization required")
    if _refresh_lock.locked():
        raise HTTPException(status_code=409, detail="Refresh already running")
    async with _refresh_lock:
        return await _run_social_profiles_refresh(language, force)


async def _run_social_profiles_refresh(language, force):
    """Refresh profiles independently of the immediate cached response."""
    cache_before = http_cache_diagnostics()
    rejection_before = {key: value["count"] for key, value in _http_cache_rejections.items()}
    rejection_reason_before = {key: dict(value["reasons"]) for key, value in _http_cache_rejections.items()}
    refresh_started = time.monotonic()
    phase_ms = {}
    phase_started = time.monotonic()
    rows = await fetch_members(language)
    phase_ms["fetch_members"] = round((time.monotonic() - phase_started) * 1000, 1)
    ids = {str(pick(row, "PersonNumber")) for row in rows if is_current(row)}
    phase_started = time.monotonic()
    communications, diagnostic = await fetch_communications(language, ids)
    phase_ms["fetch_communications"] = round((time.monotonic() - phase_started) * 1000, 1)
    phase_started = time.monotonic()
    previous = read_profile_snapshot().get(language, {})
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    homepage_checked = previous.get("homepage_checked_at", {})
    homepage_urls = previous.get("homepage_urls", {})
    old_sources = previous.get("profiles_by_source", {})
    current_homepages = {}
    for row in communications:
        if str(row.get("CommunicationTypeText", "")).lower() not in ("homepage", "page d'accueil", "sito internet"):
            continue
        url = homepage_url(row.get("Address"))
        if url:
            current_homepages.setdefault(str(row.get("PersonNumber")), url)
    ttl_hours = max(1, min(720, int(os.getenv("SOCIAL_HOMEPAGE_TTL_HOURS", "24"))))
    reusable_homepages = {}
    if not force:
        for number, url in current_homepages.items():
            try:
                stamp = datetime.fromisoformat(homepage_checked[number])
                if (stamp.tzinfo is None or now - stamp.astimezone(timezone.utc) > timedelta(hours=ttl_hours)
                        or homepage_urls.get(number) != url):
                    continue
                links = old_sources.get(number, {}).get("Parlamentarier-Homepage", {})
                if not isinstance(links, dict):
                    continue
                reusable_homepages[number] = {
                    platform: link for platform, link in links.items()
                    if platform in _social_domains and isinstance(link, str)
                    and verified_social_url(link)}
            except (KeyError, ValueError, TypeError):
                continue
    scan_communications = [
        row for row in communications
        if str(row.get("PersonNumber")) not in reusable_homepages
        or str(row.get("CommunicationTypeText", "")).lower() not in ("homepage", "page d'accueil", "sito internet")
    ]
    phase_ms["homepage_snapshot_lookup"] = round((time.monotonic() - phase_started) * 1000, 1)
    phase_started = time.monotonic()
    profiles, scanned_homepages, homepage_report = await homepage_profiles(
        scan_communications,
        {str(pick(row, "PersonNumber")): normalize(row)["name"] for row in rows if is_current(row)})
    phase_ms["homepage_profiles"] = round((time.monotonic() - phase_started) * 1000, 1)
    phase_started = time.monotonic()
    profiles.update(reusable_homepages)
    homepages = len(current_homepages)
    homepage_report["snapshot_reused_members"] = len(reusable_homepages)
    homepage_report["snapshot_scanned_members"] = scanned_homepages
    homepage_report["checked"] = homepages
    homepage_report["with_personal_links"] = sum(bool(profiles.get(number)) for number in current_homepages)
    homepage_report["without_personal_links"] = homepages - homepage_report["with_personal_links"]
    personal_profiles = {number: dict(links) for number, links in profiles.items()}
    parliament_profiles = communication_profiles(communications)
    checked = previous.get("party_checked_at", {})
    old_party = previous.get("profiles_by_source", {})
    fresh_party = {}
    if not force:
        for number in ids:
            try:
                timestamp = datetime.fromisoformat(checked[number])
                if timestamp.tzinfo is None or now - timestamp.astimezone(timezone.utc) > timedelta(hours=24):
                    continue
                links = old_party.get(number, {}).get("Partei-Homepage", {})
                if isinstance(links, dict):
                    fresh_party[number] = {platform: url for platform, url in links.items()
                                           if platform in _social_domains and isinstance(url, str)
                                           and verified_social_url(url)}
            except (KeyError, TypeError, ValueError):
                continue
    phase_ms["party_snapshot_lookup"] = round((time.monotonic() - phase_started) * 1000, 1)
    phase_started = time.monotonic()
    party_links, party_diagnostic = await party_profile_links(rows, fresh_party)
    phase_ms["party_profile_links"] = round((time.monotonic() - phase_started) * 1000, 1)
    phase_started = time.monotonic()
    previous_profiles = previous.get("profiles", {})
    retained = 0
    for number in ids:
        old = previous_profiles.get(number, {})
        if not isinstance(old, dict):
            continue
        current = profiles.get(number, {})
        for platform, link in old.items():
            if platform in _social_domains and platform not in current and isinstance(link, str) and verified_social_url(link):
                if number not in profiles:
                    profiles[number] = current
                current[platform] = link
                retained += 1
    party_added = 0
    for number, links in party_links.items():
        if not links:
            continue
        existing = profiles.setdefault(number, {})
        if not existing:
            party_added += 1
        for platform, url in links.items():
            existing.setdefault(platform, url)
    # Empty dictionaries are not social profiles and must never count as coverage.
    profiles = {number: links for number, links in profiles.items() if number in ids and links}
    by_source = {}
    for number in ids:
        sources = {
            "Parlament-Homepage": parliament_profiles.get(number, {}),
            "Partei-Homepage": party_links.get(number, {}),
            "Parlamentarier-Homepage": personal_profiles.get(number, {}),
        }
        if any(sources.values()):
            by_source[number] = sources
    member_names = {str(pick(row, "PersonNumber")): normalize(row)["name"]
                    for row in rows if is_current(row)}
    profile_evidence = evidence_for_sources(by_source, current_homepages, now.isoformat())
    review_candidates, review_diagnostic = load_review_candidates(
        member_names, verified_social_url,
        path=os.getenv("SOCIAL_REVIEW_CANDIDATES_PATH",
                       str(ROOT.parent / "social_review_candidates.json")),
        checked_at=now.isoformat())
    discovered_review = discover_party_review_candidates(
        party_diagnostic, member_names, verified_social_url, now.isoformat())
    for number, entries in discovered_review.items():
        existing = review_candidates.setdefault(number, [])
        known = {item["url"] for item in existing}
        for entry in entries:
            if entry["url"] not in known:
                existing.append(entry)
                known.add(entry["url"])
    homepage_discovered = discover_personal_homepage_review_candidates(
        homepage_report, member_names, current_homepages, verified_social_url,
        now.isoformat())
    for number, entries in homepage_discovered.items():
        existing = review_candidates.setdefault(number, [])
        known = {item["url"] for item in existing}
        for entry in entries:
            if entry["url"] not in known:
                existing.append(entry)
                known.add(entry["url"])
    review_diagnostic["personal_homepage_discovered"] = sum(
        len(entries) for entries in homepage_discovered.values())
    from .social_regional_discovery import discover_regional_party_candidates
    regional_discovered, regional_diagnostic = await discover_regional_party_candidates(
        member_names, verified_social_url, checked_at=now.isoformat())
    for number, entries in regional_discovered.items():
        existing = review_candidates.setdefault(number, [])
        known = {item["url"] for item in existing}
        for entry in entries:
            if entry["url"] not in known:
                existing.append(entry)
                known.add(entry["url"])
    review_diagnostic["regional_party_discovery"] = regional_diagnostic
    review_diagnostic["official_party_discovered"] = sum(
        len(items) for items in discovered_review.values())
    review_diagnostic["total_for_review"] = sum(
        len(items) for items in review_candidates.values())
    from .social_candidate_priority import annotate_candidates
    annotate_candidates(review_candidates)
    from .social_cross_source import annotate_cross_source
    annotate_cross_source(review_candidates, profiles)
    # Candidates are separate from profiles: never promote a search result to
    # a verified personal account without independent identity confirmation.
    member_homepages = {str(row.get("PersonNumber")) for row in communications if str(row.get("CommunicationTypeText", "")).lower() in ("homepage", "page d'accueil", "sito internet") and homepage_url(row.get("Address"))}
    missing = {
        "members_total": len(ids),
        "members_with_homepage": len(member_homepages),
        "members_without_homepage": len(ids - member_homepages),
        "members_with_social_profiles": len(profiles),
        "members_with_homepage_but_no_social_profiles": len(member_homepages - set(profiles)),
        "members_without_social_profiles": len(ids - set(profiles)),
    }
    platforms = {platform: sum(platform in links for links in profiles.values()) for platform in _social_domains}
    checked_next = {number: checked[number] for number in fresh_party}
    checked_next.update({number: now.isoformat() for number in ids if number not in fresh_party})
    homepage_checked_next = {
        number: homepage_checked[number] for number in reusable_homepages}
    homepage_checked_next.update({
        number: now.isoformat() for number in current_homepages
        if number not in reusable_homepages})
    data = {"member_names": member_names, "homepage_checked_at": homepage_checked_next,
            "homepage_urls": current_homepages,
            "party_checked_at": checked_next, "profiles": profiles, "profiles_by_source": by_source, "profile_evidence": profile_evidence, "review_candidates": review_candidates, "updated_at": datetime.now(timezone.utc).isoformat(), "cached": False, "debug": {"review_candidates": review_diagnostic, "party_sources": party_diagnostic, "party_refresh_force": force, "party_refresh_reused": len(fresh_party), "homepage_refresh_force": force, "homepage_refresh_reused": len(reusable_homepages), "homepage_refresh_scanned": scanned_homepages, "homepage_refresh_ttl_hours": ttl_hours, "party_members_added": party_added, "previous_links_retained": retained, "coverage": missing, "platforms": platforms, "homepages_checked": homepages, "homepage_diagnostics": homepage_report, "homepage_profiles": len(profiles), "communication_count": len(communications), "communication_error": diagnostic.get("error")}}
    phase_ms["merge_and_coverage"] = round((time.monotonic() - phase_started) * 1000, 1)
    phase_started = time.monotonic()
    cache_after = http_cache_diagnostics()
    rejection_groups = []
    for (host, status), record in _http_cache_rejections.items():
        key = (host, status)
        delta = record["count"] - rejection_before.get(key, 0)
        if delta <= 0:
            continue
        prior = rejection_reason_before.get(key, {})
        rejection_groups.append({
            "domain": host, "http_status": status, "count": delta,
            "reasons": {reason: count - prior.get(reason, 0)
                        for reason, count in record["reasons"].items()
                        if count > prior.get(reason, 0)},
        })
    rejection_groups.sort(key=lambda item: item["count"], reverse=True)
    data["debug"]["http_cache"] = {
        **cache_after,
        "rejections_by_domain_status": rejection_groups[:30],
        "refresh_negative_hits": cache_after["negative_hits"] - cache_before["negative_hits"],
        "refresh_hits": cache_after["hits"] - cache_before["hits"],
        "refresh_misses": cache_after["misses"] - cache_before["misses"],
        **{f"refresh_{key}": cache_after[key] - cache_before[key]
           for key in ("miss_new", "miss_expired", "miss_evicted", "miss_uncacheable",
                       "miss_fetch_error", "miss_response_rejected", "reject_http_status",
                       "reject_content_type", "reject_no_store", "reject_private",
                       "reject_set_cookie", "reject_oversize")},
        "refresh_coalesced": cache_after["coalesced"] - cache_before["coalesced"],
        "refresh_writes": cache_after["writes"] - cache_before["writes"],
        "refresh_write_ms": round(cache_after["write_ms"] - cache_before["write_ms"], 1),
        "refresh_duration_ms": round((time.monotonic() - refresh_started) * 1000),
    }
    phase_ms["http_cache_diagnostics"] = round((time.monotonic() - phase_started) * 1000, 1)
    phase_started = time.monotonic()
    snapshot = read_profile_snapshot()
    snapshot[language] = data
    from .social_review import apply_decisions
    apply_decisions(snapshot, language)
    data["debug"]["phase_timings_ms"] = phase_ms
    data["debug"]["phase_timings_ms"]["snapshot_read_and_prepare"] = round((time.monotonic() - phase_started) * 1000, 1)
    save_started = time.monotonic()
    save_profile_snapshot(snapshot)
    logger.info("Social refresh snapshot save_ms=%.1f phase_timings_ms=%s", (time.monotonic() - save_started) * 1000, phase_ms)
    logger.info("Social refresh complete homepages=%d profiles=%d", homepages, len(profiles))
    return data


@app.get("/api/social-profiles/missing-homepage-diagnostic")
async def missing_homepage_diagnostic(
    language: str = Query("DE", pattern="^(DE|FR|IT|RM)$"),
    scan: bool = Query(False),
):
    """Identify uncovered members with homepages; scan only those on demand.

    The default is network-light and does not invalidate the normal refresh
    snapshot. scan=true inspects at most 30 missing homepages for review.
    """
    rows = await fetch_members(language)
    current = {
        str(pick(row, "PersonNumber")): normalize(row)["name"]
        for row in rows if is_current(row)
    }
    communications, communication_diagnostic = await fetch_communications(
        language, set(current)
    )
    snapshot = read_profile_snapshot().get(language, {})
    covered = {
        number for number, links in snapshot.get("profiles", {}).items()
        if number in current and isinstance(links, dict) and bool(links)
    }
    missing = {}
    selected_rows = {}
    for row in communications:
        number = str(row.get("PersonNumber"))
        if number not in current or number in covered:
            continue
        if str(row.get("CommunicationTypeText", "")).lower() not in (
            "homepage", "page d'accueil", "sito internet"
        ):
            continue
        url = homepage_url(row.get("Address"))
        if url and number not in missing:
            missing[number] = {"person_number": number, "name": current[number],
                               "homepage": url}
            selected_rows[number] = row
    cases = sorted(missing.values(), key=lambda item: item["name"].casefold())
    result = {
        "language": language, "snapshot_updated_at": snapshot.get("updated_at"),
        "missing_homepage_count": len(cases), "cases": cases,
        "scan_requested": scan,
        "communication_error": communication_diagnostic.get("error"),
    }
    if scan:
        numbers = {item["person_number"] for item in cases[:30]}
        _, _, report = await homepage_profiles(
            [selected_rows[number] for number in numbers],
            {number: current[number] for number in numbers},
        )
        detail_by_number = {
            str(item["person_number"]): item
            for item in report.get("missing_details", [])
        }
        for item in cases:
            item["scan"] = detail_by_number.get(item["person_number"]) if item["person_number"] in numbers else None
        technical_statuses = {"hostname_not_public", "fetch_error", "http_error_or_not_html"}
        technical_cases = []
        for item in cases:
            detail = item.get("scan") or {}
            status = detail.get("fetch_status", "")
            if status not in technical_statuses and not detail.get("retry_errors"):
                continue
            attempts = detail.get("tls_attempts", [])
            categories = sorted({entry.get("error_category") for entry in attempts
                                 if entry.get("error_category")})
            technical_cases.append({
                "person_number": item["person_number"],
                "name": item["name"],
                "homepage": item["homepage"],
                "fetch_status": status,
                "error_categories": categories,
                "retry_errors": detail.get("retry_errors", []),
                "http_status": detail.get("http_status"),
                "final_url": detail.get("final_url"),
                "redirects": detail.get("redirects", []),
                "tls_attempts": [
                    {
                        "url": attempt.get("url"),
                        "attempt": attempt.get("attempt"),
                        "outcome": attempt.get("outcome"),
                        "http_status": attempt.get("http_status"),
                        "error_category": attempt.get("error_category"),
                        "error_detail": attempt.get("error_detail"),
                    }
                    for attempt in attempts
                ],
                "tls_fallback_decision": detail.get("tls_fallback_decision"),
                "tls_fallbacks": detail.get("tls_fallbacks", []),
                "recommended_action": (
                    "Check official replacement URL and public DNS; do not bypass SSRF checks"
                    if status == "hostname_not_public" or "dns_resolution" in categories else
                    "Check certificate hostname and official HTTPS address; keep TLS verification enabled"
                    if "tls_certificate" in categories else
                    "Inspect HTTP status, content type and redirect destination"
                    if status == "http_error_or_not_html" else
                    "Check site availability and TCP connectivity; retry later"
                ),
            })
        result["technical_cases"] = technical_cases
        result["technical_case_count"] = len(technical_cases)
        result["scanned_count"] = len(numbers)
        result["scan_report"] = {
            "reasons": report.get("reasons", {}),
            "fetch_statuses": report.get("fetch_statuses", {}),
        }
    return result


@app.get("/api/person-communications-diagnostic")
async def person_communications_diagnostic(person_number: int = Query(4100, ge=1)):
    """Compare unfiltered Person results, filtered lookup, and person navigation."""
    base = API.rstrip("/")
    result = {}
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        async def fetch(label, path, params):
            try:
                response = await client.get(f"{base}/{path}", params=params)
                response.raise_for_status()
                payload = response.json()
                data = payload.get("d", payload)
                rows = data.get("results", []) if isinstance(data, dict) else data if isinstance(data, list) else []
                result[label] = {
                    "response_structure": {
                        "root_type": type(payload).__name__,
                        "root_keys": list(payload)[:15] if isinstance(payload, dict) else [],
                        "d_type": type(data).__name__,
                        "d_keys": list(data)[:15] if isinstance(data, dict) else [],
                        "results_type": type(data.get("results")).__name__ if isinstance(data, dict) else None,
                        "results_length": len(data.get("results")) if isinstance(data, dict) and isinstance(data.get("results"), list) else None,
                        "value_type": type(data.get("value")).__name__ if isinstance(data, dict) else None,
                        "value_length": len(data.get("value")) if isinstance(data, dict) and isinstance(data.get("value"), list) else None,
                        "response_bytes": len(response.content),
                        "content_type": response.headers.get("content-type"),
                    },
                    "status": response.status_code,
                    "count": len(rows),
                    "sample": [{"PersonNumber": x.get("PersonNumber"), "Language": x.get("Language"), "ID": x.get("ID"), "navigation_available": bool(isinstance(x.get("PersonCommunications"), dict) and x["PersonCommunications"].get("__deferred", {}).get("uri"))} for x in rows[:3]],
                }
                return rows
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                result[label] = {"error": str(exc)[:250]}
                return []

        await fetch("direct", "PersonCommunication", {"$format": "json", "$top": 2})
        await fetch("person_unfiltered", "Person", {"$format": "json", "$top": 3})
        rows = await fetch("person_filtered", "Person", {"$format": "json", "$filter": f"PersonNumber eq {person_number}", "$top": 3})
        nav_uri = next((x.get("PersonCommunications", {}).get("__deferred", {}).get("uri") for x in rows if isinstance(x.get("PersonCommunications"), dict) and x["PersonCommunications"].get("__deferred", {}).get("uri")), None)
        if nav_uri:
            try:
                response = await client.get(nav_uri, params={"$format": "json"})
                response.raise_for_status()
                payload = response.json()
                data = payload.get("d", payload)
                records = data.get("results", []) if isinstance(data, dict) else []
                result["navigation"] = {
                    "status": response.status_code, "count": len(records),
                    "sample": [{"Language": x.get("Language"), "CommunicationType": x.get("CommunicationType"), "CommunicationTypeText": x.get("CommunicationTypeText"), "address_domain": urlparse(x.get("Address") or "").hostname or ""} for x in records[:10]]
                }
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                result["navigation"] = {"error": str(exc)[:250]}
        else:
            result["navigation"] = {"error": "No navigation URI on filtered Person results"}
    return {"person_number": person_number, "results": result}


@app.get("/api/odata-schema")
async def odata_schema():
    """Inspect actual OData entity sets and person-related fields without guessing names."""
    url = f"{API.rstrip('/')}/$metadata"
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
        root = ET.fromstring(response.content)
        entity_sets = []
        for node in root.iter():
            if node.tag.rsplit("}", 1)[-1] == "EntitySet":
                entity_sets.append({"name": node.attrib.get("Name"), "type": node.attrib.get("EntityType")})
        entity_types = []
        for node in root.iter():
            if node.tag.rsplit("}", 1)[-1] != "EntityType":
                continue
            name = node.attrib.get("Name", "")
            if not any(word in name.lower() for word in ("person", "communicat", "member", "social")):
                continue
            properties = [child.attrib.get("Name") for child in node if child.tag.rsplit("}", 1)[-1] == "Property"]
            navigation = [child.attrib.get("Name") for child in node if child.tag.rsplit("}", 1)[-1] == "NavigationProperty"]
            entity_types.append({"name": name, "properties": properties, "navigation": navigation})
        return {"entity_sets": entity_sets, "relevant_types": entity_types}
    except (httpx.HTTPError, ET.ParseError) as exc:
        logger.warning("OData metadata unavailable: %s", exc)
        raise HTTPException(status_code=502, detail=f"OData-Metadaten nicht abrufbar: {exc}") from exc


