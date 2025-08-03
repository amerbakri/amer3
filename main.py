import os
import json
import subprocess
import logging
import functools
import asyncio
import re
from datetime import datetime, timezone, timedelta
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
)
import openai

# --- إعدادات ---
ADMIN_ID = 337597459
ORANGE_NUMBER = "0781200500"
BOT_TOKEN = os.getenv("BOT_TOKEN", "ضع_توكن_البوت_هنا")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "ضع_مفتاح_OPENAI_هنا")
COOKIES_FILE = "cookies.txt"
USERS_FILE = "users.txt"
SUBSCRIPTIONS_FILE = "subscriptions.json"
LIMITS_FILE = "limits.json"
DAILY_VIDEO_LIMIT = 3
DAILY_AI_LIMIT = 5
SUB_DURATION_DAYS = 30

openai.api_key = OPENAI_API_KEY

url_store = {}
pending_subs = set()
broadcast_mode = {}
support_chats = {}
admin_reply_to = {}
quality_map = {
    "720": "bestvideo[height<=720]+bestaudio/best",
    "480": "bestvideo[height<=480]+bestaudio/best",
    "360": "bestvideo[height<=360]+bestaudio/best",
}

# --- وظائف مساعدة ---
def load_json(path, default=None):
    if not os.path.exists(path):
        return default or {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
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

def fullname(user):
    return f"{user.first_name or ''} {user.last_name or ''}".strip()

def is_valid_url(text):
    return re.match(
        r"^(https?://)?(www\.)?"
        r"(youtube\.com|youtu\.be|tiktok\.com|instagram\.com|facebook\.com|fb\.watch)/.+",
        text
    ) is not None

async def safe_edit(query, text, kb=None):
    try:
        await query.edit_message_text(text, reply_markup=kb)
    except:
        pass

# --- لوحة تحكم الأدمن/بث ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("👥 عدد المستخدمين", callback_data="admin_users")],
        [InlineKeyboardButton("🟢 المدفوعين", callback_data="admin_paidlist")],
        [InlineKeyboardButton("📢 إعلان", callback_data="admin_broadcast")],
        [InlineKeyboardButton("📊 إحصائيات", callback_data="admin_stats")],
        [InlineKeyboardButton("💬 دعم فني", callback_data="admin_supports")],
    ]
    await update.message.reply_text("🛠️ لوحة تحكم الأدمن:", reply_markup=InlineKeyboardMarkup(kb))

async def admin_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.from_user.id != ADMIN_ID: return
    data = q.data
    if data == "admin_users":
        cnt = len(open(USERS_FILE, "r", encoding="utf-8").readlines())
        await safe_edit(q, f"👥 عدد المستخدمين: {cnt}")
    elif data == "admin_paidlist":
        subs = load_subs()
        kb = []
        for uid in subs:
            kb.append([
                InlineKeyboardButton(f"{uid}", callback_data=f"admin_userinfo|{uid}"),
                InlineKeyboardButton("❌ إلغاء", callback_data=f"admin_cancel_sub|{uid}")
            ])
        if not kb: kb = [[InlineKeyboardButton("لا أحد", callback_data="ignore")]]
        await safe_edit(q, "🟢 المدفوعين:", InlineKeyboardMarkup(kb))
    elif data.startswith("admin_cancel_sub|"):
        _, uid = data.split("|", 1)
        deactivate_subscription(uid)
        await q.answer("تم إلغاء الاشتراك.")
        await safe_edit(q, f"❌ تم إلغاء اشتراك {uid}")
    elif data == "admin_broadcast":
        broadcast_mode[ADMIN_ID] = True
        await q.message.reply_text("✉️ أرسل الرسالة أو الوسائط الآن ليتم بثها لجميع المستخدمين.")
    elif data == "admin_stats":
        subs = load_subs()
        total = len(open(USERS_FILE, "r", encoding="utf-8").readlines())
        paid = len(subs)
        await safe_edit(q, f"📊 الإحصائيات:\nعدد الكلي: {total}\nالمدفوعين: {paid}")
    elif data == "admin_supports":
        chats = [str(k) for k in support_chats.keys()]
        await safe_edit(q, f"دردشات الدعم: {', '.join(chats) if chats else 'لا يوجد'}")
    else:
        await safe_edit(q, "رجوع ...")

# --- بث/إعلان ---
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.from_user.id == ADMIN_ID and broadcast_mode.get(ADMIN_ID):
        broadcast_mode[ADMIN_ID] = False
        sent = 0
        users = open(USERS_FILE, "r", encoding="utf-8").readlines()
        for line in users:
            uid = int(line.split("|",1)[0])
            try:
                if update.message.text:
                    await context.bot.send_message(uid, f"📢 إعلان جديد:\n{update.message.text}")
                elif update.message.photo:
                    await context.bot.send_photo(uid, update.message.photo[-1].file_id, caption="📢 إعلان بالصور")
                elif update.message.video:
                    await context.bot.send_video(uid, update.message.video.file_id, caption="📢 إعلان فيديو")
                elif update.message.audio:
                    await context.bot.send_audio(uid, update.message.audio.file_id, caption="📢 إعلان صوتي")
                sent += 1
            except:
                continue
        await update.message.reply_text(f"تم إرسال الإعلان إلى {sent} مستخدم.")

