"""Amharic message templates and visual game guides for EtooBingo bot."""

REGISTRATION_REQUIRED = (
    "🛡️ *እንኳን ወደ EtooBingo በደህና መጡ!*\n\n"
    "ጨዋታውን ለመጀመር እና የራስዎን የሂሳብ አካውንት ለመክፈት እባክዎ ከታች ያለውን "
    "*📱 ስልክ ቁጥር አጋራ (Share Phone)* የሚለውን ቁልፍ ይጫኑ።"
)

REGISTERED = (
    "✅ *ምዝገባዎ በተሳካ ሁኔታ ተጠናቋል!*\n\n"
    "💰 ወቅታዊ ቀሪ ሂሳብ: *{balance:.2f} ETB*\n\n"
    "አሁን መጫወት ይችላሉ። መልካም እድል! 🎱"
)

MAIN_MENU = (
    "🎱 *EtooBingo ዋና ማውጫ*\n\n"
    "ከታች ካሉት አማራጮች የሚፈልጉትን ይምረጡ:\n\n"
    "🎮 /play — ጨዋታ ጀምር (Play Game)\n"
    "💰 /balance — ቀሪ ሂሳብ እይ (Check Balance)\n"
    "💳 /deposit — ገንዘብ አስገባ (Deposit Funds)\n"
    "💸 /withdraw — ገንዘብ አውጣ (Withdraw Funds)\n"
    "📜 /history — የሂሳብ እንቅስቃሴ (History)\n"
    "📋 /instructions — የጨዋታ ህግና መመሪያ (Game Rules)"
)

# ---------------------------------------------------------------------------
# Interactive Instructions & Visual Guides
# ---------------------------------------------------------------------------

INSTRUCTIONS_MENU = (
    "📋 *EtooBingo የጨዋታ ህጎችና መመሪያዎች*\n\n"
    "የየትኛውን ጨዋታ መመሪያና የማሸነፊያ ምስላዊ ማብራሪያ ማየት ይፈልጋሉ? ከታች ይምረጡ:\n\n"
    "1️⃣ 🎱 *10 ብር ጨዋታ (PLAY — 24/7)*\n"
    "2️⃣ 🌟 *50 ብር ሱፐር ቢንጎ (superBingo — ማታ 1:00)*\n"
    "3️⃣ 📖 *አጠቃላይ የጨዋታ መመሪያ (General Rules)*\n\n"
    "❓ *የደንበኞች ድጋፍ (Customer Support):*\n"
    "💬 ቴሌግራም: [@SEtoo\\_9](https://t.me/SEtoo_9)\n"
    "📞 ስልክ: `0963572327`"
)

