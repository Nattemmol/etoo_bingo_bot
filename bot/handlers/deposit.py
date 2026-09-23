"""Deposit handling via PeerPayment.org integration.

All payments are verified authoritatively through PeerPay:
  • Creates secure hosted checkouts with assigned merchant receiving accounts.
  • Submits transaction references directly to PeerPay bank verification engine.
  • Prevents fake, repeated, and wrong-receiver transactions.
  • Fulfills balance exactly once on authoritative 'succeeded' status or signed webhook.
"""

import logging
import re
import uuid
from telegram import Update
from telegram.ext import ContextTypes

from bot import database as db
from bot import messages as msg
from bot.config import settings
from bot.handlers.menu import require_registration
from bot.keyboards import (
    deposit_amount_keyboard,
    deposit_method_keyboard,
    peerpay_pay_keyboard,
)
from bot.peerpay import PeerPayClient, extract_token_from_url

logger = logging.getLogger(__name__)
peerpay_client = PeerPayClient()

METHOD_LABELS = {
    "telebirr": "🔵 Telebirr (ቴሌብር)",
    "cbebirr": "🟢 CBE Birr (ሲቢኢ ብር)",
    "cbe_bank": "🏦 Mobile Banking (ንግድ ባንክ)",
}

MIN_DEPOSIT_AMOUNT = 10.0


def _extract_reference_token(text: str) -> str | None:
    """Extract bare transaction ID or token from text or URL."""
    if not text:
        return None
    clean = text.strip()

    # 1. Telebirr receipt URL
    m = re.search(r"transactioninfo\.ethiotelecom\.et/receipt/([A-Za-z0-9_-]+)", clean, re.I)
    if m:
        return m.group(1)

    # 2. CBE Mobile Banking URL
    m = re.search(r"mbreciept\.cbe\.com\.et/([A-Za-z0-9_-]+)", clean, re.I)
    if m:
        return m.group(1)

    # 3. CBE Branch Receipt URL
    m = re.search(r"apps\.cbe\.com\.et(?::\d+)?/BranchReceipt/(FT[0-9A-Z]+)", clean, re.I)
    if m:
        return m.group(1)

    # 4. Bare Telebirr ID (DI...)
    m = re.search(r"\b(DI[A-Z0-9]{8})\b", clean, re.I)
    if m:
        return m.group(1)

    # 5. Bare CBE FT Reference (FT...)
    m = re.search(r"\b(FT[0-9A-Z]{8,16})\b", clean, re.I)
    if m:
        return m.group(1)

    # 6. Generic reference keywords
    m = re.search(
        r"(?:txn|tx|ref|reference|መለያ|ቁጥር)\s*[:#\-]?\s*([A-Za-z0-9_\-]{6,30})",
        clean,
        re.I,
    )
    if m:
        return m.group(1)

    # 7. Single token of length 8-25
    if re.match(r"^[A-Za-z0-9_\-]{8,30}$", clean):
        return clean

    return None


