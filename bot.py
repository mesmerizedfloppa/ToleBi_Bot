# bot.py — Telegram-бот для линейной нарративной игры-исповеди
# python-telegram-bot v20.x (async/await)
# ДОПОЛНЕН: фичи 2-9, исправлены ошибки Optional

import asyncio
import logging
import sqlite3
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

from scenes import get_scene, get_first_scene_id, get_total_scenes_count

# ─────────────────────────────────────────────────────────────────────────────
# КОНФИГУРАЦИЯ — замените на реальный токен бота от @BotFather
# ─────────────────────────────────────────────────────────────────────────────
BOT_TOKEN = "8608671773:AAEC7k-BiX_ox33uQVEpzI9domxdPCr23us"

DB_PATH = Path(__file__).parent / "progress.db"
LOG_FILE = Path(__file__).parent / "bot.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

STATE_WAITING_CONFIRM = 1

# ─────────────────────────────────────────────────────────────────────────────
# База данных
# ─────────────────────────────────────────────────────────────────────────────
def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_progress (
                user_id INTEGER PRIMARY KEY,
                current_scene INTEGER NOT NULL DEFAULT 0
            )
        """)
    logger.info("База данных инициализирована: %s", DB_PATH)

def get_user_scene(user_id: int) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT current_scene FROM user_progress WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return row[0] if row else 0

def set_user_scene(user_id: int, scene_id: int) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO user_progress (user_id, current_scene) "
            "VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET current_scene = ?",
            (user_id, scene_id, scene_id),
        )
        conn.commit()

def reset_user_progress(user_id: int) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM user_progress WHERE user_id = ?", (user_id,))

# ─────────────────────────────────────────────────────────────────────────────
# Клавиатуры
# ─────────────────────────────────────────────────────────────────────────────
def build_keyboard(buttons: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton(text, callback_data=data)] for text, data in buttons]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def build_scene_keyboard(scene: dict) -> InlineKeyboardMarkup | None:
    if scene.get("next_scene") is None:
        return None
    text = scene.get("button_text", "Дальше")
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text, callback_data="next")]])

def build_start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton("Начать исповедь", callback_data="start_story")]])

def build_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("Да, начать сначала", callback_data="restart_yes")],
        [InlineKeyboardButton("Продолжить", callback_data="continue_story")]
    ])

def build_restart_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton("Начать заново", callback_data="start_story")]])

# ─────────────────────────────────────────────────────────────────────────────
# Reply-клавиатура для пользователя (внизу экрана)
# ─────────────────────────────────────────────────────────────────────────────
def build_user_keyboard() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton("🔙 Назад")],
        [KeyboardButton("📊 Статус"), KeyboardButton("❓ Помощь")],
        [KeyboardButton("🔄 Сбросить")],
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# ─────────────────────────────────────────────────────────────────────────────
# Отправка сцены (расширенная)
# ─────────────────────────────────────────────────────────────────────────────
async def send_scene(update: Update, scene: dict, user_id: int) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat:
        return

    scene_text = scene.get("text", "")
    image_url = scene.get("image_url")
    audio_url = scene.get("audio_url")
    has_next = scene.get("next_scene") is not None
    delay = scene.get("delay_before_button", 0)

    if delay > 0:
        logger.info("Пауза %d сек. перед сценой %d", delay, scene["scene_id"])
        for _ in range(delay // 3):
            await chat.send_chat_action("typing")
            await asyncio.sleep(3)
        await asyncio.sleep(delay % 3)

    # Клавиатура — fake_choices, shaky_button, или обычная
    keyboard = None
    shaky_button = scene.get("shaky_button")
    fake_choices = scene.get("fake_choices")
    
    logger.info("SEND_SCENE: scene_id=%d, has_next=%s", scene.get("scene_id"), has_next)
    
    update_shaky_later = False
    if fake_choices:
        # Выборы показываем всегда (даже если это финальная сцена)
        keyboard = build_keyboard(fake_choices)
    elif has_next:
        if shaky_button:
            # Дрожащая кнопка
            temp_keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton("Я не могу...", callback_data="shaky")]]
            )
            keyboard = temp_keyboard
            update_shaky_later = True
        else:
            keyboard = build_scene_keyboard(scene)
    else:
        keyboard = None

    # Отправка контента с кнопкой под текстом
    if image_url:
        try:
            await msg.reply_photo(photo=image_url, caption=scene_text, parse_mode="Markdown", reply_markup=keyboard)
        except Exception as e:
            logger.warning("Ошибка картинки: %s", e)
            try:
                await msg.reply_text(scene_text, parse_mode="Markdown", reply_markup=keyboard)
            except Exception:
                pass
    elif audio_url:
        # Сначала текст с кнопкой, потом аудио отдельно
        if scene_text:
            await msg.reply_text(scene_text, parse_mode="Markdown", reply_markup=keyboard)
        try:
            await msg.reply_audio(
                audio=audio_url,
                performer=scene.get("audio_performer") or "Unknown",
                title=scene.get("audio_title") or "Аудио",
            )
        except Exception as e:
            logger.warning("Ошибка аудио: %s", e)
    else:
        if scene_text:
            await msg.reply_text(scene_text, parse_mode="Markdown", reply_markup=keyboard)

    # Обновляем дрожащую кнопку на нормальную (если была)
    if update_shaky_later:
        await asyncio.sleep(2)
        normal_keyboard = build_scene_keyboard(scene)
        if scene_text:
            # Редактируем кнопку в последнем сообщении (с текстом)
            await msg.edit_reply_markup(reply_markup=normal_keyboard)

    # Флэшбек (отдельным сообщением, без кнопки — кнопка уже была)
    flashback_text = scene.get("flashback_text")
    flashback_delay = scene.get("flashback_delay", 0)
    if flashback_text:
        await asyncio.sleep(1)
        await chat.send_chat_action("typing")
        await asyncio.sleep(flashback_delay)
        await msg.reply_text(flashback_text, parse_mode="Markdown")

    # Сообщение о конце истории (только если нет fake_choices — они сами покажут текст)
    if not has_next and not fake_choices:
        await chat.send_message(
            text="🔚 *Конец исповеди.*\n\nИспользуй /start, чтобы начать заново.",
            parse_mode="Markdown"
        )

    set_user_scene(user_id, scene["scene_id"])

# ─────────────────────────────────────────────────────────────────────────────
# Обработчики команд
# ─────────────────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message
    if not msg:
        return ConversationHandler.END
    user = update.effective_user
    if not user:
        return ConversationHandler.END
    user_id = user.id
    current_scene = get_user_scene(user_id)

    if current_scene > 0:
        await msg.reply_text(
            f"Ты уже на *сцене {current_scene}* из {get_total_scenes_count()}.\n\n"
            "Начать сначала?",
            parse_mode="Markdown",
            reply_markup=build_confirm_keyboard(),
        )
        await msg.reply_text("Используй кнопки внизу экрана:", reply_markup=build_user_keyboard())
        return STATE_WAITING_CONFIRM
    else:
        await msg.reply_text(
            "*Добро пожаловать.*\n\n"
            "Это история, которую я никому не рассказывал.\n"
            "Никому, кроме тебя.",
            parse_mode="Markdown",
            reply_markup=build_start_keyboard(),
        )
        await msg.reply_text("Используй кнопки внизу экрана:", reply_markup=build_user_keyboard())
    return ConversationHandler.END

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg:
        return
    user = update.effective_user
    if not user:
        return
    user_id = user.id
    current_scene = get_user_scene(user_id)
    total = get_total_scenes_count()
    if current_scene == 0:
        text = "Ты ещё не начинал исповедь. Нажми /start."
    else:
        text = (
            f"Текущая сцена: *{current_scene}* из *{total}*\n"
            f"Пройдено: *{(current_scene / total * 100):.0f}%*"
        )
    await msg.reply_text(text, parse_mode="Markdown")

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg:
        return
    user = update.effective_user
    if not user:
        return
    user_id = user.id
    reset_user_progress(user_id)
    logger.info("Прогресс сброшен для пользователя %d", user_id)
    await msg.reply_text(
        "Прогресс сброшен. Ты начинаешь сначала.",
        reply_markup=build_restart_keyboard(),
    )

# ─────────────────────────────────────────────────────────────────────────────
# Обработчик кнопок
# ─────────────────────────────────────────────────────────────────────────────
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    user = update.effective_user
    if not user:
        return
    user_id = user.id
    data = query.data
    logger.info("Пользователь %d нажал кнопку: %s", user_id, data)

    if data == "start_story":
        first_scene = get_first_scene_id()
        scene = get_scene(first_scene)
        if scene:
            await send_scene(update, scene, user_id)
    elif data == "continue_story":
        current = get_user_scene(user_id)
        if current > 0:
            scene = get_scene(current)
            if scene:
                await send_scene(update, scene, user_id)
    elif data == "restart_yes":
        reset_user_progress(user_id)
        first_scene = get_first_scene_id()
        scene = get_scene(first_scene)
        if scene:
            await send_scene(update, scene, user_id)
    elif data == "next":
        current = get_user_scene(user_id)
        logger.info("NEXT: current=%d", current)
        scene = get_scene(current)
        if scene and scene.get("next_scene") is not None:
            next_id = scene["next_scene"]
            logger.info("NEXT: going to scene %d", next_id)
            next_scene = get_scene(next_id)
            if next_scene:
                await send_scene(update, next_scene, user_id)
        else:
            await query.edit_message_reply_markup(reply_markup=None)
            chat = update.effective_chat
            if chat:
                await chat.send_message(
                    "Эта часть истории уже позади. /status — проверить прогресс."
                )
    elif data == "end":
        await query.edit_message_text("🔚 *Исповедь окончена.* Степь помнит.")
        chat = update.effective_chat
        if chat:
            await chat.send_message("Начать заново? /start")
    elif data == "shaky":
        # Дрожащая кнопка нажата — переходим дальше
        current = get_user_scene(user_id)
        scene = get_scene(current)
        if scene and scene.get("next_scene") is not None:
            next_id = scene["next_scene"]
            next_scene = get_scene(next_id)
            if next_scene:
                await send_scene(update, next_scene, user_id)
    else:
        # Ложный выбор или вариант с реакцией
        chat = update.effective_chat
        current = get_user_scene(user_id)
        scene = get_scene(current)
        if scene:
            # Проверяем choice_reactions
            choice_reactions = scene.get("choice_reactions")
            if choice_reactions and data in choice_reactions:
                if chat:
                    await chat.send_message(choice_reactions[data], parse_mode="Markdown")
                await asyncio.sleep(2)
            
            # Переход к следующей сцене
            if scene.get("next_scene") is not None:
                next_id = scene["next_scene"]
                next_scene = get_scene(next_id)
                if next_scene:
                    await send_scene(update, next_scene, user_id)
            else:
                # Финальная сцена
                if chat:
                    await chat.send_message(
                        "🔚 *Конец исповеди.*\n\nИспользуй /start, чтобы начать заново.",
                        parse_mode="Markdown"
                    )
        else:
            logger.warning("Неизвестный callback_data: %s", data)

# ─────────────────────────────────────────────────────────────────────────────
# Пасхальные яйца
# ─────────────────────────────────────────────────────────────────────────────
async def easter_egg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg:
        return
    if not msg.text:
        return
    text = msg.text.strip()
    
    # Reply-кнопки — отправляем в отдельные хендлеры
    if text == "🔙 Назад":
        await user_back_handler(update, context)
        return
    elif text == "📊 Статус":
        await user_status_handler(update, context)
        return
    elif text == "❓ Помощь":
        await user_help_handler(update, context)
        return
    elif text == "🔄 Сбросить":
        await user_reset_handler(update, context)
        return
    
    # Пасхальные яйца
    if text.lower() == "домбра":
        await msg.reply_audio("https://your-hosting.com/media/random_kui.mp3")
    elif text.lower() == "кто ты?":
        await msg.reply_text("Я Толе. Тот, кто убил брата.")
    elif text.lower() == "тенгри":
        await msg.reply_text("Тенгри молчит. Он всегда молчит, когда приходят палачи.")
    else:
        await echo_handler(update, context)

# ─────────────────────────────────────────────────────────────────────────────
# Обычный обработчик текста
# ─────────────────────────────────────────────────────────────────────────────
async def echo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg:
        return
    user = update.effective_user
    if not user:
        return
    user_id = user.id
    current = get_user_scene(user_id)

    if current > 0:
        scene = get_scene(current)
        if scene and scene.get("next_scene") is not None:
            await msg.reply_text(
                "Нажми кнопку, чтобы продолжить.",
                reply_markup=build_scene_keyboard(scene),
            )
    else:
        await msg.reply_text("Используй /start, чтобы начать.")

# ─────────────────────────────────────────────────────────────────────────────
# Обработчики Reply-кнопок пользователя
# ─────────────────────────────────────────────────────────────────────────────
async def user_back_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопка 'Назад' — заглушка, текст добавишь сам."""
    msg = update.message
    if not msg:
        return
    await msg.reply_text(
        "🔙 *Назад нельзя.*\n\nЭто исповедь. Нельзя перемотать назад.",
        parse_mode="Markdown"
    )

