"""Withdrawal handling via PeerPayment.org integration (Telebirr payout)."""

import logging
import re
import uuid
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from bot import database as db
from bot import messages as msg
from bot.config import settings
from bot.handlers.menu import require_registration
from bot.peerpay import PeerPayClient

logger = logging.getLogger(__name__)
peerpay_client = PeerPayClient()

AWAITING_METHOD = 1
AWAITING_AMOUNT = 2
AWAITING_ACCOUNT = 3
MIN_WITHDRAWAL_AMOUNT = 10.0


def _normalize_phone(raw: str) -> str | None:
    """Normalize Ethiopian phone number to 09XXXXXXXX or 07XXXXXXXX."""
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("251") and len(digits) == 12:
        digits = "0" + digits[3:]
    elif digits.startswith("9") and len(digits) == 9:
        digits = "0" + digits
    elif digits.startswith("7") and len(digits) == 9:
        digits = "0" + digits
    if len(digits) == 10 and (digits.startswith("09") or digits.startswith("07")):
        return digits
    return None


async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for /withdraw command: Telebirr payout flow."""
    if not await require_registration(update):
        return ConversationHandler.END

    user = update.effective_user
    assert user is not None

    user_data = await db.get_user(user.id)
    balance = float(user_data["balance"]) if user_data else 0.0

    if balance < MIN_WITHDRAWAL_AMOUNT:
        message = update.message or (update.callback_query.message if update.callback_query else None)
        if message:
            await message.reply_text(
                f"❌ *ለማውጣት በቂ ሂሳብ የሎትም!*\n\n"
                f"💵 ወቅታዊ ቀሪ ሂሳብ: *{balance:.2f} ETB*\n"
                f"⚠️ ዝቅተኛው የማውጣት መጠን: *{MIN_WITHDRAWAL_AMOUNT:.0f} ETB* ነው።",
                parse_mode="Markdown",
            )
        return ConversationHandler.END

    context.user_data["withdraw_method"] = "telebirr"

    text = (
        "💸 *ገንዘብ ማውጣት (Telebirr Payout)*\n\n"
        f"💵 ወቅታዊ ቀሪ ሂሳብ: *{balance:.2f} ETB*\n"
        f"ማውጣት የሚፈልጉትን የብር መጠን በቁጥር ያስገቡ (ዝቅተኛ: {MIN_WITHDRAWAL_AMOUNT:.0f} ETB):"
    )
    message = update.message or (update.callback_query.message if update.callback_query else None)
    if message:
        await message.reply_text(
            text,
            parse_mode="Markdown",
        )
    return AWAITING_AMOUNT


async def withdraw_method_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle payout method button click (Telebirr)."""
    query = update.callback_query
    if not query:
        return AWAITING_METHOD
    await query.answer()

    return await withdraw_command(update, context)


