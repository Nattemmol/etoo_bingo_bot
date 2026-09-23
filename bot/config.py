import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    bot_token: str
    webapp_url: str
    database_path: Path
    server_port: int
    super_bingo_always_open: bool = False
    # Telebirr merchant credentials
    telebirr_base_url: str = "https://196.188.120.3:38443/apiaccess/payment/gateway"
    telebirr_fabric_app_id: str = ""
    telebirr_app_secret: str = ""
    telebirr_merchant_app_id: str = ""
    telebirr_merchant_code: str = ""
    telebirr_web_base_url: str = "https://developerportal.ethiotelebirr.et:38443/payment/web/paygate?"
    telebirr_private_key: str = ""
    telebirr_public_key: str = ""
    # PeerPay payment webhooks (https://peerpayment.org/docs/webhooks)
    peerpay_api_key: str = ""
    peerpay_base_url: str = "https://api.peerpayment.org"
    peerpay_webhook_secret: str = ""
    # Verify.et transaction verification (https://verify.et/docs/api)
    verify_et_api_key: str = ""
    verify_et_base_url: str = "https://verify.et"

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("BOT_TOKEN", "")
        if not token:
            raise ValueError("BOT_TOKEN is not set. Copy .env.example to .env and add your token.")

        db_path = Path(os.getenv("DATABASE_PATH", "goodbingo.db"))
        always_open = os.getenv("SUPER_BINGO_ALWAYS_OPEN", "false").lower() in ("1", "true", "yes")
        return cls(
            bot_token=token,
            webapp_url=os.getenv("WEBAPP_URL", "https://example.com").rstrip("/"),
            database_path=db_path if db_path.is_absolute() else BASE_DIR / db_path,
            server_port=int(os.getenv("SERVER_PORT", "8080")),
            super_bingo_always_open=always_open,
            telebirr_base_url=os.getenv(
                "TELEBIRR_BASE_URL",
                "https://196.188.120.3:38443/apiaccess/payment/gateway",
            ),
            telebirr_fabric_app_id=os.getenv("TELEBIRR_FABRIC_APP_ID", ""),
            telebirr_app_secret=os.getenv("TELEBIRR_APP_SECRET", ""),
            telebirr_merchant_app_id=os.getenv("TELEBIRR_MERCHANT_APP_ID", ""),
            telebirr_merchant_code=os.getenv("TELEBIRR_MERCHANT_CODE", ""),
            telebirr_web_base_url=os.getenv(
                "TELEBIRR_WEB_BASE_URL",
                "https://developerportal.ethiotelebirr.et:38443/payment/web/paygate?",
            ),
            telebirr_private_key=os.getenv("TELEBIRR_PRIVATE_KEY", ""),
            telebirr_public_key=os.getenv("TELEBIRR_PUBLIC_KEY", ""),
            peerpay_api_key=os.getenv("PEERPAY_API_KEY", ""),
            peerpay_base_url=os.getenv("PEERPAY_BASE_URL", "https://api.peerpayment.org"),
            peerpay_webhook_secret=os.getenv("PEERPAY_WEBHOOK_SECRET", ""),
            verify_et_api_key=os.getenv("VERIFY_ET_API_KEY", ""),
            verify_et_base_url=os.getenv("VERIFY_BASE_URL", "https://verify.et"),
        )


settings = Settings.from_env()
