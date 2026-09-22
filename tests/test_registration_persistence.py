import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from bot import database as db


def run(coro):
    return asyncio.run(coro)


class TestRegistrationPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.original_settings = db.settings
        run(db.close_db())
        db.settings = SimpleNamespace(database_path=Path(self.tmp_dir) / "users.db")

    def tearDown(self):
        run(db.close_db())
        db.settings = self.original_settings

    def test_same_telegram_id_is_upserted_without_losing_balance(self):
        run(db.init_db())
        run(db.create_user(100_001, "0911000000", "first", "First"))
        run(db.update_balance(100_001, 125.50))

        # Telegram can redeliver a contact update. It must update the profile,
        # not create a second account or reset the stored wallet balance.
        run(db.create_user(100_001, "0922000000", "second", "Second"))

        user = run(db.get_user(100_001))
        self.assertIsNotNone(user)
        self.assertEqual(user["phone_number"], "0922000000")
        self.assertEqual(user["username"], "second")
        self.assertAlmostEqual(user["balance"], 125.50)

    def test_existing_database_is_migrated_without_losing_player(self):
        path = db.settings.database_path
        conn = sqlite3.connect(path)
        conn.execute(
            """
            CREATE TABLE users (
                telegram_id INTEGER PRIMARY KEY,
                phone_number TEXT NOT NULL,
                username TEXT,
                first_name TEXT,
                balance REAL NOT NULL DEFAULT 0.0,
                registered_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)",
            (100_002, "0911333444", "saved", "Saved", 88.0, "2026-01-01 00:00:00"),
        )
        conn.commit()
        conn.close()

        run(db.init_db())
        user = run(db.get_user(100_002))

        self.assertIsNotNone(user)
        self.assertEqual(user["phone_number"], "0911333444")
        self.assertAlmostEqual(user["balance"], 88.0)
        self.assertIn("updated_at", user)


if __name__ == "__main__":
    unittest.main()
