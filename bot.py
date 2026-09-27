# -*- coding: utf-8 -*-
"""
🚀 NPA TRACKING BOT — theo dõi vận đơn SPX (Shopee Express) & GHN (Giao Hàng Nhanh)

Cách chạy nhanh:
  1. Dán BOT_TOKEN vào file .env
  2. pip install -r requirements.txt
  3. python bot.py
(Trên server dùng deploy.sh để cài thành service chạy 24/7.)
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import sys
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

from carriers import (VN_TZ, TrackingEvent, TrackingInfo, cached_tracking,
                      close_client, detect_carrier, fetch_tracking)
from storage import Store


# ---------------------------------------------------------------- cấu hình

def load_env(path: str = ".env"):
    """Đọc file .env đơn giản (KEY=VALUE), không đè biến môi trường có sẵn."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
try:
    CHECK_INTERVAL_MIN = max(1, int(os.environ.get("CHECK_INTERVAL_MINUTES", "3") or 3))
except ValueError:
    CHECK_INTERVAL_MIN = 3
DATA_FILE = os.environ.get("DATA_FILE", "data.json").strip() or "data.json"
ALLOWED_CHAT_IDS = {s.strip() for s in os.environ.get("ALLOWED_CHAT_IDS", "").split(",") if s.strip()}

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("npa-bot")

store = Store(DATA_FILE)

CODE_RE = re.compile(r"^[A-Z0-9]{6,32}$")
SEP = "━━━━━━━━━━━━━━━"
NO_NOTE = "Không có note"


# ---------------------------------------------------------------- tiện ích

def esc(s) -> str:
    return html.escape(str(s), quote=False)


def note_of(o: dict) -> str:
    return o.get("note") or NO_NOTE


def allowed(update: Update) -> bool:
    """Bot đang khoá (ALLOWED_CHAT_IDS có giá trị) -> chỉ các chat đó dùng được."""
    if not ALLOWED_CHAT_IDS:
        return True
    chat = update.effective_chat
    return chat is not None and str(chat.id) in ALLOWED_CHAT_IDS


# ---------------------------------------------------------------- định dạng tin nhắn

def welcome_text(chat_id) -> str:
    text = (
        "🚀 <b>NPA TRACKING SPX</b>\n"
        "📦 Theo dõi vận đơn tự động\n\n"
        f"{SEP}\n"
        "📌 <b>TÍNH NĂNG</b>\n"
        f"├ 🕐 Auto check {CHECK_INTERVAL_MIN} phút/lần\n"
        "├ 🔔 Báo ngay khi đổi trạng thái\n"
        "└ 📋 Xem chi tiết hành trình\n\n"
        f"{SEP}\n"
        "📥 <b>THÊM MÃ</b> (mỗi dòng 1 mã)\n"
        "├ <code>SPXVN000000000001</code>\n"
        "└ <code>SPXVN000000000002 quà cho mẹ</code>\n\n"
        f"{SEP}\n"
        "⚙️ <b>LỆNH</b>\n"
        "├ 📋 /list — danh sách &amp; chi tiết\n"
        "└ 🗑 /delete — xoá mã\n\n"
        f"{SEP}\n"
        "💡 <b>LƯU Ý</b>\n"
        "├ 🚚 Hỗ trợ SPX (SPXVN…) &amp; GHN\n"
        "└ 📝 Không ghi note → <code>Không có note</code>"
    )
    if not ALLOWED_CHAT_IDS:
        text += (
            f"\n\n{SEP}\n"
            "⚠️ Bot đang mở cho mọi người.\n"
            f"Chat ID của bạn: <code>{chat_id}</code>\n"
            "Muốn chỉ mình bạn dùng: điền ID này vào\n"
            "<code>ALLOWED_CHAT_IDS</code> trong file .env rồi restart bot."
        )
    return text


