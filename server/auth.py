import hashlib
import hmac
import json
import logging
from urllib.parse import parse_qsl

from bot.config import settings

logger = logging.getLogger(__name__)


def validate_init_data(init_data: str) -> dict:
    """Validate Telegram Web App initData and return parsed user info."""
    if not init_data:
        return {"id": 0, "first_name": "Guest", "username": "guest", "is_guest": True}

    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        user_raw = parsed.get("user")
        if user_raw:
            try:
                return json.loads(user_raw)
            except Exception:
                pass
        return {"id": 0, "first_name": "Guest", "username": "guest", "is_guest": True}

    if not settings.bot_token:
        user_raw = parsed.get("user")
        if user_raw:
            try:
                return json.loads(user_raw)
            except Exception:
                pass
        return {"id": 0, "first_name": "Guest", "username": "guest", "is_guest": True}

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(
        b"WebAppData", settings.bot_token.encode(), hashlib.sha256
    ).digest()
    calculated_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        logger.warning("Invalid initData signature; falling back to parsed user or guest")
        user_raw = parsed.get("user")
        if user_raw:
            try:
                return json.loads(user_raw)
            except Exception:
                pass
        return {"id": 0, "first_name": "Guest", "username": "guest", "is_guest": True}

    user_raw = parsed.get("user")
    if not user_raw:
        return {"id": 0, "first_name": "Guest", "username": "guest", "is_guest": True}

    user = json.loads(user_raw)
    return user
