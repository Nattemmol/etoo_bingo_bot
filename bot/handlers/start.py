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
        if context.args and any("super" in a.lower() for a in context.args):
            from bot.keyboards import webapp_keyboard
            from bot.config import settings
            from server.game import is_super_bingo_open

            is_active = is_super_bingo_open(settings.super_bingo_always_open)
            status_line = "🟢 *አሁን ክፍት ነው!*" if is_active else "⏰ *በየቀኑ ማታ 1:00 ሰዓት (7:00 PM EAT)*"
            message_text = (
                f"🌟 *superBingo* — *50 ብር*\n"
                f"🕒 *መርሐግብር:* {status_line}\n"
                f"💰 *የእርስዎ ቀሪ ሂሳብ:* *{float(existing['balance']):.2f} ETB*\n\n"
                f"👀 *የቦርድ እይታ በነፃ ነው!* በማንኛውም ሰዓት ጨዋታውን በቀጥታ መከታተል ይችላሉ።\n"
                f"የ 50 ብር ካርቴላ መምረጫ ማታ 1:00 ሰዓት ይከፈታል።\n\n"
                f"👇 ጨዋታውን ለመክፈት ከታች ያለውን ይጫኑ:"
            )
            await update.message.reply_text(
                message_text,
                reply_markup=webapp_keyboard(settings.webapp_url, "room_super_50"),
                parse_mode="Markdown",
            )
            return

        await update.message.reply_text(msg.MAIN_MENU, parse_mode="Markdown")
        return

    if context.args and any("super" in a.lower() for a in context.args):
        context.user_data["pending_start_arg"] = "super_50"

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
    if context.user_data.get("pending_start_arg") == "super_50":
        context.user_data.pop("pending_start_arg", None)
        from bot.keyboards import webapp_keyboard
        from bot.config import settings
        from server.game import is_super_bingo_open

        is_active = is_super_bingo_open(settings.super_bingo_always_open)
        status_line = "🟢 *አሁን ክፍት ነው!*" if is_active else "⏰ *በየቀኑ ማታ 1:00 ሰዓት (7:00 PM EAT)*"
        message_text = (
            f"🌟 *superBingo* — *50 ብር*\n"
            f"🕒 *መርሐግብር:* {status_line}\n"
            f"💰 *የእርስዎ ቀሪ ሂሳብ:* *{float(new_user['balance']):.2f} ETB*\n\n"
            f"👀 *የቦርድ እይታ በነፃ ነው!* በማንኛውም ሰዓት ጨዋታውን በቀጥታ መከታተል ይችላሉ።\n"
            f"የ 50 ብር ካርቴላ መምረጫ ማታ 1:00 ሰዓት ይከፈታል።\n\n"
            f"👇 ጨዋታውን ለመክፈት ከታች ያለውን ይጫኑ:"
        )
        await update.message.reply_text(
            message_text,
            reply_markup=webapp_keyboard(settings.webapp_url, "room_super_50"),
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text(msg.MAIN_MENU, parse_mode="Markdown")
