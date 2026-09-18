# GoodBingo - Run Guide & Commands

ይህ ሰነድ ፕሮጀክቱን በቀላሉ ለማስኬድ የሚያስፈልጉ ትዕዛዞችን (Commands) ይዟል።

---

## 🚀 1. ፕሮጀክቱን ለማስኬድ (Run the Project)

### Windows (PowerShell / Command Prompt):

```powershell
# 1. ወደ ፕሮጀክት ማህደር ይግቡ (Project Directory)
cd "c:\Users\kalki\Desktop\3rd year\DSA\Etoo_bot"

# 2. ፕሮጀክቱን ያስጀምሩ (ይህም ሰርቨሩን እና የቴሌግራም ቦቱን በአንድ ላይ ያስጀምራል)
.\.venv\Scripts\python.exe -u run.py
```

ወይም ቨርቹዋል ኢንቫይሮንመንቱን አክቲቭ በማድረግ፡

```powershell
.\.venv\Scripts\Activate.ps1
python run.py
```

> **ማስታወሻ:** `run.py` ሁለቱንም በአንድ ላይ ያስጀምራል፡
> - **Game Server & WebApp:** `http://localhost:8765`
> - **Telegram Bot:** Polling በራስ-ሰር ይጀምራል።

---

## 🌐 2. Cloudflare Tunnel (የቴሌግራም Mini App እና Webhook ለማገናኘት)

የቴሌግራም ቦት Mini App እና የ PeerPayment Webhook በኢንተርኔት እንዲሰሩ አዲስ የቱነል ዊንዶው (New Terminal) ከፍተው ይህን ያስኪዱ፡

```powershell
.\cloudflared.exe tunnel --url http://127.0.0.1:8765
```

የሚሰጣችሁን የ HTTPS ሊንክ (ለምሳሌ፡ `https://xxxx.trycloudflare.com`) ወስዳችሁ፡
1. **Telegram BotFather:** ለ Mini App WebApp URL ይጠቀሙበት።
2. **PeerPayment Dashboard:** ለ Webhook URL `https://xxxx.trycloudflare.com/api/payment/peerpay/webhook` አድርገው ያስገቡት።

---

## 🧪 3. ቴስቶችን ለማስኬድ (Run Test Suite)

ሁሉንም 80 ቴስቶች ለማረጋገጥ፡

```powershell
.\.venv\Scripts\python.exe -m unittest discover tests
```

---

## ⚙️ 4. የአካባቢ ተለዋዋጮች (.env Configuration)

በ `.env` ፋይል ውስጥ የሚከተሉት መኖራቸውን ያረጋግጡ፡

```env
BOT_TOKEN=8673869441:AAGw8QrahC3JAc894cN6vQonHN2ETV5BGvQ
WEBAPP_URL=https://<YOUR-CLOUDFLARE-URL>.trycloudflare.com
SERVER_PORT=8765
DATABASE_PATH=goodbingo.db
PEERPAY_API_KEY=pp_sk_merchant_...
PEERPAY_WEBHOOK_SECRET=whsec_...
FREE_PLAY=False
```

---

## 🛠️ አጠቃላይ ሁኔታዎችን ለማቆም ወይም እንደገና ለማስጀመር (Stop & Restart)

- ለማቆም በቴርሚናሉ ላይ **`Ctrl + C`** ይጫኑ።
- እንደገና ለማስጀመር `.\.venv\Scripts\python.exe -u run.py` ያሂዱ።
