import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from bot import database as db
from server.game import (
    GamePhase,
    GameRoom,
    Player,
    check_bingo,
    check_bingo_fast,
    check_bingo_fast_with_indexes,
    check_bingo_marked,
    check_bingo_marked_fast,
    check_bingo_marked_fast_with_indexes,
    generate_card_by_id,
    get_card_number_index,
)
from server import main as server_main
from server.main import find_winning_card, add_claim, handle_cards_locked


def _card_row_indexes(row: int) -> list[int]:
    return [row * 5 + c for c in range(5)]


def _card_flat(card):
    return [cell for r in card for cell in r]


class TestBingoCurrentNumberRule(unittest.TestCase):
    def setUp(self):
        asyncio.run(db.init_db())
        self.card = generate_card_by_id(1)
        self.flat = _card_flat(self.card)

    def test_get_card_number_index(self):
        num_at_0 = self.flat[0]
        self.assertEqual(get_card_number_index(self.card, num_at_0), 0)

        num_at_12 = self.flat[12]  # center FREE (None)
        self.assertIsNone(num_at_12)
        self.assertIsNone(get_card_number_index(self.card, None))

        # A number not on this card
        self.assertIsNone(get_card_number_index(self.card, 999))

    def test_valid_bingo_requires_current_number_on_winning_line(self):
        # Row 0 cells
        row_0_cells = [self.flat[i] for i in _card_row_indexes(0)]
        called = set(row_0_cells)
        marks = set(_card_row_indexes(0))

        # Ball 0 is in Row 0 -> Valid Win!
        current_ball = row_0_cells[0]
        pattern, indexes = check_bingo_marked_fast_with_indexes(
            self.card, marks, called, "line", current_number=current_ball
        )
        self.assertEqual(pattern, "row")
        self.assertEqual(indexes, [0, 1, 2, 3, 4])

        # A ball from Row 1 is called (not in Row 0) -> Invalid! Row 0 does NOT contain this ball!
        row_1_cells = [self.flat[i] for i in _card_row_indexes(1)]
        different_ball = row_1_cells[0]
        called.add(different_ball)

        pattern, indexes = check_bingo_marked_fast_with_indexes(
            self.card, marks, called, "line", current_number=different_ball
        )
        self.assertIsNone(pattern)
        self.assertEqual(indexes, [])

    def test_multi_line_matches_line_containing_current_number(self):
        # Complete Row 0 AND Diagonal (0, 6, 12, 18, 24)
        row_0_idxs = set(_card_row_indexes(0))
        diag_idxs = {0, 6, 18, 24}  # 12 is FREE

        all_idxs = row_0_idxs | diag_idxs
        called = {self.flat[i] for i in all_idxs if self.flat[i] is not None}
        marks = set(all_idxs)

        # Number at cell 18 (on diagonal, NOT on row 0)
        num_at_18 = self.flat[18]
        pattern, indexes = check_bingo_marked_fast_with_indexes(
            self.card, marks, called, "line", current_number=num_at_18
        )
        self.assertEqual(pattern, "diagonal")
        self.assertIn(18, indexes)

        # Number at cell 1 (on row 0, NOT on diagonal)
        num_at_1 = self.flat[1]
        pattern, indexes = check_bingo_marked_fast_with_indexes(
            self.card, marks, called, "line", current_number=num_at_1
        )
        self.assertEqual(pattern, "row")
        self.assertIn(1, indexes)

    def test_find_winning_card_locks_card_when_current_number_missing(self):
        room = GameRoom(room_id="room_play_10", name="PLAY", entry_fee=10.0, bingo_rule="line_corners")
        room.phase = GamePhase.PLAYING

        card = generate_card_by_id(2)
        flat = _card_flat(card)
        row_0_cells = [flat[i] for i in _card_row_indexes(0)]
        row_1_cells = [flat[i] for i in _card_row_indexes(1)]

        # History of called numbers: row 0 numbers was called first, then row 1 ball was called latest!
        room.called_numbers = list(row_0_cells) + [row_1_cells[0]]

        player = Player(
            telegram_id=12345,
            name="Tester",
            ws_id="ws-12345",
            card_ids=[2],
            cards={2: card},
            marks={2: set(_card_row_indexes(0))},  # marked row 0
        )
        room.players = {"ws-12345": player}

        # Current ball on board is row_1_cells[0], NOT on player's marked row 0
        win = find_winning_card(room, player, allow_unmarked=False, target_card_id=2)
        # MUST return None because the latest ball is not on their marked row!
        self.assertIsNone(win)

        # Now suppose latest ball called was indeed row_0_cells[4]
        room.called_numbers.append(row_0_cells[4])
        win2 = find_winning_card(room, player, allow_unmarked=False, target_card_id=2)
        self.assertIsNotNone(win2)
        self.assertEqual(win2[0], "row")
        self.assertEqual(win2[1], 2)

    def test_multi_winner_same_number_requirement(self):
        room = GameRoom(room_id="room_play_10", name="PLAY", entry_fee=10.0, bingo_rule="line_corners")
        room.phase = GamePhase.PLAYING

        card_a = generate_card_by_id(10)
        card_b = generate_card_by_id(20)
        card_c = generate_card_by_id(30)

        flat_a = _card_flat(card_a)
        flat_b = _card_flat(card_b)
        flat_c = _card_flat(card_c)

        # Both Player A (row 0) and Player B (column 0) share a number at cell 0:
        # Let's say shared ball is 5.
        # We manually construct cards with ball 5 on their lines to test multi-winner:
        ball_5 = 5
        card_a[0][0] = ball_5
        card_b[0][0] = ball_5
        card_c[1][0] = 99  # Player C does NOT have 5 on their line

        # Player A marked row 0
        row_a = [card_a[0][c] for c in range(5)]
        # Player B marked col 0
        col_b = [card_b[r][0] for r in range(5)]
        # Player C marked row 2
        row_c = [card_c[2][c] for c in range(5) if card_c[2][c] is not None]

        room.called_numbers = list(set(row_a) | set(col_b) | set(row_c) - {ball_5})
        # Ball 5 is now drawn from the board!
        room.called_numbers.append(ball_5)

        player_a = Player(1, "Alice", "ws-1", [10], {10: card_a}, {10: set(range(5))})
        player_b = Player(2, "Bob", "ws-2", [20], {20: card_b}, {20: {0, 5, 10, 15, 20}})
        player_c = Player(3, "Charlie", "ws-3", [30], {30: card_c}, {30: {10, 11, 13, 14}})

        room.players = {"ws-1": player_a, "ws-2": player_b, "ws-3": player_c}

        async def _test():
            # 1. Player A claims on ball 5 -> Valid!
            win_a = find_winning_card(room, player_a, target_card_id=10)
            self.assertIsNotNone(win_a)
            with patch("server.main.finalize_bingo", new=AsyncMock()):
                added_a = add_claim(room, player_a, win_a[0], win_a[1], win_a[2])
                self.assertTrue(added_a)

            # Board is frozen on winning_number = 5
            self.assertEqual(room.winning_number, ball_5)
            self.assertIsNotNone(room.bingo_window_until)

            # 2. Player B claims during the 5s window with ball 5 on col 0 -> Valid!
            win_b = find_winning_card(room, player_b, target_card_id=20)
            self.assertIsNotNone(win_b)
            added_b = add_claim(room, player_b, win_b[0], win_b[1], win_b[2])
            self.assertTrue(added_b)

            # 3. Player C claims during the 5s window, but Player C does NOT have ball 5 on their line -> Invalid!
            win_c = find_winning_card(room, player_c, target_card_id=30)
            self.assertIsNone(win_c)

            # Claimants are strictly Player A and Player B!
            self.assertEqual(len(room.bingo_claimants), 2)
            claimant_ids = [c["telegram_id"] for c in room.bingo_claimants]
            self.assertEqual(claimant_ids, [1, 2])

        asyncio.run(_test())

    def test_premarked_uncalled_numbers_allowed_and_wins_when_number_called(self):
        """Player pre-marks 7 uncalled numbers. When their needed number is picked, claim is valid!"""
        room = GameRoom(room_id="room_play_10", name="PLAY", entry_fee=10.0, bingo_rule="line_corners")
        room.phase = GamePhase.PLAYING

        card = generate_card_by_id(5)
        flat = _card_flat(card)
        row_0_cells = [flat[i] for i in _card_row_indexes(0)]

        # Row 0 cells 0..3 called earlier; cell 4 is the winning ball called NOW!
        earlier_balls = row_0_cells[:4]
        winning_ball = row_0_cells[4]
        room.called_numbers = list(earlier_balls) + [winning_ball]

        # Player marked Row 0 (including pre-marking cell 4 before it was picked)
        # PLUS pre-marked 7 other numbers on card that have NOT been called!
        uncalled_indexes = {5, 6, 7, 8, 9, 15, 16}
        all_marks = set(_card_row_indexes(0)) | uncalled_indexes

        player = Player(
            telegram_id=555,
            name="PreMarker",
            ws_id="ws-555",
            card_ids=[5],
            cards={5: card},
            marks={5: all_marks},
        )
        room.players = {"ws-555": player}

        win = find_winning_card(room, player, allow_unmarked=False, target_card_id=5)
        self.assertIsNotNone(win)
        self.assertEqual(win[0], "row")
        self.assertEqual(win[1], 5)
        self.assertEqual(win[2], [0, 1, 2, 3, 4])

    def test_multiple_lines_where_one_contains_current_number(self):
        """Card has multiple completed lines; the one containing current number wins."""
        room = GameRoom(room_id="room_play_10", name="PLAY", entry_fee=10.0, bingo_rule="line_corners")
        room.phase = GamePhase.PLAYING

        card = generate_card_by_id(6)
        flat = _card_flat(card)

        # Row 0 (0..4) and Column 0 (0, 5, 10, 15, 20)
        row_0_cells = [flat[i] for i in range(5)]
        col_0_rest = [flat[5], flat[10], flat[15]]
        current_ball = flat[20]  # On Column 0, NOT on Row 0!

        # Called list has Row 0 numbers and Col 0 numbers, with current_ball at the end
        room.called_numbers = list(row_0_cells) + list(col_0_rest) + [current_ball]

        # Marks have both Row 0 and Col 0, plus 5 uncalled marks
        marks = {0, 1, 2, 3, 4, 5, 10, 15, 20, 6, 7, 8, 9, 14}
        player = Player(
            telegram_id=666,
            name="MultiLiner",
            ws_id="ws-666",
            card_ids=[6],
            cards={6: card},
            marks={6: marks},
        )
        room.players = {"ws-666": player}

        win = find_winning_card(room, player, allow_unmarked=False, target_card_id=6)
        self.assertIsNotNone(win)
        self.assertEqual(win[0], "column")
        self.assertEqual(win[1], 6)
        self.assertEqual(win[2], [0, 5, 10, 15, 20])

    def test_strict_target_card_validation(self):
        """When player specifies target_card_id, ONLY that specific card is checked."""
        room = GameRoom(room_id="room_play_10", name="PLAY", entry_fee=10.0, bingo_rule="line_corners")
        room.phase = GamePhase.PLAYING

        card_a = generate_card_by_id(8)
        card_b = generate_card_by_id(9)
        flat_b = _card_flat(card_b)

        row_0_cells_b = [flat_b[i] for i in _card_row_indexes(0)]
        room.called_numbers = list(row_0_cells_b)

        player = Player(
            telegram_id=888,
            name="TwoCardPlayer",
            ws_id="ws-888",
            card_ids=[8, 9],
            cards={8: card_a, 9: card_b},
            marks={8: {1, 2}, 9: set(_card_row_indexes(0))},
        )
        room.players = {"ws-888": player}

        # Player targets Card 8 which has NOT won
        win = find_winning_card(room, player, allow_unmarked=False, target_card_id=8)
        self.assertIsNone(win)

        # Targeting Card 9 wins
        win_b = find_winning_card(room, player, allow_unmarked=False, target_card_id=9)
        self.assertIsNotNone(win_b)
        self.assertEqual(win_b[1], 9)

    def test_super_bingo_auto_claim_on_call(self):
        """In room_super_50 with rule='full', calling the 24th card number auto-declares BINGO."""
        room = GameRoom(room_id="room_super_50", name="superBingo", entry_fee=50.0, bingo_rule="full")
        room.phase = GamePhase.PLAYING

        card = generate_card_by_id(50)
        flat = _card_flat(card)
        # All 24 card numbers (excluding index 12 / FREE)
        card_numbers = [flat[i] for i in range(25) if i != 12]
        all_card_marks = {i for i in range(25) if i != 12}

        # Player has pre-marked all numbers
        player = Player(
            telegram_id=5050,
            name="SuperWinner",
            ws_id="ws-5050",
            card_ids=[50],
            cards={50: card},
            marks={50: all_card_marks},
        )
        room.players = {"ws-5050": player}

        # First 23 numbers called
        room.called_numbers = list(card_numbers[:23])
        room.available_numbers = [n for n in range(1, 76) if n not in set(room.called_numbers)]

        async def _test():
            # Before 24th number is called, no win
            await server_main.check_all_wins(room)
            self.assertEqual(len(room.bingo_claimants), 0)

            # 24th number is called!
            final_ball = card_numbers[23]
            room.called_numbers.append(final_ball)

            # check_all_wins automatically detects full card win on this ball
            with patch("server.main.finalize_bingo", new=AsyncMock()):
                await server_main.check_all_wins(room)

            self.assertEqual(len(room.bingo_claimants), 1)
            self.assertEqual(room.bingo_claimants[0]["telegram_id"], 5050)
            self.assertEqual(room.bingo_claimants[0]["card_id"], 50)
            self.assertEqual(room.bingo_claimants[0]["pattern"], "full")
            self.assertIsNotNone(room.bingo_window_until)
            self.assertEqual(room.winning_number, final_ball)

        asyncio.run(_test())

    def test_super_bingo_auto_claim_on_mark(self):
        """When player marks the 24th number in room_super_50 after it is called, check_player_win auto-claims."""
        room = GameRoom(room_id="room_super_50", name="superBingo", entry_fee=50.0, bingo_rule="full")
        room.phase = GamePhase.PLAYING

        card = generate_card_by_id(51)
        flat = _card_flat(card)
        card_numbers = [flat[i] for i in range(25) if i != 12]

        # 24 card numbers called, but player only marked 23 so far
        room.called_numbers = list(card_numbers)
        last_ball = card_numbers[-1]
        last_ball_idx = [i for i in range(25) if flat[i] == last_ball][0]

        player_marks = {i for i in range(25) if i != 12 and i != last_ball_idx}
        player = Player(
            telegram_id=5151,
            name="MarkLaterWinner",
            ws_id="ws-5151",
            card_ids=[51],
            cards={51: card},
            marks={51: player_marks},
        )
        room.players = {"ws-5151": player}

        async def _test():
            # Player hasn't marked the 24th number yet -> no win
            won = await server_main.check_player_win(room, player)
            self.assertFalse(won)
            self.assertEqual(len(room.bingo_claimants), 0)

            # Player taps/marks the 24th cell
            r, c = last_ball_idx // 5, last_ball_idx % 5
            server_main.process_player_mark(room, player, 51, r, c, True)

            # check_player_win is called by mark handler
            with patch("server.main.finalize_bingo", new=AsyncMock()):
                won = await server_main.check_player_win(room, player)

            self.assertTrue(won)
            self.assertEqual(len(room.bingo_claimants), 1)
            self.assertEqual(room.bingo_claimants[0]["telegram_id"], 5151)
            self.assertEqual(room.bingo_claimants[0]["pattern"], "full")

        asyncio.run(_test())


if __name__ == "__main__":
    unittest.main()
