import os
import json
import subprocess
import re
import logging
import asyncio
import functools

from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
import openai

# --- Logging ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Config ---
ADMIN_ID = 337597459
BOT_TOKEN = os.getenv("BOT_TOKEN", "ضع_توكن_البوت_هنا")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "ضع_مفتاح_OPENAI_هنا")
COOKIES_FILE = "cookies.txt"
USERS_FILE = "users.txt"
SUBSCRIPTIONS_FILE = "subscriptions.json"
LIMITS_FILE = "limits.json"
ORANGE_NUMBER = "0781200500"
DAILY_VIDEO_LIMIT = 3
DAILY_AI_LIMIT = 5
SUB_DURATION_DAYS = 30

openai.api_key = OPENAI_API_KEY

url_store = {}
pending_subs = set()
open_chats = set()
admin_reply_to = {}
admin_broadcast_mode = False

quality_map = {
    "720": "bestvideo[height<=720]+bestaudio/best",
    "480": "bestvideo[height<=480]+bestaudio/best",
    "360": "bestvideo[height<=360]+bestaudio/best",
}

def load_json(path, default=None):
    if not os.path.exists(path):
        return default or {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default or {}

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def store_user(user):
    if not os.path.exists(USERS_FILE):
        open(USERS_FILE, "w", encoding="utf-8").close()
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    existing_ids = {line.split("|",1)[0] for line in lines}
    if str(user.id) not in existing_ids:
        entry = f"{user.id}|{user.username or 'NO'}|{user.first_name or ''} {user.last_name or ''}".strip()
        with open(USERS_FILE, "a", encoding="utf-8") as f:
            f.write(entry + "\n")

def load_subs():
    return load_json(SUBSCRIPTIONS_FILE, {})

def is_subscribed(uid):
    subs = load_subs()
    return subs.get(str(uid), {}).get("active", False)

def activate_subscription(uid):
    subs = load_subs()
    subs[str(uid)] = {"active": True, "date": datetime.now(timezone.utc).isoformat()}
    save_json(SUBSCRIPTIONS_FILE, subs)

def deactivate_subscription(uid):
    subs = load_subs()
    subs.pop(str(uid), None)
    save_json(SUBSCRIPTIONS_FILE, subs)

def check_limits(uid, action):
    # الأدمن دائماً لا حدود عليه
    if is_subscribed(uid) or uid == ADMIN_ID:
        return True
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    limits = load_json(LIMITS_FILE, {})
    u = limits.get(str(uid), {})
    if u.get("date") != today:
        u = {"date": today, "video": 0, "ai": 0}
    if action == "video" and u["video"] >= DAILY_VIDEO_LIMIT:
        return False
    if action == "ai" and u["ai"] >= DAILY_AI_LIMIT:
        return False
    u[action] += 1
    limits[str(uid)] = u
    save_json(LIMITS_FILE, limits)
    return True

def is_valid_url(text):
    return re.match(
        r"^(https?://)?(www\.)?"
        r"(youtube\.com|youtu\.be|tiktok\.com|instagram\.com|facebook\.com|fb\.watch)/.+",
        text
    ) is not None

def fullname(user):
    return f"{user.first_name or ''} {user.last_name or ''}".strip()

async def safe_edit(query, text, kb=None):
    try:
        await query.edit_message_text(text, reply_markup=kb)
    except:
        pass

# ============= Handlers ==============

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    store_user(user)
    if user.id == ADMIN_ID:
        keyboard = [
            [InlineKeyboardButton("👥 عدد المستخدمين", callback_data="admin_users")],
            [InlineKeyboardButton("📢 إعلان",         callback_data="admin_broadcast")],
            [InlineKeyboardButton("💬 محادثات الدعم", callback_data="admin_supports")],
            [InlineKeyboardButton("🟢 مدفوعين",       callback_data="admin_paidlist")],
            [InlineKeyboardButton("📊 إحصائيات متقدمة", callback_data="admin_stats")],
            [InlineKeyboardButton("❌ إغلاق",         callback_data="admin_panel_close")],
        ]
        kb = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "🛠️ *لوحة تحكم الأدمن*\nاختر أحد الخيارات:",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        return

    if is_subscribed(user.id):
        subs = load_subs()
        date_iso = subs[str(user.id)]["date"]
        activated = datetime.fromisoformat(date_iso)
        expiry = activated + timedelta(days=SUB_DURATION_DAYS)
        days_left = (expiry - datetime.now(timezone.utc)).days
        if days_left > 0:
            text = (
                f"✅ اشتراكك ساري لمدّة **{days_left}** يوم إضافي.\n"
                "استمتع بكل ميزات البوت دون حدود يومية 🎉\n"
                "💬 لأي استفسار اضغط زر الدعم أدناه."
            )
        else:
            text = (
                "⚠️ انتهت مدّة اشتراكك.\n"
                f"🔓 لإعادة الاشتراك، أرسل *2 د.أ* عبر أورنج ماني إلى:\n➡️ `{ORANGE_NUMBER}`\n\n"
                "ثم اضغط `اشترك` لإرسال طلبك للأدمن."
            )
        keyboard = [[InlineKeyboardButton("💬 دعم", callback_data="support_start")]]
    else:
        text = (
            "👋 *مرحباً في بوت التحميل والـ AI!*\n\n"
            f"🔓 للاشتراك بدون حدود يومية، أرسل *2 د.أ* عبر أورنج ماني إلى:\n➡️ `{ORANGE_NUMBER}`\n\n"
            "ثم اضغط `اشترك` لإرسال طلبك للأدمن."
        )
        keyboard = [
            [InlineKeyboardButton("🔓 اشترك", callback_data="subscribe_request")],
            [InlineKeyboardButton("💬 دعم",     callback_data="support_start")],
        ]
    kb = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")

# --- زر طلب الاشتراك ---
async def subscribe_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    u = q.from_user
    if u.id in pending_subs:
        await q.answer("❗️ طلبك قيد المراجعة.")
        return
    pending_subs.add(u.id)
    info = (
        f"📥 *طلب اشتراك جديد*\n"
        f"👤 {fullname(u)} | @{u.username or 'NO'}\n"
        f"🆔 {u.id}"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ تفعيل", callback_data=f"confirm_sub|{u.id}"),
        InlineKeyboardButton("❌ رفض",  callback_data=f"reject_sub|{u.id}")
    ]])
    await context.bot.send_message(ADMIN_ID, info, reply_markup=kb, parse_mode="Markdown")
    await q.edit_message_text("✅ تم إرسال طلب الاشتراك للأدمن.")

