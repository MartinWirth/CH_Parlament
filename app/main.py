from datetime import datetime, timezone
from pathlib import Path
import os
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="CH_Parlament")
ROOT = Path(__file__).resolve().parent
API = os.getenv("PARLIAMENT_API", "https://ws.parlament.ch/odata.svc")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


def pick(row, *keys):
    for key in keys:
        if row.get(key) is not None:
            return row[key]
    return ""


def normalize(row):
    first = pick(row, "FirstName", "FirstNameDe")
    last = pick(row, "LastName", "LastNameDe")
    name = pick(row, "FullName", "PersonName") or f"{first} {last}".strip()
    return {
        "id": str(pick(row, "PersonNumber", "PersonId", "Id")),
        "name": name,
        "rat": str(pick(row, "CouncilName", "CouncilNameDe", "CouncilAbbreviation", "Council")),
        "partei": str(pick(row, "PartyAbbreviation", "PartyName", "PartyNameDe", "Party")),
        "kanton": str(pick(row, "CantonName", "CantonNameDe", "CantonAbbreviation", "Canton")),
        "fraktion": str(pick(row, "ParlGroupName", "ParlGroupNameDe", "ParlGroupAbbreviation", "ParlGroup")),
        "sprache": str(pick(row, "Language", "LanguageCode", "LanguageName")),
        "geschlecht": str(pick(row, "Gender", "Sex")),
    }


def is_current(row):
    # Official MemberCouncil records expose membership status or mandate end.
    active = pick(row, "Active", "IsActive")
    if active != "":
        return active is True or str(active).lower() in ("true", "1")
    end = pick(row, "DateLeaving", "DateTo", "EndDate", "MembershipEndDate")
    if end:
        try:
            return str(end)[:10] >= datetime.now(timezone.utc).date().isoformat()
        except (ValueError, TypeError):
            return False
    return True


async def fetch_members():
    rows, url, seen = [], f"{API.rstrip('/')}/MemberCouncil", set()
    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
        while url:
            if url in seen or len(seen) >= 30:
                raise ValueError("API pagination loop or limit exceeded")
            seen.add(url)
            response = await client.get(url, params={"$format": "json", "$top": 1000} if len(seen) == 1 else None)
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict) and "d" in payload:
                data = payload["d"]
                page = data.get("results", []) if isinstance(data, dict) else data
                next_url = data.get("__next") if isinstance(data, dict) else None
            else:
                page = payload.get("value", []) if isinstance(payload, dict) else []
                next_url = payload.get("@odata.nextLink") if isinstance(payload, dict) else None
            rows.extend(page)
            url = str(httpx.URL(url).join(next_url)) if next_url else None
    return rows


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/members")
async def members():
    try:
        rows = await fetch_members()
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=502, detail=f"Parlamentsdienst nicht erreichbar: {exc}") from exc
    result = [normalize(row) for row in rows if is_current(row)]
    # One member can appear in several records; preserve distinct council memberships.
    unique = {(item["id"], item["rat"]): item for item in result if item["name"]}
    return {
        "source": "Parlamentsdienste der Bundesversammlung, Bern",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "members": sorted(unique.values(), key=lambda m: m["name"].casefold()),
    }
