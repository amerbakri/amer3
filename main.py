import os
import asyncio
from aiohttp import web
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# قراءة توكن البوت من متغير البيئة
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set!")

# إنشاء بوت وتطبيق telegram
bot = Bot(token=BOT_TOKEN)
application = Application.builder().token(BOT_TOKEN).build()

# دالة الرد على الأمر /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("مرحبا 👋", callback_data="hello")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("أهلاً بك في البوت ✅", reply_markup=reply_markup)

# دالة الرد على الضغط على الأزرار
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text=f"✅ تم الضغط على: {query.data}")

# دالة الرد على أي رسالة نصية غير أوامر
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔁 أرسل /start للبدء.")

# تسجيل المعالجات في التطبيق
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(button_handler))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

# تعريف route لاستقبال التحديثات من Telegram (Webhook)
async def handle(request):
    if request.method == "POST":
        data = await request.json()
        update = Update.de_json(data, bot)
        await application.update_queue.put(update)
        return web.Response(text="ok")
    return web.Response(status=405)

# إنشاء تطبيق aiohttp وإضافة route
app = web.Application()
app.router.add_post(f"/{BOT_TOKEN}", handle)

# تشغيل السيرفر
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    web.run_app(app, host="0.0.0.0", port=port)
