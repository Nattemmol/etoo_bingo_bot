import asyncio
import hashlib
import hmac
import json
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock


from starlette.testclient import TestClient

from bot import database as db
from bot.config import settings
from server.game import (
    GamePhase,
    GameRoom,
    Player,
    ROOM_CONFIG,
    check_bingo,
    generate_card,
    generate_card_by_id,
    get_eat_now,
    is_super_bingo_open,
    get_seconds_until_super_bingo,
)
from server.main import (
    BINGO_CLAIM_WINDOW_SECONDS,
    FREE_PLAY,
    WINNER_RESET_WAIT_SECONDS,
    app,
    get_room,
    reset_room,
)


def make_mock_init_data(telegram_id: int, first_name: str = "TestUser") -> str:
    user_json = json.dumps({"id": telegram_id, "first_name": first_name})
    data = {"auth_date": "1700000000", "user": user_json}
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    sig = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    return f"auth_date=1700000000&user={user_json}&hash={sig}"


async def ensure_user(user_id: int, phone: str, username: str, first_name: str, balance: float = 0.0):
    user = await db.get_user(user_id)
    if not user:
        await db.create_user(user_id, phone, username, first_name)
    await db.update_balance(user_id, balance)


class TestSpectatorAndSchedule(unittest.TestCase):
    def setUp(self):
        asyncio.run(db.init_db())

    def _read_until(self, ws, wanted):
        """Read websocket messages (draining interleaved broadcasts) until type wanted."""
        if isinstance(wanted, str):
            wanted = {wanted}
        for _ in range(50):
            msg = json.loads(ws.receive_text())
            if msg.get("type") in wanted:
                return msg
        raise AssertionError(f"Did not receive a message of type in {wanted}")

    def test_eat_timezone_and_schedule(self):
        eat_now = get_eat_now()
        self.assertEqual(eat_now.utcoffset(), timedelta(hours=3))

        self.assertTrue(is_super_bingo_open(always_open=True))

        secs = get_seconds_until_super_bingo()
        self.assertIsInstance(secs, int)
        self.assertGreaterEqual(secs, 0)
        self.assertLessEqual(secs, 86400)

    def test_deterministic_card_generator(self):
        card_a = generate_card_by_id(7)
        card_b = generate_card_by_id(7)
        self.assertEqual(card_a, card_b)

        card_c = generate_card_by_id(8)
        self.assertNotEqual(card_a, card_c)

        # Free cell is the center (N column, middle row)
        self.assertIsNone(card_a[2][2])

        # Values follow column ranges
        for col_idx, values in enumerate([range(1, 16), range(16, 31), range(31, 46), range(46, 61), range(61, 76)]):
            for row in range(5):
                val = card_a[row][col_idx]
                if row == 2 and col_idx == 2:
                    self.assertIsNone(val)
                else:
                    self.assertIn(val, values)

    def test_bot_play_room_callback_free_launch(self):
        from bot.handlers.play import play_room_callback
        user_id = 999111
        asyncio.run(ensure_user(user_id, "+251911000000", "zerouser", "Zero", 0.0))

        update = MagicMock()
        update.effective_user.id = user_id
        update.callback_query.data = "room_play_10"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()

        context = MagicMock()
        asyncio.run(play_room_callback(update, context))

        update.callback_query.edit_message_text.assert_called_once()
        args, kwargs = update.callback_query.edit_message_text.call_args
        self.assertIn("reply_markup", kwargs)
        msg_text = args[0] if args else kwargs.get("text", "")
        self.assertIn("PLAY", msg_text)
        self.assertIn("Watching is free", msg_text)

    def test_websocket_spectator_and_insufficient_balance_card_selection(self):
        user_id = 999222
        asyncio.run(ensure_user(user_id, "+251911000001", "pooruser", "Poor", 0.0))

        reset_room("room_play_10")
        client = TestClient(app)
        with client.websocket_connect("/ws/room_play_10") as ws:
            init_data = make_mock_init_data(user_id, "Poor")
            ws.send_text(json.dumps({"type": "join", "initData": init_data}))

            init_resp = self._read_until(ws, "init")
            self.assertEqual(init_resp["type"], "init")
            self.assertFalse(init_resp["is_player"])
            self.assertEqual(init_resp["user"]["balance"], 0.0)
            self.assertEqual(init_resp["room"]["entry_fee"], 10.0)

            if FREE_PLAY:
                # Testing mode: balance is bypassed, the pick goes through anyway
                ws.send_text(json.dumps({"type": "select_card", "card_id": 1}))
                conf = self._read_until(ws, "card_confirmed")
                self.assertEqual(conf["type"], "card_confirmed")
                self.assertEqual(conf["card_id"], 1)
                self.assertEqual(conf["balance"], 0.0)
                self.assertEqual(conf["pot"], 8.0)
            else:
                # Try selecting a card with 0 balance
                ws.send_text(json.dumps({"type": "select_card", "card_id": 1}))

                err_resp = self._read_until(ws, "card_error")
                self.assertEqual(err_resp["type"], "card_error")
                self.assertIn("Insufficient balance", err_resp["message"])

            # Verify websocket is still connected and user is still spectator!
            ws.send_text(json.dumps({"type": "ping"}))
            pong = self._read_until(ws, "pong")
            self.assertEqual(pong["type"], "pong")

    def test_websocket_card_selection_with_sufficient_balance(self):
        user_id = 999333
        asyncio.run(ensure_user(user_id, "+251911000002", "richuser", "Rich", 50.0))

        reset_room("room_play_10")
        expected_balance = 50.0 if FREE_PLAY else 40.0
        client = TestClient(app)
        with client.websocket_connect("/ws/room_play_10") as ws:
            init_data = make_mock_init_data(user_id, "Rich")
            ws.send_text(json.dumps({"type": "join", "initData": init_data}))

            init_resp = self._read_until(ws, "init")
            self.assertEqual(init_resp["type"], "init")
            self.assertFalse(init_resp["is_player"])

            # Select card by id
            ws.send_text(json.dumps({"type": "select_card", "card_id": 1}))

            resp = self._read_until(ws, "card_confirmed")
            self.assertEqual(resp["type"], "card_confirmed")
            self.assertEqual(resp["card_id"], 1)
            self.assertEqual(resp["balance"], expected_balance)
            self.assertEqual(resp["pot"], 8.0)
            self.assertEqual(resp["players"], 1)
            self.assertEqual(resp["card_ids"], [1])

            # Verify database balance
            bal = asyncio.run(db.get_balance(user_id))
            self.assertEqual(bal, expected_balance)

    def test_multi_card_purchase_and_taken_conflict(self):
        uid_a = 999666
        uid_b = 999667
        asyncio.run(ensure_user(uid_a, "+251911000006", "multia", "MultiA", 60.0))
        asyncio.run(ensure_user(uid_b, "+251911000007", "multib", "MultiB", 100.0))

        reset_room("room_play_10")
        expected_balance_a = 60.0 if FREE_PLAY else 40.0
        client = TestClient(app)
        with client.websocket_connect("/ws/room_play_10") as ws_a:
            ws_a.send_text(json.dumps({"type": "join", "initData": make_mock_init_data(uid_a, "MultiA")}))
            init_a = self._read_until(ws_a, "init")
            self.assertFalse(init_a["is_player"])

            # First card purchase
            ws_a.send_text(json.dumps({"type": "select_card", "card_id": 1}))
            conf1 = self._read_until(ws_a, "card_confirmed")
            self.assertEqual(conf1["card_id"], 1)
            self.assertEqual(conf1["card_ids"], [1])
            self.assertEqual(conf1["balance"], 60.0 if FREE_PLAY else 50.0)
            self.assertEqual(conf1["pot"], 8.0)

            # Second card purchase (same player)
            ws_a.send_text(json.dumps({"type": "select_card", "card_id": 2}))
            conf2 = self._read_until(ws_a, "card_confirmed")
            self.assertEqual(conf2["card_id"], 2)
            self.assertEqual(conf2["card_ids"], [1, 2])
            self.assertEqual(conf2["balance"], expected_balance_a)
            self.assertEqual(conf2["pot"], 16.0)
            self.assertEqual(len(conf2["cards"]), 2)
            self.assertEqual(conf2["players"], 2)  # lobby stat = cards picked, not unique users

            bal = asyncio.run(db.get_balance(uid_a))
            self.assertEqual(bal, expected_balance_a)

            # Second player tries to take an already-taken card
            with client.websocket_connect("/ws/room_play_10") as ws_b:
                ws_b.send_text(json.dumps({"type": "join", "initData": make_mock_init_data(uid_b, "MultiB")}))
                init_b = self._read_until(ws_b, "init")
                self.assertIn("taken_cards", init_b["room"])
                self.assertEqual(init_b["room"]["taken_cards"]["1"], uid_a)

                ws_b.send_text(json.dumps({"type": "select_card", "card_id": 1}))
                taken = self._read_until(ws_b, "card_taken_error")
                self.assertEqual(taken["type"], "card_taken_error")
                self.assertIn("ተይዞአል", taken["message"])
                self.assertEqual(taken["card_id"], 1)

            # uid_a can still leave the lobby without corruption
            bal_a = asyncio.run(db.get_balance(uid_a))
            self.assertEqual(bal_a, expected_balance_a)

    def test_auto_win_on_second_card_via_marks(self):
        uid = 999778
        asyncio.run(ensure_user(uid, "+251911000008", "bingoplayer", "Bingo", 100.0))

        reset_room("room_play_10")
        room = get_room("room_play_10")

        # Find a pair of cards where a full row on the SECOND card wins,
        # but the FIRST card has no complete line from those called numbers.
        chosen = None
        for cand1 in range(1, 101):
            for cand2 in range(1, 101):
                if cand1 == cand2:
                    continue
                card2 = generate_card_by_id(cand2)
                called = set(card2[0])
                if check_bingo(card2, called) and not check_bingo(generate_card_by_id(cand1), called):
                    chosen = (cand1, cand2, card2, called)
                    break
            if chosen:
                break
        self.assertIsNotNone(chosen, "No card pair found where only the second card wins a row")
        id1, id2, card2, called = chosen

        client = TestClient(app)
        prev_reset_wait = WINNER_RESET_WAIT_SECONDS
        prev_bingo_window = BINGO_CLAIM_WINDOW_SECONDS
        try:
            import server.main as server_main
            server_main.WINNER_RESET_WAIT_SECONDS = 0.05
            server_main.BINGO_CLAIM_WINDOW_SECONDS = 0.1

            with client.websocket_connect("/ws/room_play_10") as ws:
                ws.send_text(json.dumps({"type": "join", "initData": make_mock_init_data(uid, "Bingo")}))
                init_resp = self._read_until(ws, "init")
                self.assertFalse(init_resp["is_player"])

                ws.send_text(json.dumps({"type": "select_card", "card_id": id1}))
                conf1 = self._read_until(ws, "card_confirmed")
                self.assertEqual(conf1["card_ids"], [id1])

                ws.send_text(json.dumps({"type": "select_card", "card_id": id2}))
                conf2 = self._read_until(ws, "card_confirmed")
                # In single player game, house_cut is 0 and pot is 2 * entry_fee
                room.house_cut = 0.0
                room.pot = 2 * room.entry_fee
                pot = room.pot

                # Force a playing round where the called numbers cover only card2's row
                room.phase = GamePhase.PLAYING
                room.called_numbers = list(called)

                # Manually mark the full first row of card2 -> auto BINGO
                for col in range(5):
                    ws.send_text(json.dumps({"type": "mark", "card_id": id2, "row": 0, "col": col, "marked": True}))
                    self._read_until(ws, "mark_ack")

                claim = self._read_until(ws, "bingo_claim")
                self.assertEqual(claim["claimant_id"], uid)
                self.assertEqual(claim["card_id"], id2)

                # Wait for the claim window to close and the winner to be finalized
                winner = self._read_until(ws, "winner")
                self.assertEqual(len(winner["winners"]), 1)
                self.assertEqual(winner["winners"][0]["telegram_id"], uid)
                self.assertEqual(winner["winners"][0]["card_id"], id2)
                self.assertEqual(winner["winners"][0]["prize"], pot)

                bal = asyncio.run(db.get_balance(uid))
                # FREE_PLAY: no fees paid, wins the whole pot (20). Paid: -2 fees + whole pot.
                self.assertEqual(bal, (100.0 + pot) if FREE_PLAY else 100.0)

                # The room automatically returns to the selection lobby...
                reset_msg = self._read_until(ws, "round_reset")
                self.assertEqual(reset_msg["type"], "round_reset")
                self.assertEqual(reset_msg["room"]["players"], 0)  # no cards picked yet

                # ...and a fresh countdown is scheduled right away
                lobby_msg = self._read_until(ws, "lobby")
                self.assertEqual(lobby_msg["type"], "lobby")
                self.assertGreater(lobby_msg["countdown"], 0)
        finally:
            server_main.WINNER_RESET_WAIT_SECONDS = prev_reset_wait
            server_main.BINGO_CLAIM_WINDOW_SECONDS = prev_bingo_window

    def test_incomplete_marks_do_not_declare_win(self):
        uid = 999779
        asyncio.run(ensure_user(uid, "+251911000009", "fakebingo", "Faker", 50.0))

        reset_room("room_play_10")
        room = get_room("room_play_10")

        # Pick a card and a 20-number window that does NOT complete any line
        card_id = 3
        card = generate_card_by_id(card_id)
        called = None
        for start in range(1, 57):
            window = list(range(start, start + 20))
            if check_bingo(card, set(window)) is None:
                called = window
                break
        self.assertIsNotNone(called, "No 20-number window leaves a line incomplete")
        self.assertIsNone(check_bingo(card, set(called)))

        client = TestClient(app)
        with client.websocket_connect("/ws/room_play_10") as ws:
            ws.send_text(json.dumps({"type": "join", "initData": make_mock_init_data(uid, "Faker")}))
            init_resp = self._read_until(ws, "init")
            self.assertFalse(init_resp["is_player"])

            ws.send_text(json.dumps({"type": "select_card", "card_id": card_id}))
            conf = self._read_until(ws, "card_confirmed")
            self.assertEqual(conf["card_ids"], [card_id])
            self.assertEqual(conf["balance"], 50.0 if FREE_PLAY else 40.0)

            # Start a playing round with called numbers that give no line
            room.phase = GamePhase.PLAYING
            room.called_numbers = called

            # False BINGO call returns card_locked and, for single-player, triggers refund + round end
            ws.send_text(json.dumps({"type": "bingo"}))
            result = self._read_until(ws, "card_locked")
            self.assertEqual(result["type"], "card_locked")
            self.assertTrue(result["all_locked"])

            # For single player, all_locked triggers winner modal with refund
            winner_msg = self._read_until(ws, "winner")
            self.assertEqual(winner_msg["type"], "winner")
            self.assertTrue(winner_msg.get("all_locked"))
            self.assertTrue(winner_msg.get("is_single_player"))

            # Single player received full 10.0 refund -> balance is restored to 50.0
            bal_after = asyncio.run(db.get_balance(uid))
            self.assertEqual(bal_after, 50.0)

    def test_multiple_winners_split_pot(self):
        uid_a = 999881
        uid_b = 999882
        asyncio.run(ensure_user(uid_a, "+251911000010", "wina", "WinA", 100.0))
        asyncio.run(ensure_user(uid_b, "+251911000011", "winb", "WinB", 100.0))

        reset_room("room_play_10")
        room = get_room("room_play_10")

        card_a = generate_card_by_id(11)
        card_b = generate_card_by_id(12)
        self.assertIsNotNone(check_bingo(card_a, set(range(1, 76))))
        self.assertIsNotNone(check_bingo(card_b, set(range(1, 76))))

        client = TestClient(app)
        prev_reset_wait = WINNER_RESET_WAIT_SECONDS
        prev_bingo_window = BINGO_CLAIM_WINDOW_SECONDS
        try:
            import server.main as server_main
            server_main.WINNER_RESET_WAIT_SECONDS = 0.05
            server_main.BINGO_CLAIM_WINDOW_SECONDS = 0.2

            with client.websocket_connect("/ws/room_play_10") as ws_a:
                ws_a.send_text(json.dumps({"type": "join", "initData": make_mock_init_data(uid_a, "WinA")}))
                self._read_until(ws_a, "init")
                ws_a.send_text(json.dumps({"type": "select_card", "card_id": 11}))
                self._read_until(ws_a, "card_confirmed")

                with client.websocket_connect("/ws/room_play_10") as ws_b:
                    ws_b.send_text(json.dumps({"type": "join", "initData": make_mock_init_data(uid_b, "WinB")}))
                    self._read_until(ws_b, "init")
                    ws_b.send_text(json.dumps({"type": "select_card", "card_id": 12}))
                    self._read_until(ws_b, "card_confirmed")

                    pot = room.pot
                    room.phase = GamePhase.PLAYING
                    room.called_numbers = list(range(1, 76))

                    # Player A marks a full winning row -> first claim opens the window
                    marks_a = [(r, 0) for r in range(5)]
                    for row, col in marks_a:
                        ws_a.send_text(json.dumps({"type": "mark", "card_id": 11, "row": row, "col": col, "marked": True}))
                        self._read_until(ws_a, "mark_ack")
                    claim_a = self._read_until(ws_a, "bingo_claim")
                    self.assertEqual(claim_a["claimant_id"], uid_a)

                    # Player B marks their winning row within the window -> shares pot
                    for row, col in marks_a:
                        ws_b.send_text(json.dumps({"type": "mark", "card_id": 12, "row": row, "col": col, "marked": True}))
                        self._read_until(ws_b, "mark_ack")
                    claim_b = self._read_until(ws_b, "bingo_claim")
                    self.assertEqual(claim_b["claimant_id"], uid_b)
                    self.assertEqual(claim_b["claimants"], 2)

                    # After the window, both are winners splitting the pot
                    winner_a = self._read_until(ws_a, "winner")
                    self.assertEqual(len(winner_a["winners"]), 2)
                    share = winner_a["winners"][0]["prize"]
                    self.assertEqual(share, round(pot / 2, 2))

                    bal_a = asyncio.run(db.get_balance(uid_a))
                    bal_b = asyncio.run(db.get_balance(uid_b))
                    # Multiplayer: -10 fee + 8 share = 98.0 (2 Birr house deduction preserved)
                    expected_split_balance = (100.0 + share) if FREE_PLAY else (100.0 - 10.0 + share)
                    self.assertEqual(bal_a, expected_split_balance)
                    self.assertEqual(bal_b, expected_split_balance)
        finally:
            server_main.WINNER_RESET_WAIT_SECONDS = prev_reset_wait
            server_main.BINGO_CLAIM_WINDOW_SECONDS = prev_bingo_window

    def test_super_bingo_schedule_restriction(self):
        user_id = 999444
        asyncio.run(ensure_user(user_id, "+251911000003", "superuser", "Super", 100.0))

        reset_room("room_super_50")
        client = TestClient(app)
        with client.websocket_connect("/ws/room_super_50") as ws:
            init_data = make_mock_init_data(user_id, "Super")
            ws.send_text(json.dumps({"type": "join", "initData": init_data}))

            init_resp = self._read_until(ws, "init")
            self.assertEqual(init_resp["type"], "init")
            self.assertEqual(init_resp["room"]["entry_fee"], 50.0)

            # Selecting card in superBingo is allowed anytime
            ws.send_text(json.dumps({"type": "select_card", "card_id": 5}))
            resp = self._read_until(ws, "card_confirmed")
            self.assertEqual(resp["type"], "card_confirmed")
            self.assertEqual(resp["card_id"], 5)

            # Balance check based on FREE_PLAY
            bal = asyncio.run(db.get_balance(user_id))
            self.assertEqual(bal, 100.0 if FREE_PLAY else 50.0)

    def test_room_reset_clears_taken_cards(self):
        reset_room("room_play_10")
        room = get_room("room_play_10")
        room.phase = GamePhase.PLAYING
        room.pot = 20.0
        room.taken_cards[3] = 555
        room.players["x"] = Player(
            telegram_id=555,
            name="X",
            ws_id="x",
            card_ids=[3],
            cards={3: generate_card_by_id(3)},
        )

        reset_room("room_play_10")

        new_room = get_room("room_play_10")
        self.assertEqual(new_room.taken_cards, {})
        self.assertEqual(new_room.players, {})
        self.assertEqual(new_room.pot, 0.0)
        self.assertEqual(new_room.phase, GamePhase.LOBBY)
        self.assertEqual(new_room.called_numbers, [])
        self.assertEqual(new_room.winner_id, None)

    def test_spectator_joins_during_playing_game_and_receives_calls(self):
        from server.main import broadcast
        reset_room("room_play_10")
        room = get_room("room_play_10")
        room.phase = GamePhase.PLAYING
        room.called_numbers = [5, 18, 32]
        room.pot = 10.0

        user_id = 999555
        asyncio.run(ensure_user(user_id, "+251911000004", "watcher", "Watcher", 0.0))

        client = TestClient(app)
        with client.websocket_connect("/ws/room_play_10") as ws:
            init_data = make_mock_init_data(user_id, "Watcher")
            ws.send_text(json.dumps({"type": "join", "initData": init_data}))

            init_resp = self._read_until(ws, "init")
            self.assertEqual(init_resp["type"], "init")
            self.assertEqual(init_resp["room"]["phase"], "playing")
            self.assertEqual(init_resp["room"]["called"], [5, 18, 32])
            self.assertEqual(init_resp["room"]["latest_call"]["number"], 32)
            self.assertFalse(init_resp["is_player"])

            # Broadcast a new ball call to the room
            asyncio.run(broadcast(room, {"type": "call", "number": 45, "letter": "N", "called": [5, 18, 32, 45]}))
            call_msg = self._read_until(ws, "call")
            self.assertEqual(call_msg["type"], "call")
            self.assertEqual(call_msg["number"], 45)
            self.assertEqual(call_msg["letter"], "N")

    def test_lobby_countdown_is_30_seconds(self):
        reset_room("room_play_10")
        room = get_room("room_play_10")
        self.assertEqual(room.lobby_seconds, 30)

        super_room = get_room("room_super_50")
        self.assertEqual(super_room.lobby_seconds, 30)

    def test_card_unselect_and_full_refund(self):
        uid = 999333
        asyncio.run(ensure_user(uid, "+251911000012", "unselector", "UnselectUser", 50.0))
        reset_room("room_play_10")

        client = TestClient(app)
        with client.websocket_connect("/ws/room_play_10") as ws:
            ws.send_text(json.dumps({"type": "join", "initData": make_mock_init_data(uid, "UnselectUser")}))
            init = self._read_until(ws, "init")
            self.assertEqual(init["type"], "init")

            # Select card 7
            ws.send_text(json.dumps({"type": "select_card", "card_id": 7}))
            conf = self._read_until(ws, "card_confirmed")
            self.assertEqual(conf["card_id"], 7)
            self.assertEqual(conf["pot"], 8.0)
            self.assertEqual(conf["players"], 1)

            room = get_room("room_play_10")
            self.assertIn(7, room.taken_cards)
            self.assertEqual(room.pot, 8.0)

            # Unselect card 7
            ws.send_text(json.dumps({"type": "unselect_card", "card_id": 7}))
            unsel = self._read_until(ws, "card_unselected")
            self.assertEqual(unsel["type"], "card_unselected")
            self.assertEqual(unsel["card_id"], 7)
            self.assertEqual(unsel["card_ids"], [])
            self.assertFalse(unsel["is_player"])
            self.assertEqual(unsel["pot"], 0.0)
            self.assertEqual(unsel["players"], 0)
            self.assertEqual(unsel["refund_amount"], 10.0)

            # Check room state
            self.assertNotIn(7, room.taken_cards)
            self.assertEqual(room.pot, 0.0)
            self.assertEqual(len(room.taken_cards), 0)

    def test_false_bingo_locks_card(self):
        uid = 999444
        asyncio.run(ensure_user(uid, "+251911000013", "falser", "FalseUser", 50.0))
        reset_room("room_play_10")
        room = get_room("room_play_10")

        uid2 = 999778
        asyncio.run(ensure_user(uid2, "+251911000078", "otheruser", "OtherUser", 100.0))

        client = TestClient(app)
        with client.websocket_connect("/ws/room_play_10") as ws:
            ws.send_text(json.dumps({"type": "join", "initData": make_mock_init_data(uid, "FalseUser")}))
            self._read_until(ws, "init")

            # Select card 12
            ws.send_text(json.dumps({"type": "select_card", "card_id": 12}))
            conf = self._read_until(ws, "card_confirmed")
            self.assertEqual(conf["card_id"], 12)

            with client.websocket_connect("/ws/room_play_10") as ws2:
                ws2.send_text(json.dumps({"type": "join", "initData": make_mock_init_data(uid2, "OtherUser")}))
                self._read_until(ws2, "init")
                ws2.send_text(json.dumps({"type": "select_card", "card_id": 13}))
                self._read_until(ws2, "card_confirmed")

                # Move room to playing phase
                room.phase = GamePhase.PLAYING

                # False BINGO call on card 12
                ws.send_text(json.dumps({"type": "bingo", "card_id": 12}))
                lock_resp = self._read_until(ws, "card_locked")
                self.assertEqual(lock_resp["type"], "card_locked")
                self.assertEqual(lock_resp["card_id"], 12)
                self.assertIn(12, lock_resp["locked_cards"])
                self.assertTrue(lock_resp["all_locked"])

                # In multiplayer, room remains PLAYING and locked card cannot be marked
                self.assertEqual(room.phase, GamePhase.PLAYING)
                ws.send_text(json.dumps({"type": "mark", "card_id": 12, "row": 0, "col": 0, "marked": True}))
                err = self._read_until(ws, "mark_error")
                self.assertEqual(err["type"], "mark_error")
                self.assertIn("ተቆልፏል", err["message"])

                # Player 1 balance retains the fee deduction (no refund in multiplayer: 50.0 - 10.0 = 40.0)
                bal1 = asyncio.run(db.get_balance(uid))
                self.assertEqual(bal1, 40.0)


if __name__ == "__main__":
    unittest.main()