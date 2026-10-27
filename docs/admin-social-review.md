# Administrator review of social profiles

Visit `/admin` over HTTPS and sign in with `ADMIN_REFRESH_PASSWORD`.
`SOCIAL_REFRESH_TOKEN` must also be set. The dashboard can load the
review candidates for DE/FR/IT/RM and show their profile and source links.

**Confirm** publishes the selected profile through the saved snapshot,
with `Admin-Freigabe` source attribution and `manually_approved` evidence.
**Reject** marks the candidate rejected and does not promote it. Decisions
are saved in `.social_review_decisions.json` by default (override with
`SOCIAL_REVIEW_DECISIONS_PATH`). Persist this file on durable storage.
Approved decisions are re-applied when reading cached profiles and after
each refresh, but only for candidates still present in that language's
review queue. If a previously approved account needs to be removed,
review the existing published snapshot as well before switching its decision.

The endpoints `GET /admin/review?language=DE` and `POST /admin/review`
require an authenticated admin session; the POST additionally requires
same-origin and `X-Admin-Action: 1`. Example JSON POST:
```json
{"language":"DE","person_number":"42","url":"https://instagram.com/example","decision":"approved"}
```
The URL must match an existing candidate in the current snapshot.
The admin session and refresh lock remain process-local; use one worker.

## Review dashboard filters

The `/admin` review list now supports case-insensitive search across member number, platform, profile URL and source URL, filtering by pending/approved/rejected state and by platform, plus sorting by member, platform or status. Counts for all four statuses show the complete currently loaded language; the displayed count shows the filtered subset. All filtering happens locally in the browser, without changing review decisions. Refresh the list after a server refresh to see newly discovered candidates.

## Qualitätsprüfung

Unter `/admin` zeigt **Links prüfen** eine schreibgeschützte Prüfung der veröffentlichten Social-Media-Links an. Gemeldet werden ungültige URLs, Plattformabweichungen und identische Profil-URLs bei verschiedenen Mitgliedern. Die Prüfung erfolgt ohne Netzwerkzugriffe und löscht keine Profile. Sie kann deshalb nicht feststellen, ob ein Profil inzwischen offline ist oder zu einer bestimmten Person gehört.

## Optionale Online-Erreichbarkeitsprüfung

Unter `/admin` startet **Erreichbarkeit prüfen** eine asynchrone, nur nach Anmeldung und ausdrücklichem Klick ausgeführte Prüfung von maximal 100 verschiedenen Profil-URLs pro Lauf (maximal drei gleichzeitige HTTPS-Anfragen, sechs Sekunden Timeout). Der bestehende PublicHTTPSClient schützt bei DNS-Auflösung vor nicht öffentlichen IP-Adressen; Weiterleitungen werden nicht automatisch verfolgt. HTTP 200–299 gilt als erreichbar, 404/410 als möglicherweise nicht mehr vorhanden, 401/403/429 als eingeschränkt oder rate-limitiert. Fehler und Weiterleitungen werden separat gemeldet. Keine URL wird automatisch gelöscht oder umgehängt. Die Resultate werden nur im Arbeitsspeicher des Serverprozesses gehalten; ein Neustart verwirft sie. Eine Plattform kann Bots blockieren, weshalb ein HTTP-Fehler nicht zwingend ein verschwundenes Profil bedeutet.

## Persistenter Prüfverlauf

Jeder erfolgreich abgeschlossene Online-Erreichbarkeitstest wird in einer SQLite-Datei gespeichert. Standard: `.social_check_history.sqlite3`; mit `SOCIAL_CHECK_HISTORY_PATH` kann ein dauerhaftes Verzeichnis gewählt werden. Pro Sprache werden die letzten 30 Prüfläufe behalten. Das Admin-Dashboard zeigt die letzten 10 Durchläufe mit auffälligen Links und Änderungen des HTTP-Status gegenüber dem unmittelbar vorherigen Lauf. Verglichen werden nur identische Kombinationen aus Mitglied, Plattform und normalisierter URL, die in beiden Läufen tatsächlich geprüft wurden. Nicht geprüfte Links werden **nicht** als gelöscht interpretiert. Die Datenbank muss auf persistentem Speicher liegen und sollte nur für den Serveradministrator lesbar sein. Die Prüfung selbst bleibt manuell ausgelöst; es gibt keine automatische Änderung oder Löschung der Profile. Die Verlaufsansicht ist nur nach Admin-Anmeldung erreichbar: `GET /admin/live-check/history?language=DE`.

## Automatische regelmässige Linkprüfung (Opt-in)

