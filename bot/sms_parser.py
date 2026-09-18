"""Parse Telebirr / CBE Birr SMS notifications for deposit auto-approval."""

import hashlib
import re

# Amount adjacent to an ETB/Birr keyword, e.g. "ETB 500.00", "500.00 ETB", "500ብር".
_AMOUNT_ETB_RE = re.compile(
    r"(?:ETB|Birr|ብር)\s*[:#/\-]?\s*(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
    r"|(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)\s*(?:ETB|Birr|ብር)",
    re.IGNORECASE,
)

# Credit keywords that mark the received amount (not a balance/previous amount).
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
    "ደርሷል",
    "ደርሶበታል",
    "ገንዘብ",
    "ተከፍሏል",
    "ተክፍሏል",
    "ብ ክፍያ",
    "የተከፈለ",
)

# Transaction references: TXN/Ref/TxId/transaction number etc. followed by an alphanumeric token.
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
    """Fetch live receipt details from Ethio Telecom's public receipt endpoint.

    Returns dict with {amount, reference, payer, status, url} or None.
    """
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


# Long digit runs commonly used as payment references when labels vary.
_DIGIT_RUN_RE = re.compile(r"(?<!\d)(\d{9,16})(?!\d)")


def fingerprint(sms_text: str) -> str:
    """Stable hash of normalized SMS text used to reject duplicate receipts."""
    normalized = re.sub(r"[\s\W_]+", "", sms_text).lower()
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:24]


def _clean_amount(number: str) -> float:
    return float(number.replace(",", ""))


def parse_deposit_sms(sms_text: str) -> dict | None:
    """Extract {amount, reference} from a deposit SMS.

    Returns None when no believable amount is found.
    """
    if not sms_text or not sms_text.strip():
        return None

    matches = list(_AMOUNT_ETB_RE.finditer(sms_text))
    if not matches:
        return None

    candidates = []
    for m in matches:
        raw = next((g for g in m.groups() if g is not None), None)
        if raw is None:
            continue
        amount = _clean_amount(raw)
        if amount <= 0 or amount > 1_000_000:
            continue

        before = sms_text[max(0, m.start() - 60) : m.start()].lower()
        after = sms_text[m.end() : m.end() + 60].lower()
        by_kw = any(k in before or k in after for k in _CREDIT_KEYWORDS)
        candidates.append((by_kw and m.start() >= 0, amount))

    if not candidates:
        return None

    # Prefer an amount tied to a credit keyword; otherwise take the first amount.
    chosen = next((amount for scored, amount in candidates if scored), candidates[0][1])

    ref_match = _REF_RE.search(sms_text)
    reference = None
    if ref_match:
        reference = ref_match.group(1)
    else:
        tb_match = _TELEBIRR_REF_RE.search(sms_text)
        if tb_match:
            reference = tb_match.group(1)
        else:
            # Fall back to a long digit run (transaction digits).
            digit = _DIGIT_RUN_RE.search(sms_text)
            if digit:
                reference = digit.group(1)

    return {"amount": chosen, "reference": reference}