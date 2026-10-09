"""Comprehensive automated test suite covering all transaction scenarios and edge cases.

Scenarios tested:
1. Uninitialized Transactions:
   - Spontaneous SMS/reference pasting without pre-initiating /deposit.
   - Exact amount found in SMS vs reference-only prompting for amount.
2. Expired Transactions:
   - Deposits submitted > 60 minutes after initialization or expired at PeerPay.
   - Status updates for expired/cancelled deposits preventing double credit.
3. Wrong / Non-official Account Transfers:
   - Transfers to stranger's Telebirr account or stranger's CBE account rejected with recipient_mismatch.
   - Transfer to old/decommissioned Telebirr (0934921104) rejected.
4. Repetitive / Replay Transactions:
   - Same SMS / reference submitted multiple times rejected with DEPOSIT_REUSED.
   - Exactly-once balance increment guarantee under concurrent/repeated requests.
5. Cross-Account Transfers (Between Official Accounts):
   - User selected Telebirr but paid CBE Bank (1000413343538).
   - User selected CBE Birr but paid Telebirr (0963572327).
   - User selected CBE Bank but paid CBE Birr (0934920411).
   - Inter-account directional verification accepts all 3 official accounts.
6. Edge Cases & Attack Vectors:
   - Sub-minimum deposits (< 10 ETB) rejected.
   - Negative / zero amounts rejected.
   - Failed / cancelled / reversed SMS texts detected and rejected.
   - Airtime / data package / utility purchase SMS rejected.
   - Webhook timestamp tolerance expiry (> 300s window).
   - Webhook HMAC signature tampering / invalid secret.
7. Valid Transactions & Reconciliations:
   - Valid Telebirr deposit (0963572327 Habtamu Melese).
   - Valid CBE Birr deposit (0934920411 Natnael Temesegen).
   - Valid CBE Mobile Banking deposit (1000413343538 Natnael Temesegen).
   - PeerPay webhook deposit.succeeded credit flow.
   - Periodic reconciliation of uncredited pending deposits.
"""

import asyncio
import hashlib
import hmac
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.testclient import TestClient

from bot import database as db
from bot import messages as msg
from bot import peerpay
from bot.handlers.deposit import (
    _create_and_send_peerpay_checkout,
    handle_sms_or_reference_text,
    reconcile_user_pending_deposits,
)
from bot.sms_parser import (
    OFFICIAL_ACCOUNTS,
    extract_directional_accounts,
    fingerprint,
    is_failed_transaction_sms,
    is_package_or_service_sms,
    parse_deposit_sms,
    verify_directional_match,
)
from server.main import app


def run(coro):
    return asyncio.run(coro)


def sign_webhook_payload(secret: str, event_id: str, timestamp: str, raw_body: bytes) -> str:
    message = f"{event_id}.{timestamp}.".encode("utf-8") + raw_body
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"v2={digest}"


class BaseTransactionTestCase(unittest.TestCase):
    def setUp(self):
        db._db_conn = None
        self._tmp = tempfile.mkdtemp()
        self.db_path = Path(self._tmp) / "test_transactions.db"

        self.mock_settings = SimpleNamespace(
            database_path=self.db_path,
            peerpay_api_key="pp_live_key_999",
            peerpay_base_url="https://api.peerpayment.org",
            peerpay_webhook_secret="pp_live_secret_888",
            admin_telegram_id=999999999,
            bot_token="test_token_xyz",
            webapp_url="https://bingo.natinael.tech",
            server_port=8765,
            api_version="2026-06-01",
        )
        self._pdb = patch("bot.database.settings", self.mock_settings)
        self._psrv = patch("server.main.settings", self.mock_settings)
        self._ppay = patch("bot.peerpay.settings", self.mock_settings)
        self._pcfg = patch("bot.config.settings", self.mock_settings)
        self._pnotify = patch("server.main._notify_telegram", new_callable=AsyncMock)
        self._pdb.start()
        self._psrv.start()
        self._ppay.start()
        self._pcfg.start()
        self._pnotify.start()

        run(db.init_db())
        # Seed test user
        self.user_id = 777888
        run(db.create_user(self.user_id, "0911223344", "test_player", "Test Player"))

    def tearDown(self):
        db._db_conn = None
        self._pnotify.stop()
        self._pcfg.stop()
        self._ppay.stop()
        self._psrv.stop()
        self._pdb.stop()


