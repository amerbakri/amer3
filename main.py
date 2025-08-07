import os
import subprocess
import logging
import functools
import urllib.parse as up
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
import psycopg2
from psycopg2.extras import RealDictCursor

# ====== إعدادات عامة ======
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
ADMIN_ID = 337597459
ORANGE_NUMBER = "0781200500"
BOT_TOKEN = os.getenv("BOT_TOKEN", "ضع_توكن_البوت")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "ضع_OPENAI")
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://amerr_user:ubrbdqmywTnokDWpOFOBPV76PFE3dTz5@dpg-d289c3fdiees73det7og-a/amerr"
)
COOKIES_FILE = "cookies.txt"
DAILY_VIDEO_LIMIT = 3
DAILY_AI_LIMIT = 5
openai.api_key = OPENAI_API_KEY

quality_map = {
    "720": "bestvideo[height<=720]+bestaudio/best",
    "480": "bestvideo[height<=480]+bestaudio/best",
    "360": "bestvideo[height<=360]+bestaudio/best",
}

# ====== متغيرات الذاكرة ======
url_store = {}
pending_subs = set()
broadcast_mode = {}
active_support_chats = {}
limits = {}

# ====== قاعدة البيانات ======
def get_db_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL غير معرف")
    up.uses_netloc.append("postgres")
    url = up.urlparse(DATABASE_URL)
    return psycopg2.connect(
        dbname=url.path[1:],
        user=url.username,
        password=url.password,
        host=url.hostname,
        port=url.port,
        cursor_factory=RealDictCursor
    )

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id BIGINT PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            user_id BIGINT PRIMARY KEY,
            activated_at TIMESTAMPTZ NOT NULL
        );
    """)
    conn.commit()
    cur.close(); conn.close()

def store_user_db(user):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO users (id,username,first_name,last_name)
        VALUES (%s,%s,%s,%s)
        ON CONFLICT (id) DO UPDATE SET
          username=EXCLUDED.username,
          first_name=EXCLUDED.first_name,
          last_name=EXCLUDED.last_name;
        """, (user.id,user.username,user.first_name,user.last_name)
    )
    conn.commit(); cur.close(); conn.close()

