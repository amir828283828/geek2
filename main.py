import logging
import re
import sqlite3
import uuid
from html import escape
from urllib.parse import urlparse

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
CHANNEL_USERNAME = "@sosaworking"   # твой канал

TON_WALLET = "UQBsdCC3QfN8OUim2ym5mKu9lx21F_X7DKA_xnVUWDymW0VR"                 # твой TON-адрес
PRICE_NANOTON = 5_000_000_000        # 1 TON (поменяй сумму если нужно)
TONAPI_KEY = "AG3OQIBPC7OZYLAAAAAFHTLPFKSTBTOKP6LLJ2E7NETOTH6JL7GQWQIDEVIAQ7MD6HYCXQY"                   # ключ с tonapi.io
# Заполни эти значения перед запуском
BOT_TOKEN = "9078325056:AAHmwt_XpTLrvcu9nd6Kc15huT38xAQDxus"
OWNER_ID = 9041049277  # Можно оставить, но больше не обязателен
DB_NAME = "offers.sqlite3"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def connect_db():
    return sqlite3.connect(DB_NAME)


def register_user(user):
    if not user:
        return
    with connect_db() as con:
        con.execute("""
            INSERT INTO users (user_id, username, display_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                display_name = excluded.display_name
        """, (user.id, user.username, user.full_name))


def init_db():
    with connect_db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                username TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                connection_id TEXT NOT NULL,
                display_name TEXT
            )
        """)

        con.execute("""
           CREATE TABLE IF NOT EXISTS access (
           user_id INTEGER PRIMARY KEY,
           paid_until TIMESTAMP,
           tx_hash TEXT,
           created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
""")

        con.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                display_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                user_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (user_id, key)
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS offers (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                connection_id TEXT NOT NULL,
                price INTEGER NOT NULL,
                gift_url TEXT NOT NULL,
                status TEXT NOT NULL,
                buyer_username TEXT
            )
        """)

        # Миграция: добавляем колонку, если её нет
        columns = [row[1] for row in con.execute("PRAGMA table_info(offers)").fetchall()]
        if "buyer_username" not in columns:
            con.execute("ALTER TABLE offers ADD COLUMN buyer_username TEXT")


def valid_tme_link(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and parsed.netloc.lower() in {"t.me", "www.t.me"}


def save_contact(username, chat_id, user_id, connection_id, display_name):
    with connect_db() as con:
        con.execute("""
            INSERT OR REPLACE INTO contacts
            (username, chat_id, user_id, connection_id, display_name)
            VALUES (?, ?, ?, ?, ?)
        """, (username.lower(), chat_id, user_id, connection_id, display_name))


def find_contact(username):
    with connect_db() as con:
        return con.execute("""
            SELECT chat_id, user_id, connection_id, display_name
            FROM contacts WHERE username = ?
        """, (username.lower(),)).fetchone()


def set_setting(user_id, key, value):
    with connect_db() as con:
        con.execute("""
            INSERT INTO settings (user_id, key, value)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value
        """, (user_id, key, value))


def get_setting(user_id, key):
    with connect_db() as con:
        row = con.execute("""
            SELECT value FROM settings WHERE user_id = ? AND key = ?
        """, (user_id, key)).fetchone()
    return row[0] if row else None


async def capture_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.business_message
    if (
        not message
        or not message.business_connection_id
        or message.chat.type != "private"
        or not message.from_user
        or message.from_user.is_bot
        or not message.from_user.username
    ):
        return

    user = message.from_user
    save_contact(
        user.username,
        message.chat_id,
        user.id,
        message.business_connection_id,
        user.full_name,
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user)

    # Проверяем подписку на канал
    is_subscribed = await check_subscription(context, user.id)

    if not is_subscribed:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Подписаться на канал", url=f"https://t.me/sosaworking")],
            [InlineKeyboardButton("✅ Проверить подписку", callback_data="check_sub")]
        ])

        await update.effective_message.reply_text(
            "👋 Для использования бота необходимо быть подписанным на канал!",
            reply_markup=keyboard
        )
        return
    else:
        # Если подписан — показываем обычное меню
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Подписка", callback_data="subscribe")],
            [InlineKeyboardButton("Инструкция", callback_data="instruction")]
        ])

        photo_path = "welcome.png"

        await update.effective_message.reply_photo(
            photo=open(photo_path, "rb"),
            caption="Добро пожаловать! Ты успешно зарегистрирован.",
            reply_markup=keyboard,
        )

