import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server.game import (
    GamePhase,
    GameRoom,
    Player,
    check_bingo_marked,
    generate_card_by_id,
)
from server import main as server_main


def _room_with_players() -> GameRoom:
    room = GameRoom(room_id="room_play_10", name="PLAY", entry_fee=10.0, bingo_rule="line_corners")
    room.phase = GamePhase.PLAYING
    return room


def _card_row_indexes(row: int) -> list[int]:
    return [row * 5 + c for c in range(5)]


def _card_flat(card):
    return [cell for r in card for cell in r]


class TestCheckBingoMarked(unittest.TestCase):
    def setUp(self):
        self.card = generate_card_by_id(7)
        self.flat = _card_flat(self.card)

    def test_full_row_with_called_numbers_wins_line(self):
        row_idx = 0
        row_cells = [self.flat[i] for i in _card_row_indexes(row_idx)]
        self.assertNotIn(None, row_cells)
        called = set(row_cells)
        marks = set(_card_row_indexes(row_idx))
        self.assertEqual(check_bingo_marked(self.card, marks, called, "line"), "row")

    def test_middle_row_uses_free_center(self):
        # Row 2 contains the FREE center; only the 4 outer cells need marks.
        row_cells = [
            self.flat[i]
            for i in _card_row_indexes(2)
            if self.flat[i] is not None
        ]
        called = set(row_cells)
        marks = {i for i in _card_row_indexes(2) if self.flat[i] is not None}
        self.assertEqual(check_bingo_marked(self.card, marks, called, "line"), "row")

    def test_marked_but_not_called_does_not_win(self):
        row_idx = 1
        row_cells = [self.flat[i] for i in _card_row_indexes(row_idx)]
        self.assertNotIn(None, row_cells)
        # Marks are complete but half the numbers were never called.
        called = set(row_cells[:2])
        marks = set(_card_row_indexes(row_idx))
        self.assertIsNone(check_bingo_marked(self.card, marks, called, "line"))

    def test_corners_only_wins_with_line_corners_rule(self):
        corner_values = {self.flat[i] for i in (0, 4, 20, 24)}
        self.assertNotIn(None, corner_values)
        marks = {0, 4, 20, 24}
        self.assertEqual(
            check_bingo_marked(self.card, marks, corner_values, "line_corners"), "corners"
        )
        # superBingo rule = line only -> corners do NOT win.
        self.assertIsNone(check_bingo_marked(self.card, marks, corner_values, "line"))

    def test_column_wins(self):
        col = 2
        cells = [self.flat[r * 5 + col] for r in range(5) if self.flat[r * 5 + col] is not None]
        called = set(cells)
        marks = {r * 5 + col for r in range(5) if self.flat[r * 5 + col] is not None}
        self.assertEqual(check_bingo_marked(self.card, marks, called, "line"), "column")

    def test_full_card_rule(self):
        called = {v for v in self.flat if v is not None}
        marks = {i for i in range(25) if self.flat[i] is not None}
        self.assertEqual(check_bingo_marked(self.card, marks, called, "full"), "full")

    def test_line_does_not_win_in_full_card_rule(self):
        row_cells = [self.flat[i] for i in _card_row_indexes(0)]
        called = set(row_cells)
        marks = set(_card_row_indexes(0))
        self.assertIsNone(check_bingo_marked(self.card, marks, called, "full"))

    def test_extra_uncalled_marks_do_not_prevent_valid_line_win(self):
        row_cells = [self.flat[i] for i in _card_row_indexes(0)]
        called = set(row_cells)
        # Marks include valid row 0 PLUS extra cells that were never called
        marks = set(_card_row_indexes(0)) | {10, 11, 23}
        self.assertEqual(check_bingo_marked(self.card, marks, called, "line"), "row")

    def test_diagonal_wins(self):
        # Diagonal from top-left (0) to bottom-right (24): 0, 6, 12(FREE), 18, 24
        diag_idxs = [0, 6, 18, 24]
        diag_cells = {self.flat[i] for i in diag_idxs}
        marks = set(diag_idxs)
        self.assertEqual(check_bingo_marked(self.card, marks, diag_cells, "line"), "diagonal")


