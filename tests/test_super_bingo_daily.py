import asyncio
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from bot import database as db
from bot.config import settings
from server.game import (
    EAT,
    GamePhase,
    GameRoom,
    ROOM_CONFIG,
    get_eat_now,
    get_seconds_until_super_bingo,
    get_super_bingo_last_played_date,
    is_super_bingo_open,
    is_super_bingo_played_today,
    mark_super_bingo_played,
    reset_super_bingo_played,
)
from server.main import get_room, get_room_state, reset_room


class TestSuperBingoDaily(unittest.TestCase):
    def setUp(self):
        asyncio.run(db.init_db())
        # Clear in-memory state before each test
        reset_super_bingo_played()

    def tearDown(self):
        reset_super_bingo_played()

    def test_in_memory_tracking(self):
        self.assertFalse(is_super_bingo_played_today())
        self.assertIsNone(get_super_bingo_last_played_date())

        today_str = get_eat_now().strftime("%Y-%m-%d")
        mark_super_bingo_played(today_str)

        self.assertTrue(is_super_bingo_played_today())
        self.assertEqual(get_super_bingo_last_played_date(), today_str)

        # Yesterday's date should NOT be considered played today
        yesterday_str = (get_eat_now() - timedelta(days=1)).strftime("%Y-%m-%d")
        mark_super_bingo_played(yesterday_str)
        self.assertFalse(is_super_bingo_played_today())

    def test_is_super_bingo_open_lifecycle(self):
        # 1. always_open override
        self.assertTrue(is_super_bingo_open(always_open=True))

        # 2. Before 19:00 (e.g. 18:30)
        dt_1830 = datetime(2026, 10, 9, 18, 30, 0, tzinfo=EAT)
        with patch("server.game.get_eat_now", return_value=dt_1830):
            self.assertFalse(is_super_bingo_open())

        # 3. During 19:xx when NOT yet played today
        dt_1905 = datetime(2026, 10, 9, 19, 5, 0, tzinfo=EAT)
        with patch("server.game.get_eat_now", return_value=dt_1905):
            self.assertTrue(is_super_bingo_open())

        # 4. During 19:xx AFTER today's game has played
        mark_super_bingo_played("2026-10-09")
        with patch("server.game.get_eat_now", return_value=dt_1905):
            # MUST be False! This prevents repeating rounds during the same hour!
            self.assertFalse(is_super_bingo_open())

        # 5. After 20:00
        dt_2005 = datetime(2026, 10, 9, 20, 5, 0, tzinfo=EAT)
        with patch("server.game.get_eat_now", return_value=dt_2005):
            self.assertFalse(is_super_bingo_open())

    def test_get_seconds_until_super_bingo(self):
        # 1. always_open returns 0
        self.assertEqual(get_seconds_until_super_bingo(always_open=True), 0)

        # 2. At 18:30 (30 mins before 19:00 today)
        dt_1830 = datetime(2026, 10, 9, 18, 30, 0, tzinfo=EAT)
        with patch("server.game.get_eat_now", return_value=dt_1830):
            secs = get_seconds_until_super_bingo()
            self.assertEqual(secs, 1800)

        # 3. At 19:05 when NOT yet played today -> returns 0 (ready to play)
        dt_1905 = datetime(2026, 10, 9, 19, 5, 0, tzinfo=EAT)
        with patch("server.game.get_eat_now", return_value=dt_1905):
            secs = get_seconds_until_super_bingo()
            self.assertEqual(secs, 0)

        # 4. At 19:05 AFTER today's game has played -> MUST countdown to tomorrow 19:00:00!
        mark_super_bingo_played("2026-10-09")
        with patch("server.game.get_eat_now", return_value=dt_1905):
            secs = get_seconds_until_super_bingo()
            # Tomorrow 19:00 is 24 hours - 5 minutes = 86400 - 300 = 86100 seconds
            self.assertEqual(secs, 86100)

        # 5. At 20:00 (after 19:xx window) -> counts down to tomorrow 19:00
        dt_2000 = datetime(2026, 10, 9, 20, 0, 0, tzinfo=EAT)
        with patch("server.game.get_eat_now", return_value=dt_2000):
            secs = get_seconds_until_super_bingo()
            # 23 hours = 82800 seconds
            self.assertEqual(secs, 82800)

    def test_database_metadata_persistence(self):
        async def _test():
            await db.set_game_metadata("last_super_bingo_played_date", "2026-10-09")
            val = await db.get_game_metadata("last_super_bingo_played_date")
            self.assertEqual(val, "2026-10-09")

            # Update to next day
            await db.set_game_metadata("last_super_bingo_played_date", "2026-10-10")
            val2 = await db.get_game_metadata("last_super_bingo_played_date")
            self.assertEqual(val2, "2026-10-10")

        asyncio.run(_test())

    def test_room_state_reflects_daily_status(self):
        reset_room("room_super_50")
        room = get_room("room_super_50")

        # Simulate 19:10 EAT when game has already completed today
        dt_1910 = datetime(2026, 10, 9, 19, 10, 0, tzinfo=EAT)
        mark_super_bingo_played("2026-10-09")

        with patch("server.game.get_eat_now", return_value=dt_1910):
            state = get_room_state(room)
            self.assertFalse(state["is_open"])
            # Countdown is to tomorrow 19:00:00 (~85,800s), NOT 0
            self.assertEqual(state["seconds_until_open"], 85800)


if __name__ == "__main__":
    unittest.main()
