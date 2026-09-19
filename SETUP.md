# Client Radar — Buyer Setup Guide

Get your private radar live in about 5 minutes. No coding required.

## What you need

From your purchase delivery email:
- Your **license key** (looks like `CR-G-XXXXXXXXXXXX-XXXXXX`)
- Your **license secret** (a long code labeled `LICENSE_SECRET`)

Plus:
- A free account at [Render](https://render.com) (recommended), Railway, or Fly.io
- Optionally: an OpenAI-compatible API key (for AI intent scoring — ~$1–3/month)

## Step 1 — Deploy your private copy (Render)

1. In Render: **New → Web Service**.
2. Choose **"Public Git repository"** and paste the repo URL from your delivery email.
3. Settings:
   - **Start command:** `pip install -r requirements.txt && python app.py`
   - Under **Environment variables**, add exactly these two:
     - `LICENSE_SECRET` → paste the license secret from your delivery email ⚠️
       **Use the value we sent you — do NOT make up your own.** Your license
       key only unlocks with the matching secret.
     - `FLASK_SECRET` → paste any random string (e.g. generate one at
       [random.org](https://www.random.org/strings/) — this one just protects
       your login session).
4. Add a **persistent disk** (Render → your service → Disks) so your data
   survives restarts, and set `CLIENT_RADAR_DB` to the disk path
   (e.g. `/data/client_radar.db`). Or skip this — the app works without it,
   but mentions reset if the service restarts.
5. Click **Deploy**. Wait ~2 minutes, then open your service URL.

## Step 2 — Activate

Your browser opens on the activation page. Paste your **license key** and
submit. The dashboard unlocks immediately.

*Key rejected?* Double-check `LICENSE_SECRET` in your Render environment
variables matches the delivery email exactly — no extra spaces.

## Step 3 — Add your AI key (recommended)

Go to **Settings** and paste any OpenAI-compatible API key. This turns on
AI intent scoring (0–10 with a written rationale) and AI-drafted outreach
replies. Without it, the app still works on keyword-based scoring, but expect
more false positives.

## Step 4 — Add keywords

Go to **Keywords** and add the phrases your buyers type when they want what
you sell (e.g. `looking for a logo designer`). Your tier sets the limit:
Starter 1, Growth 3, Scale 10.

The radar polls every 30 minutes automatically. New mentions appear on the
**dashboard**, scored and with outreach drafts for anything 7+.

## Daily digest

The **Digest** page shows the last 24 hours of high-intent mentions. Add
`SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `SMTP_FROM`
environment variables (any SMTP provider, e.g. Gmail app password,
Resend, Amazon SES) to also get it by email.

## Privacy

Your instance is yours alone: your keywords, mentions, and API key live in
your database on your host. Nothing is shared. Your API key is stored
server-side, shown masked, and never logged.