def fmt_update_notice(code: str, note: str, ev: TrackingEvent) -> str:
    """Thông báo khi đơn đổi trạng thái — đúng định dạng yêu cầu."""
    return (
        f"🔔 <b>Cập nhật đơn hàng</b> • <code>{esc(note or NO_NOTE)}</code>\n"
        f"{SEP}\n"
        f"📦 Mã vận đơn: <code>{esc(code)}</code>\n"
        f"└ 🔔 {esc(ev.description)}\n\n"
        f"🕒 Time: <code>{ev.time_str()}</code>"
    )


def _history_block(events, base_len: int) -> str:
    """Ghép danh sách trạng thái, tự cắt bớt cái quá cũ để không vượt 4096 ký tự."""
    parts = [f"\n\n• {esc(ev.description)}\n🕒 {ev.time_str()}" for ev in events]
    out = ""
    hidden = 0
    for i, p in enumerate(parts):
        if base_len + len(out) + len(p) > 3800:
            hidden = len(parts) - i
            break
        out += p
    if hidden:
        out += f"\n\n… và {hidden} trạng thái cũ hơn."
    return out


def fmt_final_notice(code: str, note: str, info: TrackingInfo) -> str:
    """Thông báo cuối khi đơn xong: cập nhật + XẢ toàn bộ lịch trình + dòng tự xoá."""
    text = fmt_update_notice(code, note, info.latest)
    text += f"\n{SEP}\n📜 <b>Tất cả trạng thái:</b>"
    text += _history_block(info.events, len(text) + 60)  # chừa chỗ cho dòng 🧹
    text += "\n\n🧹 Đơn đã xong — tự xoá khỏi danh sách theo dõi."
    return text


def fmt_detail(code: str, note: str, info: TrackingInfo | None, stale_note: str = "") -> str:
    """Màn hình chi tiết vận đơn — đúng định dạng yêu cầu."""
    head = (
        "📋 <b>Chi tiết vận đơn</b>\n"
        f"{SEP}\n"
        f"📦 Mã vận đơn: <code>{esc(code)}</code>\n"
        f"└ 📝 Note: {esc(note or NO_NOTE)}\n\n"
    )
    if not info or not info.events:
        return head + "⏳ Chưa có dữ liệu hành trình cho mã này.\nBot sẽ tự cập nhật khi hãng có thông tin."
    latest = info.latest
    text = head + (
        f"🚚 <b>Trạng thái mới nhất:</b> {esc(latest.description)}\n"
        f"🕒 Thời gian: {latest.time_str()}\n"
        f"{SEP}\n"
        "📜 <b>Tất cả trạng thái:</b>"
    )
    text += _history_block(info.events, len(text))
    if stale_note:
        text += f"\n\n{stale_note}"
    return text


async def get_tracking_for_view(code: str, o: dict | None = None):
    """Lấy hành trình để HIỂN THỊ. Hãng không phản hồi -> dùng bản đầy đủ đã lưu gần nhất.
    Trả về (info, stale_note). stale_note rỗng nghĩa là dữ liệu mới tinh."""
    info = await fetch_tracking(code)
    if info and info.latest:
        return info, ""
    cached = cached_tracking(code)
    if cached:
        cinfo, at = cached
        when = datetime.fromtimestamp(at, VN_TZ).strftime("%H:%M %d/%m")
        return cinfo, (f"⚠️ Hãng tạm chưa phản hồi — đang hiện hành trình đã lưu lúc {when}.\n"
                       "Bấm 🔄 Thử lại sau ít phút để làm mới.")
    if o and o.get("last_desc"):
        return (TrackingInfo(code=code, carrier=o.get("carrier", ""),
                             events=[TrackingEvent(o["last_desc"], o.get("last_ts"))]),
                "⚠️ Hãng tạm chưa phản hồi — đang hiện trạng thái mới nhất đã lưu.\n"
                "Bấm 🔄 Thử lại sau ít phút để xem đủ hành trình.")
    return None, ""


