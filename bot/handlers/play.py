from telegram import Update
from telegram.ext import ContextTypes

from bot import messages as msg
from bot.config import settings
from bot.handlers.menu import require_registration
from bot.keyboards import deposit_method_keyboard, play_room_keyboard, webapp_keyboard, withdraw_method_keyboard

from server.game import is_super_bingo_open

ROOMS = {
    "room_play_10": {
        "name": "PLAY",
        "price": 10.0,
        "schedule": "24/7 (ሁልጊዜ ክፍት)",
    },
    "room_super_50": {
        "name": "superBingo",
        "price": 50.0,
        "schedule": "በየቀኑ ማታ 1:00 ሰዓት (7:00 PM EAT)",
    },
}


async def play_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_registration(update):
        return

    message = update.message or (update.callback_query.message if update.callback_query else None)
    if message:
        await message.reply_text(
            msg.PLAY_ROOM_PROMPT,
            reply_markup=play_room_keyboard(),
            parse_mode="Markdown",
        )


async def play_room_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    user_data = await require_registration(update)
    if not user_data:
        return

    room_key = query.data
    room = ROOMS.get(room_key)
    if not room:
        await query.edit_message_text("❌ ያልታወቀ የጨዋታ ክፍል (Unknown room)።")
        return

    balance = float(user_data["balance"])

    if room_key == "room_super_50":
        is_active = is_super_bingo_open(settings.super_bingo_always_open)
        status_line = "🟢 *አሁን ክፍት ነው!*" if is_active else "⏰ *በየቀኑ ማታ 1:00 ሰዓት (7:00 PM EAT)*"
        message_text = (
            f"🌟 *{room['name']}* — *{room['price']:.0f} ብር*\n"
            f"🕒 *መርሐግብር:* {status_line}\n"
            f"💰 *የእርስዎ ቀሪ ሂሳብ:* *{balance:.2f} ETB*\n\n"
            f"👀 *የቦርድ እይታ በነፃ ነው!* በማንኛውም ሰዓት ጨዋታውን በቀጥታ መከታተል ይችላሉ።\n"
            f"የ 50 ብር ካርቴላ መምረጫ ማታ 1:00 ሰዓት ይከፈታል።\n\n"
            f"👇 ጨዋታውን ለመክፈት ከታች ያለውን ይጫኑ:"
        )
    else:
        message_text = (
            f"🎱 *{room['name']}* — *{room['price']:.0f} ብር*\n"
            f"🕒 *መርሐግብር:* 24/7 (ሁልጊዜ ክፍት — በየ 30 ሰከንዱ አዲስ ዙር)\n"
            f"💰 *የእርስዎ ቀሪ ሂሳብ:* *{balance:.2f} ETB*\n\n"
            f"👀 *የቦርድ እይታ በነፃ ነው!* ማንኛውም ሰው ያለምንም ክፍያ ጨዋታውን መከታተል ይችላል።\n"
            f"የ 10 ብር የመግቢያ ክፍያ የሚቆረጠው ካርቴላ መርጠው ሲያረጋግጡ ብቻ ነው።\n\n"
            f"👇 ጨዋታውን ለመክፈት ከታች ያለውን ይጫኑ:"
        )

    await query.edit_message_text(
        message_text,
        reply_markup=webapp_keyboard(settings.webapp_url, room_key),
        parse_mode="Markdown",
    )


async def action_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle action buttons from balance and instruction menus."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data or ""
    if data == "btn_action_play":
        await query.message.reply_text(
            msg.PLAY_ROOM_PROMPT,
            reply_markup=play_room_keyboard(),
            parse_mode="Markdown",
        )
    elif data == "btn_action_deposit":
        await query.message.reply_text(
            msg.DEPOSIT_METHOD_PROMPT,
            reply_markup=deposit_method_keyboard(),
            parse_mode="Markdown",
        )
    elif data == "btn_action_withdraw":
        await query.message.reply_text(
            "💸 *ገንዘብ ማውጣት (Telebirr Payout)*\n\n"
            "ገንዘብ ማውጣት የሚፈልጉትን የብር መጠን በ /withdraw በኩል ያስገቡ (ዝቅተኛ: 10 ETB)።",
            reply_markup=withdraw_method_keyboard(),
            parse_mode="Markdown",
        )
