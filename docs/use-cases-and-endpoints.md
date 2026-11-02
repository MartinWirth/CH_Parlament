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
| GET | `/admin/directory-source-health` | `language=DE` | Gespeicherte Erreichbarkeit und Nutzbarkeit der Parteiverzeichnis-Quellen |
| POST | `/admin/directory-source-review` | JSON: `language`, `url`, `status`, `comment` | Authentifizierte manuelle Quellenprüfung (Same-Origin-Schutz) |
| GET | `/admin/directory-review-audit` | `language`, `source`, `status`, `from_date`, `to_date`, `format=json\|csv` | Authentifizierter gefilterter Audit-Verlauf und CSV/JSON-Export |
| GET | `/admin/directory-storage-health` | – | Geschützter Speicher- und Wiederherstellungsstatus |
| POST | `/admin/directory-review-backup` | – | Geschützte manuelle Sicherung von Prüfstatus und Audit |
| POST | `/admin/directory-review-restore` | – | Geschützte Wiederherstellung aus letzter gültiger Sicherung |
| GET | `/admin/directory-backup-versions` | – | Geschützte Liste der SQLite-Backup-Versionen |
| POST | `/admin/directory-backup-version-restore` | JSON `{version}` | Geschützte Wiederherstellung einer ausgewählten SQLite-Version |
| GET | `/admin/discovery-report` | `language=DE`, `limit=100` (1–500) | Geschützter Bericht über Kandidaten, Fundstellen, Abdeckung und Identitätskonflikte aus dem gespeicherten Snapshot |
| POST | `/admin/review` | JSON mit exakt `language`, `person_number`, `url`, `decision` | Review-Entscheidung für vorhandenen Kandidaten speichern |
| GET | `/admin/targeted-ui.js` | – | Öffentliches JavaScript der Nachrecherche-Anzeige; API-Daten erfordern Admin-Anmeldung |
| GET | `/admin/directory-health-ui.js` | – | Statisches JavaScript der Quellenstatus-Anzeige |
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

### Verifizierte kantonale Quellen (PR #73)

Die Quellenliste `social_cantonal_party_directories.json` enthält neu zusätzlich die offiziellen Mitgliederverzeichnisse von Die Mitte St. Gallen (`https://sg.die-mitte.ch/menschen/`), Thurgau (`https://tg.die-mitte.ch/menschen/`) und Graubünden (`https://gr.die-mitte.ch/menschen/`). Die bestehenden Grenzen und die manuelle Identitätsprüfung bleiben unverändert. Diese Quellen wurden anhand der öffentlich zugänglichen offiziellen Partei-Webseiten recherchiert; der tatsächliche Scanner-Ertrag wird erst bei einem Refresh sichtbar.

### Zusätzliche Parteiverzeichnisse (PR #74)

Drei offizielle SVP-Kantonsseiten (Zürich, Bern, Aargau) und die offiziellen nationalen Parlamentsübersichten von FDP, SP und Grünen ergänzen die vier bestehenden kantonalen Quellen. **Hinweis:** Die drei nationalen Übersichten sind keine kantonalen Verzeichnisse, werden aber durch denselben begrenzten Rechercheprozess verarbeitet. Jede Quelle muss weiterhin den Host-/HTTPS-Prüfungen entsprechen; Social-Media-Funde bleiben manuell zu prüfen. Quellen: https://www.svp.ch/partei/kantone/zuerich/ ; https://www.svp.ch/partei/kantone/bern/ ; https://www.svp.ch/partei/kantone/aargau/ ; https://www.fdp.ch/partei/organe/fraktion/ ; https://www.sp-ps.ch/menschen/national-und-staenderat/ ; https://gruene.ch/gruene-im-bundeshaus .

### Diagnose der Parteiverzeichnis-Quellen (PR #75)

