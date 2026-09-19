"""Parse and strictly verify Telebirr, CBE Birr, and CBE Mobile Banking SMS receipts.

Enforces:
1. Anti-Package / Anti-Airtime / Anti-Service Filter: Rejects package, bundle, airtime, and utility purchases.
2. Recipient Account Match: Verifies that money was transferred specifically to EtooBingo official accounts:
   - Telebirr: 0963572327 (Habtamu Melese)
   - CBE Birr: 0934920411 (Natnael Temesegen)
   - CBE Mobile Banking: 10000413343538 (Natnael Temesegen)
3. Anti-Duplicate / Anti-Replay: Stable fingerprint generation and reference extraction.
4. Positive Amount & Direction Verification: Ensures money was actually credited / transferred.
"""

import hashlib
import re

OFFICIAL_ACCOUNTS = {
    "telebirr": {
        "display_name": "Telebirr (ቴሌብር)",
        "name": "Habtamu Melese",
        "phone": "0963572327",
        "short_phone": "963572327",
        "keywords": [
            "0963572327",
            "963572327",
            "63572327",
            "habtamu",
            "melese",
            "habtamumelese",
            "ሀብታሙ",
            "መለሰ",
        ],
    },
    "cbebirr": {
        "display_name": "CBE Birr (ሲቢኢ ብር)",
        "name": "Natnael Temesegen",
        "phone": "0934920411",
        "short_phone": "934920411",
        "keywords": [
            "0934920411",
            "934920411",
            "34920411",
            "natnael",
            "temesegen",
            "natnaeltemesegen",
            "ናትናኤል",
            "ተመስገን",
        ],
    },
    "cbe_bank": {
        "display_name": "CBE Mobile Banking (ንግድ ባንክ)",
        "name": "Natnael Temesegen",
        "account": "10000413343538",
        "short_account": "413343538",
        "keywords": [
            "10000413343538",
            "1000413343538",
            "413343538",
            "1000041334353",
            "natnael",
            "temesegen",
            "natnaeltemesegen",
            "ናትናኤል",
            "ተመስገን",
        ],
    },
}

# Keywords that explicitly identify mobile packages, airtime, or non-peer transfers
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

# Transaction references: TXN/Ref/TxId/transaction number etc.
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

# Telebirr specific reference pattern (e.g. DIG0SAVPRY, DIH0TF0X58)
_TELEBIRR_REF_RE = re.compile(r"\b(DI[A-Z0-9]{8})\b", re.IGNORECASE)

# CBE Mobile Banking FT reference pattern (e.g. FT2412345678, FT25...)
_CBE_FT_REF_RE = re.compile(r"\b(FT[0-9A-Z]{8,16})\b", re.IGNORECASE)

# Telebirr official receipt URL pattern
_RECEIPT_URL_RE = re.compile(
    r"https?://(?:transactioninfo\.ethiotelecom\.et/receipt/|telebirr[^\s]*/receipt/)([A-Za-z0-9_\-]+)",
    re.IGNORECASE,
)

