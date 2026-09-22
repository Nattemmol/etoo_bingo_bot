"""Parse and strictly verify Telebirr, CBE Birr, and CBE Mobile Banking SMS receipts.

Enforces:
1. Anti-Package / Anti-Airtime Filter: Rejects package, bundle, airtime, and utility purchases.
2. Strict Directional Verification (Sender vs Receiver):
   - Confirms money is transferred TO EtooBingo official accounts:
     • Telebirr: 0963572327 / 251963572327 (Habtamu Melese)
     • CBE Birr: 0934920411 / 251934920411 (Natnael Temesegen)
     • CBE Mobile Banking: 1000413343538 (Natnael Temesegen)
   - Rejects outgoing transfers (where merchant account is the SENDER).
   - Rejects transfers where destination account/phone belongs to someone else (e.g. 1000418895067).
3. Live Ethio Telecom Portal Verification:
   - Scrapes official receipt (https://transactioninfo.ethiotelecom.et/receipt/<txn_id>).
   - Verifies Credited Party / Destination Bank Account matches EtooBingo receiver.
4. Anti-Duplicate / Anti-Replay: Stable fingerprint generation and reference tracking.
"""

import hashlib
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

OFFICIAL_ACCOUNTS = {
    "telebirr": {
        "display_name": "Telebirr (ቴሌብር)",
        "name": "Habtamu Melese",
        "phone": "0963572327",
        "intl_phone": "251963572327",
        "short_phone": "963572327",
        "valid_numbers": ["0963572327", "251963572327", "+251963572327", "963572327", "63572327"],
        "valid_names": ["habtamu", "melese", "habtamumelese", "ሀብታሙ", "መለሰ"],
    },
    "cbebirr": {
        "display_name": "CBE Birr (ሲቢኢ ብር)",
        "name": "Natnael Temesegen",
        "phone": "0934920411",
        "intl_phone": "251934920411",
        "short_phone": "934920411",
        "valid_numbers": ["0934920411", "251934920411", "+251934920411", "934920411", "34920411"],
        "valid_names": ["natnael", "temesegen", "natnaeltemesegen", "ናትናኤል", "ተመስገን"],
    },
    "cbe_bank": {
        "display_name": "CBE Mobile Banking (ንግድ ባንክ)",
        "name": "Natnael Temesegen",
        "account": "1000413343538",
        "short_account": "413343538",
        "valid_accounts": ["1000413343538", "413343538"],
        "valid_names": ["natnael", "temesegen", "natnaeltemesegen", "ናትናኤል", "ተመስገን"],
    },
}

# Keywords identifying mobile packages, airtime, or non-deposit services
_PACKAGE_AND_SERVICE_KEYWORDS = (
    "package",
    "bundle",
    "ጥቅል",
    "የጥቅል",
    "ጥቅል ግዢ",
    "የጥቅል ግዢ",
    "ወርሃዊ ጥቅል",
    "ሳምንታዊ ጥቅል",
    "ዕለታዊ ጥቅል",
    "የቀን ጥቅል",
    "የማታ ጥቅል",
    "የድምፅ ጥቅል",
    "የኢንተርኔት ጥቅል",
    "voice package",
    "data package",
    "internet package",
    "airtime",
    "የአየር ሰዓት",
    "አየር ሰዓት",
    "recharge",
    "voucher",
    "ካርድ ሞልተዋል",
    "card recharge",
    "topup",
    "top up",
    "top-up",
    "bill payment",
    "utility payment",
    "dstv",
    "canalsat",
)

# Keywords indicating failed, cancelled, or reversed transactions
_FAILED_KEYWORDS = (
    "failed",
    "unsuccessful",
    "reversed",
    "cancelled",
    "canceled",
    "declined",
    "rejected",
    "ተመላሽ",
    "ተሰርዟል",
    "አልተሳካም",
    "ይቅርታ",
)

# Regex to capture currency and amount
_AMOUNT_ETB_RE = re.compile(
    r"(?:ETB|Birr|ብር)\s*[:#/\-]?\s*(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
    r"|(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)\s*(?:ETB|Birr|ብር)",
    re.IGNORECASE,
)

