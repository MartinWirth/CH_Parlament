# Social profile provenance and candidate review

The refresh snapshot now retains `profile_evidence` for profiles found through
existing Parliament, party and personal-homepage scanners. Each evidence entry
includes platform, URL, source type, optional source page, check time and status.

`GET /api/social-profiles` also returns a separate `review_candidates` map.
Review candidates are **never** inserted into `profiles` automatically.

## Additional research sources

- Public member biographies and homepage fields published by the Swiss
  Parliament: https://www.parlament.ch/de/ratsmitglieder?k=%2A
- Official personal websites linked from those biographies.
- Official party member pages, including party rosters already supported by
  the application.
- Other third-party social-media directories only as **unverified leads**,
  since older listings can be stale or contain name collisions.

The public Parliament biographies provide website fields, but they are not a
guarantee that any social account belongs to the member.

## Optional candidate file

Place a JSON array in `social_review_candidates.json` at the repository root
(or configure `SOCIAL_REVIEW_CANDIDATES_PATH`). Example:

```json
[
  {
    "person_number": "CURRENT_MEMBER_PERSON_NUMBER",
    "name": "Exact Name From Current Member API",
    "url": "https://www.instagram.com/example_account/",
    "source_url": "https://example.org/official-person-page"
  }
]
```

Use **actual current** member numbers and exact API names; placeholders will
be rejected. All URLs must be HTTPS. Entries are validated against the
current member list and accepted only into a manual review queue. Do not
label an account as verified solely because the URL has a matching name.

The source URL is evidence of where the candidate was found, **not** proof
of ownership. Check the linked source and profile manually before approving
an attribution. The system does not automatically open arbitrary external
candidate URLs.

`profile_evidence.verification_status = source_attributed` indicates the
link's provenance is known; it does not claim independent platform-level
identity verification. For party/parliament sources where an exact source
page is unavailable, `source_url` is null rather than guessed.

## Limitations

- The candidate file is an import mechanism, not a live web-search API.
- No unverified link is automatically promoted to an accepted profile.
- Existing external homepage scanning remains opt-in with
  `ENABLE_EXTERNAL_HOMEPAGE_SCAN=1`.
- Candidate source URLs are not fetched, avoiding a new SSRF surface.
- No private or non-public profiles are collected.