def activate_subscription_db(uid: int):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO subscriptions (user_id,activated_at)
        VALUES (%s,NOW())
        ON CONFLICT (user_id) DO UPDATE SET activated_at=EXCLUDED.activated_at;
        """, (uid,)
    )
    conn.commit(); cur.close(); conn.close()

def deactivate_subscription_db(uid: int):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM subscriptions WHERE user_id=%s;", (uid,))
    conn.commit(); cur.close(); conn.close()

def is_subscribed_db(uid: int) -> bool:
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM subscriptions WHERE user_id=%s;", (uid,))
    res = cur.fetchone() is not None
    cur.close(); conn.close()
    return res

def get_subscription_days_left(uid: int) -> int:
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT activated_at FROM subscriptions WHERE user_id=%s;", (uid,))
    row = cur.fetchone()
    cur.close(); conn.close()
    if row:
        activated = row['activated_at']
        days_left = max(0, 30 - (datetime.now(timezone.utc) - activated).days)
        return days_left
    return 0

# ====== أدوات ======
def fullname(user):
    return f"{user.first_name or ''} {user.last_name or ''}".strip()

def is_valid_url(text: str) -> bool:
    return bool(re.match(
        r"^(https?://)?(www\.)?"
        r"(youtube\.com|youtu\.be|tiktok\.com|instagram\.com|facebook\.com|fb\.watch)/.+",
        text
    ))

async def safe_edit(query, text, kb=None):
    try:
        await query.edit_message_text(text, reply_markup=kb)
    except:
        pass

def get_limits(uid):
    today = datetime.now().strftime("%Y-%m-%d")
    lim = limits.get(uid)
    if not lim or lim.get("date") != today:
        limits[uid] = {"date": today, "video": 0, "ai": 0}
    return limits[uid]

def increment_limit(uid, key):
    lim = get_limits(uid)
    lim[key] = lim.get(key, 0) + 1

# ====== لوحة الأدمن ======
async def admin_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.from_user.id != ADMIN_ID: return
    d = q.data
    if d.startswith("admin_support_user|"):
        uid = int(d.split("|",1)[1])
        context.user_data['support_contact'] = uid
        await q.message.reply_text(f"📝 اكتب رسالتك للمستخدم {uid}")
        return
    if d == "admin_users":
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("SELECT id,username FROM users;"); rows = cur.fetchall(); cur.close(); conn.close()
        kb = []
        for r in rows:
            uid, uname = r['id'], r['username']
            status = "مشترك" if is_subscribed_db(uid) else "غير مشترك"
            kb.append([
                InlineKeyboardButton(f"{uname or 'NO'} | {status}", callback_data="ignore"),
                InlineKeyboardButton("🆘 دعم", callback_data=f"admin_support_user|{uid}")
            ])
        if not kb: kb = [[InlineKeyboardButton("لا مستخدمين", callback_data="ignore")]]
        return await safe_edit(q, "👥 المستخدمون:", InlineKeyboardMarkup(kb))
    if d == "admin_paidlist":
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("SELECT user_id FROM subscriptions;"); subs = [r['user_id'] for r in cur.fetchall()]
        cur.close(); conn.close()
        kb = [[
            InlineKeyboardButton(str(uid), callback_data="ignore"),
            InlineKeyboardButton("❌ إلغاء", callback_data=f"admin_cancel_sub|{uid}")
        ] for uid in subs]
        if not kb: kb=[[InlineKeyboardButton("لا أحد",callback_data="ignore")]]
        return await safe_edit(q, "🟢 المدفوعين:", InlineKeyboardMarkup(kb))
    if d.startswith("admin_cancel_sub|"):
        uid=int(d.split("|",1)[1]); deactivate_subscription_db(uid)
        await q.answer("تم الإلغاء."); return await safe_edit(q, f"❌ تم إلغاء {uid}")
    if d == "admin_broadcast":
        broadcast_mode[ADMIN_ID] = True
        return await q.edit_message_text("✉️ أرسل نص/صورة/فيديو للإعلان لكل المستخدمين")
    if d == "admin_stats":
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS c FROM users;")
        total = cur.fetchone()['c']
        cur.execute("SELECT COUNT(*) AS c FROM subscriptions;")
        paid = cur.fetchone()['c']
        cur.close()
        conn.close()
        vids = sum(l.get('video', 0) for l in limits.values())
        ai = sum(l.get('ai', 0) for l in limits.values())
        txt = (
            "📊 إحصائيات اليوم:\n"
            f"👤 جميع المستخدمين: {total}\n"
            f"💎 المشتركين: {paid}\n"
            f"📥 تنزيلات الفيديو اليوم: {vids}\n"
            f"🤖 استخدام الذكاء الصناعي اليوم: {ai}"
        )
        return await safe_edit(q, txt)
    if d == "admin_supports":
        kb=[[InlineKeyboardButton(f"{info['name']} @{info['username']}", callback_data=f"reply_support|{uid}")] for uid,info in active_support_chats.items()]
        if not kb: kb=[[InlineKeyboardButton("لا دردشات",callback_data="ignore")]]
        return await safe_edit(q, "🆘 دعم المستخدمين:", InlineKeyboardMarkup(kb))
    kb_main=[
        [InlineKeyboardButton("👥 المستخدمون",callback_data="admin_users")],
        [InlineKeyboardButton("🟢 المدفوعين",callback_data="admin_paidlist")],
        [InlineKeyboardButton("📢 إعلان",callback_data="admin_broadcast")],
        [InlineKeyboardButton("📊 إحصائيات",callback_data="admin_stats")],
        [InlineKeyboardButton("🆘 دردشات الدعم",callback_data="admin_supports")]
    ]
    await safe_edit(q, "🛠️ لوحة الأدمن:", InlineKeyboardMarkup(kb_main))

async def reply_support_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    uid=int(q.data.split("|",1)[1]); context.user_data['support_reply_to']=uid
    await q.message.reply_text(f"📝 اكتب ردك للمستخدم {uid}")

# ====== الاشتراك ======
async def subscribe_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    uid=q.from_user.id
    if uid in pending_subs:
        return await q.answer("❗️ طلبك قيد المراجعة.")
    pending_subs.add(uid)
    info=f"📥 طلب اشتراك جديد\n👤 {fullname(q.from_user)} | @{q.from_user.username} | {uid}"
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ تفعيل",callback_data=f"confirm_sub|{uid}"),InlineKeyboardButton("❌ رفض",callback_data=f"reject_sub|{uid}")]])
    await context.bot.send_message(ADMIN_ID,info,reply_markup=kb)
    return await q.edit_message_text("✅ تم إرسال طلبك للأدمن")

async def confirm_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query;await q.answer()
    uid=int(q.data.split("|",1)[1]);activate_subscription_db(uid);pending_subs.discard(uid)
    await context.bot.send_message(uid,"✅ تم تفعيل اشتراكك!")
    return await q.edit_message_text("✅ اشتراك مفعل.")

async def reject_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query;await q.answer()
    uid=int(q.data.split("|",1)[1]);pending_subs.discard(uid)
    await context.bot.send_message(uid,"❌ تم رفض الاشتراك.")
    return await q.edit_message_text("🚫 تم الرفض.")

# ====== ذكاء اصطناعي ======
async def ask_openai(text: str) -> str:
    res = await asyncio.get_event_loop().run_in_executor(None, lambda: openai.ChatCompletion.create(model="gpt-3.5-turbo", messages=[{"role": "user", "content": text}], max_tokens=256))
    return res["choices"][0]["message"]["content"].strip()

# ====== الهاندلر الموحد ======
async def main_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message or (update.callback_query and update.callback_query.message)
    user = (update.effective_user or
            (update.callback_query and update.callback_query.from_user))
    uid = user.id
    store_user_db(user)
    lim = get_limits(uid)
    is_admin = uid == ADMIN_ID

    # بث الأدمن
    if is_admin and broadcast_mode.get(ADMIN_ID):
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("SELECT id FROM users;"); uids = [r['id'] for r in cur.fetchall()]
        cur.close(); conn.close(); sent = 0
        for u in uids:
            try:
                if update.message.text: await context.bot.send_message(u, update.message.text)
                elif update.message.photo: await context.bot.send_photo(u, update.message.photo[-1].file_id)
                elif update.message.video: await context.bot.send_video(u, update.message.video.file_id)
                elif update.message.audio: await context.bot.send_audio(u, update.message.audio.file_id)
                elif update.message.document: await context.bot.send_document(u, update.message.document.file_id)
                sent += 1
            except: pass
        broadcast_mode[ADMIN_ID] = False
        await msg.reply_text(f"✅ بث إلى {sent} مستخدم")
        return

    # دعم فني
    if uid in active_support_chats or context.user_data.get('support_reply_to') or context.user_data.get('support_contact'):
        # المستخدم يرسل دعم للأدمن
        if uid in active_support_chats and active_support_chats[uid]['waiting']:
            info = active_support_chats[uid]
            kwargs = {}
            if update.message.text: kwargs['text'] = f"💬 دعم من {info['name']} ({uid}):\n{update.message.text}"
            elif update.message.photo: kwargs['photo'] = update.message.photo[-1].file_id; kwargs['caption'] = f"💬 صورة دعم من {uid}"
            elif update.message.video: kwargs['video'] = update.message.video.file_id; kwargs['caption'] = f"💬 فيديو دعم من {uid}"
            elif update.message.audio: kwargs['audio'] = update.message.audio.file_id; kwargs['caption'] = f"💬 صوت دعم من {uid}"
            elif update.message.document: kwargs['document'] = update.message.document.file_id; kwargs['caption'] = f"💬 ملف دعم من {uid}"
            await getattr(context.bot, 'send_' + ('message' if 'text' in kwargs else list(kwargs.keys())[0]))(
                ADMIN_ID, **kwargs,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("رد عليه", callback_data=f"reply_support|{uid}")]])
            )
            active_support_chats[uid]['waiting'] = False
            return await msg.reply_text("✅ تم الإرسال للأدمن")
        # الأدمن يرد على المستخدم
        if is_admin and context.user_data.get('support_reply_to'):
            target = context.user_data.pop('support_reply_to')
            if update.message.text: await context.bot.send_message(target, f"🟢 رد الأدمن:\n{update.message.text}")
            elif update.message.photo: await context.bot.send_photo(target, update.message.photo[-1].file_id, caption="🟢 صورة من الأدمن")
            elif update.message.video: await context.bot.send_video(target, update.message.video.file_id, caption="🟢 فيديو من الأدمن")
            elif update.message.audio: await context.bot.send_audio(target, update.message.audio.file_id, caption="🟢 صوت من الأدمن")
            elif update.message.document: await context.bot.send_document(target, update.message.document.file_id, caption="🟢 ملف من الأدمن")
            await msg.reply_text("✅ تم الرد للمستخدم")
            return
        # الأدمن في وضع دعم مباشر
        if is_admin and context.user_data.get('support_contact'):
            target_id = context.user_data.pop('support_contact')
            if update.message.text:
                await context.bot.send_message(target_id, f"📩 دعم من الأدمن:\n{update.message.text}")
            elif update.message.photo:
                await context.bot.send_photo(target_id, update.message.photo[-1].file_id, caption=update.message.caption or "📩 صورة دعم من الأدمن")
            elif update.message.video:
                await context.bot.send_video(target_id, update.message.video.file_id, caption=update.message.caption or "📩 فيديو دعم من الأدمن")
            elif update.message.audio:
                await context.bot.send_audio(target_id, update.message.audio.file_id, caption=update.message.caption or "📩 صوت دعم من الأدمن")
            elif update.message.document:
                await context.bot.send_document(target_id, update.message.document.file_id, caption=update.message.caption or "📩 ملف دعم من الأدمن")
            else:
                await context.bot.send_message(target_id, "📩 وصلك دعم من الأدمن.")
            await msg.reply_text("✅ تم إرسال رسالتك للمستخدم وتم إنهاء الجلسة.")
            return

    # رسالة /start أو زر
    if (update.message and update.message.text and update.message.text.strip() == "/start") or (update.callback_query and update.callback_query.data.startswith("start")):
        kb = []
        if is_admin:
            kb = [
                [InlineKeyboardButton("👥 المستخدمون", callback_data="admin_users")],
                [InlineKeyboardButton("🟢 المدفوعين", callback_data="admin_paidlist")],
                [InlineKeyboardButton("📢 إعلان", callback_data="admin_broadcast")],
                [InlineKeyboardButton("📊 إحصائيات", callback_data="admin_stats")],
                [InlineKeyboardButton("🆘 دردشات الدعم", callback_data="admin_supports")],
            ]
            text = "🛠️ لوحة تحكم الأدمن:"
        elif is_subscribed_db(uid):
            days_left = get_subscription_days_left(uid)
            kb = [[InlineKeyboardButton("💬 دعم فني", callback_data="support_start")]]
            text = (
                f"✅ أهلاً يا {fullname(user)}، اشتراكك مفعل!\n"
                f"⏳ تبقى {days_left} يوم من اشتراكك.\n"
                "💬 لأي مشكلة اضغط دعم فني."
            )
        else:
            kb = [
                [InlineKeyboardButton("💎 اشترك الآن", callback_data="subscribe_request")],
                [InlineKeyboardButton("💬 دعم فني", callback_data="support_start")],
            ]
            text = (
                f"👋 أهلاً بك!\n"
                f"🔓 حمّل {DAILY_VIDEO_LIMIT} فيديوهات مجاناً يومياً أو اشترك.\n"
                f"حوّل على أورنج موني {ORANGE_NUMBER} ثم اضغط اشتراك الآن."
            )
        await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
        return

    # روابط تحميل الفيديو
    if update.message and update.message.text and is_valid_url(update.message.text.strip()):
        if not (is_subscribed_db(uid) or is_admin) and lim['video'] >= DAILY_VIDEO_LIMIT:
            return await msg.reply_text("🚫 انتهى الحد المجاني اليومي، اشترك للمتابعة!")
        mid = str(update.message.message_id); url_store[mid] = update.message.text.strip()
        kb = [
            [InlineKeyboardButton("▶️ 720p", callback_data=f"video|720|{mid}")],
            [InlineKeyboardButton("▶️ 480p", callback_data=f"video|480|{mid}")],
            [InlineKeyboardButton("▶️ 360p", callback_data=f"video|360|{mid}")],
            [InlineKeyboardButton("🎵 MP3", callback_data=f"audio|360|{mid}")],
            [InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel|{mid}")]
        ]
        return await msg.reply_text("اختر الجودة:", reply_markup=InlineKeyboardMarkup(kb))

    # ذكاء اصطناعي - حدود يومية
    if update.message and update.message.text:
        if not (is_subscribed_db(uid) or is_admin) and lim['ai'] >= DAILY_AI_LIMIT:
            return await msg.reply_text("🚫 انتهى الحد المجاني لاستفسارات الذكاء الصناعي لليوم.")
        await msg.reply_text("🤖 التفكير...")
        try:
            ans = await ask_openai(update.message.text.strip())
            await msg.reply_text(ans)
            increment_limit(uid, "ai")
        except Exception as e:
            await msg.reply_text(f"❌ خطأ AI: {e}")

# ====== أزرار التحميل ======
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    parts = q.data.split("|")
    if parts[0] == "cancel":
        await q.message.delete()
        url_store.pop(parts[1], None)
        return

    action, quality, msg_id = parts
    url = url_store.pop(msg_id, None)
    if not url:
        await q.answer("⚠️ رابط منتهي.")
        return

    lim = get_limits(uid)
    is_admin = uid == ADMIN_ID

    if not (is_subscribed_db(uid) or is_admin) and lim['video'] >= DAILY_VIDEO_LIMIT:
        return await q.message.reply_text("🚫 انتهى الحد المجاني اليومي، اشترك للمتابعة!")

    if not os.path.exists(COOKIES_FILE) or os.path.getsize(COOKIES_FILE) == 0:
        text = (
            "⚠️ لا يوجد ملف كوكيز.\n"
            "يمكنك تحميل الفيديو الآن من فيسبوك أو إنستاغرام أو تيك توك.\n"
            "وسيتم دعم يوتيوب لاحقاً."
        )
        await q.message.reply_text(text)
        return

    os.makedirs("downloads", exist_ok=True)
    ext = "mp3" if action == "audio" else "mp4"
    outfile = f"downloads/{msg_id}.{ext}"
    await q.edit_message_text("⏳ جاري التحميل...")

    # yt-dlp command
    if action == "audio":
        cmd = [
            "yt-dlp", "--cookies", COOKIES_FILE,
            "-f", "bestaudio[ext=m4a]/bestaudio/best",
            "--extract-audio", "--audio-format", "mp3",
            "-o", outfile,
            url
        ]
        cap = "🎵 صوت فقط"
    else:
        fmt = quality_map.get(quality, "best")
        cmd = [
            "yt-dlp", "--cookies", COOKIES_FILE,
            "-f", fmt,
            "--merge-output-format", "mp4",
            "-o", outfile,
            url
        ]
        cap = f"🎬 جودة {quality}p"

    runner = functools.partial(subprocess.run, cmd, check=True)
    try:
        await asyncio.get_event_loop().run_in_executor(None, runner)
    except Exception:
        await context.bot.send_message(uid,
            "📢 حالياً التحميل من يوتيوب متوقف مؤقتاً بسبب الضغط أو تحديث النظام.\n"
            "🔄 جرب بعد ساعتين أو أكثر، وإن شاء الله الخدمة بترجع قريباً!\n"
            "✌️ في الوقت الحالي بتقدر تحمل من فيسبوك، إنستاغرام أو تيك توك بدون مشاكل.\n"
            "شكراً لصبرك وتفهمك، وأي استفسار الدعم جاهز دائماً! ❤️"
        )
        return

    if not os.path.exists(outfile):
        await context.bot.send_message(uid,
            "❌ لم يتم العثور على الملف!\n"
            "جرب مجدداً أو اختر رابطاً آخر."
        )
        return

    import math
MAX_TG_SIZE_MB = 49.5  # الحد الآمن

try:
    # افحص حجم الملف قبل الإرسال
    file_size_mb = os.path.getsize(outfile) / (1024 * 1024)
    if file_size_mb > MAX_TG_SIZE_MB:
        await context.bot.send_message(
            uid,
            f"❌ الفيديو أكبر من الحد المسموح لإرساله عبر تليجرام بوت (الحجم: {math.ceil(file_size_mb)}MB).\n"
            "جرب رابط آخر أو اختر جودة أقل!"
        )
        os.remove(outfile)
        return

    with open(outfile, "rb") as f:
        if action == "audio":
            await context.bot.send_audio(uid, f, caption=cap)
        else:
            await context.bot.send_video(uid, f, caption=cap)
    await q.message.delete()
    increment_limit(uid, "video")
except Exception:
    await context.bot.send_message(
        uid,
        "❌ حدث خطأ أثناء إرسال الملف للمستخدم.\n"
        "جرب رابط آخر أو تواصل مع الدعم."
    )
finally:
    try:
        os.remove(outfile)
    except:
        pass

# ====== البوت & الويب هوك ======
init_db()
app = Application.builder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", main_handler))
app.add_handler(CallbackQueryHandler(admin_panel_callback, pattern="^admin_"))
app.add_handler(CallbackQueryHandler(reply_support_callback, pattern="^reply_support\\|"))
app.add_handler(CallbackQueryHandler(subscribe_request, pattern="^subscribe_request$"))
app.add_handler(CallbackQueryHandler(confirm_sub, pattern="^confirm_sub\\|"))
app.add_handler(CallbackQueryHandler(reject_sub, pattern="^reject_sub\\|"))
app.add_handler(CallbackQueryHandler(button_handler, pattern="^(video|audio|cancel)\\|"))
app.add_handler(CallbackQueryHandler(main_handler, pattern="^support_start$"))
app.add_handler(MessageHandler(filters.ALL, main_handler))

async def handle(request):
    if request.method=="POST":
        data=await request.json()
        update=Update.de_json(data,app.bot)
        await app.process_update(update)
        return web.Response(text="ok")
    return web.Response(status=405)

web_app=web.Application()
web_app.router.add_post(f"/{BOT_TOKEN}", handle)
web_app.on_startup.append(lambda _: app.initialize())
web_app.on_startup.append(lambda _: app.start())
web_app.on_cleanup.append(lambda _: app.stop())
web_app.on_cleanup.append(lambda _: app.shutdown())

if __name__=="__main__":
    port=int(os.getenv("PORT",10000))
    web.run_app(web_app,host="0.0.0.0",port=port)