async def confirm_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, uid = q.data.split("|", 1)
    activate_subscription(int(uid))
    pending_subs.discard(int(uid))
    await context.bot.send_message(
        int(uid),
        "✅ *تم تفعيل اشتراكك بنجاح!* الآن جميع الميزات متاحة بدون حدود يومية.",
        parse_mode="Markdown"
    )
    await q.edit_message_text("✅ تم تفعيل الاشتراك.")

async def reject_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, uid = q.data.split("|", 1)
    pending_subs.discard(int(uid))
    await context.bot.send_message(
        int(uid),
        "❌ *تم رفض طلب اشتراكك.*\nللمساعدة استخدم زر الدعم.",
        parse_mode="Markdown"
    )
    await q.edit_message_text("🚫 تم رفض الاشتراك.")

# --- دعم فني وأزرار أدمن وتبليغ ---
# (نفس الكود الذي أرسلته لك، لم يتغير)

# --- زر المدفوعين مع زر إلغاء الاشتراك ---
async def admin_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.from_user.id != ADMIN_ID: return
    data = q.data
    back = [[InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]]
    if data == "admin_paidlist":
        subs = load_subs()
        buttons = []
        if subs:
            for uid in subs:
                btns = [
                    InlineKeyboardButton(
                        f"{uid}",
                        callback_data=f"admin_paid_user|{uid}"
                    ),
                    InlineKeyboardButton(
                        "❌ إلغاء", callback_data=f"admin_cancel_sub|{uid}"
                    )
                ]
                buttons.append(btns)
        else:
            buttons.append([InlineKeyboardButton("لا أحد", callback_data="ignore")])
        await safe_edit(q, "💰 مشتركون مدفوعون:", InlineKeyboardMarkup(buttons + back))
    elif data.startswith("admin_cancel_sub|"):
        _, uid = data.split("|", 1)
        deactivate_subscription(uid)
        await q.answer("تم إلغاء الاشتراك.")
        await safe_edit(q, f"❌ تم إلغاء اشتراك {uid}")
    else:
        # (أكمل باقي الشيفرات الإدارية العادية هنا حسب كودك)
        pass

