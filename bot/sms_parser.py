"""Parse and strictly verify Telebirr, CBE Birr, and CBE Mobile Banking deposit receipts.

Accepts three input forms:
  1. Full SMS text pasted from the bank / wallet notification
  2. A bare Transaction ID / reference token (e.g. ``DIK7W7R5VZ`` or ``FT262641DG9X``)
  3. An official receipt URL:
     • Telebirr:  https://transactioninfo.ethiotelecom.et/receipt/<txn_id>
     • CBE MB:    https://mbreciept.cbe.com.et/<token>
     • CBE Birr:  https://apps.cbe.com.et:100/BranchReceipt/<FT_ref>&<acct>

Enforces:
  1. Anti-Package / Anti-Airtime Filter
  2. Strict Directional Verification (Sender vs Receiver) — money must flow TO
     our official accounts
  3. Live Receipt Portal Verification — scrapes/queries the public receipt
     endpoint for each provider
  4. Verify.et API secondary verification (when API key is configured)
  5. Anti-Duplicate / Anti-Replay via stable fingerprinting
"""

import hashlib
import logging
import re
import uuid
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
        "valid_accounts": ["1000413343538", "413343538", "13343538"],
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
    r"transaction\s*(?:number|no|num|id|ref(?:erence)?)|\s*"
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
    r"https?://(?:"
    r"(?:transactioninfo\.ethiotelecom\.et/receipt/)([A-Za-z0-9_-]+)"
    r"|(?:mbreciept\.cbe\.com\.et/)([A-Za-z0-9_-]+)"
    r"|(?:apps\.cbe\.com\.et(?::\d+)?/BranchReceipt/)(FT[0-9A-Z]+)(?:&(\d+))?"
    r")",
    re.IGNORECASE,
)
_DIGIT_RUN_RE = re.compile(r"(?<!\d)(\d{9,16})(?!\d)")

# CBE Mobile Banking public API (reverse-engineered from their Nuxt SPA)
_CBE_MB_API_BASE = "https://Mb.cbe.com.et/api/v1/transactions/public/transaction-detail"
_CBE_MB_HEADERS = {
    "X-App-ID": "d1292e42-7400-49de-a2d3-9731caa4c819",
    "X-App-Version": "0a01980b-9859-1369-8198-59f403820000",
    "Accept": "application/json",
}


def fingerprint(sms_text: str) -> str:
    """Stable hash of normalized SMS text used to reject duplicate receipts."""
    normalized = re.sub(r"[\s\W_]+", "", sms_text).lower()
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:24]


def _clean_amount(number: str) -> float:
    return float(number.replace(",", ""))


def _mask_matches(masked: str, full: str) -> bool:
    """Check if a partially masked account like ``1********3538`` matches the full value.

    Masked digits are represented by ``*``. Handles two cases:
      1. Same length: each non-``*`` char must match the corresponding position in *full*.
      2. Different length: align by suffix (last N visible chars must match *full*'s suffix)
         and align by prefix (first visible char must match *full*'s prefix).
    """
    if not masked or not full:
        return False
    stripped_m = masked.replace(" ", "")
    stripped_f = full.replace(" ", "")

    if len(stripped_m) == len(stripped_f):
        # Same length — positional match
        return all(mc == "*" or mc == fc for mc, fc in zip(stripped_m, stripped_f))

    # Different length — extract visible chars from the mask and check they
    # appear as a sub-sequence at the expected start/end positions of full.
    visible_chars = [c for c in stripped_m if c != "*"]
    if not visible_chars:
        return False

    # Check suffix: the last visible chars of the mask must match the tail of full
    # (common CBE pattern: "1********3538" → last 4 digits = "3538")
    num_stars = stripped_m.count("*")
    # visible prefix + visible suffix must bookend the full number
    prefix_chars = []
    suffix_chars = []
    in_stars = False
    for c in stripped_m:
        if c == "*":
            in_stars = True
        elif not in_stars:
            prefix_chars.append(c)
        else:
            suffix_chars.append(c)

    # Check that the full account starts with the same prefix chars
    prefix_ok = stripped_f[:len(prefix_chars)] == "".join(prefix_chars) if prefix_chars else True
    # Check that the full account ends with the same suffix chars
    suffix_ok = stripped_f[-len(suffix_chars):] == "".join(suffix_chars) if suffix_chars else True
    return prefix_ok and suffix_ok


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


