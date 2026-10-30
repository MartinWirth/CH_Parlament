# CH_Parlament – Use Cases und HTTP-Endpunkte

Stand: Code in `app/main.py` und `app/admin.py`. Die CI prüft mit `tests/test_endpoint_documentation.py`, dass die nachstehenden HTTP-Methoden und Pfade vollständig und ohne veraltete Einträge den registrierten FastAPI-Routen entsprechen. Bei neuen Endpunkten müssen diese Tabellen im selben Pull Request aktualisiert werden. Die API ist eine FastAPI-Anwendung; interaktive OpenAPI-Dokumentation ist bei Standardkonfiguration unter `/docs` und das Schema unter `/openapi.json` verfügbar.

## Rollen und zentrale Anwendungsfälle

| Rolle | Use Case | Einstieg / Ergebnis |
|---|---|---|
| Besucher | Aktuelle Mitglieder von National- und Ständerat ansehen, nach Name, Rat, Partei, Kanton, Fraktion, Sprache und Geschlecht filtern und sortieren | `/` und `GET /api/members` |
| Besucher | Persönliche Social-Media-Profile anzeigen, ohne bei jedem Aufruf externe Webseiten zu scannen | `GET /api/social-profiles` |
| Betreiber | Aktuelle Parlamentsdaten und PersonCommunication-Daten einlesen | `GET /api/members` |
| Betreiber | Social-Media-Profile aus offiziellen Kommunikationsdaten, persönlichen Homepages und Partei-Personenseiten recherchieren | `POST /api/social-profiles/refresh` oder Admin-Refresh |
| Betreiber | Homepage-Abdeckung und technische Scan-Probleme untersuchen | `GET /api/social-profiles/missing-homepage-diagnostic` |
| Entwickler | PersonCommunication-Datenpfade und OData-Schema untersuchen | `GET /api/person-communications-diagnostic`, `GET /api/odata-schema` |
| Administrator | Anmelden, abmelden, Refresh starten und Jobstatus prüfen | `/admin`, `/admin/login`, `/admin/logout`, `/admin/refresh`, `/admin/status` |
| Administrator | Nicht eindeutig zugeordnete Social-Media-Kandidaten prüfen und entscheiden | `GET/POST /admin/review` |
| Administrator | Fehlende persönliche Profile nach Plattform priorisieren | `GET /admin/social-discovery/worklist` |
| Administrator | Verifizierte Profile auf Format, Duplikate und Qualität prüfen | `GET /admin/quality` |
| Administrator | Erreichbarkeit von Profil-URLs prüfen, Verlauf und Zeitplan einsehen | `/admin/live-check*` |
| Administrator | Auffällige Links sichten, triagieren, exportieren und archivieren | `/admin/live-check/alerts*`, `/admin/live-check/archive*` |
| Administrator | Facebook-Legacy-Fälle überprüfen | `/admin/live-check/facebook-legacy*` |
| Administrator | Datenqualität, historische Trends und Regressionen überwachen | `/admin/data-health*` |

## Authentifizierung und Betriebsregeln

- Öffentliche `/api/*`-GET-Endpunkte sind ohne Admin-Login erreichbar; Diagnoseendpunkte können externe Daten abrufen. Der Homepage-Diagnose-Endpunkt kann mit `scan=true` bis zu 30 bisher unversorgte Homepages prüfen, sofern der externe Scan aktiviert ist.
- `POST /api/social-profiles/refresh` benötigt den Header `X-Refresh-Token: <SOCIAL_REFRESH_TOKEN>`. Ohne konfiguriertes Token: HTTP 503; bei falschem Token: HTTP 403; paralleler Refresh: HTTP 409.
- Admin-Login benötigt `ADMIN_REFRESH_PASSWORD` **und** `SOCIAL_REFRESH_TOKEN`. Die Anmeldung setzt ein `Secure`, `HttpOnly`, `SameSite=Strict`-Cookie `chp_admin` mit einer einstündigen serverseitigen Sitzung. In einer reinen HTTP-Lokalentwicklung wird das Secure-Cookie möglicherweise nicht übertragen.
- Alle geschützten `/admin/*`-Datenendpunkte benötigen die Admin-Sitzung (sonst HTTP 401). Zustandsändernde Admin-POSTs erfordern zusätzlich einen zum Request-Host passenden `Origin`-Header und `X-Admin-Action: 1` (sonst HTTP 403).
- `language` ist, soweit angeboten, `DE`, `FR`, `IT` oder `RM`; Standard ist `DE`. Falsche Werte führen zu HTTP 422.
- Ein Social-Media-Link aus einer regionalen Parteiseite ist **nur ein Prüfkandidat**, keine automatisch bestätigte Personenzuordnung. Externe Homepage-Scans sind standardmässig deaktiviert; nur `ENABLE_EXTERNAL_HOMEPAGE_SCAN=1` aktiviert sie. `social_regional_party_sources.json` enthält die konfigurierten Quellen.
- Refresh- und Live-Check-Jobs laufen asynchron; den Fortschritt über die Status-Endpunkte abfragen. Daten werden lokal in konfigurierbaren Snapshot-, Cache-, Review- und History-Dateien gespeichert.

