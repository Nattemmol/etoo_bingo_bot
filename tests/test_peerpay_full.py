"""Comprehensive tests for PeerPayment.org integration across Bot, Server, and Mini App."""

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
from urllib.parse import urlencode

from starlette.testclient import TestClient

from bot import database as db
from bot import peerpay
from bot.handlers.deposit import handle_sms_or_reference_text
from bot.handlers.withdraw import withdraw_amount_handler
from bot.peerpay import PeerPayClient
from server.main import app


def run(coro):
    return asyncio.run(coro)


def sign_payload(secret, event_id, timestamp, raw_body):
    message = f"{event_id}.{timestamp}.".encode("utf-8") + raw_body
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"v2={digest}"


def generate_valid_init_data(bot_token: str, user_id: int = 777111, first_name: str = "TestUser") -> str:
    user_dict = {"id": user_id, "first_name": first_name, "username": "testuser"}
    user_json = json.dumps(user_dict, separators=(",", ":"))
    params = {
        "auth_date": str(int(time.time())),
        "query_id": "AAHdF6IQAAAAAN0XohD12345",
        "user": user_json,
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    params["hash"] = calc_hash
    return urlencode(params)


class TestPeerPayFullIntegration(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.bot_token = "test_bot_token_123"
        self.shared = SimpleNamespace(
            database_path=Path(self._tmp) / "test_peerpay_full.db",
            peerpay_api_key="pp_test_api_key",
            peerpay_base_url="https://api.peerpayment.org",
            peerpay_webhook_secret="pp_test_secret",
            bot_token=self.bot_token,
            webapp_url="https://example.com",
            server_port=8765,
            super_bingo_always_open=False,
            telebirr_base_url="https://example.com",
            telebirr_fabric_app_id="",
            telebirr_app_secret="",
            telebirr_merchant_app_id="",
            telebirr_merchant_code="",
            telebirr_web_base_url="https://example.com",
            telebirr_private_key="",
            telebirr_public_key="",
        )
        self._pdb = patch("bot.database.settings", self.shared)
        self._psrv = patch("server.main.settings", self.shared)
        self._pcfg = patch("bot.config.settings", self.shared)
        self._pauth = patch("server.auth.settings", self.shared)
        self._pdb.start()
        self._psrv.start()
        self._pcfg.start()
        self._pauth.start()

        self._pnotify = patch("server.main._notify_telegram", new_callable=AsyncMock)
        self._notify_mock = self._pnotify.start()

        run(db.init_db())
        run(db.create_user(777111, "251911000002", "player_full", "Player Full"))
        self.client = TestClient(app)

    def tearDown(self):
        self._pnotify.stop()
        self._pauth.stop()
        self._pcfg.stop()
        self._psrv.stop()
        self._pdb.stop()

    def test_peerpay_client_create_deposit(self):
        client = PeerPayClient(api_key="test_key", base_url="https://api.peerpayment.org")
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=201,
                json=lambda: {
                    "data": {
                        "id": "dep_123",
                        "checkout_url": "https://checkout.peerpayment.org/c/ptk_test",
                        "status": "awaiting_transfer",
                    }
                },
            )
            res = run(client.create_deposit(merchant_customer_id="tg_777111", amount=150.0))
            self.assertIn("data", res)
            self.assertEqual(res["data"]["id"], "dep_123")
            mock_post.assert_awaited_once()

    def test_peerpay_client_submit_reference(self):
        client = PeerPayClient()
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"status": "verification_pending"},
            )
            res = run(
                client.submit_deposit_reference(
                    checkout_token_or_url="https://checkout.peerpayment.org/c/ptk_test",
                    reference="CHK7M2P9QX",
                )
            )
            self.assertEqual(res.get("status"), "verification_pending")

    def test_peerpay_client_create_withdrawal(self):
        client = PeerPayClient(api_key="test_key")
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=201,
                json=lambda: {
                    "data": {
                        "id": "wd_123",
                        "checkout_url": "https://checkout.peerpayment.org/w/ptk_wtest",
                        "status": "created",
                    }
                },
            )
            res = run(
                client.create_withdrawal(
                    merchant_customer_id="tg_777111",
                    amount=200.0,
                    destination={"bank": "telebirr", "account_number": "0911223344"},
                )
            )
            self.assertEqual(res["data"]["id"], "wd_123")

    def test_handle_sms_pasted_by_user(self):
        sms_text = "You have received ETB 150.00 from Abebe Kebede. Transaction ID: CHK7M2P9QX. Thank you for using Telebirr."
        update = MagicMock()
        update.message.text = sms_text
        update.effective_user.id = 777111
        update.message.reply_text = AsyncMock()

        handled = run(handle_sms_or_reference_text(update, None))
        self.assertTrue(handled)
        update.message.reply_text.assert_awaited()

        dep = run(db.get_peerpay_deposit("CHK7M2P9QX"))
        self.assertIsNotNone(dep)
        self.assertEqual(dep["status"], "succeeded")
        self.assertEqual(dep["credited"], 1)
        self.assertAlmostEqual(run(db.get_balance(777111)), 150.0)

    def test_handle_duplicate_sms_rejected(self):
        sms_text = "You have received ETB 150.00. Transaction ID: DUP1234567. Telebirr."
        update = MagicMock()
        update.message.text = sms_text
        update.effective_user.id = 777111
        update.message.reply_text = AsyncMock()

        # Seed completed deposit with same fingerprint
        from bot.sms_parser import fingerprint
        fp = fingerprint(sms_text)
        run(db.auto_credit_deposit(777111, 150.0, fp, "seeded"))

        handled = run(handle_sms_or_reference_text(update, None))
        self.assertTrue(handled)
        # Should reply that receipt was already used
        call_args = update.message.reply_text.call_args[0][0]
        self.assertTrue("ቀድሞውኑ" in call_args or "already" in call_args.lower() or "etb" in call_args.lower())

    def test_withdraw_amount_handler_hold_and_link(self):
        from bot.handlers.withdraw import withdraw_amount_handler, withdraw_account_handler, AWAITING_ACCOUNT
        run(db.update_balance(777111, 500.0))

        context = MagicMock()
        context.user_data = {"withdraw_method": "telebirr"}

        update_amount = MagicMock()
        update_amount.message.text = "200"
        update_amount.effective_user.id = 777111
        update_amount.message.reply_text = AsyncMock()

        # Step 1: amount handler -> returns AWAITING_ACCOUNT
        state = run(withdraw_amount_handler(update_amount, context))
        self.assertEqual(state, AWAITING_ACCOUNT)
        self.assertEqual(context.user_data["withdraw_amount"], 200.0)

        # Step 2: account handler -> creates withdrawal & ends conversation
        update_acc = MagicMock()
        update_acc.message.text = "0911223344"
        update_acc.effective_user.id = 777111
        update_acc.message.reply_text = AsyncMock()

        with patch("bot.peerpay.PeerPayClient.create_withdrawal", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = {
                "data": {
                    "id": "wd_test_99",
                    "checkout_url": "https://checkout.peerpayment.org/w/ptk_wd_99",
                }
            }
            res = run(withdraw_account_handler(update_acc, context))
            self.assertEqual(res, -1)  # ConversationHandler.END is -1

            # Balance should be deducted to 300.0
            new_bal = run(db.get_balance(777111))
            self.assertAlmostEqual(new_bal, 300.0)

            # Withdrawal record created in DB
            wd = run(db.get_peerpay_withdrawal("wd_test_99"))
            self.assertIsNotNone(wd)
            self.assertEqual(wd["captured"], 0)
            self.assertEqual(wd["released"], 0)


    def test_mini_app_api_user_me(self):
        init_data = generate_valid_init_data(self.bot_token, user_id=777111)
        run(db.update_balance(777111, 250.0))

        resp = self.client.get("/api/user/me", headers={"X-Telegram-Init-Data": init_data})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertAlmostEqual(data["balance"], 250.0)

    def test_mini_app_api_deposit_create(self):
        init_data = generate_valid_init_data(self.bot_token, user_id=777111)
        with patch("bot.peerpay.PeerPayClient.create_deposit", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = {
                "data": {
                    "id": "dep_api_1",
                    "checkout_url": "https://checkout.peerpayment.org/c/ptk_api_1",
                }
            }
            resp = self.client.post(
                "/api/deposit/create",
                json={"init_data": init_data, "amount": 100.0, "payment_method": "telebirr"},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data["ok"])
            self.assertEqual(data["data"]["id"], "dep_api_1")

    def test_mini_app_api_withdraw_create(self):
        init_data = generate_valid_init_data(self.bot_token, user_id=777111)
        run(db.update_balance(777111, 400.0))

        with patch("bot.peerpay.PeerPayClient.create_withdrawal", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = {
                "data": {
                    "id": "wd_api_1",
                    "checkout_url": "https://checkout.peerpayment.org/w/ptk_wapi_1",
                }
            }
            resp = self.client.post(
                "/api/withdraw/create",
                json={
                    "init_data": init_data,
                    "amount": 150.0,
                    "destination": {"bank": "telebirr", "account_number": "0911223344"},
                },
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data["ok"])
            self.assertAlmostEqual(data["new_balance"], 250.0)

            # DB balance checked
            self.assertAlmostEqual(run(db.get_balance(777111)), 250.0)


    def test_handle_receipt_url_auto_credited(self):
        update = MagicMock()
        update.message.text = "https://transactioninfo.ethiotelecom.et/receipt/DIH9URLTEST1"
        update.effective_user.id = 777111
        update.message.reply_text = AsyncMock()

        with patch("bot.handlers.deposit.fetch_telebirr_receipt", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = {
                "amount": 75.0,
                "reference": "DIH9URLTEST1",
                "status": "completed",
            }
            initial_bal = run(db.get_balance(777111))
            handled = run(handle_sms_or_reference_text(update, None))
            self.assertTrue(handled)
            update.message.reply_text.assert_awaited()

            # Verify balance credited by 75 ETB
            new_bal = run(db.get_balance(777111))
            self.assertAlmostEqual(new_bal, initial_bal + 75.0)

            # DB deposit record
            dep = run(db.get_peerpay_deposit("DIH9URLTEST1"))
            self.assertIsNotNone(dep)
            self.assertEqual(dep["status"], "succeeded")
            self.assertEqual(dep["credited"], 1)

    def test_handle_transaction_id_with_amount_auto_credited(self):
        update = MagicMock()
        update.message.text = "DIH9INLINETEST2 120"
        update.effective_user.id = 777111
        update.message.reply_text = AsyncMock()

        initial_bal = run(db.get_balance(777111))
        handled = run(handle_sms_or_reference_text(update, None))
        self.assertTrue(handled)
        update.message.reply_text.assert_awaited()

        # Verify balance credited by 120 ETB
        new_bal = run(db.get_balance(777111))
        self.assertAlmostEqual(new_bal, initial_bal + 120.0)

    def test_mini_app_api_deposit_submit_reference_with_amount(self):
        init_data = generate_valid_init_data(self.bot_token, user_id=777111)
        initial_bal = run(db.get_balance(777111))

        resp = self.client.post(
            "/api/deposit/submit-reference",
            json={
                "init_data": init_data,
                "reference": "DIH9APITEST3",
                "amount": 80.0,
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["data"]["status"], "succeeded")
        self.assertAlmostEqual(data["data"]["amount"], 80.0)

        # Balance in DB
        new_bal = run(db.get_balance(777111))
        self.assertAlmostEqual(new_bal, initial_bal + 80.0)


if __name__ == "__main__":
    unittest.main()