# ---------------------------------------------------------------------------
# Telebirr receipt fetcher (Ethio Telecom public receipt page)
# ---------------------------------------------------------------------------

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
                    "provider": "telebirr",
                    "amount": settled_amount,
                    "reference": final_ref,
                    "payer_name": payer_name,
                    "payer_no": payer_no,
                    "credited_party": credited_party,
                    "receiver_account": bank_account,
                    "status": status,
                    "url": target_url,
                }
    except Exception as exc:
        logger.warning("Error fetching live telebirr receipt %s: %s", target_url, exc)
    return None


# ---------------------------------------------------------------------------
# CBE Mobile Banking receipt fetcher (public JSON API)
# ---------------------------------------------------------------------------

async def fetch_cbe_mobile_banking_receipt(token: str) -> dict | None:
    """Fetch receipt data from CBE Mobile Banking public transaction API.

    The CBE receipt viewer at ``mbreciept.cbe.com.et`` is a Nuxt SPA that
    fetches transaction details from ``Mb.cbe.com.et``.  We call the same
    JSON API directly for reliable structured data.
    """
    if not token or not re.match(r"^[A-Za-z0-9_\-]{6,50}$", token):
        return None

    api_url = f"{_CBE_MB_API_BASE}/{token}"
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(api_url, headers=_CBE_MB_HEADERS)
            if resp.status_code != 200:
                logger.warning("CBE MB API returned %s for token %s", resp.status_code, token)
                return None

            data = resp.json()
            if not isinstance(data, dict) or "id" not in data:
                return None

            amount_str = data.get("amountDebited") or data.get("debitAmount") or "0"
            try:
                amount = float(str(amount_str).replace(",", ""))
            except (ValueError, TypeError):
                amount = None

            reference = data.get("id", "")
            debit_account = data.get("debitAccountNo", "")
            credit_account = data.get("creditAccountNo", "")
            payer_name = data.get("debitAccountHolder", "")
            receiver_name = data.get("creditAccountHolder", "")
            status_raw = (data.get("status") or "").upper()
            status = "completed" if status_raw in ("COMPLETED", "SUCCESS") else status_raw.lower()

            return {
                "provider": "cbe_bank",
                "amount": amount if amount and amount > 0 else None,
                "reference": reference,
                "payer_name": payer_name,
                "payer_account": debit_account,
                "receiver_name": receiver_name,
                "receiver_account": credit_account,
                "status": status,
                "url": f"https://mbreciept.cbe.com.et/{token}",
                "raw": data,
            }
    except Exception as exc:
        logger.warning("Error fetching CBE MB receipt for token %s: %s", token, exc)
    return None


# ---------------------------------------------------------------------------
# Verify.et API client (secondary verification layer)
# ---------------------------------------------------------------------------

async def verify_via_verify_et(
    reference: str,
    bank: str | None = None,
    suffix: str | None = None,
    phone: str | None = None,
) -> dict | None:
    """Call the Verify.et API to verify a transaction reference.

    Returns the parsed response dict on success, or ``None`` on failure / no
    API key.  The caller should treat this as a *secondary* signal — primary
    verification is done via direct receipt scraping.
    """
    from bot.config import settings  # lazy to avoid circular import at module level

    api_key = settings.verify_et_api_key
    base_url = settings.verify_et_base_url.rstrip("/")
    if not api_key:
        return None

    # Build payload per bank-specs.md
    payload: dict[str, Any] = {}
    if bank:
        payload["bank"] = bank
        if bank == "cbe":
            payload["receiptNumber"] = reference
            if suffix:
                payload["accountSuffix"] = suffix
        elif bank == "telebirr":
            payload["transactionNumber"] = reference
        elif bank == "cbebirr":
            payload["receiptNumber"] = reference
            if phone:
                payload["phoneNumber"] = phone
        else:
            payload["reference"] = reference
    else:
        # Universal mode
        payload["reference"] = reference
        if suffix:
            payload["suffix"] = suffix
        if phone:
            payload["phoneNumber"] = phone

    idempotency_key = f"etoo_{reference}_{uuid.uuid4().hex[:8]}"
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "Idempotency-Key": idempotency_key,
    }

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.post(
                f"{base_url}/api/verify?waitMs=8000",
                json=payload,
                headers=headers,
            )

            if resp.status_code == 200:
                data = resp.json()
                verification = data.get("verification", {})
                if verification.get("verified"):
                    return {
                        "verified": True,
                        "status": verification.get("status", "success"),
                        "request_id": data.get("requestId"),
                        "result": verification.get("result", {}),
                        "confirmed_before": (
                            data.get("data", [{}])[0].get("confirmationHistory", {}).get("confirmedBefore", False)
                            if data.get("data")
                            else False
                        ),
                    }
                else:
                    return {
                        "verified": False,
                        "status": verification.get("status", "not_found"),
                        "request_id": data.get("requestId"),
                    }

            elif resp.status_code == 202:
                # Queued — return pending status, we'll rely on receipt scraping
                data = resp.json()
                return {
                    "verified": False,
                    "status": "pending",
                    "request_id": data.get("requestId"),
                    "queued": True,
                }

            else:
                logger.warning("Verify.et returned %s: %s", resp.status_code, resp.text[:200])
                return None

    except Exception as exc:
        logger.warning("Verify.et API error for %s: %s", reference, exc)
    return None


