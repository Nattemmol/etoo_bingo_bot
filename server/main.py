import asyncio
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from bot import database as db
from bot.config import settings
from bot.database import (
    complete_telebirr_order,
    get_telebirr_order,
)
from bot.peerpay import PeerPayClient, customer_id_to_telegram_id, verify_peerpay_signature
from bot.sms_parser import (
    extract_reference_and_url,
    fetch_cbe_mobile_banking_receipt,
    fetch_telebirr_receipt,
    fingerprint,
    parse_deposit_sms,
    verify_deposit_submission,
    verify_via_verify_et,
    OFFICIAL_ACCOUNTS,
)
from bot.telebirr import verify_callback_signature
from server.auth import validate_init_data
from server.game import (
    GamePhase,
    GameRoom,
    Player,
    ROOM_CONFIG,
    MAX_CARDS_PER_PLAYER,
    check_bingo,
    check_bingo_marked,
    generate_card,
    generate_card_by_id,
    get_seconds_until_super_bingo,
    is_super_bingo_open,
    number_to_letter,
)
from server.game import FREE_INDEX

logger = logging.getLogger(__name__)

WEBAPP_DIR = Path(__file__).resolve().parent.parent / "webapp"

WINNER_RESET_WAIT_SECONDS = 10.0
# Multi-winner window: everyone who claims a valid BINGO within this window
# after the first claim shares the pot equally.
BINGO_CLAIM_WINDOW_SECONDS = 5.0
SOCKET_SEND_TIMEOUT_SECONDS = 1.0
ROUND_SNAPSHOT_DEBOUNCE_SECONDS = 0.25

# FREE PLAY TESTING: bypass entry-fee balance checks and deductions so the
# playing room can be tested without real money. Set to False to enforce
# payments again (balance must cover each card's fee, deducted one at a time).
FREE_PLAY = False

rooms: dict[str, GameRoom] = {}
room_tasks: dict[str, asyncio.Task] = {}
connections: dict[str, WebSocket] = {}  # ws_id -> websocket
player_ws: dict[str, str] = {}  # ws_id -> room_id
user_ws: dict[int, set[str]] = {}  # telegram_id -> set of ws_ids
snapshot_tasks: dict[str, asyncio.Task] = {}


async def _persist_room_snapshot(room: GameRoom) -> None:
    """Save active game state to SQLite for server crash recovery."""
    try:
        player_data = {}
        for p in room.players.values():
            player_data[str(p.telegram_id)] = {
                "name": p.name,
                "card_ids": p.card_ids,
                "marks": {str(cid): list(m) for cid, m in p.marks.items()},
                "locked_cards": list(p.locked_cards),
                "forfeited": p.forfeited,
            }
        await db.save_active_round(
            room_id=room.room_id,
            phase=room.phase.value,
            pot=room.pot,
            house_income=room.house_income,
            called_numbers=room.called_numbers,
            taken_cards=room.taken_cards,
            player_data=player_data,
        )
    except Exception as e:
        logger.warning("Could not persist room snapshot for %s: %s", room.room_id, e)



async def _flush_room_snapshot(room_id: str) -> None:
    """Persist a burst of player actions once, rather than once per socket event."""
    try:
        await asyncio.sleep(ROUND_SNAPSHOT_DEBOUNCE_SECONDS)
        room = get_room(room_id)
        if room.phase in (GamePhase.LOBBY, GamePhase.PLAYING) and room.taken_cards:
            await _persist_room_snapshot(room)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Could not persist active round %s", room_id)
    finally:
        snapshot_tasks.pop(room_id, None)


def queue_room_snapshot(room_id: str) -> None:
    """Coalesce card/mark updates into one durable snapshot."""
    task = snapshot_tasks.get(room_id)
    if task is None or task.done():
        snapshot_tasks[room_id] = asyncio.create_task(_flush_room_snapshot(room_id))


def round_reset_delay(room: GameRoom) -> float:
    """The weekly room opens its next-round lobby immediately after settlement."""
    return 0.0 if room.room_id == "room_super_50" else WINNER_RESET_WAIT_SECONDS
@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    logger.info("Game server database ready.")
    for room_id in ROOM_CONFIG:
        # Check if there was an active round before server restart
        try:
            saved = await db.get_active_round(room_id)
            if saved and saved.get("phase") in (GamePhase.LOBBY.value, GamePhase.PLAYING.value) and saved.get("taken_cards"):
                logger.info("Restoring active round for %s (%d cards taken)", room_id, len(saved["taken_cards"]))
                room = get_room(room_id)
                room.phase = GamePhase(saved.get("phase", GamePhase.LOBBY.value))
                room.pot = float(saved.get("pot", 0.0))
                room.house_income = float(saved.get("house_income", 0.0))
                room.restore_called_numbers(saved.get("called_numbers", []))
                room.taken_cards = saved.get("taken_cards", {})
                for tid_str, pdata in saved.get("player_data", {}).items():
                    tid = int(tid_str)
                    dummy_ws = f"restored_{tid}"
                    cids = pdata.get("card_ids", [])
                    cards = {cid: generate_card_by_id(cid) for cid in cids}
                    marks = {int(cid): set(m) for cid, m in pdata.get("marks", {}).items()}
                    locked = set(pdata.get("locked_cards", []))
                    player = Player(
                        telegram_id=tid,
                        name=pdata.get("name", str(tid)),
                        ws_id=dummy_ws,
                        card_ids=cids,
                        cards=cards,
                        marks=marks,
                        locked_cards=locked,
                        forfeited=pdata.get("forfeited", False),
                    )
                    room.players[dummy_ws] = player
                await schedule_lobby(room_id)
                continue
        except Exception as e:
            logger.warning("Could not restore active round for %s: %s", room_id, e)

        await schedule_lobby(room_id)
    yield
    pending_snapshots = [task for task in snapshot_tasks.values() if not task.done()]
    if pending_snapshots:
        await asyncio.gather(*pending_snapshots, return_exceptions=True)
    await db.close_db()


