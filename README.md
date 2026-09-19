# Client Radar — AI buying-intent monitor for freelancers & agencies

Zero-setup lead radar: enter niche keywords once, and the app watches Reddit +
Hacker News around the clock, AI-scores every mention for buying intent (0–10),
drafts a personalized outreach reply for hot leads, and serves a searchable
dashboard + daily digest.

**Delivery model (how it's sold on Gumroad):** license key + self-hosted access.
The buyer deploys their own private instance in ~5 minutes via a one-click host
(Render/Railway/Fly), enters the license key from their Gumroad receipt, adds
their own LLM API key, and they're live. One instance = one buyer (single-tenant
by design for the MVP — no shared database, no cross-customer data).

**Tiers** (one-time): Starter $49 → 1 keyword · Growth $99 → 3 keywords ·
Scale $199 → 10 keywords.

## Quick start (local)

```bash
cd client-radar
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export LICENSE_SECRET="$(openssl rand -hex 16)"
export FLASK_SECRET="$(openssl rand -hex 16)"
.venv/bin/python app.py        # -> http://localhost:5000
```

Issue a license key (or wire the Gumroad webhook below and skip this):

```bash
.venv/bin/python issue_key.py --tier growth --email buyer@example.com
# -> CR-G-XXXXXXXXXXXX-XXXXXX
```

Run a poll cycle manually any time:

```bash
.venv/bin/python monitor.py --once
```

## How it works

- **Monitoring** (`monitor.py`): every poll cycle (default 30 min, configurable)
  each keyword is searched on Reddit (via the PullPush submission-search API,
  falling back to Reddit's own `search.rss` — both public, no auth, since
  Reddit's anonymous `search.json` is widely 403-blocked) and Hacker News
  (Algolia API, comments). New mentions are stored in SQLite, deduplicated by
  source ID and URL.
- **Scoring**: if the buyer configured an LLM endpoint + key (Settings page),
  each mention is scored 0–10 with a one-line rationale via an OpenAI-compatible
  `/chat/completions` call. Otherwise a transparent keyword-heuristic fallback
  scores it (labeled `heuristic` in the UI and in the stored rationale).
- **Outreach drafts**: every mention scoring ≥ 7 gets a short personalized reply
  draft (LLM-generated when a key is configured, template fallback otherwise).
- **Digest**: `/digest` shows the last 24h of score-5+ mentions; "Send email
  digest" delivers it via SMTP if configured.
- **License auth**: keys are `CR-<tier>-<random>-<hmac>`; validation is offline
  HMAC-SHA256 against `LICENSE_SECRET` — no license server to run. The app
  redirects to `/activate` until a valid key is entered. The API key the buyer
  pastes in Settings is stored server-side in SQLite, masked in the UI, and
  never logged.

## Environment variables

| Var | Required | Purpose |
|---|---|---|
| `LICENSE_SECRET` | **yes (prod)** | HMAC secret for issuing/validating license keys. Keys issued under one secret only validate under the same secret. |
| `FLASK_SECRET` | yes (prod) | Flask session signing. |
| `PORT` | no | Default `5000`. |
| `CLIENT_RADAR_DB` | no | SQLite path. Default `./client_radar.db`. |
| `GUMROAD_PERMALINK_STARTER` | no | Gumroad product permalink for Starter (default `client-radar-starter`). |
| `GUMROAD_PERMALINK_GROWTH` | no | Same for Growth (default `client-radar-growth`). |
| `GUMROAD_PERMALINK_SCALE` | no | Same for Scale (default `client-radar-scale`). |
| `SMTP_HOST` / `SMTP_PORT` | no | Digest + license-key emails. Without these, digest is in-app only. |
| `SMTP_USER` / `SMTP_PASS` | no | SMTP auth (if needed). |
| `SMTP_FROM` | no | From address. Default `client-radar@localhost`. |
| `EXTRA_CA_BUNDLE` | no | Extra CA PEM for TLS-intercepting egress proxies (dev-sandbox use). |

No secrets are committed to the repo. `.venv/` and `*.db` are runtime artifacts.

## Hosting (buyer self-host, ~5 min)

Single Python service + SQLite, no build step. Any of:

- **Render**: New → Web Service → point at the repo, start command
  `pip install -r requirements.txt && python app.py`; add a persistent disk for
  `client_radar.db` (or set `CLIENT_RADAR_DB` to the disk path). ~$7/mo.
- **Railway**: New project → deploy repo, set env vars. ~$5/mo.
- **Fly.io**: `fly launch`, 256MB VM is plenty. ~$2–5/mo.

Scheduler runs in-process (APScheduler); SQLite lives on the attached disk.
No other infrastructure. **Estimated cost: $5–7/mo hosting + ~$1–3/mo buyer LLM
usage** (a few hundred short scoring calls/day on a mini-class model).

## Gumroad wiring

1. Create three products ($49 / $99 / $199), note each product's permalink.
2. Set `GUMROAD_PERMALINK_*` env vars to those permalinks.
3. Product Settings → Advanced → add webhook URL:
   `https://YOUR-HOST/webhook/gumroad`
4. On each sale Gumroad POSTs to `/webhook/gumroad`; the app maps the product
   permalink → tier, issues a license key, stores it, and:
   - if `SMTP_HOST` is set → emails the key to the buyer automatically;
   - otherwise → returns the key in the webhook response; deliver it via
     Gumroad's receipt workflow email, or issue manually anytime with
     `python issue_key.py --tier growth --email buyer@example.com`.

   (Gumroad does not sign webhooks; keep the webhook URL unlisted and treat the
   permalink check as the gate — documented in `app.py`.)

## Verified working (Sep 19 2026, live demo)

- License issue → validate → tampered-key rejection; `/activate` gate.
- Keyword add/remove with tier enforcement (1/3/10).
- Real poll: 25 Reddit mentions fetched for "looking for a logo designer",
  scored, drafts generated; dashboard / mentions / digest all render.
- Gumroad webhook: sale payload → key issued + stored.
- Digest email degrades gracefully without SMTP.

See `DEMO.md` for the full transcript.

## Known limitations (MVP)

- Heuristic fallback scoring has false positives (e.g. product-recommendation
  posts matching "looking for"/"budget") — the LLM path exists precisely for
  this; buyers should add their key.
- HN results can include older threads (90-day freshness filter applied).
- Reddit comment-level search is via PullPush submissions + RSS posts only
  (their comment endpoint rate-limits automated clients).
- Single-tenant: one instance per buyer. Multi-tenant is out of MVP scope.
