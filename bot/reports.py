"""Automated and on-demand daily and weekly revenue reporting for EtooBingo.

Reports:
1. Daily:
   - Scheduled: Every night at 3:00 Ethiopian Time (21:00 EAT / 18:00 UTC) covering the completed 24 hours.
   - On-Demand: Live accumulated progress from last night 3:00 up to the current exact moment.
2. Weekly:
   - Scheduled: Every Sunday night at 3:00 Ethiopian Time (21:00 EAT / 18:00 UTC) covering the completed 7 days.
   - On-Demand: Live accumulated progress from last Sunday night 3:00 up to the current exact moment.

Metrics:
- Number of 10 Birr games (single-player games are excluded as no house fee was extracted; multiplayer games with fines/locks are counted).
- Total amount (the 2 Birr extracted per card) from 10 Birr games.
- Total amount (the 10 Birr extracted per card) from 50 Birr Super Bingo games.
- Grand Total house commission.

Recipients:
- Delivered strictly to the bot owner's Telegram account (@Ettmol / ID 1559899527), with support for adding more recipients dynamically.
- Formatted in clear, beautiful Amharic.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any

from telegram import Bot, Update
from telegram.ext import ContextTypes

from bot.config import settings
from bot import database as db

logger = logging.getLogger(__name__)

# Ethiopian Local Time: East Africa Time (EAT), UTC+03:00
EAT = timezone(timedelta(hours=3))

DEFAULT_OWNER_ID = 1559899527  # @Ettmol


def get_eat_now() -> datetime:
    """Return current datetime in Ethiopian local time (UTC+3)."""
    return datetime.now(EAT)


def get_daily_window(now: datetime | None = None, is_live: bool = False) -> tuple[datetime, datetime, str]:
    """Calculate the [start, end) time window and Amharic label for daily report.

    Parameters
    ----------
    now:
        Current EAT datetime (defaults to get_eat_now()).
    is_live:
        If True, returns window from last night 3:00 (21:00 EAT) up to current time `now`.
        If False (scheduled completed cycle), returns the past 24h [yesterday 21:00, today 21:00].
    """
    if now is None:
        now = get_eat_now()

    if is_live:
        # Determine last night 3:00 (21:00 EAT)
        if now.hour >= 21:
            start_eat = now.replace(hour=21, minute=0, second=0, microsecond=0)
            window_label = "ከዛሬ ማታ 3:00 እስከ አሁን (የቀጥታ ሁኔታ / Live)"
        else:
            start_eat = (now - timedelta(days=1)).replace(hour=21, minute=0, second=0, microsecond=0)
            window_label = "ከትላንት ማታ 3:00 እስከ አሁን (የቀጥታ ሁኔታ / Live)"
        end_eat = now
    else:
        # Scheduled full 24 hours up to today 21:00 EAT
        end_eat = now.replace(hour=21, minute=0, second=0, microsecond=0)
        start_eat = end_eat - timedelta(days=1)
        window_label = "ከትላንት ማታ 3:00 እስከ ዛሬ ማታ 3:00 (24 ሰዓት)"

    return start_eat, end_eat, window_label


def get_weekly_window(now: datetime | None = None, is_live: bool = False) -> tuple[datetime, datetime, str]:
    """Calculate the [start, end) time window and Amharic label for weekly report.

    Parameters
    ----------
    now:
        Current EAT datetime (defaults to get_eat_now()).
    is_live:
        If True, returns window from last Sunday 21:00 EAT up to current time `now`.
        If False (scheduled completed cycle), returns the past 7 days [prev Sunday 21:00, today Sunday 21:00].
    """
    if now is None:
        now = get_eat_now()

    if is_live:
        # If today is Sunday after 21:00, last Sunday is today 21:00
        if now.weekday() == 6 and now.hour >= 21:
            start_eat = now.replace(hour=21, minute=0, second=0, microsecond=0)
        else:
            # Days back to most recent Sunday
            days_back = (now.weekday() - 6) % 7
            if days_back == 0:
                days_back = 7
            start_eat = (now - timedelta(days=days_back)).replace(hour=21, minute=0, second=0, microsecond=0)
        end_eat = now
        window_label = "ካለፈው እሁድ ማታ 3:00 እስከ አሁን (የቀጥታ ሁኔታ / Live)"
    else:
        # Scheduled full 7 days up to today Sunday 21:00 EAT
        end_eat = now.replace(hour=21, minute=0, second=0, microsecond=0)
        start_eat = end_eat - timedelta(days=7)
        window_label = "ካለፈው እሁድ ማታ 3:00 እስከ ዛሬ እሁድ ማታ 3:00 (7 ቀናት)"

    return start_eat, end_eat, window_label


def format_revenue_report(
    data: dict[str, Any],
    start_eat: datetime,
    end_eat: datetime,
    window_label: str,
    report_type: str = "daily",
    is_live: bool = False,
) -> str:
    """Format report metrics into standard Amharic message."""
    start_str = start_eat.strftime("%Y-%m-%d %H:%M")
    end_str = end_eat.strftime("%Y-%m-%d %H:%M")

    is_daily = report_type == "daily"
    header_title = (
        "📊 *ዕለታዊ የገቢ ሪፖርት (Daily Revenue Report)*"
        if is_daily
        else "📈 *ሳምንታዊ የገቢ ሪፖርት (Weekly Revenue Report)*"
    )
    if is_live:
        header_title += " ⚡ _[የቀጥታ መረጃ]_"

    g_10 = data.get("games_10", 0)
    c_10 = data.get("cards_10", 0)
    cut_10 = float(data.get("cut_10", 0.0))

    g_50 = data.get("games_50", 0)
    c_50 = data.get("cards_50", 0)
    cut_50 = float(data.get("cut_50", 0.0))

    grand_total = float(data.get("grand_total", 0.0))

    lines = [
        header_title,
        f"📅 *ጊዜ:* {window_label}",
        f"🕒 *ሰዓት (EAT):* `{start_str}` — `{end_str}`",
        "",
        "🎮 *የ 10 ብር ጨዋታ (10 Birr Game):*",
        f"• የተካሄዱ ጨዋታዎች: *{g_10}*",
        f"• የተሸጡ ካርቴላዎች: *{c_10}*",
        f"• የተቆረጠ የቤት ኮሚሽን (በካርድ 2 ብር): *{cut_10:,.2f} ETB*",
        "",
        "⭐ *የ 50 ብር ሱፐር ቢንጎ (Super Bingo):*",
        f"• የተካሄዱ ጨዋታዎች: *{g_50}*",
        f"• የተሸጡ ካርቴላዎች: *{c_50}*",
        f"• የተቆረጠ የቤት ኮሚሽን (በካርድ 10 ብር): *{cut_50:,.2f} ETB*",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        f"💰 *አጠቃላይ የተጣራ የቤት ገቢ (Grand Total):* *{grand_total:,.2f} ETB*",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    # Deposits Section
    deposits = data.get("deposits") or {}
    dep_accounts = deposits.get("by_account") or []
    tot_dep_amt = float(deposits.get("total_amount") or 0.0)
    tot_dep_cnt = int(deposits.get("total_count") or 0)

    lines.append("")
    lines.append("📥 *ጠቅላላ ገቢ በየአካውንቱ (Deposits by Account):*")
    if dep_accounts:
        for acc in dep_accounts:
            lbl = acc.get("label", "Telebirr")
            amt = float(acc.get("amount") or 0.0)
            cnt = int(acc.get("count") or 0)
            lines.append(f"• {lbl}: *{amt:,.2f} ETB* ({cnt} ተቀማጭ)")
        lines.append(f"➡️ *አጠቃላይ የተቀማጭ ገንዘብ:* *{tot_dep_amt:,.2f} ETB* ({tot_dep_cnt} ተቀማጮች)")
    else:
        lines.append("• ምንም የተቀማጭ ገንዘብ እንቅስቃሴ አልነበረም (0.00 ETB)")

    # Withdrawals Section
    withdrawals = data.get("withdrawals") or {}
    wd_accounts = withdrawals.get("by_account") or []
    tot_wd_amt = float(withdrawals.get("total_amount") or 0.0)
    tot_wd_cnt = int(withdrawals.get("total_count") or 0)

    lines.append("")
    lines.append("📤 *ጠቅላላ ወጪ በየአካውንቱ (Withdrawals by Account):*")
    if wd_accounts:
        for acc in wd_accounts:
            lbl = acc.get("label", "Telebirr")
            amt = float(acc.get("amount") or 0.0)
            cnt = int(acc.get("count") or 0)
            lines.append(f"• {lbl}: *{amt:,.2f} ETB* ({cnt} ወጪ)")
        lines.append(f"➡️ *አጠቃላይ የተከፈለ ገንዘብ:* *{tot_wd_amt:,.2f} ETB* ({tot_wd_cnt} ክፍያዎች)")
    else:
        lines.append("• ምንም የወጪ ገንዘብ እንቅስቃሴ አልነበረም (0.00 ETB)")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


async def get_report_recipients() -> set[int]:
    """Retrieve all authorized recipient Telegram IDs for reports."""
    recipients = {DEFAULT_OWNER_ID}

    # Include settings admin ID if configured
    if getattr(settings, "admin_telegram_id", None):
        try:
            recipients.add(int(settings.admin_telegram_id))
        except (ValueError, TypeError):
            pass

    # Include additional recipients from game_metadata if configured
    try:
        raw = await db.get_game_metadata("report_recipient_ids")
        if raw:
            ids = json.loads(raw)
            if isinstance(ids, list):
                for uid in ids:
                    try:
                        recipients.add(int(uid))
                    except (ValueError, TypeError):
                        pass
    except Exception as exc:
        logger.warning("Could not load report_recipient_ids metadata: %s", exc)

    return recipients


async def add_report_recipient(telegram_id: int) -> bool:
    """Add a new authorized recipient ID to persistent metadata."""
    try:
        recipients = await get_report_recipients()
        recipients.add(int(telegram_id))
        await db.set_game_metadata("report_recipient_ids", json.dumps(list(recipients)))
        return True
    except Exception as exc:
        logger.error("Failed to add report recipient %s: %s", telegram_id, exc)
        return False


async def send_report_to_recipients(bot: Bot, message_text: str) -> dict[str, int]:
    """Deliver a report message to all authorized owner/admin accounts."""
    recipients = await get_report_recipients()
    sent = 0
    failed = 0

    for chat_id in recipients:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=message_text,
                parse_mode="Markdown",
            )
            sent += 1
            logger.info("Revenue report delivered successfully to %s", chat_id)
        except Exception as exc:
            logger.warning("Failed to send revenue report to %s: %s", chat_id, exc)
            failed += 1

    return {"sent": sent, "failed": failed, "total": len(recipients)}


async def trigger_scheduled_daily_report(bot: Bot, now: datetime | None = None) -> bool:
    """Generate and dispatch the scheduled 24-hour daily report."""
    if now is None:
        now = get_eat_now()

    start_eat, end_eat, window_label = get_daily_window(now, is_live=False)
    data = await db.get_revenue_report_data(start_eat, end_eat)
    report_text = format_revenue_report(data, start_eat, end_eat, window_label, report_type="daily", is_live=False)
    res = await send_report_to_recipients(bot, report_text)
    return res["sent"] > 0


async def trigger_scheduled_weekly_report(bot: Bot, now: datetime | None = None) -> bool:
    """Generate and dispatch the scheduled 7-day weekly report."""
    if now is None:
        now = get_eat_now()

    start_eat, end_eat, window_label = get_weekly_window(now, is_live=False)
    data = await db.get_revenue_report_data(start_eat, end_eat)
    report_text = format_revenue_report(data, start_eat, end_eat, window_label, report_type="weekly", is_live=False)
    res = await send_report_to_recipients(bot, report_text)
    return res["sent"] > 0


async def run_revenue_report_scheduler(bot: Bot) -> None:
    """Continuous background loop triggering daily and weekly reports at Ethiopian night 3:00 (21:00 EAT)."""
    logger.info("Revenue report scheduler background loop started.")

    while True:
        try:
            now = get_eat_now()
            today_str = now.strftime("%Y-%m-%d")

            # Check metadata for deduplication across restarts
            try:
                last_daily_sent = await db.get_game_metadata("last_daily_revenue_report_date")
                last_weekly_sent = await db.get_game_metadata("last_weekly_revenue_report_date")
            except Exception as e:
                logger.warning("Could not read revenue report metadata: %s", e)
                last_daily_sent = getattr(run_revenue_report_scheduler, "_mem_daily", None)
                last_weekly_sent = getattr(run_revenue_report_scheduler, "_mem_weekly", None)

            # 1. Daily Report Check:
            # Trigger once a day at Ethiopian night 3:00 (21:00 EAT).
            # If current hour is 21 or later, and today's report hasn't fired yet:
            if now.hour >= 21 and last_daily_sent != today_str:
                logger.info("Triggering scheduled Daily Revenue Report for %s at %s EAT", today_str, now.strftime("%H:%M:%S"))
                success = await trigger_scheduled_daily_report(bot, now)
                run_revenue_report_scheduler._mem_daily = today_str
                try:
                    await db.set_game_metadata("last_daily_revenue_report_date", today_str)
                except Exception as exc:
                    logger.warning("Could not persist last_daily_revenue_report_date: %s", exc)

            # 2. Weekly Report Check:
            # Trigger on Sunday (now.weekday() == 6) at Ethiopian night 3:00 (21:00 EAT).
            if now.weekday() == 6 and now.hour >= 21 and last_weekly_sent != today_str:
                logger.info("Triggering scheduled Weekly Revenue Report for Sunday %s at %s EAT", today_str, now.strftime("%H:%M:%S"))
                success = await trigger_scheduled_weekly_report(bot, now)
                run_revenue_report_scheduler._mem_weekly = today_str
                try:
                    await db.set_game_metadata("last_weekly_revenue_report_date", today_str)
                except Exception as exc:
                    logger.warning("Could not persist last_weekly_revenue_report_date: %s", exc)

            await asyncio.sleep(25)
        except asyncio.CancelledError:
            logger.info("Revenue report scheduler loop stopped.")
            break
        except Exception as exc:
            logger.error("Error in revenue report scheduler loop: %s", exc, exc_info=True)
            await asyncio.sleep(30)


# ---------------------------------------------------------------------------
# Telegram Command Handlers (On-Demand Status)
# ---------------------------------------------------------------------------

async def _is_authorized_reporter(user) -> bool:
    """Verify if user is owner (@Ettmol) or in authorized recipient IDs."""
    if not user:
        return False
    recipients = await get_report_recipients()
    if user.id in recipients:
        return True
    username = (user.username or "").lower().lstrip("@")
    if username == "ettmol":
        # Register user ID dynamically
        await add_report_recipient(user.id)
        return True
    return False


async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /report [daily|weekly|all] — live status from last cycle start up to current moment."""
    message = update.message
    if not message:
        return

    user = update.effective_user
    if not await _is_authorized_reporter(user):
        await message.reply_text("⛔ ይህ ትዕዛዝ ለቦቱ ባለቤት (@Ettmol) ብቻ የተፈቀደ ነው።")
        return

    now = get_eat_now()
    args = [a.lower() for a in (context.args or [])]
    sub = args[0] if args else "all"

    try:
        if sub in ("daily", "day", "d"):
            start_eat, end_eat, label = get_daily_window(now, is_live=True)
            data = await db.get_revenue_report_data(start_eat, end_eat)
            text = format_revenue_report(data, start_eat, end_eat, label, report_type="daily", is_live=True)
            await message.reply_text(text, parse_mode="Markdown")

        elif sub in ("weekly", "week", "w"):
            start_eat, end_eat, label = get_weekly_window(now, is_live=True)
            data = await db.get_revenue_report_data(start_eat, end_eat)
            text = format_revenue_report(data, start_eat, end_eat, label, report_type="weekly", is_live=True)
            await message.reply_text(text, parse_mode="Markdown")

        else:
            # Default "all": show both daily live and weekly live
            start_d, end_d, label_d = get_daily_window(now, is_live=True)
            data_d = await db.get_revenue_report_data(start_d, end_d)
            text_d = format_revenue_report(data_d, start_d, end_d, label_d, report_type="daily", is_live=True)

            start_w, end_w, label_w = get_weekly_window(now, is_live=True)
            data_w = await db.get_revenue_report_data(start_w, end_w)
            text_w = format_revenue_report(data_w, start_w, end_w, label_w, report_type="weekly", is_live=True)

            await message.reply_text(text_d, parse_mode="Markdown")
            await message.reply_text(text_w, parse_mode="Markdown")

    except Exception as exc:
        logger.error("Failed to generate on-demand report: %s", exc, exc_info=True)
        await message.reply_text(f"⚠️ ሪፖርቱን በማመንጨት ላይ ስህተት ተፈጥሯል: {exc}")


