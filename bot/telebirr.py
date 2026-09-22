"""
Telebirr H5 Checkout integration for GoodBingo.

Flow:
  1. get_fabric_token()           -> bearer token
  2. create_order(...)            -> (merch_order_id, checkout_url)
  3. User pays in Telebirr app
  4. Telebirr POSTs to /telebirr/notify  -> verify & credit balance
  5. (backup) query_order(...)    -> check status manually
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from base64 import b64decode, b64encode

import httpx

try:
    from Crypto.Hash import SHA256
    from Crypto.PublicKey import RSA
    from Crypto.Signature import pkcs1_15
    _HAS_CRYPTO = True
except ImportError:
    SHA256 = RSA = pkcs1_15 = None  # type: ignore[assignment]
    _HAS_CRYPTO = False

from bot.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _nonce_str() -> str:
    return uuid.uuid4().hex  # 32-char hex, no special chars


def _timestamp() -> str:
    return str(int(time.time()))


def _merch_order_id() -> str:
    """Unique order ID: timestamp-millis + 6 random hex chars."""
    return f"{int(time.time() * 1000)}{uuid.uuid4().hex[:6]}".upper()


def _canonical_string(payload: dict) -> str:
    """
    Canonical source string for SHA256WithRSA signature:
    all top-level fields (minus excluded ones), sorted alphabetically,
    joined as key=value with '&'; biz_content is flattened one level deep.
    """
    exclude = {"sign", "sign_type", "header", "refund_info", "openType", "raw_request"}
    parts: list[str] = []

    for key in sorted(payload.keys()):
        if key in exclude:
            continue
        value = payload[key]
        if key == "biz_content" and isinstance(value, dict):
            for bk in sorted(value.keys()):
                parts.append(f"{bk}={value[bk]}")
        else:
            parts.append(f"{key}={value}")

    return "&".join(parts)


def _load_private_key() -> RSA.RsaKey:
    return RSA.import_key(_pem_from_base64(settings.telebirr_private_key, "PRIVATE KEY"))


def _load_public_key(pem_or_b64: str) -> RSA.RsaKey:
    return RSA.import_key(_pem_from_base64(pem_or_b64, "PUBLIC KEY"))


def _pem_from_base64(body: str, header: str) -> str:
    """Wrap a raw base64 key body in PEM if it isn't PEM already."""
    body = body.strip()
    if body.startswith("-----"):
        return body
    return (
        f"-----BEGIN {header}-----\n"
        + "\n".join(body[i : i + 64] for i in range(0, len(body), 64))
        + f"\n-----END {header}-----"
    )


def _sign(payload: dict) -> str:
    """
    SHA256withRSA signature over the canonical key=value string of all
    non-excluded top-level fields (flattening biz_content one level deep).
    """
    sign_str = _canonical_string(payload)
    logger.debug("Sign source string: %s", sign_str)

    key = _load_private_key()
    h = SHA256.new(sign_str.encode("utf-8"))
    signature = pkcs1_15.new(key).sign(h)
    return b64encode(signature).decode("utf-8")


def verify_callback_signature(payload: dict) -> bool | None:
    """
    Verify Telebirr's RSA signature on an async payment notification.

    Payload must contain 'sign' (and ideally 'sign_type').
    Returns True if the signature is valid, False if invalid.
    Returns None when no public key is configured (verification skipped).
    """
    if not settings.telebirr_public_key:
        logger.warning("TELEBIRR_PUBLIC_KEY not set — skipping callback signature check")
        return None

    signature = payload.get("sign", "")
    if not signature:
        logger.warning("Telebirr notify missing 'sign' field")
        return False

    sign_str = _canonical_string(payload)
    try:
        key = _load_public_key(settings.telebirr_public_key)
        h = SHA256.new(sign_str.encode("utf-8"))
        pkcs1_15.new(key).verify(h, b64decode(signature))
        return True
    except (ValueError, TypeError) as e:
        logger.warning("Telebirr callback signature verification failed: %s", e)
        return False


