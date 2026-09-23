import logging

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
    deposit_callback,
    deposit_command,
    handle_pending_deposit_amount,
    handle_sms_or_reference_text,
)
from bot.handlers.menu import balance_command, history_command, instructions_callback, instructions_command
from bot.handlers.play import action_button_callback, play_command, play_room_callback
from bot.handlers.start import contact_handler, start_command
from bot.handlers.withdraw import (
    AWAITING_ACCOUNT as WD_AWAITING_ACCOUNT,
    AWAITING_AMOUNT as WD_AWAITING_AMOUNT,
    AWAITING_METHOD as WD_AWAITING_METHOD,
    withdraw_account_handler,
    withdraw_amount_handler,
    withdraw_cancel,
    withdraw_command,
    withdraw_method_callback,
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
        ],
        states={
            WD_AWAITING_METHOD: [
                CallbackQueryHandler(withdraw_method_callback, pattern=r"^withdraw_")
            ],
            WD_AWAITING_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_amount_handler)
            ],
            WD_AWAITING_ACCOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_account_handler)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", withdraw_cancel),
            CommandHandler("start", start_command),
            CommandHandler("play", play_command),
        ],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.CONTACT, contact_handler))

    app.add_handler(CommandHandler("instructions", instructions_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("play", play_command))
    app.add_handler(CommandHandler("deposit", deposit_command))

    app.add_handler(withdraw_conv)
    app.add_handler(CallbackQueryHandler(play_room_callback, pattern=r"^room_"))
    app.add_handler(CallbackQueryHandler(deposit_callback, pattern=r"^deposit_"))
    app.add_handler(CallbackQueryHandler(instructions_callback, pattern=r"^inst_"))
    app.add_handler(CallbackQueryHandler(action_button_callback, pattern=r"^btn_action_"))

    # Combined text handler: pending deposit amount → SMS / receipt parser
    async def _text_dispatcher(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:  # type: ignore[override]
        if await handle_pending_deposit_amount(update, context):  # type: ignore[arg-type]
            return
        await handle_sms_or_reference_text(update, context)  # type: ignore[arg-type]

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _text_dispatcher))

    return app


async def post_init(application: Application) -> None:
    await init_db()
    logger.info("Database initialized.")


def main() -> None:
    # Same lifecycle as `python run.py` (fresh event loop + restart on Telegram
    # network errors). Do not call Application.run_polling() here.
    from run import run_bot

    run_bot()


if __name__ == "__main__":
    main()