Bei jedem Social-Media-Refresh erfasst `cantonal_directory_discovery.sources` für jede konfigurierte Quelle die URL, den Scanner-Status, `usable`, `matched_members` und gegebenenfalls `reason` (Abruf-/Parsingfehler oder `no_matching_member_links`). `usable=true` bedeutet nur, dass die Seite geparst werden konnte; es bestätigt nicht, dass sie tatsächlich Personenprofile liefert. Fehlerhafte Quellen werden nicht automatisch entfernt. Die Prüfung nutzt die bereits vorhandenen SSRF-geschützten Scans; es werden keine zusätzlichen Netzwerkanfragen ausgelöst.

### Admin-Ansicht Quellenstatus (PR #76)

Der authentifizierte Endpunkt `GET /admin/directory-source-health?language=DE` liest den zuletzt gespeicherten Snapshot (`debug.review_candidates.cantonal_directory_discovery`). Das Admin-Dashboard zeigt URL, Scanner-Status, Anzahl gefundener Mitglieder und Fehler-/Hinweisgrund in einer Tabelle. Keine zusätzlichen Netzwerkanfragen und keine automatische Quellenfreigabe. Ohne vorherigen Refresh erscheint ein Hinweis, dass noch keine Quelldiagnose vorhanden ist.

### Wiederholt fehlerhafte Parteiverzeichnisse (PR #77)

Jeder erfolgreiche Social-Media-Refresh vergleicht den Quellenstatus mit dem vorherigen gespeicherten Snapshot derselben Sprache. Nach drei aufeinanderfolgenden Scan-/Parse-Fehlern derselben URL wird `persistent_failure=true` gesetzt; `failure_streak` zeigt die Anzahl aufeinanderfolgender Fehler (maximal 999). Ein erfolgreich geparstes Verzeichnis setzt den Zähler zurück, auch wenn keine passenden Mitglieder gefunden wurden. Neue URLs beginnen bei null. Die Admin-Quellenstatus-Tabelle zeigt den Fehlerzähler und kennzeichnet dauerhaft fehlerhafte Quellen; die API liefert `persistent_failure_count`. Die Kennzeichnung ist eine manuelle Prüfempfehlung: Quellen werden nicht automatisch deaktiviert und keine Identitäten automatisch bestätigt. Der Verlauf ist nur so dauerhaft wie der gespeicherte Snapshot.

### Priorisierte Prüfliste für Parteiverzeichnisse (PR #78)

Die bestehende geschützte API `GET /admin/directory-source-health` liefert zusätzlich `review_queue` aus dem zuletzt gespeicherten Snapshot. Prioritäten: `high` nach mindestens drei aufeinanderfolgenden Scanfehlern, `medium` bei einzelnen Scanfehlern, `low` bei erfolgreich geparsten Verzeichnissen ohne neue Mitgliedertreffer. Die Prüfliste ist nach Priorität, Fehleranzahl und URL sortiert. Pro Quelle werden bis zu zehn Fehlerereignisse mit Zeitstempel, Status und Grund sowie der letzte erfolgreiche Scan gespeichert. Die Admin-Oberfläche zeigt diese Angaben und manuelle Handlungsempfehlungen. Die Historie beginnt erst nach Aktivierung dieser Funktion, reicht nur so weit zurück wie der gespeicherte Snapshot und ersetzt kein dauerhaftes Audit-Log. Es erfolgt keine automatische Deaktivierung oder Freigabe.

### Manueller Prüfworkflow für Parteiverzeichnisse (PR #79)

Administratoren können in der priorisierten Quellenprüfliste eine konfigurierte URL als `in_review` (In Prüfung), `resolved` (Behoben) oder `deferred` (Zurückgestellt) markieren und einen Kommentar (maximal 1000 Zeichen) speichern. Der geschützte Endpunkt `POST /admin/directory-source-review` verlangt Session-Cookie, gültigen Origin und `X-Admin-Action: 1`. Die Daten werden pro Sprache und URL in `SOCIAL_DIRECTORY_REVIEW_PATH` (Standard `.social_directory_review.json`) atomar gespeichert, unabhängig vom Scan-Snapshot. `GET /admin/directory-source-health` liefert `manual_reviews` für die im Snapshot vorhandenen Quellen. Der Status beeinflusst weder Scans noch automatische Freigaben und kann beim nächsten Refresh weiter bearbeitet werden.

