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
import unicodedata
from html.parser import HTMLParser
from urllib.parse import urljoin, parse_qs
import xml.etree.ElementTree as ET
from urllib.parse import urlparse, urlunparse
import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("ch_parlament")
app = FastAPI(title="CH_Parlament")
ROOT = Path(__file__).resolve().parent
API = os.getenv("PARLIAMENT_API", "https://ws.parlament.ch/odata.svc")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


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
            logger.info("API parsed page=%d rows=%d keys=%s", len(seen), len(page), list(page[0].keys()) if page else [])
            rows.extend(page)
            url = str(httpx.URL(url).join(next_url)) if next_url else None
    logger.info("API fetch completed rows=%d duration_ms=%d", len(rows), round((time.monotonic()-started)*1000))
    return rows


async def fetch_communications(language, person_numbers=None):
    """Page through communications, retaining only records for current members."""
    url = f"{API.rstrip('/')}/PersonCommunication"
    rows, seen = [], set()
    page_size, max_pages = 1000, 20
    diagnostic = {"http_status": None, "raw_count": 0, "languages": {}, "communication_types": {}, "error": None, "pages": 0, "truncated": False, "matched_members": 0}
    allowed = {str(n) for n in person_numbers} if person_numbers is not None else None
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            for page_number in range(max_pages):
                params = {"$format": "json", "$top": page_size, "$skip": page_number * page_size}
                response = await client.get(url, params=params)
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
                    lang = str(row.get("Language", ""))
                    kind = str(row.get("CommunicationTypeText") or row.get("CommunicationType") or "")
                    diagnostic["languages"][lang] = diagnostic["languages"].get(lang, 0) + 1
                    diagnostic["communication_types"][kind] = diagnostic["communication_types"].get(kind, 0) + 1
                    if lang.upper() == language and (allowed is None or str(row.get("PersonNumber")) in allowed):
                        rows.append(row)
                if len(page) < page_size:
                    break
            else:
                diagnostic["truncated"] = True
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
        if tag == "a" and attrs.get("href"):
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
        if self._json_ld:
            self._json_chunks.append(data)

    def handle_endtag(self, tag):
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


async def scan_homepage(client, url, semaphore, diagnostic=None):
    now = time.time()
    cached = _homepage_cache.get(url)
    if cached and now - cached[0] < _homepage_cache_ttl and diagnostic is None:
        return cached[1]
    async with semaphore:
        found = {}
        if diagnostic is not None:
            diagnostic.update({"status": "pending", "http_status": None, "final_url": url, "redirects": [], "cache_hit": bool(cached and now - cached[0] < _homepage_cache_ttl)})
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
                        response = await client.get(current, follow_redirects=False)
                        break
                    except httpx.ConnectError as exc:
                        if diagnostic is not None:
                            diagnostic.setdefault("retry_errors", []).append(type(exc).__name__)
                        detail = str(exc).lower()
                        certificate_error = "certificate" in detail or "hostname mismatch" in detail
                        parsed_current = urlparse(current)
                        host = parsed_current.hostname or ""
                        # Jauslin's independently verified personal site is on a
                        # different domain. Use it only after a TLS failure.
                        verified_alternate = (
                            "www.mjauslin.ch"
                            if original.hostname in ("jauslin.com", "www.jauslin.com")
                            and host in ("jauslin.com", "www.jauslin.com")
                            else None
                        )
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
                    diagnostic["raw_social_urls"] = raw_social[:40]
                    diagnostic["raw_social_count"] = len(raw_social)
                if diagnostic is not None:
                    diagnostic["html_bytes"] = len(content)
                    diagnostic["parser"] = dict(parser.link_diagnostics)
                    diagnostic["parser"]["candidate_urls"] = len(parser.links) + len(list(parser.social_references()))
                candidate_links = list(parser.links) + list(parser.social_references())
                candidate_links += raw_social
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
    # Inspect raw social URLs as well: many personal sites embed their links in
    # script data rather than anchors. Accept extras only with a surname match.
    diagnostics = [{} for _ in homepages]
    async with httpx.AsyncClient(timeout=8, trust_env=False, headers={"User-Agent": "CH-Parlament/1.0"}) as client:
        results = await asyncio.gather(*(scan_homepage(client, url, semaphore, diagnostics[i]) for i, url in enumerate(homepages.values())))
    enriched = {}
    for (number, _), links, diagnostic in zip(homepages.items(), results, diagnostics):
        combined = dict(links)
        for candidate in diagnostic.get("raw_social_urls", []):
            try:
                platform = verified_social_url(candidate)
            except (ValueError, UnicodeError):
                continue
            if platform and platform not in combined and personal_handle_matches(candidate, (member_names or {}).get(number, "")):
                combined[platform] = candidate
        if combined:
            enriched[number] = combined
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
            "error_detail": diagnostic.get("error_detail"),
        "error_category": diagnostic.get("error_category"),
            "ssl_hint": diagnostic.get("ssl_hint"),
            "error_stage_url": diagnostic.get("error_stage_url"),
            "redirect_reason": diagnostic.get("redirect_reason"),
            "redirect_target_host": diagnostic.get("redirect_target_host"),
            "redirect_chain": diagnostic.get("redirects", []),
            "candidate_count": len(candidates),
            "candidate_samples": candidates[:5],
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
            response = await client.get(roster_url)
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


