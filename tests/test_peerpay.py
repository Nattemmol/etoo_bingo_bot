"""PeerPay webhook integration tests.

Covers signature verification, idempotent wallet transitions, and the
/peerpay/webhook FastAPI endpoint. Follows the convention of the older
test_verify_et.py (SimpleNamespace settings patches + TestClient).
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
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

from bot import database as db
from bot import peerpay
from server.main import app


def run(coro):
    return asyncio.run(coro)


def sign_payload(secret, event_id, timestamp, raw_body):
    message = f"{event_id}.{timestamp}.".encode("utf-8") + raw_body
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"v2={digest}"


def deposit_object(event_id, payment_id, amount="120.00", customer="tg_777111", merchant_order_id="pp_ord_1", status="succeeded"):
    now = "2026-06-19T15:10:15Z"
    return {
        "id": event_id,
        "type": "deposit.succeeded",
        "api_version": "2026-06-01",
        "sequence": 7,
        "created_at": now,
        "data": {
            "object": {
                "id": payment_id,
                "merchant_order_id": merchant_order_id,
                "merchant_customer_id": customer,
                "environment": "live",
                "status": status,
                "amount": amount,
                "currency": "ETB",
                "payment_method": "telebirr",
                "transaction": {"reference": "CHK7M2P9QX", "verified_at": now},
                "verification": {
                    "provider": "bank",
                    "status": "succeeded",
                    "verified": True,
                    "amount_match": True,
                },
                "created_at": now,
                "expires_at": "2026-06-19T16:05:00Z",
            }
        },
    }


def withdrawal_object(event_id, payment_id, amount="200.00", customer="tg_777111", status="succeeded"):
    now = "2026-06-19T15:10:15Z"
    return {
        "id": event_id,
        "type": "withdrawal.succeeded",
        "api_version": "2026-06-01",
        "sequence": 7,
        "created_at": now,
        "data": {
            "object": {
                "id": payment_id,
                "merchant_withdrawal_id": f"pp_wd_{payment_id}",
                "merchant_customer_id": customer,
                "status": status,
                "amount": amount,
                "currency": "ETB",
                "verification": {"provider": "bank", "status": "succeeded", "verified": True},
                "created_at": now,
                "expires_at": "2026-06-19T16:05:00Z",
            }
        },
    }


class TestPeerPaySignature(unittest.TestCase):
    def test_valid_signature(self):
        secret = "pp_test_secret"
        event_id = "evt_4d8e2a71"
        ts = str(int(time.time()))
        raw = b'{"type":"deposit.succeeded"}'
        header = sign_payload(secret, event_id, ts, raw)
        self.assertTrue(
            peerpay.verify_peerpay_signature(secret, event_id, ts, raw, header)
        )

    def test_invalid_signature_rejected(self):
        self.assertFalse(
            peerpay.verify_peerpay_signature(
                "secret", "evt_1", str(int(time.time())), b"{}", "v2=deadbeef" * 8
            )
        )

    def test_non_v2_prefix_rejected(self):
        secret = "s"
        event_id = "evt_1"
        ts = str(int(time.time()))
        raw = b"{}"
        header = sign_payload(secret, event_id, ts, raw).replace("v2=", "v3=")
        self.assertFalse(peerpay.verify_peerpay_signature(secret, event_id, ts, raw, header))

    def test_wrong_secret_rejected(self):
        event_id = "evt_1"
        ts = str(int(time.time()))
        raw = b"{}"
        header = sign_payload("right_secret", event_id, ts, raw)
        self.assertFalse(peerpay.verify_peerpay_signature("wrong_secret", event_id, ts, raw, header))

    def test_expired_timestamp_rejected(self):
        secret = "s"
        event_id = "evt_1"
        ts = str(int(time.time()) - 301)
        raw = b"{}"
        header = sign_payload(secret, event_id, ts, raw)
        self.assertFalse(peerpay.verify_peerpay_signature(secret, event_id, ts, raw, header))

    def test_bad_timestamp_rejected(self):
        self.assertFalse(
            peerpay.verify_peerpay_signature("s", "evt_1", "not-a-number", b"{}", "v2=" + "a" * 64)
        )

    def test_missing_signature_rejected(self):
        self.assertFalse(
            peerpay.verify_peerpay_signature("s", "evt_1", str(int(time.time())), b"{}", "")
        )

    def test_dev_mode_accepts_without_secret(self):
        self.assertTrue(peerpay.verify_peerpay_signature("", "evt_1", "", b"{}", ""))


class TestCustomerMapping(unittest.TestCase):
    def test_tg_prefix(self):
        self.assertEqual(peerpay.customer_id_to_telegram_id("tg_777111"), 777111)
        self.assertEqual(peerpay.customer_id_to_telegram_id("tg777111"), 777111)
        self.assertEqual(peerpay.customer_id_to_telegram_id("user_42"), 42)
        self.assertEqual(peerpay.customer_id_to_telegram_id("123"), 123)

    def test_unmapable(self):
        self.assertIsNone(peerpay.customer_id_to_telegram_id(None))
        self.assertIsNone(peerpay.customer_id_to_telegram_id(""))
        self.assertIsNone(peerpay.customer_id_to_telegram_id("cust_abebe_kebede"))


class TestPeerPayDatabase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = db.settings
        db.settings = SimpleNamespace(database_path=Path(self._tmp) / "test_peerpay.db")
        run(db.init_db())
        run(db.create_user(777001, "251911000001", "player1", "Player One"))

    def tearDown(self):
        db.settings = self._orig

    def test_webhook_event_dedupe(self):
        self.assertTrue(
            run(db.record_webhook_event_once("evt_1", "whd_1", "deposit.succeeded", "dep_1", "{}"))
        )
        self.assertFalse(
            run(db.record_webhook_event_once("evt_1", "whd_1", "deposit.succeeded", "dep_1", "{}"))
        )
        self.assertTrue(run(db.record_webhook_event_once("evt_2", "whd_1", "deposit.succeeded", "dep_1", "{}")))

    def test_deposit_credit_idempotent(self):
        credited, bal, is_dup = run(
            db.credit_peerpay_deposit_once("dep_pay_1", 777001, 150.0)
        )
        self.assertTrue(credited)
        self.assertAlmostEqual(bal, 150.0)
        self.assertFalse(is_dup)

        credited2, bal2, is_dup2 = run(
            db.credit_peerpay_deposit_once("dep_pay_1", 777001, 150.0)
        )
        self.assertFalse(credited2)
        self.assertTrue(is_dup2)
        self.assertAlmostEqual(bal2, 150.0)

        credited3, bal3, _ = run(
            db.credit_peerpay_deposit_once("dep_pay_2", 777001, 50.0)
        )
        self.assertTrue(credited3)
        self.assertAlmostEqual(bal3, 200.0)

    def test_deposit_credit_out_of_order(self):
        credited, bal, _ = run(db.credit_peerpay_deposit_once("dep_late", 777001, 300.0))
        self.assertTrue(credited)
        self.assertAlmostEqual(bal, 300.0)

    def test_deposit_created_records_row_without_credit(self):
        run(
            db.upsert_peerpay_deposit(
                "dep_pay_created", 777001, 75.0, "ETB", "pp_ord_9", "created"
            )
        )
        rec = run(db.get_peerpay_deposit("dep_pay_created"))
        self.assertIsNotNone(rec)
        self.assertEqual(rec["status"], "created")
        self.assertEqual(rec["credited"], 0)
        self.assertAlmostEqual(run(db.get_balance(777001)), 0.0)

    def test_withdrawal_capture_exactly_once(self):
        run(db.create_peerpay_withdrawal_hold("wd_1", 777001, 200.0))
        captured, _ = run(db.capture_peerpay_withdrawal_once("wd_1"))
        self.assertTrue(captured)
        captured2, _ = run(db.capture_peerpay_withdrawal_once("wd_1"))
        self.assertFalse(captured2)
        self.assertAlmostEqual(run(db.get_balance(777001)), 0.0)

    def test_withdrawal_release_refunds_once(self):
        run(db.create_peerpay_withdrawal_hold("wd_2", 777001, 100.0))
        released, new_balance = run(db.release_peerpay_withdrawal_once("wd_2"))
        self.assertTrue(released)
        self.assertAlmostEqual(new_balance, 100.0)
        released2, _ = run(db.release_peerpay_withdrawal_once("wd_2"))
        self.assertFalse(released2)
        self.assertAlmostEqual(run(db.get_balance(777001)), 100.0)

    def test_capture_then_release_does_not_refund(self):
        run(db.create_peerpay_withdrawal_hold("wd_3", 777001, 100.0))
        run(db.capture_peerpay_withdrawal_once("wd_3"))
        released, _ = run(db.release_peerpay_withdrawal_once("wd_3"))
        self.assertFalse(released)
        self.assertAlmostEqual(run(db.get_balance(777001)), 0.0)


class TestPeerPayWebhookEndpoint(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        shared = SimpleNamespace(
            database_path=Path(self._tmp) / "test_webhook.db",
            peerpay_webhook_secret="pp_test_secret",
            bot_token="test_token",
            webapp_url="https://example.com",
            server_port=8765,
            api_version="2026-06-01",
        )
        self._pdb = patch("bot.database.settings", shared)
        self._psrv = patch("server.main.settings", shared)
        self._pdb.start()
        self._psrv.start()
        self._pnotify = patch("server.main._notify_telegram", new_callable=AsyncMock)
        self._notify_mock = self._pnotify.start()

        run(db.init_db())
        run(db.create_user(777111, "251911000002", "webhook_user", "Webhook User"))
        self.client = TestClient(app)

    def tearDown(self):
        self._pnotify.stop()
        self._psrv.stop()
        self._pdb.stop()

    def _post(self, payload, event_type=None, override_signature=None):
        raw = json.dumps(payload).encode("utf-8")
        event_id = payload.get("id", "evt_default")
        ts = str(int(time.time()))
        headers = {
            "Content-Type": "application/json",
            "PeerPay-Event": event_type or payload.get("type", ""),
            "PeerPay-Event-Id": event_id,
            "PeerPay-Delivery-Id": f"whd_{event_id}",
            "PeerPay-Timestamp": ts,
            "PeerPay-Signature": override_signature
            or sign_payload("pp_test_secret", event_id, ts, raw),
        }
        return self.client.post("/peerpay/webhook", content=raw, headers=headers)

    def test_deposit_success_credits_wallet(self):
        resp = self._post(deposit_object("evt_dep_1", "dep_pay_1"))
        self.assertEqual(resp.status_code, 204)
        self.assertAlmostEqual(run(db.get_balance(777111)), 120.0)
        self._notify_mock.assert_awaited_once()

    def test_duplicate_event_not_double_credited(self):
        payload = deposit_object("evt_dep_dup", "dep_pay_dup", amount="120.00")
        self.assertEqual(self._post(payload).status_code, 204)
        self.assertEqual(self._post(payload).status_code, 204)
        self.assertAlmostEqual(run(db.get_balance(777111)), 120.0)
        self._notify_mock.assert_awaited_once()

    def test_bad_signature_rejected(self):
        payload = deposit_object("evt_dep_bad", "dep_pay_bad")
        resp = self._post(payload, override_signature="v2=" + "0" * 64)
        self.assertEqual(resp.status_code, 401)
        self.assertAlmostEqual(run(db.get_balance(777111)), 0.0)
        self._notify_mock.assert_not_awaited()

    def test_webhook_test_is_noop(self):
        payload = {
            "id": "evt_test_1",
            "type": "webhook.test",
            "api_version": "2026-06-01",
            "data": {"object": {}},
        }
        self.assertEqual(self._post(payload, event_type="webhook.test").status_code, 204)
        self.assertIsNotNone(run(db.get_webhook_event("evt_test_1")))
        self.assertAlmostEqual(run(db.get_balance(777111)), 0.0)

    def test_deposit_failed_never_credits(self):
        payload = deposit_object("evt_dep_fail", "dep_pay_fail", status="failed")
        payload["type"] = "deposit.failed"
        resp = self._post(payload, event_type="deposit.failed")
        self.assertEqual(resp.status_code, 204)
        self.assertAlmostEqual(run(db.get_balance(777111)), 0.0)
        rec = run(db.get_peerpay_deposit("dep_pay_fail"))
        self.assertEqual(rec["status"], "failed")
        self.assertEqual(rec["credited"], 0)

    def test_deposit_created_records_row_only(self):
        payload = deposit_object("evt_dep_created", "dep_pay_created", status="created")
        payload["type"] = "deposit.created"
        self.assertEqual(self._post(payload, event_type="deposit.created").status_code, 204)
        self.assertAlmostEqual(run(db.get_balance(777111)), 0.0)
        rec = run(db.get_peerpay_deposit("dep_pay_created"))
        self.assertEqual(rec["status"], "created")

    def test_withdrawal_success_captures_once(self):
        run(db.create_peerpay_withdrawal_hold("wd_pay_1", 777111, 200.0))
        payload = withdrawal_object("evt_wd_1", "wd_pay_1", amount="200.00")
        self.assertEqual(self._post(payload, event_type="withdrawal.succeeded").status_code, 204)
        rec = run(db.get_peerpay_withdrawal("wd_pay_1"))
        self.assertEqual(rec["captured"], 1)
        self.assertEqual(rec["released"], 0)
        self.assertAlmostEqual(run(db.get_balance(777111)), 0.0)
        self._notify_mock.assert_awaited_once()

    def test_withdrawal_cancelled_releases_refund(self):
        run(db.create_peerpay_withdrawal_hold("wd_pay_2", 777111, 200.0))
        payload = withdrawal_object("evt_wd_2", "wd_pay_2", amount="200.00", status="cancelled")
        payload["type"] = "withdrawal.cancelled"
        self.assertEqual(
            self._post(payload, event_type="withdrawal.cancelled").status_code, 204
        )
        self.assertAlmostEqual(run(db.get_balance(777111)), 200.0)
        rec = run(db.get_peerpay_withdrawal("wd_pay_2"))
        self.assertEqual(rec["captured"], 0)
        self.assertEqual(rec["released"], 1)

    def test_withdrawal_verify_failed_keeps_hold(self):
        run(db.create_peerpay_withdrawal_hold("wd_pay_3", 777111, 200.0))
        payload = withdrawal_object("evt_wd_3", "wd_pay_3", amount="200.00", status="transfer_submitted")
        payload["type"] = "withdrawal.verification_failed"
        payload["data"]["object"]["verification"] = {
            "status": "amount_mismatch",
            "verified": False,
            "decision_code": "amount_mismatch",
        }
        self.assertEqual(
            self._post(payload, event_type="withdrawal.verification_failed").status_code, 204
        )
        self.assertAlmostEqual(run(db.get_balance(777111)), 0.0)
        rec = run(db.get_peerpay_withdrawal("wd_pay_3"))
        self.assertEqual(rec["captured"], 0)
        self.assertEqual(rec["released"], 0)
        self.assertEqual(rec["decision_code"], "amount_mismatch")


if __name__ == "__main__":
    unittest.main()