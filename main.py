import os
import asyncio
from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

bot = Bot(token=BOT_TOKEN)
app = Flask(__name__)

application = Application.builder().token(BOT_TOKEN).build()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"📥 وصلني أمر /start من: {update.effective_user.id}")
    await update.message.reply_text("أهلاً بك في البوت!")

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"📩 رسالة نصية: {update.message.text} من: {update.effective_user.id}")
    await update.message.reply_text(f"رسالتك: {update.message.text}")

application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    asyncio.run(application.update_queue.put(update))
    return "ok"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
