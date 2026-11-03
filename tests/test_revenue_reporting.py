"""Unit tests for automated and on-demand daily/weekly revenue and transfers reporting."""

import asyncio
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import AsyncMock, patch

from bot.reports import (
    EAT,
    DEFAULT_OWNER_ID,
    add_report_recipient,
    format_game_revenue_report,
    format_transfers_report,
    format_revenue_report,
    get_daily_window,
    get_report_recipients,
    get_weekly_window,
    _is_authorized_reporter,
)


class TestRevenueReportingWindows(unittest.TestCase):
    def test_daily_window_scheduled(self):
        # Scheduled run at 21:00 EAT on 2026-10-27
        dt = datetime(2026, 10, 27, 21, 0, 0, tzinfo=EAT)
        start, end, label = get_daily_window(dt, is_live=False)
        self.assertEqual(end, datetime(2026, 10, 27, 21, 0, 0, tzinfo=EAT))
        self.assertEqual(start, datetime(2026, 10, 26, 21, 0, 0, tzinfo=EAT))
        self.assertIn("24 ሰዓት", label)

    def test_daily_window_live_after_21(self):
        # Live run at 22:30 EAT on 2026-10-27 (started at 21:00 today)
        dt = datetime(2026, 10, 27, 22, 30, 0, tzinfo=EAT)
        start, end, label = get_daily_window(dt, is_live=True)
        self.assertEqual(start, datetime(2026, 10, 27, 21, 0, 0, tzinfo=EAT))
        self.assertEqual(end, dt)
        self.assertIn("Live", label)

    def test_daily_window_live_before_21(self):
        # Live run at 15:00 EAT on 2026-10-27 (started at 21:00 yesterday)
        dt = datetime(2026, 10, 27, 15, 0, 0, tzinfo=EAT)
        start, end, label = get_daily_window(dt, is_live=True)
        self.assertEqual(start, datetime(2026, 10, 26, 21, 0, 0, tzinfo=EAT))
        self.assertEqual(end, dt)
        self.assertIn("Live", label)

    def test_weekly_window_scheduled_sunday(self):
        # 2026-11-01 is a Sunday
        sunday_dt = datetime(2026, 11, 1, 21, 0, 0, tzinfo=EAT)
        self.assertEqual(sunday_dt.weekday(), 6)
        start, end, label = get_weekly_window(sunday_dt, is_live=False)
        self.assertEqual(end, sunday_dt)
        self.assertEqual(start, datetime(2026, 10, 25, 21, 0, 0, tzinfo=EAT))
        self.assertIn("7 ቀናት", label)

    def test_weekly_window_live_midweek(self):
        # 2026-10-28 is a Wednesday (weekday 2). Most recent Sunday was 2026-10-25 21:00.
        wed_dt = datetime(2026, 10, 28, 14, 0, 0, tzinfo=EAT)
        self.assertEqual(wed_dt.weekday(), 2)
        start, end, label = get_weekly_window(wed_dt, is_live=True)
        self.assertEqual(start, datetime(2026, 10, 25, 21, 0, 0, tzinfo=EAT))
        self.assertEqual(end, wed_dt)
        self.assertIn("Live", label)


