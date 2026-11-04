# Entwicklungsdialog – CH_Parlament

Dieses Dokument fasst den Entwicklungsdialog und die wichtigsten Entscheidungen zum Repository `MartinWirth/CH_Parlament` zusammen. Es ist **keine wortgetreue Abschrift** des gesamten Chats.

## Zusammenarbeit

- `do it`: Die nächste vorgeschlagene Verbesserung implementieren und als Pull Request zur Prüfung bereitstellen.
- `ci test`: GitHub Actions für den aktuellen Pull Request kontrollieren, Fehler analysieren und gegebenenfalls korrigieren.
- `merge`: Den geprüften Pull Request nach erfolgreicher CI in `main` übernehmen.

## Projektentwicklung

Die Anwendung verwaltet Informationen zum Schweizer Parlament und ergänzt diese um Quellen, Kontakt- und Social-Media-Links sowie administrative Prüffunktionen.

### Datenquellen und Qualität

- Prüfung und Dokumentation von Links zu Parteien, Kantonen und Social-Media-Profilen.
- Sicherheitskontrollen für externe HTTPS-Anfragen, darunter Schutz vor DNS-Rebinding.
- Administrativer Review-Prozess mit Status, Kommentaren und Audit-Historie.

### Speicherung und Sicherung

- Umstellung der Review-Daten auf SQLite mit Transaktionen und Migration aus JSON.
- Integritätsprüfung, regelmässige Backups, versionierte Sicherungen und Aufbewahrungsregeln.
- Prozessübergreifende Wartungssperre für Backup- und Restore-Operationen.
- Backup-Historie mit Zeitstempel, Dateigrösse und Integritätsstatus.
- Isolierte Wiederherstellungstests in temporären SQLite-Datenbanken ohne Veränderung der produktiven Daten.

## Letzte Pull Requests

| PR | Inhalt | Status |
| --- | --- | --- |
| [#88](https://github.com/MartinWirth/CH_Parlament/pull/88) | Prozessübergreifende Backup-Sperre und E2E-Tests | Gemergt |
| [#89](https://github.com/MartinWirth/CH_Parlament/pull/89) | Backup-Historie und manuelle Integritätsprüfung | Gemergt; CI #715 erfolgreich |
| [#90](https://github.com/MartinWirth/CH_Parlament/pull/90) | Isolierte SQLite-Wiederherstellungstests | Gemergt; CI #724 erfolgreich |

## Lokale Entwicklung

Die Python-Tests können mit `python -m pytest -q` ausgeführt werden. Die GitHub-Actions-Pipeline **Python tests** prüft Änderungen vor dem Merge.

## Hinweise

Dieses Dokument enthält bewusst keine Zugangsdaten, Tokens oder vertraulichen Konfigurationswerte. Details zu den API-Endpunkten und Anwendungsfällen stehen in [use-cases-and-endpoints.md](use-cases-and-endpoints.md).