### Änderungsverlauf manueller Quellenprüfungen (PR #80)

Jede tatsächliche Änderung des Prüfstatus oder Kommentars erzeugt ein Audit-Ereignis mit Sprache, Quellen-URL, UTC-Zeitstempel, vorherigem und neuem Status sowie vorherigem und neuem Kommentar. Identische wiederholte Speichervorgänge erzeugen keinen zusätzlichen Eintrag. Die Ereignisse werden unabhängig vom Snapshot unter `SOCIAL_DIRECTORY_AUDIT_PATH` (Standard: `SOCIAL_DIRECTORY_REVIEW_PATH` plus `.audit.json`) gespeichert; die letzten 1000 Ereignisse bleiben erhalten. Die bestehende authentifizierte API `GET /admin/directory-source-health` liefert `review_audit` für im Snapshot sichtbare Quellen. Das Admin-Dashboard zeigt den Verlauf pro Quelle. Dies ist eine begrenzte Änderungshistorie und kein manipulationssicheres, unbegrenzt aufbewahrtes Compliance-Audit; mangels individueller Admin-Accounts wird kein Benutzername protokolliert.

### Audit-Filter und Export (PR #81)

Der authentifizierte Endpunkt `GET /admin/directory-review-audit` filtert die gespeicherten Audit-Ereignisse nach Sprache (`language`), exakter Quellen-URL (`source`), neuem Prüfstatus (`status` = `in_review`, `resolved`, `deferred`) und inklusivem UTC-Datumsbereich (`from_date`, `to_date` im Format `YYYY-MM-DD`). Standard `format=json` liefert Anzahl und Ereignisse; `format=csv` exportiert eine UTF-8-CSV mit BOM und Spalten für Zeitpunkt, Sprache, Quelle, vorherigen/neuen Status und Kommentare. Potenzielle Tabellenformeln werden im CSV-Export entschärft. Das Admin-Dashboard bietet Filter, eine Trefferliste und CSV-/JSON-Downloads. Die Ausgabe umfasst auch frühere Quellen, sofern Ereignisse im begrenzten Audit-Log vorhanden sind; maximal 1000 Audit-Ereignisse werden gespeichert.

### Sichere Audit-Speicherung und Wiederherstellung (PR #82)

Schreibvorgänge auf manuelle Quellenprüfungen werden über eine exklusive Lock-Datei (`SOCIAL_DIRECTORY_REVIEW_PATH` plus `.lock`) pro Speicherort zwischen Prozessen serialisiert. Bei Sperrkonflikten wartet die Anwendung maximal zehn Sekunden und bricht dann ab, statt Daten zu überschreiben. Status und Audit werden über temporäre Dateien mit `fsync` und atomarem `os.replace` geschrieben. Vor den beiden Primärdateien wird ein konsistenter Transaktions-Snapshot in `SOCIAL_DIRECTORY_BACKUP_PATH` (Standard `.social_directory_review.json.backup.json`) geschrieben, der bei fehlenden oder beschädigten Primärdateien zur Wiederherstellung dient. Die geschützten Endpunkte `POST /admin/directory-review-backup` und `POST /admin/directory-review-restore` verlangen Admin-Session und Same-Origin. Das Admin-Dashboard bietet Sicherung und bestätigte Wiederherstellung. Die Sicherung wird bei jeder Änderung aktualisiert und ist daher ein Recovery-Checkpoint, keine historische Versionierung. Die Lock-Datei muss bei einem harten Prozessabsturz gegebenenfalls nach Prüfung manuell entfernt werden; Sicherungen sollten zusätzlich extern gesichert werden.

