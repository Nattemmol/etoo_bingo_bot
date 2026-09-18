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
        marks_a, called_a = self._row_marks_and_called(card_a, 0)
        marks_b, called_b = self._row_marks_and_called(card_b, 1)
        called = called_a | called_b
        self._add_player(101, "Alice", 3, marks_a, called)
        self._add_player(102, "Bob", 5, marks_b, called)

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


if __name__ == "__main__":
    unittest.main()