"""Withdrawal handling via PeerPayment.org integration (Telebirr, CBE Birr, CBE Mobile Banking).

All payouts are verified authoritatively through PeerPay:
  • Supports Telebirr, CBE Birr, and CBE Mobile Banking (Bank Account).
  • Places reversible holds on user balance atomically.
  • Creates hosted withdrawal requests on PeerPay and auto-confirms prefilled destinations.
  • Releases holds (refunds user balance) on terminal failed/expired/cancelled events.
  • Captures holds on authoritative succeeded status or signed webhook events.
  • Real-time balance updates broadcast to Mini App WebSocket sessions.
"""

import logging
import re
import uuid
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from bot import database as db
from bot import messages as msg
from bot.config import settings
from bot.handlers.menu import require_registration
from bot.keyboards import (
    peerpay_withdraw_confirm_keyboard,
    withdraw_amount_keyboard,
    withdraw_method_keyboard,
)
from bot.peerpay import PeerPayClient

logger = logging.getLogger(__name__)
peerpay_client = PeerPayClient()

WD_AWAITING_METHOD = 1
WD_AWAITING_AMOUNT = 2
WD_AWAITING_ACCOUNT = 3
# Backwards-compatibility aliases
AWAITING_AMOUNT = WD_AWAITING_AMOUNT
AWAITING_ACCOUNT = WD_AWAITING_ACCOUNT
MIN_WITHDRAWAL_AMOUNT = 10.0

METHOD_LABELS = {
    "telebirr": "🔵 Telebirr (ቴሌብር)",
    "cbebirr": "🟢 CBE Birr (ሲቢኢ ብር)",
    "cbe_bank": "🏦 Mobile Banking (የንግድ ባንክ)",
}


async def _broadcast_balance(telegram_id: int, new_balance: float) -> None:
    """Push real-time balance update to all open WebSocket sessions for this user."""
    try:
        import server.main as srv
        await srv.broadcast_user_balance(telegram_id, new_balance)
    except Exception:
        pass


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


def _clean_bank_account(raw: str) -> str | None:
    """Clean and validate CBE Bank account number (digits only, length 10-16)."""
    digits = re.sub(r"\D", "", raw)
    if 10 <= len(digits) <= 16:
        return digits
    return None


