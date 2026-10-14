"""Unit tests for Super Bingo winning card image generation and announcements."""
import io
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image

from server.card_image import (
    generate_winner_card_image,
    generate_winner_card_image_from_winner_dict,
)
from bot.reminders import send_super_bingo_winner_announcement


class TestCardImage(unittest.IsolatedAsyncioTestCase):
    def test_generate_single_winner_card_image(self):
        """Test single card image generation produces valid JPEG of expected dimensions."""
        sample_card = [
            [4, 22, 35, 51, 68],
            [11, 19, 39, 55, 72],
            [2, 27, None, 49, 63],
            [8, 30, 33, 58, 75],
            [15, 18, 44, 60, 70],
        ]
        img_bytes = generate_winner_card_image(
            card=sample_card,
            card_id=84,
            winner_name="Natnael",
            prize=5000.0,
            pattern="full",
        )
        self.assertIsInstance(img_bytes, bytes)
        self.assertGreater(len(img_bytes), 10000)

        # Verify it can be loaded by PIL and has dimensions 760x930
        im = Image.open(io.BytesIO(img_bytes))
        self.assertEqual(im.size, (760, 930))
        self.assertEqual(im.format, "JPEG")

    def test_generate_winner_from_dict(self):
        """Test wrapper from winner dictionary."""
        winner_dict = {
            "telegram_id": 99999,
            "name": "በሪሁን ደሴው",
            "card_id": 42,
            "prize": 25000.0,
            "pattern": "full",
        }
        img_bytes = generate_winner_card_image_from_winner_dict(winner_dict, pot=25000.0)
        self.assertIsInstance(img_bytes, bytes)
        self.assertGreater(len(img_bytes), 10000)

        im = Image.open(io.BytesIO(img_bytes))
        self.assertEqual(im.size, (760, 930))

    async def test_send_super_bingo_winner_announcement_single(self):
        """Test announcement flow for single winner sends 1 photo with caption."""
        mock_bot = AsyncMock()
        mock_msg = MagicMock()
        mock_photo = MagicMock()
        mock_photo.file_id = "test_file_id_single"
        mock_msg.photo = [mock_photo]
        mock_bot.send_photo.return_value = mock_msg

        winners = [
            {"telegram_id": 111, "name": "አሸናፊ 1", "card_id": 10, "prize": 50000.0}
        ]

        with patch("bot.reminders.settings") as mock_settings, \
             patch("bot.reminders.db.get_all_user_ids", return_value=[111]):
            mock_settings.announcement_channel_id = ""
            mock_settings.announcement_broadcast_users = True

            res = await send_super_bingo_winner_announcement(mock_bot, winners, 50000.0)
            self.assertEqual(res["sent"], 1)
            self.assertTrue(mock_bot.send_photo.called)
            # Ensure caption contains winner name
            caption = mock_bot.send_photo.call_args[1]["caption"]
            self.assertIn("አሸናፊ 1", caption)

    async def test_send_super_bingo_winner_announcement_multiple(self):
        """Test announcement flow for multiple winners sends media group (Option A2)."""
        mock_bot = AsyncMock()
        mock_msg1 = MagicMock()
        mock_photo1 = MagicMock()
        mock_photo1.file_id = "test_file_id_1"
        mock_msg1.photo = [mock_photo1]

        mock_msg2 = MagicMock()
        mock_photo2 = MagicMock()
        mock_photo2.file_id = "test_file_id_2"
        mock_msg2.photo = [mock_photo2]

        mock_bot.send_media_group.return_value = [mock_msg1, mock_msg2]

        winners = [
            {"telegram_id": 111, "name": "አሸናፊ 1", "card_id": 10, "prize": 25000.0},
            {"telegram_id": 222, "name": "አሸናፊ 2", "card_id": 20, "prize": 25000.0},
        ]

        with patch("bot.reminders.settings") as mock_settings, \
             patch("bot.reminders.db.get_all_user_ids", return_value=[111]):
            mock_settings.announcement_channel_id = ""
            mock_settings.announcement_broadcast_users = True

            res = await send_super_bingo_winner_announcement(mock_bot, winners, 50000.0)
            self.assertEqual(res["sent"], 1)
            self.assertTrue(mock_bot.send_media_group.called)
            media_arg = mock_bot.send_media_group.call_args[1]["media"]
            self.assertEqual(len(media_arg), 2)


if __name__ == "__main__":
    unittest.main()
