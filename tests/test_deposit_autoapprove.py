import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from bot import database as db
from bot.sms_parser import fingerprint, parse_deposit_sms


_LOOP = asyncio.new_event_loop()


def run(coro):
    return _LOOP.run_until_complete(coro)


def close_loop():
    _LOOP.close()


class TestSmsParser(unittest.TestCase):
    def test_telebirr_english_receipt(self):
        text = (
            "Telebirr: You have received ETB 500.00 from Walta Trading. "
            "Transaction ID: 873402918452. Date: 06/09/2026 14:32:45. "
            "Balance: 1,250.00 ETB."
        )
        parsed = parse_deposit_sms(text)
        self.assertIsNotNone(parsed)
        self.assertAlmostEqual(parsed["amount"], 500.00)
        self.assertEqual(parsed["reference"], "873402918452")

    def test_cbe_birr_credit(self):
        text = (
            "CBE Birr: Your account has been credited with ETB 200.00. "
            "Ref: 9876543210. Available balance: 2,000.00 ETB."
        )
        parsed = parse_deposit_sms(text)
        self.assertIsNotNone(parsed)
        self.assertAlmostEqual(parsed["amount"], 200.00)
        self.assertEqual(parsed["reference"], "9876543210")

    def test_telebirr_amharic_receipt(self):
        text = "የተከፈለ ገንዘብ 500.00 ብር ነው። የግብይት ቁጥር: 1234567890"
        parsed = parse_deposit_sms(text)
        self.assertIsNotNone(parsed)
        self.assertAlmostEqual(parsed["amount"], 500.00)

    def test_amount_suffix(self):
        text = "Payment Confirmed: 120.50 ETB received. Receipt No: ABC12345678."
        parsed = parse_deposit_sms(text)
        self.assertIsNotNone(parsed)
        self.assertAlmostEqual(parsed["amount"], 120.50)
        self.assertEqual(parsed["reference"], "ABC12345678")

    def test_thousands_separator_is_not_an_amount(self):
        # "1,250.00 ETB" is a balance, not the credited amount.
        text = (
            "Your account has been credited with ETB 100.00. "
            "Available balance: 1,250.00 ETB."
        )
        parsed = parse_deposit_sms(text)
        self.assertIsNotNone(parsed)
        self.assertAlmostEqual(parsed["amount"], 100.00)
        self.assertNotAlmostEqual(parsed["amount"], 250.00)

    def test_no_amount_returns_none(self):
        self.assertIsNone(parse_deposit_sms("I sent the money please add it"))
        self.assertIsNone(parse_deposit_sms("   "))

    def test_fingerprint_is_normalized(self):
        self.assertEqual(fingerprint("Abc 123 - ብር"), fingerprint(" abc123ብር "))


class TestAutoCreditDeposit(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig_settings = db.settings
        run(db.close_db())
        db.settings = SimpleNamespace(database_path=Path(self._tmp) / "test.db")
        run(db.init_db())
        run(db.create_user(777001, "0900000001", "tester1", "Tester One"))

    def tearDown(self):
        run(db.close_db())
        db.settings = self._orig_settings

    def test_deposit_credited_once_per_receipt(self):
        text = "Telebirr: You have received ETB 500.00. Transaction ID: 873402918452."
        fp = fingerprint(text)

        credited, new_balance, already_used = run(
            db.auto_credit_deposit(777001, 500.00, fp, "TELEBIRR SMS")
        )
        self.assertTrue(credited)
        self.assertFalse(already_used)
        self.assertAlmostEqual(new_balance, 500.00)

        credited, new_balance, already_used = run(
            db.auto_credit_deposit(777001, 500.00, fp, "TELEBIRR SMS")
        )
        self.assertFalse(credited)
        self.assertTrue(already_used)
        self.assertAlmostEqual(new_balance, 500.00)

    def test_different_receipts_credit_separately(self):
        fp1 = fingerprint("Telebirr: received ETB 100.00. Transaction ID: 111111111111.")
        fp2 = fingerprint("Telebirr: received ETB 200.00. Transaction ID: 222222222222.")

        _, bal1, _ = run(db.auto_credit_deposit(777001, 100.00, fp1, "TELEBIRR SMS"))
        _, bal2, _ = run(db.auto_credit_deposit(777001, 200.00, fp2, "TELEBIRR SMS"))
        self.assertAlmostEqual(bal1, 100.00)
        self.assertAlmostEqual(bal2, 300.00)


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        close_loop()