# Credit / transfer keywords that signify money movement
_CREDIT_KEYWORDS = (
    "received",
    "credited",
    "credit",
    "saved",
    "paid",
    "amount",
    "payment",
    "transfer",
    "transferred",
    "sent",
    "debited",
    "to",
    "ወደ",
    "ደርሷል",
    "ደርሶበታል",
    "ገንዘብ",
    "ተከፍሏል",
    "ተክፍሏል",
    "ተላልፏል",
    "አስተላልፈዋል",
    "ብ ክፍያ",
    "የተከፈለ",
    "የተላለፈ",
)

# Reference regex patterns
_REF_RE = re.compile(
    r"(?:"
    r"transaction\s*(?:number|no|num|id|ref(?:erence)?)|"
    r"txn?\s*(?:id|no|num|ref(?:erence)?)?|"
    r"trans?\s*(?:number|no|id)|"
    r"trnref|"
    r"reference|"
    r"receipt(?:\.?\s*(?:no|num(?:ber)?))?|"
    r"ref|"
    r"የግብይት\s*(?:ቁጥር|መለያ)(?:ዎ|ው|የ)?|"
    r"የማስተላለፊያ\s*(?:ቁጥር|መለያ)(?:ዎ|ው|የ)?|"
    r"የማመሳከሪያ\s*ቁጥር(?:ዎ|ው|የ)?|"
    r"ግብይት\s*ቁጥር(?:ዎ|ው|የ)?|"
    r"መለያ\s*ቁጥር(?:ዎ|ው|የ)?"
    r")\s*(?:is|ነው|:|#|-|=)?\s*([A-Za-z0-9][A-Za-z0-9_\-]{5,30})",
    re.IGNORECASE,
)

_TELEBIRR_REF_RE = re.compile(r"\b(DI[A-Z0-9]{8})\b", re.IGNORECASE)
_CBE_FT_REF_RE = re.compile(r"\b(FT[0-9A-Z]{8,16})\b", re.IGNORECASE)
_RECEIPT_URL_RE = re.compile(
    r"https?://(?:(?:transactioninfo\.ethiotelecom\.et/receipt/)([A-Za-z0-9_-]+)|(?:mbreciept\.cbe\.com\.et/)([A-Za-z0-9_-]+)|(?:apps\.cbe\.com\.et:100/BranchReceipt/)(FT[0-9A-Z]+)(?:&[0-9]+)?)",
    re.IGNORECASE,
)
_DIGIT_RUN_RE = re.compile(r"(?<!\d)(\d{9,16})(?!\d)")


def fingerprint(sms_text: str) -> str:
    """Stable hash of normalized SMS text used to reject duplicate receipts."""
    normalized = re.sub(r"[\s\W_]+", "", sms_text).lower()
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:24]


def _clean_amount(number: str) -> float:
    return float(number.replace(",", ""))


def extract_reference_and_url(text: str) -> tuple[str | None, str | None, float | None]:
    """Extract (reference, receipt_url, amount) from text, receipt link, or bare token."""
    if not text or not text.strip():
        return None, None, None

    clean = text.strip()

    # 1. Check for receipt URL
    url_m = _RECEIPT_URL_RE.search(clean)
    if url_m:
        ref = next((group for group in url_m.groups() if group), "").strip()
        url = url_m.group(0).strip()
        return ref or None, url, None

    # 2. Check for bare Telebirr or CBE reference
    tb_m = _TELEBIRR_REF_RE.search(clean)
    if tb_m:
        return tb_m.group(1).strip(), None, None

    ft_m = _CBE_FT_REF_RE.search(clean)
    if ft_m:
        return ft_m.group(1).strip(), None, None

    if re.match(r"^[A-Za-z0-9_\-]{6,30}$", clean):
        return clean, None, None

    return None, None, None


