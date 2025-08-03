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
    ChatAction,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ========== الإعدادات ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not BOT_TOKEN or not OPENAI_API_KEY:
    raise RuntimeError("BOT_TOKEN و OPENAI_API_KEY لازم يكونوا مضبوطين في المتغيرات البيئية.")

openai.api_key = OPENAI_API_KEY

SUBSCRIPTIONS_FILE = "subscriptions.json"
USERS_FILE = "users.txt"
url_store = {}
support_chats = {}  # {user_id: admin_id} للعناية بالدعم الفني

quality_map = {
    "720": "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
    "best": "best",
}

application = Application.builder().token(BOT_TOKEN).build()

# ========== وظائف الاشتراك والصلاحيات ==========
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
        return True  # لا حد للمشتركين المدفوعين
    # مثال: حد 3 فيديوهات يومياً للمجانيين (يمكن تطويرها مع حفظ الاستخدامات)
    return True  # مؤقتاً السماح للجميع

def register_user(user_id):
    if not os.path.exists(USERS_FILE):
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            f.write("")
    with open(USERS_FILE, "r+", encoding="utf-8") as f:
        users = f.read().splitlines()
        if str(user_id) not in users:
            f.write(f"{user_id}\n")

# ========== تحميل الفيديو والصوت ==========
def download_video(url, output_file, quality="720"):
    ydl_opts = {
        'format': quality_map.get(quality, "best"),
        'outtmpl': output_file,
        'quiet': True,
        'no_warnings': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

def download_audio(url, output_file):
    ydl_opts = {
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
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

# ========== الذكاء الاصطناعي ==========

async def ask_openai(prompt):
    response = await openai.ChatCompletion.acreate(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=500,
    )
    return response.choices[0].message.content.strip()

# ========== أوامر البوت ==========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    register_user(user_id)
    await update.message.reply_text(
        "أهلاً! أرسل رابط فيديو لتحميله أو اكتب استفسارك وسيتم الرد عليه بالذكاء الاصطناعي."
    )

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if not check_limits(user_id, "video"):
        await update.message.reply_text("🚫 انتهى الحد المجاني من تنزيل الفيديو.")
        return

    # لو الرابط
    if text.startswith("http://") or text.startswith("https://"):
        msg_id = str(update.message.message_id)
        url_store[msg_id] = text

        keyboard = [
            [InlineKeyboardButton("🎵 صوت فقط", callback_data=f"audio|{msg_id}")],
            [InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel|{msg_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text("⏳ جاري تحميل الفيديو بجودة 720p أو أفضل، انتظر قليلاً...", reply_markup=reply_markup)

        loop = asyncio.get_running_loop()
        loop.create_task(download_video_background(url=text, msg_id=msg_id, context=context, user_id=user_id))

    else:
        # الرد على نصوص الذكاء الاصطناعي
        await update.message.chat.send_action(ChatAction.TYPING)
        try:
            answer = await ask_openai(text)
            await update.message.reply_text(answer)
        except Exception as e:
            await update.message.reply_text(f"❌ خطأ في الرد: {e}")

async def download_video_background(url, msg_id, context, user_id):
    output_dir = "downloads"
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, f"{msg_id}.mp4")

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, functools.partial(download_video, url, output_file, "720"))

        with open(output_file, "rb") as video_file:
            await context.bot.send_video(chat_id=user_id, video=video_file)

    except Exception as e:
        await context.bot.send_message(chat_id=user_id, text=f"❌ حدث خطأ أثناء التحميل: {e}")

    finally:
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

        loop = asyncio.get_running_loop()

        def run_audio_download():
            download_audio(url, output_file)

        try:
            await loop.run_in_executor(None, run_audio_download)
            with open(output_file, "rb") as audio_file:
                await context.bot.send_audio(chat_id=query.from_user.id, audio=audio_file, caption="🎵 الصوت فقط")
            await query.edit_message_text("✅ تم تحميل الصوت وإرساله.")
        except Exception as e:
            await context.bot.send_message(chat_id=query.from_user.id, text=f"❌ حدث خطأ أثناء تحميل الصوت: {e}")
        finally:
            if os.path.exists(output_file):
                os.remove(output_file)
            url_store.pop(msg_id, None)

# ========== دعم فني ==========

async def support_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # إذا المستخدم مش مضاف، ضيفه مع حالة مفتوحة
    support_chats[user_id] = None  # مفتوح للدعم
    await update.message.reply_text("✅ تم فتح غرفة الدعم الفني، تواصل معنا!")

async def support_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in support_chats:
        # أرسل رسالة الأدمن إلى المستخدم أو العكس (حسب من يرسل)
        # هنا لازم تضيف منطق تحويل الرسائل بين الطرفين
        await update.message.reply_text("تم استلام رسالتك في الدعم الفني.")
    else:
        await update.message.reply_text("لم تقم بفتح غرفة دعم، ارسل /support للبدء.")

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # فقط الأدمن يقدر يستخدمها
    admin_id = 337597459  # عدل حسب الايدي الحقيقي
    if update.effective_user.id != admin_id:
        await update.message.reply_text("🚫 هذا الأمر خاص بالأدمن فقط.")
        return
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text("اكتب نص الإعلان بعد الأمر.")
        return

    with open(USERS_FILE, "r", encoding="utf-8") as f:
        users = f.read().splitlines()

    sent = 0
    for uid in users:
        try:
            await context.bot.send_message(chat_id=int(uid), text=text)
            sent += 1
        except Exception:
            pass
    await update.message.reply_text(f"تم إرسال الإعلان إلى {sent} مستخدم.")

# ========== تسجيل المعالجات ==========

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("support", support_start))
application.add_handler(CommandHandler("broadcast", admin_broadcast))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
application.add_handler(CallbackQueryHandler(button_handler))

# ========== Webhook ==========

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