async def check_subscription(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """Проверяет, подписан ли пользователь на канал"""
    try:
        member = await context.bot.get_chat_member(
            chat_id=CHANNEL_USERNAME,
            user_id=user_id
        )
        # Статусы подписки
        return member.status in ["member", "administrator", "creator"]
    except Exception:
        return False  # если ошибка (например, бот не в канале или пользователь не найден)

async def check_sub_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    is_subscribed = await check_subscription(context, user_id)

    if is_subscribed:
        await query.edit_message_text(
            "✅ Отлично! Ты подписан на канал.\n\nТеперь можешь пользоваться ботом.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Продолжить", callback_data="back_to_start")
            ]])
        )
    else:
        await query.edit_message_text(
            "❌ Ты всё ещё не подписан на канал\n\nПодпишись и нажми кнопку ниже.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Подписаться", url="https://t.me/sosaworking")],
                [InlineKeyboardButton("✅ Проверить ещё раз", callback_data="check_sub")]
            ])
        )

async def instruction_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    text = (
     "<b>Как отправить оффер:</b>\n\n"
     "В чате с пользователем напиши:\n"
     "<code>жз цена ссылка</code>\n\n"
     "Пример:\n"
     "<code>жз 790 https://t.me/nft/...</code>\n\n"
     "• <b>цена</b> — число от 1 до 1 000 000\n"
     "• <b>ссылка</b> — ссылка на подарок (https://t.me/...)\n\n"
     "После отправки бот удалит твоё сообщение и отправит оффер."
) 

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Назад", callback_data="back_to_start")
    ]])

    try:
        await query.message.delete()
    except Exception:
        pass

    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=text,
        parse_mode="HTML",
        reply_markup=keyboard
    )



async def offer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 3:
        await update.effective_message.reply_text(
            "Формат:\n/offer @username 790 https://t.me/nft/..."
        )
        return

    username = context.args[0].lstrip("@")
    price_text = context.args[1]
    gift_url = context.args[2]

    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
        await update.effective_message.reply_text("Проверь username получателя.")
        return
    if not price_text.isdigit() or not (1 <= int(price_text) <= 1_000_000):
        await update.effective_message.reply_text("Укажи корректную цену.")
        return
    if not valid_tme_link(gift_url):
        await update.effective_message.reply_text("Ссылка на подарок должна быть https://t.me/...")
        return

    contact = find_contact(username)
    if not contact:
        await update.effective_message.reply_text(
            "Username не найден среди сохранённых Business-контактов."
        )
        return

    chat_id, user_id, connection_id, _ = contact
    price = int(price_text)
    buyer_username = update.effective_user.username or ""

    try:
        business = await context.bot.get_business_connection(connection_id)
        if not business.rights or not business.rights.can_reply:
            raise ValueError("У бота нет права can_reply.")
    except Exception as exc:
        await update.effective_message.reply_text(f"Ошибка Business-подключения: {exc}")
        return

    offer_id = uuid.uuid4().hex
    with connect_db() as con:
        con.execute("""
            INSERT INTO offers
            (id, username, chat_id, user_id, connection_id, price, gift_url, status, buyer_username)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """, (offer_id, username.lower(), chat_id, user_id, connection_id, price, gift_url, buyer_username))

    sender_name = escape(update.effective_user.full_name)
    safe_url = escape(gift_url, quote=True)
    gift_name = escape(gift_url.rstrip("/").split("/")[-1])

    text = (
        f"<b>{sender_name}</b> хочет приобрести Ваш подарок "
        f'<a href="{safe_url}"><b>{gift_name}</b></a> 'f'за <b>{price}</b>''<tg-emoji emoji-id="5321485469249198987">⭐</tg-emoji>\n\n'
        'Сделка активна в течение 23ч 59м.'
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Отклонить", callback_data=f"decline:{offer_id}"),
        InlineKeyboardButton("Принять", callback_data=f"accept:{offer_id}"),
    ]])

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard,
            business_connection_id=connection_id,
            link_preview_options=LinkPreviewOptions(
                url=gift_url,
                is_disabled=False,
                show_above_text=True,
                prefer_large_media=True,
            ),
        )
    except Exception as exc:
        with connect_db() as con:
            con.execute("DELETE FROM offers WHERE id = ?", (offer_id,))
        await update.effective_message.reply_text(f"Не удалось отправить: {exc}")
        return

    await update.effective_message.reply_text(f"Предложение отправлено @{username}.")