Standardmässig **deaktiviert**. Setzen Sie serverseitig `SOCIAL_CHECK_INTERVAL_HOURS=24` für täglich oder `168` für wöchentlich (zulässig: 1–720 Stunden). Mit `SOCIAL_CHECK_LANGUAGES=DE,FR` legen Sie die zu prüfenden Sprachen fest (Standard: DE). Beim Serverstart und danach stündlich wird anhand des letzten erfolgreich gespeicherten Prüflaufs in SQLite entschieden, ob eine Sprache fällig ist. Prüfungen laufen nacheinander und verwenden die bestehenden HTTPS-/SSRF-Schutzmechanismen sowie die Obergrenze von 100 URLs je Lauf. Ein bereits laufender manueller Check wird nicht überlagert. Neue Auffälligkeiten und Statusänderungen sind im bestehenden Admin-Prüfverlauf sichtbar; es gibt keine E-Mail-Benachrichtigung und keine automatische Löschung. Unter `/admin/live-check/schedule` ist der Zeitplan nur für angemeldete Administratoren einsehbar. **Wichtig:** Der Scheduler ist pro Serverprozess lokal; für den Betrieb nur einen Uvicorn-Worker und keine parallelen Serverinstanzen verwenden. Die SQLite-Datei muss persistent gespeichert werden. Änderungen der Umgebungsvariablen erfordern einen Serverneustart.

## Priorisierte Warnmeldungen

Die Admin-Oberfläche zeigt unter **Warnmeldungen** die Auffälligkeiten des letzten gespeicherten Prüflaufs. Priorität **Hoch** bedeutet HTTP 404/410 (Profil möglicherweise nicht vorhanden), **Mittel** bedeutet Weiterleitung, unklarer Status oder Netzwerkfehler; **Niedrig** bedeutet HTTP 401/403/429 (Zugriff eingeschränkt oder Rate-Limit, kein Beweis für Löschung). Ein **neues Problem** wird nur angezeigt, wenn derselbe Link beim unmittelbar vorherigen Prüflauf ausdrücklich erreichbar war und jetzt eine Auffälligkeit aufweist. Filter für Priorität und neue Probleme wirken im Browser. Die Ergebnisse sind nur mit Admin-Anmeldung über `GET /admin/live-check/alerts?language=DE` abrufbar. Es werden keine Profile automatisch gelöscht oder verändert.

## Manuelle Bearbeitung von Warnmeldungen

Administratoren können bei einer aktuellen Warnmeldung **Offen**, **In Bearbeitung**, **Erledigt** oder **Ignoriert** auswählen und eine Begründung hinterlegen. Für Erledigt und Ignoriert ist die Begründung Pflicht. Entscheidungen werden in der bestehenden SQLite-Datei (`SOCIAL_CHECK_HISTORY_PATH`) pro Sprache, Mitglied, Plattform und kanonischer URL gespeichert, auch nach Serverneustarts. Die Warnmeldungsübersicht kann nach Bearbeitungsstatus gefiltert werden. Nur aktuell gemeldete Links können über den authentifizierten, Same-Origin-geschützten Endpunkt `POST /admin/live-check/alerts/triage` bearbeitet werden. Die Entscheidungen verändern oder löschen **keine** Social-Media-Profile. Nachfolgende Prüfungen aktualisieren die technische Warnung unabhängig vom manuellen Bearbeitungsstatus.

## Automatische Wiedereröffnung

Beim Speichern eines abgeschlossenen Prüflaufs wird eine zuvor manuell als **Erledigt** oder **Ignoriert** markierte Warnmeldung automatisch auf **Offen** zurückgesetzt, wenn derselbe Mitglied-/Plattform-/URL-Link im unmittelbar vorherigen Prüflauf als **erreichbar** galt und im neuen Prüflauf wieder auffällig ist. Ungeprüfte Links, fortbestehende Fehler und Fälle ohne direkte Vergleichsmessung führen **nicht** zur Wiedereröffnung. Einträge **In Bearbeitung** bleiben unverändert. Die bisherigen manuellen Entscheidungen und automatischen Wiedereröffnungen werden in `alert_triage_events` dauerhaft protokolliert; der Button **Verlauf** zeigt diese Historie im Dashboard. `GET /admin/live-check/alerts/events` erfordert eine Admin-Sitzung. Es erfolgt keine Änderung an veröffentlichten Social-Media-Profilen.

## Admin-Kennzahlen und Trends

Der Abschnitt **Kennzahlen und Trends** zeigt aktuelle Bearbeitungsstände (Offen, In Bearbeitung, Erledigt, Ignoriert), die Zahl aktiver Warnmeldungen und die Zahl aktiver Warnmeldungen hoher Priorität. Das Diagramm zeigt die Anzahl technischer Auffälligkeiten der letzten 5, 10, 20 oder 30 gespeicherten Prüfläufe in chronologischer Reihenfolge. Der authentifizierte Endpunkt `GET /admin/live-check/metrics?language=DE&limit=10` liefert diese Daten. **Wichtig:** Der historische Verlauf basiert auf den jeweiligen technischen Prüfergebnissen, nicht auf früheren manuellen Bearbeitungsständen. Unterschiedliche Prüfumfänge können den Trend beeinflussen; die Anzahl geprüfter Links wird pro Datenpunkt bereitgestellt. Die Funktion verändert keine Profile.
