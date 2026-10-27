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
