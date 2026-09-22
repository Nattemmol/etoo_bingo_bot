"""Deposit handling with PeerPayment.org integration and SMS receipt parsing."""

import logging
import uuid
from telegram import Update
from telegram.ext import ContextTypes

from bot import database as db
from bot import messages as msg
from bot.config import settings
from bot.handlers.menu import require_registration
from bot.keyboards import deposit_method_keyboard, peerpay_pay_keyboard
from bot.peerpay import PeerPayClient
from bot.sms_parser import extract_reference_and_url

logger = logging.getLogger(__name__)
peerpay_client = PeerPayClient()


def _checkout_error_text(error: Exception) -> str:
    """Turn known provider setup failures into safe, actionable bot feedback."""
    detail = str(error).strip()
    lowered = detail.lower()
    if "routing" in lowered or "eligible account" in lowered:
        return "No active PeerPayment receiving account is available for this payment method. Please ask the administrator to approve/activate one in the PeerPayment Workspace."
    if "return" in lowered and "domain" in lowered:
        return "The bot's return domain is not allowlisted in PeerPayment. Add the deployed WEBAPP_URL domain in PeerPayment Developer Controls."
    if "unauthor" in lowered or "api key" in lowered or "scope" in lowered:
        return "PeerPayment rejected the server API key. Confirm it is a live key with deposits:create and deposits:read scopes."
    if detail and detail != "Checkout unavailable" and len(detail) <= 180:
        return f"PeerPayment could not create this checkout: {detail}"
    return "PeerPayment did not return a checkout. Confirm the live API key, allowed return domain, and active receiving account."


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
        method = "telebirr"
    elif data == "deposit_cbebirr":
        context.user_data["selected_deposit_method"] = "cbebirr"
        method = "cbebirr"
    elif data == "deposit_cbe_bank":
        context.user_data["selected_deposit_method"] = "cbe_bank"
        method = "cbe"
    else:
        return

    user = update.effective_user
    if not user:
        return
    try:
        # Dynamic checkout keeps payment/account assignment and receipt entry
        # inside PeerPay; this bot never parses a receipt page as proof.
        response = await peerpay_client.create_deposit(
            merchant_customer_id=f"tg_{user.id}",
            return_url=f"{settings.webapp_url}/deposits/return",
            idempotency_key=f"bot-deposit-{user.id}-{uuid.uuid4().hex}",
        )
        data = response.get("data") or {}
        payment_id, checkout_url = data.get("id"), data.get("checkout_url")
        if not payment_id or not checkout_url:
            raise RuntimeError((response.get("error") or {}).get("message", "Checkout unavailable"))
        await db.upsert_peerpay_deposit(payment_id, user.id, 0.0, "ETB", data.get("merchant_order_id"), data.get("status", "created"))
        await db.create_peerpay_deposit_checkout(payment_id, user.id, data.get("merchant_order_id"), checkout_url, method, None, data.get("status", "awaiting_transfer"))
        context.user_data["active_deposit_id"] = payment_id
        context.user_data["active_deposit_method"] = method
        await query.message.reply_text(
            "🔒 *Secure payment checkout created*\n\nOpen the link below, choose the selected payment method, use the assigned receiving account, and submit your transaction ID or official receipt link there. Your balance changes only after PeerPay verifies it.",
            reply_markup=peerpay_pay_keyboard(checkout_url), parse_mode="Markdown",
        )
    except Exception as exc:
        logger.exception("Could not create bot deposit checkout")
        await query.message.reply_text(f"⚠️ {_checkout_error_text(exc)}")


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

    user_data = context.user_data if context else {}
    expected_method = user_data.get("active_deposit_method")
    payment_id = user_data.get("active_deposit_id")
    reference, _, _ = extract_reference_and_url(raw_text)
    if not reference:
        return False

    existing_user = await db.get_user(user.id)
    if not existing_user:
        await update.message.reply_text(msg.NOT_REGISTERED)
        return True

    if not payment_id or not expected_method:
        await update.message.reply_text("Start a secure deposit checkout first, then submit the receipt inside that checkout.")
        return True
    checkout = await db.get_peerpay_deposit_checkout(payment_id, user.id)
    if not checkout:
        await update.message.reply_text("That secure payment order is unavailable. Please start a new deposit.")
        return True
    reserved, error = await db.reserve_peerpay_deposit_reference(payment_id, user.id, reference)
    if not reserved:
        await update.message.reply_text(error)
        return True
    try:
        result = await peerpay_client.submit_deposit_reference(checkout["checkout_url"], reference, expected_method)
    except Exception:
        await update.message.reply_text("⏳ The transaction ID is being reconciled. Do not submit it again.")
        return True
    if result.get("error"):
        await db.release_peerpay_deposit_reference(payment_id, reference)
        await update.message.reply_text((result["error"] or {}).get("message", "Receipt was rejected. Use a new valid transaction ID."))
        return True
    await update.message.reply_text("⏳ Receipt submitted. PeerPay is verifying the receiver, amount, freshness, and one-time use. Your balance remains unchanged until verified success.")
    return True

