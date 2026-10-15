"""Super Bingo scheduled reminders, announcements, and winner notifications."""
import asyncio
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from typing import Any

import telegram.error
from telegram import Bot, InlineKeyboardMarkup, InputMediaPhoto

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
    photo_bytes: bytes | None = None,
    photo_bytes_list: list[bytes] | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> dict[str, int]:
    """Broadcast an announcement with photo or media group to the configured channel and registered users.

    Caches Telegram file_id(s) from the first successful send to stream-send
    to subsequent recipients without re-uploading the file(s).
    """
    # If a list of photos was provided
    if photo_bytes_list and len(photo_bytes_list) == 1:
        photo_bytes = photo_bytes_list[0]
        photo_bytes_list = None

    if not photo_bytes_list and not photo_bytes:
        path = Path(photo_path) if photo_path else settings.super_bingo_image_path
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

    users_count = 0
    if settings.announcement_broadcast_users:
        try:
            user_ids = await db.get_all_user_ids()
            users_count = len(user_ids)
            for uid in user_ids:
                if uid not in targets:
                    targets.append(uid)
        except Exception as e:
            logger.warning("Could not fetch user ids for announcement: %s", e)

    if not targets:
        logger.warning(
            "Super Bingo announcement has NO targets! ANNOUNCEMENT_CHANNEL_ID='%s', ANNOUNCEMENT_BROADCAST_USERS=%s, users_in_db=%d",
            settings.announcement_channel_id,
            settings.announcement_broadcast_users,
            users_count,
        )
    else:
        logger.info(
            "Broadcasting Super Bingo announcement to %d targets (channel='%s', users=%d)",
            len(targets),
            settings.announcement_channel_id,
            users_count,
        )

    sent = 0
    failed = 0
    cached_file_id: str | None = None
    cached_file_ids: list[str] | None = None

    for target in targets:
        try:
            if photo_bytes_list and len(photo_bytes_list) > 1:
                # Option A2: Multiple winners media album
                if cached_file_ids:
                    media = [
                        InputMediaPhoto(media=fid, caption=text if idx == 0 else None)
                        for idx, fid in enumerate(cached_file_ids)
                    ]
                else:
                    media = [
                        InputMediaPhoto(media=b, caption=text if idx == 0 else None)
                        for idx, b in enumerate(photo_bytes_list)
                    ]
                sent_msgs = await bot.send_media_group(chat_id=target, media=media)
                if not cached_file_ids and sent_msgs:
                    cached_file_ids = [m.photo[-1].file_id for m in sent_msgs if m.photo]
                if reply_markup:
                    try:
                        await bot.send_message(
                            chat_id=target,
                            text="🎮 በሱፐር ቢንጎ ይጫወቱ (Play superBingo)",
                            reply_markup=reply_markup,
                        )
                    except Exception:
                        pass
            elif cached_file_id or photo_bytes:
                # Single photo announcement
                photo_payload = cached_file_id if cached_file_id else photo_bytes
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
                if photo_bytes_list and len(photo_bytes_list) > 1:
                    media_sources = cached_file_ids if cached_file_ids else photo_bytes_list
                    media = [
                        InputMediaPhoto(media=item, caption=text if idx == 0 else None)
                        for idx, item in enumerate(media_sources)
                    ]
                    await bot.send_media_group(chat_id=target, media=media)
                    if reply_markup:
                        try:
                            await bot.send_message(
                                chat_id=target,
                                text="🎮 በሱፐር ቢንጎ ይጫወቱ (Play superBingo)",
                                reply_markup=reply_markup,
                            )
                        except Exception:
                            pass
                elif cached_file_id or photo_bytes:
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
    """Send winner announcement with real card images at the conclusion of 50 Birr Super Bingo."""
    text = format_super_bingo_winner_announcement(winners, pot)
    photo_bytes_list: list[bytes] = []

    if winners:
        try:
            from server.card_image import generate_winner_card_image_from_winner_dict
            for w in winners:
                try:
                    img_data = generate_winner_card_image_from_winner_dict(w, pot=pot)
                    if img_data:
                        photo_bytes_list.append(img_data)
                except Exception as e:
                    logger.error("Failed to generate winner card image: %s", e)
        except Exception as exc:
            logger.error("Could not import card_image generator: %s", exc)

    return await broadcast_super_bingo_announcement(
        bot=bot,
        text=text,
        photo_bytes_list=photo_bytes_list if photo_bytes_list else None,
        reply_markup=super_bingo_reminder_keyboard(),
    )


async def run_super_bingo_scheduler(bot: Bot) -> None:
    """Background loop checking EAT time and triggering 6h and 10m reminders."""
    logger.info("Super Bingo reminder scheduler started.")

    while True:
        try:
            now = get_eat_now()
            today_str = now.strftime("%Y-%m-%d")

            # Check persistent state in db (fallback to in-memory if db query fails)
            try:
                last_sent_6h_date = await db.get_game_metadata("last_super_bingo_6h_sent_date")
                last_sent_10m_date = await db.get_game_metadata("last_super_bingo_10m_sent_date")
            except Exception as e:
                logger.warning("Could not read super bingo reminder metadata: %s", e)
                last_sent_6h_date = getattr(run_super_bingo_scheduler, "_mem_6h", None)
                last_sent_10m_date = getattr(run_super_bingo_scheduler, "_mem_10m", None)

            # 1. Check 6-hour reminder:
            # Scheduled for 13:00 EAT (1:00 PM). If not sent yet today and time is between 13:00 and 18:49 EAT:
            if 13 <= now.hour < 18 or (now.hour == 18 and now.minute < 50):
                if last_sent_6h_date != today_str:
                    logger.info("Triggering 6-hour Super Bingo reminder for %s at %s EAT", today_str, now.strftime("%H:%M:%S"))
                    await send_super_bingo_reminder_6h(bot)
                    run_super_bingo_scheduler._mem_6h = today_str
                    try:
                        await db.set_game_metadata("last_super_bingo_6h_sent_date", today_str)
                    except Exception as e:
                        logger.warning("Could not persist 6h reminder date: %s", e)

            # 2. Check 10-minute reminder:
            # Scheduled for 18:50 EAT (6:50 PM / 12:50 local night). Fires anytime from 18:50:00 to 18:59:59 before game:
            if now.hour == 18 and now.minute >= 50:
                if last_sent_10m_date != today_str:
                    logger.info("Triggering 10-minute Super Bingo reminder for %s at %s EAT", today_str, now.strftime("%H:%M:%S"))
                    await send_super_bingo_reminder_10m(bot)
                    run_super_bingo_scheduler._mem_10m = today_str
                    try:
                        await db.set_game_metadata("last_super_bingo_10m_sent_date", today_str)
                    except Exception as e:
                        logger.warning("Could not persist 10m reminder date: %s", e)

            await asyncio.sleep(20)
        except asyncio.CancelledError:
            logger.info("Super Bingo reminder scheduler stopped.")
            break
        except Exception as exc:
            logger.error("Error in Super Bingo scheduler loop: %s", exc)
            await asyncio.sleep(30)
