import os
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
import psycopg2
from psycopg2.extras import RealDictCursor

# ------------- Logging Configuration -------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# ------------- Configuration -------------
ADMIN_ID = 337597459  # عدّل لآيديك!
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

# ------------- In-Memory Stores -------------
url_store = {}                # msg_id → URL
pending_subs = set()          # pending subscription requests
broadcast_mode = {}           # ADMIN_ID → True/False
active_support_chats = {}     # user_id → {name,username,waiting}
limits = {}                   # uid → {date,video,ai}

# ------------- Database Helpers -------------
def get_db_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL غير معرف")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

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
    cur.close()
    conn.close()

async def on_startup_db(app):
    try:
        init_db()
        logging.info("Database initialized successfully.")
    except Exception as e:
        logging.error(f"DB init error: {e}")

# ------------- Database Operations -------------
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
    conn.commit()
    cur.close(); conn.close()

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

# ------------- Utilities -------------
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

# ------------- Handlers -------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user; uid = user.id
    store_user_db(user)
    # Admin view
    if uid == ADMIN_ID:
        kb = [
            [InlineKeyboardButton("👥 المستخدمون", callback_data="admin_users")],
            [InlineKeyboardButton("🟢 المدفوعين", callback_data="admin_paidlist")],
            [InlineKeyboardButton("📢 إعلان", callback_data="admin_broadcast")],
            [InlineKeyboardButton("📊 إحصائيات", callback_data="admin_stats")],
            [InlineKeyboardButton("🆘 دردشات الدعم", callback_data="admin_supports")],
        ]
        text = "🛠️ لوحة تحكم الأدمن:"
    # Subscribed view
    elif is_subscribed_db(uid):
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("SELECT activated_at FROM subscriptions WHERE user_id=%s;", (uid,))
        row = cur.fetchone(); cur.close(); conn.close()
        activated = row['activated_at'] if row else datetime.now(timezone.utc)
        days_left = max(0, 30 - (datetime.now(timezone.utc) - activated).days)
        kb = [[InlineKeyboardButton("💬 دعم فني", callback_data="support_start")]]
        text = (
            f"✅ أهلاً يا {fullname(user)}، اشتراكك مفعل!\n"
            f"⏳ تبقى {days_left} يوم من اشتراكك.\n"
            "💬 لأي مشكلة اضغط دعم فني."
        )
    # Free user view
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
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def admin_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.from_user.id != ADMIN_ID: return
    d = q.data
    # Support user
    if d.startswith("admin_support_user|"):
        return await admin_support_user_callback(update, context)
    # List users
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
    # Paid list
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
    # Cancel subscription
    if d.startswith("admin_cancel_sub|"):
        uid=int(d.split("|",1)[1]); deactivate_subscription_db(uid)
        await q.answer("تم الإلغاء."); return await safe_edit(q, f"❌ تم إلغاء {uid}")
    # Broadcast
    if d == "admin_broadcast":
        broadcast_mode[ADMIN_ID] = True
        return await q.edit_message_text("✉️ أرسل نص للإعلان")
    # Stats
    if d == "admin_stats":
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS c FROM users;"); total=cur.fetchone()['c']
        cur.execute("SELECT COUNT(*) AS c FROM subscriptions;"); paid=cur.fetchone()['c']
        cur.close(); conn.close()
        vids=sum(l.get('video',0) for l in limits.values()); ai=sum(l.get('ai',0) for l in limits.values())
        txt=f"📊 اليوم: Users={total}, Subs={paid}, Videos={vids}, AI={ai}"
        return await safe_edit(q, txt)
    # Support chats
    if d == "admin_supports":
        kb=[[InlineKeyboardButton(f"{info['name']} @{info['username']}", callback_data=f"reply_support|{uid}")] for uid,info in active_support_chats.items()]
        if not kb: kb=[[InlineKeyboardButton("لا دردشات",callback_data="ignore")]]
        return await safe_edit(q, "🆘 دعم المستخدمين:", InlineKeyboardMarkup(kb))
    # Back to main
    kb_main=[
        [InlineKeyboardButton("👥 المستخدمون",callback_data="admin_users")],
        [InlineKeyboardButton("🟢 المدفوعين",callback_data="admin_paidlist")],
        [InlineKeyboardButton("📢 إعلان",callback_data="admin_broadcast")],
        [InlineKeyboardButton("📊 إحصائيات",callback_data="admin_stats")],
        [InlineKeyboardButton("🆘 دردشات الدعم",callback_data="admin_supports")]
    ]
    await safe_edit(q, "🛠️ لوحة الأدمن:", InlineKeyboardMarkup(kb_main))