class TestRevenueReportFormatting(unittest.TestCase):
    def test_format_game_revenue_report_daily(self):
        data = {
            "games_10": 15,
            "cards_10": 45,
            "cut_10": 90.0,
            "games_50": 1,
            "cards_50": 30,
            "cut_50": 300.0,
            "total_games": 16,
            "total_cards": 75,
            "grand_total": 390.0,
        }
        start = datetime(2026, 10, 26, 21, 0, tzinfo=EAT)
        end = datetime(2026, 10, 27, 21, 0, tzinfo=EAT)
        label = "ከትላንት ማታ 3:00 እስከ ዛሬ ማታ 3:00 (24 ሰዓት)"
        text = format_game_revenue_report(data, start, end, label, report_type="daily", is_live=False)

        self.assertIn("ዕለታዊ የጨዋታ ገቢ ሪፖርት", text)
        self.assertIn("የ 10 ብር ጨዋታ", text)
        self.assertIn("• የተካሄዱ ጨዋታዎች: *15*", text)
        self.assertIn("• የተሸጡ ካርቴላዎች: *45*", text)
        self.assertIn("90.00 ETB", text)
        self.assertIn("የ 50 ብር ሱፐር ቢንጎ", text)
        self.assertIn("300.00 ETB", text)
        self.assertIn("አጠቃላይ የተጣራ የቤት ገቢ (Grand Total):* *390.00 ETB*", text)

    def test_format_game_revenue_report_weekly(self):
        data = {
            "games_10": 100,
            "cards_10": 300,
            "cut_10": 600.0,
            "games_50": 7,
            "cards_50": 210,
            "cut_50": 2100.0,
            "total_games": 107,
            "total_cards": 510,
            "grand_total": 2700.0,
        }
        start = datetime(2026, 10, 18, 21, 0, tzinfo=EAT)
        end = datetime(2026, 10, 25, 21, 0, tzinfo=EAT)
        label = "ካለፈው እሁድ ማታ 3:00 እስከ ዛሬ እሁድ ማታ 3:00 (7 ቀናት)"
        text = format_game_revenue_report(data, start, end, label, report_type="weekly", is_live=False)

        self.assertIn("ሳምንታዊ የጨዋታ ገቢ ሪፖርት", text)
        self.assertIn("2,700.00 ETB", text)
        self.assertIn("600.00 ETB", text)
        self.assertIn("2,100.00 ETB", text)

    def test_format_transfers_report(self):
        data = {
            "deposits": {
                "total_amount": 10600.0,
                "total_count": 8,
                "by_account": [
                    {"label": "Telebirr (09***1111)", "amount": 5200.0, "count": 4},
                    {"label": "Telebirr (09***2222)", "amount": 3400.0, "count": 2},
                    {"label": "CBE Birr", "amount": 2000.0, "count": 2},
                ]
            },
            "withdrawals": {
                "total_amount": 4500.0,
                "total_count": 3,
                "by_account": [
                    {"label": "Telebirr", "amount": 3000.0, "count": 2},
                    {"label": "CBE Birr", "amount": 1500.0, "count": 1},
                ]
            }
        }
        start = datetime(2026, 10, 26, 21, 0, tzinfo=EAT)
        end = datetime(2026, 10, 27, 21, 0, tzinfo=EAT)
        label = "ከትላንት ማታ 3:00 እስከ ዛሬ ማታ 3:00 (24 ሰዓት)"
        text = format_transfers_report(data, start, end, label, report_type="daily", is_live=False)

        self.assertIn("ዕለታዊ የዝውውር ሪፖርት - ተቀማጭ እና ወጪ", text)
        self.assertIn("ጠቅላላ ገቢ በየአካውንቱ (Deposits by Account)", text)
        self.assertIn("Telebirr (09***1111): *5,200.00 ETB* (4 ተቀማጭ)", text)
        self.assertIn("Telebirr (09***2222): *3,400.00 ETB* (2 ተቀማጭ)", text)
        self.assertIn("CBE Birr: *2,000.00 ETB* (2 ተቀማጭ)", text)
        self.assertIn("አጠቃላይ የተቀማጭ ገንዘብ:* *10,600.00 ETB* (8 ተቀማጮች)", text)

        self.assertIn("ጠቅላላ ወጪ በየአካውንቱ (Withdrawals by Account)", text)
        self.assertIn("Telebirr: *3,000.00 ETB* (2 ወጪ)", text)
        self.assertIn("CBE Birr: *1,500.00 ETB* (1 ወጪ)", text)
        self.assertIn("አጠቃላይ የተከፈለ ገንዘብ:* *4,500.00 ETB* (3 ክፍያዎች)", text)
        self.assertIn("የዝውውር ልዩነት (Net Flow):* *+6,100.00 ETB*", text)

    def test_default_channels_always_present(self):
        """Verify that Telebirr, CBE Birr, and CBE Mobile Banking are always in the report, even with 0 activity."""
        data = {
            "deposits": {
                "total_amount": 0.0,
                "total_count": 0,
                "by_account": [
                    {"label": "Telebirr", "amount": 0.0, "count": 0},
                    {"label": "CBE Birr", "amount": 0.0, "count": 0},
                    {"label": "CBE Mobile Banking", "amount": 0.0, "count": 0},
                ]
            },
            "withdrawals": {
                "total_amount": 0.0,
                "total_count": 0,
                "by_account": [
                    {"label": "Telebirr", "amount": 0.0, "count": 0},
                    {"label": "CBE Birr", "amount": 0.0, "count": 0},
                    {"label": "CBE Mobile Banking", "amount": 0.0, "count": 0},
                ]
            }
        }
        start = datetime(2026, 10, 26, 21, 0, tzinfo=EAT)
        end = datetime(2026, 10, 27, 21, 0, tzinfo=EAT)
        label = "ከትላንት ማታ 3:00 እስከ ዛሬ ማታ 3:00 (24 ሰዓት)"
        text = format_transfers_report(data, start, end, label, report_type="daily", is_live=False)

        # Confirm all 3 accounts appear under both Deposits and Withdrawals
        self.assertIn("• Telebirr: *0.00 ETB* (0 ተቀማጭ)", text)
        self.assertIn("• CBE Birr: *0.00 ETB* (0 ተቀማጭ)", text)
        self.assertIn("• CBE Mobile Banking: *0.00 ETB* (0 ተቀማጭ)", text)
        self.assertIn("• Telebirr: *0.00 ETB* (0 ወጪ)", text)
        self.assertIn("• CBE Birr: *0.00 ETB* (0 ወጪ)", text)
        self.assertIn("• CBE Mobile Banking: *0.00 ETB* (0 ወጪ)", text)