async def daily_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shortcut handler for /daily_report — live status from last night 3:00 to now."""
    context.args = ["daily"]
    await report_command(update, context)


async def weekly_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shortcut handler for /weekly_report — live status from last Sunday night 3:00 to now."""
    context.args = ["weekly"]
    await report_command(update, context)


async def add_report_recipient_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /add_report_recipient <telegram_id> — adds an extra report recipient account."""
    message = update.message
    if not message:
        return

    user = update.effective_user
    if not await _is_authorized_reporter(user):
        await message.reply_text("⛔ ይህ ትዕዛዝ ለቦቱ ባለቤት (@Ettmol) ብቻ የተፈቀደ ነው።")
        return

    args = context.args or []
    if not args or not args[0].isdigit():
        await message.reply_text("ℹ️ እባክዎ የሚጨመረውን የቴሌግራም User ID ያስገቡ:\n`/add_report_recipient 12345678`", parse_mode="Markdown")
        return

    new_id = int(args[0])
    ok = await add_report_recipient(new_id)
    if ok:
        await message.reply_text(f"✅ የቴሌግራም አካውንት `{new_id}` የገቢ ሪፖርት ተቀባይ ሆኖ በተሳካ ሁኔታ ተመዝግቧል!", parse_mode="Markdown")
    else:
        await message.reply_text("⚠️ አካውንቱን መመዝገብ አልተቻለም። እባክዎ እንደገና ይሞክሩ።")
