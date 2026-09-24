"""
PeerPay API client and webhook receiver helpers.

PeerPay (https://peerpayment.org/docs) is a server-to-server payment API for
Ethiopian bank transfers and mobile wallets (Telebirr, CBE Birr).
"""

import hashlib
import hmac
import logging
import time
import uuid
from typing import Any

import httpx

from bot.config import settings

logger = logging.getLogger(__name__)

_PREFIX = "v2="
_WINDOW_SECONDS = 300
_API_VERSION = "2026-06-01"
_CHECKOUT_API_BASE = "https://api.peerpayment.org/checkout-api"


def verify_peerpay_signature(
    secret: str,
    event_id: str,
    timestamp: str,
    raw_body: bytes,
    signature_header: str,
    *,
    tolerance_seconds: int = _WINDOW_SECONDS,
) -> bool:
    """Validate a PeerPay signature header safely.

    Accepts multiple signature formats:
    - v2=<hex>
    - v1=<hex>
    - sha256=<hex>
    - t=<ts>,v1=<hex>
    - bare 64-character hex string
    """
    if not secret:
        logger.info("PeerPay webhook signature check skipped (no PEERPAY_WEBHOOK_SECRET).")
        return True

    if not signature_header:
        return False

    # Extract all candidate signatures from header
    candidates: list[str] = []
    header_clean = signature_header.strip()
    for part in header_clean.replace(";", ",").split(","):
        p = part.strip()
        if "=" in p:
            k, v = p.split("=", 1)
            k = k.strip().lower()
            v = v.strip().lower()
            if k in ("v2", "v1", "sha256") and len(v) == 64:
                candidates.append(v)
            elif k == "t" and not timestamp:
                timestamp = v
        elif len(p) == 64:
            candidates.append(p.lower())

    if not candidates:
        return False

    # Normalize timestamp (support seconds and milliseconds)
    ts_val = 0.0
    if timestamp:
        try:
            ts_val = float(timestamp)
            if ts_val > 1e11:  # Milliseconds
                ts_val = ts_val / 1000.0
        except (TypeError, ValueError):
            ts_val = 0.0

    # Tolerance check if timestamp exists
    if ts_val > 0 and abs(time.time() - ts_val) > tolerance_seconds:
        logger.warning("PeerPay webhook signature rejected — timestamp expired (%s)", timestamp)
        return False

    # Candidate message constructions
    messages_to_try: list[bytes] = [
        f"{event_id}.{timestamp}.".encode("utf-8") + (raw_body or b""),
        f"{timestamp}.".encode("utf-8") + (raw_body or b""),
        (raw_body or b""),
    ]
    if timestamp and raw_body:
        try:
            messages_to_try.append(f"{timestamp}.{raw_body.decode('utf-8', 'ignore')}".encode("utf-8"))
        except Exception:
            pass

    keys_to_try: list[bytes] = [secret.encode("utf-8") if isinstance(secret, str) else secret]
    if settings.peerpay_api_key and settings.peerpay_api_key != secret:
        keys_to_try.append(settings.peerpay_api_key.encode("utf-8"))

    for key in keys_to_try:
        for msg_bytes in messages_to_try:
            expected = hmac.new(key, msg_bytes, hashlib.sha256).hexdigest()
            for cand in candidates:
                if hmac.compare_digest(cand, expected):
                    return True

    return False


def customer_id_to_telegram_id(merchant_customer_id: Any) -> int | None:
    """Map a PeerPay ``merchant_customer_id`` back to a GoodBingo telegram id.

    Checkout creation passes ``tg_<telegram_id>``; a bare numeric string is
    also accepted so the mapping survives manual payloads.
    """
    if merchant_customer_id is None:
        return None
    value = str(merchant_customer_id).strip()
    if not value:
        return None
    numeric = value.lstrip("-").isdigit()
    if numeric:
        return int(value)
    lowered = value.lower()
    for prefix in ("tg_", "tg", "user_", "u_"):
        if lowered.startswith(prefix) and value[len(prefix):].lstrip("-").isdigit():
            return int(value[len(prefix):])
    return None


def extract_token_from_url(checkout_url: str) -> str:
    """Extract public token `ptk_...` from checkout url or return bare token."""
    if not checkout_url:
        return ""
    clean = checkout_url.strip().rstrip("/")
    return clean.split("/")[-1]


