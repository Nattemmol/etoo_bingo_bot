import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

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
    fetch_telebirr_receipt,
    fingerprint,
    parse_deposit_sms,
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

# FREE PLAY TESTING: bypass entry-fee balance checks and deductions so the
# playing room can be tested without real money. Set to False to enforce
# payments again (balance must cover each card's fee, deducted one at a time).
FREE_PLAY = False

rooms: dict[str, GameRoom] = {}
room_tasks: dict[str, asyncio.Task] = {}
connections: dict[str, WebSocket] = {}  # ws_id -> websocket
player_ws: dict[str, str] = {}  # ws_id -> room_id


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    logger.info("Game server database ready.")
    yield


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
        cfg = ROOM_CONFIG.get(room_id, {"name": room_id, "entry_fee": 10.0, "house_cut": 2.0, "max_cards": 150, "lobby_seconds": 30})
        rooms[room_id] = GameRoom(
            room_id=room_id,
            name=cfg["name"],
            entry_fee=cfg["entry_fee"],
            house_cut=cfg.get("house_cut", 0.0),
            max_cards=cfg.get("max_cards", 150),
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
    dead = []
    # Broadcast to all connected clients in the room (both active players and spectators)
    for ws_id in list(room.connections):
        if ws_id == exclude:
            continue
        ws = connections.get(ws_id)
        if ws:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws_id)
        else:
            dead.append(ws_id)

    for ws_id in dead:
        room.connections.discard(ws_id)
        room.players.pop(ws_id, None)
        connections.pop(ws_id, None)
        player_ws.pop(ws_id, None)


async def send(ws: WebSocket, message: dict) -> None:
    await ws.send_json(message)