async def business_offer_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.business_message
    if not message or not message.from_user or message.chat.type != "private" or not message.business_connection_id:
        return

    command_text = (message.text or "").strip()
    parts = command_text.split()
    if not parts or parts[0].lower() != "жз":
    return

    if len(parts) != 4:
    await notify_owner("Формат: жз ссылка сумма зв/грам\nПример: жз https://t.me/nft/... 790 зв")
    return

    gift_url = parts[1]
    price_text = parts[2]
    currency = parts[3].lower()

    if currency not in ("зв", "грам", "звёзды", "звезды"):
    await notify_owner("Валюта должна быть 'зв' или 'грам'")
    return

    is_grams = currency in ("грам",)

    async def notify_owner(text):
        try:
            await context.bot.send_message(chat_id=OWNER_ID, text=text)
        except Exception:
            log.exception("Не удалось уведомить владельца")

    if len(parts) != 3:
        await notify_owner("Формат: жз 790 https://t.me/nft/...")
        return

    price_text, gift_url = parts[1], parts[2]
    if not price_text.isdigit() or not (1 <= int(price_text) <= 1_000_000):
        await notify_owner("Некорректная цена.")
        return
    if not valid_tme_link(gift_url):
        await notify_owner("Некорректная ссылка.")
        return

    chat_id = message.chat.id
    user_id = message.chat.id
    connection_id = message.business_connection_id
    price = int(price_text)
    buyer_username = message.from_user.username or ""

    try:
        business = await context.bot.get_business_connection(connection_id)
        if not business.is_enabled or not business.rights or not business.rights.can_reply:
            raise ValueError("Нет прав или подключение отключено.")
    except Exception as exc:
        await notify_owner(f"Ошибка подключения: {exc}")
        return

    offer_id = uuid.uuid4().hex
    with connect_db() as con:
        con.execute("""
            INSERT INTO offers
            (id, username, chat_id, user_id, connection_id, price, gift_url, status, buyer_username)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """, (offer_id, "", chat_id, user_id, connection_id, price, gift_url, buyer_username))

    sender_name = escape(message.from_user.full_name)
    safe_url = escape(gift_url, quote=True)
    gift_name = escape(gift_url.rstrip("/").split("/")[-1])

    if is_grams:
    emoji = '<tg-emoji emoji-id="5474161364664629753">💎</tg-emoji>'
    currency_name = "Gram"
    else:
    emoji = '<tg-emoji emoji-id="5321485469249198987">⭐</tg-emoji>'
    currency_name = "⭐"

    text = (
    f"<b>{sender_name}</b> хочет приобрести Ваш подарок "
    f'<a href="{safe_url}">{gift_name}</a> '
    f'за <b>{price}</b> {emoji}\n\n'
    'Сделка акт

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Отклонить", callback_data=f"decline:{offer_id}"),
        InlineKeyboardButton("Принять", callback_data=f"accept:{offer_id}"),
    ]])

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard,
            business_connection_id=connection_id,
            link_preview_options=LinkPreviewOptions(
                url=gift_url,
                is_disabled=False,
                prefer_large_media=True,
                show_above_text=True,
            ),
        )
        try:
            await message.delete()
        except Exception:
            pass
        await notify_owner(f"Оффер отправлен. Цена: {price} ⭐")
    except Exception as exc:
        with connect_db() as con:
            con.execute("DELETE FROM offers WHERE id = ?", (offer_id,))
        await notify_owner(f"Ошибка отправки: {exc}")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return

    parts = query.data.split(":", 1)
    if len(parts) != 2:
        await query.answer()
        return

    action, offer_id = parts
    with connect_db() as con:
        row = con.execute("""
            SELECT username, chat_id, user_id, connection_id, status, gift_url, price, buyer_username
            FROM offers WHERE id = ?
        """, (offer_id,)).fetchone()

    if not row:
        await query.answer("Предложение не найдено.", show_alert=True)
        return

    username, chat_id, user_id, connection_id, status, gift_url, price, buyer_username = row

    if query.from_user.id != user_id:
        await query.answer("Это предложение адресовано другому человеку.", show_alert=True)
        return
    if status != "pending":
        await query.answer("На это предложение уже ответили.", show_alert=True)
        return
    if action not in ("accept", "decline"):
        await query.answer()
        return

    new_status = "accepted" if action == "accept" else "declined"
    with connect_db() as con:
        result = con.execute("""
            UPDATE offers SET status = ? WHERE id = ? AND status = 'pending'
        """, (new_status, offer_id))
        if result.rowcount != 1:
            await query.answer("Уже ответили.", show_alert=True)
            return

    await query.answer("Оффер принят." if action == "accept" else "Оффер отклонён.")

    if action == "decline":
        response = "Предложение отклонено."
        keyboard = None
    else:
        gift_name = escape(gift_url.rstrip("/").split("/")[-1])
        safe_url = escape(gift_url, quote=True)
        response = (
            f'<tg-emoji emoji-id="6032937473162614352">🎉</tg-emoji> '
            f'<b>Ваш подарок</b> <a href="{safe_url}">{gift_name}</a> <b>успешно продан!</b>\n\n'
            f'• Передайте данный подарок покупателю для зачисления <b>{price}</b>'
            f'<tg-emoji emoji-id="5321485469249198987">⭐</tg-emoji> '
            f'на ваш баланс.'
        )

        if buyer_username:
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "Передать",
                    url=f"tg://send_gift?to={buyer_username}",
                    icon_custom_emoji_id="5256143829672672750",
                )
            ]])
        else:
            keyboard = None
            response += "\n\nНе удалось определить получателя."
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=response,
            parse_mode="HTML",
            reply_markup=keyboard,
            business_connection_id=connection_id,
            link_preview_options=LinkPreviewOptions(
                url=gift_url,
                is_disabled=False,
                prefer_large_media=True,
                show_above_text=True,
            ),
        )
    except Exception as e:
        log.exception("Ошибка отправки ответа")
        await context.bot.send_message(chat_id=OWNER_ID, text=f"Ошибка после ответа на оффер:\n{e}")


