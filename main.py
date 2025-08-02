import os
import asyncio
from aiohttp import web
from telegram import Update, Bot
from telegram.ext import Application, ContextTypes, CommandHandler

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set!")

bot = Bot(token=BOT_TOKEN)
application = Application.builder().token(BOT_TOKEN).build()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلاً بك في البوت ✅")

application.add_handler(CommandHandler("start", start))

async def handle(request):
    if request.method == "POST":
        data = await request.json()
        update = Update.de_json(data, bot)
        await application.process_update(update)
        return web.Response(text="ok")
    return web.Response(status=405)

app = web.Application()
app.router.add_post(f"/{BOT_TOKEN}", handle)

async def on_startup(app):
    await application.initialize()
    await application.start()

async def on_cleanup(app):
    await application.stop()
    await application.shutdown()

app.on_startup.append(on_startup)
app.on_cleanup.append(on_cleanup)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    web.run_app(app, host="0.0.0.0", port=port)
