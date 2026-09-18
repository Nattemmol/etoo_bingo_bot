from telegram import Update
from telegram.ext import ContextTypes

from bot import database as db
from bot import messages as msg
from bot.keyboards import main_menu_keyboard, phone_keyboard


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    assert user is not None

    existing = await db.get_user(user.id)
    if existing:
        await update.message.reply_text(
            msg.REGISTERED.format(balance=existing["balance"]),
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )
        await update.message.reply_text(msg.MAIN_MENU, parse_mode="Markdown")
        return

    await update.message.reply_text(
        msg.REGISTRATION_REQUIRED,
        reply_markup=phone_keyboard(),
        parse_mode="Markdown",
    )


async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    contact = update.message.contact
    user = update.effective_user
    assert contact is not None and user is not None

    if contact.user_id != user.id:
        await update.message.reply_text(
            "⚠️ Please share your own phone number.",
            reply_markup=phone_keyboard(),
        )
        return

    existing = await db.get_user(user.id)
    if existing:
        await update.message.reply_text(
            msg.REGISTERED.format(balance=existing["balance"]),
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )
        return

    new_user = await db.create_user(
        telegram_id=user.id,
        phone_number=contact.phone_number,
        username=user.username,
        first_name=user.first_name,
    )

    await update.message.reply_text(
        msg.REGISTERED.format(balance=new_user["balance"]),
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
    await update.message.reply_text(msg.MAIN_MENU, parse_mode="Markdown")