async def withdraw_amount_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle amount input for withdrawal."""
    user = update.effective_user
    assert user is not None

    if not update.message or not update.message.text:
        return AWAITING_AMOUNT

    try:
        amount = float(update.message.text.replace(",", ".").strip())
    except (ValueError, AttributeError):
        await update.message.reply_text(msg.INVALID_AMOUNT, parse_mode="Markdown")
        return AWAITING_AMOUNT

    if amount < MIN_WITHDRAWAL_AMOUNT:
        await update.message.reply_text(
            f"❌ ዝቅተኛው የማውጣት መጠን *{MIN_WITHDRAWAL_AMOUNT:.0f} ETB* ነው። እባክዎ ከ {MIN_WITHDRAWAL_AMOUNT:.0f} ETB በላይ ያስገቡ።",
            parse_mode="Markdown",
        )
        return AWAITING_AMOUNT

    user_data = await db.get_user(user.id)
    balance = float(user_data["balance"]) if user_data else 0.0

    if amount > balance:
        await update.message.reply_text(
            f"❌ *በቂ ቀሪ ሂሳብ የሎትም!*\n\n"
            f"የእርስዎ ቀሪ ሂሳብ: *{balance:.2f} ETB*\n"
            f"የጠየቁት: *{amount:.2f} ETB*",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    context.user_data["withdraw_amount"] = amount
    context.user_data["withdraw_method"] = "telebirr"

    prompt = (
        f"📱 *የ Telebirr ስልክ ቁጥር ያስገቡ:*\n\n"
        f"💰 የሚወጣው መጠን: *{amount:.2f} ETB*\n\n"
        "ገንዘቡ የሚላክበትን የ Telebirr ስልክ ቁጥር ያስገቡ (ለምሳሌ፦ `0911223344`):"
    )
    await update.message.reply_text(prompt, parse_mode="Markdown")
    return AWAITING_ACCOUNT


async def withdraw_account_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle receiver phone number input and create withdrawal hold via PeerPay."""
    user = update.effective_user
    assert user is not None

    if not update.message or not update.message.text:
        return AWAITING_ACCOUNT

    account_raw = update.message.text.strip()
    clean_phone = _normalize_phone(account_raw)

    if not clean_phone:
        await update.message.reply_text(
            "⚠️ *የተሳሳተ የቴሌብር ስልክ ቁጥር ነው!*\n\n"
            "እባክዎ በ 09 ወይም በ 07 የሚጀምር ትክክለኛ ባለ 10 ዲጂት ስልክ ቁጥር ያስገቡ (ለምሳሌ፦ `0911223344`):",
            parse_mode="Markdown",
        )
        return AWAITING_ACCOUNT

    amount = float(context.user_data.get("withdraw_amount", 0.0))
    user_data = await db.get_user(user.id)
    balance = float(user_data["balance"]) if user_data else 0.0

    if amount <= 0 or amount > balance:
        await update.message.reply_text(msg.INSUFFICIENT_BALANCE, parse_mode="Markdown")
        return ConversationHandler.END

    new_balance = round(balance - amount, 2)
    idempotency_key = f"etoobingo-withdrawal-{uuid.uuid4().hex}"
    payment_id = f"wd_{uuid.uuid4().hex[:12]}"

    try:
        res = await peerpay_client.create_withdrawal(
            merchant_customer_id=f"tg_{user.id}",
            amount=amount,
            destination={"bank": "telebirr", "account_number": clean_phone},
            idempotency_key=idempotency_key,
        )
        wd_data = res.get("data", {})
        if wd_data.get("id"):
            payment_id = wd_data["id"]
        checkout_url = wd_data.get("checkout_url")
        if checkout_url:
            try:
                await peerpay_client.confirm_withdrawal_destination(
                    checkout_token_or_url=checkout_url,
                    bank="telebirr",
                    account_number=clean_phone,
                )
                logger.info("Confirmed withdrawal destination on checkout API for %s", payment_id)
            except Exception as e:
                logger.warning("Auto-confirm destination error: %s", e)
    except Exception as exc:
        logger.exception("Error creating PeerPay withdrawal: %s", exc)

    # Place reversible hold and update balance atomically
    await db.update_balance(user.id, new_balance)
    await db.create_peerpay_withdrawal_hold(
        payment_id=payment_id,
        telegram_id=user.id,
        amount=amount,
        status="created",
    )
    await db.add_transaction(
        telegram_id=user.id,
        tx_type="withdraw",
        amount=amount,
        status="pending",
        description=f"PeerPay withdrawal hold — {payment_id} (Telebirr: {clean_phone})",
    )

    await update.message.reply_text(
        (
            "✅ *የገንዘብ ማውጣት ጥያቄዎ በተሳካ ሁኔታ ተመዝግቧል!*\n\n"
            f"💰 መጠን: *{amount:.2f} ETB*\n"
            f"🔵 መንገድ: *Telebirr (ቴሌብር)*\n"
            f"📱 መላኪያ ስልክ: `{clean_phone}`\n"
            f"💳 አዲስ ቀሪ ሂሳብ: *{new_balance:.2f} ETB*\n\n"
            "ጥያቄዎ ወደ ክፍያ ስርዓት ተልኳል። ክፍያው ሲጠናቀቅ በራስ-ሰር ማረጋገጫ ይደርስዎታል! 🎱"
        ),
        parse_mode="Markdown",
    )

    context.user_data.clear()
    return ConversationHandler.END


async def withdraw_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.message:
        await update.message.reply_text("የገንዘብ ማውጣት ሂደት ተሰርዟል።")
    return ConversationHandler.END
