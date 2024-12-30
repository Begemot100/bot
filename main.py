import logging
import asyncio
import os
from datetime import datetime, timedelta
import random
import sqlite3

import openai
from cryptography.fernet import Fernet
import nest_asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ChatAction

# Apply nest_asyncio for nested event loops
nest_asyncio.apply()
logging.basicConfig(level=logging.INFO)

# Load environment variables
load_dotenv()

# Function to decrypt keys
def load_encrypted_key(env_var_name: str, encryption_key: bytes) -> str:
    try:
        encrypted_value = os.getenv(env_var_name)
        if not encrypted_value:
            raise ValueError(f"{env_var_name} отсутствует в .env файле.")
        cipher_suite = Fernet(encryption_key)
        return cipher_suite.decrypt(encrypted_value.encode()).decode()
    except Exception as e:
        logging.error(f"Ошибка при расшифровке {env_var_name}: {e}")
        raise

# Your encryption key (32 URL-safe Base64-encoded bytes)
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY").encode()

# Load keys
try:
    TELEGRAM_TOKEN = load_encrypted_key("ENCRYPTED_TELEGRAM_TOKEN", ENCRYPTION_KEY)
    OPENAI_API_KEY = load_encrypted_key("ENCRYPTED_OPENAI_API_KEY", ENCRYPTION_KEY)
    openai.api_key = OPENAI_API_KEY
except Exception as e:
    logging.error(f"Ошибка загрузки ключей: {e}")
    raise SystemExit("Не удалось загрузить зашифрованные ключи.")

# APScheduler for reminders
scheduler = AsyncIOScheduler()

# Task storage
tasks = {}

# Initialize database
# Initialize database
def initialize_db():
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()

    # Create the table if it does not exist
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, -- Ensure the user_id column exists
            category TEXT NOT NULL,
            user_message TEXT NOT NULL,
            bot_response TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Check and add missing columns dynamically
    cursor.execute("PRAGMA table_info(interactions)")
    columns = {col[1] for col in cursor.fetchall()}
    if "user_id" not in columns:
        cursor.execute("ALTER TABLE interactions ADD COLUMN user_id INTEGER NOT NULL")
    if "category" not in columns:
        cursor.execute("ALTER TABLE interactions ADD COLUMN category TEXT NOT NULL")
    if "user_message" not in columns:
        cursor.execute("ALTER TABLE interactions ADD COLUMN user_message TEXT NOT NULL")
    if "bot_response" not in columns:
        cursor.execute("ALTER TABLE interactions ADD COLUMN bot_response TEXT NOT NULL")
    conn.commit()
    conn.close()


def save_interaction(user_id, category, user_message, bot_response):
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO interactions (user_id, category, user_message, bot_response)
        VALUES (?, ?, ?, ?)
    """, (user_id, category, user_message, bot_response))
    conn.commit()
    conn.close()

def get_user_queries(user_id, category):
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT user_message, bot_response FROM interactions
        WHERE user_id = ? AND category = ?
    """, (user_id, category))
    rows = cursor.fetchall()
    conn.close()
    return rows

# Menu function
async def main_menu():
    keyboard = [
        [InlineKeyboardButton("Программирование", callback_data="programming")],
        [InlineKeyboardButton("База данных", callback_data="database")],
        [InlineKeyboardButton("Ежедневник", callback_data="diary")],
        [InlineKeyboardButton("Болталка", callback_data="chat")],
    ]
    return InlineKeyboardMarkup(keyboard)

async def animated_start_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    await asyncio.sleep(1)
    keyboard = await main_menu()
    await update.message.reply_text("Выберите категорию:", reply_markup=keyboard)

# Button handler
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    category = query.data
    context.user_data["current_category"] = category

    if category == "programming":
        await query.edit_message_text("Категория: Программирование. Задавайте вопросы, и я постараюсь помочь!")
    elif category == "database":
        await query.edit_message_text("Категория: База данных. Что вас интересует?")
    elif category == "diary":
        await diary_menu(update, context)
    elif category == "chat":
        await query.edit_message_text("Категория: Болталка. Просто поболтаем!")

# Diary function
async def diary_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Введите задачу в формате:\n\n`Задача | 2024-12-30 15:00`", parse_mode="Markdown")
    context.user_data['diary'] = True

async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('diary'):
        return

    try:
        task_text, task_time = update.message.text.split("|")
        task_datetime = datetime.strptime(task_time.strip(), "%Y-%m-%d %H:%M")
        reminder_time = task_datetime - timedelta(minutes=30)

        scheduler.add_job(
            send_reminder,
            trigger=DateTrigger(run_date=reminder_time),
            args=[context, update.message.chat_id, task_text.strip()],
        )
        await update.message.reply_text(f"Задача добавлена: {task_text.strip()}\nНапоминание в: {reminder_time.strftime('%Y-%m-%d %H:%M')}")
        context.user_data['diary'] = False
    except Exception as e:
        await update.message.reply_text(f"Ошибка добавления задачи: {e}")

async def send_reminder(context: ContextTypes.DEFAULT_TYPE, chat_id: int, task_text: str):
    await context.bot.send_message(chat_id=chat_id, text=f"Напоминание: {task_text}")

# Handle text messages based on category
async def start_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text

    if user_message.lower() in ["start", "🚀 старт"]:
        keyboard = await main_menu()
        await update.message.reply_text("Выберите категорию:", reply_markup=keyboard)
        return

    current_category = context.user_data.get("current_category")

    if current_category == "programming":
        await handle_programming_question(update, context, user_message)
    elif current_category == "database":
        await handle_database_question(update, context, user_message)
    elif current_category == "chat":
        await handle_chat(update, context, user_message)
    else:
        keyboard = await main_menu()
        await update.message.reply_text("Сначала выберите категорию из меню!", reply_markup=keyboard)

async def handle_programming_question(update: Update, context: ContextTypes.DEFAULT_TYPE, user_message: str):
    user_id = update.message.chat_id
    previous_queries = get_user_queries(user_id, "programming")

    context_text = "\n".join([f"Вопрос: {q[0]} Ответ: {q[1]}" for q in previous_queries])
    prompt = f"""
    Ты помощник по программированию. Вот история предыдущих вопросов пользователя:\n{context_text}\n
    Теперь ответь на новый вопрос:\n{user_message}
    """

    try:
        response = await openai.ChatCompletion.acreate(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Ты — помощник по программированию."},
                {"role": "user", "content": prompt},
            ],
        )
        bot_reply = response["choices"][0]["message"]["content"]

        # Сохраняем взаимодействие
        save_interaction(user_id, "programming", user_message, bot_reply)

        await update.message.reply_text(bot_reply)
    except Exception as e:
        await update.message.reply_text(f"Произошла ошибка: {e}")

async def handle_database_question(update: Update, context: ContextTypes.DEFAULT_TYPE, user_message: str):
    await update.message.reply_text(f"База данных пока не подключена, но ваш вопрос: {user_message}")

async def handle_chat(update: Update, context: ContextTypes.DEFAULT_TYPE, user_message: str):
    try:
        response = await openai.ChatCompletion.acreate(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Ты дружелюбный собеседник. Поддерживай разговор и будь интересным."},
                {"role": "user", "content": user_message},
            ],
        )
        bot_reply = response["choices"][0]["message"]["content"]
        await update.message.reply_text(bot_reply)
    except Exception as e:
        await update.message.reply_text(f"Произошла ошибка: {e}")

# Main function
async def main():
    initialize_db()
    scheduler.start()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", animated_start_menu))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, start_button_handler))
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())