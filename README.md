# CH_Parlament

Interaktive Übersicht der Mitglieder von Nationalrat und Ständerat mit Mehrfachauswahl-Filtern für Rat, Partei, Kanton, Fraktion, Sprache und Geschlecht sowie Namenssuche und sortierbarer Tabelle.

## Start

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Danach http://127.0.0.1:8000 öffnen.

Tests: `pytest -q`

## Datenquelle

Offizielle OData-Schnittstelle der Schweizer Parlamentsdienste: `https://ws.parlament.ch/odata.svc/MemberCouncil`. Der Endpoint ist mit `PARLIAMENT_API` konfigurierbar. Die App liest die Daten beim Laden neu; die Filterung erfolgt im Browser. Bei API-Fehlern wird eine Fehlermeldung statt erfundener Beispieldaten angezeigt.

Quelle: **Parlamentsdienste der Bundesversammlung, Bern**. Der Zeitpunkt des Datenabrufs wird angezeigt. Dies ist **keine amtliche Veröffentlichung**.

Hinweis: Das Datenmodell des offiziellen Dienstes kann sich ändern; die Feldzuordnung in `app/main.py` ist bewusst zentralisiert und muss gegen die Live-Antwort validiert werden.