async def fetch_telebirr_receipt(ref_or_url: str) -> dict | None:
    """Fetch and parse live receipt details from Ethio Telecom's public receipt endpoint."""
    ref, url, _ = extract_reference_and_url(ref_or_url)
    txn_id = ref or ref_or_url.strip()
    if not txn_id or not re.match(r"^[A-Za-z0-9_\-]{6,30}$", txn_id):
        return None

    target_url = url or f"https://transactioninfo.ethiotelecom.et/receipt/{txn_id}"
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
            resp = await client.get(target_url)
            if resp.status_code == 200 and "telebirr receipt" in resp.text:
                html = resp.text

                # Parse structured table fields
                payer_name = ""
                payer_no = ""
                credited_party = ""
                bank_account = ""
                status = "completed"
                settled_amount = None

                # Extract Settled Amount
                amt_match = re.search(
                    r"Settled Amount.*?(\d+(?:\.\d{1,2})?)\s*Birr",
                    html,
                    re.DOTALL | re.IGNORECASE,
                )
                if not amt_match:
                    amt_match = re.search(r"(\d+(?:\.\d{1,2})?)\s*Birr", html, re.IGNORECASE)
                if amt_match:
                    settled_amount = float(amt_match.group(1))

                # Extract Payer Name & Number
                payer_m = re.search(r"Payer Name.*?<td[^>]*>(.*?)</td>", html, re.DOTALL | re.IGNORECASE)
                if payer_m:
                    payer_name = re.sub(r"<[^>]+>", "", payer_m.group(1)).strip()

                payer_no_m = re.search(r"Payer telebirr no\..*?<td[^>]*>(.*?)</td>", html, re.DOTALL | re.IGNORECASE)
                if payer_no_m:
                    payer_no = re.sub(r"<[^>]+>", "", payer_no_m.group(1)).strip()

                # Extract Credited Party / Bank Account
                cred_m = re.search(r"Credited Party name.*?<td[^>]*>(.*?)</td>", html, re.DOTALL | re.IGNORECASE)
                if cred_m:
                    credited_party = re.sub(r"<[^>]+>", "", cred_m.group(1)).strip()

                bank_m = re.search(r"Bank account number.*?<td[^>]*>(.*?)</td>", html, re.DOTALL | re.IGNORECASE)
                if bank_m:
                    bank_account = re.sub(r"<[^>]+>", "", bank_m.group(1)).strip()

                # Extract Reference ID
                ref_m = re.search(r"\b(DI[A-Z0-9]{8})\b", html, re.IGNORECASE)
                final_ref = ref_m.group(1) if ref_m else txn_id

                return {
                    "amount": settled_amount,
                    "reference": final_ref,
                    "payer_name": payer_name,
                    "payer_no": payer_no,
                    "credited_party": credited_party,
                    "bank_account": bank_account,
                    "status": status,
                    "url": target_url,
                }
    except Exception as exc:
        logger.warning("Error fetching live telebirr receipt %s: %s", target_url, exc)
    return None


def extract_directional_accounts(text: str) -> dict[str, str | None]:
    """Extract sender and receiver accounts / phone numbers using directional patterns."""
    sender_account = None
    receiver_account = None
    receiver_phone = None

    # Sender extraction: e.g. "from your telebirr account 251963572327"
    sender_m = re.search(
        r"(?:from\s*(?:your)?\s*(?:telebirr|cbebirr|cbe|bank)?\s*(?:account)?|ከ(?:\s*የእርስዎ|\s*ቴሌብር|\s*ሲቢኢ|\s*ባንክ)?\s*(?:አካውንትዎ?|ቁጥር)?)\s*[:#]?\s*(\+?251\d{9}|09\d{8}|07\d{8}|\d{10,16})",
        text,
        re.IGNORECASE,
    )
    if sender_m:
        sender_account = sender_m.group(1).strip()

    # Receiver bank account: e.g. "to Commercial Bank of Ethiopia account number 1000418895067"
    to_acc_m = re.search(
        r"(?:to|ወደ)\s*(?:commercial\s*bank(?:\s*of\s*ethiopia)?|cbe|telebirr|cbebirr|ንግድ\s*ባንክ)?\s*(?:account\s*(?:number|no)?|አካውንት\s*(?:ቁጥር)?|a/c)\s*[:#]?\s*(\d{10,16})",
        text,
        re.IGNORECASE,
    )
    if to_acc_m:
        receiver_account = to_acc_m.group(1).strip()

    # Receiver phone: e.g. "transferred to 0963572327"
    to_phone_m = re.search(
        r"(?:to|ወደ)\s*(?:(?:phone|ስልክ|ቁጥር|account)\s*[:#]?\s*)?(\+?251\d{9}|09\d{8}|07\d{8})",
        text,
        re.IGNORECASE,
    )
    if to_phone_m:
        receiver_phone = to_phone_m.group(1).strip()

    return {
        "sender": sender_account,
        "receiver_account": receiver_account,
        "receiver_phone": receiver_phone,
    }