app = FastAPI(title="EtooBingo Game Server", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_room(room_id: str) -> GameRoom:
    if room_id not in rooms:
        cfg = ROOM_CONFIG.get(room_id, {"name": room_id, "entry_fee": 10.0, "house_cut": 2.0, "max_cards": 450, "lobby_seconds": 30})
        rooms[room_id] = GameRoom(
            room_id=room_id,
            name=cfg["name"],
            entry_fee=cfg["entry_fee"],
            house_cut=cfg.get("house_cut", 0.0),
            max_cards=cfg.get("max_cards", 450),
            call_interval=cfg.get("call_interval", 4.0),
            lobby_seconds=cfg.get("lobby_seconds", 30),
            bingo_rule=cfg.get("bingo_rule", "line"),
        )
    return rooms[room_id]


def get_room_state(room: GameRoom) -> dict:
    cfg = ROOM_CONFIG.get(room.room_id, {})
    is_super = room.room_id == "room_super_50"
    is_open = is_super_bingo_open(settings.super_bingo_always_open) if is_super else True
    seconds_until = get_seconds_until_super_bingo() if is_super else 0
    schedule_text = cfg.get("schedule", "24/7")
    unique_players = len(set(p.telegram_id for p in room.players.values()))

    return {
        "id": room.room_id,
        "name": room.name,
        "entry_fee": room.entry_fee,
        "house_cut": room.house_cut,
        "max_cards": room.max_cards,
        "bingo_rule": room.bingo_rule,
        "phase": room.phase.value,
        "pot": room.pot,
        "players": len(room.taken_cards),
        "spectators": max(0, len(room.connections) - len(room.players)),
        "countdown": room.countdown or room.lobby_seconds,
        "called": room.called_numbers,
        "latest_call": (
            {
                "number": room.called_numbers[-1],
                "letter": number_to_letter(room.called_numbers[-1]),
            }
            if room.called_numbers
            else None
        ),
        "is_open": True,  # card selection allowed anytime; only the game itself is scheduled
        "schedule": schedule_text,
        "seconds_until_open": seconds_until,
        "taken_cards": room.taken_cards,
    }


async def broadcast(room: GameRoom, message: dict, exclude: str | None = None) -> None:
    """Broadcast to all connected clients. Serializes JSON once for performance."""
    payload = json.dumps(message)  # serialize ONCE — not N times
    targets = []
    ws_map = {}
    for ws_id in list(room.connections):
        if ws_id == exclude:
            continue
        ws = connections.get(ws_id)
        if ws:
            targets.append(ws_id)
            ws_map[ws_id] = ws

    if not targets:
        return

    async def _send(ws_id: str, ws: WebSocket):
        try:
            await asyncio.wait_for(ws.send_text(payload), timeout=SOCKET_SEND_TIMEOUT_SECONDS)
            return None
        except Exception:
            return ws_id

    results = await asyncio.gather(*[_send(wid, ws_map[wid]) for wid in targets], return_exceptions=True)
    dead = [r for r in results if isinstance(r, str)]
    for ws_id in dead:
        room.connections.discard(ws_id)
        # Keep player state while a socket is slow or disconnected; the user can reconnect.
        if room.phase == GamePhase.FINISHED:
            room.players.pop(ws_id, None)
        connections.pop(ws_id, None)
        player_ws.pop(ws_id, None)


async def send(ws: WebSocket, message: dict) -> None:
    await asyncio.wait_for(ws.send_json(message), timeout=SOCKET_SEND_TIMEOUT_SECONDS)


async def broadcast_user_balance(telegram_id: int, new_balance: float) -> None:
    """Broadcast real-time balance update to all open WebSockets for this user."""
    if not telegram_id:
        return
    ws_ids = user_ws.get(telegram_id, set())
    if not ws_ids:
        return
    msg = {"type": "balance", "balance": float(new_balance)}
    for wid in list(ws_ids):
        ws = connections.get(wid)
        if ws:
            try:
                await send(ws, msg)
            except Exception:
                pass


async def run_lobby_countdown(room: GameRoom) -> None:
    if room.phase == GamePhase.PLAYING:
        await run_playing_round(room)
        return

    if room.room_id == "room_super_50" and not settings.super_bingo_always_open:
        # Super Bingo: wait until 7:00 PM EAT
        secs = get_seconds_until_super_bingo()
        room.countdown = secs
        deadline_ts = time.time() + secs  # Unix timestamp for client-side countdown
        # Broadcast deadline once — clients count down locally
        await broadcast(
            room,
            {
                "type": "lobby",
                "players": len(room.taken_cards),
                "countdown": room.countdown,
                "deadline": deadline_ts,
                "pot": room.pot,
            },
        )
        while room.phase == GamePhase.LOBBY:
            secs = get_seconds_until_super_bingo()
            room.countdown = secs
            if secs <= 0:
                break
            # Only re-broadcast every 30 seconds or at key milestones
            if secs % 30 == 0 or secs <= 5:
                await broadcast(
                    room,
                    {
                        "type": "lobby",
                        "players": len(room.taken_cards),
                        "countdown": secs,
                        "pot": room.pot,
                    },
                )
            await asyncio.sleep(1)

        if room.phase != GamePhase.LOBBY:
            return

        if len(room.taken_cards) == 0:
            room_tasks.pop(room.room_id, None)
            await schedule_lobby(room.room_id)
            return

        unique_players = set(room.taken_cards.values())
        if len(unique_players) == 1:
            # Single player (1 or 2 cards): do not cut the two birr!
            room.house_cut = 0.0
            room.pot = round(len(room.taken_cards) * room.entry_fee, 2)
            room.house_income = 0.0
        else:
            cfg = ROOM_CONFIG.get(room.room_id, {})
            room.house_cut = cfg.get("house_cut", 2.0)
            room.pot = round(len(room.taken_cards) * (room.entry_fee - room.house_cut), 2)
            room.house_income = round(len(room.taken_cards) * room.house_cut, 2)

        room.phase = GamePhase.PLAYING
        await _persist_room_snapshot(room)
        await broadcast(
            room,
            {
                "type": "start",
                "pot": room.pot,
                "players": len(room.taken_cards),
            },
        )
    else:
        # 10 ETB room: 30-second countdown with deadline-based approach
        room.countdown = room.lobby_seconds
        deadline_ts = time.time() + room.lobby_seconds
        # Send deadline once — clients count down locally
        await broadcast(
            room,
            {
                "type": "lobby",
                "players": len(room.taken_cards),
                "countdown": room.countdown,
                "deadline": deadline_ts,
                "pot": room.pot,
            },
        )
        while room.countdown > 0 and room.phase == GamePhase.LOBBY:
            await asyncio.sleep(1)
            room.countdown -= 1
            # Only broadcast at key moments: 10s, 5s, 3s, 2s, 1s
            if room.countdown in (10, 5, 3, 2, 1, 0):
                await broadcast(
                    room,
                    {
                        "type": "lobby",
                        "players": len(room.taken_cards),
                        "countdown": room.countdown,
                        "pot": room.pot,
                    },
                )

        if room.phase != GamePhase.LOBBY:
            return

        if len(room.taken_cards) == 0:
            # Nobody picked a card during the countdown — restart the 30s countdown
            room_tasks.pop(room.room_id, None)
            await schedule_lobby(room.room_id)
            return

        unique_players = set(room.taken_cards.values())
        if len(unique_players) == 1:
            # Single player (1 or 2 cards): do not cut the two birr!
            room.house_cut = 0.0
            room.pot = round(len(room.taken_cards) * room.entry_fee, 2)
            room.house_income = 0.0
        else:
            cfg = ROOM_CONFIG.get(room.room_id, {})
            room.house_cut = cfg.get("house_cut", 2.0)
            room.pot = round(len(room.taken_cards) * (room.entry_fee - room.house_cut), 2)
            room.house_income = round(len(room.taken_cards) * room.house_cut, 2)

        room.phase = GamePhase.PLAYING
        await _persist_room_snapshot(room)
        await broadcast(
            room,
            {
                "type": "start",
                "pot": room.pot,
                "players": len(room.taken_cards),
            },
        )

    await run_playing_round(room)

async def run_playing_round(room: GameRoom) -> None:
    while room.phase == GamePhase.PLAYING:
        await asyncio.sleep(room.call_interval)
        if room.phase != GamePhase.PLAYING or room.bingo_window_until is not None:
            break  # a valid BINGO has been claimed; freeze the board for the 5s window

        num = room.next_number()
        if num is None:
            room.phase = GamePhase.FINISHED
            await db.clear_active_round(room.room_id)
            await broadcast(room, {"type": "game_over", "reason": "all_numbers_called"})
            await asyncio.sleep(round_reset_delay(room))
            reset_room(room.room_id)
            await schedule_lobby(room.room_id)
            new_room = get_room(room.room_id)
            await broadcast(
                new_room,
                {
                    "type": "round_reset",
                    "room": get_room_state(new_room),
                },
            )
            break

        await broadcast(
            room,
            {
                "type": "call",
                "number": num,
                "letter": number_to_letter(num),
                "called": room.called_numbers,
            },
        )

        # Snapshot every 5 ball calls for crash recovery
        if len(room.called_numbers) % 5 == 0:
            await _persist_room_snapshot(room)


async def schedule_lobby(room_id: str) -> None:
    if room_id in room_tasks and not room_tasks[room_id].done():
        return

    async def _run():
        room = get_room(room_id)
        try:
            await run_lobby_countdown(room)
        except asyncio.CancelledError:
            pass

    room_tasks[room_id] = asyncio.create_task(_run())


def reset_room(room_id: str) -> None:
    task = room_tasks.pop(room_id, None)
    # Do not cancel the task that is performing the reset: it still needs to
    # publish the reset and schedule the next round.
    try:
        current_task = asyncio.current_task()
    except RuntimeError:
        current_task = None
    if task and task is not current_task:
        task.cancel()

    cfg = ROOM_CONFIG.get(room_id, {"name": room_id, "entry_fee": 10.0, "house_cut": 2.0, "max_cards": 450, "lobby_seconds": 30})
    existing_conns = set(rooms[room_id].connections) if room_id in rooms else set()
    rooms[room_id] = GameRoom(
        room_id=room_id,
        name=cfg["name"],
        entry_fee=cfg["entry_fee"],
        house_cut=cfg.get("house_cut", 0.0),
        max_cards=cfg.get("max_cards", 450),
        call_interval=cfg.get("call_interval", 4.0),
        lobby_seconds=cfg.get("lobby_seconds", 30),
        bingo_rule=cfg.get("bingo_rule", "line"),
        connections=existing_conns,
    )


def find_winning_card(
    room: GameRoom,
    player: Player,
    allow_unmarked: bool = False,
    target_card_id: int | None = None,
) -> tuple[str, int] | None:
    """Return (pattern, card_id) for the first player card that wins.

    Ignores locked cards (false bingo penalty). Checks player's manual marks
    on called numbers so that completing a line or rule automatically
    triggers BINGO. When allow_unmarked is True, also checks called numbers directly.
    """
    card_items = (
        [(target_card_id, player.cards[target_card_id])]
        if (target_card_id is not None and target_card_id in player.cards)
        else list(player.cards.items())
    )

    for cid, card in card_items:
        if cid in player.locked_cards:
            continue
        pattern = check_bingo_marked(
            card, player.marks.get(cid, set()), room.called_set, room.bingo_rule
        )
        if not pattern and allow_unmarked:
            pattern = check_bingo(card, room.called_set, room.bingo_rule)
        if pattern:
            return pattern, cid
    return None


def add_claim(room: GameRoom, player: Player, pattern: str, card_id: int) -> bool:
    """Register an auto-detected win claim; returns True if a new claim was added."""
    now = time.monotonic()
    if room.bingo_window_until is not None and now > room.bingo_window_until:
        return False
    if any(c["telegram_id"] == player.telegram_id for c in room.bingo_claimants):
        return False

    claim = {
        "telegram_id": player.telegram_id,
        "name": player.name,
        "card_id": card_id,
        "pattern": pattern,
    }

    if room.bingo_window_until is None:
        # First auto-detected win opens the 5s multi-winner window and freezes the board.
        room.bingo_window_until = now + BINGO_CLAIM_WINDOW_SECONDS
        room.bingo_claimants = [claim]
        task = room_tasks.get(room.room_id)
        if task and not task.done():
            task.cancel()
        room_tasks.pop(room.room_id, None)
        asyncio.create_task(finalize_bingo(room.room_id))
    else:
        room.bingo_claimants.append(claim)

    return True


async def check_player_win(room: GameRoom, player: Player) -> bool:
    """Check win condition for a single player. Returns True if a new claim was made."""
    if room.phase != GamePhase.PLAYING:
        return False
    if room.bingo_window_until is not None and time.monotonic() > room.bingo_window_until:
        return False
    if not player.cards:
        return False

    win = find_winning_card(room, player, allow_unmarked=False)
    if not win:
        return False
    pattern, card_id = win
    if add_claim(room, player, pattern, card_id):
        claim_count = len(room.bingo_claimants)
        await broadcast(
            room,
            {
                "type": "bingo_claim",
                "claimant_id": player.telegram_id,
                "claimant_name": player.name,
                "card_id": card_id,
                "pattern": pattern,
                "claimants": claim_count,
                "window_seconds": int(BINGO_CLAIM_WINDOW_SECONDS),
            },
        )
        return True
    return False


async def check_all_wins(room: GameRoom) -> None:
    """Auto-declare BINGO for every player whose manual marks now satisfy the rule."""
    if room.phase != GamePhase.PLAYING:
        return
    if room.bingo_window_until is not None and time.monotonic() > room.bingo_window_until:
        return

    for player in list(room.players.values()):
        await check_player_win(room, player)


async def _delayed_locked_reset(room_id: str) -> None:
    try:
        await db.clear_active_round(room_id)
        await asyncio.sleep(round_reset_delay(get_room(room_id)))
        reset_room(room_id)
        await schedule_lobby(room_id)
        new_room = get_room(room_id)
        await broadcast(
            new_room,
            {
                "type": "round_reset",
                "room": get_room_state(new_room),
            },
        )
    except Exception as exc:
        logger.warning("Error in _delayed_locked_reset: %s", exc)


async def handle_cards_locked(
    room: GameRoom,
    player: Player,
    websocket: WebSocket,
    ws_id: str,
    target_cid: int | None,
    all_locked: bool,
) -> None:
    """Handle false BINGO card lock, single-player refund, and round ending when all cards are locked."""
    unique_players = set(room.taken_cards.values())
    is_single_player = len(unique_players) <= 1

    if all_locked and is_single_player:
        # Single player with all cards locked:
        # 1. Full refund of their entry fee (do not cut the 2 Birr)
        refund_amount = round(len(player.card_ids) * room.entry_fee, 2)
        new_balance = 0.0
        if not FREE_PLAY:
            await db.credit_balance(
                player.telegram_id,
                refund_amount,
                f"Refund: Single player all cards locked in {room.name}",
            )
        user_data = await db.get_user(player.telegram_id)
        new_balance = float(user_data["balance"]) if user_data else 0.0

        # 2. Finish round immediately
        room.phase = GamePhase.FINISHED

        # 3. Inform player of the lock
        await send(
            websocket,
            {
                "type": "card_locked",
                "card_id": target_cid,
                "locked_cards": list(player.locked_cards),
                "all_locked": True,
                "message": f"❌ ትክክለኛ ያልሆነ BINGO! መጫወቻ #{target_cid} ተቆልፏል።" if target_cid else "❌ ሁሉም መጫወቻዎች ተቆልፈዋል።",
            },
        )

        # 4. Broadcast winner/all-locked modal to show 10s countdown and continue button
        await broadcast(
            room,
            {
                "type": "winner",
                "all_locked": True,
                "is_single_player": True,
                "refund_amount": refund_amount,
                "balance": new_balance,
                "winners": [],
                "pot": room.pot,
                "called": room.called_numbers,
                "wait_seconds": int(WINNER_RESET_WAIT_SECONDS),
                "message": f"ሁሉም መጫወቻዎችዎ ተቆልፈዋል! ለብቻዎ ስለነበሩ የተከፈለው {refund_amount:.0f} ETB ሙሉ በሙሉ ተመልሷል።",
            },
        )

        # 5. Schedule reset for next round
        asyncio.create_task(_delayed_locked_reset(room.room_id))
        return

    # If not a single player or not all cards are locked:
    # Send card_locked to the caller
    await send(
        websocket,
        {
            "type": "card_locked",
            "card_id": target_cid,
            "locked_cards": list(player.locked_cards),
            "all_locked": all_locked,
            "message": (
                f"❌ ትክክለኛ ያልሆነ BINGO! መጫወቻ #{target_cid} ተቆልፏል። "
                f"{'ሁሉም መጫወቻዎችዎ ተቆልፈዋል — ጨዋታውን መከታተል ይችላሉ።' if all_locked else 'በቀሪው መጫወቻዎ መቀጠል ይችላሉ።'}"
                if target_cid
                else "❌ ትክክለኛ ያልሆነ BINGO! ሁሉም መጫወቻዎችዎ ተቆልፈዋል። ጨዋታውን መከታተል ይችላሉ።"
            ),
        },
    )

    # Broadcast to other players in the room
    await broadcast(
        room,
        {
            "type": "player_card_locked",
            "telegram_id": player.telegram_id,
            "name": player.name,
            "card_id": target_cid,
            "all_locked": all_locked,
        },
        exclude=ws_id,
    )

    # If multiple players and all active players are now forfeited/locked
    if all_locked and not is_single_player:
        active_unlocked_players = [
            p for p in room.players.values()
            if p.telegram_id in unique_players and not p.forfeited
        ]
        if len(active_unlocked_players) == 0:
            # Everyone in the room is locked!
            room.phase = GamePhase.FINISHED
            total_house_rev = round(len(room.taken_cards) * room.house_cut, 2)
            try:
                await db.record_house_revenue(
                    room_id=room.room_id,
                    cards_count=len(room.taken_cards),
                    cut_per_card=room.house_cut,
                    total_revenue=total_house_rev,
                )
            except Exception as e:
                logger.warning("Could not record house revenue: %s", e)

            await broadcast(
                room,
                {
                    "type": "winner",
                    "all_locked": True,
                    "is_single_player": False,
                    "refund_amount": 0.0,
                    "winners": [],
                    "pot": room.pot,
                    "called": room.called_numbers,
                    "wait_seconds": int(WINNER_RESET_WAIT_SECONDS),
                    "message": "ሁሉም መጫወቻዎች ተቆልፈዋል — በዚህ ዙር ምንም አሸናፊ አልተገኘም",
                },
            )
            asyncio.create_task(_delayed_locked_reset(room.room_id))


async def finalize_bingo(room_id: str) -> None:
    """Close the 5s claim window, split the pot among valid claimants, and reset."""
    try:
        await asyncio.sleep(BINGO_CLAIM_WINDOW_SECONDS)
        room = get_room(room_id)
        if room.phase != GamePhase.PLAYING or not room.bingo_claimants:
            return

        room.phase = GamePhase.FINISHED
        winners = room.bingo_claimants
        prize = room.pot
        share = round(prize / len(winners), 2)
        for w in winners:
            await db.credit_balance(
                w["telegram_id"],
                share,
                f"Won {room.name} with Card #{w['card_id']} — {w['pattern']}",
            )
            w["prize"] = share
            w["card"] = generate_card_by_id(w["card_id"])

        unique_players = set(room.taken_cards.values())
        if len(unique_players) > 1 and room.house_cut > 0 and room.taken_cards:
            total_house_rev = round(len(room.taken_cards) * room.house_cut, 2)
            try:
                await db.record_house_revenue(
                    room_id=room.room_id,
                    cards_count=len(room.taken_cards),
                    cut_per_card=room.house_cut,
                    total_revenue=total_house_rev,
                )
            except Exception as e:
                logger.warning("Could not record house revenue: %s", e)

        await broadcast(
            room,
            {
                "type": "winner",
                "winners": winners,
                "pot": prize,
                "called": room.called_numbers,
                "wait_seconds": int(WINNER_RESET_WAIT_SECONDS),
            },
        )

        await db.clear_active_round(room_id)
        await asyncio.sleep(round_reset_delay(get_room(room_id)))
        reset_room(room_id)
        await schedule_lobby(room_id)
        new_room = get_room(room_id)
        await broadcast(
            new_room,
            {
                "type": "round_reset",
                "room": get_room_state(new_room),
            },
        )
    except asyncio.CancelledError:
        pass


@app.websocket("/ws/{room_id}")
async def game_ws(websocket: WebSocket, room_id: str) -> None:
    await websocket.accept()
    ws_id = str(uuid.uuid4())
    connections[ws_id] = websocket
    player_ws[ws_id] = room_id

    if room_id not in ROOM_CONFIG:
        await send(websocket, {"type": "error", "message": "Unknown room."})
        await websocket.close()
        return

    room = get_room(room_id)
    room.connections.add(ws_id)

    telegram_id: int | None = None
    try:
        raw = await websocket.receive_text()
        data = json.loads(raw)

        if data.get("type") != "join":
            await send(websocket, {"type": "error", "message": "Send join message first."})
            await websocket.close()
            return

        try:
            user = validate_init_data(data.get("initData", ""))
        except ValueError as e:
            await send(websocket, {"type": "error", "message": str(e)})
            await websocket.close()
            return

        telegram_id = int(user["id"])
        user_ws.setdefault(telegram_id, set()).add(ws_id)
        display_name = user.get("first_name") or user.get("username") or str(telegram_id)

        user_db = await db.get_user(telegram_id)
        balance = float(user_db["balance"]) if user_db else 0.0

        # Check if this player is already registered with cards in this round
        player = None
        for p in room.players.values():
            if p.telegram_id == telegram_id:
                player = p
                break

        # If player disconnected before, reconstruct from room.taken_cards
        if not player and telegram_id and telegram_id in room.taken_cards.values():
            my_cids = [cid for cid, tid in room.taken_cards.items() if tid == telegram_id]
            if my_cids:
                player = Player(
                    telegram_id=telegram_id,
                    name=display_name,
                    ws_id=ws_id,
                    card_ids=my_cids,
                    cards={cid: generate_card_by_id(cid) for cid in my_cids},
                    marks={},
                )
                room.players[ws_id] = player

        if player:
            old_ws = player.ws_id
            player.ws_id = ws_id
            room.players[ws_id] = player
            if old_ws != ws_id:
                room.players.pop(old_ws, None)  # remove stale key
            is_player = len(player.card_ids) > 0
            user_card_ids = list(player.card_ids)
            user_cards = {str(cid): c for cid, c in player.cards.items()}
            user_marks = {str(cid): list(player.marks.get(cid, set())) for cid in player.card_ids}
            user_locked = list(player.locked_cards)
            user_forfeited = player.forfeited
        else:
            is_player = False
            user_card_ids = []
            user_cards = {}
            user_marks = {}
            user_locked = []
            user_forfeited = False

        # Send initial room state to the client (allows free spectating & shows taken cards!)
        await send(
            websocket,
            {
                "type": "init",
                "room": get_room_state(room),
                "user": {
                    "id": telegram_id,
                    "name": display_name,
                    "balance": balance,
                },
                "is_player": is_player,
                "card_ids": user_card_ids,
                "cards": user_cards,
                "marks": user_marks,
                "locked_cards": user_locked,
                "forfeited": user_forfeited,
            },
        )

        unique_players = len(set(p.telegram_id for p in room.players.values()))

        # Notify room of updated spectator / user count
        await broadcast(
            room,
            {
                "type": "room_stats",
                "players": len(room.taken_cards),
                "spectators": max(0, len(room.connections) - len(room.players)),
                "pot": room.pot,
                "countdown": room.countdown or room.lobby_seconds,
                "taken_cards": room.taken_cards,
            },
        )

        # Message loop
        while True:
            msg_raw = await websocket.receive_text()
            msg = json.loads(msg_raw)
            msg_type = msg.get("type")

            # Always re-fetch the current room object — reset_room() replaces
            # the GameRoom instance, so a stale reference would still show
            # phase=PLAYING after a round has already ended and a new lobby started.
            room = get_room(room_id)

            if msg_type == "ping":
                await send(websocket, {"type": "pong"})
                continue

            # User attempts to select a card by card_id (1-150 / 1-1500)
            if msg_type == "select_card":
                if room.phase != GamePhase.LOBBY:
                    await send(
                        websocket,
                        {
                            "type": "card_error",
                            "message": "ጨዋታው ተጀምሯል! ለቀጣዩ ዙር ይጠብቁ (Game is already in progress. Wait for next round).",
                        },
                    )
                    continue

                card_id = msg.get("card_id")
                if not isinstance(card_id, int) or card_id < 1 or card_id > room.max_cards:
                    await send(
                        websocket,
                        {
                            "type": "card_error",
                            "message": f"ትክክለኛ ያልሆነ መጫወቻ ምርጫ (1-{room.max_cards} ብቻ).",
                        },
                    )
                    continue

                # Cap at MAX_CARDS_PER_PLAYER per user
                existing_count = 0
                for p in room.players.values():
                    if p.telegram_id == telegram_id:
                        existing_count = len(p.card_ids)
                        break
                if existing_count >= MAX_CARDS_PER_PLAYER:
                    await send(
                        websocket,
                        {
                            "type": "card_error",
                            "message": (
                                f"ከፍተኛ {MAX_CARDS_PER_PLAYER} መጫወቻዎች ብቻ በአንድ ዙር "
                                "(Maximum 2 cards per round)."
                            ),
                        },
                    )
                    continue

                # Check if already picked by another player or by this player
                if card_id in room.taken_cards:
                    await send(
                        websocket,
                        {
                            "type": "card_taken_error",
                            "card_id": card_id,
                            "message": "ይህ መጫወቻ ተይዞአል",
                        },
                    )
                    continue

                # Balance validation (one card at a time)
                if FREE_PLAY:
                    new_balance = balance  # no deduction while testing
                else:
                    ok, new_balance = await db.deduct_balance(
                        telegram_id,
                        room.entry_fee,
                        f"{room.name} card #{card_id}",
                    )
                    if not ok:
                        user_data = await db.get_user(telegram_id)
                        current_balance = float(user_data["balance"]) if user_data else 0.0
                        await send(
                            websocket,
                            {
                                "type": "card_error",
                                "message": (
                                    f"❌ በቂ ሂሳብ የሎትም (Insufficient balance: {current_balance:.2f} ETB). "
                                    f"ይህንን መጫወቻ ለመምረጥ {room.entry_fee:.0f} ETB ያስፈልጋል።"
                                ),
                                "balance": current_balance,
                            },
                        )
                        continue

                # Assign card to player
                card = generate_card_by_id(card_id)
                room.taken_cards[card_id] = telegram_id

                player = room.players.get(ws_id)
                if not player:
                    for p in room.players.values():
                        if p.telegram_id == telegram_id:
                            player = p
                            break
                    if player:
                        old_ws = player.ws_id
                        player.ws_id = ws_id
                        room.players[ws_id] = player
                        room.players.pop(old_ws, None)
                    else:
                        player = Player(
                            telegram_id=telegram_id,
                            name=display_name,
                            ws_id=ws_id,
                            card_ids=[],
                            cards={},
                        )
                        room.players[ws_id] = player

                player.card_ids.append(card_id)
                player.cards[card_id] = card
                pot_contribution = room.entry_fee - room.house_cut
                room.pot += pot_contribution
                room.house_income += room.house_cut

                unique_players = len(set(p.telegram_id for p in room.players.values()))
                queue_room_snapshot(room_id)

                await send(
                    websocket,
                    {
                        "type": "card_confirmed",
                        "card_id": card_id,
                        "card": card,
                        "card_ids": player.card_ids,
                        "cards": {str(cid): c for cid, c in player.cards.items()},
                        "balance": new_balance,
                        "pot": room.pot,
                        "players": len(room.taken_cards),
                    },
                )

                await broadcast(
                    room,
                    {
                        "type": "card_taken",
                        "card_id": card_id,
                        "telegram_id": telegram_id,
                        "players": len(room.taken_cards),
                        "spectators": max(0, len(room.connections) - len(room.players)),
                        "pot": room.pot,
                        "name": display_name,
                    },
                )

                if room.phase == GamePhase.LOBBY and len(room.taken_cards) == 1:
                    await schedule_lobby(room_id)
                continue

            # User unselects a card during lobby countdown (full refund & card release)
            if msg_type == "unselect_card":
                if room.phase != GamePhase.LOBBY:
                    await send(
                        websocket,
                        {
                            "type": "card_error",
                            "message": "መጫወቻ መሰረዝ የሚቻለው በመጠባበቂያ ጊዜ (lobby) ብቻ ነው።",
                        },
                    )
                    continue

                card_id = msg.get("card_id")
                if not isinstance(card_id, int):
                    await send(
                        websocket,
                        {
                            "type": "card_error",
                            "message": "ትክክለኛ ያልሆነ መጫወቻ ምርጫ።",
                        },
                    )
                    continue

                if room.taken_cards.get(card_id) != telegram_id:
                    await send(
                        websocket,
                        {
                            "type": "card_error",
                            "message": "ይህ መጫወቻ የእርስዎ አይደለም።",
                        },
                    )
                    continue

                player = room.players.get(ws_id)
                if not player:
                    for p in room.players.values():
                        if p.telegram_id == telegram_id:
                            player = p
                            break

                if not player or card_id not in player.card_ids:
                    await send(
                        websocket,
                        {
                            "type": "card_error",
                            "message": "መጫወቻው በእርስዎ ስም አልተመዘገበም።",
                        },
                    )
                    continue

                # Remove card from player and room
                player.card_ids.remove(card_id)
                player.cards.pop(card_id, None)
                room.taken_cards.pop(card_id, None)

                # Deduct pot contribution and house income
                pot_reduction = room.entry_fee - room.house_cut
                room.pot = max(0.0, room.pot - pot_reduction)
                room.house_income = max(0.0, room.house_income - room.house_cut)

                # Refund player full entry fee
                if FREE_PLAY:
                    new_balance = balance
                else:
                    await db.credit_balance(
                        telegram_id,
                        room.entry_fee,
                        f"Refund — unselected {room.name} card #{card_id}",
                    )
                    user_data = await db.get_user(telegram_id)
                    new_balance = float(user_data["balance"]) if user_data else 0.0

                is_player = len(player.card_ids) > 0
                if not is_player:
                    room.players.pop(player.ws_id, None)

                await send(
                    websocket,
                    {
                        "type": "card_unselected",
                        "card_id": card_id,
                        "card_ids": player.card_ids if is_player else [],
                        "cards": {str(cid): c for cid, c in player.cards.items()} if is_player else {},
                        "is_player": is_player,
                        "balance": new_balance,
                        "pot": room.pot,
                        "players": len(room.taken_cards),
                        "refund_amount": room.entry_fee,
                    },
                )

                await broadcast(
                    room,
                    {
                        "type": "card_released",
                        "card_id": card_id,
                        "players": len(room.taken_cards),
                        "spectators": max(0, len(room.connections) - len(room.players)),
                        "pot": room.pot,
                    },
                    exclude=ws_id,
                )
                continue

            # User taps a cell on one of their cards (manual marking).
            # The system automatically declares BINGO when marks fulfill the rule.
            if msg_type == "mark":
                if room.phase != GamePhase.PLAYING:
                    await send(websocket, {"type": "mark_error", "message": "Game not active."})
                    continue

                player = room.players.get(ws_id)
                card_id = msg.get("card_id")
                row = msg.get("row")
                col = msg.get("col")
                desired = msg.get("marked")
                if (
                    not player
                    or not isinstance(card_id, int)
                    or not isinstance(row, int)
                    or not isinstance(col, int)
                    or not (0 <= row < 5 and 0 <= col < 5)
                    or not isinstance(desired, bool)
                ):
                    await send(websocket, {"type": "mark_error", "message": "Invalid mark payload."})
                    continue

                if card_id not in player.cards:
                    await send(websocket, {"type": "mark_error", "message": "You do not own this card."})
                    continue

                if card_id in player.locked_cards:
                    await send(websocket, {"type": "mark_error", "message": f"መጫወቻ #{card_id} ተቆልፏል (Card #{card_id} is locked)."})
                    continue

                flat = row * 5 + col
                if flat == FREE_INDEX:
                    continue  # the FREE center is always marked

                marks = player.marks.setdefault(card_id, set())
                if desired:
                    marks.add(flat)
                else:
                    marks.discard(flat)
                player.marks[card_id] = marks
                queue_room_snapshot(room_id)

                await send(
                    websocket,
                    {"type": "mark_ack", "card_id": card_id, "flat": flat, "marked": desired},
                )
                continue

            if msg_type == "bingo":
                if room.phase != GamePhase.PLAYING:
                    await send(
                        websocket,
                        {
                            "type": "bingo_result",
                            "valid": False,
                            "message": "Game not active.",
                        },
                    )
                    continue

                player = room.players.get(ws_id)
                if not player or not player.cards:
                    await send(
                        websocket,
                        {
                            "type": "bingo_result",
                            "valid": False,
                            "message": "Only active players with selected cards can call Bingo.",
                        },
                    )
                    continue

                target_cid = msg.get("card_id")
                # If specific card_id is claimed
                if target_cid is not None:
                    if target_cid not in player.cards:
                        await send(
                            websocket,
                            {
                                "type": "bingo_result",
                                "valid": False,
                                "message": "ይህ መጫወቻ የእርስዎ አይደለም። (You do not own this card).",
                            },
                        )
                        continue

                    if target_cid in player.locked_cards:
                        await send(
                            websocket,
                            {
                                "type": "bingo_result",
                                "valid": False,
                                "message": f"መጫወቻ #{target_cid} ቀድሞውኑ ተቆልፏል። (Card #{target_cid} is already locked).",
                            },
                        )
                        continue

                    win = find_winning_card(room, player, allow_unmarked=True, target_card_id=target_cid)
                    if not win:
                        # False BINGO: Lock this specific card!
                        player.locked_cards.add(target_cid)
                        queue_room_snapshot(room_id)
                        all_locked = len(player.locked_cards) >= len(player.card_ids)
                        if all_locked:
                            player.forfeited = True

                        await handle_cards_locked(room, player, websocket, ws_id, target_cid, all_locked)
                        continue

                    pattern, card_id = win
                    if add_claim(room, player, pattern, card_id):
                        claim_count = len(room.bingo_claimants)
                        await broadcast(
                            room,
                            {
                                "type": "bingo_claim",
                                "claimant_id": player.telegram_id,
                                "claimant_name": player.name,
                                "card_id": card_id,
                                "pattern": pattern,
                                "claimants": claim_count,
                                "window_seconds": int(BINGO_CLAIM_WINDOW_SECONDS),
                            },
                        )
                    continue
                else:
                    # General BINGO claim across all non-locked cards
                    win = find_winning_card(room, player, allow_unmarked=True)
                    if not win:
                        # Lock all unlocked cards
                        for cid in list(player.cards.keys()):
                            player.locked_cards.add(cid)
                        player.forfeited = True
                        queue_room_snapshot(room_id)

                        await handle_cards_locked(room, player, websocket, ws_id, None, True)
                        continue

                    pattern, card_id = win
                    if add_claim(room, player, pattern, card_id):
                        claim_count = len(room.bingo_claimants)
                        await broadcast(
                            room,
                            {
                                "type": "bingo_claim",
                                "claimant_id": player.telegram_id,
                                "claimant_name": player.name,
                                "card_id": card_id,
                                "pattern": pattern,
                                "claimants": claim_count,
                                "window_seconds": int(BINGO_CLAIM_WINDOW_SECONDS),
                            },
                        )
                    continue

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.exception("WebSocket error: %s", e)
    finally:
        room = get_room(room_id)
        room.connections.discard(ws_id)
        connections.pop(ws_id, None)
        player_ws.pop(ws_id, None)
        if telegram_id is not None and telegram_id in user_ws:
            user_ws[telegram_id].discard(ws_id)
            if not user_ws[telegram_id]:
                user_ws.pop(telegram_id, None)

        # Keep a player's selected cards and marks in every phase. A socket
        # closing is normal on mobile; it must never refund or release a seat.
        if ws_id in room.players:
            queue_room_snapshot(room_id)

        try:
            await websocket.close()
        except Exception:
            pass


def create_app() -> FastAPI:
    return app


# ---------------------------------------------------------------------------
# Telebirr payment notification webhook
# ---------------------------------------------------------------------------

async def _notify_telegram(chat_id: int, text: str) -> None:
    """Fire a Telegram message to a user (best-effort)."""
    import httpx as _httpx

    try:
        async with _httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{settings.bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            )
    except Exception as e:
        logger.warning("Could not send Telegram notification to %s: %s", chat_id, e)


@app.post("/telebirr/notify")
async def telebirr_notify(request: Request) -> JSONResponse:
    """
    Receive Telebirr's async payment notification (callback).
    Telebirr POSTs JSON here after a successful payment.
    We credit the user's balance and send them a Telegram message.
    """
    try:
        payload: dict = await request.json()
    except Exception:
        payload = dict(await request.form())

    logger.info("Telebirr notify received: %s", payload)

    # When a public key is configured, reject callbacks with an invalid RSA
    # signature. Without a configured key verification is skipped (dev mode).
    signature_ok = verify_callback_signature(payload)
    if signature_ok is False:
        logger.warning("Telebirr notify rejected — bad signature for order %s",
                       payload.get("merch_order_id", "?"))
        return JSONResponse({"code": "49401026001", "msg": "invalid signature"}, status_code=401)
    if signature_ok is None:
        logger.info("Telebirr callback signature verification skipped (no TELEBIRR_PUBLIC_KEY).")

    trade_status = payload.get("trade_status", "")
    merch_order_id = payload.get("merch_order_id", "")

    # Only process fully completed payments
    if trade_status != "Completed" or not merch_order_id:
        logger.info("Telebirr notify ignored — status=%s order=%s", trade_status, merch_order_id)
        return JSONResponse({"code": "0", "msg": "ignored"})

    credited, new_balance, telegram_id = await complete_telebirr_order(merch_order_id)

    if credited and telegram_id:
        order = await get_telebirr_order(merch_order_id)
        amount = order["amount"] if order else payload.get("total_amount", "?")
        # Send Telegram notification to the user via Bot API
        text = (
            f"✅ *ክፍያዎ ተቀብሏል!*\n\n"
            f"💰 *{amount} ETB* ወደ ሂሳብዎ ተጨምሯል።\n"
            f"💳 አዲስ ሂሳብ: *{new_balance:.2f} ETB*\n\n"
            f"እንኳን ደስ ያልዎ! EtooBingo ለመጫወት ዝግጁ ነዎት። 🎱"
        )
        await broadcast_user_balance(telegram_id, new_balance)
        await _notify_telegram(telegram_id, text)
    else:
        logger.info("Telebirr order %s already processed or not found.", merch_order_id)

    # Always return success to Telebirr so it stops retrying
    return JSONResponse({"code": "0", "msg": "success"})


async def _notify_telegram(telegram_id: int, message: str) -> bool:
    """Send a message to a telegram user using the bot token."""
    if not telegram_id or not settings.bot_token:
        return False
    url = f"https://api.telegram.org/bot{settings.bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                url,
                json={
                    "chat_id": telegram_id,
                    "text": message,
                    "parse_mode": "Markdown",
                },
            )
            return resp.status_code == 200
    except Exception as exc:
        logger.warning("Failed to send Telegram notification to %s: %s", telegram_id, exc)
        return False


