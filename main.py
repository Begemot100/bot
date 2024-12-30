import logging
import asyncio
import os
from datetime import datetime, timedelta
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
except Exception as e:
    logging.error(f"Ошибка загрузки ключей: {e}")
    raise SystemExit("Не удалось загрузить зашифрованные ключи.")

# APScheduler for reminders
scheduler = AsyncIOScheduler()

# Task storage
tasks = {}

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

# Programming handler
async def programming_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Категория: Программирование. Задавайте вопросы, и я постараюсь помочь!")

# Database handler
async def database_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Категория: База данных. Чем могу помочь?")

# Chat handler
async def chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Категория: Болталка. О чём хотите поговорить?")

# Callback query handler
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    category = query.data

    if category == "programming":
        await programming_handler(update, context)
    elif category == "database":
        await database_handler(update, context)
    elif category == "diary":
        await diary_menu(update, context)
    elif category == "chat":
        await chat_handler(update, context)

# Main function
async def main():
    scheduler.start()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", animated_start_menu))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, add_task))
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