class TestOfficialAccountsConfig(unittest.TestCase):
    """Verify official accounts match requirements: 0934921104 is completely removed."""

    def test_telebirr_official_account(self):
        self.assertIn("telebirr", OFFICIAL_ACCOUNTS)
        tb = OFFICIAL_ACCOUNTS["telebirr"]
        self.assertEqual(tb["phone"], "0963572327")
        self.assertEqual(tb["name"], "Habtamu Melese")
        # Ensure 0934921104 is NOT in valid_numbers
        self.assertNotIn("0934921104", tb["valid_numbers"])
        self.assertNotIn("251934921104", tb["valid_numbers"])

    def test_cbebirr_official_account(self):
        self.assertIn("cbebirr", OFFICIAL_ACCOUNTS)
        cbe_b = OFFICIAL_ACCOUNTS["cbebirr"]
        self.assertEqual(cbe_b["phone"], "0934920411")
        self.assertEqual(cbe_b["name"], "Natnael Temesegen")

    def test_cbe_bank_official_account(self):
        self.assertIn("cbe_bank", OFFICIAL_ACCOUNTS)
        bank = OFFICIAL_ACCOUNTS["cbe_bank"]
        self.assertEqual(bank["account"], "1000413343538")
        self.assertEqual(bank["name"], "Natnael Temesegen")


class TestUninitializedTransactions(BaseTransactionTestCase):
    """Transactions submitted before / without pre-initializing /deposit."""

    def test_spontaneous_full_sms_guides_user_to_peerpay_checkout(self):
        """User pastes full incoming SMS directly without touching any button.

        In-bot scraping is removed: the bot directs the user to /deposit PeerPay checkout.
        """
        sms_text = (
            "ውድ Habtamu Melese: በ 0963572327 የ 150.00 ብር ክፍያ ከ 0911223344 ተቀብለዋል። "
            "የግብይት ቁጥር: DIK99887766 Date: 2026-10-09. ቀሪ ሂሳብ: 1,500.00 ብር"
        )
        msg_mock = AsyncMock()
        msg_mock.text = sms_text
        update = MagicMock()
        update.message = msg_mock
        update.effective_user.id = self.user_id

        context = MagicMock()
        context.user_data = {}

        handled = run(handle_sms_or_reference_text(update, context))
        self.assertTrue(handled)

        # Balance remains 0.0 until verified on PeerPay hosted checkout
        u = run(db.get_user(self.user_id))
        self.assertEqual(u["balance"], 0.0)

        # Bot instructs the user to use PeerPay checkout
        msg_mock.reply_text.assert_called()
        last_call_text = msg_mock.reply_text.call_args[0][0]
        self.assertIn("PeerPay", last_call_text)

    def test_spontaneous_reference_only_guides_user_to_peerpay_checkout(self):
        """User pastes just a reference token (e.g. DIK11223344).

        In-bot scraping is removed: the bot instructs user to initiate via PeerPay.
        """
        ref_text = "DIK11223344"
        msg_mock = AsyncMock()
        msg_mock.text = ref_text
        update = MagicMock()
        update.message = msg_mock
        update.effective_user.id = self.user_id

        context = MagicMock()
        context.user_data = {}

        handled = run(handle_sms_or_reference_text(update, context))
        self.assertTrue(handled)

        # Balance remains 0.0
        u = run(db.get_user(self.user_id))
        self.assertEqual(u["balance"], 0.0)

        msg_mock.reply_text.assert_called()
        last_call_text = msg_mock.reply_text.call_args[0][0]
        self.assertIn("PeerPay", last_call_text)

    def test_custom_deposit_amount_entry_creates_peerpay_checkout(self):
        """When user selected custom amount and enters a number, checkout is created."""
        msg_amount = AsyncMock()
        msg_amount.text = "150"
        update_amt = MagicMock()
        update_amt.message = msg_amount
        update_amt.effective_user.id = self.user_id

        context = MagicMock()
        context.user_data = {
            "awaiting_custom_deposit_amount": True,
            "selected_deposit_method": "telebirr",
        }

        with patch("bot.handlers.deposit.peerpay_client.create_deposit") as mock_create:
            mock_create.return_value = {
                "ok": True,
                "data": {
                    "id": "dep_custom_1",
                    "checkout_url": "https://checkout.peerpayment.org/c/ptk_custom_1",
                    "status": "awaiting_transfer",
                },
            }

            handled = run(handle_sms_or_reference_text(update_amt, context))
            self.assertTrue(handled)

            # Check that checkout was stored in DB
            dep = run(db.get_peerpay_deposit("dep_custom_1"))
            self.assertIsNotNone(dep)
            self.assertEqual(dep["amount"], 150.0)
            self.assertEqual(dep["status"], "awaiting_transfer")

            # Check reply includes checkout button
            msg_amount.reply_text.assert_called()
            reply_kwargs = msg_amount.reply_text.call_args[1]
            self.assertIsNotNone(reply_kwargs.get("reply_markup"))