INSTRUCTIONS_10 = (
    "🎱 *የ 10 ብር ጨዋታ ህግና የማሸነፊያ መንገዶች (PLAY — 24/7)*\n\n"
    "💵 *የመግቢያ ክፍያ:* 10 ብር በካርቴላ\n"
    "⏰ *ጊዜ:* 24/7 (ሁልጊዜ ክፍት — በየ 30 ሰከንዱ አዲስ ዙር)\n"
    "👥 *የካርቴላ ብዛት:* 1 እስከ 450 (አንድ ተጫዋች እስከ 2 ካርቴላ መያዝ ይችላል)\n\n"
    "🏆 *እንዴት ያሸንፋሉ? (የማሸነፊያ መንገዶች):*\n"
    "በ 10 ብር ጨዋታ ውስጥ ከሚከተሉት *አንዱ* ሲሞላ BINGO ይሆናል:\n"
    "1. *አግድም (Row)* — የትኛውም ሙሉ አግድም መስመር\n"
    "2. *ቁልቁል (Column)* — የትኛውም ሙሉ ቁልቁል አምድ\n"
    "3. *ሰያፍ (Diagonal)* — ከዳር እስከ ዳር ሰያፍ መስመር\n"
    "4. *4ቱ ማዕዘኖች (4 Corners)* — የካርቴላው 4ቱ ጥግ ቁጥሮች\n\n"
    "🖼 *ምስላዊ ማብራሪያ (Visual Card Diagram):*\n"
    "```\n"
    "1. አግድም (Row)       2. 4ቱ ማዕዘኖች (Corners)\n"
    " [🔴][🔴][🔴][🔴][🔴]     [🔴][⚪][⚪][⚪][🔴]\n"
    " [⚪][⚪][⚪][⚪][⚪]     [⚪][⚪][⚪][⚪][⚪]\n"
    " [⚪][⚪][⭐][⚪][⚪]     [⚪][⚪][⭐][⚪][⚪]\n"
    " [⚪][⚪][⚪][⚪][⚪]     [⚪][⚪][⚪][⚪][⚪]\n"
    " [⚪][⚪][⚪][⚪][⚪]     [🔴][⚪][⚪][⚪][🔴]\n"
    "\n"
    "3. ቁልቁል (Column)    4. ሰያፍ (Diagonal)\n"
    " [🔴][⚪][⚪][⚪][⚪]     [🔴][⚪][⚪][⚪][⚪]\n"
    " [🔴][⚪][⚪][⚪][⚪]     [⚪][🔴][⚪][⚪][⚪]\n"
    " [🔴][⚪][⭐][⚪][⚪]     [⚪][⚪][⭐][⚪][⚪]\n"
    " [🔴][⚪][⚪][⚪][⚪]     [⚪][⚪][⚪][🔴][⚪]\n"
    " [🔴][⚪][⚪][⚪][⚪]     [⚪][⚪][⚪][⚪][🔴]\n"
    "```\n"
    "💡 *ማስታወሻ:* መሃል ላይ ያለው [⭐] (FREE Space) በነፃ እንደተሞላ ይቆጠራል!\n\n"
    "❓ *የደንበኞች ድጋፍ:*\n"
    "💬 ቴሌግራም: [@SEtoo\\_9](https://t.me/SEtoo_9)\n"
    "📞 ስልክ: `0963572327`"
)

INSTRUCTIONS_50 = (
    "🌟 *የ 50 ብር superBingo ህግና ማብራሪያ (ሙሉ ካርድ)*\n\n"
    "💵 *የመግቢያ ክፍያ:* 50 ብር በካርቴላ\n"
    "⏰ *ጊዜ:* በየቀኑ ማታ 1:00 ሰዓት (7:00 PM EAT)\n"
    "👥 *የካርቴላ ብዛት:* 1 እስከ 1,500 ካርቴላዎች\n\n"
    "🏆 *እንዴት ያሸንፋሉ? (Full Card / ሙሉ ካርድ):*\n"
    "በ 50 ብር superBingo ለማሸነፍ በካርቴላዎ ላይ ያሉ *ሙሉ 24ቱም ቁጥሮች* መውጣት አለባቸው!\n\n"
    "🖼 *ምስላዊ ማብራሪያ (Full Card Diagram):*\n"
    "```\n"
    " ┌── 50 ብር superBingo (ሙሉ ካርድ) ──┐\n"
    "    B    I    N    G    O\n"
    "  [🔴] [🔴] [🔴] [🔴] [🔴]\n"
    "  [🔴] [🔴] [🔴] [🔴] [🔴]\n"
    "  [🔴] [🔴] [⭐] [🔴] [🔴]  <- ሙሉ 24ቱም\n"
    "  [🔴] [🔴] [🔴] [🔴] [🔴]     ቁጥሮች ሲወጡ\n"
    "  [🔴] [🔴] [🔴] [🔴] [🔴]     ያሸንፋሉ!\n"
    " └────────────────────────────────┘\n"
    "```\n"
    "💰 *ትልቅ የገንዘብ ሽልማት (Jackpot Pot):* በሺዎች የሚቆጠሩ ተጫዋቾች ስለሚሳተፉ አሸናፊው እጅግ ከፍተኛ የገንዘብ ሽልማት ያገኛል!\n\n"
    "❓ *የደንበኞች ድጋፍ:*\n"
    "💬 ቴሌግራም: [@SEtoo\\_9](https://t.me/SEtoo_9)\n"
    "📞 ስልክ: `0963572327`"
)

