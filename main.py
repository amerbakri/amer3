import os
import asyncio
from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

bot = Bot(token=BOT_TOKEN)
app = Flask(__name__)

application = Application.builder().token(BOT_TOKEN).build()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"📥 Got /start from {update.effective_user.id}")
    await update.message.reply_text("أهلاً! تم استقبال /start بنجاح.")

application.add_handler(CommandHandler("start", start))

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    data = request.get_json(force=True)
    print(f"📩 Incoming update: {data}")
    update = Update.de_json(data, bot)
    asyncio.run(application.update_queue.put(update))
    return "ok"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