# ---------------------------------------------------------------------------
# PeerPay payment webhook receiver
# ---------------------------------------------------------------------------
# https://peerpayment.org/docs/webhooks
# PeerPay POSTs signed JSON here for deposits and withdrawals. The signature
# is verified against the raw body BEFORE parsing, each event is deduplicated
# by PeerPay-Event-Id, and wallet transitions are applied idempotently.

_CREDIT_DEPOSIT_EVENTS = {"deposit.succeeded", "deposit.manually_succeeded"}
_TERMINAL_DEPOSIT_EVENTS = {
    "deposit.failed",
    "deposit.manually_failed",
    "deposit.expired",
    "deposit.cancelled",
    "deposit.review_required",
}
_CAPTURE_WITHDRAWAL_EVENTS = {"withdrawal.succeeded"}
_RELEASE_WITHDRAWAL_EVENTS = {"withdrawal.failed", "withdrawal.expired", "withdrawal.cancelled"}


def _as_amount(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


@app.api_route("/peerpay/webhook", methods=["GET", "POST", "HEAD", "OPTIONS", "PUT", "PATCH"])
@app.api_route("/peerpay/webhook/", methods=["GET", "POST", "HEAD", "OPTIONS", "PUT", "PATCH"])
@app.api_route("/webhook", methods=["GET", "POST", "HEAD", "OPTIONS", "PUT", "PATCH"])
@app.api_route("/webhook/", methods=["GET", "POST", "HEAD", "OPTIONS", "PUT", "PATCH"])
@app.api_route("/api/webhook", methods=["GET", "POST", "HEAD", "OPTIONS", "PUT", "PATCH"])
@app.api_route("/api/webhook/", methods=["GET", "POST", "HEAD", "OPTIONS", "PUT", "PATCH"])
@app.api_route("/api/payment/peerpay/webhook", methods=["GET", "POST", "HEAD", "OPTIONS", "PUT", "PATCH"])
@app.api_route("/api/payment/peerpay/webhook/", methods=["GET", "POST", "HEAD", "OPTIONS", "PUT", "PATCH"])
@app.api_route("/api/peerpay/webhook", methods=["GET", "POST", "HEAD", "OPTIONS", "PUT", "PATCH"])
@app.api_route("/api/peerpay/webhook/", methods=["GET", "POST", "HEAD", "OPTIONS", "PUT", "PATCH"])
@app.api_route("/peerpayment/webhook", methods=["GET", "POST", "HEAD", "OPTIONS", "PUT", "PATCH"])
@app.api_route("/peerpayment/webhook/", methods=["GET", "POST", "HEAD", "OPTIONS", "PUT", "PATCH"])
async def peerpay_webhook(request: Request) -> Response:
    """Receive PeerPay deposit/withdrawal notifications (at-least-once)."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return JSONResponse({"status": "ok", "message": "PeerPay webhook endpoint ready and active"}, status_code=200)

    raw = await request.body()

    event_id = (
        request.headers.get("PeerPay-Event-Id")
        or request.headers.get("X-PeerPay-Event-Id")
        or request.headers.get("X-Event-Id")
        or request.headers.get("Webhook-Id")
        or ""
    )
    event_type = (
        request.headers.get("PeerPay-Event")
        or request.headers.get("X-PeerPay-Event")
        or request.headers.get("X-Event")
        or request.headers.get("Webhook-Event")
        or ""
    )
    delivery_id = (
        request.headers.get("PeerPay-Delivery-Id")
        or request.headers.get("X-PeerPay-Delivery-Id")
        or request.headers.get("X-Delivery-Id")
        or ""
    )
    timestamp = (
        request.headers.get("PeerPay-Timestamp")
        or request.headers.get("X-PeerPay-Timestamp")
        or request.headers.get("X-Timestamp")
        or request.headers.get("Webhook-Timestamp")
        or ""
    )
    signature = (
        request.headers.get("PeerPay-Signature")
        or request.headers.get("X-PeerPay-Signature")
        or request.headers.get("X-Signature")
        or request.headers.get("Webhook-Signature")
        or ""
    )

    try:
        payload = json.loads(raw) if raw else {}
    except Exception:
        payload = {}

    if not event_type and isinstance(payload, dict):
        event_type = payload.get("event") or payload.get("type") or ""

    if (
        event_type in ("webhook.test", "test", "ping")
        or (isinstance(payload, dict) and payload.get("type") in ("webhook.test", "test", "ping"))
        or (isinstance(payload, dict) and payload.get("event") in ("webhook.test", "test", "ping"))
    ):
        logger.info("PeerPay webhook test received (delivery %s) — returning 200 OK", delivery_id)
        if event_id or delivery_id:
            await db.record_webhook_event_once(
                event_id or f"evt_test_{uuid.uuid4().hex[:8]}",
                delivery_id or f"whd_{uuid.uuid4().hex[:8]}",
                event_type or "webhook.test",
                "",
                raw.decode("utf-8", "ignore") if raw else "{}",
            )
        return Response(status_code=204)

    if not verify_peerpay_signature(
        settings.peerpay_webhook_secret,
        event_id,
        timestamp,
        raw,
        signature,
    ):
        logger.warning(
            "PeerPay webhook rejected — bad signature (delivery %s event %s sig %s)",
            delivery_id,
            event_id,
            signature[:15] if signature else "none",
        )
        return Response(status_code=401)

    obj = (payload.get("data") or {}).get("object") or {}
    object_id = obj.get("id", "")

    if event_id:
        inserted = await db.record_webhook_event_once(
            event_id, delivery_id, event_type, object_id, json.dumps(payload)
        )
        if not inserted:
            logger.info("PeerPay webhook duplicate event %s — already processed", event_id)
            return Response(status_code=204)

    await _apply_peerpay_event(payload)

    logger.info(
        "PeerPay webhook processed: event=%s delivery=%s object=%s",
        event_type,
        delivery_id,
        object_id,
    )
    return Response(status_code=204)


async def _apply_peerpay_event(payload: dict) -> None:
    """Dispatch a verified PeerPay event to the deposit/withdrawal handlers."""
    try:
        event_type = payload.get("type") or payload.get("event") or ""
        data_dict = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        obj = data_dict.get("object") if isinstance(data_dict.get("object"), dict) else (data_dict if data_dict.get("id") else payload)
        payment_id = obj.get("id", "")
        if not payment_id:
            logger.warning("PeerPay event %s without object.id — ignored", event_type)
            return

        if event_type.startswith("deposit."):
            await _apply_peerpay_deposit(event_type, obj)
        elif event_type.startswith("withdrawal."):
            await _apply_peerpay_withdrawal(event_type, obj)
        else:
            logger.info("Ignoring unknown PeerPay event type: %s", event_type)
    except Exception:
        logger.exception("PeerPay event processing failed")


async def _resolve_deposit_telegram_id(payment_id: str, merchant_customer_id):
    telegram_id = customer_id_to_telegram_id(merchant_customer_id)
    if telegram_id is not None:
        return telegram_id
    rec = await db.get_peerpay_deposit(payment_id)
    if rec and rec.get("telegram_id"):
        return rec["telegram_id"]
    try:
        dep_res = await peerpay_client.get_deposit(payment_id)
        if dep_res and dep_res.get("data"):
            cust_id = dep_res["data"].get("merchant_customer_id")
            return customer_id_to_telegram_id(cust_id)
    except Exception as exc:
        logger.warning("Could not resolve merchant_customer_id from PeerPay API: %s", exc)
    return None


async def _apply_peerpay_deposit(event_type: str, obj: dict) -> None:
    payment_id = obj.get("id", "")
    amount = _as_amount(obj.get("amount"))
    currency = obj.get("currency") or "ETB"
    merchant_order_id = obj.get("merchant_order_id")

    if amount <= 0:
        try:
            dep_res = await peerpay_client.get_deposit(payment_id)
            if dep_res and dep_res.get("data"):
                amount = _as_amount(dep_res["data"].get("amount"))
        except Exception as exc:
            logger.warning("Could not fetch deposit amount from PeerPay for %s: %s", payment_id, exc)

    if event_type in _CREDIT_DEPOSIT_EVENTS:
        telegram_id = await _resolve_deposit_telegram_id(
            payment_id, obj.get("merchant_customer_id")
        )
        if telegram_id is None:
            logger.warning(
                "PeerPay deposit %s — cannot resolve telegram user, not crediting",
                payment_id,
            )
            return

        credited, new_balance, is_dup = await db.credit_peerpay_deposit_once(
            payment_id, telegram_id, amount, merchant_order_id
        )
        if credited:
            logger.info(
                "PeerPay deposit %s credited %.2f ETB to user %s (new balance: %.2f)",
                payment_id,
                amount,
                telegram_id,
                new_balance,
            )
            await broadcast_user_balance(telegram_id, new_balance)
            await _notify_telegram(
                telegram_id,
                (
                    "✅ *ፔይመንት ተረጋግጧል (Payment Verified)!*\n\n"
                    f"💰 *{amount:.2f} ETB* ወደ ሂሳብዎ ተጨምሯል።\n"
                    f"💳 አዲስ ሂሳብ: *{new_balance:.2f} ETB*\n\n"
                    "እንኳን ደስ ያልዎ! መልካም እድል! 🎱"
                ),
            )
        elif is_dup:
            logger.info("PeerPay deposit %s — duplicate, already credited (balance: %.2f)", payment_id, new_balance)
            await broadcast_user_balance(telegram_id, new_balance)
        return

    # Status bookkeeping only — never credit on non-terminal events.
    telegram_id = await _resolve_deposit_telegram_id(
        payment_id, obj.get("merchant_customer_id")
    )
    if telegram_id is not None:
        await db.upsert_peerpay_deposit(
            payment_id,
            telegram_id,
            amount,
            currency,
            merchant_order_id,
            event_type.replace("deposit.", ""),
        )
    elif event_type in _TERMINAL_DEPOSIT_EVENTS:
        logger.info(
            "PeerPay deposit %s terminal event without user mapping — "
            "reconciliation via GET /v1/deposits/:id needed",
            payment_id,
        )


async def _apply_peerpay_withdrawal(event_type: str, obj: dict) -> None:
    payment_id = obj["id"]
    telegram_id = customer_id_to_telegram_id(obj.get("merchant_customer_id"))
    amount = _as_amount(obj.get("amount"))
    verification = obj.get("verification") or {}
    decision_code = verification.get("decision_code")

    if event_type == "withdrawal.created":
        await db.create_peerpay_withdrawal_hold(
            payment_id, telegram_id or 0, amount, status=obj.get("status", "created")
        )
        return

    if telegram_id is None:
        rec = await db.get_peerpay_withdrawal(payment_id)
        telegram_id = rec["telegram_id"] if rec else None

    if event_type in _CAPTURE_WITHDRAWAL_EVENTS:
        captured, target_id = await db.capture_peerpay_withdrawal_once(payment_id)
        if captured:
            logger.info("PeerPay withdrawal %s captured for user %s", payment_id, target_id)
            user_data = await db.get_user(target_id)
            if user_data:
                await broadcast_user_balance(target_id, float(user_data["balance"]))
            await _notify_telegram(
                target_id,
                (
                    "✅ *መውጣት (Withdrawal) ተጠናቀቀ!*\n\n"
                    f"💰 *{amount:.2f} ETB* መልቀቃችው ተረጋግጧል።\n\n"
                    "ቶሎ ገንዘብዎን ይመልከቱ።"
                ),
            )
        else:
            logger.info("PeerPay withdrawal %s — already captured/released", payment_id)
        return

    if event_type in _RELEASE_WITHDRAWAL_EVENTS:
        released, new_balance = await db.release_peerpay_withdrawal_once(payment_id)
        if released:
            logger.info(
                "PeerPay withdrawal %s released (refunded %.2f ETB)",
                payment_id,
                amount,
            )
            if telegram_id:
                await broadcast_user_balance(telegram_id, new_balance)
            await _notify_telegram(
                telegram_id or 0,
                (
                    "↩️ *መውጣት አልተሳካም — ገንዘብ ተመልሷል*\n\n"
                    f"💰 *{amount:.2f} ETB* ወደ ሂሳብዎ ተመልሷል።\n"
                    f"💳 አዲስ ሂሳብ: *{new_balance:.2f} ETB*"
                ),
            )
        return

    # Hold stays unchanged for every other withdrawal event.
    await db.update_peerpay_withdrawal_progress(
        payment_id,
        obj.get("status", event_type),
        verification.get("status"),
        decision_code,
    )
    logger.info(
        "PeerPay withdrawal %s progress updated (event=%s)", payment_id, event_type
    )


peerpay_client = PeerPayClient()


@app.get("/api/user/me")
async def api_user_me(request: Request):
    """Fetch user balance and info for Mini App."""
    init_data = request.headers.get("X-Telegram-Init-Data") or request.query_params.get("init_data")
    if not init_data:
        return JSONResponse(status_code=401, content={"error": "Missing init_data"})
    try:
        user_info = validate_init_data(init_data)
        tg_id = int(user_info["id"])
        user = await db.get_user(tg_id)
        balance = float(user["balance"]) if user else 0.0
        return JSONResponse({"ok": True, "telegram_id": tg_id, "balance": balance, "registered": user is not None})
    except Exception as exc:
        return JSONResponse(status_code=401, content={"error": str(exc)})


@app.post("/api/deposit/create")
async def api_deposit_create(request: Request):
    """Create a verified PeerPay deposit checkout link for the Mini App."""
    init_data = request.headers.get("X-Telegram-Init-Data")
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    init_data = init_data or body.get("init_data")
    if not init_data:
        return JSONResponse(status_code=401, content={"error": "Missing init_data"})
    try:
        user_info = validate_init_data(init_data)
        tg_id = int(user_info["id"])
    except Exception as exc:
        return JSONResponse(status_code=401, content={"error": str(exc)})

    amount_raw = body.get("amount")
    amount = None
    if amount_raw is not None:
        try:
            amount = float(amount_raw)
        except (ValueError, TypeError):
            pass

    payment_method = str(body.get("payment_method", "telebirr")).lower()
    peerpay_method = "telebirr" if payment_method == "telebirr" else ("cbebirr" if payment_method == "cbebirr" else "cbe")
    idempotency_key = f"etoobingo-deposit-{uuid.uuid4().hex}"

    try:
        res = await peerpay_client.create_deposit(
            merchant_customer_id=f"tg_{tg_id}",
            amount=amount,
            payment_method=peerpay_method,
            idempotency_key=idempotency_key,
        )
        dep_data = res.get("data", {})
        deposit_id = dep_data.get("id")
        checkout_url = dep_data.get("checkout_url")
        if deposit_id:
            await db.upsert_peerpay_deposit(
                payment_id=deposit_id,
                telegram_id=tg_id,
                amount=float(dep_data.get("amount") or amount or 0.0),
                currency="ETB",
                merchant_order_id=dep_data.get("merchant_order_id"),
                status=dep_data.get("status", "awaiting_transfer"),
            )
        return JSONResponse({
            "ok": True,
            "deposit_id": deposit_id,
            "checkout_url": checkout_url,
            "amount": dep_data.get("amount") or amount,
            "data": dep_data,
        })
    except Exception as exc:
        logger.exception("Failed to create PeerPay deposit: %s", exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.get("/api/deposit/status/{deposit_id}")
async def api_deposit_status(deposit_id: str, request: Request):
    """Poll authoritative deposit status from PeerPay and credit on success."""
    init_data = request.headers.get("X-Telegram-Init-Data") or request.query_params.get("init_data")
    if not init_data:
        return JSONResponse(status_code=401, content={"error": "Missing init_data"})
    try:
        user_info = validate_init_data(init_data)
        tg_id = int(user_info["id"])
    except Exception as exc:
        return JSONResponse(status_code=401, content={"error": str(exc)})

    try:
        res = await peerpay_client.get_deposit(deposit_id)
        dep_data = res.get("data", {})
        status = dep_data.get("status", "unknown")
        amount = float(dep_data.get("amount") or 0.0)

        if status == "succeeded":
            credited, new_balance, is_dup = await db.credit_peerpay_deposit_once(
                payment_id=deposit_id,
                telegram_id=tg_id,
                amount=amount,
                merchant_order_id=dep_data.get("merchant_order_id"),
            )
            if credited:
                await _notify_telegram(
                    tg_id,
                    (
                        "✅ *ፔይመንት ተረጋግጧል (Payment Verified)!*\n\n"
                        f"💰 *{amount:.2f} ETB* ወደ ሂሳብዎ ተጨምሯል።\n"
                        f"💳 አዲስ ሂሳብ: *{new_balance:.2f} ETB*\n\n"
                        "እንኳን ደስ ያልዎ! መልካም እድል! 🎱"
                    ),
                )
            await broadcast_user_balance(tg_id, new_balance)
            return JSONResponse({
                "ok": True,
                "status": "succeeded",
                "credited": credited or is_dup,
                "amount": amount,
                "new_balance": new_balance,
                "message": f"✅ {amount:.2f} ETB ወደ ሂሳብዎ ተጨምሯል!",
            })

        return JSONResponse({
            "ok": True,
            "status": status,
            "data": dep_data,
        })
    except Exception as exc:
        logger.exception("Failed to check deposit status: %s", exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.post("/api/deposit/submit-reference")
async def api_deposit_submit_reference(request: Request):
    """Submit payment reference to PeerPay Checkout API and verify with bank."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    init_data = request.headers.get("X-Telegram-Init-Data") or body.get("init_data")
    if not init_data:
        return JSONResponse(status_code=401, content={"error": "Missing init_data"})
    try:
        tg_id = int(validate_init_data(init_data)["id"])
    except Exception as exc:
        return JSONResponse(status_code=401, content={"error": str(exc)})

    raw_input = str(body.get("reference", "")).strip()
    if not raw_input:
        return JSONResponse(
            status_code=400,
            content={"error": "እባክዎ የደረሰኝ ሊንክ ወይም Transaction ID ያስገቡ።"},
        )

    # Extract bare reference token
    ref_match = re.search(r"\b(DI[A-Z0-9]{8}|FT[0-9A-Z]{8,16})\b", raw_input, re.I)
    ref = ref_match.group(1) if ref_match else raw_input.split("/")[-1].strip()

    # Check anti-duplicate locally
    existing_dep = await db.get_peerpay_deposit(ref)
    if existing_dep and existing_dep.get("credited"):
        return JSONResponse(
            status_code=409,
            content={"error": "ይህ ግብይት ቀድሞውኑ ተረጋግጦ ጥቅም ላይ ውሏል!"},
        )

    amount_raw = body.get("amount")
    amount = None
    if amount_raw is not None:
        try:
            amount = float(str(amount_raw).replace(",", ".").strip())
        except (ValueError, TypeError):
            pass

    if (amount is None or amount < 10.0) and raw_input:
        parsed_sms = parse_deposit_sms(raw_input)
        if parsed_sms.is_valid and parsed_sms.amount:
            amount = float(parsed_sms.amount)

    if amount is None or amount < 10.0:
        return JSONResponse(
            status_code=400,
            content={"error": "እባክዎ ያስተላለፉትን ትክክለኛ የብር መጠን ያስገቡ (ዝቅተኛ: 10 ETB)።"},
        )

    deposit_id = str(body.get("deposit_id", "")).strip()
    checkout_url = str(body.get("checkout_url", "")).strip()
    payment_method = str(body.get("payment_method", "telebirr")).lower()

    # If no active deposit session, create one with PeerPay matching the exact amount
    if not deposit_id or not checkout_url:
        peerpay_method = "telebirr" if ref.startswith("DI") else ("cbebirr" if payment_method == "cbebirr" else "cbe")
        try:
            create_res = await peerpay_client.create_deposit(
                merchant_customer_id=f"tg_{tg_id}",
                amount=amount,
                payment_method=peerpay_method,
            )
            dep_data = create_res.get("data", {})
            deposit_id = dep_data.get("id", "")
            checkout_url = dep_data.get("checkout_url", "")
            if deposit_id:
                await db.upsert_peerpay_deposit(
                    payment_id=deposit_id,
                    telegram_id=tg_id,
                    amount=amount,
                    currency="ETB",
                    merchant_order_id=dep_data.get("merchant_order_id"),
                    status=dep_data.get("status", "created"),
                )
        except Exception as exc:
            logger.warning("Error creating deposit for reference submission: %s", exc)

    if not deposit_id or not checkout_url:
        return JSONResponse(status_code=400, content={"error": "የክፍያ ማስፈንጠሪያ ማዘጋጀት አልተቻለም።"})

    try:
        verify_res = await peerpay_client.submit_and_verify_reference(
            deposit_id=deposit_id,
            checkout_url=checkout_url,
            reference=ref,
            payment_method=payment_method,
        )

        if not verify_res.get("ok"):
            err_msg = verify_res.get("error", "ይህ ግብይት አልተረጋገጠም ወይም የተሳሳተ ነው!")
            return JSONResponse(status_code=400, content={"error": err_msg})

        status = verify_res.get("status")
        amount = verify_res.get("amount") or 0.0

        if status == "succeeded":
            credited, new_balance, is_dup = await db.credit_peerpay_deposit_once(
                payment_id=deposit_id,
                telegram_id=tg_id,
                amount=amount,
            )
            if credited:
                await _notify_telegram(
                    tg_id,
                    (
                        "✅ *ፔይመንት ተረጋግጧል (Payment Verified)!*\n\n"
                        f"💰 *{amount:.2f} ETB* ወደ ሂሳብዎ ተጨምሯል።\n"
                        f"💳 አዲስ ሂሳብ: *{new_balance:.2f} ETB*\n\n"
                        "እንኳን ደስ ያልዎ! መልካም እድል! 🎱"
                    ),
                )
            await broadcast_user_balance(tg_id, new_balance)
            return JSONResponse({
                "ok": True,
                "status": "credited",
                "amount": amount,
                "new_balance": new_balance,
                "reference": ref,
                "message": f"✅ {amount:.2f} ETB ወደ ሂሳብዎ ተጨምሯል!",
                "data": {
                    "status": "succeeded",
                    "amount": amount,
                    "new_balance": new_balance,
                    "reference": ref,
                },
            })

        return JSONResponse({
            "ok": True,
            "status": "verification_pending",
            "deposit_id": deposit_id,
            "reference": ref,
            "message": "⏳ ክፍያው በ PeerPayment በኩል በማረጋገጥ ላይ ነው። ማረጋገጫው እንደተጠናቀቀ በራስ-ሰር ይጨመርልዎታል!",
        })
    except Exception as exc:
        logger.exception("Error submitting reference to PeerPay: %s", exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})