INSTRUCTIONS_GENERAL = (
    "📖 *አጠቃላይ የ EtooBingo አጫወት መመሪያ*\n\n"
    "1️⃣ *የቦርድ እይታ በነፃ ነው:* ማንኛውም ሰው ያለምንም ክፍያ ጨዋታውን በቀጥታ መከታተል ይችላል።\n"
    "2️⃣ *ካርቴላ መምረጥ:* በሎቢ (Lobby) ውስጥ ሲሆኑ ከ 1-450 ካሉት ቁጥሮች የሚፈልጉትን እስከ 2 ካርቴላ መርጠው መጫወት ይችላሉ።\n"
    "3️⃣ *የክፍያ አቆራረጥ:* ገንዘብ የሚቆረጠው ካርቴላ መርጠው 'አረጋግጥ' ሲሉ ብቻ ነው።\n"
    "4️⃣ *ቁጥሮችን መጫን:* ቁጥሮች በየ 4 ሰከንዱ በስክሪኑ ላይ ሲወጡ ካርቴላዎ ላይ ያሉትን ተጭነው ምልክት ያድርጉ።\n"
    "5️⃣ *ራስ-ሰር BINGO ማረጋገጫ:* መስመርዎ ሲሞላ ስርዓታችን ወዲያውኑ አረጋግጦ ሽልማትዎን ወደ አካውንትዎ ያስገባል!\n"
    "6️⃣ *የተመላሽ ገንዘብ ዋስትና:* ጨዋታው ሳይጀመር ካርቴላዎን መሰረዝ ከፈለጉ የከፈሉት ሙሉ ገንዘብ ወዲያውኑ ይመለስልዎታል።\n\n"
    "❓ *ማንኛውም ጥያቄ ወይም እርዳታ ከፈለጉ:*\n"
    "💬 ቴሌግራም: [@SEtoo\\_9](https://t.me/SEtoo_9)\n"
    "📞 ስልክ: `0963572327`"
)

# ---------------------------------------------------------------------------
# Balance, History, and Play
# ---------------------------------------------------------------------------

BALANCE = (
    "💰 *የእርስዎ ወቅታዊ ቀሪ ሂሳብ:*\n\n"
    "💵 ቀሪ ገንዘብ: *{balance:.2f} ETB*\n\n"
    "👇 ከታች ያሉትን አማራጮች በመጠቀም መጫወት ወይም ገንዘብ ማስገባት/ማውጣት ይችላሉ:"
)

NO_HISTORY = "📭 *እስካሁን ምንም የሂሳብ እንቅስቃሴ አልተመዘገበም።*"

HISTORY_HEADER = "📜 *የሂሳብ እንቅስቃሴ ዝርዝር (Transaction History):*\n\n"

PLAY_ROOM_PROMPT = (
    "🎮 *የጨዋታ ክፍል ይምረጡ:*\n\n"
    "መጫወት የሚፈልጉትን ክፍል ይምረጡ:\n\n"
    "1️⃣ 🎱 *PLAY (10 ብር)* — ሁልጊዜ ክፍት (24/7)\n"
    "2️⃣ 🌟 *superBingo (50 ብር)* — በየቀኑ ማታ 1:00 ሰዓት"
)

NOT_REGISTERED = "⚠️ *እባክዎ መጀመሪያ በ /start ይመዝገቡ።*"

INVALID_AMOUNT = "❌ *እባክዎ ትክክለኛ የብር መጠን ያስገቡ።*"

NOT_ENOUGH_FOR_GAME = (
    "❌ *ለዚህ ክፍል በቂ ሂሳብ የሎትም።*\n\n"
    "ቢያንስ *{amount:.2f} ETB* ያስፈልግዎታል።\n"
    "ሂሳብ ለመሙላት /deposit ይጠቀሙ።"
)

INSUFFICIENT_BALANCE = "❌ *በቂ ቀሪ ሂሳብ የሎትም (Insufficient balance)።*"

# ---------------------------------------------------------------------------
# Deposit Messages (Telebirr, CBE Birr, CBE Mobile Banking)
# ---------------------------------------------------------------------------

DEPOSIT_METHOD_PROMPT = (
    "💳 *የገንዘብ ማስገቢያ መንገድ ይምረጡ*\n\n"
    "ገንዘብ ለማስገባት ከታች ካሉት አማራጮች አንዱን ይምረጡ:\n\n"
    "1️⃣ 🔵 *Telebirr (ቴሌብር)*\n"
    "2️⃣ 🟢 *CBE Birr (ሲቢኢ ብር)*\n"
    "3️⃣ 🏦 *Mobile Banking (የንግድ ባንክ አካውንት)*\n\n"
    "ክፍያ ከፈጸሙ በኋላ ከባንክ ወይም ከቴሌብር የደረሰዎትን ሙሉ የ SMS መልዕክት ወይም Transaction ID እዚሁ ይላኩልን!"
)