## Öffentliche Endpunkte

| Methode | Pfad | Parameter / Body | Zweck |
|---|---|---|---|
| GET | `/` | – | Öffentliche HTML-Anwendung |
| GET | `/api/members` | `language=DE` | Aktuelle Mitglieder, Kommunikationsprofile, Quelle, Abrufzeit und Debug-Informationen |
| GET | `/api/social-profiles` | `language=DE` | Gespeicherte Profile, Quellen, Evidenz, Prüfkandidaten, Snapshot-Zeitpunkt; kein neuer Scan |
| POST | `/api/social-profiles/refresh` | `language=DE`, `force=false`; Header `X-Refresh-Token` | Autorisierter synchroner Refresh und Snapshot-Speicherung |
| GET | `/api/social-profiles/missing-homepage-diagnostic` | `language=DE`, `scan=false` | Fehlende Homepage-Abdeckung; optional bis zu 30 Scans und technische Diagnose |
| GET | `/api/person-communications-diagnostic` | `person_number=4100` (Integer ≥ 1) | Vergleicht OData-Abfragewege für eine Person |
| GET | `/api/odata-schema` | – | Metadaten und relevante Felder des Parlaments-OData-Dienstes |
| GET | `/docs` | – | FastAPI Swagger UI (sofern nicht deaktiviert) |
| GET | `/redoc` | – | FastAPI ReDoc (sofern nicht deaktiviert) |
| GET | `/docs/oauth2-redirect` | – | FastAPI Swagger UI OAuth2-Redirect-Hilfsseite |
| GET | `/openapi.json` | – | Generiertes OpenAPI-Schema |

## Admin-Endpunkte

Alle folgenden Pfade liegen unter `/admin`. `GET /admin` liefert die Login-/Administrationsoberfläche, ohne bereits eine Sitzung vorauszusetzen.

| Methode | Pfad | Parameter / Body | Zweck |
|---|---|---|---|
| GET | `/admin` | – | Admin-HTML |
| POST | `/admin/login` | JSON `{"password":"..."}` | Sitzung eröffnen; Login-Drosselung nach fünf Fehlversuchen innerhalb von fünf Minuten |
| POST | `/admin/logout` | – | Sitzung beenden |
| GET | `/admin/status` | – | Status des Refresh-Jobs |
| POST | `/admin/refresh` | JSON `{"language":"DE","force":false}` | Hintergrund-Refresh starten |
| GET | `/admin/review` | `language=DE` | Ungeprüfte/zu prüfende Social-Profile aus Snapshot |
| GET | `/admin/targeted-research` | `language=DE`, `limit=100` (1–500) | Geschützte Nachrecherche-Aufträge für Mitglieder ohne bestätigte Profile; nur gespeicherte Daten |
| GET | `/admin/discovery-report` | `language=DE`, `limit=100` (1–500) | Geschützter Bericht über Kandidaten, Fundstellen, Abdeckung und Identitätskonflikte aus dem gespeicherten Snapshot |
| POST | `/admin/review` | JSON mit exakt `language`, `person_number`, `url`, `decision` | Review-Entscheidung für vorhandenen Kandidaten speichern |
| GET | `/admin/targeted-ui.js` | – | Öffentliches JavaScript der Nachrecherche-Anzeige; API-Daten erfordern Admin-Anmeldung |
| GET | `/admin/discovery-ui.js` | – | Statisches JavaScript für die Recherche-Abdeckung; öffentlich wie die Admin-Seite |
| GET | `/admin/review-ui.js` | – | Statische JavaScript-Datei für Review-Oberfläche; öffentlich |
| GET | `/admin/quality` | `language=DE` | Audit publizierter Links auf Format und Duplikate |
| POST | `/admin/live-check` | JSON `{"language":"DE"}` | Erreichbarkeitsprüfung als Hintergrundjob starten |
| GET | `/admin/live-check` | – | Live-Check-Jobstatus |
| GET | `/admin/live-check/history` | `language=DE` | Letzte zehn gespeicherte Prüfungen |
| GET | `/admin/live-check/schedule` | – | Zeitplan, Intervalle, Sprachen und letzte Ausführung |
| GET | `/admin/live-check/alerts` | `language`, `search`, `platform`, `severity`, `state`, `date_from`, `date_to`, `new_only` | Auffälligkeiten filtern |
| POST | `/admin/live-check/alerts/triage` | JSON gemäss OpenAPI | Alarmstatus / Triage aktualisieren |
| GET | `/admin/live-check/alerts/events` | `language`, `person_number`, `platform`, `url` (erforderlich) | Ereignisverlauf zu einem Link |
| GET | `/admin/live-check/metrics` | `language=DE`, `limit=10` | Alarm-Kennzahlen |
| GET | `/admin/live-check/alerts/export.csv` | Dieselben Filter wie `/alerts` | Gefilterte Alarme als CSV |
| GET | `/admin/live-check/metrics/export.csv` | `language=DE`, `limit=10` | Kennzahlen als CSV |
| GET | `/admin/live-check/archive` | `language=DE`, `limit=30`, `search`, `platform`, `severity`, `date_from`, `date_to`, `new_only` | Archivierte Alarmfälle |
| GET | `/admin/live-check/archive/export.csv` | Dieselben Archivfilter | Archiv als CSV |
| GET | `/admin/live-check/facebook-legacy` | `language=DE` | Legacy-Facebook-Kandidaten |
| POST | `/admin/live-check/facebook-legacy/review` | JSON gemäss OpenAPI | Legacy-Facebook-Entscheidung |
| GET | `/admin/data-health` | `language=DE`, `stale_hours=48` | Aktualität und Datenqualität |
| GET | `/admin/data-health/trends` | `language=DE`, `limit=10` | Historische Qualitätskennzahlen |
| GET | `/admin/data-health/regressions` | `language=DE`, `limit=10`, `reachability_drop=10`, `flagged_rise=10`, `duplicate_increase=1`, `invalid_increase=1` | Verschlechterungen gegenüber früheren Snapshots |
| GET | `/admin/social-discovery/worklist` | `language=DE`, `limit=50`, `platform=all` | Liste fehlender persönlicher Profile mit Recherchehilfen |