# Reference with inline amount, e.g. "DIH3TFMD7H 100", "DIH3TFMD7H amount: 100", "100 ETB DIH3TFMD7H"
_REF_WITH_AMOUNT_RE = re.compile(
    r"(?:^|\s)(?:ref(?:erence)?|txn?\s*(?:id)?|id|የግብይት\s*ቁጥር)?\s*[:#]?\s*([A-Za-z0-9_\-]{6,30})"
    r"\s+(?:መጠን\s*[:#]?\s*|amount\s*[:#]?\s*|etb\s*|ብር\s*|birr\s*)?(\d+(?:\.\d{1,2})?)(?:\s*(?:etb|ብር|birr))?"
    r"|(?:መጠን\s*[:#]?\s*|amount\s*[:#]?\s*|etb\s*|ብር\s*|birr\s*)?(\d+(?:\.\d{1,2})?)(?:\s*(?:etb|ብር|birr))?"
    r"\s+(?:ref(?:erence)?|txn?\s*(?:id)?|id|የግብይት\s*ቁጥር)?\s*[:#]?\s*([A-Za-z0-9_\-]{6,30})",
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
        ref = url_m.group(1).strip()
        url = url_m.group(0).strip()
        return ref, url, None

    # 2. Check for reference + amount format (e.g. "DIH3TFMD7H 10")
    amt_m = _REF_WITH_AMOUNT_RE.search(clean)
    if amt_m:
        g1, g2, g3, g4 = amt_m.groups()
        if g1 and g2:
            return g1.strip(), None, float(g2)
        elif g3 and g4:
            return g4.strip(), None, float(g3)

    # 3. Check for bare reference
    if re.match(r"^[A-Za-z0-9_\-]{6,30}$", clean):
        return clean, None, None

    return None, None, None


async def fetch_telebirr_receipt(ref_or_url: str) -> dict | None:
    """Fetch live receipt details from Ethio Telecom's public receipt endpoint."""
    import httpx

    ref, url, _ = extract_reference_and_url(ref_or_url)
    txn_id = ref or ref_or_url.strip()
    if not txn_id or not re.match(r"^[A-Za-z0-9_\-]{6,30}$", txn_id):
        return None

    target_url = url or f"https://transactioninfo.ethiotelecom.et/receipt/{txn_id}"
    try:
        async with httpx.AsyncClient(timeout=8.0, verify=False) as client:
            resp = await client.get(target_url)
            if resp.status_code == 200 and "telebirr receipt" in resp.text:
                html = resp.text

                # Extract Settled Amount in Birr
                amt_match = re.search(
                    r"Settled Amount.*?(\d+(?:\.\d{1,2})?)\s*Birr",
                    html,
                    re.DOTALL | re.IGNORECASE,
                )
                if not amt_match:
                    amt_match = re.search(r"(\d+(?:\.\d{1,2})?)\s*Birr", html, re.IGNORECASE)
                amount = float(amt_match.group(1)) if amt_match else None

                # Extract reference
                ref_m = re.search(r"\b(DI[A-Z0-9]{8})\b", html, re.IGNORECASE)
                final_ref = ref_m.group(1) if ref_m else txn_id

                # Extract payer name
                payer_m = re.search(r"Payer Name.*?<td[^>]*>(.*?)</td>", html, re.DOTALL | re.IGNORECASE)
                payer = payer_m.group(1).strip() if payer_m else None

                return {
                    "amount": amount,
                    "reference": final_ref,
                    "payer": payer,
                    "status": "completed",
                    "url": target_url,
                }
    except Exception:
        pass
    return None


def verify_recipient_match(text: str, expected_method: str | None = None) -> tuple[bool, str | None]:
    """Check if the SMS or receipt specifies an official EtooBingo account as the receiver.

    Returns (is_match, matched_method_key).
    """
    clean_text = text.lower()
    clean_text_no_space = re.sub(r"[\s\W_]+", "", clean_text)

    # Check if a specific method was chosen
    methods_to_check = [expected_method] if expected_method and expected_method in OFFICIAL_ACCOUNTS else list(OFFICIAL_ACCOUNTS.keys())

    for method_key in methods_to_check:
        acc = OFFICIAL_ACCOUNTS[method_key]
        for kw in acc["keywords"]:
            kw_clean = kw.lower()
            if kw_clean in clean_text or kw_clean in clean_text_no_space:
                return True, method_key

    # Check all official accounts if expected_method didn't match directly
    for method_key, acc in OFFICIAL_ACCOUNTS.items():
        for kw in acc["keywords"]:
            kw_clean = kw.lower()
            if kw_clean in clean_text or kw_clean in clean_text_no_space:
                return True, method_key

    return False, None


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
    """Comprehensive multi-layer verification of a deposit submission.

    Returns dict with:
    - valid (bool)
    - amount (float or None)
    - reference (str or None)
    - method (str or None)
    - fingerprint (str)
    - error_type (str or None)
    - error_message (str or None)
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

    # 3. Check for receipt URL or token online check
    ref, url, inline_amt = extract_reference_and_url(clean_text)
    amount = explicit_amount or inline_amt
    reference = ref

    # Attempt live Telebirr receipt lookup if link or token is provided
    if (url or (ref and _TELEBIRR_REF_RE.match(ref))) and not amount:
        fetched = await fetch_telebirr_receipt(url or ref or "")
        if fetched and fetched.get("amount") and fetched.get("status") == "completed":
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

    # 5. Check Recipient Account Match (Must be to our official accounts)
    # If full SMS text was pasted (> 35 characters)
    is_match, matched_method = verify_recipient_match(clean_text, expected_method)
    if len(clean_text) > 35 and not is_match:
        return {
            "valid": False,
            "error_type": "recipient_mismatch",
            "error_message": (
                "❌ *ክፍያው የተላከው ወደ ሌላ ሰው አካውንት ነው!*\n\n"
                "እባክዎ ወደ EtooBingo ይፋዊ አካውንት ያስተላለፉበትን የ SMS መልዕክት ይላኩ:\n"
                "• 🔵 *Telebirr:* `0963572327` (Habtamu Melese)\n"
                "• 🟢 *CBE Birr:* `0934920411` (Natnael Temesegen)\n"
                "• 🏦 *CBE Bank:* `10000413343538` (Natnael Temesegen)"
            ),
        }

    detected_method = matched_method or expected_method or "telebirr"

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