# ---------------------------------------------------------------------------
# Directional account matching (SMS text analysis)
# ---------------------------------------------------------------------------

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

    # Receiver destination: check for bank account or phone number
    to_m = re.search(
        r"(?:to|ወደ)\s*(?:commercial\s*bank(?:\s*of\s*ethiopia)?|cbe|telebirr|cbebirr|ንግድ\s*ባንክ)?\s*(?:account\s*(?:number|no)?|አካውንት\s*(?:ቁጥር)?|phone|ስልክ|ቁጥር|a/c)?\s*[:#]?\s*(\+?251\d{9}|09\d{8}|07\d{8}|\d{10,16})",
        text,
        re.IGNORECASE,
    )
    if to_m:
        dest = to_m.group(1).strip()
        digits = re.sub(r"\D", "", dest)
        if digits.startswith("09") or digits.startswith("07") or digits.startswith("2519") or digits.startswith("2517"):
            receiver_phone = dest
        else:
            receiver_account = dest

    # Separate phone search fallback if not set
    if not receiver_phone:
        to_phone_m = re.search(
            r"(?:to|ወደ)\s*(?:(?:phone|ስልክ|ቁጥር|telebirr|cbebirr)\s*[:#]?\s*)?(\+?251\d{9}|09\d{8}|07\d{8})",
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
    Permits inter-account transfers between official accounts (e.g. owner's Telebirr to CBE).
    """
    clean_text = text.lower()
    directional = extract_directional_accounts(text)

    sender = directional["sender"]
    receiver_acc = directional["receiver_account"]
    receiver_phone = directional["receiver_phone"]

    merchant_telebirr_numbers = OFFICIAL_ACCOUNTS["telebirr"]["valid_numbers"]
    merchant_cbebirr_numbers = OFFICIAL_ACCOUNTS["cbebirr"]["valid_numbers"]
    merchant_bank_accounts = OFFICIAL_ACCOUNTS["cbe_bank"]["valid_accounts"]

    # 1. Check destination phone
    if receiver_phone:
        if any(num == receiver_phone or num in receiver_phone for num in merchant_telebirr_numbers):
            return True, "telebirr", None
        elif any(num == receiver_phone or num in receiver_phone for num in merchant_cbebirr_numbers):
            return True, "cbebirr", None
        elif any(acc == receiver_phone or acc in receiver_phone for acc in merchant_bank_accounts):
            return True, "cbe_bank", None
        else:
            return (
                False,
                None,
                f"ክፍያው የተላከው ወደ ሌላ ስልክ ቁጥር ({receiver_phone}) ነው! እባክዎ ወደ EtooBingo ይፋዊ አካውንት ያስተላለፉበትን የ SMS መልዕክት ያስገቡ።",
            )

    # 2. Check destination bank account
    if receiver_acc:
        if any(acc == receiver_acc or acc in receiver_acc for acc in merchant_bank_accounts):
            return True, "cbe_bank", None
        elif any(num == receiver_acc or num in receiver_acc for num in merchant_telebirr_numbers):
            return True, "telebirr", None
        elif any(num == receiver_acc or num in receiver_acc for num in merchant_cbebirr_numbers):
            return True, "cbebirr", None
        else:
            return (
                False,
                None,
                f"ክፍያው የተላከው ወደ ሌላ የባንክ አካውንት ({receiver_acc}) ነው! እባክዎ ወደ EtooBingo ይፋዊ አካውንት (1000413343538 - Natnael Temesegen) ያስተላለፉበትን የ SMS መልዕክት ያስገቡ።",
            )

    # 3. Fallback to presence of accounts and names in text
    if any(acc in clean_text for acc in merchant_bank_accounts) and any(n in clean_text for n in OFFICIAL_ACCOUNTS["cbe_bank"]["valid_names"]):
        return True, "cbe_bank", None

    if any(num in clean_text for num in merchant_telebirr_numbers) and any(n in clean_text for n in OFFICIAL_ACCOUNTS["telebirr"]["valid_names"]):
        return True, "telebirr", None

    if any(num in clean_text for num in merchant_cbebirr_numbers) and any(n in clean_text for n in OFFICIAL_ACCOUNTS["cbebirr"]["valid_names"]):
        return True, "cbebirr", None

    if any(acc in clean_text for acc in merchant_bank_accounts):
        return True, "cbe_bank", None

    if any(num in clean_text for num in merchant_telebirr_numbers):
        return True, "telebirr", None

    if any(num in clean_text for num in merchant_cbebirr_numbers):
        return True, "cbebirr", None

    return (
        False,
        None,
        "ክፍያው ወደ EtooBingo ይፋዊ አካውንቶች መላኩን ማረጋገጥ አልተቻለም። እባክዎ ወደ አንዱ ይፋዊ አካውንት ያስተላለፉበትን ሙሉ የ SMS መልዕክት ያስገቡ።",
    )


# ---------------------------------------------------------------------------
# Helper filters
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Receipt-based receiver verification helpers
# ---------------------------------------------------------------------------

def _verify_telebirr_receipt_receiver(fetched: dict) -> tuple[bool, str | None]:
    """Return (is_valid, rejection_reason) for a fetched Telebirr receipt.

    Accepts transfers to any official account, including transfers initiated
    from one of our own accounts to another.
    """
    bank_account = fetched.get("receiver_account") or fetched.get("bank_account", "")
    credited_party = fetched.get("credited_party", "")
    payer_name = fetched.get("payer_name", "")

    all_accounts = OFFICIAL_ACCOUNTS["cbe_bank"]["valid_accounts"]
    all_names = (
        OFFICIAL_ACCOUNTS["telebirr"]["valid_names"]
        + OFFICIAL_ACCOUNTS["cbebirr"]["valid_names"]
        + OFFICIAL_ACCOUNTS["cbe_bank"]["valid_names"]
    )
    all_numbers = (
        OFFICIAL_ACCOUNTS["telebirr"]["valid_numbers"]
        + OFFICIAL_ACCOUNTS["cbebirr"]["valid_numbers"]
    )

    # 1. If bank account is present on receipt (transfer to CBE Bank)
    if bank_account:
        is_our_bank = any(acc in bank_account for acc in all_accounts)
        if is_our_bank:
            return True, None
        else:
            return (
                False,
                f"ክፍያው የተላከው ወደ ሌላ የባንክ አካውንት ({bank_account}) ነው! "
                "እባክዎ ወደ EtooBingo ይፋዊ አካውንት ያስተላለፉበትን የክፍያ ማስረጃ ያስገቡ:\n"
                "• 🏦 *CBE Bank:* `1000413343538` (Natnael Temesegen)",
            )

    # 2. If credited party is present
    if credited_party:
        is_our_name = any(n in credited_party.lower() for n in all_names)
        is_our_num = any(num in credited_party for num in all_numbers)
        if is_our_name or is_our_num:
            return True, None
        # Only reject if credited party explicitly does not match any official name
        return False, f"ክፍያው የተላከው ወደ ሌላ ሰው ({credited_party}) ነው! እባክዎ ወደ EtooBingo ይፋዊ አካውንት ያስተላልፉ።"

    return True, None


def _verify_cbe_mb_receipt_receiver(fetched: dict) -> tuple[bool, str | None]:
    """Return (is_valid, rejection_reason) for a fetched CBE MB receipt.

    Accepts transfers to our CBE bank account, or wallet transfers (Telebirr / CBE Birr)
    with our official phone numbers in description/details, including transfers
    from one official account to another.
    """
    debit_account = fetched.get("payer_account", "")
    credit_account = fetched.get("receiver_account", "")
    receiver_name = fetched.get("receiver_name", "")
    status = fetched.get("status", "")
    raw = fetched.get("raw", {})
    description = raw.get("description", "")
    payment_details = raw.get("paymentDetails", [])

    if status and status not in ("completed", "success"):
        return False, f"ይህ ግብይት ያልተሳካ ነው (ሁኔታ: {status})!"

    all_accounts = OFFICIAL_ACCOUNTS["cbe_bank"]["valid_accounts"]
    all_names = (
        OFFICIAL_ACCOUNTS["telebirr"]["valid_names"]
        + OFFICIAL_ACCOUNTS["cbebirr"]["valid_names"]
        + OFFICIAL_ACCOUNTS["cbe_bank"]["valid_names"]
    )
    all_numbers = (
        OFFICIAL_ACCOUNTS["telebirr"]["valid_numbers"]
        + OFFICIAL_ACCOUNTS["cbebirr"]["valid_numbers"]
    )

    # Check matches:
    # A. Credit account matches our CBE account
    matched_account = credit_account and any(
        _mask_matches(credit_account, acc) or acc in credit_account or credit_account in acc
        for acc in all_accounts
    )

    # B. Receiver name matches any of our official names
    matched_name = receiver_name and any(n in receiver_name.lower() for n in all_names)

    # C. Wallet settlement transfer (To Telebirr Transfer Settlement / To Cbe Birr Transfer Settlement)
    is_settlement = any(
        kw in receiver_name.lower() for kw in ("settlement", "telebirr", "cbe birr", "cbebirr", "transfer", "wallet")
    )
    desc_match = any(num in description for num in all_numbers)
    detail_match = any(
        any(num in str(d) for num in all_numbers)
        for d in payment_details
    )

    if matched_account or matched_name or (is_settlement and (desc_match or detail_match)) or desc_match or detail_match:
        return True, None

    # If it is an explicit transfer to a non-matching account:
    if credit_account and not matched_account and not is_settlement:
        return (
            False,
            f"ክፍያው ወደ EtooBingo ይፋዊ አካውንት አልተላከም! "
            "እባክዎ ወደ `1000413343538` (Natnael Temesegen) ያስተላለፉበትን ማስረጃ ይላኩ።",
        )

    return True, None


# ---------------------------------------------------------------------------
# Detect provider from receipt URL
# ---------------------------------------------------------------------------

def _detect_receipt_provider(url: str) -> str | None:
    """Detect the payment provider from a receipt URL."""
    if "transactioninfo.ethiotelecom.et" in url:
        return "telebirr"
    if "mbreciept.cbe.com.et" in url:
        return "cbe_bank"
    if "apps.cbe.com.et" in url:
        return "cbebirr"
    return None


# ---------------------------------------------------------------------------
# Main verification orchestrator
# ---------------------------------------------------------------------------

async def verify_deposit_submission(
    raw_text: str,
    expected_method: str | None = None,
    explicit_amount: float | None = None,
) -> dict:
    """Comprehensive multi-layer verification of a deposit submission.

    Accepts: full SMS text, a bare transaction ID, or a receipt URL.
    Amount is optional — if it can be extracted from the receipt it will be;
    otherwise the deposit proceeds with ``amount=None`` and the caller may
    prompt the user or auto-detect later.
    """
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

    # 3. Extract reference and URL from the input
    ref, url, inline_amt = extract_reference_and_url(clean_text)
    amount = explicit_amount or inline_amt
    reference = ref
    detected_method = expected_method
    receipt_verified = False  # True when a live receipt confirms receiver

    # ── 3a. Receipt URL live lookup ─────────────────────────────────────
    if url:
        provider = _detect_receipt_provider(url)

        if provider == "telebirr":
            fetched = await fetch_telebirr_receipt(url)
            if fetched:
                is_valid, reject = _verify_telebirr_receipt_receiver(fetched)
                if not is_valid:
                    return {
                        "valid": False,
                        "error_type": "recipient_mismatch",
                        "error_message": f"❌ *{reject}*",
                    }
                if fetched.get("amount"):
                    amount = fetched["amount"]
                reference = fetched.get("reference") or reference
                detected_method = "telebirr"
                receipt_verified = True

        elif provider == "cbe_bank":
            # Extract token from URL
            url_m = _RECEIPT_URL_RE.search(url)
            token = url_m.group(2) if url_m and url_m.group(2) else None
            if token:
                fetched = await fetch_cbe_mobile_banking_receipt(token)
                if fetched:
                    is_valid, reject = _verify_cbe_mb_receipt_receiver(fetched)
                    if not is_valid:
                        return {
                            "valid": False,
                            "error_type": "recipient_mismatch",
                            "error_message": f"❌ *{reject}*",
                        }
                    if fetched.get("amount"):
                        amount = fetched["amount"]
                    reference = fetched.get("reference") or reference
                    detected_method = "cbe_bank"
                    receipt_verified = True

        elif provider == "cbebirr":
            # CBE Branch returns a PDF — can't scrape, use FT ref + Verify.et
            url_m = _RECEIPT_URL_RE.search(url)
            ft_ref = url_m.group(3) if url_m and url_m.group(3) else None
            acct_suffix = url_m.group(4) if url_m and url_m.group(4) else None
            if ft_ref:
                reference = ft_ref
                detected_method = "cbebirr"
                # Try Verify.et for CBE Branch receipts
                verify_result = await verify_via_verify_et(
                    ft_ref, bank="cbe", suffix=acct_suffix
                )
                if verify_result and verify_result.get("verified"):
                    receipt_verified = True
                    if verify_result.get("confirmed_before"):
                        return {
                            "valid": False,
                            "error_type": "duplicate_verified",
                            "error_message": "⚠️ *ይህ የግብይት ቁጥር ቀድሞውኑ ተረጋግጦ ጥቅም ላይ ውሏል!*",
                        }

    # ── 3b. Bare Telebirr reference (DI...) without URL ─────────────────
    elif ref and _TELEBIRR_REF_RE.match(ref) and not url:
        fetched = await fetch_telebirr_receipt(ref)
        if fetched:
            is_valid, reject = _verify_telebirr_receipt_receiver(fetched)
            if not is_valid:
                return {
                    "valid": False,
                    "error_type": "recipient_mismatch",
                    "error_message": f"❌ *{reject}*",
                }
            if fetched.get("amount"):
                amount = fetched["amount"]
            reference = fetched.get("reference") or reference
            detected_method = "telebirr"
            receipt_verified = True

    # ── 3c. Bare FT reference ───────────────────────────────────────────
    elif ref and _CBE_FT_REF_RE.match(ref) and not url:
        detected_method = detected_method or "cbe_bank"
        # Try Verify.et for FT references
        suffix = OFFICIAL_ACCOUNTS["cbe_bank"]["short_account"][-8:]  # "13343538"
        verify_result = await verify_via_verify_et(ref, bank="cbe", suffix=suffix)
        if verify_result and verify_result.get("verified"):
            receipt_verified = True
            if verify_result.get("confirmed_before"):
                return {
                    "valid": False,
                    "error_type": "duplicate_verified",
                    "error_message": "⚠️ *ይህ የግብይት ቁጥር ቀድሞውኑ ተረጋግጦ ጥቅም ላይ ውሏል!*",
                }

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
    if len(clean_text) > 35 and not receipt_verified:
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
        detected_method = matched_method or detected_method or "telebirr"
    elif not detected_method:
        detected_method = expected_method or "telebirr"

    # 6. Try Verify.et as secondary confirmation if not yet receipt-verified
    if not receipt_verified and reference:
        bank_for_verify = None
        suffix_for_verify = None
        phone_for_verify = None

        if detected_method == "telebirr":
            bank_for_verify = "telebirr"
        elif detected_method == "cbe_bank":
            bank_for_verify = "cbe"
            if _CBE_FT_REF_RE.match(reference):
                suffix_for_verify = OFFICIAL_ACCOUNTS["cbe_bank"]["short_account"][-8:]
        elif detected_method == "cbebirr":
            bank_for_verify = "cbebirr"
            phone_for_verify = OFFICIAL_ACCOUNTS["cbebirr"]["intl_phone"]

        verify_result = await verify_via_verify_et(
            reference, bank=bank_for_verify, suffix=suffix_for_verify, phone=phone_for_verify
        )
        if verify_result:
            if verify_result.get("verified"):
                receipt_verified = True
                if verify_result.get("confirmed_before"):
                    return {
                        "valid": False,
                        "error_type": "duplicate_verified",
                        "error_message": "⚠️ *ይህ የግብይት ቁጥር ቀድሞውኑ ተረጋግጦ ጥቅም ላይ ውሏል!*",
                    }
            elif verify_result.get("status") == "not_found":
                # Verify.et couldn't find the transaction — still allow with
                # warning if SMS directional check passed
                logger.info("Verify.et not_found for %s — proceeding with SMS validation only", reference)

    # 7. Amount is optional — if missing, the deposit still proceeds
    #    The caller will prompt the user or handle it gracefully.
    return {
        "valid": True,
        "amount": amount,  # may be None
        "reference": reference,
        "method": detected_method,
        "fingerprint": fp,
        "receipt_verified": receipt_verified,
        "error_type": None,
        "error_message": None,
    }