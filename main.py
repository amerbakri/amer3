import os
import json
import asyncio
import functools
import yt_dlp
import openai
from aiohttp import web
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not BOT_TOKEN or not OPENAI_API_KEY:
    raise RuntimeError("BOT_TOKEN و OPENAI_API_KEY لازم يكونوا مضبوطين في المتغيرات البيئية.")

openai.api_key = OPENAI_API_KEY

SUBSCRIPTIONS_FILE = "subscriptions.json"
USERS_FILE = "users.txt"
url_store = {}

application = Application.builder().token(BOT_TOKEN).build()

def load_subscriptions():
    if not os.path.exists(SUBSCRIPTIONS_FILE):
        return {}
    with open(SUBSCRIPTIONS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_subscriptions(data):
    with open(SUBSCRIPTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def is_paid_user(user_id):
    subs = load_subscriptions()
    return subs.get(str(user_id), False)

def check_limits(user_id, action):
    if is_paid_user(user_id):
        return True
    return True  # لتبسيط المثال، السماح للجميع

def register_user(user_id):
    if not os.path.exists(USERS_FILE):
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            f.write("")
    with open(USERS_FILE, "r+", encoding="utf-8") as f:
        users = f.read().splitlines()
        if str(user_id) not in users:
            f.write(f"{user_id}\n")

def download_video(url, output_file):
    ydl_opts = {
        'format': 'bestvideo[height<=720]+bestaudio/best[height<=720]/best',
        'outtmpl': output_file,
        'quiet': False,
        'no_warnings': False,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    print(f"Video downloaded: {output_file}")

def download_audio(url, output_file):
    # احذف الامتداد لو كان mp3 لتوافق yt-dlp مع التحويل
    base, ext = os.path.splitext(output_file)
    if ext.lower() == '.mp3':
        output_file = base

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_file,
        'quiet': False,
        'no_warnings': False,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    print(f"Audio downloaded (converted): {output_file}.mp3")

async def ask_openai(prompt):
    response = await openai.ChatCompletion.acreate(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=500,
    )
    return response.choices[0].message.content.strip()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    register_user(user_id)
    await update.message.reply_text(
        "أهلاً! أرسل رابط فيديو لتحميله، أو اسألني أي سؤال وسيتم الرد عليك."
    )

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if not check_limits(user_id, "video"):
        await update.message.reply_text("🚫 انتهى الحد المجاني من تنزيل الفيديو.")
        return

    if text.startswith("http://") or text.startswith("https://"):
        msg_id = str(update.message.message_id)
        url_store[msg_id] = text

        keyboard = [
            [InlineKeyboardButton("▶️ تحميل فيديو", callback_data=f"download_video|{msg_id}")],
            [InlineKeyboardButton("🎵 تحميل صوت MP3", callback_data=f"download_audio|{msg_id}")],
            [InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel|{msg_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text("اختر نوع التحميل:", reply_markup=reply_markup)

    else:
        await update.message.chat.send_action(ChatAction.TYPING)
        try:
            answer = await ask_openai(text)
            await update.message.reply_text(answer)
        except Exception as e:
            await update.message.reply_text(f"❌ خطأ في الرد: {e}")

async def download_background(url, output_file, is_audio, context, user_id, msg):
    try:
        await msg.edit_text("⏳ جاري التحميل، انتظر قليلاً...")

        loop = asyncio.get_running_loop()
        func = functools.partial(download_audio if is_audio else download_video, url, output_file)
        await loop.run_in_executor(None, func)

        # هنا نحدد المسار الصحيح للملف للإرسال
        if is_audio:
            file_path = output_file + ".mp3"  # اضف .mp3 عند الفتح والإرسال
        else:
            file_path = output_file

        print(f"فتح الملف للإرسال: {file_path}")

        with open(file_path, "rb") as file:
            if is_audio:
                await context.bot.send_audio(chat_id=user_id, audio=file, caption="🎵 الصوت فقط")
            else:
                await context.bot.send_video(chat_id=user_id, video=file)

        await msg.edit_text("✅ تم التحميل والإرسال بنجاح.")
    except Exception as e:
        print(f"خطأ أثناء إرسال الملف: {e}")
        await context.bot.send_message(chat_id=user_id, text=f"❌ حدث خطأ أثناء التحميل أو الإرسال: {e}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"تم حذف الملف المؤقت: {file_path}")
        url_store.pop(msg.message_id if hasattr(msg, 'message_id') else msg, None)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    action, msg_id = data.split("|", 1)
    url = url_store.get(msg_id)

    if action == "cancel":
        await query.message.delete()
        url_store.pop(msg_id, None)
        return

    if url is None:
        await query.answer("⚠️ انتهت صلاحية الرابط.")
        return

    output_dir = "downloads"
    os.makedirs(output_dir, exist_ok=True)

    is_audio = action == "download_audio"
    output_file = os.path.join(output_dir, f"{msg_id}.mp3" if is_audio else f"{msg_id}.mp4")

    await download_background(url, output_file, is_audio, context, query.from_user.id, query.message)

application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
application.add_handler(CallbackQueryHandler(button_handler))

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