async def admin_support_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    uid=int(q.data.split("|",1)[1]); context.user_data['support_contact']=uid
    await q.message.reply_text(f"📝 اكتب رسالتك للمستخدم {uid}")

async def reply_support_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    uid=int(q.data.split("|",1)[1]); context.user_data['support_reply_to']=uid
    await q.message.reply_text(f"📝 اكتب ردك للمستخدم {uid}")

async def broadcast_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg=update.message
    conn=get_db_connection(); cur=conn.cursor()
    cur.execute("SELECT id FROM users;"); uids=[r['id'] for r in cur.fetchall()]
    cur.close(); conn.close()
    sent=0
    for uid in uids:
        try:
            if msg.text: await context.bot.send_message(uid,msg.text)
            elif msg.photo: await context.bot.send_photo(uid,msg.photo[-1].file_id)
            elif msg.video: await context.bot.send_video(uid,msg.video.file_id)
            elif msg.audio: await context.bot.send_audio(uid,msg.audio.file_id)
            elif msg.document: await context.bot.send_document(uid,msg.document.file_id)
            sent+=1
        except: pass
    broadcast_mode[ADMIN_ID]=False
    await msg.reply_text(f"✅ بث إلى {sent} مستخدم")

async def support_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        q=update.callback_query; await q.answer(); user=q.from_user; target=q.message
    else:
        user=update.effective_user; target=update.message
    active_support_chats[user.id]={'name':fullname(user),'username':user.username or 'NO','waiting':True}
    await target.reply_text("✉️ أرسل رسالتك للدعم")

async def support_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg=update.message; uid=msg.from_user.id
    # initial support
    if uid in active_support_chats and active_support_chats[uid]['waiting']:
        info=active_support_chats[uid]
        kwargs={}
        if msg.text: kwargs['text']=f"💬 دعم من {info['name']} ({uid}):\n{msg.text}"
        elif msg.photo: kwargs['photo']=msg.photo[-1].file_id;kwargs['caption']=f"💬 صورة دعم من {uid}"
        elif msg.video: kwargs['video']=msg.video.file_id;kwargs['caption']=f"💬 فيديو دعم من {uid}"
        elif msg.audio: kwargs['audio']=msg.audio.file_id;kwargs['caption']=f"💬 صوت دعم من {uid}"
        elif msg.document: kwargs['document']=msg.document.file_id;kwargs['caption']=f"💬 ملف دعم من {uid}"
        await getattr(context.bot,'send_'+('message' if 'text' in kwargs else list(kwargs.keys())[0]))(
            ADMIN_ID,**kwargs,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("رد عليه",callback_data=f"reply_support|{uid}")]])
        )
        active_support_chats[uid]['waiting']=False
        return await msg.reply_text("✅ تم الإرسال للأدمن")
    # admin reply
    if uid==ADMIN_ID and context.user_data.get('support_reply_to'):
        target=context.user_data.pop('support_reply_to')
        if msg.text: await context.bot.send_message(target,f"🟢 رد الأدمن:\n{msg.text}")
        elif msg.photo: await context.bot.send_photo(target,msg.photo[-1].file_id,caption="🟢 صورة من الأدمن")
        elif msg.video: await context.bot.send_video(target,msg.video.file_id,caption="🟢 فيديو من الأدمن")
        elif msg.audio: await context.bot.send_audio(target,msg.audio.file_id,caption="🟢 صوت من الأدمن")
        elif msg.document: await context.bot.send_document(target,msg.document.file_id,caption="🟢 ملف من الأدمن")
        await msg.reply_text("✅ تم الرد للمستخدم")

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

