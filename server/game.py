import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

# Ethiopian Local Time: East Africa Time (EAT), UTC+03:00
EAT = timezone(timedelta(hours=3))


def get_eat_now() -> datetime:
    """Return current datetime in Ethiopian local time (UTC+3)."""
    return datetime.now(EAT)


def is_super_bingo_open(always_open: bool = False) -> bool:
    """Check if superBingo is currently active (1:00 LT night / 7:00 PM EAT)."""
    if always_open:
        return True
    now = get_eat_now()
    return now.hour == 19


def get_seconds_until_super_bingo() -> int:
    """Return seconds remaining until next 1:00 LT night (7:00 PM EAT)."""
    now = get_eat_now()
    target = now.replace(hour=19, minute=0, second=0, microsecond=0)
    if now.hour == 19:
        return 0
    if now >= target:
        target += timedelta(days=1)
    return max(0, int((target - now).total_seconds()))


COLUMNS = {
    "B": range(1, 16),
    "I": range(16, 31),
    "N": range(31, 46),
    "G": range(46, 61),
    "O": range(61, 76),
}

LETTERS = ["B", "I", "N", "G", "O"]
FREE_INDEX = 12  # center cell in row-major 5x5 grid

MAX_CARDS_PER_PLAYER = 2

CORNERS = (0, 4, 20, 24)  # row-major flat indexes of the four card corners


class GamePhase(str, Enum):
    LOBBY = "lobby"
    PLAYING = "playing"
    FINISHED = "finished"


def number_to_letter(n: int) -> str:
    for letter, values in COLUMNS.items():
        if n in values:
            return letter
    return "?"


def generate_card() -> list[list[int | None]]:
    """Generate a random 5x5 bingo card. Center is FREE (None)."""
    return generate_card_by_id(random.randint(1, 150))


PM_MOD = 2147483647


def _lcg_next(seed: int) -> int:
    """Park–Miller (MINSTD) next state — reproducible in JS for card previews."""
    state = (16807 * seed) % PM_MOD
    return state or 1


def generate_card_by_id(card_id: int) -> list[list[int | None]]:
    """Generate a deterministic 5x5 bingo card based on card_id (1-100).

    Uses a simple LCG instead of the Mersenne Twister so the exact same
    layout can be reproduced in the JavaScript client (webapp/game.js)
    for previews before purchase.
    """
    seed = (card_id * 10007 + 7919) % PM_MOD or 1
    card: list[list[int | None]] = [[None] * 5 for _ in range(5)]
    for col_idx, letter in enumerate(LETTERS):
        pool = list(COLUMNS[letter])
        # Fisher–Yates shuffle using the LCG
        for i in range(len(pool) - 1, 0, -1):
            seed = _lcg_next(seed)
            j = seed % (i + 1)
            pool[i], pool[j] = pool[j], pool[i]
        picks = pool[:5]
        if letter == "N":
            picks[2] = None  # center free space
        for row_idx in range(5):
            card[row_idx][col_idx] = picks[row_idx]
    return card


def card_to_flat(card: list[list[int | None]]) -> list[int | None]:
    return [cell for row in card for cell in row]


def flat_to_card(flat: list[int | None]) -> list[list[int | None]]:
    return [flat[i : i + 5] for i in range(0, 25, 5)]


def check_bingo(card: list[list[int | None]], called: set[int], rule: str = "line") -> str | None:
    """Return win pattern name or None.

    rule:
      - "line":         one full row, column, or diagonal.
      - "line_corners": one line OR all four corners (10 ETB rooms).
      - "corners":      only all four corners.
      - "full":         entire card marked (all 25 numbers/free space for superBingo).
    """
    grid = card_to_flat(card)

    def cell_marked(idx: int) -> bool:
        if idx == FREE_INDEX:
            return True
        val = grid[idx]
        return val is not None and val in called

    if rule == "full":
        return "full" if all(cell_marked(i) for i in range(25)) else None

    if rule in ("line", "line_corners"):
        # rows
        for r in range(5):
            if all(cell_marked(r * 5 + c) for c in range(5)):
                return "row"

        # columns
        for c in range(5):
            if all(cell_marked(r * 5 + c) for r in range(5)):
                return "column"

        # diagonals
        if all(cell_marked(i * 6) for i in range(5)):
            return "diagonal"
        if all(cell_marked((i + 1) * 4) for i in range(5)):
            return "diagonal"

    if rule in ("corners", "line_corners") and all(cell_marked(i) for i in CORNERS):
        return "corners"

    return None