async def party_profile_links(member_rows):
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
    if not sources:
        diagnostic["pages"] = []
        return {}, diagnostic
    semaphore = asyncio.Semaphore(8)
    members = {str(pick(row, "PersonNumber")): normalize(row)["name"] for row in member_rows if is_current(row)}
    def ascii_key(value):
        return "".join(ch for ch in unicodedata.normalize("NFKD", value.casefold()) if ch.isascii() and ch.isalnum())
    async with httpx.AsyncClient(timeout=5, trust_env=False, headers={"User-Agent": "CH-Parlament/1.0"}) as client:
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
    snapshot = read_profile_snapshot().get(language, {})
    return {"profiles": snapshot.get("profiles", {}), "profiles_by_source": snapshot.get("profiles_by_source", {}), "updated_at": snapshot.get("updated_at"), "cached": True, "debug": snapshot.get("debug", {})}

@app.get("/api/social-profiles/refresh")
async def social_profiles_refresh(language: str = Query("DE", pattern="^(DE|FR|IT|RM)$")):
    """Refresh profiles independently of the immediate cached response."""
    rows = await fetch_members(language)
    ids = {str(pick(row, "PersonNumber")) for row in rows if is_current(row)}
    communications, diagnostic = await fetch_communications(language, ids)
    profiles, homepages, homepage_report = await homepage_profiles(communications, {str(pick(row, "PersonNumber")): normalize(row)["name"] for row in rows if is_current(row)})
    personal_profiles = {number: dict(links) for number, links in profiles.items()}
    parliament_profiles = communication_profiles(communications)
    party_links, party_diagnostic = await party_profile_links(rows)
    previous = read_profile_snapshot().get(language, {})
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
    data = {"profiles": profiles, "profiles_by_source": by_source, "updated_at": datetime.now(timezone.utc).isoformat(), "cached": False, "debug": {"party_sources": party_diagnostic, "party_members_added": party_added, "previous_links_retained": retained, "coverage": missing, "platforms": platforms, "homepages_checked": homepages, "homepage_diagnostics": homepage_report, "homepage_profiles": len(profiles), "communication_count": len(communications), "communication_error": diagnostic.get("error")}}
    snapshot = read_profile_snapshot()
    snapshot[language] = data
    save_profile_snapshot(snapshot)
    logger.info("Social refresh complete homepages=%d profiles=%d", homepages, len(profiles))
    return data


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
