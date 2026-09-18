# Deploy GoodBingo on Fly.io (CI/CD)

One always-on VM runs the Telegram bot, FastAPI, WebSockets, and the mini-app. That is the stable setup; Vercel is not used.

## 1. One-time: Fly app, volume, secrets

```bash
# https://fly.io/docs/hands-on/install-flyctl/
fly auth login
fly apps create etoobingobot
fly volumes create goodbingo_data --region fra --size 1 --app etoobingobot
```

If `etoobingobot` is taken, change `app` and `WEBAPP_URL` in `fly.toml` to match.

Set secrets (never commit `.env`):

```bash
fly secrets set --app etoobingobot \
  BOT_TOKEN="..." \
  WEBAPP_URL="https://etoobingobot.fly.dev" \
  TELEBIRR_BASE_URL="..." \
  TELEBIRR_FABRIC_APP_ID="..." \
  TELEBIRR_APP_SECRET="..." \
  TELEBIRR_MERCHANT_APP_ID="..." \
  TELEBIRR_MERCHANT_CODE="..." \
  TELEBIRR_WEB_BASE_URL="..." \
  TELEBIRR_PRIVATE_KEY="..." \
  API_KEY="..."
```

Optional: `TELEBIRR_PUBLIC_KEY`, `VERIFY_ET_WEBHOOK_SECRET`, `SUPER_BINGO_ALWAYS_OPEN`.

Then:

```bash
fly deploy
fly status
curl https://etoobingobot.fly.dev/healthz
```

## 2. Telegram

1. [@BotFather](https://t.me/BotFather) → `/setdomain` → `etoobingobot.fly.dev`
2. Restart is not required if `WEBAPP_URL` already matches; `/play` opens `https://etoobingobot.fly.dev?room=...`
3. Keep **one** replica. Two machines = split bingo rooms + Telegram 409 conflicts.

## 3. GitHub Actions

The workflow in `.github/workflows/ci.yml`:

- **Every push/PR:** `python -m unittest discover -s tests`
- **Push to `main`/`master`:** `flyctl deploy --remote-only` when `FLY_API_TOKEN` is set

Create a Fly token: `fly tokens create deploy` (or dashboard → Access Tokens).  
GitHub repo → Settings → Secrets → Actions → `FLY_API_TOKEN`.

Until that secret exists, CI still runs tests and skips deploy.

## 4. After each deploy

- SQLite lives on the `goodbingo_data` volume (`/data/goodbingo.db`). Image deploys do not wipe users.
- Telebirr notify URL must be `https://etoobingobot.fly.dev/telebirr/notify` (same host as the mini-app).
- Real deposits still need valid Telebirr merchant credentials; hosting does not fix sandbox keys.

## 5. Scale and ops

| Do | Don't |
|---|---|
| `min_machines_running = 1` | `fly scale count 2` |
| `auto_stop_machines = "off"` | Let Fly stop the VM (bot polling dies) |
| Health check `/healthz` | Point checks at `/` only |

Logs: `fly logs -a etoobingobot`

Local Docker smoke test:

```bash
docker build -t goodbingo .
docker run --rm -p 8080:8080 --env-file .env -e SERVER_PORT=8080 -e DATABASE_PATH=/data/goodbingo.db goodbingo
```

(`BOT_TOKEN` must be in the env file; do not bake `.env` into the image.)
