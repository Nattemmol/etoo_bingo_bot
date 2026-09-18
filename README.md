# GoodBingo — Telegram Bot & Mini-App

Telegram bingo bot with phone registration, wallet, and a real-time Web App game.

## Features

| Command | Description |
|---------|-------------|
| `/start` | Register via phone share |
| `/play` | Choose room → opens bingo mini-app |
| `/balance` | View wallet balance |
| `/deposit` | CBE BIRR / TELE BIRR deposit |
| `/withdraw` | Withdraw with balance check |
| `/history` | Transaction history |
| `/instructions` | Game rules |

### Mini-App Game

- **75-ball bingo** with B-I-N-G-O columns
- **Real-time multiplayer** rooms via WebSocket
- **Free launch & spectating:** Users can launch the mini-app and watch the live game board without paying any entry fee.
- **Card selection entry fee:** Entry fee is only deducted when a player chooses to select a card to play in the round. If balance is insufficient, they are informed and remain in spectator mode.
- **Two rooms:**
  - **PLAY (10 ETB):** Runs 24/7 ("all the time") with continuous rounds.
  - **superBingo (50 ETB):** Scheduled daily at **1:00 LT night (7:00 PM EAT)** (UTC+03:00 / EAT). Outside 1:00 LT, players can watch the room and view a live countdown timer. (Can be tested anytime with `SUPER_BINGO_ALWAYS_OPEN=true` in `.env`).
- **15-second lobby** countdown once players join
- **Tap to mark** called numbers on your card
- **BINGO!** button validates row/column/diagonal wins
- Winner takes the full pot

## Quick Start

### 1. Configure `.env`

```env
BOT_TOKEN=your_bot_token
WEBAPP_URL=https://your-ngrok-url.ngrok-free.app
SERVER_PORT=8080
SUPER_BINGO_ALWAYS_OPEN=false
```

### 2. Install & run

```powershell
cd "C:\Users\kalki\Desktop\3rd year\DSA\Etoo_bot"
.venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

This starts **both** the Telegram bot and the game server on port 8080.

### 3. Expose with ngrok (required for Telegram Web App)

Telegram mini-apps require **HTTPS**:

```powershell
ngrok http 8080
```

Copy the `https://....ngrok-free.app` URL into `.env` as `WEBAPP_URL`, then restart `python run.py`.

### 4. Test

1. Open [@etoobingobot](https://t.me/etoobingobot)
2. `/start` → register
3. Add test balance (deposit flow) or manually update DB
4. `/play` → pick a room → **Open Game**
5. Run automated test suite:
   ```powershell
   .venv\Scripts\python.exe -m unittest tests/test_spectator_and_schedule.py
   ```

## Project Structure

```
Etoo_bot/
├── run.py                 # Start bot + game server together
├── bot/                   # Telegram bot
├── server/                # FastAPI game server + WebSocket
│   ├── main.py            # WebSocket handler, static files
│   ├── game.py            # Bingo logic, schedules (EAT), win check
│   └── auth.py            # Telegram initData validation
├── webapp/                # Mini-app frontend
│   ├── index.html
│   ├── style.css
│   ├── app.js             # WebSocket client, spectator & card state
│   └── game.js            # Card rendering, mark logic
└── tests/                 # Automated test suite
```

## Game Flow

1. User opens mini-app from `/play` → connects freely as spectator (no balance required).
2. User can watch the game board (called numbers 1-75, current called ball, pot, and winners) at any time.
3. In the lobby, user can preview and shuffle cards.
4. When clicking **Select Card to Play**:
   - If balance >= entry fee: fee is deducted, player is entered into the round with that card.
   - If balance < entry fee: an alert is displayed, fee is not deducted, and user remains a spectator.
5. Lobby countdown (15s) runs when players join.
6. Numbers called automatically every 3–4 seconds.
7. Active players mark matching numbers on their card.
8. First valid BINGO wins the pot.
9. Round resets automatically for all connected users, returning to lobby for the next round.

## Production (Fly.io + GitHub Actions)

Do **not** deploy this stack to Vercel. Use Fly.io so the bot, mini-app, and `/ws` share one HTTPS host.

See **[DEPLOY.md](DEPLOY.md)** for app creation, volume, secrets, BotFather `/setdomain`, and the `FLY_API_TOKEN` GitHub secret.

`GET /healthz` reports process + SQLite (and optional `WEBAPP_URL` reachability).

## Development Notes

- Entry fee is checked in the bot before showing the Web App button, and deducted again on the server at join (with balance validation).
- Leaving during lobby refunds the entry fee.
- Deposit SMS verification is still manual/pending — use DB to add test balance for now.
- `python -m bot.main` uses the same `run.run_bot` loop as `python run.py`.
