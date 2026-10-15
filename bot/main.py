import logging

from telegram import Update
from telegram.ext import (
    Application,
    ContextTypes,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.config import settings
from bot.database import init_db
from bot.handlers.deposit import (
    deposit_amount_callback,
    deposit_callback,
    deposit_command,
    deposit_status_callback,
    handle_pending_deposit_amount,
    handle_sms_or_reference_text,
)
from bot.handlers.menu import balance_command, history_command, instructions_callback, instructions_command
from bot.handlers.play import action_button_callback, play_command, play_room_callback
from bot.handlers.start import contact_handler, start_command
from bot.handlers.withdraw import (
    WD_AWAITING_ACCOUNT,
    WD_AWAITING_AMOUNT,
    WD_AWAITING_METHOD,
    withdraw_account_handler,
    withdraw_amount_callback,
    withdraw_amount_handler,
    withdraw_cancel,
    withdraw_command,
    withdraw_method_callback,
    withdraw_status_callback,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    import telegram.error
    if isinstance(context.error, (telegram.error.TimedOut, telegram.error.NetworkError)):
        logger.warning("Telegram network glitch (transient timeout): %s", context.error)
    else:
        logger.exception("Unhandled exception in update handler: %s", context.error)


def build_application() -> Application:
    from telegram.request import HTTPXRequest

    t_request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=10.0,
        connection_pool_size=20,
    )
    app = (
        Application.builder()
        .token(settings.bot_token)
        .request(t_request)
        .build()
    )
    app.add_error_handler(error_handler)

    withdraw_conv = ConversationHandler(
        entry_points=[
            CommandHandler("withdraw", withdraw_command),
            CallbackQueryHandler(withdraw_method_callback, pattern=r"^withdraw_"),
            CallbackQueryHandler(withdraw_amount_callback, pattern=r"^wd_amt_"),
        ],
        states={
            WD_AWAITING_METHOD: [
                CallbackQueryHandler(withdraw_method_callback, pattern=r"^withdraw_"),
            ],
            WD_AWAITING_AMOUNT: [
                CallbackQueryHandler(withdraw_amount_callback, pattern=r"^wd_amt_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_amount_handler),
            ],
            WD_AWAITING_ACCOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_account_handler),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", withdraw_cancel),
            CommandHandler("start", start_command),
            CommandHandler("play", play_command),
            CommandHandler("deposit", deposit_command),
        ],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.CONTACT, contact_handler))

    app.add_handler(CommandHandler(["instructions", "instruction"], instructions_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("play", play_command))
    app.add_handler(CommandHandler("deposit", deposit_command))
    app.add_handler(CommandHandler("test_reminder", test_reminder_command))

    app.add_handler(withdraw_conv)
    app.add_handler(CallbackQueryHandler(play_room_callback, pattern=r"^room_"))
    app.add_handler(CallbackQueryHandler(deposit_callback, pattern=r"^deposit_"))
    app.add_handler(CallbackQueryHandler(deposit_amount_callback, pattern=r"^dep_amt_"))
    app.add_handler(CallbackQueryHandler(deposit_status_callback, pattern=r"^dep_status_"))
    app.add_handler(CallbackQueryHandler(withdraw_status_callback, pattern=r"^wd_status_"))
    app.add_handler(CallbackQueryHandler(instructions_callback, pattern=r"^inst_"))
    app.add_handler(CallbackQueryHandler(action_button_callback, pattern=r"^btn_action_"))

    # Combined text handler: pending deposit amount → SMS / receipt parser
    async def _text_dispatcher(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:  # type: ignore[override]
        if await handle_pending_deposit_amount(update, context):  # type: ignore[arg-type]
            return
        await handle_sms_or_reference_text(update, context)  # type: ignore[arg-type]

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _text_dispatcher))

    return app