def check_bingo_marked(
    card: list[list[int | None]],
    marks: set[int],
    called: set[int],
    rule: str = "line",
) -> str | None:
    """Win check for manually marked cards.

    `marks` are the flat indexes the player tapped. A tapped cell only counts
    once its number has actually been called (or it is the FREE center), so
    tapping un-called numbers never produces a false win.
    """
    grid = card_to_flat(card)

    def cell_hit(idx: int) -> bool:
        if idx == FREE_INDEX:
            return True
        if idx not in marks or idx < 0 or idx > 24:
            return False
        val = grid[idx]
        return val is not None and val in called

    if rule == "full":
        return "full" if all(cell_hit(i) for i in range(25)) else None

    if rule in ("line", "line_corners"):
        for r in range(5):
            if all(cell_hit(r * 5 + c) for c in range(5)):
                return "row"
        for c in range(5):
            if all(cell_hit(r * 5 + c) for r in range(5)):
                return "column"
        if all(cell_hit(i * 6) for i in range(5)):
            return "diagonal"
        if all(cell_hit((i + 1) * 4) for i in range(5)):
            return "diagonal"

    if rule in ("corners", "line_corners") and all(cell_hit(i) for i in CORNERS):
        return "corners"

    return None


@dataclass
class Player:
    telegram_id: int
    name: str
    ws_id: str
    card_ids: list[int] = field(default_factory=list)
    cards: dict[int, list[list[int | None]]] = field(default_factory=dict)
    marks: dict[int, set[int]] = field(default_factory=dict)  # card_id -> tapped flat indexes
    locked_cards: set[int] = field(default_factory=set)  # card_ids locked due to false BINGO calls
    forfeited: bool = False  # True when all cards are locked

    @property
    def card(self) -> list[list[int | None]] | None:
        if self.cards:
            return next(iter(self.cards.values()))
        return None


@dataclass
class GameRoom:
    room_id: str
    name: str
    entry_fee: float
    house_cut: float = 0.0
    max_cards: int = 150
    call_interval: float = 4.0
    lobby_seconds: int = 30  # 30-second intermission between rounds
    bingo_rule: str = "line"  # passed to check_bingo: line / line_corners / corners / full
    phase: GamePhase = GamePhase.LOBBY
    players: dict[str, Player] = field(default_factory=dict)  # ws_id -> Player
    connections: set[str] = field(default_factory=set)  # ws_ids of all connected clients (spectators + players)
    taken_cards: dict[int, int] = field(default_factory=dict)  # card_id -> telegram_id
    called_numbers: list[int] = field(default_factory=list)
    pot: float = 0.0
    house_income: float = 0.0
    winner_id: int | None = None
    winner_name: str | None = None
    winner_card_id: int | None = None
    countdown: int = 0
    bingo_window_until: float | None = None  # monotonic deadline of the 5s BINGO claim window
    bingo_claimants: list[dict] = field(default_factory=list)  # valid claims inside the window
    available_numbers: list[int] = field(default_factory=lambda: list(range(1, 76)))

    @property
    def called_set(self) -> set[int]:
        return set(self.called_numbers)

    def restore_called_numbers(self, called_numbers: list[int]) -> None:
        """Restore a persisted draw and rebuild the O(1) remaining-number pool."""
        self.called_numbers = list(called_numbers)
        called = set(self.called_numbers)
        self.available_numbers = [number for number in range(1, 76) if number not in called]

    def next_number(self) -> int | None:
        """Draw without rebuilding a 75-number list on every ball."""
        if not self.available_numbers:
            return None
        index = random.randrange(len(self.available_numbers))
        num = self.available_numbers.pop(index)
        self.called_numbers.append(num)
        return num


ROOM_CONFIG = {
    "room_play_10": {
        "name": "PLAY",
        "entry_fee": 10.0,
        "house_cut": 2.0,
        "max_cards": 150,
        "call_interval": 4.0,
        "lobby_seconds": 30,
        "bingo_rule": "line_corners",  # one line OR all four corners
        "schedule": "24/7 (All the time)",
    },
    "room_super_50": {
        "name": "superBingo",
        "entry_fee": 50.0,
        "house_cut": 10.0,
        "max_cards": 1500,
        "call_interval": 3.0,
        "lobby_seconds": 30,
        "bingo_rule": "full",
        "schedule": "Daily at 1:00 LT night (7:00 PM EAT)",
    },
}
