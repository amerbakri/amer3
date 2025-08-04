import os
import json
import subprocess
import logging
import functools
import asyncio
import re
from datetime import datetime, timezone
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
import openai

# ——— Logging configuration ——————————————————
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# ——— Configuration ——————————————————————
ADMIN_ID = 337597459              # غيّر لآيديك!
ORANGE_NUMBER = "0781200500"
BOT_TOKEN = os.getenv("BOT_TOKEN", "ضع_توكن_البوت")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "ضع_OPENAI")
COOKIES_FILE = "cookies.txt"
USERS_FILE = "users.txt"
SUBSCRIPTIONS_FILE = "subscriptions.json"
LIMITS_FILE = "limits.json"
DAILY_VIDEO_LIMIT = 3
DAILY_AI_LIMIT = 5

openai.api_key = OPENAI_API_KEY

quality_map = {
    "720": "bestvideo[height<=720]+bestaudio/best",
    "480": "bestvideo[height<=480]+bestaudio/best",
    "360": "bestvideo[height<=360]+bestaudio/best",
}

# ——— In-memory stores ————————————————————
url_store: dict = {}
pending_subs: set = set()
broadcast_mode: dict = {}
active_support_chats: dict = {}

# ——— Helpers —————————————————————————
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
    existing = {line.split("|",1)[0] for line in lines}
    if str(user.id) not in existing:
        entry = f"{user.id}|{user.username or 'NO'}|{user.first_name or ''} {user.last_name or ''}".strip()
        with open(USERS_FILE, "a", encoding="utf-8") as f:
            f.write(entry + "\n")

def load_subs():
    return load_json(SUBSCRIPTIONS_FILE, {})

def is_subscribed(uid: int) -> bool:
    subs = load_subs()
    return subs.get(str(uid), {}).get("active", False)

def activate_subscription(uid: int):
    subs = load_subs()
    subs[str(uid)] = {"active": True, "date": datetime.now(timezone.utc).isoformat()}
    save_json(SUBSCRIPTIONS_FILE, subs)

def deactivate_subscription(uid: int):
    subs = load_subs()
    subs.pop(str(uid), None)
    save_json(SUBSCRIPTIONS_FILE, subs)

def check_limits(uid: int, action: str) -> bool:
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

def is_valid_url(text: str) -> bool:
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

# ============ /start & Admin Panel ================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id == ADMIN_ID:
        kb = [
            [InlineKeyboardButton("👥 المستخدمون", callback_data="admin_users")],
            [InlineKeyboardButton("🟢 المدفوعين", callback_data="admin_paidlist")],
            [InlineKeyboardButton("📢 إعلان", callback_data="admin_broadcast")],
            [InlineKeyboardButton("📊 إحصائيات", callback_data="admin_stats")],
            [InlineKeyboardButton("🆘 دردشات الدعم", callback_data="admin_supports")],
        ]
        await update.message.reply_text("🛠️ لوحة تحكم الأدمن:", reply_markup=InlineKeyboardMarkup(kb))
    else:
        kb = [
            [InlineKeyboardButton("💎 اشترك الآن", callback_data="subscribe_request")],
            [InlineKeyboardButton("💬 دعم فني", callback_data="support_start")],
        ]
        await update.message.reply_text(
            "👋 أهلاً بك!\n"
            "🔓 حمّل 3 فيديوهات يومياً مجاناً أو اشترك لتفعيل الميزات الكاملة.\n"
            f"للاشتراك، حول على أورنج موني {ORANGE_NUMBER} ثم اضغط 💎 اشترك الآن.",
            reply_markup=InlineKeyboardMarkup(kb)
        )