### Integritätskontrolle der Verzeichnisprüfungen (PR #83)

Beim ASGI-Anwendungsstart werden Primärdateien mit dem Recovery-Checkpoint verglichen. Falls ein gültiger Checkpoint vorliegt und eine Primärdatei fehlt, beschädigt ist oder vom Checkpoint abweicht, wird sie automatisch wiederhergestellt. Der Vorgang wird mit UTC-Zeitstempel und Anzahl wiederhergestellter Audit-Ereignisse in `SOCIAL_DIRECTORY_RECOVERY_LOG_PATH` (Standard `.social_directory_review.json.recovery.json`) protokolliert; maximal 100 Einträge bleiben erhalten. Nach jedem Prüfstatus-Schreibvorgang erfolgt eine Integritätsprüfung. `GET /admin/directory-storage-health` zeigt authentifizierten Administratoren den Status `empty`, `healthy`, `recovery_needed` oder `degraded` und den letzten Recovery-Vorgang. Die Admin-Oberfläche zeigt den Zustand an. Bei fehlendem oder ungültigem Checkpoint erfolgt keine automatische Wiederherstellung; ein manueller Eingriff ist erforderlich. Die Kontrolle erkennt strukturelle JSON-Fehler und Abweichungen vom Checkpoint, aber keine absichtliche Manipulation aller Dateien.

### SQLite-Transaktionsspeicherung für Verzeichnisprüfungen (PR #84)

Neue Installationen verwenden standardmässig SQLite in `SOCIAL_DIRECTORY_DB_PATH` (Standard `.social_directory_review.sqlite3`), sofern `SOCIAL_DIRECTORY_REVIEW_PATH` nicht explizit gesetzt ist. `SOCIAL_DIRECTORY_STORAGE_BACKEND=sqlite` aktiviert SQLite auch bei konfigurierten Legacy-JSON-Pfaden; `SOCIAL_DIRECTORY_STORAGE_BACKEND=json` behält das bisherige JSON-Verfahren bei. Beim Anwendungsstart werden vorhandene Legacy-JSON-Daten in eine leere SQLite-Datenbank einmalig importiert; die alten Dateien bleiben als Migrationskopie erhalten. Vor der Umstellung sollten die Legacy-Dateien extern gesichert werden. SQLite nutzt `BEGIN IMMEDIATE` und eine gemeinsame Transaktion für Prüfstatus und Audit, `WAL`, `synchronous=FULL` und `busy_timeout=10000`. Die letzten 1000 Audit-Ereignisse werden behalten. Die geschützten Backup-/Restore-Endpunkte verwenden SQLite-Online-Backup unter `SOCIAL_DIRECTORY_DB_BACKUP_PATH` (Standard `.social_directory_review.sqlite3.backup`). Der Speicherstatus basiert auf `PRAGMA integrity_check`. Die frühere JSON-Recovery-Protokollierung gilt weiterhin nur für den JSON-Modus; SQLite nutzt die transaktionale Wiederherstellung und eine explizite Datenbanksicherung. Ein beschädigtes SQLite-File wird nicht automatisch aus dem Backup überschrieben.

### Validierte und abgesicherte SQLite-Migration (PR #85)

