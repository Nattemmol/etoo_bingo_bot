"""Unit tests for Super Bingo scheduled reminders, announcements, and winner notifications."""
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.config import settings
from bot import database as db
from bot.messages import (
    SUPER_BINGO_REMINDER_6H,
    SUPER_BINGO_REMINDER_10M,
    format_amharic_prize,
    format_super_bingo_winner_announcement,
)
from bot.reminders import (
    broadcast_super_bingo_announcement,
    send_super_bingo_reminder_6h,
    send_super_bingo_reminder_10m,
    send_super_bingo_winner_announcement,
    get_eat_now,
    EAT,
)


class TestSuperBingoReminders(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Use in-memory SQLite or test DB
        await db.init_db()

    def test_reminder_messages_content(self):
        """Verify reminder templates contain exact requested text and support contact info."""
        self.assertIn("🗓ዘወትር ከእሁድ እስከ እሁድ", SUPER_BINGO_REMINDER_6H)
        self.assertIn("🕙 ከምሽቱ 1 ሰዐት", SUPER_BINGO_REMINDER_6H)
        self.assertIn("0963572327", SUPER_BINGO_REMINDER_6H)
        self.assertIn("@SEtoo_9", SUPER_BINGO_REMINDER_6H)

        self.assertIn("የ ETOO ሱፐር ቢንጎ ጨዋታ", SUPER_BINGO_REMINDER_10M)
        self.assertIn("ሙሉ ዝግ", SUPER_BINGO_REMINDER_10M)
        self.assertIn("0963572327", SUPER_BINGO_REMINDER_10M)
        self.assertIn("@SEtoo_9", SUPER_BINGO_REMINDER_10M)

    def test_instruction_messages_support_contact(self):
        """Verify instruction templates contain @SEtoo_9 and 0963572327."""
        from bot.messages import INSTRUCTIONS_MENU, INSTRUCTIONS_10, INSTRUCTIONS_50, INSTRUCTIONS_GENERAL
        for tpl in (INSTRUCTIONS_MENU, INSTRUCTIONS_10, INSTRUCTIONS_50, INSTRUCTIONS_GENERAL):
            self.assertIn("SEtoo", tpl)
            self.assertIn("0963572327", tpl)

    def test_format_amharic_prize(self):
        """Verify prize formatting matches Amharic conventions and user specification."""
        self.assertEqual(format_amharic_prize(50000.0), "50 ሺ ብር")
        self.assertEqual(format_amharic_prize(25000.0), "25 ሺ ብር")
        self.assertEqual(format_amharic_prize(1000.0), "1 ሺ ብር")
        self.assertEqual(format_amharic_prize(500.0), "500 ብር")
        self.assertEqual(format_amharic_prize(1250.0), "1,250 ብር")

    def test_single_winner_announcement_format(self):
        """Verify single winner formatting matches example: 1. በሪሁን ደሴው ፡ 50 ሺ ብር."""
        winners = [{"name": "በሪሁን ደሴው", "prize": 50000.0}]
        announcement = format_super_bingo_winner_announcement(winners, 50000.0)

        self.assertIn("🏆 የዛሬ ሱፐር ቢንጎ አሸናፊ 🏆", announcement)
        self.assertIn("1. በሪሁን ደሴው ፡ 50 ሺ ብር", announcement)

    def test_multiple_winners_announcement_format(self):
        """Verify multiple winners divide the pot and list each winner."""
        winners = [
            {"name": "በሪሁን ደሴው"},
            {"name": "ናትናኤል ተመስገን"},
        ]
        # Total pot 50,000 -> divided to 25,000 each
        announcement = format_super_bingo_winner_announcement(winners, 50000.0)

        self.assertIn("🏆 የዛሬ ሱፐር ቢንጎ አሸናፊ 🏆", announcement)
        self.assertIn("1. በሪሁን ደሴው ፡ 25 ሺ ብር", announcement)
        self.assertIn("2. ናትናኤል ተመስገን ፡ 25 ሺ ብር", announcement)

    async def test_get_all_user_ids(self):
        """Ensure get_all_user_ids returns list of ints from database."""
        test_id_1 = 8888801
        test_id_2 = 8888802
        await db.create_user(test_id_1, "+251911111111", "user1", "User One")
        await db.create_user(test_id_2, "+251922222222", "user2", "User Two")

        ids = await db.get_all_user_ids()
        self.assertIn(test_id_1, ids)
        self.assertIn(test_id_2, ids)

    async def test_broadcast_super_bingo_announcement_mock(self):
        """Test broadcast delivery with photo and caption via mocked bot."""
        mock_bot = AsyncMock()
        mock_msg = MagicMock()
        mock_photo_size = MagicMock()
        mock_photo_size.file_id = "test_file_id_123"
        mock_msg.photo = [mock_photo_size]
        mock_bot.send_photo.return_value = mock_msg

        with patch("bot.reminders.settings") as mock_settings, \
             patch("bot.reminders.db.get_all_user_ids", return_value=[12345, 67890]):
            mock_settings.announcement_channel_id = "@test_channel"
            mock_settings.announcement_broadcast_users = True
            mock_settings.super_bingo_image_path = None

            res = await broadcast_super_bingo_announcement(
                bot=mock_bot,
                text="Test announcement text",
            )

            # Targets: @test_channel, 12345, 67890 -> total 3
            self.assertEqual(res["total"], 3)
            self.assertEqual(res["sent"], 3)
            self.assertEqual(res["failed"], 0)
            self.assertEqual(mock_bot.send_message.call_count, 3)

    def test_eat_timezone(self):
        """Verify Ethiopian time is UTC+3."""
        eat_time = get_eat_now()
        self.assertEqual(eat_time.utcoffset(), timedelta(hours=3))

    async def test_peerpay_503_fallback_messaging(self):
        """Verify _create_and_send_peerpay_checkout handles 503 order_routing_unavailable gracefully."""
        from bot.handlers.deposit import _create_and_send_peerpay_checkout

        mock_message = AsyncMock()
        mock_context = MagicMock()
        mock_context.user_data = {}

        err_503_response = {
            "error": {
                "code": "order_routing_unavailable",
                "message": "No eligible receiving account is currently available to process this order.",
                "retryable": True,
            },
            "status_code": 503,
        }

        for m in ("telebirr", "telebirr_1", "telebirr_2", "cbebirr", "cbe_bank"):
            mock_message.reset_mock()
            with patch("bot.handlers.deposit.peerpay_client.create_deposit", new_callable=AsyncMock) as mock_create:
                mock_create.return_value = err_503_response

                await _create_and_send_peerpay_checkout(
                    message=mock_message,
                    telegram_id=99999,
                    amount=50.0,
                    method=m,
                    context=mock_context,
                )
                self.assertTrue(mock_message.reply_text.called)
                sent_text = mock_message.reply_text.call_args[0][0]
                self.assertIn("የክፍያ ማስፈንጠሪያ ማዘጋጀት አልተቻለም", sent_text)
                # Verify that parse_mode="Markdown" does not cause entity errors
                parse_mode = mock_message.reply_text.call_args[1].get("parse_mode")
                if parse_mode == "Markdown":
                    # Check that markdown delimiters are balanced
                    parts = sent_text.split("`")
                    ast_count = sum(p.count("*") for i, p in enumerate(parts) if i % 2 == 0)
                    self.assertEqual(ast_count % 2, 0, f"Odd asterisks for method {m}")

    def test_super_bingo_reminder_keyboard_channel_vs_user(self):
        """Verify channel keyboard has standard URL button (no WebAppInfo) and user has WebAppInfo."""
        from bot.keyboards import super_bingo_reminder_keyboard

        channel_kb = super_bingo_reminder_keyboard(is_channel=True)
        user_kb = super_bingo_reminder_keyboard(is_channel=False)

        # Channel button must be URL, no web_app
        channel_btn = channel_kb.inline_keyboard[0][0]
        self.assertIsNotNone(channel_btn.url)
        self.assertIsNone(channel_btn.web_app)
        self.assertIn("start=super_50", channel_btn.url)

        # User button must be web_app
        user_btn = user_kb.inline_keyboard[0][0]
        self.assertIsNotNone(user_btn.web_app)

    async def test_broadcast_routes_channel_markup_to_channel_target(self):
        """Verify broadcast automatically sends channel-safe markup to channels and web_app to users."""
        from bot.keyboards import super_bingo_reminder_keyboard

        mock_bot = AsyncMock()
        mock_msg = MagicMock()
        mock_photo_size = MagicMock()
        mock_photo_size.file_id = "cached_fid_999"
        mock_msg.photo = [mock_photo_size]
        mock_bot.send_photo.return_value = mock_msg

        with patch("bot.reminders.settings") as mock_settings, \
             patch("bot.reminders.db.get_all_user_ids", return_value=[55555]):
            mock_settings.announcement_channel_id = "@official_channel"
            mock_settings.announcement_broadcast_users = True
            mock_settings.super_bingo_image_path = None

            # Pass sample photo bytes
            sample_photo = b"fake_jpeg_bytes"
            res = await broadcast_super_bingo_announcement(
                bot=mock_bot,
                text="Super Bingo starts soon!",
                photo_bytes=sample_photo,
                reply_markup=super_bingo_reminder_keyboard(is_channel=False),
                channel_reply_markup=super_bingo_reminder_keyboard(is_channel=True),
            )

            self.assertEqual(res["sent"], 2)
            self.assertEqual(res["failed"], 0)

            # Check call args for target 1 (@official_channel)
            first_call_kwargs = mock_bot.send_photo.call_args_list[0][1]
            self.assertEqual(first_call_kwargs["chat_id"], "@official_channel")
            channel_btn = first_call_kwargs["reply_markup"].inline_keyboard[0][0]
            self.assertIsNotNone(channel_btn.url)
            self.assertIsNone(channel_btn.web_app)

            # Check call args for target 2 (user 55555)
            second_call_kwargs = mock_bot.send_photo.call_args_list[1][1]
            self.assertEqual(second_call_kwargs["chat_id"], 55555)
            user_btn = second_call_kwargs["reply_markup"].inline_keyboard[0][0]
            self.assertIsNotNone(user_btn.web_app)

    async def test_broadcast_survives_channel_error(self):
        """Verify that if the channel target throws an exception, user broadcasts still succeed."""
        import telegram.error
        from bot.keyboards import super_bingo_reminder_keyboard

        mock_bot = AsyncMock()
        # First send (channel) fails with BadRequest, second send (user) succeeds
        mock_msg = MagicMock()
        mock_msg.photo = [MagicMock(file_id="user_fid")]
        mock_bot.send_photo.side_effect = [
            telegram.error.BadRequest("Web app buttons are not allowed in channel posts"),
            mock_msg,
        ]

        with patch("bot.reminders.settings") as mock_settings, \
             patch("bot.reminders.db.get_all_user_ids", return_value=[77777]):
            mock_settings.announcement_channel_id = "@failed_channel"
            mock_settings.announcement_broadcast_users = True
            mock_settings.super_bingo_image_path = None

            res = await broadcast_super_bingo_announcement(
                bot=mock_bot,
                text="Super Bingo Announcement",
                photo_bytes=b"fake_bytes",
            )

            self.assertEqual(res["total"], 2)
            self.assertEqual(res["failed"], 1)
            self.assertEqual(res["sent"], 1)
            # User 77777 still received it!
            self.assertEqual(mock_bot.send_photo.call_count, 2)


if __name__ == "__main__":
    unittest.main()