async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show deposit method selection."""
    if not await require_registration(update):
        return

    text = (
        "💳 *ገንዘብ ማስገቢያ (Deposit via PeerPay)*\n\n"
        "ክፍያ የሚፈጽሙበትን መንገድ ይምረጡ:\n"
        "1️⃣ 🔵 Telebirr (ቴሌብር)\n"
        "2️⃣ 🟢 CBE Birr (ሲቢኢ ብር)\n"
        "3️⃣ 🏦 Mobile Banking (የንግድ ባንክ)\n\n"
        "ከታች ካሉት አማራጮች አንዱን ይጫኑ:"
    )
    message = update.message or (update.callback_query.message if update.callback_query else None)
    if message:
        await message.reply_text(
            text,
            reply_markup=deposit_method_keyboard(webapp_url=settings.webapp_url),
            parse_mode="Markdown",
        )


async def deposit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle deposit method selection buttons."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data or ""
    method = "telebirr"
    if data == "deposit_cbebirr":
        method = "cbebirr"
    elif data == "deposit_cbe_bank":
        method = "cbe_bank"

    context.user_data["selected_deposit_method"] = method
    method_label = METHOD_LABELS.get(method, method)

    await query.message.reply_text(
        f"✅ የተመረጠው መንገድ: *{method_label}*\n\n"
        "💰 *የሚያስገቡትን የብር መጠን ይምረጡ ወይም በቁጥር ይጻፉ:*\n"
        f"(ዝቅተኛ: {MIN_DEPOSIT_AMOUNT:.0f} ETB)",
        reply_markup=deposit_amount_keyboard(method),
        parse_mode="Markdown",
    )


async def deposit_amount_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle quick deposit amount buttons."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data or ""  # e.g. "dep_amt_telebirr_50" or "dep_amt_telebirr_custom"
    parts = data.split("_")
    if len(parts) < 4:
        return

    method = parts[2]
    amt_str = parts[3]

    if amt_str == "custom":
        context.user_data["awaiting_custom_deposit_amount"] = True
        context.user_data["selected_deposit_method"] = method
        await query.message.reply_text(
            f"✏️ ማስገባት የሚፈልጉትን የብር መጠን በቁጥር ይጻፉ (ለምሳሌ: `150`):",
            parse_mode="Markdown",
        )
        return

    try:
        amount = float(amt_str)
    except ValueError:
        amount = 50.0

    await _create_and_send_peerpay_checkout(query.message, update.effective_user.id, amount, method, context)


async def _create_and_send_peerpay_checkout(
    message, telegram_id: int, amount: float, method: str, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Create a PeerPay deposit checkout and send interactive checkout buttons."""
    method_label = METHOD_LABELS.get(method, method)
    peerpay_method_code = "telebirr" if method == "telebirr" else ("cbebirr" if method == "cbebirr" else "cbe")
    idempotency_key = f"etoobingo-deposit-{uuid.uuid4().hex}"

    try:
        # Create open/variable-amount checkout so PeerPay accepts any transferred amount
        res = await peerpay_client.create_deposit(
            merchant_customer_id=f"tg_{telegram_id}",
            amount=None,
            payment_method=peerpay_method_code,
            idempotency_key=idempotency_key,
        )
        dep_data = res.get("data", {})
        deposit_id = dep_data.get("id")
        checkout_url = dep_data.get("checkout_url")

        if not deposit_id or not checkout_url:
            err = res.get("error", {})
            err_msg = err.get("message", "የክፍያ ማስፈንጠሪያ ማዘጋጀት አልተቻለም።")
            await message.reply_text(f"❌ *ስህተት:* {err_msg}", parse_mode="Markdown")
            return

        # Store in database and context
        await db.upsert_peerpay_deposit(
            payment_id=deposit_id,
            telegram_id=telegram_id,
            amount=amount,
            currency="ETB",
            merchant_order_id=dep_data.get("merchant_order_id"),
            status=dep_data.get("status", "awaiting_transfer"),
        )
        context.user_data["active_deposit_id"] = deposit_id
        context.user_data["active_checkout_url"] = checkout_url
        context.user_data["active_deposit_method"] = method

        text = (
            "🔐 *የተረጋገጠ የክፍያ ማስፈንጠሪያ ተዘጋጅቷል (PeerPay)*\n\n"
            f"💰 መጠን: *{amount:.2f} ETB*\n"
            f"🏦 መንገድ: *{method_label}*\n\n"
            "📌 *ቀጣይ እርምጃ:*\n"
            "1. ከታች ያለውን **'💳 በ PeerPay ክፈሉ'** የሚለውን ይጫኑ።\n"
            "2. በሚከፈተው ገጽ ላይ የተሰጠውን አካውንት ኮፒ አድርገው ይክፈሉ።\n"
            "3. ክፍያው ሲጠናቀቅ የደረሰዎትን **Transaction ID** በገጹ ላይ ያስገቡ።\n"
            "4. PeerPay ክፍያውን ወዲያውኑ ከባንክ አረጋግጦ ወደ አካውንትዎ ይጨምራል! 🎱"
        )
        await message.reply_text(
            text,
            reply_markup=peerpay_pay_keyboard(checkout_url=checkout_url, deposit_id=deposit_id),
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.exception("Error creating PeerPay deposit: %s", exc)
        await message.reply_text(
            "❌ የክፍያ ማስፈንጠሪያ ማዘጋጀት አልተቻለም። እባክዎ ከጥቂት ደቂቃዎች በኋላ እንደገና ይሞክሩ።",
            parse_mode="Markdown",
        )


async def deposit_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle status check button click on a deposit."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data or ""
    if not data.startswith("dep_status_"):
        return

    deposit_id = data[len("dep_status_") :]
    user = update.effective_user
    assert user is not None

    try:
        res = await peerpay_client.get_deposit(deposit_id)
        dep_data = res.get("data", {})
        status = dep_data.get("status", "unknown")
        amount = float(dep_data.get("amount") or 0.0)

        if status == "succeeded":
            credited, new_balance, is_dup = await db.credit_peerpay_deposit_once(
                payment_id=deposit_id,
                telegram_id=user.id,
                amount=amount,
                merchant_order_id=dep_data.get("merchant_order_id"),
            )
            await query.message.reply_text(
                (
                    "✅ *ክፍያዎ በተሳካ ሁኔታ ተረጋግጧል!*\n\n"
                    f"💰 የተጨመረ መጠን: *{amount:.2f} ETB*\n"
                    f"💳 ወቅታዊ ቀሪ ሂሳብ: *{new_balance:.2f} ETB*\n\n"
                    "አሁን በ /play ጨዋታ መጀመር ይችላሉ! 🎱"
                ),
                parse_mode="Markdown",
            )
        elif status in ("verification_pending", "reference_submitted"):
            await query.message.reply_text(
                "⏳ *የክፍያ ማረጋገጫ በሂደት ላይ ነው*\n\n"
                "Transaction ID ተቀብለን ከባንክ በማረጋገጥ ላይ ነን። እንደተጠናቀቀ በራስ-ሰር ይጨመርልዎታል።",
                parse_mode="Markdown",
            )
        elif status == "awaiting_transfer":
            checkout_url = dep_data.get("checkout_url") or ""
            await query.message.reply_text(
                "⚠️ *ክፍያው ገና አልተጠናቀቀም!*\n\n"
                "እባክዎ መጀመሪያ ወደ ተሰጠው አካውንት ከፍለው Transaction ID በ PeerPay ገጽ ላይ ያስገቡ።",
                reply_markup=peerpay_pay_keyboard(checkout_url=checkout_url, deposit_id=deposit_id) if checkout_url else None,
                parse_mode="Markdown",
            )
        else:
            await query.message.reply_text(
                f"ℹ️ የግብይት ሁኔታ: *{status}*",
                parse_mode="Markdown",
            )
    except Exception as exc:
        logger.exception("Error checking deposit status: %s", exc)
        await query.message.reply_text("❌ የክፍያ ሁኔታ ማረጋገጥ አልተቻለም።", parse_mode="Markdown")


async def handle_sms_or_reference_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Detect and process pasted SMS text, transaction IDs, or receipt links via PeerPay.

    Submits the transaction reference directly to PeerPay for live bank verification.
    """
    if not update.message or not update.message.text:
        return False

    raw_text = update.message.text.strip()
    if raw_text.startswith("/"):
        return False

    user = update.effective_user
    if not user:
        return False

    # Check for custom deposit amount reply
    if context.user_data.get("awaiting_custom_deposit_amount"):
        try:
            amt = float(raw_text.replace(",", ".").strip())
            if amt < MIN_DEPOSIT_AMOUNT:
                await update.message.reply_text(
                    f"❌ ዝቅተኛው የማስገቢያ መጠን *{MIN_DEPOSIT_AMOUNT:.0f} ETB* ነው።",
                    parse_mode="Markdown",
                )
                return True
            context.user_data.pop("awaiting_custom_deposit_amount", None)
            method = context.user_data.get("selected_deposit_method", "telebirr")
            await _create_and_send_peerpay_checkout(update.message, user.id, amt, method, context)
            return True
        except ValueError:
            pass

    ref = _extract_reference_token(raw_text)
    if not ref:
        return False

    # Check if this reference was already credited in our database
    existing_dep = await db.get_peerpay_deposit(ref)
    if existing_dep and existing_dep.get("credited"):
        await update.message.reply_text(
            msg.DEPOSIT_REUSED.format(amount=existing_dep.get("amount", "?")),
            parse_mode="Markdown",
        )
        return True

    existing_tx = await db.get_deposit_by_fingerprint(f"ref:{ref}")
    if existing_tx:
        await update.message.reply_text(
            msg.DEPOSIT_REUSED.format(amount=existing_tx.get("amount", "?")),
            parse_mode="Markdown",
        )
        return True

    deposit_id = context.user_data.get("active_deposit_id")
    checkout_url = context.user_data.get("active_checkout_url")
    method = context.user_data.get("active_deposit_method", "telebirr")

    # If no active session, create a deposit with PeerPay
    if not deposit_id or not checkout_url:
        peerpay_method = "telebirr" if ref.startswith("DI") else "cbe"
        idempotency_key = f"etoobingo-deposit-{uuid.uuid4().hex}"
        try:
            create_res = await peerpay_client.create_deposit(
                merchant_customer_id=f"tg_{user.id}",
                payment_method=peerpay_method,
                idempotency_key=idempotency_key,
            )
            dep_data = create_res.get("data", {})
            deposit_id = dep_data.get("id")
            checkout_url = dep_data.get("checkout_url")
            if deposit_id:
                await db.upsert_peerpay_deposit(
                    payment_id=deposit_id,
                    telegram_id=user.id,
                    amount=0.0,
                    currency="ETB",
                    merchant_order_id=dep_data.get("merchant_order_id"),
                    status=dep_data.get("status", "created"),
                )
        except Exception as exc:
            logger.warning("Error auto-creating deposit for reference: %s", exc)

    if not deposit_id or not checkout_url:
        await update.message.reply_text(
            "⚠️ እባክዎ መጀመሪያ /deposit በማለት የክፍያ መንገድና መጠን ይምረጡ።",
            parse_mode="Markdown",
        )
        return True

    # Submit reference to PeerPay
    await update.message.reply_text("⏳ *ክፍያዎን በ PeerPayment በኩል በማረጋገጥ ላይ ነን...*", parse_mode="Markdown")

    try:
        verify_res = await peerpay_client.submit_and_verify_reference(
            deposit_id=deposit_id,
            checkout_url=checkout_url,
            reference=ref,
            payment_method=method,
        )

        if not verify_res.get("ok"):
            err_msg = verify_res.get("error", "የተሳሳተ ወይም የተደገመ የግብይት ቁጥር ነው!")
            await update.message.reply_text(
                f"❌ *ክፍያው አልተረጋገጠም!*\n\n{err_msg}\n\nእባክዎ ትክክለኛውን Transaction ID እንደገና ያስገቡ።",
                parse_mode="Markdown",
            )
            return True

        status = verify_res.get("status")
        amount = verify_res.get("amount") or 0.0

        if status == "succeeded":
            credited, new_balance, is_dup = await db.credit_peerpay_deposit_once(
                payment_id=deposit_id,
                telegram_id=user.id,
                amount=amount,
            )
            if credited:
                await update.message.reply_text(
                    msg.DEPOSIT_AUTO_APPROVED.format(amount=amount, balance=new_balance),
                    parse_mode="Markdown",
                )
            elif is_dup:
                await update.message.reply_text(
                    msg.DEPOSIT_REUSED.format(amount=amount),
                    parse_mode="Markdown",
                )
        else:
            await update.message.reply_text(
                (
                    "⏳ *የግብይት ቁጥርዎ ተቀብለናል!*\n\n"
                    f"📋 የማስረጃ ቁጥር: `{ref}`\n\n"
                    "PeerPay ከባንክ በማረጋገጥ ላይ ነው። ማረጋገጫው እንደተጠናቀቀ ወዲያውኑ ሂሳብዎ ላይ ይጨመራል! 🎱"
                ),
                parse_mode="Markdown",
            )
        return True
    except Exception as exc:
        logger.exception("Error verifying reference with PeerPay: %s", exc)
        await update.message.reply_text(
            "❌ ማረጋገጫውን ማጠናቀቅ አልተቻለም። እባክዎ ከጥቂት ደቂቃዎች በኋላ እንደገና ይሞክሩ።",
            parse_mode="Markdown",
        )
        return True


async def handle_pending_deposit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """No-op kept for backwards compatibility."""
    return False