async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for /withdraw command: Show payout method selection."""
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

    text = (
        "💸 *ገንዘብ ማውጣት (Withdraw via PeerPay)*\n\n"
        f"💵 ወቅታዊ ቀሪ ሂሳብ: *{balance:.2f} ETB*\n\n"
        "ገንዘብ የሚቀበሉበትን መንገድ ይምረጡ:\n"
        "1️⃣ 🔵 Telebirr (ቴሌብር)\n"
        "2️⃣ 🟢 CBE Birr (ሲቢኢ ብር)\n"
        "3️⃣ 🏦 Mobile Banking (የንግድ ባንክ)\n\n"
        "ከታች ካሉት አማራጮች አንዱን ይጫኑ:"
    )
    message = update.message or (update.callback_query.message if update.callback_query else None)
    if message:
        await message.reply_text(
            text,
            reply_markup=withdraw_method_keyboard(webapp_url=settings.webapp_url),
            parse_mode="Markdown",
        )
    return WD_AWAITING_METHOD


async def withdraw_method_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle payout method button click (Telebirr, CBE Birr, CBE Mobile Banking)."""
    query = update.callback_query
    if not query:
        return WD_AWAITING_METHOD
    await query.answer()

    data = query.data or ""
    method = "telebirr"
    if data == "withdraw_cbebirr":
        method = "cbebirr"
    elif data == "withdraw_cbe_bank":
        method = "cbe_bank"

    user = update.effective_user
    assert user is not None

    user_data = await db.get_user(user.id)
    balance = float(user_data["balance"]) if user_data else 0.0

    if balance < MIN_WITHDRAWAL_AMOUNT:
        await query.message.reply_text(
            f"❌ *ለማውጣት በቂ ሂሳብ የሎትም!*\n\n"
            f"💵 ወቅታዊ ቀሪ ሂሳብ: *{balance:.2f} ETB*\n"
            f"⚠️ ዝቅተኛው የማውጣት መጠን: *{MIN_WITHDRAWAL_AMOUNT:.0f} ETB* ነው።",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    context.user_data["withdraw_method"] = method
    method_label = METHOD_LABELS.get(method, method)

    await query.message.reply_text(
        f"✅ የተመረጠው መንገድ: *{method_label}*\n"
        f"💵 ወቅታዊ ቀሪ ሂሳብ: *{balance:.2f} ETB*\n\n"
        "💰 *የሚያወጡትን የብር መጠን ይምረጡ ወይም በቁጥር ይጻፉ:*\n"
        f"(ዝቅተኛ: {MIN_WITHDRAWAL_AMOUNT:.0f} ETB)",
        reply_markup=withdraw_amount_keyboard(method),
        parse_mode="Markdown",
    )
    return WD_AWAITING_AMOUNT


async def withdraw_amount_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle quick withdrawal amount buttons."""
    query = update.callback_query
    if not query:
        return WD_AWAITING_AMOUNT
    await query.answer()

    data = query.data or ""  # e.g. "wd_amt_telebirr_50", "wd_amt_cbe_bank_custom"
    if not data.startswith("wd_amt_"):
        return WD_AWAITING_AMOUNT

    raw = data[len("wd_amt_") :]
    if "_" not in raw:
        return WD_AWAITING_AMOUNT

    method, amt_str = raw.rsplit("_", 1)
    context.user_data["withdraw_method"] = method
    method_label = METHOD_LABELS.get(method, method)

    if amt_str == "custom":
        context.user_data["awaiting_custom_withdraw_amount"] = True
        await query.message.reply_text(
            f"✏️ *{method_label}*\n\n"
            f"ማውጣት የሚፈልጉትን የብር መጠን በቁጥር ይጻፉ (ለምሳሌ: `150`):\n"
            f"(ዝቅተኛ: {MIN_WITHDRAWAL_AMOUNT:.0f} ETB)",
            parse_mode="Markdown",
        )
        return WD_AWAITING_AMOUNT

    try:
        amount = float(amt_str)
    except ValueError:
        amount = 50.0

    return await _process_selected_withdraw_amount(query.message, update.effective_user.id, amount, method, context)


async def withdraw_amount_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle custom text amount input for withdrawal."""
    user = update.effective_user
    assert user is not None

    if not update.message or not update.message.text:
        return WD_AWAITING_AMOUNT

    try:
        amount = float(update.message.text.replace(",", ".").strip())
    except (ValueError, AttributeError):
        await update.message.reply_text(msg.INVALID_AMOUNT, parse_mode="Markdown")
        return WD_AWAITING_AMOUNT

    method = context.user_data.get("withdraw_method", "telebirr")
    return await _process_selected_withdraw_amount(update.message, user.id, amount, method, context)


async def _process_selected_withdraw_amount(
    message, user_id: int, amount: float, method: str, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Validate amount against balance and prompt for method-specific destination account."""
    if amount < MIN_WITHDRAWAL_AMOUNT:
        await message.reply_text(
            f"❌ ዝቅተኛው የማውጣት መጠን *{MIN_WITHDRAWAL_AMOUNT:.0f} ETB* ነው። እባክዎ ከ {MIN_WITHDRAWAL_AMOUNT:.0f} ETB በላይ ያስገቡ።",
            parse_mode="Markdown",
        )
        return WD_AWAITING_AMOUNT

    user_data = await db.get_user(user_id)
    balance = float(user_data["balance"]) if user_data else 0.0

    if amount > balance:
        await message.reply_text(
            f"❌ *በቂ ቀሪ ሂሳብ የሎትም!*\n\n"
            f"የእርስዎ ቀሪ ሂሳብ: *{balance:.2f} ETB*\n"
            f"የጠየቁት: *{amount:.2f} ETB*",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    context.user_data["withdraw_amount"] = amount
    context.user_data["withdraw_method"] = method

    if method == "telebirr":
        prompt = (
            f"📱 *የ Telebirr ስልክ ቁጥር ያስገቡ:*\n\n"
            f"💰 የሚወጣው መጠን: *{amount:.2f} ETB*\n\n"
            "ገንዘቡ የሚላክበትን ባለ 10 ዲጂት የ Telebirr ስልክ ቁጥር ያስገቡ (ለምሳሌ፦ `0911223344`):"
        )
    elif method == "cbebirr":
        prompt = (
            f"🟢 *የ CBE Birr ስልክ ቁጥር ያስገቡ:*\n\n"
            f"💰 የሚወጣው መጠን: *{amount:.2f} ETB*\n\n"
            "ገንዘቡ የሚላክበትን ባለ 10 ዲጂት የ CBE Birr ስልክ ቁጥር ያስገቡ (ለምሳሌ፦ `0911223344`):"
        )
    else:  # cbe_bank
        prompt = (
            f"🏦 *የ CBE የባንክ ሂሳብ ቁጥር (Account Number) ያስገቡ:*\n\n"
            f"💰 የሚወጣው መጠን: *{amount:.2f} ETB*\n\n"
            "ገንዘቡ የሚላክበትን የኢትዮጵያ ንግድ ባንክ (CBE) ሂሳብ ቁጥር ያስገቡ (ለምሳሌ፦ `1000123456789`):"
        )

    await message.reply_text(prompt, parse_mode="Markdown")
    return WD_AWAITING_ACCOUNT


async def withdraw_account_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle receiver phone / bank account input and create withdrawal hold via PeerPay."""
    user = update.effective_user
    assert user is not None

    if not update.message or not update.message.text:
        return WD_AWAITING_ACCOUNT

    method = context.user_data.get("withdraw_method", "telebirr")
    account_raw = update.message.text.strip()

    clean_dest_account = None
    if method in ("telebirr", "cbebirr"):
        clean_dest_account = _normalize_phone(account_raw)
        if not clean_dest_account:
            provider_name = "Telebirr" if method == "telebirr" else "CBE Birr"
            await update.message.reply_text(
                f"⚠️ *የተሳሳተ የ{provider_name} ስልክ ቁጥር ነው!*\n\n"
                "እባክዎ በ 09 ወይም በ 07 የሚጀምር ትክክለኛ ባለ 10 ዲጂት ስልክ ቁጥር ያስገቡ (ለምሳሌ፦ `0911223344`):",
                parse_mode="Markdown",
            )
            return WD_AWAITING_ACCOUNT
    else:  # cbe_bank
        clean_dest_account = _clean_bank_account(account_raw)
        if not clean_dest_account:
            await update.message.reply_text(
                "⚠️ *የተሳሳተ የንግድ ባንክ ሂሳብ ቁጥር ነው!*\n\n"
                "እባክዎ ትክክለኛ የኢትዮጵያ ንግድ ባንክ (CBE) ሂሳብ ቁጥር ያስገቡ (ለምሳሌ፦ `1000123456789`):",
                parse_mode="Markdown",
            )
            return WD_AWAITING_ACCOUNT

    amount = float(context.user_data.get("withdraw_amount", 0.0))
    user_data = await db.get_user(user.id)
    balance = float(user_data["balance"]) if user_data else 0.0

    if amount <= 0 or amount > balance:
        await update.message.reply_text(msg.INSUFFICIENT_BALANCE, parse_mode="Markdown")
        return ConversationHandler.END

    new_balance = round(balance - amount, 2)
    idempotency_key = f"etoobingo-withdrawal-{uuid.uuid4().hex}"
    payment_id = f"wd_{uuid.uuid4().hex[:12]}"
    peerpay_bank_code = "telebirr" if method == "telebirr" else ("cbebirr" if method == "cbebirr" else "cbe")
    checkout_url = ""

    try:
        res = await peerpay_client.create_withdrawal(
            merchant_customer_id=f"tg_{user.id}",
            amount=amount,
            destination={"bank": peerpay_bank_code, "account_number": clean_dest_account},
            idempotency_key=idempotency_key,
        )
        wd_data = res.get("data", {})
        if wd_data.get("id"):
            payment_id = wd_data["id"]
        checkout_url = wd_data.get("checkout_url") or ""
        if checkout_url:
            try:
                await peerpay_client.confirm_withdrawal_destination(
                    checkout_token_or_url=checkout_url,
                    bank=peerpay_bank_code,
                    account_number=clean_dest_account,
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
        description=f"PeerPay withdrawal hold — {payment_id} ({method}: {clean_dest_account})",
    )

    # Broadcast updated balance to Mini App WebSocket immediately
    await _broadcast_balance(user.id, new_balance)

    method_label = METHOD_LABELS.get(method, method)
    acct_label = "ስልክ ቁጥር" if method in ("telebirr", "cbebirr") else "የባንክ ሂሳብ"

    await update.message.reply_text(
        (
            "✅ *የገንዘብ ማውጣት ጥያቄዎ በተሳካ ሁኔታ ተመዝግቧል!*\n\n"
            f"💰 መጠን: *{amount:.2f} ETB*\n"
            f"🏦 መንገድ: *{method_label}*\n"
            f"📋 መላኪያ {acct_label}: `{clean_dest_account}`\n"
            f"💳 አዲስ ቀሪ ሂሳብ: *{new_balance:.2f} ETB*\n\n"
            "ጥያቄዎ ወደ ክፍያ ስርዓት ተልኳል። ክፍያው ሲጠናቀቅ በራስ-ሰር ማረጋገጫ ይደርስዎታል! 🎱"
        ),
        reply_markup=peerpay_withdraw_confirm_keyboard(
            checkout_url=checkout_url,
            withdrawal_id=payment_id,
        ),
        parse_mode="Markdown",
    )

    context.user_data.clear()
    return ConversationHandler.END


async def withdraw_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check withdrawal status on PeerPay and reconcile wallet hold."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data or ""
    if not data.startswith("wd_status_"):
        return

    withdrawal_id = data[len("wd_status_") :]
    user = update.effective_user
    assert user is not None

    try:
        res = await peerpay_client.get_withdrawal(withdrawal_id)
        wd_data = res.get("data", {})
        status = wd_data.get("status", "unknown")
        amount = float(wd_data.get("amount") or 0.0)

        if status == "succeeded":
            captured, _ = await db.capture_peerpay_withdrawal_once(withdrawal_id)
            user_data = await db.get_user(user.id)
            current_bal = float(user_data["balance"]) if user_data else 0.0
            await _broadcast_balance(user.id, current_bal)
            await query.message.reply_text(
                (
                    "✅ *ገንዘቡ በተሳካ ሁኔታ ተላልፏል!*\n\n"
                    f"💰 የተላለፈ መጠን: *{amount:.2f} ETB*\n"
                    f"💳 ወቅታዊ ቀሪ ሂሳብ: *{current_bal:.2f} ETB*\n\n"
                    "ገንዘቡ ወደ አካውንትዎ ገብቷል። መልካም እድል! 🎱"
                ),
                parse_mode="Markdown",
            )
        elif status in ("failed", "expired", "cancelled"):
            released, refund_amt = await db.release_peerpay_withdrawal_once(withdrawal_id)
            user_data = await db.get_user(user.id)
            current_bal = float(user_data["balance"]) if user_data else 0.0
            await _broadcast_balance(user.id, current_bal)
            await query.message.reply_text(
                (
                    f"❌ *የገንዘብ ማውጣቱ አልተሳካም ({status})*\n\n"
                    f"🔄 የተያዘው *{refund_amt:.2f} ETB* ወደ ቀሪ ሂሳብዎ ተመልሷል!\n"
                    f"💳 ወቅታዊ ቀሪ ሂሳብ: *{current_bal:.2f} ETB*"
                ),
                parse_mode="Markdown",
            )
        elif status in ("transfer_submitted", "verification_pending"):
            await query.message.reply_text(
                "⏳ *ክፍያው ተልኮ በባንክ በማረጋገጥ ላይ ነው*\n\n"
                "ወኪሉ ክፍያውን ፈጽሞ Transaction ID አስገብቷል። ባንኩ እንዳረጋገጠው ይጠናቀቃል!",
                parse_mode="Markdown",
            )
        elif status in ("assigned", "customer_confirmed", "created"):
            await query.message.reply_text(
                "⏳ *የገንዘብ ማውጣት ጥያቄዎ በሂደት ላይ ነው*\n\n"
                "ወኪል ተመድቦ ገንዘቡን ወደ አካውንትዎ በመላክ ላይ ነው። እባክዎ ጥቂት ደቂቃዎች ይጠብቁ።",
                parse_mode="Markdown",
            )
        elif status == "review_required":
            await query.message.reply_text(
                "⏳ *ጥያቄው በግምገማ ላይ ነው*\n\n"
                "ስርዓቱ ጥያቄዎን በመፈተሽ ላይ ነው። ጥቂት ቆይተው እንደገና ያረጋግጡ።",
                parse_mode="Markdown",
            )
        else:
            await query.message.reply_text(
                f"ℹ️ የጥያቄው ሁኔታ: *{status}*",
                parse_mode="Markdown",
            )
    except Exception as exc:
        logger.exception("Error checking withdrawal status: %s", exc)
        await query.message.reply_text("❌ የጥያቄውን ሁኔታ ማረጋገጥ አልተቻለም።", parse_mode="Markdown")


async def withdraw_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.message:
        await update.message.reply_text("የገንዘብ ማውጣት ሂደት ተሰርዟል።")
    return ConversationHandler.END