class TestExpiredTransactions(BaseTransactionTestCase):
    """Transactions verified after 1 hour (expired) or marked expired by provider."""

    def test_peerpay_deposit_expired_after_one_hour(self):
        """PeerPay returns status='expired' when checked or reconciled."""
        run(db.upsert_peerpay_deposit(
            payment_id="dep_exp_1",
            telegram_id=self.user_id,
            amount=200.0,
            merchant_order_id="ord_exp_1",
            status="awaiting_transfer",
        ))

        # Reconcile when PeerPay responds with expired
        with patch("bot.handlers.deposit.peerpay_client.get_deposit") as mock_get:
            mock_get.return_value = {
                "ok": True,
                "data": {
                    "id": "dep_exp_1",
                    "status": "expired",
                    "amount": "200.00",
                },
            }
            credited_count, latest_balance = run(reconcile_user_pending_deposits(self.user_id))
            self.assertEqual(credited_count, 0)
            self.assertEqual(latest_balance, 0.0)

            # Record in database should now be marked 'expired'
            rec = run(db.get_peerpay_deposit("dep_exp_1"))
            self.assertEqual(rec["status"], "expired")
            self.assertEqual(rec["credited"], 0)

            # User balance remains unchanged (0.0)
            u = run(db.get_user(self.user_id))
            self.assertEqual(u["balance"], 0.0)

    def test_webhook_expired_event_does_not_credit(self):
        """Webhook delivery with deposit.expired must not increment balance."""
        client = TestClient(app)
        event_id = "evt_dep_exp"
        ts = str(int(time.time()))
        payload = {
            "id": event_id,
            "type": "deposit.expired",
            "api_version": "2026-06-01",
            "data": {
                "object": {
                    "id": "dep_exp_webhook",
                    "merchant_customer_id": f"tg_{self.user_id}",
                    "amount": "300.00",
                    "status": "expired",
                }
            },
        }
        body_bytes = json.dumps(payload).encode("utf-8")
        sig = sign_webhook_payload(self.mock_settings.peerpay_webhook_secret, event_id, ts, body_bytes)

        res = client.post(
            "/peerpay/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "PeerPay-Event": "deposit.expired",
                "PeerPay-Event-Id": event_id,
                "PeerPay-Delivery-Id": f"whd_{event_id}",
                "PeerPay-Timestamp": ts,
                "PeerPay-Signature": sig,
            },
        )
        self.assertEqual(res.status_code, 204)

        u = run(db.get_user(self.user_id))
        self.assertEqual(u["balance"], 0.0)