TELEBIRR_DEPOSIT_INSTRUCTIONS = (
    "🔵 *የ Telebirr ክፍያ መረጃ*\n\n"
    "📱 ስልክ ቁጥር: `0963572327`\n"
    "👤 ስም: *Habtamu Melese*\n\n"
    "*መመሪያ:*\n"
    "1. ወደ Telebirr በመግባት ወደ `0963572327` (Habtamu Melese) ያስተላልፉ።\n"
    "2. ክፍያው ሲጠናቀቅ ከታች ካሉት *አንዱን* እዚሁ ይላኩ (paste ያድርጉ):\n"
    "   • 🔗 *ደረሰኝ ሊንክ* (receipt link) ፦ `https://transactioninfo.ethiotelecom.et/receipt/...`\n"
    "   • 🔢 *Transaction ID* (ለምሳሌ: `DIK7W7R5VZ`)\n"
    "   • 📩 *ሙሉ SMS መልዕክት* (ከ Telebirr የደረሰዎ)\n"
    "3. ስርዓታችን ወዲያውኑ አረጋግጦ ሂሳብዎ ላይ ይጨምራል! 🎱"
)

TELEBIRR_1_DEPOSIT_INSTRUCTIONS = TELEBIRR_DEPOSIT_INSTRUCTIONS
TELEBIRR_2_DEPOSIT_INSTRUCTIONS = TELEBIRR_DEPOSIT_INSTRUCTIONS

CBE_DEPOSIT_INSTRUCTIONS = (
    "🟢 *የ CBE Birr ክፍያ መረጃ*\n\n"
    "📱 ስልክ ቁጥር: `0934920411`\n"
    "👤 ስም: *Natnael Temesegen*\n\n"
    "*መመሪያ:*\n"
    "1. ወደ CBE Birr በመግባት ወደ `0934920411` (Natnael Temesegen) ያስተላልፉ።\n"
    "2. ክፍያው ሲጠናቀቅ ከታች ካሉት *አንዱን* እዚሁ ይላኩ (paste ያድርጉ):\n"
    "   • 🔗 *ደረሰኝ ሊንክ* ፦ `https://apps.cbe.com.et:100/BranchReceipt/...`\n"
    "   • 🔢 *Transaction ID / FT ቁጥር*\n"
    "   • 📩 *ሙሉ SMS መልዕክት*\n"
    "3. ስርዓታችን ወዲያውኑ አረጋግጦ ሂሳብዎ ላይ ይጨምራል! 🎱"
)

MOBILE_BANKING_DEPOSIT_INSTRUCTIONS = (
    "🏦 *የ CBE Mobile Banking (የንግድ ባንክ አካውንት) መረጃ*\n\n"
    "💳 አካውንት ቁጥር: `1000413343538`\n"
    "👤 ስም: *Natnael Temesegen*\n\n"
    "🏦 ባንክ: *የኢትዮጵያ ንግድ ባንክ (Commercial Bank of Ethiopia)*\n\n"
    "*መመሪያ:*\n"
    "1. በ CBE Mobile App ወይም በ `*889#` ወደ `1000413343538` ያስተላልፉ።\n"
    "2. ክፍያው ሲጠናቀቅ ከታች ካሉት *አንዱን* እዚሁ ይላኩ (paste ያድርጉ):\n"
    "   • 🔗 *ደረሰኝ ሊንክ* ፦ `https://mbreciept.cbe.com.et/...`\n"
    "   • 🔢 *Transaction ID / FT ቁጥር* (ለምሳሌ: `FT262641DG9X`)\n"
    "   • 📩 *ሙሉ SMS መልዕክት*\n"
    "3. ስርዓታችን ወዲያውኑ አረጋግጦ ሂሳብዎ ላይ ይጨምራል! 🎱"
)

DEPOSIT_SMS_PROMPT = "📩 *የከፈሉበትን የ SMS መልዕክት ወይም Transaction ID እዚህ ይላኩልን:*"

