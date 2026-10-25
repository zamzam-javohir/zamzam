# ZAM ZAM Mini App bot — free cloud deploy (Render + Neon)

Host: **Render free Web Service** (Docker, HTTPS, stable `https://<name>.onrender.com`, no card).
DB: **Neon free Postgres** (no card, persistent). Render's disk is ephemeral, so all data lives in Neon.
Menu photos for sample items ship in `photos/`; photos added by admins are stored as Telegram `file_id` in the DB.

Modes: `PUBLIC_URL` (or Render's automatic `RENDER_EXTERNAL_URL`) set → webhook at `PUBLIC_URL/tg/<hash>` with
`secret_token`, Mini App + menu button = `PUBLIC_URL/`. Not set → long polling (box mode). `DATABASE_URL` set → Postgres,
else SQLite (`DB_PATH` or `zamzam.db`).

## Env vars
| var | value |
|---|---|
| `ZAMZAM_APP_TOKEN` | bot token from @BotFather (secret) |
| `DATABASE_URL` | Neon connection string, `postgresql://...?sslmode=require` (secret) |
| `OWNER_ID` | `5280407495` (seeded as owner only if admins table is empty) |
| `PUBLIC_URL` | optional on Render (auto from `RENDER_EXTERNAL_URL`) |
| `KEEPALIVE` / `KEEPALIVE_SECONDS` | self-ping `/health` every 600 s so the free service doesn't sleep (default on) |

## Steps
1. **Neon**: https://console.neon.tech/signup → sign up (GitHub/Google/email) → create project `zamzam`, region
   *AWS Europe (Frankfurt)* → Dashboard → **Connect** → copy the connection string (pooled or direct, `sslmode=require`).
2. **Migrate data** (from the box): `DATABASE_URL='<neon url>' python migrate.py /workspace/zamzam_app/zamzam.db`
3. **Code on GitHub** (public repo is fine, no secrets inside): push this folder.
4. **Render**: https://dashboard.render.com/register → sign up → **New + → Web Service** → connect GitHub repo
   (or "Public Git repository" URL) → Language **Docker**, Region **Frankfurt**, Instance type **Free** →
   Environment: add `ZAMZAM_APP_TOKEN`, `DATABASE_URL`, `OWNER_ID` → Health check path `/health` → **Deploy**.
   (Or **New + → Blueprint** and pick the repo: `render.yaml` defines everything; it asks for the 2 secrets.)
5. Stop the box copy of the bot (only one process may own the token; the cloud copy sets the webhook, which also
   makes any polling copy fail).
6. Optional backup keep-alive: https://uptimerobot.com or https://cron-job.org → HTTP monitor on
   `https://<name>.onrender.com/health` every 5–10 min.

## API/CLI deploy after signup
Render API key: Dashboard → Account Settings → API Keys → Create. Then:
```
curl -s -X POST https://api.render.com/v1/services -H "Authorization: Bearer $RENDER_API_KEY" -H 'Content-Type: application/json' -d '{
 "type":"web_service","name":"zamzam-bot","ownerId":"<owner id from GET /v1/owners>",
 "repo":"https://github.com/<user>/zamzam_deploy","branch":"main","autoDeploy":"yes",
 "serviceDetails":{"runtime":"docker","plan":"free","region":"frankfurt","healthCheckPath":"/health",
   "envSpecificDetails":{"dockerfilePath":"./Dockerfile"}},
 "envVars":[{"key":"ZAMZAM_APP_TOKEN","value":"..."},{"key":"DATABASE_URL","value":"..."},{"key":"OWNER_ID","value":"5280407495"}]}'
```
Neon: `npm i -g neonctl && neonctl auth` (or `NEON_API_KEY`) → `neonctl projects create --name zamzam --region-id aws-eu-central-1`
→ `neonctl connection-string`.

## Tests
`python test_webapp.py && python test_bot.py && python test_webhook.py` (set `DATABASE_URL` to run them on Postgres).

## Limits (free)
Render free: sleeps after 15 min without inbound HTTP (self-ping prevents it; a Telegram webhook or Mini App open
also wakes it in ~1 min), 750 instance-hours/month (enough for one service 24/7), restarts on deploy.
Neon free: 0.5 GB storage, compute auto-suspends when idle (first query after idle takes ~1 s; the bot reconnects).
