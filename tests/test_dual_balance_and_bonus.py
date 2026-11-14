"""Unit tests for Policy B Dual-Balance system and 50% First-Deposit Welcome Bonus."""

import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from bot import database as db


class TestDualBalanceAndBonus(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await db.close_db()
        self._tmp = tempfile.mkdtemp()
        self._orig = db.settings
        db.settings = SimpleNamespace(database_path=Path(self._tmp) / "test_dual.db")
        await db.init_db()

    async def asyncTearDown(self):
        await db.close_db()
        db.settings = self._orig

    async def test_policy_b_grandfathering(self):
        """Existing users with balance > 0 should have win_balance = balance, deposit_balance = 0.0."""
        uid = 9988001
        await db.create_user(uid, "+251911000001", "old_user", "Old")
        await db.update_balance(uid, 250.0)

        user = await db.get_user(uid)
        self.assertEqual(float(user["balance"]), 250.0)
        self.assertEqual(float(user.get("win_balance", 0.0)), 250.0)
        self.assertEqual(float(user.get("deposit_balance", 0.0)), 0.0)

    async def test_first_deposit_50_percent_bonus(self):
        """First deposit gets 50% bonus added to deposit_balance."""
        uid = 9988002
        await db.create_user(uid, "+251911000002", "new_user", "New")

        user = await db.get_user(uid)
        self.assertEqual(float(user["balance"]), 0.0)
        self.assertFalse(bool(user.get("first_deposit_bonus_claimed", 0)))

        # 1st deposit: 100 ETB
        credited, new_bal, is_dup = await db.credit_peerpay_deposit_once(
            payment_id="dep_test_001",
            telegram_id=uid,
            amount=100.0,
        )
        self.assertTrue(credited)
        self.assertFalse(is_dup)
        # 100 + 50% bonus (50) = 150 total
        self.assertEqual(new_bal, 150.0)

        user = await db.get_user(uid)
        self.assertEqual(float(user["deposit_balance"]), 150.0)
        self.assertEqual(float(user["win_balance"]), 0.0)
        self.assertEqual(float(user["balance"]), 150.0)
        self.assertEqual(float(user["total_deposited"]), 100.0)  # Real cash deposited is 100
        self.assertTrue(bool(user.get("first_deposit_bonus_claimed", 0)))

        # 2nd deposit: 100 ETB (should NOT get bonus)
        credited2, new_bal2, is_dup2 = await db.credit_peerpay_deposit_once(
            payment_id="dep_test_002",
            telegram_id=uid,
            amount=100.0,
        )
        self.assertTrue(credited2)
        # 150 + 100 = 250
        self.assertEqual(new_bal2, 250.0)

        user2 = await db.get_user(uid)
        self.assertEqual(float(user2["deposit_balance"]), 250.0)
        self.assertEqual(float(user2["win_balance"]), 0.0)
        self.assertEqual(float(user2["balance"]), 250.0)
        self.assertEqual(float(user2["total_deposited"]), 200.0)

    async def test_partial_card_deduction(self):
        """Card deduction takes from deposit_balance first, and remainder from win_balance."""
        uid = 9988003
        await db.create_user(uid, "+251911000003", "player_split", "Split")
        # User has: deposit_balance = 4.0, win_balance = 20.0 (Total = 24.0)
        await db.update_user_balances(uid, deposit_balance=4.0, win_balance=20.0)

        user = await db.get_user(uid)
        self.assertEqual(float(user["deposit_balance"]), 4.0)
        self.assertEqual(float(user["win_balance"]), 20.0)
        self.assertEqual(float(user["balance"]), 24.0)

        # Deduct 10 ETB for a card
        deduct_res = await db.deduct_balance(uid, 10.0, "Test card deduction")
        ok, new_bal = deduct_res
        self.assertTrue(ok)
        self.assertEqual(new_bal, 14.0)
        self.assertEqual(deduct_res.deposit_deducted, 4.0)
        self.assertEqual(deduct_res.win_deducted, 6.0)

        user_after = await db.get_user(uid)
        self.assertEqual(float(user_after["deposit_balance"]), 0.0)
        self.assertEqual(float(user_after["win_balance"]), 14.0)
        self.assertEqual(float(user_after["balance"]), 14.0)

        # Test refunding exact split (e.g. unselect card)
        new_refund_bal = await db.refund_balance(
            uid,
            deposit_amount=deduct_res.deposit_deducted,
            win_amount=deduct_res.win_deducted,
            description="Refund test card",
        )
        self.assertEqual(new_refund_bal, 24.0)
        user_refunded = await db.get_user(uid)
        self.assertEqual(float(user_refunded["deposit_balance"]), 4.0)
        self.assertEqual(float(user_refunded["win_balance"]), 20.0)
        self.assertEqual(float(user_refunded["balance"]), 24.0)

    async def test_withdrawal_restriction_win_balance_only(self):
        """User cannot withdraw deposit_balance, only win_balance."""
        uid = 9988004
        await db.create_user(uid, "+251911000004", "withdrawer", "Wd")
        # 100 in deposit_balance, 0 in win_balance
        await db.update_user_balances(uid, deposit_balance=100.0, win_balance=0.0)

        # Attempt to hold 50 ETB for withdrawal -> should fail because win_balance is 0
        ok, bal = await db.hold_withdrawal_balance(uid, 50.0)
        self.assertFalse(ok)

        # Now simulate user winning 80 ETB in a game
        new_bal = await db.credit_win(uid, 80.0, "Won game")
        self.assertEqual(new_bal, 180.0)
        user = await db.get_user(uid)
        self.assertEqual(float(user["deposit_balance"]), 100.0)
        self.assertEqual(float(user["win_balance"]), 80.0)
        self.assertEqual(float(user["balance"]), 180.0)
        self.assertEqual(float(user["total_won"]), 80.0)

        # Hold 50 ETB for withdrawal -> should succeed because win_balance is 80
        ok, bal_after_hold = await db.hold_withdrawal_balance(uid, 50.0)
        self.assertTrue(ok)
        self.assertEqual(bal_after_hold, 130.0)

        user_after_hold = await db.get_user(uid)
        self.assertEqual(float(user_after_hold["deposit_balance"]), 100.0)
        self.assertEqual(float(user_after_hold["win_balance"]), 30.0)
        self.assertEqual(float(user_after_hold["balance"]), 130.0)

        # Attempt to hold another 40 ETB -> should fail (only 30 in win_balance)
        ok_fail, _ = await db.hold_withdrawal_balance(uid, 40.0)
        self.assertFalse(ok_fail)


    async def test_game_win_credits_win_balance_strictly(self):
        """Game winnings MUST go 100% into win_balance (withdrawable), never deposit_balance."""
        from server.main import finalize_bingo, get_room
        from server.game import GamePhase
        uid = 9988005
        await db.create_user(uid, "+251911000005", "game_winner", "Winner")
        # Player has 50 ETB in deposit_balance, 0 in win_balance
        await db.update_user_balances(uid, deposit_balance=50.0, win_balance=0.0)

        # Set up a room where this player wins
        room = get_room("classic")
        room.phase = GamePhase.PLAYING
        room.pot = 100.0
        room.bingo_claimants = [{
            "telegram_id": uid,
            "name": "Winner",
            "card_id": 1,
            "pattern": "Line 1",
            "called_count": 15,
        }]

        # Finalize bingo (run without 5s delay by calling db.credit_win as finalize_bingo does)
        new_bal = await db.credit_win(uid, 100.0, "Won classic with Card #1")
        self.assertEqual(new_bal, 150.0)

        user = await db.get_user(uid)
        # deposit_balance must still be 50.0!
        self.assertEqual(float(user["deposit_balance"]), 50.0)
        # win_balance must be 100.0!
        self.assertEqual(float(user["win_balance"]), 100.0)
        self.assertEqual(float(user["balance"]), 150.0)
        self.assertEqual(float(user["total_won"]), 100.0)

        # Player can withdraw from the 100 ETB won
        ok, bal_after = await db.hold_withdrawal_balance(uid, 100.0)
        self.assertTrue(ok)
        self.assertEqual(bal_after, 50.0)
        user_after = await db.get_user(uid)
        self.assertEqual(float(user_after["win_balance"]), 0.0)
        self.assertEqual(float(user_after["deposit_balance"]), 50.0)


if __name__ == "__main__":
    unittest.main()

