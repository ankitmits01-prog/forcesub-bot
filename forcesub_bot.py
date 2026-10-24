"""
Force-Subscribe Telegram Bot (private channels, join-request based)

Install:   pip install "python-telegram-bot>=21.0"
Run:       BOT_TOKEN=xxx OWNER_ID=123456789 python forcesub_bot.py

Setup:
 1. Bot ko apne 2 MAIN channels me admin banao (invite-link permission ke saath).
 2. Bot ko PROTECTED channel(s) me bhi admin banao (invite users + add members permission).
 3. Bot ko private chat me /start karo.
 4. Kisi bhi channel ka koi message bot ko FORWARD karo -> buttons aayenge
    (Main 1 / Main 2 / Batch channel) -> button dabao, bas. Link bot khud banayega.
"""
import json
import logging
import os
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ====== YAHAN APNI DETAILS BHARO ======
BOT_TOKEN = os.environ.get("BOT_TOKEN", "PASTE_BOT_TOKEN_HERE")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))  # apni numeric Telegram ID, e.g. 123456789
# =======================================
DB = Path("data.json")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("forcesub")


# ---------------- storage ----------------
def load():
    if DB.exists():
        return json.loads(DB.read_text())
    return {"main": {}, "protected": {}}


def save(d):
    DB.write_text(json.dumps(d, indent=2))


