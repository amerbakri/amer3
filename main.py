import os
import asyncio
from aiohttp import web
from telegram import Update, Bot
from telegram.ext import Application, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set!")

bot = Bot(token=BOT_TOKEN)
application = Application.builder().token(BOT_TOKEN).build()

# تعريف أمر /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلاً بك في البوت ✅")

application.add_handler(CommandHandler("start", start))

# معالجة التحديث فوراً
async def handle(request):
    if request.method == "POST":
        data = await request.json()
        update = Update.de_json(data, bot)
        # هنا نعالج التحديث فوراً
        await application.process_update(update)
        return web.Response(text="ok")
    return web.Response(status=405)

app = web.Application()
app.router.add_post(f"/{BOT_TOKEN}", handle)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    web.run_app(app, host="0.0.0.0", port=port)