DEPOSIT_AUTO_APPROVED = (
    "✅ *ክፍያዎ በተሳካ ሁኔታ ተረጋግጧል!*\n\n"
    "💰 የተጨመረው መጠን: *{amount:.2f} ETB*\n"
    "💳 አዲስ ቀሪ ሂሳብ: *{balance:.2f} ETB*\n\n"
    "አሁን መጫወት ይችላሉ። መልካም እድል! 🎱"
)

DEPOSIT_REUSED = (
    "⚠️ *ይህ የክፍያ ማስረጃ ቀድሞውኑ ገቢ ተደርጓል።*\n\n"
    "የተሳሳተ መስሎ ከታየዎት [@SEtoo\\_9](https://t.me/SEtoo_9) ን ያነጋግሩ።"
)

DEPOSIT_PENDING = (
    "⏳ *የክፍያ ማረጋገጫ በመካሄድ ላይ ነው...*\n\n"
    "ማረጋገጫው እንዳለቀ በራስ-ሰር ይጨመርልዎታል!"
)

# ---------------------------------------------------------------------------
# Withdraw Messages
# ---------------------------------------------------------------------------

WITHDRAW_PROMPT = "💸 *ማውጣት የሚፈልጉትን መጠን በ ETB ያስገቡ (ዝቅተኛ: 10 ETB):*"

WITHDRAW_SUCCESS = (
    "✅ *የገንዘብ ማውጣት ጥያቄዎ በተሳካ ሁኔታ ተልኳል!*\n\n"
    "💰 የተጠየቀው መጠን: *{amount:.2f} ETB*\n"
    "💳 አዲስ ቀሪ ሂሳብ: *{balance:.2f} ETB*\n\n"
    "ገንዘቡ በደቂቃዎች ውስጥ ወደ አካውንትዎ ይተላለፋል።"
)

# ---------------------------------------------------------------------------
# Super Bingo Reminders & Announcements
# ---------------------------------------------------------------------------

SUPER_BINGO_REMINDER_6H = (
    "🗓ዘወትር ከእሁድ እስከ እሁድ\n"
    "🕙 ከምሽቱ 1 ሰዐት\n"
    "🎫 ካርቴላ ሳያልቅ ⏳ ቀድመው ይያዙ 🏃‍♂️\n\n"
    "❓ ማንኛውም ጥያቄ ካለ ወይም ለድጋፍ:\n"
    "📞 0963572327\n"
    "💬 @SEtoo_9"
)

SUPER_BINGO_REMINDER_10M = (
    "⏳ የ ETOO ሱፐር ቢንጎ ጨዋታ ከ 10 ደቂቃዎች በኋላ ይጀምራል! 🚀\n"
    "🔒 የጨዋታ አይነት: 🎯 ሙሉ ዝግ 🏆\n\n"
    "❓ ማንኛውም ጥያቄ ካለ ወይም ለድጋፍ:\n"
    "📞 0963572327\n"
    "💬 @SEtoo_9"
)


def format_amharic_prize(amount: float) -> str:
    amt = round(amount, 2)
    if amt >= 1000 and amt % 1000 == 0:
        return f"{int(amt // 1000)} ሺ ብር"
    elif amt.is_integer():
        return f"{int(amt):,} ብር"
    else:
        return f"{amt:,.2f} ብር"


def format_super_bingo_winner_announcement(winners: list[dict], pot: float) -> str:
    """Format the winner announcement post-game with Amharic prize format and split if multiple winners."""
    lines = ["🏆 የዛሬ ሱፐር ቢንጎ አሸናፊ 🏆", ""]
    count = len(winners)
    if count == 0:
        return "🏆 የዛሬ ሱፐር ቢንጎ አሸናፊ 🏆\n\nበዚህ ዙር ምንም አሸናፊ አልተገኘም።"
    share = round(pot / count, 2) if count > 0 else 0.0
    for idx, w in enumerate(winners, start=1):
        name = w.get("name") or "ተጫዋች"
        prize_val = w.get("prize") if w.get("prize") is not None else share
        prize_str = format_amharic_prize(float(prize_val))
        lines.append(f"{idx}. {name} ፡ {prize_str}")
    return "\n".join(lines)