def build_list_keyboard(chat_id, prefix: str) -> InlineKeyboardMarkup | None:
    orders = store.orders(chat_id)
    if not orders:
        return None
    rows = []
    for code, o in sorted(orders.items(), key=lambda kv: kv[1].get("added_at", 0)):
        label = f"📦 {code} | {note_of(o)}"
        if len(label) > 60:
            label = label[:57] + "…"
        rows.append([InlineKeyboardButton(label, callback_data=f"{prefix}:{code}")])
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------- lệnh

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await update.message.reply_text(welcome_text(update.effective_chat.id),
                                    parse_mode=ParseMode.HTML)


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await update.message.reply_text(
        f"🆔 Chat ID của bạn: <code>{update.effective_chat.id}</code>",
        parse_mode=ParseMode.HTML)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    kb = build_list_keyboard(update.effective_chat.id, "detail")
    if not kb:
        await update.message.reply_text(
            "📭 Chưa có mã nào trong danh sách.\nGửi mã vận đơn (mỗi dòng 1 mã) để bắt đầu theo dõi!")
        return
    await update.message.reply_text(
        "📦 <b>Danh sách đang theo dõi</b>\n\nBấm vào mã để xem chi tiết.",
        parse_mode=ParseMode.HTML, reply_markup=kb)


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    kb = build_list_keyboard(update.effective_chat.id, "deldirect")
    if not kb:
        await update.message.reply_text("📭 Danh sách trống, không có gì để xoá.")
        return
    await update.message.reply_text(
        "🗑 <b>Chọn mã cần xoá</b>\n\nBấm vào mã để xoá khỏi danh sách.",
        parse_mode=ParseMode.HTML, reply_markup=kb)


# ---------------------------------------------------------------- thêm mã (tin nhắn thường)

HINT_TEXT = (
    "🤔 Không thấy mã vận đơn hợp lệ trong tin nhắn.\n\n"
    "Gửi mỗi dòng 1 mã, muốn ghi chú thì cách ra rồi viết thêm:\n"
    "<code>SPXVN000000000001</code>\n"
    "<code>SPXVN000000000002 quà cho mẹ</code>\n"
    "<code>GABC1234 đơn GHN</code>"
)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    chat_id = update.effective_chat.id
    lines = [l.strip() for l in (update.message.text or "").splitlines() if l.strip()]
    if not lines:
        return

    orders = store.orders(chat_id)
    todo, existed, invalid = [], [], []
    for line in lines:
        parts = line.split(maxsplit=1)
        code = parts[0].upper()
        note = parts[1].strip() if len(parts) > 1 else ""
        if not CODE_RE.match(code):
            invalid.append(line)
            continue
        if code in orders:
            existed.append(code)
            continue
        if any(t[0] == code for t in todo):  # trùng ngay trong 1 tin nhắn
            continue
        todo.append((code, note))

    if not todo and not existed:
        await update.message.reply_text(HINT_TEXT, parse_mode=ParseMode.HTML)
        return

    msg = None
    if todo:
        msg = await update.message.reply_text(f"⏳ Đang kiểm tra {len(todo)} mã…")

    results, finished = [], []
    if todo:
        for code, note in todo:
            info = await fetch_tracking(code)
            if info and info.latest and info.done:
                # đơn đã giao/huỷ xong từ trước -> không cần theo dõi
                finished.append((code, note, info.latest))
                continue
            carrier = info.carrier if info else detect_carrier(code)
            store.add(chat_id, code, note, carrier)
            o = store.get(chat_id, code)
            if info and info.latest:
                o["last_desc"] = info.latest.description
                o["last_ts"] = info.latest.ts
                status_line = f"└ 🚚 {esc(info.latest.description)}"
            else:
                status_line = "└ ⏳ Chưa có dữ liệu — bot sẽ tự cập nhật khi có"
            results.append((code, note, status_line))
        if results:
            store.save()

    out = []
    if results:
        out.append(f"✅ <b>Đã thêm {len(results)} mã:</b>")
        for code, note, status_line in results:
            out.append(f"\n📦 <code>{esc(code)}</code> • {esc(note or NO_NOTE)}\n{status_line}")
    if finished:
        if results:
            out.append("\n")
        out.append(f"\n🏁 <b>Đơn đã xong, không đưa vào theo dõi ({len(finished)}):</b>")
        for code, note, ev in finished:
            out.append(f"\n📦 <code>{esc(code)}</code> • {esc(note or NO_NOTE)}\n"
                       f"└ ✅ {esc(ev.description)} ({ev.time_str()})")
    if existed:
        out.append("\n\n♻️ Đã có sẵn trong danh sách: "
                   + ", ".join(f"<code>{esc(c)}</code>" for c in existed))
    if invalid:
        out.append("\n\n⚠️ Bỏ qua dòng không hợp lệ: "
                   + " | ".join(esc(l) for l in invalid))
    text = "".join(out).strip()

    kb = None
    if finished:
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton(f"📋 Xem hành trình {code}", callback_data=f"peek:{code}")]
             for code, _n, _e in finished[:10]])

    if msg:
        await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