async def user_status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопка 'Статус'."""
    msg = update.message
    if not msg:
        return
    user = update.effective_user
    if not user:
        return
    user_id = user.id
    current = get_user_scene(user_id)
    total = get_total_scenes_count()
    if current == 0:
        text = "Ты ещё не начинал исповедь. Нажми /start."
    else:
        text = f"Текущая сцена: *{current}* из *{total}*\nПройдено: *{(current / total * 100):.0f}%*"
    await msg.reply_text(text, parse_mode="Markdown")

async def user_help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопка 'Помощь'."""
    msg = update.message
    if not msg:
        return
    await msg.reply_text(
        "*Помощь*\n\n"
        "🔙 Назад — вернуться (не работает, история линейная)\n"
        "📊 Статус — показать прогресс\n"
        "❓ Помощь — эта подсказка\n"
        "🔄 Сбросить — начать сначала\n\n"
        "/start — начать исповедь\n"
        "/status — прогресс\n"
        "/reset — сбросить",
    )

async def user_reset_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопка 'Сбросить'."""
    msg = update.message
    if not msg:
        return
    user = update.effective_user
    if not user:
        return
    user_id = user.id
    reset_user_progress(user_id)
    await msg.reply_text(
        "Прогресс сброшен. Начинаем сначала.",
        reply_markup=build_start_keyboard()
    )

# ─────────────────────────────────────────────────────────────────────────────
# ConversationHandler
# ─────────────────────────────────────────────────────────────────────────────
conv_handler = ConversationHandler(
    entry_points=[CommandHandler("start", cmd_start)],
    states={
        STATE_WAITING_CONFIRM: [CallbackQueryHandler(button_handler)],
    },
    fallbacks=[CommandHandler("start", cmd_start)],
)

# ─────────────────────────────────────────────────────────────────────────────
# Главная функция
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    init_db()
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("reset", cmd_reset))
    application.add_handler(CallbackQueryHandler(button_handler))
    # Reply-кнопки пользователя (проверяем вручную в хендлерах)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, easter_egg_handler))

    logger.info("Бот запускается...")
    print("Бот запущен. Нажми Ctrl+C для остановки.")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()