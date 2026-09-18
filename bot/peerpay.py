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
    """Validate a PeerPay ``PeerPay-Signature`` header (timing-safe compare).

    Returns True when no secret is configured (dev mode), matching the
    project's dev policy. Otherwise requires ``v2=<hex>``, a
    plausible integer Unix timestamp inside the tolerance window, and an
    HMAC-SHA256 match over ``event_id.timestamp.raw_body``.
    """
    if not secret:
        logger.info("PeerPay webhook signature check skipped (no PEERPAY_WEBHOOK_SECRET).")
        return True

    if not signature_header or not signature_header.startswith(_PREFIX):
        return False

    received = signature_header[len(_PREFIX):].strip().lower()
    if len(received) != 64:
        return False

    try:
        ts = float(timestamp)
    except (TypeError, ValueError):
        return False
    if not ts or abs(time.time() - ts) > tolerance_seconds:
        return False

    message = f"{event_id}.{timestamp}.".encode("utf-8") + (raw_body or b"")
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    expected = hmac.new(key, message, hashlib.sha256).hexdigest()

    if hmac.compare_digest(received, expected):
        return True

    # Also check against PEERPAY_API_KEY in case user used API key as secret
    if settings.peerpay_api_key and settings.peerpay_api_key != secret:
        alt_key = settings.peerpay_api_key.encode("utf-8")
        alt_expected = hmac.new(alt_key, message, hashlib.sha256).hexdigest()
        if hmac.compare_digest(received, alt_expected):
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
        payload: dict[str, Any] = {
            "merchant_customer_id": merchant_customer_id,
            "currency": "ETB",
            "return_url": return_url or f"{settings.webapp_url}/deposits/return",
        }
        if amount is not None:
            payload["amount"] = f"{float(amount):.2f}"
        if payment_method:
            payload["payment_method"] = payment_method

        key = idempotency_key or f"dep-{merchant_customer_id}-{uuid.uuid4().hex[:12]}"
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
        payload: dict[str, Any] = {
            "merchant_customer_id": merchant_customer_id,
            "amount": f"{float(amount):.2f}",
            "currency": "ETB",
        }
        if return_url:
            payload["return_url"] = return_url
        if destination:
            payload["destination"] = destination
        if metadata:
            payload["metadata"] = metadata

        key = idempotency_key or f"wd-{merchant_customer_id}-{uuid.uuid4().hex[:12]}"
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