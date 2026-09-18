"""Amharic and English message templates for GoodBingo bot."""

REGISTRATION_REQUIRED = (
    "🛡️ *ምዝገባ ያስፈልጋል (Registration Required)*\n\n"
    "GoodBingoን ለመጠቀም ከታች *Share Phone* የሚለውን ይጫኑት። "
    "ከዛም *Share* የሚለውን ይጫኑ"
)

REGISTERED = "✅ Registered! Balance: {balance:.2f} ETB"

MAIN_MENU = (
    "🎱 *GoodBingo Menu*\n\n"
    "Choose a command:\n"
    "/play — Join a game\n"
    "/balance — View balance\n"
    "/deposit — Add funds\n"
    "/withdraw — Withdraw funds\n"
    "/history — Transaction history\n"
    "/instructions — Game rules"
)

INSTRUCTIONS = (
    "📋 *Game Instructions*\n\n"
    "1. Use /play to open a room: PLAY (10 ETB, 24/7) or superBingo (50 ETB, 1:00 LT night / 7:00 PM EAT).\n"
    "2. Watching the game board is free! Anyone can launch the mini-app and spectate.\n"
    "3. In the lobby, you can choose and select a card to play. The entry fee is only deducted when you select a card.\n"
    "4. If your balance is below the fee, you won't be playing, but can continue watching.\n"
    "5. Numbers are called automatically — mark matching numbers on your card.\n"
    "6. Call *Bingo!* when you complete a winning pattern to win the pot!\n\n"
    "Need help? Contact @GoodBingoSupport"
)

NO_HISTORY = "📭 No transactions yet."

HISTORY_HEADER = "📜 *Transaction History*\n\n"

WITHDRAW_PROMPT = "💸 Enter the amount you want to withdraw (ETB):"

INSUFFICIENT_BALANCE = "❌ በቂ ሂሳብ የሎትም (Insufficient balance)."

WITHDRAW_SUCCESS = "✅ Withdrawal of {amount:.2f} ETB submitted. New balance: {balance:.2f} ETB"

DEPOSIT_METHOD_PROMPT = (
    "💳 *የገንዘብ ማስገቢያ መንገድ ይምረጡ*\n\n"
    "ገንዘብ ለማስገባት ከታች ካሉት አማራጮች አንዱን ይምረጡ:\n"
    "1️⃣ 🔵 Telebirr\n"
    "2️⃣ 🟢 CBE Birr\n"
    "3️⃣ 🏦 Mobile Banking (CBE Account)\n\n"
    "ክፍያ ከፈጸሙ በኋላ የደረሰዎትን ሙሉ የ SMS መልዕክት ወይም Transaction ID እዚሁ ይላኩልን!"
)

TELEBIRR_DEPOSIT_INSTRUCTIONS = (
    "🔵 *የ Telebirr ክፍያ መረጃ*\n\n"
    "📱 ስልክ ቁጥር: `096357327`\n"
    "👤 ስም: *Habtamu Melese*\n\n"
    "*መመሪያ:*\n"
    "1. ወደ Telebirr መተግበሪያ በመግባት ከላይ ባለው ስልክ ቁጥር የሚፈልጉትን መጠን ያስተላልፉ።\n"
    "2. ክፍያው እንደተጠናቀቀ ከ Telebirr የደረሰዎትን ሙሉ የ SMS መልዕክት ወይም *Transaction ID* ኮፒ (copy) አድርገው እዚሁ ይላኩልን (paste ያድርጉ)።\n"
    "3. ስርዓታችን ወዲያውኑ አረጋግጦ ሂሳብዎ ላይ ይጨምራል! 🎱"
)

CBE_DEPOSIT_INSTRUCTIONS = (
    "🟢 *የ CBE Birr ክፍያ መረጃ*\n\n"
    "📱 ስልክ ቁጥር: `093490411`\n"
    "👤 ስም: *Natnael Temesegen*\n\n"
    "*መመሪያ:*\n"
    "1. ወደ CBE Birr በመግባት ከላይ ባለው ስልክ ቁጥር የሚፈልጉትን መጠን ያስተላልፉ።\n"
    "2. ክፍያው እንደተጠናቀቀ ከ CBE Birr የደረሰዎትን ሙሉ የ SMS መልዕክት ወይም *Transaction ID* ኮፒ (copy) አድርገው እዚሁ ይላኩልን (paste ያድርጉ)።\n"
    "3. ስርዓታችን ወዲያውኑ አረጋግጦ ሂሳብዎ ላይ ይጨምራል! 🎱"
)