async def test_reminder_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Test or preview Super Bingo reminders and winner announcements."""
    from bot.keyboards import super_bingo_reminder_keyboard
    from bot.messages import (
        SUPER_BINGO_REMINDER_6H,
        SUPER_BINGO_REMINDER_10M,
        format_super_bingo_winner_announcement,
    )
    from bot.reminders import send_super_bingo_reminder_6h, send_super_bingo_reminder_10m

    message = update.message
    if not message:
        return

    user = update.effective_user
    if settings.admin_telegram_id and (not user or user.id != settings.admin_telegram_id):
        await message.reply_text("⛔ ይህ ትዕዛዝ ለአስተዳዳሪ (Admin) ብቻ የተፈቀደ ነው።")
        return

    args = context.args or []
    sub = args[0].lower() if args else ""
    is_broadcast = len(args) > 1 and args[1].lower() == "all"

    img_path = settings.super_bingo_image_path
    photo_bytes: bytes | None = None
    if img_path and img_path.is_file():
        try:
            with open(img_path, "rb") as f:
                photo_bytes = f.read()
        except Exception:
            pass

    kb = super_bingo_reminder_keyboard(settings.webapp_url)

    if sub == "6h":
        if is_broadcast:
            res = await send_super_bingo_reminder_6h(context.bot)
            await message.reply_text(f"📢 6-hour reminder broadcasted: {res}")
            return
        if photo_bytes:
            await message.reply_photo(photo=photo_bytes, caption=SUPER_BINGO_REMINDER_6H, reply_markup=kb)
        else:
            await message.reply_text(SUPER_BINGO_REMINDER_6H, reply_markup=kb)
    elif sub == "10m":
        if is_broadcast:
            res = await send_super_bingo_reminder_10m(context.bot)
            await message.reply_text(f"📢 10-minute reminder broadcasted: {res}")
            return
        if photo_bytes:
            await message.reply_photo(photo=photo_bytes, caption=SUPER_BINGO_REMINDER_10M, reply_markup=kb)
        else:
            await message.reply_text(SUPER_BINGO_REMINDER_10M, reply_markup=kb)
    elif sub in ("winner", "winners"):
        # Support flexible arguments: /test_reminder winner [card_id] [prize] [name] [all]
        clean_args = [a for a in args[1:] if a.lower() != "all"]
        is_broadcast = "all" in [a.lower() for a in args[1:]]

        winner_card_id = 84
        winner_prize = 50000.0
        winner_name = (update.effective_user.first_name if update.effective_user else None) or "በሪሁን ደሴው"

        if len(clean_args) >= 1 and clean_args[0].isdigit():
            winner_card_id = int(clean_args[0])
        if len(clean_args) >= 2:
            try:
                winner_prize = float(clean_args[1].replace(",", ""))
            except ValueError:
                pass
        if len(clean_args) >= 3:
            winner_name = " ".join(clean_args[2:]).replace("_", " ")

        sample_winners = [
            {
                "telegram_id": update.effective_user.id if update.effective_user else 0,
                "name": winner_name,
                "card_id": winner_card_id,
                "prize": winner_prize,
                "pattern": "full",
            }
        ]
        if is_broadcast:
            from bot.reminders import send_super_bingo_winner_announcement
            res = await send_super_bingo_winner_announcement(context.bot, sample_winners, winner_prize)
            await message.reply_text(f"📢 Winner announcement broadcasted: {res}")
            return
        from server.card_image import generate_winner_card_image_from_winner_dict
        card_img = generate_winner_card_image_from_winner_dict(sample_winners[0], winner_prize)
        winner_text = format_super_bingo_winner_announcement(sample_winners, winner_prize)
        await message.reply_photo(photo=card_img, caption=winner_text, reply_markup=kb)
    elif sub == "multiwinner":
        sample_winners = [
            {"telegram_id": 1, "name": "በሪሁን ደሴው", "card_id": 84, "prize": 25000.0, "pattern": "full"},
            {"telegram_id": 2, "name": "ናትናኤል ተመስገን", "card_id": 12, "prize": 25000.0, "pattern": "full"},
        ]
        if is_broadcast:
            from bot.reminders import send_super_bingo_winner_announcement
            res = await send_super_bingo_winner_announcement(context.bot, sample_winners, 50000.0)
            await message.reply_text(f"📢 Multi-winner announcement broadcasted: {res}")
            return
        from server.card_image import generate_winner_card_image_from_winner_dict
        from telegram import InputMediaPhoto
        winner_text = format_super_bingo_winner_announcement(sample_winners, 50000.0)
        media = [
            InputMediaPhoto(media=generate_winner_card_image_from_winner_dict(w, 25000.0), caption=winner_text if i == 0 else None)
            for i, w in enumerate(sample_winners)
        ]
        await message.reply_media_group(media=media)
        await message.reply_text("🎮 በሱፐር ቢንጎ ይጫወቱ (Play superBingo)", reply_markup=kb)
    else:
        await message.reply_text(
            "ℹ️ *የማስታወቂያ መሞከሪያ መመሪያ:*\n\n"
            "• `/test_reminder 6h` — የ 6 ሰዓት በፊት ማስታወቂያ በፎቶው ይሞክሩ\n"
            "• `/test_reminder 10m` — የ 10 ደቂቃ በፊት ማስታወቂያ በፎቶው ይሞክሩ\n"
            "• `/test_reminder winner` — የአሸናፊ ማስታወቂያ ይሞክሩ (ለምሳሌ: በሪሁን ደሴው ፡ 50 ሺ ብር)\n"
            "• `/test_reminder multiwinner` — የብዙ አሸናፊዎች ማስታወቂያ ይሞክሩ\n"
            "• `/test_reminder 6h all` — ለሁሉም ተጠቃሚዎችና ቻናል በይፋ ይላኩ\n",
            parse_mode="Markdown",
        )


_scheduler_task = None


async def post_init(application: Application) -> None:
    global _scheduler_task
    import asyncio
    await init_db()
    logger.info("Database initialized.")
    from bot.reminders import run_super_bingo_scheduler
    _scheduler_task = asyncio.create_task(run_super_bingo_scheduler(application.bot))
    logger.info("Super Bingo reminder scheduler background task registered.")


def main() -> None:
    # Same lifecycle as `python run.py` (fresh event loop + restart on Telegram
    # network errors). Do not call Application.run_polling() here.
    from run import run_bot

    run_bot()


if __name__ == "__main__":
    main()
