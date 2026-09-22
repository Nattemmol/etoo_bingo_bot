"""Run bot and game server together."""
import logging
import os
import socket
import threading

import uvicorn

from bot.config import settings
from bot.main import build_application, post_init
from server.main import app as game_app

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_lock_socket: socket.socket | None = None


def _acquire_single_instance() -> bool:
    """Ensure only one run.py is alive: bind a sidecar lock port beside 8765.

    Note: SO_REUSEADDR must NOT be set — on Windows it would let a second
    instance forcibly bind the same port.
    """
    global _lock_socket
    lock_port = int(settings.server_port) + 1
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", lock_port))
        sock.listen(1)
        _lock_socket = sock
        return True
    except OSError:
        logger.warning(
            "Another run.py instance is already running (port %s in use) — exiting.",
            lock_port,
        )
        return False
    except Exception:  # pragma: no cover - lock is best-effort
        logger.warning("Single-instance lock unavailable, continuing anyway.")
        return True


def run_server() -> None:
    uvicorn.run(
        game_app,
        host="0.0.0.0",
        port=settings.server_port,
        log_level="info",
        ws_ping_interval=20,
        ws_ping_timeout=20,
        timeout_keep_alive=30,
        backlog=2048,
    )


def run_bot() -> None:
    import asyncio
    import telegram.error
    import time

    while True:
        try:
            asyncio.run(_run_bot_once())
            logger.info("GoodBingo bot stopped cleanly.")
            break
        except telegram.error.TimedOut:
            logger.warning("Telegram polling timed out — restarting in 3s.")
        except telegram.error.NetworkError as exc:
            logger.warning("Telegram network error (%s) — restarting in 3s.", exc)
        except telegram.error.Conflict:
            logger.warning("Telegram 409 conflict (another instance?) — waiting 8s.")
            time.sleep(8)
        except RuntimeError as exc:
            if "Event loop is closed" in str(exc):
                logger.warning("Stale event loop (%s) — restarting fresh in 3s.", exc)
            else:
                logger.exception("Polling loop crashed — restarting in 5s.")
        except Exception:
            logger.exception("Polling loop crashed — restarting in 5s.")
        time.sleep(3)


async def _run_bot_once() -> None:
    """Run the bot on the event loop provided by asyncio.run() (always fresh).

    Mirrors Application.run_polling()'s lifecycle (initialize -> post_init ->
    start_polling -> start -> run_forever) but keeps full control of the loop,
    so a closed loop is never reused across restarts.
    """
    import asyncio

    application = build_application()
    application.post_init = post_init
    logger.info("GoodBingo bot starting...")

    await application.initialize()
    await application.post_init(application)

    async def watchdog() -> None:
        """Ping Telegram periodically; restart only after repeated failures."""
        failures = 0
        while True:
            await asyncio.sleep(120)
            try:
                await application.bot.get_me()
                failures = 0
            except Exception:
                failures += 1
                if failures >= 3:
                    raise

    try:
        await application.updater.start_polling(
            allowed_updates=["message", "callback_query"],
            bootstrap_retries=5,
            timeout=20,
            drop_pending_updates=True,
        )
        await application.start()
        await asyncio.gather(watchdog(), asyncio.Event().wait())
    finally:
        # Best-effort cleanup in PTB's canonical order (stop updater, then
        # application, then shutdown). Never mask the original exception.
        for action in (
            lambda: application.updater.stop()
            if application.updater.running
            else None,
            lambda: application.stop() if application.running else None,
            lambda: application.shutdown(),
        ):
            try:
                coro = action()
                if coro is not None:
                    await coro
            except Exception:
                logger.warning("Cleanup step failed (non-fatal).", exc_info=True)


if __name__ == "__main__":
    if not _acquire_single_instance():
        logger.warning("Another run.py instance is already running — exiting.")
        os._exit(0)

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    logger.info("Game server at http://0.0.0.0:%s", settings.server_port)
    run_bot()