import httpx
from datetime import datetime, timedelta

# ← сюда свои данные
TON_WALLET = "UQBsdCC3QfN8OUim2ym5mKu9lx21F_X7DKA_xnVUWDymW0VR"                 # твой TON-адрес
PRICE_NANOTON = 5_000_000_000        # 1 TON
TONAPI_KEY = "AG3OQIBPC7OZYLAAAAAFHTLPFKSTBTOKP6LLJ2E7NETOTH6JL7GQWQIDEVIAQ7MD6HYCXQY"                   # ключ с tonapi.io


async def has_access(user_id: int) -> bool:
    with connect_db() as con:
        row = con.execute(
            "SELECT paid_until FROM access WHERE user_id = ?",
            (user_id,)
        ).fetchone()
    if not row or not row[0]:
        return False
    paid_until = datetime.fromisoformat(row[0])
    return datetime.utcnow() < paid_until


async def check_and_grant_access(user_id: int) -> bool:
    url = f"https://tonapi.io/v2/blockchain/accounts/{TON_WALLET}/transactions"
    headers = {"Authorization": f"Bearer {TONAPI_KEY}"}

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(url, headers=headers, params={"limit": 30})
            data = r.json()
        except Exception:
            return False

    for tx in data.get("transactions", []):
        in_msg = tx.get("in_msg", {})
        if not in_msg:
            continue
        value = int(in_msg.get("value", 0))
        comment = ""
        if in_msg.get("decoded_body"):
            comment = in_msg["decoded_body"].get("text", "") or ""

        if value >= PRICE_NANOTON and str(user_id) in comment:
            paid_until = (datetime.utcnow() + timedelta(hours=24)).isoformat()
            with connect_db() as con:
                con.execute("""
                    INSERT INTO access (user_id, paid_until, tx_hash)
                    VALUES (?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        paid_until = excluded.paid_until,
                        tx_hash = excluded.tx_hash
                """, (user_id, paid_until, tx.get("hash", "")))
            return True
    return False