# ---------------------------------------------------------------- nút bấm (callback)

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not allowed(update):
        await q.answer()
        return
    data = q.data or ""
    if ":" not in data:
        await q.answer()
        return
    action, code = data.split(":", 1)
    chat_id = update.effective_chat.id
    o = store.get(chat_id, code)

    # ----- xem chi tiết (từ /list, nút dưới thông báo, nút Thử lại) -----
    if action in ("detail", "peek"):
        await q.answer("⏳ Đang lấy dữ liệu…")
        info, stale_note = await get_tracking_for_view(code, o)
        retry_btn = InlineKeyboardButton("🔄 Thử lại", callback_data=f"{action}:{code}")
        if not o:
            # mã không còn trong danh sách (đơn đã xong / tự xoá) -> vẫn xem được hành trình
            kb = InlineKeyboardMarkup([[retry_btn]]) if (stale_note or not info) else None
            await context.bot.send_message(chat_id, fmt_detail(code, "", info, stale_note),
                                           parse_mode=ParseMode.HTML, reply_markup=kb)
            return
        finished = bool(info and info.done and not stale_note)
        if finished:
            # vừa phát hiện đơn đã xong -> tự xoá khỏi danh sách
            store.remove(chat_id, code)
            store.save()
        elif info and info.latest and not stale_note:
            # tiện thể cập nhật trạng thái đã lưu (không cần thông báo vì user đang xem)
            o["last_desc"] = info.latest.description
            o["last_ts"] = info.latest.ts
            store.save()
        text = fmt_detail(code, note_of(o), info, stale_note)
        kb = None
        if finished:
            text += "\n\n🧹 Đơn đã xong — tự xoá khỏi danh sách theo dõi."
        else:
            row = [InlineKeyboardButton("🗑 Xoá mã này", callback_data=f"del:{code}")]
            if stale_note or not info:
                row.insert(0, retry_btn)
            kb = InlineKeyboardMarkup([row])
        await context.bot.send_message(chat_id, text,
                                       parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    # ----- xoá từ màn hình chi tiết -----
    if action == "del":
        removed = store.remove(chat_id, code)
        store.save()
        await q.answer("✅ Đã xoá" if removed else "Mã không còn trong danh sách")
        try:
            await q.edit_message_text(
                f"🗑 Đã xoá mã <code>{esc(code)}</code> khỏi danh sách theo dõi.",
                parse_mode=ParseMode.HTML)
        except TelegramError:
            pass
        return

    # ----- xoá trực tiếp từ /delete -----
    if action == "deldirect":
        removed = store.remove(chat_id, code)
        store.save()
        await q.answer("✅ Đã xoá" if removed else "Mã đã được xoá trước đó")
        kb = build_list_keyboard(chat_id, "deldirect")
        try:
            if kb:
                await q.edit_message_text(
                    f"🗑 <b>Chọn mã cần xoá</b>\n\n✅ Đã xoá <code>{esc(code)}</code>."
                    "\nBấm mã khác để xoá tiếp.",
                    parse_mode=ParseMode.HTML, reply_markup=kb)
            else:
                await q.edit_message_text(
                    f"✅ Đã xoá <code>{esc(code)}</code>.\n📭 Danh sách trống.",
                    parse_mode=ParseMode.HTML)
        except TelegramError:
            pass
        return

    await q.answer()


# ---------------------------------------------------------------- check ngầm

async def check_all(app: Application):
    """Quét toàn bộ mã đang theo dõi, báo ngay khi có trạng thái mới."""
    dirty = False
    for chat_id in store.all_chats():
        # bot đang khoá -> chỉ check ngầm & gửi thông báo cho các chat được phép
        if ALLOWED_CHAT_IDS and chat_id not in ALLOWED_CHAT_IDS:
            continue
        for code, o in list(store.orders(chat_id).items()):
            if o.get("done"):
                # đơn đã xong còn sót lại (từ phiên bản cũ) -> dọn êm
                store.remove(chat_id, code)
                dirty = True
                continue
            info = await fetch_tracking(code, attempts=2)
            if info and info.latest:
                ev = info.latest
                if (ev.description != o.get("last_desc")
                        or ev.ts != o.get("last_ts")):
                    # user có thể vừa xoá mã trong lúc đang quét -> kiểm tra lại
                    if store.get(chat_id, code) is not o:
                        continue
                    o["last_desc"] = ev.description
                    o["last_ts"] = ev.ts
                    o["done"] = info.done
                    dirty = True
                    if info.done:
                        # giao/hoàn/huỷ xong -> xả toàn bộ lịch trình rồi tự xoá
                        store.remove(chat_id, code)
                        text = fmt_final_notice(code, o.get("note", ""), info)
                        kb = None  # lịch trình đã hiện đủ, khỏi cần nút
                    else:
                        text = fmt_update_notice(code, o.get("note", ""), ev)
                        kb = InlineKeyboardMarkup([[InlineKeyboardButton(
                            "📋 Xem chi tiết", callback_data=f"detail:{code}")]])
                    try:
                        await app.bot.send_message(int(chat_id), text,
                                                   parse_mode=ParseMode.HTML,
                                                   reply_markup=kb)
                    except TelegramError as e:
                        log.warning("Không gửi được thông báo tới %s: %s", chat_id, e)
            await asyncio.sleep(1.2)  # lịch sự với API hãng, tránh bị chặn IP
    if dirty:
        store.save()


async def check_loop(app: Application):
    log.info("🕐 Auto-check mỗi %s phút đã bật.", CHECK_INTERVAL_MIN)
    await asyncio.sleep(15)  # chờ bot khởi động xong
    while True:
        try:
            await check_all(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Lỗi trong vòng check ngầm (sẽ thử lại ở lượt sau)")
        await asyncio.sleep(CHECK_INTERVAL_MIN * 60)


# ---------------------------------------------------------------- khởi động

_bg_tasks = set()


async def post_init(app: Application):
    task = asyncio.create_task(check_loop(app))
    _bg_tasks.add(task)
    try:
        await app.bot.set_my_commands([
            ("list", "📋 Danh sách vận đơn"),
            ("delete", "🗑 Xoá vận đơn"),
            ("start", "🚀 Hướng dẫn sử dụng"),
            ("id", "🆔 Xem Chat ID"),
        ])
    except TelegramError:
        pass


async def post_shutdown(app: Application):
    for task in _bg_tasks:
        task.cancel()
    await close_client()


def main():
    if not BOT_TOKEN:
        print("❌ Chưa có BOT_TOKEN!")
        print("   Mở file .env, dán token lấy từ @BotFather vào dòng BOT_TOKEN= rồi chạy lại.")
        sys.exit(1)
    app = (Application.builder()
           .token(BOT_TOKEN)
           .post_init(post_init)
           .post_shutdown(post_shutdown)
           .build())
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    log.info("🚀 NPA Tracking Bot khởi động (check ngầm mỗi %s phút)…", CHECK_INTERVAL_MIN)
    app.run_polling(allowed_updates=["message", "callback_query"],
                    drop_pending_updates=True)


if __name__ == "__main__":
    main()