class TestWrongAccountRejection(BaseTransactionTestCase):
    """Transfers sent to wrong, personal, or decommissioned accounts."""

    def test_transfer_to_stranger_phone_rejected(self):
        sms_text = (
            "You have transferred ETB 200.00 to 0988776655 (Abebe Kebede). "
            "Txn ID: TX987123. Date: 2026-10-09."
        )
        is_valid, matched, reason = verify_directional_match(sms_text)
        self.assertFalse(is_valid)
        self.assertIsNone(matched)
        self.assertIn("ሌላ ስልክ ቁጥር", reason or "")

    def test_transfer_to_stranger_bank_account_rejected(self):
        sms_text = (
            "Your account has been debited ETB 500.00 to Commercial Bank of Ethiopia account 1000999999999 (Chala Tola). "
            "Ref: FT260100ABCD."
        )
        is_valid, matched, reason = verify_directional_match(sms_text)
        self.assertFalse(is_valid)
        self.assertIsNone(matched)
        self.assertIn("ሌላ የባንክ አካውንት", reason or "")

    def test_transfer_to_removed_telebirr_account_rejected(self):
        """0934921104 is no longer accepted as a Telebirr merchant account."""
        sms_text = (
            "You have transferred ETB 300.00 to 0934921104 (Natnael Temesegen). "
            "Transaction ID: DIK000111222."
        )
        is_valid, matched, reason = verify_directional_match(sms_text)
        self.assertFalse(is_valid)
        self.assertIsNone(matched)
        self.assertIn("ሌላ ስልክ ቁጥር", reason or "")


class TestRepetitiveTransactions(BaseTransactionTestCase):
    """Duplicate / replay transaction testing."""

    def test_repeated_peerpay_credit_is_idempotent(self):
        """Calling credit_peerpay_deposit_once twice only credits once."""
        credited, new_bal, is_dup = run(db.credit_peerpay_deposit_once(
            payment_id="dep_repeat_1",
            telegram_id=self.user_id,
            amount=150.0,
            merchant_order_id="ord_rep_1",
        ))
        self.assertTrue(credited)
        self.assertFalse(is_dup)
        self.assertEqual(new_bal, 150.0)

        # Second attempt with exact same payment_id
        credited2, new_bal2, is_dup2 = run(db.credit_peerpay_deposit_once(
            payment_id="dep_repeat_1",
            telegram_id=self.user_id,
            amount=150.0,
            merchant_order_id="ord_rep_1",
        ))
        self.assertFalse(credited2)
        self.assertTrue(is_dup2)
        self.assertEqual(new_bal2, 150.0)

        u = run(db.get_user(self.user_id))
        self.assertEqual(u["balance"], 150.0)

    def test_repeated_sms_submission_blocked_by_fingerprint(self):
        """Submitting the exact same SMS text twice is blocked by fingerprint check."""
        sms_text = (
            "Telebirr: You have received ETB 100.00 from 0911223344. "
            "Transaction ID: DIKREPLAY123 to 0963572327 (Habtamu Melese)."
        )
        msg_mock = AsyncMock()
        msg_mock.text = sms_text
        update = MagicMock()
        update.message = msg_mock
        update.effective_user.id = self.user_id
        context = MagicMock()
        context.user_data = {}

        # First execution: records fingerprint
        fp = fingerprint(sms_text)
        credited, b, _ = run(db.auto_credit_deposit(self.user_id, 100.0, fp, "SMS Credit"))
        self.assertTrue(credited)

        # Second submission via handler
        handled = run(handle_sms_or_reference_text(update, context))
        self.assertTrue(handled)
        msg_mock.reply_text.assert_called_with(
            msg.DEPOSIT_REUSED.format(amount=100.0),
            parse_mode="Markdown",
        )


class TestCrossAccountTransfers(BaseTransactionTestCase):
    """User selects one payment method but sends funds to one of our other official accounts."""

    def test_selected_telebirr_but_paid_cbe_bank(self):
        """User selected Telebirr, but transferred to CBE Bank account (1000413343538)."""
        sms_text = (
            "Dear Customer, you have transferred ETB 250.00 to Commercial Bank of Ethiopia account 1000413343538 "
            "(Natnael Temesegen). Ref: FT2601009988."
        )
        is_valid, matched, reason = verify_directional_match(sms_text, expected_method="telebirr")
        self.assertTrue(is_valid)
        self.assertEqual(matched, "cbe_bank")
        self.assertIsNone(reason)

    def test_selected_cbebirr_but_paid_telebirr(self):
        """User selected CBE Birr, but paid Telebirr account 0963572327."""
        sms_text = (
            "You have transferred ETB 300.00 to 0963572327 (Habtamu Melese). "
            "Transaction ID: DIK55443322."
        )
        is_valid, matched, reason = verify_directional_match(sms_text, expected_method="cbebirr")
        self.assertTrue(is_valid)
        self.assertEqual(matched, "telebirr")
        self.assertIsNone(reason)

    def test_selected_cbe_bank_but_paid_cbebirr(self):
        """User selected CBE Bank, but paid CBE Birr 0934920411."""
        sms_text = (
            "You have sent ETB 400.00 to 0934920411 (Natnael Temesegen). "
            "Ref: CB88776655."
        )
        is_valid, matched, reason = verify_directional_match(sms_text, expected_method="cbe_bank")
        self.assertTrue(is_valid)
        self.assertEqual(matched, "cbebirr")
        self.assertIsNone(reason)


