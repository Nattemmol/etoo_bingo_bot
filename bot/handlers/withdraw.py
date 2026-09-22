import logging
import uuid
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from bot import database as db
from bot import messages as msg
from bot.config import settings
from bot.handlers.menu import require_registration
from bot.keyboards import withdraw_method_keyboard
from bot.peerpay import PeerPayClient

logger = logging.getLogger(__name__)
peerpay_client = PeerPayClient()

AWAITING_METHOD = 1
AWAITING_AMOUNT = 2
AWAITING_ACCOUNT = 3
MIN_WITHDRAWAL_AMOUNT = 10.0

METHOD_LABELS = {
    "telebirr": "🔵 Telebirr (ቴሌብር)",
    "cbebirr": "🟢 CBE Birr (ሲቢኢ ብር)",
    "cbe_bank": "🏦 Mobile Banking (ንግድ ባንክ)",
}


async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for /withdraw command: show the 3 payout options."""
    if not await require_registration(update):
        return ConversationHandler.END

    text = (
        "💸 *ገንዘብ ማውጣት (Withdrawal)*\n\n"
        "ገንዘብ የሚቀበሉበትን መንገድ ይምረጡ:\n"
        "1️⃣ 🔵 Telebirr (ቴሌብር)\n"
        "2️⃣ 🟢 CBE Birr (ሲቢኢ ብር)\n"
        "3️⃣ 🏦 Mobile Banking (የንግድ ባንክ አካውንት)\n\n"
        "ከታች ካሉት አማራጮች አንዱን ይጫኑ:"
    )
    message = update.message or (update.callback_query.message if update.callback_query else None)
    if message:
        await message.reply_text(
            text,
            reply_markup=withdraw_method_keyboard(),
            parse_mode="Markdown",
        )
    return AWAITING_METHOD


async def withdraw_method_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle payout method button click."""
    query = update.callback_query
    if not query:
        return AWAITING_METHOD
    await query.answer()

    data = query.data or ""
    method = "telebirr"
    if "cbebirr" in data:
        method = "cbebirr"
    elif "cbe_bank" in data:
        method = "cbe_bank"

    context.user_data["withdraw_method"] = method
    method_label = METHOD_LABELS.get(method, method)

    await query.message.reply_text(
        f"✅ የተመረጠው መንገድ: *{method_label}*\n\n"
        f"ማውጣት የሚፈልጉትን መጠን በ ETB ያስገቡ (ዝቅተኛ: {MIN_WITHDRAWAL_AMOUNT:.0f} ETB):",
        parse_mode="Markdown",
    )
    return AWAITING_AMOUNT


async def withdraw_amount_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle amount input for withdrawal."""
    user = update.effective_user
    assert user is not None

    try:
        amount = float(update.message.text.replace(",", ".").strip())
    except (ValueError, AttributeError):
        await update.message.reply_text(msg.INVALID_AMOUNT)
        return AWAITING_AMOUNT

    if amount < MIN_WITHDRAWAL_AMOUNT:
        await update.message.reply_text(
            f"❌ ዝቅተኛው የማውጣት መጠን *{MIN_WITHDRAWAL_AMOUNT:.0f} ETB* ነው።",
            parse_mode="Markdown",
        )
        return AWAITING_AMOUNT

    user_data = await db.get_user(user.id)
    balance = float(user_data["balance"]) if user_data else 0.0

    if amount > balance:
        await update.message.reply_text(
            f"❌ *በቂ ቀሪ ሂሳብ የሎትም!* (ያለዎት: {balance:.2f} ETB)",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    context.user_data["withdraw_amount"] = amount
    method = context.user_data.get("withdraw_method", "telebirr")

    if method == "cbe_bank":
        prompt = "💳 እባክዎ ገንዘቡ የሚላክበትን የ CBE (ንግድ ባንክ) አካውንት ቁጥር ያስገቡ (13 ዲጂት):"
    elif method == "cbebirr":
        prompt = "📱 እባክዎ ገንዘቡ የሚላክበትን የ CBE Birr ስልክ ቁጥር ያስገቡ (ለምሳሌ: 0911223344):"
    else:
        prompt = "📱 እባክዎ ገንዘቡ የሚላክበትን የ Telebirr ስልክ ቁጥር ያስገቡ (ለምሳሌ: 0911223344):"

    await update.message.reply_text(prompt, parse_mode="Markdown")
    return AWAITING_ACCOUNT


async def withdraw_account_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle receiver phone or bank account number input and create withdrawal."""
    user = update.effective_user
    assert user is not None

    account_raw = update.message.text.strip()
    # Normalize account / phone
    clean_account = "".join(c for c in account_raw if c.isdigit())
    if len(clean_account) < 9:
        await update.message.reply_text(
            "⚠️ እባክዎ ትክክለኛ የስልክ ቁጥር ወይም የባንክ አካውንት ቁጥር ያስገቡ።",
            parse_mode="Markdown",
        )
        return AWAITING_ACCOUNT

    amount = float(context.user_data.get("withdraw_amount", 0.0))
    method = context.user_data.get("withdraw_method", "telebirr")
    method_label = METHOD_LABELS.get(method, method)

    user_data = await db.get_user(user.id)
    balance = float(user_data["balance"]) if user_data else 0.0

    if amount <= 0 or amount > balance:
        await update.message.reply_text(msg.INSUFFICIENT_BALANCE)
        return ConversationHandler.END

    new_balance = balance - amount
    payment_id = f"wd_{uuid.uuid4().hex[:12]}"
    peerpay_bank_code = "telebirr" if method == "telebirr" else ("cbebirr" if method == "cbebirr" else "cbe")

    try:
        res = await peerpay_client.create_withdrawal(
            merchant_customer_id=f"tg_{user.id}",
            amount=amount,
            destination={"bank": peerpay_bank_code, "account_number": clean_account},
        )
        wd_data = res.get("data", {})
        if wd_data.get("id"):
            payment_id = wd_data["id"]
        checkout_url = wd_data.get("checkout_url")
        if checkout_url:
            try:
                await peerpay_client.confirm_withdrawal_destination(
                    checkout_token_or_url=checkout_url,
                    bank=peerpay_bank_code,
                    account_number=clean_account,
                )
                logger.info("Confirmed withdrawal destination on checkout API for %s", payment_id)
            except Exception as e:
                logger.warning("Auto-confirm destination error: %s", e)
    except Exception as exc:
        logger.exception("Error creating PeerPay withdrawal: %s", exc)

    # Place reversible hold and update balance
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
        description=f"PeerPay withdrawal hold — {payment_id} ({method_label})",
    )

    await update.message.reply_text(
        (
            "✅ *የገንዘብ ማውጣት ጥያቄዎ በተሳካ ሁኔታ ተመዝግቧል!*\n\n"
            f"💰 መጠን: *{amount:.2f} ETB*\n"
            f"🏦 መንገድ: *{method_label}*\n"
            f"💳 መላኪያ ቁጥር: `{clean_account}`\n"
            f"💳 አዲስ ቀሪ ሂሳብ: *{new_balance:.2f} ETB*\n\n"
            "ጥያቄዎ ወደ PeerPayment ተልኳል። ገንዘቡ እንደተላከ በራስ-ሰር ተረጋግጦ መልዕክት ይደርስዎታል! 🎱"
        ),
        parse_mode="Markdown",
    )

    context.user_data.clear()
    return ConversationHandler.END


async def withdraw_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("የገንዘብ ማውጣት ሂደት ተሰርዟል።")
    return ConversationHandler.END