async def subscribe_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user

    # Удаляем предыдущее сообщение
    try:
        await query.message.delete()
    except Exception:
        pass

    if await has_access(user.id):
        with connect_db() as con:
            row = con.execute(
                "SELECT paid_until FROM access WHERE user_id = ?",
                (user.id,)
            ).fetchone()
        paid_until = datetime.fromisoformat(row[0])
        remaining = paid_until - datetime.utcnow()
        hours = int(remaining.total_seconds() // 3600)
        minutes = int((remaining.total_seconds() % 3600) // 60)

        text = (
            f"✅ У тебя есть активная подписка.\n"
            f"Осталось: <b>{hours} ч. {minutes} мин.</b>"
        )
    else:
        text = "❌ У тебя нет активной подписки."

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Купить", callback_data="buy_subscription"),
        InlineKeyboardButton("Назад", callback_data="back_to_start")
    ]])

    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=text,
        parse_mode="HTML",
        reply_markup=keyboard
    )

async def buy_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user

    try:
        await query.message.delete()
    except Exception:
        pass

    text = (
        f"<b>Подписка на бота (24 часа)</b>\n\n"
        f"Стоимость: <b>5 Gram</b>\n"
        f"Адрес: <code>{TON_WALLET}</code>\n"
        f"Комментарий: <code>{user.id}</code>\n\n"
        f"Переведи указанную сумму с комментарием и нажми кнопку ниже."
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Я оплатил", callback_data="check_payment"),
        InlineKeyboardButton("Назад", callback_data="back_to_start")
    ]])

    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=text,
        parse_mode="HTML",
        reply_markup=keyboard
    )


async def check_payment_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if await has_access(user_id):
        await query.edit_message_text("Доступ уже активирован.")
        return

    await query.edit_message_text("Проверяю оплату...")
    success = await check_and_grant_access(user_id)

    if success:
        await query.edit_message_text("✅ Оплата найдена! Доступ открыт на 24 часа.")
    else:
        await query.edit_message_text(
            "Оплата пока не найдена. Подожди 1-2 минуты и нажми ещё раз.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Проверить ещё раз", callback_data="check_payment")
            ]])
        )

async def back_to_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        await query.message.delete()
    except Exception:
        pass

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Подписка", callback_data="subscribe")
    ]])

    photo_path = "welcome.png"

    await context.bot.send_photo(
        chat_id=query.message.chat_id,
        photo=open(photo_path, "rb"),
        caption="Добро пожаловать! Ты успешно зарегистрирован.",
        reply_markup=keyboard
    )

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("offer", offer))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, business_offer_text))

    # Сначала специфичные кнопки подписки
    app.add_handler(CallbackQueryHandler(subscribe_button, pattern="^subscribe$"))
    app.add_handler(CallbackQueryHandler(check_payment_button, pattern="^check_payment$"))
    app.add_handler(CallbackQueryHandler(back_to_start, pattern="^back_to_start$"))
    app.add_handler(CallbackQueryHandler(buy_subscription, pattern="^buy_subscription$"))
    app.add_handler(CallbackQueryHandler(instruction_button, pattern="^instruction$"))
    app.add_handler(CallbackQueryHandler(check_sub_button, pattern="^check_sub$"))



    # Потом общий обработчик офферов
    app.add_handler(CallbackQueryHandler(button_handler))

    app.add_handler(MessageHandler(filters.UpdateType.BUSINESS_MESSAGE, capture_business_message), group=1)

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
