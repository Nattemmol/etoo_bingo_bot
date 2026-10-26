"""Unit tests for automated and on-demand daily/weekly revenue reporting."""

import asyncio
from datetime import datetime, timedelta, timezone
import unittest

from bot.reports import (
    EAT,
    DEFAULT_OWNER_ID,
    add_report_recipient,
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
    def test_format_daily_report(self):
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
        text = format_revenue_report(data, start, end, label, report_type="daily", is_live=False)

        self.assertIn("ዕለታዊ የገቢ ሪፖርት", text)
        self.assertIn("የ 10 ብር ጨዋታ", text)
        self.assertIn("• የተካሄዱ ጨዋታዎች: *15*", text)
        self.assertIn("• የተሸጡ ካርቴላዎች: *45*", text)
        self.assertIn("90.00 ETB", text)
        self.assertIn("የ 50 ብር ሱፐር ቢንጎ", text)
        self.assertIn("300.00 ETB", text)
        self.assertIn("አጠቃላይ የተጣራ ገቢ (Grand Total):* *390.00 ETB*", text)

    def test_format_weekly_report(self):
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
        text = format_revenue_report(data, start, end, label, report_type="weekly", is_live=False)

        self.assertIn("ሳምንታዊ የገቢ ሪፖርት", text)
        self.assertIn("2,700.00 ETB", text)
        self.assertIn("600.00 ETB", text)
        self.assertIn("2,100.00 ETB", text)


class TestRevenueReportRecipients(unittest.IsolatedAsyncioTestCase):
    async def test_owner_authorized(self):
        class MockUser:
            id = DEFAULT_OWNER_ID
            username = "Ettmol"

        is_auth = await _is_authorized_reporter(MockUser())
        self.assertTrue(is_auth)

    async def test_username_case_insensitive_authorization(self):
        class MockUser:
            id = 9999999999
            username = "ettmol"

        is_auth = await _is_authorized_reporter(MockUser())
        self.assertTrue(is_auth)

    async def test_unauthorized_user(self):
        class MockUser:
            id = 123456
            username = "random_user"

        is_auth = await _is_authorized_reporter(MockUser())
        self.assertFalse(is_auth)


class TestDatabaseRevenueQuery(unittest.IsolatedAsyncioTestCase):
    async def test_sqlite_aggregation(self):
        import aiosqlite
        # Use an in-memory database to test the exact SQL query logic
        db = await aiosqlite.connect(":memory:")
        db.row_factory = aiosqlite.Row
        await db.execute("""
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
        # Window: 2026-10-26 18:00:00 UTC (21:00 EAT) to 2026-10-27 18:00:00 UTC (21:00 EAT)
        # 1. 10 Birr inside window (2 cards, 2 birr cut = 4 birr)
        await db.execute(
            "INSERT INTO house_revenue (room_id, cards_count, cut_per_card, total_revenue, created_at) VALUES (?, ?, ?, ?, ?)",
            ("room_play_10", 2, 2.0, 4.0, "2026-10-26 20:00:00"),
        )
        # 2. 10 Birr inside window (3 cards, 2 birr cut = 6 birr)
        await db.execute(
            "INSERT INTO house_revenue (room_id, cards_count, cut_per_card, total_revenue, created_at) VALUES (?, ?, ?, ?, ?)",
            ("room_play_10", 3, 2.0, 6.0, "2026-10-27 10:00:00"),
        )
        # 3. Super Bingo 50 Birr inside window (10 cards, 10 birr cut = 100 birr)
        await db.execute(
            "INSERT INTO house_revenue (room_id, cards_count, cut_per_card, total_revenue, created_at) VALUES (?, ?, ?, ?, ?)",
            ("room_super_50", 10, 10.0, 100.0, "2026-10-27 16:15:00"),
        )
        # 4. Record OUTSIDE window (yesterday afternoon, before 18:00 UTC)
        await db.execute(
            "INSERT INTO house_revenue (room_id, cards_count, cut_per_card, total_revenue, created_at) VALUES (?, ?, ?, ?, ?)",
            ("room_play_10", 2, 2.0, 4.0, "2026-10-26 15:00:00"),
        )
        await db.commit()

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
        async with db.execute(query, (start_str, end_str)) as cursor:
            rows = await cursor.fetchall()

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

        await db.close()

        self.assertEqual(res["games_10"], 2)
        self.assertEqual(res["cards_10"], 5)
        self.assertEqual(res["cut_10"], 10.0)
        self.assertEqual(res["games_50"], 1)
        self.assertEqual(res["cards_50"], 10)
        self.assertEqual(res["cut_50"], 100.0)
        self.assertEqual(res["grand_total"], 110.0)


if __name__ == "__main__":
    unittest.main()
