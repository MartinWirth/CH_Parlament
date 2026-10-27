# Admin refresh dashboard

The opt-in administrator interface is available at `/admin`.

## Configuration

Set **both** `ADMIN_REFRESH_PASSWORD` and `SOCIAL_REFRESH_TOKEN` in the
**server environment**. Never place either secret in frontend code or commit
it to Git. Serve the application over HTTPS using a trusted reverse proxy.
The login cookie has `Secure`, `HttpOnly` and `SameSite=Strict`.

The browser sends the admin password once during login, over HTTPS, and then
uses an opaque HttpOnly session cookie. The refresh token is never sent to
the browser. POST requests require a matching `Origin` and
`X-Admin-Action: 1` to reduce CSRF risk. Five failed login attempts per
client IP within five minutes cause temporary lockout.

## Workflow

1. Open `https://YOUR-HOST/admin` and log in.
2. Select a language and optionally enable force.
3. Click **Aktualisieren**. The UI polls `GET /admin/status` every 2.5 s.
4. Review completion or failure. Detailed errors remain in server logs.
5. Log out.

## Deployment limitations

- **Single worker only**: sessions, job status, and the concurrency lock
  are in-process. Multi-worker or multi-instance deployments require shared
  session storage and a distributed job lock.
- The status reports queued/running/completed/failed and timestamps, **not**
  per-member percentage progress.
- The browser-facing admin page must only be deployed behind HTTPS; HTTP
  localhost does not accept Secure cookies.
- The existing `POST /api/social-profiles/refresh` token-protected API
  and `python -m app.refresh_cli` remain available.
- External homepage scanning remains opt-in through
  `ENABLE_EXTERNAL_HOMEPAGE_SCAN=1`; enabling it requires operational
  review and network egress restrictions.
