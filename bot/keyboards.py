from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

from bot import messages as msg


def phone_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Share Phone", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["/play", "/balance"],
            ["/deposit", "/withdraw"],
            ["/history", "/instructions"],
        ],
        resize_keyboard=True,
    )


def deposit_method_keyboard():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔵 Telebirr (ቴሌብር)", callback_data="deposit_telebirr")],
            [InlineKeyboardButton("🟢 CBE Birr (ሲቢኢ ብር)", callback_data="deposit_cbebirr")],
            [InlineKeyboardButton("🏦 Mobile Banking (ንግድ ባንክ)", callback_data="deposit_cbe_bank")],
        ]
    )


def withdraw_method_keyboard():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔵 Telebirr (ቴሌብር)", callback_data="withdraw_telebirr")],
            [InlineKeyboardButton("🟢 CBE Birr (ሲቢኢ ብር)", callback_data="withdraw_cbebirr")],
            [InlineKeyboardButton("🏦 Mobile Banking (ንግድ ባንክ)", callback_data="withdraw_cbe_bank")],
        ]
    )


def peerpay_pay_keyboard(checkout_url: str):
    """Single inline button that opens the PeerPay hosted checkout page."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "💳 Pay on PeerPay",
                    url=checkout_url,
                )
            ]
        ]
    )


def peerpay_withdraw_confirm_keyboard(checkout_url: str):
    """Inline button to confirm destination account on PeerPay hosted checkout."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🏦 Confirm Destination Account",
                    url=checkout_url,
                )
            ]
        ]
    )


def play_room_keyboard():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎱 PLAY | 10 birr (24/7)", callback_data="room_play_10")],
            [InlineKeyboardButton("🌟 superBingo | 50 birr (1:00 LT Night)", callback_data="room_super_50")],
        ]
    )


def webapp_keyboard(webapp_url: str, room: str):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

    base_url = (webapp_url or "https://example.com").rstrip("/")
    full_url = f"{base_url}/?room={room}"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🎮 Open Game",
                    web_app=WebAppInfo(url=full_url),
                )
            ]
        ]
    )


def telebirr_pay_keyboard(checkout_url: str):
    """Single inline button that opens the Telebirr H5 checkout page."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "💳 Pay with Telebirr",
                    url=checkout_url,
                )
            ]
        ]
    )


def verify_bank_keyboard():
    """/verify bank selection: CBE Birr or TeleBirr."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🟢 CBE BIRR", callback_data="verify_cbebirr")],
            [InlineKeyboardButton("🔵 TELE BIRR", callback_data="verify_telebirr")],
        ]
    )
