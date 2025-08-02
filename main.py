import os
import asyncio
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
import yt_dlp

# قراءة توكن البوت من متغير البيئة
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set!")

# إنشاء التطبيق
application = Application.builder().token(BOT_TOKEN).build()

# دالة لجلب بيانات الفيديو (metadata)
def get_video_info(url):
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return info

# أمر /info لجلب معلومات الفيديو
async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "يرجى إرسال رابط الفيديو بعد الأمر:\n/info https://youtu.be/..."
        )
        return

    url = context.args[0]
    await update.message.reply_text("جارٍ جلب معلومات الفيديو...")

    try:
        info = get_video_info(url)
        title = info.get('title', 'لا يوجد عنوان')
        duration = info.get('duration_string', 'غير معروف')
        uploader = info.get('uploader', 'غير معروف')
        thumbnail = info.get('thumbnail')

        msg = f"📹 العنوان: {title}\n⏱️ المدة: {duration}\n👤 الناشر: {uploader}"
        await update.message.reply_photo(photo=thumbnail, caption=msg)
    except Exception as e:
        await update.message.reply_text(f"حدث خطأ أثناء جلب المعلومات: {e}")

# أمر /download لتحميل الفيديو وإرساله
async def download_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "يرجى إرسال رابط الفيديو بعد الأمر:\n/download https://youtu.be/..."
        )
        return

    url = context.args[0]
    await update.message.reply_text("⏳ جاري تحميل الفيديو...")

    output_dir = "downloads"
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, f"{update.effective_user.id}.mp4")

    ydl_opts = {
        'format': 'best[height<=720][ext=mp4]/best',
        'outtmpl': output_file,
        'quiet': True,
        'no_warnings': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        with open(output_file, 'rb') as video_file:
            await update.message.reply_video(video_file)
        os.remove(output_file)
    except Exception as e:
        await update.message.reply_text(f"❌ حدث خطأ أثناء التحميل أو الإرسال: {e}")

# زر تفاعلي (مثال)
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text=f"✅ تم الضغط على: {query.data}")

# رد على أي رسالة نصية غير أوامر
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("مرحباً! أرسل /info أو /download مع رابط الفيديو.")

# تسجيل المعالجات
application.add_handler(CommandHandler("info", info_command))
application.add_handler(CommandHandler("download", download_command))
application.add_handler(CallbackQueryHandler(button_handler))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

# aiohttp webhook handler
async def handle(request):
    if request.method == "POST":
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        return web.Response(text="ok")
    return web.Response(status=405)

app = web.Application()
app.router.add_post(f"/{BOT_TOKEN}", handle)

# تهيئة وتشغيل التطبيق بشكل صحيح
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
