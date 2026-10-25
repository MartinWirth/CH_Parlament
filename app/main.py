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
from html.parser import HTMLParser
from urllib.parse import urljoin
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
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
        self._excluded_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("footer", "nav") or (tag in ("div", "section") and any(word in (attrs.get("class", "")+" "+attrs.get("id", "")).lower().split() for word in ("footer", "site-footer", "main-footer"))):
            self._excluded_depth += 1
        if self._excluded_depth:
            return
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        elif tag == "link" and "me" in attrs.get("rel", "").lower().split() and attrs.get("href"):
            self.links.append(attrs["href"])
        elif tag == "meta" and attrs.get("property", "").lower() in ("og:see_also", "article:author") and attrs.get("content"):
            self.links.append(attrs["content"])
        elif tag == "script" and attrs.get("type", "").lower() == "application/ld+json":
            self._json_ld = True
            self._json_chunks = []

    def handle_data(self, data):
        if self._json_ld:
            self._json_chunks.append(data)

    def handle_endtag(self, tag):
        if self._excluded_depth and tag in ("footer", "nav", "div", "section"):
            self._excluded_depth -= 1
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
    return value


def public_hostname(host):
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(info[4][0]).is_global for info in addresses)
    except (OSError, ValueError):
        return False


async def scan_homepage(client, url, semaphore):
    now = time.time()
    cached = _homepage_cache.get(url)
    if cached and now - cached[0] < _homepage_cache_ttl:
        return cached[1]
    async with semaphore:
        found = {}
        try:
            original = urlparse(url)
            if not await asyncio.to_thread(public_hostname, original.hostname):
                return found
            current = url
            for _ in range(3):
                async with client.stream("GET", current, follow_redirects=False) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        target = urljoin(current, response.headers.get("location", ""))
                        parsed = urlparse(target)
                        if parsed.scheme != "https" or parsed.hostname != original.hostname or parsed.port not in (None, 443) or parsed.username or parsed.password:
                            return found
                        current = target
                        continue
                    if response.status_code != 200 or "text/html" not in response.headers.get("content-type", "").lower():
                        return found
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 300_000:
                            return found
                parser = SocialLinkParser()
                parser.feed(content.decode("utf-8", errors="replace"))
                for href in list(parser.links) + list(parser.social_references()):
                    target = urljoin(current, href)
                    platform = verified_social_url(target)
                    if platform:
                        found.setdefault(platform, target)
                break
        except (httpx.HTTPError, ValueError, UnicodeError):
            pass
        _homepage_cache[url] = (time.time(), found)
        return found


async def homepage_profiles(communications):
    homepages = {}
    for row in communications:
        if str(row.get("CommunicationTypeText", "")).lower() not in ("homepage", "page d'accueil", "sito internet"):
            continue
        url = homepage_url(row.get("Address"))
        if url:
            homepages.setdefault(str(row.get("PersonNumber")), url)
    semaphore = asyncio.Semaphore(8)
    async with httpx.AsyncClient(timeout=5, trust_env=False, headers={"User-Agent": "CH-Parlament/1.0"}) as client:
        tasks = [scan_homepage(client, url, semaphore) for url in homepages.values()]
        results = await asyncio.gather(*tasks)
    save_homepage_cache()
    return {number: links for number, links in zip(homepages, results) if links}, len(homepages)


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


async def party_profile_links(member_rows):
    sources, diagnostic = configured_party_profiles(member_rows)
    if not sources:
        return {}, diagnostic
    semaphore = asyncio.Semaphore(8)
    async with httpx.AsyncClient(timeout=5, trust_env=False, headers={"User-Agent": "CH-Parlament/1.0"}) as client:
        results = await asyncio.gather(*(scan_homepage(client, url, semaphore) for url in sources.values()))
    save_homepage_cache()
    return {number: links for number, links in zip(sources, results) if links}, diagnostic


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
    profiles, homepages = await homepage_profiles(communications)
    personal_profiles = {number: dict(links) for number, links in profiles.items()}
    parliament_profiles = communication_profiles(communications)
    party_links, party_diagnostic = await party_profile_links(rows)
    party_added = 0
    for number, links in party_links.items():
        existing = profiles.setdefault(number, {})
        if not existing:
            party_added += 1
        for platform, url in links.items():
            existing.setdefault(platform, url)
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
    data = {"profiles": profiles, "profiles_by_source": by_source, "updated_at": datetime.now(timezone.utc).isoformat(), "cached": False, "debug": {"party_sources": party_diagnostic, "party_members_added": party_added, "coverage": missing, "platforms": platforms, "homepages_checked": homepages, "homepage_profiles": len(profiles), "communication_count": len(communications), "communication_error": diagnostic.get("error")}}
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