class PeerPayClient:
    """Async HTTP client for PeerPayment.org REST and Checkout APIs."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 15.0,
    ):
        self.api_key = api_key if api_key is not None else settings.peerpay_api_key
        self.base_url = (base_url or settings.peerpay_base_url or "https://api.peerpayment.org").rstrip("/")
        self.timeout = timeout

    def _auth_headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "PeerPay-Version": _API_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    async def create_deposit(
        self,
        merchant_customer_id: str,
        amount: float | str | None = None,
        payment_method: str | None = None,
        return_url: str | None = None,
        metadata: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a deposit on PeerPay.

        Returns the response dict containing data: {id, checkout_url, status, payment_options, ...}.
        """
        resolved_return_url = (
            return_url
            or settings.peerpay_return_url
            or f"{settings.webapp_url}/deposits/return"
        )
        payload: dict[str, Any] = {
            "merchant_customer_id": merchant_customer_id,
            "currency": "ETB",
            "return_url": resolved_return_url,
        }
        if amount is not None:
            try:
                amt_f = float(str(amount).replace(",", ".").strip())
                if amt_f > 0:
                    payload["amount"] = f"{amt_f:.2f}"
            except (ValueError, TypeError):
                pass
        if payment_method:
            payload["payment_method"] = payment_method

        key = idempotency_key or f"etoobingo-deposit-{uuid.uuid4().hex}"
        headers = self._auth_headers(idempotency_key=key)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/v1/deposits",
                headers=headers,
                json=payload,
            )
            try:
                data = resp.json()
            except Exception:
                data = {"status_code": resp.status_code, "text": resp.text}
            if resp.status_code not in (200, 201):
                err = data.get("error", {}) if isinstance(data, dict) else {}
                code = err.get("code", "unknown_error")
                msg = err.get("message", resp.text)
                logger.warning("PeerPay create_deposit failed: %s (%s) - %s", code, resp.status_code, msg)
            return data

    async def submit_deposit_reference(
        self,
        checkout_token_or_url: str,
        reference: str,
        payment_method: str = "telebirr",
        phone: str | None = None,
    ) -> dict[str, Any]:
        """Submit payment transaction reference to PeerPay Checkout API.

        This triggers PeerPay's automated bank/wallet verification against the assigned receiving account.
        """
        token = extract_token_from_url(checkout_token_or_url)
        url = f"{_CHECKOUT_API_BASE}/c/{token}/references"

        payload: dict[str, Any] = {
            "payment_method": payment_method.lower(),
            "reference": reference.strip(),
        }
        if phone:
            payload["supplementary_fields"] = {"phone": phone.strip()}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                json=payload,
            )
            try:
                data = resp.json()
            except Exception:
                data = {"status_code": resp.status_code, "text": resp.text}
            return data

    async def submit_and_verify_reference(
        self,
        deposit_id: str,
        checkout_url: str,
        reference: str,
        payment_method: str = "telebirr",
        phone: str | None = None,
    ) -> dict[str, Any]:
        """Submit reference to checkout and fetch updated deposit status."""
        # 1. Submit reference to checkout API
        sub_res = await self.submit_deposit_reference(
            checkout_token_or_url=checkout_url,
            reference=reference,
            payment_method=payment_method,
            phone=phone,
        )
        if isinstance(sub_res, dict) and sub_res.get("error"):
            err_dict = sub_res["error"] if isinstance(sub_res["error"], dict) else {}
            return {
                "ok": False,
                "error": err_dict.get("message", "PeerPay rejected this reference."),
                "code": err_dict.get("code", "rejected"),
            }

        # 2. Fetch authoritative deposit status
        dep_res = await self.get_deposit(deposit_id)
        dep_data = dep_res.get("data") or {}
        status = dep_data.get("status", "verification_pending")
        amount = None
        try:
            amount = float(dep_data.get("amount") or 0)
        except Exception:
            pass

        return {
            "ok": True,
            "status": status,
            "amount": amount,
            "data": dep_data,
            "verified": dep_data.get("verification", {}).get("verified", False),
        }

    async def get_deposit(self, deposit_id: str) -> dict[str, Any]:
        """Fetch current status and verification state of a deposit."""
        headers = self._auth_headers()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                f"{self.base_url}/v1/deposits/{deposit_id}",
                headers=headers,
            )
            return resp.json()

    async def create_withdrawal(
        self,
        merchant_customer_id: str,
        amount: float | str,
        destination: dict[str, str] | None = None,
        return_url: str | None = None,
        metadata: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a withdrawal request on PeerPay."""
        resolved_return_url = (
            return_url
            or settings.peerpay_return_url
            or f"{settings.webapp_url}/withdrawals/return"
        )
        payload: dict[str, Any] = {
            "merchant_customer_id": merchant_customer_id,
            "amount": f"{float(amount):.2f}",
            "currency": "ETB",
            "return_url": resolved_return_url,
        }
        if destination:
            payload["destination"] = destination
        if metadata:
            payload["metadata"] = metadata

        key = idempotency_key or f"etoobingo-withdrawal-{uuid.uuid4().hex}"
        headers = self._auth_headers(idempotency_key=key)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/v1/withdrawals",
                headers=headers,
                json=payload,
            )
            try:
                data = resp.json()
            except Exception:
                data = {"status_code": resp.status_code, "text": resp.text}
            if resp.status_code not in (200, 201):
                err = data.get("error", {}) if isinstance(data, dict) else {}
                code = err.get("code", "unknown_error")
                msg = err.get("message", resp.text)
                logger.warning("PeerPay create_withdrawal failed: %s (%s) - %s", code, resp.status_code, msg)
            return data

    async def confirm_withdrawal_destination(
        self,
        checkout_token_or_url: str,
        bank: str,
        account_number: str,
    ) -> dict[str, Any]:
        """Confirm destination account on PeerPay hosted checkout."""
        token = extract_token_from_url(checkout_token_or_url)
        url = f"{_CHECKOUT_API_BASE}/w/{token}/confirm"

        payload = {
            "destination": {
                "bank": bank.lower(),
                "account_number": account_number.strip(),
            }
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                json=payload,
            )
            try:
                return resp.json()
            except Exception:
                return {"status_code": resp.status_code, "text": resp.text}

    async def get_withdrawal(self, withdrawal_id: str) -> dict[str, Any]:
        """Fetch current status and verification state of a withdrawal."""
        headers = self._auth_headers()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                f"{self.base_url}/v1/withdrawals/{withdrawal_id}",
                headers=headers,
            )
            return resp.json()