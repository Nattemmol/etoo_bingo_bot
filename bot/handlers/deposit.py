"""Deposit handling with PeerPayment.org integration and SMS receipt parsing."""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from bot import database as db
from bot import messages as msg
from bot.handlers.menu import require_registration
from bot.keyboards import deposit_method_keyboard
from bot.sms_parser import verify_deposit_submission

logger = logging.getLogger(__name__)


async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show deposit options or prompt to paste SMS receipt."""
    if not await require_registration(update):
        return

    message = update.message or (update.callback_query.message if update.callback_query else None)
    if message:
        await message.reply_text(
            msg.DEPOSIT_METHOD_PROMPT,
            reply_markup=deposit_method_keyboard(),
            parse_mode="Markdown",
        )


async def deposit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle deposit method selection buttons."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data or ""
    if data == "deposit_telebirr":
        context.user_data["selected_deposit_method"] = "telebirr"
        await query.message.reply_text(
            msg.TELEBIRR_DEPOSIT_INSTRUCTIONS,
            parse_mode="Markdown",
        )
    elif data == "deposit_cbebirr":
        context.user_data["selected_deposit_method"] = "cbebirr"
        await query.message.reply_text(
            msg.CBE_DEPOSIT_INSTRUCTIONS,
            parse_mode="Markdown",
        )
    elif data == "deposit_cbe_bank":
        context.user_data["selected_deposit_method"] = "cbe_bank"
        await query.message.reply_text(
            msg.MOBILE_BANKING_DEPOSIT_INSTRUCTIONS,
            parse_mode="Markdown",
        )


async def handle_sms_or_reference_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Detect and process pasted SMS transaction receipts or reference tokens.

    Returns True if handled as a deposit SMS/reference, False otherwise.
    """
    if not update.message or not update.message.text:
        return False

    raw_text = update.message.text.strip()
    if raw_text.startswith("/"):
        return False  # Command, not a deposit text

    user = update.effective_user
    if not user:
        return False

    expected_method = context.user_data.get("selected_deposit_method")
    verification = await verify_deposit_submission(raw_text, expected_method=expected_method)

    if not verification["valid"]:
        # If user just sent casual text without any payment keywords or references, ignore
        if (
            verification.get("error_type") == "missing_reference"
            and len(raw_text) < 25
            and not any(k in raw_text.lower() for k in ("birr", "etb", "ብር", "cbe", "telebirr", "ref", "txn", "ft"))
        ):
            return False

        await update.message.reply_text(
            verification["error_message"],
            parse_mode="Markdown",
        )
        return True

    existing_user = await db.get_user(user.id)
    if not existing_user:
        await update.message.reply_text(msg.NOT_REGISTERED)
        return True

    reference = verification["reference"]
    amount = verification["amount"]
    fp = verification["fingerprint"]

    # Anti-duplicate checks (Check fingerprint and reference across transactions and deposits)
    existing_tx = await db.get_deposit_by_fingerprint(fp)
    if existing_tx:
        await update.message.reply_text(
            msg.DEPOSIT_REUSED.format(amount=existing_tx["amount"]),
            parse_mode="Markdown",
        )
        return True

    existing_dep = await db.get_peerpay_deposit(reference)
    if existing_dep and existing_dep.get("credited"):
        await update.message.reply_text(
            msg.DEPOSIT_REUSED.format(amount=existing_dep["amount"]),
            parse_mode="Markdown",
        )
        return True

    # Credit user balance atomically
    credited, new_balance, is_dup = await db.credit_peerpay_deposit_once(
        payment_id=reference,
        telegram_id=user.id,
        amount=amount,
    )
    if is_dup or not credited:
        await update.message.reply_text(
            msg.DEPOSIT_REUSED.format(amount=amount),
            parse_mode="Markdown",
        )
        return True

    await update.message.reply_text(
        f"✅ *ክፍያዎ በተሳካ ሁኔታ ተረጋግጧል! (Deposit Approved)*\n\n"
        f"📋 የማስረጃ ቁጥር: `{reference}`\n"
        f"💰 የተጨመረ መጠን: *{amount:.2f} ETB*\n"
        f"💳 አጠቃላይ ሂሳብዎ: *{new_balance:.2f} ETB*\n\n"
        f"እንኳን ደስ ያልዎ! አሁን በ /play ወይም Mini App በመክፈት መጫወት ይችላሉ! 🎱",
        parse_mode="Markdown",
    )
    context.user_data.pop("selected_deposit_method", None)
    return True

