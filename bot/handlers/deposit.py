"""Deposit handling with PeerPayment.org integration and SMS receipt parsing."""

import logging
import re
from telegram import Update
from telegram.ext import ContextTypes

from bot import database as db
from bot import messages as msg
from bot.config import settings
from bot.handlers.menu import require_registration
from bot.keyboards import deposit_method_keyboard, peerpay_pay_keyboard
from bot.peerpay import PeerPayClient
from bot.sms_parser import (
    extract_reference_and_url,
    fetch_telebirr_receipt,
    fingerprint,
    parse_deposit_sms,
)

logger = logging.getLogger(__name__)
peerpay_client = PeerPayClient()

# Raw reference regex fallback: e.g. Telebirr alphanumeric or 9+ digits
_REF_FALLBACK_RE = re.compile(r"^[A-Za-z0-9_\-]{6,30}$")


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
        await query.message.reply_text(
            msg.TELEBIRR_DEPOSIT_INSTRUCTIONS,
            parse_mode="Markdown",
        )
    elif data == "deposit_cbebirr":
        await query.message.reply_text(
            msg.CBE_DEPOSIT_INSTRUCTIONS,
            parse_mode="Markdown",
        )
    elif data == "deposit_cbe_bank":
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

    # 1. Parse using SMS parser
    parsed = parse_deposit_sms(raw_text)
    # 1. Parse using SMS parser or URL / reference extractors
    parsed = parse_deposit_sms(raw_text)
    reference = None
    amount = None
    detected_method = "telebirr"

    if parsed and parsed.get("reference"):
        reference = parsed["reference"]
        amount = parsed.get("amount")
    else:
        ref, url, inline_amt = extract_reference_and_url(raw_text)
        if ref:
            reference = ref
            amount = inline_amt
        elif _REF_FALLBACK_RE.match(raw_text):
            reference = raw_text.strip()

    if not reference:
        return False  # Not a recognized transaction receipt

    # 2. If amount is still missing, attempt online receipt lookup (Telebirr)
    if amount is None:
        fetched = await fetch_telebirr_receipt(raw_text)
        if fetched and fetched.get("amount") and fetched.get("status") == "completed":
            amount = fetched["amount"]
            reference = fetched.get("reference") or reference
            detected_method = "telebirr"

    # Determine method from text if possible
    lowered = raw_text.lower()
    if "cbe" in lowered or "commercial bank" in lowered:
        detected_method = "cbebirr"
    elif "telebirr" in lowered or "tele" in lowered:
        detected_method = "telebirr"

    existing_user = await db.get_user(user.id)
    if not existing_user:
        await update.message.reply_text(msg.NOT_REGISTERED)
        return True

    # Check if duplicate receipt
    fp = fingerprint(raw_text) if len(raw_text) > 30 else f"ref:{reference}"
    existing_tx = await db.get_deposit_by_fingerprint(fp)
    if existing_tx:
        await update.message.reply_text(
            msg.DEPOSIT_REUSED.format(amount=existing_tx["amount"]),
            parse_mode="Markdown",
        )
        return True

    # Check peerpay_deposits table
    existing_dep = await db.get_peerpay_deposit(reference)
    if existing_dep and existing_dep.get("credited"):
        await update.message.reply_text(
            msg.DEPOSIT_REUSED.format(amount=existing_dep["amount"]),
            parse_mode="Markdown",
        )
        return True

    # 3. If valid SMS or verified receipt with amount was provided, credit user immediately!
    if amount is not None and amount > 0:
        credited, new_balance, is_dup = await db.credit_peerpay_deposit_once(
            payment_id=reference,
            telegram_id=user.id,
            amount=amount,
        )
        if is_dup:
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
            f"እንኳን ደስ ያልዎ! አሁን Mini App በመክፈት መጫወት ይችላሉ! 🎱",
            parse_mode="Markdown",
        )
        return True

    # Store pending record locally
    await db.upsert_peerpay_deposit(
        payment_id=reference,
        telegram_id=user.id,
        amount=amount or 0.0,
        currency="ETB",
        merchant_order_id=None,
        status="verification_pending",
    )

    # 4. If only bare transaction ID was pasted (no amount in text and offline)
    await update.message.reply_text(
        f"📋 *የግብይት መለያ ቁጥር ተቀብለናል:* `{reference}`\n\n"
        f"⚠️ ሙሉውን የገንዘብ መጠን አረጋግጠን ወዲያውኑ ሂሳብዎ ላይ ለመጨመር እባክዎ ከሚከተሉት አንዱን ይላኩልን፦\n"
        f"1. የደረሰዎትን *ሙሉ የSMS መልእክት*\n"
        f"2. የደረሰኝ ሊንክ (ለምሳሌ፦ `https://transactioninfo.ethiotelecom.et/receipt/{reference}`)\n"
        f"3. ወይም የላኩትን መጠን ከቁጥሩ ጋር አያይዘው (ለምሳሌ፦ `{reference} 50`)",
        parse_mode="Markdown",
    )
    return True