class TestRevenueReportRecipients(unittest.IsolatedAsyncioTestCase):
    @patch("bot.reports.get_report_recipients", new_callable=AsyncMock)
    async def test_owner_authorized(self, mock_recipients):
        mock_recipients.return_value = {DEFAULT_OWNER_ID}
        class MockUser:
            id = DEFAULT_OWNER_ID
            username = "Ettmol"

        is_auth = await _is_authorized_reporter(MockUser())
        self.assertTrue(is_auth)

    @patch("bot.reports.add_report_recipient", new_callable=AsyncMock)
    @patch("bot.reports.get_report_recipients", new_callable=AsyncMock)
    async def test_username_case_insensitive_authorization(self, mock_recipients, mock_add):
        mock_recipients.return_value = {DEFAULT_OWNER_ID}
        mock_add.return_value = True
        class MockUser:
            id = 9999999999
            username = "ettmol"

        is_auth = await _is_authorized_reporter(MockUser())
        self.assertTrue(is_auth)

    @patch("bot.reports.get_report_recipients", new_callable=AsyncMock)
    async def test_unauthorized_user(self, mock_recipients):
        mock_recipients.return_value = {DEFAULT_OWNER_ID}
        class MockUser:
            id = 123456
            username = "random_user"

        is_auth = await _is_authorized_reporter(MockUser())
        self.assertFalse(is_auth)


