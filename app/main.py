from datetime import datetime, timezone
from pathlib import Path
import os
import logging
import time
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


async def fetch_communications(language):
    """Fetch communication records and return diagnostic counts without exposing addresses."""
    url = f"{API.rstrip('/')}/PersonCommunication"
    rows, seen = [], set()
    diagnostic = {"http_status": None, "raw_count": 0, "languages": {}, "communication_types": {}, "error": None}
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            while url and len(seen) < 30:
                if url in seen:
                    break
                seen.add(url)
                response = await client.get(url, params={"$format": "json", "$top": 1000} if len(seen) == 1 else None)
                diagnostic["http_status"] = response.status_code
                response.raise_for_status()
                payload = response.json()
                data = payload.get("d", payload)
                page = data.get("results", []) if isinstance(data, dict) and "results" in data else data.get("value", []) if isinstance(data, dict) else []
                rows.extend(page)
                next_url = (data.get("__next") or data.get("@odata.nextLink")) if isinstance(data, dict) else None
                url = str(httpx.URL(url).join(next_url)) if next_url else None
        diagnostic["raw_count"] = len(rows)
        for row in rows:
            lang = str(row.get("Language", ""))
            kind = str(row.get("CommunicationTypeText") or row.get("CommunicationType") or "")
            diagnostic["languages"][lang] = diagnostic["languages"].get(lang, 0) + 1
            diagnostic["communication_types"][kind] = diagnostic["communication_types"].get(kind, 0) + 1
        selected = [row for row in rows if str(row.get("Language", "")).upper() == language]
        logger.info("PersonCommunication diagnostic=%s filtered=%d", diagnostic, len(selected))
        return selected, diagnostic
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        diagnostic["error"] = str(exc)[:300]
        logger.warning("PersonCommunication diagnostic=%s", diagnostic)
        return [], diagnostic

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
    communications, communication_debug = await fetch_communications(language)
    profiles = communication_profiles(communications)
    result = [normalize(row) for row in rows if is_current(row)]
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
