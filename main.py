import os
import logging
from flask import Flask, request
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import asyncio

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

# إعداد البوت
bot = Bot(token=BOT_TOKEN)
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# نقطة البداية
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("مرحبا", callback_data="hello")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("أهلاً بك في البوت ✅", reply_markup=reply_markup)

# رد عند الضغط على الأزرار
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text=f"تم الضغط: {query.data}")

# استقبال الرسائل النصية
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أرسل /start للبدء")

# إعداد التطبيق
application = Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(button_handler))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

# Flask Webhook
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
async def webhook() -> str:
    if request.method == "POST":
        await application.update_queue.put(Update.de_json(request.get_json(force=True), bot))
    return "ok"

# تشغيل التطبيق
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8443))
    host = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "localhost")
    app.run(host="0.0.0.0", port=port)