## Typische Abläufe

### A. Mitgliederdaten anzeigen

1. Browser öffnet `/`.
2. Frontend ruft `GET /api/members?language=DE` auf.
3. Nutzer filtert und sortiert die zurückgegebenen Mitglieder im Browser.
4. Für zusätzlich gespeicherte Social-Profile ruft das Frontend `GET /api/social-profiles?language=DE` auf.

### B. Social-Media-Profile aktualisieren und verifizieren

1. Betreiber konfiguriert `SOCIAL_REFRESH_TOKEN` und optional `ADMIN_REFRESH_PASSWORD`, Quellen und externe Scans.
2. Admin meldet sich über `POST /admin/login` an.
3. `POST /admin/refresh` startet die Recherche; `GET /admin/status` meldet den Fortschritt. Alternativ steht der tokenbasierte öffentliche Refresh-Endpunkt bereit.
4. Quellen sind Parlamentskommunikation, offizielle Personen-Homepages, Partei-Personenseiten und konfigurierte regionale Quellen. Fehlende oder zweifelhafte Links bleiben Prüfkandidaten.
5. `GET /admin/review` liefert die Kandidaten; `POST /admin/review` speichert eine manuelle Entscheidung.
6. `GET /api/social-profiles` zeigt den gespeicherten Zustand inklusive angewandter Review-Entscheidungen.

### C. Qualität und Erreichbarkeit überwachen

1. `GET /admin/quality` zeigt die strukturelle Qualität.
2. `POST /admin/live-check` startet die URL-Prüfung; `GET /admin/live-check` liefert den Jobstatus.
3. `/admin/live-check/history`, `/alerts`, `/metrics` und `/archive` zeigen Verlauf, Auffälligkeiten und Kennzahlen.
4. Admin triagiert Auffälligkeiten über `POST /admin/live-check/alerts/triage`; CSV-Exporte unterstützen die Auswertung.
5. `/admin/data-health*` zeigt Aktualität und Regressionen.

### D. Fehlende Profile gezielt ergänzen

1. `GET /admin/social-discovery/worklist?platform=all` zeigt Lücken nach Person und Plattform.
2. Admin sucht anhand offizieller Quellen nach konkreten persönlichen Accounts.
3. Neue Quellen werden in den kuratierten Dateien hinterlegt und beim nächsten Refresh geprüft.
4. Nicht eindeutig zuordenbare Treffer werden nur nach manueller Identitätsprüfung freigegeben.

## Fehlerbilder und Grenzen

