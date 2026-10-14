"""Super Bingo winning card image renderer using Pillow (PIL).

Produces authentic, high-resolution (760x930) card images styled with Dark Luxury theme
(B-I-N-G-O colorful badges, emerald green marked cells with clear numbers, gold FREE space,
and gold outer border) matching the in-game playing card aesthetics.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from server.game import generate_card_by_id

logger = logging.getLogger(__name__)

# Preferred fonts in order of priority (Windows, Linux, and fallback)
_FONT_CANDIDATES = [
    # Project bundled font if present
    str(Path(__file__).resolve().parent.parent / "assets" / "fonts" / "NotoSansEthiopic-Bold.ttf"),
    # Windows native fonts supporting Ethiopic & clean Latin
    "nyala.ttf",
    "ebrimabd.ttf",
    "ebrima.ttf",
    "arialbd.ttf",
    "arial.ttf",
    "segoeuib.ttf",
    "segoeui.ttf",
    # Linux (Ubuntu / Debian VPS) fonts
    "/usr/share/fonts/truetype/noto/NotoSansEthiopic-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    """Load a truetype font with graceful fallback to scalable default font."""
    for path_or_name in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path_or_name, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: str,
) -> None:
    """Draw text mathematically centered within the given bounding box [x0, y0, x1, y1]."""
    x0, y0, x1, y1 = box
    box_w = x1 - x0
    box_h = y1 - y0
    try:
        bbox = font.getbbox(text)
        left, top, right, bottom = bbox
        text_w = right - left
        text_h = bottom - top
        draw_x = x0 + (box_w - text_w) / 2 - left
        draw_y = y0 + (box_h - text_h) / 2 - top
    except Exception:
        draw_x = x0 + box_w / 4
        draw_y = y0 + box_h / 4
    draw.text((draw_x, draw_y), text, fill=fill, font=font)


def generate_winner_card_image(
    card: list[list[int | None]] | None,
    card_id: int,
    winner_name: str,
    prize: float | int,
    pattern: str = "full",
) -> bytes:
    """Generate a high-res (760x930) JPEG image of the winning Super Bingo card.

    Features:
    - Dark luxury aesthetic: Deep navy background (#100F20) with gold outer border.
    - Super Bingo Winner Header with winner's name, card ID, and prize badge.
    - Authentic B-I-N-G-O colorful column badges (Red, Orange, Green, Blue, Purple).
    - 5x5 Grid: Emerald green marked cells (#00B894) with clean white numbers (no checkmark).
    - Center cell: Rich gold (#F1C40F) with bold 'FREE' text.
    - Footer branding: 'EtooBingo | @etoobingobot'.
    """
    if card is None:
        card = generate_card_by_id(card_id)

    W, H = 760, 930
    img = Image.new("RGB", (W, H), "#100F20")
    draw = ImageDraw.Draw(img)

    # 1. Outer Luxury Gold Border & Card Container
    draw.rounded_rectangle([12, 12, W - 12, H - 12], radius=24, outline="#F1C40F", width=3)

    # Fonts
    font_title = _load_font(32, bold=True)
    font_sub = _load_font(21, bold=True)
    font_prize = _load_font(21, bold=True)
    font_letter = _load_font(32, bold=True)
    font_num = _load_font(36, bold=True)
    font_free = _load_font(23, bold=True)
    font_footer = _load_font(18, bold=False)

    # 2. Top Title
    title_text = "🏆 SUPER BINGO WINNER 🏆"
    _draw_centered_text(draw, (30, 28, W - 30, 72), title_text, font_title, "#FFE066")

    # 3. Winner Info Bar (Name, Card ID, Prize Tag)
    clean_name = (winner_name or "ተጫዋች").strip()
    if len(clean_name) > 16:
        clean_name = clean_name[:15] + "…"
    name_info = f"👤 {clean_name} | መጫወቻ #{card_id}"
    draw.text((45, 92), name_info, fill="#FFFFFF", font=font_sub)

    # Prize pill badge
    prize_val = float(prize)
    if prize_val.is_integer():
        prize_display = f"{int(prize_val):,} ETB"
    else:
        prize_display = f"{prize_val:,.2f} ETB"
    prize_text = f"💰 {prize_display}"

    try:
        pbox = font_prize.getbbox(prize_text)
        pw = pbox[2] - pbox[0]
    except Exception:
        pw = 140

    pill_right = W - 45
    pill_left = pill_right - pw - 24
    draw.rounded_rectangle([pill_left, 84, pill_right, 126], radius=9, fill="#00B894", outline="#00E676", width=1)
    _draw_centered_text(draw, (pill_left, 84, pill_right, 126), prize_text, font_prize, "#FFFFFF")

    # 4. B-I-N-G-O Column Badges
    letters = ["B", "I", "N", "G", "O"]
    colors = ["#FF2A44", "#FF8C00", "#05C46B", "#3742FA", "#9B59B6"]
    grid_left = 45
    grid_top = 145
    grid_w = W - 90  # 670px total grid width
    cell_gap = 7
    cell_w = (grid_w - (4 * cell_gap)) // 5  # ~128px per column
    header_h = 48
    cell_h = 118

    for i, letter in enumerate(letters):
        x0 = grid_left + i * (cell_w + cell_gap)
        y0 = grid_top
        draw.rounded_rectangle(
            [x0, y0, x0 + cell_w, y0 + header_h],
            radius=8,
            fill=colors[i],
        )
        _draw_centered_text(draw, (x0, y0, x0 + cell_w, y0 + header_h), letter, font_letter, "#FFFFFF")

    # 5. 5x5 Number Cells (All marked in Super Bingo Full Card)
    start_y = grid_top + header_h + cell_gap
    for r in range(5):
        for c in range(5):
            x0 = grid_left + c * (cell_w + cell_gap)
            y0 = start_y + r * (cell_h + cell_gap)
            val = card[r][c]

            if r == 2 and c == 2:
                # FREE Center Cell
                draw.rounded_rectangle(
                    [x0, y0, x0 + cell_w, y0 + cell_h],
                    radius=10,
                    fill="#F1C40F",
                    outline="#E67E22",
                    width=2,
                )
                _draw_centered_text(draw, (x0, y0, x0 + cell_w, y0 + cell_h), "FREE", font_free, "#0F0F1A")
            else:
                # Marked Emerald Green Cell with clear number (no checkmark)
                draw.rounded_rectangle(
                    [x0, y0, x0 + cell_w, y0 + cell_h],
                    radius=10,
                    fill="#00B894",
                    outline="#00E676",
                    width=2,
                )
                num_str = str(val if val is not None else "")
                _draw_centered_text(draw, (x0, y0, x0 + cell_w, y0 + cell_h), num_str, font_num, "#FFFFFF")

    # 6. Footer Branding
    footer_text = "EtooBingo | @etoobingobot"
    _draw_centered_text(draw, (40, H - 46, W - 40, H - 18), footer_text, font_footer, "#8E8EA8")

    # Export to in-memory JPEG bytes
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=95, optimize=True)
    return buffer.getvalue()


def generate_winner_card_image_from_winner_dict(
    winner: dict[str, Any],
    pot: float = 0.0,
) -> bytes:
    """Convenience wrapper accepting the winner dictionary from server/game."""
    card_id = int(winner.get("card_id") or 1)
    card = winner.get("card") or generate_card_by_id(card_id)
    name = str(winner.get("name") or f"ተጫዋች #{winner.get('telegram_id', '')}")
    prize = float(winner.get("prize") if winner.get("prize") is not None else pot)
    pattern = str(winner.get("pattern") or "full")
    return generate_winner_card_image(card, card_id, name, prize, pattern)
