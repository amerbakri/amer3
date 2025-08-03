import os
import asyncio
import yt_dlp
import functools
import subprocess
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

COOKIES_FILE = "cookies.txt"  # إذا تستخدم كوكيز (أزل هذا السطر إذا مش محتاج)

application = Application.builder().token(BOT_TOKEN).build()

url_store = {}

def is_url(text: str) -> bool:
    return text.startswith("http://") or text.startswith("https://")

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id
    message_id = str(update.message.message_id)

    if not is_url(text):
        # لو ما كان رابط تجاهل أو رُد برسالة عادية
        await update.message.reply_text("أرسل رابط فيديو للتحميل.")
        return

    url_store[message_id] = text

    keyboard = [
        [InlineKeyboardButton("🎵 صوت فقط", callback_data=f"audio|{message_id}")],
        [InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel|{message_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "⏳ جاري تحميل الفيديو بجودة 720p أو أفضل، انتظر قليلاً...",
        reply_markup=reply_markup
    )

    # ابدأ التحميل في الخلفية بدون انتظار
    loop = asyncio.get_running_loop()
    loop.create_task(download_video_in_background(url=text, msg_id=message_id, context=context, user_id=user_id))

async def download_video_in_background(url, msg_id, context, user_id):
    output_dir = "downloads"
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, f"{msg_id}.mp4")

    # خريطة الجودة: جرب 720p أولًا ثم أفضل جودة متاحة
    ydl_opts_720p = {
        'format': 'bestvideo[height<=720]+bestaudio/best[height<=720]/best',
        'outtmpl': output_file,
        'quiet': True,
        'no_warnings': True,
    }
    ydl_opts_best = {
        'format': 'best',
        'outtmpl': output_file,
        'quiet': True,
        'no_warnings': True,
    }

    try:
        def run_ydl(opts):
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])

        loop = asyncio.get_running_loop()
        # حاول تحميل 720p أولاً
        await loop.run_in_executor(None, functools.partial(run_ydl, ydl_opts_720p))
        # تحقق إذا الملف نزل بنجاح (الحجم > 0)
        if not os.path.exists(output_file) or os.path.getsize(output_file) == 0:
            # جرب تحميل أفضل جودة
            await loop.run_in_executor(None, functools.partial(run_ydl, ydl_opts_best))

        # أرسل الفيديو
        with open(output_file, "rb") as video_file:
            await context.bot.send_video(chat_id=user_id, video=video_file)
    except Exception as e:
        await context.bot.send_message(chat_id=user_id, text=f"❌ حدث خطأ أثناء التحميل: {e}")
    finally:
        # نظف الملف
        if os.path.exists(output_file):
            os.remove(output_file)
        url_store.pop(msg_id, None)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    action, msg_id = data.split("|", 1)

    if action == "cancel":
        await query.message.delete()
        url_store.pop(msg_id, None)
        return

    if action == "audio":
        url = url_store.get(msg_id)
        if not url:
            await query.answer("⚠️ انتهت صلاحية الرابط أو لم يتم العثور عليه.")
            return

        await query.edit_message_text("⏳ جاري تحميل الصوت فقط ...")

        output_dir = "downloads"
        os.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(output_dir, f"{msg_id}.mp3")

        ydl_opts_audio = {
            'format': 'bestaudio/best',
            'outtmpl': output_file,
            'quiet': True,
            'no_warnings': True,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        }

        def run_audio_ydl():
            with yt_dlp.YoutubeDL(ydl_opts_audio) as ydl:
                ydl.download([url])

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, run_audio_ydl)

            with open(output_file, "rb") as audio_file:
                await context.bot.send_audio(chat_id=query.from_user.id, audio=audio_file, caption="🎵 الصوت فقط")
            await query.edit_message_text("✅ تم تحميل الصوت وإرساله.")
        except Exception as e:
            await context.bot.send_message(chat_id=query.from_user.id, text=f"❌ حدث خطأ أثناء تحميل الصوت: {e}")
        finally:
            if os.path.exists(output_file):
                os.remove(output_file)
            url_store.pop(msg_id, None)

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
