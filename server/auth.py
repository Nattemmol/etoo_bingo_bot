import hashlib
import hmac
import json
from urllib.parse import parse_qsl

from bot.config import settings


def validate_init_data(init_data: str) -> dict:
    """Validate Telegram Web App initData and return parsed user info."""
    if not init_data:
        raise ValueError("Missing initData")

    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise ValueError("Missing hash in initData")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(
        b"WebAppData", settings.bot_token.encode(), hashlib.sha256
    ).digest()
    calculated_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise ValueError("Invalid initData signature")

    user_raw = parsed.get("user")
    if not user_raw:
        raise ValueError("Missing user in initData")

    user = json.loads(user_raw)
    return user