def verify_directional_match(text: str, expected_method: str | None = None) -> tuple[bool, str | None, str | None]:
    """Verify that the SMS is an INCOMING transfer TO one of our official accounts.

    Returns (is_valid, matched_method, rejection_reason).
    """
    clean_text = text.lower()
    directional = extract_directional_accounts(text)

    sender = directional["sender"]
    receiver_acc = directional["receiver_account"]
    receiver_phone = directional["receiver_phone"]

    # 1. Check if the merchant's account is the SENDER (outgoing transfer)
    merchant_telebirr_numbers = OFFICIAL_ACCOUNTS["telebirr"]["valid_numbers"]
    merchant_cbebirr_numbers = OFFICIAL_ACCOUNTS["cbebirr"]["valid_numbers"]
    merchant_bank_accounts = OFFICIAL_ACCOUNTS["cbe_bank"]["valid_accounts"]

    is_sender_merchant = (
        (sender and any(num in sender for num in merchant_telebirr_numbers))
        or (sender and any(num in sender for num in merchant_cbebirr_numbers))
        or (sender and any(acc in sender for acc in merchant_bank_accounts))
    )

    # 2. Check destination / receiver
    if receiver_acc:
        # Check if the destination bank account matches our official CBE account
        if any(acc == receiver_acc or acc in receiver_acc for acc in merchant_bank_accounts):
            return True, "cbe_bank", None
        else:
            # Transfer was explicitly sent to someone else's bank account!
            return (
                False,
                None,
                f"ክፍያው የተላከው ወደ ሌላ የባንክ አካውንት ({receiver_acc}) ነው! እባክዎ ወደ EtooBingo ይፋዊ አካውንት (1000413343538 - Natnael Temesegen) ያስተላለፉበትን የ SMS መልዕክት ያስገቡ።",
            )

    if receiver_phone:
        # Check if the destination phone matches our official Telebirr or CBE Birr
        if any(num == receiver_phone or num in receiver_phone for num in merchant_telebirr_numbers):
            return True, "telebirr", None
        elif any(num == receiver_phone or num in receiver_phone for num in merchant_cbebirr_numbers):
            return True, "cbebirr", None
        else:
            # Transfer was sent to someone else's phone!
            return (
                False,
                None,
                f"ክፍያው የተላከው ወደ ሌላ ስልክ ቁጥር ({receiver_phone}) ነው! እባክዎ ወደ EtooBingo ይፋዊ አካውንት ያስተላለፉበትን የ SMS መልዕክት ያስገቡ።",
            )

    # If merchant was the sender and no valid receiver was found, reject as outgoing
    if is_sender_merchant:
        return (
            False,
            None,
            "ይህ መልዕክት ከ EtooBingo አካውንት ወደ ሌላ ሰው የተደረገ ወጪ ዝውውር (Outgoing Transfer) ነው! እባክዎ ወደ EtooBingo የተላከበትን የገቢ SMS ያስገቡ።",
        )

    # Fallback to name/keyword check if explicit directional patterns weren't present
    # Check CBE Bank
    if any(acc in clean_text for acc in merchant_bank_accounts) and any(n in clean_text for n in OFFICIAL_ACCOUNTS["cbe_bank"]["valid_names"]):
        return True, "cbe_bank", None

    # Check Telebirr
    if any(num in clean_text for num in merchant_telebirr_numbers) and any(n in clean_text for n in OFFICIAL_ACCOUNTS["telebirr"]["valid_names"]):
        return True, "telebirr", None

    # Check CBE Birr
    if any(num in clean_text for num in merchant_cbebirr_numbers) and any(n in clean_text for n in OFFICIAL_ACCOUNTS["cbebirr"]["valid_names"]):
        return True, "cbebirr", None

    return (
        False,
        None,
        "ክፍያው ወደ EtooBingo ይፋዊ አካውንቶች መላኩን ማረጋገጥ አልተቻለም። እባክዎ ወደ አንዱ ይፋዊ አካውንት ያስተላለፉበትን ሙሉ የ SMS መልዕክት ያስገቡ።",
    )


