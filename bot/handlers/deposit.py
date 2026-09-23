"""Deposit handling: SMS receipt / reference text + receipt URL verification.

Accepts any of:
  • Full SMS text from Telebirr, CBE Birr, or CBE Mobile Banking
  • A bare Transaction ID / reference token  (e.g. DIK7W7R5VZ, FT262641DG9X)
  • An official receipt URL (Telebirr, CBE MB, CBE Birr)

Amount is *not* required for receipt-link or ID submissions — we extract it
from the live receipt.  If extraction fails, we ask the user to confirm the
amount separately.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot import database as db
from bot import messages as msg
from bot.handlers.menu import require_registration
from bot.keyboards import deposit_method_keyboard
from bot.sms_parser import verify_deposit_submission, fingerprint

logger = logging.getLogger(__name__)


async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show deposit options."""
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
    """Detect and process pasted SMS text, transaction IDs, or receipt links.

    Returns True if handled as a deposit submission, False otherwise.
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
        # Silently ignore casual text that has no payment signals
        if (
            verification.get("error_type") == "missing_reference"
            and len(raw_text) < 25
            and not any(
                k in raw_text.lower()
                for k in ("birr", "etb", "ብር", "cbe", "telebirr", "ref", "txn", "ft", "http", "di")
            )
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
    amount = verification.get("amount")
    fp = verification["fingerprint"]

    # ── Anti-duplicate checks ─────────────────────────────────────────────
    existing_tx = await db.get_deposit_by_fingerprint(fp)
    if existing_tx:
        await update.message.reply_text(
            msg.DEPOSIT_REUSED.format(amount=existing_tx.get("amount", "?")),
            parse_mode="Markdown",
        )
        return True

    existing_dep = await db.get_peerpay_deposit(reference)
    if existing_dep and existing_dep.get("credited"):
        await update.message.reply_text(
            msg.DEPOSIT_REUSED.format(amount=existing_dep.get("amount", "?")),
            parse_mode="Markdown",
        )
        return True

    # ── If amount is available and receipt was verified — credit immediately ─
    if amount and amount > 0 and verification.get("receipt_verified"):
        method_label = {
            "telebirr": "Telebirr",
            "cbebirr": "CBE Birr",
            "cbe_bank": "CBE Mobile Banking",
        }.get(verification.get("method", ""), "ሌላ")

        credited, new_balance, already_used = await db.auto_credit_deposit(
            telegram_id=user.id,
            amount=amount,
            fingerprint=fp,
            description=f"Deposit via {method_label} | ref:{reference}",
        )

        if already_used:
            await update.message.reply_text(
                msg.DEPOSIT_REUSED.format(amount=amount),
                parse_mode="Markdown",
            )
            return True

        if credited:
            await update.message.reply_text(
                msg.DEPOSIT_AUTO_APPROVED.format(amount=amount, balance=new_balance),
                parse_mode="Markdown",
            )
            return True

    # ── Amount found but receipt not verified via live API ───────────────
    if amount and amount > 0:
        await update.message.reply_text(
            f"⏳ *የክፍያ ማረጋገጫ በመካሄድ ላይ ነው*\n\n"
            f"📋 *የማስረጃ ቁጥር:* `{reference}`\n"
            f"💰 *መጠን:* {amount:.2f} ETB\n\n"
            "ሂሳቡ ማረጋገጫ ካጠናቀቀ በኋላ ወዲያውኑ ይጨምርልዎታል።",
            parse_mode="Markdown",
        )
        return True

    # ── Amount is missing — ask user to provide it ───────────────────────
    context.user_data["pending_deposit_reference"] = reference
    context.user_data["pending_deposit_fp"] = fp
    context.user_data["pending_deposit_method"] = verification.get("method")

    await update.message.reply_text(
        f"✅ *የማስረጃ ቁጥር ተቀብለናል:* `{reference}`\n\n"
        f"💬 ያስተላለፉትን *የብር መጠን* እዚሁ ያስገቡ (ለምሳሌ: `50`)።",
        parse_mode="Markdown",
    )
    return True


async def handle_pending_deposit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle a follow-up amount message after a reference was accepted without an amount.

    Returns True if handled, False otherwise.
    """
    if not update.message or not update.message.text:
        return False

    reference = context.user_data.get("pending_deposit_reference")
    if not reference:
        return False

    raw = update.message.text.strip().replace(",", "")
    try:
        amount = float(raw)
    except ValueError:
        return False

    if amount <= 0 or amount > 1_000_000:
        await update.message.reply_text("❌ *ትክክለኛ የብር መጠን ያስገቡ።*", parse_mode="Markdown")
        return True

    user = update.effective_user
    if not user:
        return False

    fp = context.user_data.get("pending_deposit_fp") or f"ref:{reference}"
    method = context.user_data.get("pending_deposit_method") or "telebirr"

    existing_tx = await db.get_deposit_by_fingerprint(fp)
    if existing_tx:
        await update.message.reply_text(
            msg.DEPOSIT_REUSED.format(amount=existing_tx.get("amount", "?")),
            parse_mode="Markdown",
        )
        context.user_data.pop("pending_deposit_reference", None)
        context.user_data.pop("pending_deposit_fp", None)
        context.user_data.pop("pending_deposit_method", None)
        return True

    method_label = {
        "telebirr": "Telebirr",
        "cbebirr": "CBE Birr",
        "cbe_bank": "CBE Mobile Banking",
    }.get(method, "ሌላ")

    credited, new_balance, already_used = await db.auto_credit_deposit(
        telegram_id=user.id,
        amount=amount,
        fingerprint=fp,
        description=f"Deposit via {method_label} | ref:{reference}",
    )

    context.user_data.pop("pending_deposit_reference", None)
    context.user_data.pop("pending_deposit_fp", None)
    context.user_data.pop("pending_deposit_method", None)

    if already_used:
        await update.message.reply_text(
            msg.DEPOSIT_REUSED.format(amount=amount),
            parse_mode="Markdown",
        )
        return True

    if credited:
        await update.message.reply_text(
            msg.DEPOSIT_AUTO_APPROVED.format(amount=amount, balance=new_balance),
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "❌ ክፍያ መጨመር አልተቻለም። @EtooBingoSupport ን ያነጋግሩ።",
            parse_mode="Markdown",
        )
    return True
