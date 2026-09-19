from telegram import Update
from telegram.ext import ContextTypes

from bot import database as db
from bot import messages as msg
from bot.keyboards import (
    balance_action_keyboard,
    instructions_back_keyboard,
    instructions_keyboard,
)


async def require_registration(update: Update) -> dict | None:
    user = update.effective_user
    assert user is not None

    existing = await db.get_user(user.id)
    if not existing:
        message = update.message or (update.callback_query.message if update.callback_query else None)
        if message:
            await message.reply_text(msg.NOT_REGISTERED, parse_mode="Markdown")
        return None
    return existing


async def instructions_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_registration(update):
        return
    message = update.message or (update.callback_query.message if update.callback_query else None)
    if message:
        await message.reply_text(
            msg.INSTRUCTIONS_MENU,
            reply_markup=instructions_keyboard(),
            parse_mode="Markdown",
        )


async def instructions_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data or ""
    if data == "inst_10":
        await query.edit_message_text(
            msg.INSTRUCTIONS_10,
            reply_markup=instructions_back_keyboard(),
            parse_mode="Markdown",
        )
    elif data == "inst_50":
        await query.edit_message_text(
            msg.INSTRUCTIONS_50,
            reply_markup=instructions_back_keyboard(),
            parse_mode="Markdown",
        )
    elif data == "inst_general":
        await query.edit_message_text(
            msg.INSTRUCTIONS_GENERAL,
            reply_markup=instructions_back_keyboard(),
            parse_mode="Markdown",
        )
    elif data == "inst_back":
        await query.edit_message_text(
            msg.INSTRUCTIONS_MENU,
            reply_markup=instructions_keyboard(),
            parse_mode="Markdown",
        )


async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_data = await require_registration(update)
    if not user_data:
        return
    message = update.message or (update.callback_query.message if update.callback_query else None)
    if message:
        await message.reply_text(
            msg.BALANCE.format(balance=user_data["balance"]),
            reply_markup=balance_action_keyboard(),
            parse_mode="Markdown",
        )


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    assert user is not None

    if not await require_registration(update):
        return

    transactions = await db.get_transactions(user.id)
    if not transactions:
        await update.message.reply_text(msg.NO_HISTORY, parse_mode="Markdown")
        return

    type_labels = {
        "deposit": "💳 ገቢ (Deposit)",
        "withdrawal": "💸 ወጪ (Withdrawal)",
        "win": "🏆 አሸናፊነት (Game Win)",
        "card_buy": "🎱 የካርቴላ መግዣ (Card Purchase)",
        "refund": "↩️ ተመላሽ ገንዘብ (Refund)",
    }

    lines = [msg.HISTORY_HEADER]
    for tx in transactions[:15]:
        ttype = tx.get("type", "transaction")
        tlabel = type_labels.get(ttype, ttype)
        sign = "+" if ttype in ("deposit", "win", "refund") else "-"
        date_str = str(tx.get("created_at", ""))[:19]
        lines.append(
            f"{sign}*{tx['amount']:.2f} ETB* — {tlabel}\n"
            f"  📅 `{date_str}` | ሁኔታ: _{tx.get('status', 'completed')}_\n\n"
        )

    await update.message.reply_text("".join(lines), parse_mode="Markdown")