Vor dem ersten Import werden Legacy-Status und Audit-Daten vollständig strukturell validiert; ungültige Statuswerte, Sprachen, URLs und fehlende Pflichtfelder stoppen die Migration ohne Teilimport. Vor dem Schreiben in SQLite werden vorhandene JSON-Quelldateien und der Recovery-Checkpoint bytegenau nach `SOCIAL_DIRECTORY_MIGRATION_BACKUP_DIR` (Standard `.social_directory_review.sqlite3.legacy-backup`) kopiert und atomar gespeichert. Bereits vorhandene Sicherungen werden nicht überschrieben: abweichende Inhalte verhindern einen automatischen Neuversuch und müssen manuell geprüft werden. Der Import und der persistente Migrationsmarker werden in derselben SQLite-Transaktion geschrieben. Bei Fehlern erfolgt ein Rollback; nach Beseitigung der Ursache kann die Migration erneut gestartet werden. Der Admin-Endpunkt `GET /admin/directory-storage-health` liefert im SQLite-Modus zusätzlich `migration.state` (`not_migrated` oder `completed`), Zeitpunkt und importierte Datensatzanzahl; das Admin-Dashboard zeigt den Status an. Bestehende SQLite-Daten werden nicht durch alte JSON-Dateien überschrieben. Das Verfahren validiert Datenstruktur, nicht die fachliche Richtigkeit historischer Quellenangaben.

### Automatische SQLite-Wartung (PR #86)

Optional kann die Anwendung SQLite-Integritätsprüfungen und anschliessende Backups mit `SOCIAL_DIRECTORY_MAINTENANCE_INTERVAL_HOURS` (Standard `0`, deaktiviert) periodisch ausführen. Bei einem Wert grösser als null startet ein Wartungs-Task im ASGI-Lifecycle; beim Herunterfahren wird er beendet. Jeder Lauf prüft die Datenbank mit `PRAGMA integrity_check`. Nur nach erfolgreicher Prüfung wird über die SQLite-Online-Backup-API eine temporäre Sicherung erstellt, separat geprüft und anschliessend atomar als `SOCIAL_DIRECTORY_DB_BACKUP_PATH` veröffentlicht. Bei Fehlern bleibt die vorherige Sicherung erhalten. Letzte Prüfung, Ergebnis, letzter erfolgreicher Sicherungszeitpunkt und letzter Fehler werden in `SOCIAL_DIRECTORY_MAINTENANCE_STATE_PATH` (Standard `.social_directory_review.sqlite3.maintenance.json`) gespeichert. Der authentifizierte Endpunkt `GET /admin/directory-storage-health` und das Admin-Dashboard zeigen diese Angaben. Die Sicherung ist ein einzelner rotierender Stand und ersetzt kein externes Backup oder eine Aufbewahrungsstrategie. Der periodische Task läuft pro App-Prozess; bei mehreren Workern können gleichzeitige Läufe auftreten.

### SQLite-Backup-Versionierung und gezielte Wiederherstellung (PR #87)

Bei aktivierter periodischer SQLite-Wartung werden verifizierte Sicherungen zusätzlich mit UTC-Zeitstempel und zufälliger Kennung in `SOCIAL_DIRECTORY_BACKUP_VERSIONS_DIR` (Standard `.social_directory_review.sqlite3.backup.versions`) archiviert. `SOCIAL_DIRECTORY_BACKUP_RETENTION_COUNT` (Standard 7; 1–365) begrenzt die Anzahl der archivierten Versionen. Die ältesten Versionen werden erst nach erfolgreicher Erstellung und Prüfung einer neuen Version entfernt. Die geschützte API `GET /admin/directory-backup-versions` liefert Versions-IDs, Gültigkeit und Dateigrösse. Über `POST /admin/directory-backup-version-restore` mit JSON `{"version":"<ID>"}` kann ein Administrator eine ausgewählte gültige Version wiederherstellen. Vor dem Restore wird die aktuelle Datenbank zusätzlich als `<backup>.pre-restore` gesichert. Die Admin-Oberfläche zeigt die auswählbaren Versionen und verlangt vor dem Wiederherstellen eine Bestätigung. Die Wiederherstellung ersetzt den aktuellen SQLite-Datenbestand. Die Versionskennung wird streng validiert; beliebige Dateipfade sind nicht zulässig. Die automatische Versionierung setzt Wartungsläufe voraus; der bestehende manuelle Backup-Endpunkt erzeugt weiterhin nur den aktuellen Einzel-Checkpoint.