- `401`: Admin-Sitzung fehlt oder ist abgelaufen.
- `403`: falsches Token/Passwort oder fehlende Same-Origin-Anforderung bei Admin-POST.
- `409`: gleichzeitiger Refresh oder bereits laufender Live-Check.
- `422`: ungültige Sprache, Parameter oder Review-Entscheidung.
- `429`: Login-Versuche vorübergehend begrenzt.
- `502`: externer Parlamentsdienst nicht erreichbar (je nach Endpoint).
- `503`: benötigte Admin-/Refresh-Secrets nicht konfiguriert.
- Netzwerkzugriffe auf externe Homepages sind begrenzt und sicherheitsgeprüft; ein Kandidat beweist keine persönliche Kontoinhaberschaft. Ein Scan kann wegen DNS, TLS, HTTP-Status, Weiterleitung oder Seitendarstellung scheitern.
- Die API liefert keine garantiert vollständige Sammlung persönlicher Social-Media-Profile. Die Datenqualität hängt von aktuellen offiziellen Quellen und manueller Prüfung ab.

### Automatische gezielte Nachrecherche (Refresh)

Beim bestehenden Social-Profil-Refresh werden bis zu 30 bereits aus Parlamentsdaten bekannte HTTPS-Homepages von Mitgliedern ohne bestätigte Profile erneut mit dem bestehenden SSRF-geschützten Scanner untersucht (maximal vier parallele Anfragen). Gefundene Social-Media-Links werden ausschliesslich als unbestätigte Kandidaten mit Quellen-URL, Prüfzeitpunkt und `auto_accept=false` in die Review-Queue übernommen. Es werden keine unbekannten Domains geraten, keine Suchmaschinen automatisch abgefragt und keine Accounts ohne manuelle Identitätsprüfung publiziert. Der Diagnosebericht enthält `targeted_homepage_discovery`.

### Zusätzliche verifizierte Partei-Mitgliederseiten

Ein Eintrag in `social_regional_party_sources.json` darf optional `additional_profile_urls` (Liste mit maximal drei zusätzlichen HTTPS-Seiten) enthalten. Diese werden nur für Ratsmitglieder ohne bestätigte Social-Media-Profile untersucht. Jede URL muss zum exakt gleichen Host der zuvor manuell verifizierten offiziellen Partei-Mitgliederseite gehören; fremde Hosts, unsichere Protokolle und URLs mit Zugangsdaten werden verworfen. Die Erweiterung verwendet weiterhin den SSRF-geschützten Scanner und übergibt Funde ausschliesslich mit Quellenbeleg und `auto_accept=false` an die manuelle Review-Queue. `regional_party_discovery.explicit_additional_pages` zählt die geprüften Zusatzseiten. Ohne konfigurierte Zusatzseiten erfolgt keine zusätzliche Netzabfrage.

### Automatische Erkennung offizieller Mitglieder-Unterseiten

Aus bereits verifizierten Partei-Mitgliederseiten werden relevante interne Links automatisch erkannt. Der Scanner folgt maximal zwei neuen HTTPS-Unterseiten pro Mitglied auf demselben Host, priorisiert Personen ohne bestätigte Social-Profile, dedupliziert gegen explizit konfigurierte Zusatzseiten und begrenzt die Gesamtzahl der Unterseiten-Requests auf 200. Die Diagnostik meldet `regional_party_discovery.auto_discovered_subpages`. Entdeckte Social-Links gelangen nur in die manuelle Review-Queue; es erfolgt keine automatische Identitätsbestätigung.

### Kantonale Parteiverzeichnisse

`SOCIAL_CANTONAL_DIRECTORY_SOURCES_PATH` (Standard `social_cantonal_party_directories.json`) definiert manuell verifizierte offizielle kantonale Mitgliederverzeichnisse. Maximal 20 Verzeichnisse und 40 daraus eindeutig anhand des vollständigen Namens im URL-Slug zugeordnete Mitgliederseiten werden über den bestehenden HTTPS-/SSRF-geschützten Scanner geprüft. Unbestätigte Social-Links erscheinen ausschliesslich in der manuellen Review-Queue mit `source_type=verified_cantonal_party_directory`, Quellen-URL und `auto_accept=false`. Der Diagnosebericht enthält `cantonal_directory_discovery`. Die URL-Namensübereinstimmung ist nur ein Recherchehinweis und kein Identitätsnachweis.

### Kantonale Verzeichnisse mit abweichenden URL-Strukturen

Der HTML-Scanner stellt bis zu 300 Verzeichnis-Anker mit absoluter URL und Linktext als Diagnose bereit. Die kantonale Recherche akzeptiert neben exakten vollständigen Namens-Slugs auch exakt übereinstimmende vollständige Namen im Linktext (z. B. Profil-URL `/p/12345`). Fremde Hosts, URL-Parameter, Fragmente, Zugangsdaten, Namenskonflikte und mehrdeutige Namenszuordnungen werden verworfen. Ein Treffer bleibt immer ein ungeprüfter Review-Kandidat und wird niemals automatisch akzeptiert.