async def admin_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_ID:
        return
    data = q.data

    if data == "admin_users":
        subs = load_subs()
        users = []
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                for line in f.read().splitlines():
                    uid, uname, name = line.split("|", 2)
                    sub = subs.get(uid)
                    if sub and sub.get("active"):
                        dt = datetime.fromisoformat(sub["date"])
                        days_used = (datetime.now(timezone.utc) - dt).days
                        days_left = max(0, 30 - days_used)
                        status = f"مشترك ({days_left} يوم)"
                    else:
                        status = "غير مشترك"
                    users.append((uid, uname, status))
        kb = []
        for uid, uname, status in users:
            label = f"{uname or 'NO'} | {status}"
            kb.append([
                InlineKeyboardButton(label, callback_data="ignore"),
                InlineKeyboardButton("🆘 دعم", callback_data=f"admin_support_user|{uid}")
            ])
        if not kb:
            kb = [[InlineKeyboardButton("لا يوجد مستخدمون", callback_data="ignore")]]
        await safe_edit(q, "👥 قائمة المستخدمين:", InlineKeyboardMarkup(kb))

    elif data == "admin_paidlist":
        subs = load_subs()
        kb = []
        for uid in subs:
            kb.append([
                InlineKeyboardButton(str(uid), callback_data="ignore"),
                InlineKeyboardButton("❌ إلغاء", callback_data=f"admin_cancel_sub|{uid}")
            ])
        if not kb:
            kb = [[InlineKeyboardButton("لا أحد", callback_data="ignore")]]
        await safe_edit(q, "🟢 المدفوعين:", InlineKeyboardMarkup(kb))

    elif data.startswith("admin_cancel_sub|"):
        _, uid = data.split("|", 1)
        deactivate_subscription(int(uid))
        await q.answer("تم إلغاء الاشتراك.")
        await safe_edit(q, f"❌ تم إلغاء اشتراك {uid}")

    elif data == "admin_broadcast":
        broadcast_mode[ADMIN_ID] = True
        await q.message.reply_text("✉️ أرسل النص أو الوسائط الآن ليتم بثها لجميع المستخدمين.")

    elif data == "admin_stats":
        subs = load_subs()
        total_users = len(open(USERS_FILE, "r", encoding="utf-8").readlines()) if os.path.exists(USERS_FILE) else 0
        paid_count = len(subs)
        limits = load_json(LIMITS_FILE, {})
        total_videos = sum(item.get("video", 0) for item in limits.values())
        total_ai = sum(item.get("ai", 0) for item in limits.values())
        stats_text = (
            f"📊 الإحصائيات اليوم:\n"
            f"• عدد المستخدمين الكلي: {total_users}\n"
            f"• عدد المشتركين: {paid_count}\n"
            f"• 🚀 تحميلات الفيديو اليوم: {total_videos}\n"
            f"• 🤖 استفسارات AI اليوم: {total_ai}"
        )
        await safe_edit(q, stats_text)

    elif data == "admin_supports":
        chats = []
        for uid, info in active_support_chats.items():
            chats.append([
                InlineKeyboardButton(
                    f"{info['name']} @{info['username']} | {uid}",
                    callback_data=f"reply_support|{uid}"
                )
            ])
        if not chats:
            chats = [[InlineKeyboardButton("لا يوجد دردشات دعم", callback_data="ignore")]]
        await safe_edit(q, "🆘 دردشات الدعم النشطة:", InlineKeyboardMarkup(chats))

    else:
        await safe_edit(q, "🔙 رجوع...")

# —————————— بدء دعم مباشر من الأدمن إلى مستخدم ——————————
async def admin_support_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, uid = q.data.split("|", 1)
    context.user_data["support_contact"] = int(uid)
    await q.message.reply_text(f"📝 اكتب رسالتك وسيتم إرسالها إلى المستخدم {uid}.")

# —————————— رد الأدمن على رسالة الدعم ——————————
async def reply_support_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, uid = q.data.split("|", 1)
    context.user_data["support_reply_to"] = int(uid)
    await q.message.reply_text(f"📝 اكتب ردك هنا وسيُرسل مباشرةً إلى المستخدم {uid}.")