# --- دعم فني مباشر ---
async def support_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    support_chats[update.effective_user.id] = True
    await update.message.reply_text("✉️ أرسل رسالتك الآن وسيتم تحويلها للأدمن.")

async def support_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in support_chats:
        await context.bot.send_message(
            ADMIN_ID,
            f"💬 رسالة دعم من {fullname(update.effective_user)}\n\n{update.message.text}"
        )
        await update.message.reply_text("✅ تم إرسال رسالتك للدعم الفني، سيقوم الأدمن بالرد قريباً.")
        support_chats.pop(uid)
    elif update.message.reply_to_message and update.message.reply_to_message.from_user.id == ADMIN_ID:
        ref_uid = int(update.message.reply_to_message.text.split("من ")[-1].split("\n")[0])
        await context.bot.send_message(ref_uid, f"🟢 رد الأدمن:\n{update.message.text}")

# --- اشتراك وتفعيل ---
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
    q = update.callback_query; await q.answer()
    _, uid = q.data.split("|", 1)
    activate_subscription(int(uid))
    pending_subs.discard(int(uid))
    await context.bot.send_message(int(uid), "✅ *تم تفعيل اشتراكك بنجاح!* الآن جميع الميزات متاحة بدون حدود يومية.", parse_mode="Markdown")
    await q.edit_message_text("✅ تم تفعيل الاشتراك.")

async def reject_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    _, uid = q.data.split("|", 1)
    pending_subs.discard(int(uid))
    await context.bot.send_message(int(uid), "❌ *تم رفض طلب اشتراكك.*\nللمساعدة استخدم زر الدعم.", parse_mode="Markdown")
    await q.edit_message_text("🚫 تم رفض الاشتراك.")

# --- ذكاء صناعي ---
async def ask_openai(text):
    res = await asyncio.get_event_loop().run_in_executor(
        None, lambda: openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": text}],
            max_tokens=256,
        )
    )
    return res["choices"][0]["message"]["content"].strip()

# --- رسائل المستخدمين ---
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    store_user(user)
    text = update.message.text.strip()
    uid = user.id

    # دعم فني
    if text.startswith("/support"):
        await support_start(update, context)
        return

    # بث للأدمن
    if uid == ADMIN_ID and broadcast_mode.get(ADMIN_ID):
        await broadcast(update, context)
        return

    # دعم فني مباشر
    if uid in support_chats:
        await support_msg(update, context)
        return

    # روابط فيديوهات
    if is_valid_url(text):
        if not check_limits(uid, "video"):
            await update.message.reply_text("🚫 انتهى الحد المجاني من تنزيل الفيديو.")
            return

        msg_id = str(update.message.message_id)
        url_store[msg_id] = text
        keyboard = [
            [InlineKeyboardButton("▶️ تحميل فيديو 720p", callback_data=f"video|720|{msg_id}")],
            [InlineKeyboardButton("▶️ تحميل فيديو 480p", callback_data=f"video|480|{msg_id}")],
            [InlineKeyboardButton("▶️ تحميل فيديو 360p", callback_data=f"video|360|{msg_id}")],
            [InlineKeyboardButton("🎵 تحميل صوت MP3", callback_data=f"audio|360|{msg_id}")],
            [InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel|{msg_id}")]
        ]
        await update.message.reply_text("اختر نوع التحميل المطلوب:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # ذكاء صناعي لأي شيء ثاني
    await update.message.reply_text("🤖 جارٍ التفكير ...")
    try:
        answer = await ask_openai(text)
        await update.message.reply_text(answer)
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ في الرد: {e}")

# --- تحميل الفيديو/الصوت ---
async def button_handler(update, context):
    import glob
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
    await q.edit_message_text("⏳ جاري التحميل ... شايك على الإبداع!")

    # yt-dlp command
    if action == "audio":
        cmd = [
            "yt-dlp", "--cookies", COOKIES_FILE,
            "-f", "bestaudio[ext=m4a]/bestaudio/best",
            "--extract-audio", "--audio-format", "mp3",
            "-o", outfile, url
        ]
        caption = "🎵 استمتع بالصوت فقط!"
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

# =========== ويب هوك وتسجيل المعالجات ===========

application = Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("start", admin_panel))
application.add_handler(CallbackQueryHandler(subscribe_request,    pattern=r"^subscribe_request$"))
application.add_handler(CallbackQueryHandler(confirm_sub,          pattern=r"^confirm_sub\|"))
application.add_handler(CallbackQueryHandler(reject_sub,           pattern=r"^reject_sub\|"))
application.add_handler(CallbackQueryHandler(button_handler,       pattern=r"^(video|audio|cancel)\|"))
application.add_handler(CallbackQueryHandler(admin_panel_callback, pattern=r"^admin_"))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
application.add_handler(MessageHandler(filters.ALL, broadcast))

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
