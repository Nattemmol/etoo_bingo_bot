from telegram import Update
from telegram.ext import ContextTypes

from bot import messages as msg
from bot.config import settings
from bot.handlers.menu import require_registration
from bot.keyboards import play_room_keyboard, webapp_keyboard

from server.game import is_super_bingo_open

ROOMS = {
    "room_play_10": {
        "name": "PLAY",
        "price": 10.0,
        "schedule": "24/7 (All the time)",
    },
    "room_super_50": {
        "name": "superBingo",
        "price": 50.0,
        "schedule": "Daily at 1:00 LT night (7:00 PM EAT)",
    },
}


async def play_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_registration(update):
        return

    await update.message.reply_text(
        msg.PLAY_ROOM_PROMPT,
        reply_markup=play_room_keyboard(),
        parse_mode="Markdown",
    )


async def play_room_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user_data = await require_registration(update)
    if not user_data:
        return

    room_key = query.data
    room = ROOMS.get(room_key)
    if not room:
        await query.edit_message_text("Unknown room.")
        return

    balance = float(user_data["balance"])

    if room_key == "room_super_50":
        is_active = is_super_bingo_open(settings.super_bingo_always_open)
        status_line = "🟢 *Active now!*" if is_active else "⏰ *Runs daily at 1:00 LT night (7:00 PM EAT)*"
        message_text = (
            f"🌟 *{room['name']}* — {room['price']:.0f} ETB\n"
            f"🕒 *Schedule:* {status_line}\n"
            f"💰 *Your Balance:* {balance:.2f} ETB\n\n"
            f"👀 *Watching is free!* You can launch the mini-app and watch the game board at any time.\n"
            f"Card selection ({room['price']:.0f} ETB) opens at 1:00 LT night (7:00 PM EAT).\n\n"
            f"Tap below to open the game:"
        )
    else:
        message_text = (
            f"🎮 *{room['name']}* — {room['price']:.0f} ETB\n"
            f"🕒 *Schedule:* 24/7 (All the time)\n"
            f"💰 *Your Balance:* {balance:.2f} ETB\n\n"
            f"👀 *Watching is free!* You can launch the mini-app and watch the game board at any time.\n"
            f"The {room['price']:.0f} ETB entry fee is only deducted when you choose to select a card to play.\n\n"
            f"Tap below to open the game:"
        )

    await query.edit_message_text(
        message_text,
        reply_markup=webapp_keyboard(settings.webapp_url, room_key),
        parse_mode="Markdown",
    )