# ============ بث/إعلان لجميع المستخدمين =================
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    broadcast_mode[ADMIN_ID] = False
    sent = 0
    lines = open(USERS_FILE,"r",encoding="utf-8").read().splitlines() if os.path.exists(USERS_FILE) else []
    for line in lines:
        try:
            uid = int(line.split("|",1)[0])
            msg = update.message
            if msg.text:
                await context.bot.send_message(uid, f"📢 إعلان جديد:\n{msg.text}")
            elif msg.photo:
                await context.bot.send_photo(uid, msg.photo[-1].file_id, caption="📢 إعلان بالصور")
            elif msg.video:
                await context.bot.send_video(uid, msg.video.file_id, caption="📢 إعلان فيديو")
            elif msg.audio:
                await context.bot.send_audio(uid, msg.audio.file_id, caption="📢 إعلان صوتي")
            elif msg.document:
                await context.bot.send_document(uid, msg.document.file_id, caption="📢 إعلان ملف")
            sent += 1
        except:
            continue
    await update.message.reply_text(f"✅ تم إرسال الإعلان إلى {sent} مستخدم.")

# ============ دعم فني تفاعلي (مستخدم → أدمن ثم رد) ============
async def support_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # إذا هو CallbackQuery
    if update.callback_query:
        q = update.callback_query
        await q.answer()
        user = q.from_user
        target_msg = q.message
    else:
        # هو أمر /support
        user = update.effective_user
        target_msg = update.message

    # سجّل المستخدم في قائمة الدعم
    active_support_chats[user.id] = {
        "name": fullname(user),
        "username": user.username or "NO",
        "waiting": True
    }
    # إرسِل الرسالة على نفس الرسالة (زر أو أمر)
    await target_msg.reply_text("✉️ أرسل رسالتك الآن وسيتم تحويلها فوراً للأدمن.")


async def support_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = update.message

    # — مستخدم يرسل دعم أول مرة
    if uid in active_support_chats and active_support_chats[uid]["waiting"]:
        info = active_support_chats[uid]
        # دعم بأي نوع محتوى
        if msg.text:
            sent = await context.bot.send_message(
                ADMIN_ID,
                f"💬 دعم جديد:\n👤 {info['name']} | @{info['username']} | {uid}\n\n{msg.text}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("رد عليه", callback_data=f"reply_support|{uid}")]])
            )
        elif msg.photo:
            sent = await context.bot.send_photo(
                ADMIN_ID, msg.photo[-1].file_id,
                caption=f"💬 دعم جديد من {info['name']} | @{info['username']} | {uid}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("رد عليه", callback_data=f"reply_support|{uid}")]])
            )
        elif msg.video:
            sent = await context.bot.send_video(
                ADMIN_ID, msg.video.file_id,
                caption=f"💬 دعم جديد من {info['name']} | @{info['username']} | {uid}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("رد عليه", callback_data=f"reply_support|{uid}")]])
            )
        elif msg.audio:
            sent = await context.bot.send_audio(
                ADMIN_ID, msg.audio.file_id,
                caption=f"💬 دعم جديد من {info['name']} | @{info['username']} | {uid}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("رد عليه", callback_data=f"reply_support|{uid}")]])
            )
        elif msg.document:
            sent = await context.bot.send_document(
                ADMIN_ID, msg.document.file_id,
                caption=f"💬 دعم جديد من {info['name']} | @{info['username']} | {uid}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("رد عليه", callback_data=f"reply_support|{uid}")]])
            )
        else:
            sent = await context.bot.send_message(
                ADMIN_ID,
                f"💬 دعم جديد:\n👤 {info['name']} | @{info['username']} | {uid}\n\n(نوع رسالة غير مدعوم)"
            )

        active_support_chats[uid]["waiting"] = False
        active_support_chats[uid]["admin_msg_id"] = sent.message_id
        await update.message.reply_text("✅ تم إرسال رسالتك، انتظر رد الأدمن.")
        return

    # — الأدمن يرد
    if uid == ADMIN_ID and context.user_data.get("support_reply_to"):
        target = context.user_data.pop("support_reply_to")
        if msg.text:
            await context.bot.send_message(target, f"🟢 رد الأدمن:\n{msg.text}")
        elif msg.photo:
            await context.bot.send_photo(target, msg.photo[-1].file_id, caption="🟢 صورة من الأدمن")
        elif msg.video:
            await context.bot.send_video(target, msg.video.file_id, caption="🟢 فيديو من الأدمن")
        elif msg.audio:
            await context.bot.send_audio(target, msg.audio.file_id, caption="🟢 صوت من الأدمن")
        elif msg.document:
            await context.bot.send_document(target, msg.document.file_id, caption="🟢 ملف من الأدمن")
        else:
            await context.bot.send_message(target, "🟢 (نوع رسالة غير مدعوم من الأدمن)")
        await update.message.reply_text("✅ تم إرسال الرد.")
        active_support_chats.pop(target, None)
        return

