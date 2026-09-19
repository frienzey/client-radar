# Deploying straight from this zip (no waiting on the repo link)

This zip is the complete Client Radar app. Two ways to get it live:

## Option A — use the public repo (recommended when you have the link)
Your delivery email includes the public GitHub repo URL. In Render, choose
"Public Git repository" and paste that URL, then follow SETUP.md from Step 1.

## Option B — push it yourself (works right now)
1. Unzip this file and push the `client-radar` folder to a new GitHub repo
   (it can be private — it's your copy).
2. In Render: New → Web Service → connect your repo.
3. Follow SETUP.md from Step 1, using YOUR repo URL.

Either way, you'll still need from your delivery email:
- your **license key** (activation page)
- the **LICENSE_SECRET** (Render environment variable — use the emailed value
  exactly, don't invent your own)