MOBILE_BANKING_DEPOSIT_INSTRUCTIONS = (
    "🏦 *የ CBE Mobile Banking (የንግድ ባንክ አካውንት) መረጃ*\n\n"
    "💳 አካውንት ቁጥር: `1000413343538`\n"
    "👤 ስም: *Natnael Temesegen*\n"
    "🏦 ባንክ: *የኢትዮጵያ ንግድ ባንክ (Commercial Bank of Ethiopia)*\n\n"
    "*መመሪያ:*\n"
    "1. በ CBE Mobile App ወይም በ *889# ከላይ ባለው የባንክ አካውንት ቁጥር የሚፈልጉትን መጠን ያስተላልፉ።\n"
    "2. ክፍያው እንደተጠናቀቀ ከባንክ የደረሰዎትን ሙሉ የ SMS መልዕክት ወይም *Transaction ID* ኮፒ (copy) አድርገው እዚሁ ይላኩልን (paste ያድርጉ)።\n"
    "3. ስርዓታችን ወዲያውኑ አረጋግጦ ሂሳብዎ ላይ ይጨምራል! 🎱"
)

DEPOSIT_SMS_PROMPT = "📩 የከፈሉበትን የ SMS መልዕክት ወይም Transaction ID እዚህ ይላኩልን:"

DEPOSIT_AUTO_APPROVED = (
    "✅ *ክፍያዎ ተረጋግጧል!*\n\n"
    "💰 መጠን: *{amount:.2f} ETB*\n"
    "💳 አዲስ ቀሪ ሂሳብ: *{balance:.2f} ETB*\n\n"
    "አሁን መጫወት ይችላሉ። መልካም እድል! 🎱"
)

DEPOSIT_REUSED = (
    "⚠️ ይህ የክፍያ ማስረጃ ቀድሞውኑ የ{amount:.2f} ETB ገቢ ተደርጓል።\n"
    "የተሳሳተ መስሎ ከታየዎት @GoodBingoSupport ን ያነጋግሩ።"
)

DEPOSIT_PENDING = (
    "⏳ የክፍያ ማረጋገጫ በመካሄድ ላይ ነው...\n"
    "ማረጋገጫው እንዳለቀ በራስ-ሰር ይጨመርልዎታል!"
)

BALANCE = "💰 Your balance: *{balance:.2f} ETB*"

PLAY_ROOM_PROMPT = (
    "🕹 *PLAY IN:*\n"
    "Choose a room to join the game:"
)

NOT_REGISTERED = "⚠️ Please register first with /start"

INVALID_AMOUNT = "❌ Please enter a valid positive number."

NOT_ENOUGH_FOR_GAME = (
    "❌ Insufficient balance for this room. "
    "You need at least {amount:.2f} ETB. Use /deposit to add funds."
)

# ---------------------------------------------------------------------------
# Telebirr deposit messages
# ---------------------------------------------------------------------------

DEPOSIT_AMOUNT_PROMPT = (
    "💳 *Telebirr ክፍያ*\n\n"
    "ስንት ETB ማስቀመጥ ይፈልጋሉ?\n"
    "_(ዝቅተኛ ገቢ: 10 ETB)_"
)

DEPOSIT_INVALID_AMOUNT = (
    "❌ እባክዎ ትክክለኛ መጠን ያስገቡ።\n"
    "ዝቅተኛ: *{min:.0f} ETB* | ከፍተኛ: *{max:.0f} ETB*"
)

DEPOSIT_CREATING_ORDER = "⏳ የTelebirr ክፍያ ትዕዛዝ በማዘጋጀት ላይ..."

DEPOSIT_ORDER_CREATED = (
    "✅ *ትዕዛዝ ተዘጋጅቷል!*\n\n"
    "💰 መጠን: *{amount:.2f} ETB*\n"
    "📋 ትዕዛዝ ቁጥር: `{order_id}`\n\n"
    "👇 ከታች ያለውን ቁልፍ ተጭነው Telebirr ላይ ይክፈሉ። "
    "ክፍያ ሲጠናቀቅ ሂሳብዎ ወዲያውኑ ይጨምራል።"
)

