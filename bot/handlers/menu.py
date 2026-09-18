from telegram import Update
from telegram.ext import ContextTypes

from bot import database as db
from bot import messages as msg


async def require_registration(update: Update) -> dict | None:
    user = update.effective_user
    assert user is not None

    existing = await db.get_user(user.id)
    if not existing:
        message = update.message or update.callback_query.message
        await message.reply_text(msg.NOT_REGISTERED)
        return None
    return existing


async def instructions_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_registration(update):
        return
    await update.message.reply_text(msg.INSTRUCTIONS, parse_mode="Markdown")


async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_data = await require_registration(update)
    if not user_data:
        return
    await update.message.reply_text(
        msg.BALANCE.format(balance=user_data["balance"]),
        parse_mode="Markdown",
    )


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    assert user is not None

    if not await require_registration(update):
        return

    transactions = await db.get_transactions(user.id)
    if not transactions:
        await update.message.reply_text(msg.NO_HISTORY)
        return

    lines = [msg.HISTORY_HEADER]
    for tx in transactions:
        sign = "+" if tx["type"] in ("deposit", "win") else "-"
        lines.append(
            f"{sign}{tx['amount']:.2f} ETB — {tx['type']} ({tx['status']})\n"
            f"  _{tx['created_at']}_\n"
        )

    await update.message.reply_text("".join(lines), parse_mode="Markdown")