# ============= زر التحميل (الفيديو/الصوت) وإرسال الملف =============
import glob

async def button_handler(update, context):
    q = update.callback_query
    uid = q.from_user.id
    await q.answer()
    parts = q.data.split("|")
    if len(parts) == 2 and parts[0] == "cancel":
        await q.message.delete()
        url_store.pop(parts[1], None)
        return
    elif len(parts) == 3:
        action, quality, msg_id = parts
    else:
        await q.answer("⚠️ أمر غير معروف")
        return

    url = url_store.get(msg_id)
    if not url:
        await q.answer("⚠️ انتهت صلاحية الرابط.")
        return

    outfile = f"{msg_id}.{'mp3' if action == 'audio' else 'mp4'}"
    await q.edit_message_text("⏳ جاري التحميل في الخلفية... استعد لمتعة المشاهدة أو الاستماع!")

    # yt-dlp command
    if action == "audio":
        cmd = [
            "yt-dlp", "--cookies", COOKIES_FILE,
            "-f", "bestaudio[ext=m4a]/bestaudio/best",
            "--extract-audio", "--audio-format", "mp3",
            "-o", outfile, url
        ]
        caption = "🎵 تم تحويل الفيديو إلى صوت!"
    else:
        fmt = quality_map.get(quality, "best")
        cmd = ["yt-dlp", "--cookies", COOKIES_FILE, "-f", fmt, "-o", outfile, url]
        caption = f"🎬 تم تحميل الفيديو بجودة {quality}p!"

    runner = functools.partial(subprocess.run, cmd, check=True)
    try:
        await asyncio.get_running_loop().run_in_executor(None, runner)
    except subprocess.CalledProcessError as e:
        await context.bot.send_message(uid, f"❌ فشل التحميل: {e}")
        url_store.pop(msg_id, None)
        return

    downloaded_files = glob.glob(f"{msg_id}.*")
    if not downloaded_files:
        await context.bot.send_message(uid, "❌ لم أستطع العثور على الملف النهائي!")
        url_store.pop(msg_id, None)
        return

    outfile = downloaded_files[0]
    with open(outfile, "rb") as f:
        if action == "audio":
            await context.bot.send_audio(uid, f, caption=caption)
        else:
            await context.bot.send_video(uid, f, caption=caption)
    # حذف الملفات المؤقتة والرسائل
    for file in downloaded_files:
        try: os.remove(file)
        except Exception: pass
    url_store.pop(msg_id, None)
    try: await q.message.delete()
    except: pass
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
            [InlineKeyboardButton("▶️ تحميل فيديو", callback_data=f"video|720|{msg_id}")],
            [InlineKeyboardButton("🎵 تحميل صوت MP3", callback_data=f"audio|360|{msg_id}")],
            [InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel|{msg_id}")]
        ]
        await update.message.reply_text("🔽 اختر نوع التحميل:", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text("اكتب لي رابط فيديو أو ملف!")



# ============= بوت ويب هوك =============
app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(subscribe_request,    pattern=r"^subscribe_request$"))
app.add_handler(CallbackQueryHandler(confirm_sub,          pattern=r"^confirm_sub\|"))
app.add_handler(CallbackQueryHandler(reject_sub,           pattern=r"^reject_sub\|"))
app.add_handler(CallbackQueryHandler(button_handler,       pattern=r"^(video|audio|cancel)\|"))
app.add_handler(CallbackQueryHandler(admin_panel_callback, pattern=r"^admin_"))
# (أضف باقي الكول باك هاندلرز الإدارية والدعم الفني هنا)
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
# ... وباقي الهاندلرز مثل دعم الوسائط، OCR... (كما هو في كودك الأخير)
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8443))
    host = os.getenv("RENDER_EXTERNAL_HOSTNAME", "localhost")
    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=BOT_TOKEN,
        webhook_url=f"https://{host}/{BOT_TOKEN}"
    )