def owner_only(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != OWNER_ID:
            return
        return await func(update, ctx)

    return wrapper


# ---------------- helpers ----------------
async def is_member(bot, chat_id, user_id) -> bool:
    try:
        m = await bot.get_chat_member(chat_id, user_id)
        return m.status in ("member", "administrator", "creator") or (
            m.status == "restricted" and getattr(m, "is_member", False)
        )
    except TelegramError:
        return False


async def missing_channels(bot, user_id):
    d = load()
    missing = []
    for slot in ("1", "2"):
        ch = d["main"].get(slot)
        if ch and not await is_member(bot, int(ch["id"]), user_id):
            missing.append((slot, ch))
    return missing


def join_keyboard(target_chat_id):
    d = load()
    rows = []
    for slot in ("1", "2"):
        ch = d["main"].get(slot)
        if ch:
            rows.append([InlineKeyboardButton(f"📢 Join Channel {slot}", url=ch["link"])])
    rows.append([InlineKeyboardButton("✅ Maine join kar liya", callback_data=f"check:{target_chat_id}")])
    return InlineKeyboardMarkup(rows)


# ---------------- join request flow ----------------
async def on_join_request(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    req = update.chat_join_request
    d = load()
    if str(req.chat.id) not in d["protected"]:
        return
    user = req.from_user

    if not await missing_channels(ctx.bot, user.id):
        try:
            await req.approve()
            await ctx.bot.send_message(user.id, "✅ Aapki join request approve ho gayi. Welcome!")
        except TelegramError as e:
            log.warning("approve failed: %s", e)
        return

    try:
        await ctx.bot.send_message(
            user.id,
            "⚠️ Is channel me join hone se pehle neeche diye gaye channels join karo, "
            "phir '✅ Maine join kar liya' dabao.",
            reply_markup=join_keyboard(req.chat.id),
        )
    except TelegramError as e:
        log.warning("dm failed: %s", e)


async def on_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    target = int(q.data.split(":")[1])
    user_id = q.from_user.id

    if await missing_channels(ctx.bot, user_id):
        await q.answer("❌ Abhi dono channels join nahi kiye!", show_alert=True)
        return
    try:
        await ctx.bot.approve_chat_join_request(target, user_id)
        await q.answer("✅ Approved!", show_alert=True)
        await q.edit_message_text("✅ Aapki join request approve ho gayi. Welcome!")
    except TelegramError as e:
        log.warning("approve failed: %s", e)
        await q.answer("Request expire ho gayi. Channel link se dobara request bhejo.", show_alert=True)


# ---------------- owner commands ----------------
HELP = (
    "<b>Force-Sub Bot</b>\n\n"
    "✅ <b>Easy tareeka:</b> kisi bhi channel ka koi message mujhe forward karo, "
    "phir button se chuno ki wo Main 1, Main 2 ya Batch channel hai.\n\n"
    "<b>Commands (optional)</b>\n\n"
    "<b>Main channels (jo join karna zaroori hai)</b>\n"
    "/setmain &lt;1|2&gt; &lt;channel_id&gt; &lt;invite_link&gt; — main channel set karo\n"
    "/setlink &lt;1|2&gt; &lt;new_link&gt; — main channel ka link manually badlo\n"
    "/newlink &lt;1|2&gt; — bot khud naya link banayega (purana revoke)\n\n"
    "<b>Protected channels (jisme bot admin hai)</b>\n"
    "/add &lt;channel_id&gt; — channel add karo, join-request link milega\n"
    "/newprotected &lt;channel_id&gt; — us channel ka naya link\n"
    "/remove &lt;channel_id&gt; — channel hatao\n\n"
    "/status — current settings"
)


@owner_only
async def start(update: Update, ctx):
    await update.message.reply_html(HELP)


@owner_only
async def setmain(update: Update, ctx):
    try:
        slot, cid, link = ctx.args[0], int(ctx.args[1]), ctx.args[2]
        assert slot in ("1", "2")
    except Exception:
        return await update.message.reply_text("Use: /setmain 1 -1001234567890 https://t.me/+abcd")
    try:
        chat = await ctx.bot.get_chat(cid)
        me = await ctx.bot.get_chat_member(cid, ctx.bot.id)
        if me.status != "administrator":
            return await update.message.reply_text("❌ Bot us channel me admin nahi hai.")
    except TelegramError as e:
        return await update.message.reply_text(f"❌ Error: {e}")
    d = load()
    d["main"][slot] = {"id": cid, "link": link}
    save(d)
    await update.message.reply_text(f"✅ Main channel {slot} set: {chat.title}")


@owner_only
async def setlink(update: Update, ctx):
    try:
        slot, link = ctx.args[0], ctx.args[1]
        assert slot in ("1", "2")
    except Exception:
        return await update.message.reply_text("Use: /setlink 1 https://t.me/+newlink")
    d = load()
    if slot not in d["main"]:
        return await update.message.reply_text("Pehle /setmain se channel set karo.")
    d["main"][slot]["link"] = link
    save(d)
    await update.message.reply_text(f"✅ Channel {slot} ka link update ho gaya.")


@owner_only
async def newlink(update: Update, ctx):
    try:
        slot = ctx.args[0]
        assert slot in ("1", "2")
    except Exception:
        return await update.message.reply_text("Use: /newlink 1")
    d = load()
    if slot not in d["main"]:
        return await update.message.reply_text("Pehle /setmain se channel set karo.")
    try:
        link = await ctx.bot.export_chat_invite_link(d["main"][slot]["id"])
    except TelegramError as e:
        return await update.message.reply_text(f"❌ Error: {e}")
    d["main"][slot]["link"] = link
    save(d)
    await update.message.reply_text(f"✅ Naya link (purana band ho gaya):\n{link}")


async def _make_protected_link(bot, cid, old=None):
    if old:
        try:
            await bot.revoke_chat_invite_link(cid, old)
        except TelegramError:
            pass
    inv = await bot.create_chat_invite_link(cid, creates_join_request=True, name="forcesub")
    return inv.invite_link


@owner_only
async def add(update: Update, ctx):
    try:
        cid = int(ctx.args[0])
    except Exception:
        return await update.message.reply_text("Use: /add -1001234567890")
    try:
        chat = await ctx.bot.get_chat(cid)
        link = await _make_protected_link(ctx.bot, cid)
    except TelegramError as e:
        return await update.message.reply_text(f"❌ Error (bot admin hai?): {e}")
    d = load()
    d["protected"][str(cid)] = {"title": chat.title, "link": link}
    save(d)
    await update.message.reply_text(f"✅ {chat.title} protected.\nIs link ko share karo:\n{link}")


@owner_only
async def newprotected(update: Update, ctx):
    try:
        cid = str(int(ctx.args[0]))
    except Exception:
        return await update.message.reply_text("Use: /newprotected -1001234567890")
    d = load()
    if cid not in d["protected"]:
        return await update.message.reply_text("Ye channel add nahi hai.")
    try:
        link = await _make_protected_link(ctx.bot, int(cid), d["protected"][cid]["link"])
    except TelegramError as e:
        return await update.message.reply_text(f"❌ Error: {e}")
    d["protected"][cid]["link"] = link
    save(d)
    await update.message.reply_text(f"✅ Naya link:\n{link}")


@owner_only
async def remove(update: Update, ctx):
    try:
        cid = str(int(ctx.args[0]))
    except Exception:
        return await update.message.reply_text("Use: /remove -1001234567890")
    d = load()
    if d["protected"].pop(cid, None):
        save(d)
        await update.message.reply_text("✅ Hata diya.")
    else:
        await update.message.reply_text("Ye channel list me nahi tha.")


@owner_only
async def status(update: Update, ctx):
    d = load()
    lines = ["<b>Main channels</b>"]
    for slot in ("1", "2"):
        ch = d["main"].get(slot)
        lines.append(f"{slot}: {ch['id']} — {ch['link']}" if ch else f"{slot}: not set")
    lines.append("\n<b>Protected channels</b>")
    for cid, v in d["protected"].items():
        lines.append(f"{v['title']} ({cid})\n{v['link']}")
    if not d["protected"]:
        lines.append("none")
    await update.message.reply_html("\n".join(lines), disable_web_page_preview=True)


# ---------------- forward-to-add flow ----------------
@owner_only
async def on_forward(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    origin = getattr(msg, "forward_origin", None)
    chat = getattr(origin, "chat", None) if origin else getattr(msg, "forward_from_chat", None)
    if not chat or chat.type != "channel":
        return await msg.reply_text(
            "❌ Channel ka message forward karo. (Agar channel me 'Restrict saving content' on hai "
            "ya forward hidden hai to forward nahi chalega; tab /add ya /setmain use karo.)"
        )
    try:
        me = await ctx.bot.get_chat_member(chat.id, ctx.bot.id)
        if me.status != "administrator":
            return await msg.reply_text(f"❌ Bot '{chat.title}' me admin nahi hai. Pehle admin banao.")
    except TelegramError as e:
        return await msg.reply_text(f"❌ Error: {e}")

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Main Channel 1", callback_data=f"fw:m1:{chat.id}"),
         InlineKeyboardButton("📢 Main Channel 2", callback_data=f"fw:m2:{chat.id}")],
        [InlineKeyboardButton("🎓 Batch Channel", callback_data=f"fw:b:{chat.id}")],
    ])
    await msg.reply_text(f"Channel mila: <b>{chat.title}</b>\nIse kya banana hai?", reply_markup=kb, parse_mode="HTML")


