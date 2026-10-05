"""Super Bingo scheduled reminders, announcements, and winner notifications."""
import asyncio
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from typing import Any

import telegram.error
from telegram import Bot, InlineKeyboardMarkup

from bot.config import settings
from bot import database as db
from bot.keyboards import super_bingo_reminder_keyboard
from bot.messages import (
    SUPER_BINGO_REMINDER_6H,
    SUPER_BINGO_REMINDER_10M,
    format_super_bingo_winner_announcement,
)

logger = logging.getLogger(__name__)

# Ethiopian Local Time: East Africa Time (EAT), UTC+03:00
EAT = timezone(timedelta(hours=3))


def get_eat_now() -> datetime:
    """Return current datetime in Ethiopian local time (UTC+3)."""
    return datetime.now(EAT)


async def broadcast_super_bingo_announcement(
    bot: Bot,
    text: str,
    photo_path: Path | str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> dict[str, int]:
    """Broadcast an announcement with photo to the configured channel and registered users.

    Caches the Telegram file_id from the first successful send to stream-send
    to subsequent recipients without re-uploading the file.
    """
    path = Path(photo_path) if photo_path else settings.super_bingo_image_path
    photo_bytes: bytes | None = None
    if path and path.is_file():
        try:
            with open(path, "rb") as f:
                photo_bytes = f.read()
        except Exception as e:
            logger.warning("Could not read super bingo image %s: %s", path, e)

    # Collect recipient targets
    targets: list[str | int] = []
    if settings.announcement_channel_id:
        targets.append(settings.announcement_channel_id.strip())

    if settings.announcement_broadcast_users:
        try:
            user_ids = await db.get_all_user_ids()
            for uid in user_ids:
                if uid not in targets:
                    targets.append(uid)
        except Exception as e:
            logger.warning("Could not fetch user ids for announcement: %s", e)

    sent = 0
    failed = 0
    cached_file_id: str | None = None

    for target in targets:
        try:
            photo_payload = cached_file_id if cached_file_id else photo_bytes
            if photo_payload:
                sent_msg = await bot.send_photo(
                    chat_id=target,
                    photo=photo_payload,
                    caption=text,
                    reply_markup=reply_markup,
                )
                if not cached_file_id and sent_msg.photo:
                    cached_file_id = sent_msg.photo[-1].file_id
            else:
                await bot.send_message(
                    chat_id=target,
                    text=text,
                    reply_markup=reply_markup,
                )
            sent += 1
            await asyncio.sleep(0.05)  # Telegram broadcast rate limit throttle
        except telegram.error.RetryAfter as e:
            logger.warning("Telegram flood limit: sleeping %s seconds", e.retry_after)
            await asyncio.sleep(e.retry_after)
            try:
                if cached_file_id or photo_bytes:
                    await bot.send_photo(
                        chat_id=target,
                        photo=cached_file_id or photo_bytes,
                        caption=text,
                        reply_markup=reply_markup,
                    )
                else:
                    await bot.send_message(
                        chat_id=target,
                        text=text,
                        reply_markup=reply_markup,
                    )
                sent += 1
            except Exception:
                failed += 1
        except (telegram.error.Forbidden, telegram.error.ChatNotFound):
            # User blocked the bot or chat was deleted
            failed += 1
        except Exception as exc:
            logger.warning("Failed to send announcement to %s: %s", target, exc)
            failed += 1

    logger.info("Super Bingo announcement finished: %d sent, %d failed", sent, failed)
    return {"total": len(targets), "sent": sent, "failed": failed}


async def send_super_bingo_reminder_6h(bot: Bot) -> dict[str, int]:
    """Send 6-hour reminder (13:00 EAT / 1:00 PM)."""
    return await broadcast_super_bingo_announcement(
        bot=bot,
        text=SUPER_BINGO_REMINDER_6H,
        reply_markup=super_bingo_reminder_keyboard(),
    )


async def send_super_bingo_reminder_10m(bot: Bot) -> dict[str, int]:
    """Send 10-minute reminder (18:50 EAT / 6:50 PM)."""
    return await broadcast_super_bingo_announcement(
        bot=bot,
        text=SUPER_BINGO_REMINDER_10M,
        reply_markup=super_bingo_reminder_keyboard(),
    )


async def send_super_bingo_winner_announcement(
    bot: Bot, winners: list[dict[str, Any]], pot: float
) -> dict[str, int]:
    """Send winner announcement at the conclusion of 50 Birr Super Bingo."""
    text = format_super_bingo_winner_announcement(winners, pot)
    return await broadcast_super_bingo_announcement(
        bot=bot,
        text=text,
        reply_markup=super_bingo_reminder_keyboard(),
    )


async def run_super_bingo_scheduler(bot: Bot) -> None:
    """Background loop checking EAT time and triggering 6h and 10m reminders."""
    logger.info("Super Bingo reminder scheduler started.")
    last_sent_6h_date: str | None = None
    last_sent_10m_date: str | None = None

    while True:
        try:
            now = get_eat_now()
            today_str = now.strftime("%Y-%m-%d")

            # 1. Check 6-hour reminder (13:00 EAT / 1:00 PM)
            if now.hour == 13 and now.minute < 5 and last_sent_6h_date != today_str:
                logger.info("Triggering 6-hour Super Bingo reminder for %s", today_str)
                await send_super_bingo_reminder_6h(bot)
                last_sent_6h_date = today_str

            # 2. Check 10-minute reminder (18:50 EAT / 6:50 PM)
            if now.hour == 18 and 50 <= now.minute < 55 and last_sent_10m_date != today_str:
                logger.info("Triggering 10-minute Super Bingo reminder for %s", today_str)
                await send_super_bingo_reminder_10m(bot)
                last_sent_10m_date = today_str

            await asyncio.sleep(20)
        except asyncio.CancelledError:
            logger.info("Super Bingo reminder scheduler stopped.")
            break
        except Exception as exc:
            logger.error("Error in Super Bingo scheduler loop: %s", exc)
            await asyncio.sleep(30)