class TestAutoDeclareWins(unittest.TestCase):
    def setUp(self):
        self.room = _room_with_players()

    def _add_player(self, telegram_id, name, card_id, marks, called):
        card = generate_card_by_id(card_id)
        player = Player(telegram_id=telegram_id, name=name, ws_id=f"ws-{telegram_id}")
        player.card_ids = [card_id]
        player.cards = {card_id: card}
        player.marks = {card_id: set(marks)}
        self.room.players[f"ws-{telegram_id}"] = player
        self.room.called_numbers = sorted(set(self.room.called_numbers) | set(called))

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def _row_marks_and_called(self, card, row):
        flat = _card_flat(card)
        idxs = _card_row_indexes(row)
        called = {flat[i] for i in idxs if flat[i] is not None}
        marks = {i for i in idxs if flat[i] is not None}
        return marks, called

    @patch("server.main.broadcast", new=AsyncMock())
    @patch("server.main.finalize_bingo", new=AsyncMock())
    def test_single_winner_auto_declared(self):
        card = generate_card_by_id(3)
        marks, called = self._row_marks_and_called(card, 0)
        self._add_player(101, "Alice", 3, marks, called)

        self._run(server_main.check_all_wins(self.room))

        self.assertEqual(len(self.room.bingo_claimants), 1)
        self.assertEqual(self.room.bingo_claimants[0]["telegram_id"], 101)
        self.assertIsNotNone(self.room.bingo_window_until)
        server_main.broadcast.assert_awaited()

    @patch("server.main.broadcast", new=AsyncMock())
    @patch("server.main.finalize_bingo", new=AsyncMock())
    def test_multiple_winners_share_pot(self):
        card_a = generate_card_by_id(3)
        card_b = generate_card_by_id(5)
        shared_ball = 7
        card_a[0][0] = shared_ball
        card_b[1][0] = shared_ball
        marks_a, called_a = self._row_marks_and_called(card_a, 0)
        marks_b, called_b = self._row_marks_and_called(card_b, 1)
        called = (called_a | called_b) - {shared_ball}
        self._add_player(101, "Alice", 3, marks_a, called)
        self._add_player(102, "Bob", 5, marks_b, called)
        self.room.players["ws-101"].cards[3] = card_a
        self.room.players["ws-102"].cards[5] = card_b
        self.room.called_numbers.append(shared_ball)

        self._run(server_main.check_all_wins(self.room))

        self.assertEqual(len(self.room.bingo_claimants), 2)
        ids = {c["telegram_id"] for c in self.room.bingo_claimants}
        self.assertEqual(ids, {101, 102})

    @patch("server.main.broadcast", new=AsyncMock())
    @patch("server.main.finalize_bingo", new=AsyncMock())
    def test_only_winning_player_declared(self):
        card_a = generate_card_by_id(3)
        card_b = generate_card_by_id(5)
        marks_a, called_a = self._row_marks_and_called(card_a, 0)
        self._add_player(101, "Alice", 3, marks_a, called_a)
        # Bob marked a full row but his numbers were never called.
        self._add_player(102, "Bob", 5, {0, 1, 2, 3, 4}, set())

        self._run(server_main.check_all_wins(self.room))

        self.assertEqual(len(self.room.bingo_claimants), 1)
        self.assertEqual(self.room.bingo_claimants[0]["telegram_id"], 101)

    def test_window_closed_no_claims(self):
        import time

        self.room.bingo_window_until = time.monotonic() - 1
        card = generate_card_by_id(3)
        marks, called = self._row_marks_and_called(card, 0)
        self._add_player(101, "Alice", 3, marks, called)

        with patch("server.main.broadcast", new=AsyncMock()):
            with patch("server.main.finalize_bingo", new=AsyncMock()):
                self._run(server_main.check_all_wins(self.room))

        self.assertEqual(self.room.bingo_claimants, [])

    def test_locked_card_does_not_win(self):
        card = generate_card_by_id(3)
        marks, called = self._row_marks_and_called(card, 0)
        self._add_player(101, "Alice", 3, marks, called)
        # Lock Alice's card
        player = self.room.players["ws-101"]
        player.locked_cards.add(3)

        with patch("server.main.broadcast", new=AsyncMock()):
            with patch("server.main.finalize_bingo", new=AsyncMock()):
                self._run(server_main.check_all_wins(self.room))

        self.assertEqual(self.room.bingo_claimants, [])

    def test_check_bingo_marked_fast_with_indexes(self):
        from server.game import check_bingo_marked_fast_with_indexes
        card = generate_card_by_id(3)
        marks, called = self._row_marks_and_called(card, 0)
        pattern, indexes = check_bingo_marked_fast_with_indexes(card, marks, called, "line")
        self.assertEqual(pattern, "row")
        self.assertEqual(indexes, [0, 1, 2, 3, 4])

    def test_call_interval_increased_by_two_seconds(self):
        from server.game import ROOM_CONFIG
        self.assertEqual(ROOM_CONFIG["room_play_10"]["call_interval"], 6.0)
        self.assertEqual(ROOM_CONFIG["room_super_50"]["call_interval"], 5.0)

    def test_claim_includes_winning_indexes(self):
        card = generate_card_by_id(3)
        marks, called = self._row_marks_and_called(card, 0)
        self._add_player(101, "Alice", 3, marks, called)
        self._run(server_main.check_all_wins(self.room))
        self.assertEqual(len(self.room.bingo_claimants), 1)
        self.assertEqual(self.room.bingo_claimants[0]["winning_indexes"], [0, 1, 2, 3, 4])

    def test_credit_balance_accepts_default_description(self):
        import inspect
        from bot.database import credit_balance as cb_sqlite
        from bot.db_postgres import credit_balance as cb_pg
        sig_sqlite = inspect.signature(cb_sqlite)
        sig_pg = inspect.signature(cb_pg)
        self.assertIn("description", sig_sqlite.parameters)
        self.assertNotEqual(sig_sqlite.parameters["description"].default, inspect.Parameter.empty)
        self.assertIn("description", sig_pg.parameters)
        self.assertNotEqual(sig_pg.parameters["description"].default, inspect.Parameter.empty)

    def test_solo_player_all_locked_refund(self):
        player = Player(
            telegram_id=999,
            name="SoloUser",
            ws_id="ws-999",
            card_ids=[1, 2],
            cards={1: generate_card_by_id(1), 2: generate_card_by_id(2)},
            marks={},
            locked_cards={1, 2},
        )
        self.room.players["ws-999"] = player
        self.room.taken_cards = {1: 999, 2: 999}
        self.room.entry_fee = 10.0

        mock_credit = AsyncMock(return_value=120.0)
        mock_notify = AsyncMock()
        mock_send = AsyncMock()
        mock_broadcast = AsyncMock()

        with patch("server.main.db.credit_balance", new=mock_credit):
            with patch("server.main._notify_telegram", new=mock_notify):
                with patch("server.main.send", new=mock_send):
                    with patch("server.main.broadcast", new=mock_broadcast):
                        with patch("server.main.db.add_transaction", new=AsyncMock()):
                            with patch("server.main.db.get_user", new=AsyncMock(return_value={"balance": 120.0})):
                                with patch("server.main._delayed_locked_reset", new=AsyncMock()):
                                    with patch("server.main.FREE_PLAY", False):
                                        self._run(server_main.handle_cards_locked(
                                            self.room, player, AsyncMock(), "ws-999", 2, True
                                        ))

        # 2 cards * 10 ETB = 20.0 ETB refunded (100%)
        mock_credit.assert_called_once()
        args = mock_credit.call_args[0]
        self.assertEqual(args[0], 999)
        self.assertEqual(args[1], 20.0)
        mock_notify.assert_called_once()
        self.assertEqual(mock_notify.call_args[0][0], 999)

    def test_multiplayer_all_locked_refund(self):
        p1 = Player(
            telegram_id=101,
            name="P1",
            ws_id="ws-101",
            card_ids=[1],
            cards={1: generate_card_by_id(1)},
            marks={},
            locked_cards={1},
            forfeited=True,
        )
        p2 = Player(
            telegram_id=102,
            name="P2",
            ws_id="ws-102",
            card_ids=[2],
            cards={2: generate_card_by_id(2)},
            marks={},
            locked_cards={2},
            forfeited=True,
        )
        self.room.players = {"ws-101": p1, "ws-102": p2}
        self.room.taken_cards = {1: 101, 2: 102}
        self.room.entry_fee = 10.0

        mock_credit = AsyncMock(return_value=110.0)
        mock_notify = AsyncMock()

        with patch("server.main.db.credit_balance", new=mock_credit):
            with patch("server.main._notify_telegram", new=mock_notify):
                with patch("server.main.send", new=AsyncMock()):
                    with patch("server.main.broadcast", new=AsyncMock()):
                        with patch("server.main._delayed_locked_reset", new=AsyncMock()):
                            with patch("server.main.db.add_transaction", new=AsyncMock()):
                                self._run(server_main.handle_cards_locked(
                                    self.room, p2, AsyncMock(), "ws-102", 2, True
                                ))

        # Both players should be refunded 10.0 ETB
        self.assertEqual(mock_credit.call_count, 2)
        refunded_tids = {call[0][0] for call in mock_credit.call_args_list}
        self.assertEqual(refunded_tids, {101, 102})
        self.assertEqual(mock_notify.call_count, 2)

    def test_fast_dumps_handles_int_keys(self):
        from server.main import _fast_dumps
        payload = {
            "type": "room_stats",
            "taken_cards": {1: 12345, 2: 67890},
            "nested": {42: "answer"}
        }
        res = _fast_dumps(payload)
        self.assertIsInstance(res, str)
        import json
        data = json.loads(res)
        self.assertEqual(data["taken_cards"]["1"], 12345)

    def test_cross_card_auto_marking(self):
        from server.main import process_player_mark
        card1 = generate_card_by_id(1)
        card2 = generate_card_by_id(2)

        # Find a common number between card1 and card2
        common_num = None
        pos1 = None
        pos2 = None
        for r1 in range(5):
            for c1 in range(5):
                v1 = card1[r1][c1]
                if v1 is None:
                    continue
                for r2 in range(5):
                    for c2 in range(5):
                        v2 = card2[r2][c2]
                        if v1 == v2:
                            common_num = v1
                            pos1 = (r1, c1)
                            pos2 = (r2, c2)
                            break
                    if common_num is not None:
                        break
            if common_num is not None:
                break

        self.assertIsNotNone(common_num, "Should find a shared number between card 1 and card 2")
        r1, c1 = pos1
        r2, c2 = pos2
        flat1 = r1 * 5 + c1
        flat2 = r2 * 5 + c2

        player = Player(
            telegram_id=777,
            name="TwoCardPlayer",
            ws_id="ws-777",
            card_ids=[1, 2],
            cards={1: card1, 2: card2},
            marks={1: set(), 2: set()},
        )
        self.room.players["ws-777"] = player

        # 1. Mark on Card 1 -> should mark both Card 1 and Card 2
        success = process_player_mark(self.room, player, 1, r1, c1, True)
        self.assertTrue(success)
        self.assertIn(flat1, player.marks[1])
        self.assertIn(flat2, player.marks[2])

        # 2. Unmark on Card 1 -> should unmark both Card 1 and Card 2
        success = process_player_mark(self.room, player, 1, r1, c1, False)
        self.assertTrue(success)
        self.assertNotIn(flat1, player.marks[1])
        self.assertNotIn(flat2, player.marks[2])

        # 3. If Card 2 is locked, marking on Card 1 does NOT mark locked Card 2
        player.locked_cards.add(2)
        success = process_player_mark(self.room, player, 1, r1, c1, True)
        self.assertTrue(success)
        self.assertIn(flat1, player.marks[1])
        self.assertNotIn(flat2, player.marks[2])


if __name__ == "__main__":
    unittest.main()