async def ask_openai(text: str) -> str:
    res = await asyncio.get_event_loop().run_in_executor(None, lambda: openai.ChatCompletion.create(model="gpt-3.5-turbo",messages=[{"role":"user","content":text}],max_tokens=256))
    return res["choices"][0]["message"]["content"].strip()

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg=update.message
    if not msg: return
    store_user_db(msg.from_user)
    uid=msg.from_user.id
    # admin broadcast
    if uid==ADMIN_ID and broadcast_mode.get(ADMIN_ID):
        return await broadcast_handler(update,context)
    # support flow
    if uid in active_support_chats or context.user_data.get('support_reply_to'):
        return await support_msg(update,context)
    # URL handling
    if msg.text and is_valid_url(msg.text.strip()):
        if not (is_subscribed_db(uid) or uid==ADMIN_ID) and limits.get(uid,{}).get('video',0)>=DAILY_VIDEO_LIMIT:
            return await msg.reply_text("🚫 انتهى الحد المجاني.")
        mid=str(msg.message_id);url_store[mid]=msg.text.strip()
        kb=[
            [InlineKeyboardButton("▶️ 720p",callback_data=f"video|720|{mid}")],
            [InlineKeyboardButton("▶️ 480p",callback_data=f"video|480|{mid}")],
            [InlineKeyboardButton("▶️ 360p",callback_data=f"video|360|{mid}")],
            [InlineKeyboardButton("🎵 MP3",callback_data=f"audio|360|{mid}")],
            [InlineKeyboardButton("❌ إلغاء",callback_data=f"cancel|{mid}")]
        ]
        return await msg.reply_text("اختر الجودة:",reply_markup=InlineKeyboardMarkup(kb))
    # AI fallback
    if msg.text:
        await msg.reply_text("🤖 التفكير...")
        try:
            ans=await ask_openai(msg.text.strip());await msg.reply_text(ans)
        except Exception as e:
            await msg.reply_text(f"❌ خطأ AI: {e}")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query;await q.answer()
    uid=q.from_user.id
    parts=q.data.split("|")
    if parts[0]=="cancel":
        await q.message.delete();url_store.pop(parts[1],None);return
    action,quality,msg_id=parts;url=url_store.get(msg_id)
    if not url: return await q.answer("⚠️ رابط منتهي.")
   if not os.path.exists(COOKIES_FILE) or os.path.getsize(COOKIES_FILE) == 0:
    text = """⚠️ لا يوجد ملف كوكيز.
يمكنك تحميل الفيديو الآن من فيسبوك أو إنستاغرام أو تيك توك.
وسيتم دعمه عبر الكوكيز لاحقاً."""
    await q.message.reply_text(text)
    return

    os.makedirs("downloads",exist_ok=True)
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
    except subprocess.CalledProcessError as e:
        await context.bot.send_message(uid, f"❌ فشل التحميل: {e}")
        url_store.pop(msg_id, None)
        return

    if not os.path.exists(outfile):
        await context.bot.send_message(uid, "❌ الملف غير موجود!")
        url_store.pop(msg_id, None)
        return

    try:
        with open(outfile, "rb") as f:
            if action == "audio":
                await context.bot.send_audio(uid, f, caption=cap)
            else:
                await context.bot.send_video(uid, f, caption=cap)
        await q.message.delete()
    except Exception as e:
        await context.bot.send_message(uid, f"❌ خطأ: {e}")
    finally:
        url_store.pop(msg_id, None)
        try:
            os.remove(outfile)
        except:
            pass
        url_store.pop(msg_id,None);
        try: os.remove(outfile)
        except: pass

# ------------- App & Webhook Setup -------------
app = Application.builder().token(BOT_TOKEN).build()
app.on_startup.append(on_startup_db)

# Register Handlers
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(admin_panel_callback, pattern="^admin_"))
app.add_handler(CallbackQueryHandler(admin_support_user_callback, pattern="^admin_support_user\\|"))
app.add_handler(CallbackQueryHandler(reply_support_callback, pattern="^reply_support\\|"))
app.add_handler(CallbackQueryHandler(subscribe_request, pattern="^subscribe_request$"))
app.add_handler(CallbackQueryHandler(confirm_sub, pattern="^confirm_sub\\|"))
app.add_handler(CallbackQueryHandler(reject_sub, pattern="^reject_sub\\|"))
app.add_handler(CallbackQueryHandler(broadcast_handler, pattern="^admin_broadcast$"))
app.add_handler(CallbackQueryHandler(button_handler, pattern="^(video|audio|cancel)\\|"))
app.add_handler(CallbackQueryHandler(support_start, pattern="^support_start$"))
app.add_handler(MessageHandler(~filters.COMMAND, message_handler))

# Webhook
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
```