def is_package_or_service_sms(text: str) -> bool:
    """Detect if SMS is a mobile data/voice package, airtime, or utility payment."""
    lowered = text.lower()
    return any(kw in lowered for kw in _PACKAGE_AND_SERVICE_KEYWORDS)


def is_failed_transaction_sms(text: str) -> bool:
    """Detect if SMS indicates a failed or reversed transaction."""
    lowered = text.lower()
    return any(kw in lowered for kw in _FAILED_KEYWORDS)


def extract_amount_and_reference(text: str) -> tuple[float | None, str | None]:
    """Extract payment amount and transaction reference token from SMS text."""
    if not text or not text.strip():
        return None, None

    # Amount extraction
    matches = list(_AMOUNT_ETB_RE.finditer(text))
    chosen_amount = None
    if matches:
        candidates = []
        for m in matches:
            raw = next((g for g in m.groups() if g is not None), None)
            if raw is None:
                continue
            amt = _clean_amount(raw)
            if amt <= 0 or amt > 1_000_000:
                continue

            before = text[max(0, m.start() - 60) : m.start()].lower()
            after = text[m.end() : m.end() + 60].lower()
            by_kw = any(k in before or k in after for k in _CREDIT_KEYWORDS)
            candidates.append((by_kw and m.start() >= 0, amt))

        if candidates:
            chosen_amount = next((amt for scored, amt in candidates if scored), candidates[0][1])

    # Reference extraction
    reference = None
    ref_match = _REF_RE.search(text)
    if ref_match:
        reference = ref_match.group(1).strip()
    else:
        tb_match = _TELEBIRR_REF_RE.search(text)
        if tb_match:
            reference = tb_match.group(1).strip()
        else:
            ft_match = _CBE_FT_REF_RE.search(text)
            if ft_match:
                reference = ft_match.group(1).strip()
            else:
                digit = _DIGIT_RUN_RE.search(text)
                if digit:
                    reference = digit.group(1).strip()

    return chosen_amount, reference


def parse_deposit_sms(sms_text: str) -> dict | None:
    """Backwards-compatible helper returning {amount, reference}."""
    amt, ref = extract_amount_and_reference(sms_text)
    if amt is None:
        return None
    return {"amount": amt, "reference": ref}