async def on_fw_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != OWNER_ID:
        return await q.answer()
    _, kind, cid = q.data.split(":")
    cid = int(cid)
    try:
        chat = await ctx.bot.get_chat(cid)
        d = load()
        if kind in ("m1", "m2"):
            slot = kind[1]
            link = await ctx.bot.export_chat_invite_link(cid)
            d["main"][slot] = {"id": cid, "link": link}
            save(d)
            await q.edit_message_text(f"✅ Main Channel {slot} set: {chat.title}\nLink: {link}")
        else:
            old = d["protected"].get(str(cid), {}).get("link")
            link = await _make_protected_link(ctx.bot, cid, old)
            d["protected"][str(cid)] = {"title": chat.title, "link": link}
            save(d)
            await q.edit_message_text(f"✅ Batch channel add: {chat.title}\nIs link ko students ko do:\n{link}")
    except TelegramError as e:
        await q.edit_message_text(f"❌ Error (bot ke paas invite link permission hai?): {e}")


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("setmain", setmain))
    app.add_handler(CommandHandler("setlink", setlink))
    app.add_handler(CommandHandler("newlink", newlink))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("newprotected", newprotected))
    app.add_handler(CommandHandler("remove", remove))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(ChatJoinRequestHandler(on_join_request))
    app.add_handler(CallbackQueryHandler(on_check, pattern=r"^check:"))
    app.add_handler(CallbackQueryHandler(on_fw_choice, pattern=r"^fw:"))
    app.add_handler(MessageHandler(filters.FORWARDED & filters.ChatType.PRIVATE, on_forward))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
