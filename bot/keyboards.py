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


def deposit_method_keyboard(webapp_url: str = ""):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔵 Telebirr (ቴሌብር)", callback_data="deposit_telebirr"),
                InlineKeyboardButton("🟢 CBE Birr (ሲቢኢ ብር)", callback_data="deposit_cbebirr"),
            ],
            [
                InlineKeyboardButton("🏦 Mobile Banking (ንግድ ባንክ)", callback_data="deposit_cbe_bank"),
            ],
        ]
    )


def withdraw_method_keyboard(webapp_url: str = ""):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔵 Telebirr (ቴሌብር)", callback_data="withdraw_telebirr"),
                InlineKeyboardButton("🟢 CBE Birr (ሲቢኢ ብር)", callback_data="withdraw_cbebirr"),
            ],
            [
                InlineKeyboardButton("🏦 Mobile Banking (ንግድ ባንክ)", callback_data="withdraw_cbe_bank"),
            ],
        ]
    )


def withdraw_amount_keyboard(method: str):
    """Inline keyboard for quick withdrawal amount selection."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("10 ETB", callback_data=f"wd_amt_{method}_10"),
                InlineKeyboardButton("25 ETB", callback_data=f"wd_amt_{method}_25"),
                InlineKeyboardButton("50 ETB", callback_data=f"wd_amt_{method}_50"),
            ],
            [
                InlineKeyboardButton("100 ETB", callback_data=f"wd_amt_{method}_100"),
                InlineKeyboardButton("200 ETB", callback_data=f"wd_amt_{method}_200"),
                InlineKeyboardButton("500 ETB", callback_data=f"wd_amt_{method}_500"),
            ],
            [
                InlineKeyboardButton("✏️ ሌላ መጠን (Custom)", callback_data=f"wd_amt_{method}_custom"),
            ],
        ]
    )


def deposit_amount_keyboard(method: str):
    """Inline keyboard for quick deposit amount selection."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("10 ETB", callback_data=f"dep_amt_{method}_10"),
                InlineKeyboardButton("25 ETB", callback_data=f"dep_amt_{method}_25"),
                InlineKeyboardButton("50 ETB", callback_data=f"dep_amt_{method}_50"),
            ],
            [
                InlineKeyboardButton("100 ETB", callback_data=f"dep_amt_{method}_100"),
                InlineKeyboardButton("200 ETB", callback_data=f"dep_amt_{method}_200"),
                InlineKeyboardButton("500 ETB", callback_data=f"dep_amt_{method}_500"),
            ],
            [
                InlineKeyboardButton("✏️ ሌላ መጠን (Custom)", callback_data=f"dep_amt_{method}_custom"),
            ],
        ]
    )


def peerpay_pay_keyboard(checkout_url: str, deposit_id: str = ""):
    """Inline button that opens the PeerPay hosted checkout page in Telegram WebApp or browser, plus status check button."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

    buttons = [
        [
            InlineKeyboardButton("💳 በ Telegram ክፈሉ (Pay in App)", web_app=WebAppInfo(url=checkout_url)),
            InlineKeyboardButton("🌐 Browser", url=checkout_url),
        ],
    ]
    if deposit_id:
        buttons.append([InlineKeyboardButton("🔄 ሁኔታውን አረጋግጥ (Check Status)", callback_data=f"dep_status_{deposit_id}")])
    return InlineKeyboardMarkup(buttons)


def peerpay_withdraw_confirm_keyboard(checkout_url: str = "", withdrawal_id: str = ""):
    """Inline button to confirm destination account on PeerPay hosted checkout in Telegram WebApp or browser."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

    buttons = []
    if checkout_url:
        buttons.append([
            InlineKeyboardButton("📱 በ Telegram አረጋግጡ (Confirm in App)", web_app=WebAppInfo(url=checkout_url)),
            InlineKeyboardButton("🌐 Browser", url=checkout_url),
        ])
    if withdrawal_id:
        buttons.append([
            InlineKeyboardButton("🔄 ሁኔታውን አረጋግጥ (Check Status)", callback_data=f"wd_status_{withdrawal_id}")
        ])
    return InlineKeyboardMarkup(buttons)


def balance_action_keyboard():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🎮 አሁን ተጫወት (Play)", callback_data="btn_action_play"),
            ],
            [
                InlineKeyboardButton("💳 ገንዘብ አስገባ (Deposit)", callback_data="btn_action_deposit"),
            ],
            [
                InlineKeyboardButton("💸 ገንዘብ አውጣ (Withdraw)", callback_data="btn_action_withdraw"),
            ],
        ]
    )


def instructions_keyboard():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🎱 10 ብር ጨዋታ (PLAY — 24/7)", callback_data="inst_10"),
            ],
            [
                InlineKeyboardButton("🌟 50 ብር superBingo (ማታ 1:00)", callback_data="inst_50"),
            ],
            [
                InlineKeyboardButton("📖 አጠቃላይ መመሪያ (General Rules)", callback_data="inst_general"),
            ],
        ]
    )


def instructions_back_keyboard():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⬅️ ወደ መመሪያዎች ማውጫ ተመለስ", callback_data="inst_back"),
            ],
            [
                InlineKeyboardButton("🎮 ወደ ጨዋታ ሂድ (Play Now)", callback_data="btn_action_play"),
            ],
        ]
    )


def play_room_keyboard():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎱 PLAY | 10 ብር (24/7 ሁልጊዜ ክፍት)", callback_data="room_play_10")],
            [InlineKeyboardButton("🌟 superBingo | 50 ብር (ማታ 1:00 ሰዓት)", callback_data="room_super_50")],
        ]
    )


def webapp_keyboard(webapp_url: str, room: str):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

    base_url = (webapp_url or "https://etoo-bingo-game.vercel.app").rstrip("/")
    sep = "&" if "?" in base_url else "?"
    backend_api = "https://etoo-bingo-game.onrender.com"
    if "api=" not in base_url:
        full_url = f"{base_url}{sep}room={room}&api={backend_api}"
    else:
        full_url = f"{base_url}{sep}room={room}"

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
