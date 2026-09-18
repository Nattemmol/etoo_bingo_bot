import asyncio
import tempfile
import unittest
from base64 import b64encode
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from Crypto.PublicKey import RSA

from bot import database as db
from bot import telebirr as tb

_LOOP = asyncio.new_event_loop()


def run(coro):
    return _LOOP.run_until_complete(coro)


class TestTeleBirrSignature(unittest.TestCase):
    def setUp(self):
        key = RSA.generate(2048)
        self.private_pem = key.export_key().decode()
        self.private_b64 = b64encode(key.export_key(format="DER", pkcs=8)).decode()
        self.public_pem = key.publickey().export_key().decode()
        self.public_b64 = b64encode(key.publickey().export_key(format="DER")).decode()

        self.payload = {
            "appid": "1694675186560001",
            "merch_code": "276644",
            "merch_order_id": "1754743012412123456",
            "payment_order_id": "00801104C911443200001002",
            "total_amount": "10.00",
            "trans_currency": "ETB",
            "trade_status": "Completed",
            "trans_end_time": "1754743012345",
            "notify_time": "1754743012345",
            "callback_info": "123456",
            "sign_type": "SHA256WithRSA",
        }

    def _settings(self, public_key: str) -> SimpleNamespace:
        return SimpleNamespace(
            telebirr_private_key=self.private_b64,
            telebirr_public_key=public_key,
        )

    def test_roundtrip_valid_signature(self):
        settings = self._settings(self.public_b64)
        with patch.object(tb, "settings", settings):
            signed = dict(self.payload)
            signed["sign"] = tb._sign(signed)
            self.assertTrue(tb.verify_callback_signature(signed))

    def test_pem_wrapped_public_key_accepted(self):
        # Config can hold a full PEM public key too.
        settings = self._settings(self.public_pem)
        with patch.object(tb, "settings", settings):
            signed = dict(self.payload)
            signed["sign"] = tb._sign(signed)
            self.assertTrue(tb.verify_callback_signature(signed))

    def test_tampered_payload_rejected(self):
        settings = self._settings(self.public_b64)
        with patch.object(tb, "settings", settings):
            signed = dict(self.payload)
            signed["sign"] = tb._sign(signed)
            tampered = dict(signed)
            tampered["total_amount"] = "9999.00"
            self.assertFalse(tb.verify_callback_signature(tampered))

    def test_missing_sign_rejected(self):
        settings = self._settings(self.public_b64)
        with patch.object(tb, "settings", settings):
            self.assertFalse(tb.verify_callback_signature(dict(self.payload)))

    def test_skipped_when_no_public_key_configured(self):
        settings = self._settings("")
        with patch.object(tb, "settings", settings):
            signed = dict(self.payload)
            signed["sign"] = tb._sign(signed)
            self.assertIsNone(tb.verify_callback_signature(signed))


class TestCompleteTelebirrOrderIdempotency(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig_settings = db.settings
        db.settings = SimpleNamespace(database_path=Path(self._tmp) / "test.db")
        run(db.init_db())
        run(db.create_user(777301, "0900000021", "payer1", "Payer One"))
        run(db.update_balance(777301, 0.0))

    def tearDown(self):
        db.settings = self._orig_settings

    def test_order_completed_only_once(self):
        run(db.create_telebirr_order("ORDER-1", 777301, 50.0, "https://example/checkout"))

        credited, new_balance, telegram_id = run(db.complete_telebirr_order("ORDER-1"))
        self.assertTrue(credited)
        self.assertAlmostEqual(new_balance, 50.0)
        self.assertEqual(telegram_id, 777301)

        # Second notify for the same order must NOT double-credit.
        credited, new_balance, _ = run(db.complete_telebirr_order("ORDER-1"))
        self.assertFalse(credited)
        self.assertAlmostEqual(new_balance, 50.0)

    def test_unknown_order_not_credited(self):
        credited, new_balance, telegram_id = run(db.complete_telebirr_order("MISSING"))
        self.assertFalse(credited)
        self.assertEqual(new_balance, 0.0)
        self.assertEqual(telegram_id, 0)


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        _LOOP.close()