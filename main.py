import os
import logging
import asyncio
from flask import Flask, request
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# التوكن
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

# البوت و Flask
bot = Bot(token=BOT_TOKEN)
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# التطبيق
application = Application.builder().token(BOT_TOKEN).build()

# أمر /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("📥 وصلك /start من:", update.effective_user.first_name)
    keyboard = [[InlineKeyboardButton("مرحبا", callback_data="hello")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("أهلاً بك في البوت ✅", reply_markup=reply_markup)

# زر "مرحبا"
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    print("🔘 تم الضغط على زر:", query.data)
    await query.answer()
    await query.edit_message_text(text=f"تم الضغط: {query.data}")

# رسالة نصية عادية
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"💬 وصلك نص: {update.message.text}")
    await update.message.reply_text("أرسل /start للبدء")

# المعالجات
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(button_handler))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

# Webhook sync
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    print("📡 Webhook تم استدعاؤه ✅")
    asyncio.run(application.update_queue.put(update))
    return "ok"

# تشغيل السيرفر
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8443))
    app.run(host="0.0.0.0", port=port)