def _build_checkout_url(prepay_id: str) -> str:
    """Build the H5 checkout URL from a prepayId."""
    payload = {
        "appid": settings.telebirr_merchant_app_id,
        "merch_code": settings.telebirr_merchant_code,
        "nonce_str": _nonce_str(),
        "prepay_id": prepay_id,
        "timestamp": _timestamp(),
    }
    sign = _sign(payload)
    raw_request = "&".join(f"{k}={payload[k]}" for k in sorted(payload)) + f"&sign={sign}&sign_type=SHA256WithRSA"
    return settings.telebirr_web_base_url + raw_request + "&version=1.0&trade_type=Checkout"


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------

async def get_fabric_token() -> str:
    """Obtain a short-lived bearer token from the Fabric auth endpoint."""
    auth_url = settings.telebirr_base_url.replace(
        "/apiaccess/payment/gateway", "/apiaccess/token/payment"
    )
    headers = {
        "Content-Type": "application/json",
        "X-APP-Key": settings.telebirr_fabric_app_id,
    }
    payload = {"appSecret": settings.telebirr_app_secret}

    async with httpx.AsyncClient(verify=False, timeout=15) as client:
        resp = await client.post(auth_url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    token = data.get("token") or data.get("access_token") or data.get("Token")
    if not token:
        raise ValueError(f"Telebirr token response missing token field: {data}")
    return token


async def create_order(
    telegram_id: int,
    amount: float,
    notify_url: str,
    title: str = "GoodBingo Deposit",
) -> tuple[str, str]:
    """
    Create a Telebirr pre-order.
    Returns (merch_order_id, checkout_url).
    Raises on API failure.
    """
    fabric_token = await get_fabric_token()
    merch_order_id = _merch_order_id()

    biz_content = {
        "notify_url": notify_url,
        "business_type": "BuyGoods",
        "trade_type": "InApp",
        "appid": settings.telebirr_merchant_app_id,
        "merch_code": settings.telebirr_merchant_code,
        "merch_order_id": merch_order_id,
        "title": title,
        "total_amount": f"{amount:.2f}",
        "trans_currency": "ETB",
        "timeout_express": "120m",
        "payee_identifier": settings.telebirr_merchant_code,
        "payee_identifier_type": "04",
        "payee_type": "5000",
        # Pass telegram_id as callback_info so we get it back in the notify
        "callback_info": str(telegram_id),
    }

    body = {
        "nonce_str": _nonce_str(),
        "method": "payment.preorder",
        "timestamp": _timestamp(),
        "version": "1.0",
        "biz_content": biz_content,
        "sign_type": "SHA256WithRSA",
    }
    body["sign"] = _sign(body)

    headers = {
        "Content-Type": "application/json",
        "X-APP-Key": settings.telebirr_fabric_app_id,
        "Authorization": fabric_token,
    }

    async with httpx.AsyncClient(verify=False, timeout=20) as client:
        resp = await client.post(settings.telebirr_base_url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

    logger.info("Telebirr createOrder response: %s", data)

    if str(data.get("code", "")) != "0" and data.get("result") not in ("SUCCESS", "success"):
        raise ValueError(f"Telebirr createOrder failed: {data.get('msg', data)}")

    biz = data.get("biz_content", {})
    prepay_id = biz.get("prepay_id")
    if not prepay_id:
        raise ValueError(f"No prepay_id in response: {data}")

    checkout_url = _build_checkout_url(prepay_id)
    return merch_order_id, checkout_url


async def query_order(merch_order_id: str) -> str:
    """
    Query order status. Returns trade_status string e.g. PAY_SUCCESS / WAIT_PAY.
    """
    fabric_token = await get_fabric_token()

    biz_content = {
        "appid": settings.telebirr_merchant_app_id,
        "merch_code": settings.telebirr_merchant_code,
        "merch_order_id": merch_order_id,
    }
    body = {
        "nonce_str": _nonce_str(),
        "method": "payment.queryorder",
        "timestamp": _timestamp(),
        "version": "1.0",
        "biz_content": biz_content,
        "sign_type": "SHA256WithRSA",
    }
    body["sign"] = _sign(body)

    query_url = settings.telebirr_base_url  # same gateway endpoint
    headers = {
        "Content-Type": "application/json",
        "X-APP-Key": settings.telebirr_fabric_app_id,
        "Authorization": fabric_token,
    }

    async with httpx.AsyncClient(verify=False, timeout=15) as client:
        resp = await client.post(query_url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

    logger.info("Telebirr queryOrder response: %s", data)
    biz = data.get("biz_content", {})
    return biz.get("trade_status", "UNKNOWN")
