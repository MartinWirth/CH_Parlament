# CH_Parlament

Interaktive Übersicht der Mitglieder von Nationalrat und Ständerat mit Mehrfachauswahl-Filtern für Rat, Partei, Kanton, Fraktion, Sprache und Geschlecht sowie Namenssuche und sortierbarer Tabelle.

## Use Cases und API-Endpunkte

Eine vollständige Übersicht der Anwendungsfälle, HTTP-Endpunkte, Parameter, Berechtigungen und typischen Abläufe steht in [docs/use-cases-and-endpoints.md](docs/use-cases-and-endpoints.md). Die automatisch generierte FastAPI-Referenz ist nach dem Start unter `/docs` verfügbar.

## Start

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
# .env.example nach .env kopieren und bei Bedarf anpassen
uvicorn app.main:app --reload
```

Danach http://127.0.0.1:8000 öffnen.

Tests: `pytest -q`

## Datenquelle

Offizielle OData-Schnittstelle der Schweizer Parlamentsdienste: `https://ws.parlament.ch/odata.svc/MemberCouncil`. Der Endpoint ist mit `PARLIAMENT_API` konfigurierbar. Die App liest die Daten beim Laden neu; die Filterung erfolgt im Browser. Bei API-Fehlern wird eine Fehlermeldung statt erfundener Beispieldaten angezeigt.

Quelle: **Parlamentsdienste der Bundesversammlung, Bern**. Der Zeitpunkt des Datenabrufs wird angezeigt. Dies ist **keine amtliche Veröffentlichung**.

Hinweis: Das Datenmodell des offiziellen Dienstes kann sich ändern; die Feldzuordnung in `app/main.py` ist bewusst zentralisiert und muss gegen die Live-Antwort validiert werden.


### Verified party profile sources

Optional `party_profile_sources.json` provides a curated list of **person-specific** official party biography pages. The file starts as `[]`: no party URLs are guessed or scraped from search results. Example entry (replace placeholders with reviewed real data):

```json
[
  {
    "person_number": "PERSON_NUMBER",
    "name": "Exact Firstname Lastname from /api/members",
    "profile_url": "https://party.example/person-specific-biography",
    "verified": true
  }
]
```

Only set `verified: true` after manually confirming the official page identifies the specific parliamentarian. Entries must match an active parliamentarian's ID and full name; invalid entries are ignored and counted in `debug.party_sources`. The scanner reads links and structured metadata on the reviewed page; it does not associate generic party social accounts with a person. After editing the registry, click **Social-Media-Profile aktualisieren**; to rescan a previously cached page within 24 hours, delete `.social_profiles_cache.json` while the server is stopped. The `PARTY_PROFILE_SOURCES` environment variable can override the registry path.
