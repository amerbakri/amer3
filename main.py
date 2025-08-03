import os
import json
import random
import yt_dlp
import asyncio
import functools
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
ADMIN_ID = 337597459  # عدل آيديك هنا
SUBS_FILE = "subscriptions.json"
url_store = {}
DOWNLOADS_DIR = "downloads"
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

application = Application.builder().token(BOT_TOKEN).build()

# ========== الاشتراك ==========
def load_subs():
    if not os.path.exists(SUBS_FILE): return {}
    with open(SUBS_FILE, encoding="utf-8") as f: return json.load(f)
def save_subs(d):
    with open(SUBS_FILE, "w", encoding="utf-8") as f: json.dump(d, f, ensure_ascii=False, indent=2)
def is_paid(uid): return str(uid) in load_subs()
def deactivate_subscription(uid):
    subs = load_subs()
    if str(uid) in subs:
        subs.pop(str(uid))
        save_subs(subs)

def check_limits(user_id, action):
    if user_id == ADMIN_ID: return True
    if is_paid(user_id): return True
    # ضع هنا منطق الحد اليومي إذا أردت
    return True  # بدون حدود حاليًا

def register_user(user_id):
    # تسجيل المستخدمين الجدد (اختياري حسب رغبتك)
    pass

# ========== تحميل الفيديو والصوت ==========
def download_video(url, output_file):
    ydl_opts = {
        'format': 'bestvideo[height<=720]+bestaudio/best[height<=720]/best',
        'outtmpl': output_file,
        'quiet': True,
        'no_warnings': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl: ydl.download([url])

def download_audio(url, output_file):
    base, ext = os.path.splitext(output_file)
    if ext.lower() == ".mp3":
        output_file = base
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
    with yt_dlp.YoutubeDL(ydl_opts) as ydl: ydl.download([url])

# ========== ردود عشوائية ==========
FUN_MESSAGES = [
    "استمتع بالمشاهدة! 😉",
    "ها هو الملف، جيبلي فشار! 🍿",
    "تم التنزيل يا معلم 🚀",
    "جاهز للتحميل… شغّل السماعات! 🎧",
    "إن شاء الله يعجبك 😎"
]

def random_fun():
    return random.choice(FUN_MESSAGES)

# ========== الأوامر ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 مرحباً بك!\n\n"
        "أرسل رابط فيديو وسيظهر لك خيارات التحميل.\n"
        "لوحة إدارة الاشتراكات: /subscribers"
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
        await update.message.reply_text("🔽 اختر نوع التحميل:", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.chat.send_action(ChatAction.TYPING)
        # هنا يمكنك ربط أي ذكاء اصطناعي أو دردشة
        await update.message.reply_text("اكتب لي رابط فيديو أو ملف!")

async def download_background(url, output_file, is_audio, context, user_id, loading_msg, reply_msg_id):
    try:
        loop = asyncio.get_running_loop()
        func = functools.partial(download_audio if is_audio else download_video, url, output_file)
        await loop.run_in_executor(None, func)

        # تحديد اسم الملف النهائي
        file_path = output_file + ".mp3" if is_audio else output_file
        with open(file_path, "rb") as file:
            if is_audio:
                await context.bot.send_audio(chat_id=user_id, audio=file, caption=random_fun(), reply_to_message_id=reply_msg_id)
            else:
                await context.bot.send_video(chat_id=user_id, video=file, caption=random_fun(), reply_to_message_id=reply_msg_id)
        # حذف رسالة جاري التحميل
        await loading_msg.delete()
    except Exception as e:
        await loading_msg.edit_text(f"❌ حدث خطأ أثناء التحميل أو الإرسال: {e}")
    finally:
        # حذف الملف المؤقت
        if os.path.exists(file_path):
            os.remove(file_path)
        # حذف الرابط من التخزين المؤقت
        url_store.pop(str(reply_msg_id), None)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # زر إلغاء اشتراك مدفوع
    if data.startswith("cancel_paid|"):
        if query.from_user.id != ADMIN_ID:
            await query.edit_message_text("❌ فقط الأدمن يمكنه إلغاء الاشتراك.")
            return
        _, uid = data.split("|", 1)
        deactivate_subscription(uid)
        await query.edit_message_text(f"✅ تم إلغاء اشتراك {uid}.")
        return

    if "|" in data:
        action, msg_id = data.split("|", 1)
        url = url_store.get(msg_id)
        if action == "cancel":
            await query.message.delete()
            url_store.pop(msg_id, None)
            return
        if url is None:
            await query.answer("⚠️ انتهت صلاحية الرابط.")
            return

        output_file = os.path.join(DOWNLOADS_DIR, f"{msg_id}.mp4")
        is_audio = action == "download_audio"
        loading_msg = await query.message.edit_text("⏳ جاري التحميل...")
        # حذف رسالة الرابط الأصلي (لو موجودة)
        try:
            await context.bot.delete_message(chat_id=query.message.chat_id, message_id=int(msg_id))
        except Exception:
            pass
        await download_background(url, output_file, is_audio, context, query.from_user.id, loading_msg, int(msg_id))

# لوحة الأدمن لعرض المشتركين المدفوعين مع زر إلغاء بجانب كل واحد
async def list_paid_subscribers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ الأمر للمشرف فقط.")
        return

    subs = load_subs()
    keyboard = []
    for uid in subs.keys():
        keyboard.append([InlineKeyboardButton(f"❌ إلغاء {uid}", callback_data=f"cancel_paid|{uid}")])
    if not keyboard:
        keyboard = [[InlineKeyboardButton("لا يوجد مشتركين", callback_data="none")]]
    await update.message.reply_text(
        "المشتركين المدفوعين:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("subscribers", list_paid_subscribers))
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
