import os
import asyncio
import yt_dlp
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set!")

application = Application.builder().token(BOT_TOKEN).build()

def get_formats(url):
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    formats = info.get('formats', [])
    available_formats = []
    for f in formats:
        if f.get('acodec') != 'none' and f.get('vcodec') != 'none':
            resolution = f.get('resolution') or f.get('format_note') or "Unknown"
            filesize = f.get('filesize') or 0
            available_formats.append({
                'format_id': f.get('format_id'),
                'resolution': resolution,
                'filesize': filesize,
            })
    return available_formats

def download_by_format(url, format_id, output_file):
    ydl_opts = {
        'format': format_id,
        'outtmpl': output_file,
        'quiet': True,
        'no_warnings': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text.startswith("https://") or text.startswith("http://"):
        url = text.strip()
        await update.message.reply_text("جارٍ جلب الجودات المتاحة...")
        try:
            formats = get_formats(url)
            buttons = []
            for f in formats:
                size_mb = f['filesize'] / (1024 * 1024) if f['filesize'] else 0
                text_btn = f"{f['resolution']} - {size_mb:.2f} MB" if size_mb > 0 else f"{f['resolution']}"
                buttons.append([InlineKeyboardButton(text_btn, callback_data=f"download:{f['format_id']}:{url}")])
            reply_markup = InlineKeyboardMarkup(buttons)
            await update.message.reply_text("اختر الجودة لتحميل الفيديو:", reply_markup=reply_markup)
        except Exception as e:
            await update.message.reply_text(f"حدث خطأ أثناء جلب الجودات: {e}")
    else:
        # لو حابب ترد على رسائل غير الروابط، اضف هنا
        pass

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("download:"):
        _, format_id, url = data.split(":", 2)
        output_dir = "downloads"
        os.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(output_dir, f"{query.from_user.id}_{format_id}.mp4")

        await query.edit_message_text("⏳ جاري تحميل الفيديو ...")

        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, download_by_format, url, format_id, output_file)
            with open(output_file, 'rb') as video_file:
                await query.message.reply_video(video_file)
            os.remove(output_file)
            await query.edit_message_text("✅ تم تحميل الفيديو وإرساله.")
        except Exception as e:
            await query.edit_message_text(f"❌ حدث خطأ أثناء التحميل: {e}")

application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
application.add_handler(CallbackQueryHandler(callback_handler))

async def handle(request):
    if request.method == "POST":
        data = await request.json()
        update = Update.de_json(data, application.bot)
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