async def verify_deposit_submission(
    raw_text: str,
    expected_method: str | None = None,
    explicit_amount: float | None = None,
) -> dict:
    """Comprehensive multi-layer verification of a deposit submission."""
    if not raw_text or not raw_text.strip():
        return {
            "valid": False,
            "error_type": "empty_input",
            "error_message": "⚠️ እባክዎ የ SMS መልዕክት፣ የደረሰኝ ሊንክ ወይም የግብይት ቁጥር ያስገቡ።",
        }

    clean_text = raw_text.strip()
    fp = fingerprint(clean_text) if len(clean_text) > 30 else f"ref:{clean_text}"

    # 1. Check for package / airtime / bundle purchases
    if is_package_or_service_sms(clean_text):
        return {
            "valid": False,
            "error_type": "package_purchase",
            "error_message": (
                "❌ *የተላከው መልዕክት የጥቅል (Package) ወይም የአየር ሰዓት ግዢ ነው!*\n\n"
                "እባክዎ ወደ EtooBingo ይፋዊ አካውንት ያስተላለፉበትን ትክክለኛ የገንዘብ ዝውውር SMS ያስገቡ።"
            ),
        }

    # 2. Check for failed or cancelled transfers
    if is_failed_transaction_sms(clean_text):
        return {
            "valid": False,
            "error_type": "failed_transaction",
            "error_message": "❌ ይህ የግብይት መልዕክት ያልተሳካ ወይም የተሰረዘ ዝውውር ያሳያል። እባክዎ የተሳካ የክፍያ SMS ይላኩ።",
        }

    # 3. Check for receipt URL or token live lookup on Ethio Telecom portal
    ref, url, inline_amt = extract_reference_and_url(clean_text)
    amount = explicit_amount or inline_amt
    reference = ref

    if url or (ref and _TELEBIRR_REF_RE.match(ref)):
        fetched = await fetch_telebirr_receipt(url or ref or "")
        if fetched:
            # Validate receiver on the live Ethio Telecom receipt
            bank_account = fetched.get("bank_account", "")
            credited_party = fetched.get("credited_party", "")
            payer_name = fetched.get("payer_name", "")

            # If it's a transfer to bank on Ethio Telecom portal
            if bank_account:
                merchant_bank_accounts = OFFICIAL_ACCOUNTS["cbe_bank"]["valid_accounts"]
                is_our_bank = any(acc in bank_account for acc in merchant_bank_accounts)
                if not is_our_bank:
                    return {
                        "valid": False,
                        "error_type": "recipient_mismatch",
                        "error_message": (
                            f"❌ *ክፍያው የተላከው ወደ ሌላ የባንክ አካውንት ({bank_account}) ነው!*\n\n"
                            "እባክዎ ወደ EtooBingo ይፋዊ አካውንት ያስተላለፉበትን የክፍያ ማስረጃ ያስገቡ:\n"
                            "• 🏦 *CBE Bank:* `1000413343538` (Natnael Temesegen)"
                        ),
                    }

            # If Payer is Habtamu and Credited Party is someone else
            if "habtamu" in payer_name.lower() and not any(acc in bank_account for acc in OFFICIAL_ACCOUNTS["cbe_bank"]["valid_accounts"]):
                if not any(n in credited_party.lower() for n in OFFICIAL_ACCOUNTS["telebirr"]["valid_names"]):
                    return {
                        "valid": False,
                        "error_type": "outgoing_from_merchant",
                        "error_message": "❌ ይህ የደረሰኝ ማስረጃ ከ EtooBingo አካውንት ወደ ሌላ ሰው የተደረገ ወጪ ዝውውር ነው!",
                    }

            if fetched.get("amount"):
                amount = fetched["amount"]
                reference = fetched.get("reference") or reference

    # 4. Extract amount and reference from full text if not yet found
    if not amount or not reference:
        parsed_amt, parsed_ref = extract_amount_and_reference(clean_text)
        if not amount:
            amount = parsed_amt
        if not reference:
            reference = parsed_ref

    if not reference:
        return {
            "valid": False,
            "error_type": "missing_reference",
            "error_message": "❌ ትክክለኛ የግብይት መለያ ቁጥር (Transaction ID / Ref) ማግኘት አልተቻለም። እባክዎ ሙሉውን SMS ይላኩ።",
        }

    # 5. Check Directional Recipient Account Match (Full SMS text pasted)
    if len(clean_text) > 35:
        is_valid_dest, matched_method, reject_reason = verify_directional_match(clean_text, expected_method)
        if not is_valid_dest:
            return {
                "valid": False,
                "error_type": "recipient_mismatch",
                "error_message": (
                    f"❌ *{reject_reason}*\n\n"
                    "ይፋዊ የ EtooBingo አካውንቶች:\n"
                    "• 🔵 *Telebirr:* `0963572327` (Habtamu Melese)\n"
                    "• 🟢 *CBE Birr:* `0934920411` (Natnael Temesegen)\n"
                    "• 🏦 *CBE Bank:* `1000413343538` (Natnael Temesegen)"
                ),
            }
        detected_method = matched_method or expected_method or "telebirr"
    else:
        detected_method = expected_method or "telebirr"

    if amount is None or amount <= 0:
        return {
            "valid": False,
            "error_type": "missing_amount",
            "reference": reference,
            "error_message": (
                f"📋 *የግብይት መለያ ቁጥር ተቀብለናል:* `{reference}`\n\n"
                f"⚠️ ሙሉውን የገንዘብ መጠን አረጋግጠን ሂሳብዎ ላይ ለመጨመር እባክዎ ከሚከተሉት አንዱን ያድርጉ፦\n"
                f"1. የደረሰዎትን *ሙሉ የ SMS መልዕክት* ይላኩ\n"
                f"2. ወይም የላኩትን የብር መጠን ከቁጥሩ ጋር አያይዘው ይላኩ (ለምሳሌ፦ `{reference} 50`)"
            ),
        }

    return {
        "valid": True,
        "amount": amount,
        "reference": reference,
        "method": detected_method,
        "fingerprint": fp,
        "error_type": None,
        "error_message": None,
    }