@app.post("/api/withdraw/create")
async def api_withdraw_create(request: Request):
    """Request a withdrawal from the Mini App."""
    init_data = request.headers.get("X-Telegram-Init-Data")
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    init_data = init_data or body.get("init_data")
    if not init_data:
        return JSONResponse(status_code=401, content={"error": "Missing init_data"})
    try:
        user_info = validate_init_data(init_data)
        tg_id = int(user_info["id"])
    except Exception as exc:
        return JSONResponse(status_code=401, content={"error": str(exc)})

    try:
        amount = float(body.get("amount", 0))
    except (TypeError, ValueError):
        return JSONResponse(status_code=400, content={"error": "Invalid amount"})

    if amount < 10.0:
        return JSONResponse(status_code=400, content={"error": "Minimum withdrawal is 10 ETB"})

    user = await db.get_user(tg_id)
    if not user:
        return JSONResponse(status_code=400, content={"error": "User not registered"})

    balance = float(user["balance"])
    if balance < amount:
        return JSONResponse(status_code=400, content={"error": "Insufficient balance"})

    destination = body.get("destination") or {}
    bank_raw = str(destination.get("bank", "telebirr")).lower()
    peerpay_bank_code = "telebirr" if bank_raw == "telebirr" else ("cbebirr" if bank_raw == "cbebirr" else "cbe")
    account_raw = str(destination.get("account_number", "")).strip()

    clean_account = None
    if peerpay_bank_code in ("telebirr", "cbebirr"):
        digits = re.sub(r"\D", "", account_raw)
        if digits.startswith("251") and len(digits) == 12:
            digits = "0" + digits[3:]
        elif digits.startswith("9") and len(digits) == 9:
            digits = "0" + digits
        elif digits.startswith("7") and len(digits) == 9:
            digits = "0" + digits

        if not (len(digits) == 10 and (digits.startswith("09") or digits.startswith("07"))):
            method_name = "Telebirr" if peerpay_bank_code == "telebirr" else "CBE Birr"
            return JSONResponse(
                status_code=400,
                content={"error": f"Invalid {method_name} phone number. Please provide a 10-digit number (e.g. 0911223344)."},
            )
        clean_account = digits
    else:  # cbe bank
        digits = re.sub(r"\D", "", account_raw)
        if not (10 <= len(digits) <= 16):
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid CBE Bank account number. Please provide a valid 10-16 digit account number (e.g. 1000123456789)."},
            )
        clean_account = digits

    clean_dest = {"bank": peerpay_bank_code, "account_number": clean_account}
    payment_id = f"wd_{uuid.uuid4().hex[:12]}"
    idempotency_key = f"etoobingo-withdrawal-{uuid.uuid4().hex}"
    checkout_url = ""

    try:
        res = await peerpay_client.create_withdrawal(
            merchant_customer_id=f"tg_{tg_id}",
            amount=amount,
            destination=clean_dest,
            idempotency_key=idempotency_key,
        )
        wd_data = res.get("data", {})
        if wd_data.get("id"):
            payment_id = wd_data["id"]
        checkout_url = wd_data.get("checkout_url") or ""
        if checkout_url:
            try:
                await peerpay_client.confirm_withdrawal_destination(
                    checkout_token_or_url=checkout_url,
                    bank=peerpay_bank_code,
                    account_number=clean_account,
                )
                logger.info("Auto-confirmed withdrawal %s destination on checkout API", payment_id)
            except Exception as conf_err:
                logger.warning("Auto-confirm destination in API error: %s", conf_err)
    except Exception as exc:
        logger.exception("Failed to create PeerPay withdrawal: %s", exc)

    new_balance = round(balance - amount, 2)
    await db.update_balance(tg_id, new_balance)
    await db.create_peerpay_withdrawal_hold(
        payment_id=payment_id,
        telegram_id=tg_id,
        amount=amount,
        status="created",
    )
    await db.add_transaction(
        telegram_id=tg_id,
        tx_type="withdraw",
        amount=amount,
        status="pending",
        description=f"PeerPay withdrawal hold — {payment_id} ({peerpay_bank_code}: {clean_account})",
    )

    # Push updated balance to open WebSocket sessions immediately
    await broadcast_user_balance(tg_id, new_balance)

    return JSONResponse({
        "ok": True,
        "withdrawal_id": payment_id,
        "checkout_url": checkout_url,
        "amount": amount,
        "new_balance": new_balance,
        "method": peerpay_bank_code,
        "account_number": clean_account,
    })