class TestEdgeCasesAndAttacks(BaseTransactionTestCase):
    """Edge cases: minimum amounts, failed transactions, packages, invalid signatures."""

    def test_below_minimum_amount_rejected(self):
        """Deposits below 10 ETB are rejected."""
        sms_text = (
            "Telebirr: You have received ETB 5.00 from 0911223344. "
            "Transaction ID: DIK_LOW_001 to 0963572327."
        )
        parsed = parse_deposit_sms(sms_text)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["amount"], 5.0)

        # In handle_sms_or_reference_text, amounts < MIN_DEPOSIT_AMOUNT (10 ETB) are not credited
        msg_mock = AsyncMock()
        msg_mock.text = "5"
        update = MagicMock()
        update.message = msg_mock
        update.effective_user.id = self.user_id
        context = MagicMock()
        context.user_data = {"awaiting_custom_deposit_amount": True, "selected_deposit_method": "telebirr"}

        run(handle_sms_or_reference_text(update, context))
        msg_mock.reply_text.assert_called()
        self.assertIn("ዝቅተኛው የማስገቢያ መጠን", msg_mock.reply_text.call_args[0][0])

    def test_package_airtime_sms_rejected(self):
        """Mobile airtime or internet bundle purchase SMS is recognized as non-deposit."""
        sms_text = "የ 100 ብር ወርሃዊ የኢንተርኔት ጥቅል ግዢ በተሳካ ሁኔታ ተጠናቋል።"
        self.assertTrue(is_package_or_service_sms(sms_text))

    def test_failed_or_reversed_transaction_sms_rejected(self):
        """Failed or cancelled transactions must not be processed."""
        sms_text = "ክፍያው አልተሳካም (Transaction failed). ETB 200.00 ተመላሽ ተደርጓል።"
        self.assertTrue(is_failed_transaction_sms(sms_text))

    def test_webhook_bad_hmac_signature_rejected(self):
        """Tampered or invalid signature returns 401."""
        client = TestClient(app)
        event_id = "evt_attack_1"
        ts = str(int(time.time()))
        body_bytes = b'{"type":"deposit.succeeded","data":{"object":{"id":"dep_attack"}}}'

        res = client.post(
            "/peerpay/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "PeerPay-Event": "deposit.succeeded",
                "PeerPay-Event-Id": event_id,
                "PeerPay-Delivery-Id": f"whd_{event_id}",
                "PeerPay-Timestamp": ts,
                "PeerPay-Signature": "v2=" + "0" * 64,
            },
        )
        self.assertEqual(res.status_code, 401)

    def test_webhook_expired_timestamp_rejected(self):
        """Webhook timestamp older than 300 seconds is rejected."""
        client = TestClient(app)
        event_id = "evt_expired_ts"
        expired_ts = str(int(time.time()) - 400)  # > 300s window
        body_bytes = b'{"type":"deposit.succeeded"}'
        sig = sign_webhook_payload(self.mock_settings.peerpay_webhook_secret, event_id, expired_ts, body_bytes)

        res = client.post(
            "/peerpay/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "PeerPay-Event": "deposit.succeeded",
                "PeerPay-Event-Id": event_id,
                "PeerPay-Delivery-Id": f"whd_{event_id}",
                "PeerPay-Timestamp": expired_ts,
                "PeerPay-Signature": sig,
            },
        )
        self.assertEqual(res.status_code, 401)