DEPOSIT_TELEBIRR_ERROR = (
    "❌ *የTelebirr ስርዓት ችግር*\n\n"
    "አሁን ትዕዛዝ ማዘጋጀት አልተቻለም። ቆይተው እንደገና ይሞክሩ "
    "ወይም @GoodBingoSupport ያግኙ።"
)

DEPOSIT_CONFIRMED = (
    "✅ *ክፍያዎ ተቀብሏል!*\n\n"
    "💰 *{amount:.2f} ETB* ወደ ሂሳብዎ ተጨምሯል።\n"
    "💳 አዲስ ሂሳብ: *{balance:.2f} ETB*\n\n"
    "እንኳን ደስ ያልዎ! GoodBingo ለመጫወት ዝግጁ ነዎት። 🎱"
)

# ---------------------------------------------------------------------------
# Payment verification (Verify.et) messages
# ---------------------------------------------------------------------------

VERIFY_PROMPT = (
    "🔎 *የክፍያ ማረጋገጫ (Payment Verification)*\n\n"
    "ክፍያ ከከፈሉ ከዚህ በታች ያለውን ዘዴ ይምረጡ — "
    "ከዛ የክፍያ ማስረጃ ቁጥሩን ወደ እኛ ይላኩ።\n\n"
    "Select how you paid so we can verify your receipt:"
)

VERIFY_REFERENCE_PROMPT = (
    "🔑 *{bank} ማረጋገጫ*\n\n"
    "የክፍያ ማስረጃ ቁጥሩን (transaction/receipt number) ይላኩ።\n\n"
    "Send your payment reference number:"
)

VERIFY_PHONE_PROMPT = (
    "📱 *CBE Birr:* ክፍያ የፈጸሙበትን የስልክ ቁጥር ይላኩ።\n\n"
    "Send the phone number you used to pay (e.g. 09XXXXXXXXX):"
)

VERIFY_AMOUNT_PROMPT = (
    "💰 የከፈሉትን መጠን በ ETB ይላኩ። (ዝቅተኛ: 10 ETB)\n\n"
    "Enter the amount you paid in ETB (min 10):"
)

VERIFY_CHECKING = "⏳ ክፍያዎን በማረጋገጥ ላይ... (Verifying your payment...)"

VERIFY_SUCCESS = (
    "✅ *ፔይመንት ተረጋግጧል (Payment Verified)!*\n\n"
    "💰 *{amount:.2f} ETB* ወደ ሂሳብዎ ተጨምሯል።\n"
    "💳 አዲስ ሂሳብ: *{balance:.2f} ETB*\n\n"
    "እንኳን ደስ ያልዎ! መልካም እድል! 🎱"
)

VERIFY_REUSED = (
    "⚠️ ይህ የክፍያ ማስረጃ ቀድሞውኑ የ{amount:.2f} ETB ገቢ አድርጓል።\n"
    "This receipt has already been used for a deposit. "
    "If you think this is a mistake, contact @GoodBingoSupport."
)

VERIFY_PENDING = (
    "⏳ ክፍያዎ *እየተረጋገጠ* ነው። ውጤቱ ሲገኝ በራስ-ሰር እናሳውቅዎታለን።\n\n"
    "Your payment is being verified — you will be notified automatically."
)

VERIFY_NOT_FOUND = (
    "❌ ክፍያው አልተገኘም። የማስረጃ ቁጥሩን አረጋግጠው እንደገና ይሞክሩ።\n"
    "Payment not found. Please double-check the reference and try again."
)

VERIFY_RETRY_LATER = (
    "⚠️ ለጊዜው ማረጋገጥ አልተቻለም። ትንሽ ቆይተው እንደገና ይሞክሩ።\n"
    "Verification is temporarily unavailable — please try again shortly."
)

VERIFY_NO_CREDITS = (
    "⚠️ የማረጋገጫ አገልግሎት ጥቅም አልቋል። @GoodBingoSupport ያግኙ።\n"
    "The verification service has no credits left — contact @GoodBingoSupport."
)

VERIFY_SERVICE_ERROR = (
    "❌ የማረጋገጫ አገልግሎት ችግር አጋጥሟል። @GoodBingoSupport ያግኙ።\n"
    "A verification service error occurred — please contact @GoodBingoSupport."
)

VERIFY_INVALID_REFERENCE = (
    "⚠️ የተላከው የክፍያ ማስረጃ ቁጥር ተቀባይነት የለውም።\n"
    "The payment reference format is invalid."
)

VERIFY_CANCELLED = "Verification cancelled."