async def run_lobby_countdown(room: GameRoom) -> None:
    if room.room_id == "room_super_50" and not settings.super_bingo_always_open:
        while room.phase == GamePhase.LOBBY:
            secs = get_seconds_until_super_bingo()
            room.countdown = secs
            await broadcast(
                room,
                {
                    "type": "lobby",
                    "players": len(room.taken_cards),
                    "countdown": room.countdown,
                    "pot": room.pot,
                },
            )
            if secs <= 0:
                break
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
        await broadcast(
            room,
            {
                "type": "start",
                "pot": room.pot,
                "players": len(room.taken_cards),
            },
        )
    else:
        room.countdown = room.lobby_seconds
        while room.countdown > 0 and room.phase == GamePhase.LOBBY:
            await broadcast(
                room,
                {
                    "type": "lobby",
                    "players": len(room.taken_cards),
                    "countdown": room.countdown,
                    "pot": room.pot,
                },
            )
            await asyncio.sleep(1)
            room.countdown -= 1

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
        await broadcast(
            room,
            {
                "type": "start",
                "pot": room.pot,
                "players": len(room.taken_cards),
            },
        )

    while room.phase == GamePhase.PLAYING:
        await asyncio.sleep(room.call_interval)
        if room.phase != GamePhase.PLAYING or room.bingo_window_until is not None:
            break  # a valid BINGO has been claimed; freeze the board for the 5s window

        num = room.next_number()
        if num is None:
            room.phase = GamePhase.FINISHED
            await broadcast(room, {"type": "game_over", "reason": "all_numbers_called"})
            await asyncio.sleep(10)
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

        # Auto-declare BINGO for any player whose manual marks now win.
        await check_all_wins(room)


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
    if room_id in room_tasks:
        room_tasks[room_id].cancel()
        room_tasks.pop(room_id, None)

    cfg = ROOM_CONFIG.get(room_id, {"name": room_id, "entry_fee": 10.0, "house_cut": 2.0, "max_cards": 150, "lobby_seconds": 30})
    existing_conns = set(rooms[room_id].connections) if room_id in rooms else set()
    rooms[room_id] = GameRoom(
        room_id=room_id,
        name=cfg["name"],
        entry_fee=cfg["entry_fee"],
        house_cut=cfg.get("house_cut", 0.0),
        max_cards=cfg.get("max_cards", 150),
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


async def check_all_wins(room: GameRoom) -> None:
    """Auto-declare BINGO for every player whose manual marks now satisfy the rule."""
    if room.phase != GamePhase.PLAYING:
        return
    if room.bingo_window_until is not None and time.monotonic() > room.bingo_window_until:
        return

    for player in list(room.players.values()):
        if not player.cards:
            continue
        win = find_winning_card(room, player, allow_unmarked=False)
        if not win:
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


async def _delayed_locked_reset(room_id: str) -> None:
    try:
        await asyncio.sleep(WINNER_RESET_WAIT_SECONDS)
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

        await asyncio.sleep(WINNER_RESET_WAIT_SECONDS)
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

        telegram_id = user["id"]
        display_name = user.get("first_name") or user.get("username") or str(telegram_id)

        user_db = await db.get_user(telegram_id)
        balance = float(user_db["balance"]) if user_db else 0.0

        # Check if this player is already registered with cards in this round
        player = None
        for p in room.players.values():
            if p.telegram_id == telegram_id:
                player = p
                break

        if player:
            old_ws = player.ws_id
            player.ws_id = ws_id
            room.players[ws_id] = player
            room.players.pop(old_ws, None)  # remove stale key so old socket disconnect won't double-refund
            is_player = len(player.card_ids) > 0
            user_card_ids = list(player.card_ids)
            user_cards = {str(cid): c for cid, c in player.cards.items()}
        else:
            is_player = False
            user_card_ids = []
            user_cards = {}

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

                await send(
                    websocket,
                    {"type": "mark_ack", "card_id": card_id, "flat": flat, "marked": desired},
                )

                await check_all_wins(room)
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

        if ws_id in room.players:
            if room_id != "room_super_50":
                # For 24/7 fast 30s lobby, refund entry fees if user disconnects before game begins
                player = room.players.pop(ws_id)
                if room.phase == GamePhase.LOBBY and player.card_ids:
                    num_cards = len(player.card_ids)
                    refund_amount = room.entry_fee * num_cards
                    pot_reduction = (room.entry_fee - room.house_cut) * num_cards
                    room.pot = max(0.0, room.pot - pot_reduction)
                    room.house_income = max(0.0, room.house_income - room.house_cut * num_cards)
                    if not FREE_PLAY:
                        await db.credit_balance(
                            player.telegram_id,
                            refund_amount,
                            f"Refund — left {room.name} lobby",
                        )
                    for cid in player.card_ids:
                        room.taken_cards.pop(cid, None)

                    await broadcast(
                        room,
                        {
                            "type": "room_stats",
                            "players": len(room.taken_cards),
                            "spectators": max(0, len(room.connections) - len(room.players)),
                            "pot": room.pot,
                            "taken_cards": room.taken_cards,
                        },
                    )

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
        await _notify_telegram(telegram_id, text)
    else:
        logger.info("Telebirr order %s already processed or not found.", merch_order_id)

    # Always return success to Telebirr so it stops retrying
    return JSONResponse({"code": "0", "msg": "success"})


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


@app.api_route("/peerpay/webhook", methods=["GET", "POST", "HEAD", "OPTIONS", "PUT"])
@app.api_route("/peerpay/webhook/", methods=["GET", "POST", "HEAD", "OPTIONS", "PUT"])
@app.api_route("/api/payment/peerpay/webhook", methods=["GET", "POST", "HEAD", "OPTIONS", "PUT"])
@app.api_route("/api/payment/peerpay/webhook/", methods=["GET", "POST", "HEAD", "OPTIONS", "PUT"])
@app.api_route("/api/peerpay/webhook", methods=["GET", "POST", "HEAD", "OPTIONS", "PUT"])
@app.api_route("/api/peerpay/webhook/", methods=["GET", "POST", "HEAD", "OPTIONS", "PUT"])
async def peerpay_webhook(request: Request) -> Response:
    """Receive PeerPay deposit/withdrawal notifications (at-least-once)."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return JSONResponse({"status": "ok", "message": "PeerPay webhook endpoint ready"}, status_code=200)

    raw = await request.body()

    event_id = request.headers.get("PeerPay-Event-Id", "")
    event_type = request.headers.get("PeerPay-Event", "")
    delivery_id = request.headers.get("PeerPay-Delivery-Id", "")
    timestamp = request.headers.get("PeerPay-Timestamp", "")
    signature = request.headers.get("PeerPay-Signature", "")

    try:
        payload = json.loads(raw) if raw else {}
    except Exception:
        payload = {}

    if not event_type and isinstance(payload, dict):
        event_type = payload.get("event") or payload.get("type") or ""

    if event_type == "webhook.test" or (isinstance(payload, dict) and payload.get("type") == "webhook.test"):
        logger.info("PeerPay webhook.test received (delivery %s) — returning 200 to activate endpoint", delivery_id)
        await db.record_webhook_event_once(
            event_id or "evt_test",
            delivery_id or f"whd_{event_id}",
            event_type or "webhook.test",
            "",
            raw.decode("utf-8", "ignore") if raw else "{}",
        )
        return Response(status_code=200)

    if not verify_peerpay_signature(
        settings.peerpay_webhook_secret,
        event_id,
        timestamp,
        raw,
        signature,
    ):
        logger.warning(
            "PeerPay webhook rejected — bad signature (delivery %s event %s)",
            delivery_id,
            event_id,
        )
        return Response(status_code=401)

    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {}

    obj = (payload.get("data") or {}).get("object") or {}
    object_id = obj.get("id", "")

    inserted = await db.record_webhook_event_once(
        event_id, delivery_id, event_type, object_id, json.dumps(payload)
    )
    if not inserted:
        logger.info("PeerPay webhook duplicate event %s — already processed", event_id)
        return Response(status_code=204)

    if event_type == "webhook.test":
        logger.info("PeerPay webhook.test received — endpoint active")
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
        event_type = payload.get("type", "")
        obj = (payload.get("data") or {}).get("object") or {}
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
    return rec["telegram_id"] if rec else None


async def _apply_peerpay_deposit(event_type: str, obj: dict) -> None:
    payment_id = obj["id"]
    amount = _as_amount(obj.get("amount"))
    currency = obj.get("currency") or "ETB"
    merchant_order_id = obj.get("merchant_order_id")

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
                "PeerPay deposit %s credited %.2f ETB to user %s",
                payment_id,
                amount,
                telegram_id,
            )
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
            logger.info("PeerPay deposit %s — duplicate, already credited", payment_id)
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
    """Create a PeerPay deposit checkout link from the Mini App."""
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

    amount = body.get("amount")
    payment_method = body.get("payment_method")
    try:
        res = await peerpay_client.create_deposit(
            merchant_customer_id=f"tg_{tg_id}",
            amount=amount,
            payment_method=payment_method,
            return_url=f"{settings.webapp_url}/deposits/return",
        )
        return JSONResponse({"ok": True, "data": res.get("data", {})})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.post("/api/deposit/submit-reference")
async def api_deposit_submit_reference(request: Request):
    """Submit payment transaction reference to PeerPay Checkout API."""
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

    raw_input = str(body.get("reference", "")).strip()
    checkout_url = body.get("checkout_url") or ""
    payment_method = body.get("payment_method", "telebirr")

    if not raw_input:
        return JSONResponse(status_code=400, content={"error": "Missing reference"})

    # Optional explicit amount provided from UI
    explicit_amount = None
    if body.get("amount") is not None:
        try:
            explicit_amount = float(body.get("amount"))
            if explicit_amount <= 0:
                explicit_amount = None
        except (ValueError, TypeError):
            explicit_amount = None

    # 1. Parse SMS if full notification was pasted or extract URL / token
    parsed = parse_deposit_sms(raw_input)
    reference = raw_input
    amount = explicit_amount

    if parsed and parsed.get("reference"):
        reference = parsed["reference"]
        if not amount:
            amount = parsed.get("amount")
    else:
        ref, url, inline_amt = extract_reference_and_url(raw_input)
        if ref:
            reference = ref
            if not amount:
                amount = inline_amt

    # 2. If amount is still missing, attempt online receipt lookup (Telebirr)
    if amount is None:
        fetched = await fetch_telebirr_receipt(raw_input)
        if fetched and fetched.get("amount") and fetched.get("status") == "completed":
            amount = fetched["amount"]
            reference = fetched.get("reference") or reference

    # 3. Duplicate receipt check
    fp = fingerprint(raw_input) if len(raw_input) > 30 else f"ref:{reference}"
    existing_tx = await db.get_deposit_by_fingerprint(fp)
    if existing_tx:
        return JSONResponse(
            status_code=400,
            content={"error": f"ይህ የክፍያ ማስረጃ ቀድሞውኑ የ{existing_tx['amount']:.2f} ETB ገቢ ተደርጓል"},
        )

    existing_dep = await db.get_peerpay_deposit(reference)
    if existing_dep and existing_dep.get("credited"):
        return JSONResponse(
            status_code=400,
            content={"error": f"ይህ የክፍያ ማስረጃ ቀድሞውኑ የ{existing_dep['amount']:.2f} ETB ገቢ ተደርጓል"},
        )

    # 4. If genuine SMS receipt or verified receipt with amount is provided, auto-credit user immediately!
    if amount is not None and amount > 0:
        credited, new_balance, is_dup = await db.credit_peerpay_deposit_once(
            payment_id=reference,
            telegram_id=tg_id,
            amount=amount,
        )
        if credited:
            return JSONResponse({
                "ok": True,
                "data": {
                    "status": "succeeded",
                    "reference": reference,
                    "amount": amount,
                    "new_balance": new_balance,
                    "message": f"ክፍያዎ በተሳካ ሁኔታ ተረጋግጧል! {amount:.2f} ETB ወደ ሂሳብዎ ተጨምሯል።",
                },
            })

    # 5. If checkout_url was provided, submit reference to checkout API
    if checkout_url:
        try:
            res = await peerpay_client.submit_deposit_reference(
                checkout_token_or_url=checkout_url,
                reference=reference,
                payment_method=payment_method,
            )
            return JSONResponse({"ok": True, "data": res})
        except Exception as exc:
            logger.warning("PeerPay submit reference error: %s", exc)

    # 6. Resilient fallback: record pending deposit locally so user is never blocked
    await db.upsert_peerpay_deposit(
        payment_id=reference,
        telegram_id=tg_id,
        amount=amount or 0.0,
        currency="ETB",
        merchant_order_id=None,
        status="verification_pending",
    )
    return JSONResponse({
        "ok": True,
        "data": {
            "status": "verification_pending",
            "reference": reference,
            "message": "የግብይት ቁጥሩ ተመዝግቧል፤ እባክዎ ሙሉውን የSMS መልእክት፣ የደረሰኝ ሊንክ ወይም መጠኑን ያስገቡ።",
        },
    })


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

    new_balance = balance - amount
    destination = body.get("destination")
    payment_id = f"wd_{uuid.uuid4().hex[:12]}"
    checkout_url = ""

    try:
        res = await peerpay_client.create_withdrawal(
            merchant_customer_id=f"tg_{tg_id}",
            amount=amount,
            destination=destination,
        )
        wd_data = res.get("data", {})
        if wd_data.get("id"):
            payment_id = wd_data["id"]
        checkout_url = wd_data.get("checkout_url") or ""
        if checkout_url and destination and destination.get("bank") and destination.get("account_number"):
            try:
                await peerpay_client.confirm_withdrawal_destination(
                    checkout_token_or_url=checkout_url,
                    bank=destination["bank"],
                    account_number=destination["account_number"],
                )
                logger.info("Auto-confirmed withdrawal %s destination on checkout API", payment_id)
            except Exception as conf_err:
                logger.warning("Auto-confirm destination in API error: %s", conf_err)
    except Exception as exc:
        logger.exception("Failed to create PeerPay withdrawal: %s", exc)

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
        description=f"PeerPay withdrawal hold — {payment_id}",
    )

    return JSONResponse({
        "ok": True,
        "withdrawal_id": payment_id,
        "checkout_url": checkout_url,
        "amount": amount,
        "new_balance": new_balance,
    })





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