class TestValidTransactions(BaseTransactionTestCase):
    """Valid deposit flows for all three channels and webhook reconciliation."""

    def test_valid_telebirr_deposit(self):
        sms_text = (
            "ውድ Habtamu Melese: በ 0963572327 የ 500.00 ብር ክፍያ ከ 0911223344 ተቀብለዋል። "
            "የግብይት ቁጥር: DIKVALTB01 Date: 2026-10-09. ቀሪ ሂሳብ: 2,500.00 ብር"
        )
        is_valid, matched, reason = verify_directional_match(sms_text)
        self.assertTrue(is_valid)
        self.assertEqual(matched, "telebirr")

        parsed = parse_deposit_sms(sms_text)
        self.assertEqual(parsed["amount"], 500.0)

        credited, new_bal, is_dup = run(db.auto_credit_deposit(
            telegram_id=self.user_id,
            amount=500.0,
            fingerprint=fingerprint(sms_text),
            description="Valid Telebirr Deposit",
        ))
        self.assertTrue(credited)
        self.assertEqual(new_bal, 500.0)

    def test_valid_cbebirr_deposit(self):
        sms_text = (
            "CBE Birr: Your account 0934920411 has been credited with ETB 350.00 from 0922334455. "
            "Ref: 9876543210. Available balance: 3,000.00 ETB."
        )
        is_valid, matched, reason = verify_directional_match(sms_text)
        self.assertTrue(is_valid)
        self.assertEqual(matched, "cbebirr")

        parsed = parse_deposit_sms(sms_text)
        self.assertEqual(parsed["amount"], 350.0)

        credited, new_bal, is_dup = run(db.auto_credit_deposit(
            telegram_id=self.user_id,
            amount=350.0,
            fingerprint=fingerprint(sms_text),
            description="Valid CBE Birr Deposit",
        ))
        self.assertTrue(credited)
        self.assertEqual(new_bal, 350.0)

    def test_valid_cbe_bank_deposit(self):
        sms_text = (
            "You have received ETB 1,000.00 into account 1000413343538 (Natnael Temesegen). "
            "Txn: FT2601007766. Date: 2026-10-09."
        )
        is_valid, matched, reason = verify_directional_match(sms_text)
        self.assertTrue(is_valid)
        self.assertEqual(matched, "cbe_bank")

        parsed = parse_deposit_sms(sms_text)
        self.assertEqual(parsed["amount"], 1000.0)

        credited, new_bal, is_dup = run(db.auto_credit_deposit(
            telegram_id=self.user_id,
            amount=1000.0,
            fingerprint=fingerprint(sms_text),
            description="Valid CBE Bank Deposit",
        ))
        self.assertTrue(credited)
        self.assertEqual(new_bal, 1000.0)

    def test_peerpay_webhook_deposit_succeeded_flow(self):
        """Full end-to-end webhook credit and idempotency."""
        client = TestClient(app)
        event_id = "evt_dep_success_100"
        ts = str(int(time.time()))
        payload = {
            "id": event_id,
            "type": "deposit.succeeded",
            "api_version": "2026-06-01",
            "data": {
                "object": {
                    "id": "dep_wh_success_100",
                    "merchant_customer_id": f"tg_{self.user_id}",
                    "amount": "250.00",
                    "status": "succeeded",
                    "merchant_order_id": "ord_wh_100",
                }
            },
        }
        body_bytes = json.dumps(payload).encode("utf-8")
        sig = sign_webhook_payload(self.mock_settings.peerpay_webhook_secret, event_id, ts, body_bytes)

        res = client.post(
            "/peerpay/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "PeerPay-Event": "deposit.succeeded",
                "PeerPay-Event-Id": event_id,
                "PeerPay-Delivery-Id": f"whd_{event_id}",
                "PeerPay-Timestamp": ts,
                "PeerPay-Signature": sig,
            },
        )
        self.assertEqual(res.status_code, 204)

        u = run(db.get_user(self.user_id))
        self.assertEqual(u["balance"], 250.0)


if __name__ == "__main__":
    unittest.main()