class TestDatabaseRevenueQuery(unittest.TestCase):
    def test_sqlite_aggregation(self):
        import sqlite3
        # Use an in-memory database to test the exact SQL query logic
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("""
            CREATE TABLE house_revenue (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id        TEXT NOT NULL,
                cards_count    INTEGER NOT NULL,
                cut_per_card   REAL NOT NULL,
                total_revenue  REAL NOT NULL,
                created_at     TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)

        # Insert test records
        db.execute(
            "INSERT INTO house_revenue (room_id, cards_count, cut_per_card, total_revenue, created_at) VALUES (?, ?, ?, ?, ?)",
            ("room_play_10", 2, 2.0, 4.0, "2026-10-26 20:00:00"),
        )
        db.execute(
            "INSERT INTO house_revenue (room_id, cards_count, cut_per_card, total_revenue, created_at) VALUES (?, ?, ?, ?, ?)",
            ("room_play_10", 3, 2.0, 6.0, "2026-10-27 10:00:00"),
        )
        db.execute(
            "INSERT INTO house_revenue (room_id, cards_count, cut_per_card, total_revenue, created_at) VALUES (?, ?, ?, ?, ?)",
            ("room_super_50", 10, 10.0, 100.0, "2026-10-27 16:15:00"),
        )
        db.execute(
            "INSERT INTO house_revenue (room_id, cards_count, cut_per_card, total_revenue, created_at) VALUES (?, ?, ?, ?, ?)",
            ("room_play_10", 2, 2.0, 4.0, "2026-10-26 15:00:00"),
        )
        db.commit()

        start_str = "2026-10-26 18:00:00"
        end_str = "2026-10-27 18:00:00"

        query = """
        SELECT
            room_id,
            COUNT(*) as games_count,
            COALESCE(SUM(cards_count), 0) as total_cards,
            COALESCE(SUM(total_revenue), 0.0) as total_revenue
        FROM house_revenue
        WHERE created_at >= ? AND created_at < ?
        GROUP BY room_id
        """
        cursor = db.execute(query, (start_str, end_str))
        rows = cursor.fetchall()

        res = {"games_10": 0, "cards_10": 0, "cut_10": 0.0, "games_50": 0, "cards_50": 0, "cut_50": 0.0, "grand_total": 0.0}
        for r in rows:
            if r["room_id"] == "room_play_10":
                res["games_10"] = r["games_count"]
                res["cards_10"] = r["total_cards"]
                res["cut_10"] = r["total_revenue"]
            elif r["room_id"] == "room_super_50":
                res["games_50"] = r["games_count"]
                res["cards_50"] = r["total_cards"]
                res["cut_50"] = r["total_revenue"]
            res["grand_total"] += r["total_revenue"]

        db.close()

        self.assertEqual(res["games_10"], 2)
        self.assertEqual(res["cards_10"], 5)
        self.assertEqual(res["cut_10"], 10.0)
        self.assertEqual(res["games_50"], 1)
        self.assertEqual(res["cards_50"], 10)
        self.assertEqual(res["cut_50"], 100.0)
        self.assertEqual(res["grand_total"], 110.0)

    def test_sqlite_aggregation_with_multi_accounts(self):
        import sqlite3
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row

        db.execute("""
            CREATE TABLE peerpay_deposits (
                payment_id TEXT PRIMARY KEY,
                telegram_id INTEGER,
                amount REAL,
                credited INTEGER DEFAULT 0,
                status TEXT,
                updated_at TEXT,
                payment_method TEXT DEFAULT 'telebirr',
                account_identifier TEXT DEFAULT ''
            );
        """)
        db.execute("""
            CREATE TABLE peerpay_withdrawals (
                payment_id TEXT PRIMARY KEY,
                telegram_id INTEGER,
                amount REAL,
                captured INTEGER DEFAULT 0,
                released INTEGER DEFAULT 0,
                status TEXT,
                updated_at TEXT,
                bank_code TEXT DEFAULT 'telebirr',
                account_number TEXT DEFAULT ''
            );
        """)

        # Insert test deposits within window
        db.execute(
            "INSERT INTO peerpay_deposits (payment_id, amount, credited, status, updated_at, payment_method, account_identifier) VALUES (?, ?, 1, 'succeeded', '2026-10-27 10:00:00', 'telebirr', '0911223344')",
            ("dep_1", 1000.0),
        )
        db.execute(
            "INSERT INTO peerpay_deposits (payment_id, amount, credited, status, updated_at, payment_method, account_identifier) VALUES (?, ?, 1, 'succeeded', '2026-10-27 11:00:00', 'telebirr', '0988776655')",
            ("dep_2", 2000.0),
        )
        db.execute(
            "INSERT INTO peerpay_deposits (payment_id, amount, credited, status, updated_at, payment_method, account_identifier) VALUES (?, ?, 1, 'succeeded', '2026-10-27 12:00:00', 'cbebirr', '')",
            ("dep_3", 500.0),
        )

        # Insert test withdrawals within window
        db.execute(
            "INSERT INTO peerpay_withdrawals (payment_id, amount, captured, status, updated_at, bank_code, account_number) VALUES (?, ?, 1, 'succeeded', '2026-10-27 14:00:00', 'telebirr', '0911223344')",
            ("wd_1", 300.0),
        )
        db.execute(
            "INSERT INTO peerpay_withdrawals (payment_id, amount, captured, status, updated_at, bank_code, account_number) VALUES (?, ?, 1, 'succeeded', '2026-10-27 15:00:00', 'cbe', '1000123456')",
            ("wd_2", 700.0),
        )
        db.commit()

        start_str = "2026-10-26 18:00:00"
        end_str = "2026-10-27 18:00:00"

        # Query deposits (using production WHERE clause)
        dep_query = """
        SELECT payment_method, account_identifier, COUNT(*) as dep_count, COALESCE(SUM(amount), 0.0) as total_amount
        FROM peerpay_deposits
        WHERE (credited = 1 OR status IN ('succeeded', 'manually_succeeded', 'approved', 'completed'))
          AND status NOT IN ('failed', 'manually_failed', 'cancelled', 'expired', 'review_required', 'pending', 'created', 'awaiting_transfer')
          AND amount > 0
          AND updated_at >= ? AND updated_at < ?
        GROUP BY payment_method, account_identifier
        """
        cursor = db.execute(dep_query, (start_str, end_str))
        dep_rows = cursor.fetchall()

        self.assertEqual(len(dep_rows), 3)
        totals_by_acc = {f"{r['payment_method']}_{r['account_identifier']}": r["total_amount"] for r in dep_rows}
        self.assertEqual(totals_by_acc["telebirr_0911223344"], 1000.0)
        self.assertEqual(totals_by_acc["telebirr_0988776655"], 2000.0)
        self.assertEqual(totals_by_acc["cbebirr_"], 500.0)

        # Query withdrawals (using production WHERE clause)
        wd_query = """
        SELECT bank_code, COUNT(*) as wd_count, COALESCE(SUM(amount), 0.0) as total_amount
        FROM peerpay_withdrawals
        WHERE (captured = 1 OR status IN ('succeeded', 'captured', 'completed', 'approved', 'manually_succeeded'))
          AND released = 0
          AND status NOT IN ('held', 'created', 'pending', 'released', 'failed', 'cancelled', 'expired', 'review_required')
          AND amount > 0
          AND updated_at >= ? AND updated_at < ?
        GROUP BY bank_code
        """
        cursor = db.execute(wd_query, (start_str, end_str))
        wd_rows = cursor.fetchall()

        self.assertEqual(len(wd_rows), 2)
        wd_totals = {r["bank_code"]: r["total_amount"] for r in wd_rows}
        self.assertEqual(wd_totals["telebirr"], 300.0)
        self.assertEqual(wd_totals["cbe"], 700.0)

        db.close()

    def test_approved_and_successful_transfers_filtering(self):
        """Verify that pending, failed, cancelled, and refund records are strictly excluded from transfers report."""
        import sqlite3
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row

        db.execute("""
            CREATE TABLE peerpay_deposits (
                payment_id TEXT PRIMARY KEY,
                amount REAL,
                credited INTEGER DEFAULT 0,
                status TEXT,
                updated_at TEXT,
                payment_method TEXT DEFAULT 'telebirr',
                account_identifier TEXT DEFAULT ''
            );
        """)
        db.execute("""
            CREATE TABLE peerpay_withdrawals (
                payment_id TEXT PRIMARY KEY,
                amount REAL,
                captured INTEGER DEFAULT 0,
                released INTEGER DEFAULT 0,
                status TEXT,
                updated_at TEXT,
                bank_code TEXT DEFAULT 'telebirr',
                account_number TEXT DEFAULT ''
            );
        """)
        db.execute("""
            CREATE TABLE transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT,
                amount REAL,
                status TEXT,
                description TEXT,
                fingerprint TEXT,
                created_at TEXT
            );
        """)

        # 1. Deposits test data:
        # A. Succeeded deposit -> SHOULD BE INCLUDED (500 ETB)
        db.execute(
            "INSERT INTO peerpay_deposits VALUES ('dep_ok', 500.0, 1, 'succeeded', '2026-10-27 10:00:00', 'telebirr', '')"
        )
        # B. Manually approved deposit -> SHOULD BE INCLUDED (300 ETB)
        db.execute(
            "INSERT INTO peerpay_deposits VALUES ('dep_manual', 300.0, 1, 'manually_succeeded', '2026-10-27 11:00:00', 'cbebirr', '')"
        )
        # C. Pending deposit -> MUST BE EXCLUDED (1000 ETB)
        db.execute(
            "INSERT INTO peerpay_deposits VALUES ('dep_pending', 1000.0, 0, 'awaiting_transfer', '2026-10-27 12:00:00', 'telebirr', '')"
        )
        # D. Review required / uncredited deposit -> MUST BE EXCLUDED (250 ETB)
        db.execute(
            "INSERT INTO peerpay_deposits VALUES ('dep_review', 250.0, 0, 'review_required', '2026-10-27 12:30:00', 'telebirr', '')"
        )
        # E. Failed deposit -> MUST BE EXCLUDED (400 ETB)
        db.execute(
            "INSERT INTO peerpay_deposits VALUES ('dep_fail', 400.0, 0, 'failed', '2026-10-27 13:00:00', 'telebirr', '')"
        )

        # 2. Withdrawals test data:
        # A. Succeeded withdrawal -> SHOULD BE INCLUDED (200 ETB)
        db.execute(
            "INSERT INTO peerpay_withdrawals VALUES ('wd_ok', 200.0, 1, 0, 'succeeded', '2026-10-27 14:00:00', 'telebirr', '')"
        )
        # B. Manually approved / succeeded withdrawal -> SHOULD BE INCLUDED (150 ETB)
        db.execute(
            "INSERT INTO peerpay_withdrawals VALUES ('wd_man_appr', 150.0, 1, 0, 'approved', '2026-10-27 14:30:00', 'cbe', '')"
        )
        # C. Created / pending withdrawal hold -> MUST BE EXCLUDED (800 ETB)
        db.execute(
            "INSERT INTO peerpay_withdrawals VALUES ('wd_pending', 800.0, 0, 0, 'created', '2026-10-27 15:00:00', 'telebirr', '')"
        )
        # D. Released / cancelled withdrawal -> MUST BE EXCLUDED (600 ETB)
        db.execute(
            "INSERT INTO peerpay_withdrawals VALUES ('wd_released', 600.0, 0, 1, 'released', '2026-10-27 16:00:00', 'cbebirr', '')"
        )

        # 3. Transactions fallback test data:
        # A. Completed manual deposit -> SHOULD BE INCLUDED (100 ETB)
        db.execute(
            "INSERT INTO transactions VALUES (1, 'deposit', 100.0, 'completed', 'Manual telebirr topup', NULL, '2026-10-27 10:15:00')"
        )
        # B. Pending deposit -> MUST BE EXCLUDED (90 ETB)
        db.execute(
            "INSERT INTO transactions VALUES (2, 'deposit', 90.0, 'pending', 'Pending telebirr deposit', NULL, '2026-10-27 10:20:00')"
        )
        # C. Withdrawal refund deposit -> MUST BE EXCLUDED (600 ETB)
        db.execute(
            "INSERT INTO transactions VALUES (3, 'deposit', 600.0, 'completed', 'PeerPay withdrawal refund — wd_released', 'peerpay_wd_refund:wd_released', '2026-10-27 16:00:00')"
        )
        # D. Pending withdrawal transaction -> MUST BE EXCLUDED (800 ETB)
        db.execute(
            "INSERT INTO transactions VALUES (4, 'withdraw', 800.0, 'pending', 'PeerPay withdrawal hold — wd_pending (telebirr)', NULL, '2026-10-27 15:00:00')"
        )
        # E. Completed withdrawal transaction -> SHOULD BE INCLUDED (50 ETB)
        db.execute(
            "INSERT INTO transactions VALUES (5, 'withdraw', 50.0, 'completed', 'Completed manual withdrawal (Telebirr)', NULL, '2026-10-27 15:30:00')"
        )

        db.commit()

        start_str = "2026-10-26 18:00:00"
        end_str = "2026-10-27 18:00:00"

        # Test deposits query
        dep_query = """
        SELECT payment_method, account_identifier, COUNT(*) as dep_count, COALESCE(SUM(amount), 0.0) as total_amount
        FROM peerpay_deposits
        WHERE (credited = 1 OR status IN ('succeeded', 'manually_succeeded', 'approved', 'completed'))
          AND status NOT IN ('failed', 'manually_failed', 'cancelled', 'expired', 'review_required', 'pending', 'created', 'awaiting_transfer')
          AND amount > 0
          AND updated_at >= ? AND updated_at < ?
        GROUP BY payment_method, account_identifier
        """
        dep_rows = db.execute(dep_query, (start_str, end_str)).fetchall()
        tot_dep = sum(r["total_amount"] for r in dep_rows)
        # Only dep_ok (500) and dep_manual (300) = 800 ETB
        self.assertEqual(tot_dep, 800.0)
        self.assertEqual(sum(r["dep_count"] for r in dep_rows), 2)

        # Test fallback deposits query (refund excluded, pending excluded)
        tx_dep_query = """
        SELECT description, amount
        FROM transactions
        WHERE type = 'deposit' AND status = 'completed'
          AND created_at >= ? AND created_at < ?
          AND (fingerprint IS NULL OR (NOT fingerprint LIKE 'peerpay_dep:%' AND NOT fingerprint LIKE 'peerpay_wd_refund:%'))
          AND (description IS NULL OR (NOT description LIKE '%refund%' AND NOT description LIKE '%bonus%'))
        """
        tx_dep_rows = db.execute(tx_dep_query, (start_str, end_str)).fetchall()
        self.assertEqual(len(tx_dep_rows), 1)
        self.assertEqual(tx_dep_rows[0]["amount"], 100.0)

        # Test withdrawals query
        wd_query = """
        SELECT bank_code, COUNT(*) as wd_count, COALESCE(SUM(amount), 0.0) as total_amount
        FROM peerpay_withdrawals
        WHERE (captured = 1 OR status IN ('succeeded', 'captured', 'completed', 'approved', 'manually_succeeded'))
          AND released = 0
          AND status NOT IN ('held', 'created', 'pending', 'released', 'failed', 'cancelled', 'expired', 'review_required')
          AND amount > 0
          AND updated_at >= ? AND updated_at < ?
        GROUP BY bank_code
        """
        wd_rows = db.execute(wd_query, (start_str, end_str)).fetchall()
        tot_wd = sum(r["total_amount"] for r in wd_rows)
        # Only wd_ok (200) and wd_man_appr (150) = 350 ETB
        self.assertEqual(tot_wd, 350.0)
        self.assertEqual(sum(r["wd_count"] for r in wd_rows), 2)

        # Test fallback withdrawals query (pending excluded)
        tx_wd_query = """
        SELECT description, amount
        FROM transactions
        WHERE type IN ('withdraw', 'withdrawal')
          AND status IN ('completed', 'succeeded', 'approved')
          AND status NOT IN ('pending', 'held', 'cancelled', 'failed', 'released')
          AND created_at >= ? AND created_at < ?
        """
        tx_wd_rows = db.execute(tx_wd_query, (start_str, end_str)).fetchall()
        self.assertEqual(len(tx_wd_rows), 1)
        self.assertEqual(tx_wd_rows[0]["amount"], 50.0)

        db.close()


if __name__ == "__main__":
    unittest.main()