@app.get("/api/withdraw/status/{withdrawal_id}")
async def api_withdraw_status(withdrawal_id: str):
    """Authoritatively poll PeerPay withdrawal status and reconcile hold/capture/release."""
    try:
        res = await peerpay_client.get_withdrawal(withdrawal_id)
        wd_data = res.get("data", {})
        status = wd_data.get("status", "unknown")
        amount = float(wd_data.get("amount") or 0.0)

        if status == "succeeded":
            captured, tg_id = await db.capture_peerpay_withdrawal_once(withdrawal_id)
            if captured and tg_id:
                user_data = await db.get_user(tg_id)
                if user_data:
                    await broadcast_user_balance(tg_id, float(user_data["balance"]))
            return JSONResponse({
                "ok": True,
                "status": "succeeded",
                "captured": captured,
                "amount": amount,
                "message": f"✅ {amount:.2f} ETB ወደ Telebirr ቁጥርዎ በተሳካ ሁኔታ ተላልፏል!",
            })
        elif status in ("failed", "expired", "cancelled"):
            released, refund_amt = await db.release_peerpay_withdrawal_once(withdrawal_id)
            if released:
                rec = await db.get_peerpay_withdrawal(withdrawal_id)
                if rec and rec.get("telegram_id"):
                    user_data = await db.get_user(rec["telegram_id"])
                    if user_data:
                        await broadcast_user_balance(rec["telegram_id"], float(user_data["balance"]))
            return JSONResponse({
                "ok": True,
                "status": status,
                "released": released,
                "refund_amount": refund_amt,
                "message": f"❌ ክፍያው አልተሳካም ({status})። {refund_amt:.2f} ETB ወደ ሂሳብዎ ተመልሷል።",
            })

        return JSONResponse({
            "ok": True,
            "status": status,
            "data": wd_data,
        })
    except Exception as exc:
        logger.exception("Failed to check withdrawal status: %s", exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.get("/deposits/return")
@app.get("/deposits/return/")
@app.get("/withdrawals/return")
@app.get("/withdrawals/return/")
async def return_page():
    from fastapi.responses import FileResponse
    return FileResponse(os.path.join(WEBAPP_DIR, "index.html"))


@app.get("/healthz")
async def healthz():
    """Liveness for Fly/GitHub/monitoring. Must stay registered before StaticFiles."""
    db_ok = False
    db_error = None
    try:
        db_ok = await db.ping_db()
    except Exception as exc:
        db_error = str(exc)

    webapp_url = (settings.webapp_url or "").rstrip("/")
    reachable = None
    check_webapp = os.getenv("HEALTHZ_CHECK_WEBAPP", "").lower() in ("1", "true", "yes")
    if check_webapp and webapp_url and not webapp_url.startswith("https://example.com"):
        try:
            import httpx

            async with httpx.AsyncClient(timeout=2.0, follow_redirects=True) as client:
                response = await client.get(webapp_url)
                reachable = 200 <= response.status_code < 500
        except Exception:
            reachable = False

    healthy = db_ok
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if healthy else "degraded",
            "server": "ok",
            "database": "ok" if db_ok else "error",
            "database_error": db_error,
            "webapp_url": webapp_url or None,
            "webapp_reachable": reachable,
        },
    )


app.mount("/", StaticFiles(directory=WEBAPP_DIR, html=True), name="webapp")