# ============ تأكيد/رفض الاشتراك ================
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
    await context.bot.send_message(int(uid),
        "✅ *تم تفعيل اشتراكك بنجاح!* الآن جميع الميزات متاحة بدون حدود يومية.",
        parse_mode="Markdown"
    )
    await q.edit_message_text("✅ تم تفعيل الاشتراك.")

async def reject_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, uid = q.data.split("|", 1)
    pending_subs.discard(int(uid))
    await context.bot.send_message(int(uid),
        "❌ *تم رفض طلب اشتراكك.*\nللمساعدة استخدم زر الدعم.",
        parse_mode="Markdown"
    )
    await q.edit_message_text("🚫 تم رفض الاشتراك.")

# ============ OpenAI Chat ================
async def ask_openai(text: str) -> str:
    res = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": text}],
            max_tokens=256,
        )
    )
    return res["choices"][0]["message"]["content"].strip()

# ============ Message Handler ================
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    user = update.effective_user
    uid = user.id

    # — الأدمن يرسل دعم لمستخدم معيّن —
    if uid == ADMIN_ID and context.user_data.get("support_contact"):
        target = context.user_data.pop("support_contact")
        if msg.text:
            await context.bot.send_message(target, f"🛠️ رسالة من الأدمن:\n{msg.text}")
        elif msg.photo:
            await context.bot.send_photo(target, msg.photo[-1].file_id, caption="🛠️ صورة من الأدمن")
        elif msg.video:
            await context.bot.send_video(target, msg.video.file_id, caption="🛠️ فيديو من الأدمن")
        elif msg.audio:
            await context.bot.send_audio(target, msg.audio.file_id, caption="🛠️ صوت من الأدمن")
        elif msg.document:
            await context.bot.send_document(target, msg.document.file_id, caption="🛠️ ملف من الأدمن")
        else:
            await context.bot.send_message(target, "🛠️ (نوع رسالة غير مدعوم)")
        await msg.reply_text("✅ تم إرسال رسالتك.")
        return

    # — بث إعلان —
    if uid == ADMIN_ID and broadcast_mode.get(ADMIN_ID):
        await broadcast(update, context)
        return

    # — دردشات الدعم —
    if uid in active_support_chats or context.user_data.get("support_reply_to"):
        await support_msg(update, context)
        return

    # — نصوص وروابط فيديو/AI —
    if not msg.text:
        return

    text = msg.text.strip()
    store_user(user)

    if is_valid_url(text):
        if not check_limits(uid, "video"):
            await msg.reply_text("🚫 انتهى الحد المجاني من تنزيل الفيديو.")
            return
        msg_id = str(msg.message_id)
        url_store[msg_id] = text
        kb = [
            [InlineKeyboardButton("▶️ 720p", callback_data=f"video|720|{msg_id}")],
            [InlineKeyboardButton("▶️ 480p", callback_data=f"video|480|{msg_id}")],
            [InlineKeyboardButton("▶️ 360p", callback_data=f"video|360|{msg_id}")],
            [InlineKeyboardButton("🎵 MP3", callback_data=f"audio|360|{msg_id}")],
            [InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel|{msg_id}")],
        ]
        await msg.reply_text("اختر جودة التحميل:", reply_markup=InlineKeyboardMarkup(kb))
        return

    # — أي نص آخر → AI —
    await msg.reply_text("🤖 جارٍ التفكير ...")
    try:
        answer = await ask_openai(text)
        await msg.reply_text(answer)
    except Exception as e:
        await msg.reply_text(f"❌ خطأ في الرد: {e}")

# ============ Download Handler =============
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split("|")
    if parts[0] == "cancel":
        await q.message.delete()
        url_store.pop(parts[1], None)
        return
    action, quality, msg_id = parts
    url = url_store.get(msg_id)
    if not url:
        await q.answer("⚠️ انتهت صلاحية الرابط.")
        return

    os.makedirs("downloads", exist_ok=True)
    ext = "mp3" if action == "audio" else "mp4"
    outfile = f"downloads/{msg_id}.{ext}"
    await q.edit_message_text("⏳ جاري التحميل ...")

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
        await asyncio.get_event_loop().run_in_executor(None, runner)
    except subprocess.CalledProcessError as e:
        await context.bot.send_message(uid, f"❌ فشل التحميل: {e}")
        url_store.pop(msg_id, None)
        return

    if not os.path.exists(outfile):
        await context.bot.send_message(uid, "❌ لم أستطع العثور على الملف النهائي!")
        url_store.pop(msg_id, None)
        return

    try:
        with open(outfile, "rb") as f:
            if action == "audio":
                await context.bot.send_audio(q.from_user.id, f, caption=caption)
            else:
                await context.bot.send_video(q.from_user.id, f, caption=caption)
        await q.message.delete()
    except Exception as e:
        await context.bot.send_message(q.from_user.id, f"❌ خطأ أثناء الإرسال: {e}")
    finally:
        try: os.remove(outfile)
        except: pass
        url_store.pop(msg_id, None)

# ============ Register Handlers =============
app = Application.builder().token(BOT_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(admin_panel_callback,    pattern="^admin_"))
app.add_handler(CallbackQueryHandler(admin_support_user_callback, pattern="^admin_support_user\\|"))
app.add_handler(CallbackQueryHandler(reply_support_callback, pattern="^reply_support\\|"))
app.add_handler(CallbackQueryHandler(subscribe_request,      pattern="^subscribe_request$"))
app.add_handler(CallbackQueryHandler(confirm_sub,            pattern="^confirm_sub\\|"))
app.add_handler(CallbackQueryHandler(reject_sub,             pattern="^reject_sub\\|"))
app.add_handler(CallbackQueryHandler(support_start,          pattern="^support_start$"))
app.add_handler(CallbackQueryHandler(button_handler,         pattern="^(video|audio|cancel)\\|"))
app.add_handler(CommandHandler("support", support_start))
app.add_handler(MessageHandler(~filters.COMMAND,             message_handler))

# ============ Webhook aiohttp =============
# ============ Webhook aiohttp =============
# احتفظ بـ `app` كبوت تيليجرام، و أنشئ web_app لخادم aiohttp
bot_app = app

async def handle(request):
    if request.method == "POST":
        data = await request.json()
        update = Update.de_json(data, bot_app.bot)
        await bot_app.process_update(update)
        return web.Response(text="ok")
    return web.Response(status=405)

web_app = web.Application()
web_app.router.add_post(f"/{BOT_TOKEN}", handle)

# عند بدء web_app نشغّل بوت التيليجرام
async def on_startup(_):
    await bot_app.initialize()
    await bot_app.start()

# عند إيقاف web_app نوقف بوت التيليجرام
async def on_cleanup(_):
    await bot_app.stop()
    await bot_app.shutdown()

web_app.on_startup.append(on_startup)
web_app.on_cleanup.append(on_cleanup)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    web.run_app(web_app, host="0.0.0.0", port=port)
